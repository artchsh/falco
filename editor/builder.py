"""Turn a :class:`LauncherConfig` into standalone executables by configuring
prebuilt Rust stubs.

"Building" a launcher does not compile anything. We copy a prebuilt
``falco-stub`` binary and append the config (including encrypted key material in key mode) as a trailer the stub
reads from its own file at runtime:

    [ ...stub binary... ][ config JSON (utf-8) ][ u64 LE length ][ b"FALCOCFG" ]

Because appending bytes is OS-agnostic, one editor can emit launchers for *every*
platform whose stub it bundles — Windows, macOS and Linux — from a single run.
No toolchain is needed, so even a frozen editor produces all three offline. The
SSH password is never embedded; only the non-secret config fields are.
"""

from __future__ import annotations

import os
import re
import tempfile
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from editor.templates import render_how_to_use
from shared.config import LauncherConfig
from shared.errors import FalcoError

# Callback that receives human-readable build progress lines.
ProgressFn = Callable[[str], None]

# Must byte-match launcher-rs `config::MAGIC`.
MAGIC = b"FALCOCFG"

_REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class StubTarget:
    """A platform the editor can emit a launcher for."""

    key: str  # "windows" | "macos" | "linux"
    bundled_name: str  # stub file name inside the bundled ``stubs/`` dir
    output_ext: str  # extension appended to the produced launcher


# Order is the order launchers are produced/reported in.
TARGETS: tuple[StubTarget, ...] = (
    StubTarget("windows", "falco-stub-windows.exe", ".exe"),
    StubTarget("macos", "falco-stub-macos", ""),
    StubTarget("linux", "falco-stub-linux", ""),
)
_TARGET_BY_KEY = {t.key: t for t in TARGETS}


@dataclass(frozen=True)
class BuildResult:
    executable: Path
    work_dir: Path
    how_to_use: Path


@dataclass(frozen=True)
class MultiBuildResult:
    executables: list[Path]
    how_to_use: Path
    skipped: list[str]  # os keys with no bundled stub available


def _emit(progress: ProgressFn | None, message: str) -> None:
    """Send a progress line to the caller, if it asked for one."""

    if progress is not None:
        progress(message)


def _strip_exe_suffix(name: str) -> str:
    for suffix in (".exe", ".app", ".bin"):
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return name


def _current_os_key() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def discover_stubs() -> dict[str, Path]:
    """Return ``{os_key: stub_path}`` for every launcher stub available.

    A frozen/packaged editor bundles a ``stubs/`` directory (populated by CI with
    one stub per OS), which is what enables cross-platform output. A plain dev
    checkout only has the current OS's stub from a local ``cargo build``.
    """

    found: dict[str, Path] = {}

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        stubs_dir = Path(meipass) / "stubs"
        for target in TARGETS:
            candidate = stubs_dir / target.bundled_name
            if candidate.exists():
                found[target.key] = candidate
    if found:
        return found

    # Dev fallback: only the current OS's stub, from a local cargo release build.
    cur = _current_os_key()
    exe_name = "falco-stub.exe" if cur == "windows" else "falco-stub"
    dev = _REPO_ROOT / "launcher-rs" / "target" / "release" / exe_name
    if dev.exists():
        found[cur] = dev
    return found


def stub_path_for_current_os() -> Path:
    """Locate the bundled ``falco-stub`` for the OS the editor is running on."""

    stubs = discover_stubs()
    cur = _current_os_key()
    if cur in stubs:
        return stubs[cur]
    raise FalcoError(
        "Could not find the falco-stub binary for this OS. Build it with "
        "`cd launcher-rs && cargo build --release`, or ensure it is bundled "
        "with the editor."
    )


def append_config(stub_bytes: bytes, config: LauncherConfig) -> bytes:
    """Append the config trailer (json + u64 LE length + magic) to a stub."""

    json_bytes = config.to_json(indent=None).encode("utf-8")
    length = struct.pack("<Q", len(json_bytes))
    return stub_bytes + json_bytes + length + MAGIC


