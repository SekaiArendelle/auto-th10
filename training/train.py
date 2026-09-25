"""Run online DAgger updates against a game already in a stage."""

import argparse
from collections.abc import Iterable
from datetime import datetime
import itertools
import json
import math
import pathlib
import random
import statistics
import sys

from auto_th10 import NotInStage
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
    run_dagger_iteration,
    save_checkpoint,
)

DEFAULT_CHECKPOINT = pathlib.Path("runs/dagger.pt")
DEFAULT_TENSORBOARD_ROOT = pathlib.Path("runs/tensorboard")
INPUT_BACKEND = "background"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m training.train",
        description=(
            "Train an in-memory policy from EvasivePolicy labels. Start TH10 and "
            "enter a stage before running this command."
        ),
    )
    parser.add_argument(
        "--iterations",
        type=_iteration_limit,
        default=None,
        help="DAgger iterations to run; inf (the default) runs until interrupted",
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
        # Built inside the handler's reach: attaching to the game can itself be
        # interrupted, or refused with NotInStage, before the first iteration.
        env = MemoryGymEnv(feature_spec=feature_spec)
        features, _ = env.reset(seed=args.seed)
        teacher.reset()
        for iteration in _iteration_range(args.iterations):
            def finish_updates(
                rollout: DaggerRollout,
                updates: tuple[ImitationMetrics, ...],
            ) -> None:
                nonlocal episode, episode_reward, episode_frames, episode_bombs
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
                _log_rollout_metrics(writer, rollout, iteration=iteration)
                episode_reward, episode_frames, episode_bombs = (
                    _accumulate_episode(
                        rollout,
                        reward=episode_reward,
                        frames=episode_frames,
                        bombs=episode_bombs,
                    )
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
            beta = max(args.beta_min, beta * args.beta_decay)
            if result.rollout.terminated or result.rollout.truncated:
                teacher.reset()
                features, _ = env.reset()
            else:
                features = result.next_features
    except NotInStage as refused:
        print(f"training stopped: {refused}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(f"training interrupted at iteration {iteration}", file=sys.stderr)
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


def _log_rollout_metrics(
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
        "iterations": "inf" if args.iterations is None else args.iterations,
        "learning_rate": args.learning_rate,
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


def _iteration_limit(value: str) -> int | None:
    if value.lower() == "inf":
        return None
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be positive or 'inf'")
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


def _probability(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or not 0.0 <= parsed <= 1.0:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
