import contextlib
import io
import itertools
import json
from types import SimpleNamespace
import unittest
from unittest import mock

from training import train


class _FakeEnv:
    """Stand-in for MemoryGymEnv: no game, but the same reset/close surface."""

    def __init__(self) -> None:
        self.closed = False

    def reset(self, *, seed: int | None = None) -> tuple[str, dict[str, object]]:
        del seed
        return "features", {}

    def close(self) -> None:
        self.closed = True


class TrainEntryPointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.writer = mock.Mock()
        patcher = mock.patch.object(
            train, "SummaryWriter", return_value=self.writer
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_defaults_run_forever_with_a_fixed_horizon_dagger_rollout(self) -> None:
        args = train.build_parser().parse_args([])

        self.assertIsNone(args.iterations)
        self.assertEqual(args.horizon, 1024)
        self.assertEqual(args.beta, 1.0)
        self.assertEqual(args.beta_min, 0.05)
        self.assertEqual(args.checkpoint, train.DEFAULT_CHECKPOINT)
        self.assertEqual(args.tensorboard_dir, train.DEFAULT_TENSORBOARD_ROOT)

    def test_iterations_counts_from_one_and_inf_has_no_last(self) -> None:
        self.assertEqual(list(train._iteration_range(3)), [1, 2, 3])
        self.assertEqual(list(itertools.islice(train._iteration_range(None), 3)), [1, 2, 3])

    def test_inf_spells_an_unbounded_run(self) -> None:
        for spelling in ("inf", "INF"):
            with self.subTest(spelling=spelling):
                args = train.build_parser().parse_args(["--iterations", spelling])

                self.assertIsNone(args.iterations)

    def test_iterations_rejects_a_count_that_is_not_positive(self) -> None:
        for value in ("0", "-1", "abc"):
            with (
                self.subTest(value=value),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as refused,
            ):
                train.build_parser().parse_args(["--iterations", value])

            self.assertEqual(refused.exception.code, 2)

    def test_a_finite_run_stops_after_its_iterations(self) -> None:
        env = _FakeEnv()
        stdout = io.StringIO()
        iteration = SimpleNamespace(
            rollout=SimpleNamespace(
                samples=(),
                terminated=False,
                truncated=False,
                boundary_reward=0.0,
                boundary_frames=0,
                episode_frames=0,
            ),
            next_features="features",
        )

        with (
            mock.patch.object(train, "MemoryGymEnv", return_value=env),
            mock.patch.object(train, "run_dagger_iteration", return_value=iteration) as run,
            contextlib.redirect_stdout(stdout),
        ):
            code = train.main(["--iterations", "2"])

        self.assertEqual(code, 0)
        self.assertEqual(run.call_count, 2)
        self.assertTrue(env.closed)
        self.assertIn("Input backend: background", stdout.getvalue())
        self.writer.close.assert_called_once_with()

    def test_a_late_terminal_boundary_is_logged_after_resume(self) -> None:
        env = _FakeEnv()
        env.raw_observation = SimpleNamespace(snapshot=SimpleNamespace(score=123))
        sample = SimpleNamespace(
            reward=0.0,
            frames=0,
            episode_frames=10,
            executed_action=SimpleNamespace(bomb=False),
            learner_action=SimpleNamespace(movement=0),
            teacher_action=SimpleNamespace(movement=0, bomb=False),
            used_teacher=False,
        )
        preliminary = SimpleNamespace(
            samples=(sample,),
            terminated=False,
            truncated=False,
            boundary_reward=0.0,
            boundary_frames=0,
            episode_frames=10,
        )
        terminal = SimpleNamespace(
            samples=(sample,),
            terminated=True,
            truncated=False,
            boundary_reward=-2.0,
            boundary_frames=1,
            episode_frames=11,
        )
        iteration = SimpleNamespace(rollout=terminal, next_features="terminal")

        def run_iteration(*args: object, **kwargs: object) -> object:
            del args
            kwargs["after_updates"](preliminary, ())
            return iteration

        with (
            mock.patch.object(train, "MemoryGymEnv", return_value=env),
            mock.patch.object(train, "run_dagger_iteration", side_effect=run_iteration),
            mock.patch.object(train, "save_checkpoint"),
        ):
            code = train.main(["--iterations", "1"])

        self.assertEqual(code, 0)
        self.writer.add_scalar.assert_has_calls(
            (
                mock.call("rollout/reward", -2.0, 1),
                mock.call("rollout/frames", 1, 1),
                mock.call("rollout/terminated", 1, 1),
                mock.call("episode/reward", -2.0, 0),
                mock.call("episode/survival_frames", 11, 0),
                mock.call("episode/score", 123, 0),
            ),
            any_order=True,
        )

    def test_hyperparameters_name_the_fixed_input_backend(self) -> None:
        args = train.build_parser().parse_args([])

        train._write_hyperparameters(self.writer, args)

        text = self.writer.add_text.call_args.args[1]
        values = json.loads(
            text.removeprefix("```json\n").removesuffix("\n```")
        )
        self.assertEqual(values["input_backend"], "background")

    def test_iteration_metrics_are_grouped_for_tensorboard(self) -> None:
        updates = (
            SimpleNamespace(total=4.0, movement=3.0, bomb=1.0),
            SimpleNamespace(total=2.0, movement=1.0, bomb=1.0),
        )
        samples = (
            SimpleNamespace(
                reward=1.5,
                frames=2,
                learner_action=SimpleNamespace(movement=3),
                teacher_action=SimpleNamespace(movement=3, bomb=True),
                used_teacher=True,
            ),
            SimpleNamespace(
                reward=-0.5,
                frames=1,
                learner_action=SimpleNamespace(movement=2),
                teacher_action=SimpleNamespace(movement=4, bomb=False),
                used_teacher=False,
            ),
        )
        rollout = SimpleNamespace(
            samples=samples,
            terminated=False,
            truncated=False,
            boundary_reward=0.0,
            boundary_frames=0,
            episode_frames=3,
        )

        train._log_update_metrics(
            self.writer,
            iteration=7,
            beta=0.25,
            buffer_size=50,
            updates=updates,
        )
        train._log_rollout_metrics(self.writer, rollout, iteration=7)

        self.writer.add_scalar.assert_has_calls(
            (
                mock.call("loss/total", 3.0, 7),
                mock.call("loss/movement", 2.0, 7),
                mock.call("loss/bomb", 1.0, 7),
                mock.call("rollout/reward", 1.0, 7),
                mock.call("rollout/steps", 2, 7),
                mock.call("rollout/frames", 3, 7),
                mock.call("rollout/movement_agreement", 0.5, 7),
                mock.call("rollout/bomb_label_rate", 0.5, 7),
                mock.call("rollout/teacher_execution_rate", 0.5, 7),
            ),
            any_order=True,
        )

    def test_episode_frames_use_the_environment_total_across_rollouts(self) -> None:
        first = SimpleNamespace(
            samples=(
                SimpleNamespace(
                    reward=1.0,
                    episode_frames=100,
                    executed_action=SimpleNamespace(bomb=False),
                ),
            ),
            boundary_reward=0.0,
            episode_frames=100,
        )
        second = SimpleNamespace(
            samples=(
                SimpleNamespace(
                    reward=2.0,
                    episode_frames=205,
                    executed_action=SimpleNamespace(bomb=True),
                ),
            ),
            boundary_reward=-2.0,
            episode_frames=205,
        )

        reward, frames, bombs = train._accumulate_episode(
            first, reward=0.0, frames=0, bombs=0
        )
        reward, frames, bombs = train._accumulate_episode(
            second, reward=reward, frames=frames, bombs=bombs
        )

        self.assertEqual(reward, 1.0)
        self.assertEqual(frames, 205)
        self.assertEqual(bombs, 1)

    def test_an_interrupted_run_reports_the_iteration_it_stopped_in(self) -> None:
        env = _FakeEnv()
        stderr = io.StringIO()

        with (
            mock.patch.object(train, "MemoryGymEnv", return_value=env),
            mock.patch.object(train, "run_dagger_iteration", side_effect=KeyboardInterrupt),
            contextlib.redirect_stderr(stderr),
        ):
            code = train.main([])

        self.assertEqual(code, 130)
        self.assertIn("training interrupted at iteration 1", stderr.getvalue())
        self.assertTrue(env.closed)

    def test_an_interrupt_while_attaching_still_stops_cleanly(self) -> None:
        stderr = io.StringIO()

        with (
            mock.patch.object(train, "MemoryGymEnv", side_effect=KeyboardInterrupt),
            contextlib.redirect_stderr(stderr),
        ):
            code = train.main([])

        self.assertEqual(code, 130)
        self.assertIn("training interrupted at iteration 0", stderr.getvalue())

    def test_an_interrupt_before_the_first_iteration_closes_the_environment(self) -> None:
        env = mock.Mock()
        env.reset.side_effect = KeyboardInterrupt
        stderr = io.StringIO()

        with (
            mock.patch.object(train, "MemoryGymEnv", return_value=env),
            contextlib.redirect_stderr(stderr),
        ):
            code = train.main([])

        self.assertEqual(code, 130)
        self.assertIn("training interrupted at iteration 0", stderr.getvalue())
        env.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
