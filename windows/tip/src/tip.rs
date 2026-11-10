//! The text service object (docs/spec/windows-tip.md §3.5): activation, the key sink, the focus sinks and the composition sink.
//! One instance per UI thread. The core, in the resident host, decides what a key does (`client` carries it there and back,
//! docs/spec/windows-tip.md §5); this file maps keys in, applies the answer to the document (`doc`), and ends compositions in time.
use crate::cand;
use crate::client::{self, Reply};
use crate::display::{DisplayEnum, DisplayInfo};
use crate::doc::{self, Comp, CompSlot};
use crate::guard;
use crate::guids::DISPLAY_ATTR_COMPOSING;
use crate::keys::{map_key, may_eat, may_eat_either, Mods};
use crate::timing;
use std::cell::{Cell, RefCell};
use std::rc::Rc;
use std::sync::atomic::Ordering;
use windows::Win32::Foundation::{E_INVALIDARG, FALSE, LPARAM, WPARAM};
use windows::core::BOOL;
use windows::Win32::System::Com::{CoCreateInstance, CLSCTX_INPROC_SERVER};
use windows::Win32::System::Threading::GetCurrentThreadId;
use windows::Win32::System::Variant::VARIANT;
use windows::Win32::UI::Input::KeyboardAndMouse::GetKeyState;
use windows::Win32::UI::TextServices::{
    CLSID_TF_CategoryMgr, IEnumTfDisplayAttributeInfo, ITfCategoryMgr, ITfComposition, ITfCompositionSink, ITfCompositionSink_Impl, ITfCompartmentMgr, ITfContext, ITfDisplayAttributeInfo,
    ITfDisplayAttributeProvider, ITfDisplayAttributeProvider_Impl, ITfDocumentMgr, ITfKeyEventSink, ITfKeyEventSink_Impl, ITfKeystrokeMgr, ITfSource, ITfTextInputProcessorEx,
    ITfTextInputProcessorEx_Impl, ITfTextInputProcessor_Impl, ITfThreadFocusSink, ITfThreadFocusSink_Impl, ITfThreadMgr, ITfThreadMgrEventSink, ITfThreadMgrEventSink_Impl,
    GUID_COMPARTMENT_EMPTYCONTEXT, GUID_COMPARTMENT_KEYBOARD_DISABLED, GUID_COMPARTMENT_KEYBOARD_OPENCLOSE, GUID_PROP_ATTRIBUTE, TF_TMAE_SECUREMODE,
};
use windows_core::{implement, Error, IUnknown, IUnknownImpl, Interface, Ref, Result, GUID};

#[implement(ITfTextInputProcessorEx, ITfThreadMgrEventSink, ITfThreadFocusSink, ITfKeyEventSink, ITfCompositionSink, ITfDisplayAttributeProvider)]
pub struct TextService {
    tid: Cell<u32>,
    thread_mgr: RefCell<Option<ITfThreadMgr>>,
    /// False before `ActivateEx`, after `Deactivate`, and in secure mode: the TIP does nothing.
    active: Cell<bool>,
    key_sink_on: Cell<bool>,
    mgr_cookie: Cell<Option<u32>>,
    focus_cookie: Cell<Option<u32>>,
    /// `TfGuidAtom` of the composition's display attribute.
    atom: Cell<u32>,
    /// The composition this instance opened (each instance ends its own, docs/spec/windows-tip.md §3.8).
    comp: CompSlot,
    /// The last `Out` of this instance had a candidate window open (`candidate_selected >= 0`): Page Up and Page Down are keys of the window (docs/spec/windows-tip.md §3.6).
    /// Cleared with the composition on every path that ends it (`cand_closed`), so a closed window never claims those keys.
    cand_open: Cell<bool>,
    /// Document managers this thread has already focused: the keyboard is opened only the first time each one gets focus, so a state
    /// the user changed later (for example Ctrl+Space in that window) is kept. Holding the references keeps their addresses unique;
    /// a manager is removed when TSF uninitialises it (`OnUninitDocumentMgr`), and the list is cleared on Deactivate.
    seen_dims: RefCell<Vec<ITfDocumentMgr>>,
}

