"""Observations and actions: the agent's half of the loop.

The environment drives only what it has to. It starts from a stage the player is
already in, and it refuses to touch the game from anywhere else - entering a
stage, picking a shot type and leaving the pause menu stay with the player,
because those are choices a script has no business making. That boundary is what
keeps a wrong key press from landing on a menu.

See training/README.md for how the layers above this one fit together.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto

from . import restart
from .session import Action, Session, State
from .types import Snapshot


class OnDeath(Enum):
    """What the episode lifecycle does when a run ends."""

    STOP = auto()
    """Leave the ending on screen: for a validation run the ending is the point."""

    RESTART = auto()
    """Drive back into a stage: for a collector the ending is noise."""


class OnNameEntry(Enum):
    """What to do when a broken record makes the game ask for a name."""

    STOP = auto()
    """Leave it on screen: a record is worth surfacing, not a hiccup."""

    TYPE = auto()
    """Enter a name and carry on. Not implemented - see restart.type_name()."""


@dataclass(frozen=True, slots=True)
class Settings:
    """The lifecycle choices, as a value the caller hands to Th10Env."""

    on_death: OnDeath = OnDeath.STOP
    on_name_entry: OnNameEntry = OnNameEntry.STOP
    confirm_timeout_s: float = 5.0
    """How long a menu key sequence is given to take effect."""

    @staticmethod
    def for_episodes(episodes: int) -> Settings:
        """The settings that fit a run of `episodes` episodes.

        One episode is watched, so its ending stays on screen. More than one has
        to restart in between, because the game never leaves an ending by itself.
        """
        return TRAIN_PRESET if episodes > 1 else EVAL_PRESET


TRAIN_PRESET = Settings(on_death=OnDeath.RESTART)
"""Collect data: get back into a stage as fast as the game allows."""

EVAL_PRESET = Settings()
"""Watch a run: stop at the ending and leave it on screen."""


@dataclass(frozen=True, slots=True)
class Frame:
    """One captured screen, for a policy that reads pixels instead of memory.

    Nothing fills this in yet: the scripted policies decide from
    Observation.snapshot alone, and reading pixels needs the capture path to be
    measured for throughput first.
    """

    width: int
    height: int
    data: bytes


@dataclass(frozen=True, slots=True)
class Observation:
    """What a policy decides from: the game's state, optionally with its screen."""

    snapshot: Snapshot
    frame: Frame | None = None


class NotInStage(RuntimeError):
    """The game is not in a running stage, so injecting keys would be wrong.

    Raised when the screen is a menu, the pause menu or a game over, and when the
    stage frame counter stops moving under step(). Both are the same problem:
    the keys would land on a menu instead of the game.
    """


def _not_in_stage(state: State) -> str:
    return (
        f"the game is in {state.name.lower()}: enter a stage and leave the pause menu before "
        "starting the agent"
    )


def score_delta(previous: Snapshot, current: Snapshot) -> float:
    """The default reward: how much the score moved.

    Exposed rather than private because a policy that wants a shaped reward has
    to start from something, and this is the honest signal the game gives.
    """
    return float(current.score - previous.score)


class Th10Env:
    """A stage, an observation and an action mask.

    Construct it while the game is in a stage (that is checked), call reset() to
    start an episode and step() to move one frame. The session is opened by the
    environment unless one is passed in, which is how the tests drive it without
    a game.
    """

    poll_seconds: float = 0.001
    """How often the frame counter is re-read while waiting for the game."""

    def __init__(
        self,
        *,
        settings: Settings = EVAL_PRESET,
        frame_timeout_s: float = 2.0,
        reward_fn: Callable[[Snapshot, Snapshot], float] = score_delta,
        require_stage: bool = True,
        session: Session | None = None,
    ) -> None:
        self.settings = settings
        self.frame_timeout_s = frame_timeout_s
        self.reward_fn = reward_fn
        self.session = Session() if session is None else session
        self._snapshot: Snapshot | None = None
        self._frames = 0
        self._steps = 0
        self._started = False
        if require_stage:
            self._require_startable()

    def require_in_stage(self) -> None:
        """Raise NotInStage unless the game is in a playing stage.

        Asking for the screen costs about 120 ms, because th10_read_state() has
        to sample the frame counter twice to tell playing from paused, so this is
        a slow-path check: the constructor and reset() make it, the step loop
        never does.
        """
        self._require_playing(self.session.state())

    @staticmethod
    def _require_playing(state: State) -> None:
        if state is not State.PLAYING:
            raise NotInStage(_not_in_stage(state))

    def _require_startable(self) -> None:
        """Raise NotInStage unless the agent could take over from where the game is.

        A finished run counts: an ending that was already on screen when the agent
        started was left there by an earlier attempt, and clearing it is one key
        press rather than a choice. A menu or the pause menu does not, because
        leaving those is the player's decision.
        """
        state = self.session.state()
        if state in (State.PLAYING, State.GAME_OVER):
            return
        raise NotInStage(_not_in_stage(state))

    def reset(self) -> tuple[Observation, dict[str, object]]:
        """Starts an episode, clearing or restarting whatever ended before it.

        An ending that is on screen before the first episode is left over from an
        earlier attempt, so it is always cleared - one `<Z>`, not a choice. An
        ending this environment produced is different: OnDeath.STOP leaves it
        there, and reset() then refuses to start another run on top of it.
        """
        state = self.session.state()
        if state is State.GAME_OVER:
            if self._started and self.settings.on_death is not OnDeath.RESTART:
                raise NotInStage("the run is over and OnDeath is STOP: restart it in the game")
            self._leave_game_over()
            state = self.session.state()
        self._require_playing(state)
        self.session.focus()
        self._snapshot = self.session.snapshot()
        self._frames = self.session.stage_frames()
        self._steps = 0
        self._started = True
        return Observation(snapshot=self._snapshot), {}

    def step(self, action: Action | int) -> tuple[Observation, float, bool, bool, dict[str, object]]:
        """Holds `action`, waits for the game to advance a frame, then reads it.

        Waiting for the counter instead of sleeping is what keeps the loop
        aligned with the game: the same decisions then cover the same frames, and
        a game that has stopped is noticed rather than fed keys.
        """
        previous = self._snapshot
        self.session.set_input(action)
        self._frames = self._wait_for_next_frame()
        snapshot = self.session.snapshot()
        self._snapshot = snapshot
        self._steps += 1
        reward = 0.0 if previous is None else self.reward_fn(previous, snapshot)
        return Observation(snapshot=snapshot), reward, snapshot.game_over, False, {
            "steps": self._steps
        }

    @property
    def frames(self) -> int:
        """The last stage frame counter the environment saw."""
        return self._frames

    @property
    def steps(self) -> int:
        """How many steps the current episode has taken."""
        return self._steps

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> Th10Env:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _wait_for_next_frame(self) -> int:
        deadline = time.monotonic() + self.frame_timeout_s
        while True:
            frames = self.session.stage_frames()
            if frames != self._frames:
                return frames
            if time.monotonic() >= deadline:
                raise NotInStage(
                    f"the stage frame counter stayed at {frames} for {self.frame_timeout_s:g} s: "
                    "the game is paused, in a menu, or gone"
                )
            time.sleep(self.poll_seconds)

    def _leave_game_over(self) -> None:
        if self.session.record_broken():
            if self.settings.on_name_entry is OnNameEntry.STOP:
                raise NotInStage(
                    "the run broke the record and OnNameEntry is STOP: the game is asking for a name"
                )
            restart.type_name(self.session)
        restart.leave_game_over(self.session, timeout_s=self.settings.confirm_timeout_s)
