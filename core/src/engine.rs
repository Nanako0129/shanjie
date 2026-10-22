//! S3a key engine (docs/contracts/s3a.md): key events in, preedit / commit / candidates out.
//! R2: no input text in errors or panics; types holding input text do not derive Debug.

use crate::colloquial::{self, Colloquial, ReadingChannel};
use crate::learn::{context_key, digit_of, english_of, english_or_space, is_han, is_space_unit, local_day, newword_score, next_key, record_key, Learner, Level, Record, NEWWORD, NEWWORD_BREAK, NEWWORD_DIGITS, NEWWORD_MAX, NEWWORD_MIN, NEWWORD_SHADOW, PUNCT_PREFIX};
use crate::learn_store::{LearnStore, Opened, StoreError, JOURNAL_MAX};
use crate::predict::{predict, predict_digits, reading_matches_in, unit_of_syllable, Mode, Unit};
use crate::lm::{decode_segment_learned, history, CappedLexicon, Demote, End, Learn, Lm, Profile};
use crate::trigram::TriMemo;
use crate::digits::DigitTable;
mod digit_layer;
use digit_layer::{DRun, DigitAlt, DigitCache, DigitEvent};
use crate::tinylm::{RerankLevel, TinyLm, TOPK};
use crate::{decode_beam, Lexicon, NoLearning, BEAM_S1};
use std::collections::{HashMap, HashSet};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

/// S7c-2a section 2.3: one counter for the whole process, so a job of one engine never matches another engine, nor an engine
/// built later at the same address.
static GENERATION: AtomicU64 = AtomicU64::new(1);

fn next_generation() -> u64 {
    GENERATION.fetch_add(1, Ordering::Relaxed)
}

/// One free stretch of a rerank job (S7c-2a section 2.2). Owned data only.
struct Stretch {
    /// Index in `Engine::path` of the stretch's first word.
    at: usize,
    /// The first `TOPK` paths of the n-best: (word, lp, demotion delta, syllables), as `path` holds them. `paths[0]` is what `path` holds at
    /// `at..at + len` when the job is made; apply checks that it still does (section 2.3, revision 1).
    paths: Vec<Vec<(String, f64, f64, usize)>>,
    /// Their n-gram scores.
    ng: Vec<f64>,
    /// The stretch's syllables, one per character of every path (what the reading-channel term cuts over the paths' words); empty when the job
    /// does not read the term.
    syls: Vec<String>,
    /// The shown text between the previous stretch of the job (or the composition's start) and this one: fixed words, and the
    /// n-gram text of stretches left out of the job.
    gap: String,
}

/// What the small model has to rerank after one change of the composition (S7c-2a section 2.2): only owned data and the model, never
/// a borrow of the engine, so it can run on another thread and outlive the engine. Holds input text, so no `Debug`.
pub struct RerankJob {
    generation: u64,
    model: Arc<TinyLm>,
    /// The profile's (alpha, tau, delta) at the engine's rerank level when the job was made (rerank-strength section 2.2): an own
    /// Copy value, so a later level or profile change never reaches a job already made (it is stale anyway).
    params: (f64, f64, f64),
    /// The colloquial term (docs/contracts/colloquial-term.md section 2.2), decided when the job was made: `Some` only for chat (any
    /// strength) and the small model the term was measured with. `None`: the plain `rerank` with `params`.
    colloquial: Option<Arc<Colloquial>>,
    /// The engine's rerank level when the job was made (docs/contracts/colloquial-levels.md): the colloquial and reading-channel terms use that
    /// level's (alpha, tau, delta), and their fallback the header triple of that level. Copy, like `params`.
    level: RerankLevel,
    /// The engine's lexicon, `Some` only where the reading-channel term applies (docs/contracts/reading-channel.md): when `colloquial` is
    /// `Some` and the engine's reading channel is on (the default; `Engine::set_reading_channel`).
    channel_lex: Option<Arc<Lexicon>>,
    /// The engine's left context when the job was made.
    left: String,
    stretches: Vec<Stretch>,
}

/// One stretch's choice: index into the job's paths, and that path when it is not the first.
struct Choice {
    at: usize,
    /// The words `path` must still hold at `at..` (the n-gram first path the job was made from).
    expect: Vec<String>,
    pick: usize,
    words: Vec<(String, f64, f64, usize)>,
    /// The context key the stretch was scored with.
    key: String,
}

/// The output of `RerankJob::run`, for `Engine::apply_rerank`. Holds input text, so no `Debug`.
pub struct RerankResult {
    generation: u64,
    choices: Vec<Choice>,
}

impl RerankJob {
    /// Section 2.4: `TinyLm::rerank` on every stretch in order; a candidate longer than the model's context keeps the first.
    /// Revision 1: a stretch's context key is `context_key` of the left context and the text before it as it will be shown,
    /// i.e. with the earlier stretches' chosen candidates (one stretch: the n-gram text before it, as before).
    pub fn run(&self) -> RerankResult {
        let mut before = self.left.clone();
        let choices = self
            .stretches
            .iter()
            .map(|s| {
                before.push_str(&s.gap);
                let key = context_key(&before);
                let surfaces: Vec<String> = s.paths.iter().map(|p| p.iter().map(|w| w.0.as_str()).collect()).collect();
                let cands: Vec<&str> = surfaces.iter().map(String::as_str).collect();
                let pick = match &self.colloquial {
                    Some(co) => {
                        let words: Vec<Vec<&str>> = s.paths.iter().map(|p| p.iter().map(|w| w.0.as_str()).collect()).collect();
                        let rc = self.channel_lex.as_deref().map(|lex| ReadingChannel { lex, syls: &s.syls });
                        self.model.rerank_colloquial(&s.ng, &cands, &words, &key, co, rc, self.level)
                    }
                    None => self.model.rerank(&s.ng, &cands, &key, self.params),
                }
                .map_or(0, |r| r.0);
                let pick = if pick < s.paths.len() { pick } else { 0 };
                before.push_str(&surfaces[pick]);
                let words = if pick == 0 { Vec::new() } else { s.paths[pick].clone() };
                let expect = s.paths[0].iter().map(|w| w.0.clone()).collect();
                Choice { at: s.at, expect, pick, words, key }
            })
            .collect();
        RerankResult { generation: self.generation, choices }
    }

    /// The most characters any candidate of any stretch has (composition-cap: at most `ctx - 3`, so none ends in `TooLong`).
    pub fn max_candidate_chars(&self) -> usize {
        self.stretches.iter().flat_map(|s| &s.paths).map(|p| p.iter().map(|w| w.0.chars().count()).sum()).max().unwrap_or(0)
    }

    /// Test hook (the FFI panic test): every candidate's text, so an injected panic payload carries input text.
    #[cfg(test)]
    pub(crate) fn texts(&self) -> String {
        self.stretches.iter().flat_map(|s| s.paths.iter().flatten().map(|w| w.0.as_str())).collect()
    }
}

impl RerankResult {
    /// The chosen index of each stretch, in composition order (0 = the n-gram first).
    pub fn picks(&self) -> Vec<usize> {
        self.choices.iter().map(|c| c.pick).collect()
    }

    /// The context key each stretch was scored with, in composition order (holds input text: never log it).
    pub fn keys(&self) -> Vec<String> {
        self.choices.iter().map(|c| c.key.clone()).collect()
    }
}

// Section 2.5 (F8): the thread guarantees come from the types themselves, never from an `unsafe impl`.
const _: () = {
    fn send<T: Send>() {}
    fn sync<T: Sync>() {}
    #[allow(dead_code)]
    fn assertions() {
        send::<TinyLm>();
        sync::<TinyLm>();
        send::<RerankJob>();
        send::<RerankResult>();
    }
};

pub const MOD_SHIFT: u32 = 1;
pub const MOD_CONTROL: u32 = 2;
pub const MOD_OPTION: u32 = 4;
pub const MOD_COMMAND: u32 = 8;
pub const MOD_CAPSLOCK: u32 = 16;
/// Most units (syllables, punctuation, English / digit tokens, space units) a composition holds; the one that makes this many sends
/// everything (s3d §1). 128 since docs/contracts/composition-cap.md: past the small model's window (`TinyLm::ctx`), the head of a
/// long free stretch is frozen (`cap_refreeze`) so the rerank still sees a stretch that fits.
pub const MAX_SYLLABLES: usize = 128;
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
/// Most unfinished units the abbreviation composer holds (V3 engine contract section 12.2). The longest names in the ACG pack
/// are 10 characters and an abbreviation only helps up to the longest word; past this an occupied column replaces (rule 9).
pub const ABBR_MAX_UNITS: usize = 10;
/// Items in the prediction row.
pub const PREDICT_MAX: usize = PAGE_SIZE;
/// Items asked of `predict` for a start that some learned record could match (V3 engine contract section 10.2 step 1).
/// A taught situation's items sit in the first `PREDICT_MAX`, a variant of it up to this far. Any other start asks for
/// `PREDICT_MAX` only, so with an empty learner the row is the slice-1 row bit for bit. The added time of 27 over 9
/// is only measured as part of the whole row with a 50,000-record store (research log 2026-10-08), not by itself.
pub const PREDICT_SCAN: usize = 27;
/// zhen-mixed section 2.2: the shadow holds at most this many keys; the next one empties it.
pub const SHADOW_MAX: usize = 32;
/// zhen-mixed section 2.6: the word list's limits (bytes, lines) and its word length range.
const ENGLISH_MAX_BYTES: u64 = 4 << 20;
const ENGLISH_MAX_LINES: usize = 200_000;
const ENGLISH_LEN: std::ops::RangeInclusive<usize> = 3..=32;

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
    /// Mark (ˊˇˋ˙) of a tone key; `None` for any other key (Space, tone 1, is the caller's).
    pub(crate) fn key_tone_mark(self, key: char) -> Option<char> {
        self.tone_of(key).and_then(|i| TONE_MARKS[i].chars().next())
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
    /// Only the vertical candidate window takes these (candidate-vertical contract section 2.2); the shell sends them
    /// only then. Anywhere else they pass through and change nothing.
    PageUp,
    PageDown,
}

impl KeyKind {
    /// ABI code (1..=15) to kind.
    pub fn from_code(code: u32) -> Option<KeyKind> {
        use KeyKind::*;
        [Char, Space, Enter, Backspace, Delete, Esc, Left, Right, Up, Down, Home, End, Tab, PageUp, PageDown]
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
    /// Candidate-vertical contract section 2.2: 0 horizontal (or no candidates), 1 the vertical candidate window (fixed
    /// when it opened), 2 the vertical prediction row, entered or not (it follows the setting on every output, section 2.4).
    pub vertical: u32,
    /// Zhen-inline section 1.3f: the open English run in `preedit`, start and length in UTF-16 code units; both 0 when no run
    /// is open.
    pub english_run_utf16_start: u32,
    pub english_run_utf16_len: u32,
}

/// One word of the displayed best path.
struct PathWord {
    word: String,
    lp: f64,
    is_punct: bool,
    delta: f64,
    /// Syllables the word covers (punctuation is 1).
    syllables: usize,
}

/// composition-cap §1.2: the window W (characters) when no small model is loaded, so the head freeze does not depend on one: the shipping
/// model's `ctx` 64 minus 3. With a model loaded W is its `ctx - 3`.
const CAP_WINDOW_NO_MODEL: usize = 61;
/// composition-cap §1.2 and §7: once a free stretch is longer than W the freeze goes on down to W - H characters, so a head that has left the
/// window is not decoded again and only about one key in H decodes twice (measured: with H = 0 almost every key past W froze one more word and
/// decoded twice, 26 ms against 13 to 17 expected).
const CAP_BUFFER: usize = 16;

/// composition-cap §1.2 step 3 (revision 2): a word that was not frozen before the key is not frozen when it ends less than this many syllables
/// before the longest common prefix of the syllables before and after the key, i.e. right at the edit. Typing in the middle of a long stretch
/// would otherwise freeze the syllable just typed as a single character on the next key (Home, ㄉㄧㄢˋ, ㄋㄠˇ showed 店腦), and a word of
/// several syllables could not form any more. Four is the longest word the typing is likely still completing (`PREDICT_BACK` is five for
/// the prediction row). Words that were already frozen are put back whatever their distance (release-then-refreeze, tests 3 and 4).
const CAP_EDGE: usize = 4;

/// What `cap_snapshot` keeps of the state before a key (composition-cap §1.2 step 1).
struct CapSnap {
    /// Each word of `path` with the syllables it covers.
    path: Vec<(String, usize)>,
    syls: Vec<String>,
    generation: u64,
    /// The ranges of the frozen words of the composition before the key (the pieces of a Backspace included).
    frozen: Vec<(usize, usize)>,
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
    /// Fixed by the engine, not chosen by the user (repick-freeze): the words outside a candidate re-pick (section 1) or a
    /// prediction pick (section 13), and the pieces a Backspace leaves of a deleted word (section 11). Never learned.
    /// Released when a newly typed syllable merges with it into a lexicon word (section 6.1; the merged word decodes
    /// freely since section 11) and when the candidate window's ⌘⌫ forgets an entry in effect over a span it overlaps
    /// (sections 7.2, 9); the Backspace pieces also when any syllable is typed (`split_off`, section 15).
    frozen: bool,
    /// A frozen single character a Backspace or Delete left of the word it deleted from (section 11). Any newly typed
    /// syllable drops all of these before it decodes (section 15); nothing else does.
    split_off: bool,
    /// The synthetic sentence-start key (`^>` + the next reading) the displaced word was shown under when this candidate
    /// pick was made, when the span's context key was `^`. The commit halves the displaced word there too if the key
    /// at the commit differs (the user may have typed on after the pick). `None` otherwise.
    pick_key: Option<String>,
    /// zhen-inline section 1.3c: for an English token made by an auto-keep, the unfinished syllable (tone 1) that was on the
    /// columns when it was made, so the token can be turned back into that Chinese syllable. `None` for every other fixed word.
    zh: Option<String>,
    /// New-word B §2.5: the new words (reading, word) of the path this pick displaced, chosen while learning was on;
    /// each is halved at a commit with learning on.
    displaced_new: Vec<(Vec<String>, String)>,
}

impl Fixed {
    /// A word the engine fixed on its own (repick-freeze): not learned, not a user choice.
    fn frozen(start: usize, end: usize, word: String) -> Self {
        Fixed { start, end, word, pre: None, key: None, pick_key: None, zh: None, frozen: true, split_off: false, displaced_new: Vec::new() }
    }
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
    /// Candidate-vertical contract section 2.2: the orientation at the moment the window opened. A vertical window
    /// never expands; `first` is the list position of its first visible row.
    vertical: bool,
    first: usize,
    /// zhen-mixed section 2.3: the English window (items have syllable length 0, except an optional Chinese last item of 1).
    english: bool,
    /// zhen-inline section 1.3c: the window of an English token that an auto-keep made; its last item is the Chinese
    /// syllable the keys typed, and this is that syllable's reading (the token's reading is replaced by it when it is chosen).
    zh: Option<String>,
    /// Predict-down contract B.3.1: the prediction row's items the window opened with. They are the first `rows.len()`
    /// entries of `list` (the length field of a list entry cannot mark them: the English window uses 0). Choosing one whose
    /// reading is longer than or differs from the typed syllables is choosing it from the row; one over exactly those
    /// syllables is a re-pick (`choose`). They go with the window when it closes.
    rows: Vec<Pred>,
    /// Digits contract A.3.6: the last item is not a lexicon entry but the other reading of digit keys (the digits of a syllable
    /// that just closed, or the syllable of the last digit tokens). Judged by the item's index, never by a length field.
    dalt: Option<DigitAlt>,
}

impl Cands {
    fn new(list: Vec<(String, usize)>, vertical: bool) -> Cands {
        Cands { list, sel: 0, expanded: false, top: 0, vertical, first: 0, english: false, zh: None, rows: Vec::new(), dalt: None }
    }

    fn scroll(&mut self) {
        let row = self.sel / PAGE_SIZE;
        if row < self.top {
            self.top = row;
        } else if row >= self.top + GRID_ROWS {
            self.top = row + 1 - GRID_ROWS;
        }
    }

    /// After the list changed under an open window (a ⌘⌫ that removed or reordered items), with `sel` already set
    /// inside the new list: keep `first` within what ↑, ↓ and Page Down reach (the last row shows the last item).
    fn reclamped(mut self) -> Self {
        self.first = self.first.min(self.sel).min(self.list.len().saturating_sub(PAGE_SIZE));
        if self.sel >= self.first + PAGE_SIZE {
            self.first = self.sel + 1 - PAGE_SIZE;
        }
        self.top = self.top.min((self.list.len() - 1) / PAGE_SIZE);
        if self.expanded {
            self.scroll();
        }
        self
    }

