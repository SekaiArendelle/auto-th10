import unittest
from unittest import mock

from auto_th10 import (
    EVAL_PRESET,
    TRAIN_PRESET,
    Action,
    NotInStage,
    OnDeath,
    Scene,
    ScreenKind,
    SessionClosedError,
    Settings,
    Th10Env,
)
from auto_th10 import restart as restart_module
from fakes import FakeSession, make_snapshot


class NoWaiting:
    """Takes the spacing between the restart presses out: a fake session answers
    instantly, so the real constants would only make the suite slower."""

    def setUp(self) -> None:
        super().setUp()
        for name in ("TAP_SECONDS", "STEP_SECONDS"):
            patcher = mock.patch.object(restart_module, name, 0.0)
            patcher.start()
            self.addCleanup(patcher.stop)


class ConstructionTests(unittest.TestCase):
    """Construction stores dependencies but leaves all game inspection to reset()."""

    def test_a_playing_stage_is_accepted(self) -> None:
        session = FakeSession()

        env = Th10Env(session=session)

        self.assertIs(env.session, session)

    def test_a_menu_is_not_inspected_until_reset(self) -> None:
        env = Th10Env(session=FakeSession(scenes=(Scene.MENU,)))

        with self.assertRaises(NotInStage):
            env.reset()

    def test_an_unknown_family_is_not_inspected_until_reset(self) -> None:
        env = Th10Env(session=FakeSession(scenes=(Scene.UNKNOWN,)))

        with self.assertRaises(NotInStage):
            env.reset()


class SettingsTests(unittest.TestCase):
    def test_one_episode_leaves_the_ending_on_screen(self) -> None:
        self.assertIs(Settings.for_episodes(1).on_death, OnDeath.STOP)

    def test_more_than_one_episode_restarts_between_them(self) -> None:
        self.assertIs(Settings.for_episodes(2).on_death, OnDeath.RESTART)

    def test_the_presets_say_what_they_are_documented_to_say(self) -> None:
        self.assertIs(EVAL_PRESET.on_death, OnDeath.STOP)
        self.assertIs(TRAIN_PRESET.on_death, OnDeath.RESTART)


