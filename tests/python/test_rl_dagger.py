import random
import unittest
from unittest import mock

import numpy as np
import torch

from auto_th10 import Action
from auto_th10 import env as env_module
from fakes import FakeSession, make_bullet, make_environment, make_snapshot
from training.policy import EvasivePolicy
from training.rl import (
    ActionSample,
    ActorCritic,
    DaggerBuffer,
    EvasiveTeacher,
    MemoryGymEnv,
    ModelAction,
    ModelSpec,
    collect_dagger_rollout,
    run_dagger_iteration,
)
from training.rl import gym_env as gym_env_module


class FixedLearner:
    def __init__(self, action: ModelAction) -> None:
        self.action = action

    def act(
        self, observation: np.ndarray, *, deterministic: bool = False
    ) -> ActionSample:
        del observation, deterministic
        return ActionSample(action=self.action, log_prob=0.0, value=0.0)


class FailingLearner:
    def act(
        self, observation: np.ndarray, *, deterministic: bool = False
    ) -> ActionSample:
        del observation, deterministic
        raise RuntimeError("learner failed")


class DaggerTests(unittest.TestCase):
    def setUp(self) -> None:
        patchers = (
            mock.patch.object(env_module, "PAUSE_SAMPLE_SECONDS", 0.0),
            mock.patch("auto_th10.restart.TAP_SECONDS", 0.0),
        )
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def make_env(
        self, session: FakeSession, *, action_repeat: int = 1
    ) -> MemoryGymEnv:
        core = make_environment(session)
        with mock.patch.object(gym_env_module, "Th10Env", return_value=core):
            return MemoryGymEnv(action_repeat=action_repeat)

    def dangerous_session(self) -> FakeSession:
        snapshot = make_snapshot(
            player=(0.0, 400.0),
            enemy_bullets=(make_bullet(0.0, 400.0),),
        )
        return FakeSession(snapshots=(snapshot,))

    def test_beta_one_executes_the_teacher_but_records_both_actions(self) -> None:
        session = self.dangerous_session()
        env = self.make_env(session)
        features, _ = env.reset()
        teacher = EvasiveTeacher(EvasivePolicy(bomb_cooldown_frames=5))
        learner = FixedLearner(ModelAction(3, False))

        rollout = collect_dagger_rollout(
            env,
            learner,
            teacher,
            features,
            steps=1,
            beta=1.0,
            rng=random.Random(1),
        )

        sample = rollout.samples[0]
        self.assertTrue(sample.teacher_action.bomb)
        self.assertFalse(sample.learner_action.bomb)
        self.assertEqual(sample.executed_action, sample.teacher_action)
        self.assertTrue(session.inputs[0] & Action.BOMB)

    def test_beta_zero_leaves_control_with_the_learner(self) -> None:
        session = self.dangerous_session()
        env = self.make_env(session)
        features, _ = env.reset()
        teacher = EvasiveTeacher(EvasivePolicy(bomb_cooldown_frames=5))
        learner = FixedLearner(ModelAction(3, False))

        rollout = collect_dagger_rollout(
            env,
            learner,
            teacher,
            features,
            steps=1,
            beta=0.0,
            rng=random.Random(1),
        )

        sample = rollout.samples[0]
        self.assertEqual(sample.executed_action, sample.learner_action)
        self.assertFalse(session.inputs[0] & Action.BOMB)
        next_label = teacher.annotate(env.raw_observation)
        self.assertTrue(next_label.bomb)

    def test_buffer_oversamples_rare_bomb_labels(self) -> None:
        session = self.dangerous_session()
        env = self.make_env(session)
        features, _ = env.reset()
        teacher = EvasiveTeacher()
        rollout = collect_dagger_rollout(
            env,
            FixedLearner(ModelAction(0, False)),
            teacher,
            features,
            steps=4,
            beta=1.0,
            rng=random.Random(1),
        )
        buffer = DaggerBuffer()
        buffer.extend(rollout.samples)

        self.assertEqual(rollout.samples[0].features.dtype, np.float32)
        self.assertFalse(rollout.samples[0].features.flags.writeable)

        batch = buffer.sample(
            8, bomb_fraction=0.5, rng=random.Random(2)
        )

        self.assertEqual(batch.observations.shape[0], 8)
        self.assertEqual(int(batch.teacher_actions[:, 1].sum().item()), 4)

    def test_iteration_pauses_for_updates_and_resumes_afterwards(self) -> None:
        session = self.dangerous_session()
        env = self.make_env(session)
        features, _ = env.reset()
        model = ActorCritic(ModelSpec(features.size, hidden_sizes=(8,)))
        optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
        callback_pause_states: list[bool] = []

        result = run_dagger_iteration(
            env,
            model,
            EvasiveTeacher(),
            optimizer,
            DaggerBuffer(),
            features,
            horizon=2,
            beta=1.0,
            batch_size=2,
            update_steps=1,
            rng=random.Random(3),
            after_updates=lambda updates: callback_pause_states.append(
                session.paused
            ),
        )

        self.assertEqual(len(result.rollout.samples), 2)
        self.assertEqual(len(result.updates), 1)
        self.assertFalse(session.paused)
        self.assertEqual(callback_pause_states, [True])
        self.assertIn(Action.ESCAPE, session.inputs)
        self.assertEqual(result.next_features.shape, features.shape)

    def test_pause_boundary_frames_advance_teacher_cooldown(self) -> None:
        session = self.dangerous_session()
        env = self.make_env(session)
        features, _ = env.reset()
        teacher = EvasiveTeacher(EvasivePolicy(bomb_cooldown_frames=1))
        model = ActorCritic(ModelSpec(features.size, hidden_sizes=(8,)))
        optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)

        run_dagger_iteration(
            env,
            model,
            teacher,
            optimizer,
            DaggerBuffer(),
            features,
            horizon=1,
            beta=1.0,
            batch_size=1,
            update_steps=0,
            rng=random.Random(5),
        )
        ready = teacher.annotate(env.raw_observation)

        self.assertTrue(ready.bomb)

    def test_collection_failure_stops_the_environment(self) -> None:
        session = self.dangerous_session()
        env = self.make_env(session)
        features, _ = env.reset()

        with self.assertRaisesRegex(RuntimeError, "learner failed"):
            collect_dagger_rollout(
                env,
                FailingLearner(),
                EvasiveTeacher(),
                features,
                steps=1,
                beta=0.0,
                rng=random.Random(4),
            )

        self.assertEqual(session.inputs[-1], Action.NONE)
        with self.assertRaisesRegex(RuntimeError, "after the episode ends"):
            env.step(np.asarray((0, 0), dtype=np.int64))

    def test_iteration_rejects_an_environment_with_step_truncation(self) -> None:
        session = self.dangerous_session()
        env = self.make_env(session)
        env.max_steps = 1
        features, _ = env.reset()
        model = ActorCritic(ModelSpec(features.size, hidden_sizes=(8,)))
        optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)

        with self.assertRaisesRegex(ValueError, "max_steps truncation"):
            run_dagger_iteration(
                env,
                model,
                EvasiveTeacher(),
                optimizer,
                DaggerBuffer(),
                features,
                horizon=1,
                beta=1.0,
                batch_size=1,
                update_steps=1,
                rng=random.Random(6),
            )

        self.assertEqual(session.inputs, [])


if __name__ == "__main__":
    unittest.main()
