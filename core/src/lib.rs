//! S0 core: port of reference/proto/ime.py (lexicon, to_syllables, beam decode, learners).
//! R2 (contract section 8): errors carry a kind and a length only, never input text.

pub mod engine;
pub mod eval;
pub mod ffi;

use std::collections::HashMap;
use std::fmt;

pub const BEAM: usize = 32;
pub const PER_KEY: usize = 12;
/// S1 candidate path (PLAN S1): 64 candidates need a beam of at least 64. S0 BEAM stays for `unigram`/golden.
pub const BEAM_S1: usize = 64;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Error {
    BadScore { token_len: usize },
    EmptyLexicon,
    NoPath { len: usize },
    MissingSeparator { line_len: usize },
    BadOverlayRow { line_len: usize },
    OverlayDuplicate { word_len: usize },
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::BadScore { token_len } => write!(f, "bad score (token length {token_len})"),
            Error::EmptyLexicon => write!(f, "empty lexicon"),
            Error::NoPath { len } => write!(f, "no decode path (input length {len})"),
            Error::MissingSeparator { line_len } => {
                write!(f, "missing separator (line length {line_len})")
            }
            Error::BadOverlayRow { line_len } => write!(f, "bad overlay row (line length {line_len})"),
            Error::OverlayDuplicate { word_len } => {
                write!(f, "overlay duplicates a base entry (word length {word_len})")
            }
        }
    }
}
impl std::error::Error for Error {}

pub type Syls = Vec<String>;

/// Compact lexicon, layout in docs/contracts/l.md. Syllables are interned ids; readings are sorted
/// id sequences with contiguous entry ranges; words live in one string pool.
pub struct Lexicon {
    pub max_len: usize,
    syl_ids: HashMap<String, u32>,
    syl_names: Vec<String>,
    key_pool: Vec<u32>,
    readings: Vec<Reading>,
    ents: Vec<Ent>,
    words: String,
    /// Entry positions of each distinct word's by_word winner, sorted by word bytes.
    by_word: Vec<u32>,
}

#[derive(Clone, Copy)]
struct Reading {
    key_off: u32,
    key_len: u32,
    start: u32,
}

#[derive(Clone, Copy)]
struct Ent {
    off: u32,
    len: u32,
    score: f64,
}

/// File-order row while parsing: an `Ent` plus where its reading ids sit in the key pool.
struct Raw {
    ent: Ent,
    key_off: u32,
    key_len: u32,
}

#[derive(Default)]
struct Builder {
    syl_ids: HashMap<String, u32>,
    syl_names: Vec<String>,
    key_pool: Vec<u32>,
    raw: Vec<Raw>,
    words: String,
}

impl Builder {
    fn push(&mut self, reading: &str, word: &str, score: f64) {
        let key_off = self.key_pool.len() as u32;
        for s in reading.split('-') {
            let id = match self.syl_ids.get(s) {
                Some(&i) => i,
                None => {
                    let i = self.syl_names.len() as u32;
                    self.syl_names.push(s.to_string());
                    self.syl_ids.insert(s.to_string(), i);
                    i
                }
            };
            self.key_pool.push(id);
        }
        let ent = Ent { off: self.words.len() as u32, len: word.len() as u32, score };
        self.words.push_str(word);
        self.raw.push(Raw { ent, key_off, key_len: self.key_pool.len() as u32 - key_off });
    }
}

fn overlay_row(line: &str) -> Option<(&str, &str, f64)> {
    let mut it = line.split('\t');
    let (Some(r), Some(w), Some(s), Some(_), None) = (it.next(), it.next(), it.next(), it.next(), it.next()) else {
        return None;
    };
    Some((r, w, s.parse().ok()?))
}

impl Lexicon {
    pub fn parse(text: &str) -> Result<Lexicon, Error> {
        Self::parse_with(text, None)
    }

    /// Base parse, then overlay rows `reading\tword\tscore\ttag` appended in file order, then one stable sort.
    pub fn parse_with(text: &str, overlay: Option<&str>) -> Result<Lexicon, Error> {
        let mut b = Builder::default();
        for line in text.lines() {
            if matches!(line.chars().next(), Some('#') | Some('_')) {
                continue;
            }
            let mut it = line.split_whitespace();
            let (Some(reading), Some(word), Some(score), None) = (it.next(), it.next(), it.next(), it.next()) else {
                continue;
            };
            if word.chars().count() != reading.split('-').count() {
                continue;
            }
            let score: f64 = score.parse().map_err(|_| Error::BadScore { token_len: score.chars().count() })?;
            b.push(reading, word, score);
        }
        let base_n = b.raw.len();
        // A bad overlay row stops parsing but is reported only if no earlier row is a duplicate.
        let mut bad_row = None;
        for line in overlay.unwrap_or("").lines() {
            match overlay_row(line) {
                Some((r, w, s)) => b.push(r, w, s),
                None => {
                    bad_row = Some(Error::BadOverlayRow { line_len: line.chars().count() });
                    break;
                }
            }
        }
        b.finish(base_n, bad_row)
    }
}

