//! S3a C ABI (docs/contracts/s3a.md §6) over `engine::Engine`; declarations in `core/include/shanjie.h`.
//! R2: every export runs under `catch_unwind` with a silent panic hook; errors are codes only, never text.
#![deny(unsafe_op_in_unsafe_fn)]

#[cfg(panic = "abort")]
compile_error!("C ABI requires panic=unwind");

use crate::engine::{Engine, EngineError, Key, KeyKind, Layout, Output, RerankJob, RerankResult, ResetMode, PACK_ALL};
use crate::lm::Profile;
use crate::tinylm::RerankLevel;
use std::ffi::{c_char, CStr, CString};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::Path;
use std::ptr;
use std::sync::{Mutex, Once, PoisonError};

pub const SHANJIE_OK: i32 = 0;
pub const SHANJIE_ERR_NULL: i32 = 1;
pub const SHANJIE_ERR_INVALID: i32 = 2;
pub const SHANJIE_ERR_LOAD: i32 = 3;
pub const SHANJIE_ERR_INTERNAL: i32 = 4;
/// S7c-2a section 2.5: `shanjie_engine_rerank_apply` changed nothing (stale, not run, already applied, every stretch kept its first).
pub const SHANJIE_NO_CHANGE: i32 = 5;

/// Holds a typed character, so no `Debug` (R2).
#[repr(C)]
#[derive(Clone, Copy)]
pub struct ShanjieKey {
    pub kind: u32,
    pub ch: u32,
    pub modifiers: u32,
}

#[repr(C)]
pub struct ShanjieOutput {
    pub handled: i32,
    pub commit: *const c_char,
    pub preedit: *const c_char,
    pub cursor_utf16: u32,
    pub candidate_count: u32,
    pub candidates: *const *const c_char,
    pub candidate_selected: i32,
    pub candidate_columns: u32,
    pub candidate_first: u32,
    pub candidate_total: u32,
    /// Appended last (candidate-vertical contract section 2.2): 0 horizontal or none, 1 vertical candidate window, 2 vertical prediction row.
    pub candidate_vertical: u32,
    /// Appended last (zhen-inline section 1.3f): the open English run in `preedit`, UTF-16 start and length; both 0 without a run.
    pub english_run_utf16_start: u32,
    pub english_run_utf16_len: u32,
}

/// Opaque to C.
pub struct ShanjieEngine(Engine);

/// Opaque to C (S7c-2a section 2.5): a rerank job and the result `shanjie_rerank_run` leaves in it, taken by the first apply.
/// The job and the result are owned data only (no engine borrow), so the job outlives the engine. Holds candidate text: no
/// `Debug`. Only shared references are ever made to it, and the result sits behind the mutex, so even a shell that broke the
/// one-thread-at-a-time rule could not race on the result.
pub struct ShanjieRerankJob {
    job: RerankJob,
    result: Mutex<Option<RerankResult>>,
}

// F8: Send and Sync from the field types, never from an `unsafe impl`.
const _: () = {
    fn send_sync<T: Send + Sync>() {}
    #[allow(dead_code)]
    fn assertion() {
        send_sync::<ShanjieRerankJob>();
    }
};

/// What a `*mut ShanjieOutput` really points to: the C struct first (repr(C) => offset 0), then the
/// buffers it points into. Moving a CString / Vec does not move its heap buffer.
#[repr(C)]
struct OwnedOutput {
    out: ShanjieOutput,
    _strings: Vec<CString>,
    _ptrs: Vec<*const c_char>,
}

fn quiet_panics() {
    static ONCE: Once = Once::new();
    #[cfg(test)]
    if std::env::var_os(tests::DEFAULT_HOOK_ENV).is_some() {
        return; // positive control only (§7.3)
    }
    ONCE.call_once(|| std::panic::set_hook(Box::new(|_| {})));
}

/// Silent hook + catch_unwind; a panic becomes code 4.
fn guard(f: impl FnOnce() -> i32) -> i32 {
    quiet_panics();
    catch_unwind(AssertUnwindSafe(f)).unwrap_or(SHANJIE_ERR_INTERNAL)
}

/// Sets `*out = NULL` when `out` is non-NULL; false when `out` is NULL.
///
/// # Safety
/// `out` is NULL or valid for one pointer write.
unsafe fn clear_out<T>(out: *mut *mut T) -> bool {
    if out.is_null() {
        return false;
    }
    // SAFETY: non-NULL and writable per the caller contract (§6).
    unsafe { *out = ptr::null_mut() };
    true
}

fn to_key(k: ShanjieKey) -> Option<Key> {
    let kind = KeyKind::from_code(k.kind)?;
    let ch = if kind == KeyKind::Char { char::from_u32(k.ch)? } else { '\0' };
    Some(Key { kind, ch, modifiers: k.modifiers })
}

/// Copies an engine output into C-owned memory; `None` when a string holds an interior NUL.
fn to_c(o: Output) -> Option<*mut ShanjieOutput> {
    let mut strings = Vec::with_capacity(2 + o.candidates.len());
    strings.push(CString::new(o.commit).ok()?);
    strings.push(CString::new(o.preedit).ok()?);
    for c in o.candidates {
        strings.push(CString::new(c).ok()?);
    }
    let ptrs: Vec<*const c_char> = strings[2..].iter().map(|s| s.as_ptr()).collect();
    let out = ShanjieOutput {
        handled: o.handled as i32,
        commit: strings[0].as_ptr(),
        preedit: strings[1].as_ptr(),
        cursor_utf16: o.cursor_utf16,
        candidate_count: u32::try_from(ptrs.len()).ok()?,
        candidates: if ptrs.is_empty() { ptr::null() } else { ptrs.as_ptr() },
        candidate_selected: match o.selected {
            Some(s) => i32::try_from(s).ok()?,
            None => -1,
        },
        candidate_columns: o.columns,
        candidate_first: o.first,
        candidate_total: o.total,
        candidate_vertical: o.vertical,
        english_run_utf16_start: o.english_run_utf16_start,
        english_run_utf16_len: o.english_run_utf16_len,
    };
    Some(Box::into_raw(Box::new(OwnedOutput { out, _strings: strings, _ptrs: ptrs })).cast())
}

/// # Safety
/// `out` is NULL or valid for one pointer write; `o` came from the engine.
unsafe fn emit(o: Output, out: *mut *mut ShanjieOutput) -> i32 {
    match to_c(o) {
        Some(p) => {
            // SAFETY: `out` was checked non-NULL by `clear_out` in the caller.
            unsafe { *out = p };
            SHANJIE_OK
        }
        None => SHANJIE_ERR_INTERNAL,
    }
}

/// §6: after code 4 the engine is reset (discard). A panic inside the reset itself is swallowed.
///
/// # Safety
/// `engine` is NULL or a live handle from `shanjie_engine_new`.
unsafe fn discard(engine: *mut ShanjieEngine) {
    if engine.is_null() {
        return;
    }
    let _ = catch_unwind(AssertUnwindSafe(|| {
        // SAFETY: live handle, single-threaded use (§6).
        let e = unsafe { &mut (*engine).0 };
        e.reset(ResetMode::Discard);
    }));
}

/// # Safety
/// `data_dir` is NULL or a NUL-terminated string; `out` is NULL or valid for one pointer write.
#[no_mangle]
pub unsafe extern "C" fn shanjie_engine_new(
    data_dir: *const c_char,
    layout: u32,
    out: *mut *mut ShanjieEngine,
) -> i32 {
    // SAFETY: forwarded caller contract; mask 0 ignores the NULL packs_dir.
    unsafe { shanjie_engine_new_packs(data_dir, layout, std::ptr::null(), 0, out) }
}

/// Like `shanjie_engine_new`, with word packs (docs/contracts/acg-pack.md A.2): `packs_dir` holds the pack
/// files and `packs` is the bit mask of the packs to enable (bit 0 ACG, bit 2 IT). Mask 0 ignores `packs_dir` (may be
/// NULL) and is exactly `shanjie_engine_new`. A pack whose file is missing contributes nothing. A bit outside
/// the known ones returns 2.
///
/// # Safety
/// `data_dir` is NULL or a NUL-terminated string; `packs_dir` is NULL (mask 0 only) or a NUL-terminated
/// string; `out` is NULL or valid for one pointer write.
#[no_mangle]
pub unsafe extern "C" fn shanjie_engine_new_packs(
    data_dir: *const c_char,
    layout: u32,
    packs_dir: *const c_char,
    packs: u32,
    out: *mut *mut ShanjieEngine,
) -> i32 {
    guard(|| {
        // SAFETY: forwarded caller contract.
        if !unsafe { clear_out(out) } || data_dir.is_null() || (packs != 0 && packs_dir.is_null()) {
            return SHANJIE_ERR_NULL;
        }
        let layout = match layout {
            0 => Layout::Standard,
            1 => Layout::Eten,
            _ => return SHANJIE_ERR_INVALID,
        };
        if packs & !PACK_ALL != 0 {
            return SHANJIE_ERR_INVALID;
        }
        // SAFETY: non-NULL, NUL-terminated per the caller contract.
        let Ok(dir) = unsafe { CStr::from_ptr(data_dir) }.to_str() else {
            return SHANJIE_ERR_INVALID;
        };
        let pdir = if packs == 0 {
            None
        } else {
            // SAFETY: non-NULL (checked above), NUL-terminated per the caller contract.
            let Ok(p) = unsafe { CStr::from_ptr(packs_dir) }.to_str() else {
                return SHANJIE_ERR_INVALID;
            };
            Some((Path::new(p), packs))
        };
        match Engine::new_with_packs(Path::new(dir), layout, pdir) {
            Ok(e) => {
                // SAFETY: `out` checked non-NULL above.
                unsafe { *out = Box::into_raw(Box::new(ShanjieEngine(e))) };
                SHANJIE_OK
            }
            Err(EngineError::LoadFailed) => SHANJIE_ERR_LOAD,
            Err(EngineError::Internal) => SHANJIE_ERR_INTERNAL,
        }
    })
}

/// # Safety
/// `engine` is NULL or a handle from `shanjie_engine_new` not yet freed.
#[no_mangle]
pub unsafe extern "C" fn shanjie_engine_free(engine: *mut ShanjieEngine) {
    guard(|| {
        if !engine.is_null() {
            // SAFETY: allocated by `Box::into_raw` in `shanjie_engine_new`, freed once (§6).
            drop(unsafe { Box::from_raw(engine) });
        }
        SHANJIE_OK
    });
}

/// # Safety
/// `engine` is NULL or a live handle; `out` is NULL or valid for one pointer write.
#[no_mangle]
pub unsafe extern "C" fn shanjie_engine_key(
    engine: *mut ShanjieEngine,
    key: ShanjieKey,
    out: *mut *mut ShanjieOutput,
) -> i32 {
    let rc = guard(|| {
        // SAFETY: forwarded caller contract.
        if !unsafe { clear_out(out) } || engine.is_null() {
            return SHANJIE_ERR_NULL;
        }
        let Some(k) = to_key(key) else { return SHANJIE_ERR_INVALID };
        // SAFETY: live handle, single-threaded use (§6).
        let e = unsafe { &mut (*engine).0 };
        match e.key(k) {
            Ok(o) => {
                #[cfg(test)]
                tests::inject(&o);
                // SAFETY: `out` checked non-NULL above.
                unsafe { emit(o, out) }
            }
            Err(_) => SHANJIE_ERR_INTERNAL,
        }
    });
    if rc == SHANJIE_ERR_INTERNAL {
        // SAFETY: forwarded caller contract.
        unsafe { discard(engine) };
    }
    rc
}

/// s3b2 §8.2 mouse pick: chooses `candidate_first + index` of the last output through the same
/// `choose()` as Enter; with the candidate window closed it selects from the V3 prediction row instead. 2 when
/// there is neither a window nor a row, or `index` is outside that output (state
/// unchanged).
///
/// # Safety
/// `engine` is NULL or a live handle; `out` is NULL or valid for one pointer write.
#[no_mangle]
pub unsafe extern "C" fn shanjie_engine_pick(
    engine: *mut ShanjieEngine,
    index: u32,
    out: *mut *mut ShanjieOutput,
) -> i32 {
    let rc = guard(|| {
        // SAFETY: forwarded caller contract.
        if !unsafe { clear_out(out) } || engine.is_null() {
            return SHANJIE_ERR_NULL;
        }
        // SAFETY: live handle, single-threaded use (§6).
        let e = unsafe { &mut (*engine).0 };
        match e.pick(index as usize) {
            Ok(Some(o)) => {
                // SAFETY: `out` checked non-NULL above.
                unsafe { emit(o, out) }
            }
            Ok(None) => SHANJIE_ERR_INVALID,
            Err(_) => SHANJIE_ERR_INTERNAL,
        }
    });
    if rc == SHANJIE_ERR_INTERNAL {
        // SAFETY: forwarded caller contract.
        unsafe { discard(engine) };
    }
    rc
}

/// # Safety
/// `engine` is NULL or a live handle; `out` is NULL or valid for one pointer write.
#[no_mangle]
pub unsafe extern "C" fn shanjie_engine_reset(
    engine: *mut ShanjieEngine,
    mode: u32,
    out: *mut *mut ShanjieOutput,
) -> i32 {
    let rc = guard(|| {
        // SAFETY: forwarded caller contract.
        if !unsafe { clear_out(out) } || engine.is_null() {
            return SHANJIE_ERR_NULL;
        }
        let mode = match mode {
            0 => ResetMode::Commit,
            1 => ResetMode::Discard,
            _ => return SHANJIE_ERR_INVALID,
        };
        // SAFETY: live handle, single-threaded use (§6).
        let o = unsafe { &mut (*engine).0 }.reset(mode);
        // SAFETY: `out` checked non-NULL above.
        unsafe { emit(o, out) }
    });
    if rc == SHANJIE_ERR_INTERNAL {
        // SAFETY: forwarded caller contract.
        unsafe { discard(engine) };
    }
    rc
}

/// S2c. Loads the bigram model at `path` (with `classes.sjc` beside it, and a `kn.sjkn` Kneser-Ney side file if one
/// exists there: a broken or mismatching one fails the load with code 3, a valid one changes the scoring; the app
/// ships one since model-v6; likewise an optional `trigram.sjt3`, which only the formal profile uses, the app ships none yet), reading `data_dir/overlay-add.tsv`, `names-add.tsv` (and demote.tsv) again. Does not
/// recompute the composition display. Any failure leaves the previous LM state (none or the old model).
///
/// # Safety
/// `engine` is NULL or a live handle; `path` is NULL or a NUL-terminated string.
#[no_mangle]
pub unsafe extern "C" fn shanjie_engine_load_lm(engine: *mut ShanjieEngine, path: *const c_char) -> i32 {
    let rc = guard(|| {
        if engine.is_null() || path.is_null() {
            return SHANJIE_ERR_NULL;
        }
        // SAFETY: non-NULL, NUL-terminated per the caller contract.
        let Ok(path) = unsafe { CStr::from_ptr(path) }.to_str() else {
            return SHANJIE_ERR_INVALID;
        };
        // SAFETY: live handle, single-threaded use (§6).
        let e = unsafe { &mut (*engine).0 };
        match e.load_lm(Path::new(path)) {
            Ok(()) => SHANJIE_OK,
            Err(EngineError::LoadFailed) => SHANJIE_ERR_LOAD,
            Err(EngineError::Internal) => SHANJIE_ERR_INTERNAL,
        }
    });
    if rc == SHANJIE_ERR_INTERNAL {
        // SAFETY: forwarded caller contract.
        unsafe { discard(engine) };
    }
    rc
}

