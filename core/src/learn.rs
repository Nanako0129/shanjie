//! S4 learning from candidate-window re-picks (docs/contracts/s4-learning.md §1).
//!
//! Interface commit (contract §8 "介面先行"): `context_key` and the constants are final; the store's
//! method bodies are the executor's. Records hold only the five fields of §4: context key, reading,
//! word, weight, day.

/// Han characters kept as context (§1.1).
pub const MAX_CONTEXT: usize = 2;
/// Context key when no Han character precedes the span (sentence start, after punctuation or ASCII,
/// or no readable left context). Distinct from the global key "". The context key of a span, never a record's:
/// learning and lookup replace it by `next_key` (§14, rule 8), and `learn_store` drops records that hold it.
pub const SENTINEL: &str = "^";
/// Prefix of the synthetic context keys (s4-learning §14, rule 8): at sentence start (`SENTINEL`) a word is learned
/// and looked up under `^>` plus the reading of the syllable right after it, `^>$` when nothing follows.
/// These keys never take part in the last-character level.
pub const NEXT_PREFIX: &str = "^>";
/// s3d §2: punctuation in the composition is a one-cell token under this reserved reading prefix (`_punct_，`); the
/// lexicon has no such reading. An English word kept in the composition (zhen-inline §1.0) is one too (`_punct_rust`).
pub const PUNCT_PREFIX: &str = "_punct_";
/// The reading every English token counts as in a rule 8 key.
const ENGLISH_NEXT: &str = "_punct_en";
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
/// At most this many records; the lowest weights go first (§1.3). New words (`NEWWORD`) are counted apart.
pub const CAPACITY: usize = 50_000;

// ---- New-word B (docs/contracts/newword-b.md) ----

/// Context field of a new-word record (§2.3): a sentinel distinct from the sentence-start keys (`NEXT_PREFIX`) and
/// the global "". Neither `context_key` nor `record_key` returns it, so the S4 lookups never match it; versions
/// without new-word B skip these lines at load, because their `legal_context` requires `context_key(c) == c` and
/// `context_key("#")` is "^".
pub const NEWWORD: &str = "#";
/// Syllables (= characters) of a learned new word (§2.2).
pub const NEWWORD_MIN: usize = 2;
pub const NEWWORD_MAX: usize = 4;
/// At most this many new-word records, trimmed lowest weight first apart from `CAPACITY` (§2.3): in memory at every
/// new-word learn (`learn_new`), on load and at every full rewrite (`prune`).
pub const NEWWORD_CAP: usize = 2_000;
/// Characters that split a run of re-picked single characters (§2.2): high-frequency function characters. Table T1,
/// the one the pre-study simulation used (picked after looking at dev, so in sample). It was not swept in this slice:
/// contract §3.2 and §7 keep T1 and leave other tables out of scope.
pub const NEWWORD_BREAK: &str = "的了是在再嗎呢吧啊喔耶囉啦欸也而就和或與及跟你我他她它們這那有沒要會很都還又給把被讓";
/// A run made only of these is never learned (§2.2): numbers, codes and amounts.
pub const NEWWORD_DIGITS: &str = "〇零一二三四五六七八九十百千萬億兩壹貳參肆伍陸柒捌玖拾佰仟";
/// Entry score (log10) of a new word of decayed weight w: `S_MIN + (S_MAX - S_MIN) * w / (w + 1)` (§2.4).
/// Chosen by the contract §3.2 sweep (2026-10-17, research log): of [-5,-3], [-4,-2] and [-3,-1], every range passed the
/// veto, net >= 0 and class (i) = 0 in all four cvtune/wikitune x chat/formal cells; [-3,-1] has the largest summed net
/// (190 against 186 and 181). Never tuned on the dev sets.
pub const NEWWORD_S_MIN: f64 = -3.0;
pub const NEWWORD_S_MAX: f64 = -1.0;
/// Contract §2.2 item 3 (revision one): a run is not learned when the best lexicon word under its reading scores above
/// this (log10, the file score before capping and demotion), so a learned word never covers a strong lexicon word. -6.5
/// sits above every wiki-sourced row of overlay-add.tsv ([-7.67, -6.61]) and the base's three count-less modes (-7.47,
/// -7.17, -7.04), which therefore do not block, and below the manual overlay rows (-6.29 to -5.43), the whole names
/// layer (-5.54 to -3.75) and the eight base words the 2026-10-10 sweep saw covered (lowest 工黨 -6.09), which do. Chosen
/// after looking at those 16 rows (in sample); the cvtune and formal sweep of contract §3.2 is its first test.
pub const NEWWORD_SHADOW: f64 = -6.5;
/// Contract §2.2 item 5 (variant B, chosen by the user on 2026-10-10 after the measurement in the research log): a run
/// learned under §2.2 also teaches each contiguous sub-span of NEWWORD_MIN..=NEWWORD_MAX characters that holds a
/// re-picked character, is not all NEWWORD_DIGITS and whose reading has no lexicon word at all. Off: rule A only.
pub const NEWWORD_SUBSPANS: bool = true;

