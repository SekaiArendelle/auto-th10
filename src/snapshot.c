#include "internal.h"

#include <stdlib.h>
#include <string.h>

enum {
    BULLET_SLOT_COUNT = 2000,
    RESOURCE_SLOT_COUNT = 2000,
    MAX_LINKED_NODES = 4096,
};

/* Offsets inside the structures the game hangs off the addresses in internal.h.
 * These stay local: they are relative to a pointer the snapshot has already
 * resolved, and they describe this file's reading of those structures rather
 * than anything shared. */
enum {
    /* The stage object: the player's own position. */
    STAGE_PLAYER_X = 0x3C0u,
    STAGE_PLAYER_Y = 0x3C4u,

    /* The enemy manager points at a linked list; each node holds the object
     * pointer and the next node, and the object is offset by a fixed amount
     * before its flags and rectangle become readable. */
    NODE_OBJECT = 0x0u,
    NODE_NEXT = 0x4u,
    ENEMY_OBJECT_BIAS = 0x103Cu,
    ENEMY_FLAGS = 0x1444u,
    ENEMY_FLAGS_HIDDEN = 0x52u,
    ENEMY_X = 0x2Cu,
    ENEMY_Y = 0x30u,
    ENEMY_WIDTH = 0xB8u,
    ENEMY_HEIGHT = 0xBCu,
    ENEMY_LIST_HEAD = 0x58u,

    /* Enemy bullets are a fixed stride array hung off the manager. */
    BULLET_SLOTS = 0x60u,
    BULLET_STRIDE = 0x7F0u,
    BULLET_ACTIVE = 0x446u,
    BULLET_FLAGS_NODE = 0x58u,
    BULLET_FLAG_HIDDEN = 0x400u,
    BULLET_X = 0x3B4u,
    BULLET_Y = 0x3B8u,
    BULLET_WIDTH = 0x3F0u,
    BULLET_HEIGHT = 0x3F4u,
    BULLET_DX = 0x3C0u,
    BULLET_DY = 0x3C4u,

    /* Enemy lasers are a linked list read in place, with no object bias. */
    LASER_LIST_HEAD = 0x18u,
    LASER_NEXT = 0x8u,
    LASER_X = 0x24u,
    LASER_Y = 0x28u,
    LASER_HEIGHT = 0x40u,
    LASER_WIDTH = 0x44u,
    LASER_RADIAN = 0x3Cu,

    /* Resources are a fixed stride array; the loop address sits four bytes past
     * the pair the snapshot reads, hence the bias. */
    RESOURCE_SLOTS = 0x3C4u,
    RESOURCE_STRIDE = 0x3F0u,
    RESOURCE_ACTIVE = 0x2Cu,
    RESOURCE_X_BIAS = 0x4u,
};

static th10_snapshot_result snapshot_success(void) {
    return (th10_snapshot_result){.tag = TH10_SNAPSHOT_SUCCESS};
}

static th10_snapshot_result snapshot_not_in_game(void) {
    return (th10_snapshot_result){.tag = TH10_SNAPSHOT_NOT_IN_GAME};
}

/* Reads and reports whether it succeeded, recording the failure details into the
 * snapshot result so the caller can hand the whole result back. */
static bool read_value(th10_session *session, uintptr_t address, void *output, size_t size,
                       th10_snapshot_result *failure) {
    if (th10_read_memory(session, address, output, size, &failure->value.read_failed)) {
        return true;
    }
    failure->tag = TH10_SNAPSHOT_READ_FAILED;
    return false;
}

#define READ_VALUE(session, address, value, failure) \
    read_value((session), (uintptr_t)(address), &(value), sizeof(value), &(failure))

static size_t next_capacity(size_t capacity) {
    return capacity == 0 ? 16u : (capacity > SIZE_MAX / 2u ? SIZE_MAX : capacity * 2u);
}

