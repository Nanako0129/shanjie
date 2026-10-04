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
/// Records decayed below this are dropped on load and on a full rewrite (§1.3): about 60 days unused, so a
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
/// No `Debug`: it holds learned words (s3a §6 R2, types holding input text).
#[derive(Clone, PartialEq)]
pub struct Record {
    pub context: String,
    pub reading: Vec<String>,
    pub word: String,
    pub weight: f64,
    pub day: i64,
}

/// Local calendar day as days since 1970-01-01 (the `Record::day` unit).
pub fn local_day() -> i64 {
    #[repr(C)]
    struct Tm {
        sec: i32,
        min: i32,
        hour: i32,
        mday: i32,
        mon: i32,
        year: i32,
        wday: i32,
        yday: i32,
        isdst: i32,
        gmtoff: i64,
        zone: *const std::ffi::c_char,
    }
    extern "C" {
        fn time(t: *mut i64) -> i64;
        fn localtime_r(t: *const i64, out: *mut Tm) -> *mut Tm;
    }
    // SAFETY: `time` accepts NULL; `localtime_r` fills `tm`, laid out as in <time.h> on macOS and glibc.
    let (t, off) = unsafe {
        let t = time(std::ptr::null_mut());
        let mut tm: Tm = std::mem::zeroed();
        let off = if localtime_r(&t, &mut tm).is_null() { 0 } else { tm.gmtoff };
        (t, off)
    };
    (t + off).div_euclid(86_400)
}

/// Weight of `r` on `today`: halves every `HALF_LIFE_DAYS`; a clock that went back never grows it.
fn decayed(r: &Record, today: i64) -> f64 {
    r.weight * 0.5f64.powf((today - r.day).max(0) as f64 / HALF_LIFE_DAYS)
}

fn reading_key(reading: &[String]) -> String {
    reading.join(" ")
}

/// In-memory learning model (§1). `index` maps a reading to its record positions so decoding can ask
/// "does this reading have any record" in one hash lookup.
#[derive(Default)]
pub struct Learner {
    records: Vec<Record>,
    index: std::collections::HashMap<String, Vec<usize>>,
}

