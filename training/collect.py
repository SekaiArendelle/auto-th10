"""Records trajectories, one JSON object per step.

The schema is provisional on purpose: it is settled after the reset behaviour has
been validated against a real game. Until then the recorder writes what the loop
already has - the small part of the observation a policy reads, plus the action
and the reward - and leaves the bulk out unless asked, because a step carries up
to a few hundred bullets and a minute of play carries thousands of steps.

Rows go to runs/<timestamp>.jsonl; runs/ is ignored by Git.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

from auto_th10 import NotInStage, Settings, Th10Env

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
    parser.add_argument("--bullets", action="store_true", help="also record every bullet position")
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
                stream.write(json.dumps(to_row(step, with_bullets=args.bullets), sort_keys=True) + "\n")
                written += 1

            run_episodes(env, policy, args.episodes, max_steps=args.max_steps, on_step=record)
    except NotInStage as refused:
        print(f"the agent did not run: {refused}", file=sys.stderr)
        return 1

    print(f"wrote {written} step(s) to {output}")
    return 0


def to_row(step: Step, *, with_bullets: bool) -> dict[str, object]:
    snapshot = step.observation.snapshot
    row: dict[str, object] = {
        "episode": step.episode,
        "step": step.index,
        "score": snapshot.score,
        "power": snapshot.power,
        "lives": snapshot.lives,
        "player": [snapshot.player.x, snapshot.player.y],
        "enemies": len(snapshot.enemies),
        "bullets": len(snapshot.enemy_bullets),
        "lasers": len(snapshot.enemy_lasers),
        "action": int(step.action),
        "reward": step.reward,
        "terminated": step.terminated,
    }
    if with_bullets:
        row["bullet_positions"] = [[bullet.x, bullet.y] for bullet in snapshot.enemy_bullets]
    return row


if __name__ == "__main__":
    raise SystemExit(main())
