#include "internal.h"

#include <wincrypt.h>

#include <limits.h>
#include <string.h>

enum {
    INPUT_PATCH_SIZE = 8,
    INPUT_CONTROL_SIZE = 8,
    INPUT_CODE_SIZE = 29,
    INPUT_LEASE_FRAMES = 120,
};

static const uint8_t INPUT_PATCH_ORIGINAL[INPUT_PATCH_SIZE] = {
    0x66, 0x8B, 0x0E, 0xBB, 0x01, 0x00, 0x00, 0x00,
};

/* Exact hashes keep the code-injection boundary narrow without excluding the
 * two localized executables th10_open() has always recognized. On 2026-09-25,
 * all three files had identical PE section layouts and identical bytes for the
 * input reducer from 0x0044A8BD through 0x0044A8FC. The localized .text sections
 * differed from the original at only fifteen one-byte font creation arguments
 * in 0x00437A79..0x00437CE8 (0x80 -> 0x86), away from the reducer and trampoline.
 * The names below describe the files that were hashed, not a filename check: an
 * unknown replacement with the same name is still rejected. */
static const uint8_t TH10_VERIFIED_SHA256[][32] = {
    {/* th10.exe 1.00a */
     0x2F, 0x14, 0x76, 0x0B, 0x6F, 0xBB, 0xF5, 0x75,
     0x49, 0x54, 0x15, 0x83, 0x28, 0x3B, 0xAD, 0xB9,
     0xA1, 0x9A, 0x42, 0x22, 0xB9, 0x0F, 0x0A, 0x14,
     0x6D, 0x5A, 0xA1, 0x7F, 0x01, 0xDC, 0x90, 0x40},
    {/* th10chs.exe */
     0x97, 0xFD, 0xCE, 0xDE, 0x94, 0x2D, 0x42, 0x5B,
     0xBB, 0x21, 0xA0, 0x80, 0x67, 0xD9, 0x5E, 0x08,
     0xD9, 0x16, 0x87, 0x4A, 0x1C, 0xB3, 0x99, 0xD7,
     0x3B, 0xE1, 0x67, 0x37, 0xAF, 0xCA, 0x6A, 0x17},
    {/* th10cht.exe */
     0x3B, 0x2B, 0xDA, 0x90, 0x81, 0x6B, 0xF4, 0xE5,
     0x16, 0x8A, 0x1A, 0x0A, 0xE0, 0xB3, 0x39, 0x02,
     0xD5, 0xC9, 0xBE, 0xE9, 0x80, 0xB9, 0xDC, 0xA7,
     0x35, 0x84, 0x8B, 0xE0, 0x98, 0x64, 0x66, 0x10},
};

static bool is_verified_digest(const uint8_t digest[32]) {
    size_t index;

    for (index = 0; index < sizeof(TH10_VERIFIED_SHA256) / sizeof(TH10_VERIFIED_SHA256[0]);
         ++index) {
        if (memcmp(digest, TH10_VERIFIED_SHA256[index],
                   sizeof(TH10_VERIFIED_SHA256[index])) == 0) {
            return true;
        }
    }
    return false;
}

static th10_input_result bridge_failure(th10_input_bridge_operation operation, DWORD error) {
    return (th10_input_result){
        .tag = TH10_INPUT_BRIDGE_FAILED,
        .value.bridge_failed = {
            .operation = operation,
            .win32_error = error,
        },
    };
}

