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


#endif
