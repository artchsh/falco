"""Interactive SSH shell with a pseudo-terminal.

Started when the launcher is invoked with no command arguments. It requests a
remote PTY and bridges the local terminal to it so that command history,
tab-completion, colours/ANSI sequences, ``sudo`` prompts and (where the platform
allows) full-screen apps such as vim/htop work.

Two implementations are provided:

* POSIX (Linux/macOS): puts the local tty in raw mode and uses ``select`` to
  shuttle bytes both ways. Handles window-resize via ``SIGWINCH`` and always
  restores the terminal in a ``finally`` block, even after an error.
* Windows: a threaded reader/writer fallback. It covers normal line input,
  colours and Ctrl+C/Ctrl+D; classic Windows consoles may not render every
  full-screen TUI, which the spec explicitly allows to slip past the MVP.
"""

from __future__ import annotations

import sys
import threading

import paramiko

from shared.config import LauncherConfig

_CHUNK = 32768


def _term_size() -> tuple[int, int]:
    """Return (columns, rows) of the local terminal, with a sane fallback."""

    import shutil

    size = shutil.get_terminal_size(fallback=(80, 24))
    return size.columns, size.lines


def start_interactive_shell(client: paramiko.SSHClient, config: LauncherConfig) -> int:
    """Open an interactive shell. Returns 0 on a clean disconnect."""

    cols, rows = _term_size()
    channel = client.invoke_shell(term=_term_type(), width=cols, height=rows)
    try:
        if sys.platform == "win32":
            return _interactive_windows(channel)
        return _interactive_posix(channel)
    finally:
        channel.close()


def _term_type() -> str:
    import os

    return os.environ.get("TERM", "xterm-256color")


# --------------------------------------------------------------------------- #
# POSIX
# --------------------------------------------------------------------------- #
def _interactive_posix(channel: paramiko.Channel) -> int:
    import select
    import signal
    import termios
    import tty

    stdin_fd = sys.stdin.fileno()
    old_attrs = termios.tcgetattr(stdin_fd)

    def _on_resize(_signum: int, _frame: object) -> None:
        cols, rows = _term_size()
        try:
            channel.resize_pty(width=cols, height=rows)
        except paramiko.SSHException:
            pass

    previous_winch = signal.getsignal(signal.SIGWINCH)
    signal.signal(signal.SIGWINCH, _on_resize)
    try:
        tty.setraw(stdin_fd)
        tty.setcbreak(stdin_fd)
        channel.settimeout(0.0)
        while True:
            ready, _, _ = select.select([channel, sys.stdin], [], [])
            if channel in ready:
                try:
                    data = channel.recv(_CHUNK)
                except paramiko.SSHException:
                    break
                if not data:
                    break  # remote closed
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
            if sys.stdin in ready:
                data = sys.stdin.buffer.read1(_CHUNK)
                if not data:
                    # local EOF (Ctrl+D on an empty line already forwarded)
                    channel.shutdown_write()
                else:
                    channel.sendall(data)
    finally:
        # Always restore the terminal, even after an exception/disconnect.
        termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_attrs)
        signal.signal(signal.SIGWINCH, previous_winch)

    return channel.recv_exit_status() if channel.exit_status_ready() else 0


# --------------------------------------------------------------------------- #
# Windows
# --------------------------------------------------------------------------- #
def _interactive_windows(channel: paramiko.Channel) -> int:
    """Threaded bridge for Windows consoles (no termios/select-on-stdin)."""

    channel.settimeout(0.0)
    stop = threading.Event()

    def _reader() -> None:
        while not stop.is_set():
            try:
                data = channel.recv(_CHUNK)
            except Exception:
                continue
            if not data:
                stop.set()
                break
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()

    try:
        while not stop.is_set():
            try:
                line = sys.stdin.buffer.readline()
            except KeyboardInterrupt:
                # Forward Ctrl+C as ETX to the remote shell.
                channel.sendall(b"\x03")
                continue
            if not line:  # Ctrl+D / EOF
                channel.shutdown_write()
                break
            channel.sendall(line)
    finally:
        stop.set()
        reader.join(timeout=1.0)

    return channel.recv_exit_status() if channel.exit_status_ready() else 0
