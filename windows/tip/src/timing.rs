//! The measurement file (docs/contracts/win-tip.md section 2.5, win-resident.md section 2.8). The line format is pure and always
//! compiled; the file exists only with the cargo feature `timing`. Numbers only: `<event>\t<start>\t<end>\n`, microseconds of one
//! `QueryPerformanceCounter` clock (the same clock in every process, so the DLL's and the host's times compare). Shared by the text
//! service (`timing-<pid>.txt`) and the resident host (`host-<pid>.txt`, which includes this file by path): the event codes of both
//! are listed here, in one place.

#![cfg_attr(not(feature = "timing"), allow(dead_code))]

pub const EV_ACTIVATE: u32 = 1;
/// The model load (in the host: from before `new_packs` to the engine in place).
pub const EV_LOAD: u32 = 2;
/// One key: in the DLL from entering `OnKeyDown` to the document edited; in the host from the key received to the answer sent.
pub const EV_KEY: u32 = 3;
/// Diagnostic events (docs/spec/windows-tip.md §3.11): a point in time (start = end) with a numeric reason, never any key or text.
/// 41-44: OnTestKeyDown answered FALSE because the instance is not active, the engine is not usable, the keyboard compartment is
/// closed, or the context is disabled or empty. 47: ActivateEx could not advise its sinks. 48: ActivateEx in secure mode.
pub const EV_TEST_INACTIVE: u32 = 41;
pub const EV_TEST_UNUSABLE: u32 = 42;
pub const EV_TEST_CLOSED: u32 = 43;
pub const EV_TEST_CONTEXT: u32 = 44;
pub const EV_ACTIVATE_ADVISE_FAILED: u32 = 47;
pub const EV_ACTIVATE_SECURE: u32 = 48;
/// 49: ActivateEx on a thread; both columns are the thread id (not a time), so runs show which threads activated the TIP.
pub const EV_ACTIVATE_THREAD: u32 = 49;
/// 40: OnTestKeyDown answered TRUE; 50: OnKeyDown entered. Both columns are the OS thread id, so runs show which threads get keys.
pub const EV_TEST_TRUE_THREAD: u32 = 40;
pub const EV_KEYDOWN_THREAD: u32 = 50;
/// 45: OnKeyDown's synchronous edit session was not granted; both columns are the HRESULT (as u32). 46: the engine lock was busy.
pub const EV_KEY_SESSION_REFUSED: u32 = 45;
pub const EV_KEY_ENGINE_BUSY: u32 = 46;
/// 51-55: OnKeyDown returned before calling the core because the instance is not active, the engine is not usable, the keyboard
/// compartment is closed, the context is disabled or empty, or the key has no mapping. 56: the keyboard compartment read closed when the
/// TIP opened it for a document manager getting focus for the first time (docs/spec/windows-tip.md §3.11).
pub const EV_KD_INACTIVE: u32 = 51;
pub const EV_KD_UNUSABLE: u32 = 52;
pub const EV_KD_CLOSED: u32 = 53;
pub const EV_KD_CONTEXT: u32 = 54;
pub const EV_KD_UNMAPPED: u32 = 55;
pub const EV_REOPENED: u32 = 56;
/// win-resident.md section 2.8. 60: the DLL waited for the host's answer to a key (start = sent, end = received). 61: the DLL's
/// `CreateProcessW` of the host succeeded (start = end = now). 62: a timeout or a broken connection (both columns: the Win32 code).
/// 63: the DLL passed a key through for a reason below (both columns: the reason). 64: the host accepted an AppContainer client
/// (both columns 1). 65: the host's number of simultaneous connections reached a new high (both columns: that number).
pub const EV_WAIT: u32 = 60;
pub const EV_LAUNCHED: u32 = 61;
pub const EV_IO_FAILED: u32 = 62;
pub const EV_PASS: u32 = 63;
pub const EV_HOST_APPCONTAINER: u32 = 64;
pub const EV_HOST_PEAK: u32 = 65;

/// Records a diagnostic event whose two columns carry one number (a thread id or an HRESULT), not times.
pub fn diag_value(event: u32, value: u64) {
    record(event, value, value);
}

/// Records a diagnostic event at the current time (no-op without the `timing` feature).
pub fn diag(event: u32) {
    let t = now_us();
    record(event, t, t);
}

/// One line: three decimal numbers, tabs, newline; nothing else ever reaches the file.
pub fn format_line(event: u32, start_us: u64, end_us: u64) -> String {
    format!("{event}\t{start_us}\t{end_us}\n")
}

#[cfg(feature = "timing")]
mod file {
    use std::io::Write;
    use std::sync::{Mutex, Once};
    use windows::core::PWSTR;
    use windows::Win32::Foundation::{CloseHandle, HANDLE};
    use windows::Win32::Security::{GetSidSubAuthority, GetSidSubAuthorityCount, GetTokenInformation, TokenIntegrityLevel, TOKEN_MANDATORY_LABEL, TOKEN_QUERY};
    use windows::Win32::System::Com::CoTaskMemFree;
    use windows::Win32::System::Performance::{QueryPerformanceCounter, QueryPerformanceFrequency};
    use windows::Win32::System::Threading::{GetCurrentProcess, GetCurrentProcessId, OpenProcessToken};
    use windows::Win32::UI::Shell::{FOLDERID_LocalAppData, SHGetKnownFolderPath, KF_FLAG_NO_PACKAGE_REDIRECTION};

