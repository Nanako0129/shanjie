//! What the host's test files share (`tests`, on Windows against a real pipe server; `rerank_tests`, on every platform against the engine module): the checks that
//! name their step, the key tokens, the core driven directly (`Direct`), and the one engine installed in this test process.
use crate::engine;
use shanjie_core::ffi::{self as core_ffi, ShanjieEngine, ShanjieKey, ShanjieOutput, ShanjieRerankJob};
use shanjie_win_proto::Key;
use std::ffi::CStr;
use std::path::Path;
use std::ptr;
use std::sync::{Mutex, Once};

/// The tests that use the installed engine (and, on Windows, the server and the rate limit of this process) run one at a time.
pub static ONE_AT_A_TIME: Mutex<()> = Mutex::new(());

/// The R2 guard installs a silent panic hook for the whole process (guard.rs), so a plain `assert!` or `expect` fails with no
/// message. Each check names its step on stderr first (libtest prints captured stderr of a failed test); the names are test labels,
/// never composition text.
pub fn fail(what: &str) -> ! {
    eprintln!("check failed: {what}");
    panic!("check failed");
}

pub fn check(ok: bool, what: &str) {
    if !ok {
        fail(what);
    }
}

pub trait Need<T> {
    fn need(self, what: &str) -> T;
}

impl<T> Need<T> for Option<T> {
    fn need(self, what: &str) -> T {
        self.unwrap_or_else(|| fail(what))
    }
}

impl<T, E: std::fmt::Debug> Need<T> for Result<T, E> {
    fn need(self, what: &str) -> T {
        match self {
            Ok(v) => v,
            Err(e) => {
                eprintln!("error: {e:?}");
                fail(what)
            }
        }
    }
}

pub fn w(s: &str) -> Vec<u16> {
    s.encode_utf16().collect()
}

/// A character key (kind 1, no modifier) and a key of the other kinds of shanjie.h.
pub fn chr(c: char) -> Key {
    Key { kind: 1, ch: c as u32, modifiers: 0 }
}

pub fn kind(kind: u32) -> Key {
    Key { kind, ch: 0, modifiers: 0 }
}

pub const SPACE: u32 = 2;
pub const ENTER: u32 = 3;
pub const ESC: u32 = 6;
pub const DOWN: u32 = 10;
pub const PAGE_DOWN: u32 = 15;

pub fn repo_root() -> &'static Path {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().and_then(Path::parent).need("repository root")
}

/// The one engine of this test process, installed once: key scheme 0 (the tests that use it were written for the current rules) and the semantic rerank on,
/// as the host starts with no `Rerank` value. The models are those of data/ (scripts/fetch-models.sh), as for the core's tests.
pub fn installed() {
    static ONCE: Once = Once::new();
    ONCE.call_once(|| engine::install(engine::load(repo_root(), 0, true).need("the engine loads from data/ (scripts/fetch-models.sh)"), 0, true));
}

/// No connection of an earlier test is still being cleaned up by the pipe server (its `drop_connection` comes after the client closed, on the server's
/// thread, and clears the applied snapshot whoever owns it). Off Windows there is no server.
pub fn quiet() {
    #[cfg(windows)]
    {
        let until = std::time::Instant::now() + std::time::Duration::from_secs(30);
        while crate::server::live() != 0 {
            check(std::time::Instant::now() < until, "the connections of the test before are closed");
            std::thread::sleep(std::time::Duration::from_millis(5));
        }
    }
}

/// One key token: a character is its keycap on the standard layout; `space`, `enter`, `esc`, `down`, `up`, `pgup`, `pgdn`, `bksp` and the others below are the
/// other keys; `S+` Shift, `C+` Control (MOD_SHIFT 1, MOD_CONTROL 2 of shanjie.h), in front of any other token.
pub fn parse_keys(line: &str) -> Vec<Key> {
    line.split(' ')
        .map(|t| {
            let (modifiers, t) = match (t.strip_prefix("S+"), t.strip_prefix("C+")) {
                (Some(r), _) => (1, r),
                (_, Some(r)) => (2, r),
                _ => (0, t),
            };
            let k = match t {
                "space" => kind(SPACE),
                "enter" => kind(ENTER),
                "bksp" => kind(4),
                "del" => kind(5),
                "esc" => kind(ESC),
                "left" => kind(7),
                "right" => kind(8),
                "up" => kind(9),
                "down" => kind(DOWN),
                "home" => kind(11),
                "end" => kind(12),
                "tab" => kind(13),
                "pgup" => kind(14),
                "pgdn" => kind(PAGE_DOWN),
                t if t.chars().count() == 1 => chr(t.chars().next().need("a character")),
                _ => fail("an unknown key token"),
            };
            Key { modifiers, ..k }
        })
        .collect()
}

/// What one core output holds, copied and freed at once.
#[derive(Clone, PartialEq)]
pub struct Snap {
    pub handled: bool,
    pub commit: Vec<u16>,
    pub preedit: Vec<u16>,
    pub cursor: u32,
    pub selected: i32,
    pub first: u32,
    pub total: u32,
    pub candidates: Vec<Vec<u16>>,
}

