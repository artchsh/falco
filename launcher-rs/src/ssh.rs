//! Host-verified SSH connections with private keystore authentication.
use crate::config::LauncherConfig;
use crate::credentials::{self, KeyStore};
use crate::errors::{FResult, FalcoError};
use crate::host_keys;
use async_trait::async_trait;
use russh::client::{self, Handle};
use russh::{ChannelMsg, Disconnect};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tokio::io::{AsyncWriteExt, BufWriter};

pub struct Client {
    cfg: LauncherConfig,
    store: Arc<dyn KeyStore>,
    accept_new_key: bool,
    trust_error: Arc<Mutex<Option<FalcoError>>>,
}
#[async_trait]
impl client::Handler for Client {
    type Error = FalcoError;
    async fn check_server_key(
        &mut self,
        key: &russh::keys::key::PublicKey,
    ) -> Result<bool, Self::Error> {
        let fingerprint = format!("SHA256:{}", key.fingerprint());
        match host_keys::verify_host_key(
            &self.cfg,
            &fingerprint,
            self.accept_new_key,
            self.store.as_ref(),
        ) {
            Ok(()) => Ok(true),
            Err(error) => {
                *self.trust_error.lock().unwrap() = Some(error);
                Ok(false)
            }
        }
    }
}
#[derive(Clone, Copy)]
pub struct ConnectOptions {
    pub accept_new_key: bool,
    pub interactive: bool,
    pub timeout: Duration,
}
impl Default for ConnectOptions {
    fn default() -> Self {
        Self {
            accept_new_key: false,
            interactive: false,
            timeout: Duration::from_secs(15),
        }
    }
}

pub struct SshSession {
    handle: Handle<Client>,
    timeout: Duration,
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

    pub fn timeout(&self) -> Duration {
        self.timeout
    }

    pub async fn open_channel(&self) -> FResult<russh::Channel<client::Msg>> {
        tokio::time::timeout(self.timeout, self.handle.channel_open_session())
            .await
            .map_err(|_| {
                FalcoError::new(
                    "SESSION_OPEN_TIMEOUT",
                    "SSH authentication succeeded, but opening a session timed out.",
                    "Check the SSH server's session limits and logs. No command was started.",
                    4,
                )
            })?
            .map_err(|e| FalcoError::Remote(format!("Failed to open SSH session: {e}")))
    }

    async fn exec(&self, command: &[u8]) -> FResult<i32> {
        let mut channel = self.open_channel().await?;
        channel
            .exec(true, command)
            .await
            .map_err(|e| FalcoError::Remote(format!("Failed to request remote command: {e}")))?;
        pump_channel(&mut channel, self.timeout).await
    }

    async fn exec_with_stdin(&self, command: &[u8], stdin: &[u8]) -> FResult<i32> {
        let mut channel = self.open_channel().await?;
        channel
            .exec(true, command)
            .await
            .map_err(|e| FalcoError::Remote(format!("Failed to request remote shell: {e}")))?;
        let mut writer = channel.make_writer();
        let send = async {
            writer
                .write_all(stdin)
                .await
                .map_err(|e| FalcoError::Remote(format!("Failed to send script: {e}")))?;
            writer
                .shutdown()
                .await
                .map_err(|e| FalcoError::Remote(format!("Failed to signal script EOF: {e}")))?;
            Ok::<(), FalcoError>(())
        };
        let (_, code) = tokio::try_join!(send, pump_channel(&mut channel, self.timeout))?;
        Ok(code)
    }

    /// Open an SFTP session over a new channel + the `sftp` subsystem.
    pub async fn open_sftp(&self) -> FResult<russh_sftp::client::SftpSession> {
        let channel = self.open_channel().await?;
        let start = async {
            channel
                .request_subsystem(true, "sftp")
                .await
                .map_err(|e| FalcoError::Remote(format!("Could not request SFTP: {e}")))?;
            russh_sftp::client::SftpSession::new(channel.into_stream())
                .await
                .map_err(|e| FalcoError::Remote(format!("Could not start SFTP: {e}")))
        };
        tokio::time::timeout(self.timeout, start)
            .await
            .map_err(|_| {
                FalcoError::new(
                    "SFTP_START_TIMEOUT",
                    "The SSH server did not start SFTP within the timeout.",
                    "Check that the server enables the SFTP subsystem and has available sessions.",
                    4,
                )
            })?
    }

