//! The host's one engine (docs/spec/windows-tip.md §5.2 and §5.3; the scheme of the in-DLL engine of docs/spec/windows-tip.md §3.4). One engine behind
//! one lock; its owner is assigned by the host as (connection id, ctx), so a client's `ctx` means nothing on any other connection.
//! A key from another owner first discards the old owner's state (reset mode 1, never mode 0). Only the core's C ABI is used.
//!
//! Locking: a key takes the lock with `try_lock` only and answers `Busy` when it is taken, so no client waits on another; the lock
//! covers "call the core, copy the output", and the answer is written after it is released (the caller writes). `Release`, `Reset` and
//! a closed connection have no one waiting for an answer and must not be skipped (a stale owner would keep its composition), so they
//! wait for the lock. A poisoned lock ends the process (the next client starts a new host).
//!
//! The semantic rerank (docs/spec/windows-tip.md §5.2, docs/spec/semantic-rerank.md §8.5). After every key the job the core left is taken under the engine lock and
//! handed to the one rerank thread, which only calls `shanjie_rerank_run`: it never touches the engine and never takes the engine lock. Nothing is pushed: the
//! result waits until its owner asks (`poll`), and is applied then, under the engine lock; an Enter first catches up with the job of its composition (`catch_up`).
//! Two locks, always in this order: the engine lock (`SHARED`), then the rerank lock (`RR`); `SHARED` is never taken while `RR` is held. The jobs (`rerank::Slots`),
//! the applied snapshot and the owner are single slots for the whole process. Compiled on every platform for its tests; only the thread priority is Windows code.
use crate::guard;
pub use crate::rerank::Owner;
use crate::rerank::Slots;
use crate::startup::{exit, EXIT_POISONED, EXIT_RERANK};
use crate::timing;
use shanjie_core::ffi::{self as core_ffi, ShanjieEngine, ShanjieKey, ShanjieOutput, ShanjieRerankJob};
use shanjie_win_proto::{Key, Out, PollResult, Polled};
use std::ffi::{CStr, CString};
use std::path::{Path, PathBuf};
use std::ptr;
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicU8, Ordering};
use std::sync::{Condvar, Mutex, MutexGuard, TryLockError};
use std::time::{Duration, Instant};

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

/// One rerank job of the core. Dropping it frees it, and a job is in one place at a time (`rerank::Slots`, or the rerank thread's hands while it runs), so
/// every job the core hands out is freed exactly once on every path, and never while it runs.
struct Job(*mut ShanjieRerankJob);
// SAFETY: `shanjie.h`: run and free take no engine and may be called on any thread, and a job is `Send` in the core (docs/spec/semantic-rerank.md §6.3); one thread at a time holds a `Job`.
unsafe impl Send for Job {}

impl Drop for Job {
    fn drop(&mut self) {
        // SAFETY: a job `shanjie_engine_rerank_take` handed out, not running (the rerank thread owns a job for the whole run), freed here and nowhere else.
        unsafe { core_ffi::shanjie_rerank_free(self.0) };
        #[cfg(test)]
        probe::FREES.fetch_add(1, Ordering::SeqCst);
    }
}

/// The composition as the last applied rerank left it, kept until its owner confirms it wrote it (`poll`) or anything else happens to the engine.
struct Snapshot {
    owner: Owner,
    id: u64,
    out: Out,
}

pub struct Shared {
    eng: Option<EnginePtr>,
    owner: Option<Owner>,
    /// The owner's last output had the candidate window open: an Enter then picks a row and does not wait for the rerank.
    window: bool,
    /// The one applied snapshot of the process; stored and cleared only under this lock.
    snap: Option<Snapshot>,
    /// The number of the last snapshot: counts up for the life of the process, so a number a client hands back never names a later snapshot.
    snaps: u64,
}

static SHARED: Mutex<Shared> = Mutex::new(Shared { eng: None, owner: None, window: false, snap: None, snaps: 0 });
/// The rerank lock: taken after `SHARED` or alone, never before it.
static RR: Mutex<Slots<Job>> = Mutex::new(Slots::new());
/// Signalled when a job is queued (the rerank thread waits on it) and when a run returns (an Enter waits on it).
static RR_CHANGED: Condvar = Condvar::new();
/// A copy of `Shared.owner` for the question "is the asker the owner?" without any lock (`poll`): written only under `SHARED`, conn 0 for no owner (connection
/// ids start at 1). A reader racing a writer may see half of a change; the answer is then "no" or is checked again under the lock, and the two cannot differ
/// for a connection that owns the engine before and after, because only its own thread, the one asking, changes that.
static OWNER_CONN: AtomicU64 = AtomicU64::new(0);
static OWNER_CTX: AtomicU64 = AtomicU64::new(0);
/// The engine was loaded with the rerank on (`load`), so jobs exist.
static RERANK: AtomicBool = AtomicBool::new(false);
/// How long an Enter waits for the rerank of its composition (`ENTER_RERANK_WAIT_MS` of the macOS shell, docs/spec/semantic-rerank.md §8.1).
const ENTER_WAIT: Duration = Duration::from_millis(100);
/// `ShanjieKey.kind` of Enter (shanjie.h).
const KIND_ENTER: u32 = 3;
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

