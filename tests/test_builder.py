"""Tests for launcher code generation and the PyInstaller command assembly.

The actual PyInstaller invocation is slow and OS-specific, so it runs in CI, not
here. These tests cover the pure, deterministic parts: the generated entry
script and the constructed command line.
"""

from __future__ import annotations

from pathlib import Path

from editor import builder
from editor.templates import render_entry_script
from shared.config import LauncherConfig


def test_entry_script_embeds_config_but_no_password() -> None:
    cfg = LauncherConfig.create(
        launcher_name="meks", host="10.0.0.5", username="deploy", port=2200
    )
    src = render_entry_script(cfg)
    assert "10.0.0.5" in src
    assert "deploy" in src
    assert "2200" in src
    assert cfg.credential_id in src
    # No password is embedded: the config passes no password argument and the
    # LauncherConfig type has no password field to hold one.
    assert "password=" not in src
    assert "password" not in {f.name for f in __import__("dataclasses").fields(cfg)}


def test_entry_script_is_valid_python() -> None:
    cfg = LauncherConfig.create(launcher_name="meks", host="h", username="u")
    compile(render_entry_script(cfg), "<generated>", "exec")


def test_pyinstaller_command_onefile_named_and_iconed(tmp_path: Path) -> None:
    cmd = builder.pyinstaller_command(
        entry_script=tmp_path / "entry.py",
        output_name="meks.exe",
        dist_dir=tmp_path / "dist",
        work_dir=tmp_path / "work",
        icon_path=tmp_path / "icon.ico",
        paths=[tmp_path / "repo"],
    )
    assert "--onefile" in cmd
    # ".exe" suffix stripped for PyInstaller's --name.
    assert cmd[cmd.index("--name") + 1] == "meks"
    assert "--icon" in cmd
    assert cmd[cmd.index("--icon") + 1] == str(tmp_path / "icon.ico")
    assert cmd[-1] == str(tmp_path / "entry.py")


def test_pyinstaller_command_without_icon(tmp_path: Path) -> None:
    cmd = builder.pyinstaller_command(
        entry_script=tmp_path / "entry.py",
        output_name="tool",
        dist_dir=tmp_path / "dist",
        work_dir=tmp_path / "work",
        icon_path=None,
        paths=[],
    )
    assert "--icon" not in cmd


def test_strip_exe_suffix_variants() -> None:
    assert builder._strip_exe_suffix("meks.exe") == "meks"
    assert builder._strip_exe_suffix("meks.app") == "meks"
    assert builder._strip_exe_suffix("meks") == "meks"
