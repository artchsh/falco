"""Source template for the tiny entry script embedded in each launcher.

The generated launcher is *not* a copy-paste of the runtime. It is a two-line
program that constructs the (non-secret) :class:`LauncherConfig` and calls
``launcher.run``. PyInstaller freezes this script together with the ``launcher``
and ``shared`` packages into a single executable.

Only host/port/username/credential_id are embedded. The password is never part
of the config, the script, or the resulting binary's metadata.
"""

from __future__ import annotations

from shared.config import LauncherConfig

ENTRY_TEMPLATE = '''\
"""Auto-generated Falco launcher entry point. Do not edit by hand.

Embedded, NON-SECRET configuration only. The SSH password is retrieved at
runtime from the OS credential store (Windows Credential Manager / macOS
Keychain) and is never stored here.
"""

import sys

from launcher.runtime import run
from shared.config import LauncherConfig

CONFIG = LauncherConfig(
    launcher_name={launcher_name!r},
    host={host!r},
    username={username!r},
    port={port!r},
    credential_id={credential_id!r},
)


def main() -> int:
    return run(CONFIG)


if __name__ == "__main__":
    sys.exit(main())
'''


def render_entry_script(config: LauncherConfig) -> str:
    """Return Python source for the launcher entry script for ``config``."""

    return ENTRY_TEMPLATE.format(
        launcher_name=config.launcher_name,
        host=config.host,
        username=config.username,
        port=config.port,
        credential_id=config.credential_id,
    )
