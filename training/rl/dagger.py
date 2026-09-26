"""DAgger rollout collection, aggregation and paused imitation updates."""

import math
import random
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from numbers import Real
from typing import Protocol

import numpy as np
import torch

from .actions import ModelAction
from .gym_env import MemoryGymEnv
from .imitation import ImitationMetrics, update_imitation
from .model import ActionSample, ActorCritic
from .teacher import Teacher


class Learner(Protocol):
    """The part of ActorCritic a rollout collector needs."""

    def act(
        self,
        observation: np.ndarray,
        *,
        deterministic: bool = False,
    ) -> ActionSample:
        """Choose one model action from an encoded observation."""


@dataclass(frozen=True, slots=True)
class DaggerSample:
    features: np.ndarray
    teacher_action: ModelAction
    learner_action: ModelAction
    executed_action: ModelAction
    used_teacher: bool
    reward: float
    frames: int
    episode_frames: int
    terminated: bool
    truncated: bool


@dataclass(frozen=True, slots=True)
class DaggerRollout:
    samples: tuple[DaggerSample, ...]
    next_features: np.ndarray
    terminated: bool
    truncated: bool
    boundary_reward: float
    boundary_frames: int
    episode_frames: int


@dataclass(frozen=True, slots=True)
class ImitationBatch:
    observations: torch.Tensor
    teacher_actions: torch.Tensor


@dataclass(frozen=True, slots=True)
class DaggerIteration:
    rollout: DaggerRollout
    next_features: np.ndarray
    updates: tuple[ImitationMetrics, ...]


class DaggerBuffer:
    """A bounded aggregate of learner-visited states and teacher labels."""

    def __init__(self, capacity: int = 100_000) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int):
            raise TypeError("capacity must be an integer")
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._samples: deque[DaggerSample] = deque(maxlen=capacity)

    def __len__(self) -> int:
        return len(self._samples)

    def extend(self, samples: Iterable[DaggerSample]) -> None:
        self._samples.extend(samples)

    def sample(
        self,
        batch_size: int,
        *,
        bomb_fraction: float = 0.25,
        rng: random.Random,
        device: torch.device | str = "cpu",
    ) -> ImitationBatch:
        if isinstance(batch_size, bool) or not isinstance(batch_size, int):
            raise TypeError("batch_size must be an integer")
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if (
            isinstance(bomb_fraction, bool)
            or not isinstance(bomb_fraction, Real)
            or not math.isfinite(float(bomb_fraction))
            or not 0.0 <= bomb_fraction <= 1.0
        ):
            raise ValueError("bomb_fraction must be in [0, 1]")
        if not self._samples:
            raise RuntimeError("cannot sample an empty DAgger buffer")
        positives = [sample for sample in self._samples if sample.teacher_action.bomb]
        negatives = [sample for sample in self._samples if not sample.teacher_action.bomb]
        positive_count = round(batch_size * bomb_fraction) if positives else 0
        negative_count = batch_size - positive_count if negatives else 0
        if not negatives:
            positive_count = batch_size
        elif not positives:
            negative_count = batch_size
        chosen = rng.choices(positives, k=positive_count)
        chosen.extend(rng.choices(negatives, k=negative_count))
        rng.shuffle(chosen)
        observations = torch.as_tensor(
            np.stack([sample.features for sample in chosen]),
            dtype=torch.float32,
            device=device,
        )
        actions = torch.tensor(
            [
                (sample.teacher_action.movement, int(sample.teacher_action.bomb))
                for sample in chosen
            ],
            dtype=torch.long,
            device=device,
        )
        return ImitationBatch(observations=observations, teacher_actions=actions)