impl TextService {
    pub fn new() -> Self {
        crate::dll::OBJECTS.fetch_add(1, Ordering::AcqRel);
        Self {
            tid: Cell::new(0),
            thread_mgr: RefCell::new(None),
            active: Cell::new(false),
            key_sink_on: Cell::new(false),
            mgr_cookie: Cell::new(None),
            focus_cookie: Cell::new(None),
            atom: Cell::new(0),
            comp: RefCell::new(None),
            cand_open: Cell::new(false),
            seen_dims: RefCell::new(Vec::new()),
        }
    }
}

/// `Drop` runs inside `Release`: it only frees (the COM fields drop by themselves).
impl Drop for TextService {
    fn drop(&mut self) {
        crate::dll::OBJECTS.fetch_sub(1, Ordering::AcqRel);
    }
}

/// The context as this connection names it to the host: its pointer value. The host pairs it with the connection (docs/spec/windows-tip.md
/// §5.3), so it needs no thread id and means nothing on any other connection.
fn ctx_id(ctx: &ITfContext) -> u64 {
    ctx.as_raw() as usize as u64
}

/// Ends a composition the host no longer backs: in this edit session when it lives in `ctx`, else in its own context later.
fn end_stale(ec: u32, ctx: &ITfContext, tid: u32, c: Comp) {
    if c.ctx.as_raw() == ctx.as_raw() {
        let _ = doc::end_composition(ec, &c);
    } else {
        doc::end_composition_later(tid, c);
    }
}

/// `GetKeyState` high bit: down.
fn down(vk: i32) -> bool {
    // SAFETY: plain query.
    unsafe { GetKeyState(vk) < 0 }
}

fn read_mods() -> Mods {
    // SAFETY: plain query; the low bit of VK_CAPITAL is the toggle.
    let caps = unsafe { GetKeyState(0x14) } & 1 != 0;
    Mods { shift: down(0x10), ctrl: down(0x11), alt: down(0x12), win: down(0x5B) || down(0x5C), caps }
}

/// A compartment holding a non-zero number.
fn flag(mgr: &ITfCompartmentMgr, guid: &GUID) -> Option<bool> {
    // SAFETY: live interfaces.
    let v: VARIANT = unsafe { mgr.GetCompartment(guid).ok()?.GetValue().ok()? };
    i32::try_from(&v).ok().map(|n| n != 0)
}

#[derive(Default)]
struct Outcome {
    /// The host answered with an `Out`.
    ran: bool,
    eaten: bool,
    /// The answer had a candidate window open and the document was edited to match.
    window: bool,
    /// What to draw for it, read inside the edit session (`cand::capture`); `None` for a password field or when the rectangle was unavailable.
    show: Option<cand::Show>,
}

impl TextService_Impl {
    /// A COM method body: panics become a bare failure code, the host is told to reset and the composition object dropped (docs/spec/windows-tip.md §3.9).
    fn guarded<T>(&self, f: impl FnOnce() -> Result<T>) -> Result<T> {
        guard::com_with(f, || {
            drop(self.comp.try_borrow_mut().map(|mut c| c.take()));
            self.cand_closed();
        })
    }

    /// A key method: on a panic or any error the answer is "not eaten", never an error code (the out parameter would stay unset).
    fn guarded_key(&self, f: impl FnOnce() -> Result<bool>) -> Result<BOOL> {
        let eaten = guard::run(false, || f().unwrap_or(false), || {
            client::reset_after_panic();
            drop(self.comp.try_borrow_mut().map(|mut c| c.take()));
            self.cand_closed();
        });
        Ok(eaten.into())
    }

    /// The core's candidate window is gone, or the composition it belonged to ended: Page Up and Page Down stop being claimed and the window hides.
    fn cand_closed(&self) {
        self.cand_open.set(false);
        cand::hide();
    }

