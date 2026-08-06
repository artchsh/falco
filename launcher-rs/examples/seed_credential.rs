//! Throwaway dev helper: seed the OS keystore so the launcher's first-run
//! password prompt is skipped during automated smoke tests.
//!
//!   cargo run --example seed_credential -- <service> <user> <password>
//!
//! Not part of the shipped product; safe to delete.

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.len() != 3 {
        eprintln!("usage: seed_credential <service> <user> <password>");
        std::process::exit(2);
    }
    let entry = keyring::Entry::new(&args[0], &args[1]).expect("keyring entry");
    entry.set_password(&args[2]).expect("store password");
    println!("seeded {}::{}", args[0], args[1]);
}
