import unittest

import torch

from training.rl import ActorCritic, ModelSpec, imitation_loss, update_imitation


class ImitationLossTests(unittest.TestCase):
    def setUp(self) -> None:
        torch.manual_seed(7)
        self.model = ActorCritic(ModelSpec(4, hidden_sizes=(8,)))
        self.observations = torch.randn((6, 4))
        self.labels = torch.tensor(
            ((0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (5, 1))
        )

    def test_loss_has_independently_visible_terms(self) -> None:
        losses = imitation_loss(self.model, self.observations, self.labels)

        self.assertAlmostEqual(
            losses.total.item(),
            losses.movement.item() + losses.bomb.item(),
            places=6,
        )
        self.assertGreater(losses.movement.item(), 0.0)
        self.assertGreater(losses.bomb.item(), 0.0)

    def test_an_update_changes_model_parameters(self) -> None:
        optimizer = torch.optim.Adam(self.model.parameters(), lr=1e-2)
        before = [parameter.detach().clone() for parameter in self.model.parameters()]

        metrics = update_imitation(
            self.model, optimizer, self.observations, self.labels
        )

        self.assertGreater(metrics.total, 0.0)
        self.assertTrue(
            any(
                not torch.equal(old, new)
                for old, new in zip(before, self.model.parameters())
            )
        )

    def test_invalid_weights_and_label_shapes_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            imitation_loss(
                self.model,
                self.observations,
                self.labels,
                bomb_positive_weight=0.0,
            )
        with self.assertRaises(ValueError):
            imitation_loss(
                self.model, self.observations, torch.zeros((6, 1), dtype=torch.long)
            )


if __name__ == "__main__":
    unittest.main()