    /// SECURITY_MANDATORY_MEDIUM_RID: only a process of exactly this level writes (docs/spec/windows-tip.md §3.11).
    const MEDIUM_RID: u32 = 0x2000;

    static SINK: Mutex<Option<std::fs::File>> = Mutex::new(None);
    static OPEN: Once = Once::new();

    pub fn now_us() -> u64 {
        let (mut c, mut f) = (0i64, 0i64);
        // SAFETY: both pointers are to live locals.
        unsafe {
            let _ = QueryPerformanceCounter(&mut c);
            let _ = QueryPerformanceFrequency(&mut f);
        }
        if f <= 0 {
            return 0;
        }
        (c as u128 * 1_000_000 / f as u128) as u64
    }

    fn is_medium() -> bool {
        // SAFETY: standard token query; the buffer is sized by the first call and the label points into it.
        unsafe {
            let mut token = HANDLE::default();
            if OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token).is_err() {
                return false;
            }
            let mut len = 0u32;
            let _ = GetTokenInformation(token, TokenIntegrityLevel, None, 0, &mut len);
            let mut buf = vec![0u64; (len as usize).div_ceil(8).max(1)]; // 8-byte aligned
            let ok = len != 0 && GetTokenInformation(token, TokenIntegrityLevel, Some(buf.as_mut_ptr().cast()), len, &mut len).is_ok();
            let _ = CloseHandle(token);
            if !ok {
                return false;
            }
            let sid = (*buf.as_ptr().cast::<TOKEN_MANDATORY_LABEL>()).Label.Sid;
            if sid.0.is_null() {
                return false;
            }
            let n = *GetSidSubAuthorityCount(sid);
            n > 0 && *GetSidSubAuthority(sid, u32::from(n) - 1) == MEDIUM_RID
        }
    }

    /// `%LOCALAPPDATA%\shanjie-dev\timing\<prefix>-<pid>.txt`; the folder is made by the driver script, never by the TIP or the host.
    fn open_file(prefix: &str) -> Option<std::fs::File> {
        if !is_medium() {
            return None;
        }
        // SAFETY: the returned string is CoTaskMem memory, copied and freed here.
        let dir = unsafe {
            let p: PWSTR = SHGetKnownFolderPath(&FOLDERID_LocalAppData, KF_FLAG_NO_PACKAGE_REDIRECTION, None).ok()?;
            let s = p.to_string();
            CoTaskMemFree(Some(p.0.cast()));
            s.ok()?
        };
        // SAFETY: plain query.
        let pid = unsafe { GetCurrentProcessId() };
        let path = std::path::Path::new(&dir).join("shanjie-dev").join("timing").join(format!("{prefix}-{pid}.txt"));
        std::fs::OpenOptions::new().write(true).create_new(true).open(path).ok()
    }

    /// Opens the file once per process (`prefix`: "timing" for the DLL, "host" for the host); any failure leaves the process without timing.
    pub fn init(prefix: &str) {
        OPEN.call_once(|| {
            if let Ok(mut g) = SINK.lock() {
                *g = open_file(prefix);
            }
        });
    }

    pub fn record(event: u32, start_us: u64, end_us: u64) {
        if let Ok(mut g) = SINK.try_lock() {
            if let Some(f) = g.as_mut() {
                let line = super::format_line(event, start_us, end_us);
                if f.write_all(line.as_bytes()).and_then(|_| f.flush()).is_err() {
                    *g = None;
                }
            }
        }
    }
}

#[cfg(feature = "timing")]
pub use file::{init, now_us, record};

#[cfg(not(feature = "timing"))]
pub fn init(_prefix: &str) {}
#[cfg(not(feature = "timing"))]
pub fn now_us() -> u64 {
    0
}
#[cfg(not(feature = "timing"))]
pub fn record(_event: u32, _start_us: u64, _end_us: u64) {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_line_has_three_numeric_columns() {
        let l = format_line(3, 12, 4_000_000_000_000);
        assert!(l == "3\t12\t4000000000000\n");
        assert!(l.bytes().all(|b| b.is_ascii_digit() || b == b'\t' || b == b'\n'));
        assert!(l.matches('\t').count() == 2 && l.matches('\n').count() == 1 && l.ends_with('\n'));
        for ev in [EV_ACTIVATE, EV_LOAD, EV_KEY, EV_WAIT, EV_LAUNCHED, EV_IO_FAILED, EV_PASS, EV_HOST_APPCONTAINER, EV_HOST_PEAK] {
            let l = format_line(ev, 0, u64::MAX);
            assert!(l.bytes().all(|b| b.is_ascii_digit() || b == b'\t' || b == b'\n'));
        }
    }
}