/// §2.4: the entry score of a new word of decayed weight `w`.
pub fn newword_score(w: f64) -> f64 {
    NEWWORD_S_MIN + (NEWWORD_S_MAX - NEWWORD_S_MIN) * (w / (w + 1.0))
}

pub(crate) fn is_han(c: char) -> bool {
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

/// zhen-inline §1.0: the English word of a token reading (`_punct_rust` -> `rust`): the text after `PUNCT_PREFIX`, when it is
/// non-empty ASCII letters only (no punctuation mark is).
pub fn english_of(reading: &str) -> Option<&str> {
    reading.strip_prefix(PUNCT_PREFIX).filter(|w| !w.is_empty() && w.bytes().all(|b| b.is_ascii_alphabetic()))
}

/// zhen-inline §1.3e: a space unit, the token a Space types after an English token (`_punct_ `).
pub fn is_space_unit(reading: &str) -> bool {
    reading.strip_prefix(PUNCT_PREFIX) == Some(" ")
}

/// The one test "English token or space unit": the two kinds of unit that count as one kind of neighbour (rule 8 key,
/// `next_key`) and that a Space types a space after (`Engine::breaks_with_space`).
pub fn english_or_space(reading: &str) -> bool {
    english_of(reading).is_some() || is_space_unit(reading)
}

/// Rule 8: the key a word under `SENTINEL` is learned and looked up with; `next` is the reading of the syllable
/// right after the word (`None`: the word ends the composition). An English token or a space unit after the word counts as
/// one kind of neighbour, not as the word it holds (zhen-inline §1.0, §1.3e): `^>_punct_en`. Every key of rule 8 comes through here.
pub fn next_key(next: Option<&str>) -> String {
    let next = if next.is_some_and(english_or_space) { Some(ENGLISH_NEXT) } else { next };
    format!("{NEXT_PREFIX}{}", next.unwrap_or("$"))
}

/// The one place a span's context key becomes the key records are learned and looked up under (rule 8): `^`
/// becomes `next_key(next)`, every other key is kept. Decoding, the commit and the prediction row all call it.
pub fn record_key(context: String, next: Option<&str>) -> String {
    if context == SENTINEL {
        next_key(next)
    } else {
        context
    }
}

/// Which lookup level answered (§1.1, §12): decides the boost size (`lm::LEARN_EPS` or the smaller global one).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Level {
    Exact,
    LastChar,
    Global,
}

/// A one-character word: no global level, no last-character level, learned only under a full two-character
/// context (§12, rule 7).
pub fn is_single(word: &str) -> bool {
    word.chars().count() == 1
}

