//! The host's one engine (docs/spec/windows-tip.md §5.2 and §5.3; the scheme of the in-DLL engine of docs/spec/windows-tip.md §3.4). One engine behind
//! one lock; its owner is assigned by the host as (connection id, ctx), so a client's `ctx` means nothing on any other connection.
//! A key from another owner first discards the old owner's state (reset mode 1, never mode 0). Only the core's C ABI is used.
//!
//! Locking: a key takes the lock with `try_lock` only and answers `Busy` when it is taken, so no client waits on another; the lock
//! covers "call the core, copy the output", and the answer is written after it is released (the caller writes). `Release`, `Reset` and
//! a closed connection have no one waiting for an answer and must not be skipped (a stale owner would keep its composition), so they
//! wait for the lock. A poisoned lock ends the process (the next client starts a new host).
use crate::server::{exit, EXIT_POISONED};
use crate::timing;
use shanjie_core::ffi::{self as core_ffi, ShanjieEngine, ShanjieKey, ShanjieOutput};
use shanjie_win_proto::{Key, Out};
use std::ffi::{CStr, CString};
use std::path::{Path, PathBuf};
use std::ptr;
use std::sync::atomic::{AtomicBool, AtomicU8, Ordering};
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
/// The key scheme of the installed engine (0 or 1): what every `Welcome` tells the client (docs/spec/windows-tip.md §5.3). Set by `install`, before `READY`.
static KEY_SCHEME: AtomicU8 = AtomicU8::new(0);

pub fn ready() -> bool {
    READY.load(Ordering::Acquire)
}

fn lock_wait() -> MutexGuard<'static, Shared> {
    SHARED.lock().unwrap_or_else(|_| exit(EXIT_POISONED))
}

/// The key scheme the installed engine was loaded with (`Welcome.key_scheme`).
pub fn key_scheme() -> u8 {
    KEY_SCHEME.load(Ordering::Acquire)
}

/// Puts a loaded engine in place; from now on clients are welcome. `scheme` is the one `load` gave the engine (0 or 1).
pub fn install(e: *mut ShanjieEngine, scheme: u32) {
    KEY_SCHEME.store(scheme as u8, Ordering::Release);
    lock_wait().eng = Some(EnginePtr(e));
    READY.store(true, Ordering::Release);
}

// ---- loading (docs/spec/windows-tip.md §3.4: the call list of cli/src/replay.rs `build_engine_packs`, except set_rerank(0)) ----

/// `SHANJIE_PACK_ACG | SHANJIE_PACK_IT`: `cli::replay::PACKS` (29) without the meme (8) and beauty (16) packs, which the Windows build does not ship (docs/spec/windows-tip.md §3.4).
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

/// The engine of `dir/data` (the folder of this exe, §5.1), with key scheme `scheme` (0 or 1; `server::run` reads the registry and passes the value, this
/// function never reads it, so a test's engine does not depend on the machine it runs on). `None` when any call fails (the engine built so far is freed).
/// Done without the lock, so a load never holds it.
pub fn load(dir: &Path, scheme: u32) -> Option<*mut ShanjieEngine> {
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
            && snap(|o| core_ffi::shanjie_engine_set_candidate_vertical(e, 1, o))
            && snap(|o| core_ffi::shanjie_engine_set_key_scheme(e, scheme, o))
            && snap(|o| core_ffi::shanjie_engine_set_rerank(e, 0, o))
            && core_ffi::shanjie_engine_set_learning(e, 0) == 0;
        if !ok {
            core_ffi::shanjie_engine_free(e);
            return None;
        }
    }
    Some(e)
}

// ---- warm-up (docs/spec/windows-tip.md §5.2) ----

const fn chr(c: char) -> Key {
    Key { kind: 1, ch: c as u32, modifiers: 0 }
}

/// The first four syllables of `REPLAY_KEYS_VERTICAL` (tests.rs): `a u / 6 w u 0 space y l 3 g ; 4`, ㄇㄧㄥˊ ㄊㄧㄢ ㄗㄠˇ ㄕㄤˋ. No Down, no window, no commit.
pub(crate) const WARM_KEYS: [Key; 14] = [
    chr('a'),
    chr('u'),
    chr('/'),
    chr('6'),
    chr('w'),
    chr('u'),
    chr('0'),
    Key { kind: 2, ch: 0, modifiers: 0 },
    chr('y'),
    chr('l'),
    chr('3'),
    chr('g'),
    chr(';'),
    chr('4'),
];

/// `SHANJIE_HOST_NO_WARM=1` skips the warm-up, for the measurements only: a build without the `timing` feature never reads the environment (§5.5 item 7).
#[cfg(feature = "timing")]
fn warm_disabled() -> bool {
    std::env::var_os("SHANJIE_HOST_NO_WARM").is_some_and(|v| v == "1")
}
#[cfg(not(feature = "timing"))]
fn warm_disabled() -> bool {
    false
}

