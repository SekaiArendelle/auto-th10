"""The key sequences the agent needs but never decides.

These belong next to the binding rather than in the training layer: they are
knowledge about driving the game through its menus, and the episode lifecycle in
env.py has to call them.

Waiting here is driven by observation - the game's own state says when a press has
landed - rather than by a sleep long enough for the worst machine. The presses
themselves are spaced by a fixed interval instead, because that interval is what
lets the game see each one as its own press, and there is nothing to observe about
a press that has not arrived yet.
"""

import time

from .session import Action, GameplayNotActive, Scene, ScreenKind, Session

TAP_SECONDS = 0.15
"""How long a menu key is held, inside the window docs/game-ui.md measured.

That window is 150-200 ms: long enough for a frame to see the key, short enough
that the game does not read it as two presses. The module held it for 100 ms, which
the same notes record as below the window that works."""

POLL_SECONDS = 0.01
"""How often a predicate is re-checked while waiting for the game."""

STEP_SECONDS = 0.35
"""How long the game is given to answer one menu press.

A press and the screen it causes are a step apart (docs/game-ui.md), so a sequence
has to land on the screen the previous press opened rather than on the one it was
sent from. The same gap is the release: the game's menus repeat a held direction,
and two presses that run together are read as one long press, which moves the
cursor several entries instead of one."""

NAME_ENTRY_COLUMNS = 13
"""How many cells one row of the name entry's grid holds.

Measured by moving along a row: cell 12 is the row's last and the next press wraps
to cell 0, so the rows are separate lists rather than one long one."""

NAME_ENTRY_LAST_ROW = 6
"""The grid's last row, which is the one holding `終`.

That row runs from cell 78 to cell 90 and wraps there, measured the same way; the
rows above it hold 13 cells each, so this is the last row and not an estimate."""

NAME_ENTRY_DONE = NAME_ENTRY_LAST_ROW * NAME_ENTRY_COLUMNS + NAME_ENTRY_COLUMNS - 1
"""The cell that finishes the name entry: `終`, the grid's bottom right corner.

It has to be selected and confirmed; nothing else on the screen writes the record.
Measured at cell 90 - the last cell of the last row - by driving the cursor onto
it and confirming, which saved the name and put the ending's menu back on screen.
"""

MENU_CONTINUE = 0
"""The ending menu's `継続する / Continue`, its first entry.

The menu opens on `Quit and Return to Select` - its last entry - so the cursor is
read and moved rather than counted to: the same three entries are one press apart
from a replay save, and a press too many there is the one outcome this module must
not produce.
"""


class NotAnEnding(RuntimeError):
    """The game is not on an ending's own menu, so there is nothing here to press through.

    Raised in place of pressing on. A menu screen is where this shows up: the title
    and the setup screens are where the ending's own default entry leads, and the way
    back into a run from there goes through the screens the player is meant to choose
    on (see env.py). The name entry is the other case - it is an ending, but this
    layer only leaves it on the caller's say-so. The environment turns this into its
    own refusal, so a caller of the environment still sees the one NotInStage.
    """


def tap(session: Session, action: Action | int, seconds: float = TAP_SECONDS) -> None:
    """Presses `action` for `seconds`, then releases everything."""
    session.set_input(action)
    time.sleep(seconds)
    session.set_input(Action.NONE)


def _press(session: Session, action: Action | int) -> None:
    """Presses once and leaves the keyboard clear long enough for the next press.

    The focus is asked for again before every press. A window that has lost it is
    sent nothing, and that loss is silent - a press that never arrives looks exactly
    like a menu that ignores input (docs/game-ui.md) - so a sequence that asked once
    at the start would only work while nothing else took the foreground away.

    Both constants are read here rather than captured as defaults, which is what
    lets a test take the waiting out.
    """
    session.focus()
    tap(session, action, seconds=TAP_SECONDS)
    time.sleep(STEP_SECONDS)