    fn activate(&self, ptim: Ref<'_, ITfThreadMgr>, tid: u32, flags: u32) -> Result<()> {
        self.guarded(|| {
            let t0 = timing::now_us();
            let mgr = ptim.ok()?.clone();
            self.tid.set(tid);
            *self.thread_mgr.borrow_mut() = Some(mgr.clone());
            // The timing file first (it opens once per process; its own Medium-integrity gate decides), so 49 and 48 are recorded
            // on the first activation too.
            timing::init("timing");
            // SAFETY: no preconditions.
            let os_tid = unsafe { GetCurrentThreadId() } as u64;
            timing::record(timing::EV_ACTIVATE_THREAD, os_tid, os_tid);
            if flags & TF_TMAE_SECUREMODE != 0 {
                self.active.set(false);
                timing::diag(timing::EV_ACTIVATE_SECURE);
                return Ok(());
            }
            // No connection here: the first key opens it (docs/spec/windows-tip.md §5.4).
            if let Err(e) = self.advise(&mgr, tid) {
                self.unadvise();
                timing::diag(timing::EV_ACTIVATE_ADVISE_FAILED);
                return Err(e);
            }
            self.active.set(true);
            // advise() opened the keyboard for the document that has focus now: count it as seen, so a later visit keeps its state.
            // SAFETY: live interface.
            if let Ok(d) = unsafe { mgr.GetFocus() } {
                self.seen_dims.borrow_mut().push(d);
            }
            timing::record(timing::EV_ACTIVATE, t0, timing::now_us());
            Ok(())
        })
    }

    fn advise(&self, mgr: &ITfThreadMgr, tid: u32) -> Result<()> {
        // SAFETY: live interfaces; the sinks are this object's own interfaces.
        unsafe {
            let ks: ITfKeystrokeMgr = mgr.cast()?;
            ks.AdviseKeyEventSink(tid, &self.to_interface::<ITfKeyEventSink>(), true)?;
            self.key_sink_on.set(true);
            let src: ITfSource = mgr.cast()?;
            let a: IUnknown = self.to_interface::<ITfThreadMgrEventSink>().cast()?;
            self.mgr_cookie.set(Some(src.AdviseSink(&ITfThreadMgrEventSink::IID, &a)?));
            let b: IUnknown = self.to_interface::<ITfThreadFocusSink>().cast()?;
            self.focus_cookie.set(Some(src.AdviseSink(&ITfThreadFocusSink::IID, &b)?));
            let cats: ITfCategoryMgr = CoCreateInstance(&CLSID_TF_CategoryMgr, None, CLSCTX_INPROC_SERVER)?;
            self.atom.set(cats.RegisterGUID(&DISPLAY_ATTR_COMPOSING)?);
            // The keyboard is "open"; while this compartment reads closed, the key sinks pass keys through.
            Self::set_open(mgr, tid);
        }
        Ok(())
    }

    /// Sets the thread's keyboard compartment to open (docs/spec/windows-tip.md §3.5: this slice is always in Chinese mode). Windows keeps the open state per
    /// window, so a newly focused window can read closed: called on ActivateEx and on every focus change to this thread or document.
    /// Returns whether it read closed before.
    fn set_open(mgr: &ITfThreadMgr, tid: u32) -> bool {
        let Ok(cm) = mgr.cast::<ITfCompartmentMgr>() else { return false };
        let was_closed = flag(&cm, &GUID_COMPARTMENT_KEYBOARD_OPENCLOSE) == Some(false);
        // SAFETY: live interfaces; a failure leaves the compartment as it was.
        unsafe {
            if let Ok(c) = cm.GetCompartment(&GUID_COMPARTMENT_KEYBOARD_OPENCLOSE) {
                let _ = c.SetValue(tid, &VARIANT::from(1i32));
            }
        }
        was_closed
    }

