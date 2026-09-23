import unittest

from auto_th10 import (
    EVAL_PRESET,
    TRAIN_PRESET,
    Action,
    NotInStage,
    OnDeath,
    Scene,
    Settings,
    Th10Env,
)
from fakes import FakeSession, make_snapshot


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


class ResetTests(unittest.TestCase):
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
        session = FakeSession(
            snapshots=(make_snapshot(lives=-1, game_over=True),),
            record_broken=True,
        )

        with self.assertRaises(NotInStage):
            Th10Env(settings=TRAIN_PRESET, session=session, require_stage=False).reset()

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

    def test_a_record_flag_read_failure_stops_before_confirming_the_ending(self) -> None:
        session = FakeSession(
            snapshots=(make_snapshot(lives=-1, game_over=True),),
            record_broken_error=OSError("record flag could not be read"),
        )

        with self.assertRaisesRegex(OSError, "record flag could not be read"):
            Th10Env(settings=TRAIN_PRESET, session=session, require_stage=False).reset()

        self.assertEqual(session.inputs, [])

    def test_a_record_flag_read_failure_releases_the_previous_episode_input(self) -> None:
        session = FakeSession(
            snapshots=(make_snapshot(), make_snapshot(lives=-1, game_over=True)),
            record_broken_error=OSError("record flag could not be read"),
        )
        env = Th10Env(settings=TRAIN_PRESET, session=session, require_stage=False)
        env.reset()
        env.step(Action.RIGHT | Action.SHOOT)

        with self.assertRaisesRegex(OSError, "record flag could not be read"):
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