static th10_snapshot_result allocation_failure(th10_snapshot_array array, size_t capacity,
                                               size_t element_size) {
    return (th10_snapshot_result){
        .tag = TH10_SNAPSHOT_ALLOCATION_FAILED,
        .value.allocation_failed = {
            .array = array,
            .requested_capacity = next_capacity(capacity),
            .element_size = element_size,
        },
    };
}

void th10_snapshot_init(th10_snapshot *snapshot) {
    if (snapshot != NULL) {
        memset(snapshot, 0, sizeof(*snapshot));
    }
}

void th10_snapshot_clear(th10_snapshot *snapshot) {
    if (snapshot == NULL) {
        return;
    }
    snapshot->player = (th10_point){0};
    snapshot->score = 0;
    snapshot->power = 0;
    snapshot->lives = 0;
    snapshot->game_over = 0;
    snapshot->enemies.size = 0;
    snapshot->enemy_bullets.size = 0;
    snapshot->enemy_lasers.size = 0;
    snapshot->resources.size = 0;
}

void th10_snapshot_destroy(th10_snapshot *snapshot) {
    if (snapshot == NULL) {
        return;
    }
    free(snapshot->enemies.data);
    free(snapshot->enemy_bullets.data);
    free(snapshot->enemy_lasers.data);
    free(snapshot->resources.data);
    memset(snapshot, 0, sizeof(*snapshot));
}

static void *grow_array(void *data, size_t capacity, size_t element_size, size_t *new_capacity) {
    const size_t capacity_candidate = next_capacity(capacity);
    void *new_data;

    if (capacity_candidate == SIZE_MAX || capacity_candidate > SIZE_MAX / element_size) {
        return NULL;
    }
    new_data = realloc(data, capacity_candidate * element_size);
    if (new_data != NULL) {
        *new_capacity = capacity_candidate;
    }
    return new_data;
}

#define DEFINE_APPEND(name, array_type, value_type)                                                \
    static bool name(array_type *array, value_type value) {                                        \
        if (array->size == array->capacity) {                                                       \
            size_t new_capacity = 0;                                                               \
            void *new_data = grow_array(array->data, array->capacity, sizeof(*array->data),         \
                                        &new_capacity);                                             \
            if (new_data == NULL) {                                                                \
                return false;                                                                      \
            }                                                                                      \
            array->data = (value_type *)new_data;                                                   \
            array->capacity = new_capacity;                                                        \
        }                                                                                          \
        array->data[array->size++] = value;                                                        \
        return true;                                                                               \
    }

DEFINE_APPEND(append_point, th10_point_array, th10_point)
DEFINE_APPEND(append_rect, th10_rect_array, th10_rect)
DEFINE_APPEND(append_bullet, th10_enemy_bullet_array, th10_enemy_bullet)
DEFINE_APPEND(append_laser, th10_enemy_laser_array, th10_enemy_laser)

#undef DEFINE_APPEND

static th10_snapshot_result read_player(th10_session *session, th10_snapshot *snapshot) {
    th10_snapshot_result failure;
    uint32_t base = 0;
    if (!READ_VALUE(session, TH10_STAGE_BASE_ADDRESS, base, failure)) {
        return failure;
    }
    if (base == 0) {
        return snapshot_not_in_game();
    }
    if (!READ_VALUE(session, base + STAGE_PLAYER_X, snapshot->player.x, failure) ||
        !READ_VALUE(session, base + STAGE_PLAYER_Y, snapshot->player.y, failure)) {
        return failure;
    }
    return snapshot_success();
}