/// # Safety
/// `o` is a live output of the core; it is freed here.
unsafe fn snap_of(o: *mut ShanjieOutput) -> Snap {
    // SAFETY: caller contract; the strings are valid NUL-terminated UTF-8 until freed, and the output is freed once, after the copy.
    unsafe {
        let r = &*o;
        let s = |p: *const std::os::raw::c_char| CStr::from_ptr(p).to_string_lossy().encode_utf16().collect::<Vec<u16>>();
        let candidates = if r.candidates.is_null() { Vec::new() } else { (0..r.candidate_count as usize).map(|i| s(*r.candidates.add(i))).collect() };
        let snap = Snap { handled: r.handled != 0, commit: s(r.commit), preedit: s(r.preedit), cursor: r.cursor_utf16, selected: r.candidate_selected, first: r.candidate_first, total: r.candidate_total, candidates };
        core_ffi::shanjie_output_free(o);
        snap
    }
}

/// An engine of the host's own build (`engine::load`: the packs, the vertical window, the learning off), called with no pipe and no host in between. The rerank
/// is off unless `with` asks for it; then `settle` plays the host's take, run and apply in one call, on this thread.
pub struct Direct(pub *mut ShanjieEngine);

impl Direct {
    /// Key scheme 0, named: the tests that use it were written for the current rules. No rerank: every answer is the n-gram's.
    #[cfg_attr(not(windows), allow(dead_code))]
    pub fn new() -> Direct {
        Direct::with_scheme(0)
    }

    #[cfg_attr(not(windows), allow(dead_code))]
    pub fn with_scheme(scheme: u32) -> Direct {
        Direct::with(scheme, false)
    }

    pub fn with(scheme: u32, rerank: bool) -> Direct {
        Direct(engine::load(repo_root(), scheme, rerank).need("the engine loads from data/ (scripts/fetch-models.sh)"))
    }

    pub fn key(&self, k: Key) -> Snap {
        let mut o: *mut ShanjieOutput = ptr::null_mut();
        // SAFETY: a live engine of this struct; `o` is a valid out pointer.
        let rc = unsafe { core_ffi::shanjie_engine_key(self.0, ShanjieKey { kind: k.kind, ch: k.ch, modifiers: k.modifiers }, &mut o) };
        check(rc == 0 && !o.is_null(), "the core accepts the key");
        // SAFETY: the live output of the call above.
        unsafe { snap_of(o) }
    }

    pub fn reset(&self) {
        let mut o: *mut ShanjieOutput = ptr::null_mut();
        // SAFETY: a live engine; the snapshot is freed at once.
        unsafe {
            check(core_ffi::shanjie_engine_reset(self.0, 1, &mut o) == 0, "the core resets");
            core_ffi::shanjie_output_free(o);
        }
    }

    /// The job the last change left, run and applied here and now. `None` when there was no job; `Some(None)` when the job changed nothing; else the
    /// composition after the rerank.
    pub fn settle(&self) -> Option<Option<Snap>> {
        let mut j: *mut ShanjieRerankJob = ptr::null_mut();
        // SAFETY: a live engine used on this thread only; the job is run, applied and freed once, in that order.
        unsafe {
            check(core_ffi::shanjie_engine_rerank_take(self.0, &mut j) == 0, "the core hands out the job");
            if j.is_null() {
                return None;
            }
            check(core_ffi::shanjie_rerank_run(j) == 0, "the job runs");
            let mut o: *mut ShanjieOutput = ptr::null_mut();
            let rc = core_ffi::shanjie_engine_rerank_apply(self.0, j, &mut o);
            core_ffi::shanjie_rerank_free(j);
            check(rc == 0 || rc == core_ffi::SHANJIE_NO_CHANGE, "the apply changes the composition or says it did not");
            Some((rc == 0).then(|| snap_of(o)))
        }
    }

    /// Types `keys` (tokens of `parse_keys`), settling the rerank after every key as a host whose rerank is always finished in time would, then Enter:
    /// (the composition just before the Enter, everything committed, how many keys left a job).
    pub fn typed(&self, keys: &str) -> (Vec<u16>, Vec<u16>, u32) {
        self.reset();
        let (mut shown, mut sent, mut jobs) = (Vec::new(), Vec::new(), 0);
        for k in parse_keys(keys) {
            let s = self.key(k);
            sent.extend_from_slice(&s.commit);
            shown = s.preedit;
            if let Some(after) = self.settle() {
                jobs += 1;
                if let Some(a) = after {
                    shown = a.preedit;
                }
            }
        }
        sent.extend_from_slice(&self.key(kind(ENTER)).commit);
        (shown, sent, jobs)
    }
}

impl Drop for Direct {
    fn drop(&mut self) {
        // SAFETY: the engine of `with`, freed once.
        unsafe { core_ffi::shanjie_engine_free(self.0) };
    }
}