static th10_input_result verify_executable(th10_session *session) {
    wchar_t path[32768];
    DWORD path_size = (DWORD)(sizeof(path) / sizeof(path[0]));
    HANDLE file = INVALID_HANDLE_VALUE;
    HCRYPTPROV provider = 0;
    HCRYPTHASH hash = 0;
    uint8_t buffer[65536];
    uint8_t digest[32];
    DWORD bytes_read = 0;
    DWORD digest_size = (DWORD)sizeof(digest);
    DWORD error = ERROR_SUCCESS;
    bool incompatible = false;

    if (!QueryFullProcessImageNameW(session->process, 0, path, &path_size)) {
        error = GetLastError();
        goto finish;
    }
    file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                       NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN, NULL);
    if (file == INVALID_HANDLE_VALUE) {
        error = GetLastError();
        goto finish;
    }
    if (!CryptAcquireContextW(&provider, NULL, NULL, PROV_RSA_AES, CRYPT_VERIFYCONTEXT)) {
        error = GetLastError();
        goto finish;
    }
    if (!CryptCreateHash(provider, CALG_SHA_256, 0, 0, &hash)) {
        error = GetLastError();
        goto finish;
    }
    do {
        if (!ReadFile(file, buffer, sizeof(buffer), &bytes_read, NULL)) {
            error = GetLastError();
            goto finish;
        }
        if (bytes_read != 0 && !CryptHashData(hash, buffer, bytes_read, 0)) {
            error = GetLastError();
            goto finish;
        }
    } while (bytes_read != 0);
    if (!CryptGetHashParam(hash, HP_HASHVAL, digest, &digest_size, 0)) {
        error = GetLastError();
        goto finish;
    }
    incompatible = digest_size != sizeof(digest) || !is_verified_digest(digest);

finish:
    if (hash != 0) {
        CryptDestroyHash(hash);
    }
    if (provider != 0) {
        CryptReleaseContext(provider, 0);
    }
    if (file != INVALID_HANDLE_VALUE) {
        CloseHandle(file);
    }
    if (error != ERROR_SUCCESS) {
        return bridge_failure(TH10_INPUT_BRIDGE_VERIFY_EXECUTABLE, error);
    }
    if (incompatible) {
        return (th10_input_result){.tag = TH10_INPUT_BRIDGE_INCOMPATIBLE};
    }
    return (th10_input_result){.tag = TH10_INPUT_SUCCESS};
}

static th10_input_result write_remote(th10_session *session, uintptr_t address, const void *data,
                                      size_t size, th10_input_bridge_operation operation) {
    th10_write_failure failure;

    if (!th10_write_memory(session, address, data, size, &failure)) {
        return bridge_failure(operation, failure.win32_error);
    }
    return (th10_input_result){.tag = TH10_INPUT_SUCCESS};
}

static bool instruction_pointer(HANDLE thread, uintptr_t *address) {
#ifdef _WIN64
    WOW64_CONTEXT context;

    ZeroMemory(&context, sizeof(context));
    context.ContextFlags = WOW64_CONTEXT_CONTROL;
    if (!Wow64GetThreadContext(thread, &context)) {
        return false;
    }
    *address = (uintptr_t)context.Eip;
#else
    CONTEXT context;

    ZeroMemory(&context, sizeof(context));
    context.ContextFlags = CONTEXT_CONTROL;
    if (!GetThreadContext(thread, &context)) {
        return false;
    }
    *address = (uintptr_t)context.Eip;
#endif
    return true;
}

/* Only the window thread executes the reducer being patched. Suspending that
 * known thread closes both the hot-patch race and the check/write race: the
 * bytes are replaced only when they still equal this caller's expected image. */
