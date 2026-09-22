#include "internal.h"

#include <stdlib.h>
#include <string.h>

enum {
    BULLET_SLOT_COUNT = 2000,
    RESOURCE_SLOT_COUNT = 2000,
    MAX_LINKED_NODES = 4096,
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
    if (!READ_VALUE(session, 0x00477834u, base, failure)) {
        return failure;
    }
    if (base == 0) {
        return snapshot_not_in_game();
    }
    if (!READ_VALUE(session, base + 0x3C0u, snapshot->player.x, failure) ||
        !READ_VALUE(session, base + 0x3C4u, snapshot->player.y, failure)) {
        return failure;
    }
    return snapshot_success();
}

static th10_snapshot_result read_enemies(th10_session *session, th10_snapshot *snapshot) {
    th10_snapshot_result failure;
    uint32_t manager = 0;
    uint32_t node = 0;
    size_t visited = 0;

    if (!READ_VALUE(session, 0x00477704u, manager, failure)) {
        return failure;
    }
    if (manager == 0) {
        return snapshot_not_in_game();
    }
    if (!READ_VALUE(session, manager + 0x58u, node, failure)) {
        return failure;
    }
    while (node != 0 && visited++ < MAX_LINKED_NODES) {
        uint32_t object = 0;
        uint32_t next = 0;
        uint32_t flags = 0;
        th10_rect enemy;
        if (!READ_VALUE(session, node, object, failure) ||
            !READ_VALUE(session, node + 4u, next, failure)) {
            return failure;
        }
        object += 0x103Cu;
        if (!READ_VALUE(session, object + 0x1444u, flags, failure)) {
            return failure;
        }
        if ((flags & 0x52u) == 0) {
            if (!READ_VALUE(session, object + 0x2Cu, enemy.x, failure) ||
                !READ_VALUE(session, object + 0x30u, enemy.y, failure) ||
                !READ_VALUE(session, object + 0xB8u, enemy.width, failure) ||
                !READ_VALUE(session, object + 0xBCu, enemy.height, failure)) {
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

    if (!READ_VALUE(session, 0x004776F0u, manager, failure)) {
        return failure;
    }
    if (manager == 0) {
        return snapshot_not_in_game();
    }
    address = manager + 0x60u;
    for (index = 0; index < BULLET_SLOT_COUNT; ++index, address += 0x7F0u) {
        uint16_t active = 0;
        th10_enemy_bullet bullet;
        if (!READ_VALUE(session, address + 0x446u, active, failure)) {
            return failure;
        }
        if (active == 0) {
            continue;
        }
        if (!READ_VALUE(session, 0x00477810u, bullet_flags, failure)) {
            return failure;
        }
        if (bullet_flags == 0) {
            continue;
        }
        if (!READ_VALUE(session, bullet_flags + 0x58u, bullet_flags, failure)) {
            return failure;
        }
        if ((bullet_flags & 0x400u) != 0) {
            continue;
        }
        if (!READ_VALUE(session, address + 0x3B4u, bullet.x, failure) ||
            !READ_VALUE(session, address + 0x3B8u, bullet.y, failure) ||
            !READ_VALUE(session, address + 0x3F0u, bullet.width, failure) ||
            !READ_VALUE(session, address + 0x3F4u, bullet.height, failure) ||
            !READ_VALUE(session, address + 0x3C0u, bullet.dx, failure) ||
            !READ_VALUE(session, address + 0x3C4u, bullet.dy, failure)) {
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

    if (!READ_VALUE(session, 0x0047781Cu, manager, failure)) {
        return failure;
    }
    if (manager == 0) {
        return snapshot_not_in_game();
    }
    if (!READ_VALUE(session, manager + 0x18u, node, failure)) {
        return failure;
    }
    while (node != 0 && visited++ < MAX_LINKED_NODES) {
        uint32_t next = 0;
        th10_enemy_laser laser;
        if (!READ_VALUE(session, node + 0x8u, next, failure) ||
            !READ_VALUE(session, node + 0x24u, laser.x, failure) ||
            !READ_VALUE(session, node + 0x28u, laser.y, failure) ||
            !READ_VALUE(session, node + 0x44u, laser.width, failure) ||
            !READ_VALUE(session, node + 0x40u, laser.height, failure) ||
            !READ_VALUE(session, node + 0x3Cu, laser.radian, failure)) {
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

    if (!READ_VALUE(session, 0x00477818u, manager, failure)) {
        return failure;
    }
    if (manager == 0) {
        return snapshot_not_in_game();
    }
    address = manager + 0x3C4u;
    for (index = 0; index < RESOURCE_SLOT_COUNT; ++index, address += 0x3F0u) {
        uint32_t active = 0;
        th10_point resource;
        if (!READ_VALUE(session, address + 0x2Cu, active, failure)) {
            return failure;
        }
        if (active == 0) {
            continue;
        }
        if (!READ_VALUE(session, address - 0x4u, resource.x, failure) ||
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
    if (!READ_VALUE(session, 0x00474C44u, snapshot->score, result) ||
        !READ_VALUE(session, 0x00474C48u, snapshot->power, result) ||
        !READ_VALUE(session, 0x00474C70u, snapshot->lives, result)) {
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