/// S2c. Profile 0 chat, 1 formal; recomputes the composition and returns its snapshot (`handled = 1`,
/// empty commit). Out-of-range profile changes nothing.
///
/// # Safety
/// `engine` is NULL or a live handle; `out` is NULL or valid for one pointer write.
#[no_mangle]
pub unsafe extern "C" fn shanjie_engine_set_profile(
    engine: *mut ShanjieEngine,
    profile: u32,
    out: *mut *mut ShanjieOutput,
) -> i32 {
    let rc = guard(|| {
        // SAFETY: forwarded caller contract.
        if !unsafe { clear_out(out) } || engine.is_null() {
            return SHANJIE_ERR_NULL;
        }
        let Some(profile) = Profile::from_code(profile) else { return SHANJIE_ERR_INVALID };
        // SAFETY: live handle, single-threaded use (§6).
        let e = unsafe { &mut (*engine).0 };
        match e.set_profile(profile) {
            Ok(o) => {
                #[cfg(test)]
                tests::inject(&o);
                // SAFETY: `out` checked non-NULL above.
                unsafe { emit(o, out) }
            }
            Err(_) => SHANJIE_ERR_INTERNAL,
        }
    });
    if rc == SHANJIE_ERR_INTERNAL {
        // SAFETY: forwarded caller contract.
        unsafe { discard(engine) };
    }
    rc
}

/// s3e. Replace the punctuation alternatives with `table`: UTF-8 lines `mark\talt\talt…`, blank
/// lines ignored, a repeated mark overrides the earlier line, at most 64 KB and 1,000 lines. Any
/// invalid input returns 2 and keeps the previous table. Does not change the current display.
///
/// # Safety
/// `engine` is NULL or a live handle; `table` is NULL or a NUL-terminated string.
#[no_mangle]
pub unsafe extern "C" fn shanjie_engine_set_punctuation(engine: *mut ShanjieEngine, table: *const c_char) -> i32 {
    guard(|| {
        if engine.is_null() || table.is_null() {
            return SHANJIE_ERR_NULL;
        }
        // SAFETY: non-NULL, NUL-terminated per the caller contract.
        let Ok(table) = unsafe { CStr::from_ptr(table) }.to_str() else {
            return SHANJIE_ERR_INVALID;
        };
        // SAFETY: live handle, single-threaded use (§6).
        let e = unsafe { &mut (*engine).0 };
        if e.set_punctuation(table) { SHANJIE_OK } else { SHANJIE_ERR_INVALID }
    })
}

// S4 (docs/contracts/s4-learning.md §2–§4): see core/include/shanjie.h for the semantics.

/// # Safety
/// `engine` is NULL or a live handle; `utf8` is NULL or a NUL-terminated string.
#[no_mangle]
pub unsafe extern "C" fn shanjie_engine_set_left_context(engine: *mut ShanjieEngine, utf8: *const c_char) -> i32 {
    guard(|| {
        if engine.is_null() {
            return SHANJIE_ERR_NULL;
        }
        // SAFETY: live handle, single-threaded use (§6).
        let e = unsafe { &mut (*engine).0 };
        if utf8.is_null() {
            e.set_left_context("");
            return SHANJIE_OK;
        }
        // SAFETY: NUL-terminated per the caller contract.
        match unsafe { CStr::from_ptr(utf8) }.to_str() {
            Ok(s) => {
                e.set_left_context(s);
                SHANJIE_OK
            }
            Err(_) => {
                e.set_left_context("");
                SHANJIE_ERR_INVALID
            }
        }
    })
}

/// # Safety
/// `engine` is NULL or a live handle.
#[no_mangle]
pub unsafe extern "C" fn shanjie_engine_set_learning(engine: *mut ShanjieEngine, enabled: u32) -> i32 {
    guard(|| {
        if engine.is_null() {
            return SHANJIE_ERR_NULL;
        }
        if enabled > 1 {
            return SHANJIE_ERR_INVALID;
        }
        // SAFETY: live handle, single-threaded use (§6).
        unsafe { &mut (*engine).0 }.set_learning(enabled == 1);
        SHANJIE_OK
    })
}

/// sw. `enabled` 0 or 1 (default 1): whether demote.tsv applies. Recomputes the composition and returns
/// its snapshot like `set_profile`; any other value changes nothing and returns 2.
///
/// # Safety
/// `engine` is NULL or a live handle; `out` is NULL or valid for one pointer write.
#[no_mangle]
pub unsafe extern "C" fn shanjie_engine_set_demote(
    engine: *mut ShanjieEngine,
    enabled: u32,
    out: *mut *mut ShanjieOutput,
) -> i32 {
    let rc = guard(|| {
        // SAFETY: forwarded caller contract.
        if !unsafe { clear_out(out) } || engine.is_null() {
            return SHANJIE_ERR_NULL;
        }
        if enabled > 1 {
            return SHANJIE_ERR_INVALID;
        }
        // SAFETY: live handle, single-threaded use (§6).
        let e = unsafe { &mut (*engine).0 };
        match e.set_demote(enabled == 1) {
            Ok(o) => {
                #[cfg(test)]
                tests::inject(&o);
                // SAFETY: `out` checked non-NULL above.
                unsafe { emit(o, out) }
            }
            Err(_) => SHANJIE_ERR_INTERNAL,
        }
    });
    if rc == SHANJIE_ERR_INTERNAL {
        // SAFETY: forwarded caller contract.
        unsafe { discard(engine) };
    }
    rc
}

/// V3 engine contract section 10.5. `enabled` 0 or 1 (default 1): whether the prediction row is computed. Returns the
/// snapshot like `set_demote`; any other value changes nothing and returns 2.
///
/// # Safety
/// `engine` is NULL or a live handle; `out` is NULL or valid for one pointer write.
#[no_mangle]
pub unsafe extern "C" fn shanjie_engine_set_prediction(
    engine: *mut ShanjieEngine,
    enabled: u32,
    out: *mut *mut ShanjieOutput,
) -> i32 {
    let rc = guard(|| {
        // SAFETY: forwarded caller contract.
        if !unsafe { clear_out(out) } || engine.is_null() {
            return SHANJIE_ERR_NULL;
        }
        if enabled > 1 {
            return SHANJIE_ERR_INVALID;
        }
        // SAFETY: live handle, single-threaded use (§6).
        let e = unsafe { &mut (*engine).0 };
        match e.set_prediction(enabled == 1) {
            Ok(o) => {
                #[cfg(test)]
                tests::inject(&o);
                // SAFETY: `out` checked non-NULL above.
                unsafe { emit(o, out) }
            }
            Err(_) => SHANJIE_ERR_INTERNAL,
        }
    });
    if rc == SHANJIE_ERR_INTERNAL {
        // SAFETY: forwarded caller contract.
        unsafe { discard(engine) };
    }
    rc
}

/// Candidate-vertical contract section 2.2. `enabled` 0 or 1 (default 0): the orientation of the candidate windows that
/// open from now on. Returns the snapshot like `set_prediction`: an open candidate window keeps its orientation, a
/// prediction row on screen is re-rendered in the new one (`candidate_vertical` 2 or 0); any other value changes nothing
/// and returns 2.
///
/// # Safety
/// `engine` is NULL or a live handle; `out` is NULL or valid for one pointer write.
#[no_mangle]
pub unsafe extern "C" fn shanjie_engine_set_candidate_vertical(
    engine: *mut ShanjieEngine,
    enabled: u32,
    out: *mut *mut ShanjieOutput,
) -> i32 {
    let rc = guard(|| {
        // SAFETY: forwarded caller contract.
        if !unsafe { clear_out(out) } || engine.is_null() {
            return SHANJIE_ERR_NULL;
        }
        if enabled > 1 {
            return SHANJIE_ERR_INVALID;
        }
        // SAFETY: live handle, single-threaded use (§6).
        let e = unsafe { &mut (*engine).0 };
        match e.set_candidate_vertical(enabled == 1) {
            // SAFETY: `out` checked non-NULL above.
            Ok(o) => unsafe { emit(o, out) },
            Err(_) => SHANJIE_ERR_INTERNAL,
        }
    });
    if rc == SHANJIE_ERR_INTERNAL {
        // SAFETY: forwarded caller contract.
        unsafe { discard(engine) };
    }
    rc
}

/// V3 engine contract section 12.1. `enabled` 0 or 1 (default 0): whether a symbol in an occupied column opens a new
/// unfinished unit. Returns the snapshot like `set_prediction`; any other value changes nothing and returns 2.
///
/// # Safety
/// `engine` is NULL or a live handle; `out` is NULL or valid for one pointer write.
#[no_mangle]
pub unsafe extern "C" fn shanjie_engine_set_abbreviation(
    engine: *mut ShanjieEngine,
    enabled: u32,
    out: *mut *mut ShanjieOutput,
) -> i32 {
    let rc = guard(|| {
        // SAFETY: forwarded caller contract.
        if !unsafe { clear_out(out) } || engine.is_null() {
            return SHANJIE_ERR_NULL;
        }
        if enabled > 1 {
            return SHANJIE_ERR_INVALID;
        }
        // SAFETY: live handle, single-threaded use (§6).
        let e = unsafe { &mut (*engine).0 };
        match e.set_abbreviation(enabled == 1) {
            Ok(o) => {
                #[cfg(test)]
                tests::inject(&o);
                // SAFETY: `out` checked non-NULL above.
                unsafe { emit(o, out) }
            }
            Err(_) => SHANJIE_ERR_INTERNAL,
        }
    });
    if rc == SHANJIE_ERR_INTERNAL {
        // SAFETY: forwarded caller contract.
        unsafe { discard(engine) };
    }
    rc
}

/// zhen-mixed section 3.1, as zhen-revert changes it. `enabled` 0 or 1 (default 0): English mixing. On, keys that cannot be a Zhuyin
/// syllable revert to the raw keys as an English run in the composition (s3a rules 9 and 10; switches `set_english_overwrite` and
/// `set_english_key_order`); only with the standard layout and the abbreviation composer off.
/// Returns the snapshot like `set_abbreviation`; any other value changes nothing and returns 2.
///
/// # Safety
/// `engine` is NULL or a live handle; `out` is NULL or valid for one pointer write.
#[no_mangle]
pub unsafe extern "C" fn shanjie_engine_set_english(
    engine: *mut ShanjieEngine,
    enabled: u32,
    out: *mut *mut ShanjieOutput,
) -> i32 {
    let rc = guard(|| {
        // SAFETY: forwarded caller contract.
        if !unsafe { clear_out(out) } || engine.is_null() {
            return SHANJIE_ERR_NULL;
        }
        if enabled > 1 {
            return SHANJIE_ERR_INVALID;
        }
        // SAFETY: live handle, single-threaded use (§6).
        let e = unsafe { &mut (*engine).0 };
        match e.set_english(enabled == 1) {
            // SAFETY: `out` checked non-NULL above.
            Ok(o) => unsafe { emit(o, out) },
            Err(_) => SHANJIE_ERR_INTERNAL,
        }
    });
    if rc == SHANJIE_ERR_INTERNAL {
        // SAFETY: forwarded caller contract.
        unsafe { discard(engine) };
    }
    rc
}

/// zhen-revert section 3. `enabled` 0 or 1 (default 1): whether a Zhuyin key over a symbol already in its column reverts the
/// unfinished syllable to its raw keys as English (`o k` is `ok`). Returns the snapshot like `set_english`; any other value changes
/// nothing and returns 2. Only matters with `set_english(1)`.
///
/// # Safety
/// `engine` is NULL or a live handle; `out` is NULL or valid for one pointer write.
#[no_mangle]
pub unsafe extern "C" fn shanjie_engine_set_english_overwrite(
    engine: *mut ShanjieEngine,
    enabled: u32,
    out: *mut *mut ShanjieOutput,
) -> i32 {
    let rc = guard(|| {
        // SAFETY: forwarded caller contract.
        if !unsafe { clear_out(out) } || engine.is_null() {
            return SHANJIE_ERR_NULL;
        }
        if enabled > 1 {
            return SHANJIE_ERR_INVALID;
        }
        // SAFETY: live handle, single-threaded use (§6).
        let e = unsafe { &mut (*engine).0 };
        match e.set_english_overwrite(enabled == 1) {
            // SAFETY: `out` checked non-NULL above.
            Ok(o) => unsafe { emit(o, out) },
            Err(_) => SHANJIE_ERR_INTERNAL,
        }
    });
    if rc == SHANJIE_ERR_INTERNAL {
        // SAFETY: forwarded caller contract.
        unsafe { discard(engine) };
    }
    rc
}

/// Fuzzy input (docs/contracts/fuzzy-input.md section 2.1): `flags` bits 0-3 are the sound pairs, bit 4 the adjacent-key slips; any
/// other bit is 2 with the state unchanged. Codes and snapshot as `shanjie_engine_set_english`.
///
/// # Safety
/// `engine` is NULL or a live handle; `out` is NULL or valid for one pointer write.
#[no_mangle]
pub unsafe extern "C" fn shanjie_engine_set_fuzzy(
    engine: *mut ShanjieEngine,
    flags: u32,
    out: *mut *mut ShanjieOutput,
) -> i32 {
    let rc = guard(|| {
        // SAFETY: forwarded caller contract.
        if !unsafe { clear_out(out) } || engine.is_null() {
            return SHANJIE_ERR_NULL;
        }
        if flags & !crate::fuzzy::FUZZY_ALL != 0 {
            return SHANJIE_ERR_INVALID;
        }
        // SAFETY: live handle, single-threaded use (§6).
        let e = unsafe { &mut (*engine).0 };
        match e.set_fuzzy(flags) {
            // SAFETY: `out` checked non-NULL above.
            Ok(o) => unsafe { emit(o, out) },
            Err(_) => SHANJIE_ERR_INTERNAL,
        }
    });
    if rc == SHANJIE_ERR_INTERNAL {
        // SAFETY: forwarded caller contract.
        unsafe { discard(engine) };
    }
    rc
}

/// zhen-keyorder section 3 as zhen-revert changes it. `enabled` 0 or 1 (default 1): whether a Zhuyin key out of column order, and a
/// tone key that completes a syllable the lexicon lacks, revert to the raw keys as English (`ls` is `ls`, `s 7` is `s7`). Returns the
/// snapshot like `set_english_overwrite`; any other value changes nothing and returns 2. Only matters with `set_english(1)`.
///
/// # Safety
/// `engine` is NULL or a live handle; `out` is NULL or valid for one pointer write.
#[no_mangle]
pub unsafe extern "C" fn shanjie_engine_set_english_key_order(
    engine: *mut ShanjieEngine,
    enabled: u32,
    out: *mut *mut ShanjieOutput,
) -> i32 {
    let rc = guard(|| {
        // SAFETY: forwarded caller contract.
        if !unsafe { clear_out(out) } || engine.is_null() {
            return SHANJIE_ERR_NULL;
        }
        if enabled > 1 {
            return SHANJIE_ERR_INVALID;
        }
        // SAFETY: live handle, single-threaded use (§6).
        let e = unsafe { &mut (*engine).0 };
        match e.set_english_key_order(enabled == 1) {
            // SAFETY: `out` checked non-NULL above.
            Ok(o) => unsafe { emit(o, out) },
            Err(_) => SHANJIE_ERR_INTERNAL,
        }
    });
    if rc == SHANJIE_ERR_INTERNAL {
        // SAFETY: forwarded caller contract.
        unsafe { discard(engine) };
    }
    rc
}

/// shift-toggle section 4: the ASCII state of the shell's per-app Shift toggle. `on` 0 or 1 (2 otherwise, state unchanged). With 1 the
/// open window or row closes, the digit run flushes and the unfinished syllable's keys open the English run; every printable ASCII
/// character after it goes on the run as typed. With 0 the run ends as a token. Returns the snapshot like `set_english` (`commit` may
/// be non-empty); 1 when `engine` or `out` is NULL.
///
/// # Safety
/// `engine` is NULL or a live handle; `out` is NULL or valid for one pointer write.
#[no_mangle]
pub unsafe extern "C" fn shanjie_engine_set_ascii(engine: *mut ShanjieEngine, on: u32, out: *mut *mut ShanjieOutput) -> i32 {
    let rc = guard(|| {
        // SAFETY: forwarded caller contract.
        if !unsafe { clear_out(out) } || engine.is_null() {
            return SHANJIE_ERR_NULL;
        }
        if on > 1 {
            return SHANJIE_ERR_INVALID;
        }
        // SAFETY: live handle, single-threaded use (§6).
        let e = unsafe { &mut (*engine).0 };
        match e.set_ascii(on == 1) {
            // SAFETY: `out` checked non-NULL above.
            Ok(o) => unsafe { emit(o, out) },
            Err(_) => SHANJIE_ERR_INTERNAL,
        }
    });
    if rc == SHANJIE_ERR_INTERNAL {
        // SAFETY: forwarded caller contract.
        unsafe { discard(engine) };
    }
    rc
}

