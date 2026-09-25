"""Frame-synchronized actions and transitions: the agent's half of the loop.

The environment drives only what it has to. It starts from a stage the player is
already in, and it refuses to touch the game from anywhere else - entering a
stage and picking a shot type stay with the player, because those are choices a
script has no business making. The one menu it drives is the pause menu: one this
environment opened itself, after checking that its safe default entry has not
moved, and one reset() finds already up - no run can start behind it, so leaving
it from that same entry is part of starting an episode. That boundary is what
keeps a wrong key press from landing on another menu.

It asks the game's own data rather than a summary of it: the screen family (one
read, no waiting), the snapshot (the run's numbers) and the screen the game is
driving (which page is up, and where its cursor is). The coarse state word that
used to stand in for all of them is not even bound in Python - see
`session.Session` - because it says less than those reads answer between them.

See training/README.md for how the layers above this one fit together.
"""

import time
from dataclasses import dataclass
from enum import Enum, auto

from . import restart
from .session import Action, GameplayNotActive, Scene, ScreenKind, Session
from .types import Snapshot

PAUSE_SAMPLE_SECONDS = 0.12
"""How far apart the two frame-counter samples are when asking whether a stage is
frozen: long enough for a running stage to have advanced several frames, short
enough that both reads still describe the same moment.

A pause is not read from these samples: the pause menu is a page of its own, so
`screen()` answers what two samples could only say as "the clock has stopped" -
which is what a stage that is still loading looks like as well. The samples are
left for the clock question itself, which reset() asks as the gate before reading
that page and again after a tap to see the tap land, and which
`_require_not_frozen` words as a refusal."""

PAUSE_MENU_RESUME = 0
"""`Return to Game`, the pause menu's first entry - the only cursor position this
layer may press Z from.

The confirmation behind `Retry This Game` keeps its cursor in the same field and
opens on 1, and its 0 is `Yes`, which leaves the run for the title. That is why
the screen's own kind is part of the check and not only this number."""


class OnDeath(Enum):
    """What the episode lifecycle does when a run ends."""

    STOP = auto()
    """Leave the ending on screen: for a validation run the ending is the point."""

    RESTART = auto()
    """Drive back into a stage: for a collector the ending is noise."""


class OnNameEntry(Enum):
    """What to do when the run reached its difficulty's ranking and the game asks
    for a name.

    This is not the same question as a broken high score: the ranking takes the top
    ten of the difficulty that was played, which a run reaches far more often, and
    the high score is a different and higher bar that a run can be asked for a name
    without ever passing.
    """

    STOP = auto()
    """Leave it on screen: a record is worth surfacing, not a hiccup."""

    LEAVE = auto()
    """Confirm the name the game already holds and carry on.

    Nothing types into the grid, so what gets written is the game's own default
    name - which is what a collector wants, since the next episode needs a stage
    rather than a name of its own.
    """


@dataclass(frozen=True, slots=True)
class Settings:
    """The lifecycle choices, as a value the caller hands to Th10Env."""

    on_death: OnDeath = OnDeath.STOP
    on_name_entry: OnNameEntry = OnNameEntry.STOP
    confirm_timeout_s: float = 5.0
    """How long a menu key sequence is given to take effect."""

    name_entry_timeout_s: float = 20.0
    """How long the name entry's grid is given to be walked.

    That screen is 13 cells by 7 rows and the cursor moves one cell per press, so
    this is a budget of presses rather than of screens: the same 5 s the menu gets
    would cover a third of the way to 終 and then refuse a game that was working.
    """

    @staticmethod
    def for_episodes(episodes: int) -> Settings:
        """The settings that fit a run of `episodes` episodes.

        One episode is watched, so its ending stays on screen. More than one has
        to restart in between, because the game never leaves an ending by itself.
        """
        return TRAIN_PRESET if episodes > 1 else EVAL_PRESET


