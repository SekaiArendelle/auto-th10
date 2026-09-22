#include "auto_th10/auto_th10.h"

#include <assert.h>
#include <stdlib.h>

int main(void) {
    th10_snapshot snapshot;
    th10_snapshot_result snapshot_result = th10_read_snapshot(NULL, NULL);
    th10_input_result input_result = th10_set_input(NULL, TH10_ACTION_NONE);
    th10_close_result close_result = th10_close(NULL);
    uint32_t frames = 0;
    assert(snapshot_result.tag == TH10_SNAPSHOT_INVALID_ARGUMENT);
    assert(input_result.tag == TH10_INPUT_INVALID_SESSION);
    assert(close_result.tag == TH10_CLOSE_INVALID_SESSION);
    assert(th10_focus(NULL).tag == TH10_FOCUS_INVALID_SESSION);
    assert(th10_capture(NULL, L"shot.bmp").tag == TH10_CAPTURE_INVALID_ARGUMENT);
    assert(!th10_read_record_broken(NULL));
    assert(!th10_read_stage_frames(NULL, &frames));
    assert(!th10_read_stage_frames(NULL, NULL));
    th10_snapshot_init(&snapshot);
    assert(snapshot.enemies.data == NULL);
    assert(snapshot.enemies.size == 0);
    assert(snapshot.enemies.capacity == 0);
    snapshot.enemies.data = malloc(8 * sizeof(*snapshot.enemies.data));
    assert(snapshot.enemies.data != NULL);
    snapshot.enemies.size = 3;
    snapshot.enemies.capacity = 8;
    th10_snapshot_clear(&snapshot);
    assert(snapshot.enemies.data != NULL);
    assert(snapshot.enemies.size == 0);
    assert(snapshot.enemies.capacity == 8);
    th10_snapshot_destroy(&snapshot);
    assert(snapshot.enemies.data == NULL);
    assert(snapshot.enemies.capacity == 0);
    return 0;
}
