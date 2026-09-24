"""The versioned action vocabulary exposed to a learned policy.

The native API accepts a bit mask, but most bit patterns are not useful actions:
left and right can be held together, for example.  The model instead chooses one
of the canonical movement modes and whether to bomb.  Shooting is supplied by
the caller because the first memory policy will hold it during combat and pulse
it during dialogue rather than spend policy capacity learning that convention.
"""

from __future__ import annotations

import operator
from dataclasses import dataclass

from auto_th10 import Action

ACTION_SCHEMA_VERSION = 1

_DIRECTIONS = (
    Action.NONE,
    Action.UP,
    Action.UP | Action.RIGHT,
    Action.RIGHT,
    Action.DOWN | Action.RIGHT,
    Action.DOWN,
    Action.DOWN | Action.LEFT,
    Action.LEFT,
    Action.UP | Action.LEFT,
)

MOVEMENT_ACTIONS: tuple[Action, ...] = _DIRECTIONS + tuple(
    direction | Action.FOCUS for direction in _DIRECTIONS if direction != Action.NONE
)
"""The stable order of the 17 movement choices in action schema version 1."""

_DIRECTION_MASK = Action.LEFT | Action.RIGHT | Action.UP | Action.DOWN
_MODEL_MASK = _DIRECTION_MASK | Action.FOCUS | Action.SHOOT | Action.BOMB


@dataclass(frozen=True, slots=True)
class ModelAction:
    """One policy output: a movement category and a binary bomb decision."""

    movement: int
    bomb: bool | int = False


@dataclass(frozen=True, slots=True)
class ActionSpec:
    """The checkpoint metadata needed to interpret a model's action heads."""

    schema_version: int = ACTION_SCHEMA_VERSION
    movement_choices: int = len(MOVEMENT_ACTIONS)
    bomb_choices: int = 2

    def __post_init__(self) -> None:
        for name in ("schema_version", "movement_choices", "bomb_choices"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
        if self.schema_version != ACTION_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported action schema {self.schema_version}; "
                f"expected {ACTION_SCHEMA_VERSION}"
            )
        if self.movement_choices != len(MOVEMENT_ACTIONS) or self.bomb_choices != 2:
            raise ValueError("action head sizes do not match the selected schema")

    @property
    def shape(self) -> tuple[int, int]:
        """Return the category count of the movement and bomb heads."""
        return (self.movement_choices, self.bomb_choices)


def decode_action(action: ModelAction, *, shoot: bool = True) -> Action:
    """Convert a model output into a valid native action bit mask.

    ``shoot`` is deliberately external to ``ModelAction``.  The initial policy
    always shoots in combat and pulses the key in dialogue, while the model owns
    movement and the scarce bomb resource.
    """
    movement = _require_integer(action.movement, "movement")
    bomb = _require_integer(action.bomb, "bomb", allow_bool=True)
    if movement < 0 or movement >= len(MOVEMENT_ACTIONS):
        raise ValueError(f"movement must be in [0, {len(MOVEMENT_ACTIONS)})")
    if bomb not in (0, 1):
        raise ValueError("bomb must be 0 or 1")

    native = MOVEMENT_ACTIONS[movement]
    if shoot:
        native |= Action.SHOOT
    if bomb:
        native |= Action.BOMB
    return native


def encode_action(action: Action | int) -> ModelAction:
    """Project a native action onto the learned movement and bomb heads.

    The shoot bit is intentionally discarded.  Focus while standing still is
    canonicalized to the ordinary idle movement because it changes no position.
    Escape and contradictory directions are rejected: neither belongs in a
    gameplay policy's action vocabulary.
    """
    raw = _require_integer(action, "action")
    if raw < 0 or raw & ~int(_MODEL_MASK):
        raise ValueError("action contains bits outside the gameplay action vocabulary")
    native = Action(raw)
    if native & Action.LEFT and native & Action.RIGHT:
        raise ValueError("action holds LEFT and RIGHT together")
    if native & Action.UP and native & Action.DOWN:
        raise ValueError("action holds UP and DOWN together")

    movement = native & (_DIRECTION_MASK | Action.FOCUS)
    if movement == Action.FOCUS:
        movement = Action.NONE
    try:
        movement_index = MOVEMENT_ACTIONS.index(movement)
    except ValueError as error:
        raise ValueError(f"action has no canonical movement: {movement!r}") from error
    return ModelAction(movement=movement_index, bomb=bool(native & Action.BOMB))


def _require_integer(value: object, name: str, *, allow_bool: bool = False) -> int:
    if isinstance(value, bool) and not allow_bool:
        raise TypeError(f"{name} must be an integer")
    try:
        return operator.index(value)
    except TypeError as error:
        raise TypeError(f"{name} must be an integer") from error
