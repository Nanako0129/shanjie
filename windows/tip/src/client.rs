//! The thin client (docs/spec/windows-tip.md §5.2, §5.4, §5.5 item 1 and §5.6): the text service no longer holds an
//! engine; each key goes to the resident host over a named pipe and back, synchronously, inside the key's edit session.
//!
//! - One connection per UI thread (one TIP instance per thread), opened by the first key, never in `ActivateEx`. A thread timer closes
//!   it after 60 s idle with no composition; it is also closed on `Deactivate`, after any failure and after any answer but `Out`.
//! - Before anything is sent the host is checked (§5.5 item 1): the pipe's owner SID and session (handle only), then, outside an
//!   AppContainer, the server process's token and image path. A failure passes the key through.
//! - Every exchange has a 200 ms deadline; a wait that runs out is cancelled and its cancellation awaited (`pipeio`). Two timeouts in
//!   a row open the circuit breaker for 10 s. `Refuse { 1 }` passes every key through for the life of the process, `Refuse { 3 }`
//!   for 60 s.
//! - `ERROR_FILE_NOT_FOUND` starts the host without waiting for it, only from a process that meets every condition of §5.2.
//!
//! The host is the only holder of composition state: nothing typed is kept or replayed here. R2: no key or text reaches a log, a file
//! or an error; the timing file gets numbers only.
use crate::guard;
use crate::pipeio::{self, Pipe, ERR_BAD_DATA, ERR_TIMEOUT};
use crate::timing::{self, pass};
use crate::token::{self, Facts};
use shanjie_win_proto::{self as proto, ClientMsg, Key, Out, ServerMsg};
use std::cell::RefCell;
use std::os::windows::ffi::OsStrExt;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};
use windows::core::{PCWSTR, PWSTR};
use windows::Win32::Foundation::{CloseHandle, LocalFree, ERROR_FILE_NOT_FOUND, ERROR_PIPE_BUSY, ERROR_SUCCESS, HANDLE, HLOCAL, HWND, WAIT_OBJECT_0};
use windows::Win32::Globalization::{CompareStringOrdinal, CSTR_EQUAL};
use windows::Win32::Security::Authorization::{GetSecurityInfo, SE_KERNEL_OBJECT};
use windows::Win32::Security::{OWNER_SECURITY_INFORMATION, PSECURITY_DESCRIPTOR, PSID, SECURITY_ATTRIBUTES, TOKEN_QUERY};
use windows::Win32::Storage::FileSystem::{
    CreateFileW, FILE_FLAG_OVERLAPPED, FILE_GENERIC_READ, FILE_GENERIC_WRITE, FILE_SHARE_MODE, FILE_SHARE_READ, FILE_SHARE_WRITE, OPEN_EXISTING, SECURITY_IDENTIFICATION,
    SECURITY_SQOS_PRESENT, FILE_FLAGS_AND_ATTRIBUTES,
};
use windows::Win32::System::Pipes::{GetNamedPipeServerProcessId, GetNamedPipeServerSessionId, PeekNamedPipe};
use windows::Win32::System::SystemInformation::GetSystemWindowsDirectoryW;
use windows::Win32::System::Threading::{
    CreateProcessW, DeleteProcThreadAttributeList, GetCurrentProcessId, GetCurrentThreadId, InitializeProcThreadAttributeList, OpenProcess, OpenProcessToken,
    QueryFullProcessImageNameW, UpdateProcThreadAttribute, WaitForSingleObject, CREATE_BREAKAWAY_FROM_JOB, CREATE_NO_WINDOW, CREATE_UNICODE_ENVIRONMENT, DETACHED_PROCESS,
    EXTENDED_STARTUPINFO_PRESENT, LPPROC_THREAD_ATTRIBUTE_LIST, PROCESS_INFORMATION, PROCESS_NAME_WIN32, PROCESS_QUERY_LIMITED_INFORMATION, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
    STARTF_USESTDHANDLES, STARTUPINFOEXW, STARTUPINFOW,
};
use windows::Win32::UI::WindowsAndMessaging::{KillTimer, SetTimer};