static th10_snapshot_result read_enemies(th10_session *session, th10_snapshot *snapshot) {
    th10_snapshot_result failure;
    uint32_t manager = 0;
    uint32_t node = 0;
    size_t visited = 0;

    if (!READ_VALUE(session, TH10_ENEMY_MANAGER_ADDRESS, manager, failure)) {
        return failure;
    }
    if (manager == 0) {
        return snapshot_not_in_game();
    }
    if (!READ_VALUE(session, manager + ENEMY_LIST_HEAD, node, failure)) {
        return failure;
    }
    while (node != 0 && visited++ < MAX_LINKED_NODES) {
        uint32_t object = 0;
        uint32_t next = 0;
        uint32_t flags = 0;
        th10_rect enemy;
        if (!READ_VALUE(session, node + NODE_OBJECT, object, failure) ||
            !READ_VALUE(session, node + NODE_NEXT, next, failure)) {
            return failure;
        }
        object += ENEMY_OBJECT_BIAS;
        if (!READ_VALUE(session, object + ENEMY_FLAGS, flags, failure)) {
            return failure;
        }
        if ((flags & ENEMY_FLAGS_HIDDEN) == 0) {
            if (!READ_VALUE(session, object + ENEMY_X, enemy.x, failure) ||
                !READ_VALUE(session, object + ENEMY_Y, enemy.y, failure) ||
                !READ_VALUE(session, object + ENEMY_WIDTH, enemy.width, failure) ||
                !READ_VALUE(session, object + ENEMY_HEIGHT, enemy.height, failure)) {
                return failure;
            }
            if (!append_rect(&snapshot->enemies, enemy)) {
                return allocation_failure(TH10_SNAPSHOT_ENEMIES, snapshot->enemies.capacity,
                                          sizeof(*snapshot->enemies.data));
            }
        }
        node = next;
    }
    return snapshot_success();
}

static th10_snapshot_result read_enemy_bullets(th10_session *session, th10_snapshot *snapshot) {
    th10_snapshot_result failure;
    uint32_t manager = 0;
    uint32_t bullet_flags = 0;
    uint32_t address;
    size_t index;

    if (!READ_VALUE(session, TH10_BULLET_MANAGER_ADDRESS, manager, failure)) {
        return failure;
    }
    if (manager == 0) {
        return snapshot_not_in_game();
    }
    address = manager + BULLET_SLOTS;
    for (index = 0; index < BULLET_SLOT_COUNT; ++index, address += BULLET_STRIDE) {
        uint16_t active = 0;
        th10_enemy_bullet bullet;
        if (!READ_VALUE(session, address + BULLET_ACTIVE, active, failure)) {
            return failure;
        }
        if (active == 0) {
            continue;
        }
        if (!READ_VALUE(session, TH10_BULLET_FLAGS_ADDRESS, bullet_flags, failure)) {
            return failure;
        }
        if (bullet_flags == 0) {
            continue;
        }
        if (!READ_VALUE(session, bullet_flags + BULLET_FLAGS_NODE, bullet_flags, failure)) {
            return failure;
        }
        if ((bullet_flags & BULLET_FLAG_HIDDEN) != 0) {
            continue;
        }
        if (!READ_VALUE(session, address + BULLET_X, bullet.x, failure) ||
            !READ_VALUE(session, address + BULLET_Y, bullet.y, failure) ||
            !READ_VALUE(session, address + BULLET_WIDTH, bullet.width, failure) ||
            !READ_VALUE(session, address + BULLET_HEIGHT, bullet.height, failure) ||
            !READ_VALUE(session, address + BULLET_DX, bullet.dx, failure) ||
            !READ_VALUE(session, address + BULLET_DY, bullet.dy, failure)) {
            return failure;
        }
        if (!append_bullet(&snapshot->enemy_bullets, bullet)) {
            return allocation_failure(TH10_SNAPSHOT_ENEMY_BULLETS,
                                      snapshot->enemy_bullets.capacity,
                                      sizeof(*snapshot->enemy_bullets.data));
        }
    }
    return snapshot_success();
}

