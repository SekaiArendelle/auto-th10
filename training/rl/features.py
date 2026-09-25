"""A fixed, versioned feature layout for the memory-backed observation.

Snapshots contain variable-length entity tuples.  The first model protocol
sorts each tuple by distance from the player, keeps a configured prefix, and
pads the rest with zeroes plus an explicit validity mask.  It is deliberately a
plain tuple rather than a NumPy array, so this module needs nothing from the
training stack; the Gymnasium adapter is the only module that imports it.
"""

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import NamedTuple, TypeVar

from auto_th10 import EnemyBullet, EnemyLaser, Observation, Point, Rect

from ..dodging import BOMB_COOLDOWN_FRAMES, laser_box

FEATURE_SCHEMA_VERSION = 2

HALF_WIDTH = 200.0
FIELD_WIDTH = 400.0
FIELD_HEIGHT = 480.0
BULLET_SPEED_SCALE = 16.0
SCORE_SCALE = 1_000_000.0
POWER_SCALE = 100.0
LIVES_SCALE = 5.0


class _GlobalFeatures(NamedTuple):
    player_x: float
    player_y: float
    score: float
    power: float
    lives: float
    game_over: float
    frames_since_bomb: float
    enemy_count: float
    bullet_count: float
    laser_count: float
    resource_count: float


class _EnemyFeatures(NamedTuple):
    relative_x: float
    relative_y: float
    width: float
    height: float
    valid: float


class _BulletFeatures(NamedTuple):
    relative_x: float
    relative_y: float
    width: float
    height: float
    velocity_x: float
    velocity_y: float
    valid: float


class _LaserFeatures(NamedTuple):
    relative_x: float
    relative_y: float
    width: float
    height: float
    angle_sin: float
    angle_cos: float
    valid: float


class _ResourceFeatures(NamedTuple):
    relative_x: float
    relative_y: float
    valid: float


Entity = TypeVar("Entity")


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    """Entity limits and schema version stored alongside a model checkpoint."""

    max_enemies: int = 8
    max_bullets: int = 24
    max_lasers: int = 4
    max_resources: int = 8
    schema_version: int = FEATURE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in (
            "max_enemies",
            "max_bullets",
            "max_lasers",
            "max_resources",
            "schema_version",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value < 0:
                raise ValueError(f"{name} must not be negative")
        if self.schema_version != FEATURE_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported feature schema {self.schema_version}; "
                f"expected {FEATURE_SCHEMA_VERSION}"
            )

    @property
    def size(self) -> int:
        """Number of scalar values produced by this specification."""
        return (
            len(_GlobalFeatures._fields)
            + self.max_enemies * len(_EnemyFeatures._fields)
            + self.max_bullets * len(_BulletFeatures._fields)
            + self.max_lasers * len(_LaserFeatures._fields)
            + self.max_resources * len(_ResourceFeatures._fields)
        )


class MemoryFeatureEncoder:
    """Encode an ``Observation`` into the layout described by ``FeatureSpec``."""

    def __init__(self, spec: FeatureSpec = FeatureSpec()) -> None:
        self.spec = spec

    def encode(
        self,
        observation: Observation,
        *,
        frames_since_bomb: int | None = None,
    ) -> tuple[float, ...]:
        """Return bounded scalars in deterministic nearest-entity order."""
        snapshot = observation.snapshot
        player = snapshot.player
        features = list(
            _GlobalFeatures(
                player_x=_clip(player.x / HALF_WIDTH),
                player_y=_clip(2.0 * player.y / FIELD_HEIGHT - 1.0),
                score=_positive_squash(snapshot.score, SCORE_SCALE),
                power=_positive_squash(snapshot.power, POWER_SCALE),
                lives=_clip(snapshot.lives / LIVES_SCALE),
                game_over=1.0 if snapshot.game_over else 0.0,
                frames_since_bomb=_bomb_history_feature(frames_since_bomb),
                enemy_count=_count_feature(len(snapshot.enemies), self.spec.max_enemies),
                bullet_count=_count_feature(
                    len(snapshot.enemy_bullets), self.spec.max_bullets
                ),
                laser_count=_count_feature(
                    len(snapshot.enemy_lasers), self.spec.max_lasers
                ),
                resource_count=_count_feature(
                    len(snapshot.resources), self.spec.max_resources
                ),
            )
        )
        features.extend(
            _encode_entities(
                snapshot.enemies,
                self.spec.max_enemies,
                player,
                _encode_rect,
                len(_EnemyFeatures._fields),
            )
        )
        features.extend(
            _encode_entities(
                snapshot.enemy_bullets,
                self.spec.max_bullets,
                player,
                _encode_bullet,
                len(_BulletFeatures._fields),
            )
        )
        features.extend(
            _encode_entities(
                snapshot.enemy_lasers,
                self.spec.max_lasers,
                player,
                _encode_laser,
                len(_LaserFeatures._fields),
                key=_laser_distance_key,
            )
        )
        features.extend(
            _encode_entities(
                snapshot.resources,
                self.spec.max_resources,
                player,
                _encode_point,
                len(_ResourceFeatures._fields),
            )
        )
        return tuple(features)


