# Rust Launcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the ~25 MB PyInstaller Python launcher with a 1–3 MB pure-Rust launcher that has full behavioral parity, distributed as a prebuilt stub the editor configures by appending a config blob (no toolchain at author time).

**Architecture:** A single Cargo crate `launcher-rs` builds one config-less binary (`falco-stub`) per OS. The Python editor "builds" a launcher by copying the stub and appending the serialized `LauncherConfig` as a trailing blob; at startup the stub reads its own file to recover the config. SSH/SFTP use pure-Rust `russh`/`russh-sftp`; credentials use the `keyring` crate; the PTY uses `crossterm`.

**Tech Stack:** Rust (tokio current-thread), russh + russh-sftp, keyring, rpassword, crossterm, serde/serde_json, thiserror. Editor side stays Python.

## Global Constraints

- Rust edition **2021**, minimum toolchain **1.75+**.
- Launcher must remain a **single self-contained file**; no runtime deps, no OpenSSL/C libraries (russh is pure Rust).
- **No password** is ever embedded, logged, placed on a command line, put in an env var, or written to a temp file. Only non-secret fields are embedded.
- Embedded config fields, verbatim: `launcher_name`, `host`, `username`, `port`, `credential_id`, `schema_version`. `schema_version` current value **1**.
- Credential keyring service id format, verbatim: `falco:{launcher_name}:{username}@{host}:{port}` (the editor generates it; the launcher only consumes the embedded value).
- Host-key policy: **auto-accept unknown AND changed host keys** (matches Paramiko `AutoAddPolicy`). This is deliberate; do not add verification.
- SSH auth is **password-only** — no keys, no agent.
- Exit codes, verbatim: `0` success/clean disconnect; remote exit code in command/stdin modes; `2` arg/parse error (incl. unconfigured stub); `3` credential error; `4` SSH/remote/other Falco error; `130` interrupt.
- Config trailer format, verbatim: `[stub][config JSON utf-8][u64 LE length][8-byte magic b"FALCOCFG"]`.
- `[profile.release]`: `opt-level="z"`, `lto=true`, `codegen-units=1`, `panic="abort"`, `strip=true`. Final stub must measure **≤ 5 MB** (target 1–3 MB).
- **Dependency-version reconciliation:** the russh / russh-sftp / crossterm / keyring APIs drift between releases. Code blocks below target the API shape described in each task; when a version resolves differently at `cargo build`, adapt call sites to the resolved API while preserving the behavior and signatures this plan specifies. Pin exact versions in `Cargo.lock` once building.
- The Python launcher (`launcher/`) and its tests stay green as the parity oracle until Task 15; do not modify them before then.

---

## File Structure

```
launcher-rs/
  Cargo.toml                 # deps + release profile
  src/
    main.rs                  # entry: load config, parse argv, dispatch, map exit codes
    errors.rs                # FalcoError + exit-code mapping
    config.rs                # LauncherConfig, default_credential_id, trailer read/parse
    cli.rs                   # argv -> LaunchRequest (modes, flags, quoting, SFTP arity)
    credentials.rs           # KeyStore trait, keyring impl, hidden prompt, resolve_password
    ssh.rs                   # connect, run_command, run_script (russh)
    interactive.rs           # crossterm raw-mode PTY bridge + resize + restore
    sftp.rs                  # 8 SFTP ops, recursion, progress, overwrite guard, temp-rename
  tests/
    cli.rs                   # integration-style unit tests for parsing
    config_trailer.rs        # append + read-back round trip
    ssh_integration.rs       # in-process russh server: connect + exec + exit code

editor/builder.py            # MODIFY: stub-copy + append config (replaces PyInstaller path)
build/build_launcher.py      # MODIFY: same model for the CLI build path
build/build_editor.py        # MODIFY: bundle the stub via --add-data
tests/ (Python)              # MODIFY: builder tests target the new model
.github/workflows/           # MODIFY/ADD: build stub per OS before editor packaging
```

Editor-visible interface preserved: `editor.builder.build_launcher(config, *, output_name, icon_path=None, output_dir, progress=None) -> BuildResult` with `BuildResult(executable, work_dir, how_to_use)`.

---

## Task 1: Scaffold the crate

**Files:**
- Create: `launcher-rs/Cargo.toml`
- Create: `launcher-rs/src/main.rs`
- Create: `launcher-rs/.gitignore`

**Interfaces:**
- Produces: a buildable crate named `falco-stub` (bin).

- [ ] **Step 1: Write `Cargo.toml`**

```toml
[package]
name = "falco-stub"
version = "0.1.0"
edition = "2021"
rust-version = "1.75"

[[bin]]
name = "falco-stub"
path = "src/main.rs"

[dependencies]
tokio = { version = "1", features = ["rt", "macros", "io-std", "io-util", "net", "time", "signal"] }
russh = "0.45"
russh-sftp = "2"
keyring = "3"
rpassword = "7"
crossterm = "0.28"
serde = { version = "1", features = ["derive"] }
serde_json = "1"
thiserror = "2"

[profile.release]
opt-level = "z"
lto = true
codegen-units = 1
panic = "abort"
strip = true
```

- [ ] **Step 2: Write a minimal `main.rs`**

```rust
fn main() {
    std::process::exit(2);
}
```

- [ ] **Step 3: Write `.gitignore`**

```
/target
```

- [ ] **Step 4: Verify it builds**

Run: `cd launcher-rs && cargo build`
Expected: compiles successfully (dependencies download on first run).

- [ ] **Step 5: Commit**

```bash
git add launcher-rs/Cargo.toml launcher-rs/src/main.rs launcher-rs/.gitignore
git commit -m "feat(launcher-rs): scaffold Rust launcher crate"
```

---

## Task 2: Error types and exit-code mapping

**Files:**
- Create: `launcher-rs/src/errors.rs`
- Modify: `launcher-rs/src/main.rs`

**Interfaces:**
- Produces: `enum FalcoError` with variants `Config(String)`, `Credential(String)`, `Ssh(String)`, `Remote(String)`; `impl FalcoError { fn exit_code(&self) -> i32 }`; `type FResult<T> = Result<T, FalcoError>`.
- Exit mapping: `Config -> 2`, `Credential -> 3`, `Ssh -> 4`, `Remote -> 4`.

- [ ] **Step 1: Write the failing test**

Add to the bottom of `src/errors.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exit_codes_match_contract() {
        assert_eq!(FalcoError::Config("x".into()).exit_code(), 2);
        assert_eq!(FalcoError::Credential("x".into()).exit_code(), 3);
        assert_eq!(FalcoError::Ssh("x".into()).exit_code(), 4);
        assert_eq!(FalcoError::Remote("x".into()).exit_code(), 4);
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd launcher-rs && cargo test errors`
Expected: FAIL — `FalcoError` not found / does not compile.

- [ ] **Step 3: Write the implementation** (top of `src/errors.rs`)

```rust
use thiserror::Error;

#[derive(Debug, Error)]
pub enum FalcoError {
    #[error("{0}")]
    Config(String),
    #[error("{0}")]
    Credential(String),
    #[error("{0}")]
    Ssh(String),
    #[error("{0}")]
    Remote(String),
}

impl FalcoError {
    pub fn exit_code(&self) -> i32 {
        match self {
            FalcoError::Config(_) => 2,
            FalcoError::Credential(_) => 3,
            FalcoError::Ssh(_) | FalcoError::Remote(_) => 4,
        }
    }
}

pub type FResult<T> = Result<T, FalcoError>;
```

- [ ] **Step 4: Wire the module in `main.rs`**

```rust
mod errors;

fn main() {
    std::process::exit(2);
}
```

- [ ] **Step 5: Run tests to verify pass**

Run: `cd launcher-rs && cargo test errors`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add launcher-rs/src/errors.rs launcher-rs/src/main.rs
git commit -m "feat(launcher-rs): error types and exit-code mapping"
```

---

## Task 3: Config model and default credential id

**Files:**
- Create: `launcher-rs/src/config.rs`
- Modify: `launcher-rs/src/main.rs`

**Interfaces:**
- Produces:
  - `struct LauncherConfig { launcher_name: String, host: String, username: String, port: u16, credential_id: String, schema_version: u32 }` (derive `Serialize, Deserialize, Debug, Clone, PartialEq`).
  - `fn default_credential_id(launcher_name, username, host, port) -> String`.
  - `fn LauncherConfig::from_json(&str) -> FResult<LauncherConfig>`.

- [ ] **Step 1: Write the failing test** (bottom of `src/config.rs`)

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn credential_id_matches_python_format() {
        let id = default_credential_id("server-client-X", "root", "1.2.3.4", 22);
        assert_eq!(id, "falco:server-client-X:root@1.2.3.4:22");
    }

    #[test]
    fn from_json_parses_all_fields() {
        let json = r#"{"launcher_name":"server-client-X","host":"h","username":"u","port":2222,"credential_id":"cid","schema_version":1}"#;
        let cfg = LauncherConfig::from_json(json).unwrap();
        assert_eq!(cfg.launcher_name, "server-client-X");
        assert_eq!(cfg.port, 2222);
        assert_eq!(cfg.credential_id, "cid");
    }

    #[test]
    fn from_json_rejects_garbage() {
        assert!(LauncherConfig::from_json("not json").is_err());
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd launcher-rs && cargo test config`
Expected: FAIL — types not defined.

- [ ] **Step 3: Write the implementation** (top of `src/config.rs`)

```rust
use serde::{Deserialize, Serialize};

use crate::errors::{FalcoError, FResult};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LauncherConfig {
    pub launcher_name: String,
    pub host: String,
    pub username: String,
    pub port: u16,
    pub credential_id: String,
    pub schema_version: u32,
}

pub fn default_credential_id(launcher_name: &str, username: &str, host: &str, port: u16) -> String {
    format!("falco:{launcher_name}:{username}@{host}:{port}")
}

impl LauncherConfig {
    pub fn from_json(text: &str) -> FResult<LauncherConfig> {
        serde_json::from_str(text)
            .map_err(|e| FalcoError::Config(format!("Configuration is not valid JSON: {e}")))
    }
}
```

- [ ] **Step 4: Wire the module in `main.rs`**