/// §5.4's 200 ms deadline. The in-DLL engine measured a per-key p95 of 5.5 ms and a maximum of 12.5 ms (§3.13); 200 ms is far above both.
const REPLY: Duration = Duration::from_millis(200);
const IDLE: Duration = Duration::from_secs(60);
/// How often the idle check runs (the connection closes between 60 and 75 s idle).
const IDLE_TICK_MS: u32 = 15_000;
const BREAKER: Duration = Duration::from_secs(10);
const COOLDOWN: Duration = Duration::from_secs(60);
const BACKOFF: Duration = Duration::from_secs(10);
const STILL_MISSING: Duration = Duration::from_secs(15);
/// `FILE_READ_DATA | FILE_WRITE_DATA | SYNCHRONIZE | READ_CONTROL` (§5.5 item 2): exactly what the pipe's DACL grants an
/// AppContainer. `GENERIC_READ`/`GENERIC_WRITE` would ask for more and make every AppContainer look shut out.
pub const PIPE_ACCESS: u32 = 0x0000_0001 | 0x0000_0002 | 0x0010_0000 | 0x0002_0000;
/// §5.5 item 3: the host can identify the client, never act as it.
pub const PIPE_FLAGS: FILE_FLAGS_AND_ATTRIBUTES = FILE_FLAGS_AND_ATTRIBUTES(FILE_FLAG_OVERLAPPED.0 | SECURITY_SQOS_PRESENT.0 | SECURITY_IDENTIFICATION.0);
const HOST_EXE: &str = "shanjie-host.exe";
/// The one fixed argument of the host's command line (§5.2).
const HOST_FLAG: &str = "--resident";

pub enum Reply {
    /// The host's answer (checked: seq, caret, the candidate window's fields, no control characters; `proto::out_ok`).
    Out(Out),
    /// No answer to use: pass the key through (or, with a composition open, end it; §5.6). The connection is closed.
    Pass,
}

// ---- this process ----

struct Me {
    facts: Facts,
    /// The pipe name, NUL-terminated.
    pipe: Vec<u16>,
    dir: PathBuf,
    host: PathBuf,
    may_connect: bool,
    may_launch: bool,
}

fn me() -> Option<&'static Me> {
    static ME: OnceLock<Option<Me>> = OnceLock::new();
    ME.get_or_init(|| {
        let facts = token::process_facts()?;
        // The host sits beside this DLL (§5.1); a path that is not complete gives no client at all (dll::module_path).
        let dir = crate::dll::module_path()?.parent()?.to_path_buf();
        let sid = token::sid_string(&facts.user)?;
        let pipe = proto::pipe_name(&sid, facts.session).encode_utf16().chain([0]).collect();
        let may_connect = !token::is_service_account(&facts.user) && facts.integrity < token::SYSTEM_RID;
        let may_launch = may_connect
            && facts.integrity == token::MEDIUM_RID
            && facts.app_container.is_none()
            && !facts.elevated
            && !facts.restricted
            && facts.session != 0
            && !token::has_package();
        Some(Me { host: dir.join(HOST_EXE), dir, pipe, facts, may_connect, may_launch })
    })
    .as_ref()
}

/// Milliseconds since this process first asked (for the process-wide deadlines below, kept in atomics).
fn now_ms() -> u64 {
    static BASE: OnceLock<Instant> = OnceLock::new();
    BASE.get_or_init(Instant::now).elapsed().as_millis() as u64
}

fn later(d: Duration) -> u64 {
    now_ms() + d.as_millis() as u64
}

/// `Refuse { 1 }`: a host of another protocol version; only restarting the app helps (§5.6).
static PERMANENT: AtomicBool = AtomicBool::new(false);
static BREAKER_UNTIL: AtomicU64 = AtomicU64::new(0);
static COOLDOWN_UNTIL: AtomicU64 = AtomicU64::new(0);
static TIMEOUTS: AtomicU32 = AtomicU32::new(0);
/// A connection (with its thread timer and thread-local destructor) has existed in this process.
static ATTEMPTED: AtomicBool = AtomicBool::new(false);

/// Every key passes through: the circuit breaker, the permanent pass-through or the `Refuse { 3 }` cool-down (§5.4). The only
/// connection state `OnTestKeyDown` looks at.
pub fn blocked() -> bool {
    let now = now_ms();
    PERMANENT.load(Ordering::Acquire) || now < BREAKER_UNTIL.load(Ordering::Acquire) || now < COOLDOWN_UNTIL.load(Ordering::Acquire)
}

