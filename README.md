# auto-th10
Playing th10 game automatically by AI (auto-th10).

download th10 from [there](https://cloud.lilywhite.cc/s/4ZUW?path=%2F%E4%B8%9C%E6%96%B9Project%2F%E5%AE%98%E6%96%B9%E6%B8%B8%E6%88%8F).

other projects like this:
*  [TH10AI](https://github.com/Infinideastudio/TH10AI)
*  [twinject](https://github.com/Netdex/twinject)

## how to use
you can also type `python -m auto_th10 --help` to get help.

## api
* C++ api in `include/auto_th10/`
* Python wrapper for C++ api is in `py/`

## build
Requires at least `c++23` on windows

support: [GCC, llvm](https://github.com/24bit-xjkp/toolchains/releases)

```sh
cmake -S py -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release
cmake --install build --config Release
```
