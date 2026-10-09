//! S3a key engine (docs/contracts/s3a.md): key events in, preedit / commit / candidates out.
//! R2: no input text in errors or panics; types holding input text do not derive Debug.

use crate::learn::{context_key, local_day, Learner, Level, Record};
use crate::learn_store::{LearnStore, Opened, StoreError, JOURNAL_MAX};
use crate::predict::{predict, reading_matches, unit_of_syllable, Mode, Unit};
use crate::lm::{decode_segment_learned, history, CappedLexicon, Demote, End, Learn, Lm, Profile};
use crate::{decode_beam, Lexicon, NoLearning, BEAM_S1};
use std::collections::{HashMap, HashSet};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::{Path, PathBuf};
use std::sync::Arc;

pub const MOD_SHIFT: u32 = 1;
pub const MOD_CONTROL: u32 = 2;
pub const MOD_OPTION: u32 = 4;
pub const MOD_COMMAND: u32 = 8;
pub const MOD_CAPSLOCK: u32 = 16;
pub const MAX_SYLLABLES: usize = 40;
pub const PAGE_SIZE: usize = 9;
/// Rows visible in the expanded grid (a-4: five, with a scroll bar).
pub const GRID_ROWS: usize = 5;
/// V3 engine contract section 1.2: at most this many items from the long starts (the decoded word starts, section 11)
/// go ahead of the cursor start's items; the rest of them follow. Chosen by the user from a measured trade-off
/// (research log 2026-10-07): the first key keeps its own candidates, the second syllable still finds the long word.
pub const PREDICT_LONG_CAP: usize = 3;
/// V3 engine contract section 11: decoded word starts this many syllables back are long starts too, and the positions
/// inside words this far back are queried last. Five is the user's choice; long names in the ACG pack were measured
/// at 4, 5 and 6 (research log 2026-10-09).
pub const PREDICT_BACK: usize = 5;
/// Items in the prediction row.
pub const PREDICT_MAX: usize = PAGE_SIZE;
/// Items asked of `predict` for a start that some learned record could match (V3 engine contract section 10.2 step 1).
/// A taught situation's items sit in the first `PREDICT_MAX`, a variant of it up to this far. Any other start asks for
/// `PREDICT_MAX` only, so with an empty learner the row is the slice-1 row bit for bit. The added time of 27 over 9
/// is only measured as part of the whole row with a 50,000-record store (research log 2026-10-08), not by itself.
pub const PREDICT_SCAN: usize = 27;

/// Initial (21), medial (3), final (13) symbols in the contract's column order.
const SYMBOLS: &str = "ㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓㄔㄕㄖㄗㄘㄙㄧㄨㄩㄚㄛㄜㄝㄞㄟㄠㄡㄢㄣㄤㄥㄦ";
const KEYS_STANDARD: &str = "1qaz2wsxedcrfv5tgbyhnujm8ik,9ol.0p;/-";
const KEYS_ETEN: &str = "bpmfdtnlvkhg7c,./j;'sexuaorwiqzy890-=";
/// Tone marks for tone 1..5 (tone 1 is unmarked).
const TONE_MARKS: [&str; 5] = ["", "ˊ", "ˇ", "ˋ", "˙"];
const TONE_KEYS_STANDARD: [char; 4] = ['6', '3', '4', '7'];
const TONE_KEYS_ETEN: [char; 4] = ['2', '3', '4', '1'];
/// Shift + key -> punctuation (§4). The bracket row follows Apple's Zhuyin
/// (com.apple.inputmethod.TCIM.Zhuyin), measured 2026-10-05 with a key probe that typed each key into
/// its own window: ⇧[ 『, ⇧] 』, ⇧\ ｜, ⇧' “, ⇧= ＋, ⇧` ～.
const SHIFT_PUNCT: [(char, char); 13] = [
    (',', '，'), ('.', '。'), ('/', '？'), ('1', '！'), (';', '：'),
    ('[', '『'), (']', '』'), ('9', '（'), ('0', '）'), ('`', '～'),
    ('\\', '｜'), ('\'', '“'), ('=', '＋'),
];
/// Unshifted key -> punctuation, from the same probe: [ 「, ] 」, \ 、, ' ‘, = ＝, ` ·. Only when the key is
/// neither a Zhuyin nor a tone key in the current layout (Eten uses ' and = for Zhuyin).
const PLAIN_PUNCT: [(char, char); 6] = [('[', '「'), (']', '」'), ('\\', '、'), ('\'', '‘'), ('=', '＝'), ('`', '·')];

/// s3d §2: punctuation in the composition is a one-cell token under this reserved reading prefix
/// (`_punct_，`); the lexicon has no such reading. Each one is also a length-1 fixed word.
const PUNCT_PREFIX: &str = "_punct_";

/// s3e §3: built-in punctuation alternatives, used until `set_punctuation` succeeds. From
/// data/lexicon/mcbpmf-data.txt (McBopomofo, MIT): ， `_punctuation_Standard_<` lines 1093-1097;
/// 。 `_punctuation_Standard_>` 1098-1103; ： `_punctuation_:` 1044-1045; 「 `_punctuation_{`
/// 2302-2308 and 」 `_punctuation_}` 2313-2319 (`_punctuation_[`/`]` hold only 「」); 、
/// `_punctuation_\\` 1105-1106. The other marks have no alternatives there. 『』 (now Shift+[ / Shift+])
/// and the quotes ‘“ (plain and Shift+') are ours, so a mark without Apple's table still reaches its
/// pair and the closing quotes ’”.
const DEFAULT_PUNCT: [(char, &[&str]); 10] = [
    ('，', &["〈", "《", "︿", "︽"]),
    ('。', &["．", "〉", "》", "﹀", "︾"]),
    ('：', &["；"]),
    ('「', &["『", "《", "〔", "｛", "〈", "【", "〖"]),
    ('」', &["』", "》", "〕", "｝", "〉", "】", "〗"]),
    ('、', &["＼", "／"]),
    ('『', &["「", "《", "〔", "｛", "〈", "【", "〖"]),
    ('』', &["」", "》", "〕", "｝", "〉", "】", "〗"]),
    ('‘', &["’"]),
    ('“', &["”"]),
];
/// s3e §3 limits on a table passed to `set_punctuation`.
const PUNCT_TABLE_MAX_BYTES: usize = 64 * 1024;
const PUNCT_TABLE_MAX_LINES: usize = 1000;

fn default_punct() -> HashMap<char, Vec<String>> {
    DEFAULT_PUNCT.iter().map(|(k, v)| (*k, v.iter().map(|s| s.to_string()).collect())).collect()
}

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
    /// Collapsed: the current page, at most `PAGE_SIZE`. Expanded: the visible rows from `top`, at
    /// most `GRID_ROWS * PAGE_SIZE`.
    pub candidates: Vec<String>,
    /// Index within `candidates`; `None` when candidates are closed.
    pub selected: Option<usize>,
    /// 0 = collapsed single row; `PAGE_SIZE` = expanded (each row is one page).
    pub columns: u32,
    /// Position of `candidates[0]` in the whole list (what `Engine::pick` offsets from); 0 when closed.
    pub first: u32,
    /// Length of the whole list; 0 when closed.
    pub total: u32,
}

struct Fixed {
    start: usize,
    end: usize,
    word: String,
    /// S4: the text the span showed before the candidate pick, when the pick is a pending learn
    /// (chosen while learning was on, not punctuation). Learned at commit if still on and different.
    /// `Some("")` is a prediction pick (V3 section 10.1): nothing was displaced, so nothing halves.
    pre: Option<String>,
    /// The context key a prediction pick is recorded under, fixed when the row was built: re-decoding after the pick can
    /// change the text before the start, so the commit must not recompute it (section 10.1). `None` for candidate picks.
    key: Option<String>,
}