/// Exactly `MAX_CONTEXT` Han characters: the only key a one-character word is learned under (rule 7).
pub fn is_full_context(key: &str) -> bool {
    key.chars().count() == MAX_CONTEXT && key.chars().all(is_han)
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
    /// How many records are new words (`NEWWORD`): with none, decoding skips the new-word lookups entirely.
    new_count: usize,
}

impl Learner {
    pub fn from_records(records: Vec<Record>) -> Learner {
        let mut l = Learner { records, index: Default::default(), new_count: 0 };
        l.reindex();
        l
    }
    fn reindex(&mut self) {
        self.index.clear();
        for (i, r) in self.records.iter().enumerate() {
            self.index.entry(reading_key(&r.reading)).or_default().push(i);
        }
        self.new_count = self.records.iter().filter(|r| r.context == NEWWORD).count();
    }
    /// True when any new-word record exists.
    pub fn has_new_words(&self) -> bool {
        self.new_count > 0
    }
    /// §2.4: new words of `reading` with decayed weight ≥ `ACTIVE`, heaviest first (ties by word).
    pub fn new_words(&self, reading: &[String], today: i64) -> Vec<(&str, f64)> {
        let mut v = self.new_words_weighed(reading, today);
        v.retain(|x| x.1 >= ACTIVE);
        v
    }
    /// Every new word of `reading` whatever its weight, heaviest first: what the candidate window lists, so ⌘⌫ can
    /// reach any record still on disk.
    pub fn new_words_all(&self, reading: &[String], today: i64) -> Vec<&str> {
        self.new_words_weighed(reading, today).into_iter().map(|x| x.0).collect()
    }
    /// The new words of `reading` with their decayed weights, heaviest first (ties by word).
    fn new_words_weighed(&self, reading: &[String], today: i64) -> Vec<(&str, f64)> {
        if self.new_count == 0 {
            return Vec::new();
        }
        let mut v: Vec<(&str, f64)> = self.new_records(reading).map(|r| (r.word.as_str(), decayed(r, today))).collect();
        v.sort_by(|a, b| b.1.total_cmp(&a.1).then(a.0.cmp(b.0)));
        v
    }
    fn new_records(&self, reading: &[String]) -> impl Iterator<Item = &Record> {
        self.index.get(&reading_key(reading)).into_iter().flatten().map(|&i| &self.records[i]).filter(|r| r.context == NEWWORD)
    }
    /// Decayed weight of the new word (reading, word), whatever it is (§2.4: a fixed new word scores from its record
    /// without the `ACTIVE` threshold); `None` without a record.
    pub fn new_word_weight(&self, reading: &[String], word: &str, today: i64) -> Option<f64> {
        if self.new_count == 0 {
            return None;
        }
        self.find(NEWWORD, reading, word).map(|i| decayed(&self.records[i], today))
    }
    /// Day of the new word (reading, word)'s record; `None` without one.
    pub fn new_word_day(&self, reading: &[String], word: &str) -> Option<i64> {
        if self.new_count == 0 {
            return None;
        }
        self.find(NEWWORD, reading, word).map(|i| self.records[i].day)
    }
    /// §2.2 step 4: learn (or strengthen by 1 after decay) the new word (reading, word); returns the record.
    /// Past `NEWWORD_CAP` the lowest new words go at once, as `prune` would trim them (§2.3), so appends between full
    /// rewrites never grow the new words in memory past the cap (the returned record can be among those dropped).
    pub fn learn_new(&mut self, reading: &[String], word: &str, today: i64) -> Record {
        let r = self.bump(NEWWORD, reading, word, today, 1.0);
        if self.new_count > NEWWORD_CAP {
            let w: Vec<f64> = self.records.iter().map(|r| decayed(r, today)).collect();
            let mut new: Vec<usize> = (0..w.len()).filter(|&i| self.records[i].context == NEWWORD).collect();
            let drop = over_cap(&mut new, NEWWORD_CAP, &w).to_vec();
            self.remove_at(drop);
        }
        r
    }
    /// §2.5 "used": +1 after decay. `None` without a record: a use never creates one. The caller counts a word at
    /// most once per local day (the engine passes only records not dated today).
    pub fn use_new(&mut self, reading: &[String], word: &str, today: i64) -> Option<Record> {
        self.find(NEWWORD, reading, word)?;
        Some(self.bump(NEWWORD, reading, word, today, 1.0))
    }
    /// §2.5 "re-picked away": halve the decayed weight; `None` without a record.
    pub fn halve_new(&mut self, reading: &[String], word: &str, today: i64) -> Option<Record> {
        let i = self.find(NEWWORD, reading, word)?;
        let r = &mut self.records[i];
        r.weight = decayed(r, today) * 0.5;
        r.day = today;
        Some(r.clone())
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
                self.new_count += (context == NEWWORD) as usize;
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
    /// Halves the decayed weight of (context, reading, word) if it has a record; returns the record as it now is.
    /// A re-pick halves the displaced word under the key it is committed with and, when different, under the key
    /// it was picked with (§14 amendment, point 2).
    pub fn halve(&mut self, context: &str, reading: &[String], word: &str, today: i64) -> Option<Record> {
        let i = self.find(context, reading, word)?;
        let r = &mut self.records[i];
        r.weight = decayed(r, today) * 0.5;
        r.day = today;
        Some(r.clone())
    }
    /// One re-pick at commit (§1.2–§1.3): `context` is a full key from `context_key`, `displaced` the
    /// word shown before the pick (its weight halves if it had a record under this key). Returns the
    /// records it changed, as they now are (displaced, taught, global; at most 3): what an append
    /// writes (§4).
    pub fn teach(&mut self, context: &str, reading: &[String], word: &str, displaced: &str, today: i64) -> Vec<Record> {
        // The bare "^" is never a record key (§14 rule 8; §8 point 5: memory matches the disk, where `valid` drops it).
        // §12 rules 6 and 7: a single character learns only under a full two-character Han context (not "^",
        // not a one-character key, not a synthetic key).
        if context == SENTINEL || (is_single(word) && !is_full_context(context)) {
            return Vec::new();
        }
        let mut touched = Vec::with_capacity(3);
        touched.extend(self.halve(context, reading, displaced, today));
        touched.push(self.bump(context, reading, word, today, 1.0));
        if is_single(word) {
            return touched; // never globalizes (§12 rule 1)
        }
        // §1.3 globalize: ≥ 2 distinct full keys with an active record. All sentence-start keys (`NEXT_PREFIX`)
        // count as one key, as the single "^" did before rule 8. A new-word record is never a context (new-word B §2.4).
        let Some(ix) = self.index.get(&reading_key(reading)) else { return touched };
        let keys: std::collections::HashSet<&str> = ix
            .iter()
            .map(|&i| &self.records[i])
            .filter(|r| r.word == word && r.context != GLOBAL && r.context != NEWWORD && decayed(r, today) >= ACTIVE)
            .map(|r| if r.context.starts_with(NEXT_PREFIX) { NEXT_PREFIX } else { r.context.as_str() })
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
    /// (exact, last character, global), with their decayed weight, and the level that answered. The
    /// first level that has any usable record answers; a word found by several records of that level
    /// keeps the highest weight. Single characters skip the last-character level (§12 rule 5); a
    /// single-character record under any key but a full two-character one cannot exist (rules 6 and 7: teach
    /// does not make one, and load drops old ones). A synthetic key (`NEXT_PREFIX`, rule 8) has no
    /// last-character level, like "^".
    pub fn lookup(&self, context: &str, reading: &[String], today: i64) -> (Level, Vec<(&str, f64)>) {
        let Some(ix) = self.index.get(&reading_key(reading)) else { return (Level::Global, Vec::new()) };
        let last = context.chars().next_back().filter(|_| context != SENTINEL && context != GLOBAL && !context.starts_with(NEXT_PREFIX));
        let levels: [(Level, &dyn Fn(&str) -> bool); 3] = [
            (Level::Exact, &|c| c == context),
            (Level::LastChar, &|c| last.is_some_and(|l| c != SENTINEL && c != GLOBAL && !c.starts_with(NEXT_PREFIX) && c.chars().next_back() == Some(l))),
            (Level::Global, &|c| c == GLOBAL),
        ];
        for (lv, level) in levels {
            // Rule 5: no last-character level for single characters. Single-character records under any
            // key but a full two-character one do not exist: teach never makes them and load drops old ones.
            let single_barred = lv == Level::LastChar;
            let mut hits: Vec<(&str, f64)> = Vec::new();
            for &i in ix {
                let r = &self.records[i];
                let w = decayed(r, today);
                if w < ACTIVE || !level(&r.context) || (single_barred && is_single(&r.word)) {
                    continue;
                }
                match hits.iter_mut().find(|h| h.0 == r.word) {
                    Some(h) => h.1 = h.1.max(w),
                    None => hits.push((r.word.as_str(), w)),
                }
            }
            if !hits.is_empty() {
                hits.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal).then(a.0.cmp(b.0)));
                return (lv, hits);
            }
        }
        (Level::Global, Vec::new())
    }
    /// Removes the records at positions `drop` with swap_remove, patching the index instead of
    /// rebuilding it: every learning Enter on a full store trims one record, and a rebuild of
    /// CAPACITY entries took about 5 ms of the 16 ms key budget (§6.14). Record order is not kept.
    fn remove_at(&mut self, mut drop: Vec<usize>) {
        // Highest first: whatever sits last is never a later victim.
        drop.sort_unstable_by(|a, b| b.cmp(a));
        for i in drop {
            let last = self.records.len() - 1;
            self.new_count -= (self.records[i].context == NEWWORD) as usize;
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
    /// among equal weights the later record goes first). New words are trimmed to NEWWORD_CAP the same way, apart
    /// from the S4 records (new-word B §2.3).
    pub fn prune(&mut self, today: i64) {
        let w: Vec<f64> = self.records.iter().map(|r| decayed(r, today)).collect();
        let (live, mut drop): (Vec<usize>, Vec<usize>) = (0..w.len()).partition(|&i| w[i] >= PRUNE_FLOOR);
        let (mut new, mut old): (Vec<usize>, Vec<usize>) = live.into_iter().partition(|&i| self.records[i].context == NEWWORD);
        drop.extend_from_slice(over_cap(&mut old, CAPACITY, &w));
        drop.extend_from_slice(over_cap(&mut new, NEWWORD_CAP, &w));
        self.remove_at(drop);
    }
    pub fn clear(&mut self) {
        self.records.clear();
        self.index.clear();
        self.new_count = 0;
    }
}

/// The records of `live` past the `cap` heaviest (decayed weights `w`; among equal weights the later record goes
/// first); empty when `live` fits. A partition, not a sort: on a full store a save trims about one record.
fn over_cap<'a>(live: &'a mut [usize], cap: usize, w: &[f64]) -> &'a [usize] {
    if live.len() <= cap {
        return &[];
    }
    live.select_nth_unstable_by(cap, |&a, &b| w[b].total_cmp(&w[a]).then(a.cmp(&b)));
    &live[cap..]
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

