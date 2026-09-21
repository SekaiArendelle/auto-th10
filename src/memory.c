#include "internal.h"

th10_memory_result th10_read_memory(th10_session *session, uintptr_t address, void *output, size_t size) {
    SIZE_T bytes_read = 0;
    th10_memory_result result = {
        .success = false,
        .address = address,
        .requested_size = size,
    };

    if (session == NULL || session->process == NULL || output == NULL || size == 0) {
        return result;
    }
    if (!ReadProcessMemory(session->process, (LPCVOID)address, output, size, &bytes_read) || bytes_read != size) {
        result.bytes_read = bytes_read;
        result.win32_error = GetLastError();
        return result;
    }
    result.success = true;
    result.bytes_read = bytes_read;
    return result;
}
