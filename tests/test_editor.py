import time
import tkinter as tk

import pytest

from editor import app, builder


@pytest.fixture
def window(monkeypatch):
    monkeypatch.setattr(builder, "discover_stubs", lambda: {})
    try:
        probe = tk.Tk()
        probe.destroy()
    except tk.TclError as exc:
        pytest.skip(f"Tk display is unavailable: {exc}")
    root = app.FalcoEditor()
    root.withdraw()
    yield root
    if not root._closing:
        root._on_close()


def test_name_default_tracks_launcher_name_but_preserves_custom_output(window):
    window.var_name.set("my-server")
    assert window.var_output.get().startswith("my-server")
    window.var_output.set("custom")
    window.var_name.set("another-server")
    assert window.var_output.get() == "custom"


def test_build_failure_restores_controls_and_shows_useful_error(window, tmp_path, monkeypatch):
    messages = []
    monkeypatch.setattr(app.messagebox, "showerror", lambda title, message, **kwargs: messages.append(message))
    window.var_host.set("127.0.0.1")
    window.var_user.set("user")
    window.var_directory.set(str(tmp_path))
    window._on_build()
    deadline = time.monotonic() + 3
    while window._building and time.monotonic() < deadline:
        window._drain_events()
        time.sleep(0.01)
    assert not window._building
    assert "disabled" not in window.build_button.state()
    assert "failed" in window.status.get().lower()
    assert messages and "stub" in messages[-1]


def test_close_with_queued_completion_is_safe(window):
    from editor.build_jobs import BuildEvent
    window._events.put(BuildEvent(kind="error", message="late worker result"))
    window._on_close()
    assert window._closing


def test_unresolvable_output_folder_is_reported_without_starting_build(window, monkeypatch):
    messages = []
    monkeypatch.setattr(app.messagebox, "showerror", lambda title, message, **kwargs: messages.append(message))
    window.var_host.set("127.0.0.1")
    window.var_user.set("user")
    window.var_directory.set("~falco-user-that-does-not-exist-9e8a1/output")
    window._on_build()
    assert messages and "folder" in messages[-1].lower()
    assert not window._building


def test_thread_start_failure_restores_build_controls(window, tmp_path, monkeypatch):
    messages = []
    monkeypatch.setattr(app.messagebox, "showerror", lambda title, message, **kwargs: messages.append(message))
    def fail_start(thread):
        raise RuntimeError("cannot start build worker")
    monkeypatch.setattr(app.threading.Thread, "start", fail_start)
    window.var_host.set("127.0.0.1")
    window.var_user.set("user")
    window.var_directory.set(str(tmp_path))
    window._on_build()
    assert not window._building
    assert "disabled" not in window.build_button.state()
    assert messages and "worker" in messages[-1]
