//! The three exports and their Windows calls. Every export runs inside `guarded`: a silent panic hook installed on first entry (not in `DllMain`,
//! which this DLL does not define), `catch_unwind`, and the export's own failure value. Nothing here prints; what each action did goes to the
//! MSI log (`log`), in plain ASCII words and numbers.
//!
//! Imports: msi, kernel32, user32, advapi32, imm32 and ntdll (scripts/windows/check-msi.ps1 holds the list). No `HRESULT::ok()`/`?` on an
//! `HRESULT`: the `windows` crate turns that into an `Error` that asks oleaut32 for `IErrorInfo`, a DLL outside the list. `SetDefaultDllDirectories`
//! is never called (it would change the whole custom action server process).
use crate::{answer, is_host_path, parse_pid, same_path, settings_layers, token, Answer, CANCEL_BUTTON, UNINSTALL_BUTTON};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::Once;
use windows::core::{s, w, BOOL, HRESULT, PCWSTR, PWSTR};
use windows::Wdk::System::Registry::NtDeleteKey;
use windows::Win32::Foundation::{
    CloseHandle, FreeLibrary, ERROR_INSTALL_USEREXIT, ERROR_MORE_DATA, ERROR_NO_MORE_ITEMS, ERROR_NO_TOKEN, ERROR_SUCCESS, HANDLE, HINSTANCE, HMODULE, HWND, LPARAM, S_OK, WIN32_ERROR,
    WPARAM,
};
use windows::Win32::Security::TOKEN_QUERY;
use windows::Win32::System::ApplicationInstallationAndServicing::{
    ActivateActCtx, CreateActCtxW, DeactivateActCtx, MsiCloseHandle, MsiCreateRecord, MsiGetPropertyW, MsiProcessMessage, MsiRecordSetStringW, MsiSetPropertyW, ReleaseActCtx, ACTCTXW,
    INSTALLMESSAGE_INFO, MSIHANDLE,
};
use windows::Win32::System::Diagnostics::ToolHelp::{CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W, TH32CS_SNAPPROCESS};
use windows::Win32::System::LibraryLoader::{GetModuleFileNameW, GetModuleHandleExW, GetProcAddress, LoadLibraryW, GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS, GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT};
use windows::Win32::System::Registry::{RegCloseKey, RegEnumKeyExW, RegOpenCurrentUser, RegOpenKeyExW, HKEY, KEY_ENUMERATE_SUB_KEYS, KEY_QUERY_VALUE, KEY_READ, REG_OPTION_OPEN_LINK, REG_SAM_FLAGS};
use windows::Win32::System::StationsAndDesktops::{GetProcessWindowStation, GetUserObjectInformationW, UOI_FLAGS, USEROBJECTFLAGS};
use windows::Win32::System::Threading::{AttachThreadInput, GetCurrentThreadId, GetCurrentProcessId, GetCurrentThread, OpenProcess, OpenThreadToken, QueryFullProcessImageNameW, TerminateProcess, PROCESS_NAME_WIN32, PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_TERMINATE};
use windows::Win32::System::WindowsProgramming::{ACTCTX_FLAG_HMODULE_VALID, ACTCTX_FLAG_RESOURCE_NAME_VALID};
use windows::Win32::UI::Controls::{
    TASKDIALOGCONFIG, TASKDIALOGCONFIG_0, TASKDIALOGCONFIG_1, TASKDIALOG_BUTTON, TASKDIALOG_COMMON_BUTTON_FLAGS, TASKDIALOG_NOTIFICATIONS, TDF_ALLOW_DIALOG_CANCELLATION, TDF_CALLBACK_TIMER, TDF_VERIFICATION_FLAG_CHECKED,
    TDN_CREATED, TDN_TIMER,
};
use windows::Win32::UI::Input::Ime::{ImmAssociateContextEx, HIMC, IACE_CHILDREN};
use windows::Win32::UI::WindowsAndMessaging::{
    BringWindowToTop, GetForegroundWindow, GetWindowThreadProcessId, SendMessageTimeoutW, SetForegroundWindow, SetWindowPos, HWND_TOPMOST, SMTO_ABORTIFHUNG,
    SMTO_BLOCK, SWP_NOMOVE, SWP_NOSIZE, WM_NULL, WSF_VISIBLE,
};

