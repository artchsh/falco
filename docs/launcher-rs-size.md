# Rust launcher binary size

Measured size of `falco-stub` (release profile: `opt-level="z"`, `lto=true`,
`codegen-units=1`, `panic="abort"`, `strip=true`; russh is pure Rust so no
OpenSSL is linked).

| OS            | Size | Source |
|---------------|------|--------|
| Windows (x86_64)| **1.14 MB** (1,191,424 bytes) | measured, rustc 1.97.1 |
| Linux (x86_64)  | _pending CI_ | tests.yml `rust` job |
| macOS (arm64)   | _pending CI_ | tests.yml `rust` job |

Windows comes in at **1.14 MB** — a ~22× reduction from the ~25 MB PyInstaller
launcher, and at the low end of the 1–3 MB target. Linux/macOS are expected in
the same ballpark (russh is pure Rust; no per-OS native libs). Fill those rows
from the `rust` job's "Report stub size" step.

**Budget:** ≤ 5 MB (target 1–3 MB) — comfortably met on Windows. If a future
build exceeds 5 MB, tighten the levers: confirm `strip` applied, try
`opt-level="s"` vs `"z"`, and audit default crate features.

This replaces the ~25 MB PyInstaller launcher documented in
[launcher-size.md](launcher-size.md).
