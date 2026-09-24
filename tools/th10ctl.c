/*
 * th10ctl - command line driver for the auto-th10 C API.
 *
 * Attaches to a running Touhou 10 (Mountain of Faith) process through the
 * public C binding so that it can be exercised by hand, without going through
 * the Python extension. Every command attaches, acts, and detaches, the way a
 * one-shot command line tool does; no session needs to be kept alive, because a
 * single command can still hold an action down for a fixed time.
 *
 * Results go to stdout, failures to stderr, so `th10ctl -j snapshot` can be
 * piped straight into other tools, and no command ever waits for input, so the
 * tool is safe to run from a script.
 */

#include "auto_th10/auto_th10.h"

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

typedef struct action_entry {
    uint32_t value;
    const char *name;
} action_entry;

static const action_entry ACTIONS[] = {
    {TH10_ACTION_LEFT, "left"},
    {TH10_ACTION_RIGHT, "right"},
    {TH10_ACTION_UP, "up"},
    {TH10_ACTION_DOWN, "down"},
    {TH10_ACTION_SHOOT, "shoot"},
    {TH10_ACTION_FOCUS, "focus"},
    {TH10_ACTION_BOMB, "bomb"},
    {TH10_ACTION_ESCAPE, "escape"},
};

#define ACTION_COUNT (sizeof(ACTIONS) / sizeof(ACTIONS[0]))

static bool g_json = false;
static bool g_verbose = false;
static th10_session *g_session = NULL;
static uint32_t g_action_mask = 0;
static volatile LONG g_interrupted = 0;

static void print_usage(void) {
    printf("Usage: th10ctl [options] [command [arguments...]]\n"
           "\n"
           "Drives a running Touhou 10 (Mountain of Faith) process through the\n"
           "auto-th10 C binding. Every command attaches, acts, and detaches.\n"
           "\n"
           "Options:\n"
           "  -j, --json      print results as JSON\n"
           "  -v, --verbose   list every element of a snapshot\n"
           "  -h, --help      show this help\n"
           "\n"
           "Commands:\n"
           "  info                      attach to the game and report the session\n"
           "  focus                     hand the game the keyboard focus, so that it\n"
           "                            receives the keys injected by hold\n"
           "  scene                     report the screen family: menu or stage (one\n"
           "                            read, no waiting)\n"
           "  state                     report the screen: menu / playing / paused /\n"
           "                            game over\n"
           "  ui [cursor]               report the screen the game is driving and its\n"
           "                            cursor (ending menu vs. the name entry behind\n"
           "                            it); with a cursor, move that cursor instead\n"
           "  frames                    report the stage frame counter: it advances while\n"
           "                            playing and freezes while paused\n"
           "  snapshot                  read and print one snapshot\n"
           "  shot [file.bmp]           capture the game window (works in the background)\n"
           "  hold <spec> <ms>          press actions for <ms>, then release them again\n"
           "  watch [interval_ms] [n]   read snapshots repeatedly; n defaults to 10 and\n"
           "                            0 means until interrupted\n"
           "  windows                   list visible top level windows\n"
           "  launch <th10.exe> [ms]    start the game and wait for its window\n"
           "  help                      show this help\n"
           "\n"
           "Actions: left right up down shoot focus bomb escape\n"
           "\n"
           "An action spec is a space or comma separated list; '+' and '-' adjust what\n"
           "is held instead of replacing it:\n"
           "  th10ctl hold \"shoot focus\" 500     hold both for 500 ms\n"
           "  th10ctl hold \"+left\" 300          add left to the held actions\n"
           "  th10ctl hold none 100              release everything for 100 ms\n");
}

#define RETURN_TAG_NAME(tag_value)                                                                 \
    case tag_value:                                                                                \
        return #tag_value;

static const char *open_tag_name(th10_open_result_tag tag) {
    switch (tag) {
        RETURN_TAG_NAME(TH10_OPEN_SUCCESS)
        RETURN_TAG_NAME(TH10_OPEN_WINDOW_NOT_FOUND)
        RETURN_TAG_NAME(TH10_OPEN_ENUM_WINDOWS_FAILED)
        RETURN_TAG_NAME(TH10_OPEN_GET_PROCESS_ID_FAILED)
        RETURN_TAG_NAME(TH10_OPEN_PROCESS_FAILED)
        RETURN_TAG_NAME(TH10_OPEN_OUT_OF_MEMORY)
    }
    return "TH10_OPEN_UNKNOWN";
}

static const char *focus_tag_name(th10_focus_result_tag tag) {
    switch (tag) {
        RETURN_TAG_NAME(TH10_FOCUS_SUCCESS)
        RETURN_TAG_NAME(TH10_FOCUS_INVALID_SESSION)
        RETURN_TAG_NAME(TH10_FOCUS_PERMISSION_AND_SET_REJECTED)
        RETURN_TAG_NAME(TH10_FOCUS_REJECTED)
    }
    return "TH10_FOCUS_UNKNOWN";
}

static const char *input_tag_name(th10_input_result_tag tag) {
    switch (tag) {
        RETURN_TAG_NAME(TH10_INPUT_SUCCESS)
        RETURN_TAG_NAME(TH10_INPUT_INVALID_SESSION)
        RETURN_TAG_NAME(TH10_INPUT_UNSUPPORTED_ACTION)
        RETURN_TAG_NAME(TH10_INPUT_SEND_FAILED)
    }
    return "TH10_INPUT_UNKNOWN";
}

static const char *close_tag_name(th10_close_result_tag tag) {
    switch (tag) {
        RETURN_TAG_NAME(TH10_CLOSE_SUCCESS)
        RETURN_TAG_NAME(TH10_CLOSE_INVALID_SESSION)
        RETURN_TAG_NAME(TH10_CLOSE_INPUT_RELEASE_FAILED)
        RETURN_TAG_NAME(TH10_CLOSE_HANDLE_FAILED)
        RETURN_TAG_NAME(TH10_CLOSE_INPUT_AND_HANDLE_FAILED)
    }
    return "TH10_CLOSE_UNKNOWN";
}