impl Builder {
    fn finish(self, base_n: usize, bad_row: Option<Error>) -> Result<Lexicon, Error> {
        let Builder { syl_ids, syl_names, key_pool, raw, words } = self;
        let n = raw.len();
        let key = |f: u32| {
            let r = &raw[f as usize];
            &key_pool[r.key_off as usize..(r.key_off + r.key_len) as usize]
        };
        let word = |f: u32| {
            let e = &raw[f as usize].ent;
            &words[e.off as usize..(e.off + e.len) as usize]
        };
        // by_word: per distinct word, the first row in file order holding the maximum (strict `>` replaces).
        let mut widx: Vec<u32> = (0..n as u32).collect();
        widx.sort_by(|&a, &b| word(a).cmp(word(b)));
        let mut winners: Vec<u32> = Vec::new();
        let mut i = 0;
        while i < n {
            let mut cur = widx[i];
            let mut j = i + 1;
            while j < n && word(widx[j]) == word(cur) {
                if raw[widx[j] as usize].ent.score > raw[cur as usize].ent.score {
                    cur = widx[j];
                }
                j += 1;
            }
            winners.push(cur);
            i = j;
        }
        drop(widx);

        // Group rows by reading (stable: file order kept inside a group).
        let mut order: Vec<u32> = (0..n as u32).collect();
        order.sort_by(|&a, &b| key(a).cmp(key(b)));
        let mut dup: Option<u32> = None;
        let mut readings: Vec<Reading> = Vec::new();
        let mut fkeys: Vec<u32> = Vec::new();
        let mut max_len = 0;
        let mut s = 0;
        while s < n {
            let mut e = s + 1;
            while e < n && key(order[e]) == key(order[s]) {
                e += 1;
            }
            // An overlay row repeating any earlier row of the same reading (base or overlay) is a duplicate.
            for p in s..e {
                let f = order[p];
                if f as usize >= base_n && (s..p).any(|q| word(order[q]) == word(f)) {
                    dup = Some(dup.map_or(f, |d| d.min(f)));
                    break;
                }
            }
            order[s..e].sort_by(|&a, &b| {
                raw[b as usize].ent.score.partial_cmp(&raw[a as usize].ent.score).unwrap_or(std::cmp::Ordering::Equal)
            });
            let k = key(order[s]);
            readings.push(Reading { key_off: fkeys.len() as u32, key_len: k.len() as u32, start: s as u32 });
            fkeys.extend_from_slice(k);
            max_len = max_len.max(k.len());
            s = e;
        }
        if let Some(f) = dup {
            return Err(Error::OverlayDuplicate { word_len: word(f).chars().count() });
        }
        if let Some(e) = bad_row {
            return Err(e);
        }
        if n == 0 {
            return Err(Error::EmptyLexicon);
        }
        let mut pos = vec![0u32; n];
        for (p, &f) in order.iter().enumerate() {
            pos[f as usize] = p as u32;
        }
        let by_word = winners.iter().map(|&f| pos[f as usize]).collect();
        let ents = order.iter().map(|&f| raw[f as usize].ent).collect();
        let mut lex = Lexicon { max_len, syl_ids, syl_names, key_pool: fkeys, readings, ents, words, by_word };
        lex.words.shrink_to_fit();
        Ok(lex)
    }
}

impl Lexicon {
    fn word(&self, p: usize) -> &str {
        let e = &self.ents[p];
        &self.words[e.off as usize..(e.off + e.len) as usize]
    }
    fn key_of(&self, r: &Reading) -> &[u32] {
        &self.key_pool[r.key_off as usize..(r.key_off + r.key_len) as usize]
    }
    /// Syllable ids; unknown syllables map to u32::MAX, which no reading contains.
    fn ids(&self, syls: &[String]) -> Vec<u32> {
        syls.iter().map(|s| self.syl_ids.get(s).copied().unwrap_or(u32::MAX)).collect()
    }
    fn find(&self, ids: &[u32]) -> Option<usize> {
        self.readings.binary_search_by(|r| self.key_of(r).cmp(ids)).ok()
    }
    fn range(&self, r: usize) -> std::ops::Range<usize> {
        let end = self.readings.get(r + 1).map_or(self.ents.len(), |n| n.start as usize);
        self.readings[r].start as usize..end
    }
    fn reading_entries(&self, r: usize) -> impl Iterator<Item = (&str, f64)> {
        self.range(r).map(|p| (self.word(p), self.ents[p].score))
    }
    /// Entry position of the by_word winner.
    fn winner(&self, w: &str) -> Option<usize> {
        let i = self.by_word.binary_search_by(|&p| self.word(p as usize).cmp(w)).ok()?;
        Some(self.by_word[i] as usize)
    }
    fn reading_of(&self, p: usize) -> usize {
        self.readings.partition_point(|r| r.start as usize <= p) - 1
    }

