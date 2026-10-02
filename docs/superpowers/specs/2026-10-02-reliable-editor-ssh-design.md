# Reliable editor and agent-friendly SSH

User approved the design and requested inline implementation without further human review.

## Intent

Keep Falco's portable, toolchain-free launcher workflow. Improve the Tk editor's usability and failure recovery, verify SSH host identity, support embedded encrypted private keys, and give AI agents clear recovery instructions without exposing credentials.

## Editor and builds

Keep Python 3.12+, Tk/ttk, and native Rust launchers. Group fields into Connection, Authentication, and Output sections, with a restrained light theme, clear hierarchy, password/key selection, key-file browse, VPN checkbox, explicit available platforms, useful build progress and output-folder action. Remove the ineffective executable-icon input. Name defaults follow the selected launcher name and platform. Build workers send events through a queue; only the Tk main thread touches widgets. Capture errors as values; restore controls on failure. Closing during a build must not call destroyed widgets.

Validate output basenames on every platform (including traversal, Windows reserved names, separators and packaging suffixes), required configuration types, schema versions, and encrypted key format. Stage complete launcher and guide files before replacing destinations; each replacement is atomic, failed writes leave existing files intact, temporary files are cleaned up. Report partial multi-output replacement failures truthfully; atomicity across unrelated destination files is not promised. Existing output replacement requires editor confirmation. CLI retains explicit deterministic behavior. Release publication must wait for tests.

## Configuration and authentication

Schema 2 adds `auth_method` (`password` or `private_key`), `encrypted_private_key` (optional encrypted OpenSSH key text), and `requires_vpn` (boolean). Read legacy schema 1 as password/no VPN. Reject unsupported schema versions and inconsistent authentication fields. Builder must reject old stubs for schema 2 using a capability marker compiled into new Rust stubs. Existing generated binaries cannot be retroactively upgraded.

Only encrypted OpenSSH private keys are embedded. The editor can inspect the OpenSSH envelope without the passphrase; Rust decrypts and authenticates at runtime. This is private-key authentication, not signed SSH user certificates. Never put a password, passphrase, or decrypted private key into configuration, logs, argv, environment variables, or temporary files. Encrypted key bytes are sensitive but intentionally portable. Avoid Debug output of key-bearing configuration and authentication values.

Connect and verify host identity before retrieving/sending credentials. First use requires a human terminal and hidden prompt; unattended runs with missing credentials return `CREDENTIAL_SETUP_REQUIRED` immediately. Save a newly entered password/passphrase only after successful authentication. Password and key passphrase use separate keystore entries; key-passphrase entries are tied to the embedded key fingerprint. `--reset-credential` clears the active authentication secret; preserve `--reset-password` compatibility. Keystore failures fail closed with actionable diagnostics, including failed deletes.

## Host identity

Pin the first observed host-key fingerprint per normalized host/port in the OS credential store, separate from passwords and launcher names. First connection is trust on first use. Reject any subsequent changed key before authentication. Error code `HOST_KEY_CHANGED` includes expected/observed fingerprints and: “Server key changed. If you accept this change, retry with --accept-new-key.” `--accept-new-key` explicitly authorizes updating the observed pin for this invocation, not disabling verification. Documentation tells agents to obtain user authorization before using that flag. Resetting credentials never clears host trust. Failure to read/write trust must not silently bypass verification.

## Errors and command behavior

Print launcher-owned errors as a single JSON object on stderr with `error`, `message`, `action`, and optional `target`/`details`; do not alter remote stdout/stderr. Preserve exit groups 2 (config/arguments), 3 (credentials), 4 (connection/remote). Successful commands return their actual remote exit status. Premature disconnect, remote signal, or missing exit status must never return fabricated success. No automatic retries of remote operations.

Distinguish invalid configuration, DNS lookup failure, connection refusal, network unreachable, connection timeout, SSH handshake/authentication timeout, authentication rejection, wrong key passphrase/invalid key, credential-store unavailable, and host-key mismatch. Explanations name known facts rather than assert that a timeout proves a bad password or wrong IP. Actions state what the agent can inspect and when a human must intervene. Remote/SFTP/terminal/local-file failures also receive understandable codes and actions. Do not impose a short execution timeout on legitimate long commands; bound connect/authentication/session startup instead.

VPN checkbox adds an explicit Tailscale/WireGuard/VPN hint in connection failures and `how-to-use.md`: if timeout or address errors occur, the user may have forgotten to enable the required VPN. Generated guides use actual output filenames and document authentication setup, JSON diagnostics, trust changes, and exit codes.

## Validation

Python tests cover strict config parsing, encrypted-key envelope validation, filename rejection, interrupted/failed writes, old-stub rejection, correct generated invocation filenames, worker failure recovery and no Tk calls from background workers. Rust tests cover config compatibility/validation, flag parsing, error JSON, noninteractive credential setup, secret separation and successful-save ordering, host trust (unchanged/changed/accepted/store failure), real password and encrypted-key SSH authentication, startup timeouts and missing remote status. Run both suites, Rust formatting/lint, a release stub build and launcher-assembly smoke test. Preview the real Tk UI where the local display permits. Test backends may replace the external OS store; production never accepts secrets via environment variables.
