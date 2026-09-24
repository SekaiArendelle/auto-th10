import unittest
from unittest import mock

from auto_th10 import (
    EVAL_PRESET,
    TRAIN_PRESET,
    Action,
    NotInStage,
    OnDeath,
    Scene,
    Screen,
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


class StartableTests(unittest.TestCase):
    """What the constructor accepts: the cheap half of the question.

    The constructor reads the screen family and nothing else - one read of one
    word - so it refuses a menu or an unknown family and accepts a stage. Whether
    that stage has a run behind it, and whether the run is frozen, is reset()'s
    business; ResetTests covers that.
    """

    def test_a_playing_stage_is_accepted(self) -> None:
        session = FakeSession()

        env = Th10Env(session=session)

        self.assertIs(env.session, session)

    def test_a_menu_is_refused(self) -> None:
        with self.assertRaises(NotInStage):
            Th10Env(session=FakeSession(scenes=(Scene.MENU,)))

    def test_an_unknown_family_is_refused(self) -> None:
        with self.assertRaises(NotInStage):
            Th10Env(session=FakeSession(scenes=(Scene.UNKNOWN,)))

    def test_a_frozen_stage_gets_past_the_constructor(self) -> None:
        # The family is all it asks, so a paused stage is accepted here and refused
        # by reset(), which is where keys would start arriving.
        env = Th10Env(session=FakeSession(frame_step=0))

        self.assertIsInstance(env, Th10Env)

    def test_the_check_can_be_skipped_when_a_test_drives_the_environment(self) -> None:
        env = Th10Env(session=FakeSession(scenes=(Scene.MENU,)), require_stage=False)

        self.assertIsInstance(env, Th10Env)


class SettingsTests(unittest.TestCase):
    def test_one_episode_leaves_the_ending_on_screen(self) -> None:
        self.assertIs(Settings.for_episodes(1).on_death, OnDeath.STOP)

    def test_more_than_one_episode_restarts_between_them(self) -> None:
        self.assertIs(Settings.for_episodes(2).on_death, OnDeath.RESTART)

    def test_the_presets_say_what_they_are_documented_to_say(self) -> None:
        self.assertIs(EVAL_PRESET.on_death, OnDeath.STOP)
        self.assertIs(TRAIN_PRESET.on_death, OnDeath.RESTART)


class ResetTests(NoWaiting, unittest.TestCase):
    """reset() drives the lifecycle, so the constructor check is kept out of the way."""

    def test_reset_focuses_the_window_and_returns_the_first_observation(self) -> None:
        session = FakeSession(snapshots=(make_snapshot(score=100),))

        observation, info = Th10Env(session=session, require_stage=False).reset()

        self.assertEqual(observation.snapshot.score, 100)
        self.assertEqual(info, {})
        self.assertEqual(session.focus_calls, 1)

    def test_reset_clears_an_ending_that_was_already_on_screen(self) -> None:
        session = FakeSession(
            snapshots=(make_snapshot(lives=-1, game_over=True), make_snapshot(score=3)),
        )

        observation, _ = Th10Env(settings=EVAL_PRESET, session=session, require_stage=False).reset()

        self.assertEqual(observation.snapshot.score, 3)
        self.assertEqual(session.inputs[-2:], [Action.SHOOT, Action.NONE])

    def test_reset_refuses_an_ending_this_environment_produced(self) -> None:
        session = FakeSession(
            snapshots=(make_snapshot(), make_snapshot(lives=-1, game_over=True)),
        )
        env = Th10Env(settings=EVAL_PRESET, session=session, require_stage=False)
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
        env = Th10Env(settings=TRAIN_PRESET, session=session, require_stage=False)
        env.reset()
        env.step(Action.NONE)

        observation, _ = env.reset()

        self.assertEqual(observation.snapshot.score, 8)

    def test_reset_refuses_the_name_entry_when_on_name_entry_is_stop(self) -> None:
        # The ranking asks for a name when a run reaches it, which is a lower bar
        # than a broken high score - the flag stays clear here - so the screen is
        # what the lifecycle reads. Nothing is sent into the grid.
        session = FakeSession(
            snapshots=(make_snapshot(lives=-1, game_over=True),),
            ui_screen=Screen.NAME_ENTRY,
            ui_cursor=0,
        )

        with self.assertRaisesRegex(NotInStage, "waiting for a name"):
            Th10Env(settings=EVAL_PRESET, session=session, require_stage=False).reset()

        self.assertEqual(session.inputs, [])

    def test_reset_walks_the_name_entry_out_when_the_settings_say_so(self) -> None:
        # The collecting preset answers the ranking instead of stopping on it: the
        # grid is walked onto 終, the record is written with the game's own default
        # name, and the next run starts from the menu the confirm leaves behind.
        session = FakeSession(
            snapshots=(make_snapshot(lives=-1, game_over=True), make_snapshot(score=8)),
            ui_screen=Screen.NAME_ENTRY,
            ui_cursor=0,
        )

        observation, _ = Th10Env(
            settings=TRAIN_PRESET, session=session, require_stage=False
        ).reset()

        self.assertEqual(observation.snapshot.score, 8)
        self.assertIs(session.ui_screen, Screen.STAGE)

    def test_reset_handles_a_name_entry_that_appears_during_restart(self) -> None:
        # The run's game-over flag can become visible before the ranking installs
        # its UI object. The ordinary restart sees STAGE first and tries to open
        # the ending menu; if the name entry appears around that press, reset must
        # redispatch it instead of surfacing NotAnEnding and aborting evaluation's
        # next episode.
        class LateNameEntrySession(FakeSession):
            def _apply(self, action: object) -> None:
                if self.ui_screen is Screen.STAGE and action == Action.SHOOT:
                    self.ui_screen = Screen.NAME_ENTRY
                    self.ui_cursor = 0
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
        env = Th10Env(settings=TRAIN_PRESET, session=session, require_stage=False)
        env.reset()
        env.step(Action.RIGHT | Action.SHOOT)

        observation, _ = env.reset()

        self.assertEqual(observation.snapshot.score, 8)
        self.assertIs(session.ui_screen, Screen.STAGE)
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
            Th10Env(session=BrokenSession(), require_stage=False).reset()

    def test_reset_propagates_a_closed_session(self) -> None:
        # SessionClosedError derives from RuntimeError just as GameplayNotActive
        # does, so this is the case that pins the rule down: the environment
        # swallows one named failure, not the family it belongs to.
        class ClosedSession(FakeSession):
            def snapshot(self) -> object:
                raise SessionClosedError("session is closed")

        with self.assertRaises(SessionClosedError):
            Th10Env(session=ClosedSession(), require_stage=False).reset()

    def test_reset_refuses_a_restart_that_lands_at_the_title(self) -> None:
        # The restart sequence reports a game that ended up back at the title
        # rather than pressing on through the setup screens, and the environment
        # turns that into its own refusal: a caller only ever sees NotInStage.
        session = FakeSession(
            snapshots=(make_snapshot(), make_snapshot(lives=-1, game_over=True)),
            scenes=(Scene.STAGE, Scene.STAGE, Scene.MENU),
        )
        env = Th10Env(settings=TRAIN_PRESET, session=session, require_stage=False)
        env.reset()
        env.step(Action.NONE)

        with self.assertRaisesRegex(NotInStage, "ending's own menu"):
            env.reset()

        self.assertEqual(session.inputs[-1], Action.NONE)

    def test_a_ui_read_failure_stops_before_confirming_the_ending(self) -> None:
        # The screen is what tells an ending's menu from the name entry behind it,
        # and a read that failed says neither: a confirm sent on the strength of it
        # could type into the ranking. The failure travels out instead.
        session = FakeSession(
            snapshots=(make_snapshot(lives=-1, game_over=True),),
            ui_error=OSError("the game's ui could not be read"),
        )

        with self.assertRaisesRegex(OSError, "ui could not be read"):
            Th10Env(settings=TRAIN_PRESET, session=session, require_stage=False).reset()

        self.assertEqual(session.inputs, [])

    def test_a_ui_read_failure_releases_the_previous_episode_input(self) -> None:
        session = FakeSession(
            snapshots=(make_snapshot(), make_snapshot(lives=-1, game_over=True)),
            ui_error=OSError("the game's ui could not be read"),
        )
        env = Th10Env(settings=TRAIN_PRESET, session=session, require_stage=False)
        env.reset()
        env.step(Action.RIGHT | Action.SHOOT)

        with self.assertRaisesRegex(OSError, "ui could not be read"):
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

    def test_step_holds_the_action_and_returns_the_score_delta(self) -> None:
        session = FakeSession(
            snapshots=(make_snapshot(score=10), make_snapshot(score=25)),
        )
        env = Th10Env(session=session)
        env.reset()

        observation, reward, terminated, truncated, info = env.step(Action.SHOOT | Action.FOCUS)

        self.assertEqual(observation.snapshot.score, 25)
        self.assertEqual(reward, 15.0)
        self.assertFalse(terminated)
        self.assertFalse(truncated)
        self.assertEqual(info["steps"], 1)
        self.assertEqual(session.inputs, [Action.SHOOT | Action.FOCUS])

    def test_step_reports_game_over_as_terminated(self) -> None:
        session = FakeSession(
            snapshots=(make_snapshot(score=5), make_snapshot(score=5, lives=-1, game_over=True)),
        )
        env = Th10Env(session=session)
        env.reset()

        _, _, terminated, truncated, _ = env.step(Action.NONE)

        self.assertTrue(terminated)
        self.assertFalse(truncated)

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

        observation, reward, terminated, _, _ = env.step(Action.SHOOT | Action.RIGHT)

        self.assertEqual(observation.snapshot.score, 25)
        self.assertEqual(reward, 15.0)
        self.assertFalse(terminated)
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

        _, _, terminated, truncated, _ = env.step(Action.NONE)

        self.assertTrue(terminated)
        self.assertFalse(truncated)

    def test_the_reward_can_be_shaped(self) -> None:
        session = FakeSession(
            snapshots=(make_snapshot(score=1), make_snapshot(score=2)),
        )
        env = Th10Env(session=session, reward_fn=lambda previous, current: -1.0)
        env.reset()

        _, reward, _, _, _ = env.step(Action.NONE)

        self.assertEqual(reward, -1.0)


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
