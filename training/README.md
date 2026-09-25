# The scripted agent

This directory holds the agent layer: a rule-based player that drives the game
from memory reads alone. It exists for two jobs, and both shape the design.

The first job is to prove the loop. Read an observation, choose an action, inject
it, and notice when the run is over: a scripted player is the cheapest thing that
exercises all of that end to end.

The second job is to be a teacher. A deterministic policy that plays a real stage
produces `(observation, action, reward)` rows without a model in the loop, so it
can generate the first dataset and later act as the baseline a model has to beat.

The learned layer starts with online DAgger: the model visits states, the evasive
policy labels them, and supervised updates teach the two action heads. The same
network already has a value head so PPO can reuse it after imitation has produced
a policy capable of surviving long enough to collect useful rollouts. Nothing
below depends on that training choice: scripted baselines and learned policies use
the same observation, action and episode boundaries.

## Layers

```
Policy            decide an action from an observation
  |
Agent loop        per frame: observe, act, reward, record; per run: ending
  |
Episode lifecycle when does a run end, and what happens then
  |
Th10Env           actions and memory-backed transitions (python/auto_th10/env.py)
  |
Session           process handle, memory reads, input injection
```

The `Policy` boundary is the one that matters for the future: it takes an
observation and returns an action, so a script and a model are interchangeable
behind it and nothing below it changes. `Th10Env` applies that action and returns
a transition with both observations, the applied action, elapsed game frames and
the terminal flag. Reward belongs to the runner or training adapter consuming
that transition. An observation carries the complete memory snapshot; the PPO
feature encoder turns its variable-length entity lists into the fixed tuple
consumed by a model.

Two facts about the layer underneath are worth knowing here rather than reading
again in the sources:

- The game addresses, the offsets inside the structures they point at, and the
  traps that come with them (a neighbour word that looks like the screen family,
  a high score that follows the current one) are documented in `src/internal.h`
  and in the public header.
- A plain game over and the ranking's name entry look alike on screen, so the
  episode lifecycle asks the game which screen it is driving instead of guessing:
  `Session.screen()` names it and hands back that screen's cursor. That cursor is also
  writable (`Session.set_screen_cursor()`), which is what turns leaving the name entry
  from eighteen key presses into one step and a confirm.
- The high score is what the game tracks in a flag of its own, and reading that flag
  as "a name is due" is exactly what walked a restart into the name entry: the
  ranking takes runs the high score does not, so the flag was clear while the game
  waited for a name. The flag is no longer read at all.
- Failures and answers are told apart at the binding rather than here: a snapshot
  read that fails because no stage is loaded raises `GameplayNotActive`, a closed
  session raises `SessionClosedError`, and a tag the binding does not know raises
  `SystemError`. Only the first means "the game is between runs", which is why the
  episode lifecycle catches that one by name instead of catching every
  `RuntimeError` and reading a closed session as a loading screen.

## Restart sequences

Keystrokes are needed to get from one run to the next, and they have to be driven
by observation rather than by sleeping a fixed time: the game's own transitions
are the only reliable clock. They live in `python/auto_th10/restart.py`, next to
the binding, because the episode lifecycle is what calls them.

| Transition | Keys | Status |
| --- | --- | --- |
| Game over, no record, into a new run | open the ending's menu, write the cursor onto 継続する, `<Z>` | implemented in `restart.leave_game_over()`; the entry is read before it is confirmed |
| Name entry, out of it | write the cursor onto 終, `<Z>` | implemented in `restart.leave_name_entry()`; `OnNameEntry.LEAVE` does it, `STOP` refuses |
| Title into a stage | `<Z>` through the menus | not implemented on purpose: entering a stage stays with the player |
| Cold start into a stage | launch, wait out the ~15 s logo, then the above | `th10ctl launch` starts the game; the stage is entered by hand |

Entering a stage is a deliberate hole rather than an unfinished one: a script that
walks menus has to know how many presses each screen takes and what they select,
and a wrong guess lands in a shot type or a difficulty nobody chose. The
environment therefore refuses to run anywhere but in a playing stage, raising
`NotInStage` for the menu, an unknown screen, and a pause menu whose cursor has
moved off `Return to Game`.

The one pause it leaves of its own accord is a game that was already sitting in
one: an update that failed while the stage was paused, or the operator's own
ESCAPE. No run can start behind that menu, so `reset()` taps `Return to Game` -
the entry its cursor opens on - after the checks `resume()` makes, and refuses
anything else on that screen rather than pressing through it.

`Th10Env.pause()` is the controlled exception used at an in-memory trainer's
rollout boundary. The environment enables the background-input bridge when it
opens its session, so it releases the policy's held action and taps `ESCAPE`
without taking focus, then returns only after the stage clock has stayed frozen
for a full sample interval and the pause cursor has been read on `Return to Game`.
Its matching `resume()` rechecks the stage family, live run,
unchanged frozen clock, menu and cursor before tapping `SHOOT`, then waits for the
clock and snapshots to become live. Both return the observation at their verified
side of the boundary; `MemoryGymEnv` encodes and exposes those values so the
trainer never reaches through it to the session. If confirmation fails after an
input, the environment is interrupted rather than guessing where that input
landed. Calling `reset()` while paused refuses without discarding this boundary,
so the caller can still resume it safely.

