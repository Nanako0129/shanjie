//! The custom actions of the Windows installer (windows/installer/shanjie.wxs), exported from `installer-ca.dll`:
//!
//! - `AskKeep` (immediate, basic or reduced UI uninstall): a TaskDialog with one check box, "keep settings"; writes `KEEPSETTINGS`, or ends
//!   the uninstall when the user cancels.
//! - `KillHost` (deferred, system): ends every process whose image path is the installed `shanjie-host.exe` (its CustomActionData).
//! - `DeleteSettings` (deferred, impersonated): deletes the uninstalling user's `HKCU\Software\Shanjie` without following registry links.
//!
//! Only the Windows code is Windows-only (`ca`); the rules it applies (the answer of the dialog, the path comparison, the settings key's
//! layers) are plain functions here, built and tested on every platform.
use shanjie_win_proto::ids::RELEASE;

#[cfg(windows)]
mod ca;
// The token facts of the text service, shared by path like the host does (one copy).
#[cfg(windows)]
#[allow(dead_code)]
#[path = "../../tip/src/token.rs"]
mod token;

/// The id of the dialog's "uninstall" button (TaskDialog's custom button ids are the caller's; 100 is clear of the IDOK..IDCONTINUE range).
pub const UNINSTALL_BUTTON: i32 = 100;
/// IDCANCEL: the "cancel" button, Esc and the close box (TDF_ALLOW_DIALOG_CANCELLATION).
pub const CANCEL_BUTTON: i32 = 2;

/// What `AskKeep` does with the dialog's result.
#[derive(Debug, PartialEq, Eq)]
pub enum Answer {
    /// The user cancelled: the uninstall ends (ERROR_INSTALL_USEREXIT).
    Cancel,
    /// Go on and set `KEEPSETTINGS` to 1 (true) or 0 (false).
    Keep(bool),
    /// The dialog failed or answered something unknown: as when there is no dialog, `KEEPSETTINGS` is left alone (both kept after the
    /// normalising SetKEEPSETTINGS) and the uninstall goes on.
    AsIfNoDialog,
}

/// `shown`: `TaskDialogIndirect` succeeded; `button` and `keep_checked` are what it returned.
pub fn answer(shown: bool, button: i32, keep_checked: bool) -> Answer {
    match (shown, button) {
        (false, _) => Answer::AsIfNoDialog,
        (true, UNINSTALL_BUTTON) => Answer::Keep(keep_checked),
        (true, CANCEL_BUTTON) => Answer::Cancel,
        _ => Answer::AsIfNoDialog,
    }
}

fn fold(c: u16) -> u16 {
    if (u16::from(b'a')..=u16::from(b'z')).contains(&c) {
        c - 32
    } else {
        c
    }
}

/// Two Win32 paths name the same file for `KillHost`: equal ignoring ASCII case.
// ponytail: ASCII-only folding. It never equates two paths NTFS tells apart (NTFS folds at least ASCII), so a mismatch can only spare a
// process, never end a wrong one; the installed path is ASCII unless Program Files is on a non-ASCII path, where an equal-case path still matches.
pub fn same_path(a: &[u16], b: &[u16]) -> bool {
    a.len() == b.len() && a.iter().zip(b).all(|(x, y)| fold(*x) == fold(*y))
}

/// The process id in Windows Installer's CLIENTPROCESSID property: decimal ASCII digits only, at most 10, not 0, fits a u32. Anything else
/// is no id (the AskKeep dialog then attaches to no thread).
pub fn parse_pid(v: &[u16]) -> Option<u32> {
    if v.is_empty() || v.len() > 10 || !v.iter().all(|c| (u16::from(b'0')..=u16::from(b'9')).contains(c)) {
        return None;
    }
    let n = v.iter().fold(0u64, |n, c| n * 10 + u64::from(c - u16::from(b'0')));
    u32::try_from(n).ok().filter(|&n| n != 0)
}

/// `KillHost`'s parameter is acceptable: a drive-absolute path (`X:\…`) whose last component is `shanjie-host.exe`, with no NUL. Anything
/// else (an empty CustomActionData, a relative path, another program) ends nothing.
pub fn is_host_path(p: &[u16]) -> bool {
    let name: Vec<u16> = r"\shanjie-host.exe".encode_utf16().collect();
    p.len() > 2 + name.len()
        && u8::try_from(p[0]).is_ok_and(|c| c.is_ascii_alphabetic())
        && p[1] == u16::from(b':')
        && p[2] == u16::from(b'\\')
        && !p.contains(&0)
        && same_path(&p[p.len() - name.len()..], &name)
}

