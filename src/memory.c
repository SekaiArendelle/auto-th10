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
    /* win32_error stays 0 on this path, which is how a caller tells "the read
     * never started" from "ReadProcessMemory failed": a rejected argument is not
     * a Win32 error, and GetLastError() at this point describes something else. */
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

/* The write side of the same idiom. Nothing public exposes it: a caller never
 * gets an address from this library, only the meaning of the field it wants
 * changed, so the address always comes from a constant in this file's headers. */
bool th10_write_memory(th10_session *session, uintptr_t address, const void *input, size_t size,
                       th10_write_failure *failure) {
    SIZE_T bytes_written = 0;

    if (failure != NULL) {
        *failure = (th10_write_failure){
            .address = address,
            .requested_size = size,
        };
    }
    if (session == NULL || session->process == NULL || input == NULL || size == 0) {
        return false;
    }
    if (!WriteProcessMemory(session->process, (LPVOID)address, input, size, &bytes_written) ||
        bytes_written != size) {
        if (failure != NULL) {
            failure->bytes_written = bytes_written;
            failure->win32_error = GetLastError();
        }
        return false;
    }
    return true;
}
