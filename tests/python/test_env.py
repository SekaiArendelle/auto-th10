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
)
from auto_th10 import restart as restart_module
from auto_th10 import env as env_module
from fakes import FakeSession, make_environment, make_snapshot


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

        env = make_environment(session)

        self.assertIs(env.session, session)

    def test_a_menu_is_not_inspected_until_reset(self) -> None:
        env = make_environment(FakeSession(scenes=(Scene.MENU,)))

        with self.assertRaises(NotInStage):
            env.reset()

    def test_an_unknown_family_is_not_inspected_until_reset(self) -> None:
        env = make_environment(FakeSession(scenes=(Scene.UNKNOWN,)))

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

        observation = make_environment(session).reset()

        self.assertEqual(observation.snapshot.score, 100)
        self.assertEqual(session.focus_calls, 1)

    def test_reset_clears_an_ending_that_was_already_on_screen(self) -> None:
        session = FakeSession(
            snapshots=(make_snapshot(lives=-1, game_over=True), make_snapshot(score=3)),
        )

        observation = make_environment(session, settings=EVAL_PRESET).reset()

        self.assertEqual(observation.snapshot.score, 3)
        self.assertEqual(session.inputs[-2:], [Action.SHOOT, Action.NONE])

    def test_reset_refuses_an_ending_this_environment_produced(self) -> None:
        session = FakeSession(
            snapshots=(make_snapshot(), make_snapshot(lives=-1, game_over=True)),
        )
        env = make_environment(session, settings=EVAL_PRESET)
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
        env = make_environment(session, settings=TRAIN_PRESET)
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
            make_environment(session, settings=EVAL_PRESET).reset()

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

        observation = make_environment(session, settings=TRAIN_PRESET).reset()

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
        env = make_environment(session, settings=TRAIN_PRESET)
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
            make_environment(FakeSession(frame_step=0)).reset()

    def test_reset_leaves_a_pause_menu_the_game_was_left_on(self) -> None:
        # The pause is not ours: an update that failed with the stage paused, or
        # the operator's own ESCAPE. No run starts behind the menu, and the cursor
        # opens on Return to Game, so reset() takes the one key that leaves it.
        session = FakeSession(screen_kind=ScreenKind.PAUSE_MENU, screen_cursor=0)
        session.paused = True
        env = make_environment(session)

        env.reset()
        env.step(Action.NONE)

        self.assertIs(session.screen_kind, ScreenKind.STAGE)
        self.assertIn(Action.SHOOT, session.inputs)
        self.assertEqual(env.steps, 1)

    def test_reset_refuses_a_pause_menu_away_from_return_to_game(self) -> None:
        # Another entry on that menu is not one this layer may choose: `Retry This
        # Game` is one press away, and its confirmation would end the run.
        session = FakeSession(screen_kind=ScreenKind.PAUSE_MENU, screen_cursor=1)
        session.paused = True

        with self.assertRaisesRegex(NotInStage, "Return to Game"):
            make_environment(session).reset()

        self.assertEqual(session.inputs, [])

    def test_reset_refuses_a_pause_confirmation_without_pressing_through_it(self) -> None:
        # The Retry confirmation is a page of its own whose cursor opens on `No`,
        # and its 0 sits in the same field as Return to Game does on the menu
        # above: a driver that went by the number alone would answer `Yes`.
        session = FakeSession(screen_kind=ScreenKind.PAUSE_CONFIRM, screen_cursor=0)
        session.paused = True

        with self.assertRaises(NotInStage):
            make_environment(session).reset()

        self.assertEqual(session.inputs, [])

    def test_a_running_stage_is_not_asked_about_its_screen(self) -> None:
        # The screen is what decides between leaving a pause and refusing one, but
        # it is read only once the clock has stopped: a screen read failure must
        # not be able to break a reset that has nothing to repair.
        session = FakeSession(screen_error=OSError("the game's screen could not be read"))
        env = make_environment(session)

        env.reset()
        env.step(Action.NONE)

        self.assertEqual(env.steps, 1)

    def test_reset_refuses_a_pause_menu_behind_an_ending_without_confirming_it(self) -> None:
        # The ending's branch runs first and reaches the restart sequence, which
        # refuses both pause pages before its first press: all that leaves is the
        # release `leave_game_over()` opens with, so no confirmation can be
        # answered through an ending's sequence.
        session = FakeSession(
            snapshots=(make_snapshot(lives=-1, game_over=True),),
            screen_kind=ScreenKind.PAUSE_MENU,
            screen_cursor=0,
        )
        session.paused = True

        with self.assertRaises(NotInStage):
            make_environment(session).reset()

        self.assertEqual(session.inputs, [Action.NONE])

    def test_reset_refuses_a_stage_with_no_run_behind_it(self) -> None:
        # Between runs the family can still read as a stage while the stage object
        # is gone, which is what a snapshot that raises means.
        with self.assertRaises(NotInStage):
            make_environment(FakeSession(no_stage_for=99)).reset()

    def test_reset_propagates_an_unexpected_snapshot_runtime_error(self) -> None:
        class BrokenSession(FakeSession):
            def snapshot(self) -> object:
                raise RuntimeError("unexpected snapshot failure")

        with self.assertRaisesRegex(RuntimeError, "unexpected snapshot failure"):
            make_environment(BrokenSession()).reset()

    def test_reset_propagates_a_closed_session(self) -> None:
        # SessionClosedError derives from RuntimeError just as GameplayNotActive
        # does, so this is the case that pins the rule down: the environment
        # swallows one named failure, not the family it belongs to.
        class ClosedSession(FakeSession):
            def snapshot(self) -> object:
                raise SessionClosedError("session is closed")

        with self.assertRaises(SessionClosedError):
            make_environment(ClosedSession()).reset()

    def test_reset_refuses_a_restart_that_lands_at_the_title(self) -> None:
        # The restart sequence reports a game that ended up back at the title
        # rather than pressing on through the setup screens, and the environment
        # turns that into its own refusal: a caller only ever sees NotInStage.
        session = FakeSession(
            snapshots=(make_snapshot(), make_snapshot(lives=-1, game_over=True)),
            scenes=(Scene.STAGE, Scene.STAGE, Scene.MENU),
        )
        env = make_environment(session, settings=TRAIN_PRESET)
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
            make_environment(session, settings=TRAIN_PRESET).reset()

        self.assertEqual(session.inputs, [])

    def test_a_screen_read_failure_releases_the_previous_episode_input(self) -> None:
        session = FakeSession(
            snapshots=(make_snapshot(), make_snapshot(lives=-1, game_over=True)),
            screen_error=OSError("the game's screen could not be read"),
        )
        env = make_environment(session, settings=TRAIN_PRESET)
        env.reset()
        env.step(Action.RIGHT | Action.SHOOT)

        with self.assertRaisesRegex(OSError, "screen could not be read"):
            env.reset()

        self.assertEqual(session.inputs[-1], Action.NONE)

    def test_an_interrupted_restart_releases_the_key_it_was_holding(self) -> None:
        # An ending on screen is what takes the restart sequence, and that sequence
        # holds keys down. An interrupt inside one of those holds - a Ctrl-C in the
        # sleep between a press and its release - must not leave the game holding a
        # direction, and no layer above a failed reset calls stop() to repair it.
        session = FakeSession(snapshots=(make_snapshot(lives=-1, game_over=True),))
        env = make_environment(session)

        with mock.patch.object(restart_module.time, "sleep", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                env.reset()

        self.assertEqual(session.inputs[-1], Action.NONE)


class StepTests(unittest.TestCase):
    def test_step_before_reset_is_refused_without_touching_the_keyboard(self) -> None:
        # A frozen stage gets past the constructor, because the family is all the
        # constructor asks. step() must not take that as licence to inject: it
        # insists on reset(), which is where the run is actually checked.
        session = FakeSession(frame_step=0)
        env = make_environment(session)

        with self.assertRaises(NotInStage):
            env.step(Action.NONE)
        self.assertEqual(session.inputs, [])

    def test_a_failed_step_requires_another_reset(self) -> None:
        session = FakeSession(freeze_after=2)
        env = make_environment(session, frame_timeout_s=0.02)
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
        env = make_environment(session)
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

        env = make_environment(BrokenInputSession())
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
        env = make_environment(session)
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
        env = make_environment(session)
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
        env = make_environment(session)
        env.reset()

        with self.assertRaisesRegex(OSError, "release failed"):
            env.step(Action.SHOOT)
        with self.assertRaisesRegex(NotInStage, "call reset"):
            env.step(Action.SHOOT)

    def test_step_stops_when_the_game_stops_advancing(self) -> None:
        # Two reads of the clock while it is moving get the episode started; after
        # that it freezes, which is what a pause in the middle looks like.
        session = FakeSession(freeze_after=2)
        env = make_environment(session, frame_timeout_s=0.02)
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
        env = make_environment(session)
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
        env = make_environment(session, transition_timeout_s=0.01)
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
        env = make_environment(session)
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
        env = make_environment(session, frame_timeout_s=0.02)
        env.reset()

        transition = env.step(Action.NONE)

        self.assertTrue(transition.terminated)
        self.assertEqual(transition.frames, 0)

        with self.assertRaises(NotInStage):
            env.step(Action.NONE)


class PauseTests(NoWaiting, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch.object(env_module, "PAUSE_SAMPLE_SECONDS", 0.0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_pause_releases_input_and_confirms_the_frozen_observation(self) -> None:
        session = FakeSession(
            snapshots=(make_snapshot(score=10), make_snapshot(score=25)),
        )
        env = make_environment(session)
        env.reset()
        env.step(Action.RIGHT | Action.SHOOT)

        observation = env.pause()

        self.assertEqual(observation.snapshot.score, 25)
        self.assertEqual(
            session.inputs[-3:], [Action.NONE, Action.ESCAPE, Action.NONE]
        )
        self.assertTrue(session.paused)
        with self.assertRaisesRegex(NotInStage, "call reset"):
            env.step(Action.NONE)

    def test_a_run_ending_while_pause_opens_is_left_on_its_ending(self) -> None:
        session = FakeSession(
            snapshots=(
                make_snapshot(lives=0),
                make_snapshot(lives=-1, game_over=True),
            )
        )
        env = make_environment(session)
        env.reset()

        observation = env.pause()

        self.assertTrue(observation.snapshot.game_over)
        self.assertFalse(session.paused)
        self.assertEqual(
            session.inputs[-5:],
            [Action.NONE, Action.ESCAPE, Action.NONE, Action.SHOOT, Action.NONE],
        )
        with self.assertRaisesRegex(NotInStage, "call reset"):
            env.step(Action.NONE)

    def test_a_run_ending_before_pause_opens_is_a_terminal_boundary(self) -> None:
        session = FakeSession(
            snapshots=(
                make_snapshot(lives=0),
                make_snapshot(lives=-1, game_over=True),
            ),
            accept_pause=False,
        )
        env = make_environment(session)
        env.reset()

        observation = env.pause()

        self.assertTrue(observation.snapshot.game_over)
        self.assertFalse(session.paused)
        self.assertEqual(
            session.inputs[-3:], [Action.NONE, Action.ESCAPE, Action.NONE]
        )
        with self.assertRaisesRegex(NotInStage, "call reset"):
            env.step(Action.NONE)

    def test_an_unreadable_snapshot_while_pause_opens_is_still_refused(self) -> None:
        session = FakeSession(snapshot_gaps=(2,))
        env = make_environment(session)
        env.reset()

        with self.assertRaisesRegex(NotInStage, "became unavailable"):
            env.pause()

        self.assertTrue(session.paused)
        with self.assertRaisesRegex(NotInStage, "call reset"):
            env.step(Action.NONE)

    def test_pause_interrupts_when_escape_does_not_freeze_the_stage(self) -> None:
        session = FakeSession(accept_pause=False)
        env = make_environment(session, frame_timeout_s=0.01)
        env.reset()

        with self.assertRaisesRegex(NotInStage, "did not pause"):
            env.pause()

        self.assertEqual(session.inputs[-1], Action.NONE)
        with self.assertRaisesRegex(NotInStage, "call reset"):
            env.step(Action.NONE)

    def test_pause_refuses_a_menu_that_opens_on_an_unsafe_entry(self) -> None:
        class MovedPauseCursorSession(FakeSession):
            def _apply(self, action: object) -> None:
                super()._apply(action)
                if action == Action.ESCAPE and self.paused:
                    self.screen_cursor = 1

        session = MovedPauseCursorSession()
        env = make_environment(session)
        env.reset()

        with self.assertRaisesRegex(NotInStage, "away from Return to Game"):
            env.pause()

        self.assertEqual(session.inputs[-1], Action.NONE)
        with self.assertRaisesRegex(NotInStage, "call reset"):
            env.step(Action.NONE)

    def test_pause_refuses_the_retry_confirmation(self) -> None:
        class ConfirmingPauseSession(FakeSession):
            """A pause whose menu is wearing `Retry This Game`'s confirmation."""

            def _apply(self, action: object) -> None:
                super()._apply(action)
                if action == Action.ESCAPE and self.paused:
                    self.screen_kind = ScreenKind.PAUSE_CONFIRM
                    self.screen_cursor = 1  # `No`, where that confirmation opens

        session = ConfirmingPauseSession()
        env = make_environment(session)
        env.reset()

        with self.assertRaisesRegex(NotInStage, "Retry confirmation"):
            env.pause()

        self.assertEqual(session.inputs[-1], Action.NONE)
        with self.assertRaisesRegex(NotInStage, "call reset"):
            env.step(Action.NONE)

    def test_resume_checks_the_cursor_before_sending_z(self) -> None:
        session = FakeSession()
        env = make_environment(session)
        env.reset()
        env.pause()
        session.screen_cursor = 1
        inputs_before_resume = list(session.inputs)

        with self.assertRaisesRegex(NotInStage, "Return to Game"):
            env.resume()

        self.assertEqual(session.inputs, inputs_before_resume)
        self.assertTrue(session.paused)

    def test_resume_refuses_the_retry_confirmation_at_its_own_zero(self) -> None:
        # The confirmation keeps its cursor in the pause menu's field, so a check
        # on that number alone would read its `Yes` as `Return to Game`.
        session = FakeSession()
        env = make_environment(session)
        env.reset()
        env.pause()
        session.screen_kind = ScreenKind.PAUSE_CONFIRM
        session.screen_cursor = 0
        inputs_before_resume = list(session.inputs)

        with self.assertRaisesRegex(NotInStage, "Return to Game"):
            env.resume()

        self.assertEqual(session.inputs, inputs_before_resume)
        self.assertTrue(session.paused)

    def test_resume_refuses_a_title_menu_without_sending_z(self) -> None:
        session = FakeSession(scenes=(Scene.STAGE, Scene.MENU))
        env = make_environment(session)
        env.reset()
        env.pause()
        inputs_before_resume = list(session.inputs)

        with self.assertRaisesRegex(NotInStage, "left the paused stage"):
            env.resume()

        self.assertEqual(session.inputs, inputs_before_resume)

    def test_resume_refuses_a_game_over_menu_without_sending_z(self) -> None:
        session = FakeSession()
        env = make_environment(session)
        env.reset()
        env.pause()
        session._snapshots = [make_snapshot(game_over=True)]
        inputs_before_resume = list(session.inputs)

        with self.assertRaisesRegex(NotInStage, "no longer live"):
            env.resume()

        self.assertEqual(session.inputs, inputs_before_resume)

    def test_resume_refuses_a_pause_whose_saved_clock_has_moved(self) -> None:
        session = FakeSession()
        env = make_environment(session)
        env.reset()
        env.pause()
        session._frame_value += 1
        inputs_before_resume = list(session.inputs)

        with self.assertRaisesRegex(NotInStage, "clock moved"):
            env.resume()

        self.assertEqual(session.inputs, inputs_before_resume)

    def test_reset_during_pause_is_rejected_without_losing_the_pause(self) -> None:
        session = FakeSession()
        env = make_environment(session)
        env.reset()
        env.pause()

        with self.assertRaisesRegex(NotInStage, "call resume"):
            env.reset()

        observation = env.resume()
        self.assertFalse(session.paused)
        self.assertEqual(env.step(Action.NONE).observation, observation)

    def test_resume_confirms_progress_and_returns_the_live_observation(self) -> None:
        session = FakeSession(
            snapshots=(make_snapshot(score=10), make_snapshot(score=20)),
        )
        env = make_environment(session)
        env.reset()
        env.pause()

        observation = env.resume()

        self.assertEqual(observation.snapshot.score, 20)
        self.assertEqual(session.inputs[-2:], [Action.SHOOT, Action.NONE])
        self.assertFalse(session.paused)
        transition = env.step(Action.RIGHT)
        self.assertEqual(transition.observation, observation)

    def test_resume_interrupts_when_z_does_not_restart_the_clock(self) -> None:
        session = FakeSession(accept_resume=False)
        env = make_environment(session, frame_timeout_s=0.01)
        env.reset()
        env.pause()

        with self.assertRaisesRegex(NotInStage, "did not resume"):
            env.resume()

        self.assertEqual(session.inputs[-1], Action.NONE)
        with self.assertRaisesRegex(NotInStage, "call reset"):
            env.step(Action.NONE)


class CloseTests(NoWaiting, unittest.TestCase):
    """The session is this environment's to close: its factory built it."""

    def test_close_closes_the_session_its_factory_built(self) -> None:
        session = FakeSession()

        make_environment(session).close()

        self.assertTrue(session.closed)

    def test_close_releases_input_the_environment_injected(self) -> None:
        session = FakeSession()
        env = make_environment(session)
        env.reset()
        env.step(Action.RIGHT)

        env.close()

        self.assertEqual(session.inputs[-1], Action.NONE)
        self.assertTrue(session.closed)

    def test_the_context_manager_closes_the_session_its_factory_built(self) -> None:
        session = FakeSession()

        with make_environment(session):
            pass

        self.assertTrue(session.closed)


if __name__ == "__main__":
    unittest.main()
