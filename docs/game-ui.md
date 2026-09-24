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

Menu presses want a short hold - `restart.tap()` uses 100 ms, and everything below
was measured with 150-200 ms, which is the window that works: long enough for a
frame to see, short enough not to be read as two. Holding a direction longer than
that starts repeating it, which moves a cursor several cells at once.

## Driving the window

Keys only reach the game while its window owns the foreground, and `th10_focus()`
is what asks for it. That ask can be refused - `TH10_FOCUS_REJECTED` - and then
every press is dropped in silence: the screen does not change and no error comes
back, which reads exactly like a menu that ignores input. Ask again before each
press while a driver is running, and expect to have to force it, because Windows
does not hand the foreground to a process that nobody is touching.

What worked, repeatedly: attach to the foreground thread's input queue, ask again,
and fall back to an Alt press if that is refused.

```c
uint32_t pid;
uint32_t target_thread = GetWindowThreadProcessId(target, &pid);
ShowWindow(target, SW_RESTORE);
AttachThreadInput(GetCurrentThreadId(), target_thread, true);
bool ok = SetForegroundWindow(target);
AttachThreadInput(GetCurrentThreadId(), target_thread, false);
if (!ok) {
    keybd_event(VK_MENU, 0, 0, 0);                       /* Alt down */
    keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0);         /* Alt up */
    SetForegroundWindow(target);
}
```

A press and the screen it causes are a step apart: a capture taken the moment the
press returns can still show the previous screen, and one taken after the next
press can show the previous press's result. Either wait a second before believing
a capture or read `th10_read_state()` as well and believe that instead.

## Reading a menu

Which entry a menu has its cursor on is a colour change on a single line, and a
downscaled capture is not enough to tell it from the others. Getting this wrong
has already cost this file one answer: the game over menu's cursor was read as
sitting on `Continue` when it was on `Quit and Return to Select`, and two separate
probes then confirmed that same entry and were written up as a comparison of two.
Crop the menu out of the capture and scale it up before believing it, and where a
behavioural check exists, prefer it - pressing `down` and capturing again shows
the highlight move, and the entry under the cursor is the one whose confirm does
something.

The two menus in this game start their cursors at opposite ends (the pause menu on
its first entry, the game over menu on its last), and they share one of their three
labels. Neither the look of the menu nor the entry count says which one is up;
`TH10_STATE_PAUSED` does.

## Asking the game where it is

Three reads, and the rule is to use the cheapest one that answers the question:

| Question | Read | Cost |
| --- | --- | --- |
| A stage at all, or the menus? | `th10_read_scene()` / `Session.scene()` | one word, nothing to wait for |
| Which screen is it, and where is its cursor? | `th10_read_screen()` / `Session.screen()` | a pointer chase and a word |
| Is there a run, and is it over? | `th10_read_snapshot()` | one pass over the game's live objects |
| Still running, or frozen? | `th10_read_stage_frames()` twice | two reads, and the gap between them is yours to choose |

`th10_read_state()` (and `th10ctl state`) answers all three at once, and it is the
wrong tool for a driver: it blocks for about 120 ms whenever the answer is "playing
or paused", and what it returns is coarser than the snapshot sitting beside it. It
is kept for people and for one-shot diagnostics, and the Python binding does not
offer it at all - `Session` deliberately has no `state()`, so an agent cannot
reach for it by accident.

`th10_read_screen()` is the one read that says which screen the game is driving and
where its highlight sits. It is a different kind of read from the rest: the screen
id and the cursor live in an object the game points at from a fixed address, and
that pointer moves as the game changes screen, so it is read on every call rather
than kept. Three ids have been measured - a stage, a menu, the ranking's name entry -
and anything else is reported as unknown rather than guessed at.

That cursor is also the one thing this library writes. `th10_write_screen_cursor()`
moves it, which is the same change a direction key makes: the field is what the
game's own key handling reads and writes, so nothing else about the game is touched -
not the run, not the score, not the record. It exists because a highlight is easier
to place than to drive: reaching the name entry's `終` by pressing `right` and `down`
is eighteen presses that each have to arrive, and the press is also the part that
fails silently when the window loses the foreground.

What each value can and cannot say, all measured:

- `TH10_STATE_PLAYING` is what the title screen's own demo reports as well, so it
  is not proof that anybody is playing. The demo draws a `Demo Play` caption over
  the field, and a snapshot taken during one carries the demo's own saved state:
  `lives` at 9, full power, the ship moving by itself.
- `TH10_STATE_PAUSED` is what a frozen stage reports - which is also what a stage
  that is still loading reports, since both are "the frame counter did not move".
- `TH10_STATE_GAME_OVER` freezes the stage clock (measured: 4720 twice, half a
  second apart). The screen it belongs to still draws its `Player ★★` header, so
  the picture is not what to trust here.
- `TH10_STATE_MENU` is six screens under one value: the title, RANK, PLAYER
  SELECT, WEAPON SELECT, the replay list, and the name entry.
