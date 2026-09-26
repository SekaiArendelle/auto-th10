"""Plays episodes without a model and reports how they went.

This is the check that the whole loop works against a real game: the policy it
runs is a script, so a score here belongs to the pipeline rather than to a model.
Start the game, enter a stage by hand, and run it.
"""

import argparse
import json
import statistics

from auto_th10 import Settings, Th10Env

from .loop import EpisodeResult, run_episodes
from .policy import POLICIES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m training.evaluate",
        description="Run scripted episodes against a game that is already in a stage.",
    )
    parser.add_argument("--policy", choices=sorted(POLICIES), default="evasive")
    parser.add_argument(
        "--episodes",
        type=int,
        default=1,
        help="how many runs to play; more than one restarts after each ending",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="decisions per episode; unlimited when omitted",
    )
    parser.add_argument("--json", action="store_true", help="print one JSON object per line")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings.for_episodes(args.episodes)
    policy = POLICIES[args.policy]()
    results: list[EpisodeResult] = []

    with Th10Env(settings=settings) as env:
        run_episodes(
            env,
            policy,
            args.episodes,
            max_steps=args.max_steps,
            on_episode=results.append,
        )

    report(results, as_json=args.json)
    return 0


def report(results: list[EpisodeResult], *, as_json: bool) -> None:
    for result in results:
        if as_json:
            print(json.dumps(to_dict(result), sort_keys=True))
        else:
            print(
                f"episode {result.episode}: score {result.score}, {result.steps} steps, "
                f"{result.frames} frames, reward {result.total_reward:.0f}, {result.ending}"
            )
    if not results:
        return
    scores = [result.score for result in results]
    summary = {"episodes": len(results), "best": max(scores), "mean": statistics.fmean(scores)}
    if as_json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(f"{summary['episodes']} episode(s): best {summary['best']}, mean {summary['mean']:.1f}")


def to_dict(result: EpisodeResult) -> dict[str, object]:
    return {
        "episode": result.episode,
        "steps": result.steps,
        "frames": result.frames,
        "score": result.score,
        "reward": result.total_reward,
        "ending": result.ending,
    }


if __name__ == "__main__":
    raise SystemExit(main())
