"""The files ccx needs, per platform: ccx itself and the libraries it loads, transitively, from the conda packages.

Reads each binary's own headers (ELF DT_NEEDED, Mach-O LC_LOAD_DYLIB, PE imports) and follows them through the
unpacked packages; anything the operating system provides is left out. Prints, per platform, each file to ship
under the name the loader asks for, and what the binaries require of the system.

    python scripts/closure.py EXTRACTED_ROOT   # EXTRACTED_ROOT/<platform>/ holds that platform's unpacked packages
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# What each operating system provides, which a wheel never ships (manylinux's allowed set; macOS's and Windows' own).
LINUX_SYSTEM = re.compile(r"^(libc|libm|libpthread|librt|libdl|libutil|libresolv|ld-linux.*|ld64|libgcc_s)\.so")
WINDOWS_SYSTEM = re.compile(r"^(kernel32|user32|advapi32|ws2_32|msvcrt|ntdll|shell32|ole32|bcrypt|ucrtbase|api-ms-win-.*)\.dll$", re.I)
LINUX_SYSTEM_KEEP = {"libgcc_s.so.1"}  # manylinux allows it, but conda's newer one is what its gfortran was linked against


def _elf_needed(path: Path) -> list[str]:
    from elftools.elf.dynamic import DynamicSection
    from elftools.elf.elffile import ELFFile

    with open(path, "rb") as handle:
        elf = ELFFile(handle)
        return [tag.needed for section in elf.iter_sections() if isinstance(section, DynamicSection)
                for tag in section.iter_tags() if tag.entry.d_tag == "DT_NEEDED"]


def _glibc(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    found = [tuple(map(int, m.groups())) for m in re.finditer(rb"GLIBC_(\d+)\.(\d+)", data)]
    return max(found) if found else (2, 17)


def _macho_needed(path: Path) -> list[str]:
    from macholib.MachO import MachO

    names = []
    for header in MachO(str(path)).headers:
        for _cmd, _data, name in [(c[0], c[1], c[2]) for c in header.commands if c[0].get_cmd_name() in ("LC_LOAD_DYLIB", "LC_LOAD_WEAK_DYLIB", "LC_REEXPORT_DYLIB")]:
            names.append(name.decode().rstrip("\x00"))
    return names


def _macho_minos(path: Path) -> str:
    from macholib.MachO import MachO

    for header in MachO(str(path)).headers:
        for command in header.commands:
            if command[0].get_cmd_name() == "LC_BUILD_VERSION":
                v = command[1].minos
                return f"{v >> 16}.{(v >> 8) & 0xff}"
            if command[0].get_cmd_name() == "LC_VERSION_MIN_MACOSX":
                v = command[1].version
                return f"{v >> 16}.{(v >> 8) & 0xff}"
    return "11.0"


def _pe_needed(path: Path) -> list[str]:
    import pefile

    pe = pefile.PE(str(path), fast_load=True)
    pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
    return [entry.dll.decode() for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", [])]


def closure(root: Path, platform: str) -> dict:
    """``{"files": {ship_name: source_relative_path}, "binary": ..., "system": [...], "min": ...}`` for one platform."""
    if platform.startswith("win"):
        binary = root / "Library" / "bin" / "ccx.exe"
        search = [root / "Library" / "bin", root / "Library" / "mingw-w64" / "bin", root]
        needed_of, system = _pe_needed, lambda name: WINDOWS_SYSTEM.match(name) is not None
    elif platform.startswith("osx"):
        binary = root / "bin" / "ccx"
        search = [root / "lib"]
        needed_of = lambda path: [n.replace("@rpath/", "") for n in _macho_needed(path)]
        system = lambda name: name.startswith(("/usr/lib/", "/System/"))
    else:
        binary = root / "bin" / "ccx"
        search = [root / "lib"]
        needed_of, system = _elf_needed, lambda name: LINUX_SYSTEM.match(name) is not None and name not in LINUX_SYSTEM_KEEP

    def find(name: str) -> Path:
        for folder in search:
            for candidate in (folder / name, *(p for p in folder.glob("*") if p.name.lower() == name.lower())):
                if candidate.exists():
                    return candidate
        raise FileNotFoundError(f"{platform}: {name} is in no package")

    files, system_deps, queue, seen = {}, set(), [binary], set()
    while queue:
        path = queue.pop()
        for name in needed_of(path):
            base = Path(name).name
            if system(name):
                system_deps.add(base)
                continue
            if base in seen:
                continue
            seen.add(base)
            found = find(base)
            files[base] = found.resolve().relative_to(root.resolve()).as_posix()
            queue.append(found.resolve())
    files[binary.name] = binary.resolve().relative_to(root.resolve()).as_posix()
    out = {"binary": binary.name, "files": dict(sorted(files.items())), "system": sorted(system_deps)}
    if platform.startswith("linux"):
        out["glibc"] = ".".join(map(str, max(_glibc(root / rel) for rel in files.values())))
    if platform.startswith("osx"):
        out["macos"] = max((_macho_minos(root / rel) for rel in files.values()), key=lambda v: tuple(map(int, v.split("."))))
    return out


if __name__ == "__main__":
    base = Path(sys.argv[1])
    report = {platform.name: closure(platform, platform.name) for platform in sorted(base.iterdir()) if platform.is_dir()}
    for platform, found in report.items():
        size = sum((base / platform / rel).resolve().stat().st_size for rel in found["files"].values())
        print(f"{platform}: {len(found['files'])} files, {size / 1e6:.1f} MB; system {found['system']}; "
              + ", ".join(f"{k} {v}" for k, v in found.items() if k in ("glibc", "macos")))
        for name, rel in found["files"].items():
            print(f"   {name:34} <- {rel}")
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(json.dumps(report, indent=2) + "\n")