    fn rd(s: &str) -> Vec<String> {
        s.split(' ').map(String::from).collect()
    }

    /// New-word B §2.4: a "#" record answers no S4 lookup level (exact, last character, global) for any context key
    /// decoding can ask with (`context_key` never returns "#").
    #[test]
    fn new_words_never_answer_a_context_lookup() {
        let mut l = Learner::default();
        let r = rd("ㄅㄢˇ ㄎㄨㄥˋ");
        l.learn_new(&r, "版控", 20_000);
        for ctx in ["要進", "進", SENTINEL, GLOBAL, "加入"] {
            assert!(l.lookup(ctx, &r, 20_000).1.is_empty(), "{ctx}");
        }
        assert_eq!(l.new_words(&r, 20_000), vec![("版控", 1.0)]);
    }

    /// New-word B §2.4: S4's globalization counts full contexts; "#" is never one, so one S4 teach next to a new-word
    /// record of the same (reading, word) makes no global record.
    #[test]
    fn globalization_does_not_count_the_new_word_record() {
        let mut l = Learner::default();
        let r = rd("ㄅㄢˇ ㄎㄨㄥˋ");
        l.learn_new(&r, "版控", 20_000);
        l.teach("要進", &r, "版控", "板控", 20_000);
        assert!(!l.records().iter().any(|x| x.context == GLOBAL), "a global record from one context");
        l.teach("加入", &r, "版控", "板控", 20_000);
        assert!(l.records().iter().any(|x| x.context == GLOBAL), "two real contexts still globalize");
    }

