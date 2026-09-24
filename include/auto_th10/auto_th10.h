/**
 * @file auto_th10.h
 * @brief The public C API: attach to a running Touhou 10 process, read what the
 *        game keeps in its own memory, inject keyboard input, and capture its
 *        window.
 *
 * Every fallible call returns a tagged result instead of setting a global error
 * state or aborting, so a caller can always tell what went wrong. The Python
 * binding and th10ctl are both built on this header alone.
 */

#ifndef AUTO_TH10_AUTO_TH10_H
#define AUTO_TH10_AUTO_TH10_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief An attached game process.
 *
 * Opened by th10_open() and released by th10_close(). The struct itself is
 * opaque: its fields are an implementation detail of the C core.
 */
typedef struct th10_session th10_session;

/**
 * @brief The keys that can be held down, as a bit mask.
 *
 * A mask rather than one value per combination because the game reads the keys
 * simultaneously: a typical frame holds a direction together with the shoot and
 * focus keys.
 */
typedef enum th10_action {
    TH10_ACTION_NONE = 0, /**< no key held */
    TH10_ACTION_LEFT = 1u << 0, /**< the left arrow key */
    TH10_ACTION_RIGHT = 1u << 1, /**< the right arrow key */
    TH10_ACTION_UP = 1u << 2, /**< the up arrow key */
    TH10_ACTION_DOWN = 1u << 3, /**< the down arrow key */
    TH10_ACTION_SHOOT = 1u << 4, /**< the shoot key ('Z') */
    TH10_ACTION_FOCUS = 1u << 5, /**< the focus key (left Shift) */
    TH10_ACTION_BOMB = 1u << 6, /**< the bomb key ('X') */
    TH10_ACTION_ESCAPE = 1u << 7 /**< the escape key, which pauses a stage and
                                  * backs out of a menu the game is waiting on */
} th10_action;

/** @brief A position, in the game's own coordinates. */
typedef struct th10_point {
    float x;
    float y;
} th10_point;

/** @brief An axis-aligned box, in the game's own coordinates. */
typedef struct th10_rect {
    float x;
    float y;
    float width;
    float height;
} th10_rect;

/** @brief An enemy bullet's box, plus the velocity the game has given it. */
typedef struct th10_enemy_bullet {
    float x;
    float y;
    float width;
    float height;
    float dx;
    float dy;
} th10_enemy_bullet;

/** @brief An enemy laser: a rotated box, plus its angle in radians. */
typedef struct th10_enemy_laser {
    float x;
    float y;
    float width;
    float height;
    float radian;
} th10_enemy_laser;

/**
 * @brief An owned array of th10_point.
 *
 * @c data holds @c size elements and has room for @c capacity; the storage is
 * managed by th10_snapshot_init(), th10_snapshot_clear() and
 * th10_snapshot_destroy().
 */
typedef struct th10_point_array {
    th10_point *data;
    size_t size;
    size_t capacity;
} th10_point_array;

/** @brief An owned array of th10_rect; see th10_point_array for the layout. */
typedef struct th10_rect_array {
    th10_rect *data;
    size_t size;
    size_t capacity;
} th10_rect_array;

/** @brief An owned array of th10_enemy_bullet; see th10_point_array for the layout. */
typedef struct th10_enemy_bullet_array {
    th10_enemy_bullet *data;
    size_t size;
    size_t capacity;
} th10_enemy_bullet_array;

/** @brief An owned array of th10_enemy_laser; see th10_point_array for the layout. */
typedef struct th10_enemy_laser_array {
    th10_enemy_laser *data;
    size_t size;
    size_t capacity;
} th10_enemy_laser_array;

/**
 * @brief One read of the game's state.
 *
 * The four arrays are owned by the snapshot: th10_read_snapshot() grows them as
 * needed, th10_snapshot_clear() keeps their storage for reuse, and
 * th10_snapshot_destroy() releases it.
 */
