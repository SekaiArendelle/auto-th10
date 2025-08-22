#pragma once

#include <cstdio>
#include <cstring>
#include <memory>
#include <windows.h>
#include <exception/exception.hh>

namespace auto_th10 {

namespace details {

inline BOOL enum_window_proc(HWND hwnd, LPARAM th10_hwnd_) noexcept {
    char title[64]{};
    if (::GetWindowTextA(hwnd, title, sizeof(title)) <= 0) {
        return TRUE;
    }
    if (::std::strstr(title, "Mountain of Faith") == nullptr) {
        return TRUE;
    }
    HWND* th10_hwnd_ptr = reinterpret_cast<HWND*>(th10_hwnd_);
    *th10_hwnd_ptr = hwnd;
    return FALSE;
}

} // namespace details

/**
 * @brief Get TH10 window handle
 */
inline HWND get_hwnd() noexcept {
    HWND th10_hwnd
#if __has_cpp_attribute(indeterminate)
        [[indeterminate]]
#endif
        ;
    ::EnumWindows(details::enum_window_proc, reinterpret_cast<LPARAM>(::std::addressof(th10_hwnd)));
    if (th10_hwnd == nullptr) [[unlikely]] {
        ::std::fprintf(stderr, "auto_th10::BindError: get_hwnd failed\n");
        ::std::fflush(stderr);
        ::exception::terminate();
    }
    return th10_hwnd;
}

/**
 * @brief Get TH10 process ID
 */
inline DWORD get_pid() noexcept {
    DWORD pid
#if __has_cpp_attribute(indeterminate)
        [[indeterminate]]
#endif
        ;
    if (::GetWindowThreadProcessId(get_hwnd(), ::std::addressof(pid)) == 0) [[unlikely]] {
        ::std::fprintf(stderr, "auto_th10::BindError: get_pid failed\n");
        ::std::fflush(stderr);
        ::exception::terminate();
    }
    return pid;
}

/**
 * @brief Get TH10 process handle
 */
inline HANDLE get_process_handle() noexcept {
    return ::OpenProcess(PROCESS_VM_READ, true, get_pid());
}

} // namespace auto_th10
