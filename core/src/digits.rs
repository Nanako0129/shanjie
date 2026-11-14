//! Digits in the Zhuyin composition (docs/spec/english-mixed.md §7.2, §7.8, §7.3): the count table `digits.tsv`, the legal tails of a
//! buffer of keys, and the scoring that picks, for every key, how many of the buffered keys are digits and how many are the
//! unfinished syllable behind them. A port of reference/proto/digits_score.py (`Table`, `Legal`, `Scorer.decide`); Python is the
//! baseline and `cli/tests/golden.rs` compares the scores bit for bit. Every sum is a plain left-to-right float sum in candidate order.

use crate::engine::Layout;
use crate::learn::is_han;
use crate::lm::{Lm, Scoring};
use crate::predict::{compat_words, units_of, Index, Unit};
use std::collections::{HashMap, HashSet};

/// The alphabet A: the keys that are digits and also Zhuyin keys or tone keys of the standard layout.
pub const ALPHABET: &str = "0123456789./-";
/// A buffer holds at most this many keys (a syllable is three symbols and a tone).
pub const MAX_BUF: usize = 4;
/// C's R2: a pure-Zhuyin split of a closed syllable is scored `theta` higher.
pub const THETA: f64 = 3.0;
/// docs/spec/english-mixed.md §7.3, §15: how much lower (log10) a reading with keys put in column order scores than the same syllable typed in order.
/// The value is the cell the grid of the research repo's experiments/reversal-digits chose (registration and results, 2026-11-14: (7, 4) of ρ in 6..10 by ρs in 4, 6).
/// A run's digit-alphabet keys can rejoin the syllable typed right behind them only at this price: lower and more numbers are eaten, higher and fewer hurried syllables are saved.
pub const RHO: f64 = 7.0;
/// `RHO` for a run that starts with `.` `/` `-` (a decimal point, a slash or a hyphen is a weaker start of a number: with `RHO` alone the hurried `. u 3` at a sentence start stayed `.以`).
pub const RHO_SYM: f64 = 4.0;
/// beta of P(D | C) and gamma of P(w | v, D) (docs/spec/english-mixed.md §7.2, §7.8, written in).
const BETA: f64 = 1.0;
const GAMMA: f64 = 1.0;
/// The `10+` row of `L`.
const LONG: usize = 10;
const TONE_CHARS: [char; 4] = ['ˊ', 'ˇ', 'ˋ', '˙'];

pub fn is_alphabet(c: char) -> bool {
    ALPHABET.contains(c)
}

/// The count table (docs/spec/english-mixed.md §7.2, §7.8). Absent or malformed means the digit feature is off: `parse` returns `None` and says nothing else.
pub struct DigitTable {
    t: u64,
    nd: u64,
    d: HashMap<String, (u64, u64)>,
    w: HashMap<String, u64>,
    q: HashMap<String, u64>,
    p: HashMap<String, HashMap<String, u64>>,
    l: [u64; LONG + 1],
    c: HashMap<char, u64>,
    sum_c: u64,
}

