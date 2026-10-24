//! English before Chinese (docs/contracts/english-next.md): the count table `english-next.tsv` of the Chinese words that follow an
//! English word. Rows: `E\te\tN(e)` and `W\te\tw\tN(e->w)`; `#` lines and blank lines are skipped. Absent or malformed means the
//! feature is off: `parse` returns `None`.

use std::collections::{HashMap, HashSet};

/// gamma of P(w | e) (english-next.md 2.2), the same as the digit context's.
const GAMMA: f64 = 1.0;
/// The letters of an English word e (english-next.md 2.1).
const LETTERS: std::ops::RangeInclusive<usize> = 2..=32;

/// What the table holds for one English word: N(e) and the words that followed it.
pub struct Followers {
    n: u64,
    w: HashMap<String, u64>,
}

impl Followers {
    /// (N(e->w) + gamma P(w)) / (N(e) + gamma); `pw` is the model's back-off probability of w.
    pub fn p_w_given_e(&self, w: &str, pw: f64) -> f64 {
        (self.w.get(w).copied().unwrap_or(0) as f64 + GAMMA * pw) / (self.n as f64 + GAMMA)
    }

    pub fn contains(&self, w: &str) -> bool {
        self.w.contains_key(w)
    }

    pub fn words(&self) -> impl Iterator<Item = &str> {
        self.w.keys().map(String::as_str)
    }
}

pub struct EnglishTable(HashMap<String, Followers>);

impl EnglishTable {
    pub fn parse(text: &str) -> Option<EnglishTable> {
        let mut t: HashMap<String, Followers> = HashMap::new();
        let mut ws: Vec<(&str, &str, u64)> = Vec::new();
        for line in text.split('\n').filter(|l| !l.is_empty() && !l.starts_with('#')) {
            let f: Vec<&str> = line.split('\t').collect();
            let num = |i: usize| f.get(i).and_then(|x| x.parse::<u64>().ok());
            match *f.first()? {
                "E" => {
                    t.insert(f.get(1)?.to_string(), Followers { n: num(2).filter(|&n| n > 0)?, w: HashMap::new() });
                }
                "W" => ws.push((f.get(1)?, f.get(2)?, num(3)?)),
                _ => return None,
            }
        }
        for (e, w, n) in ws {
            t.get_mut(e)?.w.insert(w.to_string(), n);
        }
        (!t.is_empty()).then_some(EnglishTable(t))
    }

    /// The row of the English word that `text` ends with (english-next.md 2.1): at most one half-width space is dropped, then 2 to 32
    /// ASCII letters not preceded by an ASCII letter or digit, lowercased; `None` when the table has no `E` row for it.
    pub fn lookup(&self, text: &str) -> Option<&Followers> {
        let t = text.strip_suffix(' ').unwrap_or(text);
        let head = t.trim_end_matches(|c: char| c.is_ascii_alphabetic());
        let word = &t[head.len()..];
        if !LETTERS.contains(&word.len()) || head.ends_with(|c: char| c.is_ascii_digit()) {
            return None;
        }
        self.0.get(&word.to_ascii_lowercase())
    }

    /// Every word any row holds: what the word-reading cache is built for.
    pub fn all_words(&self) -> HashSet<&str> {
        self.0.values().flat_map(Followers::words).collect()
    }
}
