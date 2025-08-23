#pragma once

#include <utility>
#include "float32.hh"

namespace auto_th10 {

class ThObject {
public:
    ::auto_th10::float32_type x;
    ::auto_th10::float32_type y;

    constexpr auto&& get_x(this auto&& self) noexcept {
        return ::std::forward_like<decltype(self)>(self.x);
    }

    constexpr auto&& get_y(this auto&& self) noexcept {
        return ::std::forward_like<decltype(self)>(self.y);
    }

    constexpr auto&& get_width(this auto&& self) noexcept
        requires requires { self.width; }
    {
        return ::std::forward_like<decltype(self)>(self.width);
    }

    constexpr auto&& get_height(this auto&& self) noexcept
        requires requires { self.height; }
    {
        return ::std::forward_like<decltype(self)>(self.height);
    }
};

class Player : public ::auto_th10::ThObject {
public:
    static constexpr ::auto_th10::float32_type width{4};
    static constexpr ::auto_th10::float32_type height{4};
};

class Enemy : public ::auto_th10::ThObject {
public:
    ::auto_th10::float32_type width;
    ::auto_th10::float32_type height;
};

class EnemyBullet : public ::auto_th10::ThObject {
public:
    // dx, dy : bullet's derection is `dy / dx`
    ::auto_th10::float32_type width;
    ::auto_th10::float32_type height;
    ::auto_th10::float32_type dx;
    ::auto_th10::float32_type dy;

    constexpr ::auto_th10::float32_type get_direction(this ::auto_th10::EnemyBullet const& self) noexcept {
        return self.dy / self.dx;
    }
};

class EnemyLaser : public ::auto_th10::ThObject {
public:
    ::auto_th10::float32_type width;
    ::auto_th10::float32_type height;
    ::auto_th10::float32_type radian;

    constexpr auto&& get_radian(this auto&& self) noexcept {
        return self.radian;
    }
};

class Resource : public ::auto_th10::ThObject {
public:
    static constexpr ::auto_th10::float32_type width{6};
    static constexpr ::auto_th10::float32_type height{6};
};

} // namespace auto_th10
