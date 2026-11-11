//! Mixed words (docs/spec/english-mixed.md §5.7): the table `mixed-words.tsv` of words that are an English part followed by Chinese
//! (`CP值`, `SIM卡`). Rows: `english\treading\twriting\tcount`; `#` lines and blank lines are skipped. The table is optional: absent,
//! or with any row that breaks a rule, nothing is loaded and the engine is as it was without it.

use crate::learn::is_han;
use crate::Lexicon;
use std::collections::HashMap;

/// The most rows and bytes a table may have.
pub const MIXED_MAX_ROWS: usize = 5000;
pub const MIXED_MAX_BYTES: usize = 256 * 1024;
/// The most syllables the Chinese part of a row may have.
pub const MIXED_MAX_SYLLABLES: usize = 4;

/// (English part in lower case, the syllables of the Chinese part) -> the writing.
pub struct MixedTable {
    rows: HashMap<(String, Vec<String>), String>,
}

impl MixedTable {
    /// `None` when any row breaks a rule: the English part is `[a-z][a-z0-9]{1,15}`; the reading is 1 to 4 syllables the lexicon
    /// knows; the writing starts with the English part (any case) and goes on with as many Han characters as the reading has
    /// syllables; the count is a positive integer; no (English, reading) pair twice; within the limits above.
    pub fn parse(text: &str, lex: &Lexicon) -> Option<MixedTable> {
        if text.len() > MIXED_MAX_BYTES {
            return None;
        }
        let mut rows = HashMap::new();
        for line in text.lines().filter(|l| !l.is_empty() && !l.starts_with('#')) {
            let f: Vec<&str> = line.split('\t').collect();
            let [english, reading, writing, count] = f[..] else { return None };
            let e = english.as_bytes();
            let english_ok = (2..=16).contains(&e.len()) && e[0].is_ascii_lowercase() && e.iter().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit());
            let syls: Vec<String> = reading.split(' ').map(String::from).collect();
            let reading_ok = (1..=MIXED_MAX_SYLLABLES).contains(&syls.len()) && syls.iter().all(|s| lex.has_syllable(s));
            let writing_ok = writing.get(..english.len()).is_some_and(|head| head.eq_ignore_ascii_case(english)) && {
                let rest = &writing[english.len()..];
                rest.chars().count() == syls.len() && rest.chars().all(is_han)
            };
            let count_ok = count.parse::<u64>().is_ok_and(|n| n > 0) && count.bytes().all(|b| b.is_ascii_digit());
            if !(english_ok && reading_ok && writing_ok && count_ok) || rows.insert((english.to_string(), syls), writing.to_string()).is_some() {
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
        self.rows.get(&(english.to_ascii_lowercase(), reading.to_vec())).map(String::as_str)
    }

    /// Every row as (English part, reading, writing), for the check set's test.
    pub fn rows(&self) -> impl Iterator<Item = (&str, &[String], &str)> {
        self.rows.iter().map(|((e, r), w)| (e.as_str(), r.as_slice(), w.as_str()))
    }
}