```rust
mod config;
mod errors;

fn main() {
    std::process::exit(2);
}
```

- [ ] **Step 5: Run tests to verify pass**

Run: `cd launcher-rs && cargo test config`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add launcher-rs/src/config.rs launcher-rs/src/main.rs
git commit -m "feat(launcher-rs): config model and credential-id format"
```

---

## Task 4: Config trailer (append + self-read)

**Files:**
- Modify: `launcher-rs/src/config.rs`
- Create: `launcher-rs/tests/config_trailer.rs`

**Interfaces:**
- Consumes: `LauncherConfig`, `FalcoError`.
- Produces:
  - `const MAGIC: &[u8; 8] = b"FALCOCFG";`
  - `fn append_config(binary: &mut Vec<u8>, cfg: &LauncherConfig)` — appends `json + u64 LE length + magic`.
  - `fn read_config_from_bytes(bytes: &[u8]) -> FResult<LauncherConfig>` — parses the trailer; `FalcoError::Config` if magic absent/short/invalid.
  - `fn load_embedded_config() -> FResult<LauncherConfig>` — reads `std::env::current_exe()` and calls `read_config_from_bytes`.

- [ ] **Step 1: Write the failing round-trip test** (`tests/config_trailer.rs`)

```rust
use falco_stub::config::{append_config, read_config_from_bytes, LauncherConfig};

fn sample() -> LauncherConfig {
    LauncherConfig {
        launcher_name: "server-client-X".into(),
        host: "1.2.3.4".into(),
        username: "root".into(),
        port: 22,
        credential_id: "falco:server-client-X:root@1.2.3.4:22".into(),
        schema_version: 1,
    }
}

#[test]
fn append_then_read_round_trips() {
    let mut bin = b"FAKE-STUB-BINARY-CONTENT".to_vec();
    append_config(&mut bin, &sample());
    let got = read_config_from_bytes(&bin).unwrap();
    assert_eq!(got, sample());
}

#[test]
fn read_without_trailer_errors() {
    let bin = b"just a plain binary".to_vec();
    assert!(read_config_from_bytes(&bin).is_err());
}
```

- [ ] **Step 2: Expose a lib target so integration tests can import**

Add to `Cargo.toml` under `[[bin]]`:

```toml
[lib]
name = "falco_stub"
path = "src/lib.rs"
```

Create `launcher-rs/src/lib.rs`:

```rust
pub mod cli;
pub mod config;
pub mod credentials;
pub mod errors;
pub mod ssh;
pub mod sftp;
pub mod interactive;
```

> NOTE: modules `cli`, `credentials`, `ssh`, `sftp`, `interactive` do not exist yet. To keep this task compiling on its own, temporarily list only the modules that exist (`config`, `errors`) and add the rest in their tasks. Use:

```rust
pub mod config;
pub mod errors;
```

And change `main.rs` to use the lib crate:

```rust
use falco_stub::{config, errors};

fn main() {
    let _ = (&config::MAGIC, errors::FalcoError::Config(String::new()).exit_code());
    std::process::exit(2);
}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd launcher-rs && cargo test --test config_trailer`
Expected: FAIL — `append_config` / `read_config_from_bytes` not found.

- [ ] **Step 4: Implement the trailer** (append to `src/config.rs`)

```rust
pub const MAGIC: &[u8; 8] = b"FALCOCFG";

pub fn append_config(binary: &mut Vec<u8>, cfg: &LauncherConfig) {
    let json = serde_json::to_vec(cfg).expect("config serializes");
    let len = json.len() as u64;
    binary.extend_from_slice(&json);
    binary.extend_from_slice(&len.to_le_bytes());
    binary.extend_from_slice(MAGIC);
}

pub fn read_config_from_bytes(bytes: &[u8]) -> FResult<LauncherConfig> {
    let not_configured =
        || FalcoError::Config("This stub was not configured by the Falco editor.".into());
    if bytes.len() < 16 {
        return Err(not_configured());
    }
    let (rest, magic) = bytes.split_at(bytes.len() - 8);
    if magic != MAGIC {
        return Err(not_configured());
    }
    let (rest, len_bytes) = rest.split_at(rest.len() - 8);
    let len = u64::from_le_bytes(len_bytes.try_into().unwrap()) as usize;
    if len == 0 || len > rest.len() {
        return Err(FalcoError::Config("Embedded config length is invalid.".into()));
    }
    let json = &rest[rest.len() - len..];
    let text = std::str::from_utf8(json)
        .map_err(|_| FalcoError::Config("Embedded config is not valid UTF-8.".into()))?;
    LauncherConfig::from_json(text)
}

pub fn load_embedded_config() -> FResult<LauncherConfig> {
    let exe = std::env::current_exe()
        .map_err(|e| FalcoError::Config(format!("Cannot locate own executable: {e}")))?;
    let bytes = std::fs::read(&exe)
        .map_err(|e| FalcoError::Config(format!("Cannot read own executable: {e}")))?;
    read_config_from_bytes(&bytes)
}
```

- [ ] **Step 5: Run tests to verify pass**

Run: `cd launcher-rs && cargo test --test config_trailer && cargo test config`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add launcher-rs/Cargo.toml launcher-rs/src/lib.rs launcher-rs/src/main.rs launcher-rs/src/config.rs launcher-rs/tests/config_trailer.rs
git commit -m "feat(launcher-rs): appended-config trailer read/write"
```

---

## Task 5: CLI parsing

**Files:**
- Create: `launcher-rs/src/cli.rs`
- Create: `launcher-rs/tests/cli.rs`
- Modify: `launcher-rs/src/lib.rs` (add `pub mod cli;`)

**Interfaces:**
- Consumes: `FalcoError`.
- Produces:
  - `enum Mode { Interactive, Command, Stdin, Sftp }`
  - `enum SftpAction { Upload, Download, List, Mkdir, Remove, Move, UploadDir, DownloadDir }`
  - `struct SftpOp { action: SftpAction, operands: Vec<String> }`
  - `struct LaunchRequest { mode: Mode, command: Option<String>, stdin_path: Option<String>, sftp: Option<SftpOp>, reset_password: bool, overwrite: bool, mkdirs: bool }`
  - `fn parse_args(argv: &[String]) -> FResult<LaunchRequest>`
  - `fn build_command(args: &[String]) -> String`

Behavioral rules copied from [launcher/cli.py](../../../launcher/cli.py):
- Any SFTP action flag present → SFTP mode; leading args before the action flag are an error; exactly one action; operand arity must match (`upload/download/move/upload-dir/download-dir` = 2; `list/mkdir/remove` = 1). `--reset-password`/`--reset-credential`, `--overwrite`, `--mkdirs` may appear anywhere in SFTP mode.
- Non-SFTP: leading `--reset-password`/`--reset-credential` set the flag and are stripped. No remaining args → Interactive. `--stdin <file>` (exactly one path) → Stdin. Otherwise Command.
- `build_command`: single arg verbatim; multiple args each POSIX-quoted and space-joined.

- [ ] **Step 1: Write the failing tests** (`tests/cli.rs`)

```rust
use falco_stub::cli::{parse_args, build_command, Mode, SftpAction};

fn v(args: &[&str]) -> Vec<String> {
    args.iter().map(|s| s.to_string()).collect()
}

#[test]
fn no_args_is_interactive() {
    let r = parse_args(&v(&[])).unwrap();
    assert!(matches!(r.mode, Mode::Interactive));
}

#[test]
fn leading_reset_then_interactive() {
    let r = parse_args(&v(&["--reset-password"])).unwrap();
    assert!(matches!(r.mode, Mode::Interactive));
    assert!(r.reset_password);
}

#[test]
fn single_command_is_verbatim() {
    let r = parse_args(&v(&["a && b"])).unwrap();
    assert!(matches!(r.mode, Mode::Command));
    assert_eq!(r.command.unwrap(), "a && b");
}

#[test]
fn multi_args_are_quoted_and_joined() {
    assert_eq!(build_command(&v(&["docker", "ps"])), "docker ps");
    assert_eq!(build_command(&v(&["echo", "a b"])), "echo 'a b'");
}

#[test]
fn stdin_requires_one_path() {
    let r = parse_args(&v(&["--stdin", "deploy.sh"])).unwrap();
    assert!(matches!(r.mode, Mode::Stdin));
    assert_eq!(r.stdin_path.unwrap(), "deploy.sh");
    assert!(parse_args(&v(&["--stdin"])).is_err());
    assert!(parse_args(&v(&["--stdin", "a", "b"])).is_err());
}

#[test]
fn sftp_upload_needs_two_operands() {
    let r = parse_args(&v(&["--upload", "./f", "/remote/f"])).unwrap();
    let op = r.sftp.unwrap();
    assert!(matches!(op.action, SftpAction::Upload));
    assert_eq!(op.operands, v(&["./f", "/remote/f"]));
    assert!(parse_args(&v(&["--upload", "./f"])).is_err());
}

#[test]
fn sftp_flags_extracted_anywhere() {
    let r = parse_args(&v(&["--overwrite", "--upload", "./f", "/r"])).unwrap();
    assert!(r.overwrite);
    assert!(matches!(r.sftp.unwrap().action, SftpAction::Upload));
}

#[test]
fn sftp_leading_arg_is_error() {
    assert!(parse_args(&v(&["junk", "--list", "/dir"])).is_err());
}

#[test]
fn two_sftp_actions_is_error() {
    assert!(parse_args(&v(&["--list", "/a", "--mkdir", "/b"])).is_err());
}
```

- [ ] **Step 2: Run tests to verify fail**

Run: `cd launcher-rs && cargo test --test cli`
Expected: FAIL — module `cli` missing.

- [ ] **Step 3: Implement `src/cli.rs`**

