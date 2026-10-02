# Reliable Editor and SSH Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline, as explicitly requested by the user. Steps use checkbox syntax for tracking. Human review gates are waived by the user.

**Goal:** Reliable, polished launcher builds and portable, host-verified SSH authentication with actionable errors for agents.

**Architecture:** Extend the existing Python/Tk editor and Rust launcher. Shared schema describes authentication and network requirements. Isolate build events, host trust and diagnostic formatting from GUI/SSH transport code.

**Tech Stack:** Python 3.12+, Tk/ttk, pytest; Rust, tokio, russh, russh-sftp, keyring, serde_json.

**Spec:** `docs/superpowers/specs/2026-10-02-reliable-editor-ssh-design.md`

## Global Constraints

- Preserve portable prebuilt-stub assembly and all three OS targets.
- Embed only encrypted OpenSSH private keys; never embed passwords/passphrases.
- Schema 2; accept legacy schema 1; reject unsupported schemas.
- JSON errors on stderr; exit groups 2, 3, 4; preserve real remote status.
- First-seen host trust, reject changed keys; override only with `--accept-new-key`.
- No extra human review gates or per-task implementation agents.
- Work on feature branch `improve/reliable-editor-ssh`; keep changes reviewable locally.

## Review Focus

- Malformed/truncated/enormous OpenSSH envelopes must fail without secret output (Task 1).
- Invalid/reserved/path output names and write interruption must preserve existing destinations (Task 2).
- Changing embedded key must not reuse another key's passphrase (Task 3).
- Trust-store failures and early remote close must never succeed (Task 3).
- Closing Tk while a worker finishes must not access destroyed widgets (Task 4).

### Task 1: Shared configuration and safe key embedding

**Files:** `shared/config.py`, new `shared/ssh_keys.py`, `tests/test_config.py`, new key tests, Rust `config.rs` and config tests.

**Interfaces:** `LauncherConfig.create(..., auth_method='password', encrypted_private_key=None, requires_vpn=False)`; serialized schema 2. `validate_encrypted_private_key(text: str) -> str` validates the OpenSSH envelope, returns normalized text. Rust config mirrors fields and validates on `from_json`.

- [x] Write tests: schema 1 defaults; schema 2 key/VPN round trip; invalid types and schema rejection; encrypted key accepted, plaintext/truncated key rejected without leaking input.
- [x] Run `.venv/bin/python -m pytest tests/test_config.py tests/test_ssh_keys.py`; expect failures for missing behavior.
- [x] Implement Python schema/key validation and equivalent Rust config validation. Add runtime capability marker `FALCO_SCHEMA_2` to stub.
- [x] Run config/key tests and Rust config tests; expect pass.

### Task 2: Reliable build assembly and guides

**Files:** `editor/builder.py`, `editor/templates.py`, `build/build_launcher.py`, `tests/test_builder.py`, new build failure tests.

**Interfaces:** `validate_output_name(output_name: str) -> str`; `build_launcher`/`build_all_launchers` existing return types, explicit target filenames passed to guide renderer; CLI flags `--private-key`, `--requires-vpn`, `--all-platforms`.

- [x] Write tests for unsafe/reserved basenames, failed staged writes preserving existing launchers, cleanup, schema-capability mismatch and actual filenames/VPN/auth guidance.
- [x] Run builder tests; expect failures for missing validation/atomicity/capability checks.
- [x] Implement validated staged writes, meaningful filesystem failures, capability check and conditional guide sections; update build CLI.
- [x] Run Python suite; expect pass.

### Task 3: SSH trust, authentication and diagnostics

**Files:** Rust `errors.rs`, `cli.rs`, `credentials.rs`, `ssh.rs`, new `host_keys.rs`, `main.rs`, `interactive.rs`, `sftp.rs`, CLI/config/SSH tests and credential fixtures.

**Interfaces:** `FalcoError` carries stable code/message/action; `diagnostic_json` emits structured stderr. `LaunchRequest.accept_new_key: bool`. `KeyStore` remains injectable. `connect` verifies trust before resolving authentication and saves prompted credentials after successful auth. Separate host-pin and key-passphrase service IDs.