typedef struct th10_snapshot {
    th10_point player; /**< the player's own position */
    uint32_t score; /**< the score the game is showing */
    uint16_t power; /**< the power meter */
    /** Remaining lives, not hit points: 2, 1, 0 while the run is alive and -1
     * once it is over. game_over repeats that -1 so callers need not know it. */
    int16_t lives;
    uint8_t game_over; /**< set when the run is over, the same news as lives == -1 */
    th10_rect_array enemies; /**< every enemy the game is tracking */
    th10_enemy_bullet_array enemy_bullets; /**< every enemy bullet */
    th10_enemy_laser_array enemy_lasers; /**< every enemy laser */
    th10_point_array resources; /**< every resource the game is tracking */
} th10_snapshot;

/** @brief Why th10_open() returned what it did. */
typedef enum th10_open_result_tag {
    TH10_OPEN_SUCCESS = 0, /**< the session was created; see value.session */
    TH10_OPEN_WINDOW_NOT_FOUND, /**< no visible window belongs to the game */
    TH10_OPEN_ENUM_WINDOWS_FAILED, /**< EnumWindows failed; see value.win32_error.code */
    TH10_OPEN_GET_PROCESS_ID_FAILED, /**< the window has no owning process; see value.win32_error.code */
    TH10_OPEN_PROCESS_FAILED, /**< the process could not be opened; see value.win32_error.code */
    TH10_OPEN_OUT_OF_MEMORY /**< the session could not be allocated */
} th10_open_result_tag;

/**
 * @brief The outcome of th10_open().
 *
 * @c value carries the member @c tag names, and is only meaningful for that tag.
 */
typedef struct th10_open_result {
    th10_open_result_tag tag;
    union {
        th10_session *session; /**< valid when tag is TH10_OPEN_SUCCESS */
        struct {
            uint32_t code; /**< GetLastError() of the failing call */
        } win32_error;
    } value;
} th10_open_result;

/** @brief Why th10_focus() returned what it did. */
typedef enum th10_focus_result_tag {
    TH10_FOCUS_SUCCESS = 0, /**< the game window owns the keyboard focus */
    TH10_FOCUS_INVALID_SESSION, /**< the session is NULL or already closed */
    TH10_FOCUS_PERMISSION_AND_SET_REJECTED, /**< the permission request and the request itself were both refused */
    TH10_FOCUS_REJECTED /**< the request was accepted but the window never took the focus */
} th10_focus_result_tag;

/**
 * @brief The outcome of th10_focus().
 *
 * @c value carries the member @c tag names, and is only meaningful for that tag.
 */
typedef struct th10_focus_result {
    th10_focus_result_tag tag;
    union {
        struct {
            uint32_t win32_error; /**< GetLastError() of AllowSetForegroundWindow() */
        } permission_and_set_rejected;
    } value;
} th10_focus_result;

/** @brief Why th10_set_input() returned what it did. */
typedef enum th10_input_result_tag {
    TH10_INPUT_SUCCESS = 0, /**< the requested keys are now the ones held */
    TH10_INPUT_INVALID_SESSION, /**< the session is NULL or already closed */
    TH10_INPUT_UNSUPPORTED_ACTION, /**< the mask had bits outside th10_action; see value.unsupported_action */
    TH10_INPUT_SEND_FAILED /**< SendInput inserted fewer events than asked; see value.send_failed */
} th10_input_result_tag;

/**
 * @brief The outcome of th10_set_input().
 *
 * @c value carries the member @c tag names, and is only meaningful for that tag.
 */
typedef struct th10_input_result {
    th10_input_result_tag tag;
    union {
        struct {
            uint32_t unsupported_bits; /**< the bits of the mask that name no action */
        } unsupported_action;
        struct {
            uint32_t requested_count; /**< how many events were built */
            uint32_t inserted_count; /**< how many SendInput() reported as inserted */
            uint32_t win32_error; /**< GetLastError(); may be zero when UIPI blocked the input */
        } send_failed;
    } value;
} th10_input_result;

/** @brief Why th10_close() returned what it did. */
typedef enum th10_close_result_tag {
    TH10_CLOSE_SUCCESS = 0, /**< the keys were released, the handle closed and the session freed */
    TH10_CLOSE_INVALID_SESSION, /**< the session is NULL, so there was nothing to release */
    TH10_CLOSE_INPUT_RELEASE_FAILED, /**< releasing the held keys failed; see value.input_release_failed */
    TH10_CLOSE_HANDLE_FAILED, /**< the process handle could not be closed; see value.handle_failed */
    TH10_CLOSE_INPUT_AND_HANDLE_FAILED /**< both failed; see value.input_and_handle_failed */
} th10_close_result_tag;

