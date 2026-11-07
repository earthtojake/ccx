"""Resolve the conda-forge files a CalculiX wheel is made of, per platform, and write ``pins.json``.

CalculiX (``calculix``) at :data:`CALCULIX`, its runtime dependencies, and the reference (netlib) BLAS and LAPACK
in place of OpenBLAS: CalculiX's sparse solver (SPOOLES) brings its own kernels, so a static solve barely touches
BLAS, and OpenBLAS would add its own threads and ship twice (``libblas`` and ``liblapack`` are one OpenBLAS file
under two names, and a wheel holds no symlinks). Each dependency takes the newest file that satisfies every spec
asking for it. It reads conda-forge's own index, the sharded repodata (CEP 16): the dependency specs as conda-forge
patched them after upload, which is what conda resolves against. Only that metadata is read; nothing is installed
or run. Each file's url, sha256 and size are what the wheel build checks. Needs ``msgpack``.

    python scripts/pins.py            # rewrite pins.json

Beside each platform's packages it records the files a wheel ships (``scripts/closure.py``: ``ccx`` and the libraries
it loads, read from the binaries' own headers) and the wheel's platform tag, so building a wheel needs nothing but
the standard library. Needs ``msgpack``, and ``pyelftools``, ``macholib`` and ``pefile`` for the closure.
"""

from __future__ import annotations

import functools
import json
import re
import sys
import urllib.request
from pathlib import Path

CALCULIX = "2.23"
PLATFORMS = ("linux-64", "linux-aarch64", "osx-64", "osx-arm64", "win-64")
#: Variants chosen where a spec leaves a choice: the reference BLAS and LAPACK; LLVM's OpenMP on macOS, GNU's elsewhere.
VARIANTS = {"libblas": "netlib", "liblapack": "netlib", "libcblas": "netlib"}
CHANNEL = "https://conda.anaconda.org/conda-forge"
ROOT = Path(__file__).resolve().parents[1]


def _fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def _unpack(data: bytes):
    import msgpack
    from compression import zstd

    return msgpack.unpackb(zstd.decompress(data), raw=False)


@functools.cache
def _index(subdir: str) -> dict:
    return _unpack(_fetch(f"{CHANNEL}/{subdir}/repodata_shards.msgpack.zst"))


@functools.cache
def files_of(name: str, subdir: str) -> list[dict]:
    """Every ``.conda`` file of ``name`` in ``subdir``, as conda-forge's index records it (patched)."""
    index = _index(subdir)
    digest = index["shards"].get(name)
    if digest is None:
        return []
    shard = _unpack(_fetch(f"{CHANNEL}/{subdir}/{digest.hex()}.msgpack.zst"))
    return [{**record, "fn": fn, "subdir": subdir, "sha256": record["sha256"].hex() if isinstance(record["sha256"], bytes)
             else record["sha256"]} for fn, record in shard.get("packages.conda", {}).items()]


def _key(version: str) -> tuple:
    """A conda version, ordered: numbers as numbers, a pre-release tag (``a0``) before the release."""
    parts = []
    for piece in re.split(r"[._-]", version.lower()):
        for token in re.findall(r"\d+|[a-z]+", piece):
            parts.append((1, int(token), "") if token.isdigit() else (0, 0, token))
    return tuple(parts)


def _one(version: str, spec: str) -> bool:
    spec = spec.strip()
    if not spec or spec == "*":
        return True
    match = re.match(r"^(>=|<=|==|!=|>|<|=)?(.+)$", spec)
    op, target = match.group(1) or "==", match.group(2)
    if target.endswith(".*") or op == "=":
        prefix = target[:-2] if target.endswith(".*") else target
        return version == prefix or version.startswith(prefix + ".")
    if op in ("==",) and "*" in target:
        return re.fullmatch(target.replace(".", r"\.").replace("*", ".*"), version) is not None
    a, b = _key(version), _key(target)
    return {">=": a >= b, "<=": a <= b, ">": a > b, "<": a < b, "==": a == b, "!=": a != b}[op]


def satisfies(record: dict, spec: str) -> bool:
    """Whether ``record`` (an index record) meets a dependency spec ``name [version [build]]``."""
    parts = spec.split()
    version_spec = parts[1] if len(parts) > 1 else ""
    build_spec = parts[2] if len(parts) > 2 else ""
    version = record["version"]
    if version_spec and not any(all(_one(version, s) for s in alt.split(",")) for alt in version_spec.split("|")):
        return False
    if build_spec:
        build = record["build"]
        if re.fullmatch(build_spec.replace("*", ".*"), build) is None:
            return False
    return True