/// The manifest resource build.rs embeds (ISOLATIONAWARE_MANIFEST_RESOURCE_ID).
const MANIFEST_RESOURCE: usize = 2;
/// DELETE (winnt.h): the right `NtDeleteKey` needs on the handle.
const DELETE: REG_SAM_FLAGS = REG_SAM_FLAGS(0x0001_0000);
/// How deep `delete_key` goes below `Software\Shanjie` (the program writes one level; a deeper tree is left as it is).
const MAX_DEPTH: usize = 32;
/// How many subkeys one level may have before `delete_key` gives up (it never loops for ever on a key that keeps a child).
const MAX_CHILDREN: u32 = 4096;
/// The longest property value read (`KillHost`'s CustomActionData is one path).
const MAX_PROPERTY: usize = 32768;

fn install_hook() {
    static ONCE: Once = Once::new();
    ONCE.call_once(|| std::panic::set_hook(Box::new(|_| {})));
}

/// Runs `f`; a panic is logged as one word and gives `fail`.
fn guarded(h: MSIHANDLE, name: &str, fail: u32, f: impl FnOnce(MSIHANDLE) -> u32) -> u32 {
    install_hook();
    match catch_unwind(AssertUnwindSafe(|| f(h))) {
        Ok(v) => v,
        Err(_) => {
            let _ = catch_unwind(AssertUnwindSafe(|| log(h, &format!("{name}: panic"))));
            fail
        }
    }
}

/// One INFO line in the MSI log. The text has no `[` or `{` (record formatting).
fn log(h: MSIHANDLE, text: &str) {
    let wide: Vec<u16> = format!("shanjie-ca {text}").encode_utf16().chain([0]).collect();
    // SAFETY: `wide` is NUL-terminated and outlives the calls; the record is closed here.
    unsafe {
        let rec = MsiCreateRecord(1);
        if rec.is_invalid() {
            return;
        }
        if MsiRecordSetStringW(rec, 0, PCWSTR(wide.as_ptr())) == ERROR_SUCCESS.0 {
            let _ = MsiProcessMessage(h, INSTALLMESSAGE_INFO, rec);
        }
        let _ = MsiCloseHandle(rec);
    }
}

/// A property's value without the NUL; `None` if it cannot be read.
fn property(h: MSIHANDLE, name: PCWSTR) -> Option<Vec<u16>> {
    let mut buf = vec![0u16; 1024];
    for _ in 0..2 {
        let mut n = buf.len() as u32;
        // SAFETY: `buf` holds `n` units; on ERROR_MORE_DATA `n` is the length needed without the NUL.
        let r = unsafe { MsiGetPropertyW(h, name, Some(PWSTR(buf.as_mut_ptr())), Some(&mut n)) };
        if r == ERROR_SUCCESS.0 {
            buf.truncate(n as usize);
            return Some(buf);
        }
        if r != ERROR_MORE_DATA.0 || n as usize >= MAX_PROPERTY {
            return None;
        }
        buf = vec![0u16; n as usize + 1];
    }
    None
}

/// The thread's impersonation token's facts: `Some(None)` when the thread has no token, `None` when a query failed.
fn thread_facts() -> Option<Option<token::Facts>> {
    let mut t = HANDLE::default();
    // SAFETY: the pseudo handle of this thread; the token is closed below.
    match unsafe { OpenThreadToken(GetCurrentThread(), TOKEN_QUERY, true, &mut t) } {
        Ok(()) => {
            let f = token::facts(t);
            // SAFETY: opened above.
            unsafe {
                let _ = CloseHandle(t);
            }
            f.map(Some)
        }
        Err(e) if e.code() == HRESULT::from_win32(ERROR_NO_TOKEN.0) => Some(None),
        Err(_) => None,
    }
}

/// Neither the process token nor the thread's token (if any) is SYSTEM, LocalService or NetworkService, and `extra` holds for both.
/// Any failed query answers false.
fn tokens_are_a_users(extra: impl Fn(&token::Facts) -> bool) -> bool {
    let ok = |f: &token::Facts| !token::is_service_account(&f.user) && extra(f);
    let Some(p) = token::process_facts() else {
        return false;
    };
    match thread_facts() {
        Some(Some(t)) => ok(&p) && ok(&t),
        Some(None) => ok(&p),
        None => false,
    }
}

