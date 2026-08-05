"""Turn a :class:`LauncherConfig` into a standalone executable via PyInstaller.

The build is intentionally simple and reproducible:

1. Write the generated entry script into a temporary build directory.
2. Invoke PyInstaller in ``--onefile`` mode, naming the output after the
   requested filename and attaching the chosen icon.
3. Return the path to the produced executable.

The same code path runs on Windows, macOS and Linux; the OS it runs on
determines the kind of binary produced, which is why CI builds per-OS.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# Callback that receives human-readable build progress lines.
ProgressFn = Callable[[str], None]

from editor.templates import render_entry_script, render_how_to_use
from shared.config import LauncherConfig
from shared.errors import FalcoError

# Repo root == the directory that contains the launcher/ and shared/ packages.
_REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class BuildResult:
    executable: Path
    work_dir: Path
    how_to_use: Path


def _strip_exe_suffix(name: str) -> str:
    for suffix in (".exe", ".app", ".bin"):
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return name


def pyinstaller_command(
    *,
    entry_script: Path,
    output_name: str,
    dist_dir: Path,
    work_dir: Path,
    icon_path: Path | None,
    paths: list[Path],
) -> list[str]:
    """Build the PyInstaller argument list (kept pure for unit testing)."""

    name = _strip_exe_suffix(output_name)
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--noconfirm",
        "--clean",
        "--name",
        name,
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(work_dir / "build"),
        "--specpath",
        str(work_dir),
        # Make the launcher/shared packages importable during analysis.
        "--hidden-import",
        "launcher",
        "--hidden-import",
        "shared",
        "--collect-submodules",
        "paramiko",
        "--collect-submodules",
        "keyring",
    ]
    for p in paths:
        cmd += ["--paths", str(p)]
    if icon_path is not None:
        cmd += ["--icon", str(icon_path)]
    cmd.append(str(entry_script))
    return cmd


def _emit(progress: ProgressFn | None, message: str) -> None:
    """Send a progress line to the caller, if it asked for one."""

    if progress is not None:
        progress(message)


def build_launcher(
    config: LauncherConfig,
    *,
    output_name: str,
    icon_path: str | Path | None = None,
    output_dir: str | Path,
    progress: ProgressFn | None = None,
) -> BuildResult:
    """Generate and compile a launcher executable for ``config``.

    Parameters
    ----------
    output_name:
        The desired file name, e.g. ``meks.exe``.
    icon_path:
        Optional ``.ico`` (Windows) / ``.icns`` (macOS) icon. A ``.png`` also
        works when Pillow is installed.
    output_dir:
        Where the finished executable is placed.
    progress:
        Optional callback invoked with each build progress line (our own stage
        markers plus PyInstaller's live output). Used by the GUI to show what
        the builder is currently doing.
    """

    _emit(progress, "Checking that PyInstaller is available…")
    if shutil.which("pyinstaller") is None and not _pyinstaller_importable():
        raise FalcoError(
            "PyInstaller is not installed. Install it with:\n"
            "    pip install pyinstaller"
        )

    dist_dir = Path(output_dir).resolve()
    dist_dir.mkdir(parents=True, exist_ok=True)
    work_dir = dist_dir / f".falco-build-{_strip_exe_suffix(output_name)}"
    work_dir.mkdir(parents=True, exist_ok=True)
    _emit(progress, f"Preparing build directory: {work_dir}")

    entry_script = work_dir / "falco_entry.py"
    entry_script.write_text(render_entry_script(config), encoding="utf-8")
    _emit(progress, "Generated launcher entry script (no password embedded).")

    icon = Path(icon_path).resolve() if icon_path else None
    if icon is not None and not icon.exists():
        raise FalcoError(f"Icon file does not exist: {icon}")
    if icon is not None:
        _emit(progress, f"Using icon: {icon.name}")

    cmd = pyinstaller_command(
        entry_script=entry_script,
        output_name=output_name,
        dist_dir=dist_dir,
        work_dir=work_dir,
        icon_path=icon,
        paths=[_REPO_ROOT],
    )

    _emit(progress, "Starting PyInstaller (this can take a minute)…")
    tail = _run_streaming(cmd, progress=progress)
    if tail.returncode != 0:
        raise FalcoError(
            "PyInstaller build failed:\n"
            f"$ {' '.join(cmd)}\n\n" + "\n".join(tail.lines[-40:])
        )
    _emit(progress, "PyInstaller finished successfully.")

    exe = _resolve_output_path(dist_dir, output_name)
    if not exe.exists():
        raise FalcoError(
            f"Build reported success but no executable was found at {exe}."
        )

    # Write a small usage guide (for humans and AI agents) next to the binary.
    how_to_use = dist_dir / "how-to-use.md"
    how_to_use.write_text(render_how_to_use(config, output_name), encoding="utf-8")
    _emit(progress, f"Wrote usage guide: {how_to_use}")

    return BuildResult(executable=exe, work_dir=work_dir, how_to_use=how_to_use)


def _resolve_output_path(dist_dir: Path, output_name: str) -> Path:
    name = _strip_exe_suffix(output_name)
    if sys.platform == "win32":
        return dist_dir / f"{name}.exe"
    return dist_dir / name


def _pyinstaller_importable() -> bool:
    try:
        import PyInstaller  # noqa: F401

        return True
    except Exception:
        return False


@dataclass(frozen=True)
class _StreamResult:
    returncode: int
    lines: list[str]


def _run_streaming(cmd: list[str], *, progress: ProgressFn | None) -> _StreamResult:
    """Run ``cmd``, forwarding each output line to ``progress`` as it arrives.

    PyInstaller logs to stderr, so stderr is merged into stdout and read line by
    line. Every line is both remembered (for error reporting) and streamed live,
    which is what lets the GUI show the build's current step.
    """

    proc = subprocess.Popen(
        cmd,
        cwd=_REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    lines: list[str] = []
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip()
        if not line:
            continue
        lines.append(line)
        _emit(progress, line)
    returncode = proc.wait()
    return _StreamResult(returncode=returncode, lines=lines)
