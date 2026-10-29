//! English before Chinese (docs/contracts/english-next.md): the count table `english-next.tsv` of the Chinese words that follow an
//! English word. Rows: `E\te\tN(e)` and `W\te\tw\tN(e->w)`; `#` lines and blank lines are skipped. Absent means the feature is off; a table
//! that is there but does not parse is an error (`ParseError`), never a silent off.

use std::collections::{HashMap, HashSet};

/// gamma of P(w | e) (english-next.md 2.2), the same as the digit context's.
const GAMMA: f64 = 1.0;

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

/// Why a table was refused: the number of the first bad line (1-based) and the rule; never the text (R2).
#[derive(Debug, PartialEq, Eq)]
pub enum ParseError {
    /// Line `n` is not an `E`/`W` row, has a wrong field count or a count that is not a positive number, repeats an `E` row, or names a `W` row's e that has no `E` row before it.
    BadRow(usize),
    /// Line `n` is the `E` row of an e whose N(e) is smaller than the sum of its `W` counts.
    TooSmall(usize),
    /// No row at all.
    Empty,
}

impl std::fmt::Display for ParseError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ParseError::BadRow(n) => write!(f, "english-next table: bad row at line {n}"),
            ParseError::TooSmall(n) => write!(f, "english-next table: N(e) at line {n} is smaller than the sum of its W counts"),
            ParseError::Empty => write!(f, "english-next table: no rows"),
        }
    }
}

impl EnglishTable {
    /// `E\te\tN(e)` rows (N(e) > 0) and `W\te\tw\tN(e->w)` rows (count > 0, after their `E` row); a trailing `\r` is dropped. N(e) must be at least
    /// the sum of the e's W counts (a word's events are among the e's events).
    pub fn parse(text: &str) -> Result<EnglishTable, ParseError> {
        let mut t: HashMap<String, (Followers, usize)> = HashMap::new();
        for (i, line) in text.split('\n').enumerate() {
            let line = line.strip_suffix('\r').unwrap_or(line);
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let (bad, f) = (ParseError::BadRow(i + 1), line.split('\t').collect::<Vec<_>>());
            let num = |k: usize| f.get(k).and_then(|x| x.parse::<u64>().ok()).filter(|&n| n > 0);
            match f.as_slice() {
                ["E", e, _] => {
                    let fol = Followers { n: num(2).ok_or(bad)?, w: HashMap::new() };
                    if t.insert(e.to_string(), (fol, i + 1)).is_some() {
                        return Err(ParseError::BadRow(i + 1));
                    }
                }
                ["W", e, w, _] => {
                    let n = num(3).ok_or(ParseError::BadRow(i + 1))?;
                    t.get_mut(*e).ok_or(ParseError::BadRow(i + 1))?.0.w.insert(w.to_string(), n);
                }
                _ => return Err(bad),
            }
        }
        if let Some((_, line)) = t.values().find(|(f, _)| f.w.values().sum::<u64>() > f.n) {
            return Err(ParseError::TooSmall(*line));
        }
        if t.is_empty() {
            return Err(ParseError::Empty);
        }
        Ok(EnglishTable(t.into_iter().map(|(e, (f, _))| (e, f)).collect()))
    }

    /// The row of the lower-cased English word `e` (the English token the prediction start follows, english-next.md 2.1).
    pub fn get(&self, e: &str) -> Option<&Followers> {
        self.0.get(e)
    }

    /// Every word any row holds: what the word-reading cache is built for.
    pub fn all_words(&self) -> HashSet<&str> {
        self.0.values().flat_map(Followers::words).collect()
    }
}
