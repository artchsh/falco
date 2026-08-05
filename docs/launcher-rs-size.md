# Rust launcher binary size

Measured size of `falco-stub` (release profile: `opt-level="z"`, `lto=true`,
`codegen-units=1`, `panic="abort"`, `strip=true`; russh is pure Rust so no
OpenSSL is linked).

| OS            | Size |
|---------------|------|
| Linux (x86_64)  | _fill from CI/local build_ |
| macOS (arm64)   | _fill from CI/local build_ |
| Windows (x86_64)| _fill from CI/local build_ |

> **Not yet measured.** The Rust toolchain was not available when the launcher
> was written. To fill this table: `cd launcher-rs && cargo build --release`
> then check the size of `target/release/falco-stub[.exe]`.

**Budget:** ≤ 5 MB (target 1–3 MB). If a build exceeds 5 MB, tighten the levers
before shipping: confirm `strip` applied, try `opt-level="s"` vs `"z"`, and
audit default crate features (disable unused `tokio` / `crossterm` features).

This replaces the ~25 MB PyInstaller launcher documented in
[launcher-size.md](launcher-size.md).