/**
 * @brief The outcome of th10_close().
 *
 * @c value carries the member @c tag names, and is only meaningful for that tag.
 */
typedef struct th10_close_result {
    th10_close_result_tag tag;
    union {
        th10_input_result input_release_failed; /**< the failure of the key release */
        struct {
            uint32_t win32_error; /**< GetLastError() of CloseHandle() */
        } handle_failed;
        struct {
            th10_input_result input; /**< the failure of the key release */
            uint32_t win32_error; /**< GetLastError() of CloseHandle() */
        } input_and_handle_failed;
    } value;
} th10_close_result;

/** @brief Which of the four arrays a snapshot operation was working on. */
typedef enum th10_snapshot_array {
    TH10_SNAPSHOT_ENEMIES = 0, /**< th10_snapshot::enemies */
    TH10_SNAPSHOT_ENEMY_BULLETS, /**< th10_snapshot::enemy_bullets */
    TH10_SNAPSHOT_ENEMY_LASERS, /**< th10_snapshot::enemy_lasers */
    TH10_SNAPSHOT_RESOURCES /**< th10_snapshot::resources */
} th10_snapshot_array;

/** @brief Why th10_read_snapshot() returned what it did. */
typedef enum th10_snapshot_result_tag {
    TH10_SNAPSHOT_SUCCESS = 0, /**< the snapshot was filled in */
    TH10_SNAPSHOT_INVALID_ARGUMENT, /**< the session or the snapshot was NULL */
    TH10_SNAPSHOT_NOT_IN_GAME, /**< no stage is loaded, so there is nothing to read */
    TH10_SNAPSHOT_READ_FAILED, /**< a memory read failed; see value.read_failed */
    TH10_SNAPSHOT_ALLOCATION_FAILED /**< an array could not grow; see value.allocation_failed */
} th10_snapshot_result_tag;

/**
 * @brief Why a memory read failed.
 *
 * Shared by the snapshot result and the internal reader, so both describe a
 * failure the same way.
 */
typedef struct th10_read_failure {
    uintptr_t address; /**< the address the read started at */
    size_t requested_size; /**< how many bytes were asked for */
    size_t bytes_read; /**< how many bytes ReadProcessMemory() reported */
    uint32_t win32_error; /**< GetLastError() of the failed read; 0 when the read never started */
} th10_read_failure;

/**
 * @brief The address a write targeted and why it did not take.
 *
 * The write-side twin of th10_read_failure, and it carries the same fields so a
 * caller can decode both the same way.
 */
typedef struct th10_write_failure {
    uintptr_t address; /**< the address the write was aimed at */
    size_t requested_size; /**< how many bytes were to be written */
    size_t bytes_written; /**< how many bytes WriteProcessMemory() reported */
    uint32_t win32_error; /**< GetLastError() of the failed write; 0 when it never started */
} th10_write_failure;

/**
 * @brief The outcome of th10_read_snapshot().
 *
 * @c value carries the member @c tag names, and is only meaningful for that tag.
 */
typedef struct th10_snapshot_result {
    th10_snapshot_result_tag tag;
    union {
        th10_read_failure read_failed; /**< the read that failed */
        struct {
            th10_snapshot_array array; /**< which array was being grown */
            size_t requested_capacity; /**< the capacity that was asked for */
            size_t element_size; /**< the size of one element */
        } allocation_failed;
    } value;
} th10_snapshot_result;

/**
 * @brief Attaches to a running game.
 *
 * The game window is recognized by its title or by the executable name of the
 * owning process, so a localized or patched build is still found.
 *
 * @return A result with TH10_OPEN_SUCCESS and a new session in value.session, or
 *         the reason the attach failed. The caller owns the session and releases
 *         it with th10_close().
 */
th10_open_result th10_open(void);

