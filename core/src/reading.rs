//! Reading decode (docs/spec/decoder.md §5): the n-gram decoder adds a
//! reading term to the path score of every single-character word, `LAMBDA * max(t(r, w), -CAP)`, t being P5's reading term
//! (the reading-channel research's `lex_logp`) with the sandhi exemption and the `floor_mc` floor and the 12 pairs of
//! `data/lexicon/std-exempt.tsv` (R1g), the registered arm B-f of section R1c. The table is built by `engine::load_reading_decode` from the merged
//! lexicon and the data directory, and handed to the decoder by `CappedLexicon::with_reading`; the engine (`load_lm`), the evaluation CLI and the replay
//! all call that one function. A lexicon built without it decodes as before. Last of all `with_mass` (reading-counts B, variant T3b) lets the
//! multi-character words tell apart the readings of a character whose single-character scores tie.
use crate::Lexicon;
use crate::eval::sha256_hex;
use std::collections::{HashMap, HashSet};

/// Weight of the reading term in the path score (R1 README, R1c: lambda 2.0 of the registered grid, arm B-f; R1d fixes it, no selection).
pub const LAMBDA: f64 = 2.0;
/// Clip of one single-character word's term: at least -CAP (R1 README: c 4, the clip of P5's cell `lex,1.0,4`).
pub const CAP: f64 = 4.0;
/// The score `data/lexicon/mcbpmf-data.txt` gives a single character's secondary reading it has no count for (the reading-decode research's `LEX_FLOOR`).
/// A pair at this score is estimated from the multi-character words instead (`ReadingDecode::new`).
pub const LEX_FLOOR: f64 = -6.8;
/// The tone-sandhi readings that cost nothing (the research's `SANDHI`): 一 read ㄧˊ or ㄧˋ and 不 read ㄅㄨˊ are the standard readings before the
/// right tones (docs/spec/decoder.md §8.1), not rare ones. Whatever the next syllable: the rule that picks the sandhi tone is not checked.
pub const SANDHI: [(&str, &str); 3] = [("ㄧˊ", "一"), ("ㄧˋ", "一"), ("ㄅㄨˊ", "不")];
/// `with_mass` adopts a mass-estimated t only when it is at most `-MASS_MIN_DEDUCTION` (variant T3b of the reading-counts B research; a smaller
/// deduction is a tie the multi-character words do not settle, so the reading stays at 0).
pub const MASS_MIN_DEDUCTION: f64 = 1.0;
/// Added to both masses of the ratio in `with_mass`, so a reading no multi-character word reads gets a finite (very negative) t.
const MASS_EPS: f64 = 1e-9;
/// The tone marks and the neutral-tone dot: a syllable without them is its initial and final.
const TONE_MARKS: [char; 4] = ['ˊ', 'ˇ', 'ˋ', '˙'];

/// Whether `with_mass` adopts the mass-estimated `t`: a deduction of at least `MASS_MIN_DEDUCTION`, the boundary included.
fn mass_adopts(t: f64) -> bool {
    t <= -MASS_MIN_DEDUCTION
}

fn is_floor(score: f64) -> bool {
    (score - LEX_FLOOR).abs() < 1e-9
}

/// The mc table of a lexicon (the research's `floor_mc` over `mc_logp`), only for the single-character pairs at `LEX_FLOOR` (the only ones read):
/// M(r, w) = 10^s(r, w) + the mass 10^s of every multi-character word of `base` (the text of `mcbpmf-data.txt` alone, the same rows
/// `Lexicon::parse_with` reads), once per position whose character is w and whose syllable is r; the value is log10 M(r, w) minus the largest
/// over w's readings in `lex`. A (reading, word) that repeats in `base` counts once, at its largest score; the masses add up in the order the
/// pairs first occur in the file, which is the order the research sums in. Keyed by (syllable id, character).
fn build_floor_mc(lex: &Lexicon, base: &str) -> HashMap<(u32, char), f64> {
    // The single-character rows of one-syllable readings, per character; a reading's entries are best first, so the first row of a word is its score.
    let mut by_char: HashMap<char, Vec<(u32, f64)>> = HashMap::new();
    for (r, rd) in lex.readings.iter().enumerate() {
        if rd.key_len != 1 {
            continue;
        }
        let syl = lex.key_pool[rd.key_off as usize];
        for p in lex.range(r) {
            let mut cs = lex.word(p).chars();
            if let (Some(c), None) = (cs.next(), cs.next()) {
                let v = by_char.entry(c).or_default();
                if !v.iter().any(|&(s, _)| s == syl) {
                    v.push((syl, lex.ents[p].score));
                }
            }
        }
    }
    by_char.retain(|_, v| v.iter().any(|&(_, s)| is_floor(s)));
    if by_char.is_empty() {
        return HashMap::new();
    }
    let rows = multi_rows(base, |c| by_char.contains_key(&c));
    let mut extra: HashMap<(u32, char), f64> = HashMap::new();
    for (reading, word, score) in rows {
        for (c, syl) in word.chars().zip(reading.split('-')) {
            if let (true, Some(&id)) = (by_char.contains_key(&c), lex.syl_ids.get(syl)) {
                *extra.entry((id, c)).or_insert(0.0) += 10f64.powf(score);
            }
        }
    }
    let mut out = HashMap::new();
    for (c, readings) in &by_char {
        let m: Vec<f64> = readings.iter().map(|&(syl, s)| (10f64.powf(s) + extra.get(&(syl, *c)).copied().unwrap_or(0.0)).log10()).collect();
        let best = m.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        for (&(syl, s), m) in readings.iter().zip(&m) {
            if is_floor(s) {
                out.insert((syl, *c), m - best);
            }
        }
    }
    out
}

