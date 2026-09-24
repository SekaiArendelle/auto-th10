"""Gymnasium adapter for the memory-backed TH10 environment.

This module is intentionally not imported by :mod:`training.rl`: Gymnasium and
NumPy are optional training dependencies, while the feature and action
protocols remain usable by the base installation.
"""

from collections.abc import Mapping

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from auto_th10 import Action, TRAIN_PRESET, Observation, Th10Env

from ..shooting import shoot_action
from .actions import ActionSpec, ModelAction, decode_action
from .features import FeatureSpec, MemoryFeatureEncoder
from .rewards import RewardBreakdown, RewardSpec, memory_reward


class MemoryGymEnv(gym.Env[np.ndarray, np.ndarray]):
    """Expose fixed memory features and two discrete action heads to PPO."""

    metadata = {"render_modes": []}

    def __init__(
        self,
        *,
        feature_spec: FeatureSpec = FeatureSpec(),
        reward_spec: RewardSpec = RewardSpec(),
        action_repeat: int = 1,
        max_steps: int | None = None,
    ) -> None:
        super().__init__()
        checked_action_repeat = _require_positive_integer(action_repeat, "action_repeat")
        if max_steps is not None:
            max_steps = _require_positive_integer(max_steps, "max_steps")
        self.env = Th10Env(settings=TRAIN_PRESET)
        self.encoder = MemoryFeatureEncoder(feature_spec)
        self.reward_spec = reward_spec
        self.action_repeat = checked_action_repeat
        self.max_steps = max_steps
        self.observation_space = spaces.Box(
            low=-1.0,
            high=1.0,
            shape=(feature_spec.size,),
            dtype=np.float32,
        )
        self.action_space = spaces.MultiDiscrete(
            np.asarray(ActionSpec().shape, dtype=np.int64)
        )
        self._observation: Observation | None = None
        self._decisions = 0
        self._episode_done = False

    def reset(
        self,
        *,
        seed: int | None = None,
        options: Mapping[str, object] | None = None,
    ) -> tuple[np.ndarray, dict[str, object]]:
        """Start a game episode and return its fixed memory feature vector."""
        super().reset(seed=seed)
        del options
        if self._observation is not None:
            self.env.session.set_input(Action.NONE)
        observation = self.env.reset()
        self._observation = observation
        self._decisions = 0
        self._episode_done = False
        return self._encode(observation), self._info({}, delta_frames=0)

    def step(
        self, action: np.ndarray
    ) -> tuple[np.ndarray, float, bool, bool, dict[str, object]]:
        """Apply one model decision, optionally for several game frames."""
        if self._observation is None:
            raise RuntimeError("reset() must be called before step()")
        if self._episode_done:
            raise RuntimeError("reset() must be called after the episode ends")
        if not self.action_space.contains(action):
            raise ValueError(f"action {action!r} is outside {self.action_space}")

        model_action = ModelAction(movement=int(action[0]), bomb=int(action[1]))
        shooting = shoot_action(self._observation.snapshot, self._decisions + 1)
        native_action = decode_action(model_action, shoot=bool(shooting))
        total = RewardBreakdown(0.0, 0.0, 0.0, 0.0, 0.0)
        delta_frames = 0
        terminated = False
        for repeat in range(self.action_repeat):
            applied_action = (
                native_action if repeat == 0 else native_action & ~Action.BOMB
            )
            transition = self.env.step(applied_action)
            delta_frames += transition.frames
            reward_frames = max(1, transition.frames)
            total = _add_rewards(
                total,
                memory_reward(
                    transition.observation.snapshot,
                    transition.next_observation.snapshot,
                    transition.action,
                    frames=reward_frames,
                    spec=self.reward_spec,
                ),
            )
            self._observation = transition.next_observation
            terminated = transition.terminated
            if terminated:
                break

        self._decisions += 1
        truncated = (
            not terminated
            and self.max_steps is not None
            and self._decisions >= self.max_steps
        )
        if terminated or truncated:
            self.env.stop()
            self._episode_done = True
        elif applied_action & Action.BOMB:
            self.env.session.set_input(native_action & ~Action.BOMB)
        result_info = self._info({}, delta_frames=delta_frames)
        result_info.update(
            {
                "native_action": int(native_action),
                "reward/survival": total.survival,
                "reward/score": total.score,
                "reward/life": total.life,
                "reward/bomb": total.bomb,
                "reward/game_over": total.game_over,
                "reward/total": total.total,
            }
        )
        return (
            self._encode(self._observation),
            total.total,
            terminated,
            truncated,
            result_info,
        )

    def close(self) -> None:
        """Close the core environment this adapter built."""
        self.env.close()

    def _encode(self, observation: Observation) -> np.ndarray:
        return np.asarray(self.encoder.encode(observation), dtype=np.float32)

    def _info(
        self, info: Mapping[str, object], *, delta_frames: int
    ) -> dict[str, object]:
        if self._observation is None:
            raise RuntimeError("no observation is available")
        snapshot = self._observation.snapshot
        result = dict(info)
        result.update(
            {
                "score": snapshot.score,
                "lives": snapshot.lives,
                "steps": self._decisions,
                "frames": self.env.frames,
                "delta_frames": delta_frames,
            }
        )
        return result


def _add_rewards(left: RewardBreakdown, right: RewardBreakdown) -> RewardBreakdown:
    return RewardBreakdown(*(a + b for a, b in zip(left, right)))


def _require_positive_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value
