import unittest

import numpy as np
import torch

from training.rl import ActorCritic, ModelSpec


class ModelSpecTests(unittest.TestCase):
    def test_sizes_must_be_positive_integers(self) -> None:
        with self.assertRaises(ValueError):
            ModelSpec(0)
        with self.assertRaises(TypeError):
            ModelSpec(True)
        with self.assertRaises(ValueError):
            ModelSpec(4, hidden_sizes=(0,))


class ActorCriticTests(unittest.TestCase):
    def test_forward_has_two_action_heads_and_one_value(self) -> None:
        model = ActorCritic(ModelSpec(5, hidden_sizes=(8,)))

        output = model(torch.zeros((3, 5)))

        self.assertEqual(output.movement_logits.shape, (3, 17))
        self.assertEqual(output.bomb_logits.shape, (3, 2))
        self.assertEqual(output.value.shape, (3,))

    def test_evaluation_sums_the_two_categorical_distributions(self) -> None:
        model = ActorCritic(ModelSpec(5, hidden_sizes=()))
        observations = torch.zeros((2, 5))
        actions = torch.tensor(((0, 0), (3, 1)))

        evaluation = model.evaluate_actions(observations, actions)

        self.assertEqual(evaluation.log_prob.shape, (2,))
        self.assertEqual(evaluation.entropy.shape, (2,))
        self.assertEqual(evaluation.value.shape, (2,))
        self.assertTrue(torch.all(evaluation.entropy > 0.0))

    def test_act_returns_a_valid_model_action(self) -> None:
        model = ActorCritic(ModelSpec(5, hidden_sizes=(8,)))

        sample = model.act(np.zeros(5, dtype=np.float32))

        self.assertIn(sample.action.movement, range(17))
        self.assertIn(sample.action.bomb, (0, 1))
        self.assertTrue(np.isfinite(sample.log_prob))
        self.assertTrue(np.isfinite(sample.value))

    def test_deterministic_action_uses_each_heads_argmax(self) -> None:
        model = ActorCritic(ModelSpec(3, hidden_sizes=()))
        with torch.no_grad():
            model.movement_head.weight.zero_()
            model.movement_head.bias.copy_(torch.arange(17, dtype=torch.float32))
            model.bomb_head.weight.zero_()
            model.bomb_head.bias.copy_(torch.tensor((2.0, 1.0)))

        sample = model.act((0.0, 0.0, 0.0), deterministic=True)

        self.assertEqual(sample.action.movement, 16)
        self.assertEqual(sample.action.bomb, 0)

    def test_wrong_observation_or_action_shapes_are_rejected(self) -> None:
        model = ActorCritic(ModelSpec(5))

        with self.assertRaises(ValueError):
            model(torch.zeros((2, 4)))
        with self.assertRaises(ValueError):
            model.act(torch.zeros((2, 5)))
        with self.assertRaises(ValueError):
            model.evaluate_actions(torch.zeros((2, 5)), torch.zeros((2, 1)))


if __name__ == "__main__":
    unittest.main()
