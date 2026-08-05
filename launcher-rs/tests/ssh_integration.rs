// Integration test: stand up an in-process russh server that accepts a fixed
// password, then assert connect + run_command works.
//
// The assertions this test must enforce (the contract):
//   - connect() succeeds with the correct password
//   - connect() returns FalcoError::Ssh with the wrong password
//   - run_command returns the remote exit status
//
// NOTE: russh's SERVER API also drifts across versions. Implement the in-process
// server against the resolved russh server traits when the toolchain is
// available. If standing one up proves version-fragile, keep this #[ignore] and
// rely on the CI smoke test (plan Task 14) against a real sshd — but do NOT
// delete the assertions above; they are the behavioral contract.

#[test]
#[ignore = "requires in-process russh server; see plan Task 14 CI smoke test"]
fn connect_exec_exit_code() {
    // Implemented against resolved russh server API.
}
