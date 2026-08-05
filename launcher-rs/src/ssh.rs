//! Password-based SSH connection and one-shot command/script execution (russh).
//!
//! SECURITY: the client handler auto-accepts ALL host keys (unknown or
//! changed). This is the documented Falco trade-off (matches Paramiko
//! `AutoAddPolicy`) and defeats SSH's protection against server impersonation.
//! Only run launchers against hosts on networks you trust.
//!
//! RECONCILE(russh): the russh client API drifts between versions. The most
//! likely drift points are marked inline. Preserve behavior (auto-accept host
//! keys, live stdout/stderr streaming, remote exit-code propagation) regardless
//! of exact method/type names.

use std::sync::Arc;
use std::time::Duration;

use russh::client::{self, Handle};
use russh::{ChannelMsg, Disconnect};
use tokio::io::{AsyncWriteExt, BufWriter};

use crate::config::LauncherConfig;
use crate::errors::{FalcoError, FResult};

/// Client handler that auto-accepts every host key.
pub struct Client;

impl client::Handler for Client {
    type Error = russh::Error;

    // RECONCILE(russh): signature/key type may be
    // `check_server_key(&mut self, &russh::keys::PublicKey)` or
    // `&russh_keys::key::PublicKey` depending on version.
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
        self.exec(command.as_bytes()).await
    }

    /// Feed `script` to a remote `/bin/sh -s` via stdin and return the exit code.
    pub async fn run_script(&self, script: &[u8]) -> FResult<i32> {
        self.exec_with_stdin(b"/bin/sh -s", script).await
    }

    async fn exec(&self, command: &[u8]) -> FResult<i32> {
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

    /// Open an SFTP session over a new channel + the `sftp` subsystem.
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
        // RECONCILE(russh-sftp): the adapter from a russh channel to an
        // SftpSession depends on the resolved crate version — `channel.into_stream()`
        // is the common form. Use whatever that version documents.
        russh_sftp::client::SftpSession::new(channel.into_stream())
            .await
            .map_err(|e| FalcoError::Ssh(format!("Could not open SFTP session: {e}")))
    }

    pub async fn disconnect(&self) {
        let _ = self
            .handle
            .disconnect(Disconnect::ByApplication, "", "en")
            .await;
    }
}

/// Stream a channel's stdout/stderr live and return its remote exit code.
async fn pump_channel(channel: &mut russh::Channel<client::Msg>) -> FResult<i32> {
    let mut stdout = BufWriter::new(tokio::io::stdout());
    let mut stderr = BufWriter::new(tokio::io::stderr());
    let mut code: i32 = 0;
    loop {
        let Some(msg) = channel.wait().await else {
            break;
        };
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

/// Return an authenticated session (password auth, ~15s connect timeout).
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

    // RECONCILE(russh): `authenticate_password` returns an auth result exposing
    // `.success()` in recent versions; older ones return `bool`. Adapt the check.
    let authed = handle
        .authenticate_password(&cfg.username, password)
        .await
        .map_err(|e| FalcoError::Ssh(format!("Authentication error: {e}")))?;

    if !authed.success() {
        return Err(FalcoError::Ssh(format!(
            "Authentication failed for {}@{}. The stored password may be wrong; \
             re-run with --reset-password.",
            cfg.username, cfg.host
        )));
    }
    Ok(SshSession { handle })
}
