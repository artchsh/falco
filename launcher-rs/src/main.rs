//! The launcher entry point: load embedded config, parse argv, resolve the
//! password, connect, dispatch by mode, and map errors to the exit-code contract.

use std::process::ExitCode;

use falco_stub::cli::{self, Mode};
use falco_stub::config::{load_embedded_config, LauncherConfig};
use falco_stub::credentials::{self, hidden_prompt, OsKeyStore};
use falco_stub::errors::FalcoError;
use falco_stub::{interactive, sftp, ssh};

fn main() -> ExitCode {
    let rt = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .expect("tokio runtime");
    let code = rt.block_on(run());
    ExitCode::from(code as u8)
}

async fn run() -> i32 {
    // 1. Load embedded (non-secret) config from our own file.
    let config = match load_embedded_config() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("falco: {e}");
            return 2;
        }
    };

    // 2. Parse argv (excluding program name).
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let request = match cli::parse_args(&argv) {
        Ok(r) => r,
        Err(e) => {
            eprintln!("falco: {e}");
            return 2;
        }
    };

    // 3. Resolve the password (prompt once, store).
    let store = OsKeyStore;
    if request.reset_password {
        credentials::delete_password(&config, &store);
    }
    let password =
        match credentials::resolve_password(&config, &store, &|label| hidden_prompt(label)) {
            Ok(p) => p,
            Err(e) => {
                eprintln!("falco: {e}");
                return 3;
            }
        };

    // 4. Connect and dispatch.
    match dispatch(&config, &password, &request).await {
        Ok(code) => code,
        Err(e) => {
            eprintln!("falco: {e}");
            e.exit_code()
        }
    }
}

async fn dispatch(
    config: &LauncherConfig,
    password: &str,
    request: &cli::LaunchRequest,
) -> Result<i32, FalcoError> {
    let session = ssh::connect(config, password).await?;

    let result = match request.mode {
        Mode::Interactive => interactive::start_interactive_shell(&session).await,
        Mode::Command => {
            let cmd = request.command.as_deref().unwrap_or("");
            session.run_command(cmd).await
        }
        Mode::Stdin => {
            let path = request.stdin_path.as_deref().unwrap_or("");
            match std::fs::read(path) {
                Ok(bytes) => session.run_script(&bytes).await,
                Err(e) => {
                    session.disconnect().await;
                    return Err(FalcoError::Config(format!("cannot read {path}: {e}")));
                }
            }
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
