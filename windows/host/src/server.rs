//! Startup and the pipe server of the resident host (docs/spec/windows-tip.md §5.2, §5.4, §5.5, §5.6 and §5.8).
//!
//! - `run`: the self-check (exactly Medium, no AppContainer, not restricted, not elevated, not session 0, not a service account, no
//!   package identity), `SetDefaultDllDirectories(SYSTEM32)`, the WER exclusion, then all pipe instances, then the model. Any failure
//!   ends the process with a numeric exit code; the next client that may start a host starts a new one.
//! - All `INSTANCES` instances are created at start (the first with `FILE_FLAG_FIRST_PIPE_INSTANCE`: a name someone else holds ends
//!   this process) and reused forever; each has its own thread, and each connection runs inside `guard::run`.
//! - A connection: `Hello` within 1 s; the client's impersonation token is checked (same user, same session, every query must answer);
//!   `NotReady` while the model loads; at most `PER_CLIENT` connections per client (its AppContainer SID, else its PID: a rate limit,
//!   never authority); then `Welcome` and keys. A malformed message drops the connection without an answer.
//! - The host never acts as the client: the impersonation is only long enough to read the token, and `RevertToSelf` failing ends the
//!   process.
use crate::engine::{self, Answer, Owner};
use crate::guard;
use crate::pipeio::{self, Pipe};
use crate::timing;
use crate::token::{self, Facts};
use shanjie_win_proto::{self as proto, Accept, ClientMsg, HostSession, ServerMsg};
use std::collections::BTreeMap;
use std::os::windows::ffi::{OsStrExt, OsStringExt};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU32, AtomicU64, Ordering};
use std::sync::{Mutex, PoisonError};
use std::time::{Duration, Instant};
use windows::core::{w, PCWSTR};
use windows::Win32::Foundation::{CloseHandle, LocalFree, ERROR_IO_PENDING, ERROR_PIPE_CONNECTED, HANDLE, HLOCAL, INVALID_HANDLE_VALUE};
use windows::Win32::Security::Authorization::{ConvertStringSecurityDescriptorToSecurityDescriptorW, SDDL_REVISION_1};
use windows::Win32::Security::{RevertToSelf, PSECURITY_DESCRIPTOR, SECURITY_ATTRIBUTES, TOKEN_QUERY};
use windows::Win32::Storage::FileSystem::{FILE_FLAGS_AND_ATTRIBUTES, FILE_FLAG_FIRST_PIPE_INSTANCE, FILE_FLAG_OVERLAPPED, PIPE_ACCESS_DUPLEX};
use windows::Win32::System::ErrorReporting::WerAddExcludedApplication;
use windows::Win32::System::IO::{GetOverlappedResult, OVERLAPPED};
use windows::Win32::System::LibraryLoader::{GetModuleFileNameW, SetDefaultDllDirectories, LOAD_LIBRARY_SEARCH_SYSTEM32};
use windows::Win32::System::Pipes::{
    ConnectNamedPipe, CreateNamedPipeW, DisconnectNamedPipe, GetNamedPipeClientProcessId, ImpersonateNamedPipeClient, PIPE_READMODE_BYTE, PIPE_REJECT_REMOTE_CLIENTS, PIPE_TYPE_BYTE,
    PIPE_WAIT,
};
use windows::Win32::System::Registry::{RegGetValueW, HKEY_CURRENT_USER, RRF_RT_REG_DWORD};
use windows::Win32::System::Threading::{GetCurrentThread, OpenThreadToken, ResetEvent, WaitForSingleObject, INFINITE};

/// §5.2: N, checked against the high mark of event 65 in the VM run.
pub const INSTANCES: u32 = 64;
const PER_CLIENT: u32 = 4;
/// The only idle timeout: before the `Hello` (§5.4).
const HELLO: Duration = Duration::from_secs(1);
const WRITE: Duration = Duration::from_secs(1);
/// After a final answer (`Refuse`, `NotReady`) the host waits this long for the client to close, so the answer is read before
/// `DisconnectNamedPipe` throws unread data away.
const LINGER: Duration = Duration::from_secs(1);
const BUFFER: u32 = 4096;