    pub fn reading_count(&self) -> usize {
        self.readings.len()
    }
    /// Sorted (word, score) list of one reading; empty when the reading is unknown.
    pub fn entries(&self, key: &[String]) -> Vec<(&str, f64)> {
        self.find(&self.ids(key)).map(|r| self.reading_entries(r).collect()).unwrap_or_default()
    }
    /// by_word[word]: the winning reading and score.
    pub fn word_info(&self, word: &str) -> Option<(Syls, f64)> {
        let p = self.winner(word)?;
        let r = &self.readings[self.reading_of(p)];
        Some((self.key_of(r).iter().map(|&i| self.syl_names[i as usize].clone()).collect(), self.ents[p].score))
    }

    /// Best word segmentation as (start, end) char spans; strict `>` keeps the first best.
    fn segment_spans(&self, chars: &[char]) -> Option<Vec<(usize, usize)>> {
        let n = chars.len();
        let mut best = vec![(f64::NEG_INFINITY, 0usize); n + 1];
        best[0] = (0.0, 0);
        for i in 1..=n {
            for l in 1..=self.max_len.min(i) {
                let w: String = chars[i - l..i].iter().collect();
                if let Some(p) = self.winner(&w) {
                    let ws = self.ents[p].score;
                    if best[i - l].0 > f64::NEG_INFINITY {
                        let s = best[i - l].0 + ws;
                        if s > best[i].0 {
                            best[i] = (s, i - l);
                        }
                    }
                }
            }
        }
        if best[n].0 == f64::NEG_INFINITY {
            return None;
        }
        let (mut out, mut i) = (Vec::new(), n);
        while i > 0 {
            let j = best[i].1;
            out.push((j, i));
            i = j;
        }
        out.reverse();
        Some(out)
    }

    pub fn to_syllables(&self, text: &str) -> Option<Syls> {
        let chars: Vec<char> = text.chars().collect();
        let spans = self.segment_spans(&chars)?;
        let mut out = Vec::new();
        for (j, i) in spans {
            let w: String = chars[j..i].iter().collect();
            out.extend(self.word_info(&w).unwrap().0);
        }
        Some(out)
    }

    /// Words of the answer sentence with their readings (eval.py segment_words).
    pub fn segment_words(&self, text: &str) -> Option<Vec<(String, Syls)>> {
        let syls = self.to_syllables(text)?;
        let chars: Vec<char> = text.chars().collect();
        let mut pos = 0;
        Some(
            self.segment_spans(&chars)?
                .into_iter()
                .map(|(j, i)| {
                    let w: String = chars[j..i].iter().collect();
                    let r = (w, syls[pos..pos + (i - j)].to_vec());
                    pos += i - j;
                    r
                })
                .collect(),
        )
    }

    fn best_score(&self, key: &[String]) -> f64 {
        let r = self.find(&self.ids(key)).expect("reading missing");
        self.ents[self.range(r).start].score
    }
    /// dict(by_reading[key])[word]: last duplicate wins.
    fn word_score(&self, key: &[String], word: &str) -> f64 {
        let r = self.find(&self.ids(key)).expect("reading missing");
        self.range(r)
            .rev()
            .find(|&p| self.word(p) == word)
            .map(|p| self.ents[p].score)
            .expect("learner word missing from reading")
    }
}

pub trait Learner {
    fn bonus(&mut self, _prev: &str, _key: &[String], _word: &str) -> f64 {
        0.0
    }
    fn observe(&mut self, _prev: &str, _syls: &[String], _right: &str) {}
}

pub struct NoLearning;
impl Learner for NoLearning {}

pub struct GlobalBoost<'a> {
    lex: &'a Lexicon,
    top: HashMap<Syls, String>,
}
impl<'a> GlobalBoost<'a> {
    pub fn new(lex: &'a Lexicon) -> Self {
        GlobalBoost { lex, top: HashMap::new() }
    }
}
impl Learner for GlobalBoost<'_> {
    fn bonus(&mut self, _prev: &str, key: &[String], word: &str) -> f64 {
        if self.top.get(key).map(String::as_str) != Some(word) {
            return 0.0;
        }
        self.lex.best_score(key) - self.lex.word_score(key, word) + 0.01
    }
    fn observe(&mut self, _prev: &str, syls: &[String], right: &str) {
        self.top.insert(syls.to_vec(), right.to_string());
    }
}

