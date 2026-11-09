//! The host's one engine (docs/spec/windows-tip.md §5.2 and §5.3; the scheme of the in-DLL engine of docs/spec/windows-tip.md §3.4). One engine behind
//! one lock; its owner is assigned by the host as (connection id, ctx), so a client's `ctx` means nothing on any other connection.
//! A key from another owner first discards the old owner's state (reset mode 1, never mode 0). Only the core's C ABI is used.
//!
//! Locking: a key takes the lock with `try_lock` only and answers `Busy` when it is taken, so no client waits on another; the lock
//! covers "call the core, copy the output", and the answer is written after it is released (the caller writes). `Release`, `Reset` and
//! a closed connection have no one waiting for an answer and must not be skipped (a stale owner would keep its composition), so they
//! wait for the lock. A poisoned lock ends the process (the next client starts a new host).
use crate::server::{exit, EXIT_POISONED};
use shanjie_core::ffi::{self as core_ffi, ShanjieEngine, ShanjieKey, ShanjieOutput};
use shanjie_win_proto::{Key, Out};
use std::ffi::{CStr, CString};
use std::path::{Path, PathBuf};
use std::ptr;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Mutex, MutexGuard, TryLockError};

/// The engine handle crosses threads under the mutex. The assertion below is the precondition for the `unsafe impl`: the core's engine
/// type is itself `Send`, so the pointer may move between the host's connection threads.
struct EnginePtr(*mut ShanjieEngine);
const _: () = {
    fn assert_send<T: Send>() {}
    #[allow(dead_code)]
    fn engine_is_send() {
        assert_send::<ShanjieEngine>();
    }
};
// SAFETY: see the assertion above; every use is under the `SHARED` mutex, one call at a time.
unsafe impl Send for EnginePtr {}

#[derive(Clone, Copy, PartialEq, Eq)]
pub struct Owner {
    pub conn: u64,
    pub ctx: u64,
}

struct Shared {
    eng: Option<EnginePtr>,
    owner: Option<Owner>,
}

static SHARED: Mutex<Shared> = Mutex::new(Shared { eng: None, owner: None });
/// The model is loaded (the pipe exists before it is: until then every `Hello` and `Key` gets `NotReady`).
static READY: AtomicBool = AtomicBool::new(false);

pub fn ready() -> bool {
    READY.load(Ordering::Acquire)
}

fn lock_wait() -> MutexGuard<'static, Shared> {
    SHARED.lock().unwrap_or_else(|_| exit(EXIT_POISONED))
}

/// Puts a loaded engine in place; from now on clients are welcome.
pub fn install(e: *mut ShanjieEngine) {
    lock_wait().eng = Some(EnginePtr(e));
    READY.store(true, Ordering::Release);
}

// ---- loading (docs/spec/windows-tip.md §3.4: the call list of cli/src/replay.rs `build_engine_packs`, except set_rerank(0)) ----

/// `SHANJIE_PACK_ACG | SHANJIE_PACK_IT`, `cli::replay::PACKS`.
const PACKS: u32 = 1 | 4;

/// Calls a setter that returns a snapshot; the snapshot is freed at once.
///
/// # Safety
/// `f` calls a core export with a valid engine and the given out pointer.
unsafe fn snap(f: impl FnOnce(*mut *mut ShanjieOutput) -> i32) -> bool {
    let mut o: *mut ShanjieOutput = ptr::null_mut();
    let rc = f(&mut o);
    if !o.is_null() {
        // SAFETY: an output of the core, freed once.
        unsafe { core_ffi::shanjie_output_free(o) };
    }
    rc == 0
}

/// The engine of `dir/data` (the folder of this exe, §5.1). `None` when any call fails (the engine built so far is freed).
/// Done without the lock, so a load never holds it.
pub fn load(dir: &Path) -> Option<*mut ShanjieEngine> {
    fn c(p: PathBuf) -> Option<CString> {
        CString::new(p.to_str()?).ok()
    }
    let data = dir.join("data");
    let (lexicon, packs) = (c(data.join("lexicon"))?, c(data.join("packs"))?);
    let (lm, tiny, colloquial) = (c(data.join("lm").join("bigram.sjlm"))?, c(data.join("lm").join("tinylm.sjtl"))?, c(data.join("lm").join("colloquial.sjlm"))?);
    let mut e: *mut ShanjieEngine = ptr::null_mut();
    // SAFETY: valid NUL-terminated strings and a live handle; the engine is freed on the first failing call.
    unsafe {
        if core_ffi::shanjie_engine_new_packs(lexicon.as_ptr(), 0, packs.as_ptr(), PACKS, &mut e) != 0 || e.is_null() {
            return None;
        }
        let ok = core_ffi::shanjie_engine_load_lm(e, lm.as_ptr()) == 0
            && core_ffi::shanjie_engine_load_tinylm(e, tiny.as_ptr()) == 0
            && core_ffi::shanjie_engine_load_colloquial(e, colloquial.as_ptr()) == 0
            && snap(|o| core_ffi::shanjie_engine_set_demote(e, 1, o))
            && snap(|o| core_ffi::shanjie_engine_set_prediction(e, 0, o))
            && snap(|o| core_ffi::shanjie_engine_set_abbreviation(e, 0, o))
            && snap(|o| core_ffi::shanjie_engine_set_english(e, 0, o))
            && snap(|o| core_ffi::shanjie_engine_set_candidate_vertical(e, 0, o))
            && snap(|o| core_ffi::shanjie_engine_set_rerank(e, 0, o))
            && core_ffi::shanjie_engine_set_learning(e, 0) == 0;
        if !ok {
            core_ffi::shanjie_engine_free(e);
            return None;
        }
    }
    Some(e)
}

