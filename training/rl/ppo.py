"""On-policy PPO rollouts and clipped actor-critic updates."""

import copy
import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from numbers import Real

import numpy as np
import torch
from torch import nn

from .actions import ModelAction
from .gym_env import MemoryGymEnv
from .model import ActorCritic


@dataclass(frozen=True, slots=True)
class PpoSample:
    features: np.ndarray
    action: ModelAction
    log_prob: float
    value: float
    reward: float
    frames: int
    episode_frames: int
    terminated: bool


@dataclass(frozen=True, slots=True)
class PpoRollout:
    samples: tuple[PpoSample, ...]
    next_features: np.ndarray
    bootstrap_value: float
    terminated: bool
    boundary_reward: float
    boundary_frames: int
    episode_frames: int
    resume_reward: float = 0.0
    resume_frames: int = 0


@dataclass(frozen=True, slots=True)
class PpoBatch:
    observations: torch.Tensor
    actions: torch.Tensor
    old_log_probs: torch.Tensor
    old_values: torch.Tensor
    advantages: torch.Tensor
    returns: torch.Tensor


@dataclass(frozen=True, slots=True)
class PpoMetrics:
    total: float
    policy: float
    value: float
    entropy: float
    approximate_kl: float
    clip_fraction: float


@dataclass(frozen=True, slots=True)
class PpoIteration:
    rollout: PpoRollout
    next_features: np.ndarray
    updates: tuple[PpoMetrics, ...]


def collect_ppo_rollout(
    env: MemoryGymEnv,
    model: ActorCritic,
    features: np.ndarray,
    *,
    steps: int,
) -> PpoRollout:
    """Collect a pure-learner rollout with behavior-policy probabilities."""
    _require_positive_integer(steps, "steps")
    current = np.asarray(features, dtype=np.float32)
    samples: list[PpoSample] = []
    terminated = False
    try:
        for _ in range(steps):
            selected = model.act(current)
            action = selected.action
            next_features, reward, terminated, truncated, info = env.step(
                np.asarray((action.movement, int(action.bomb)), dtype=np.int64)
            )
            if truncated:
                raise RuntimeError(
                    "PPO uses horizon for rollout boundaries; environment "
                    "truncation cannot provide a resumable pause"
                )
            stored_features = current.copy()
            stored_features.flags.writeable = False
            samples.append(
                PpoSample(
                    features=stored_features,
                    action=action,
                    log_prob=selected.log_prob,
                    value=selected.value,
                    reward=float(reward),
                    frames=int(info["delta_frames"]),
                    episode_frames=int(info["frames"]),
                    terminated=terminated,
                )
            )
            current = next_features
            if terminated:
                break
    except BaseException:
        try:
            env.stop()
        except BaseException:
            pass
        raise
    return PpoRollout(
        samples=tuple(samples),
        next_features=current,
        bootstrap_value=0.0,
        terminated=terminated,
        boundary_reward=0.0,
        boundary_frames=0,
        episode_frames=samples[-1].episode_frames,
    )


def prepare_ppo_batch(
    rollout: PpoRollout,
    *,
    gamma: float = 0.999,
    gae_lambda: float = 0.95,
    device: torch.device | str = "cpu",
) -> PpoBatch:
    """Compute frame-discounted GAE and returns for one rollout."""
    checked_gamma = _require_probability(gamma, "gamma", allow_one=True)
    checked_lambda = _require_probability(gae_lambda, "gae_lambda", allow_one=True)
    if not rollout.samples:
        raise ValueError("rollout must contain at least one sample")
    advantages = np.empty(len(rollout.samples), dtype=np.float32)
    advantage = 0.0
    next_value = rollout.bootstrap_value
    for index in range(len(rollout.samples) - 1, -1, -1):
        sample = rollout.samples[index]
        discount = checked_gamma ** sample.frames
        continuation = 0.0 if sample.terminated else 1.0
        delta = sample.reward + discount * next_value * continuation - sample.value
        advantage = (
            delta
            + discount * checked_lambda * continuation * advantage
        )
        advantages[index] = advantage
        next_value = sample.value
    observations = torch.as_tensor(
        np.stack([sample.features for sample in rollout.samples]),
        dtype=torch.float32,
        device=device,
    )
    actions = torch.tensor(
        [
            (sample.action.movement, int(sample.action.bomb))
            for sample in rollout.samples
        ],
        dtype=torch.long,
        device=device,
    )
    old_log_probs = torch.tensor(
        [sample.log_prob for sample in rollout.samples],
        dtype=torch.float32,
        device=device,
    )
    old_values = torch.tensor(
        [sample.value for sample in rollout.samples],
        dtype=torch.float32,
        device=device,
    )
    advantage_tensor = torch.as_tensor(advantages, device=device)
    returns = advantage_tensor + old_values
    return PpoBatch(
        observations=observations,
        actions=actions,
        old_log_probs=old_log_probs,
        old_values=old_values,
        advantages=advantage_tensor,
        returns=returns,
    )