TRAIN_PRESET = Settings(on_death=OnDeath.RESTART, on_name_entry=OnNameEntry.LEAVE)
"""Collect data: get back into a stage as fast as the game allows.

A record the run reached is not worth stopping for here - what the game writes is
its own default name - so this walks the name entry out and takes the next run."""

EVAL_PRESET = Settings()
"""Watch a run: stop at the ending and leave it on screen."""


@dataclass(frozen=True, slots=True)
class Observation:
    """The memory-backed game state from which a policy decides."""

    snapshot: Snapshot


@dataclass(frozen=True, slots=True)
class Transition:
    """One applied action and the game state on both sides of it.

    `frames` is actual progress of the stage clock. It can be zero when the
    terminal snapshot is discovered after that clock has already frozen; a
    reward model that charges per decision can impose its own minimum.
    """

    observation: Observation
    action: Action
    next_observation: Observation
    frames: int
    terminated: bool


class _Phase(Enum):
    NEW = auto()
    RUNNING = auto()
    PAUSED = auto()
    ENDED = auto()
    INTERRUPTED = auto()
    CLOSED = auto()


class NotInStage(RuntimeError):
    """The game is not in a running stage, so injecting keys would be wrong.

    Raised when the screen is a menu, a game over, or a pause menu on an entry
    other than `Return to Game`, and when the stage frame counter has stopped by
    the time reset() or require_in_stage() samples it. Both are the same problem:
    the keys would land on a menu instead of the game.
    """


