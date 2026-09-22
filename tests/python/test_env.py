import unittest

from auto_th10 import (
    EVAL_PRESET,
    TRAIN_PRESET,
    Action,
    NotInStage,
    OnDeath,
    Settings,
    State,
    Th10Env,
)
from fakes import FakeSession, make_snapshot


class StartableTests(unittest.TestCase):
    """What the constructor accepts, before reset() is involved."""

    def test_a_playing_stage_is_accepted(self) -> None:
        session = FakeSession(states=(State.PLAYING,))

        env = Th10Env(session=session)

        self.assertIs(env.session, session)

    def test_a_menu_is_refused(self) -> None:
        with self.assertRaises(NotInStage):
            Th10Env(session=FakeSession(states=(State.MENU,)))

    def test_a_paused_stage_is_refused(self) -> None:
        with self.assertRaises(NotInStage):
            Th10Env(session=FakeSession(states=(State.PAUSED,)))

    def test_an_unknown_screen_is_refused(self) -> None:
        with self.assertRaises(NotInStage):
            Th10Env(session=FakeSession(states=(State.UNKNOWN,)))

    def test_a_finished_run_is_accepted_even_when_it_may_not_be_restarted(self) -> None:
        # The ending was left there by an earlier attempt, and clearing it is one
        # key press rather than a choice, so reset() may do it under any settings.
        env = Th10Env(settings=EVAL_PRESET, session=FakeSession(states=(State.GAME_OVER,)))

        self.assertIsInstance(env, Th10Env)

    def test_the_check_can_be_skipped_when_a_test_drives_the_environment(self) -> None:
        env = Th10Env(session=FakeSession(states=(State.MENU,)), require_stage=False)

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
        session = FakeSession(states=(State.PLAYING,), snapshots=(make_snapshot(score=100),))

        observation, info = Th10Env(session=session, require_stage=False).reset()

        self.assertEqual(observation.snapshot.score, 100)
        self.assertEqual(info, {})
        self.assertEqual(session.focus_calls, 1)

    def test_reset_clears_an_ending_that_was_already_on_screen(self) -> None:
        session = FakeSession(
            states=(State.GAME_OVER, State.PLAYING),
            snapshots=(make_snapshot(lives=-1, game_over=True), make_snapshot(score=3)),
        )

        observation, _ = Th10Env(settings=EVAL_PRESET, session=session, require_stage=False).reset()

        self.assertEqual(observation.snapshot.score, 3)
        self.assertEqual(session.inputs[-2:], [Action.SHOOT, Action.NONE])

    def test_reset_refuses_an_ending_this_environment_produced(self) -> None:
        session = FakeSession(
            states=(State.PLAYING, State.GAME_OVER),
            snapshots=(make_snapshot(), make_snapshot(lives=-1, game_over=True)),
        )
        env = Th10Env(settings=EVAL_PRESET, session=session, require_stage=False)
        env.reset()
        env.step(Action.NONE)

        with self.assertRaises(NotInStage):
            env.reset()

    def test_a_second_episode_restarts_when_the_settings_say_so(self) -> None:
        session = FakeSession(
            states=(State.PLAYING, State.GAME_OVER, State.PLAYING),
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
            states=(State.GAME_OVER,),
            snapshots=(make_snapshot(lives=-1, game_over=True),),
            record_broken=True,
        )

        with self.assertRaises(NotInStage):
            Th10Env(settings=TRAIN_PRESET, session=session, require_stage=False).reset()

    def test_reset_refuses_a_menu_the_restart_landed_on(self) -> None:
        session = FakeSession(
            states=(State.GAME_OVER, State.MENU),
            snapshots=(make_snapshot(lives=-1, game_over=True), make_snapshot()),
        )

        with self.assertRaises(NotInStage):
            Th10Env(settings=TRAIN_PRESET, session=session, require_stage=False).reset()


class StepTests(unittest.TestCase):
    def test_step_holds_the_action_and_returns_the_score_delta(self) -> None:
        session = FakeSession(
            states=(State.PLAYING,),
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
            states=(State.PLAYING,),
            snapshots=(make_snapshot(score=5), make_snapshot(score=5, lives=-1, game_over=True)),
        )
        env = Th10Env(session=session)
        env.reset()

        _, _, terminated, truncated, _ = env.step(Action.NONE)

        self.assertTrue(terminated)
        self.assertFalse(truncated)

    def test_step_stops_when_the_game_stops_advancing(self) -> None:
        session = FakeSession(states=(State.PLAYING,), frame_step=0)
        env = Th10Env(session=session, frame_timeout_s=0.02)
        env.reset()

        with self.assertRaises(NotInStage):
            env.step(Action.NONE)

    def test_step_ends_the_run_when_the_clock_stops_for_an_ending(self) -> None:
        # A finished run freezes the stage clock, so the wait has to hand the
        # step back and let it read the ending instead of timing out on it.
        session = FakeSession(
            states=(State.PLAYING,),
            snapshots=(make_snapshot(score=5), make_snapshot(score=5, lives=-1, game_over=True)),
            frame_step=0,
        )
        env = Th10Env(session=session, frame_timeout_s=0.02)
        env.reset()

        _, _, terminated, truncated, _ = env.step(Action.NONE)

        self.assertTrue(terminated)
        self.assertFalse(truncated)

    def test_the_reward_can_be_shaped(self) -> None:
        session = FakeSession(
            states=(State.PLAYING,),
            snapshots=(make_snapshot(score=1), make_snapshot(score=2)),
        )
        env = Th10Env(session=session, reward_fn=lambda previous, current: -1.0)
        env.reset()

        _, reward, _, _, _ = env.step(Action.NONE)

        self.assertEqual(reward, -1.0)


class CloseTests(unittest.TestCase):
    def test_close_closes_the_session(self) -> None:
        session = FakeSession(states=(State.PLAYING,))

        Th10Env(session=session).close()

        self.assertTrue(session.closed)

    def test_the_context_manager_closes_the_session(self) -> None:
        session = FakeSession(states=(State.PLAYING,))

        with Th10Env(session=session):
            pass

        self.assertTrue(session.closed)


if __name__ == "__main__":
    unittest.main()
