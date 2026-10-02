//! TOFU host pins are independent of launchers, usernames and auth secrets.
use crate::{
    config::LauncherConfig,
    credentials::KeyStore,
    errors::{FResult, FalcoError},
};

pub fn host_service(cfg: &LauncherConfig) -> String {
    // Hostnames are case insensitive; normalize IP spelling as well.
    let host = cfg
        .host
        .parse::<std::net::IpAddr>()
        .map(|ip| ip.to_string())
        .unwrap_or_else(|_| cfg.host.trim_end_matches('.').to_ascii_lowercase());
    format!("falco:host-key:{host}:{}", cfg.port)
}
pub fn verify_host_key(
    cfg: &LauncherConfig,
    observed: &str,
    accept_new: bool,
    store: &dyn KeyStore,
) -> FResult<()> {
    let service = host_service(cfg);
    if let Some(expected) = store.get(&service, "host-key")? {
        if expected == observed {
            return Ok(());
        }
        if !accept_new {
            return Err(FalcoError::new("HOST_KEY_CHANGED", "Server key changed.", "If you accept this change, retry with --accept-new-key. Ask the user to verify and approve the new fingerprint before retrying.", 4)
                .target(format!("{}:{}", cfg.host, cfg.port))
                .details(format!("Expected {expected}; observed {observed}. Authentication was not sent.")));
        }
    }
    store.set(&service, "host-key", observed)
}