/// The multi-character rows of `base` with at least one character `keep` accepts, one per (reading, word) at its largest score, in first-occurrence
/// order (the rows `Lexicon::parse_with` reads). The callers sum only over the characters they keep, so a row without one adds nothing; leaving it
/// out keeps the load's transient memory to the few rows that count.
fn multi_rows(base: &str, keep: impl Fn(char) -> bool) -> Vec<(&str, &str, f64)> {
    let (mut at, mut rows): (HashMap<(&str, &str), usize>, Vec<(&str, &str, f64)>) = (HashMap::new(), Vec::new());
    for l in base.lines() {
        if l.starts_with(['#', '_']) {
            continue;
        }
        let mut it = l.split_whitespace();
        let (Some(reading), Some(word), Some(score), None) = (it.next(), it.next(), it.next(), it.next()) else { continue };
        let n = word.chars().count();
        if n < 2 || n != reading.split('-').count() || !word.chars().any(&keep) {
            continue;
        }
        let Ok(score) = score.parse::<f64>() else { continue };
        match at.get(&(reading, word)) {
            Some(&i) => rows[i].2 = rows[i].2.max(score),
            None => {
                at.insert((reading, word), rows.len());
                rows.push((reading, word, score));
            }
        }
    }
    rows
}

/// What the decoder needs for the reading term besides the lexicon: the mc values of the floor pairs. Built once from the lexicon and the text of
/// `mcbpmf-data.txt`, read-only afterwards.
pub struct ReadingDecode {
    floor_mc: HashMap<(u32, char), f64>,
    /// (syllable, character) pairs whose term is 0 like a `SANDHI` reading (R1g, `std-exempt.tsv`); empty until `with_exempt`.
    exempt: HashSet<(String, char)>,
    /// First 8 hex of the SHA-256 of the exempt list's text (`with_exempt`), for the summary tag; empty until then.
    exempt_sha8: String,
    /// The mass-estimated t of the pairs `with_mass` adopted (each <= `-MASS_MIN_DEDUCTION`); empty until then. Keyed by (syllable id, character).
    mass: HashMap<(u32, char), f64>,
    /// Whether `with_mass` ran, for the summary tag.
    mass_built: bool,
    /// Evaluation only (`with_table`, the CLI's `--reading-table`): replaces the whole of `logp` for the pairs of the lexicon; a pair the table lacks
    /// is 0. Keyed by (syllable id, character). `None` in the engine and in every shipping path.
    table: Option<HashMap<(u32, char), f64>>,
}

impl ReadingDecode {
    pub fn new(lex: &Lexicon, base_text: &str) -> ReadingDecode {
        ReadingDecode { floor_mc: build_floor_mc(lex, base_text), exempt: HashSet::new(), exempt_sha8: String::new(), mass: HashMap::new(), mass_built: false, table: None }
    }

    /// Evaluation only: replaces the built-in t by `text`, one `character TAB reading TAB t` row per line (the format of `dump_table`), t being the value
    /// before the `LAMBDA * max(t, -CAP)` step. A pair of the lexicon the text lacks reads t = 0. A malformed row, a character or reading the lexicon has
    /// no single-character pair for, or a pair twice is an error naming the line.
    pub fn with_table(mut self, lex: &Lexicon, text: &str) -> Result<ReadingDecode, String> {
        let mut table = HashMap::new();
        for (i, l) in text.lines().enumerate() {
            let n = i + 1;
            let bad = || format!("line {n}: not `character TAB reading TAB value`");
            let mut f = l.split('\t');
            let (Some(w), Some(syl), Some(t), None) = (f.next(), f.next(), f.next(), f.next()) else { return Err(bad()) };
            let (mut cs, t) = (w.chars(), t.parse::<f64>().ok().filter(|t| t.is_finite()));
            let (Some(c), None, Some(t)) = (cs.next(), cs.next(), t) else { return Err(bad()) };
            let Some(&id) = lex.syl_ids.get(syl).filter(|_| lex.syllable_score(syl, w).is_some()) else {
                return Err(format!("line {n}: the lexicon has no single-character pair {w} {syl}"));
            };
            if table.insert((id, c), t).is_some() {
                return Err(format!("line {n}: the pair is twice in the table"));
            }
        }
        self.table = Some(table);
        Ok(self)
    }