/// One item of the prediction row: the word, the compatible reading that scored it, and the syllable index where
/// the word starts.
struct Pred {
    word: String,
    reading: Vec<String>,
    start: usize,
    /// The start's context key (`context_key` of the left context and the display before the start).
    key: String,
    /// Decayed weight of the learned (key, reading, word) record at the start's context (0: not learned).
    weight: f64,
}

struct Cands {
    /// (word, length in syllables)
    list: Vec<(String, usize)>,
    sel: usize,
    expanded: bool,
    /// First visible grid row while expanded; keeps the selected row inside `GRID_ROWS`. Set to the
    /// selected page on expand, so the first row is the page the collapsed bar showed (s3b2 §9).
    top: usize,
}

impl Cands {
    fn new(list: Vec<(String, usize)>) -> Cands {
        Cands { list, sel: 0, expanded: false, top: 0 }
    }

    fn scroll(&mut self) {
        let row = self.sel / PAGE_SIZE;
        if row < self.top {
            self.top = row;
        } else if row >= self.top + GRID_ROWS {
            self.top = row + 1 - GRID_ROWS;
        }
    }

    /// (position of the first output candidate, how many are output).
    fn window(&self) -> (usize, usize) {
        let len = self.list.len();
        if self.expanded {
            let first = self.top * PAGE_SIZE;
            (first, (len - first).min(GRID_ROWS * PAGE_SIZE))
        } else {
            let first = self.sel / PAGE_SIZE * PAGE_SIZE;
            (first, (len - first).min(PAGE_SIZE))
        }
    }
}

/// Loaded bigram model and the capped lexicon built from it (decoding only).
#[derive(Clone)]
struct LmState {
    lm: Arc<Lm>,
    capped: Arc<CappedLexicon>,
}

pub struct Engine {
    lex: Arc<Lexicon>,
    /// Where `new` read the data from; `None` for `with_lexicon` engines (they cannot `load_lm`).
    data_dir: Option<PathBuf>,
    /// The enabled packs' rows `new` read (acg-pack contract A.2), appended to `overlay-add.tsv` when
    /// `load_lm` caps; `None` with no pack, so a pack-off engine holds nothing extra.
    pack_text: Option<String>,
    lm: Option<LmState>,
    profile: Profile,
    /// Whether the table applies (default on; `set_demote`).
    demote: bool,
    /// Words of the current best path with the lp each was scored with, whether the word is a
    /// punctuation token, and its demotion delta (LM mode only).
    path: Vec<(String, f64, bool, f64)>,
    /// s3e: punctuation mark -> its alternatives (built-in default until `set_punctuation`).
    punct: HashMap<char, Vec<String>>,
    layout: Layout,
    syls: Vec<String>,
    cursor: usize,
    pend: [Option<char>; 3],
    fixed: Vec<Fixed>,
    display: String,
    cands: Option<Cands>,
    /// V3: the passive prediction row (empty = none) and, once Tab entered it, the selected item.
    pred: Vec<Pred>,
    pred_sel: Option<usize>,
    /// Whether the row is computed at all (default on; `set_prediction`).
    predict_on: bool,
    /// Test hook: false queries `PREDICT_SCAN` at every start once the learner has records (no gate).
    scan_gate: bool,
    /// Set by the key rules that recompute the row (section 1.1); `key` recomputes after the rule ran.
    pred_dirty: bool,
    /// S4: the Han tail (≤ 2 chars) of the text before the insertion point, from `set_left_context`.
    left: String,
    learning: bool,
    /// ε of the global learning level (§12); a test hook sweeps it.
    eps_global: f64,
    learner: Learner,
    store: Option<LearnStore>,
    /// §4 bit0: the last full rewrite failed. Set by a failed full rewrite; cleared only by a
    /// successful full rewrite or clear. While it is set, `must_rewrite` is set too.
    write_failed: bool,
    /// §4: the next write must be a full rewrite. Set before a forget or clear touches memory and
    /// before every full rewrite; cleared only when a full rewrite's rename (or a clear) succeeds, so
    /// a failure or a panic part way leaves it set.
    must_rewrite: bool,
    /// §4: day of the last successful full rewrite; `None` after open and after a clear, so the first
    /// write then, and the first write of each day, is a full rewrite.
    last_full: Option<i64>,
    /// §4: lines appended since the last full rewrite.
    appended: usize,
    /// Test-purpose clock (day number); `None` uses the local calendar day.
    today: Option<i64>,
    /// Test-purpose injection: the next learning pass panics (contract §6.9).
    learn_panic: bool,
    /// Test-purpose injection: the next ⌘⌫ forget panics after changing memory (§6.13).
    forget_panic: bool,
}

/// Base lexicon + overlay from `data_dir` (§5), same as the eval CLI default. The overlay rows are
/// `overlay-add.tsv` then `sandhi-add.tsv` (S2r: MOE-standard 一/不 readings derived from the base),
/// in that fixed order; both are required.
pub fn load_lexicon(data_dir: &Path) -> Result<Arc<Lexicon>, EngineError> {
    load_lexicon_packs(data_dir, None).map(|(lex, _)| lex)
}

/// Pack bits of `Engine::new_with_packs` and `shanjie_engine_new_packs` (docs/contracts/acg-pack.md A.2).
pub const PACK_ACG: u32 = 1;
pub const PACK_ALL: u32 = PACK_ACG;
/// A pack's overlay file inside the packs directory, in the order the packs are appended.
const PACK_FILES: [(u32, &str); 1] = [(PACK_ACG, "acg-add.tsv")];

