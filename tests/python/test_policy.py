import unittest

from auto_th10 import Action
from fakes import make_bullet, make_enemy, make_laser, make_snapshot, observe
from training.policy import POLICIES, EvasivePolicy, FixedPolicy, RandomPolicy


class FixedPolicyTests(unittest.TestCase):
    def test_cycles_through_the_actions(self) -> None:
        policy = FixedPolicy([Action.LEFT, Action.RIGHT], frames_per_action=1)

        decisions = [policy.decide(observe(make_snapshot())) for _ in range(4)]

        self.assertEqual(decisions, [Action.LEFT, Action.RIGHT, Action.LEFT, Action.RIGHT])

    def test_holds_each_action_for_its_frames(self) -> None:
        policy = FixedPolicy([Action.LEFT, Action.RIGHT], frames_per_action=2)

        decisions = [policy.decide(observe(make_snapshot())) for _ in range(4)]

        self.assertEqual(decisions, [Action.LEFT, Action.LEFT, Action.RIGHT, Action.RIGHT])

    def test_rejects_an_empty_pattern(self) -> None:
        with self.assertRaises(ValueError):
            FixedPolicy([])

    def test_rejects_a_non_positive_duration(self) -> None:
        with self.assertRaises(ValueError):
            FixedPolicy([Action.SHOOT], frames_per_action=0)


class EvasivePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = EvasivePolicy()

    def test_shoots_and_focuses_on_a_clear_screen(self) -> None:
        action = self.policy.decide(observe(make_snapshot(player=(320.0, 400.0))))

        self.assertIn(Action.SHOOT, action)
        self.assertIn(Action.FOCUS, action)

    def test_bombs_when_a_bullet_is_on_top_of_the_player(self) -> None:
        snapshot = make_snapshot(player=(320.0, 400.0), enemy_bullets=(make_bullet(320.0, 400.0),))

        action = self.policy.decide(observe(snapshot))

        self.assertIn(Action.BOMB, action)

    def test_steps_away_from_a_bullet_that_is_closing_in(self) -> None:
        # The bullet sits 44 px to the right of the player, so the room is left.
        snapshot = make_snapshot(player=(320.0, 400.0), enemy_bullets=(make_bullet(360.0, 400.0),))

        action = self.policy.decide(observe(snapshot))

        self.assertIn(Action.LEFT, action)
        self.assertNotIn(Action.RIGHT, action)

    def test_steps_the_other_way_when_the_bullet_is_on_the_left(self) -> None:
        snapshot = make_snapshot(player=(320.0, 400.0), enemy_bullets=(make_bullet(280.0, 400.0),))

        action = self.policy.decide(observe(snapshot))

        self.assertIn(Action.RIGHT, action)
        self.assertNotIn(Action.LEFT, action)

    def test_ignores_a_bullet_far_outside_the_danger_radius(self) -> None:
        snapshot = make_snapshot(
            player=(320.0, 400.0),
            enemy_bullets=(make_bullet(320.0, 100.0),),
        )

        action = self.policy.decide(observe(snapshot))

        self.assertNotIn(Action.LEFT, action)
        self.assertNotIn(Action.RIGHT, action)

    def test_lasers_are_treated_as_hazards(self) -> None:
        snapshot = make_snapshot(player=(320.0, 400.0), enemy_lasers=(make_laser(320.0, 400.0),))

        action = self.policy.decide(observe(snapshot))

        self.assertIn(Action.BOMB, action)

    def test_aims_at_the_nearest_enemy_when_nothing_is_near(self) -> None:
        snapshot = make_snapshot(player=(320.0, 400.0), enemies=(make_enemy(500.0, 100.0),))

        action = self.policy.decide(observe(snapshot))

        self.assertIn(Action.RIGHT, action)

    def test_does_not_move_when_already_aligned_with_the_enemy(self) -> None:
        # The enemy is centred on the player's x, so there is nothing to line up.
        snapshot = make_snapshot(player=(320.0, 400.0), enemies=(make_enemy(308.0, 100.0),))

        action = self.policy.decide(observe(snapshot))

        self.assertNotIn(Action.LEFT, action)
        self.assertNotIn(Action.RIGHT, action)
        self.assertIn(Action.SHOOT, action)

    def test_focus_can_be_turned_off(self) -> None:
        action = EvasivePolicy(focus=False).decide(observe(make_snapshot()))

        self.assertNotIn(Action.FOCUS, action)


class RandomPolicyTests(unittest.TestCase):
    def test_chooses_from_the_given_actions(self) -> None:
        policy = RandomPolicy([Action.LEFT, Action.RIGHT], seed=1)
        observation = observe(make_snapshot())

        chosen = {policy.decide(observation) for _ in range(50)}

        self.assertLessEqual(chosen, {Action.LEFT, Action.RIGHT})
        self.assertEqual(len(chosen), 2)

    def test_is_reproducible_with_a_seed(self) -> None:
        observation = observe(make_snapshot())
        first = RandomPolicy([Action.LEFT, Action.RIGHT], seed=7)
        second = RandomPolicy([Action.LEFT, Action.RIGHT], seed=7)

        self.assertEqual(
            [first.decide(observation) for _ in range(8)],
            [second.decide(observation) for _ in range(8)],
        )

    def test_rejects_an_empty_choice_list(self) -> None:
        with self.assertRaises(ValueError):
            RandomPolicy([])


class RegistryTests(unittest.TestCase):
    def test_names_the_three_policies(self) -> None:
        self.assertEqual(sorted(POLICIES), ["evasive", "fixed", "random"])

    def test_every_entry_builds_a_policy(self) -> None:
        for name, build in POLICIES.items():
            with self.subTest(policy=name):
                self.assertTrue(hasattr(build(), "decide"))


if __name__ == "__main__":
    unittest.main()