static th10_input_result write_code(th10_session *session, uintptr_t address,
                                    const uint8_t expected[INPUT_PATCH_SIZE],
                                    const uint8_t replacement[INPUT_PATCH_SIZE],
                                    bool *replacement_written, bool *retain_allocations) {
    HANDLE thread;
    uint8_t actual[INPUT_PATCH_SIZE];
    uintptr_t instruction = 0;
    DWORD old_protection = 0;
    DWORD ignored_protection = 0;
    DWORD error = ERROR_SUCCESS;
    bool failed = false;
    bool incompatible = false;
    bool safe_to_resume = true;
    bool suspended = false;
    bool protection_changed = false;
    th10_input_bridge_operation operation = TH10_INPUT_BRIDGE_OPEN_THREAD;
    th10_read_failure read_failure;
    th10_write_failure failure;

    if (replacement_written != NULL) {
        *replacement_written = false;
    }
    thread = OpenThread(THREAD_SUSPEND_RESUME | THREAD_GET_CONTEXT, FALSE, session->thread_id);
    if (thread == NULL) {
        return bridge_failure(operation, GetLastError());
    }
    operation = TH10_INPUT_BRIDGE_SUSPEND_THREAD;
    if (SuspendThread(thread) == (DWORD)-1) {
        error = GetLastError();
        CloseHandle(thread);
        return bridge_failure(operation, error);
    }
    suspended = true;

    if (retain_allocations != NULL) {
        /* Failure to read EIP is conservative: the caller then retains both
         * pages. If EIP is outside the trampoline, restoring the only entry
         * branch while this thread is stopped proves it cannot enter later. */
        *retain_allocations = true;
        if (instruction_pointer(thread, &instruction)) {
            *retain_allocations =
                instruction >= (uintptr_t)session->input_bridge_code &&
                instruction < (uintptr_t)session->input_bridge_code + INPUT_CODE_SIZE;
        }
    }
    operation = TH10_INPUT_BRIDGE_READ_PATCH;
    if (!th10_read_memory(session, address, actual, sizeof(actual), &read_failure)) {
        error = read_failure.win32_error;
        failed = true;
        goto finish;
    }
    if (memcmp(actual, expected, sizeof(actual)) != 0) {
        incompatible = true;
        goto finish;
    }

    operation = TH10_INPUT_BRIDGE_PROTECT_PATCH;
    if (!VirtualProtectEx(session->process, (LPVOID)address, INPUT_PATCH_SIZE,
                          PAGE_EXECUTE_READWRITE, &old_protection)) {
        error = GetLastError();
        failed = true;
        goto finish;
    }
    protection_changed = true;

    operation = TH10_INPUT_BRIDGE_WRITE_PATCH;
    if (!th10_write_memory(session, address, replacement, INPUT_PATCH_SIZE, &failure)) {
        error = failure.win32_error;
        failed = true;
        /* Do not expose a torn branch to the resumed thread. Determine which
         * complete image, if any, is present while it is still suspended. If
         * neither matches, restore the caller-owned preimage in this same
         * critical section. */
        if (!th10_read_memory(session, address, actual, sizeof(actual), &read_failure)) {
            safe_to_resume = false;
            goto finish;
        }
        if (memcmp(actual, replacement, sizeof(actual)) == 0) {
            if (replacement_written != NULL) {
                *replacement_written = true;
            }
            goto finish;
        }
        if (memcmp(actual, expected, sizeof(actual)) == 0) {
            goto finish;
        }
        if (!th10_write_memory(session, address, expected, INPUT_PATCH_SIZE, &failure) ||
            !FlushInstructionCache(session->process, (LPCVOID)address, INPUT_PATCH_SIZE)) {
            safe_to_resume = false;
        }
        goto finish;
    }
    if (replacement_written != NULL) {
        *replacement_written = true;
    }
    operation = TH10_INPUT_BRIDGE_FLUSH_CODE;
    if (!FlushInstructionCache(session->process, (LPCVOID)address, INPUT_PATCH_SIZE)) {
        error = GetLastError();
        failed = true;
        /* As with a short write, never resume through a modified image whose
         * instruction cache could not be made coherent. Roll back while the
         * thread is still stopped and expose no replacement to the caller. */
        if (!th10_write_memory(session, address, expected, INPUT_PATCH_SIZE, &failure) ||
            !FlushInstructionCache(session->process, (LPCVOID)address, INPUT_PATCH_SIZE)) {
            safe_to_resume = false;
        } else if (replacement_written != NULL) {
            *replacement_written = false;
        }
        goto finish;
    }

finish:
    if (protection_changed &&
        !VirtualProtectEx(session->process, (LPVOID)address, INPUT_PATCH_SIZE, old_protection,
                          &ignored_protection)) {
        if (!failed) {
            operation = TH10_INPUT_BRIDGE_RESTORE_PROTECTION;
            error = GetLastError();
            failed = true;
        }
    }
    /* A failed resume is more urgent than the operation that led here: report
     * it even while unwinding another failure, because the game thread may still
     * be suspended and the caller needs the right diagnosis. */
    if (suspended && safe_to_resume && ResumeThread(thread) == (DWORD)-1) {
        operation = TH10_INPUT_BRIDGE_RESUME_THREAD;
        error = GetLastError();
        failed = true;
    }
    CloseHandle(thread);
    if (failed) {
        return bridge_failure(operation, error);
    }
    if (incompatible) {
        return (th10_input_result){.tag = TH10_INPUT_BRIDGE_INCOMPATIBLE};
    }
    return (th10_input_result){.tag = TH10_INPUT_SUCCESS};
}

