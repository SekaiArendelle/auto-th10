from __future__ import annotations

from enum import IntFlag
from types import TracebackType

from . import _native
from .types import Snapshot


class Action(IntFlag):
    NONE = _native.NONE
    LEFT = _native.LEFT
    RIGHT = _native.RIGHT
    UP = _native.UP
    DOWN = _native.DOWN
    SHOOT = _native.SHOOT
    FOCUS = _native.FOCUS
    BOMB = _native.BOMB


class Session:
    def __init__(self) -> None:
        self._native = _native.Session()

    def close(self) -> None:
        self._native.close()

    def focus(self) -> None:
        self._native.focus()

    def set_input(self, action: Action | int) -> None:
        self._native.set_input(int(action))

    def snapshot(self) -> Snapshot:
        return Snapshot.from_native(self._native.snapshot())

    def __enter__(self) -> Session:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
