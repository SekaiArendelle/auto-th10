import unittest

from auto_th10 import Action, Point
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


PLAYER = (0.0, 400.0)
"""Where the tests put the player: the middle of the field, near the bottom.

The playfield is the game's own, x from -200 to 200 and y from 0 to 480 (see
training/dodging.py), so these are real positions rather than arbitrary ones -
the value of a spot depends on where it is.
"""


class EvasivePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = EvasivePolicy()

    def decide(self, **snapshot: object) -> Action:
        return self.policy.decide(observe(make_snapshot(player=PLAYER, **snapshot)))

    def test_shoots_and_stays_focused_on_a_clear_screen(self) -> None:
        action = self.decide()

        self.assertIn(Action.SHOOT, action)
        self.assertIn(Action.FOCUS, action)
        self.assertFalse(action & (Action.LEFT | Action.RIGHT | Action.UP | Action.DOWN))

    def test_bombs_when_a_bullet_is_already_on_the_player(self) -> None:
        # Nothing can be reached: the bullet is hit on the very first frame.
        action = self.decide(enemy_bullets=(make_bullet(*PLAYER),))

        self.assertIn(Action.BOMB, action)

    def test_does_not_spend_a_second_bomb_straight_away(self) -> None:
        bullets = (make_bullet(*PLAYER),)

        self.assertIn(Action.BOMB, self.decide(enemy_bullets=bullets))
        self.assertNotIn(Action.BOMB, self.decide(enemy_bullets=bullets))

    def test_the_bomb_cooldown_lasts_the_configured_frames(self) -> None:
        policy = EvasivePolicy(bomb_cooldown_frames=5)
        observation = observe(make_snapshot(player=PLAYER, enemy_bullets=(make_bullet(*PLAYER),)))

        self.assertIn(Action.BOMB, policy.decide(observation))
        for _ in range(5):
            self.assertNotIn(Action.BOMB, policy.decide(observation))
        self.assertIn(Action.BOMB, policy.decide(observation))

    def test_a_new_run_clears_the_bomb_cooldown(self) -> None:
        bullets = (make_bullet(*PLAYER),)

        self.assertIn(Action.BOMB, self.decide(enemy_bullets=bullets, lives=0))
        # The next run hands out fresh bombs, so the old cooldown must not hold.
        self.assertIn(Action.BOMB, self.decide(enemy_bullets=bullets, lives=2))

    def test_does_not_bomb_a_bullet_it_has_room_to_avoid(self) -> None:
        # The old rule bombed anything inside 24 px; here there is room to move.
        action = self.decide(enemy_bullets=(make_bullet(20.0, 400.0, dx=-3.0, dy=0.0),))

        self.assertNotIn(Action.BOMB, action)

    def test_steps_out_of_the_way_of_a_bullet_that_is_closing_in(self) -> None:
        # Closing in from the right, with a still bullet 20 px above and below:
        # the only way through is to the left.
        bullets = (
            make_bullet(30.0, 400.0, dx=-3.0, dy=0.0),
            make_bullet(0.0, 380.0, dx=0.0, dy=0.0),
            make_bullet(0.0, 420.0, dx=0.0, dy=0.0),
        )

        action = self.decide(enemy_bullets=bullets)

        self.assertIn(Action.LEFT, action)
        self.assertNotIn(Action.RIGHT, action)

    def test_steps_the_other_way_when_the_bullet_is_on_the_left(self) -> None:
        bullets = (
            make_bullet(-30.0, 400.0, dx=3.0, dy=0.0),
            make_bullet(0.0, 380.0, dx=0.0, dy=0.0),
            make_bullet(0.0, 420.0, dx=0.0, dy=0.0),
        )

        action = self.decide(enemy_bullets=bullets)

        self.assertIn(Action.RIGHT, action)
        self.assertNotIn(Action.LEFT, action)

    def test_leaves_a_bullet_that_is_flying_away_alone(self) -> None:
        # The old rule only saw a bullet 30 px away and jumped aside; this one
        # can see that it is leaving.
        action = self.decide(enemy_bullets=(make_bullet(30.0, 400.0, dx=3.0, dy=0.0),))

        self.assertNotIn(Action.BOMB, action)
        self.assertNotIn(Action.LEFT, action)

    def test_stands_still_in_the_gap_between_two_bullets(self) -> None:
        # 9 px above and below, just outside the hit box: every step is into one
        # of them, so standing still is the only way through.
        bullets = (make_bullet(0.0, 391.0, dx=0.0, dy=0.0), make_bullet(0.0, 409.0, dx=0.0, dy=0.0))

        action = self.decide(enemy_bullets=bullets)

        self.assertNotIn(Action.BOMB, action)
        self.assertFalse(action & (Action.LEFT | Action.RIGHT | Action.UP | Action.DOWN))

    def test_ignores_a_bullet_that_cannot_arrive_in_time(self) -> None:
        action = self.decide(enemy_bullets=(make_bullet(0.0, 100.0),))

        self.assertFalse(action & (Action.LEFT | Action.RIGHT | Action.UP | Action.DOWN))

    def test_steps_aside_for_a_bullet_that_will_arrive(self) -> None:
        # Ten frames out: inside the walk, so the move that stands still is
        # dropped and the policy has to go somewhere.
        action = self.decide(enemy_bullets=(make_bullet(0.0, 380.0, dx=0.0, dy=2.0),))

        self.assertTrue(action & (Action.LEFT | Action.RIGHT | Action.UP | Action.DOWN))

    def test_lasers_are_treated_as_hazards(self) -> None:
        action = self.decide(enemy_lasers=(make_laser(*PLAYER),))

        self.assertIn(Action.BOMB, action)

    def test_aims_at_the_nearest_enemy_when_nothing_is_near(self) -> None:
        action = self.decide(enemies=(make_enemy(150.0, 100.0),))

        self.assertIn(Action.RIGHT, action)

    def test_does_not_move_when_already_aligned_with_the_enemy(self) -> None:
        action = self.decide(enemies=(make_enemy(0.0, 100.0),))

        self.assertFalse(action & (Action.LEFT | Action.RIGHT))
        self.assertIn(Action.SHOOT, action)

    def test_moves_towards_a_resource_point(self) -> None:
        action = self.decide(resources=(Point(150.0, 300.0),))

        self.assertIn(Action.RIGHT, action)
        self.assertIn(Action.UP, action)

    def test_focus_can_be_turned_off(self) -> None:
        action = EvasivePolicy(focus=False).decide(observe(make_snapshot(player=PLAYER)))

        self.assertNotIn(Action.FOCUS, action)

    def test_rejects_a_horizon_that_sees_nothing(self) -> None:
        with self.assertRaises(ValueError):
            EvasivePolicy(horizon=0)

    def test_rejects_a_hazard_limit_below_one(self) -> None:
        with self.assertRaises(ValueError):
            EvasivePolicy(max_hazards=0)

    def test_rejects_a_negative_bomb_cooldown(self) -> None:
        with self.assertRaises(ValueError):
            EvasivePolicy(bomb_cooldown_frames=-1)


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
