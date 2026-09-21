from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class Rect:
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True, slots=True)
class EnemyBullet(Rect):
    dx: float
    dy: float


@dataclass(frozen=True, slots=True)
class EnemyLaser(Rect):
    radian: float


@dataclass(frozen=True, slots=True)
class Snapshot:
    player: Point
    score: int
    power: int
    hp: int
    game_over: bool
    enemies: tuple[Rect, ...]
    enemy_bullets: tuple[EnemyBullet, ...]
    enemy_lasers: tuple[EnemyLaser, ...]
    resources: tuple[Point, ...]

    @classmethod
    def from_native(cls, value: Mapping[str, Any]) -> Snapshot:
        def point(item: Sequence[float]) -> Point:
            return Point(float(item[0]), float(item[1]))

        return cls(
            player=point(value["player"]),
            score=int(value["score"]),
            power=int(value["power"]),
            hp=int(value["hp"]),
            game_over=bool(value["game_over"]),
            enemies=tuple(Rect(*map(float, item)) for item in value["enemies"]),
            enemy_bullets=tuple(EnemyBullet(*map(float, item)) for item in value["enemy_bullets"]),
            enemy_lasers=tuple(EnemyLaser(*map(float, item)) for item in value["enemy_lasers"]),
            resources=tuple(point(item) for item in value["resources"]),
        )
