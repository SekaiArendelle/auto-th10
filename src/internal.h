#ifndef AUTO_TH10_INTERNAL_H
#define AUTO_TH10_INTERNAL_H

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <stdbool.h>
#include <stdint.h>

#include "auto_th10/auto_th10.h"

/* One session, one owner. The pointer th10_open() hands back is moved rather than
 * copied, and whoever holds it now is the only thing that can close it.
 *
 * th10_close() destroys that object - it frees the session rather than emptying
 * it - so the pointer it was given is dead once it returns and calling it twice
 * is undefined behaviour, the same rule free() follows. The library cannot clear
 * the caller's variable, because all it ever receives is the value. A caller that
 * passes the session on does it by assignment and sets its own name to NULL in
 * the same breath, which is what leaves no state in which two names could close
 * one session. There is no th10_move_session() for that: nothing here or in
 * th10ctl moves a session between owners, and the whole operation is those two
 * lines. */
struct th10_session {
    HWND window;
    HANDLE process;
    DWORD process_id;
    DWORD thread_id;
    uint32_t action_mask;
    LPVOID input_bridge_control;
    LPVOID input_bridge_code;
    bool input_bridge_installed;
    bool input_bridge_allocations_retained;
};

/* Writes the bridge control block when background input is enabled. */
th10_input_result th10_set_background_input(th10_session *session, uint32_t action_mask);

/* Reads and reports whether it succeeded. Pass NULL for `failure` when only the
 * outcome matters; otherwise it is filled in with the address, the sizes and
 * GetLastError() of the attempt - the same shape th10_snapshot_result carries,
 * so the two do not have to be translated into each other. */
bool th10_read_memory(th10_session *session, uintptr_t address, void *output, size_t size,
                      th10_read_failure *failure);

/* The same for a write, and it stays internal on purpose: nothing public hands a
 * caller an address to write to. The one caller is the cursor the game's own
 * screens keep, which is the screen's own field rather than a game value - see
 * th10_write_screen_cursor(). */
bool th10_write_memory(th10_session *session, uintptr_t address, const void *input, size_t size,
                       th10_write_failure *failure);

/* Addresses in the game's static data, all verified against th10.exe 1.00a by
 * reading them while the game was running in each state.
 *
 * Keep TH10_SCENE_ADDRESS apart from its neighbour 0x00491FBC: that neighbour
 * moves at the same moments but holds the opposite value and is not what the
 * game reads, so using it inverts every decision. */
static const uintptr_t TH10_SCENE_ADDRESS = 0x00491FB8u;        /* 0x4 title and menus,
                                                                 * 0x7 a stage, 0xd the
                                                                 * screens between runs */
/* The screen the game is driving, and that screen's cursor, both hang off one
 * object the game points at here - the menu and the name entry keep their own
 * state inside it rather than in static data. Read the object pointer first, as
 * this one moves with the screen:
 *   [0x00477830] + 0x04   screen id: 6 the stage whose run is over, 8 a menu,
 *                         12 the Score Ranking name entry, 2 the pause menu a
 *                         running stage opens on ESCAPE, 4 the confirmation its
 *                         `Retry This Game` opens. A stage that is still playing
 *                         reports 0 here and the screens between runs report 21,
 *                         and both of those are left unmapped
 *   [0x00477830] + 0x24   the highlighted entry of any of those menus: 0..2 on a
 *                         menu or the pause menu, 0..1 on the confirmation
 *   [0x00477830] + 0xFC   the name entry's highlighted grid cell, 0..90
 * Measured by pressing one arrow key at a time while the game sat on each
 * screen and diffing the whole committed address space around the press: the
 * screen id moved 12 -> 8 when the name entry was confirmed, the menu entry
 * 2 -> 0 on one `down` (its cursor opens on Quit, the last entry), and the grid
 * cell +1 on one `right`, wrapping from cell 90 back to 78 on the last row.
 * Measured with a live run behind them since: the pause menu read 2 while
 * `down`, `up`, `up`, `down` walked +0x24 0 -> 1 -> 0 -> 2 -> 0, and confirming
 * `Retry This Game` left the same object on 4 with +0x24 at 1, the `No` that
 * screen's cursor opens on. A stage that was playing read 0 on six reads in a
 * row until it ended, whereupon the same field read 6. The playing stage's 0 is
 * the reason a driver that needs "is there a run" asks the snapshot rather than
 * this id, and the pause menu's 2 is what th10_read_state() reads playing
 * against paused from. A frame or two of a transition reads as an id that is
 * none of these - 3 while a pause was being left, 7 and 13 around the ending's
 * menu - and each of those answers UNKNOWN rather than being guessed at. */
static const uintptr_t TH10_SCREEN_OBJECT_ADDRESS = 0x00477830u;
static const uintptr_t TH10_LIVES_ADDRESS = 0x00474C70u;        /* 2, 1, 0 alive, then -1 once over */
static const uintptr_t TH10_STAGE_FRAMES_ADDRESS = 0x00474C88u; /* advances while playing, frozen
                                                                 * while paused */
static const uintptr_t TH10_SCORE_ADDRESS = 0x00474C44u;
static const uintptr_t TH10_POWER_ADDRESS = 0x00474C48u;
static const uintptr_t TH10_STAGE_BASE_ADDRESS = 0x00477834u;   /* null until a stage is loaded */
static const uintptr_t TH10_ENEMY_MANAGER_ADDRESS = 0x00477704u;
static const uintptr_t TH10_BULLET_MANAGER_ADDRESS = 0x004776F0u;
static const uintptr_t TH10_BULLET_FLAGS_ADDRESS = 0x00477810u;
static const uintptr_t TH10_LASER_MANAGER_ADDRESS = 0x0047781Cu;
static const uintptr_t TH10_RESOURCE_MANAGER_ADDRESS = 0x00477818u;

/* The input reducer stores its final 16-bit action word at 0x0044A8C9. The
 * eight bytes immediately before that store are `66 8b 0e bb 01 00 00 00`
 * (`mov cx,[esi]`; `mov ebx,1`), and are the reversible patch site used by the
 * background-input bridge. The address, bytes and the action word at
 * 0x00474E30 were verified on 2026-09-25 by reading them from the running
 * th10.exe 1.00a whose SHA-256 is
 * 2F14760B6FBBF57549541583283BADB9A19A4222B90F0A146D5AA17F01DC9040. A
 * 120-frame down lease then produced current/previous words 0x20/0x20 and a
 * hold count of 6 at that structure, before the original bytes were restored.
 * The bridge reproduces the two displaced instructions and returns at +8.
 * On 2026-09-25 the th10chs.exe and th10cht.exe hashes listed in
 * input_bridge.c were also checked: their PE layouts and the reducer bytes from
 * this address through 0x0044A8FC match the original. Their only .text changes
 * are fifteen font creation arguments at 0x00437A79..0x00437CE8. */
static const uintptr_t TH10_INPUT_PATCH_ADDRESS = 0x0044A8BDu;
static const uintptr_t TH10_INPUT_PATCH_RETURN_ADDRESS = 0x0044A8C5u;

#endif
