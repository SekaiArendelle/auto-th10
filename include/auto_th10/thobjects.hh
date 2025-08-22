#pragma once

#include <utility>
#include "float32.hh"

namespace auto_th10 {

class ThObject {
public:
    ::auto_th10::float32_type x_;
    ::auto_th10::float32_type y_;

    constexpr ThObject(float32_type x, float32_type y) noexcept
        : x_{x},
          y_{y} {
    }

    constexpr ThObject(::auto_th10::ThObject const&) noexcept = default;
    constexpr ThObject(::auto_th10::ThObject&&) noexcept = default;
    constexpr ~ThObject() noexcept = default;
    constexpr ::auto_th10::ThObject& operator=(::auto_th10::ThObject const&) noexcept = default;
    constexpr ::auto_th10::ThObject& operator=(::auto_th10::ThObject&&) noexcept = default;

    constexpr auto&& get_x(this auto&& self) noexcept {
        return ::std::forward_like<decltype(self)>(self.x_);
    }

    constexpr auto&& get_y(this auto&& self) noexcept {
        return ::std::forward_like<decltype(self)>(self.y_);
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

    constexpr Player(float32_type x, float32_type y) noexcept
        : ::auto_th10::ThObject{x, y} {
    }

    constexpr Player(Player const&) noexcept = default;
    constexpr Player(Player&&) noexcept = default;
    constexpr ~Player() noexcept = default;
    constexpr Player& operator=(Player const&) noexcept = default;
    constexpr Player& operator=(Player&&) noexcept = default;
};

class Enemy : public ::auto_th10::ThObject {
public:
    ::auto_th10::float32_type width;
    ::auto_th10::float32_type height;

    constexpr Enemy(float32_type x, float32_type y, float32_type width, float32_type height) noexcept
        : ::auto_th10::ThObject{x, y},
          width{width},
          height{height} {
    }

    constexpr Enemy(Enemy const&) noexcept = default;
    constexpr Enemy(Enemy&&) noexcept = default;
    constexpr ~Enemy() noexcept = default;
    constexpr Enemy& operator=(Enemy const&) noexcept = default;
    constexpr Enemy& operator=(Enemy&&) noexcept = default;
};

class EnemyBullet : public ::auto_th10::ThObject {
    // dx, dy : bullet's derection is `dy / dx`
    ::auto_th10::float32_type width;
    ::auto_th10::float32_type height;
    ::auto_th10::float32_type dx;
    ::auto_th10::float32_type dy;

public:
    constexpr EnemyBullet(::auto_th10::float32_type x, ::auto_th10::float32_type y, ::auto_th10::float32_type width,
                          ::auto_th10::float32_type height, ::auto_th10::float32_type dx,
                          ::auto_th10::float32_type dy) noexcept
        : ::auto_th10::ThObject{x, y},
          width{width},
          height{height},
          dx{dx},
          dy{dy} {
    }

    constexpr EnemyBullet(::auto_th10::EnemyBullet const&) noexcept = default;
    constexpr EnemyBullet(::auto_th10::EnemyBullet&&) noexcept = default;
    constexpr ~EnemyBullet() noexcept = default;
    constexpr ::auto_th10::EnemyBullet& operator=(::auto_th10::EnemyBullet const&) noexcept = default;
    constexpr ::auto_th10::EnemyBullet& operator=(::auto_th10::EnemyBullet&&) noexcept = default;

    constexpr ::auto_th10::float32_type get_direction(this ::auto_th10::EnemyBullet const& self) noexcept {
        return self.dy / self.dx;
    }
};

class EnemyLaser : public ::auto_th10::ThObject {
public:
    ::auto_th10::float32_type width;
    ::auto_th10::float32_type height;
    ::auto_th10::float32_type radian;

    constexpr EnemyLaser(float32_type x, float32_type y, float32_type width, float32_type height,
                         float32_type radian) noexcept
        : ::auto_th10::ThObject{x, y},
          width{width},
          height{height},
          radian{radian} {
    }

    constexpr EnemyLaser(::auto_th10::EnemyLaser const&) noexcept = default;
    constexpr EnemyLaser(::auto_th10::EnemyLaser&&) noexcept = default;
    constexpr ~EnemyLaser() noexcept = default;
    constexpr ::auto_th10::EnemyLaser& operator=(::auto_th10::EnemyLaser const&) noexcept = default;
    constexpr ::auto_th10::EnemyLaser& operator=(::auto_th10::EnemyLaser&&) noexcept = default;

    constexpr auto&& get_radian(this auto&& self) noexcept {
        return self.radian;
    }
};

class Resource : public ::auto_th10::ThObject {
public:
    static constexpr ::auto_th10::float32_type width{6};
    static constexpr ::auto_th10::float32_type height{6};

    constexpr Resource(::auto_th10::float32_type x, ::auto_th10::float32_type y) noexcept
        : ::auto_th10::ThObject(x, y) {
    }

    constexpr Resource(::auto_th10::Resource const&) noexcept = default;
    constexpr Resource(::auto_th10::Resource&&) noexcept = default;
    constexpr ~Resource() noexcept = default;
    constexpr Resource& operator=(::auto_th10::Resource const&) noexcept = default;
    constexpr Resource& operator=(::auto_th10::Resource&&) noexcept = default;
};

} // namespace auto_th10
