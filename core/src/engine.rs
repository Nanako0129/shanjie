//! S3a key engine (docs/spec/keys-and-composition.md §4.1): key events in, preedit / commit / candidates out.
//! R2: no input text in errors or panics; types holding input text do not derive Debug.

use crate::colloquial::{self, Colloquial, ColloquialTable};
use crate::learn::{context_key, is_single, digit_of, last_chars, english_of, english_or_space, is_han, is_space_unit, fine_key, mid_key, local_day, newword_score, next_key, record_key, Learner, Level, Record, EN_PREFIX, NEXT_PREFIX, SENTINEL, NEWWORD, NEWWORD_BREAK, NEWWORD_DIGITS, NEWWORD_MAX, NEWWORD_MIN, NEWWORD_SHADOW, PUNCT_PREFIX};
use crate::learn_store::{LearnStore, Opened, StoreError, JOURNAL_MAX};
use crate::predict::{
    abbr_cuts, predict_capped, predict_digits_capped, predict_english_capped, reading_matches_in, unit_of_syllable, DigitWords,
    Mode, Unit,
};
use crate::fuzzy::{FUZZY_ADJACENT, FUZZY_ALL, FUZZY_ONE_SYM};
use crate::lm::{decode_segment_fuzzy, history, pool_order, rerank_pool, fuzzy_path, CappedLexicon, DecodedWord, Demote, End, Learn, Lm, Profile, RecurGrams, Scored, Sinit, SinitLayer};
use crate::trigram::TriMemo;
use crate::digits::DigitTable;
use crate::english_next::EnglishTable;
use crate::mixed::MixedTable;
mod auto_english;
pub use auto_english::AutoEvent;
use crate::english_auto::{AutoTable, Params as AutoParams};
mod digit_layer;
use digit_layer::{DRun, DigitAlt, DigitCache, DigitEvent, Late, LateEvent};
use crate::tinylm::{manual_mask, Learning, RerankLevel, TinyLm, TOPK};
use crate::{decode_beam, Lexicon, ManualSet, NoLearning, VariantOrder, BEAM_S1};
use std::collections::{HashMap, HashSet};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

/// docs/spec/semantic-rerank.md §6.2: one counter for the whole process, so a job of one engine never matches another engine, nor an engine
/// built later at the same address.
static GENERATION: AtomicU64 = AtomicU64::new(1);

fn next_generation() -> u64 {
    GENERATION.fetch_add(1, Ordering::Relaxed)
}

/// A word of a job's path as `PathWord` keeps it: (word, lp, demotion delta, syllables, SINIT marker).
type JobWord = (String, f64, f64, usize, bool, bool);

/// docs/spec/learning.md §7.4: `b` of a path, the weight of the learned sentence-start character it starts with (the literal first character of its
/// text, whatever words the path cuts it into); 0 when it starts with none.
fn sinit_b(path: &[DecodedWord], chars: &[(char, f64)]) -> f64 {
    let first = path.first().and_then(|w| w.0.chars().next());
    first.and_then(|c| chars.iter().find(|x| x.0 == c)).map_or(0.0, |x| x.1)
}

/// What `sinit_swap` did to a pool.
#[derive(Debug, PartialEq, Eq)]
enum Swap {
    /// The lifted n-best has no path without a fuzzy word that starts with a learned character: nothing to put in, the pool is as it was.
    Missing,
    /// The first such path is in the pool already (a path with the same text).
    Kept,
    /// That path was not in the pool: it was added to `best` and replaced the pool's last path.
    In,
}

/// A path of a `Sinit::Lift` decode as the unlifted decode (`Sinit::Off`) would have it: the lifted word back to the lp it has without the lift
/// and the score without what the lift added to it (the word's marker holds both, `DecodedWord`). The marker stays on the word.
fn sinit_unlift<'a>(path: &Scored<'a>) -> Scored<'a> {
    let gain: f64 = path.1.iter().filter_map(|w| w.5).map(|m| m.1).sum();
    (path.0 - gain, path.1.iter().map(|&(w, lp, delta, n, fuzzy, mark, mixed)| (w, mark.map_or(lp, |m| m.0), delta, n, fuzzy, mark, mixed)).collect())
}

fn text_of(path: &[DecodedWord]) -> String {
    path.iter().map(|w| w.0).collect()
}

/// docs/spec/learning.md §7.3: makes sure the pool (`rerank_pool`'s indices into the unlifted n-best `best`, two paths at least) holds the best path that
/// starts with a learned sentence-start character and has no fuzzy word, found in `lifted`, the n-best of the decode that lifts the learned
/// characters (the lift only finds it; the path comes into `best` unlifted, with the score and the lps of the unlifted decode, `sinit_unlift`).
/// A path outside the pool takes the place of the pool's last path, never of the first: the pool has two paths at least, so its last one is not
/// the n-gram's first.
fn sinit_swap<'a>(best: &mut Vec<Scored<'a>>, lifted: &[Scored<'a>], pool: &mut [usize], chars: &[(char, f64)]) -> Swap {
    match lifted.iter().find(|(_, ws)| !fuzzy_path(ws) && sinit_b(ws, chars) > 0.0) {
        None => Swap::Missing,
        Some((_, ws)) if pool.iter().any(|&i| text_of(&best[i].1) == text_of(ws)) => Swap::Kept,
        Some(path) => {
            best.push(sinit_unlift(path));
            *pool.last_mut().expect("a pool has two paths") = best.len() - 1;
            Swap::In
        }
    }
}

/// docs/spec/decoder.md §7 and docs/spec/semantic-rerank.md §3.1.1: what a job's stretch keeps of its pool `cands` (the decoder's order, after
/// `sinit_swap`) with the n-gram scores `ng`: the manual mask with `cands[0]` (the decoder's first) as the base (`used`, the readings that path used, is
/// asked for only when `manual` has words), each candidate's pool weight (`RecurGrams::pool_bonus`; all 0 without `recur`), and `lm::pool_order` of the
/// three (empty: not reordered).
fn pool_lists(cands: &[&str], ng: &[f64], used: impl FnOnce() -> Vec<String>, manual: &ManualSet, recur: Option<&RecurGrams>, mixed: Option<&str>) -> (Vec<bool>, Vec<f64>, Vec<usize>) {
    let mut masked = if manual.is_empty() { vec![false; cands.len()] } else { manual_mask(&used(), cands, manual) };
    // docs/spec/decoder.md §7: the Han part of a mixed word the decoder's first path took through the edge (it starts the stretch) is protected for this job
    // only, by the rule of `manual_mask`: a candidate that does not hold it there cannot be chosen.
    if let Some(han) = mixed {
        masked.iter_mut().zip(cands).for_each(|(m, c)| *m |= !c.starts_with(han));
    }
    let bonus: Vec<f64> = cands.iter().map(|c| recur.map_or(0.0, |r| r.pool_bonus(c))).collect();
    let order = pool_order(ng, &bonus, &masked).map_or_else(Vec::new, |(o, _)| o);
    (masked, bonus, order)
}

/// What the sentence-start single-character learning (SINIT, docs/spec/learning.md §7) did in the current decode, counted for the
/// evaluation replay only (`Engine::sinit_stats`): reset with the job.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct SinitStats {
    /// Stretches sent to the semantic correction that have a rule 10 record for their first syllable under the key the stretch starts with.
    pub stretches: usize,
    /// Of those, the stretches whose pool got a learned path swapped in (docs/spec/learning.md §7.3; the path comes from the lifted decode, unlifted).
    pub swaps: usize,
    /// Of those, the stretches whose lifted n-best has no non-fuzzy path starting with a learned character (nothing to swap in).
    pub missing: usize,
    /// Stretches where the lift brought the n-gram's first-second margin under tau, from tau or more. Always 0 now:
    /// the pool and its scores are the unlifted decode's, so the lift cannot open the gate (kept so the replay's `sinit:` line has its five numbers).
    pub tau_opened: usize,
}

/// One free stretch of a rerank job (docs/spec/semantic-rerank.md §6.1). Owned data only. Every per-candidate list is in the decoder's order
/// (the pool after `sinit_swap`); `order` says how the rerank sees them.
struct Stretch {
    /// Index in `Engine::path` of the stretch's first word.
    at: usize,
    /// The first `TOPK` paths of the n-best: (word, lp, demotion delta, syllables, SINIT marker), as `path` holds them. `paths[0]` is what `path` holds at
    /// `at..at + len` when the job is made; apply checks that it still does (docs/spec/semantic-rerank.md §6.2).
    paths: Vec<Vec<JobWord>>,
    /// Their n-gram scores (the decoder's, without `bonus`).
    ng: Vec<f64>,
    /// docs/spec/semantic-rerank.md §3.1.1: each path's context recurrence weight on the pool (`RecurGrams::pool_bonus` of its text; all 0 when the
    /// term does not apply). Numbers made from the recurrence set when the job is made; the set itself never goes into a job.
    bonus: Vec<f64>,
    /// `lm::pool_order` of `ng`, `bonus` and `masked`: the index in `paths` of the rerank's k-th candidate. Empty: the rerank sees the decoder's order.
    order: Vec<usize>,
    /// `tinylm::manual_mask` of the paths with `paths[0]` as the base (docs/spec/decoder.md §7), made once when the job is made.
    masked: Vec<bool>,
    /// Their SINIT weights `b_i = w / (w + 1)` (docs/spec/learning.md §7.4): the record weight of the sentence-start single character the candidate starts with, else 0,
    /// of the records of `layer`. All 0 for almost every stretch.
    b: Vec<f64>,
    /// The longest layer the stretch has a record under (fine `^>` plus the next three syllables, mid the next two, coarse the next one), so `b` is that layer's
    /// (docs/spec/learning.md §7.4; the layers are never added).
    layer: SinitLayer,
    /// The shown text between the previous stretch of the job (or the composition's start) and this one: fixed words, and the
    /// n-gram text of stretches left out of the job.
    gap: String,
}

/// What the small model has to rerank after one change of the composition (docs/spec/semantic-rerank.md §6.1): only owned data and the model, never
/// a borrow of the engine, so it can run on another thread and outlive the engine. Holds input text, so no `Debug`.
pub struct RerankJob {
    generation: u64,
    model: Arc<TinyLm>,
    /// The profile's (alpha, tau, delta) at the engine's rerank level when the job was made (docs/spec/semantic-rerank.md §7): an own
    /// Copy value, so a later level or profile change never reaches a job already made (it is stale anyway).
    params: (f64, f64, f64),
    /// The colloquial term (docs/spec/semantic-rerank.md §4), decided when the job was made: `Some` only for chat (any
    /// strength) and the small model the term was measured with. `None`: the plain `rerank` with `params`.
    colloquial: Option<Arc<Colloquial>>,
    /// The engine's rerank level when the job was made (docs/spec/semantic-rerank.md §4.4): the colloquial term uses that
    /// level's (alpha, tau, delta), and its fallback the header triple of that level. Copy, like `params`.
    level: RerankLevel,
    /// 1, or the tests' override (`Engine::set_eps_sinit`) when the job was made: the factor on `EPS_SINIT_FINE`, `EPS_SINIT_MID` and `EPS_SINIT_COARSE`, the sizes of the learning term of the fused score.
    eps_sinit: f64,
    /// The engine's left context when the job was made: its Han tail (`Engine::left`), or for a model declaring a left-context length L
    /// the last L characters of `Engine::left_full` (docs/spec/semantic-rerank.md §5.2; nothing longer is copied into a job).
    left: String,
    stretches: Vec<Stretch>,
}

/// One stretch's choice: index into the job's paths, and that path when it is not the first.
struct Choice {
    at: usize,
    /// The words `path` must still hold at `at..` (the n-gram first path the job was made from).
    expect: Vec<String>,
    pick: usize,
    words: Vec<JobWord>,
    /// The context key the stretch was scored with.
    key: String,
    /// A SINIT learned candidate led the stretch (`b_0 > 0`) and the choice is one with none (`b == 0`): the semantic correction undid the learning.
    overturned: bool,
    /// Evaluation only: for a stretch with a SINIT learned candidate the small model scored, (`ng[0] - ng[i]`, `alpha * (ll[0] - ll[i])`) of the
    /// best-ranked learned candidate `i` (the anchor itself: (0, 0)); `alpha` is the strength's cell when the colloquial term applies, else the header's.
    sinit_diag: Option<(f64, f64)>,
}

/// The output of `RerankJob::run`, for `Engine::apply_rerank`. Holds input text, so no `Debug`.
pub struct RerankResult {
    generation: u64,
    choices: Vec<Choice>,
}

impl RerankJob {
    /// docs/spec/semantic-rerank.md §6.3: `TinyLm::rerank` on every stretch in order; a candidate longer than the model's context keeps the first.
    /// A stretch's context key is `context_key` of the left context and the text before it as it will be shown,
    /// i.e. with the earlier stretches' chosen candidates (one stretch: the n-gram text before it, as before). A model declaring a
    /// left-context length L (SJTL0005) gets the last L characters of that same text instead, not through `context_key`
    /// (`TinyLm::key_for_len`, docs/spec/semantic-rerank.md §5.2).
    ///
    /// docs/spec/semantic-rerank.md §3.1.1: with a non-empty `order` the rerank sees the candidates, words, scores `ng + bonus`, `b` and `masked` in that
    /// order (put together here, once per stretch): the gate, the colloquial term, the fusion, delta and the learning term all take the new first
    /// candidate as the anchor, and the chosen position is mapped back to a `paths` index (a position outside `order` is no choice: the new first).
    /// An error of the small model keeps `paths[0]`, the decoder's first, before any mapping. With an empty `order` every list is the stretch's own.
    pub fn run(&self) -> RerankResult {
        let mut before = self.left.clone();
        let choices = self
            .stretches
            .iter()
            .map(|s| {
                before.push_str(&s.gap);
                let surfaces: Vec<String> = s.paths.iter().map(|p| p.iter().map(|w| w.0.as_str()).collect()).collect();
                // The rerank's view: `paths` indices in the order the rerank sees them, and the lists in that order.
                let view: Vec<usize> = if s.order.is_empty() { (0..s.paths.len()).collect() } else { s.order.clone() };
                let ng: Vec<f64> = if s.order.is_empty() { s.ng.clone() } else { view.iter().map(|&i| s.ng[i] + s.bonus[i]).collect() };
                let b: Vec<f64> = view.iter().map(|&i| s.b.get(i).copied().unwrap_or(0.0)).collect();
                let masked: Vec<bool> = view.iter().map(|&i| s.masked.get(i).copied().unwrap_or(false)).collect();
                let cands: Vec<&str> = view.iter().map(|&i| surfaces[i].as_str()).collect();
                // The key of a boundary-format model depends on the longest candidate the rerank scores (docs/spec/semantic-rerank.md §5.2).
                let key = self.model.key_for_len(&before, cands.iter().take(TOPK).map(|c| c.chars().count()).max().unwrap_or(0));
                let learn = Learning { layer: s.layer, b: b.clone(), scale: self.eps_sinit };
                let alpha = if self.colloquial.is_some() { colloquial::cell(self.level).0 } else { self.params.0 };
                let picked = match &self.colloquial {
                    Some(co) => {
                        let words: Vec<Vec<&str>> = view.iter().map(|&i| s.paths[i].iter().map(|w| w.0.as_str()).collect()).collect();
                        self.model.rerank_colloquial(&ng, &cands, &words, &key, co, self.level, &masked, &learn)
                    }
                    None => self.model.rerank(&ng, &cands, &key, self.params, &masked, &learn),
                };
                // (`paths` index, position in the view, the LLs); an error keeps the decoder's first and is no choice of the model.
                let (pick, at, ll) = match picked {
                    Ok((p, ll)) => match view.get(p) {
                        Some(&i) => (i, p, ll),
                        None => (view[0], 0, ll),
                    },
                    Err(_) => (0, 0, None),
                };
                let sinit_diag = ll.as_ref().and_then(|ll| {
                    let i = b.iter().position(|&b| b > 0.0)?;
                    Some((ng[0] - ng[i], alpha * (ll[0] - ll[i])))
                });
                let pick = if pick < s.paths.len() { pick } else { 0 };
                before.push_str(&surfaces[pick]);
                let words = if pick == 0 { Vec::new() } else { s.paths[pick].clone() };
                let expect = s.paths[0].iter().map(|w| w.0.clone()).collect();
                let overturned = b.first().is_some_and(|&b0| b0 > 0.0) && b[at] == 0.0;
                Choice { at: s.at, expect, pick, words, key, overturned, sinit_diag }
            })
            .collect();
        RerankResult { generation: self.generation, choices }
    }

    /// Test hook: every stretch's `lm::pool_order` (indices into the candidates of `candidates()`, the rerank's first candidate first; empty when the
    /// pool is not reordered). Rust only, not in shanjie.h.
    pub fn pool_order(&self) -> Vec<Vec<usize>> {
        self.stretches.iter().map(|s| s.order.clone()).collect()
    }

    /// Test hook: every stretch's context recurrence weight on the pool (`RecurGrams::pool_bonus`), in the candidates' order. Rust only, not in shanjie.h.
    pub fn pool_bonus(&self) -> Vec<Vec<f64>> {
        self.stretches.iter().map(|s| s.bonus.clone()).collect()
    }

    /// Every stretch's candidate texts, in the job's order (the n-gram first, then the rest of the pool): what the small model is asked
    /// to choose among. The tests read it; the job does not change.
    pub fn candidates(&self) -> Vec<Vec<String>> {
        self.stretches.iter().map(|s| s.paths.iter().map(|p| p.iter().map(|w| w.0.as_str()).collect()).collect()).collect()
    }

    /// Every stretch's SINIT weights `b_i`, in the candidates' order (docs/spec/learning.md §7.4); the tests read them, the job does not change.
    pub fn sinit_weights(&self) -> Vec<Vec<f64>> {
        self.stretches.iter().map(|s| s.b.clone()).collect()
    }

    /// Every stretch's n-gram scores of its candidates, in the candidates' order (what the unlifted pool of docs/spec/learning.md §7.2 is tested against); the tests
    /// read them, the job does not change.
    pub fn ng_scores(&self) -> Vec<Vec<f64>> {
        self.stretches.iter().map(|s| s.ng.clone()).collect()
    }

    /// The most characters any candidate of any stretch has (composition-cap: at most `ctx - 3`, so none ends in `TooLong`).
    pub fn max_candidate_chars(&self) -> usize {
        self.stretches.iter().flat_map(|s| &s.paths).map(|p| p.iter().map(|w| w.0.chars().count()).sum()).max().unwrap_or(0)
    }

    /// Test hook (the FFI panic test): the job's left context and every candidate's text, so an injected panic payload carries input text.
    #[cfg(test)]
    pub(crate) fn texts(&self) -> String {
        let cands: String = self.stretches.iter().flat_map(|s| s.paths.iter().flatten().map(|w| w.0.as_str())).collect();
        format!("{}{cands}", self.left)
    }
}

impl RerankResult {
    /// The chosen index of each stretch, in composition order (0 = the n-gram first).
    pub fn picks(&self) -> Vec<usize> {
        self.choices.iter().map(|c| c.pick).collect()
    }

    /// How many stretches the semantic correction took a SINIT learned candidate away from (`Choice::overturned`); the evaluation replay counts it.
    pub fn sinit_overturned(&self) -> usize {
        self.choices.iter().filter(|c| c.overturned).count()
    }

    /// Evaluation only (`shanjie-replay --sinit-stretches`): `Choice::sinit_diag` of every stretch that has one, in composition order.
    pub fn sinit_diag(&self) -> Vec<(f64, f64)> {
        self.choices.iter().filter_map(|c| c.sinit_diag).collect()
    }

    /// The context key each stretch was scored with, in composition order (holds input text: never log it).
    pub fn keys(&self) -> Vec<String> {
        self.choices.iter().map(|c| c.key.clone()).collect()
    }
}

// docs/spec/semantic-rerank.md §6.3: the thread guarantees come from the types themselves, never from an `unsafe impl`.
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
/// everything (docs/spec/keys-and-composition.md §8.2). 128 since docs/spec/keys-and-composition.md §5: past the small model's window (`TinyLm::ctx`), the head of a
/// long free stretch is frozen (`cap_refreeze`) so the rerank still sees a stretch that fits.
pub const MAX_SYLLABLES: usize = 128;
pub const PAGE_SIZE: usize = 9;
/// Rows visible in the expanded grid (a-4: five, with a scroll bar).
pub const GRID_ROWS: usize = 5;
/// docs/spec/prediction.md §3.1: at most this many items from the long starts (the decoded word starts)
/// go ahead of the cursor start's items; the rest of them follow. Chosen from a measured trade-off:
/// the first key keeps its own candidates, the second syllable still finds the long word.
pub const PREDICT_LONG_CAP: usize = 3;
/// docs/spec/prediction.md §3.1: decoded word starts this many syllables back are long starts too, and the positions
/// inside words this far back are queried last. Five is the chosen value; long names in the ACG pack were measured
/// at 4, 5 and 6.
pub const PREDICT_BACK: usize = 5;
/// Most unfinished units the abbreviation composer holds (docs/spec/prediction.md §6). The longest names in the ACG pack
/// are 10 characters and an abbreviation only helps up to the longest word. At this cap (or completed syllables plus units == 128),
/// with two or more units a key that would open a new unit is eaten with no change; with one unit the usual rules apply (rule 9).
pub const ABBR_MAX_UNITS: usize = 10;
/// Items in the prediction row.
pub const PREDICT_MAX: usize = PAGE_SIZE;
/// Items asked of `predict` for a start that some learned record could match (docs/spec/prediction.md §3.3 step 1).
/// A taught situation's items sit in the first `PREDICT_MAX`, a variant of it up to this far. Any other start asks for
/// `PREDICT_MAX` only, so with an empty learner the row is the slice-1 row bit for bit. The added time of 27 over 9
/// is only measured as part of the whole row with a 50,000-record store, not by itself.
pub const PREDICT_SCAN: usize = 27;
/// Most characters an English run holds (docs/spec/english-mixed.md §4.1); the next one ends the run and starts another.
pub const ENGLISH_RUN_MAX: usize = 32;
/// docs/spec/english-mixed.md §2: the key record holds at most this many keys, so the record and the key or tone digit that reverts
/// it never exceed a run.
pub const ENGLISH_RECORD_MAX: usize = ENGLISH_RUN_MAX - 1;
/// docs/spec/english-mixed.md §6: the short words that can be typed in column order and whose one-tone syllable is in the lexicon, so no
/// trigger ever reverts them; the candidate list adds their lower and upper case at its end. Each was checked with `Lexicon::entries`
/// (test `english_short_is_as_documented`). `go`, `cp`, `so`, `to`, `do`, `hi`, `am`, `zi` and `cup` are not here: their syllable is
/// missing, so the space key reverts them.
pub const ENGLISH_SHORT: &[&str] = &["ai", "ui", "up", "co", "hp", "jp", "mp", "mp3", "np", "rm", "xl"];

/// Initial (21), medial (3), final (13) symbols in the layout's column order.
const SYMBOLS: &str = "ㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓㄔㄕㄖㄗㄘㄙㄧㄨㄩㄚㄛㄜㄝㄞㄟㄠㄡㄢㄣㄤㄥㄦ";
const KEYS_STANDARD: &str = "1qaz2wsxedcrfv5tgbyhnujm8ik,9ol.0p;/-";
const KEYS_ETEN: &str = "bpmfdtnlvkhg7c,./j;'sexuaorwiqzy890-=";
/// Tone marks for tone 1..5 (tone 1 is unmarked).
const TONE_MARKS: [&str; 5] = ["", "ˊ", "ˇ", "ˋ", "˙"];
const TONE_KEYS_STANDARD: [char; 4] = ['6', '3', '4', '7'];
const TONE_KEYS_ETEN: [char; 4] = ['2', '3', '4', '1'];
/// Shift + key -> punctuation (docs/spec/keys-and-composition.md §8.1). The bracket row follows Apple's Zhuyin
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
/// Punctuation layout 1 (`set_punct_layout`, docs/spec/keys-and-composition.md §8.1): the cells that differ from layout 0, from the McBopomofo lexicon
/// `data/lexicon/mcbpmf-data.txt` (`_punctuation_Standard_<c>`, else `_punctuation_<c>`, first row; `c` the character the US keyboard gives for the key).
/// A key not listed here is layout 0's. The test `punct_layout_1_cells_are_the_lexicon_rows` reads the file.
const SHIFT_PUNCT_1: [(char, char); 8] = [('\'', '；'), ('2', '＠'), ('3', '＃'), ('4', '＄'), ('5', '％'), ('6', '︿'), ('7', '＆'), ('8', '＊')];
const PLAIN_PUNCT_1: [(char, char); 2] = [('\'', '、'), ('\\', '＼')];

/// All 27 non-Zhuyin symbol keys controlled by `Engine::set_half_punct` (bits 0..=26).
pub const HALF_PUNCT_ALL_MASK: u32 = (1u32 << 27) - 1;
/// Default 14 half-width punctuation keys when partial half-width punctuation is enabled:
/// bits 0..=8 (`~ ! @ # $ % ^ & *`), 11 (`_`), 12 (`+`), 15 (`|`), 21 (`` ` ``), 22 (`=`).
pub const HALF_PUNCT_DEFAULT_MASK: u32 = 0x0060_99FF;

struct HalfPunctEntry {
    bit: u32,
    keycap: char,
    ascii: char,
    full0: char,
    full1: char,
    sec: Option<char>,
}

const HALF_PUNCT_SHIFT: [HalfPunctEntry; 21] = [
    HalfPunctEntry { bit: 0,  keycap: '`',  ascii: '~', full0: '～', full1: '～', sec: None },
    HalfPunctEntry { bit: 1,  keycap: '1',  ascii: '!', full0: '！', full1: '！', sec: None },
    HalfPunctEntry { bit: 2,  keycap: '2',  ascii: '@', full0: '＠', full1: '＠', sec: None },
    HalfPunctEntry { bit: 3,  keycap: '3',  ascii: '#', full0: '＃', full1: '＃', sec: None },
    HalfPunctEntry { bit: 4,  keycap: '4',  ascii: '$', full0: '＄', full1: '＄', sec: None },
    HalfPunctEntry { bit: 5,  keycap: '5',  ascii: '%', full0: '％', full1: '％', sec: None },
    HalfPunctEntry { bit: 6,  keycap: '6',  ascii: '^', full0: '＾', full1: '︿', sec: None },
    HalfPunctEntry { bit: 7,  keycap: '7',  ascii: '&', full0: '＆', full1: '＆', sec: None },
    HalfPunctEntry { bit: 8,  keycap: '8',  ascii: '*', full0: '＊', full1: '＊', sec: None },
    HalfPunctEntry { bit: 9,  keycap: '9',  ascii: '(', full0: '（', full1: '（', sec: None },
    HalfPunctEntry { bit: 10, keycap: '0',  ascii: ')', full0: '）', full1: '）', sec: None },
    HalfPunctEntry { bit: 11, keycap: '-',  ascii: '_', full0: '＿', full1: '＿', sec: None },
    HalfPunctEntry { bit: 12, keycap: '=',  ascii: '+', full0: '＋', full1: '＋', sec: None },
    HalfPunctEntry { bit: 13, keycap: '[',  ascii: '{', full0: '『', full1: '『', sec: Some('｛') },
    HalfPunctEntry { bit: 14, keycap: ']',  ascii: '}', full0: '』', full1: '』', sec: Some('｝') },
    HalfPunctEntry { bit: 15, keycap: '\\', ascii: '|', full0: '｜', full1: '｜', sec: None },
    HalfPunctEntry { bit: 16, keycap: ';',  ascii: ':', full0: '：', full1: '：', sec: None },
    HalfPunctEntry { bit: 17, keycap: '\'', ascii: '"', full0: '“', full1: '；', sec: None },
    HalfPunctEntry { bit: 18, keycap: ',',  ascii: '<', full0: '，', full1: '，', sec: Some('＜') },
    HalfPunctEntry { bit: 19, keycap: '.',  ascii: '>', full0: '。', full1: '。', sec: Some('＞') },
    HalfPunctEntry { bit: 20, keycap: '/',  ascii: '?', full0: '？', full1: '？', sec: None },
];

const HALF_PUNCT_PLAIN: [HalfPunctEntry; 6] = [
    HalfPunctEntry { bit: 21, keycap: '`',  ascii: '`',  full0: '·', full1: '·', sec: Some('｀') },
    HalfPunctEntry { bit: 22, keycap: '=',  ascii: '=',  full0: '＝', full1: '＝', sec: None },
    HalfPunctEntry { bit: 23, keycap: '[',  ascii: '[',  full0: '「', full1: '「', sec: Some('［') },
    HalfPunctEntry { bit: 24, keycap: ']',  ascii: ']',  full0: '」', full1: '」', sec: Some('］') },
    HalfPunctEntry { bit: 25, keycap: '\\', ascii: '\\', full0: '、', full1: '＼', sec: None },
    HalfPunctEntry { bit: 26, keycap: '\'', ascii: '\'', full0: '‘', full1: '、', sec: None },
];
/// Option + key and Option + Shift + key -> the character rule 1o puts into the composition (docs/spec/keys-and-composition.md §8.1), position by position
/// the keys of `OPTION_KEYS`. Apple's Zhuyin, measured 2026-11-20 in a VM (macOS 26.6.2) with HID-level key events, every cell.
const OPTION_KEYS: &str = "`1234567890-=[]\\;',./";
const OPTION_PLAIN: &str = "｀１２３４５６７８９０—＝「」、；‘，．／";
const OPTION_SHIFT: &str = "～！＠＃＄％＾＆＊（）＿＋『』｜：“《》？";

/// Rule 1o's character for the keycap `ch` (as the shell sends it: US, unshifted), or none for a key outside the rule. Letters give the full-width
/// lower-case letter, with Shift the upper-case one (measured for a and u; the other letters follow the same pattern, not measured).
fn option_char(ch: char, shift: bool) -> Option<char> {
    if ch.is_ascii_lowercase() {
        return char::from_u32(ch as u32 - 'a' as u32 + if shift { 0xFF21 } else { 0xFF41 });
    }
    let i = OPTION_KEYS.chars().position(|c| c == ch)?;
    (if shift { OPTION_SHIFT } else { OPTION_PLAIN }).chars().nth(i)
}

/// docs/spec/keys-and-composition.md §8.3: built-in punctuation alternatives, used until `set_punctuation` succeeds. From
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
/// docs/spec/keys-and-composition.md §8.3 limits on a table passed to `set_punctuation`.
const PUNCT_TABLE_MAX_BYTES: usize = 64 * 1024;
const PUNCT_TABLE_MAX_LINES: usize = 1000;

/// docs/spec/semantic-rerank.md §5.2: `text` ends, after at most one ASCII space, with 2-32 ASCII letters not preceded by an ASCII letter or
/// digit. Returns the character index where the word starts and the text from there (the word and that space). Only ASCII decides.
fn english_tail(text: &str) -> Option<(usize, String)> {
    let body = text.strip_suffix(' ').unwrap_or(text);
    let n = body.chars().rev().take_while(char::is_ascii_alphabetic).count();
    if !(2..=32).contains(&n) || body.chars().rev().nth(n).is_some_and(|c| c.is_ascii_digit()) {
        return None;
    }
    let at = body.chars().count() - n;
    Some((at, text.chars().skip(at).collect()))
}

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

/// The fuzzy flags the decoder, the window and the learning guard read (docs/contracts/typo-one-sym.md §2.4 item 1): the Eten layout has no standard
/// keyboard rows, so it never reads the adjacent-key bit; every other bit, and the standard layout, as set.
pub fn effective_fuzzy_for(layout: Layout, flags: u32) -> u32 {
    if layout == Layout::Eten { flags & !FUZZY_ADJACENT } else { flags }
}

/// Which trigger of the direct revert fired (docs/spec/english-mixed.md §3.2; tool hook `Engine::english_reverted`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Revert {
    /// (a) a key over the symbol already in its column.
    Overwrite,
    /// (b) a key in a column before one that is filled.
    Order,
    /// (c) a space or tone key that completes a syllable the lexicon lacks.
    Missing,
    /// The closing key of a syllable the lexicon has, read as English by the comparison of docs/spec/english-mixed.md §16.
    Auto,
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
    /// Only the vertical candidate window takes these (docs/spec/macos-shell.md §8.6.2); the shell sends them
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
    /// The unfinished syllable goes first (every unit of the abbreviation composer, what Esc clears under rule 12), then as `Commit`
    /// (docs/spec/keys-and-composition.md §7.2; the shell's switch to English where the ASCII state cannot keep the composition, §9.2).
    CommitFinished,
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
    /// docs/spec/macos-shell.md §8.6.2: 0 horizontal (or no candidates), 1 the vertical candidate window (fixed
    /// when it opened), 2 the vertical prediction row, entered or not (it follows the setting on every output, docs/spec/macos-shell.md §8.6.4).
    pub vertical: u32,
    /// docs/spec/english-mixed.md §4.6: the open English run in `preedit`, start and length in UTF-16 code units; both 0 when no run
    /// is open.
    pub english_run_utf16_start: u32,
    pub english_run_utf16_len: u32,
}

/// What `Engine::en_cache` builds: the best entry of every word of the English table, for one (lexicon, model, table).
struct EnWords {
    key: (usize, (u64, usize, usize)),
    words: DigitWords,
}

/// One word of the displayed best path.
struct PathWord {
    word: String,
    lp: f64,
    /// The decoded word matched a rule 10 record (`DecodedWord.5` is set). Its lp is its own entry's: the pool is the unlifted decode (docs/spec/learning.md §7.2),
    /// so the marker is information about the path, not what the learning guard or `total_score` decide by.
    sinit: bool,
    is_punct: bool,
    delta: f64,
    /// Syllables the word covers (punctuation is 1).
    syllables: usize,
    /// The Han part of a mixed word, put out through the decoder's edge (`DecodedWord.6`, docs/spec/decoder.md §4.7): `lp` is the edge's whole score.
    mixed: bool,
}

/// docs/spec/prediction.md §3.5: the key of an English completion in the row of an open run (no record is ever kept under it).
const EN_WORD_KEY: &str = "^enword";

/// docs/spec/keys-and-composition.md §5.3: the window W (characters) when no small model is loaded, so the head freeze does not depend on one: the shipping
/// model's `ctx` 64 minus 3. With a model loaded W is its `ctx - 3`.
const CAP_WINDOW_NO_MODEL: usize = 61;
/// docs/spec/keys-and-composition.md §5.3: once a free stretch is longer than W the freeze goes on down to W - H characters, so a head that has left the
/// window is not decoded again and only about one key in H decodes twice (measured: with H = 0 almost every key past W froze one more word and
/// decoded twice, 26 ms against 13 to 17 expected).
const CAP_BUFFER: usize = 16;

/// docs/spec/keys-and-composition.md §5.3 step 3: a word that was not frozen before the key is not frozen when it ends less than this many syllables
/// before the longest common prefix of the syllables before and after the key, i.e. right at the edit. Typing in the middle of a long stretch
/// would otherwise freeze the syllable just typed as a single character on the next key (Home, ㄉㄧㄢˋ, ㄋㄠˇ showed 店腦), and a word of
/// several syllables could not form any more. Four is the longest word the typing is likely still completing (`PREDICT_BACK` is five for
/// the prediction row). Words that were already frozen are put back whatever their distance (release-then-refreeze, tests 3 and 4).
const CAP_EDGE: usize = 4;

/// What `cap_snapshot` keeps of the state before a key (docs/spec/keys-and-composition.md §5.3 step 1).
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
    /// `Some("")` is a prediction pick (docs/spec/learning.md §4.3): nothing was displaced, so nothing halves.
    pre: Option<String>,
    /// The context key a prediction pick is recorded under, fixed when the row was built: re-decoding after the pick can
    /// change the text before the start, so the commit must not recompute it (docs/spec/learning.md §4.3). `None` for candidate picks.
    key: Option<String>,
    /// Fixed by the engine, not a setting (docs/spec/keys-and-composition.md §6.3): the words outside a candidate re-pick or a
    /// prediction pick (docs/spec/keys-and-composition.md §6.3), and the pieces a Backspace leaves of a deleted word (docs/spec/keys-and-composition.md §6.5). Never learned.
    /// Released when a newly typed syllable merges with it into a lexicon word (docs/spec/keys-and-composition.md §6.4; the merged word decodes
    /// freely since docs/spec/keys-and-composition.md §6.4) and when the candidate window's ⌘⌫ forgets an entry in effect over a span it overlaps
    /// (docs/spec/keys-and-composition.md §6.6); the Backspace pieces also when any syllable is typed (`split_off`, docs/spec/keys-and-composition.md §6.5).
    frozen: bool,
    /// A frozen single character a Backspace or Delete left of the word it deleted from (docs/spec/keys-and-composition.md §6.5). Any newly typed
    /// syllable drops all of these before it decodes (docs/spec/keys-and-composition.md §6.5); nothing else does.
    split_off: bool,
    /// The synthetic sentence-start keys the displaced word was shown under when this candidate pick was made, when the
    /// span's context key was `^`: `^>` + the next reading, and for a single character the mid and fine keys the syllables after it
    /// made then (docs/spec/learning.md §7.1). The commit halves the displaced word under each one that is not a key of the commit
    /// (the user may have typed on after the pick). Empty otherwise.
    pick_keys: Vec<String>,
    /// docs/spec/english-mixed.md §3.2 (docs/spec/english-mixed.md §3.3, §5.2): for an English token a revert made, the unfinished syllable (tone 1) the
    /// keys would have made when the lexicon has it, so the token can be turned back into that Chinese syllable. `None` for every other fixed word.
    zh: Option<String>,
    /// docs/spec/learning.md §8.5: the new words (reading, word) of the path this pick displaced, chosen while learning was on;
    /// each is halved at a commit with learning on.
    displaced_new: Vec<(Vec<String>, String)>,
    /// docs/spec/english-mixed.md §5.7: for an English token, the mixed word behind it. Default for every other fixed word.
    mixed: MixedMark,
    /// docs/spec/english-mixed.md §4.4: for an English token that shows the table's writing of the English part (the path shown takes the mixed word behind
    /// it), the text it had before: it comes back when the path no longer takes the mixed word. `None` for every other fixed word.
    own: Option<String>,
    /// docs/spec/english-mixed.md §4.3: for the token a split at the closing key made, when the keys of the run (with the tone key that closed it) are an
    /// English word the core knows: those keys and the syllable the split made. The window lists the word while that syllable alone stands
    /// behind the token with the cursor at the end (`mixed_sweep` drops it otherwise).
    whole: Option<(String, String)>,
    /// Partial half-width punctuation (`set_half_punct`): unit inserted via `half_punct_of`, so `open_candidates` expands it to its full-width alternatives.
    from_half_punct: bool,
}

/// docs/spec/english-mixed.md §5.7: the mixed word behind one English token.
#[derive(Clone, Copy, Default, PartialEq)]
struct MixedMark {
    /// The user chose the token's writing, or the English word a split took apart: the table's word is no candidate behind this token again.
    dead: bool,
    /// The syllables behind the token came from the row of the open English run with learning on (docs/spec/learning.md §4.3), this many: recorded at the
    /// commit when the text shown over the token and them is that row's writing. 0: no such pick.
    picked: usize,
}

