"""Tests for launcher assembly (stub + appended config) and the usage guide.

The launcher is now a prebuilt Rust stub with the non-secret config appended as
a trailer. These tests cover the pure, deterministic parts: the appended-config
trailer layout and the generated usage guide. The actual stub build runs in CI.
"""

from __future__ import annotations

import struct

from editor import builder
from editor.builder import MAGIC, append_config
from editor.templates import render_how_to_use
from shared.config import LauncherConfig


def test_append_config_writes_trailer() -> None:
    cfg = LauncherConfig.create(
        launcher_name="server-client-X", host="1.2.3.4", username="root", port=22
    )
    stub = b"FAKE-STUB"
    blob = append_config(stub, cfg)

    # Layout: [stub][json][u64 LE length][magic]
    assert blob.startswith(stub)
    assert blob.endswith(MAGIC)
    length = struct.unpack("<Q", blob[-16:-8])[0]
    json_bytes = blob[-16 - length : -16]
    assert b'"host": "1.2.3.4"' in json_bytes or b'"host":"1.2.3.4"' in json_bytes
    # No password field can leak into the trailer — the type has none.
    assert b"password" not in json_bytes


def test_append_config_embeds_all_nonsecret_fields() -> None:
    cfg = LauncherConfig.create(
        launcher_name="server-client-X", host="10.0.0.5", username="deploy", port=2200
    )
    blob = append_config(b"STUB", cfg)
    length = struct.unpack("<Q", blob[-16:-8])[0]
    json_bytes = blob[-16 - length : -16]
    text = json_bytes.decode("utf-8")
    assert "10.0.0.5" in text
    assert "deploy" in text
    assert "2200" in text
    assert cfg.credential_id in text


def test_how_to_use_uses_invocation_name_and_target() -> None:
    cfg = LauncherConfig.create(
        launcher_name="server-client-X", host="203.0.113.10", username="ubuntu", port=2200
    )
    doc = render_how_to_use(cfg, "server-client-X.exe")
    # The command examples use the name without the .exe suffix.
    assert "`server-client-X`" in doc
    assert "server-client-X.exe" not in doc
    assert "203.0.113.10" in doc
    assert "ubuntu" in doc
    assert "2200" in doc


def test_how_to_use_makes_credential_and_encryption_claims() -> None:
    cfg = LauncherConfig.create(launcher_name="server-client-X", host="h", username="u")
    doc = render_how_to_use(cfg, "server-client-X").lower()
    assert "no password is stored" in doc
    assert "credential store" in doc
    assert "encrypted" in doc
    assert "man-in-the-middle" in doc or "trust-on-connect" in doc


def test_how_to_use_notes_chmod_for_unix_launchers() -> None:
    cfg = LauncherConfig.create(launcher_name="server-client-X", host="h", username="u")
    doc = render_how_to_use(cfg, "server-client-X.exe")
    assert "chmod +x server-client-X-linux" in doc
    assert "chmod +x server-client-X-macos" in doc
    # macOS Gatekeeper quarantine guidance is present too.
    assert "com.apple.quarantine" in doc


def test_how_to_use_has_timeout_troubleshooting() -> None:
    cfg = LauncherConfig.create(
        launcher_name="server-client-X", host="10.0.0.5", username="deploy"
    )
    doc = render_how_to_use(cfg, "server-client-X")
    assert "Troubleshooting" in doc
    assert "times out" in doc
    assert "not logged in" in doc
    assert "ssh deploy@10.0.0.5" in doc


def test_how_to_use_contains_no_secret_value() -> None:
    cfg = LauncherConfig.create(launcher_name="server-client-X", host="h", username="u")
    doc = render_how_to_use(cfg, "server-client-X")
    assert "password=" not in doc


def test_strip_exe_suffix_variants() -> None:
    assert builder._strip_exe_suffix("server-client-X.exe") == "server-client-X"
    assert builder._strip_exe_suffix("server-client-X.app") == "server-client-X"
    assert builder._strip_exe_suffix("server-client-X") == "server-client-X"


def _fake_stubs(tmp_path):
    """Create fake per-OS stub files and return {key: path}."""
    stubs_dir = tmp_path / "stubs"
    stubs_dir.mkdir()
    fake = {}
    for target in builder.TARGETS:
        p = stubs_dir / target.bundled_name
        p.write_bytes(b"STUB-" + target.key.encode())
        fake[target.key] = p
    return fake


def test_build_all_launchers_emits_one_per_platform(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(builder, "discover_stubs", lambda: _fake_stubs(tmp_path))
    cfg = LauncherConfig.create(launcher_name="server-client-X", host="h", username="u")

    res = builder.build_all_launchers(
        cfg, base_name="server-client-X.exe", output_dir=tmp_path / "out"
    )

    names = sorted(p.name for p in res.executables)
    assert names == ["server-client-X-linux", "server-client-X-macos", "server-client-X-windows.exe"]
    # Every produced launcher is stub bytes + the config trailer.
    for p in res.executables:
        blob = p.read_bytes()
        assert blob.startswith(b"STUB-")
        assert blob.endswith(builder.MAGIC)
    assert res.skipped == []
    assert res.how_to_use.exists()


def test_build_all_launchers_reports_skipped_platforms(tmp_path, monkeypatch) -> None:
    all_stubs = _fake_stubs(tmp_path)
    # Only Linux available in this editor.
    monkeypatch.setattr(builder, "discover_stubs", lambda: {"linux": all_stubs["linux"]})
    cfg = LauncherConfig.create(launcher_name="server-client-X", host="h", username="u")

    res = builder.build_all_launchers(
        cfg, base_name="server-client-X", output_dir=tmp_path / "out"
    )

    assert [p.name for p in res.executables] == ["server-client-X-linux"]
    assert sorted(res.skipped) == ["macos", "windows"]


def test_build_all_launchers_errors_when_no_stubs(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(builder, "discover_stubs", lambda: {})
    cfg = LauncherConfig.create(launcher_name="server-client-X", host="h", username="u")
    try:
        builder.build_all_launchers(cfg, base_name="server-client-X", output_dir=tmp_path / "out")
    except Exception as exc:  # FalcoError
        assert "No launcher stubs" in str(exc)
    else:
        raise AssertionError("expected FalcoError when no stubs are available")
