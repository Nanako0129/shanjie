//! The process-level engine (docs/spec/windows-tip.md §3.4): one engine per process behind a `Mutex`, loaded synchronously by the
//! first `ActivateEx`, never freed (a loaded engine also pins the DLL, see `dll::DllCanUnloadNow`). Only the C ABI of the core is used.
//! The lock covers "call the core, copy the output" and nothing else; the document is edited after it is released.
use crate::keys::{Key, KIND_ESC};
use crate::timing;
use shanjie_core::ffi::{self as core_ffi, ShanjieEngine, ShanjieKey, ShanjieOutput};
use std::ffi::{CStr, CString};
use std::path::{Path, PathBuf};
use std::ptr;
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::{Mutex, MutexGuard, TryLockError};

/// The engine handle crosses threads under the mutex. The assertion below is the contract's precondition for the `unsafe impl`:
/// the core's engine type is itself `Send`, so the pointer may move between the UI threads of the process.
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

enum Eng {
    Unloaded,
    Ready(EnginePtr),
    /// A load call failed: the process's TIP stays inert, no retry (docs/spec/windows-tip.md §3.4).
    Failed,
}

/// Who composes: the thread and the `ITfContext` (its pointer value) of the last key that reached the core.
#[derive(Clone, Copy, PartialEq, Eq)]
pub struct Owner {
    pub thread: u32,
    pub ctx: usize,
}

struct Shared {
    eng: Eng,
    owner: Option<Owner>,
}

static SHARED: Mutex<Shared> = Mutex::new(Shared { eng: Eng::Unloaded, owner: None });

const UNLOADED: u8 = 0;
const READY: u8 = 1;
const INERT: u8 = 2;
/// Lock-free mirror of `SHARED.eng`, for `OnTestKeyDown` and `DllCanUnloadNow`.
static STATE: AtomicU8 = AtomicU8::new(UNLOADED);

/// `try_lock` only: a thread must never wait on the load or on another thread's key. A poisoned lock makes the process inert for good.
fn lock() -> Option<MutexGuard<'static, Shared>> {
    match SHARED.try_lock() {
        Ok(g) => Some(g),
        Err(TryLockError::WouldBlock) => None,
        Err(TryLockError::Poisoned(_)) => {
            STATE.store(INERT, Ordering::Release);
            None
        }
    }
}

/// The engine is loaded and usable.
pub fn usable() -> bool {
    STATE.load(Ordering::Acquire) == READY
}

/// A load was tried (so `DllCanUnloadNow` must say no).
pub fn attempted() -> bool {
    STATE.load(Ordering::Acquire) != UNLOADED
}

// ---- loading (docs/spec/windows-tip.md §3.4: the call list of cli/src/replay.rs `build_engine_packs`, except set_rerank(0)) ----

/// `SHANJIE_PACK_ACG | SHANJIE_PACK_IT`, `cli::replay::PACKS`.
const PACKS: u32 = 1 | 4;

pub fn ensure_loaded() {
    if STATE.load(Ordering::Acquire) != UNLOADED {
        return;
    }
    let Some(mut g) = lock() else { return };
    if !matches!(g.eng, Eng::Unloaded) {
        return;
    }
    let t0 = timing::now_us();
    let loaded = crate::dll::module_path().and_then(|p| p.parent().map(Path::to_path_buf)).and_then(|d| load(&d));
    match loaded {
        Some(p) => {
            timing::record(timing::EV_LOAD, t0, timing::now_us());
            g.eng = Eng::Ready(EnginePtr(p));
            STATE.store(READY, Ordering::Release);
        }
        None => {
            g.eng = Eng::Failed;
            STATE.store(INERT, Ordering::Release);
        }
    }
}

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

fn load(dir: &Path) -> Option<*mut ShanjieEngine> {
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

/// What the TIP keeps of a `ShanjieOutput`: copied and freed inside one call (the header's rule).
pub struct Out {
    pub handled: bool,
    pub commit: String,
    pub preedit: String,
    pub cursor: u32,
    pub candidate_selected: i32,
}

/// # Safety
/// `e` is a live engine handle, used under the mutex.
unsafe fn send(e: *mut ShanjieEngine, k: Key) -> Option<Out> {
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
        Out { handled: r.handled != 0, commit: s(r.commit), preedit: s(r.preedit), cursor: r.cursor_utf16, candidate_selected: r.candidate_selected }
    };
    // SAFETY: freed once, right after the copy.
    unsafe { core_ffi::shanjie_output_free(o) };
    // A non-zero return means "not handled, do not record" (shanjie.h): the caller treats it as no output.
    (rc == 0).then_some(out)
}

/// # Safety
/// `e` is a live engine handle, used under the mutex.
unsafe fn reset(e: *mut ShanjieEngine) {
    // SAFETY: caller contract.
    unsafe { snap(|o| core_ffi::shanjie_engine_reset(e, 1, o)) };
}

pub struct Keyed {
    /// The core was called (the lock was free and the engine ready).
    pub ran: bool,
    /// The engine belonged to someone else (or no one) before this key; the previous composition, if any, was discarded.
    pub took_over: bool,
    /// `None` when the core returned non-zero.
    pub out: Option<Out>,
}

/// One key for `me`: lock, take ownership (discarding another owner's state with mode 1, never mode 0), call the core, copy, unlock.
/// A candidate window cannot be shown in this slice, so a key that opens one is followed by ESC (docs/spec/windows-tip.md §3.7, `OnKeyDown` step 5).
pub fn key(me: Owner, k: Key) -> Keyed {
    let idle = Keyed { ran: false, took_over: false, out: None };
    let Some(mut g) = lock() else { return idle };
    let Eng::Ready(EnginePtr(e)) = g.eng else { return idle };
    let took_over = g.owner != Some(me);
    // SAFETY: `e` is the live engine and the lock is held.
    unsafe {
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
                    }
                }
                o.handled = true;
            }
        }
        Keyed { ran: true, took_over, out }
    }
}

/// `OnSetFocus`, `OnKillThreadFocus`, `Deactivate`, `OnCompositionTerminated`, a failed document edit: discard the engine's state, but only
/// when `me` still owns it.
pub fn release_if_owner(me: Owner) {
    let Some(mut g) = lock() else { return };
    if g.owner == Some(me) {
        if let Eng::Ready(EnginePtr(e)) = g.eng {
            // SAFETY: live engine, lock held.
            unsafe { reset(e) };
        }
        g.owner = None;
    }
}

/// After a caught panic (guard.rs): reset whatever state there is.
pub fn reset_after_panic() {
    let Some(mut g) = lock() else { return };
    if let Eng::Ready(EnginePtr(e)) = g.eng {
        // SAFETY: live engine, lock held.
        unsafe { reset(e) };
    }
    g.owner = None;
}