class ResetTests(NoWaiting, unittest.TestCase):

    def test_reset_focuses_the_window_and_returns_the_first_observation(self) -> None:
        session = FakeSession(snapshots=(make_snapshot(score=100),))

        observation = Th10Env(session=session).reset()

        self.assertEqual(observation.snapshot.score, 100)
        self.assertEqual(session.focus_calls, 1)

    def test_reset_clears_an_ending_that_was_already_on_screen(self) -> None:
        session = FakeSession(
            snapshots=(make_snapshot(lives=-1, game_over=True), make_snapshot(score=3)),
        )

        observation = Th10Env(settings=EVAL_PRESET, session=session).reset()

        self.assertEqual(observation.snapshot.score, 3)
        self.assertEqual(session.inputs[-2:], [Action.SHOOT, Action.NONE])

    def test_reset_refuses_an_ending_this_environment_produced(self) -> None:
        session = FakeSession(
            snapshots=(make_snapshot(), make_snapshot(lives=-1, game_over=True)),
        )
        env = Th10Env(settings=EVAL_PRESET, session=session)
        env.reset()
        env.step(Action.RIGHT | Action.SHOOT)

        with self.assertRaises(NotInStage):
            env.reset()

        self.assertEqual(session.inputs[-1], Action.NONE)

    def test_a_second_episode_restarts_when_the_settings_say_so(self) -> None:
        session = FakeSession(
            snapshots=(
                make_snapshot(),
                make_snapshot(lives=-1, game_over=True),
                make_snapshot(score=8),
            ),
        )
        env = Th10Env(settings=TRAIN_PRESET, session=session)
        env.reset()
        env.step(Action.NONE)

        observation = env.reset()

        self.assertEqual(observation.snapshot.score, 8)

    def test_reset_refuses_the_name_entry_when_on_name_entry_is_stop(self) -> None:
        # The ranking asks for a name when a run reaches it, which is a lower bar
        # than a broken high score - the flag stays clear here - so the screen is
        # what the lifecycle reads. Nothing is sent into the grid.
        session = FakeSession(
            snapshots=(make_snapshot(lives=-1, game_over=True),),
            screen_kind=ScreenKind.NAME_ENTRY,
            screen_cursor=0,
        )

        with self.assertRaisesRegex(NotInStage, "waiting for a name"):
            Th10Env(settings=EVAL_PRESET, session=session).reset()

        self.assertEqual(session.inputs, [])

    def test_reset_walks_the_name_entry_out_when_the_settings_say_so(self) -> None:
        # The collecting preset answers the ranking instead of stopping on it: the
        # grid is walked onto 終, the record is written with the game's own default
        # name, and the next run starts from the menu the confirm leaves behind.
        session = FakeSession(
            snapshots=(make_snapshot(lives=-1, game_over=True), make_snapshot(score=8)),
            screen_kind=ScreenKind.NAME_ENTRY,
            screen_cursor=0,
        )

        observation = Th10Env(settings=TRAIN_PRESET, session=session).reset()

        self.assertEqual(observation.snapshot.score, 8)
        self.assertIs(session.screen_kind, ScreenKind.STAGE)

    def test_reset_handles_a_name_entry_that_appears_during_restart(self) -> None:
        # The run's game-over flag can become visible before the ranking installs
        # its screen object. The ordinary restart sees STAGE first and tries to open
        # the ending menu; if the name entry appears around that press, reset must
        # redispatch it instead of surfacing NotAnEnding and aborting evaluation's
        # next episode.
        class LateNameEntrySession(FakeSession):
            def _apply(self, action: object) -> None:
                if self.screen_kind is ScreenKind.STAGE and action == Action.SHOOT:
                    self.screen_kind = ScreenKind.NAME_ENTRY
                    self.screen_cursor = 0
                    return
                super()._apply(action)

        session = LateNameEntrySession(
            snapshots=(
                make_snapshot(),
                make_snapshot(lives=-1, game_over=True),
                make_snapshot(lives=-1, game_over=True),
                make_snapshot(score=8),
            ),
        )
        env = Th10Env(settings=TRAIN_PRESET, session=session)
        env.reset()
        env.step(Action.RIGHT | Action.SHOOT)

        observation = env.reset()

        self.assertEqual(observation.snapshot.score, 8)
        self.assertIs(session.screen_kind, ScreenKind.STAGE)
        self.assertIn(Action.NONE, session.inputs)  # the previous episode's input was released

    def test_reset_refuses_a_stage_that_is_frozen(self) -> None:
        # Past the constructor - the family is a stage - and stopped at reset: the
        # clock does not move, so keys would be going nowhere.
        with self.assertRaises(NotInStage):
            Th10Env(session=FakeSession(frame_step=0)).reset()

    def test_reset_refuses_a_stage_with_no_run_behind_it(self) -> None:
        # Between runs the family can still read as a stage while the stage object
        # is gone, which is what a snapshot that raises means.
        with self.assertRaises(NotInStage):
            Th10Env(session=FakeSession(no_stage_for=99)).reset()

    def test_reset_propagates_an_unexpected_snapshot_runtime_error(self) -> None:
        class BrokenSession(FakeSession):
            def snapshot(self) -> object:
                raise RuntimeError("unexpected snapshot failure")

        with self.assertRaisesRegex(RuntimeError, "unexpected snapshot failure"):
            Th10Env(session=BrokenSession()).reset()

    def test_reset_propagates_a_closed_session(self) -> None:
        # SessionClosedError derives from RuntimeError just as GameplayNotActive
        # does, so this is the case that pins the rule down: the environment
        # swallows one named failure, not the family it belongs to.
        class ClosedSession(FakeSession):
            def snapshot(self) -> object:
                raise SessionClosedError("session is closed")

        with self.assertRaises(SessionClosedError):
            Th10Env(session=ClosedSession()).reset()

    def test_reset_refuses_a_restart_that_lands_at_the_title(self) -> None:
        # The restart sequence reports a game that ended up back at the title
        # rather than pressing on through the setup screens, and the environment
        # turns that into its own refusal: a caller only ever sees NotInStage.
        session = FakeSession(
            snapshots=(make_snapshot(), make_snapshot(lives=-1, game_over=True)),
            scenes=(Scene.STAGE, Scene.STAGE, Scene.MENU),
        )
        env = Th10Env(settings=TRAIN_PRESET, session=session)
        env.reset()
        env.step(Action.NONE)

        with self.assertRaisesRegex(NotInStage, "ending's own menu"):
            env.reset()

        self.assertEqual(session.inputs[-1], Action.NONE)

    def test_a_screen_read_failure_stops_before_confirming_the_ending(self) -> None:
        # The screen is what tells an ending's menu from the name entry behind it,
        # and a read that failed says neither: a confirm sent on the strength of it
        # could type into the ranking. The failure travels out instead.
        session = FakeSession(
            snapshots=(make_snapshot(lives=-1, game_over=True),),
            screen_error=OSError("the game's screen could not be read"),
        )

        with self.assertRaisesRegex(OSError, "screen could not be read"):
            Th10Env(settings=TRAIN_PRESET, session=session).reset()

        self.assertEqual(session.inputs, [])

    def test_a_screen_read_failure_releases_the_previous_episode_input(self) -> None:
        session = FakeSession(
            snapshots=(make_snapshot(), make_snapshot(lives=-1, game_over=True)),
            screen_error=OSError("the game's screen could not be read"),
        )
        env = Th10Env(settings=TRAIN_PRESET, session=session)
        env.reset()
        env.step(Action.RIGHT | Action.SHOOT)

        with self.assertRaisesRegex(OSError, "screen could not be read"):
            env.reset()

        self.assertEqual(session.inputs[-1], Action.NONE)


