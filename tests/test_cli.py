"""Tests for command-line parsing and safe command reconstruction."""

from __future__ import annotations

import pytest

from launcher.cli import Mode, build_command, parse_args
from shared.errors import ConfigError


def test_no_args_is_interactive() -> None:
    req = parse_args([])
    assert req.mode is Mode.INTERACTIVE
    assert req.command is None


def test_single_quoted_command_passed_verbatim() -> None:
    req = parse_args(["cd /var/www && git pull && docker compose up -d"])
    assert req.mode is Mode.COMMAND
    assert req.command == "cd /var/www && git pull && docker compose up -d"


def test_multiple_bare_args_are_joined() -> None:
    req = parse_args(["docker", "ps"])
    assert req.mode is Mode.COMMAND
    assert req.command == "docker ps"


def test_bare_args_with_spaces_are_quoted_safely() -> None:
    # e.g. meks echo "hello world"  -> the shell passed one arg "hello world"
    assert build_command(["echo", "hello world"]) == "echo 'hello world'"


def test_bare_args_with_metacharacters_are_quoted() -> None:
    # Metacharacters in separate args must not become live shell operators.
    cmd = build_command(["echo", "a; rm -rf /"])
    assert cmd == "echo 'a; rm -rf /'"
    assert ";" in cmd  # preserved as data, not as a second command


def test_single_arg_preserves_intended_operators() -> None:
    assert build_command(["a && b"]) == "a && b"


def test_stdin_mode_requires_one_path() -> None:
    req = parse_args(["--stdin", "deploy.sh"])
    assert req.mode is Mode.STDIN
    assert req.stdin_path == "deploy.sh"


@pytest.mark.parametrize("argv", [["--stdin"], ["--stdin", "a", "b"]])
def test_stdin_mode_rejects_wrong_arity(argv: list[str]) -> None:
    with pytest.raises(ConfigError):
        parse_args(argv)


def test_reset_password_flag_then_interactive() -> None:
    req = parse_args(["--reset-password"])
    assert req.reset_password is True
    assert req.mode is Mode.INTERACTIVE


def test_reset_password_flag_before_command() -> None:
    req = parse_args(["--reset-password", "docker", "ps"])
    assert req.reset_password is True
    assert req.command == "docker ps"
