//! The candidate window (docs/spec/windows-tip.md §3.15): a layered Win32 popup in the host app's own process, drawn with Direct2D and DirectWrite,
//! because a window of another process cannot be put over the Start menu and Windows Search.
//!
//! What it shows is what the host's answer holds (`Out.candidates`, the selection, the window position in the list); it never chooses. Text
//! only goes out of this module as pixels: a key that picks a word is sent to the host like any other key and the core decides what is committed.
//! R2 (docs/spec/windows-tip.md §3.12): the candidate texts live in `Show` and in the layouts of one drawing, are never copied into a log, the timing file, an
//! error, a window title, a window property or `OutputDebugString`, and are gone when `show` returns; the DIB is freed as soon as the window has
//! taken it, and a hidden window holds a 1 x 1 transparent surface. `Show` has no `Debug`.
//!
//! One window per UI thread at most (`STATE`, a thread local compared by `HWND`; no pointer is ever put in the window's memory, so another
//! process cannot rewrite it). Everything that touches the window runs on the thread that made it, outside an edit session (`capture` only reads the
//! text rectangle inside one), under `guard::run` (a panic must not leave the window procedure). The Direct2D and DirectWrite factories are
//! one pair per process, made on a background thread by the first key the TIP eats (`prewarm`; every GUI process loads the TIP, most never type). The render target is a
//! software one (no GPU driver loaded into every app, no device loss) and is made again for each drawing.
use crate::candlayout::{self, Metrics, Rect};
use crate::dll;
use crate::guard;
use crate::timing;
use shanjie_win_proto::Out;
use std::ffi::c_void;
use std::sync::atomic::{AtomicBool, AtomicI8, Ordering};
use std::sync::{Mutex, Once, PoisonError};
use std::cell::RefCell;
use windows::core::{w, BOOL, PCWSTR};
use windows::Win32::Foundation::{COLORREF, HWND, LPARAM, LRESULT, POINT, RECT, SIZE, WPARAM};
use windows::Win32::Graphics::Direct2D::Common::{D2D1_ALPHA_MODE_PREMULTIPLIED, D2D1_COLOR_F, D2D1_PIXEL_FORMAT, D2D_RECT_F};
use windows::Win32::Graphics::Direct2D::{
    D2D1CreateFactory, ID2D1Factory, ID2D1SolidColorBrush, D2D1_DEBUG_LEVEL_NONE, D2D1_DRAW_TEXT_OPTIONS, D2D1_DRAW_TEXT_OPTIONS_CLIP, D2D1_DRAW_TEXT_OPTIONS_ENABLE_COLOR_FONT,
    D2D1_FACTORY_OPTIONS, D2D1_FACTORY_TYPE_MULTI_THREADED, D2D1_FEATURE_LEVEL_DEFAULT, D2D1_RENDER_TARGET_PROPERTIES, D2D1_RENDER_TARGET_TYPE_SOFTWARE, D2D1_RENDER_TARGET_USAGE_NONE,
    D2D1_ROUNDED_RECT, D2D1_TEXT_ANTIALIAS_MODE_GRAYSCALE,
};
use windows::Win32::Graphics::DirectWrite::{
    DWriteCreateFactory, IDWriteFactory, IDWriteTextFormat, IDWriteTextLayout, DWRITE_FACTORY_TYPE_SHARED, DWRITE_FONT_STRETCH_NORMAL, DWRITE_FONT_STYLE_NORMAL, DWRITE_FONT_WEIGHT_NORMAL,
    DWRITE_PARAGRAPH_ALIGNMENT_CENTER, DWRITE_TEXT_ALIGNMENT_LEADING, DWRITE_TEXT_METRICS, DWRITE_TRIMMING, DWRITE_TRIMMING_GRANULARITY_CHARACTER, DWRITE_WORD_WRAPPING_NO_WRAP,
};
use windows::Win32::Graphics::Dxgi::Common::DXGI_FORMAT_B8G8R8A8_UNORM;
use windows::Win32::Graphics::Gdi::{
    CreateCompatibleDC, CreateDIBSection, DeleteDC, DeleteObject, GetMonitorInfoW, MonitorFromRect, SelectObject, AC_SRC_ALPHA, AC_SRC_OVER, BITMAPINFO, BITMAPINFOHEADER, BI_RGB, BLENDFUNCTION,
    DIB_RGB_COLORS, HBITMAP, HDC, HGDIOBJ, MONITORINFO, MONITOR_DEFAULTTONEAREST,
};
use windows::Win32::System::Com::CoTaskMemFree;
use windows::Win32::System::Registry::{RegGetValueW, HKEY_CURRENT_USER, RRF_RT_REG_DWORD};
use windows::Win32::System::Threading::GetCurrentThreadId;
use windows::Win32::UI::Accessibility::NotifyWinEvent;
use windows::Win32::UI::HiDpi::{GetDpiForWindow, SetThreadDpiAwarenessContext, DPI_AWARENESS_CONTEXT, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2};
use windows::Win32::UI::Input::KeyboardAndMouse::GetFocus;
use windows::Win32::UI::TextServices::{ITfContext, ITfInputScope, GUID_PROP_INPUTSCOPE, IS_PASSWORD, InputScope};
use windows::Win32::UI::WindowsAndMessaging::{
    CreateWindowExW, DefWindowProcW, DestroyWindow, GetAncestor, GetWindowThreadProcessId, IsWindow, KillTimer, RegisterClassExW, SetTimer, SetWindowPos, ShowWindow, UpdateLayeredWindow,
    EVENT_OBJECT_IME_CHANGE, EVENT_OBJECT_IME_HIDE, EVENT_OBJECT_IME_SHOW, GA_ROOT, MA_NOACTIVATE, OBJID_CLIENT, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOREDRAW, SWP_NOSIZE, SWP_NOZORDER, SWP_SHOWWINDOW, SW_HIDE,
    HWND_TOPMOST, ULW_ALPHA, USER_TIMER_MINIMUM, WM_MOUSEACTIVATE, WM_NCDESTROY, WM_SETTINGCHANGE, WM_TIMER, WNDCLASSEXW, WS_EX_LAYERED, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TOPMOST,
    WS_POPUP,
};
use windows_core::Interface;

