"""Turn a :class:`LauncherConfig` into standalone executables by configuring
prebuilt Rust stubs.

"Building" a launcher does not compile anything. We copy a prebuilt
``falco-stub`` binary and append the (non-secret) config as a trailer the stub
reads from its own file at runtime:

    [ ...stub binary... ][ config JSON (utf-8) ][ u64 LE length ][ b"FALCOCFG" ]

Because appending bytes is OS-agnostic, one editor can emit launchers for *every*
platform whose stub it bundles — Windows, macOS and Linux — from a single run.
No toolchain is needed, so even a frozen editor produces all three offline. The
SSH password is never embedded; only the non-secret config fields are.
"""

from __future__ import annotations

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


def _write_launcher(stub_path: Path, config: LauncherConfig, out_path: Path) -> None:
    """Write ``stub + config`` to ``out_path`` and mark non-Windows outputs
    executable."""

    blob = append_config(stub_path.read_bytes(), config)
    out_path.write_bytes(blob)
    if out_path.suffix.lower() != ".exe":
        try:
            out_path.chmod(0o755)
        except OSError:
            # Best effort — irrelevant on filesystems that ignore Unix perms.
            pass


def _resolve_output_path(dist_dir: Path, output_name: str) -> Path:
    name = _strip_exe_suffix(output_name)
    if sys.platform == "win32":
        return dist_dir / f"{name}.exe"
    return dist_dir / name


def build_launcher(
    config: LauncherConfig,
    *,
    output_name: str,
    icon_path: str | Path | None = None,  # accepted for API compatibility; unused
    output_dir: str | Path,
    progress: ProgressFn | None = None,
) -> BuildResult:
    """Generate a launcher for the **current OS** by configuring its stub."""

    _emit(progress, "Locating prebuilt launcher stub…")
    stub = stub_path_for_current_os()
    _emit(progress, f"Using stub: {stub}")

    dist_dir = Path(output_dir).resolve()
    dist_dir.mkdir(parents=True, exist_ok=True)
    work_dir = dist_dir / f".falco-build-{_strip_exe_suffix(output_name)}"
    work_dir.mkdir(parents=True, exist_ok=True)

    exe = _resolve_output_path(dist_dir, output_name)
    _emit(progress, "Embedding launcher configuration (no password embedded)…")
    _write_launcher(stub, config, exe)
    _emit(progress, f"Wrote launcher: {exe}")

    how_to_use = dist_dir / "how-to-use.md"
    how_to_use.write_text(render_how_to_use(config, output_name), encoding="utf-8")
    _emit(progress, f"Wrote usage guide: {how_to_use}")

    return BuildResult(executable=exe, work_dir=work_dir, how_to_use=how_to_use)


def build_all_launchers(
    config: LauncherConfig,
    *,
    base_name: str,
    output_dir: str | Path,
    progress: ProgressFn | None = None,
) -> MultiBuildResult:
    """Generate one launcher per bundled stub (Windows / macOS / Linux).

    Output names are disambiguated per platform, e.g. ``meks-windows.exe``,
    ``meks-macos``, ``meks-linux``. Platforms whose stub is not bundled are
    skipped and reported (never silently dropped).
    """

    stubs = discover_stubs()
    if not stubs:
        raise FalcoError(
            "No launcher stubs are available to build from. Build them with "
            "`cd launcher-rs && cargo build --release` (current OS) or use a "
            "packaged editor that bundles all platforms."
        )

    dist_dir = Path(output_dir).resolve()
    dist_dir.mkdir(parents=True, exist_ok=True)
    base = _strip_exe_suffix(base_name)

    executables: list[Path] = []
    for target in TARGETS:
        if target.key not in stubs:
            _emit(progress, f"Skipping {target.key}: no bundled stub in this editor.")
            continue
        out = dist_dir / f"{base}-{target.key}{target.output_ext}"
        _emit(progress, f"Building {target.key} launcher: {out.name}")
        _write_launcher(stubs[target.key], config, out)
        _emit(progress, f"Wrote {out.name}")
        executables.append(out)

    how_to_use = dist_dir / "how-to-use.md"
    how_to_use.write_text(render_how_to_use(config, base), encoding="utf-8")
    _emit(progress, f"Wrote usage guide: {how_to_use}")

    skipped = [t.key for t in TARGETS if t.key not in stubs]
    return MultiBuildResult(
        executables=executables, how_to_use=how_to_use, skipped=skipped
    )