    /// Evaluation only: the table `logp` uses now in the format `with_table` reads: one row per (single character, reading) pair of every character with at
    /// least two single-character readings (the others read t = 0), sorted by character then reading, t in Rust's shortest round-trip float text so
    /// reading it back changes nothing.
    pub fn dump_table(&self, lex: &Lexicon) -> String {
        let mut pairs: Vec<(char, String)> = Vec::new();
        for (r, rd) in lex.readings.iter().enumerate() {
            if rd.key_len != 1 {
                continue;
            }
            let syl = &lex.syl_names[lex.key_pool[rd.key_off as usize] as usize];
            for p in lex.range(r) {
                let mut cs = lex.word(p).chars();
                if let (Some(c), None) = (cs.next(), cs.next()) {
                    pairs.push((c, syl.clone()));
                }
            }
        }
        pairs.sort();
        pairs.dedup();
        let mut readings: HashMap<char, usize> = HashMap::new();
        for (c, _) in &pairs {
            *readings.entry(*c).or_default() += 1;
        }
        pairs.iter().filter(|(c, _)| readings[c] >= 2).map(|(c, syl)| format!("{c}\t{syl}\t{:?}\n", self.logp(lex, syl, &c.to_string()).unwrap_or(0.0))).collect()
    }

    /// R1g: the pairs in `text` (`syllable<TAB>character` rows; blank lines and `#` lines skipped) cost 0, whatever the lexicon scores them.
    /// A row that is not a syllable and one character stops the run (`Err`); a pair the lexicon lacks stays harmless (`logp` is `None` for it).
    pub fn with_exempt(mut self, text: &str) -> Result<ReadingDecode, String> {
        for (i, l) in text.lines().enumerate().filter(|(_, l)| !l.trim().is_empty() && !l.starts_with('#')) {
            let mut f = l.split('\t');
            let (Some(syl), Some(w), None) = (f.next(), f.next(), f.next()) else { return Err(format!("line {}: expected syllable<TAB>character", i + 1)) };
            let mut cs = w.chars();
            match (cs.next(), cs.next()) {
                (Some(c), None) if !syl.is_empty() => self.exempt.insert((syl.to_string(), c)),
                _ => return Err(format!("line {}: expected syllable<TAB>character", i + 1)),
            };
        }
        self.exempt_sha8 = sha256_hex(text.as_bytes())[..8].to_string();
        Ok(self)
    }

    /// Reading counts B, variant T3b. A character with at least two single-character readings whose t are all 0 now (so after the exemptions, which is why
    /// this runs after `with_exempt`) and that is a 聲韻字 (at least two of those readings differ once the tone marks are taken away, not only in tone)
    /// gets, for each reading r, t(r) = log10((m(r) + EPS) / (max m + EPS)) where m(r) is the sum of 10^score over the rows of `base_text` (the text
    /// of `mcbpmf-data.txt`) of at least two characters that read the character r at some position, once per (reading, word) at its largest score, summed
    /// in the order the pairs first occur. Only a t <= `-MASS_MIN_DEDUCTION` is adopted; the other readings, a character no such row reads (all m 0),
    /// the exempt pairs and the `SANDHI` readings stay at 0. `logp` then answers the adopted t.
    pub fn with_mass(mut self, lex: &Lexicon, base_text: &str) -> ReadingDecode {
        // The single-character readings per character (syllable ids, first occurrence first).
        let mut by_char: HashMap<char, Vec<u32>> = HashMap::new();
        for (r, rd) in lex.readings.iter().enumerate() {
            if rd.key_len != 1 {
                continue;
            }
            let syl = lex.key_pool[rd.key_off as usize];
            for p in lex.range(r) {
                let mut cs = lex.word(p).chars();
                if let (Some(c), None) = (cs.next(), cs.next()) {
                    let v = by_char.entry(c).or_default();
                    if !v.contains(&syl) {
                        v.push(syl);
                    }
                }
            }
        }
        let body = |syl: u32| lex.syl_names[syl as usize].chars().filter(|c| !TONE_MARKS.contains(c)).collect::<String>();
        by_char.retain(|c, v| {
            let c = c.to_string();
            v.len() >= 2
                && v.iter().all(|&s| self.logp(lex, &lex.syl_names[s as usize], &c) == Some(0.0))
                && v.iter().map(|&s| body(s)).collect::<HashSet<_>>().len() >= 2
        });
        let mut m: HashMap<(u32, char), f64> = HashMap::new();
        for (reading, word, score) in multi_rows(base_text, |c| by_char.contains_key(&c)) {
            for (c, syl) in word.chars().zip(reading.split('-')) {
                if let (true, Some(&id)) = (by_char.contains_key(&c), lex.syl_ids.get(syl)) {
                    if by_char[&c].contains(&id) {
                        *m.entry((id, c)).or_insert(0.0) += 10f64.powf(score);
                    }
                }
            }
        }
        for (c, readings) in &by_char {
            let mx = readings.iter().map(|&s| m.get(&(s, *c)).copied().unwrap_or(0.0)).fold(0.0, f64::max);
            if mx == 0.0 {
                continue;
            }
            for &s in readings {
                let t = ((m.get(&(s, *c)).copied().unwrap_or(0.0) + MASS_EPS) / (mx + MASS_EPS)).log10();
                let syl = lex.syl_names[s as usize].as_str();
                let exempt = SANDHI.contains(&(syl, c.to_string().as_str())) || self.exempt.contains(&(syl.to_string(), *c));
                if mass_adopts(t) && !exempt {
                    self.mass.insert((s, *c), t);
                }
            }
        }
        self.mass_built = true;
        self
    }

