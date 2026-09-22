# The game's own screens, and how to walk them

Everything below was measured against `th10.exe` 1.00a the same way the addresses
in `src/internal.h` were: by pressing keys against a running game and taking a
screenshot after each press. The screenshots themselves are not committed; what
they showed is.

The file exists because the screens are the part of driving the game that code
cannot check. `restart.py` knows the key sequences it needs, but nothing said
which screen follows which, which entry is the default, or where a stray confirm
press lands. A wrong press here does not corrupt anything - it walks the game
somewhere nobody asked for, into a replay save or into a difficulty nobody chose.

## Keys we can send

`th10_set_input()` sends the direction keys, `SHOOT` (Z), `FOCUS` (Shift), `BOMB`
(X) and `ESCAPE`, as scan codes. `ESCAPE` pauses a stage, and it is also the key
that backs out of a screen the game is waiting on. It exists in the action set
because without it a menu the agent had walked into could only be left with
`SHOOT`, and only when its default entry happened to be the way back.

Keys only reach the game while its window owns the foreground. `th10_focus()`
does that and also works around a Chinese IME on the game's thread, and it can
fail with `AllowSetForegroundWindow` error 5 when Windows refuses the foreground
request - retry it, and re-focus periodically while driving, because a window that
has scrolled behind another stops receiving keys silently.

Menu presses want a short hold, 150-200 ms: long enough for a frame to see, short
enough not to be read as two. Holding a direction longer than that starts
repeating it, which moves a cursor several cells at once.

## What the state word can and cannot tell you

- `TH10_STATE_PLAYING` means the stage clock is running. It is **not** proof that
  somebody is playing: the title screen's own demo advances the same clock and
  reads as `PLAYING` too. The demo is only distinguishable on screen, where it
  draws a `Demo Play` caption over the field.
- `TH10_STATE_GAME_OVER` freezes the stage clock (measured: 4720 twice, half a
  second apart).
- The screens between runs have no stage behind them, so `th10_read_snapshot()`
  fails and the binding raises `RuntimeError: gameplay is not active`. That is
  not a fault to report - it is the answer "the game is between runs", which is
  how `restart.leave_game_over()` reads it.
- `TH10_STATE_MENU` covers the title menu and those in-between screens alike, so
  it cannot be used to tell them apart.

## Title menu

Eight entries, in order: `GAME START`, `Extra Start`, `Practice Start`, `Replay`,
`Player Data`, `Music Room`, `Option`, `Quit`. The locked ones are dimmed until
they are unlocked. `SHOOT` on `GAME START` goes straight to RANK, not to a
character select.

If the menu is left alone for a while the game starts its own demo. `SHOOT`
takes it back to the title.

## RANK - difficulty

`Easy`, `Normal`, `Hard`, `Lunatic`. The one that is highlighted is the choice,
and every screen from here on repeats it in the bottom-left corner, which is the
cheapest way to see which difficulty a run is on.

## PLAYER SELECT - character

Two entries: `博麗霊夢` and `霧雨魔理沙`, with a line of description under the
highlighted one.

## WEAPON SELECT - shot type

Three entries per character, described under the highlight - measured for Marisa:
`高威力装備`, `貫通装備` (`イリュージョンレーザー`, option `近接配置`) and
`魔法使い装備`. `SHOOT` starts the run: a loading screen, then the stage.

## Game over

`満身創痍！ Game Over!` over the field, and a menu of three entries:

1. `継続する` - start the next run. This is the default, so a single `SHOOT`
   straight after the ending picks it.
2. `リプレイを保存する` - save a replay of the run that just ended.
3. `タイトル画面に戻る` - back to the title.

The cursor is the brighter entry and the up/down keys move it. Entry 2 is the
one to keep away from: a second `SHOOT` lands in the replay-save flow, which is
two screens deep and has no way back out that does not go through a name entry.

## Replay save, and the name entry behind it

`リプレイを保存する` opens a list of 25 slots (`No. 01` … `No. 25`); up/down
walks the list and wraps around at both ends. `SHOOT` on a slot opens the name
entry for that replay.

The name entry is a grid of characters (A-Z, a-z, 0-9, punctuation) with:

- the direction keys moving a cursor over the grid,
- `SHOOT` entering the highlighted character into the name,
- **Escape acting as backspace** - it deletes a character and does not leave,
- a `終` cell at the bottom right, which has to be selected and confirmed to
  finish.

Measured on the grid: two `Escape` presses shortened a name from `AAAA` to `AA`,
and `SHOOT` with the cursor on `m` produced `AAm`. Twelve presses of the right key
from the first column did land on the last column, but four presses of up did not
land where the count said. Columns are reliable, rows are not: a held direction
slips past the repeat boundary and moves several cells, and a row wraps into the
next one. Counting presses to reach the `終` in the bottom-right corner is
therefore not a plan, and neither is leaving by pressing `Escape` a few times - it
only eats the name one character at a time. **An agent should never confirm its
way into this screen**: the only way out is `終`, and reaching it by script was
not achieved; the one time it was left, it was walked by hand.

## Pause menu

Reachable now that `ESCAPE` is in the action set - `th10ctl hold escape 150`, or
`Action.ESCAPE` from Python.

Its entries are still not written down: the one probe that reached the screen had
no screenshot taken, and the debug keyboard could not reopen it afterwards. The
recorded fact is that the default entry resumes the run, which is how a paused run
was brought back during debugging.

Two things that do **not** pause the game, both measured, because they look like
they should: leaving it alone in a stage, and giving another window the
foreground. The game keeps playing in the second case, and the keys stop arriving
at the same time, since a window that has lost the foreground is not sent any.
