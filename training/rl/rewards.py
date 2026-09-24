"""Reward shaping over consecutive memory snapshots."""

import math
import operator
from dataclasses import dataclass
from typing import NamedTuple

from auto_th10 import Action, Snapshot


@dataclass(frozen=True, slots=True)
class RewardSpec:
    """The scales and penalties that define the first PPO reward."""

    survival_per_frame: float = 0.001
    score_scale: float = 1000.0
    score_clip: float = 1.0
    life_lost_penalty: float = 1.0
    bomb_penalty: float = 0.1
    game_over_penalty: float = 1.0

    def __post_init__(self) -> None:
        for name in (
            "survival_per_frame",
            "score_scale",
            "score_clip",
            "life_lost_penalty",
            "bomb_penalty",
            "game_over_penalty",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be a number")
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
            if value < 0:
                raise ValueError(f"{name} must not be negative")
        if self.score_scale == 0:
            raise ValueError("score_scale must be positive")


class RewardBreakdown(NamedTuple):
    """Individual reward terms, retained so training metrics stay explainable."""

    survival: float
    score: float
    life: float
    bomb: float
    game_over: float

    @property
    def total(self) -> float:
        return sum(self)


def memory_reward(
    previous: Snapshot,
    current: Snapshot,
    action: Action | int,
    *,
    frames: int,
    spec: RewardSpec = RewardSpec(),
) -> RewardBreakdown:
    """Score one transition using only memory data and the applied action."""
    frame_count = _require_integer(frames, "frames")
    if frame_count < 1:
        raise ValueError("frames must be positive")
    action_value = _require_integer(action, "action")

    score_gain = max(0, current.score - previous.score)
    score = min(score_gain / spec.score_scale, spec.score_clip)
    lives_lost = max(0, previous.lives - current.lives)
    newly_game_over = current.game_over and not previous.game_over
    return RewardBreakdown(
        survival=0.0 if current.game_over else spec.survival_per_frame * frame_count,
        score=score,
        life=-spec.life_lost_penalty * lives_lost,
        bomb=-spec.bomb_penalty if action_value & int(Action.BOMB) else 0.0,
        game_over=-spec.game_over_penalty if newly_game_over else 0.0,
    )


def _require_integer(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    try:
        return operator.index(value)
    except TypeError as error:
        raise TypeError(f"{name} must be an integer") from error