impl DigitTable {
    pub fn parse(text: &str) -> Option<DigitTable> {
        let mut tb = DigitTable { t: 0, nd: 0, d: HashMap::new(), w: HashMap::new(), q: HashMap::new(), p: HashMap::new(), l: [0; LONG + 1], c: HashMap::new(), sum_c: 0 };
        for line in text.split('\n') {
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let f: Vec<&str> = line.split('\t').collect();
            let num = |i: usize| f.get(i).and_then(|x| x.parse::<u64>().ok());
            match *f.first()? {
                "T" => tb.t = num(1)?,
                "N" => tb.nd = num(1)?,
                "S" => {
                    num(1)?;
                }
                "D" => {
                    tb.d.insert(f.get(1)?.to_string(), (num(2)?, num(3)?));
                }
                "W" => {
                    tb.w.insert(f.get(1)?.to_string(), num(2)?);
                }
                "Q" => {
                    tb.q.insert(f.get(1)?.to_string(), num(2)?);
                }
                "P" => {
                    tb.p.entry(f.get(1)?.to_string()).or_default().insert(f.get(2)?.to_string(), num(3)?);
                }
                "L" => {
                    let k = if *f.get(1)? == "10+" { LONG } else { f.get(1)?.parse::<usize>().ok().filter(|k| (1..LONG).contains(k))? };
                    tb.l[k] = num(2)?;
                }
                "C" => {
                    let mut ch = f.get(1)?.chars();
                    let (c, None) = (ch.next()?, ch.next()) else { return None };
                    tb.c.insert(c, num(2)?);
                }
                _ => return None,
            }
        }
        tb.sum_c = tb.c.values().sum();
        (tb.t > 0 && tb.nd > 0 && tb.sum_c > 0).then_some(tb)
    }

    fn p_d(&self) -> f64 {
        self.nd as f64 / self.t as f64
    }

    /// (N(C->D) + beta P(D)) / (N(C) + beta); no D row for `ctx`: P(D).
    pub fn p_d_given(&self, ctx: &str) -> f64 {
        match self.d.get(ctx) {
            None => self.p_d(),
            Some(&(a, b)) => (a as f64 + BETA * self.p_d()) / (b as f64 + BETA),
        }
    }

    /// P_len(m | ended) = N(L = m) / N(D); not ended: N(L >= m) / N(D); m >= 10 reads the `10+` row either way.
    pub fn p_len(&self, m: usize, ended: bool) -> f64 {
        let m = m.min(LONG);
        let n = if ended || m == LONG { self.l[m] } else { self.l[m..].iter().sum() };
        n as f64 / self.nd as f64
    }

    pub fn p_ch(&self, ch: char) -> f64 {
        self.c.get(&ch).copied().unwrap_or(0) as f64 / self.sum_c as f64
    }

    /// (N(D->w) + gamma P(w)) / (N(D) + gamma); no W row: N(D->w) = 0.
    fn p_w_given_d(&self, w: &str, pw: f64) -> f64 {
        (self.w.get(w).copied().unwrap_or(0) as f64 + GAMMA * pw) / (self.nd as f64 + GAMMA)
    }

    /// (N(v,D->w) + gamma P(w | D)) / (N(v,D->.) + gamma); no Q row for `v`: P(w | D); no P row: N(v,D->w) = 0.
    pub fn p_w_given_vd(&self, w: &str, v: &str, pw: f64) -> f64 {
        let base = self.p_w_given_d(w, pw);
        match self.q.get(v) {
            None => base,
            Some(&q) => (self.p.get(v).and_then(|m| m.get(w)).copied().unwrap_or(0) as f64 + GAMMA * base) / (q as f64 + GAMMA),
        }
    }

    /// The successor layer of docs/spec/english-mixed.md §7.6: words with N(v,D->w) > 0 or N(D->w) > 0.
    pub fn is_successor(&self, v: &str, w: &str) -> bool {
        self.w.contains_key(w) || self.p.get(v).is_some_and(|m| m.contains_key(w))
    }

    /// Every word of the layer for `v`, each once (the words of the `W` rows first).
    pub fn successors<'a>(&'a self, v: &'a str) -> impl Iterator<Item = &'a str> + 'a {
        self.w.keys().map(String::as_str).chain(self.p.get(v).into_iter().flat_map(|m| m.keys().filter(|w| !self.w.contains_key(*w)).map(String::as_str)))
    }

    /// Every word any successor layer can hold (all `W` words and all `P` words): what the word-reading cache is built for.
    pub fn all_words(&self) -> HashSet<&str> {
        self.w.keys().map(String::as_str).chain(self.p.values().flat_map(|m| m.keys().map(String::as_str))).collect()
    }
}

fn symbol_of_key(k: char) -> Option<char> {
    Layout::Standard.symbol_of(k).map(|(_, s)| s)
}

