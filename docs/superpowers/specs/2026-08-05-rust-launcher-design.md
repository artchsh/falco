# Rust Launcher — Design Spec

**Date:** 2026-08-05
**Status:** Approved (design), pending implementation plan
**Author:** Falco team

## Problem

The generated launcher is a PyInstaller one-file binary (~25 MB). Almost all of
that is the bundled CPython interpreter plus `cryptography`/OpenSSL pulled in by
Paramiko. The practical floor for a Python + Paramiko launcher is ~20–30 MB;
1–5 MB is unreachable while the launcher stays in Python (see
[docs/launcher-size.md](../../launcher-size.md)).

Separately, because "building a launcher" means invoking a compiler toolchain
(PyInstaller today), a *frozen* editor cannot produce launchers offline — there
is no Python toolchain inside a frozen binary.

## Goal

Replace the Python launcher with a native **Rust** launcher that is:

- **Small**: 1–3 MB self-contained static binary (target; 1–5 MB acceptable).
- **Full parity**: a drop-in behavioral replacement for the current Python
  launcher — same modes, flags, credential handling, SSH semantics, SFTP suite,
  and exit codes.
- **Toolchain-free at author time**: creating a launcher requires no compiler,
  so even a frozen editor can mint launchers offline.

The **editor stays in Python.** Only its build step changes.

## Non-goals

