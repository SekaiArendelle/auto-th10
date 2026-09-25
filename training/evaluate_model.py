"""Evaluate a checkpointed memory model against a game already in a stage."""

import argparse
from dataclasses import dataclass
import json
import pathlib
import statistics
import sys

import numpy as np

from auto_th10 import NotInStage

from .rl import (
    CheckpointError,
    EvasiveTeacher,
    Learner,
    MemoryGymEnv,
    Teacher,
    load_checkpoint,
)
from .train import DEFAULT_CHECKPOINT


@dataclass(frozen=True, slots=True)
class ModelEpisodeResult:
    episode: int
    steps: int
    frames: int
    score: int
    total_reward: float
    ending: str
    bombs: int
    movement_matches: int
    bomb_true_positives: int
    bomb_false_positives: int
    bomb_false_negatives: int
    bomb_true_negatives: int

    @property
    def movement_agreement(self) -> float:
        return self.movement_matches / self.steps if self.steps else 0.0

    @property
    def bomb_precision(self) -> float | None:
        predicted = self.bomb_true_positives + self.bomb_false_positives
        return self.bomb_true_positives / predicted if predicted else None

    @property
    def bomb_recall(self) -> float | None:
        positive = self.bomb_true_positives + self.bomb_false_negatives
        return self.bomb_true_positives / positive if positive else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m training.evaluate_model",
        description=(
            "Run a checkpointed model without teacher intervention against a game "
            "that is already in a stage."
        ),
    )
    parser.add_argument(
        "--checkpoint",
        type=pathlib.Path,
        default=DEFAULT_CHECKPOINT,
    )
    parser.add_argument("--episodes", type=_positive_int, default=1)
    parser.add_argument(
        "--max-steps",
        type=_positive_int,
        default=None,
        help="decisions per episode; unlimited when omitted",
    )
    parser.add_argument("--json", action="store_true", help="print JSON lines")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.episodes > 1 and args.max_steps is not None:
        parser.error("--max-steps cannot be combined with --episodes greater than 1")
    try:
        checkpoint = load_checkpoint(args.checkpoint)
    except (CheckpointError, OSError) as error:
        print(f"model was not loaded: {error}", file=sys.stderr)
        return 1

    env = MemoryGymEnv(
        feature_spec=checkpoint.feature_spec,
        max_steps=args.max_steps,
    )
    results: list[ModelEpisodeResult] = []
    try:
        for episode in range(args.episodes):
            results.append(
                evaluate_episode(
                    env,
                    checkpoint.model,
                    EvasiveTeacher(),
                    episode=episode,
                )
            )
    except NotInStage as refused:
        print(f"the model did not run: {refused}", file=sys.stderr)
        return 1
    finally:
        env.close()
    report(results, as_json=args.json)
    return 0


def evaluate_episode(
    env: MemoryGymEnv,
    learner: Learner,
    teacher: Teacher,
    *,
    episode: int,
) -> ModelEpisodeResult:
    """Run one deterministic model episode and compare it with teacher labels."""
    features, info = env.reset()
    teacher.reset()
    steps = 0
    total_reward = 0.0
    bombs = 0
    movement_matches = 0
    bomb_true_positives = 0
    bomb_false_positives = 0
    bomb_false_negatives = 0
    bomb_true_negatives = 0
    terminated = False
    truncated = False
    try:
        while not terminated and not truncated:
            teacher_action = teacher.annotate(env.raw_observation)
            model_action = learner.act(features, deterministic=True).action
            next_features, reward, terminated, truncated, info = env.step(
                np.asarray(
                    (model_action.movement, int(model_action.bomb)),
                    dtype=np.int64,
                )
            )
            teacher.feedback(model_action, frames=int(info["delta_frames"]))
            steps += 1
            total_reward += float(reward)
            bombs += int(model_action.bomb)
            movement_matches += int(
                model_action.movement == teacher_action.movement
            )
            if model_action.bomb and teacher_action.bomb:
                bomb_true_positives += 1
            elif model_action.bomb:
                bomb_false_positives += 1
            elif teacher_action.bomb:
                bomb_false_negatives += 1
            else:
                bomb_true_negatives += 1
            features = next_features
    except BaseException:
        try:
            env.stop()
        except BaseException:
            pass
        raise
    return ModelEpisodeResult(
        episode=episode,
        steps=steps,
        frames=int(info["frames"]),
        score=int(info["score"]),
        total_reward=total_reward,
        ending="game_over" if terminated else "step_limit",
        bombs=bombs,
        movement_matches=movement_matches,
        bomb_true_positives=bomb_true_positives,
        bomb_false_positives=bomb_false_positives,
        bomb_false_negatives=bomb_false_negatives,
        bomb_true_negatives=bomb_true_negatives,
    )


def report(results: list[ModelEpisodeResult], *, as_json: bool) -> None:
    for result in results:
        if as_json:
            print(json.dumps(to_dict(result), sort_keys=True))
        else:
            print(
                f"episode {result.episode}: score {result.score}, "
                f"{result.frames} frames, {result.bombs} bombs, {result.ending}; "
                f"movement agreement {result.movement_agreement:.1%}, "
                f"bomb precision {_ratio_text(result.bomb_precision)}, "
                f"recall {_ratio_text(result.bomb_recall)}"
            )
    if not results:
        return
    summary = summarize(results)
    if as_json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(
            f"{summary['episodes']} episode(s): best {summary['best_score']}, "
            f"mean {summary['mean_score']:.1f}, "
            f"movement agreement {summary['movement_agreement']:.1%}, "
            f"bomb precision {_ratio_text(summary['bomb_precision'])}, "
            f"recall {_ratio_text(summary['bomb_recall'])}"
        )


def to_dict(result: ModelEpisodeResult) -> dict[str, object]:
    return {
        "episode": result.episode,
        "steps": result.steps,
        "frames": result.frames,
        "score": result.score,
        "reward": result.total_reward,
        "ending": result.ending,
        "bombs": result.bombs,
        "movement_agreement": result.movement_agreement,
        "bomb_precision": result.bomb_precision,
        "bomb_recall": result.bomb_recall,
        "bomb_true_positives": result.bomb_true_positives,
        "bomb_false_positives": result.bomb_false_positives,
        "bomb_false_negatives": result.bomb_false_negatives,
        "bomb_true_negatives": result.bomb_true_negatives,
    }


def summarize(results: list[ModelEpisodeResult]) -> dict[str, int | float | None]:
    steps = sum(result.steps for result in results)
    movement_matches = sum(result.movement_matches for result in results)
    true_positives = sum(result.bomb_true_positives for result in results)
    false_positives = sum(result.bomb_false_positives for result in results)
    false_negatives = sum(result.bomb_false_negatives for result in results)
    predicted = true_positives + false_positives
    positive = true_positives + false_negatives
    scores = [result.score for result in results]
    return {
        "episodes": len(results),
        "best_score": max(scores),
        "mean_score": statistics.fmean(scores),
        "bombs": sum(result.bombs for result in results),
        "movement_agreement": movement_matches / steps if steps else 0.0,
        "bomb_precision": true_positives / predicted if predicted else None,
        "bomb_recall": true_positives / positive if positive else None,
    }


def _ratio_text(value: object) -> str:
    return "n/a" if value is None else f"{float(value):.1%}"


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
