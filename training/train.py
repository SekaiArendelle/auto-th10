"""Run online DAgger updates against a game already in a stage."""

import argparse
from collections.abc import Iterable
import itertools
import math
import pathlib
import random
import statistics
import sys

from auto_th10 import NotInStage
import torch

from .rl import (
    ActionSpec,
    ActorCritic,
    DaggerBuffer,
    EvasiveTeacher,
    FeatureSpec,
    ImitationMetrics,
    MemoryGymEnv,
    ModelSpec,
    run_dagger_iteration,
    save_checkpoint,
)

DEFAULT_CHECKPOINT = pathlib.Path("runs/dagger.pt")


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
    env: MemoryGymEnv | None = None

    try:
        # Built inside the handler's reach: attaching to the game can itself be
        # interrupted, or refused with NotInStage, before the first iteration.
        env = MemoryGymEnv(feature_spec=feature_spec)
        features, _ = env.reset(seed=args.seed)
        teacher.reset()
        for iteration in _iteration_range(args.iterations):
            def finish_updates(updates: tuple[ImitationMetrics, ...]) -> None:
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
        if env is not None:
            env.close()
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
