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

/* Addresses in the game's static data, all verified against th10.exe 1.00a by
 * reading them while the game was running in each state.
 *
 * Keep TH10_SCENE_ADDRESS apart from its neighbour 0x00491FBC: that neighbour
 * moves at the same moments but holds the opposite value and is not what the
 * game reads, so using it inverts every decision. */
static const uintptr_t TH10_SCENE_ADDRESS = 0x00491FB8u;        /* 0x4 title and menus, 0x7 a stage */
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