/// The DLL must stay loaded: a thread timer or a thread-local destructor of this DLL may exist.
pub fn attempted() -> bool {
    ATTEMPTED.load(Ordering::Acquire)
}

fn failed(code: u32) {
    timing::diag_value(timing::EV_IO_FAILED, u64::from(code));
    if code == ERR_TIMEOUT {
        if TIMEOUTS.fetch_add(1, Ordering::AcqRel) + 1 >= 2 {
            BREAKER_UNTIL.store(later(BREAKER), Ordering::Release);
            TIMEOUTS.store(0, Ordering::Release);
        }
    } else {
        TIMEOUTS.store(0, Ordering::Release);
    }
}

fn passed(reason: u64) {
    timing::diag_value(timing::EV_PASS, reason);
}

/// Any answer but the one wanted (§5.6): the reason is noted and its rule applied.
fn refused(m: &ServerMsg) {
    TIMEOUTS.store(0, Ordering::Release);
    passed(match m {
        ServerMsg::Refuse { reason: proto::REFUSE_PROTO } => {
            PERMANENT.store(true, Ordering::Release);
            pass::REFUSE_PROTO
        }
        ServerMsg::Refuse { reason: proto::REFUSE_CLIENT } => {
            COOLDOWN_UNTIL.store(later(COOLDOWN), Ordering::Release);
            pass::REFUSE_CLIENT
        }
        // Rate-limited, or a code this build does not know: this key passes, the next one connects again.
        ServerMsg::Refuse { .. } => pass::REFUSE_LIMIT,
        ServerMsg::NotReady => pass::NOT_READY,
        ServerMsg::Busy => pass::BUSY,
        _ => pass::UNEXPECTED,
    });
}

// ---- the connection of this thread ----

struct Link {
    pipe: Pipe,
    seq: u64,
    last: Instant,
    /// The last answer had a composition (non-empty preedit): no idle close then.
    composing: bool,
    /// The thread timer of the idle check (0: none).
    timer: usize,
    /// The host engine's key scheme (0 or 1), from this connection's `Welcome`; the only place the text service learns it (docs/spec/windows-tip.md §3.6).
    scheme: u8,
}

impl Drop for Link {
    fn drop(&mut self) {
        if self.timer != 0 {
            // SAFETY: a timer this thread set; failure is ignored.
            unsafe {
                let _ = KillTimer(None, self.timer);
            }
        }
    }
}

thread_local! {
    static LINK: RefCell<Option<Link>> = const { RefCell::new(None) };
}

impl Link {
    fn send(&mut self, m: &ClientMsg, deadline: Instant) -> Result<u64, u32> {
        self.seq += 1;
        let f = proto::encode_client(self.seq, m).ok_or(ERR_BAD_DATA)?;
        self.pipe.write_all(&f, Some(deadline))?;
        Ok(self.seq)
    }

    /// The answer to the message `seq`; a `Pushed` in between is dropped (reserved, §5.3).
    fn answer(&mut self, seq: u64, deadline: Instant) -> Result<ServerMsg, u32> {
        loop {
            let f = self.pipe.read_frame(Some(deadline))?;
            let (s, m) = proto::decode_server(&f).ok_or(ERR_BAD_DATA)?;
            if matches!(m, ServerMsg::Pushed(_)) {
                continue;
            }
            if s != seq {
                return Err(ERR_BAD_DATA);
            }
            if let ServerMsg::Out(o) = &m {
                if !o.ran || !proto::out_ok(o) {
                    return Err(ERR_BAD_DATA);
                }
            }
            return Ok(m);
        }
    }
}

/// How a key exchange ended without an `Out`.
enum Step {
    /// The host did not get the key (§5.4: reconnect once, send once more).
    Write(u32),
    /// Sent; the answer failed or was malformed: never sent again (the host may have acted on it).
    Read(u32),
    Answer(ServerMsg),
}

fn call(link: &mut Link, ctx: u64, k: Key) -> Result<Out, Step> {
    let deadline = Instant::now() + REPLY;
    let seq = link.send(&ClientMsg::Key { ctx, key: k }, deadline).map_err(Step::Write)?;
    let t0 = timing::now_us();
    match link.answer(seq, deadline) {
        Ok(ServerMsg::Out(o)) => {
            timing::record(timing::EV_WAIT, t0, timing::now_us());
            TIMEOUTS.store(0, Ordering::Release);
            link.last = Instant::now();
            link.composing = !o.preedit.is_empty();
            Ok(o)
        }
        Ok(m) => Err(Step::Answer(m)),
        Err(e) => Err(Step::Read(e)),
    }
}

