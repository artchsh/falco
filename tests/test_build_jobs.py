from pathlib import Path
from queue import Queue
from threading import Thread

from editor import builder
from editor import build_jobs
from shared.config import LauncherConfig


def config():
    return LauncherConfig.create(launcher_name="n", host="h", username="u")


def test_worker_emits_output_files_and_final_success(tmp_path, monkeypatch):
    stub = tmp_path / "stub"
    stub.write_bytes(b"STUB-FALCO_SCHEMA_2")
    monkeypatch.setattr(builder, "discover_stubs", lambda: {"linux": stub})
    monkeypatch.setattr(builder, "_current_os_key", lambda: "linux")
    events = Queue()
    thread = Thread(target=build_jobs.run_build, args=(config(), "server", tmp_path / "out", False, events))
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive()
    received = []
    while not events.empty():
        received.append(events.get_nowait())
    assert received[-1].kind == "success"
    assert received[-1].paths == (tmp_path / "out/server",)
    assert received[-1].guide.is_file()
    assert any(e.kind == "progress" for e in received)


def test_worker_catches_unexpected_errors_without_losing_message(tmp_path, monkeypatch):
    def broken():
        raise RuntimeError("disk unavailable")
    monkeypatch.setattr(builder, "discover_stubs", broken)
    events = Queue()
    build_jobs.run_build(config(), "server", tmp_path, False, events)
    received = []
    while not events.empty():
        received.append(events.get_nowait())
    assert received[-1].kind == "error"
    assert "disk unavailable" in received[-1].message


def test_worker_reports_expected_missing_stub_error(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "discover_stubs", lambda: {})
    events = Queue()
    build_jobs.run_build(config(), "server", tmp_path, False, events)
    last = None
    while not events.empty():
        last = events.get_nowait()
    assert last.kind == "error"
    assert "stub" in last.message
