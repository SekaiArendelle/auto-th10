#pragma once

#include <windows.h>
#include "read_memory.hh"

namespace auto_th10 {

inline bool is_game_over(HANDLE process) noexcept {
    auto hp = ::auto_th10::get_hp(process);
    return hp == -1;
}

inline void set_as_foreground(HWND hwnd) noexcept {
    ::AllowSetForegroundWindow(ASFW_ANY);
    ::SetForegroundWindow(hwnd);
}

} // namespace auto_th10
