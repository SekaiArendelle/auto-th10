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
    /* Raised by the game the moment the score passes the high score, so it
     * says the run that just ended is owed a name entry. */
    FLAG_RECORD_BROKEN = 0x4u,
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

th10_record_result th10_read_record_broken(th10_session *session) {
    th10_read_failure failure;
    uint32_t flags = 0;

    if (session == NULL) {
        return (th10_record_result){.tag = TH10_RECORD_INVALID_SESSION};
    }
    if (!th10_read_memory(session, TH10_FLAGS_ADDRESS, &flags, sizeof(flags), &failure)) {
        return (th10_record_result){
            .tag = TH10_RECORD_READ_FAILED,
            .value.read_failed = failure,
        };
    }
    return (th10_record_result){
        .tag = TH10_RECORD_SUCCESS,
        .value.broken = (flags & FLAG_RECORD_BROKEN) != 0,
    };
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