class Th10Env:
    """Execute actions against a stage and report the resulting transitions.

    Construction opens the session but does not inspect or drive the game. Call
    reset() to validate the stage and start an episode, then step() to move one
    frame. Reward belongs to the runner or training adapter consuming the
    transition, not to this game-driving layer.

    The session is this environment's own: it builds one and therefore closes it,
    and the constructor takes no way to hand one in. A test that needs a stand-in
    patches `auto_th10.env.Session` (see `tests/python/fakes.py`), which is what
    keeps "what this holds" and "what this closes" one object.
    """

    poll_seconds: float = 0.001
    """How often the frame counter is re-read while waiting for the game."""

    def __init__(
        self,
        *,
        settings: Settings = EVAL_PRESET,
        frame_timeout_s: float = 2.0,
        transition_timeout_s: float = 10.0,
    ) -> None:
        self.settings = settings
        self.frame_timeout_s = frame_timeout_s
        self.transition_timeout_s = transition_timeout_s
        self.session = Session()
        self._snapshot: Snapshot | None = None
        self._stage_frames = 0
        self._frames = 0
        self._steps = 0
        self._phase = _Phase.NEW

    def require_in_stage(self) -> None:
        """Raise NotInStage unless the game is in a running stage."""
        snapshot = self._look()
        if snapshot is None or snapshot.game_over:
            raise self._refuse()
        self._require_not_frozen()

    def reset(self) -> Observation:
        """Starts an episode, clearing or restarting whatever ended before it.

        An ending that is on screen before the first episode is left over from an
        earlier attempt, so it is always cleared - `restart.leave_game_over()`
        walks its menu back into a run. An ending this environment produced is
        different: OnDeath.STOP leaves it there, and reset() then refuses to start
        another run on top of it.

        A pause menu the game was left on is the same kind of leftover: no run can
        start behind it, so once the clock reads as stopped, `_leave_pause_if_up()`
        leaves it from `Return to Game`, the only entry a key may be sent from.
        This environment's own pause is not: that boundary is refused above, still
        open for resume() to close.
        """
        if self._phase is _Phase.CLOSED:
            raise RuntimeError("the environment is closed")
        if self._phase is _Phase.PAUSED:
            raise NotInStage("the episode is paused: call resume() before reset()")
        continuing = self._phase is not _Phase.NEW
        if self._phase is _Phase.RUNNING:
            self.session.set_input(Action.NONE)
        if continuing:
            self._phase = _Phase.INTERRUPTED
        if self.session.scene() is not Scene.STAGE:
            raise self._refuse()
        snapshot = self._look()
        if snapshot is not None and snapshot.game_over:
            if continuing:
                self.session.set_input(Action.NONE)
            if (
                continuing and self.settings.on_death is not OnDeath.RESTART
            ):
                raise NotInStage("the run is over and OnDeath is STOP: restart it in the game")
            # The sequence below presses keys, and a key only reaches the game while
            # its window owns the foreground (docs/game-ui.md), so the focus is
            # asked for before them rather than only with the episode's own keys.
            self.session.focus()
            self._leave_game_over()
            snapshot = self._look()
        if snapshot is None or snapshot.game_over:
            raise self._refuse()
        if self._stuck_frame_count() is not None:
            # The clock stopped, and of the three causes in the message below only
            # a pause menu the game was left on can be repaired here. The screen is
            # read only on this path - a run that is moving costs nothing - and the
            # clock is sampled again afterwards, which is what shows a tap landed.
            self._leave_pause_if_up()
            self._require_not_frozen()
        self.session.focus()
        # The snapshot read on the way in is the one to keep. focus() hands the
        # window the keyboard and changes nothing about the run, so reading again
        # would only move the moment this observation describes.
        self._snapshot = snapshot
        self._stage_frames = self.session.stage_frames()
        self._frames = 0
        self._steps = 0
        self._phase = _Phase.RUNNING
        return Observation(snapshot=snapshot)

    def step(self, action: Action | int) -> Transition:
        """Holds `action`, waits for the game to advance a frame, then reads it.

        Waiting for the counter instead of sleeping is what keeps the loop
        aligned with the game: the same decisions then cover the same frames, and
        a game that has stopped is noticed rather than fed keys.

        reset() has to come first. The constructor only asks the screen family, so
        a stage that turns out to be frozen, or to have no run behind it, gets past
        it; reset() is what refuses those, and this guard is what keeps a key from
        being injected by a caller that skipped it.
        """
        if self._phase is not _Phase.RUNNING:
            raise NotInStage("the episode is not running: call reset() before step()")
        previous = self._snapshot
        if previous is None:
            raise RuntimeError("the running episode has no observation")
        try:
            applied_action = Action(action)
            self.session.set_input(applied_action)
            stage_frames, snapshot = self._wait_for_next_snapshot()
        except BaseException:
            self._phase = _Phase.INTERRUPTED
            self._release_input()
            raise
        frames = self._advance_frame_count(stage_frames)
        self._snapshot = snapshot
        self._steps += 1
        observation = Observation(snapshot=previous)
        next_observation = Observation(snapshot=snapshot)
        if snapshot.game_over:
            self._phase = _Phase.ENDED
            self.session.set_input(Action.NONE)
        return Transition(
            observation=observation,
            action=applied_action,
            next_observation=next_observation,
            frames=frames,
            terminated=snapshot.game_over,
        )

    def pause(self) -> Observation:
        """Pause a running episode and return the observation at the frozen clock.

        The action held by the policy is released before ESC is pressed. Success
        means more than sending that key: the stage clock must stop, the run must
        still be alive, and the pause menu must be on its safe first entry. A
        caller may therefore do arbitrary CPU work only after this method returns.
        """
        if self._phase is not _Phase.RUNNING:
            raise NotInStage("the episode is not running: call reset() before pause()")
        try:
            self.session.set_input(Action.NONE)
            self.session.focus()
            restart.tap(self.session, Action.ESCAPE, seconds=restart.TAP_SECONDS)
            stage_frames = self._wait_until_paused()
            snapshot = self._look()
            if snapshot is None or snapshot.game_over:
                raise NotInStage("the run ended while the pause menu was opening")
        except BaseException:
            self._phase = _Phase.INTERRUPTED
            self._release_input()
            raise
        self._advance_frame_count(stage_frames)
        self._snapshot = snapshot
        self._phase = _Phase.PAUSED
        return Observation(snapshot=snapshot)

    def resume(self) -> Observation:
        """Resume this environment's pause and return the first live observation.

        Z is only safe while the pause cursor remains on ``Return to Game``. The
        cursor is checked immediately before the press, and success is not
        reported until the stage clock advances again and a live snapshot can be
        read. If either input leaves the outcome uncertain, the episode becomes
        interrupted and reset() is required before any later action.
        """
        if self._phase is not _Phase.PAUSED:
            raise NotInStage("the episode is not paused: call pause() before resume()")
        try:
            self.session.focus()
            self._require_own_pause()
        except BaseException:
            self._phase = _Phase.INTERRUPTED
            raise
        try:
            restart.tap(self.session, Action.SHOOT, seconds=restart.TAP_SECONDS)
            stage_frames, snapshot = self._wait_until_resumed()
        except BaseException:
            self._phase = _Phase.INTERRUPTED
            self._release_input()
            raise
        self._advance_frame_count(stage_frames)
        self._snapshot = snapshot
        self._phase = _Phase.RUNNING
        return Observation(snapshot=snapshot)

    @property
    def frames(self) -> int:
        """How many stage frames the current episode has covered.

        The game's counter can restart between stages, so this is accumulated
        rather than exposing the latest raw value.
        """
        return self._frames

    @property
    def steps(self) -> int:
        """How many steps the current episode has taken."""
        return self._steps

    def close(self) -> None:
        """Release held input and close the session this environment built."""
        if self._phase is _Phase.CLOSED:
            return
        try:
            self.stop()
        finally:
            self.session.close()
            self._phase = _Phase.CLOSED

    def stop(self) -> None:
        """Release held input and require reset() before another action."""
        if self._phase not in (_Phase.RUNNING, _Phase.PAUSED):
            return
        self._phase = _Phase.INTERRUPTED
        self.session.set_input(Action.NONE)

    def _release_input(self) -> None:
        """Release held input without replacing the failure being handled.

        A second input failure while unwinding must not hide the action, read or
        interrupt that made the caller unwind: the game state is no worse for it,
        and the first failure is the one worth reporting.
        """
        try:
            self.session.set_input(Action.NONE)
        except BaseException:
            pass

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
        state word says something coarser about the same game.
        """
        try:
            return self.session.snapshot()
        except GameplayNotActive:
            return None

    def _stuck_frame_count(self) -> int | None:
        """The value the clock stopped at when two samples agree it has, else None.

        Playing and paused share a screen family and the game raises no flag that
        a single read could see, so this is the one question here that needs two
        samples. It is asked by reset() rather than once per step - twice there
        when the clock has stopped, because the second pair is what shows a tap
        landed - and the step loop does not need it at all: it waits on the same
        counter.
        """
        earlier = self.session.stage_frames()
        time.sleep(PAUSE_SAMPLE_SECONDS)
        if self.session.stage_frames() == earlier:
            return earlier
        return None

    def _require_not_frozen(self) -> None:
        """Raise NotInStage when the stage clock has stopped, worded from where."""
        stuck = self._stuck_frame_count()
        if stuck is not None:
            raise NotInStage(
                f"the stage frame counter is not moving (stuck at {stuck}): "
                "the game is paused, loading or gone"
            )

    def _leave_pause_if_up(self) -> None:
        """Leaves a pause menu the game was left on, so an episode can start.

        Reached only once the clock has stopped, and a pause is the one of the
        three reasons in `_require_not_frozen`'s message that can be repaired
        here. The pause is not this environment's own - reset() refuses that while
        its boundary is open - it is one left behind by an update that failed with
        the stage paused, or by the operator's own ESCAPE. Nothing can be played
        behind the menu, so the key that leaves it is sent here rather than asked
        for, under the check `resume()` makes, in the same order it makes them:
        the window is focused first, so the cursor read that follows is the last
        thing before the press rather than something the focus call aged. The page
        has to be the pause menu and its cursor on `Return to Game`, because the
        Retry confirmation keeps a cursor in the same field where `0` answers `Yes`
        and ends the run (`docs/game-ui.md`). Any other page - that confirmation
        included - is refused rather than pressed through.
        """
        self.session.focus()
        screen = self.session.screen()
        if screen.kind is ScreenKind.PAUSE_CONFIRM:
            raise NotInStage(
                "the pause menu is showing its Retry confirmation: resume it manually"
            )
        if screen.kind is not ScreenKind.PAUSE_MENU:
            return
        if screen.cursor != PAUSE_MENU_RESUME:
            raise NotInStage(
                "the pause menu is not on Return to Game: resume it manually"
            )
        restart.tap(self.session, Action.SHOOT, seconds=restart.TAP_SECONDS)

    def _refuse(self) -> NotInStage:
        """The refusal to run, worded from the one read that is free.

        There are two ways to get here - the game is in the menus, or the family
        reads as a stage while no run is behind it - and the family is enough to
        tell them apart. It used to name the fine-grained screen instead, which
        cost a whole other read; the episode lifecycle does not bind the state
        word at all (see `session.Session`), and an error message is not a reason
        to bring it back.
        """
        where = "in the menus" if self.session.scene() is Scene.MENU else "in a stage with no run"
        return NotInStage(
            f"the game is {where}: enter a stage and leave the pause menu before starting the agent"
        )

    def _wait_for_next_snapshot(self) -> tuple[int, Snapshot]:
        """Wait for a new frame whose gameplay snapshot is ready.

        A run that is over stops advancing the stage clock, so the wait would
        time out on the ending; the snapshot read at that point is what says the
        run is over. Between stages the clock can change before the next stage
        object exists. That is a loading gap, not the end of the episode: release
        the held action and wait separately for the snapshot to become readable.
        """
        frame_deadline = time.monotonic() + self.frame_timeout_s
        transition_deadline: float | None = None
        input_released = False
        while True:
            frames = self.session.stage_frames()
            if frames != self._stage_frames or transition_deadline is not None:
                snapshot = self._look()
                if snapshot is not None:
                    return frames, snapshot
                if not input_released:
                    self.session.set_input(Action.NONE)
                    input_released = True
                if self.session.scene() is Scene.MENU:
                    raise NotInStage(
                        "the game entered the menus while waiting for the next stage"
                    )
                now = time.monotonic()
                if transition_deadline is None:
                    transition_deadline = now + self.transition_timeout_s
                if now >= transition_deadline:
                    raise NotInStage(
                        "the next stage did not finish loading within "
                        f"{self.transition_timeout_s:g} s"
                    )
            elif time.monotonic() >= frame_deadline:
                snapshot = self._look()
                if snapshot is not None and snapshot.game_over:
                    return frames, snapshot
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

    def _wait_until_paused(self) -> int:
        """Wait for the pause menu to come up, then return the clock it stopped.

        The menu is a page of its own, so this polls the screen the game says it
        is driving rather than sampling the frame counter twice: the page coming
        up *is* the pause, and the clock is read once it is there - frozen because
        the menu is up, not because two samples agreed.

        The page has to be the pause menu at its safe entry. The confirmation
        behind `Retry This Game` is another page whose entry 0 is `Yes`, so a
        pause that opened onto it is refused rather than waited through: it is
        not a boundary this layer may press Z from.
        """
        deadline = time.monotonic() + self.frame_timeout_s
        while True:
            if time.monotonic() >= deadline:
                raise NotInStage(
                    f"the stage did not pause within {self.frame_timeout_s:g} s"
                )
            screen = self.session.screen()
            if screen.kind is ScreenKind.PAUSE_MENU:
                if screen.cursor != PAUSE_MENU_RESUME:
                    raise NotInStage(
                        "the pause menu opened away from Return to Game"
                    )
                return self.session.stage_frames()
            if screen.kind is ScreenKind.PAUSE_CONFIRM:
                raise NotInStage("the pause menu opened its Retry confirmation")
            if self.session.scene() is not Scene.STAGE:
                raise NotInStage("the game left the stage while pausing")
            time.sleep(self.poll_seconds)

    def _wait_until_resumed(self) -> tuple[int, Snapshot]:
        """Return the first live snapshot after the paused clock advances."""
        deadline = time.monotonic() + self.frame_timeout_s
        while True:
            frames = self.session.stage_frames()
            if frames != self._stage_frames:
                snapshot = self._look()
                if snapshot is not None and not snapshot.game_over:
                    return frames, snapshot
                if snapshot is not None:
                    raise NotInStage("the run ended while leaving the pause menu")
            if self.session.scene() is not Scene.STAGE:
                raise NotInStage("the game left the stage while resuming")
            if time.monotonic() >= deadline:
                raise NotInStage(
                    f"the stage did not resume within {self.frame_timeout_s:g} s"
                )
            time.sleep(self.poll_seconds)

    def _require_own_pause(self) -> None:
        """Refuse to press Z unless the game is still at our frozen boundary.

        The pause menu is the only page Z may be sent from, and only with the
        cursor on `Return to Game`. The confirmation behind `Retry This Game`
        keeps its cursor in the same field and opens on `No`, and a Z on its 0
        would answer `Yes` - so its page is a refusal like any other.
        """
        if self.session.scene() is not Scene.STAGE:
            raise NotInStage("the game left the paused stage: resume it manually")
        snapshot = self._look()
        if snapshot is None or snapshot.game_over:
            raise NotInStage("the paused run is no longer live: resume it manually")
        frames = self.session.stage_frames()
        if frames != self._stage_frames:
            raise NotInStage(
                "the stage clock moved after pause(): resume the game manually"
            )
        screen = self.session.screen()
        if (
            screen.kind is not ScreenKind.PAUSE_MENU
            or screen.cursor != PAUSE_MENU_RESUME
        ):
            raise NotInStage(
                "the pause menu is not on Return to Game: resume it manually"
            )

    def _advance_frame_count(self, stage_frames: int) -> int:
        """Accumulate a raw stage clock that can restart between stages."""
        if stage_frames > self._stage_frames:
            frames = stage_frames - self._stage_frames
        elif stage_frames < self._stage_frames:
            frames = max(1, stage_frames)
        else:
            frames = 0
        self._frames += frames
        self._stage_frames = stage_frames
        return frames

    def _leave_game_over(self) -> None:
        """Clears whatever ended the run, whatever kind of ending it was.

        The two endings wear the same stage family: a plain game over, which has no
        menu of its own, and the Score Ranking name entry the game opens when the
        run reached the difficulty's top ten. The high score is not what tells them
        apart - it is a different and higher bar, tracked in a flag of its own - so
        the screen is read instead: a name entry is walked here, under
        `on_name_entry`, and an ending's own menu is left to the restart sequence.
        A refusal that comes out of that sequence is this layer's own kind of "no":
        the game ended up somewhere no key may be sent from.
        """
        while True:
            if self.session.screen().kind is ScreenKind.NAME_ENTRY:
                if self.settings.on_name_entry is OnNameEntry.STOP:
                    raise NotInStage(
                        "the run reached the ranking and OnNameEntry is STOP: "
                        "the game is waiting for a name"
                    )
                restart.leave_name_entry(
                    self.session, timeout_s=self.settings.name_entry_timeout_s
                )
            try:
                restart.leave_game_over(
                    self.session, timeout_s=self.settings.confirm_timeout_s
                )
                return
            except restart.NotAnEnding as misplaced:
                # `game_over` becomes visible before the ranking has necessarily
                # installed its name-entry screen object. If that transition finishes
                # while leave_game_over() is looking for the ordinary ending menu,
                # dispatch the newly visible screen instead of treating it as an
                # unrelated menu. Any other refusal is still a hard boundary: it
                # may be the title or a setup screen, where pressing on is unsafe.
                if self.session.screen().kind is not ScreenKind.NAME_ENTRY:
                    raise NotInStage(str(misplaced)) from misplaced