```rust
use crate::errors::{FalcoError, FResult};

#[derive(Debug, PartialEq)]
pub enum Mode {
    Interactive,
    Command,
    Stdin,
    Sftp,
}

#[derive(Debug, PartialEq, Clone, Copy)]
pub enum SftpAction {
    Upload,
    Download,
    List,
    Mkdir,
    Remove,
    Move,
    UploadDir,
    DownloadDir,
}

#[derive(Debug)]
pub struct SftpOp {
    pub action: SftpAction,
    pub operands: Vec<String>,
}

#[derive(Debug)]
pub struct LaunchRequest {
    pub mode: Mode,
    pub command: Option<String>,
    pub stdin_path: Option<String>,
    pub sftp: Option<SftpOp>,
    pub reset_password: bool,
    pub overwrite: bool,
    pub mkdirs: bool,
}

impl LaunchRequest {
    fn bare(mode: Mode) -> Self {
        LaunchRequest {
            mode,
            command: None,
            stdin_path: None,
            sftp: None,
            reset_password: false,
            overwrite: false,
            mkdirs: false,
        }
    }
}

fn sftp_spec(flag: &str) -> Option<(SftpAction, usize)> {
    Some(match flag {
        "--upload" => (SftpAction::Upload, 2),
        "--download" => (SftpAction::Download, 2),
        "--list" => (SftpAction::List, 1),
        "--mkdir" => (SftpAction::Mkdir, 1),
        "--remove" => (SftpAction::Remove, 1),
        "--move" => (SftpAction::Move, 2),
        "--upload-dir" => (SftpAction::UploadDir, 2),
        "--download-dir" => (SftpAction::DownloadDir, 2),
        _ => return None,
    })
}

fn take_flag(args: &mut Vec<String>, name: &str) -> bool {
    let before = args.len();
    args.retain(|a| a != name);
    before != args.len()
}

/// POSIX single-quote shell escaping, matching Python's shlex.quote for the
/// common cases the launcher needs.
fn shell_quote(arg: &str) -> String {
    if !arg.is_empty()
        && arg
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"@%_-+=:,./".contains(&b))
    {
        return arg.to_string();
    }
    // Wrap in single quotes, escaping embedded single quotes as '\''.
    let mut out = String::with_capacity(arg.len() + 2);
    out.push('\'');
    for ch in arg.chars() {
        if ch == '\'' {
            out.push_str("'\\''");
        } else {
            out.push(ch);
        }
    }
    out.push('\'');
    out
}

pub fn build_command(args: &[String]) -> String {
    if args.len() == 1 {
        return args[0].clone();
    }
    args.iter()
        .map(|a| shell_quote(a))
        .collect::<Vec<_>>()
        .join(" ")
}

fn parse_sftp(mut args: Vec<String>) -> FResult<LaunchRequest> {
    let reset = take_flag(&mut args, "--reset-password") | take_flag(&mut args, "--reset-credential");
    let overwrite = take_flag(&mut args, "--overwrite");
    let mkdirs = take_flag(&mut args, "--mkdirs");

    let idx = args
        .iter()
        .position(|a| sftp_spec(a).is_some())
        .expect("caller guaranteed an sftp flag");
    if idx != 0 {
        let leading = args[..idx].join(" ");
        return Err(FalcoError::Config(format!(
            "Unexpected argument(s) before {}: {leading}",
            args[idx]
        )));
    }
    let action_flag = args[0].clone();
    let operands: Vec<String> = args[1..].to_vec();
    if operands.iter().any(|a| sftp_spec(a).is_some()) {
        return Err(FalcoError::Config(
            "Only one SFTP operation may be given at a time.".into(),
        ));
    }
    let (action, arity) = sftp_spec(&action_flag).unwrap();
    if operands.len() != arity {
        return Err(FalcoError::Config(format!(
            "{action_flag} expects {arity} argument(s), got {}.",
            operands.len()
        )));
    }
    let mut req = LaunchRequest::bare(Mode::Sftp);
    req.sftp = Some(SftpOp { action, operands });
    req.reset_password = reset;
    req.overwrite = overwrite;
    req.mkdirs = mkdirs;
    Ok(req)
}

pub fn parse_args(argv: &[String]) -> FResult<LaunchRequest> {
    let mut args: Vec<String> = argv.to_vec();

    if args.iter().any(|a| sftp_spec(a).is_some()) {
        return parse_sftp(args);
    }

    let mut reset = false;
    while let Some(first) = args.first() {
        if first == "--reset-password" || first == "--reset-credential" {
            reset = true;
            args.remove(0);
        } else {
            break;
        }
    }

    if args.is_empty() {
        let mut req = LaunchRequest::bare(Mode::Interactive);
        req.reset_password = reset;
        return Ok(req);
    }

    if args[0] == "--stdin" {
        if args.len() != 2 {
            return Err(FalcoError::Config(
                "--stdin requires exactly one file path, e.g. --stdin deploy.sh".into(),
            ));
        }
        let mut req = LaunchRequest::bare(Mode::Stdin);
        req.stdin_path = Some(args[1].clone());
        req.reset_password = reset;
        return Ok(req);
    }

    let mut req = LaunchRequest::bare(Mode::Command);
    req.command = Some(build_command(&args));
    req.reset_password = reset;
    Ok(req)
}
```

- [ ] **Step 4: Add `pub mod cli;` to `src/lib.rs`**

```rust
pub mod cli;
pub mod config;
pub mod errors;
```

- [ ] **Step 5: Run tests to verify pass**

Run: `cd launcher-rs && cargo test --test cli`
Expected: PASS (all 9 test fns).

- [ ] **Step 6: Commit**

```bash
git add launcher-rs/src/cli.rs launcher-rs/src/lib.rs launcher-rs/tests/cli.rs
git commit -m "feat(launcher-rs): CLI argument parsing with full parity"
```

---

## Task 6: Credentials (keystore trait + resolve)

**Files:**
- Create: `launcher-rs/src/credentials.rs`
- Modify: `launcher-rs/src/lib.rs` (add `pub mod credentials;`)

**Interfaces:**
- Consumes: `LauncherConfig`, `FalcoError`.
- Produces:
  - `trait KeyStore { fn get(&self, service, user) -> FResult<Option<String>>; fn set(&self, service, user, pw) -> FResult<()>; fn delete(&self, service, user) -> FResult<()>; }`
  - `struct OsKeyStore;` implementing `KeyStore` via the `keyring` crate.
  - `fn resolve_password(cfg: &LauncherConfig, store: &dyn KeyStore, prompt: &dyn Fn(&str) -> FResult<String>) -> FResult<String>` — returns stored password, else prompts (non-empty), stores, returns.
  - `fn delete_password(cfg: &LauncherConfig, store: &dyn KeyStore)` — ignores "not found".
  - `fn hidden_prompt(label: &str) -> FResult<String>` — rpassword no-echo; blank aborts.

- [ ] **Step 1: Write the failing test** (bottom of `src/credentials.rs`)

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    use std::collections::HashMap;

    struct FakeStore {
        map: RefCell<HashMap<(String, String), String>>,
    }
    impl FakeStore {
        fn new() -> Self { Self { map: RefCell::new(HashMap::new()) } }
    }
    impl KeyStore for FakeStore {
        fn get(&self, s: &str, u: &str) -> FResult<Option<String>> {
            Ok(self.map.borrow().get(&(s.into(), u.into())).cloned())
        }
        fn set(&self, s: &str, u: &str, pw: &str) -> FResult<()> {
            self.map.borrow_mut().insert((s.into(), u.into()), pw.into());
            Ok(())
        }
        fn delete(&self, s: &str, u: &str) -> FResult<()> {
            self.map.borrow_mut().remove(&(s.into(), u.into()));
            Ok(())
        }
    }

    fn cfg() -> LauncherConfig {
        LauncherConfig {
            launcher_name: "server-client-X".into(), host: "h".into(), username: "u".into(),
            port: 22, credential_id: "cid".into(), schema_version: 1,
        }
    }

    #[test]
    fn resolve_prompts_and_stores_on_first_use() {
        let store = FakeStore::new();
        let pw = resolve_password(&cfg(), &store, &|_| Ok("secret".into())).unwrap();
        assert_eq!(pw, "secret");
        // second call returns stored value without prompting
        let pw2 = resolve_password(&cfg(), &store, &|_| panic!("should not prompt")).unwrap();
        assert_eq!(pw2, "secret");
    }

    #[test]
    fn blank_password_is_rejected() {
        let store = FakeStore::new();
        let err = resolve_password(&cfg(), &store, &|_| Ok(String::new()));
        assert!(err.is_err());
    }
}
```

- [ ] **Step 2: Run test to verify fail**

Run: `cd launcher-rs && cargo test credentials`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `src/credentials.rs`**

```rust
use crate::config::LauncherConfig;
use crate::errors::{FalcoError, FResult};

pub trait KeyStore {
    fn get(&self, service: &str, user: &str) -> FResult<Option<String>>;
    fn set(&self, service: &str, user: &str, password: &str) -> FResult<()>;
    fn delete(&self, service: &str, user: &str) -> FResult<()>;
}

pub struct OsKeyStore;

impl KeyStore for OsKeyStore {
    fn get(&self, service: &str, user: &str) -> FResult<Option<String>> {
        let entry = keyring::Entry::new(service, user)
            .map_err(|e| FalcoError::Credential(format!("keyring init failed: {e}")))?;
        match entry.get_password() {
            Ok(pw) => Ok(Some(pw)),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(e) => Err(FalcoError::Credential(format!(
                "Could not read the password from the OS credential store: {e}"
            ))),
        }
    }
    fn set(&self, service: &str, user: &str, password: &str) -> FResult<()> {
        let entry = keyring::Entry::new(service, user)
            .map_err(|e| FalcoError::Credential(format!("keyring init failed: {e}")))?;
        entry.set_password(password).map_err(|e| {
            FalcoError::Credential(format!(
                "Could not save the password to the OS credential store: {e}"
            ))
        })
    }
    fn delete(&self, service: &str, user: &str) -> FResult<()> {
        let entry = match keyring::Entry::new(service, user) {
            Ok(e) => e,
            Err(_) => return Ok(()),
        };
        // Deleting a non-existent entry is not an error worth surfacing.
        let _ = entry.delete_credential();
        Ok(())
    }
}

pub fn hidden_prompt(label: &str) -> FResult<String> {
    let pw = rpassword::prompt_password(label)
        .map_err(|e| FalcoError::Credential(format!("Could not read password: {e}")))?;
    if pw.is_empty() {
        return Err(FalcoError::Credential("No password entered; aborting.".into()));
    }
    Ok(pw)
}

