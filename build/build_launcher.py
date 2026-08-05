"""Headless launcher build (used by CI and power users).

Example::

    python build/build_launcher.py \\
        --name meks --host 203.0.113.10 --user deploy --port 22 \\
        --output meks.exe --icon assets/meks.ico --out-dir dist

The produced binary is identical to one built from the GUI; this script simply
skips Tkinter so it can run inside GitHub Actions.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the repo packages importable when run as a bare script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from editor.builder import build_launcher  # noqa: E402
from shared.config import LauncherConfig  # noqa: E402
from shared.errors import FalcoError  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a standalone Falco launcher.")
    parser.add_argument("--name", required=True, help="Launcher name, e.g. meks")
    parser.add_argument("--host", required=True, help="SSH host or IP")
    parser.add_argument("--user", required=True, help="SSH username")
    parser.add_argument("--port", type=int, default=22, help="SSH port (default 22)")
    parser.add_argument("--output", required=True, help="Output filename, e.g. meks.exe")
    parser.add_argument("--icon", default=None, help="Optional icon (.ico/.icns)")
    parser.add_argument("--out-dir", default="dist", help="Output directory")
    args = parser.parse_args(argv)

    try:
        config = LauncherConfig.create(
            launcher_name=args.name,
            host=args.host,
            username=args.user,
            port=args.port,
        )
        result = build_launcher(
            config,
            output_name=args.output,
            icon_path=args.icon,
            output_dir=args.out_dir,
        )
    except FalcoError as exc:
        print(f"build failed: {exc}", file=sys.stderr)
        return 1

    print(f"Built {result.executable}")
    print(f"credential_id: {config.credential_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