def candidates(name: str, platform: str) -> list[dict]:
    found = files_of(name, platform) + files_of(name, "noarch")
    variant = VARIANTS.get(name)
    if variant:
        found = [f for f in found if variant in f["build"]]
    if name == "_openmp_mutex":
        wanted = "llvm" if platform.startswith("osx") else "gnu"
        found = [f for f in found if wanted in f["build"]] or found
    return sorted(found, key=lambda f: (_key(f["version"]), f.get("build_number", 0), f.get("timestamp", 0)), reverse=True)


def resolve(platform: str) -> list[dict]:
    """Every file a CalculiX install on ``platform`` needs: for each package, the newest file meeting every spec the
    current picks put on it, repeated until the picks stop changing."""
    seeds = {"calculix": [f"calculix {CALCULIX}"]}
    chosen: dict[str, dict] = {}
    for _ in range(50):
        specs = {name: list(given) for name, given in seeds.items()}
        for record in chosen.values():
            for dependency in record.get("depends", []):
                specs.setdefault(dependency.split()[0], []).append(dependency)
        picks = {}
        for name, wanted in specs.items():
            if name.startswith("__"):
                continue
            pick = next((f for f in candidates(name, platform) if all(satisfies(f, s) for s in wanted)), None)
            if pick is None:
                raise SystemExit(f"{platform}: nothing satisfies {wanted}")
            picks[name] = pick
        if {n: r["fn"] for n, r in picks.items()} == {n: r["fn"] for n, r in chosen.items()}:
            return [chosen[name] for name in sorted(chosen)]
        chosen = picks
    raise SystemExit(f"{platform}: the picks did not settle")


ARCH = {"linux-64": "x86_64", "linux-aarch64": "aarch64", "osx-64": "x86_64", "osx-arm64": "arm64"}


def wheel_tag(platform: str, closure: dict) -> str:
    """The platform tag of a wheel holding ``closure``: the oldest glibc or macOS its binaries need."""
    if platform == "win-64":
        return "win_amd64"
    if platform.startswith("osx"):
        major, minor = closure["macos"].split(".")[:2]
        return f"macosx_{major}_{minor}_{ARCH[platform]}"
    major, minor = closure["glibc"].split(".")[:2]
    tag = f"manylinux_{major}_{minor}_{ARCH[platform]}"
    return f"{tag}.manylinux2014_{ARCH[platform]}" if (major, minor) == ("2", "17") else tag


def shipped_as(platform: str, name: str, closure: dict) -> str:
    """Where a file goes in the wheel's ``ccx/_bin``: beside ``ccx.exe`` on Windows, where it loads DLLs from; else
    ``bin/ccx`` and ``lib/<name>``, which its ``$ORIGIN/../lib`` or ``@loader_path/../lib`` finds."""
    if platform == "win-64" or name == closure["binary"]:
        return f"bin/{name}"
    return f"lib/{name}"


def main() -> None:
    import importlib.util
    import tempfile

    spec = importlib.util.spec_from_file_location("hatch_build", ROOT / "hatch_build.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    sys.path.insert(0, str(ROOT / "scripts"))
    from closure import closure as closure_of

    platforms = {}
    for platform in PLATFORMS:
        records = resolve(platform)
        packages = [{"name": r["name"], "file": r["fn"], "url": f"{CHANNEL}/{r['subdir']}/{r['fn']}",
                     "sha256": r["sha256"], "size": r["size"]} for r in records]
        with tempfile.TemporaryDirectory() as folder:
            for pin in packages:
                build.unpack(build.download(pin, platform), Path(folder))
            found = closure_of(Path(folder), platform)
        files = {shipped_as(platform, name, found): source for name, source in found["files"].items()}
        platforms[platform] = {"tag": wheel_tag(platform, found), "packages": packages, "files": dict(sorted(files.items())),
                               "system": found["system"]}
        print(f"{platform}: {platforms[platform]['tag']}, {len(files)} files from {len(packages)} packages", file=sys.stderr)
    (ROOT / "pins.json").write_text(json.dumps({"calculix": CALCULIX, "platforms": platforms}, indent=2) + "\n")


if __name__ == "__main__":
    main()
