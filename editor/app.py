"""The Falco Editor GUI (Tkinter).

Tkinter is chosen because it ships with CPython, adds no runtime dependency, and
is available on Windows, macOS and Linux. The window collects the launcher
fields, validates them into a :class:`LauncherConfig`, and runs the build on a
background thread so the UI stays responsive.
"""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from editor.builder import build_launcher
from shared.config import DEFAULT_SSH_PORT, LauncherConfig
from shared.errors import FalcoError


class FalcoEditor(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Falco Editor")
        self.minsize(560, 420)
        self._icon_path: Path | None = None
        self._build_form()

    # -- UI construction -------------------------------------------------- #
    def _build_form(self) -> None:
        pad = {"padx": 8, "pady": 6}
        frame = ttk.Frame(self, padding=16)
        frame.grid(sticky="nsew")
        self.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

        self.var_name = tk.StringVar(value="meks")
        self.var_host = tk.StringVar()
        self.var_port = tk.StringVar(value=str(DEFAULT_SSH_PORT))
        self.var_user = tk.StringVar()
        self.var_output = tk.StringVar(value="meks.exe")
        self.var_icon = tk.StringVar(value="")

        rows = [
            ("Launcher name", self.var_name, None),
            ("SSH host / IP", self.var_host, None),
            ("SSH port", self.var_port, None),
            ("SSH username", self.var_user, None),
            ("Output filename", self.var_output, None),
        ]
        r = 0
        for label, var, _ in rows:
            ttk.Label(frame, text=label).grid(row=r, column=0, sticky="w", **pad)
            ttk.Entry(frame, textvariable=var).grid(row=r, column=1, sticky="ew", **pad)
            r += 1

        ttk.Label(frame, text="Executable icon").grid(row=r, column=0, sticky="w", **pad)
        icon_row = ttk.Frame(frame)
        icon_row.grid(row=r, column=1, sticky="ew", **pad)
        icon_row.columnconfigure(0, weight=1)
        ttk.Entry(icon_row, textvariable=self.var_icon).grid(row=0, column=0, sticky="ew")
        ttk.Button(icon_row, text="Browse…", command=self._pick_icon).grid(row=0, column=1, padx=(6, 0))
        r += 1

        ttk.Label(
            frame,
            text="The SSH password is NOT stored in the launcher. It is entered\n"
                 "on first run and saved to the OS credential store.",
            foreground="#555",
            justify="left",
        ).grid(row=r, column=0, columnspan=2, sticky="w", **pad)
        r += 1

        self.build_button = ttk.Button(frame, text="Build launcher", command=self._on_build)
        self.build_button.grid(row=r, column=0, columnspan=2, sticky="ew", **pad)
        r += 1

        self.status = tk.StringVar(value="Ready.")
        ttk.Label(frame, textvariable=self.status, foreground="#333").grid(
            row=r, column=0, columnspan=2, sticky="w", **pad
        )

    # -- Actions ---------------------------------------------------------- #
    def _pick_icon(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose an icon",
            filetypes=[("Icons", "*.ico *.icns *.png"), ("All files", "*.*")],
        )
        if path:
            self.var_icon.set(path)

    def _collect_config(self) -> LauncherConfig:
        try:
            port = int(self.var_port.get().strip())
        except ValueError as exc:
            raise FalcoError("SSH port must be a number.") from exc
        return LauncherConfig.create(
            launcher_name=self.var_name.get(),
            host=self.var_host.get(),
            username=self.var_user.get(),
            port=port,
        )

    def _on_build(self) -> None:
        try:
            config = self._collect_config()
        except FalcoError as exc:
            messagebox.showerror("Invalid configuration", str(exc))
            return

        output_name = self.var_output.get().strip() or f"{config.launcher_name}.exe"
        icon = self.var_icon.get().strip() or None
        out_dir = filedialog.askdirectory(title="Choose output folder")
        if not out_dir:
            return

        self.build_button.configure(state="disabled")
        self.status.set("Building… this can take a minute.")

        def worker() -> None:
            try:
                result = build_launcher(
                    config,
                    output_name=output_name,
                    icon_path=icon,
                    output_dir=out_dir,
                )
            except FalcoError as exc:
                self.after(0, lambda: self._build_done(error=str(exc)))
            else:
                self.after(0, lambda: self._build_done(path=str(result.executable)))

        threading.Thread(target=worker, daemon=True).start()

    def _build_done(self, *, path: str | None = None, error: str | None = None) -> None:
        self.build_button.configure(state="normal")
        if error:
            self.status.set("Build failed.")
            messagebox.showerror("Build failed", error)
        else:
            self.status.set(f"Built: {path}")
            messagebox.showinfo("Build complete", f"Launcher written to:\n{path}")


def launch() -> int:
    FalcoEditor().mainloop()
    return 0
