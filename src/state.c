#include "internal.h"

/* The three addresses in internal.h answer everything here - see their comments
 * for what each one means and how it was verified. All this file adds is the
 * vocabulary of values they take, plus the one rule that needs two samples. */

enum {
    SCENE_MENU = 0x4u,
    SCENE_STAGE = 0x7u,
    LIVES_GAME_OVER = 0xFFFFFFFFu,
    /* Long enough for a running stage to have advanced by several frames, short
     * enough that both reads still describe the same moment. */
    STATE_SAMPLE_INTERVAL_MS = 120,
};

/* The screen family, as one read and nothing more. It answers "menu or stage" and
 * refuses to answer anything finer: every finer distinction a caller could want
 * comes out of the snapshot, the frame counter, or th10_read_state() - which
 * opens with this same word and then spends its 120 ms on what is left. */
th10_scene th10_read_scene(th10_session *session) {
    uint32_t scene = 0;

    if (session == NULL) {
        return TH10_SCENE_UNKNOWN;
    }
    if (!th10_read_memory(session, TH10_SCENE_ADDRESS, &scene, sizeof(scene), NULL)) {
        return TH10_SCENE_UNKNOWN;
    }
    if (scene == SCENE_MENU) {
        return TH10_SCENE_MENU;
    }
    if (scene == SCENE_STAGE) {
        return TH10_SCENE_STAGE;
    }
    return TH10_SCENE_UNKNOWN;
}

th10_state th10_read_state(th10_session *session) {
    th10_scene scene;
    uint32_t lives;
    uint32_t frames_earlier;
    uint32_t frames_later;

    if (session == NULL) {
        return TH10_STATE_UNKNOWN;
    }

    scene = th10_read_scene(session);
    if (scene == TH10_SCENE_MENU) {
        return TH10_STATE_MENU;
    }
    if (scene != TH10_SCENE_STAGE) {
        return TH10_STATE_UNKNOWN;
    }

    /* Inside the stage family: the lives counter alone says whether the run is
     * still going, so this needs no second sample. */
    if (!th10_read_memory(session, TH10_LIVES_ADDRESS, &lives, sizeof(lives), NULL)) {
        return TH10_STATE_UNKNOWN;
    }
    if (lives == LIVES_GAME_OVER) {
        return TH10_STATE_GAME_OVER;
    }

    /* Playing and paused share the screen family and the pause menu sets no flag
     * a single sample could read, so the frame counter is the only tell. */
    if (!th10_read_memory(session, TH10_STAGE_FRAMES_ADDRESS, &frames_earlier, sizeof(frames_earlier),
                          NULL)) {
        return TH10_STATE_UNKNOWN;
    }
    Sleep(STATE_SAMPLE_INTERVAL_MS);
    if (!th10_read_memory(session, TH10_STAGE_FRAMES_ADDRESS, &frames_later, sizeof(frames_later),
                          NULL)) {
        return TH10_STATE_UNKNOWN;
    }
    return frames_earlier != frames_later ? TH10_STATE_PLAYING : TH10_STATE_PAUSED;
}

/* The word th10_read_state() samples twice to separate playing from paused,
 * exposed on its own so that a caller can wait for the game to advance without
 * paying that built-in 120 ms. */
th10_frames_result th10_read_stage_frames(th10_session *session) {
    th10_read_failure failure;
    uint32_t frames = 0;

    if (session == NULL) {
        return (th10_frames_result){.tag = TH10_FRAMES_INVALID_SESSION};
    }
    if (!th10_read_memory(session, TH10_STAGE_FRAMES_ADDRESS, &frames, sizeof(frames), &failure)) {
        return (th10_frames_result){
            .tag = TH10_FRAMES_READ_FAILED,
            .value.read_failed = failure,
        };
    }
    return (th10_frames_result){
        .tag = TH10_FRAMES_SUCCESS,
        .value.frames = frames,
    };
}

/* Fields of the object the game hangs off TH10_UI_OBJECT_ADDRESS. See the note
 * next to that address for how each one was measured. */
enum {
    UI_SCREEN_OFFSET = 0x04u,
    /* Each cursor is kept twice, a word apart: the game writes the pair in step
     * and which of the two it reads back is not known, so both are written. */
    UI_MENU_CURSOR_OFFSET = 0x24u,
    UI_MENU_CURSOR_TWIN_OFFSET = 0x28u,
    UI_NAME_CURSOR_OFFSET = 0xFCu,
    UI_NAME_CURSOR_TWIN_OFFSET = 0x100u,
    UI_SCREEN_STAGE = 6,
    UI_SCREEN_MENU = 8,
    UI_SCREEN_NAME_ENTRY = 12,
    /* How many entries each screen's cursor runs over. The grid's count is 91
     * rather than 7 rows of 13 because its last row is full: cell 90 is `終`. */
    UI_MENU_ENTRY_COUNT = 3,
    UI_NAME_ENTRY_CELL_COUNT = 91,
};

static th10_ui_screen ui_screen_from_id(uint32_t id) {
    switch (id) {
    case UI_SCREEN_STAGE:
        return TH10_UI_SCREEN_STAGE;
    case UI_SCREEN_MENU:
        return TH10_UI_SCREEN_MENU;
    case UI_SCREEN_NAME_ENTRY:
        return TH10_UI_SCREEN_NAME_ENTRY;
    default:
        return TH10_UI_SCREEN_UNKNOWN;
    }
}

