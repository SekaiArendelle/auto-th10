import unittest

from auto_th10 import Action
from auto_th10.restart import leave_game_over, tap, type_name, wait_for
from fakes import FakeSession, make_snapshot


class TapTests(unittest.TestCase):
    def test_presses_then_releases(self) -> None:
        session = FakeSession()

        tap(session, Action.SHOOT, seconds=0)

        self.assertEqual(session.inputs, [Action.SHOOT, Action.NONE])


class WaitForTests(unittest.TestCase):
    def test_returns_as_soon_as_the_predicate_holds(self) -> None:
        session = FakeSession()

        wait_for(session, lambda game: True, timeout_s=1.0)

    def test_raises_when_the_predicate_never_holds(self) -> None:
        session = FakeSession()

        with self.assertRaises(TimeoutError):
            wait_for(session, lambda game: False, timeout_s=0.01, poll_s=0.001)


class LeaveGameOverTests(unittest.TestCase):
    def test_confirms_the_ending_and_waits_for_the_next_run(self) -> None:
        # The ending screen is one read, the new run is the next: it presses
        # until the stage is back, then stops.
        session = FakeSession(
            snapshots=(make_snapshot(lives=-1, game_over=True), make_snapshot(score=1))
        )

        leave_game_over(session, timeout_s=1.0)

        self.assertEqual(session.inputs[-2:], [Action.SHOOT, Action.NONE])
        self.assertNotIn(Action.SHOOT, session.inputs[-1:])

    def test_presses_on_through_the_screens_that_have_no_stage(self) -> None:
        # Between the ending and the next run there is a menu with no stage
        # behind it, and reading a snapshot there raises: the presses have to
        # carry on rather than give up.
        session = FakeSession(
            snapshots=(make_snapshot(lives=-1, game_over=True), make_snapshot(score=4)),
            no_stage_for=2,
        )

        leave_game_over(session, timeout_s=2.0)

        self.assertEqual(len(session.inputs), 8)
        self.assertEqual(session.inputs[-1], Action.NONE)

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


class TypeNameTests(unittest.TestCase):
    def test_is_not_implemented(self) -> None:
        with self.assertRaises(NotImplementedError):
            type_name(FakeSession())


if __name__ == "__main__":
    unittest.main()
