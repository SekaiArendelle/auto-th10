import os
from enum import IntFlag, StrEnum
from types import TracebackType

from . import _native
from .types import ScreenState, Snapshot

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


class Scene(StrEnum):
    """The family of screen the game is on, straight from its own state word.

    One read of one word, with no waiting: it answers "a menu or a stage" and
    nothing finer. That is exactly the question a driver has to ask before it
    injects a key - a menu is six screens under one value and a stage is playing,
    paused and over alike - and for anything past it, read `snapshot()` for the
    run's own numbers or `stage_frames()` twice for playing against paused.

    A StrEnum, so a member compares equal to the raw name the C side returns
    and prints as that name rather than as an enum literal.
    """

    UNKNOWN = "TH10_SCENE_UNKNOWN"
    MENU = "TH10_SCENE_MENU"
    STAGE = "TH10_SCENE_STAGE"


class ScreenKind(StrEnum):
    """The kind of screen the game is driving, straight from the game's own id.

    This is finer than `Scene` and answers a different question. `Scene` says
    which family the game is in - a menu or a stage - and is deliberately blind
    to everything below that; `ScreenKind` names the page itself, and knows only
    the three the binding has measured (a stage, a menu, the Score Ranking name
    entry). Every other page answers UNKNOWN, and the game has several.

    It exists because it is the only read that tells an ending's menu from the
    name entry behind it: both run inside a stage family with the run over, so
    nothing else an agent can see separates them, and a restart that guesses
    wrong types a name into the ranking.

    A StrEnum, so a member compares equal to the raw name the C side returns
    and prints as that name rather than as an enum literal.
    """

    UNKNOWN = "TH10_SCREEN_KIND_UNKNOWN"
    STAGE = "TH10_SCREEN_KIND_STAGE"
    MENU = "TH10_SCREEN_KIND_MENU"
    NAME_ENTRY = "TH10_SCREEN_KIND_NAME_ENTRY"


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

    def screen(self) -> ScreenState:
        """The screen the game is driving, and that screen's cursor.

        One pointer chase and one read, with nothing to wait for. The returned
        `ScreenState.kind` is the page itself, finer than `scene()`'s family, and
        this is the read that tells an ending's menu from the name entry behind
        it: both run inside a stage family with the run over, and nothing else an
        agent can see separates them.
        """
        kind, cursor = self._native.screen()
        return ScreenState(kind=ScreenKind(kind), cursor=int(cursor))

    def set_screen_cursor(self, cursor: int) -> int:
        """Moves that cursor without a key press, and returns what the game reports.

        This is the write side of `screen()`, and the reason it exists is the name
        entry: its grid holds 91 cells and the screen is left by highlighting the
        last of them, so walking there is eighteen key presses that each have to
        arrive. Writing the cell the game keeps is one step, and it changes nothing
        else - not the run, not the score, not the record.

        A screen that keeps no cursor raises `RuntimeError`, and an entry outside
        that screen's list raises `ValueError`. Confirming is still a key press:
        this only puts the highlight where a press would have put it.
        """
        return int(self._native.set_screen_cursor(int(cursor)))

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
