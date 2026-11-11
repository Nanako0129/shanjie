//! shanjie TSF text input processor (docs/spec/windows-tip.md §3.1). Windows only, except the two pure modules (`keys`, `candlayout`), which are
//! built everywhere so their unit tests run on every CI platform; the rest is an empty crate elsewhere.
//!
//! The core, in the resident host `shanjie-host.exe` (docs/spec/windows-tip.md §5), decides what a key does; this crate is its thin
//! client: it maps keys in, carries them to the host and back (`client`), applies the answer to the TSF document, draws the candidate window
//! the host's answer describes (`cand`), and ends compositions in time. It links no core and loads no model. R2 (docs/spec/windows-tip.md §3.12): no
//! composition, commit, candidate or key reaches any log, file, `OutputDebugString`, window title or error text; the only file is the numbers-only
//! `timing` file.

pub mod candlayout;
pub mod keys;

#[cfg(windows)]
mod cand;
#[cfg(windows)]
mod client;
#[cfg(windows)]
mod dll;
#[cfg(windows)]
mod display;
#[cfg(windows)]
mod doc;
#[cfg(windows)]
mod guard;
#[cfg(windows)]
mod guids;
#[cfg(windows)]
mod pipeio;
#[cfg(windows)]
mod timing;
#[cfg(windows)]
mod tip;
#[cfg(windows)]
mod token;

#[cfg(all(windows, test))]
mod r2_tests;
