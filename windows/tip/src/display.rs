//! The composition underline as a TSF display attribute (docs/spec/windows-tip.md §3.10): a system-native mechanism, because system components come first
//! for every UI surface. The host draws it in the text colour; the style is the dashed line the Microsoft Zhuyin measured in W0.
use crate::guard;
use crate::guids::DISPLAY_ATTR_COMPOSING;
use std::cell::Cell;
use windows::core::BSTR;
use windows::Win32::Foundation::{E_INVALIDARG, E_NOTIMPL, S_FALSE};
use windows::Win32::UI::TextServices::{
    IEnumTfDisplayAttributeInfo, IEnumTfDisplayAttributeInfo_Impl, ITfDisplayAttributeInfo, ITfDisplayAttributeInfo_Impl, TF_DISPLAYATTRIBUTE, TF_ATTR_INPUT, TF_CT_NONE,
    TF_DA_COLOR, TF_DA_LINESTYLE,
};
use windows_core::{implement, Error, Result, GUID};

/// `TF_LS_DASH` (msctf.h), absent from the `windows` crate.
const TF_LS_DASH: TF_DA_LINESTYLE = TF_DA_LINESTYLE(3);

fn none_color() -> TF_DA_COLOR {
    TF_DA_COLOR { r#type: TF_CT_NONE, ..Default::default() }
}

fn attribute() -> TF_DISPLAYATTRIBUTE {
    TF_DISPLAYATTRIBUTE { crText: none_color(), crBk: none_color(), lsStyle: TF_LS_DASH, fBoldLine: false.into(), crLine: none_color(), bAttr: TF_ATTR_INPUT }
}

#[implement(ITfDisplayAttributeInfo)]
pub struct DisplayInfo;

impl ITfDisplayAttributeInfo_Impl for DisplayInfo_Impl {
    fn GetGUID(&self) -> Result<GUID> {
        guard::com(|| Ok(DISPLAY_ATTR_COMPOSING))
    }
    fn GetDescription(&self) -> Result<BSTR> {
        guard::com(|| Ok(BSTR::from("shanjie composition")))
    }
    fn GetAttributeInfo(&self, pda: *mut TF_DISPLAYATTRIBUTE) -> Result<()> {
        guard::com(|| {
            if pda.is_null() {
                return Err(Error::from_hresult(E_INVALIDARG));
            }
            // SAFETY: checked non-null; the caller passes room for one structure.
            unsafe { *pda = attribute() };
            Ok(())
        })
    }
    fn SetAttributeInfo(&self, _pda: *const TF_DISPLAYATTRIBUTE) -> Result<()> {
        guard::com(|| Err(Error::from_hresult(E_NOTIMPL)))
    }
    fn Reset(&self) -> Result<()> {
        guard::com(|| Ok(()))
    }
}

/// Enumerates the one attribute.
#[implement(IEnumTfDisplayAttributeInfo)]
pub struct DisplayEnum {
    pos: Cell<u32>,
}

impl DisplayEnum {
    pub fn new() -> Self {
        Self { pos: Cell::new(0) }
    }
}

impl IEnumTfDisplayAttributeInfo_Impl for DisplayEnum_Impl {
    fn Clone(&self) -> Result<IEnumTfDisplayAttributeInfo> {
        guard::com(|| {
            let e = DisplayEnum { pos: Cell::new(self.pos.get()) };
            Ok(e.into())
        })
    }
    fn Next(&self, ulcount: u32, rginfo: *mut Option<ITfDisplayAttributeInfo>, pcfetched: *mut u32) -> Result<()> {
        guard::com(|| {
            let mut fetched = 0u32;
            if !rginfo.is_null() && ulcount >= 1 && self.pos.get() == 0 {
                // SAFETY: the caller passes room for `ulcount` (>= 1) slots; the slot is overwritten, not read.
                unsafe { rginfo.write(Some(DisplayInfo.into())) };
                self.pos.set(1);
                fetched = 1;
            }
            if !pcfetched.is_null() {
                // SAFETY: checked non-null.
                unsafe { *pcfetched = fetched };
            }
            // The enumerator protocol answers S_FALSE when fewer than asked came back; `Result` has no other way to say a success code.
            if fetched == ulcount {
                Ok(())
            } else {
                Err(Error::from_hresult(S_FALSE))
            }
        })
    }
    fn Reset(&self) -> Result<()> {
        guard::com(|| {
            self.pos.set(0);
            Ok(())
        })
    }
    fn Skip(&self, ulcount: u32) -> Result<()> {
        guard::com(|| {
            let p = self.pos.get().saturating_add(ulcount);
            self.pos.set(p.min(1));
            if p <= 1 {
                Ok(())
            } else {
                Err(Error::from_hresult(S_FALSE))
            }
        })
    }
}
