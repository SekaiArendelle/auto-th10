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

from .session import Action, Session

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
    """Confirms the game over screen and waits for the game to leave it.

    Verified for the plain ending: <Z> on the default entry starts the next run.
    The other ending - the one a record-breaking run gets - asks for a name
    first, which type_name() does not do yet.
    """
    tap(session, Action.SHOOT)
    wait_for(session, lambda game: not game.snapshot().game_over, timeout_s=timeout_s)


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