// Exit codes (numbers only, R2).
pub const EXIT_SELF_CHECK: i32 = 2;
pub const EXIT_SETUP: i32 = 3;
pub const EXIT_PIPE: i32 = 4;
pub const EXIT_LOAD: i32 = 5;
pub const EXIT_POISONED: i32 = 6;
pub const EXIT_REVERT: i32 = 7;

pub fn exit(code: i32) -> ! {
    std::process::exit(code)
}

/// §5.5 item 2, the one SDDL string: the user gets `FILE_GENERIC_READ | FILE_GENERIC_WRITE` (no `WRITE_DAC`, `WRITE_OWNER`,
/// `DELETE`); ALL APPLICATION PACKAGES and ALL RESTRICTED APPLICATION PACKAGES get read data, write data, read attributes,
/// `SYNCHRONIZE` and `READ_CONTROL` only (no `FILE_CREATE_PIPE_INSTANCE`); no Everyone, no Users; a Low mandatory label (no write up).
fn sddl(user_sid: &str) -> String {
    format!("D:P(A;;0x12019F;;;{user_sid})(A;;0x120083;;;AC)(A;;0x120083;;;S-1-15-2-2)S:(ML;;NW;;;LW)")
}

fn soon(d: Duration) -> Option<Instant> {
    Some(Instant::now() + d)
}

/// The full path of this exe; a path that does not fit is `None` (no truncated prefix is used).
fn exe_path() -> Option<PathBuf> {
    let mut cap = 512usize;
    loop {
        let mut buf = vec![0u16; cap];
        // SAFETY: `buf` holds `cap` units.
        let n = unsafe { GetModuleFileNameW(None, &mut buf) } as usize;
        if n == 0 {
            return None;
        }
        if n < cap {
            return Some(PathBuf::from(std::ffi::OsString::from_wide(&buf[..n])));
        }
        if cap >= 65536 {
            return None;
        }
        cap *= 2;
    }
}

/// §5.2's self-check: the host only ever runs as the plain desktop user.
fn self_check(f: &Facts) -> bool {
    f.integrity == token::MEDIUM_RID && f.app_container.is_none() && !f.restricted && !f.elevated && f.session != 0 && !token::is_service_account(&f.user) && !token::has_package()
}

/// `HKCU\Software\shanjie-dev\KeyScheme` (a DWORD): the key scheme this host gives the core, and where it came from (event 71): 0 no value, 1 a valid
/// value (0 or 1), 2 an invalid one. A missing value is 1 (the Windows default follows Microsoft Zhuyin); so is every other error (a value of another type,
/// `ERROR_MORE_DATA`) and every DWORD other than 0 or 1: the host never passes the core a value it refuses (that would end the process). Only a process of this
/// user at Medium integrity or above can write the value, and such a process can already influence this host (docs/spec/windows-tip.md §5.5).
pub fn key_scheme_from(rc: u32, data: u32) -> (u32, u64) {
    const ERROR_SUCCESS: u32 = 0;
    const ERROR_FILE_NOT_FOUND: u32 = 2;
    const ERROR_PATH_NOT_FOUND: u32 = 3;
    match rc {
        ERROR_SUCCESS if data <= 1 => (data, 1),
        ERROR_FILE_NOT_FOUND | ERROR_PATH_NOT_FOUND => (1, 0),
        _ => (1, 2),
    }
}

fn read_key_scheme() -> (u32, u64) {
    let mut data = 0u32;
    let mut size = std::mem::size_of::<u32>() as u32;
    // SAFETY: `data` and `size` are live locals sized for one DWORD; the strings are NUL-terminated literals. `RRF_RT_REG_DWORD` refuses any other type.
    let rc = unsafe {
        RegGetValueW(
            HKEY_CURRENT_USER,
            w!(r"Software\shanjie-dev"),
            w!("KeyScheme"),
            RRF_RT_REG_DWORD,
            None,
            Some((&mut data as *mut u32).cast::<std::ffi::c_void>()),
            Some(&mut size),
        )
    };
    key_scheme_from(rc.0, data)
}

