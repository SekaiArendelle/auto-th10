"""The agent loop: observe, decide, act, record.

The loop knows neither which policy it is running nor where the observations go.
It walks an environment and hands each step to the callbacks it was given, which
is what lets the same loop collect a dataset, score a policy, or follow a
scripted player.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from auto_th10 import Action, Observation, Th10Env, Transition

from .policy import Policy


@dataclass(frozen=True, slots=True)
class Step:
    """One transition, from the observation used to decide through its result."""

    episode: int
    index: int
    observation: Observation
    action: Action
    reward: float
    next_observation: Observation
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
RewardFn = Callable[[Transition], float]


def score_delta(transition: Transition) -> float:
    """Reward a transition by the score gained across it."""
    return float(
        transition.next_observation.snapshot.score
        - transition.observation.snapshot.score
    )


def run_episode(
    env: Th10Env,
    policy: Policy,
    *,
    episode: int = 0,
    max_steps: int | None = 3600,
    reward_fn: RewardFn = score_delta,
    on_step: StepHook | None = None,
) -> EpisodeResult:
    """Plays one episode: reset, then step until the run ends or the cap is hit.

    `max_steps` is a guard, not a limit anybody should reach: one minute of play
    at 60 frames is 3600 decisions. `None` drops the guard and runs until the
    run ends on its own.
    """
    if max_steps is not None and max_steps < 1:
        raise ValueError("max_steps must be positive")
    observation = env.reset()
    first_frame = env.frames
    total_reward = 0.0
    terminated = False
    steps = 0

    try:
        while max_steps is None or steps < max_steps:
            steps += 1
            action = policy.decide(observation)
            transition = env.step(action)
            observation = transition.next_observation
            reward = reward_fn(transition)
            terminated = transition.terminated
            total_reward += reward
            if on_step is not None:
                on_step(
                    Step(
                        episode=episode,
                        index=steps - 1,
                        observation=transition.observation,
                        action=transition.action,
                        reward=reward,
                        next_observation=observation,
                        terminated=terminated,
                    )
                )
            if terminated:
                break
    except BaseException:
        try:
            env.stop()
        except BaseException:
            # Preserve the policy, transition, reward or hook failure that made
            # the runner unwind; the environment is already non-running.
            pass
        raise
    if not terminated:
        env.stop()

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
    max_steps: int | None = 3600,
    reward_fn: RewardFn = score_delta,
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
        result = run_episode(
            env,
            policy,
            episode=episode,
            max_steps=max_steps,
            reward_fn=reward_fn,
            on_step=on_step,
        )
        results.append(result)
        if on_episode is not None:
            on_episode(result)
    return results