def validate_output_name(output_name: str) -> str:
    """Return a portable basename, excluding its packaging extension."""
    if not isinstance(output_name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", output_name) or output_name.endswith("."):
        raise FalcoError("Output filename must use 1–120 letters, digits, dots, underscores or hyphens, start with a letter/digit, and contain no path.")
    name = _strip_exe_suffix(output_name)
    if _strip_exe_suffix(name) != name:
        raise FalcoError("Use at most one optional .exe, .app or .bin output suffix; repeated or mixed suffixes are not supported.")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if not name or name.endswith(".") or name.lower() == "how-to-use.md" or name.split(".")[0].upper() in reserved:
        raise FalcoError("Choose an output filename that is not a Windows reserved device name or empty basename.")
    return name


def _stub_bytes(stub: Path) -> bytes:
    data = stub.read_bytes()
    if b"FALCO_SCHEMA_2" not in data:
        raise FalcoError("The launcher stub is outdated and cannot use this configuration. Rebuild the stub with the current Rust source or download the latest editor.")
    return data


def _assemble(config: LauncherConfig, output_dir: str | Path, outputs: list[tuple[Path, str, str]], progress: ProgressFn | None) -> list[Path]:
    # Validate even callers that directly constructed the dataclass.
    config = LauncherConfig.from_dict(config.to_dict())
    try:
        stubs = [(append_config(_stub_bytes(stub), config), name, platform) for stub, name, platform in outputs]
        dist = Path(output_dir).resolve()
        sources = {stub.resolve() for stub, _, _ in outputs}
        if any((dist / name).resolve() in sources for _, name, _ in outputs):
            raise FalcoError("Output files must not replace source launcher stubs. Choose a different folder or filename.")
        dist.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        with tempfile.TemporaryDirectory(prefix=".falco-build-", dir=dist) as scratch:
            stage = Path(scratch)
            names = [name for _, name, _ in stubs]
            for blob, name, platform in stubs:
                path = stage / name
                path.write_bytes(blob)
                if platform != "windows":
                    path.chmod(0o755)
            (stage / "how-to-use.md").write_text(render_how_to_use(config, config.launcher_name, filenames=names, platforms={name: platform for _, name, platform in stubs}), encoding="utf-8")
            for name in [*names, "how-to-use.md"]:
                path = stage / name
                # Windows FlushFileBuffers requires a writable handle.
                with path.open("r+b") as stream:
                    os.fsync(stream.fileno())
                os.replace(path, dist / name)
                written.append(dist / name)
                _emit(progress, f"Wrote {name}")
        return written
    except OSError as exc:
        replaced = ", ".join(p.name for p in locals().get("written", []))
        partial = f" Already replaced: {replaced}." if replaced else " Existing output files were preserved."
        raise FalcoError(f"Could not write launcher output: {exc}.{partial} Check folder permissions and free disk space, then rebuild.") from exc


def _resolve_output_path(dist_dir: Path, output_name: str) -> Path:
    name = validate_output_name(output_name)
    return dist_dir / (f"{name}.exe" if _current_os_key() == "windows" else name)


def build_launcher(
    config: LauncherConfig,
    *,
    output_name: str,
    icon_path: str | Path | None = None,
    output_dir: str | Path,
    progress: ProgressFn | None = None,
) -> BuildResult:
    """Build the current OS's launcher; stage every file before replacement."""
    validate_output_name(output_name)
    if icon_path:
        raise FalcoError("Custom executable icons are not supported by prebuilt launcher stubs.")
    _emit(progress, "Locating launcher stub…")
    stub = stub_path_for_current_os()
    exe = _resolve_output_path(Path(output_dir).resolve(), output_name)
    paths = _assemble(config, output_dir, [(stub, exe.name, _current_os_key())], progress)
    return BuildResult(executable=paths[0], work_dir=exe.parent, how_to_use=paths[-1])


def build_all_launchers(
    config: LauncherConfig,
    *,
    base_name: str,
    output_dir: str | Path,
    progress: ProgressFn | None = None,
) -> MultiBuildResult:
    base = validate_output_name(base_name)
    stubs = discover_stubs()
    if not stubs:
        raise FalcoError("No launcher stubs are available. Build with `cd launcher-rs && cargo build --release` or use a packaged editor.")
    skipped = [target.key for target in TARGETS if target.key not in stubs]
    outputs = [(stubs[t.key], f"{base}-{t.key}{t.output_ext}", t.key) for t in TARGETS if t.key in stubs]
    for key in skipped:
        _emit(progress, f"Skipping {key}: no bundled stub.")
    paths = _assemble(config, output_dir, outputs, progress)
    return MultiBuildResult(executables=paths[:-1], how_to_use=paths[-1], skipped=skipped)