pub fn run() -> ! {
    guard::install_hook();
    let me = token::process_facts().filter(self_check).unwrap_or_else(|| exit(EXIT_SELF_CHECK));
    // SAFETY: plain calls with live arguments.
    unsafe { SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_SYSTEM32) }.unwrap_or_else(|_| exit(EXIT_SETUP));
    let exe = exe_path().unwrap_or_else(|| exit(EXIT_SETUP));
    // A dump of this process would hold every app's composition: WER leaves it out (HKCU, no administrator needed), named by the
    // executable's file name. Failing to exclude ends the process (no host is better than dumps with compositions).
    let exe_name: Vec<u16> = exe.file_name().unwrap_or_else(|| exit(EXIT_SETUP)).encode_wide().chain([0]).collect();
    // SAFETY: NUL-terminated.
    unsafe { WerAddExcludedApplication(PCWSTR(exe_name.as_ptr()), false) }.unwrap_or_else(|_| exit(EXIT_SETUP));
    timing::init("host");
    let sid = token::sid_string(&me.user).unwrap_or_else(|| exit(EXIT_SETUP));
    let name = proto::pipe_name(&sid, me.session);
    let dir = exe.parent().map(PathBuf::from).unwrap_or_else(|| exit(EXIT_SETUP));
    // The pipe first, then the model: a client during the load gets `NotReady` at once and passes its key through.
    if !start(&name, INSTANCES, me) {
        exit(EXIT_PIPE);
    }
    // The key scheme (§5.2): read once here, never in `engine::load`; event 71 records it with where it came from.
    let (scheme, source) = read_key_scheme();
    timing::record(timing::EV_KEY_SCHEME, u64::from(scheme), source);
    let t0 = timing::now_us();
    let e = engine::load(&dir, scheme).unwrap_or_else(|| exit(EXIT_LOAD));
    let t1 = timing::now_us();
    // Warm-up before `install`: until then every client passes through (§5.2).
    let e = engine::warmed(&dir, scheme, e).unwrap_or_else(|| exit(EXIT_LOAD));
    engine::install(e, scheme);
    timing::record(timing::EV_LOAD, t0, t1);
    // Lives until logoff (§5.2): the instance threads do the work.
    loop {
        std::thread::park();
    }
}

/// Creates all `n` instances of `name` now and serves each on its own thread for the life of the process. False when any instance
/// cannot be made, the first one included (the name is taken). `me` is this process's token (the client check compares with it).
pub fn start(name: &str, n: u32, me: Facts) -> bool {
    let me: &'static Facts = Box::leak(Box::new(me));
    let Some(sid) = token::sid_string(&me.user) else { return false };
    let sddl: Vec<u16> = sddl(&sid).encode_utf16().chain([0]).collect();
    let name: Vec<u16> = name.encode_utf16().chain([0]).collect();
    let mut sd = PSECURITY_DESCRIPTOR::default();
    // SAFETY: NUL-terminated; the descriptor is LocalAlloc memory, freed below (each instance keeps its own copy).
    if unsafe { ConvertStringSecurityDescriptorToSecurityDescriptorW(PCWSTR(sddl.as_ptr()), SDDL_REVISION_1, &mut sd, None) }.is_err() {
        return false;
    }
    let sa = SECURITY_ATTRIBUTES { nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32, lpSecurityDescriptor: sd.0, bInheritHandle: false.into() };
    let mut pipes = Vec::with_capacity(n as usize);
    for i in 0..n {
        let first = if i == 0 { FILE_FLAG_FIRST_PIPE_INSTANCE.0 } else { 0 };
        let open = FILE_FLAGS_AND_ATTRIBUTES(PIPE_ACCESS_DUPLEX.0 | FILE_FLAG_OVERLAPPED.0 | first);
        // SAFETY: NUL-terminated name, live security attributes.
        let h = unsafe { CreateNamedPipeW(PCWSTR(name.as_ptr()), open, PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS, n, BUFFER, BUFFER, 0, Some(&sa)) };
        match (h != INVALID_HANDLE_VALUE).then(|| Pipe::new(h)).flatten() {
            Some(p) => pipes.push(p),
            None => break,
        }
    }
    // SAFETY: allocated by the conversion above, freed once.
    unsafe {
        let _ = LocalFree(Some(HLOCAL(sd.0)));
    }
    if pipes.len() != n as usize {
        return false;
    }
    pipes.into_iter().all(|p| std::thread::Builder::new().spawn(move || instance(p, me)).is_ok())
}

