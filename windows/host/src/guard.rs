//! The connection wrapper (docs/spec/windows-tip.md §5.4): every connection's body runs under `catch_unwind` with a silent
//! panic hook, installed by the wrapper itself on first use (the same idea as `quiet_panics` in core/src/ffi.rs). A panic payload may
//! hold typed text (R2), so nothing is printed.
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::Once;

pub fn install_hook() {
    static ONCE: Once = Once::new();
    ONCE.call_once(|| std::panic::set_hook(Box::new(|_| {})));
}

/// Runs `f`; a panic gives `fail`.
pub fn run<T>(fail: T, f: impl FnOnce() -> T) -> T {
    install_hook();
    catch_unwind(AssertUnwindSafe(f)).unwrap_or(fail)
}
