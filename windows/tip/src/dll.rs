//! COM entry points and the development registration (docs/spec/windows-tip.md §3.2). `DllMain` only records the module handle.
use crate::guard;
use crate::guids::{CLSID_SHANJIE_DEV, LANGID_ZH_TW, PROFILE_NAME, PROFILE_SHANJIE_DEV};
use crate::tip::TextService;
use std::ffi::{c_void, OsString};
use std::os::windows::ffi::OsStringExt;
use std::path::PathBuf;
use std::sync::atomic::{AtomicIsize, AtomicU32, Ordering};
use windows::core::{BOOL, GUID, HRESULT, PCWSTR};
use windows::Win32::Foundation::{CLASS_E_CLASSNOTAVAILABLE, CLASS_E_NOAGGREGATION, ERROR_FILE_NOT_FOUND, E_POINTER, HINSTANCE, HMODULE, S_FALSE, S_OK, TRUE};
use windows::Win32::System::Com::{CoCreateInstance, CoInitializeEx, CoUninitialize, IClassFactory, IClassFactory_Impl, CLSCTX_INPROC_SERVER, COINIT_APARTMENTTHREADED};
use windows::Win32::System::LibraryLoader::GetModuleFileNameW;
use windows::Win32::System::Registry::{RegCloseKey, RegCreateKeyExW, RegDeleteTreeW, RegSetValueExW, HKEY, HKEY_LOCAL_MACHINE, KEY_WRITE, REG_OPTION_NON_VOLATILE, REG_SZ};
use windows::Win32::UI::Input::KeyboardAndMouse::HKL;
use windows::Win32::UI::TextServices::{
    CLSID_TF_CategoryMgr, CLSID_TF_InputProcessorProfiles, ITfCategoryMgr, ITfInputProcessorProfileMgr, GUID_TFCAT_DISPLAYATTRIBUTEPROVIDER, GUID_TFCAT_TIPCAP_COMLESS,
    GUID_TFCAT_TIPCAP_IMMERSIVESUPPORT, GUID_TFCAT_TIP_KEYBOARD,
};
use windows_core::{implement, Error, IUnknown, Interface, Ref, Result};

static MODULE: AtomicIsize = AtomicIsize::new(0);
/// Live `TextService` objects and `LockServer` locks, for `DllCanUnloadNow`.
pub static OBJECTS: AtomicU32 = AtomicU32::new(0);
static LOCKS: AtomicU32 = AtomicU32::new(0);

fn err(h: HRESULT) -> Error {
    Error::from_hresult(h)
}

/// Records the module handle and nothing else (docs/spec/windows-tip.md §3.2).
#[no_mangle]
pub extern "system" fn DllMain(hinst: HINSTANCE, _reason: u32, _reserved: *mut c_void) -> BOOL {
    MODULE.store(hinst.0 as isize, Ordering::Relaxed);
    TRUE
}

/// This DLL's module handle (the window class of the candidate window is its own).
pub fn module() -> HMODULE {
    HMODULE(MODULE.load(Ordering::Relaxed) as *mut c_void)
}

/// The full path of this DLL. A buffer that is too small is doubled; a path that never fits gives `None` (no truncated prefix is used).
pub fn module_path() -> Option<PathBuf> {
    let h = module();
    let mut cap = 512usize;
    loop {
        let mut buf = vec![0u16; cap];
        // SAFETY: `buf` is a live buffer of `cap` units.
        let n = unsafe { GetModuleFileNameW(Some(h), &mut buf) } as usize;
        if n == 0 {
            return None;
        }
        if n < cap {
            return Some(PathBuf::from(OsString::from_wide(&buf[..n])));
        }
        if cap >= 65536 {
            return None;
        }
        cap *= 2;
    }
}

#[implement(IClassFactory)]
struct Factory;

