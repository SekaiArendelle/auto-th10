import unittest
from unittest import mock

try:
    import gymnasium
    import numpy as np
except ModuleNotFoundError:
    gymnasium = None
    np = None

from auto_th10 import Action, Observation, Th10Env
from auto_th10 import env as env_module
from fakes import FakeSession, make_snapshot, observe

if gymnasium is not None:
    from training.rl.gym_env import MemoryGymEnv
else:
    MemoryGymEnv = None


@unittest.skipIf(gymnasium is None, "the optional training dependencies are not installed")
class MemoryGymEnvTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(env_module, "PAUSE_SAMPLE_SECONDS", 0.0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_env(
        self,
        session: FakeSession,
        *,
        action_repeat: int = 1,
        max_steps: int | None = None,
    ) -> MemoryGymEnv:
        core = Th10Env(session=session, require_stage=False)
        return MemoryGymEnv(
            env=core, action_repeat=action_repeat, max_steps=max_steps
        )

    def test_reset_returns_a_bounded_float32_observation(self) -> None:
        env = self.make_env(FakeSession(snapshots=(make_snapshot(score=100),)))

        observation, info = env.reset(seed=7)

        self.assertEqual(observation.dtype, np.float32)
        self.assertTrue(env.observation_space.contains(observation))
        self.assertEqual(env.action_space.nvec.tolist(), [17, 2])
        self.assertEqual(info["score"], 100)
        self.assertEqual(info["steps"], 0)

    def test_step_decodes_heads_and_pulses_shoot_on_a_quiet_field(self) -> None:
        session = FakeSession(snapshots=(make_snapshot(), make_snapshot(), make_snapshot()))
        env = self.make_env(session)
        env.reset()

        _, _, _, _, first_info = env.step(np.asarray([3, 1], dtype=np.int64))
        _, _, _, _, second_info = env.step(np.asarray([3, 0], dtype=np.int64))

        self.assertEqual(
            first_info["native_action"], int(Action.RIGHT | Action.SHOOT | Action.BOMB)
        )
        self.assertEqual(second_info["native_action"], int(Action.RIGHT))
        self.assertEqual(session.inputs[0], Action.RIGHT | Action.SHOOT | Action.BOMB)
        self.assertEqual(session.inputs[1], Action.RIGHT | Action.SHOOT)
        self.assertEqual(session.inputs[2], Action.RIGHT)

    def test_consecutive_bomb_decisions_have_separate_key_presses(self) -> None:
        session = FakeSession(snapshots=(make_snapshot(), make_snapshot(), make_snapshot()))
        env = self.make_env(session)
        env.reset()

        first = env.step(np.asarray([0, 1], dtype=np.int64))
        second = env.step(np.asarray([0, 1], dtype=np.int64))

        self.assertAlmostEqual(first[1], -0.099)
        self.assertAlmostEqual(second[1], -0.099)
        self.assertTrue(session.inputs[0] & Action.BOMB)
        self.assertFalse(session.inputs[1] & Action.BOMB)
        self.assertTrue(session.inputs[2] & Action.BOMB)
        self.assertFalse(session.inputs[3] & Action.BOMB)

    def test_action_repeat_accumulates_frames_but_penalizes_a_bomb_once(self) -> None:
        session = FakeSession(
            snapshots=(
                make_snapshot(score=0),
                make_snapshot(score=100),
                make_snapshot(score=300),
            ),
            frame_step=2,
        )
        env = self.make_env(session, action_repeat=2)
        env.reset()

        _, reward, terminated, truncated, info = env.step(
            np.asarray([0, 1], dtype=np.int64)
        )

        self.assertFalse(terminated)
        self.assertFalse(truncated)
        self.assertEqual(info["delta_frames"], 4)
        self.assertAlmostEqual(info["reward/score"], 0.3)
        self.assertAlmostEqual(info["reward/survival"], 0.004)
        self.assertAlmostEqual(info["reward/bomb"], -0.1)
        self.assertAlmostEqual(reward, 0.204)
        self.assertTrue(session.inputs[-2] & Action.BOMB)
        self.assertFalse(session.inputs[-1] & Action.BOMB)

    def test_core_truncation_stops_repeating_and_is_propagated(self) -> None:
        class TruncatingCore:
            def __init__(self) -> None:
                self.session = FakeSession()
                self.frames = 0
                self.calls = 0

            def reset(self) -> tuple[Observation, dict[str, object]]:
                return observe(make_snapshot()), {}

            def step(
                self, action: object
            ) -> tuple[Observation, float, bool, bool, dict[str, object]]:
                self.calls += 1
                self.frames += 1
                self.session.set_input(action)
                return observe(make_snapshot()), 0.0, False, True, {}

            def close(self) -> None:
                self.session.close()

        core = TruncatingCore()
        env = MemoryGymEnv(env=core, action_repeat=3)
        env.reset()

        _, _, terminated, truncated, _ = env.step(
            np.asarray([0, 0], dtype=np.int64)
        )

        self.assertFalse(terminated)
        self.assertTrue(truncated)
        self.assertEqual(core.calls, 1)
        self.assertEqual(core.session.inputs[-1], Action.NONE)

    def test_game_over_terminates_and_releases_input(self) -> None:
        session = FakeSession(
            snapshots=(make_snapshot(lives=0), make_snapshot(lives=-1, game_over=True))
        )
        env = self.make_env(session, action_repeat=3)
        env.reset()

        _, reward, terminated, truncated, info = env.step(
            np.asarray([0, 0], dtype=np.int64)
        )

        self.assertTrue(terminated)
        self.assertFalse(truncated)
        self.assertEqual(reward, -2.0)
        self.assertEqual(info["reward/game_over"], -1.0)
        self.assertEqual(session.inputs[-1], Action.NONE)

    def test_step_limit_truncates_and_releases_input(self) -> None:
        session = FakeSession()
        env = self.make_env(session, max_steps=1)
        env.reset()

        _, _, terminated, truncated, _ = env.step(
            np.asarray([0, 0], dtype=np.int64)
        )

        self.assertFalse(terminated)
        self.assertTrue(truncated)
        self.assertEqual(session.inputs[-1], Action.NONE)

        with self.assertRaisesRegex(RuntimeError, "after the episode ends"):
            env.step(np.asarray([0, 0], dtype=np.int64))
        self.assertEqual(session.inputs[-1], Action.NONE)

    def test_invalid_action_is_rejected_before_input(self) -> None:
        session = FakeSession()
        env = self.make_env(session)
        env.reset()

        with self.assertRaises(ValueError):
            env.step(np.asarray([17, 0], dtype=np.int64))

        self.assertEqual(session.inputs, [])

    def test_close_delegates_to_the_core_environment(self) -> None:
        session = FakeSession()
        env = self.make_env(session)
        env.reset()
        env.step(np.asarray([3, 0], dtype=np.int64))

        env.close()

        self.assertTrue(session.closed)
        self.assertEqual(session.inputs[-1], Action.NONE)


if __name__ == "__main__":
    unittest.main()