// ---- sizes and colours (docs/spec/windows-tip.md §4.3) ----

/// The window class: the acceptance run finds the window by this name.
const CLASS_NAME: PCWSTR = w!("ShanjieDevCandidate");
/// The font: the system UI font of Traditional Chinese Windows, taken from the system collection only (no font file loader, no path, no variable).
const FONT: PCWSTR = w!("Microsoft JhengHei UI");
const LOCALE: PCWSTR = w!("zh-TW");
/// A text wider than the window is cut at a character with an ellipsis.
const TRIM: DWRITE_TRIMMING = DWRITE_TRIMMING { granularity: DWRITE_TRIMMING_GRANULARITY_CHARACTER, delimiter: 0, delimiterCount: 0 };

/// Window, border, selected row, selection bar, candidate text, number (and the number on the selected row). 24-bit values of Microsoft Zhuyin's
/// candidate window in the test VM (Notepad, 100 %, docs/spec/windows-tip.md §4.3 "顏色（24 位元）"); the RGB565 readings of §4.3 had a colour cast
/// (white with a pink tint, dark grey with a green one) that the real neutral greys do not have.
struct Theme {
    window: u32,
    border: u32,
    selected: u32,
    bar: u32,
    text: u32,
    number: u32,
    number_selected: u32,
}

const LIGHT: Theme = Theme { window: 0xF9F9F9, border: 0xEAEAEA, selected: 0xF0F0F0, bar: 0x0067C0, text: 0x1A1A1A, number: 0x5F5F5F, number_selected: 0x5B5B5B };
const DARK: Theme = Theme { window: 0x2C2C2C, border: 0x1F1F1F, selected: 0x383838, bar: 0x4CC2FF, text: 0xFFFFFF, number: 0xCFCFCF, number_selected: 0xD2D2D2 };

fn color(rgb: u32) -> D2D1_COLOR_F {
    let c = |shift: u32| ((rgb >> shift) & 0xFF) as f32 / 255.0;
    D2D1_COLOR_F { r: c(16), g: c(8), b: c(0), a: 1.0 }
}

fn rect_f(r: Rect) -> D2D_RECT_F {
    D2D_RECT_F { left: r.left as f32, top: r.top as f32, right: r.right as f32, bottom: r.bottom as f32 }
}

/// The point `DrawTextLayout` wants is a `windows_numerics::Vector2`: two `f32`, `repr(C)`, X then Y. The `windows` crate does not re-export that type, and naming it
/// would add a dependency edge to the lock, so the value is made from its layout; the target type is inferred from the call, and `transmute` checks the size.
fn origin<T>(x: f32, y: f32) -> T {
    const { assert!(std::mem::size_of::<T>() == 8) };
    // SAFETY: `T` is that vector type at every call site (the parameter of `DrawTextLayout`); eight bytes of two floats, no padding, no invalid values.
    unsafe { std::mem::transmute_copy(&[x, y]) }
}

fn rounded(r: Rect, radius: i32) -> D2D1_ROUNDED_RECT {
    D2D1_ROUNDED_RECT { rect: rect_f(r), radiusX: radius.max(0) as f32, radiusY: radius.max(0) as f32 }
}

/// `AppsUseLightTheme` of `HKCU\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize`: -1 not read yet, 0 dark, 1 light. Read when the first
/// window is drawn and again after the next `WM_SETTINGCHANGE` of `ImmersiveColorSet`; a key that cannot be read (a sandboxed app) is light.
static LIGHT_THEME: AtomicI8 = AtomicI8::new(-1);

