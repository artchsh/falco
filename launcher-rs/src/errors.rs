//! Stable launcher diagnostics for humans, AI agents and automation.
use serde::Serialize;
use thiserror::Error;

#[derive(Debug, Serialize)]
pub struct Diagnostic {
    pub error: &'static str,
    pub message: String,
    pub action: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub target: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub details: Option<String>,
    #[serde(skip)]
    exit_code: i32,
}
impl std::fmt::Display for Diagnostic {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {} {}", self.error, self.message, self.action)
    }
}
#[derive(Debug, Error)]
pub enum FalcoError {
    #[error("{0}")]
    Config(String),
    #[error("{0}")]
    Credential(String),
    #[error("{0}")]
    Ssh(String),
    #[error("{0}")]
    Remote(String),
    #[error("{0}")]
    Diagnostic(Box<Diagnostic>),
}
impl FalcoError {
    pub fn new(
        code: &'static str,
        message: impl Into<String>,
        action: impl Into<String>,
        exit_code: i32,
    ) -> Self {
        Self::Diagnostic(Box::new(Diagnostic {
            error: code,
            message: message.into(),
            action: action.into(),
            target: None,
            details: None,
            exit_code,
        }))
    }
    pub fn target(mut self, target: impl Into<String>) -> Self {
        if let Self::Diagnostic(ref mut d) = self {
            d.target = Some(target.into());
        }
        self
    }
    pub fn details(mut self, details: impl Into<String>) -> Self {
        if let Self::Diagnostic(ref mut d) = self {
            d.details = Some(details.into());
        }
        self
    }
    pub fn exit_code(&self) -> i32 {
        match self {
            Self::Config(_) => 2,
            Self::Credential(_) => 3,
            Self::Ssh(_) | Self::Remote(_) => 4,
            Self::Diagnostic(d) => d.exit_code,
        }
    }
    pub fn diagnostic_json(&self) -> String {
        let fallback;
        let d = match self {
            Self::Diagnostic(d) => d.as_ref(),
            Self::Config(message) => {
                fallback = Diagnostic { error: "CONFIGURATION_ERROR", message: message.clone(), action: "Check invocation arguments against how-to-use.md; rebuild with the current editor if the embedded configuration is invalid.".into(), target: None, details: None, exit_code: 2 };
                &fallback
            }
            Self::Credential(message) => {
                fallback = Diagnostic { error: "CREDENTIAL_STORE_UNAVAILABLE", message: message.clone(), action: "Ask the user to unlock the OS credential store. On Linux start a Secret Service provider; then retry in a user terminal.".into(), target: None, details: None, exit_code: 3 };
                &fallback
            }
            Self::Ssh(message) => {
                fallback = Diagnostic { error: "SSH_CONNECTION_FAILED", message: message.clone(), action: "Check the configured host/port, network/VPN and server SSH logs. No command was automatically retried.".into(), target: None, details: None, exit_code: 4 };
                &fallback
            }
            Self::Remote(message) => {
                fallback = Diagnostic { error: "REMOTE_OPERATION_FAILED", message: message.clone(), action: "Inspect remote/local paths, permissions and destination state before retrying; the operation may have partially completed.".into(), target: None, details: None, exit_code: 4 };
                &fallback
            }
        };
        serde_json::to_string(d).expect("diagnostic serializes")
    }
}
impl From<russh::Error> for FalcoError {
    fn from(e: russh::Error) -> Self {
        Self::Ssh(format!("SSH transport failed: {e}"))
    }
}
pub type FResult<T> = Result<T, FalcoError>;

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn exit_codes_match_contract() {
        assert_eq!(FalcoError::Config("x".into()).exit_code(), 2);
        assert_eq!(FalcoError::Credential("x".into()).exit_code(), 3);
        assert_eq!(FalcoError::Ssh("x".into()).exit_code(), 4);
        assert_eq!(FalcoError::Remote("x".into()).exit_code(), 4);
    }
    #[test]
    fn diagnostic_escapes_untrusted_details_into_one_json_line() {
        let err = FalcoError::new("TEST", "line\nquoted\"", "retry", 4).details("data\n");
        let text = err.diagnostic_json();
        assert_eq!(text.lines().count(), 1);
        let data: serde_json::Value = serde_json::from_str(&text).unwrap();
        assert_eq!(data["message"], "line\nquoted\"");
        assert_eq!(data["action"], "retry");
    }
}
