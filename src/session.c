#include "internal.h"

#include <stdlib.h>
#include <string.h>
#include <wchar.h>

typedef struct find_window_context {
    HWND window;
} find_window_context;

static bool is_game_executable(HWND window) {
    DWORD process_id = 0;
    HANDLE process;
    wchar_t path[32768];
    DWORD path_length = (DWORD)(sizeof(path) / sizeof(path[0]));
    wchar_t *name;

    GetWindowThreadProcessId(window, &process_id);
    if (process_id == 0) {
        return false;
    }
    process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, process_id);
    if (process == NULL) {
        return false;
    }
    if (!QueryFullProcessImageNameW(process, 0, path, &path_length)) {
        CloseHandle(process);
        return false;
    }
    CloseHandle(process);
    name = wcsrchr(path, L'\\');
    name = name == NULL ? path : name + 1;
    return _wcsicmp(name, L"th10.exe") == 0 || _wcsicmp(name, L"th10chs.exe") == 0 ||
           _wcsicmp(name, L"th10cht.exe") == 0;
}

static BOOL CALLBACK find_game_window(HWND window, LPARAM parameter) {
    find_window_context *context = (find_window_context *)parameter;
    char title[256];

    if (!IsWindowVisible(window) || GetWindowTextA(window, title, (int)sizeof(title)) <= 0) {
        return TRUE;
    }
    if (strstr(title, "Mountain of Faith") == NULL && !is_game_executable(window)) {
        return TRUE;
    }
    context->window = window;
    return FALSE;
}

th10_open_result th10_open(void) {
    find_window_context context = {0};
    th10_session *session;
    BOOL enumeration_result;

    SetLastError(ERROR_SUCCESS);
    enumeration_result = EnumWindows(find_game_window, (LPARAM)&context);
    if (context.window == NULL) {
        const DWORD error = GetLastError();
        if (!enumeration_result && error != ERROR_SUCCESS) {
            return (th10_open_result){
                .tag = TH10_OPEN_ENUM_WINDOWS_FAILED,
                .value.win32_error = {.code = error},
            };
        }
        return (th10_open_result){.tag = TH10_OPEN_WINDOW_NOT_FOUND};
    }

    session = (th10_session *)calloc(1, sizeof(*session));
    if (session == NULL) {
        return (th10_open_result){.tag = TH10_OPEN_OUT_OF_MEMORY};
    }
    session->window = context.window;
    session->thread_id = GetWindowThreadProcessId(context.window, &session->process_id);
    if (session->thread_id == 0 || session->process_id == 0) {
        const DWORD error = GetLastError();
        free(session);
        return (th10_open_result){
            .tag = TH10_OPEN_GET_PROCESS_ID_FAILED,
            .value.win32_error = {.code = error},
        };
    }

    session->process = OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_LIMITED_INFORMATION, FALSE, session->process_id);
    if (session->process == NULL) {
        const DWORD error = GetLastError();
        free(session);
        return (th10_open_result){
            .tag = TH10_OPEN_PROCESS_FAILED,
            .value.win32_error = {.code = error},
        };
    }
    return (th10_open_result){
        .tag = TH10_OPEN_SUCCESS,
        .value.session = session,
    };
}

th10_close_result th10_close(th10_session *session) {
    th10_input_result input_result;
    BOOL handle_result = TRUE;
    DWORD handle_error = ERROR_SUCCESS;

    if (session == NULL) {
        return (th10_close_result){.tag = TH10_CLOSE_INVALID_SESSION};
    }
    input_result = th10_set_input(session, TH10_ACTION_NONE);
    if (session->process != NULL) {
        handle_result = CloseHandle(session->process);
        if (!handle_result) {
            handle_error = GetLastError();
        }
    }
    free(session);

    if (input_result.tag != TH10_INPUT_SUCCESS && !handle_result) {
        return (th10_close_result){
            .tag = TH10_CLOSE_INPUT_AND_HANDLE_FAILED,
            .value.input_and_handle_failed = {
                .input = input_result,
                .win32_error = handle_error,
            },
        };
    }
    if (input_result.tag != TH10_INPUT_SUCCESS) {
        return (th10_close_result){
            .tag = TH10_CLOSE_INPUT_RELEASE_FAILED,
            .value.input_release_failed = input_result,
        };
    }
    if (!handle_result) {
        return (th10_close_result){
            .tag = TH10_CLOSE_HANDLE_FAILED,
            .value.handle_failed = {.win32_error = handle_error},
        };
    }
    return (th10_close_result){.tag = TH10_CLOSE_SUCCESS};
}

th10_focus_result th10_focus(th10_session *session) {
    BOOL permission_result;
    DWORD permission_error = ERROR_SUCCESS;

    if (session == NULL || session->window == NULL) {
        return (th10_focus_result){.tag = TH10_FOCUS_INVALID_SESSION};
    }
    permission_result = AllowSetForegroundWindow(ASFW_ANY);
    if (!permission_result) {
        permission_error = GetLastError();
    }
    if (!SetForegroundWindow(session->window)) {
        if (!permission_result) {
            return (th10_focus_result){
                .tag = TH10_FOCUS_PERMISSION_AND_SET_REJECTED,
                .value.permission_and_set_rejected = {.win32_error = permission_error},
            };
        }
        return (th10_focus_result){.tag = TH10_FOCUS_REJECTED};
    }
    return (th10_focus_result){.tag = TH10_FOCUS_SUCCESS};
}
