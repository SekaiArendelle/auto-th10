"""Pretrain with online DAgger, then fine-tune with on-policy PPO."""

import argparse
from collections.abc import Iterable, Mapping
from datetime import datetime
import itertools
import json
import math
import os
import pathlib
import random
import shutil
import statistics
import sys

import torch
from torch.utils.tensorboard import SummaryWriter

from .rl import (
    ActionSpec,
    ActorCritic,
    CheckpointError,
    DaggerBuffer,
    DaggerRollout,
    EvasiveTeacher,
    FeatureSpec,
    ImitationMetrics,
    MemoryGymEnv,
    ModelSpec,
    PpoMetrics,
    PpoRollout,
    load_checkpoint,
    run_dagger_iteration,
    run_ppo_iteration,
    save_checkpoint,
)

DEFAULT_CHECKPOINT_DIR = pathlib.Path("runs/policy")
DEFAULT_CHECKPOINT = DEFAULT_CHECKPOINT_DIR / "latest.pt"
DEFAULT_CHECKPOINT_EVERY = 25
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
        "--checkpoint-dir",
        type=pathlib.Path,
        default=DEFAULT_CHECKPOINT_DIR,
        help="directory containing latest.pt and periodic checkpoint snapshots",
    )
    parser.add_argument(
        "--checkpoint-every",
        type=_nonnegative_int,
        default=DEFAULT_CHECKPOINT_EVERY,
        help="snapshot interval in global iterations; 0 disables periodic snapshots",
    )
    parser.add_argument(
        "--resume",
        nargs="?",
        const=pathlib.Path("latest.pt"),
        type=pathlib.Path,
        default=None,
        metavar="CHECKPOINT",
        help=(
            "resume complete training state from latest.pt, or branch from an "
            "explicit checkpoint path into a new --checkpoint-dir"
        ),
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
    raw_argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(raw_argv)
    schedule_overrides = {
        argument.split("=", 1)[0]
        for argument in raw_argv
        if argument.startswith("--")
    }
    latest_checkpoint = args.checkpoint_dir / "latest.pt"
    try:
        resume_checkpoint = _resume_checkpoint_path(
            args.checkpoint_dir, args.resume
        )
        if resume_checkpoint is None:
            _require_empty_checkpoint_dir(args.checkpoint_dir)
            checkpoint = None
        else:
            same_directory = (
                resume_checkpoint.parent.resolve()
                == args.checkpoint_dir.resolve()
            )
            if same_directory and resume_checkpoint.name != "latest.pt":
                raise ValueError(
                    "a historical checkpoint must resume into a new "
                    "--checkpoint-dir"
                )
            if not same_directory:
                _require_empty_checkpoint_dir(args.checkpoint_dir)
            checkpoint = load_checkpoint(resume_checkpoint)
        if checkpoint is not None:
            state = _restore_training_state(
                args, checkpoint, schedule_overrides=schedule_overrides
            )
            if same_directory and resume_checkpoint.name == "latest.pt":
                _recover_staged_snapshot(
                    args.checkpoint_dir,
                    iteration=state["iteration"],
                    dagger_completed=state["dagger_completed"],
                    ppo_completed=state["ppo_completed"],
                )
    except (CheckpointError, OSError, ValueError, TypeError) as error:
        print(f"training was not resumed: {error}", file=sys.stderr)
        return 1

    if checkpoint is None:
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
        beta = args.beta
        iteration = 0
        dagger_completed = 0
        ppo_completed = 0
        phase = "dagger"
    else:
        feature_spec = checkpoint.feature_spec
        action_spec = checkpoint.action_spec
        model_spec = checkpoint.model_spec
        model = checkpoint.model
        model.train()
        try:
            optimizer = torch.optim.Adam(model.parameters(), lr=args.learning_rate)
            optimizer.load_state_dict(checkpoint.optimizer_state_dict)
            buffer = DaggerBuffer.from_state_dict(
                state["buffer"],
                feature_size=feature_spec.size,
                movement_choices=action_spec.movement_choices,
            )
            if buffer.capacity != args.buffer_capacity:
                raise ValueError(
                    "DAgger buffer capacity disagrees with the saved config"
                )
            rng = random.Random()
            random.setstate(state["python_rng"])
            rng.setstate(state["dagger_rng"])
            torch.set_rng_state(state["torch_rng"])
        except (ValueError, TypeError, RuntimeError) as error:
            print(f"training was not resumed: invalid training state: {error}", file=sys.stderr)
            return 1
        beta = state["beta"]
        iteration = state["iteration"]
        dagger_completed = state["dagger_completed"]
        ppo_completed = state["ppo_completed"]
        phase = state["phase"]

    teacher = EvasiveTeacher()
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
        pending_checkpoint = args.checkpoint_dir / ".latest.pt.pending"
        dagger_remaining = (
            0 if phase == "ppo" else args.dagger_iterations - dagger_completed
        )
        for _ in range(dagger_remaining):
            iteration += 1
            checkpoint_staged = False
            staged_snapshot: pathlib.Path | None = None

            def finish_updates(
                rollout: DaggerRollout,
                updates: tuple[ImitationMetrics, ...],
            ) -> None:
                nonlocal checkpoint_staged, staged_snapshot
                del rollout
                next_beta = max(args.beta_min, beta * args.beta_decay)
                next_dagger_completed = dagger_completed + 1
                staged_snapshot = _snapshot_path(
                    args,
                    iteration=iteration,
                    dagger_completed=next_dagger_completed,
                    ppo_completed=ppo_completed,
                    phase_transition=(
                        next_dagger_completed >= args.dagger_iterations
                    ),
                    training_complete=(
                        next_dagger_completed >= args.dagger_iterations
                        and args.ppo_iterations == 0
                    ),
                )
                save_checkpoint(
                    pending_checkpoint,
                    iteration=iteration,
                    beta=next_beta,
                    feature_spec=feature_spec,
                    action_spec=action_spec,
                    model_spec=model_spec,
                    model=model,
                    optimizer=optimizer,
                    training_state=_training_state(
                        args,
                        phase=(
                            "ppo"
                            if next_dagger_completed >= args.dagger_iterations
                            else "dagger"
                        ),
                        iteration=iteration,
                        dagger_completed=next_dagger_completed,
                        ppo_completed=ppo_completed,
                        beta=next_beta,
                        buffer=buffer,
                        feature_size=feature_spec.size,
                        rng=rng,
                    ),
                )
                _stage_checkpoint_snapshot(
                    pending_checkpoint, staged_snapshot
                )
                checkpoint_staged = True
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
            if checkpoint_staged:
                _publish_checkpoint(
                    pending_checkpoint,
                    latest_checkpoint,
                    snapshot=staged_snapshot,
                )
            dagger_completed += 1
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
        for _ in _remaining_iteration_range(ppo_completed, args.ppo_iterations):
            iteration += 1
            staged_snapshot: pathlib.Path | None = None

            def prepare_ppo_updates(
                rollout: PpoRollout,
                updates: tuple[PpoMetrics, ...],
            ) -> None:
                nonlocal staged_snapshot
                del rollout, updates
                # Flush the preceding iteration while the game is paused, then
                # perform all expensive serialization into a staging path. A
                # verified resume publishes it with one atomic rename.
                writer.flush()
                next_ppo_completed = ppo_completed + 1
                staged_snapshot = _snapshot_path(
                    args,
                    iteration=iteration,
                    dagger_completed=dagger_completed,
                    ppo_completed=next_ppo_completed,
                    phase_transition=False,
                    training_complete=(
                        args.ppo_iterations is not None
                        and next_ppo_completed >= args.ppo_iterations
                    ),
                )
                save_checkpoint(
                    pending_checkpoint,
                    iteration=iteration,
                    beta=beta,
                    feature_spec=feature_spec,
                    action_spec=action_spec,
                    model_spec=model_spec,
                    model=model,
                    optimizer=optimizer,
                    training_state=_training_state(
                        args,
                        phase="ppo",
                        iteration=iteration,
                        dagger_completed=dagger_completed,
                        ppo_completed=ppo_completed + 1,
                        beta=beta,
                        buffer=buffer,
                        feature_size=feature_spec.size,
                        rng=rng,
                    ),
                )
                _stage_checkpoint_snapshot(
                    pending_checkpoint, staged_snapshot
                )

            def finish_ppo_updates(
                rollout: PpoRollout,
                updates: tuple[PpoMetrics, ...],
            ) -> None:
                del rollout
                _publish_checkpoint(
                    pending_checkpoint,
                    latest_checkpoint,
                    snapshot=staged_snapshot,
                )
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
            ppo_completed += 1
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


def _resume_checkpoint_path(
    checkpoint_dir: pathlib.Path, resume: pathlib.Path | None
) -> pathlib.Path | None:
    if resume is None:
        return None
    return checkpoint_dir / resume if resume.parent == pathlib.Path(".") else resume


def _require_empty_checkpoint_dir(checkpoint_dir: pathlib.Path) -> None:
    if checkpoint_dir.is_file():
        raise ValueError(f"checkpoint directory is a file: {checkpoint_dir}")
    if checkpoint_dir.exists() and any(checkpoint_dir.glob("*.pt")):
        raise ValueError(
            f"checkpoint directory already contains checkpoints: {checkpoint_dir}; "
            "use --resume or choose a new directory"
        )


def _snapshot_path(
    args: argparse.Namespace,
    *,
    iteration: int,
    dagger_completed: int,
    ppo_completed: int,
    phase_transition: bool,
    training_complete: bool,
) -> pathlib.Path | None:
    periodic = (
        args.checkpoint_every > 0 and iteration % args.checkpoint_every == 0
    )
    if not periodic and not phase_transition and not training_complete:
        return None
    return args.checkpoint_dir / _snapshot_name(
        iteration=iteration,
        dagger_completed=dagger_completed,
        ppo_completed=ppo_completed,
    )


def _snapshot_name(
    *, iteration: int, dagger_completed: int, ppo_completed: int
) -> str:
    return (
        f"iteration-{iteration:08d}-dagger-{dagger_completed:08d}-"
        f"ppo-{ppo_completed:08d}.pt"
    )


def _publish_checkpoint(
    pending: pathlib.Path,
    latest: pathlib.Path,
    *,
    snapshot: pathlib.Path | None,
) -> None:
    """Publish one verified checkpoint and its already-staged immutable copy."""
    snapshot_pending: pathlib.Path | None = None
    if snapshot is not None:
        if snapshot.exists():
            raise FileExistsError(f"checkpoint snapshot already exists: {snapshot}")
        snapshot_pending = _snapshot_pending_path(snapshot)
    # latest.pt is the recovery source of truth, so publish it first. A crash
    # before the optional second rename may omit history but cannot roll back
    # the resumable state.
    os.replace(pending, latest)
    if snapshot is not None and snapshot_pending is not None:
        # The project is Windows-only. Unlike replace(), rename() refuses an
        # existing destination there, preserving immutable snapshots.
        os.rename(snapshot_pending, snapshot)


def _stage_checkpoint_snapshot(
    pending: pathlib.Path, snapshot: pathlib.Path | None
) -> None:
    if snapshot is None:
        return
    if snapshot.exists():
        raise FileExistsError(f"checkpoint snapshot already exists: {snapshot}")
    shutil.copyfile(pending, _snapshot_pending_path(snapshot))


def _snapshot_pending_path(snapshot: pathlib.Path) -> pathlib.Path:
    return snapshot.with_name(f".{snapshot.name}.pending")


def _recover_staged_snapshot(
    checkpoint_dir: pathlib.Path,
    *,
    iteration: int,
    dagger_completed: int,
    ppo_completed: int,
) -> None:
    snapshot = checkpoint_dir / _snapshot_name(
        iteration=iteration,
        dagger_completed=dagger_completed,
        ppo_completed=ppo_completed,
    )
    pending = _snapshot_pending_path(snapshot)
    if pending.exists() and not snapshot.exists():
        os.rename(pending, snapshot)


def _write_hyperparameters(writer: SummaryWriter, args: argparse.Namespace) -> None:
    values = {
        "batch_size": args.batch_size,
        "beta": args.beta,
        "beta_decay": args.beta_decay,
        "beta_min": args.beta_min,
        "bomb_fraction": args.bomb_fraction,
        "bomb_positive_weight": args.bomb_positive_weight,
        "buffer_capacity": args.buffer_capacity,
        "checkpoint_dir": str(args.checkpoint_dir),
        "checkpoint_every": args.checkpoint_every,
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


def _remaining_iteration_range(completed: int, limit: int | None) -> Iterable[int]:
    """Yield the unfinished ordinal numbers from a finite or unbounded phase."""
    if limit is not None and completed > limit:
        raise ValueError(
            f"checkpoint has completed {completed} PPO iterations, "
            f"more than the requested limit {limit}"
        )
    return (
        itertools.count(completed + 1)
        if limit is None
        else range(completed + 1, limit + 1)
    )


_TRAINING_CONFIG_KEYS = (
    "dagger_iterations",
    "ppo_iterations",
    "horizon",
    "updates",
    "batch_size",
    "buffer_capacity",
    "learning_rate",
    "beta",
    "beta_decay",
    "beta_min",
    "bomb_fraction",
    "bomb_positive_weight",
    "ppo_epochs",
    "ppo_batch_size",
    "gamma",
    "gae_lambda",
    "clip_ratio",
    "value_clip",
    "value_coefficient",
    "entropy_coefficient",
    "max_grad_norm",
    "target_kl",
    "seed",
)


def _training_state(
    args: argparse.Namespace,
    *,
    phase: str,
    iteration: int,
    dagger_completed: int,
    ppo_completed: int,
    beta: float,
    buffer: DaggerBuffer,
    feature_size: int,
    rng: random.Random,
) -> dict[str, object]:
    return {
        "state_version": 1,
        "phase": phase,
        "iteration": iteration,
        "dagger_completed": dagger_completed,
        "ppo_completed": ppo_completed,
        "beta": beta,
        "config": {name: getattr(args, name) for name in _TRAINING_CONFIG_KEYS},
        "buffer": buffer.state_dict(feature_size=feature_size),
        "python_rng": random.getstate(),
        "dagger_rng": rng.getstate(),
        "torch_rng": torch.get_rng_state(),
    }


def _restore_training_state(
    args: argparse.Namespace,
    checkpoint: object,
    *,
    schedule_overrides: set[str],
) -> dict[str, object]:
    raw = getattr(checkpoint, "training_state", None)
    if raw is None:
        raise CheckpointError(
            "checkpoint has no resumable training state; it can only be evaluated"
        )
    if not isinstance(raw, Mapping):
        raise CheckpointError("training_state must be a mapping")
    if raw.get("state_version") != 1:
        raise CheckpointError("unsupported training state version")
    phase = raw.get("phase")
    if phase not in ("dagger", "ppo"):
        raise CheckpointError("training phase must be dagger or ppo")
    iteration = _state_nonnegative_integer(raw.get("iteration"), "iteration")
    dagger_completed = _state_nonnegative_integer(
        raw.get("dagger_completed"), "dagger_completed"
    )
    ppo_completed = _state_nonnegative_integer(
        raw.get("ppo_completed"), "ppo_completed"
    )
    if iteration != dagger_completed + ppo_completed:
        raise CheckpointError("training iteration counters are inconsistent")
    if iteration != getattr(checkpoint, "iteration"):
        raise CheckpointError("training iteration disagrees with checkpoint metadata")
    beta = raw.get("beta")
    if (
        isinstance(beta, bool)
        or not isinstance(beta, (int, float))
        or not math.isfinite(float(beta))
        or not 0.0 <= float(beta) <= 1.0
    ):
        raise CheckpointError("training beta must be between 0 and 1")
    if float(beta) != getattr(checkpoint, "beta"):
        raise CheckpointError("training beta disagrees with checkpoint metadata")
    if phase == "dagger" and ppo_completed:
        raise CheckpointError("a DAgger checkpoint cannot contain completed PPO work")

    config = raw.get("config")
    if not isinstance(config, Mapping):
        raise CheckpointError("training config must be a mapping")
    if set(config) != set(_TRAINING_CONFIG_KEYS):
        raise CheckpointError("training config fields do not match this trainer")
    _validate_saved_config(config)
    for name in _TRAINING_CONFIG_KEYS:
        option = f"--{name.replace('_', '-')}"
        if (
            name in ("dagger_iterations", "ppo_iterations")
            and option in schedule_overrides
        ):
            continue
        setattr(args, name, config[name])
    if args.beta_min > args.beta:
        raise CheckpointError("saved beta_min exceeds saved beta")
    if dagger_completed > args.dagger_iterations:
        raise CheckpointError(
            "checkpoint has completed more DAgger iterations than requested"
        )
    if args.ppo_iterations is not None and ppo_completed > args.ppo_iterations:
        raise CheckpointError(
            "checkpoint has completed more PPO iterations than requested"
        )

    python_rng = raw.get("python_rng")
    dagger_rng = raw.get("dagger_rng")
    try:
        random.Random().setstate(python_rng)
        random.Random().setstate(dagger_rng)
    except (TypeError, ValueError) as error:
        raise CheckpointError(f"invalid Python RNG state: {error}") from error
    torch_rng = raw.get("torch_rng")
    if not isinstance(torch_rng, torch.Tensor) or torch_rng.dtype != torch.uint8:
        raise CheckpointError("torch_rng must be a uint8 tensor")
    if not isinstance(raw.get("buffer"), dict):
        raise CheckpointError("training buffer must be a dictionary")
    return {
        "phase": phase,
        "iteration": iteration,
        "dagger_completed": dagger_completed,
        "ppo_completed": ppo_completed,
        "beta": float(beta),
        "buffer": raw["buffer"],
        "python_rng": python_rng,
        "dagger_rng": dagger_rng,
        "torch_rng": torch_rng,
    }


def _state_nonnegative_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CheckpointError(f"training {name} must be a non-negative integer")
    return value


def _validate_saved_config(config: Mapping[object, object]) -> None:
    validators = {
        "dagger_iterations": _positive_int,
        "ppo_iterations": _nonnegative_iteration_limit,
        "horizon": _positive_int,
        "updates": _nonnegative_int,
        "batch_size": _positive_int,
        "buffer_capacity": _positive_int,
        "learning_rate": _positive_float,
        "beta": _probability,
        "beta_decay": _probability,
        "beta_min": _probability,
        "bomb_fraction": _probability,
        "bomb_positive_weight": _positive_float,
        "ppo_epochs": _positive_int,
        "ppo_batch_size": _positive_int,
        "gamma": _probability,
        "gae_lambda": _probability,
        "clip_ratio": _positive_float,
        "value_clip": _positive_float,
        "value_coefficient": _nonnegative_float,
        "entropy_coefficient": _nonnegative_float,
        "max_grad_norm": _positive_float,
    }
    try:
        for name, validator in validators.items():
            value = config[name]
            text = "inf" if name == "ppo_iterations" and value is None else str(value)
            if validator(text) != value:
                raise ValueError(f"{name} has a non-canonical value")
        target_kl = config["target_kl"]
        if target_kl is not None and _positive_float(str(target_kl)) != target_kl:
            raise ValueError("target_kl has a non-canonical value")
        seed = config["seed"]
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError("seed must be an integer")
    except (argparse.ArgumentTypeError, TypeError, ValueError) as error:
        raise CheckpointError(f"invalid saved training config: {error}") from error


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
