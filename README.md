# auto-th10

An experimental project for reading and controlling *Touhou Shin Giant Youma* (東方風神録) via Win32 API. The project consists of a pure C core, a Python C API extension module, and subsequent data collection and model training code.

## Directory Structure

```text
include/auto_th10/  Public C API
src/                Win32 process, memory, and input implementation
python/auto_th10/   Python extension and higher-level interface
training/           Data collection, training, and evaluation entry points
tests/              C and Python tests
```

## Building

Requires Windows and [Pixi](https://pixi.sh/). Pixi provides the compiler,
CMake, Ninja, and Python; the default toolchain is MinGW-w64 GCC.

Build and test:

```powershell
pixi run build
pixi run test
```

Pixi installs the Python package as an editable PyPI path dependency. Python
source changes are visible immediately, while importing the package rebuilds
the native extension when its C sources changed. To produce the standalone
release build separately:

```powershell
pixi run build-release
```

## Manual Testing

`th10ctl` is a small command line driver that talks to a running game through
the same C API, without going through the Python extension. It is built by
default (`-DAUTO_TH10_BUILD_TOOLS=OFF` skips it).

One-shot commands attach, act, and detach:

```powershell
.\build\dev\th10ctl.exe snapshot              # attach, print one snapshot, detach
.\build\dev\th10ctl.exe -j snapshot           # the same snapshot as JSON on stdout
.\build\dev\th10ctl.exe -v snapshot           # list every enemy, bullet, and laser
.\build\dev\th10ctl.exe shot shot.bmp         # capture the game window into a BMP file
.\build\dev\th10ctl.exe hold "shoot focus" 500  # press for 500 ms, then release
.\build\dev\th10ctl.exe windows               # list candidate windows when attach fails
.\build\dev\th10ctl.exe launch <path to>\th10.exe
```

`shot` goes through `PrintWindow`, so it reads the game window itself instead of
the screen: it works while the game is in the background or covered over, and
needs neither the focus nor the foreground. The output is an uncompressed
32-bit BMP (about 1.2 MB at 640x480), because that is what the Win32 blit path
produces and the binding carries no image encoder.

`hold` runs the press, the wait, and the release inside a single process. A
DirectInput game polls its input every frame, so keystrokes whose duration
depends on how quickly the next command arrives are not reliable.

Holding an action down is therefore one command:

```powershell
.\build\dev\th10ctl.exe hold "shoot focus" 500   # press both for 500 ms, then release
.\build\dev\th10ctl.exe --input-backend foreground hold left 300  # compatibility path
.\build\dev\th10ctl.exe watch 100 20            # 20 snapshots, 100 ms apart
```

`hold` uses the opt-in background-input bridge. It does not foreground the game
or send keys to the desktop: the bridge replaces the action word after the game
has combined DirectInput and joystick state, before the original held/pressed/
released and movement logic runs. Each update carries a 120-frame lease, so a
controller that disappears falls back to physical input in about two seconds;
normal close restores the original instructions immediately. Installation first
checks the exact instruction bytes verified against `th10.exe` 1.00a and refuses
another build instead of guessing.

The optional `--input-backend foreground` compatibility path gives the game
focus and sends virtual keys to the desktop. It can therefore interfere with
other keyboard use and is never selected as an automatic fallback when the
background bridge rejects an executable.

No command ever waits for input, so the tool is safe to call from a script:
without a command it prints its usage, and `watch` reads 10 snapshots by default
and only runs until interrupted when given an explicit `0`.

Errors go to stderr and results to stdout, so `-j` output can be piped into
other tools.

## Game Files

The game executable, data files, saves, and localization patches are not part of this repository and should not be committed to Git. Users must legally obtain the game and launch it before calling `Session`. For downloading Touhou series games (e.g., TH10), you can visit https://cloud.lilywhite.cc/.

The current memory layout uses the TH10 reverse-engineering results from the original project.

## Python Example

```python
from auto_th10 import Action, Session

with Session(background_input=True) as game:
    game.set_input(Action.SHOOT | Action.FOCUS)
    snapshot = game.snapshot()
    print(snapshot.player, len(snapshot.enemy_bullets))
```

Thanks to [TH10AI](https://github.com/Infinideastudio/TH10AI) for the reverse-engineering research.
