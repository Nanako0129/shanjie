//! Everything that touches the TSF document (docs/spec/windows-tip.md §3.7): edit sessions, the composition range, the underline
//! attribute, the selection. The functions take the edit cookie `ec` of a session that is already running.
use crate::guard;
use shanjie_win_proto::Out;
use std::cell::RefCell;
use std::mem::ManuallyDrop;
use std::ptr;
use windows::Win32::UI::TextServices::{
    ITfComposition, ITfCompositionSink, ITfContext, ITfContextComposition, ITfEditSession, ITfEditSession_Impl, ITfInsertAtSelection, ITfRange, GUID_PROP_ATTRIBUTE,
    INSERT_TEXT_AT_SELECTION_FLAGS, TF_AE_NONE, TF_ANCHOR_END, TF_ANCHOR_START, TF_CONTEXT_EDIT_CONTEXT_FLAGS, TF_ES_ASYNCDONTCARE, TF_ES_READWRITE, TF_ES_SYNC, TF_IAS_QUERYONLY, TF_SELECTION,
    TF_SELECTIONSTYLE,
};
use windows::Win32::System::Variant::VARIANT;
use windows_core::{implement, Interface, Result};

/// The composition this TIP instance opened, with the context it lives in.
#[derive(Clone)]
pub struct Comp {
    pub composition: ITfComposition,
    pub ctx: ITfContext,
}

pub type CompSlot = RefCell<Option<Comp>>;

/// The flags of a request made outside a key handler: write access, run at once if the host allows, else later.
pub const ASYNC_WRITE: TF_CONTEXT_EDIT_CONTEXT_FLAGS = TF_CONTEXT_EDIT_CONTEXT_FLAGS(TF_ES_ASYNCDONTCARE.0 | TF_ES_READWRITE.0);
/// `OnKeyDown`: the host must grant the write lock now or refuse.
pub const SYNC_WRITE: TF_CONTEXT_EDIT_CONTEXT_FLAGS = TF_CONTEXT_EDIT_CONTEXT_FLAGS(TF_ES_SYNC.0 | TF_ES_READWRITE.0);

type Job = Box<dyn FnOnce(u32) -> Result<()>>;

#[implement(ITfEditSession)]
struct EditSession {
    job: RefCell<Option<Job>>,
}

impl ITfEditSession_Impl for EditSession_Impl {
    fn DoEditSession(&self, ec: u32) -> Result<()> {
        guard::com(|| {
            let job = self.job.borrow_mut().take();
            match job {
                Some(f) => f(ec),
                None => Ok(()),
            }
        })
    }
}

/// Requests an edit session on `ctx`; `Ok` only when the request was granted and the job succeeded (for an async request: queued).
pub fn request(ctx: &ITfContext, tid: u32, flags: TF_CONTEXT_EDIT_CONTEXT_FLAGS, job: impl FnOnce(u32) -> Result<()> + 'static) -> Result<()> {
    let es: ITfEditSession = EditSession { job: RefCell::new(Some(Box::new(job))) }.into();
    // SAFETY: `es` is a live COM object.
    let session_hr = unsafe { ctx.RequestEditSession(tid, &es, flags) }?;
    session_hr.ok()
}

/// docs/spec/windows-tip.md §3.8: the underline attribute goes before `EndComposition`, on every path that ends a composition.
fn clear_attr(ec: u32, ctx: &ITfContext, range: &ITfRange) {
    // SAFETY: live interfaces; failures are ignored (the text is what matters).
    unsafe {
        if let Ok(p) = ctx.GetProperty(&GUID_PROP_ATTRIBUTE) {
            let _ = p.Clear(ec, range);
        }
    }
}

/// Clears the underline, then ends the composition; the text stays in the document.
pub fn end_composition(ec: u32, c: &Comp) -> Result<()> {
    // SAFETY: live interfaces.
    unsafe {
        if let Ok(range) = c.composition.GetRange() {
            clear_attr(ec, &c.ctx, &range);
        }
        c.composition.EndComposition(ec)
    }
}

/// Ends `c` from outside an edit session (focus changes, `Deactivate`, a composition left behind by another owner).
pub fn end_composition_later(tid: u32, c: Comp) {
    let ctx = c.ctx.clone();
    let _ = request(&ctx, tid, ASYNC_WRITE, move |ec| end_composition(ec, &c));
}

fn set_selection(ec: u32, ctx: &ITfContext, r: &ITfRange) -> Result<()> {
    let mut sel = TF_SELECTION { range: ManuallyDrop::new(Some(r.clone())), style: TF_SELECTIONSTYLE { ase: TF_AE_NONE, fInterimChar: false.into() } };
    // SAFETY: `sel` holds one reference, released right after the call.
    let r = unsafe { ctx.SetSelection(ec, std::slice::from_ref(&sel)) };
    // SAFETY: dropped exactly once.
    unsafe { ManuallyDrop::drop(&mut sel.range) };
    r
}

