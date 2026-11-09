//! R2 behaviour test of the host (docs/spec/windows-tip.md §5.5 item 6: the text service's r2_tests.rs, for the host's
//! connection wrapper).
//!
//! The child installs nothing: the production wrapper (`guard::run`, which every connection runs in) must install the silent hook
//! itself. A panic inside it carries a marker in its message; the parent finds the marker in neither stdout, stderr nor the debug
//! output. Positive controls prove each capture works: a child with the default hook panics unwrapped (the marker must show on
//! stderr), a child calls `OutputDebugStringW` (the marker must arrive as a debug event). The marker is ASCII so a debug-output code
//! page cannot hide it.
use crate::guard;
use std::process::Command;
use windows::core::{PCWSTR, PWSTR};
use windows::Win32::Foundation::{CloseHandle, DBG_CONTINUE, DBG_EXCEPTION_NOT_HANDLED};
use windows::Win32::System::Diagnostics::Debug::{
    ContinueDebugEvent, OutputDebugStringW, ReadProcessMemory, WaitForDebugEvent, DEBUG_EVENT, EXCEPTION_DEBUG_EVENT, EXIT_PROCESS_DEBUG_EVENT, OUTPUT_DEBUG_STRING_EVENT,
};
use windows::Win32::System::Threading::{CreateProcessW, DEBUG_ONLY_THIS_PROCESS, PROCESS_INFORMATION, STARTUPINFOW};

const MARKER: &str = "R2MARKER7F3A9C";
/// Sent to the debugger by the wrapper child first, so the parent can tell the child really ran.
const RAN: &str = "R2CHILDRAN";

fn is_child(name: &str) -> bool {
    let a: Vec<String> = std::env::args().collect();
    a.iter().any(|x| x == "--exact") && a.iter().any(|x| *x == format!("r2_tests::{name}"))
}

fn debug_string(s: &str) {
    let w: Vec<u16> = s.encode_utf16().chain([0]).collect();
    // SAFETY: NUL-terminated and alive for the call.
    unsafe { OutputDebugStringW(PCWSTR(w.as_ptr())) };
}

#[test]
fn child_wrapper_panic() {
    if !is_child("child_wrapper_panic") {
        return;
    }
    debug_string(RAN);
    let served = guard::run(false, || -> bool { panic!("{MARKER}") });
    assert!(!served, "a panic inside the wrapper comes back as its failure value, not as an unwind");
}

#[test]
fn child_default_hook_panic() {
    if !is_child("child_default_hook_panic") {
        return;
    }
    let _ = std::panic::catch_unwind(|| panic!("{MARKER}"));
}

#[test]
fn child_debug_string() {
    if !is_child("child_debug_string") {
        return;
    }
    debug_string(MARKER);
}

fn run_child(name: &str) -> (bool, String, String) {
    let o = Command::new(std::env::current_exe().unwrap()).args(["--exact", &format!("r2_tests::{name}"), "--nocapture"]).output().unwrap();
    (o.status.success(), String::from_utf8_lossy(&o.stdout).into(), String::from_utf8_lossy(&o.stderr).into())
}

#[test]
fn r2_panic_marker_never_reaches_stdout_or_stderr() {
    let (ok, out, err) = run_child("child_wrapper_panic");
    assert!(ok, "wrapper child failed");
    assert!(out.contains("1 passed"), "wrapper child actually ran");
    assert!(!out.contains(MARKER) && !err.contains(MARKER), "marker leaked through the wrapper");
    // Positive control: the same panic with the default hook, no wrapper.
    let (ok, out, err) = run_child("child_default_hook_panic");
    assert!(ok, "default-hook child failed");
    assert!(out.contains("1 passed"), "default-hook child actually ran");
    assert!(err.contains(MARKER), "positive control: the default hook did not print the marker to stderr");
}

