import math
import unittest

from auto_th10 import Action, Point
from fakes import make_bullet, make_enemy, make_laser, make_snapshot
from training import dodging


class BoxesFromTests(unittest.TestCase):
    def test_a_bullet_carries_its_box_and_its_velocity(self) -> None:
        snapshot = make_snapshot(player=(0.0, 400.0), enemy_bullets=(make_bullet(20.0, 400.0, dx=-3.0, dy=0.0),))

        (box,) = dodging.boxes_from(snapshot)

        self.assertEqual(box, dodging.box(20.0, 400.0, 4.0, 4.0, dx=-3.0, dy=0.0))

    def test_the_lead_scales_the_velocity(self) -> None:
        snapshot = make_snapshot(player=(0.0, 400.0), enemy_bullets=(make_bullet(20.0, 400.0, dx=-4.0, dy=0.0),))

        (box,) = dodging.boxes_from(snapshot, lead=0.5)

        self.assertEqual(box[4], -2.0)

    def test_a_bullet_that_cannot_reach_is_dropped(self) -> None:
        snapshot = make_snapshot(player=(0.0, 400.0), enemy_bullets=(make_bullet(0.0, 0.0),))

        self.assertEqual(dodging.boxes_from(snapshot), ())

    def test_only_the_nearest_hazards_are_kept(self) -> None:
        bullets = tuple(make_bullet(float(x), 400.0) for x in (-10, -5, 0, 5, 10))
        snapshot = make_snapshot(player=(0.0, 400.0), enemy_bullets=bullets)

        boxes = dodging.boxes_from(snapshot, limit=3)

        self.assertEqual([box[0] for box in boxes], [0.0, -5.0, 5.0])

    def test_an_enemy_is_a_hazard_of_its_own_size(self) -> None:
        snapshot = make_snapshot(player=(0.0, 400.0), enemies=(make_enemy(10.0, 400.0, size=48.0),))

        (box,) = dodging.boxes_from(snapshot)

        self.assertEqual(box, dodging.box(10.0, 400.0, 24.0, 24.0))

    def test_a_laser_is_boxed_however_far_away_it_is(self) -> None:
        # A laser box is long and thin, so its centre says nothing about whether
        # it covers the player: it must not be dropped by distance.
        snapshot = make_snapshot(player=(0.0, 400.0), enemy_lasers=(make_laser(0.0, 0.0),))

        boxes = dodging.boxes_from(snapshot)

        self.assertEqual(len(boxes), 1)

    def test_a_laser_survives_the_limit_that_thins_the_bullets(self) -> None:
        snapshot = make_snapshot(
            player=(0.0, 400.0),
            enemy_bullets=(make_bullet(6.0, 400.0, dx=0.0, dy=0.0),),
            enemy_lasers=(make_laser(0.0, 0.0),),
        )

        boxes = dodging.boxes_from(snapshot, limit=1)

        self.assertEqual(len(boxes), 2)


class LaserBoxTests(unittest.TestCase):
    def test_a_straight_laser_is_boxed_around_its_rotated_rectangle(self) -> None:
        laser = make_laser(0.0, 0.0, size=8.0, radian=0.0)

        box = dodging.laser_box(laser)

        # A quarter of the width either side of the origin and the height
        # downwards makes a 4 px wide, 8 px tall rectangle; the quarter turn
        # stands it up, so the box around it is 8 px wide and 4 px tall.
        self.assertAlmostEqual(box[0], 4.0)
        self.assertAlmostEqual(box[1], 0.0)
        self.assertAlmostEqual(box[2], 4.0)
        self.assertAlmostEqual(box[3], 2.0)

    def test_a_laser_at_a_right_angle_is_left_alone(self) -> None:
        # The arc offset cancels a quarter turn, so this one is not turned at
        # all: 4 px wide and 8 px tall stays that way.
        laser = make_laser(0.0, 0.0, size=8.0, radian=math.pi / 2.0)

        box = dodging.laser_box(laser)

        self.assertAlmostEqual(box[0], 0.0)
        self.assertAlmostEqual(box[1], 4.0)
        self.assertAlmostEqual(box[2], 2.0)
        self.assertAlmostEqual(box[3], 4.0)


class SurvivalTests(unittest.TestCase):
    def test_a_clear_move_survives_the_whole_horizon(self) -> None:
        frames, end_x, end_y = dodging.survival(0.0, 400.0, 2.0, 0.0, (), horizon=5)

        self.assertEqual(frames, 6)
        self.assertEqual((end_x, end_y), (10.0, 400.0))

    def test_a_hazard_on_the_player_hits_on_the_first_frame(self) -> None:
        bullet = dodging.box(0.0, 400.0, 4.0, 4.0)

        frames, _, _ = dodging.survival(0.0, 400.0, 0.0, 0.0, (bullet,), horizon=5)

        self.assertEqual(frames, 1)

    def test_a_bullet_is_extrapolated_by_its_velocity(self) -> None:
        bullet = dodging.box(0.0, 200.0, 4.0, 4.0, dy=10.0)

        frames, _, _ = dodging.survival(0.0, 400.0, 0.0, 0.0, (bullet,), horizon=25)

        self.assertEqual(frames, 20)

    def test_a_move_into_the_wall_slides_along_it(self) -> None:
        frames, end_x, end_y = dodging.survival(200.0, 400.0, 4.5, 0.0, (), horizon=3)

        self.assertEqual(frames, 4)
        self.assertEqual((end_x, end_y), (200.0, 400.0))

    def test_the_margin_is_what_turns_a_near_miss_into_a_hit(self) -> None:
        bullet = dodging.box(4.0, 400.0, 1.0, 1.0)

        hit, _, _ = dodging.survival(0.0, 400.0, 0.0, 0.0, (bullet,), horizon=1)
        miss, _, _ = dodging.survival(0.0, 400.0, 0.0, 0.0, (bullet,), horizon=1, margin=0.0)

        self.assertEqual(hit, 1)
        self.assertEqual(miss, 2)