def leave_game_over(session: Session, *, timeout_s: float) -> None:
    """Presses through the ending's menu into the next run.

    The ending's menu is three entries - `継続する / Continue`, `リプレイを保存する /
    Save Replay`, `タイトル画面に戻る / Quit and Return to Select` - and it opens on
    the last of them, one press away from the replay save whose name entry has no
    scripted way out (docs/game-ui.md). It is therefore walked by reading the cursor
    rather than by counting presses: open the menu if the ending is still wearing
    none, move the highlight onto `Continue`, and confirm only then.

    `Continue` starts the next run on the spot, keeping the difficulty and the
    character already chosen, which is why it is the entry to aim for: the way into
    a run from the title goes through RANK, PLAYER SELECT and WEAPON SELECT, and
    those are the player's choices rather than this layer's.

    The name entry is a different screen behind the same ending and is *not* handled
    here - see `leave_name_entry()` - because whether to write a record is a decision
    rather than a key sequence. A caller that reaches here with it on screen is
    refused.
    """
    # A key already held would swallow the first press: the mask would not change,
    # so the game would see no new press at all.
    session.set_input(Action.NONE)
    if session.scene() is Scene.MENU:
        # The title and the menus around a run are not endings: their first entries
        # start a run, a replay or a practice game rather than leave an ending, and
        # the entry a cursor would be moved onto is the one that starts one. An
        # ending keeps the stage family, which is what this rules out.
        raise _not_an_ending()
    _walk_to_continue(session, timeout_s=timeout_s)

    deadline = time.monotonic() + timeout_s
    while True:
        if _in_a_playable_stage(session):
            return
        if session.scene() is Scene.MENU:
            raise _not_an_ending()
        if time.monotonic() >= deadline:
            raise TimeoutError(f"the game did not leave the ending within {timeout_s:g} s")
        time.sleep(POLL_SECONDS)


