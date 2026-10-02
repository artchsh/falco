"""Generate instructions using the actual launcher filenames and auth mode."""

from shared.config import LauncherConfig


def render_how_to_use(config: LauncherConfig, output_name: str, *, filenames: list[str] | None = None) -> str:
    name = output_name
    for suffix in (".exe", ".app", ".bin"):
        if name.lower().endswith(suffix):
            name = name[:-len(suffix)]
            break
    files = filenames if filenames is not None else [f"{name}-windows.exe", f"{name}-macos", f"{name}-linux"]
    invocation = "./" + next((f for f in files if not f.endswith(".exe")), files[0])
    setup = "\n".join(f"chmod +x {f}" for f in files if not f.endswith(".exe"))
    mac = next((f for f in files if "macos" in f), next((f for f in files if not f.endswith(".exe")), None))
    quarantine = f"On macOS, right-click → Open once, or run `xattr -d com.apple.quarantine {mac}` if Gatekeeper quarantines the unsigned launcher." if mac else ""
    secret = "private-key passphrase" if config.auth_method == "private_key" else "SSH password"
    key_note = "An encrypted OpenSSH private key is embedded. The passphrase and decrypted key are never embedded or written to a file by Falco." if config.auth_method == "private_key" else "No password is stored in this executable."
    vpn = """
## Required private network

This server requires Tailscale/WireGuard/VPN. If an AI agent gets a timeout,
network-unreachable or address error, the user may have forgotten to enable
Tailscale/WireGuard/VPN. Ask the user to enable it, then retry; do not guess a new
server IP or repeatedly retry a state-changing command.
""" if config.requires_vpn else ""
    return f'''# How to use `{name}`

Standalone SSH launcher for **{config.username}@{config.host}:{config.port}**.
Authentication: **{config.auth_method}**. Files: {", ".join(f"`{f}`" for f in files)}.
Use the file for your operating system. Windows runs the `.exe` directly;
on Unix use `./` before the filename. The examples use `{invocation}`.

## First run — user setup

A human must run the launcher once in a terminal and enter the {secret} in its
hidden prompt. After successful authentication it is saved in the OS credential
store (Windows Credential Manager, macOS Keychain, Linux Secret Service).
AI agents must never request the secret in chat, supply it via argv/environment,
or capture its prompt. If `CREDENTIAL_SETUP_REQUIRED` appears, ask the user to
complete this terminal setup. Subsequent agent runs retrieve the secret privately.

{key_note} No passwords or passphrases are passed on command lines, exported as
environment variables, or written to temporary files. Secret values are held in
process memory during authentication; Falco cannot control external crash dumps
or an agent with unrestricted access to the user's OS account.

On macOS/Linux a copied launcher may need its executable bit restored:

```sh
{setup or "# Windows: no chmod needed"}
```

{quarantine}
{vpn}
## Running commands

```sh
{invocation} "cd /srv/app && git pull && docker compose up -d"
{invocation} docker ps
{invocation} --stdin deploy.sh
{invocation}                           # interactive terminal
```

Commands run only on the remote server. Remote stdout/stderr stream live;
command execution returns the real remote exit code. Falco does not retry commands.

## File transfer

```sh
{invocation} --upload ./app.zip /srv/app.zip
{invocation} --download /var/log/app.log ./app.log
{invocation} --list /srv
{invocation} --mkdir /srv/new
{invocation} --remove /srv/old
{invocation} --move /srv/a /srv/b
{invocation} --upload-dir ./dist /var/www
{invocation} --download-dir /var/log ./logs
```

Only one transfer action per invocation. Add `--overwrite` to replace existing
files or `--mkdirs` to create missing destinations. Downloads are staged before
replacement. Interrupted transfers fail; inspect the destination before retrying.

## Troubleshooting — instructions for AI agents

Falco's own errors are single-line JSON on stderr with `error`, `message`,
`action` and optional `target`/`details`. Remote stderr is passed through unchanged.
Exit groups: 2 = arguments/config; 3 = local credentials; 4 = SSH/remote failure.
A remote command's nonzero status is that command's status, not automatically a
Falco error. A disconnected command with unknown status is never reported as success.

- `DNS_LOOKUP_FAILED`: check the configured hostname and DNS/private-network access.
- `CONNECTION_REFUSED`: check the host/port and whether SSH is listening.
- `NETWORK_UNREACHABLE` / `CONNECTION_TIMEOUT`: the connection failed or times out.
  Check host/IP, port, network/VPN and server availability. Do not infer a wrong
  password or that the user is \"not logged in\" from a timeout. Compare with
  `ssh {config.username}@{config.host} -p {config.port}` if needed.
- `AUTHENTICATION_FAILED`: the server rejected authentication. Ask the user to
  check account/key authorization and refresh the stored secret in a terminal:
  `{invocation} --reset-credential`. Password mode also accepts `--reset-password`.
- `KEY_DECRYPTION_FAILED`: ask the user to reset the stored passphrase and enter
  it again privately. A damaged/unsupported key may require rebuilding.
- `CREDENTIAL_STORE_UNAVAILABLE`: unlock/start the OS credential store; on Linux
  provide a running Secret Service (GNOME Keyring/KWallet). No insecure fallback.

## Connection security — host identity

The SSH session is encrypted. Host identity uses **trust on first use**: Falco
remembers the first observed server key for this host/port in the OS credential
store. This protects later connections against changed keys, but the first
connection still needs a trusted network or independently verified server identity.

`HOST_KEY_CHANGED` means: **Server key changed. If you accept this change, retry
with --accept-new-key.** Expected and observed fingerprints are included in the
error. AI agents must ask the user to verify and approve the new fingerprint
before using this flag; never automatically bypass the error.

After user approval, retry the original invocation with `--accept-new-key` before
the remote command/action, e.g. `{invocation} --accept-new-key "docker ps"`.
Credential reset does not clear host trust. Keys/passphrases are stored separately;
sharing a launcher requires fresh credential setup on each recipient's machine.
'''
