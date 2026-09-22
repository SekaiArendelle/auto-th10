#ifndef AUTO_TH10_AUTO_TH10_H
#define AUTO_TH10_AUTO_TH10_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct th10_session th10_session;

typedef enum th10_action {
    TH10_ACTION_NONE = 0,
    TH10_ACTION_LEFT = 1u << 0,
    TH10_ACTION_RIGHT = 1u << 1,
    TH10_ACTION_UP = 1u << 2,
    TH10_ACTION_DOWN = 1u << 3,
    TH10_ACTION_SHOOT = 1u << 4,
    TH10_ACTION_FOCUS = 1u << 5,
    TH10_ACTION_BOMB = 1u << 6
} th10_action;

typedef struct th10_point {
    float x;
    float y;
} th10_point;

typedef struct th10_rect {
    float x;
    float y;
    float width;
    float height;
} th10_rect;

typedef struct th10_enemy_bullet {
    float x;
    float y;
    float width;
    float height;
    float dx;
    float dy;
} th10_enemy_bullet;

typedef struct th10_enemy_laser {
    float x;
    float y;
    float width;
    float height;
    float radian;
} th10_enemy_laser;

typedef struct th10_point_array {
    th10_point *data;
    size_t size;
    size_t capacity;
} th10_point_array;

typedef struct th10_rect_array {
    th10_rect *data;
    size_t size;
    size_t capacity;
} th10_rect_array;

typedef struct th10_enemy_bullet_array {
    th10_enemy_bullet *data;
    size_t size;
    size_t capacity;
} th10_enemy_bullet_array;

typedef struct th10_enemy_laser_array {
    th10_enemy_laser *data;
    size_t size;
    size_t capacity;
} th10_enemy_laser_array;

typedef struct th10_snapshot {
    th10_point player;
    uint32_t score;
    uint16_t power;
    /* Remaining lives, not hit points: 2, 1, 0 while the run is alive and -1
     * once it is over. game_over repeats that -1 so callers need not know it. */
    int16_t lives;
    uint8_t game_over;
    th10_rect_array enemies;
    th10_enemy_bullet_array enemy_bullets;
    th10_enemy_laser_array enemy_lasers;
    th10_point_array resources;
} th10_snapshot;

typedef enum th10_open_result_tag {
    TH10_OPEN_SUCCESS = 0,
    TH10_OPEN_WINDOW_NOT_FOUND,
    TH10_OPEN_ENUM_WINDOWS_FAILED,
    TH10_OPEN_GET_PROCESS_ID_FAILED,
    TH10_OPEN_PROCESS_FAILED,
    TH10_OPEN_OUT_OF_MEMORY
} th10_open_result_tag;

typedef struct th10_open_result {
    th10_open_result_tag tag;
    union {
        th10_session *session;
        struct {
            uint32_t code;
        } win32_error;
    } value;
} th10_open_result;

typedef enum th10_focus_result_tag {
    TH10_FOCUS_SUCCESS = 0,
    TH10_FOCUS_INVALID_SESSION,
    TH10_FOCUS_PERMISSION_AND_SET_REJECTED,
    TH10_FOCUS_REJECTED
} th10_focus_result_tag;

typedef struct th10_focus_result {
    th10_focus_result_tag tag;
    union {
        struct {
            uint32_t win32_error;
        } permission_and_set_rejected;
    } value;
} th10_focus_result;

typedef enum th10_input_result_tag {
    TH10_INPUT_SUCCESS = 0,
    TH10_INPUT_INVALID_SESSION,
    TH10_INPUT_UNSUPPORTED_ACTION,
    TH10_INPUT_SEND_FAILED
} th10_input_result_tag;

typedef struct th10_input_result {
    th10_input_result_tag tag;
    union {
        struct {
            uint32_t unsupported_bits;
        } unsupported_action;
        struct {
            uint32_t requested_count;
            uint32_t inserted_count;
            uint32_t win32_error;
        } send_failed;
    } value;
} th10_input_result;