/// The process's window station is the interactive one (WSF_VISIBLE).
fn interactive_station() -> bool {
    // SAFETY: the station handle belongs to the process and is not closed; `flags` is the buffer of its size.
    unsafe {
        let Ok(ws) = GetProcessWindowStation() else {
            return false;
        };
        let mut flags = USEROBJECTFLAGS::default();
        let size = std::mem::size_of::<USEROBJECTFLAGS>() as u32;
        GetUserObjectInformationW(HANDLE(ws.0), UOI_FLAGS, Some((&mut flags as *mut USEROBJECTFLAGS).cast()), size, None).is_ok() && flags.dwFlags & WSF_VISIBLE as u32 != 0
    }
}

// ---- AskKeep ----

/// Immediate action, basic or reduced UI uninstall (the sequence's condition). Shows the question when this is a user's process on the
/// interactive desktop (not a service account, not session 0); otherwise, or when the dialog fails, leaves `KEEPSETTINGS` alone (both kept).
/// Returns ERROR_INSTALL_USEREXIT only when the user cancels.
#[no_mangle]
pub extern "system" fn AskKeep(h: MSIHANDLE) -> u32 {
    guarded(h, "AskKeep", ERROR_SUCCESS.0, ask_keep)
}

fn ask_keep(h: MSIHANDLE) -> u32 {
    if !(tokens_are_a_users(|f| f.session != 0) && interactive_station()) {
        log(h, "AskKeep: no interactive user desktop, nothing asked, both kept");
        return ERROR_SUCCESS.0;
    }
    let (shown, button, checked) = ask(h);
    match answer(shown, button, checked) {
        Answer::Cancel => {
            log(h, "AskKeep: cancelled");
            ERROR_INSTALL_USEREXIT.0
        }
        Answer::Keep(keep) => {
            // SAFETY: literal NUL-terminated strings.
            let r = unsafe { MsiSetPropertyW(h, w!("KEEPSETTINGS"), if keep { w!("1") } else { w!("0") }) };
            log(h, &format!("AskKeep: KEEPSETTINGS {} rc {r}", u8::from(keep)));
            ERROR_SUCCESS.0
        }
        Answer::AsIfNoDialog => {
            log(h, &format!("AskKeep: dialog failed or unknown answer {button}, both kept"));
            ERROR_SUCCESS.0
        }
    }
}

/// This DLL's module handle (from an address inside it; no `DllMain` records it).
fn this_module() -> Option<HMODULE> {
    let mut m = HMODULE::default();
    // SAFETY: the address of one of this DLL's functions; UNCHANGED_REFCOUNT, so nothing to free.
    unsafe { GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT, PCWSTR(AskKeep as *const () as *const u16), &mut m) }.ok()?;
    Some(m)
}

/// The full path of `m`, NUL-terminated. A path that does not fit 32768 units gives `None`.
fn module_path(m: HMODULE) -> Option<Vec<u16>> {
    let mut cap = 512usize;
    loop {
        let mut buf = vec![0u16; cap];
        // SAFETY: `buf` is a live buffer of `cap` units.
        let n = unsafe { GetModuleFileNameW(Some(m), &mut buf) } as usize;
        if n == 0 {
            return None;
        }
        if n < cap {
            buf.truncate(n);
            buf.push(0);
            return Some(buf);
        }
        if cap >= 32768 {
            return None;
        }
        cap *= 2;
    }
}

/// The activation context of this DLL's manifest, active from `activate` until drop (also on unwind).
struct ActCtx {
    ctx: HANDLE,
    cookie: Option<usize>,
}

impl ActCtx {
    fn activate(h: MSIHANDLE) -> Option<ActCtx> {
        let module = this_module()?;
        let path = module_path(module)?;
        let ac = ACTCTXW {
            cbSize: std::mem::size_of::<ACTCTXW>() as u32,
            dwFlags: ACTCTX_FLAG_HMODULE_VALID | ACTCTX_FLAG_RESOURCE_NAME_VALID,
            lpSource: PCWSTR(path.as_ptr()),
            lpResourceName: PCWSTR(MANIFEST_RESOURCE as *const u16),
            hModule: module,
            ..Default::default()
        };
        // SAFETY: `ac` and `path` outlive the call.
        let ctx = match unsafe { CreateActCtxW(&ac) } {
            Ok(c) => c,
            Err(e) => {
                log(h, &format!("AskKeep: CreateActCtxW error {:#x}", e.code().0));
                return None;
            }
        };
        let mut a = ActCtx { ctx, cookie: None };
        let mut cookie = 0usize;
        // SAFETY: a context created above; the cookie is kept for the deactivation.
        match unsafe { ActivateActCtx(Some(ctx), &mut cookie) } {
            Ok(()) => a.cookie = Some(cookie),
            Err(e) => {
                log(h, &format!("AskKeep: ActivateActCtx error {:#x}", e.code().0));
                return None;
            }
        }
        Some(a)
    }
}