A death can land in the short interval between the rollout's final live snapshot
and the pause menu opening. That boundary is terminal rather than a training
failure: the environment observes `game_over` while waiting for the page, closes
a pause only if its safe `Return to Game` entry is actually present, accounts for
the terminal reward and lets DAgger update and restart normally. If death made
ESCAPE a no-op, it sends no further key on the ending. An unreadable snapshot or
an unexpected page is still refused; only an observed `game_over` takes this
path.

The game over menu is walked for the same reason. Its cursor opens on
`Quit and Return to Select`, so a driver that only keeps pressing `<Z>` retreats to
the title and then has to walk RANK, PLAYER SELECT and WEAPON SELECT to get back
into a run - the three screens this layer has no business choosing on. The
sequence moves onto `継続する` instead, which starts the next run on the spot with
the difficulty and the character already chosen, and it finds that entry by
reading the cursor rather than by counting presses from where it expects the
cursor to be. A game that does end up at the title is refused rather than pressed
on from: the entry one press away from that path is the replay-save list, whose
name entry has no scripted way out (`docs/game-ui.md`).

The name entry behind the same ending is the case that was left open for a long
time, and what closed it was reading the cursor instead of counting presses: the
screen is a grid the game lays out 13 cells per row, `終` is its last cell, and its
cursor can be written directly. `restart.leave_name_entry()` writes and verifies
that cell before it confirms, which records the name the game already holds - nothing
types into the grid - and puts the ending's menu back with its cursor on
`継続する`, ready for the sequence above. What the grid is *not* used for is
choosing a name: `OnNameEntry.LEAVE` answers the ranking, and a caller that wants
its own name has to say so in the game by hand.

A game over is the exception, and only when it was already on screen before the
first episode: that ending was left there by an earlier attempt, and clearing it is
a fixed sequence of presses rather than a choice, so `reset()` does it under any
settings. An ending the environment produced itself is a different matter -
`OnDeath.STOP` leaves it alone. That is why one episode keeps its ending on screen
while several restart between them, which is what `Settings.for_episodes()` decides.

A run continues across its stage-loading screens. The game's frame counter can
change before the next stage object is ready, so `step()` releases the current
keys and waits up to `transition_timeout_s` for a readable snapshot instead of
ending the episode. A real menu is still refused immediately.

The name entry row used to be the unknown this layer stopped on, and the flag
detour described above was an attempt to answer it from the wrong signal. It is
measured now (`docs/game-ui.md`): the grid is 13 cells per row, `終` is its last
cell, and the cursor is readable, which is all a sequence needs to walk it. What is
still not modelled is the name itself - nothing types into the grid.

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
pixi run python -m training.train --iterations 100
pixi run python -m training.evaluate_model --checkpoint runs/dagger.pt
```

Training writes a separate timestamped TensorBoard run below
`runs/tensorboard/`. Start the monitoring page in another terminal and open
`http://localhost:6006/`:

```powershell
pixi run tensorboard --logdir runs/tensorboard
```

The dashboard separates imitation losses, DAgger state, rollout behavior and
completed-episode results. Use `--tensorboard-dir PATH` to put the timestamped
run under a different root, then pass that same root to
`pixi run tensorboard --logdir PATH`. The console summary remains available for
quick checks.

`--iterations` defaults to `inf`: the run then ends only when the game refuses
to play (`NotInStage`) or at Ctrl+C, which names the iteration it stopped in.
Held input is released either way, and an interrupt during an update leaves the
game paused - the next start finds that menu and leaves it from `Return to Game`
itself.

- `training/policy.py` - `FixedPolicy`, `EvasivePolicy` and `RandomPolicy`,
  behind one `Policy` boundary.
- `training/dodging.py` - the geometry `EvasivePolicy` decides with: hazard
  boxes, the frame a move is first hit on, and the value of a spot.
- `training/loop.py` - `run_episode()` / `run_episodes()`, which hand complete
  state-action-state transitions and every episode to callbacks.
- `training/dataset.py` - the versioned JSON representation of a transition,
  including every field in both memory-backed observations.
- `training/collect.py` - writes those transitions as JSONL rows under `runs/`.
- `training/rl/features.py` - feature schema version 2: a bounded fixed-length
  tuple made from the memory snapshot plus measured frames since the model last
  bombed. It keeps configurable nearest-entity prefixes and pads them with
  explicit masks. A checkpoint records the schema version and entity limits that
  define the tuple's exact layout.
