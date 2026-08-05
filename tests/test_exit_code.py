"""Tests that the remote exit code is propagated faithfully.

These exercise two layers:

* ``ssh_client._pump`` — the streaming loop that drains a channel and returns
  its exit status — using an in-memory fake channel.
* ``runtime.run`` — the top-level dispatcher — with the SSH and credential
  layers stubbed, verifying the remote code becomes the process exit code and
  that output is streamed to the provided buffers.
"""

from __future__ import annotations

import io
from typing import Iterator

import pytest

from launcher import runtime, ssh_client
from launcher.cli import Mode
from shared.config import LauncherConfig


class FakeChannel:
    """A minimal stand-in for a paramiko channel driven by scripted events."""

    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", exit_code: int = 0) -> None:
        self._out = stdout
        self._err = stderr
        self._exit = exit_code
        self._out_done = not stdout
        self._err_done = not stderr

    def setblocking(self, _flag: bool) -> None:
        pass

    def recv_ready(self) -> bool:
        return not self._out_done

    def recv_stderr_ready(self) -> bool:
        return not self._err_done

    def recv(self, _n: int) -> bytes:
        data, self._out, = self._out, b""
        self._out_done = True
        return data

    def recv_stderr(self, _n: int) -> bytes:
        data, self._err = self._err, b""
        self._err_done = True
        return data

    def exit_status_ready(self) -> bool:
        return self._out_done and self._err_done

    def recv_exit_status(self) -> int:
        return self._exit


@pytest.fixture(autouse=True)
def _fast_select(monkeypatch: pytest.MonkeyPatch) -> None:
    # Make select() return our channel as "ready" immediately, no real waiting.
    def _fake_select(rlist, _w, _x, _timeout):  # type: ignore[no-untyped-def]
        return rlist, [], []

    monkeypatch.setattr(ssh_client.select, "select", _fake_select)


def test_pump_streams_and_returns_exit_code() -> None:
    out, err = io.BytesIO(), io.BytesIO()
    chan = FakeChannel(stdout=b"hello\n", stderr=b"warn\n", exit_code=7)
    code = ssh_client._pump(chan, stdout=out, stderr=err)  # type: ignore[arg-type]
    assert code == 7
    assert out.getvalue() == b"hello\n"
    assert err.getvalue() == b"warn\n"


@pytest.mark.parametrize("expected", [0, 1, 42, 255])
def test_pump_propagates_various_codes(expected: int) -> None:
    code = ssh_client._pump(
        FakeChannel(exit_code=expected),  # type: ignore[arg-type]
        stdout=io.BytesIO(),
        stderr=io.BytesIO(),
    )
    assert code == expected


def test_runtime_run_returns_remote_exit_code(
    monkeypatch: pytest.MonkeyPatch, config: LauncherConfig
) -> None:
    monkeypatch.setattr(runtime.credentials, "resolve_password", lambda _c: "pw")

    class FakeClient:
        def close(self) -> None:
            pass

    monkeypatch.setattr(runtime.ssh_client, "connect", lambda _c, _p: FakeClient())

    captured: dict[str, object] = {}

    def fake_run_command(_client: object, command: str) -> int:
        captured["command"] = command
        return 13

    monkeypatch.setattr(runtime.ssh_client, "run_command", fake_run_command)

    code = runtime.run(config, argv=["docker", "ps"])
    assert code == 13
    assert captured["command"] == "docker ps"


def test_runtime_run_stdin_mode(
    monkeypatch: pytest.MonkeyPatch, config: LauncherConfig, tmp_path
) -> None:
    script = tmp_path / "deploy.sh"
    script.write_text("echo deploying\n", encoding="utf-8")

    monkeypatch.setattr(runtime.credentials, "resolve_password", lambda _c: "pw")

    class FakeClient:
        def close(self) -> None:
            pass

    monkeypatch.setattr(runtime.ssh_client, "connect", lambda _c, _p: FakeClient())

    seen: dict[str, object] = {}

    def fake_run_script(_client: object, text: str) -> int:
        seen["text"] = text
        return 0

    monkeypatch.setattr(runtime.ssh_client, "run_script", fake_run_script)

    code = runtime.run(config, argv=["--stdin", str(script)])
    assert code == 0
    assert seen["text"] == "echo deploying\n"


def test_runtime_run_bad_stdin_path_returns_2(
    monkeypatch: pytest.MonkeyPatch, config: LauncherConfig
) -> None:
    monkeypatch.setattr(runtime.credentials, "resolve_password", lambda _c: "pw")

    class FakeClient:
        def close(self) -> None:
            pass

    monkeypatch.setattr(runtime.ssh_client, "connect", lambda _c, _p: FakeClient())
    code = runtime.run(config, argv=["--stdin", "/does/not/exist.sh"])
    assert code == 2


def test_runtime_run_dispatches_interactive(
    monkeypatch: pytest.MonkeyPatch, config: LauncherConfig
) -> None:
    monkeypatch.setattr(runtime.credentials, "resolve_password", lambda _c: "pw")

    class FakeClient:
        def close(self) -> None:
            pass

    monkeypatch.setattr(runtime.ssh_client, "connect", lambda _c, _p: FakeClient())
    monkeypatch.setattr(
        runtime.interactive, "start_interactive_shell", lambda _client, _cfg: 0
    )
    called: dict[str, Mode] = {}

    original = runtime.cli.parse_args

    def spy(argv: object) -> object:
        req = original(argv)
        called["mode"] = req.mode
        return req

    monkeypatch.setattr(runtime.cli, "parse_args", spy)
    code = runtime.run(config, argv=[])
    assert code == 0
    assert called["mode"] is Mode.INTERACTIVE
