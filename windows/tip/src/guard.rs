//! The COM boundary wrapper (docs/spec/windows-tip.md §3.9). Every COM method body, every `Dll*` export, every
//! `IClassFactory` method and `DoEditSession` runs inside it: a silent panic hook installed on first entry (`DllMain` does nothing, so it
//! cannot be installed there), `catch_unwind`, and a bare failure code. Failures are `Error::from_hresult`, never an error with a message
//! (which would hang an `IErrorInfo` on the thread or hand text to a debugger).
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::Once;
use windows::Win32::Foundation::E_FAIL;
use windows_core::{Error, Result};

/// Same idea as `quiet_panics` in core/src/ffi.rs: the payload of a panic may hold typed text (R2), so nothing is printed.
fn install_hook() {
    static ONCE: Once = Once::new();
    ONCE.call_once(|| std::panic::set_hook(Box::new(|_| {})));
}

/// No message, no `IErrorInfo`.
pub fn fail() -> Error {
    Error::from_hresult(E_FAIL)
}

/// Runs `f`; on a panic runs `cleanup` (itself guarded) and returns `fail`.
pub fn run<T>(fail: T, f: impl FnOnce() -> T, cleanup: impl FnOnce()) -> T {
    install_hook();
    match catch_unwind(AssertUnwindSafe(f)) {
        Ok(v) => v,
        Err(_) => {
            let _ = catch_unwind(AssertUnwindSafe(cleanup));
            fail
        }
    }
}

/// The default wrapper: after a caught panic the host is told to reset this connection's state (discard) and the connection closes.
pub fn com<T>(f: impl FnOnce() -> Result<T>) -> Result<T> {
    com_with(f, || {})
}

/// `com` plus the instance's own cleanup (it drops the composition object it holds).
pub fn com_with<T>(f: impl FnOnce() -> Result<T>, cleanup: impl FnOnce()) -> Result<T> {
    run(Err(fail()), f, || {
        crate::client::reset_after_panic();
        cleanup();
    })
}