def _encode_entities(
    entities: Sequence[Entity],
    limit: int,
    player: Point,
    encode: Callable[[Entity, Point], tuple[float, ...]],
    width: int,
    *,
    key: Callable[[Entity, Point], tuple[float, float, float]] | None = None,
) -> list[float]:
    distance_key = _distance_key if key is None else key
    nearest = sorted(entities, key=lambda entity: distance_key(entity, player))[:limit]
    features = [value for entity in nearest for value in encode(entity, player)]
    features.extend([0.0] * ((limit - len(nearest)) * width))
    return features


def _distance_key(entity: Point, player: Point) -> tuple[float, float, float]:
    return ((entity.x - player.x) ** 2 + (entity.y - player.y) ** 2, entity.x, entity.y)


def _laser_distance_key(laser: EnemyLaser, player: Point) -> tuple[float, float, float]:
    """Rank a laser by its conservative body rather than its distant origin."""
    centre_x, centre_y, half_width, half_height, _, _ = laser_box(laser)
    distance_x = max(0.0, abs(player.x - centre_x) - half_width)
    distance_y = max(0.0, abs(player.y - centre_y) - half_height)
    return (distance_x**2 + distance_y**2, laser.x, laser.y)


def _encode_rect(rect: Rect, player: Point) -> _EnemyFeatures:
    return _EnemyFeatures(
        relative_x=_relative_x(rect.x, player.x),
        relative_y=_relative_y(rect.y, player.y),
        width=_positive_clip(rect.width / FIELD_WIDTH),
        height=_positive_clip(rect.height / FIELD_HEIGHT),
        valid=1.0,
    )


def _encode_bullet(bullet: EnemyBullet, player: Point) -> _BulletFeatures:
    return _BulletFeatures(
        relative_x=_relative_x(bullet.x, player.x),
        relative_y=_relative_y(bullet.y, player.y),
        width=_positive_clip(bullet.width / FIELD_WIDTH),
        height=_positive_clip(bullet.height / FIELD_HEIGHT),
        velocity_x=_clip(bullet.dx / BULLET_SPEED_SCALE),
        velocity_y=_clip(bullet.dy / BULLET_SPEED_SCALE),
        valid=1.0,
    )


def _encode_laser(laser: EnemyLaser, player: Point) -> _LaserFeatures:
    return _LaserFeatures(
        relative_x=_relative_x(laser.x, player.x),
        relative_y=_relative_y(laser.y, player.y),
        width=_positive_clip(laser.width / FIELD_WIDTH),
        height=_positive_clip(laser.height / FIELD_HEIGHT),
        angle_sin=math.sin(laser.radian),
        angle_cos=math.cos(laser.radian),
        valid=1.0,
    )


def _encode_point(point: Point, player: Point) -> _ResourceFeatures:
    return _ResourceFeatures(
        relative_x=_relative_x(point.x, player.x),
        relative_y=_relative_y(point.y, player.y),
        valid=1.0,
    )


def _relative_x(value: float, player: float) -> float:
    return _clip((value - player) / FIELD_WIDTH)


def _relative_y(value: float, player: float) -> float:
    return _clip((value - player) / FIELD_HEIGHT)


def _count_feature(count: int, limit: int) -> float:
    return _positive_squash(count, max(1, limit))


def _positive_squash(value: float, scale: float) -> float:
    positive = max(0.0, float(value))
    return positive / (positive + scale)


def _clip(value: float) -> float:
    return min(1.0, max(-1.0, float(value)))


def _positive_clip(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def _bomb_history_feature(frames_since_bomb: int | None) -> float:
    """Encode recent bomb history, with an old or absent bomb meaning ready."""
    if frames_since_bomb is None:
        return 1.0
    if isinstance(frames_since_bomb, bool) or not isinstance(frames_since_bomb, int):
        raise TypeError("frames_since_bomb must be an integer or None")
    if frames_since_bomb < 0:
        raise ValueError("frames_since_bomb must not be negative")
    age = min(frames_since_bomb, BOMB_COOLDOWN_FRAMES)
    return 2.0 * age / BOMB_COOLDOWN_FRAMES - 1.0
