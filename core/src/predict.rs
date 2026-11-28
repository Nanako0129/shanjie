//! V3 core (docs/spec/prediction.md §2): successor-first prediction candidates for typed zhuyin keys.
//! Port of reference/proto/predict.py (units, compatibility, Index) and predict3.py (reference, order_s);
//! the Python code is ground truth, scores come from `Lm::word` unchanged.

use crate::digits::DigitTable;
use crate::english_next::Followers;
use crate::lm::{CappedLexicon, Lm, Scoring};
use crate::Lexicon;
use std::sync::Arc;
use std::collections::{HashMap, HashSet};
use std::cmp::Ordering;

const TONES: [char; 4] = ['ˊ', 'ˇ', 'ˋ', '˙'];

/// One typed syllable slot: zhuyin characters, whether a tone key closed it, and that tone (None: first tone).
#[derive(Clone, PartialEq)]
pub struct Unit {
    pub chars: Vec<char>,
    pub done: bool,
    pub tone: Option<char>,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    /// Prefix reading only.
    P,
    /// Union of the prefix and the abbreviation reading.
    PA,
}

/// predict.py `units_of`: a tone key (space is the first tone) completes the open unit; the next character opens a new one.
pub fn units_of(keys: &str) -> Vec<Unit> {
    let mut out: Vec<Unit> = Vec::new();
    for k in keys.chars() {
        let open = out.last_mut().filter(|u| !u.done);
        if k == ' ' || TONES.contains(&k) {
            if let Some(u) = open {
                (u.done, u.tone) = (true, (k != ' ').then_some(k));
            }
        } else if let Some(u) = open {
            u.chars.push(k);
        } else {
            out.push(Unit { chars: vec![k], done: false, tone: None });
        }
    }
    out
}

/// Golden text of a unit sequence: space-separated `chars/done(0|1)/tone`.
pub fn units_str(units: &[Unit]) -> String {
    let one = |u: &Unit| format!("{}/{}/{}", u.chars.iter().collect::<String>(), u.done as u8, u.tone.map_or(String::new(), String::from));
    units.iter().map(one).collect::<Vec<_>>().join(" ")
}

pub fn parse_units(s: &str) -> Option<Vec<Unit>> {
    s.split(' ')
        .map(|t| {
            let mut p = t.split('/');
            let (c, d, tone) = (p.next()?, p.next()?, p.next()?);
            let done = match d {
                "0" => false,
                "1" => true,
                _ => return None,
            };
            Some(Unit { chars: c.chars().collect(), done, tone: tone.chars().next() })
        })
        .collect()
}

struct Syl {
    chars: Vec<char>,
    tone: Option<char>,
}

/// predict.py `parse_syl`; a syllable without zhuyin characters is None.
fn parse_syl(s: &str) -> Option<Syl> {
    let mut chars: Vec<char> = s.chars().collect();
    let tone = if chars.last().is_some_and(|c| TONES.contains(c)) {
        chars.pop()
    } else if chars.first() == Some(&'˙') {
        Some(chars.remove(0))
    } else {
        None
    };
    (!chars.is_empty()).then_some(Syl { chars, tone })
}

/// A completed unit for a typed syllable text such as `ㄋㄧˇ` or `˙ㄅㄚ`; None when it has no zhuyin character.
pub fn unit_of_syllable(s: &str) -> Option<Unit> {
    parse_syl(s).map(|y| Unit { chars: y.chars, done: true, tone: y.tone })
}

fn unit_ok(u: &Unit, s: &Syl) -> bool {
    if u.done {
        s.chars == u.chars && s.tone == u.tone
    } else {
        s.chars.starts_with(&u.chars)
    }
}

