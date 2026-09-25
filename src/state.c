#include "internal.h"

/* The three addresses in internal.h answer everything here - see their comments
 * for what each one means and how it was verified. All this file adds is the
 * vocabulary of values they take, plus the one rule that needs two samples. */

enum {
    SCENE_MENU = 0x4u,
    SCENE_STAGE = 0x7u,
    LIVES_GAME_OVER = 0xFFFFFFFFu,
};

/* The screen family, as one read and nothing more. It answers "menu or stage" and
 * refuses to answer anything finer: every finer distinction a caller could want
 * comes out of the snapshot, the screen the game is driving, or the frame
 * counter - which th10_read_state() opens with this same word and then asks. */
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
    th10_screen_result screen;
    uint32_t lives;

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
     * still going, so this needs no second sample either. */
    if (!th10_read_memory(session, TH10_LIVES_ADDRESS, &lives, sizeof(lives), NULL)) {
        return TH10_STATE_UNKNOWN;
    }
    if (lives == LIVES_GAME_OVER) {
        return TH10_STATE_GAME_OVER;
    }

    /* Playing and paused share the screen family, and the pause menu is what
     * tells them apart: the game records it as a page of its own, so one read of
     * the screen answers what two samples of the frame counter could only guess
     * at - and a stage whose screen the game has not built yet reads PLAYING
     * here, where a standing clock would have called it paused. */
    screen = th10_read_screen(session);
    if (screen.tag != TH10_SCREEN_SUCCESS) {
        return TH10_STATE_UNKNOWN;
    }
    if (screen.value.state.kind == TH10_SCREEN_KIND_PAUSE_MENU ||
        screen.value.state.kind == TH10_SCREEN_KIND_PAUSE_CONFIRM) {
        return TH10_STATE_PAUSED;
    }
    return TH10_STATE_PLAYING;
}

/* The stage's own clock, exposed on its own so that a caller can wait for the
 * game to advance a frame. It is not how a pause is read - the pause menu is a
 * page of its own and th10_read_state() asks that one - but it is what says the
 * clock has stopped, which is also what a stage that is still loading looks
 * like. */
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

/* Fields of the object the game hangs off TH10_SCREEN_OBJECT_ADDRESS. See the
 * note next to that address for how each one was measured. */
enum {
    SCREEN_KIND_OFFSET = 0x04u,
    /* Each cursor is kept twice, a word apart: the game writes the pair in step
     * and which of the two it reads back is not known, so both are written. All
     * three menus keep theirs here - the ending's, the pause menu and its
     * confirmation - and it is the id that says which one a 0 means. */
    SCREEN_MENU_CURSOR_OFFSET = 0x24u,
    SCREEN_MENU_CURSOR_TWIN_OFFSET = 0x28u,
    SCREEN_NAME_CURSOR_OFFSET = 0xFCu,
    SCREEN_NAME_CURSOR_TWIN_OFFSET = 0x100u,
    /* The stage's own screen, which is the one a run that is over wears - and
     * only that one. A stage that is still playing reports 0 in the same field
     * (measured six reads in a row while a fresh run's clock advanced), and 0
     * is left unmapped on purpose: "is there a run, and is it over" is what the
     * snapshot answers, and nothing else in this tree needs the playing stage's
     * screen id. */
    SCREEN_ID_STAGE = 6,
    SCREEN_ID_MENU = 8,
    SCREEN_ID_NAME_ENTRY = 12,
    /* A paused stage drives menus of its own, and each keeps an id of its own:
     * measured while a run sat paused, where +0x04 read 2 rather than the 8 an
     * ending's menu reads, and pressing down, up, up, down walked +0x24 through
     * 0 -> 1 -> 0 -> 2 -> 0. So the pause menu's cursor is the menu's own field,
     * its list wraps at both ends, and it holds the same three entries.
     *
     * The confirmation `Retry This Game` opens is the second one: the same
     * object read 4 with +0x24 at 1, the `No` its cursor opens on, and the run
     * stayed frozen behind it. It is a page of its own rather than part of the
     * pause menu because a cursor means something else on it - 0 is `Yes` there,
     * where 0 on the pause menu is `Return to Game`. */
    SCREEN_ID_PAUSE_MENU = 2,
    SCREEN_ID_PAUSE_CONFIRM = 4,
    /* How many entries each screen's cursor runs over. The grid's count is 91
     * rather than 7 rows of 13 because its last row is full: cell 90 is `終`. */
    SCREEN_MENU_ENTRY_COUNT = 3,
    SCREEN_NAME_ENTRY_CELL_COUNT = 91,
};