/**
 * @brief Releases the held keys, closes the process handle and frees the session.
 *
 * The keys are released first, so a caller that exits while an action is held
 * cannot leave the game with a key stuck down. A NULL session is reported as
 * TH10_CLOSE_INVALID_SESSION instead of being silently ignored.
 *
 * @param session The session from th10_open(); it must not be used afterwards.
 * @return The outcome of each step that had something to do.
 */
th10_close_result th10_close(th10_session *session);

/**
 * @brief Hands the game window the keyboard focus, so injected keys reach it.
 *
 * SetForegroundWindow only queues the request, so the focus is polled for and
 * this thread is attached to the game's input queue as a fallback. The window's
 * input context is then detached and switched to the neutral Latin layout,
 * because an IME on the game's thread swallows the character keys ('Z' and 'X')
 * into a composition before the game can read them.
 *
 * @note Worst case this blocks for about 1.2 s: two 500 ms polls for the focus,
 *       plus the settle that follows. Even a window that already owns the focus
 *       waits out that settle.
 *
 * @param session The session from th10_open().
 * @return TH10_FOCUS_SUCCESS, or why the window did not end up with the focus.
 */
th10_focus_result th10_focus(th10_session *session);

/**
 * @brief Makes @p action_mask the exact set of keys that is held down.
 *
 * The mask is a new state, not a delta: keys that were held and are no longer in
 * the mask are released, the ones that appear are pressed, and the ones that stay
 * are left alone, so repeating a call injects nothing. The keys are injected by
 * scan code with the arrow keys marked as extended keys, because a DirectInput
 * game never reads the virtual key path.
 *
 * @param session The session from th10_open().
 * @param action_mask A combination of th10_action bits, or TH10_ACTION_NONE to
 *        release everything.
 * @return TH10_INPUT_SUCCESS, or which part of the mask was unsupported, or
 *         which events SendInput() refused.
 */
th10_input_result th10_set_input(th10_session *session, uint32_t action_mask);

/**
 * @brief Prepares a snapshot for its first read.
 *
 * @param snapshot The snapshot to initialize; it is zeroed, so no storage has to
 *        be released before this call.
 */
void th10_snapshot_init(th10_snapshot *snapshot);

/**
 * @brief Empties a snapshot for reuse, keeping the storage of its arrays.
 *
 * The arrays' capacity is not released, so a snapshot read in a loop stops
 * allocating once it has seen the game's largest frame.
 *
 * @param snapshot An initialized snapshot.
 */
void th10_snapshot_clear(th10_snapshot *snapshot);

/**
 * @brief Releases all array storage of a snapshot and zeroes it.
 *
 * @param snapshot An initialized snapshot; it can be used again only after
 *        th10_snapshot_init().
 */
void th10_snapshot_destroy(th10_snapshot *snapshot);

/**
 * @brief Reads the game's current state into @p out_snapshot.
 *
 * The part of the state that always exists (player, score, power, lives) is read
 * first, then the four arrays, which are grown on demand. Objects that are behind
 * the game's own active/hidden flags are not copied.
 *
 * @param session The session from th10_open().
 * @param out_snapshot An initialized snapshot; its previous contents are
 *        discarded, not freed.
 * @return TH10_SNAPSHOT_SUCCESS, or the first reason the read could not finish.
 */
th10_snapshot_result th10_read_snapshot(th10_session *session, th10_snapshot *out_snapshot);

/** @brief Why th10_capture() returned what it did. */
typedef enum th10_capture_result_tag {
    TH10_CAPTURE_SUCCESS = 0, /**< the file was written; width and height describe it */
    TH10_CAPTURE_INVALID_ARGUMENT, /**< the session or the path was NULL */
    TH10_CAPTURE_CLIENT_RECT_FAILED, /**< the client area could not be measured, or is empty */
    TH10_CAPTURE_CREATE_DC_FAILED, /**< a device context could not be created; see win32_error */
    TH10_CAPTURE_CREATE_BITMAP_FAILED, /**< the bitmap could not be created; see win32_error */
    TH10_CAPTURE_PRINT_WINDOW_FAILED, /**< the window did not draw itself; see win32_error */
    TH10_CAPTURE_FILE_OPEN_FAILED, /**< the file could not be opened; win32_error holds errno */
    TH10_CAPTURE_FILE_WRITE_FAILED /**< the file could not be written; win32_error holds errno */
} th10_capture_result_tag;

