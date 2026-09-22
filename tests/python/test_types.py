import unittest

from auto_th10.types import Snapshot


class SnapshotTests(unittest.TestCase):
    def test_snapshot_from_native(self) -> None:
        snapshot = Snapshot.from_native(
            {
                "player": (1.0, 2.0),
                "score": 100,
                "power": 20,
                "lives": 2,
                "game_over": False,
                "enemies": [(3.0, 4.0, 5.0, 6.0)],
                "enemy_bullets": [(7.0, 8.0, 9.0, 10.0, 0.5, -0.5)],
                "enemy_lasers": [(11.0, 12.0, 13.0, 14.0, 1.5)],
                "resources": [(15.0, 16.0)],
            }
        )

        self.assertEqual(snapshot.player.x, 1.0)
        self.assertEqual(snapshot.enemies[0].width, 5.0)
        self.assertEqual(snapshot.enemy_bullets[0].dy, -0.5)
        self.assertEqual(snapshot.enemy_lasers[0].radian, 1.5)


if __name__ == "__main__":
    unittest.main()