    /// On a focus change to this thread or to a document: the first time a document manager gets focus, open the keyboard (see
    /// `set_open`); later visits keep whatever state it has (the open state follows the window, as Microsoft Zhuyin's does, W0).
    /// `dim` None means "the thread's current focus".
    fn open_if_new(&self, dim: Option<&ITfDocumentMgr>) {
        if !self.active.get() {
            return;
        }
        let Some(m) = self.thread_mgr.borrow().clone() else { return };
        // SAFETY: live interface.
        let focus = match dim {
            Some(d) => Some(d.clone()),
            None => unsafe { m.GetFocus() }.ok(),
        };
        let Some(d) = focus else { return };
        let new = !self.seen_dims.borrow().iter().any(|x| x.as_raw() == d.as_raw());
        if new {
            self.seen_dims.borrow_mut().push(d);
            if Self::set_open(&m, self.tid.get()) {
                timing::diag(timing::EV_REOPENED);
            }
        }
    }

    fn unadvise(&self) {
        let mgr = self.thread_mgr.borrow().clone();
        let Some(mgr) = mgr else { return };
        // SAFETY: live interfaces; failures at teardown are ignored.
        unsafe {
            if let Ok(src) = mgr.cast::<ITfSource>() {
                if let Some(c) = self.focus_cookie.take() {
                    let _ = src.UnadviseSink(c);
                }
                if let Some(c) = self.mgr_cookie.take() {
                    let _ = src.UnadviseSink(c);
                }
            }
            if self.key_sink_on.replace(false) {
                if let Ok(ks) = mgr.cast::<ITfKeystrokeMgr>() {
                    let _ = ks.UnadviseKeyEventSink(self.tid.get());
                }
            }
        }
    }

    /// Ends this instance's own composition in its own context (underline off, text stays) and tells the host to let go of it.
    fn end_own_composition(&self) {
        let open = self.comp.borrow_mut().take();
        if let Some(c) = open {
            client::release(ctx_id(&c.ctx));
            doc::end_composition_later(self.tid.get(), c);
        }
        self.cand_closed();
    }

    fn deactivate(&self) -> Result<()> {
        self.guarded(|| {
            self.end_own_composition();
            cand::destroy();
            self.unadvise();
            client::close();
            self.seen_dims.borrow_mut().clear();
            *self.thread_mgr.borrow_mut() = None;
            self.active.set(false);
            Ok(())
        })
    }

    /// Whether this thread's keyboard is open (default: yes when the compartment is empty).
    fn keyboard_open(&self) -> bool {
        self.thread_mgr.borrow().as_ref().and_then(|m| m.cast::<ITfCompartmentMgr>().ok()).and_then(|cm| flag(&cm, &GUID_COMPARTMENT_KEYBOARD_OPENCLOSE)).unwrap_or(true)
    }

    /// Whether the context takes input (not disabled, not an empty context).
    fn context_usable(ctx: &ITfContext) -> bool {
        match ctx.cast::<ITfCompartmentMgr>() {
            Ok(cm) => flag(&cm, &GUID_COMPARTMENT_KEYBOARD_DISABLED) != Some(true) && flag(&cm, &GUID_COMPARTMENT_EMPTYCONTEXT) != Some(true),
            Err(_) => true,
        }
    }