/**
 * @brief The outcome of th10_capture().
 *
 * @c width and @c height describe the captured client area and are set on
 * success; @c win32_error holds the Win32 error of the failing step, or errno
 * when the failure is a file one.
 */
typedef struct th10_capture_result {
    th10_capture_result_tag tag;
    uint32_t width;
    uint32_t height;
    uint32_t win32_error;
} th10_capture_result;

/**
 * @brief Writes the client area of the game window into a 32-bit BMP file.
 *
 * The window is asked to draw itself through PrintWindow, so this reads the
 * game's own content rather than the screen: it works while the game is in the
 * background or covered over, and needs neither the focus nor the foreground.
 * The image is uncompressed with a 32-bit pixel format, because that is what the
 * Win32 blit path produces and the core carries no image encoder.
 *
 * @param session The session from th10_open().
 * @param path The file to write; an existing file is overwritten.
 * @return TH10_CAPTURE_SUCCESS with width and height set, or the step that failed.
 */
th10_capture_result th10_capture(th10_session *session, const wchar_t *path);

/** @brief The family of screen the game is on, straight from its state word. */
typedef enum th10_scene {
    TH10_SCENE_UNKNOWN = 0, /**< the word could not be read, or holds no known value */
    TH10_SCENE_MENU, /**< the title screen and the menus around a run */
    TH10_SCENE_STAGE /**< a stage: playing, paused and over all look alike here */
} th10_scene;

/**
 * @brief Reports which family of screen the game is on.
 *
 * One word decides it (0x00491FB8): 0x4 is the title and the menus, 0x7 is a
 * stage, and anything else is a value this build does not know. It is a single
 * read with no waiting, which makes it the cheap way to ask "is the game in a
 * stage at all". th10_read_state() opens with exactly this question and then
 * spends its 120 ms on the part this cannot answer.
 *
 * What it deliberately does not say is anything finer, and a caller should not
 * ask it to: a menu is six screens under one value (title, RANK, PLAYER SELECT,
 * WEAPON SELECT, the replay list, the name entry), and a stage is playing, paused
 * and over alike. For those, read the run's own numbers with
 * th10_read_snapshot(), and sample th10_read_stage_frames() twice to tell playing
 * from paused - which is a question about two moments in time, so no single read
 * can answer it.
 *
 * @param session The session from th10_open().
 * @return The family, or TH10_SCENE_UNKNOWN when the session is NULL or the word
 *         could not be read.
 */
th10_scene th10_read_scene(th10_session *session);

/** @brief The screen the game is on, as read from the game's own state words. */
typedef enum th10_state {
    TH10_STATE_UNKNOWN = 0, /**< the state could not be determined */
    TH10_STATE_MENU, /**< the title and its menus, no stage in play */
    TH10_STATE_PLAYING, /**< a stage is running */
    TH10_STATE_PAUSED, /**< a stage is loaded but frozen by the pause menu */
    TH10_STATE_GAME_OVER /**< the run is over */
} th10_state;

/**
 * @brief Reports which of the five states the game is in.
 *
 * The state is read from three words of the game's static data rather than
 * inferred from bulk memory diffing:
 *
 *   0x00491FB8  screen family: 0x4 is the title and menus, 0x7 is a stage
 *   0x00474C70  remaining lives: 2, 1, 0 alive, then -1 once the run is over
 *   0x00474C88  stage frame counter: advances while playing, freezes while paused
 *
 * The screen family is what separates a menu from a stage, the lives counter
 * alone decides game over, and the frame counter is the only thing that can
 * tell playing from paused - both are family 0x7 and the pause menu sets no
 * flag that a single sample could read.
 *
 * The first of those three is exposed on its own as th10_read_scene(), which is
 * one read with nothing to wait for; a caller that only needs "menu or stage"
 * should ask that rather than pay for this one.
 *
 * @param session The session from th10_open().
 * @return One of the five states, or TH10_STATE_UNKNOWN when a read failed or
 *         the state word holds none of the known values.
 *
 * @note This blocks for about 120 ms when it has to sample the frame counter
 *       twice; the menu and game-over answers return immediately.
 */
