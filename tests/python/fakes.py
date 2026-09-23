"""Stand-ins shared by the Python tests: a session that answers from a script.

No game is started and no process is opened, so the tests that use these run
anywhere. They live in their own module because both the environment tests and
the restart tests need them.
"""

from __future__ import annotations

from auto_th10 import (
    EnemyBullet,
    EnemyLaser,
    GameplayNotActive,
    Observation,
    Point,
    Rect,
    Scene,
    Snapshot,
)

DEFAULT_SCENE = Scene.STAGE


def make_snapshot(
    *,
    score: int = 0,
    power: int = 0,
    lives: int = 2,
    game_over: bool = False,
    player: tuple[float, float] = (0.0, 0.0),
    enemies: tuple[Rect, ...] = (),
    enemy_bullets: tuple[EnemyBullet, ...] = (),
    enemy_lasers: tuple[EnemyLaser, ...] = (),
    resources: tuple[Point, ...] = (),
) -> Snapshot:
    return Snapshot(
        player=Point(*player),
        score=score,
        power=power,
        lives=lives,
        game_over=game_over,
        enemies=enemies,
        enemy_bullets=enemy_bullets,
        enemy_lasers=enemy_lasers,
        resources=resources,
    )


def make_bullet(x: float, y: float, *, size: float = 8.0, dx: float = 0.0, dy: float = 1.0) -> EnemyBullet:
    return EnemyBullet(x=x, y=y, width=size, height=size, dx=dx, dy=dy)


def make_laser(x: float, y: float, *, size: float = 8.0, radian: float = 0.0) -> EnemyLaser:
    return EnemyLaser(x=x, y=y, width=size, height=size, radian=radian)


def make_enemy(x: float, y: float, *, size: float = 24.0) -> Rect:
    return Rect(x=x, y=y, width=size, height=size)


def observe(snapshot: Snapshot) -> Observation:
    return Observation(snapshot=snapshot)


class FakeSession:
    """A Session stand-in: reads come from a script, input is recorded.

    Each read has a queue whose last entry repeats forever, so a test scripts
    only the answers it cares about. The frame counter advances by one on every
    read, which is what a running stage looks like to the environment; pass
    frame_step=0 to look like a paused one, or freeze_after=N to look like one
    that pauses after N reads.

    There is no state() here either, deliberately: the real Session does not offer
    one (see its docstring), and a fake that did would let a test pass on a call
    the production path cannot make.
    """

    def __init__(
        self,
        *,
        scenes: tuple[Scene, ...] = (DEFAULT_SCENE,),
        snapshots: tuple[Snapshot, ...] = (),
        frame_step: int = 1,
        frame_values: tuple[int, ...] = (),
        record_broken: bool = False,
        record_broken_error: Exception | None = None,
        no_stage_for: int = 0,
        snapshot_gaps: tuple[int, ...] = (),
        freeze_after: int = 0,
    ) -> None:
        self._scenes = list(scenes) or [DEFAULT_SCENE]
        self._snapshots = list(snapshots) or [make_snapshot()]
        self._frame_step = frame_step
        self._frame_values = list(frame_values)
        self._frame_value = 1000
        self._frame_reads = 0
        self._freeze_after = freeze_after
        self._no_stage = no_stage_for
        self._snapshot_gaps = set(snapshot_gaps)
        self._snapshot_reads = 0
        self.record_broken_value = record_broken
        self.record_broken_error = record_broken_error
        self.inputs: list[object] = []
        self.focus_calls = 0
        self.closed = False

    def scene(self) -> Scene:
        if len(self._scenes) > 1:
            return self._scenes.pop(0)
        return self._scenes[0]

    def snapshot(self) -> Snapshot:
        self._snapshot_reads += 1
        if self._no_stage > 0:
            self._no_stage -= 1
            raise GameplayNotActive("gameplay is not active")
        if self._snapshot_reads in self._snapshot_gaps:
            raise GameplayNotActive("gameplay is not active")
        if len(self._snapshots) > 1:
            return self._snapshots.pop(0)
        return self._snapshots[0]

    def stage_frames(self) -> int:
        self._frame_reads += 1
        if self._frame_values:
            if len(self._frame_values) > 1:
                return self._frame_values.pop(0)
            return self._frame_values[0]
        if self._freeze_after and self._frame_reads > self._freeze_after:
            self._frame_step = 0
        self._frame_value += self._frame_step
        return self._frame_value

    def set_input(self, action: object) -> None:
        self.inputs.append(action)

    def focus(self) -> None:
        self.focus_calls += 1

    def record_broken(self) -> bool:
        if self.record_broken_error is not None:
            raise self.record_broken_error
        return self.record_broken_value

    def close(self) -> None:
        self.closed = True