/// Mark of a tone key (ˊˇˋ˙) of the standard layout.
fn tone_mark_of_key(k: char) -> Option<char> {
    Layout::Standard.key_tone_mark(k)
}

/// The legal-tail test (docs/spec/english-mixed.md §7.1, "切分"), built from the lexicon's readings.
pub struct Legal {
    prefixes: HashSet<String>,
    han: HashSet<String>,
}

impl Legal {
    /// `readings`: every reading with its (word, score) list, as `CappedLexicon::readings` gives them.
    pub fn new<'a>(readings: impl Iterator<Item = (Vec<&'a str>, Vec<(&'a str, f64)>)>) -> Legal {
        let (mut prefixes, mut han) = (HashSet::new(), HashSet::new());
        let mut seen: HashSet<&'a str> = HashSet::new();
        for (syls, entries) in readings {
            // The prefixes of a syllable are the same wherever it occurs: work them out the first time only.
            for s in &syls {
                if seen.insert(s) {
                    let bare: Vec<char> = s.chars().filter(|c| !TONE_CHARS.contains(c)).collect();
                    for i in 1..=bare.len() {
                        prefixes.insert(bare[..i].iter().collect::<String>());
                    }
                }
            }
            // A complete syllable must occur in an entry with a Han character (not just the letter word "ㄅ").
            if entries.iter().any(|(w, _)| w.chars().any(is_han)) {
                for s in &syls {
                    if !han.contains(*s) {
                        han.insert(s.to_string());
                    }
                }
            }
        }
        Legal { prefixes, han }
    }

    /// `new` as it was written before the syllable cache (each syllable's prefixes and string redone at every reading), kept for the test that builds both ways.
    #[cfg(test)]
    pub(crate) fn new_each<'a>(readings: impl Iterator<Item = (Vec<&'a str>, Vec<(&'a str, f64)>)>) -> Legal {
        let (mut prefixes, mut han) = (HashSet::new(), HashSet::new());
        for (syls, entries) in readings {
            for s in &syls {
                let bare: Vec<char> = s.chars().filter(|c| !TONE_CHARS.contains(c)).collect();
                for i in 1..=bare.len() {
                    prefixes.insert(bare[..i].iter().collect::<String>());
                }
            }
            if entries.iter().any(|(w, _)| w.chars().any(is_han)) {
                han.extend(syls.iter().map(|s| s.to_string()));
            }
        }
        Legal { prefixes, han }
    }

    /// The two sets (tests).
    #[cfg(test)]
    pub(crate) fn sets(&self) -> (&HashSet<String>, &HashSet<String>) {
        (&self.prefixes, &self.han)
    }

    /// Whether `syl` (with its tone mark, none for tone 1) is a complete syllable of an entry with a Han character.
    pub fn has_han_syllable(&self, syl: &str) -> bool {
        self.han.contains(syl)
    }

    /// Keys -> units when they form a legal tail, else `None`. Last key a tone key or Space: a complete syllable that exists in the
    /// lexicon with a Han entry; otherwise symbols only, a prefix of some toneless syllable (this also fixes the initial-medial-final order).
    pub fn tail_units(&self, keys: &[char]) -> Option<Vec<Unit>> {
        let last = *keys.last()?;
        if last == ' ' || tone_mark_of_key(last).is_some() {
            let body = &keys[..keys.len() - 1];
            if body.is_empty() {
                return None;
            }
            let mut s: String = body.iter().map(|&k| symbol_of_key(k)).collect::<Option<String>>()?;
            let mark = tone_mark_of_key(last);
            let mut full = s.clone();
            full.extend(mark);
            if !self.han.contains(&full) {
                return None;
            }
            s.push(mark.unwrap_or(' '));
            return Some(units_of(&s));
        }
        let s: String = keys.iter().map(|&k| symbol_of_key(k)).collect::<Option<String>>()?;
        self.prefixes.contains(&s).then(|| units_of(&s))
    }
}