/// One key for `ctx` (this thread's context; its meaning stays inside this connection). `composing`: this instance has a composition
/// open; then a failed write is not sent again, because the host's state for that composition is gone (§5.6, first row).
pub fn key(ctx: u64, k: Key, composing: bool) -> Reply {
    if blocked() {
        return Reply::Pass;
    }
    let Some(me) = me() else { return Reply::Pass };
    LINK.try_with(|cell| {
        let Ok(mut slot) = cell.try_borrow_mut() else { return Reply::Pass };
        for again in [false, true] {
            if slot.is_none() {
                *slot = connect(me);
            }
            let Some(link) = slot.as_mut() else { return Reply::Pass };
            match call(link, ctx, k) {
                Ok(o) => return Reply::Out(o),
                Err(step) => {
                    *slot = None;
                    match step {
                        Step::Write(e) => {
                            failed(e);
                            if composing || again {
                                return Reply::Pass;
                            }
                        }
                        Step::Read(e) => {
                            failed(e);
                            return Reply::Pass;
                        }
                        Step::Answer(m) => {
                            refused(&m);
                            return Reply::Pass;
                        }
                    }
                }
            }
        }
        Reply::Pass
    })
    .unwrap_or(Reply::Pass)
}

/// Called from `OnTestKeyDown` with an empty composition (§5.4): makes sure this thread has a working connection, connecting (and
/// starting the host when the pipe is missing and this process may) as `key` would. `false` means the key must pass through, and
/// `OnTestKeyDown` must say so itself: in an IMM32 (CUAS) host such as the Run dialog, a key `OnTestKeyDown` claimed reaches the app
/// as `VK_PROCESSKEY` and is lost when `OnKeyDown` then lets it go (seen in the VM: the passed-through `s` never appeared).
pub fn ready() -> bool {
    if blocked() {
        return false;
    }
    let Some(me) = me() else { return false };
    LINK.try_with(|cell| {
        let Ok(mut slot) = cell.try_borrow_mut() else { return false };
        // A connection kept from an earlier key may point at a host that has since ended: a broken pipe fails a zero-byte peek at
        // once (no wait), so it is dropped and a new one made here, before the key is claimed.
        // SAFETY: a pipe handle this connection owns; no buffers.
        if slot.as_ref().is_some_and(|l| unsafe { PeekNamedPipe(l.pipe.handle(), None, 0, None, None, None) }.is_err()) {
            *slot = None;
        }
        if slot.is_none() {
            *slot = connect(me);
        }
        slot.is_some()
    })
    .unwrap_or(false)
}

/// The key scheme of this thread's live connection (from its `Welcome`), or `None` when the thread has none (not connected yet, or the link is in use).
/// Never connects: `OnTestKeyDown` asks this first, and connects only for a key the answer cannot decide (docs/spec/windows-tip.md §3.6).
pub fn scheme() -> Option<u8> {
    LINK.try_with(|cell| cell.try_borrow().ok().and_then(|slot| slot.as_ref().map(|l| l.scheme))).ok().flatten()
}

/// A message without an answer (`Release`, `Reset`); a failed write closes the connection.
fn tell(m: ClientMsg) {
    let _ = LINK.try_with(|cell| {
        let Ok(mut slot) = cell.try_borrow_mut() else { return };
        let ok = match slot.as_mut() {
            Some(link) => {
                let sent = link.send(&m, Instant::now() + REPLY).is_ok();
                link.composing = false;
                link.last = Instant::now();
                sent
            }
            None => true,
        };
        if !ok {
            *slot = None;
        }
    });
}

/// This instance's composition in `ctx` ended (focus moved, `Deactivate`, the app ended it, a failed edit): the host drops it if this
/// connection and `ctx` still own its engine.
pub fn release(ctx: u64) {
    tell(ClientMsg::Release { ctx });
}

/// After a caught panic: the host resets whatever this connection owns, and the connection is closed (an exchange may have been cut
/// in the middle).
pub fn reset_after_panic() {
    tell(ClientMsg::Reset { ctx: 0 });
    close();
}