/// Feeds `keys` to `e`, then always discards the state (reset mode 1). A key the core refuses stops the feed and is recorded as event 70
/// (its position in `keys`, the return code). False when the reset itself failed.
///
/// # Safety
/// `e` is a live engine handle that no other thread uses.
pub unsafe fn warm_with(e: *mut ShanjieEngine, keys: &[Key]) -> bool {
    for (n, k) in keys.iter().enumerate() {
        let mut o: *mut ShanjieOutput = ptr::null_mut();
        // SAFETY: caller contract; `o` is a valid out pointer, freed at once.
        let rc = unsafe {
            let rc = core_ffi::shanjie_engine_key(e, ShanjieKey { kind: k.kind, ch: k.ch, modifiers: k.modifiers }, &mut o);
            if !o.is_null() {
                core_ffi::shanjie_output_free(o);
            }
            rc
        };
        if rc != 0 {
            timing::record(timing::EV_WARM_FAILED, n as u64, u64::from(rc as u32));
            break;
        }
    }
    // SAFETY: caller contract.
    unsafe { reset(e) }
}

/// Decodes a fixed fake composition once on the freshly loaded `e` so the first real key does not meet cold model pages, then discards it:
/// no composition, no context, no learning (it is off). `e` is not installed yet, so `ready()` is still false. If the reset fails the engine
/// is freed and `dir` is loaded once more (with `scheme`), without a warm-up; `None` when that load fails too.
pub fn warmed(dir: &Path, scheme: u32, e: *mut ShanjieEngine) -> Option<*mut ShanjieEngine> {
    if warm_disabled() {
        return Some(e);
    }
    let t0 = timing::now_us();
    // SAFETY: `e` is the engine `load` just made; nothing else holds it.
    let clean = unsafe { warm_with(e, &WARM_KEYS) };
    timing::record(timing::EV_WARM, t0, timing::now_us());
    if clean {
        return Some(e);
    }
    // SAFETY: freed once, never installed.
    unsafe { core_ffi::shanjie_engine_free(e) };
    load(dir, scheme)
}

// ---- keys ----

/// What is kept of a `ShanjieOutput`: copied and freed inside one call (the header's rule). The candidates are copied only with a window open
/// (`candidate_selected >= 0`): `-1` with a count above 0 is the prediction row, which the Windows build keeps off; a closed window has
/// first and total 0 (`shanjie.h`).
struct CoreOut {
    handled: bool,
    commit: String,
    preedit: String,
    cursor: u32,
    candidate_selected: i32,
    candidate_first: u32,
    candidate_total: u32,
    candidates: Vec<String>,
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
        let open = r.candidate_selected >= 0 && !r.candidates.is_null();
        let candidates = if open { (0..r.candidate_count as usize).map(|i| s(*r.candidates.add(i))).collect() } else { Vec::new() };
        CoreOut {
            handled: r.handled != 0,
            commit: s(r.commit),
            preedit: s(r.preedit),
            cursor: r.cursor_utf16,
            candidate_selected: r.candidate_selected,
            candidate_first: if open { r.candidate_first } else { 0 },
            candidate_total: if open { r.candidate_total } else { 0 },
            candidates,
        }
    };
    // SAFETY: freed once, right after the copy.
    unsafe { core_ffi::shanjie_output_free(o) };
    // A non-zero return means "not handled, do not record" (shanjie.h): no output.
    (rc == 0).then_some(out)
}

/// # Safety
/// `e` is a live engine handle, used under the mutex. False when the core refused the reset.
unsafe fn reset(e: *mut ShanjieEngine) -> bool {
    // SAFETY: caller contract.
    unsafe { snap(|o| core_ffi::shanjie_engine_reset(e, 1, o)) }
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
            candidate_first: o.candidate_first,
            candidate_total: o.candidate_total,
            candidates: o.candidates.iter().map(|c| c.encode_utf16().collect()).collect(),
        },
        // The core returned non-zero: the client edits nothing and lets the key go (the in-DLL engine's `out: None`, docs/spec/windows-tip.md §3.4).
        None => Out {
            ran: true,
            core_error: true,
            took_over,
            handled: false,
            commit: Vec::new(),
            preedit: Vec::new(),
            cursor_utf16: 0,
            candidate_selected: -1,
            candidate_first: 0,
            candidate_total: 0,
            candidates: Vec::new(),
        },
    }
}

/// One key for `me`: take ownership (discarding another owner's state with mode 1), call the core, copy, unlock. The candidate window is the
/// core's vertical window (set in `load`); its rows, selection, first and total go to the client, which draws it (docs/spec/windows-tip.md §3.7, §5.3).
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
        send(e, k)
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