fn light_theme() -> bool {
    match LIGHT_THEME.load(Ordering::Relaxed) {
        -1 => {
            let mut data = 1u32;
            let mut size = std::mem::size_of::<u32>() as u32;
            // SAFETY: `data` and `size` are live locals sized for one DWORD; the strings are NUL-terminated literals.
            let rc = unsafe {
                RegGetValueW(
                    HKEY_CURRENT_USER,
                    w!(r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"),
                    w!("AppsUseLightTheme"),
                    RRF_RT_REG_DWORD,
                    None,
                    Some((&mut data as *mut u32).cast::<c_void>()),
                    Some(&mut size),
                )
            };
            let light = rc.is_err() || data != 0;
            LIGHT_THEME.store(light as i8, Ordering::Relaxed);
            light
        }
        v => v == 1,
    }
}

// ---- what the key handler hands over ----

/// One update of the window, built inside the edit session (`capture`) and drawn after it. No `Debug`: it holds candidate text.
pub struct Show {
    /// The composition's text rectangle, in the screen coordinates `ITfContextView::GetTextExt` gave.
    pub rect: RECT,
    /// The window `ITfContextView::GetWnd` named (it is only used when it belongs to this thread).
    pub owner: HWND,
    pub items: Vec<Vec<u16>>,
    pub selected: usize,
    /// When the `Out` arrived (`timing::now_us`): event 66 starts there.
    pub t_out: u64,
}

/// Read inside the edit session of the key: the rectangle of the composition, its view's window and the candidates of `out`. `None` when there
/// is nothing to draw: the context is a password field (input scope `IS_PASSWORD`), the rectangle cannot be read, or the answer has no window.
pub fn capture(ec: u32, ctx: &ITfContext, composition: &windows::Win32::UI::TextServices::ITfComposition, out: &Out, t_out: u64) -> Option<Show> {
    let selected = usize::try_from(out.candidate_selected).ok()?;
    // SAFETY: live interfaces; the out parameters are locals.
    unsafe {
        let range = composition.GetRange().ok()?;
        if is_password(ec, ctx, &range) {
            return None;
        }
        let view = ctx.GetActiveView().ok()?;
        let mut rect = RECT::default();
        let mut clipped = BOOL(0);
        view.GetTextExt(ec, &range, &mut rect, &mut clipped).ok()?;
        let owner = view.GetWnd().unwrap_or_default();
        Some(Show { rect, owner, items: out.candidates.clone(), selected, t_out })
    }
}

/// The input scope `IS_PASSWORD` among the scopes of the composition's range.
///
/// # Safety
/// `ec` is the cookie of the running edit session and `range` belongs to `ctx`.
unsafe fn is_password(ec: u32, ctx: &ITfContext, range: &windows::Win32::UI::TextServices::ITfRange) -> bool {
    // SAFETY: caller contract; `scopes` is CoTaskMem memory of the scope list, freed once after the read.
    unsafe {
        let Ok(prop) = ctx.GetProperty(&GUID_PROP_INPUTSCOPE) else { return false };
        let Ok(value) = prop.GetValue(ec, range) else { return false };
        let Ok(unknown) = windows_core::IUnknown::try_from(&value) else { return false };
        let Ok(scope) = unknown.cast::<ITfInputScope>() else { return false };
        let mut scopes: *mut InputScope = std::ptr::null_mut();
        let mut n = 0u32;
        if scope.GetInputScopes(&mut scopes, &mut n).is_err() || scopes.is_null() {
            return false;
        }
        let found = std::slice::from_raw_parts(scopes, (n as usize).min(64)).iter().any(|s| s.0 == IS_PASSWORD.0);
        CoTaskMemFree(Some(scopes.cast()));
        found
    }
}

// ---- the window of this thread ----

struct State {
    hwnd: HWND,
    owner: HWND,
    shown: bool,
}

thread_local! {
    static STATE: RefCell<Option<State>> = const { RefCell::new(None) };
}

static CLASS: Once = Once::new();
static CLASS_OK: AtomicBool = AtomicBool::new(false);
/// The first window of the process has been made (event 67 is written once).
static FIRST_DONE: AtomicBool = AtomicBool::new(false);
/// The timer of a hide asked for from inside an edit session (`hide_later`).
const HIDE_TIMER: usize = 1;

/// The class has been registered (or the attempt failed): the DLL has a window procedure that windows may still use, so it must not unload.
pub fn attempted() -> bool {
    CLASS.is_completed()
}

/// A window of this thread that still exists.
fn mine(h: HWND) -> bool {
    // SAFETY: plain queries on a handle that may be stale; both calls fail cleanly on one.
    unsafe { !h.0.is_null() && IsWindow(Some(h)).as_bool() && GetWindowThreadProcessId(h, None) == GetCurrentThreadId() }
}

fn state() -> Option<(HWND, HWND, bool)> {
    STATE.with(|s| s.try_borrow().ok().and_then(|g| g.as_ref().map(|st| (st.hwnd, st.owner, st.shown))))
}

fn set_shown(shown: bool) {
    STATE.with(|s| {
        if let Ok(mut g) = s.try_borrow_mut() {
            if let Some(st) = g.as_mut() {
                st.shown = shown;
            }
        }
    });
}

/// Takes the window out of `STATE` and destroys it (state first: `DestroyWindow` sends `WM_NCDESTROY` to the procedure, which touches `STATE`).
fn destroy_window() {
    let taken = STATE.with(|s| s.try_borrow_mut().ok().and_then(|mut g| g.take()));
    if let Some(st) = taken {
        if mine(st.hwnd) {
            // SAFETY: a window of this thread; the procedure runs here.
            unsafe {
                let _ = KillTimer(Some(st.hwnd), HIDE_TIMER);
                let _ = DestroyWindow(st.hwnd);
            }
        }
    }
}

/// The owner of the window: the view's window, else the root of the focus window, each only when it belongs to this thread (an owner of
/// another thread would tie input queues together, and one of another process is out of reach for a Medium window); else none.
fn pick_owner(hint: HWND) -> HWND {
    let root = |h: HWND| -> HWND {
        if h.0.is_null() {
            return HWND::default();
        }
        // SAFETY: plain query.
        let r = unsafe { GetAncestor(h, GA_ROOT) };
        if mine(r) {
            r
        } else {
            HWND::default()
        }
    };
    let a = root(hint);
    if !a.0.is_null() {
        return a;
    }
    // SAFETY: plain query.
    root(unsafe { GetFocus() })
}

/// Per-Monitor V2 awareness for this thread while it lives, the previous context put back on every path (a panic included). The DLL declares
/// no awareness of its own (the host app's choice stands); the window and the monitor queries need the real pixels.
struct DpiScope(DPI_AWARENESS_CONTEXT);

impl DpiScope {
    fn enter() -> Self {
        // SAFETY: no preconditions; returns the previous context (null on failure).
        Self(unsafe { SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) })
    }
}

