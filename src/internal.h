#ifndef AUTO_TH10_INTERNAL_H
#define AUTO_TH10_INTERNAL_H

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <stdbool.h>
#include <stdint.h>

#include "auto_th10/auto_th10.h"

struct th10_session {
    HWND window;
    HANDLE process;
    DWORD process_id;
    DWORD thread_id;
    uint32_t action_mask;
};

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
static const uintptr_t TH10_SCENE_ADDRESS = 0x00491FB8u;        /* 0x4 title and menus, 0x7 a stage */
/* The screen the game is driving, and that screen's cursor, both hang off one
 * object the game points at here - the menu and the name entry keep their own
 * state inside it rather than in static data. Read the object pointer first, as
 * this one moves with the screen:
 *   [0x00477830] + 0x04   screen id: 6 a stage (playing and over alike), 8 a
 *                         menu, 12 the Score Ranking name entry
 *   [0x00477830] + 0x24   the menu's highlighted entry, 0..2
 *   [0x00477830] + 0xFC   the name entry's highlighted grid cell, 0..90
 * Measured by pressing one arrow key at a time while the game sat on each
 * screen and diffing the whole committed address space around the press: the
 * screen id moved 12 -> 8 when the name entry was confirmed, the menu entry
 * 2 -> 0 on one `down` (its cursor opens on Quit, the last entry), and the grid
 * cell +1 on one `right`, wrapping from cell 90 back to 78 on the last row. */
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

#endif