/// `Deactivate`: this thread's connection closes.
pub fn close() {
    let _ = LINK.try_with(|cell| {
        if let Ok(mut slot) = cell.try_borrow_mut() {
            *slot = None;
        }
    });
}

/// The idle check (a thread timer: it runs on this UI thread, from its message loop). It never waits and never runs inside a key's
/// exchange (the link is borrowed then).
unsafe extern "system" fn idle_tick(_hwnd: HWND, _msg: u32, id: usize, _time: u32) {
    guard::run(
        (),
        || {
            let _ = LINK.try_with(|cell| {
                let Ok(mut slot) = cell.try_borrow_mut() else { return };
                match slot.as_ref() {
                    Some(l) if l.timer == id => {
                        if !l.composing && l.last.elapsed() >= IDLE {
                            *slot = None;
                        }
                    }
                    // A timer whose connection is gone.
                    _ => {
                        // SAFETY: a timer of this thread.
                        unsafe {
                            let _ = KillTimer(None, id);
                        }
                    }
                }
            });
        },
        || {},
    );
}

// ---- connecting ----

/// Opens the pipe exactly as §5.5 items 2 and 3 say (access and impersonation level). The Win32 code on failure.
pub fn open(name: &[u16]) -> Result<Pipe, u32> {
    // SAFETY: `name` is NUL-terminated.
    let h = unsafe { CreateFileW(PCWSTR(name.as_ptr()), PIPE_ACCESS, FILE_SHARE_MODE(0), None, OPEN_EXISTING, PIPE_FLAGS, None) }.map_err(|e| pipeio::win32(&e))?;
    Pipe::new(h).ok_or(ERR_BAD_DATA)
}

fn connect(me: &Me) -> Option<Link> {
    if !me.may_connect {
        passed(pass::NO_CONNECT);
        return None;
    }
    let pipe = match open(&me.pipe) {
        Ok(p) => p,
        Err(e) if e == ERROR_FILE_NOT_FOUND.0 => {
            launch(me);
            return None;
        }
        Err(e) if e == ERROR_PIPE_BUSY.0 => {
            passed(pass::PIPE_BUSY);
            return None;
        }
        // ERROR_ACCESS_DENIED for a sandbox the DACL does not let in, among others.
        Err(e) => {
            failed(e);
            return None;
        }
    };
    if !server_ok(me, pipe.handle()) {
        passed(pass::SERVER_CHECK);
        return None;
    }
    let mut link = Link { pipe, seq: 0, last: Instant::now(), composing: false, timer: 0, scheme: 0 };
    let deadline = Instant::now() + REPLY;
    // SAFETY: plain queries.
    let (pid, tid) = unsafe { (GetCurrentProcessId(), GetCurrentThreadId()) };
    let hello = ClientMsg::Hello { build_hash: proto::BUILD_HASH, pid, tid };
    match link.send(&hello, deadline).and_then(|seq| link.answer(seq, deadline)) {
        // A build hash that differs is allowed (a development build); the timing file has no event for it (numbers only, §5.8).
        Ok(ServerMsg::Welcome { key_scheme, .. }) => {
            link.scheme = key_scheme;
            TIMEOUTS.store(0, Ordering::Release);
            ATTEMPTED.store(true, Ordering::Release);
            // SAFETY: a thread timer; its procedure is guarded and this DLL never unloads once `attempted`.
            link.timer = unsafe { SetTimer(None, 0, IDLE_TICK_MS, Some(idle_tick)) };
            Some(link)
        }
        Ok(m) => {
            refused(&m);
            None
        }
        Err(e) => {
            failed(e);
            None
        }
    }
}

/// The pipe's owner SID, from the handle alone.
fn pipe_owner(h: HANDLE) -> Option<Vec<u8>> {
    let mut owner = PSID::default();
    let mut sd = PSECURITY_DESCRIPTOR::default();
    // SAFETY: valid out pointers; the descriptor is LocalAlloc memory, freed after the SID is copied out of it.
    unsafe {
        if GetSecurityInfo(h, SE_KERNEL_OBJECT, OWNER_SECURITY_INFORMATION, Some(&mut owner), None, None, None, Some(&mut sd)) != ERROR_SUCCESS {
            return None;
        }
        let b = token::sid_bytes(owner);
        let _ = LocalFree(Some(HLOCAL(sd.0)));
        b
    }
}

