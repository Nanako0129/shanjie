//! The development GUIDs (docs/spec/windows-tip.md §3.2). A release build uses another set.
use windows_core::GUID;

/// The text service's COM class.
pub const CLSID_SHANJIE_DEV: GUID = GUID::from_u128(0x3e71b7b0_e549_4c6e_b73b_36ddce57fb30);
/// The language profile (zh-TW, 0x0404).
pub const PROFILE_SHANJIE_DEV: GUID = GUID::from_u128(0xd6404d13_e3f9_4bb5_8aea_557044f21b68);
/// The display attribute of the composition (dashed underline).
pub const DISPLAY_ATTR_COMPOSING: GUID = GUID::from_u128(0xe9fa2737_f8d5_4568_892c_d0331c7309a7);

pub const LANGID_ZH_TW: u16 = 0x0404;
pub const PROFILE_NAME: &str = "善解（開發版）";
