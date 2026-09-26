"""Gymnasium adapter for the memory-backed TH10 environment.

This is the only module in the layer that imports Gymnasium and NumPy, and
`training.rl` re-exports it, so importing anything from that package needs them.
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
        self._frames_since_bomb: int | None = None
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
        observation = self.env.reset()
        self._observation = observation
        self._frames_since_bomb = None
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
        self._advance_bomb_history(bomb=bool(model_action.bomb), frames=delta_frames)
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

    def pause(self) -> tuple[np.ndarray, dict[str, object]]:
        """Freeze a rollout, or report a run that ended across the boundary."""
        self._require_live_episode("pause")
        previous = self._observation
        if previous is None:
            raise RuntimeError("pause requires an observation")
        previous_frames = self.env.frames
        observation = self.env.pause()
        self._observation = observation
        delta_frames = self.env.frames - previous_frames
        self._advance_bomb_history(bomb=False, frames=delta_frames)
        terminated = observation.snapshot.game_over
        total = RewardBreakdown(0.0, 0.0, 0.0, 0.0, 0.0)
        if terminated:
            total = memory_reward(
                previous.snapshot,
                observation.snapshot,
                Action.NONE,
                frames=max(1, delta_frames),
                spec=self.reward_spec,
            )
            self._episode_done = True
        info = self._info({}, delta_frames=delta_frames)
        info.update(
            {
                "terminated": terminated,
                "reward/survival": total.survival,
                "reward/score": total.score,
                "reward/life": total.life,
                "reward/bomb": total.bomb,
                "reward/game_over": total.game_over,
                "reward/total": total.total,
            }
        )
        return self._encode(observation), info

    def resume(self) -> tuple[np.ndarray, dict[str, object]]:
        """Leave a verified pause and report a late terminal boundary."""
        self._require_live_episode("resume")
        previous = self._observation
        if previous is None:
            raise RuntimeError("resume requires an observation")
        previous_frames = self.env.frames
        observation = self.env.resume()
        self._observation = observation
        delta_frames = self.env.frames - previous_frames
        self._advance_bomb_history(bomb=False, frames=delta_frames)
        terminated = observation.snapshot.game_over
        total = RewardBreakdown(0.0, 0.0, 0.0, 0.0, 0.0)
        if terminated:
            total = memory_reward(
                previous.snapshot,
                observation.snapshot,
                Action.NONE,
                frames=max(1, delta_frames),
                spec=self.reward_spec,
            )
            self._episode_done = True
        info = self._info({}, delta_frames=delta_frames)
        info.update(
            {
                "terminated": terminated,
                "reward/survival": total.survival,
                "reward/score": total.score,
                "reward/life": total.life,
                "reward/bomb": total.bomb,
                "reward/game_over": total.game_over,
                "reward/total": total.total,
            }
        )
        return self._encode(observation), info

    def close(self) -> None:
        """Close the core environment this adapter built."""
        self.env.close()

    def stop(self) -> None:
        """Interrupt the current episode and release any held game input."""
        self.env.stop()
        self._episode_done = True

    @property
    def raw_observation(self) -> Observation:
        """The memory observation matching the current encoded observation.

        DAgger teachers need the variable-length game objects that the model's
        fixed feature vector intentionally discards. Exposing the synchronized
        value here keeps the trainer out of the adapter's core environment.
        """
        if self._observation is None:
            raise RuntimeError("reset() must be called before reading raw_observation")
        return self._observation

    @property
    def frames_since_bomb(self) -> int | None:
        """Measured game frames since the learner's latest bomb, if any."""
        return self._frames_since_bomb

    def _encode(self, observation: Observation) -> np.ndarray:
        return np.asarray(
            self.encoder.encode(
                observation, frames_since_bomb=self._frames_since_bomb
            ),
            dtype=np.float32,
        )

    def _advance_bomb_history(self, *, bomb: bool, frames: int) -> None:
        if bomb:
            self._frames_since_bomb = max(0, frames - 1)
        elif self._frames_since_bomb is not None:
            self._frames_since_bomb += frames

    def _require_live_episode(self, operation: str) -> None:
        if self._observation is None:
            raise RuntimeError(f"reset() must be called before {operation}()")
        if self._episode_done:
            raise RuntimeError(
                f"reset() must be called after the episode ends, before {operation}()"
            )

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
                "frames_since_bomb": self._frames_since_bomb,
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