impl Legal {
    /// `tail_units` of the tail with its symbol keys in column order (docs/spec/english-mixed.md §7.3): the keys but the last (the closing key) go one to a
    /// column (initial, medial, final), a column asked twice or a key that is no symbol key makes it `None`, then the closing key.
    pub fn tail_units_in_columns(&self, tail: &[char]) -> Option<Vec<Unit>> {
        let (closing, body) = tail.split_last()?;
        let mut cols: [Option<char>; 3] = [None; 3];
        for &k in body {
            let (c, _) = Layout::Standard.symbol_of(k)?;
            if cols[c].replace(k).is_some() {
                return None;
            }
        }
        let sorted: Vec<char> = cols.iter().flatten().chain(std::iter::once(closing)).copied().collect();
        self.tail_units(&sorted)
    }
}

/// One way to read the buffer: the first `m` keys are digits, the next `t` keys (plus an extra key, when there is one) are the tail.
pub struct Split {
    pub m: usize,
    pub t: usize,
    pub score: f64,
}

/// What one scoring call decided. `trace` of the engine keeps these.
pub struct Decision {
    pub splits: Vec<Split>,
    pub pick: Option<usize>,
}

/// The scorer over a table, a model, the prediction index and the legal tails.
pub struct Scorer<'a> {
    pub table: &'a DigitTable,
    pub lm: &'a Lm,
    pub idx: &'a Index<'a>,
    pub legal: &'a Legal,
    /// The profile's scoring: its Kneser-Ney beta is the one `lp_z` blends with (`Lm::unigram_back`).
    pub cfg: Scoring,
}

fn log10_sum(terms: impl Iterator<Item = f64>) -> Option<f64> {
    let mut s = 0.0;
    for (i, x) in terms.enumerate() {
        if x <= 0.0 {
            return None;
        }
        s = if i == 0 { x.log10() } else { s + x.log10() };
    }
    Some(s)
}

