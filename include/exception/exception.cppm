module;

#include "exception.hh"

/**
 * @file exception.cppm
 * @details https://github.com/GoodenoughPhysicsLab/exception.git
 * @note experimental support for C++20 module
 *       requires clang22+
 *       MSVC has bug with deducing this
 *       gcc has bug with std::construct_at
 */

export module exception;

export namespace exception {

using ::exception::assert_false;
using ::exception::assert_true;
using ::exception::expected;
using ::exception::is_expected;
using ::exception::is_optional;
using ::exception::is_unexpected;
using ::exception::nullopt_t;
using ::exception::optional;
using ::exception::terminate;
using ::exception::unexpected;
using ::exception::unreachable;

} // namespace exception
