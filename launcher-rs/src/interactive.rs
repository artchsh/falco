//! Interactive SSH shell with a pseudo-terminal.
//!
//! Requests a remote PTY sized to the local terminal and bridges local stdin
//! to the channel and channel output to local stdout, in raw mode, so history,
//! tab-completion, colours and `sudo` prompts work. The terminal is always
//! restored, even after an error or disconnect.
//!
//! The PTY is sized once at open (matches the Windows path of the Python
//! launcher). To fully match the POSIX SIGWINCH path, add a resize arm to the
//! select loop (see the note in `bridge`). Verified against russh 0.45 /
//! crossterm 0.28.

use crossterm::terminal::{disable_raw_mode, enable_raw_mode, size as term_size};
use russh::client;
use russh::ChannelMsg;
use std::io::IsTerminal;
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

use crate::errors::{FResult, FalcoError};
use crate::ssh::SshSession;

fn term_type() -> String {
    std::env::var("TERM").unwrap_or_else(|_| "xterm-256color".into())
}

pub async fn start_interactive_shell(session: &SshSession) -> FResult<i32> {
    if !std::io::stdin().is_terminal() || !std::io::stdout().is_terminal() {
        return Err(FalcoError::new("TERMINAL_REQUIRED", "An interactive SSH shell requires a local terminal.", "AI agents should pass a remote command, --stdin script, or an SFTP action. Use a user terminal for an interactive shell.", 2));
    }
    let (cols, rows) = term_size().unwrap_or((80, 24));
    let mut channel = session.open_channel().await?;
    let requests = async {
        channel
            .request_pty(false, &term_type(), cols as u32, rows as u32, 0, 0, &[])
            .await
            .map_err(|e| FalcoError::Remote(format!("Could not request PTY: {e}")))?;
        channel
            .request_shell(true)
            .await
            .map_err(|e| FalcoError::Remote(format!("Could not request shell: {e}")))
    };
    tokio::time::timeout(session.timeout(), requests)
        .await
        .map_err(|_| {
            FalcoError::new(
                "SHELL_START_TIMEOUT",
                "The server did not respond to the shell request.",
                "Check the configured account's shell and SSH server restrictions.",
                4,
            )
        })??;

    enable_raw_mode().map_err(|e| FalcoError::Remote(format!("Failed to set raw mode: {e}")))?;

    let result = bridge(&mut channel, session.timeout()).await;

    // ALWAYS restore the terminal, even after error/disconnect.
    let _ = disable_raw_mode();

    let _ = channel.close().await;
    result
}

async fn bridge(channel: &mut russh::Channel<client::Msg>, timeout: Duration) -> FResult<i32> {
    let mut stdin = tokio::io::stdin();
    let mut stdout = tokio::io::stdout();
    let mut buf = [0u8; 32768];
    let mut code = None;
    let mut stdin_open = true;
    let mut started = false;
    let startup = tokio::time::sleep(timeout);
    tokio::pin!(startup);

    loop {
        tokio::select! {
            // Local keystrokes -> remote.
            n = stdin.read(&mut buf), if stdin_open => {
                match n {
                    Ok(0) => { stdin_open = false; channel.eof().await.map_err(|e| FalcoError::Remote(format!("Failed to send terminal EOF: {e}")))?; }
                    Ok(n) => {
                        channel.data(&buf[..n]).await.map_err(|e| FalcoError::Remote(format!("Failed to send terminal input: {e}")))?;
                    }
                    Err(e) => return Err(FalcoError::Remote(format!("Failed to read local terminal: {e}"))),
                }
            }
            // Remote output -> local terminal.
            msg = channel.wait() => {
                match msg {
                    Some(ChannelMsg::Data { data }) => {
                        started = true;
                        stdout.write_all(&data).await.map_err(|e| FalcoError::Remote(format!("Failed to write terminal output: {e}")))?;
                        stdout.flush().await.map_err(|e| FalcoError::Remote(format!("Failed to flush terminal output: {e}")))?;
                    }
                    Some(ChannelMsg::ExtendedData { data, .. }) => {
                        started = true;
                        stdout.write_all(&data).await.map_err(|e| FalcoError::Remote(format!("Failed to write terminal output: {e}")))?;
                        stdout.flush().await.map_err(|e| FalcoError::Remote(format!("Failed to flush terminal output: {e}")))?;
                    }
                    Some(ChannelMsg::ExitStatus { exit_status }) => {
                        started = true;
                        code = Some(exit_status as i32);
                    }
                    Some(ChannelMsg::Success) => started = true,
                    Some(ChannelMsg::Failure) => return Err(FalcoError::new("SHELL_REQUEST_REJECTED", "The server rejected the interactive shell.", "Check the account's shell and SSH restrictions.", 4)),
                    Some(ChannelMsg::ExitSignal { signal_name, .. }) => return Err(FalcoError::new("REMOTE_COMMAND_SIGNALLED", format!("Remote shell terminated by {signal_name:?}."), "Check the remote shell/process logs before reconnecting.", 4)),
                    Some(ChannelMsg::Close) | None => break,
                    _ => {}
                }
            }
            _ = &mut startup, if !started => return Err(FalcoError::new("SHELL_START_TIMEOUT", "The server did not acknowledge the interactive shell request.", "Check the account's shell and SSH server restrictions.", 4)),
            // TODO(resize parity): add a third arm reading
            // crossterm::event::EventStream for Event::Resize(cols, rows) and call
            // channel.window_change(cols as u32, rows as u32, 0, 0). Add only after
            // the basic bridge builds and works.
        }
    }
    code.ok_or_else(|| {
        FalcoError::new(
            "REMOTE_EXIT_STATUS_MISSING",
            "The shell connection closed without an exit status.",
            "Check server availability and shell state before reconnecting.",
            4,
        )
    })
}