/// The rerank lock; poisoned ends the process, like the engine lock.
fn rr() -> MutexGuard<'static, Slots<Job>> {
    RR.lock().unwrap_or_else(|_| exit(EXIT_POISONED))
}

/// The key scheme the installed engine was loaded with (`Welcome.key_scheme`).
pub fn key_scheme() -> u8 {
    KEY_SCHEME.load(Ordering::Acquire)
}

/// Puts a loaded engine in place; from now on clients are welcome. `scheme` and `rerank` are the ones `load` gave the engine. With the rerank on, the one
/// rerank thread of the process starts here; when it cannot be started the process ends (keys would leave jobs nobody runs).
pub fn install(e: *mut ShanjieEngine, scheme: u32, rerank: bool) {
    KEY_SCHEME.store(scheme as u8, Ordering::Release);
    RERANK.store(rerank, Ordering::Release);
    if rerank {
        static STARTED: std::sync::Once = std::sync::Once::new();
        STARTED.call_once(|| {
            // The thread never returns: coming back from the guard means it panicked. Without it the flag of every answer would stay set and every
            // Enter would wait its 100 ms for nothing, so the process ends and the next client starts a new host.
            let spawned = std::thread::Builder::new().spawn(|| {
                guard::run((), || rerank_thread());
                exit(EXIT_RERANK)
            });
            if spawned.is_err() {
                exit(EXIT_RERANK);
            }
        });
    }
    lock_wait().eng = Some(EnginePtr(e));
    READY.store(true, Ordering::Release);
}

// ---- the rerank thread ----

#[cfg(windows)]
fn below_normal() {
    use windows::Win32::System::Threading::{GetCurrentThread, SetThreadPriority, THREAD_PRIORITY_BELOW_NORMAL};
    // SAFETY: the pseudo handle of this thread; a failure leaves the normal priority.
    unsafe {
        let _ = SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_BELOW_NORMAL);
    }
}
#[cfg(not(windows))]
fn below_normal() {}

/// The one rerank thread: takes the waiting job, runs it with no lock held, and puts it back as finished, or frees it itself when nobody wants it any more
/// (checked under the rerank lock: the owner and the job's number). It calls only `shanjie_rerank_run` and `shanjie_rerank_free`: no engine, no `SHARED`.
fn rerank_thread() -> ! {
    below_normal();
    loop {
        let (token, job) = {
            let mut s = rr();
            loop {
                if let Some(next) = s.start() {
                    break next;
                }
                s = RR_CHANGED.wait(s).unwrap_or_else(|_| exit(EXIT_POISONED));
            }
        };
        #[cfg(test)]
        let _running = probe::Running::enter();
        let t0 = timing::now_us();
        // SAFETY: a live job that only this thread holds. A non-zero return (4: a caught panic) keeps no result: the apply then changes nothing.
        let _ = unsafe { core_ffi::shanjie_rerank_run(job.0) };
        timing::record(timing::EV_RERANK_RUN, t0, timing::now_us());
        #[cfg(test)]
        drop(_running);
        let mut s = rr();
        // An unwanted job comes back and is freed here, by this thread, under the rerank lock.
        drop(s.finish(token, job));
        drop(s);
        RR_CHANGED.notify_all();
    }
}

// ---- loading (docs/spec/windows-tip.md §3.4: the call list of cli/src/replay.rs `build_engine_packs`; set_rerank from the `Rerank` setting) ----

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

