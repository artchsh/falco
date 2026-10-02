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
use falco_stub::ssh;

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
    let session = ssh::connect(&cfg(port), PASSWORD).await.expect("connect");
    let code = session.run_command("echo hi").await.expect("exec");
    assert_eq!(code, 0);
    session.disconnect().await;
}

#[tokio::test]
async fn exec_propagates_remote_exit_code() {
    let port = start_server().await;
    let session = ssh::connect(&cfg(port), PASSWORD).await.expect("connect");
    let code = session.run_command("exit 7").await.expect("exec");
    assert_eq!(code, 7);
    session.disconnect().await;
}

#[tokio::test]
async fn wrong_password_is_rejected() {
    let port = start_server().await;
    let result = ssh::connect(&cfg(port), "wrong-pass").await;
    assert!(result.is_err(), "expected authentication to fail");
}