pub fn delete_password(cfg: &LauncherConfig, store: &dyn KeyStore) {
    let _ = store.delete(&cfg.credential_id, &cfg.username);
}

pub fn resolve_password(
    cfg: &LauncherConfig,
    store: &dyn KeyStore,
    prompt: &dyn Fn(&str) -> FResult<String>,
) -> FResult<String> {
    if let Some(existing) = store.get(&cfg.credential_id, &cfg.username)? {
        if !existing.is_empty() {
            return Ok(existing);
        }
    }
    let label = format!("SSH password for {}@{}: ", cfg.username, cfg.host);
    let pw = prompt(&label)?;
    store.set(&cfg.credential_id, &cfg.username, &pw)?;
    Ok(pw)
}
```

- [ ] **Step 4: Add `pub mod credentials;` to `src/lib.rs`**

- [ ] **Step 5: Run tests to verify pass**

Run: `cd launcher-rs && cargo test credentials`
Expected: PASS.

> RECONCILE: the `keyring` v3 error variant for a missing entry is `keyring::Error::NoEntry` and delete is `delete_credential()`. If the resolved version differs (e.g. `delete_password()`), adjust these two call sites; behavior (None on missing, ignore delete-missing) must stay identical.

- [ ] **Step 6: Commit**

```bash
git add launcher-rs/src/credentials.rs launcher-rs/src/lib.rs
git commit -m "feat(launcher-rs): OS keystore credentials and resolve flow"
```

---

## Task 7: SSH connect + command/script execution

**Files:**
- Create: `launcher-rs/src/ssh.rs`
- Create: `launcher-rs/tests/ssh_integration.rs`
- Modify: `launcher-rs/src/lib.rs` (add `pub mod ssh;`)

**Interfaces:**
- Consumes: `LauncherConfig`, `FalcoError`.
- Produces (async):
  - `struct SshSession { /* holds russh handle */ }`
  - `async fn connect(cfg: &LauncherConfig, password: &str) -> FResult<SshSession>` — password auth, auto-accept host keys, ~15s connect timeout; auth failure → `FalcoError::Ssh` mentioning `--reset-password`.
  - `impl SshSession { async fn run_command(&self, command: &str) -> FResult<i32>; async fn run_script(&self, script: &[u8]) -> FResult<i32>; async fn open_sftp(&self) -> FResult<russh_sftp::client::SftpSession>; fn handle(&self) -> &russh::client::Handle<Client>; }`
  - `run_command`/`run_script` stream channel `Data` to stdout and `ExtendedData` (stderr) to stderr, flushing live, and return the remote exit code.

- [ ] **Step 1: Implement `src/ssh.rs`**

```rust
use std::sync::Arc;
use std::time::Duration;

use russh::client::{self, Handle};
use russh::{ChannelId, ChannelMsg, Disconnect};
use tokio::io::{AsyncWriteExt, BufWriter};

use crate::config::LauncherConfig;
use crate::errors::{FalcoError, FResult};

/// Client handler that auto-accepts ALL host keys (unknown or changed).
/// This is the documented Falco trade-off (matches Paramiko AutoAddPolicy).
pub struct Client;

impl client::Handler for Client {
    type Error = russh::Error;

    async fn check_server_key(
        &mut self,
        _server_public_key: &russh::keys::PublicKey,
    ) -> Result<bool, Self::Error> {
        Ok(true)
    }
}

pub struct SshSession {
    handle: Handle<Client>,
}

impl SshSession {
    pub fn handle(&self) -> &Handle<Client> {
        &self.handle
    }

    pub async fn run_command(&self, command: &str) -> FResult<i32> {
        self.exec(command.as_bytes(), false).await
    }

    pub async fn run_script(&self, script: &[u8]) -> FResult<i32> {
        // Feed script to `/bin/sh -s` via a channel, closing stdin (EOF).
        self.exec_with_stdin(b"/bin/sh -s", script).await
    }

    async fn exec(&self, command: &[u8], _want_reply: bool) -> FResult<i32> {
        let mut channel = self
            .handle
            .channel_open_session()
            .await
            .map_err(|e| FalcoError::Remote(format!("Failed to open session: {e}")))?;
        channel
            .exec(true, command)
            .await
            .map_err(|e| FalcoError::Remote(format!("Failed to start remote command: {e}")))?;
        pump_channel(&mut channel).await
    }

    async fn exec_with_stdin(&self, command: &[u8], stdin: &[u8]) -> FResult<i32> {
        let mut channel = self
            .handle
            .channel_open_session()
            .await
            .map_err(|e| FalcoError::Remote(format!("Failed to open session: {e}")))?;
        channel
            .exec(true, command)
            .await
            .map_err(|e| FalcoError::Remote(format!("Failed to start remote shell: {e}")))?;
        channel
            .data(stdin)
            .await
            .map_err(|e| FalcoError::Remote(format!("Failed to send script: {e}")))?;
        channel
            .eof()
            .await
            .map_err(|e| FalcoError::Remote(format!("Failed to signal EOF: {e}")))?;
        pump_channel(&mut channel).await
    }

    pub async fn disconnect(&self) {
        let _ = self
            .handle
            .disconnect(Disconnect::ByApplication, "", "en")
            .await;
    }
}

async fn pump_channel(channel: &mut russh::Channel<client::Msg>) -> FResult<i32> {
    let mut stdout = BufWriter::new(tokio::io::stdout());
    let mut stderr = BufWriter::new(tokio::io::stderr());
    let mut code: i32 = 0;
    loop {
        let Some(msg) = channel.wait().await else { break };
        match msg {
            ChannelMsg::Data { data } => {
                stdout.write_all(&data).await.ok();
                stdout.flush().await.ok();
            }
            ChannelMsg::ExtendedData { data, .. } => {
                stderr.write_all(&data).await.ok();
                stderr.flush().await.ok();
            }
            ChannelMsg::ExitStatus { exit_status } => {
                code = exit_status as i32;
            }
            ChannelMsg::Eof | ChannelMsg::Close => {}
            _ => {}
        }
    }
    stdout.flush().await.ok();
    stderr.flush().await.ok();
    Ok(code)
}

pub async fn connect(cfg: &LauncherConfig, password: &str) -> FResult<SshSession> {
    let config = Arc::new(client::Config::default());
    let addr = (cfg.host.as_str(), cfg.port);

    let connect_fut = client::connect(config, addr, Client);
    let mut handle = tokio::time::timeout(Duration::from_secs(15), connect_fut)
        .await
        .map_err(|_| {
            FalcoError::Ssh(format!(
                "Could not connect to {}:{}: timed out",
                cfg.host, cfg.port
            ))
        })?
        .map_err(|e| {
            FalcoError::Ssh(format!("Could not connect to {}:{}: {e}", cfg.host, cfg.port))
        })?;

    let authed = handle
        .authenticate_password(&cfg.username, password)
        .await
        .map_err(|e| FalcoError::Ssh(format!("Authentication error: {e}")))?;

    if !authed.success() {
        return Err(FalcoError::Ssh(format!(
            "Authentication failed for {}@{}. The stored password may be wrong; re-run with --reset-password.",
            cfg.username, cfg.host
        )));
    }
    Ok(SshSession { handle })
}

// Silence unused-import warnings if a reconciled russh drops a symbol.
#[allow(unused_imports)]
use russh::ChannelId as _ChannelId;
#[allow(dead_code)]
fn _touch(_: ChannelId) {}
```

> RECONCILE (russh API shape, most likely drift points):
> - `client::connect(config, addr, handler)` and `Handle::authenticate_password(user, pass)` returning an `AuthResult` with `.success()`. In some versions the method is `authenticate_password` returning `bool`; adapt the `authed.success()` check accordingly.
> - `Handler::check_server_key(&mut self, &PublicKey) -> Result<bool>`; key type path may be `russh::keys::PublicKey` or `russh_keys::key::PublicKey`.
> - `channel.exec(want_reply, cmd)`, `channel.data(&[u8])`, `channel.eof()`, and `channel.wait() -> Option<ChannelMsg>` with variants `Data{data}`, `ExtendedData{data, ext}`, `ExitStatus{exit_status}`. Keep the streaming + exit-code behavior identical regardless of exact names.

- [ ] **Step 2: Add `pub mod ssh;` to `src/lib.rs`**

- [ ] **Step 3: Write the integration test** (`tests/ssh_integration.rs`)

```rust
// Integration test: stand up an in-process russh server that accepts a fixed
// password and echoes an exit code, then assert connect + run_command works.
//
// NOTE: russh's server API also drifts across versions. Implement this against
// the resolved russh server traits. The assertions below are the contract:
//   - connect() succeeds with the correct password
//   - connect() returns FalcoError::Ssh with the wrong password
//   - run_command returns the remote exit status
//
// If standing up an in-process server proves version-fragile, mark this test
// #[ignore] and rely on the CI smoke test (Task 14) against a real sshd; do
// NOT delete the assertions.

#[test]
#[ignore = "requires in-process russh server; see Task 14 CI smoke test"]
fn connect_exec_exit_code() {
    // Implemented against resolved russh server API.
}
```

- [ ] **Step 4: Verify it compiles and the crate builds**

Run: `cd launcher-rs && cargo build && cargo test --test ssh_integration`
Expected: builds; the ignored test is skipped.

- [ ] **Step 5: Commit**

```bash
git add launcher-rs/src/ssh.rs launcher-rs/src/lib.rs launcher-rs/tests/ssh_integration.rs
git commit -m "feat(launcher-rs): SSH connect and command/script execution"
```

---

## Task 8: Interactive PTY shell

**Files:**
- Create: `launcher-rs/src/interactive.rs`
- Modify: `launcher-rs/src/lib.rs` (add `pub mod interactive;`)

**Interfaces:**
- Consumes: `SshSession`.
- Produces: `async fn start_interactive_shell(session: &SshSession) -> FResult<i32>` — opens a PTY channel sized to the local terminal, bridges local stdin↔channel in raw mode, forwards resize, always restores the terminal, returns exit code (0 on clean disconnect).

- [ ] **Step 1: Implement `src/interactive.rs`**

```rust
use crossterm::terminal::{disable_raw_mode, enable_raw_mode, size as term_size};
use russh::client;
use russh::ChannelMsg;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

