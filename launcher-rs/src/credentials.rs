//! Secrets are retrieved privately from the native OS store and saved after auth.
use crate::config::LauncherConfig;
use crate::errors::{FResult, FalcoError};
use zeroize::Zeroizing;

pub trait KeyStore: Send + Sync {
    fn get(&self, service: &str, user: &str) -> FResult<Option<String>>;
    fn set(&self, service: &str, user: &str, value: &str) -> FResult<()>;
    fn delete(&self, service: &str, user: &str) -> FResult<()>;
}
pub struct OsKeyStore;
fn store_error(context: &str, error: impl std::fmt::Display) -> FalcoError {
    FalcoError::Credential(format!("{context}: {error}"))
}
impl KeyStore for OsKeyStore {
    fn get(&self, service: &str, user: &str) -> FResult<Option<String>> {
        let entry = keyring::Entry::new(service, user)
            .map_err(|e| store_error("Cannot open OS credential store", e))?;
        match entry.get_password() {
            Ok(value) => Ok(Some(value)),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(e) => Err(store_error("Cannot read OS credential store", e)),
        }
    }
    fn set(&self, service: &str, user: &str, value: &str) -> FResult<()> {
        keyring::Entry::new(service, user)
            .map_err(|e| store_error("Cannot open OS credential store", e))?
            .set_password(value)
            .map_err(|e| store_error("Cannot save to OS credential store", e))
    }
    fn delete(&self, service: &str, user: &str) -> FResult<()> {
        let entry = keyring::Entry::new(service, user)
            .map_err(|e| store_error("Cannot open OS credential store", e))?;
        match entry.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(e) => Err(store_error("Cannot delete stored credential", e)),
        }
    }
}
pub fn hidden_prompt(label: &str) -> FResult<String> {
    rpassword::prompt_password(label).map_err(|_| {
        FalcoError::new(
            "CREDENTIAL_PROMPT_FAILED",
            "Could not read the hidden credential prompt.",
            "Ask the user to run this launcher in an interactive terminal.",
            3,
        )
    })
}
pub fn secret_service(cfg: &LauncherConfig) -> FResult<String> {
    if cfg.auth_method == "private_key" {
        let fingerprint = cfg.private_key()?.fingerprint(ssh_key::HashAlg::Sha256);
        Ok(format!(
            "{}:key-passphrase:{fingerprint}",
            cfg.credential_id
        ))
    } else {
        Ok(cfg.credential_id.clone())
    }
}
pub fn delete_password(cfg: &LauncherConfig, store: &dyn KeyStore) -> FResult<()> {
    store.delete(&secret_service(cfg)?, &cfg.username)
}
// Deliberately no Debug implementation: secret values never appear in diagnostics.
pub struct Secret {
    pub value: Zeroizing<String>,
    service: String,
    needs_save: bool,
}
pub fn prepare_secret(
    cfg: &LauncherConfig,
    store: &dyn KeyStore,
    interactive: bool,
    prompt: &dyn Fn(&str) -> FResult<String>,
) -> FResult<Secret> {
    let service = secret_service(cfg)?;
    if let Some(value) = store
        .get(&service, &cfg.username)?
        .filter(|s| !s.is_empty())
    {
        return Ok(Secret {
            value: Zeroizing::new(value),
            service,
            needs_save: false,
        });
    }
    if !interactive {
        return Err(FalcoError::new("CREDENTIAL_SETUP_REQUIRED", "No saved credential is available for this launcher on this machine.", "Ask the user to run the launcher once in a terminal and enter the password or key passphrase privately; then retry. Never request the secret in agent chat.", 3));
    }
    let kind = if cfg.auth_method == "private_key" {
        "SSH private-key passphrase"
    } else {
        "SSH password"
    };
    let value = prompt(&format!("{kind} for {}@{}: ", cfg.username, cfg.host))?;
    if value.is_empty() {
        return Err(FalcoError::new(
            "CREDENTIAL_INPUT_EMPTY",
            "No credential entered; authentication was cancelled.",
            "Ask the user to run this launcher in a terminal and enter the credential privately.",
            3,
        ));
    }
    Ok(Secret {
        value: Zeroizing::new(value),
        service,
        needs_save: true,
    })
}
pub fn commit_secret(cfg: &LauncherConfig, store: &dyn KeyStore, secret: &Secret) -> FResult<()> {
    if secret.needs_save {
        store.set(&secret.service, &cfg.username, &secret.value)?;
    }
    Ok(())
}
