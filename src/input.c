#include "internal.h"

/* DirectInput games read the keyboard by scan code, so a key injected with only
 * a virtual key never reaches the game: without KEYEVENTF_SCANCODE the four
 * arrow keys are delivered as their numeric keypad twins, which the game does
 * not look at. Every action therefore carries its own scan code, and the four
 * arrow keys are extended keys (without the E0 prefix they have no scan code of
 * their own). Every row here was verified against th10.exe by injection
 * experiment, Escape included. */
typedef struct action_key {
    uint32_t action;
    WORD scan_code;
    DWORD flags;
} action_key;

static const action_key ACTION_KEYS[] = {
    {TH10_ACTION_LEFT, 0x4Bu, KEYEVENTF_EXTENDEDKEY},
    {TH10_ACTION_RIGHT, 0x4Du, KEYEVENTF_EXTENDEDKEY},
    {TH10_ACTION_UP, 0x48u, KEYEVENTF_EXTENDEDKEY},
    {TH10_ACTION_DOWN, 0x50u, KEYEVENTF_EXTENDEDKEY},
    {TH10_ACTION_SHOOT, 0x2Cu, 0}, /* 'Z' */
    {TH10_ACTION_FOCUS, 0x2Au, 0}, /* left Shift */
    {TH10_ACTION_BOMB, 0x2Du, 0},  /* 'X' */
    {TH10_ACTION_ESCAPE, 0x01u, 0}, /* Escape */
};

#define ACTION_KEY_COUNT (sizeof(ACTION_KEYS) / sizeof(ACTION_KEYS[0]))

th10_input_result th10_set_input(th10_session *session, uint32_t action_mask) {
    INPUT inputs[ACTION_KEY_COUNT];
    UINT count = 0;
    size_t index;
    const uint32_t supported = TH10_ACTION_LEFT | TH10_ACTION_RIGHT | TH10_ACTION_UP |
                               TH10_ACTION_DOWN | TH10_ACTION_SHOOT | TH10_ACTION_FOCUS |
                               TH10_ACTION_BOMB | TH10_ACTION_ESCAPE;

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
    for (index = 0; index < ACTION_KEY_COUNT; ++index) {
        const bool was_down = (session->action_mask & ACTION_KEYS[index].action) != 0;
        const bool should_be_down = (action_mask & ACTION_KEYS[index].action) != 0;
        if (was_down == should_be_down) {
            continue;
        }
        inputs[count].type = INPUT_KEYBOARD;
        inputs[count].ki.wVk = 0; /* KEYEVENTF_SCANCODE requires a zero virtual key */
        inputs[count].ki.wScan = ACTION_KEYS[index].scan_code;
        inputs[count].ki.dwFlags =
            KEYEVENTF_SCANCODE | ACTION_KEYS[index].flags | (should_be_down ? 0u : KEYEVENTF_KEYUP);
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