    fn test_key_down(&self, pic: Ref<'_, ITfContext>, wparam: WPARAM) -> Result<bool> {
        if !self.active.get() {
            timing::diag(timing::EV_TEST_INACTIVE);
            return Ok(false);
        }
        // §5.4: the three blocks pass every key. With an empty composition the connection is checked further down (and the host
        // started from here when it is missing: explorer asks this first).
        if client::blocked() {
            timing::diag(timing::EV_TEST_UNUSABLE);
            return Ok(false);
        }
        let ctx = pic.ok()?;
        if !self.keyboard_open() {
            timing::diag(timing::EV_TEST_CLOSED);
            return Ok(false);
        }
        if !Self::context_usable(ctx) {
            timing::diag(timing::EV_TEST_CONTEXT);
            return Ok(false);
        }
        let Some(k) = map_key(wparam.0 as u32, read_mods(), self.cand_open.get()) else { return Ok(false) };
        let window = self.cand_open.get();
        let composing = self.comp.borrow().is_some() || window;
        // §3.6: with a composition or a window every mapped key may be eaten. With none, the core's key scheme decides, and the scheme is only what this
        // connection's `Welcome` said (this DLL never reads the registry). A thread that has no connection yet refuses, without connecting, the keys
        // both schemes pass on; for any other it connects first and decides with the scheme it then learns.
        let mut eat = if composing {
            may_eat(0, true, window, &k)
        } else {
            match client::scheme() {
                Some(s) => may_eat(s, false, false, &k),
                None => may_eat_either(&k),
            }
        };
        // §5.4, §5.6: a key that would only pass through must be refused here, not let go in OnKeyDown (an IMM32 host loses it).
        if eat && !composing {
            if !client::ready() {
                timing::diag(timing::EV_TEST_UNUSABLE);
                eat = false;
            } else {
                // A connection made or replaced just now may belong to a host with another scheme: decide again with the live one.
                eat = may_eat(client::scheme().unwrap_or(0), false, false, &k);
            }
        }
        if eat {
            // SAFETY: no preconditions.
            timing::diag_value(timing::EV_TEST_TRUE_THREAD, unsafe { GetCurrentThreadId() } as u64);
        }
        Ok(eat)
    }

    fn key_down(&self, pic: Ref<'_, ITfContext>, wparam: WPARAM) -> Result<bool> {
        let t0 = timing::now_us();
        // SAFETY: no preconditions.
        timing::diag_value(timing::EV_KEYDOWN_THREAD, unsafe { GetCurrentThreadId() } as u64);
        if !self.active.get() {
            timing::diag(timing::EV_KD_INACTIVE);
            return Ok(false);
        }
        if client::blocked() {
            timing::diag(timing::EV_KD_UNUSABLE);
            return Ok(false);
        }
        let ctx = pic.ok()?.clone();
        if !self.keyboard_open() {
            timing::diag(timing::EV_KD_CLOSED);
            return Ok(false);
        }
        if !Self::context_usable(&ctx) {
            timing::diag(timing::EV_KD_CONTEXT);
            return Ok(false);
        }
        let Some(k) = map_key(wparam.0 as u32, read_mods(), self.cand_open.get()) else {
            timing::diag(timing::EV_KD_UNMAPPED);
            return Ok(false);
        };
        let me = ctx_id(&ctx);
        let tid = self.tid.get();
        let atom = self.atom.get();
        let sink: ITfCompositionSink = self.to_interface();
        let svc = self.to_object();
        let outcome = Rc::new(Cell::new(Outcome::default()));
        let (o2, ctx2) = (outcome.clone(), ctx.clone());
        // Step 2: everything happens inside the edit session. If the app refuses the synchronous request nothing is sent to the host.
        let granted = doc::request(&ctx, tid, doc::SYNC_WRITE, move |ec| {
            let svc = svc.get();
            let composing = svc.comp.borrow().is_some();
            let reply = client::key(me, k, composing);
            // Event 66 starts when the answer is in hand.
            let t_out = timing::now_us();
            let out = match reply {
                Reply::Out(o) => o,
                Reply::Pass => {
                    // docs/spec/windows-tip.md §5.6, first row: with a composition open, the host's state for it is gone or out of reach.
                    // The composition ends here (underline off, the text stays) and the key is eaten. Without one the key goes to the app.
                    let open = svc.comp.borrow_mut().take();
                    if let Some(c) = open {
                        end_stale(ec, &ctx2, tid, c);
                        o2.set(Outcome { eaten: true, ..Default::default() });
                    }
                    return Ok(());
                }
            };
            o2.set(Outcome { ran: true, ..Default::default() });
            // A composition this instance left open while another owner took the engine is stale: end it before anything else.
            if out.took_over {
                let stale = svc.comp.borrow_mut().take();
                if let Some(s) = stale {
                    end_stale(ec, &ctx2, tid, s);
                }
            }
            // The core returned non-zero: not handled, the document is not touched (shanjie.h).
            if out.core_error {
                return Ok(());
            }
            match doc::apply(ec, &ctx2, &svc.comp, &sink, atom, &out) {
                Ok(()) => {
                    // The text rectangle can only be read inside this session; the window itself is touched after it (docs/spec/windows-tip.md §3.15).
                    let window = out.candidate_selected >= 0;
                    let comp = if window { svc.comp.borrow().clone() } else { None };
                    let show = comp.and_then(|c| cand::capture(ec, &ctx2, &c.composition, &out, t_out));
                    o2.set(Outcome { ran: true, eaten: out.handled, window, show });
                }
                Err(_) => {
                    // The document and the engine disagree now: drop both sides.
                    let open = svc.comp.borrow_mut().take();
                    if let Some(c) = open {
                        let _ = doc::end_composition(ec, &c);
                    }
                    client::release(me);
                }
            }
            Ok(())
        });
        if let Err(e) = &granted {
            timing::diag_value(timing::EV_KEY_SESSION_REFUSED, e.code().0 as u32 as u64);
        }
        let o = outcome.take();
        // The candidate window follows the answer, outside the edit session: shown or redrawn when the core's window is open, hidden on every other
        // path that ran a key or ended a composition. A refused or failed session leaves no window.
        if o.ran || o.eaten {
            if o.eaten {
                cand::prewarm();
            }
            if granted.is_ok() && o.window {
                self.cand_open.set(true);
                match o.show {
                    Some(s) => cand::show(s),
                    None => cand::hide(),
                }
            } else {
                self.cand_closed();
            }
        }
        if o.ran {
            // Event 3 ends after the window is updated: the DLL's whole share of one key.
            timing::record(timing::EV_KEY, t0, timing::now_us());
            if granted.is_err() {
                // The job did not finish (a panic in it): same cleanup.
                self.comp.borrow_mut().take();
                client::release(me);
            }
        }
        Ok(o.eaten && granted.is_ok())
    }

