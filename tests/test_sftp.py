"""Tests for SFTP argument parsing and file-operation behaviour.

The behaviour tests run against :class:`FakeSFTP`, which maps a real local
temp directory as the "remote" filesystem. This exercises the real overwrite
guards, temp-file-then-rename download logic, and recursive structure handling
without needing a live SSH server.
"""

from __future__ import annotations

import io
import os
import stat as stat_module
from pathlib import Path

import paramiko
import pytest

from launcher import runtime, sftp_client
from launcher.cli import Mode, SftpAction, parse_args
from shared.config import LauncherConfig
from shared.errors import ConfigError, FalcoError


# --------------------------------------------------------------------------- #
# CLI parsing
# --------------------------------------------------------------------------- #
def test_parse_upload() -> None:
    req = parse_args(["--upload", "./f", "/remote/path"])
    assert req.mode is Mode.SFTP
    assert req.sftp is not None
    assert req.sftp.action is SftpAction.UPLOAD
    assert req.sftp.operands == ("./f", "/remote/path")


@pytest.mark.parametrize(
    "argv, action, arity",
    [
        (["--download", "/r/f", "./l"], SftpAction.DOWNLOAD, 2),
        (["--list", "/r"], SftpAction.LIST, 1),
        (["--mkdir", "/r"], SftpAction.MKDIR, 1),
        (["--remove", "/r/f"], SftpAction.REMOVE, 1),
        (["--move", "/a", "/b"], SftpAction.MOVE, 2),
        (["--upload-dir", "./d", "/r"], SftpAction.UPLOAD_DIR, 2),
        (["--download-dir", "/r", "./d"], SftpAction.DOWNLOAD_DIR, 2),
    ],
)
def test_parse_all_actions(argv, action, arity) -> None:
    req = parse_args(argv)
    assert req.sftp is not None
    assert req.sftp.action is action
    assert len(req.sftp.operands) == arity


def test_overwrite_and_mkdirs_flags_are_extracted() -> None:
    req = parse_args(["--upload", "./f", "/r/f", "--overwrite", "--mkdirs"])
    assert req.overwrite is True
    assert req.mkdirs is True
    assert req.sftp is not None
    assert req.sftp.operands == ("./f", "/r/f")  # flags removed from operands


def test_flags_may_precede_action() -> None:
    req = parse_args(["--overwrite", "--upload", "./f", "/r/f"])
    assert req.overwrite is True
    assert req.sftp is not None and req.sftp.operands == ("./f", "/r/f")


@pytest.mark.parametrize("argv", [["--upload", "onlyone"], ["--move", "a", "b", "c"], ["--list"]])
def test_wrong_arity_raises(argv) -> None:
    with pytest.raises(ConfigError):
        parse_args(argv)


def test_two_actions_rejected() -> None:
    with pytest.raises(ConfigError):
        parse_args(["--upload", "a", "--download"])


def test_plain_command_not_treated_as_sftp() -> None:
    req = parse_args(["docker", "ps"])
    assert req.mode is Mode.COMMAND
    assert req.overwrite is False


# --------------------------------------------------------------------------- #
# FakeSFTP backed by a temp "remote" directory
# --------------------------------------------------------------------------- #
class FakeSFTP:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _p(self, remote: str) -> str:
        return str(self.root / remote.lstrip("/").replace("/", os.sep))

    def stat(self, remote: str):
        return os.stat(self._p(remote))

    def listdir_attr(self, remote: str):
        result = []
        base = self._p(remote)
        for name in os.listdir(base):
            attr = paramiko.SFTPAttributes.from_stat(os.stat(os.path.join(base, name)))
            attr.filename = name
            result.append(attr)
        return result

    def mkdir(self, remote: str) -> None:
        os.mkdir(self._p(remote))

    def remove(self, remote: str) -> None:
        os.remove(self._p(remote))

    def rename(self, src: str, dst: str) -> None:
        os.rename(self._p(src), self._p(dst))

    def posix_rename(self, src: str, dst: str) -> None:
        os.replace(self._p(src), self._p(dst))

    def put(self, local: str, remote: str, callback=None) -> None:
        data = Path(local).read_bytes()
        Path(self._p(remote)).write_bytes(data)
        if callback:
            callback(len(data), len(data))

    def get(self, remote: str, local: str, callback=None) -> None:
        data = Path(self._p(remote)).read_bytes()
        Path(local).write_bytes(data)
        if callback:
            callback(len(data), len(data))

    def close(self) -> None:
        pass