def update_ppo(
    model: ActorCritic,
    optimizer: torch.optim.Optimizer,
    batch: PpoBatch,
    *,
    epochs: int = 10,
    batch_size: int = 256,
    clip_ratio: float = 0.2,
    value_clip: float = 0.2,
    value_coefficient: float = 0.5,
    entropy_coefficient: float = 0.01,
    max_grad_norm: float = 0.5,
    target_kl: float | None = None,
) -> tuple[PpoMetrics, ...]:
    """Apply shuffled clipped PPO epochs to a prepared on-policy batch."""
    epoch_count = _require_positive_integer(epochs, "epochs")
    minibatch_size = _require_positive_integer(batch_size, "batch_size")
    policy_clip = _require_positive_finite(clip_ratio, "clip_ratio")
    critic_clip = _require_positive_finite(value_clip, "value_clip")
    value_weight = _require_nonnegative_finite(
        value_coefficient, "value_coefficient"
    )
    entropy_weight = _require_nonnegative_finite(
        entropy_coefficient, "entropy_coefficient"
    )
    gradient_limit = _require_positive_finite(max_grad_norm, "max_grad_norm")
    kl_limit = (
        None if target_kl is None else _require_positive_finite(target_kl, "target_kl")
    )
    count = batch.observations.shape[0]
    if count < 1:
        raise ValueError("batch must not be empty")
    if batch.actions.shape != (count, 2):
        raise ValueError("actions must have shape (batch, 2)")
    for name, tensor in (
        ("old_log_probs", batch.old_log_probs),
        ("old_values", batch.old_values),
        ("advantages", batch.advantages),
        ("returns", batch.returns),
    ):
        if tensor.shape != (count,):
            raise ValueError(f"{name} must have shape (batch,)")

    advantages = batch.advantages
    if count > 1:
        advantages = (advantages - advantages.mean()) / (
            advantages.std(unbiased=False) + 1e-8
        )
    metrics: list[PpoMetrics] = []
    stop = False
    for _ in range(epoch_count):
        for indices in torch.randperm(count, device=batch.observations.device).split(
            minibatch_size
        ):
            evaluation = model.evaluate_actions(
                batch.observations[indices], batch.actions[indices]
            )
            log_ratio = evaluation.log_prob - batch.old_log_probs[indices]
            ratio = log_ratio.exp()
            selected_advantages = advantages[indices]
            policy = torch.maximum(
                -selected_advantages * ratio,
                -selected_advantages
                * ratio.clamp(1.0 - policy_clip, 1.0 + policy_clip),
            ).mean()
            value_delta = evaluation.value - batch.old_values[indices]
            clipped_values = batch.old_values[indices] + value_delta.clamp(
                -critic_clip, critic_clip
            )
            value = 0.5 * torch.maximum(
                (evaluation.value - batch.returns[indices]).square(),
                (clipped_values - batch.returns[indices]).square(),
            ).mean()
            entropy = evaluation.entropy.mean()
            total = policy + value_weight * value - entropy_weight * entropy
            optimizer.zero_grad(set_to_none=True)
            total.backward()
            nn.utils.clip_grad_norm_(model.parameters(), gradient_limit)
            optimizer.step()
            with torch.no_grad():
                approximate_kl = ((ratio - 1.0) - log_ratio).mean()
                clip_fraction = (
                    (ratio - 1.0).abs() > policy_clip
                ).float().mean()
            result = PpoMetrics(
                total=float(total.detach().item()),
                policy=float(policy.detach().item()),
                value=float(value.detach().item()),
                entropy=float(entropy.detach().item()),
                approximate_kl=float(approximate_kl.item()),
                clip_fraction=float(clip_fraction.item()),
            )
            metrics.append(result)
            if kl_limit is not None and result.approximate_kl > kl_limit:
                stop = True
                break
        if stop:
            break
    return tuple(metrics)


