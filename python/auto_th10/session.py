from __future__ import annotations

import os
from enum import Enum, IntFlag
from types import TracebackType

from . import _native
from .types import Snapshot


class Action(IntFlag):
    NONE = _native.NONE
    LEFT = _native.LEFT
    RIGHT = _native.RIGHT
    UP = _native.UP
    DOWN = _native.DOWN
    SHOOT = _native.SHOOT
    FOCUS = _native.FOCUS
    BOMB = _native.BOMB
    ESCAPE = _native.ESCAPE


class State(str, Enum):
    """The screen the game is on, straight from the game's own state word.

    PLAYING and PAUSED are worth distinguishing: a paused stage still reads as a
    stage, and only the frame counter tells them apart.

    Derives from str so a member compares equal to the raw name the C side
    returns, without needing StrEnum (which would raise the project's Python
    floor to 3.11).
    """

    UNKNOWN = "TH10_STATE_UNKNOWN"
    MENU = "TH10_STATE_MENU"
    PLAYING = "TH10_STATE_PLAYING"
    PAUSED = "TH10_STATE_PAUSED"
    GAME_OVER = "TH10_STATE_GAME_OVER"


class Session:
    def __init__(self) -> None:
        self._native = _native.Session()

    def close(self) -> None:
        self._native.close()

    def focus(self) -> None:
        self._native.focus()

    def set_input(self, action: Action | int) -> None:
        self._native.set_input(int(action))

    def snapshot(self) -> Snapshot:
        return Snapshot.from_native(self._native.snapshot())

    def state(self) -> State:
        return State(self._native.state())

    def stage_frames(self) -> int:
        """The game's own clock: it advances while a stage is playing and freezes
        while it is paused.

        Reading it twice tells "the game moved" from "the game stopped" without
        the ~120 ms `state()` spends on the same answer.
        """
        return self._native.stage_frames()

    def record_broken(self) -> bool:
        """Whether the run that just ended set a new high score.

        Read this after State.GAME_OVER: a set flag means the game is about to
        ask for a name and a restart has to type one, while a clear flag means
        confirming the game over menu is enough.
        """
        return self._native.record_broken()

    def capture(self, path: str | os.PathLike[str]) -> tuple[int, int]:
        """Write the game window into `path` as a BMP; returns (width, height)."""
        return self._native.capture(os.fspath(path))

    def __enter__(self) -> Session:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