    /// The summary-line tag of a run that decodes with this table: `+rd`, then `+rx:<sha8 of the exempt list>` once `with_exempt` gave one, then `+rm`
    /// once `with_mass` ran (the CLI and the replay print it at the end of every setting's tag).
    pub fn tag(&self) -> String {
        let rx = if self.exempt_sha8.is_empty() { String::new() } else { format!("+rx:{}", self.exempt_sha8) };
        format!("+rd{rx}{}", if self.mass_built { "+rm" } else { "" })
    }

    /// log10 P(reading | word) of the single-character word `word` read `syl`, relative to the word's best reading (<= 0): the lexicon's score
    /// minus the word's best score (the research's `lex_logp`), 0 for a `SANDHI` reading, the mc value for a pair at `LEX_FLOOR`, the mass value for a pair
    /// `with_mass` adopted (never an exempt or `SANDHI` pair, which stay 0), and 0 again for an exempt pair. `None` when the
    /// lexicon has no such pair. The scores are the raw lexicon's (largest over duplicate rows), before capping, demotion and learning.
    pub fn logp(&self, lex: &Lexicon, syl: &str, word: &str) -> Option<f64> {
        if let Some(tb) = &self.table {
            lex.syllable_score(syl, word)?;
            let mut cs = word.chars();
            return Some(match (cs.next(), cs.next(), lex.syl_ids.get(syl)) {
                (Some(c), None, Some(&id)) => tb.get(&(id, c)).copied().unwrap_or(0.0),
                _ => 0.0,
            });
        }
        let (s, best) = (lex.syllable_score(syl, word)?, lex.best_word_score(word)?);
        let mut t = s - best;
        if SANDHI.contains(&(syl, word)) {
            t = 0.0;
        }
        if is_floor(s) {
            let mc = lex.syl_ids.get(syl).zip(word.chars().next()).and_then(|(&id, c)| self.floor_mc.get(&(id, c)));
            t = mc.copied().unwrap_or(t);
        }
        if !self.mass.is_empty() {
            let id = lex.syl_ids.get(syl).zip(word.chars().next()).filter(|_| word.chars().count() == 1);
            t = id.and_then(|(&id, c)| self.mass.get(&(id, c))).copied().unwrap_or(t);
        }
        if word.chars().next().is_some_and(|c| self.exempt.contains(&(syl.to_string(), c))) {
            t = 0.0;
        }
        Some(t)
    }

    /// The term one single-character word adds to a path: `LAMBDA * max(logp, -CAP)`; 0 for a pair the lexicon lacks and for any word of
    /// more than one character (a one-syllable multi-character entry is not a reading of a character).
    pub fn term(&self, lex: &Lexicon, syl: &str, word: &str) -> f64 {
        if word.chars().count() != 1 {
            return 0.0;
        }
        self.logp(lex, syl, word).map_or(0.0, |t| LAMBDA * t.max(-CAP))
    }
}

/// Tests build the table through `engine::load_reading_decode`, the one function the engine and the CLIs call: a data directory of two files.
#[cfg(test)]
pub(crate) mod test_support {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    /// `base` is the text of `mcbpmf-data.txt` (the lexicon is parsed from it, plus `overlay` when given), `exempt` that of `std-exempt.tsv`.
    /// `Err` is the loader's error as a string.
    pub fn try_built(base: &str, overlay: Option<&str>, exempt: &str) -> Result<(Lexicon, ReadingDecode), String> {
        static N: AtomicUsize = AtomicUsize::new(0);
        let dir = std::env::temp_dir().join(format!("shanjie-reading-{}-{}", std::process::id(), N.fetch_add(1, Ordering::Relaxed)));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("mcbpmf-data.txt"), base).unwrap();
        std::fs::write(dir.join(crate::engine::READING_EXEMPT_FILE), exempt).unwrap();
        let lex = Lexicon::parse_with(base, overlay).unwrap();
        let rd = crate::engine::load_reading_decode(&lex, &dir).map_err(|e| e.to_string());
        let _ = std::fs::remove_dir_all(&dir);
        rd.map(|rd| (lex, rd))
    }

    pub fn built(base: &str, overlay: Option<&str>, exempt: &str) -> (Lexicon, ReadingDecode) {
        try_built(base, overlay, exempt).unwrap()
    }
}

#[cfg(test)]
mod tests {
    use super::test_support::{built, try_built};
    use super::*;

    fn close(a: f64, b: f64) {
        assert!((a - b).abs() < 1e-9, "{a} vs {b}");
    }

    fn mk(base: &str) -> (Lexicon, ReadingDecode) {
        built(base, None, "")
    }