static const char *snapshot_tag_name(th10_snapshot_result_tag tag) {
    switch (tag) {
        RETURN_TAG_NAME(TH10_SNAPSHOT_SUCCESS)
        RETURN_TAG_NAME(TH10_SNAPSHOT_INVALID_ARGUMENT)
        RETURN_TAG_NAME(TH10_SNAPSHOT_NOT_IN_GAME)
        RETURN_TAG_NAME(TH10_SNAPSHOT_READ_FAILED)
        RETURN_TAG_NAME(TH10_SNAPSHOT_ALLOCATION_FAILED)
    }
    return "TH10_SNAPSHOT_UNKNOWN";
}

static const char *capture_tag_name(th10_capture_result_tag tag) {
    switch (tag) {
        RETURN_TAG_NAME(TH10_CAPTURE_SUCCESS)
        RETURN_TAG_NAME(TH10_CAPTURE_INVALID_ARGUMENT)
        RETURN_TAG_NAME(TH10_CAPTURE_CLIENT_RECT_FAILED)
        RETURN_TAG_NAME(TH10_CAPTURE_CREATE_DC_FAILED)
        RETURN_TAG_NAME(TH10_CAPTURE_CREATE_BITMAP_FAILED)
        RETURN_TAG_NAME(TH10_CAPTURE_PRINT_WINDOW_FAILED)
        RETURN_TAG_NAME(TH10_CAPTURE_FILE_OPEN_FAILED)
        RETURN_TAG_NAME(TH10_CAPTURE_FILE_WRITE_FAILED)
    }
    return "TH10_CAPTURE_UNKNOWN";
}

static const char *state_name(th10_state state) {
    switch (state) {
        RETURN_TAG_NAME(TH10_STATE_UNKNOWN)
        RETURN_TAG_NAME(TH10_STATE_MENU)
        RETURN_TAG_NAME(TH10_STATE_PLAYING)
        RETURN_TAG_NAME(TH10_STATE_PAUSED)
        RETURN_TAG_NAME(TH10_STATE_GAME_OVER)
    }
    return "TH10_STATE_UNKNOWN";
}

static const char *scene_name(th10_scene scene) {
    switch (scene) {
        RETURN_TAG_NAME(TH10_SCENE_UNKNOWN)
        RETURN_TAG_NAME(TH10_SCENE_MENU)
        RETURN_TAG_NAME(TH10_SCENE_STAGE)
    }
    return "TH10_SCENE_UNKNOWN";
}

#undef RETURN_TAG_NAME

static void print_json_string(const char *text) {
    putchar('"');
    for (; *text != '\0'; ++text) {
        const unsigned char character = (unsigned char)*text;
        switch (character) {
        case '"':
            fputs("\\\"", stdout);
            break;
        case '\\':
            fputs("\\\\", stdout);
            break;
        case '\n':
            fputs("\\n", stdout);
            break;
        case '\r':
            fputs("\\r", stdout);
            break;
        case '\t':
            fputs("\\t", stdout);
            break;
        default:
            if (character < 0x20) {
                printf("\\u%04x", (unsigned)character);
            } else {
                putchar((int)character);
            }
            break;
        }
    }
    putchar('"');
}

static void format_actions(uint32_t mask, char *buffer, size_t size) {
    size_t used = 0;
    size_t index;

    if (size == 0) {
        return;
    }
    buffer[0] = '\0';
    for (index = 0; index < ACTION_COUNT; ++index) {
        const char *name;
        size_t length;

        if ((mask & ACTIONS[index].value) == 0) {
            continue;
        }
        name = ACTIONS[index].name;
        length = strlen(name);
        if (used + length + 2 > size) {
            break;
        }
        if (used != 0) {
            buffer[used++] = '|';
        }
        memcpy(buffer + used, name, length);
        used += length;
        buffer[used] = '\0';
    }
    if (used == 0) {
        (void)snprintf(buffer, size, "none");
    }
}

static void print_open_failure(const th10_open_result *result) {
    uint32_t win32_error = 0;
    bool has_win32_error = false;

    switch (result->tag) {
    case TH10_OPEN_ENUM_WINDOWS_FAILED:
    case TH10_OPEN_GET_PROCESS_ID_FAILED:
    case TH10_OPEN_PROCESS_FAILED:
        win32_error = result->value.win32_error.code;
        has_win32_error = true;
        break;
    default:
        break;
    }

    if (g_json) {
        fputs("{\"error\":\"open\",\"tag\":\"", stderr);
        fputs(open_tag_name(result->tag), stderr);
        fputc('"', stderr);
        if (has_win32_error) {
            fprintf(stderr, ",\"win32_error\":%lu", (unsigned long)win32_error);
        }
        fputs("}\n", stderr);
    } else {
        fprintf(stderr, "open failed: %s", open_tag_name(result->tag));
        if (has_win32_error) {
            fprintf(stderr, " (win32 error %lu)", (unsigned long)win32_error);
        }
        fputc('\n', stderr);
        if (result->tag == TH10_OPEN_WINDOW_NOT_FOUND) {
            fputs("  hint: the game must be running; 'th10ctl windows' lists candidates\n",
                  stderr);
        }
    }
}

typedef enum attach_mode {
    ATTACH_QUIET,  /* report neither failures nor success */
    ATTACH_NORMAL, /* report failures only */
} attach_mode;

static bool attach_session(attach_mode mode) {
    th10_open_result result;

    if (g_session != NULL) {
        return true;
    }
    result = th10_open();
    if (result.tag != TH10_OPEN_SUCCESS) {
        if (mode != ATTACH_QUIET) {
            print_open_failure(&result);
        }
        return false;
    }
    g_session = result.value.session;
    g_action_mask = 0;
    return true;
}