th10_ui_result th10_read_ui(th10_session *session) {
    th10_read_failure failure;
    th10_ui_result result;
    uint32_t object = 0;
    uint32_t screen_id = 0;
    uint32_t cursor = 0;
    uintptr_t cursor_offset;

    if (session == NULL) {
        return (th10_ui_result){.tag = TH10_UI_INVALID_SESSION};
    }

    /* The object pointer moves as the game changes screen, so it is read on
     * every call rather than kept. A null pointer is not an error: it is the
     * game before it built its first screen, which is the same answer as a
     * screen this file has no id for. */
    if (!th10_read_memory(session, TH10_UI_OBJECT_ADDRESS, &object, sizeof(object), &failure)) {
        return (th10_ui_result){.tag = TH10_UI_READ_FAILED, .value.read_failed = failure};
    }
    result = (th10_ui_result){
        .tag = TH10_UI_SUCCESS,
        .value.ui = {.screen = TH10_UI_SCREEN_UNKNOWN, .cursor = 0},
    };
    if (object == 0) {
        return result;
    }
    if (!th10_read_memory(session, (uintptr_t)object + UI_SCREEN_OFFSET, &screen_id,
                          sizeof(screen_id), &failure)) {
        return (th10_ui_result){.tag = TH10_UI_READ_FAILED, .value.read_failed = failure};
    }
    result.value.ui.screen = ui_screen_from_id(screen_id);

    /* Only the two screens that keep a cursor have one; a stage answers 0, which
     * is what its own fields happen to hold and not an entry. */
    switch (result.value.ui.screen) {
    case TH10_UI_SCREEN_MENU:
        cursor_offset = UI_MENU_CURSOR_OFFSET;
        break;
    case TH10_UI_SCREEN_NAME_ENTRY:
        cursor_offset = UI_NAME_CURSOR_OFFSET;
        break;
    default:
        return result;
    }
    if (!th10_read_memory(session, (uintptr_t)object + cursor_offset, &cursor, sizeof(cursor),
                          &failure)) {
        return (th10_ui_result){.tag = TH10_UI_READ_FAILED, .value.read_failed = failure};
    }
    result.value.ui.cursor = (int32_t)cursor;
    return result;
}

th10_write_result th10_write_ui_cursor(th10_session *session, int32_t entry) {
    th10_read_failure read_failure;
    th10_write_failure write_failure;
    th10_write_result result;
    uint32_t object = 0;
    uint32_t screen_id = 0;
    uintptr_t cursor_address;
    uintptr_t twin_address;
    int32_t count;
    int32_t reported = 0;
    int32_t value = entry;

    if (session == NULL) {
        return (th10_write_result){.tag = TH10_WRITE_INVALID_SESSION};
    }
    if (!th10_read_memory(session, TH10_UI_OBJECT_ADDRESS, &object, sizeof(object), &read_failure)) {
        return (th10_write_result){.tag = TH10_WRITE_READ_FAILED,
                                   .value.read_failed = read_failure};
    }
    if (object == 0) {
        /* No screen object yet, which is the same answer as a screen with no
         * cursor: there is nothing here to move. */
        return (th10_write_result){.tag = TH10_WRITE_UNSUPPORTED_SCREEN};
    }
    if (!th10_read_memory(session, (uintptr_t)object + UI_SCREEN_OFFSET, &screen_id,
                          sizeof(screen_id), &read_failure)) {
        return (th10_write_result){.tag = TH10_WRITE_READ_FAILED,
                                   .value.read_failed = read_failure};
    }

    switch (ui_screen_from_id(screen_id)) {
    case TH10_UI_SCREEN_MENU:
        cursor_address = (uintptr_t)object + UI_MENU_CURSOR_OFFSET;
        twin_address = (uintptr_t)object + UI_MENU_CURSOR_TWIN_OFFSET;
        count = UI_MENU_ENTRY_COUNT;
        break;
    case TH10_UI_SCREEN_NAME_ENTRY:
        cursor_address = (uintptr_t)object + UI_NAME_CURSOR_OFFSET;
        twin_address = (uintptr_t)object + UI_NAME_CURSOR_TWIN_OFFSET;
        count = UI_NAME_ENTRY_CELL_COUNT;
        break;
    default:
        return (th10_write_result){.tag = TH10_WRITE_UNSUPPORTED_SCREEN};
    }

    if (entry < 0 || entry >= count) {
        return (th10_write_result){
            .tag = TH10_WRITE_INVALID_ARGUMENT,
            .value.invalid_argument = {.entry = entry, .count = count},
        };
    }

    if (!th10_write_memory(session, cursor_address, &value, sizeof(value), &write_failure) ||
        !th10_write_memory(session, twin_address, &value, sizeof(value), &write_failure)) {
        return (th10_write_result){.tag = TH10_WRITE_FAILED,
                                   .value.write_failed = write_failure};
    }
    /* The answer is the game's own word rather than the request: a screen that
     * puts its cursor back where it was has not moved, and a caller that reads
     * this one back finds that out here rather than at its next press. */
    if (!th10_read_memory(session, cursor_address, &reported, sizeof(reported), &read_failure)) {
        return (th10_write_result){.tag = TH10_WRITE_READ_FAILED,
                                   .value.read_failed = read_failure};
    }
    result = (th10_write_result){.tag = TH10_WRITE_SUCCESS};
    result.value.cursor = reported;
    return result;
}