/// Whether `predict` in `mode` could return a word read `reading` for `units`: a cheap string test on the first
/// syllable first, then the same compatibility test as the scan (the prefix reading, and in `PA` the abbreviation
/// reading, `compat_prefix` / `compat_abbr`). Never false for a reading `predict` would return.
pub fn reading_matches_in(units: &[Unit], reading: &[String], mode: Mode) -> bool {
    let Some(first) = units.first() else { return false };
    // Runs per learned record and start (up to 50,000 records): compare chars, allocate nothing.
    if reading.len() < units.len()
        || !first.chars.iter().copied().eq(reading[0].trim_start_matches('˙').chars().take(first.chars.len()))
    {
        return false;
    }
    let Some((_, init)) = units.split_last() else { return false };
    let abbr = mode == Mode::PA && reading.len() == units.len() && units.iter().all(|u| !u.done);
    (abbr || init.iter().all(|u| u.done)) && units.iter().zip(reading).all(|(u, y)| parse_syl(y).is_some_and(|s| unit_ok(u, &s)))
}

/// `table` is `Index::syls`; an index only lists readings without a zhuyin-less syllable, so every slot a reading names is `Some`.
fn compat_prefix(units: &[Unit], syls: &[u32], table: &[Option<Syl>]) -> bool {
    let Some((_, init)) = units.split_last() else { return false };
    syls.len() >= units.len() && init.iter().all(|u| u.done) && units.iter().zip(syls).all(|(u, &s)| table[s as usize].as_ref().is_some_and(|y| unit_ok(u, y)))
}

fn compat_abbr(units: &[Unit], syls: &[u32], table: &[Option<Syl>]) -> bool {
    !units.is_empty() && syls.len() == units.len() && units.iter().all(|u| !u.done) && units.iter().zip(syls).all(|(u, &s)| table[s as usize].as_ref().is_some_and(|y| unit_ok(u, y)))
}

/// One lexicon entry of the index, 24 bytes with no padding on 32-bit and 64-bit targets (all fields are full `f64`/`u32`, nothing is packed).
struct Ent {
    lp: f64,
    /// Id in the language model (u32::MAX: not in it).
    wid: u32,
    /// Index in `Lexicon::by_word`: the word's dense id (equal for equal words) and, `by_word` being sorted by the word's bytes, its sort rank.
    wix: u32,
    /// Reading number; readings are numbered in the order of their `-`-joined text, so it is also the reading's sort rank (`rd_to_lex` maps it back).
    rd: u32,
    wlen: u32,
}
const _: () = assert!(std::mem::size_of::<Ent>() == 24 && std::mem::align_of::<Ent>() == 8);

/// Lexicon entries sorted by (-lp, word length, reading, word) and bucketed by every prefix of the first syllable's
/// characters (predict.py `Index`). Entries with a syllable that has no zhuyin character are left out. Words and readings are read from
/// the shared `Lexicon` (`base`), not copied.
pub struct Index {
    base: Arc<Lexicon>,
    ents: Vec<Ent>,
    /// Indexed by the lexicon's syllable id; `None` for a syllable with no zhuyin character (the four bare tones).
    syls: Vec<Option<Syl>>,
    /// Reading number `rd` (text order, readings with a `None` syllable left out) -> the lexicon's reading index.
    rd_to_lex: Vec<u32>,
    by_prefix: HashMap<Vec<char>, Vec<u32>>,
    toneless: HashSet<Vec<char>>,
}

