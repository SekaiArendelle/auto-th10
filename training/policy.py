"""Policies: the one layer the three planned agents differ in.

A policy is handed an Observation and returns the action to hold for the next
frame. The scripted policies here are the first of the three; the other two will
read memory and pixels, and because all of them sit behind this boundary, nothing
below it has to know which kind is running.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence
from typing import Protocol

from auto_th10 import Action, Observation, Rect, Snapshot

Point = tuple[float, float]


class Policy(Protocol):
    """Decides the action to hold until the next decision."""

    def decide(self, observation: Observation) -> Action:
        """Returns the keys to hold for the next frame."""


def centre(rect: Rect) -> Point:
    """The middle of a box: the game reports positions as top-left corners."""
    return (rect.x + rect.width / 2.0, rect.y + rect.height / 2.0)


def _distance(first: Point, second: Point) -> float:
    return math.dist(first, second)


class FixedPolicy:
    """Repeats a fixed sequence of actions, ignoring the observation.

    It exists to prove the loop end to end: it reads nothing, so whatever it
    scores comes from the rest of the pipeline. It is also the shape a replay of
    recorded actions would take.
    """

    def __init__(self, actions: Sequence[Action] = (Action.SHOOT,), *, frames_per_action: int = 30) -> None:
        if not actions:
            raise ValueError("actions must not be empty")
        if frames_per_action < 1:
            raise ValueError("frames_per_action must be positive")
        self.actions = tuple(actions)
        self.frames_per_action = frames_per_action
        self._decisions = 0

    def decide(self, observation: Observation) -> Action:
        index = (self._decisions // self.frames_per_action) % len(self.actions)
        self._decisions += 1
        return self.actions[index]


class RandomPolicy:
    """Holds a random combination drawn from `choices`, for smoke tests.

    It exercises the input path without pretending to play, which makes it the
    quickest way to check that keys reach the game at all.
    """

    def __init__(
        self,
        choices: Sequence[Action] = (Action.NONE, Action.LEFT, Action.RIGHT, Action.SHOOT),
        *,
        seed: int | None = None,
    ) -> None:
        if not choices:
            raise ValueError("choices must not be empty")
        self.choices = tuple(choices)
        self._random = random.Random(seed)

    def decide(self, observation: Observation) -> Action:
        return self._random.choice(self.choices)


class EvasivePolicy:
    """A hand-written survival policy: dodge, shoot, and bomb when cornered.

    The rules, in order:

    1. if the nearest bullet or laser is inside `panic_radius`, bomb: the bomb
       clears what is around the player and the game grants a moment of grace;
    2. otherwise step left or right, whichever puts more room between the player
       and the nearest hazard inside `danger_radius`;
    3. with nothing near, line up horizontally with the nearest enemy, which is
       what makes the shot connect.

    Focus is held on every decision, because the precise movement the dodging
    rule assumes is what focus gives.

    It is deliberately simple: the point is a deterministic baseline that plays a
    real stage, so a model has something to beat.
    """

    def __init__(
        self,
        *,
        danger_radius: float = 96.0,
        panic_radius: float = 24.0,
        dodge_step: float = 24.0,
        aim_deadzone: float = 4.0,
        focus: bool = True,
    ) -> None:
        self.danger_radius = danger_radius
        self.panic_radius = panic_radius
        self.dodge_step = dodge_step
        self.aim_deadzone = aim_deadzone
        self.focus = focus

    def decide(self, observation: Observation) -> Action:
        snapshot = observation.snapshot
        action = Action.SHOOT if not self.focus else Action.SHOOT | Action.FOCUS
        x, y = snapshot.player.x, snapshot.player.y
        hazards = [centre(item) for item in (*snapshot.enemy_bullets, *snapshot.enemy_lasers)]

        if hazards:
            nearest = min(_distance(point, (x, y)) for point in hazards)
            if nearest <= self.panic_radius:
                return action | Action.BOMB
            return action | self._step_away(hazards, x, y)
        return action | self._aim(snapshot, x, y)

    def _step_away(self, hazards: list[Point], x: float, y: float) -> Action:
        near = [point for point in hazards if _distance(point, (x, y)) <= self.danger_radius]
        if not near:
            return Action.NONE
        options = (
            (Action.NONE, x),
            (Action.LEFT, x - self.dodge_step),
            (Action.RIGHT, x + self.dodge_step),
        )
        best = max(options, key=lambda option: min(_distance(point, (option[1], y)) for point in near))
        return best[0]

    def _aim(self, snapshot: Snapshot, x: float, y: float) -> Action:
        if not snapshot.enemies:
            return Action.NONE
        target = min((centre(enemy) for enemy in snapshot.enemies), key=lambda point: _distance(point, (x, y)))
        if target[0] < x - self.aim_deadzone:
            return Action.LEFT
        if target[0] > x + self.aim_deadzone:
            return Action.RIGHT
        return Action.NONE


POLICIES: dict[str, Callable[[], Policy]] = {
    "evasive": EvasivePolicy,
    "fixed": FixedPolicy,
    "random": RandomPolicy,
}
"""The policies the command line entry points can build by name."""
