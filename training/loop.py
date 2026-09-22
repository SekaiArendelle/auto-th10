"""The agent loop: observe, decide, act, record.

The loop knows neither which policy it is running nor where the observations go.
It walks an environment and hands each step to the callbacks it was given, which
is what lets the same loop collect a dataset, score a policy, or follow a
scripted player.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from auto_th10 import Action, Observation, Th10Env

from .policy import Policy


@dataclass(frozen=True, slots=True)
class Step:
    """One decision and what it led to, as handed to a recorder."""

    episode: int
    index: int
    observation: Observation
    action: Action
    reward: float
    terminated: bool


@dataclass(frozen=True, slots=True)
class EpisodeResult:
    """How one episode went."""

    episode: int
    steps: int
    frames: int
    """How many game frames the episode covered, as counted by the environment."""
    score: int
    total_reward: float
    ending: str
    """'game_over' when the run ended, 'step_limit' when the cap was reached."""


StepHook = Callable[[Step], None]
EpisodeHook = Callable[[EpisodeResult], None]


def run_episode(
    env: Th10Env,
    policy: Policy,
    *,
    episode: int = 0,
    max_steps: int = 3600,
    on_step: StepHook | None = None,
) -> EpisodeResult:
    """Plays one episode: reset, then step until the run ends or the cap is hit.

    `max_steps` is a guard, not a limit anybody should reach: one minute of play
    at 60 frames is 3600 decisions.
    """
    if max_steps < 1:
        raise ValueError("max_steps must be positive")
    observation, _ = env.reset()
    first_frame = env.frames
    total_reward = 0.0
    terminated = False
    steps = 0

    for steps in range(1, max_steps + 1):
        action = policy.decide(observation)
        observation, reward, terminated, _, _ = env.step(action)
        total_reward += reward
        if on_step is not None:
            on_step(
                Step(
                    episode=episode,
                    index=steps - 1,
                    observation=observation,
                    action=action,
                    reward=reward,
                    terminated=terminated,
                )
            )
        if terminated:
            break

    return EpisodeResult(
        episode=episode,
        steps=steps,
        frames=env.frames - first_frame,
        score=observation.snapshot.score,
        total_reward=total_reward,
        ending="game_over" if terminated else "step_limit",
    )


def run_episodes(
    env: Th10Env,
    policy: Policy,
    episodes: int,
    *,
    max_steps: int = 3600,
    on_step: StepHook | None = None,
    on_episode: EpisodeHook | None = None,
) -> list[EpisodeResult]:
    """Runs `episodes` episodes back to back and returns what each one did.

    What happens between the episodes is the environment's business: with
    OnDeath.STOP the next reset() refuses a finished run, and with OnDeath.RESTART
    it drives the game back into a stage.
    """
    if episodes < 1:
        raise ValueError("episodes must be positive")
    results = []
    for episode in range(episodes):
        result = run_episode(env, policy, episode=episode, max_steps=max_steps, on_step=on_step)
        results.append(result)
        if on_episode is not None:
            on_episode(result)
    return results