/// The engine of `dir/data` (the folder of this exe, §5.1), with key scheme `scheme` (0 or 1) and the semantic rerank on or off (`server::run` reads the registry and
/// passes both values, this function never reads it, so a test's engine does not depend on the machine it runs on). The rerank strength is the core's default (standard).
/// `None` when any call fails (the engine built so far is freed).
/// Done without the lock, so a load never holds it.
pub fn load(dir: &Path, scheme: u32, rerank: bool) -> Option<*mut ShanjieEngine> {
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
            && snap(|o| core_ffi::shanjie_engine_set_rerank(e, u32::from(rerank), o))
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
    // The warm-up's keys leave a rerank job when the rerank is on: taken and freed here, never run (the reset below makes it stale anyway).
    let mut j: *mut ShanjieRerankJob = ptr::null_mut();
    // SAFETY: caller contract; a job handed out is freed once, by `Job`.
    if unsafe { core_ffi::shanjie_engine_rerank_take(e, &mut j) } == 0 && !j.is_null() {
        #[cfg(test)]
        probe::TAKES.fetch_add(1, Ordering::SeqCst);
        drop(Job(j));
    }
    // SAFETY: caller contract.
    unsafe { reset(e) }
}

/// Decodes a fixed fake composition once on the freshly loaded `e` so the first real key does not meet cold model pages, then discards it:
/// no composition, no context, no learning (it is off). `e` is not installed yet, so `ready()` is still false. If the reset fails the engine
/// is freed and `dir` is loaded once more (with `scheme`), without a warm-up; `None` when that load fails too.
pub fn warmed(dir: &Path, scheme: u32, rerank: bool, e: *mut ShanjieEngine) -> Option<*mut ShanjieEngine> {
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
    load(dir, scheme, rerank)
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

/// Copies and frees one output of the core; `None` for NULL.
///
/// # Safety
/// `o` is NULL or a live output of the core that nothing else frees.
unsafe fn copy_out(o: *mut ShanjieOutput) -> Option<CoreOut> {
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
    Some(out)
}

/// # Safety
/// `e` is a live engine handle, used under the mutex.
unsafe fn send(e: *mut ShanjieEngine, k: Key) -> Option<CoreOut> {
    let mut o: *mut ShanjieOutput = ptr::null_mut();
    // SAFETY: caller contract; `o` is a valid out pointer.
    let rc = unsafe { core_ffi::shanjie_engine_key(e, ShanjieKey { kind: k.kind, ch: k.ch, modifiers: k.modifiers }, &mut o) };
    // SAFETY: the output of the call above.
    let out = unsafe { copy_out(o) };
    // A non-zero return means "not handled, do not record" (shanjie.h): no output.
    out.filter(|_| rc == 0)
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

fn wire(took_over: bool, out: Option<CoreOut>, pending: bool) -> Out {
    match out {
        Some(o) => Out {
            ran: true,
            core_error: false,
            took_over,
            handled: o.handled,
            pending,
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
            pending,
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

/// Changes the owner (to `me`, or to nobody) under the lock: the copy for `poll` follows, and everything that belonged to the old owner goes, its candidate
/// window flag, the applied snapshot and its rerank jobs (the one that is running is only marked unwanted; the rerank thread frees it).
fn own(g: &mut Shared, owner: Option<Owner>) {
    g.owner = owner;
    OWNER_CTX.store(owner.map_or(0, |o| o.ctx), Ordering::SeqCst);
    OWNER_CONN.store(owner.map_or(0, |o| o.conn), Ordering::SeqCst);
    g.window = false;
    g.snap = None;
    drop(rr().discard());
    // An Enter of the old owner that waits for its job (`catch_up`) has nothing left to wait for.
    RR_CHANGED.notify_all();
}

/// Applies a finished job and frees it. `Some`: the composition changed, this is all of it. `None`: nothing changed (stale, a window open, every stretch kept
/// its first). A code other than 0 and 5 means the core reset the engine in discard mode: nobody owns it then, so the client's next key takes it over and
/// ends the composition the engine no longer has (the text stays in the document).
///
/// # Safety
/// `e` is the live engine and `g` is the guard of its lock.
unsafe fn apply(g: &mut Shared, e: *mut ShanjieEngine, job: Job) -> Option<CoreOut> {
    let mut o: *mut ShanjieOutput = ptr::null_mut();
    // SAFETY: caller contract; the job's run has returned (it is a finished job) and it is freed below, after the apply.
    let (rc, out) = unsafe { (core_ffi::shanjie_engine_rerank_apply(e, job.0, &mut o), copy_out(o)) };
    drop(job);
    match rc {
        0 => out,
        core_ffi::SHANJIE_NO_CHANGE => None,
        _ => {
            own(g, None);
            None
        }
    }
}

/// An Enter of the owner with no candidate window (the macOS shell's `waitForRerank`): the composition catches up with its rerank before the Enter reaches the
/// core. A job that has run is applied at once. A job that is waiting or running is waited for, at most `ENTER_WAIT`, with `SHARED` released so every other
/// connection goes on; then `SHARED` is taken again (waiting for it: this key was already accepted), and the job is applied only if `me` still owns the engine.
/// The caller looks at the owner again afterwards. The rerank lock is never held while `SHARED` is taken.
fn catch_up(mut g: MutexGuard<'static, Shared>, e: *mut ShanjieEngine, me: Owner) -> MutexGuard<'static, Shared> {
    let mut s = rr();
    if let Some(job) = s.take_done(me) {
        drop(s);
        // SAFETY: the live engine, its lock held.
        unsafe { apply(&mut g, e, job) };
        return g;
    }
    if !s.in_flight(me) {
        return g;
    }
    drop(g);
    #[cfg(test)]
    probe::ENTER_WAITS.fetch_add(1, Ordering::SeqCst);
    let until = Instant::now() + ENTER_WAIT;
    while s.in_flight(me) {
        let left = until.saturating_duration_since(Instant::now());
        if left.is_zero() {
            break;
        }
        s = RR_CHANGED.wait_timeout(s, left).unwrap_or_else(|_| exit(EXIT_POISONED)).0;
    }
    drop(s);
    let mut g = lock_wait();
    if g.owner == Some(me) {
        let done = rr().take_done(me);
        if let Some(job) = done {
            // SAFETY: the live engine, its lock held.
            unsafe { apply(&mut g, e, job) };
        }
    }
    g
}

/// One key for `me`: take ownership (discarding another owner's state with mode 1), call the core, copy, take the rerank job the key left, unlock. The candidate
/// window is the core's vertical window (set in `load`); its rows, selection, first and total go to the client, which draws it (docs/spec/windows-tip.md §3.7, §5.3).
pub fn key(me: Owner, k: Key) -> Answer {
    let mut g = match SHARED.try_lock() {
        Ok(g) => g,
        Err(TryLockError::WouldBlock) => return Answer::Busy,
        Err(TryLockError::Poisoned(_)) => exit(EXIT_POISONED),
    };
    let Some(EnginePtr(e)) = g.eng else { return Answer::NotReady };
    // The answer to a key carries the whole composition, so a snapshot nobody confirmed is not needed any more; another owner's must go in any case.
    g.snap = None;
    if g.owner == Some(me) && k.kind == KIND_ENTER && !g.window {
        g = catch_up(g, e, me);
    }
    let took_over = g.owner != Some(me);
    // SAFETY: `e` is the live engine and the lock is held.
    let out = unsafe {
        if took_over {
            if g.owner.is_some() {
                reset(e);
            }
            own(&mut g, Some(me));
        }
        send(e, k)
    };
    if let Some(o) = &out {
        g.window = o.candidate_selected >= 0;
    }
    let mut j: *mut ShanjieRerankJob = ptr::null_mut();
    // SAFETY: the live engine, the lock held; `j` is a valid out pointer.
    let rc = unsafe { core_ffi::shanjie_engine_rerank_take(e, &mut j) };
    if !j.is_null() {
        #[cfg(test)]
        probe::TAKES.fetch_add(1, Ordering::SeqCst);
        // A new job: the waiting one and a finished one nobody asked for are freed here, the running one is left to the rerank thread.
        drop(rr().put(me, Job(j)));
        RR_CHANGED.notify_all();
    } else if rc != 0 {
        // The core reset the engine (code 4): as in `apply`.
        own(&mut g, None);
    } else if out.as_ref().is_some_and(|o| o.preedit.is_empty()) {
        // The composition ended (sent or emptied): what is left of its jobs is stale.
        drop(rr().discard());
    }
    let pending = rr().pending(me);
    drop(g);
    Answer::Out(wire(took_over, out, pending))
}

/// The fixed answer to a `Poll` of anyone who is not the owner: nothing of the owner's state is in it, and it is built without any lock.
const NOT_YOURS: Polled = Polled { pending: false, what: PollResult::Idle };

/// `Poll { ctx, snap }` of `me`; `acked` is the number of the last snapshot the client wrote. Never waits: the engine lock is tried, and a busy engine is
/// "still running". Only the owner gets anything, and only under the lock is anything applied:
/// - not the owner by the lock-free copy: `NOT_YOURS`, no lock touched; the owner by the copy: the lock is tried and the real owner checked again;
/// - a snapshot applied earlier that the client has not confirmed is sent again, the same one, until `acked` names it (then it is cleared);
/// - a finished job is applied: a change becomes the snapshot (numbered, kept) and is sent; no change is "idle";
/// - a job waiting or running is "running".
pub fn poll(me: Owner, acked: u64) -> Polled {
    if OWNER_CONN.load(Ordering::SeqCst) != me.conn || OWNER_CTX.load(Ordering::SeqCst) != me.ctx {
        return NOT_YOURS;
    }
    let mut g = match SHARED.try_lock() {
        Ok(g) => g,
        Err(TryLockError::WouldBlock) => return Polled { pending: true, what: PollResult::Running },
        Err(TryLockError::Poisoned(_)) => exit(EXIT_POISONED),
    };
    let Some(EnginePtr(e)) = g.eng else { return NOT_YOURS };
    if g.owner != Some(me) {
        return NOT_YOURS;
    }
    match &g.snap {
        Some(s) if s.owner == me && s.id != acked => return Polled { pending: true, what: PollResult::Snapshot { snap: s.id, out: s.out.clone() } },
        // Confirmed, or (never, `own` clears it) someone else's.
        Some(_) => g.snap = None,
        None => {}
    }
    let done = rr().take_done(me);
    if let Some(job) = done {
        // SAFETY: the live engine, its lock held.
        if let Some(o) = unsafe { apply(&mut g, e, job) } {
            g.snaps += 1;
            let (id, out) = (g.snaps, wire(false, Some(o), true));
            g.snap = Some(Snapshot { owner: me, id, out: out.clone() });
            return Polled { pending: true, what: PollResult::Snapshot { snap: id, out } };
        }
        if g.owner != Some(me) {
            return NOT_YOURS;
        }
    }
    if rr().in_flight(me) {
        Polled { pending: true, what: PollResult::Running }
    } else {
        NOT_YOURS
    }
}

/// `Release { ctx }`: the composition of exactly this owner ended in the app; discard the engine's state if it is still theirs. The applied snapshot is
/// cleared whoever sent this.
pub fn release(me: Owner) {
    let mut g = lock_wait();
    g.snap = None;
    if g.owner == Some(me) {
        if let Some(EnginePtr(e)) = g.eng {
            // SAFETY: live engine, lock held.
            unsafe { reset(e) };
        }
        own(&mut g, None);
    }
}

/// `Reset` (after the client caught a panic) or the connection closed: discard the engine's state if this connection owns it, any ctx. The applied snapshot
/// is cleared whoever it was.
pub fn drop_connection(conn: u64) {
    let mut g = lock_wait();
    g.snap = None;
    if g.owner.is_some_and(|o| o.conn == conn) {
        if let Some(EnginePtr(e)) = g.eng {
            // SAFETY: live engine, lock held.
            unsafe { reset(e) };
        }
        own(&mut g, None);
    }
}

/// What the tests look at and hold (docs/spec/windows-tip.md §5.11): counters of the jobs, a gate that keeps a job "running", and the engine lock itself.
#[cfg(test)]
pub mod probe {
    use super::*;

    /// Jobs the core handed out, and jobs freed.
    pub static TAKES: AtomicU64 = AtomicU64::new(0);
    pub static FREES: AtomicU64 = AtomicU64::new(0);
    /// Runs in progress right now, the most there ever were at once, and runs finished.
    pub static RUNNING: AtomicU64 = AtomicU64::new(0);
    pub static RUNNING_MAX: AtomicU64 = AtomicU64::new(0);
    pub static RUNS: AtomicU64 = AtomicU64::new(0);
    /// Enters that released the engine lock to wait for a job.
    pub static ENTER_WAITS: AtomicU64 = AtomicU64::new(0);
    /// Held by a test: the rerank thread stops with its job in hand, just before the run, until the test lets go.
    pub static GATE: Mutex<()> = Mutex::new(());

    pub struct Running;
    impl Running {
        pub fn enter() -> Running {
            let n = RUNNING.fetch_add(1, Ordering::SeqCst) + 1;
            RUNNING_MAX.fetch_max(n, Ordering::SeqCst);
            drop(GATE.lock().unwrap_or_else(std::sync::PoisonError::into_inner));
            Running
        }
    }
    impl Drop for Running {
        fn drop(&mut self) {
            RUNNING.fetch_sub(1, Ordering::SeqCst);
            RUNS.fetch_add(1, Ordering::SeqCst);
        }
    }

    /// The engine lock, held: a key then gets `Busy` and the owner's `poll` "running".
    pub fn hold() -> MutexGuard<'static, Shared> {
        lock_wait()
    }
    pub fn has_done(me: Owner) -> bool {
        rr().has_done(me)
    }
    pub fn in_flight(me: Owner) -> bool {
        rr().in_flight(me)
    }
    pub fn queued() -> bool {
        rr().has_queued()
    }
    /// No job waits and the rerank thread holds none: every job it had to free is freed.
    pub fn idle() -> bool {
        rr().is_idle()
    }
    pub fn owner() -> Option<Owner> {
        lock_wait().owner
    }
}