static void build_patch(const th10_session *session, uint8_t patch[INPUT_PATCH_SIZE]) {
    uint32_t displacement;

    patch[0] = 0xE9;
    patch[5] = 0x90;
    patch[6] = 0x90;
    patch[7] = 0x90;
    displacement = (uint32_t)((uintptr_t)session->input_bridge_code -
                              (TH10_INPUT_PATCH_ADDRESS + 5u));
    memcpy(patch + 1, &displacement, sizeof(displacement));
}

static th10_input_result free_allocations(th10_session *session) {
    if (session->input_bridge_code != NULL) {
        if (!VirtualFreeEx(session->process, session->input_bridge_code, 0, MEM_RELEASE)) {
            return bridge_failure(TH10_INPUT_BRIDGE_FREE_CODE, GetLastError());
        }
        session->input_bridge_code = NULL;
    }
    if (session->input_bridge_control != NULL) {
        if (!VirtualFreeEx(session->process, session->input_bridge_control, 0, MEM_RELEASE)) {
            return bridge_failure(TH10_INPUT_BRIDGE_FREE_CONTROL, GetLastError());
        }
        session->input_bridge_control = NULL;
    }
    session->input_bridge_allocations_retained = false;
    return (th10_input_result){.tag = TH10_INPUT_SUCCESS};
}

static uint16_t internal_action_mask(uint32_t action_mask) {
    uint16_t internal = 0;

    if ((action_mask & TH10_ACTION_LEFT) != 0) {
        internal |= 0x40u;
    }
    if ((action_mask & TH10_ACTION_RIGHT) != 0) {
        internal |= 0x80u;
    }
    if ((action_mask & TH10_ACTION_UP) != 0) {
        internal |= 0x10u;
    }
    if ((action_mask & TH10_ACTION_DOWN) != 0) {
        internal |= 0x20u;
    }
    if ((action_mask & TH10_ACTION_SHOOT) != 0) {
        internal |= 0x01u;
    }
    if ((action_mask & TH10_ACTION_FOCUS) != 0) {
        internal |= 0x04u;
    }
    if ((action_mask & TH10_ACTION_BOMB) != 0) {
        internal |= 0x02u;
    }
    if ((action_mask & TH10_ACTION_ESCAPE) != 0) {
        internal |= 0x08u;
    }
    return internal;
}

th10_input_result th10_set_background_input(th10_session *session, uint32_t action_mask) {
    const uint16_t internal = internal_action_mask(action_mask);
    const uint32_t lease = INPUT_LEASE_FRAMES;
    th10_input_result result;

    result = write_remote(session, (uintptr_t)session->input_bridge_control + 4u, &internal,
                          sizeof(internal), TH10_INPUT_BRIDGE_WRITE_CONTROL);
    if (result.tag != TH10_INPUT_SUCCESS) {
        return result;
    }
    /* Publishing the lease last prevents the injected code from observing a
     * new lease with the previous action. Repeating a state is intentional: it
     * refreshes the crash-safe lease even when no action bit changed. */
    return write_remote(session, (uintptr_t)session->input_bridge_control, &lease, sizeof(lease),
                        TH10_INPUT_BRIDGE_WRITE_CONTROL);
}

