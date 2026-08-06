//! Command-line argument parsing. Behavioral parity with `launcher/cli.py`.
//!
//! Invocation styles (all against the one configured server):
//!   falco                       interactive shell (PTY)
//!   falco "docker ps"           one quoted command string (verbatim)
//!   falco docker ps             multiple args -> one safe re-quoted command
//!   falco --stdin deploy.sh     send a local file as a remote script
//! plus SFTP actions (see `sftp_spec`) and modifiers `--overwrite`, `--mkdirs`,
//! and the local-only `--reset-password` / `--reset-credential`.

use crate::errors::{FalcoError, FResult};

#[derive(Debug, PartialEq)]
pub enum Mode {
    Interactive,
    Command,
    Stdin,
    Sftp,
}

#[derive(Debug, PartialEq, Clone, Copy)]
pub enum SftpAction {
    Upload,
    Download,
    List,
    Mkdir,
    Remove,
    Move,
    UploadDir,
    DownloadDir,
}

#[derive(Debug)]
pub struct SftpOp {
    pub action: SftpAction,
    pub operands: Vec<String>,
}

#[derive(Debug)]
pub struct LaunchRequest {
    pub mode: Mode,
    pub command: Option<String>,
    pub stdin_path: Option<String>,
    pub sftp: Option<SftpOp>,
    pub reset_password: bool,
    pub overwrite: bool,
    pub mkdirs: bool,
}

impl LaunchRequest {
    fn bare(mode: Mode) -> Self {
        LaunchRequest {
            mode,
            command: None,
            stdin_path: None,
            sftp: None,
            reset_password: false,
            overwrite: false,
            mkdirs: false,
        }
    }
}

/// flag -> (action, number of positional operands it consumes)
fn sftp_spec(flag: &str) -> Option<(SftpAction, usize)> {
    Some(match flag {
        "--upload" => (SftpAction::Upload, 2),
        "--download" => (SftpAction::Download, 2),
        "--list" => (SftpAction::List, 1),
        "--mkdir" => (SftpAction::Mkdir, 1),
        "--remove" => (SftpAction::Remove, 1),
        "--move" => (SftpAction::Move, 2),
        "--upload-dir" => (SftpAction::UploadDir, 2),
        "--download-dir" => (SftpAction::DownloadDir, 2),
        _ => return None,
    })
}

fn take_flag(args: &mut Vec<String>, name: &str) -> bool {
    let before = args.len();
    args.retain(|a| a != name);
    before != args.len()
}

/// POSIX single-quote shell escaping, matching Python's `shlex.quote` for the
/// cases the launcher needs.
fn shell_quote(arg: &str) -> String {
    if !arg.is_empty()
        && arg
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"@%_-+=:,./".contains(&b))
    {
        return arg.to_string();
    }
    let mut out = String::with_capacity(arg.len() + 2);
    out.push('\'');
    for ch in arg.chars() {
        if ch == '\'' {
            out.push_str("'\\''");
        } else {
            out.push(ch);
        }
    }
    out.push('\'');
    out
}

/// Turn bare CLI arguments into a single remote command string. A single arg is
/// passed through verbatim; multiple args are each shell-quoted and joined.
pub fn build_command(args: &[String]) -> String {
    if args.len() == 1 {
        return args[0].clone();
    }
    args.iter()
        .map(|a| shell_quote(a))
        .collect::<Vec<_>>()
        .join(" ")
}

fn parse_sftp(mut args: Vec<String>) -> FResult<LaunchRequest> {
    let reset =
        take_flag(&mut args, "--reset-password") | take_flag(&mut args, "--reset-credential");
    let overwrite = take_flag(&mut args, "--overwrite");
    let mkdirs = take_flag(&mut args, "--mkdirs");

    let idx = args
        .iter()
        .position(|a| sftp_spec(a).is_some())
        .expect("caller guaranteed an sftp flag");
    if idx != 0 {
        let leading = args[..idx].join(" ");
        return Err(FalcoError::Config(format!(
            "Unexpected argument(s) before {}: {leading}",
            args[idx]
        )));
    }
    let action_flag = args[0].clone();
    let operands: Vec<String> = args[1..].to_vec();
    if operands.iter().any(|a| sftp_spec(a).is_some()) {
        return Err(FalcoError::Config(
            "Only one SFTP operation may be given at a time.".into(),
        ));
    }
    let (action, arity) = sftp_spec(&action_flag).unwrap();
    if operands.len() != arity {
        return Err(FalcoError::Config(format!(
            "{action_flag} expects {arity} argument(s), got {}.",
            operands.len()
        )));
    }
    let mut req = LaunchRequest::bare(Mode::Sftp);
    req.sftp = Some(SftpOp { action, operands });
    req.reset_password = reset;
    req.overwrite = overwrite;
    req.mkdirs = mkdirs;
    Ok(req)
}

pub fn parse_args(argv: &[String]) -> FResult<LaunchRequest> {
    let mut args: Vec<String> = argv.to_vec();

    // SFTP mode is selected by the presence of an SFTP action flag anywhere.
    if args.iter().any(|a| sftp_spec(a).is_some()) {
        return parse_sftp(args);
    }

    // Leading local-only reset flag(s).
    let mut reset = false;
    while let Some(first) = args.first() {
        if first == "--reset-password" || first == "--reset-credential" {
            reset = true;
            args.remove(0);
        } else {
            break;
        }
    }

    if args.is_empty() {
        let mut req = LaunchRequest::bare(Mode::Interactive);
        req.reset_password = reset;
        return Ok(req);
    }

    if args[0] == "--stdin" {
        if args.len() != 2 {
            return Err(FalcoError::Config(
                "--stdin requires exactly one file path, e.g. --stdin deploy.sh".into(),
            ));
        }
        let mut req = LaunchRequest::bare(Mode::Stdin);
        req.stdin_path = Some(args[1].clone());
        req.reset_password = reset;
        return Ok(req);
    }

    let mut req = LaunchRequest::bare(Mode::Command);
    req.command = Some(build_command(&args));
    req.reset_password = reset;
    Ok(req)
}