static NEXT_CONN: AtomicU64 = AtomicU64::new(1);
static LIVE: AtomicU32 = AtomicU32::new(0);
static PEAK: AtomicU32 = AtomicU32::new(0);

/// One instance, forever: wait for a client, serve it, drop it.
fn instance(p: Pipe, me: &'static Facts) {
    loop {
        if accept(&p) {
            let conn = NEXT_CONN.fetch_add(1, Ordering::Relaxed);
            let live = LIVE.fetch_add(1, Ordering::AcqRel) + 1;
            if live > PEAK.fetch_max(live, Ordering::AcqRel) {
                timing::record(timing::EV_HOST_PEAK, u64::from(live), u64::from(live));
            }
            guard::run((), || serve(&p, conn, me));
            engine::drop_connection(conn);
            LIVE.fetch_sub(1, Ordering::AcqRel);
        } else {
            // A client that came and went before the connect, or a failure: no hot loop.
            std::thread::sleep(Duration::from_millis(10));
        }
        // SAFETY: the instance's own handle.
        unsafe {
            let _ = DisconnectNamedPipe(p.handle());
        }
    }
}

/// The overlapped `ConnectNamedPipe`, waited for without a limit (no client is waiting on this).
fn accept(p: &Pipe) -> bool {
    let mut ov = OVERLAPPED { hEvent: p.event(), ..Default::default() };
    // SAFETY: `ov` stays alive until the connect completes (the infinite wait below).
    unsafe {
        let _ = ResetEvent(p.event());
        match ConnectNamedPipe(p.handle(), Some(&mut ov)) {
            Ok(()) => true,
            Err(e) => match pipeio::win32(&e) {
                c if c == ERROR_PIPE_CONNECTED.0 => true,
                c if c == ERROR_IO_PENDING.0 => {
                    let _ = WaitForSingleObject(p.event(), INFINITE);
                    let mut n = 0u32;
                    GetOverlappedResult(p.handle(), &ov, &mut n, false).is_ok()
                }
                _ => false,
            },
        }
    }
}

fn send(p: &Pipe, seq: u64, m: &ServerMsg) -> bool {
    proto::encode_server(seq, m).is_some_and(|f| p.write_all(&f, soon(WRITE)).is_ok())
}

/// A last answer: sent, then the client gets a moment to read it and close.
fn last_word(p: &Pipe, seq: u64, m: &ServerMsg) {
    if send(p, seq, m) {
        let mut b = [0u8; 1];
        let _ = p.read_exact(&mut b, soon(LINGER));
    }
}

/// Who a client is for the rate limit: its AppContainer, else its process id at connect time. Never used to authorise.
#[derive(Clone, PartialEq, Eq, PartialOrd, Ord)]
enum Who {
    AppContainer(Vec<u8>),
    Pid(u32),
}

static PER: Mutex<BTreeMap<Who, u32>> = Mutex::new(BTreeMap::new());

/// One of a client's `PER_CLIENT` connections; given back on drop.
struct Slot(Who);

impl Slot {
    fn take(who: Who) -> Option<Slot> {
        let mut m = PER.lock().unwrap_or_else(PoisonError::into_inner);
        let n = m.entry(who.clone()).or_insert(0);
        if *n >= PER_CLIENT {
            return None;
        }
        *n += 1;
        Some(Slot(who))
    }
}

impl Drop for Slot {
    fn drop(&mut self) {
        let mut m = PER.lock().unwrap_or_else(PoisonError::into_inner);
        if let Some(n) = m.get_mut(&self.0) {
            *n -= 1;
            if *n == 0 {
                m.remove(&self.0);
            }
        }
    }
}