/// §5.5 item 1: (a) the pipe's owner is this user and the server is in this session (every client, AppContainer included);
/// (b) outside an AppContainer, the server process's token is this user at exactly Medium, not an AppContainer, not restricted; (c)
/// its image is the `shanjie-host.exe` beside this DLL, as a supplement. An AppContainer cannot open the server process, so it gets
/// (a) only: the residual risk §5.5 item 1 accepts.
fn server_ok(me: &Me, h: HANDLE) -> bool {
    let mut session = u32::MAX;
    // SAFETY: valid out pointer.
    let same_session = unsafe { GetNamedPipeServerSessionId(h, &mut session) }.is_ok() && session == me.facts.session;
    if !same_session || pipe_owner(h).as_deref() != Some(me.facts.user.as_slice()) {
        return false;
    }
    if me.facts.app_container.is_some() {
        return true;
    }
    let mut pid = 0u32;
    // SAFETY: valid out pointer.
    if unsafe { GetNamedPipeServerProcessId(h, &mut pid) }.is_err() {
        return false;
    }
    // SAFETY: a query-only handle, closed below.
    let Ok(p) = (unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) }) else { return false };
    let ok = server_token_ok(me, p) && image_ok(me, p);
    // SAFETY: opened above.
    unsafe {
        let _ = CloseHandle(p);
    }
    ok
}

fn server_token_ok(me: &Me, p: HANDLE) -> bool {
    let mut t = HANDLE::default();
    // SAFETY: valid out pointer; the token is closed below.
    if unsafe { OpenProcessToken(p, TOKEN_QUERY, &mut t) }.is_err() {
        return false;
    }
    let f = token::facts(t);
    // SAFETY: opened above.
    unsafe {
        let _ = CloseHandle(t);
    }
    f.is_some_and(|f| f.user == me.facts.user && f.integrity == token::MEDIUM_RID && f.app_container.is_none() && !f.restricted)
}

/// Win32 path, compared ordinally without case; a path that cannot be read fails.
fn image_ok(me: &Me, p: HANDLE) -> bool {
    let mut buf = vec![0u16; 32_768];
    let mut n = buf.len() as u32;
    // SAFETY: `buf` holds `n` units.
    if unsafe { QueryFullProcessImageNameW(p, PROCESS_NAME_WIN32, PWSTR(buf.as_mut_ptr()), &mut n) }.is_err() {
        return false;
    }
    let want: Vec<u16> = me.host.as_os_str().encode_wide().collect();
    // SAFETY: two live slices.
    unsafe { CompareStringOrdinal(&buf[..n as usize], &want, true) == CSTR_EQUAL }
}

// ---- starting the host (§5.2) ----

struct Launched {
    at: Instant,
    /// The process handle (as a number, so the state can sit in a static) of a start that succeeded.
    process: Option<isize>,
    noted_missing: bool,
}

static LAUNCH: Mutex<Option<Launched>> = Mutex::new(None);

/// The pipe is not there. Starts the host when this process may, without waiting: this key passes through either way. One start at a
/// time per process (`try_lock`), at most one per 10 s.
fn launch(me: &Me) {
    if !me.may_launch {
        passed(pass::NOT_ELIGIBLE);
        return;
    }
    let Ok(mut g) = LAUNCH.try_lock() else {
        passed(pass::BACKOFF);
        return;
    };
    if let Some(l) = g.as_mut() {
        if let Some(h) = l.process {
            // SAFETY: a process handle this process owns; a zero wait.
            let exited = unsafe { WaitForSingleObject(HANDLE(h as *mut _), 0) } == WAIT_OBJECT_0;
            if !l.noted_missing && (exited || l.at.elapsed() >= STILL_MISSING) {
                l.noted_missing = true;
                passed(pass::STILL_MISSING);
            }
        }
        if l.at.elapsed() < BACKOFF {
            passed(pass::BACKOFF);
            return;
        }
    }
    let process = spawn(me);
    match process {
        Some(_) => timing::diag(timing::EV_LAUNCHED),
        None => passed(pass::LAUNCH_FAILED),
    }
    if let Some(old) = g.take().and_then(|l| l.process) {
        // SAFETY: the handle of the previous start, closed once.
        unsafe {
            let _ = CloseHandle(HANDLE(old as *mut _));
        }
    }
    *g = Some(Launched { at: Instant::now(), process, noted_missing: false });
}