static bool detach_session(void) {
    th10_close_result result;

    if (g_session == NULL) {
        return true;
    }
    result = th10_close(g_session);
    g_session = NULL;
    g_action_mask = 0;
    if (result.tag == TH10_CLOSE_SUCCESS) {
        return true;
    }

    if (g_json) {
        fputs("{\"error\":\"close\",\"tag\":\"", stderr);
        fputs(close_tag_name(result.tag), stderr);
        fputs("\"", stderr);
        if (result.tag == TH10_CLOSE_HANDLE_FAILED) {
            fprintf(stderr, ",\"win32_error\":%lu", (unsigned long)result.value.handle_failed.win32_error);
        }
        fputs("}\n", stderr);
    } else {
        fprintf(stderr, "close failed: %s\n", close_tag_name(result.tag));
        if (result.tag == TH10_CLOSE_HANDLE_FAILED) {
            fprintf(stderr, "  win32 error %lu\n", (unsigned long)result.value.handle_failed.win32_error);
        }
    }
    return false;
}

static void print_point(const th10_point *point) {
    printf("(%.2f, %.2f)", (double)point->x, (double)point->y);
}

static void print_snapshot_human(const th10_snapshot *snapshot, bool verbose) {
    size_t index;

    printf("player=");
    print_point(&snapshot->player);
    printf(" score=%lu power=%u lives=%d game_over=%s\n", (unsigned long)snapshot->score,
           (unsigned)snapshot->power, (int)snapshot->lives, snapshot->game_over ? "yes" : "no");
    printf("  enemies=%zu enemy_bullets=%zu enemy_lasers=%zu resources=%zu\n",
           snapshot->enemies.size, snapshot->enemy_bullets.size, snapshot->enemy_lasers.size,
           snapshot->resources.size);

    if (!verbose) {
        return;
    }
    for (index = 0; index < snapshot->enemies.size; ++index) {
        const th10_rect *enemy = &snapshot->enemies.data[index];
        printf("    enemy[%zu] pos=(%.2f, %.2f) size=%.2f x %.2f\n", index, (double)enemy->x,
               (double)enemy->y, (double)enemy->width, (double)enemy->height);
    }
    for (index = 0; index < snapshot->enemy_bullets.size; ++index) {
        const th10_enemy_bullet *bullet = &snapshot->enemy_bullets.data[index];
        printf("    bullet[%zu] pos=(%.2f, %.2f) size=%.2f x %.2f delta=(%.2f, %.2f)\n", index,
               (double)bullet->x, (double)bullet->y, (double)bullet->width, (double)bullet->height,
               (double)bullet->dx, (double)bullet->dy);
    }
    for (index = 0; index < snapshot->enemy_lasers.size; ++index) {
        const th10_enemy_laser *laser = &snapshot->enemy_lasers.data[index];
        printf("    laser[%zu] pos=(%.2f, %.2f) size=%.2f x %.2f radian=%.4f\n", index,
               (double)laser->x, (double)laser->y, (double)laser->width, (double)laser->height,
               (double)laser->radian);
    }
    for (index = 0; index < snapshot->resources.size; ++index) {
        printf("    resource[%zu] pos=(%.2f, %.2f)\n", index,
               (double)snapshot->resources.data[index].x, (double)snapshot->resources.data[index].y);
    }
}

static void print_snapshot_json(const th10_snapshot *snapshot) {
    size_t index;

    printf("{\"player\":[%.9g,%.9g],\"score\":%lu,\"power\":%u,\"lives\":%d,\"game_over\":%s,",
           (double)snapshot->player.x, (double)snapshot->player.y, (unsigned long)snapshot->score,
           (unsigned)snapshot->power, (int)snapshot->lives, snapshot->game_over ? "true" : "false");

    fputs("\"enemies\":[", stdout);
    for (index = 0; index < snapshot->enemies.size; ++index) {
        const th10_rect *enemy = &snapshot->enemies.data[index];
        printf("%s[%.9g,%.9g,%.9g,%.9g]", index == 0 ? "" : ",", (double)enemy->x, (double)enemy->y,
               (double)enemy->width, (double)enemy->height);
    }
    fputs("],\"enemy_bullets\":[", stdout);
    for (index = 0; index < snapshot->enemy_bullets.size; ++index) {
        const th10_enemy_bullet *bullet = &snapshot->enemy_bullets.data[index];
        printf("%s[%.9g,%.9g,%.9g,%.9g,%.9g,%.9g]", index == 0 ? "" : ",", (double)bullet->x,
               (double)bullet->y, (double)bullet->width, (double)bullet->height, (double)bullet->dx,
               (double)bullet->dy);
    }
    fputs("],\"enemy_lasers\":[", stdout);
    for (index = 0; index < snapshot->enemy_lasers.size; ++index) {
        const th10_enemy_laser *laser = &snapshot->enemy_lasers.data[index];
        printf("%s[%.9g,%.9g,%.9g,%.9g,%.9g]", index == 0 ? "" : ",", (double)laser->x,
               (double)laser->y, (double)laser->width, (double)laser->height, (double)laser->radian);
    }
    fputs("],\"resources\":[", stdout);
    for (index = 0; index < snapshot->resources.size; ++index) {
        printf("%s[%.9g,%.9g]", index == 0 ? "" : ",", (double)snapshot->resources.data[index].x,
               (double)snapshot->resources.data[index].y);
    }
    fputs("]}\n", stdout);
}

