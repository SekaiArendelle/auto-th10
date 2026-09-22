#include "internal.h"

/* Reads and returns whether it succeeded, so callers that only branch on the
 * outcome stay one line. The optional `failure` out-parameter carries why it
 * did not - only the callers that report errors to a user ask for it. */
bool th10_read_memory(th10_session *session, uintptr_t address, void *output, size_t size,
                      th10_read_failure *failure) {
    SIZE_T bytes_read = 0;

    if (failure != NULL) {
        *failure = (th10_read_failure){
            .address = address,
            .requested_size = size,
        };
    }
    if (session == NULL || session->process == NULL || output == NULL || size == 0) {
        return false;
    }
    if (!ReadProcessMemory(session->process, (LPCVOID)address, output, size, &bytes_read) ||
        bytes_read != size) {
        if (failure != NULL) {
            failure->bytes_read = bytes_read;
            failure->win32_error = GetLastError();
        }
        return false;
    }
    return true;
}
