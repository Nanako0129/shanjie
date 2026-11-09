//! shanjie TSF text input processor (docs/spec/windows-tip.md §3.1). Windows only; an empty crate elsewhere.
//!
//! The core decides what a key does (`shanjie_core::ffi`); this crate maps keys in, applies the output to the TSF document, and owns
//! lifecycle, ownership and locking. R2 (docs/spec/windows-tip.md §3.12): no composition, commit, candidate or key reaches any log, file,
//! `OutputDebugString` or error text; the only file is the numbers-only `timing` file.
#![cfg(windows)]

mod dll;
mod display;
mod doc;
mod engine;
mod guard;
mod guids;
mod keys;
mod timing;
mod tip;

#[cfg(test)]
mod r2_tests;