impl Drop for ActCtx {
    fn drop(&mut self) {
        // SAFETY: the cookie and the context are this object's.
        unsafe {
            if let Some(c) = self.cookie {
                let _ = DeactivateActCtx(0, c);
            }
            ReleaseActCtx(self.ctx);
        }
    }
}

type TaskDialogIndirectFn = unsafe extern "system" fn(*const TASKDIALOGCONFIG, *mut i32, *mut i32, *mut BOOL) -> HRESULT;

/// The dialog, inside this DLL's activation context, so `comctl32.dll` resolves to version 6 (the only version with `TaskDialogIndirect`)
/// and the call does not depend on the custom action server's own manifest. Returns (shown, button, keep checked).
fn ask(h: MSIHANDLE) -> (bool, i32, bool) {
    let Some(_ctx) = ActCtx::activate(h) else {
        return (false, 0, false);
    };
    // SAFETY: the one run-time load, by the bare name inside the active context (the measured probe did the same and got version 6 from
    // WinSxS). Without the redirection the search would reach System32's version 5 first, which has no TaskDialogIndirect: the question is
    // then not asked and both are kept.
    let cc = match unsafe { LoadLibraryW(w!("comctl32.dll")) } {
        Ok(m) => m,
        Err(e) => {
            log(h, &format!("AskKeep: comctl32 load error {:#x}", e.code().0));
            return (false, 0, false);
        }
    };
    // SAFETY: the export's documented signature.
    let result = match unsafe { GetProcAddress(cc, s!("TaskDialogIndirect")) } {
        Some(p) => unsafe { show(h, std::mem::transmute::<unsafe extern "system" fn() -> isize, TaskDialogIndirectFn>(p)) },
        None => {
            log(h, "AskKeep: no TaskDialogIndirect");
            (false, 0, false)
        }
    };
    // SAFETY: loaded above; the dialog has returned.
    unsafe {
        let _ = FreeLibrary(cc);
    }
    result
}

/// # Safety
/// `tdi` is comctl32's `TaskDialogIndirect`.
unsafe fn show(h: MSIHANDLE, tdi: TaskDialogIndirectFn) -> (bool, i32, bool) {
    let buttons = [
        TASKDIALOG_BUTTON { nButtonID: UNINSTALL_BUTTON, pszButtonText: w!("解除安裝") },
        TASKDIALOG_BUTTON { nButtonID: CANCEL_BUTTON, pszButtonText: w!("取消") },
    ];
    // Fixed text, one check box, two buttons: no links, no expanded area, no footer, no help button, no owner window.
    let config = TASKDIALOGCONFIG {
        cbSize: std::mem::size_of::<TASKDIALOGCONFIG>() as u32,
        hwndParent: HWND::default(),
        hInstance: HINSTANCE::default(),
        dwFlags: TDF_ALLOW_DIALOG_CANCELLATION | TDF_VERIFICATION_FLAG_CHECKED | TDF_CALLBACK_TIMER,
        dwCommonButtons: TASKDIALOG_COMMON_BUTTON_FLAGS(0),
        pszWindowTitle: w!("善解"),
        Anonymous1: TASKDIALOGCONFIG_0 { pszMainIcon: PCWSTR::null() },
        pszMainInstruction: w!("要解除安裝善解嗎？"),
        pszContent: w!("開著的 App 如果還在用善解，要等那些 App 重新開啟之後，善解才會完全移除。"),
        cButtons: buttons.len() as u32,
        pButtons: buttons.as_ptr(),
        nDefaultButton: UNINSTALL_BUTTON,
        cRadioButtons: 0,
        pRadioButtons: std::ptr::null(),
        nDefaultRadioButton: 0,
        pszVerificationText: w!("保留設定"),
        pszExpandedInformation: PCWSTR::null(),
        pszExpandedControlText: PCWSTR::null(),
        pszCollapsedControlText: PCWSTR::null(),
        Anonymous2: TASKDIALOGCONFIG_1 { pszFooterIcon: PCWSTR::null() },
        pszFooter: PCWSTR::null(),
        pfCallback: Some(on_notify),
        lpCallbackData: h.0 as isize, // on_notify's MSI handle: its log line and the CLIENTPROCESSID property (this thread, during the modal call)
        cxWidth: 0,
    };
    let (mut button, mut checked) = (0i32, BOOL(0));
    // SAFETY: `config` and `buttons` outlive the modal call; the out pointers are live.
    let hr = unsafe { tdi(&config, &mut button, std::ptr::null_mut(), &mut checked) };
    if hr.is_err() {
        log(h, &format!("AskKeep: TaskDialogIndirect error {:#x}", hr.0));
        return (false, 0, false);
    }
    (true, button, checked.as_bool())
}