def run_ppo_iteration(
    env: MemoryGymEnv,
    model: ActorCritic,
    optimizer: torch.optim.Optimizer,
    features: np.ndarray,
    *,
    horizon: int,
    epochs: int,
    batch_size: int,
    gamma: float = 0.999,
    gae_lambda: float = 0.95,
    clip_ratio: float = 0.2,
    value_clip: float = 0.2,
    value_coefficient: float = 0.5,
    entropy_coefficient: float = 0.01,
    max_grad_norm: float = 0.5,
    target_kl: float | None = None,
    prepare_updates: Callable[[PpoRollout, tuple[PpoMetrics, ...]], None]
    | None = None,
    after_updates: Callable[[PpoRollout, tuple[PpoMetrics, ...]], None]
    | None = None,
) -> PpoIteration:
    """Collect, pause a live game, update on-policy, then safely resume.

    ``prepare_updates`` runs while a live game is paused and may run again if a
    terminal resume requires a corrected update. ``after_updates`` runs exactly
    once with the finalized boundary, so callers can stage expensive output in
    the former and publish it cheaply in the latter.
    """
    if env.max_steps is not None:
        raise ValueError(
            "PPO uses horizon for rollout boundaries; max_steps truncation "
            "cannot provide a resumable pause"
        )
    rollout = collect_ppo_rollout(env, model, features, steps=horizon)
    live = not rollout.terminated
    if live:
        boundary_features, info = env.pause()
        boundary_reward = float(info["reward/total"])
        boundary_frames = int(info["delta_frames"])
        last = rollout.samples[-1]
        rollout = replace(
            rollout,
            samples=rollout.samples[:-1]
            + (
                replace(
                    last,
                    reward=last.reward + boundary_reward,
                    frames=last.frames + boundary_frames,
                    episode_frames=int(info["frames"]),
                    terminated=bool(info["terminated"]),
                ),
            ),
            next_features=boundary_features,
            boundary_reward=boundary_reward,
            boundary_frames=boundary_frames,
            episode_frames=int(info["frames"]),
        )
        if bool(info["terminated"]):
            rollout = replace(
                rollout,
                terminated=True,
            )
            live = False
        else:
            bootstrap = model.act(boundary_features, deterministic=True).value
            rollout = replace(
                rollout,
                bootstrap_value=bootstrap,
            )
    device = next(model.parameters()).device
    model_before_update = copy.deepcopy(model.state_dict()) if live else None
    optimizer_before_update = copy.deepcopy(optimizer.state_dict()) if live else None
    batch = prepare_ppo_batch(
        rollout, gamma=gamma, gae_lambda=gae_lambda, device=device
    )
    updates = update_ppo(
        model,
        optimizer,
        batch,
        epochs=epochs,
        batch_size=batch_size,
        clip_ratio=clip_ratio,
        value_clip=value_clip,
        value_coefficient=value_coefficient,
        entropy_coefficient=entropy_coefficient,
        max_grad_norm=max_grad_norm,
        target_kl=target_kl,
    )
    if prepare_updates is not None:
        prepare_updates(rollout, updates)
    if live:
        next_features, info = env.resume()
        if bool(info["terminated"]):
            last = rollout.samples[-1]
            rollout = replace(
                rollout,
                samples=rollout.samples[:-1]
                + (
                    replace(
                        last,
                        reward=last.reward + float(info["reward/total"]),
                        frames=last.frames + int(info["delta_frames"]),
                        episode_frames=int(info["frames"]),
                        terminated=True,
                    ),
                ),
                next_features=next_features,
                bootstrap_value=0.0,
                terminated=True,
                boundary_reward=rollout.boundary_reward
                + float(info["reward/total"]),
                boundary_frames=rollout.boundary_frames
                + int(info["delta_frames"]),
                episode_frames=int(info["frames"]),
            )
            if model_before_update is None or optimizer_before_update is None:
                raise RuntimeError("live PPO update is missing rollback state")
            model.load_state_dict(model_before_update)
            optimizer.load_state_dict(optimizer_before_update)
            batch = prepare_ppo_batch(
                rollout, gamma=gamma, gae_lambda=gae_lambda, device=device
            )
            updates = update_ppo(
                model,
                optimizer,
                batch,
                epochs=epochs,
                batch_size=batch_size,
                clip_ratio=clip_ratio,
                value_clip=value_clip,
                value_coefficient=value_coefficient,
                entropy_coefficient=entropy_coefficient,
                max_grad_norm=max_grad_norm,
                target_kl=target_kl,
            )
            if prepare_updates is not None:
                prepare_updates(rollout, updates)
        else:
            rollout = replace(
                rollout,
                next_features=next_features,
                episode_frames=int(info["frames"]),
                resume_reward=float(info["reward/total"]),
                resume_frames=int(info["delta_frames"]),
            )
    else:
        next_features = rollout.next_features
    if after_updates is not None:
        after_updates(rollout, updates)
    return PpoIteration(
        rollout=rollout,
        next_features=next_features,
        updates=updates,
    )


def _require_positive_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _require_probability(value: object, name: str, *, allow_one: bool) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} must be a number")
    checked = float(value)
    upper_ok = checked <= 1.0 if allow_one else checked < 1.0
    if not math.isfinite(checked) or checked < 0.0 or not upper_ok:
        bound = "[0, 1]" if allow_one else "[0, 1)"
        raise ValueError(f"{name} must be in {bound}")
    return checked


def _require_positive_finite(value: object, name: str) -> float:
    checked = _require_nonnegative_finite(value, name)
    if checked == 0.0:
        raise ValueError(f"{name} must be positive")
    return checked


def _require_nonnegative_finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} must be a number")
    checked = float(value)
    if not math.isfinite(checked) or checked < 0.0:
        raise ValueError(f"{name} must be non-negative and finite")
    return checked
