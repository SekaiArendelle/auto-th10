import unittest
from unittest import mock

from auto_th10 import Action, Scene
from auto_th10 import session as session_module


class SceneTests(unittest.TestCase):
    """The family word: one read, no waiting, and the same exact-string rule."""

    def test_members_carry_the_native_names(self) -> None:
        self.assertEqual(
            {member.value for member in Scene},
            {
                "TH10_SCENE_UNKNOWN",
                "TH10_SCENE_MENU",
                "TH10_SCENE_STAGE",
            },
        )

    def test_members_round_trip_from_text(self) -> None:
        for member in Scene:
            self.assertIs(Scene(member.value), member)

    def test_is_a_str_so_it_compares_with_the_raw_value(self) -> None:
        self.assertEqual(Scene.STAGE, "TH10_SCENE_STAGE")


class ActionTests(unittest.TestCase):
    def test_members_combine_as_bits(self) -> None:
        held = Action.LEFT | Action.SHOOT
        self.assertEqual(int(held & Action.LEFT), int(Action.LEFT))
        self.assertEqual(int(held & Action.SHOOT), int(Action.SHOOT))

    def test_none_is_empty(self) -> None:
        self.assertEqual(int(Action.NONE), 0)

    def test_escape_keeps_the_bit_the_c_header_assigns_it(self) -> None:
        # The mask crosses the C API, where TH10_ACTION_ESCAPE is 1u << 7: a
        # Python-side slip that renumbered the actions would send the wrong keys
        # and nothing else would notice.
        self.assertEqual(int(Action.ESCAPE), 1 << 7)

    def test_directions_and_face_buttons_are_distinct_bits(self) -> None:
        bits = [int(member) for member in Action if member is not Action.NONE]
        self.assertEqual(len(bits), len(set(bits)))
        # Every combination still lands inside the declared mask.
        combined = Action.NONE
        for member in Action:
            combined |= member
        for member in Action:
            self.assertTrue(int(combined) & int(member))


class StageFramesTests(unittest.TestCase):
    """The wrapper only forwards: the counter itself needs a running game."""

    def test_forwards_to_the_native_session(self) -> None:
        class FakeNativeSession:
            def __init__(self) -> None:
                self.calls = 0

            def stage_frames(self) -> int:
                self.calls += 1
                return 4242

        fake = FakeNativeSession()

        with mock.patch.object(session_module._native, "Session", lambda: fake):
            game = session_module.Session()
            self.assertEqual(game.stage_frames(), 4242)

        self.assertEqual(fake.calls, 1)


if __name__ == "__main__":
    unittest.main()
