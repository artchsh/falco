"""Package the Falco Editor GUI as a standalone executable (PyInstaller).

Used by the release workflow to produce one downloadable binary per OS:

* Windows  -> falco-editor.exe   (windowed: no console box behind the GUI)
* macOS    -> falco-editor       (single binary; launches the Tk window)
* Linux    -> falco-editor       (single binary; needs a display + Tk at runtime)

Note: a *frozen* editor cannot itself run PyInstaller to compile server
launchers (there is no Python toolchain inside the binary). The packaged editor
is for authoring/validating launcher configuration; compiling launchers is done
from a Python environment via ``build/build_launcher.py`` or the build
workflows. The editor surfaces a clear message if PyInstaller is unavailable.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def pyinstaller_command(*, name: str, dist_dir: Path, work_dir: Path) -> list[str]:
    entry = _REPO_ROOT / "editor" / "__main__.py"
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

    cmd = pyinstaller_command(name=args.name, dist_dir=dist_dir, work_dir=work_dir)
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
