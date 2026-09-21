from __future__ import annotations

import time

from .session import Action, Session
from .types import Snapshot


class Th10Env:
    """Small environment facade; Gymnasium adaptation belongs in the training layer."""

    def __init__(self, step_seconds: float = 1 / 60) -> None:
        if step_seconds <= 0:
            raise ValueError("step_seconds must be positive")
        self.session = Session()
        self.step_seconds = step_seconds
        self._previous_score = 0

    def reset(self) -> tuple[Snapshot, dict[str, object]]:
        self.session.set_input(Action.NONE)
        self.session.focus()
        snapshot = self.session.snapshot()
        if snapshot.game_over:
            raise RuntimeError("automatic game/menu reset is not implemented yet")
        self._previous_score = snapshot.score
        return snapshot, {}

    def step(self, action: Action | int) -> tuple[Snapshot, float, bool, bool, dict[str, object]]:
        self.session.set_input(action)
        time.sleep(self.step_seconds)
        snapshot = self.session.snapshot()
        reward = float(snapshot.score - self._previous_score)
        self._previous_score = snapshot.score
        return snapshot, reward, snapshot.game_over, False, {}

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> Th10Env:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