impl Drop for DpiScope {
    fn drop(&mut self) {
        if !self.0 .0.is_null() {
            // SAFETY: the context this thread had when `enter` ran.
            unsafe { SetThreadDpiAwarenessContext(self.0) };
        }
    }
}

fn register_class() -> bool {
    CLASS.call_once(|| {
        let wc = WNDCLASSEXW {
            cbSize: std::mem::size_of::<WNDCLASSEXW>() as u32,
            lpfnWndProc: Some(wnd_proc),
            hInstance: dll::module().into(),
            lpszClassName: CLASS_NAME,
            ..Default::default()
        };
        // SAFETY: a fully initialised class of this DLL, not global; never unregistered (the DLL stays loaded, `attempted`).
        CLASS_OK.store(unsafe { RegisterClassExW(&wc) } != 0, Ordering::Release);
    });
    CLASS_OK.load(Ordering::Acquire)
}

/// Only what the window uses: it takes no activation, forgets its handle when it is destroyed (with its owner, the system destroys it too), hides on
/// the timer of `hide_later` and re-reads the colour theme when Windows says the colours changed. No message carries a pointer of ours.
unsafe extern "system" fn wnd_proc(hwnd: HWND, msg: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    guard::run(
        LRESULT(0),
        || match msg {
            WM_MOUSEACTIVATE => LRESULT(MA_NOACTIVATE as isize),
            WM_NCDESTROY => {
                STATE.with(|s| {
                    if let Ok(mut g) = s.try_borrow_mut() {
                        if g.as_ref().is_some_and(|st| st.hwnd == hwnd) {
                            *g = None;
                        }
                    }
                });
                // SAFETY: the default procedure of the message we were given.
                unsafe { DefWindowProcW(hwnd, msg, wparam, lparam) }
            }
            WM_SETTINGCHANGE => {
                if is_immersive_color_set(lparam) {
                    LIGHT_THEME.store(-1, Ordering::Relaxed);
                }
                // SAFETY: as above.
                unsafe { DefWindowProcW(hwnd, msg, wparam, lparam) }
            }
            WM_TIMER if wparam.0 == HIDE_TIMER => {
                // SAFETY: a timer of this window.
                unsafe {
                    let _ = KillTimer(Some(hwnd), HIDE_TIMER);
                }
                hide_now();
                LRESULT(0)
            }
            // SAFETY: as above; `WM_GETOBJECT` and everything else take the system's answer.
            _ => unsafe { DefWindowProcW(hwnd, msg, wparam, lparam) },
        },
        || {},
    )
}

/// `lparam` of `WM_SETTINGCHANGE` is a string pointer or null: it is read for at most 32 units, up to the first NUL, and compared with the name.
fn is_immersive_color_set(lparam: LPARAM) -> bool {
    const NAME: [u16; 17] = utf16(*b"ImmersiveColorSet");
    let p = lparam.0 as *const u16;
    if p.is_null() {
        return false;
    }
    for (i, want) in NAME.iter().enumerate() {
        // SAFETY: the system passes a NUL-terminated string; a mismatch or the NUL stops the read before the end of what is valid.
        if unsafe { *p.add(i) } != *want {
            return false;
        }
    }
    // SAFETY: the 17 units above matched, so the next one is at least the NUL.
    unsafe { *p.add(NAME.len()) == 0 }
}