@pytest.fixture()
def remote(tmp_path: Path) -> FakeSFTP:
    root = tmp_path / "remote"
    root.mkdir()
    return FakeSFTP(root)


def test_upload_creates_dirs_and_preserves_content(tmp_path: Path, remote: FakeSFTP) -> None:
    local = tmp_path / "hello.txt"
    local.write_text("hi there", encoding="utf-8")
    sftp_client.upload_file(
        remote, str(local), "/data/hello.txt", overwrite=False, mkdirs=True, err=io.StringIO()
    )
    assert (remote.root / "data" / "hello.txt").read_text(encoding="utf-8") == "hi there"


def test_upload_refuses_overwrite(tmp_path: Path, remote: FakeSFTP) -> None:
    local = tmp_path / "f.txt"
    local.write_text("v1", encoding="utf-8")
    sftp_client.upload_file(remote, str(local), "/f.txt", overwrite=False, mkdirs=False, err=io.StringIO())
    local.write_text("v2", encoding="utf-8")
    with pytest.raises(FalcoError):
        sftp_client.upload_file(remote, str(local), "/f.txt", overwrite=False, mkdirs=False, err=io.StringIO())
    # With --overwrite it succeeds and replaces content.
    sftp_client.upload_file(remote, str(local), "/f.txt", overwrite=True, mkdirs=False, err=io.StringIO())
    assert (remote.root / "f.txt").read_text(encoding="utf-8") == "v2"


def test_download_uses_temp_then_rename(tmp_path: Path, remote: FakeSFTP) -> None:
    (remote.root / "src.txt").write_text("payload", encoding="utf-8")
    dest = tmp_path / "out.txt"
    sftp_client.download_file(
        remote, "/src.txt", str(dest), overwrite=False, mkdirs=False, err=io.StringIO()
    )
    assert dest.read_text(encoding="utf-8") == "payload"
    # No temp file left behind.
    assert not (tmp_path / ("out.txt" + sftp_client._TMP_SUFFIX)).exists()


def test_download_refuses_overwrite(tmp_path: Path, remote: FakeSFTP) -> None:
    (remote.root / "src.txt").write_text("payload", encoding="utf-8")
    dest = tmp_path / "out.txt"
    dest.write_text("existing", encoding="utf-8")
    with pytest.raises(FalcoError):
        sftp_client.download_file(
            remote, "/src.txt", str(dest), overwrite=False, mkdirs=False, err=io.StringIO()
        )
    assert dest.read_text(encoding="utf-8") == "existing"  # untouched


def test_download_missing_leaves_no_temp(tmp_path: Path, remote: FakeSFTP) -> None:
    dest = tmp_path / "out.txt"
    with pytest.raises(FalcoError):
        sftp_client.download_file(
            remote, "/nope.txt", str(dest), overwrite=False, mkdirs=False, err=io.StringIO()
        )
    assert not (tmp_path / ("out.txt" + sftp_client._TMP_SUFFIX)).exists()


def test_list_dir_outputs_entries(remote: FakeSFTP) -> None:
    (remote.root / "a.txt").write_text("x", encoding="utf-8")
    (remote.root / "sub").mkdir()
    out = io.StringIO()
    sftp_client.list_dir(remote, "/", out=out)
    text = out.getvalue()
    assert "a.txt" in text
    assert "sub/" in text


