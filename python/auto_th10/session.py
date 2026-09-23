from __future__ import annotations

import os
from enum import Enum, IntFlag
from types import TracebackType

from . import _native
from .types import Snapshot

GameNotFound = _native.GameNotFound
"""Raised when no running TH10 window could be found to attach to."""

GameplayNotActive = _native.GameplayNotActive
"""Raised when no stage object exists for a gameplay snapshot."""

SessionClosedError = _native.SessionClosedError
"""Raised when a closed session is used again."""


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


class Scene(str, Enum):
    """The family of screen the game is on, straight from its own state word.

    One read of one word, with no waiting: it answers "a menu or a stage" and
    nothing finer. That is exactly the question a driver has to ask before it
    injects a key - a menu is six screens under one value and a stage is playing,
    paused and over alike - and for anything past it, read `snapshot()` for the
    run's own numbers or `stage_frames()` twice for playing against paused.

    Derives from str so a member compares equal to the raw name the C side
    returns, without needing StrEnum (which would raise the project's Python
    floor to 3.11).
    """

    UNKNOWN = "TH10_SCENE_UNKNOWN"
    MENU = "TH10_SCENE_MENU"
    STAGE = "TH10_SCENE_STAGE"


class Session:
    """A named wrapper over the native session.

    Everything here is a read or an input, and the coarse "which screen is it"
    word is deliberately not among them: `th10_read_state()` is not bound at all.
    It blocks for about 120 ms to separate playing from paused, and what it
    returns says less than the reads that replace it - `scene()` for the family,
    `snapshot()` for the run, `stage_frames()` twice for a stage that has frozen.
    It is kept for people and for `th10ctl`; the reasoning is in `_native.c` and
    in docs/game-ui.md.
    """

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

    def scene(self) -> Scene:
        """The screen family: a menu, a stage, or a word we do not know.

        One read of one word, with nothing to wait for. Ask this to decide whether
        keys can be injected at all, and ask `snapshot()` or `stage_frames()` for
        anything finer - the family cannot tell a stage that is playing from one
        that is paused.
        """
        return Scene(self._native.scene())

    # No state(): th10_read_state() is deliberately unbound, for the reasons in
    # the class docstring. The C API keeps it for people and for th10ctl.

    def stage_frames(self) -> int:
        """The game's own clock: it advances while a stage is playing and freezes
        while it is paused.

        Reading it twice is the only way to tell those apart - they share a screen
        family, and the pause menu raises no flag that a single read could see.
        """
        return self._native.stage_frames()

    def record_broken(self) -> bool:
        """Whether the run that just ended set a new high score.

        Read this once the run is over: a set flag means the game is about to ask
        for a name and a restart has to type one, while a clear flag means
        confirming the game over menu is enough. A read that fails raises
        `OSError` instead of answering false, so a broken read is never taken for
        "no name is due".
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