impl IClassFactory_Impl for Factory_Impl {
    fn CreateInstance(&self, punkouter: Ref<'_, IUnknown>, riid: *const GUID, ppvobject: *mut *mut c_void) -> Result<()> {
        guard::com(|| {
            if ppvobject.is_null() || riid.is_null() {
                return Err(err(E_POINTER));
            }
            // SAFETY: checked non-null; TSF passes a valid out pointer.
            unsafe { *ppvobject = std::ptr::null_mut() };
            if !punkouter.is_null() {
                return Err(err(CLASS_E_NOAGGREGATION));
            }
            let obj: IUnknown = TextService::new().into();
            // SAFETY: valid IID and out pointer, checked above.
            unsafe { obj.query(riid, ppvobject) }.ok()
        })
    }

    fn LockServer(&self, flock: BOOL) -> Result<()> {
        guard::com(|| {
            if flock.as_bool() {
                LOCKS.fetch_add(1, Ordering::AcqRel);
            } else {
                let _ = LOCKS.fetch_update(Ordering::AcqRel, Ordering::Acquire, |n| n.checked_sub(1));
            }
            Ok(())
        })
    }
}

/// # Safety
/// Called by COM with valid pointers (checked for null here).
#[no_mangle]
pub unsafe extern "system" fn DllGetClassObject(rclsid: *const GUID, riid: *const GUID, ppv: *mut *mut c_void) -> HRESULT {
    guard::com(|| {
        if ppv.is_null() || rclsid.is_null() || riid.is_null() {
            return Err(err(E_POINTER));
        }
        // SAFETY: checked non-null above.
        unsafe {
            *ppv = std::ptr::null_mut();
            if *rclsid != CLSID_SHANJIE_DEV {
                return Err(err(CLASS_E_CLASSNOTAVAILABLE));
            }
            let f: IClassFactory = Factory.into();
            f.query(riid, ppv).ok()
        }
    })
    .into()
}

/// Once a connection to the host existed, or the candidate window's class was registered, the answer is always `S_FALSE`: a thread timer, thread-local
/// destructors and a window procedure of this DLL may still be in use (docs/spec/windows-tip.md §5.4, §3.15).
#[no_mangle]
pub extern "system" fn DllCanUnloadNow() -> HRESULT {
    guard::run(S_FALSE, || if OBJECTS.load(Ordering::Acquire) == 0 && LOCKS.load(Ordering::Acquire) == 0 && !crate::client::attempted() && !crate::cand::attempted() { S_OK } else { S_FALSE }, || {})
}

// ---- registration (development only; needs an administrator) ----

fn clsid_key() -> String {
    format!("SOFTWARE\\Classes\\CLSID\\{{{:?}}}", CLSID_SHANJIE_DEV)
}