- [x] Write tests for flag behavior and JSON, first use/changed key/explicit update/store failure, noninteractive setup, wrong password/passphrase, separate key identity, and real encrypted-key SSH auth.
- [x] Run Rust tests; expect failures for absent behavior.
- [x] Implement host pinning, bounded DNS/TCP/handshake/auth/session startup, encrypted key decrypt/public-key authentication, TTY-only prompt, post-auth save, structured recovery actions and VPN hints.
- [x] Add tests for no exit status and remote signals; run red, implement correct failure propagation; preserve remote status and stdout.
- [x] Run Rust suite; expect pass. Format and lint.

### Task 4: Editor usability and UI

**Files:** `editor/app.py`, new `editor/build_jobs.py`, GUI/build-worker tests.

**Interfaces:** queued progress/success/failure events; worker handles Python exceptions and never touches Tk. GUI polls queue and updates build controls on main thread.

- [x] Write worker tests for success, FalcoError, unexpected filesystem error and safe delayed completion; run red.
- [x] Implement queue-based worker, grouped ttk form, key picker/auth controls, VPN checkbox, available-platform choices, consistent name defaults, replacement confirmation, folder action and readable status/log.
- [x] Run worker/Python tests; expect pass. Launch actual Tk UI and inspect where available.

### Task 5: Release checks, documentation and verification

**Files:** `.github/workflows/release.yml`, README, plan checkboxes and execution ledger; new assembly/runtime smoke tests where needed.

- [x] Make release publication depend on successful Python/Rust verification; use locked Rust builds.
- [x] Update README to accurately document encrypted keys, host trust, JSON errors, VPN hint, first-run user setup and supported formats.
- [x] Run `.venv/bin/python -m pytest`, `cargo test --locked`, `cargo fmt --check`, `cargo clippy --locked --all-targets -- -D warnings`, and `cargo build --locked --release` in the Rust crate. Expected all pass.
- [x] Assemble and run a configured release launcher in noninteractive mode; expect structured setup/connection error, never a hang or secret output.
- [x] Review the full diff with one fresh reviewer as required by inline execution skill, fix material findings and rerun affected checks. Leave final implementation on the local feature branch without publishing.

## Final verification and implementation decisions

- Python: 76 passed with no skips, including native release-launcher assembly/error smoke tests.
- Rust: 41 passed; `cargo fmt --check`, locked Clippy with warnings denied, and locked release build passed.
- macOS editor package rebuilt and started successfully without stderr. Real Tk window startup was also verified; OS screenshot capture was unavailable.
- One fresh final review completed. Fixed interactive runtime shutdown hanging on pending stdin, nested packaging suffixes and executable permissions, malformed OpenSSH public envelopes/ciphertext, and local flags being parsed after `--`. Regression tests reproduce the defects and pass after fixes.
- Release verification now runs native assembled-launcher smoke tests on each OS. Local runtime validation was macOS only; Linux/Windows execution awaits CI.
- Additional editor checks cover invalid output folders and inability to start a worker.
- Existing SFTP transfer mechanics and interactive terminal resizing were outside this change and were not fully audited by the reviewer. No unresolved finding in the requested implementation was deferred.

Decisions recorded during inline execution:

1. Work in place on a new feature branch rather than creating a worktree because the user requested direct inline implementation without review gates. Main was not changed; the cost is a different workspace placement if isolation was preferred.
2. Raise the Rust source-build floor to 1.85 for precise network-unreachable error kinds. Older source-build toolchains require updating.
3. Drain the real GUI event queue directly in pytest because Tk update under capture can hang locally. Timer scheduling is covered by real UI startup instead of pytest.

Work remains on local branch `improve/reliable-editor-ssh`; nothing was pushed, merged or published.

## Release follow-up

The user subsequently authorized merging to main, pushing, and publishing a release. Main was fast-forwarded and pushed. The first release gate caught Windows requiring a writable handle for `fsync`; staged files now open with `r+b`, covered by a regression test that fails with the old read-only handle. macOS smoke tests also used a 10-second process deadline shorter than the launcher's 15-second connection timeout; the deadline now allows 25 seconds, including process startup. These fixes preserve the release verification gate rather than bypassing it.

The second Windows verification run confirmed build assembly succeeds. It exposed a GUI-test assumption: Windows can expand an unknown `~user` differently from Unix, so the error test now injects the documented `expanduser` failure. Hosted Python also intermittently fails Tcl initialization between test roots; only the explicit missing `init.tcl` environment error is skipped, while widget/application initialization errors still fail. Local GUI tests passed after these test portability changes.