- Rewriting the editor GUI in Rust.
- Code-signed launchers (see caveat in [Appended-config format](#appended-config-format)).
- Changing the SSH security model (host-key auto-accept stays; documented
  trade-off).
- SSH key / agent authentication (password-only, as today).

## Decisions (settled during brainstorming)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Config embedding | **Prebuilt stub + appended config blob** | Instant "build", single-file launcher, no toolchain at author time, frozen editor works offline. |
| SSH library | **russh + russh-sftp (pure Rust, async)** | No OpenSSL/C deps; one toolchain cross-compiles all 3 OSes; smallest static binary. |
| Scope | **Full parity in one spec** | True drop-in replacement; lets us retire the Python launcher. |
| Python launcher | **Kept as behavioral reference during port**, deleted in a follow-up once the Rust launcher is proven. | Existing pytest suite is the parity checklist. |
| Codesigning | **Not supported now** (accepted; same as today's unsigned PyInstaller binaries). | Appended trailer is incompatible with Authenticode. |

## Architecture

Two artifacts replace today's per-build PyInstaller run:

- **`falco-stub`** — one prebuilt Rust binary per OS, compiled in CI. Contains
  all launcher logic but no server config. Built once, bundled into the editor.
- **A generated launcher** — the editor produces one by: copy `falco-stub` →
  append the serialized `LauncherConfig` as a trailing blob → rename. At startup
  the stub reads its own file (`std::env::current_exe`) to recover its config.

Only the editor's `build_launcher` step changes: from "run PyInstaller" to "copy
stub + append config bytes." No compiler, PyInstaller, or cargo at author time.

### Appended-config format

Little-endian trailer at end-of-file:

```
[ ...stub binary... ][ config JSON (utf-8) ][ u64 length ][ 8-byte magic "FALCOCFG" ]
```

Startup reads the last 16 bytes. If the magic matches, it slices out the JSON
using the length field and parses the same non-secret fields as today
(`launcher_name`, `host`, `username`, `port`, `credential_id`, `schema_version`).
If the magic is absent (someone ran the bare stub), it prints a friendly
"this stub was not configured by the Falco editor" message and exits `2`.

**Caveat (accepted):** appending bytes after EOF is incompatible with
Authenticode / macOS codesigning — the signature would break or reject the
trailer. Today's PyInstaller binaries are unsigned, so this is no regression. If
signed launchers are ever needed, that OS switches to a cargo-compile model.

## Repo layout & crate

```
launcher-rs/               # new Cargo crate -> produces falco-stub
  Cargo.toml
  src/
    main.rs                # tokio current_thread entry; reads embedded config; maps exit codes
    config.rs              # LauncherConfig + trailer read/parse   (mirrors shared/config.py)
    cli.rs                 # argv -> LaunchRequest                 (mirrors launcher/cli.py)
    credentials.rs         # keyring get/store/delete + hidden prompt
    ssh.rs                 # russh connect (password, auto-accept host keys), exec, run_script
    interactive.rs         # crossterm raw-mode PTY bridge + resize (POSIX & Windows)
    sftp.rs                # russh-sftp: 8 ops, recursion, progress, temp-file-then-rename
    errors.rs              # FalcoError variants -> exit codes
```

### Crate dependencies

- `russh` + `russh-sftp` — SSH transport and SFTP (pure Rust).
- `keyring` — OS credential store (Windows Credential Manager / macOS Keychain /
  Linux Secret Service), same backends as the Python `keyring`.
- `rpassword` — no-echo password prompt.
- `crossterm` — cross-platform raw terminal mode + resize events for the PTY.
- `tokio` — async runtime, **current-thread flavor** (no multi-thread pool).
- `serde` / `serde_json` — config (de)serialization.
- `thiserror` / `anyhow` — error types.

## Behavioral parity contract

Ported 1:1 from the Python launcher and guarded by tests.

### Modes (argv dispatch — mirrors [launcher/cli.py](../../../launcher/cli.py))

- **Interactive** (no args): PTY shell.
- **Command**: one arg passed verbatim; multiple args individually
  shell-quoted and joined (so `meks docker ps` → `docker ps`, and args with
  spaces/metacharacters survive).
- **`--stdin <file>`**: pipe the file's contents into a remote `/bin/sh -s`.
- **SFTP**: selected by presence of any SFTP action flag.

### Flags

- `--reset-password` / `--reset-credential` — delete stored credential first.
- `--overwrite` — allow replacing existing files (SFTP).
- `--mkdirs` — create missing destination directories (SFTP).
- `--stdin` — remote-script mode.

### Credentials (mirrors [launcher/credentials.py](../../../launcher/credentials.py))

- Password read from / written to the OS keystore under the embedded
  `credential_id` service name and `username`.
- On first run (no stored password): prompt once, **no echo**, blank input
  aborts, then save.
- Service id format is whatever the editor embeds — currently
  `falco:{launcher_name}:{username}@{host}:{port}`.
- Password is only ever in memory; never on a command line, env var, temp file,
  or written to disk by Falco.

### SSH (mirrors [launcher/ssh_client.py](../../../launcher/ssh_client.py))

- Password auth only — no keys, no agent.
- **Auto-accept unknown *and* changed host keys** (equivalent to Paramiko
  `AutoAddPolicy`). Documented MITM trade-off; unchanged from today.
- Connect timeout ~15 s.
- Command execution streams stdout and stderr live and returns the remote exit
  code.
- `run_script`: feed script bytes to `/bin/sh -s`, signal EOF, stream output,
  return exit code.

### Interactive PTY (mirrors [launcher/interactive.py](../../../launcher/interactive.py))

- Request a remote PTY sized to the local terminal, `TERM` from environment
  (fallback `xterm-256color`).
- Bridge local terminal ↔ channel via crossterm raw mode.
- Handle window resize (forward new size to the remote PTY).
- **Always restore terminal state** on exit/error/disconnect.
- Windows: functional line input, colours, Ctrl+C (→ ETX) and Ctrl+D (EOF);
  full-screen TUIs on classic consoles may be imperfect (allowed, as today).

### SFTP (mirrors the behavioral contract in [editor/templates.py](../../../editor/templates.py))

Eight operations: `upload`, `download`, `list`, `mkdir`, `remove`, `move`,
`upload-dir` (recursive), `download-dir` (recursive). Plus:

- Live transfer progress to the terminal.
- Names and directory structure preserved on recursive transfers.
- **Existing files never overwritten** unless `--overwrite`.
- `--mkdirs` creates missing destination directories.
- Downloads written to a **temp file, renamed only on success** (no truncated
  file on interruption).
- Any failure exits non-zero.
- Exactly one SFTP operation per invocation; extra actions or wrong operand
  arity is a parse error.

### Exit codes (preserved exactly — mirrors [launcher/runtime.py](../../../launcher/runtime.py))

| Code | Meaning |
|------|---------|
| `0` | Success (or clean interactive disconnect). |
| remote code | Command/stdin mode: the remote command's exit status. |
| `2` | Argument / parse error (incl. unconfigured stub). |
| `3` | Credential error. |
| `4` | SSH connection / remote / other Falco error. |
| `130` | Interrupted (Ctrl+C). |

## Size strategy

`[profile.release]`:

```toml
opt-level = "z"
lto = true
codegen-units = 1
panic = "abort"
strip = true
```

Plus tokio `current_thread` flavor (single connection needs no thread pool).
russh is pure Rust, so no OpenSSL is linked. Target: 1–3 MB.

## Editor integration (Python side)

- `editor/builder.py` `build_launcher` is rewritten to:
  1. Locate the bundled `falco-stub` for the current OS.
  2. Copy it to `output_name`.
  3. Append `config.to_json()` bytes + the `[u64 length][magic]` trailer.
  4. Write `how-to-use.md` next to it (unchanged content).
  The PyInstaller code path is removed.
- Editor packaging bundles the stub: PyInstaller `--add-data` when frozen; read
  from `launcher-rs/target/release/` in dev.
- CI: a Rust build job produces `falco-stub` per OS **before** the editor
  packaging job consumes it.
- `build/build_launcher.py` (CLI equivalent) updated the same way.

## Testing

- **Rust unit tests**, mirroring the existing Python pytest suite:
  - CLI parsing: mode selection, single-vs-multi arg quoting, flag extraction,
    SFTP action/operand arity and error cases.
  - Config trailer: round-trip append → read-back; missing/invalid magic.
  - Exit-code mapping.
- **Integration test**: append a config to a built stub, run it against an
  in-process russh test server, assert connect + exec + exit-code propagation.
- **CI smoke test**: build stub, generate a launcher, run a trivial command
  against a throwaway SSH server.
- The Python launcher's pytest suite stays green as the parity oracle until the
  Rust launcher is proven, then is retired with the Python launcher.

## Migration / rollout

1. Land `launcher-rs` crate with full parity and tests.
2. Wire CI to build the stub per OS.
3. Switch the editor's build step to the stub-append model; bundle the stub.
4. Verify end-to-end on all three OSes.
5. Follow-up PR: delete `launcher/` (Python) and its now-redundant tests; keep
   `shared/config.py` only if the editor still needs it (it does — the editor
   constructs `LauncherConfig`).

## Open questions

None blocking. Codesigning and Python-launcher deletion are deferred as noted.