def test_mkdir_creates_nested(remote: FakeSFTP) -> None:
    sftp_client.make_dir(remote, "/a/b/c", err=io.StringIO())
    assert (remote.root / "a" / "b" / "c").is_dir()


def test_remove_file_and_refuse_dir(remote: FakeSFTP) -> None:
    (remote.root / "f.txt").write_text("x", encoding="utf-8")
    sftp_client.remove_file(remote, "/f.txt", err=io.StringIO())
    assert not (remote.root / "f.txt").exists()
    (remote.root / "d").mkdir()
    with pytest.raises(FalcoError):
        sftp_client.remove_file(remote, "/d", err=io.StringIO())


def test_move_and_overwrite_guard(remote: FakeSFTP) -> None:
    (remote.root / "a.txt").write_text("A", encoding="utf-8")
    sftp_client.move(remote, "/a.txt", "/b.txt", overwrite=False, mkdirs=False, err=io.StringIO())
    assert (remote.root / "b.txt").read_text(encoding="utf-8") == "A"
    (remote.root / "c.txt").write_text("C", encoding="utf-8")
    with pytest.raises(FalcoError):
        sftp_client.move(remote, "/c.txt", "/b.txt", overwrite=False, mkdirs=False, err=io.StringIO())
    sftp_client.move(remote, "/c.txt", "/b.txt", overwrite=True, mkdirs=False, err=io.StringIO())
    assert (remote.root / "b.txt").read_text(encoding="utf-8") == "C"


def test_upload_dir_preserves_structure(tmp_path: Path, remote: FakeSFTP) -> None:
    src = tmp_path / "site"
    (src / "css").mkdir(parents=True)
    (src / "index.html").write_text("<html>", encoding="utf-8")
    (src / "css" / "app.css").write_text("body{}", encoding="utf-8")
    sftp_client.upload_dir(remote, str(src), "/var/www", overwrite=False, err=io.StringIO())
    assert (remote.root / "var" / "www" / "index.html").read_text(encoding="utf-8") == "<html>"
    assert (remote.root / "var" / "www" / "css" / "app.css").read_text(encoding="utf-8") == "body{}"


def test_download_dir_preserves_structure(tmp_path: Path, remote: FakeSFTP) -> None:
    base = remote.root / "logs"
    (base / "2026").mkdir(parents=True)
    (base / "root.log").write_text("r", encoding="utf-8")
    (base / "2026" / "jan.log").write_text("j", encoding="utf-8")
    dest = tmp_path / "out"
    sftp_client.download_dir(remote, "/logs", str(dest), overwrite=False, err=io.StringIO())
    assert (dest / "root.log").read_text(encoding="utf-8") == "r"
    assert (dest / "2026" / "jan.log").read_text(encoding="utf-8") == "j"


# --------------------------------------------------------------------------- #
# Runtime integration: non-zero exit on SFTP failure
# --------------------------------------------------------------------------- #
def test_runtime_sftp_success_and_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "remote"
    root.mkdir()
    (root / "there.txt").write_text("ok", encoding="utf-8")
    fake = FakeSFTP(root)

    class FakeClient:
        def open_sftp(self):
            return fake

        def close(self) -> None:
            pass

    cfg = LauncherConfig.create(launcher_name="meks", host="h", username="u")
    monkeypatch.setattr(runtime.credentials, "resolve_password", lambda _c: "pw")
    monkeypatch.setattr(runtime.ssh_client, "connect", lambda _c, _p: FakeClient())

    ok = runtime.run(cfg, argv=["--download", "/there.txt", str(tmp_path / "got.txt")])
    assert ok == 0
    assert (tmp_path / "got.txt").read_text(encoding="utf-8") == "ok"

    # Missing remote file -> non-zero exit code.
    bad = runtime.run(cfg, argv=["--download", "/missing.txt", str(tmp_path / "x.txt")])
    assert bad != 0
