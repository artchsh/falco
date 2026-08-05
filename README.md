# Falco

Falco generates **standalone, single-file SSH launcher executables**. You
configure a launcher once in a small GUI (the *Falco Editor*), and it produces a
self-contained binary such as `meks.exe`. Running that binary opens or scripts an
SSH session to a preconfigured server — **without ever embedding the password**.

```bash
meks.exe "cd /var/www && git pull && docker compose up -d"
```

The password is entered once, on first run, and stored in the OS credential
store (Windows Credential Manager / macOS Keychain). It is never baked into the
binary, passed on a command line, placed in an environment variable, or written
to a file by Falco.

---

## 1. Architecture summary

Falco is one Python codebase with two runtime "faces":

```
┌────────────────────┐        generates         ┌──────────────────────────┐
│   Falco Editor      │ ───────────────────────▶ │  Standalone launcher     │
│  (Tkinter GUI)      │   (PyInstaller onefile)  │  e.g. meks.exe           │
│                     │                          │                          │
│  collects config →  │                          │  embeds NON-SECRET config│
│  runs the builder   │                          │  reads password from     │
└────────────────────┘                          │  OS keystore at runtime  │
                                                 └──────────────────────────┘
```

Three importable packages, kept deliberately small and separate:

| Package     | Responsibility                                                        |
|-------------|-----------------------------------------------------------------------|
| `shared/`   | The non-secret `LauncherConfig` model + typed error hierarchy.        |
| `launcher/` | Everything a generated binary runs: CLI parsing, credentials, SSH, interactive shell, runtime dispatch. |
| `editor/`   | The GUI, the entry-script template, and the PyInstaller build driver. |

**How config is "embedded":** the Editor does *not* copy the runtime into the
binary by hand. It writes a two-line entry script (`editor/templates.py`) that
constructs a `LauncherConfig` and calls `launcher.run(config)`. PyInstaller
freezes that script together with the `launcher` and `shared` packages. Only
`host`, `port`, `username` and `credential_id` are embedded — there is no
password field anywhere in the model, so a secret physically cannot be baked in.

**Data flow at runtime:**

1. `launcher/cli.py` parses argv into a `LaunchRequest` (command / stdin / interactive).
2. `launcher/credentials.py` resolves the password from the OS keystore (via
   `keyring`), prompting once (no echo) and saving it if absent.
3. `launcher/ssh_client.py` connects with Paramiko (password auth,
   auto-accepting host keys) and streams output, returning the remote exit code.
4. `launcher/interactive.py` handles the no-args PTY shell.
5. `launcher/runtime.py` wires it together and returns the exit code.

## 2. Implementation plan (and status)

The spec asked to prioritise one-shot execution, password storage and Windows
packaging first. That MVP is **done and verified on Windows**:

- [x] Non-secret typed config model with JSON round-trip
- [x] Credential storage/lookup via `keyring` (Windows Credential Manager / macOS Keychain)
- [x] First-run no-echo password prompt + save
- [x] Password-based SSH connect with auto host-key acceptance
- [x] One-shot command execution, live stdout/stderr streaming, exit-code propagation
- [x] Safe command parsing for both `"docker ps"` and `docker ps` forms
- [x] `--stdin deploy.sh` remote-script mode
- [x] Tkinter Editor GUI + PyInstaller `--onefile` build driver
- [x] Automated tests (config, credentials, CLI parsing, exit-code propagation, build)
- [x] GitHub Actions for Windows / macOS / Linux builds + a test matrix
- [x] Interactive PTY shell — full on POSIX (raw mode, resize, restore), threaded fallback on Windows

Deliberately *after* the MVP (documented, not yet hardened): guaranteed
full-screen TUI fidelity (vim/htop/tmux) inside legacy Windows consoles, and
code-signing of macOS/Windows binaries.

## 3. Project layout

```
falco/
  editor/            # GUI + build driver + entry-script template
  launcher/          # runtime that generated binaries execute
  shared/            # config model + errors (used by both)
  build/             # headless build script for CI
  tests/             # pytest suite
  .github/workflows/ # tests + per-OS build workflows
  pyproject.toml
  README.md
```

## 4. Build instructions

### Prerequisites
- Python 3.12+

