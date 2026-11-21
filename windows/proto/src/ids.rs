//! The identity of a build: which COM class, language profile, pipe, settings key and timing folder it uses, so the development build and the
//! release build (the installer's) can be installed side by side. One build has exactly one identity, chosen by the `release-ids` feature of
//! `shanjie-win-proto` (the `shanjie-tip` and `shanjie-host` features of the same name turn it on); both sets are always compiled, so one test
//! binary can compare them. Plain data with no platform types: the GUIDs are `u128` (the Windows crates make `GUID` values from them) and the
//! module builds and is tested on every platform.
//!
//! Not part of the identity: the install folder (each exe finds its neighbours from its own path) and the candidate window's class name (a
//! class registered by a DLL is private to that module, so two DLLs in one process do not collide on it).

pub struct Ids {
    /// The text service's COM class (docs/spec/windows-tip.md §3.2).
    pub clsid: u128,
    /// The language profile (zh-TW, 0x0404).
    pub profile: u128,
    /// The display attribute of the composition (dashed underline).
    pub display_attr: u128,
    /// The profile's name in the language bar and in the COM class key.
    pub profile_name: &'static str,
    /// The start of the pipe name, up to the user SID: `\\.\pipe\<prefix><user SID>-<session id>` (§5.2).
    pub pipe_prefix: &'static str,
    /// The `HKCU` subkey of the user's settings (the host reads `KeyScheme` here, §5.2).
    pub settings_key: &'static str,
    /// The folder of the numbers-only timing files under `%LOCALAPPDATA%` (`timing` feature only, §5.8).
    pub timing_dir: &'static str,
}

/// The development build (the values before the installer existed).
pub const DEV: Ids = Ids {
    clsid: 0x3e71b7b0_e549_4c6e_b73b_36ddce57fb30,
    profile: 0xd6404d13_e3f9_4bb5_8aea_557044f21b68,
    display_attr: 0xe9fa2737_f8d5_4568_892c_d0331c7309a7,
    profile_name: "善解（開發版）",
    pipe_prefix: "shanjie-dev-",
    settings_key: r"Software\shanjie-dev",
    timing_dir: "shanjie-dev",
};

/// The release build (installed by the MSI).
pub const RELEASE: Ids = Ids {
    clsid: 0xcff5e6bc_dcce_4b58_8b53_fd7e67c8d182,
    profile: 0x0d7b85a2_a8b7_44db_9a6d_92a5534f0fd9,
    display_attr: 0xe3fcadab_c242_4981_afd3_a75259ad9881,
    profile_name: "善解",
    pipe_prefix: "shanjie-",
    settings_key: r"Software\Shanjie",
    timing_dir: "Shanjie",
};

/// The identity of this build.
#[cfg(feature = "release-ids")]
pub const IDS: Ids = RELEASE;
/// The identity of this build.
#[cfg(not(feature = "release-ids"))]
pub const IDS: Ids = DEV;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_two_identities_share_nothing() {
        let (d, r) = (&DEV, &RELEASE);
        assert!(d.clsid != r.clsid && d.profile != r.profile && d.display_attr != r.display_attr, "GUIDs");
        assert!(d.clsid != d.profile && d.clsid != d.display_attr && d.profile != d.display_attr, "dev GUIDs");
        assert!(r.clsid != r.profile && r.clsid != r.display_attr && r.profile != r.display_attr, "release GUIDs");
        assert!(d.profile_name != r.profile_name, "profile name");
        assert!(d.pipe_prefix != r.pipe_prefix, "pipe prefix");
        assert!(d.settings_key != r.settings_key, "settings key");
        assert!(d.timing_dir != r.timing_dir, "timing folder");
    }

    /// The values of this build are the literals of the identity table (release and development side by side), so a changed constant is a visible edit.
    #[test]
    fn this_build_has_the_documented_literals() {
        if cfg!(feature = "release-ids") {
            assert!(IDS.pipe_prefix == "shanjie-" && IDS.settings_key == r"Software\Shanjie" && IDS.profile_name == "善解" && IDS.timing_dir == "Shanjie");
            assert_eq!(IDS.clsid, RELEASE.clsid);
        } else {
            assert!(IDS.pipe_prefix == "shanjie-dev-" && IDS.settings_key == r"Software\shanjie-dev" && IDS.profile_name == "善解（開發版）" && IDS.timing_dir == "shanjie-dev");
            assert_eq!(IDS.clsid, 0x3e71b7b0_e549_4c6e_b73b_36ddce57fb30, "the development CLSID never changes");
        }
    }
}
