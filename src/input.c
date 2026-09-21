#include "internal.h"

typedef struct action_key {
    uint32_t action;
    WORD virtual_key;
} action_key;

static const action_key ACTION_KEYS[] = {
    {TH10_ACTION_LEFT, VK_LEFT},
    {TH10_ACTION_RIGHT, VK_RIGHT},
    {TH10_ACTION_UP, VK_UP},
    {TH10_ACTION_DOWN, VK_DOWN},
    {TH10_ACTION_SHOOT, 'Z'},
    {TH10_ACTION_FOCUS, VK_SHIFT},
    {TH10_ACTION_BOMB, 'X'},
};

th10_input_result th10_set_input(th10_session *session, uint32_t action_mask) {
    INPUT inputs[sizeof(ACTION_KEYS) / sizeof(ACTION_KEYS[0])];
    UINT count = 0;
    size_t index;
    const uint32_t supported = TH10_ACTION_LEFT | TH10_ACTION_RIGHT | TH10_ACTION_UP |
                               TH10_ACTION_DOWN | TH10_ACTION_SHOOT | TH10_ACTION_FOCUS |
                               TH10_ACTION_BOMB;

    if (session == NULL) {
        return (th10_input_result){.tag = TH10_INPUT_INVALID_SESSION};
    }
    if ((action_mask & ~supported) != 0) {
        return (th10_input_result){
            .tag = TH10_INPUT_UNSUPPORTED_ACTION,
            .value.unsupported_action = {.unsupported_bits = action_mask & ~supported},
        };
    }

    ZeroMemory(inputs, sizeof(inputs));
    for (index = 0; index < sizeof(ACTION_KEYS) / sizeof(ACTION_KEYS[0]); ++index) {
        const bool was_down = (session->action_mask & ACTION_KEYS[index].action) != 0;
        const bool should_be_down = (action_mask & ACTION_KEYS[index].action) != 0;
        if (was_down == should_be_down) {
            continue;
        }
        inputs[count].type = INPUT_KEYBOARD;
        inputs[count].ki.wVk = ACTION_KEYS[index].virtual_key;
        inputs[count].ki.dwFlags = should_be_down ? 0 : KEYEVENTF_KEYUP;
        ++count;
    }

    if (count != 0) {
        const UINT inserted = SendInput(count, inputs, sizeof(INPUT));
        if (inserted != count) {
            return (th10_input_result){
                .tag = TH10_INPUT_SEND_FAILED,
                .value.send_failed = {
                    .requested_count = count,
                    .inserted_count = inserted,
                    .win32_error = GetLastError(),
                },
            };
        }
    }
    session->action_mask = action_mask;
    return (th10_input_result){.tag = TH10_INPUT_SUCCESS};
}
