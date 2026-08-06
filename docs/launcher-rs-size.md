# Rust launcher binary size

Measured size of `falco-stub` (release profile: `opt-level="z"`, `lto=true`,
`codegen-units=1`, `panic="abort"`, `strip=true`; russh is pure Rust so no
OpenSSL is linked).

| OS            | Size | Source |
|---------------|------|--------|
| macOS         | **1.02 MB** (1,074,848 bytes) | CI, `rust` job |
| Windows (x86_64)| **1.14 MB** (1,200,128 bytes) | CI, `rust` job |
| Linux (x86_64)  | **1.78 MB** (1,864,288 bytes) | CI, `rust` job |

All three are comfortably under the 5 MB gate and at/near the 1–3 MB target — a
~14–24× reduction from the ~25 MB PyInstaller launcher. Linux is the largest
because it bundles the Secret Service client stack (dbus-secret-service +
crypto-rust); macOS/Windows use their native OS keystore APIs.

**Budget:** ≤ 5 MB (target 1–3 MB) — comfortably met on Windows. If a future
build exceeds 5 MB, tighten the levers: confirm `strip` applied, try
`opt-level="s"` vs `"z"`, and audit default crate features.

This replaces the ~25 MB PyInstaller launcher documented in
[launcher-size.md](launcher-size.md).

## Platform notes

- **Linux** links system `libdbus-1` to reach the Secret Service (GNOME Keyring
  / KWallet), because that keystore is a D-Bus service. Building needs
  `libdbus-1-dev` + `pkg-config`; running needs a Secret Service provider (present
  on any desktop that has a login keyring). There is no OpenSSL dependency
  (`crypto-rust`). Windows (Credential Manager) and macOS (Keychain) use their
  native OS APIs with no extra system libraries.