/// What the check of the client's token found, kept on the connection (§5.5 item 4).
struct Client {
    who: Who,
    app_container: bool,
    /// Integrity and elevation are recorded for the client policies of later slices (docs/spec/windows-tip.md §5.12); nothing here
    /// depends on them.
    #[allow(dead_code)]
    integrity: u32,
    #[allow(dead_code)]
    elevated: bool,
}

/// §5.5 item 4: impersonate (the client allowed identification only), read the thread token as this process (OpenAsSelf),
/// revert. The same user and session, and every query answered (an anonymous client cannot be read), or `None`. A failed revert ends
/// the process: it must never go on as the client.
fn check_client(p: &Pipe, me: &Facts) -> Option<Client> {
    let mut t = HANDLE::default();
    // SAFETY: the impersonation lasts only for the token query; the token is closed below.
    let facts = unsafe {
        let f = if ImpersonateNamedPipeClient(p.handle()).is_ok() && OpenThreadToken(GetCurrentThread(), TOKEN_QUERY, true, &mut t).is_ok() {
            let f = token::facts(t);
            let _ = CloseHandle(t);
            f
        } else {
            None
        };
        if RevertToSelf().is_err() {
            exit(EXIT_REVERT);
        }
        f
    }?;
    if facts.user != me.user || facts.session != me.session {
        return None;
    }
    let who = match &facts.app_container {
        Some(sid) => Who::AppContainer(sid.clone()),
        None => {
            let mut pid = 0u32;
            // SAFETY: valid out pointer; a failure leaves 0, one shared bucket.
            let _ = unsafe { GetNamedPipeClientProcessId(p.handle(), &mut pid) };
            Who::Pid(pid)
        }
    };
    Some(Client { who, app_container: facts.app_container.is_some(), integrity: facts.integrity, elevated: facts.elevated })
}

/// One connection, from its `Hello` to its end.
fn serve(p: &Pipe, conn: u64, me: &Facts) {
    let Ok(first) = p.read_frame(soon(HELLO)) else { return };
    let mut session = HostSession::default();
    let seq = match session.accept(&first) {
        Accept::Msg(seq, ClientMsg::Hello { .. }) => seq,
        Accept::VersionMismatch(seq) => return last_word(p, seq, &ServerMsg::Refuse { reason: proto::REFUSE_PROTO }),
        _ => return,
    };
    let Some(client) = check_client(p, me) else { return last_word(p, seq, &ServerMsg::Refuse { reason: proto::REFUSE_CLIENT }) };
    if !engine::ready() {
        return last_word(p, seq, &ServerMsg::NotReady);
    }
    let Some(_slot) = Slot::take(client.who.clone()) else { return last_word(p, seq, &ServerMsg::Refuse { reason: proto::REFUSE_LIMIT }) };
    if client.app_container {
        timing::record(timing::EV_HOST_APPCONTAINER, 1, 1);
    }
    if !send(p, seq, &ServerMsg::Welcome { build_hash: proto::BUILD_HASH, key_scheme: engine::key_scheme() }) {
        return;
    }
    loop {
        // No idle timeout after the Hello: a client keeps its connection while its app is open.
        let Ok(f) = p.read_frame(None) else { return };
        let t0 = timing::now_us();
        match session.accept(&f) {
            Accept::Msg(seq, ClientMsg::Key { ctx, key }) => {
                let m = match engine::key(Owner { conn, ctx }, key) {
                    Answer::Out(o) => ServerMsg::Out(o),
                    Answer::Busy => ServerMsg::Busy,
                    Answer::NotReady => ServerMsg::NotReady,
                };
                if !send(p, seq, &m) {
                    return;
                }
                timing::record(timing::EV_KEY, t0, timing::now_us());
            }
            Accept::Msg(_, ClientMsg::Release { ctx }) => engine::release(Owner { conn, ctx }),
            Accept::Msg(_, ClientMsg::Reset { .. }) => engine::drop_connection(conn),
            _ => return,
        }
    }
}
