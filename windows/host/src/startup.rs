//! The resident host's command line and its identity-dependent settings path, apart from the Windows code so they are tested on every platform.
use shanjie_win_proto::ids::IDS;

/// Exit codes of the host that the portable modules use (numbers only, R2); the others are in `server`.
/// The engine lock or the rerank lock is poisoned.
pub const EXIT_POISONED: i32 = 6;
/// The rerank thread could not be started, or ended (it never returns: a panic).
pub const EXIT_RERANK: i32 = 9;

pub fn exit(code: i32) -> ! {
    std::process::exit(code)
}

/// `HKCU\<this build's settings key>\Rerank` (a DWORD, read like `KeyScheme`: `rc` is the return code of `RegGetValueW` with `RRF_RT_REG_DWORD`, `data` the value): whether the
/// host turns the semantic rerank on, and where that came from (event 76): 0 no value, 1 a valid value (0 or 1), 2 an invalid one. Only a readable DWORD 0 turns it
/// off; no value, a value of another type, any other error and any other number are "on" (docs/spec/windows-tip.md §5.2).
pub fn rerank_from(rc: u32, data: u32) -> (bool, u64) {
    const ERROR_SUCCESS: u32 = 0;
    const ERROR_FILE_NOT_FOUND: u32 = 2;
    const ERROR_PATH_NOT_FOUND: u32 = 3;
    match rc {
        ERROR_SUCCESS if data <= 1 => (data == 1, 1),
        ERROR_FILE_NOT_FOUND | ERROR_PATH_NOT_FOUND => (true, 0),
        _ => (true, 2),
    }
}

/// How the host was started (docs/spec/windows-tip.md §5.2).
#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub enum Launch {
    /// Started by the text service DLL (`--resident`), or by anything else: no check before the start.
    Resident,
    /// Started at logon by the installer's Run value (`--resident --logon`): ends at once unless this build's profile is enabled for the user.
    Logon,
}

/// `args` is the command line without the program name. Exactly `["--resident", "--logon"]` is `Logon`; every other list, however long, in whatever
/// order and with whatever words, is `Resident`: an unknown argument never ends the process and nothing is printed.
pub fn parse_args(args: &[String]) -> Launch {
    match args {
        [a, b] if a == "--resident" && b == "--logon" => Launch::Logon,
        _ => Launch::Resident,
    }
}

/// The `HKCU` subkey the host reads `KeyScheme` and `Rerank` from: this build's settings key (`Software\shanjie-dev` or `Software\Shanjie`).
pub fn settings_subkey() -> &'static str {
    IDS.settings_key
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(a: &[&str]) -> Launch {
        parse_args(&a.iter().map(|s| s.to_string()).collect::<Vec<_>>())
    }

    #[test]
    fn only_resident_logon_in_that_order_is_a_logon_start() {
        assert_eq!(parse(&["--resident", "--logon"]), Launch::Logon);
        assert_eq!(parse(&[]), Launch::Resident);
        assert_eq!(parse(&["--resident"]), Launch::Resident);
        assert_eq!(parse(&["--logon"]), Launch::Resident, "--logon alone");
        assert_eq!(parse(&["--logon", "--resident"]), Launch::Resident, "the other order");
        assert_eq!(parse(&["--resident", "--foo"]), Launch::Resident, "an unknown argument");
        assert_eq!(parse(&["--resident", "--logon", "--foo"]), Launch::Resident, "an extra argument after a logon start");
        assert_eq!(parse(&["--resident", "--LOGON"]), Launch::Resident, "case matters");
        assert_eq!(parse(&["", ""]), Launch::Resident);
    }

    /// docs/spec/windows-tip.md §5.2: only a DWORD 0 turns the rerank off.
    #[test]
    fn the_rerank_value_is_on_unless_it_is_a_dword_zero() {
        const ERROR_MORE_DATA: u32 = 234;
        const ERROR_UNSUPPORTED_TYPE: u32 = 1630;
        assert!(rerank_from(0, 0) == (false, 1), "a valid 0 turns it off");
        assert!(rerank_from(0, 1) == (true, 1), "a valid 1");
        for v in [2, 3, 255, 0x8000_0000, u32::MAX] {
            assert!(rerank_from(0, v) == (true, 2), "a DWORD other than 0 or 1 is on, invalid");
        }
        assert!(rerank_from(2, 0) == (true, 0) && rerank_from(3, 0) == (true, 0), "no value is on");
        for rc in [ERROR_MORE_DATA, ERROR_UNSUPPORTED_TYPE, 5, 87] {
            assert!(rerank_from(rc, 0) == (true, 2) && rerank_from(rc, 1) == (true, 2), "any other error is on, invalid");
        }
    }

    /// The path the host reads `KeyScheme` from is the identity table's, never a literal of its own.
    #[test]
    fn the_key_scheme_is_read_from_this_builds_settings_key() {
        assert_eq!(settings_subkey(), IDS.settings_key);
        assert_eq!(settings_subkey(), if cfg!(feature = "release-ids") { r"Software\Shanjie" } else { r"Software\shanjie-dev" });
    }
}