const fn utf16<const N: usize>(s: [u8; N]) -> [u16; N] {
    let mut o = [0u16; N];
    let mut i = 0;
    while i < N {
        o[i] = s[i] as u16;
        i += 1;
    }
    o
}

// ---- the process's factories ----

struct Factories {
    d2d: ID2D1Factory,
    dw: IDWriteFactory,
}

static FACTORIES: Mutex<Option<Factories>> = Mutex::new(None);

/// One pair per process, made by `prewarm` (or by the first window if that has not run yet); a failed attempt is made again by the next caller.
fn factories() -> Option<(ID2D1Factory, IDWriteFactory)> {
    let mut g = FACTORIES.lock().unwrap_or_else(PoisonError::into_inner);
    if g.is_none() {
        let opts = D2D1_FACTORY_OPTIONS { debugLevel: D2D1_DEBUG_LEVEL_NONE };
        // SAFETY: plain factory creation; no debug layer in any build.
        let made = unsafe {
            let d2d = D2D1CreateFactory::<ID2D1Factory>(D2D1_FACTORY_TYPE_MULTI_THREADED, Some(&opts)).ok();
            let dw = DWriteCreateFactory::<IDWriteFactory>(DWRITE_FACTORY_TYPE_SHARED).ok();
            d2d.zip(dw)
        };
        *g = made.map(|(d2d, dw)| Factories { d2d, dw });
    }
    g.as_ref().map(|f| (f.d2d.clone(), f.dw.clone()))
}

// ---- warming up ----

static PREWARM: Once = Once::new();

/// Started by the first key the TIP eats (once per process, on a background thread). That key went through a connection to the host, so
/// `client::attempted()` is already true and the DLL never unloads (`dll.rs`): the thread cannot outlive the module. Do not call this from a
/// path that runs before a connection was attempted (for example `ActivateEx`). It makes the factories and has DirectWrite load the system
/// font collection and the UI font once, so the first window does not pay for it on the key that opens it. Only fixed text is laid out here;
/// nothing of the user's.
pub fn prewarm() {
    PREWARM.call_once(|| {
        let _ = std::thread::Builder::new().name("shanjie-cand-warm".into()).spawn(|| {
            guard::run((), warm, || ());
        });
    });
}

fn warm() {
    const SAMPLE: [u16; 5] = [0x7684, 0x4E00, 0x662F, 0x6E2C, 0x8A66]; // 的一是測試, fixed text
    let Some((_, dw)) = factories() else { return };
    let Some(f) = format(&dw, 16) else { return };
    if let Some(l) = layout(&dw, &SAMPLE, &f, 1000, 100) {
        let mut tm = DWRITE_TEXT_METRICS::default();
        // SAFETY: a live layout and a valid out pointer; the metrics force the font to load.
        let _ = unsafe { l.GetMetrics(&mut tm) };
    }
}

// ---- drawing ----

/// The two text formats of one size pair, made once per UI thread and reused by every update (a format holds no text).
struct Formats {
    text_px: i32,
    number_px: i32,
    text: IDWriteTextFormat,
    number: IDWriteTextFormat,
}

thread_local! {
    static FORMATS: RefCell<Option<Formats>> = const { RefCell::new(None) };
}

fn formats(dw: &IDWriteFactory, m: &Metrics) -> Option<(IDWriteTextFormat, IDWriteTextFormat)> {
    FORMATS.with(|c| {
        let mut c = c.borrow_mut();
        let fresh = c.as_ref().is_none_or(|f| f.text_px != m.font_px || f.number_px != m.number_font_px);
        if fresh {
            *c = Some(Formats { text_px: m.font_px, number_px: m.number_font_px, text: format(dw, m.font_px)?, number: format(dw, m.number_font_px)? });
        }
        c.as_ref().map(|f| (f.text.clone(), f.number.clone()))
    })
}

/// A 32 bpp top-down DIB selected into a memory DC; released (the bitmap is deleted) when it goes out of scope, so no candidate pixel outlives one update.
struct Frame {
    dc: HDC,
    bitmap: HBITMAP,
    old: HGDIOBJ,
    w: i32,
    h: i32,
}

impl Frame {
    fn new(w: i32, h: i32) -> Option<Self> {
        if w <= 0 || h <= 0 {
            return None;
        }
        let info = BITMAPINFO {
            bmiHeader: BITMAPINFOHEADER {
                biSize: std::mem::size_of::<BITMAPINFOHEADER>() as u32,
                biWidth: w,
                biHeight: -h,
                biPlanes: 1,
                biBitCount: 32,
                biCompression: BI_RGB.0,
                ..Default::default()
            },
            ..Default::default()
        };
        // SAFETY: a memory DC and a DIB section of the sizes above (the memory starts zeroed: transparent); every failure path frees what was made.
        unsafe {
            let dc = CreateCompatibleDC(None);
            if dc.is_invalid() {
                return None;
            }
            let mut bits: *mut c_void = std::ptr::null_mut();
            let Ok(bitmap) = CreateDIBSection(Some(dc), &info, DIB_RGB_COLORS, &mut bits, None, 0) else {
                let _ = DeleteDC(dc);
                return None;
            };
            let old = SelectObject(dc, bitmap.into());
            Some(Self { dc, bitmap, old, w, h })
        }
    }
}