    /// (position of the first output candidate, how many are output).
    fn window(&self) -> (usize, usize) {
        let len = self.list.len();
        if self.vertical {
            (self.first, (len - self.first).min(PAGE_SIZE))
        } else if self.expanded {
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
    path: Vec<PathWord>,
    /// s3e: punctuation mark -> its alternatives (built-in default until `set_punctuation`).
    punct: HashMap<char, Vec<String>>,
    layout: Layout,
    syls: Vec<String>,
    cursor: usize,
    pend: [Option<char>; 3],
    /// V3 engine contract section 12: with the abbreviation composer on, the unfinished units before `pend` (the last one),
    /// in typing order. Never empty units; non-empty only while `pend` is.
    pend_prev: Vec<[Option<char>; 3]>,
    fixed: Vec<Fixed>,
    display: String,
    cands: Option<Cands>,
    /// V3: the passive prediction row (empty = none) and, once Tab entered it, the selected item.
    pred: Vec<Pred>,
    pred_sel: Option<usize>,
    /// Whether the row is computed at all (default on; `set_prediction`).
    predict_on: bool,
    /// Orientation the next candidate window opens with (default horizontal; `set_candidate_vertical`).
    cand_vertical: bool,
    /// Whether a symbol in an occupied column opens a new unfinished unit (default off; `set_abbreviation`, section 12).
    abbr: bool,
    /// Test hook: false queries `PREDICT_SCAN` at every start once the learner has records (no gate).
    scan_gate: bool,
    /// Set by the key rules that recompute the row (section 1.1); `key` recomputes after the rule ran.
    pred_dirty: bool,
    /// S4: the Han tail (≤ 2 chars) of the text before the insertion point, from `set_left_context`.
    left: String,
    learning: bool,
    /// New-word B §2.1: learning was off at some point of this composition, so its commit learns no new word. Set by
    /// `set_learning(false)` while composing and by any key handled with learning off; cleared by `clear_all` and by
    /// the first key on an empty composition (`key`).
    paused: bool,
    /// Test hook: false turns new-word B's learning off (S4 alone), the baseline of `measure_newword`.
    newword: bool,
    /// Variant B of new-word learning (`NEWWORD_SUBSPANS`); a test hook switches it for measurement.
    subspans: bool,
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
    /// S7c-2a: the small character model, from `load_tinylm`; dropped when another n-gram model is loaded (its `.sjtl` names
    /// the n-gram model's hash).
    tinylm: Option<Arc<TinyLm>>,
    /// The colloquial table and the word sets of its k term (`load_colloquial`); independent of the n-gram and the small model.
    /// Whether a job uses it is decided per job in `refresh_lm`.
    colloquial: Option<Arc<Colloquial>>,
    /// Whether the reading-channel term (docs/contracts/reading-channel.md) goes with the colloquial term (default on, the App's behavior).
    /// Off only from `set_reading_channel`, an evaluation switch for `shanjie-replay --no-reading-channel`; not in shanjie.h.
    reading_channel: bool,
    /// Whether rerank jobs are made (default off since S7c-2c, docs/contracts/s7c2c-delta.md section 2.3; `set_rerank`).
    /// `load_tinylm` leaves it as it is.
    rerank_on: bool,
    /// Which (alpha, tau, delta) of the small model the jobs use (rerank-strength section 2.2; default standard;
    /// `set_rerank_level`). Kept across `load_tinylm` and `load_lm`, and remembered without a model; a version 1 or 2 file has only
    /// the standard triple, which every level then gets.
    rerank_level: RerankLevel,
    /// The S7c-2a section 2.2 learning guard (default on, the app's behavior). Off only from `set_learn_guard`, an evaluation
    /// switch for `shanjie-replay --learn-guard` (engine-replay.md section 2); not in shanjie.h.
    learn_guard: bool,
    /// Stretches the guard kept out of the current decode's job (`guard_skips`); reset with the job in `invalidate`.
    guard_skips: usize,
    /// Section 2.3 (修正一): renewed from `GENERATION` only where decoding changes: every `refresh`, `clear_all`, the model and
    /// decoding settings (`load_lm`, `set_lm`, `load_tinylm`, `load_colloquial`, `set_rerank`, `set_rerank_level`, `set_profile`, `set_demote`,
    /// learning open/clear, the evaluation-only `set_learn_guard` and `set_reading_channel`).
    generation: u64,
    /// The job of the last refresh, until `take_rerank_job` or the next change.
    rerank_job: Option<RerankJob>,
    /// zhen-mixed: the switch (default off; `set_english`) and the word list (`load_english`).
    english: bool,
    /// Sorted by bytes, no duplicate: the full-word and the prefix lookups are binary searches (zhen-inline §1.5).
    english_words: Option<Vec<Box<str>>>,
    /// zhen-inline: whether the unfinished syllable shows as the shadow's letters when `inline_shown` holds (default on).
    english_inline: bool,
    /// Section 2.2: the keycaps of the unfinished syllable's Zhuyin keys (at most `SHADOW_MAX`), and how many times a key
    /// replaced a symbol in an occupied column (rule 9). Empty unless `shadow_on`; cleared with `clear_pend`.
    shadow: Vec<char>,
    shadow_over: u32,
    /// Set by `choose_english` when the token it inserted was the unit that reaches `MAX_SYLLABLES` and sent everything: the committed text, for the
    /// output of the key or pick that made it. Choosing an English item sends nothing otherwise (zhen-inline section 1.0).
    commit_out: Option<String>,
    /// zhen-inline section 1.3b: the English run being typed (Shift+letter starts it): letters shown at the cursor, not yet a
    /// unit. Empty = no run. Never together with an unfinished syllable or a window.
    run: String,
    /// Zhen-inline section 1.3i: a token reopened as the run, with the Chinese item it had; it comes back when the run ends with
    /// the same letters.
    run_zh: Option<(String, String, String)>,
    /// Whether the latest `key()` made an auto-keep (zhen-inline section 1.3; tool hook `english_autokept`). Cleared at the start
    /// of `key()` and nowhere else; no other entry point (a pick, a reset, a setting) touches it, so none can leave it stale.
    autokept: bool,
    /// Digits (docs/contracts/digits.md): the count table (`digits.tsv`, optional), what is built from it and the model once, the run
    /// of digit-alphabet keys being typed, the keys of the syllable that just closed (A.3.6; `ld_now` is what the current key saw), and
    /// the counters and trace of the simulator and the golden test. All idle unless `digits_active`.
    digit_table: Option<Arc<DigitTable>>,
    digit_cache: Option<Arc<DigitCache>>,
    drun: Option<DRun>,
    last_digits: Option<String>,
    ld_now: Option<String>,
    d_shown: usize,
    d_emitted: usize,
    dtrace: Option<Vec<DigitEvent>>,
    /// composition-cap §1.2 step 3: the syllable ranges ⌘⌫ forgot an entry over in the latest `key()`; the head freeze leaves them alone, so
    /// a forget shows on screen at once. Cleared by `cap_snapshot`.
    forgot: Vec<(usize, usize)>,
    /// composition-cap §1.2 step 1: the state before the running key or pick, kept only while a stretch could pass the window (`cap_snapshot`);
    /// taken by `cap_freeze`.
    cap: Option<CapSnap>,
    /// The free stretches of the latest decode, as `refresh_lm` cut them: `[from, to)` in syllables (fixed words and an open English run
    /// bound them). One definition of a stretch for the rerank job and for `cap_refreeze`.
    free: Vec<(usize, usize)>,
    /// Test hook (`decodes`): how many times `refresh` decoded the composition.
    decodes: u64,
}

/// Base lexicon + overlay from `data_dir` (§5), same as the eval CLI default. The overlay rows are
/// `overlay-add.tsv`, `sandhi-add.tsv` (S2r: MOE-standard 一/不 readings derived from the base, plus rule R6's colloquial 紮 ㄓㄚ), then `names-add.tsv`
/// (the always-on common proper nouns, docs/contracts/model-v6.md section 2.5), in that fixed order; all three are required.
pub fn load_lexicon(data_dir: &Path) -> Result<Arc<Lexicon>, EngineError> {
    load_lexicon_packs(data_dir, None).map(|(lex, _)| lex)
}

/// Pack bits of `Engine::new_with_packs` and `shanjie_engine_new_packs` (docs/contracts/acg-pack.md A.2).
pub const PACK_ACG: u32 = 1;
pub const PACK_ALL: u32 = PACK_ACG;
/// A pack's overlay file inside the packs directory, in the order the packs are appended.
const PACK_FILES: [(u32, &str); 1] = [(PACK_ACG, "acg-add.tsv")];

/// Why a lexicon load failed, for messages: the file that could not be read and its `ErrorKind`, or the lexicon text
/// that did not parse (`path` is the data directory the files came from, `detail` the `Lexicon` parse error). The C ABI
/// and `EngineError` stay as they were; the plain functions map this to `LoadFailed`.
#[derive(Debug)]
pub enum LoadError {
    Read { path: PathBuf, kind: std::io::ErrorKind },
    Parse { path: PathBuf, detail: String },
}

impl std::fmt::Display for LoadError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            LoadError::Read { path, kind } => write!(f, "cannot read {} ({kind:?})", path.display()),
            LoadError::Parse { path, detail } => write!(f, "cannot parse the lexicon in {}: {detail}", path.display()),
        }
    }
}

fn read_file(p: PathBuf) -> Result<String, LoadError> {
    std::fs::read_to_string(&p).map_err(|e| LoadError::Read { kind: e.kind(), path: p })
}

/// The enabled packs' overlay rows in `dir`, concatenated; a pack whose file is missing contributes
/// nothing (the engine is then identical to one without packs). An unreadable file is a load failure.
pub fn read_packs_detailed(dir: &Path, mask: u32) -> Result<String, LoadError> {
    let mut text = String::new();
    for (bit, name) in PACK_FILES {
        if mask & bit == 0 {
            continue;
        }
        match read_file(dir.join(name)) {
            Ok(t) => {
                if !text.is_empty() && !text.ends_with('\n') {
                    text.push('\n');
                }
                text.push_str(&t);
            }
            Err(LoadError::Read { kind: std::io::ErrorKind::NotFound, .. }) => {}
            Err(e) => return Err(e),
        }
    }
    Ok(text)
}

/// The always-on names layer (docs/contracts/names-lexicon.md, model-v6 section 2.5): read after `sandhi-add.tsv` into the lexicon
/// and, like `overlay-add.tsv`, into the capping text. Missing is a load failure, as for `overlay-add.tsv`.
pub const NAMES_FILE: &str = "names-add.tsv";

/// `load_lexicon` with the rows of `packs` (`(directory, mask)`, see `read_packs_detailed`) after `names-add.tsv`,
/// plus those rows alone (empty with no pack), for `capping_overlay`. `None` or an empty mask: exactly
/// `load_lexicon`. The engine and the evaluation CLI share this.
pub fn load_lexicon_packs(data_dir: &Path, packs: Option<(&Path, u32)>) -> Result<(Arc<Lexicon>, String), EngineError> {
    load_lexicon_packs_detailed(data_dir, packs).map_err(|_| EngineError::LoadFailed)
}

/// `load_lexicon_packs` with the failing file named (the evaluation CLI prints it).
pub fn load_lexicon_packs_detailed(data_dir: &Path, packs: Option<(&Path, u32)>) -> Result<(Arc<Lexicon>, String), LoadError> {
    load_lexicon_extra_detailed(data_dir, packs, None, true)
}

/// `load_lexicon_packs_detailed` plus the evaluation-only `--extra-overlay` file (names-lexicon contract section 3.5):
/// its rows join the lexicon after the packs' rows and come back in the returned text, so `capping_overlay` caps them
/// the same way. `None`: exactly `load_lexicon_packs_detailed`. A missing or unreadable file is a load failure naming it.
/// `names` false (the CLI's evaluation-only `--no-names`) leaves `names-add.tsv` out of the lexicon; the engine always passes true.
pub fn load_lexicon_extra_detailed(data_dir: &Path, packs: Option<(&Path, u32)>, extra_file: Option<&Path>, names: bool) -> Result<(Arc<Lexicon>, String), LoadError> {
    let base = read_file(data_dir.join("mcbpmf-data.txt"))?;
    let overlay = read_file(data_dir.join("overlay-add.tsv"))?;
    let sandhi = read_file(data_dir.join("sandhi-add.tsv"))?;
    let mut text = join_overlays(overlay, &sandhi);
    if names {
        text = join_overlays(text, &read_file(data_dir.join(NAMES_FILE))?);
    }
    let mut extra = match packs {
        Some((dir, mask)) => read_packs_detailed(dir, mask)?,
        None => String::new(),
    };
    if let Some(f) = extra_file {
        extra = join_overlays(extra, &read_file(f.to_path_buf())?);
    }
    if !extra.is_empty() {
        text = join_overlays(text, &extra);
    }
    let lex = Lexicon::parse_with(&base, Some(&text)).map_err(|e| LoadError::Parse { path: data_dir.to_path_buf(), detail: e.to_string() })?;
    Ok((Arc::new(lex), extra))
}

/// The text `CappedLexicon::new` takes as its overlay: `overlay-add.tsv` and `names-add.tsv` (read here, as `load_lm` always
/// did for the first), then the packs' rows `load_lexicon_packs` returned (no sandhi rows).
pub fn capping_overlay(data_dir: &Path, pack_text: &str) -> Result<String, EngineError> {
    capping_overlay_detailed(data_dir, pack_text).map_err(|_| EngineError::LoadFailed)
}

