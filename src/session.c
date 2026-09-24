#include "internal.h"

#include <imm.h>

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

    /* The write access is for one field and one field only: a screen's cursor,
     * which th10_write_screen_cursor() moves so that leaving an ending does not mean
     * driving a highlight across a 91-cell grid one key press at a time. Nothing
     * else in this library writes to the game. */
    session->process =
        OpenProcess(PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION |
                        PROCESS_QUERY_LIMITED_INFORMATION,
                    FALSE, session->process_id);
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

/* SetForegroundWindow reports success as soon as the request is queued, but the
 * window only starts receiving keys once it actually owns the keyboard focus: a
 * DirectInput game stays unacquired until then and reads no keys at all. The
 * request is also refused outright while another process owns the foreground,
 * which is the normal case for a command line tool. So poll for the focus
 * instead of trusting the return value, and attach to the game's input queue as
 * a fallback, because that is what lets SetFocus hand the keyboard focus over. */
enum {
    FOCUS_POLL_INTERVAL_MS = 10,
    FOCUS_POLL_TIMEOUT_MS = 500,
    /* Once the focus lands the game still has to notice it and re-acquire its
     * input device; injecting in the same instant SetForegroundWindow returned
     * is what made injected keys disappear. */
    FOCUS_SETTLE_MS = 200,
};

static bool session_owns_keyboard_focus(const th10_session *session) {
    GUITHREADINFO info;

    if (GetForegroundWindow() != session->window) {
        return false;
    }
    info.cbSize = sizeof(info);
    if (!GetGUIThreadInfo(session->thread_id, &info)) {
        return true;
    }
    return info.hwndActive == session->window || info.hwndFocus == session->window;
}

static bool wait_for_keyboard_focus(const th10_session *session, DWORD timeout_ms) {
    const ULONGLONG deadline = GetTickCount64() + (ULONGLONG)timeout_ms;

    for (;;) {
        if (session_owns_keyboard_focus(session)) {
            return true;
        }
        if (GetTickCount64() >= deadline) {
            return false;
        }
        Sleep(FOCUS_POLL_INTERVAL_MS);
    }
}

static void attach_and_focus(const th10_session *session) {
    const DWORD current_thread_id = GetCurrentThreadId();

    if (current_thread_id == session->thread_id) {
        (void)SetFocus(session->window);
        return;
    }
    if (AttachThreadInput(current_thread_id, session->thread_id, TRUE)) {
        (void)SetForegroundWindow(session->window);
        (void)SetFocus(session->window);
        (void)AttachThreadInput(current_thread_id, session->thread_id, FALSE);
    }
}

/* The game's thread carries whatever input layout the user has active, and with
 * a Chinese IME on it the character keys ('Z' and 'X') are swallowed into a
 * composition before the game can ever read them, while the arrow keys and Shift
 * pass straight through - which is what "Z and X never work, the arrows always
 * do" looks like from the outside. A mouse click hands the input focus over, but
 * it depends on the cursor actually landing in the client area and is lost as
 * soon as the user touches their mouse, so the IME is pushed out of the way
 * directly instead: the window's input context is detached, and the window is
 * asked to switch to the neutral Latin layout, which DefWindowProc applies.
 *
 * This changes the input language of the game window only, not of the user's
 * other windows; the game reads the keyboard through DirectInput and never
 * needs an IME of its own. */
static void detach_ime(const th10_session *session) {
    HKL latin = LoadKeyboardLayoutW(L"00000409", 0);

    (void)ImmAssociateContext(session->window, NULL);
    if (latin != NULL) {
        (void)PostMessageW(session->window, WM_INPUTLANGCHANGEREQUEST, 0, (LPARAM)latin);
    }
}

/* SetForegroundWindow and SetFocus move the Win32 foreground window and the
 * keyboard focus, but the IME follows its own input focus. A real mouse click is
 * one way to make the system hand that over, so it is used in addition to
 * detach_ime() above as a belt-and-braces measure. The cursor is parked in the
 * middle of the client area for the click and moved back afterwards, so the only
 * lasting effect is that the game owns the input focus. The game itself never
 * looks at the mouse, so the click is inert. */
static void hand_input_focus_to_window(const th10_session *session) {
    INPUT inputs[2];
    POINT previous_cursor;
    POINT centre;
    RECT client;
    bool restore_cursor;

    if (!GetClientRect(session->window, &client)) {
        return;
    }
    centre.x = (client.right - client.left) / 2;
    centre.y = (client.bottom - client.top) / 2;
    if (!ClientToScreen(session->window, &centre)) {
        return;
    }
    restore_cursor = GetCursorPos(&previous_cursor) != FALSE;
    if (!SetCursorPos(centre.x, centre.y)) {
        return;
    }

    ZeroMemory(inputs, sizeof(inputs));
    inputs[0].type = INPUT_MOUSE;
    inputs[0].mi.dwFlags = MOUSEEVENTF_LEFTDOWN;
    inputs[1].type = INPUT_MOUSE;
    inputs[1].mi.dwFlags = MOUSEEVENTF_LEFTUP;
    (void)SendInput(2, inputs, sizeof(INPUT));

    if (restore_cursor) {
        (void)SetCursorPos(previous_cursor.x, previous_cursor.y);
    }
}

th10_focus_result th10_focus(th10_session *session) {
    BOOL permission_result;
    BOOL set_result;
    DWORD permission_error = ERROR_SUCCESS;

    if (session == NULL || session->window == NULL) {
        return (th10_focus_result){.tag = TH10_FOCUS_INVALID_SESSION};
    }

    if (!session_owns_keyboard_focus(session)) {
        permission_result = AllowSetForegroundWindow(ASFW_ANY);
        if (!permission_result) {
            permission_error = GetLastError();
        }
        set_result = SetForegroundWindow(session->window);
        if (!wait_for_keyboard_focus(session, FOCUS_POLL_TIMEOUT_MS)) {
            attach_and_focus(session);
            if (!wait_for_keyboard_focus(session, FOCUS_POLL_TIMEOUT_MS)) {
                if (!set_result && !permission_result) {
                    return (th10_focus_result){
                        .tag = TH10_FOCUS_PERMISSION_AND_SET_REJECTED,
                        .value.permission_and_set_rejected = {.win32_error = permission_error},
                    };
                }
                return (th10_focus_result){.tag = TH10_FOCUS_REJECTED};
            }
        }
    }

    /* A window that already owns the Win32 focus can still be missing the input
     * focus the IME follows, so this runs on every path, not only after the
     * window had to be brought forward. */
    detach_ime(session);
    hand_input_focus_to_window(session);
    Sleep(FOCUS_SETTLE_MS);
    return (th10_focus_result){.tag = TH10_FOCUS_SUCCESS};
}
