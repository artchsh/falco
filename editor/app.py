"""Falco's desktop editor. Tk stays on the main thread; builds use queued events."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from queue import Empty, Queue
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from editor.builder import TARGETS, _current_os_key, discover_stubs, validate_output_name
from editor.build_jobs import BuildEvent, run_build
from shared.config import DEFAULT_SSH_PORT, LauncherConfig
from shared.errors import FalcoError
from shared.ssh_keys import MAX_KEY_BYTES

BG = "#F4F6FA"
INK = "#192A43"
MUTED = "#64748B"
BLUE = "#315AE8"


class FalcoEditor(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Falco · SSH launcher builder")
        self.geometry("860x820")
        self.minsize(800, 790)
        self.configure(background=BG)
        self._closing = False
        self._building = False
        self._events: Queue[BuildEvent] = Queue()
        self._controls: list[ttk.Widget] = []
        self._last_output_dir: Path | None = None
        self._stubs = discover_stubs()
        self._current_os = _current_os_key()
        self._configure_styles()
        self._build_form()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_id = self.after(80, self._poll_events)

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        font = "Helvetica Neue" if sys.platform == "darwin" else "Segoe UI" if sys.platform == "win32" else "DejaVu Sans"
        self._font = font
        self.option_add("*Font", (font, 11))
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=INK, font=(font, 11))
        style.configure("Muted.TLabel", foreground=MUTED, font=(font, 10))
        style.configure("Brand.TLabel", font=(font, 29, "bold"), foreground=INK)
        style.configure("Eyebrow.TLabel", font=(font, 9, "bold"), foreground=BLUE)
        style.configure("Card.TLabelframe", background="white", bordercolor="#DBE2ED", relief="solid", borderwidth=1)
        style.configure("Card.TLabelframe.Label", background="white", foreground=INK, font=(font, 12, "bold"))
        style.configure("Card.TFrame", background="white")
        style.configure("Card.TLabel", background="white", foreground=INK)
        style.configure("Hint.TLabel", background="white", foreground=MUTED, font=(font, 10))
        style.configure("TEntry", fieldbackground="white", foreground=INK, padding=7, bordercolor="#CCD5E2", lightcolor="#CCD5E2", darkcolor="#CCD5E2")
        style.map("TEntry", bordercolor=[("focus", BLUE)])
        style.configure("TButton", padding=(12, 7), foreground=INK, background="#E9EDF5", borderwidth=0, font=(font, 10))
        style.map("TButton", background=[("active", "#DCE4F3")])
        style.configure("Primary.TButton", padding=(22, 12), background=BLUE, foreground="white", font=(font, 11, "bold"))
        style.map("Primary.TButton", background=[("disabled", "#ADBCEB"), ("active", "#2548CA")], foreground=[("disabled", "white")])
        style.configure("TRadiobutton", background="white", foreground=INK, padding=(0, 4))
        style.configure("TCheckbutton", background="white", foreground=INK, padding=(0, 4))
        style.configure("Horizontal.TProgressbar", background=BLUE, troughcolor="#E1E7F2", borderwidth=0)

    def _entry(self, parent: ttk.Widget, variable: tk.StringVar, **kwargs) -> ttk.Entry:
        widget = ttk.Entry(parent, textvariable=variable, **kwargs)
        self._controls.append(widget)
        return widget

    def _button(self, parent: ttk.Widget, **kwargs) -> ttk.Button:
        widget = ttk.Button(parent, **kwargs)
        self._controls.append(widget)
        return widget

    def _card(self, parent: ttk.Widget, title: str) -> ttk.LabelFrame:
        return ttk.LabelFrame(parent, text=title, style="Card.TLabelframe", padding=18)

    def _build_form(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        body = ttk.Frame(self, padding=(28, 22))
        body.grid(sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(5, weight=1)
        header = ttk.Frame(body)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 20))
        ttk.Label(header, text="FALCO", style="Eyebrow.TLabel").pack(anchor="w")
        ttk.Label(header, text="One server. One launcher.", style="Brand.TLabel").pack(anchor="w", pady=(3, 4))
        ttk.Label(header, text="Create a portable SSH app for your team and AI agents.", style="Muted.TLabel").pack(anchor="w")

        self.var_name = tk.StringVar(value="server-client-X")
        self.var_host = tk.StringVar()
        self.var_port = tk.StringVar(value=str(DEFAULT_SSH_PORT))
        self.var_user = tk.StringVar()
        self._auto_output = "server-client-X.exe" if self._current_os == "windows" else "server-client-X"
        self.var_output = tk.StringVar(value=self._auto_output)
        self.var_directory = tk.StringVar()
        self.var_auth = tk.StringVar(value="password")
        self.var_key = tk.StringVar()
        self.var_vpn = tk.BooleanVar(value=False)
        self.var_all_platforms = tk.BooleanVar(value=bool(self._stubs and self._current_os not in self._stubs))

        cards = ttk.Frame(body)
        cards.grid(row=1, column=0, sticky="ew")
        cards.columnconfigure(0, weight=1, uniform="cards")
        cards.columnconfigure(1, weight=1, uniform="cards")
        connection = self._card(cards, "  Connection  ")
        connection.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        connection.columnconfigure(0, weight=1)
        address = ttk.Frame(connection, style="Card.TFrame")
        address.grid(row=0, column=0, sticky="ew")
        address.columnconfigure(0, weight=1)
        ttk.Label(address, text="Host / IP address", style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 5))
        ttk.Label(address, text="Port", style="Card.TLabel").grid(row=0, column=1, sticky="w", padx=(10, 0), pady=(0, 5))
        self.host_entry = self._entry(address, self.var_host)
        self.host_entry.grid(row=1, column=0, sticky="ew")
        self._entry(address, self.var_port, width=6).grid(row=1, column=1, padx=(10, 0))
        ttk.Label(connection, text="SSH username", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=(14, 5))
        self._entry(connection, self.var_user).grid(row=2, column=0, sticky="ew")
        vpn = ttk.Checkbutton(connection, text="Requires Tailscale / WireGuard / VPN", variable=self.var_vpn)
        vpn.grid(row=3, column=0, sticky="w", pady=(13, 0))
        self._controls.append(vpn)
        ttk.Label(connection, text="Adds a network reminder for your AI agents.", style="Hint.TLabel").grid(row=4, column=0, sticky="w", pady=(2, 0))

        auth = self._card(cards, "  Authentication  ")
        auth.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        auth.columnconfigure(0, weight=1)
        options = ttk.Frame(auth, style="Card.TFrame")
        options.grid(row=0, column=0, sticky="ew")
        for column, (label, value) in enumerate((("Password", "password"), ("Encrypted key", "private_key"))):
            radio = ttk.Radiobutton(options, text=label, variable=self.var_auth, value=value, command=self._auth_changed)
            radio.grid(row=0, column=column, padx=(0, 15), sticky="w")
            self._controls.append(radio)
        ttk.Label(auth, text="OpenSSH private key", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=(12, 5))
        key_row = ttk.Frame(auth, style="Card.TFrame")
        key_row.grid(row=2, column=0, sticky="ew")
        key_row.columnconfigure(0, weight=1)
        self.key_entry = self._entry(key_row, self.var_key, width=15)
        self.key_entry.grid(row=0, column=0, sticky="ew")
        self.key_button = self._button(key_row, text="Choose…", command=self._pick_key)
        self.key_button.grid(row=0, column=1, padx=(7, 0))
        self.auth_hint = tk.StringVar()
        ttk.Label(auth, textvariable=self.auth_hint, style="Hint.TLabel", wraplength=320, justify="left").grid(row=3, column=0, sticky="w", pady=(13, 0))

        output = self._card(body, "  Output  ")
        output.grid(row=2, column=0, sticky="ew", pady=(18, 0))
        output.columnconfigure(0, weight=1)
        output.columnconfigure(1, weight=1)
        ttk.Label(output, text="Launcher name", style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 5))
        ttk.Label(output, text="Output filename", style="Card.TLabel").grid(row=0, column=1, sticky="w", padx=(16, 0), pady=(0, 5))
        self._entry(output, self.var_name).grid(row=1, column=0, sticky="ew")
        self._entry(output, self.var_output).grid(row=1, column=1, sticky="ew", padx=(16, 0))
        platforms = ttk.Frame(output, style="Card.TFrame")
        platforms.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 3))
        label = {"macos": "macOS", "windows": "Windows", "linux": "Linux"}[self._current_os]
        self.current_radio = ttk.Radiobutton(platforms, text=f"This computer ({label})", variable=self.var_all_platforms, value=False)
        self.current_radio.grid(row=0, column=0, sticky="w")
        self.all_radio = ttk.Radiobutton(platforms, text="All available platforms", variable=self.var_all_platforms, value=True)
        self.all_radio.grid(row=0, column=1, padx=(20, 0), sticky="w")
        self._controls.extend((self.current_radio, self.all_radio))
        available = ", ".join({"macos": "macOS", "windows": "Windows", "linux": "Linux"}[t.key] for t in TARGETS if t.key in self._stubs)
        ttk.Label(output, text=f"Available: {available}" if available else "No launcher files bundled. Build the Rust launcher or use a packaged editor.", style="Hint.TLabel").grid(row=3, column=0, columnspan=2, sticky="w")
        folder = ttk.Frame(output, style="Card.TFrame")
        folder.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(13, 0))
        folder.columnconfigure(0, weight=1)
        self._entry(folder, self.var_directory).grid(row=0, column=0, sticky="ew")
        self._button(folder, text="Output folder…", command=self._pick_directory).grid(row=0, column=1, padx=(8, 0))

        actions = ttk.Frame(body)
        actions.grid(row=3, column=0, sticky="ew", pady=(18, 10))
        actions.columnconfigure(0, weight=1)
        ttk.Label(actions, text="Password and passphrase stay in your OS keychain.\nNo secrets are entered in the editor.", style="Muted.TLabel", justify="left").grid(row=0, column=0, sticky="w")
        self.build_button = ttk.Button(actions, text="Build launcher", style="Primary.TButton", command=self._on_build)
        self.build_button.grid(row=0, column=1, sticky="e")
        activity = ttk.Frame(body)
        activity.grid(row=4, column=0, sticky="ew")
        activity.columnconfigure(0, weight=1)
        self.status = tk.StringVar(value="Ready to build.")
        self.status_label = ttk.Label(activity, textvariable=self.status, wraplength=610)
        self.status_label.grid(row=0, column=0, sticky="w", pady=(0, 7))
        self.open_button = ttk.Button(activity, text="Open output folder", command=self._open_output, state="disabled")
        self.open_button.grid(row=0, column=1, sticky="e", padx=(12, 0), pady=(0, 7))
        self.progress = ttk.Progressbar(activity, mode="indeterminate")
        self.progress.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.log = ScrolledText(body, height=5, wrap="word", background="white", foreground=MUTED,
                                font=("Menlo" if sys.platform == "darwin" else "Consolas", 10),
                                relief="flat", borderwidth=0, padx=12, pady=10, state="disabled")
        self.log.grid(row=5, column=0, sticky="nsew", pady=(12, 0))
        self.log.tag_configure("error", foreground="#B42318")
        self.log.tag_configure("success", foreground="#16724C")
        self.var_name.trace_add("write", self._name_changed)
        self.var_all_platforms.trace_add("write", lambda *_: self._update_platforms())
        self._auth_changed()
        self._update_platforms()
        self.host_entry.focus_set()

    def _name_changed(self, *_args) -> None:
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", self.var_name.get().strip()).strip(".-") or "server-client"
        suggested = slug + (".exe" if self._current_os == "windows" else "")
        if self.var_output.get() == self._auto_output:
            self.var_output.set(suggested)
        self._auto_output = suggested

    def _auth_changed(self) -> None:
        is_key = self.var_auth.get() == "private_key"
        for control in (self.key_entry, self.key_button):
            control.state(["!disabled" if is_key and not self._building else "disabled"])
        self.auth_hint.set("Encrypted key travels with the launcher. Its passphrase is entered privately on first use." if is_key else "Password is entered privately on first use, then saved in the OS credential store.")

    def _update_platforms(self) -> None:
        if not self._building:
            self.current_radio.state(["disabled" if self._stubs and self._current_os not in self._stubs else "!disabled"])
            self.all_radio.state(["!disabled" if self._stubs else "disabled"])
        self.build_button.configure(text="Build all launchers" if self.var_all_platforms.get() else "Build launcher")

    def _pick_key(self) -> None:
        path = filedialog.askopenfilename(title="Choose an encrypted OpenSSH private key", parent=self)
        if path:
            self.var_key.set(path)

    def _pick_directory(self) -> None:
        path = filedialog.askdirectory(title="Choose output folder", parent=self, initialdir=self.var_directory.get() or str(Path.home()))
        if path:
            self.var_directory.set(path)

    def _collect_config(self) -> LauncherConfig:
        try:
            port = int(self.var_port.get().strip())
        except ValueError as exc:
            raise FalcoError("SSH port must be a number between 1 and 65535.") from exc
        key = None
        if self.var_auth.get() == "private_key":
            if not self.var_key.get().strip():
                raise FalcoError("Choose an encrypted OpenSSH private-key file.")
            try:
                with Path(self.var_key.get()).open("rb") as stream:
                    raw = stream.read(MAX_KEY_BYTES + 1)
                if len(raw) > MAX_KEY_BYTES:
                    raise FalcoError("Private key must be smaller than 128 KiB.")
                key = raw.decode("utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise FalcoError("Could not read the selected key. Choose a readable encrypted OpenSSH text file.") from exc
        return LauncherConfig.create(launcher_name=self.var_name.get(), host=self.var_host.get(), username=self.var_user.get(), port=port,
                                     auth_method=self.var_auth.get(), encrypted_private_key=key, requires_vpn=self.var_vpn.get())

    def _on_build(self) -> None:
        if self._building or self._closing:
            return
        try:
            config = self._collect_config()
            base = validate_output_name(self.var_output.get().strip())
        except FalcoError as exc:
            messagebox.showerror("Check your settings", str(exc), parent=self)
            return
        if not self.var_directory.get().strip():
            self._pick_directory()
        if not self.var_directory.get().strip():
            return
        try:
            directory = Path(self.var_directory.get()).expanduser().resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Check output folder", f"Could not resolve the output folder: {exc}. Choose a valid folder and try again.", parent=self)
            return
        all_platforms = self.var_all_platforms.get()
        names = [f"{base}-{t.key}{t.output_ext}" for t in TARGETS if t.key in self._stubs] if all_platforms else [base + (".exe" if self._current_os == "windows" else "")]
        existing = [name for name in [*names, "how-to-use.md"] if (directory / name).exists()]
        if existing and not messagebox.askyesno("Replace existing files?", "These files already exist:\n" + "\n".join(existing) + "\n\nReplace them with this build?", parent=self):
            return
        self._last_output_dir = directory
        self._clear_log()
        self._set_building(True)
        self._append_log(f"Building for {config.username}@{config.host}:{config.port}")
        self.status.set("Building launchers…" if all_platforms else "Building launcher…")
        self.status_label.configure(foreground=INK)
        try:
            threading.Thread(target=run_build, args=(config, self.var_output.get().strip(), directory, all_platforms, self._events), daemon=True).start()
        except RuntimeError as exc:
            self._events.put(BuildEvent(kind="error", message=f"Could not start build worker: {exc}. Close unused applications and try again."))
            self._drain_events()

    def _set_building(self, building: bool) -> None:
        self._building = building
        for control in [*self._controls, self.build_button]:
            control.state(["disabled" if building else "!disabled"])
        if building:
            self.progress.start(12)
            self.open_button.state(["disabled"])
        else:
            self.progress.stop()
            self._auth_changed()
            self._update_platforms()

    def _poll_events(self) -> None:
        if self._closing:
            return
        self._drain_events()
        if not self._closing:
            self._poll_id = self.after(80, self._poll_events)

    def _drain_events(self) -> None:
        if self._closing:
            return
        try:
            while True:
                event = self._events.get_nowait()
                if event.kind == "progress":
                    self._append_log(event.message)
                elif event.kind == "error":
                    self._set_building(False)
                    self._append_log(event.message, "error")
                    self.status.set("Build failed. Check the details below and try again.")
                    self.status_label.configure(foreground="#B42318")
                    messagebox.showerror("Build failed", event.message, parent=self)
                elif event.kind == "success":
                    self._set_building(False)
                    self.status.set(f"Built {len(event.paths)} launcher{'s' if len(event.paths) != 1 else ''}. Ready to share.")
                    self.status_label.configure(foreground="#16724C")
                    for path in event.paths:
                        self._append_log(f"Ready: {path}", "success")
                    self._append_log(f"Agent instructions: {event.guide}")
                    if event.skipped:
                        note = "Unavailable platforms: " + ", ".join(event.skipped)
                        self._append_log(note)
                        self.status.set(self.status.get() + " " + note)
                    self.open_button.state(["!disabled"])
        except Empty:
            pass

    def _append_log(self, message: str, tag: str = "") -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _open_output(self) -> None:
        if self._last_output_dir:
            try:
                if sys.platform == "win32":
                    os.startfile(str(self._last_output_dir))
                else:
                    subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(self._last_output_dir)])
            except OSError:
                messagebox.showerror("Could not open folder", str(self._last_output_dir), parent=self)

    def _on_close(self) -> None:
        self._closing = True
        if getattr(self, "_poll_id", None):
            self.after_cancel(self._poll_id)
        self.destroy()


def launch() -> int:
    FalcoEditor().mainloop()
    return 0