    pub async fn disconnect(&self) {
        let _ = self
            .handle
            .disconnect(Disconnect::ByApplication, "", "en")
            .await;
    }
}

/// Stream a channel's stdout/stderr live and return its remote exit code.
async fn pump_channel(
    channel: &mut russh::Channel<client::Msg>,
    timeout: Duration,
) -> FResult<i32> {
    let mut stdout = BufWriter::new(tokio::io::stdout());
    let mut stderr = BufWriter::new(tokio::io::stderr());
    let mut code = None;
    let mut started = false;
    loop {
        let msg = if started {
            channel.wait().await
        } else {
            tokio::time::timeout(timeout, channel.wait()).await
                .map_err(|_| FalcoError::new("COMMAND_START_TIMEOUT", "The SSH server did not acknowledge or produce output for the command within the startup timeout.", "Inspect the remote server and command state before retrying; the command may already have started.", 4))?
        };
        let Some(msg) = msg else {
            break;
        };
        match msg {
            ChannelMsg::Data { data } => {
                started = true;
                stdout.write_all(&data).await.map_err(output_error)?;
                stdout.flush().await.map_err(output_error)?;
            }
            ChannelMsg::ExtendedData { data, .. } => {
                started = true;
                stderr.write_all(&data).await.map_err(output_error)?;
                stderr.flush().await.map_err(output_error)?;
            }
            ChannelMsg::Success => started = true,
            ChannelMsg::Failure => {
                return Err(FalcoError::new(
                    "REMOTE_COMMAND_REJECTED",
                    "The server rejected the command request.",
                    "Check the account's allowed commands, shell and server restrictions.",
                    4,
                ))
            }
            ChannelMsg::ExitStatus { exit_status } => {
                started = true;
                code = Some(exit_status as i32);
            }
            ChannelMsg::ExitSignal { signal_name, .. } => {
                return Err(FalcoError::new(
                    "REMOTE_COMMAND_SIGNALLED",
                    format!("The remote command terminated by signal {signal_name:?}."),
                    "Inspect remote process logs and partial effects before retrying.",
                    4,
                ))
            }
            ChannelMsg::Close => break,
            _ => {}
        }
    }
    stdout.flush().await.map_err(output_error)?;
    stderr.flush().await.map_err(output_error)?;
    code.ok_or_else(|| FalcoError::new("REMOTE_EXIT_STATUS_MISSING", "The SSH channel closed without a remote command exit status; success is unknown.", "Inspect the server and command's partial effects before retrying. Do not assume the command succeeded or retry a state-changing operation blindly.", 4))
}
fn output_error(error: std::io::Error) -> FalcoError {
    FalcoError::new("LOCAL_OUTPUT_FAILED", format!("Could not write streamed output: {error}"), "Check the local output destination or pipe. The remote command may have partially completed.", 4)
}

fn network_action(cfg: &LauncherConfig) -> String {
    let vpn = if cfg.requires_vpn {
        " This server requires Tailscale/WireGuard/VPN; ask the user to enable it before retrying."
    } else {
        " Check any required VPN or private-network access."
    };
    format!("Check the configured host/IP, SSH port, server availability and firewall.{vpn} Do not infer a wrong password from this network failure.")
}
fn connection_error(
    cfg: &LauncherConfig,
    code: &'static str,
    message: impl Into<String>,
) -> FalcoError {
    FalcoError::new(code, message, network_action(cfg), 4)
        .target(format!("{}@{}:{}", cfg.username, cfg.host, cfg.port))
}
fn io_error(cfg: &LauncherConfig, error: std::io::Error) -> FalcoError {
    use std::io::ErrorKind;
    let code = match error.kind() {
        ErrorKind::ConnectionRefused => "CONNECTION_REFUSED",
        ErrorKind::TimedOut => "CONNECTION_TIMEOUT",
        ErrorKind::NetworkUnreachable
        | ErrorKind::HostUnreachable
        | ErrorKind::AddrNotAvailable => "NETWORK_UNREACHABLE",
        _ => "CONNECTION_FAILED",
    };
    connection_error(
        cfg,
        code,
        format!("Could not establish a TCP connection: {error}"),
    )
}

