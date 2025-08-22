#pragma once

#include <cstdio>
#include <cstdint>
#include <vector>
#include <memoryapi.h>
#include <exception/exception.hh>
#include "float32.hh"
#include "process.hh"
#include "thobjects.hh"

namespace auto_th10 {

namespace details {

[[noreturn]]
inline void game_not_start() noexcept {
    ::std::fprintf(stderr, "Game combat interface not started\n");
    ::std::fflush(stderr);
    ::exception::terminate();
}

} // namespace details

#ifdef _MSC_VER
    #pragma warning(disable : 4312)
#endif

inline auto get_player() noexcept {
    ::auto_th10::float32_type x, y;
    uint32_t obj_base{};
    ::std::size_t nbr;
    ::ReadProcessMemory(::auto_th10::get_process_handle(), LPCVOID(0x00477834), &obj_base, 4, &nbr);
    if (obj_base == 0) [[unlikely]] {
        ::auto_th10::details::game_not_start();
    }
    ::ReadProcessMemory(::auto_th10::get_process_handle(), reinterpret_cast<LPCVOID>(obj_base + 0x3C0), &x, 4, &nbr);
    ::ReadProcessMemory(::auto_th10::get_process_handle(), reinterpret_cast<LPCVOID>(obj_base + 0x3C4), &y, 4, &nbr);

    return ::auto_th10::Player{x, y};
}

inline auto get_enemies() noexcept {
    SIZE_T nbr;
    uint32_t base{}, obj_base{}, obj_addr, obj_next{};

    ::ReadProcessMemory(::auto_th10::get_process_handle(), LPCVOID(0x00477704), &base, 4, &nbr);
    if (base == 0) [[unlikely]] {
        ::auto_th10::details::game_not_start();
    }
    ::ReadProcessMemory(::auto_th10::get_process_handle(), reinterpret_cast<LPCVOID>(base + 0x58), &obj_base, 4, &nbr);

    ::std::vector<::auto_th10::Enemy> res{};
    if (obj_base) {
        while (true) {
            ::ReadProcessMemory(::auto_th10::get_process_handle(), reinterpret_cast<LPCVOID>(obj_base), &obj_addr, 4,
                                &nbr);
            ::ReadProcessMemory(::auto_th10::get_process_handle(), reinterpret_cast<LPCVOID>(obj_base + 4), &obj_next,
                                4, &nbr);
            obj_addr += 0x103C;
            uint32_t t;
            ::ReadProcessMemory(::auto_th10::get_process_handle(), reinterpret_cast<LPCVOID>(obj_addr + 0x1444), &t, 4,
                                &nbr);
            if ((t & 0x40) == 0) {
                ::ReadProcessMemory(::auto_th10::get_process_handle(), reinterpret_cast<LPCVOID>(obj_addr + 0x1444), &t,
                                    4, &nbr);
                if ((t & 0x12) == 0) {
                    ::auto_th10::float32_type x, y, w, h;
                    ::ReadProcessMemory(::auto_th10::get_process_handle(), reinterpret_cast<LPCVOID>(obj_addr + 0x2C),
                                        &x, 4, &nbr);
                    ::ReadProcessMemory(::auto_th10::get_process_handle(), reinterpret_cast<LPCVOID>(obj_addr + 0x30),
                                        &y, 4, &nbr);
                    ::ReadProcessMemory(::auto_th10::get_process_handle(), reinterpret_cast<LPCVOID>(obj_addr + 0xB8),
                                        &w, 4, &nbr);
                    ::ReadProcessMemory(::auto_th10::get_process_handle(), reinterpret_cast<LPCVOID>(obj_addr + 0xBC),
                                        &h, 4, &nbr);
                    res.emplace_back(x, y, w, h);
                }
            }
            if (obj_next == 0) {
                break;
            }
            obj_base = obj_next;
        }
    }
    return res;
}

inline auto get_enemy_bullets() noexcept {
    uint32_t base{};
    SIZE_T nbr;
    ::ReadProcessMemory(::auto_th10::get_process_handle(), LPCVOID(0x004776F0), &base, 4, &nbr);
    if (base == 0) [[unlikely]] {
        ::auto_th10::details::game_not_start();
    }
    uint32_t ebx = base + 0x60;
    ::std::vector<::auto_th10::EnemyBullet> res{};
    for (int _{}; _ < 2000; ++_) {
        uint32_t edi = ebx + 0x400;
        uint32_t bp{};
        ::ReadProcessMemory(::auto_th10::get_process_handle(), reinterpret_cast<LPCVOID>(edi + 0x46), &bp, 4, &nbr);
        bp &= 0x0000FFFF;
        if (bp) {
            uint32_t eax{};
            ::ReadProcessMemory(::auto_th10::get_process_handle(), reinterpret_cast<LPCVOID>(0x477810), &eax, 4, &nbr);
            if (eax) {
                ::ReadProcessMemory(::auto_th10::get_process_handle(), reinterpret_cast<LPCVOID>(eax + 0x58), &eax, 4,
                                    &nbr);
                if ((eax & 0x00000400) == 0) {
                    ::auto_th10::float32_type x, y, w, h, dx, dy;
                    ::ReadProcessMemory(::auto_th10::get_process_handle(), reinterpret_cast<LPCVOID>(ebx + 0x3C0), &dx,
                                        4, &nbr);
                    ::ReadProcessMemory(::auto_th10::get_process_handle(), reinterpret_cast<LPCVOID>(ebx + 0x3C4), &dy,
                                        4, &nbr);
                    ::ReadProcessMemory(::auto_th10::get_process_handle(), reinterpret_cast<LPCVOID>(ebx + 0x3B4), &x,
                                        4, &nbr);
                    ::ReadProcessMemory(::auto_th10::get_process_handle(), reinterpret_cast<LPCVOID>(ebx + 0x3B8), &y,
                                        4, &nbr);
                    ::ReadProcessMemory(::auto_th10::get_process_handle(), reinterpret_cast<LPCVOID>(ebx + 0x3F0), &w,
                                        4, &nbr);
                    ::ReadProcessMemory(::auto_th10::get_process_handle(), reinterpret_cast<LPCVOID>(ebx + 0x3F4), &h,
                                        4, &nbr);
                    res.emplace_back(x, y, w, h, dx, dy);
                }
            }
        }
        ebx += 0x7F0;
    }
    return res;
}

inline auto get_enemy_lasers() noexcept {
    uint32_t base{};
    SIZE_T nbr;
    ::ReadProcessMemory(::auto_th10::get_process_handle(), LPCVOID(0x0047781C), &base, 4, &nbr);
    if (base == 0) [[unlikely]] {
        ::auto_th10::details::game_not_start();
    }

    uint32_t esi{}, ebx{};
    ::ReadProcessMemory(::auto_th10::get_process_handle(), reinterpret_cast<LPCVOID>(base + 0x18), &esi, 4, &nbr);

    ::std::vector<::auto_th10::EnemyLaser> res{};
    if (esi) {
        while (true) {
            ::ReadProcessMemory(::auto_th10::get_process_handle(), reinterpret_cast<LPCVOID>(esi + 0x8), &ebx, 4, &nbr);
            ::auto_th10::float32_type x, y, h, w, radian;
            ::ReadProcessMemory(::auto_th10::get_process_handle(), reinterpret_cast<LPCVOID>(esi + 0x24), &x, 4, &nbr);
            ::ReadProcessMemory(::auto_th10::get_process_handle(), reinterpret_cast<LPCVOID>(esi + 0x28), &y, 4, &nbr);
            ::ReadProcessMemory(::auto_th10::get_process_handle(), reinterpret_cast<LPCVOID>(esi + 0x3C), &radian, 4,
                                &nbr);
            ::ReadProcessMemory(::auto_th10::get_process_handle(), reinterpret_cast<LPCVOID>(esi + 0x40), &h, 4, &nbr);
            ::ReadProcessMemory(::auto_th10::get_process_handle(), reinterpret_cast<LPCVOID>(esi + 0x44), &w, 4, &nbr);
            res.emplace_back(x, y, w, h, radian);
            if (ebx == 0) {
                break;
            }
            esi = ebx;
        }
    }
    return res;
}

inline auto get_resources() noexcept {
    SIZE_T nbr;
    uint32_t base;
    ::ReadProcessMemory(::auto_th10::get_process_handle(), LPCVOID(0x00477818), &base, 4, &nbr);
    if (base == 0) [[unlikely]] {
        ::auto_th10::details::game_not_start();
    }
    uint32_t esi = base + 0x14;
    uint32_t ebp = esi + 0x3B0;
    ::std::vector<::auto_th10::Resource> res{};

    for (int i = 0; i < 2000; i++) {
        uint32_t eax;
        ::ReadProcessMemory(::auto_th10::get_process_handle(), reinterpret_cast<LPCVOID>(ebp + 0x2C), &eax, 4, &nbr);
        if (eax != 0) {
            ::auto_th10::float32_type x, y;
            ::ReadProcessMemory(::auto_th10::get_process_handle(), reinterpret_cast<LPCVOID>(ebp - 0x4), &x, 4, &nbr);
            ::ReadProcessMemory(::auto_th10::get_process_handle(), reinterpret_cast<LPCVOID>(ebp), &y, 4, &nbr);
            res.emplace_back(x, y);
        }
        ebp += 0x3F0;
    }
    return res;
}

inline auto get_score() noexcept {
    ::std::uint32_t score;
    ::ReadProcessMemory(::auto_th10::get_process_handle(), reinterpret_cast<LPCVOID>(0x00474C44), &score, 4, nullptr);
    return score;
}

inline auto get_power() noexcept {
    ::std::uint16_t power;
    ::ReadProcessMemory(::auto_th10::get_process_handle(), reinterpret_cast<LPCVOID>(0x00474C48), &power, sizeof(power),
                        nullptr);
    return power;
}

/* Get Player's HP
 */
inline auto get_hp() noexcept {
    ::std::uint16_t hp;
    ::ReadProcessMemory(::auto_th10::get_process_handle(), reinterpret_cast<LPCVOID>(0x00474C70), &hp, sizeof(hp),
                        nullptr);
    return hp;
}

#ifdef _MSC_VER
    #pragma warning(default : 4312)
#endif

} // namespace auto_th10
