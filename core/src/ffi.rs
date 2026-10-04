//! S3a C ABI (docs/contracts/s3a.md §6) over `engine::Engine`; declarations in `core/include/shanjie.h`.
//! R2: every export runs under `catch_unwind` with a silent panic hook; errors are codes only, never text.
#![deny(unsafe_op_in_unsafe_fn)]

#[cfg(panic = "abort")]
compile_error!("C ABI requires panic=unwind");

use crate::engine::{Engine, EngineError, Key, KeyKind, Layout, Output, ResetMode};
use crate::lm::Profile;
use std::ffi::{c_char, CStr, CString};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::Path;
use std::ptr;
use std::sync::Once;

pub const SHANJIE_OK: i32 = 0;
pub const SHANJIE_ERR_NULL: i32 = 1;
pub const SHANJIE_ERR_INVALID: i32 = 2;
pub const SHANJIE_ERR_LOAD: i32 = 3;
pub const SHANJIE_ERR_INTERNAL: i32 = 4;

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
}

/// Opaque to C.
pub struct ShanjieEngine(Engine);

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
    guard(|| {
        // SAFETY: forwarded caller contract.
        if !unsafe { clear_out(out) } || data_dir.is_null() {
            return SHANJIE_ERR_NULL;
        }
        let layout = match layout {
            0 => Layout::Standard,
            1 => Layout::Eten,
            _ => return SHANJIE_ERR_INVALID,
        };
        // SAFETY: non-NULL, NUL-terminated per the caller contract.
        let Ok(dir) = unsafe { CStr::from_ptr(data_dir) }.to_str() else {
            return SHANJIE_ERR_INVALID;
        };
        match Engine::new(Path::new(dir), layout) {
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

/// S2c. Loads the bigram model at `path` with `data_dir/overlay-add.tsv` of this engine. Does not
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
    const ESC: u32 = 6;
    /// Leaves a composition intact, so an empty preedit after it proves the reset.
    const LEFT: u32 = 7;

    /// A data dir with a tiny base lexicon and an empty overlay.
    fn tiny_dir(tag: &str, base: &[u8]) -> PathBuf {
        let d = std::env::temp_dir().join(format!("shanjie-ffi-{}-{tag}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        std::fs::write(d.join("mcbpmf-data.txt"), base).unwrap();
        std::fs::write(d.join("overlay-add.tsv"), "").unwrap();
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
        let e = new_engine(&dir, 0);

        // engine_key
        let mut o: *mut ShanjieOutput = sentinel();
        assert!(unsafe { shanjie_engine_key(e, key(ESC, '\0'), ptr::null_mut()) } == 1, "key out NULL");
        assert!(unsafe { shanjie_engine_key(ptr::null_mut(), key(ESC, '\0'), &mut o) } == 1 && o.is_null(), "key engine NULL");
        for (kind, ch) in [(0, 0x61), (14, 0x61), (CHAR, 0xD800), (CHAR, 0x110000)] {
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
        // An engine without data_dir is not reachable through the ABI (only `new` creates engines);
        // the closest case is the overlay vanishing from data_dir after `new`.
        std::fs::rename(dir.join("overlay-add.tsv"), dir.join("overlay.bak")).unwrap();
        assert!(unsafe { shanjie_engine_load_lm(e, lm_c.as_ptr()) } == 3, "load without overlay");
        std::fs::rename(dir.join("overlay.bak"), dir.join("overlay-add.tsv")).unwrap();
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
}
