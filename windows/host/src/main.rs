//! `shanjie-host.exe`, the resident host (docs/spec/windows-tip.md §5): one per user and logon session, the only process that loads
//! the models and holds an engine. The text service DLL in every app is its client over a named pipe. Windows only; on other targets an
//! empty binary. R2: no composition, commit, candidate or key reaches a log, a file, an error or `OutputDebugString`; the only file is
//! the numbers-only timing file (`timing` feature).

#[cfg(windows)]
mod engine;
#[cfg(windows)]
mod guard;
// The pipe I/O, the token checks and the timing file are the text service's own modules, shared by path (one copy of each).
#[cfg(windows)]
#[path = "../../tip/src/pipeio.rs"]
mod pipeio;
#[cfg(windows)]
mod server;
#[cfg(windows)]
#[allow(dead_code)]
#[path = "../../tip/src/timing.rs"]
mod timing;
#[cfg(windows)]
#[allow(dead_code)]
#[path = "../../tip/src/token.rs"]
mod token;

// The text service's key predicate, built into the test binary only: `window_keys_match_the_core` checks it against the real core.
#[cfg(all(windows, test))]
#[allow(dead_code)]
#[path = "../../tip/src/keys.rs"]
mod keys;

#[cfg(all(windows, test))]
mod r2_tests;
#[cfg(all(windows, test))]
mod tests;

#[cfg(windows)]
fn main() {
    server::run()
}

#[cfg(not(windows))]
fn main() {}
