//! Integration test: stand up an in-process russh server and exercise the
//! launcher's real SSH client against it — connect (right/wrong password) and
//! command execution with remote exit-code propagation.
//!
//! No OS keystore is involved (that path is unit-tested with a fake store);
//! this drives `ssh::connect` + `SshSession::run_command` over a real socket.

use std::sync::Arc;

use async_trait::async_trait;
use russh::keys::key::KeyPair;
use russh::server::{self, Auth, Msg, Server as _, Session};
use russh::{Channel, ChannelId, CryptoVec};
use tokio::net::TcpListener;

use falco_stub::config::LauncherConfig;
use falco_stub::credentials::KeyStore;
use falco_stub::errors::FResult;
use falco_stub::ssh;
use std::collections::HashMap;
use std::sync::Mutex;
#[derive(Default)]
struct Store(Mutex<HashMap<(String, String), String>>);
impl KeyStore for Store {
    fn get(&self, s: &str, u: &str) -> FResult<Option<String>> {
        Ok(self.0.lock().unwrap().get(&(s.into(), u.into())).cloned())
    }
    fn set(&self, s: &str, u: &str, v: &str) -> FResult<()> {
        self.0
            .lock()
            .unwrap()
            .insert((s.into(), u.into()), v.into());
        Ok(())
    }
    fn delete(&self, s: &str, u: &str) -> FResult<()> {
        self.0.lock().unwrap().remove(&(s.into(), u.into()));
        Ok(())
    }
}
async fn connect(cfg: &LauncherConfig, password: &str) -> FResult<ssh::SshSession> {
    ssh::connect_with_store(
        cfg,
        ssh::ConnectOptions {
            interactive: true,
            ..Default::default()
        },
        Arc::new(Store::default()),
        &|_| Ok(password.into()),
    )
    .await
}

const PASSWORD: &str = "s3cret-pass";

fn cfg(port: u16) -> LauncherConfig {
    LauncherConfig {
        launcher_name: "it".into(),
        host: "127.0.0.1".into(),
        username: "tester".into(),
        port,
        credential_id: "it".into(),
        schema_version: 1,
        auth_method: "password".into(),
        encrypted_private_key: None,
        requires_vpn: false,
    }
}

/// Minimal SSH server: accepts a fixed password and, on exec, echoes the
/// command back on stdout with exit code 0 — except `exit 7`, which exits 7.
#[derive(Clone)]
struct TestServer;

impl server::Server for TestServer {
    type Handler = TestHandler;
    fn new_client(&mut self, _peer: Option<std::net::SocketAddr>) -> TestHandler {
        TestHandler
    }
}

struct TestHandler;

#[async_trait]
impl server::Handler for TestHandler {
    type Error = russh::Error;

    async fn auth_password(&mut self, _user: &str, password: &str) -> Result<Auth, Self::Error> {
        if password == PASSWORD {
            Ok(Auth::Accept)
        } else {
            Ok(Auth::Reject {
                proceed_with_methods: None,
            })
        }
    }

    async fn auth_publickey(
        &mut self,
        user: &str,
        key: &russh::keys::key::PublicKey,
    ) -> Result<Auth, Self::Error> {
        let expected = russh::keys::decode_secret_key(
            include_str!("../../tests/fixtures/encrypted_ed25519"),
            Some("falco-test-passphrase"),
        )
        .unwrap()
        .clone_public_key()
        .unwrap();
        if user == "tester" && key == &expected {
            Ok(Auth::Accept)
        } else {
            Ok(Auth::Reject {
                proceed_with_methods: None,
            })
        }
    }

    async fn channel_open_session(
        &mut self,
        _channel: Channel<Msg>,
        _session: &mut Session,
    ) -> Result<bool, Self::Error> {
        Ok(true)
    }

    async fn exec_request(
        &mut self,
        channel: ChannelId,
        data: &[u8],
        session: &mut Session,
    ) -> Result<(), Self::Error> {
        let cmd = String::from_utf8_lossy(data);
        let (out, code) = if cmd.trim() == "exit 7" {
            (String::new(), 7u32)
        } else {
            (format!("ran: {cmd}\n"), 0u32)
        };
        if cmd == "no-status" {
            session.close(channel);
            return Ok(());
        }
        if cmd == "signal" {
            session.exit_signal_request(channel, russh::Sig::TERM, false, "terminated", "en");
            session.close(channel);
            return Ok(());
        }
        if !out.is_empty() {
            session.data(channel, CryptoVec::from_slice(out.as_bytes()));
        }
        session.exit_status_request(channel, code);
        session.eof(channel);
        session.close(channel);
        Ok(())
    }
}

/// Bind an ephemeral port, spawn the server accept loop, return the port.
async fn start_server() -> u16 {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    let config = Arc::new(server::Config {
        keys: vec![KeyPair::generate_ed25519().unwrap()],
        ..Default::default()
    });
    tokio::spawn(async move {
        let mut server = TestServer;
        let _ = server.run_on_socket(config, &listener).await;
    });
    // Let the accept loop reach its first await before the client connects.
    tokio::time::sleep(std::time::Duration::from_millis(50)).await;
    port
}

#[tokio::test]
async fn connect_and_exec_returns_zero() {
    let port = start_server().await;
    let session = connect(&cfg(port), PASSWORD).await.expect("connect");
    let code = session.run_command("echo hi").await.expect("exec");
    assert_eq!(code, 0);
    session.disconnect().await;
}

#[tokio::test]
async fn exec_propagates_remote_exit_code() {
    let port = start_server().await;
    let session = connect(&cfg(port), PASSWORD).await.expect("connect");
    let code = session.run_command("exit 7").await.expect("exec");
    assert_eq!(code, 7);
    session.disconnect().await;
}

