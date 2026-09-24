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
class ScreenState:
    """The screen the game is driving, and the cursor that screen keeps.

    `kind` is the raw name the C side reports (see `session.ScreenKind`), and
    `cursor` means whatever that screen's cursor is: the menu entry or the name
    entry's grid cell. A stage keeps neither, so the number is then meaningless
    rather than absent - read `kind` first, which is what makes this the one
    observation that tells an ending's menu from the name entry behind it.
    """

    kind: str
    cursor: int


@dataclass(frozen=True, slots=True)
class Snapshot:
    player: Point
    score: int
    power: int
    lives: int
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
            lives=int(value["lives"]),
            game_over=bool(value["game_over"]),
            enemies=tuple(Rect(*map(float, item)) for item in value["enemies"]),
            enemy_bullets=tuple(EnemyBullet(*map(float, item)) for item in value["enemy_bullets"]),
            enemy_lasers=tuple(EnemyLaser(*map(float, item)) for item in value["enemy_lasers"]),
            resources=tuple(point(item) for item in value["resources"]),
        )