use crate::errors::{FalcoError, FResult};
use crate::ssh::SshSession;

fn term_type() -> String {
    std::env::var("TERM").unwrap_or_else(|_| "xterm-256color".into())
}

pub async fn start_interactive_shell(session: &SshSession) -> FResult<i32> {
    let (cols, rows) = term_size().unwrap_or((80, 24));

    let mut channel = session
        .handle()
        .channel_open_session()
        .await
        .map_err(|e| FalcoError::Remote(format!("Failed to open session: {e}")))?;

    channel
        .request_pty(false, &term_type(), cols as u32, rows as u32, 0, 0, &[])
        .await
        .map_err(|e| FalcoError::Remote(format!("Failed to request PTY: {e}")))?;
    channel
        .request_shell(true)
        .await
        .map_err(|e| FalcoError::Remote(format!("Failed to start shell: {e}")))?;

    enable_raw_mode()
        .map_err(|e| FalcoError::Remote(format!("Failed to set raw mode: {e}")))?;

    let result = bridge(&mut channel).await;

    // ALWAYS restore the terminal, even after error/disconnect.
    let _ = disable_raw_mode();

    let _ = channel.close().await;
    result
}

async fn bridge(channel: &mut russh::Channel<client::Msg>) -> FResult<i32> {
    let mut stdin = tokio::io::stdin();
    let mut stdout = tokio::io::stdout();
    let mut buf = [0u8; 32768];
    let mut code = 0i32;

    loop {
        tokio::select! {
            // Local keystrokes -> remote.
            n = stdin.read(&mut buf) => {
                match n {
                    Ok(0) => { let _ = channel.eof().await; }
                    Ok(n) => {
                        if channel.data(&buf[..n]).await.is_err() { break; }
                    }
                    Err(_) => break,
                }
            }
            // Remote output -> local terminal.
            msg = channel.wait() => {
                match msg {
                    Some(ChannelMsg::Data { data }) => {
                        stdout.write_all(&data).await.ok();
                        stdout.flush().await.ok();
                    }
                    Some(ChannelMsg::ExtendedData { data, .. }) => {
                        stdout.write_all(&data).await.ok();
                        stdout.flush().await.ok();
                    }
                    Some(ChannelMsg::ExitStatus { exit_status }) => {
                        code = exit_status as i32;
                    }
                    Some(ChannelMsg::Eof) | Some(ChannelMsg::Close) | None => break,
                    _ => {}
                }
            }
        }
    }
    Ok(code)
}
```

> RECONCILE:
> - Terminal resize: `crossterm` exposes resize via its event stream. For MVP the PTY is sized once at open (matches the Windows path of the Python launcher). To match the POSIX SIGWINCH behavior fully, add a third `select!` arm reading `crossterm::event::EventStream` for `Event::Resize(cols, rows)` and call `channel.window_change(cols as u32, rows as u32, 0, 0)`. Add this only after the basic bridge works.
> - `channel.request_pty(...)`, `request_shell(...)`, `window_change(...)` names may differ slightly by russh version.

- [ ] **Step 2: Add `pub mod interactive;` to `src/lib.rs`**

- [ ] **Step 3: Verify it builds**

Run: `cd launcher-rs && cargo build`
Expected: compiles.

- [ ] **Step 4: Manual smoke (documented, not automated)**

PTY behavior is not unit-testable in CI. Record a manual check to run after Task 13 wires `main`: build a configured launcher, run it with no args against a test host, confirm: prompt appears, `ls --color` shows colours, arrow-key history works, `exit` returns you to a normal (restored) local terminal.

- [ ] **Step 5: Commit**

```bash
git add launcher-rs/src/interactive.rs launcher-rs/src/lib.rs
git commit -m "feat(launcher-rs): interactive PTY shell bridge"
```

---

## Task 9: SFTP path helpers (pure logic)

**Files:**
- Create: `launcher-rs/src/sftp.rs`
- Modify: `launcher-rs/src/lib.rs` (add `pub mod sftp;`)

**Interfaces:**
- Consumes: `FalcoError`, `cli::{SftpOp, SftpAction}`.
- Produces (pure, unit-testable helpers used by Task 10):
  - `fn join_remote(dir: &str, name: &str) -> String` — POSIX join (always `/`).
  - `fn human_bytes(n: u64) -> String` — e.g. `1.5 MB`, for progress output.
  - `fn temp_download_name(final_path: &str) -> String` — `<final>.falco-partial`.

- [ ] **Step 1: Write the failing tests** (bottom of `src/sftp.rs`)

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn join_remote_uses_forward_slash() {
        assert_eq!(join_remote("/var/www", "index.html"), "/var/www/index.html");
        assert_eq!(join_remote("/var/www/", "index.html"), "/var/www/index.html");
    }

    #[test]
    fn temp_name_is_partial_suffix() {
        assert_eq!(temp_download_name("/tmp/f.bin"), "/tmp/f.bin.falco-partial");
    }

    #[test]
    fn human_bytes_scales() {
        assert_eq!(human_bytes(512), "512 B");
        assert_eq!(human_bytes(1536), "1.5 KB");
    }
}
```

- [ ] **Step 2: Run tests to verify fail**

Run: `cd launcher-rs && cargo test sftp`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the helpers** (top of `src/sftp.rs`)

```rust
pub fn join_remote(dir: &str, name: &str) -> String {
    let trimmed = dir.strip_suffix('/').unwrap_or(dir);
    format!("{trimmed}/{name}")
}

pub fn temp_download_name(final_path: &str) -> String {
    format!("{final_path}.falco-partial")
}

pub fn human_bytes(n: u64) -> String {
    const UNITS: [&str; 5] = ["B", "KB", "MB", "GB", "TB"];
    if n < 1024 {
        return format!("{n} B");
    }
    let mut value = n as f64;
    let mut unit = 0;
    while value >= 1024.0 && unit < UNITS.len() - 1 {
        value /= 1024.0;
        unit += 1;
    }
    format!("{value:.1} {}", UNITS[unit])
}
```

- [ ] **Step 4: Add `pub mod sftp;` to `src/lib.rs`**

- [ ] **Step 5: Run tests to verify pass**

Run: `cd launcher-rs && cargo test sftp`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add launcher-rs/src/sftp.rs launcher-rs/src/lib.rs
git commit -m "feat(launcher-rs): SFTP path/format helpers"
```

---

## Task 10: SFTP operations (execute)

**Files:**
- Modify: `launcher-rs/src/sftp.rs`

**Interfaces:**
- Consumes: `russh_sftp::client::SftpSession`, `cli::{SftpOp, SftpAction}`, helpers from Task 9.
- Produces: `async fn execute(sftp: &russh_sftp::client::SftpSession, op: &SftpOp, overwrite: bool, mkdirs: bool) -> FResult<()>` dispatching all 8 actions with the behavioral rules below.

Behavioral rules (from [editor/templates.py](../../../editor/templates.py) "File transfer"):
- `upload local remote`, `download remote local`, `list dir`, `mkdir dir`, `remove path`, `move a b`, `upload-dir local remote` (recursive), `download-dir remote local` (recursive).
- Never overwrite an existing destination file unless `overwrite`.
- `mkdirs` creates missing destination directories.
- Downloads: write to `temp_download_name(dest)` then rename to `dest` on success.
- Print live progress lines (bytes transferred, using `human_bytes`).
- Any failure returns `FalcoError::Remote(...)` (caller maps to exit 4 / non-zero).

- [ ] **Step 1: Implement `execute` and per-action helpers** (append to `src/sftp.rs`)

```rust
use russh_sftp::client::SftpSession;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

use crate::cli::{SftpAction, SftpOp};
use crate::errors::{FalcoError, FResult};

pub async fn execute(
    sftp: &SftpSession,
    op: &SftpOp,
    overwrite: bool,
    mkdirs: bool,
) -> FResult<()> {
    let a = &op.operands;
    match op.action {
        SftpAction::Upload => upload_file(sftp, &a[0], &a[1], overwrite, mkdirs).await,
        SftpAction::Download => download_file(sftp, &a[0], &a[1], overwrite).await,
        SftpAction::List => list_dir(sftp, &a[0]).await,
        SftpAction::Mkdir => make_dir(sftp, &a[0]).await,
        SftpAction::Remove => remove_path(sftp, &a[0]).await,
        SftpAction::Move => move_path(sftp, &a[0], &a[1]).await,
        SftpAction::UploadDir => upload_dir(sftp, &a[0], &a[1], overwrite, mkdirs).await,
        SftpAction::DownloadDir => download_dir(sftp, &a[0], &a[1], overwrite).await,
    }
}

fn remote_err(context: &str, e: impl std::fmt::Display) -> FalcoError {
    FalcoError::Remote(format!("{context}: {e}"))
}

async fn remote_exists(sftp: &SftpSession, path: &str) -> bool {
    sftp.metadata(path.to_string()).await.is_ok()
}

async fn ensure_remote_parent(sftp: &SftpSession, remote_path: &str, mkdirs: bool) -> FResult<()> {
    if !mkdirs {
        return Ok(());
    }
    if let Some(idx) = remote_path.rfind('/') {
        let parent = &remote_path[..idx];
        if !parent.is_empty() && !remote_exists(sftp, parent).await {
            // Create each missing ancestor.
            let mut acc = String::new();
            for part in parent.split('/') {
                if part.is_empty() {
                    acc.push('/');
                    continue;
                }
                if !acc.ends_with('/') {
                    acc.push('/');
                }
                acc.push_str(part);
                if !remote_exists(sftp, &acc).await {
                    let _ = sftp.create_dir(acc.clone()).await;
                }
            }
        }
    }
    Ok(())
}