impl Index {
    pub fn new(lex: &CappedLexicon, lm: &Lm) -> Index {
        let base = lex.base().clone();
        let syls: Vec<Option<Syl>> = base.syllables().iter().map(|s| parse_syl(s)).collect();
        // Joined text compares like the syllable sequence because `-` sorts below every syllable character, so readings are
        // numbered by the lexicographic order of their syllables' text ranks.
        let names = base.syllables();
        let mut by_name: Vec<u32> = (0..names.len() as u32).collect();
        by_name.sort_unstable_by_key(|&i| names[i as usize].as_str());
        let mut syl_rank = vec![0u32; names.len()];
        for (k, &i) in by_name.iter().enumerate() {
            syl_rank[i as usize] = k as u32;
        }
        let ranks = |r: u32| base.reading_syl_ids(r as usize).iter().map(|&s| syl_rank[s as usize]);
        let mut rd_to_lex: Vec<u32> = (0..base.reading_count() as u32).filter(|&r| base.reading_syl_ids(r as usize).iter().all(|&s| syls[s as usize].is_some())).collect();
        rd_to_lex.sort_unstable_by(|&a, &b| ranks(a).cmp(ranks(b)));
        let mut lex_to_rd = vec![u32::MAX; base.reading_count()];
        for (k, &r) in rd_to_lex.iter().enumerate() {
            lex_to_rd[r as usize] = k as u32;
        }
        let mut ents: Vec<Ent> = Vec::with_capacity(base.main_ents());
        for (r, words) in lex.readings_by_index(lm) {
            let rd = lex_to_rd[r];
            if rd == u32::MAX {
                continue;
            }
            for (word, lp) in words {
                let wix = base.find_wix(word).expect("every word of a main reading is in by_word");
                ents.push(Ent { lp, wid: lm.word_id(word).unwrap_or(u32::MAX), wix, rd, wlen: word.chars().count() as u32 });
            }
        }
        ents.shrink_to_fit();
        // `wix` order is the word's byte order.
        ents.sort_by(|a, b| b.lp.partial_cmp(&a.lp).unwrap_or(Ordering::Equal).then(a.wlen.cmp(&b.wlen)).then(a.rd.cmp(&b.rd)).then(a.wix.cmp(&b.wix)));
        let mut by_prefix: HashMap<Vec<char>, Vec<u32>> = HashMap::new();
        for (i, e) in ents.iter().enumerate() {
            let first = base.reading_syl_ids(rd_to_lex[e.rd as usize] as usize)[0];
            let c0 = &syls[first as usize].as_ref().expect("a listed reading has only zhuyin syllables").chars;
            for j in 1..=c0.len() {
                by_prefix.entry(c0[..j].to_vec()).or_default().push(i as u32);
            }
        }
        by_prefix.values_mut().for_each(Vec::shrink_to_fit);
        let toneless: HashSet<Vec<char>> = syls.iter().flatten().map(|s| s.chars.clone()).collect();
        Index { base, ents, syls, rd_to_lex, by_prefix, toneless }
    }

    fn word(&self, e: &Ent) -> &str {
        self.base.word_by_wix(e.wix)
    }

    /// The syllable ids of the entry's reading.
    fn syl_ids(&self, e: &Ent) -> &[u32] {
        self.base.reading_syl_ids(self.rd_to_lex[e.rd as usize] as usize)
    }

    /// Whether `chars` equals a syllable in `syls` once its tone is stripped (docs/spec/prediction.md §6).
    pub fn has_toneless_syllable(&self, chars: &[char]) -> bool {
        self.toneless.contains(chars)
    }
}

/// At most this many cuts of the unfinished symbols are queried (docs/spec/prediction.md §6).
pub const ABBR_MAX_CUTS: usize = 64;

/// The column of a Zhuyin symbol: 0 initial, 1 medial (ㄧㄨㄩ), 2 final.
pub fn abbr_column(c: char) -> usize {
    match c {
        'ㄅ'..='ㄙ' => 0,
        'ㄧ' | 'ㄨ' | 'ㄩ' => 1,
        _ => 2,
    }
}