static th10_screen_kind screen_kind_from_id(uint32_t id) {
    switch (id) {
    case SCREEN_ID_STAGE:
        return TH10_SCREEN_KIND_STAGE;
    case SCREEN_ID_MENU:
        return TH10_SCREEN_KIND_MENU;
    case SCREEN_ID_PAUSE_MENU:
        /* The two screens a paused run drives are kinds of their own, apart from
         * the ending's menu on purpose: a caller that presses a key on a menu
         * reads the cursor, and 0 is `Continue` on one, `Return to Game` on
         * another and `Yes` on the third. */
        return TH10_SCREEN_KIND_PAUSE_MENU;
    case SCREEN_ID_PAUSE_CONFIRM:
        return TH10_SCREEN_KIND_PAUSE_CONFIRM;
    case SCREEN_ID_NAME_ENTRY:
        return TH10_SCREEN_KIND_NAME_ENTRY;
    default:
        return TH10_SCREEN_KIND_UNKNOWN;
    }
}

th10_screen_result th10_read_screen(th10_session *session) {
    th10_read_failure failure;
    th10_screen_result result;
    uint32_t object = 0;
    uint32_t screen_id = 0;
    uint32_t cursor = 0;
    uintptr_t cursor_offset;

    if (session == NULL) {
        return (th10_screen_result){.tag = TH10_SCREEN_INVALID_SESSION};
    }

    /* The object pointer moves as the game changes screen, so it is read on
     * every call rather than kept. A null pointer is not an error: it is the
     * game before it built its first screen, which is the same answer as a
     * screen this file has no id for. */
    if (!th10_read_memory(session, TH10_SCREEN_OBJECT_ADDRESS, &object, sizeof(object), &failure)) {
        return (th10_screen_result){.tag = TH10_SCREEN_READ_FAILED, .value.read_failed = failure};
    }
    result = (th10_screen_result){
        .tag = TH10_SCREEN_SUCCESS,
        .value.state = {.kind = TH10_SCREEN_KIND_UNKNOWN, .cursor = 0},
    };
    if (object == 0) {
        return result;
    }
    if (!th10_read_memory(session, (uintptr_t)object + SCREEN_KIND_OFFSET, &screen_id,
                          sizeof(screen_id), &failure)) {
        return (th10_screen_result){.tag = TH10_SCREEN_READ_FAILED, .value.read_failed = failure};
    }
    result.value.state.kind = screen_kind_from_id(screen_id);

    /* Only the screens that keep a cursor have one; a stage answers 0, which is
     * what its own fields happen to hold and not an entry. */
    switch (result.value.state.kind) {
    case TH10_SCREEN_KIND_MENU:
    case TH10_SCREEN_KIND_PAUSE_MENU:
    case TH10_SCREEN_KIND_PAUSE_CONFIRM:
        cursor_offset = SCREEN_MENU_CURSOR_OFFSET;
        break;
    case TH10_SCREEN_KIND_NAME_ENTRY:
        cursor_offset = SCREEN_NAME_CURSOR_OFFSET;
        break;
    default:
        return result;
    }
    if (!th10_read_memory(session, (uintptr_t)object + cursor_offset, &cursor, sizeof(cursor),
                          &failure)) {
        return (th10_screen_result){.tag = TH10_SCREEN_READ_FAILED, .value.read_failed = failure};
    }
    result.value.state.cursor = (int32_t)cursor;
    return result;
}

th10_write_result th10_write_screen_cursor(th10_session *session, int32_t entry) {
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
    if (!th10_read_memory(session, TH10_SCREEN_OBJECT_ADDRESS, &object, sizeof(object), &read_failure)) {
        return (th10_write_result){.tag = TH10_WRITE_READ_FAILED,
                                   .value.read_failed = read_failure};
    }
    if (object == 0) {
        /* No screen object yet, which is the same answer as a screen with no
         * cursor: there is nothing here to move. */
        return (th10_write_result){.tag = TH10_WRITE_UNSUPPORTED_SCREEN};
    }
    if (!th10_read_memory(session, (uintptr_t)object + SCREEN_KIND_OFFSET, &screen_id,
                          sizeof(screen_id), &read_failure)) {
        return (th10_write_result){.tag = TH10_WRITE_READ_FAILED,
                                   .value.read_failed = read_failure};
    }

    switch (screen_kind_from_id(screen_id)) {
    case TH10_SCREEN_KIND_MENU:
    case TH10_SCREEN_KIND_PAUSE_MENU:
        /* The ending's menu and the pause menu are one list each - three entries
         * a cursor wraps around - which is why they share this arm. The
         * confirmation is not here: it keeps a cursor and nothing in this tree
         * needs it moved, and a write nothing reads is a write nothing has
         * verified. */
        cursor_address = (uintptr_t)object + SCREEN_MENU_CURSOR_OFFSET;
        twin_address = (uintptr_t)object + SCREEN_MENU_CURSOR_TWIN_OFFSET;
        count = SCREEN_MENU_ENTRY_COUNT;
        break;
    case TH10_SCREEN_KIND_NAME_ENTRY:
        cursor_address = (uintptr_t)object + SCREEN_NAME_CURSOR_OFFSET;
        twin_address = (uintptr_t)object + SCREEN_NAME_CURSOR_TWIN_OFFSET;
        count = SCREEN_NAME_ENTRY_CELL_COUNT;
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