impl Fixed {
    /// A word the engine fixed on its own (docs/spec/keys-and-composition.md §6.3): not learned, not a user choice.
    fn frozen(start: usize, end: usize, word: String) -> Self {
        Fixed { start, end, word, pre: None, key: None, pick_keys: Vec::new(), zh: None, frozen: true, split_off: false, displaced_new: Vec::new(), mixed: MixedMark::default(), own: None, whole: None, from_half_punct: false }
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

/// docs/spec/keys-and-composition.md §4.1.1 rule 16u: what the first Up press of a run fixed. The run goes on only while the syllables and the cursor are
/// as the last press left them and the character before the cursor is the one it put there; anything else starts a new run.
struct UpRun {
    syls: Vec<String>,
    cursor: usize,
    /// The character shown at the first press, then the single characters of the syllable in §6.1 order.
    base: Vec<String>,
    /// The positions in `base` the next presses take (2, then 1, those that exist); empty: the next press opens the window.
    steps: Vec<usize>,
    /// The character the last press put before the cursor.
    shown: String,
}

struct Cands {
    /// (word, length in syllables)
    list: Vec<(String, usize)>,
    sel: usize,
    expanded: bool,
    /// First visible grid row while expanded; keeps the selected row inside `GRID_ROWS`. Set to the
    /// selected page on expand, so the first row is the page the collapsed bar showed (docs/spec/macos-shell.md §8.4).
    top: usize,
    /// docs/spec/macos-shell.md §8.6.2: the orientation at the moment the window opened. A vertical window
    /// never expands; `first` is the list position of its first visible row.
    vertical: bool,
    first: usize,
    /// docs/spec/keys-and-composition.md §4.3: the window opened under key scheme 1 (`set_key_scheme`), fixed when it opened like `vertical`. A vertical one then
    /// moves by whole pages (`candidate_key_ms`), so `first` is always a multiple of `PAGE_SIZE`.
    ms: bool,
    /// docs/spec/english-mixed.md §6: the last two items are the lower and upper case of a short word (`ENGLISH_SHORT`) the syllable at
    /// this position was typed as; choosing one swaps that one syllable for an English token. Judged by the item's index, never by
    /// a length field.
    short: Option<(usize, String)>,
    /// Fuzzy input (docs/spec/decoder.md §6.2): list position -> the lexicon reading of a word that was found
    /// through a tolerant reading. Choosing such a word rewrites the span to that reading. Empty without fuzzy input.
    alt: HashMap<usize, Vec<String>>,
    /// docs/spec/english-mixed.md §3.3, §5.2: the window of an English token that a revert made (docs/spec/english-mixed.md §3.2); its last item is the
    /// Chinese syllable the keys typed, and this is that syllable's reading (the token's reading is replaced by it when it is chosen).
    zh: Option<String>,
    /// docs/spec/english-mixed.md §5.7: the list's mixed-word item, its first, as it stands in `list` (the writing, and the English
    /// token plus its syllables as the length). No lexicon item has that length (the candidate range stops at the token), so the
    /// pair names the item; a window that has it carries no prediction row items.
    mixed: Option<(String, usize)>,
    /// docs/spec/prediction.md §5: the prediction row's items the window opened with. They are the first `rows.len()`
    /// entries of `list` (the length field of a list entry cannot mark them). Choosing one whose
    /// reading is longer than or differs from the typed syllables is choosing it from the row; one over exactly those
    /// syllables is a re-pick (`choose`). They go with the window when it closes.
    rows: Vec<Pred>,
    /// docs/spec/english-mixed.md §7.7: the last item is not a lexicon entry but the other reading of digit keys (the digits of a syllable
    /// that just closed, or the syllable of the last digit tokens). Judged by the item's index, never by a length field.
    dalt: Option<DigitAlt>,
}

impl Cands {
    fn new(list: Vec<(String, usize)>, vertical: bool, ms: bool) -> Cands {
        Cands { list, sel: 0, expanded: false, top: 0, vertical, first: 0, ms, short: None, alt: HashMap::new(), zh: None, mixed: None, rows: Vec::new(), dalt: None }
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
        if self.ms && self.vertical {
            self.first = self.sel / PAGE_SIZE * PAGE_SIZE; // whole pages (§4.3)
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

/// docs/spec/english-mixed.md §2: a Zhuyin replay (`Engine::replay`): the syllables completed, then the unfinished syllable's columns,
/// shadow and key record.
#[derive(Default)]
struct Replay {
    done: Vec<String>,
    pend: [Option<char>; 3],
    shadow: [Option<Shadow>; 3],
    typed: String,
}

/// A shadow entry: the keycap and its arrival rank (see `Engine::shadow_key`).
type Shadow = (char, u8);

/// What an engine hands over to build the next one without reading the model files again (docs/spec/c-abi.md §5.4, `Engine::share`,
/// `Engine::new_shared`): the loaded n-gram model (its `.sjlm` model, `classes.sjc`, `kn.sjkn` and the two trigram tables `trigram*.sjt3`
/// inside it are read-only memory maps), the small model, the colloquial table (its `.sjlm` is a read-only memory map),
/// the reading table, the digit table and the English follower table, all `Arc`s, plus the data folder. It owns no user input and borrows
/// nothing from the engine, so it outlives it, and it never changes after it is made.
pub struct Shared {
    data_dir: PathBuf,
    lm: Arc<Lm>,
    tinylm: Option<Arc<TinyLm>>,
    colloquial: Option<Arc<ColloquialTable>>,
    reading: Option<Arc<crate::reading::ReadingDecode>>,
    /// The source engine's `pack_single`: its reading table counts the pack rows of its own mask.
    src_pack_single: bool,
    digit_table: Option<Arc<DigitTable>>,
    en_table: Option<Arc<EnglishTable>>,
    auto_table: Option<Arc<AutoTable>>,
}

impl Shared {
    /// The reading table the source engine decodes with (tests).
    pub fn reading_table(&self) -> Option<&Arc<crate::reading::ReadingDecode>> {
        self.reading.as_ref()
    }
}

/// The addresses `Engine::part_ptrs` reports (0 for a part the engine has none of).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PartPtrs {
    pub lm: usize,
    pub tinylm: usize,
    pub colloquial: usize,
    pub reading: usize,
    pub digit_table: usize,
    pub en_table: usize,
}

/// Whether any row of the packs' text reads one syllable (so a one-character word, or a one-syllable entry, that the reading table would see).
/// Rows are `reading TAB word TAB score TAB tag`; a row that does not parse is the lexicon's error, not decided here.
fn pack_has_single(pack_text: &str) -> bool {
    pack_text.lines().any(|l| l.split('\t').next().is_some_and(|r| !r.is_empty() && !r.contains('-')))
}

pub struct Engine {
    lex: Arc<Lexicon>,
    /// Where `new` read the data from; `None` for `with_lexicon` engines (they cannot `load_lm`).
    data_dir: Option<PathBuf>,
    /// The enabled packs' rows `new` read (docs/spec/lexicon-and-packs.md §4.1), appended to `overlay-add.tsv` when
    /// `load_lm` caps; `None` with no pack, so a pack-off engine holds nothing extra.
    pack_text: Option<String>,
    /// How many leading bytes of `pack_text` are the rows of the enabled `PACK_OOV` packs, which `load_lm` exempts from the cap.
    /// They are a prefix because `read_packs_split` reads the `PACK_OOV` packs first; a length, not a copy, so the engine holds the
    /// rows once.
    pack_oov_len: usize,
    /// Whether the enabled packs hold a row with a one-syllable reading (`pack_has_single`): what decides if a reading table can be reused
    /// (`Engine::new_shared`).
    pack_single: bool,
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
    /// docs/spec/prediction.md §6: with the abbreviation composer on, the unfinished units before `pend` (the last one),
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
    /// Key scheme (`set_key_scheme`): 0 the rules of docs/spec/keys-and-composition.md §4.1 (default), 1 the `m` rows. A candidate window keeps the one it opened with (`Cands::ms`).
    key_scheme: u8,
    /// Punctuation layout (`set_punct_layout`): 0 Apple Zhuyin's (default), 1 McBopomofo's.
    punct_layout: u8,
    /// Partial half-width punctuation mask (`set_half_punct`): bits 0..=26 (`HALF_PUNCT_ALL_MASK`), default 0 (off).
    half_punct_mask: u32,
    /// docs/spec/keys-and-composition.md §4.1.1 rule 16u: the run of Up presses in a composition under key scheme 1, set by each swap. Every key past rule 1 and
    /// 5q that is not Up clears it, and so does the third press (it opens the window); a call that is not a key is caught by `UpRun`'s check.
    up_run: Option<UpRun>,
    /// Whether a non-advancing symbol opens a new unfinished unit (default off; `set_abbreviation`, docs/spec/prediction.md §6).
    abbr: bool,
    /// Whether each prediction query skips candidates longer than its queried unit count (default off; `set_candidate_length_cap`, docs/spec/prediction.md §3.3).
    cand_len_cap: bool,
    /// Test hook: false queries `PREDICT_SCAN` at every start once the learner has records (no gate).
    scan_gate: bool,
    /// Set by the key rules that recompute the row; `key` recomputes after the rule ran.
    pred_dirty: bool,
    /// S4: the Han tail (≤ 2 chars) of the text before the insertion point, from `set_left_context`.
    left: String,
    /// docs/spec/semantic-rerank.md §5.2: only what the small model or the English table can use, from the same text: its last L characters
    /// when the loaded small model declares L, else its trailing English word (and one space after it), else empty. Never returned over
    /// the C ABI, written, logged or sent anywhere; cleared with `left` (`clear_all`) and after a rule 22 pass-through.
    left_full: String,
    /// `left_full` holds the last L characters of a longer text (it was cut), so a word starting at its first character may be half
    /// of one.
    left_cut: bool,
    /// The context recurrence set (docs/spec/decoder.md §4.6): the Han pairs of the last `lm::RECUR_CHARS` characters of the
    /// text `set_left_context` was given, rebuilt (not added to) by every call, with the number of scalar values that cut held. This engine's own, passed
    /// to the decoder as an argument: never in a structure engines share (`Lm`, the lexicons), never in a `RerankJob` (a job carries only the pool
    /// weights `RecurGrams::pool_bonus` made from it and the order they give, docs/spec/semantic-rerank.md §3.1.1), never returned over the C ABI,
    /// written, logged or sent anywhere. Cleared with `left` (`clear_left`).
    recur: RecurGrams,
    /// Whether the decoder gets `recur` (default on, the App's behavior). Off only from `set_recur`, an evaluation switch for
    /// `shanjie-replay --no-recur`; not in shanjie.h. Off also turns the pool weight off.
    recur_on: bool,
    /// Whether the rerank jobs get the pool weight of docs/spec/semantic-rerank.md §3.1.1 (default on, the App's behavior). Off only from
    /// `set_pool_recur`, an evaluation switch for `shanjie-replay --no-pool-recur`; not in shanjie.h.
    pool_recur_on: bool,
    /// `set_left_context` ran since the last `key()`: the key that comes next may start a composition with that text. Taken by every `key()`;
    /// a key on an empty composition without it clears the left text first (`key`).
    left_fresh: bool,
    learning: bool,
    /// docs/spec/learning.md §8.1: learning was off at some point of this composition, so its commit learns no new word. Set by
    /// `set_learning(false)` while composing and by any key handled with learning off; cleared by `clear_all` and by
    /// the first key on an empty composition (`key`).
    paused: bool,
    /// Test hook: false turns new-word B's learning off (S4 alone), the baseline of `measure_newword`.
    newword: bool,
    /// Variant B of new-word learning (`NEWWORD_SUBSPANS`); a test hook switches it for measurement.
    subspans: bool,
    /// ε of the global learning level (docs/spec/learning.md §5.4); a test hook sweeps it.
    eps_global: f64,
    /// SINIT: the factor on the fused score's learning term sizes (`lm::EPS_SINIT_FINE`, `lm::EPS_SINIT_MID`, `lm::EPS_SINIT_COARSE`), 1; `set_eps_sinit` overrides it for the tests.
    eps_sinit: f64,
    /// What SINIT did in the current decode (`sinit_stats`); reset with the job in `invalidate`.
    sinit: SinitStats,
    learner: Learner,
    store: Option<LearnStore>,
    /// docs/spec/learning.md §10.4 bit0: the last full rewrite failed. Set by a failed full rewrite; cleared only by a
    /// successful full rewrite or clear. While it is set, `must_rewrite` is set too.
    write_failed: bool,
    /// docs/spec/learning.md §10.3: the next write must be a full rewrite. Set before a forget or clear touches memory and
    /// before every full rewrite; cleared only when a full rewrite's rename (or a clear) succeeds, so
    /// a failure or a panic part way leaves it set.
    must_rewrite: bool,
    /// docs/spec/learning.md §10.3: day of the last successful full rewrite; `None` after open and after a clear, so the first
    /// write then, and the first write of each day, is a full rewrite.
    last_full: Option<i64>,
    /// docs/spec/learning.md §10.3: lines appended since the last full rewrite.
    appended: usize,
    /// Test-purpose clock (day number); `None` uses the local calendar day.
    today: Option<i64>,
    /// Test-purpose injection: the next learning pass panics (docs/spec/learning.md §13.1).
    learn_panic: bool,
    /// Test-purpose injection: the next ⌘⌫ forget panics after changing memory (docs/spec/learning.md §13.1).
    forget_panic: bool,
    /// S7c-2a: the small character model, from `load_tinylm`; dropped when another n-gram model is loaded (its `.sjtl` names
    /// the n-gram model's hash).
    tinylm: Option<Arc<TinyLm>>,
    /// The colloquial table and the word sets of its k term (`load_colloquial`); independent of the n-gram and the small model.
    /// Whether a job uses it is decided per job in `refresh_lm`.
    colloquial: Option<Arc<Colloquial>>,
    /// Whether the decoder adds the reading term (docs/spec/decoder.md §5; default on, the App's behavior). Off only from
    /// `set_reading_decode`, an evaluation switch for `shanjie-replay --no-reading-decode`; not in shanjie.h.
    reading_decode: bool,
    /// Whether rerank jobs are made (default off since S7c-2c, docs/spec/semantic-rerank.md §1.2; `set_rerank`).
    /// `load_tinylm` leaves it as it is.
    rerank_on: bool,
    /// Which (alpha, tau, delta) of the small model the jobs use (docs/spec/semantic-rerank.md §7; default standard;
    /// `set_rerank_level`). Kept across `load_tinylm` and `load_lm`, and remembered without a model; a version 1 or 2 file has only
    /// the standard triple, which every level then gets.
    rerank_level: RerankLevel,
    /// The learning guard (docs/spec/semantic-rerank.md §6.1) (default on, the app's behavior). Off only from `set_learn_guard`, an evaluation
    /// switch for `shanjie-replay --learn-guard` (docs/spec/evaluation.md §4.3); not in shanjie.h.
    learn_guard: bool,
    /// Stretches the guard kept out of the current decode's job (`guard_skips`); reset with the job in `invalidate`.
    guard_skips: usize,
    /// Stretches the fuzzy-sound rule kept out of the current decode's job (`fuzzy_skips`): their n-gram first path has a fuzzy word. Reset
    /// with the job in `invalidate`.
    fuzzy_skips: usize,
    /// docs/spec/semantic-rerank.md §6.2: renewed from `GENERATION` only where decoding changes: every `refresh`, `clear_all`, the model and
    /// decoding settings (`load_lm`, `set_lm`, `load_tinylm`, `load_colloquial`, `set_rerank`, `set_rerank_level`, `set_profile`, `set_demote`,
    /// learning open/clear, the evaluation-only `set_learn_guard`, `set_reading_decode` and `set_recur`).
    generation: u64,
    /// The job of the last refresh, until `take_rerank_job` or the next change.
    rerank_job: Option<RerankJob>,
    /// zhen-mixed: the switch (default off; `set_english`).
    english: bool,
    /// docs/spec/english-mixed.md §2, §3.1: the keycap that put the symbol now in each column of the unfinished syllable, with its arrival rank
    /// (zhen-reapply: with the reversal guard on a key can go into a column before a filled one, so every English text reads the keys in
    /// the order typed, not the column order; a key that replaces a column's key is the newest). Recorded only where `shadow_on`, so it
    /// always matches `pend`; empty (all `None`) otherwise. Cleared with `clear_pend`.
    shadow_key: [Option<Shadow>; 3],
    /// docs/spec/english-mixed.md §2: the key record: every Zhuyin key of the unfinished syllable in the order typed, the keys a later
    /// key replaced in its column included (at most `ENGLISH_RECORD_MAX`). The English text of every revert, Shift+letter and ASCII
    /// run. Recorded and cleared with the shadow; Backspace on the syllable resets it to the shadow's keys.
    typed: String,
    /// docs/spec/english-mixed.md §1.1, §1.2, §3.2: the triggers of the direct revert (a key over the same column: `set_english_overwrite`; a key out of
    /// column order and a tone key that makes a syllable the lexicon lacks: `set_english_key_order`). Both default on. The
    /// out-of-order trigger (b) fires only while `english_key_order` is on and `english_reversal_guard` is off.
    english_overwrite: bool,
    english_key_order: bool,
    /// docs/spec/english-mixed.md §1.1, §3.2: while on (default), a key in a column before a filled one is never English (trigger (b) does not fire,
    /// whatever `english_key_order` says): `set_english_reversal_guard`.
    english_reversal_guard: bool,
    /// docs/spec/keys-and-composition.md §9.3: the ASCII state (`set_ascii`), default off. A mode like a setting: `reset` and `clear_all` keep it.
    ascii: bool,
    /// Set by a choice from the candidate window (a digit alternative, a row item, a short word's English token) whose unit reached
    /// `MAX_SYLLABLES` and sent everything: the committed text, for the output of the key or pick that made it.
    commit_out: Option<String>,
    /// Whether and how the latest `key()` reverted to the raw keys, and whether it split an English run at a tone key (tool hooks
    /// `english_reverted`, `english_split`); cleared at the start of `key()` and nowhere else.
    reverted: Option<Revert>,
    split: bool,
    /// docs/spec/english-mixed.md §3.3, §4.2: what the unfinished syllable was before the revert (c) at a tone key that opened the
    /// English run (columns, shadow and key record), for the tone moved up; dropped by anything that changes the run.
    revert_point: Option<([Option<char>; 3], [Option<Shadow>; 3], String)>,
    /// docs/spec/english-mixed.md §6: the position and text of the syllable that was just made from a short word's keys, and what the
    /// current key saw of it (`ls_now`, like `ld_now`). Taken at the start of every `key()`; a key that changes nothing puts it back.
    last_short: Option<(usize, String)>,
    ls_now: Option<(usize, String)>,
    /// docs/spec/english-mixed.md §16: the core switch of the English-or-Zhuyin comparison (default on; nothing happens without the table and a model).
    auto_on: bool,
    auto_table: Option<Arc<AutoTable>>,
    auto_params: AutoParams,
    /// Of the unfinished syllable: keys replaced by the same keycap, whether the reversal guard took a key out of column order, and whether it is
    /// not a decision point at all (Esc brought it back as Zhuyin). They go with the shadow.
    pend_dup: u32,
    pend_rev: bool,
    pend_plain: bool,
    /// The token the comparison made at Space, for the Esc right after it: (position, the keys typed, the syllable they are today). Lives as `last_short` does.
    last_auto: Option<(usize, String, String)>,
    la_now: Option<(usize, String, String)>,
    /// Tool hooks, cleared at every `key()`: (n_ow, n_dup, n_rev) when the key closed a syllable at a decision point; the comparison when one was made.
    auto_point: Option<(u32, u32, u32)>,
    auto_event: Option<AutoEvent>,
    /// Set by the arms of `dispatch` that change nothing (`Engine::nothing`, `mark_noop`), only when the arm runs in the outermost
    /// `dispatch` of the key (`depth` 1); `key()` reads it once, after the key, to give the short-word mark back. The one place that
    /// rule lives (docs/spec/english-mixed.md §6).
    noop: bool,
    /// Set by rule 1o when it eats the key (docs/spec/keys-and-composition.md §4.1): handled, and nothing changes. `key()` reads it once, after the key, to put back
    /// what it took at its top for the key to see (`late`, `last_digits`); the short-word marks come back through `noop`.
    eaten: bool,
    /// How many `dispatch` calls the running key is inside: a key that ends a run or a digit buffer first and then dispatches itself again
    /// calls `dispatch` a second time, and whatever the inner call does nothing about, the key as a whole changed something.
    depth: u32,
    /// Fuzzy input flags (`set_fuzzy`, default 0): bits 0-3 the sound pairs, bit 4 adjacent-key slips, bit 5 one-symbol slips; the decoder reads `effective_fuzzy`.
    fuzzy: u32,
    /// Third-tone and colloquial sandhi (`set_tone3`, default on; docs/spec/decoder.md §6.6): the switch handed to `tone3::positions`, and whether the `colloq` rows are read.
    tone3: bool,
    /// docs/spec/english-mixed.md §4.1: the English run being typed (Shift+letter starts it): letters shown at the cursor, not yet a
    /// unit. Empty = no run. Never together with an unfinished syllable or a window.
    run: String,
    /// docs/spec/english-mixed.md §4.4, §5.3: the run keeps its text as typed whatever ends it. Set for a run typed in the ASCII state
    /// (`set_ascii(1)`; `set_ascii(0)` switches the state off before it ends the run, so the mark is on the run) and for a run
    /// Backspace reopened from a token whose text the table did not write (the user picked it, or typed it in the ASCII state).
    /// False whenever there is no run.
    run_ascii: bool,
    /// docs/spec/english-mixed.md §3.3, §4.5, §5.3: the run was opened by a revert ((a), (b), (c)), or reopened from a token that has a
    /// Chinese item. Esc replays such a run as Zhuyin and its token carries the Chinese item of its characters. False whenever there
    /// is no run.
    run_rev: bool,
    /// Digits (docs/spec/english-mixed.md §7): the count table (`digits.tsv`, optional), what is built from it and the model once, the run
    /// of digit-alphabet keys being typed, the keys of the syllable that just closed (docs/spec/english-mixed.md §7.7; `ld_now` is what the current key saw), and
    /// the counters and trace of the simulator and the golden test. All idle unless `digits_active`.
    digit_table: Option<Arc<DigitTable>>,
    /// docs/spec/english-mixed.md §5.7: the mixed words (`mixed-words.tsv`, optional). `None`: no item is ever added.
    mixed: Option<Arc<MixedTable>>,
    /// docs/spec/decoder.md §4.7: the bonus of the mixed word's edge: `lm::MIXED_BONUS`, or the evaluation hook's value (`set_mixed_bonus`).
    mixed_bonus: f64,
    /// docs/spec/prediction.md §3.5: the English words the completions of an open run are taken from, in lower case with the count of the `E` row
    /// (`None`: only in the lists of official writings), sorted by the word; built at the first use from the English table (`auto_table`, when one is
    /// loaded) and those lists, dropped when the table changes.
    en_index: Option<Arc<Vec<(String, Option<u64>)>>>,
    /// Test hook (`set_english_complete`, default on): whether the row of an open run carries English completions.
    en_complete: bool,
    digit_cache: Option<Arc<DigitCache>>,
    /// English before Chinese (docs/spec/english-mixed.md §8): the count table (`english-next.tsv`, optional) and the best entry of every
    /// word it names, built once per (lexicon, model). Only the prediction row reads them.
    en_table: Option<Arc<EnglishTable>>,
    en_words: Option<Arc<EnWords>>,
    drun: Option<DRun>,
    last_digits: Option<String>,
    ld_now: Option<String>,
    d_shown: usize,
    d_emitted: usize,
    dtrace: Option<Vec<DigitEvent>>,
    /// docs/spec/english-mixed.md §7.4 "待歸鍵": the digit tokens a run just made that the syllable typed next may take back (`digit_layer::Late`). `late_in` is what the
    /// current key saw of `late` (taken at the start of every `key()`, like `ld_now`); the key puts back what it did not use: rule 9 (`late_keep`), or a key
    /// that changed nothing (not handled, nothing committed). Any other key leaves `late` empty.
    late: Option<Late>,
    late_in: Option<Late>,
    /// The two penalties of the column-order reading (`digits::RHO`, `digits::RHO_SYM`); the tool hook `set_reorder_penalty` changes them.
    reorder: (f64, f64),
    /// Test and tool hook: the rejoin tests made.
    ltrace: Option<Vec<LateEvent>>,
    /// docs/spec/keys-and-composition.md §5.3 step 3: the syllable ranges ⌘⌫ forgot an entry over in the latest `key()`; the head freeze leaves them alone, so
    /// a forget shows on screen at once. Cleared by `cap_snapshot`.
    forgot: Vec<(usize, usize)>,
    /// docs/spec/keys-and-composition.md §5.3 step 1: the state before the running key or pick, kept only while a stretch could pass the window (`cap_snapshot`);
    /// taken by `cap_freeze`.
    cap: Option<CapSnap>,
    /// The free stretches of the latest decode, as `refresh_lm` cut them: `[from, to)` in syllables (fixed words and an open English run
    /// bound them). One definition of a stretch for the rerank job and for `cap_refreeze`.
    free: Vec<(usize, usize)>,
    /// Test hook (`decodes`): how many times `refresh` decoded the composition.
    decodes: u64,
}

/// Base lexicon + overlay from `data_dir` (docs/spec/c-abi.md §8), same as the eval CLI default. The overlay rows are
/// `overlay-add.tsv`, `sandhi-add.tsv` (S2r: MOE-standard 一/不 readings derived from the base, plus rule R6's colloquial 紮 ㄓㄚ), then `names-add.tsv`
/// (the always-on common proper nouns, docs/spec/lexicon-and-packs.md §1.1, §3), in that fixed order; all three are required.
pub fn load_lexicon(data_dir: &Path) -> Result<Arc<Lexicon>, EngineError> {
    load_lexicon_packs(data_dir, None).map(|(lex, _)| lex)
}

/// Pack bits of `Engine::new_with_packs` and `shanjie_engine_new_packs` (docs/spec/lexicon-and-packs.md §4.1, §4.2; `PACK_QUOTES`:
/// docs/spec/lexicon-and-packs.md §7.2, §4.1, §4.2; `PACK_IT`: docs/spec/lexicon-and-packs.md §4.1; `PACK_MEME`: docs/spec/lexicon-and-packs.md §9;
/// `PACK_BEAUTY`: docs/spec/lexicon-and-packs.md §8).
pub const PACK_ACG: u32 = 1;
pub const PACK_QUOTES: u32 = 2;
pub const PACK_IT: u32 = 4;
pub const PACK_MEME: u32 = 8;
pub const PACK_BEAUTY: u32 = 16;
pub const PACK_ALL: u32 = PACK_ACG | PACK_QUOTES | PACK_IT | PACK_MEME | PACK_BEAUTY;
/// The packs whose rows `CappedLexicon::with_packs` reads (docs/spec/decoder.md §2.5): rows tagged `lm::PACK_OOV_TAG` are
/// unknown to the model, scored by the pack alone, not capped; the other rows are capped as overlay words, except that a word the model
/// never saw keeps its pack score. Only the anime pack for now; the quotes pack, the IT pack, the meme pack, the beauty pack and a pack added later are capped like any overlay.
pub const PACK_OOV: u32 = PACK_ACG;
/// A pack's overlay file inside the packs directory, in the order the packs are appended.
pub const PACK_FILES: [(u32, &str); 5] = [(PACK_ACG, "acg-add.tsv"), (PACK_QUOTES, "quotes-add.tsv"), (PACK_IT, "it-add.tsv"), (PACK_MEME, "meme-add.tsv"), (PACK_BEAUTY, "beauty-add.tsv")];

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
    read_packs_split(dir, mask).map(|(text, _)| text)
}

/// `read_packs_detailed` and the length of its `PACK_OOV` prefix: the `PACK_OOV` packs are read first, so their rows are
/// `text[..len]`, exactly what `read_packs_detailed(dir, mask & PACK_OOV)` returns.
fn read_packs_split(dir: &Path, mask: u32) -> Result<(String, usize), LoadError> {
    let (mut text, mut oov_len) = (String::new(), 0);
    for oov in [true, false] {
        for (bit, name) in PACK_FILES {
            if mask & bit == 0 || (bit & PACK_OOV != 0) != oov {
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
        if oov {
            oov_len = text.len();
        }
    }
    Ok((text, oov_len))
}

/// The always-on names layer (docs/spec/lexicon-and-packs.md §3, §1.1): read after `sandhi-add.tsv` into the lexicon
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

/// `load_lexicon_packs_detailed` plus the evaluation-only `--extra-overlay` file (docs/spec/lexicon-and-packs.md §3.6):
/// its rows join the lexicon after the packs' rows and come back in the returned text, so `capping_overlay` caps them
/// the same way. `None`: exactly `load_lexicon_packs_detailed`. A missing or unreadable file is a load failure naming it.
/// `names` false (the CLI's evaluation-only `--no-names`) leaves `names-add.tsv` out of the lexicon; the engine always passes true.
pub fn load_lexicon_extra_detailed(data_dir: &Path, packs: Option<(&Path, u32)>, extra_file: Option<&Path>, names: bool) -> Result<(Arc<Lexicon>, String), LoadError> {
    load_lexicon_split(data_dir, packs, extra_file, names).map(|(lex, text, _)| (lex, text))
}

/// The variant-order table in the data directory (docs/spec/decoder.md §2.3): `reading<TAB>less-typed<TAB>more-typed<TAB>source` rows.
pub const VARIANT_ORDER_FILE: &str = "variant-order.tsv";

/// `load_lexicon_extra_detailed` and the length of the `PACK_OOV` prefix of the returned text (`read_packs_split`).
fn load_lexicon_split(data_dir: &Path, packs: Option<(&Path, u32)>, extra_file: Option<&Path>, names: bool) -> Result<(Arc<Lexicon>, String, usize), LoadError> {
    let base = read_file(data_dir.join("mcbpmf-data.txt"))?;
    let overlay = read_file(data_dir.join("overlay-add.tsv"))?;
    let sandhi = read_file(data_dir.join("sandhi-add.tsv"))?;
    let mut text = join_overlays(overlay, &sandhi);
    if names {
        text = join_overlays(text, &read_file(data_dir.join(NAMES_FILE))?);
    }
    let (mut extra, oov_len) = match packs {
        Some((dir, mask)) => read_packs_split(dir, mask)?,
        None => (String::new(), 0),
    };
    if let Some(f) = extra_file {
        extra = join_overlays(extra, &read_file(f.to_path_buf())?);
    }
    if !extra.is_empty() {
        text = join_overlays(text, &extra);
    }
    // Required like the other data files (docs/spec/decoder.md §2.3): missing, malformed, or naming a spelling the lexicon lacks is a load failure.
    let variants = VariantOrder::parse(&read_file(data_dir.join(VARIANT_ORDER_FILE))?).map_err(|e| LoadError::Parse { path: data_dir.join(VARIANT_ORDER_FILE), detail: e.to_string() })?;
    let lex = Lexicon::parse_with_variants(&base, Some(&text), Some(&variants)).map_err(|e| LoadError::Parse { path: data_dir.to_path_buf(), detail: e.to_string() })?;
    // Required too (docs/spec/lexicon-and-packs.md §2.12): the rerank-protect words join the manual mask; missing or malformed is a load failure.
    let protect = read_file(data_dir.join(RERANK_PROTECT_FILE))?;
    let lex = lex.with_protected(&protect).map_err(|detail| LoadError::Parse { path: data_dir.join(RERANK_PROTECT_FILE), detail })?;
    Ok((Arc::new(lex), extra, oov_len))
}

/// The rerank-protect list in the data directory (docs/spec/lexicon-and-packs.md §2.12): words the rerank may not take out of the n-gram's
/// first choice, with no score change.
pub const RERANK_PROTECT_FILE: &str = "rerank-protect.tsv";

/// The text `CappedLexicon::with_packs` takes as its overlay: `overlay-add.tsv` and `names-add.tsv` (read here, as `load_lm` always
/// did for the first), then the packs' rows `load_lexicon_packs` returned (no sandhi rows). Its words are capped at their corpus
/// frequency, except the rows of the `PACK_OOV` packs that `with_packs` takes again as its `pack_text` and treats by their tag.
pub fn capping_overlay(data_dir: &Path, pack_text: &str) -> Result<String, EngineError> {
    capping_overlay_detailed(data_dir, pack_text).map_err(|_| EngineError::LoadFailed)
}

pub fn capping_overlay_detailed(data_dir: &Path, pack_text: &str) -> Result<String, LoadError> {
    let overlay = read_file(data_dir.join("overlay-add.tsv"))?;
    let overlay = join_overlays(overlay, &read_file(data_dir.join(NAMES_FILE))?);
    Ok(if pack_text.is_empty() { overlay } else { join_overlays(overlay, pack_text) })
}

/// The reading-decode exemption list in the data directory (docs/spec/decoder.md §5): `syllable<TAB>character` rows whose term is 0.
pub const READING_EXEMPT_FILE: &str = "std-exempt.tsv";

/// The reading table of the decoder (`reading::ReadingDecode`), built from the merged lexicon (`load_lexicon*`'s) and the data directory: the text
/// of `mcbpmf-data.txt` (the rows the mc estimate counts) and `std-exempt.tsv`. The one function the engine (`load_lm`), the evaluation CLI and the
/// replay call. A missing or unreadable file is a read error naming it, a malformed exemption row a parse error: never a silent table without the exemptions.
pub fn load_reading_decode(lex: &Lexicon, data_dir: &Path) -> Result<crate::reading::ReadingDecode, LoadError> {
    let base = read_file(data_dir.join("mcbpmf-data.txt"))?;
    let exempt = read_file(data_dir.join(READING_EXEMPT_FILE))?;
    let rd = crate::reading::ReadingDecode::new(lex, &base)
        .with_exempt(&exempt)
        .map_err(|detail| LoadError::Parse { path: data_dir.join(READING_EXEMPT_FILE), detail })?;
    Ok(rd.with_mass(lex, &base).with_syllable_norm(lex))
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
    /// docs/spec/lexicon-and-packs.md §4.1). The pack rows are parsed into the lexicon, so a pack is fixed for the engine's life;
    /// the shell switches by building a new engine, like a layout change. Mask 0 / `None`: same as `new`.
    /// An unknown bit is `LoadFailed`.
    pub fn new_with_packs(data_dir: &Path, layout: Layout, packs: Option<(&Path, u32)>) -> Result<Engine, EngineError> {
        Engine::build(data_dir, layout, packs, None)
    }

    /// `new_with_packs`; with `shared`, the digit table and the English follower table are the loaded ones, not read again.
    fn build(data_dir: &Path, layout: Layout, packs: Option<(&Path, u32)>, shared: Option<&Shared>) -> Result<Engine, EngineError> {
        if packs.is_some_and(|(_, m)| m & !PACK_ALL != 0) {
            return Err(EngineError::LoadFailed);
        }
        let packs = packs.filter(|&(_, m)| m != 0);
        let (lex, pack_text, oov_len) = load_lexicon_split(data_dir, packs, None, true).map_err(|_| EngineError::LoadFailed)?;
        let mut e = Engine::with_lexicon(lex, layout);
        e.pack_single = pack_has_single(&pack_text);
        e.pack_text = Some(pack_text).filter(|t| !t.is_empty());
        e.pack_oov_len = oov_len;
        // Required like the other data files, and every row must name an entry of the lexicon (docs/spec/decoder.md §2.4); `load_lm` reads it again to resolve it against the capped lexicon.
        let demote = std::fs::read_to_string(data_dir.join("demote.tsv")).map_err(|_| EngineError::LoadFailed)?;
        if !Demote::parse(&demote).is_some_and(|d| d.check(&e.lex)) {
            return Err(EngineError::LoadFailed);
        }
        e.data_dir = Some(data_dir.to_path_buf());
        // Optional (docs/spec/english-mixed.md §7.2, §7.8): a missing or malformed table leaves the digit feature off and says nothing.
        e.digit_table = match shared {
            Some(s) => s.digit_table.clone(),
            None => std::fs::read_to_string(data_dir.join("digits.tsv")).ok().and_then(|t| DigitTable::parse(&t)).map(Arc::new),
        };
        // Optional (docs/spec/english-mixed.md §16.5): a missing or malformed table leaves the comparison off and says nothing.
        e.auto_table = match shared {
            Some(s) => s.auto_table.clone(),
            None => std::fs::read_to_string(data_dir.join("english.tsv")).ok().and_then(|t| AutoTable::parse(&t)).map(Arc::new),
        };
        // Optional (docs/spec/english-mixed.md §5.7): a missing table, or one with a row that breaks a rule, loads nothing and says nothing.
        if let Ok(t) = std::fs::read_to_string(data_dir.join("mixed-words.tsv")) {
            e.set_mixed_words(&t);
        }
        // Optional (docs/spec/english-mixed.md §8): absent leaves the feature off; a file that is there and does not parse is a load failure (a shipping error
        // must not switch the feature off unseen).
        if let Some(s) = shared {
            e.en_table = s.en_table.clone();
            return Ok(e);
        }
        match std::fs::read_to_string(data_dir.join("english-next.tsv")) {
            Ok(t) => e.en_table = Some(Arc::new(EnglishTable::parse(&t).map_err(|_| EngineError::LoadFailed)?)),
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {}
            Err(_) => return Err(EngineError::LoadFailed), // there but unreadable (not UTF-8, permissions)
        }
        Ok(e)
    }

    /// The parts of this engine a new engine can reuse (docs/spec/c-abi.md §5.4): only `Arc`s are copied, nothing is read. `None` without a data
    /// folder or an n-gram model (nothing to share). Owns no user input (no left context, composition, learner or candidates).
    pub fn share(&self) -> Option<Shared> {
        let (dir, st) = (self.data_dir.clone()?, self.lm.as_ref()?);
        Some(Shared {
            data_dir: dir,
            lm: st.lm.clone(),
            tinylm: self.tinylm.clone(),
            colloquial: self.colloquial.as_ref().map(|c| c.table().clone()),
            reading: st.capped.reading_table().cloned(),
            src_pack_single: self.pack_single,
            digit_table: self.digit_table.clone(),
            en_table: self.en_table.clone(),
            auto_table: self.auto_table.clone(),
        })
    }

    /// `new_with_packs` followed by `load_lm`, `load_tinylm` and `load_colloquial`, with the parts of `shared` in place of what those read
    /// (docs/spec/c-abi.md §5.4). All or nothing: any step failing is `LoadFailed` and no engine is returned. The files the engine builds from
    /// (lexicon and pack rows, `demote.tsv`, `variant-order.tsv`, `rerank-protect.tsv`, `std-exempt.tsv`, `mixed-words.tsv`) are read as
    /// `new_with_packs` and `load_lm` read them. The output equals that of the engine built from the files.
    pub fn new_shared(shared: &Shared, layout: Layout, packs: Option<(&Path, u32)>) -> Result<Engine, EngineError> {
        let mut e = Engine::build(&shared.data_dir, layout, packs, Some(shared))?;
        e.install_lm(shared.lm.clone(), Some(shared))?;
        e.set_tinylm(shared.tinylm.clone());
        if let Some(t) = &shared.colloquial {
            let dir = e.data_dir.as_ref().ok_or(EngineError::LoadFailed)?;
            e.colloquial = Some(Arc::new(e.colloquial_on(t.clone(), dir).map_err(|_| EngineError::LoadFailed)?));
        }
        Ok(e)
    }

    /// The identity of each part that `share` hands over, as addresses (0 for none): tests compare them with the source engine's to see what was
    /// shared and what was built again.
    pub fn part_ptrs(&self) -> PartPtrs {
        let addr = |p: Option<*const ()>| p.map_or(0, |p| p as usize);
        PartPtrs {
            lm: addr(self.lm.as_ref().map(|s| Arc::as_ptr(&s.lm).cast())),
            tinylm: addr(self.tinylm.as_ref().map(|t| Arc::as_ptr(t).cast())),
            colloquial: addr(self.colloquial.as_ref().map(|c| Arc::as_ptr(c.table()).cast())),
            reading: addr(self.lm.as_ref().and_then(|s| s.capped.reading_table()).map(|r| Arc::as_ptr(r).cast())),
            digit_table: addr(self.digit_table.as_ref().map(|t| Arc::as_ptr(t).cast())),
            en_table: addr(self.en_table.as_ref().map(|t| Arc::as_ptr(t).cast())),
        }
    }

    /// Share an already-loaded lexicon (tests load the 131 MB file once).
    pub fn with_lexicon(lex: Arc<Lexicon>, layout: Layout) -> Engine {
        Engine {
            lex,
            data_dir: None,
            pack_text: None,
            pack_oov_len: 0,
            pack_single: false,
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
            key_scheme: 0,
            punct_layout: 0,
            half_punct_mask: 0,
            up_run: None,
            abbr: false,
            cand_len_cap: false,
            scan_gate: true,
            pred_dirty: false,
            left: String::new(),
            left_full: String::new(),
            left_cut: false,
            recur: RecurGrams::default(),
            recur_on: true,
            pool_recur_on: true,
            left_fresh: false,
            learning: false,
            paused: false,
            newword: true,
            subspans: crate::learn::NEWWORD_SUBSPANS,
            eps_global: crate::lm::LEARN_EPS_GLOBAL,
            eps_sinit: 1.0,
            sinit: SinitStats::default(),
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
            reading_decode: true,
            rerank_level: RerankLevel::Standard,
            learn_guard: true,
            guard_skips: 0,
            fuzzy_skips: 0,
            generation: next_generation(),
            rerank_job: None,
            english: false,
            shadow_key: [None; 3],
            typed: String::new(),
            english_overwrite: true,
            english_key_order: true,
            english_reversal_guard: true,
            ascii: false,
            commit_out: None,
            reverted: None,
            split: false,
            revert_point: None,
            last_short: None,
            ls_now: None,
            auto_on: true,
            auto_table: None,
            auto_params: AutoParams::default(),
            pend_dup: 0,
            pend_rev: false,
            pend_plain: false,
            last_auto: None,
            la_now: None,
            auto_point: None,
            auto_event: None,
            noop: false,
            eaten: false,
            depth: 0,
            fuzzy: 0,
            tone3: true,
            run: String::new(),
            run_ascii: false,
            run_rev: false,
            digit_table: None,
            mixed: None,
            mixed_bonus: crate::lm::MIXED_BONUS,
            en_index: None,
            en_complete: true,
            digit_cache: None,
            en_table: None,
            en_words: None,
            drun: None,
            last_digits: None,
            ld_now: None,
            d_shown: 0,
            d_emitted: 0,
            dtrace: None,
            late: None,
            late_in: None,
            reorder: (crate::digits::RHO, crate::digits::RHO_SYM),
            ltrace: None,
            forgot: Vec::new(),
            cap: None,
            free: Vec::new(),
            decodes: 0,
        }
    }

    /// docs/spec/semantic-rerank.md §6.2: called wherever decoding changes (every `refresh`, `clear_all`, a new n-gram or small model,
    /// a new colloquial table, the rerank switch, the rerank level, the profile, the demotion table, learning open / clear, `set_learn_guard`): a job made
    /// before is stale from now on, and its `guard_skips` count goes with it.
    /// `set_learning` is not among them: the flag only decides whether picks are recorded (`pre`), never how the composition
    /// decodes (decoding reads the learner whenever it is non-empty).
    /// Keys that do not re-decode (cursor moves, unfinished symbols, entering the prediction row, pass-through) keep it.
    /// A setting that changes how the composition decodes, what the keys do or what the candidate window lists was set (docs/spec/english-mixed.md §6):
    /// the short-word mark goes. Every such setter calls this; the ones that do NOT, on purpose, are: `set_learning` and
    /// `set_left_context` (the shell calls `set_learning` before every key and `set_left_context` before a key that may start a composition, so they
    /// would clear the mark of the key before), `set_punctuation`,
    /// `set_newword`, `set_newword_subspans`, `set_scan_gate`, `set_eps_global`, `set_today` and `set_english_next` (they change a table or
    /// a learning knob, not the composition or the short word's items), and the `load_*` functions and `learning_open`/`learning_clear`
    /// (they run when the engine is built, before the first key, and do not recompute the composition). Not `invalidate()` itself:
    /// `refresh()` calls that inside a key.
    fn setting_changed(&mut self) {
        self.last_short = None;
        self.last_auto = None;
    }

    fn invalidate(&mut self) {
        self.generation = next_generation();
        self.rerank_job = None;
        self.guard_skips = 0;
        self.fuzzy_skips = 0;
        self.sinit = SinitStats::default();
    }

    // ---- S7c-2a rerank (docs/spec/semantic-rerank.md §6) ----

    /// docs/spec/semantic-rerank.md §7: the small model at `path` for this engine's n-gram model. Fails (the previous model, or none, stays) when
    /// the file cannot be read or is malformed, when no n-gram model is loaded, when the `.sjtl` was tuned with another n-gram
    /// model, or when its recorded Kneser-Ney side file hash is not the loaded side file's (none for a file tuned without KN;
    /// docs/spec/models.md §3.5, §8.1). A pairing check, not an integrity check of the `.sjtl`. Leaves the rerank switch and the display as
    /// they are.
    pub fn load_tinylm(&mut self, path: &Path) -> Result<(), EngineError> {
        self.invalidate();
        let st = self.lm.as_ref().ok_or(EngineError::LoadFailed)?;
        let model = TinyLm::load_paired(path, &st.lm).map_err(|_| EngineError::LoadFailed)?;
        self.set_tinylm(Some(Arc::new(model)));
        Ok(())
    }

    /// Every change of the small model: `left_full` was cut for the model loaded at `set_left_context` (its L, or the English word without
    /// one), so it goes with it; the next composition start sets it again (docs/spec/semantic-rerank.md §5.2). `left` and the recurrence set do not
    /// depend on the model and stay.
    fn set_tinylm(&mut self, model: Option<Arc<TinyLm>>) {
        self.tinylm = model;
        self.left_full.clear();
        self.left_cut = false;
    }

    /// The colloquial table at `path` (docs/spec/semantic-rerank.md §4), with the word sets of its k term read from the
    /// engine's lexicon folder (`mcbpmf-data.txt`, `overlay-add.tsv`) and the pack rows this engine was built with. Callable before or
    /// after `load_lm` / `load_tinylm`: the pairing is checked per job. Turns the SR-TW-M first-word estimate on (`Colloquial::unseen_start_unigram`).
    /// Fails (the previous table, or none, stays) when the engine has no data folder or a file cannot be read or parsed. Drops the pending job.
    pub fn load_colloquial(&mut self, path: &Path) -> Result<(), EngineError> {
        self.invalidate();
        let dir = self.data_dir.as_ref().ok_or(EngineError::LoadFailed)?;
        let table = Arc::new(ColloquialTable::load(path).map_err(|_| EngineError::LoadFailed)?);
        self.colloquial = Some(Arc::new(self.colloquial_on(table, dir).map_err(|_| EngineError::LoadFailed)?));
        Ok(())
    }

    /// The colloquial term on a loaded table: the word sets of k from `dir` and this engine's pack rows (`load_colloquial`, `new_shared`).
    fn colloquial_on(&self, table: Arc<ColloquialTable>, dir: &Path) -> Result<Colloquial, colloquial::ColloquialError> {
        let base = std::fs::read_to_string(dir.join("mcbpmf-data.txt")).map_err(|_| colloquial::ColloquialError::Read)?;
        let overlay = std::fs::File::open(dir.join("overlay-add.tsv")).map_err(|_| colloquial::ColloquialError::Read)?;
        let mut co = Colloquial::with_table(table, &base, std::io::BufReader::new(overlay), self.pack_text.as_deref())?;
        // The shipping setting (docs/spec/semantic-rerank.md §4.4): a first word the table has no `<s>` bigram for is scored by the unigram estimate.
        co.set_unseen_start_unigram(true);
        Ok(co)
    }

    /// The loaded colloquial table, if any (tests).
    pub fn colloquial(&self) -> Option<&Colloquial> {
        self.colloquial.as_deref()
    }

    /// Whether rerank jobs are made (default off, S7c-2c); recomputes the composition and returns the snapshot like `set_demote`,
    /// so turning it off shows the n-gram result at once. On a decode failure the engine resets itself.
    pub fn set_rerank(&mut self, on: bool) -> Result<Output, EngineError> {
        self.setting_changed();
        self.invalidate();
        self.rerank_on = on;
        self.clear_pred();
        if let Err(e) = self.refresh() {
            self.clear_all();
            return Err(e);
        }
        self.handled()
    }

    /// Evaluation only (docs/spec/evaluation.md §4.3, `--learn-guard`): turns the learning guard off so stretches with a learned
    /// boost still go to the small model. The next decode uses it; the pending job is dropped like any decoding change.
    pub fn set_learn_guard(&mut self, on: bool) {
        self.setting_changed();
        self.invalidate();
        self.learn_guard = on;
    }

    /// Evaluation only (`shanjie-replay --no-reading-decode`): with `false` the n-gram decoder does not add the reading term
    /// (docs/spec/decoder.md §5), the arithmetic of a lexicon without it. It reaches the model already loaded and the next
    /// `load_lm`; the pending job is dropped like any decoding change.
    pub fn set_reading_decode(&mut self, on: bool) {
        self.setting_changed();
        self.invalidate();
        self.reading_decode = on;
        if let Some(st) = &self.lm {
            st.capped.set_reading_active(on);
        }
    }

    /// Evaluation only (`shanjie-replay --no-recur`): with `false` the decoder does not get the context recurrence set (docs/spec/decoder.md §4.6), the arithmetic without the term; the set itself is kept and cleared as before. The pending job is dropped like any decoding change.
    pub fn set_recur(&mut self, on: bool) {
        self.setting_changed();
        self.invalidate();
        self.recur_on = on;
    }

    /// Evaluation only (`shanjie-replay --no-pool-recur`): with `false` the rerank jobs do not get the context recurrence weight on the pool
    /// (docs/spec/semantic-rerank.md §3.1.1), the jobs of the engine before it; the decoder's term (`set_recur`) is unchanged. Not in shanjie.h. The pending
    /// job is dropped like any decoding change.
    pub fn set_pool_recur(&mut self, on: bool) {
        self.setting_changed();
        self.invalidate();
        self.pool_recur_on = on;
    }

    /// The summary tag of the reading table the decoder uses now (`+rd+rx:<sha8>+rm+rn`; empty without a model or with `set_reading_decode(false)`): what
    /// `shanjie-replay` prints, so the tag names the table the engine really loaded.
    pub fn reading_tag(&self) -> String {
        self.lm.as_ref().and_then(|st| st.capped.reading_tag()).unwrap_or_default()
    }

    /// How many stretches the learning guard kept out of the current job: stretches that would have gone to the small model
    /// (rerank on, model loaded, demotion table on, two candidates or more). Counted by each decode; 0 after any change that
    /// drops the job (a commit, Esc, a setting) until the next decode, and 0 with the guard off.
    pub fn guard_skips(&self) -> usize {
        self.guard_skips
    }

    /// How many stretches the fuzzy-sound rule kept out of the current job (docs/spec/decoder.md §6.5): stretches that would have
    /// gone to the small model whose n-gram first path has a fuzzy word (a rescue is shown as the n-gram has it). Counted and reset like
    /// `guard_skips`; always 0 without a fuzzy-sound bit.
    pub fn fuzzy_skips(&self) -> usize {
        self.fuzzy_skips
    }

    /// Rerank strength (docs/spec/semantic-rerank.md §7): which of the model's (alpha, tau, delta) the jobs use. Remembered without a
    /// model and kept by `load_tinylm` / `load_lm`; with a version 1 or 2 file every level uses the standard triple. Recomputes the
    /// composition and returns the snapshot like `set_profile`; on a decode failure the engine resets itself.
    pub fn set_rerank_level(&mut self, level: RerankLevel) -> Result<Output, EngineError> {
        self.setting_changed();
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

    /// docs/spec/semantic-rerank.md §6.4: puts the chosen paths into the composition and returns the new output. `None` (nothing changes) when the
    /// result is stale (another decode since the job, docs/spec/semantic-rerank.md §6.2), `path` no longer holds the words the job was
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
            self.path.splice(c.at..c.at + c.expect.len(), c.words.into_iter().map(|(word, lp, delta, syllables, sinit, mixed)| PathWord { word, lp, sinit, is_punct: false, delta, syllables, mixed }));
        }
        // `refresh_lm` builds the display from exactly the strings of `path`.
        self.display = self.path.iter().map(|p| p.word.as_str()).collect();
        self.mixed_sync(); // docs/spec/english-mixed.md §4.4: the token's writing follows the path the rerank chose
        if !self.pred.is_empty() {
            self.recompute_pred();
        }
        self.handled().map(Some)
    }

    // ---- S4 learning (docs/spec/learning.md §1) ----

    /// docs/spec/learning.md §2.1: keep only the last ≤ 2 consecutive Han characters of `text` (none: empty) for decoding, learning and the prediction row's
    /// key. docs/spec/semantic-rerank.md §5.2: `left_full` keeps only what the small model or the English table can use (see the field). The decoder also
    /// reads the recurrence set, the Han pairs of the last `lm::RECUR_CHARS` characters (`recur`), rebuilt here; the text itself is not kept.
    /// The C ABI passes "" for NULL and for text that is not UTF-8, so all three leave every one of them empty. A key that starts a composition on
    /// an empty one without this call first clears them (`key`).
    pub fn set_left_context(&mut self, text: &str) {
        let k = context_key(text);
        self.left = if k == crate::learn::SENTINEL { String::new() } else { k };
        (self.left_full, self.left_cut) = match self.tinylm.as_ref().and_then(|m| m.left_len()) {
            Some(l) => (last_chars(text, l), text.chars().nth(l).is_some()),
            None => (english_tail(text).map_or_else(String::new, |(_, kept)| kept), false),
        };
        self.recur = RecurGrams::from_text(text);
        self.left_fresh = true;
    }

    /// Test hook: how many pairs the recurrence set holds (the pairs themselves are never handed out).
    #[doc(hidden)]
    pub fn recur_pairs(&self) -> usize {
        self.recur.len()
    }

    /// Everything kept from the left text goes at once: `left`, `left_full`, `left_cut` and the recurrence set. The one place they are cleared
    /// together (`clear_all`, the rule 22 pass-throughs, a key on an empty composition without a fresh left text); `set_tinylm` clears only `left_full`.
    fn clear_left(&mut self) {
        self.left.clear();
        self.left_full.clear();
        self.left_cut = false;
        self.recur = RecurGrams::default();
    }

    /// The composition shows nothing (the shell's "not composing", `Output::preedit` empty): no unit, no English run, no unfinished symbol and no
    /// open digit buffer (its keys can all show as digits while the other three are empty).
    fn composition_empty(&self) -> bool {
        self.syls.is_empty() && self.run.is_empty() && self.pending().is_empty() && self.drun.is_none()
    }

    /// Test hook: how many characters `left_full` holds (docs/spec/semantic-rerank.md §10; the text itself is never handed out).
    #[doc(hidden)]
    pub fn left_full_chars(&self) -> usize {
        self.left_full.chars().count()
    }

    /// docs/spec/learning.md §3: off by default. Turning it off drops pending learns; a span is learned only if the flag was
    /// on both when it was chosen and at commit. It never makes a rerank job stale (docs/spec/semantic-rerank.md §6.2):
    /// the flag does not change decoding, which reads the learner whenever it is non-empty; it only decides
    /// whether picks are recorded. The shell sends it before every key (docs/spec/learning.md §3).
    /// docs/spec/learning.md §8.1: turning it off while composing, or any key handled while it is off (`key`), marks the
    /// composition, so its commit learns no new word even when learning is back on by then (text typed during the pause
    /// is never stored next to a later re-pick). The mark goes when the composition is empty again.
    pub fn set_learning(&mut self, on: bool) {
        self.learning = on;
        if !on {
            self.fixed.iter_mut().for_each(|f| {
                f.pre = None;
                f.displaced_new.clear();
                f.mixed.picked = 0;
            });
            if !self.syls.is_empty() || self.pend.iter().any(Option::is_some) {
                self.paused = true;
            }
        }
    }

    /// Test hook: whether new-word B learns (default on). Off is S4 alone (docs/spec/learning.md §13.2 baseline).
    pub fn set_newword(&mut self, on: bool) {
        self.newword = on;
    }

    /// Test hook: variant B (`NEWWORD_SUBSPANS`, on by default) on or off.
    pub fn set_newword_subspans(&mut self, on: bool) {
        self.subspans = on;
    }

    /// docs/spec/learning.md §10.2: load `dir/learning.tsv` (memory pruned only; the file is tidied by the first write, which
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

    /// docs/spec/learning.md §9.2: forget everything: memory, pending learns, files. Memory and pending learns go even when
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
            f.mixed.picked = 0;
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

    /// bit0: the last full rewrite of the learning file failed (docs/spec/learning.md §10.4).
    pub fn learning_status(&self) -> u32 {
        self.write_failed as u32
    }

    pub fn learner(&self) -> &Learner {
        &self.learner
    }

    /// Test hook (docs/spec/prediction.md §3.3 step 1): false turns the "could a record match" gate off.
    pub fn set_scan_gate(&mut self, on: bool) {
        self.scan_gate = on;
    }

    /// Test hook (docs/spec/learning.md §5.4): ε of the global level.
    pub fn set_eps_global(&mut self, eps: f64) {
        self.eps_global = eps;
    }

    /// Tests only (SINIT): the factor on the three sizes of the learning term in the fused score (`lm::EPS_SINIT_FINE` 4, `lm::EPS_SINIT_MID` 4, `lm::EPS_SINIT_COARSE` 2; 1 is the shipping value), to show that the knob acts and that 0 is no record.
    /// Taken by the jobs made after it; it does not drop the pending one.
    pub fn set_eps_sinit(&mut self, eps: f64) {
        self.eps_sinit = eps;
    }

    /// What SINIT did in the current decode (see `SinitStats`); all 0 after any change that drops the job, until the next decode.
    pub fn sinit_stats(&self) -> SinitStats {
        self.sinit
    }

    /// Test-purpose clock: day number to use instead of the local day.
    pub fn set_today(&mut self, day: Option<i64>) {
        self.today = day;
    }

    /// Test-purpose injection: the next commit's learning pass panics (docs/spec/learning.md §13.1).
    pub fn inject_learn_panic(&mut self) {
        self.learn_panic = true;
    }

    /// Test-purpose injection: the next ⌘⌫ forget panics right after removing the word from memory,
    /// before the file is rewritten (docs/spec/learning.md §13.1).
    pub fn inject_forget_panic(&mut self) {
        self.forget_panic = true;
    }

    fn today(&self) -> i64 {
        self.today.unwrap_or_else(local_day)
    }

    /// docs/spec/learning.md §10.3 write after a learning commit: append `touched` when allowed, else a full rewrite.
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

    /// docs/spec/learning.md §10.3 full rewrite: prune, then replace the file. Success clears `must_rewrite` and bit0; a
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

    /// docs/spec/learning.md §4.1: runs after the commit text is known, inside its own `catch_unwind`: whatever fails here
    /// only costs this learn. `display` is the decoded composition text, without the unfinished symbols that the
    /// commit itself may carry (docs/spec/keys-and-composition.md §4.1 rule 12a): the context keys are offsets into it.
    fn learn_commit(&mut self, display: &str) {
        if !self.learning {
            return;
        }
        let new_on = self.newword && !self.paused;
        self.mixed_sweep(); // a pick whose syllables the commit's own key changed is not recorded
        if !(new_on && self.learner.has_new_words()) && self.fixed.iter().all(|f| f.pre.is_none() && f.displaced_new.is_empty() && f.mixed.picked == 0) {
            return;
        }
        let _ = catch_unwind(AssertUnwindSafe(|| {
            if std::mem::take(&mut self.learn_panic) {
                panic!("injected learning panic");
            }
            let today = self.today();
            let uses = if new_on { self.new_word_uses(today) } else { Vec::new() };
            // docs/contracts/typo-one-sym.md §2.4 item 4: a syllable only bit 5 accepted is never in a record, a key or a reading.
            let unk = self.bit5_unknown();
            let u = |i: usize| unk.get(i).copied().unwrap_or(false);
            let plans: Vec<_> = self
                .fixed
                .iter()
                .flat_map(|f| {
                    let Some(pre) = f.pre.as_ref().filter(|p| **p != f.word && !self.is_punct(f.start)) else { return Vec::new() };
                    if (f.start..f.end).any(u) {
                        return Vec::new();
                    }
                    let off: usize = (0..f.start).map(|i| self.token_width(i)).sum();
                    let before: String = display.chars().take(off).collect();
                    let ctx = f.key.clone().unwrap_or_else(|| context_key(&format!("{}{before}", self.left)));
                    // Sentence-start key: at sentence start the key holds the reading of the syllable after the word; a
                    // prediction pick (`f.key` set) was made at the end of the composition, so it is `^>$`.
                    let next = if f.key.is_some() { None } else { self.syls.get(f.end).map(String::as_str) };
                    let ctx = record_key(ctx, next);
                    let sentence_start = f.key.is_none() && ctx.starts_with(NEXT_PREFIX);
                    if sentence_start && u(f.end) {
                        return Vec::new(); // `^>` + a syllable only bit 5 accepted, or `^>$` made of it: neither key is recorded
                    }
                    let reading = self.syls[f.start..f.end].to_vec();
                    // docs/spec/learning.md §7.1: a sentence-start single character is also learned under the mid key (the next two syllables) and the fine key (the next three, or
                    // two and `$` when the composition ends there). A mid or fine key is not made when a syllable it needs is missing, English, a digit or punctuation
                    // (`mid_key`, `fine_key`), so a layer that is not recorded means no longer layer is; English and digits stop the coarse key too (`single_key_ok`),
                    // punctuation does not (`^>_punct_，` is recorded). The displaced word is halved under them too.
                    let (a, b, c) = (self.syls.get(f.end), self.syls.get(f.end + 1), self.syls.get(f.end + 2));
                    let (a, b, c) = (a.map(String::as_str), b.map(String::as_str), c.map(String::as_str));
                    let deeper: Vec<String> = if is_single(&f.word) && f.key.is_none() && ctx.starts_with(NEXT_PREFIX) {
                        let (mid_ok, fine_ok) = (!u(f.end + 1), !u(f.end + 1) && !u(f.end + 2));
                        [mid_ok.then(|| mid_key(a, b)).flatten(), fine_ok.then(|| fine_key(a, b, c)).flatten()].into_iter().flatten().collect()
                    } else {
                        Vec::new()
                    };
                    // The keys the displaced word was scored under at the pick (the coarse one and, for a single character, the mid and fine ones),
                    // those that are not a key of the commit.
                    let at_pick: Vec<String> = f.pick_keys.iter().filter(|k| **k != ctx && !deeper.contains(k)).cloned().collect();
                    let deeper = deeper.into_iter().map(|k| (k, reading.clone(), f.word.clone(), pre.clone(), Vec::new()));
                    [(ctx, reading.clone(), f.word.clone(), pre.clone(), at_pick)].into_iter().chain(deeper).collect::<Vec<_>>()
                })
                .collect();
            let mut touched = Vec::new();
            for (ctx, reading, word, displaced, at_pick) in &plans {
                touched.extend(self.learner.teach(ctx, reading, word, displaced, today));
                for k in at_pick {
                    touched.extend(self.learner.halve(k, reading, displaced, today));
                }
            }
            // docs/spec/learning.md §4.3: a pick from the row of an open English run, when the text sent over the token and the row's syllables is the row's
            // writing: weight + 1 under `^en:` + the English part.
            let en: Vec<(String, Vec<String>, String)> = self
                .fixed
                .iter()
                .filter(|f| f.mixed.picked > 0)
                .filter_map(|f| {
                    let english = english_of(&self.syls[f.start])?;
                    let reading = self.syls.get(f.end..f.end + f.mixed.picked)?;
                    let word = self.mixed.as_ref()?.get(english, reading)?;
                    // `display` is the text being sent (`self.display` is taken by now): the token's characters, then one per syllable.
                    let off: usize = (0..f.start).map(|i| self.token_width(i)).sum();
                    let sent: String = display.chars().skip(off).take(self.token_width(f.start) + f.mixed.picked).collect();
                    (sent == word).then(|| (format!("{EN_PREFIX}{}", english.to_ascii_lowercase()), reading.to_vec(), word.to_string()))
                })
                .collect();
            for (key, reading, word) in &en {
                touched.push(self.learner.teach_en(key, reading, word, today));
            }
            // New-word B: learn (docs/spec/learning.md §8.2), halve what re-picks displaced and count the uses (docs/spec/learning.md §8.5, once a day).
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
            // Nothing touched (e.g. a single character under a one-character key or at `^>$`, docs/spec/learning.md §5.3, §7.1): no write, unless a full
            // rewrite is pending (a failed forget or rewrite). It is retried only by commits that reach
            // this point: learning on and at least one re-pick or displaced new word, or new-word B learning on
            // (no pause) with any new-word record in the learner (the early return above); other commits leave it
            // pending.
            if !touched.is_empty() || self.must_rewrite {
                self.persist(&touched);
            }
        }));
    }

    /// Whether the lexicon has `word` under `reading`. The new-word gate's one lexicon (docs/spec/learning.md §8.2 item 2) is
    /// `self.lex`, what `load_lexicon_packs` loaded: the base, overlay-add.tsv, sandhi-add.tsv, names-add.tsv and the
    /// packs this engine was built with, at their file scores (before capping and demotion). The new-word rules (docs/spec/learning.md §8.2 items 2 and 5), the shadow
    /// gate, the decoder's "the lexicon has it" check all use it.
    fn in_lexicon(&self, reading: &[String], word: &str) -> bool {
        self.lex.has_with(reading, word, self.colloq_on())
    }

    /// docs/spec/learning.md §8.2 item 2: the reading leaves room for a new word, i.e. no lexicon word under it scores above
    /// `NEWWORD_SHADOW` (no word at all also does).
    fn room_for_new_word(&self, reading: &[String]) -> bool {
        self.lex.entries_with(reading, self.colloq_on()).iter().all(|&(_, score)| score <= NEWWORD_SHADOW)
    }

    /// docs/spec/learning.md §8.2: the runs of single Han characters on the path at commit (split at punctuation, non-Han and
    /// `NEWWORD_BREAK`) that hold a character re-picked in the candidate window, are NEWWORD_MIN..=NEWWORD_MAX long,
    /// are not all `NEWWORD_DIGITS`, are not in the lexicon under their reading and whose reading has no lexicon word above
    /// `NEWWORD_SHADOW`: (reading, word) each, plus variant B's sub-spans (docs/spec/learning.md §8.2 item 5).
    fn new_word_spans(&self) -> Vec<(Vec<String>, String)> {
        // A re-pick of one character (docs/spec/learning.md §4.2: chosen in the window, text before it differed); a prediction pick
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
        // docs/contracts/typo-one-sym.md §2.4 item 4: a syllable only bit 5 accepted breaks the run, so it is no part of a new word's reading.
        let unk = self.bit5_unknown();
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
                        if self.lex.entries_with(&r, self.colloq_on()).is_empty() {
                            out.push((r, sub.iter().map(|x| x.1).collect()));
                        }
                    }
                }
            }
        };
        for (p, s) in self.path.iter().zip(self.path_starts()) {
            let mut cs = p.word.chars();
            match (cs.next(), cs.next()) {
                (Some(c), None) if !p.is_punct && is_han(c) && !NEWWORD_BREAK.contains(c) && !unk.get(s).copied().unwrap_or(false) => run.push((s, c)),
                _ => flush(&mut run),
            }
        }
        flush(&mut run);
        // One commit learns a (reading, word) once, however many runs or sub-spans spell it (e.g. 板空板空 → 板空 twice).
        let mut seen = HashSet::new();
        out.retain(|x| seen.insert(x.clone()));
        out
    }