    fn composition_terminated(&self, ecwrite: u32, pcomposition: Ref<'_, ITfComposition>) -> Result<()> {
        self.guarded(|| {
            // Only the composition this instance holds (the same interface pointer) is cleared; any other one is not ours to touch.
            let Ok(done) = pcomposition.ok() else { return Ok(()) };
            let open = {
                let mut slot = self.comp.borrow_mut();
                if slot.as_ref().is_some_and(|c| c.composition.as_raw() == done.as_raw()) {
                    slot.take()
                } else {
                    None
                }
            };
            if let Some(c) = open {
                // The window goes with the composition; this runs inside an edit session, so the window hides a moment later from its own timer (cand.rs).
                self.cand_open.set(false);
                cand::hide_later();
                // The app ended it; the text is already in the document as displayed. Only the underline and the host's state are left.
                // SAFETY: live interfaces; best effort.
                unsafe {
                    if let (Ok(range), Ok(p)) = (done.GetRange(), c.ctx.GetProperty(&GUID_PROP_ATTRIBUTE)) {
                        let _ = p.Clear(ecwrite, &range);
                    }
                }
                client::release(ctx_id(&c.ctx));
            }
            Ok(())
        })
    }
}

impl ITfTextInputProcessor_Impl for TextService_Impl {
    fn Activate(&self, ptim: Ref<'_, ITfThreadMgr>, tid: u32) -> Result<()> {
        self.activate(ptim, tid, 0)
    }
    fn Deactivate(&self) -> Result<()> {
        self.deactivate()
    }
}

impl ITfTextInputProcessorEx_Impl for TextService_Impl {
    fn ActivateEx(&self, ptim: Ref<'_, ITfThreadMgr>, tid: u32, dwflags: u32) -> Result<()> {
        self.activate(ptim, tid, dwflags)
    }
}