/// Every cut of `symbols` (in typing order) into units that keeps the typing order (docs/spec/prediction.md §6):
/// inside a unit the columns (`abbr_column`) only go strictly forward, so a symbol whose column is not after the
/// one typed before it must open a unit (a forced split), and any other symbol may join the unit or open one.
/// Under R3, when a run between forced splits satisfies `is_syllable`, a cut may either keep the whole run as one
/// unit or split every symbol into its own unit; runs that are not syllables are unrestricted.
/// No cut has more than `max_units` units, and `completed_syls` plus a cut's units never exceeds `max_composition`.
/// Both filters run before truncating to `ABBR_MAX_CUTS`; the flag reports whether a valid cut was truncated there.
/// Order: the cut that joins wherever it may first, then by increasing unit count, a later split before an earlier one.
pub fn abbr_cuts(
    symbols: &[char],
    completed_syls: usize,
    max_units: usize,
    max_composition: usize,
    is_syllable: impl Fn(&[char]) -> bool,
) -> (Vec<Vec<String>>, bool) {
    if symbols.is_empty() {
        return (Vec::new(), false);
    }
    let free = |i: usize| abbr_column(symbols[i]) > abbr_column(symbols[i - 1]);
    let opt: Vec<usize> = (1..symbols.len()).rev().filter(|&i| free(i)).collect();
    let forced = symbols.len() - 1 - opt.len();
    let mut constrained: Vec<(usize, usize)> = Vec::new();
    let mut run_start = 0;
    for i in 1..=symbols.len() {
        if i == symbols.len() || !free(i) {
            if i - run_start >= 3 && is_syllable(&symbols[run_start..i]) {
                constrained.push((run_start, i));
            }
            run_start = i;
        }
    }
    let limit = max_units.min(max_composition.saturating_sub(completed_syls));
    let mut out: Vec<Vec<String>> = Vec::new();
    for j in 0..=opt.len() {
        if forced + 1 + j > limit {
            break;
        }
        let mut pick: Vec<usize> = (0..j).collect();
        loop {
            let ok = constrained.iter().all(|&(a, b)| {
                let s = pick.iter().filter(|&&p| a < opt[p] && opt[p] < b).count();
                s == 0 || s == b - a - 1
            });
            if ok {
                if out.len() == ABBR_MAX_CUTS {
                    return (out, true);
                }
                let mut cut: Vec<String> = Vec::new();
                for (i, &c) in symbols.iter().enumerate() {
                    if i == 0 || !free(i) || pick.iter().any(|&p| opt[p] == i) {
                        cut.push(String::new());
                    }
                    cut.last_mut().expect("the first symbol opens a unit").push(c);
                }
                out.push(cut);
            }
            let Some(t) = (0..j).rev().find(|&t| pick[t] != t + opt.len() - j) else { break };
            pick[t] += 1;
            for u in t + 1..j {
                pick[u] = pick[u - 1] + 1;
            }
        }
    }
    (out, false)
}

/// Candidates of `units` in order S, at most `limit`: (word, score, is a successor of `v`, reading). The reading is the
/// syllable texts of the compatible reading that reached the word's `lp_max` (the one the order uses). `v` is the history word
/// (`lm::history`, maybe `<s>`). Tier one holds the words listed after `v`, tier two the rest; each tier is ordered by
/// (-score, word length, reading, word), where the reading is the smallest compatible one reaching the word's best
/// compatible score. That score (`lp_max`) is the first compatible entry of the word in bucket order.
pub fn predict(idx: &Index, lm: &Lm, cfg: impl Into<Scoring>, v: &str, units: &[Unit], mode: Mode, limit: usize) -> Vec<(String, f64, bool, Vec<String>)> {
    predict_capped(idx, lm, cfg, v, units, mode, limit, None)
}

/// `predict` with an optional upper bound `max_chars` on candidate word length (in Unicode scalar values), skipped during
/// the bucket scan before `limit` applies (docs/spec/prediction.md §3.3, §6).
#[allow(clippy::too_many_arguments)]
pub(crate) fn predict_capped(
    idx: &Index,
    lm: &Lm,
    cfg: impl Into<Scoring>,
    v: &str,
    units: &[Unit],
    mode: Mode,
    limit: usize,
    max_chars: Option<usize>,
) -> Vec<(String, f64, bool, Vec<String>)> {
    let cfg = cfg.into();
    let (succ, ctx) = (lm.successor_ids(v), lm.context_of(v));
    let classify = |e: &Ent| {
        let wid = (e.wid != u32::MAX).then_some(e.wid);
        (!wid.is_some_and(|w| succ.binary_search(&w).is_ok()) as usize, lm.word_by_id(cfg, ctx, wid, e.lp))
    };
    tiered(idx, units, mode, limit, max_chars, classify).into_iter().map(|(e, s, t)| (idx.word(e).to_string(), s, t == 0, reading_of(idx, e))).collect()
}