    /// §2.4 threshold, §2.5 once a day and halving.
    #[test]
    fn new_word_weights() {
        let mut l = Learner::default();
        let r = rd("ㄅㄢˇ ㄎㄨㄥˋ");
        assert!(l.use_new(&r, "版控", 20_000).is_none(), "a use never creates a record");
        l.learn_new(&r, "版控", 20_000);
        assert_eq!(l.new_word_day(&r, "版控"), Some(20_000));
        let u = l.use_new(&r, "版控", 20_014).unwrap();
        assert_eq!((u.weight, u.day), (1.5, 20_014), "decayed 14 days, plus one");
        let h = l.halve_new(&r, "版控", 20_014).unwrap();
        assert_eq!(h.weight, 0.75);
        l.halve_new(&r, "版控", 20_014);
        assert!(l.new_words(&r, 20_014).is_empty(), "under ACTIVE: not enumerated");
        assert_eq!(l.new_words_all(&r, 20_014), vec!["版控"], "still listed for the window");
        assert!(l.new_word_weight(&r, "版控", 20_014).is_some());
        assert_eq!(newword_score(0.0), NEWWORD_S_MIN);
        assert!(newword_score(1e9) < NEWWORD_S_MAX && newword_score(1.0) == (NEWWORD_S_MIN + NEWWORD_S_MAX) / 2.0);
        l.forget(&r, "版控");
        assert!(!l.has_new_words());
    }

