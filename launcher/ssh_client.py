"""Password-based SSH connection and one-shot command execution (Paramiko).

Responsibilities:

* Open an authenticated connection using a password (no keys, no agent).
* Auto-accept unknown/changed host keys (see the security note in the README:
  this trades protection against server impersonation for zero-friction use).
* Execute a single command, streaming stdout and stderr live to the local
  terminal, and return the remote exit code.
* Run a script supplied on stdin (``--stdin`` mode).
"""

from __future__ import annotations

import select
import sys
from typing import BinaryIO

import paramiko

from shared.config import LauncherConfig
from shared.errors import RemoteCommandError, SSHConnectionError

# How long to block in select() before looping again to re-check exit status.
_SELECT_TIMEOUT = 0.1
_CHUNK = 32768


def connect(config: LauncherConfig, password: str, *, timeout: float = 15.0) -> paramiko.SSHClient:
    """Return an authenticated :class:`paramiko.SSHClient`.

    Host keys are auto-accepted via :class:`paramiko.AutoAddPolicy`. This is a
    deliberate, documented security trade-off; see the README.
    """

    client = paramiko.SSHClient()
    # SECURITY: auto-accept unknown *and* changed host keys. This defeats the
    # protection SSH host-key verification normally provides against a
    # man-in-the-middle / impersonating server. See README "Security limitations".
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=config.host,
            port=config.port,
            username=config.username,
            password=password,
            look_for_keys=False,
            allow_agent=False,
            timeout=timeout,
        )
    except paramiko.AuthenticationException as exc:
        client.close()
        raise SSHConnectionError(
            f"Authentication failed for {config.username}@{config.host}. "
            f"The stored password may be wrong; re-run with --reset-password."
        ) from exc
    except (paramiko.SSHException, OSError) as exc:
        client.close()
        raise SSHConnectionError(
            f"Could not connect to {config.host}:{config.port}: {exc}"
        ) from exc
    return client


def _pump(
    channel: paramiko.Channel,
    *,
    stdout: BinaryIO,
    stderr: BinaryIO,
) -> int:
    """Stream a channel's stdout/stderr live and return its exit code."""

    channel.setblocking(False)
    while True:
        ready, _, _ = select.select([channel], [], [], _SELECT_TIMEOUT)
        got_data = False
        if ready:
            while channel.recv_ready():
                data = channel.recv(_CHUNK)
                if data:
                    stdout.write(data)
                    stdout.flush()
                    got_data = True
            while channel.recv_stderr_ready():
                data = channel.recv_stderr(_CHUNK)
                if data:
                    stderr.write(data)
                    stderr.flush()
                    got_data = True

        if channel.exit_status_ready() and not got_data \
                and not channel.recv_ready() and not channel.recv_stderr_ready():
            break

    return channel.recv_exit_status()


def run_command(
    client: paramiko.SSHClient,
    command: str,
    *,
    stdout: BinaryIO | None = None,
    stderr: BinaryIO | None = None,
) -> int:
    """Execute ``command`` remotely, stream output live, return the exit code."""

    out = stdout if stdout is not None else sys.stdout.buffer
    err = stderr if stderr is not None else sys.stderr.buffer
    transport = client.get_transport()
    if transport is None:  # pragma: no cover - defensive
        raise RemoteCommandError("SSH transport is not available.")
    try:
        channel = transport.open_session()
        channel.exec_command(command)
    except paramiko.SSHException as exc:
        raise RemoteCommandError(f"Failed to start remote command: {exc}") from exc
    return _pump(channel, stdout=out, stderr=err)


def run_script(
    client: paramiko.SSHClient,
    script: str,
    *,
    shell: str = "/bin/sh -s",
    stdout: BinaryIO | None = None,
    stderr: BinaryIO | None = None,
) -> int:
    """Feed ``script`` to a remote shell via stdin and return the exit code.

    Used by ``--stdin deploy.sh``: the file's contents are piped into
    ``/bin/sh -s`` on the server, so the script executes remotely without ever
    being written to a file or expanded by a local shell.
    """

    out = stdout if stdout is not None else sys.stdout.buffer
    err = stderr if stderr is not None else sys.stderr.buffer
    transport = client.get_transport()
    if transport is None:  # pragma: no cover - defensive
        raise RemoteCommandError("SSH transport is not available.")
    try:
        channel = transport.open_session()
        channel.exec_command(shell)
    except paramiko.SSHException as exc:
        raise RemoteCommandError(f"Failed to start remote shell: {exc}") from exc

    payload = script.encode("utf-8") if isinstance(script, str) else script
    channel.sendall(payload)
    channel.shutdown_write()  # signal EOF so the shell runs and exits
    return _pump(channel, stdout=out, stderr=err)
