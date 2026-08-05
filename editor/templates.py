"""Usage-guide template written next to each generated launcher.

A launcher is a prebuilt Rust stub with the non-secret :class:`LauncherConfig`
appended as a trailer (see :mod:`editor.builder`); there is no generated Python
entry script anymore. This module renders the human/agent-oriented
``how-to-use.md`` that ships alongside the binary.
"""

from __future__ import annotations

from shared.config import LauncherConfig

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
