"""Stand-ins shared by the Python tests: a session that answers from a script.

No game is started and no process is opened, so the tests that use these run
anywhere. They live in their own module because both the environment tests and
the restart tests need them.
"""

from auto_th10 import (
    Action,
    EnemyBullet,
    EnemyLaser,
    GameplayNotActive,
    Observation,
    Point,
    Rect,
    Scene,
    ScreenKind,
    ScreenState,
    Snapshot,
)

DEFAULT_SCENE = Scene.STAGE

MENU_ENTRIES = 3
"""How many entries the ending's menu has: 継続する, リプレイを保存する, タイトル画面に戻る."""

NAME_ENTRY_COLUMNS = 13
"""How many cells one row of the name entry's grid holds."""

NAME_ENTRY_LAST_ROW = 6
"""The grid's last row, the one holding the 終 cell that finishes the screen."""


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
        no_stage_for: int = 0,
        snapshot_gaps: tuple[int, ...] = (),
        freeze_after: int = 0,
        screen_kind: ScreenKind = ScreenKind.STAGE,
        screen_cursor: int = 0,
        screen_error: Exception | None = None,
        refuse_cursor_writes: bool = False,
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
        self.screen_kind = screen_kind
        self.screen_cursor = screen_cursor
        self.screen_error = screen_error
        self.refuse_cursor_writes = refuse_cursor_writes
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
        self._apply(action)

    def _apply(self, action: object) -> None:
        """Moves the fake game's cursor the way the real one does.

        Only the presses the restart sequences send are modelled, and only the
        arithmetic those sequences depend on: a direction on the name entry's grid
        (one cell, rows wrapping) or in the ending's menu (one entry, the list
        wrapping), and the confirm that leaves either screen. The real game's other
        keys are not modelled because no sequence sends them here.
        """
        value = int(action)
        if value == int(Action.NONE):
            return
        if self.screen_kind is ScreenKind.NAME_ENTRY:
            self._apply_grid(value)
        elif self.screen_kind is ScreenKind.MENU:
            if value == int(Action.DOWN):
                self.screen_cursor = (self.screen_cursor + 1) % MENU_ENTRIES
            elif value == int(Action.SHOOT):
                self.screen_kind = ScreenKind.STAGE  # 継続する starts the next run
                self.screen_cursor = 0
        elif value == int(Action.SHOOT):
            # The ending wears no menu of its own: one confirm opens it, and it
            # opens on its last entry (measured; the cursor is what a sequence has
            # to read rather than count from).
            self.screen_kind = ScreenKind.MENU
            self.screen_cursor = MENU_ENTRIES - 1

    def _apply_grid(self, value: int) -> None:
        row, column = divmod(self.screen_cursor, NAME_ENTRY_COLUMNS)
        if value == int(Action.DOWN):
            row = min(row + 1, NAME_ENTRY_LAST_ROW)
        elif value == int(Action.RIGHT):
            column = (column + 1) % NAME_ENTRY_COLUMNS  # a row wraps at its end
        elif value == int(Action.SHOOT):
            self.screen_kind = ScreenKind.MENU  # 終 wrote the record and went back
            self.screen_cursor = 0
            return
        self.screen_cursor = row * NAME_ENTRY_COLUMNS + column

    def focus(self) -> None:
        self.focus_calls += 1

    def screen(self) -> ScreenState:
        if self.screen_error is not None:
            raise self.screen_error
        return ScreenState(kind=self.screen_kind, cursor=self.screen_cursor)

    def set_screen_cursor(self, cursor: int) -> int:
        """The write side of `screen()`, and it takes the cell the way the real one does.

        `refuse_cursor_writes` makes the fake answer as a screen with no cursor
        does, which is what the restart sequences fall back from.
        """
        if self.refuse_cursor_writes or self.screen_kind not in (
            ScreenKind.MENU,
            ScreenKind.NAME_ENTRY,
        ):
            raise RuntimeError("the screen the game is driving keeps no cursor")
        limit = (
            MENU_ENTRIES
            if self.screen_kind is ScreenKind.MENU
            else (NAME_ENTRY_LAST_ROW + 1) * NAME_ENTRY_COLUMNS
        )
        if not 0 <= cursor < limit:
            raise ValueError(f"entry {cursor} is outside the cursor's range 0..{limit - 1}")
        self.screen_cursor = cursor
        return cursor

    def close(self) -> None:
        self.closed = True