async fn upload_file(
    sftp: &SftpSession,
    local: &str,
    remote: &str,
    overwrite: bool,
    mkdirs: bool,
) -> FResult<()> {
    if !overwrite && remote_exists(sftp, remote).await {
        return Err(FalcoError::Remote(format!(
            "Refusing to overwrite existing remote file {remote} (use --overwrite)."
        )));
    }
    ensure_remote_parent(sftp, remote, mkdirs).await?;

    let mut src = tokio::fs::File::open(local)
        .await
        .map_err(|e| remote_err(&format!("cannot open {local}"), e))?;
    let mut dst = sftp
        .create(remote.to_string())
        .await
        .map_err(|e| remote_err(&format!("cannot create {remote}"), e))?;

    let mut buf = vec![0u8; 32768];
    let mut total: u64 = 0;
    loop {
        let n = src.read(&mut buf).await.map_err(|e| remote_err("read error", e))?;
        if n == 0 {
            break;
        }
        dst.write_all(&buf[..n]).await.map_err(|e| remote_err("write error", e))?;
        total += n as u64;
        print!("\rUploading {remote}: {}", human_bytes(total));
        let _ = std::io::Write::flush(&mut std::io::stdout());
    }
    dst.flush().await.map_err(|e| remote_err("flush error", e))?;
    println!("\rUploaded {remote}: {} ", human_bytes(total));
    Ok(())
}

async fn download_file(
    sftp: &SftpSession,
    remote: &str,
    local: &str,
    overwrite: bool,
) -> FResult<()> {
    if !overwrite && tokio::fs::try_exists(local).await.unwrap_or(false) {
        return Err(FalcoError::Remote(format!(
            "Refusing to overwrite existing local file {local} (use --overwrite)."
        )));
    }
    let tmp = temp_download_name(local);
    let mut src = sftp
        .open(remote.to_string())
        .await
        .map_err(|e| remote_err(&format!("cannot open remote {remote}"), e))?;
    let mut dst = tokio::fs::File::create(&tmp)
        .await
        .map_err(|e| remote_err(&format!("cannot create {tmp}"), e))?;

    let mut buf = vec![0u8; 32768];
    let mut total: u64 = 0;
    loop {
        let n = src.read(&mut buf).await.map_err(|e| remote_err("read error", e))?;
        if n == 0 {
            break;
        }
        dst.write_all(&buf[..n]).await.map_err(|e| remote_err("write error", e))?;
        total += n as u64;
        print!("\rDownloading {local}: {}", human_bytes(total));
        let _ = std::io::Write::flush(&mut std::io::stdout());
    }
    dst.flush().await.map_err(|e| remote_err("flush error", e))?;
    drop(dst);
    // Rename temp -> final only on success (no truncated file on interruption).
    tokio::fs::rename(&tmp, local)
        .await
        .map_err(|e| remote_err("rename error", e))?;
    println!("\rDownloaded {local}: {} ", human_bytes(total));
    Ok(())
}

async fn list_dir(sftp: &SftpSession, dir: &str) -> FResult<()> {
    let entries = sftp
        .read_dir(dir.to_string())
        .await
        .map_err(|e| remote_err(&format!("cannot list {dir}"), e))?;
    for entry in entries {
        println!("{}", entry.file_name());
    }
    Ok(())
}

async fn make_dir(sftp: &SftpSession, dir: &str) -> FResult<()> {
    sftp.create_dir(dir.to_string())
        .await
        .map_err(|e| remote_err(&format!("cannot mkdir {dir}"), e))
}

async fn remove_path(sftp: &SftpSession, path: &str) -> FResult<()> {
    sftp.remove_file(path.to_string())
        .await
        .map_err(|e| remote_err(&format!("cannot remove {path}"), e))
}

async fn move_path(sftp: &SftpSession, from: &str, to: &str) -> FResult<()> {
    sftp.rename(from.to_string(), to.to_string())
        .await
        .map_err(|e| remote_err(&format!("cannot move {from} -> {to}"), e))
}

async fn upload_dir(
    sftp: &SftpSession,
    local: &str,
    remote: &str,
    overwrite: bool,
    _mkdirs: bool,
) -> FResult<()> {
    // Ensure the remote root exists.
    if !remote_exists(sftp, remote).await {
        let _ = sftp.create_dir(remote.to_string()).await;
    }
    let mut stack = vec![(std::path::PathBuf::from(local), remote.to_string())];
    while let Some((local_dir, remote_dir)) = stack.pop() {
        let mut rd = tokio::fs::read_dir(&local_dir)
            .await
            .map_err(|e| remote_err(&format!("cannot read {}", local_dir.display()), e))?;
        while let Some(entry) = rd
            .next_entry()
            .await
            .map_err(|e| remote_err("dir walk error", e))?
        {
            let name = entry.file_name().to_string_lossy().to_string();
            let child_remote = join_remote(&remote_dir, &name);
            let ft = entry.file_type().await.map_err(|e| remote_err("stat error", e))?;
            if ft.is_dir() {
                if !remote_exists(sftp, &child_remote).await {
                    let _ = sftp.create_dir(child_remote.clone()).await;
                }
                stack.push((entry.path(), child_remote));
            } else {
                upload_file(
                    sftp,
                    &entry.path().to_string_lossy(),
                    &child_remote,
                    overwrite,
                    false,
                )
                .await?;
            }
        }
    }
    Ok(())
}

async fn download_dir(
    sftp: &SftpSession,
    remote: &str,
    local: &str,
    overwrite: bool,
) -> FResult<()> {
    tokio::fs::create_dir_all(local)
        .await
        .map_err(|e| remote_err(&format!("cannot create {local}"), e))?;
    let mut stack = vec![(remote.to_string(), std::path::PathBuf::from(local))];
    while let Some((remote_dir, local_dir)) = stack.pop() {
        let entries = sftp
            .read_dir(remote_dir.clone())
            .await
            .map_err(|e| remote_err(&format!("cannot list {remote_dir}"), e))?;
        for entry in entries {
            let name = entry.file_name();
            let child_remote = join_remote(&remote_dir, &name);
            let child_local = local_dir.join(&name);
            if entry.file_type().is_dir() {
                tokio::fs::create_dir_all(&child_local)
                    .await
                    .map_err(|e| remote_err("mkdir error", e))?;
                stack.push((child_remote, child_local));
            } else {
                download_file(
                    sftp,
                    &child_remote,
                    &child_local.to_string_lossy(),
                    overwrite,
                )
                .await?;
            }
        }
    }
    Ok(())
}
```

> RECONCILE (russh-sftp API): method names used — `metadata`, `create`, `open`, `read_dir` (returning entries exposing `file_name()` and `file_type()`), `create_dir`, `remove_file`, `rename`. `create`/`open` return async read/write handles implementing tokio `AsyncRead`/`AsyncWrite`. Adjust to the resolved crate's exact names/handle traits while preserving: overwrite guard, temp-then-rename download, recursion, progress. `sftp.remove_file` handles files; for `--remove` on a directory, fall back to `remove_dir` if `remove_file` fails.

- [ ] **Step 2: Verify it builds and helper tests still pass**

Run: `cd launcher-rs && cargo build && cargo test sftp`
Expected: builds; Task 9 helper tests pass.

- [ ] **Step 3: Commit**

```bash
git add launcher-rs/src/sftp.rs
git commit -m "feat(launcher-rs): SFTP operations with recursion, guards, temp-rename"
```

---

## Task 11: Wire `main` (dispatch + exit codes)

**Files:**
- Modify: `launcher-rs/src/main.rs`

**Interfaces:**
- Consumes: everything above.
- Produces: `main` that loads embedded config, parses argv, resolves password (honoring `--reset-password`), connects, dispatches by mode, prints `falco: <error>` to stderr on failure, and exits with the contract codes.

- [ ] **Step 1: Implement `src/main.rs`**

```rust
use std::process::ExitCode;

use falco_stub::cli::{self, Mode};
use falco_stub::config::load_embedded_config;
use falco_stub::credentials::{self, hidden_prompt, OsKeyStore};
use falco_stub::errors::FalcoError;
use falco_stub::{interactive, sftp, ssh};

fn main() -> ExitCode {
    let rt = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .expect("tokio runtime");
    let code = rt.block_on(run());
    ExitCode::from(code as u8)
}

async fn run() -> i32 {
    // 1. Load embedded (non-secret) config from our own file.
    let config = match load_embedded_config() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("falco: {e}");
            return 2;
        }
    };

    // 2. Parse argv (excluding program name).
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let request = match cli::parse_args(&argv) {
        Ok(r) => r,
        Err(e) => {
            eprintln!("falco: {e}");
            return 2;
        }
    };

    // 3. Resolve the password (prompt once, store).
    let store = OsKeyStore;
    if request.reset_password {
        credentials::delete_password(&config, &store);
    }
    let password = match credentials::resolve_password(&config, &store, &|label| hidden_prompt(label)) {
        Ok(p) => p,
        Err(e) => {
            eprintln!("falco: {e}");
            return 3;
        }
    };

    // 4. Connect and dispatch.
    match dispatch(&config, &password, &request).await {
        Ok(code) => code,
        Err(e) => {
            eprintln!("falco: {e}");
            e.exit_code()
        }
    }
}

