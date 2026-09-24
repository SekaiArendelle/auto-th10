"""Policies: the boundary shared by scripted and learned players.

A policy is handed an Observation and returns the action to hold for the next
frame. The scripted policies here are the baseline for a memory-backed learned
policy, and because both sit behind this boundary, nothing below it has to know
which kind is running.

`EvasivePolicy` is the one worth reading: it searches the moves the game accepts
instead of reacting to the nearest bullet, and the arithmetic it does that with
lives in `dodging.py`, next to the reference each number came from.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from typing import Protocol

from auto_th10 import Action, Observation, Snapshot

from . import dodging
from .shooting import shoot_action


class Policy(Protocol):
    """Decides the action to hold until the next decision."""

    def decide(self, observation: Observation) -> Action:
        """Returns the keys to hold for the next frame."""


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
    """A hand-written survival policy: look ahead, then stand where it is best.

    Every frame it tries each key combination the game accepts - the eight
    directions, each at focus speed and at full speed, plus standing still (once,
    since holding focus while going nowhere moves nobody) - walks each one
    `horizon` frames into the future past the bullets, lasers and enemies it can
    see, and drops the ones that get hit. What survives is ranked by how good the
    spot it ends on is: low on the field and centred is safe, lined up under an
    enemy is where the shots land, near a resource point is worth the risk. Only
    when nothing survives - every candidate is hit within `bomb_frames` - does it
    spend a bomb, and then it leaves the bomb key alone for
    `bomb_cooldown_frames` frames.

    The shape is TH10AI's `GameManager`, which searches the same moves with a BFS
    over a value map. Two things are deliberately different. The value is read
    once per move, at the position it ends on, rather than at every state the
    search passes through: that is what keeps the search cheap enough for Python
    to run every frame, and with a horizon long enough to see a bullet coming it
    gives up little. And the bomb is held back by a frame counter instead of by
    the invulnerability the game would report, because the snapshot carries no
    such flag yet.

    It is still deliberately simple, and every number it uses is a keyword
    argument: it is the deterministic baseline that plays a real stage, so a
    model has something to beat, and it should be tunable against one.
    """

    def __init__(
        self,
        *,
        horizon: int = 12,
        focus: bool = True,
        scan_radius: float = dodging.SCAN_RADIUS,
        max_hazards: int | None = dodging.MAX_HAZARDS,
        margin: float = dodging.COLLISION_MARGIN,
        bullet_lead: float = dodging.BULLET_LEAD,
        bomb_frames: int = dodging.BOMB_FRAMES,
        bomb_cooldown_frames: int = dodging.BOMB_COOLDOWN_FRAMES,
        position_weight: float = 80.0,
        aim_weight: float = 80.0,
        resource_weight: float = 180.0,
        threat_weight: float = 80.0,
        incoming_weight: float = 70.0,
    ) -> None:
        if horizon < 1:
            raise ValueError("horizon must be positive")
        if bomb_frames < 0:
            raise ValueError("bomb_frames must not be negative")
        if bomb_cooldown_frames < 0:
            raise ValueError("bomb_cooldown_frames must not be negative")
        if max_hazards is not None and max_hazards < 1:
            raise ValueError("max_hazards must be positive")
        self.horizon = horizon
        self.focus = focus
        self.scan_radius = scan_radius
        self.max_hazards = max_hazards
        self.margin = margin
        self.bullet_lead = bullet_lead
        self.bomb_frames = bomb_frames
        self.bomb_cooldown_frames = bomb_cooldown_frames
        self.position_weight = position_weight
        self.aim_weight = aim_weight
        self.resource_weight = resource_weight
        self.threat_weight = threat_weight
        self.incoming_weight = incoming_weight
        self._moves = dodging.moves(focus=focus)
        self._cooldown = 0
        self._lives = 0
        self._decisions = 0

    def decide(self, observation: Observation) -> Action:
        snapshot = observation.snapshot
        self._decisions += 1
        self._forget_an_old_cooldown(snapshot)
        x, y = snapshot.player.x, snapshot.player.y
        boxes = dodging.boxes_from(
            snapshot,
            lead=self.bullet_lead,
            radius=self.scan_radius,
            limit=self.max_hazards,
        )
        ranked = []
        for action, step_x, step_y in self._moves:
            frames, end_x, end_y = dodging.survival(
                x,
                y,
                step_x,
                step_y,
                boxes,
                horizon=self.horizon,
                margin=self.margin,
            )
            value = self._value(end_x, end_y, snapshot, boxes)
            ranked.append((frames, value, bool(action & Action.FOCUS), action))

        # While any move survives the whole walk, the surviving ones are the
        # only candidates: they all last, so what is left to choose between is
        # where they end up - which is the reference's rule, and it is what keeps
        # the policy from running to the bottom of the field to outlast a bullet
        # that is falling at the same speed. Staying alive longer only decides
        # between moves when none of them survives.
        survivors = [move for move in ranked if move[0] > self.horizon]
        if survivors:
            frames, _, _, action = max(survivors, key=lambda move: (move[1], move[2]))
        else:
            frames, _, _, action = max(ranked, key=lambda move: move[:3])

        if frames <= self.bomb_frames and self._cooldown == 0:
            self._cooldown = self.bomb_cooldown_frames
            return action | shoot_action(snapshot, self._decisions) | Action.BOMB
        self._cooldown = max(0, self._cooldown - 1)
        return action | shoot_action(snapshot, self._decisions)

    def _forget_an_old_cooldown(self, snapshot: Snapshot) -> None:
        """Drops the bomb cooldown when a new run has begun.

        The policy object outlives an episode - an entry point builds one and
        runs several with it - so a cooldown spent in the last seconds of one run
        would otherwise carry into the next, which hands out a fresh set of
        bombs. A life count that has gone up is the sign: the run restarted.
        A stage boundary within one run looks the same as anywhere else, so a
        cooldown can survive into the next stage; that costs at most the length
        of the cooldown and needs no state the snapshot does not carry.
        """
        if snapshot.lives > self._lives:
            self._cooldown = 0
        self._lives = snapshot.lives

    def _value(self, x: float, y: float, snapshot: Snapshot, boxes: Sequence[dodging.Box]) -> float:
        """How good the spot a move ends on is, over everything on the field.

        `boxes` is the hazard set the walk used, so a move is never ranked by a
        bullet it was not asked to survive.
        """
        return (
            self.position_weight * dodging.position_value(x, y)
            + self.aim_weight * dodging.aim_value(x, snapshot.enemies)
            + self.resource_weight * dodging.resource_value(x, y, snapshot.resources)
            + self.threat_weight * dodging.threat_value(x, y, snapshot.enemies)
            + self.incoming_weight * dodging.attack_value(x, y, boxes)
        )


POLICIES: dict[str, Callable[[], Policy]] = {
    "evasive": EvasivePolicy,
    "fixed": FixedPolicy,
    "random": RandomPolicy,
}
"""The policies the command line entry points can build by name."""
