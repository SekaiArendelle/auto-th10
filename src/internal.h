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

typedef struct th10_memory_result {
    bool success;
    uintptr_t address;
    size_t requested_size;
    size_t bytes_read;
    uint32_t win32_error;
} th10_memory_result;

th10_memory_result th10_read_memory(th10_session *session, uintptr_t address, void *output, size_t size);

#endif
