"""Observations and actions: the agent's half of the loop.

The environment drives only what it has to. It starts from a stage the player is
already in, and it refuses to touch the game from anywhere else - entering a
stage, picking a shot type and leaving the pause menu stay with the player,
because those are choices a script has no business making. That boundary is what
keeps a wrong key press from landing on a menu.

It asks the game's own data rather than a summary of it: the screen family (one
read, no waiting), the snapshot (the run's numbers) and the stage frame counter,
sampled twice, for the one question no single read can answer. The coarse state
word that used to stand in for all three is not even bound in Python - see
`session.Session` - because it blocks for about 120 ms and says less.

See training/README.md for how the layers above this one fit together.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto

from . import restart
from .session import Action, Scene, Session
from .types import Snapshot

PAUSE_SAMPLE_SECONDS = 0.12
"""How far apart the two frame-counter samples are when asking whether a stage is
frozen: the same 120 ms the C side's own state read uses, and for the same reason
- long enough for a running stage to have advanced several frames, short enough
that both reads still describe the same moment."""


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
        """Raise NotInStage unless the game is in a running stage."""
        snapshot = self._look()
        if snapshot is None or snapshot.game_over:
            raise self._refuse()
        self._require_not_frozen()

    def reset(self) -> tuple[Observation, dict[str, object]]:
        """Starts an episode, clearing or restarting whatever ended before it.

        An ending that is on screen before the first episode is left over from an
        earlier attempt, so it is always cleared - one `<Z>`, not a choice. An
        ending this environment produced is different: OnDeath.STOP leaves it
        there, and reset() then refuses to start another run on top of it.
        """
        snapshot = self._look()
        if snapshot is not None and snapshot.game_over:
            if self._started and self.settings.on_death is not OnDeath.RESTART:
                raise NotInStage("the run is over and OnDeath is STOP: restart it in the game")
            self._leave_game_over()
            snapshot = self._look()
        if snapshot is None or snapshot.game_over:
            raise self._refuse()
        self._require_not_frozen()
        self.session.focus()
        # The snapshot read on the way in is the one to keep. focus() hands the
        # window the keyboard and changes nothing about the run, so reading again
        # would only move the moment this observation describes.
        self._snapshot = snapshot
        self._frames = self.session.stage_frames()
        self._steps = 0
        self._started = True
        return Observation(snapshot=snapshot), {}

    def step(self, action: Action | int) -> tuple[Observation, float, bool, bool, dict[str, object]]:
        """Holds `action`, waits for the game to advance a frame, then reads it.

        Waiting for the counter instead of sleeping is what keeps the loop
        aligned with the game: the same decisions then cover the same frames, and
        a game that has stopped is noticed rather than fed keys.

        reset() has to come first. The constructor only asks the screen family, so
        a stage that turns out to be frozen, or to have no run behind it, gets past
        it; reset() is what refuses those, and this guard is what keeps a key from
        being injected by a caller that skipped it.
        """
        if not self._started:
            raise NotInStage("the episode has not started: call reset() before step()")
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

    def _look(self) -> Snapshot | None:
        """The run as it is right now, or None when there is no run to read.

        This is how the layer asks "is there a stage at all": the read fails
        exactly when the stage object is gone, which covers the title screen, the
        setup screens and the gaps between runs in one test. It also costs
        nothing extra - step() reads a snapshot every frame anyway - where the
        state word charges 120 ms to say something coarser about the same game.
        """
        try:
            return self.session.snapshot()
        except RuntimeError:
            return None

    def _require_startable(self) -> None:
        """Raise NotInStage unless the agent could take over from where the game is.

        The constructor asks the cheap half of the question - the screen family,
        one read of one word - and leaves the rest to reset(), which is where a run
        is actually needed. A menu is refused here; a stage that turns out to be
        frozen, or to have no run behind it at all, is refused when the episode
        starts, because that is when keys would start arriving.
        """
        if self.session.scene() is not Scene.STAGE:
            raise self._refuse()

    def _require_not_frozen(self) -> None:
        """Raise NotInStage when the stage clock has stopped.

        Playing and paused share a screen family and the game raises no flag that
        a single read could see, so this is the one question here that needs two
        samples. It is asked once per episode rather than once per step, and the
        step loop does not need it at all - it waits on the same counter.
        """
        earlier = self.session.stage_frames()
        time.sleep(PAUSE_SAMPLE_SECONDS)
        if self.session.stage_frames() == earlier:
            raise NotInStage(
                f"the stage frame counter is not moving (stuck at {earlier}): "
                "the game is paused, loading or gone"
            )

    def _refuse(self) -> NotInStage:
        """The refusal to run, worded from the one read that is free.

        There are two ways to get here - the game is in the menus, or the family
        reads as a stage while no run is behind it - and the family is enough to
        tell them apart. It used to name the fine-grained screen instead, which
        cost the state word's 120 ms; the episode lifecycle does not bind that word
        at all (see `session.Session`), and an error message is not a reason to
        bring it back.
        """
        where = "in the menus" if self.session.scene() is Scene.MENU else "in a stage with no run"
        return NotInStage(
            f"the game is {where}: enter a stage and leave the pause menu before starting the agent"
        )

    def _wait_for_next_frame(self) -> int:
        """Waits for the game to move on, or hands the step back when it cannot.

        A run that is over stops advancing the stage clock, so the wait would
        time out on the ending rather than read it: the caller is handed back and
        the snapshot that step() reads next is what says the run is over. Anything
        else that stops the clock says which it was, because the screen family is
        free to read and tells a pause from a trip back to the menus.
        """
        deadline = time.monotonic() + self.frame_timeout_s
        while True:
            frames = self.session.stage_frames()
            if frames != self._frames:
                return frames
            if time.monotonic() >= deadline:
                snapshot = self._look()
                if snapshot is not None and snapshot.game_over:
                    return frames
                if self.session.scene() is not Scene.STAGE:
                    raise NotInStage(
                        f"the game left the stage: the frame counter stayed at {frames} for "
                        f"{self.frame_timeout_s:g} s"
                    )
                raise NotInStage(
                    f"the stage frame counter stayed at {frames} for {self.frame_timeout_s:g} s: "
                    "the game is paused, loading, or gone"
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
