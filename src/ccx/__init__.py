"""CalculiX's solver, ``ccx``, packaged as a wheel, with a small launcher.

This package ships the ``ccx`` program, CalculiX's finite-element solver, built by conda-forge and unmodified,
with the libraries it loads, for one platform per wheel. It is unofficial: not affiliated with the CalculiX project
(https://www.calculix.de). What it adds is a way to find and run that program from Python::

    import ccx

    ccx.executable()                      # Path to the bundled ccx program
    ccx.version()                         # "2.23", the CalculiX release bundled
    done = ccx.run("beam.inp")            # runs ccx on beam.inp in its own folder, on one thread
    done.dat.read_text()                  # what it printed to beam.dat

``run`` sets every thread count CalculiX reads to ``threads`` (default 1, so a solve prints the same numbers every
run), captures what ``ccx`` says, collects its ``*ERROR`` lines, and raises :class:`CcxError` when it fails
(``check=False`` returns the result instead). The ``ccx`` command this package installs runs the bundled program
with its arguments as given. Nothing here reads or writes CalculiX's file formats: that is the caller's.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Tuple, Union

__version__ = "2.23.0"
#: The CalculiX release this package bundles.
CALCULIX_VERSION = "2.23"
#: Every environment variable CalculiX takes a thread count from.
THREAD_VARIABLES = ("OMP_NUM_THREADS", "CCX_NPROC_EQUATION_SOLVER", "CCX_NPROC_STIFFNESS", "CCX_NPROC_RESULTS")

__all__ = ["CALCULIX_VERSION", "THREAD_VARIABLES", "CcxError", "Run", "executable", "run", "version"]

_BIN = Path(__file__).resolve().parent / "_bin"


def executable() -> Path:
    """The bundled ``ccx`` program. ``FileNotFoundError`` for an install that has none (a source checkout, or a
    wheel for another platform)."""
    path = _BIN / "bin" / ("ccx.exe" if sys.platform == "win32" else "ccx")
    if not path.is_file():
        raise FileNotFoundError(f"this install of the ccx package has no ccx program at {path}: install a wheel built "
                                "for this platform")
    return path


def version() -> str:
    """The CalculiX release bundled ("2.23"), without running anything."""
    return CALCULIX_VERSION


@dataclass(frozen=True)
class Run:
    """One run of ``ccx``: the job (its ``.inp`` without the suffix), its exit code, what it printed, and the
    ``*ERROR`` lines among that."""

    job: Path
    returncode: int
    stdout: str
    stderr: str
    errors: Tuple[str, ...]

    @property
    def ok(self) -> bool:
        """Whether it finished cleanly: exit code 0 and no ``*ERROR`` line."""
        return self.returncode == 0 and not self.errors

    def output(self, suffix: str) -> Optional[Path]:
        """The file it wrote with ``suffix`` (``".dat"``, ``".frd"``, ``".sta"``, ``".cvg"``), or None."""
        path = self.job.with_name(self.job.name + suffix)
        return path if path.is_file() else None

    @property
    def dat(self) -> Optional[Path]:
        return self.output(".dat")

    @property
    def frd(self) -> Optional[Path]:
        return self.output(".frd")


class CcxError(RuntimeError):
    """``ccx`` failed: it exited non-zero or printed an ``*ERROR`` line. ``run`` is the result."""

    def __init__(self, run: Run):
        said = "; ".join(run.errors[:3]) or "\n  ".join((run.stdout + run.stderr).strip().splitlines()[-8:])
        super().__init__(f"ccx failed on {run.job.name}.inp (exit {run.returncode}): {said}")
        self.run = run


def run(inp: Union[str, "os.PathLike[str]"], *, threads: int = 1, timeout: Optional[float] = None,
        env: Optional[Mapping[str, str]] = None, check: bool = True) -> Run:
    """Run the bundled ``ccx`` on ``inp`` (a ``.inp`` file, with or without its suffix), in the file's own folder,
    where ``ccx`` writes its output files beside it.

    threads: the thread count every CalculiX variable is set to (``THREAD_VARIABLES``); 1 gives the same numbers
      every run.
    timeout: seconds before the run is stopped (``subprocess.TimeoutExpired``); None waits.
    env: the environment it runs in, before the thread counts are set; None: this process's.
    check: raise :class:`CcxError` when it fails; False returns the failed :class:`Run`.
    """
    path = Path(inp).resolve()
    job = path.with_suffix("") if path.suffix.lower() == ".inp" else path
    if not job.with_name(job.name + ".inp").is_file():
        raise FileNotFoundError(f"no input file {job.with_name(job.name + '.inp')}")
    if threads < 1:
        raise ValueError(f"threads is at least 1, not {threads}")
    environment = dict(os.environ if env is None else env)
    environment.update(dict.fromkeys(THREAD_VARIABLES, str(threads)))
    done = subprocess.run([str(executable()), "-i", job.name], cwd=job.parent, env=environment, capture_output=True,
                          text=True, errors="replace", stdin=subprocess.DEVNULL, timeout=timeout)
    errors = tuple(line.strip() for line in (done.stdout + done.stderr).splitlines() if "*ERROR" in line)
    result = Run(job=job, returncode=done.returncode, stdout=done.stdout, stderr=done.stderr, errors=errors)
    if check and not result.ok:
        raise CcxError(result)
    return result
