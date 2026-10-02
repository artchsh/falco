//! SFTP operations over the same connection as command execution.
//!
//! Eight operations with: overwrite guards (never replace an existing
//! destination unless `--overwrite`), `--mkdirs` for missing destination
//! directories, recursive dir transfer, live progress, and downloads written to
//! a temp file that is renamed only on success (no truncated file on interrupt).
//!
//! Verified against russh-sftp 2.4: `File` implements tokio `AsyncRead`/
//! `AsyncWrite`; `read_dir` returns a `ReadDir` iterator of `DirEntry`.

use russh_sftp::client::SftpSession;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

use crate::cli::{SftpAction, SftpOp};
use crate::errors::{FResult, FalcoError};

// --------------------------------------------------------------------------- //
// Pure helpers (unit-tested)
// --------------------------------------------------------------------------- //

pub fn join_remote(dir: &str, name: &str) -> String {
    let trimmed = dir.strip_suffix('/').unwrap_or(dir);
    format!("{trimmed}/{name}")
}

pub fn temp_download_name(final_path: &str) -> String {
    format!("{final_path}.falco-partial")
}

pub fn human_bytes(n: u64) -> String {
    const UNITS: [&str; 5] = ["B", "KB", "MB", "GB", "TB"];
    if n < 1024 {
        return format!("{n} B");
    }
    let mut value = n as f64;
    let mut unit = 0;
    while value >= 1024.0 && unit < UNITS.len() - 1 {
        value /= 1024.0;
        unit += 1;
    }
    format!("{value:.1} {}", UNITS[unit])
}

// --------------------------------------------------------------------------- //
// Dispatch
// --------------------------------------------------------------------------- //

pub async fn execute(
    sftp: &SftpSession,
    op: &SftpOp,
    overwrite: bool,
    mkdirs: bool,
) -> FResult<()> {
    let a = &op.operands;
    match op.action {
        SftpAction::Upload => upload_file(sftp, &a[0], &a[1], overwrite, mkdirs).await,
        SftpAction::Download => download_file(sftp, &a[0], &a[1], overwrite).await,
        SftpAction::List => list_dir(sftp, &a[0]).await,
        SftpAction::Mkdir => make_dir(sftp, &a[0]).await,
        SftpAction::Remove => remove_path(sftp, &a[0]).await,
        SftpAction::Move => move_path(sftp, &a[0], &a[1]).await,
        SftpAction::UploadDir => upload_dir(sftp, &a[0], &a[1], overwrite, mkdirs).await,
        SftpAction::DownloadDir => download_dir(sftp, &a[0], &a[1], overwrite).await,
    }
}

fn remote_err(context: &str, e: impl std::fmt::Display) -> FalcoError {
    FalcoError::new("SFTP_OPERATION_FAILED", format!("{context}: {e}"), "Check the named local/remote path, read/write permissions, disk space and SSH/SFTP server availability. Inspect any partial transfer before retrying.", 4)
}

async fn remote_exists(sftp: &SftpSession, path: &str) -> bool {
    sftp.metadata(path.to_string()).await.is_ok()
}

async fn ensure_remote_parent(sftp: &SftpSession, remote_path: &str, mkdirs: bool) -> FResult<()> {
    if !mkdirs {
        return Ok(());
    }
    if let Some(idx) = remote_path.rfind('/') {
        let parent = &remote_path[..idx];
        if !parent.is_empty() && !remote_exists(sftp, parent).await {
            let mut acc = String::new();
            for part in parent.split('/') {
                if part.is_empty() {
                    acc.push('/');
                    continue;
                }
                if !acc.ends_with('/') {
                    acc.push('/');
                }
                acc.push_str(part);
                if !remote_exists(sftp, &acc).await {
                    let _ = sftp.create_dir(acc.clone()).await;
                }
            }
        }
    }
    Ok(())
}

async fn upload_file(
    sftp: &SftpSession,
    local: &str,
    remote: &str,
    overwrite: bool,
    mkdirs: bool,
) -> FResult<()> {
    if !overwrite && remote_exists(sftp, remote).await {
        return Err(FalcoError::Remote(format!(
            "Refusing to overwrite existing remote file {remote} (use --overwrite)."
        )));
    }
    ensure_remote_parent(sftp, remote, mkdirs).await?;

    let mut src = tokio::fs::File::open(local)
        .await
        .map_err(|e| remote_err(&format!("cannot open {local}"), e))?;
    let mut dst = sftp
        .create(remote.to_string())
        .await
        .map_err(|e| remote_err(&format!("cannot create {remote}"), e))?;

    let mut buf = vec![0u8; 32768];
    let mut total: u64 = 0;
    loop {
        let n = src
            .read(&mut buf)
            .await
            .map_err(|e| remote_err("read error", e))?;
        if n == 0 {
            break;
        }
        dst.write_all(&buf[..n])
            .await
            .map_err(|e| remote_err("write error", e))?;
        total += n as u64;
        print!("\rUploading {remote}: {}", human_bytes(total));
        let _ = std::io::Write::flush(&mut std::io::stdout());
    }
    dst.flush()
        .await
        .map_err(|e| remote_err("flush error", e))?;
    println!("\rUploaded {remote}: {} ", human_bytes(total));
    Ok(())
}

