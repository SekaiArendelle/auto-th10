import unittest

from auto_th10 import Action, Th10Env
from fakes import FakeSession, make_snapshot
from training.loop import run_episode, run_episodes
from training.policy import FixedPolicy


def build_env(*snapshots) -> Th10Env:
    return Th10Env(session=FakeSession(snapshots=snapshots))


class RunEpisodeTests(unittest.TestCase):
    def test_runs_until_the_run_is_over(self) -> None:
        env = build_env(
            make_snapshot(score=0),
            make_snapshot(score=5),
            make_snapshot(score=9, lives=-1, game_over=True),
        )
        steps = []

        result = run_episode(env, FixedPolicy([Action.SHOOT], frames_per_action=1), on_step=steps.append)

        self.assertEqual(result.ending, "game_over")
        self.assertEqual(result.steps, 2)
        self.assertEqual(result.score, 9)
        self.assertEqual(result.total_reward, 9.0)
        self.assertEqual([step.action for step in steps], [Action.SHOOT, Action.SHOOT])
        self.assertEqual([step.index for step in steps], [0, 1])
        self.assertTrue(steps[-1].terminated)

    def test_stops_at_the_step_limit(self) -> None:
        env = build_env(make_snapshot(), make_snapshot(score=1), make_snapshot(score=2))

        result = run_episode(env, FixedPolicy(), max_steps=2)

        self.assertEqual(result.ending, "step_limit")
        self.assertEqual(result.steps, 2)

    def test_counts_the_frames_the_episode_covered(self) -> None:
        env = build_env(make_snapshot(), make_snapshot(), make_snapshot(lives=-1, game_over=True))

        result = run_episode(env, FixedPolicy(), max_steps=5)

        self.assertEqual(result.frames, result.steps)

    def test_without_a_step_limit_it_runs_until_the_run_is_over(self) -> None:
        env = build_env(
            make_snapshot(),
            make_snapshot(score=1),
            make_snapshot(lives=-1, game_over=True),
        )

        result = run_episode(env, FixedPolicy(), max_steps=None)

        self.assertEqual(result.ending, "game_over")
        self.assertEqual(result.steps, 2)

    def test_ending_on_the_final_allowed_step_is_a_game_over(self) -> None:
        env = build_env(make_snapshot(), make_snapshot(lives=-1, game_over=True))

        result = run_episode(env, FixedPolicy(), max_steps=1)

        self.assertEqual(result.ending, "game_over")
        self.assertEqual(result.steps, 1)

    def test_rejects_a_non_positive_step_limit(self) -> None:
        with self.assertRaises(ValueError):
            run_episode(build_env(make_snapshot()), FixedPolicy(), max_steps=0)

    def test_the_step_hook_can_be_left_out(self) -> None:
        env = build_env(make_snapshot(), make_snapshot(lives=-1, game_over=True))

        result = run_episode(env, FixedPolicy())

        self.assertEqual(result.steps, 1)


class RunEpisodesTests(unittest.TestCase):
    def test_numbers_the_episodes_and_reports_each_one(self) -> None:
        env = build_env(
            make_snapshot(),
            make_snapshot(lives=-1, game_over=True),
            make_snapshot(),
            make_snapshot(lives=-1, game_over=True),
        )
        seen = []

        results = run_episodes(env, FixedPolicy(), 2, on_episode=seen.append)

        self.assertEqual([result.episode for result in results], [0, 1])
        self.assertEqual(seen, results)
        self.assertEqual([result.steps for result in results], [1, 1])

    def test_rejects_a_non_positive_episode_count(self) -> None:
        with self.assertRaises(ValueError):
            run_episodes(build_env(make_snapshot()), FixedPolicy(), 0)


if __name__ == "__main__":
    unittest.main()
