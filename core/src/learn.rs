//! S4 learning from candidate-window re-picks (docs/contracts/s4-learning.md §1).
//!
//! Interface commit (contract §8 "介面先行"): `context_key` and the constants are final; the store's
//! method bodies are the executor's. Records hold only the five fields of §4: context key, reading,
//! word, weight, day.

/// Han characters kept as context (§1.1).
pub const MAX_CONTEXT: usize = 2;
/// Context key when no Han character precedes the span (sentence start, after punctuation or ASCII,
/// or no readable left context). Distinct from the global key "".
pub const SENTINEL: &str = "^";
/// The global key (§1.3): created only after ≥ 2 distinct full keys learned the same (reading, word).
pub const GLOBAL: &str = "";
/// Half-life in days (user decision 2026-10-05): one teach stays effective about two weeks; words in
/// regular use keep being refreshed.
pub const HALF_LIFE_DAYS: f64 = 14.0;
/// A record boosts decoding only from this weight up (§1.4).
pub const ACTIVE: f64 = 0.5;
/// Records decayed below this are dropped on load and save (§1.3): about 60 days unused, so a
/// repeated teach still has room to accumulate, while long-dead entries do not linger on disk.
pub const PRUNE_FLOOR: f64 = 0.05;
/// At most this many records; the lowest weights go first (§1.3).
pub const CAPACITY: usize = 50_000;

fn is_han(c: char) -> bool {
    matches!(c as u32,
        0x3400..=0x4DBF | 0x4E00..=0x9FFF | 0xF900..=0xFAFF | 0x20000..=0x2A6DF | 0x2A700..=0x2EBEF
        | 0x30000..=0x3134F | 0x2F800..=0x2FA1F | 0x3007)
}

/// §1.1: the last ≤ 2 consecutive Han characters at the end of `prefix`, stopping at any non-Han
/// character; `SENTINEL` when there is none. Shared by learning and decoding.
pub fn context_key(prefix: &str) -> String {
    let tail: Vec<char> = prefix.chars().rev().take_while(|&c| is_han(c)).take(MAX_CONTEXT).collect();
    if tail.is_empty() {
        SENTINEL.to_string()
    } else {
        tail.into_iter().rev().collect()
    }
}

/// One learned record (§1.3, §4). `day` is the local calendar day as days since 1970-01-01.
#[derive(Clone, Debug, PartialEq)]
pub struct Record {
    pub context: String,
    pub reading: Vec<String>,
    pub word: String,
    pub weight: f64,
    pub day: i64,
}

/// In-memory learning model (§1). Bodies are the executor's.
#[derive(Default)]
pub struct Learner {
    records: Vec<Record>,
}

impl Learner {
    pub fn from_records(records: Vec<Record>) -> Learner {
        Learner { records }
    }
    pub fn records(&self) -> &[Record] {
        &self.records
    }
    /// One re-pick at commit (§1.2–§1.3): `context` is a full key from `context_key`, `displaced` the
    /// word shown before the pick (its weight halves if it had a record under this key).
    pub fn teach(&mut self, _context: &str, _reading: &[String], _word: &str, _displaced: &str, _today: i64) {
        todo!("S4 executor: §1.3")
    }
    /// Words with an active record for `reading` reachable from `context` by the §1.1 lookup order
    /// (exact, last character, global), with their decayed weight.
    pub fn lookup(&self, _context: &str, _reading: &[String], _today: i64) -> Vec<(&str, f64)> {
        todo!("S4 executor: §1.1, §1.4")
    }
    /// §1.5: removes every record of (reading, word) under every key, including SENTINEL and GLOBAL.
    pub fn forget(&mut self, _reading: &[String], _word: &str) {
        todo!("S4 executor: §1.5")
    }
    /// §1.3: decay to `today`, drop below PRUNE_FLOOR, then trim to CAPACITY.
    pub fn prune(&mut self, _today: i64) {
        todo!("S4 executor: §1.3")
    }
    pub fn clear(&mut self) {
        self.records.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn context_key_is_the_han_tail() {
        assert_eq!(context_key("管把"), "管把");
        assert_eq!(context_key("我們今天"), "今天");
        assert_eq!(context_key("他"), "他");
        assert_eq!(context_key(""), SENTINEL);
        assert_eq!(context_key("ab\t中"), "中");
        assert_eq!(context_key("x\r國"), "國");
        assert_eq!(context_key("「中」"), SENTINEL);
        assert_eq!(context_key("好😀"), SENTINEL);
        assert_eq!(context_key("吃飯了。"), SENTINEL);
        assert_ne!(SENTINEL, GLOBAL);
    }
}
