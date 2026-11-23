//! Mixed words (docs/spec/english-mixed.md §5.7): the table `mixed-words.tsv` of words that are an English part followed by Chinese
//! (`CP值`, `SIM卡`). Rows: `english\treading\twriting\tcount`, and optionally a fifth field, the mark `nopredict` (the row is left out of the
//! row of an open run, docs/spec/prediction.md §3.5; everything else about it is as without the mark); `#` lines and blank lines are skipped. The
//! table is optional: absent, or with any row that breaks a rule, nothing is loaded and the engine is as it was without it.

use crate::learn::is_han;
use crate::Lexicon;
use std::collections::HashMap;

/// The most rows and bytes a table may have.
pub const MIXED_MAX_ROWS: usize = 5000;
pub const MIXED_MAX_BYTES: usize = 256 * 1024;
/// The most syllables the Chinese part of a row may have.
pub const MIXED_MAX_SYLLABLES: usize = 4;

/// The fifth field of a row that is left out of the row of an open run (docs/spec/prediction.md §3.5).
pub const MIXED_NO_PREDICT: &str = "nopredict";

/// (English part in lower case, the syllables of the Chinese part) -> the writing, the count, and whether the row is marked `nopredict`.
pub struct MixedTable {
    rows: HashMap<(String, Vec<String>), (String, u64, bool)>,
}

impl MixedTable {
    /// `None` when any row breaks a rule: the English part is `[a-z][a-z0-9]{0,15}`; the reading is 1 to 4 syllables the lexicon
    /// knows; the writing starts with the English part (any case) and goes on with as many Han characters as the reading has
    /// syllables; the count is a positive integer; no (English, reading) pair twice; a fifth field,
    /// when there is one, is `nopredict`; within the limits above.
    pub fn parse(text: &str, lex: &Lexicon) -> Option<MixedTable> {
        if text.len() > MIXED_MAX_BYTES {
            return None;
        }
        let mut rows = HashMap::new();
        for line in text.lines().filter(|l| !l.is_empty() && !l.starts_with('#')) {
            let f: Vec<&str> = line.split('\t').collect();
            let (english, reading, writing, count, no_predict) = match f[..] {
                [e, r, w, n] => (e, r, w, n, false),
                [e, r, w, n, MIXED_NO_PREDICT] => (e, r, w, n, true),
                _ => return None,
            };
            let e = english.as_bytes();
            let english_ok = (1..=16).contains(&e.len()) && e[0].is_ascii_lowercase() && e.iter().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit());
            let syls: Vec<String> = reading.split(' ').map(String::from).collect();
            let reading_ok = (1..=MIXED_MAX_SYLLABLES).contains(&syls.len()) && syls.iter().all(|s| lex.has_syllable(s));
            let writing_ok = writing.get(..english.len()).is_some_and(|head| head.eq_ignore_ascii_case(english)) && {
                let rest = &writing[english.len()..];
                rest.chars().count() == syls.len() && rest.chars().all(is_han)
            };
            let n = count.parse::<u64>().ok().filter(|&n| n > 0 && count.bytes().all(|b| b.is_ascii_digit()));
            let (true, Some(n)) = (english_ok && reading_ok && writing_ok, n) else { return None };
            if rows.insert((english.to_string(), syls), (writing.to_string(), n, no_predict)).is_some() {
                return None;
            }
            if rows.len() > MIXED_MAX_ROWS {
                return None;
            }
        }
        Some(MixedTable { rows })
    }

    /// The writing of the English part `english` (any case) followed by exactly the syllables `reading`.
    pub fn get(&self, english: &str, reading: &[String]) -> Option<&str> {
        if reading.is_empty() || reading.len() > MIXED_MAX_SYLLABLES {
            return None;
        }
        self.rows.get(&(english.to_ascii_lowercase(), reading.to_vec())).map(|(w, ..)| w.as_str())
    }

    /// docs/spec/english-mixed.md §4.3 (the split at the closing key): some row of the English part `english` (any case) has a reading
    /// that starts with the syllable `first`.
    pub fn starts(&self, english: &str, first: &str) -> bool {
        let english = english.to_ascii_lowercase();
        self.rows.keys().any(|(e, r)| *e == english && r[0] == first)
    }

    /// docs/spec/prediction.md §3.5: the rows of the English part `english` (any case) that are not marked `nopredict`, one per writing (of several rows with one writing
    /// the largest count, then the smallest reading string), as (writing, reading, count) ordered by count, largest first, then by writing.
    pub fn of_english(&self, english: &str) -> Vec<(&str, &[String], u64)> {
        let english = english.to_ascii_lowercase();
        let mut by_writing: HashMap<&str, (&[String], u64)> = HashMap::new();
        for ((_, r), (w, n, _)) in self.rows.iter().filter(|((e, _), (_, _, no_predict))| *e == english && !no_predict) {
            let better = |old: &(&[String], u64)| *n > old.1 || (*n == old.1 && r.join(" ") < old.0.join(" "));
            if by_writing.get(w.as_str()).is_none_or(better) {
                by_writing.insert(w, (r, *n));
            }
        }
        let mut v: Vec<(&str, &[String], u64)> = by_writing.into_iter().map(|(w, (r, n))| (w, r, n)).collect();
        v.sort_by(|a, b| b.2.cmp(&a.2).then(a.0.cmp(b.0)));
        v
    }

    /// Every row as (English part, reading, writing), for the check set's test.
    pub fn rows(&self) -> impl Iterator<Item = (&str, &[String], &str)> {
        self.rows.iter().map(|((e, r), (w, ..))| (e.as_str(), r.as_slice(), w.as_str()))
    }
}
