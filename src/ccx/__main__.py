"""``ccx`` (and ``python -m ccx``): the bundled ``ccx`` program, run with these arguments, its exit code ours."""

from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    from ccx import executable

    try:
        program = str(executable())
    except FileNotFoundError as error:
        print(f"ccx: {error}", file=sys.stderr)
        return 127
    arguments = [program, *sys.argv[1:]]
    if sys.platform != "win32":
        os.execv(program, arguments)  # the process becomes ccx: its signals, stdio and exit code
    return subprocess.call(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