impl Drop for Frame {
    fn drop(&mut self) {
        // SAFETY: the DC and bitmap made in `new`, released once, the old bitmap selected back first.
        unsafe {
            SelectObject(self.dc, self.old);
            let _ = DeleteObject(self.bitmap.into());
            let _ = DeleteDC(self.dc);
        }
    }
}

fn format(dw: &IDWriteFactory, px: i32) -> Option<IDWriteTextFormat> {
    // SAFETY: plain DirectWrite calls with live arguments; the size is in device-independent pixels, which the render target makes equal to pixels.
    unsafe {
        let f = dw.CreateTextFormat(FONT, None, DWRITE_FONT_WEIGHT_NORMAL, DWRITE_FONT_STYLE_NORMAL, DWRITE_FONT_STRETCH_NORMAL, px.max(1) as f32, LOCALE).ok()?;
        f.SetWordWrapping(DWRITE_WORD_WRAPPING_NO_WRAP).ok()?;
        f.SetTextAlignment(DWRITE_TEXT_ALIGNMENT_LEADING).ok()?;
        f.SetParagraphAlignment(DWRITE_PARAGRAPH_ALIGNMENT_CENTER).ok()?;
        let sign = dw.CreateEllipsisTrimmingSign(&f).ok()?;
        f.SetTrimming(&TRIM, &sign).ok()?;
        Some(f)
    }
}

fn layout(dw: &IDWriteFactory, text: &[u16], f: &IDWriteTextFormat, max_w: i32, max_h: i32) -> Option<IDWriteTextLayout> {
    // SAFETY: plain call with live arguments.
    unsafe { dw.CreateTextLayout(text, f, max_w.max(0) as f32, max_h.max(0) as f32).ok() }
}

/// Lays the texts out (the widest sets the window's width), then draws the window: border, background, the selected row's block and bar, the
/// numbers 1 to 9 and the texts. `work_w` is the width of the monitor's work area.
fn draw(d2d: &ID2D1Factory, dw: &IDWriteFactory, m: &Metrics, items: &[Vec<u16>], selected: usize, light: bool, work_w: i32) -> Option<Frame> {
    let theme = if light { &LIGHT } else { &DARK };
    let (text_format, number_format) = formats(dw, m)?;
    // The most a text may take is what a window of half the work area leaves it; the layouts trim there.
    let cap = m.text_width(work_w / 2);
    let layouts: Vec<IDWriteTextLayout> = items.iter().map(|t| layout(dw, t, &text_format, cap, m.row)).collect::<Option<_>>()?;
    let mut widest = 0i32;
    for l in &layouts {
        let mut tm = DWRITE_TEXT_METRICS::default();
        // SAFETY: a live layout and a valid out pointer.
        unsafe { l.GetMetrics(&mut tm) }.ok()?;
        widest = widest.max(tm.widthIncludingTrailingWhitespace.ceil() as i32);
    }
    let width = m.width(widest, work_w);
    let height = m.height(items.len());
    let frame = Frame::new(width, height)?;
    let props = D2D1_RENDER_TARGET_PROPERTIES {
        r#type: D2D1_RENDER_TARGET_TYPE_SOFTWARE,
        pixelFormat: D2D1_PIXEL_FORMAT { format: DXGI_FORMAT_B8G8R8A8_UNORM, alphaMode: D2D1_ALPHA_MODE_PREMULTIPLIED },
        dpiX: 96.0,
        dpiY: 96.0,
        usage: D2D1_RENDER_TARGET_USAGE_NONE,
        minLevel: D2D1_FEATURE_LEVEL_DEFAULT,
    };
    // SAFETY: a software DC render target bound to the frame's DC for the extent of this function; the brush and the layouts are used only between
    // BeginDraw and EndDraw, and everything is dropped before the frame.
    unsafe {
        let rt = d2d.CreateDCRenderTarget(&props).ok()?;
        rt.BindDC(frame.dc, &RECT { left: 0, top: 0, right: frame.w, bottom: frame.h }).ok()?;
        // Per-pixel alpha cannot carry ClearType: grey-scale text.
        rt.SetTextAntialiasMode(D2D1_TEXT_ANTIALIAS_MODE_GRAYSCALE);
        let brush: ID2D1SolidColorBrush = rt.CreateSolidColorBrush(&color(theme.border), None).ok()?;
        let opts = D2D1_DRAW_TEXT_OPTIONS(D2D1_DRAW_TEXT_OPTIONS_CLIP.0 | D2D1_DRAW_TEXT_OPTIONS_ENABLE_COLOR_FONT.0);
        rt.BeginDraw();
        rt.Clear(Some(&D2D1_COLOR_F::default()));
        let outer = Rect { left: 0, top: 0, right: width, bottom: height };
        rt.FillRoundedRectangle(&rounded(outer, m.window_radius), &brush);
        brush.SetColor(&color(theme.window));
        let inner = Rect { left: m.border, top: m.border, right: width - m.border, bottom: height - m.border };
        rt.FillRoundedRectangle(&rounded(inner, m.window_radius - m.border), &brush);
        for (i, l) in layouts.iter().enumerate() {
            let block = m.block(i, width);
            let on = i == selected;
            if on {
                brush.SetColor(&color(theme.selected));
                rt.FillRoundedRectangle(&rounded(block, m.block_radius), &brush);
                let mid = (block.top + block.bottom) / 2;
                let bar = Rect { left: block.left, top: mid - m.bar_h / 2, right: block.left + m.bar_w, bottom: mid - m.bar_h / 2 + m.bar_h };
                brush.SetColor(&color(theme.bar));
                rt.FillRoundedRectangle(&rounded(bar, m.bar_w / 2), &brush);
            }
            let top = m.row_top(i) as f32;
            let digit = [u16::from(b'1') + i as u16];
            if let Some(n) = layout(dw, &digit, &number_format, m.text_x - m.number_x, m.row) {
                brush.SetColor(&color(if on { theme.number_selected } else { theme.number }));
                rt.DrawTextLayout(origin((block.left + m.number_x) as f32, top), &n, &brush, opts);
            }
            brush.SetColor(&color(theme.text));
            rt.DrawTextLayout(origin((block.left + m.text_x) as f32, top), l, &brush, opts);
        }
        rt.EndDraw(None, None).ok()?;
    }
    Some(frame)
}

