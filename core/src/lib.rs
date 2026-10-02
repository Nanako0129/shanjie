//! S0 core: port of reference/proto/ime.py (lexicon, to_syllables, beam decode, learners).
//! R2 (contract section 8): errors carry a kind and a length only, never input text.

pub mod eval;

use std::collections::HashMap;
use std::fmt;

pub const BEAM: usize = 32;
pub const PER_KEY: usize = 12;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Error {
    BadScore { token_len: usize },
    EmptyLexicon,
    NoPath { len: usize },
    MissingSeparator { line_len: usize },
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
        }
    }
}
impl std::error::Error for Error {}

pub type Syls = Vec<String>;

pub struct Lexicon {
    pub by_reading: HashMap<Syls, Vec<(String, f64)>>,
    pub by_word: HashMap<String, (Syls, f64)>,
    pub max_len: usize,
}

impl Lexicon {
    pub fn parse(text: &str) -> Result<Lexicon, Error> {
        let mut by_reading: HashMap<Syls, Vec<(String, f64)>> = HashMap::new();
        let mut by_word: HashMap<String, (Syls, f64)> = HashMap::new();
        for line in text.lines() {
            if matches!(line.chars().next(), Some('#') | Some('_')) {
                continue;
            }
            let parts: Vec<&str> = line.split_whitespace().collect();
            if parts.len() != 3 {
                continue;
            }
            let syls: Syls = parts[0].split('-').map(String::from).collect();
            let word = parts[1];
            if word.chars().count() != syls.len() {
                continue;
            }
            let score: f64 = parts[2]
                .parse()
                .map_err(|_| Error::BadScore { token_len: parts[2].chars().count() })?;
            by_reading.entry(syls.clone()).or_default().push((word.to_string(), score));
            match by_word.get(word) {
                Some((_, s)) if !(score > *s) => {}
                _ => {
                    by_word.insert(word.to_string(), (syls, score));
                }
            }
        }
        // Vec::sort_by is stable: equal scores keep file order, as in Python.
        for v in by_reading.values_mut() {
            v.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        }
        let max_len = by_reading.keys().map(|k| k.len()).max().ok_or(Error::EmptyLexicon)?;
        Ok(Lexicon { by_reading, by_word, max_len })
    }

    /// Best word segmentation as (start, end) char spans; strict `>` keeps the first best.
    fn segment_spans(&self, chars: &[char]) -> Option<Vec<(usize, usize)>> {
        let n = chars.len();
        let mut best = vec![(f64::NEG_INFINITY, 0usize); n + 1];
        best[0] = (0.0, 0);
        for i in 1..=n {
            for l in 1..=self.max_len.min(i) {
                let w: String = chars[i - l..i].iter().collect();
                if let Some((_, ws)) = self.by_word.get(&w) {
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
            out.extend(self.by_word[&w].0.iter().cloned());
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
        self.by_reading[key][0].1
    }
    /// dict(by_reading[key])[word]: last duplicate wins.
    fn word_score(&self, key: &[String], word: &str) -> f64 {
        self.by_reading[key]
            .iter()
            .rev()
            .find(|(w, _)| w == word)
            .expect("learner word missing from reading")
            .1
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
    let n = syls.len();
    // hyps[i]: (score, surface, words)
    let mut hyps: Vec<Vec<(f64, String, Vec<String>)>> = vec![Vec::new(); n + 1];
    hyps[0].push((0.0, String::new(), Vec::new()));
    for i in 1..=n {
        // Insertion-ordered map: index by surface, replacement keeps the slot (Python dict).
        let mut cand: Vec<(f64, String, Vec<String>)> = Vec::new();
        let mut idx: HashMap<String, usize> = HashMap::new();
        for l in 1..=lex.max_len.min(i) {
            let key = &syls[i - l..i];
            let Some(entries) = lex.by_reading.get(key) else { continue };
            if hyps[i - l].is_empty() {
                continue;
            }
            for (word, lp) in entries.iter().take(PER_KEY) {
                for (s, surf, ws) in &hyps[i - l] {
                    let prev = ws.last().map(String::as_str).unwrap_or("<s>");
                    let sc = (s + lp) + learner.bonus(prev, key, word);
                    let surface = format!("{surf}{word}");
                    match idx.get(&surface) {
                        Some(&p) if !(sc > cand[p].0) => {}
                        found => {
                            let mut nw = ws.clone();
                            nw.push(word.clone());
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
        cand.truncate(BEAM);
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
