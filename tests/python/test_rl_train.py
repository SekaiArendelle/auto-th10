import contextlib
import io
import itertools
import json
import pathlib
import random
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import torch

from auto_th10 import NotInStage
from training import train
from training.rl import (
    ActionSpec,
    ActorCritic,
    CheckpointError,
    DaggerBuffer,
    FeatureSpec,
    ModelSpec,
    load_checkpoint,
    save_checkpoint,
)


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

    def test_defaults_run_finite_dagger_then_unbounded_ppo(self) -> None:
        args = train.build_parser().parse_args([])

        self.assertEqual(args.dagger_iterations, 100)
        self.assertIsNone(args.ppo_iterations)
        self.assertEqual(args.horizon, 1024)
        self.assertEqual(args.beta, 1.0)
        self.assertEqual(args.beta_min, 0.05)
        self.assertEqual(args.checkpoint_dir, train.DEFAULT_CHECKPOINT_DIR)
        self.assertEqual(args.checkpoint_every, 25)
        self.assertEqual(args.tensorboard_dir, train.DEFAULT_TENSORBOARD_ROOT)
        self.assertFalse(args.resume)

    def test_iterations_counts_from_one_and_inf_has_no_last(self) -> None:
        self.assertEqual(list(train._iteration_range(3)), [1, 2, 3])
        self.assertEqual(list(itertools.islice(train._iteration_range(None), 3)), [1, 2, 3])

    def test_inf_spells_an_unbounded_run(self) -> None:
        for spelling in ("inf", "INF"):
            with self.subTest(spelling=spelling):
                args = train.build_parser().parse_args(["--ppo-iterations", spelling])

                self.assertIsNone(args.ppo_iterations)

    def test_iterations_rejects_a_count_that_is_not_positive(self) -> None:
        for value in ("0", "-1", "abc"):
            with (
                self.subTest(value=value),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as refused,
            ):
                train.build_parser().parse_args(["--dagger-iterations", value])

            self.assertEqual(refused.exception.code, 2)

    def test_checkpoint_snapshots_cover_intervals_and_phase_boundaries(self) -> None:
        args = train.build_parser().parse_args(
            ["--checkpoint-dir", "runs/example", "--checkpoint-every", "25"]
        )

        ordinary = train._snapshot_path(
            args,
            iteration=24,
            dagger_completed=24,
            ppo_completed=0,
            phase_transition=False,
            training_complete=False,
        )
        periodic = train._snapshot_path(
            args,
            iteration=25,
            dagger_completed=25,
            ppo_completed=0,
            phase_transition=False,
            training_complete=False,
        )
        transition = train._snapshot_path(
            args,
            iteration=100,
            dagger_completed=100,
            ppo_completed=0,
            phase_transition=True,
            training_complete=False,
        )

        self.assertIsNone(ordinary)
        self.assertEqual(
            periodic,
            pathlib.Path("runs/example")
            / "iteration-00000025-dagger-00000025-ppo-00000000.pt",
        )
        self.assertEqual(
            transition,
            pathlib.Path("runs/example")
            / "iteration-00000100-dagger-00000100-ppo-00000000.pt",
        )

    def test_checkpoint_publish_keeps_latest_and_immutable_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            pending = root / ".latest.pt.pending"
            latest = root / "latest.pt"
            snapshot = root / "iteration-00000025-dagger-00000025-ppo-00000000.pt"
            pending.write_bytes(b"checkpoint")

            train._stage_checkpoint_snapshot(pending, snapshot)
            train._publish_checkpoint(pending, latest, snapshot=snapshot)

            self.assertEqual(latest.read_bytes(), b"checkpoint")
            self.assertEqual(snapshot.read_bytes(), b"checkpoint")
            self.assertFalse(pending.exists())
            with self.assertRaisesRegex(FileExistsError, "already exists"):
                train._stage_checkpoint_snapshot(latest, snapshot)

    def test_latest_survives_snapshot_publish_failure_and_recovers_next_time(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            pending = root / ".latest.pt.pending"
            latest = root / "latest.pt"
            snapshot = root / "iteration-00000025-dagger-00000025-ppo-00000000.pt"
            pending.write_bytes(b"checkpoint")
            train._stage_checkpoint_snapshot(pending, snapshot)

            with (
                mock.patch.object(train.os, "rename", side_effect=OSError("failed")),
                self.assertRaisesRegex(OSError, "failed"),
            ):
                train._publish_checkpoint(pending, latest, snapshot=snapshot)

            self.assertEqual(latest.read_bytes(), b"checkpoint")
            self.assertFalse(snapshot.exists())
            train._recover_staged_snapshot(
                root,
                iteration=25,
                dagger_completed=25,
                ppo_completed=0,
            )
            self.assertEqual(snapshot.read_bytes(), b"checkpoint")

    def test_resume_accepts_a_name_inside_the_directory_or_an_external_path(self) -> None:
        root = pathlib.Path("runs/example")

        self.assertEqual(
            train._resume_checkpoint_path(root, pathlib.Path("latest.pt")),
            root / "latest.pt",
        )
        external = pathlib.Path("runs/other/iteration-00000025.pt")
        self.assertEqual(
            train._resume_checkpoint_path(root, external), external
        )

    def test_fresh_training_refuses_to_overwrite_a_checkpoint_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "latest.pt").write_bytes(b"existing")

            with self.assertRaisesRegex(ValueError, "already contains"):
                train._require_empty_checkpoint_dir(root)

    def test_historical_checkpoint_cannot_resume_into_its_own_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            snapshot = root / "iteration-00000025-dagger-00000025-ppo-00000000.pt"
            snapshot.write_bytes(b"checkpoint")
            stderr = io.StringIO()

            with contextlib.redirect_stderr(stderr):
                code = train.main(
                    [
                        "--resume",
                        str(snapshot),
                        "--checkpoint-dir",
                        str(root),
                    ]
                )

        self.assertEqual(code, 1)
        self.assertIn("must resume into a new --checkpoint-dir", stderr.getvalue())

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
                resume_reward=0.0,
                resume_frames=0,
            ),
            next_features="features",
        )

        with (
            mock.patch.object(train, "MemoryGymEnv", return_value=env),
            mock.patch.object(train, "run_dagger_iteration", return_value=iteration) as run,
            contextlib.redirect_stdout(stdout),
        ):
            code = train.main(
                ["--dagger-iterations", "2", "--ppo-iterations", "0"]
            )

        self.assertEqual(code, 0)
        self.assertEqual(run.call_count, 2)
        self.assertTrue(env.closed)
        self.assertIn("Input backend: background", stdout.getvalue())
        self.writer.close.assert_called_once_with()

    def test_training_switches_from_finite_dagger_to_ppo(self) -> None:
        env = _FakeEnv()
        env.raw_observation = SimpleNamespace(snapshot=SimpleNamespace(score=10))
        dagger = SimpleNamespace(
            rollout=SimpleNamespace(
                samples=(),
                terminated=False,
                truncated=False,
                boundary_reward=0.0,
                boundary_frames=0,
                episode_frames=0,
            ),
            next_features="after-dagger",
        )
        ppo = SimpleNamespace(
            rollout=SimpleNamespace(
                samples=(),
                terminated=False,
                boundary_reward=0.0,
                boundary_frames=0,
                episode_frames=0,
                resume_reward=0.0,
                resume_frames=0,
            ),
            next_features="after-ppo",
        )
        ppo_updates = (
            SimpleNamespace(
                total=1.0,
                policy=0.1,
                value=2.0,
                entropy=0.5,
                approximate_kl=0.01,
                clip_fraction=0.0,
            ),
        )

        def run_ppo_iteration(*args: object, **kwargs: object) -> object:
            del args
            kwargs["prepare_updates"](ppo.rollout, ppo_updates)
            kwargs["after_updates"](ppo.rollout, ppo_updates)
            return ppo

        with (
            mock.patch.object(train, "MemoryGymEnv", return_value=env),
            mock.patch.object(
                train, "run_dagger_iteration", return_value=dagger
            ) as run_dagger,
            mock.patch.object(
                train, "run_ppo_iteration", side_effect=run_ppo_iteration
            ) as run_ppo,
            mock.patch.object(train, "save_checkpoint") as save,
            mock.patch.object(train, "_stage_checkpoint_snapshot"),
            mock.patch.object(train, "_publish_checkpoint") as publish,
        ):
            code = train.main(
                ["--dagger-iterations", "1", "--ppo-iterations", "1"]
            )

        self.assertEqual(code, 0)
        self.assertEqual(run_dagger.call_count, 1)
        self.assertEqual(run_ppo.call_count, 1)
        self.assertEqual(run_ppo.call_args.args[3], "after-dagger")
        pending = train.DEFAULT_CHECKPOINT_DIR / ".latest.pt.pending"
        snapshot = train.DEFAULT_CHECKPOINT_DIR / (
            "iteration-00000002-dagger-00000001-ppo-00000001.pt"
        )
        self.assertEqual(save.call_args.args[0], pending)
        publish.assert_called_once_with(
            pending,
            train.DEFAULT_CHECKPOINT,
            snapshot=snapshot,
        )

    def test_resume_restores_state_and_runs_only_unfinished_ppo(self) -> None:
        env = _FakeEnv()
        env.raw_observation = SimpleNamespace(snapshot=SimpleNamespace(score=10))
        rollout = SimpleNamespace(
            samples=(),
            terminated=False,
            boundary_reward=0.0,
            boundary_frames=0,
            episode_frames=0,
            resume_reward=0.0,
            resume_frames=0,
        )
        result = SimpleNamespace(rollout=rollout, next_features="after-ppo")
        updates = (
            SimpleNamespace(
                total=1.0,
                policy=0.1,
                value=2.0,
                entropy=0.5,
                approximate_kl=0.01,
                clip_fraction=0.0,
            ),
        )

        def run_ppo(*args: object, **kwargs: object) -> object:
            del args
            kwargs["prepare_updates"](rollout, updates)
            kwargs["after_updates"](rollout, updates)
            return result

        with tempfile.TemporaryDirectory() as directory:
            checkpoint_dir = pathlib.Path(directory) / "policy"
            path = checkpoint_dir / "latest.pt"
            saved_args = train.build_parser().parse_args([])
            saved_args.dagger_iterations = 2
            saved_args.ppo_iterations = 2
            feature_spec = FeatureSpec()
            action_spec = ActionSpec()
            model_spec = ModelSpec(feature_spec.size, hidden_sizes=(8,))
            model = ActorCritic(model_spec, action_spec=action_spec)
            optimizer = torch.optim.Adam(model.parameters(), lr=3e-4)
            rng = random.Random(7)
            state = train._training_state(
                saved_args,
                phase="ppo",
                iteration=3,
                dagger_completed=2,
                ppo_completed=1,
                beta=0.5,
                buffer=DaggerBuffer(),
                feature_size=feature_spec.size,
                rng=rng,
            )
            save_checkpoint(
                path,
                iteration=3,
                beta=0.5,
                feature_spec=feature_spec,
                action_spec=action_spec,
                model_spec=model_spec,
                model=model,
                optimizer=optimizer,
                training_state=state,
            )

            with (
                mock.patch.object(train, "MemoryGymEnv", return_value=env),
                mock.patch.object(train, "run_dagger_iteration") as run_dagger,
                mock.patch.object(
                    train, "run_ppo_iteration", side_effect=run_ppo
                ) as run_ppo_iteration,
            ):
                code = train.main(
                    [
                        "--resume",
                        "--checkpoint-dir",
                        str(checkpoint_dir),
                    ]
                )

            restored = load_checkpoint(path)

        self.assertEqual(code, 0)
        run_dagger.assert_not_called()
        self.assertEqual(run_ppo_iteration.call_count, 1)
        self.assertEqual(restored.iteration, 4)
        self.assertEqual(restored.beta, 0.5)
        self.assertEqual(restored.training_state["dagger_completed"], 2)
        self.assertEqual(restored.training_state["ppo_completed"], 2)
        expected_python = random.Random()
        expected_python.setstate(state["python_rng"])
        resumed_python = random.Random()
        resumed_python.setstate(restored.training_state["python_rng"])
        self.assertEqual(expected_python.random(), resumed_python.random())
        expected_dagger = random.Random()
        expected_dagger.setstate(state["dagger_rng"])
        resumed_dagger = random.Random()
        resumed_dagger.setstate(restored.training_state["dagger_rng"])
        self.assertEqual(expected_dagger.random(), resumed_dagger.random())
        self.assertTrue(
            torch.equal(
                state["torch_rng"], restored.training_state["torch_rng"]
            )
        )

    def test_resume_rejects_an_evaluation_only_checkpoint(self) -> None:
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            checkpoint_dir = pathlib.Path(directory) / "policy"
            path = checkpoint_dir / "latest.pt"
            feature_spec = FeatureSpec(max_enemies=1, max_bullets=1)
            action_spec = ActionSpec()
            model_spec = ModelSpec(feature_spec.size, hidden_sizes=(8,))
            model = ActorCritic(model_spec, action_spec=action_spec)
            optimizer = torch.optim.Adam(model.parameters())
            save_checkpoint(
                path,
                iteration=0,
                beta=1.0,
                feature_spec=feature_spec,
                action_spec=action_spec,
                model_spec=model_spec,
                model=model,
                optimizer=optimizer,
            )

            with contextlib.redirect_stderr(stderr):
                code = train.main(
                    ["--resume", "--checkpoint-dir", str(checkpoint_dir)]
                )

        self.assertEqual(code, 1)
        self.assertIn("can only be evaluated", stderr.getvalue())

    def test_resume_rejects_a_limit_below_completed_work(self) -> None:
        saved_args = train.build_parser().parse_args([])
        saved_args.dagger_iterations = 2
        saved_args.ppo_iterations = 5
        feature_spec = FeatureSpec()
        state = train._training_state(
            saved_args,
            phase="ppo",
            iteration=7,
            dagger_completed=2,
            ppo_completed=5,
            beta=0.5,
            buffer=DaggerBuffer(),
            feature_size=feature_spec.size,
            rng=random.Random(7),
        )
        checkpoint = SimpleNamespace(
            training_state=state,
            iteration=7,
            beta=0.5,
        )
        cases = (
            (["--dagger-iterations", "1"], {"--dagger-iterations"}, "DAgger"),
            (
                ["--dagger-iterations", "2", "--ppo-iterations", "3"],
                {"--dagger-iterations", "--ppo-iterations"},
                "PPO",
            ),
        )
        for arguments, overrides, phase in cases:
            with self.subTest(phase=phase):
                requested = train.build_parser().parse_args(arguments)
                with self.assertRaisesRegex(
                    CheckpointError, f"{phase} iterations"
                ):
                    train._restore_training_state(
                        requested,
                        checkpoint,
                        schedule_overrides=overrides,
                    )

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
            mock.patch.object(train, "_stage_checkpoint_snapshot"),
            mock.patch.object(train, "_publish_checkpoint"),
        ):
            code = train.main(
                ["--dagger-iterations", "1", "--ppo-iterations", "0"]
            )

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
        train._log_dagger_rollout_metrics(self.writer, rollout, iteration=7)

        self.writer.add_scalar.assert_has_calls(
            (
                mock.call("loss/total", 3.0, 7),
                mock.call("loss/movement", 2.0, 7),
                mock.call("loss/bomb", 1.0, 7),
                mock.call("rollout/reward", 1.0, 7),
                mock.call("rollout/steps", 2, 7),
                mock.call("rollout/frames", 3, 7),
                mock.call("validation/preupdate_movement_accuracy", 0.5, 7),
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
        self.assertIn(
            "training interrupted during dagger at iteration 1", stderr.getvalue()
        )
        self.assertTrue(env.closed)

    def test_a_runtime_refusal_keeps_its_traceback_and_cleans_up(self) -> None:
        env = _FakeEnv()

        with (
            mock.patch.object(train, "MemoryGymEnv", return_value=env),
            mock.patch.object(
                train,
                "run_dagger_iteration",
                side_effect=NotInStage("lost stage"),
            ),
            self.assertRaisesRegex(NotInStage, "lost stage"),
        ):
            train.main([])

        self.assertTrue(env.closed)
        self.writer.close.assert_called_once_with()

    def test_an_interrupt_while_attaching_still_stops_cleanly(self) -> None:
        stderr = io.StringIO()

        with (
            mock.patch.object(train, "MemoryGymEnv", side_effect=KeyboardInterrupt),
            contextlib.redirect_stderr(stderr),
        ):
            code = train.main([])

        self.assertEqual(code, 130)
        self.assertIn(
            "training interrupted during dagger at iteration 0", stderr.getvalue()
        )

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
        self.assertIn(
            "training interrupted during dagger at iteration 0", stderr.getvalue()
        )
        env.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
