import math
import unittest

from auto_th10 import Action
from fakes import make_snapshot
from training.rl.rewards import RewardSpec, memory_reward


class RewardSpecTests(unittest.TestCase):
    def test_invalid_scales_and_penalties_are_rejected(self) -> None:
        for kwargs in (
            {"survival_per_frame": -0.1},
            {"score_scale": 0.0},
            {"score_clip": -1.0},
            {"life_lost_penalty": math.inf},
            {"bomb_penalty": True},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises((TypeError, ValueError)):
                RewardSpec(**kwargs)


class MemoryRewardTests(unittest.TestCase):
    def test_reward_terms_are_independently_visible(self) -> None:
        reward = memory_reward(
            make_snapshot(score=100, lives=3),
            make_snapshot(score=700, lives=2),
            Action.RIGHT | Action.BOMB,
            frames=4,
        )

        self.assertAlmostEqual(reward.survival, 0.004)
        self.assertAlmostEqual(reward.score, 0.6)
        self.assertEqual(reward.life, -1.0)
        self.assertEqual(reward.bomb, -0.1)
        self.assertEqual(reward.game_over, 0.0)
        self.assertAlmostEqual(reward.total, -0.496)

    def test_score_gain_is_clipped_and_score_loss_is_not_punished(self) -> None:
        clipped = memory_reward(
            make_snapshot(score=0), make_snapshot(score=5000), Action.NONE, frames=1
        )
        reset = memory_reward(
            make_snapshot(score=5000), make_snapshot(score=0), Action.NONE, frames=1
        )

        self.assertEqual(clipped.score, 1.0)
        self.assertEqual(reset.score, 0.0)

    def test_game_over_replaces_survival_and_is_only_penalized_once(self) -> None:
        first = memory_reward(
            make_snapshot(),
            make_snapshot(lives=-1, game_over=True),
            Action.NONE,
            frames=1,
        )
        repeated = memory_reward(
            make_snapshot(lives=-1, game_over=True),
            make_snapshot(lives=-1, game_over=True),
            Action.NONE,
            frames=1,
        )

        self.assertEqual(first.survival, 0.0)
        self.assertEqual(first.game_over, -1.0)
        self.assertEqual(repeated.game_over, 0.0)

    def test_frames_must_be_a_positive_integer(self) -> None:
        for frames in (0, -1):
            with self.subTest(frames=frames), self.assertRaises(ValueError):
                memory_reward(make_snapshot(), make_snapshot(), Action.NONE, frames=frames)
        for frames in (True, 1.5):
            with self.subTest(frames=frames), self.assertRaises(TypeError):
                memory_reward(make_snapshot(), make_snapshot(), Action.NONE, frames=frames)


if __name__ == "__main__":
    unittest.main()