class StepTests(unittest.TestCase):
    def test_step_before_reset_is_refused_without_touching_the_keyboard(self) -> None:
        # A frozen stage gets past the constructor, because the family is all the
        # constructor asks. step() must not take that as licence to inject: it
        # insists on reset(), which is where the run is actually checked.
        session = FakeSession(frame_step=0)
        env = Th10Env(session=session)

        with self.assertRaises(NotInStage):
            env.step(Action.NONE)

        self.assertEqual(session.inputs, [])

    def test_a_failed_step_requires_another_reset(self) -> None:
        session = FakeSession(freeze_after=2)
        env = Th10Env(session=session, frame_timeout_s=0.02)
        env.reset()

        with self.assertRaises(NotInStage):
            env.step(Action.NONE)
        with self.assertRaisesRegex(NotInStage, "call reset"):
            env.step(Action.NONE)

    def test_a_rejected_input_releases_the_previous_action(self) -> None:
        class RejectingSession(FakeSession):
            def set_input(self, action: object) -> None:
                if int(action) & (1 << 30):
                    raise ValueError("unsupported input bit")
                super().set_input(action)

        session = RejectingSession(snapshots=(make_snapshot(), make_snapshot()))
        env = Th10Env(session=session)
        env.reset()
        env.step(Action.SHOOT)

        with self.assertRaisesRegex(ValueError, "unsupported input bit"):
            env.step(1 << 30)

        self.assertEqual(session.inputs[-1], Action.NONE)
        with self.assertRaisesRegex(NotInStage, "call reset"):
            env.step(Action.NONE)

    def test_a_cleanup_failure_does_not_hide_the_input_error(self) -> None:
        class BrokenInputSession(FakeSession):
            def set_input(self, action: object) -> None:
                if int(action) == int(Action.NONE):
                    raise OSError("release failed")
                if int(action) & (1 << 30):
                    raise ValueError("unsupported input bit")
                super().set_input(action)

        env = Th10Env(session=BrokenInputSession())
        env.reset()
        env.step(Action.SHOOT)

        with self.assertRaisesRegex(ValueError, "unsupported input bit"):
            env.step(1 << 30)
        with self.assertRaisesRegex(NotInStage, "call reset"):
            env.step(Action.NONE)

    def test_step_holds_the_action_and_returns_a_transition(self) -> None:
        session = FakeSession(
            snapshots=(make_snapshot(score=10), make_snapshot(score=25)),
        )
        env = Th10Env(session=session)
        env.reset()

        transition = env.step(Action.SHOOT | Action.FOCUS)

        self.assertEqual(transition.observation.snapshot.score, 10)
        self.assertEqual(transition.next_observation.snapshot.score, 25)
        self.assertEqual(transition.action, Action.SHOOT | Action.FOCUS)
        self.assertEqual(transition.frames, 1)
        self.assertFalse(transition.terminated)
        self.assertEqual(session.inputs, [Action.SHOOT | Action.FOCUS])

    def test_step_reports_game_over_as_terminated(self) -> None:
        session = FakeSession(
            snapshots=(make_snapshot(score=5), make_snapshot(score=5, lives=-1, game_over=True)),
        )
        env = Th10Env(session=session)
        env.reset()

        transition = env.step(Action.NONE)

        self.assertTrue(transition.terminated)
        self.assertEqual(session.inputs[-1], Action.NONE)

    def test_a_terminal_release_failure_still_ends_the_episode(self) -> None:
        class BrokenReleaseSession(FakeSession):
            def set_input(self, action: object) -> None:
                if int(action) == int(Action.NONE):
                    raise OSError("release failed")
                super().set_input(action)

        session = BrokenReleaseSession(
            snapshots=(make_snapshot(), make_snapshot(game_over=True))
        )
        env = Th10Env(session=session)
        env.reset()

        with self.assertRaisesRegex(OSError, "release failed"):
            env.step(Action.SHOOT)
        with self.assertRaisesRegex(NotInStage, "call reset"):
            env.step(Action.SHOOT)

    def test_step_stops_when_the_game_stops_advancing(self) -> None:
        # Two reads of the clock while it is moving get the episode started; after
        # that it freezes, which is what a pause in the middle looks like.
        session = FakeSession(freeze_after=2)
        env = Th10Env(session=session, frame_timeout_s=0.02)
        env.reset()

        with self.assertRaises(NotInStage):
            env.step(Action.NONE)

        self.assertEqual(session.inputs[-1], Action.NONE)

    def test_step_waits_through_the_gap_between_stages(self) -> None:
        session = FakeSession(
            snapshots=(make_snapshot(score=10), make_snapshot(score=25)),
            snapshot_gaps=(2, 3),
            frame_values=(100, 101, 101, 0),
        )
        env = Th10Env(session=session)
        env.reset()

        transition = env.step(Action.SHOOT | Action.RIGHT)

        self.assertEqual(transition.next_observation.snapshot.score, 25)
        self.assertFalse(transition.terminated)
        self.assertEqual(session.inputs, [Action.SHOOT | Action.RIGHT, Action.NONE])
        self.assertEqual(env.frames, 1)

    def test_step_times_out_when_the_next_stage_never_finishes_loading(self) -> None:
        session = FakeSession(
            snapshot_gaps=tuple(range(2, 100)),
            frame_values=(100, 101, 101, 0),
        )
        env = Th10Env(session=session, transition_timeout_s=0.01)
        env.reset()

        with self.assertRaisesRegex(NotInStage, "next stage did not finish loading"):
            env.step(Action.SHOOT)

        self.assertEqual(session.inputs[-1], Action.NONE)

    def test_step_refuses_a_menu_reached_during_a_loading_gap(self) -> None:
        session = FakeSession(
            scenes=(Scene.STAGE, Scene.MENU),
            snapshot_gaps=(2,),
            frame_values=(100, 101, 101, 0),
        )
        env = Th10Env(session=session)
        env.reset()

        with self.assertRaisesRegex(NotInStage, "entered the menus"):
            env.step(Action.SHOOT)

        self.assertEqual(session.inputs[-1], Action.NONE)

    def test_step_ends_the_run_when_the_clock_stops_for_an_ending(self) -> None:
        # A finished run freezes the stage clock, so the wait has to hand the
        # step back and let it read the ending instead of timing out on it.
        session = FakeSession(
            snapshots=(make_snapshot(score=5), make_snapshot(score=5, lives=-1, game_over=True)),
            freeze_after=2,
        )
        env = Th10Env(session=session, frame_timeout_s=0.02)
        env.reset()

        transition = env.step(Action.NONE)

        self.assertTrue(transition.terminated)
        self.assertEqual(transition.frames, 0)

        with self.assertRaises(NotInStage):
            env.step(Action.NONE)


class CloseTests(unittest.TestCase):
    def test_close_closes_the_session(self) -> None:
        session = FakeSession()

        Th10Env(session=session).close()

        self.assertTrue(session.closed)

    def test_the_context_manager_closes_the_session(self) -> None:
        session = FakeSession()

        with Th10Env(session=session):
            pass

        self.assertTrue(session.closed)


if __name__ == "__main__":
    unittest.main()