fn wide_nul(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

fn reg_str(key: HKEY, name: Option<&str>, value: &str) -> Result<()> {
    let n = name.map(wide_nul);
    let v: Vec<u8> = wide_nul(value).into_iter().flat_map(u16::to_le_bytes).collect();
    let pn = n.as_ref().map_or(PCWSTR::null(), |n| PCWSTR(n.as_ptr()));
    // SAFETY: `pn` is NULL or NUL-terminated and outlives the call.
    unsafe { RegSetValueExW(key, pn, None, REG_SZ, Some(&v)) }.ok()
}

fn create_key(path: &str) -> Result<HKEY> {
    let p = wide_nul(path);
    let mut h = HKEY::default();
    // SAFETY: `p` is NUL-terminated; `h` is a valid out pointer.
    unsafe { RegCreateKeyExW(HKEY_LOCAL_MACHINE, PCWSTR(p.as_ptr()), None, PCWSTR::null(), REG_OPTION_NON_VOLATILE, KEY_WRITE, None, &mut h, None) }.ok()?;
    Ok(h)
}

fn register_com() -> Result<()> {
    let dll = module_path().ok_or_else(guard::fail)?;
    let base = clsid_key();
    let k = create_key(&base)?;
    let r = reg_str(k, None, PROFILE_NAME);
    // SAFETY: `k` was opened above.
    unsafe { _ = RegCloseKey(k) };
    r?;
    let k = create_key(&format!("{base}\\InprocServer32"))?;
    let r = reg_str(k, None, dll.to_str().ok_or_else(guard::fail)?).and_then(|_| reg_str(k, Some("ThreadingModel"), "Apartment"));
    // SAFETY: `k` was opened above.
    unsafe { _ = RegCloseKey(k) };
    r
}

/// The four categories of docs/spec/windows-tip.md §3.2. `TIPCAP_UIELEMENTENABLED` (no candidate UI yet) and `TIPCAP_SECUREMODE` are not claimed.
const CATEGORIES: [GUID; 4] = [GUID_TFCAT_TIP_KEYBOARD, GUID_TFCAT_TIPCAP_IMMERSIVESUPPORT, GUID_TFCAT_TIPCAP_COMLESS, GUID_TFCAT_DISPLAYATTRIBUTEPROVIDER];

/// Runs `f` with COM initialised on this thread (regsvr32 already did; this is for any other caller).
fn with_com<T>(f: impl FnOnce() -> Result<T>) -> Result<T> {
    // SAFETY: balanced with `CoUninitialize` below when it succeeded.
    let inited = unsafe { CoInitializeEx(None, COINIT_APARTMENTTHREADED) }.is_ok();
    let r = f();
    if inited {
        // SAFETY: the matching call for the successful `CoInitializeEx`.
        unsafe { CoUninitialize() };
    }
    r
}

fn register_tsf() -> Result<()> {
    with_com(|| {
        let desc: Vec<u16> = PROFILE_NAME.encode_utf16().collect();
        // SAFETY: plain COM calls with live arguments.
        unsafe {
            let profiles: ITfInputProcessorProfileMgr = CoCreateInstance(&CLSID_TF_InputProcessorProfiles, None, CLSCTX_INPROC_SERVER)?;
            profiles.RegisterProfile(&CLSID_SHANJIE_DEV, LANGID_ZH_TW, &PROFILE_SHANJIE_DEV, &desc, &[], 0, HKL::default(), 0, true, 0)?;
            let cats: ITfCategoryMgr = CoCreateInstance(&CLSID_TF_CategoryMgr, None, CLSCTX_INPROC_SERVER)?;
            for c in &CATEGORIES {
                cats.RegisterCategory(&CLSID_SHANJIE_DEV, c, &CLSID_SHANJIE_DEV)?;
            }
        }
        Ok(())
    })
}

/// Everything of `register_*` is removed one item at a time. The TSF calls (UnregisterCategory, UnregisterProfile) are best effort and
/// their results are ignored; only the registry delete decides the return (a missing key is not an error). The driver checks the keys
/// afterwards (docs/spec/windows-tip.md §3.13 (h)).
fn unregister_all() -> Result<()> {
    let mut first: Result<()> = Ok(());
    let mut note = |r: Result<()>| {
        if first.is_ok() {
            first = r;
        }
    };
    note(with_com(|| {
        // SAFETY: plain COM calls with live arguments.
        unsafe {
            let cats: ITfCategoryMgr = CoCreateInstance(&CLSID_TF_CategoryMgr, None, CLSCTX_INPROC_SERVER)?;
            for c in &CATEGORIES {
                let _ = cats.UnregisterCategory(&CLSID_SHANJIE_DEV, c, &CLSID_SHANJIE_DEV);
            }
            let profiles: ITfInputProcessorProfileMgr = CoCreateInstance(&CLSID_TF_InputProcessorProfiles, None, CLSCTX_INPROC_SERVER)?;
            let _ = profiles.UnregisterProfile(&CLSID_SHANJIE_DEV, LANGID_ZH_TW, &PROFILE_SHANJIE_DEV, 0);
        }
        Ok(())
    }));
    let p = wide_nul(&clsid_key());
    // SAFETY: `p` is NUL-terminated.
    let rc = unsafe { RegDeleteTreeW(HKEY_LOCAL_MACHINE, PCWSTR(p.as_ptr())) };
    note(if rc == ERROR_FILE_NOT_FOUND { Ok(()) } else { rc.ok() });
    first
}

#[no_mangle]
pub extern "system" fn DllRegisterServer() -> HRESULT {
    guard::com(|| register_com().and_then(|_| register_tsf())).into()
}

#[no_mangle]
pub extern "system" fn DllUnregisterServer() -> HRESULT {
    guard::com(unregister_all).into()
}
