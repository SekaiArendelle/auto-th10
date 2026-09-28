import unittest
from unittest import mock

import numpy as np
import torch

from auto_th10 import env as env_module
from fakes import FakeSession, make_environment, make_snapshot
from training.rl import (
    ActorCritic,
    MemoryGymEnv,
    ModelAction,
    ModelSpec,
    PpoRollout,
    PpoSample,
    prepare_ppo_batch,
    run_ppo_iteration,
    update_ppo,
)
from training.rl import gym_env as gym_env_module


class PpoTests(unittest.TestCase):
    def setUp(self) -> None:
        patchers = (
            mock.patch.object(env_module, "PAUSE_SAMPLE_SECONDS", 0.0),
            mock.patch("auto_th10.restart.TAP_SECONDS", 0.0),
        )
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def make_env(self, session: FakeSession) -> MemoryGymEnv:
        core = make_environment(session)
        with mock.patch.object(gym_env_module, "Th10Env", return_value=core):
            return MemoryGymEnv()

    def test_gae_discounts_by_elapsed_game_frames_and_bootstraps(self) -> None:
        features = np.zeros(3, dtype=np.float32)
        rollout = PpoRollout(
            samples=(
                PpoSample(
                    features,
                    ModelAction(0),
                    -1.0,
                    1.0,
                    0.5,
                    2,
                    2,
                    False,
                ),
                PpoSample(
                    features,
                    ModelAction(1),
                    -2.0,
                    2.0,
                    0.25,
                    1,
                    3,
                    False,
                ),
            ),
            next_features=features,
            bootstrap_value=3.0,
            terminated=False,
            boundary_reward=0.0,
            boundary_frames=0,
            episode_frames=3,
        )

        batch = prepare_ppo_batch(rollout, gamma=0.9, gae_lambda=1.0)

        torch.testing.assert_close(
            batch.advantages, torch.tensor((1.8895, 0.95)), rtol=1e-4, atol=1e-4
        )
        torch.testing.assert_close(
            batch.returns, torch.tensor((2.8895, 2.95)), rtol=1e-4, atol=1e-4
        )

    def test_terminal_sample_does_not_bootstrap(self) -> None:
        features = np.zeros(2, dtype=np.float32)
        rollout = PpoRollout(
            samples=(
                PpoSample(
                    features,
                    ModelAction(0),
                    0.0,
                    4.0,
                    -1.0,
                    1,
                    1,
                    True,
                ),
            ),
            next_features=features,
            bootstrap_value=100.0,
            terminated=True,
            boundary_reward=0.0,
            boundary_frames=0,
            episode_frames=1,
        )

        batch = prepare_ppo_batch(rollout)

        self.assertAlmostEqual(batch.advantages.item(), -5.0)
        self.assertAlmostEqual(batch.returns.item(), -1.0)

    def test_update_trains_the_actor_and_value_head(self) -> None:
        torch.manual_seed(4)
        model = ActorCritic(ModelSpec(3, hidden_sizes=(8,)))
        observations = np.asarray(
            ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            dtype=np.float32,
        )
        samples = []
        for index, observation in enumerate(observations):
            selected = model.act(observation)
            stored = observation.copy()
            stored.flags.writeable = False
            samples.append(
                PpoSample(
                    stored,
                    selected.action,
                    selected.log_prob,
                    selected.value,
                    float(index + 1),
                    1,
                    index + 1,
                    index == 2,
                )
            )
        rollout = PpoRollout(
            samples=tuple(samples),
            next_features=observations[-1],
            bootstrap_value=0.0,
            terminated=True,
            boundary_reward=0.0,
            boundary_frames=0,
            episode_frames=3,
        )
        batch = prepare_ppo_batch(rollout)
        optimizer = torch.optim.Adam(model.parameters(), lr=1e-2)
        actor_before = model.movement_head.weight.detach().clone()
        value_before = model.value_head.weight.detach().clone()

        metrics = update_ppo(model, optimizer, batch, epochs=2, batch_size=2)

        self.assertTrue(metrics)
        self.assertFalse(torch.equal(actor_before, model.movement_head.weight))
        self.assertFalse(torch.equal(value_before, model.value_head.weight))
        self.assertTrue(all(np.isfinite(metric.total) for metric in metrics))

    def test_iteration_pauses_for_updates_and_resumes(self) -> None:
        session = FakeSession(snapshots=(make_snapshot(),))
        env = self.make_env(session)
        features, _ = env.reset()
        model = ActorCritic(ModelSpec(features.size, hidden_sizes=(8,)))
        optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
        preparation_pause_states: list[bool] = []
        finalized_pause_states: list[bool] = []

        result = run_ppo_iteration(
            env,
            model,
            optimizer,
            features,
            horizon=2,
            epochs=1,
            batch_size=2,
            prepare_updates=lambda rollout, updates: preparation_pause_states.append(
                session.paused
            ),
            after_updates=lambda rollout, updates: finalized_pause_states.append(
                session.paused
            ),
        )

        self.assertEqual(len(result.rollout.samples), 2)
        self.assertTrue(result.updates)
        self.assertEqual(preparation_pause_states, [True])
        self.assertEqual(finalized_pause_states, [False])
        self.assertFalse(session.paused)
        self.assertEqual(result.next_features.shape, features.shape)

    def test_late_terminal_resume_replaces_the_provisional_update(self) -> None:
        terminal = make_snapshot(lives=-1, game_over=True)

        class DeathOnResumeSession(FakeSession):
            def _apply(self, action: object) -> None:
                was_paused = self.paused
                super()._apply(action)
                if was_paused and not self.paused:
                    self._snapshots = [terminal]

        session = DeathOnResumeSession(snapshots=(make_snapshot(lives=0),))
        env = self.make_env(session)
        features, _ = env.reset()
        model = ActorCritic(ModelSpec(features.size, hidden_sizes=(8,)))
        optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
        prepared_rollouts: list[PpoRollout] = []
        callback_rollouts: list[PpoRollout] = []

        result = run_ppo_iteration(
            env,
            model,
            optimizer,
            features,
            horizon=1,
            epochs=1,
            batch_size=1,
            prepare_updates=lambda rollout, updates: prepared_rollouts.append(rollout),
            after_updates=lambda rollout, updates: callback_rollouts.append(rollout),
        )

        self.assertTrue(result.rollout.terminated)
        self.assertTrue(result.rollout.samples[-1].terminated)
        self.assertLess(result.rollout.samples[-1].reward, 0.0)
        self.assertEqual(len(prepared_rollouts), 2)
        self.assertFalse(prepared_rollouts[0].terminated)
        self.assertTrue(prepared_rollouts[1].terminated)
        self.assertEqual(len(callback_rollouts), 1)
        self.assertTrue(callback_rollouts[0].terminated)


if __name__ == "__main__":
    unittest.main()
