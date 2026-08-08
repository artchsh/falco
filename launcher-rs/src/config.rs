//! The non-secret launcher configuration and the appended-config trailer.
//!
//! A launcher binary is a config-less stub with the following trailer appended
//! at end-of-file by the editor:
//!
//! ```text
//! [ ...stub binary... ][ config JSON (utf-8) ][ u64 LE length ][ 8-byte magic "FALCOCFG" ]
//! ```
//!
//! At startup the launcher reads its own file (`std::env::current_exe`) and
//! recovers the config from that trailer. Only non-secret fields are embedded;
//! the SSH password is never stored here.

use serde::{Deserialize, Serialize};

use crate::errors::{FalcoError, FResult};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LauncherConfig {
    pub launcher_name: String,
    pub host: String,
    pub username: String,
    pub port: u16,
    pub credential_id: String,
    pub schema_version: u32,
}

/// Canonical keyring service name for a launcher. Mirrors the Python
/// `default_credential_id`. It is a stable label, not a secret.
pub fn default_credential_id(launcher_name: &str, username: &str, host: &str, port: u16) -> String {
    format!("falco:{launcher_name}:{username}@{host}:{port}")
}

impl LauncherConfig {
    pub fn from_json(text: &str) -> FResult<LauncherConfig> {
        serde_json::from_str(text)
            .map_err(|e| FalcoError::Config(format!("Configuration is not valid JSON: {e}")))
    }
}

pub const MAGIC: &[u8; 8] = b"FALCOCFG";

/// Append the config trailer (`json + u64 LE length + magic`) to a stub binary.
pub fn append_config(binary: &mut Vec<u8>, cfg: &LauncherConfig) {
    let json = serde_json::to_vec(cfg).expect("config serializes");
    let len = json.len() as u64;
    binary.extend_from_slice(&json);
    binary.extend_from_slice(&len.to_le_bytes());
    binary.extend_from_slice(MAGIC);
}

/// Parse the trailer from a full binary image.
pub fn read_config_from_bytes(bytes: &[u8]) -> FResult<LauncherConfig> {
    let not_configured =
        || FalcoError::Config("This stub was not configured by the Falco editor.".into());
    if bytes.len() < 16 {
        return Err(not_configured());
    }
    let (rest, magic) = bytes.split_at(bytes.len() - 8);
    if magic != MAGIC {
        return Err(not_configured());
    }
    let (rest, len_bytes) = rest.split_at(rest.len() - 8);
    let len = u64::from_le_bytes(len_bytes.try_into().unwrap()) as usize;
    if len == 0 || len > rest.len() {
        return Err(FalcoError::Config("Embedded config length is invalid.".into()));
    }
    let json = &rest[rest.len() - len..];
    let text = std::str::from_utf8(json)
        .map_err(|_| FalcoError::Config("Embedded config is not valid UTF-8.".into()))?;
    LauncherConfig::from_json(text)
}

/// Read this executable's own file and recover the embedded config.
pub fn load_embedded_config() -> FResult<LauncherConfig> {
    let exe = std::env::current_exe()
        .map_err(|e| FalcoError::Config(format!("Cannot locate own executable: {e}")))?;
    let bytes = std::fs::read(&exe)
        .map_err(|e| FalcoError::Config(format!("Cannot read own executable: {e}")))?;
    read_config_from_bytes(&bytes)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn credential_id_matches_python_format() {
        let id = default_credential_id("server-client-X", "root", "1.2.3.4", 22);
        assert_eq!(id, "falco:server-client-X:root@1.2.3.4:22");
    }

    #[test]
    fn from_json_parses_all_fields() {
        let json = r#"{"launcher_name":"server-client-X","host":"h","username":"u","port":2222,"credential_id":"cid","schema_version":1}"#;
        let cfg = LauncherConfig::from_json(json).unwrap();
        assert_eq!(cfg.launcher_name, "server-client-X");
        assert_eq!(cfg.port, 2222);
        assert_eq!(cfg.credential_id, "cid");
    }

    #[test]
    fn from_json_rejects_garbage() {
        assert!(LauncherConfig::from_json("not json").is_err());
    }
}
