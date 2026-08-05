"""Run the launcher during development without building an executable.

Supply the config via ``FALCO_CONFIG_JSON`` (inline JSON) or ``FALCO_CONFIG``
(path to a JSON file), then::

    FALCO_CONFIG=./meks.falco.json python -m launcher "docker ps"

Generated executables do **not** use this module; they call ``launcher.run``
directly from a tiny embedded entry script (see ``editor/templates.py``).
"""

from __future__ import annotations

import os
import sys

from launcher.runtime import run
from shared.config import LauncherConfig
from shared.errors import ConfigError


def _load_config() -> LauncherConfig:
    inline = os.environ.get("FALCO_CONFIG_JSON")
    if inline:
        return LauncherConfig.from_json(inline)
    path = os.environ.get("FALCO_CONFIG")
    if path:
        return LauncherConfig.load(path)
    raise ConfigError(
        "Set FALCO_CONFIG_JSON or FALCO_CONFIG to run the launcher via `python -m launcher`."
    )


def main() -> int:
    try:
        config = _load_config()
    except ConfigError as exc:
        print(f"falco: {exc}", file=sys.stderr)
        return 2
    return run(config)


if __name__ == "__main__":
    raise SystemExit(main())