#[tokio::test]
async fn wrong_password_is_rejected() {
    let port = start_server().await;
    let result = connect(&cfg(port), "wrong-pass").await;
    assert!(result.is_err(), "expected authentication to fail");
}

#[tokio::test]
async fn missing_remote_exit_status_is_failure() {
    let port = start_server().await;
    let session = connect(&cfg(port), PASSWORD).await.unwrap();
    let result = session.run_command("no-status").await;
    assert!(
        result.is_err(),
        "missing exit status must not report success"
    );
    assert!(result
        .unwrap_err()
        .diagnostic_json()
        .contains("REMOTE_EXIT_STATUS_MISSING"));
}
#[tokio::test]
async fn remote_signal_is_failure() {
    let port = start_server().await;
    let session = connect(&cfg(port), PASSWORD).await.unwrap();
    assert!(session.run_command("signal").await.is_err());
}
#[tokio::test]
async fn encrypted_key_authenticates_and_saves_passphrase_after_success() {
    let port = start_server().await;
    let mut config = cfg(port);
    config.schema_version = 2;
    config.auth_method = "private_key".into();
    config.encrypted_private_key =
        Some(include_str!("../../tests/fixtures/encrypted_ed25519").into());
    let store = Arc::new(Store::default());
    let session = ssh::connect_with_store(
        &config,
        ssh::ConnectOptions {
            interactive: true,
            ..Default::default()
        },
        store.clone(),
        &|_| Ok("falco-test-passphrase".into()),
    )
    .await
    .unwrap();
    assert_eq!(session.run_command("echo key").await.unwrap(), 0);
    assert_eq!(
        store
            .get(
                &falco_stub::credentials::secret_service(&config).unwrap(),
                "tester"
            )
            .unwrap()
            .as_deref(),
        Some("falco-test-passphrase")
    );
    session.disconnect().await;
    // A later unattended connection must not invoke a credential prompt.
    let next = ssh::connect_with_store(&config, ssh::ConnectOptions::default(), store, &|_| {
        panic!("must not prompt")
    })
    .await
    .unwrap();
    next.disconnect().await;
}
#[tokio::test]
async fn wrong_password_is_not_saved() {
    let port = start_server().await;
    let config = cfg(port);
    let store = Arc::new(Store::default());
    let result = ssh::connect_with_store(
        &config,
        ssh::ConnectOptions {
            interactive: true,
            ..Default::default()
        },
        store.clone(),
        &|_| Ok("wrong".into()),
    )
    .await;
    assert!(result.is_err());
    assert_eq!(store.get("it", "tester").unwrap(), None);
}
#[tokio::test]
async fn stalled_handshake_times_out_with_vpn_guidance() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let mut config = cfg(listener.local_addr().unwrap().port());
    config.requires_vpn = true;
    let result = ssh::connect_with_store(
        &config,
        ssh::ConnectOptions {
            timeout: std::time::Duration::from_millis(40),
            ..Default::default()
        },
        Arc::new(Store::default()),
        &|_| panic!("must not prompt"),
    )
    .await;
    let error = result.err().unwrap().diagnostic_json();
    assert!(error.contains("SSH_HANDSHAKE_TIMEOUT"));
    assert!(error.contains("Tailscale/WireGuard/VPN"));
}
#[tokio::test]
async fn changed_host_key_is_rejected_before_prompt() {
    let port = start_server().await;
    let config = cfg(port);
    let store = Arc::new(Store::default());
    falco_stub::host_keys::verify_host_key(&config, "SHA256:old", false, store.as_ref()).unwrap();
    let result = ssh::connect_with_store(
        &config,
        ssh::ConnectOptions {
            interactive: true,
            ..Default::default()
        },
        store.clone(),
        &|_| panic!("must verify key before prompting"),
    )
    .await;
    assert!(result
        .err()
        .unwrap()
        .diagnostic_json()
        .contains("HOST_KEY_CHANGED"));
    let session = ssh::connect_with_store(
        &config,
        ssh::ConnectOptions {
            interactive: true,
            accept_new_key: true,
            ..Default::default()
        },
        store,
        &|_| Ok(PASSWORD.into()),
    )
    .await
    .unwrap();
    session.disconnect().await;
}

#[tokio::test]
async fn wrong_key_passphrase_is_not_saved_or_echoed() {
    let port = start_server().await;
    let mut config = cfg(port);
    config.schema_version = 2;
    config.auth_method = "private_key".into();
    config.encrypted_private_key =
        Some(include_str!("../../tests/fixtures/encrypted_ed25519").into());
    let store = Arc::new(Store::default());
    let result = ssh::connect_with_store(
        &config,
        ssh::ConnectOptions {
            interactive: true,
            ..Default::default()
        },
        store.clone(),
        &|_| Ok("bad-test-passphrase".into()),
    )
    .await;
    let error = result.err().unwrap().diagnostic_json();
    assert!(error.contains("KEY_DECRYPTION_FAILED"));
    assert!(!error.contains("bad-test-passphrase"));
    assert_eq!(
        store
            .get(
                &falco_stub::credentials::secret_service(&config).unwrap(),
                "tester"
            )
            .unwrap(),
        None
    );
}
#[tokio::test]
async fn refused_connection_has_precise_code_and_does_not_prompt() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let config = cfg(listener.local_addr().unwrap().port());
    drop(listener);
    let result = ssh::connect_with_store(
        &config,
        ssh::ConnectOptions::default(),
        Arc::new(Store::default()),
        &|_| panic!("must not prompt"),
    )
    .await;
    assert!(result
        .err()
        .unwrap()
        .diagnostic_json()
        .contains("CONNECTION_REFUSED"));
}
