"""Package the Falco Editor GUI as a standalone executable (PyInstaller).

Used by the release workflow to produce one downloadable binary per OS:

* Windows  -> falco-editor.exe   (windowed: no console box behind the GUI)
* macOS    -> falco-editor       (single binary; launches the Tk window)
* Linux    -> falco-editor       (single binary; needs a display + Tk at runtime)

Note: launchers are no longer compiled per build. The editor ships prebuilt Rust
``falco-stub`` binaries (bundled here via ``--add-data`` under ``stubs/``) and
produces a launcher by copying a stub and appending the non-secret config. This
needs no toolchain, so a *frozen* editor can produce launchers fully offline —
and, when all three per-OS stubs are bundled, can emit Windows/macOS/Linux
launchers from a single run.

Stub sources, in priority order:

1. ``<repo>/stubs/falco-stub-{windows.exe,macos,linux}`` — CI downloads the
   per-OS stub artifacts here so every editor bundles all three platforms.
2. ``launcher-rs/target/release/falco-stub[.exe]`` — a local ``cargo build
   --release`` provides the current OS's stub for dev builds.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Canonical per-OS stub file names bundled under ``stubs/``.
_CANONICAL = {
    "windows": "falco-stub-windows.exe",
    "macos": "falco-stub-macos",
    "linux": "falco-stub-linux",
}


def _current_os_key() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def stage_stubs(staging_root: Path) -> Path:
    """Assemble a ``stubs/`` dir with every available per-OS stub; return it.

    Always includes the current OS's stub (from CI artifacts or a local cargo
    build); includes other platforms when CI has provided their artifacts.
    """

    staging = staging_root / "stubs"
    staging.mkdir(parents=True, exist_ok=True)
    collected: list[str] = []

    # 1. CI-provided stubs (one artifact per OS, canonical names) in <repo>/stubs.
    provided = _REPO_ROOT / "stubs"
    if provided.exists():
        for name in _CANONICAL.values():
            src = provided / name
            if src.exists() and src.resolve() != (staging / name).resolve():
                shutil.copy2(src, staging / name)
                collected.append(name)

    # 2. Ensure the current OS's stub is present (from a local cargo build).
    cur = _current_os_key()
    cur_name = _CANONICAL[cur]
    if cur_name not in collected:
        dev_name = "falco-stub.exe" if cur == "windows" else "falco-stub"
        dev = _REPO_ROOT / "launcher-rs" / "target" / "release" / dev_name
        if dev.exists():
            shutil.copy2(dev, staging / cur_name)
            collected.append(cur_name)

    if not collected:
        raise SystemExit(
            "no launcher stubs found to bundle. Build one with "
            "`cd launcher-rs && cargo build --release`, or provide per-OS stubs "
            f"under {provided}."
        )
    print(f"Bundling stubs: {', '.join(sorted(collected))}", flush=True)
    return staging


def pyinstaller_command(
    *, name: str, dist_dir: Path, work_dir: Path, stubs_dir: Path
) -> list[str]:
    entry = _REPO_ROOT / "editor" / "__main__.py"
    sep = ";" if sys.platform == "win32" else ":"
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
        "--paths",
        str(_REPO_ROOT),
        "--hidden-import",
        "editor",
        "--hidden-import",
        "shared",
        # Bundle every available per-OS launcher stub under ``stubs/``.
        "--add-data",
        f"{stubs_dir}{sep}stubs",
    ]

    # Hide the console window behind the GUI on Windows.
    if sys.platform == "win32":
        cmd.append("--windowed")
    cmd.append(str(entry))
    return cmd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the standalone Falco Editor.")
    parser.add_argument("--name", default="falco-editor", help="Output name")
    parser.add_argument("--out-dir", default="dist", help="Output directory")
    args = parser.parse_args(argv)

    dist_dir = Path(args.out_dir).resolve()
    work_dir = dist_dir / ".falco-editor-build"
    dist_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    stubs_dir = stage_stubs(work_dir)

    cmd = pyinstaller_command(
        name=args.name, dist_dir=dist_dir, work_dir=work_dir, stubs_dir=stubs_dir
    )
    print("$", " ".join(cmd), flush=True)
    proc = subprocess.run(cmd, cwd=_REPO_ROOT)
    if proc.returncode != 0:
        print("editor build failed", file=sys.stderr)
        return proc.returncode

    exe = dist_dir / (f"{args.name}.exe" if sys.platform == "win32" else args.name)
    if not exe.exists():
        print(f"expected editor binary not found at {exe}", file=sys.stderr)
        return 1
    print(f"Built {exe}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
