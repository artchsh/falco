from pathlib import Path

import pytest

from editor import builder
from shared.config import LauncherConfig
from shared.errors import FalcoError


def setup_stub(tmp_path, monkeypatch, contents=b"STUB-FALCO_SCHEMA_2"):
    stub = tmp_path / "stub"
    stub.write_bytes(contents)
    monkeypatch.setattr(builder, "discover_stubs", lambda: {"linux": stub})
    monkeypatch.setattr(builder, "_current_os_key", lambda: "linux")
    return LauncherConfig.create(launcher_name="n", host="h", username="u")


@pytest.mark.parametrize("name", ["../escape", "/tmp/escape", "..", "CON.exe", "aux", "NUL.txt", "a\\b", "a:b", "hello;echo", "name.", ".exe", "how-to-use.md", "a" * 121])
def test_bad_names_do_not_create_files(tmp_path, monkeypatch, name):
    cfg = setup_stub(tmp_path, monkeypatch)
    with pytest.raises(FalcoError):
        builder.build_launcher(cfg, output_name=name, output_dir=tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("name", ["server.exe.exe", "server.exe.bin", "server.APP.EXE"])
def test_nested_packaging_suffixes_fail_before_writing(tmp_path, monkeypatch, name):
    cfg = setup_stub(tmp_path, monkeypatch)
    with pytest.raises(FalcoError):
        builder.build_launcher(cfg, output_name=name, output_dir=tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_old_stub_is_rejected_before_any_output(tmp_path, monkeypatch):
    cfg = setup_stub(tmp_path, monkeypatch, b"old stub")
    with pytest.raises(FalcoError, match="stub"):
        builder.build_launcher(cfg, output_name="server", output_dir=tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_output_cannot_replace_source_stub(tmp_path, monkeypatch):
    cfg = setup_stub(tmp_path, monkeypatch)
    before = (tmp_path / "stub").read_bytes()
    with pytest.raises(FalcoError, match="stub"):
        builder.build_launcher(cfg, output_name="stub", output_dir=tmp_path)
    assert (tmp_path / "stub").read_bytes() == before


def test_failed_guide_staging_preserves_existing_launcher(tmp_path, monkeypatch):
    cfg = setup_stub(tmp_path, monkeypatch)
    out = tmp_path / "out"
    out.mkdir()
    (out / "server").write_bytes(b"existing")
    original = Path.write_text

    def fail_guide(path, *args, **kwargs):
        if path.name == "how-to-use.md":
            raise OSError("disk full")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_guide)
    with pytest.raises(FalcoError, match="disk full"):
        builder.build_launcher(cfg, output_name="server", output_dir=out)
    assert (out / "server").read_bytes() == b"existing"
    assert sorted(p.name for p in out.iterdir()) == ["server"]


def test_guide_uses_actual_single_platform_filename(tmp_path, monkeypatch):
    cfg = setup_stub(tmp_path, monkeypatch)
    result = builder.build_launcher(cfg, output_name="server.exe", output_dir=tmp_path / "out")
    doc = result.how_to_use.read_text()
    assert "./server \"" in doc
    assert "server-linux" not in doc
    assert "com.apple.quarantine" not in doc


def test_invalid_embedded_key_preserves_existing_output(tmp_path, monkeypatch):
    setup_stub(tmp_path, monkeypatch)
    cfg = LauncherConfig(launcher_name="n", host="h", username="u",
                         auth_method="private_key", encrypted_private_key="malformed key")
    out = tmp_path / "out"
    out.mkdir()
    (out / "server").write_bytes(b"existing")
    with pytest.raises(FalcoError):
        builder.build_launcher(cfg, output_name="server", output_dir=out)
    assert (out / "server").read_bytes() == b"existing"
    assert sorted(p.name for p in out.iterdir()) == ["server"]


def test_vpn_hint_and_key_setup_are_conditional():
    from editor.templates import render_how_to_use
    key = (Path(__file__).parent / "fixtures/encrypted_ed25519").read_text()
    cfg = LauncherConfig.create(launcher_name="n", host="h", username="u", requires_vpn=True,
                               auth_method="private_key", encrypted_private_key=key)
    doc = render_how_to_use(cfg, "server", filenames=["server"])
    assert "Tailscale/WireGuard/VPN" in doc
    assert "passphrase" in doc
    assert "HOST_KEY_CHANGED" in doc
    assert key not in doc


def test_staged_files_are_writable_when_synced(tmp_path, monkeypatch):
    """Windows fsync requires a writable handle even after closing the writer."""
    import errno
    cfg = setup_stub(tmp_path, monkeypatch)
    opened = {}
    original_open = Path.open
    original_sync = builder.os.fsync

    def track_open(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        if path.parent.name.startswith('.falco-build-'):
            opened[stream.fileno()] = stream
        return stream

    def windows_sync(fd):
        if not opened[fd].writable():
            raise OSError(errno.EBADF, 'sync requires a writable handle')
        original_sync(fd)

    monkeypatch.setattr(Path, 'open', track_open)
    monkeypatch.setattr(builder.os, 'fsync', windows_sync)
    result = builder.build_launcher(cfg, output_name='server', output_dir=tmp_path / 'out')
    assert result.executable.exists()
    assert result.how_to_use.exists()