/// The enabled packs' overlay rows in `dir`, concatenated; a pack whose file is missing contributes
/// nothing (the engine is then identical to one without packs). An unreadable file is a load failure.
pub fn read_packs(dir: &Path, mask: u32) -> Result<String, EngineError> {
    let mut text = String::new();
    for (bit, name) in PACK_FILES {
        if mask & bit == 0 {
            continue;
        }
        match std::fs::read_to_string(dir.join(name)) {
            Ok(t) => {
                if !text.is_empty() && !text.ends_with('\n') {
                    text.push('\n');
                }
                text.push_str(&t);
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(_) => return Err(EngineError::LoadFailed),
        }
    }
    Ok(text)
}

/// `load_lexicon` with the rows of `packs` (`(directory, mask)`, see `read_packs`) after `sandhi-add.tsv`,
/// plus those rows alone (empty with no pack), for `capping_overlay`. `None` or an empty mask: exactly
/// `load_lexicon`. The engine and the evaluation CLI share this.
pub fn load_lexicon_packs(data_dir: &Path, packs: Option<(&Path, u32)>) -> Result<(Arc<Lexicon>, String), EngineError> {
    let base = std::fs::read_to_string(data_dir.join("mcbpmf-data.txt")).map_err(|_| EngineError::LoadFailed)?;
    let overlay = std::fs::read_to_string(data_dir.join("overlay-add.tsv")).map_err(|_| EngineError::LoadFailed)?;
    let sandhi = std::fs::read_to_string(data_dir.join("sandhi-add.tsv")).map_err(|_| EngineError::LoadFailed)?;
    let mut text = join_overlays(overlay, &sandhi);
    let extra = match packs {
        Some((dir, mask)) => read_packs(dir, mask)?,
        None => String::new(),
    };
    if !extra.is_empty() {
        text = join_overlays(text, &extra);
    }
    let lex = Lexicon::parse_with(&base, Some(&text)).map_err(|_| EngineError::LoadFailed)?;
    Ok((Arc::new(lex), extra))
}

/// The text `CappedLexicon::new` takes as its overlay: `overlay-add.tsv` (read here, as `load_lm` always
/// did), then the packs' rows `load_lexicon_packs` returned (no sandhi rows).
pub fn capping_overlay(data_dir: &Path, pack_text: &str) -> Result<String, EngineError> {
    let overlay = std::fs::read_to_string(data_dir.join("overlay-add.tsv")).map_err(|_| EngineError::LoadFailed)?;
    Ok(if pack_text.is_empty() { overlay } else { join_overlays(overlay, pack_text) })
}

/// The overlay text the lexicon is parsed with: `overlay-add.tsv` then `sandhi-add.tsv`, with a line
/// break between them even if the first lacks a trailing one. Appends in place
/// with an exact reserve: peak RSS of the engine_lm production replay (2026-10-04) was 373 MB before S2r,
/// 390 MB when the 17 MB overlay-add.tsv was copied, 411 MB when push_str grew it by doubling, 375 MB now.
fn join_overlays(mut overlay: String, sandhi: &str) -> String {
    overlay.reserve_exact(sandhi.len() + 1);
    if !overlay.is_empty() && !overlay.ends_with('\n') {
        overlay.push('\n');
    }
    overlay.push_str(sandhi);
    overlay
}

impl Engine {
    pub fn new(data_dir: &Path, layout: Layout) -> Result<Engine, EngineError> {
        Engine::new_with_packs(data_dir, layout, None)
    }

    /// `new` with word packs: `packs` is the packs directory and the `PACK_*` bits to enable (acg-pack
    /// contract A.2). The pack rows are parsed into the lexicon, so a pack is fixed for the engine's life;
    /// the shell switches by building a new engine, like a layout change. Mask 0 / `None`: same as `new`.
    /// An unknown bit is `LoadFailed`.
    pub fn new_with_packs(data_dir: &Path, layout: Layout, packs: Option<(&Path, u32)>) -> Result<Engine, EngineError> {
        if packs.is_some_and(|(_, m)| m & !PACK_ALL != 0) {
            return Err(EngineError::LoadFailed);
        }
        let packs = packs.filter(|&(_, m)| m != 0);
        let (lex, pack_text) = load_lexicon_packs(data_dir, packs)?;
        let mut e = Engine::with_lexicon(lex, layout);
        e.pack_text = Some(pack_text).filter(|t| !t.is_empty());
        // Required like the other data files, and every row must name an entry of the lexicon (contract
        // sw-sensitive-demote section 2); `load_lm` reads it again to resolve it against the capped lexicon.
        let demote = std::fs::read_to_string(data_dir.join("demote.tsv")).map_err(|_| EngineError::LoadFailed)?;
        if !Demote::parse(&demote).is_some_and(|d| d.check(&e.lex)) {
            return Err(EngineError::LoadFailed);
        }
        e.data_dir = Some(data_dir.to_path_buf());
        Ok(e)
    }

    /// Share an already-loaded lexicon (tests load the 131 MB file once).
    pub fn with_lexicon(lex: Arc<Lexicon>, layout: Layout) -> Engine {
        Engine {
            lex,
            data_dir: None,
            pack_text: None,
            lm: None,
            profile: Profile::Chat,
            demote: true,
            path: Vec::new(),
            punct: default_punct(),
            layout,
            syls: Vec::new(),
            cursor: 0,
            pend: [None; 3],
            fixed: Vec::new(),
            display: String::new(),
            cands: None,
            pred: Vec::new(),
            pred_sel: None,
            predict_on: true,
            scan_gate: true,
            pred_dirty: false,
            left: String::new(),
            learning: false,
            eps_global: crate::lm::LEARN_EPS_GLOBAL,
            learner: Learner::default(),
            store: None,
            write_failed: false,
            must_rewrite: false,
            last_full: None,
            appended: 0,
            today: None,
            learn_panic: false,
            forget_panic: false,
        }
    }

    // ---- S4 learning (docs/contracts/s4-learning.md) ----

    /// §2: keep only the last ≤ 2 consecutive Han characters of `text` (none: empty).
    pub fn set_left_context(&mut self, text: &str) {
        let k = context_key(text);
        self.left = if k == crate::learn::SENTINEL { String::new() } else { k };
    }

    /// §3: off by default. Turning it off drops pending learns; a span is learned only if the flag was
    /// on both when it was chosen and at commit.
    pub fn set_learning(&mut self, on: bool) {
        self.learning = on;
        if !on {
            self.fixed.iter_mut().for_each(|f| f.pre = None);
        }
    }

    /// §4: load `dir/learning.tsv` (memory pruned only; the file is tidied by the first write, which
    /// is always a full rewrite). On error the previous learner and store stay. `must_rewrite` and
    /// bit0 are left as they are: only a successful full rewrite or clear clears them.
    pub fn learning_open(&mut self, dir: &Path) -> Result<Opened, StoreError> {
        let (store, records, opened) = LearnStore::open(dir)?;
        self.learner = Learner::from_records(records);
        self.learner.prune(self.today());
        self.store = Some(store);
        self.last_full = None;
        self.appended = 0;
        Ok(opened)
    }

    /// §4: forget everything: memory, pending learns, files. Memory and pending learns go even when
    /// deleting the files fails. Without a store (no successful `learning_open`) it is an error: there
    /// is no file it could have deleted, and the shell must not show the clear as done.
    pub fn learning_clear(&mut self) -> Result<(), StoreError> {
        if self.store.is_some() {
            self.must_rewrite = true;
        }
        self.learner.clear();
        self.fixed.iter_mut().for_each(|f| f.pre = None);
        let store = self.store.as_ref().ok_or(StoreError::Io)?;
        store.clear()?;
        // The next write starts a new file from the header by the day rule, not by finding none.
        self.last_full = None;
        self.appended = 0;
        self.must_rewrite = false;
        self.write_failed = false;
        Ok(())
    }

    /// bit0: the last full rewrite of the learning file failed (§4).
    pub fn learning_status(&self) -> u32 {
        self.write_failed as u32
    }

    pub fn learner(&self) -> &Learner {
        &self.learner
    }

    /// Test hook (V3 section 10.2 step 1): false turns the "could a record match" gate off.
    pub fn set_scan_gate(&mut self, on: bool) {
        self.scan_gate = on;
    }

    /// Test hook (§12): ε of the global level.
    pub fn set_eps_global(&mut self, eps: f64) {
        self.eps_global = eps;
    }

    /// Test-purpose clock: day number to use instead of the local day.
    pub fn set_today(&mut self, day: Option<i64>) {
        self.today = day;
    }

    /// Test-purpose injection: the next commit's learning pass panics (§6.9).
    pub fn inject_learn_panic(&mut self) {
        self.learn_panic = true;
    }

    /// Test-purpose injection: the next ⌘⌫ forget panics right after removing the word from memory,
    /// before the file is rewritten (§6.13).
    pub fn inject_forget_panic(&mut self) {
        self.forget_panic = true;
    }

    fn today(&self) -> i64 {
        self.today.unwrap_or_else(local_day)
    }

    /// §4 write after a learning commit: append `touched` when allowed, else a full rewrite.
    /// Without a store only memory is pruned (to keep CAPACITY).
    fn persist(&mut self, touched: &[Record]) {
        let today = self.today();
        let Some(store) = &self.store else {
            self.learner.prune(today);
            return;
        };
        let append_ok = !self.must_rewrite && self.last_full == Some(today) && self.appended + touched.len() < JOURNAL_MAX;
        if append_ok && store.append(touched).is_ok() {
            self.appended += touched.len();
            return;
        }
        self.rewrite(false);
    }

    /// §4 full rewrite: prune, then replace the file. Success clears `must_rewrite` and bit0; a
    /// failure leaves `must_rewrite` set and sets bit0. No-op without a store.
    fn rewrite(&mut self, forgetting: bool) {
        let today = self.today();
        let Some(store) = &self.store else { return };
        self.must_rewrite = true;
        self.learner.prune(today);
        let r = if forgetting { store.save_forgetting(self.learner.records()) } else { store.save(self.learner.records()) };
        if r.is_ok() {
            self.must_rewrite = false;
            self.write_failed = false;
            self.last_full = Some(today);
            self.appended = 0;
        } else {
            self.write_failed = true;
        }
    }

    /// §1.2: runs after the commit text is known, inside its own `catch_unwind`: whatever fails here
    /// only costs this learn. `display` is the decoded composition text, without the unfinished symbols that the
    /// commit itself may carry (s3a rule 12a): the context keys are offsets into it.
    fn learn_commit(&mut self, display: &str) {
        if !self.learning || self.fixed.iter().all(|f| f.pre.is_none()) {
            return;
        }
        let _ = catch_unwind(AssertUnwindSafe(|| {
            if std::mem::take(&mut self.learn_panic) {
                panic!("injected learning panic");
            }
            let today = self.today();
            let plans: Vec<_> = self
                .fixed
                .iter()
                .filter_map(|f| {
                    let pre = f.pre.as_ref().filter(|p| **p != f.word && !self.is_punct(f.start))?;
                    let off: usize = (0..f.start).map(|i| self.token_width(i)).sum();
                    let before: String = display.chars().take(off).collect();
                    let ctx = f.key.clone().unwrap_or_else(|| context_key(&format!("{}{before}", self.left)));
                    Some((ctx, self.syls[f.start..f.end].to_vec(), f.word.clone(), pre.clone()))
                })
                .collect();
            let mut touched = Vec::new();
            for (ctx, reading, word, displaced) in &plans {
                touched.extend(self.learner.teach(ctx, reading, word, displaced, today));
            }
            // Nothing touched (e.g. a single character under "^", §12): no write, unless a full
            // rewrite is pending (a failed forget or rewrite). It is retried only by commits that reach
            // this point: learning on and at least one re-pick (the early return above); other commits
            // leave it pending.
            if !touched.is_empty() || self.must_rewrite {
                self.persist(&touched);
            }
        }));
    }

    /// S2c: read the model at `path` and `data_dir/overlay-add.tsv` (plus the pack rows `new` kept), build the capped lexicon with the
    /// shared constructor. Failure leaves the previous state. The composition display is not recomputed;
    /// the next change to it decodes with the new model.
    pub fn load_lm(&mut self, path: &Path) -> Result<(), EngineError> {
        let dir = self.data_dir.as_ref().ok_or(EngineError::LoadFailed)?;
        let overlay = capping_overlay(dir, self.pack_text.as_deref().unwrap_or(""))?;
        let demote = std::fs::read_to_string(dir.join("demote.tsv")).map_err(|_| EngineError::LoadFailed)?;
        let demote = Demote::parse(&demote).ok_or(EngineError::LoadFailed)?;
        let lm = Lm::load(path).map_err(|_| EngineError::LoadFailed)?;
        let capped = CappedLexicon::new(self.lex.clone(), &overlay, &lm, Some(&demote)).ok_or(EngineError::LoadFailed)?;
        // Built now, not at the first key: the index takes hundreds of ms (V3 engine contract section 1.2).
        capped.predict_index(&lm);
        self.lm = Some(LmState { lm: Arc::new(lm), capped: Arc::new(capped) });
        Ok(())
    }

    /// s3e §3: replace the punctuation alternatives with `table` (lines `mark\talt\talt…`, blank
    /// lines ignored, a repeated mark overrides the earlier line). On any invalid input nothing
    /// changes and `false` is returned. The composition is not recomputed.
    pub fn set_punctuation(&mut self, table: &str) -> bool {
        if table.len() > PUNCT_TABLE_MAX_BYTES {
            return false;
        }
        let lines: Vec<&str> = table.split('\n').filter(|l| !l.is_empty()).collect();
        if lines.is_empty() || lines.len() > PUNCT_TABLE_MAX_LINES {
            return false;
        }
        let mut map = HashMap::new();
        for line in lines {
            let mut fields = line.split('\t');
            let mut key = fields.next().unwrap_or("").chars();
            let (Some(mark), None) = (key.next(), key.next()) else { return false };
            let alts: Vec<String> = fields.map(str::to_string).collect();
            if alts.is_empty() || alts.iter().any(String::is_empty) {
                return false;
            }
            map.insert(mark, alts);
        }
        self.punct = map;
        true
    }

    /// Display chars of token `i`: 1 for a syllable, the fixed word's length for punctuation.
    fn token_width(&self, i: usize) -> usize {
        if !self.is_punct(i) {
            return 1;
        }
        self.fixed.iter().find(|f| f.start == i).map_or(1, |f| f.word.chars().count())
    }

    /// Test-purpose injection for `with_lexicon` engines: a prebuilt model and its capped lexicon
    /// (built by `CappedLexicon::new` from this engine's lexicon).
    pub fn set_lm(&mut self, lm: Arc<Lm>, capped: Arc<CappedLexicon>) {
        self.lm = Some(LmState { lm, capped });
    }

    /// Whether the demotion table applies (default on, remembered even before a model is loaded);
    /// recomputes the composition and returns the snapshot, like `set_profile`. On a decode failure the
    /// engine resets itself.
    pub fn set_demote(&mut self, on: bool) -> Result<Output, EngineError> {
        self.demote = on;
        self.clear_pred();
        if let Err(e) = self.refresh() {
            self.clear_all();
            return Err(e);
        }
        self.handled()
    }

    /// Whether the prediction row is computed (default on, V3 section 10.5). Off clears the row (an entered row is left);
    /// on recomputes it, so a row that fits section 1.1 shows at once. Returns the snapshot.
    pub fn set_prediction(&mut self, on: bool) -> Result<Output, EngineError> {
        self.predict_on = on;
        self.recompute_pred(); // clears first; returns empty while off
        self.handled()
    }

    /// Switch the profile (default chat; remembered even before a model is loaded), recompute the
    /// composition and return the snapshot. On a decode failure the engine resets itself.
    pub fn set_profile(&mut self, profile: Profile) -> Result<Output, EngineError> {
        self.profile = profile;
        self.clear_pred();
        if let Err(e) = self.refresh() {
            self.clear_all();
            return Err(e);
        }
        self.handled()
    }

    /// Where each token of the current best path starts (a word or a punctuation mark), from the composition's start.
    /// `recompute_pred` takes its long starts from it (V3 engine contract section 11); public so tests can tell a
    /// decoded word's start from a position inside a word.
    pub fn path_starts(&self) -> Vec<usize> {
        let mut pos = 0;
        self.path
            .iter()
            .map(|(w, _, is_punct, _)| {
                let s = pos;
                pos += if *is_punct { 1 } else { w.chars().count() };
                s
            })
            .collect()
    }

    /// Total score of the current best path as `lm.decode` scores one: every word (fixed words with
    /// their capped `lp_F`) adds `word(λ, previous, w, lp) − δ` (δ: the demotion of the entry, 0 when demotion is off), then `eos` of the last word. Punctuation
    /// splits it into sentences (s3d §4): the stretch before it closes with `eos`, the next starts from
    /// `<s>`, and the punctuation itself scores nothing. `None` without a model or without words.
    pub fn total_score(&self) -> Option<f64> {
        let st = self.lm.as_ref()?;
        let lam = self.profile.lambda();
        // S2h §1: the first sentence starts from the history word of the left context; `open` is whether
        // the current sentence has a word (the history word is not one, and its own eos is not scored).
        let mut prev = history(&self.left, &st.lm);
        let mut total = 0.0;
        let (mut any, mut open) = (false, false);
        for (w, lp, is_punct, delta) in &self.path {
            if *is_punct {
                if open {
                    total += st.lm.eos(lam, prev);
                }
                (prev, open) = ("<s>", false);
                continue;
            }
            total += st.lm.word(lam, prev, w, *lp) - delta;
            (prev, any, open) = (w, true, true);
        }
        any.then(|| if open { total + st.lm.eos(lam, prev) } else { total })
    }

    /// §6 reset: Commit returns the composition as shown, unfinished symbols included, like Enter (enter-pending
    /// contract §2; no learning here); both clear everything.
    pub fn reset(&mut self, mode: ResetMode) -> Output {
        let commit = if mode == ResetMode::Commit { self.shown().0 } else { String::new() };
        self.clear_all();
        self.view(true, commit)
    }

    pub fn key(&mut self, k: Key) -> Result<Output, EngineError> {
        self.pred_dirty = false;
        let mut r = self.dispatch(k);
        match &mut r {
            Err(_) => self.clear_all(),
            Ok(o) if std::mem::take(&mut self.pred_dirty) => {
                self.recompute_pred();
                (o.candidates, o.selected, o.total) = self.pred_view();
            }
            Ok(_) => {}
        }
        r
    }

    fn clear_pred(&mut self) {
        self.pred.clear();
        self.pred_sel = None;
    }

    /// The row as output fields (candidates, selected, total); `columns` and `first` stay 0.
    fn pred_view(&self) -> (Vec<String>, Option<usize>, u32) {
        (self.pred.iter().map(|p| p.word.clone()).collect(), self.pred_sel, self.pred.len() as u32)
    }

    /// V3 engine contract sections 1.2, 10.2 and 11: one `predict` per start (decoded word starts, positions inside words,
    /// the cursor), merged and reordered by what was learned.
    /// Empty while off, without a model, when the cursor is not at the end or the candidate window is open.
    fn recompute_pred(&mut self) {
        self.clear_pred();
        let n = self.syls.len();
        let (Some(st), true) = (self.lm.clone(), self.predict_on && self.cursor == n && self.cands.is_none()) else { return };
        let idx = st.capped.predict_index(&st.lm);
        let lam = self.profile.lambda();
        let pending: Vec<char> = self.pend.iter().flatten().copied().collect();
        // Section 11: the last two path tokens' starts, and the start of every earlier one within `PREDICT_BACK`, far to
        // near. Positions inside words within `PREDICT_BACK` come last, except inside a word the user fixed: choosing
        // such an item would take the user's choice (and its pending learn) apart.
        let starts = self.path_starts();
        let k = starts.len();
        let long_starts: Vec<usize> =
            starts.iter().enumerate().filter(|&(i, &s)| i + 2 >= k || s + PREDICT_BACK >= n).map(|(_, &s)| s).collect();
        let mid: Vec<usize> = (n.saturating_sub(PREDICT_BACK)..n)
            .filter(|&p| !long_starts.contains(&p) && !self.fixed.iter().any(|f| f.start < p && p < f.end))
            .collect();
        let disp: Vec<char> = self.display.chars().collect();
        let off = |i: usize| (0..i).map(|j| self.token_width(j)).sum::<usize>().min(disp.len());
        let (learned, today) = (!self.learner.is_empty(), self.today());
        let query = |s: usize| -> Vec<Pred> {
            if (s..n).any(|i| self.is_punct(i)) {
                return Vec::new();
            }
            let mut units: Vec<Unit> = self.syls[s..n].iter().filter_map(|y| unit_of_syllable(y)).collect();
            if units.len() != n - s {
                return Vec::new();
            }
            if !pending.is_empty() {
                units.push(Unit { chars: pending.clone(), done: false, tone: None });
            }
            if units.is_empty() {
                return Vec::new();
            }
            let before: String = disp[..off(s)].iter().collect();
            let key = context_key(&format!("{}{before}", self.left));
            let v = history(if key == crate::learn::SENTINEL { "" } else { &key }, &st.lm);
            let shown: String = disp[off(s)..off(n)].iter().collect();
            // A start no record can match scans 9 and weighs nothing: its row is the slice-1 one, cheaper.
            let gate = learned && (!self.scan_gate || self.learner.records().iter().any(|r| reading_matches(&units, &r.reading)));
            predict(idx, &st.lm, lam, v, &units, Mode::P, if gate { PREDICT_SCAN } else { PREDICT_MAX })
                .into_iter()
                .enumerate()
                .filter_map(|(i, (word, _, _, reading))| {
                    // Step 2: keep the slice-1 window and anything learned; the index counts before step 3 filters.
                    let weight = if gate { self.learned_weight(&key, &reading, &word, today) } else { 0.0 };
                    (i < PREDICT_MAX || weight > 0.0).then_some(Pred { word, reading, start: s, key: key.clone(), weight })
                })
                .filter(|p| p.word != shown)
                .collect()
        };
        // Path starts are strictly increasing (every token is at least one position wide), so no start repeats.
        let mut long: Vec<Pred> = long_starts.into_iter().flat_map(&query).collect();
        // Step 4: L keeps the first position of a word and the largest weight of its copies.
        let mut best: HashMap<String, f64> = HashMap::new();
        for p in &long {
            let w = best.entry(p.word.clone()).or_insert(0.0);
            *w = w.max(p.weight);
        }
        let mut words = HashSet::new();
        long.retain(|p| words.insert(p.word.clone()));
        long.iter_mut().for_each(|p| p.weight = best[&p.word]);
        // Step 5: learned items are exempt from the cap of 3 positions; the order inside head and tail is L's, and the
        // stable sort below puts the learned ones first, so a partition gives the same row as "learned, then L[0..3]".
        let (head, tail): (Vec<_>, Vec<_>) = long.into_iter().enumerate().partition(|(i, p)| p.weight > 0.0 || *i < PREDICT_LONG_CAP);
        let (head, tail) = (head.into_iter().map(|x| x.1), tail.into_iter().map(|x| x.1));
        let cursor_items = if pending.is_empty() { Vec::new() } else { query(n) };
        // Section 11: the positions inside words come last, far to near.
        let mid_items = mid.into_iter().flat_map(&query);
        let mut all: Vec<Pred> = head.chain(cursor_items).chain(tail).chain(mid_items).collect();
        all.sort_by(|a, b| b.weight.total_cmp(&a.weight)); // step 6: stable
        let mut words = HashSet::new();
        all.retain(|p| words.insert(p.word.clone())); // step 7
        all.truncate(PREDICT_MAX);
        self.pred = all;
    }

    /// Weight of `word` for `reading` under context `key` at the exact or last-character level; the global level
    /// counts 0 (s4-learning section 12: it never overrides what the model is sure of).
    fn learned_weight(&self, key: &str, reading: &[String], word: &str, today: i64) -> f64 {
        match self.learner.lookup(key, reading, today) {
            (Level::Global, _) => 0.0,
            (_, hits) => hits.iter().find(|h| h.0 == word).map_or(0.0, |h| h.1),
        }
    }

    /// Section 2: replace `syls[start..cursor]` by the item's reading, fix the word over it, move the cursor behind
    /// it. With learning on `pre` is `Some("")` (section 10.1): taught at commit like a re-pick, with nothing displaced.
    fn choose_pred(&mut self, i: usize) -> Result<Output, EngineError> {
        let Pred { word, reading, start, key, .. } = self.pred.swap_remove(i);
        let pre = self.learning.then(String::new);
        self.clear_pred();
        self.pend = [None; 3];
        let (m, end) = (reading.len(), self.cursor);
        self.fixed.retain(|f| !(f.start < end && start < f.end));
        self.syls.splice(start..end, reading);
        let key = pre.is_some().then_some(key);
        self.fixed.push(Fixed { start, end: start + m, word, pre, key });
        self.fixed.sort_by_key(|f| f.start);
        self.cursor = start + m;
        self.refresh()?;
        if self.syls.len() >= MAX_SYLLABLES {
            let commit = self.take_commit();
            return Ok(self.view(true, commit));
        }
        self.handled()
    }

    fn clear_all(&mut self) {
        self.left.clear();
        self.syls.clear();
        self.cursor = 0;
        self.pend = [None; 3];
        self.fixed.clear();
        self.display.clear();
        self.path.clear();
        self.cands = None;
        self.clear_pred();
    }

    fn pending(&self) -> String {
        self.pend.iter().flatten().collect()
    }

    /// The composition as shown: the display text with the unfinished symbols at the cursor, and the cursor after
    /// them in UTF-16 code units.
    fn shown(&self) -> (String, u32) {
        let chars: Vec<char> = self.display.chars().collect();
        // A syllable shows as one char (lexicon invariant); a punctuation token as its fixed word,
        // which can be longer (⋯⋯, s3e §3). Clamped in case the invariant ever breaks.
        let at = (0..self.cursor).map(|i| self.token_width(i)).sum::<usize>().min(chars.len());
        let mut text: String = chars[..at].iter().collect();
        text.push_str(&self.pending());
        let cursor_utf16 = text.encode_utf16().count() as u32;
        text.extend(chars[at..].iter());
        (text, cursor_utf16)
    }

    fn view(&self, handled: bool, commit: String) -> Output {
        let (preedit, cursor_utf16) = self.shown();
        let (candidates, selected, columns, first, total) = match &self.cands {
            Some(c) => {
                let (first, n) = c.window();
                let list = c.list[first..first + n].iter().map(|(w, _)| w.clone()).collect();
                let columns = if c.expanded { PAGE_SIZE as u32 } else { 0 };
                (list, Some(c.sel - first), columns, first as u32, c.list.len() as u32)
            }
            None => {
                let (list, sel, total) = self.pred_view();
                (list, sel, 0, 0, total)
            }
        };
        Output { handled, commit, preedit, cursor_utf16, candidates, selected, columns, first, total }
    }

    /// Recompute the display string: free segments decoded top-1, fixed words in between (§3.1).
    fn refresh(&mut self) -> Result<(), EngineError> {
        if let Some(st) = self.lm.clone() {
            return self.refresh_lm(&st);
        }
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

    /// S2c: each blank stretch is decoded with the bigram model. Its left context is the fixed word on
    /// its left (`<s>` if none); it closes with the transition into the fixed word on its right, or with
    /// the sentence end when there is none. Fixed words score with `lp_F`, the capped score under their reading.
    fn refresh_lm(&mut self, st: &LmState) -> Result<(), EngineError> {
        let lam = self.profile.lambda();
        // The clock (a libc time conversion) is read only when a record could use it.
        let today = if self.learner.is_empty() { 0 } else { self.today() };
        let mut lp_fixed = Vec::with_capacity(self.fixed.len());
        let mut delta_fixed = Vec::with_capacity(self.fixed.len());
        for f in &self.fixed {
            // Punctuation has no reading in the lexicon; 0.0 only keeps `path` aligned (s3d §4).
            let (lp, delta) = if self.is_punct(f.start) {
                (0.0, 0.0)
            } else {
                st.capped.best_lp_delta(&self.syls[f.start..f.end], &f.word).ok_or(EngineError::Internal)?
            };
            lp_fixed.push(lp);
            delta_fixed.push(if self.demote { delta } else { 0.0 });
        }
        let (mut out, mut path) = (String::new(), Vec::new());
        for gap in 0..=self.fixed.len() {
            let from = if gap == 0 { 0 } else { self.fixed[gap - 1].end };
            let right = self.fixed.get(gap);
            let to = right.map_or(self.syls.len(), |f| f.start);
            if from < to {
                // Punctuation is a sentence boundary, as in the counts the model was built from (s3d §4).
                // The first stretch (no fixed word on its left) starts from the left context (S2h §1).
                let prev = match gap.checked_sub(1).map(|g| &self.fixed[g]) {
                    Some(f) if !self.is_punct(f.start) => f.word.as_str(),
                    Some(_) => "<s>",
                    None => history(&self.left, &st.lm),
                };
                let end = match right {
                    Some(f) if !self.is_punct(f.start) => End::Next { word: &f.word, lp: lp_fixed[gap], delta: delta_fixed[gap] },
                    _ => End::Eos,
                };
                let before = format!("{}{out}", self.left);
                let learn = (!self.learner.is_empty()).then(|| Learn { learner: &self.learner, before: &before, today, eps_global: self.eps_global });
                let best = decode_segment_learned(
                    &st.capped, &self.syls[from..to], &st.lm, lam, prev, end, BEAM_S1, learn.as_ref(), self.demote,
                )
                .map_err(|_| EngineError::Internal)?;
                for (w, lp, delta) in &best.first().ok_or(EngineError::Internal)?.1 {
                    out.push_str(w);
                    path.push((w.to_string(), *lp, false, *delta));
                }
            }
            if let Some(f) = right {
                out.push_str(&f.word);
                path.push((f.word.clone(), lp_fixed[gap], self.is_punct(f.start), delta_fixed[gap]));
            }
        }
        self.display = out;
        self.path = path;
        Ok(())
    }

    fn handled(&self) -> Result<Output, EngineError> {
        Ok(self.view(true, String::new()))
    }
    fn passthrough(&self, commit: String) -> Result<Output, EngineError> {
        Ok(self.view(false, commit))
    }
    /// Commit the whole composition as shown, unfinished symbols included (s3a rule 12a), and clear all state. Learning
    /// sees the decoded text only.
    fn take_commit(&mut self) -> String {
        let (shown, _) = self.shown();
        let s = std::mem::take(&mut self.display);
        self.learn_commit(&s);
        self.clear_all();
        shown
    }

    /// Rules 12a, 12b, 19, 19a: Enter commits what is shown; Shift+Enter then passes the key on (Apple Zhuyin, measured
    /// 2026-10-09: it commits and the app gets the line break).
    fn commit_enter(&mut self, m: u32) -> Result<Output, EngineError> {
        let commit = self.take_commit();
        Ok(self.view(m & MOD_SHIFT == 0, commit))
    }

    fn dispatch(&mut self, k: Key) -> Result<Output, EngineError> {
        let m = k.modifiers;
        let is_char = k.kind == KeyKind::Char;
        // V3 section 10.3: ⌘⌫ while the prediction row is entered forgets the selected item (before rule 1).
        if let (KeyKind::Backspace, true, Some(sel)) = (k.kind, m & MOD_COMMAND != 0, self.pred_sel) {
            let Pred { word, reading, .. } = &self.pred[sel];
            let (word, reading) = (word.clone(), reading.clone());
            self.forget_entry(&reading, &word);
            self.refresh()?; // the free segments decode without the forgotten record, as after the candidate window's ⌘⌫
            self.recompute_pred();
            if !self.pred.is_empty() {
                self.pred_sel = Some(self.pred.iter().position(|p| p.word == word).unwrap_or(0));
            }
            return self.handled();
        }
        // §1.5: ⌘⌫ with candidates open forgets the highlighted word (before rule 1 passes ⌘ keys on).
        if k.kind == KeyKind::Backspace && m & MOD_COMMAND != 0 && self.cands.is_some() {
            self.forget_highlighted()?;
            return self.handled();
        }
        let ctrl_bs = is_char && k.ch == '\\' && m == MOD_CONTROL;
        // 1: pass through, no state change.
        if m & (MOD_OPTION | MOD_COMMAND | MOD_CAPSLOCK) != 0 || (m & MOD_CONTROL != 0 && !ctrl_bs) {
            return self.passthrough(String::new());
        }
        // V3 section 3: the entered prediction row, between rules 1 and 2. Any other key leaves it and goes on.
        if let Some(sel) = self.pred_sel {
            let len = self.pred.len();
            let digit = (is_char && m == 0 && ('1'..='9').contains(&k.ch)).then(|| k.ch as usize - '1' as usize);
            match (k.kind, digit) {
                (KeyKind::Char, Some(d)) => {
                    if d < len {
                        return self.choose_pred(d);
                    }
                    return self.handled();
                }
                (KeyKind::Left, _) => self.pred_sel = Some(sel.saturating_sub(1)),
                (KeyKind::Right, _) => self.pred_sel = Some((sel + 1).min(len - 1)),
                (KeyKind::Tab, _) if m & MOD_SHIFT == 0 => self.pred_sel = Some((sel + 1).min(len - 1)),
                (KeyKind::Tab, _) | (KeyKind::Esc, _) => self.pred_sel = None,
                (KeyKind::Enter, _) => return self.choose_pred(sel),
                _ => self.pred_sel = None,
            }
            if matches!(k.kind, KeyKind::Left | KeyKind::Right | KeyKind::Tab | KeyKind::Esc) {
                return self.handled();
            }
        }
        // Section 1.1: every other change clears the row; the rules below that recompute or keep it say so.
        let old = std::mem::take(&mut self.pred);
        if k.kind == KeyKind::Tab && m == 0 && !old.is_empty() && self.cands.is_none() {
            self.pred = old;
            self.pred_sel = Some(0);
            return self.handled();
        }
        // 2: punctuation.
        let punct = if ctrl_bs {
            Some('、')
        } else if is_char && m == MOD_SHIFT {
            SHIFT_PUNCT.iter().find(|(c, _)| *c == k.ch).map(|(_, p)| *p)
        } else if is_char && m == 0 && self.layout.symbol_of(k.ch).is_none() && self.layout.tone_of(k.ch).is_none() {
            PLAIN_PUNCT.iter().find(|(c, _)| *c == k.ch).map(|(_, p)| *p)
        } else {
            None
        };
        if let Some(p) = punct {
            // s3d §1: into the composition at the cursor, not committed.
            self.pend = [None; 3];
            self.cands = None;
            return self.insert_token(format!("{PUNCT_PREFIX}{p}"), Some(p.to_string()));
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
        let has_pending = self.pend.iter().any(Option::is_some);
        if has_pending {
            // 9-13
            if let Some((col, sym)) = zy {
                self.pend[col] = Some(sym);
                self.pred_dirty = true;
            } else if let Some(t) = tone {
                return self.finish_syllable(t, old);
            } else if k.kind == KeyKind::Backspace {
                // Row 11: the last symbol in display order (final, then medial, then initial), as Apple Zhuyin and
                // McBopomofo do (measured 2026-10-06: ㄉㄨㄟ, ㄅ replaces ㄉ, then Backspace gives ㄅㄨ, then ㄅ).
                if let Some(col) = (0..3).rev().find(|&c| self.pend[c].is_some()) {
                    self.pend[col] = None;
                }
                self.pred_dirty = true;
            } else if k.kind == KeyKind::Esc {
                self.pend = [None; 3];
            } else if k.kind == KeyKind::Enter {
                return self.commit_enter(m); // 12a, 12b
            } else {
                self.pred = old; // the key does nothing
            }
            return self.handled();
        }
        // 14
        if let Some((col, sym)) = zy {
            self.pend[col] = Some(sym);
            self.pred_dirty = true;
            return self.handled();
        }
        if self.syls.is_empty() {
            // 22a (user report 2026-10-05): a tone key on an empty composition types its mark
            // (ˊ ˇ ˋ ˙) as Apple Zhuyin does, instead of passing the digit on. Unlike Apple, which
            // commits the mark at once, it goes into the composition like punctuation (s3d), so
            // Backspace can still take it back and Enter sends it with the sentence (user's choice).
            if let (KeyKind::Char, Some(t)) = (k.kind, tone) {
                let mark = TONE_MARKS[t];
                return self.insert_token(format!("{PUNCT_PREFIX}{mark}"), Some(mark.to_string()));
            }
            return self.passthrough(String::new()); // 22
        }
        let n = self.syls.len();
        match k.kind {
            KeyKind::Space | KeyKind::Down => self.open_candidates(),
            KeyKind::Up => self.pred = old,
            KeyKind::Char if tone.is_some() => self.pred = old,
            KeyKind::Right | KeyKind::End if self.cursor == n => self.pred = old, // nothing moves
            KeyKind::Left => self.cursor = self.cursor.saturating_sub(1),
            KeyKind::Right => self.cursor = (self.cursor + 1).min(n),
            KeyKind::Home => self.cursor = 0,
            KeyKind::End => self.cursor = n,
            KeyKind::Backspace if self.cursor > 0 => {
                self.cursor -= 1;
                self.remove_syllable(self.cursor)?;
            }
            KeyKind::Delete if self.cursor < n => self.remove_syllable(self.cursor)?,
            KeyKind::Backspace | KeyKind::Delete => self.pred = old,
            KeyKind::Enter => return self.commit_enter(m), // 19, 19a
            KeyKind::Esc => self.clear_all(),
            _ => {
                let commit = self.take_commit(); // 21
                return self.passthrough(commit);
            }
        }
        self.handled()
    }

    /// Rules 3-8 (s3a §3, s3b2 §8.2). `Ok(true)` = consumed; `Ok(false)` = candidates closed, key
    /// continues at rule 9.
    fn candidate_key(&mut self, k: Key) -> Result<bool, EngineError> {
        let Some(c) = &mut self.cands else { return Ok(false) };
        let (len, sel, cols) = (c.list.len(), c.sel, PAGE_SIZE);
        let digit = (k.kind == KeyKind::Char && k.modifiers == 0 && ('1'..='9').contains(&k.ch))
            .then(|| k.ch as usize - '1' as usize);
        if c.expanded {
            let (row, col, last_row) = (sel / cols, sel % cols, (len - 1) / cols);
            // Next row, same column; a short last row ends at its last candidate.
            let below = ((row + 1) * cols + col).min(len - 1);
            match (k.kind, digit) {
                (KeyKind::Char, Some(d)) => {
                    let idx = row * cols + d;
                    if idx < len {
                        self.choose(idx)?;
                    }
                }
                (KeyKind::Down, _) => c.sel = if row == last_row { sel } else { below },
                (KeyKind::Space, _) => c.sel = if row == last_row { col } else { below },
                // Absolute row 0 collapses; any other row moves up, and `scroll` brings the top row
                // up when the selection leaves it (s3b2 §9).
                (KeyKind::Up, _) if row == 0 => {
                    c.expanded = false;
                    c.top = 0;
                }
                (KeyKind::Up, _) => c.sel = sel - cols,
                (KeyKind::Left, _) => c.sel = sel.saturating_sub(1),
                (KeyKind::Right, _) => c.sel = (sel + 1).min(len - 1),
                (KeyKind::Enter, _) => self.choose(sel)?,
                (KeyKind::Esc | KeyKind::Backspace, _) => self.cands = None,
                _ => {
                    self.cands = None;
                    return Ok(false);
                }
            }
            if let Some(c) = &mut self.cands {
                if c.expanded {
                    c.scroll();
                }
            }
            return Ok(true);
        }
        let page_start = sel / PAGE_SIZE * PAGE_SIZE;
        let next_page = (page_start + PAGE_SIZE < len).then_some(page_start + PAGE_SIZE);
        match (k.kind, digit) {
            (KeyKind::Char, Some(d)) => {
                let idx = page_start + d;
                if idx < len {
                    self.choose(idx)?;
                }
            }
            // Left/right move the selection (user report 2026-10-04: paging on them was wrong); down
            // expands into the grid, as in the system Zhuyin (s3b2 §8.1 b-4).
            (KeyKind::Up | KeyKind::Left, _) => c.sel = sel.saturating_sub(1),
            (KeyKind::Right, _) => c.sel = (sel + 1).min(len - 1),
            (KeyKind::Down, _) => {
                c.expanded = true;
                c.top = sel / PAGE_SIZE;
            }
            (KeyKind::Space, _) => c.sel = next_page.unwrap_or(0),
            (KeyKind::Enter, _) => self.choose(sel)?,
            (KeyKind::Esc | KeyKind::Backspace, _) => self.cands = None,
            _ => {
                self.cands = None;
                return Ok(false);
            }
        }
        Ok(true)
    }

    /// s3b2 §8.2 mouse pick: `index` is a position in the last output's `candidates`: the candidate window's, or, with
    /// the window closed, the V3 prediction row's (entered or not). `Ok(None)` when there is neither or `index` is
    /// outside that output (state unchanged).
    pub fn pick(&mut self, index: usize) -> Result<Option<Output>, EngineError> {
        let Some(c) = &self.cands else {
            // V3 section 4: a click on the prediction row selects at once, entered or not.
            if index >= self.pred.len() {
                return Ok(None);
            }
            let r = self.choose_pred(index);
            if r.is_err() {
                self.clear_all();
            }
            return r.map(Some);
        };
        let (first, n) = c.window();
        if index >= n {
            return Ok(None);
        }
        let r = self.choose(first + index).and_then(|()| self.handled());
        if r.is_err() {
            self.clear_all();
        }
        r.map(Some)
    }

    /// §2: complete the pending syllable with tone 0..=4 (0 = space = tone 1).
    fn finish_syllable(&mut self, tone: usize, old: Vec<Pred>) -> Result<Output, EngineError> {
        let mut syl = self.pending();
        // tone index 1..=4 -> marks[1..=4]; space (0) -> unmarked tone 1
        syl.push_str(TONE_MARKS[tone]);
        if self.lex.entries(std::slice::from_ref(&syl)).is_empty() {
            self.pred = old; // section 1.1: a syllable the lexicon lacks changes nothing
            return self.handled();
        }
        self.pend = [None; 3];
        self.pred_dirty = true;
        self.insert_token(syl, None)
    }

    /// Insert one token (a syllable, or punctuation with its fixed word) at the cursor, shift the
    /// fixed words on its right, recompute; at MAX_SYLLABLES tokens commit everything (s3d §1).
    fn insert_token(&mut self, reading: String, fixed_word: Option<String>) -> Result<Output, EngineError> {
        let c = self.cursor;
        self.fixed.retain_mut(|f| {
            if f.end <= c {
                true
            } else if f.start >= c {
                f.start += 1;
                f.end += 1;
                f.key = None; // the user changed the text before it: record under the key at the commit (section 10.1)
                true
            } else {
                false
            }
        });
        self.syls.insert(c, reading);
        if let Some(word) = fixed_word {
            self.fixed.push(Fixed { start: c, end: c + 1, word, pre: None, key: None });
            self.fixed.sort_by_key(|f| f.start);
        }
        self.cursor += 1;
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
                f.key = None; // the user changed the text before it (section 10.1)
                true
            } else {
                false
            }
        });
        self.refresh()
    }

    fn is_punct(&self, i: usize) -> bool {
        self.syls[i].starts_with(PUNCT_PREFIX)
    }

    /// §3.1 candidate range: readings ending at the cursor (or starting at 0 when the cursor is 0),
    /// within the run of syllables that touches it; punctuation bounds the run (s3d §3). An empty
    /// run lists nothing, so space and down are ignored.
    fn open_candidates(&mut self) {
        let a = self.cursor;
        let avail = if a == 0 {
            (0..self.syls.len()).take_while(|&i| !self.is_punct(i)).count()
        } else {
            (0..a).rev().take_while(|&i| !self.is_punct(i)).count()
        };
        let mut seen = HashSet::new();
        let mut list = Vec::new();
        // s3e §3: right after punctuation (or punctuation first at cursor 0), list the typed mark,
        // then its alternatives.
        let touching = if a == 0 { 0 } else { a - 1 };
        if avail == 0 && touching < self.syls.len() && self.is_punct(touching) {
            let typed = self.syls[touching][PUNCT_PREFIX.len()..].to_string();
            let alts = typed.chars().next().and_then(|c| self.punct.get(&c)).cloned().unwrap_or_default();
            let mut listed = HashSet::new();
            for w in std::iter::once(typed).chain(alts) {
                if listed.insert(w.clone()) {
                    list.push((w, 1));
                }
            }
            self.cands = Some(Cands::new(list));
            return;
        }
        for l in (1..=self.lex.max_len.min(avail)).rev() {
            let key = if a == 0 { &self.syls[..l] } else { &self.syls[a - l..a] };
            for (w, _) in self.lex.entries(key) {
                if seen.insert(w) {
                    list.push((w.to_string(), l));
                }
            }
        }
        if !list.is_empty() {
            self.cands = Some(Cands::new(list));
        }
    }

    /// S4 §1.2: what to remember as the text before this pick, `None` for punctuation. Re-picking the
    /// exact span of an earlier pick keeps that pick's original text.
    fn pre_pick(&self, start: usize, end: usize) -> Option<String> {
        if self.is_punct(start) {
            return None;
        }
        if let Some(f) = self.fixed.iter().find(|f| f.start == start && f.end == end && f.pre.as_ref().is_some_and(|p| !p.is_empty())) {
            return f.pre.clone();
        }
        let off: usize = (0..start).map(|i| self.token_width(i)).sum();
        let len: usize = (start..end).map(|i| self.token_width(i)).sum();
        Some(self.display.chars().skip(off).take(len).collect())
    }

    /// §1.5: drop the highlighted candidate's records for its reading (all keys), then re-decode.
    /// With a store the file is always fully rewritten (§4): a record pruned from memory on load can
    /// still be in the file, so "memory changed" says nothing about the file. `must_rewrite` is set
    /// first, so a panic or failure part way makes the next write a full rewrite too.
    fn forget_highlighted(&mut self) -> Result<(), EngineError> {
        let Some(c) = &self.cands else { return Ok(()) };
        let Some((word, l)) = c.list.get(c.sel).cloned() else { return Ok(()) };
        let (start, end) = if self.cursor == 0 { (0, l) } else { (self.cursor - l, self.cursor) };
        if self.is_punct(start) {
            return Ok(());
        }
        let reading = self.syls[start..end].to_vec();
        self.forget_entry(&reading, &word);
        self.refresh()
    }

    /// Forget (reading, word) everywhere: pending learns of it are dropped and its records go, then the file is
    /// rewritten. Shared by the candidate window's and the prediction row's ⌘⌫.
    fn forget_entry(&mut self, reading: &[String], word: &str) {
        if self.store.is_some() {
            self.must_rewrite = true;
        }
        // A pending learn of the same word would teach it again at the next commit and append it
        // back (§1.5), as learning_clear's pending-learn drop prevents for clear.
        let syls = &self.syls;
        for f in self.fixed.iter_mut() {
            if f.word == word && syls[f.start..f.end] == *reading {
                f.pre = None;
            }
        }
        self.learner.forget(reading, word);
        if std::mem::take(&mut self.forget_panic) {
            panic!("injected forget panic");
        }
        self.rewrite(true);
    }

    /// Fix candidate `idx` over its range, close candidates, recompute.
    fn choose(&mut self, idx: usize) -> Result<(), EngineError> {
        let Some(c) = self.cands.take() else { return Ok(()) };
        let Some((word, l)) = c.list.get(idx).cloned() else { return Ok(()) };
        let (start, end) = if self.cursor == 0 { (0, l) } else { (self.cursor - l, self.cursor) };
        // Choosing again the word a prediction pick fixed over this exact span keeps that pending learn (section 10.1).
        let again = self.fixed.iter().find(|f| f.start == start && f.end == end && f.word == word && f.pre.as_deref() == Some(""));
        let (pre, key) = match again {
            Some(f) if self.learning => (f.pre.clone(), f.key.clone()),
            _ => (self.learning.then(|| self.pre_pick(start, end)).flatten(), None),
        };
        self.fixed.retain(|f| !(f.start < end && start < f.end));
        // A candidate pick changes the text before every fixed word to its right: their prediction keys no longer
        // describe what the user sees, so those records fall back to the key at the commit (section 10.1).
        for f in self.fixed.iter_mut().filter(|f| f.start >= end) {
            f.key = None;
        }
        self.fixed.push(Fixed { start, end, word, pre, key });
        self.fixed.sort_by_key(|f| f.start);
        self.refresh()
    }
}