/// On creation: no input method for the dialog's controls (with Zhuyin in Chinese mode the space bar would not reach the check box), then
/// topmost and a request for the foreground.
///
/// On the first timer tick (TDF_CALLBACK_TIMER; the dialog is visible by then, it is not yet at TDN_CREATED), once: if the dialog still
/// is not the foreground window, its thread attaches its input to the foreground window's thread for the one call that brings it to
/// the front, then detaches. Measured on the test VM (2026-11-12): after Windows Installer's files-in-use dialog had been answered, the
/// request at creation left the dialog topmost but without the keyboard (the foreground stayed with the client msiexec's progress
/// window). The foreground window is taken as it is (GetForegroundWindow): no window is looked up by class or title, and the attach is
/// made only when that window belongs to this installation's client process (Windows Installer's CLIENTPROCESSID: msiexec.exe from the
/// command line or the Settings page, explorer.exe from Programs and Features); any other program's window gets only the plain request.
/// Before the attach the foreground window must answer WM_NULL within FRONT_PROBE_MS (SMTO_ABORTIFHUNG): with the input attached, the
/// activation change is sent to its thread synchronously, and a thread that does not pump (for example explorer's thread waiting in
/// MsiConfigureProduct) would block the uninstall. One log line says what was done and why not (client=none: no client process id;
/// client=other: the foreground is another program; busy: the probe failed). Known limit: a thread that stops pumping right after the
/// probe can still block it.
/// How long the foreground window has to answer WM_NULL before the attach (it answers at once when its thread pumps messages).
const FRONT_PROBE_MS: u32 = 500;

unsafe extern "system" fn on_notify(hwnd: HWND, msg: TASKDIALOG_NOTIFICATIONS, _: WPARAM, _: LPARAM, data: isize) -> HRESULT {
    static FRONT_DONE: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
    let h = MSIHANDLE(data as u32);
    if msg == TDN_CREATED {
        FRONT_DONE.store(false, std::sync::atomic::Ordering::SeqCst);
        // A panic may not unwind into comctl32.
        let _ = catch_unwind(|| {
            // SAFETY: the dialog's own window, alive during the notification.
            unsafe {
                let _ = ImmAssociateContextEx(hwnd, HIMC::default(), IACE_CHILDREN);
                let _ = SetWindowPos(hwnd, Some(HWND_TOPMOST), 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE);
                let _ = SetForegroundWindow(hwnd);
            }
        });
    } else if msg == TDN_TIMER && !FRONT_DONE.swap(true, std::sync::atomic::Ordering::SeqCst) {
        let _ = catch_unwind(|| {
            // SAFETY: the dialog's own window, alive during the notification; the attach is undone before returning.
            unsafe {
                let client = property(h, w!("CLIENTPROCESSID")).and_then(|v| parse_pid(&v));
                let fg = GetForegroundWindow();
                if fg.0.is_null() || fg == hwnd {
                    log(h, "AskKeep: front already");
                    return;
                }
                let mut fg_pid = 0u32;
                let fg_thread = GetWindowThreadProcessId(fg, Some(&mut fg_pid));
                let me = GetCurrentThreadId();
                let why = if client.is_none() {
                    Some("client=none")
                } else if fg_thread == 0 || fg_thread == me || client != Some(fg_pid) {
                    Some("client=other")
                } else if SendMessageTimeoutW(fg, WM_NULL, WPARAM(0), LPARAM(0), SMTO_ABORTIFHUNG | SMTO_BLOCK, FRONT_PROBE_MS, None).0 == 0 {
                    Some("busy")
                } else {
                    None
                };
                if let Some(why) = why {
                    let set = SetForegroundWindow(hwnd).as_bool();
                    log(h, &format!("AskKeep: front attach=0 {why} set={}", set as u8));
                    return;
                }
                let attached = AttachThreadInput(me, fg_thread, true).as_bool();
                let _ = BringWindowToTop(hwnd);
                let set = SetForegroundWindow(hwnd).as_bool();
                if attached {
                    let _ = AttachThreadInput(me, fg_thread, false);
                }
                log(h, &format!("AskKeep: front attach={} set={}", attached as u8, set as u8));
            }
        });
    }
    S_OK
}

