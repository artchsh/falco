"""Headless launcher build (used by CI and power users).

Example::

    python build/build_launcher.py \\
        --name server-client-X --host 203.0.113.10 --user deploy --port 22 \\
        --output server-client-X.exe --icon assets/server-client-X.ico --out-dir dist

The produced binary is identical to one built from the GUI; this script simply
skips Tkinter so it can run inside GitHub Actions.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the repo packages importable when run as a bare script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from editor.builder import build_launcher, build_all_launchers  # noqa: E402
from shared.config import LauncherConfig  # noqa: E402
from shared.errors import FalcoError  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a standalone Falco launcher.")
    parser.add_argument("--name", required=True, help="Launcher name, e.g. server-client-X")
    parser.add_argument("--host", required=True, help="SSH host or IP")
    parser.add_argument("--user", required=True, help="SSH username")
    parser.add_argument("--port", type=int, default=22, help="SSH port (default 22)")
    parser.add_argument("--output", required=True, help="Output filename, e.g. server-client-X.exe")
    parser.add_argument("--icon", default=None, help="Optional icon (.ico/.icns)")
    parser.add_argument("--private-key", help="Encrypted OpenSSH private key to embed (passphrase is not read)")
    parser.add_argument("--requires-vpn", action="store_true", help="Include required Tailscale/WireGuard/VPN guidance")
    parser.add_argument("--all-platforms", action="store_true", help="Build all available bundled platforms")
    parser.add_argument("--out-dir", default="dist", help="Output directory")
    args = parser.parse_args(argv)

    try:
        key = None
        if args.private_key:
            from shared.ssh_keys import MAX_KEY_BYTES
            with Path(args.private_key).open("rb") as stream:
                raw = stream.read(MAX_KEY_BYTES + 1)
            if len(raw) > MAX_KEY_BYTES:
                raise FalcoError("Encrypted OpenSSH key must be smaller than 128 KiB.")
            try:
                key = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise FalcoError("Key must be an encrypted OpenSSH text file.") from exc
        config = LauncherConfig.create(
            launcher_name=args.name,
            host=args.host,
            username=args.user,
            port=args.port,
            auth_method="private_key" if key is not None else "password",
            encrypted_private_key=key,
            requires_vpn=args.requires_vpn,
        )
        if args.all_platforms:
            if args.icon:
                raise FalcoError("Custom icons are not supported by prebuilt launcher stubs.")
            multi = build_all_launchers(config, base_name=args.output, output_dir=args.out_dir)
            for path in multi.executables:
                print(f"Built {path}")
            if multi.skipped:
                print("Unavailable platforms: " + ", ".join(multi.skipped))
            return 0
        result = build_launcher(
            config,
            output_name=args.output,
            icon_path=args.icon,
            output_dir=args.out_dir,
        )
    except (FalcoError, OSError) as exc:
        print(f"build failed: {exc}", file=sys.stderr)
        return 1

    print(f"Built {result.executable}")
    print(f"credential_id: {config.credential_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