/// Runs the named child under this process as its debugger; returns its exit code and every debug string it sent (raw bytes).
fn run_under_debugger(name: &str) -> (u32, Vec<u8>) {
    let exe = std::env::current_exe().unwrap();
    let mut cmd: Vec<u16> = format!("\"{}\" --exact r2_tests::{name} --nocapture", exe.display()).encode_utf16().chain([0]).collect();
    let si = STARTUPINFOW { cb: std::mem::size_of::<STARTUPINFOW>() as u32, ..Default::default() };
    let mut pi = PROCESS_INFORMATION::default();
    // SAFETY: `cmd` is a writable NUL-terminated buffer; the structures are live.
    unsafe { CreateProcessW(PCWSTR::null(), Some(PWSTR(cmd.as_mut_ptr())), None, None, false, DEBUG_ONLY_THIS_PROCESS, None, PCWSTR::null(), &si, &mut pi) }.expect("spawn child");
    let mut bytes = Vec::new();
    let mut exit = u32::MAX;
    loop {
        let mut ev = DEBUG_EVENT::default();
        // SAFETY: valid out structure; 60 s is far above a test child's life.
        unsafe { WaitForDebugEvent(&mut ev, 60_000) }.expect("debug event");
        let mut verdict = DBG_CONTINUE;
        let mut done = false;
        // SAFETY: the union member read matches the event code.
        unsafe {
            match ev.dwDebugEventCode {
                OUTPUT_DEBUG_STRING_EVENT => {
                    let d = ev.u.DebugString;
                    let n = d.nDebugStringLength as usize * if d.fUnicode != 0 { 2 } else { 1 };
                    let mut buf = vec![0u8; n];
                    let mut got = 0usize;
                    if ReadProcessMemory(pi.hProcess, d.lpDebugStringData.0.cast(), buf.as_mut_ptr().cast(), n, Some(&mut got)).is_ok() {
                        bytes.extend_from_slice(&buf[..got.min(n)]);
                    }
                }
                // The initial loader breakpoint is continued; every other exception (a Rust panic is a C++ exception) goes to the program.
                EXCEPTION_DEBUG_EVENT => {
                    if ev.u.Exception.ExceptionRecord.ExceptionCode.0 != 0x8000_0003u32 as i32 {
                        verdict = DBG_EXCEPTION_NOT_HANDLED;
                    }
                }
                EXIT_PROCESS_DEBUG_EVENT => {
                    exit = ev.u.ExitProcess.dwExitCode;
                    done = true;
                }
                _ => {}
            }
            ContinueDebugEvent(ev.dwProcessId, ev.dwThreadId, verdict).expect("continue");
        }
        if done {
            break;
        }
    }
    // SAFETY: handles from CreateProcessW, closed once.
    unsafe {
        let _ = CloseHandle(pi.hProcess);
        let _ = CloseHandle(pi.hThread);
    }
    (exit, bytes)
}

/// The text as ANSI/UTF-8 bytes or as UTF-16LE (a debug string may arrive either way).
fn contains(hay: &[u8], needle: &str) -> bool {
    let w: Vec<u8> = needle.encode_utf16().flat_map(u16::to_le_bytes).collect();
    hay.windows(needle.len()).any(|x| x == needle.as_bytes()) || hay.windows(w.len()).any(|x| x == w.as_slice())
}

#[test]
fn r2_panic_marker_never_reaches_the_debug_output() {
    let (exit, dbg) = run_under_debugger("child_wrapper_panic");
    assert!(exit == 0, "wrapper child failed under the debugger");
    assert!(contains(&dbg, RAN), "the wrapper child really ran (its first debug string arrived)");
    assert!(!contains(&dbg, MARKER), "marker leaked into the debug output");
    // Positive control: a child that sends the marker with OutputDebugStringW is seen.
    let (exit, dbg) = run_under_debugger("child_debug_string");
    assert!(exit == 0, "debug-string child failed");
    assert!(contains(&dbg, MARKER), "positive control: the debug capture did not see OutputDebugStringW");
}
