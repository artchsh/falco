//! Error types and their mapping to process exit codes.
//!
//! Exit-code contract (identical to the Python launcher's runtime):
//! `Config -> 2`, `Credential -> 3`, `Ssh -> 4`, `Remote -> 4`.

use thiserror::Error;

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
}

impl FalcoError {
    pub fn exit_code(&self) -> i32 {
        match self {
            FalcoError::Config(_) => 2,
            FalcoError::Credential(_) => 3,
            FalcoError::Ssh(_) | FalcoError::Remote(_) => 4,
        }
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
}