    /// 甲 has three readings (a -1.0 twice over, b -3.0, c -9.0: the duplicate row a -5.0 loses to a -1.0), 乙 ties under a and b, 丁 has one reading,
    /// and 甲甲 is a two-character word whose characters would score -2 each if they were counted.
    const LEX: &str = "a 甲 -1.0\nb 甲 -3.0\nc 甲 -9.0\na 甲 -5.0\na 乙 -2.0\nb 乙 -2.0\nd 丁 -7.0\na-a 甲甲 -5.0\n";

    /// A single-character word is scored against its own best reading (the largest over its rows, duplicates included); a tie with the best
    /// or the only reading is 0; a rare reading is penalised, a missing pair is `None` and adds 0.
    #[test]
    fn a_rare_reading_is_penalised_against_the_words_best() {
        let (lex, rd) = mk(LEX);
        close(rd.logp(&lex, "b", "甲").unwrap(), -2.0);
        close(rd.logp(&lex, "a", "甲").unwrap(), 0.0);
        close(rd.logp(&lex, "b", "乙").unwrap(), 0.0);
        close(rd.logp(&lex, "d", "丁").unwrap(), 0.0);
        assert!(rd.logp(&lex, "z", "甲").is_none() && rd.logp(&lex, "a", "戊").is_none());
        close(rd.term(&lex, "b", "甲"), LAMBDA * -2.0);
        close(rd.term(&lex, "z", "甲"), 0.0);
        close(rd.term(&lex, "a", "甲"), 0.0);
    }

    /// A one-syllable entry whose word has two characters is not a character's reading: the term is 0 whatever the lexicon scores it.
    #[test]
    fn a_multi_character_word_gets_no_term() {
        // the base file drops such a row; an overlay row (pack, learned word) can carry one
        let base = "a 甲 -1.0\nb 甲 -3.0\n";
        let (lex, rd) = built(base, Some("b\t甲乙\t-9.0\tx\n"), "");
        close(rd.term(&lex, "b", "甲"), LAMBDA * -2.0);
        assert!(rd.logp(&lex, "b", "甲乙").is_some());
        close(rd.term(&lex, "b", "甲乙"), 0.0);
    }

    /// The term is clipped at -CAP before it is weighted (-8 becomes -4, so -8.0 with LAMBDA 2).
    #[test]
    fn the_term_is_clipped_at_cap() {
        assert_eq!((LAMBDA, CAP), (2.0, 4.0));
        let (lex, rd) = mk(LEX);
        close(rd.logp(&lex, "c", "甲").unwrap(), -8.0);
        close(rd.term(&lex, "c", "甲"), -LAMBDA * CAP);
    }

    /// The three sandhi readings cost 0 where the rule would charge them; the same character's other readings and other characters do not.
    #[test]
    fn sandhi_readings_are_free() {
        let (lex, rd) = mk("ㄧ 一 -1.0\nㄧˊ 一 -5.0\nㄧˋ 一 -4.0\nㄧㄠ 一 -9.0\nㄅㄨˋ 不 -1.0\nㄅㄨˊ 不 -1.0\nㄅㄨ 不 -5.0\nㄧˊ 乙 -5.0\nㄧ 乙 -1.0\n");
        close(rd.logp(&lex, "ㄧˊ", "一").unwrap(), 0.0);
        close(rd.logp(&lex, "ㄧˋ", "一").unwrap(), 0.0);
        close(rd.logp(&lex, "ㄅㄨˊ", "不").unwrap(), 0.0);
        close(rd.logp(&lex, "ㄅㄨ", "不").unwrap(), -4.0);
        close(rd.logp(&lex, "ㄧㄠ", "一").unwrap(), -8.0);
        close(rd.logp(&lex, "ㄧˊ", "乙").unwrap(), -4.0);
    }

    /// The mc value of a floor pair: 長 has a count reading a (-1.0) and two floor readings, b and c. The base word 長長 reads b then a (its repeated
    /// row counts once, at the larger score -2.0), so M(b) = 10^-6.8 + 10^-2.0, M(a) = 10^-1.0 + 10^-2.0, M(c) = 10^-6.8, and each reading's value is
    /// log10 M minus the largest log10 M. A floor character whose only reading is the floor one is its own best (0).
    #[test]
    fn floor_pairs_take_the_mc_value() {
        let base = "a 長 -1.0\nb 長 -6.8\nc 長 -6.8\nb-a 長長 -3.0\nb-a 長長 -2.0\nd 短 -6.8\nd-a 短短 -2.0\n_x _y 0\n# c\n";
        let (lex, rd) = mk(base);
        let best = (10f64.powf(-1.0) + 10f64.powf(-2.0)).log10();
        let want = (10f64.powf(-6.8) + 10f64.powf(-2.0)).log10() - best;
        close(rd.logp(&lex, "b", "長").unwrap(), want);
        close(rd.term(&lex, "b", "長"), LAMBDA * want.max(-CAP));
        close(rd.logp(&lex, "a", "長").unwrap(), 0.0);
        // c is a floor reading no multi-character word reads
        close(rd.logp(&lex, "c", "長").unwrap(), (10f64.powf(-6.8)).log10() - best);
        // 短's only reading is the floor one: its best reading is itself
        close(rd.logp(&lex, "d", "短").unwrap(), 0.0);
        // with no multi-character word at all the pair costs its lexicon difference
        let (lex, rd) = mk("a 長 -1.0\nb 長 -6.8\n");
        close(rd.logp(&lex, "b", "長").unwrap(), (10f64.powf(-6.8)).log10() - (10f64.powf(-1.0)).log10());
    }