static void print_snapshot_failure(const th10_snapshot_result *result) {
    if (g_json) {
        fputs("{\"error\":\"snapshot\",\"tag\":\"", stderr);
        fputs(snapshot_tag_name(result->tag), stderr);
        fputc('"', stderr);
        switch (result->tag) {
        case TH10_SNAPSHOT_READ_FAILED:
            fprintf(stderr, ",\"address\":%lu,\"requested_size\":%zu,\"bytes_read\":%zu,\"win32_error\":%lu",
                    (unsigned long)result->value.read_failed.address,
                    result->value.read_failed.requested_size, result->value.read_failed.bytes_read,
                    (unsigned long)result->value.read_failed.win32_error);
            break;
        case TH10_SNAPSHOT_ALLOCATION_FAILED:
            fprintf(stderr, ",\"requested_capacity\":%zu,\"element_size\":%zu",
                    result->value.allocation_failed.requested_capacity,
                    result->value.allocation_failed.element_size);
            break;
        default:
            break;
        }
        fputs("}\n", stderr);
    } else {
        fprintf(stderr, "snapshot failed: %s\n", snapshot_tag_name(result->tag));
        if (result->tag == TH10_SNAPSHOT_READ_FAILED) {
            fprintf(stderr, "  address=0x%lx requested=%zu read=%zu win32 error=%lu\n",
                    (unsigned long)result->value.read_failed.address,
                    result->value.read_failed.requested_size, result->value.read_failed.bytes_read,
                    (unsigned long)result->value.read_failed.win32_error);
        }
        if (result->tag == TH10_SNAPSHOT_NOT_IN_GAME) {
            fputs("  hint: start a stage; the addresses are only valid in game\n", stderr);
        }
    }
}

static bool read_snapshot(th10_snapshot *snapshot) {
    th10_snapshot_result result;

    result = th10_read_snapshot(g_session, snapshot);
    if (result.tag != TH10_SNAPSHOT_SUCCESS) {
        print_snapshot_failure(&result);
        return false;
    }
    return true;
}

static bool command_snapshot(void) {
    th10_snapshot snapshot;
    bool ok;

    if (!attach_session(ATTACH_NORMAL)) {
        return false;
    }
    th10_snapshot_init(&snapshot);
    ok = read_snapshot(&snapshot);
    if (ok) {
        if (g_json) {
            print_snapshot_json(&snapshot);
        } else {
            print_snapshot_human(&snapshot, g_verbose);
        }
    }
    th10_snapshot_destroy(&snapshot);
    return ok;
}

static bool report_focus(const th10_focus_result *result) {
    if (result->tag == TH10_FOCUS_SUCCESS) {
        return true;
    }

    if (g_json) {
        fputs("{\"error\":\"focus\",\"tag\":\"", stderr);
        fputs(focus_tag_name(result->tag), stderr);
        fputs("\"", stderr);
        if (result->tag == TH10_FOCUS_PERMISSION_AND_SET_REJECTED) {
            fprintf(stderr, ",\"win32_error\":%lu",
                    (unsigned long)result->value.permission_and_set_rejected.win32_error);
        }
        fputs("}\n", stderr);
    } else {
        fprintf(stderr, "focus failed: %s\n", focus_tag_name(result->tag));
        if (result->tag == TH10_FOCUS_PERMISSION_AND_SET_REJECTED) {
            fprintf(stderr, "  win32 error %lu\n",
                    (unsigned long)result->value.permission_and_set_rejected.win32_error);
        }
    }
    return false;
}

static bool command_focus(void) {
    th10_focus_result result;

    if (!attach_session(ATTACH_NORMAL)) {
        return false;
    }
    result = th10_focus(g_session);
    if (!report_focus(&result)) {
        return false;
    }

    if (g_json) {
        fputs("{\"focus\":\"success\"}\n", stdout);
    } else {
        fputs("focus: ok\n", stdout);
    }
    return true;
}

static bool command_watch(int interval_ms, long count) {
    th10_snapshot snapshot;
    ULONGLONG start;
    long iteration = 0;

    if (!attach_session(ATTACH_NORMAL)) {
        return false;
    }
    th10_snapshot_init(&snapshot);
    start = GetTickCount64();
    while (count <= 0 || iteration < count) {
        const double elapsed = (double)(GetTickCount64() - start) / 1000.0;

        if (!read_snapshot(&snapshot)) {
            th10_snapshot_destroy(&snapshot);
            return false;
        }
        if (g_json) {
            print_snapshot_json(&snapshot);
        } else {
            printf("%7.2fs  player=(%7.2f,%7.2f)  score=%lu  power=%u  lives=%d  bullets=%zu  lasers=%zu"
                   "  enemies=%zu  resources=%zu  game_over=%s\n",
                   elapsed, (double)snapshot.player.x, (double)snapshot.player.y,
                   (unsigned long)snapshot.score, (unsigned)snapshot.power, (int)snapshot.lives,
                   snapshot.enemy_bullets.size, snapshot.enemy_lasers.size, snapshot.enemies.size,
                   snapshot.resources.size, snapshot.game_over ? "yes" : "no");
        }
        fflush(stdout);
        ++iteration;
        if (count > 0 && iteration >= count) {
            break;
        }
        Sleep((DWORD)interval_ms);

        if (InterlockedExchange(&g_interrupted, 0) != 0) {
            if (!g_json) {
                fputs("watch interrupted\n", stdout);
            }
            break;
        }
    }
    th10_snapshot_destroy(&snapshot);
    return true;
}

typedef struct window_scan {
    size_t count;
} window_scan;

