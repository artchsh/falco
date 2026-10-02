//! Password retrieval and storage backed by the OS credential store.
//!
//! The `keyring` crate selects the native backend automatically (Windows
//! Credential Manager / macOS Keychain / Linux Secret Service). The password is
//! only ever held in memory; it is never written to disk by Falco, placed on a
//! command line, or exported as an environment variable.

use crate::config::LauncherConfig;
use crate::errors::{FalcoError, FResult};

/// The small slice of a keystore Falco uses. A trait so tests can inject a fake.
pub trait KeyStore {
    fn get(&self, service: &str, user: &str) -> FResult<Option<String>>;
    fn set(&self, service: &str, user: &str, password: &str) -> FResult<()>;
    fn delete(&self, service: &str, user: &str) -> FResult<()>;
}

pub struct OsKeyStore;

impl KeyStore for OsKeyStore {
    fn get(&self, service: &str, user: &str) -> FResult<Option<String>> {
        let entry = keyring::Entry::new(service, user)
            .map_err(|e| FalcoError::Credential(format!("keyring init failed: {e}")))?;
        match entry.get_password() {
            Ok(pw) => Ok(Some(pw)),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(e) => Err(FalcoError::Credential(format!(
                "Could not read the password from the OS credential store: {e}"
            ))),
        }
    }

    fn set(&self, service: &str, user: &str, password: &str) -> FResult<()> {
        let entry = keyring::Entry::new(service, user)
            .map_err(|e| FalcoError::Credential(format!("keyring init failed: {e}")))?;
        entry.set_password(password).map_err(|e| {
            FalcoError::Credential(format!(
                "Could not save the password to the OS credential store: {e}"
            ))
        })
    }

    fn delete(&self, service: &str, user: &str) -> FResult<()> {
        let entry = match keyring::Entry::new(service, user) {
            Ok(e) => e,
            Err(_) => return Ok(()),
        };
        // Deleting a non-existent entry is not an error worth surfacing.
        // keyring v3 names this `delete_credential()` (v2 was `delete_password()`).
        let _ = entry.delete_credential();
        Ok(())
    }
}

/// Prompt for a password without echoing it. Blank input aborts.
pub fn hidden_prompt(label: &str) -> FResult<String> {
    let pw = rpassword::prompt_password(label)
        .map_err(|e| FalcoError::Credential(format!("Could not read password: {e}")))?;
    if pw.is_empty() {
        return Err(FalcoError::Credential("No password entered; aborting.".into()));
    }
    Ok(pw)
}

/// Remove any stored password (used by `--reset-password`).
pub fn delete_password(cfg: &LauncherConfig, store: &dyn KeyStore) {
    let _ = store.delete(&cfg.credential_id, &cfg.username);
}

/// Return a usable password, prompting for and storing one on first use.
pub fn resolve_password(
    cfg: &LauncherConfig,
    store: &dyn KeyStore,
    prompt: &dyn Fn(&str) -> FResult<String>,
) -> FResult<String> {
    if let Some(existing) = store.get(&cfg.credential_id, &cfg.username)? {
        if !existing.is_empty() {
            return Ok(existing);
        }
    }
    let label = format!("SSH password for {}@{}: ", cfg.username, cfg.host);
    let pw = prompt(&label)?;
    if pw.is_empty() {
        return Err(FalcoError::Credential("No password entered; aborting.".into()));
    }
    store.set(&cfg.credential_id, &cfg.username, &pw)?;
    Ok(pw)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    use std::collections::HashMap;

    struct FakeStore {
        map: RefCell<HashMap<(String, String), String>>,
    }
    impl FakeStore {
        fn new() -> Self {
            Self {
                map: RefCell::new(HashMap::new()),
            }
        }
    }
    impl KeyStore for FakeStore {
        fn get(&self, s: &str, u: &str) -> FResult<Option<String>> {
            Ok(self.map.borrow().get(&(s.into(), u.into())).cloned())
        }
        fn set(&self, s: &str, u: &str, pw: &str) -> FResult<()> {
            self.map.borrow_mut().insert((s.into(), u.into()), pw.into());
            Ok(())
        }
        fn delete(&self, s: &str, u: &str) -> FResult<()> {
            self.map.borrow_mut().remove(&(s.into(), u.into()));
            Ok(())
        }
    }

    fn cfg() -> LauncherConfig {
        LauncherConfig {
            launcher_name: "server-client-X".into(),
            host: "h".into(),
            username: "u".into(),
            port: 22,
            credential_id: "cid".into(),
            schema_version: 1,
            auth_method: "password".into(),
            encrypted_private_key: None,
            requires_vpn: false,
        }
    }

    #[test]
    fn resolve_prompts_and_stores_on_first_use() {
        let store = FakeStore::new();
        let pw = resolve_password(&cfg(), &store, &|_| Ok("secret".into())).unwrap();
        assert_eq!(pw, "secret");
        let pw2 = resolve_password(&cfg(), &store, &|_| panic!("should not prompt")).unwrap();
        assert_eq!(pw2, "secret");
    }

    #[test]
    fn blank_password_is_rejected() {
        let store = FakeStore::new();
        let err = resolve_password(&cfg(), &store, &|_| Ok(String::new()));
        assert!(err.is_err());
    }
}