    /// R1g: an exempt pair scores 0 (its lexicon difference is -2.0 without the list), a rare pair that is not on the list still gets the term,
    /// the list's comments and blank lines are skipped, and a malformed row is a load error naming std-exempt.tsv, never a table without it.
    #[test]
    fn an_exempt_pair_scores_zero_and_the_rest_keep_the_term() {
        let (lex, rd) = built(LEX, None, "# header\n\nb\t甲\nz\t乙\n");
        close(rd.logp(&lex, "b", "甲").unwrap(), 0.0);
        close(rd.term(&lex, "b", "甲"), 0.0);
        close(rd.term(&lex, "c", "甲"), -LAMBDA * CAP);
        assert!(rd.logp(&lex, "z", "乙").is_none(), "an exempt pair the lexicon lacks is still absent");
        for bad in ["b\t甲乙\n", "b 甲\n", "b\t甲\textra\n", "\t甲\n"] {
            let e = try_built(LEX, None, bad).err().unwrap_or_else(|| panic!("{bad:?} loaded"));
            assert!(e.contains("std-exempt.tsv"), "{e}");
        }
    }

    /// Evaluation only: the dump lists the pairs of characters with at least two single-character readings (the exempt and sandhi ones as 0), and
    /// read back it changes no `logp` or `term` bit; a changed row changes that pair only, a pair the table lacks is 0, and a malformed row, an
    /// unknown pair or a pair twice is an error naming the line.
    #[test]
    fn a_table_replaces_the_built_in_values() {
        let base = "a 長 -1.0\nb 長 -6.8\nb-a 長長 -2.0\na 甲 -1.0\nb 甲 -3.0\nc 甲 -9.0\nㄧˊ 一 -5.0\nㄧ 一 -1.0\nd 乙 -4.0\n";
        let (lex, rd) = built(base, None, "c\t甲\n");
        let dump = rd.dump_table(&lex);
        assert_eq!(dump.lines().count(), 7, "乙 has one reading and is left out: {dump}");
        assert!(dump.contains("甲\tc\t0.0\n") && dump.contains("一\tㄧˊ\t0.0\n") && !dump.contains('乙'));
        let rd2 = ReadingDecode::new(&lex, base).with_table(&lex, &dump).unwrap();
        for syl_w in [("a", "長"), ("b", "長"), ("a", "甲"), ("b", "甲"), ("c", "甲"), ("ㄧˊ", "一"), ("ㄧ", "一"), ("d", "乙")] {
            assert_eq!(rd.logp(&lex, syl_w.0, syl_w.1).map(f64::to_bits), rd2.logp(&lex, syl_w.0, syl_w.1).map(f64::to_bits), "{syl_w:?}");
            assert_eq!(rd.term(&lex, syl_w.0, syl_w.1).to_bits(), rd2.term(&lex, syl_w.0, syl_w.1).to_bits(), "{syl_w:?}");
        }
        assert_eq!(rd2.dump_table(&lex), dump, "the dump of a table is the table");
        let rd3 = ReadingDecode::new(&lex, base).with_table(&lex, "甲\tb\t-0.5\n").unwrap();
        close(rd3.logp(&lex, "b", "甲").unwrap(), -0.5);
        close(rd3.logp(&lex, "c", "甲").unwrap(), 0.0);
        close(rd3.logp(&lex, "ㄧˊ", "一").unwrap(), 0.0);
        assert!(rd3.logp(&lex, "z", "甲").is_none());
        for (bad, line) in [("甲\tb\t-1\n甲\tb\n", 2), ("甲\tb\tx\n", 1), ("甲甲\tb\t0\n", 1), ("甲\tb\t0\textra\n", 1), ("甲\tb\tNaN\n", 1), ("甲\tb\t0\n甲\tb\t1\n", 2), ("甲\tz\t0\n", 1), ("戊\ta\t0\n", 1), ("甲\ta\t0\n甲\td\t0\n", 2)] {
            let e = ReadingDecode::new(&lex, base).with_table(&lex, bad).err().unwrap_or_else(|| panic!("{bad:?} loaded"));
            assert!(e.starts_with(&format!("line {line}:")), "{bad:?}: {e}");
        }
    }

