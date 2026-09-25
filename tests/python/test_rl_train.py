import unittest

from training import train


class TrainEntryPointTests(unittest.TestCase):
    def test_defaults_define_a_fixed_horizon_dagger_run(self) -> None:
        args = train.build_parser().parse_args([])

        self.assertEqual(args.horizon, 1024)
        self.assertEqual(args.beta, 1.0)
        self.assertEqual(args.beta_min, 0.05)
        self.assertEqual(args.checkpoint, train.DEFAULT_CHECKPOINT)

if __name__ == "__main__":
    unittest.main()
