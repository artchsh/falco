# Reducing the launcher binary size

> **Superseded.** The launcher was rewritten in Rust (Option B below), so it is
> no longer a ~25 MB Python binary. See [launcher-rs-size.md](launcher-rs-size.md)
> for the current sizes and [`launcher-rs/`](../launcher-rs) for the crate. This
> note is kept for the historical analysis that motivated the rewrite.

The generated launcher is ~25 MB. This note records why, and the options for
shrinking it. **Nothing here is implemented yet** — it's a plan for later.

## Where the 25 MB comes from

Almost none of it is Falco's own code. A PyInstaller one-file binary bundles:

- the entire **CPython interpreter + standard library** (~7–10 MB on its own), and
- **`cryptography`** (pulled in by `paramiko`), which ships native OpenSSL — the
  single largest chunk.

So the practical floor for *any* Python + Paramiko launcher is ~20–30 MB.
**1 MB is not reachable while the launcher stays in Python.**

## Option A — stay in Python, shrink what we can (no rewrite)

| Lever | Effort | Result |
|-------|--------|--------|
| UPX compression (`--upx-dir`) | trivial (build flag) | 25 MB → ~9–12 MB |
| `--exclude-module` for unused stdlib (tkinter, unittest, …) — the launcher doesn't use them | small | −1–3 MB |
| `--strip` on Linux/macOS | trivial | minor |

Realistic Python floor: **~8–10 MB**. Good enough for most uses; not 1 MB.

Notes/caveats:
- UPX-packed binaries are occasionally flagged by Windows Defender / SmartScreen.
- Verify the launcher still starts after excludes (over-excluding breaks imports).

## Option B — rewrite only the launcher in a native language

The size problem is fundamentally "we ship a Python VM." A native launcher drops
that entirely. The **Falco Editor can stay in Python** — it would just emit a
tiny native launcher instead of running PyInstaller.

| Language | Typical size | Trade-off |
|----------|--------------|-----------|
| **C** + libssh2 | ~0.3–1 MB | Smallest. Hand-write per-OS credential APIs (Windows `wincred.h`, macOS `Security.framework`), PTY, memory management. Highest maintenance. |
| **Rust** + `russh`/`ssh2` + `keyring` crate | ~1–3 MB | Best if small size is a hard requirement. Memory-safe; mature crates map to the *same* OS keystores; `crossterm` covers PTY/terminal. |
| **Go** + `golang.org/x/crypto/ssh` | ~6–8 MB | Bigger, but one toolchain **cross-compiles all 3 OSes** — could delete the per-OS CI runners. |

Structural change either native route forces: the editor's "build" step becomes
"invoke a compiler toolchain (cargo / cc / go build)" instead of PyInstaller, so
the build machine / CI needs that toolchain. Go and Rust cross-compilation make
this the easiest.

## Recommendation

- If **~10 MB is acceptable** → add UPX + module excludes to the current build.
  Zero rewrite, ~an afternoon.
- If **1–2 MB is a hard requirement** → rewrite **only the launcher in Rust**,
  keep the Editor in Python. The current, fully-tested Python launcher serves as
  an exact behavioural spec for the port (config embedding, credential store,
  command exec, interactive PTY, SFTP, arg parsing, exit codes).

## Reference: what a native launcher must re-implement

The behaviour is well-defined and already covered by tests:

- Embedded non-secret config (host / port / user / credential id); **no password
  embedded**.
- Credential retrieval from the OS keystore; first-run no-echo prompt + save.
- Password-based SSH connect; auto-accept unknown/changed host keys.
- One-shot command exec with live stdout/stderr streaming; exit-code propagation.
- Safe argument handling (single quoted string vs. re-quoted bare args).
- `--stdin` remote script.
- Interactive PTY shell (history, tab-completion, colours, sudo prompts, resize,
  clean restore).
- SFTP: upload/download/list/mkdir/remove/move + recursive dir transfer, live
  progress, overwrite guards, temp-file-then-rename downloads, `--mkdirs`,
  non-zero exit on failure.
