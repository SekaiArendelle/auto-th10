"""Pretrain with online DAgger, then fine-tune with on-policy PPO."""

import argparse
from collections.abc import Iterable
from datetime import datetime
import itertools
import json
import math
import os
import pathlib
import random
import statistics
import sys

import torch
from torch.utils.tensorboard import SummaryWriter

from .rl import (
    ActionSpec,
    ActorCritic,
    DaggerBuffer,
    DaggerRollout,
    EvasiveTeacher,
    FeatureSpec,
    ImitationMetrics,
    MemoryGymEnv,
    ModelSpec,
    PpoMetrics,
    PpoRollout,
    run_dagger_iteration,
    run_ppo_iteration,
    save_checkpoint,
)

DEFAULT_CHECKPOINT = pathlib.Path("runs/policy.pt")
DEFAULT_TENSORBOARD_ROOT = pathlib.Path("runs/tensorboard")
INPUT_BACKEND = "background"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m training.train",
        description=(
            "Pretrain an in-memory policy from EvasivePolicy labels, then "
            "fine-tune it with PPO. Start TH10 and enter a stage before running "
            "this command."
        ),
    )
    parser.add_argument("--dagger-iterations", type=_positive_int, default=100)
    parser.add_argument(
        "--ppo-iterations",
        type=_nonnegative_iteration_limit,
        default=None,
        help="PPO iterations to run; inf (the default) runs until interrupted",
    )
    parser.add_argument("--horizon", type=_positive_int, default=1024)
    parser.add_argument("--updates", type=_nonnegative_int, default=16)
    parser.add_argument("--batch-size", type=_positive_int, default=256)
    parser.add_argument("--buffer-capacity", type=_positive_int, default=100_000)
    parser.add_argument("--learning-rate", type=_positive_float, default=3e-4)
    parser.add_argument(
        "--beta",
        type=_probability,
        default=1.0,
        help="initial probability of executing the teacher action",
    )
    parser.add_argument(
        "--beta-decay",
        type=_probability,
        default=0.95,
        help="multiply beta by this after each rollout",
    )
    parser.add_argument(
        "--beta-min",
        type=_probability,
        default=0.05,
        help="lower bound for the teacher execution probability",
    )
    parser.add_argument("--bomb-fraction", type=_probability, default=0.25)
    parser.add_argument("--bomb-positive-weight", type=_positive_float, default=8.0)
    parser.add_argument("--ppo-epochs", type=_positive_int, default=10)
    parser.add_argument("--ppo-batch-size", type=_positive_int, default=256)
    parser.add_argument("--gamma", type=_probability, default=0.999)
    parser.add_argument("--gae-lambda", type=_probability, default=0.95)
    parser.add_argument("--clip-ratio", type=_positive_float, default=0.2)
    parser.add_argument("--value-clip", type=_positive_float, default=0.2)
    parser.add_argument("--value-coefficient", type=_nonnegative_float, default=0.5)
    parser.add_argument("--entropy-coefficient", type=_nonnegative_float, default=0.01)
    parser.add_argument("--max-grad-norm", type=_positive_float, default=0.5)
    parser.add_argument("--target-kl", type=_positive_float, default=None)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--checkpoint",
        type=pathlib.Path,
        default=DEFAULT_CHECKPOINT,
        help="checkpoint written atomically after every iteration",
    )
    parser.add_argument(
        "--tensorboard-dir",
        type=pathlib.Path,
        default=DEFAULT_TENSORBOARD_ROOT,
        help=(
            "root for a timestamped TensorBoard run directory; defaults to "
            "runs/tensorboard"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.beta_min > args.beta:
        raise SystemExit("--beta-min must not exceed --beta")

    random.seed(args.seed)
    torch.manual_seed(args.seed)
    rng = random.Random(args.seed)
    feature_spec = FeatureSpec()
    action_spec = ActionSpec()
    model_spec = ModelSpec(feature_spec.size)
    model = ActorCritic(model_spec, action_spec=action_spec)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.learning_rate)
    buffer = DaggerBuffer(args.buffer_capacity)
    teacher = EvasiveTeacher()
    beta = args.beta
    iteration = 0
    phase = "dagger"
    episode = 0
    episode_reward = 0.0
    episode_frames = 0
    episode_bombs = 0
    env: MemoryGymEnv | None = None
    run_dir = _new_run_directory(args.tensorboard_dir)
    writer = SummaryWriter(log_dir=str(run_dir))

    try:
        _write_hyperparameters(writer, args)
        print(f"TensorBoard logs: {run_dir}")
        print(f"Input backend: {INPUT_BACKEND}")
        # Built inside the cleanup scope: attaching to the game can itself be
        # interrupted or fail before the first iteration.
        env = MemoryGymEnv(feature_spec=feature_spec)
        features, _ = env.reset(seed=args.seed)
        teacher.reset()
        for _ in range(args.dagger_iterations):
            iteration += 1
            def finish_updates(
                rollout: DaggerRollout,
                updates: tuple[ImitationMetrics, ...],
            ) -> None:
                del rollout
                save_checkpoint(
                    args.checkpoint,
                    iteration=iteration,
                    beta=beta,
                    feature_spec=feature_spec,
                    action_spec=action_spec,
                    model_spec=model_spec,
                    model=model,
                    optimizer=optimizer,
                )
                _report_iteration(iteration, beta, len(buffer), updates)
                _log_update_metrics(
                    writer,
                    iteration=iteration,
                    beta=beta,
                    buffer_size=len(buffer),
                    updates=updates,
                )
                writer.flush()

            result = run_dagger_iteration(
                env,
                model,
                teacher,
                optimizer,
                buffer,
                features,
                horizon=args.horizon,
                beta=beta,
                batch_size=args.batch_size,
                update_steps=args.updates,
                bomb_fraction=args.bomb_fraction,
                bomb_positive_weight=args.bomb_positive_weight,
                rng=rng,
                after_updates=finish_updates,
            )
            rollout = result.rollout
            _log_dagger_rollout_metrics(writer, rollout, iteration=iteration)
            episode_reward, episode_frames, episode_bombs = _accumulate_episode(
                rollout,
                reward=episode_reward,
                frames=episode_frames,
                bombs=episode_bombs,
            )
            if rollout.terminated or rollout.truncated:
                _log_episode_metrics(
                    writer,
                    episode=episode,
                    reward=episode_reward,
                    frames=episode_frames,
                    score=env.raw_observation.snapshot.score,
                    bombs=episode_bombs,
                )
                episode += 1
                episode_reward = 0.0
                episode_frames = 0
                episode_bombs = 0
            beta = max(args.beta_min, beta * args.beta_decay)
            if rollout.terminated or rollout.truncated:
                teacher.reset()
                features, _ = env.reset()
            else:
                features = result.next_features
        phase = "ppo"
        pending_checkpoint = args.checkpoint.with_name(
            f".{args.checkpoint.name}.pending"
        )
        for _ in _iteration_range(args.ppo_iterations):
            iteration += 1

            def prepare_ppo_updates(
                rollout: PpoRollout,
                updates: tuple[PpoMetrics, ...],
            ) -> None:
                del rollout, updates
                # Flush the preceding iteration while the game is paused, then
                # perform all expensive serialization into a staging path. A
                # verified resume publishes it with one atomic rename.
                writer.flush()
                save_checkpoint(
                    pending_checkpoint,
                    iteration=iteration,
                    beta=0.0,
                    feature_spec=feature_spec,
                    action_spec=action_spec,
                    model_spec=model_spec,
                    model=model,
                    optimizer=optimizer,
                )

            def finish_ppo_updates(
                rollout: PpoRollout,
                updates: tuple[PpoMetrics, ...],
            ) -> None:
                del rollout
                os.replace(pending_checkpoint, args.checkpoint)
                _report_ppo_iteration(iteration, updates)
                _log_ppo_update_metrics(writer, iteration=iteration, updates=updates)

            result = run_ppo_iteration(
                env,
                model,
                optimizer,
                features,
                horizon=args.horizon,
                epochs=args.ppo_epochs,
                batch_size=args.ppo_batch_size,
                gamma=args.gamma,
                gae_lambda=args.gae_lambda,
                clip_ratio=args.clip_ratio,
                value_clip=args.value_clip,
                value_coefficient=args.value_coefficient,
                entropy_coefficient=args.entropy_coefficient,
                max_grad_norm=args.max_grad_norm,
                target_kl=args.target_kl,
                prepare_updates=prepare_ppo_updates,
                after_updates=finish_ppo_updates,
            )
            rollout = result.rollout
            _log_ppo_rollout_metrics(writer, rollout, iteration=iteration)
            episode_reward, episode_frames, episode_bombs = _accumulate_ppo_episode(
                rollout,
                reward=episode_reward,
                frames=episode_frames,
                bombs=episode_bombs,
            )
            if rollout.terminated:
                _log_episode_metrics(
                    writer,
                    episode=episode,
                    reward=episode_reward,
                    frames=episode_frames,
                    score=env.raw_observation.snapshot.score,
                    bombs=episode_bombs,
                )
                episode += 1
                episode_reward = 0.0
                episode_frames = 0
                episode_bombs = 0
                features, _ = env.reset()
            else:
                features = result.next_features
    except KeyboardInterrupt:
        print(
            f"training interrupted during {phase} at iteration {iteration}",
            file=sys.stderr,
        )
        return 130
    finally:
        try:
            if env is not None:
                env.close()
        finally:
            writer.close()
    return 0


def _report_iteration(
    iteration: int,
    beta: float,
    buffer_size: int,
    updates: tuple[ImitationMetrics, ...],
) -> None:
    losses = [update.total for update in updates]
    loss = statistics.fmean(losses) if losses else math.nan
    print(
        f"iteration {iteration}: beta {beta:.3f}, buffer {buffer_size}, "
        f"imitation loss {loss:.4f}"
    )


def _log_update_metrics(
    writer: SummaryWriter,
    *,
    iteration: int,
    beta: float,
    buffer_size: int,
    updates: tuple[ImitationMetrics, ...],
) -> None:
    writer.add_scalar("dagger/beta", beta, iteration)
    writer.add_scalar("dagger/buffer_size", buffer_size, iteration)
    if not updates:
        return
    writer.add_scalar(
        "loss/total", statistics.fmean(update.total for update in updates), iteration
    )
    writer.add_scalar(
        "loss/movement",
        statistics.fmean(update.movement for update in updates),
        iteration,
    )
    writer.add_scalar(
        "loss/bomb", statistics.fmean(update.bomb for update in updates), iteration
    )


def _log_dagger_rollout_metrics(
    writer: SummaryWriter, rollout: DaggerRollout, *, iteration: int
) -> None:
    samples = rollout.samples
    writer.add_scalar(
        "rollout/reward",
        sum(sample.reward for sample in samples) + rollout.boundary_reward,
        iteration,
    )
    writer.add_scalar("rollout/steps", len(samples), iteration)
    writer.add_scalar(
        "rollout/frames",
        sum(sample.frames for sample in samples) + rollout.boundary_frames,
        iteration,
    )
    writer.add_scalar("rollout/terminated", int(rollout.terminated), iteration)
    if not samples:
        return
    writer.add_scalar(
        "rollout/movement_agreement",
        statistics.fmean(
            sample.learner_action.movement == sample.teacher_action.movement
            for sample in samples
        ),
        iteration,
    )
    writer.add_scalar(
        "rollout/bomb_label_rate",
        statistics.fmean(bool(sample.teacher_action.bomb) for sample in samples),
        iteration,
    )
    writer.add_scalar(
        "rollout/teacher_execution_rate",
        statistics.fmean(sample.used_teacher for sample in samples),
        iteration,
    )


def _report_ppo_iteration(
    iteration: int, updates: tuple[PpoMetrics, ...]
) -> None:
    policy = statistics.fmean(update.policy for update in updates)
    value = statistics.fmean(update.value for update in updates)
    print(
        f"iteration {iteration}: PPO policy loss {policy:.4f}, "
        f"value loss {value:.4f}"
    )


def _log_ppo_update_metrics(
    writer: SummaryWriter,
    *,
    iteration: int,
    updates: tuple[PpoMetrics, ...],
) -> None:
    if not updates:
        return
    for name in (
        "total",
        "policy",
        "value",
        "entropy",
        "approximate_kl",
        "clip_fraction",
    ):
        writer.add_scalar(
            f"ppo/{name}",
            statistics.fmean(getattr(update, name) for update in updates),
            iteration,
        )


def _log_ppo_rollout_metrics(
    writer: SummaryWriter, rollout: PpoRollout, *, iteration: int
) -> None:
    writer.add_scalar(
        "ppo_rollout/reward",
        sum(sample.reward for sample in rollout.samples) + rollout.resume_reward,
        iteration,
    )
    writer.add_scalar("ppo_rollout/steps", len(rollout.samples), iteration)
    writer.add_scalar(
        "ppo_rollout/frames",
        sum(sample.frames for sample in rollout.samples) + rollout.resume_frames,
        iteration,
    )
    writer.add_scalar("ppo_rollout/terminated", int(rollout.terminated), iteration)


def _log_episode_metrics(
    writer: SummaryWriter,
    *,
    episode: int,
    reward: float,
    frames: int,
    score: int,
    bombs: int,
) -> None:
    writer.add_scalar("episode/reward", reward, episode)
    writer.add_scalar("episode/survival_frames", frames, episode)
    writer.add_scalar("episode/score", score, episode)
    writer.add_scalar("episode/bombs", bombs, episode)


def _accumulate_episode(
    rollout: DaggerRollout, *, reward: float, frames: int, bombs: int
) -> tuple[float, int, int]:
    samples = rollout.samples
    return (
        reward
        + sum(sample.reward for sample in samples)
        + rollout.boundary_reward,
        rollout.episode_frames if samples else frames,
        bombs + sum(int(sample.executed_action.bomb) for sample in samples),
    )


def _accumulate_ppo_episode(
    rollout: PpoRollout, *, reward: float, frames: int, bombs: int
) -> tuple[float, int, int]:
    return (
        reward
        + sum(sample.reward for sample in rollout.samples)
        + rollout.resume_reward,
        rollout.episode_frames if rollout.samples else frames,
        bombs + sum(int(sample.action.bomb) for sample in rollout.samples),
    )


def _new_run_directory(root: pathlib.Path) -> pathlib.Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return root / timestamp


def _write_hyperparameters(writer: SummaryWriter, args: argparse.Namespace) -> None:
    values = {
        "batch_size": args.batch_size,
        "beta": args.beta,
        "beta_decay": args.beta_decay,
        "beta_min": args.beta_min,
        "bomb_fraction": args.bomb_fraction,
        "bomb_positive_weight": args.bomb_positive_weight,
        "buffer_capacity": args.buffer_capacity,
        "checkpoint": str(args.checkpoint),
        "horizon": args.horizon,
        "input_backend": INPUT_BACKEND,
        "dagger_iterations": args.dagger_iterations,
        "learning_rate": args.learning_rate,
        "ppo_iterations": (
            "inf" if args.ppo_iterations is None else args.ppo_iterations
        ),
        "ppo_epochs": args.ppo_epochs,
        "ppo_batch_size": args.ppo_batch_size,
        "gamma": args.gamma,
        "gae_lambda": args.gae_lambda,
        "clip_ratio": args.clip_ratio,
        "value_clip": args.value_clip,
        "value_coefficient": args.value_coefficient,
        "entropy_coefficient": args.entropy_coefficient,
        "max_grad_norm": args.max_grad_norm,
        "target_kl": args.target_kl,
        "seed": args.seed,
        "updates": args.updates,
    }
    writer.add_text(
        "run/hyperparameters",
        f"```json\n{json.dumps(values, indent=2, sort_keys=True)}\n```",
        0,
    )


def _iteration_range(limit: int | None) -> Iterable[int]:
    """Number the iterations from 1; `inf` (parsed to `None`) has no last one."""
    return itertools.count(1) if limit is None else range(1, limit + 1)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _nonnegative_iteration_limit(value: str) -> int | None:
    if value.lower() == "inf":
        return None
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must not be negative or must be 'inf'")
    return parsed


def _nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise argparse.ArgumentTypeError("must be positive and finite")
    return parsed


def _nonnegative_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0.0:
        raise argparse.ArgumentTypeError("must be non-negative and finite")
    return parsed


def _probability(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or not 0.0 <= parsed <= 1.0:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
