# AGENTS.md — Developer Guide for `auto-th10`

This file is the entry point for AI coding agents. It contains the development
workflow, build/format/test commands, and coding conventions. Human-facing
project documentation lives in [README.md](./README.md); the design notes for the
agent layer live in [training/README.md](./training/README.md).

## Mandatory rules

- **Do NOT run git write operations without explicit human instruction.** An agent must not run `git add`,
  `git commit`, `git push`, open a **Pull Request**, open an **Issue**, or perform any other write operation to the
  repository or remote unless the human explicitly asks for it. When a change is ready, leave it in the working tree
  and report what changed.
- **After changing code, build and run the tests** (commands below). Do not declare a change done on inspection alone.
- **Keep the work focused.** Touch only what the request needs. Do not reformat or reorganize anything beyond the lines
  your change touches (see [Formatting](#formatting)).
- **Stop and report when the request appears misguided.** If the agent believes the user's prompt is based on a false
  premise, points in the wrong direction, or would lead to an incorrect change, it must stop, explain the problem with
  concrete evidence (file paths, code excerpts, command output), and propose the corrected direction — rather than
  silently complying or silently "fixing" the intent. Do not use this rule to avoid difficult tasks: when the direction
  is sound and only the approach is unclear, proceed or ask a focused question instead.
- **Never invent memory addresses or offsets.** Every address in `src/internal.h` and every offset in `src/snapshot.c`
  was verified against a running `th10.exe` 1.00a. New ones have to be verified the same way and their comment has to
  say how, or how they were found (see [C conventions](#c-conventions)).

## Project layout

Windows-only project: a C core that reads and drives a running Touhou 10 (Mountain of Faith) process through Win32, a
CPython extension over it, and an agent/training layer in Python.

| Path | Purpose |
|------|---------|
| `include/auto_th10/auto_th10.h` | The public C API — the only header a consumer includes |
| `src/` | Win32 implementation; `internal.h` holds the session struct and the verified game addresses |
| `python/auto_th10/_native.c` | CPython extension module (`_native`) — the only file that touches the CPython API |
| `python/auto_th10/` | Python layer: `session.py`, `types.py`, `env.py` (observations and the episode lifecycle), `restart.py` (the key sequences out of a menu), re-exported by `__init__.py` |
| `tools/th10ctl.c` | `th10ctl`, the command line driver over the same C API — see [README.md](./README.md#manual-testing) |
| `docs/game-ui.md` | The game's menus and endings, screen by screen — read it before driving the UI |
| `tests/c/test_c_api.c` | C test (ctest target `c_api`) |
| `tests/python/` | `unittest` suites, with the shared fakes in `fakes.py` |
| `training/` | Agent layer: `policy.py`, `loop.py`, the `evaluate`/`collect` entry points (`train.py` is still a stub), and the design notes in `README.md` |
| `CMakeLists.txt`, `CMakePresets.json` | Build description and `dev`/`release` presets |
| `pixi.toml` | Toolchain and task definitions |

The game executable, its data files, saves, and localization patches are not part of this repository and must not be
added to it (`README.md`). Tests never need the game to be running; only manual verification does.

## Development environment

The toolchain comes from [Pixi](https://pixi.sh/) — do not install a compiler, CMake, or Python separately:

- MinGW-w64 GCC (`gcc_win-64`, GCC 16 at the time of writing)
- CMake and Ninja
- Python 3.14 with `pip` and `scikit-build-core`

`CMakeLists.txt` fails on any non-Windows host, and no other platform is supported. `clang-format` is not part of the
Pixi environment either; see [Formatting](#formatting) for the optional formatting step.

## Workflow

1. **Locate** – Read [README.md](./README.md) for the layout, [training/README.md](./training/README.md) for the agent
   layer, and `src/internal.h` when the change involves a memory address. Know which layer the change belongs to: Win32
   work goes in `src/`, brand-new C behavior is exposed through `include/auto_th10/auto_th10.h`, and anything that is
   just plumbing belongs in `python/auto_th10/`.
2. **Code** – Follow [Coding conventions](#coding-conventions) below, and match the style of the file you edit.
3. **Test** – Run `pixi run test-c` for C changes and `pixi run test-python` for Python changes (`pixi run test` runs
   both, and is slower because it also produces a release build). Build first: `pixi run build`.
4. **Review** – After a substantive code change, ask a subagent to perform the
   [independent read-only review](#independent-read-only-review) when subagents are available. Validate its findings,
   fix confirmed issues, and rerun the affected checks.
5. **Submit** – Do NOT run any git write operation or open a PR/Issue. Leave the change in the working tree and report
   what was changed and how it was verified.

## Commit messages

Commits follow the shape `<type>(<scope>): <subject>`, with a body explaining *why* the change is needed, and optional
trailers. The reference template is [`.gitmessage`](./.gitmessage).

Used in this repository:

- Types: `feat`, `fix`, `refactor`, `chore`. Add others only when they clearly fit better.
- Scopes: the area touched — `state`, `snapshot`, `memory`, `input`, `capture`, `cli`, `python`.
- Trailers: `Assisted-by: <model name> [<version>]` when material was produced by a model, `Closes #123` for issues.

A commit body in this repository explains the problem, why the obvious alternative does not work, and how the change
was verified. Write it that way, but remember the mandatory rule above: never run a git write operation unless the
human asked for it.

## Independent read-only review

After completing and testing a substantive code change, the implementing agent must ask a subagent to independently
review the change when subagents are available. Substantive changes include behavior changes, memory reading and
address/offset work, public C API changes, the CPython binding, input injection, and non-trivial refactoring.
Documentation-only, comment-only, and obviously mechanical changes do not require a subagent review.

The reviewing subagent must:

- Act only as a read-only reviewer: do not edit files or run git write operations.
- Review the original request, applicable repository instructions, and the exact files or diff changed by the
  implementing agent. Do not attribute unrelated user changes in the working tree to the implementing agent.
- Independently look for correctness defects, regressions, missing edge cases, and inadequate tests rather than
  assuming the implementation or its rationale is correct. In this project, pay particular attention to struct offsets
  and strides, pointer lifetimes vs. `th10_snapshot_clear`/`th10_snapshot_destroy`, handle leaks across the
  `th10_open`/`th10_close` paths, and error propagation from the C API into the Python exceptions.
- Report only actionable findings, each with severity, location, reasoning, and a triggering example when practical. If
  no issues are found, explicitly say so and summarize the areas examined.

The implementing agent remains responsible for the final result. It must validate each finding against the code, fix
confirmed issues rather than applying suggestions mechanically, and rerun the affected checks after any fix. Normally
one review pass is sufficient; request another only when fixes address high-severity findings or materially change the
design. A subagent review supplements, but does not replace, the required automated checks.

## Quick commands (run from repository root)

### Build and test

```powershell
pixi run build            # configure (--fresh) + debug build into build/dev
pixi run test-c           # build, then ctest --preset dev
pixi run test-python      # release build, editable install, then unittest discover
pixi run test             # test-c and test-python
```

Release and Python installation on their own:

```powershell
pixi run build-release    # configure-release + release build into build/release
pixi run install-python   # build-release, then pip install --no-build-isolation -e .
```

Notes:

- Python imports the extension installed by `install-python`, which is a release build. After a C change, run
  `pixi run test-python` — it rebuilds in release mode and reinstalls, which is what makes the change visible to the
  Python layer. `pixi run build` alone (debug) does not.
- `pixi run ctl ...` runs `build/dev/th10ctl.exe`, so run `pixi run build` first.
- The CMake options are `-DAUTO_TH10_BUILD_PYTHON`, `-DAUTO_TH10_BUILD_TESTS`, `-DAUTO_TH10_BUILD_TOOLS` (all `ON` by
  default). The Pixi tasks always use the full set, so for a narrower build run the CMake steps yourself:

  ```powershell
  cmake --preset dev --fresh -DAUTO_TH10_BUILD_PYTHON=OFF
  cmake --build --preset dev
  ctest --preset dev
  ```

### Formatting

Formatting is **not** a required step: match the style of the file you are editing and leave the rest of it alone.
`.clang-format` at the repository root is the style definition; its `PointerAlignment` is `Right`, matching the
`th10_session *session` form used throughout the tree.

If you do want clang-format, run it through the `git-clang-format` script, which formats only the lines that differ
from a commit (default `HEAD`) and leaves the rest of each file untouched:

```powershell
git-clang-format -f             # rewrite the lines that differ from HEAD
git-clang-format -f --diff      # print that change instead of writing it
```

- Never run `clang-format` over a whole file or over the tree. The sources still differ from what the configuration
  asks for in other ways — `SeparateDefinitionBlocks`, `IndentCaseLabels: false`, hand-wrapped parameter lists and
  aligned trailing comments — so a whole-file run is hundreds of lines of unrelated churn.
- Without `-f`/`--force` the script refuses a file with unstaged changes ("Please commit, stage, or stash them
  first"): it is built to format what is already staged. Staging is a repository write an agent must not perform on
  its own (see [Mandatory rules](#mandatory-rules)), so pass `-f`.
- It skips untracked files (a brand-new file has no diff to format) and extensions outside clang-format's default
  list, and exits `1` when it changed a file and `0` when it did not.
- If `git clang-format` prints nothing and exits `49` on this machine, call `git-clang-format` directly: an
  extension-less copy of the script shadows the `.bat` wrapper on `PATH` and git cannot execute it.

### Manual testing against a running game

The game has to be running, and it has to be the legally obtained one
([README.md § Game Files](./README.md#game-files)). `th10ctl` drives it through the same C API the Python layer uses:

```powershell
.\build\dev\th10ctl.exe snapshot            # attach, print one snapshot, detach
.\build\dev\th10ctl.exe -j snapshot         # the same, as JSON on stdout
.\build\dev\th10ctl.exe state               # menu / playing / paused / game over
.\build\dev\th10ctl.exe record              # did the run set a new high score
.\build\dev\th10ctl.exe frames              # the stage frame counter: moves while playing
.\build\dev\th10ctl.exe shot shot.bmp       # capture the window, works in the background
.\build\dev\th10ctl.exe hold "shoot focus" 500
.\build\dev\th10ctl.exe windows             # candidate windows when attach fails
```

Keys only reach a DirectInput game while its window owns the focus, and a Chinese IME on the game's thread swallows
`Z`/`X` — `th10_focus` (used by `hold` and `watch`) handles both. Results go to stdout, failures to stderr.

## Coding conventions

General:

- Keep changes focused and minimal.
- Follow the existing style and structure of the module you touch.
- Preserve current behavior unless the change intentionally updates it.
- Add or update tests when behavior changes. Extend the existing suites rather than adding a new framework.

### C conventions

C17 throughout (`c_std_17` in `CMakeLists.txt`). `th10_core` and `th10ctl` are compiled with `-Wall -Wextra -Wpedantic`
(MinGW-w64) or `/W4 /utf-8` (MSVC); the extension module adds `/U_DEBUG` and the release CRT on MSVC. Keep the build
warning-free.

- **Public API naming and shape** – public symbols carry the `th10_` prefix, types are `th10_something`, enum members
  are `TH10_SOMETHING`, internal helpers are `static` and unprefixed. `include/auto_th10/auto_th10.h` is a C header
  that is also consumed by C++: keep the `extern "C"` guard and include guards (`AUTO_TH10_..._H`).
- **Errors travel in tagged results, not in globals.** A fallible entry point returns a `th10_*_result` struct with a
  `tag` enum and a `value` union carrying the details (see `th10_open_result`, `th10_input_result`,
  `th10_snapshot_result`). Add a new tag member instead of overloading an existing one, and put input validation
  failures in the tag list too (`TH10_*_INVALID_ARGUMENT`, `TH10_*_INVALID_SESSION`).
- **Win32 detail stays in the result or is dropped.** When a Win32 call fails, capture `GetLastError()` immediately
  into the result *before* any other call can clobber it (see `th10_open` in `src/session.c`).
- **`bool` return plus an optional out-parameter is the internal read idiom.** `th10_read_memory` reports success and
  takes an optional `th10_read_failure *`; callers that only branch pass `NULL`. Reuse it instead of calling
  `ReadProcessMemory` directly.
- **Addresses and offsets live in one place.** Game addresses are `static const uintptr_t` in `src/internal.h`;
  structure offsets that are relative to a pointer the snapshot already resolved stay in the file that reads them
  (`src/snapshot.c`). Every one of them keeps a comment saying what it holds and how it was verified — including the
  traps (see `TH10_SCENE_ADDRESS` and the note on `0x00474C40`).
- **Comments explain why, not what.** Use `/* ... */` blocks, wrap at the file's column limit, and continue with a
  leading ` * `. Long explanations of a decision belong on the declaration or next to the code that depends on it.
- **The public header is documented in Doxygen style.** `include/auto_th10/auto_th10.h` is the only header a consumer
  sees, so its comments carry markup as well as prose: `/** ... */` blocks with `@brief`, plus `@param`, `@return` and
  `@note` where they apply, and `/**< ... */` on enumeration members, struct fields and union members. The design
  rationale that was already there stays in the block body — the markup is added around it, not instead of it. There
  is deliberately no `Doxyfile`: nothing renders these comments today, they are read by people and by agents.
- **Internal code keeps the plain narrative style.** `src/`, `tools/`, `tests/` and the internal headers stay on
  `/* ... */` blocks written for the maintainer; do not sprinkle Doxygen markup there.
- **Declare variables at the top of the block** and initialize them there, as the existing functions do.
- **Do not grow the public API for a single caller.** A helper that only one module needs stays `static` there; the
  public header carries what both `th10ctl` and the Python binding use.
- **`python/auto_th10/_native.c` includes only `auto_th10/auto_th10.h`.** It must not include `src/internal.h`: the
  binding is a consumer of the public API, and reaching into the internals would make the two-layer split pointless.
- **Release memory on every path.** The snapshot arrays are owned by the caller of `th10_snapshot_init` /
  `th10_snapshot_clear` / `th10_snapshot_destroy`; the extension keeps one snapshot object alive per session. New
  allocations in `_native.c` need matching cleanup in `tp_dealloc` and in every error branch.

### Python conventions

The Python floor is 3.10 (`pyproject.toml`), even though Pixi installs 3.14. Do not use anything newer than 3.10 —
`StrEnum`, `typing.Self`, and `except*` are examples that would silently raise the floor.

- Start each module with `from __future__ import annotations` and annotate fully, including `-> None` return types.
- Value types are `@dataclass(frozen=True, slots=True)` (`python/auto_th10/types.py`). Keep them immutable.
- Keep the layer thin and declarative: Win32 behavior belongs in C, and `session.py` should stay a named wrapper over
  `_native`. Anything that drives the game (the environment, the key sequences in `restart.py`) stays in
  `python/auto_th10/`; anything that decides or records (a policy, a loop, an entry point) belongs in `training/`.
- Keep the agent on a short leash, because every mistake here ends the same way — an agent typing into a menu. A policy
  decides from an `Observation` and nothing else; the step loop never calls the blocking `state()` (about 120 ms, it
  samples the frame counter twice); and `Th10Env` raises `NotInStage` rather than inject a key for a menu, the pause
  menu, an unknown screen, or an ending it is not allowed to leave. The one ending it does clear is one that was
  already on screen before the first episode, since that is left over from an earlier attempt.
- Text-producing enums that mirror C constants derive from `str` rather than `StrEnum`, so a member compares equal to
  the raw name the C side returns while still working on 3.10 (`State` in `session.py`).
- Export public names explicitly through `__all__` in `python/auto_th10/__init__.py`.
- Use `unittest`, not a third-party test framework: suites live in `tests/python/test_*.py` and are run with
  `python -m unittest discover -s tests/python` (which is what `pixi run test-python` does).
- There is no configured Python formatter or linter (no ruff/black/mypy configuration). Match the surrounding modules:
  4 spaces, PEP 8 names, module and function docstrings that state the reason for the design rather than restating the
  code.

### Tests

- `tests/c/test_c_api.c` exercises the API's contract without the game: invalid-argument and invalid-session paths,
  and the snapshot array lifecycle (`init` → `clear` keeps storage → `destroy` releases it). Extend it in the same
  style; it must keep passing with no game running.
- `tests/python/` covers the Python layer's own logic (enum values, native-dictionary conversion) and likewise needs no
  game. Do not write tests that attach to the game — verify those by hand with `th10ctl` and say so in the change
  report.