def collect_dagger_rollout(
    env: MemoryGymEnv,
    learner: Learner,
    teacher: Teacher,
    features: np.ndarray,
    *,
    steps: int,
    beta: float,
    rng: random.Random,
    deterministic_learner: bool = False,
) -> DaggerRollout:
    """Collect learner states, teacher labels and the β-mixture's real actions."""
    if isinstance(steps, bool) or not isinstance(steps, int):
        raise TypeError("steps must be an integer")
    if steps < 1:
        raise ValueError("steps must be positive")
    if (
        isinstance(beta, bool)
        or not isinstance(beta, Real)
        or not math.isfinite(float(beta))
        or not 0.0 <= beta <= 1.0
    ):
        raise ValueError("beta must be in [0, 1]")
    current = np.asarray(features, dtype=np.float32)
    samples: list[DaggerSample] = []
    terminated = False
    truncated = False
    try:
        for _ in range(steps):
            teacher_action = teacher.annotate(env.raw_observation)
            learner_sample = learner.act(current, deterministic=deterministic_learner)
            learner_action = learner_sample.action
            use_teacher = beta >= 1.0 or (beta > 0.0 and rng.random() < beta)
            executed_action = teacher_action if use_teacher else learner_action
            next_features, reward, terminated, truncated, info = env.step(
                np.asarray(
                    (executed_action.movement, int(executed_action.bomb)),
                    dtype=np.int64,
                )
            )
            frames = int(info["delta_frames"])
            teacher.feedback(executed_action, frames=frames)
            stored_features = current.copy()
            stored_features.flags.writeable = False
            samples.append(
                DaggerSample(
                    features=stored_features,
                    teacher_action=teacher_action,
                    learner_action=learner_action,
                    executed_action=executed_action,
                    used_teacher=use_teacher,
                    reward=float(reward),
                    frames=frames,
                    episode_frames=int(info["frames"]),
                    terminated=terminated,
                    truncated=truncated,
                )
            )
            current = next_features
            if terminated or truncated:
                break
    except BaseException:
        try:
            env.stop()
        except BaseException:
            # Releasing input is best-effort here; preserve the collection error
            # that explains why the environment was interrupted.
            pass
        raise
    return DaggerRollout(
        samples=tuple(samples),
        next_features=current,
        terminated=terminated,
        truncated=truncated,
        boundary_reward=0.0,
        boundary_frames=0,
        episode_frames=samples[-1].episode_frames,
    )


def run_dagger_iteration(
    env: MemoryGymEnv,
    model: ActorCritic,
    teacher: Teacher,
    optimizer: torch.optim.Optimizer,
    buffer: DaggerBuffer,
    features: np.ndarray,
    *,
    horizon: int,
    beta: float,
    batch_size: int,
    update_steps: int,
    bomb_fraction: float = 0.25,
    bomb_positive_weight: float = 8.0,
    rng: random.Random,
    after_updates: Callable[
        [DaggerRollout, tuple[ImitationMetrics, ...]], None
    ]
    | None = None,
) -> DaggerIteration:
    """Collect a horizon, pause a live game, update, then safely resume it."""
    if env.max_steps is not None:
        raise ValueError(
            "DAgger uses horizon for rollout boundaries; max_steps truncation "
            "cannot provide a resumable pause"
        )
    if isinstance(update_steps, bool) or not isinstance(update_steps, int):
        raise TypeError("update_steps must be an integer")
    if update_steps < 0:
        raise ValueError("update_steps must not be negative")
    rollout = collect_dagger_rollout(
        env,
        model,
        teacher,
        features,
        steps=horizon,
        beta=beta,
        rng=rng,
    )
    buffer.extend(rollout.samples)
    live = not rollout.terminated and not rollout.truncated
    if live:
        boundary_features, info = env.pause()
        if bool(info["terminated"]):
            rollout = replace(
                rollout,
                next_features=boundary_features,
                terminated=True,
                boundary_reward=float(info["reward/total"]),
                boundary_frames=int(info["delta_frames"]),
                episode_frames=int(info["frames"]),
            )
            live = False
        else:
            teacher.advance(frames=int(info["delta_frames"]))
    updates: list[ImitationMetrics] = []
    try:
        device = next(model.parameters()).device
        for _ in range(update_steps):
            batch = buffer.sample(
                batch_size,
                bomb_fraction=bomb_fraction,
                rng=rng,
                device=device,
            )
            updates.append(
                update_imitation(
                    model,
                    optimizer,
                    batch.observations,
                    batch.teacher_actions,
                    bomb_positive_weight=bomb_positive_weight,
                )
            )
        update_results = tuple(updates)
        if after_updates is not None:
            after_updates(rollout, update_results)
    except BaseException:
        # A failed update deliberately leaves a live game paused. Resuming in a
        # finally block would let it run unattended while the failure unwinds.
        raise
    if live:
        next_features, info = env.resume()
        if bool(info["terminated"]):
            rollout = replace(
                rollout,
                next_features=next_features,
                terminated=True,
                boundary_reward=float(info["reward/total"]),
                boundary_frames=int(info["delta_frames"]),
                episode_frames=int(info["frames"]),
            )
        else:
            try:
                teacher.advance(frames=int(info["delta_frames"]))
            except BaseException:
                env.stop()
                raise
    else:
        next_features = rollout.next_features
    return DaggerIteration(
        rollout=rollout,
        next_features=next_features,
        updates=update_results,
    )