    /// §2.3 (review of PR #196, finding 9): learning a new word past NEWWORD_CAP drops the lowest new word from memory at
    /// once (appends between full rewrites never call `prune`); S4 records are not counted or touched.
    #[test]
    fn learning_past_the_cap_trims_new_words_in_memory() {
        let mut recs: Vec<Record> = (0..NEWWORD_CAP)
            .map(|i| Record { context: NEWWORD.into(), reading: rd("ㄅㄢˇ ㄎㄨㄥˋ"), word: format!("版{}", char::from_u32(0x4e00 + i as u32).unwrap()), weight: 2.0, day: 20_000 })
            .collect();
        recs[7].weight = 0.6;
        let low = recs[7].word.clone();
        recs.push(Record { context: "要進".into(), reading: rd("ㄅㄢˇ"), word: "版".into(), weight: 0.2, day: 20_000 });
        let mut l = Learner::from_records(recs);
        l.learn_new(&rd("ㄆㄞˋ ㄍㄨㄥ"), "派工", 20_000);
        let new: Vec<&Record> = l.records().iter().filter(|r| r.context == NEWWORD).collect();
        assert_eq!(new.len(), NEWWORD_CAP);
        assert!(!new.iter().any(|r| r.word == low), "the lowest new word went");
        assert!(new.iter().any(|r| r.word == "派工"));
        assert_eq!(l.records().len(), NEWWORD_CAP + 1, "the S4 record stays");
        assert!(l.has_new_words());
        assert_eq!(l.new_words(&rd("ㄆㄞˋ ㄍㄨㄥ"), 20_000), vec![("派工", 1.0)], "index rebuilt");
    }
}