impl ITfKeyEventSink_Impl for TextService_Impl {
    fn OnSetFocus(&self, _fforeground: BOOL) -> Result<()> {
        Ok(())
    }
    fn OnTestKeyDown(&self, pic: Ref<'_, ITfContext>, wparam: WPARAM, _lparam: LPARAM) -> Result<BOOL> {
        self.guarded_key(|| self.test_key_down(pic, wparam))
    }
    fn OnTestKeyUp(&self, _pic: Ref<'_, ITfContext>, _wparam: WPARAM, _lparam: LPARAM) -> Result<BOOL> {
        Ok(FALSE)
    }
    fn OnKeyDown(&self, pic: Ref<'_, ITfContext>, wparam: WPARAM, _lparam: LPARAM) -> Result<BOOL> {
        self.guarded_key(|| self.key_down(pic, wparam))
    }
    fn OnKeyUp(&self, _pic: Ref<'_, ITfContext>, _wparam: WPARAM, _lparam: LPARAM) -> Result<BOOL> {
        Ok(FALSE)
    }
    fn OnPreservedKey(&self, _pic: Ref<'_, ITfContext>, _rguid: *const GUID) -> Result<BOOL> {
        Ok(FALSE)
    }
}

impl ITfCompositionSink_Impl for TextService_Impl {
    fn OnCompositionTerminated(&self, ecwrite: u32, pcomposition: Ref<'_, ITfComposition>) -> Result<()> {
        self.composition_terminated(ecwrite, pcomposition)
    }
}

impl ITfThreadMgrEventSink_Impl for TextService_Impl {
    fn OnInitDocumentMgr(&self, _pdim: Ref<'_, ITfDocumentMgr>) -> Result<()> {
        Ok(())
    }
    /// The document manager goes away: it leaves `seen_dims` (a new one may later get the same address).
    fn OnUninitDocumentMgr(&self, pdim: Ref<'_, ITfDocumentMgr>) -> Result<()> {
        self.guarded(|| {
            if let Ok(d) = pdim.ok() {
                self.seen_dims.borrow_mut().retain(|x| x.as_raw() != d.as_raw());
            }
            Ok(())
        })
    }
    /// A different document got the focus: the composition of the old one ends, its text stays.
    fn OnSetFocus(&self, pdimfocus: Ref<'_, ITfDocumentMgr>, _pdimprevfocus: Ref<'_, ITfDocumentMgr>) -> Result<()> {
        self.guarded(|| {
            self.end_own_composition();
            if let Ok(d) = pdimfocus.ok() {
                self.open_if_new(Some(d));
            }
            Ok(())
        })
    }
    fn OnPushContext(&self, _pic: Ref<'_, ITfContext>) -> Result<()> {
        Ok(())
    }
    fn OnPopContext(&self, _pic: Ref<'_, ITfContext>) -> Result<()> {
        Ok(())
    }
}

impl ITfThreadFocusSink_Impl for TextService_Impl {
    fn OnSetThreadFocus(&self) -> Result<()> {
        self.guarded(|| {
            self.open_if_new(None);
            Ok(())
        })
    }
    /// The focus went to another thread (another window of the same process included).
    fn OnKillThreadFocus(&self) -> Result<()> {
        self.guarded(|| {
            self.end_own_composition();
            Ok(())
        })
    }
}

impl ITfDisplayAttributeProvider_Impl for TextService_Impl {
    fn EnumDisplayAttributeInfo(&self) -> Result<IEnumTfDisplayAttributeInfo> {
        guard::com(|| Ok(DisplayEnum::new().into()))
    }
    fn GetDisplayAttributeInfo(&self, guid: *const GUID) -> Result<ITfDisplayAttributeInfo> {
        guard::com(|| {
            // SAFETY: TSF passes a valid GUID pointer; null is refused.
            if guid.is_null() || unsafe { *guid } != DISPLAY_ATTR_COMPOSING {
                return Err(Error::from_hresult(E_INVALIDARG));
            }
            Ok(DisplayInfo.into())
        })
    }
}