### Set up a dev environment
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev,build]"
```

### Run the tests
```bash
pytest
```

### Run the Editor (GUI)
```bash
python -m editor
```
Fill in the fields, click **Build launcher**, choose an output folder. The
generated executable appears there.

### Build a launcher headlessly (what CI uses)
```bash
python build/build_launcher.py --name meks --host 203.0.113.10 --user deploy --port 22 --output meks.exe --out-dir dist
```

> **Cross-OS note:** a Windows `.exe` must be built on Windows and a macOS
> binary on macOS. That is why cross-platform releases go through GitHub Actions
> (below) rather than one machine.

## 5. GitHub Actions

| Workflow                         | Runner          | Trigger            | Produces                    |
|----------------------------------|-----------------|--------------------|-----------------------------|
| `.github/workflows/tests.yml`        | ubuntu/win/macOS | push / PR          | test results (3-OS matrix)  |
| `.github/workflows/build-windows.yml`| windows-latest  | manual (dispatch)  | `*.exe` artifact            |
| `.github/workflows/build-macos.yml`  | macos-latest    | manual (dispatch)  | macOS binary artifact       |
| `.github/workflows/build-linux.yml`  | ubuntu-latest   | manual (dispatch)  | Linux binary (optional)     |

To build a signed-per-your-account launcher: open the repo's **Actions** tab →
pick *build-windows* / *build-macos* → **Run workflow**, enter the launcher name,
host, user and port. Download the artifact when it finishes. **No password is
entered in CI** — the launcher collects and stores it locally on first run.

## 6. Security limitations

Read this before deploying Falco launchers.

- **Automatic host-key acceptance defeats server-authentication.** Launchers use
  Paramiko's `AutoAddPolicy`, which accepts *unknown and changed* host
  fingerprints silently. This removes SSH's protection against
  **man-in-the-middle attacks and server impersonation**: if an attacker can
  redirect or spoof the host/IP, the launcher will connect and send your
  password to the attacker's server without warning. This is a usability/
  security trade-off chosen to match the spec. Only point launchers at hosts on
  networks you trust, and prefer this for low-stakes automation over hostile
  networks. A future hardening option is trust-on-first-use pinning.
- **Password auth, not keys.** Falco intentionally uses passwords. Key-based auth
  is stronger; Falco's model exists for environments that require passwords.
- **The password is only as protected as the OS keystore.** It is stored via
  `keyring` (Windows Credential Manager / macOS Keychain), so any process running
  as your user that can read that store can read the password. Falco never puts
  the secret on a command line, in an environment variable, in a generated file,
  or in the executable's metadata — but it cannot protect against a compromised
  user account.
- **Binaries are unsigned.** Generated executables are not code-signed. Windows
  SmartScreen and macOS Gatekeeper will warn on first run.
- **Embedded config is readable.** Host/port/username/credential-id are visible
  in the binary. They are not secrets, but treat them as disclosed.

## 7. Example launcher configuration & usage

Editor input:

| Field            | Value              |
|------------------|--------------------|
| Launcher name    | `meks`             |
| SSH host / IP    | `203.0.113.10`     |
| SSH port         | `22`               |
| SSH username     | `deploy`           |
| Output filename  | `meks.exe`         |

The embedded (non-secret) config becomes:

```json
{
  "launcher_name": "meks",
  "host": "203.0.113.10",
  "username": "deploy",
  "port": 22,
  "credential_id": "falco:meks:deploy@203.0.113.10:22",
  "schema_version": 1
}
```

### Usage

```bash
# One-shot command (single quoted string — operators preserved):
meks.exe "cd /var/www && git pull && docker compose up -d"

# One-shot command (bare args — re-quoted safely, run only remotely):
meks.exe docker ps

# Send a local script to run on the server:
meks.exe --stdin deploy.sh

# Interactive shell (PTY: history, tab-completion, colours, sudo prompts):
meks.exe

# Forget the stored password and re-prompt next run:
meks.exe --reset-password
```

On the **first** command the launcher prompts:

```
SSH password for deploy@203.0.113.10:
```

Nothing is echoed. The password is saved to the OS keystore and reused silently
afterwards. The launcher exits with the **remote command's** exit code, so it
composes cleanly in scripts and CI.

### Developer shortcut (no build required)

You can run the launcher runtime directly against a config during development:

```bash
# PowerShell
$env:FALCO_CONFIG_JSON = '{"launcher_name":"meks","host":"203.0.113.10","username":"deploy","port":22}'
python -m launcher "docker ps"
```

## License

MIT.
