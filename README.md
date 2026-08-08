# Falco

**Turn one SSH server into a tiny double-click app.**

Falco lets you package access to a single server into a small standalone program
(1–2 MB). You run that program instead of typing `ssh user@host` every time — no
SSH config, no key files, and the password is never stored in the file.

## What you get

Falco has two parts:

1. **The Editor** — a small desktop app. You type in a server's host, port, and
   username, click **Build**, and it spits out a launcher program.
2. **The Launcher** — the little program the Editor creates. It talks to *one*
   preconfigured server and nothing else.

The first time you run a launcher it asks for the server password once, then
saves it in your operating system's secure store (Windows Credential Manager,
macOS Keychain, or Linux Secret Service). After that, it just works.

## What a launcher can do

Say you built one called `server-client-X`:

```bash
server-client-X "docker ps"          # run a command on the server
server-client-X deploy.sh --stdin    # run a local script on the server
server-client-X                      # open an interactive shell

# copy files over the same connection:
server-client-X --upload ./app.zip /srv/app.zip
server-client-X --download /var/log/app.log ./app.log
server-client-X --upload-dir ./dist /var/www      # whole folders, too
```

Commands run **only on the server**, output streams back live, and the program
exits with the command's real exit code — so it drops straight into scripts and
CI.

## Why it's safe to hand around

- **The password is never inside the launcher.** The file only contains the
  host, port, and username. The password lives in your OS's secure store and is
  never written to disk by Falco, put on a command line, or shown in logs.
- Share the launcher with a teammate and it's useless without the password —
  which each person enters once on their own machine.

One honest trade-off: for zero-setup convenience, a launcher **trusts whatever
server answers** at that host (it doesn't verify the server's identity). Only
point launchers at servers on networks you trust.

## Build for every OS at once

Because building a launcher just means "stamp the config into a prebuilt
program", a single Editor can produce **Windows, macOS, and Linux** launchers in
one click — tick *"Build for all platforms"*. (On macOS/Linux you may need to
`chmod +x` the file once; the generated `how-to-use.md` explains it.)

## Getting it

Download the Editor for your OS from the
[Releases](https://github.com/Media-Boost-Group/falco/releases) page, or build
it yourself:

```bash
cd launcher-rs && cargo build --release        # build the launcher core (needs Rust)
pip install -e ".[build]"                       # editor build tooling (needs Python 3.12+)
python build/build_editor.py --out-dir dist     # produces the Falco Editor
```

## Troubleshooting

**A launcher hangs or times out connecting.** That almost always means you're
not logged in / don't have access to the server yet — not a bug. Check that you
can reach it with a normal client first (`ssh user@host`), that you're on the
right network/VPN, and that your account is active on that server.

## How it works (for the curious)

- `editor/` — the desktop Editor (Python + Tkinter) and the build step.
- `launcher-rs/` — the launcher itself, written in Rust (`russh` for SSH/SFTP).
  Built once per OS into a config-less "stub".
- `shared/` — the small, non-secret config model shared by both.

"Building" a launcher doesn't compile anything: the Editor copies the prebuilt
stub and appends your server config as a trailer the launcher reads from its own
file at startup. That's why builds are instant, single-file, and can target any
OS from any OS.

## License

MIT.
