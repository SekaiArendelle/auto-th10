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

Install the editable Python package:

```powershell
pixi run install-python
```

## Game Files

The game executable, data files, saves, and localization patches are not part of this repository and should not be committed to Git. Users must legally obtain the game and launch it before calling `Session`. For downloading Touhou series games (e.g., TH10), you can visit https://cloud.lilywhite.cc/.

The current memory layout uses the TH10 reverse-engineering results from the original project.

## Python Example

```python
from auto_th10 import Action, Session

with Session() as game:
    game.focus()
    game.set_input(Action.SHOOT | Action.FOCUS)
    snapshot = game.snapshot()
    print(snapshot.player, len(snapshot.enemy_bullets))
```

Thanks to [TH10AI](https://github.com/Infinideastudio/TH10AI) for the reverse-engineering research.