static BOOL CALLBACK scan_window(HWND window, LPARAM parameter) {
    window_scan *scan = (window_scan *)parameter;
    char title[256];
    char process_name[260] = "";
    DWORD process_id = 0;
    HANDLE process;
    wchar_t path[32768];
    DWORD path_length = (DWORD)(sizeof(path) / sizeof(path[0]));

    if (!IsWindowVisible(window)) {
        return TRUE;
    }
    if (GetWindowTextA(window, title, (int)sizeof(title)) <= 0) {
        return TRUE;
    }
    (void)GetWindowThreadProcessId(window, &process_id);
    process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, process_id);
    if (process != NULL) {
        if (QueryFullProcessImageNameW(process, 0, path, &path_length)) {
            const wchar_t *name = wcsrchr(path, L'\\');
            name = name == NULL ? path : name + 1;
            (void)WideCharToMultiByte(CP_ACP, 0, name, -1, process_name, (int)sizeof(process_name),
                                      NULL, NULL);
        }
        CloseHandle(process);
    }

    if (g_json) {
        if (scan->count != 0) {
            putchar(',');
        }
        printf("{\"hwnd\":\"0x%llx\",\"pid\":%lu,\"process\":",
               (unsigned long long)(uintptr_t)window, (unsigned long)process_id);
        print_json_string(process_name);
        fputs(",\"title\":", stdout);
        print_json_string(title);
        putchar('}');
    } else {
        printf("  hwnd=0x%p pid=%-6lu %-16s %s\n", (void *)window, (unsigned long)process_id,
               process_name, title);
    }
    ++scan->count;
    return TRUE;
}

static bool command_windows(void) {
    window_scan scan = {0};

    if (g_json) {
        putchar('[');
    } else {
        fputs("visible top level windows:\n", stdout);
    }
    if (!EnumWindows(scan_window, (LPARAM)&scan)) {
        fprintf(stderr, "EnumWindows failed (win32 error %lu)\n", (unsigned long)GetLastError());
        if (g_json) {
            fputs("]\n", stdout);
        }
        return false;
    }
    if (g_json) {
        fputs("]\n", stdout);
    } else if (scan.count == 0) {
        fputs("  (none)\n", stdout);
    }
    return true;
}

static bool to_wide(const char *text, wchar_t *output, size_t capacity) {
    return MultiByteToWideChar(CP_ACP, 0, text, -1, output, (int)capacity) != 0;
}

static bool command_launch(const char *executable, int timeout_ms) {
    STARTUPINFOW startup;
    PROCESS_INFORMATION process;
    wchar_t path[32768];
    wchar_t working_directory[32768];
    wchar_t *separator;
    ULONGLONG deadline;
    size_t length;

    if (!to_wide(executable, path, sizeof(path) / sizeof(path[0]))) {
        fprintf(stderr, "cannot convert path: %s (win32 error %lu)\n", executable,
                (unsigned long)GetLastError());
        return false;
    }
    for (length = 0; path[length] != L'\0'; ++length) {
    }
    if (length >= sizeof(working_directory) / sizeof(working_directory[0])) {
        fputs("path is too long\n", stderr);
        return false;
    }
    memcpy(working_directory, path, (length + 1) * sizeof(wchar_t));
    separator = wcsrchr(working_directory, L'\\');
    if (separator != NULL) {
        *separator = L'\0';
    } else {
        working_directory[0] = L'.';
        working_directory[1] = L'\0';
    }

    ZeroMemory(&startup, sizeof(startup));
    ZeroMemory(&process, sizeof(process));
    startup.cb = sizeof(startup);
    if (!CreateProcessW(path, NULL, NULL, NULL, FALSE, 0, NULL, working_directory, &startup,
                        &process)) {
        fprintf(stderr, "CreateProcess failed for %s (win32 error %lu)\n", executable,
                (unsigned long)GetLastError());
        return false;
    }
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);

    deadline = GetTickCount64() + (ULONGLONG)timeout_ms;
    while (GetTickCount64() < deadline) {
        if (attach_session(ATTACH_QUIET)) {
            if (g_json) {
                fputs("{\"launch\":\"success\",\"executable\":", stdout);
                print_json_string(executable);
                fputs("}\n", stdout);
            } else {
                printf("launched %s and attached\n", executable);
            }
            return true;
        }
        Sleep(250);
    }
    fprintf(stderr, "no th10 window appeared within %d ms\n", timeout_ms);
    return false;
}

/* Reports the screen the game is on, read from its own state words. */
static bool command_state(void) {
    th10_state state;

    if (!attach_session(ATTACH_NORMAL)) {
        return false;
    }
    state = th10_read_state(g_session);
    if (g_json) {
        printf("{\"state\":\"%s\"}\n", state_name(state));
    } else {
        printf("state: %s\n", state_name(state));
    }
    return state != TH10_STATE_UNKNOWN;
}

/* Reports the family of screen the game is on: one read of one word, no waiting,
 * and the cheap way to ask whether keys would land on a stage at all. */
static bool command_scene(void) {
    th10_scene scene;

    if (!attach_session(ATTACH_NORMAL)) {
        return false;
    }
    scene = th10_read_scene(g_session);
    if (g_json) {
        printf("{\"scene\":\"%s\"}\n", scene_name(scene));
    } else {
        printf("scene: %s\n", scene_name(scene));
    }
    return scene != TH10_SCENE_UNKNOWN;
}

/* parse_long() is defined with the other argument helpers further down; the one
 * command that parses its number this early is `ui`. */
static bool parse_long(const char *text, long minimum, long maximum, long *out);

/* Reports why a cursor could not be moved, the way the other commands report a
 * failure: the tag that came back, and the Win32 detail when there is one. */
