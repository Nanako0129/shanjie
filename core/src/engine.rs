//! S3a key engine (docs/contracts/s3a.md): key events in, preedit / commit / candidates out.
//! R2: no input text in errors or panics; types holding input text do not derive Debug.

use crate::{decode_beam, Lexicon, NoLearning, BEAM_S1};
use std::collections::HashSet;
use std::path::Path;
use std::sync::Arc;

pub const MOD_SHIFT: u32 = 1;
pub const MOD_CONTROL: u32 = 2;
pub const MOD_OPTION: u32 = 4;
pub const MOD_COMMAND: u32 = 8;
pub const MOD_CAPSLOCK: u32 = 16;
pub const MAX_SYLLABLES: usize = 40;
pub const PAGE_SIZE: usize = 9;

/// Initial (21), medial (3), final (13) symbols in the contract's column order.
const SYMBOLS: &str = "ㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓㄔㄕㄖㄗㄘㄙㄧㄨㄩㄚㄛㄜㄝㄞㄟㄠㄡㄢㄣㄤㄥㄦ";
const KEYS_STANDARD: &str = "1qaz2wsxedcrfv5tgbyhnujm8ik,9ol.0p;/-";
const KEYS_ETEN: &str = "bpmfdtnlvkhg7c,./j;'sexuaorwiqzy890-=";
/// Tone marks for tone 1..5 (tone 1 is unmarked).
const TONE_MARKS: [&str; 5] = ["", "ˊ", "ˇ", "ˋ", "˙"];
const TONE_KEYS_STANDARD: [char; 4] = ['6', '3', '4', '7'];
const TONE_KEYS_ETEN: [char; 4] = ['2', '3', '4', '1'];
/// Shift + key -> punctuation (§4).
const SHIFT_PUNCT: [(char, char); 10] = [
    (',', '，'), ('.', '。'), ('/', '？'), ('1', '！'), (';', '：'),
    ('[', '「'), (']', '」'), ('9', '（'), ('0', '）'), ('`', '～'),
];

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Layout {
    Standard,
    Eten,
}