typedef enum th10_close_result_tag {
    TH10_CLOSE_SUCCESS = 0,
    TH10_CLOSE_INVALID_SESSION,
    TH10_CLOSE_INPUT_RELEASE_FAILED,
    TH10_CLOSE_HANDLE_FAILED,
    TH10_CLOSE_INPUT_AND_HANDLE_FAILED
} th10_close_result_tag;

typedef struct th10_close_result {
    th10_close_result_tag tag;
    union {
        th10_input_result input_release_failed;
        struct {
            uint32_t win32_error;
        } handle_failed;
        struct {
            th10_input_result input;
            uint32_t win32_error;
        } input_and_handle_failed;
    } value;
} th10_close_result;

typedef enum th10_snapshot_array {
    TH10_SNAPSHOT_ENEMIES = 0,
    TH10_SNAPSHOT_ENEMY_BULLETS,
    TH10_SNAPSHOT_ENEMY_LASERS,
    TH10_SNAPSHOT_RESOURCES
} th10_snapshot_array;

typedef enum th10_snapshot_result_tag {
    TH10_SNAPSHOT_SUCCESS = 0,
    TH10_SNAPSHOT_INVALID_ARGUMENT,
    TH10_SNAPSHOT_NOT_IN_GAME,
    TH10_SNAPSHOT_READ_FAILED,
    TH10_SNAPSHOT_ALLOCATION_FAILED
} th10_snapshot_result_tag;

/* Why a memory read failed. Shared by the snapshot result and the internal
 * reader, so both describe a failure the same way. */
typedef struct th10_read_failure {
    uintptr_t address;
    size_t requested_size;
    size_t bytes_read;
    uint32_t win32_error;
} th10_read_failure;

typedef struct th10_snapshot_result {
    th10_snapshot_result_tag tag;
    union {
        th10_read_failure read_failed;
        struct {
            th10_snapshot_array array;
            size_t requested_capacity;
            size_t element_size;
        } allocation_failed;
    } value;
} th10_snapshot_result;

th10_open_result th10_open(void);
th10_close_result th10_close(th10_session *session);
th10_focus_result th10_focus(th10_session *session);
th10_input_result th10_set_input(th10_session *session, uint32_t action_mask);

/* A snapshot must be initialized before its first read. Clear keeps allocated
 * array storage for reuse; destroy releases all array storage. */
void th10_snapshot_init(th10_snapshot *snapshot);
void th10_snapshot_clear(th10_snapshot *snapshot);
void th10_snapshot_destroy(th10_snapshot *snapshot);
th10_snapshot_result th10_read_snapshot(th10_session *session, th10_snapshot *out_snapshot);

typedef enum th10_capture_result_tag {
    TH10_CAPTURE_SUCCESS = 0,
    TH10_CAPTURE_INVALID_ARGUMENT,
    TH10_CAPTURE_CLIENT_RECT_FAILED,
    TH10_CAPTURE_CREATE_DC_FAILED,
    TH10_CAPTURE_CREATE_BITMAP_FAILED,
    TH10_CAPTURE_PRINT_WINDOW_FAILED,
    TH10_CAPTURE_FILE_OPEN_FAILED,
    TH10_CAPTURE_FILE_WRITE_FAILED
} th10_capture_result_tag;

typedef struct th10_capture_result {
    th10_capture_result_tag tag;
    uint32_t width;
    uint32_t height;
    uint32_t win32_error;
} th10_capture_result;

/* Captures the client area of the game window into a 32-bit BMP file. The
 * window is asked to draw itself through PrintWindow, so this reads the game's
 * own content rather than the screen: it works while the game is in the
 * background or covered over, and needs neither the focus nor the foreground. */
th10_capture_result th10_capture(th10_session *session, const wchar_t *path);

#ifdef __cplusplus
}
#endif

#endif
