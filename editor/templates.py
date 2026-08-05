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


HOW_TO_USE_TEMPLATE = '''\
# How to use `{name}`

`{name}` is a **standalone SSH launcher**. It runs commands on one preconfigured
remote server over SSH. No separate SSH client, config file, or password on the
command line is needed. This document is written for AI agents and automation.

## Target (non-secret)

| Field | Value |
|-------|-------|
| Host  | `{host}` |
| Port  | `{port}` |
| User  | `{username}` |

## Running commands

```
{name} "cd /srv/app && git pull && docker compose up -d"   # one quoted command
{name} docker ps                                            # bare args also work
{name} --stdin deploy.sh                                    # run a local script remotely
{name}                                                      # interactive shell (PTY)
```

- Commands run **only on the remote server**, never in a local shell.
- Output (stdout and stderr) is streamed live.
- The process exits with the **remote command's exit code**, so it composes
  cleanly in scripts and pipelines.

## File transfer (SFTP over the same connection)

```
{name} --upload ./local-file /remote/path      {name} --download /remote/file ./local-path
{name} --list /remote/dir                       {name} --mkdir /remote/dir
{name} --remove /remote/file                     {name} --move /remote/a /remote/b
{name} --upload-dir ./dist /var/www/site/dist    {name} --download-dir /var/log ./logs
```

- Uses the **same** connection and stored password as command execution.
- Transfer progress is streamed to the terminal; names and directory structure
  are preserved on recursive transfers.
- **Existing files are never overwritten** unless you add `--overwrite`.
- Add `--mkdirs` to create missing destination directories.
- Downloads are written to a temporary file and renamed only on success, so an
  interrupted transfer never leaves a truncated file.
- Any failure exits non-zero.

## Credential safety — no secrets leak

This is safe to run in automated environments:

- **No password is stored in this executable**, in its metadata, or in any file
  it writes. The binary contains only the non-secret fields above.
- The password is entered **once, hidden (no echo)**, on first run and saved to
  the **OS credential store** (Windows Credential Manager / macOS Keychain).
- The password is **never** passed as a command-line argument, environment
  variable, or temp file — so it cannot appear in process listings, shell
  history, CI logs, or crash dumps.
- To clear the saved password:  `{name} --reset-password`

## Connection security — honest summary

- **The entire session is encrypted by the SSH protocol** — authentication,
  the commands you send, and all output travel over an encrypted channel and
  are never sent in cleartext.
- **Host identity is trust-on-connect:** an unknown *or changed* server host key
  is accepted automatically. This means `{name}` does **not** protect against a
  man-in-the-middle or an impersonating server. Only run it against hosts on
  networks you trust. (This is a deliberate design trade-off for zero-setup use.)
'''


def render_how_to_use(config: LauncherConfig, output_name: str) -> str:
    """Return a short, agent-oriented usage guide for a generated launcher."""

    # Use the invocation name the user will actually type (drop packaging suffix).
    name = output_name
    for suffix in (".exe", ".app", ".bin"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
            break
    return HOW_TO_USE_TEMPLATE.format(
        name=name,
        host=config.host,
        port=config.port,
        username=config.username,
    )