    /// The tag names the exempt list: `+rd+rx:` and the first 8 hex of the SHA-256 of its text (`+rm` follows once `with_mass` ran; this table has no mass step).
    #[test]
    fn the_tag_carries_the_exempt_lists_hash() {
        let (_, rd) = built(LEX, None, "b\t甲\n");
        assert_eq!(rd.tag(), format!("+rd+rx:{}+rm", &crate::eval::sha256_hex("b\t甲\n".as_bytes())[..8]));
        let plain = ReadingDecode::new(&Lexicon::parse(LEX).unwrap(), LEX);
        assert_eq!(plain.tag(), "+rd");
        assert_eq!(plain.with_exempt("b\t甲\n").unwrap().tag(), format!("+rd+rx:{}", &crate::eval::sha256_hex("b\t甲\n".as_bytes())[..8]));
    }

    /// Singles tie at -1 for every character below; the multi-character words give the masses. 甲 (a, b): m(a) = 10^-1, m(b) = 10^-3, so t(b) = -2 and
    /// t(a) = 0. 乙 (a, b): m(a) = 10^-1, m(b) = 10^-1.5, a deduction of 0.5, under 1: both stay 0. 丙 (a, b, c): the third reading has no word at all and
    /// takes log10(1e-9 / (0.1 + 1e-9)), about -8, the table clips it later. 丁 (a, b) has no multi-character word: all m 0, stays 0.
    const TIES: &str = "a 甲 -1.0\nb 甲 -1.0\na 乙 -1.0\nb 乙 -1.0\na 丙 -1.0\nb 丙 -1.0\nc 丙 -1.0\na 丁 -1.0\nb 丁 -1.0\n\
        a-x 甲戊 -1.0\nb-x 甲戊 -3.0\na-x 乙戊 -1.0\nb-x 乙戊 -1.5\na-x 丙戊 -1.0\nb-x 丙戊 -1.0\nx-x 戊戊 -2.0\n";

    fn mass_ratio(m: f64, mx: f64) -> f64 {
        ((m + 1e-9) / (mx + 1e-9)).log10()
    }

    /// A tie the multi-character words settle by at least one order of magnitude is broken (the least likely readings only); a smaller deduction, a
    /// character no word reads and a character with one reading stay at 0; the mass counts a (reading, word) once, at its largest row.
    #[test]
    fn mass_breaks_a_tie_of_at_least_one_order_of_magnitude() {
        assert_eq!(MASS_MIN_DEDUCTION, 1.0);
        let (lex, rd) = mk(TIES);
        close(rd.logp(&lex, "b", "甲").unwrap(), mass_ratio(1e-3, 1e-1));
        close(rd.logp(&lex, "a", "甲").unwrap(), 0.0);
        close(rd.term(&lex, "b", "甲"), LAMBDA * mass_ratio(1e-3, 1e-1));
        for (syl, w) in [("a", "乙"), ("b", "乙"), ("a", "丁"), ("b", "丁"), ("c", "丙")] {
            let want = if (syl, w) == ("c", "丙") { mass_ratio(0.0, 0.1) } else { 0.0 };
            close(rd.logp(&lex, syl, w).unwrap(), want);
        }
        // 丙's a and b tie at the largest mass: 0; c has no word: about -8
        close(rd.logp(&lex, "a", "丙").unwrap(), 0.0);
        close(rd.logp(&lex, "b", "丙").unwrap(), 0.0);
        assert!(rd.logp(&lex, "c", "丙").unwrap() < -7.9);
        // a (reading, word) repeated counts once at its largest row: b-x 甲戊 -3.0 and -4.0 count once, at 10^-3, not 10^-3 + 10^-4
        let (lex2, rd2) = mk(&format!("{TIES}b-x 甲戊 -4.0\nb-x 甲戊 -3.0\n"));
        close(rd2.logp(&lex2, "b", "甲").unwrap(), mass_ratio(1e-3, 1e-1));
        // a character whose readings are not all tied is not touched (甲's b is a plain lexicon difference), nor is a single-reading one
        let (lex3, rd3) = mk(&format!("{TIES}a 己 -1.0\nb 己 -2.0\na-x 己戊 -1.0\nb-x 己戊 -9.0\nd 庚 -1.0\nd-x 庚戊 -1.0\n"));
        close(rd3.logp(&lex3, "b", "己").unwrap(), -1.0);
        close(rd3.logp(&lex3, "d", "庚").unwrap(), 0.0);
    }

    /// The boundary: a deduction of exactly `MASS_MIN_DEDUCTION` is adopted, anything smaller is not; on either side of it by 1e-7 the table follows.
    #[test]
    fn a_deduction_of_exactly_one_is_adopted() {
        assert!(mass_adopts(-1.0) && mass_adopts(-1.0000001) && !mass_adopts(-0.9999999) && !mass_adopts(0.0));
        // reading b has no word, a's mass is 10^s: t(b) = log10(1e-9 / (10^s + 1e-9)) is -1.0000001 for s = -8.0457573796 and -0.9999999 for -8.0457576015
        for (s, adopted) in [("-8.0457573796", true), ("-8.0457576015", false)] {
            let (lex, rd) = mk(&format!("a 甲 -1.0\nb 甲 -1.0\na-x 甲戊 {s}\n"));
            assert_eq!(rd.logp(&lex, "b", "甲").unwrap() < -0.99, adopted, "{s}");
            assert_eq!(rd.logp(&lex, "b", "甲").unwrap() == 0.0, !adopted, "{s}");
        }
    }