// ---- KillHost ----

/// Deferred, system. Ends every process, in every session, whose full image path is the CustomActionData (the installed
/// `shanjie-host.exe`), comparing paths ignoring case; the path is read and the process ended through the same handle. Always returns success.
#[no_mangle]
pub extern "system" fn KillHost(h: MSIHANDLE) -> u32 {
    guarded(h, "KillHost", ERROR_SUCCESS.0, kill_host)
}

fn kill_host(h: MSIHANDLE) -> u32 {
    let Some(target) = property(h, w!("CustomActionData")).filter(|p| is_host_path(p)) else {
        log(h, "KillHost: the parameter is not the host's path, nothing ended");
        return ERROR_SUCCESS.0;
    };
    // SAFETY: a process snapshot, closed below.
    let snap = match unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) } {
        Ok(s) => s,
        Err(e) => {
            log(h, &format!("KillHost: snapshot error {:#x}", e.code().0));
            return ERROR_SUCCESS.0;
        }
    };
    let me = unsafe { GetCurrentProcessId() };
    let (mut ended, mut failed) = (0u32, 0u32);
    let mut e = PROCESSENTRY32W { dwSize: std::mem::size_of::<PROCESSENTRY32W>() as u32, ..Default::default() };
    // SAFETY: `e` carries its size; the snapshot is open.
    let mut more = unsafe { Process32FirstW(snap, &mut e) }.is_ok();
    while more {
        if e.th32ProcessID != 0 && e.th32ProcessID != me {
            match end_if_host(e.th32ProcessID, &target) {
                Some(true) => ended += 1,
                Some(false) => failed += 1,
                None => {}
            }
        }
        // SAFETY: as above.
        more = unsafe { Process32NextW(snap, &mut e) }.is_ok();
    }
    // SAFETY: opened above.
    unsafe {
        let _ = CloseHandle(snap);
    }
    log(h, &format!("KillHost: ended {ended} failed {failed}"));
    ERROR_SUCCESS.0
}

/// `Some(ended)` when `pid`'s image is `target`; `None` when it is not, or cannot be opened or asked. One handle for the path and the end,
/// so a reused process id cannot be ended by mistake.
fn end_if_host(pid: u32, target: &[u16]) -> Option<bool> {
    // SAFETY: the handle is closed below.
    let p = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE, false, pid) }.ok()?;
    let mut buf = [0u16; 1024];
    let mut n = buf.len() as u32;
    // SAFETY: `buf` holds `n` units; on success `n` is the length without the NUL.
    let ours = unsafe { QueryFullProcessImageNameW(p, PROCESS_NAME_WIN32, PWSTR(buf.as_mut_ptr()), &mut n) }.is_ok() && same_path(&buf[..n as usize], target);
    // SAFETY: the process handle opened above.
    let r = ours.then(|| unsafe { TerminateProcess(p, 1) }.is_ok());
    unsafe {
        let _ = CloseHandle(p);
    }
    r
}

// ---- DeleteSettings ----

/// Deferred, impersonated. Deletes `HKCU\Software\Shanjie` of the user the action runs as (`RegOpenCurrentUser`), every layer opened with
/// REG_OPTION_OPEN_LINK so a link is deleted as itself and never followed; enumeration and deletion go through the opened handles
/// (`NtDeleteKey`), never a name looked up again. Does nothing for SYSTEM, LocalService or NetworkService. Always returns success.
#[no_mangle]
pub extern "system" fn DeleteSettings(h: MSIHANDLE) -> u32 {
    guarded(h, "DeleteSettings", ERROR_SUCCESS.0, delete_settings)
}

