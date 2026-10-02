"""Background builds report values through a queue; this module never uses Tk."""
from dataclasses import dataclass
from pathlib import Path
from queue import Queue

from editor.builder import build_all_launchers, build_launcher
from shared.config import LauncherConfig
from shared.errors import FalcoError


@dataclass(frozen=True)
class BuildEvent:
    kind: str
    message: str = ""
    paths: tuple[Path, ...] = ()
    guide: Path | None = None
    skipped: tuple[str, ...] = ()


def run_build(config: LauncherConfig, output_name: str, output_dir: str | Path,
              all_platforms: bool, events: Queue[BuildEvent]) -> None:
    def progress(message: str) -> None:
        events.put(BuildEvent(kind="progress", message=message))

    try:
        if all_platforms:
            result = build_all_launchers(config, base_name=output_name, output_dir=output_dir, progress=progress)
            events.put(BuildEvent(kind="success", paths=tuple(result.executables), guide=result.how_to_use, skipped=tuple(result.skipped)))
        else:
            result = build_launcher(config, output_name=output_name, output_dir=output_dir, progress=progress)
            events.put(BuildEvent(kind="success", paths=(result.executable,), guide=result.how_to_use))
    except FalcoError as exc:
        events.put(BuildEvent(kind="error", message=str(exc)))
    except Exception as exc:
        events.put(BuildEvent(kind="error", message=f"Build failed ({type(exc).__name__}): {exc}. Check the output folder, free disk space and launcher files, then retry."))