pub async fn connect_with_store(
    cfg: &LauncherConfig,
    options: ConnectOptions,
    store: Arc<dyn KeyStore>,
    prompt: &dyn Fn(&str) -> FResult<String>,
) -> FResult<SshSession> {
    cfg.validate()?;
    let addresses = tokio::time::timeout(
        options.timeout,
        tokio::net::lookup_host((cfg.host.as_str(), cfg.port)),
    )
    .await
    .map_err(|_| connection_error(cfg, "DNS_LOOKUP_TIMEOUT", "Hostname resolution timed out."))?
    .map_err(|e| {
        connection_error(
            cfg,
            "DNS_LOOKUP_FAILED",
            format!("Could not resolve the configured host: {e}"),
        )
    })?
    .collect::<Vec<_>>();
    if addresses.is_empty() {
        return Err(connection_error(
            cfg,
            "DNS_LOOKUP_FAILED",
            "The configured hostname resolved to no addresses.",
        ));
    }
    let stream = tokio::time::timeout(
        options.timeout,
        tokio::net::TcpStream::connect(addresses.as_slice()),
    )
    .await
    .map_err(|_| {
        connection_error(
            cfg,
            "CONNECTION_TIMEOUT",
            "Timed out connecting to the configured SSH server.",
        )
    })?
    .map_err(|e| io_error(cfg, e))?;
    let trust_error = Arc::new(Mutex::new(None));
    let handler = Client {
        cfg: cfg.clone(),
        store: store.clone(),
        accept_new_key: options.accept_new_key,
        trust_error: trust_error.clone(),
    };
    let connected = tokio::time::timeout(
        options.timeout,
        client::connect_stream(Arc::new(client::Config::default()), stream, handler),
    )
    .await;
    // The library may surface UnknownKey rather than the handler's useful error.
    if let Some(error) = trust_error.lock().unwrap().take() {
        return Err(error);
    }
    let mut handle = connected
        .map_err(|_| {
            connection_error(
                cfg,
                "SSH_HANDSHAKE_TIMEOUT",
                "The TCP connection opened, but the SSH handshake timed out.",
            )
        })?
        .map_err(|e| {
            connection_error(
                cfg,
                "SSH_HANDSHAKE_FAILED",
                format!("The server did not complete a valid SSH handshake: {e}"),
            )
        })?;
    let authenticated = async {
        let secret = credentials::prepare_secret(cfg, store.as_ref(), options.interactive, prompt)?;
        let auth_result = if cfg.auth_method == "private_key" {
            let key = russh::keys::decode_secret_key(cfg.encrypted_private_key.as_deref().unwrap_or(""), Some(&secret.value))
                .map_err(|_| FalcoError::new("KEY_DECRYPTION_FAILED", "The embedded private key could not be decrypted with the saved or entered passphrase.", "Ask the user to run --reset-credential in a terminal and enter the correct passphrase privately. If this persists, rebuild with a valid supported encrypted OpenSSH key.", 3))?;
            tokio::time::timeout(options.timeout, handle.authenticate_publickey(&cfg.username, Arc::new(key))).await
        } else {
            tokio::time::timeout(options.timeout, handle.authenticate_password(&cfg.username, secret.value.as_str())).await
        };
        let authed = auth_result.map_err(|_| connection_error(cfg, "AUTHENTICATION_TIMEOUT", "The SSH server did not finish authentication within the timeout."))?
            .map_err(|e| connection_error(cfg, "AUTHENTICATION_ERROR", format!("The SSH authentication exchange failed: {e}")))?;
        if !authed {
            return Err(FalcoError::new("AUTHENTICATION_FAILED", "The server rejected authentication for the configured account.", "Ask the user to check the SSH username, account access and password/key authorization. To refresh a saved password or passphrase, have the user run --reset-credential in a terminal; never supply credentials in agent chat.", 4)
                .target(format!("{}@{}:{}", cfg.username, cfg.host, cfg.port)));
        }
        credentials::commit_secret(cfg, store.as_ref(), &secret)?;
        Ok(())
    }.await;
    if let Err(error) = authenticated {
        let _ = handle.disconnect(Disconnect::ByApplication, "", "en").await;
        return Err(error);
    }
    Ok(SshSession {
        handle,
        timeout: options.timeout,
    })
}