fn reading_of(idx: &Index, e: &Ent) -> Vec<String> {
    idx.base.reading_syllables(idx.rd_to_lex[e.rd as usize] as usize).into_iter().map(String::from).collect()
}

/// The words compatible with `units` (prefix reading, or also the abbreviation reading in `Mode::PA`), one entry per word in bucket order,
/// split into tiers by `classify` (tier, score), each tier ordered by (-score, word length, reading, word) and cut to `limit`, the tiers
/// in order, at most `limit` in all: (entry, score, tier). When `max_chars` is `Some(mc)`, words longer than `mc` characters are skipped
/// during the scan before `limit`. Shared by `predict`, `predict_digits` and `predict_english`.
fn tiered<'i>(
    idx: &'i Index,
    units: &[Unit],
    mode: Mode,
    limit: usize,
    max_chars: Option<usize>,
    mut classify: impl FnMut(&'i Ent) -> (usize, f64),
) -> Vec<(&'i Ent, f64, usize)> {
    let Some(bucket) = units.first().and_then(|u| idx.by_prefix.get(u.chars.as_slice())) else { return Vec::new() };
    let mut seen = HashSet::new();
    let mut tiers: [Vec<(f64, &Ent)>; 3] = Default::default();
    for &i in bucket {
        let e = &idx.ents[i as usize];
        if max_chars.is_some_and(|mc| e.wlen as usize > mc) {
            continue;
        }
        let syls = idx.syl_ids(e);
        if (compat_prefix(units, syls, &idx.syls) || (mode == Mode::PA && compat_abbr(units, syls, &idx.syls))) && seen.insert(e.wix) {
            let (t, s) = classify(e);
            tiers[t].push((s, e));
        }
    }
    let mut out: Vec<(&Ent, f64, usize)> = Vec::new();
    for (t, tier) in tiers.iter_mut().enumerate() {
        let order = |a: &(f64, &Ent), b: &(f64, &Ent)| {
            b.0.partial_cmp(&a.0).unwrap_or(Ordering::Equal).then(a.1.wlen.cmp(&b.1.wlen)).then(a.1.rd.cmp(&b.1.rd)).then(a.1.wix.cmp(&b.1.wix))
        };
        if tier.len() > limit && limit > 0 {
            tier.select_nth_unstable_by(limit - 1, order);
            tier.truncate(limit);
        }
        tier.sort_by(order);
        out.extend(tier.iter().map(|&(s, e)| (e, s, t)));
    }
    out.truncate(limit);
    out
}

/// The words compatible (prefix reading) with `units`, one entry per word in bucket order: (word, score, model id). The first compatible
/// entry of a word is its best, so the score is the word's best compatible lp (predict3.reference of the Python baseline).
pub fn compat_words<'i>(idx: &'i Index, units: &[Unit]) -> Vec<(&'i str, f64, Option<u32>)> {
    let Some(bucket) = units.first().and_then(|u| idx.by_prefix.get(u.chars.as_slice())) else { return Vec::new() };
    let mut seen = HashSet::new();
    let mut out = Vec::new();
    for &i in bucket {
        let e = &idx.ents[i as usize];
        let syls = idx.syl_ids(e);
        if compat_prefix(units, syls, &idx.syls) && seen.insert(e.wix) {
            out.push((idx.word(e), e.lp, (e.wid != u32::MAX).then_some(e.wid)));
        }
    }
    out
}

/// What the row of the digit context needs of a word when no syllable is unfinished: its best entry (`lex.by_word` of the baseline).
pub struct DigitWord {
    lp: f64,
    rd: u32,
    wlen: u32,
    wid: Option<u32>,
    reading: Vec<String>,
}

/// The best entry of each word of a set (docs/spec/english-mixed.md §7.6), built once per index and table.
pub struct DigitWords(HashMap<String, DigitWord>);