th10_state th10_read_state(th10_session *session);

/** @brief Why th10_read_screen() returned what it did. */
typedef enum th10_screen_result_tag {
    TH10_SCREEN_SUCCESS = 0, /**< the object was read; see value.state */
    TH10_SCREEN_INVALID_SESSION, /**< the session is NULL or already closed */
    TH10_SCREEN_READ_FAILED /**< the screen's object could not be read; see value.read_failed */
} th10_screen_result_tag;

/** @brief Which screen the game is driving, as it records it itself.
 *
 * This is a different question from th10_read_scene(): a scene is the coarse
 * family a screen belongs to (a menu, a stage), while a kind names the page
 * itself. One family holds several kinds, and the kinds below are only the ones
 * this binding has measured.
 */
typedef enum th10_screen_kind {
    TH10_SCREEN_KIND_UNKNOWN = 0, /**< the game's id maps to none of the kinds below */
    TH10_SCREEN_KIND_STAGE, /**< a stage is the active object: playing and over alike */
    TH10_SCREEN_KIND_MENU, /**< a menu is up; measured on the ending's menu */
    TH10_SCREEN_KIND_NAME_ENTRY /**< the Score Ranking name entry is waiting for a name */
} th10_screen_kind;

/** @brief The screen the game is driving, with the cursor that screen keeps. */
typedef struct th10_screen_state {
    th10_screen_kind kind; /**< one of TH10_SCREEN_KIND_*, or UNKNOWN when the id maps
                            *   to no listed kind */
    int32_t cursor; /**< the highlighted entry: the menu's (0..2) on
                     *   TH10_SCREEN_KIND_MENU, the name entry's grid cell (0..90) on
                     *   TH10_SCREEN_KIND_NAME_ENTRY. A stage keeps neither, so this
                     *   is then whatever the screen last left behind and means
                     *   nothing. */
} th10_screen_state;

/** @brief The outcome of th10_read_screen(). */
typedef struct th10_screen_result {
    th10_screen_result_tag tag;
    union {
        th10_screen_state state; /**< the screen and its cursor; valid on success */
        th10_read_failure read_failed; /**< the read that failed */
    } value;
} th10_screen_result;

/**
 * @brief Reads which screen the game is driving, and that screen's cursor.
 *
 * The screen id and the cursor are fields of one object the game points at from
 * 0x00477830, and that object is what the menus and the name entry drive
 * themselves through - unlike the run's own numbers, this state lives in the
 * object rather than in static data, so the read is a pointer chase:
 *
 *   +0x04   screen id: 6 a stage, 8 a menu, 12 the Score Ranking name entry
 *   +0x24   the menu's highlighted entry, 0..2
 *   +0xFC   the name entry's highlighted grid cell, 0..90
 *
 * The three ids and both cursors were measured against th10.exe 1.00a by
 * pressing one arrow key at a time on each screen and diffing the game's
 * committed memory around the press; the object pointer itself moved as the
 * game changed screens, which is why the id has to be read before the cursors.
 *
 * This is the read that tells the two endings apart. A plain game over and the
 * name entry look the same through everything else the binding exposes - both
 * are screen family 0x7 with the run over - so a driver that has to leave an
 * ending has to ask this one first.
 *
 * @param session The session from th10_open().
 * @return TH10_SCREEN_SUCCESS with the screen and its cursor in value.state, or
 *         the reason the object could not be read.
 *
 * @note TH10_SCREEN_KIND_UNKNOWN is a normal answer, not a failure: it means the
 *       game is on a screen this binding has not measured. A caller must not
 *       treat it as "a stage".
 */
th10_screen_result th10_read_screen(th10_session *session);

/** @brief Why th10_write_screen_cursor() returned what it did. */
typedef enum th10_write_result_tag {
    TH10_WRITE_SUCCESS = 0, /**< the cursor was written and read back; see value.cursor */
    TH10_WRITE_INVALID_SESSION, /**< the session is NULL or already closed */
    TH10_WRITE_UNSUPPORTED_SCREEN, /**< the screen the game is driving keeps no cursor */
    TH10_WRITE_INVALID_ARGUMENT, /**< the entry is outside that screen's list */
    TH10_WRITE_READ_FAILED, /**< the screen could not be read; see value.read_failed */
    TH10_WRITE_FAILED /**< the cursor could not be written; see value.write_failed */
} th10_write_result_tag;

