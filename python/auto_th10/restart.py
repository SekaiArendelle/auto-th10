"""The key sequences the agent needs but never decides.

These belong next to the binding rather than in the training layer: they are
knowledge about driving the game through its menus, and the episode lifecycle in
env.py has to call them.

Every sequence here is driven by observation - the game's own state says when a
press has landed - because a fixed sleep is either too short on a loaded machine
or wasted time on an idle one.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from .session import Action, GameplayNotActive, Session

TAP_SECONDS = 0.1
"""How long a menu key is held: long enough for a frame to see it, short enough
not to be read as two presses."""

POLL_SECONDS = 0.01
"""How often a predicate is re-checked while waiting for the game."""


def tap(session: Session, action: Action | int, seconds: float = TAP_SECONDS) -> None:
    """Presses `action` for `seconds`, then releases everything."""
    session.set_input(action)
    time.sleep(seconds)
    session.set_input(Action.NONE)


def wait_for(
    session: Session,
    predicate: Callable[[Session], bool],
    *,
    timeout_s: float,
    poll_s: float = POLL_SECONDS,
) -> None:
    """Polls `predicate` until it holds, or raises TimeoutError."""
    deadline = time.monotonic() + timeout_s
    while True:
        if predicate(session):
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(f"the game did not reach the expected state within {timeout_s:g} s")
        time.sleep(poll_s)


def leave_game_over(session: Session, *, timeout_s: float) -> None:
    """Presses through the ending until the game is back in a stage.

    The ending is not one screen. There is the ending itself, and then the menu
    the game puts up to ask whether to play on, and no stage exists behind
    either of them - reading a snapshot there raises. So waiting for `game_over`
    to clear is not enough, and one press is not enough either: the confirms
    have to keep coming until a stage is there again.

    Verified against a running game for the plain ending: <Z> clears the ending,
    and further <Z> presses walk the menu into the next run. The other ending -
    the one a record-breaking run gets - asks for a name first, which type_name()
    does not do yet.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        tap(session, Action.SHOOT)
        if _in_a_playable_stage(session):
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"the game did not leave the ending within {timeout_s:g} s"
            )


def _in_a_playable_stage(session: Session) -> bool:
    """Whether the game is in a stage that has not ended.

    Between one run and the next the game has no stage at all, and reading a
    snapshot on those screens raises. That is not a failure here - it is the
    answer "not yet", which is why the press loop keeps going.
    """
    try:
        return not session.snapshot().game_over
    except GameplayNotActive:
        return False


def type_name(session: Session) -> None:
    """Enters a name on the record screen. Deliberately not implemented.

    The sequence is the one unknown left in the restart path: how many
    characters the game wants and whether the screen can be skipped have not been
    pinned down against a running game yet. Until they are, OnNameEntry.TYPE must
    not be used - it stops here instead of guessing at the keyboard.
    """
    raise NotImplementedError(
        "the name entry sequence has not been pinned down yet: OnNameEntry.TYPE is not usable"
    )
