# ccx

CalculiX's finite-element solver, `ccx`, packaged as a Python wheel, with a small launcher.

```bash
pip install ccx
ccx -v                      # This is Version 2.23
```

```python
import ccx

ccx.executable()            # the bundled ccx program
done = ccx.run("beam.inp")  # runs it in beam.inp's folder, on one thread; raises ccx.CcxError on failure
print(done.dat.read_text()) # what it printed to beam.dat
```

**Unofficial.** This is not the CalculiX project and is not affiliated with it. CalculiX is by Guido Dhondt and
Klaus Wittig: <https://www.calculix.de>. The `ccx` program here is conda-forge's build of the CalculiX source,
unmodified.

## What a wheel holds

One platform's `ccx` and the libraries it loads, read from the binary's own headers and nothing more:

| Platform | Wheel tag | Files |
| --- | --- | --- |
| Linux x86_64 | `manylinux_2_17_x86_64` | `ccx`, reference BLAS and LAPACK, ARPACK, GCC's Fortran, OpenMP and support runtimes |
| Linux aarch64 | `manylinux_2_17_aarch64` | the same |
| macOS Intel | `macosx_11_0_x86_64` | the same, with LLVM's OpenMP |
| macOS Apple silicon | `macosx_11_0_arm64` | the same, with LLVM's OpenMP |
| Windows x64 | `win_amd64` | the same as DLLs beside `ccx.exe`, with winpthreads and Microsoft's C runtime |

BLAS and LAPACK are the reference (netlib) builds rather than OpenBLAS: CalculiX's sparse solver, SPOOLES, carries
its own kernels, so a static solve barely uses BLAS, and the reference builds add no threads of their own.
Each wheel's `ccx/_bin/manifest.json` names every conda package it came from, with its URL and sha256.

## The launcher

| | |
| --- | --- |
| `ccx.executable()` | The bundled program's path; `FileNotFoundError` in an install without one |
| `ccx.version()` | The CalculiX release bundled, `"2.23"` |
| `ccx.run(inp, *, threads=1, timeout=None, env=None, check=True)` | Runs `ccx -i <job>` in the input's folder with every CalculiX thread variable set to `threads`; returns a `ccx.Run` (`returncode`, `stdout`, `stderr`, `errors`: the `*ERROR` lines, `ok`, `dat`, `frd`, `output(suffix)`); raises `ccx.CcxError` when it fails, unless `check=False` |
| `ccx` (the command), `python -m ccx` | The bundled program with your arguments |

The launcher reads and writes no CalculiX file: building a deck and reading its results are the caller's.

## Building

A wheel is a repackaging, not a compile. `hatch_build.py` downloads the platform's pinned conda-forge packages
(`pins.json`: URL and sha256 of each, checked), unpacks the files `pins.json` lists for it, and tags the wheel.
Any machine builds any platform's wheel:

```bash
CCX_PLATFORM=win-64 python -m build --wheel    # linux-64, linux-aarch64, osx-64, osx-arm64, win-64
```

Building from the sdist does the same, so it needs network access to conda-forge. Downloads are cached in
`CCX_CACHE` (default: the user's cache folder).

`scripts/pins.py` regenerates `pins.json`: it resolves CalculiX's dependencies against conda-forge's own index,
with the reference BLAS and LAPACK, and records each platform's files (`scripts/closure.py`) and wheel tag. Bump
`CALCULIX` there and `version` in `pyproject.toml` and `src/ccx/__init__.py` together.

CI (`.github/workflows/wheels.yml`) builds every wheel and the sdist, tests each wheel on its own platform (the
launcher, and a cantilever solved against beam theory), and publishes to PyPI from a `v*` tag through trusted
publishing.

## Licences

The launcher and build scripts are MIT (`LICENSE`). A wheel also distributes:

| Component | Licence | Text |
| --- | --- | --- |
| CalculiX `ccx` | GPL-2.0 | `LICENSES/CalculiX.txt` |
| Reference BLAS and LAPACK | BSD-3-Clause | `LICENSES/LAPACK.txt` |
| ARPACK-NG | BSD-3-Clause | `LICENSES/ARPACK-NG.txt` |
| GCC's libgfortran, libquadmath, libgomp, libgcc_s | GPL-3.0 with the GCC Runtime Library Exception 3.1 | `LICENSES/GCC-runtime.txt` |
| LLVM's OpenMP (macOS) | Apache-2.0 with LLVM exception | `LICENSES/LLVM.txt` |
| mingw-w64 winpthreads (Windows) | MIT | `LICENSES/mingw-w64-winpthreads.txt` |
| Microsoft Visual C++ runtime (Windows) | Microsoft's redistribution terms | `LICENSES/Microsoft-Visual-C-Runtime.txt` |

Source for every binary: the CalculiX source at <https://www.calculix.de> and the conda-forge recipes and
packages each wheel's `manifest.json` names (<https://github.com/conda-forge>).
