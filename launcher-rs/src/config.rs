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

#[derive(Clone, PartialEq, Serialize, Deserialize)]
pub struct LauncherConfig {
    pub launcher_name: String,
    pub host: String,
    pub username: String,
    pub port: u16,
    pub credential_id: String,
    pub schema_version: u32,
    #[serde(default = "password_auth")]
    pub auth_method: String,
    #[serde(default)]
    pub encrypted_private_key: Option<String>,
    #[serde(default)]
    pub requires_vpn: bool,
}

fn password_auth() -> String { "password".into() }

#[used]
pub static CAPABILITY_MARKER: [u8; 14] = *b"FALCO_SCHEMA_2";

impl std::fmt::Debug for LauncherConfig {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("LauncherConfig").field("host", &self.host)
            .field("port", &self.port).field("auth_method", &self.auth_method)
            .finish_non_exhaustive()
    }
}

impl LauncherConfig {
    pub fn validate(&self) -> FResult<()> {
        let invalid = |message: &str| FalcoError::Config(message.into());
        if !matches!(self.schema_version, 1 | 2) {
            return Err(invalid("Unsupported configuration schema. Rebuild this launcher with the current editor."));
        }
        if self.port == 0 || [&self.launcher_name, &self.host, &self.username, &self.credential_id]
            .iter().any(|s| s.trim().is_empty() || s.chars().any(char::is_control)) {
            return Err(invalid("Invalid server configuration. Rebuild with a host, username and port in 1–65535."));
        }
        if self.host.chars().any(char::is_whitespace) || self.host.contains(['/', '\\']) {
            return Err(invalid("Invalid hostname/IP. Enter an address without a URL or whitespace in the editor."));
        }
        match self.auth_method.as_str() {
            "password" if self.encrypted_private_key.is_none() => (),
            "private_key" if self.schema_version == 2 => { self.private_key()?; },
            _ => return Err(invalid("Invalid authentication configuration. Rebuild with password or an encrypted OpenSSH private key.")),
        }
        Ok(())
    }

    pub fn private_key(&self) -> FResult<ssh_key::PrivateKey> {
        let text = self.encrypted_private_key.as_deref().filter(|s| s.len() <= 128 * 1024)
            .ok_or_else(|| FalcoError::Config("Missing or oversized encrypted OpenSSH private key. Rebuild the launcher.".into()))?;
        let key = ssh_key::PrivateKey::from_openssh(text)
            .map_err(|_| FalcoError::Config("Malformed encrypted OpenSSH private key. Rebuild with a valid key.".into()))?;
        let valid_kdf = matches!(key.kdf(), ssh_key::Kdf::Bcrypt { salt, rounds } if salt.len() >= 16 && (1..=1024).contains(rounds));
        if !key.is_encrypted() || !valid_kdf || !matches!(key.cipher(), ssh_key::Cipher::Aes128Ctr | ssh_key::Cipher::Aes192Ctr | ssh_key::Cipher::Aes256Ctr | ssh_key::Cipher::Aes256Cbc) {
            return Err(FalcoError::Config("The embedded key must be encrypted with AES and 1–1024 bcrypt rounds. Rebuild with an encrypted key.".into()));
        }
        Ok(key)
    }
}

/// Canonical keyring service name for a launcher. Mirrors the Python
/// `default_credential_id`. It is a stable label, not a secret.
pub fn default_credential_id(launcher_name: &str, username: &str, host: &str, port: u16) -> String {
    format!("falco:{launcher_name}:{username}@{host}:{port}")
}

impl LauncherConfig {
    pub fn from_json(text: &str) -> FResult<LauncherConfig> {
        let config: Self = serde_json::from_str(text)
            .map_err(|_| FalcoError::Config("Configuration is not valid JSON or has invalid field types. Rebuild this launcher.".into()))?;
        config.validate()?;
        Ok(config)
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