class ValueTests(unittest.TestCase):
    def test_the_bottom_middle_is_the_best_spot_on_an_empty_field(self) -> None:
        best = dodging.position_value(0.0, 390.0)

        self.assertGreater(best, dodging.position_value(0.0, 400.0))
        self.assertGreater(best, dodging.position_value(0.0, 100.0))
        self.assertGreater(best, dodging.position_value(150.0, 390.0))
        self.assertGreater(dodging.position_value(0.0, 150.0), dodging.position_value(0.0, 20.0))

    def test_the_field_does_not_run_out_of_room(self) -> None:
        self.assertGreaterEqual(dodging.position_value(200.0, 480.0), 0.0)

    def test_the_very_bottom_edge_is_not_the_best_spot(self) -> None:
        self.assertGreater(dodging.position_value(0.0, 390.0), dodging.position_value(0.0, 480.0))

    def test_aiming_scores_the_enemy_the_player_is_under(self) -> None:
        enemy = make_enemy(0.0, 100.0)

        self.assertGreater(dodging.aim_value(0.0, (enemy,)), dodging.aim_value(100.0, (enemy,)))
        self.assertEqual(dodging.aim_value(0.0, ()), 0.0)

    def test_a_resource_point_pulls_harder_the_closer_it_is(self) -> None:
        point = Point(0.0, 300.0)

        self.assertGreater(
            dodging.resource_value(0.0, 300.0, (point,)),
            dodging.resource_value(0.0, 400.0, (point,)),
        )
        self.assertEqual(dodging.resource_value(0.0, 400.0, ()), 0.0)

    def test_standing_under_an_enemy_that_is_still_firing_costs_the_most(self) -> None:
        enemy = make_enemy(0.0, 100.0)

        under = dodging.threat_value(0.0, 50.0, (enemy,))
        beside = dodging.threat_value(100.0, 150.0, (enemy,))
        below = dodging.threat_value(0.0, 350.0, (enemy,))

        self.assertLess(under, beside)
        self.assertLess(under, below)

    def test_an_enemy_past_the_firing_line_only_counts_as_a_body(self) -> None:
        enemy = make_enemy(0.0, 300.0)

        value = dodging.threat_value(0.0, 400.0, (enemy,))

        # 100 px away: inside the close range, and not under it.
        self.assertAlmostEqual(value, -(1.0 - 10000.0 / dodging.THREAT_RANGE_SQR))


class AttackValueTests(unittest.TestCase):
    def test_a_bullet_flying_at_the_spot_is_the_worst_case(self) -> None:
        # 40 px up and closing: inside the band.
        boxes = (dodging.box(0.0, 360.0, 3.0, 3.0, dy=2.0),)

        self.assertAlmostEqual(dodging.attack_value(0.0, 400.0, boxes), -2.0)

    def test_a_bullet_flying_away_does_not_count(self) -> None:
        boxes = (dodging.box(0.0, 360.0, 3.0, 3.0, dy=-2.0),)

        self.assertAlmostEqual(dodging.attack_value(0.0, 400.0, boxes), 0.0)

    def test_the_worst_bullet_decides_rather_than_the_average(self) -> None:
        # One coming for the player and two flying away: the mean would be
        # -0.67, which is the dilution that buries this term in a heavy stage.
        boxes = (
            dodging.box(0.0, 360.0, 3.0, 3.0, dy=2.0),
            dodging.box(0.0, 360.0, 3.0, 3.0, dy=-2.0),
            dodging.box(0.0, 360.0, 3.0, 3.0, dy=-2.0),
        )

        self.assertAlmostEqual(dodging.attack_value(0.0, 400.0, boxes), -2.0)

    def test_a_bullet_that_is_not_moving_has_no_course_to_avoid(self) -> None:
        boxes = (dodging.box(0.0, 360.0, 3.0, 3.0),)

        self.assertEqual(dodging.attack_value(0.0, 400.0, boxes), 0.0)

    def test_a_bullet_outside_the_band_is_ignored(self) -> None:
        boxes = (dodging.box(0.0, 300.0, 3.0, 3.0, dy=2.0),)

        self.assertEqual(dodging.attack_value(0.0, 400.0, boxes), 0.0)


class MovesTests(unittest.TestCase):
    def test_offers_both_speeds_and_standing_still(self) -> None:
        offered = dodging.moves()

        self.assertEqual(len(offered), 17)
        self.assertIn((Action.FOCUS, 0.0, 0.0), offered)

    def test_without_focus_only_the_full_speed_is_offered(self) -> None:
        offered = dodging.moves(focus=False)

        self.assertEqual(len(offered), 9)
        self.assertTrue(all(not (action & Action.FOCUS) for action, _, _ in offered))

    def test_a_diagonal_covers_the_same_distance_as_a_straight_step(self) -> None:
        for action, step_x, step_y in dodging.moves():
            if action & (Action.LEFT | Action.RIGHT) and action & (Action.UP | Action.DOWN):
                with self.subTest(action=action):
                    expected = dodging.FOCUS_SPEED if action & Action.FOCUS else dodging.NORMAL_SPEED
                    self.assertAlmostEqual(math.hypot(step_x, step_y), expected)


if __name__ == "__main__":
    unittest.main()