fn delete_settings(h: MSIHANDLE) -> u32 {
    if !tokens_are_a_users(|_| true) {
        log(h, "DeleteSettings: a service account or an unreadable token, nothing deleted");
        return ERROR_SUCCESS.0;
    }
    let Some(layers) = settings_layers() else {
        log(h, "DeleteSettings: no settings key, nothing deleted");
        return ERROR_SUCCESS.0;
    };
    let mut root = HKEY::default();
    // SAFETY: closed below.
    let r = unsafe { RegOpenCurrentUser(KEY_READ.0, &mut root) };
    if r != ERROR_SUCCESS {
        log(h, &format!("DeleteSettings: RegOpenCurrentUser error {}", r.0));
        return ERROR_SUCCESS.0;
    }
    let outcome = delete_path(root, &layers);
    // SAFETY: opened above.
    unsafe {
        let _ = RegCloseKey(root);
    }
    log(h, &format!("DeleteSettings: {outcome}"));
    ERROR_SUCCESS.0
}

fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain([0]).collect()
}

/// `parent\name`, opened as itself if it is a link. `name` is NUL-terminated.
fn open_link(parent: HKEY, name: &[u16], access: REG_SAM_FLAGS) -> Result<HKEY, WIN32_ERROR> {
    let mut k = HKEY::default();
    // SAFETY: `name` is NUL-terminated; the caller closes the key.
    let r = unsafe { RegOpenKeyExW(parent, PCWSTR(name.as_ptr()), Some(REG_OPTION_OPEN_LINK.0), access, &mut k) };
    if r == ERROR_SUCCESS {
        Ok(k)
    } else {
        Err(r)
    }
}

fn close(k: HKEY) {
    // SAFETY: a key this module opened.
    unsafe {
        let _ = RegCloseKey(k);
    }
}

/// Opens the layers down to the last and deletes the last with everything under it. Returns a log word.
fn delete_path(root: HKEY, layers: &[&str]) -> String {
    let (last, parents) = layers.split_last().expect("settings_layers has two or more layers");
    let mut opened: Vec<HKEY> = Vec::new();
    let mut parent = root;
    let mut outcome = None;
    for name in parents {
        match open_link(parent, &wide(name), KEY_QUERY_VALUE) {
            Ok(k) => {
                opened.push(k);
                parent = k;
            }
            Err(e) => {
                outcome = Some(if e == windows::Win32::Foundation::ERROR_FILE_NOT_FOUND { "absent".to_string() } else { format!("open error {}", e.0) });
                break;
            }
        }
    }
    let outcome = outcome.unwrap_or_else(|| match open_link(parent, &wide(last), KEY_ENUMERATE_SUB_KEYS | DELETE) {
        Ok(k) => {
            let ok = delete_key(k, 0);
            close(k);
            if ok { "deleted".to_string() } else { "not all deleted".to_string() }
        }
        Err(e) if e == windows::Win32::Foundation::ERROR_FILE_NOT_FOUND => "absent".to_string(),
        Err(e) => format!("open error {}", e.0),
    });
    for k in opened.into_iter().rev() {
        close(k);
    }
    outcome
}

/// Deletes every subkey of `key` (each opened with REG_OPTION_OPEN_LINK relative to `key`), then `key` itself through its handle. A link key
/// has no subkeys of its own, so only the link goes. False if anything stayed.
fn delete_key(key: HKEY, depth: usize) -> bool {
    if depth > MAX_DEPTH {
        return false;
    }
    let mut index = 0u32;
    for _ in 0..MAX_CHILDREN {
        // A key name is at most 255 units.
        let mut name = [0u16; 256];
        let mut n = name.len() as u32;
        // SAFETY: `name` holds `n` units; on success it is NUL-terminated and `n` is the length without the NUL.
        let r = unsafe { RegEnumKeyExW(key, index, Some(PWSTR(name.as_mut_ptr())), &mut n, None, None, None, None) };
        if r == ERROR_NO_MORE_ITEMS {
            break;
        }
        if r != ERROR_SUCCESS {
            return false;
        }
        let gone = match open_link(key, &name[..=n as usize], KEY_ENUMERATE_SUB_KEYS | DELETE) {
            Ok(child) => {
                let ok = delete_key(child, depth + 1);
                close(child);
                ok
            }
            Err(_) => false,
        };
        if !gone {
            // It stays: step over it so the enumeration ends.
            index += 1;
        }
    }
    // SAFETY: a registry handle opened with DELETE; a key with subkeys left is not deleted (the call fails).
    unsafe { NtDeleteKey(HANDLE(key.0)) }.is_ok()
}
