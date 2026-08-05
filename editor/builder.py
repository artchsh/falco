"""Turn a :class:`LauncherConfig` into a standalone executable by configuring a
prebuilt Rust stub.

"Building" a launcher no longer compiles anything. We copy the bundled
``falco-stub`` binary for the current OS and append the (non-secret) config as a
trailer that the stub reads from its own file at runtime:

    [ ...stub binary... ][ config JSON (utf-8) ][ u64 LE length ][ b"FALCOCFG" ]

This needs no toolchain, so even a frozen editor can produce launchers offline.
The SSH password is never embedded — only the non-secret config fields are.
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
class BuildResult:
    executable: Path
    work_dir: Path
    how_to_use: Path


def _emit(progress: ProgressFn | None, message: str) -> None:
    """Send a progress line to the caller, if it asked for one."""

    if progress is not None:
        progress(message)


def _strip_exe_suffix(name: str) -> str:
    for suffix in (".exe", ".app", ".bin"):
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return name


def stub_path_for_current_os() -> Path:
    """Locate the bundled ``falco-stub`` for the OS the editor is running on."""

    exe_name = "falco-stub.exe" if sys.platform == "win32" else "falco-stub"

    # Frozen editor: PyInstaller unpacks bundled data under sys._MEIPASS.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = Path(meipass) / "stub" / exe_name
        if candidate.exists():
            return candidate

    # Dev: use the cargo release build.
    candidate = _REPO_ROOT / "launcher-rs" / "target" / "release" / exe_name
    if candidate.exists():
        return candidate

    raise FalcoError(
        "Could not find the falco-stub binary. Build it with "
        "`cd launcher-rs && cargo build --release`, or ensure it is bundled "
        "with the editor."
    )


def append_config(stub_bytes: bytes, config: LauncherConfig) -> bytes:
    """Append the config trailer (json + u64 LE length + magic) to a stub."""

    json_bytes = config.to_json(indent=None).encode("utf-8")
    length = struct.pack("<Q", len(json_bytes))
    return stub_bytes + json_bytes + length + MAGIC


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
    """Generate a launcher executable for ``config`` by configuring the stub.

    Parameters
    ----------
    output_name:
        The desired file name, e.g. ``meks.exe``.
    icon_path:
        Accepted for backward compatibility with the previous PyInstaller-based
        builder. The stub already carries its icon; this argument is ignored.
    output_dir:
        Where the finished executable is placed.
    progress:
        Optional callback invoked with each build progress line, used by the GUI.
    """

    _emit(progress, "Locating prebuilt launcher stub…")
    stub = stub_path_for_current_os()
    _emit(progress, f"Using stub: {stub}")

    dist_dir = Path(output_dir).resolve()
    dist_dir.mkdir(parents=True, exist_ok=True)
    work_dir = dist_dir / f".falco-build-{_strip_exe_suffix(output_name)}"
    work_dir.mkdir(parents=True, exist_ok=True)

    stub_bytes = stub.read_bytes()
    _emit(progress, "Embedding launcher configuration (no password embedded)…")
    blob = append_config(stub_bytes, config)

    exe = _resolve_output_path(dist_dir, output_name)
    exe.write_bytes(blob)
    if sys.platform != "win32":
        exe.chmod(0o755)
    _emit(progress, f"Wrote launcher: {exe}")

    # Write a small usage guide (for humans and AI agents) next to the binary.
    how_to_use = dist_dir / "how-to-use.md"
    how_to_use.write_text(render_how_to_use(config, output_name), encoding="utf-8")
    _emit(progress, f"Wrote usage guide: {how_to_use}")

    return BuildResult(executable=exe, work_dir=work_dir, how_to_use=how_to_use)