impl Learner {
    pub fn from_records(records: Vec<Record>) -> Learner {
        let mut l = Learner { records, index: Default::default() };
        l.reindex();
        l
    }
    fn reindex(&mut self) {
        self.index.clear();
        for (i, r) in self.records.iter().enumerate() {
            self.index.entry(reading_key(&r.reading)).or_default().push(i);
        }
    }
    pub fn records(&self) -> &[Record] {
        &self.records
    }
    pub fn is_empty(&self) -> bool {
        self.records.is_empty()
    }
    /// True when any record (under any key) exists for `reading`.
    pub fn has_reading(&self, reading: &[String]) -> bool {
        !self.index.is_empty() && self.index.contains_key(&reading_key(reading))
    }
    /// Every word recorded for `reading` under any key (decoding enumerates these beyond PER_KEY).
    pub fn words_of(&self, reading: &[String]) -> Vec<&str> {
        let mut v: Vec<&str> = self
            .index
            .get(&reading_key(reading))
            .map(|ix| ix.iter().map(|&i| self.records[i].word.as_str()).collect())
            .unwrap_or_default();
        v.sort_unstable();
        v.dedup();
        v
    }
    fn find(&self, context: &str, reading: &[String], word: &str) -> Option<usize> {
        self.index
            .get(&reading_key(reading))?
            .iter()
            .copied()
            .find(|&i| self.records[i].context == context && self.records[i].word == word)
    }
    /// Adds `delta` to the decayed weight of (context, reading, word), creating the record if needed;
    /// returns the record as it now is.
    fn bump(&mut self, context: &str, reading: &[String], word: &str, today: i64, delta: f64) -> Record {
        match self.find(context, reading, word) {
            Some(i) => {
                let r = &mut self.records[i];
                r.weight = decayed(r, today) + delta;
                r.day = today;
                r.clone()
            }
            None => {
                self.index.entry(reading_key(reading)).or_default().push(self.records.len());
                let r = Record {
                    context: context.to_string(),
                    reading: reading.to_vec(),
                    word: word.to_string(),
                    weight: delta,
                    day: today,
                };
                self.records.push(r.clone());
                r
            }
        }
    }
    /// One re-pick at commit (§1.2–§1.3): `context` is a full key from `context_key`, `displaced` the
    /// word shown before the pick (its weight halves if it had a record under this key). Returns the
    /// records it changed, as they now are (displaced, taught, global; at most 3): what an append
    /// writes (§4).
    pub fn teach(&mut self, context: &str, reading: &[String], word: &str, displaced: &str, today: i64) -> Vec<Record> {
        let mut touched = Vec::with_capacity(3);
        if let Some(i) = self.find(context, reading, displaced) {
            let r = &mut self.records[i];
            r.weight = decayed(r, today) * 0.5;
            r.day = today;
            touched.push(r.clone());
        }
        touched.push(self.bump(context, reading, word, today, 1.0));
        // §1.3 globalize: ≥ 2 distinct full keys (SENTINEL counts) with an active record.
        let Some(ix) = self.index.get(&reading_key(reading)) else { return touched };
        let keys: std::collections::HashSet<&str> = ix
            .iter()
            .map(|&i| &self.records[i])
            .filter(|r| r.word == word && r.context != GLOBAL && decayed(r, today) >= ACTIVE)
            .map(|r| r.context.as_str())
            .collect();
        if keys.len() >= 2 {
            let prev = self.find(GLOBAL, reading, word).map(|i| self.records[i].clone());
            let have = prev.as_ref().map_or(0.0, |r| decayed(r, today));
            let now = self.bump(GLOBAL, reading, word, today, (1.0 - have).max(0.0));
            // A global already at full weight today is unchanged: nothing to append for it.
            if prev.as_ref() != Some(&now) {
                touched.push(now);
            }
        }
        touched
    }
    /// Words with an active record for `reading` reachable from `context` by the §1.1 lookup order
    /// (exact, last character, global), with their decayed weight. The first level that has any
    /// active record answers; a word found by several records of that level keeps the highest weight.
    pub fn lookup(&self, context: &str, reading: &[String], today: i64) -> Vec<(&str, f64)> {
        let Some(ix) = self.index.get(&reading_key(reading)) else { return Vec::new() };
        let last = context.chars().next_back().filter(|_| context != SENTINEL && context != GLOBAL);
        let levels: [&dyn Fn(&str) -> bool; 3] = [
            &|c| c == context,
            &|c| last.is_some_and(|l| c != SENTINEL && c != GLOBAL && c.chars().next_back() == Some(l)),
            &|c| c == GLOBAL,
        ];
        for level in levels {
            let mut hits: Vec<(&str, f64)> = Vec::new();
            for &i in ix {
                let r = &self.records[i];
                let w = decayed(r, today);
                if w < ACTIVE || !level(&r.context) {
                    continue;
                }
                match hits.iter_mut().find(|h| h.0 == r.word) {
                    Some(h) => h.1 = h.1.max(w),
                    None => hits.push((r.word.as_str(), w)),
                }
            }
            if !hits.is_empty() {
                hits.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal).then(a.0.cmp(b.0)));
                return hits;
            }
        }
        Vec::new()
    }
    /// Removes the records at positions `drop` with swap_remove, patching the index instead of
    /// rebuilding it: every learning Enter on a full store trims one record, and a rebuild of
    /// CAPACITY entries took about 5 ms of the 16 ms key budget (§6.14). Record order is not kept.
    fn remove_at(&mut self, mut drop: Vec<usize>) {
        // Highest first: whatever sits last is never a later victim.
        drop.sort_unstable_by(|a, b| b.cmp(a));
        for i in drop {
            let last = self.records.len() - 1;
            let key = reading_key(&self.records[i].reading);
            let bucket = self.index.get_mut(&key).expect("indexed");
            bucket.retain(|&j| j != i);
            if bucket.is_empty() {
                self.index.remove(&key);
            }
            if i != last {
                let moved = self.index.get_mut(&reading_key(&self.records[last].reading)).expect("indexed");
                *moved.iter_mut().find(|j| **j == last).expect("indexed") = i;
            }
            self.records.swap_remove(i);
        }
    }
    /// §1.5: removes every record of (reading, word) under every key, including SENTINEL and GLOBAL.
    pub fn forget(&mut self, reading: &[String], word: &str) {
        let drop = self.index.get(&reading_key(reading)).map_or(Vec::new(), |ix| {
            ix.iter().copied().filter(|&i| self.records[i].word == word).collect()
        });
        self.remove_at(drop);
    }
    /// §1.3: drop below PRUNE_FLOOR once decayed to `today`, then trim to CAPACITY (lowest first;
    /// among equal weights the later record goes first).
    pub fn prune(&mut self, today: i64) {
        let w: Vec<f64> = self.records.iter().map(|r| decayed(r, today)).collect();
        let (mut live, mut drop): (Vec<usize>, Vec<usize>) = (0..w.len()).partition(|&i| w[i] >= PRUNE_FLOOR);
        if live.len() > CAPACITY {
            // A partition, not a sort: on a full store a save trims about one record.
            live.select_nth_unstable_by(CAPACITY, |&a, &b| w[b].total_cmp(&w[a]).then(a.cmp(&b)));
            drop.extend_from_slice(&live[CAPACITY..]);
        }
        self.remove_at(drop);
    }
    pub fn clear(&mut self) {
        self.records.clear();
        self.index.clear();
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
