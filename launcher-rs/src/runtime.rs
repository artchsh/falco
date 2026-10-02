//! The process owns this runtime, including any pending blocking terminal input.
use std::future::Future;

pub fn run_to_exit(future: impl Future<Output = i32>) -> i32 {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .expect("tokio runtime");
    let code = runtime.block_on(future);
    // Tokio stdin uses a blocking read which cannot be cancelled. Waiting for
    // it in Runtime::drop would hang after a shell timeout or disconnect.
    // This is a process-owned runtime: main exits immediately after this call.
    runtime.shutdown_background();
    code
}