impl Index {
    /// The first entry in the index order (highest score, then word length, reading, word) of every word in `words` that has one.
    pub fn digit_words(&self, words: &HashSet<&str>) -> DigitWords {
        let mut out: HashMap<String, DigitWord> = HashMap::new();
        for e in &self.ents {
            let word = self.word(e);
            if words.contains(word) && !out.contains_key(word) {
                let reading = self.base.reading_syllables(self.rd_to_lex[e.rd as usize] as usize).into_iter().map(String::from).collect();
                out.insert(word.to_string(), DigitWord { lp: e.lp, rd: e.rd, wlen: e.wlen, wid: (e.wid != u32::MAX).then_some(e.wid), reading });
            }
        }
        DigitWords(out)
    }
}

/// The prediction row after digits (docs/spec/english-mixed.md §7.6): (word, score, reading), best first, at most `limit`. `v` is the run's history word,
/// `cfg` the profile's scoring (`Lm::scoring`: its lambda and Kneser-Ney beta; a plain lambda keeps the side file's beta). With units (an unfinished syllable): the candidates of `predict` (compatibility, buckets, dedupe, order),
/// scored `lam * log10 P(w | v, D) + (1 - lam) * lp`, the successor layer (N(v,D->w) > 0 or N(D->w) > 0) first. Without units: the words of
/// the successor layer that the lexicon has, scored `log10 P(w | v, D)` alone, ordered the same way.
pub fn predict_digits(idx: &Index, lm: &Lm, table: &DigitTable, words: &DigitWords, cfg: impl Into<Scoring>, v: &str, units: &[Unit], limit: usize) -> Vec<(String, f64, Vec<String>)> {
    predict_digits_capped(idx, lm, table, words, cfg, v, units, limit, None)
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn predict_digits_capped(
    idx: &Index,
    lm: &Lm,
    table: &DigitTable,
    words: &DigitWords,
    cfg: impl Into<Scoring>,
    v: &str,
    units: &[Unit],
    limit: usize,
    max_chars: Option<usize>,
) -> Vec<(String, f64, Vec<String>)> {
    let cfg = cfg.into();
    if units.is_empty() {
        let rows = table.successors(v).filter_map(|w| {
            let info = words.0.get(w)?;
            Some((table.p_w_given_vd(w, v, lm.unigram_back(cfg, info.wid, info.lp)).log10(), w, info))
        });
        return known_row(rows, limit);
    }
    let classify = |e: &Ent| {
        let word = idx.word(e);
        let p = table.p_w_given_vd(word, v, lm.unigram_back(cfg, (e.wid != u32::MAX).then_some(e.wid), e.lp));
        (!table.is_successor(v, word) as usize, if p > 0.0 { cfg.lam * p.log10() + (1.0 - cfg.lam) * e.lp } else { f64::NEG_INFINITY })
    };
    tiered(idx, units, Mode::P, limit, max_chars, classify).into_iter().map(|(e, s, _)| (idx.word(e).to_string(), s, reading_of(idx, e))).collect()
}

/// The words of a set that the lexicon has, ordered by (-score, word length, reading, word), at most `limit`: (word, score, reading).
fn known_row<'a>(rows: impl Iterator<Item = (f64, &'a str, &'a DigitWord)>, limit: usize) -> Vec<(String, f64, Vec<String>)> {
    let mut rows: Vec<_> = rows.collect();
    rows.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(Ordering::Equal).then(a.2.wlen.cmp(&b.2.wlen)).then(a.2.rd.cmp(&b.2.rd)).then(a.1.cmp(b.1)));
    rows.truncate(limit);
    rows.into_iter().map(|(s, w, info)| (w.to_string(), s, info.reading.clone())).collect()
}

