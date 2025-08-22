#pragma once

// https://en.cppreference.com/w/cpp/types/floating-point
#if __has_include(<stdfloat>) && (__cplusplus >= 202302L || _MSVC_LANG >= 202302L)
    #include <stdfloat>
#endif

namespace auto_th10 {

#if __STDCPP_FLOAT32_T__
using float32_type = ::std::float32_t;
#else // ^^^ __STDCPP_FLOAT32_T__ / vvv !__STDCPP_FLOAT32_T__
static_assert(sizeof(float) == 4);
using float32_type = float;
#endif // !__STDCPP_FLOAT32_T__

} // namespace auto_th10
