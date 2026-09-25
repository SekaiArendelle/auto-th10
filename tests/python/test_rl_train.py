import pathlib
import tempfile
import unittest

import torch

from training import train
from training.rl import ActionSpec, ActorCritic, FeatureSpec, ModelSpec


class TrainEntryPointTests(unittest.TestCase):
    def test_defaults_define_a_fixed_horizon_dagger_run(self) -> None:
        args = train.build_parser().parse_args([])

        self.assertEqual(args.horizon, 1024)
        self.assertEqual(args.beta, 1.0)
        self.assertEqual(args.beta_min, 0.05)
        self.assertEqual(args.checkpoint, train.DEFAULT_CHECKPOINT)

    def test_checkpoint_carries_model_and_schema_metadata(self) -> None:
        feature_spec = FeatureSpec(max_enemies=1, max_bullets=1)
        action_spec = ActionSpec()
        model_spec = ModelSpec(feature_spec.size, hidden_sizes=(8,))
        model = ActorCritic(model_spec, action_spec=action_spec)
        optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)

        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "checkpoint.pt"
            train.save_checkpoint(
                path,
                iteration=3,
                beta=0.5,
                feature_spec=feature_spec,
                action_spec=action_spec,
                model_spec=model_spec,
                model=model,
                optimizer=optimizer,
            )
            payload = torch.load(path)

        self.assertEqual(payload["format_version"], train.CHECKPOINT_FORMAT_VERSION)
        self.assertEqual(payload["iteration"], 3)
        self.assertEqual(payload["feature_spec"]["schema_version"], 2)
        self.assertEqual(payload["action_spec"]["schema_version"], 1)
        self.assertEqual(payload["model_spec"]["hidden_sizes"], (8,))
        self.assertIn("movement_head.weight", payload["model_state_dict"])


if __name__ == "__main__":
    unittest.main()