    /// Readings that differ only in tone or neutral tone (a 聲調字) are not touched, however far the masses are apart; the readings of a 聲韻字 that
    /// differ in tone as well as in the body are all treated.
    #[test]
    fn a_tone_only_character_is_left_alone() {
        let tones = "ㄅㄚ 甲 -1.0\nㄅㄚˋ 甲 -1.0\nㄅㄚ˙ 甲 -1.0\nㄅㄚ-ㄇ 甲戊 -1.0\nㄅㄚˋ-ㄇ 甲戊 -5.0\n";
        let (lex, rd) = mk(tones);
        for syl in ["ㄅㄚ", "ㄅㄚˋ", "ㄅㄚ˙"] {
            close(rd.logp(&lex, syl, "甲").unwrap(), 0.0);
        }
        let (lex, rd) = mk(&format!("{tones}ㄆㄚˊ 甲 -1.0\nㄆㄚˊ-ㄇ 甲戊 -1.0\n"));
        close(rd.logp(&lex, "ㄅㄚˋ", "甲").unwrap(), mass_ratio(1e-5, 1e-1));
        close(rd.logp(&lex, "ㄅㄚ˙", "甲").unwrap(), mass_ratio(0.0, 1e-1));
        close(rd.logp(&lex, "ㄅㄚ", "甲").unwrap(), 0.0);
    }

    /// An exempt pair and a `SANDHI` reading stay 0 although their mass is the smallest (and they still count in the largest mass); a reading that is
    /// not tied (not all t 0) keeps the character out: 一's ㄧㄠ costs -8 in the lexicon, so 一's other readings are not estimated.
    #[test]
    fn exempt_and_sandhi_readings_stay_free() {
        let base = "ㄅㄨˋ 不 -1.0\nㄅㄨˊ 不 -1.0\nㄈㄡˇ 不 -1.0\nㄅㄨˋ-ㄇ 不戊 -1.0\nㄅㄨˊ-ㄇ 不戊 -4.0\nㄈㄡˇ-ㄇ 不戊 -4.0\n\
                    a 甲 -1.0\nb 甲 -1.0\nc 甲 -1.0\na-x 甲戊 -1.0\nb-x 甲戊 -4.0\nc-x 甲戊 -4.0\n";
        let (lex, rd) = built(base, None, "b\t甲\n");
        // sandhi: ㄅㄨˊ is 0 whatever its mass; the other low-mass reading is estimated
        close(rd.logp(&lex, "ㄅㄨˊ", "不").unwrap(), 0.0);
        close(rd.logp(&lex, "ㄈㄡˇ", "不").unwrap(), mass_ratio(1e-4, 1e-1));
        close(rd.logp(&lex, "ㄅㄨˋ", "不").unwrap(), 0.0);
        // exempt: b of 甲 is 0, c is estimated, a is the largest
        close(rd.logp(&lex, "b", "甲").unwrap(), 0.0);
        close(rd.logp(&lex, "c", "甲").unwrap(), mass_ratio(1e-4, 1e-1));
        // an untied character: 一 reads ㄧㄠ at -9 (t -8 in the lexicon), so its tied sandhi readings are left to the existing rule
        let (lex, rd) = mk("ㄧ 一 -1.0\nㄧˊ 一 -1.0\nㄧㄠ 一 -9.0\nㄧ-x 一戊 -1.0\nㄧˊ-x 一戊 -5.0\nㄧㄠ-x 一戊 -9.0\n");
        close(rd.logp(&lex, "ㄧˊ", "一").unwrap(), 0.0);
        close(rd.logp(&lex, "ㄧㄠ", "一").unwrap(), -8.0);
    }

    /// Without `with_mass` the table is the one before the rule, and the exemptions are read first: an exempt pair does not make a character untied.
    #[test]
    fn the_rule_follows_the_exemptions() {
        // 甲's b is -3.0 below a: not tied until it is exempt, and then the masses decide (c: m 0 against a's 10^-1: about -8)
        let base = "a 甲 -1.0\nb 甲 -4.0\nc 甲 -1.0\na-x 甲戊 -1.0\n";
        let rd = ReadingDecode::new(&Lexicon::parse(base).unwrap(), base);
        let lex = Lexicon::parse(base).unwrap();
        close(rd.logp(&lex, "c", "甲").unwrap(), 0.0);
        let (lex, rd) = built(base, None, "");
        close(rd.logp(&lex, "c", "甲").unwrap(), 0.0);
        close(rd.logp(&lex, "b", "甲").unwrap(), -3.0);
        let (lex, rd) = built(base, None, "b\t甲\n");
        assert!(rd.logp(&lex, "c", "甲").unwrap() < -7.9);
        close(rd.logp(&lex, "b", "甲").unwrap(), 0.0);
        close(rd.logp(&lex, "a", "甲").unwrap(), 0.0);
    }
}