// ---- keys ----

/// `KIND_ESC` of shanjie.h.
const KIND_ESC: u32 = 6;

/// What is kept of a `ShanjieOutput`: copied and freed inside one call (the header's rule).
struct CoreOut {
    handled: bool,
    commit: String,
    preedit: String,
    cursor: u32,
    candidate_selected: i32,
}

/// # Safety
/// `e` is a live engine handle, used under the mutex.
unsafe fn send(e: *mut ShanjieEngine, k: Key) -> Option<CoreOut> {
    let mut o: *mut ShanjieOutput = ptr::null_mut();
    // SAFETY: caller contract; `o` is a valid out pointer.
    let rc = unsafe { core_ffi::shanjie_engine_key(e, ShanjieKey { kind: k.kind, ch: k.ch, modifiers: k.modifiers }, &mut o) };
    if o.is_null() {
        return None;
    }
    // SAFETY: a live output of the core; its strings are valid NUL-terminated UTF-8 until freed.
    let out = unsafe {
        let r = &*o;
        let s = |p: *const std::os::raw::c_char| CStr::from_ptr(p).to_string_lossy().into_owned();
        CoreOut { handled: r.handled != 0, commit: s(r.commit), preedit: s(r.preedit), cursor: r.cursor_utf16, candidate_selected: r.candidate_selected }
    };
    // SAFETY: freed once, right after the copy.
    unsafe { core_ffi::shanjie_output_free(o) };
    // A non-zero return means "not handled, do not record" (shanjie.h): no output.
    (rc == 0).then_some(out)
}

/// # Safety
/// `e` is a live engine handle, used under the mutex.
unsafe fn reset(e: *mut ShanjieEngine) {
    // SAFETY: caller contract.
    unsafe { snap(|o| core_ffi::shanjie_engine_reset(e, 1, o)) };
}

pub enum Answer {
    NotReady,
    Busy,
    Out(Out),
}

fn wire(took_over: bool, out: Option<CoreOut>) -> Out {
    match out {
        Some(o) => Out {
            ran: true,
            core_error: false,
            took_over,
            handled: o.handled,
            commit: o.commit.encode_utf16().collect(),
            preedit: o.preedit.encode_utf16().collect(),
            cursor_utf16: o.cursor,
            candidate_selected: o.candidate_selected,
            candidates: Vec::new(),
        },
        // The core returned non-zero: the client edits nothing and lets the key go (the in-DLL engine's `out: None`, docs/spec/windows-tip.md §3.4).
        None => Out { ran: true, core_error: true, took_over, handled: false, commit: Vec::new(), preedit: Vec::new(), cursor_utf16: 0, candidate_selected: -1, candidates: Vec::new() },
    }
}

/// One key for `me`: take ownership (discarding another owner's state with mode 1), call the core, copy, unlock. No candidate window
/// exists in this slice, so a key that opens one is followed by ESC here (`OnKeyDown` step 5 of docs/spec/windows-tip.md §3.7, moved into the host).
pub fn key(me: Owner, k: Key) -> Answer {
    let mut g = match SHARED.try_lock() {
        Ok(g) => g,
        Err(TryLockError::WouldBlock) => return Answer::Busy,
        Err(TryLockError::Poisoned(_)) => exit(EXIT_POISONED),
    };
    let Some(EnginePtr(e)) = g.eng else { return Answer::NotReady };
    let took_over = g.owner != Some(me);
    // SAFETY: `e` is the live engine and the lock is held.
    let out = unsafe {
        if took_over && g.owner.is_some() {
            reset(e);
        }
        g.owner = Some(me);
        let mut out = send(e, k);
        if let Some(o) = out.as_mut() {
            if o.candidate_selected >= 0 {
                match send(e, Key { kind: KIND_ESC, ch: 0, modifiers: 0 }) {
                    Some(esc) => {
                        o.commit.push_str(&esc.commit);
                        o.preedit = esc.preedit;
                        o.cursor = esc.cursor;
                        o.candidate_selected = esc.candidate_selected;
                    }
                    None => {
                        reset(e);
                        o.preedit.clear();
                        o.cursor = 0;
                        o.candidate_selected = -1;
                    }
                }
                o.handled = true;
            }
        }
        out
    };
    drop(g);
    Answer::Out(wire(took_over, out))
}

/// `Release { ctx }`: the composition of exactly this owner ended in the app; discard the engine's state if it is still theirs.
pub fn release(me: Owner) {
    let mut g = lock_wait();
    if g.owner == Some(me) {
        if let Some(EnginePtr(e)) = g.eng {
            // SAFETY: live engine, lock held.
            unsafe { reset(e) };
        }
        g.owner = None;
    }
}

/// `Reset` (after the client caught a panic) or the connection closed: discard the engine's state if this connection owns it, any ctx.
pub fn drop_connection(conn: u64) {
    let mut g = lock_wait();
    if g.owner.is_some_and(|o| o.conn == conn) {
        if let Some(EnginePtr(e)) = g.eng {
            // SAFETY: live engine, lock held.
            unsafe { reset(e) };
        }
        g.owner = None;
    }
}