async fn download_file(
    sftp: &SftpSession,
    remote: &str,
    local: &str,
    overwrite: bool,
) -> FResult<()> {
    if !overwrite && tokio::fs::try_exists(local).await.unwrap_or(false) {
        return Err(FalcoError::Remote(format!(
            "Refusing to overwrite existing local file {local} (use --overwrite)."
        )));
    }
    let tmp = temp_download_name(local);
    let mut src = sftp
        .open(remote.to_string())
        .await
        .map_err(|e| remote_err(&format!("cannot open remote {remote}"), e))?;
    let mut dst = tokio::fs::File::create(&tmp)
        .await
        .map_err(|e| remote_err(&format!("cannot create {tmp}"), e))?;

    let mut buf = vec![0u8; 32768];
    let mut total: u64 = 0;
    loop {
        let n = src
            .read(&mut buf)
            .await
            .map_err(|e| remote_err("read error", e))?;
        if n == 0 {
            break;
        }
        dst.write_all(&buf[..n])
            .await
            .map_err(|e| remote_err("write error", e))?;
        total += n as u64;
        print!("\rDownloading {local}: {}", human_bytes(total));
        let _ = std::io::Write::flush(&mut std::io::stdout());
    }
    dst.flush()
        .await
        .map_err(|e| remote_err("flush error", e))?;
    drop(dst);
    // Rename temp -> final only on success (no truncated file on interruption).
    tokio::fs::rename(&tmp, local)
        .await
        .map_err(|e| remote_err("rename error", e))?;
    println!("\rDownloaded {local}: {} ", human_bytes(total));
    Ok(())
}

async fn list_dir(sftp: &SftpSession, dir: &str) -> FResult<()> {
    let entries = sftp
        .read_dir(dir.to_string())
        .await
        .map_err(|e| remote_err(&format!("cannot list {dir}"), e))?;
    for entry in entries {
        println!("{}", entry.file_name());
    }
    Ok(())
}

async fn make_dir(sftp: &SftpSession, dir: &str) -> FResult<()> {
    sftp.create_dir(dir.to_string())
        .await
        .map_err(|e| remote_err(&format!("cannot mkdir {dir}"), e))
}

async fn remove_path(sftp: &SftpSession, path: &str) -> FResult<()> {
    // Try file removal first; fall back to directory removal.
    if sftp.remove_file(path.to_string()).await.is_ok() {
        return Ok(());
    }
    sftp.remove_dir(path.to_string())
        .await
        .map_err(|e| remote_err(&format!("cannot remove {path}"), e))
}

async fn move_path(sftp: &SftpSession, from: &str, to: &str) -> FResult<()> {
    sftp.rename(from.to_string(), to.to_string())
        .await
        .map_err(|e| remote_err(&format!("cannot move {from} -> {to}"), e))
}

async fn upload_dir(
    sftp: &SftpSession,
    local: &str,
    remote: &str,
    overwrite: bool,
    _mkdirs: bool,
) -> FResult<()> {
    if !remote_exists(sftp, remote).await {
        let _ = sftp.create_dir(remote.to_string()).await;
    }
    let mut stack = vec![(std::path::PathBuf::from(local), remote.to_string())];
    while let Some((local_dir, remote_dir)) = stack.pop() {
        let mut rd = tokio::fs::read_dir(&local_dir)
            .await
            .map_err(|e| remote_err(&format!("cannot read {}", local_dir.display()), e))?;
        while let Some(entry) = rd
            .next_entry()
            .await
            .map_err(|e| remote_err("dir walk error", e))?
        {
            let name = entry.file_name().to_string_lossy().to_string();
            let child_remote = join_remote(&remote_dir, &name);
            let ft = entry
                .file_type()
                .await
                .map_err(|e| remote_err("stat error", e))?;
            if ft.is_dir() {
                if !remote_exists(sftp, &child_remote).await {
                    let _ = sftp.create_dir(child_remote.clone()).await;
                }
                stack.push((entry.path(), child_remote));
            } else {
                upload_file(
                    sftp,
                    &entry.path().to_string_lossy(),
                    &child_remote,
                    overwrite,
                    false,
                )
                .await?;
            }
        }
    }
    Ok(())
}

async fn download_dir(
    sftp: &SftpSession,
    remote: &str,
    local: &str,
    overwrite: bool,
) -> FResult<()> {
    tokio::fs::create_dir_all(local)
        .await
        .map_err(|e| remote_err(&format!("cannot create {local}"), e))?;
    let mut stack = vec![(remote.to_string(), std::path::PathBuf::from(local))];
    while let Some((remote_dir, local_dir)) = stack.pop() {
        let entries = sftp
            .read_dir(remote_dir.clone())
            .await
            .map_err(|e| remote_err(&format!("cannot list {remote_dir}"), e))?;
        for entry in entries {
            let name = entry.file_name();
            let child_remote = join_remote(&remote_dir, &name);
            let child_local = local_dir.join(&name);
            if entry.file_type().is_dir() {
                tokio::fs::create_dir_all(&child_local)
                    .await
                    .map_err(|e| remote_err("mkdir error", e))?;
                stack.push((child_remote, child_local));
            } else {
                download_file(
                    sftp,
                    &child_remote,
                    &child_local.to_string_lossy(),
                    overwrite,
                )
                .await?;
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn join_remote_uses_forward_slash() {
        assert_eq!(join_remote("/var/www", "index.html"), "/var/www/index.html");
        assert_eq!(
            join_remote("/var/www/", "index.html"),
            "/var/www/index.html"
        );
    }

    #[test]
    fn temp_name_is_partial_suffix() {
        assert_eq!(temp_download_name("/tmp/f.bin"), "/tmp/f.bin.falco-partial");
    }

    #[test]
    fn human_bytes_scales() {
        assert_eq!(human_bytes(512), "512 B");
        assert_eq!(human_bytes(1536), "1.5 KB");
    }
}
