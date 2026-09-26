import contextlib
import io
import json
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np

from auto_th10 import Action, NotInStage
from fakes import FakeSession, make_bullet, make_environment, make_snapshot
from training import evaluate_model
from training.rl import ActionSample, EvasiveTeacher, MemoryGymEnv, ModelAction
from training.rl import gym_env as gym_env_module


class FixedLearner:
    def act(
        self, observation: np.ndarray, *, deterministic: bool = False
    ) -> ActionSample:
        del observation
        if not deterministic:
            raise AssertionError("evaluation must request a deterministic action")
        return ActionSample(ModelAction(0, False), log_prob=0.0, value=0.0)


class EvaluateModelTests(unittest.TestCase):
    def make_env(self, session: FakeSession) -> MemoryGymEnv:
        core = make_environment(session)
        with mock.patch.object(gym_env_module, "Th10Env", return_value=core):
            return MemoryGymEnv()

    def test_defaults_use_the_training_checkpoint(self) -> None:
        args = evaluate_model.build_parser().parse_args([])

        self.assertEqual(args.checkpoint, evaluate_model.DEFAULT_CHECKPOINT)
        self.assertEqual(args.episodes, 1)
        self.assertIsNone(args.max_steps)

    def test_multiple_episodes_cannot_split_one_live_run_at_a_step_limit(
        self,
    ) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                evaluate_model.main(["--episodes", "2", "--max-steps", "10"])

        self.assertEqual(raised.exception.code, 2)

    def test_a_runtime_refusal_keeps_its_traceback_and_closes_the_env(self) -> None:
        env = mock.Mock()
        checkpoint = SimpleNamespace(feature_spec=object(), model=object())

        with (
            mock.patch.object(
                evaluate_model,
                "load_checkpoint",
                return_value=checkpoint,
            ),
            mock.patch.object(evaluate_model, "MemoryGymEnv", return_value=env),
            mock.patch.object(
                evaluate_model,
                "evaluate_episode",
                side_effect=NotInStage("lost stage"),
            ),
            self.assertRaisesRegex(NotInStage, "lost stage"),
        ):
            evaluate_model.main([])

        env.close.assert_called_once_with()

    def test_episode_executes_only_the_model_and_measures_teacher_disagreement(
        self,
    ) -> None:
        player = (0.0, 400.0)
        session = FakeSession(
            snapshots=(
                make_snapshot(
                    player=player,
                    enemy_bullets=(make_bullet(*player),),
                ),
                make_snapshot(player=player, score=123, game_over=True),
            )
        )
        env = self.make_env(session)

        result = evaluate_model.evaluate_episode(
            env,
            FixedLearner(),
            EvasiveTeacher(),
            episode=2,
        )

        self.assertEqual(result.episode, 2)
        self.assertEqual(result.steps, 1)
        self.assertEqual(result.score, 123)
        self.assertEqual(result.ending, "game_over")
        self.assertEqual(result.bombs, 0)
        self.assertEqual(result.bomb_false_negatives, 1)
        self.assertEqual(result.bomb_recall, 0.0)
        self.assertFalse(session.inputs[0] & Action.BOMB)

    def test_json_report_contains_episode_and_aggregate_metrics(self) -> None:
        result = evaluate_model.ModelEpisodeResult(
            episode=0,
            steps=4,
            frames=4,
            score=100,
            total_reward=1.0,
            ending="step_limit",
            bombs=2,
            movement_matches=3,
            bomb_true_positives=1,
            bomb_false_positives=1,
            bomb_false_negatives=1,
            bomb_true_negatives=1,
        )
        stream = io.StringIO()

        with contextlib.redirect_stdout(stream):
            evaluate_model.report([result], as_json=True)

        rows = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertEqual(rows[0]["bomb_precision"], 0.5)
        self.assertEqual(rows[0]["bomb_recall"], 0.5)
        self.assertEqual(rows[1]["episodes"], 1)
        self.assertEqual(rows[1]["movement_agreement"], 0.75)


if __name__ == "__main__":
    unittest.main()
