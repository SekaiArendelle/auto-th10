#pragma once

#include <memory>
#include <cstdio>
#include <cstddef>
#include <cstdint>
#include <memoryapi.h>
#include <fast_io/fast_io_dsal/vector.h>
#include <exception/exception.hh>
#include "float32.hh"
#include "thobjects.hh"

namespace auto_th10 {

namespace details {

[[noreturn]]
inline void game_not_start() noexcept {
    ::std::fprintf(stderr, "auto_th10::BindError: Game combat interface not started\n");
    ::std::fflush(stderr);
    ::exception::terminate();
}

} // namespace details

#ifdef _MSC_VER
    #pragma warning(disable : 4312)
#endif

inline auto get_player(HANDLE process) noexcept {
    ::std::uint32_t obj_base{};
    ::std::size_t nbr;
    ::ReadProcessMemory(process, LPCVOID(0x00477834), &obj_base, 4, &nbr);
    if (obj_base == 0) [[unlikely]] {
        ::auto_th10::details::game_not_start();
    }
#if __has_cpp_attribute(indeterminate)
    ::auto_th10::float32_type x [[indeterminate]];
    ::auto_th10::float32_type y [[indeterminate]];
#else
    ::auto_th10::float32_type x;
    ::auto_th10::float32_type y;
#endif
    ::ReadProcessMemory(process, reinterpret_cast<LPCVOID>(obj_base + 0x3C0), &x, 4, &nbr);
    ::ReadProcessMemory(process, reinterpret_cast<LPCVOID>(obj_base + 0x3C4), &y, 4, &nbr);

    return ::auto_th10::Player{x, y};
}

inline auto get_enemies(HANDLE process) noexcept {
    ::std::size_t nbr;
    ::std::uint32_t base{}, obj_base{}, obj_addr, obj_next{};

    ::ReadProcessMemory(process, LPCVOID(0x00477704), &base, 4, &nbr);
    if (base == 0) [[unlikely]] {
        ::auto_th10::details::game_not_start();
    }
    ::ReadProcessMemory(process, reinterpret_cast<LPCVOID>(base + 0x58), &obj_base, 4, &nbr);

    ::fast_io::vector<::auto_th10::Enemy> res{};
    if (obj_base) {
        while (true) {
            ::ReadProcessMemory(process, reinterpret_cast<LPCVOID>(obj_base), &obj_addr, 4, &nbr);
            ::ReadProcessMemory(process, reinterpret_cast<LPCVOID>(obj_base + 4), &obj_next, 4, &nbr);
            obj_addr += 0x103C;
            ::std::uint32_t t;
            ::ReadProcessMemory(process, reinterpret_cast<LPCVOID>(obj_addr + 0x1444), &t, 4, &nbr);
            if ((t & 0x40) == 0) {
                ::ReadProcessMemory(process, reinterpret_cast<LPCVOID>(obj_addr + 0x1444), &t, 4, &nbr);
                if ((t & 0x12) == 0) {
                    ::auto_th10::float32_type x, y, w, h;
                    ::ReadProcessMemory(process, reinterpret_cast<LPCVOID>(obj_addr + 0x2C), ::std::addressof(x), 4,
                                        ::std::addressof(nbr));
                    ::ReadProcessMemory(process, reinterpret_cast<LPCVOID>(obj_addr + 0x30), ::std::addressof(y), 4,
                                        ::std::addressof(nbr));
                    ::ReadProcessMemory(process, reinterpret_cast<LPCVOID>(obj_addr + 0xB8), ::std::addressof(w), 4,
                                        ::std::addressof(nbr));
                    ::ReadProcessMemory(process, reinterpret_cast<LPCVOID>(obj_addr + 0xBC), ::std::addressof(h), 4,
                                        ::std::addressof(nbr));
                    res.push_back({{x, y}, w, h});
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

inline auto get_enemy_bullets(HANDLE process) noexcept {
    ::std::uint32_t base{};
    ::std::size_t nbr;
    ::ReadProcessMemory(process, LPCVOID(0x004776F0), &base, 4, &nbr);
    if (base == 0) [[unlikely]] {
        ::auto_th10::details::game_not_start();
    }
    ::std::uint32_t ebx{base + 0x60};
    ::fast_io::vector<::auto_th10::EnemyBullet> res{};
    for (int _{}; _ < 2000; ++_) {
        ::std::uint32_t edi{ebx + 0x400};
        ::std::uint32_t bp{};
        ::ReadProcessMemory(process, reinterpret_cast<LPCVOID>(edi + 0x46), &bp, 4, &nbr);
        bp &= 0x0000FFFF;
        if (bp) {
            ::std::uint32_t eax{};
            ::ReadProcessMemory(process, reinterpret_cast<LPCVOID>(0x477810), &eax, 4, &nbr);
            if (eax) {
                ::ReadProcessMemory(process, reinterpret_cast<LPCVOID>(eax + 0x58), &eax, 4, &nbr);
                if ((eax & 0x00000400) == 0) {
                    ::auto_th10::float32_type x, y, w, h, dx, dy;
                    ::ReadProcessMemory(process, reinterpret_cast<LPCVOID>(ebx + 0x3C0), ::std::addressof(dx), 4,
                                        ::std::addressof(nbr));
                    ::ReadProcessMemory(process, reinterpret_cast<LPCVOID>(ebx + 0x3C4), ::std::addressof(dy), 4,
                                        ::std::addressof(nbr));
                    ::ReadProcessMemory(process, reinterpret_cast<LPCVOID>(ebx + 0x3B4), ::std::addressof(x), 4,
                                        ::std::addressof(nbr));
                    ::ReadProcessMemory(process, reinterpret_cast<LPCVOID>(ebx + 0x3B8), ::std::addressof(y), 4,
                                        ::std::addressof(nbr));
                    ::ReadProcessMemory(process, reinterpret_cast<LPCVOID>(ebx + 0x3F0), ::std::addressof(w), 4,
                                        ::std::addressof(nbr));
                    ::ReadProcessMemory(process, reinterpret_cast<LPCVOID>(ebx + 0x3F4), ::std::addressof(h), 4,
                                        ::std::addressof(nbr));
                    res.push_back({{x, y}, w, h, dx, dy});
                }
            }
        }
        ebx += 0x7F0;
    }
    return res;
}

inline auto get_enemy_lasers(HANDLE process) noexcept {
    ::std::uint32_t base{};
    ::std::size_t nbr;
    ::ReadProcessMemory(process, LPCVOID(0x0047781C), &base, 4, &nbr);
    if (base == 0) [[unlikely]] {
        ::auto_th10::details::game_not_start();
    }

    ::std::uint32_t esi{}, ebx{};
    ::ReadProcessMemory(process, reinterpret_cast<LPCVOID>(base + 0x18), &esi, 4, &nbr);

    ::fast_io::vector<::auto_th10::EnemyLaser> res{};
    if (esi) {
        while (true) {
            ::ReadProcessMemory(process, reinterpret_cast<LPCVOID>(esi + 0x8), &ebx, 4, &nbr);
            ::auto_th10::float32_type x, y, h, w, radian;
            ::ReadProcessMemory(process, reinterpret_cast<LPCVOID>(esi + 0x24), ::std::addressof(x), 4,
                                ::std::addressof(nbr));
            ::ReadProcessMemory(process, reinterpret_cast<LPCVOID>(esi + 0x28), ::std::addressof(y), 4,
                                ::std::addressof(nbr));
            ::ReadProcessMemory(process, reinterpret_cast<LPCVOID>(esi + 0x3C), ::std::addressof(radian), 4,
                                ::std::addressof(nbr));
            ::ReadProcessMemory(process, reinterpret_cast<LPCVOID>(esi + 0x40), ::std::addressof(h), 4,
                                ::std::addressof(nbr));
            ::ReadProcessMemory(process, reinterpret_cast<LPCVOID>(esi + 0x44), ::std::addressof(w), 4,
                                ::std::addressof(nbr));
            res.push_back({{x, y}, w, h, radian});
            if (ebx == 0) {
                break;
            }
            esi = ebx;
        }
    }
    return res;
}

inline auto get_resources(HANDLE process) noexcept {
    ::std::size_t nbr;
    ::std::uint32_t base;
    ::ReadProcessMemory(process, LPCVOID(0x00477818), &base, 4, &nbr);
    if (base == 0) [[unlikely]] {
        ::auto_th10::details::game_not_start();
    }
    ::std::uint32_t esi = base + 0x14;
    ::std::uint32_t ebp = esi + 0x3B0;
    ::fast_io::vector<::auto_th10::Resource> res{};

    for (int i = 0; i < 2000; i++) {
        ::std::uint32_t eax;
        ::ReadProcessMemory(process, reinterpret_cast<LPCVOID>(ebp + 0x2C), &eax, 4, ::std::addressof(nbr));
        if (eax != 0) {
            ::auto_th10::float32_type x, y;
            ::ReadProcessMemory(process, reinterpret_cast<LPCVOID>(ebp - 0x4), &x, 4, ::std::addressof(nbr));
            ::ReadProcessMemory(process, reinterpret_cast<LPCVOID>(ebp), &y, 4, ::std::addressof(nbr));
            res.push_back({x, y});
        }
        ebp += 0x3F0;
    }
    return res;
}

inline auto get_score(HANDLE process) noexcept {
    ::std::uint32_t score;
    ::ReadProcessMemory(process, reinterpret_cast<LPCVOID>(0x00474C44), &score, 4, nullptr);
    return score;
}

inline auto get_power(HANDLE process) noexcept {
    ::std::uint16_t power;
    ::ReadProcessMemory(process, reinterpret_cast<LPCVOID>(0x00474C48), &power, sizeof(power), nullptr);
    return power;
}

/**
 * @brief Get Player's HP
 * @returns 0: game not start or the minimal hp
 * @returns -1: game over
 */
inline auto get_hp(HANDLE process) noexcept {
    ::std::int16_t hp;
    ::ReadProcessMemory(process, reinterpret_cast<LPCVOID>(0x00474C70), &hp, sizeof(hp), nullptr);
    return hp;
}

#ifdef _MSC_VER
    #pragma warning(default : 4312)
#endif

} // namespace auto_th10
