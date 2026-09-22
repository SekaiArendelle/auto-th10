# The scripted agent

This directory holds the agent layer: a rule-based player that drives the game
from memory reads alone. It exists for two jobs, and both shape the design.

The first job is to prove the loop. Read an observation, choose an action, inject
it, and notice when the run is over: none of that has been exercised end to end
yet, and a scripted player is the cheapest thing that can exercise it.

The second job is to be a teacher. A deterministic policy that plays a real stage
produces `(observation, action, reward)` rows without a model in the loop, so it
can generate the first dataset and later act as a baseline that a model has to
beat.

A model is deliberately out of scope here. Nothing below needs one, and the
interfaces are shaped so that swapping one in later does not move any other
layer.

## What the game gives us

Everything here is verified against `th10.exe` 1.00a by reading the values while
the game ran in each state. The addresses live in `src/internal.h`.

| Value | Address | What it tells us |
| --- | --- | --- |
| Screen family | `0x00491FB8` | `0x4` title and menus, `0x7` a stage |
| Remaining lives | `0x00474C70` | `2, 1, 0` alive, then `-1` once the run is over |
| Stage frame counter | `0x00474C88` | advances while playing, freezes while paused |
| Event flags | `0x00474CA0` | bit 2 is raised when the score passes the high score |
| Score / high score / power | `0x00474C44` / `0x00474C40` / `0x00474C48` | gameplay numbers |

Two traps are worth repeating because both cost real time to find:

- `0x00491FBC` sits next to the screen family, holds the opposite value at the
  same moments, and is not read by the game at all. Reading it inverts every
  decision.
- The high score at `0x00474C40` follows the current score while the game runs,
  because the display keeps it topped up. Comparing the two therefore always
  comes out equal and never reveals a record; the flag bit above is the only
  usable signal.

### The three endings are not three screens

The screen family cannot separate a plain game over from the name entry that a
record-breaking run gets. Both run inside family `0x7`, and the name entry shows
the same screen the run before it did. What distinguishes them is the flag word:

| Situation | `lives` | `0x00474CA0` bit 2 | Screen family |
| --- | --- | --- | --- |
| Stage running | `2`, `1`, `0` | either | `0x7` |
| Game over, no record | `-1` | clear | `0x7` |
| Game over, new record | `-1` | set | `0x7` |
| Waiting for a name | `0` | set | `0x7` |

That table is the whole reason for the flag: a restart that only looks at the
screen and the lives counter cannot tell which keys the game is waiting for.

## Layers

```
Policy            decide an action from an observation
  |
Agent loop        per frame: observe, act, record; per run: reward, ending
  |
Episode lifecycle when does a run end, and what happens then
  |
Th10Env           observations and actions (python/auto_th10/env.py)
  |
Session           process handle, memory reads, input injection
```

The `Policy` boundary is the one that matters for the future: it takes an
observation and returns an action, so a script and a model are interchangeable
behind it and nothing below it changes. Observations are a struct rather than a
bare array so that a memory-reading policy and a screen-reading policy can both
be served without touching the loop:

```python
@dataclass
class Observation:
    snapshot: Snapshot             # for a memory-reading policy
    frame: Frame | None = None     # for a screen-reading policy, opt-in
```

## Automatic restarting

Restarting is a framework concern, not a per-experiment detail, because different
uses want opposite behaviour and the difference is not cosmetic.

Watching a validation run, the interesting moment is the ending, so the loop
should stop and leave the screen alone. Collecting training data, the ending is
noise, so the loop should get back into a stage as fast as it can. Collapsing
both into one boolean would hide that, so there are two settings:

```python
class OnDeath(Enum):
    STOP = auto()       # leave the ending on screen for a human or a capture
    RESTART = auto()    # drive back into a stage and keep going

class OnNameEntry(Enum):
    STOP = auto()       # a record is an event worth surfacing, not a hiccup
    TYPE = auto()       # enter a name and carry on
```

Two settings rather than one because a record-breaking ending is not just
another ending. It is the one case where the game stops to ask for something, and
a validation harness wants to know it happened, while a collector wants it over
with. The `record_broken()` query exists precisely to make that call:

```python
if state is State.GAME_OVER:
    if session.record_broken():
        handle_name_entry()      # OnNameEntry decides what that means
    else:
        confirm_game_over_menu()
```

Presets keep the common pairs from being spelled out at every call site:

```python
TRAIN_PRESET = Settings(on_death=OnDeath.RESTART, on_name_entry=OnNameEntry.TYPE)
EVAL_PRESET = Settings(on_death=OnDeath.STOP, on_name_entry=OnNameEntry.STOP)
```

## Restart sequences

Keystrokes are needed to get from one run to the next, and they have to be driven
by observation rather than by sleeping a fixed time: the game's own transitions
are the only reliable clock.

| Transition | Keys | Status |
| --- | --- | --- |
| Game over, no record, into a new run | `<Z>` on the default entry | verified |
| Name entry, out of it | arrows to pick, `<Z>` to accept | **not yet explored** |
| Title into a stage | `<Z>` through the menus | partly known (four presses reached a stage) |
| Cold start into a stage | launch, wait out the ~15 s logo, then the above | known to work in `th10ctl launch` |

The name entry row is the one real unknown, and it is the one the whole flag
detour was for. It needs a session against the game to pin down: how many
characters it wants, and whether it can be skipped.

## Work plan

1. Expose what the agent needs to Python. Done: `state()`, `record_broken()` and
   `capture()` on `Session`.
2. Pin down the restart sequences above against a running game.
3. Implement the episode lifecycle in `Th10Env.reset()`, which currently raises
   because reset was never built:

   ```python
   if snapshot.game_over:
       raise RuntimeError("automatic game/menu reset is not implemented yet")
   ```

4. Add a `ScriptedPolicy` that first holds a fixed pattern, to prove observation,
   decision, injection and restart all line up end to end.
5. Grow that policy into an evasive one: repel from the nearest bullets, aim at
   the nearest enemy, hold focus for precision, spend bombs when cornered.
6. Add the collection loop and settle the dataset schema, which
   `training/collect.py` deliberately leaves open until the reset behaviour is
   validated.

Steps 1 and 2 come before any policy work on purpose. Writing a policy against a
reset that does not exist would mean rebuilding it once the real one lands.