- The screens between runs have no stage object behind them, so a snapshot read
  fails there - `GameplayNotActive: gameplay is not active` from Python. That is
  not a fault to report: it is the answer "the game is between runs", and it is
  how the episode lifecycle tells a stage from everything else.

## Title menu

Eight entries, in order: `GAME START`, `Extra Start`, `Practice Start`, `Replay`,
`Player Data`, `Music Room`, `Option`, `Quit`. The locked ones are dimmed until
they are unlocked. The cursor starts on `GAME START` - established by pressing
`SHOOT` and landing on RANK, not by reading the highlight - and, as everywhere
else, the entry it is on is the lit one. None of that shows up in the state word.

If the menu is left alone for a while the game starts its own demo. `SHOOT` takes
it back to the title - **and if the game has already switched back to the menu by
the time the key lands, that same press confirms `GAME START`.** One press of `Z`
on a demo was measured to land two screens later, on RANK.

## Starting a run

`GAME START` leads to three screens in a row, each confirmed with `SHOOT`:

| Screen | Entries | Notes |
| --- | --- | --- |
| `RANK` (難易度の選択) | `Easy`, `Normal`, `Hard`, `Lunatic` | up/down wrap around the list |
| `PLAYER SELECT` (自機の選択) | `博麗霊夢`, `霧雨魔理沙` | the two are side by side, and **left/right** pick between them; up/down move nothing here (measured) |
| `WEAPON SELECT` (使用武器を選択) | three shot types per character | the highlighted entry describes itself; up/down wrap around |

**None of the three has a fixed default.** Each opens on a value that depends on
what was chosen before, so there is nothing here to predict: a run started by
confirming three times is not guaranteed to be the one played last, and never a
known one. Nothing in this repository drives these screens - entering a stage is
the player's decision, and `restart.py` deliberately has no sequence for it - and
they are written down so that a stray confirm is recognisable for what it is, and
so that a driver which has to sit through them knows what it is looking at.

From RANK onwards the chosen difficulty is spelled out in the bottom-left corner of
every screen, and that corner is the only way to tell which difficulty a run is on:
the setup screens will not, since none of them has a fixed default.

Measured for Reimu, the three shot types are `誘導装備` (`ホーミングアミュレット`,
option `後方配置`), `前方集中装備` (option `前方配置`) and `封印装備` (`妖怪バスター`,
option `左右＆集中配置`). `SHOOT` on one of them starts the run: a loading screen,
then the stage.

## In a stage

The clock runs, `TH10_STATE_PLAYING` is reported and keys reach the ship. The
screen draws the stage name in the corner (`Stage 1 妖怪の山の麓`) and the track
title over the field at the start; neither is anything the snapshot reports, and
neither is needed to drive.

## Pause menu

`ESCAPE` opens it, and `TH10_STATE_PAUSED` confirms it is up. Three entries, each
with its English underneath:

| Entry | English | What it does |
| --- | --- | --- |
| `一時停止を解除する` | `Return to Game` | resumes the run |
| `タイトル画面に戻る` | `Quit and Return to Select` | back to the title |
| `最初からやり直す` | `Retry This Game` | asks a confirmation, then also ends at the title |

**The cursor starts on the first entry here**, so a single `SHOOT` on a pause menu
nobody has touched resumes the stage - measured. That is the opposite of the game
over menu, whose cursor starts on its **last** entry, and the two menus look
alike. Check which line is lit rather than assuming.

`Retry This Game` does not restart anything from this menu. It opens a
confirmation - `本当に / Really?` over `はい / Yes` and `いいえ / No`, **with the
cursor on `No`** - and answering `Yes` was measured to land on `TH10_STATE_MENU`
with no stage behind it, which is where `Quit and Return to Select` goes; the
title's demo then started on its own a few seconds later. Whether the same label
behaves differently in the game over menu was not established. Answering `No` (the
default) simply closes the box and stays paused.

Two things that do **not** pause the game, both measured, because they look like
they should: leaving it alone in a stage, and giving another window the
foreground. The game keeps playing in the second case - and the keys stop arriving
at the same time, since a window that has lost the foreground is not sent any.

## Game over

`満身創痍！ Game Over!` comes up on its own, over the field, with **no menu**. The
menu needs one `SHOOT`. It is drawn in Japanese with the English underneath, and
the English says more about what each entry is for:

One ending is not this one. A run that reached the top ten of its difficulty is
asked for a name before any of this appears - see "The ranking's name entry" below -
and the menu comes back with its cursor on `継続する` once that screen is answered.

| Entry | English | What it does |
| --- | --- | --- |
| `継続する` | `Continue` | starts the next run straight away |
| `リプレイを保存する` | `Save Replay` | opens the replay-save flow (below) |
| `タイトル画面に戻る` | `Quit and Return to Select` | returns to the title menu |