fn wide_nul(s: &std::ffi::OsStr) -> Vec<u16> {
    s.encode_wide().chain([0]).collect()
}

/// `SystemRoot=<Windows folder>` and nothing else: no `PATH`, `__COMPAT_LAYER` or `RUST_*` of the host app reaches the host.
fn minimal_environment() -> Option<Vec<u16>> {
    let mut dir = vec![0u16; 512];
    // SAFETY: `dir` is a live buffer.
    let n = unsafe { GetSystemWindowsDirectoryW(Some(&mut dir)) } as usize;
    if n == 0 || n >= dir.len() {
        return None;
    }
    let mut env: Vec<u16> = "SystemRoot=".encode_utf16().collect();
    env.extend_from_slice(&dir[..n]);
    env.extend_from_slice(&[0, 0]);
    Some(env)
}

/// `CreateProcessW` of §5.2: the absolute path beside this DLL, a quoted command line with one fixed flag, the install folder
/// as the working directory, the minimal environment, only an inheritable NUL handle as the three standard handles (the handle list
/// lets nothing else through), no window, detached, out of the app's job: a job that refuses breakaway makes this fail, and then the
/// host is not started at all. The process handle on success.
fn spawn(me: &Me) -> Option<isize> {
    let app = wide_nul(me.host.as_os_str());
    let quote = u16::from(b'"');
    let mut cmd: Vec<u16> = std::iter::once(quote).chain(me.host.as_os_str().encode_wide()).chain([quote]).chain(format!(" {HOST_FLAG}").encode_utf16()).chain([0]).collect();
    let dir = wide_nul(me.dir.as_os_str());
    let env = minimal_environment()?;
    let nul_name: Vec<u16> = "NUL".encode_utf16().chain([0]).collect();
    let inherit = SECURITY_ATTRIBUTES { nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32, lpSecurityDescriptor: std::ptr::null_mut(), bInheritHandle: true.into() };
    // SAFETY: plain calls with live, NUL-terminated buffers; every handle and the attribute list are released before returning.
    unsafe {
        let nul = CreateFileW(PCWSTR(nul_name.as_ptr()), (FILE_GENERIC_READ | FILE_GENERIC_WRITE).0, FILE_SHARE_READ | FILE_SHARE_WRITE, Some(&inherit), OPEN_EXISTING, FILE_FLAGS_AND_ATTRIBUTES(0), None).ok()?;
        let mut size = 0usize;
        let _ = InitializeProcThreadAttributeList(None, 1, None, &mut size);
        // usize elements: the list is pointer-aligned.
        let mut list_buf = vec![0usize; size.div_ceil(std::mem::size_of::<usize>()).max(1)];
        let list = LPPROC_THREAD_ATTRIBUTE_LIST(list_buf.as_mut_ptr().cast());
        let mut pi = PROCESS_INFORMATION::default();
        let handles = [nul];
        let started = InitializeProcThreadAttributeList(Some(list), 1, None, &mut size).is_ok() && {
            let ok = UpdateProcThreadAttribute(list, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST as usize, Some(handles.as_ptr().cast()), std::mem::size_of_val(&handles), None, None).is_ok()
                && {
                    let si = STARTUPINFOEXW {
                        StartupInfo: STARTUPINFOW {
                            cb: std::mem::size_of::<STARTUPINFOEXW>() as u32,
                            dwFlags: STARTF_USESTDHANDLES,
                            hStdInput: nul,
                            hStdOutput: nul,
                            hStdError: nul,
                            ..Default::default()
                        },
                        lpAttributeList: list,
                    };
                    let flags = CREATE_NO_WINDOW | DETACHED_PROCESS | CREATE_BREAKAWAY_FROM_JOB | EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT;
                    CreateProcessW(PCWSTR(app.as_ptr()), Some(PWSTR(cmd.as_mut_ptr())), None, None, true, flags, Some(env.as_ptr().cast()), PCWSTR(dir.as_ptr()), &si.StartupInfo, &mut pi)
                        .is_ok()
                };
            DeleteProcThreadAttributeList(list);
            ok
        };
        let _ = CloseHandle(nul);
        if !started {
            return None;
        }
        let _ = CloseHandle(pi.hThread);
        Some(pi.hProcess.0 as isize)
    }
}