/// The caret at the end of `range`.
fn caret_after(ec: u32, ctx: &ITfContext, range: &ITfRange) -> Result<()> {
    // SAFETY: live interfaces.
    unsafe {
        let r = range.Clone()?;
        r.Collapse(ec, TF_ANCHOR_END)?;
        set_selection(ec, ctx, &r)
    }
}

/// The caret `cursor` UTF-16 units into `range` (`cursor_utf16` of the core, which counts the same units as a TSF range).
fn caret_at(ec: u32, ctx: &ITfContext, range: &ITfRange, cursor: u32) -> Result<()> {
    // SAFETY: live interfaces; `moved` is a valid out pointer, no halt condition.
    unsafe {
        let r = range.Clone()?;
        r.Collapse(ec, TF_ANCHOR_START)?;
        let mut moved = 0i32;
        r.ShiftEnd(ec, i32::try_from(cursor).unwrap_or(i32::MAX), &mut moved, ptr::null())?;
        r.Collapse(ec, TF_ANCHOR_END)?;
        set_selection(ec, ctx, &r)
    }
}

fn start_composition(ec: u32, ctx: &ITfContext, sink: &ITfCompositionSink) -> Result<Comp> {
    // SAFETY: live interfaces. QUERYONLY with no text returns the range at the selection without inserting anything.
    unsafe {
        let ins: ITfInsertAtSelection = ctx.cast()?;
        let at = ins.InsertTextAtSelection(ec, TF_IAS_QUERYONLY, &[])?;
        let cc: ITfContextComposition = ctx.cast()?;
        let composition = cc.StartComposition(ec, &at, sink)?;
        Ok(Comp { composition, ctx: ctx.clone() })
    }
}

/// Runs `f`, which ends `c`. A composition taken out of its slot is ended on every path: when `f` fails part way, `c` is still ended
/// (underline off, the text as it is) before the error goes up, so no composition stays open with nothing tracking it.
fn ending(ec: u32, c: &Comp, f: impl FnOnce() -> Result<()>) -> Result<()> {
    let r = f();
    if r.is_err() {
        let _ = end_composition(ec, c);
    }
    r
}

/// `OnKeyDown` step 3 (docs/spec/windows-tip.md §3.7), in this order: `commit` replaces the composition range (or goes in at the selection) and ends the
/// composition; `preedit` starts or continues one, with the underline and the caret; nothing at all with a composition open empties the
/// range first and then ends it. A composition that stays in `slot` when this fails is ended by the caller.
pub fn apply(ec: u32, ctx: &ITfContext, slot: &CompSlot, sink: &ITfCompositionSink, atom: u32, out: &Out) -> Result<()> {
    if !out.commit.is_empty() {
        let open = slot.borrow_mut().take();
        let text = &out.commit;
        match open {
            // SAFETY: live interfaces.
            Some(c) => ending(ec, &c, || unsafe {
                let range = c.composition.GetRange()?;
                clear_attr(ec, &c.ctx, &range);
                range.SetText(ec, 0, text)?;
                caret_after(ec, ctx, &range)?;
                c.composition.EndComposition(ec)
            })?,
            // SAFETY: live interfaces.
            None => unsafe {
                let ins: ITfInsertAtSelection = ctx.cast()?;
                let range = ins.InsertTextAtSelection(ec, INSERT_TEXT_AT_SELECTION_FLAGS(0), text)?;
                caret_after(ec, ctx, &range)?;
            },
        }
    }
    if !out.preedit.is_empty() {
        let existing = slot.borrow().clone();
        let c = match existing {
            Some(c) => c,
            None => {
                let c = start_composition(ec, ctx, sink)?;
                *slot.borrow_mut() = Some(c.clone());
                c
            }
        };
        // SAFETY: live interfaces; the attribute value is a TfGuidAtom as VT_I4.
        unsafe {
            let range = c.composition.GetRange()?;
            range.SetText(ec, 0, &out.preedit)?;
            ctx.GetProperty(&GUID_PROP_ATTRIBUTE)?.SetValue(ec, &range, &VARIANT::from(atom as i32))?;
            caret_at(ec, ctx, &range, out.cursor_utf16)?;
        }
    } else {
        let open = slot.borrow_mut().take();
        if let Some(c) = open {
            // SAFETY: live interfaces.
            ending(ec, &c, || unsafe {
                let range = c.composition.GetRange()?;
                clear_attr(ec, &c.ctx, &range);
                range.SetText(ec, 0, &[])?;
                c.composition.EndComposition(ec)
            })?;
        }
    }
    Ok(())
}