static bool report_write_failure(th10_write_result result) {
    if (g_json) {
        fputs("{\"error\":\"ui\"}\n", stderr);
        return false;
    }
    switch (result.tag) {
    case TH10_WRITE_UNSUPPORTED_SCREEN:
        fputs("ui failed: the screen the game is driving keeps no cursor\n", stderr);
        break;
    case TH10_WRITE_INVALID_ARGUMENT:
        fprintf(stderr, "ui failed: entry %d is outside the cursor's range 0..%d\n",
                (int)result.value.invalid_argument.entry,
                (int)result.value.invalid_argument.count - 1);
        break;
    case TH10_WRITE_READ_FAILED:
        fprintf(stderr,
                "ui failed: ReadProcessMemory at 0x%llx requested %zu bytes, read %zu "
                "(Win32 error %lu)\n",
                (unsigned long long)result.value.read_failed.address,
                result.value.read_failed.requested_size, result.value.read_failed.bytes_read,
                (unsigned long)result.value.read_failed.win32_error);
        break;
    case TH10_WRITE_FAILED:
        fprintf(stderr,
                "ui failed: WriteProcessMemory at 0x%llx requested %zu bytes, wrote %zu "
                "(Win32 error %lu)\n",
                (unsigned long long)result.value.write_failed.address,
                result.value.write_failed.requested_size, result.value.write_failed.bytes_written,
                (unsigned long)result.value.write_failed.win32_error);
        break;
    default:
        fputs("ui failed: invalid session or result\n", stderr);
        break;
    }
    return false;
}

/* Reports which screen the game is driving and that screen's cursor: the read
 * that tells the ending's menu from the Score Ranking name entry behind it, and
 * the only one that says where either screen's highlight sits.
 *
 * Given a cursor it moves that cursor instead of reporting it, which is the write
 * side of the same field: `ui 90` highlights 終 on the name entry, and one `hold
 * shoot` after it writes the record - the eighteen presses the grid would
 * otherwise cost are not needed, and neither is the foreground. */
static bool command_ui(const char *cursor_text) {
    static const char *names[] = {
        "TH10_UI_SCREEN_UNKNOWN",
        "TH10_UI_SCREEN_STAGE",
        "TH10_UI_SCREEN_MENU",
        "TH10_UI_SCREEN_NAME_ENTRY",
    };
    th10_ui_result result;
    const char *screen;

    if (!attach_session(ATTACH_NORMAL)) {
        return false;
    }
    if (cursor_text != NULL) {
        th10_write_result written;
        long entry = 0;

        if (!parse_long(cursor_text, 0, 0x7FFFFFFF, &entry)) {
            fprintf(stderr, "ui: '%s' is not a cursor position\n", cursor_text);
            return false;
        }
        written = th10_write_ui_cursor(g_session, (int32_t)entry);
        if (written.tag != TH10_WRITE_SUCCESS) {
            return report_write_failure(written);
        }
    }
    result = th10_read_ui(g_session);
    if (result.tag != TH10_UI_SUCCESS) {
        if (g_json) {
            fputs("{\"error\":\"ui\"}\n", stderr);
        } else if (result.tag == TH10_UI_READ_FAILED) {
            fprintf(stderr,
                    "ui failed: ReadProcessMemory at 0x%llx requested %zu bytes, read %zu "
                    "(Win32 error %lu)\n",
                    (unsigned long long)result.value.read_failed.address,
                    result.value.read_failed.requested_size,
                    result.value.read_failed.bytes_read,
                    (unsigned long)result.value.read_failed.win32_error);
        } else {
            fputs("ui failed: invalid session or result\n", stderr);
        }
        return false;
    }
    screen = names[(unsigned int)result.value.ui.screen];
    if (g_json) {
        printf("{\"screen\":\"%s\",\"cursor\":%d}\n", screen, (int)result.value.ui.cursor);
    } else {
        printf("ui: %s cursor=%d\n", screen, (int)result.value.ui.cursor);
    }
    return true;
}

/* Reports the stage frame counter, the game's own clock: it advances while a
 * stage is playing and freezes while it is paused. Read twice, it tells "the
 * game moved" from "the game stopped" without the 120 ms th10_read_state()
 * spends on the same answer. */
static bool command_frames(void) {
    th10_frames_result result;
    uint32_t frames;

    if (!attach_session(ATTACH_NORMAL)) {
        return false;
    }
    result = th10_read_stage_frames(g_session);
    if (result.tag != TH10_FRAMES_SUCCESS) {
        if (g_json) {
            fputs("{\"error\":\"frames\"}\n", stderr);
        } else if (result.tag == TH10_FRAMES_READ_FAILED) {
            fprintf(stderr,
                    "frames failed: ReadProcessMemory at 0x%llx requested %zu bytes, read %zu "
                    "(Win32 error %lu)\n",
                    (unsigned long long)result.value.read_failed.address,
                    result.value.read_failed.requested_size,
                    result.value.read_failed.bytes_read,
                    (unsigned long)result.value.read_failed.win32_error);
        } else {
            fputs("frames failed: invalid session or result\n", stderr);
        }
        return false;
    }
    frames = result.value.frames;
    if (g_json) {
        printf("{\"frames\":%lu}\n", (unsigned long)frames);
    } else {
        printf("frames: %lu\n", (unsigned long)frames);
    }
    return true;
}

static bool command_info(void) {
    if (!attach_session(ATTACH_NORMAL)) {
        return false;
    }
    if (g_json) {
        fputs("{\"attached\":true}\n", stdout);
    } else {
        fputs("attached to the game window\n", stdout);
    }
    return true;
}

/* Capturing needs neither the focus nor the foreground: PrintWindow makes the
 * window draw itself, so the game can stay in the background while we look at
 * it. This is why the command deliberately does not call th10_focus. */