pub struct ContextKeyed<'a> {
    lex: &'a Lexicon,
    mem: HashMap<(String, Syls), String>,
}
impl<'a> ContextKeyed<'a> {
    pub fn new(lex: &'a Lexicon) -> Self {
        ContextKeyed { lex, mem: HashMap::new() }
    }
}
impl Learner for ContextKeyed<'_> {
    fn bonus(&mut self, prev: &str, key: &[String], word: &str) -> f64 {
        if self.mem.get(&(prev.to_string(), key.to_vec())).map(String::as_str) != Some(word) {
            return 0.0;
        }
        self.lex.best_score(key) - self.lex.word_score(key, word) + 0.01
    }
    fn observe(&mut self, prev: &str, syls: &[String], right: &str) {
        self.mem.insert((prev.to_string(), syls.to_vec()), right.to_string());
    }
}

pub struct Promotion<'a> {
    base: GlobalBoost<'a>,
    count: HashMap<(Syls, String), u32>,
}
impl<'a> Promotion<'a> {
    const TEMP_BONUS: f64 = 0.5;
    const PROMOTE_AT: u32 = 3;
    pub fn new(lex: &'a Lexicon) -> Self {
        Promotion { base: GlobalBoost::new(lex), count: HashMap::new() }
    }
}
impl Learner for Promotion<'_> {
    fn bonus(&mut self, prev: &str, key: &[String], word: &str) -> f64 {
        let c = self.count.get(&(key.to_vec(), word.to_string())).copied().unwrap_or(0);
        if c >= Self::PROMOTE_AT {
            // Side effect kept from the prototype: bonus() itself promotes.
            self.base.top.insert(key.to_vec(), word.to_string());
            return self.base.bonus(prev, key, word);
        }
        if c > 0 { Self::TEMP_BONUS } else { 0.0 }
    }
    fn observe(&mut self, _prev: &str, syls: &[String], right: &str) {
        *self.count.entry((syls.to_vec(), right.to_string())).or_insert(0) += 1;
    }
}

/// Beam N-best, best first. Each hypothesis is (score, words).
pub fn decode(
    lex: &Lexicon,
    syls: &[String],
    learner: &mut dyn Learner,
) -> Result<Vec<(f64, Vec<String>)>, Error> {
    decode_beam(lex, syls, learner, BEAM)
}

pub fn decode_beam(
    lex: &Lexicon,
    syls: &[String],
    learner: &mut dyn Learner,
    beam: usize,
) -> Result<Vec<(f64, Vec<String>)>, Error> {
    let n = syls.len();
    let ids = lex.ids(syls);
    // hyps[i]: (score, surface, words)
    let mut hyps: Vec<Vec<(f64, String, Vec<String>)>> = vec![Vec::new(); n + 1];
    hyps[0].push((0.0, String::new(), Vec::new()));
    for i in 1..=n {
        // Insertion-ordered map: index by surface, replacement keeps the slot (Python dict).
        let mut cand: Vec<(f64, String, Vec<String>)> = Vec::new();
        let mut idx: HashMap<String, usize> = HashMap::new();
        for l in 1..=lex.max_len.min(i) {
            let key = &syls[i - l..i];
            let Some(r) = lex.find(&ids[i - l..i]) else { continue };
            if hyps[i - l].is_empty() {
                continue;
            }
            for (word, lp) in lex.reading_entries(r).take(PER_KEY) {
                for (s, surf, ws) in &hyps[i - l] {
                    let prev = ws.last().map(String::as_str).unwrap_or("<s>");
                    let sc = (s + lp) + learner.bonus(prev, key, word);
                    let surface = format!("{surf}{word}");
                    match idx.get(&surface) {
                        Some(&p) if !(sc > cand[p].0) => {}
                        found => {
                            let mut nw = ws.clone();
                            nw.push(word.to_string());
                            match found {
                                Some(&p) => cand[p] = (sc, surface, nw),
                                None => {
                                    idx.insert(surface.clone(), cand.len());
                                    cand.push((sc, surface, nw));
                                }
                            }
                        }
                    }
                }
            }
        }
        // Stable descending sort == heapq.nlargest tie behavior.
        cand.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
        cand.truncate(beam);
        hyps[i] = cand;
    }
    let out = std::mem::take(&mut hyps[n]);
    if out.is_empty() {
        return Err(Error::NoPath { len: n });
    }
    Ok(out.into_iter().map(|(s, _, w)| (s, w)).collect())
}

#[cfg(test)]
mod tests;
