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

    enable_raw_mode().map_err(|e| FalcoError::Remote(format!("Failed to set raw mode: {e}")))?;

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
            // TODO(resize parity): add a third arm reading
            // crossterm::event::EventStream for Event::Resize(cols, rows) and call
            // channel.window_change(cols as u32, rows as u32, 0, 0). Add only after
            // the basic bridge builds and works.
        }
    }
    Ok(code)
}