def _walk_to_continue(session: Session, *, timeout_s: float) -> None:
    """Puts the ending's menu on screen, highlights `継続する` on it, and confirms it.

    Two states have to be told apart on the way. An ending that has not been
    answered wears no menu at all - just the caption over the field - and one
    `SHOOT` opens it; an ending whose menu is already up, which is what the name
    entry leaves behind, must not be confirmed again before the highlight has been
    read, because that menu opens on the entry next to the replay save.

    The id the menu is recognised by is not always available: the game answers with
    ids of its own in the middle of a stage and for a frame or two between two of
    its screens. Anything that is not the menu is therefore read as "no menu yet"
    and gets the press that opens one, and an entry is only confirmed once it has
    been read. The presses are spaced by `_press`, so a transition has ended before
    the next read rather than after it.

    The menu wraps at both ends, so `down` reaches `継続する` from every entry.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        state = session.screen()
        if state.kind is ScreenKind.NAME_ENTRY:
            raise _not_an_ending()
        if state.kind is ScreenKind.MENU:
            if state.cursor == MENU_CONTINUE:
                _press(session, Action.SHOOT)  # starts the next run on the spot
                return
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"the ending menu did not reach entry {MENU_CONTINUE} "
                    f"within {timeout_s:g} s"
                )
            _select_menu_entry(session, MENU_CONTINUE)
            continue
        if time.monotonic() >= deadline:
            raise _not_an_ending()
        _press(session, Action.SHOOT)  # the ending has no menu of its own


def leave_name_entry(session: Session, *, timeout_s: float) -> None:
    """Drives the name entry's cursor onto `終` and confirms it.

    The screen is the difficulty's own ranking with a character grid under it, and
    the game asks for a name because the run reached that ranking - a lower bar than
    the high score, and one a driver has to answer or sit on forever.

    The grid is a list the game lays out 13 cells per row, and the cell that finishes
    it (`終`) is the last one. Its cursor is written directly and read back before
    anything is confirmed; a failed write stops the sequence rather than falling
    back to direction keys.

    Confirming writes the record with the name already in the buffer, which is the
    game's own default unless something typed into the grid. It leaves the ending's
    menu on screen, so a caller that wants the next run calls `leave_game_over()`
    afterwards.
    """
    deadline = time.monotonic() + timeout_s
    cells = (NAME_ENTRY_LAST_ROW + 1) * NAME_ENTRY_COLUMNS
    while True:
        state = session.screen()
        if state.kind is not ScreenKind.NAME_ENTRY:
            return  # already left, by us or by whoever was here first
        if not 0 <= state.cursor < cells:
            raise RuntimeError(
                f"the name entry reported cell {state.cursor}, outside 0..{cells - 1}"
            )
        if state.cursor == NAME_ENTRY_DONE:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"the name entry did not reach 終 within {timeout_s:g} s "
                f"(the cursor is at cell {state.cursor})"
            )
        _select_name_cell(session, NAME_ENTRY_DONE)

    session.focus()
    _press(session, Action.SHOOT)
    # The confirm is repeated rather than sent once, and with the menu's own hold
    # rather than the grid's shorter one. It is the one press whose failure is
    # silent - a window that has lost the focus is sent nothing and the screen looks
    # exactly the same - and a second confirm on the cell that finishes the screen
    # writes nothing twice, so retrying is free where guessing once is not.
    #
    # The wait ends on the menu and not on "anything else": the game reports an id of
    # its own for a frame or two while it switches screens, and reading that as "gone"
    # is what handed the caller a game that was still on the grid.
    deadline = time.monotonic() + timeout_s
    while True:
        state = session.screen()
        if state.kind is ScreenKind.MENU:
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(f"the name entry did not close within {timeout_s:g} s")
        if state.kind is ScreenKind.NAME_ENTRY:
            _press(session, Action.SHOOT)  # the confirm did not take; try again
        else:
            time.sleep(POLL_SECONDS)  # between screens: wait for the game to settle


def _select_name_cell(session: Session, target: int) -> None:
    """Writes the name entry's cursor directly onto `target`.

    The shortest way is to write the cell: it is a word the game keeps for its own
    key handling to move, so writing it lands the highlight where eighteen presses
    would have, and it does not need the window to hold the foreground. Measured by
    writing the cell and confirming - the game writes the record from it, so the
    write is what it read.

    Direction-key fallback is deliberately disabled: if the binding cannot write
    the cursor, or the readback is not the requested cell, continuing would put
    input into a screen whose state the sequence has not established.
    """
    cursor = session.set_screen_cursor(target)
    if cursor != target:
        raise AssertionError(
            f"the name entry cursor write reported cell {cursor}, expected {target}"
        )


def _select_menu_entry(session: Session, target: int) -> None:
    """Puts the ending menu's highlight on `target`, or stops on a bad write.

    Direction-key fallback is deliberately disabled for the same reason as on the
    name entry: confirmation is only safe after the requested cursor was read back.
    """
    cursor = session.set_screen_cursor(target)
    if cursor != target:
        raise AssertionError(
            f"the ending menu cursor write reported entry {cursor}, expected {target}"
        )


def _not_an_ending() -> NotAnEnding:
    """The refusal for a game that is somewhere other than on an ending's menu.

    A menu screen is one place this is reached from: the title and the setup screens
    are where the ending's own default entry leads, and the replay list is one press
    away from it. The name entry is the other, and it is the reason this is worded
    around the *menu* rather than around endings: that screen is an ending too, but
    the keys that leave it are sent only from `leave_name_entry()`, so a caller that
    arrives here with it on screen has skipped a decision rather than found a menu.
    """
    return NotAnEnding(
        "the game is not on an ending's own menu: "
        "leave the name entry or enter a stage before restarting the agent"
    )


def _in_a_playable_stage(session: Session) -> bool:
    """Whether the game is in a run that has not ended.

    The ending itself is a stage that is over - the snapshot still reads, and says
    so. The menu behind the ending, and the loading screen after it, have no stage
    at all: reading a snapshot there raises. That is not a failure here, it is the
    answer "not yet", which is what the wait after the presses sits through.
    """
    try:
        return not session.snapshot().game_over
    except GameplayNotActive:
        return False
