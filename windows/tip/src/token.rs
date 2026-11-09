//! Token facts both ends check (docs/spec/windows-tip.md §5.2 and §5.5): the user, the logon session, the integrity
//! level, AppContainer, restricted, elevated. Shared by the text service and the resident host (the host includes this file by path,
//! so it refers to nothing else in either crate). Any query that fails makes `facts` `None`: callers refuse, never assume.
use windows::core::PWSTR;
use windows::Win32::Foundation::{CloseHandle, LocalFree, APPMODEL_ERROR_NO_PACKAGE, HANDLE, HLOCAL};
use windows::Win32::Security::Authorization::ConvertSidToStringSidW;
use windows::Win32::Security::{
    GetLengthSid, GetSidSubAuthority, GetSidSubAuthorityCount, GetTokenInformation, IsValidSid, IsWellKnownSid, TokenAppContainerSid, TokenElevation, TokenIntegrityLevel,
    TokenIsAppContainer, TokenRestrictedSids, TokenSessionId, TokenUser, PSID, TOKEN_APPCONTAINER_INFORMATION, TOKEN_GROUPS, TOKEN_INFORMATION_CLASS, TOKEN_MANDATORY_LABEL, TOKEN_QUERY,
    TOKEN_USER, WinLocalServiceSid, WinLocalSystemSid, WinNetworkServiceSid,
};
use windows::Win32::Storage::Packaging::Appx::GetCurrentPackageFullName;
use windows::Win32::System::Threading::{GetCurrentProcess, OpenProcessToken};

/// SECURITY_MANDATORY_MEDIUM_RID and SECURITY_MANDATORY_SYSTEM_RID.
pub const MEDIUM_RID: u32 = 0x2000;
pub const SYSTEM_RID: u32 = 0x4000;

pub struct Facts {
    /// The user SID as bytes (two SIDs are equal exactly when their bytes are).
    pub user: Vec<u8>,
    pub session: u32,
    /// The integrity level's RID (`SECURITY_MANDATORY_*_RID`).
    pub integrity: u32,
    /// The AppContainer SID, for an AppContainer token.
    pub app_container: Option<Vec<u8>>,
    /// The token has restricting SIDs (what `IsTokenRestricted` reports, read directly so a failed query is not "unrestricted").
    pub restricted: bool,
    pub elevated: bool,
}

/// One information class into an 8-byte aligned buffer.
fn info(t: HANDLE, class: TOKEN_INFORMATION_CLASS) -> Option<Vec<u64>> {
    let mut len = 0u32;
    // SAFETY: a size query without a buffer.
    let _ = unsafe { GetTokenInformation(t, class, None, 0, &mut len) };
    if len == 0 {
        return None;
    }
    let mut buf = vec![0u64; (len as usize).div_ceil(8)];
    // SAFETY: the buffer holds `len` bytes.
    unsafe { GetTokenInformation(t, class, Some(buf.as_mut_ptr().cast()), len, &mut len) }.ok()?;
    Some(buf)
}

fn u32_info(t: HANDLE, class: TOKEN_INFORMATION_CLASS) -> Option<u32> {
    let (mut v, mut len) = (0u32, 0u32);
    // SAFETY: the classes asked for here (session id, IsAppContainer, TOKEN_ELEVATION) are one u32.
    unsafe { GetTokenInformation(t, class, Some((&mut v as *mut u32).cast()), 4, &mut len) }.ok()?;
    Some(v)
}

/// The bytes of a SID.
///
/// # Safety
/// `sid` is null or points to memory that stays valid for the call.
pub unsafe fn sid_bytes(sid: PSID) -> Option<Vec<u8>> {
    // SAFETY: caller contract; IsValidSid is checked before the length is trusted.
    unsafe {
        if sid.0.is_null() || !IsValidSid(sid).as_bool() {
            return None;
        }
        let n = GetLengthSid(sid) as usize;
        Some(std::slice::from_raw_parts(sid.0 as *const u8, n).to_vec())
    }
}

/// Everything §5.2 and §5.5 check about a token opened with `TOKEN_QUERY`.
pub fn facts(t: HANDLE) -> Option<Facts> {
    let user = info(t, TokenUser)?;
    let label = info(t, TokenIntegrityLevel)?;
    let restricted = info(t, TokenRestrictedSids)?;
    // SAFETY: each buffer starts with the structure of its class, and the SIDs point into the same buffer.
    let (user, integrity, restricted) = unsafe {
        let user = sid_bytes((*user.as_ptr().cast::<TOKEN_USER>()).User.Sid)?;
        let sid = (*label.as_ptr().cast::<TOKEN_MANDATORY_LABEL>()).Label.Sid;
        if !IsValidSid(sid).as_bool() {
            return None;
        }
        let n = *GetSidSubAuthorityCount(sid);
        if n == 0 {
            return None;
        }
        (user, *GetSidSubAuthority(sid, u32::from(n) - 1), (*restricted.as_ptr().cast::<TOKEN_GROUPS>()).GroupCount != 0)
    };
    let app_container = if u32_info(t, TokenIsAppContainer)? != 0 {
        let b = info(t, TokenAppContainerSid)?;
        // SAFETY: as above.
        Some(unsafe { sid_bytes((*b.as_ptr().cast::<TOKEN_APPCONTAINER_INFORMATION>()).TokenAppContainer)? })
    } else {
        None
    };
    Some(Facts { user, session: u32_info(t, TokenSessionId)?, integrity, app_container, restricted, elevated: u32_info(t, TokenElevation)? != 0 })
}

/// The facts of this process's own token.
pub fn process_facts() -> Option<Facts> {
    let mut t = HANDLE::default();
    // SAFETY: the pseudo handle of this process; the token is closed below.
    unsafe { OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut t) }.ok()?;
    let f = facts(t);
    // SAFETY: opened above.
    unsafe {
        let _ = CloseHandle(t);
    }
    f
}

/// SYSTEM, LocalService or NetworkService.
pub fn is_service_account(sid: &[u8]) -> bool {
    let p = PSID(sid.as_ptr() as *mut _);
    // SAFETY: `sid` holds a SID (from `sid_bytes`) and outlives the calls.
    unsafe { [WinLocalSystemSid, WinLocalServiceSid, WinNetworkServiceSid].iter().any(|k| IsWellKnownSid(p, *k).as_bool()) }
}

/// `S-1-5-21-…` for the pipe name.
pub fn sid_string(sid: &[u8]) -> Option<String> {
    let mut s = PWSTR::null();
    // SAFETY: `sid` holds a SID; the string is LocalAlloc memory, copied and freed here.
    unsafe {
        ConvertSidToStringSidW(PSID(sid.as_ptr() as *mut _), &mut s).ok()?;
        let out = s.to_string().ok();
        let _ = LocalFree(Some(HLOCAL(s.0.cast())));
        out
    }
}

/// This process has a package identity (`GetCurrentPackageFullName` answers anything but `APPMODEL_ERROR_NO_PACKAGE`).
pub fn has_package() -> bool {
    let mut len = 0u32;
    // SAFETY: a size query without a buffer.
    unsafe { GetCurrentPackageFullName(&mut len, None) != APPMODEL_ERROR_NO_PACKAGE }
}
