"""Command-line argument parsing for a generated launcher.

Invocation styles (all execute against the one configured remote server)::

    meks                              # interactive shell (PTY)
    meks "docker ps"                  # one quoted command string
    meks docker ps                    # multiple args -> one safe command
    meks --stdin deploy.sh            # send a local file as a remote script

SFTP file operations (same connection + stored password)::

    meks --upload ./f /remote/path        meks --download /remote/f ./path
    meks --list /remote/dir               meks --mkdir /remote/dir
    meks --remove /remote/f               meks --move /remote/a /remote/b
    meks --upload-dir ./dist /var/www     meks --download-dir /var/log ./logs

Modifiers: ``--overwrite`` (allow replacing existing files) and ``--mkdirs``
(create missing destination directories). ``--reset-password`` forgets the
stored credential first.

Bare command arguments are never handed to a *local* shell: when several are
given they are re-quoted with :func:`shlex.quote` and joined, so the command is
reconstructed faithfully and executed only on the remote server.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from shared.errors import ConfigError

_RESET_FLAGS = {"--reset-password", "--reset-credential"}
_STDIN_FLAGS = {"--stdin"}
_OVERWRITE_FLAG = "--overwrite"
_MKDIRS_FLAG = "--mkdirs"


class Mode(str, Enum):
    INTERACTIVE = "interactive"
    COMMAND = "command"
    STDIN = "stdin"
    SFTP = "sftp"


class SftpAction(str, Enum):
    UPLOAD = "upload"
    DOWNLOAD = "download"
    LIST = "list"
    MKDIR = "mkdir"
    REMOVE = "remove"
    MOVE = "move"
    UPLOAD_DIR = "upload-dir"
    DOWNLOAD_DIR = "download-dir"


# flag -> (action, number of positional operands it consumes)
SFTP_SPECS: dict[str, tuple[SftpAction, int]] = {
    "--upload": (SftpAction.UPLOAD, 2),
    "--download": (SftpAction.DOWNLOAD, 2),
    "--list": (SftpAction.LIST, 1),
    "--mkdir": (SftpAction.MKDIR, 1),
    "--remove": (SftpAction.REMOVE, 1),
    "--move": (SftpAction.MOVE, 2),
    "--upload-dir": (SftpAction.UPLOAD_DIR, 2),
    "--download-dir": (SftpAction.DOWNLOAD_DIR, 2),
}


@dataclass(frozen=True)
class SftpOp:
    action: SftpAction
    operands: tuple[str, ...]


@dataclass(frozen=True)
class LaunchRequest:
    """A fully-parsed description of what the launcher should do."""

    mode: Mode
    command: str | None = None
    stdin_path: str | None = None
    sftp: SftpOp | None = None
    reset_password: bool = False
    overwrite: bool = False
    mkdirs: bool = False


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


def _take_flag(args: list[str], name: str) -> bool:
    """Remove all occurrences of ``name`` from ``args``; return whether present."""

    present = name in args
    while name in args:
        args.remove(name)
    return present


def _parse_sftp(args: list[str]) -> LaunchRequest:
    """Parse an argv that contains one SFTP action flag."""

    reset = _take_flag(args, "--reset-password") or _take_flag(args, "--reset-credential")
    overwrite = _take_flag(args, _OVERWRITE_FLAG)
    mkdirs = _take_flag(args, _MKDIRS_FLAG)

    action_flag = next(a for a in args if a in SFTP_SPECS)
    idx = args.index(action_flag)
    leading = args[:idx]
    if leading:
        raise ConfigError(
            f"Unexpected argument(s) before {action_flag}: {' '.join(leading)}"
        )

    operands = args[idx + 1 :]
    other_actions = [a for a in operands if a in SFTP_SPECS]
    if other_actions:
        raise ConfigError("Only one SFTP operation may be given at a time.")

    action, arity = SFTP_SPECS[action_flag]
    if len(operands) != arity:
        raise ConfigError(
            f"{action_flag} expects {arity} argument(s), got {len(operands)}."
        )

    return LaunchRequest(
        mode=Mode.SFTP,
        sftp=SftpOp(action=action, operands=tuple(operands)),
        reset_password=reset,
        overwrite=overwrite,
        mkdirs=mkdirs,
    )


def parse_args(argv: Sequence[str]) -> LaunchRequest:
    """Parse ``argv`` (excluding the program name) into a :class:`LaunchRequest`."""

    args = list(argv)

    # SFTP mode is selected by the presence of an SFTP action flag anywhere.
    if any(a in SFTP_SPECS for a in args):
        return _parse_sftp(args)

    # Leading local-only reset flag.
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
