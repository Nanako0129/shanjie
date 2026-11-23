//! The resident host's command line and its identity-dependent settings path, apart from the Windows code so they are tested on every platform.
use shanjie_win_proto::ids::IDS;

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

/// The `HKCU` subkey the host reads `KeyScheme` from: this build's settings key (`Software\shanjie-dev` or `Software\Shanjie`).
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

    /// The path the host reads `KeyScheme` from is the identity table's, never a literal of its own.
    #[test]
    fn the_key_scheme_is_read_from_this_builds_settings_key() {
        assert_eq!(settings_subkey(), IDS.settings_key);
        assert_eq!(settings_subkey(), if cfg!(feature = "release-ids") { r"Software\Shanjie" } else { r"Software\shanjie-dev" });
    }
}
