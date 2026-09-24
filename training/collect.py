"""Records complete trajectories, one JSON object per transition.

The versioned shape lives in `dataset.py`. Each row carries the observation used
to choose the action and the observation that resulted, including all objects in
the memory snapshot.

Rows go to runs/<timestamp>.jsonl; runs/ is ignored by Git.
"""

import argparse
import json
import pathlib
import sys
import time

from auto_th10 import NotInStage, Settings, Th10Env

from . import dataset
from .loop import Step, run_episodes
from .policy import POLICIES

DEFAULT_DIRECTORY = pathlib.Path("runs")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m training.collect",
        description="Record a scripted policy's trajectory against a running game.",
    )
    parser.add_argument("--policy", choices=sorted(POLICIES), default="evasive")
    parser.add_argument(
        "--episodes",
        type=int,
        default=1,
        help="how many runs to record; more than one restarts after each ending",
    )
    parser.add_argument("--max-steps", type=int, default=3600, help="decisions per episode")
    parser.add_argument(
        "--out",
        type=pathlib.Path,
        default=None,
        help="where to write; defaults to runs/<timestamp>.jsonl",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output = args.out or DEFAULT_DIRECTORY / f"{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    policy = POLICIES[args.policy]()
    settings = Settings.for_episodes(args.episodes)
    written = 0

    try:
        with Th10Env(settings=settings) as env, output.open("w", encoding="utf-8") as stream:

            def record(step: Step) -> None:
                nonlocal written
                stream.write(json.dumps(dataset.to_row(step), sort_keys=True) + "\n")
                written += 1

            run_episodes(env, policy, args.episodes, max_steps=args.max_steps, on_step=record)
    except NotInStage as refused:
        print(f"the agent did not run: {refused}", file=sys.stderr)
        return 1

    print(f"wrote {written} step(s) to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
