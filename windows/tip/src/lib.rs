//! shanjie TSF text input processor (docs/spec/windows-tip.md §3.1). Windows only; an empty crate elsewhere.
//!
//! The core, in the resident host `shanjie-host.exe` (docs/contracts/win-resident.md), decides what a key does; this crate is its thin
//! client: it maps keys in, carries them to the host and back (`client`), applies the answer to the TSF document, and ends compositions
//! in time. It links no core and loads no model. R2 (PLAN section 7): no composition, commit, candidate or key reaches any log, file,
//! `OutputDebugString` or error text; the only file is the numbers-only `timing` file.
#![cfg(windows)]

mod client;
mod dll;
mod display;
mod doc;
mod guard;
mod guids;
mod keys;
mod pipeio;
mod timing;
mod tip;
mod token;

#[cfg(test)]
mod r2_tests;
