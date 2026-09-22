import contextlib
import io
import json
import unittest

from auto_th10 import Action
from fakes import make_bullet, make_snapshot, observe
from training import collect, evaluate
from training.loop import EpisodeResult, Step


class EvaluateTests(unittest.TestCase):
    def test_the_documented_defaults_are_the_real_ones(self) -> None:
        args = evaluate.build_parser().parse_args([])

        self.assertEqual(args.policy, "evasive")
        self.assertEqual(args.episodes, 1)
        self.assertEqual(args.max_steps, 3600)

    def test_the_json_report_is_one_object_per_episode_plus_a_summary(self) -> None:
        results = [
            EpisodeResult(episode=0, steps=10, frames=10, score=100, total_reward=100.0, ending="game_over"),
            EpisodeResult(episode=1, steps=20, frames=20, score=300, total_reward=300.0, ending="game_over"),
        ]
        buffer = io.StringIO()

        with contextlib.redirect_stdout(buffer):
            evaluate.report(results, as_json=True)

        lines = [json.loads(line) for line in buffer.getvalue().splitlines()]
        self.assertEqual(lines[0]["score"], 100)
        self.assertEqual(lines[-1], {"episodes": 2, "best": 300, "mean": 200.0})

    def test_the_text_report_names_the_ending(self) -> None:
        results = [
            EpisodeResult(episode=0, steps=1, frames=1, score=0, total_reward=0.0, ending="step_limit")
        ]
        buffer = io.StringIO()

        with contextlib.redirect_stdout(buffer):
            evaluate.report(results, as_json=False)

        self.assertIn("step_limit", buffer.getvalue())


class CollectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.step = Step(
            episode=0,
            index=3,
            observation=observe(
                make_snapshot(
                    score=42,
                    power=100,
                    lives=2,
                    player=(10.0, 20.0),
                    enemy_bullets=(make_bullet(1.0, 2.0),),
                )
            ),
            action=Action.SHOOT,
            reward=2.0,
            terminated=False,
        )

    def test_the_row_carries_what_a_policy_reads(self) -> None:
        row = collect.to_row(self.step, with_bullets=False)

        self.assertEqual(row["score"], 42)
        self.assertEqual(row["power"], 100)
        self.assertEqual(row["player"], [10.0, 20.0])
        self.assertEqual(row["bullets"], 1)
        self.assertEqual(row["action"], int(Action.SHOOT))
        self.assertEqual(row["reward"], 2.0)
        self.assertNotIn("bullet_positions", row)

    def test_bullet_positions_are_opt_in(self) -> None:
        row = collect.to_row(self.step, with_bullets=True)

        self.assertEqual(row["bullet_positions"], [[1.0, 2.0]])

    def test_rows_land_under_runs_by_default(self) -> None:
        args = collect.build_parser().parse_args([])

        self.assertIsNone(args.out)
        self.assertEqual(collect.DEFAULT_DIRECTORY.name, "runs")


if __name__ == "__main__":
    unittest.main()
