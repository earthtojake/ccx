"""The installed wheel: its program, its launcher, and a real solve checked against beam theory."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

import ccx

# A steel cantilever, 100 x 10 x 10 mm, clamped at x = 0, 100 N down at its tip, in 20 x 2 x 2 incompatible-mode
# bricks (C3D8I), which bend without locking. Beam theory: P L^3 / (3 E I) = 0.1905 mm, shear adding about 0.3%.
LENGTH, SIDE, FORCE, E, NU = 100.0, 10.0, 100.0, 210_000.0, 0.3
COLUMNS, ROWS = 20, 2


def _cantilever(folder: Path, name: str = "beam", boundary: str = "FIXED") -> Path:
    nodes, number = {}, 0
    lines = ["*NODE, NSET=NALL"]
    for i in range(COLUMNS + 1):
        for j in range(ROWS + 1):
            for k in range(ROWS + 1):
                number += 1
                nodes[i, j, k] = number
                lines.append(f"{number}, {LENGTH * i / COLUMNS}, {SIDE * j / ROWS}, {SIDE * k / ROWS}")
    lines.append("*ELEMENT, TYPE=C3D8I, ELSET=EALL")
    element = 0
    for i in range(COLUMNS):
        for j in range(ROWS):
            for k in range(ROWS):
                element += 1
                corners = [nodes[i, j, k], nodes[i + 1, j, k], nodes[i + 1, j + 1, k], nodes[i, j + 1, k],
                           nodes[i, j, k + 1], nodes[i + 1, j, k + 1], nodes[i + 1, j + 1, k + 1], nodes[i, j + 1, k + 1]]
                lines.append(f"{element}, " + ", ".join(map(str, corners)))
    fixed = [n for (i, _, _), n in nodes.items() if i == 0]
    tip = [n for (i, _, _), n in nodes.items() if i == COLUMNS]
    lines += ["*NSET, NSET=FIXED", ", ".join(map(str, fixed)), "*NSET, NSET=TIP", ", ".join(map(str, tip)),
              "*MATERIAL, NAME=STEEL", "*ELASTIC", f"{E}, {NU}", "*SOLID SECTION, ELSET=EALL, MATERIAL=STEEL",
              "*STEP", "*STATIC", "*BOUNDARY", f"{boundary}, 1, 3, 0.", "*CLOAD",
              *(f"{n}, 3, {-FORCE / len(tip)}" for n in tip), "*NODE PRINT, NSET=TIP", "U", "*END STEP"]
    path = folder / f"{name}.inp"
    path.write_text("\n".join(lines) + "\n")
    return path


def _tip_deflection(dat: Path) -> float:
    rows = [line.split() for line in dat.read_text().splitlines()]
    values = [float(row[3]) for row in rows if len(row) == 4 and row[0].isdigit()]
    return sum(values) / len(values)


def test_the_program_is_bundled_and_runs():
    path = ccx.executable()
    assert path.is_file() and os.access(path, os.X_OK)
    assert ccx.version() == ccx.CALCULIX_VERSION == "2.23"
    said = subprocess.run([str(path), "-v"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert "Version 2.23" in said.stdout


def test_the_manifest_names_every_shipped_file_and_its_packages():
    folder = ccx.executable().parent.parent
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["calculix"] == "2.23"
    for shipped in manifest["files"]:
        assert (folder / shipped).is_file(), shipped
    assert all(len(package["sha256"]) == 64 for package in manifest["packages"])


def test_a_cantilever_solves_and_matches_beam_theory(tmp_path):
    done = ccx.run(_cantilever(tmp_path))
    assert done.ok and done.errors == ()
    inertia = SIDE ** 4 / 12
    expected = FORCE * LENGTH ** 3 / (3 * E * inertia)
    assert _tip_deflection(done.dat) == pytest.approx(-expected, rel=0.03)


def test_the_same_deck_gives_the_same_numbers(tmp_path):
    printed = []
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        printed.append(ccx.run(_cantilever(tmp_path / name)).dat.read_text())
    assert printed[0] == printed[1]


def test_a_failure_says_why_and_raises_unless_asked_not_to(tmp_path):
    deck = _cantilever(tmp_path, boundary="NOSUCHSET")
    with pytest.raises(ccx.CcxError) as raised:
        ccx.run(deck)
    assert raised.value.run.errors and "NOSUCHSET" in str(raised.value).upper()
    done = ccx.run(deck, check=False)
    assert not done.ok and done.errors


def test_every_thread_variable_is_set(tmp_path):
    deck = _cantilever(tmp_path)
    finished = subprocess.CompletedProcess([], 0, "", "")
    with mock.patch("ccx.subprocess.run", return_value=finished) as called:
        ccx.run(deck, threads=3, env={"KEEP": "1"})
    environment = called.call_args.kwargs["env"]
    assert {name: environment[name] for name in ccx.THREAD_VARIABLES} == dict.fromkeys(ccx.THREAD_VARIABLES, "3")
    assert environment["KEEP"] == "1"
    assert called.call_args.kwargs["cwd"] == tmp_path
    assert called.call_args.args[0][1:] == ["-i", "beam"]


def test_an_input_that_is_not_there_is_refused(tmp_path):
    with pytest.raises(FileNotFoundError):
        ccx.run(tmp_path / "missing.inp")
    with pytest.raises(ValueError):
        ccx.run(_cantilever(tmp_path), threads=0)


def test_the_command_runs_the_bundled_program():
    said = subprocess.run([sys.executable, "-m", "ccx", "-v"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert "Version 2.23" in said.stdout