/** @brief The outcome of th10_write_screen_cursor(). */
typedef struct th10_write_result {
    th10_write_result_tag tag;
    union {
        int32_t cursor; /**< what the game reports after the write; valid on success */
        th10_read_failure read_failed; /**< the read that failed */
        th10_write_failure write_failed; /**< the write that failed */
        struct {
            int32_t entry; /**< the entry that was asked for */
            int32_t count; /**< how many entries that screen's cursor has */
        } invalid_argument; /**< an entry the screen does not have */
    } value;
} th10_write_result;

/**
 * @brief Moves the cursor of the screen the game is driving, without pressing a key.
 *
 * This is the write side of th10_read_screen(), and it exists for the same reason the
 * read does: a screen's cursor is state the game keeps, and every screen is left
 * by putting that cursor somewhere and confirming. Reaching a cell by pressing a
 * direction is a loop - one press, one read, repeat - where the same thing can be
 * asked for in one step, and the presses are the part that goes wrong when the
 * window loses the foreground.
 *
 * What it changes is the field the game itself moves when a key arrives, at the
 * same address the read reports it from, so nothing about the game's own state is
 * touched: no score, no run, no record. Both copies of the value are written when
 * a screen keeps two - the game maintains each pair in step - and the result is
 * read back, so `value.cursor` is the game's answer rather than the request.
 *
 * A screen that keeps no cursor refuses rather than guessing: a stage and a screen
 * this binding has not measured are both TH10_WRITE_UNSUPPORTED_SCREEN.
 *
 * @param session The session from th10_open().
 * @param entry The cursor to select: the menu's entry (0..2, `Continue` first) on
 *              TH10_SCREEN_KIND_MENU, the grid cell (0..90, `終` last) on
 *              TH10_SCREEN_KIND_NAME_ENTRY.
 * @return TH10_WRITE_SUCCESS with the cursor the game reports, or the reason it
 *         could not be moved.
 *
 * @note Confirming is still a key press: this only puts the highlight where a
 *       press would have put it. A caller that wants the screen gone sends its
 *       confirm afterwards.
 */
th10_write_result th10_write_screen_cursor(th10_session *session, int32_t entry);

/** @brief Why th10_read_stage_frames() returned what it did. */
typedef enum th10_frames_result_tag {
    TH10_FRAMES_SUCCESS = 0, /**< the counter was read; see value.frames */
    TH10_FRAMES_INVALID_SESSION, /**< the session is NULL or already closed */
    TH10_FRAMES_READ_FAILED /**< the counter could not be read; see value.read_failed */
} th10_frames_result_tag;

/** @brief The outcome of th10_read_stage_frames(). */
typedef struct th10_frames_result {
    th10_frames_result_tag tag;
    union {
        uint32_t frames; /**< the stage frame counter; valid on success */
        th10_read_failure read_failed; /**< the read that failed */
    } value;
} th10_frames_result;

/**
 * @brief Reads the stage frame counter, the game's own clock.
 *
 * The word at 0x00474C88 advances while a stage is playing and freezes while it
 * is paused. It is the same word th10_read_state() samples twice to separate
 * playing from paused, exposed on its own so that a caller can wait for the game
 * to advance - or notice that it has stopped - without paying that built-in
 * 120 ms.
 *
 * @param session The session from th10_open().
 * @return TH10_FRAMES_SUCCESS with the counter in value.frames, or the reason it
 *         could not be read. A caller that waits for the counter to move must
 *         not read a failure as "the game has stopped": a read that failed says
 *         nothing about the game. The counter also lives in the game's static
 *         data, so a successful read says nothing about whether a stage is
 *         loaded; ask th10_read_state() for that.
 */
th10_frames_result th10_read_stage_frames(th10_session *session);

#ifdef __cplusplus
}
#endif

#endif
