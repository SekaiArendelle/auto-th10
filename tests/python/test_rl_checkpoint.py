import pathlib
import tempfile
import unittest

import torch

from training.rl import (
    ActionSpec,
    ActorCritic,
    CheckpointError,
    FeatureSpec,
    ModelSpec,
    load_checkpoint,
    save_checkpoint,
)


class CheckpointTests(unittest.TestCase):
    def make_checkpoint(self, path: pathlib.Path) -> ActorCritic:
        feature_spec = FeatureSpec(max_enemies=1, max_bullets=1)
        action_spec = ActionSpec()
        model_spec = ModelSpec(feature_spec.size, hidden_sizes=(8,))
        model = ActorCritic(model_spec, action_spec=action_spec)
        optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
        save_checkpoint(
            path,
            iteration=3,
            beta=0.5,
            feature_spec=feature_spec,
            action_spec=action_spec,
            model_spec=model_spec,
            model=model,
            optimizer=optimizer,
        )
        return model

    def test_round_trip_reconstructs_an_evaluation_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "checkpoint.pt"
            original = self.make_checkpoint(path)

            loaded = load_checkpoint(path)

        self.assertEqual(loaded.iteration, 3)
        self.assertEqual(loaded.beta, 0.5)
        self.assertEqual(loaded.model_spec.hidden_sizes, (8,))
        self.assertEqual(loaded.model_spec.observation_size, loaded.feature_spec.size)
        self.assertFalse(loaded.model.training)
        for name, value in original.state_dict().items():
            self.assertTrue(torch.equal(value, loaded.model.state_dict()[name]))

    def test_unknown_feature_schema_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "checkpoint.pt"
            self.make_checkpoint(path)
            payload = torch.load(path, weights_only=True)
            payload["feature_spec"]["schema_version"] = 999
            torch.save(payload, path)

            with self.assertRaisesRegex(CheckpointError, "feature_spec"):
                load_checkpoint(path)

    def test_observation_size_must_match_the_feature_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "checkpoint.pt"
            self.make_checkpoint(path)
            payload = torch.load(path, weights_only=True)
            payload["model_spec"]["observation_size"] += 1
            torch.save(payload, path)

            with self.assertRaisesRegex(CheckpointError, "observation size"):
                load_checkpoint(path)

    def test_weight_shape_must_match_the_model_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "checkpoint.pt"
            self.make_checkpoint(path)
            payload = torch.load(path, weights_only=True)
            del payload["model_state_dict"]["movement_head.weight"]
            torch.save(payload, path)

            with self.assertRaisesRegex(CheckpointError, "weights"):
                load_checkpoint(path)

    def test_weight_dtype_must_match_the_model_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "checkpoint.pt"
            self.make_checkpoint(path)
            payload = torch.load(path, weights_only=True)
            weight = payload["model_state_dict"]["movement_head.weight"]
            payload["model_state_dict"]["movement_head.weight"] = weight.to(
                torch.int64
            )
            torch.save(payload, path)

            with self.assertRaisesRegex(CheckpointError, "dtype"):
                load_checkpoint(path)

    def test_non_finite_weights_are_rejected_at_load_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "checkpoint.pt"
            self.make_checkpoint(path)
            payload = torch.load(path, weights_only=True)
            payload["model_state_dict"]["movement_head.weight"][0, 0] = float(
                "nan"
            )
            torch.save(payload, path)

            with self.assertRaisesRegex(CheckpointError, "non-finite"):
                load_checkpoint(path)


if __name__ == "__main__":
    unittest.main()