async fn dispatch(
    config: &falco_stub::config::LauncherConfig,
    password: &str,
    request: &cli::LaunchRequest,
) -> Result<i32, FalcoError> {
    let session = ssh::connect(config, password).await?;

    let result = match request.mode {
        Mode::Interactive => interactive::start_interactive_shell(&session).await,
        Mode::Command => {
            let cmd = request.command.as_deref().unwrap_or("");
            session.run_command(cmd).await
        }
        Mode::Stdin => {
            let path = request.stdin_path.as_deref().unwrap_or("");
            match std::fs::read(path) {
                Ok(bytes) => session.run_script(&bytes).await,
                Err(e) => {
                    session.disconnect().await;
                    return Err(FalcoError::Config(format!("cannot read {path}: {e}")));
                }
            }
        }
        Mode::Sftp => {
            let op = request.sftp.as_ref().expect("sftp op present");
            let sftp_session = session.open_sftp().await?;
            let r = sftp::execute(&sftp_session, op, request.overwrite, request.mkdirs)
                .await
                .map(|_| 0);
            r
        }
    };

    session.disconnect().await;
    result
}
```

- [ ] **Step 2: Add `open_sftp` to `SshSession`** in `src/ssh.rs`

```rust
impl SshSession {
    pub async fn open_sftp(&self) -> FResult<russh_sftp::client::SftpSession> {
        let channel = self
            .handle
            .channel_open_session()
            .await
            .map_err(|e| FalcoError::Ssh(format!("Could not open SFTP session: {e}")))?;
        channel
            .request_subsystem(true, "sftp")
            .await
            .map_err(|e| FalcoError::Ssh(format!("Could not start SFTP subsystem: {e}")))?;
        russh_sftp::client::SftpSession::new(channel.into_stream())
            .await
            .map_err(|e| FalcoError::Ssh(format!("Could not open SFTP session: {e}")))
    }
}
```

> RECONCILE: `channel.request_subsystem(want_reply, "sftp")` then bridging the channel into `russh_sftp::client::SftpSession::new(...)`. The exact adapter (`channel.into_stream()` vs a provided wrapper) depends on the resolved `russh-sftp` version; use whatever that version documents for "create an SftpSession from a russh channel".

- [ ] **Step 3: Verify the whole crate builds**

Run: `cd launcher-rs && cargo build --release`
Expected: compiles; produces `target/release/falco-stub`.

- [ ] **Step 4: Verify config self-read end-to-end (no network)**

Run:
```bash
cd launcher-rs
cargo run --release -- --help-nonexistent 2>&1 | head -1
```
Expected: prints `falco: This stub was not configured by the Falco editor.` (because the dev binary has no trailer), confirming the load-config path works.

- [ ] **Step 5: Commit**

```bash
git add launcher-rs/src/main.rs launcher-rs/src/ssh.rs
git commit -m "feat(launcher-rs): wire main dispatch, SFTP subsystem, exit codes"
```

---

## Task 12: Size verification

**Files:**
- Create: `docs/launcher-rs-size.md`

**Interfaces:** none (verification + documentation).

- [ ] **Step 1: Build release and measure**

Run:
```bash
cd launcher-rs && cargo build --release
ls -l target/release/falco-stub
```
Expected: a single binary. Record its byte size.

- [ ] **Step 2: Assert the size budget**

If size > 5 MB: enable additional levers before proceeding — confirm `strip=true` applied, try `opt-level="s"` vs `"z"`, and audit heavy default features (e.g. disable unused tokio features, unused crossterm features). Re-measure. The task is not complete until size ≤ 5 MB.

- [ ] **Step 3: Document the result** (`docs/launcher-rs-size.md`)

```markdown
# Rust launcher binary size

Measured size of `falco-stub` (release profile: opt-level=z, lto, panic=abort, strip):

| OS | Size |
|----|------|
| <fill per-OS from CI> | <fill> |

This replaces the ~25 MB PyInstaller launcher documented in launcher-size.md.
```

Fill the table with the actual measured value(s).

- [ ] **Step 4: Commit**

```bash
git add docs/launcher-rs-size.md
git commit -m "docs: record Rust launcher binary size"
```

---

## Task 13: Editor build integration (Python)

**Files:**
- Modify: `editor/builder.py`
- Modify: `build/build_launcher.py`
- Test: `tests/` (find and update the existing builder test; e.g. `tests/test_builder.py`)

**Interfaces:**
- Preserve the public signature: `build_launcher(config, *, output_name, icon_path=None, output_dir, progress=None) -> BuildResult` and `BuildResult(executable, work_dir, how_to_use)`.
- New helper: `stub_path_for_current_os() -> Path` — locates the bundled `falco-stub` (frozen: alongside the editor via `sys._MEIPASS`; dev: `launcher-rs/target/release/falco-stub[.exe]`).
- New helper: `append_config(stub_bytes: bytes, config: LauncherConfig) -> bytes` — mirrors the Rust trailer: `json + u64 LE length + b"FALCOCFG"`.

- [ ] **Step 1: Write the failing test** (`tests/test_builder.py`, add/replace)

```python
import struct
from pathlib import Path

from editor.builder import append_config, MAGIC
from shared.config import LauncherConfig


def test_append_config_writes_trailer():
    cfg = LauncherConfig.create(
        launcher_name="server-client-X", host="1.2.3.4", username="root", port=22
    )
    stub = b"FAKE-STUB"
    blob = append_config(stub, cfg)

    assert blob.startswith(stub)
    assert blob.endswith(MAGIC)
    length = struct.unpack("<Q", blob[-16:-8])[0]
    json_bytes = blob[-16 - length : -16]
    assert b'"host": "1.2.3.4"' in json_bytes or b'"host":"1.2.3.4"' in json_bytes
```

- [ ] **Step 2: Run test to verify fail**

Run: `python -m pytest tests/test_builder.py::test_append_config_writes_trailer -v`
Expected: FAIL — `append_config` / `MAGIC` not importable.

- [ ] **Step 3: Rewrite `editor/builder.py`**

Replace the PyInstaller machinery with the stub-append model. Keep `BuildResult` and the public `build_launcher` signature. New content:

```python
"""Turn a LauncherConfig into a standalone executable by configuring a prebuilt
Rust stub.

"Building" a launcher no longer compiles anything: we copy the bundled
``falco-stub`` binary for the current OS and append the (non-secret) config as a
trailer the stub reads from its own file at runtime. This needs no toolchain, so
even a frozen editor can produce launchers offline.
"""

from __future__ import annotations

import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from editor.templates import render_how_to_use
from shared.config import LauncherConfig
from shared.errors import FalcoError

ProgressFn = Callable[[str], None]

MAGIC = b"FALCOCFG"

_REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class BuildResult:
    executable: Path
    work_dir: Path
    how_to_use: Path


def _emit(progress: ProgressFn | None, message: str) -> None:
    if progress is not None:
        progress(message)


def _strip_exe_suffix(name: str) -> str:
    for suffix in (".exe", ".app", ".bin"):
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return name


def stub_path_for_current_os() -> Path:
    """Locate the bundled falco-stub for the OS the editor is running on."""

    exe_name = "falco-stub.exe" if sys.platform == "win32" else "falco-stub"

    # Frozen editor: PyInstaller unpacks bundled data under sys._MEIPASS.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = Path(meipass) / "stub" / exe_name
        if candidate.exists():
            return candidate

    # Dev: use the cargo release build.
    candidate = _REPO_ROOT / "launcher-rs" / "target" / "release" / exe_name
    if candidate.exists():
        return candidate

    raise FalcoError(
        "Could not find the falco-stub binary. Build it with "
        "`cd launcher-rs && cargo build --release`, or ensure it is bundled "
        "with the editor."
    )


def append_config(stub_bytes: bytes, config: LauncherConfig) -> bytes:
    """Append the config trailer (json + u64 LE length + magic) to a stub."""

    json_bytes = config.to_json(indent=None).encode("utf-8")
    length = struct.pack("<Q", len(json_bytes))
    return stub_bytes + json_bytes + length + MAGIC


def _resolve_output_path(dist_dir: Path, output_name: str) -> Path:
    name = _strip_exe_suffix(output_name)
    if sys.platform == "win32":
        return dist_dir / f"{name}.exe"
    return dist_dir / name


def build_launcher(
    config: LauncherConfig,
    *,
    output_name: str,
    icon_path: str | Path | None = None,  # accepted for API compatibility; unused
    output_dir: str | Path,
    progress: ProgressFn | None = None,
) -> BuildResult:
    _emit(progress, "Locating prebuilt launcher stub…")
    stub = stub_path_for_current_os()
    _emit(progress, f"Using stub: {stub}")

    dist_dir = Path(output_dir).resolve()
    dist_dir.mkdir(parents=True, exist_ok=True)
    work_dir = dist_dir / f".falco-build-{_strip_exe_suffix(output_name)}"
    work_dir.mkdir(parents=True, exist_ok=True)

    stub_bytes = stub.read_bytes()
    _emit(progress, "Embedding launcher configuration (no password embedded)…")
    blob = append_config(stub_bytes, config)

    exe = _resolve_output_path(dist_dir, output_name)
    exe.write_bytes(blob)
    if sys.platform != "win32":
        exe.chmod(0o755)
    _emit(progress, f"Wrote launcher: {exe}")

    how_to_use = dist_dir / "how-to-use.md"
    how_to_use.write_text(render_how_to_use(config, output_name), encoding="utf-8")
    _emit(progress, f"Wrote usage guide: {how_to_use}")

    return BuildResult(executable=exe, work_dir=work_dir, how_to_use=how_to_use)
```

- [ ] **Step 4: Confirm `LauncherConfig.to_json` supports `indent=None`**

Check [shared/config.py:92](../../../shared/config.py) — `to_json(self, *, indent: int | None = 2)` already accepts `indent`. No change needed. (The Rust side parses either compact or pretty JSON.)

- [ ] **Step 5: Update `build/build_launcher.py`**

Point the CLI build path at the same `build_launcher`. Replace its PyInstaller invocation with:

```python
"""CLI wrapper to build a single launcher from a JSON config file."""

from __future__ import annotations

import argparse
from pathlib import Path

