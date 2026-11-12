//! The GUIDs and the profile name of this build (docs/spec/windows-tip.md §3.2): the development set or the release set, by the `release-ids`
//! feature (the values are in `shanjie_win_proto::ids`, which every platform builds and tests).
use shanjie_win_proto::ids::IDS;
use windows_core::GUID;

/// The text service's COM class.
pub const CLSID_SHANJIE: GUID = GUID::from_u128(IDS.clsid);
/// The language profile (zh-TW, 0x0404).
pub const PROFILE_SHANJIE: GUID = GUID::from_u128(IDS.profile);
/// The display attribute of the composition (dashed underline).
pub const DISPLAY_ATTR_COMPOSING: GUID = GUID::from_u128(IDS.display_attr);

pub const LANGID_ZH_TW: u16 = 0x0404;
pub const PROFILE_NAME: &str = IDS.profile_name;
