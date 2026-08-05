//! Falco launcher library. The binary (`main.rs`) is a thin wrapper that loads
//! the embedded config and dispatches through these modules.

pub mod cli;
pub mod config;
pub mod credentials;
pub mod errors;
pub mod interactive;
pub mod sftp;
pub mod ssh;
