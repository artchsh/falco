"""The launcher entry point: wire config + credentials + SSH together.

``run(config)`` is what a generated launcher's entry script calls. It:

1. Parses the command-line arguments.
2. Resolves the password from the OS credential store (prompting once).
3. Connects using password-based SSH (auto-accepting host keys).
4. Dispatches to command / stdin / interactive mode.
5. Returns the remote exit code so ``SystemExit`` can propagate it.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence

import paramiko

from launcher import cli, credentials, interactive, sftp_client, ssh_client
from launcher.cli import Mode
from shared.config import LauncherConfig
from shared.errors import FalcoError, SSHConnectionError


def run(config: LauncherConfig, argv: Sequence[str] | None = None) -> int:
    """Execute the launcher for ``config`` and return a process exit code."""

    raw_argv = list(sys.argv[1:] if argv is None else argv)
    try:
        request = cli.parse_args(raw_argv)
    except FalcoError as exc:
        print(f"falco: {exc}", file=sys.stderr)
        return 2

    try:
        if request.reset_password:
            credentials.delete_password(config)
        password = credentials.resolve_password(config)
    except FalcoError as exc:
        print(f"falco: {exc}", file=sys.stderr)
        return 3

    client = None
    try:
        client = ssh_client.connect(config, password)

        if request.mode is Mode.INTERACTIVE:
            return interactive.start_interactive_shell(client, config)

        if request.mode is Mode.STDIN:
            assert request.stdin_path is not None
            try:
                script = Path(request.stdin_path).read_text(encoding="utf-8")
            except OSError as exc:
                print(f"falco: cannot read {request.stdin_path}: {exc}", file=sys.stderr)
                return 2
            return ssh_client.run_script(client, script)

        if request.mode is Mode.SFTP:
            assert request.sftp is not None
            try:
                sftp = client.open_sftp()
            except (paramiko.SSHException, OSError) as exc:
                raise SSHConnectionError(f"Could not open SFTP session: {exc}") from exc
            try:
                sftp_client.execute(
                    sftp,
                    request.sftp,
                    overwrite=request.overwrite,
                    mkdirs=request.mkdirs,
                )
            finally:
                sftp.close()
            return 0

        assert request.command is not None
        return ssh_client.run_command(client, request.command)

    except FalcoError as exc:
        print(f"falco: {exc}", file=sys.stderr)
        return 4
    except KeyboardInterrupt:
        print("falco: interrupted.", file=sys.stderr)
        return 130
    finally:
        if client is not None:
            client.close()