pub fn capping_overlay_detailed(data_dir: &Path, pack_text: &str) -> Result<String, LoadError> {
    let overlay = read_file(data_dir.join("overlay-add.tsv"))?;
    let overlay = join_overlays(overlay, &read_file(data_dir.join(NAMES_FILE))?);
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
        // Optional (digits.md A.3.2): a missing or malformed table leaves the digit feature off and says nothing.
        e.digit_table = std::fs::read_to_string(data_dir.join("digits.tsv")).ok().and_then(|t| DigitTable::parse(&t)).map(Arc::new);
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
            pend_prev: Vec::new(),
            fixed: Vec::new(),
            display: String::new(),
            cands: None,
            pred: Vec::new(),
            pred_sel: None,
            predict_on: true,
            cand_vertical: false,
            abbr: false,
            scan_gate: true,
            pred_dirty: false,
            left: String::new(),
            learning: false,
            paused: false,
            newword: true,
            subspans: crate::learn::NEWWORD_SUBSPANS,
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
            tinylm: None,
            rerank_on: false,
            colloquial: None,
            reading_channel: true,
            rerank_level: RerankLevel::Standard,
            learn_guard: true,
            guard_skips: 0,
            generation: next_generation(),
            rerank_job: None,
            english: false,
            english_words: None,
            english_inline: true,
            shadow: Vec::new(),
            shadow_over: 0,
            commit_out: None,
            run: String::new(),
            run_zh: None,
            autokept: false,
            digit_table: None,
            digit_cache: None,
            drun: None,
            last_digits: None,
            ld_now: None,
            d_shown: 0,
            d_emitted: 0,
            dtrace: None,
            forgot: Vec::new(),
            cap: None,
            free: Vec::new(),
            decodes: 0,
        }
    }

    /// Section 2.3 (revision 1): called wherever decoding changes (every `refresh`, `clear_all`, a new n-gram or small model,
    /// a new colloquial table, the rerank switch, the rerank level, the profile, the demotion table, learning open / clear, `set_learn_guard`): a job made
    /// before is stale from now on, and its `guard_skips` count goes with it.
    /// `set_learning` is not among them: the flag only decides whether picks are recorded (`pre`), never how the composition
    /// decodes (decoding reads the learner whenever it is non-empty).
    /// Keys that do not re-decode (cursor moves, unfinished symbols, entering the prediction row, pass-through) keep it.
    fn invalidate(&mut self) {
        self.generation = next_generation();
        self.rerank_job = None;
        self.guard_skips = 0;
    }

    // ---- S7c-2a rerank (docs/contracts/s7c2a-core-rerank.md) ----

    /// Section 2.5: the small model at `path` for this engine's n-gram model. Fails (the previous model, or none, stays) when
    /// the file cannot be read or is malformed, when no n-gram model is loaded, when the `.sjtl` was tuned with another n-gram
    /// model, or when its recorded Kneser-Ney side file hash is not the loaded side file's (none for a file tuned without KN;
    /// model-v6 section 2.7). A pairing check, not an integrity check of the `.sjtl`. Leaves the rerank switch and the display as
    /// they are.
    pub fn load_tinylm(&mut self, path: &Path) -> Result<(), EngineError> {
        self.invalidate();
        let st = self.lm.as_ref().ok_or(EngineError::LoadFailed)?;
        let model = TinyLm::load_paired(path, &st.lm).map_err(|_| EngineError::LoadFailed)?;
        self.tinylm = Some(Arc::new(model));
        Ok(())
    }

    /// The colloquial table at `path` (docs/contracts/colloquial-term.md section 2.2), with the word sets of its k term read from the
    /// engine's lexicon folder (`mcbpmf-data.txt`, `overlay-add.tsv`) and the pack rows this engine was built with. Callable before or
    /// after `load_lm` / `load_tinylm`: the pairing is checked per job. Fails (the previous table, or none, stays) when the engine has no
    /// data folder or a file cannot be read or parsed. Drops the pending job.
    pub fn load_colloquial(&mut self, path: &Path) -> Result<(), EngineError> {
        self.invalidate();
        let dir = self.data_dir.as_ref().ok_or(EngineError::LoadFailed)?;
        let co = Colloquial::load_from_dir(path, dir, self.pack_text.as_deref()).map_err(|_| EngineError::LoadFailed)?;
        self.colloquial = Some(Arc::new(co));
        Ok(())
    }

    /// The loaded colloquial table, if any (tests).
    pub fn colloquial(&self) -> Option<&Colloquial> {
        self.colloquial.as_deref()
    }

    /// Whether rerank jobs are made (default off, S7c-2c); recomputes the composition and returns the snapshot like `set_demote`,
    /// so turning it off shows the n-gram result at once. On a decode failure the engine resets itself.
    pub fn set_rerank(&mut self, on: bool) -> Result<Output, EngineError> {
        self.invalidate();
        self.rerank_on = on;
        self.clear_pred();
        if let Err(e) = self.refresh() {
            self.clear_all();
            return Err(e);
        }
        self.handled()
    }

    /// Evaluation only (engine-replay.md section 2, `--learn-guard`): turns the learning guard off so stretches with a learned
    /// boost still go to the small model. The next decode uses it; the pending job is dropped like any decoding change.
    pub fn set_learn_guard(&mut self, on: bool) {
        self.invalidate();
        self.learn_guard = on;
    }

    /// Evaluation only (`shanjie-replay --no-reading-channel`): with `false` the colloquial term stands alone, the P4d cell the reading-channel
    /// term was measured against. The next decode uses it; the pending job is dropped like any decoding change.
    pub fn set_reading_channel(&mut self, on: bool) {
        self.invalidate();
        self.reading_channel = on;
    }

    /// How many stretches the learning guard kept out of the current job: stretches that would have gone to the small model
    /// (rerank on, model loaded, demotion table on, two candidates or more). Counted by each decode; 0 after any change that
    /// drops the job (a commit, Esc, a setting) until the next decode, and 0 with the guard off.
    pub fn guard_skips(&self) -> usize {
        self.guard_skips
    }

    /// Rerank strength (rerank-strength section 2.2): which of the model's (alpha, tau, delta) the jobs use. Remembered without a
    /// model and kept by `load_tinylm` / `load_lm`; with a version 1 or 2 file every level uses the standard triple. Recomputes the
    /// composition and returns the snapshot like `set_profile`; on a decode failure the engine resets itself.
    pub fn set_rerank_level(&mut self, level: RerankLevel) -> Result<Output, EngineError> {
        self.invalidate();
        self.rerank_level = level;
        self.clear_pred();
        if let Err(e) = self.refresh() {
            self.clear_all();
            return Err(e);
        }
        self.handled()
    }

    /// The levels the loaded small model offers, as `shanjie_engine_rerank_levels` reports them (bit0 conservative, bit1 standard,
    /// bit2 aggressive): 0 without a model, 2 for a version 1 or 2 file, 7 for a version 3 or 4 file (whatever its values or the profile).
    pub fn rerank_levels(&self) -> u32 {
        match &self.tinylm {
            None => 0,
            Some(m) if m.has_levels() => 7,
            Some(_) => 2,
        }
    }

    /// The longest word a candidate window can list (the bound `window_list` uses). Read-only; the replay tool stops its scan there.
    pub fn max_window_len(&self) -> usize {
        self.lex.max_len.max(NEWWORD_MAX)
    }

    /// The job of the last change, if it has one (none without a model, with rerank or demotion off, or with nothing to rerank).
    pub fn take_rerank_job(&mut self) -> Option<RerankJob> {
        self.rerank_job.take()
    }

    /// Section 2.4: puts the chosen paths into the composition and returns the new output. `None` (nothing changes) when the
    /// result is stale (another decode since the job, revision 1 of section 2.3), `path` no longer holds the words the job was
    /// made from at every stretch (e.g. the same job's other result was applied), the candidate window is open, the
    /// prediction row is entered, or every stretch kept its first path. Syllables, cursor, fixed words and the candidate window
    /// are not touched; the prediction row is recomputed only when one is showing (a row the key rules cleared stays cleared).
    pub fn apply_rerank(&mut self, r: RerankResult) -> Result<Option<Output>, EngineError> {
        if r.generation != self.generation || self.cands.is_some() || self.pred_sel.is_some() {
            return Ok(None);
        }
        let holds = |c: &Choice| {
            self.path.get(c.at..c.at + c.expect.len()).is_some_and(|ws| ws.iter().map(|w| &w.word).eq(c.expect.iter()))
        };
        if !r.choices.iter().all(holds) {
            return Ok(None);
        }
        let changes: Vec<Choice> = r.choices.into_iter().filter(|c| c.pick != 0).collect();
        if changes.is_empty() {
            return Ok(None);
        }
        // Right to left, so the earlier ranges still point at their words.
        for c in changes.into_iter().rev() {
            self.path.splice(c.at..c.at + c.expect.len(), c.words.into_iter().map(|(word, lp, delta, syllables)| PathWord { word, lp, is_punct: false, delta, syllables }));
        }
        // `refresh_lm` builds the display from exactly the strings of `path`.
        self.display = self.path.iter().map(|p| p.word.as_str()).collect();
        if !self.pred.is_empty() {
            self.recompute_pred();
        }
        self.handled().map(Some)
    }

    // ---- S4 learning (docs/contracts/s4-learning.md) ----

    /// §2: keep only the last ≤ 2 consecutive Han characters of `text` (none: empty).
    pub fn set_left_context(&mut self, text: &str) {
        let k = context_key(text);
        self.left = if k == crate::learn::SENTINEL { String::new() } else { k };
    }

    /// §3: off by default. Turning it off drops pending learns; a span is learned only if the flag was
    /// on both when it was chosen and at commit. It never makes a rerank job stale (S7c-2b section 2.1, corrected
    /// 2026-10-11): the flag does not change decoding, which reads the learner whenever it is non-empty; it only decides
    /// whether picks are recorded. The shell sends it before every key (s4-learning.md section 3).
    /// New-word B §2.1: turning it off while composing, or any key handled while it is off (`key`), marks the
    /// composition, so its commit learns no new word even when learning is back on by then (text typed during the pause
    /// is never stored next to a later re-pick). The mark goes when the composition is empty again.
    pub fn set_learning(&mut self, on: bool) {
        self.learning = on;
        if !on {
            self.fixed.iter_mut().for_each(|f| {
                f.pre = None;
                f.displaced_new.clear();
            });
            if !self.syls.is_empty() || self.pend.iter().any(Option::is_some) {
                self.paused = true;
            }
        }
    }

    /// Test hook: whether new-word B learns (default on). Off is S4 alone (contract §3.1 baseline).
    pub fn set_newword(&mut self, on: bool) {
        self.newword = on;
    }

    /// Test hook: variant B (`NEWWORD_SUBSPANS`, on by default since the user's decision of 2026-10-10) on or off.
    pub fn set_newword_subspans(&mut self, on: bool) {
        self.subspans = on;
    }

    /// §4: load `dir/learning.tsv` (memory pruned only; the file is tidied by the first write, which
    /// is always a full rewrite). On error the previous learner and store stay. `must_rewrite` and
    /// bit0 are left as they are: only a successful full rewrite or clear clears them.
    pub fn learning_open(&mut self, dir: &Path) -> Result<Opened, StoreError> {
        self.invalidate();
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
        self.invalidate();
        if self.store.is_some() {
            self.must_rewrite = true;
        }
        self.learner.clear();
        self.fixed.iter_mut().for_each(|f| {
            f.pre = None;
            f.displaced_new.clear();
        });
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
        if !self.learning {
            return;
        }
        let new_on = self.newword && !self.paused;
        if !(new_on && self.learner.has_new_words()) && self.fixed.iter().all(|f| f.pre.is_none() && f.displaced_new.is_empty()) {
            return;
        }
        let _ = catch_unwind(AssertUnwindSafe(|| {
            if std::mem::take(&mut self.learn_panic) {
                panic!("injected learning panic");
            }
            let today = self.today();
            let uses = if new_on { self.new_word_uses(today) } else { Vec::new() };
            let plans: Vec<_> = self
                .fixed
                .iter()
                .filter_map(|f| {
                    let pre = f.pre.as_ref().filter(|p| **p != f.word && !self.is_punct(f.start))?;
                    let off: usize = (0..f.start).map(|i| self.token_width(i)).sum();
                    let before: String = display.chars().take(off).collect();
                    let ctx = f.key.clone().unwrap_or_else(|| context_key(&format!("{}{before}", self.left)));
                    // Rule 8: at sentence start the key holds the reading of the syllable after the word; a
                    // prediction pick (`f.key` set) was made at the end of the composition, so it is `^>$`.
                    let next = if f.key.is_some() { None } else { self.syls.get(f.end).map(String::as_str) };
                    let ctx = record_key(ctx, next);
                    // The key the displaced word was scored under at the pick, when it differs from the commit's.
                    let at_pick = f.pick_key.clone().filter(|k| *k != ctx);
                    Some((ctx, self.syls[f.start..f.end].to_vec(), f.word.clone(), pre.clone(), at_pick))
                })
                .collect();
            let mut touched = Vec::new();
            for (ctx, reading, word, displaced, at_pick) in &plans {
                touched.extend(self.learner.teach(ctx, reading, word, displaced, today));
                if let Some(k) = at_pick {
                    touched.extend(self.learner.halve(k, reading, displaced, today));
                }
            }
            // New-word B: learn (§2.2), halve what re-picks displaced (§2.5), count the uses (§2.5, once a day).
            let halves: Vec<(Vec<String>, String)> =
                if self.newword { self.fixed.iter().flat_map(|f| f.displaced_new.clone()).collect() } else { Vec::new() };
            let learns = if new_on { self.new_word_spans() } else { Vec::new() };
            for (reading, word) in &learns {
                touched.push(self.learner.learn_new(reading, word, today));
            }
            for (reading, word) in &halves {
                touched.extend(self.learner.halve_new(reading, word, today));
            }
            for (reading, word) in &uses {
                touched.extend(self.learner.use_new(reading, word, today));
            }
            // Nothing touched (e.g. a single character under "^" or a one-character key, §12, §14): no write, unless a full
            // rewrite is pending (a failed forget or rewrite). It is retried only by commits that reach
            // this point: learning on and at least one re-pick or displaced new word, or new-word B learning on
            // (no pause) with any new-word record in the learner (the early return above); other commits leave it
            // pending.
            if !touched.is_empty() || self.must_rewrite {
                self.persist(&touched);
            }
        }));
    }

    /// Whether the lexicon has `word` under `reading`. New-word B's one lexicon (contract §2.2 item 3, revision two) is
    /// `self.lex`, what `load_lexicon_packs` loaded: the base, overlay-add.tsv, sandhi-add.tsv, names-add.tsv and the
    /// packs this engine was built with, at their file scores (before capping and demotion). Rules 3 and 5, the shadow
    /// gate, the decoder's "the lexicon has it" check and experiments/newword/hijack.py all use it.
    fn in_lexicon(&self, reading: &[String], word: &str) -> bool {
        self.lex.has(reading, word)
    }

    /// New-word B §2.2 item 3: the reading leaves room for a new word, i.e. no lexicon word under it scores above
    /// `NEWWORD_SHADOW` (no word at all also does).
    fn room_for_new_word(&self, reading: &[String]) -> bool {
        self.lex.entries(reading).iter().all(|&(_, score)| score <= NEWWORD_SHADOW)
    }

    /// New-word B §2.2: the runs of single Han characters on the path at commit (split at punctuation, non-Han and
    /// `NEWWORD_BREAK`) that hold a character re-picked in the candidate window, are NEWWORD_MIN..=NEWWORD_MAX long,
    /// are not all `NEWWORD_DIGITS`, are not in the lexicon under their reading and whose reading has no lexicon word above
    /// `NEWWORD_SHADOW`: (reading, word) each, plus variant B's sub-spans (§2.2 item 5).
    fn new_word_spans(&self) -> Vec<(Vec<String>, String)> {
        // A re-pick of one character (S4 §1.2: chosen in the window, text before it differed); a prediction pick
        // (`Some("")`) is no re-pick.
        let picked: HashSet<usize> = self
            .fixed
            .iter()
            .filter(|f| f.end == f.start + 1 && f.word.chars().count() == 1 && !self.is_punct(f.start))
            .filter(|f| f.pre.as_ref().is_some_and(|p| !p.is_empty() && *p != f.word))
            .map(|f| f.start)
            .collect();
        let mut out = Vec::new();
        if picked.is_empty() {
            return out;
        }
        let mut run: Vec<(usize, char)> = Vec::new();
        let mut flush = |run: &mut Vec<(usize, char)>| {
            let take = std::mem::take(run);
            if (NEWWORD_MIN..=NEWWORD_MAX).contains(&take.len())
                && take.iter().any(|(s, _)| picked.contains(s))
                && !take.iter().all(|(_, c)| NEWWORD_DIGITS.contains(*c))
            {
                let reading = self.syls[take[0].0..take[take.len() - 1].0 + 1].to_vec();
                let word: String = take.iter().map(|x| x.1).collect();
                if !self.in_lexicon(&reading, &word) && self.room_for_new_word(&reading) {
                    out.push((reading, word));
                    // Variant B: the run's shorter pieces holding a re-pick, where the lexicon has no word at all.
                    for (a, b) in (0..take.len()).flat_map(|a| (a + NEWWORD_MIN..=take.len()).map(move |b| (a, b))) {
                        let sub = &take[a..b];
                        if !self.subspans
                            || sub.len() == take.len()
                            || !sub.iter().any(|(s, _)| picked.contains(s))
                            || sub.iter().all(|(_, c)| NEWWORD_DIGITS.contains(*c))
                        {
                            continue;
                        }
                        let r = self.syls[sub[0].0..sub[sub.len() - 1].0 + 1].to_vec();
                        if self.lex.entries(&r).is_empty() {
                            out.push((r, sub.iter().map(|x| x.1).collect()));
                        }
                    }
                }
            }
        };
        for (p, s) in self.path.iter().zip(self.path_starts()) {
            let mut cs = p.word.chars();
            match (cs.next(), cs.next()) {
                (Some(c), None) if !p.is_punct && is_han(c) && !NEWWORD_BREAK.contains(c) => run.push((s, c)),
                _ => flush(&mut run),
            }
        }
        flush(&mut run);
        // One commit learns a (reading, word) once, however many runs or sub-spans spell it (e.g. 板空板空 → 板空 twice).
        let mut seen = HashSet::new();
        out.retain(|x| seen.insert(x.clone()));
        out
    }

    /// New-word B §2.5: the new words on the path (not lexicon words under their reading) whose record is not dated
    /// `today` yet: each is "used" once by this commit.
    fn new_word_uses(&self, today: i64) -> Vec<(Vec<String>, String)> {
        if !self.learner.has_new_words() {
            return Vec::new();
        }
        self.path_new_words()
            .into_iter()
            .filter(|(_, _, r, w)| self.learner.new_word_day(r, w).is_some_and(|d| d != today))
            .map(|(_, _, r, w)| (r, w))
            .collect()
    }

    /// The path's tokens that are new words: (start, end, reading, word), with a record and not in the lexicon.
    fn path_new_words(&self) -> Vec<(usize, usize, Vec<String>, String)> {
        if !self.learner.has_new_words() {
            return Vec::new();
        }
        let mut out = Vec::new();
        for (p, s) in self.path.iter().zip(self.path_starts()) {
            let (w, n) = (&p.word, p.word.chars().count());
            if p.is_punct || !(NEWWORD_MIN..=NEWWORD_MAX).contains(&n) || s + n > self.syls.len() {
                continue;
            }
            let reading = &self.syls[s..s + n];
            if self.learner.new_word_day(reading, w).is_some() && !self.in_lexicon(reading, w) {
                out.push((s, s + n, reading.to_vec(), w.clone()));
            }
        }
        out
    }

    /// S2c: read the model at `path` and `data_dir/overlay-add.tsv` and `names-add.tsv` (plus the pack rows `new` kept), build the capped lexicon with the
    /// shared constructor. Failure leaves the previous state. The composition display is not recomputed;
    /// the next change to it decodes with the new model.
    pub fn load_lm(&mut self, path: &Path) -> Result<(), EngineError> {
        self.invalidate();
        let dir = self.data_dir.as_ref().ok_or(EngineError::LoadFailed)?;
        let overlay = capping_overlay(dir, self.pack_text.as_deref().unwrap_or(""))?;
        let demote = std::fs::read_to_string(dir.join("demote.tsv")).map_err(|_| EngineError::LoadFailed)?;
        let demote = Demote::parse(&demote).ok_or(EngineError::LoadFailed)?;
        let lm = Lm::load(path).map_err(|_| EngineError::LoadFailed)?;
        let capped = CappedLexicon::new(self.lex.clone(), &overlay, &lm, Some(&demote)).ok_or(EngineError::LoadFailed)?;
        // Built now, not at the first key: the index takes hundreds of ms (V3 engine contract section 1.2).
        capped.predict_index(&lm);
        self.lm = Some(LmState { lm: Arc::new(lm), capped: Arc::new(capped) });
        self.tinylm = None; // S7c-2a section 2.3: tuned for the previous model
        self.digit_cache = None;
        self.digit_env(); // built now, not at the first digit key (a table loaded and a model: the legal tails and the word readings)
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
        self.invalidate();
        self.lm = Some(LmState { lm, capped });
        self.tinylm = None;
    }

    /// Whether the demotion table applies (default on, remembered even before a model is loaded);
    /// recomputes the composition and returns the snapshot, like `set_profile`. On a decode failure the
    /// engine resets itself.
    pub fn set_demote(&mut self, on: bool) -> Result<Output, EngineError> {
        self.invalidate();
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
        if !on {
            self.drop_units(); // the units' only way out is the row (section 12.1)
            self.close_row_window();
        }
        self.recompute_pred(); // clears first; returns empty while off
        self.handled()
    }

    /// Orientation of the candidate windows that open from now on (default horizontal; candidate-vertical contract
    /// section 2.2). A candidate window already open keeps the orientation it opened with. A prediction row has no opening
    /// and follows the setting on every output (section 2.4), so a row on screen is re-rendered in the new orientation in
    /// the returned snapshot; with a candidate window open or nothing shown the snapshot equals the output before the call.
    pub fn set_candidate_vertical(&mut self, on: bool) -> Result<Output, EngineError> {
        self.cand_vertical = on;
        self.handled()
    }

    /// Whether a symbol typed into an occupied column opens a new unfinished unit (default off, V3 engine contract section 12).
    /// Turning it off with two or more units drops them all (as Esc does); otherwise nothing changes. Returns the snapshot.
    pub fn set_abbreviation(&mut self, on: bool) -> Result<Output, EngineError> {
        self.abbr = on;
        if !on {
            self.drop_units();
        } else {
            let flushed = self.digit_flush()?; // digits.md A.3.7: the buffered digits become tokens
            self.close_english(); // zhen-mixed section 2.1: the two are exclusive
            return self.settle_with(flushed);
        }
        self.handled()
    }

    /// zhen-mixed section 3.1: the English word list at `path`: `[a-z]{3,32}` per line, LF, no duplicate, no BOM, at most
    /// 4 MiB and 200,000 lines. Any other content, or an unreadable or non-regular file, fails and the previous list (or
    /// none) stays; nothing is loaded in part. Does not touch the switch or the display. No path or word in any error (R2).
    pub fn load_english(&mut self, path: &Path) -> Result<(), EngineError> {
        // The tiny model's reader (S7c-2a F3): a FIFO is refused at once instead of blocking the shell that builds the engine.
        let bytes = crate::tinylm::read_bounded(path, ENGLISH_MAX_BYTES).map_err(|_| EngineError::LoadFailed)?;
        let text = String::from_utf8(bytes).map_err(|_| EngineError::LoadFailed)?;
        let mut words: Vec<Box<str>> = Vec::new();
        // The last line ends with a line break (stripped once); an empty file is an empty list, a blank line anywhere is bad.
        let lines = if text.is_empty() { None } else { Some(text.strip_suffix('\n').unwrap_or(&text).split('\n')) };
        for line in lines.into_iter().flatten() {
            let ok = ENGLISH_LEN.contains(&line.len()) && line.bytes().all(|b| b.is_ascii_lowercase());
            if !ok || words.len() >= ENGLISH_MAX_LINES {
                return Err(EngineError::LoadFailed);
            }
            words.push(Box::from(line));
        }
        words.sort_unstable();
        if words.windows(2).any(|w| w[0] == w[1]) {
            return Err(EngineError::LoadFailed); // a duplicate line
        }
        self.english_words = Some(words);
        Ok(())
    }

    /// zhen-mixed (docs/contracts/zhen-mixed.md): the switch, default off, remembered even without a list. Takes effect only
    /// with a loaded list, the standard layout and the abbreviation composer off (`shadow_on`). Off closes an open English
    /// window and clears the shadow. Returns the snapshot.
    pub fn set_english(&mut self, on: bool) -> Result<Output, EngineError> {
        self.english = on;
        if !on {
            let flushed = self.digit_flush()?; // digits.md A.3.7: the buffered digits become tokens
            self.close_english();
            return self.settle_with(flushed);
        }
        self.handled()
    }

    /// Whether the English window is open (test and tool hook).
    #[doc(hidden)]
    pub fn english_open(&self) -> bool {
        self.cands.as_ref().is_some_and(|c| c.english)
    }

    /// zhen-inline: the display option "show the English as it is typed" (default on). No effect without `set_english(true)`.
    /// Returns the snapshot, so a composition on screen is drawn again in the new display.
    pub fn set_english_inline(&mut self, on: bool) -> Result<Output, EngineError> {
        self.english_inline = on;
        self.handled()
    }

    /// zhen-inline section 1.1, the display rule D: whether the unfinished syllable shows as the shadow's letters (test and
    /// tool hook; `shown` is the one consumer). All of: the option and the shadow on, the cursor at the end with one unit, at
    /// least 3 letters, the trigger's overwrite condition, and a listed word that starts with the letters.
    #[doc(hidden)]
    pub fn english_inline_shown(&self) -> bool {
        if !self.english_inline || self.shadow.len() < 3 {
            return false;
        }
        let Some((words, prefix)) = self.eligible_shadow() else { return false };
        Self::has_prefix(words, &prefix)
    }

    /// Whether `w` is a listed word.
    fn is_word(words: &[Box<str>], w: &str) -> bool {
        words.binary_search_by(|x| (**x).cmp(w)).is_ok()
    }

    /// Whether a listed word starts with `prefix` (the whole word included). Section 1.5: one binary search for the first word
    /// not below the prefix; the words that start with it are contiguous.
    fn has_prefix(words: &[Box<str>], prefix: &str) -> bool {
        let i = words.partition_point(|w| **w < *prefix);
        words.get(i).is_some_and(|w| w.starts_with(prefix))
    }

    /// Whether the latest `key()` made an auto-keep (zhen-inline section 1.3: the rule D table row, an English window kept by a
    /// Zhuyin or punctuation key, letters kept by punctuation, section 1.3h). Only `key()` sets and clears it. Test and tool
    /// hook; the simulator asks right after each key and never repeats the rule.
    #[doc(hidden)]
    pub fn english_autokept(&self) -> bool {
        self.autokept
    }

    /// The shadow's length (test and tool hook).
    #[doc(hidden)]
    pub fn english_shadow_len(&self) -> usize {
        self.shadow.len()
    }

    /// Section 2.1: the four conditions under which the shadow is recorded at all.
    fn shadow_on(&self) -> bool {
        self.english && self.english_words.is_some() && !self.abbr && self.layout == Layout::Standard
    }

    fn clear_shadow(&mut self) {
        self.shadow.clear();
        self.shadow_over = 0;
    }

    /// Closes the English window if it is open, and clears the shadow either way; the unfinished syllable stays.
    fn close_english(&mut self) {
        if self.english_open() {
            self.cands = None;
        }
        self.clear_shadow();
    }

    /// Closes the candidate window (rules 7 and 8); an English window takes the shadow with it (section 2.3).
    fn close_cands(&mut self) {
        if self.cands.take().is_some_and(|c| c.english) {
            self.clear_shadow();
        }
    }

    /// Section 2.2: one Zhuyin key of the unfinished syllable; `over` is whether it replaced a symbol (rule 9).
    fn shadow_push(&mut self, ch: char, over: bool) {
        if !self.shadow_on() {
            return;
        }
        if self.shadow.len() == SHADOW_MAX {
            self.clear_shadow(); // the 33rd key: start again
            return;
        }
        self.shadow.push(ch);
        self.shadow_over += over as u32;
    }

    /// The overwrite condition of the trigger (zhen-mixed section 2.2) and of the display rule D (zhen-inline section 1.1
    /// item 4): a key replaced a symbol at least once, and when the leftover syllable (tone 1) is itself in the lexicon at
    /// least twice.
    fn overwrite_enough(&self) -> bool {
        self.shadow_over >= 2 || (self.shadow_over == 1 && self.lex.entries(&[self.pending()]).is_empty())
    }

    /// The shadow as a candidate for an English word, and the list to look it up in: the shadow is recorded (`shadow_on`), the
    /// cursor is at the end with one unit, and `overwrite_enough`. The one place both the trigger and the display rule D ask.
    fn eligible_shadow(&self) -> Option<(&[Box<str>], String)> {
        let words = self.english_words.as_deref().filter(|_| self.shadow_on())?;
        if self.cursor != self.syls.len() || !self.pend_prev.is_empty() || !self.overwrite_enough() {
            return None;
        }
        Some((words, self.shadow.iter().collect()))
    }

    /// Section 2.2, the trigger (Space before rule 10): the eligible shadow is a whole listed word.
    fn english_trigger(&self) -> bool {
        self.eligible_shadow().is_some_and(|(words, word)| Self::is_word(words, &word))
    }

    /// The unfinished syllable's columns, for the typing simulator (tool hook; no letters, no tone).
    #[doc(hidden)]
    pub fn english_columns(&self) -> [Option<char>; 3] {
        self.pend
    }

    /// The writings of an English word, in the order of the window (zhen-inline section 1.0): as it stands, then lower case,
    /// Capitalised, UPPER (no repeat). The window of the shadow's word is lower case, so it lists three.
    fn english_writings(word: &str) -> Vec<String> {
        let lower = word.to_ascii_lowercase();
        let mut title = lower.clone();
        title[..1].make_ascii_uppercase();
        let mut list: Vec<String> = Vec::new();
        for w in [word.to_string(), lower.clone(), title, lower.to_ascii_uppercase()] {
            if !list.contains(&w) {
                list.push(w);
            }
        }
        list
    }

    /// Section 2.3: the window of the shadow's word (as typed, capitalised, upper case) and, when the leftover syllable is in
    /// the lexicon, its first entry. The unfinished syllable stays in the preedit.
    fn open_english(&mut self) -> Result<Output, EngineError> {
        let word: String = self.shadow.iter().collect();
        let mut list: Vec<(String, usize)> = Self::english_writings(&word).into_iter().map(|w| (w, 0)).collect();
        if let Some((zh, _)) = self.lex.entries(&[self.pending()]).first() {
            list.push((zh.to_string(), 1));
        }
        let mut c = Cands::new(list, self.cand_vertical);
        c.english = true;
        self.cands = Some(c);
        self.handled()
    }

    /// Section 2.3 and zhen-inline section 1.0: item `idx` of the English window. The unfinished syllable and the shadow go;
    /// an English item becomes an English token at the cursor (`insert_english`), a Chinese item the leftover syllable with the
    /// item fixed over it. Neither is learned, and nothing is committed here (the commit of a unit that reaches `MAX_SYLLABLES` waits in `commit_out`).
    ///
    /// `keep`: the window was closed by a Zhuyin key (section 1.3), so an English token is an auto-keep.
    fn choose_english(&mut self, idx: usize, keep: bool) -> Result<(), EngineError> {
        let Some(c) = self.cands.take() else { return Ok(()) };
        let Some((word, len)) = c.list.get(idx).cloned() else { return Ok(()) };
        let commit = if len == 0 {
            self.insert_english(word, keep)?
        } else {
            let reading = self.pending();
            self.clear_pend();
            self.pred_dirty = true;
            self.insert_token(reading, Some(word), None)?.commit
        };
        if !commit.is_empty() {
            self.commit_out = Some(commit); // the syllable that reached `MAX_SYLLABLES` committed everything (section 2.1 of s3d)
        }
        Ok(())
    }

    /// Letters become an English token at the cursor (reading `PUNCT_PREFIX` + the word, the word as its fixed word). `auto`: an
    /// auto-keep (section 1.3), which is reported by `english_autokept` and remembers the unfinished syllable (what the window's
    /// Chinese item would be) for section 1.3c. Returns what the unit that reaches `MAX_SYLLABLES` sent, if it did (section 1.3: it goes in this key's output).
    fn insert_english(&mut self, word: String, auto: bool) -> Result<String, EngineError> {
        let zh = auto.then(|| self.pending());
        self.autokept |= auto;
        self.put_english(format!("{PUNCT_PREFIX}{word}"), word, zh)
    }

    /// The one place an English token goes in at the cursor: the unfinished syllable and the shadow go.
    fn put_english(&mut self, reading: String, word: String, zh: Option<String>) -> Result<String, EngineError> {
        self.clear_pend();
        self.pred_dirty = true;
        Ok(self.insert_token(reading, Some(word), zh)?.commit)
    }

    /// Sections 1.3g and 1.3i: the English token at `at` goes and an English run opens there (before the removal, so the decode
    /// splits at the run, section 1.3h). `whole`: with all its letters, and an auto-keep's Chinese item and reading kept for
    /// `end_run`; else without the last letter (a one-letter token just goes). A token without its fixed word falls back to the
    /// text of its reading.
    fn reopen_token(&mut self, at: usize, whole: bool) -> Result<(), EngineError> {
        let (word, zh) = match self.fixed.iter().find(|f| f.start == at) {
            Some(f) => (f.word.clone(), f.zh.clone()),
            None => (english_of(&self.syls[at]).unwrap_or_default().to_string(), None),
        };
        if whole {
            self.run_zh = zh.map(|zh| (word.clone(), zh, self.syls[at].clone()));
            self.run = word;
        } else {
            let mut letters = word;
            letters.pop();
            self.run = letters;
        }
        self.cursor = at;
        self.remove_syllable(at)?;
        self.pred_dirty = true;
        Ok(())
    }

    /// Zhen-inline section 1.3, the auto-keep: when the letters on screen (rule D) are a whole listed word and the Zhuyin key
    /// `ch` would make something that starts no listed word, that word.
    fn autokeep_word(&self, ch: char) -> Option<String> {
        if !self.english_inline_shown() {
            return None; // the letters must be on screen: the rule D, asked of its one function
        }
        let (words, word) = self.eligible_shadow()?;
        (Self::is_word(words, &word) && !Self::has_prefix(words, &format!("{word}{ch}"))).then_some(word)
    }

    /// The first key of a new syllable after an auto-keep or a kept window: rule 14.
    fn start_syllable(&mut self, ch: char, col: usize, sym: char) {
        self.shadow_push(ch, false);
        self.pend[col] = Some(sym);
        self.pred_dirty = true;
    }

    // ---- zhen-inline section 1.3b: the English run ----

    /// Ends the run: its letters become an English token at the cursor (reading `PUNCT_PREFIX` + the letters as typed). Returns
    /// what the unit that reaches `MAX_SYLLABLES` sent, if it did.
    fn end_run(&mut self) -> Result<String, EngineError> {
        let word = std::mem::take(&mut self.run);
        if let Some((_, zh, reading)) = self.run_zh.take().filter(|(w, _, _)| *w == word) {
            return self.put_english(reading, word, Some(zh)); // section 1.3i: the auto-keep's token as it was
        }
        self.insert_english(word, false)
    }

    /// A letter on the run; the 33rd ends it and starts the next one. Returns what ending sent, if it sent anything.
    fn run_push(&mut self, c: char) -> Result<String, EngineError> {
        let commit = if self.run.len() == SHADOW_MAX { self.end_run()? } else { String::new() };
        self.run.push(c);
        self.run_zh = None; // section 1.3i: other letters, a run's token
        Ok(commit)
    }

    /// A setting that ends English mixing (`set_english(false)`, `set_abbreviation(true)`) keeps the run as a token.
    fn settle_run(&mut self) -> Result<Output, EngineError> {
        let commit = if self.run.is_empty() { String::new() } else { self.end_run()? };
        Ok(self.view(true, commit))
    }

    /// Shift+letter with English mixing on: the shadow's letters (when it is all letters; else the unfinished syllable is dropped,
    /// as Esc would) and `upper` open a run. An open window closes with it.
    fn start_run(&mut self, upper: char) -> Result<Output, EngineError> {
        let keep: String = if self.shadow.iter().all(char::is_ascii_lowercase) { self.shadow.iter().collect() } else { String::new() };
        self.cands = None;
        self.clear_pend();
        self.run = keep;
        let commit = self.run_push(upper)?;
        if self.cursor < self.syls.len() {
            self.refresh()?; // the run opened in front of units: they decode apart from it (section 1.3h)
        }
        Ok(self.view(true, commit))
    }

    /// A run that closed without becoming a token (Esc, Backspace to empty): the composition decodes as one stretch again, since
    /// the split of section 1.3h holds only while the run is open. Nothing to do while letters are left.
    fn run_closed(&mut self) -> Result<(), EngineError> {
        if self.run.is_empty() && !self.syls.is_empty() {
            self.refresh()?;
        }
        Ok(())
    }

    /// A key while the run is open (section 1.3b): letters go on it (no auto-keep, amendment three), Backspace and Esc take
    /// letters off, Space ends it and is eaten, every other key ends it and is then handled as it would be.
    fn run_key(&mut self, k: Key) -> Result<Output, EngineError> {
        let m = k.modifiers;
        if k.kind == KeyKind::Char && (m == 0 || m == MOD_SHIFT) && k.ch.is_ascii_alphabetic() {
            let c = if m == MOD_SHIFT { k.ch.to_ascii_uppercase() } else { k.ch.to_ascii_lowercase() };
            let commit = self.run_push(c)?;
            return Ok(self.view(true, commit));
        }
        match k.kind {
            KeyKind::Backspace => {
                self.run.pop();
                self.run_zh = None;
                self.pred_dirty = true;
                self.run_closed()?;
                self.handled()
            }
            KeyKind::Esc => {
                self.run.clear();
                self.run_zh = None;
                self.pred_dirty = true;
                self.run_closed()?;
                self.handled()
            }
            KeyKind::Space => {
                let commit = self.end_run()?;
                Ok(self.view(true, commit))
            }
            _ => self.end_run_then(k),
        }
    }

    /// Ends the run, then handles `k` on what is left; what ending sent goes first in this output. Enter that ended a run which
    /// sent everything stops there.
    fn end_run_then(&mut self, k: Key) -> Result<Output, EngineError> {
        let commit = self.end_run()?;
        if k.kind == KeyKind::Enter && !commit.is_empty() {
            return Ok(self.view(true, commit));
        }
        // The row is recomputed now, before the key: `pred_dirty` would overwrite the candidates this key may open (Down).
        self.pred_dirty = false;
        self.recompute_pred();
        let mut o = self.dispatch(k)?;
        o.commit.insert_str(0, &commit);
        Ok(o)
    }

    /// The output of a key or pick that chose a candidate: the commit of the unit that reaches `MAX_SYLLABLES` (an item of the English window), or the
    /// plain snapshot.
    fn chosen(&mut self) -> Result<Output, EngineError> {
        match self.commit_out.take() {
            Some(commit) => Ok(self.view(true, commit)),
            None => self.handled(),
        }
    }

    /// What `set_prediction(false)` and `set_abbreviation(false)` do with two or more unfinished units: they go, the row
    /// with them, as with Esc (which goes through `clear_pend`); the composition, fixed words and cursor stay.
    fn drop_units(&mut self) {
        if !self.pend_prev.is_empty() {
            self.clear_pend();
            self.clear_pred();
        }
    }

    /// Section 12.1: a new unit opens only with the setting on, the row on (it needs a model to show) and the cursor at
    /// the end, and only below `ABBR_MAX_UNITS`; the completed syllables plus the units stay within `MAX_SYLLABLES`.
    fn abbr_active(&self) -> bool {
        let units = self.pend_prev.len() + 1;
        self.abbr
            && self.predict_on
            && self.lm.is_some()
            && self.cursor == self.syls.len()
            && units < ABBR_MAX_UNITS
            && self.syls.len() + units < MAX_SYLLABLES
    }

    fn clear_pend(&mut self) {
        self.pend = [None; 3];
        self.pend_prev.clear();
        self.clear_shadow();
    }

    /// Switch the profile (default chat; remembered even before a model is loaded), recompute the
    /// composition and return the snapshot. On a decode failure the engine resets itself.
    pub fn set_profile(&mut self, profile: Profile) -> Result<Output, EngineError> {
        self.invalidate();
        self.profile = profile;
        self.clear_pred();
        self.close_row_window();
        if let Err(e) = self.refresh() {
            self.clear_all();
            return Err(e);
        }
        self.handled()
    }

    /// How many times the composition was decoded since the engine was made (a test hook: a key that freezes the head of a long stretch
    /// decodes once more than the same key without the freeze).
    pub fn decodes(&self) -> u64 {
        self.decodes
    }

    /// The frozen fixed words as (first syllable, end, word), in composition order (read-only; repick-freeze, composition-cap).
    pub fn frozen_words(&self) -> Vec<(usize, usize, String)> {
        self.fixed.iter().filter(|f| f.frozen).map(|f| (f.start, f.end, f.word.clone())).collect()
    }

    /// Where each token of the current best path starts (a word or a punctuation mark), from the composition's start.
    /// `recompute_pred` takes its long starts from it (V3 engine contract section 11); public so tests can tell a
    /// decoded word's start from a position inside a word.
    pub fn path_starts(&self) -> Vec<usize> {
        let mut pos = 0;
        self.path
            .iter()
            .map(|pw| {
                let s = pos;
                pos += pw.syllables;
                s
            })
            .collect()
    }

    /// Total score of the current best path as `lm.decode` scores one: every word (fixed words with
    /// their capped `lp_F`) adds `word(λ, previous, w, lp) − δ` (δ: the demotion of the entry, 0 when demotion is off), then `eos` of the last word. Punctuation
    /// splits it into sentences (s3d §4): the stretch before it closes with `eos`, the next starts from
    /// `<s>`, and the punctuation itself scores nothing. `None` without a model or without words.
    ///
    /// With the trigram level on (`Lm::scoring`: formal with a table, docs/contracts/trigram-core.md (a)-(c)) every term carries two history
    /// words, as the decoder starts and joins its stretches: `before` is the word before `prev`. A sentence start (`<s>` history, after punctuation,
    /// or after an English run) is (`<s>`, `<s>`); the first word after a fixed word is (unknown, that word), so it is scored by the bigram alone;
    /// every other word is (the word before the previous one, the previous one). A learned-boosted word is the exception (model-v6 §2.8 rule 1
    /// extended): `word_learned`, no table and no Kneser-Ney, as the decoder scores it; it still becomes the history of the next word. The
    /// open English run (`run_cut`) cuts the composition at the cursor the way `refresh_lm` decodes it: the part on the left closes with `eos`
    /// and the part on the right restarts from (`<s>`, `<s>`), like after punctuation. Only the trigram level does this; without it the run is
    /// not a boundary here (the bigram total has never cut there). `total_score` then equals the top decode score of a single stretch.
    /// Slice A2 (b'): a stretch that starts from an unknown u (after a fixed word, or from the left context) and holds exactly one word closes
    /// with the bigram: its `eos`, or the transition into the next fixed word, takes u as unknown (`close_u`), as `decode_segment_learned` does.
    pub fn total_score(&self) -> Option<f64> {
        let st = self.lm.as_ref()?;
        let cfg = st.lm.scoring(self.profile);
        let mut memo = TriMemo::default();
        let cut = self.run_cut().filter(|_| cfg.tri);
        // S2h §1: the first sentence starts from the history word of the left context; `open` is whether
        // the current sentence has a word (the history word is not one, and its own eos is not scored).
        let mut prev = history(&self.left, &st.lm);
        // The word before `prev` for the trigram level (docs/contracts/trigram-core.md (a)-(c)), as the decoder starts a stretch:
        // `<s>` after `<s>`, unknown after a real word (a left context or a fixed word supplies one word only).
        let mut before = (prev == "<s>").then_some("<s>");
        let mut total = 0.0;
        let (mut any, mut open) = (false, false);
        let mut at = 0;
        // (b') whether the stretch started from an unknown u, and how many decoded words it holds so far; a one-word stretch of that kind closes
        // with u unknown.
        let (mut unknown, mut count) = (before.is_none(), 0usize);
        fn close_u<'a>(before: Option<&'a str>, unknown: bool, count: usize) -> Option<&'a str> {
            if unknown && count == 1 { None } else { before }
        }
        for PathWord { word: w, lp, is_punct, delta, syllables: n } in &self.path {
            if cut == Some(at) {
                if open {
                    total += st.lm.eos3_in(&mut memo, cfg, close_u(before, unknown, count), prev);
                }
                (before, prev, open, unknown, count) = (Some("<s>"), "<s>", false, false, 0);
            }
            if *is_punct {
                at += 1;
                if open {
                    total += st.lm.eos3_in(&mut memo, cfg, close_u(before, unknown, count), prev);
                }
                (before, prev, open, unknown, count) = (Some("<s>"), "<s>", false, false, 0);
                continue;
            }
            let n = *n;
            // model-v6 section 2.8 rule 1: a word the learner boosted (lp above its best capped lp under its reading, the test of
            // `boosted`) was scored by the decoder without the Kneser-Ney substitution; score it the same way here.
            let learned = self.syls.get(at..at + n).and_then(|s| st.capped.best_lp(s, w)).is_some_and(|best| *lp > best);
            // A learned word is scored by P2 alone (no Kneser-Ney substitution, no table), as in the decoder.
            // A fixed word is the right neighbour that closes the stretch before it (`End::Next`), so (b') applies to its term.
            let fixed_here = self.fixed.iter().any(|f| f.start == at);
            let u = if fixed_here { close_u(before, unknown, count) } else { before };
            total += if learned { st.lm.word_learned(cfg.lam, prev, w, *lp) } else { st.lm.word3_in(&mut memo, cfg, u, prev, w, *lp) } - delta;
            // The stretch after a fixed word starts from (unknown, that word); inside a stretch the history is the last two words.
            before = if fixed_here { None } else { Some(prev) };
            (unknown, count) = if fixed_here { (true, 0) } else { (unknown, count + 1) };
            at += n;
            (prev, any, open) = (w, true, true);
        }
        any.then(|| if open { total + st.lm.eos3_in(&mut memo, cfg, close_u(before, unknown, count), prev) } else { total })
    }

    /// §6 reset: Commit returns the composition as shown, unfinished symbols included, like Enter (enter-pending
    /// contract §2; no learning here); both clear everything.
    pub fn reset(&mut self, mode: ResetMode) -> Output {
        // Digits.md A.3.4: the buffered digits are tokens before the composition is sent (Commit); Discard drops them with the rest.
        let digits = if mode == ResetMode::Commit { self.digit_flush().unwrap_or_default().0 } else { String::new() };
        let commit = if mode == ResetMode::Commit { digits + &self.shown().0 } else { String::new() };
        self.clear_all();
        self.view(true, commit)
    }

    pub fn key(&mut self, k: Key) -> Result<Output, EngineError> {
        self.pred_dirty = false;
        self.ld_now = self.last_digits.take();
        self.d_emitted = 0;
        self.d_shown = self.drun.as_ref().map_or(0, |r| r.emitted.len() + r.m);
        self.autokept = false; // the one place it is cleared; `insert_english` may set it again below
        // New-word B §2.1: `paused` marks a composition with a key handled while learning was off. A key on an empty
        // composition starts a new one (however the last one emptied: commit, Esc, Backspace), so the mark goes first.
        if self.syls.is_empty() && self.run.is_empty() && self.pending().is_empty() {
            self.paused = false;
        }
        if !self.learning {
            self.paused = true;
        }
        self.cap_snapshot();
        let mut r = self.dispatch(k).and_then(|mut o| self.cap_after(&mut o).map(|()| o));
        match &mut r {
            Err(_) => self.clear_all(),
            Ok(o) if std::mem::take(&mut self.pred_dirty) => {
                self.recompute_pred();
                (o.candidates, o.selected, o.total) = self.pred_view();
                o.vertical = self.pred_vertical();
            }
            Ok(_) => {}
        }
        r
    }

    /// The core's rerank-job condition (S7c-2a section 2.2): the small model is loaded, the switch is on and the demotion table is on.
    fn jobs_on(&self) -> bool {
        self.tinylm.is_some() && self.rerank_on && self.demote
    }

    /// composition-cap §1.2: the window W of characters a free stretch may hold and the buffer under it: the small model's `ctx - 3` (or
    /// `CAP_WINDOW_NO_MODEL`), and `CAP_BUFFER`, except that a window not wider than the buffer (a model with ctx 19 or less; none is made
    /// and none is tested) freezes down to W itself, there being no room for a buffer. `None`: ctx 3 or less, nothing fits any window.
    fn cap_window(&self) -> Option<(usize, usize)> {
        let w = self.tinylm.as_ref().map_or(Some(CAP_WINDOW_NO_MODEL), |m| m.ctx().checked_sub(3)).filter(|&w| w > 0)?;
        Some((w, if w > CAP_BUFFER { CAP_BUFFER } else { 0 }))
    }

    /// composition-cap §1.2 step 1: the path and the syllables before a key or pick runs (kept in `self.cap`), only when the key could leave a
    /// free stretch longer than W: the characters shown plus the most one key adds (a prediction pick of the longest word, plus two) pass W.
    /// Revision 1: with the rerank on or off, with or without the small model; the n-gram model gives the path.
    fn cap_snapshot(&mut self) {
        self.forgot.clear();
        self.cap = None;
        let Some((w, _)) = self.cap_window() else { return };
        if self.lm.is_none() || self.syls.is_empty() || self.display.chars().count() + self.max_window_len() + 2 <= w {
            return;
        }
        self.cap = Some(CapSnap {
            path: self.path.iter().map(|w| (w.word.clone(), w.syllables)).collect(),
            syls: self.syls.clone(),
            generation: self.generation,
            frozen: self.fixed.iter().filter(|f| f.frozen).map(|f| (f.start, f.end)).collect(),
        });
    }

    /// composition-cap §1.2 steps 3 and 4 for the running key: when it decoded (the generation changed) a non-empty composition, freeze the
    /// head of every free stretch longer than W and decode again. Takes the snapshot, so it runs once per key; the two places that rebuild
    /// the prediction row or the window's rows from a decode (the ⌘⌫ forgets) call it right after their own decode, before the rebuild.
    fn cap_freeze(&mut self) -> Result<bool, EngineError> {
        let Some(snap) = self.cap.take().filter(|s| s.generation != self.generation && !self.syls.is_empty()) else { return Ok(false) };
        if !self.cap_refreeze(&snap) {
            return Ok(false);
        }
        self.refresh()?;
        Ok(true)
    }

    /// The part of `key()` and `pick()` after `dispatch`: `cap_freeze`, and when it froze, rebuild `o` from the new state (`view` with the
    /// key's `handled` and `commit`; the caller adds the prediction row).
    fn cap_after(&mut self, o: &mut Output) -> Result<(), EngineError> {
        if self.cap_freeze()? {
            let commit = std::mem::take(&mut o.commit);
            *o = self.view(o.handled, commit);
        }
        Ok(())
    }

    /// composition-cap §1.2 step 3. For every free stretch of the latest decode (`self.free`) of more than W characters: from its start, word
    /// by word (the word across the W - H point too), freeze the words of the path shown before the key until W - H (`CAP_BUFFER`) or fewer
    /// characters are left (counted on the new decode, from the syllable after the last frozen word on). Only words that lie wholly in the
    /// longest common prefix of the syllables before and after the key are frozen, and the freeze stops at the first word that is not, that
    /// lies in a range ⌘⌫ forgot, or that was free before the key and ends less than `CAP_EDGE` syllables before that prefix ends (a word
    /// that was frozen before the key is put back at any distance). Returns whether anything froze.
    fn cap_refreeze(&mut self, snap: &CapSnap) -> bool {
        let Some(st) = self.lm.clone() else { return false };
        let Some((w, buffer)) = self.cap_window() else { return false };
        // The alignment guard of `freeze_outside`, for the path as it was shown (a composition typed before a model was set has none).
        if snap.path.iter().map(|p| p.1).sum::<usize>() != snap.syls.len() || self.path.iter().map(|p| p.syllables).sum::<usize>() != self.syls.len() {
            return false;
        }
        let lcp = snap.syls.iter().zip(&self.syls).take_while(|(a, b)| a == b).count();
        // Characters of the new decode per syllable (a word of m syllables and c characters gives each syllable c / m, the rest to the first).
        let mut chars = vec![0usize; self.syls.len()];
        let mut at = 0;
        for p in &self.path {
            let (m, c) = (p.syllables, p.word.chars().count());
            for j in 0..m {
                chars[at + j] = c / m + usize::from(j < c % m);
            }
            at += m;
        }
        // The old path's words with their syllable ranges.
        let mut old = Vec::with_capacity(snap.path.len());
        let mut at = 0;
        for (word, n) in &snap.path {
            old.push((at, at + n, word));
            at += n;
        }
        let mut frozen = Vec::new();
        for &(from, to) in &self.free {
            let mut left: usize = chars[from..to].iter().sum();
            if left <= w {
                continue;
            }
            let mut next = from;
            while left > w - buffer {
                // The old word that starts here and lies in the stretch and the common prefix.
                let Some(&(a, b, word)) = old.iter().find(|o| o.0 == next) else { break };
                let put_back = snap.frozen.contains(&(a, b));
                let fits = b <= to.min(lcp) && (put_back || b + CAP_EDGE <= lcp) && !self.forgot.iter().any(|&(x, y)| a < y && x < b);
                if !fits || st.capped.best_lp_delta(&self.syls[a..b], word).is_none() {
                    break;
                }
                frozen.push(Fixed::frozen(a, b, word.clone()));
                next = b;
                left = chars[next..to].iter().sum();
            }
        }
        let any = !frozen.is_empty();
        self.fixed.extend(frozen);
        self.fixed.sort_by_key(|f| f.start);
        any
    }

    fn clear_pred(&mut self) {
        self.pred.clear();
        self.pred_sel = None;
    }

    /// Candidate-vertical contract section 2.4: the output value of a prediction row, 2 when the setting is vertical and
    /// there is a row, else 0. Read from the current setting, not fixed at any time: the row has no opening.
    fn pred_vertical(&self) -> u32 {
        if self.cand_vertical && !self.pred.is_empty() { 2 } else { 0 }
    }

    /// The row as output fields (candidates, selected, total); `columns` and `first` stay 0.
    fn pred_view(&self) -> (Vec<String>, Option<usize>, u32) {
        (self.pred.iter().map(|p| p.word.clone()).collect(), self.pred_sel, self.pred.len() as u32)
    }

    /// V3 engine contract sections 1.2, 10.2 and 11: one `predict` per start (decoded word starts, positions inside words,
    /// the cursor), merged and reordered by what was learned.
    /// Empty while off, without a model, when the cursor is not at the end, the candidate window is open or an English run is
    /// open (zhen-inline section 1.3b: Tab then ends the run first, there is no row to enter).
    fn recompute_pred(&mut self) {
        self.clear_pred();
        let n = self.syls.len();
        let (Some(st), true) = (self.lm.clone(), self.predict_on && self.cursor == n && self.cands.is_none() && self.run.is_empty()) else { return };
        let idx = st.capped.predict_index(&st.lm);
        let lam = self.profile.lambda();
        // Digits.md A.3.5: right behind digits (tokens, or buffered keys that show as digits) the cursor start is read in the digit
        // context (v, D); buffered digits are not syllables, so no start in front of them is queried (choosing one would splice over them).
        let (dv, dcache) = match self.digit_pred_v(&st) {
            Some(v) => (Some(v), self.digit_env().map(|e| e.cache)),
            None => (None, None),
        };
        let dtable = self.digit_table.clone();
        let isolated = self.drun.as_ref().is_some_and(|r| r.m >= 1);
        // The unfinished units in order (one without the abbreviation composer); two or more can only read as an abbreviation.
        let pending: Vec<Vec<char>> =
            self.pend_prev.iter().chain([&self.pend]).map(|u| u.iter().flatten().copied().collect::<Vec<_>>()).filter(|u| !u.is_empty()).collect();
        let multi = pending.len() > 1;
        // One unit reads the same in both modes; only two or more can be an abbreviation.
        let mode = if multi { Mode::PA } else { Mode::P };
        // Section 11: the last two path tokens' starts, and the start of every earlier one within `PREDICT_BACK`, far to
        // near. Positions inside words within `PREDICT_BACK` come last, except inside a word the user fixed (`frozen == false`):
        // choosing such an item would take the user's choice (and its pending learn) apart. Inside a frozen word a start is
        // fine (repick-freeze section 11): the pick takes the frozen word apart, which is not the user's choice.
        // Two or more units match nothing that holds a complete syllable (section 12.3): only the cursor start is queried.
        let starts = if multi || isolated { Vec::new() } else { self.path_starts() };
        let k = starts.len();
        let long_starts: Vec<usize> =
            starts.iter().enumerate().filter(|&(i, &s)| i + 2 >= k || s + PREDICT_BACK >= n).map(|(_, &s)| s).collect();
        let mid: Vec<usize> = (n.saturating_sub(PREDICT_BACK)..if multi || isolated { 0 } else { n })
            .filter(|&p| !long_starts.contains(&p) && !self.fixed.iter().any(|f| !f.frozen && f.start < p && p < f.end))
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
            units.extend(pending.iter().map(|chars| Unit { chars: chars.clone(), done: false, tone: None }));
            let digits_here = s == n && dv.is_some() && dcache.is_some() && dtable.is_some();
            if units.is_empty() && !digits_here {
                return Vec::new();
            }
            let before: String = disp[..off(s)].iter().collect();
            let key = context_key(&format!("{}{before}", self.left));
            let v = history(if key == crate::learn::SENTINEL { "" } else { &key }, &st.lm);
            let shown: String = disp[off(s)..off(n)].iter().collect();
            // A start no record can match scans 9 and weighs nothing: its row is the slice-1 one, cheaper.
            // New-word records are no context record (new-word B §2.4): they never open the wider scan.
            let gate = learned
                && (!self.scan_gate
                    || self.learner.records().iter().any(|r| r.context != NEWWORD && reading_matches_in(&units, &r.reading, mode)));
            let limit = if gate { PREDICT_SCAN } else { PREDICT_MAX };
            let hits: Vec<(String, Vec<String>)> = match (&dv, &dcache, &dtable) {
                (Some(dv), Some(c), Some(t)) if digits_here => {
                    predict_digits(idx, &st.lm, t, &c.words, lam, dv, &units, limit).into_iter().map(|(w, _, r)| (w, r)).collect()
                }
                _ => predict(idx, &st.lm, lam, v, &units, mode, limit).into_iter().map(|(w, _, _, r)| (w, r)).collect(),
            };
            hits.into_iter()
                .enumerate()
                .filter_map(|(i, (word, reading))| {
                    // Step 2: keep the slice-1 window and anything learned; the index counts before step 3 filters.
                    let weight = if gate { self.learned_weight(&key, None, &reading, &word, today) } else { 0.0 };
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
        let cursor_items = if pending.is_empty() && dv.is_none() { Vec::new() } else { query(n) };
        // Section 11: the positions inside words come last, far to near.
        let mid_items = mid.into_iter().flat_map(&query);
        let mut all: Vec<Pred> = head.chain(cursor_items).chain(tail).chain(mid_items).collect();
        all.sort_by(|a, b| b.weight.total_cmp(&a.weight)); // step 6: stable
        let mut words = HashSet::new();
        all.retain(|p| words.insert(p.word.clone())); // step 7
        all.truncate(PREDICT_MAX);
        self.pred = all;
    }

    /// Weight of `word` for `reading` under context `key` (rule 8: `next` is the syllable after the word, `None` for a
    /// predicted word, which ends at the cursor) at the exact or last-character level; the global level
    /// counts 0 (s4-learning section 12: it never overrides what the model is sure of).
    fn learned_weight(&self, key: &str, next: Option<&str>, reading: &[String], word: &str, today: i64) -> f64 {
        let key = record_key(key.to_string(), next);
        match self.learner.lookup(&key, reading, today) {
            (Level::Global, _) => 0.0,
            (_, hits) => hits.iter().find(|h| h.0 == word).map_or(0.0, |h| h.1),
        }
    }

    /// Section 2: replace `syls[start..cursor]` by the item's reading, fix the word over it, move the cursor behind
    /// it. With learning on `pre` is `Some("")` (section 10.1): taught at commit like a re-pick, with nothing displaced.
    fn choose_pred(&mut self, i: usize) -> Result<Output, EngineError> {
        let p = self.pred.swap_remove(i);
        self.apply_pred(p)
    }

    /// `choose_pred` for an item already taken out of the row (or out of a window that carried the row, predict-down B.3.1).
    fn apply_pred(&mut self, mut p: Pred) -> Result<Output, EngineError> {
        let digits = self.digit_pre_pick(&mut p)?;
        let mut o = self.apply_pred_at(p)?;
        o.commit.insert_str(0, &digits);
        Ok(o)
    }

    fn apply_pred_at(&mut self, p: Pred) -> Result<Output, EngineError> {
        let Pred { word, reading, start, key, .. } = p;
        let pre = self.learning.then(String::new);
        self.clear_pred();
        self.clear_pend();
        let (m, end) = (reading.len(), self.cursor);
        // repick-freeze section 13: a row pick keeps the words outside `[start, end)` as shown, as a window re-pick does.
        // Rows exist only with the cursor at the end, so no index right of it moves.
        let frozen = self.freeze_outside(start, end);
        self.fixed.retain(|f| !(f.start < end && start < f.end));
        self.fixed.extend(frozen);
        self.syls.splice(start..end, reading);
        let key = pre.is_some().then_some(key);
        self.fixed.push(Fixed { start, end: start + m, word, pre, key, pick_key: None, zh: None, frozen: false, split_off: false, displaced_new: Vec::new() });
        self.fixed.sort_by_key(|f| f.start);
        self.cursor = start + m;
        self.refresh()?;
        if self.syls.len() >= MAX_SYLLABLES {
            let commit = self.take_commit();
            return Ok(self.view(true, commit));
        }
        self.handled()
    }

    /// Also renews the generation (section 2.3): a commit or a reset inside one call (the `MAX_SYLLABLES` auto-commit, a commit from
    /// the prediction row) voids the job the refresh before it made.
    fn clear_all(&mut self) {
        self.invalidate();
        self.left.clear();
        self.syls.clear();
        self.cursor = 0;
        self.clear_pend();
        self.fixed.clear();
        self.display.clear();
        self.path.clear();
        self.cands = None;
        self.run.clear();
        self.run_zh = None;
        self.drun = None;
        self.last_digits = None;
        self.clear_pred();
        self.paused = false;
    }

    fn pending(&self) -> String {
        self.pend_prev.iter().chain([&self.pend]).flatten().flatten().collect()
    }

    /// The composition as shown: the display text with the unfinished symbols at the cursor, the cursor after
    /// them in UTF-16 code units, and the open English run's (start, length) in UTF-16 code units (0, 0 without a run).
    fn shown(&self) -> (String, u32, (u32, u32)) {
        let chars: Vec<char> = self.display.chars().collect();
        // Zhen-inline section 1.2: with D, the unfinished syllable shows as the shadow's letters. Every path that sends what
        // is shown (Enter, `reset(Commit)`, the token that reaches `MAX_SYLLABLES`) comes through here.
        let unfinished: String = if !self.run.is_empty() {
            self.run.clone() // section 1.3b: the English run sits at the cursor
        } else if self.english_inline_shown() {
            self.shadow.iter().collect()
        } else {
            self.pending()
        };
        // A syllable shows as one char (lexicon invariant); a punctuation token as its fixed word,
        // which can be longer (⋯⋯, s3e §3). Clamped in case the invariant ever breaks.
        let at = (0..self.cursor).map(|i| self.token_width(i)).sum::<usize>().min(chars.len());
        let mut text: String = chars[..at].iter().collect();
        // Digits.md A.3.1: the buffered keys that show as digits sit before the unfinished syllable (the tail, mirrored in `pend`).
        if let Some(r) = &self.drun {
            text.extend(&r.buf[..r.m]);
        }
        let run_start = text.encode_utf16().count() as u32;
        text.push_str(&unfinished);
        let cursor_utf16 = text.encode_utf16().count() as u32;
        let run = if self.run.is_empty() { (0, 0) } else { (run_start, cursor_utf16 - run_start) };
        text.extend(chars[at..].iter());
        (text, cursor_utf16, run)
    }

    fn view(&self, handled: bool, commit: String) -> Output {
        let (preedit, cursor_utf16, (english_run_utf16_start, english_run_utf16_len)) = self.shown();
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
        let vertical = match &self.cands {
            Some(c) => c.vertical as u32,
            None => self.pred_vertical(),
        };
        Output { handled, commit, preedit, cursor_utf16, candidates, selected, columns, first, total, vertical, english_run_utf16_start, english_run_utf16_len }
    }

    /// Zhen-inline section 1.3h: where the open English run sits among the units (the cursor), if one is open. The decode treats
    /// it as it does an English token: stretches end and start there, and what follows is not after a Han character.
    fn run_cut(&self) -> Option<usize> {
        (!self.run.is_empty()).then_some(self.cursor)
    }

    /// Recompute the display string: free segments decoded top-1, fixed words in between (§3.1).
    fn refresh(&mut self) -> Result<(), EngineError> {
        self.decodes += 1;
        // Section 2.3, revision 1: every decode is a new generation; its job (if any) is made at the end of `refresh_lm`.
        self.invalidate();
        if let Some(st) = self.lm.clone() {
            return self.refresh_lm(&st);
        }
        let mut out = String::new();
        let mut pos = 0;
        let cut = self.run_cut();
        let seg =|from: usize, to: usize, out: &mut String| -> Result<(), EngineError> {
            // Section 1.3h: an open run splits a stretch, as a token does.
            let mid = cut.filter(|&c| from < c && c < to).unwrap_or(to);
            for (a, b) in [(from, mid), (mid, to)] {
                if a < b {
                    let best = decode_beam(&self.lex, &self.syls[a..b], &mut NoLearning, BEAM_S1)
                        .map_err(|_| EngineError::Internal)?;
                    out.push_str(&best.first().ok_or(EngineError::Internal)?.1.concat());
                }
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
        let cfg = st.lm.scoring(self.profile);
        // The clock (a libc time conversion) is read only when a record could use it.
        let today = if self.learner.is_empty() { 0 } else { self.today() };
        // New-word B §2.4: a fixed word the lexicon lacks is a new word picked in the window; once its record is
        // gone (forgotten, cleared) the span is unfixed and decoded again, never an error.
        if self.fixed.iter().any(|f| f.end - f.start >= NEWWORD_MIN) {
            let (syls, learner, lex) = (&self.syls, &self.learner, &self.lex);
            self.fixed.retain(|f| {
                let r = &syls[f.start..f.end];
                syls[f.start].starts_with(PUNCT_PREFIX)
                    || learner.new_word_day(r, &f.word).is_some()
                    || lex.has(r, &f.word)
            });
        }
        let mut lp_fixed = Vec::with_capacity(self.fixed.len());
        let mut delta_fixed = Vec::with_capacity(self.fixed.len());
        for f in &self.fixed {
            // Punctuation has no reading in the lexicon; 0.0 only keeps `path` aligned (s3d §4).
            let r = &self.syls[f.start..f.end];
            let (lp, delta) = if self.is_punct(f.start) {
                (0.0, 0.0)
            } else if let Some(x) = st.capped.best_lp_delta(r, &f.word) {
                x
            } else {
                // A fixed new word scores S(w) of its record, below ACTIVE too (new-word B §2.4).
                (newword_score(self.learner.new_word_weight(r, &f.word, today).ok_or(EngineError::Internal)?), 0.0)
            };
            lp_fixed.push(lp);
            delta_fixed.push(if self.demote { delta } else { 0.0 });
        }
        let (mut out, mut path) = (String::new(), Vec::new());
        // S7c-2a section 2.2: alpha and tau hold only for the scores with the demotion table on.
        let jobs = self.jobs_on();
        // Whether the job will read the reading-channel term (the same decision as `colloquial` and `channel_lex` at the end of this function): only
        // then do the stretches keep their syllables, so formal, the other strengths, no table and the channel off allocate nothing for it.
        let channel = self.reading_channel
            && self.colloquial.is_some()
            && self.tinylm.as_deref().is_some_and(|m| colloquial::applies(self.profile, m));
        let mut stretches = Vec::new();
        self.free.clear();
        // Byte offset in `out` after the last stretch put into the job: the text from there on is the next stretch's `gap`.
        let mut job_end = 0;
        // Section 1.3h: an open English run is a boundary like a token: `cut` is its place among the units, `cut_out` the byte
        // offset in `out` where it sits (the run's letters are not in `out`).
        let (cut, mut cut_out) = (self.run_cut(), None);
        for gap in 0..=self.fixed.len() {
            let from = if gap == 0 { 0 } else { self.fixed[gap - 1].end };
            let right = self.fixed.get(gap);
            let to = right.map_or(self.syls.len(), |f| f.start);
            if cut == Some(from) {
                cut_out = Some(out.len());
            }
            let mut start = from;
            while start < to {
                // A stretch the run sits inside is decoded in two parts.
                let stop = cut.filter(|&c| start < c && c < to).unwrap_or(to);
                self.free.push((start, stop));
                // The run before the part: what follows is not after a Han character, whatever stands to the left. The run after
                // it: the part ends, and its next neighbour is an English token.
                let (after_run, before_run) = (cut == Some(start), cut == Some(stop));
                // Punctuation is a sentence boundary, as in the counts the model was built from (s3d §4).
                // The first stretch (no fixed word on its left) starts from the left context (S2h §1).
                let prev = match gap.checked_sub(1).map(|g| &self.fixed[g]) {
                    _ if after_run => "<s>",
                    Some(f) if !self.is_punct(f.start) => f.word.as_str(),
                    Some(_) => "<s>",
                    None => history(&self.left, &st.lm),
                };
                let end = match right {
                    _ if before_run => End::Eos,
                    Some(f) if !self.is_punct(f.start) => End::Next { word: &f.word, lp: lp_fixed[gap], delta: delta_fixed[gap] },
                    _ => End::Eos,
                };
                let before = format!("{}{out}{}", self.left, if after_run { self.run.as_str() } else { "" });
                let run_next = before_run.then(|| format!("{PUNCT_PREFIX}{}", self.run));
                let after = run_next.as_deref().or(self.syls.get(stop).map(String::as_str));
                let learn = (!self.learner.is_empty()).then(|| Learn { learner: &self.learner, before: &before, after, today, eps_global: self.eps_global });
                let best = decode_segment_learned(
                    &st.capped, &self.syls[start..stop], &st.lm, cfg, prev, end, BEAM_S1, learn.as_ref(), self.demote,
                )
                .map_err(|_| EngineError::Internal)?;
                let first = &best.first().ok_or(EngineError::Internal)?.1;
                let eligible = jobs && best.len() >= 2;
                let guarded = eligible && self.learn_guard && self.boosted(st, start, first);
                self.guard_skips += usize::from(guarded);
                let in_job = eligible && !guarded;
                if in_job {
                    let top = &best[..best.len().min(TOPK)];
                    let mut gap_text = out[job_end..].to_string();
                    if let Some(o) = cut_out.filter(|&o| o >= job_end) {
                        gap_text.insert_str(o - job_end, &self.run); // the model sees the run between the Chinese on its two sides
                    }
                    stretches.push(Stretch {
                        at: path.len(),
                        paths: top.iter().map(|(_, ws)| ws.iter().map(|&(w, lp, delta, l)| (w.to_string(), lp, delta, l)).collect()).collect(),
                        ng: top.iter().map(|(score, _)| *score).collect(),
                        syls: if channel { self.syls[start..stop].to_vec() } else { Vec::new() },
                        gap: gap_text,
                    });
                }
                for (w, lp, delta, l) in first {
                    out.push_str(w);
                    path.push(PathWord { word: w.to_string(), lp: *lp, is_punct: false, delta: *delta, syllables: *l });
                }
                if in_job {
                    job_end = out.len();
                }
                if before_run {
                    cut_out = Some(out.len());
                }
                start = stop;
            }
            if let Some(f) = right {
                out.push_str(&f.word);
                path.push(PathWord { word: f.word.clone(), lp: lp_fixed[gap], is_punct: self.is_punct(f.start), delta: delta_fixed[gap], syllables: f.end - f.start });
            }
        }
        self.display = out;
        self.path = path;
        if let (false, Some(model)) = (stretches.is_empty(), &self.tinylm) {
            let (params, left) = (model.params(self.profile, self.rerank_level), self.left.clone());
            // The conditions of docs/contracts/colloquial-term.md section 2.2: a table is loaded, and `colloquial::applies` (chat, the
            // measured small model; any strength, docs/contracts/colloquial-levels.md).
            let colloquial = self.colloquial.clone().filter(|_| colloquial::applies(self.profile, model));
            let channel_lex = colloquial.as_ref().filter(|_| self.reading_channel).map(|_| self.lex.clone());
            self.rerank_job = Some(RerankJob { generation: self.generation, model: model.clone(), params, colloquial, level: self.rerank_level, channel_lex, left, stretches });
        }
        Ok(())
    }

    /// S7c-2a section 2.2, the learning guard: whether a word of a stretch's first path (the stretch starts at syllable `from`)
    /// was scored with a learned boost, i.e. with an lp above the best capped lp of that word under its reading (the decoder
    /// raises lp only that way). Such a stretch is not reranked, so a choice the user taught is never undone by the model.
    fn boosted(&self, st: &LmState, from: usize, words: &[(&str, f64, f64, usize)]) -> bool {
        if self.learner.is_empty() {
            return false;
        }
        let mut at = from;
        words.iter().any(|&(w, lp, _, n)| {
            let span = self.syls.get(at..at + n);
            at += n;
            // A span that does not fit cannot happen (one character per syllable); counted as boosted, the safe side.
            // A word the capped lexicon lacks under its reading is a learned new word (new-word B §2.4): taught, so boosted.
            span.is_none_or(|s| st.capped.best_lp(s, w).is_none_or(|best| lp > best))
        })
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
        let (shown, ..) = self.shown();
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
        // Candidate-vertical contract section 2.2: Page Up / Down mean something only in an open vertical window, without
        // modifiers; anywhere else the key changes nothing (not even the prediction row) and is not handled.
        if matches!(k.kind, KeyKind::PageUp | KeyKind::PageDown) && !(m == 0 && self.cands.as_ref().is_some_and(|c| c.vertical)) {
            return self.passthrough(String::new());
        }
        // V3 section 10.3: ⌘⌫ while the prediction row is entered forgets the selected item (before rule 1).
        if let (KeyKind::Backspace, true, Some(sel)) = (k.kind, m & MOD_COMMAND != 0, self.pred_sel) {
            let Pred { word, reading, .. } = &self.pred[sel];
            let (word, reading) = (word.clone(), reading.clone());
            self.note_forgot(&reading);
            self.forget_entry(&reading, &word);
            // The free segments decode without the forgotten record; unlike the candidate window's ⌘⌫ no frozen word
            // is freed (repick-freeze section 7.2).
            self.refresh()?;
            self.cap_freeze()?; // before the row is built from the decode
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
                // Candidate-vertical contract section 2.4: in the vertical row Up / Down move like Left / Right, stopping at both ends.
                (KeyKind::Up, _) if self.cand_vertical => self.pred_sel = Some(sel.saturating_sub(1)),
                (KeyKind::Down, _) if self.cand_vertical => self.pred_sel = Some((sel + 1).min(len - 1)),
                (KeyKind::Tab, _) if m & MOD_SHIFT == 0 => self.pred_sel = Some((sel + 1).min(len - 1)),
                (KeyKind::Tab, _) | (KeyKind::Esc, _) => self.pred_sel = None,
                (KeyKind::Enter, _) => return self.choose_pred(sel),
                _ => self.pred_sel = None,
            }
            if matches!(k.kind, KeyKind::Left | KeyKind::Right | KeyKind::Tab | KeyKind::Esc)
                || (self.cand_vertical && matches!(k.kind, KeyKind::Up | KeyKind::Down))
            {
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
        // Digits.md A.3.4: an open run of digit-alphabet keys sees the key before anything else (the entered row and the Tab
        // that enters it came first; a window cannot be open while a run is).
        if self.drun.is_some() {
            return self.digit_run_key(k);
        }
        // Zhen-inline section 1.3b: an open English run sees the key before anything else.
        if !self.run.is_empty() {
            return self.run_key(k);
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
            // Zhen-inline section 1.3h: English on screen (rule D) or selected in an open English window is kept as a token first,
            // as an auto-keep; what the unit that reaches `MAX_SYLLABLES` sent goes first in this output. Anything else unfinished is dropped.
            let kept = if self.english_open() {
                let sel = self.cands.as_ref().map_or(0, |c| c.sel);
                self.choose_english(sel, true)?;
                self.commit_out.take().unwrap_or_default()
            } else if self.english_inline_shown() {
                let letters = self.shadow.iter().collect();
                self.insert_english(letters, true)?
            } else {
                String::new()
            };
            // s3d §1: into the composition at the cursor, not committed.
            self.clear_pend();
            self.cands = None;
            let mut o = self.insert_token(format!("{PUNCT_PREFIX}{p}"), Some(p.to_string()), None)?;
            o.commit.insert_str(0, &kept);
            return Ok(o);
        }
        let plain = m == 0;
        // Zhen-inline section 1.3b: Shift+letter starts an English run (the letter in upper case, from the keycap and Shift).
        if is_char && m == MOD_SHIFT && k.ch.is_ascii_alphabetic() && self.shadow_on() {
            return self.start_run(k.ch.to_ascii_uppercase());
        }
        let zy = if is_char && plain { self.layout.symbol_of(k.ch) } else { None };
        // Section 1.3: a Zhuyin key that is not an item's digit, with the English window open, keeps the selected item first
        // and then starts the next syllable.
        if let (Some((col, sym)), true) = (zy, self.english_open() && !('1'..='9').contains(&k.ch)) {
            let sel = self.cands.as_ref().map_or(0, |c| c.sel);
            self.choose_english(sel, true)?;
            let commit = self.commit_out.take().unwrap_or_default();
            self.start_syllable(k.ch, col, sym);
            return Ok(self.view(true, commit));
        }
        // Zhen-inline section 1.3h: Backspace with the English window open (letters shown as typed) only closes the window; the
        // shadow stays, so rule D shows the letters again and the next Backspace is section 1.3g's.
        if k.kind == KeyKind::Backspace && self.english_inline && self.english_open() {
            self.cands = None;
            return self.handled();
        }
        // 3-8: candidates open.
        if self.cands.is_some() && self.candidate_key(k)? {
            return self.chosen();
        }
        // Digits.md A.3.4: a digit-alphabet key with nothing unfinished opens a run.
        if let Some(o) = self.digit_start(k)? {
            return Ok(o);
        }
        let tone = match k.kind {
            KeyKind::Space => Some(0),
            KeyKind::Char if plain => self.layout.tone_of(k.ch),
            _ => None,
        };
        let has_pending = self.pend.iter().any(Option::is_some);
        if has_pending {
            // 9-13
            if let Some((col, sym)) = zy {
                // Zhen-inline section 1.3: letters on screen that are a whole word, and a key that cannot go on: keep the word.
                if let Some(word) = self.autokeep_word(k.ch) {
                    let commit = self.insert_english(word, true)?;
                    self.start_syllable(k.ch, col, sym);
                    return Ok(self.view(true, commit));
                }
                // Section 12: an occupied column opens the next unit instead of being replaced.
                if self.pend[col].is_some() && self.abbr_active() {
                    self.pend_prev.push(std::mem::replace(&mut self.pend, [None; 3]));
                }
                self.shadow_push(k.ch, self.pend[col].is_some());
                self.pend[col] = Some(sym);
                self.pred_dirty = true;
            } else if let Some(t) = tone {
                if t == 0 && self.english_trigger() {
                    return self.open_english(); // 10e
                }
                if self.pend_prev.is_empty() {
                    return self.finish_syllable(t, old);
                }
                self.pred = old; // two or more units: tone and space do nothing (section 12.2)
            } else if k.kind == KeyKind::Backspace && self.english_inline_shown() {
                // Zhen-inline section 1.3g: letters are on screen; the last one goes and the rest become an English run.
                let mut letters: String = self.shadow.iter().collect();
                letters.pop();
                self.clear_pend();
                self.run = letters;
                self.pred_dirty = true;
            } else if k.kind == KeyKind::Backspace {
                // Row 11: the last symbol in display order (final, then medial, then initial), as Apple Zhuyin and
                // McBopomofo do (measured 2026-10-06: ㄉㄨㄟ, ㄅ replaces ㄉ, then Backspace gives ㄅㄨ, then ㄅ).
                if let Some(col) = (0..3).rev().find(|&c| self.pend[c].is_some()) {
                    self.pend[col] = None;
                    self.shadow.pop(); // zhen-mixed section 2.2: the last key; the replacement count stays
                }
                if self.pend.iter().all(Option::is_none) {
                    self.pend_prev.pop().into_iter().for_each(|u| self.pend = u); // an emptied unit goes
                    if self.pend.iter().all(Option::is_none) {
                        self.clear_shadow(); // the syllable is gone
                        // Zhen-inline section 1.3i ("show as typed" only): right behind the token an auto-keep made, the token opens
                        // again as a run with all its letters, so `r u s r` + Backspace + `t` is `rust`, not `rus` + ㄔ. A token
                        // chosen on purpose stays (the Backspace fixed a Zhuyin key typed after it).
                        let at = self.cursor.wrapping_sub(1);
                        if self.english_inline && self.shadow_on() && self.cursor > 0
                            && self.fixed.iter().any(|f| f.start == at && f.zh.is_some())
                        {
                            self.reopen_token(at, true)?;
                        }
                    }
                }
                self.pred_dirty = true;
            } else if k.kind == KeyKind::Esc {
                self.clear_pend();
            } else if k.kind == KeyKind::Enter {
                return self.commit_enter(m); // 12a, 12b
            } else if k.kind == KeyKind::Down && self.pend_prev.is_empty() {
                // Predict-down contract B.3.1: one unfinished syllable has no lexicon candidates; the window lists the row
                // (an empty row opens nothing, so the key is ignored as before).
                self.open_with_rows(old);
            } else {
                self.pred = old; // the key does nothing
            }
            return self.handled();
        }
        // 14
        if let Some((col, sym)) = zy {
            self.start_syllable(k.ch, col, sym);
            return self.handled();
        }
        if self.syls.is_empty() {
            // 22a (user report 2026-10-05): a tone key on an empty composition types its mark
            // (ˊ ˇ ˋ ˙) as Apple Zhuyin does, instead of passing the digit on. Unlike Apple, which
            // commits the mark at once, it goes into the composition like punctuation (s3d), so
            // Backspace can still take it back and Enter sends it with the sentence (user's choice).
            if let (KeyKind::Char, Some(t)) = (k.kind, tone) {
                let mark = TONE_MARKS[t];
                return self.insert_token(format!("{PUNCT_PREFIX}{mark}"), Some(mark.to_string()), None);
            }
            return self.passthrough(String::new()); // 22
        }
        let n = self.syls.len();
        match k.kind {
            // Zhen-inline section 1.3e (English mixing on): Space right after an English token or a space unit types a space.
            KeyKind::Space if self.cursor > 0 && self.breaks_with_space(self.cursor - 1) => {
                return self.insert_token(format!("{PUNCT_PREFIX} "), Some(" ".to_string()), None);
            }
            // In front of one (cursor 0) Space opens nothing: the writings list opens with Down only.
            KeyKind::Space if self.cursor == 0 && self.breaks_with_space(0) => self.pred = old,
            KeyKind::Space | KeyKind::Down => self.open_candidates(old),
            KeyKind::Up => self.pred = old,
            KeyKind::Char if tone.is_some() => self.pred = old,
            KeyKind::Right | KeyKind::End if self.cursor == n => self.pred = old, // nothing moves
            KeyKind::Left => self.cursor = self.cursor.saturating_sub(1),
            KeyKind::Right => self.cursor = (self.cursor + 1).min(n),
            KeyKind::Home => self.cursor = 0,
            KeyKind::End => self.cursor = n,
            // Zhen-inline section 1.3g (English mixing on): the unit before the cursor is an English token. It goes and its
            // text without the last letter becomes an English run there (a one-letter token just goes).
            KeyKind::Backspace if self.cursor > 0 && self.shadow_on() && english_of(&self.syls[self.cursor - 1]).is_some() => {
                self.reopen_token(self.cursor - 1, false)?;
            }
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
        if c.vertical {
            // Candidate-vertical contract section 2.2: `first` is the first visible row, nine rows (fewer at the end).
            let first = c.first;
            let last_first = len.saturating_sub(PAGE_SIZE); // the first row that still shows the last candidate in the ninth
            match (k.kind, digit) {
                (KeyKind::Char, Some(d)) => {
                    if first + d < len {
                        self.choose(first + d)?;
                    }
                }
                (KeyKind::Down | KeyKind::Right | KeyKind::Space, _) => {
                    c.sel = (sel + 1).min(len - 1);
                    if c.sel >= first + PAGE_SIZE {
                        c.first = c.sel + 1 - PAGE_SIZE;
                    }
                }
                (KeyKind::Up | KeyKind::Left, _) => {
                    c.sel = sel.saturating_sub(1);
                    c.first = first.min(c.sel);
                }
                (KeyKind::PageDown, _) if first < last_first => {
                    c.first = (first + PAGE_SIZE).min(last_first);
                    // The selection goes to the new first row but never backwards: when the move was clamped the new first
                    // row can be above the selection (10 items, selection on 8: it stays on 8).
                    c.sel = sel.max(c.first);
                }
                (KeyKind::PageUp, _) if first > 0 => {
                    c.first = first.saturating_sub(PAGE_SIZE);
                    c.sel = c.first;
                }
                (KeyKind::PageDown | KeyKind::PageUp, _) => {}
                (KeyKind::Enter, _) => self.choose(sel)?,
                (KeyKind::Esc | KeyKind::Backspace, _) => self.close_cands(),
                _ => {
                    self.close_cands();
                    return Ok(false);
                }
            }
            return Ok(true);
        }
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
                (KeyKind::Esc | KeyKind::Backspace, _) => self.close_cands(),
                _ => {
                    self.close_cands();
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
            (KeyKind::Esc | KeyKind::Backspace, _) => self.close_cands(),
            _ => {
                self.close_cands();
                return Ok(false);
            }
        }
        Ok(true)
    }

    /// s3b2 §8.2 mouse pick: `index` is a position in the last output's `candidates`: the candidate window's, or, with
    /// the window closed, the V3 prediction row's (entered or not). `Ok(None)` when there is neither or `index` is
    /// outside that output (state unchanged).
    pub fn pick(&mut self, index: usize) -> Result<Option<Output>, EngineError> {
        self.cap_snapshot();
        let Some(c) = &self.cands else {
            // V3 section 4: a click on the prediction row selects at once, entered or not.
            if index >= self.pred.len() {
                return Ok(None);
            }
            self.last_digits = None; // digits.md A.3.6: the next state-changing key clears the other reading of the syllable that closed
            let r = self.choose_pred(index).and_then(|mut o| self.cap_after(&mut o).map(|()| o));
            if r.is_err() {
                self.clear_all();
            }
            return r.map(Some);
        };
        let (first, n) = c.window();
        if index >= n {
            return Ok(None);
        }
        self.pred_dirty = false;
        // The frozen head (composition-cap) is part of the pick, before the row is recomputed from the path.
        let mut r = self.choose(first + index).and_then(|()| self.chosen()).and_then(|mut o| self.cap_after(&mut o).map(|()| o));
        if r.is_err() {
            self.clear_all();
        } else if std::mem::take(&mut self.pred_dirty) {
            self.recompute_pred(); // an item of the English window: a Chinese item or an English token (zhen-mixed section 2.3, zhen-inline section 1.0)
            if let Ok(o) = &mut r {
                (o.candidates, o.selected, o.total) = self.pred_view();
                o.vertical = self.pred_vertical();
            }
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
        self.clear_pend();
        self.pred_dirty = true;
        self.insert_token(syl, None, None)
    }

    /// Insert one token (a syllable, or punctuation with its fixed word) at the cursor, shift the
    /// fixed words on its right, recompute; at MAX_SYLLABLES tokens commit everything (s3d §1).
    fn insert_token(&mut self, reading: String, fixed_word: Option<String>, zh: Option<String>) -> Result<Output, EngineError> {
        // repick-freeze section 15: a newly typed syllable frees the pieces a deletion split off, before anything decodes.
        // Punctuation, English tokens and spaces are not syllables and leave them.
        if fixed_word.is_none() {
            self.fixed.retain(|f| !f.split_off);
        }
        let c = self.cursor;
        // repick-freeze section 7.3: a frozen word the insertion cuts is kept per syllable (shifted below like the rest).
        let pieces = self.split_frozen(|f| f.start < c && c < f.end);
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
        let excluded: Vec<usize> = pieces.iter().map(|&s| if s >= c { s + 1 } else { s }).collect();
        self.syls.insert(c, reading);
        if let Some(word) = fixed_word {
            self.fixed.push(Fixed { start: c, end: c + 1, word, pre: None, key: None, pick_key: None, zh, frozen: false, split_off: false, displaced_new: Vec::new() });
            self.fixed.sort_by_key(|f| f.start);
        }
        self.cursor += 1;
        self.release_frozen(c, &excluded)?;
        if self.syls.len() >= MAX_SYLLABLES {
            let commit = self.take_commit();
            return Ok(self.view(true, commit));
        }
        self.handled()
    }

    /// repick-freeze sections 6.1 and 7.4: after a syllable was inserted at `c`, decode (and set `display`/`path`). A
    /// frozen neighbour (left one ending at `c`, right one starting at `c + 1`; not punctuation, not a piece split off
    /// by this insertion, whose post-shift start is in `excluded`) stays fixed unless a word of the decode without it
    /// covers all its syllables plus the new one. Section 11: a released neighbour and the new syllable stay free (a later
    /// syllable can still rewrite them).
    fn release_frozen(&mut self, c: usize, excluded: &[usize]) -> Result<(), EngineError> {
        let is_cand = |s: &Self, f: &Fixed| {
            f.frozen && !s.is_punct(f.start) && !s.is_punct(c) && !excluded.contains(&f.start) && (f.end == c || f.start == c + 1)
        };
        let (cands, rest): (Vec<Fixed>, Vec<Fixed>) = std::mem::take(&mut self.fixed).into_iter().partition(|f| is_cand(self, f));
        self.fixed = rest;
        self.refresh()?; // tentative decode (the only one when there is no candidate)
        let (mut released, mut kept): (Vec<Fixed>, Vec<Fixed>) = cands.into_iter().partition(|f| self.merged(f, c));
        // At most two candidates, so at most two more decodes: put back what does not merge, then recheck (7.4).
        for _ in 0..2 {
            if kept.is_empty() {
                break;
            }
            self.fixed.append(&mut kept);
            self.fixed.sort_by_key(|f| f.start);
            self.refresh()?;
            let (still, lost): (Vec<Fixed>, Vec<Fixed>) = released.into_iter().partition(|f| self.merged(f, c));
            released = still;
            kept = lost;
        }
        Ok(())
    }

    /// Section 6.1: a word of the current path covers all of `f`'s syllables plus the new one at `c`.
    fn merged(&self, f: &Fixed, c: usize) -> bool {
        let (from, to) = if f.end == c { (f.start, c + 1) } else { (c, f.end) };
        let mut pos = 0;
        self.path.iter().any(|w| {
            let (a, b) = (pos, pos + w.syllables);
            pos = b;
            a <= from && to <= b
        })
    }

    /// Sections 7.3 and 9: replace each frozen word `cut` selects by its syllables as single-character frozen words, in
    /// place (before the edit's shift), where the word has one character per syllable and the syllable has that character
    /// as an entry; the rest is left to the decoder. Returns the pieces' starts.
    fn split_frozen(&mut self, cut: impl Fn(&Fixed) -> bool) -> Vec<usize> {
        let mut starts = Vec::new();
        for f in std::mem::take(&mut self.fixed) {
            if !(f.frozen && cut(&f)) {
                self.fixed.push(f);
                continue;
            }
            for p in self.pieces(f.start, f.end, &f.word, None) {
                starts.push(p.start);
                self.fixed.push(p);
            }
        }
        self.fixed.sort_by_key(|f| f.start);
        starts
    }

    /// The single-character frozen words of `word` over `[start, end)` (one character per syllable, and the character an
    /// entry for its syllable), leaving out syllable `skip`; the rest of the word is left to the decoder.
    fn pieces(&self, start: usize, end: usize, word: &str, skip: Option<usize>) -> Vec<Fixed> {
        let Some(st) = &self.lm else { return Vec::new() };
        if word.chars().count() != end - start {
            return Vec::new();
        }
        (start..end)
            .zip(word.chars())
            .filter(|&(i, ch)| Some(i) != skip && st.capped.best_lp_delta(&self.syls[i..=i], &ch.to_string()).is_some())
            .map(|(i, ch)| Fixed::frozen(i, i + 1, ch.to_string()))
            .collect()
    }

    fn remove_syllable(&mut self, i: usize) -> Result<(), EngineError> {
        // repick-freeze section 11: the displayed word that holds syllable `i` (decoded, frozen or user-chosen) keeps its
        // other syllables as single-character frozen words. A user-chosen word itself is removed below, with its pending learn.
        if self.path.iter().map(|p| p.syllables).sum::<usize>() == self.syls.len() {
            let mut p = 0;
            for w in &self.path {
                let q = p + w.syllables;
                if p <= i && i < q && q - p > 1 {
                    let mut pieces = self.pieces(p, q, &w.word, Some(i));
                    pieces.iter_mut().for_each(|f| f.split_off = true);
                    self.fixed.retain(|f| !(f.frozen && f.start == p && f.end == q));
                    self.fixed.extend(pieces);
                    self.fixed.sort_by_key(|f| f.start);
                    break;
                }
                p = q;
            }
        }
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

    /// Zhen-inline section 1.3e: whether unit `i` is an English token or a space unit, the two a Space after it types a space for.
    /// Digits.md A.3.4 adds the digit-string token (and a space unit), when the digit feature is on, without needing the word list.
    fn breaks_with_space(&self, i: usize) -> bool {
        (self.shadow_on() && english_or_space(&self.syls[i])) || (self.digits_active() && (digit_of(&self.syls[i]).is_some() || is_space_unit(&self.syls[i])))
    }

    fn is_punct(&self, i: usize) -> bool {
        self.syls[i].starts_with(PUNCT_PREFIX)
    }

    /// §3.1 candidate range: readings ending at the cursor (or starting at 0 when the cursor is 0),
    /// within the run of syllables that touches it; punctuation bounds the run (s3d §3). An empty
    /// run lists nothing, so space and down are ignored.
    ///
    /// Predict-down contract B.3.1: `rows` is the prediction row shown before this key; the lexicon window lists its items first.
    fn open_candidates(&mut self, rows: Vec<Pred>) {
        let a = self.cursor;
        let avail = self.avail();
        let mut list = Vec::new();
        // s3e §3: right after punctuation (or punctuation first at cursor 0), list the typed mark,
        // then its alternatives.
        let touching = if a == 0 { 0 } else { a - 1 };
        if avail == 0 && touching < self.syls.len() && self.is_punct(touching) {
            if is_space_unit(&self.syls[touching]) {
                return; // zhen-inline section 1.3e: a space unit has no list
            }
            if let Some(word) = english_of(&self.syls[touching]) {
                // zhen-inline sections 1.0 and 1.3c: right after an English token, its writings starting from the text it has now;
                // the punctuation table is not asked. A token an auto-keep made adds the Chinese item of the syllable it remembers.
                let f = self.fixed.iter().find(|f| f.start == touching);
                let mut list: Vec<(String, usize)> = Self::english_writings(f.map_or(word, |f| f.word.as_str())).into_iter().map(|w| (w, 1)).collect();
                let zh = f.and_then(|f| f.zh.clone());
                let zh = zh.and_then(|syl| self.lex.entries(std::slice::from_ref(&syl)).first().map(|(w, _)| (w.to_string(), syl)));
                let mut c = Cands::new(Vec::new(), self.cand_vertical);
                if let Some((word, syl)) = zh {
                    list.push((word, 1));
                    c.zh = Some(syl);
                }
                c.list = list;
                self.cands = Some(c);
                return;
            }
            if let Some(d) = digit_of(&self.syls[touching]) {
                // Digits.md A.3.4 (Down): the row of the digit context first, then the digit and its full-width form, then (A.3.6)
                // the syllable the last digit tokens could have been. The punctuation table is not asked.
                let mut list: Vec<(String, usize)> = rows.iter().map(|p| (p.word.clone(), p.reading.len())).collect();
                list.push((d.to_string(), 1));
                list.push((digit_layer::full_width(d).to_string(), 1));
                let mut c = Cands::new(Vec::new(), self.cand_vertical);
                if let Some((n, syl, word)) = self.digit_syllable_alt(touching + 1) {
                    list.push((word, 1));
                    c.dalt = Some(DigitAlt::Syllable { n, syl });
                }
                c.list = list;
                c.rows = rows;
                self.cands = Some(c);
                return;
            }
            let typed = self.syls[touching][PUNCT_PREFIX.len()..].to_string();
            let alts = typed.chars().next().and_then(|c| self.punct.get(&c)).cloned().unwrap_or_default();
            let mut listed = HashSet::new();
            for w in std::iter::once(typed).chain(alts) {
                if listed.insert(w.clone()) {
                    list.push((w, 1));
                }
            }
            self.cands = Some(Cands::new(list, self.cand_vertical));
            return;
        }
        self.open_with_rows(rows);
    }

    /// The length of the run of syllables that touches the cursor and holds no punctuation (§3.1).
    fn avail(&self) -> usize {
        if self.cursor == 0 {
            (0..self.syls.len()).take_while(|&i| !self.is_punct(i)).count()
        } else {
            (0..self.cursor).rev().take_while(|&i| !self.is_punct(i)).count()
        }
    }

    /// Predict-down contract B.3.1: the window's list: the row's items in their order, then (without an unfinished
    /// syllable, which has no lexicon candidates) the lexicon's entries for the syllables before the cursor, longest
    /// first, minus the words already listed.
    fn window_list(&self, rows: &[Pred]) -> Vec<(String, usize)> {
        let mut seen: HashSet<&str> = rows.iter().map(|p| p.word.as_str()).collect();
        let mut list: Vec<(String, usize)> = rows.iter().map(|p| (p.word.clone(), p.reading.len())).collect();
        if self.pend.iter().any(Option::is_some) {
            return list;
        }
        let a = self.cursor;
        let today = if self.learner.has_new_words() { self.today() } else { 0 };
        for l in (1..=self.lex.max_len.max(NEWWORD_MAX).min(self.avail())).rev() {
            let key = if a == 0 { &self.syls[..l] } else { &self.syls[a - l..a] };
            let lexicon = self.lex.entries(key);
            // New-word B §2.4: new words first among the same length, so ⌘⌫ reaches them (any weight: a record
            // under ACTIVE is still on disk); one the lexicon has is listed where the lexicon puts it.
            let new = if (NEWWORD_MIN..=NEWWORD_MAX).contains(&l) { self.learner.new_words_all(key, today) } else { Vec::new() };
            for w in new {
                if !lexicon.iter().any(|(x, _)| *x == w) && seen.insert(w) {
                    list.push((w.to_string(), l));
                }
            }
            for (w, _) in lexicon {
                if seen.insert(w) {
                    list.push((w.to_string(), l));
                }
            }
        }
        list
    }

    /// Predict-down contract B.3.1: a window that carries row items closes (as Esc) when the row's inputs change under it
    /// (prediction off, another profile); its items' keys and filter were made for the old decoding.
    fn close_row_window(&mut self) {
        if self.cands.as_ref().is_some_and(|c| !c.rows.is_empty()) {
            self.cands = None;
        }
    }

    /// Opens the window on `window_list`; an empty list opens nothing (and the row is gone, as before this contract).
    fn open_with_rows(&mut self, rows: Vec<Pred>) {
        let mut list = self.window_list(&rows);
        // Digits.md A.3.6: right after a syllable the digit keys closed, its digits are the last item.
        let digits = self.ld_now.take().filter(|_| !list.is_empty());
        if let Some(keys) = &digits {
            list.push((keys.clone(), 1));
        }
        if !list.is_empty() {
            let mut c = Cands::new(list, self.cand_vertical);
            c.rows = rows;
            c.dalt = digits.map(DigitAlt::Digits);
            self.cands = Some(c);
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
        Some(self.span_text(start, end))
    }

    /// The text shown now over the syllables [start, end).
    fn span_text(&self, start: usize, end: usize) -> String {
        let off: usize = (0..start).map(|i| self.token_width(i)).sum();
        let len: usize = (start..end).map(|i| self.token_width(i)).sum();
        self.display.chars().skip(off).take(len).collect()
    }

    /// Rule 8: the synthetic key (`^>` + `syls[end]`, `^>$` at the end) the span [start, end) is shown under now, when
    /// the context key before it is `^`; `None` for any other context.
    fn pick_key(&self, start: usize, end: usize) -> Option<String> {
        let off: usize = (0..start).map(|i| self.token_width(i)).sum();
        let before: String = self.display.chars().take(off).collect();
        let ctx = context_key(&format!("{}{before}", self.left));
        (ctx == crate::learn::SENTINEL).then(|| next_key(self.syls.get(end).map(String::as_str)))
    }

    /// §1.5: drop the highlighted candidate's records for its reading (all keys), then re-decode.
    /// With a store the file is always fully rewritten (§4): a record pruned from memory on load can
    /// still be in the file, so "memory changed" says nothing about the file. `must_rewrite` is set
    /// first, so a panic or failure part way makes the next write a full rewrite too.
    fn forget_highlighted(&mut self) -> Result<(), EngineError> {
        let Some(c) = self.cands.as_ref().filter(|c| !c.english) else { return Ok(()) }; // an English item has nothing to forget
        let Some((word, l)) = c.list.get(c.sel).cloned() else { return Ok(()) };
        if c.sel < c.rows.len() {
            // Predict-down contract B.3.1: a row item forgets as in the entered row (§10.3): re-decode, recompute the row
            // (its keys and its "not the shown word" filter follow the new decoding), then the window stays open on the
            // rebuilt list, selected on the same word if it is still there.
            let reading = c.rows[c.sel].reading.clone();
            self.note_forgot(&reading);
            self.forget_entry(&reading, &word);
            self.refresh()?;
            self.cap_freeze()?; // before the rows are built from the decode
            let Some(mut c) = self.cands.take() else { return Ok(()) };
            // Digits.md A.3.6: the items the window had behind the row that are not the lexicon's (the other reading of the digit keys,
            // and after digit tokens the digit and its full-width form) stay its last items; `dalt` still says what the last one is.
            let after_digit_token = self.avail() == 0 && self.cursor > 0 && digit_of(&self.syls[self.cursor - 1]).is_some();
            let kept: Vec<(String, usize)> = match &c.dalt {
                _ if after_digit_token => c.list[c.rows.len()..].to_vec(),
                Some(_) => c.list[c.list.len() - 1..].to_vec(),
                None => Vec::new(),
            };
            self.recompute_pred();
            c.rows = std::mem::take(&mut self.pred);
            c.rows.retain(|p| p.word != word);
            c.list = self.window_list(&c.rows);
            c.list.extend(kept);
            if c.list.is_empty() {
                return Ok(()); // closed, as by Esc
            }
            c.sel = c.list.iter().position(|(w, _)| *w == word).unwrap_or(0);
            self.cands = Some(c.reclamped());
            return Ok(());
        }
        let (start, end) = if self.cursor == 0 { (0, l) } else { (self.cursor - l, self.cursor) };
        if self.is_punct(start) {
            return Ok(());
        }
        let reading = self.syls[start..end].to_vec();
        // repick-freeze section 9: the frozen words over this span decode again only when the forgotten entry is in
        // effect here (exact or last-character level under this span's context; the global level adds nothing).
        let off: usize = (0..start).map(|i| self.token_width(i)).sum();
        let shown_before: String = self.display.chars().take(off).collect();
        let key = context_key(&format!("{}{shown_before}", self.left));
        // Rule 8: under the sentence-start key the record also holds the syllable after the word, as in the decoder.
        let in_effect = self.learned_weight(&key, self.syls.get(end).map(String::as_str), &reading, &word, self.today()) > 0.0;
        self.forgot.push((start, end));
        self.forget_entry(&reading, &word);
        if in_effect {
            self.fixed.retain(|f| !(f.frozen && f.start < end && start < f.end));
        }
        self.refresh()?;
        // New-word B §2.6: a forgotten new word is no longer a candidate, so it leaves the open window (a lexicon
        // word stays: only its learned weight went). The window stays on the item that took its place.
        if self.in_lexicon(&reading, &word) {
            return Ok(());
        }
        let Some(mut c) = self.cands.take() else { return Ok(()) };
        c.list.retain(|(w, n)| !(*w == word && *n == l));
        if c.list.is_empty() {
            return Ok(()); // closed, as by Esc
        }
        c.sel = c.sel.min(c.list.len() - 1);
        self.cands = Some(c.reclamped());
        Ok(())
    }

    /// composition-cap §1.2 step 3: every place `reading` stands in the composition, for the head freeze to leave alone this key.
    fn note_forgot(&mut self, reading: &[String]) {
        let n = reading.len();
        let spots: Vec<(usize, usize)> = (0..(self.syls.len() + 1).saturating_sub(n)).filter(|&p| self.syls[p..p + n] == *reading).map(|p| (p, p + n)).collect();
        self.forgot.extend(spots);
    }

    /// Forget (reading, word) everywhere: pending learns of it are dropped and its records go, then the file is
    /// rewritten. Shared by the candidate window's and the prediction row's ⌘⌫.
    fn forget_entry(&mut self, reading: &[String], word: &str) {
        if self.store.is_some() {
            self.must_rewrite = true;
        }
        // A pending learn of the same word would teach it again at the next commit and append it
        // back (§1.5), as learning_clear's pending-learn drop prevents for clear.
        // New-word B §2.6: forgetting a new word also drops the pending re-pick of every fixed span overlapping any
        // place this reading occurs, or the re-picked character on screen would teach the word again at the Enter.
        let new = self.learner.new_word_day(reading, word).is_some() || !self.in_lexicon(reading, word);
        let syls = &self.syls;
        let n = reading.len();
        let spots: Vec<usize> = if new { (0..(syls.len() + 1).saturating_sub(n)).filter(|&p| syls[p..p + n] == *reading).collect() } else { Vec::new() };
        for f in self.fixed.iter_mut() {
            if (f.word == word && syls[f.start..f.end] == *reading) || spots.iter().any(|&p| f.start < p + n && p < f.end) {
                f.pre = None;
            }
        }
        self.learner.forget(reading, word);
        if std::mem::take(&mut self.forget_panic) {
            panic!("injected forget panic");
        }
        self.rewrite(true);
    }

    /// repick-freeze: from the path displayed before a pick over `[start, end)`, the words outside it (whole ones, and
    /// the outside syllables of a word the range cuts, when that syllable has the shown character as a single-character
    /// entry) as frozen fixed words. Without a model there is no path and nothing freezes.
    fn freeze_outside(&self, start: usize, end: usize) -> Vec<Fixed> {
        let Some(st) = &self.lm else { return Vec::new() };
        if self.path.iter().map(|p| p.syllables).sum::<usize>() != self.syls.len() {
            return Vec::new();
        }
        let mut out = Vec::new();
        let mut p = 0;
        for PathWord { word, syllables: len, .. } in &self.path {
            let q = p + len;
            if q <= start || p >= end {
                if !self.fixed.iter().any(|f| f.start == p && f.end == q) {
                    out.push(Fixed::frozen(p, q, word.clone()));
                }
            } else if word.chars().count() == *len {
                // A word with another character count than syllables has no per-syllable characters: left to the decoder.
                for (i, ch) in (p..q).zip(word.chars()).filter(|(i, _)| !(start..end).contains(i)) {
                    let ch = ch.to_string();
                    if st.capped.best_lp_delta(&self.syls[i..=i], &ch).is_some() {
                        out.push(Fixed::frozen(i, i + 1, ch));
                    }
                }
            }
            p = q;
        }
        out
    }

    /// Fix candidate `idx` over its range, close candidates, recompute.
    fn choose(&mut self, idx: usize) -> Result<(), EngineError> {
        if self.english_open() {
            return self.choose_english(idx, false);
        }
        let Some(mut c) = self.cands.take() else { return Ok(()) };
        if let Some(alt) = c.dalt.take().filter(|_| idx + 1 == c.list.len() && idx >= c.rows.len()) {
            let word = c.list[idx].0.clone();
            let commit = self.choose_digit_alt(alt, word)?;
            if !commit.is_empty() {
                self.commit_out = Some(commit);
            }
            return Ok(());
        }
        // Predict-down contract B.3.1 (revision 2026-10-17): a row item over exactly the typed syllables before the cursor is
        // the lexicon's candidate for that span and falls through to the re-pick below (`list[idx]` is the same word and
        // length); one whose reading is longer or differs (it completes an unfinished unit or an abbreviation, or runs past the
        // typed syllables) is chosen from the row, and the commit of a unit that reaches `MAX_SYLLABLES` waits in `commit_out` for `chosen`, as an English
        // item's does.
        if c.rows.get(idx).is_some_and(|p| p.reading[..] != self.syls[p.start..self.cursor]) {
            let o = self.apply_pred(c.rows.swap_remove(idx))?;
            if !o.commit.is_empty() {
                self.commit_out = Some(o.commit);
            }
            return Ok(());
        }
        let Some((word, l)) = c.list.get(idx).cloned() else { return Ok(()) };
        let (start, end) = if self.cursor == 0 { (0, l) } else { (self.cursor - l, self.cursor) };
        // Choosing again the word a prediction pick fixed over this exact span keeps that pending learn (section 10.1).
        let again = self.fixed.iter().find(|f| f.start == start && f.end == end && f.word == word && f.pre.as_deref() == Some(""));
        let (mut pre, key) = match again {
            Some(f) if self.learning => (f.pre.clone(), f.key.clone()),
            _ => (self.learning.then(|| self.pre_pick(start, end)).flatten(), None),
        };
        let frozen = self.freeze_outside(start, end);
        // New-word B §2.4: picking a new word is no S4 teach (it is counted as used at the commit, §2.5).
        if !self.is_punct(start) && !self.in_lexicon(&self.syls[start..end], &word) {
            pre = None;
        }
        // §2.5: new words of the path this pick overlaps are halved at the commit. The earlier picks this one replaces
        // (every one it overlaps: the retain below drops them) hand on what they displaced, except the word chosen now
        // over this span (chosen back, not away). A pick that keeps the text shown over the span (S4's pre == word)
        // displaces nothing more.
        let mut displaced_new: Vec<(Vec<String>, String)> = Vec::new();
        if self.learning {
            let reading = &self.syls[start..end];
            for x in self.fixed.iter().filter(|f| f.start < end && start < f.end).flat_map(|f| &f.displaced_new) {
                if !(x.0 == reading && x.1 == word) && !displaced_new.contains(x) {
                    displaced_new.push(x.clone());
                }
            }
            if self.span_text(start, end) != word {
                for (s, e, r, w) in self.path_new_words() {
                    if s < end && start < e && !displaced_new.contains(&(r.clone(), w.clone())) {
                        displaced_new.push((r, w));
                    }
                }
            }
        }
        // Re-picking the span of an earlier pick keeps that pick's key along with its displaced text.
        let pick_key = match self.fixed.iter().find(|f| f.start == start && f.end == end && f.pre.as_ref().is_some_and(|p| !p.is_empty())) {
            Some(f) if self.learning => f.pick_key.clone(),
            _ => (self.learning && pre.is_some()).then(|| self.pick_key(start, end)).flatten(),
        };
        // Zhen-inline section 1.3c: another writing of an English token keeps the syllable it remembers; the Chinese item (the
        // last one of a window that has `zh`) turns the token back into that syllable, fixed to the word.
        let mut zh = None;
        if end == start + 1 && english_of(&self.syls[start]).is_some() {
            zh = self.fixed.iter().find(|f| f.start == start).and_then(|f| f.zh.clone());
            if let Some(syl) = c.zh.filter(|_| idx + 1 == c.list.len()) {
                self.syls[start] = syl;
                zh = None;
            }
        }
        self.fixed.retain(|f| !(f.start < end && start < f.end));
        self.fixed.extend(frozen);
        // A candidate pick changes the text before every fixed word to its right: their prediction keys no longer
        // describe what the user sees, so those records fall back to the key at the commit (section 10.1).
        for f in self.fixed.iter_mut().filter(|f| f.start >= end) {
            f.key = None;
        }
        self.fixed.push(Fixed { start, end, word, pre, key, pick_key, zh, frozen: false, split_off: false, displaced_new });
        self.fixed.sort_by_key(|f| f.start);
        self.refresh()
    }
}