static bool command_capture(const char *path) {
    th10_capture_result result;
    wchar_t wide[32768];

    if (path == NULL || *path == '\0') {
        path = "th10_capture.bmp";
    }
    if (!attach_session(ATTACH_NORMAL)) {
        return false;
    }
    if (!to_wide(path, wide, sizeof(wide) / sizeof(wide[0]))) {
        fprintf(stderr, "shot: cannot convert path '%s' (win32 error %lu)\n", path,
                (unsigned long)GetLastError());
        return false;
    }

    result = th10_capture(g_session, wide);
    if (result.tag != TH10_CAPTURE_SUCCESS) {
        if (g_json) {
            fputs("{\"error\":\"capture\",\"tag\":\"", stderr);
            fputs(capture_tag_name(result.tag), stderr);
            fputs("\"", stderr);
            if (result.win32_error != 0) {
                fprintf(stderr, ",\"win32_error\":%lu", (unsigned long)result.win32_error);
            }
            fputs("}\n", stderr);
        } else {
            fprintf(stderr, "capture failed: %s\n", capture_tag_name(result.tag));
            if (result.win32_error != 0) {
                fprintf(stderr, "  win32 error %lu\n", (unsigned long)result.win32_error);
            }
        }
        return false;
    }

    if (g_json) {
        fputs("{\"capture\":\"success\",\"path\":", stdout);
        print_json_string(path);
        printf(",\"width\":%lu,\"height\":%lu}\n", (unsigned long)result.width,
               (unsigned long)result.height);
    } else {
        printf("captured %lux%lu -> %s\n", (unsigned long)result.width, (unsigned long)result.height,
               path);
    }
    return true;
}

static bool command_input(uint32_t *mask) {
    th10_input_result result;
    th10_focus_result focus_result;
    char actions[128];

    if (!attach_session(ATTACH_NORMAL)) {
        return false;
    }
    /* Keys only reach a DirectInput game while its window owns the keyboard
     * focus. Injecting while the terminal is in front sends the keys to the
     * terminal instead (the IME picks them up) and the game sees nothing. */
    focus_result = th10_focus(g_session);
    if (!report_focus(&focus_result)) {
        return false;
    }
    result = th10_set_input(g_session, *mask);
    if (result.tag != TH10_INPUT_SUCCESS) {
        if (g_json) {
            fputs("{\"error\":\"input\",\"tag\":\"", stderr);
            fputs(input_tag_name(result.tag), stderr);
            fputs("\"", stderr);
            if (result.tag == TH10_INPUT_UNSUPPORTED_ACTION) {
                fprintf(stderr, ",\"unsupported_bits\":%lu",
                        (unsigned long)result.value.unsupported_action.unsupported_bits);
            }
            if (result.tag == TH10_INPUT_SEND_FAILED) {
                fprintf(stderr, ",\"requested_count\":%lu,\"inserted_count\":%lu,\"win32_error\":%lu",
                        (unsigned long)result.value.send_failed.requested_count,
                        (unsigned long)result.value.send_failed.inserted_count,
                        (unsigned long)result.value.send_failed.win32_error);
            }
            fputs("}\n", stderr);
        } else {
            fprintf(stderr, "input failed: %s\n", input_tag_name(result.tag));
            if (result.tag == TH10_INPUT_UNSUPPORTED_ACTION) {
                fprintf(stderr, "  unsupported bits 0x%lx\n",
                        (unsigned long)result.value.unsupported_action.unsupported_bits);
            }
            if (result.tag == TH10_INPUT_SEND_FAILED) {
                fprintf(stderr, "  requested=%lu inserted=%lu win32 error=%lu\n",
                        (unsigned long)result.value.send_failed.requested_count,
                        (unsigned long)result.value.send_failed.inserted_count,
                        (unsigned long)result.value.send_failed.win32_error);
            }
        }
        return false;
    }

    g_action_mask = *mask;
    format_actions(*mask, actions, sizeof(actions));
    if (g_json) {
        printf("{\"input\":\"success\",\"mask\":%lu,\"actions\":\"%s\"}\n", (unsigned long)*mask,
               actions);
    } else {
        printf("input: %s\n", actions);
    }
    return true;
}

static char *next_token(char **cursor) {
    char *start = *cursor;
    char *end;

    while (*start == ' ' || *start == '\t') {
        ++start;
    }
    if (*start == '\0') {
        *cursor = start;
        return NULL;
    }
    end = start;
    while (*end != '\0' && *end != ' ' && *end != '\t') {
        ++end;
    }
    if (*end == '\0') {
        *cursor = end;
    } else {
        *end = '\0';
        *cursor = end + 1;
    }
    return start;
}

static bool parse_action_spec(const char *spec, uint32_t current, uint32_t *out, char *error,
                              size_t error_size) {
    char buffer[256];
    char *cursor;
    char *token;
    uint32_t mask = current;
    bool absolute_started = false;
    size_t index;

    if (strlen(spec) >= sizeof(buffer)) {
        (void)snprintf(error, error_size, "spec is too long");
        return false;
    }
    memcpy(buffer, spec, strlen(spec) + 1);
    for (index = 0; buffer[index] != '\0'; ++index) {
        if (buffer[index] == ',') {
            buffer[index] = ' ';
        }
    }

    cursor = buffer;
    while ((token = next_token(&cursor)) != NULL) {
        const char *name = token;
        uint32_t value = 0;
        bool known = false;
        int sign = 0;

        if (*name == '+') {
            sign = 1;
            ++name;
        } else if (*name == '-') {
            sign = -1;
            ++name;
        }

        if (strcmp(name, "none") == 0) {
            if (sign != 0) {
                (void)snprintf(error, error_size, "'%s' cannot take a '+' or '-' prefix", name);
                return false;
            }
            if (!absolute_started) {
                absolute_started = true;
                mask = 0;
            }
            continue;
        }

        for (index = 0; index < ACTION_COUNT; ++index) {
            if (strcmp(name, ACTIONS[index].name) == 0) {
                value = ACTIONS[index].value;
                known = true;
                break;
            }
        }
        if (!known) {
            char *end = NULL;
            unsigned long number;

            errno = 0;
            number = strtoul(name, &end, 0);
            if (end != name && end != NULL && *end == '\0' && errno == 0) {
                value = (uint32_t)number;
                known = true;
            }
        }
        if (!known) {
            (void)snprintf(error, error_size, "unknown action '%s'", token);
            return false;
        }

        if (sign == 0) {
            if (!absolute_started) {
                absolute_started = true;
                mask = 0;
            }
            mask |= value;
        } else if (sign > 0) {
            mask |= value;
        } else {
            mask &= ~value;
        }
    }

    *out = mask;
    return true;
}

