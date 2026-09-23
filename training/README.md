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
  from the screen, so the episode lifecycle asks `record_broken()` instead. Its
  tagged C result keeps a read failure distinct from a clear flag, because an
  unknown ending must not be confirmed as though no name were due.

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

A run continues across its stage-loading screens. The game's frame counter can
change before the next stage object is ready, so `step()` releases the current
keys and waits up to `transition_timeout_s` for a readable snapshot instead of
ending the episode. A real menu is still refused immediately.

The name entry row is the one real unknown left, and it is the one the whole flag
detour was for. It needs a session against the game to pin down: how many
characters it wants, and whether it can be skipped.

## The evasive policy

`EvasivePolicy` is the scripted player. Every frame it enumerates the key
combinations the game accepts - eight directions plus standing still, each at
focus speed and at full speed - walks each candidate a dozen frames into the
future past the bullets, lasers and enemies in the snapshot, and drops the ones
that are hit. The survivors are ranked by a value function: low on the field and
centred is safe, lined up under an enemy is where the shots land, near a resource
point is worth the risk, and standing in the path of a bullet that is on its way
costs. Only when no candidate survives does the ranking fall back on lasting the
longest, and only when even that runs out within a few frames does it spend a
bomb. On a quiet field - at most one enemy and no bullet within 33 pixels of the
player - it also alternates the shoot key every frame. This is TH10AI's dialogue
heuristic: repeated fresh Z presses advance dialogue while holding Z does not.

The arithmetic is in `training/dodging.py` as pure functions over a Snapshot, so
it can be tested without a game and tuned in one place. The constants and the
shape of the value function come from TH10AI
(`D:\projects\TH10AI\Src\GameManager.cpp`), a rule-based player for this same
game that reads the same memory through the same offsets. Three differences are
deliberate: the value is read once per move, at the position it ends on, rather
than at every state a BFS passes through, which is what keeps the search cheap
enough for Python; the walk looks twelve frames ahead where the reference searches
four, which is still short enough that a move is not extrapolated far past what
one key press can actually promise; and the bomb cooldown is a frame counter,
because the snapshot carries no invulnerability flag to read.

Two of these are worth knowing before tuning, because both were measured and both
went the way that is not obvious. `horizon` wants to be *short*: lengthening it
to a second of play made the policy worse, not better - it starts steering around
bullets that are still far away, extrapolating a single held key for a second of
flight, and in a stage whose bullets mostly fall it ends up living at the bottom
edge. And the term that actually stops the policy standing still is
`attack_value`, the penalty for a spot a bullet is flying towards: without it the
value function parks the player in the middle of the bottom half and waits, which
looks exactly like a policy that has stopped working. `INCOMING_RADIUS` is
deliberately narrow for the same reason: a band wide enough to see the whole stage
starts moving the player around the whole stage.

Two numbers are assumptions rather than measurements, and both say so next to
themselves: how a bullet's own velocity maps onto the frames the walk counts in
(`BULLET_LEAD`), and what a laser's fields mean, which `laser_box()` copies box
for box from the reference.

## Where it stands

Both entry points expect a game that is already in a stage:

```powershell
pixi run python -m training.evaluate --episodes 3 --policy evasive
pixi run python -m training.collect --out runs/first.jsonl
```

- `training/policy.py` - `FixedPolicy`, `EvasivePolicy` and `RandomPolicy`,
  behind one `Policy` boundary.
- `training/dodging.py` - the geometry `EvasivePolicy` decides with: hazard
  boxes, the frame a move is first hit on, and the value of a spot.
- `training/loop.py` - `run_episode()` / `run_episodes()`, which hand complete
  state-action-state transitions and every episode to callbacks.
- `training/dataset.py` - the versioned JSON representation of a transition,
  including every field in both memory-backed observations.
- `training/collect.py` - writes those transitions as JSONL rows under `runs/`.
- `training/train.py` - a stub: nothing here trains a model yet.

Open: the name entry sequence above, tuning against a real stage - the entry
points run, but nothing here has been tuned with the game in front of it, and
`BULLET_LEAD` and the laser box are still guesses - and a model, which swaps in
behind `Policy` without the loop noticing.
