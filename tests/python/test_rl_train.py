import contextlib
import io
import itertools
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
    def test_defaults_run_forever_with_a_fixed_horizon_dagger_rollout(self) -> None:
        args = train.build_parser().parse_args([])

        self.assertIsNone(args.iterations)
        self.assertEqual(args.horizon, 1024)
        self.assertEqual(args.beta, 1.0)
        self.assertEqual(args.beta_min, 0.05)
        self.assertEqual(args.checkpoint, train.DEFAULT_CHECKPOINT)

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
        iteration = SimpleNamespace(
            rollout=SimpleNamespace(terminated=False, truncated=False),
            next_features="features",
        )

        with (
            mock.patch.object(train, "MemoryGymEnv", return_value=env),
            mock.patch.object(train, "run_dagger_iteration", return_value=iteration) as run,
        ):
            code = train.main(["--iterations", "2"])

        self.assertEqual(code, 0)
        self.assertEqual(run.call_count, 2)
        self.assertTrue(env.closed)

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
