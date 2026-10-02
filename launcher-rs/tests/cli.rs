use falco_stub::cli::{build_command, parse_args, Mode, SftpAction};

fn v(args: &[&str]) -> Vec<String> {
    args.iter().map(|s| s.to_string()).collect()
}

#[test]
fn no_args_is_interactive() {
    let r = parse_args(&v(&[])).unwrap();
    assert!(matches!(r.mode, Mode::Interactive));
}

#[test]
fn leading_reset_then_interactive() {
    let r = parse_args(&v(&["--reset-password"])).unwrap();
    assert!(matches!(r.mode, Mode::Interactive));
    assert!(r.reset_password);
}

#[test]
fn single_command_is_verbatim() {
    let r = parse_args(&v(&["a && b"])).unwrap();
    assert!(matches!(r.mode, Mode::Command));
    assert_eq!(r.command.unwrap(), "a && b");
}

#[test]
fn multi_args_are_quoted_and_joined() {
    assert_eq!(build_command(&v(&["docker", "ps"])), "docker ps");
    assert_eq!(build_command(&v(&["echo", "a b"])), "echo 'a b'");
}

#[test]
fn stdin_requires_one_path() {
    let r = parse_args(&v(&["--stdin", "deploy.sh"])).unwrap();
    assert!(matches!(r.mode, Mode::Stdin));
    assert_eq!(r.stdin_path.unwrap(), "deploy.sh");
    assert!(parse_args(&v(&["--stdin"])).is_err());
    assert!(parse_args(&v(&["--stdin", "a", "b"])).is_err());
}

#[test]
fn sftp_upload_needs_two_operands() {
    let r = parse_args(&v(&["--upload", "./f", "/remote/f"])).unwrap();
    let op = r.sftp.unwrap();
    assert!(matches!(op.action, SftpAction::Upload));
    assert_eq!(op.operands, v(&["./f", "/remote/f"]));
    assert!(parse_args(&v(&["--upload", "./f"])).is_err());
}

#[test]
fn sftp_flags_extracted_anywhere() {
    let r = parse_args(&v(&["--overwrite", "--upload", "./f", "/r"])).unwrap();
    assert!(r.overwrite);
    assert!(matches!(r.sftp.unwrap().action, SftpAction::Upload));
}

#[test]
fn sftp_leading_arg_is_error() {
    assert!(parse_args(&v(&["junk", "--list", "/dir"])).is_err());
}

#[test]
fn two_sftp_actions_is_error() {
    assert!(parse_args(&v(&["--list", "/a", "--mkdir", "/b"])).is_err());
}

#[test]
fn accept_new_key_is_local_for_all_modes_and_not_remote_args() {
    for argv in [
        v(&["--accept-new-key", "docker ps"]),
        v(&[
            "--reset-credential",
            "--accept-new-key",
            "--stdin",
            "script",
        ]),
        v(&["--accept-new-key", "--list", "/"]),
    ] {
        assert!(parse_args(&argv).unwrap().accept_new_key);
    }
    let req = parse_args(&v(&["echo", "--accept-new-key"])).unwrap();
    assert!(!req.accept_new_key);
    assert_eq!(req.command.as_deref(), Some("echo --accept-new-key"));
}