/// Hands `frame` to the layered window at (`x`, `y`).
fn present(hwnd: HWND, frame: &Frame, x: i32, y: i32) -> bool {
    let dst = POINT { x, y };
    let size = SIZE { cx: frame.w, cy: frame.h };
    let src = POINT { x: 0, y: 0 };
    let blend = BLENDFUNCTION { BlendOp: AC_SRC_OVER as u8, BlendFlags: 0, SourceConstantAlpha: 255, AlphaFormat: AC_SRC_ALPHA as u8 };
    // SAFETY: a window of this thread and a live memory DC; every pointer is to a local that outlives the call.
    unsafe { UpdateLayeredWindow(hwnd, None, Some(&dst), Some(&size), Some(frame.dc), Some(&src), COLORREF(0), Some(&blend), ULW_ALPHA) }.is_ok()
}

// ---- show and hide ----

fn announce(hwnd: HWND, event: u32) {
    // SAFETY: a plain notification for a window of this thread (the light dismiss of the Microsoft IME guidelines); no text goes with it.
    unsafe { NotifyWinEvent(event, hwnd, OBJID_CLIENT.0, 0) };
}

/// Makes or finds this thread's window for `owner`; one with another owner is destroyed first (an owner cannot be changed later). New windows
/// start at (`x`, `y`), 1 x 1, hidden, so their DPI is the DPI of the monitor they will open on.
fn ensure_window(owner: HWND, x: i32, y: i32) -> Option<HWND> {
    if let Some((h, o, _)) = state() {
        if mine(h) && o == owner {
            return Some(h);
        }
        destroy_window();
    }
    if !register_class() {
        return None;
    }
    let ex = WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_LAYERED;
    // SAFETY: a window of this DLL's class, no title (R2), no creation parameter; the owner is a window of this thread or none.
    let hwnd = unsafe { CreateWindowExW(ex, CLASS_NAME, PCWSTR::null(), WS_POPUP, x, y, 1, 1, (!owner.0.is_null()).then_some(owner), None, Some(dll::module().into()), None) }.ok()?;
    STATE.with(|s| {
        if let Ok(mut g) = s.try_borrow_mut() {
            *g = Some(State { hwnd, owner, shown: false });
        }
    });
    Some(hwnd)
}

/// Draws `v` and shows the window (or moves and redraws it). Any failure leaves no window; the keys still work.
pub fn show(v: Show) {
    // A window that cannot be redrawn is hidden, so it never keeps the rows of an earlier page while the core is on a new one.
    if !guard::run(false, || show_inner(v), hide_now) {
        hide();
    }
}

