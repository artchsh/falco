//! The launcher entry point: load embedded config, parse argv, resolve the
//! password, connect, dispatch by mode, and map errors to the exit-code contract.

use std::io::IsTerminal;
use std::process::ExitCode;
use std::sync::Arc;

use falco_stub::cli::{self, Mode};
use falco_stub::config::{load_embedded_config, LauncherConfig};
use falco_stub::credentials::{self, hidden_prompt, OsKeyStore};
use falco_stub::errors::FalcoError;
use falco_stub::{interactive, sftp, ssh};

fn main() -> ExitCode {
    let code = falco_stub::runtime::run_to_exit(run());
    ExitCode::from(code as u8)
}

async fn run() -> i32 {
    // 1. Load embedded (non-secret) config from our own file.
    let config = match load_embedded_config() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("{}", e.diagnostic_json());
            return 2;
        }
    };

    // 2. Parse argv (excluding program name).
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let request = match cli::parse_args(&argv) {
        Ok(r) => r,
        Err(e) => {
            eprintln!("{}", e.diagnostic_json());
            return 2;
        }
    };

    let store = Arc::new(OsKeyStore);
    if request.reset_password {
        if let Err(e) = credentials::delete_password(&config, store.as_ref()) {
            eprintln!("{}", e.diagnostic_json());
            return e.exit_code();
        }
    }
    let options = ssh::ConnectOptions {
        accept_new_key: request.accept_new_key,
        interactive: std::io::stdin().is_terminal() && std::io::stderr().is_terminal(),
        ..Default::default()
    };
    match dispatch(&config, options, store, &request).await {
        Ok(code) => code,
        Err(e) => {
            eprintln!("{}", e.diagnostic_json());
            e.exit_code()
        }
    }
}

async fn dispatch(
    config: &LauncherConfig,
    options: ssh::ConnectOptions,
    store: Arc<OsKeyStore>,
    request: &cli::LaunchRequest,
) -> Result<i32, FalcoError> {
    let script = if let Some(path) = &request.stdin_path {
        Some(std::fs::read(path).map_err(|e| FalcoError::new("LOCAL_FILE_READ_FAILED", format!("Could not read script {path}: {e}"), "Check the local file path and read permissions before retrying. No remote script was started.", 2))?)
    } else {
        None
    };
    let session = ssh::connect_with_store(config, options, store, &hidden_prompt).await?;

    let result = match request.mode {
        Mode::Interactive => interactive::start_interactive_shell(&session).await,
        Mode::Command => {
            let cmd = request.command.as_deref().unwrap_or("");
            session.run_command(cmd).await
        }
        Mode::Stdin => {
            session
                .run_script(script.as_deref().unwrap_or_default())
                .await
        }
        Mode::Sftp => {
            let op = request.sftp.as_ref().expect("sftp op present");
            let sftp_session = session.open_sftp().await?;
            sftp::execute(&sftp_session, op, request.overwrite, request.mkdirs)
                .await
                .map(|_| 0)
        }
    };

    session.disconnect().await;
    result
}
