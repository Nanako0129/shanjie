//! `shanjie-host.exe`, the resident host (docs/spec/windows-tip.md §5): one per user and logon session, the only process that loads
//! the models and holds an engine. The text service DLL in every app is its client over a named pipe. Windows only; on other targets an
//! empty binary. R2: no composition, commit, candidate or key reaches a log, a file, an error or `OutputDebugString`; the only file is
//! the numbers-only timing file (`timing` feature).
//!
//! A windows-subsystem program (no console window when the installer's Run value or setup starts it; it never reads or writes the standard
//! streams). The command line is `--resident` or `--resident --logon` (`startup`).
#![cfg_attr(not(test), windows_subsystem = "windows")]

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
#[cfg_attr(not(windows), allow(dead_code))]
mod startup;
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

/// Marker exports for the build's CI check (`dumpbin /exports`), exported by `build.rs`; not `shanjie_`-prefixed. Present exactly when the feature is on.
#[cfg(all(windows, feature = "release-ids"))]
#[no_mangle]
pub extern "C" fn SjMarkReleaseIds() {}

#[cfg(all(windows, feature = "timing"))]
#[no_mangle]
pub extern "C" fn SjMarkTiming() {}

#[cfg(windows)]
fn main() {
    // `args_os`: a word that is not Unicode must not end the process either.
    let args: Vec<String> = std::env::args_os().skip(1).map(|a| a.to_string_lossy().into_owned()).collect();
    server::run(startup::parse_args(&args))
}

#[cfg(not(windows))]
fn main() {}