from editor.builder import build_launcher
from shared.config import LauncherConfig


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a Falco launcher from config JSON.")
    parser.add_argument("config", help="Path to launcher config JSON")
    parser.add_argument("--output-name", required=True, help="e.g. server-client-X.exe")
    parser.add_argument("--out-dir", default="dist", help="Output directory")
    args = parser.parse_args(argv)

    config = LauncherConfig.load(args.config)
    result = build_launcher(
        config, output_name=args.output_name, output_dir=args.out_dir
    )
    print(f"Built {result.executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Run the builder test**

Run: `python -m pytest tests/test_builder.py -v`
Expected: PASS. Delete or update any old test that asserted on `pyinstaller_command`/PyInstaller args (those are gone); replace with the trailer test above.

- [ ] **Step 7: End-to-end local check (dev stub)**

Run:
```bash
cd launcher-rs && cargo build --release && cd ..
python -m build.build_launcher <(python -c "from shared.config import LauncherConfig; print(LauncherConfig.create(launcher_name='server-client-X', host='127.0.0.1', username='root').to_json())") --output-name server-client-X --out-dir /tmp/falco-out || true
```
(Windows: write the config JSON to a temp file first, then pass its path.)
Expected: a `server-client-X`/`server-client-X.exe` appears in the out dir; running it with no config-reachable server still proves the trailer path (`falco: Authentication...`/connection error, not "not configured").

- [ ] **Step 8: Commit**

```bash
git add editor/builder.py build/build_launcher.py tests/test_builder.py
git commit -m "feat(editor): build launchers by configuring the Rust stub"
```

---

## Task 14: CI — build stub per OS + smoke test

**Files:**
- Modify: `.github/workflows/build-linux.yml`, `build-macos.yml`, `build-windows.yml` (each builds the editor for one OS; add a stub build step before packaging)
- Create: `.github/workflows/smoke.yml` (or add to `tests.yml`)

**Interfaces:** none (CI).

- [ ] **Step 1: Inspect the existing workflows**

Run: `for f in .github/workflows/*.yml; do echo "== $f =="; cat "$f"; done`
Note the structure: three per-OS build files (`build-linux/macos/windows.yml`),
plus `release.yml` and `tests.yml`. Each per-OS file runs the editor's
PyInstaller build. In each, add a Rust step **before** the editor packaging step
so the stub exists when PyInstaller bundles it.

- [ ] **Step 2: Add a Rust stub build step to each per-OS build workflow**

In `build-linux.yml`, `build-macos.yml`, and `build-windows.yml`, before the
editor `python build/build_editor.py` step, add:

```yaml
      - uses: dtolnay/rust-toolchain@stable
      - name: Build launcher stub
        run: cargo build --release --manifest-path launcher-rs/Cargo.toml
```

The stub lands at `launcher-rs/target/release/falco-stub[.exe]`, exactly where
`build/build_editor.py` (Task 15) looks for it. No artifact upload/download is
needed because the stub and the editor are built on the same runner/OS.

(If you prefer the matrix form instead of three files, the equivalent job is:)

```yaml
  build-stub:
    strategy:
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - name: Build stub
        run: cargo build --release --manifest-path launcher-rs/Cargo.toml
      - name: Upload stub
        uses: actions/upload-artifact@v4
        with:
          name: falco-stub-${{ matrix.os }}
          path: |
            launcher-rs/target/release/falco-stub
            launcher-rs/target/release/falco-stub.exe
          if-no-files-found: ignore
```

- [ ] **Step 3: Confirm the editor packaging step picks up the stub**

Because the stub is built on the same runner (Step 2), `build/build_editor.py`
(Task 15) finds it at `launcher-rs/target/release/` and bundles it via
`--add-data`. No artifact plumbing needed. Just ensure the Rust step runs
*before* `python build/build_editor.py` in each per-OS workflow.

- [ ] **Step 4: Add a smoke test job**

Add a job (Linux) that: builds the stub, starts a throwaway `sshd` in a container (or `docker run` an openssh image with a known password), generates a launcher via `build/build_launcher.py`, and runs `./server-client-X "echo hello"` asserting stdout `hello` and exit `0`, plus `./server-client-X --nonexistent-should-parse-ok? ` sanity. Keep it minimal but real — this is the automated replacement for the PTY/SSH tests that can't run as unit tests.

```yaml
  smoke:
    runs-on: ubuntu-latest
    needs: build-stub
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - name: Build stub
        run: cargo build --release --manifest-path launcher-rs/Cargo.toml
      - name: Start test sshd
        run: |
          docker run -d --name sshd -p 2222:2222 \
            -e USER_NAME=root -e USER_PASSWORD=testpass -e PASSWORD_ACCESS=true \
            lscr.io/linuxserver/openssh-server:latest
          sleep 8
      - name: Generate launcher and run a command
        run: |
          pip install -e .
          python - <<'PY'
          from shared.config import LauncherConfig
          from editor.builder import build_launcher
          cfg = LauncherConfig.create(launcher_name="t", host="127.0.0.1", username="root", port=2222)
          build_launcher(cfg, output_name="t", output_dir="out")
          PY
          # Pre-seed the credential store is not available headless; instead
          # exercise the parse/connect path. For exec, use SSH_ASKPASS-free flow
          # by piping the password via the first-run prompt:
          printf 'testpass\n' | ./out/t "echo hello" | tee result.txt
          grep -q hello result.txt
```

> RECONCILE: headless keyring may be unavailable on CI Linux. Options, pick one when wiring: (a) install/allow `keyring` with the `linux-keyutils`/`secret-service` backend and a dbus session; or (b) add a test-only env hook so the launcher reads the password from `FALCO_TEST_PASSWORD` when set (guard behind `#[cfg(debug_assertions)]` or a cargo feature so it never ships in release). Prefer (a) to keep release behavior pure; fall back to (b) if the CI keyring proves too fragile. Document whichever is chosen in the workflow comments.

- [ ] **Step 4b: Verify CI is green**

Push the branch; confirm `build-stub`, editor packaging, and `smoke` jobs pass on all three OSes.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows
git commit -m "ci: build Rust stub per OS and smoke-test generated launchers"
```

---

## Task 15: Editor packaging bundles the stub; retire Python launcher

**Files:**
- Modify: `build/build_editor.py`
- Delete: `launcher/` (Python launcher package) and its tests
- Modify: `pyproject.toml` (drop `paramiko`; drop `launcher` from packages)

**Interfaces:** editor packaging embeds the stub; runtime no longer imports `launcher`/`paramiko`.

- [ ] **Step 1: Bundle the stub in `build/build_editor.py`**

Add `--add-data` so the frozen editor ships the stub. Insert into `pyinstaller_command` (after the `--hidden-import` block):

```python
    # Bundle the prebuilt launcher stub so the editor can produce launchers
    # with no toolchain. Expected at launcher-rs/target/release/ (built by CI
    # or `cargo build --release` locally) or downloaded into ./stub/ in CI.
    sep = ";" if sys.platform == "win32" else ":"
    stub_name = "falco-stub.exe" if sys.platform == "win32" else "falco-stub"
    stub_src = _REPO_ROOT / "stub" / stub_name
    if not stub_src.exists():
        stub_src = _REPO_ROOT / "launcher-rs" / "target" / "release" / stub_name
    if not stub_src.exists():
        raise SystemExit(f"stub binary not found: build launcher-rs first ({stub_src})")
    cmd += ["--add-data", f"{stub_src}{sep}stub"]
```

Also remove the now-irrelevant `--hidden-import launcher`/`paramiko` lines from the *editor* build (the editor never imported those; verify and clean up if present).

- [ ] **Step 2: Verify the frozen editor finds the stub**

Run:
```bash
cd launcher-rs && cargo build --release && cd ..
python build/build_editor.py --out-dir dist
```
Then run the produced editor binary and click Build against a dummy config; confirm it locates the bundled stub (the progress log prints `Using stub: …` pointing under the unpacked `_MEIPASS/stub`).

- [ ] **Step 3: Delete the Python launcher and its tests**

The Python-launcher tests are `tests/test_cli.py`, `tests/test_credentials.py`,
`tests/test_sftp.py`, `tests/test_exit_code.py`. Keep `tests/test_builder.py`
(editor) and `tests/test_config.py` (shared).

```bash
git rm -r launcher
git rm tests/test_cli.py tests/test_credentials.py tests/test_sftp.py tests/test_exit_code.py
```

- [ ] **Step 4: Update `pyproject.toml`**

- Remove `"paramiko>=3.4"` from `dependencies` (the editor doesn't use it; the launcher is Rust now). Keep `keyring` only if the editor itself reads credentials — it does not, so remove it too. Net runtime deps for the editor: none beyond stdlib + Tk.
- Change `packages = ["editor", "launcher", "shared"]` → `packages = ["editor", "shared"]`.
- Remove the `launcher` entry anywhere else it appears.

- [ ] **Step 5: Run the full Python test suite**

Run: `python -m pytest -q`
Expected: PASS. The remaining tests cover `shared/config.py` and `editor/builder.py`. Fix any import of the deleted `launcher` package.

- [ ] **Step 6: Update docs**

- In `docs/launcher-size.md`, add a top note: "Superseded — the launcher is now a Rust binary; see launcher-rs-size.md."
- Update `README.md` sections that describe the launcher as Python/PyInstaller to describe the Rust stub + append model (find with `grep -ni pyinstaller README.md`).

- [ ] **Step 7: Commit**

```bash
git add build/build_editor.py pyproject.toml docs/launcher-size.md README.md tests
git commit -m "chore: bundle Rust stub in editor, retire Python launcher"
```

---

## Self-Review (completed by plan author)

**Spec coverage:**
- Prebuilt stub + appended config → Tasks 4, 13. ✅
- russh pure-Rust SSH → Tasks 7, 11. ✅
- Full parity: CLI (Task 5), credentials (Task 6), command/stdin exec (Task 7), PTY (Task 8), SFTP 8 ops (Tasks 9–10), exit codes (Tasks 2, 11). ✅
- Config trailer format → Tasks 4 & 13 use the identical layout (`json + u64 LE + b"FALCOCFG"`). ✅
- Credential id format → Task 3 test pins `falco:server-client-X:root@1.2.3.4:22`. ✅
- Auto-accept host keys → Task 7 `check_server_key -> Ok(true)`. ✅
- Size 1–3 MB (≤5 MB gate) → Task 12. ✅
- Codesigning caveat → documented in spec; no task attempts signing (correct). ✅
- Editor stays Python, build step changes → Task 13. ✅
- Editor bundles stub, frozen editor works offline → Task 15. ✅
- CI builds stub per OS → Task 14. ✅
- Keep Python launcher as oracle until proven, then delete → Tasks 1–14 leave it intact; Task 15 deletes it. ✅

**Placeholder scan:** No "TBD/TODO/implement later". The intentional `#[ignore]` in Task 7 and the RECONCILE notes are explicit engineering guidance (API drift is real for russh), not vague placeholders; each names the exact symbols to adapt and the behavior to preserve.

**Type consistency:** `LauncherConfig` fields, `FalcoError` variants + `exit_code()`, `LaunchRequest`/`Mode`/`SftpAction`/`SftpOp`, `KeyStore`/`resolve_password`, `SshSession::{run_command,run_script,open_sftp,handle,disconnect}`, `sftp::execute`, `interactive::start_interactive_shell`, and the Python `append_config`/`MAGIC`/`build_launcher` signature are used consistently across tasks. Trailer byte layout matches between Rust (Task 4) and Python (Task 13). ✅