#[cfg(test)]
mod tests {
    //! §5.5 item 3 (§5.11 item 10): a test server impersonates a client opened with the production `open`, and the
    //! impersonation level it gets is `SecurityIdentification`.
    use super::open;
    use crate::pipeio::{self, Pipe};
    use std::time::{Duration, Instant};
    use windows::core::PCWSTR;
    use windows::Win32::Foundation::{CloseHandle, ERROR_IO_PENDING, ERROR_PIPE_CONNECTED, HANDLE, INVALID_HANDLE_VALUE};
    use windows::Win32::System::IO::{GetOverlappedResult, OVERLAPPED};
    use windows::Win32::Security::{GetTokenInformation, RevertToSelf, SecurityIdentification, TokenImpersonationLevel, SECURITY_IMPERSONATION_LEVEL, TOKEN_QUERY};
    use windows::Win32::Storage::FileSystem::{FILE_FLAG_OVERLAPPED, PIPE_ACCESS_DUPLEX};
    use windows::Win32::System::Pipes::{ConnectNamedPipe, CreateNamedPipeW, ImpersonateNamedPipeClient, PIPE_READMODE_BYTE, PIPE_REJECT_REMOTE_CLIENTS, PIPE_TYPE_BYTE, PIPE_WAIT};
    use windows::Win32::System::Threading::{GetCurrentThread, OpenThreadToken};

    #[test]
    fn the_host_can_only_identify_the_client() {
        let name: Vec<u16> = format!(r"\\.\pipe\shanjie-test-sqos-{}", std::process::id()).encode_utf16().chain([0]).collect();
        // SAFETY: plain calls; the handles are owned by `Pipe`.
        let server = unsafe {
            let h = CreateNamedPipeW(PCWSTR(name.as_ptr()), PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED, PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS, 1, 4096, 4096, 0, None);
            assert!(h != INVALID_HANDLE_VALUE, "test pipe created");
            Pipe::new(h).expect("event")
        };
        let client_name = name.clone();
        let client = std::thread::spawn(move || {
            let c = open(&client_name).expect("the production open reaches the test pipe");
            c.write_all(&[7], Some(Instant::now() + Duration::from_secs(5))).expect("one byte written");
            let mut done = [0u8; 1];
            let _ = c.read_exact(&mut done, Some(Instant::now() + Duration::from_secs(5)));
        });
        // SAFETY: `ov` stays alive until the connect has completed (a pending one is waited for with bWait = TRUE).
        unsafe {
            let mut ov = OVERLAPPED { hEvent: server.event(), ..Default::default() };
            if let Err(e) = ConnectNamedPipe(server.handle(), Some(&mut ov)) {
                if pipeio::win32(&e) == ERROR_IO_PENDING.0 {
                    let mut n = 0u32;
                    GetOverlappedResult(server.handle(), &ov, &mut n, true).expect("client connected");
                } else {
                    assert!(pipeio::win32(&e) == ERROR_PIPE_CONNECTED.0, "client connected");
                }
            }
        }
        // A pipe server can impersonate only after it has read from the client.
        let mut b = [0u8; 1];
        server.read_exact(&mut b, Some(Instant::now() + Duration::from_secs(5))).expect("byte read");
        let mut level = SECURITY_IMPERSONATION_LEVEL(-1);
        // SAFETY: impersonation is reverted before anything else runs on this thread.
        unsafe {
            ImpersonateNamedPipeClient(server.handle()).expect("impersonate");
            let mut t = HANDLE::default();
            let opened = OpenThreadToken(GetCurrentThread(), TOKEN_QUERY, true, &mut t);
            let mut len = 0u32;
            let read = opened.is_ok() && GetTokenInformation(t, TokenImpersonationLevel, Some((&mut level as *mut SECURITY_IMPERSONATION_LEVEL).cast()), 4, &mut len).is_ok();
            if opened.is_ok() {
                let _ = CloseHandle(t);
            }
            RevertToSelf().expect("revert");
            assert!(read, "the impersonation token can be read with OpenAsSelf");
        }
        assert!(level == SecurityIdentification, "the client allows identification only");
        server.write_all(&[1], Some(Instant::now() + Duration::from_secs(5))).expect("release the client");
        client.join().expect("client thread");
    }
}
