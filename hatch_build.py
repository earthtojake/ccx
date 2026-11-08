"""Build a ``ccx`` wheel for one platform from conda-forge's CalculiX, as ``pins.json`` names it.

A wheel is a repackaging, not a compile: the hook downloads the platform's pinned conda packages (each checked
against its sha256), unpacks only the files ``pins.json`` lists for it -- ``ccx`` and the libraries it loads, under
the names the loader asks for -- into ``cad_ccx/_bin``, and tags the wheel for that platform. Any machine can build any
platform's wheel. The platform is ``CCX_PLATFORM`` (``linux-64``, ``linux-aarch64``, ``osx-64``, ``osx-arm64``,
``win-64``), else this machine's. Downloads are kept in ``CCX_CACHE`` (default: the user's cache folder).

Standard library only, beside hatchling: ``scripts/pins.py`` uses the same download and unpack to write
``pins.json``. Unpacking a ``.conda`` needs zstandard, in the standard library from Python 3.14
(``compression.zstd``), else the ``zstandard`` package.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform as _platform
import shutil
import stat
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

ROOT = Path(__file__).resolve().parent
PINS = ROOT / "pins.json"


def host_platform() -> str:
    """This machine's conda platform."""
    machine = _platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    if sys.platform == "darwin":
        return "osx-arm64" if arm else "osx-64"
    if sys.platform == "win32":
        return "win-64"
    return "linux-aarch64" if arm else "linux-64"


def cache_folder() -> Path:
    given = os.environ.get("CCX_CACHE")
    if given:
        return Path(given)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "ccx-build"


def download(pin: dict, platform: str) -> bytes:
    """One pinned package's bytes, from the cache or conda-forge, refused unless its sha256 matches."""
    path = cache_folder() / platform / pin["file"]
    if path.is_file():
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() == pin["sha256"]:
            return data
    with urllib.request.urlopen(pin["url"], timeout=300) as response:
        data = response.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != pin["sha256"]:
        raise RuntimeError(f"{pin['url']}: sha256 {digest}, pinned {pin['sha256']}: refusing it")
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    partial.write_bytes(data)
    partial.replace(path)
    return data


def _zstd_decompress(data: bytes) -> bytes:
    try:
        from compression import zstd  # Python 3.14
    except ImportError:
        import zstandard

        return zstandard.ZstdDecompressor().decompress(data, max_output_size=1 << 31)
    return zstd.decompress(data)


def unpack(data: bytes, into: Path) -> None:
    """A ``.conda`` package's files (its ``pkg-*.tar.zst``) into ``into``, links as links."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        payload = next(name for name in archive.namelist() if name.startswith("pkg-") and name.endswith(".tar.zst"))
        with tarfile.open(fileobj=io.BytesIO(_zstd_decompress(archive.read(payload)))) as tar:
            tar.extractall(into, filter="tar")


def stage(platform: str, into: Path) -> dict:
    """``ccx`` and its libraries for ``platform``, unpacked from the pinned packages and laid out under ``into`` as
    the wheel ships them (``bin/``, and ``lib/`` but on Windows), with a ``manifest.json`` naming every package and
    file. The platform's record from ``pins.json``."""
    pins = json.loads(PINS.read_text())
    record = pins["platforms"].get(platform)
    if record is None:
        raise RuntimeError(f"no pins for {platform}: pins.json has {', '.join(pins['platforms'])}")
    unpacked = into.parent / f"{into.name}-unpacked"
    shutil.rmtree(unpacked, ignore_errors=True)
    shutil.rmtree(into, ignore_errors=True)
    for pin in record["packages"]:
        unpack(download(pin, platform), unpacked)
    for shipped, source in record["files"].items():
        target = into / shipped
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile((unpacked / source).resolve(), target)  # a link's target, under the name the loader asks for
        target.chmod(target.stat().st_mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP
                     | stat.S_IROTH | stat.S_IXOTH)
    (into / "manifest.json").write_text(json.dumps({
        "calculix": pins["calculix"], "platform": platform, "tag": record["tag"],
        "packages": [{key: pin[key] for key in ("file", "url", "sha256")} for pin in record["packages"]],
        "files": record["files"],
    }, indent=2) + "\n")
    shutil.rmtree(unpacked, ignore_errors=True)
    return record


class CustomBuildHook(BuildHookInterface):
    """Put one platform's ``ccx`` into the wheel and tag the wheel for that platform."""

    def initialize(self, version: str, build_data: dict) -> None:
        if self.target_name != "wheel":
            return
        platform = os.environ.get("CCX_PLATFORM") or host_platform()
        staged = Path(self.directory) / f".ccx-{platform}"
        record = stage(platform, staged)
        build_data["force_include"][str(staged)] = "cad_ccx/_bin"
        build_data["tag"] = f"py3-none-{record['tag']}"
        build_data["pure_python"] = False
        self.app.display_info(f"ccx: CalculiX {json.loads(PINS.read_text())['calculix']} for {platform} ({record['tag']})")

    def finalize(self, version: str, build_data: dict, artifact_path: str) -> None:
        if self.target_name == "wheel":
            for staged in Path(self.directory).glob(".ccx-*"):
                shutil.rmtree(staged, ignore_errors=True)
