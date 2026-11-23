//! Overlapped pipe I/O with deadlines (docs/spec/windows-tip.md §5.4), shared by the text service and the resident host
//! (the host includes this file by path). A wait that runs out cancels the operation with `CancelIoEx` and then waits for the
//! cancellation to complete (`GetOverlappedResult` with bWait = TRUE) before the `OVERLAPPED` and the buffer go out of scope. Waits are
//! never alertable and pump no messages. Errors are Win32 codes only (numbers, R2).
//!
//! A cancellation can lose the race with the operation it cancels: the bytes were then really read (or written), and the result of the awaited cancellation says
//! so. Such an operation counts as completed, and a read that fails says how many bytes it took off the pipe before (`read_frame_counted`): a caller that keeps
//! a connection after a timeout may do so only when that number is 0 (docs/spec/windows-tip.md §5.4).
use shanjie_win_proto as proto;
use std::time::Instant;
use windows::Win32::Foundation::{CloseHandle, ERROR_BROKEN_PIPE, ERROR_INVALID_DATA, ERROR_IO_PENDING, ERROR_TIMEOUT, HANDLE, WAIT_OBJECT_0};
use windows::Win32::Storage::FileSystem::{ReadFile, WriteFile};
use windows::Win32::System::IO::{CancelIoEx, GetOverlappedResult, OVERLAPPED};
use windows::Win32::System::Threading::{CreateEventW, ResetEvent, WaitForSingleObject, INFINITE};

pub const ERR_TIMEOUT: u32 = ERROR_TIMEOUT.0;
/// A malformed or unexpected message.
pub const ERR_BAD_DATA: u32 = ERROR_INVALID_DATA.0;

/// The Win32 code inside an `HRESULT_FROM_WIN32` error (any other HRESULT as its bits).
pub fn win32(e: &windows_core::Error) -> u32 {
    let h = e.code().0 as u32;
    if h & 0xFFFF_0000 == 0x8007_0000 {
        h & 0xFFFF
    } else {
        h
    }
}

/// Milliseconds left until `deadline` (INFINITE without one).
fn wait_ms(deadline: Option<Instant>) -> u32 {
    match deadline {
        None => INFINITE,
        Some(d) => d.saturating_duration_since(Instant::now()).as_millis().min(u128::from(INFINITE - 1)) as u32,
    }
}

/// A pipe handle and the manual-reset event its overlapped operations wait on; both are closed on drop.
pub struct Pipe {
    h: HANDLE,
    ev: HANDLE,
}

// SAFETY: the handles are plain kernel handles; a `Pipe` is used by one thread at a time (moved into the host's instance thread, or
// held by the client thread's link).
unsafe impl Send for Pipe {}

impl Drop for Pipe {
    fn drop(&mut self) {
        // SAFETY: owned handles, closed once.
        unsafe {
            let _ = CloseHandle(self.h);
            let _ = CloseHandle(self.ev);
        }
    }
}

impl Pipe {
    /// Takes ownership of `h`, which was opened for overlapped I/O (it is closed here when no event can be made).
    pub fn new(h: HANDLE) -> Option<Pipe> {
        // SAFETY: an unnamed manual-reset event.
        match unsafe { CreateEventW(None, true, false, None) } {
            Ok(ev) => Some(Pipe { h, ev }),
            Err(_) => {
                // SAFETY: owned, closed once.
                unsafe {
                    let _ = CloseHandle(h);
                }
                None
            }
        }
    }

    pub fn handle(&self) -> HANDLE {
        self.h
    }

    #[cfg_attr(not(test), allow(dead_code))] // only the tests wait on it directly
    pub fn event(&self) -> HANDLE {
        self.ev
    }

    /// One overlapped operation started by `start`, waited for until `deadline`. On a timeout the operation is cancelled and its
    /// completion awaited before this returns, so `ov` and the caller's buffer outlive it.
    fn io(&self, deadline: Option<Instant>, start: impl FnOnce(*mut OVERLAPPED) -> windows_core::Result<()>) -> Result<usize, u32> {
        let mut ov = OVERLAPPED { hEvent: self.ev, ..Default::default() };
        // SAFETY: `ov` and the buffer captured by `start` stay alive until the operation has completed or its cancellation has.
        unsafe {
            let _ = ResetEvent(self.ev);
            if let Err(e) = start(&mut ov) {
                let c = win32(&e);
                if c != ERROR_IO_PENDING.0 {
                    return Err(c);
                }
            }
            if WaitForSingleObject(self.ev, wait_ms(deadline)) != WAIT_OBJECT_0 {
                let _ = CancelIoEx(self.h, Some(&ov));
                let mut n = 0u32;
                // The operation completed before the cancellation reached it: its bytes moved, so it is a completed operation, not a timeout.
                if GetOverlappedResult(self.h, &ov, &mut n, true).is_ok() && n > 0 {
                    return Ok(n as usize);
                }
                return Err(ERR_TIMEOUT);
            }
            let mut n = 0u32;
            GetOverlappedResult(self.h, &ov, &mut n, false).map_err(|e| win32(&e))?;
            Ok(n as usize)
        }
    }

    #[allow(dead_code)] // the host and the tests; the text service reads whole messages, counted
    pub fn read_exact(&self, buf: &mut [u8], deadline: Option<Instant>) -> Result<(), u32> {
        self.read_counted(buf, deadline).map_err(|(code, _)| code)
    }

    /// `read_exact`; a failure also says how many bytes of `buf` were filled (taken off the pipe) before it.
    fn read_counted(&self, buf: &mut [u8], deadline: Option<Instant>) -> Result<(), (u32, usize)> {
        let mut at = 0;
        while at < buf.len() {
            let rest = &mut buf[at..];
            // SAFETY: `rest` is borrowed for the whole operation (see `io`).
            let n = self.io(deadline, |ov| unsafe { ReadFile(self.h, Some(rest), None, Some(ov)) }).map_err(|code| (code, at))?;
            if n == 0 {
                return Err((ERROR_BROKEN_PIPE.0, at));
            }
            at += n;
        }
        Ok(())
    }

    pub fn write_all(&self, buf: &[u8], deadline: Option<Instant>) -> Result<(), u32> {
        let mut at = 0;
        while at < buf.len() {
            let rest = &buf[at..];
            // SAFETY: `rest` is borrowed for the whole operation (see `io`).
            let n = self.io(deadline, |ov| unsafe { WriteFile(self.h, Some(rest), None, Some(ov)) })?;
            if n == 0 {
                return Err(ERROR_BROKEN_PIPE.0);
            }
            at += n;
        }
        Ok(())
    }

    /// One whole message: the four length bytes are checked (§5.3) before the rest is allocated and read.
    #[allow(dead_code)] // the host and the tests
    pub fn read_frame(&self, deadline: Option<Instant>) -> Result<Vec<u8>, u32> {
        self.read_frame_counted(deadline).map_err(|(code, _)| code)
    }

    /// `read_frame`; a failure also says how many bytes of the message were taken off the pipe before it (0: the stream still stands at a message boundary).
    #[allow(dead_code)] // the host reads with `read_frame` only
    pub fn read_frame_counted(&self, deadline: Option<Instant>) -> Result<Vec<u8>, (u32, usize)> {
        let mut prefix = [0u8; 4];
        self.read_counted(&mut prefix, deadline)?;
        let n = proto::frame_len(prefix).ok_or((ERR_BAD_DATA, 4))?;
        let mut f = vec![0u8; n];
        f[..4].copy_from_slice(&prefix);
        self.read_counted(&mut f[4..], deadline).map_err(|(code, read)| (code, 4 + read))?;
        Ok(f)
    }
}