impl Layout {
    fn keys(self) -> &'static str {
        match self {
            Layout::Standard => KEYS_STANDARD,
            Layout::Eten => KEYS_ETEN,
        }
    }
    fn tone_keys(self) -> &'static [char; 4] {
        match self {
            Layout::Standard => &TONE_KEYS_STANDARD,
            Layout::Eten => &TONE_KEYS_ETEN,
        }
    }
    /// (column 0 initial / 1 medial / 2 final, symbol) of an unshifted key.
    pub fn symbol_of(self, key: char) -> Option<(usize, char)> {
        let i = self.keys().chars().position(|c| c == key)?;
        let sym = SYMBOLS.chars().nth(i)?;
        Some((if i < 21 { 0 } else if i < 24 { 1 } else { 2 }, sym))
    }
    /// Tone index 1..=4 (marks ˊˇˋ˙) of an unshifted key; space is handled by the caller.
    fn tone_of(self, key: char) -> Option<usize> {
        self.tone_keys().iter().position(|&c| c == key).map(|i| i + 1)
    }
    /// Inverse of `symbol_of`, for building key presses from a reading.
    pub fn key_of_symbol(self, sym: char) -> Option<char> {
        let i = SYMBOLS.chars().position(|c| c == sym)?;
        self.keys().chars().nth(i)
    }
    /// Key of a tone mark (ˊˇˋ˙); `None` for anything else (tone 1 uses the space bar).
    pub fn key_of_tone(self, mark: char) -> Option<char> {
        let i = TONE_MARKS[1..].iter().position(|m| m.chars().next() == Some(mark))?;
        Some(self.tone_keys()[i])
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum KeyKind {
    Char = 1,
    Space,
    Enter,
    Backspace,
    Delete,
    Esc,
    Left,
    Right,
    Up,
    Down,
    Home,
    End,
    Tab,
}

impl KeyKind {
    /// ABI code (1..=13) to kind.
    pub fn from_code(code: u32) -> Option<KeyKind> {
        use KeyKind::*;
        [Char, Space, Enter, Backspace, Delete, Esc, Left, Right, Up, Down, Home, End, Tab]
            .get((code as usize).checked_sub(1)?)
            .copied()
    }
}

/// Key event. `ch` is the keycap character without Shift; ignored unless `kind` is `Char`.
#[derive(Clone, Copy, PartialEq, Eq)]
pub struct Key {
    pub kind: KeyKind,
    pub ch: char,
    pub modifiers: u32,
}

impl Key {
    pub fn new(kind: KeyKind) -> Key {
        Key { kind, ch: '\0', modifiers: 0 }
    }
    pub fn ch(ch: char, modifiers: u32) -> Key {
        Key { kind: KeyKind::Char, ch, modifiers }
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum ResetMode {
    Commit,
    Discard,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum EngineError {
    /// Data files missing or unparsable (ABI code 3).
    LoadFailed,
    /// Decode failure; the engine has already reset itself (ABI code 4).
    Internal,
}

/// What the shell needs after one key. Holds input text, so no `Debug`.
#[derive(Clone, PartialEq)]
pub struct Output {
    pub handled: bool,
    pub commit: String,
    pub preedit: String,
    pub cursor_utf16: u32,
    /// Current page, at most `PAGE_SIZE`.
    pub candidates: Vec<String>,
    /// Index within the page; `None` when candidates are closed.
    pub selected: Option<usize>,
}

struct Fixed {
    start: usize,
    end: usize,
    word: String,
}

struct Cands {
    /// (word, length in syllables)
    list: Vec<(String, usize)>,
    sel: usize,
}

pub struct Engine {
    lex: Arc<Lexicon>,
    layout: Layout,
    syls: Vec<String>,
    cursor: usize,
    pend: [Option<char>; 3],
    /// Columns in the order their symbols were placed (for Backspace).
    order: Vec<usize>,
    fixed: Vec<Fixed>,
    display: String,
    cands: Option<Cands>,
}

/// Base lexicon + overlay from `data_dir` (§5), same as the eval CLI default.
pub fn load_lexicon(data_dir: &Path) -> Result<Arc<Lexicon>, EngineError> {
    let base = std::fs::read_to_string(data_dir.join("mcbpmf-data.txt")).map_err(|_| EngineError::LoadFailed)?;
    let overlay = std::fs::read_to_string(data_dir.join("overlay-add.tsv")).map_err(|_| EngineError::LoadFailed)?;
    Lexicon::parse_with(&base, Some(&overlay)).map(Arc::new).map_err(|_| EngineError::LoadFailed)
}

impl Engine {
    pub fn new(data_dir: &Path, layout: Layout) -> Result<Engine, EngineError> {
        Ok(Engine::with_lexicon(load_lexicon(data_dir)?, layout))
    }

    /// Share an already-loaded lexicon (tests load the 131 MB file once).
    pub fn with_lexicon(lex: Arc<Lexicon>, layout: Layout) -> Engine {
        Engine {
            lex,
            layout,
            syls: Vec::new(),
            cursor: 0,
            pend: [None; 3],
            order: Vec::new(),
            fixed: Vec::new(),
            display: String::new(),
            cands: None,
        }
    }

    /// §6 reset: Commit returns the display string (pending syllable dropped); both clear everything.
    pub fn reset(&mut self, mode: ResetMode) -> Output {
        let commit = if mode == ResetMode::Commit { std::mem::take(&mut self.display) } else { String::new() };
        self.clear_all();
        self.view(true, commit)
    }

    pub fn key(&mut self, k: Key) -> Result<Output, EngineError> {
        let r = self.dispatch(k);
        if r.is_err() {
            self.clear_all();
        }
        r
    }

    fn clear_all(&mut self) {
        self.syls.clear();
        self.cursor = 0;
        self.pend = [None; 3];
        self.order.clear();
        self.fixed.clear();
        self.display.clear();
        self.cands = None;
    }

    fn pending(&self) -> String {
        self.pend.iter().flatten().collect()
    }

    fn view(&self, handled: bool, commit: String) -> Output {
        let chars: Vec<char> = self.display.chars().collect();
        // ponytail: assumes one display char per syllable (lexicon invariant); clamped otherwise.
        let at = self.cursor.min(chars.len());
        let pending = self.pending();
        let mut preedit: String = chars[..at].iter().collect();
        preedit.push_str(&pending);
        let cursor_utf16 = preedit.encode_utf16().count() as u32;
        preedit.extend(chars[at..].iter());
        let (candidates, selected) = match &self.cands {
            Some(c) => {
                let page = c.sel / PAGE_SIZE;
                let list = c.list.iter().skip(page * PAGE_SIZE).take(PAGE_SIZE).map(|(w, _)| w.clone()).collect();
                (list, Some(c.sel % PAGE_SIZE))
            }
            None => (Vec::new(), None),
        };
        Output { handled, commit, preedit, cursor_utf16, candidates, selected }
    }

    /// Recompute the display string: free segments decoded top-1, fixed words in between (§3.1).
    fn refresh(&mut self) -> Result<(), EngineError> {
        let mut out = String::new();
        let mut pos = 0;
        let seg =|from: usize, to: usize, out: &mut String| -> Result<(), EngineError> {
            if from < to {
                let best = decode_beam(&self.lex, &self.syls[from..to], &mut NoLearning, BEAM_S1)
                    .map_err(|_| EngineError::Internal)?;
                out.push_str(&best.first().ok_or(EngineError::Internal)?.1.concat());
            }
            Ok(())
        };
        for f in &self.fixed {
            seg(pos, f.start, &mut out)?;
            out.push_str(&f.word);
            pos = f.end;
        }
        seg(pos, self.syls.len(), &mut out)?;
        self.display = out;
        Ok(())
    }

    fn handled(&self) -> Result<Output, EngineError> {
        Ok(self.view(true, String::new()))
    }
    fn passthrough(&self, commit: String) -> Result<Output, EngineError> {
        Ok(self.view(false, commit))
    }
    /// Commit the whole composition and clear all state.
    fn take_commit(&mut self) -> String {
        let s = std::mem::take(&mut self.display);
        self.clear_all();
        s
    }

    fn dispatch(&mut self, k: Key) -> Result<Output, EngineError> {
        let m = k.modifiers;
        let is_char = k.kind == KeyKind::Char;
        let ctrl_bs = is_char && k.ch == '\\' && m == MOD_CONTROL;
        // 1: pass through, no state change.
        if m & (MOD_OPTION | MOD_COMMAND | MOD_CAPSLOCK) != 0 || (m & MOD_CONTROL != 0 && !ctrl_bs) {
            return self.passthrough(String::new());
        }
        // 2: punctuation.
        let punct = if ctrl_bs {
            Some('、')
        } else if is_char && m == MOD_SHIFT {
            SHIFT_PUNCT.iter().find(|(c, _)| *c == k.ch).map(|(_, p)| *p)
        } else {
            None
        };
        if let Some(p) = punct {
            let mut commit = self.take_commit();
            commit.push(p);
            return Ok(self.view(true, commit));
        }
        let plain = m == 0;
        // 3-8: candidates open.
        if self.cands.is_some() && self.candidate_key(k)? {
            return self.handled();
        }
        let zy = if is_char && plain { self.layout.symbol_of(k.ch) } else { None };
        let tone = match k.kind {
            KeyKind::Space => Some(0),
            KeyKind::Char if plain => self.layout.tone_of(k.ch),
            _ => None,
        };
        let has_pending = !self.order.is_empty();
        if has_pending {
            // 9-13
            if let Some((col, sym)) = zy {
                self.order.retain(|&c| c != col);
                self.order.push(col);
                self.pend[col] = Some(sym);
            } else if let Some(t) = tone {
                return self.finish_syllable(t);
            } else if k.kind == KeyKind::Backspace {
                if let Some(col) = self.order.pop() {
                    self.pend[col] = None;
                }
            } else if k.kind == KeyKind::Esc {
                self.pend = [None; 3];
                self.order.clear();
            }
            return self.handled();
        }
        // 14
        if let Some((col, sym)) = zy {
            self.pend[col] = Some(sym);
            self.order.push(col);
            return self.handled();
        }
        if self.syls.is_empty() {
            return self.passthrough(String::new()); // 22
        }
        let n = self.syls.len();
        match k.kind {
            KeyKind::Space | KeyKind::Down => self.open_candidates(),
            KeyKind::Up => {}
            KeyKind::Char if tone.is_some() => {}
            KeyKind::Left => self.cursor = self.cursor.saturating_sub(1),
            KeyKind::Right => self.cursor = (self.cursor + 1).min(n),
            KeyKind::Home => self.cursor = 0,
            KeyKind::End => self.cursor = n,
            KeyKind::Backspace if self.cursor > 0 => {
                self.cursor -= 1;
                self.remove_syllable(self.cursor)?;
            }
            KeyKind::Delete if self.cursor < n => self.remove_syllable(self.cursor)?,
            KeyKind::Backspace | KeyKind::Delete => {}
            KeyKind::Enter => {
                let commit = self.take_commit();
                return Ok(self.view(true, commit));
            }
            KeyKind::Esc => self.clear_all(),
            _ => {
                let commit = self.take_commit(); // 21
                return self.passthrough(commit);
            }
        }
        self.handled()
    }

    /// Rules 3-8. `Ok(true)` = consumed; `Ok(false)` = candidates closed, key continues at rule 9.
    fn candidate_key(&mut self, k: Key) -> Result<bool, EngineError> {
        let Some(c) = &mut self.cands else { return Ok(false) };
        let (len, sel) = (c.list.len(), c.sel);
        let page_start = sel / PAGE_SIZE * PAGE_SIZE;
        let next_page = (page_start + PAGE_SIZE < len).then_some(page_start + PAGE_SIZE);
        match k.kind {
            KeyKind::Char if k.modifiers == 0 && ('1'..='9').contains(&k.ch) => {
                let idx = page_start + (k.ch as usize - '1' as usize);
                if idx < len {
                    self.choose(idx)?;
                }
            }
            KeyKind::Up => c.sel = sel.saturating_sub(1),
            KeyKind::Down => c.sel = (sel + 1).min(len - 1),
            KeyKind::Left => c.sel = page_start.saturating_sub(PAGE_SIZE),
            KeyKind::Right => c.sel = next_page.unwrap_or(sel),
            KeyKind::Space => c.sel = next_page.unwrap_or(0),
            KeyKind::Enter => self.choose(sel)?,
            KeyKind::Esc | KeyKind::Backspace => self.cands = None,
            _ => {
                self.cands = None;
                return Ok(false);
            }
        }
        Ok(true)
    }

    /// §2: complete the pending syllable with tone 0..=4 (0 = space = tone 1).
    fn finish_syllable(&mut self, tone: usize) -> Result<Output, EngineError> {
        let mut syl = self.pending();
        // tone index 1..=4 -> marks[1..=4]; space (0) -> unmarked tone 1
        syl.push_str(TONE_MARKS[tone]);
        if self.lex.entries(std::slice::from_ref(&syl)).is_empty() {
            return self.handled();
        }
        let c = self.cursor;
        self.fixed.retain_mut(|f| {
            if f.end <= c {
                true
            } else if f.start >= c {
                f.start += 1;
                f.end += 1;
                true
            } else {
                false
            }
        });
        self.syls.insert(c, syl);
        self.cursor += 1;
        self.pend = [None; 3];
        self.order.clear();
        self.refresh()?;
        if self.syls.len() >= MAX_SYLLABLES {
            let commit = self.take_commit();
            return Ok(self.view(true, commit));
        }
        self.handled()
    }

    fn remove_syllable(&mut self, i: usize) -> Result<(), EngineError> {
        self.syls.remove(i);
        self.fixed.retain_mut(|f| {
            if f.end <= i {
                true
            } else if f.start > i {
                f.start -= 1;
                f.end -= 1;
                true
            } else {
                false
            }
        });
        self.refresh()
    }

    /// §3.1 candidate range: readings ending at the cursor (or starting at 0 when the cursor is 0).
    fn open_candidates(&mut self) {
        let a = self.cursor;
        let avail = if a == 0 { self.syls.len() } else { a };
        let mut seen = HashSet::new();
        let mut list = Vec::new();
        for l in (1..=self.lex.max_len.min(avail)).rev() {
            let key = if a == 0 { &self.syls[..l] } else { &self.syls[a - l..a] };
            for (w, _) in self.lex.entries(key) {
                if seen.insert(w) {
                    list.push((w.to_string(), l));
                }
            }
        }
        if !list.is_empty() {
            self.cands = Some(Cands { list, sel: 0 });
        }
    }

    /// Fix candidate `idx` over its range, close candidates, recompute.
    fn choose(&mut self, idx: usize) -> Result<(), EngineError> {
        let Some(c) = self.cands.take() else { return Ok(()) };
        let Some((word, l)) = c.list.get(idx).cloned() else { return Ok(()) };
        let (start, end) = if self.cursor == 0 { (0, l) } else { (self.cursor - l, self.cursor) };
        self.fixed.retain(|f| !(f.start < end && start < f.end));
        self.fixed.push(Fixed { start, end, word });
        self.fixed.sort_by_key(|f| f.start);
        self.refresh()
    }
}
