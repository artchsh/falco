"""SFTP file operations over the same connection Falco uses for commands.

Everything here runs on a :class:`paramiko.SFTPClient` obtained from the shared,
password-authenticated SSH connection (see ``launcher/ssh_client.py``). It
therefore reuses the exact same stored keychain credential and the same
auto-accept host-key behaviour as command execution.

Guarantees required by the spec and enforced here:

* transfer progress is streamed live to the terminal (stderr);
* file names and directory structure are preserved on recursive transfers;
* missing destination directories are created when ``--mkdirs`` is given (and
  always for the subdirectories of a recursive transfer);
* existing files are never overwritten unless ``--overwrite`` is supplied;
* downloads are written to a temporary file and renamed only after the transfer
  completes, so an interrupted download never leaves a truncated file in place;
* any failure raises :class:`FalcoError`, which the runtime turns into a
  non-zero exit code.
"""

from __future__ import annotations

import os
import posixpath
import stat as stat_module
import sys
from typing import TYPE_CHECKING, TextIO

from launcher.cli import SftpAction, SftpOp
from shared.errors import FalcoError

if TYPE_CHECKING:  # pragma: no cover - typing only
    import paramiko

# Suffix for the in-progress download file, renamed away on success.
_TMP_SUFFIX = ".falcopart"


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _human(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            return f"{int(value)}B" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024.0
    return f"{num}B"


class _Progress:
    """A paramiko transfer callback that redraws a single progress line."""

    def __init__(self, label: str, stream: TextIO) -> None:
        self._label = label
        self._stream = stream

    def __call__(self, transferred: int, total: int) -> None:
        pct = (transferred / total * 100.0) if total else 100.0
        self._stream.write(
            f"\r  {self._label}  {pct:5.1f}%  {_human(transferred)}/{_human(total)}    "
        )
        self._stream.flush()

    def finish(self) -> None:
        self._stream.write("\n")
        self._stream.flush()


def _exists(sftp: "paramiko.SFTPClient", path: str) -> bool:
    try:
        sftp.stat(path)
        return True
    except IOError:
        return False


def _is_dir(sftp: "paramiko.SFTPClient", path: str) -> bool:
    try:
        return stat_module.S_ISDIR(sftp.stat(path).st_mode)
    except IOError:
        return False


def _makedirs_remote(sftp: "paramiko.SFTPClient", path: str) -> None:
    """Create ``path`` and any missing parents on the remote (mkdir -p)."""

    path = posixpath.normpath(path)
    if path in ("", "/", "."):
        return
    parent = posixpath.dirname(path)
    if parent and parent != path and not _is_dir(sftp, parent):
        _makedirs_remote(sftp, parent)
    if not _is_dir(sftp, path):
        try:
            sftp.mkdir(path)
        except IOError as exc:
            if not _is_dir(sftp, path):  # lost a race? tolerate; else fail loudly
                raise FalcoError(f"Could not create remote directory {path}: {exc}") from exc


# --------------------------------------------------------------------------- #
# Single-item operations
# --------------------------------------------------------------------------- #
def upload_file(
    sftp: "paramiko.SFTPClient",
    local: str,
    remote: str,
    *,
    overwrite: bool,
    mkdirs: bool,
    err: TextIO,
) -> None:
    if not os.path.isfile(local):
        raise FalcoError(f"Local file not found: {local}")

    dest = remote
    if _is_dir(sftp, remote):
        dest = posixpath.join(remote, os.path.basename(local))
    if mkdirs:
        _makedirs_remote(sftp, posixpath.dirname(dest))
    if not overwrite and _exists(sftp, dest):
        raise FalcoError(f"Remote file already exists (use --overwrite): {dest}")

    progress = _Progress(f"upload {os.path.basename(local)} -> {dest}", err)
    sftp.put(local, dest, callback=progress)
    progress.finish()


def download_file(
    sftp: "paramiko.SFTPClient",
    remote: str,
    local: str,
    *,
    overwrite: bool,
    mkdirs: bool,
    err: TextIO,
) -> None:
    if not _exists(sftp, remote):
        raise FalcoError(f"Remote file not found: {remote}")
    if _is_dir(sftp, remote):
        raise FalcoError(f"Remote path is a directory (use --download-dir): {remote}")

    dest = local
    if os.path.isdir(local):
        dest = os.path.join(local, posixpath.basename(remote))
    if mkdirs:
        parent = os.path.dirname(os.path.abspath(dest))
        os.makedirs(parent, exist_ok=True)
    if not overwrite and os.path.exists(dest):
        raise FalcoError(f"Local file already exists (use --overwrite): {dest}")

    tmp = dest + _TMP_SUFFIX
    progress = _Progress(f"download {posixpath.basename(remote)} -> {dest}", err)
    try:
        sftp.get(remote, tmp, callback=progress)
        progress.finish()
        os.replace(tmp, dest)  # atomic; only after a complete transfer
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def list_dir(sftp: "paramiko.SFTPClient", remote: str, *, out: TextIO) -> None:
    if not _exists(sftp, remote):
        raise FalcoError(f"Remote path not found: {remote}")
    try:
        entries = sftp.listdir_attr(remote)
    except IOError as exc:
        raise FalcoError(f"Could not list {remote}: {exc}") from exc

    for attr in sorted(entries, key=lambda a: a.filename):
        is_dir = stat_module.S_ISDIR(attr.st_mode or 0)
        kind = "d" if is_dir else "-"
        size = attr.st_size or 0
        name = attr.filename + ("/" if is_dir else "")
        out.write(f"{kind} {size:>12}  {name}\n")
    out.flush()


def make_dir(sftp: "paramiko.SFTPClient", remote: str, *, err: TextIO) -> None:
    _makedirs_remote(sftp, remote)
    err.write(f"created {remote}\n")
    err.flush()


def remove_file(sftp: "paramiko.SFTPClient", remote: str, *, err: TextIO) -> None:
    if not _exists(sftp, remote):
        raise FalcoError(f"Remote file not found: {remote}")
    if _is_dir(sftp, remote):
        raise FalcoError(f"Refusing to remove a directory: {remote}")
    sftp.remove(remote)
    err.write(f"removed {remote}\n")
    err.flush()


def move(
    sftp: "paramiko.SFTPClient",
    src: str,
    dst: str,
    *,
    overwrite: bool,
    mkdirs: bool,
    err: TextIO,
) -> None:
    if not _exists(sftp, src):
        raise FalcoError(f"Remote source not found: {src}")

    dest = dst
    if _is_dir(sftp, dst):
        dest = posixpath.join(dst, posixpath.basename(src.rstrip("/")))
    if mkdirs:
        _makedirs_remote(sftp, posixpath.dirname(dest))

    if _exists(sftp, dest):
        if not overwrite:
            raise FalcoError(f"Remote destination already exists (use --overwrite): {dest}")
        try:
            sftp.posix_rename(src, dest)  # atomic replace where supported
            err.write(f"moved {src} -> {dest}\n")
            err.flush()
            return
        except (IOError, AttributeError):
            sftp.remove(dest)

    sftp.rename(src, dest)
    err.write(f"moved {src} -> {dest}\n")
    err.flush()


# --------------------------------------------------------------------------- #
# Recursive operations
# --------------------------------------------------------------------------- #
def upload_dir(
    sftp: "paramiko.SFTPClient",
    local_dir: str,
    remote_dir: str,
    *,
    overwrite: bool,
    err: TextIO,
) -> None:
    if not os.path.isdir(local_dir):
        raise FalcoError(f"Local directory not found: {local_dir}")

    local_root = os.path.abspath(local_dir)
    _makedirs_remote(sftp, remote_dir)

    for root, _dirs, files in os.walk(local_root):
        rel = os.path.relpath(root, local_root)
        remote_sub = (
            remote_dir if rel == "." else posixpath.join(remote_dir, rel.replace(os.sep, "/"))
        )
        _makedirs_remote(sftp, remote_sub)
        for name in sorted(files):
            local_path = os.path.join(root, name)
            remote_path = posixpath.join(remote_sub, name)
            if not overwrite and _exists(sftp, remote_path):
                raise FalcoError(f"Remote file already exists (use --overwrite): {remote_path}")
            label = name if rel == "." else posixpath.join(rel.replace(os.sep, "/"), name)
            progress = _Progress(f"upload {label}", err)
            sftp.put(local_path, remote_path, callback=progress)
            progress.finish()


def download_dir(
    sftp: "paramiko.SFTPClient",
    remote_dir: str,
    local_dir: str,
    *,
    overwrite: bool,
    err: TextIO,
) -> None:
    if not _exists(sftp, remote_dir):
        raise FalcoError(f"Remote directory not found: {remote_dir}")
    if not _is_dir(sftp, remote_dir):
        raise FalcoError(f"Remote path is not a directory (use --download): {remote_dir}")

    stack: list[tuple[str, str]] = [(remote_dir, local_dir)]
    while stack:
        rdir, ldir = stack.pop()
        os.makedirs(ldir, exist_ok=True)
        for attr in sorted(sftp.listdir_attr(rdir), key=lambda a: a.filename):
            remote_path = posixpath.join(rdir, attr.filename)
            local_path = os.path.join(ldir, attr.filename)
            if stat_module.S_ISDIR(attr.st_mode or 0):
                stack.append((remote_path, local_path))
                continue
            if not overwrite and os.path.exists(local_path):
                raise FalcoError(f"Local file already exists (use --overwrite): {local_path}")
            tmp = local_path + _TMP_SUFFIX
            progress = _Progress(f"download {attr.filename}", err)
            try:
                sftp.get(remote_path, tmp, callback=progress)
                progress.finish()
                os.replace(tmp, local_path)
            except BaseException:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #
def execute(
    sftp: "paramiko.SFTPClient",
    op: SftpOp,
    *,
    overwrite: bool,
    mkdirs: bool,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> None:
    """Run one parsed SFTP operation, raising :class:`FalcoError` on failure."""

    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    a = op.action
    args = op.operands

    if a is SftpAction.UPLOAD:
        upload_file(sftp, args[0], args[1], overwrite=overwrite, mkdirs=mkdirs, err=err)
    elif a is SftpAction.DOWNLOAD:
        download_file(sftp, args[0], args[1], overwrite=overwrite, mkdirs=mkdirs, err=err)
    elif a is SftpAction.LIST:
        list_dir(sftp, args[0], out=out)
    elif a is SftpAction.MKDIR:
        make_dir(sftp, args[0], err=err)
    elif a is SftpAction.REMOVE:
        remove_file(sftp, args[0], err=err)
    elif a is SftpAction.MOVE:
        move(sftp, args[0], args[1], overwrite=overwrite, mkdirs=mkdirs, err=err)
    elif a is SftpAction.UPLOAD_DIR:
        upload_dir(sftp, args[0], args[1], overwrite=overwrite, err=err)
    elif a is SftpAction.DOWNLOAD_DIR:
        download_dir(sftp, args[0], args[1], overwrite=overwrite, err=err)
    else:  # pragma: no cover - exhaustive
        raise FalcoError(f"Unknown SFTP action: {a}")
