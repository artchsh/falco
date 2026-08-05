"""Command-line argument parsing for a generated launcher.

Three invocation styles are supported, mirroring the spec::

    meks                          # interactive shell (PTY)
    meks "docker ps"              # one quoted command string
    meks docker ps                # multiple args -> one safe command
    meks --stdin deploy.sh        # send a local file as a remote script

Arguments are never handed to a *local* shell. When several bare arguments are
given they are re-quoted with :func:`shlex.quote` and joined, so the command is
reconstructed faithfully and executed only on the remote server.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from shared.errors import ConfigError

# Flags handled locally by the launcher rather than forwarded to the server.
_RESET_FLAGS = {"--reset-password", "--reset-credential"}
_STDIN_FLAGS = {"--stdin"}


class Mode(str, Enum):
    INTERACTIVE = "interactive"
    COMMAND = "command"
    STDIN = "stdin"


@dataclass(frozen=True)
class LaunchRequest:
    """A fully-parsed description of what the launcher should do."""

    mode: Mode
    command: str | None = None
    stdin_path: str | None = None
    reset_password: bool = False


def build_command(args: Sequence[str]) -> str:
    """Turn bare CLI arguments into a single remote command string.

    * A single argument is passed through verbatim, so ``meks "a && b"`` keeps
      the shell operators the user intended.
    * Multiple arguments are individually shell-quoted and joined, so
      ``meks docker ps`` becomes ``docker ps`` and arguments containing spaces
      or metacharacters survive intact.
    """

    if len(args) == 1:
        return args[0]
    return " ".join(shlex.quote(arg) for arg in args)


def parse_args(argv: Sequence[str]) -> LaunchRequest:
    """Parse ``argv`` (excluding the program name) into a :class:`LaunchRequest`."""

    args = list(argv)

    # Leading local-only flags.
    reset = False
    while args and args[0] in _RESET_FLAGS:
        reset = True
        args.pop(0)

    if not args:
        return LaunchRequest(mode=Mode.INTERACTIVE, reset_password=reset)

    if args[0] in _STDIN_FLAGS:
        if len(args) != 2:
            raise ConfigError("--stdin requires exactly one file path, e.g. --stdin deploy.sh")
        return LaunchRequest(mode=Mode.STDIN, stdin_path=args[1], reset_password=reset)

    return LaunchRequest(
        mode=Mode.COMMAND,
        command=build_command(args),
        reset_password=reset,
    )