th10_input_result th10_enable_background_input(th10_session *session) {
    uint8_t code[INPUT_CODE_SIZE] = {
        0x66, 0x8B, 0x0E,                         /* mov cx,[esi] */
        0xBB, 0x01, 0x00, 0x00, 0x00,             /* mov ebx,1 */
        0xBA, 0x00, 0x00, 0x00, 0x00,             /* mov edx,control */
        0x83, 0x3A, 0x00,                         /* cmp dword [edx],0 */
        0x74, 0x06,                               /* je original_action */
        0xFF, 0x0A,                               /* dec dword [edx] */
        0x66, 0x8B, 0x42, 0x04,                   /* mov ax,[edx+4] */
        0xE9, 0x00, 0x00, 0x00, 0x00,             /* jmp patch_return */
    };
    uint8_t patch[INPUT_PATCH_SIZE] = {0};
    uint32_t address32;
    uint32_t displacement;
    DWORD old_protection = 0;
    th10_input_result result;
    th10_input_result cleanup_result;
    th10_input_result restore_result;
    bool patch_written = false;
    bool original_written = false;
    bool retain_allocations = false;

    if (session == NULL || session->process == NULL) {
        return (th10_input_result){.tag = TH10_INPUT_INVALID_SESSION};
    }
    if (session->input_bridge_installed) {
        return (th10_input_result){.tag = TH10_INPUT_SUCCESS};
    }
    result = verify_executable(session);
    if (result.tag != TH10_INPUT_SUCCESS) {
        return result;
    }
    if ((session->input_bridge_code == NULL) != (session->input_bridge_control == NULL)) {
        result = free_allocations(session);
        if (result.tag != TH10_INPUT_SUCCESS) {
            return result;
        }
    }
    /* A session may opt in after using the scan-code backend. Release those
     * desktop keys before switching ownership to the remote action word. */
    if (session->action_mask != TH10_ACTION_NONE) {
        result = th10_set_input(session, TH10_ACTION_NONE);
        if (result.tag != TH10_INPUT_SUCCESS) {
            return result;
        }
    }

    if (session->input_bridge_code == NULL) {
        session->input_bridge_allocations_retained = false;
        session->input_bridge_control =
            VirtualAllocEx(session->process, NULL, INPUT_CONTROL_SIZE, MEM_COMMIT | MEM_RESERVE,
                           PAGE_READWRITE);
        if (session->input_bridge_control == NULL) {
            return bridge_failure(TH10_INPUT_BRIDGE_ALLOCATE_CONTROL, GetLastError());
        }
        session->input_bridge_code =
            VirtualAllocEx(session->process, NULL, INPUT_CODE_SIZE, MEM_COMMIT | MEM_RESERVE,
                           PAGE_READWRITE);
        if (session->input_bridge_code == NULL) {
            result = bridge_failure(TH10_INPUT_BRIDGE_ALLOCATE_CODE, GetLastError());
            goto fail;
        }
        if ((uintptr_t)session->input_bridge_control > UINT32_MAX ||
            (uintptr_t)session->input_bridge_code > UINT32_MAX) {
            result = bridge_failure(TH10_INPUT_BRIDGE_ALLOCATE_CODE, ERROR_INVALID_ADDRESS);
            goto fail;
        }

        address32 = (uint32_t)(uintptr_t)session->input_bridge_control;
        memcpy(code + 9, &address32, sizeof(address32));
        displacement = (uint32_t)(TH10_INPUT_PATCH_RETURN_ADDRESS -
                                  ((uintptr_t)session->input_bridge_code + INPUT_CODE_SIZE));
        memcpy(code + 25, &displacement, sizeof(displacement));
        result = write_remote(session, (uintptr_t)session->input_bridge_code, code, sizeof(code),
                              TH10_INPUT_BRIDGE_WRITE_CODE);
        if (result.tag != TH10_INPUT_SUCCESS) {
            goto fail;
        }
        if (!VirtualProtectEx(session->process, session->input_bridge_code, INPUT_CODE_SIZE,
                              PAGE_EXECUTE_READ, &old_protection)) {
            result = bridge_failure(TH10_INPUT_BRIDGE_PROTECT_CODE, GetLastError());
            goto fail;
        }
        if (!FlushInstructionCache(session->process, session->input_bridge_code,
                                   INPUT_CODE_SIZE)) {
            result = bridge_failure(TH10_INPUT_BRIDGE_FLUSH_CODE, GetLastError());
            goto fail;
        }
    }

    build_patch(session, patch);
    /* write_code reports whether the replacement reached memory even when a
     * later cache/protection/resume step fails, so cleanup never has to guess
     * whether the entry points at the trampoline. */
    result = write_code(session, TH10_INPUT_PATCH_ADDRESS, INPUT_PATCH_ORIGINAL, patch,
                        &patch_written, NULL);
    if (result.tag == TH10_INPUT_SUCCESS) {
        session->input_bridge_installed = true;
        session->input_bridge_allocations_retained = false;
        return result;
    }
    if (!patch_written) {
        /* Definite pre-write failures leave the entry closed. Fresh pages can
         * be freed; pages retained from a prior EIP observation remain mapped
         * because that older invocation may still be finishing in them. */
        session->input_bridge_installed = false;
        if (session->input_bridge_allocations_retained) {
            return result;
        }
        cleanup_result = free_allocations(session);
        return cleanup_result.tag == TH10_INPUT_SUCCESS ? result : cleanup_result;
    }
    session->input_bridge_installed = true;
    session->input_bridge_allocations_retained = false;

    /* A late failure may mean the branch was written before cache/protection
     * maintenance failed. Keep the allocations alive unless restoration is
     * known to have succeeded, because the game may still jump to them. */
    restore_result = write_code(session, TH10_INPUT_PATCH_ADDRESS, patch, INPUT_PATCH_ORIGINAL,
                                &original_written, &retain_allocations);
    if (original_written) {
        session->input_bridge_installed = false;
        session->input_bridge_allocations_retained = retain_allocations;
    }
    if (restore_result.tag != TH10_INPUT_SUCCESS) {
        return restore_result;
    }
    if (retain_allocations) {
        return result;
    }

fail:
    cleanup_result = free_allocations(session);
    if (cleanup_result.tag != TH10_INPUT_SUCCESS) {
        return cleanup_result;
    }
    return result;
}