/* Presses the given actions, holds them for a fixed time, then puts the session
 * back the way it was. The press and the release have to be timed inside this
 * process rather than by whatever drives the tool: a game that polls its input
 * every frame cannot be driven by keystrokes whose duration depends on how
 * quickly the next command arrives. */
static bool command_hold(const char *spec, int duration_ms) {
    uint32_t previous = g_action_mask;
    uint32_t mask = 0;
    char error[128];
    char actions[128];

    if (!parse_action_spec(spec, previous, &mask, error, sizeof(error))) {
        fprintf(stderr, "hold: %s\n", error);
        return false;
    }
    if (!command_input(&mask)) {
        return false;
    }
    format_actions(mask, actions, sizeof(actions));
    Sleep((DWORD)duration_ms);
    if (!g_json) {
        printf("held %s for %d ms\n", actions, duration_ms);
    }
    return command_input(&previous);
}

static bool parse_long(const char *text, long minimum, long maximum, long *out) {
    char *end = NULL;
    long value;

    errno = 0;
    value = strtol(text, &end, 0);
    if (end == text || end == NULL || *end != '\0' || errno != 0) {
        return false;
    }
    if (value < minimum || value > maximum) {
        return false;
    }
    *out = value;
    return true;
}

static BOOL WINAPI console_control_handler(DWORD type) {
    if (type == CTRL_C_EVENT || type == CTRL_BREAK_EVENT) {
        InterlockedExchange(&g_interrupted, 1);
        return TRUE;
    }
    return FALSE;
}

int main(int argc, char **argv) {
    int index = 1;
    const char *command;
    int status;

    while (index < argc && argv[index][0] == '-') {
        if (strcmp(argv[index], "-j") == 0 || strcmp(argv[index], "--json") == 0) {
            g_json = true;
        } else if (strcmp(argv[index], "-v") == 0 || strcmp(argv[index], "--verbose") == 0) {
            g_verbose = true;
        } else if (strcmp(argv[index], "-h") == 0 || strcmp(argv[index], "--help") == 0) {
            print_usage();
            return 0;
        } else {
            fprintf(stderr, "unknown option '%s'\n\n", argv[index]);
            print_usage();
            return 2;
        }
        ++index;
    }

    if (index >= argc) {
        print_usage();
        return 2;
    }

    command = argv[index++];
    if (strcmp(command, "help") == 0) {
        print_usage();
        return 0;
    }
    if (strcmp(command, "snapshot") == 0) {
        status = command_snapshot() ? 0 : 1;
    } else if (strcmp(command, "focus") == 0) {
        status = command_focus() ? 0 : 1;
    } else if (strcmp(command, "info") == 0) {
        status = command_info() ? 0 : 1;
    } else if (strcmp(command, "scene") == 0) {
        status = command_scene() ? 0 : 1;
    } else if (strcmp(command, "state") == 0) {
        status = command_state() ? 0 : 1;
    } else if (strcmp(command, "ui") == 0) {
        status = command_ui(index < argc ? argv[index] : NULL) ? 0 : 1;
    } else if (strcmp(command, "frames") == 0) {
        status = command_frames() ? 0 : 1;
    } else if (strcmp(command, "shot") == 0) {
        status = command_capture(index < argc ? argv[index] : NULL) ? 0 : 1;
    } else if (strcmp(command, "windows") == 0) {
        status = command_windows() ? 0 : 1;
    } else if (strcmp(command, "launch") == 0) {
        long timeout_ms = 30000;

        if (index >= argc) {
            fprintf(stderr, "launch: expected the path to th10.exe\n");
            return 2;
        }
        if (index + 1 < argc && !parse_long(argv[index + 1], 1, 600000, &timeout_ms)) {
            fprintf(stderr, "launch: bad timeout '%s'\n", argv[index + 1]);
            return 2;
        }
        status = command_launch(argv[index], (int)timeout_ms) ? 0 : 1;
    } else if (strcmp(command, "watch") == 0) {
        long interval_ms = 100;
        long count = 10; /* a run that never ends is a trap for callers */

        if (index < argc && !parse_long(argv[index], 1, 60000, &interval_ms)) {
            fprintf(stderr, "watch: bad interval '%s'\n", argv[index]);
            return 2;
        }
        if (index + 1 < argc && !parse_long(argv[index + 1], 0, 1000000, &count)) {
            fprintf(stderr, "watch: bad count '%s'\n", argv[index + 1]);
            return 2;
        }
        (void)SetConsoleCtrlHandler(console_control_handler, TRUE);
        status = command_watch((int)interval_ms, count) ? 0 : 1;
    } else if (strcmp(command, "hold") == 0) {
        long duration_ms = 0;

        if (index >= argc) {
            fputs("hold: expected an action spec, e.g. 'hold \"shoot focus\" 500'\n", stderr);
            return 2;
        }
        if (index + 1 >= argc || !parse_long(argv[index + 1], 1, 600000, &duration_ms)) {
            fputs("hold: expected a duration in milliseconds, e.g. 'hold left 300'\n", stderr);
            return 2;
        }
        status = command_hold(argv[index], (int)duration_ms) ? 0 : 1;
    } else if (strcmp(command, "input") == 0) {
        fprintf(stderr,
                "input: a one-shot process cannot hold an action down because th10_close()\n"
                "       releases every key on exit; use 'hold <spec> <ms>' instead, which\n"
                "       presses, waits, and releases inside the same process\n");
        return 2;
    } else {
        fprintf(stderr, "unknown command '%s'\n\n", command);
        print_usage();
        return 2;
    }

    if (!detach_session()) {
        return 1;
    }
    return status;
}
