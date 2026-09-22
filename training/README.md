# The scripted agent

This directory holds the agent layer: a rule-based player that drives the game
from memory reads alone. It exists for two jobs, and both shape the design.

The first job is to prove the loop. Read an observation, choose an action, inject
it, and notice when the run is over: a scripted player is the cheapest thing that
exercises all of that end to end.

The second job is to be a teacher. A deterministic policy that plays a real stage
produces `(observation, action, reward)` rows without a model in the loop, so it
can generate the first dataset and later act as the baseline a model has to beat.

A model is deliberately out of scope here. Nothing below needs one, and the
interfaces are shaped so that swapping one in later does not move any other
layer.

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
be served without touching the loop; the screen is an opt-in field that nothing
fills in yet.

Two facts about the layer underneath are worth knowing here rather than reading
again in the sources:

- The game addresses, the offsets inside the structures they point at, and the
  traps that come with them (a neighbour word that looks like the screen family,
  a high score that follows the current one) are documented in `src/internal.h`
  and in the public header.
- A plain game over and the name entry a broken record gets cannot be told apart
  from the screen, so the episode lifecycle asks `record_broken()` instead;
  `th10_read_record_broken()` in `include/auto_th10/auto_th10.h` records why.

## Restart sequences

Keystrokes are needed to get from one run to the next, and they have to be driven
by observation rather than by sleeping a fixed time: the game's own transitions
are the only reliable clock. They live in `python/auto_th10/restart.py`, next to
the binding, because the episode lifecycle is what calls them.

| Transition | Keys | Status |
| --- | --- | --- |
| Game over, no record, into a new run | `<Z>` on the default entry | verified; implemented in `restart.leave_game_over()` |
| Name entry, out of it | arrows to pick, `<Z>` to accept | **not yet explored**; `restart.type_name()` raises, so `OnNameEntry.TYPE` is unusable |
| Title into a stage | `<Z>` through the menus | not implemented on purpose: entering a stage stays with the player |
| Cold start into a stage | launch, wait out the ~15 s logo, then the above | `th10ctl launch` starts the game; the stage is entered by hand |

Entering a stage is a deliberate hole rather than an unfinished one: a script that
walks menus has to know how many presses each screen takes and what they select,
and a wrong guess lands in a shot type or a difficulty nobody chose. The
environment therefore refuses to run anywhere but in a playing stage, raising
`NotInStage` for the menu, the pause menu and an unknown screen.

A game over is the exception, and only when it was already on screen before the
first episode: that ending was left there by an earlier attempt, and clearing it
is one `<Z>` rather than a choice, so `reset()` does it under any settings. An
ending the environment produced itself is a different matter - `OnDeath.STOP`
leaves it alone. That is why one episode keeps its ending on screen while several
restart between them, which is what `Settings.for_episodes()` decides.

The name entry row is the one real unknown left, and it is the one the whole flag
detour was for. It needs a session against the game to pin down: how many
characters it wants, and whether it can be skipped.

## Where it stands

Both entry points expect a game that is already in a stage:

```powershell
pixi run python -m training.evaluate --episodes 3 --policy evasive
pixi run python -m training.collect --out runs/first.jsonl
```

- `training/policy.py` - `FixedPolicy`, `EvasivePolicy` and `RandomPolicy`,
  behind one `Policy` boundary.
- `training/loop.py` - `run_episode()` / `run_episodes()`, which hand every step
  and every episode to callbacks.
- `training/collect.py` - JSONL rows under `runs/`, with a schema that is still
  provisional.
- `training/train.py` - a stub: nothing here trains a model yet.

Open: the name entry sequence above, the dataset schema until a real collection
run says what the training side needs from it, and a model, which swaps in behind
`Policy` without the loop noticing.
