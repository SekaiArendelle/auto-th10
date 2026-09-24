import contextlib
import io
import json
import unittest

from auto_th10 import Action, Point
from fakes import make_bullet, make_enemy, make_laser, make_snapshot, observe
from training import collect, evaluate
from training.dataset import SCHEMA_VERSION, to_row
from training.loop import EpisodeResult, Step


class EvaluateTests(unittest.TestCase):
    def test_the_documented_defaults_are_the_real_ones(self) -> None:
        args = evaluate.build_parser().parse_args([])

        self.assertEqual(args.policy, "evasive")
        self.assertEqual(args.episodes, 1)
        self.assertIsNone(args.max_steps)

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
            next_observation=observe(make_snapshot(score=44, player=(11.0, 21.0))),
            terminated=False,
        )

    def test_the_row_carries_both_sides_of_the_transition(self) -> None:
        row = to_row(self.step)

        self.assertEqual(row["schema_version"], SCHEMA_VERSION)
        self.assertEqual(row["observation"]["score"], 42)
        self.assertEqual(row["observation"]["player"], {"x": 10.0, "y": 20.0})
        self.assertEqual(row["next_observation"]["score"], 44)
        self.assertEqual(row["action"], int(Action.SHOOT))
        self.assertEqual(row["reward"], 2.0)

    def test_the_row_carries_every_snapshot_object_field(self) -> None:
        step = Step(
            episode=0,
            index=0,
            observation=observe(
                make_snapshot(
                    enemies=(make_enemy(3.0, 4.0, size=24.0),),
                    enemy_bullets=(make_bullet(5.0, 6.0, size=8.0, dx=1.0, dy=2.0),),
                    enemy_lasers=(make_laser(7.0, 8.0, size=10.0, radian=0.5),),
                    resources=(Point(9.0, 10.0),),
                )
            ),
            action=Action.NONE,
            reward=0.0,
            next_observation=observe(make_snapshot()),
            terminated=False,
        )

        snapshot = to_row(step)["observation"]

        self.assertEqual(snapshot["enemies"][0], {"x": 3.0, "y": 4.0, "width": 24.0, "height": 24.0})
        self.assertEqual(
            snapshot["enemy_bullets"][0],
            {"x": 5.0, "y": 6.0, "width": 8.0, "height": 8.0, "dx": 1.0, "dy": 2.0},
        )
        self.assertEqual(
            snapshot["enemy_lasers"][0],
            {"x": 7.0, "y": 8.0, "width": 10.0, "height": 10.0, "radian": 0.5},
        )
        self.assertEqual(snapshot["resources"], [{"x": 9.0, "y": 10.0}])

    def test_rows_land_under_runs_by_default(self) -> None:
        args = collect.build_parser().parse_args([])

        self.assertIsNone(args.out)
        self.assertEqual(collect.DEFAULT_DIRECTORY.name, "runs")


if __name__ == "__main__":
    unittest.main()