/// The prediction row behind an English word (docs/spec/english-mixed.md §8): (word, score, reading), best first, at most `limit`. `f` is the
/// word's row of the table, `v` the history word of the text before (`<s>` after English). With units: the words of `f` that are
/// compatible come first, scored `lam * log10 P(w | e) + (1 - lam) * lp`; then the other compatible words as `predict` orders them
/// (successors of `v` first). Without units: the words of `f` that the lexicon has, scored `log10 P(w | e)`.
pub fn predict_english(idx: &Index, lm: &Lm, f: &Followers, words: &DigitWords, cfg: impl Into<Scoring>, v: &str, units: &[Unit], mode: Mode, limit: usize) -> Vec<(String, f64, Vec<String>)> {
    predict_english_capped(idx, lm, f, words, cfg, v, units, mode, limit, None)
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn predict_english_capped(
    idx: &Index,
    lm: &Lm,
    f: &Followers,
    words: &DigitWords,
    cfg: impl Into<Scoring>,
    v: &str,
    units: &[Unit],
    mode: Mode,
    limit: usize,
    max_chars: Option<usize>,
) -> Vec<(String, f64, Vec<String>)> {
    let cfg = cfg.into();
    if units.is_empty() {
        let rows = f.words().filter_map(|w| {
            let info = words.0.get(w)?;
            Some((f.p_w_given_e(w, lm.unigram_back(cfg, info.wid, info.lp)).log10(), w, info))
        });
        return known_row(rows, limit);
    }
    let (succ, ctx) = (lm.successor_ids(v), lm.context_of(v));
    let classify = |e: &Ent| {
        let wid = (e.wid != u32::MAX).then_some(e.wid);
        let word = idx.word(e);
        if f.contains(word) {
            (0, cfg.lam * f.p_w_given_e(word, lm.unigram_back(cfg, wid, e.lp)).log10() + (1.0 - cfg.lam) * e.lp)
        } else {
            (1 + !wid.is_some_and(|w| succ.binary_search(&w).is_ok()) as usize, lm.word_by_id(cfg, ctx, wid, e.lp))
        }
    };
    tiered(idx, units, mode, limit, max_chars, classify).into_iter().map(|(e, s, _)| (idx.word(e).to_string(), s, reading_of(idx, e))).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn data_root() -> std::path::PathBuf {
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
    }

    fn lm() -> Lm {
        Lm::load(&data_root().join("data/lm/bigram.sjlm")).unwrap()
    }

    fn toy(text: &str, lm: &Lm) -> Index {
        let capped = CappedLexicon::new(Arc::new(Lexicon::parse(text).unwrap()), "", lm, None).unwrap();
        Index::new(&capped, lm)
    }

    fn words(idx: &Index) -> Vec<&str> {
        idx.ents.iter().map(|e| idx.word(e)).collect()
    }

    /// `rd_to_lex` lists readings by the byte order of their `-`-joined text, none of them with a bare-tone syllable, and `ents`,
    /// `by_prefix` and `toneless` hold nothing of the left-out ones.
    fn check_reading_order(idx: &Index) {
        let joined = |r: u32| idx.base.reading_syllables(r as usize).join("-");
        assert!(idx.rd_to_lex.windows(2).all(|w| joined(w[0]) < joined(w[1])), "rd_to_lex is not in the order of the joined text");
        let kept: HashSet<u32> = idx.rd_to_lex.iter().copied().collect();
        for r in 0..idx.base.reading_count() as u32 {
            let bare = idx.base.reading_syl_ids(r as usize).iter().any(|&s| idx.syls[s as usize].is_none());
            assert_eq!(kept.contains(&r), !bare, "reading {}", joined(r));
        }
        assert!(idx.ents.iter().all(|e| kept.contains(&idx.rd_to_lex[e.rd as usize])));
        assert!(idx.ents.iter().all(|e| idx.syl_ids(e).iter().all(|&s| idx.syls[s as usize].is_some())));
        assert!(!idx.toneless.contains(&Vec::new()));
        assert_eq!(idx.ents.capacity(), idx.ents.len());
    }

    #[test]
    fn ent_is_24_bytes() {
        assert_eq!(std::mem::size_of::<Ent>(), 24);
        assert_eq!(std::mem::align_of::<Ent>(), 8);
    }

    #[test]
    fn reading_numbers_follow_the_joined_text_and_bare_tones_are_left_out() {
        let lm = lm();
        // "ㄅ" < "ㄅ-ㄚ" < "ㄅㄚ" < "ㄅㄚˋ": the `-` sorts below every syllable character, so a shorter key is not always first;
        // the neutral tone's mark `˙` (U+02D9) leads its syllable and sorts below the zhuyin letters.
        let idx = toy(
            "ㄅㄚˋ 爸 -1.5\nㄅㄚ 把 -1.0\nㄅ-ㄚ 甲乙 -1.0\nㄅ 不 -1.0\nˇ 嗯 -1.0\nㄅ-ˇ 把嗯 -1.0\nˊ-ㄅㄚ 嗯爸 -1.0\n˙ㄅㄚ 吧 -3.0\nㄅㄚ-ㄅㄚ 爸爸 -1.0\n",
            &lm,
        );
        check_reading_order(&idx);
        let order: Vec<String> = idx.rd_to_lex.iter().map(|&r| idx.base.reading_syllables(r as usize).join("-")).collect();
        assert_eq!(order, ["˙ㄅㄚ", "ㄅ", "ㄅ-ㄚ", "ㄅㄚ", "ㄅㄚ-ㄅㄚ", "ㄅㄚˋ"]);
        let left_out = ["嗯", "把嗯", "嗯爸"];
        assert!(words(&idx).iter().all(|w| !left_out.contains(w)));
        let first = &idx.by_prefix[&vec!['ㄅ']];
        assert!(first.iter().all(|&i| !left_out.contains(&idx.word(&idx.ents[i as usize]))));
        assert!(!idx.has_toneless_syllable(&['ˇ']) && !idx.has_toneless_syllable(&['ˊ']));
        assert!(idx.has_toneless_syllable(&['ㄅ', 'ㄚ']) && idx.has_toneless_syllable(&['ㄅ']));
    }

    #[test]
    fn a_300_character_word_and_equal_scores_break_ties_by_length_reading_then_word_bytes() {
        let lm = lm();
        let long_reading = vec!["ㄅ"; 300].join("-");
        let long_word = "戊".repeat(300);
        let text = format!("ㄅㄚ 乙 -1.0\nㄅㄚ 甲 -1.0\nㄅㄚ 丙 -1.0\nㄅㄚˋ 丁 -1.0\n{long_reading} {long_word} -1.0\nㄅ 不 -2.0\n");
        let idx = toy(&text, &lm);
        // Same score: shorter word first; then the reading in text order (ㄅㄚ before ㄅㄚˋ, though 丁 sorts below the words of ㄅㄚ);
        // then the word's bytes (丙 U+4E19 < 乙 U+4E59 < 甲 U+7532).
        assert_eq!(words(&idx), ["丙", "乙", "甲", "丁", long_word.as_str(), "不"]);
        assert_eq!(idx.ents[4].wlen, 300);
        let order: Vec<&str> = compat_words(&idx, &units_of("ㄅ")).into_iter().map(|x| x.0).collect();
        assert_eq!(order, ["丙", "乙", "甲", "丁", long_word.as_str(), "不"]);
        check_reading_order(&idx);
    }

    #[test]
    fn the_shipped_lexicon_numbers_readings_in_text_order() {
        let (dir, packs) = (data_root().join("data/lexicon"), data_root().join("data/packs"));
        let mask = 31;
        let (lex, pack_text) = crate::engine::load_lexicon_packs(&dir, Some((&packs, mask))).unwrap();
        let overlay = crate::engine::capping_overlay(&dir, &pack_text).unwrap();
        let demote = crate::lm::Demote::parse(&std::fs::read_to_string(dir.join("demote.tsv")).unwrap()).unwrap();
        let lm = lm();
        let oov = crate::engine::read_packs_detailed(&packs, mask & crate::engine::PACK_OOV).unwrap();
        let capped = CappedLexicon::with_packs(lex, &overlay, &oov, &lm, Some(&demote)).unwrap();
        let idx = Index::new(&capped, &lm);
        check_reading_order(&idx);
        let left_out = idx.base.reading_count() - idx.rd_to_lex.len();
        assert_eq!(left_out, 4, "the four bare-tone readings");
    }
}
