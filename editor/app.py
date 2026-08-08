"""The Falco Editor GUI (Tkinter).

Tkinter is chosen because it ships with CPython, adds no runtime dependency, and
is available on Windows, macOS and Linux. The window collects the launcher
fields, validates them into a :class:`LauncherConfig`, and runs the build on a
background thread so the UI stays responsive. Build progress is streamed live
into a log pane at the bottom so the user can see what the builder is doing.
"""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from editor.builder import build_all_launchers, build_launcher
from shared.config import DEFAULT_SSH_PORT, LauncherConfig
from shared.errors import FalcoError


class FalcoEditor(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Falco Editor")
        self.minsize(620, 560)
        self._icon_path: Path | None = None
        self._build_form()

    # -- UI construction -------------------------------------------------- #
    def _build_form(self) -> None:
        pad = {"padx": 8, "pady": 6}
        frame = ttk.Frame(self, padding=16)
        frame.grid(sticky="nsew")
        # Let the frame (and its log row) grow with the window.
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

        self.var_name = tk.StringVar(value="meks")
        self.var_host = tk.StringVar()
        self.var_port = tk.StringVar(value=str(DEFAULT_SSH_PORT))
        self.var_user = tk.StringVar()
        self.var_output = tk.StringVar(value="meks.exe")
        self.var_icon = tk.StringVar(value="")
        self.var_all_platforms = tk.BooleanVar(value=False)

        rows = [
            ("Launcher name", self.var_name),
            ("SSH host / IP", self.var_host),
            ("SSH port", self.var_port),
            ("SSH username", self.var_user),
            ("Output filename", self.var_output),
        ]
        r = 0
        for label, var in rows:
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

        ttk.Checkbutton(
            frame,
            text="Build for all platforms (Windows .exe, macOS, Linux)",
            variable=self.var_all_platforms,
        ).grid(row=r, column=0, columnspan=2, sticky="w", **pad)
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

        # Progress bar + short status line.
        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.grid(row=r, column=0, columnspan=2, sticky="ew", **pad)
        r += 1

        self.status = tk.StringVar(value="Ready.")
        ttk.Label(frame, textvariable=self.status, foreground="#333").grid(
            row=r, column=0, columnspan=2, sticky="w", **pad
        )
        r += 1

        # Live build log — this is the "what is the builder doing now" pane.
        ttk.Label(frame, text="Build output").grid(row=r, column=0, sticky="w", **pad)
        r += 1
        self.log = ScrolledText(frame, height=12, wrap="none", state="disabled",
                                font=("Consolas", 9))
        self.log.grid(row=r, column=0, columnspan=2, sticky="nsew", **pad)
        frame.rowconfigure(r, weight=1)

    # -- Log helpers ------------------------------------------------------ #
    def _append_log(self, message: str) -> None:
        """Append a line to the build log and keep the newest visible."""

        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
        # Mirror the latest line into the one-line status for a quick glance.
        self.status.set(message if len(message) <= 90 else message[:87] + "…")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

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
        all_platforms = self.var_all_platforms.get()
        out_dir = filedialog.askdirectory(title="Choose output folder")
        if not out_dir:
            return

        self.build_button.configure(state="disabled")
        self._clear_log()
        self.progress.start(12)
        target = "all platforms" if all_platforms else output_name
        self._append_log(
            f"Building '{target}' for {config.username}@{config.host}:{config.port}"
        )

        def on_progress(message: str) -> None:
            # Called from the worker thread; marshal onto the UI thread.
            self.after(0, lambda m=message: self._append_log(m))

        def worker() -> None:
            try:
                if all_platforms:
                    multi = build_all_launchers(
                        config,
                        base_name=output_name,
                        output_dir=out_dir,
                        progress=on_progress,
                    )
                    paths = "\n".join(str(p) for p in multi.executables)
                    if multi.skipped:
                        on_progress(
                            "Note: no bundled stub for: " + ", ".join(multi.skipped)
                        )
                    self.after(0, lambda: self._build_done(
                        path=paths, guide=str(multi.how_to_use)))
                else:
                    result = build_launcher(
                        config,
                        output_name=output_name,
                        icon_path=icon,
                        output_dir=out_dir,
                        progress=on_progress,
                    )
                    self.after(0, lambda: self._build_done(
                        path=str(result.executable), guide=str(result.how_to_use)))
            except FalcoError as exc:
                self.after(0, lambda: self._build_done(error=str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _build_done(
        self, *, path: str | None = None, guide: str | None = None, error: str | None = None
    ) -> None:
        self.progress.stop()
        self.build_button.configure(state="normal")
        if error:
            self._append_log("BUILD FAILED.")
            self.status.set("Build failed — see output below.")
            messagebox.showerror("Build failed", error)
        else:
            self._append_log(f"DONE. Executable written to: {path}")
            if guide:
                self._append_log(f"Usage guide: {guide}")
            self.status.set(f"Built: {path}")
            messagebox.showinfo(
                "Build complete",
                f"Launcher written to:\n{path}\n\nUsage guide:\n{guide}",
            )


def launch() -> int:
    FalcoEditor().mainloop()
    return 0