th10_input_result th10_disable_background_input(th10_session *session) {
    uint8_t patch[INPUT_PATCH_SIZE] = {0};
    uint32_t lease = 0;
    th10_input_result result;
    bool original_written = false;
    bool retain_allocations = false;

    if (session == NULL || session->process == NULL) {
        return (th10_input_result){.tag = TH10_INPUT_INVALID_SESSION};
    }
    if (session->input_bridge_code == NULL && session->input_bridge_control == NULL) {
        return (th10_input_result){.tag = TH10_INPUT_SUCCESS};
    }
    /* Pages retained because EIP was in the trampoline are safe to reuse, but
     * this call cannot prove the old invocation has completed, so it leaves them
     * mapped rather than turning a small leak into a use-after-free. */
    if (!session->input_bridge_installed) {
        return (th10_input_result){.tag = TH10_INPUT_SUCCESS};
    }
    if (session->input_bridge_installed) {
        result = write_remote(session, (uintptr_t)session->input_bridge_control, &lease,
                              sizeof(lease), TH10_INPUT_BRIDGE_WRITE_CONTROL);
        if (result.tag != TH10_INPUT_SUCCESS) {
            return result;
        }
        build_patch(session, patch);
        result = write_code(session, TH10_INPUT_PATCH_ADDRESS, patch, INPUT_PATCH_ORIGINAL,
                            &original_written, &retain_allocations);
        if (original_written) {
            session->input_bridge_installed = false;
            session->input_bridge_allocations_retained = retain_allocations;
            session->action_mask = TH10_ACTION_NONE;
        }
        if (result.tag != TH10_INPUT_SUCCESS) {
            return result;
        }
    }
    if (retain_allocations) {
        return (th10_input_result){.tag = TH10_INPUT_SUCCESS};
    }
    return free_allocations(session);
}
