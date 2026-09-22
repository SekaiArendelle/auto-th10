import unittest

from auto_th10 import Action, State


class StateTests(unittest.TestCase):
    def test_members_carry_the_native_names(self) -> None:
        # The C side reports these exact strings, so a typo here would only show
        # up against a running game; pin them down instead.
        self.assertEqual(
            {member.value for member in State},
            {
                "TH10_STATE_UNKNOWN",
                "TH10_STATE_MENU",
                "TH10_STATE_PLAYING",
                "TH10_STATE_PAUSED",
                "TH10_STATE_GAME_OVER",
            },
        )

    def test_members_round_trip_from_text(self) -> None:
        for member in State:
            self.assertIs(State(member.value), member)

    def test_is_a_str_so_it_compares_with_the_raw_value(self) -> None:
        self.assertEqual(State.PLAYING, "TH10_STATE_PLAYING")
        self.assertEqual(State.GAME_OVER.value, "TH10_STATE_GAME_OVER")


class ActionTests(unittest.TestCase):
    def test_members_combine_as_bits(self) -> None:
        held = Action.LEFT | Action.SHOOT
        self.assertEqual(int(held & Action.LEFT), int(Action.LEFT))
        self.assertEqual(int(held & Action.SHOOT), int(Action.SHOOT))

    def test_none_is_empty(self) -> None:
        self.assertEqual(int(Action.NONE), 0)

    def test_directions_and_face_buttons_are_distinct_bits(self) -> None:
        bits = [int(member) for member in Action if member is not Action.NONE]
        self.assertEqual(len(bits), len(set(bits)))
        # Every combination still lands inside the declared mask.
        combined = Action.NONE
        for member in Action:
            combined |= member
        for member in Action:
            self.assertTrue(int(combined) & int(member))


if __name__ == "__main__":
    unittest.main()