/// # Safety
/// `engine` is NULL or a live handle; `dir` is NULL or a NUL-terminated string.
#[no_mangle]
pub unsafe extern "C" fn shanjie_engine_learning_open(engine: *mut ShanjieEngine, dir: *const c_char) -> i32 {
    guard(|| {
        if engine.is_null() || dir.is_null() {
            return SHANJIE_ERR_NULL;
        }
        // SAFETY: non-NULL, NUL-terminated per the caller contract.
        let Ok(dir) = unsafe { CStr::from_ptr(dir) }.to_str() else {
            return SHANJIE_ERR_INVALID;
        };
        // SAFETY: live handle, single-threaded use (§6).
        match unsafe { &mut (*engine).0 }.learning_open(Path::new(dir)) {
            Ok(_) => SHANJIE_OK,
            Err(_) => SHANJIE_ERR_LOAD,
        }
    })
}

/// # Safety
/// `engine` is NULL or a live handle.
#[no_mangle]
pub unsafe extern "C" fn shanjie_engine_learning_clear(engine: *mut ShanjieEngine) -> i32 {
    guard(|| {
        if engine.is_null() {
            return SHANJIE_ERR_NULL;
        }
        // SAFETY: live handle, single-threaded use (§6).
        match unsafe { &mut (*engine).0 }.learning_clear() {
            Ok(()) => SHANJIE_OK,
            Err(_) => SHANJIE_ERR_LOAD,
        }
    })
}

/// # Safety
/// `engine` is NULL or a live handle; `flags` is NULL or writable.
#[no_mangle]
pub unsafe extern "C" fn shanjie_engine_learning_status(engine: *mut ShanjieEngine, flags: *mut u32) -> i32 {
    guard(|| {
        if engine.is_null() || flags.is_null() {
            return SHANJIE_ERR_NULL;
        }
        // SAFETY: live handle; `flags` non-NULL and writable per the caller contract.
        unsafe { *flags = (*engine).0.learning_status() };
        SHANJIE_OK
    })
}

// S7c-2a (docs/contracts/s7c2a-core-rerank.md section 2.5): the small character model and the two-phase rerank. See
// core/include/shanjie.h for the semantics, ownership and threading rules.

/// # Safety
/// `engine` is NULL or a live handle; `path` is NULL or a NUL-terminated string.
#[no_mangle]
pub unsafe extern "C" fn shanjie_engine_load_tinylm(engine: *mut ShanjieEngine, path: *const c_char) -> i32 {
    let rc = guard(|| {
        if engine.is_null() || path.is_null() {
            return SHANJIE_ERR_NULL;
        }
        // SAFETY: non-NULL, NUL-terminated per the caller contract.
        let Ok(path) = unsafe { CStr::from_ptr(path) }.to_str() else {
            return SHANJIE_ERR_INVALID;
        };
        // SAFETY: live handle, single-threaded use (§6).
        let e = unsafe { &mut (*engine).0 };
        match e.load_tinylm(Path::new(path)) {
            Ok(()) => SHANJIE_OK,
            Err(EngineError::LoadFailed) => SHANJIE_ERR_LOAD,
            Err(EngineError::Internal) => SHANJIE_ERR_INTERNAL,
        }
    });
    if rc == SHANJIE_ERR_INTERNAL {
        // SAFETY: forwarded caller contract.
        unsafe { discard(engine) };
    }
    rc
}

/// # Safety
/// `engine` is NULL or a live handle; `path` is NULL or a NUL-terminated string.
#[no_mangle]
pub unsafe extern "C" fn shanjie_engine_load_colloquial(engine: *mut ShanjieEngine, path: *const c_char) -> i32 {
    let rc = guard(|| {
        if engine.is_null() || path.is_null() {
            return SHANJIE_ERR_NULL;
        }
        // SAFETY: non-NULL, NUL-terminated per the caller contract.
        let Ok(path) = unsafe { CStr::from_ptr(path) }.to_str() else {
            return SHANJIE_ERR_INVALID;
        };
        // SAFETY: live handle, single-threaded use (§6).
        let e = unsafe { &mut (*engine).0 };
        match e.load_colloquial(Path::new(path)) {
            Ok(()) => SHANJIE_OK,
            Err(EngineError::LoadFailed) => SHANJIE_ERR_LOAD,
            Err(EngineError::Internal) => SHANJIE_ERR_INTERNAL,
        }
    });
    if rc == SHANJIE_ERR_INTERNAL {
        // SAFETY: forwarded caller contract.
        unsafe { discard(engine) };
    }
    rc
}

/// # Safety
/// `engine` is NULL or a live handle; `out` is NULL or valid for one pointer write.
#[no_mangle]
pub unsafe extern "C" fn shanjie_engine_set_rerank(engine: *mut ShanjieEngine, enabled: u32, out: *mut *mut ShanjieOutput) -> i32 {
    let rc = guard(|| {
        // SAFETY: forwarded caller contract.
        if !unsafe { clear_out(out) } || engine.is_null() {
            return SHANJIE_ERR_NULL;
        }
        if enabled > 1 {
            return SHANJIE_ERR_INVALID;
        }
        // SAFETY: live handle, single-threaded use (§6).
        let e = unsafe { &mut (*engine).0 };
        match e.set_rerank(enabled == 1) {
            // SAFETY: `out` checked non-NULL above.
            Ok(o) => unsafe { emit(o, out) },
            Err(_) => SHANJIE_ERR_INTERNAL,
        }
    });
    if rc == SHANJIE_ERR_INTERNAL {
        // SAFETY: forwarded caller contract.
        unsafe { discard(engine) };
    }
    rc
}

/// Rerank strength (docs/contracts/rerank-strength.md section 2.2): level 0 conservative, 1 standard (default), 2 aggressive; codes
/// and snapshot like `set_profile`. The out pointer is cleared and the range checked before the engine is touched, so 1 and 2
/// leave the level and the generation as they were.
///
/// # Safety
/// `engine` is NULL or a live handle; `out` is NULL or valid for one pointer write.
#[no_mangle]
pub unsafe extern "C" fn shanjie_engine_set_rerank_level(engine: *mut ShanjieEngine, level: u32, out: *mut *mut ShanjieOutput) -> i32 {
    let rc = guard(|| {
        // SAFETY: forwarded caller contract.
        if !unsafe { clear_out(out) } || engine.is_null() {
            return SHANJIE_ERR_NULL;
        }
        let Some(level) = RerankLevel::from_code(level) else { return SHANJIE_ERR_INVALID };
        // SAFETY: live handle, single-threaded use (§6).
        let e = unsafe { &mut (*engine).0 };
        match e.set_rerank_level(level) {
            Ok(o) => {
                #[cfg(test)]
                tests::inject(&o);
                // SAFETY: `out` checked non-NULL above.
                unsafe { emit(o, out) }
            }
            Err(_) => SHANJIE_ERR_INTERNAL,
        }
    });
    if rc == SHANJIE_ERR_INTERNAL {
        // SAFETY: forwarded caller contract.
        unsafe { discard(engine) };
    }
    rc
}

/// Rerank strength section 2.2 (P2-1: an out parameter, so NULL, a panic's 4 and "no model" stay apart): `*levels` is the mask
/// of `Engine::rerank_levels`. Like `learning_status`, it changes nothing, so a 4 resets nothing.
///
/// # Safety
/// `engine` is NULL or a live handle; `levels` is NULL or writable.
#[no_mangle]
pub unsafe extern "C" fn shanjie_engine_rerank_levels(engine: *mut ShanjieEngine, levels: *mut u32) -> i32 {
    guard(|| {
        if engine.is_null() || levels.is_null() {
            return SHANJIE_ERR_NULL;
        }
        // SAFETY: live handle; `levels` non-NULL and writable per the caller contract.
        unsafe { *levels = (*engine).0.rerank_levels() };
        SHANJIE_OK
    })
}

/// # Safety
/// `engine` is NULL or a live handle; `job` is NULL or valid for one pointer write.
#[no_mangle]
pub unsafe extern "C" fn shanjie_engine_rerank_take(engine: *mut ShanjieEngine, job: *mut *mut ShanjieRerankJob) -> i32 {
    let rc = guard(|| {
        // SAFETY: forwarded caller contract.
        if !unsafe { clear_out(job) } || engine.is_null() {
            return SHANJIE_ERR_NULL;
        }
        // SAFETY: live handle, single-threaded use (§6).
        if let Some(j) = unsafe { &mut (*engine).0 }.take_rerank_job() {
            // SAFETY: `job` checked non-NULL above.
            unsafe { *job = Box::into_raw(Box::new(ShanjieRerankJob { job: j, result: Mutex::new(None) })) };
        }
        SHANJIE_OK
    });
    if rc == SHANJIE_ERR_INTERNAL {
        // SAFETY: forwarded caller contract.
        unsafe { discard(engine) };
    }
    rc
}

/// No engine is touched: a 4 here resets nothing and leaves no result (an earlier run's result is cleared first).
///
/// # Safety
/// `job` is NULL or a job from `shanjie_engine_rerank_take` not yet freed, not in use by another call.
#[no_mangle]
pub unsafe extern "C" fn shanjie_rerank_run(job: *mut ShanjieRerankJob) -> i32 {
    guard(|| {
        if job.is_null() {
            return SHANJIE_ERR_NULL;
        }
        // SAFETY: live job per the caller contract; shared reference only.
        let j = unsafe { &*job };
        // A run that panics leaves no result, not even one of an earlier run of this job.
        *j.result.lock().unwrap_or_else(PoisonError::into_inner) = None;
        #[cfg(test)]
        tests::inject_run(&j.job);
        let r = j.job.run();
        *j.result.lock().unwrap_or_else(PoisonError::into_inner) = Some(r);
        SHANJIE_OK
    })
}

/// # Safety
/// `engine` is NULL or a live handle; `job` is NULL or a job from `shanjie_engine_rerank_take` not yet freed, not in use by
/// another call; `out` is NULL or valid for one pointer write.
#[no_mangle]
pub unsafe extern "C" fn shanjie_engine_rerank_apply(
    engine: *mut ShanjieEngine,
    job: *mut ShanjieRerankJob,
    out: *mut *mut ShanjieOutput,
) -> i32 {
    let rc = guard(|| {
        // SAFETY: forwarded caller contract.
        if !unsafe { clear_out(out) } || engine.is_null() || job.is_null() {
            return SHANJIE_ERR_NULL;
        }
        // SAFETY: live job per the caller contract; shared reference only. Taking the result makes a second apply a no-op.
        let Some(r) = unsafe { &*job }.result.lock().unwrap_or_else(PoisonError::into_inner).take() else {
            return SHANJIE_NO_CHANGE;
        };
        // SAFETY: live handle, single-threaded use (§6).
        let e = unsafe { &mut (*engine).0 };
        match e.apply_rerank(r) {
            Ok(Some(o)) => {
                #[cfg(test)]
                tests::inject(&o);
                // SAFETY: `out` checked non-NULL above.
                unsafe { emit(o, out) }
            }
            Ok(None) => SHANJIE_NO_CHANGE,
            Err(_) => SHANJIE_ERR_INTERNAL,
        }
    });
    if rc == SHANJIE_ERR_INTERNAL {
        // SAFETY: forwarded caller contract.
        unsafe { discard(engine) };
    }
    rc
}

/// # Safety
/// `job` is NULL or a job from `shanjie_engine_rerank_take` not yet freed, not in use by another call.
#[no_mangle]
pub unsafe extern "C" fn shanjie_rerank_free(job: *mut ShanjieRerankJob) {
    guard(|| {
        if !job.is_null() {
            // SAFETY: allocated by `Box::into_raw` in `shanjie_engine_rerank_take`, freed once (header).
            drop(unsafe { Box::from_raw(job) });
        }
        SHANJIE_OK
    });
}

/// # Safety
/// `output` is NULL or an output from this library not yet freed.
#[no_mangle]
pub unsafe extern "C" fn shanjie_output_free(output: *mut ShanjieOutput) {
    guard(|| {
        if !output.is_null() {
            // SAFETY: every output is a boxed `OwnedOutput` (see `to_c`), freed once (§6).
            drop(unsafe { Box::from_raw(output.cast::<OwnedOutput>()) });
        }
        SHANJIE_OK
    });
}

#[cfg(test)]
mod tests {
    //! §7.3. Every test that calls an export runs in a child process: the exports install a
    //! process-wide silent panic hook, which would otherwise hide panic messages of the other
    //! unit tests in this binary.
    use super::*;
    use crate::eval::{parse_rows, usable};
    use crate::{decode_beam, NoLearning, BEAM_S1};
    use std::cell::{Cell, RefCell};
    use std::path::PathBuf;
    use std::process::Command;

    /// Selects which child test runs (value = the child test's name).
    const CHILD_ENV: &str = "SHANJIE_FFI_CHILD";
    /// Positive control: leave the default panic hook in place.
    pub(super) const DEFAULT_HOOK_ENV: &str = "SHANJIE_FFI_DEFAULT_HOOK";
    /// Decodes from the tiny lexicon below; typed as keys `v u p space 1 p space` (standard).
    const MARKER: &str = "鑫犇";

    thread_local! {
        static ARMED: Cell<bool> = const { Cell::new(false) };
        static PAYLOAD: RefCell<String> = const { RefCell::new(String::new()) };
    }

    /// Injection point in `shanjie_engine_key`: when armed, panic once with the preedit in the payload.
    pub(super) fn inject(o: &Output) {
        if ARMED.with(|a| a.replace(false)) {
            let msg = format!("injected panic: {}", o.preedit);
            PAYLOAD.with(|p| *p.borrow_mut() = msg.clone());
            std::panic::panic_any(msg);
        }
    }

    /// Injection point in `shanjie_rerank_run`: when armed, panic once with the job's candidate text in the payload.
    pub(super) fn inject_run(j: &RerankJob) {
        if ARMED.with(|a| a.replace(false)) {
            let msg = format!("injected panic: {}", j.texts());
            PAYLOAD.with(|p| *p.borrow_mut() = msg.clone());
            std::panic::panic_any(msg);
        }
    }

    fn is_child(name: &str) -> bool {
        std::env::var(CHILD_ENV).is_ok_and(|v| v == name)
    }

    fn run_child(name: &str, default_hook: bool) -> (bool, String, String) {
        let mut c = Command::new(std::env::current_exe().unwrap());
        c.args(["--exact", &format!("ffi::tests::{name}"), "--nocapture"])
            .env(CHILD_ENV, name)
            .env_remove(DEFAULT_HOOK_ENV);
        if default_hook {
            c.env(DEFAULT_HOOK_ENV, "1");
        }
        let o = c.output().unwrap();
        (o.status.success(), String::from_utf8_lossy(&o.stdout).into(), String::from_utf8_lossy(&o.stderr).into())
    }

    fn key(kind: u32, ch: char) -> ShanjieKey {
        ShanjieKey { kind, ch: ch as u32, modifiers: 0 }
    }
    const CHAR: u32 = 1;
    const SPACE: u32 = 2;
    const ENTER: u32 = 3;
    const BACKSPACE: u32 = 4;
    const ESC: u32 = 6;
    /// Leaves a composition intact, so an empty preedit after it proves the reset.
    const LEFT: u32 = 7;