impl Scorer<'_> {
    /// lpD (docs/spec/english-mixed.md §7.3): 0 with no digit key; else log P(D | C) + log P_len(d | ended) + the log P_ch of the first d keys, in key order.
    pub(crate) fn lp_d(&self, c: &str, dkeys: &[char], ended: bool) -> f64 {
        if dkeys.is_empty() {
            return 0.0;
        }
        let terms = [self.table.p_d_given(c), self.table.p_len(dkeys.len(), ended)].into_iter().chain(dkeys.iter().map(|&k| self.table.p_ch(k)));
        log10_sum(terms).unwrap_or(f64::NEG_INFINITY)
    }

    /// lpZ: log of the sum, over the words compatible with the tail (candidate order), of P(w | C') with C' the word context `c` (no digit
    /// before: the model's probability, not `word_by_id`'s blend) or the digit context (c, D).
    pub(crate) fn lp_z(&self, c: &str, digit_ctx: bool, units: &[Unit]) -> f64 {
        let ctx = self.lm.context_of(c);
        let mut tot = 0.0;
        for (w, lp, wid) in compat_words(self.idx, units) {
            let pb = self.lm.unigram_back(self.cfg, wid, lp);
            tot += if digit_ctx { self.table.p_w_given_vd(w, c, pb) } else { self.lm.prob_back(ctx, wid, pb) };
        }
        if tot > 0.0 { tot.log10() } else { f64::NEG_INFINITY }
    }

    /// The splits of `buf` (the keys in the buffer, the key just pressed included when it is an alphabet key) and the pick.
    /// `extra`: `None`, `Some(' ')`, or a non-alphabet symbol key (it always belongs to the tail). `emitted`: keys already flushed (digits).
    /// `prev_d`: digits displayed before this key (`None` at a run's first key). `closing`: the key just pressed is a tone key or Space,
    /// so a tail that ends in it closes a syllable. `complete`: tails that end in a tone key are allowed (false when only re-scoring the display).
    ///
    /// `reorder` is `Some((rho, rho_sym))` when the reversal guard is on (docs/spec/english-mixed.md §7.3): at a closing key (the tail ends in a tone key
    /// or Space) a tail that is not a legal tail in the order typed is tried once more with its symbol keys in column order (each column at most once,
    /// the closing key last); a legal result is one more split, scored `rho` lower (`rho_sym` when the run starts with `.` `/` `-`).
    #[allow(clippy::too_many_arguments)]
    pub fn decide(&self, c: &str, emitted: &[char], buf: &[char], extra: Option<char>, prev_d: Option<usize>, closing: bool, complete: bool, reorder: Option<(f64, f64)>) -> Decision {
        let n = buf.len();
        let boundary = extra == Some(' ');
        // (m, t, units)
        let mut cands: Vec<(usize, usize, Option<Vec<Unit>>, f64)> = Vec::new();
        for m in (0..=n).rev() {
            let tail: Vec<char> = buf[m..].iter().copied().chain(extra).collect();
            if tail.is_empty() {
                cands.push((m, 0, None, 0.0));
            } else if tail.len() <= MAX_BUF {
                let ends_closed = tone_mark_of_key(*tail.last().unwrap_or(&'x')).is_some() || tail.last() == Some(&' ');
                if ends_closed && !complete {
                    continue;
                }
                if let Some(units) = self.legal.tail_units(&tail) {
                    cands.push((m, tail.len(), Some(units), 0.0));
                } else if let (Some((rho, rho_sym)), true) = (reorder, ends_closed) {
                    if let Some(units) = self.legal.tail_units_in_columns(&tail) {
                        let sym = matches!(emitted.first().or(buf.first()), Some('.' | '/' | '-'));
                        cands.push((m, tail.len(), Some(units), if sym { rho_sym } else { rho }));
                    }
                }
            }
        }
        if boundary {
            cands.push((n, 0, None, 0.0)); // all digits, the space is a boundary (the t = 0 split of buf + space)
        }
        if cands.is_empty() {
            return Decision { splits: Vec::new(), pick: None };
        }
        let mut splits = Vec::with_capacity(cands.len());
        for (m, t, units, rho) in &cands {
            let digits: Vec<char> = emitted.iter().chain(&buf[..*m]).copied().collect();
            let lpd = self.lp_d(c, &digits, *t > 0 || boundary);
            let lpz = match units {
                Some(u) if *t > 0 => self.lp_z(c, !digits.is_empty(), u),
                _ => 0.0,
            };
            splits.push(Split { m: *m, t: *t, score: lpd + lpz - rho });
        }
        let pick = Self::choose(&splits, emitted.len(), prev_d, closing, closing || extra.is_some());
        Decision { splits, pick: Some(pick) }
    }

    /// The rejoin test of the digit tokens a run just made (docs/spec/english-mixed.md §7.4 "待歸鍵"), at the closing key of the syllable typed right behind
    /// them, with nothing of the engine's state: `ctx` is the run's context, `before` the run's digit keys before the late keys, `keys` the late keys
    /// (one or two, typed order), `pend_keys` the unfinished syllable's keys in column order, `closing` its tone key or `' '`. For each suffix of `keys`
    /// (the longer first) whose keys fill columns `pend_keys` leaves free and that, with the closing key, is a legal complete syllable: the score
    /// `S_z` (`lpZ + THETA - rho` when no digit stays in front, else `lpD(digits before, ended) + lpZ(digit context) - rho`; `rho_sym` when `before`
    /// plus `keys` start with `.` `/` `-`); the best (the longer one on a tie) is `take`. `merge`: the syllable as typed (late keys not taken) is no
    /// complete Han syllable (`s_d` is `None`), or `s_z` is strictly higher than `s_d` = `lpD(all the digit keys, ended) + lpZ(as typed, digit context)`.
    /// `(None, false, None, None)` when no suffix qualifies.
    #[allow(clippy::too_many_arguments)]
    pub fn late_decision(&self, ctx: &str, before: &[char], keys: &[char], pend_keys: &[char], closing: char, rho: f64, rho_sym: f64) -> (Option<usize>, bool, Option<f64>, Option<f64>) {
        let k = keys.len();
        let rho = if matches!(before.first().or(keys.first()), Some('.' | '/' | '-')) { rho_sym } else { rho };
        let mut cols0: [Option<char>; 3] = [None; 3];
        for &key in pend_keys {
            if let Some((c, _)) = Layout::Standard.symbol_of(key) {
                cols0[c] = Some(key);
            }
        }
        let mut best: Option<(usize, f64)> = None;
        for j in (1..=k).rev() {
            let mut cols = cols0;
            let mut ok = true;
            for &key in &keys[k - j..] {
                match Layout::Standard.symbol_of(key) {
                    Some((c, _)) if cols[c].is_none() => cols[c] = Some(key),
                    _ => ok = false,
                }
            }
            let z_keys: Vec<char> = cols.iter().flatten().copied().chain([closing]).collect();
            let Some(u_z) = self.legal.tail_units(&z_keys).filter(|_| ok) else { continue };
            let digits: Vec<char> = before.iter().chain(&keys[..k - j]).copied().collect();
            let s_z = if digits.is_empty() { self.lp_z(ctx, false, &u_z) + THETA } else { self.lp_d(ctx, &digits, true) + self.lp_z(ctx, true, &u_z) } - rho;
            if best.is_none_or(|(_, b)| s_z > b) {
                best = Some((j, s_z));
            }
        }
        let Some((take, s_z)) = best else { return (None, false, None, None) };
        let d_keys: Vec<char> = cols0.iter().flatten().copied().chain([closing]).collect();
        match self.legal.tail_units(&d_keys) {
            Some(u_d) => {
                let all: Vec<char> = before.iter().chain(keys).copied().collect();
                let s_d = self.lp_d(ctx, &all, true) + self.lp_z(ctx, true, &u_d);
                (Some(take), s_z > s_d, Some(s_z), Some(s_d))
            }
            None => (Some(take), true, Some(s_z), None),
        }
    }

    /// The pick among scored splits (docs/spec/english-mixed.md §7.3). `final`: the key decides on the spot (a closing key, or a Zhuyin key
    /// outside the alphabet), so a pure-Zhuyin split with a tail gets theta. `closing`: theta against the all-digit split; else the best
    /// effective score, ties keeping the displayed digit count, else the larger tail.
    fn choose(out: &[Split], emitted: usize, prev_d: Option<usize>, closing: bool, is_final: bool) -> usize {
        let eff: Vec<f64> = out.iter().map(|s| s.score + if is_final && s.t > 0 && emitted + s.m == 0 { THETA } else { 0.0 }).collect();
        let zh: Vec<usize> = (0..out.len()).filter(|&i| out[i].t > 0).collect();
        let dg: Vec<usize> = (0..out.len()).filter(|&i| out[i].t == 0).collect();
        if closing && !zh.is_empty() && !dg.is_empty() {
            let mut best = zh[0];
            for &i in &zh[1..] {
                if eff[i] > eff[best] || (eff[i] == eff[best] && out[i].t > out[best].t) {
                    best = i;
                }
            }
            return if out[dg[0]].score > eff[best] { dg[0] } else { best };
        }
        let top = eff.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        let tied: Vec<usize> = (0..out.len()).filter(|&i| eff[i] == top).collect();
        if let Some(&i) = tied.iter().find(|&&i| prev_d.is_some_and(|p| emitted + out[i].m == p)) {
            return i;
        }
        let mut best = tied[0];
        for &i in &tied[1..] {
            if out[i].t > out[best].t {
                best = i;
            }
        }
        best
    }
}