- `training/rl/actions.py` - action schema version 1: 17 valid movement choices
  and a binary bomb choice. Shooting stays outside the learned action for now so
  combat can hold it and dialogue can pulse it without teaching the model that
  game-specific convention.
- `training/rl/teacher.py` - the reusable DAgger annotation boundary. Its
  `EvasiveTeacher` projects `EvasivePolicy` decisions onto the model's movement
  and bomb heads, then advances cooldown state from the learner action reported
  through `feedback()` rather than from advice the learner may have ignored.
- `training/rl/model.py` - one shared MLP trunk with independent 17-way movement,
  binary bomb and scalar value heads. Imitation trains the action heads; PPO can
  later train all three without changing the checkpoint shape.
- `training/rl/checkpoint.py` - atomic checkpoint writes and strict loading. A
  loader reconstructs all three specs from versioned metadata, verifies that the
  feature size and weight shapes agree, and returns the optimizer state without
  executing arbitrary checkpoint code.
- `training/rl/imitation.py` - movement and bomb cross-entropy updates. Bomb
  positives are both sampled deliberately and weighted because the useful label
  is rare.
- `training/rl/dagger.py` - bounded aggregation, beta-mixture rollouts and the
  fixed-horizon pause/update/resume transaction. A failed update deliberately
  leaves the game paused; a collection failure stops the environment and releases
  held input.
- `training/rl/rewards.py` - the first shaped reward: small survival progress,
  clipped positive score progress, and explicit penalties for a lost life, a
  bomb and game over. Every term remains visible in the step metadata so a
  training run can show what the policy is actually optimizing.
- `training/rl/gym_env.py` - the Gymnasium adapter. Its observation is the
  bounded feature vector, its `MultiDiscrete([17, 2])` action is movement plus
  bomb, and it releases held input whenever an episode terminates or is
  truncated. It is the only module that imports Gymnasium and NumPy, but
  `training.rl` re-exports it, so importing anything from that package - the
  action, feature and reward protocols included - needs them.
- `training/shooting.py` - the fixed combat/dialogue shooting rule shared by the
  scripted baseline and the Gymnasium adapter.
- `training/train.py` - the online DAgger entry point. It decays the probability
  of executing teacher actions and atomically writes model, optimizer and schema
  metadata to `runs/dagger.pt` after every iteration. It also writes losses,
  rollout behavior, completed-episode results and run hyperparameters for
  TensorBoard.
- `training/evaluate_model.py` - deterministic checkpoint evaluation. The model
  alone controls movement and bomb; the evasive teacher only labels those same
  states so the report can include movement agreement and bomb precision/recall
  beside score, survival frames and bomb count.

`evaluate_model --max-steps` is a bounded single-episode diagnostic. It cannot be
combined with multiple episodes: reaching that artificial limit releases input
but does not end the physical run, so resetting model and teacher history there
would mislabel a continuation as a fresh game.

The adapter is constructed directly after installing the training extras:

```python
from training.rl.gym_env import MemoryGymEnv

env = MemoryGymEnv(action_repeat=1, max_steps=60_000)
observation, info = env.reset()

env.close()  # releases the game session the adapter built
```

`pixi run test-training` runs the RL protocol, reward, model, DAgger and adapter
suites. The Pixi environment locks Gymnasium, NumPy and CPU PyTorch.

`action_repeat` defaults to one because bullet avoidance needs frame-level
control. `max_steps` counts model decisions rather than raw game frames; the
`frames` and `delta_frames` info fields retain the actual stage-clock progress.
The reward constants are starting scales, not tuned claims. Evaluation should
continue reporting raw score and survived frames independently of shaped reward.
DAgger does not accept `max_steps`: its own fixed `horizon` is the resumable
rollout boundary, while a Gym truncation releases input but cannot freeze the
still-running game for an update.

The teacher is queried alongside the learner rather than substituted for it:

```python
import numpy as np

features, _ = env.reset()
teacher_label = teacher.annotate(env.raw_observation)
learner_action = model.act(features)  # returns ModelAction
features, reward, terminated, truncated, info = env.step(
    np.asarray([learner_action.movement, learner_action.bomb], dtype=np.int64)
)
teacher.feedback(learner_action, frames=int(info["delta_frames"]))
```

`feedback()` comes only after the action was successfully applied. This keeps the
teacher's bomb cooldown on the learner's real history and the game's actual frame
clock: ignoring a recommended bomb leaves it available as the next label, while
a learner-selected bomb starts the cooldown even when the teacher preferred not
to spend one. `reset()` clears the annotation pairing and state for a genuinely
independent trajectory; a PPO rollout boundary in the middle of the same game
must not call it.

Open: tuning DAgger against a real stage; restoring the aggregate buffer and RNG
state for full training resume; then collecting advantages and adding PPO updates
behind the existing value head. Nothing here has been tuned with the game in front
of it, and `BULLET_LEAD` and the laser box are still guesses. A name of its own for
a record worth keeping also remains outside the scripted lifecycle.