    /// A data dir with a tiny base lexicon and an empty overlay.
    fn tiny_dir(tag: &str, base: &[u8]) -> PathBuf {
        let d = std::env::temp_dir().join(format!("shanjie-ffi-{}-{tag}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        std::fs::write(d.join("mcbpmf-data.txt"), base).unwrap();
        std::fs::write(d.join("overlay-add.tsv"), "").unwrap();
        std::fs::write(d.join("sandhi-add.tsv"), "").unwrap();
        std::fs::write(d.join("names-add.tsv"), "").unwrap();
        std::fs::write(d.join("demote.tsv"), "").unwrap();
        d
    }

    fn new_engine(dir: &Path, layout: u32) -> *mut ShanjieEngine {
        let c = CString::new(dir.to_str().unwrap()).unwrap();
        let mut e = ptr::null_mut();
        assert!(unsafe { shanjie_engine_new(c.as_ptr(), layout, &mut e) } == 0, "engine_new");
        e
    }

    /// Sends one key; returns (code, preedit, commit) and frees the output.
    fn send(e: *mut ShanjieEngine, k: ShanjieKey) -> (i32, String, String) {
        let mut o = ptr::null_mut();
        let rc = unsafe { shanjie_engine_key(e, k, &mut o) };
        if o.is_null() {
            return (rc, String::new(), String::new());
        }
        // SAFETY: test-only read of a live output.
        let r = unsafe {
            let s = |p| CStr::from_ptr(p).to_str().unwrap().to_string();
            (rc, s((*o).preedit), s((*o).commit))
        };
        unsafe { shanjie_output_free(o) };
        r
    }

    // ---- R2 panic + positive control ----

    #[test]
    fn child_panic() {
        if !is_child("child_panic") {
            return;
        }
        let dir = tiny_dir("panic", "ㄒㄧㄣ 鑫 -1.0\nㄅㄣ 犇 -1.0\n".as_bytes());
        let e = new_engine(&dir, 0);
        // context-return section 2: the English word of the left text is held until the reset after code 4.
        assert!(set_left(e, "Joe") == 0 && unsafe { &(*e).0 }.left_full_chars() == 3, "left text held");
        for c in ['v', 'u', 'p'] {
            assert!(send(e, key(CHAR, c)).0 == 0, "typing");
        }
        assert!(send(e, key(SPACE, '\0')).0 == 0, "typing");
        for c in ['1', 'p'] {
            assert!(send(e, key(CHAR, c)).0 == 0, "typing");
        }
        ARMED.with(|a| a.set(true));
        let mut o: *mut ShanjieOutput = ptr::NonNull::dangling().as_ptr();
        let rc = unsafe { shanjie_engine_key(e, key(SPACE, '\0'), &mut o) };
        assert!(rc == 4, "panic maps to code 4");
        assert!(o.is_null(), "out is NULL after a panic");
        assert!(PAYLOAD.with(|p| p.borrow().contains(MARKER)), "payload carries the marker");
        assert!(unsafe { &(*e).0 }.left_full_chars() == 0, "the left text went with the reset after code 4");
        let (rc, preedit, _) = send(e, key(LEFT, '\0'));
        assert!(rc == 0 && preedit.is_empty(), "engine was reset after code 4");
        unsafe { shanjie_engine_free(e) };
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn r2_panic_marker_never_reaches_stdout_or_stderr() {
        let (ok, out, err) = run_child("child_panic", false);
        assert!(ok, "quiet child failed");
        assert!(!out.contains(MARKER) && !err.contains(MARKER), "marker leaked with the silent hook");
        assert!(out.contains("1 passed"), "child test actually ran");
        // Positive control: the same child with the default hook must show the marker.
        let (ok, out, err) = run_child("child_panic", true);
        assert!(ok, "default-hook child failed");
        assert!(out.contains("1 passed"), "default-hook child actually ran");
        assert!(err.contains(MARKER), "positive control: default hook did not print the marker");
    }

    // ---- C-ABI replay of the first 20 rows of §7.2 ----

    fn root() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
    }

    fn dev_texts() -> Vec<String> {
        let mut files: Vec<PathBuf> = std::fs::read_dir(root().join("eval/dev"))
            .unwrap()
            .filter_map(|e| e.ok().map(|e| e.path()))
            .filter(|p| p.extension().is_some_and(|x| x == "txt"))
            .collect();
        files.sort();
        files.iter().map(|f| std::fs::read_to_string(f).unwrap()).collect()
    }

    fn keys_of(layout: Layout, syl: &str) -> Vec<ShanjieKey> {
        let mut v = Vec::new();
        let mut toned = false;
        for c in syl.chars() {
            match layout.key_of_tone(c) {
                Some(tk) => {
                    v.push(key(CHAR, tk));
                    toned = true;
                }
                None => v.push(key(CHAR, layout.key_of_symbol(c).expect("symbol has a key"))),
            }
        }
        if !toned {
            v.push(key(SPACE, '\0'));
        }
        v
    }

    #[test]
    fn child_replay() {
        if !is_child("child_replay") {
            return;
        }
        let data = root().join("data/lexicon");
        let lex = crate::engine::load_lexicon(&data).unwrap();
        let mut rows = Vec::new();
        for t in dev_texts() {
            rows.extend(parse_rows(&t).unwrap());
        }
        let mut rows = usable(&lex, rows);
        rows.truncate(20);
        assert!(rows.len() == 20, "20 usable rows");
        for (code, layout) in [(0, Layout::Standard), (1, Layout::Eten)] {
            let e = new_engine(&data, code);
            for (i, r) in rows.iter().enumerate() {
                let syls = match &r.reading {
                    Some(s) => s.clone(),
                    None => lex.to_syllables(&r.sent).expect("usable row"),
                };
                let want = decode_beam(&lex, &syls, &mut NoLearning, BEAM_S1).unwrap()[0].1.concat();
                let mut keys: Vec<ShanjieKey> = syls.iter().flat_map(|s| keys_of(layout, s)).collect();
                keys.push(key(ENTER, '\0'));
                let mut got = String::new();
                for k in keys {
                    let (rc, _, commit) = send(e, k);
                    assert!(rc == 0, "key code at row {}", i + 1);
                    got.push_str(&commit);
                }
                assert!(got == want, "replay mismatch at row {}", i + 1);
            }
            unsafe { shanjie_engine_free(e) };
        }
    }

    #[test]
    fn c_abi_replay_20_rows_prints_only_harness_lines() {
        let (ok, out, err) = run_child("child_replay", false);
        assert!(ok, "replay child failed");
        let all = format!("{out}{err}");
        for line in all.lines() {
            let harness = line.is_empty()
                || line == "running 1 test"
                || line == "test ffi::tests::child_replay ... ok"
                || line.starts_with("test result: ok. 1 passed; 0 failed;");
            assert!(harness, "child printed a non-harness line");
        }
        assert!(all.contains("test ffi::tests::child_replay ... ok"), "child test actually ran");
        assert!(all.is_ascii(), "child printed non-ASCII text");
        // Superset of the 20 rows: every dev sentence and confirmed reading.
        for t in dev_texts() {
            for r in parse_rows(&t).unwrap() {
                assert!(!all.contains(&r.sent), "sentence leaked");
                if let Some(s) = &r.reading {
                    assert!(!all.contains(&s.join(" ")) && !all.contains(&s.concat()), "reading leaked");
                }
            }
        }
    }

    // ---- NULL frees, error codes, ownership ----

    /// Non-NULL garbage to prove the export overwrites `*out`.
    fn sentinel<T>() -> *mut T {
        ptr::NonNull::dangling().as_ptr()
    }

    #[test]
    fn child_errors() {
        if !is_child("child_errors") {
            return;
        }
        unsafe {
            shanjie_engine_free(ptr::null_mut());
            shanjie_output_free(ptr::null_mut());
        }
        // ㄋㄧˇ decodes to a NUL character: CString creation fails -> code 4.
        let dir = tiny_dir("errors", b"\xe3\x84\x8b\xe3\x84\xa7\xcb\x87 \x00 -1.0\n\xe3\x84\x8f\xe3\x84\xa0\xcb\x87 \xe5\xa5\xbd -1.0\n");
        let dir_c = CString::new(dir.to_str().unwrap()).unwrap();
        let missing = CString::new(dir.join("missing").to_str().unwrap()).unwrap();
        let bad_utf8 = CString::new(vec![0xffu8, 0xfe]).unwrap();

        // engine_new
        let mut e: *mut ShanjieEngine = sentinel();
        assert!(unsafe { shanjie_engine_new(dir_c.as_ptr(), 0, ptr::null_mut()) } == 1, "new out NULL");
        assert!(unsafe { shanjie_engine_new(ptr::null(), 0, &mut e) } == 1 && e.is_null(), "new dir NULL");
        e = sentinel();
        assert!(unsafe { shanjie_engine_new(dir_c.as_ptr(), 2, &mut e) } == 2 && e.is_null(), "new layout");
        e = sentinel();
        assert!(unsafe { shanjie_engine_new(bad_utf8.as_ptr(), 0, &mut e) } == 2 && e.is_null(), "new non-UTF-8");
        e = sentinel();
        assert!(unsafe { shanjie_engine_new(missing.as_ptr(), 0, &mut e) } == 3 && e.is_null(), "new missing dir");
        std::fs::remove_file(dir.join("overlay-add.tsv")).unwrap();
        e = sentinel();
        assert!(unsafe { shanjie_engine_new(dir_c.as_ptr(), 0, &mut e) } == 3 && e.is_null(), "new missing overlay");
        std::fs::write(dir.join("overlay-add.tsv"), "").unwrap();
        std::fs::remove_file(dir.join("sandhi-add.tsv")).unwrap();
        e = sentinel();
        assert!(unsafe { shanjie_engine_new(dir_c.as_ptr(), 0, &mut e) } == 3 && e.is_null(), "new missing sandhi-add.tsv");
        std::fs::write(dir.join("sandhi-add.tsv"), "").unwrap();
        // model-v6 section 7 P2-3: the names layer is required like overlay-add.tsv, never silently skipped.
        std::fs::remove_file(dir.join("names-add.tsv")).unwrap();
        e = sentinel();
        assert!(unsafe { shanjie_engine_new(dir_c.as_ptr(), 0, &mut e) } == 3 && e.is_null(), "new missing names-add.tsv");
        std::fs::write(dir.join("names-add.tsv"), "").unwrap();
        // demote.tsv is required like the other lexicon files, and a malformed one fails the same way.
        std::fs::remove_file(dir.join("demote.tsv")).unwrap();
        e = sentinel();
        assert!(unsafe { shanjie_engine_new(dir_c.as_ptr(), 0, &mut e) } == 3 && e.is_null(), "new missing demote.tsv");
        // Malformed rows, and a well-formed row that names no lexicon entry, fail the same way.
        for bad in [
            "a\tb\t2.0\treading\n",
            "ㄍ\t睪\t0\treading\tx\n",
            "ㄍ\t睪\tnan\treading\tx\n",
            "ㄍ\t睪\t1\treading\tx\nㄍ\t睪\t2\treading\tx\n",
            "ㄍ\t睪\t1\treading\tx\n",
        ] {
            std::fs::write(dir.join("demote.tsv"), bad).unwrap();
            e = sentinel();
            assert!(unsafe { shanjie_engine_new(dir_c.as_ptr(), 0, &mut e) } == 3 && e.is_null(), "new bad demote.tsv");
        }
        std::fs::write(dir.join("demote.tsv"), "").unwrap();
        let e = new_engine(&dir, 0);

        // engine_set_demote (contract sw-sensitive-demote section 4 (d)): codes like set_profile.
        let mut o: *mut ShanjieOutput = sentinel();
        assert!(unsafe { shanjie_engine_set_demote(e, 1, ptr::null_mut()) } == 1, "set_demote out NULL");
        assert!(unsafe { shanjie_engine_set_demote(ptr::null_mut(), 1, &mut o) } == 1 && o.is_null(), "set_demote engine NULL");
        for v in [2, u32::MAX] {
            o = sentinel();
            assert!(unsafe { shanjie_engine_set_demote(e, v, &mut o) } == 2 && o.is_null(), "set_demote out of range");
        }
        for v in [0, 1] {
            o = sentinel();
            assert!(unsafe { shanjie_engine_set_demote(e, v, &mut o) } == 0 && !o.is_null(), "set_demote 0/1");
            unsafe { shanjie_output_free(o) };
        }

        // engine_set_prediction (V3 engine contract section 10.5): codes like set_demote.
        o = sentinel();
        assert!(unsafe { shanjie_engine_set_prediction(e, 1, ptr::null_mut()) } == 1, "set_prediction out NULL");
        assert!(unsafe { shanjie_engine_set_prediction(ptr::null_mut(), 1, &mut o) } == 1 && o.is_null(), "set_prediction engine NULL");
        for v in [2, u32::MAX] {
            o = sentinel();
            assert!(unsafe { shanjie_engine_set_prediction(e, v, &mut o) } == 2 && o.is_null(), "set_prediction out of range");
        }
        for v in [0, 1] {
            o = sentinel();
            assert!(unsafe { shanjie_engine_set_prediction(e, v, &mut o) } == 0 && !o.is_null(), "set_prediction 0/1");
            unsafe { shanjie_output_free(o) };
        }

        // engine_set_abbreviation (V3 engine contract section 12.1): codes like set_prediction.
        o = sentinel();
        assert!(unsafe { shanjie_engine_set_abbreviation(e, 1, ptr::null_mut()) } == 1, "set_abbreviation out NULL");
        assert!(unsafe { shanjie_engine_set_abbreviation(ptr::null_mut(), 1, &mut o) } == 1 && o.is_null(), "set_abbreviation engine NULL");
        for v in [2, u32::MAX] {
            o = sentinel();
            assert!(unsafe { shanjie_engine_set_abbreviation(e, v, &mut o) } == 2 && o.is_null(), "set_abbreviation out of range");
        }
        for v in [0, 1] {
            o = sentinel();
            assert!(unsafe { shanjie_engine_set_abbreviation(e, v, &mut o) } == 0 && !o.is_null(), "set_abbreviation 0/1");
            unsafe { shanjie_output_free(o) };
        }
        // On (the loop ended with 1), but this engine has no model and so no row to resolve units with: ㄋ then ㄔ replace.
        send(e, key(CHAR, 's'));
        assert!(send(e, key(CHAR, 't')).1.chars().count() == 1, "no model: one unit");
        assert!(send(e, key(ESC, '\0')).0 == 0, "esc");

        // engine_key
        o = sentinel();
        assert!(unsafe { shanjie_engine_key(e, key(ESC, '\0'), ptr::null_mut()) } == 1, "key out NULL");
        assert!(unsafe { shanjie_engine_key(ptr::null_mut(), key(ESC, '\0'), &mut o) } == 1 && o.is_null(), "key engine NULL");
        for (kind, ch) in [(0, 0x61), (16, 0x61), (CHAR, 0xD800), (CHAR, 0x110000)] {
            o = sentinel();
            let k = ShanjieKey { kind, ch, modifiers: 0 };
            assert!(unsafe { shanjie_engine_key(e, k, &mut o) } == 2 && o.is_null(), "key invalid");
        }
        // `ch` is ignored for non-CHAR kinds.
        assert!(send(e, ShanjieKey { kind: ESC, ch: 0xD800, modifiers: 0 }).0 == 0, "non-CHAR ch ignored");
        // Interior NUL -> 4, NULL out, engine discarded.
        for c in ['s', 'u'] {
            assert!(send(e, key(CHAR, c)).0 == 0, "typing");
        }
        o = sentinel();
        assert!(unsafe { shanjie_engine_key(e, key(CHAR, '3'), &mut o) } == 4 && o.is_null(), "interior NUL");
        let (rc, preedit, _) = send(e, key(LEFT, '\0'));
        assert!(rc == 0 && preedit.is_empty(), "reset after code 4");

        // engine_reset
        o = sentinel();
        assert!(unsafe { shanjie_engine_reset(e, 0, ptr::null_mut()) } == 1, "reset out NULL");
        assert!(unsafe { shanjie_engine_reset(ptr::null_mut(), 0, &mut o) } == 1 && o.is_null(), "reset engine NULL");
        o = sentinel();
        assert!(unsafe { shanjie_engine_reset(e, 2, &mut o) } == 2 && o.is_null(), "reset mode");

        // Ownership: an output outlives the engine; candidates NULL when closed.
        for c in ['c', 'l', '3'] {
            assert!(send(e, key(CHAR, c)).0 == 0, "typing");
        }
        o = ptr::null_mut();
        assert!(unsafe { shanjie_engine_reset(e, 0, &mut o) } == 0, "reset commit");
        unsafe { shanjie_engine_free(e) };
        // SAFETY: test-only read of a live output after the engine is gone.
        unsafe {
            assert!(CStr::from_ptr((*o).commit).to_bytes() == "好".as_bytes(), "commit survives engine_free");
            assert!(CStr::from_ptr((*o).preedit).to_bytes().is_empty(), "preedit empty after reset");
            assert!((*o).candidate_count == 0 && (*o).candidates.is_null() && (*o).candidate_selected == -1, "closed");
            shanjie_output_free(o);
        }
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn error_codes_and_null_frees() {
        let (ok, out, _) = run_child("child_errors", false);
        assert!(ok, "error-code child failed");
        assert!(out.contains("1 passed"), "child test actually ran");
    }

    // ---- s3b2 8.2: expand + pick ----

    /// Reads the candidates and the grid fields of a live output.
    fn read_out(o: *mut ShanjieOutput) -> (Vec<String>, i32, u32, u32, u32, String) {
        // SAFETY: test-only read of a live output.
        unsafe {
            let n = (*o).candidate_count as usize;
            let c = (0..n).map(|i| CStr::from_ptr(*(*o).candidates.add(i)).to_str().unwrap().to_string()).collect();
            let p = CStr::from_ptr((*o).preedit).to_str().unwrap().to_string();
            (c, (*o).candidate_selected, (*o).candidate_columns, (*o).candidate_first, (*o).candidate_total, p)
        }
    }

    #[test]
    fn child_pick() {
        if !is_child("child_pick") {
            return;
        }
        // 60 one-character candidates for ㄋㄧˇ: more than the 45 the grid shows.
        let base: String = (0..60).map(|i| format!("ㄋㄧˇ {} -1.0\n", char::from_u32(0x4E00 + i).unwrap())).collect();
        let dir = tiny_dir("pick", base.as_bytes());
        let e = new_engine(&dir, 0);
        let send_out = |k: ShanjieKey| {
            let mut o = ptr::null_mut();
            assert!(unsafe { shanjie_engine_key(e, k, &mut o) } == 0);
            let r = read_out(o);
            unsafe { shanjie_output_free(o) };
            r
        };
        let pick = |i: u32| {
            let mut o: *mut ShanjieOutput = sentinel();
            let rc = unsafe { shanjie_engine_pick(e, i, &mut o) };
            if rc != 0 {
                assert!(o.is_null(), "*out NULL on error");
                return (rc, None);
            }
            let r = read_out(o);
            unsafe { shanjie_output_free(o) };
            (rc, Some(r))
        };
        for c in ['s', 'u', '3'] {
            send(e, key(CHAR, c));
        }
        assert!(pick(0).0 == 2, "closed");
        let collapsed = send_out(key(SPACE, '\0'));
        assert!(collapsed.0.len() == 9 && collapsed.2 == 0 && collapsed.3 == 0 && collapsed.4 == 60);
        let grid = send_out(key(10, '\0'));
        assert!(grid.0.len() == 45 && grid.2 == 9 && grid.3 == 0 && grid.4 == 60 && grid.1 == 0);
        assert!(pick(45).0 == 2 && pick(u32::MAX).0 == 2, "outside the output, state unchanged");
        let mut o: *mut ShanjieOutput = sentinel();
        assert!(unsafe { shanjie_engine_pick(ptr::null_mut(), 0, &mut o) } == 1 && o.is_null(), "engine NULL");
        assert!(unsafe { shanjie_engine_pick(e, 0, ptr::null_mut()) } == 1, "out NULL");
        // five rows down: row 5, top row 1
        let mut last = grid;
        for _ in 0..5 {
            last = send_out(key(10, '\0'));
        }
        assert!(last.3 == 9 && last.1 == 36 && last.4 == 60 && last.0.len() == 45);
        let want = last.0[3].clone(); // candidate_first + 3 = list[12]
        let (rc, r) = pick(3);
        let r = r.unwrap();
        assert!(rc == 0 && r.5 == want && r.0.is_empty() && r.1 == -1 && r.2 == 0 && r.4 == 0);
        assert!(pick(0).0 == 2, "closed after a pick");
        unsafe { shanjie_engine_free(e) };
    }

    #[test]
    fn expand_and_pick_through_the_c_abi() {
        let (ok, out, _) = run_child("child_pick", false);
        assert!(ok, "pick child failed");
        assert!(out.contains("1 passed"), "child test actually ran");
    }

    // ---- candidate-vertical contract section 2.2 / 3.1: set_candidate_vertical, kinds 14 / 15, candidate_vertical ----

    const PAGE_UP: u32 = 14;
    const PAGE_DOWN: u32 = 15;
    const DOWN: u32 = 10;

    /// (code, handled, candidates, selected, columns, first, total, candidate_vertical) of one key; frees the output.
    fn vkey(e: *mut ShanjieEngine, k: ShanjieKey) -> (i32, i32, Vec<String>, i32, u32, u32, u32, u32) {
        let mut o = ptr::null_mut();
        let rc = unsafe { shanjie_engine_key(e, k, &mut o) };
        if rc != 0 {
            assert!(o.is_null(), "*out NULL on error");
            return (rc, 0, vec![], 0, 0, 0, 0, 0);
        }
        let (c, sel, cols, first, total, _) = read_out(o);
        // SAFETY: test-only read of a live output.
        let (handled, vert) = unsafe { ((*o).handled, (*o).candidate_vertical) };
        unsafe { shanjie_output_free(o) };
        (rc, handled, c, sel, cols, first, total, vert)
    }

    #[test]
    fn child_vertical() {
        if !is_child("child_vertical") {
            return;
        }
        let base: String = (0..30).map(|i| format!("ㄋㄧˇ {} -1.0\n", char::from_u32(0x4E00 + i).unwrap())).collect();
        let dir = tiny_dir("vertical", base.as_bytes());
        let e = new_engine(&dir, 0);

        // Codes, like set_prediction.
        let mut o: *mut ShanjieOutput = sentinel();
        assert!(unsafe { shanjie_engine_set_candidate_vertical(e, 1, ptr::null_mut()) } == 1, "out NULL");
        assert!(unsafe { shanjie_engine_set_candidate_vertical(ptr::null_mut(), 1, &mut o) } == 1 && o.is_null(), "engine NULL");
        for v in [2, u32::MAX] {
            o = sentinel();
            assert!(unsafe { shanjie_engine_set_candidate_vertical(e, v, &mut o) } == 2 && o.is_null(), "out of range");
        }
        // The failed calls changed nothing: the window still opens horizontal.
        for c in ['s', 'u', '3'] {
            send(e, key(CHAR, c));
        }
        let h = vkey(e, key(SPACE, '\0'));
        assert!(h.1 == 1 && h.2.len() == 9 && h.4 == 0 && h.7 == 0, "default horizontal, candidate_vertical 0");
        send(e, key(ESC, '\0'));
        // 1 turns it on; the snapshot (no window open) is handled 1 with no candidates.
        o = sentinel();
        assert!(unsafe { shanjie_engine_set_candidate_vertical(e, 1, &mut o) } == 0 && !o.is_null(), "set 1");
        let snap = read_out(o);
        assert!(unsafe { (*o).handled == 1 && (*o).candidate_vertical == 0 } && snap.0.is_empty() && snap.1 == -1);
        unsafe { shanjie_output_free(o) };

        // Kinds 14 / 15 are accepted (not code 2); 16 is not. With no vertical window they are not handled.
        for kind in [PAGE_UP, PAGE_DOWN] {
            let r = vkey(e, key(kind, '\0'));
            assert!(r.0 == 0 && r.1 == 0, "no window: accepted, not handled");
        }
        o = sentinel();
        assert!(unsafe { shanjie_engine_key(e, key(16, '\0'), &mut o) } == 2 && o.is_null(), "kind 16");

        // Vertical window: nine rows, columns 0, candidate_vertical 1; Down moves, Page Down turns by nine.
        let v = vkey(e, key(SPACE, '\0'));
        assert!(v.1 == 1 && v.2.len() == 9 && v.3 == 0 && v.4 == 0 && v.5 == 0 && v.6 == 30 && v.7 == 1, "vertical open");
        let d = vkey(e, key(DOWN, '\0'));
        assert!(d.2 == v.2 && d.3 == 1 && d.4 == 0 && d.7 == 1, "Down moves the selection, no grid");
        let p = vkey(e, key(PAGE_DOWN, '\0'));
        assert!(p.1 == 1 && p.3 == 0 && p.5 == 9 && p.2.len() == 9 && p.2 != v.2 && p.7 == 1, "Page Down by nine");
        let u = vkey(e, key(PAGE_UP, '\0'));
        assert!(u.2 == v.2 && u.5 == 0 && u.3 == 0 && u.7 == 1, "Page Up back");
        // Page Down with a modifier is not handled and the window stays.
        let m = vkey(e, ShanjieKey { kind: PAGE_DOWN, ch: 0, modifiers: 8 });
        assert!(m.1 == 0 && m.2 == v.2 && m.7 == 1, "COMMAND+Page Down passes through");
        // set_candidate_vertical(0) while the window is open: the snapshot is the open window, orientation kept.
        o = sentinel();
        assert!(unsafe { shanjie_engine_set_candidate_vertical(e, 0, &mut o) } == 0);
        let snap = read_out(o);
        assert!(unsafe { (*o).candidate_vertical } == 1 && snap.0 == v.2 && snap.2 == 0 && snap.4 == 30, "open window unchanged");
        unsafe { shanjie_output_free(o) };
        let d = vkey(e, key(DOWN, '\0'));
        assert!(d.4 == 0 && d.7 == 1, "still vertical after the switch");
        send(e, key(ESC, '\0'));
        let h = vkey(e, key(SPACE, '\0'));
        assert!(h.2.len() == 9 && h.4 == 0 && h.7 == 0, "reopened horizontal");
        assert!(vkey(e, key(DOWN, '\0')).4 == 9, "horizontal Down expands the grid");
        unsafe { shanjie_engine_free(e) };
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn candidate_vertical_through_the_c_abi() {
        let (ok, out, _) = run_child("child_vertical", false);
        assert!(ok, "vertical child failed");
        assert!(out.contains("1 passed"), "child test actually ran");
    }

    // ---- S2c: load_lm / set_profile ----

    /// SJLM0001 (tools/build_lm.py): vocab `<s> </s> 心 鑫`, N 10, eos 4, D 0.75; one context `<s>`
    /// (total 4: 心 4). With the lexicon of `child_lm` (鑫 -1.0, 心 -4.0 for ㄒㄧㄣ) the single-syllable
    /// path picks 鑫 without LM and with chat (λ 0.5), 心 with formal (λ 0.7).
    fn tiny_lm() -> Vec<u8> {
        let mut b = b"SJLM0001".to_vec();
        b.extend(4u32.to_le_bytes());
        b.extend(10u64.to_le_bytes());
        b.extend(4u64.to_le_bytes());
        b.extend(0.75f64.to_le_bytes());
        for w in ["<s>", "</s>", "心", "鑫"] {
            b.extend((w.len() as u16).to_le_bytes());
            b.extend(w.as_bytes());
        }
        for u in [0u64, 0, 4, 1] {
            b.extend(u.to_le_bytes());
        }
        b.extend(1u32.to_le_bytes()); // contexts
        b.extend(0u32.to_le_bytes()); // ctx id <s>
        b.extend(4u64.to_le_bytes()); // ctx total
        for o in [0u32, 1] {
            b.extend(o.to_le_bytes());
        }
        b.extend(1u32.to_le_bytes()); // entries
        b.extend(2u32.to_le_bytes()); // next 心
        b.extend(4u32.to_le_bytes()); // count
        b
    }

    /// Types ㄒㄧㄣ (`v u p space`); returns the preedit.
    fn type_xin(e: *mut ShanjieEngine) -> String {
        for c in ['v', 'u', 'p'] {
            assert!(send(e, key(CHAR, c)).0 == 0, "typing");
        }
        let (rc, preedit, _) = send(e, key(SPACE, '\0'));
        assert!(rc == 0, "typing");
        preedit
    }

    /// Calls set_profile; returns (code, out was NULL, handled, preedit, commit) and frees the output.
    fn profile(e: *mut ShanjieEngine, p: u32) -> (i32, bool, i32, String, String) {
        let mut o: *mut ShanjieOutput = sentinel();
        let rc = unsafe { shanjie_engine_set_profile(e, p, &mut o) };
        if o.is_null() {
            return (rc, true, 0, String::new(), String::new());
        }
        // SAFETY: test-only read of a live output.
        let r = unsafe {
            let s = |p| CStr::from_ptr(p).to_str().unwrap().to_string();
            (rc, false, (*o).handled, s((*o).preedit), s((*o).commit))
        };
        unsafe { shanjie_output_free(o) };
        r
    }

    #[test]
    fn child_lm() {
        if !is_child("child_lm") {
            return;
        }
        let dir = tiny_dir("lm", "ㄒㄧㄣ 鑫 -1.0\nㄒㄧㄣ 心 -4.0\n".as_bytes());
        let lm_path = dir.join("tiny.sjlm");
        std::fs::write(&lm_path, tiny_lm()).unwrap();
        let classes_path = dir.join("classes.sjc");
        let garbage = dir.join("garbage.sjlm");
        std::fs::write(&garbage, b"SJLM0001 not a model").unwrap();
        let c = |p: &Path| CString::new(p.to_str().unwrap()).unwrap();
        let (lm_c, garbage_c, missing_c) = (c(&lm_path), c(&garbage), c(&dir.join("missing.sjlm")));
        let bad_utf8 = CString::new(vec![0xffu8, 0xfe]).unwrap();
        let e = new_engine(&dir, 0);
        let enter = |e| send(e, key(ENTER, '\0')).2;

        // No LM: unigram; set_profile is remembered but changes nothing yet.
        assert!(type_xin(e) == "鑫", "unigram without LM");
        let (rc, null, handled, preedit, commit) = profile(e, 1);
        assert!(rc == 0 && !null && handled == 1 && preedit == "鑫" && commit.is_empty(), "profile without LM");
        assert!(profile(e, 0).0 == 0, "back to chat");
        assert!(enter(e) == "鑫", "commit without LM");

        // load_lm codes 1, 2, 3; each leaves "no LM".
        assert!(unsafe { shanjie_engine_load_lm(ptr::null_mut(), lm_c.as_ptr()) } == 1, "load engine NULL");
        assert!(unsafe { shanjie_engine_load_lm(e, ptr::null()) } == 1, "load path NULL");
        assert!(unsafe { shanjie_engine_load_lm(e, bad_utf8.as_ptr()) } == 2, "load non-UTF-8");
        assert!(unsafe { shanjie_engine_load_lm(e, missing_c.as_ptr()) } == 3, "load missing file");
        assert!(unsafe { shanjie_engine_load_lm(e, garbage_c.as_ptr()) } == 3, "load garbage file");
        // S2k: a directory with the model but no classes.sjc is a failed load, never a silent class-less model.
        assert!(unsafe { shanjie_engine_load_lm(e, lm_c.as_ptr()) } == 3, "load without classes.sjc");
        // K 0: classes 1 and 2 are <s> and </s>; the two words have none, so the tiny model's numbers stay as they are.
        let tiny = tiny_lm();
        let (cls, emit) = ([1u16, 2, 0xFFFF, 0xFFFF], [1.0, 1.0, 0.0, 0.0]);
        std::fs::write(&classes_path, crate::lm::test_classes(&tiny, 0, 0.8, &cls, &emit, &[0.1; 9])).unwrap();
        // An engine without data_dir is not reachable through the ABI (only `new` creates engines);
        // the closest cases are overlay-add.tsv, names-add.tsv or demote.tsv vanishing from data_dir after `new`:
        // load_lm reads them again (capping_overlay and demote.tsv), so any one fails with code 3.
        std::fs::rename(dir.join("overlay-add.tsv"), dir.join("overlay-add.bak")).unwrap();
        assert!(unsafe { shanjie_engine_load_lm(e, lm_c.as_ptr()) } == 3, "load without overlay-add.tsv");
        std::fs::rename(dir.join("overlay-add.bak"), dir.join("overlay-add.tsv")).unwrap();
        std::fs::rename(dir.join("names-add.tsv"), dir.join("names-add.bak")).unwrap();
        assert!(unsafe { shanjie_engine_load_lm(e, lm_c.as_ptr()) } == 3, "load without names-add.tsv");
        std::fs::rename(dir.join("names-add.bak"), dir.join("names-add.tsv")).unwrap();
        std::fs::rename(dir.join("demote.tsv"), dir.join("demote.bak")).unwrap();
        assert!(unsafe { shanjie_engine_load_lm(e, lm_c.as_ptr()) } == 3, "load without demote.tsv");
        std::fs::rename(dir.join("demote.bak"), dir.join("demote.tsv")).unwrap();
        assert!(type_xin(e) == "鑫" && profile(e, 1).3 == "鑫", "failed loads left no LM");
        assert!(profile(e, 0).0 == 0 && enter(e) == "鑫", "still unigram");

        // Success: the display is not recomputed by the load itself; the next change uses the LM.
        assert!(send(e, key(CHAR, 'v')).0 == 0, "typing");
        assert!(unsafe { shanjie_engine_load_lm(e, lm_c.as_ptr()) } == 0, "load ok");
        for c in ['u', 'p'] {
            assert!(send(e, key(CHAR, c)).0 == 0, "typing");
        }
        assert!(send(e, key(SPACE, '\0')).1 == "鑫", "chat with LM");
        let (rc, null, handled, preedit, commit) = profile(e, 1);
        assert!(rc == 0 && !null && handled == 1 && preedit == "心" && commit.is_empty(), "formal snapshot");
        assert!(enter(e) == "心", "formal commit");

        // set_abbreviation with a model: two initials are two units, turning it off drops them (V3 engine section 12.1).
        let mut o: *mut ShanjieOutput = sentinel();
        assert!(unsafe { shanjie_engine_set_abbreviation(e, 1, &mut o) } == 0 && !o.is_null(), "abbreviation on");
        unsafe { shanjie_output_free(o) };
        assert!(send(e, key(CHAR, 'v')).0 == 0, "typing");
        assert!(send(e, key(CHAR, 'q')).1.chars().count() == 2, "two units");
        o = sentinel();
        assert!(unsafe { shanjie_engine_set_abbreviation(e, 0, &mut o) } == 0 && !o.is_null(), "abbreviation off with two units");
        assert!(unsafe { CStr::from_ptr((*o).preedit) }.to_bytes().is_empty(), "off drops the units");
        unsafe { shanjie_output_free(o) };

        // A failed load keeps the loaded model.
        assert!(unsafe { shanjie_engine_load_lm(e, garbage_c.as_ptr()) } == 3, "garbage after success");
        assert!(type_xin(e) == "心", "previous LM kept");

        // set_profile codes 1, 2 (state unchanged).
        let mut o: *mut ShanjieOutput = sentinel();
        assert!(unsafe { shanjie_engine_set_profile(e, 0, ptr::null_mut()) } == 1, "profile out NULL");
        assert!(unsafe { shanjie_engine_set_profile(ptr::null_mut(), 0, &mut o) } == 1 && o.is_null(), "profile engine NULL");
        for p in [2, 7, u32::MAX] {
            let (rc, null, ..) = profile(e, p);
            assert!(rc == 2 && null, "profile out of range");
        }
        assert!(send(e, key(LEFT, '\0')).1 == "心", "invalid profile changed nothing");

        // set_profile code 4 (injected panic): NULL out, composition discarded, LM and profile kept.
        ARMED.with(|a| a.set(true));
        let (rc, null, ..) = profile(e, 1);
        assert!(rc == 4 && null, "panic maps to code 4 with NULL out");
        let (rc, preedit, _) = send(e, key(LEFT, '\0'));
        assert!(rc == 0 && preedit.is_empty(), "composition discarded after code 4");
        assert!(type_xin(e) == "心", "LM and formal kept after code 4");

        // reset keeps LM and profile.
        for mode in [0, 1] {
            let mut o = ptr::null_mut();
            assert!(unsafe { shanjie_engine_reset(e, mode, &mut o) } == 0, "reset");
            unsafe { shanjie_output_free(o) };
            assert!(type_xin(e) == "心", "reset keeps LM and profile");
        }
        assert!(profile(e, 0).3 == "鑫", "chat again");
        unsafe { shanjie_engine_free(e) };
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn load_lm_and_set_profile_codes() {
        let (ok, out, err) = run_child("child_lm", false);
        assert!(ok, "LM child failed");
        assert!(out.contains("1 passed"), "child test actually ran");
        assert!(!out.contains("shanjie-ffi-") && !err.contains("shanjie-ffi-"), "a path leaked");
    }

    /// Digits (docs/contracts/digits.md A.4.3) through the C ABI: a data directory with a tiny `digits.tsv`, the tiny model, `set_english(1)`:
    /// 2 0 2 6 Enter commits "2026"; with `set_english(0)` the same keys are the old Zhuyin keys.
    #[test]
    fn child_digits() {
        if !is_child("child_digits") {
            return;
        }
        let dir = tiny_dir("digits", "ㄒㄧㄣ 鑫 -1.0\n".as_bytes());
        let mut table = String::from("T\t1000\nN\t100\nS\t10\nD\t<s>\t5\t100\n");
        for k in 1..=9 {
            table += &format!("L\t{k}\t10\n");
        }
        table += "L\t10+\t10\n";
        for c in "0123456789./-".chars() {
            table += &format!("C\t{c}\t10\n");
        }
        std::fs::write(dir.join("digits.tsv"), table).unwrap();
        let lm_path = dir.join("tiny.sjlm");
        let tiny = tiny_lm();
        std::fs::write(&lm_path, &tiny).unwrap();
        let (cls, emit) = ([1u16, 2, 0xFFFF, 0xFFFF], [1.0, 1.0, 0.0, 0.0]);
        std::fs::write(dir.join("classes.sjc"), crate::lm::test_classes(&tiny, 0, 0.8, &cls, &emit, &[0.1; 9])).unwrap();
        let e = new_engine(&dir, 0);
        let c = CString::new(lm_path.to_str().unwrap()).unwrap();
        assert!(unsafe { shanjie_engine_load_lm(e, c.as_ptr()) } == 0, "load_lm");
        let keys = |e, s: &str| s.chars().map(|ch| send(e, key(CHAR, ch))).last().unwrap();
        assert!(set_english(e, 1) == 0);
        keys(e, "2026");
        let (rc, _, commit) = send(e, key(ENTER, '\0'));
        assert!(rc == 0 && commit == "2026", "set_english(1): 2026 Enter commits the digits");
        assert!(set_english(e, 0) == 0);
        let (rc, preedit, _) = send(e, key(CHAR, '2'));
        assert!(rc == 0 && preedit == "ㄉ", "set_english(0): the old Zhuyin key");
        unsafe { shanjie_engine_free(e) };
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn digits_through_the_c_abi() {
        let (ok, out, err) = run_child("child_digits", false);
        assert!(ok, "digits child failed: {out} {err}");
        assert!(out.contains("1 passed"), "child test actually ran");
        assert!(!out.contains("shanjie-ffi-") && !err.contains("shanjie-ffi-"), "a path leaked");
        assert!(!out.contains("2026") && !err.contains("2026"), "the keys leaked");
    }

    // ---- zhen-mixed: set_english (docs/contracts/zhen-mixed.md section 5.2) ----

    /// set_english; returns the code and frees the output.
    fn set_english(e: *mut ShanjieEngine, on: u32) -> i32 {
        let mut o: *mut ShanjieOutput = sentinel();
        let rc = unsafe { shanjie_engine_set_english(e, on, &mut o) };
        if rc == 0 {
            unsafe { shanjie_output_free(o) };
        } else {
            assert!(o.is_null(), "*out NULL on a non-zero code");
        }
        rc
    }

    /// set_english_overwrite; returns the code and frees the output.
    fn set_overwrite(e: *mut ShanjieEngine, on: u32) -> i32 {
        let mut o: *mut ShanjieOutput = sentinel();
        let rc = unsafe { shanjie_engine_set_english_overwrite(e, on, &mut o) };
        if rc == 0 {
            unsafe { shanjie_output_free(o) };
        } else {
            assert!(o.is_null(), "*out NULL on a non-zero code");
        }
        rc
    }

    /// set_english_key_order; returns the code and frees the output.
    fn set_key_order(e: *mut ShanjieEngine, on: u32) -> i32 {
        let mut o: *mut ShanjieOutput = sentinel();
        let rc = unsafe { shanjie_engine_set_english_key_order(e, on, &mut o) };
        if rc == 0 {
            unsafe { shanjie_output_free(o) };
        } else {
            assert!(o.is_null(), "*out NULL on a non-zero code");
        }
        rc
    }

    /// Types `keys` (Space for `_`); the last output's (candidates, commit, preedit), freed.
    fn type_out(e: *mut ShanjieEngine, keys: &str) -> (Vec<String>, String, String) {
        let mut last = (Vec::new(), String::new(), String::new());
        for c in keys.chars() {
            let mut o = ptr::null_mut();
            let k = if c == '_' { key(SPACE, '\0') } else { key(CHAR, c) };
            assert!(unsafe { shanjie_engine_key(e, k, &mut o) } == 0, "typing");
            // SAFETY: test-only read of a live output.
            last = unsafe {
                let s = |p| CStr::from_ptr(p).to_str().unwrap().to_string();
                let n = (*o).candidate_count as usize;
                let cands = (0..n).map(|i| s(*(*o).candidates.add(i))).collect();
                (cands, s((*o).commit), s((*o).preedit))
            };
            unsafe { shanjie_output_free(o) };
        }
        last
    }

    #[test]
    fn child_english() {
        if !is_child("child_english") {
            return;
        }
        let dir = tiny_dir("english", "ㄒㄧㄣ 鑫 -1.0\n".as_bytes());
        let e = new_engine(&dir, 0);
        // set_english codes, like set_abbreviation.
        let mut o: *mut ShanjieOutput = sentinel();
        assert!(unsafe { shanjie_engine_set_english(e, 1, ptr::null_mut()) } == 1, "set_english out NULL");
        assert!(unsafe { shanjie_engine_set_english(ptr::null_mut(), 1, &mut o) } == 1 && o.is_null(), "set_english engine NULL");
        for v in [2, u32::MAX] {
            assert!(set_english(e, v) == 2, "set_english out of range");
        }
        // `rust` reverts and no window opens.
        assert!(set_english(e, 1) == 0);
        let (cands, _, preedit) = type_out(e, "rust_");
        assert!(cands.is_empty() && preedit == "rust", "no window");
        assert!(send(e, key(ESC, '\0')).0 == 0);

        // set_english_overwrite (zhen-revert section 3): the codes of set_english.
        let mut o: *mut ShanjieOutput = sentinel();
        assert!(unsafe { shanjie_engine_set_english_overwrite(ptr::null_mut(), 1, &mut o) } == 1 && o.is_null(), "overwrite engine NULL");
        assert!(unsafe { shanjie_engine_set_english_overwrite(e, 1, ptr::null_mut()) } == 1, "overwrite out NULL");
        for v in [2, u32::MAX] {
            assert!(set_overwrite(e, v) == 2, "overwrite out of range");
        }
        // Through the C ABI, on by default: `o k` reverts to the raw keys, Space makes the one English token; nothing is committed
        // and no candidate window opens (zhen-revert section 5.2).
        let (cands, commit, preedit) = type_out(e, "ok");
        assert!(cands.is_empty() && commit.is_empty() && preedit == "ok", "the raw keys are the preedit");
        let (cands, commit, preedit) = type_out(e, "_");
        assert!(cands.is_empty() && commit.is_empty() && preedit == "ok", "the token stays in the composition");
        let (_, preedit, commit) = send(e, key(ENTER, '\0'));
        assert!(preedit.is_empty() && commit == "ok", "Enter sends it");
        let (cands, commit, preedit) = type_out(e, "gta6_");
        assert!(cands.is_empty() && commit.is_empty() && preedit == "gta6", "letters and a digit in one token");
        assert!(send(e, key(ESC, '\0')).0 == 0);
        // Off: a fourth key over the same column replaces, as it always did; on again, it reverts.
        assert!(set_overwrite(e, 0) == 0);
        let (_, _, preedit) = type_out(e, "vupp");
        assert!(preedit == "ㄒㄧㄣ", "off: the key replaces");
        assert!(send(e, key(ESC, '\0')).0 == 0);
        assert!(set_overwrite(e, 1) == 0);
        let (_, _, preedit) = type_out(e, "vupp");
        assert!(preedit == "vupp", "on: the raw keys");
        // Esc right after a revert brings the Zhuyin back; the second one clears it (zhen-revert section 2.4).
        assert!(send(e, key(ESC, '\0')).1 == "ㄒㄧㄣ");
        assert!(send(e, key(ESC, '\0')).1.is_empty());
        // set_english_key_order (zhen-keyorder section 3): the codes of set_english_overwrite, then the switch through the C ABI. On
        // (the default), `p v` is out of column order and reverts; off, the symbol goes into its column.
        let mut o: *mut ShanjieOutput = sentinel();
        assert!(unsafe { shanjie_engine_set_english_key_order(ptr::null_mut(), 1, &mut o) } == 1 && o.is_null(), "key order engine NULL");
        assert!(unsafe { shanjie_engine_set_english_key_order(e, 1, ptr::null_mut()) } == 1, "key order out NULL");
        for v in [2, u32::MAX] {
            assert!(set_key_order(e, v) == 2, "key order out of range");
        }
        let (_, _, preedit) = type_out(e, "pv");
        assert!(preedit == "pv", "key order on by default");
        assert!(send(e, key(ESC, '\0')).1 == "ㄒㄣ");
        assert!(send(e, key(ESC, '\0')).1.is_empty());
        assert!(set_key_order(e, 0) == 0);
        let (_, _, preedit) = type_out(e, "pv");
        assert!(preedit == "ㄒㄣ", "key order off: today");
        assert!(send(e, key(ESC, '\0')).0 == 0);
        assert!(set_key_order(e, 1) == 0);
        // The last two fields (zhen-inline section 1.3f): an open English run, in UTF-16 units of `preedit`; 0 and 0 otherwise.
        let fields = |k: ShanjieKey| {
            let mut o = ptr::null_mut();
            assert!(unsafe { shanjie_engine_key(e, k, &mut o) } == 0, "typing");
            // SAFETY: test-only read of a live output.
            let r = unsafe { (CStr::from_ptr((*o).preedit).to_str().unwrap().to_string(), (*o).english_run_utf16_start, (*o).english_run_utf16_len) };
            unsafe { shanjie_output_free(o) };
            r
        };
        type_out(e, "vup_"); // ㄒㄧㄣ with tone 1: one Chinese character in front
        let r = fields(ShanjieKey { kind: CHAR, ch: 'w' as u32, modifiers: 1 });
        assert!(r.0.chars().count() == 2 && r.1 == 1 && r.2 == 1, "the run `W` after the character: {r:?}");
        let r = fields(key(CHAR, 'i'));
        assert!(r.1 == 1 && r.2 == 2, "{r:?}");
        let r = fields(key(SPACE, '\0'));
        assert!(r.1 == 0 && r.2 == 0, "the run ended: 0 and 0, {r:?}");
        assert!(send(e, key(ESC, '\0')).0 == 0);
        // set_ascii (shift-toggle section 4): the codes of set_english, then the state through the C ABI.
        let mut o: *mut ShanjieOutput = sentinel();
        assert!(unsafe { shanjie_engine_set_ascii(ptr::null_mut(), 1, &mut o) } == 1 && o.is_null(), "ascii engine NULL");
        assert!(unsafe { shanjie_engine_set_ascii(e, 1, ptr::null_mut()) } == 1, "ascii out NULL");
        for v in [2, u32::MAX] {
            let mut o: *mut ShanjieOutput = sentinel();
            assert!(unsafe { shanjie_engine_set_ascii(e, v, &mut o) } == 2 && o.is_null(), "ascii out of range");
        }
        // ㄏ then ASCII: the snapshot is the run `c` (bold range), the next `1` is a character, Enter sends them.
        type_out(e, "c");
        let mut o = ptr::null_mut();
        assert!(unsafe { shanjie_engine_set_ascii(e, 1, &mut o) } == 0);
        // SAFETY: test-only read of a live output.
        let r = unsafe { (CStr::from_ptr((*o).preedit).to_str().unwrap().to_string(), (*o).handled, (*o).english_run_utf16_start, (*o).english_run_utf16_len) };
        unsafe { shanjie_output_free(o) };
        assert!(r == ("c".to_string(), 1, 0, 1), "{r:?}");
        let r = fields(key(CHAR, '1'));
        assert!(r.0 == "c1" && r.1 == 0 && r.2 == 2, "{r:?}");
        assert!(send(e, key(ENTER, '\0')).2 == "c1");
        let mut o = ptr::null_mut();
        assert!(unsafe { shanjie_engine_set_ascii(e, 0, &mut o) } == 0);
        unsafe { shanjie_output_free(o) };
        // Off: the same keys no longer revert.
        assert!(set_english(e, 0) == 0);
        let (_, _, preedit) = type_out(e, "ok");
        assert!(preedit == "ㄜ", "off");
        unsafe { shanjie_engine_free(e) };
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn english_codes_through_the_c_abi() {
        let (ok, out, err) = run_child("child_english", false);
        assert!(ok, "english child failed");
        assert!(out.contains("1 passed"), "child test actually ran");
        let all = format!("{out}{err}");
        assert!(!all.contains("shanjie-ffi-") && !all.contains("rust") && !all.contains("google"), "a path or a word leaked");
    }

    // ---- fuzzy input: set_fuzzy (docs/contracts/fuzzy-input.md section 2.1) ----

    /// Calls set_fuzzy; returns (code, out was NULL, handled, preedit, commit) and frees the output.
    fn fuzzy(e: *mut ShanjieEngine, flags: u32) -> (i32, bool, i32, String, String) {
        let mut o: *mut ShanjieOutput = sentinel();
        let rc = unsafe { shanjie_engine_set_fuzzy(e, flags, &mut o) };
        if o.is_null() {
            return (rc, true, 0, String::new(), String::new());
        }
        // SAFETY: test-only read of a live output.
        let r = unsafe {
            let s = |p| CStr::from_ptr(p).to_str().unwrap().to_string();
            (rc, false, (*o).handled, s((*o).preedit), s((*o).commit))
        };
        unsafe { shanjie_output_free(o) };
        r
    }

    #[test]
    fn child_fuzzy() {
        if !is_child("child_fuzzy") {
            return;
        }
        // 星 (ㄒㄧㄥ) beside 鑫 and 心 (ㄒㄧㄣ); the toy model likes 鑫 (see `tiny_lm`), and 星 is scored low enough that 鑫 beats it with the pair on even after
        // the sound penalty (`fuzzy::DELTA_SOUND`, 2.5).
        let dir = tiny_dir("fuzzy", "ㄒㄧㄣ 鑫 -1.0\nㄒㄧㄣ 心 -4.0\nㄒㄧㄥ 星 -6.0\n".as_bytes());
        let lm_path = dir.join("tiny.sjlm");
        std::fs::write(&lm_path, tiny_lm()).unwrap();
        let tiny = tiny_lm();
        let (cls, emit) = ([1u16, 2, 0xFFFF, 0xFFFF], [1.0, 1.0, 0.0, 0.0]);
        std::fs::write(dir.join("classes.sjc"), crate::lm::test_classes(&tiny, 0, 0.8, &cls, &emit, &[0.1; 9])).unwrap();
        let lm_c = CString::new(lm_path.to_str().unwrap()).unwrap();
        let e = new_engine(&dir, 0);
        assert!(unsafe { shanjie_engine_load_lm(e, lm_c.as_ptr()) } == 0, "load ok");

        // NULLs are 1 (and *out is NULL for a NULL engine).
        let mut o: *mut ShanjieOutput = sentinel();
        assert!(unsafe { shanjie_engine_set_fuzzy(e, 1, ptr::null_mut()) } == 1, "out NULL");
        assert!(unsafe { shanjie_engine_set_fuzzy(ptr::null_mut(), 1, &mut o) } == 1 && o.is_null(), "engine NULL");

        // Types ㄒㄧㄥ (`v u /`): the toy model's exact answer is 星.
        for c in ['v', 'u', '/'] {
            assert!(send(e, key(CHAR, c)).0 == 0, "typing");
        }
        let shown = send(e, key(SPACE, '\0')).1;
        assert!(shown == "星", "exact reading, got {shown}");

        // Any bit above bit 4 is 2 with a NULL out and the state unchanged: 0b1_0001 must not turn bit 0 on.
        for flags in [32, 33, 0xffff_ffff] {
            let (rc, null, ..) = fuzzy(e, flags);
            assert!(rc == 2 && null, "undefined bit {flags}");
        }
        assert!(send(e, key(LEFT, '\0')).1 == "星", "a refused call changed nothing");
        // Bit 0 (ㄣ/ㄥ) is 0 with the snapshot of the recomputed composition: 鑫 is read through ㄒㄧㄥ.
        let (rc, null, handled, preedit, commit) = fuzzy(e, 1);
        assert!(rc == 0 && !null && handled == 1 && commit.is_empty(), "snapshot");
        assert!(preedit == "鑫", "the composition is recomputed with the pair on, got {preedit}");
        // 0 and 31 are valid; 0 puts the exact answer back.
        assert!(fuzzy(e, 31).0 == 0);
        let (rc, _, _, preedit, _) = fuzzy(e, 0);
        assert!(rc == 0 && preedit == "星", "all off again");
        unsafe { shanjie_engine_free(e) };
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn fuzzy_codes_through_the_c_abi() {
        let (ok, out, err) = run_child("child_fuzzy", false);
        assert!(ok, "fuzzy child failed: {out}{err}");
        assert!(out.contains("1 passed"), "child test actually ran");
        assert!(!format!("{out}{err}").contains("shanjie-ffi-"), "a path leaked");
    }

    // ---- S7c-2a: load_tinylm, set_rerank, take / run / apply / free (docs/contracts/s7c2a-core-rerank.md section 3 item 3) ----

    /// dev302 row 12 (as engine_lm.rs counts it): the n-gram shows RERANK_NGRAM, and the toy model below changes it.
    const RERANK_ROW: usize = 12;
    const RERANK_NGRAM: &str = "我在家";
    /// context-return section 4: an ASCII marker in the left text that reaches a job (the last L = 8 characters of "我說zqxjvk ";
    /// no Han tail, so the n-gram decodes as without a left text).
    const LEFT_MARKER: &str = "zqxjvk";

    fn set_left(e: *mut ShanjieEngine, text: &str) -> i32 {
        let c = CString::new(text).unwrap();
        unsafe { shanjie_engine_set_left_context(e, c.as_ptr()) }
    }

    /// The version 1 file at `p` rewritten as version 5 declaring the left-context length `l` (docs/contracts/context-return.md
    /// section 2): both deltas 0, the standard triples for every level, an all-zero side file hash, then L.
    fn as_v5(p: &Path, l: u32) {
        let b = std::fs::read(p).unwrap();
        let triple = |at: usize| [&b[at..at + 16], &0f64.to_le_bytes()[..]].concat();
        let (chat, formal) = (triple(36), triple(52));
        let mut h = [&b[..100], &[0; 16][..], &chat, &chat, &formal, &formal, &[0; 32][..], &l.to_le_bytes()[..]].concat();
        h[7] = b'5';
        h[8..12].copy_from_slice(&5u32.to_le_bytes());
        std::fs::write(p, [h.as_slice(), &b[100..]].concat()).unwrap();
    }

    /// data/lm/bigram.sjlm and classes.sjc symlinked into `dir` without the kn.sjkn model-v6 puts beside them: the toy `.sjtl`
    /// files here (versions 1 and 3) were tuned without a side file and pair only with the model loaded without KN (model-v6
    /// section 2.7). Returns the model path.
    fn lm_without_kn(dir: &Path) -> PathBuf {
        for f in ["bigram.sjlm", "classes.sjc"] {
            let _ = std::fs::remove_file(dir.join(f));
            std::os::unix::fs::symlink(root().join("data/lm").join(f), dir.join(f)).unwrap();
        }
        dir.join("bigram.sjlm")
    }

    /// The toy model of eval/golden/tinylm-tiny/ in `dir`, its n-gram hash patched to data/lm/bigram.sjlm's (offset 68) and alpha
    /// 100 / tau infinite for both profiles (offsets 36, 52), so its LLs decide (core/tests/engine_rerank.rs). `hash: false`
    /// keeps the recorded hash (kn-tiny's), a model for another n-gram model.
    fn toy_sjtl(dir: &Path, name: &str, hash: bool) -> PathBuf {
        let mut b = std::fs::read(root().join("eval/golden/tinylm-tiny/tiny-int8.sjtl")).unwrap();
        if hash {
            let h = crate::eval::sha256_hex(&std::fs::read(root().join("data/lm/bigram.sjlm")).unwrap());
            for i in 0..32 {
                b[68 + i] = u8::from_str_radix(&h[2 * i..2 * i + 2], 16).unwrap();
            }
        }
        for at in [36, 52] {
            b[at..at + 8].copy_from_slice(&100f64.to_le_bytes());
            b[at + 8..at + 16].copy_from_slice(&f64::INFINITY.to_le_bytes());
        }
        let p = dir.join(name);
        std::fs::write(&p, b).unwrap();
        p
    }

    /// The real data, its model, and the keys of RERANK_ROW (standard layout).
    fn rerank_engine() -> (*mut ShanjieEngine, Vec<ShanjieKey>) {
        let data = root().join("data/lexicon");
        let lex = crate::engine::load_lexicon(&data).unwrap();
        let mut rows = Vec::new();
        for t in dev_texts() {
            rows.extend(parse_rows(&t).unwrap());
        }
        let r = usable(&lex, rows).swap_remove(RERANK_ROW - 1);
        let syls = r.reading.clone().unwrap_or_else(|| lex.to_syllables(&r.sent).unwrap());
        let keys = syls.iter().flat_map(|s| keys_of(Layout::Standard, s)).collect();
        (new_engine(&data, 0), keys)
    }

    fn load_tinylm(e: *mut ShanjieEngine, p: &Path) -> i32 {
        let c = CString::new(p.to_str().unwrap()).unwrap();
        unsafe { shanjie_engine_load_tinylm(e, c.as_ptr()) }
    }

    /// take; returns (code, job).
    fn take(e: *mut ShanjieEngine) -> (i32, *mut ShanjieRerankJob) {
        let mut j: *mut ShanjieRerankJob = sentinel();
        let rc = unsafe { shanjie_engine_rerank_take(e, &mut j) };
        (rc, j)
    }

    /// apply; returns (code, preedit when 0). `*out` is NULL on every non-zero code.
    fn apply(e: *mut ShanjieEngine, j: *mut ShanjieRerankJob) -> (i32, String) {
        let mut o: *mut ShanjieOutput = sentinel();
        let rc = unsafe { shanjie_engine_rerank_apply(e, j, &mut o) };
        if rc != 0 {
            assert!(o.is_null(), "*out NULL on a non-zero code");
            return (rc, String::new());
        }
        let p = read_out(o).5;
        unsafe { shanjie_output_free(o) };
        (rc, p)
    }

    /// set_rerank; returns (code, preedit when 0).
    fn set_rerank(e: *mut ShanjieEngine, on: u32) -> (i32, String) {
        let mut o: *mut ShanjieOutput = sentinel();
        let rc = unsafe { shanjie_engine_set_rerank(e, on, &mut o) };
        if rc != 0 {
            assert!(o.is_null(), "*out NULL on a non-zero code");
            return (rc, String::new());
        }
        let p = read_out(o).5;
        unsafe { shanjie_output_free(o) };
        (rc, p)
    }

    #[test]
    fn child_rerank() {
        if !is_child("child_rerank") {
            return;
        }
        let dir = std::env::temp_dir().join(format!("shanjie-ffi-{}-rerank", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let good = toy_sjtl(&dir, "good.sjtl", true);
        let other = toy_sjtl(&dir, "other.sjtl", false);
        let garbage = dir.join("garbage.sjtl");
        std::fs::write(&garbage, b"SJTL0001 not a model").unwrap();
        let (e, keys) = rerank_engine();
        let bad_utf8 = CString::new(vec![0xffu8, 0xfe]).unwrap();
        let good_c = CString::new(good.to_str().unwrap()).unwrap();
        unsafe { shanjie_rerank_free(ptr::null_mut()) };

        // load_tinylm: 1 for NULLs, 2 for a non-UTF-8 path, 3 before the n-gram model and for bad files.
        assert!(unsafe { shanjie_engine_load_tinylm(ptr::null_mut(), good_c.as_ptr()) } == 1, "load engine NULL");
        assert!(unsafe { shanjie_engine_load_tinylm(e, ptr::null()) } == 1, "load path NULL");
        assert!(unsafe { shanjie_engine_load_tinylm(e, bad_utf8.as_ptr()) } == 2, "load non-UTF-8");
        assert!(load_tinylm(e, &good) == 3, "no n-gram model yet");
        let lm = CString::new(lm_without_kn(&dir).to_str().unwrap()).unwrap();
        assert!(unsafe { shanjie_engine_load_lm(e, lm.as_ptr()) } == 0, "load_lm");
        for bad in [dir.join("missing.sjtl"), garbage, other, dir.clone()] {
            assert!(load_tinylm(e, &bad) == 3, "bad tinylm file");
        }

        // set_rerank codes, like set_demote.
        let mut o: *mut ShanjieOutput = sentinel();
        assert!(unsafe { shanjie_engine_set_rerank(ptr::null_mut(), 1, &mut o) } == 1 && o.is_null(), "set_rerank engine NULL");
        assert!(unsafe { shanjie_engine_set_rerank(e, 1, ptr::null_mut()) } == 1, "set_rerank out NULL");
        for v in [2, u32::MAX] {
            assert!(set_rerank(e, v).0 == 2, "set_rerank out of range");
        }

        // NULL combinations of take, run and apply.
        let mut j: *mut ShanjieRerankJob = sentinel();
        assert!(unsafe { shanjie_engine_rerank_take(ptr::null_mut(), &mut j) } == 1 && j.is_null(), "take engine NULL");
        assert!(unsafe { shanjie_engine_rerank_take(e, ptr::null_mut()) } == 1, "take job NULL");
        assert!(unsafe { shanjie_rerank_run(ptr::null_mut()) } == 1, "run NULL");
        assert!(apply(ptr::null_mut(), sentinel()).0 == 1, "apply engine NULL");
        assert!(apply(e, ptr::null_mut()).0 == 1, "apply job NULL");

        // Failed loads left no model: typing makes no job, even with the switch on.
        assert!(set_rerank(e, 1).0 == 0, "switch on");
        for &k in &keys {
            assert!(send(e, k).0 == 0, "typing");
            let (rc, j) = take(e);
            assert!(rc == 0 && j.is_null(), "no job without a model");
        }
        assert!(send(e, key(ESC, '\0')).0 == 0);

        // load_tinylm does not turn rerank back on.
        assert!(set_rerank(e, 0).0 == 0);
        assert!(load_tinylm(e, &good) == 0, "load ok");
        for &k in &keys {
            send(e, k);
            assert!(take(e).1.is_null(), "off after load_tinylm");
        }
        // On again: set_rerank recomputes, and that refresh makes a job.
        let (rc, shown) = set_rerank(e, 1);
        assert!(rc == 0 && shown == RERANK_NGRAM, "snapshot");
        let (rc, j) = take(e);
        assert!(rc == 0 && !j.is_null(), "a job");
        assert!(take(e).1.is_null(), "taken once");

        // apply before run: no result yet; apply's out NULL: 1.
        assert!(apply(e, j).0 == SHANJIE_NO_CHANGE, "apply before run");
        assert!(unsafe { shanjie_engine_rerank_apply(e, j, ptr::null_mut()) } == 1, "apply out NULL");
        assert!(unsafe { shanjie_rerank_run(j) } == 0, "run");
        let (rc, after) = apply(e, j);
        assert!(rc == 0 && after != RERANK_NGRAM && after.chars().count() == RERANK_NGRAM.chars().count(), "applied a change");
        assert!(apply(e, j).0 == SHANJIE_NO_CHANGE, "second apply");
        assert!(unsafe { shanjie_rerank_run(j) } == 0 && apply(e, j).0 == SHANJIE_NO_CHANGE, "run again after the apply: stale");
        unsafe { shanjie_rerank_free(j) };
        let (rc, _, commit) = send(e, key(ENTER, '\0'));
        assert!(rc == 0 && commit == after, "Enter sends the applied text");

        // A key that does not re-decode (a cursor move) keeps the job; one that does (Backspace deleting a syllable) makes it stale.
        for &k in &keys {
            send(e, k);
        }
        let (_, j) = take(e);
        assert!(send(e, key(LEFT, '\0')).1 == RERANK_NGRAM);
        assert!(unsafe { shanjie_rerank_run(j) } == 0);
        let (rc, moved) = apply(e, j);
        assert!(rc == 0 && moved == after, "applied after a cursor move");
        unsafe { shanjie_rerank_free(j) };
        assert!(send(e, key(ESC, '\0')).0 == 0);
        for &k in &keys {
            send(e, k);
        }
        let (_, j) = take(e);
        assert!(unsafe { shanjie_rerank_run(j) } == 0);
        assert!(send(e, key(BACKSPACE, '\0')).1 != RERANK_NGRAM, "Backspace re-decodes");
        assert!(apply(e, j).0 == SHANJIE_NO_CHANGE, "stale");
        unsafe { shanjie_rerank_free(j) };
        assert!(send(e, key(ESC, '\0')).0 == 0);

        // apply code 4 (injected panic after the change): NULL out, composition discarded.
        for &k in &keys {
            send(e, k);
        }
        let (_, j) = take(e);
        assert!(unsafe { shanjie_rerank_run(j) } == 0);
        ARMED.with(|a| a.set(true));
        assert!(apply(e, j).0 == 4, "apply panic maps to 4");
        let (rc, preedit, _) = send(e, key(LEFT, '\0'));
        assert!(rc == 0 && preedit.is_empty(), "discarded after apply's 4");
        unsafe { shanjie_rerank_free(j) };

        // A job outlives its engine: run and free after engine_free.
        for &k in &keys {
            send(e, k);
        }
        let (_, j) = take(e);
        unsafe { shanjie_engine_free(e) };
        assert!(unsafe { shanjie_rerank_run(j) } == 0, "run after engine_free");
        unsafe { shanjie_rerank_free(j) };
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn rerank_codes_through_the_c_abi() {
        let (ok, out, err) = run_child("child_rerank", false);
        assert!(ok, "rerank child failed");
        assert!(out.contains("1 passed"), "child test actually ran");
        assert!(!out.contains("shanjie-ffi-") && !err.contains("shanjie-ffi-"), "a path leaked");
    }

    // ---- Rerank strength: set_rerank_level, rerank_levels (docs/contracts/rerank-strength.md sections 2.2 and 9) ----

    /// alpha 0: the fused score is the n-gram score, so the first always stays (`pick` moves only on a strictly higher score).
    const KEEP: (f64, f64, f64) = (0.0, f64::INFINITY, 0.0);
    /// alpha 100, tau infinite, delta 0: the toy model's LLs decide, and RERANK_ROW changes (as `toy_sjtl`).
    const MOVE: (f64, f64, f64) = (100.0, f64::INFINITY, 0.0);

    /// The synthetic version 3 file eval/golden/tinylm-tiny/tiny-int8-v3.sjtl in `dir`, its n-gram hash patched to
    /// data/lm/bigram.sjlm's, with MOVE for both profiles at level `moving` (`RerankLevel` code) and KEEP at the other two: which
    /// level the rerank used then shows in whether RERANK_ROW changes. Offsets: rerank-strength section 2.1.
    fn toy_sjtl_v3(dir: &Path, moving: u32) -> PathBuf {
        let mut b = std::fs::read(root().join("eval/golden/tinylm-tiny/tiny-int8-v3.sjtl")).unwrap();
        let h = crate::eval::sha256_hex(&std::fs::read(root().join("data/lm/bigram.sjlm")).unwrap());
        for i in 0..32 {
            b[68 + i] = u8::from_str_radix(&h[2 * i..2 * i + 2], 16).unwrap();
        }
        let t = |level: u32| if level == moving { MOVE } else { KEEP };
        // (alpha, tau, delta offsets, level): the standard sets (chat, formal), then chat conservative, chat aggressive, formal
        // conservative, formal aggressive.
        let sets = [(36, 44, 100, 1), (52, 60, 108, 1), (116, 124, 132, 0), (140, 148, 156, 2), (164, 172, 180, 0), (188, 196, 204, 2)];
        for (a, tau, d, level) in sets {
            let (x, y, z) = t(level);
            b[a..a + 8].copy_from_slice(&x.to_le_bytes());
            b[tau..tau + 8].copy_from_slice(&y.to_le_bytes());
            b[d..d + 8].copy_from_slice(&z.to_le_bytes());
        }
        let p = dir.join(format!("v3-moving-{moving}.sjtl"));
        std::fs::write(&p, b).unwrap();
        p
    }

    /// set_rerank_level; returns (code, out was NULL) and frees the output.
    fn set_level(e: *mut ShanjieEngine, level: u32) -> (i32, bool) {
        let mut o: *mut ShanjieOutput = sentinel();
        let rc = unsafe { shanjie_engine_set_rerank_level(e, level, &mut o) };
        let null = o.is_null();
        unsafe { shanjie_output_free(o) };
        (rc, null)
    }

    fn levels(e: *mut ShanjieEngine) -> u32 {
        let mut m = 0xdead_beef;
        assert!(unsafe { shanjie_engine_rerank_levels(e, &mut m) } == 0, "rerank_levels");
        m
    }

    /// With RERANK_ROW composed: set the level (its snapshot re-decodes the n-gram text and leaves a job), take, run, apply;
    /// true when the composition changed.
    fn moves_at(e: *mut ShanjieEngine, level: u32) -> bool {
        assert!(set_level(e, level) == (0, false), "set level {level}");
        let (rc, j) = take(e);
        assert!(rc == 0 && !j.is_null(), "a job after set level {level}");
        assert!(unsafe { shanjie_rerank_run(j) } == 0);
        let (rc, shown) = apply(e, j);
        unsafe { shanjie_rerank_free(j) };
        assert!(rc == 0 || rc == SHANJIE_NO_CHANGE, "apply {rc}");
        assert!(rc == SHANJIE_NO_CHANGE || shown != RERANK_NGRAM);
        rc == 0
    }

    #[test]
    fn child_rerank_level() {
        if !is_child("child_rerank_level") {
            return;
        }
        let dir = std::env::temp_dir().join(format!("shanjie-ffi-{}-rerank-level", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let v1 = toy_sjtl(&dir, "v1.sjtl", true);
        let (e, keys) = rerank_engine();

        // Codes: 1 for a NULL engine or out, 2 out of range with *out NULL; rerank_levels 1 for a NULL engine or levels.
        let mut o: *mut ShanjieOutput = sentinel();
        assert!(unsafe { shanjie_engine_set_rerank_level(ptr::null_mut(), 1, &mut o) } == 1 && o.is_null(), "engine NULL");
        assert!(unsafe { shanjie_engine_set_rerank_level(e, 1, ptr::null_mut()) } == 1, "out NULL");
        for v in [3, 7, u32::MAX] {
            assert!(set_level(e, v) == (2, true), "level {v} out of range");
        }
        let mut m = 0u32;
        assert!(unsafe { shanjie_engine_rerank_levels(ptr::null_mut(), &mut m) } == 1, "levels engine NULL");
        assert!(unsafe { shanjie_engine_rerank_levels(e, ptr::null_mut()) } == 1, "levels out NULL");

        // No small model: mask 0, and a level is remembered for the model loaded later.
        assert!(levels(e) == 0, "no model");
        assert!(set_level(e, 2) == (0, false), "remembered without a model");
        let lm = CString::new(lm_without_kn(&dir).to_str().unwrap()).unwrap();
        assert!(unsafe { shanjie_engine_load_lm(e, lm.as_ptr()) } == 0, "load_lm");
        assert!(levels(e) == 0, "no small model after load_lm");
        assert!(load_tinylm(e, &toy_sjtl_v3(&dir, 2)) == 0 && levels(e) == 7, "version 3: 7");
        assert!(set_rerank(e, 1).0 == 0);
        for &k in &keys {
            send(e, k);
        }
        let (_, j) = take(e);
        assert!(unsafe { shanjie_rerank_run(j) } == 0);
        let (rc, shown) = apply(e, j);
        assert!(rc == 0 && shown != RERANK_NGRAM, "the level set before the load is used");
        unsafe { shanjie_rerank_free(j) };

        // Out of range leaves level and generation alone: a job run before still applies (aggressive moves).
        assert!(set_level(e, 2) == (0, false));
        let (_, j) = take(e);
        assert!(!j.is_null() && unsafe { shanjie_rerank_run(j) } == 0);
        assert!(set_level(e, 3) == (2, true));
        let (rc, shown) = apply(e, j);
        assert!(rc == 0 && shown != RERANK_NGRAM, "level 3 changed neither the generation nor the level");
        unsafe { shanjie_rerank_free(j) };

        // A valid level makes a job already run stale (the job fixed its own (alpha, tau, delta) when it was made).
        assert!(set_level(e, 2) == (0, false));
        let (_, j) = take(e);
        assert!(unsafe { shanjie_rerank_run(j) } == 0);
        assert!(set_level(e, 2) == (0, false), "even the same level");
        assert!(apply(e, j).0 == SHANJIE_NO_CHANGE, "stale after set_rerank_level");
        unsafe { shanjie_rerank_free(j) };

        // Each level uses its own set, in both profiles: with MOVE at exactly one level, only that level changes the row.
        for moving in 0..3 {
            assert!(load_tinylm(e, &toy_sjtl_v3(&dir, moving)) == 0 && levels(e) == 7);
            for p in [1, 0] {
                assert!(profile(e, p).0 == 0);
                for level in 0..3 {
                    assert!(moves_at(e, level) == (level == moving), "MOVE at {moving}, level {level}, profile {p}");
                }
            }
        }

        // load_lm keeps the level (and drops the model: mask 0); the reloaded model uses it at once.
        assert!(set_level(e, 0) == (0, false));
        assert!(unsafe { shanjie_engine_load_lm(e, lm.as_ptr()) } == 0 && levels(e) == 0);
        assert!(load_tinylm(e, &toy_sjtl_v3(&dir, 0)) == 0);
        assert!(set_rerank(e, 1).0 == 0);
        let (_, j) = take(e);
        assert!(unsafe { shanjie_rerank_run(j) } == 0 && apply(e, j).0 == 0, "conservative kept across load_lm and load_tinylm");
        unsafe { shanjie_rerank_free(j) };

        // A version 1 or 2 file: mask 2, and every level uses the standard set (the toy's alpha 100: it moves).
        let mut v2 = std::fs::read(&v1).unwrap();
        v2.splice(100..100, [0f64, 0f64].iter().flat_map(|d| d.to_le_bytes()));
        v2[7] = b'2';
        v2[8..12].copy_from_slice(&2u32.to_le_bytes());
        let v2_path = dir.join("v2.sjtl");
        std::fs::write(&v2_path, v2).unwrap();
        for f in [&v1, &v2_path] {
            assert!(load_tinylm(e, f) == 0 && levels(e) == 2, "version 1 or 2: 2");
            for level in 0..3 {
                assert!(moves_at(e, level), "level {level} uses the standard set");
            }
        }

        // Code 4 (injected panic): NULL out, composition discarded, the new level kept.
        assert!(load_tinylm(e, &toy_sjtl_v3(&dir, 2)) == 0);
        ARMED.with(|a| a.set(true));
        assert!(set_level(e, 2) == (4, true), "panic maps to 4 with NULL out");
        let (rc, preedit, _) = send(e, key(LEFT, '\0'));
        assert!(rc == 0 && preedit.is_empty(), "composition discarded after code 4");
        for &k in &keys {
            send(e, k);
        }
        let (_, j) = take(e);
        assert!(unsafe { shanjie_rerank_run(j) } == 0);
        assert!(apply(e, j).0 == 0, "the level set by the call that returned 4 is kept");
        unsafe { shanjie_rerank_free(j) };
        unsafe { shanjie_engine_free(e) };
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn rerank_level_through_the_c_abi() {
        let (ok, out, err) = run_child("child_rerank_level", false);
        assert!(ok, "rerank level child failed: {err}");
        assert!(out.contains("1 passed"), "child test actually ran");
        assert!(!out.contains("shanjie-ffi-") && !err.contains("shanjie-ffi-"), "a path leaked");
    }

    #[test]
    fn child_rerank_panic() {
        if !is_child("child_rerank_panic") {
            return;
        }
        let dir = std::env::temp_dir().join(format!("shanjie-ffi-{}-rerank-panic", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let good = toy_sjtl(&dir, "good.sjtl", true);
        as_v5(&good, 8);
        let (e, keys) = rerank_engine();
        let lm = CString::new(lm_without_kn(&dir).to_str().unwrap()).unwrap();
        assert!(unsafe { shanjie_engine_load_lm(e, lm.as_ptr()) } == 0 && load_tinylm(e, &good) == 0, "loads");
        assert!(set_rerank(e, 1).0 == 0, "on (off by default since S7c-2c)");
        assert!(set_left(e, &format!("我說{LEFT_MARKER} ")) == 0, "left text");
        for &k in &keys {
            send(e, k);
        }
        let (_, j) = take(e);
        assert!(!j.is_null(), "a job");
        // A first run succeeds; the second panics and must leave no result behind (not even the first one's).
        assert!(unsafe { shanjie_rerank_run(j) } == 0, "first run");
        ARMED.with(|a| a.set(true));
        assert!(unsafe { shanjie_rerank_run(j) } == 4, "run panic maps to 4");
        assert!(PAYLOAD.with(|p| p.borrow().contains(RERANK_NGRAM)), "payload carries the candidate text");
        assert!(PAYLOAD.with(|p| p.borrow().contains(LEFT_MARKER)), "payload carries the job's left text");
        // run has no engine: the composition is unchanged, and no result was stored.
        let (rc, preedit, _) = send(e, key(LEFT, '\0'));
        assert!(rc == 0 && preedit == RERANK_NGRAM, "composition unchanged after run's 4");
        assert!(apply(e, j).0 == SHANJIE_NO_CHANGE, "no result after run's 4");
        unsafe { shanjie_rerank_free(j) };
        unsafe { shanjie_engine_free(e) };
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn r2_rerank_run_panic_is_silent() {
        let (ok, out, err) = run_child("child_rerank_panic", false);
        assert!(ok, "quiet child failed");
        for marker in [RERANK_NGRAM, LEFT_MARKER] {
            assert!(!out.contains(marker) && !err.contains(marker), "input text leaked with the silent hook");
        }
        assert!(out.contains("1 passed"), "child test actually ran");
        // Positive control: the same child with the default hook must show the text.
        let (ok, out, err) = run_child("child_rerank_panic", true);
        assert!(ok && out.contains("1 passed"), "default-hook child failed");
        assert!(err.contains(RERANK_NGRAM) && err.contains(LEFT_MARKER), "positive control: default hook did not print the text");
    }
}
