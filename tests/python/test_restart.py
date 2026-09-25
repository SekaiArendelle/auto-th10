import unittest
from unittest import mock

from auto_th10 import Action, Scene, ScreenKind, ScreenState
from auto_th10 import restart as restart_module
from auto_th10.restart import (
    NAME_ENTRY_DONE,
    NotAnEnding,
    leave_game_over,
    leave_name_entry,
    tap,
)
from fakes import FakeSession, make_snapshot


class TapTests(unittest.TestCase):
    def test_presses_then_releases(self) -> None:
        session = FakeSession()

        tap(session, Action.SHOOT, seconds=0)

        self.assertEqual(session.inputs, [Action.SHOOT, Action.NONE])

    def test_an_interrupted_hold_still_releases(self) -> None:
        session = FakeSession()

        with mock.patch.object(restart_module.time, "sleep", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                tap(session, Action.SHOOT)

        self.assertEqual(session.inputs, [Action.SHOOT, Action.NONE])


class NoWaiting:
    """Takes the spacing between presses out: a fake answers instantly, so the
    real constants would only make the suite slower."""

    def setUp(self) -> None:
        for name in ("TAP_SECONDS", "STEP_SECONDS"):
            patcher = mock.patch.object(restart_module, name, 0.0)
            patcher.start()
            self.addCleanup(patcher.stop)


class LeaveGameOverTests(NoWaiting, unittest.TestCase):
    def test_opens_the_menu_and_confirms_continue(self) -> None:
        # An ending that has not been answered wears no menu, so the first press
        # opens one; it opens with its cursor on its last entry, Quit and Return to
        # Select, which is one press from the replay save this must never touch. The
        # highlight is then moved onto 継続する by writing the field rather than by
        # pressing a direction, so the entry is only ever confirmed after it has been
        # read.
        session = FakeSession(
            snapshots=(make_snapshot(lives=-1, game_over=True), make_snapshot(score=1)),
        )

        leave_game_over(session, timeout_s=1.0)

        self.assertEqual(
            session.inputs,
            [
                Action.NONE,  # the keyboard starts clear, or the first press is lost
                Action.SHOOT,  # the ending has no menu of its own
                Action.NONE,
                Action.SHOOT,  # starts the next run on the spot
                Action.NONE,
            ],
        )
        self.assertEqual(session.screen_cursor, 0)  # left on Continue, as written

    def test_leaves_a_menu_that_is_already_open_alone(self) -> None:
        # After the name entry the menu is already up, and with its cursor on
        # Continue: opening it again would confirm whatever entry it holds.
        session = FakeSession(
            snapshots=(make_snapshot(lives=-1, game_over=True), make_snapshot(score=1)),
            screen_kind=ScreenKind.MENU,
            screen_cursor=0,
        )

        leave_game_over(session, timeout_s=1.0)

        self.assertEqual(
            session.inputs,
            [Action.NONE, Action.SHOOT, Action.NONE],
        )

    def test_refuses_a_pause_menu_without_pressing_anything(self) -> None:
        # A paused run is not an ending: `SHOOT` on this screen resumes the run
        # rather than opening a menu, so the sequence has to refuse it.
        session = FakeSession(screen_kind=ScreenKind.PAUSE_MENU, screen_cursor=0)

        with self.assertRaisesRegex(NotAnEnding, "not on an ending"):
            leave_game_over(session, timeout_s=1.0)

        self.assertEqual(session.inputs, [Action.NONE])

    def test_refuses_a_pause_confirmation_without_pressing_anything(self) -> None:
        # The confirmation keeps its cursor in the pause menu's field, so a
        # confirm here answers whatever entry a caller found it on.
        session = FakeSession(screen_kind=ScreenKind.PAUSE_CONFIRM, screen_cursor=0)

        with self.assertRaisesRegex(NotAnEnding, "not on an ending"):
            leave_game_over(session, timeout_s=1.0)

        self.assertEqual(session.inputs, [Action.NONE])

    def test_waits_out_the_loading_screen_without_pressing_again(self) -> None:
        # Continue starts the next run immediately, but its stage still has to
        # load, and reading a snapshot there raises. That is waited out rather than
        # answered with more presses.
        session = FakeSession(
            snapshots=(make_snapshot(lives=-1, game_over=True), make_snapshot(score=4)),
            no_stage_for=3,
        )

        leave_game_over(session, timeout_s=2.0)

        self.assertEqual(session.inputs.count(Action.SHOOT), 2)
        self.assertEqual(session.inputs.count(Action.DOWN), 0)  # the highlight was written

    def test_stops_when_the_menu_cursor_cannot_be_written(self) -> None:
        session = FakeSession(
            snapshots=(make_snapshot(lives=-1, game_over=True),),
            refuse_cursor_writes=True,
        )

        with self.assertRaisesRegex(RuntimeError, "keeps no cursor"):
            leave_game_over(session, timeout_s=1.0)

        self.assertEqual(session.inputs, [Action.NONE, Action.SHOOT, Action.NONE])

    def test_asserts_that_the_menu_cursor_write_was_read_back(self) -> None:
        class MisreportingSession(FakeSession):
            def set_screen_cursor(self, cursor: int) -> int:
                super().set_screen_cursor(cursor)
                return cursor + 1

        session = MisreportingSession(
            snapshots=(make_snapshot(lives=-1, game_over=True),),
        )

        with self.assertRaisesRegex(AssertionError, "expected 0"):
            leave_game_over(session, timeout_s=1.0)

        self.assertEqual(session.inputs, [Action.NONE, Action.SHOOT, Action.NONE])

    def test_refuses_the_name_entry(self) -> None:
        # The name entry is an ending too, but leaving it means writing a record,
        # which is the caller's decision rather than this sequence's. Nothing is
        # sent into the grid.
        session = FakeSession(
            snapshots=(make_snapshot(lives=-1, game_over=True),),
            screen_kind=ScreenKind.NAME_ENTRY,
            screen_cursor=0,
        )

        with self.assertRaises(NotAnEnding):
            leave_game_over(session, timeout_s=1.0)

        self.assertEqual(session.inputs, [Action.NONE])

    def test_a_screen_that_never_becomes_a_menu_is_refused(self) -> None:
        # A game that answers with an id this binding does not know - or ignores the
        # press altogether - must not be walked blindly. The wait ends in the
        # refusal, and the only things sent were presses that would open a menu:
        # never a direction, which is what would move a highlight nobody has read.
        class StuckSession(FakeSession):
            def set_input(self, action: object) -> None:
                self.inputs.append(action)  # the game never answers

        session = StuckSession(
            snapshots=(make_snapshot(lives=-1, game_over=True),),
            screen_kind=ScreenKind.UNKNOWN,
        )

        with self.assertRaises(NotAnEnding):
            leave_game_over(session, timeout_s=0.05)

        self.assertEqual(set(session.inputs) - {Action.NONE, Action.SHOOT}, set())

    def test_refuses_a_screen_with_no_ended_run_behind_it(self) -> None:
        # A title menu answers with a menu id as well, and its first entry is GAME
        # START: moving onto it and confirming would start a run rather than leave
        # an ending. The screen family is what tells the two apart - an ending
        # keeps the stage family - and nothing is pressed.
        session = FakeSession(
            scenes=(Scene.MENU,),
            screen_kind=ScreenKind.MENU,
            screen_cursor=2,
        )

        with self.assertRaises(NotAnEnding):
            leave_game_over(session, timeout_s=1.0)

        self.assertEqual(session.inputs, [Action.NONE])

    def test_refuses_a_game_that_falls_back_to_the_title_while_waiting(self) -> None:
        # The presses land, and the game answers by going back to the title.
        session = FakeSession(
            snapshots=(make_snapshot(lives=-1, game_over=True),),
            scenes=(Scene.STAGE, Scene.STAGE, Scene.MENU),
        )

        with self.assertRaises(NotAnEnding):
            leave_game_over(session, timeout_s=1.0)

    def test_times_out_when_the_game_stays_on_the_ending(self) -> None:
        session = FakeSession(snapshots=(make_snapshot(lives=-1, game_over=True),))

        with self.assertRaises(TimeoutError):
            leave_game_over(session, timeout_s=0.01)

    def test_an_unexpected_runtime_error_is_not_treated_as_a_loading_screen(self) -> None:
        class BrokenSession(FakeSession):
            def snapshot(self) -> object:
                raise RuntimeError("unexpected snapshot failure")

        with self.assertRaisesRegex(RuntimeError, "unexpected snapshot failure"):
            leave_game_over(BrokenSession(), timeout_s=1.0)


class LeaveNameEntryTests(NoWaiting, unittest.TestCase):
    def test_writes_the_cursor_onto_terminal_and_confirms_it(self) -> None:
        # 終 is the grid's last cell, and the grid's cursor is a word the game keeps
        # for its own key handling to move: writing it puts the highlight there in
        # one step, where a walk would have taken eighteen presses. Confirming is
        # still a press.
        session = FakeSession(screen_kind=ScreenKind.NAME_ENTRY, screen_cursor=0)

        leave_name_entry(session, timeout_s=5.0)

        self.assertEqual(session.inputs.count(Action.SHOOT), 1)
        self.assertEqual(set(session.inputs) - {Action.NONE, Action.SHOOT}, set())
        # 終 writes the record and puts the ending's own menu back on screen.
        self.assertIs(session.screen_kind, ScreenKind.MENU)
        self.assertEqual(session.screen_cursor, 0)

    def test_stops_when_the_name_entry_cursor_cannot_be_written(self) -> None:
        session = FakeSession(
            screen_kind=ScreenKind.NAME_ENTRY, screen_cursor=0, refuse_cursor_writes=True
        )

        with self.assertRaisesRegex(RuntimeError, "keeps no cursor"):
            leave_name_entry(session, timeout_s=5.0)

        self.assertEqual(session.inputs, [])
        self.assertIs(session.screen_kind, ScreenKind.NAME_ENTRY)

    def test_asserts_that_the_name_entry_cursor_write_was_read_back(self) -> None:
        class MisreportingSession(FakeSession):
            def set_screen_cursor(self, cursor: int) -> int:
                super().set_screen_cursor(cursor)
                return cursor - 1

        session = MisreportingSession(screen_kind=ScreenKind.NAME_ENTRY, screen_cursor=0)

        with self.assertRaisesRegex(AssertionError, "expected 90"):
            leave_name_entry(session, timeout_s=5.0)

        self.assertEqual(session.inputs, [])

    def test_does_nothing_when_the_screen_is_already_gone(self) -> None:
        session = FakeSession(screen_kind=ScreenKind.MENU, screen_cursor=0)

        leave_name_entry(session, timeout_s=1.0)

        self.assertEqual(session.inputs, [])

    def test_reports_a_cell_outside_the_grid(self) -> None:
        # A cell the grid does not have is a wrong address or a changed layout, not
        # something to press towards.
        session = FakeSession(screen_kind=ScreenKind.NAME_ENTRY, screen_cursor=NAME_ENTRY_DONE + 1)

        with self.assertRaisesRegex(RuntimeError, "outside"):
            leave_name_entry(session, timeout_s=1.0)

    def test_times_out_when_the_cursor_never_moves(self) -> None:
        class StuckEntry(FakeSession):
            def screen(self) -> ScreenState:
                return ScreenState(kind=ScreenKind.NAME_ENTRY, cursor=0)

        with self.assertRaises(TimeoutError):
            leave_name_entry(StuckEntry(screen_kind=ScreenKind.NAME_ENTRY), timeout_s=0.01)


if __name__ == "__main__":
    unittest.main()
