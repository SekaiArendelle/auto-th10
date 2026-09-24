import math
import unittest

from auto_th10 import EnemyLaser, Point
from fakes import make_bullet, make_enemy, make_snapshot, observe
from training.rl.features import FEATURE_SCHEMA_VERSION, FeatureSpec, MemoryFeatureEncoder


class FeatureSpecTests(unittest.TestCase):
    def test_size_describes_the_fixed_layout(self) -> None:
        spec = FeatureSpec(max_enemies=1, max_bullets=2, max_lasers=1, max_resources=1)

        self.assertEqual(spec.schema_version, FEATURE_SCHEMA_VERSION)
        empty_size = FeatureSpec(
            max_enemies=0,
            max_bullets=0,
            max_lasers=0,
            max_resources=0,
        ).size

        self.assertGreater(spec.size, empty_size)
        self.assertEqual(
            len(MemoryFeatureEncoder(spec).encode(observe(make_snapshot()))),
            spec.size,
        )

    def test_limits_must_be_non_negative_integers(self) -> None:
        with self.assertRaises(ValueError):
            FeatureSpec(max_bullets=-1)
        with self.assertRaises(TypeError):
            FeatureSpec(max_bullets=1.5)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            FeatureSpec(max_bullets=True)
        with self.assertRaises(TypeError):
            FeatureSpec(schema_version=True)

    def test_unknown_schema_versions_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            FeatureSpec(schema_version=99)


class MemoryFeatureEncoderTests(unittest.TestCase):
    def test_encode_has_the_declared_size_and_bounded_finite_values(self) -> None:
        spec = FeatureSpec(max_enemies=1, max_bullets=1, max_lasers=1, max_resources=1)
        snapshot = make_snapshot(
            player=(50.0, 240.0),
            score=500_000,
            power=100,
            enemies=(make_enemy(-100.0, 30.0, size=20.0),),
            enemy_bullets=(make_bullet(60.0, 250.0, size=8.0, dx=2.0, dy=-4.0),),
            enemy_lasers=(EnemyLaser(40.0, 220.0, 10.0, 30.0, math.pi / 2.0),),
            resources=(Point(70.0, 260.0),),
        )

        features = MemoryFeatureEncoder(spec).encode(observe(snapshot))

        self.assertEqual(len(features), spec.size)
        self.assertTrue(all(math.isfinite(value) for value in features))
        self.assertTrue(all(-1.0 <= value <= 1.0 for value in features))

        expected = (
            0.25,
            0.0,
            1.0 / 3.0,
            0.5,
            0.4,
            0.0,
            0.5,
            0.5,
            0.5,
            0.5,
            -150.0 / 400.0,
            -210.0 / 480.0,
            20.0 / 400.0,
            20.0 / 480.0,
            1.0,
            10.0 / 400.0,
            10.0 / 480.0,
            8.0 / 400.0,
            8.0 / 480.0,
            2.0 / 16.0,
            -4.0 / 16.0,
            1.0,
            -10.0 / 400.0,
            -20.0 / 480.0,
            10.0 / 400.0,
            30.0 / 480.0,
            1.0,
            0.0,
            1.0,
            20.0 / 400.0,
            20.0 / 480.0,
            1.0,
        )
        self.assertEqual(len(expected), len(features))
        for actual, wanted in zip(features, expected):
            self.assertAlmostEqual(actual, wanted)

    def test_entities_are_sorted_by_distance_and_truncated(self) -> None:
        spec = FeatureSpec(max_enemies=0, max_bullets=2, max_lasers=0, max_resources=0)
        snapshot = make_snapshot(
            player=(0.0, 240.0),
            enemy_bullets=(
                make_bullet(100.0, 240.0),
                make_bullet(10.0, 240.0),
                make_bullet(-20.0, 240.0),
            ),
        )

        features = MemoryFeatureEncoder(spec).encode(observe(snapshot))
        bullet_offset = FeatureSpec(
            max_enemies=0,
            max_bullets=0,
            max_lasers=0,
            max_resources=0,
        ).size

        self.assertAlmostEqual(features[bullet_offset], 10.0 / 400.0)
        self.assertEqual(features[bullet_offset + 6], 1.0)
        self.assertAlmostEqual(features[bullet_offset + 7], -20.0 / 400.0)
        self.assertEqual(features[bullet_offset + 13], 1.0)

    def test_missing_entities_are_zero_padded_with_a_clear_mask(self) -> None:
        spec = FeatureSpec(max_enemies=1, max_bullets=2, max_lasers=0, max_resources=0)
        snapshot = make_snapshot(enemy_bullets=(make_bullet(1.0, 2.0),))

        features = MemoryFeatureEncoder(spec).encode(observe(snapshot))
        bullet_offset = FeatureSpec(
            max_enemies=1,
            max_bullets=0,
            max_lasers=0,
            max_resources=0,
        ).size

        self.assertEqual(features[bullet_offset + 6], 1.0)
        self.assertEqual(features[bullet_offset + 7 : bullet_offset + 14], (0.0,) * 7)

    def test_lasers_are_ranked_by_their_body_instead_of_their_origin(self) -> None:
        spec = FeatureSpec(max_enemies=0, max_bullets=0, max_lasers=1, max_resources=0)
        snapshot = make_snapshot(
            player=(0.0, 200.0),
            enemy_lasers=(
                EnemyLaser(50.0, 200.0, 2.0, 2.0, math.pi / 2.0),
                EnemyLaser(0.0, 0.0, 8.0, 240.0, math.pi / 2.0),
            ),
        )

        features = MemoryFeatureEncoder(spec).encode(observe(snapshot))
        laser_offset = FeatureSpec(
            max_enemies=0,
            max_bullets=0,
            max_lasers=0,
            max_resources=0,
        ).size

        self.assertEqual(features[laser_offset], 0.0)
        self.assertAlmostEqual(features[laser_offset + 1], -200.0 / 480.0)

    def test_global_features_keep_counts_after_entity_truncation(self) -> None:
        spec = FeatureSpec(max_enemies=0, max_bullets=1, max_lasers=0, max_resources=0)
        one = MemoryFeatureEncoder(spec).encode(
            observe(make_snapshot(enemy_bullets=(make_bullet(1.0, 2.0),)))
        )
        many = MemoryFeatureEncoder(spec).encode(
            observe(
                make_snapshot(
                    enemy_bullets=(make_bullet(1.0, 2.0), make_bullet(3.0, 4.0))
                )
            )
        )

        self.assertGreater(many[7], one[7])


if __name__ == "__main__":
    unittest.main()
