use falco_stub::{
    config::LauncherConfig,
    credentials::{self, KeyStore},
    errors::{FResult, FalcoError},
    host_keys,
};
use std::collections::HashMap;
use std::sync::Mutex;

#[derive(Default)]
struct Store(Mutex<HashMap<(String, String), String>>);
impl KeyStore for Store {
    fn get(&self, s: &str, u: &str) -> FResult<Option<String>> {
        Ok(self.0.lock().unwrap().get(&(s.into(), u.into())).cloned())
    }
    fn set(&self, s: &str, u: &str, value: &str) -> FResult<()> {
        self.0
            .lock()
            .unwrap()
            .insert((s.into(), u.into()), value.into());
        Ok(())
    }
    fn delete(&self, s: &str, u: &str) -> FResult<()> {
        self.0.lock().unwrap().remove(&(s.into(), u.into()));
        Ok(())
    }
}
fn cfg() -> LauncherConfig {
    LauncherConfig::from_json(r#"{"launcher_name":"n","host":"Server.EXAMPLE","username":"u","port":22,"credential_id":"c","schema_version":2}"#).unwrap()
}

#[test]
fn host_key_changed_is_rejected_until_explicit_acceptance() {
    let store = Store::default();
    host_keys::verify_host_key(&cfg(), "SHA256:first", false, &store).unwrap();
    host_keys::verify_host_key(&cfg(), "SHA256:first", false, &store).unwrap();
    let err = host_keys::verify_host_key(&cfg(), "SHA256:second", false, &store).unwrap_err();
    let json: serde_json::Value = serde_json::from_str(&err.diagnostic_json()).unwrap();
    assert_eq!(json["error"], "HOST_KEY_CHANGED");
    assert!(json["action"]
        .as_str()
        .unwrap()
        .contains("--accept-new-key"));
    assert!(json["details"].as_str().unwrap().contains("SHA256:first"));
    host_keys::verify_host_key(&cfg(), "SHA256:second", true, &store).unwrap();
    host_keys::verify_host_key(&cfg(), "SHA256:second", false, &store).unwrap();
    credentials::delete_password(&cfg(), &store).unwrap();
    assert!(host_keys::verify_host_key(&cfg(), "SHA256:first", false, &store).is_err());
}

#[test]
fn noninteractive_first_run_requires_user_and_does_not_prompt() {
    let store = Store::default();
    let err = credentials::prepare_secret(&cfg(), &store, false, &|_| panic!("must not prompt"))
        .err()
        .unwrap();
    assert!(err.diagnostic_json().contains("CREDENTIAL_SETUP_REQUIRED"));
}

#[test]
fn new_secret_is_not_saved_until_committed_and_existing_does_not_prompt() {
    let store = Store::default();
    let secret =
        credentials::prepare_secret(&cfg(), &store, true, &|_| Ok("test-password".into())).unwrap();
    assert_eq!(store.get("c", "u").unwrap(), None);
    credentials::commit_secret(&cfg(), &store, &secret).unwrap();
    assert_eq!(
        store.get("c", "u").unwrap().as_deref(),
        Some("test-password")
    );
    credentials::prepare_secret(&cfg(), &store, false, &|_| panic!("stored secret")).unwrap();
}

#[test]
fn private_key_secret_id_is_separate_and_changes_with_the_key() {
    let mut config = cfg();
    config.auth_method = "private_key".into();
    config.encrypted_private_key =
        Some(include_str!("../../tests/fixtures/encrypted_ed25519").into());
    let id = credentials::secret_service(&config).unwrap();
    assert_ne!(id, config.credential_id);
    assert!(!format!("{config:?}").contains("OPENSSH"));
}

struct Broken;
impl KeyStore for Broken {
    fn get(&self, _: &str, _: &str) -> FResult<Option<String>> {
        Err(FalcoError::Credential("store unavailable".into()))
    }
    fn set(&self, _: &str, _: &str, _: &str) -> FResult<()> {
        Err(FalcoError::Credential("store unavailable".into()))
    }
    fn delete(&self, _: &str, _: &str) -> FResult<()> {
        Err(FalcoError::Credential("store unavailable".into()))
    }
}
#[test]
fn trust_store_errors_do_not_allow_connection() {
    assert!(host_keys::verify_host_key(&cfg(), "fingerprint", true, &Broken).is_err());
}