**The cursor starts on the third entry**, `Quit and Return to Select`, not on
`Continue`. Pressing `SHOOT` on a freshly opened menu therefore walks back to the
title rather than into the next run, and a menu that has been left alone is not
the menu a driver wants to confirm. Measured, and easy to get wrong twice in a
row: a cursor sitting one entry lower than expected looks exactly like a menu
whose default entry does something else.

`継続する` was measured after moving the cursor up twice: the stage starts again
immediately, skipping the title and the three setup screens, with `lives` back to
2, `score` at 0 and the ship at its start position, keeping the difficulty and the
character already chosen. That is the entry to aim for, and it sits two `up`s above
the default - one `up` short of it is `Save Replay`, the screen with no scripted
exit. Check where the cursor is rather than counting from where you expected it to
start.

The cursor is the lit entry and the up/down keys move it. `Save Replay` is the one
to keep away from: it is two screens deep, the second of them - the name entry
below - has no scripted way out, and nothing here tried leaving the first. A driver
that wants its next run sends `SHOOT` once to open the menu, `up` twice to reach
`継続する`, and `SHOOT` again - two presses past the default, and it starts the run
instead of retreating to the title.

## The ranking's name entry

A run that reaches the top ten of the difficulty it was played on does not get the
plain ending above. The game opens **Score Ranking** on the spot - that
difficulty's own table with the run's line in it, and a character grid underneath -
and waits for a name. Nothing has to be pressed to get there, and it is not the
game over menu: that menu only comes back after the name entry has been answered.

The trigger is the ranking, not the high score. `HiScore` is a separate value, drawn
on the right of the screen, and the game raises its own event flag when a run passes
*that* - which a run that only reached tenth place has usually not done. A driver
that reads that flag as "a name is due" will sit on this screen and type into it
instead of leaving, which is exactly what happened here: the flag was clear, the
name entry was up, and the restart sequence's confirm presses went into the grid.

How it is laid out, measured by reading the cursor out of the game and pressing one
key at a time:

- The grid is one list laid out **13 cells per row**, and the last row is full:
  `{ } | ~ ^ # $ % & □ BS 終` runs from cell 78 to cell 90.
- A direction moves **exactly one cell** - 100 ms held is one cell, not two, which is
  what makes it steerable at all - and a row **wraps at its end**: cell 12 of a row is
  followed by cell 0 of that same row. `down` moves one row and does not wrap with it.
- `終`, the cell that finishes the screen, is the **last cell** (90). `SHOOT` on it
  writes the record with the name already in the buffer - `AAAAAA` if nothing typed -
  and puts the game over menu back with its cursor on `Continue`, so a driver that
  wants the next run confirms once more from there.

Counting presses to reach `終` is what made this screen look like one with no way
out. The cursor can be read, and it can also be written: the field is the one the
game's own key handling moves, on the same object the menu's cursor lives on.
Measured on both screens - writing the menu's highlight made the entry under it the
one a confirm took, and writing a cell of the grid moved the highlight to it, cell 0
and cell 90 both written and read back on a name entry the game was waiting on.
Leaving the screen is therefore two steps, highlight `終` and confirm, rather than
eighteen presses that each have to arrive.

```powershell
.\build\dev\th10ctl.exe screen      # TH10_SCREEN_KIND_NAME_ENTRY cursor=0..90
.\build\dev\th10ctl.exe screen 90   # put the highlight on 終; a `hold shoot` after it
                                    # writes the record
```

The same grid is behind `リプレイを保存する / Save Replay`, one entry below `Continue`
on the game over menu. Nothing here walks into it: that flow writes a replay file, and
its grid is only reached after a slot has been chosen - the cursor being writable does
not make that a screen to drive into.

## Replay save, and the name entry behind it

`リプレイを保存する` opens a list of 25 slots (`No. 01` … `No. 25`); up/down
walks the list and wraps around at both ends. `SHOOT` on a slot opens the name
entry for that replay.

The name entry is a grid of characters (A-Z, a-z, 0-9, punctuation) with:

- the direction keys moving a cursor over the grid,
- `SHOOT` entering the highlighted character into the name,
- **`ESCAPE` acting as backspace** - it deletes a character and does not leave,
- a `終` cell at the bottom right, which has to be selected and confirmed to
  finish.

Measured on the grid: two `ESCAPE` presses shortened a name from `AAAA` to `AA`,
and `SHOOT` with the cursor on `m` produced `AAm`. Twelve presses of the right key
from the first column did land on the last column, but four presses of up did not
land where the count said. Columns are reliable, rows are not: a held direction
slips past the repeat boundary and moves several cells, and a row wraps into the
next one. Counting presses to reach the `終` in the bottom-right corner is
therefore not a plan, and neither is leaving by pressing `ESCAPE` a few times -
it only eats the name one character at a time. **An agent should never confirm its
way into this screen**: the only way out is `終`, and while that cell is reachable
now (it is the last cell of the grid described above), walking out of a flow nobody
meant to enter is not the same as having a script for it.