    /// docs/spec/learning.md §8.5: the new words on the path (not lexicon words under their reading) whose record is not dated
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
        if self.data_dir.is_none() {
            return Err(EngineError::LoadFailed);
        }
        // The model is read first, then `install_lm` reads the small files; every failure is the same `LoadFailed` and the state is set at the end only.
        let lm = Lm::load(path).map_err(|_| EngineError::LoadFailed)?;
        self.install_lm(Arc::new(lm), None)
    }

    /// The part of `load_lm` after the model file: the capped lexicon, the reading term (`shared`: its table when it can be reused, else built),
    /// the prediction index and the caches. Failure leaves the previous state.
    fn install_lm(&mut self, lm: Arc<Lm>, shared: Option<&Shared>) -> Result<(), EngineError> {
        self.invalidate();
        let dir = self.data_dir.as_ref().ok_or(EngineError::LoadFailed)?;
        let overlay = capping_overlay(dir, self.pack_text.as_deref().unwrap_or(""))?;
        let demote = std::fs::read_to_string(dir.join("demote.tsv")).map_err(|_| EngineError::LoadFailed)?;
        let demote = Demote::parse(&demote).ok_or(EngineError::LoadFailed)?;
        // The table is already in `self.lex` (`new`); read again here so a file that went missing or bad since then fails the load too.
        VariantOrder::parse(&std::fs::read_to_string(dir.join(VARIANT_ORDER_FILE)).map_err(|_| EngineError::LoadFailed)?).map_err(|_| EngineError::LoadFailed)?;
        let capped = CappedLexicon::with_packs(self.lex.clone(), &overlay, &self.pack_text.as_deref().unwrap_or("")[..self.pack_oov_len], &lm, Some(&demote))
            .ok_or(EngineError::LoadFailed)?;
        // The decoder's reading term (docs/spec/decoder.md §5): built from the merged lexicon and the data directory, always, so a missing
        // std-exempt.tsv fails here; `set_reading_decode(false)` only switches it off.
        let reuse = shared.and_then(|s| s.reading.as_ref().filter(|_| !s.src_pack_single && !self.pack_single));
        let capped = match reuse {
            // Reusable (docs/spec/decoder.md §5): the table reads only one-syllable readings, which no enabled pack adds to. The exemption list is
            // still read and parsed, so a list gone bad fails the load as it does in `load_reading_decode`.
            Some(rd) => {
                let exempt = read_file(dir.join(READING_EXEMPT_FILE)).map_err(|_| EngineError::LoadFailed)?;
                crate::reading::parse_exempt(&exempt).map_err(|_| EngineError::LoadFailed)?;
                capped.with_reading(rd.clone())
            }
            None => capped.with_reading(load_reading_decode(&self.lex, dir).map_err(|_| EngineError::LoadFailed)?),
        };
        capped.set_reading_active(self.reading_decode);
        // Built now, not at the first key: the index takes hundreds of ms (docs/spec/prediction.md §3.1).
        capped.predict_index(&lm);
        self.lm = Some(LmState { lm, capped: Arc::new(capped) });
        self.set_tinylm(None); // Tuned for the previous model
        self.digit_cache = None;
        self.en_words = None;
        self.en_cache();
        self.digit_env(); // built now, not at the first digit key (a table loaded and a model: the legal tails and the word readings)
        Ok(())
    }

    /// docs/spec/keys-and-composition.md §8.3: replace the punctuation alternatives with `table` (lines `mark\talt\talt…`, blank
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

    /// Evaluation and test hook (docs/spec/decoder.md §4.7; Rust only, not in shanjie.h): the bonus of the mixed word's edge instead of `lm::MIXED_BONUS`.
    #[doc(hidden)]
    pub fn set_mixed_bonus(&mut self, bonus: f64) {
        self.invalidate();
        self.mixed_bonus = bonus;
    }
    /// docs/spec/english-mixed.md §5.7: takes `text` as the mixed-words table. A table with any row that breaks a rule is not loaded
    /// and the table before it goes too (the engine is as it is without one). `Engine::new` calls this with `mixed-words.tsv`;
    /// tests call it on `with_lexicon` engines.
    pub fn set_mixed_words(&mut self, text: &str) -> bool {
        self.mixed = MixedTable::parse(text, &self.lex).map(Arc::new);
        self.mixed.is_some()
    }
    pub fn mixed_words_loaded(&self) -> bool {
        self.mixed.is_some()
    }
    /// Every row of the loaded table as (English part, reading, writing): what the check set's test types.
    pub fn mixed_words(&self) -> Vec<(String, Vec<String>, String)> {
        self.mixed.iter().flat_map(|t| t.rows()).map(|(e, r, w)| (e.to_string(), r.to_vec(), w.to_string())).collect()
    }

    /// Test-purpose injection for `with_lexicon` engines: a prebuilt model and its capped lexicon
    /// (built by `CappedLexicon::new` from this engine's lexicon).
    pub fn set_lm(&mut self, lm: Arc<Lm>, capped: Arc<CappedLexicon>) {
        self.setting_changed();
        self.invalidate();
        self.lm = Some(LmState { lm, capped });
        self.set_tinylm(None);
    }

    /// Whether the demotion table applies (default on, remembered even before a model is loaded);
    /// recomputes the composition and returns the snapshot, like `set_profile`. On a decode failure the
    /// engine resets itself.
    pub fn set_demote(&mut self, on: bool) -> Result<Output, EngineError> {
        self.setting_changed();
        self.invalidate();
        self.demote = on;
        self.clear_pred();
        if let Err(e) = self.refresh() {
            self.clear_all();
            return Err(e);
        }
        self.handled()
    }

    /// Whether the prediction row is computed (default on, docs/spec/prediction.md §7). Off clears the row (an entered row is left);
    /// on recomputes it, so a row that fits docs/spec/prediction.md §1.2 shows at once. Returns the snapshot.
    pub fn set_prediction(&mut self, on: bool) -> Result<Output, EngineError> {
        self.setting_changed();
        self.predict_on = on;
        if !on {
            self.drop_units(); // the units' only way out is the row (docs/spec/prediction.md §6)
            self.close_row_window();
        }
        self.recompute_pred(); // clears first; returns empty while off
        self.handled()
    }

    /// Orientation of the candidate windows that open from now on (default horizontal; docs/spec/macos-shell.md §8.6.2). A candidate window already open keeps the orientation it opened with. A prediction row has no opening
    /// and follows the setting on every output (docs/spec/macos-shell.md §8.6.4), so a row on screen is re-rendered in the new orientation in
    /// the returned snapshot; with a candidate window open or nothing shown the snapshot equals the output before the call.
    pub fn set_candidate_vertical(&mut self, on: bool) -> Result<Output, EngineError> {
        self.setting_changed();
        self.cand_vertical = on;
        self.handled()
    }

    /// The key scheme (docs/spec/keys-and-composition.md §4.1, the `m` rows; default 0): 0 the rules as they are, 1 the Microsoft Zhuyin habits of the Windows text service. Any other value changes nothing
    /// and gives `Ok(None)` (the C ABI's 2). A candidate window already open keeps the scheme it opened with; with a window open or nothing shown the snapshot equals the output before the call.
    pub fn set_key_scheme(&mut self, scheme: u32) -> Result<Option<Output>, EngineError> {
        if scheme > 1 {
            return Ok(None);
        }
        self.setting_changed();
        self.key_scheme = scheme as u8;
        self.handled().map(Some)
    }

    /// The punctuation layout (docs/spec/keys-and-composition.md §8.1; default 0): 0 Apple Zhuyin's keys, 1 McBopomofo's (ten cells differ, `SHIFT_PUNCT_1` and
    /// `PLAIN_PUNCT_1`). Any other value changes nothing and gives `Ok(None)` (the C ABI's 2). The next key reads it; nothing shown changes, so the snapshot equals
    /// the output before the call.
    pub fn set_punct_layout(&mut self, layout: u32) -> Result<Option<Output>, EngineError> {
        if layout > 1 {
            return Ok(None);
        }
        self.setting_changed();
        self.punct_layout = layout as u8;
        self.handled().map(Some)
    }

    /// Partial half-width punctuation mask (bits 0..=26, `HALF_PUNCT_ALL_MASK`; default 0). Any bit outside `HALF_PUNCT_ALL_MASK`
    /// changes nothing and gives `Ok(None)` (the C ABI's 2). The next key reads it; nothing shown changes, so the snapshot equals
    /// the output before the call.
    pub fn set_half_punct(&mut self, mask: u32) -> Result<Option<Output>, EngineError> {
        if mask & !HALF_PUNCT_ALL_MASK != 0 {
            return Ok(None);
        }
        self.setting_changed();
        self.half_punct_mask = mask;
        self.handled().map(Some)
    }

    /// Whether a non-advancing symbol opens a new unfinished unit (default off, docs/spec/prediction.md §6).
    /// Turning it off with two or more units drops them all (as Esc does); otherwise nothing changes. Returns the snapshot.
    pub fn set_abbreviation(&mut self, on: bool) -> Result<Output, EngineError> {
        self.setting_changed();
        self.abbr = on;
        if !on {
            self.drop_units();
            self.sync_shadow();
        } else {
            let flushed = self.digit_flush()?; // docs/spec/english-mixed.md §1.1, §11: the buffered digits become tokens
            self.clear_shadow(); // docs/spec/english-mixed.md §1.2: the two are exclusive
            return self.settle_with(flushed);
        }
        self.handled()
    }

    /// Whether each prediction query skips candidates whose character count exceeds the queried unit count (default off;
    /// docs/spec/prediction.md §3.3, §7). Recomputes the prediction row when the composition is non-empty and prediction is
    /// on; otherwise only remembers `on`. Returns the snapshot.
    pub fn set_candidate_length_cap(&mut self, on: bool) -> Result<Output, EngineError> {
        self.setting_changed();
        self.cand_len_cap = on;
        if !self.composition_empty() && self.predict_on {
            self.recompute_pred();
        }
        self.handled()
    }

    /// zhen-mixed (docs/spec/english-mixed.md §1.1): the switch, default off. Takes effect only with the standard layout and the
    /// abbreviation composer off (`shadow_on`). Off clears the shadow and
    /// keeps an open English run as a token. Returns the snapshot.
    pub fn set_english(&mut self, on: bool) -> Result<Output, EngineError> {
        self.english = on;
        self.setting_changed();
        if !on {
            let flushed = self.digit_flush()?; // docs/spec/english-mixed.md §1.1, §11: the buffered digits become tokens
            self.clear_shadow();
            return self.settle_with(flushed);
        }
        self.sync_shadow();
        self.handled()
    }

    /// Fuzzy input (docs/spec/decoder.md §6): `flags` bits 0-3 are the sound pairs (ㄣ/ㄥ, ㄓ/ㄗ, ㄔ/ㄘ, ㄕ/ㄙ), bit 4 the
    /// adjacent-key slips, bit 5 the one-symbol slips (docs/contracts/typo-one-sym.md); default 0, and 0 changes nothing. Bits 4 and 5 only decide whether
    /// `finish_syllable` accepts a syllable the lexicon lacks; a syllable already accepted is decoded whatever the flags say, except that bit 5 changes
    /// which table reads it: when the flags change so that the table reading an accepted syllable changes (bit 5 turned off, or turned on without
    /// bit 4), every such syllable of the composition (not under a fixed word) is first stored as the lexicon reading it is shown as now, so the
    /// composition keeps its text and a syllable only the old table knew never has no path. Recomputes the composition and returns the
    /// snapshot, like `set_demote`; on a decode failure the engine resets itself. Bits above 5 are ignored here (the C ABI refuses them).
    pub fn set_fuzzy(&mut self, flags: u32) -> Result<Output, EngineError> {
        self.setting_changed();
        self.invalidate();
        let new = flags & FUZZY_ALL;
        let (old_eff, new_eff) = (self.effective_fuzzy(), effective_fuzzy_for(self.layout, new));
        let leaves = old_eff & FUZZY_ONE_SYM != 0 && new_eff & FUZZY_ONE_SYM == 0;
        let enters = (old_eff & FUZZY_ONE_SYM == 0 || old_eff & FUZZY_ADJACENT != 0) && new_eff & FUZZY_ONE_SYM != 0 && new_eff & FUZZY_ADJACENT == 0;
        if (leaves || enters) && self.freeze_unknown_syllables(old_eff) {
            self.cands = None;
        }
        self.fuzzy = new;
        self.clear_pred();
        if let Err(e) = self.refresh() {
            self.clear_all();
            return Err(e);
        }
        self.handled()
    }

    /// The flags the decoder, the window and the learning guard read (`effective_fuzzy_for`).
    fn effective_fuzzy(&self) -> u32 {
        effective_fuzzy_for(self.layout, self.fuzzy)
    }

    /// The learning guard's positions (docs/contracts/typo-one-sym.md §2.4 item 4.0): the syllables of the composition that stand there only because bit 5
    /// accepted them, i.e. no lexicon syllable, no punctuation or English token, no sandhi or neutral one (the position table marks those unaccepted). Never one
    /// the user chose from the window (that rewrites the syllable to the lexicon reading). All false without bit 5, so with the flags 0 and 16 nothing of learning changes.
    fn bit5_unknown(&self) -> Vec<bool> {
        if self.effective_fuzzy() & FUZZY_ONE_SYM == 0 {
            return vec![false; self.syls.len()];
        }
        let t3 = self.t3();
        (0..self.syls.len()).map(|i| !self.is_punct(i) && !self.lex.has_syllable(&self.syls[i]) && !t3.get(i).is_some_and(|p| p.unaccepted)).collect()
    }

    /// `set_fuzzy`'s rewrite: each syllable of the composition the lexicon lacks (not under a fixed word, no punctuation or English token, no sandhi one) is
    /// stored as the lexicon reading the decode under `old_eff` shows it as (`path_readings_with_fuzzy`); one the path does not cover through a table
    /// (a learned word's) falls back to its first adjacent, then one-symbol, alternative. Returns whether a syllable changed.
    fn freeze_unknown_syllables(&mut self, old_eff: u32) -> bool {
        let t3 = self.t3();
        let unknown = |i: usize| {
            !self.fixed.iter().any(|f| f.start <= i && i < f.end) && !self.is_punct(i) && !t3.get(i).is_some_and(|x| x.unaccepted) && !self.lex.has_syllable(&self.syls[i])
        };
        let mut edits: Vec<(usize, String)> = Vec::new();
        for (p, s) in self.path.iter().zip(self.path_starts()) {
            if p.is_punct || !(s..s + p.syllables).any(unknown) {
                continue;
            }
            let used = self.lex.path_readings_with_fuzzy(&self.syls[s..s + p.syllables], [(p.word.as_str(), p.syllables)], &t3[s..s + p.syllables], old_eff);
            for i in (s..s + p.syllables).filter(|&i| unknown(i)) {
                let now = &used[i - s];
                if self.lex.has_syllable(now) {
                    edits.push((i, now.clone()));
                } else if let Some(a) = self.lex.adjacent_alts(now).first().or(self.lex.one_sym_alts(now).first()) {
                    edits.push((i, a.to_string()));
                }
            }
        }
        for (i, to) in &edits {
            self.syls[*i] = to.clone();
        }
        !edits.is_empty()
    }

    /// Third-tone and colloquial sandhi (docs/spec/decoder.md §6.6, §8.7, docs/spec/keys-and-composition.md §3.2): on (the default), `finish_syllable` accepts
    /// an unaccepted syllable and a neutral syllable K takes, the decoder and the window read the sandhi and the neutral source, and the `colloq` rows of the
    /// lexicon are read. Off, the position table is empty and those rows are not read; every unaccepted syllable or neutral tail the composition holds (not
    /// under a fixed word) is stored as its third tone Xˇ, as a window pick rewrites a span to the lexicon reading, any other neutral syllable K took as the
    /// best-scoring of X, Xˊ, Xˇ, Xˋ, and a fixed word only the `colloq` rows have is unfixed, so the composition keeps a path. Recomputes the composition and
    /// returns the snapshot, like `set_fuzzy`; on a decode failure the engine resets itself.
    pub fn set_tone3(&mut self, on: bool) -> Result<Output, EngineError> {
        self.setting_changed();
        self.invalidate();
        if self.tone3 && !on {
            // A fixed word only the colloquial rows have under its reading (docs/spec/decoder.md §8.7) is no word of the lexicon read with the switch off: unfixed,
            // so its syllables are free like the rest.
            let (syls, lex) = (&self.syls, &self.lex);
            self.fixed.retain(|f| {
                let r = &syls[f.start..f.end];
                lex.has(r, &f.word) || !lex.has_with(r, &f.word, true)
            });
            // The rule reads the syllables as typed: an earlier rewrite to Xˇ must not change whether the previous one rose.
            let orig = self.syls.clone();
            for i in 0..self.syls.len() {
                let s = &orig[i];
                let fixed = self.fixed.iter().any(|f| f.start <= i && i < f.end);
                if fixed {
                    continue;
                }
                // The neutral tail the sandhi rule took (after a second tone) is stored as Xˇ, as before K; any other neutral syllable K took as its best alternative.
                let prev_rises = i.checked_sub(1).is_some_and(|j| orig[j].ends_with('ˊ') && !orig[j].starts_with(PUNCT_PREFIX));
                if crate::tone3::unaccepted(&self.lex, s) || (prev_rises && crate::tone3::unaccepted_tail(&self.lex, s)) {
                    if let Some(t) = crate::tone3::third(s) {
                        self.syls[i] = t;
                    }
                } else if let Some(t) = self.neutral_best(s) {
                    self.syls[i] = t;
                }
            }
        }
        self.tone3 = on;
        self.clear_pred();
        if let Err(e) = self.refresh() {
            self.clear_all();
            return Err(e);
        }
        self.handled()
    }

    /// Whether third-tone sandhi is on (`set_tone3`).
    pub fn tone3(&self) -> bool {
        self.tone3
    }

    /// Whether the colloquial rows are read (docs/spec/decoder.md §8.7): the switch on and a model loaded, as the decoder reads them.
    fn colloq_on(&self) -> bool {
        self.tone3 && self.lm.is_some()
    }

    /// K (docs/spec/keys-and-composition.md §3.2 (2)): for ˙X without a one-syllable entry, the one of X, Xˊ, Xˇ, Xˋ whose best one-syllable entry scores
    /// highest (the earlier one on a tie); `None` when `syl` is no such syllable.
    fn neutral_best(&self, syl: &str) -> Option<String> {
        if !crate::tone3::neutral_single(&self.lex, syl) {
            return None;
        }
        let top = |t: &String| self.lex.entries(std::slice::from_ref(t)).first().map(|e| e.1);
        let mut best: Option<(f64, String)> = None;
        for t in crate::tone3::four(syl)? {
            if let Some(sc) = top(&t).filter(|&sc| best.as_ref().is_none_or(|b| sc > b.0)) {
                best = Some((sc, t));
            }
        }
        best.map(|b| b.1)
    }

    /// The position table of the composition (docs/spec/decoder.md §6.6), the one function the evaluation CLI calls too.
    fn t3(&self) -> Vec<crate::tone3::Pos> {
        crate::tone3::positions(&self.syls, &self.lex, self.tone3)
    }

    /// docs/spec/english-mixed.md §1.1, §1.2, §3.2: the switch "a key over a symbol already in its column is English" (rule 9; default on). Only where
    /// `shadow_on`. Nothing is open that the switch could close: an English run is text and stays. Returns the snapshot.
    pub fn set_english_overwrite(&mut self, on: bool) -> Result<Output, EngineError> {
        self.english_overwrite = on;
        self.setting_changed();
        self.handled()
    }

    /// docs/spec/english-mixed.md §11 as docs/spec/english-mixed.md §1.1, §1.2, §3.2 changes it: the key-order switch (default on) covers the keys out of
    /// column order (rule 9) and the tone key that makes a syllable the lexicon lacks (rule 10). Only where `shadow_on`; an English
    /// run is text and stays. Returns the snapshot.
    pub fn set_english_key_order(&mut self, on: bool) -> Result<Output, EngineError> {
        self.english_key_order = on;
        self.setting_changed();
        self.handled()
    }

    /// docs/spec/english-mixed.md §1.1, §3.2: the reversal guard (default on). On, a key in a column before a filled one goes into its column
    /// (Apple Zhuyin), so trigger (b) never fires; (a) overwrite and (c) are untouched. Returns the snapshot.
    pub fn set_english_reversal_guard(&mut self, on: bool) -> Result<Output, EngineError> {
        self.english_reversal_guard = on;
        self.setting_changed();
        self.handled()
    }

    /// docs/spec/keys-and-composition.md §9.3: the ASCII state of the shell's per-app Shift toggle. `on` ends what the composition has open, in
    /// order: the window or the row (closed, nothing chosen), the digit run (flushed as `set_english(false)` does), the unfinished
    /// syllable (with English mixing on its shadow keys open the English run, as Shift+letter does; with it off the syllable is dropped,
    /// as Esc drops it). Without a syllable the run opens with the first
    /// character. `off` ends an open run as a token (no space) and the usual rules return. An empty composition only records the
    /// state. Returns the snapshot (`commit` is what a unit that reaches `MAX_SYLLABLES` sent).
    pub fn set_ascii(&mut self, on: bool) -> Result<Output, EngineError> {
        self.setting_changed();
        if self.ascii == on {
            return self.handled();
        }
        self.ascii = on;
        if !on {
            return self.settle_run();
        }
        // Where the ASCII state is not available (Eten, abbreviation composer) the engine keeps no English run in a composition: only the
        // state is recorded, the composition stays as it is and the ASCII branch of `dispatch_key` does not apply (the shell sends the
        // composition first, docs/spec/keys-and-composition.md §9.2).
        if !self.ascii_on() {
            return self.handled();
        }
        self.cands = None;
        self.clear_pred();
        let (commit, _) = self.digit_flush()?;
        if self.pend.iter().any(Option::is_some) {
            // English mixing off: there is no key record, and the unfinished syllable is dropped (docs/spec/keys-and-composition.md §9.3 step 3).
            let mixing = self.shadow_on();
            let keep = std::mem::take(&mut self.typed);
            self.clear_pend();
            if mixing {
                self.run = keep;
                self.run_ascii = true;
                if self.cursor < self.syls.len() {
                    self.refresh()?;
                }
            }
        }
        let mut o = self.view(true, String::new());
        o.commit = commit;
        Ok(o)
    }

    /// The syllables of the composition (test and tool hook): what `finish_syllable` stored, or a choice rewrote.
    pub fn syllables(&self) -> &[String] {
        &self.syls
    }

    /// Whether the latest `key()` reverted the unfinished syllable to its raw keys, and which trigger did (tool hook of zhen-revert
    /// docs/spec/english-mixed.md §13.3; the simulator asks after each key and never repeats the rule).
    #[doc(hidden)]
    pub fn english_reverted(&self) -> Option<Revert> {
        self.reverted
    }

    /// Whether the latest `key()` was a tone key that split an English run into an English token and a syllable (tool hook).
    #[doc(hidden)]
    pub fn english_split(&self) -> bool {
        self.split
    }

    /// The shadow in typed order (test and tool hook): the keycaps of the unfinished syllable's symbols.
    #[doc(hidden)]
    pub fn english_shadow(&self) -> String {
        self.shadow_text()
    }

    /// The key record (test and tool hook; docs/spec/english-mixed.md §2): every key of the unfinished syllable in the order typed.
    #[doc(hidden)]
    pub fn english_record(&self) -> &str {
        &self.typed
    }

    /// The Zhuyin replay of `keys` (test hook; docs/spec/english-mixed.md §2): the syllables it completes and the columns left.
    #[doc(hidden)]
    pub fn english_replay(&self, keys: &str) -> Option<(Vec<String>, [Option<char>; 3])> {
        self.replay(keys).map(|r| (r.done, r.pend))
    }

    /// The unfinished syllable's columns, for the typing simulator (tool hook; no letters, no tone).
    #[doc(hidden)]
    pub fn english_columns(&self) -> [Option<char>; 3] {
        self.pend
    }

    /// docs/spec/english-mixed.md §1.2: where the shadow is recorded and the reverts can happen (English is told apart automatically): English mixing on,
    /// and where the ASCII state is available (`ascii_on`).
    fn shadow_on(&self) -> bool {
        self.english && self.ascii_on()
    }

    /// docs/spec/keys-and-composition.md §9.3: where the ASCII state is available (`set_ascii(1)` converts the composition and the ASCII branch of
    /// `dispatch_key` applies): the standard layout and the abbreviation composer off, whatever the English mixing switch says. Everything else that
    /// English mixing brings (reverts, the digit layer, Shift+letter, the mixed-word edges, Backspace reopening a token, Space after a token) asks `shadow_on`.
    fn ascii_on(&self) -> bool {
        !self.abbr && self.layout == Layout::Standard
    }

    fn clear_shadow(&mut self) {
        self.shadow_key = [None; 3];
        self.typed.clear();
        (self.pend_dup, self.pend_rev, self.pend_plain) = (0, false, false);
    }

    /// Where the reverts become possible with a syllable half typed (the switch turned on, the abbreviation composer off), the shadow
    /// catches up with the columns, so that it always matches them (docs/spec/english-mixed.md §2, §3.1): the keycap of a symbol is the one key.
    fn sync_shadow(&mut self) {
        if self.shadow_on() && self.pend_prev.is_empty() {
            for col in 0..3 {
                self.shadow_key[col] = self.pend[col].and_then(|sym| self.layout.key_of_symbol(sym)).map(|k| (k, col as u8));
            }
            self.typed = self.shadow_text(); // docs/spec/english-mixed.md §3.1: the record is what the shadow knows
            (self.pend_dup, self.pend_rev) = (0, false);
        }
    }

    /// The keys of the unfinished syllable in the order typed (zhen-reapply).
    fn shadow_text(&self) -> String {
        let mut keys = self.shadow_key;
        keys.sort_by_key(|e| e.map_or(u16::MAX, |(_, rank)| rank as u16));
        keys.iter().flatten().map(|&(c, _)| c).collect()
    }

    /// `ch` as the entry of column `col`, one after the latest key: a key that replaces a column's key (overwrite off) is the newest,
    /// and the replaced key leaves the record with its symbol.
    fn shadow_entry(shadow: &[Option<Shadow>; 3], ch: char) -> Shadow {
        (ch, shadow.iter().flatten().map(|&(_, r)| r.saturating_add(1)).max().unwrap_or(0))
    }

    /// docs/spec/english-mixed.md §3.1: one Zhuyin key of the unfinished syllable now sits in column `col`.
    fn shadow_push(&mut self, ch: char, col: usize) {
        if self.shadow_on() {
            self.shadow_key[col] = Some(Self::shadow_entry(&self.shadow_key, ch));
            if self.typed.len() < ENGLISH_RECORD_MAX {
                self.typed.push(ch);
            }
        }
    }

    /// docs/spec/english-mixed.md §2 (Zhuyin replay): what `keys` make when typed from nothing with English mixing off: a Zhuyin key
    /// goes into its column (replacing what is there), a tone key completes the syllable when the lexicon has it and is ignored
    /// otherwise (rule 10; no adjacent-key acceptance). The shadow and the key record are those of the syllable left unfinished.
    /// `None`: a character that is neither (an upper-case letter, any other character), or a tone key before any Zhuyin key.
    fn replay(&self, keys: &str) -> Option<Replay> {
        let mut r = Replay::default();
        for ch in keys.chars() {
            if let Some((col, sym)) = Layout::Standard.symbol_of(ch) {
                r.shadow[col] = Some(Self::shadow_entry(&r.shadow, ch));
                r.pend[col] = Some(sym);
                if r.typed.len() < ENGLISH_RECORD_MAX {
                    r.typed.push(ch);
                }
                continue;
            }
            let tone = Layout::Standard.tone_of(ch)?;
            let mut syl: String = r.pend.iter().flatten().collect();
            if syl.is_empty() {
                if r.done.is_empty() {
                    return None; // on an empty composition a tone key types its mark (rule 22a): not a replay
                }
                continue; // after a completed syllable a tone key does nothing
            }
            syl.push_str(TONE_MARKS[tone]);
            if !self.lex.entries(std::slice::from_ref(&syl)).is_empty() {
                r = Replay { done: std::mem::take(&mut r.done), ..Replay::default() };
                r.done.push(syl);
            }
        }
        Some(r)
    }

    /// docs/spec/english-mixed.md §2 (Chinese item): the one syllable the replay of `keys` makes (unfinished: with tone 1), when it
    /// makes exactly one and the lexicon has it.
    fn replay_chinese(&self, keys: &str) -> Option<String> {
        let r = self.replay(keys)?;
        let rest: String = r.pend.iter().flatten().collect();
        let syl = match (r.done.as_slice(), rest.is_empty()) {
            ([], false) => rest,
            ([one], true) => one.clone(),
            _ => return None,
        };
        (!self.lex.entries(std::slice::from_ref(&syl)).is_empty()).then_some(syl)
    }

    // (`english_writing` is a free function below the impl: the CLI asks it too.)
    /// The writings of an English word, in the order of the window (docs/spec/english-mixed.md §4.4, §5.2): as it stands, then lower case,
    /// Capitalised, UPPER (no repeat).
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

    /// The one place an English token goes in at the cursor: the unfinished syllable and the shadow go. `zh`: the Chinese syllable a
    /// revert remembers (docs/spec/english-mixed.md §3.2), the last item of the token's writings.
    fn put_english(&mut self, reading: String, word: String, zh: Option<String>) -> Result<String, EngineError> {
        self.clear_pend();
        self.pred_dirty = true;
        Ok(self.insert_token(reading, Some(word), zh)?.commit)
    }

    /// docs/spec/english-mixed.md §5.3: the English token at `at` goes and an English run opens there, holding its text without the last letter (a
    /// one-letter token just goes). The run opens before the removal, so the decode splits at it (docs/spec/english-mixed.md §4.7).
    fn reopen_token(&mut self, at: usize) -> Result<(), EngineError> {
        let typed = english_of(&self.syls[at]).unwrap_or_default();
        let word = self.fixed.iter().find(|f| f.start == at).map_or(typed, |f| f.word.as_str());
        // docs/spec/english-mixed.md §5.3: a writing the table put there goes back to what was typed, so taking a letter and typing
        // it again ends as the first time did (`ChatGPT` reopens as `Chatgp`, never `ChatGP`). Any other text is one the user chose
        // (picked from the list, or typed in the ASCII state): it reopens as it stands and the table leaves it alone when it ends.
        // docs/spec/english-mixed.md §5.7: so does the writing the mixed-word table put there.
        let by_table = word == english_writing(typed) || self.fixed.iter().any(|f| f.start == at && f.own.is_some());
        let mut letters = if by_table { typed.to_string() } else { word.to_string() };
        letters.pop();
        self.run = letters;
        self.run_ascii = (self.ascii || !by_table) && !self.run.is_empty();
        // docs/spec/english-mixed.md §5.3: a token with a Chinese item reopens as a run a revert opened.
        self.run_rev = !self.run.is_empty() && self.fixed.iter().any(|f| f.start == at && f.zh.is_some());
        self.revert_point = None;
        self.cursor = at;
        self.remove_syllable(at)?;
        self.pred_dirty = true;
        Ok(())
    }

    /// The first key of a new syllable: rule 14.
    fn start_syllable(&mut self, ch: char, col: usize, sym: char) {
        self.shadow_push(ch, col);
        self.pend[col] = Some(sym);
        self.pred_dirty = true;
    }

    // ---- docs/spec/english-mixed.md §3.2: the reverts ----

    /// The unfinished syllable goes and an English run holding `text` opens at the cursor (the output shows it with the run's
    /// underline). `point`: what the tone moved up brings back (docs/spec/english-mixed.md §4.2), for (c) at a tone key.
    fn revert_to(&mut self, kind: Revert, text: String, point: Option<([Option<char>; 3], [Option<Shadow>; 3], String)>) -> Result<Output, EngineError> {
        self.clear_pend();
        self.run = text;
        self.run_rev = true;
        self.revert_point = point;
        self.reverted = Some(kind);
        self.pred_dirty = true;
        if self.cursor < self.syls.len() {
            self.refresh()?; // the run opened in front of units: they decode apart from it (docs/spec/english-mixed.md §4.7)
        }
        Ok(self.view(true, String::new()))
    }

    /// Rule 9 with a trigger (a) or (b): the Zhuyin key `ch` (symbol `sym` for column `col`) does not go into the columns; the
    /// run is the key record and this key (docs/spec/english-mixed.md §3.2).
    fn revert_keys(&mut self, kind: Revert, ch: char) -> Result<Output, EngineError> {
        let text = format!("{}{ch}", self.typed);
        self.revert_to(kind, text, None)
    }

    /// Rule 10 with trigger (c): the syllable the lexicon lacks (and no adjacent key rescued it) reverts. Space (`tone` 0) needs only
    /// the mixing switch and ends the run at once, so one token comes out; a tone key needs the key-order switch and leaves the run open
    /// with the digit on it. `None`: not reverted, rule 10 ignores the key as it always did.
    fn revert_missing(&mut self, tone: usize) -> Result<Option<Output>, EngineError> {
        if !self.shadow_on() || (tone > 0 && !self.english_key_order) {
            return Ok(None);
        }
        let mut text = self.typed.clone();
        let point = (self.pend, self.shadow_key, text.clone());
        if tone == 0 {
            self.clear_pend();
            self.run = text;
            self.run_rev = true;
            self.reverted = Some(Revert::Missing);
            let commit = self.end_run()?;
            return Ok(Some(self.view(true, commit)));
        }
        text.push(TONE_KEYS_STANDARD[tone - 1]);
        self.revert_to(Revert::Missing, text, Some(point)).map(Some)
    }

    /// docs/spec/english-mixed.md §4.2, §4.3, §4.5: a tone key on the open run. The tail syllable of two or more symbols that the tone makes a
    /// lexicon syllable splits the run: the text before it becomes an English token and the tail a syllable. Anything else puts the
    /// digit on the run.
    fn run_tone(&mut self, c: char, tone: usize) -> Result<Output, EngineError> {
        let chars: Vec<char> = self.run.chars().collect();
        let mut tail: Vec<(usize, char)> = Vec::new();
        let mut above = 3;
        for ch in chars.iter().rev() {
            match Layout::Standard.symbol_of(*ch) {
                Some((col, sym)) if col < above && tail.len() < 3 => {
                    tail.push((col, sym));
                    above = col;
                }
                _ => break,
            }
        }
        let syl: String = tail.iter().rev().map(|&(_, sym)| sym).chain(TONE_MARKS[tone].chars()).collect();
        if tail.len() < 2 || self.lex.entries(std::slice::from_ref(&syl)).is_empty() {
            let commit = self.run_push(c)?;
            return Ok(self.view(true, commit));
        }
        let head: String = chars[..chars.len() - tail.len()].iter().collect();
        self.run.clear();
        // docs/spec/english-mixed.md §4.4, §5.3: the head is a token in its writing, as typed when the run keeps its text (`end_run`).
        let writing = if std::mem::take(&mut self.run_ascii) { head.clone() } else { english_writing(&head) };
        self.run_rev = false;
        self.revert_point = None;
        self.split = true;
        let mut commit = if head.is_empty() { String::new() } else { self.put_english(format!("{PUNCT_PREFIX}{head}"), writing, None)? };
        self.pred_dirty = true;
        commit.push_str(&self.insert_token(syl, None, None)?.commit);
        Ok(self.view(true, commit))
    }

    /// docs/spec/english-mixed.md §4.3 (the split at the closing key): the cursor is at the end, the run is upper-case letters U (1 to 16) and then only
    /// Zhuyin keys typed without Shift, it was not typed in the ASCII state nor opened by a revert, and those keys with the closing key (`tone`: 0 Space, 1 to 4 the tone
    /// keys) replay to exactly one syllable of the lexicon that starts a reading of U in the mixed-word table. Then (U, the syllable).
    fn mixed_split(&self, tone: usize) -> Option<(String, String)> {
        let table = self.mixed.as_ref().filter(|_| self.english && !self.run_ascii && !self.run_rev && self.cursor == self.syls.len())?;
        let n = self.run.bytes().take_while(u8::is_ascii_uppercase).count();
        let (u, keys) = self.run.split_at(n);
        if !(1..=16).contains(&n) || keys.is_empty() || !keys.chars().all(|c| Layout::Standard.symbol_of(c).is_some()) {
            return None;
        }
        let syl = match tone {
            0 => self.replay_chinese(keys)?,
            t => {
                let r = self.replay(&format!("{keys}{}", TONE_KEYS_STANDARD[t - 1]))?;
                let [one] = r.done.as_slice() else { return None };
                r.pend.iter().all(Option::is_none).then(|| one.clone())?
            }
        };
        table.starts(u, &syl).then(|| (u.to_string(), syl))
    }

    /// The split `mixed_split` found: U becomes an English token, the syllable a finished syllable behind it, the run closes; the decoder
    /// then has the table's word behind the token as behind any other (`mixed_edges`).
    fn run_split(&mut self, u: String, syl: String, tone: usize) -> Result<Output, EngineError> {
        // docs/spec/english-mixed.md §4.3: the keys as an English word (a tone key that closed the run is one of them, Space is not).
        let mut keys = std::mem::take(&mut self.run);
        keys.extend((tone > 0).then(|| TONE_KEYS_STANDARD[tone - 1]));
        let lower = keys.to_ascii_lowercase();
        let word = english_table().contains_key(&lower) || self.auto_table.as_ref().is_some_and(|t| t.has(&lower));
        self.run_rev = false;
        self.revert_point = None;
        self.split = true;
        let writing = english_writing(&u);
        let mut commit = self.put_english(format!("{PUNCT_PREFIX}{u}"), writing, None)?;
        let at = self.cursor;
        commit.push_str(&self.insert_token(syl.clone(), None, None)?.commit);
        if let Some(f) = self.fixed.iter_mut().find(|f| word && commit.is_empty() && f.end == at && f.start + 1 == at) {
            f.whole = Some((keys, syl));
        }
        Ok(self.view(true, commit))
    }

    // ---- docs/spec/english-mixed.md §5.7: the mixed word in the decoder ----

    /// The English part the syllables from `a` on stand behind: the English token right before `a`; at the composition's start, with a small
    /// model that declares a left length loaded (only then does `left_full` keep a lone letter), the 1 to 16 ASCII letters and digits the text before
    /// the composition ends with, unless they may be the cut-off tail of a longer word.
    fn mixed_english(&self, a: usize) -> Option<String> {
        if a > 0 {
            return english_of(&self.syls[a - 1]).map(str::to_string);
        }
        self.tinylm.as_ref().and_then(|m| m.left_len())?;
        let n = self.left_full.chars().rev().take_while(char::is_ascii_alphanumeric).count();
        let cut = self.left_cut && n == self.left_full.chars().count();
        ((1..=16).contains(&n) && !cut).then(|| last_chars(&self.left_full, n))
    }

    /// docs/spec/decoder.md §4.7, the one rule of the edge: with English mixing on, the stretch `[a, stop)` stands right behind an English part (`mixed_english`;
    /// not behind a token whose mark is dead) and its first syllables are a reading the table has for it: (how many syllables, the row's Han part), one for
    /// every such row. Only a Han part that can stand as a fixed word (a lexicon word under those syllables, or one single-character entry per
    /// syllable) gives an edge, so a later freeze of the path never holds a word the lexicon cannot score.
    fn mixed_edges<'t>(&self, table: &'t MixedTable, capped: &CappedLexicon, a: usize, stop: usize) -> Vec<(usize, &'t str)> {
        let dead = a > 0 && self.fixed.iter().any(|f| f.end == a && f.start + 1 == a && f.mixed.dead);
        let Some(english) = self.mixed_english(a).filter(|_| self.english && !dead) else { return Vec::new() };
        (1..=crate::mixed::MIXED_MAX_SYLLABLES.min(stop - a))
            .filter_map(|k| {
                let r = &self.syls[a..a + k];
                let han = &table.get(&english, r)?[english.len()..];
                let singles = || han.chars().zip(r).all(|(c, s)| self.lex.has(std::slice::from_ref(s), c.encode_utf8(&mut [0; 4])));
                (capped.best_lp_delta(r, han).is_some() || singles()).then_some((k, han))
            })
            .collect()
    }

    /// docs/spec/english-mixed.md §4.4, after every decode and every applied rerank: an English token shows the table's writing of the English part while the
    /// text shown takes a mixed word behind it, and its own text again when it does not. Taken: the word after the token carries the decoder's marker, or
    /// fixed words stand over exactly a row's syllables and show its Han part (the user chose the same word in the window). `path` and `display` follow.
    fn mixed_sync(&mut self) {
        let Some(table) = self.mixed.clone() else { return };
        let (mut at, mut changed) = (0, false);
        for i in 0..self.path.len() {
            let n = self.path[i].syllables;
            let token = self.fixed.iter().position(|f| self.path[i].is_punct && f.start == at && f.end == at + 1 && !f.mixed.dead);
            if let Some((fi, e)) = token.zip(english_of(&self.syls[at])) {
                let row = |k: usize| table.get(e, self.syls.get(at + 1..at + 1 + k)?);
                let marked = self.path.get(i + 1).filter(|w| w.mixed).and_then(|w| row(w.syllables));
                let fixed = || (1..=crate::mixed::MIXED_MAX_SYLLABLES).filter(|_| self.english).filter_map(row).find(|w| self.fixed_show(at + 1, &w[e.len()..]));
                let writing = marked.or_else(fixed).map(|w| w[..e.len()].to_string());
                let f = &mut self.fixed[fi];
                match writing {
                    Some(w) if w != f.word => {
                        let old = std::mem::replace(&mut f.word, w);
                        f.own.get_or_insert(old);
                    }
                    Some(_) => {}
                    None => {
                        if let Some(own) = f.own.take() {
                            f.word = own;
                        }
                    }
                }
                if self.path[i].word != f.word {
                    self.path[i].word = f.word.clone();
                    changed = true;
                }
            }
            at += n;
        }
        if changed {
            // `refresh_lm` builds the display from exactly the strings of `path`.
            self.display = self.path.iter().map(|p| p.word.as_str()).collect();
        }
    }

    /// Fixed words stand over exactly the syllables from `a` that `han` has characters for, and show `han`.
    fn fixed_show(&self, a: usize, han: &str) -> bool {
        let (end, mut text, mut pos) = (a + han.chars().count(), String::new(), a);
        for f in self.fixed.iter().filter(|f| f.start >= a && f.end <= end) {
            if f.start != pos {
                return false;
            }
            text.push_str(&f.word);
            pos = f.end;
        }
        pos == end && text == han
    }

    /// After every key and pick. docs/spec/learning.md §4.3: a pick from the run's row is no longer one once the syllables behind the token are not that
    /// row's reading (Backspace took one). docs/spec/english-mixed.md §4.3: the English word a split took apart (`Fixed::whole`) is kept only while its one
    /// syllable stands behind the token with the cursor at the end.
    fn mixed_sweep(&mut self) {
        let Some(table) = self.mixed.clone() else { return };
        let (n, at_end) = (self.syls.len(), self.cursor == self.syls.len());
        let syls = &self.syls;
        for f in self.fixed.iter_mut() {
            if f.whole.as_ref().is_some_and(|(_, syl)| !(at_end && f.end + 1 == n && syls[f.end] == *syl)) {
                f.whole = None;
            }
            if f.mixed.picked > 0 && !english_of(&syls[f.start]).zip(syls.get(f.end..f.end + f.mixed.picked)).is_some_and(|(e, r)| table.get(e, r).is_some()) {
                f.mixed.picked = 0;
            }
        }
    }

    /// docs/spec/prediction.md §3.5: the English words completions come from (see the field).
    fn english_index(&mut self) -> Arc<Vec<(String, Option<u64>)>> {
        if let Some(i) = &self.en_index {
            return i.clone();
        }
        let mut words: HashMap<String, Option<u64>> = english_table().keys().map(|w| (w.clone(), None)).collect();
        if let Some(t) = &self.auto_table {
            words.extend(t.words().filter(|(w, _)| w.as_bytes()[0].is_ascii_alphabetic()).map(|(w, n)| (w.to_string(), Some(n))));
        }
        let mut v: Vec<(String, Option<u64>)> = words.into_iter().collect();
        v.sort();
        let i = Arc::new(v);
        self.en_index = Some(i.clone());
        i
    }

    /// Test hook (docs/spec/prediction.md §3.5): false leaves the English completions out of the row of an open run; the mixed words stay.
    #[doc(hidden)]
    pub fn set_english_complete(&mut self, on: bool) {
        self.en_complete = on;
    }

    /// docs/spec/prediction.md §3.5: the row of an open English run that Shift opened (it starts with an upper-case letter, is letters only, was not typed in
    /// the ASCII state nor opened by a revert), with the cursor at the end. First the mixed words when the run is 1 to 16 upper-case letters U: the
    /// writings of the table's rows for U, those with a learned `^en:` record first by weight, the rest by count, then by writing. Then the English
    /// words that start with the run's letters and are longer, by count (those without one last), then by the word. `PREDICT_MAX` items in all, of
    /// which the completions get at least three (or all of them, when fewer).
    fn run_row(&mut self) -> Vec<Pred> {
        let u = self.run.clone();
        let shift = (1..=ENGLISH_RUN_MAX).contains(&u.len()) && u.bytes().all(|b| b.is_ascii_alphabetic()) && u.as_bytes()[0].is_ascii_uppercase();
        if !(shift && self.predict_on && self.english && !self.run_ascii && !self.run_rev && self.cursor == self.syls.len()) {
            return Vec::new();
        }
        let mut rows: Vec<Pred> = Vec::new();
        if let Some(table) = self.mixed.as_ref().filter(|_| u.len() <= 16 && u.bytes().all(|b| b.is_ascii_uppercase())) {
            let key = format!("{EN_PREFIX}{}", u.to_ascii_lowercase());
            let today = if self.learner.is_empty() { 0 } else { self.today() };
            rows = table
                .of_english(&u)
                .into_iter()
                .map(|(w, r, _)| Pred { word: w.to_string(), reading: r.to_vec(), start: self.cursor, weight: self.learner.en_weight(&key, r, w, today), key: key.clone() })
                .collect();
            rows.sort_by(|a, b| b.weight.total_cmp(&a.weight)); // stable: `of_english` gives the count order
        }
        let mut words: Vec<Pred> = Vec::new();
        if self.en_complete {
            let index = self.english_index();
            let lower = u.to_ascii_lowercase();
            let from = index.partition_point(|(w, _)| *w < lower);
            let mut hits: Vec<&(String, Option<u64>)> = index[from..].iter().take_while(|(w, _)| w.starts_with(&lower)).filter(|(w, _)| w.len() > lower.len()).collect();
            // A count of `None` sorts last: `Some(n)` compares above it.
            let order = |a: &&(String, Option<u64>), b: &&(String, Option<u64>)| b.1.cmp(&a.1).then(a.0.cmp(&b.0));
            if hits.len() > PREDICT_MAX {
                hits.select_nth_unstable_by(PREDICT_MAX - 1, order);
                hits.truncate(PREDICT_MAX);
            }
            hits.sort_by(order);
            let caps = u.len() >= 2 && u.bytes().take(2).all(|b| b.is_ascii_uppercase());
            words = hits
                .into_iter()
                .map(|(w, _)| {
                    let word = match english_table().get(w) {
                        Some(official) => official.to_string(),
                        None if caps => w.to_ascii_uppercase(),
                        None => format!("{u}{}", &w[u.len()..]),
                    };
                    Pred { word, reading: Vec::new(), start: self.cursor, weight: 0.0, key: EN_WORD_KEY.to_string() }
                })
                .collect();
        }
        rows.truncate(PREDICT_MAX - words.len().min(3));
        words.truncate(PREDICT_MAX - rows.len());
        rows.extend(words);
        rows
    }

    /// docs/spec/prediction.md §3.5: an English completion of the run's row is chosen. The run becomes the English token of that word; nothing is sent,
    /// nothing is learned.
    fn apply_word_pred(&mut self, p: Pred) -> Result<Output, EngineError> {
        if self.run.is_empty() {
            return self.handled();
        }
        self.run.clear();
        self.run_rev = false;
        self.revert_point = None;
        self.clear_pred();
        let commit = self.put_english(format!("{PUNCT_PREFIX}{}", p.word), p.word, None)?;
        self.pred_dirty = false; // as after any row pick: no row until the next key
        Ok(self.view(true, commit))
    }

    /// docs/spec/prediction.md §3.5: an item of the run's row is chosen. The run closes, U becomes an English token, the row's syllables go in behind it and
    /// are decoded as if they had been typed (docs/spec/decoder.md §4.7); nothing is sent. With learning on the pick is marked for the commit (docs/spec/learning.md §4.3).
    fn apply_mixed_pred(&mut self, p: Pred) -> Result<Output, EngineError> {
        if self.run.is_empty() {
            return self.handled(); // the row is the open run's: without one there is nothing to choose
        }
        let u = std::mem::take(&mut self.run);
        self.run_rev = false;
        self.revert_point = None;
        self.clear_pred();
        let mut commit = self.put_english(format!("{PUNCT_PREFIX}{u}"), u.clone(), None)?;
        let k = p.reading.len();
        for syl in p.reading {
            // ponytail: a composition that reached `MAX_SYLLABLES` on the way was sent whole; what is left of the word starts the next one unfixed.
            commit.push_str(&self.insert_token(syl, None, None)?.commit);
        }
        let token = self.cursor.checked_sub(k + 1);
        if let Some(f) = self.fixed.iter_mut().find(|f| commit.is_empty() && self.learning && Some(f.start) == token && f.end == f.start + 1) {
            f.mixed.picked = k;
        }
        self.pred_dirty = false; // as after any row pick: no row until the next key
        Ok(self.view(true, commit))
    }

    // ---- docs/spec/english-mixed.md §4.1: the English run ----

    /// Ends the run: its text becomes an English token at the cursor (reading `PUNCT_PREFIX` + the text as typed). Returns what the unit
    /// that reaches `MAX_SYLLABLES` sent, if it did. The token of a run a revert opened carries the Chinese item of its characters
    /// (docs/spec/english-mixed.md §3.3).
    fn end_run(&mut self) -> Result<String, EngineError> {
        self.clear_pred(); // docs/spec/prediction.md §3.5: the run's row goes with the run, whoever ends it (a key, a setting)
        let word = std::mem::take(&mut self.run);
        // docs/spec/english-mixed.md §4.4: the token shows the word's writing; a run typed in the ASCII state stays as typed.
        let writing = if std::mem::take(&mut self.run_ascii) { word.clone() } else { english_writing(&word) };
        self.revert_point = None;
        let zh = if std::mem::take(&mut self.run_rev) { self.replay_chinese(&word) } else { None };
        self.put_english(format!("{PUNCT_PREFIX}{word}"), writing, zh)
    }

    /// A character on the run; the 33rd ends it and starts the next one. Returns what ending sent, if it sent anything.
    fn run_push(&mut self, c: char) -> Result<String, EngineError> {
        let commit = if self.run.len() == ENGLISH_RUN_MAX { self.end_run()? } else { String::new() };
        self.run.push(c);
        self.run_ascii |= self.ascii;
        self.revert_point = None; // the run was changed: no tone moved up
        Ok(commit)
    }

    /// A setting that ends English mixing (`set_english(false)`, `set_abbreviation(true)`) keeps the run as a token.
    fn settle_run(&mut self) -> Result<Output, EngineError> {
        let commit = if self.run.is_empty() { String::new() } else { self.end_run()? };
        Ok(self.view(true, commit))
    }

    /// Shift+letter with English mixing on: the key record (every key is a Zhuyin key, and a run takes all of them, docs/spec/english-mixed.md §4.2) and
    /// `upper` open a run. An open window closes with it.
    fn start_run(&mut self, upper: char) -> Result<Output, EngineError> {
        let keep = std::mem::take(&mut self.typed);
        self.cands = None;
        self.clear_pend();
        self.run = keep;
        let commit = self.run_push(upper)?;
        self.pred_dirty = true; // docs/spec/prediction.md §3.5: the run's own row
        if self.cursor < self.syls.len() {
            self.refresh()?; // the run opened in front of units: they decode apart from it (docs/spec/english-mixed.md §4.7)
        }
        Ok(self.view(true, commit))
    }

    /// A run that closed without becoming a token (Esc, Backspace to empty): the composition decodes as one stretch again, since
    /// the split of docs/spec/english-mixed.md §4.7 holds only while the run is open. Nothing to do while letters are left.
    fn run_closed(&mut self) -> Result<(), EngineError> {
        if self.run.is_empty() && !self.syls.is_empty() {
            self.refresh()?;
        }
        Ok(())
    }

    /// docs/spec/english-mixed.md §4.2 (tone moved up): the run is a revert (c) at a tone key, not changed since, and `ch` is a Zhuyin key of a column that revert left
    /// free; when the columns with `ch` and that tone make a syllable of the lexicon, the Zhuyin comes back with `ch` in its column and the tone closes it. `None`
    /// (nothing done) when any of that is not so. Only with the reversal guard on; the caller checks it.
    fn tone_early(&mut self, ch: char) -> Result<Option<Output>, EngineError> {
        let Some((col, sym)) = Layout::Standard.symbol_of(ch) else { return Ok(None) };
        let Some((pend, shadow, record)) = &self.revert_point else { return Ok(None) };
        let (pend, shadow) = (*pend, *shadow);
        let Some(tone) = self.run.chars().last().and_then(|c| Layout::Standard.tone_of(c)) else { return Ok(None) };
        if pend[col].is_some() || self.run.chars().count() != record.chars().count() + 1 {
            return Ok(None);
        }
        let mut cols = pend;
        cols[col] = Some(sym);
        let mut syl: String = cols.iter().flatten().collect();
        syl.push_str(TONE_MARKS[tone]);
        if self.lex.entries(std::slice::from_ref(&syl)).is_empty() {
            return Ok(None);
        }
        self.run.clear();
        self.run_ascii = false;
        self.run_rev = false;
        self.revert_point = None;
        (self.pend, self.shadow_key) = (pend, shadow);
        self.typed = self.shadow_text(); // the keys a later key replaced are forgotten: the syllable is read as Zhuyin
        self.shadow_push(ch, col);
        self.pend[col] = Some(sym);
        self.finish_syllable(tone, Vec::new(), false).map(Some)
    }

    /// A key while the run is open (docs/spec/english-mixed.md §4.1, §4.2, §4.3, §4.5): letters and the Zhuyin keys that are not letters
    /// go on it, a tone key goes on it or splits it, Backspace and Esc take it off, Space ends it and is eaten, every other key ends it
    /// and is then handled as it would be. Esc on a run a revert opened puts the Zhuyin replay of its characters in its place.
    fn run_key(&mut self, k: Key) -> Result<Output, EngineError> {
        let m = k.modifiers;
        if self.english_reversal_guard && k.kind == KeyKind::Char && m == 0 {
            if let Some(o) = self.tone_early(k.ch)? {
                return Ok(o);
            }
        }
        // docs/spec/english-mixed.md §4.3: Space or a tone key that closes a syllable of the mixed-word table behind upper-case letters splits the run.
        let closing = match k.kind {
            KeyKind::Space if m == 0 => Some(0),
            KeyKind::Char if m == 0 => Layout::Standard.tone_of(k.ch),
            _ => None,
        };
        if let Some((t, (u, syl))) = closing.and_then(|t| Some((t, self.mixed_split(t)?))) {
            return self.run_split(u, syl, t);
        }
        if k.kind == KeyKind::Char && (m == 0 || m == MOD_SHIFT) && k.ch.is_ascii_alphabetic() {
            let c = if m == MOD_SHIFT { k.ch.to_ascii_uppercase() } else { k.ch.to_ascii_lowercase() };
            let commit = self.run_push(c)?;
            self.pred_dirty = true; // docs/spec/prediction.md §3.5: the run's row follows its letters
            return Ok(self.view(true, commit));
        }
        if k.kind == KeyKind::Char && m == 0 {
            if Layout::Standard.symbol_of(k.ch).is_some() {
                let commit = self.run_push(k.ch)?;
                return Ok(self.view(true, commit));
            }
            if let Some(tone) = Layout::Standard.tone_of(k.ch) {
                return self.run_tone(k.ch, tone);
            }
        }
        match k.kind {
            KeyKind::Backspace => {
                self.run.pop();
                self.run_ascii &= !self.run.is_empty();
                self.run_rev &= !self.run.is_empty();
                self.revert_point = None;
                self.pred_dirty = true;
                self.run_closed()?;
                self.handled()
            }
            KeyKind::Esc => {
                let text = std::mem::take(&mut self.run);
                self.run_ascii = false;
                self.revert_point = None;
                // docs/spec/english-mixed.md §4.5: a run a revert opened becomes the Zhuyin its characters replay to; any other run just goes.
                let replay = if std::mem::take(&mut self.run_rev) { self.replay(&text) } else { None };
                self.pred_dirty = true;
                let mut commit = String::new();
                if let Some(r) = replay {
                    for syl in r.done {
                        commit.push_str(&self.insert_token(syl, None, None)?.commit); // rule 10
                    }
                    (self.pend, self.shadow_key, self.typed) = (r.pend, r.shadow, r.typed);
                    // docs/spec/english-mixed.md §16.1: Esc asked for the Zhuyin; the closing key of the syllable it left unfinished is not a decision point.
                    self.pend_plain = self.pend.iter().any(Option::is_some);
                }
                self.run_closed()?;
                Ok(self.view(true, commit))
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

    /// The output of a key or pick that chose a candidate: the commit of the unit that reaches `MAX_SYLLABLES` (a digit alternative, a row item, a short word), or the
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

    /// docs/spec/prediction.md §6: the abbreviation composer's unit rule runs only with the setting on, the row on (it needs a
    /// model to show) and the cursor at the end of the composition.
    fn abbr_composer(&self) -> bool {
        self.abbr && self.predict_on && self.lm.is_some() && self.cursor == self.syls.len()
    }

    fn clear_pend(&mut self) {
        self.pend = [None; 3];
        self.pend_prev.clear();
        self.clear_shadow();
    }

    /// Switch the profile (default chat; remembered even before a model is loaded), recompute the
    /// composition and return the snapshot. On a decode failure the engine resets itself.
    pub fn set_profile(&mut self, profile: Profile) -> Result<Output, EngineError> {
        self.setting_changed();
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

    /// The frozen fixed words as (first syllable, end, word), in composition order (read-only; docs/spec/keys-and-composition.md §5, §6.3).
    pub fn frozen_words(&self) -> Vec<(usize, usize, String)> {
        self.fixed.iter().filter(|f| f.frozen).map(|f| (f.start, f.end, f.word.clone())).collect()
    }

    /// Where each token of the current best path starts (a word or a punctuation mark), from the composition's start.
    /// `recompute_pred` takes its long starts from it (docs/spec/prediction.md §3.1); public so tests can tell a
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
    /// splits it into sentences (docs/spec/keys-and-composition.md §8.2): the stretch before it closes with `eos`, the next starts from
    /// `<s>`, and the punctuation itself scores nothing. `None` without a model or without words.
    ///
    /// With the trigram level on (`Lm::scoring`: formal with a table, docs/spec/decoder.md §3.4 (a)-(c)) every term carries two history
    /// words, as the decoder starts and joins its stretches: `before` is the word before `prev`. A sentence start (`<s>` history, after punctuation,
    /// or after an English run) is (`<s>`, `<s>`); the first word after a fixed word is (unknown, that word), so it is scored by the bigram alone;
    /// every other word is (the word before the previous one, the previous one). A learned-boosted word is the exception (the learned-boost exception of docs/spec/decoder.md §3.3):
    /// `word_learned`, no table and no Kneser-Ney, as the decoder scores it; it still becomes the history of the next word. The
    /// open English run (`run_cut`) cuts the composition at the cursor the way `refresh_lm` decodes it: the part on the left closes with `eos`
    /// and the part on the right restarts from (`<s>`, `<s>`), like after punctuation. Only the trigram level does this; without it the run is
    /// not a boundary here (the bigram total has never cut there). `total_score` then equals the top decode score of a single stretch, provided the decoder
    /// adds no reading term (docs/spec/decoder.md §5): it is not in `total_score`, so the equality holds after `set_reading_decode(false)` or with a
    /// capped lexicon built without `with_reading`. The context recurrence gain (docs/spec/decoder.md §4.6) rides in the words'
    /// deltas, so it is in `total_score` and not an exception.
    /// Slice A2 (b'): a stretch that starts from an unknown u (after a fixed word, or from the left context) and holds exactly one word closes
    /// with the bigram: its `eos`, or the transition into the next fixed word, takes u as unknown (`close_u`), as `decode_segment_learned` does.
    pub fn total_score(&self) -> Option<f64> {
        let st = self.lm.as_ref()?;
        let cfg = st.lm.scoring(self.profile);
        let mut memo = TriMemo::default();
        // An optional-pack word is unknown to the model here as in the decoder (docs/spec/decoder.md §2.5); `history()` itself is unchanged.
        let id = |s: &str| st.capped.model_id(&st.lm, s);
        let cut = self.run_cut().filter(|_| st.lm.uses_tri(cfg));
        // docs/spec/decoder.md §4.3: the first sentence starts from the history word of the left context; `open` is whether
        // the current sentence has a word (the history word is not one, and its own eos is not scored).
        let mut prev = history(&self.left, &st.lm);
        // The word before `prev` for the trigram level (docs/spec/decoder.md §3.4 (a)-(c)), as the decoder starts a stretch:
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
        for PathWord { word: w, lp, sinit, is_punct, delta, syllables: n, mixed } in &self.path {
            if cut == Some(at) {
                if open {
                    total += st.lm.eos3_in(&mut memo, cfg, close_u(before, unknown, count).and_then(id), id(prev));
                }
                (before, prev, open, unknown, count) = (Some("<s>"), "<s>", false, false, 0);
            }
            if *is_punct {
                at += 1;
                if open {
                    total += st.lm.eos3_in(&mut memo, cfg, close_u(before, unknown, count).and_then(id), id(prev));
                }
                (before, prev, open, unknown, count) = (Some("<s>"), "<s>", false, false, 0);
                continue;
            }
            let n = *n;
            // docs/spec/decoder.md §3.3 (the learned-boost exception): a word the learner boosted (lp above its best capped lp under its reading, the test of
            // `boosted`) was scored by the decoder without the Kneser-Ney substitution; score it the same way here.
            // A word that matched a rule 10 record (`sinit`) is not one of those; its lp is its own entry's (docs/spec/learning.md §7.2, the pool is unlifted).
            let learned = !sinit && self.syls.get(at..at + n).and_then(|s| st.capped.best_lp_with(s, w, self.tone3)).is_some_and(|best| *lp > best);
            // A learned word is scored by P2 alone (no Kneser-Ney substitution, no table), as in the decoder.
            // A fixed word is the right neighbour that closes the stretch before it (`End::Next`), so (b') applies to its term.
            let fixed_here = self.fixed.iter().any(|f| f.start == at);
            let u = if fixed_here { close_u(before, unknown, count) } else { before };
            total += if *mixed {
                *lp + delta // docs/spec/decoder.md §4.7: the edge's whole score (its delta is 0)
            } else if learned {
                st.lm.word_learned_by_id(cfg.lam, id(prev), id(w), *lp)
            } else {
                st.lm.word3_in(&mut memo, cfg, u.and_then(id), id(prev), id(w), *lp)
            } - delta;
            // The stretch after a fixed word starts from (unknown, that word); inside a stretch the history is the last two words.
            before = if fixed_here { None } else { Some(prev) };
            (unknown, count) = if fixed_here { (true, 0) } else { (unknown, count + 1) };
            at += n;
            (prev, any, open) = (w, true, true);
        }
        any.then(|| if open { total + st.lm.eos3_in(&mut memo, cfg, close_u(before, unknown, count).and_then(id), id(prev)) } else { total })
    }

    /// docs/spec/c-abi.md §1, reset: Commit returns the composition as shown, unfinished symbols included, like Enter (
    /// docs/spec/keys-and-composition.md §7.1; no learning here); CommitFinished drops the unfinished syllable first; all clear everything.
    pub fn reset(&mut self, mode: ResetMode) -> Output {
        let mode = if mode == ResetMode::CommitFinished {
            self.clear_pend();
            ResetMode::Commit
        } else {
            mode
        };
        // docs/spec/english-mixed.md §7.4: the buffered digits are tokens before the composition is sent (Commit); Discard drops them with the rest.
        let digits = if mode == ResetMode::Commit { self.digit_flush().unwrap_or_default().0 } else { String::new() };
        if mode == ResetMode::Commit && !self.run.is_empty() && !self.run_ascii {
            // docs/spec/english-mixed.md §4.4: an open run goes out in its writing, the text Enter would send (Enter ends the run first).
            self.run = english_writing(&self.run);
        }
        let commit = if mode == ResetMode::Commit { digits + &self.shown().0 } else { String::new() };
        self.clear_all();
        self.view(true, commit)
    }

    pub fn key(&mut self, k: Key) -> Result<Output, EngineError> {
        // The shell reads the left text only before a key that may start a composition (a character key without modifiers or with Shift alone);
        // other keys start one too (Ctrl+\ on an empty composition), and Backspace to empty or Esc on unfinished symbols alone leaves the text of an
        // earlier composition, perhaps another app's. A key on an empty composition without a `set_left_context` since the last key forgets it.
        if !std::mem::take(&mut self.left_fresh) && self.composition_empty() {
            self.clear_left();
        }
        self.pred_dirty = false;
        self.ld_now = self.last_digits.take();
        self.late_in = self.late.take();
        self.d_emitted = 0;
        self.d_shown = self.drun.as_ref().map_or(0, |r| r.emitted.len() + r.m);
        self.reverted = None; // the one place the tool hooks are cleared; the key sets them again below
        self.split = false;
        self.noop = false;
        self.eaten = false;
        self.depth = 0;
        let short = self.last_short.take();
        self.ls_now = short.clone();
        let auto = self.last_auto.take();
        self.la_now = auto.clone();
        (self.auto_point, self.auto_event) = (None, None);
        // docs/spec/learning.md §8.1: `paused` marks a composition with a key handled while learning was off. A key on an empty
        // composition starts a new one (however the last one emptied: commit, Esc, Backspace), so the mark goes first.
        if self.syls.is_empty() && self.run.is_empty() && self.pending().is_empty() {
            self.paused = false;
        }
        if !self.learning {
            self.paused = true;
        }
        self.cap_snapshot();
        let mut r = self.dispatch(k).and_then(|mut o| self.cap_after(&mut o).map(|()| o));
        // docs/spec/english-mixed.md §7.4: a key the engine passes on untouched (key-and-composition rule 1, rule 5q: not handled, nothing committed) leaves the late
        // tokens as they were, and so does a key rule 1o ate. Rule 9 put them back itself (`late_keep`); every other key lets them go.
        if matches!(&r, Ok(o) if o.commit.is_empty() && (self.eaten || !o.handled)) {
            self.late = self.late.take().or(self.late_in.take());
        }
        // Rule 1o, eaten: the other reading of the syllable that just closed (docs/spec/english-mixed.md §7.7) stays for the next key too.
        if self.eaten && r.is_ok() {
            self.last_digits = self.last_digits.take().or(self.ld_now.take());
        }
        // docs/spec/english-mixed.md §6: a key that changes nothing (a key passed on, or an arm that calls `nothing`) leaves the short word's
        // mark where it was. The one place; a new arm that does nothing calls `nothing` and needs no other line.
        if matches!(&r, Ok(o) if o.commit.is_empty() && (self.noop || !o.handled)) {
            self.last_short = self.last_short.take().or(short);
            self.last_auto = self.last_auto.take().or(auto);
        }
        self.mixed_sweep();
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

    /// The core's rerank-job condition (docs/spec/semantic-rerank.md §6.1): the small model is loaded, the switch is on and the demotion table is on.
    fn jobs_on(&self) -> bool {
        self.tinylm.is_some() && self.rerank_on && self.demote
    }

    /// docs/spec/keys-and-composition.md §5.3: the window W of characters a free stretch may hold and the buffer under it: the small model's `ctx - 3` (or
    /// `CAP_WINDOW_NO_MODEL`), and `CAP_BUFFER`, except that a window not wider than the buffer (a model with ctx 19 or less; none is made
    /// and none is tested) freezes down to W itself, there being no room for a buffer. `None`: ctx 3 or less, nothing fits any window.
    fn cap_window(&self) -> Option<(usize, usize)> {
        let w = self.tinylm.as_ref().map_or(Some(CAP_WINDOW_NO_MODEL), |m| m.ctx().checked_sub(3)).filter(|&w| w > 0)?;
        Some((w, if w > CAP_BUFFER { CAP_BUFFER } else { 0 }))
    }

    /// docs/spec/keys-and-composition.md §5.3 step 1: the path and the syllables before a key or pick runs (kept in `self.cap`), only when the key could leave a
    /// free stretch longer than W: the characters shown plus the most one key adds (a prediction pick of the longest word, plus two) pass W.
    /// With the rerank on or off, with or without the small model; the n-gram model gives the path.
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

    /// docs/spec/keys-and-composition.md §5.3 steps 3 and 4 for the running key: when it decoded (the generation changed) a non-empty composition, freeze the
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

    /// docs/spec/keys-and-composition.md §5.3 step 3. For every free stretch of the latest decode (`self.free`) of more than W characters: from its start, word
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
                if !fits || st.capped.best_lp_delta_with(&self.syls[a..b], word, self.tone3).is_none() {
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

    /// docs/spec/macos-shell.md §8.6.4: the output value of a prediction row, 2 when the setting is vertical and
    /// there is a row, else 0. Read from the current setting, not fixed at any time: the row has no opening.
    fn pred_vertical(&self) -> u32 {
        if self.cand_vertical && !self.pred.is_empty() { 2 } else { 0 }
    }

    /// The row as output fields (candidates, selected, total); `columns` and `first` stay 0.
    fn pred_view(&self) -> (Vec<String>, Option<usize>, u32) {
        (self.pred.iter().map(|p| p.word.clone()).collect(), self.pred_sel, self.pred.len() as u32)
    }

    /// Test and tool hook (docs/spec/english-mixed.md §8): the table (shared across engines), or none.
    #[doc(hidden)]
    pub fn set_english_next(&mut self, table: Option<Arc<EnglishTable>>) {
        self.en_table = table;
        self.en_words = None; // the cache belongs to the table
        self.en_cache(); // built now, not at the first key (a table and a model)
        self.clear_pred();
        self.pred_dirty = true;
    }

    /// Test and tool hook: read `english-next.tsv` at `path`; an error message (nothing changes) when it is missing or does not parse.
    #[doc(hidden)]
    pub fn load_english_next(&mut self, path: &Path) -> Result<(), String> {
        let text = std::fs::read_to_string(path).map_err(|e| format!("english-next table: cannot read file ({:?})", e.kind()))?;
        self.set_english_next(Some(Arc::new(EnglishTable::parse(&text).map_err(|e| e.to_string())?)));
        Ok(())
    }

    /// docs/spec/english-mixed.md §8: the English token the start `s` follows (one space unit may sit between), lower-cased. The units decide, not the
    /// display: a lexicon word that ends in letters (卡拉OK) is no English token.
    fn english_before(&self, s: usize) -> Option<String> {
        // docs/spec/semantic-rerank.md §5.2: at the composition's start, the English word the text before it ends with (not one that may be
        // cut, starting at the first character of a cut `left_full`).
        if s == 0 {
            return english_tail(&self.left_full).filter(|&(at, _)| !(self.left_cut && at == 0)).map(|(_, kept)| kept.trim_end().to_ascii_lowercase());
        }
        let i = if s > 0 && is_space_unit(&self.syls[s - 1]) { s.checked_sub(2)? } else { s.checked_sub(1)? };
        // docs/spec/decoder.md §6.2: 2-32 letters (a Shift+letter, Space run makes a one-letter token; the table has none).
        english_of(&self.syls[i]).filter(|w| (2..=32).contains(&w.len())).map(str::to_ascii_lowercase)
    }

    /// The best entry of every word the table names, built once per (lexicon, model); `None` without a table or a model.
    fn en_cache(&mut self) -> Option<Arc<EnWords>> {
        let (st, table) = (self.lm.clone()?, self.en_table.clone()?);
        let key = (Arc::as_ptr(&st.capped) as usize, st.lm.identity());
        if let Some(c) = self.en_words.clone().filter(|c| c.key == key) {
            return Some(c);
        }
        let c = Arc::new(EnWords { key, words: st.capped.predict_index(&st.lm).digit_words(&table.all_words()) });
        self.en_words = Some(c.clone());
        Some(c)
    }

    /// docs/spec/prediction.md §3.1, §3.3: one `predict` per start (decoded word starts, positions inside words,
    /// the cursor), merged and reordered by what was learned.
    /// Empty while off, without a model, when the cursor is not at the end, the candidate window is open or an English run is
    /// open (docs/spec/english-mixed.md §4.1: Tab then ends the run first, there is no row to enter).
    fn recompute_pred(&mut self) {
        self.clear_pred();
        if !self.run.is_empty() {
            self.pred = self.run_row(); // docs/spec/prediction.md §3.5
            return;
        }
        let n = self.syls.len();
        let (Some(st), true) = (self.lm.clone(), self.predict_on && self.cursor == n && self.cands.is_none() && self.run.is_empty()) else { return };
        let idx = st.capped.predict_index(&st.lm);
        let cfg = st.lm.scoring(self.profile);
        // docs/spec/english-mixed.md §7.6: right behind digits (tokens, or buffered keys that show as digits) the cursor start is read in the digit
        // context (v, D); buffered digits are not syllables, so no start in front of them is queried (choosing one would splice over them).
        let (dv, dcache) = match self.digit_pred_v(&st) {
            Some(v) => (Some(v), self.digit_env().map(|e| e.cache)),
            None => (None, None),
        };
        let dtable = self.digit_table.clone();
        // English before Chinese (docs/spec/english-mixed.md §8): the table and the word cache, only with both, a model, and the prediction on.
        let (etable, ecache) = (self.en_table.clone(), self.en_cache());
        let isolated = self.drun.as_ref().is_some_and(|r| r.m >= 1);
        // The unfinished units in order (one without the abbreviation composer); two or more can only read as an abbreviation.
        let pending: Vec<Vec<char>> =
            self.pend_prev.iter().chain([&self.pend]).map(|u| u.iter().flatten().copied().collect::<Vec<_>>()).filter(|u| !u.is_empty()).collect();
        let multi = pending.len() > 1;
        // One unit reads the same in both modes; only two or more can be an abbreviation.
        let mode = if multi { Mode::PA } else { Mode::P };
        // docs/spec/prediction.md §3.1: the last two path tokens' starts, and the start of every earlier one within `PREDICT_BACK`, far to
        // near. Positions inside words within `PREDICT_BACK` come last, except inside a word the user fixed (`frozen == false`):
        // choosing such an item would take the user's choice (and its pending learn) apart. Inside a frozen word a start is
        // fine (docs/spec/prediction.md §3.1): the pick takes the frozen word apart, which is not the user's choice.
        // Two or more units match nothing that holds a complete syllable (docs/spec/prediction.md §6): only the cursor start is queried.
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
            // docs/spec/semantic-rerank.md §5.2: the English word before the composition gives no row of its own on an empty composition
            // (today the same start returns nothing below: no units and no English token).
            if s == 0 && units.is_empty() && !digits_here {
                return Vec::new();
            }
            // The digit context goes first (docs/spec/english-mixed.md §8).
            let english = etable.as_deref().zip(ecache.as_deref()).filter(|_| !digits_here).and_then(|(t, c)| Some((t.get(&self.english_before(s)?)?, &c.words)));
            if units.is_empty() && !digits_here && english.is_none() {
                return Vec::new();
            }
            let before: String = disp[..off(s)].iter().collect();
            let key = context_key(&format!("{}{before}", self.left));
            let v = history(if key == crate::learn::SENTINEL { "" } else { &key }, &st.lm);
            let shown: String = disp[off(s)..off(n)].iter().collect();
            // A start no record can match scans 9 and weighs nothing: its row is the slice-1 one, cheaper.
            // New-word records are no context record (docs/spec/learning.md §8.3): they never open the wider scan.
            let gate = learned
                && (!self.scan_gate
                    || self.learner.records().iter().any(|r| r.context != NEWWORD && !r.context.starts_with(EN_PREFIX) && reading_matches_in(&units, &r.reading, mode)));
            let limit = if gate { PREDICT_SCAN } else { PREDICT_MAX };
            let max_chars = self.cand_len_cap.then_some(units.len());
            let hits: Vec<(String, Vec<String>)> = match (&dv, &dcache, &dtable, english) {
                (Some(dv), Some(c), Some(t), _) if digits_here => {
                    predict_digits_capped(idx, &st.lm, t, &c.words, cfg, dv, &units, limit, max_chars)
                        .into_iter()
                        .map(|(w, _, r)| (w, r))
                        .collect()
                }
                (_, _, _, Some((f, w))) => {
                    predict_english_capped(idx, &st.lm, f, w, cfg, v, &units, mode, limit, max_chars)
                        .into_iter()
                        .map(|(w, _, r)| (w, r))
                        .collect()
                }
                _ => predict_capped(idx, &st.lm, cfg, v, &units, mode, limit, max_chars).into_iter().map(|(w, _, _, r)| (w, r)).collect(),
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
        let cursor_items = if pending.is_empty() && dv.is_none() && etable.is_none() {
            Vec::new()
        } else if self.abbr && !pending.is_empty() {
            let digits_here = dv.is_some() && dcache.is_some() && dtable.is_some();
            let english_here = !digits_here
                && etable.as_deref().zip(ecache.as_deref()).and_then(|(t, _)| self.english_before(n).and_then(|w| t.get(&w))).is_some();
            if digits_here || english_here {
                query(n)
            } else {
                let symbols: Vec<char> = pending.iter().flatten().copied().collect();
                let (cuts, _) = abbr_cuts(&symbols, n, ABBR_MAX_UNITS, MAX_SYLLABLES, |s| idx.has_toneless_syllable(s));
                if cuts.len() <= 1 {
                    query(n)
                } else {
                    let before: String = disp[..off(n)].iter().collect();
                    let key = context_key(&format!("{}{before}", self.left));
                    let v = history(if key == crate::learn::SENTINEL { "" } else { &key }, &st.lm);
                    let mut best: HashMap<String, (Pred, f64, bool)> = HashMap::new();
                    for cut in &cuts {
                        let units: Vec<Unit> = cut.iter().map(|c| Unit { chars: c.chars().collect(), done: false, tone: None }).collect();
                        let mode_c = if units.len() > 1 { Mode::PA } else { Mode::P };
                        let max_chars = self.cand_len_cap.then_some(units.len());
                        let gate = learned
                            && (!self.scan_gate
                                || self.learner.records().iter().any(|r| {
                                    r.context != NEWWORD && !r.context.starts_with(EN_PREFIX) && reading_matches_in(&units, &r.reading, mode_c)
                                }));
                        let limit = if gate { PREDICT_SCAN } else { PREDICT_MAX };
                        for (i, (word, score, is_succ, reading)) in
                            predict_capped(idx, &st.lm, cfg, v, &units, mode_c, limit, max_chars).into_iter().enumerate()
                        {
                            let weight = if gate { self.learned_weight(&key, None, &reading, &word, today) } else { 0.0 };
                            if (i < PREDICT_MAX || weight > 0.0) && !word.is_empty() {
                                let better = |o: &(Pred, f64, bool)| (is_succ && !o.2) || (is_succ == o.2 && score > o.1);
                                if best.get(&word).is_none_or(better) {
                                    best.insert(word.clone(), (Pred { word, reading, start: n, key: key.clone(), weight }, score, is_succ));
                                }
                            }
                        }
                    }
                    let mut mixed: Vec<(Pred, f64, bool)> = best.into_values().collect();
                    mixed.sort_by(|a, b| {
                        b.2.cmp(&a.2)
                            .then(b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal))
                            .then_with(|| a.0.word.chars().count().cmp(&b.0.word.chars().count()))
                            .then_with(|| a.0.reading.join("-").cmp(&b.0.reading.join("-")))
                            .then_with(|| a.0.word.cmp(&b.0.word))
                    });
                    mixed.into_iter().map(|(p, _, _)| p).collect()
                }
            }
        } else {
            query(n)
        };
        // docs/spec/prediction.md §3.1: the positions inside words come last, far to near.
        let mid_items = mid.into_iter().flat_map(&query);
        let mut all: Vec<Pred> = head.chain(cursor_items).chain(tail).chain(mid_items).collect();
        all.sort_by(|a, b| b.weight.total_cmp(&a.weight)); // step 6: stable
        let mut words = HashSet::new();
        all.retain(|p| words.insert(p.word.clone())); // step 7
        all.truncate(PREDICT_MAX);
        self.pred = all;
    }

    /// Weight of `word` for `reading` under context `key` (sentence-start key: `next` is the syllable after the word, `None` for a
    /// predicted word, which ends at the cursor) at the exact or last-character level; the global level
    /// counts 0 (docs/spec/learning.md §5.3: it never overrides what the model is sure of).
    fn learned_weight(&self, key: &str, next: Option<&str>, reading: &[String], word: &str, today: i64) -> f64 {
        let key = record_key(key.to_string(), next);
        match self.learner.lookup(&key, reading, today) {
            (Level::Global, _) => 0.0,
            (_, hits) => hits.iter().find(|h| h.0 == word).map_or(0.0, |h| h.1),
        }
    }

    /// docs/spec/prediction.md §3.4: replace `syls[start..cursor]` by the item's reading, fix the word over it, move the cursor behind
    /// it. With learning on `pre` is `Some("")` (docs/spec/learning.md §4.3): taught at commit like a re-pick, with nothing displaced.
    fn choose_pred(&mut self, i: usize) -> Result<Output, EngineError> {
        let p = self.pred.swap_remove(i);
        self.apply_pred(p)
    }

    /// `choose_pred` for an item already taken out of the row (or out of a window that carried the row, docs/spec/prediction.md §5).
    fn apply_pred(&mut self, mut p: Pred) -> Result<Output, EngineError> {
        if p.key == EN_WORD_KEY {
            return self.apply_word_pred(p);
        }
        if p.key.starts_with(EN_PREFIX) {
            return self.apply_mixed_pred(p);
        }
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
        // docs/spec/keys-and-composition.md §6.3: a row pick keeps the words outside `[start, end)` as shown, as a window re-pick does.
        // Rows exist only with the cursor at the end, so no index right of it moves.
        let frozen = self.freeze_outside(start, end);
        self.fixed.retain(|f| !(f.start < end && start < f.end));
        self.fixed.extend(frozen);
        self.syls.splice(start..end, reading);
        let key = pre.is_some().then_some(key);
        self.fixed.push(Fixed { start, end: start + m, word, pre, key, pick_keys: Vec::new(), zh: None, frozen: false, split_off: false, displaced_new: Vec::new(), mixed: MixedMark::default(), own: None, whole: None, from_half_punct: false });
        self.fixed.sort_by_key(|f| f.start);
        self.cursor = start + m;
        self.refresh()?;
        if self.syls.len() >= MAX_SYLLABLES {
            let commit = self.take_commit();
            return Ok(self.view(true, commit));
        }
        self.handled()
    }

    /// Also renews the generation (docs/spec/semantic-rerank.md §6.2): a commit or a reset inside one call (the `MAX_SYLLABLES` auto-commit, a commit from
    /// the prediction row) voids the job the refresh before it made.
    fn clear_all(&mut self) {
        self.invalidate();
        self.clear_left();
        self.syls.clear();
        self.cursor = 0;
        self.clear_pend();
        self.fixed.clear();
        self.display.clear();
        self.path.clear();
        self.cands = None;
        self.run.clear();
        self.run_ascii = false;
        self.run_rev = false;
        self.revert_point = None;
        self.last_short = None;
        self.last_auto = None;
        self.drun = None;
        self.last_digits = None;
        self.late = None;
        self.late_in = None;
        self.clear_pred();
        self.paused = false;
    }

    fn pending(&self) -> String {
        self.pend_prev.iter().chain([&self.pend]).flatten().flatten().collect()
    }

    /// Measurement hook (not in the C ABI): the unfinished units in typing order, each one's symbols in initial, medial, final order;
    /// empty with nothing unfinished. Reads only.
    #[doc(hidden)]
    pub fn pending_units(&self) -> Vec<String> {
        self.pend_prev.iter().chain([&self.pend]).map(|u| u.iter().flatten().collect::<String>()).filter(|u| !u.is_empty()).collect()
    }

    /// Measurement hook (not in the C ABI): hands `f` what `recompute_pred` queries with: the prediction index, the model and the
    /// profile's scoring. `None` without a model. Reads only; no second index is built.
    #[doc(hidden)]
    pub fn with_predict_parts<R>(&self, f: impl FnOnce(&crate::predict::Index, &Lm, crate::lm::Scoring) -> R) -> Option<R> {
        let st = self.lm.as_ref()?;
        Some(f(st.capped.predict_index(&st.lm), &st.lm, st.lm.scoring(self.profile)))
    }

    /// The composition as shown: the display text with the unfinished symbols at the cursor, the cursor after
    /// them in UTF-16 code units, and the open English run's (start, length) in UTF-16 code units (0, 0 without a run).
    fn shown(&self) -> (String, u32, (u32, u32)) {
        let chars: Vec<char> = self.display.chars().collect();
        // Every path that sends what is shown (Enter, `reset(Commit)`, the token that reaches `MAX_SYLLABLES`) comes through here.
        let unfinished: String = if !self.run.is_empty() {
            self.run.clone() // docs/spec/english-mixed.md §4.1: the English run sits at the cursor
        } else {
            self.pending()
        };
        // A syllable shows as one char (lexicon invariant); a punctuation token as its fixed word,
        // which can be longer (⋯⋯, docs/spec/keys-and-composition.md §8.3). Clamped in case the invariant ever breaks.
        let at = (0..self.cursor).map(|i| self.token_width(i)).sum::<usize>().min(chars.len());
        let mut text: String = chars[..at].iter().collect();
        // docs/spec/english-mixed.md §7.1: the buffered keys that show as digits sit before the unfinished syllable (the tail, mirrored in `pend`).
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

    /// docs/spec/english-mixed.md §4.7: where the open English run sits among the units (the cursor), if one is open. The decode treats
    /// it as it does an English token: stretches end and start there, and what follows is not after a Han character.
    fn run_cut(&self) -> Option<usize> {
        (!self.run.is_empty()).then_some(self.cursor)
    }

    /// Recompute the display string: free segments decoded top-1, fixed words in between.
    fn refresh(&mut self) -> Result<(), EngineError> {
        self.decodes += 1;
        // docs/spec/semantic-rerank.md §6.2: every decode is a new generation; its job (if any) is made at the end of `refresh_lm`.
        self.invalidate();
        if let Some(st) = self.lm.clone() {
            return self.refresh_lm(&st);
        }
        let mut out = String::new();
        let mut pos = 0;
        let cut = self.run_cut();
        let seg =|from: usize, to: usize, out: &mut String| -> Result<(), EngineError> {
            // docs/spec/english-mixed.md §4.7: an open run splits a stretch, as a token does.
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
        let fuzzy = self.effective_fuzzy();
        // The clock (a libc time conversion) is read only when a record could use it.
        let today = if self.learner.is_empty() { 0 } else { self.today() };
        // docs/spec/learning.md §8.4: a fixed word the lexicon lacks is a new word picked in the window; once its record is
        // gone (forgotten, cleared) the span is unfixed and decoded again, never an error.
        if self.fixed.iter().any(|f| f.end - f.start >= NEWWORD_MIN) {
            let (syls, learner, lex, colloq) = (&self.syls, &self.learner, &self.lex, self.tone3);
            self.fixed.retain(|f| {
                let r = &syls[f.start..f.end];
                syls[f.start].starts_with(PUNCT_PREFIX)
                    || learner.new_word_day(r, &f.word).is_some()
                    || lex.has_with(r, &f.word, colloq)
            });
        }
        let mut lp_fixed = Vec::with_capacity(self.fixed.len());
        let mut delta_fixed = Vec::with_capacity(self.fixed.len());
        for f in &self.fixed {
            // Punctuation has no reading in the lexicon; 0.0 only keeps `path` aligned (docs/spec/keys-and-composition.md §8.2).
            let r = &self.syls[f.start..f.end];
            let (lp, delta) = if self.is_punct(f.start) {
                (0.0, 0.0)
            } else if let Some(x) = st.capped.best_lp_delta_with(r, &f.word, self.tone3) {
                x
            } else {
                // A fixed new word scores S(w) of its record, below ACTIVE too (docs/spec/learning.md §8.4).
                (newword_score(self.learner.new_word_weight(r, &f.word, today).ok_or(EngineError::Internal)?), 0.0)
            };
            lp_fixed.push(lp);
            delta_fixed.push(if self.demote { delta } else { 0.0 });
        }
        let (mut out, mut path) = (String::new(), Vec::new());
        // docs/spec/semantic-rerank.md §6.1: alpha and tau hold only for the scores with the demotion table on.
        let jobs = self.jobs_on();
        // docs/spec/decoder.md §7: the manual mask needs the readings of the first path only when the lexicon has `manual` words.
        let manual = self.lex.manual().clone();
        // docs/spec/semantic-rerank.md §3.1.1: the pool weight needs the decoder's term too.
        let pool_recur = self.recur_on && self.pool_recur_on;
        let mut stretches = Vec::new();
        // docs/spec/decoder.md §6.6: the position table over the whole composition, sliced per stretch.
        let t3 = self.t3();
        let t3_on = self.tone3.then_some(());
        self.free.clear();
        // Byte offset in `out` after the last stretch put into the job: the text from there on is the next stretch's `gap`.
        let mut job_end = 0;
        // docs/spec/english-mixed.md §4.7: an open English run is a boundary like a token: `cut` is its place among the units, `cut_out` the byte
        // offset in `out` where it sits (the run's letters are not in `out`).
        let (cut, mut cut_out) = (self.run_cut(), None);
        let table = self.mixed.clone();
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
                // Punctuation is a sentence boundary, as in the counts the model was built from (docs/spec/keys-and-composition.md §8.2).
                // The first stretch (no fixed word on its left) starts from the left context (docs/spec/decoder.md §4.3).
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
                // docs/spec/learning.md §7.2 and §7.3: with a job the pool, its n-gram scores, the anchor and the display before the rerank are the decode that
                // ignores rule 10 records (`Sinit::Off`); the lift only finds the learned path below. Without a job a rule 10 record has no effect (docs/spec/learning.md §7.6):
                // the decode is `Sinit::Off` too.
                let learn = (!self.learner.is_empty()).then(|| Learn {
                    learner: &self.learner, before: &before, after, today, eps_global: self.eps_global, sinit: Sinit::Off,
                });
                let tone3 = t3_on.map(|_| (&t3[start..stop], crate::tone3::DELTA_TONE3));
                // docs/spec/decoder.md §4.7: the mixed words that may start this stretch (none behind an open run: it is no token yet).
                let edges = table.as_deref().filter(|_| !after_run && start == from).map_or_else(Vec::new, |t| self.mixed_edges(t, &st.capped, start, stop));
                let mixed = (!edges.is_empty()).then_some((edges.as_slice(), self.mixed_bonus));
                let mut best = decode_segment_fuzzy(
                    &st.capped, &self.syls[start..stop], &st.lm, cfg, prev, end, BEAM_S1, learn.as_ref(), self.demote, fuzzy,
                    crate::fuzzy::DELTA_SOUND, self.recur_on.then_some(&self.recur), tone3, mixed,
                )
                .map_err(|_| EngineError::Internal)?;
                let eligible = jobs && best.len() >= 2;
                // docs/spec/decoder.md §6.5: the pool is the first TOPK paths with no fuzzy word; none when the n-gram first has one.
                let pool = if eligible { rerank_pool(&best, TOPK) } else { None };
                self.fuzzy_skips += usize::from(eligible && pool.is_none());
                // A pool of fewer than 2 paths has nothing to choose among: no job, and not the guard's doing, so it is not counted.
                let pool = pool.filter(|p| p.len() >= 2);
                let guarded = pool.is_some() && self.learn_guard && self.boosted(st, start, &best[0].1, &t3);
                self.guard_skips += usize::from(guarded);
                let pool = pool.filter(|_| !guarded);
                // docs/spec/learning.md §7.3 and §7.4: the learned sentence-start characters of the stretch, and the pool with a learned path in it.
                let mut pool = pool;
                let (sinit_layer, sinit_chars) = if pool.is_some() { self.sinit_chars(start, stop, &before, after, today) } else { (SinitLayer::Coarse, Vec::new()) };
                if let (Some(pool), Some(ln), false) = (pool.as_mut(), learn.as_ref(), sinit_chars.is_empty()) {
                    self.sinit.stretches += 1;
                    let lift = Learn { sinit: Sinit::Lift, ..*ln };
                    let lifted = decode_segment_fuzzy(
                        &st.capped, &self.syls[start..stop], &st.lm, cfg, prev, end, BEAM_S1, Some(&lift), self.demote, fuzzy,
                        crate::fuzzy::DELTA_SOUND, self.recur_on.then_some(&self.recur), tone3, mixed,
                    )
                    .map_err(|_| EngineError::Internal)?;
                    match sinit_swap(&mut best, &lifted, pool, &sinit_chars) {
                        Swap::Missing => self.sinit.missing += 1,
                        Swap::In => self.sinit.swaps += 1,
                        Swap::Kept => {}
                    }
                }
                let in_job = pool.is_some();
                // docs/spec/english-mixed.md §4.4: the token before a path that takes the mixed word shows the table's writing of the English part (the same
                // bytes in another case), so the text the job and the next stretches see is the text shown; `mixed_sync` then sets the token itself.
                let taken = best[0].1.first().filter(|w| w.6).map(|w| (w.0, w.3));
                if let (Some((_, k)), Some(t), true) = (taken, &table, start > 0) {
                    if let Some(w) = english_of(&self.syls[start - 1]).and_then(|e| Some(&t.get(e, &self.syls[start..start + k])?[..e.len()])) {
                        if let Some(last) = path.last_mut().filter(|p: &&mut PathWord| p.is_punct && out.ends_with(p.word.as_str())) {
                            out.truncate(out.len() - last.word.len());
                            out.push_str(w);
                            last.word = w.to_string();
                        }
                    }
                }
                if let Some(pool) = pool {
                    let top: Vec<&Scored> = pool.iter().map(|&i| &best[i]).collect();
                    let mut gap_text = out[job_end..].to_string();
                    if let Some(o) = cut_out.filter(|&o| o >= job_end) {
                        gap_text.insert_str(o - job_end, &self.run); // the model sees the run between the Chinese on its two sides
                    }
                    // The decoder's order (the pool after `sinit_swap`): the mask with the decoder's first path as the base, each path's pool weight,
                    // and the order they give (docs/spec/decoder.md §7, docs/spec/semantic-rerank.md §3.1.1).
                    let texts: Vec<String> = top.iter().map(|(_, ws)| text_of(ws)).collect();
                    let cands: Vec<&str> = texts.iter().map(String::as_str).collect();
                    let ng: Vec<f64> = top.iter().map(|(score, _)| *score).collect();
                    let used = || self.lex.path_readings_with_fuzzy(&self.syls[start..stop], best[0].1.iter().map(|w| (w.0, w.3)), &t3[start..stop], fuzzy);
                    let (masked, bonus, order) = pool_lists(&cands, &ng, used, &manual, pool_recur.then_some(&self.recur), taken.map(|t| t.0));
                    stretches.push(Stretch {
                        at: path.len(),
                        paths: top.iter().map(|(_, ws)| ws.iter().map(|&(w, lp, delta, l, _, sinit, mixed)| (w.to_string(), lp, delta, l, sinit.is_some(), mixed)).collect()).collect(),
                        ng,
                        bonus,
                        order,
                        masked,
                        b: top.iter().map(|(_, ws)| sinit_b(ws, &sinit_chars)).collect(),
                        layer: sinit_layer,
                        gap: gap_text,
                    });
                }
                for (w, lp, delta, l, _, sinit, mixed) in &best[0].1 {
                    out.push_str(w);
                    path.push(PathWord { word: w.to_string(), lp: *lp, sinit: sinit.is_some(), is_punct: false, delta: *delta, syllables: *l, mixed: *mixed });
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
                // A token that shows the table's writing goes in as its own text; the stretch behind it puts the table's writing back when it takes the mixed word.
                let word = f.own.as_ref().unwrap_or(&f.word);
                out.push_str(word);
                path.push(PathWord { word: word.clone(), lp: lp_fixed[gap], sinit: false, is_punct: self.is_punct(f.start), delta: delta_fixed[gap], syllables: f.end - f.start, mixed: false });
            }
        }
        self.display = out;
        self.path = path;
        self.mixed_sync();
        if let (false, Some(model)) = (stretches.is_empty(), &self.tinylm) {
            // `left_full` holds at most this model's L characters: it is cleared whenever the model changes (`set_tinylm`).
            let left = if model.left_len().is_some() { self.left_full.clone() } else { self.left.clone() };
            let params = model.params(self.profile, self.rerank_level);
            // The conditions of docs/spec/semantic-rerank.md §4: a table is loaded, and `colloquial::applies` (chat, the
            // measured small model; any strength, docs/spec/semantic-rerank.md §4.4).
            let colloquial = self.colloquial.clone().filter(|_| colloquial::applies(self.profile, model));
            self.rerank_job = Some(RerankJob { generation: self.generation, model: model.clone(), params, colloquial, level: self.rerank_level, eps_sinit: self.eps_sinit, left, stretches });
        }
        Ok(())
    }

    /// SINIT (docs/spec/learning.md §7.3, §7.4): the sentence-start single characters the learner holds for the stretch
    /// `start..stop`, each with `b = w / (w + 1)` of its record, and the layer they are from. Only a stretch that starts a sentence (no Han
    /// character before it: the key is `^`) has any, looked up under the first syllable as typed, the keys the decoder reads (`record_key`, `mid_key`, `fine_key`):
    /// the fine key `^>` plus the next three syllables (two and `$` when the composition ends there) when the stretch has three or more syllables, and if it has
    /// any record that is the answer (the fine layer is used for every candidate); else the mid key, the next two syllables; else the coarse key `^>` plus the
    /// syllable after the first one (the next syllable of the stretch, or `after` for a one-syllable stretch). The longest layer that hits is the only one used.
    fn sinit_chars(&self, start: usize, stop: usize, before: &str, after: Option<&str>, today: i64) -> (SinitLayer, Vec<(char, f64)>) {
        if self.learner.is_empty() || context_key(before) != SENTINEL {
            return (SinitLayer::Coarse, Vec::new());
        }
        let first = std::slice::from_ref(&self.syls[start]);
        let chars_of = |key: &str| -> Vec<(char, f64)> {
            let (level, hits) = self.learner.lookup(key, first, today);
            if level != Level::Exact {
                return Vec::new();
            }
            hits.into_iter().filter(|(w, _)| is_single(w)).filter_map(|(w, wt)| Some((w.chars().next()?, wt / (wt + 1.0)))).collect()
        };
        if stop - start >= 3 {
            let syl = |i: usize| self.syls.get(start + i).map(String::as_str);
            // The third syllable is the stretch's, or `after` when the stretch ends there: the decoder reads the same one (`decode_segment_fuzzy`).
            let third = if start + 3 < stop { syl(3) } else { after };
            for (layer, key) in [(SinitLayer::Fine, fine_key(syl(1), syl(2), third)), (SinitLayer::Mid, mid_key(syl(1), syl(2)))] {
                let chars = key.map(|k| chars_of(&k)).unwrap_or_default();
                if !chars.is_empty() {
                    return (layer, chars);
                }
            }
        }
        let next = if stop - start >= 2 { Some(self.syls[start + 1].as_str()) } else { after };
        (SinitLayer::Coarse, chars_of(&record_key(SENTINEL.to_string(), next)))
    }

    /// docs/spec/semantic-rerank.md §6.1, the learning guard: whether a word of a stretch's first path (the stretch starts at syllable `from`)
    /// was scored with a learned boost, i.e. with an lp above the best capped lp of that word under its reading (the decoder
    /// raises lp only that way). Such a stretch is not reranked, so a choice the user taught is never undone by the model.
    /// A rule 10 record (SINIT, docs/spec/learning.md §7.2) is no boost here: with a job the stretch is decoded with `Sinit::Off`, so
    /// the first path is the n-gram's without it and its rule 10 character has the lp of its own entry; the learned candidate comes into the pool
    /// from the lifted decode and the learning term in the fused score. The records of the other rules boost as ever and keep the guard.
    /// A word read through the sandhi source (docs/spec/decoder.md §6.6; `t3` is the composition's position table) is compared under the lexicon reading it was
    /// found under, so it is boosted only when learning lifted it there.
    fn boosted(&self, st: &LmState, from: usize, words: &[DecodedWord], t3: &[crate::tone3::Pos]) -> bool {
        if self.learner.is_empty() {
            return false;
        }
        let mut at = from;
        words.iter().any(|&(w, lp, _, n, _, sinit, mixed)| {
            let span = self.syls.get(at..at + n);
            at += n;
            // The Han part of a mixed word (docs/spec/decoder.md §4.7) is the table's, not a taught boost: its lp is the edge's score.
            if mixed {
                return false;
            }
            // A word that matched a rule 10 record (the marker) is not a taught boost, whatever lp it carries: the model may choose against it.
            if sinit.is_some() {
                return false;
            }
            // A span that does not fit cannot happen (one character per syllable); counted as boosted, the safe side.
            // A word read through an adjacent-key syllable (fuzzy input) is not an entry of the typed reading either, but nothing taught
            // it: not boosted, so a stretch with one is reranked whether or not the learner holds a record (the evaluation CLI, which has
            // no learner, reranks it too). (A fuzzy-sound word never gets here: a first path with one is no rerank candidate at all,
            // `rerank_pool`.)
            // A word the capped lexicon lacks under its reading is a learned new word (docs/spec/learning.md §8.4): taught, so boosted.
            let sandhi = |s: &[String]| self.lex.tone3_reading(s, t3.get(at - n..at).unwrap_or(&[]), w);
            span.is_none_or(|s| {
                let s = sandhi(s).unwrap_or_else(|| s.to_vec());
                !s.iter().any(|x| !self.lex.has_syllable(x)) && st.capped.best_lp_with(&s, w, self.tone3).is_none_or(|best| lp > best)
            })
        })
    }

    fn handled(&self) -> Result<Output, EngineError> {
        Ok(self.view(true, String::new()))
    }
    fn passthrough(&self, commit: String) -> Result<Output, EngineError> {
        Ok(self.view(false, commit))
    }
    /// Commit the whole composition as shown, unfinished symbols included (docs/spec/keys-and-composition.md §4.1 rule 12a), and clear all state. Learning
    /// sees the decoded text only.
    fn take_commit(&mut self) -> String {
        let (shown, ..) = self.shown();
        let s = std::mem::take(&mut self.display);
        self.learn_commit(&s);
        self.clear_all();
        shown
    }

    /// Rules 12a, 12b, 19, 19a: Enter commits what is shown; Shift+Enter then passes the key on (Apple Zhuyin, measured 2026-10-09:
    /// it commits and the app gets the line break).
    fn commit_enter(&mut self, m: u32) -> Result<Output, EngineError> {
        let commit = self.take_commit();
        Ok(self.view(m & MOD_SHIFT == 0, commit))
    }

    /// The key changes nothing: the prediction row stays as it was (`old`, taken at the top of `dispatch`) and the short-word mark
    /// (docs/spec/english-mixed.md §6) is given back by `key()`.
    fn nothing(&mut self, old: Vec<Pred>) {
        self.pred = old;
        self.mark_noop();
    }

    /// The key as a whole changed nothing, if this is the outermost `dispatch` of the key. In an inner one (a run or a digit buffer was
    /// ended first, which is a change) the mark is not given back.
    fn mark_noop(&mut self) {
        if self.depth == 1 {
            self.noop = true;
        }
    }

    fn dispatch(&mut self, k: Key) -> Result<Output, EngineError> {
        self.depth += 1;
        let r = self.dispatch_key(k);
        self.depth -= 1;
        r
    }

    fn dispatch_key(&mut self, k: Key) -> Result<Output, EngineError> {
        let m = k.modifiers;
        let is_char = k.kind == KeyKind::Char;
        // The key scheme in force for this key (docs/spec/keys-and-composition.md §4.1.1): the scheme the open window opened with, else the current setting. Read once, here:
        // a window that closes under rule 8 and lets the key go on does not change it, and a setting changed with a window open does not reach the rows until it closes.
        let ms = self.cands.as_ref().map_or(self.key_scheme == 1, |c| c.ms);
        // docs/spec/macos-shell.md §8.6.2: Page Up / Down mean something only in an open vertical window, without
        // modifiers; anywhere else the key changes nothing (not even the prediction row) and is not handled.
        if matches!(k.kind, KeyKind::PageUp | KeyKind::PageDown) && !(m == 0 && self.cands.as_ref().is_some_and(|c| c.vertical)) {
            return self.passthrough(String::new());
        }
        // docs/spec/prediction.md §4.4: ⌘⌫ while the prediction row is entered forgets the selected item (before rule 1).
        if let (KeyKind::Backspace, true, Some(sel)) = (k.kind, m & MOD_COMMAND != 0, self.pred_sel) {
            if self.pred[sel].key == EN_WORD_KEY {
                return self.handled(); // an English completion is no record: nothing to forget
            }
            let Pred { word, reading, .. } = &self.pred[sel];
            let (word, reading) = (word.clone(), reading.clone());
            self.note_forgot(&reading);
            self.forget_entry(&reading, &word);
            // The free segments decode without the forgotten record; unlike the candidate window's ⌘⌫ no frozen word
            // is freed (docs/spec/keys-and-composition.md §6.6).
            self.refresh()?;
            self.cap_freeze()?; // before the row is built from the decode
            self.recompute_pred();
            if !self.pred.is_empty() {
                self.pred_sel = Some(self.pred.iter().position(|p| p.word == word).unwrap_or(0));
            }
            return self.handled();
        }
        // docs/spec/learning.md §9.1: ⌘⌫ with candidates open forgets the highlighted word (before rule 1 passes ⌘ keys on).
        if k.kind == KeyKind::Backspace && m & MOD_COMMAND != 0 && self.cands.is_some() {
            self.forget_highlighted()?;
            return self.handled();
        }
        let ctrl_bs = is_char && k.ch == '\\' && m == MOD_CONTROL;
        // Key scheme 1, K11 (docs/spec/keys-and-composition.md §4.1, step 3): Ctrl+, and Ctrl+. put a full-width mark into the composition, an exception to rule 1 like Ctrl+\
        // (any unfinished syllable goes, an open window closes). Not with an English run or digit buffer open, nor in the ASCII state: those pass on as rule 1 says.
        if ms && is_char && m == MOD_CONTROL && (k.ch == ',' || k.ch == '.') && self.run.is_empty() && self.drun.is_none() && !self.ascii {
            self.clear_pred();
            return self.insert_punct(if k.ch == ',' { '，' } else { '。' });
        }
        // 1o (docs/spec/keys-and-composition.md §4.1, step 3, before rule 1): Option, or Option+Shift, with a key of the main block, outside the ASCII state. Apple's
        // Zhuyin (measured 2026-11-20): the full-width character goes into the composition as rule 2 puts a mark in; behind an unfinished syllable the key does
        // nothing. Here it is also eaten with a window open, the prediction row entered, or an English run or digit run open (not measured). Eaten: handled, and
        // every later output is what it would be without the key (`eaten`, `mark_noop`; the row, the Up run and all else are left as rule 1 leaves them).
        if !self.ascii && is_char && m & !MOD_SHIFT == MOD_OPTION {
            if let Some(p) = option_char(k.ch, m & MOD_SHIFT != 0) {
                if self.pend.iter().any(Option::is_some) || !self.pend_prev.is_empty() || self.cands.is_some() || self.pred_sel.is_some() || !self.run.is_empty() || self.drun.is_some() {
                    self.eaten = true;
                    self.mark_noop();
                    return self.handled();
                }
                self.up_run = None;
                self.clear_pred(); // as rule 2: the row goes, `insert_token` says when it is computed again
                return self.insert_punct(p);
            }
        }
        // 1: pass through, no state change.
        if m & (MOD_OPTION | MOD_COMMAND | MOD_CAPSLOCK) != 0 || (m & MOD_CONTROL != 0 && !ctrl_bs) {
            return self.passthrough(String::new());
        }
        // Rule 16u: any other key that gets past rules 1 and 5q ends a run of Up presses (the ones above that change the composition, ⌘⌫ and Ctrl+, Ctrl+.,
        // fail `UpRun`'s check instead).
        if k.kind != KeyKind::Up {
            self.up_run = None;
        }
        // docs/spec/keys-and-composition.md §9.3: in the ASCII state, where it is available (`ascii_on`), a printable ASCII character
        // closes an open window or row without choosing (so `1` is a character, never a pick) and then either goes on the English run as
        // typed (the characters `english_of` accepts: no lower case, no tone key, no revert or split) or, being any other character,
        // settles the open run as a token and goes in as its own half-width punctuation unit, so every English token keeps `english_of`'s
        // invariants.
        // Space with no run open, right after a token unit (English token, space unit, digit token, or a half-width mark of this branch),
        // types a space unit as rule 16a does after an English token; with a run open it ends the run (the next Space types the space).
        if self.ascii && k.kind == KeyKind::Space && m == 0 && self.ascii_on() && self.run.is_empty() && self.cands.is_none()
            && self.pred_sel.is_none() && self.drun.is_none() && self.cursor > 0 && self.is_ascii_token(self.cursor - 1)
        {
            self.clear_pred();
            self.pred_dirty = true;
            return self.insert_token(format!("{PUNCT_PREFIX} "), Some(" ".to_string()), None);
        }
        if self.ascii && is_char && m == 0 && ('!'..='~').contains(&k.ch) && self.ascii_on() {
            self.cands = None;
            self.clear_pred();
            let opened = self.run.is_empty();
            if opened {
                self.run = std::mem::take(&mut self.typed);
                self.run_ascii = !self.run.is_empty();
                self.clear_pend();
            }
            if !(k.ch.is_ascii_alphanumeric() || ",.;/-".contains(k.ch)) {
                let sent = if self.run.is_empty() { String::new() } else { self.end_run()? };
                let mut o = self.insert_token(format!("{PUNCT_PREFIX}{}", k.ch), Some(k.ch.to_string()), None)?;
                o.commit.insert_str(0, &sent);
                return Ok(o);
            }
            let full = self.run.len() == ENGLISH_RUN_MAX;
            let commit = self.run_push(k.ch)?;
            if (opened || full) && self.cursor < self.syls.len() {
                self.refresh()?; // a run opened in front of units: they decode apart from it (docs/spec/english-mixed.md §4.7), as `start_run` does; so does the 33rd character's
            }
            return Ok(self.view(true, commit));
        }
        // docs/spec/prediction.md §4.2: the entered prediction row, between rules 1 and 2. Any other key leaves it and goes on.
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
                // docs/spec/macos-shell.md §8.6.4: in the vertical row Up / Down move like Left / Right, stopping at both ends.
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
        // Every other change clears the row; the rules below that recompute or keep it say so.
        let old = std::mem::take(&mut self.pred);
        if k.kind == KeyKind::Tab && m == 0 && !old.is_empty() && self.cands.is_none() {
            self.pred = old;
            self.pred_sel = Some(0);
            return self.handled();
        }
        // docs/spec/english-mixed.md §7.4: an open run of digit-alphabet keys sees the key before anything else (the entered row and the Tab
        // that enters it came first; a window cannot be open while a run is).
        if self.drun.is_some() {
            return self.digit_run_key(k);
        }
        // docs/spec/english-mixed.md §4.1: an open English run sees the key before anything else.
        if !self.run.is_empty() {
            return self.run_key(k);
        }
        // Partial half-width punctuation (`set_half_punct`): before key scheme 1 rule 2m and rule 2 (`punct_of`).
        if let Some(p) = self.half_punct_of(k.ch, m, is_char) {
            return self.insert_half_punct(p);
        }
        // Key scheme 1, K8-K10 and K24a (step 7, in rule 2's place for these two keys): Shift+, and Shift+. are the half-width `<` `>`. With a window open they
        // are eaten (K24a). Nothing composed: passed on (K8). Only an unfinished syllable: it goes and the character is committed, never passed on (K9; an IMM32
        // host drops a key that was claimed and then passed on, docs/spec/windows-tip.md §5.4). Otherwise the character becomes a half-width unit at the cursor
        // (K10, and K9a after the unfinished syllable went).
        if ms && is_char && m == MOD_SHIFT && (k.ch == ',' || k.ch == '.') {
            if self.cands.is_some() {
                self.mark_noop();
                return self.handled();
            }
            let c = if k.ch == ',' { '<' } else { '>' };
            if !self.syls.is_empty() {
                return self.insert_punct(c);
            }
            if self.pend.iter().any(Option::is_some) {
                self.clear_pend();
                self.clear_left(); // the app's text changes under the cursor, as at a rule 22 pass-through
                return Ok(self.view(true, c.to_string()));
            }
            self.clear_left();
            return self.passthrough(String::new());
        }
        // 2: punctuation.
        let punct = if ctrl_bs {
            Some('、')
        } else if is_char && m == MOD_SHIFT {
            self.punct_of(&SHIFT_PUNCT_1, &SHIFT_PUNCT, k.ch)
        } else if is_char && m == 0 && !self.is_zhuyin_or_tone(k.ch) {
            self.punct_of(&PLAIN_PUNCT_1, &PLAIN_PUNCT, k.ch)
        } else {
            None
        };
        if let Some(p) = punct {
            // docs/spec/keys-and-composition.md §8.2: into the composition at the cursor, not committed. Anything unfinished is dropped.
            return self.insert_punct(p);
        }
        let plain = m == 0;
        // Key scheme 1, K3, K3a and K7 (before step 8, with no window open; with one, K24 eats the key in `candidate_key_ms`): Shift+letter. Only an unfinished
        // syllable: it goes and the upper-case letter is committed (K3). Otherwise the letter becomes a half-width unit at the cursor (K3a after the unfinished
        // syllable went, K7). Nothing composed: the rules below (rule 22, or an English run).
        if ms && is_char && m == MOD_SHIFT && k.ch.is_ascii_alphabetic() && self.cands.is_none() {
            let upper = k.ch.to_ascii_uppercase();
            if !self.syls.is_empty() {
                return self.insert_punct(upper);
            }
            if self.pend.iter().any(Option::is_some) {
                self.clear_pend();
                self.clear_left();
                return Ok(self.view(true, upper.to_string()));
            }
        }
        // docs/spec/english-mixed.md §4.1: Shift+letter starts an English run (the letter in upper case, from the keycap and Shift).
        if is_char && m == MOD_SHIFT && k.ch.is_ascii_alphabetic() && self.shadow_on() {
            return self.start_run(k.ch.to_ascii_uppercase());
        }
        let zy = if is_char && plain { self.layout.symbol_of(k.ch) } else { None };
        // 3-8: candidates open.
        if self.cands.is_some() && self.candidate_key(k)? {
            return self.chosen();
        }
        // docs/spec/english-mixed.md §7.4: a digit-alphabet key with nothing unfinished opens a run.
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
                // docs/spec/english-mixed.md §3.2: a key over a symbol of its column (a), or in a column before one that is filled (b), is English.
                if self.shadow_on() {
                    let occupied = self.pend[col].is_some();
                    let later = self.pend[col + 1..].iter().any(Option::is_some);
                    if occupied && self.english_overwrite {
                        return self.revert_keys(Revert::Overwrite, k.ch);
                    }
                    if !occupied && later && self.english_key_order && !self.english_reversal_guard {
                        return self.revert_keys(Revert::Order, k.ch);
                    }
                }
                // docs/spec/prediction.md §6: a symbol whose column does not strictly advance opens a new unit; at the cap with
                // two or more units, such a key is eaten without changing the composition or row.
                if self.abbr_composer() && self.pend[col..].iter().any(Option::is_some) {
                    let units = self.pend_prev.len() + 1;
                    if units < ABBR_MAX_UNITS && self.syls.len() + units < MAX_SYLLABLES {
                        self.pend_prev.push(std::mem::take(&mut self.pend));
                    } else if !self.pend_prev.is_empty() {
                        self.nothing(old);
                        return self.handled();
                    }
                }
                self.late_keep(col);
                if self.shadow_on() {
                    // docs/spec/english-mixed.md §16.1: what the closing key's comparison counts.
                    let later = self.pend[col + 1..].iter().any(Option::is_some);
                    self.pend_dup += u32::from(self.shadow_key[col].is_some_and(|(key, _)| key == k.ch));
                    self.pend_rev |= self.pend[col].is_none() && later && self.english_key_order && self.english_reversal_guard;
                }
                self.shadow_push(k.ch, col);
                self.pend[col] = Some(sym);
                self.pred_dirty = true;
            } else if let Some(t) = tone {
                if self.pend_prev.is_empty() {
                    return self.finish_syllable(t, old, true);
                }
                self.nothing(old); // two or more units: tone and space do nothing (docs/spec/prediction.md §6)
            } else if k.kind == KeyKind::Backspace {
                // Row 11: the last symbol in display order (final, then medial, then initial), as Apple Zhuyin and
                // McBopomofo do (measured 2026-10-06: ㄉㄨㄟ, ㄅ replaces ㄉ, then Backspace gives ㄅㄨ, then ㄅ).
                if let Some(col) = (0..3).rev().find(|&c| self.pend[c].is_some()) {
                    self.pend[col] = None;
                    self.shadow_key[col] = None; // docs/spec/english-mixed.md §3.1: the column's key goes with its symbol
                    self.typed = self.shadow_text(); // and the record forgets the replaced keys: Backspace is mending Zhuyin
                    (self.pend_dup, self.pend_rev) = (0, false); // docs/spec/english-mixed.md §16.1: and the counts with them
                }
                if self.pend.iter().all(Option::is_none) {
                    self.pend_prev.pop().into_iter().for_each(|u| self.pend = u); // an emptied unit goes
                    if self.pend.iter().all(Option::is_none) {
                        self.clear_shadow(); // the syllable is gone
                    }
                }
                self.pred_dirty = true;
            } else if k.kind == KeyKind::Esc {
                self.clear_pend();
            } else if k.kind == KeyKind::Enter && ms {
                self.nothing(old); // K1, K2: Enter and Shift+Enter do nothing
            } else if k.kind == KeyKind::Enter {
                return self.commit_enter(m); // 12a, 12b
            } else if k.kind == KeyKind::Down && self.pend_prev.is_empty() {
                // docs/spec/prediction.md §5: one unfinished syllable has no lexicon candidates; the window lists the row
                // (an empty row opens nothing, so the key is ignored as before).
                self.open_with_rows(old);
            } else {
                self.nothing(old); // the key does nothing
            }
            return self.handled();
        }
        // 14
        if let Some((col, sym)) = zy {
            self.start_syllable(k.ch, col, sym);
            return self.handled();
        }
        if self.syls.is_empty() {
            // 22a: a tone key on an empty composition types its mark
            // (ˊ ˇ ˋ ˙) as Apple Zhuyin does, instead of passing the digit on. Unlike Apple, which
            // commits the mark at once, it goes into the composition like punctuation (s3d), so
            // Backspace can still take it back and Enter sends it with the sentence (user's choice).
            if let (KeyKind::Char, Some(t)) = (k.kind, tone) {
                let mark = TONE_MARKS[t];
                return self.insert_token(format!("{PUNCT_PREFIX}{mark}"), Some(mark.to_string()), None);
            }
            // docs/spec/semantic-rerank.md §5.2: the composition stays empty, and the app's text changes under the cursor.
            self.clear_left();
            return self.passthrough(String::new()); // 22
        }
        let n = self.syls.len();
        match k.kind {
            // docs/spec/english-mixed.md §5.4 (English mixing on): Space right after an English token or a space unit types a space.
            KeyKind::Space if self.cursor > 0 && self.breaks_with_space(self.cursor - 1) => {
                self.pred_dirty = true; // docs/spec/english-mixed.md §8: the row behind English reaches the space unit (empty without the table, as before)
                return self.insert_token(format!("{PUNCT_PREFIX} "), Some(" ".to_string()), None);
            }
            // Key scheme 1, K5: Space types a half-width space unit; only Down opens the window.
            KeyKind::Space if ms => return self.insert_punct(' '),
            // In front of one (cursor 0) Space opens nothing: the writings list opens with Down only.
            KeyKind::Space if self.cursor == 0 && self.breaks_with_space(0) => self.nothing(old),
            // Key scheme 1, rule 15d: Down opens the window with the item shown over its span first.
            KeyKind::Down if ms => {
                self.open_candidates(old);
                self.shown_first();
            }
            KeyKind::Space | KeyKind::Down => self.open_candidates(old),
            // Key scheme 1, rule 16u: Up swaps the character before the cursor.
            KeyKind::Up if ms => return self.up_swap(old),
            KeyKind::Up => self.nothing(old),
            KeyKind::Char if tone.is_some() => {
                // Key scheme 1, K25: a tone key types its mark as a unit, as rule 22a does on an empty composition.
                if let (1, Some(t)) = (u8::from(ms), tone) {
                    return self.insert_punct(TONE_MARKS[t].chars().next().unwrap_or(' '));
                }
                self.nothing(old)
            }
            // Key scheme 1, K6: Tab and Shift+Tab do nothing.
            KeyKind::Tab if ms => self.nothing(old),
            KeyKind::Right | KeyKind::End if self.cursor == n => self.nothing(old), // nothing moves
            KeyKind::Left => self.cursor = self.cursor.saturating_sub(1),
            KeyKind::Right => self.cursor = (self.cursor + 1).min(n),
            KeyKind::Home => self.cursor = 0,
            KeyKind::End => self.cursor = n,
            // docs/spec/english-mixed.md §5.3 (English mixing on): the unit before the cursor is an English token. It goes and its
            // text without the last letter becomes an English run there (a one-letter token just goes).
            KeyKind::Backspace if self.cursor > 0 && self.shadow_on() && english_of(&self.syls[self.cursor - 1]).is_some() => {
                self.reopen_token(self.cursor - 1)?;
            }
            KeyKind::Backspace if self.cursor > 0 => {
                self.cursor -= 1;
                self.remove_syllable(self.cursor)?;
            }
            KeyKind::Delete if self.cursor < n => self.remove_syllable(self.cursor)?,
            KeyKind::Backspace | KeyKind::Delete => self.nothing(old),
            // Key scheme 1, K19: Shift+Enter sends the composition and the key is handled, so the app gets no line break.
            KeyKind::Enter if ms => {
                let commit = self.take_commit();
                return Ok(self.view(true, commit));
            }
            KeyKind::Enter => return self.commit_enter(m), // 19, 19a
            KeyKind::Esc => {
                // docs/spec/english-mixed.md §16.6: right after the token the comparison made at Space, Esc is the syllable those keys are today.
                if let Some((i, e, syl)) = self.la_now.take().filter(|(i, e, _)| self.cursor == i + 1 && english_of(&self.syls[*i]) == Some(e.as_str())) {
                    return self.auto_undo(i, e, syl);
                }
                self.clear_all()
            }
            _ => {
                let commit = self.take_commit(); // 21
                return self.passthrough(commit);
            }
        }
        self.handled()
    }

    /// Rules 3-8 (docs/spec/keys-and-composition.md §4.1, §4.3). `Ok(true)` = consumed; `Ok(false)` = candidates closed, key
    /// continues at rule 9.
    fn candidate_key(&mut self, k: Key) -> Result<bool, EngineError> {
        if self.cands.as_ref().is_some_and(|c| c.ms && c.vertical) {
            return self.candidate_key_ms(k);
        }
        let Some(c) = &mut self.cands else { return Ok(false) };
        let (len, sel, cols) = (c.list.len(), c.sel, PAGE_SIZE);
        let digit = (k.kind == KeyKind::Char && k.modifiers == 0 && ('1'..='9').contains(&k.ch))
            .then(|| k.ch as usize - '1' as usize);
        if c.vertical {
            // docs/spec/macos-shell.md §8.6.2: `first` is the first visible row, nine rows (fewer at the end).
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
                (KeyKind::Esc | KeyKind::Backspace, _) => self.cands = None,
                _ => {
                    self.cands = None;
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
                // up when the selection leaves it (docs/spec/macos-shell.md §8.4).
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
            // Left/right move the selection; down
            // expands into the grid, as in the system Zhuyin (docs/spec/keys-and-composition.md §4.3 b-4).
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

    /// Key scheme 1's rules 3-8 (docs/spec/keys-and-composition.md §4.1, K12-K17 and K21-K24) for a vertical window that opened under it: the visible range is a whole
    /// page, so `first` stays a multiple of `PAGE_SIZE` and `sel` inside `first..first + PAGE_SIZE`. No key that reaches here closes the window and goes on (rule 8): each is
    /// consumed, and the ones without a row (Backspace, Tab, Right, Left, Home, Delete, a Zhuyin key, Shift+letter) change nothing. Always `Ok(true)`. The keys of rule 2 (punctuation, Ctrl+\)
    /// never get here: step 7 closes the window and inserts the mark first, in both schemes.
    fn candidate_key_ms(&mut self, k: Key) -> Result<bool, EngineError> {
        let Some(c) = &mut self.cands else { return Ok(false) };
        let (len, sel, first) = (c.list.len(), c.sel, c.first);
        let last_first = len.saturating_sub(1) / PAGE_SIZE * PAGE_SIZE;
        let digit = (k.kind == KeyKind::Char && k.modifiers == 0 && ('1'..='9').contains(&k.ch)).then(|| k.ch as usize - '1' as usize);
        match (k.kind, digit) {
            (KeyKind::Char, Some(d)) => {
                if first + d < len {
                    self.choose(first + d)?;
                }
            }
            // K16: the next one, across the page; after the last one the first.
            (KeyKind::Down, _) => {
                c.sel = if sel + 1 < len { sel + 1 } else { 0 };
                c.first = c.sel / PAGE_SIZE * PAGE_SIZE;
            }
            // K13: the previous one, across the page; before the first one the last.
            (KeyKind::Up, _) => {
                c.sel = if sel > 0 { sel - 1 } else { len.saturating_sub(1) };
                c.first = c.sel / PAGE_SIZE * PAGE_SIZE;
            }
            // K14: the next page, its first row selected; after the last page the first.
            (KeyKind::Space | KeyKind::PageDown, _) => {
                c.first = if first < last_first { first + PAGE_SIZE } else { 0 };
                c.sel = c.first;
            }
            // K15: the previous page, its first row selected; before the first page the last.
            (KeyKind::PageUp, _) => {
                c.first = if first > 0 { first - PAGE_SIZE } else { last_first };
                c.sel = c.first;
            }
            // K22: the last page, its last row selected.
            (KeyKind::End, _) => {
                c.sel = len.saturating_sub(1);
                c.first = last_first;
            }
            // K23: Shift+Enter closes the window and chooses nothing.
            (KeyKind::Enter, _) if k.modifiers & MOD_SHIFT != 0 => self.cands = None,
            (KeyKind::Enter, _) => self.choose(sel)?,
            (KeyKind::Esc, _) => self.cands = None,
            _ => {}
        }
        Ok(true)
    }

    fn is_zhuyin_or_tone(&self, ch: char) -> bool {
        self.layout.symbol_of(ch).is_some() || self.layout.tone_of(ch).is_some()
    }

    /// Partial half-width punctuation (`set_half_punct`): returns the half-width ASCII mark when the key's bit is set.
    /// Guarded against the ASCII state and against Zhuyin/tone keys (so ETen's `'` and `=` stay Zhuyin).
    fn half_punct_of(&self, ch: char, m: u32, is_char: bool) -> Option<char> {
        if self.half_punct_mask == 0 || self.ascii || !is_char {
            return None;
        }
        let table: &[HalfPunctEntry] = if m == MOD_SHIFT {
            &HALF_PUNCT_SHIFT
        } else if m == 0 && !self.is_zhuyin_or_tone(ch) {
            &HALF_PUNCT_PLAIN
        } else {
            return None;
        };
        let entry = table.iter().find(|e| e.keycap == ch)?;
        ((self.half_punct_mask & (1u32 << entry.bit)) != 0).then_some(entry.ascii)
    }

    /// Full-width base (and optional secondary mark) for an ASCII character inserted via `set_half_punct`.
    fn half_punct_full(&self, ch: char) -> Option<(char, Option<char>)> {
        HALF_PUNCT_SHIFT
            .iter()
            .chain(HALF_PUNCT_PLAIN.iter())
            .find(|e| e.ascii == ch)
            .map(|e| (if self.punct_layout == 1 { e.full1 } else { e.full0 }, e.sec))
    }

    /// Rule 2's table: the key's cell in punctuation layout 1 (`one`) when that layout is chosen and has the key, else layout 0's (`base`).
    fn punct_of(&self, one: &[(char, char)], base: &[(char, char)], ch: char) -> Option<char> {
        let find = |t: &[(char, char)]| t.iter().find(|(c, _)| *c == ch).map(|(_, p)| *p);
        (self.punct_layout == 1).then(|| find(one)).flatten().or_else(|| find(base))
    }

    /// Rule 2's insertion, also the key scheme 1 rows that put a character into the composition: anything unfinished goes, an open window closes, and `p` becomes a
    /// one-character fixed unit at the cursor (docs/spec/keys-and-composition.md §8.2; a half-width one is what `set_ascii` makes).
    fn insert_punct(&mut self, p: char) -> Result<Output, EngineError> {
        self.clear_pend();
        self.cands = None;
        self.insert_token(format!("{PUNCT_PREFIX}{p}"), Some(p.to_string()), None)
    }

    /// Rule 2 insertion for a mark from `set_half_punct`: marks the `Fixed` unit with `from_half_punct = true` so `Down` offers full-width alternatives.
    fn insert_half_punct(&mut self, p: char) -> Result<Output, EngineError> {
        self.clear_pend();
        self.cands = None;
        self.insert_token_inner(format!("{PUNCT_PREFIX}{p}"), Some(p.to_string()), None, true)
    }

    /// docs/spec/keys-and-composition.md §4.3 mouse pick: `index` is a position in the last output's `candidates`: the candidate window's, or, with
    /// the window closed, the V3 prediction row's (entered or not). `Ok(None)` when there is neither or `index` is
    /// outside that output (state unchanged).
    pub fn pick(&mut self, index: usize) -> Result<Option<Output>, EngineError> {
        self.cap_snapshot();
        let Some(c) = &self.cands else {
            // docs/spec/prediction.md §1.1: a click on the prediction row selects at once, entered or not.
            if index >= self.pred.len() {
                return Ok(None);
            }
            self.last_digits = None; // docs/spec/english-mixed.md §7.7: the next state-changing key clears the other reading of the syllable that closed
            self.last_short = None;
            self.last_auto = None;
            let r = self.choose_pred(index).and_then(|mut o| self.cap_after(&mut o).map(|()| o));
            if r.is_err() {
                self.clear_all();
            }
            self.mixed_sweep();
            return r.map(Some);
        };
        let (first, n) = c.window();
        if index >= n {
            return Ok(None);
        }
        self.pred_dirty = false;
        // The frozen head (composition-cap) is part of the pick, before the row is recomputed from the path.
        let mut r = self.choose(first + index).and_then(|()| self.chosen()).and_then(|mut o| self.cap_after(&mut o).map(|()| o));
        self.mixed_sweep();
        if r.is_err() {
            self.clear_all();
        } else if std::mem::take(&mut self.pred_dirty) {
            self.recompute_pred(); // an item that changed the composition without a key: a short word's English token, a row item, a digit alternative
            if let Ok(o) = &mut r {
                (o.candidates, o.selected, o.total) = self.pred_view();
                o.vertical = self.pred_vertical();
            }
        }
        r.map(Some)
    }

    /// docs/spec/keys-and-composition.md §3.1: complete the pending syllable with tone 0..=4 (0 = space = tone 1).
    /// `auto`: the key is rule 10's own closing key (not the digit layer's tail, not the tone moved up), so it may be a decision point of
    /// docs/spec/english-mixed.md §16.
    fn finish_syllable(&mut self, tone: usize, old: Vec<Pred>, mut auto: bool) -> Result<Output, EngineError> {
        if let Some(l) = self.late_in.take() {
            auto &= !self.late_merge(l, tone)?; // docs/spec/english-mixed.md §7.4; a syllable that took late keys back is the digit layer's
        }
        let mut syl = self.pending();
        // tone index 1..=4 -> marks[1..=4]; space (0) -> unmarked tone 1
        syl.push_str(TONE_MARKS[tone]);
        let missing = self.lex.entries(std::slice::from_ref(&syl)).is_empty();
        // docs/spec/keys-and-composition.md §3.2 (2): an unaccepted syllable (or an unaccepted neutral tail right after a second tone) is accepted as typed
        // and goes on as a syllable of the lexicon, scored as its third tone (`score`) wherever a score is read here.
        let sandhi = if missing { self.tone3_accepts(&syl) } else { None };
        let known = !missing || sandhi.is_some();
        if !known && !self.adjacent_accepts(&syl) && !self.one_sym_accepts(&syl, tone) {
            // docs/spec/english-mixed.md §3.4: the adjacent-key rule above had its turn; what it did not take reverts (c).
            if let Some(o) = self.revert_missing(tone)? {
                return Ok(o);
            }
            self.nothing(old); // A syllable the lexicon lacks changes nothing
            return self.handled();
        }
        // docs/spec/english-mixed.md §16: a syllable the lexicon has, typed with a replaced key or a key out of column order, is English when the comparison says so.
        let score = sandhi.unwrap_or_else(|| syl.clone());
        if auto && known {
            self.auto_point = self.auto_point_of(&score);
            if let Some(n) = self.auto_point {
                if let Some(o) = self.auto_english(tone, &syl, &score, n)? {
                    return Ok(o);
                }
            }
        }
        // docs/spec/english-mixed.md §6: the keys of a short word (in the lexicon, so no revert), or of a syllable some key of which
        // replaced another (the record is not the shadow), that the candidate list adds the English of.
        let short = (known && self.shadow_on()).then(|| {
            let mut keys = self.typed.clone();
            keys.extend((tone > 0).then(|| TONE_KEYS_STANDARD[tone - 1]));
            (self.typed != self.shadow_text(), keys)
        });
        self.clear_pend();
        self.pred_dirty = true;
        let o = self.insert_token(syl, None, None)?;
        if let Some((_, word)) = short.filter(|(over, w)| *over || ENGLISH_SHORT.contains(&w.as_str())).filter(|_| o.commit.is_empty()) {
            self.last_short = Some((self.cursor - 1, word));
        }
        Ok(o)
    }

    /// Sandhi (docs/spec/keys-and-composition.md §3.2 (2), docs/spec/decoder.md §6.6): with the switch on and a model loaded, an unaccepted syllable, or an
    /// unaccepted neutral tail right after a second-tone syllable, is accepted: `Some(Xˇ)`, the reading it is scored as. So is, after any syllable of the
    /// composition, a neutral syllable without a one-syllable entry one of whose four tones has one (K): `Some` of the best-scoring of them. `None`: not this rule's.
    fn tone3_accepts(&self, syl: &str) -> Option<String> {
        if !self.tone3 || self.lm.is_none() {
            return None;
        }
        let prev_rises = self.cursor.checked_sub(1).and_then(|i| self.syls.get(i)).is_some_and(|p| p.ends_with('ˊ') && !p.starts_with(PUNCT_PREFIX));
        let ok = crate::tone3::unaccepted(&self.lex, syl) || (prev_rises && crate::tone3::unaccepted_tail(&self.lex, syl));
        if ok {
            return crate::tone3::third(syl);
        }
        // K (docs/spec/keys-and-composition.md §3.2 (2)): a neutral syllable without a one-syllable entry, after some syllable of the composition (punctuation
        // and English tokens are none), when one of X, Xˊ, Xˇ, Xˋ has an entry: scored as the best of them.
        let after_syllable = self.syls[..self.cursor.min(self.syls.len())].iter().any(|p| !p.starts_with(PUNCT_PREFIX));
        after_syllable.then(|| self.neutral_best(syl)).flatten()
    }

    /// Fuzzy input, bit 4 (docs/spec/decoder.md §6.2): a syllable the lexicon lacks is accepted when it has a lexicon
    /// syllable it may have been mistyped from. Needs a language model (the bigram decoder reads the alternatives; `decode_beam`
    /// does not) and the standard layout (the neighbour table is that layout's keyboard).
    fn adjacent_accepts(&self, syl: &str) -> bool {
        // An accepted syllable always decodes: the adjacent-key table lists only syllables with a one-syllable entry (`Lexicon::tol`),
        // so a non-empty alternative list always has a reading the decoder can read the syllable alone through.
        self.fuzzy & FUZZY_ADJACENT != 0
            && self.lm.is_some()
            && self.layout == Layout::Standard
            && !self.lex.has_syllable(syl)
            && !self.lex.adjacent_alts(syl).is_empty()
    }

    /// Fuzzy input, bit 5 (docs/contracts/typo-one-sym.md §2.3 step 4): a syllable the lexicon lacks is accepted when one symbol of some lexicon syllable,
    /// replaced by another of its column, shows it. Needs a language model; both layouts (no keyboard row is read). With English mixing on, Space does not
    /// take a syllable at least one of whose columns was overwritten while typing (`line`, `steam`, `wifi`): that reverts to English (`revert_missing`).
    /// A tone key does, as a syllable the lexicon lacks always did.
    fn one_sym_accepts(&self, syl: &str, tone: usize) -> bool {
        self.fuzzy & FUZZY_ONE_SYM != 0
            && self.lm.is_some()
            && !self.lex.has_syllable(syl)
            && !self.lex.one_sym_alts(syl).is_empty()
            && !(self.shadow_on() && tone == 0 && self.typed != self.shadow_text())
    }

    /// Insert one token (a syllable, or punctuation with its fixed word) at the cursor, shift the
    /// fixed words on its right, recompute; at MAX_SYLLABLES tokens commit everything (docs/spec/keys-and-composition.md §8.2).
    fn insert_token(&mut self, reading: String, fixed_word: Option<String>, zh: Option<String>) -> Result<Output, EngineError> {
        self.insert_token_inner(reading, fixed_word, zh, false)
    }

    fn insert_token_inner(&mut self, reading: String, fixed_word: Option<String>, zh: Option<String>, from_half_punct: bool) -> Result<Output, EngineError> {
        // docs/spec/keys-and-composition.md §6.5: a newly typed syllable frees the pieces a deletion split off, before anything decodes.
        // Punctuation, English tokens and spaces are not syllables and leave them.
        if fixed_word.is_none() {
            self.fixed.retain(|f| !f.split_off);
        }
        let c = self.cursor;
        // docs/spec/keys-and-composition.md §6.4: a frozen word the insertion cuts is kept per syllable (shifted below like the rest).
        let pieces = self.split_frozen(|f| f.start < c && c < f.end);
        self.fixed.retain_mut(|f| {
            if f.end <= c {
                true
            } else if f.start >= c {
                f.start += 1;
                f.end += 1;
                f.key = None; // the user changed the text before it: record under the key at the commit (docs/spec/learning.md §4.3)
                true
            } else {
                false
            }
        });
        let excluded: Vec<usize> = pieces.iter().map(|&s| if s >= c { s + 1 } else { s }).collect();
        self.syls.insert(c, reading);
        if let Some(word) = fixed_word {
            self.fixed.push(Fixed { start: c, end: c + 1, word, pre: None, key: None, pick_keys: Vec::new(), zh, frozen: false, split_off: false, displaced_new: Vec::new(), mixed: MixedMark::default(), own: None, whole: None, from_half_punct });
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

    /// docs/spec/keys-and-composition.md §6.4: after a syllable was inserted at `c`, decode (and set `display`/`path`). A
    /// frozen neighbour (left one ending at `c`, right one starting at `c + 1`; not punctuation, not a piece split off
    /// by this insertion, whose post-shift start is in `excluded`) stays fixed unless a word of the decode without it
    /// covers all its syllables plus the new one. docs/spec/keys-and-composition.md §6.4: a released neighbour and the new syllable stay free (a later
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

    /// docs/spec/keys-and-composition.md §6.4: a word of the current path covers all of `f`'s syllables plus the new one at `c`.
    fn merged(&self, f: &Fixed, c: usize) -> bool {
        let (from, to) = if f.end == c { (f.start, c + 1) } else { (c, f.end) };
        let mut pos = 0;
        self.path.iter().any(|w| {
            let (a, b) = (pos, pos + w.syllables);
            pos = b;
            a <= from && to <= b
        })
    }

    /// docs/spec/keys-and-composition.md §6.4 and §6.5: replace each frozen word `cut` selects by its syllables as single-character frozen words, in
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
        // docs/spec/keys-and-composition.md §6.5: the displayed word that holds syllable `i` (decoded, frozen or user-chosen) keeps its
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
                f.key = None; // the user changed the text before it (docs/spec/learning.md §4.3)
                true
            } else {
                false
            }
        });
        self.refresh()
    }

    /// docs/spec/english-mixed.md §5.4: whether unit `i` is an English token or a space unit, the two a Space after it types a space for.
    /// docs/spec/english-mixed.md §7.4 adds the digit-string token (and a space unit), when the digit feature is on.
    fn breaks_with_space(&self, i: usize) -> bool {
        (self.shadow_on() && english_or_space(&self.syls[i])) || (self.digits_active() && (digit_of(&self.syls[i]).is_some() || is_space_unit(&self.syls[i])))
    }

    /// Shift-toggle: whether unit `i` is one a Space types a space after in the ASCII state: an English token, a space unit, a digit
    /// token, or a half-width ASCII mark that branch made (not the full-width marks of the Shift keys).
    fn is_ascii_token(&self, i: usize) -> bool {
        let r = &self.syls[i];
        english_or_space(r) || digit_of(r).is_some() || r.strip_prefix(PUNCT_PREFIX).is_some_and(|t| t.len() == 1 && t.as_bytes()[0].is_ascii_graphic())
    }

    fn is_punct(&self, i: usize) -> bool {
        self.syls[i].starts_with(PUNCT_PREFIX)
    }

    /// docs/spec/keys-and-composition.md §6.1 candidate range: readings ending at the cursor (or starting at 0 when the cursor is 0),
    /// within the run of syllables that touches it; punctuation bounds the run (docs/spec/keys-and-composition.md §6.1). An empty
    /// run lists nothing, so space and down are ignored.
    ///
    /// docs/spec/prediction.md §5: `rows` is the prediction row shown before this key; the lexicon window lists its items first.
    fn open_candidates(&mut self, rows: Vec<Pred>) {
        let a = self.cursor;
        let avail = self.avail();
        let mut list = Vec::new();
        // docs/spec/keys-and-composition.md §8.3: right after punctuation (or punctuation first at cursor 0), list the typed mark,
        // then its alternatives.
        let touching = if a == 0 { 0 } else { a - 1 };
        if avail == 0 && touching < self.syls.len() && self.is_punct(touching) {
            if is_space_unit(&self.syls[touching]) {
                return; // docs/spec/english-mixed.md §5.4: a space unit has no list
            }
            if let Some(word) = english_of(&self.syls[touching]) {
                // docs/spec/english-mixed.md §5.2: right after an English token, its writings starting from the text it has now;
                // the punctuation table is not asked. A token a revert made adds the Chinese item of the syllable it remembers.
                let f = self.fixed.iter().find(|f| f.start == touching);
                let mut list: Vec<(String, usize)> = Self::english_writings(f.map_or(word, |f| f.word.as_str())).into_iter().map(|w| (w, 1)).collect();
                let zh = f.and_then(|f| f.zh.clone());
                let zh = zh.and_then(|syl| self.lex.entries(std::slice::from_ref(&syl)).first().map(|(w, _)| (w.to_string(), syl)));
                let mut c = Cands::new(Vec::new(), self.cand_vertical, self.key_scheme == 1);
                if let Some((word, syl)) = zh {
                    list.push((word, 1));
                    c.zh = Some(syl);
                }
                c.list = list;
                self.cands = Some(c);
                return;
            }
            if let Some(d) = digit_of(&self.syls[touching]) {
                // docs/spec/english-mixed.md §7.4 (Down): the row of the digit context first, then the digit and its full-width form, then (docs/spec/english-mixed.md §7.7)
                // the syllable the last digit tokens could have been. The punctuation table is not asked.
                let mut list: Vec<(String, usize)> = rows.iter().map(|p| (p.word.clone(), p.reading.len())).collect();
                list.push((d.to_string(), 1));
                list.push((digit_layer::full_width(d).to_string(), 1));
                let mut c = Cands::new(Vec::new(), self.cand_vertical, self.key_scheme == 1);
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
            let from_half_punct = self.fixed.iter().any(|f| f.start == touching && f.end == touching + 1 && f.from_half_punct);
            let half_full = if from_half_punct {
                typed.chars().next().and_then(|c| self.half_punct_full(c))
            } else {
                None
            };
            let alts_key = half_full.map(|(f, _)| f).or_else(|| typed.chars().next());
            let alts = alts_key.and_then(|c| self.punct.get(&c)).cloned().unwrap_or_default();
            let full_items: Vec<String> = half_full
                .into_iter()
                .flat_map(|(f, sec)| std::iter::once(f.to_string()).chain(sec.map(|s| s.to_string())))
                .collect();
            let mut listed = HashSet::new();
            for w in std::iter::once(typed).chain(full_items).chain(alts) {
                if listed.insert(w.clone()) {
                    list.push((w, 1));
                }
            }
            self.cands = Some(Cands::new(list, self.cand_vertical, self.key_scheme == 1));
            return;
        }
        self.open_with_rows(rows);
    }

    /// The length of the run of syllables that touches the cursor and holds no punctuation (docs/spec/keys-and-composition.md §6.1).
    fn avail(&self) -> usize {
        if self.cursor == 0 {
            (0..self.syls.len()).take_while(|&i| !self.is_punct(i)).count()
        } else {
            (0..self.cursor).rev().take_while(|&i| !self.is_punct(i)).count()
        }
    }

    /// docs/spec/prediction.md §5: the window's list: the row's items in their order, then (without an unfinished
    /// syllable, which has no lexicon candidates) the lexicon's entries for the syllables before the cursor, longest
    /// first, minus the words already listed.
    fn window_list(&self, rows: Vec<Pred>) -> (Vec<(String, usize)>, HashMap<usize, Vec<String>>, Vec<Pred>, Option<(String, usize)>) {
        // docs/spec/english-mixed.md §5.7: the mixed word is the first item of the list; the window then carries no row items (they are
        // told apart by being the first `rows.len()` entries), and no lexicon word has its text.
        let mixed = self.mixed_item();
        // docs/spec/english-mixed.md §4.3: the English word a split took apart follows it (or is first), told apart like the mixed word.
        let whole = self.whole_item();
        let mut rows = if mixed.is_some() || whole.is_some() { Vec::new() } else { rows };
        let mut seen: HashSet<String> = rows.iter().map(|p| p.word.clone()).collect();
        let mut list: Vec<(String, usize)> = rows.iter().map(|p| (p.word.clone(), p.reading.len())).collect();
        list.extend(mixed.clone());
        list.extend(whole);
        if self.pend.iter().any(Option::is_some) {
            self.variant_move(&mut list, &mut rows);
            return (list, HashMap::new(), rows, None);
        }
        let a = self.cursor;
        let today = if self.learner.has_new_words() { self.today() } else { 0 };
        for l in (1..=self.lex.max_len.max(NEWWORD_MAX).min(self.avail())).rev() {
            let key = if a == 0 { &self.syls[..l] } else { &self.syls[a - l..a] };
            let lexicon = self.lex.entries_with(key, self.colloq_on());
            // docs/spec/learning.md §8.4: new words first among the same length, so ⌘⌫ reaches them (any weight: a record
            // under ACTIVE is still on disk); one the lexicon has is listed where the lexicon puts it.
            let new = if (NEWWORD_MIN..=NEWWORD_MAX).contains(&l) { self.learner.new_words_all(key, today) } else { Vec::new() };
            for w in new {
                if !lexicon.iter().any(|(x, _)| *x == w) && seen.insert(w.to_string()) {
                    list.push((w.to_string(), l));
                }
            }
            for (w, _) in lexicon {
                if seen.insert(w.to_string()) {
                    list.push((w.to_string(), l));
                }
            }
        }
        self.variant_move(&mut list, &mut rows);
        // Fuzzy input (docs/spec/decoder.md §6.2): after every exact word, the words found through one tolerant
        // syllable, longer readings first, each reading's by score. Nothing is added with the flags 0 and only known syllables.
        // Third-tone sandhi (docs/spec/decoder.md §6.6): the sandhi words of the same span after them, from the composition's position table.
        let mut alt = HashMap::new();
        let t3 = self.t3();
        for l in (1..=self.lex.max_len.min(self.avail())).rev() {
            let (from, to) = if a == 0 { (0, l) } else { (a - l, a) };
            let key = &self.syls[from..to];
            for (w, reading) in self.lex.tolerant_words(key, self.effective_fuzzy(), &t3[from..to]) {
                if seen.insert(w.to_string()) {
                    alt.insert(list.len(), reading);
                    list.push((w.to_string(), l));
                }
            }
        }
        (list, alt, rows, mixed)
    }

    /// docs/spec/english-mixed.md §5.7: with the cursor at the end and no unfinished syllable, an English token and then the syllables
    /// up to the cursor that the table has as one word: that word and its length (the token and the syllables). Only the readings as
    /// typed are looked up.
    fn mixed_item(&self) -> Option<(String, usize)> {
        let (a, k) = (self.cursor, self.avail());
        if a != self.syls.len() || k == 0 || a <= k || self.pend.iter().any(Option::is_some) {
            return None;
        }
        let english = english_of(&self.syls[a - k - 1])?;
        self.mixed.as_ref()?.get(english, &self.syls[a - k..a]).map(|w| (w.to_string(), k + 1))
    }

    /// docs/spec/english-mixed.md §4.3: with the cursor at the end and nothing unfinished, right behind the token and the one syllable a split at the
    /// closing key made of an English word: that word in its writing, and its length (the token and the syllable).
    fn whole_item(&self) -> Option<(String, usize)> {
        let a = self.cursor;
        if a != self.syls.len() || a < 2 || self.pend.iter().any(Option::is_some) {
            return None;
        }
        let (keys, syl) = self.fixed.iter().find(|f| f.start == a - 2 && f.end == a - 1)?.whole.as_ref()?;
        (self.syls[a - 1] == *syl).then(|| (english_writing(keys), 2))
    }

    /// Variant order (docs/spec/decoder.md §2.3): under one reading, a less-typed spelling that the window lists before its
    /// more-typed one moves to just behind it, so the prediction row's order cannot put it first. `list[..rows.len()]` stays the row: two row
    /// items reorder in `rows` the same way; a row item over exactly the typed syllables (`choose` takes it as the lexicon's candidate) leaves
    /// `rows` for the move; any other row item stays where it is. A less-typed spelling the user taught (a row item with a learned weight,
    /// the one place learning orders the window) stays where it is too: the table never overrides learning (docs/spec/decoder.md §2.3).
    /// Runs on the exact items only (the fuzzy ones are added after it).
    fn variant_move(&self, list: &mut Vec<(String, usize)>, rows: &mut Vec<Pred>) {
        let a = self.cursor;
        for v in self.lex.variant_rows() {
            let reading_at = |i: usize, list: &[(String, usize)], rows: &[Pred]| -> Option<Vec<String>> {
                match rows.get(i) {
                    Some(p) => Some(p.reading.clone()),
                    None => {
                        let l = list[i].1;
                        let k = if a == 0 { self.syls.get(..l) } else { self.syls.get(a.checked_sub(l)?..a) };
                        k.map(<[String]>::to_vec)
                    }
                }
            };
            let find = |w: &str, list: &[(String, usize)], rows: &[Pred]| (0..list.len()).find(|&i| list[i].0 == w && reading_at(i, list, rows).is_some_and(|r| r == v.reading));
            let (Some(il), Some(im)) = (find(&v.less, list, rows), find(&v.more, list, rows)) else { continue };
            if il < im && !rows.get(il).is_some_and(|p| p.weight > 0.0) {
                if il < rows.len() {
                    if im < rows.len() {
                        let p = rows.remove(il);
                        rows.insert(im, p);
                    } else if rows[il].reading[..] == self.syls[rows[il].start..a] {
                        rows.remove(il);
                    } else {
                        continue;
                    }
                }
                let item = list.remove(il);
                list.insert(im, item);
            }
        }
    }

    /// docs/spec/prediction.md §5: a window that carries row items closes (as Esc) when the row's inputs change under it
    /// (prediction off, another profile); its items' keys and filter were made for the old decoding.
    fn close_row_window(&mut self) {
        if self.cands.as_ref().is_some_and(|c| !c.rows.is_empty()) {
            self.cands = None;
        }
    }

    /// Opens the window on `window_list`; an empty list opens nothing (and the row is gone, as before the prediction row).
    fn open_with_rows(&mut self, rows: Vec<Pred>) {
        let (mut list, alt, rows, mixed) = self.window_list(rows);
        // docs/spec/english-mixed.md §7.7: right after a syllable the digit keys closed, its digits are the last item.
        let digits = self.ld_now.take().filter(|_| !list.is_empty());
        if let Some(keys) = &digits {
            list.push((keys.clone(), 1));
        }
        // docs/spec/english-mixed.md §6: right after the syllable made from a short word's keys, that word's lower and upper case close the
        // list. Only the one syllable at position `i`, whatever range the window lists.
        let short = self.ls_now.take().filter(|(i, _)| self.cursor == i + 1 && !list.is_empty() && digits.is_none());
        if let Some((_, word)) = &short {
            list.push((word.to_ascii_lowercase(), 1));
            list.push((word.to_ascii_uppercase(), 1));
        }
        if !list.is_empty() {
            let mut c = Cands::new(list, self.cand_vertical, self.key_scheme == 1);
            c.alt = alt;
            c.rows = rows;
            c.mixed = mixed;
            c.dalt = digits.map(DigitAlt::Digits);
            c.short = short;
            self.cands = Some(c);
        }
    }

    /// Key scheme 1, rule 15d (docs/spec/keys-and-composition.md §4.1.1): in the window just opened, the first lexicon item whose text is what the composition shows
    /// over its span moves to the front of the lexicon items, the others keep their order, and it is selected. The prediction row's items (none on Windows,
    /// which turns the row off) stay first, and the items tied to the end of the list (a short word's two cases, a digit alternative) and to their positions
    /// (fuzzy readings) keep working: `alt` is renumbered and the tail is never moved.
    fn shown_first(&mut self) {
        let a = self.cursor;
        let Some(c) = self.cands.as_ref() else { return };
        let tail = if c.short.is_some() { 2 } else { usize::from(c.dalt.is_some()) };
        // The mixed word (docs/spec/english-mixed.md §5.7) stays the first item: the shown word goes right after it.
        let lo = c.rows.len() + usize::from(c.mixed.is_some());
        let hi = c.list.len().saturating_sub(tail);
        let found = (lo..hi).find(|&i| {
            let (w, l) = &c.list[i];
            let (s, e) = if a == 0 { (0, *l) } else { (a.saturating_sub(*l), a) };
            e <= self.syls.len() && s < e && self.span_text(s, e) == *w
        });
        let Some(j) = found else { return };
        let Some(c) = self.cands.as_mut() else { return };
        let item = c.list.remove(j);
        c.list.insert(lo, item);
        c.alt = std::mem::take(&mut c.alt).into_iter().map(|(k, r)| (if k == j { lo } else if (lo..j).contains(&k) { k + 1 } else { k }, r)).collect();
        c.sel = lo;
        c.first = lo / PAGE_SIZE * PAGE_SIZE;
    }

    /// Key scheme 1, rule 16u (docs/spec/keys-and-composition.md §4.1.1): Up in a composition (no unfinished syllable, no window). The first press of a run takes
    /// the character shown before the cursor and the single characters of that syllable (§6.1, lexicon order) and puts the shown one first; the presses
    /// then choose the third item and the second (as choosing a one-syllable word, `choose`: the rest of the composition freezes, and a re-pick of the
    /// span keeps the first pick's `pre`, so the commit teaches the last character only), and the press after them opens the window with the shown item first
    /// (rule 15d). A list of two skips the third item, a list of one opens the window at once. Rule 16 (nothing changes) when the cursor is at 0, the
    /// syllable before it is punctuation, a space, an English or digit token, its list is empty or the shown character is not in it.
    fn up_swap(&mut self, old: Vec<Pred>) -> Result<Output, EngineError> {
        let a = self.cursor;
        let shown_now = if a > 0 { self.span_text(a - 1, a) } else { String::new() };
        let run = self.up_run.take().filter(|r| r.syls == self.syls && r.cursor == a && r.shown == shown_now);
        let mut run = match run {
            Some(r) => r,
            None => {
                if a == 0 || self.is_punct(a - 1) {
                    self.nothing(old);
                    return self.handled();
                }
                let mut singles: Vec<String> = Vec::new();
                for (w, _) in self.lex.entries(&self.syls[a - 1..a]) {
                    if !singles.iter().any(|x| x == w) {
                        singles.push(w.to_string());
                    }
                }
                if !singles.contains(&shown_now) {
                    self.nothing(old);
                    return self.handled();
                }
                let mut base = vec![shown_now.clone()];
                base.extend(singles.into_iter().filter(|w| *w != shown_now));
                let steps = [2, 1].into_iter().filter(|&i| i < base.len()).collect();
                UpRun { syls: self.syls.clone(), cursor: a, base, steps, shown: shown_now }
            }
        };
        if run.steps.is_empty() {
            self.open_candidates(old);
            self.shown_first();
            return self.handled();
        }
        let i = run.steps.remove(0);
        self.cands = Some(Cands::new(vec![(run.base[i].clone(), 1)], self.cand_vertical, true));
        self.choose(0)?;
        run.syls.clone_from(&self.syls);
        run.cursor = self.cursor;
        run.shown = self.span_text(a - 1, a);
        self.up_run = Some(run);
        self.chosen()
    }

    /// docs/spec/learning.md §4.2: what to remember as the text before this pick, `None` for punctuation. Re-picking the
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

    /// The sentence-start synthetic keys (`^>` + `syls[end]`, `^>$` at the end) the span [start, end) is shown under now, when
    /// the context key before it is `^`; empty for any other context. For a single character (`single`) also the mid and fine keys
    /// of docs/spec/learning.md §7.1, built from the syllables after it as the commit builds them (`mid_key`, `fine_key`: `$` when the composition ends there).
    fn pick_keys(&self, start: usize, end: usize, single: bool) -> Vec<String> {
        let off: usize = (0..start).map(|i| self.token_width(i)).sum();
        let before: String = self.display.chars().take(off).collect();
        let ctx = context_key(&format!("{}{before}", self.left));
        if ctx != crate::learn::SENTINEL {
            return Vec::new();
        }
        let syl = |i: usize| self.syls.get(i).map(String::as_str);
        let (a, b, c) = (syl(end), syl(end + 1), syl(end + 2));
        // docs/contracts/typo-one-sym.md §2.4 item 4: no key names a syllable only bit 5 accepted.
        let unk = self.bit5_unknown();
        let u = |i: usize| unk.get(i).copied().unwrap_or(false);
        if u(end) {
            return Vec::new();
        }
        let deeper = if single { vec![(!u(end + 1)).then(|| mid_key(a, b)).flatten(), (!u(end + 1) && !u(end + 2)).then(|| fine_key(a, b, c)).flatten()] } else { Vec::new() };
        std::iter::once(next_key(a)).chain(deeper.into_iter().flatten()).collect()
    }

    /// docs/spec/learning.md §9.1: drop the highlighted candidate's records for its reading (all keys), then re-decode.
    /// With a store the file is always fully rewritten (docs/spec/learning.md §10.3): a record pruned from memory on load can
    /// still be in the file, so "memory changed" says nothing about the file. `must_rewrite` is set
    /// first, so a panic or failure part way makes the next write a full rewrite too.
    fn forget_highlighted(&mut self) -> Result<(), EngineError> {
        let Some(c) = self.cands.as_ref() else { return Ok(()) };
        let Some((word, l)) = c.list.get(c.sel).cloned() else { return Ok(()) };
        if c.short.is_some() && c.sel + 2 >= c.list.len() {
            return Ok(()); // a short word's English item (docs/spec/english-mixed.md §6) has nothing to forget
        }
        if c.sel < c.rows.len() {
            // docs/spec/prediction.md §5: a row item forgets as in the entered row (docs/spec/prediction.md §4.4): re-decode, recompute the row
            // (its keys and its "not the shown word" filter follow the new decoding), then the window stays open on the
            // rebuilt list, selected on the same word if it is still there.
            let reading = c.rows[c.sel].reading.clone();
            self.note_forgot(&reading);
            self.forget_entry(&reading, &word);
            self.refresh()?;
            self.cap_freeze()?; // before the rows are built from the decode
            let Some(mut c) = self.cands.take() else { return Ok(()) };
            // docs/spec/english-mixed.md §7.7: the items the window had behind the row that are not the lexicon's (the other reading of the digit keys,
            // and after digit tokens the digit and its full-width form) stay its last items; `dalt` still says what the last one is.
            let after_digit_token = self.avail() == 0 && self.cursor > 0 && digit_of(&self.syls[self.cursor - 1]).is_some();
            let kept: Vec<(String, usize)> = match &c.dalt {
                _ if after_digit_token => c.list[c.rows.len()..].to_vec(),
                _ if c.short.is_some() => c.list[c.list.len() - 2..].to_vec(),
                Some(_) => c.list[c.list.len() - 1..].to_vec(),
                None => Vec::new(),
            };
            self.recompute_pred();
            c.rows = std::mem::take(&mut self.pred);
            c.rows.retain(|p| p.word != word);
            (c.list, c.alt, c.rows, c.mixed) = self.window_list(std::mem::take(&mut c.rows));
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
        // A word found through a tolerant syllable is recorded under its lexicon reading (fuzzy input, docs/spec/decoder.md §6.2).
        let reading = c.alt.get(&c.sel).cloned().unwrap_or_else(|| self.syls[start..end].to_vec());
        // docs/spec/keys-and-composition.md §6.5: the frozen words over this span decode again only when the forgotten entry is in
        // effect here (exact or last-character level under this span's context; the global level adds nothing).
        let off: usize = (0..start).map(|i| self.token_width(i)).sum();
        let shown_before: String = self.display.chars().take(off).collect();
        let key = context_key(&format!("{}{shown_before}", self.left));
        // Under the sentence-start key the record also holds the syllable after the word, as in the decoder.
        let in_effect = self.learned_weight(&key, self.syls.get(end).map(String::as_str), &reading, &word, self.today()) > 0.0;
        self.forgot.push((start, end));
        self.forget_entry(&reading, &word);
        if in_effect {
            self.fixed.retain(|f| !(f.frozen && f.start < end && start < f.end));
        }
        self.refresh()?;
        // docs/spec/learning.md §8.6: a forgotten new word is no longer a candidate, so it leaves the open window (a lexicon
        // word stays: only its learned weight went). The window stays on the item that took its place.
        if self.in_lexicon(&reading, &word) {
            return Ok(());
        }
        let Some(mut c) = self.cands.take() else { return Ok(()) };
        // `alt` is keyed by list position: the items behind a removed one move up, and so do their readings.
        let mut alt = std::mem::take(&mut c.alt);
        let mut shifted = HashMap::new();
        let mut kept = 0;
        for (i, (w, n)) in std::mem::take(&mut c.list).into_iter().enumerate() {
            let reading = alt.remove(&i);
            if !(w == word && n == l) {
                shifted.extend(reading.map(|r| (kept, r)));
                c.list.push((w, n));
                kept += 1;
            }
        }
        c.alt = shifted;
        if c.list.is_empty() {
            return Ok(()); // closed, as by Esc
        }
        c.sel = c.sel.min(c.list.len() - 1);
        self.cands = Some(c.reclamped());
        Ok(())
    }

    /// docs/spec/keys-and-composition.md §5.3 step 3: every place `reading` stands in the composition, for the head freeze to leave alone this key.
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
        // back (docs/spec/learning.md §9.1), as learning_clear's pending-learn drop prevents for clear.
        // docs/spec/learning.md §8.6: forgetting a new word also drops the pending re-pick of every fixed span overlapping any
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

    /// docs/spec/keys-and-composition.md §6.3: from the path displayed before a pick over `[start, end)`, the words outside it (whole ones that the typed
    /// reading has, and the outside syllables of a word the range cuts, when that syllable has the shown character as a single-character
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
                // A word the typed reading does not have (one read through a tolerant syllable: fuzzy input, docs/spec/decoder.md §6.2) cannot be
                // fixed: `refresh_lm` scores a fixed word under the reading it stands over. As `pieces` and `cap_refreeze` do, it is
                // left to the decoder. (A punctuation token, a learned new word and every lexicon word pass, as ever.)
                let held = || self.is_punct(p) || st.capped.best_lp_delta_with(&self.syls[p..q], word, self.tone3).is_some() || self.learner.new_word_day(&self.syls[p..q], word).is_some();
                if !self.fixed.iter().any(|f| f.start == p && f.end == q) && held() {
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
        let Some(mut c) = self.cands.take() else { return Ok(()) };
        // docs/spec/english-mixed.md §6: the last two items swap the one syllable they are for for an English token, nothing else.
        if let Some((i, _)) = c.short.take().filter(|_| idx + 2 >= c.list.len()) {
            let word = c.list[idx].0.clone();
            self.cursor = i;
            self.remove_syllable(i)?;
            let commit = self.put_english(format!("{PUNCT_PREFIX}{word}"), word, None)?;
            if !commit.is_empty() {
                self.commit_out = Some(commit);
            }
            return Ok(());
        }
        if let Some(alt) = c.dalt.take().filter(|_| idx + 1 == c.list.len() && idx >= c.rows.len()) {
            let word = c.list[idx].0.clone();
            let commit = self.choose_digit_alt(alt, word)?;
            if !commit.is_empty() {
                self.commit_out = Some(commit);
            }
            return Ok(());
        }
        // docs/spec/prediction.md §5: a row item over exactly the typed syllables before the cursor is
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
        // docs/spec/english-mixed.md §5.7: the mixed word takes the place of the English token and its syllables as one token of the
        // punctuation kind (reading `PUNCT_PREFIX` + the writing, fixed to the writing): not a re-pick, nothing is learned.
        // docs/spec/english-mixed.md §4.3: the English word the split took apart goes back in as one English token; the table leaves it alone.
        if idx >= c.rows.len() && l == 2 && c.mixed.as_ref() != Some(&(word.clone(), l)) && self.whole_item() == Some((word.clone(), l)) {
            let start = self.cursor - l;
            let keys = self.fixed.iter().find(|f| f.start == start).and_then(|f| f.whole.clone()).map_or(word.clone(), |w| w.0);
            for i in (start..self.cursor).rev() {
                self.remove_syllable(i)?;
            }
            self.cursor = start;
            let commit = self.put_english(format!("{PUNCT_PREFIX}{keys}"), word, None)?;
            if let Some(f) = self.fixed.iter_mut().find(|f| f.start == start && commit.is_empty()) {
                f.mixed.dead = true;
            }
            if !commit.is_empty() {
                self.commit_out = Some(commit);
            }
            return Ok(());
        }
        if idx >= c.rows.len() && c.mixed.as_ref() == Some(&(word.clone(), l)) && l <= self.cursor {
            let start = self.cursor - l;
            for i in (start..self.cursor).rev() {
                self.remove_syllable(i)?;
            }
            self.cursor = start;
            self.pred_dirty = true;
            let commit = self.insert_token(format!("{PUNCT_PREFIX}{word}"), Some(word), None)?.commit;
            if !commit.is_empty() {
                self.commit_out = Some(commit);
            }
            return Ok(());
        }
        let (start, end) = if self.cursor == 0 { (0, l) } else { (self.cursor - l, self.cursor) };
        // New words of the path as shown, under the readings of their records: read before the span is rewritten below (docs/spec/learning.md §8.5, bookkeeping).
        let path_new = if self.learning { self.path_new_words() } else { Vec::new() };
        // A word found through a tolerant syllable: the span becomes its lexicon reading, as `choose_pred` replaces the span by the
        // item's reading, so refresh, `best_lp_delta` and learning read the lexicon reading (fuzzy input, docs/spec/decoder.md §6.2).
        if let Some(reading) = c.alt.get(&idx) {
            self.syls.splice(start..end, reading.iter().cloned());
        }
        // Choosing again the word a prediction pick fixed over this exact span keeps that pending learn (docs/spec/learning.md §4.3).
        let again = self.fixed.iter().find(|f| f.start == start && f.end == end && f.word == word && f.pre.as_deref() == Some(""));
        let (mut pre, key) = match again {
            Some(f) if self.learning => (f.pre.clone(), f.key.clone()),
            _ => (self.learning.then(|| self.pre_pick(start, end)).flatten(), None),
        };
        let frozen = self.freeze_outside(start, end);
        // docs/spec/learning.md §8.4: picking a new word is no S4 teach (it is counted as used at the commit, docs/spec/learning.md §8.5).
        if !self.is_punct(start) && !self.in_lexicon(&self.syls[start..end], &word) {
            pre = None;
        }
        // docs/spec/learning.md §8.5: new words of the path this pick overlaps are halved at the commit. The earlier picks this one replaces
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
                for (s, e, r, w) in path_new {
                    if s < end && start < e && !displaced_new.contains(&(r.clone(), w.clone())) {
                        displaced_new.push((r, w));
                    }
                }
            }
        }
        // Re-picking the span of an earlier pick keeps that pick's keys along with its displaced text.
        let pick_keys = match self.fixed.iter().find(|f| f.start == start && f.end == end && f.pre.as_ref().is_some_and(|p| !p.is_empty())) {
            Some(f) if self.learning => f.pick_keys.clone(),
            _ if self.learning && pre.is_some() => self.pick_keys(start, end, is_single(&word)),
            _ => Vec::new(),
        };
        // docs/spec/english-mixed.md §3.3, §5.2: another writing of an English token keeps the syllable it remembers; the Chinese item (the
        // last one of a window that has `zh`) turns the token back into that syllable, fixed to the word.
        let (mut zh, mut mixed) = (None, MixedMark::default());
        if end == start + 1 && english_of(&self.syls[start]).is_some() {
            zh = self.fixed.iter().find(|f| f.start == start).and_then(|f| f.zh.clone());
            // docs/spec/english-mixed.md §4.4, §5.7: a writing the user chose for the token stays, whichever it is (the one it had too) and whether or not
            // the table had written the token: no mixed word is a candidate behind this token again.
            mixed = MixedMark { dead: true, picked: 0 };
            if let Some(syl) = c.zh.filter(|_| idx + 1 == c.list.len()) {
                self.syls[start] = syl;
                zh = None;
            }
        }
        let from_half_punct = end == start + 1
            && self.is_punct(start)
            && self.fixed.iter().any(|f| f.start == start && f.end == end && f.from_half_punct);
        self.fixed.retain(|f| !(f.start < end && start < f.end));
        self.fixed.extend(frozen);
        // A candidate pick changes the text before every fixed word to its right: their prediction keys no longer
        // describe what the user sees, so those records fall back to the key at the commit (docs/spec/learning.md §4.3).
        for f in self.fixed.iter_mut().filter(|f| f.start >= end) {
            f.key = None;
        }
        self.fixed.push(Fixed { start, end, word, pre, key, pick_keys, zh, frozen: false, split_off: false, displaced_new, mixed, own: None, whole: None, from_half_punct });
        self.fixed.sort_by_key(|f| f.start);
        self.refresh()
    }
}

#[cfg(test)]
mod up_run_tests {
    use super::*;

    /// docs/spec/keys-and-composition.md §4.1.1 rule 16u: after a call that is not a key, the next Up goes on with the run only if the syllables, the cursor
    /// and the character before the cursor are what the last press left. Each case changes one of the three in the stored run (as a call that rewrote the
    /// composition would, with no key in between to clear it) and presses Up twice: a new run swaps on both presses, a run that went on would open the window
    /// on the second.
    #[test]
    fn a_changed_composition_starts_a_new_run() {
        let text: String = (0..30).map(|i| format!("ㄋㄧˇ {} -1.0\n", char::from_u32(0x4E00 + i).unwrap())).collect();
        let lex = Arc::new(Lexicon::parse(&text).unwrap());
        let l: Vec<String> = lex.entries(&["ㄋㄧˇ".to_string()]).into_iter().map(|(w, _)| w.to_string()).collect();
        let up = |e: &mut Engine| e.key(Key::new(KeyKind::Up)).unwrap();
        type Change = fn(&mut UpRun);
        let changes: [(&str, Change); 4] = [
            ("nothing", |_| {}),
            ("syllables", |r| r.syls.push("ㄋㄧˇ".to_string())),
            ("cursor", |r| r.cursor += 1),
            ("shown", |r| r.shown = "x".to_string()),
        ];
        for (name, change) in changes {
            let mut e = Engine::with_lexicon(lex.clone(), Layout::Standard);
            e.set_candidate_vertical(true).unwrap();
            e.set_key_scheme(1).unwrap();
            for c in ['s', 'u', '3'] {
                e.key(Key::ch(c, 0)).unwrap();
            }
            assert_eq!(up(&mut e).preedit, l[2], "{name}: the 1st press");
            change(e.up_run.as_mut().expect("a run after the 1st press"));
            assert_eq!(up(&mut e).preedit, l[1], "{name}: [l0, l1, l2][1] or [l2, l0, l1][2]");
            let o = up(&mut e);
            if name == "nothing" {
                assert!(o.selected == Some(0) && o.candidates.first() == Some(&l[1]), "nothing changed: the run goes on, the 3rd press opens");
            } else {
                assert!(o.selected.is_none() && o.preedit == l[0], "{name}: a new run, its 2nd press swaps to [l2, l0, l1][1]");
            }
        }
    }
}

#[cfg(test)]
mod variant_move_tests {
    use super::*;

    /// docs/spec/decoder.md §2.3: two row items under one reading, the less-typed one first. Untaught, the window step moves
    /// it behind the common one (rows follow); taught (a learned weight), it stays where learning put it.
    #[test]
    fn a_taught_less_typed_row_item_keeps_its_place() {
        let v = crate::VariantOrder::parse("ㄕ\t溼\t濕\tx\n").unwrap();
        let lex = Lexicon::parse_with_variants("ㄕ 濕 -1.0\nㄕ 溼 -2.0\n", None, Some(&v)).unwrap();
        let e = Engine::with_lexicon(Arc::new(lex), Layout::Standard);
        let pred = |w: &str, weight: f64| Pred { word: w.to_string(), reading: vec!["ㄕ".to_string()], start: 0, key: String::new(), weight };
        for (weight, want) in [(0.0, ["濕", "溼"]), (1.0, ["溼", "濕"])] {
            let mut rows = vec![pred("溼", weight), pred("濕", 0.0)];
            let mut list: Vec<(String, usize)> = rows.iter().map(|p| (p.word.clone(), 1)).collect();
            e.variant_move(&mut list, &mut rows);
            assert_eq!(list.iter().map(|x| x.0.as_str()).collect::<Vec<_>>(), want, "weight {weight}: the list");
            assert_eq!(rows.iter().map(|p| p.word.as_str()).collect::<Vec<_>>(), want, "weight {weight}: rows follow the list");
        }
    }
}

/// docs/spec/english-mixed.md §6, the one rule for the short-word mark: only a key that changed nothing as a whole gives it back. A key that
/// ends an English run (or a digit buffer) and then dispatches itself again has changed something, whatever the second call does nothing
/// about. The mark and a run never meet in typing (a run opens with a key that takes the mark), so the state is set by hand.
#[cfg(test)]
mod short_mark_tests {
    use super::*;

    fn engine() -> Engine {
        let mut e = Engine::with_lexicon(Arc::new(Lexicon::parse("ㄅㄚ 爸 -1.0\n").unwrap()), Layout::Standard);
        e.set_english(true).unwrap();
        e
    }

    #[test]
    fn a_key_that_ends_a_run_and_then_does_nothing_keeps_the_mark_taken() {
        let mut e = engine();
        e.key(Key::ch('w', MOD_SHIFT)).unwrap(); // the run `W`
        e.last_short = Some((0, "ai".to_string()));
        let o = e.key(Key::new(KeyKind::Up)).unwrap(); // ends the run (a token), then Up does nothing in the inner dispatch
        assert!(o.handled && e.syls.len() == 1, "premise: the run became a token");
        assert!(e.last_short.is_none(), "the key changed something: the mark is not given back");
        // The same Up on a composition with no run changes nothing: the mark is given back.
        e.last_short = Some((0, "ai".to_string()));
        e.key(Key::new(KeyKind::Up)).unwrap();
        assert!(e.last_short.is_some(), "a key that changed nothing gives the mark back");
    }

    #[test]
    fn the_up_key_of_an_open_digit_buffer_follows_the_same_rule() {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
        let mut e = Engine::new(&root.join("data/lexicon"), Layout::Standard).unwrap();
        e.load_lm(&root.join("data/lm/bigram.sjlm")).unwrap();
        e.set_english(true).unwrap();
        e.key(Key::ch('1', 0)).unwrap();
        assert!(e.drun.is_some(), "premise: a digit buffer is open");
        e.last_short = Some((0, "ai".to_string()));
        e.key(Key::new(KeyKind::Up)).unwrap();
        assert!(e.drun.is_some() && e.last_short.is_some(), "Up in the buffer changes nothing: the mark stays");
    }
}

#[cfg(test)]
mod sinit_tests {
    use super::*;

    /// A path of one-character words `text`; `fuzzy` marks its last word as a fuzzy-sound one. `score` is its (unlifted) score.
    fn scored(text: &'static str, score: f64, fuzzy: bool) -> Scored<'static> {
        let n = text.chars().count();
        (score, text.char_indices().enumerate().map(|(i, (at, c))| (&text[at..at + c.len_utf8()], 0.0, 0.0, 1, fuzzy && i + 1 == n, None, false)).collect())
    }
    fn path(text: &'static str, fuzzy: bool) -> Scored<'static> {
        scored(text, 0.0, fuzzy)
    }
    fn named(prefix: &str, i: usize) -> &'static str {
        Box::leak(format!("{prefix}{i}").into_boxed_str())
    }
    const LEARNED: [(char, f64); 1] = [('照', 0.5)];

    /// docs/spec/learning.md §7.4: `b` is the weight of the learned character the path's text starts with, however the path cuts its words.
    #[test]
    fn b_is_the_weight_of_the_first_character() {
        assert_eq!(sinit_b(&path("照建議", false).1, &LEARNED), 0.5);
        assert_eq!(sinit_b(&path("趙建議", false).1, &LEARNED), 0.0);
        assert_eq!(sinit_b(&path("建議照", false).1, &LEARNED), 0.0, "only the first character counts");
        assert_eq!(sinit_b(&[("照建", 0.0, 0.0, 2, false, None, false), ("議", 0.0, 0.0, 1, false, None, false)], &LEARNED), 0.5, "inside a two-character word");
        assert_eq!(sinit_b(&[], &LEARNED), 0.0);
        assert_eq!(sinit_b(&path("照", false).1, &[]), 0.0);
    }

    /// docs/spec/learning.md §7.3 and §13.4: the best learned path of the lifted n-best that is not in the pool (compared by its text) comes into
    /// `best` as the unlifted path (lp0 of the lifted word, the unlifted score) and replaces the pool's last path, never the first; one already in the
    /// pool changes nothing; none in the lifted n-best (or only a fuzzy one) leaves the pool as it was.
    #[test]
    fn a_learned_path_outside_the_pool_takes_the_last_place() {
        let mut best: Vec<Scored> = (0..10).map(|i| path(named("趙", i), false)).collect();
        let mut pool: Vec<usize> = (0..8).collect();
        // The lifted n-best: its best learned path carries the lifted lp 0.0 and a score 2.0 above its unlifted -7.5 (lp0 -5.0 and the gain 2.0 in the marker).
        let mut learned = scored("照9", -5.5, false);
        learned.1[0] = ("照", 0.0, 0.0, 1, false, Some((-5.0, 2.0)), false);
        let lifted = vec![path("趙0", false), learned.clone(), scored("照10", -9.0, false)];
        assert_eq!(sinit_swap(&mut best, &lifted, &mut pool, &LEARNED), Swap::In);
        assert_eq!(pool, [0, 1, 2, 3, 4, 5, 6, 10], "the best learned path, in the last place; the first is where it was");
        assert_eq!(best.len(), 11, "the learned path is appended to the unlifted n-best");
        assert_eq!((best[10].0, best[10].1[0].1, best[10].1[0].5), (-7.5, -5.0, Some((-5.0, 2.0))), "its score is the unlifted one and the lifted word has its own lp again");
        assert_eq!(sinit_swap(&mut best, &lifted, &mut pool, &LEARNED), Swap::Kept, "now it is in the pool");
        assert_eq!(best.len(), 11);
        // a path of the same text in the unlifted pool is the learned path already
        let mut small = vec![0, 1];
        let mut two = vec![path("趙", false), path("照", false)];
        assert_eq!(sinit_swap(&mut two, &[path("趙", false), path("照", false)], &mut small, &LEARNED), Swap::Kept);
        let mut one = vec![path("趙", false), path("趙趙", false)];
        assert_eq!(sinit_swap(&mut one, &[path("趙", false)], &mut vec![0, 1], &LEARNED), Swap::Missing);
        let mut pool: Vec<usize> = (0..8).collect();
        let mut best: Vec<Scored> = (0..10).map(|i| path(named("趙", i), false)).collect();
        let none: Vec<Scored> = (0..10).map(|i| path(named("趙", i), false)).collect();
        assert_eq!(sinit_swap(&mut best, &none, &mut pool, &LEARNED), Swap::Missing);
        assert_eq!(pool, (0..8).collect::<Vec<_>>(), "no learned path in the lifted n-best: the pool is unchanged");
        let mut fuzzy_only: Vec<Scored> = (0..9).map(|i| path(named("趙", i), false)).collect();
        fuzzy_only.push(path("照9", true));
        assert_eq!(sinit_swap(&mut best, &fuzzy_only, &mut pool, &LEARNED), Swap::Missing, "a learned path with a fuzzy word is not a candidate");
        assert_eq!(pool, (0..8).collect::<Vec<_>>());
        assert_eq!(best.len(), 10);
    }
}

/// docs/spec/english-mixed.md §4.4: the table of official writings, lower case -> the line. Two files, both compiled in: products,
/// brands and abbreviations (`data/english-extra.txt`) and the English writings of the names layer
/// (`data/lexicon/names-english.txt`, docs/spec/lexicon-and-packs.md §3). A lower case in both is an error like one twice in a file.
fn english_table() -> &'static HashMap<String, &'static str> {
    static TABLE: std::sync::OnceLock<HashMap<String, &'static str>> = std::sync::OnceLock::new();
    TABLE.get_or_init(|| english_table_of(&ENGLISH_FILES).expect("data/english-extra.txt and data/lexicon/names-english.txt"))
}
const ENGLISH_FILES: [&str; 2] = [include_str!("../../data/english-extra.txt"), include_str!("../../data/lexicon/names-english.txt")];

/// The table's rules: a line is `[A-Za-z]{3,32}` with an upper case letter (an all lower case line would change nothing), `#` lines and
/// blank lines are skipped, and no two lines share a lower case.
fn english_table_of(texts: &[&'static str]) -> Result<HashMap<String, &'static str>, String> {
    let mut table = HashMap::new();
    for line in texts.iter().flat_map(|t| t.lines()).filter(|l| !l.is_empty() && !l.starts_with('#')) {
        let ok = (3..=32).contains(&line.len()) && line.bytes().all(|b| b.is_ascii_alphabetic()) && line.bytes().any(|b| b.is_ascii_uppercase());
        if !ok || table.insert(line.to_ascii_lowercase(), line).is_some() {
            return Err(format!("bad or repeated line: {line}"));
        }
    }
    Ok(table)
}

/// docs/spec/english-mixed.md §4.4: how an English word typed as `typed` is written. All lower case, or with only its first letter
/// upper case, and in the table: the table's line. Anything else the user typed with Shift on purpose: as typed.
pub fn english_writing(typed: &str) -> String {
    let lower = typed.to_ascii_lowercase();
    let plain = typed.is_ascii() && typed.get(1..).is_some_and(|rest| rest == &lower[1..]);
    match english_table().get(&lower).filter(|_| plain) {
        Some(w) => w.to_string(),
        None => typed.to_string(),
    }
}

#[cfg(test)]
mod english_table_tests {
    use super::*;

    #[test]
    fn the_table_loads_and_writes() {
        assert!(english_table().len() > 200);
        // Both files are in it.
        for (typed, want) in [("nanako", "Nanako"), ("Cablate", "CabLate"), ("CABLATE", "CABLATE"), ("chatgpt", "ChatGPT"), ("Chatgpt", "ChatGPT"), ("CHATGPT", "CHATGPT"), ("cHatgpt", "cHatgpt"), ("ChatGPT", "ChatGPT"),
            ("macos", "macOS"), ("Macos", "macOS"), ("rust", "rust"), ("Rust", "Rust"), ("", ""), ("a", "a"), ("gta6", "gta6")] {
            assert_eq!(english_writing(typed), want, "{typed}");
        }
    }

    #[test]
    fn a_bad_table_is_refused() {
        assert!(english_table_of(&["# c\n\nChatGPT\nGitHub\n"]).is_ok());
        for bad in ["ChatGPT\nchatGPT\n", "npm\n", "AI\n", "GPT4\n", "Wi-Fi\n"] {
            assert!(english_table_of(&[bad]).is_err(), "{bad}");
            assert!(english_table_of(&["GitHub\n", bad]).is_err(), "{bad} in the second file");
        }
        // The same lower case in both files.
        assert!(english_table_of(&["ChatGPT\n", "Nanako\n"]).is_ok());
        assert!(english_table_of(&["ChatGPT\nNanako\n", "NaNaKo\n"]).is_err());
        // The shipped files, one at a time: each is well formed alone, and each adds lines the other lacks.
        let (a, b) = (english_table_of(&ENGLISH_FILES[..1]).unwrap(), english_table_of(&ENGLISH_FILES[1..]).unwrap());
        assert!(a.len() > 200 && b.len() >= 5 && a.len() + b.len() == english_table().len());
    }
}

/// The two measurement hooks, on the real lexicon and model (data/lm/bigram.sjlm; fails loudly without it, never skips).
#[cfg(test)]
mod measure_hook_tests {
    use super::*;
    use crate::predict::predict;

    fn engine() -> Engine {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("..");
        let mut e = Engine::new(&root.join("data/lexicon"), Layout::Standard).unwrap();
        e.load_lm(&root.join("data/lm/bigram.sjlm")).expect("data/lm/bigram.sjlm and classes.sjc are required");
        e.set_abbreviation(true).unwrap();
        e
    }
    fn typ(e: &mut Engine, symbols: &str) {
        for c in symbols.chars() {
            e.key(Key::ch(Layout::Standard.key_of_symbol(c).unwrap(), 0)).unwrap();
        }
    }

    /// The units the abbreviation composer really makes (docs/spec/prediction.md §6): a symbol whose column advances joins the last
    /// unit, and a symbol in the same or an earlier column opens the next unit.
    #[test]
    fn pending_units_are_the_composers_units() {
        let mut e = engine();
        assert!(e.pending_units().is_empty(), "nothing unfinished");
        for (keys, want) in [("ㄧㄍ", vec!["ㄧ", "ㄍ"]), ("ㄧㄑㄧ", vec!["ㄧ", "ㄑㄧ"]), ("ㄉㄚㄢ", vec!["ㄉㄚ", "ㄢ"]), ("ㄒㄧㄢ", vec!["ㄒㄧㄢ"]), ("ㄋㄔ", vec!["ㄋ", "ㄔ"])] {
            e.reset(ResetMode::Discard);
            typ(&mut e, keys);
            assert_eq!(e.pending_units(), want, "{keys}");
        }
        e.reset(ResetMode::Discard);
        assert!(e.pending_units().is_empty(), "after a reset");
    }

    /// After `reset` and `set_left_context` only the cursor start is queried, so for a single-cut input the row is one `predict` call of the hook's parts.
    #[test]
    fn predict_parts_give_the_engines_own_row() {
        let mut e = engine();
        let left = "我想喝一杯";
        for (keys, units, mode) in [("ㄋㄔ", vec!["ㄋ", "ㄔ"], Mode::PA), ("ㄋ", vec!["ㄋ"], Mode::P)] {
            e.reset(ResetMode::Discard);
            e.set_left_context(left);
            typ(&mut e, keys);
            let own: Vec<(String, Vec<String>)> = e.pred.iter().map(|p| (p.word.clone(), p.reading.clone())).collect();
            assert!(!own.is_empty(), "{keys}: the engine shows a row");
            let units: Vec<Unit> = units.iter().map(|u| Unit { chars: u.chars().collect(), done: false, tone: None }).collect();
            let got = e
                .with_predict_parts(|idx, lm, cfg| {
                    assert_eq!(cfg, lm.scoring(Profile::Chat));
                    predict(idx, lm, cfg, history(&context_key(left), lm), &units, mode, 9)
                })
                .unwrap();
            assert_eq!(got.into_iter().map(|(w, _, _, r)| (w, r)).collect::<Vec<_>>(), own, "{keys}");
        }
        let bare = Engine::with_lexicon(Arc::new(Lexicon::parse("ㄅㄚ 爸 -1.0\n").unwrap()), Layout::Standard);
        assert!(bare.with_predict_parts(|_, _, _| ()).is_none(), "no model, no parts");
    }
}

/// The fields of `Shared`, listed with their types: it holds the loaded model and tables and the data folder, never the left context, a composition,
/// the learner or the choice memory, nor anything built from user input. The pattern names every field, so adding one stops compiling here until
/// the list (and docs/spec/privacy.md) is read again.
#[cfg(test)]
mod shared_fields {
    use super::*;

    #[test]
    fn shared_holds_only_models_tables_and_the_data_folder() {
        fn fields(s: Shared) {
            let Shared { data_dir, lm, tinylm, colloquial, reading, src_pack_single, digit_table, en_table, auto_table } = s;
            let _: Option<Arc<AutoTable>> = auto_table;
            let _: (PathBuf, Arc<Lm>, Option<Arc<TinyLm>>, Option<Arc<ColloquialTable>>) = (data_dir, lm, tinylm, colloquial);
            let _: (Option<Arc<crate::reading::ReadingDecode>>, bool, Option<Arc<DigitTable>>, Option<Arc<EnglishTable>>) = (reading, src_pack_single, digit_table, en_table);
        }
        let _ = fields;
    }
}

/// docs/spec/semantic-rerank.md §3.1.1 on `RerankJob::run`: hand-made stretches (one-word paths, given n-gram scores and SINIT weights, the context
/// recurrence set of a ≥ 32-character context holding the pairs named) on the toy small model of eval/golden/tinylm-tiny; the stretch lists are made by
/// `pool_lists`, the function `refresh_lm` makes them with.
#[cfg(test)]
mod pool_recur_tests {
    use super::*;
    use crate::lm::EPS_SINIT_FINE;

    fn toy() -> Arc<TinyLm> {
        let p = Path::new(env!("CARGO_MANIFEST_DIR")).join("../eval/golden/tinylm-tiny/tiny-int8.sjtl");
        Arc::new(TinyLm::parse(&std::fs::read(p).unwrap()).unwrap())
    }
    /// The recurrence set of `pairs` behind 32 ASCII letters: a context of at least `POOL_RECUR_MIN_CHARS` scalar values.
    fn grams(pairs: &str) -> RecurGrams {
        RecurGrams::from_text(&format!("{}{pairs}", "a".repeat(32)))
    }
    /// The toy model's LL of each text under the empty key (what a job with no left text scores with).
    fn ll(texts: &[&str]) -> Vec<f64> {
        toy().ll_many(&texts.iter().map(|t| ("", *t)).collect::<Vec<_>>()).unwrap()
    }
    #[allow(clippy::too_many_arguments)]
    fn stretch(texts: &[&str], ng: &[f64], b: &[f64], layer: SinitLayer, used: &[&str], manual: &ManualSet, g: Option<&RecurGrams>) -> Stretch {
        let (masked, bonus, order) = pool_lists(texts, ng, || used.iter().map(|s| s.to_string()).collect(), manual, g, None);
        Stretch {
            at: 0,
            paths: texts.iter().map(|t| vec![(t.to_string(), 0.0, 0.0, t.chars().count(), false, false)]).collect(),
            ng: ng.to_vec(),
            bonus,
            order,
            masked,
            b: b.to_vec(),
            layer,
            gap: String::new(),
        }
    }
    /// docs/spec/decoder.md §7: the Han part of a mixed word the decoder's first path took protects itself for the one job: the candidates that do not
    /// start with it are masked (so is one that holds it elsewhere), and without it (`None`: the same texts with no English part before them) none is.
    #[test]
    fn the_mixed_word_masks_for_its_job_only() {
        let (cands, ng, manual) = (["恤是", "敘事", "續恤", "恤事"], [0.0, -1.0, -2.0, -3.0], ManualSet::default());
        assert_eq!(pool_lists(&cands, &ng, Vec::new, &manual, None, Some("恤")).0, [false, true, true, false]);
        assert_eq!(pool_lists(&cands, &ng, Vec::new, &manual, None, None).0, [false; 4]);
    }
    fn job(s: Stretch, params: (f64, f64, f64)) -> RerankJob {
        RerankJob { generation: 1, model: toy(), params, colloquial: None, level: RerankLevel::Standard, eps_sinit: 1.0, left: String::new(), stretches: vec![s] }
    }
    fn none() -> ManualSet {
        ManualSet::default()
    }

    /// The pool 我們, 你好, 我好 (n-gram -1, -2, -3) with 你好 recurring: 你好 gains 2 and leads. The accessors keep the decoder's order; `pool_order` is
    /// the permutation. When sure (margin 1 against tau 0.5) the rerank keeps its first, 你好 (paths index 1); never sure, the candidate whose fused score
    /// wins by delta is chosen and mapped back to its paths index.
    #[test]
    fn a_reordered_pool_keeps_its_first_when_sure_and_takes_a_winner_by_delta() {
        let texts = ["我們", "你好", "我好"];
        let g = grams("你好");
        let make = |params| job(stretch(&texts, &[-1.0, -2.0, -3.0], &[0.0; 3], SinitLayer::Coarse, &[], &none(), Some(&g)), params);
        let j = make((0.0, 0.5, 0.0));
        assert_eq!(j.pool_order(), [vec![1, 0, 2]]);
        assert_eq!(j.pool_bonus(), [vec![0.0, 2.0, 0.0]]);
        assert_eq!(j.ng_scores(), [vec![-1.0, -2.0, -3.0]], "the decoder's scores, in its order");
        assert_eq!(j.candidates(), [texts.map(String::from).to_vec()]);
        assert_eq!(j.sinit_weights(), [vec![0.0; 3]]);
        assert_eq!(j.run().picks(), [1], "sure: the reordered first, not paths[0]");
        // Never sure: 我好 (paths 2) wins when its LL is the highest of the three and alpha makes the gain larger than every n-gram gap.
        let l = ll(&texts);
        assert!(l[2] > l[0] && l[2] > l[1], "premise: the toy model likes 我好 best {l:?}");
        let alpha = 10.0 / (l[2] - l[0].max(l[1]));
        assert_eq!(make((alpha, f64::INFINITY, 1.0)).run().picks(), [2], "by delta, mapped back to its paths index");
        // A delta it does not reach: the reordered first, 你好.
        let gain = (-3.0 + alpha * l[2]) - (0.0 + alpha * l[1]);
        assert_eq!(make((alpha, f64::INFINITY, gain + 1.0)).run().picks(), [1]);
        // Without the set nothing is reordered: the same job picks among the decoder's order (sure keeps paths[0]).
        let plain = job(stretch(&texts, &[-1.0, -2.0, -3.0], &[0.0; 3], SinitLayer::Coarse, &[], &none(), None), (0.0, 0.5, 0.0));
        assert_eq!((plain.pool_order(), plain.pool_bonus(), plain.run().picks()), (vec![vec![]], vec![vec![0.0; 3]], vec![0]));
    }

    /// The mask is the decoder's, permuted with the order: 我們好 (holds the manual word 我們), 我都好 (does not: masked), 我們他 (holds it, gains 2 from
    /// 們他 and leads). Without the manual word the toy model's favourite 我都好 is chosen; with it, it never is.
    #[test]
    fn the_mask_follows_the_order() {
        let texts = ["我們好", "我都好", "我們他"];
        let used = ["ㄨㄛˇ", "ㄇㄣ˙", "ㄏㄠˇ"];
        let manual = ManualSet::from_pairs([("我們", "ㄨㄛˇ-ㄇㄣ˙")]);
        let g = grams("們他");
        let l = ll(&texts);
        assert!(l[1] > l[0] && l[1] > l[2], "premise: the toy model likes 我都好 best {l:?}");
        let alpha = 10.0 / (l[1] - l[0].max(l[2]));
        let run = |manual: &ManualSet| {
            let j = job(stretch(&texts, &[-1.0, -1.5, -2.0], &[0.0; 3], SinitLayer::Coarse, &used, manual, Some(&g)), (alpha, f64::INFINITY, 0.0));
            (j.pool_order(), j.run().picks())
        };
        assert_eq!(run(&none()), (vec![vec![2, 0, 1]], vec![1]), "premise: unmasked, 我都好 wins");
        let (order, picks) = run(&manual);
        assert_eq!(order, [vec![2, 0, 1]]);
        assert_ne!(picks, [1], "the masked candidate is never chosen after the reorder");
    }

    /// The learning term takes the reordered first as the anchor: 照甲 (learned, b 0.5), 趙甲, 兆甲 with 趙甲 recurring (it leads, b 0). Sure (margin 1.5
    /// against tau 1), so only a taught candidate may replace it: 照甲 is taught relative to the anchor (term EPS_SINIT_FINE * 0.5 = 2) and wins
    /// (-1 + 2 against 0.5); the anchor did not hold the learning, so nothing is overturned; `sinit_diag` reads the reordered lists.
    #[test]
    fn the_learning_term_follows_the_order() {
        let texts = ["照甲", "趙甲", "兆甲"];
        assert_eq!(EPS_SINIT_FINE * 0.5, 2.0);
        let g = grams("趙甲");
        let j = job(stretch(&texts, &[-1.0, -1.5, -5.0], &[0.5, 0.0, 0.0], SinitLayer::Fine, &[], &none(), Some(&g)), (0.0, 1.0, 0.0));
        assert_eq!(j.pool_order(), [vec![1, 0, 2]]);
        assert_eq!(j.sinit_weights(), [vec![0.5, 0.0, 0.0]], "the decoder's order");
        let r = j.run();
        assert_eq!(r.picks(), [0]);
        assert_eq!(r.sinit_overturned(), 0);
        let d = r.sinit_diag();
        assert!(d.len() == 1 && (d[0].0 - 1.5).abs() < 1e-12 && d[0].1 == 0.0, "(ng'[0] - ng'[1], alpha * ...) of the learned candidate at position 1: {d:?}");
    }

    /// A learned path `sinit_swap` put last (你好, b 0.5) leads after the reorder (你好 recurring). Sure: it is kept (paths index 2), nothing overturned,
    /// `sinit_diag` is its own (0, 0). Never sure with alpha favouring 我好 (paths[0], b 0; the coarse layer adds no term): 我好 is chosen and the
    /// learned anchor is overturned.
    #[test]
    fn a_swapped_in_learned_path_that_leads_is_the_anchor() {
        let texts = ["我好", "他去", "你好"];
        let g = grams("你好");
        let make = |params| job(stretch(&texts, &[-1.0, -2.0, -2.5], &[0.0, 0.0, 0.5], SinitLayer::Coarse, &[], &none(), Some(&g)), params);
        let j = make((0.0, 0.4, 0.0));
        assert_eq!(j.pool_order(), [vec![2, 0, 1]]);
        let r = j.run();
        assert_eq!((r.picks(), r.sinit_overturned(), r.sinit_diag()), (vec![2], 0, vec![(0.0, 0.0)]));
        let l = ll(&texts);
        assert!(l[0] != l[2], "premise: the toy model tells 我好 from 你好 {l:?}");
        let alpha = 10.0 / (l[0] - l[2]);
        assert!((-1.0 + alpha * l[0]) > (-2.0 + alpha * l[1]), "premise: 我好 beats 他去 too");
        let r = make((alpha, f64::INFINITY, 0.0)).run();
        assert_eq!((r.picks(), r.sinit_overturned()), (vec![0], 1));
        let d = r.sinit_diag();
        assert!(d.len() == 1 && d[0].0 == 0.0 && d[0].1 == 0.0, "the anchor is the learned candidate: {d:?}");
    }

    /// A small model error (a candidate over its context: `TooLong`) keeps paths[0], the decoder's first, even when the pool was reordered.
    #[test]
    fn a_small_model_error_keeps_the_decoders_first() {
        let long = "丙".repeat(toy().ctx() + 1);
        let texts = ["甲乙", long.as_str()];
        let j = job(stretch(&texts, &[-1.0, -2.0], &[0.0; 2], SinitLayer::Coarse, &[], &none(), Some(&grams("丙丙"))), (0.0, f64::INFINITY, 0.0));
        assert_eq!(j.pool_order(), [vec![1, 0]], "premise: the long candidate leads");
        assert_eq!(j.run().picks(), [0]);
    }

    /// The decoder's first 我們好 holds the manual word 我們; 我都好 does not and would lead on its recurring 我都: the pool is not reordered, and the job
    /// chooses as the job without the set does.
    #[test]
    fn the_weight_never_puts_a_masked_candidate_first() {
        let texts = ["我們好", "我都好", "我們他"];
        let used = ["ㄨㄛˇ", "ㄇㄣ˙", "ㄏㄠˇ"];
        let manual = ManualSet::from_pairs([("我們", "ㄨㄛˇ-ㄇㄣ˙")]);
        let g = grams("我都");
        let ng = [-1.0, -1.5, -2.0];
        let unmasked = job(stretch(&texts, &ng, &[0.0; 3], SinitLayer::Coarse, &used, &none(), Some(&g)), (0.5, 2.0, 0.0));
        assert_eq!(unmasked.pool_order(), [vec![1, 0, 2]], "premise: without the manual word 我都好 would lead");
        for params in [(0.5, 2.0, 0.0), (5.0, f64::INFINITY, 0.0), (-5.0, f64::INFINITY, 0.0)] {
            let with = job(stretch(&texts, &ng, &[0.0; 3], SinitLayer::Coarse, &used, &manual, Some(&g)), params);
            let without = job(stretch(&texts, &ng, &[0.0; 3], SinitLayer::Coarse, &used, &manual, None), params);
            assert_eq!(with.pool_order(), [Vec::<usize>::new()]);
            assert_eq!(with.run().picks(), without.run().picks(), "{params:?}");
        }
    }

    /// Two stretches: the first one's choice is the second one's key (`context_key` of the text before it). Stretch 1 is 我們 / 你好 (n-gram -1, -2; 你好
    /// recurs and leads with the weight), stretch 2 is 人去 / 的嗎 (no recurring pair, tied n-gram scores). Never sure, alpha 1, delta 0: with the weight
    /// stretch 1 keeps 你好 (paths 1), its key 你好 makes the toy model prefer 人去 (paths 0); without it stretch 1 keeps 我們 (paths 0) and the key 我們
    /// makes it prefer 的嗎 (paths 1). `apply_rerank` splices the paths indices right to left into an engine whose path holds the decoder's first words.
    #[test]
    fn the_first_stretchs_reordered_choice_is_the_second_ones_key() {
        let toy = toy();
        let ll = |k: &str, t: &str| toy.ll_many(&[(k, t)]).unwrap()[0];
        assert!(ll("我們", "的嗎") > ll("我們", "人去") && ll("你好", "人去") > ll("你好", "的嗎"), "premise: the key flips the toy model's choice");
        let run = |g: Option<&RecurGrams>| {
            let mut e = Engine::with_lexicon(Arc::new(Lexicon::parse("ㄅㄚ 爸 -1.0\n").unwrap()), Layout::Standard);
            let word = |w: &str| PathWord { word: w.to_string(), lp: 0.0, sinit: false, is_punct: false, delta: 0.0, syllables: 2, mixed: false };
            e.path = vec![word("我們"), word("人去")];
            e.display = "我們人去".to_string();
            let mut s1 = stretch(&["我們", "你好"], &[-1.0, -2.0], &[0.0; 2], SinitLayer::Coarse, &[], &none(), g);
            let mut s2 = stretch(&["人去", "的嗎"], &[-1.0, -1.0], &[0.0; 2], SinitLayer::Coarse, &[], &none(), g);
            (s1.at, s2.at) = (0, 1);
            let mut j = job(s1, (1.0, f64::INFINITY, 0.0));
            j.stretches.push(s2);
            j.generation = e.generation;
            let (orders, r) = (j.pool_order(), j.run());
            let (picks, keys) = (r.picks(), r.keys());
            let shown = e.apply_rerank(r).unwrap().map(|o| o.preedit);
            (orders, picks, keys, shown, e.display.clone())
        };
        let g = grams("你好");
        let (orders, picks, keys, shown, display) = run(Some(&g));
        assert_eq!(orders, [vec![1, 0], vec![]], "only stretch 1 is reordered");
        assert_eq!((picks, keys), (vec![1, 0], vec![SENTINEL.to_string(), "你好".to_string()]));
        assert_eq!((shown.as_deref(), display.as_str()), (Some("你好人去"), "你好人去"));
        let (orders, picks, keys, shown, display) = run(None);
        assert_eq!(orders, [Vec::<usize>::new(), vec![]]);
        assert_eq!((picks, keys), (vec![0, 1], vec![SENTINEL.to_string(), "我們".to_string()]), "premise: without the weight both stretches choose otherwise");
        assert_eq!((shown.as_deref(), display.as_str()), (Some("我們的嗎"), "我們的嗎"));
    }

    /// docs/spec/privacy.md §5.2: the context recurrence set never goes into a rerank job, only the numbers made from it (`Stretch::bonus`, `order`). The
    /// patterns name every field of `RerankJob` and `Stretch` with its type, so adding a field (a `RecurGrams`, a pointer to one, its length) stops compiling
    /// here until this list and the privacy rule are read again.
    #[test]
    fn a_job_holds_no_recurrence_set() {
        fn job_fields(j: RerankJob) {
            let RerankJob { generation, model, params, colloquial, level, eps_sinit, left, stretches } = j;
            let _: (u64, Arc<TinyLm>, (f64, f64, f64), Option<Arc<Colloquial>>, RerankLevel, f64, String, Vec<Stretch>) =
                (generation, model, params, colloquial, level, eps_sinit, left, stretches);
        }
        fn stretch_fields(s: Stretch) {
            let Stretch { at, paths, ng, bonus, order, masked, b, layer, gap } = s;
            let _: (usize, Vec<Vec<JobWord>>, Vec<f64>, Vec<f64>, Vec<usize>, Vec<bool>, Vec<f64>, SinitLayer, String) = (at, paths, ng, bonus, order, masked, b, layer, gap);
        }
        let _ = (job_fields, stretch_fields);
    }

    /// The mask's base is the decoder's first D (我們他去, holds the manual word 我們), not the reordered first R (我們你好, holds 我們 and the manual word
    /// 你好, which D does not): D is not masked, and when D's fused score beats R's by delta D is chosen. A mask made with R as the base (the exploration
    /// patch's) masks D, so this test fails for it.
    #[test]
    fn the_mask_is_made_with_the_decoders_first() {
        let (d, r) = ("我們他去", "我們你好");
        let used = ["ㄨㄛˇ", "ㄇㄣ˙", "ㄋㄧˇ", "ㄏㄠˇ"];
        let manual = ManualSet::from_pairs([("我們", "ㄨㄛˇ-ㄇㄣ˙"), ("你好", "ㄋㄧˇ-ㄏㄠˇ")]);
        let strs = |v: &[&str]| v.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert_eq!(manual_mask(&strs(&used), &[r, d], &manual), [false, true], "premise: with R as the base D is masked");
        let l = ll(&[d, r]);
        assert!(l[0] != l[1], "premise: the toy model tells D from R {l:?}");
        // R leads by 1 after its gain of 2; D's fused score beats it by 9 >= delta 0.5.
        let alpha = 10.0 / (l[0] - l[1]);
        let j = job(stretch(&[d, r], &[-1.0, -2.0], &[0.0; 2], SinitLayer::Coarse, &used, &manual, Some(&grams("你好"))), (alpha, f64::INFINITY, 0.5));
        assert_eq!(j.pool_order(), [vec![1, 0]]);
        assert_eq!(j.stretches[0].masked, [false, false], "D is not masked");
        assert_eq!(j.run().picks(), [0], "D is chosen");
    }
}
