import unittest

from auto_th10 import Action
from training.rl.actions import (
    ACTION_SCHEMA_VERSION,
    MOVEMENT_ACTIONS,
    ActionSpec,
    ModelAction,
    decode_action,
    encode_action,
)


class ActionSpecTests(unittest.TestCase):
    def test_schema_has_two_heads_and_seventeen_unique_movements(self) -> None:
        spec = ActionSpec()

        self.assertEqual(spec.schema_version, ACTION_SCHEMA_VERSION)
        self.assertEqual(spec.shape, (17, 2))
        self.assertEqual(len(set(MOVEMENT_ACTIONS)), 17)
        self.assertEqual(
            MOVEMENT_ACTIONS,
            (
                Action.NONE,
                Action.UP,
                Action.UP | Action.RIGHT,
                Action.RIGHT,
                Action.DOWN | Action.RIGHT,
                Action.DOWN,
                Action.DOWN | Action.LEFT,
                Action.LEFT,
                Action.UP | Action.LEFT,
                Action.UP | Action.FOCUS,
                Action.UP | Action.RIGHT | Action.FOCUS,
                Action.RIGHT | Action.FOCUS,
                Action.DOWN | Action.RIGHT | Action.FOCUS,
                Action.DOWN | Action.FOCUS,
                Action.DOWN | Action.LEFT | Action.FOCUS,
                Action.LEFT | Action.FOCUS,
                Action.UP | Action.LEFT | Action.FOCUS,
            ),
        )

    def test_every_movement_is_a_valid_direction(self) -> None:
        for action in MOVEMENT_ACTIONS:
            self.assertFalse(action & Action.LEFT and action & Action.RIGHT)
            self.assertFalse(action & Action.UP and action & Action.DOWN)
            self.assertFalse(action & (Action.SHOOT | Action.BOMB | Action.ESCAPE))

    def test_mismatched_schema_metadata_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ActionSpec(schema_version=99)
        with self.assertRaises(TypeError):
            ActionSpec(schema_version=True)


class DecodeActionTests(unittest.TestCase):
    def test_decoding_adds_the_fixed_shoot_action(self) -> None:
        native = decode_action(ModelAction(movement=3, bomb=False))

        self.assertEqual(native, Action.RIGHT | Action.SHOOT)

    def test_bomb_and_shoot_are_independent_of_movement(self) -> None:
        native = decode_action(ModelAction(movement=0, bomb=1), shoot=False)

        self.assertEqual(native, Action.BOMB)

    def test_invalid_head_values_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            decode_action(ModelAction(movement=-1))
        with self.assertRaises(ValueError):
            decode_action(ModelAction(movement=17))
        with self.assertRaises(ValueError):
            decode_action(ModelAction(movement=0, bomb=2))
        with self.assertRaises(TypeError):
            decode_action(ModelAction(movement=1.5))
        with self.assertRaises(TypeError):
            decode_action(ModelAction(movement=True))


class EncodeActionTests(unittest.TestCase):
    def test_canonical_actions_round_trip(self) -> None:
        for index, movement in enumerate(MOVEMENT_ACTIONS):
            for bomb in (False, True):
                native = movement | Action.SHOOT
                if bomb:
                    native |= Action.BOMB

                encoded = encode_action(native)

                self.assertEqual(encoded, ModelAction(index, bomb))
                self.assertEqual(decode_action(encoded), native)

    def test_focus_while_idle_is_canonicalized_to_idle(self) -> None:
        self.assertEqual(encode_action(Action.FOCUS | Action.SHOOT), ModelAction(0, False))

    def test_invalid_native_combinations_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            encode_action(Action.LEFT | Action.RIGHT)
        with self.assertRaises(ValueError):
            encode_action(Action.UP | Action.DOWN)
        with self.assertRaises(ValueError):
            encode_action(Action.ESCAPE)
        with self.assertRaises(TypeError):
            encode_action(True)


if __name__ == "__main__":
    unittest.main()