fn show_inner(v: Show) -> bool {
    // A hide asked for inside an edit session must not take the new content down.
    if let Some((h, _, _)) = state() {
        // SAFETY: a timer of this thread's window, if any.
        unsafe {
            let _ = KillTimer(Some(h), HIDE_TIMER);
        }
    }
    let t_first = timing::now_us();
    let _dpi = DpiScope::enter();
    let Some((d2d, dw)) = factories() else { return false };
    let owner = pick_owner(v.owner);
    let fresh = state().is_none();
    let Some(hwnd) = ensure_window(owner, v.rect.left, v.rect.bottom) else { return false };
    if fresh && !FIRST_DONE.swap(true, Ordering::Relaxed) {
        timing::record(timing::EV_CAND_FIRST, t_first, timing::now_us());
    }
    // SAFETY: plain queries and a move of this thread's window; the window is at the composition, so its DPI is the one of that monitor.
    let (work, dpi) = unsafe {
        let _ = SetWindowPos(hwnd, None, v.rect.left, v.rect.bottom, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOZORDER | SWP_NOREDRAW);
        let mon = MonitorFromRect(&v.rect, MONITOR_DEFAULTTONEAREST);
        let mut mi = MONITORINFO { cbSize: std::mem::size_of::<MONITORINFO>() as u32, ..Default::default() };
        if !GetMonitorInfoW(mon, &mut mi).as_bool() {
            return false;
        }
        (mi.rcWork, GetDpiForWindow(hwnd))
    };
    let work = Rect { left: work.left, top: work.top, right: work.right, bottom: work.bottom };
    let m = Metrics::new(dpi);
    let Some(frame) = draw(&d2d, &dw, &m, &v.items, v.selected, light_theme(), work.right.saturating_sub(work.left)) else { return false };
    let comp = Rect { left: v.rect.left, top: v.rect.top, right: v.rect.right, bottom: v.rect.bottom };
    let (x, y) = candlayout::place(comp, frame.w, frame.h, work);
    // The new content goes in before the window is shown, so the last field's candidates never flash.
    if !present(hwnd, &frame, x, y) {
        return false;
    }
    let was_shown = state().is_some_and(|(_, _, s)| s);
    if !was_shown {
        // SAFETY: this thread's window; shown without activation. SetWindowPos with SWP_SHOWWINDOW, not ShowWindow: the first ShowWindow of a
        // process can be replaced by the show state its parent put in STARTUPINFO (SW_HIDE for a process started without a window), and the
        // candidate window may be the first window the host app's process shows that way.
        unsafe {
            let _ = SetWindowPos(hwnd, Some(HWND_TOPMOST), 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW);
        }
        set_shown(true);
    }
    announce(hwnd, if was_shown { EVENT_OBJECT_IME_CHANGE } else { EVENT_OBJECT_IME_SHOW });
    timing::record(timing::EV_CAND_UPDATE, v.t_out, timing::now_us());
    // Event 68: the composition rectangle and the window's corner, six lines of one number each (i32 as u32, so a monitor left of or above the primary one fits).
    for n in [v.rect.left, v.rect.top, v.rect.right, v.rect.bottom, x, y] {
        timing::diag_value(timing::EV_CAND_POS, u64::from(n as u32));
    }
    true
}

/// Hides the window and empties its surface.
pub fn hide() {
    guard::run((), hide_now, || {})
}

fn hide_now() {
    let Some((h, _, shown)) = state() else { return };
    if !shown || !mine(h) {
        return;
    }
    set_shown(false);
    // One transparent pixel in place of the last picture, then the window goes (no candidate stays in the window's surface).
    if let Some(blank) = Frame::new(1, 1) {
        let _ = present_blank(h, &blank);
    }
    // SAFETY: this thread's window.
    unsafe {
        let _ = ShowWindow(h, SW_HIDE);
    }
    announce(h, EVENT_OBJECT_IME_HIDE);
}

fn present_blank(h: HWND, blank: &Frame) -> bool {
    let size = SIZE { cx: blank.w, cy: blank.h };
    let src = POINT { x: 0, y: 0 };
    let b = BLENDFUNCTION { BlendOp: AC_SRC_OVER as u8, BlendFlags: 0, SourceConstantAlpha: 255, AlphaFormat: AC_SRC_ALPHA as u8 };
    // SAFETY: as `present`, keeping the window where it is.
    unsafe { UpdateLayeredWindow(h, None, None, Some(&size), Some(blank.dc), Some(&src), COLORREF(0), Some(&b), ULW_ALPHA) }.is_ok()
}

/// A hide for a place that runs inside an edit session (`OnCompositionTerminated`): the window is not touched there; a timer of the window itself
/// hides it a few milliseconds later, from the message loop.
pub fn hide_later() {
    if let Some((h, _, true)) = state() {
        if mine(h) {
            // SAFETY: a timer of this thread's window; the window procedure takes it.
            unsafe {
                SetTimer(Some(h), HIDE_TIMER, USER_TIMER_MINIMUM, None);
            }
        }
    }
}

/// The text service is deactivated: hide and destroy this thread's window.
pub fn destroy() {
    guard::run((), || {
        hide_now();
        destroy_window();
    }, || {})
}

/// For the R2 test: this thread's window, if it exists.
#[cfg(test)]
pub fn window_for_test() -> Option<(HWND, bool)> {
    state().map(|(h, _, s)| (h, s))
}