/// The settings key `DeleteSettings` removes, as the layers it opens one by one under the user's hive: the RELEASE identity's
/// `Software\Shanjie` (the installer only installs that identity). `None` if the table held an empty layer.
pub fn settings_layers() -> Option<Vec<&'static str>> {
    let v: Vec<&'static str> = RELEASE.settings_key.split('\\').collect();
    (v.len() >= 2 && v.iter().all(|s| !s.is_empty())).then_some(v)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn w(s: &str) -> Vec<u16> {
        s.encode_utf16().collect()
    }

    #[test]
    fn the_client_process_id() {
        assert_eq!(parse_pid(&w("3900")), Some(3900));
        assert_eq!(parse_pid(&w("4294967295")), Some(u32::MAX));
        for bad in ["", "0", "4294967296", "12345678901", "-1", " 12", "12 ", "1a", "１２"] {
            assert_eq!(parse_pid(&w(bad)), None, "{bad:?}");
        }
    }

    #[test]
    fn the_dialog_answer() {
        assert_eq!(answer(true, UNINSTALL_BUTTON, true), Answer::Keep(true));
        assert_eq!(answer(true, UNINSTALL_BUTTON, false), Answer::Keep(false));
        assert_eq!(answer(true, CANCEL_BUTTON, false), Answer::Cancel);
        assert_eq!(answer(true, CANCEL_BUTTON, true), Answer::Cancel);
        // A failed dialog keeps both and goes on, whatever the out values hold.
        assert_eq!(answer(false, UNINSTALL_BUTTON, false), Answer::AsIfNoDialog);
        assert_eq!(answer(false, CANCEL_BUTTON, false), Answer::AsIfNoDialog);
        assert_eq!(answer(true, 0, false), Answer::AsIfNoDialog, "no button");
        assert_eq!(answer(true, 1, false), Answer::AsIfNoDialog, "IDOK is not one of ours");
    }

    #[test]
    fn paths_compare_ignoring_ascii_case_only() {
        let p = w(r"C:\Program Files\Shanjie\shanjie-host.exe");
        assert!(same_path(&p, &w(r"c:\PROGRAM FILES\shanjie\SHANJIE-HOST.EXE")));
        assert!(!same_path(&p, &w(r"C:\Program Files\shanjie-dev\shanjie-host.exe")), "the development build's host");
        assert!(!same_path(&p, &w(r"C:\Program Files\Shanjie\shanjie-host.exe ")), "a longer path");
        assert!(!same_path(&p, &w(r"C:\Program Files\Shanjie\shanjie-host.ex")), "a shorter path");
        assert!(!same_path(&p, &w(r"D:\Program Files\Shanjie\shanjie-host.exe")), "another drive");
        assert!(!same_path(&w("É"), &w("é")), "non-ASCII letters are compared exactly");
        assert!(!same_path(&w("@"), &w("`")), "only letters fold");
        assert!(same_path(&[], &[]));
    }

    #[test]
    fn only_a_drive_absolute_host_path_is_a_parameter() {
        assert!(is_host_path(&w(r"C:\Program Files\Shanjie\shanjie-host.exe")));
        assert!(is_host_path(&w(r"d:\x\SHANJIE-HOST.EXE")));
        assert!(!is_host_path(&w("")), "empty CustomActionData");
        assert!(!is_host_path(&w("shanjie-host.exe")), "a bare name");
        assert!(!is_host_path(&w(r"C:shanjie-host.exe")), "drive-relative");
        assert!(!is_host_path(&w(r"\\server\share\shanjie-host.exe")), "UNC");
        assert!(!is_host_path(&w(r"C:\Program Files\Shanjie\notepad.exe")), "another program");
        assert!(!is_host_path(&w(r"C:\Program Files\Shanjie\xshanjie-host.exe")), "a name that only ends the same");
        assert!(!is_host_path(&w(r"C:\shanjie-host.exe.bak")));
        assert!(!is_host_path(&w("C:\\a\0\\shanjie-host.exe")), "a NUL inside");
        assert!(!is_host_path(&w(r"C:\shanjie-host.exe")), "the drive root: never where the installer puts it");
    }

    #[test]
    fn the_settings_key_is_the_release_identitys() {
        assert_eq!(settings_layers(), Some(vec!["Software", "Shanjie"]));
        assert_eq!(RELEASE.settings_key, r"Software\Shanjie");
    }
}