static th10_snapshot_result read_enemy_lasers(th10_session *session, th10_snapshot *snapshot) {
    th10_snapshot_result failure;
    uint32_t manager = 0;
    uint32_t node = 0;
    size_t visited = 0;

    if (!READ_VALUE(session, TH10_LASER_MANAGER_ADDRESS, manager, failure)) {
        return failure;
    }
    if (manager == 0) {
        return snapshot_not_in_game();
    }
    if (!READ_VALUE(session, manager + LASER_LIST_HEAD, node, failure)) {
        return failure;
    }
    while (node != 0 && visited++ < MAX_LINKED_NODES) {
        uint32_t next = 0;
        th10_enemy_laser laser;
        if (!READ_VALUE(session, node + LASER_NEXT, next, failure) ||
            !READ_VALUE(session, node + LASER_X, laser.x, failure) ||
            !READ_VALUE(session, node + LASER_Y, laser.y, failure) ||
            !READ_VALUE(session, node + LASER_WIDTH, laser.width, failure) ||
            !READ_VALUE(session, node + LASER_HEIGHT, laser.height, failure) ||
            !READ_VALUE(session, node + LASER_RADIAN, laser.radian, failure)) {
            return failure;
        }
        if (!append_laser(&snapshot->enemy_lasers, laser)) {
            return allocation_failure(TH10_SNAPSHOT_ENEMY_LASERS,
                                      snapshot->enemy_lasers.capacity,
                                      sizeof(*snapshot->enemy_lasers.data));
        }
        node = next;
    }
    return snapshot_success();
}

static th10_snapshot_result read_resources(th10_session *session, th10_snapshot *snapshot) {
    th10_snapshot_result failure;
    uint32_t manager = 0;
    uint32_t address;
    size_t index;

    if (!READ_VALUE(session, TH10_RESOURCE_MANAGER_ADDRESS, manager, failure)) {
        return failure;
    }
    if (manager == 0) {
        return snapshot_not_in_game();
    }
    address = manager + RESOURCE_SLOTS;
    for (index = 0; index < RESOURCE_SLOT_COUNT; ++index, address += RESOURCE_STRIDE) {
        uint32_t active = 0;
        th10_point resource;
        if (!READ_VALUE(session, address + RESOURCE_ACTIVE, active, failure)) {
            return failure;
        }
        if (active == 0) {
            continue;
        }
        if (!READ_VALUE(session, address - RESOURCE_X_BIAS, resource.x, failure) ||
            !READ_VALUE(session, address, resource.y, failure)) {
            return failure;
        }
        if (!append_point(&snapshot->resources, resource)) {
            return allocation_failure(TH10_SNAPSHOT_RESOURCES, snapshot->resources.capacity,
                                      sizeof(*snapshot->resources.data));
        }
    }
    return snapshot_success();
}

th10_snapshot_result th10_read_snapshot(th10_session *session, th10_snapshot *snapshot) {
    th10_snapshot_result result;

    if (session == NULL || snapshot == NULL) {
        return (th10_snapshot_result){.tag = TH10_SNAPSHOT_INVALID_ARGUMENT};
    }
    th10_snapshot_clear(snapshot);
    if (!READ_VALUE(session, TH10_SCORE_ADDRESS, snapshot->score, result) ||
        !READ_VALUE(session, TH10_POWER_ADDRESS, snapshot->power, result) ||
        !READ_VALUE(session, TH10_LIVES_ADDRESS, snapshot->lives, result)) {
        return result;
    }
    snapshot->game_over = snapshot->lives == -1;

    result = read_player(session, snapshot);
    if (result.tag != TH10_SNAPSHOT_SUCCESS) return result;
    result = read_enemies(session, snapshot);
    if (result.tag != TH10_SNAPSHOT_SUCCESS) return result;
    result = read_enemy_bullets(session, snapshot);
    if (result.tag != TH10_SNAPSHOT_SUCCESS) return result;
    result = read_enemy_lasers(session, snapshot);
    if (result.tag != TH10_SNAPSHOT_SUCCESS) return result;
    return read_resources(session, snapshot);
}
