//! Reading decode (docs/contracts/reading-decode-2.md; experiments/r1-reading-decode/README.md, sections R1d and R1g): the n-gram decoder adds a
//! reading term to the path score of every single-character word, `LAMBDA * max(t(r, w), -CAP)`, t being P5's reading term
//! (experiments/p5-reading-channel/p5.py `lex_logp`) with the exceptions of r1.py (`sandhi_exempt`, `floor_mc`) and the 12 pairs of
//! `data/lexicon/std-exempt.tsv` (R1g), the registered arm B-f of section R1c. The table is built by `engine::load_reading_decode` from the merged
//! lexicon and the data directory, and handed to the decoder by `CappedLexicon::with_reading`; the engine (`load_lm`), the evaluation CLI and the replay
//! all call that one function. A lexicon built without it decodes as before.
use crate::Lexicon;
use crate::eval::sha256_hex;
use std::collections::{HashMap, HashSet};

/// Weight of the reading term in the path score (R1 README, R1c: lambda 2.0 of the registered grid, arm B-f; R1d fixes it, no selection).
pub const LAMBDA: f64 = 2.0;
/// Clip of one single-character word's term: at least -CAP (R1 README: c 4, the clip of P5's cell `lex,1.0,4`).
pub const CAP: f64 = 4.0;
/// The score `data/lexicon/mcbpmf-data.txt` gives a single character's secondary reading it has no count for (r1.py `LEX_FLOOR`).
/// A pair at this score is estimated from the multi-character words instead (`ReadingDecode::new`).
pub const LEX_FLOOR: f64 = -6.8;
/// The tone-sandhi readings that cost nothing (r1.py `SANDHI`): 一 read ㄧˊ or ㄧˋ and 不 read ㄅㄨˊ are the standard readings before the
/// right tones (docs/contracts/s2r), not rare ones. Whatever the next syllable: the rule that picks the sandhi tone is not checked.
pub const SANDHI: [(&str, &str); 3] = [("ㄧˊ", "一"), ("ㄧˋ", "一"), ("ㄅㄨˊ", "不")];

fn is_floor(score: f64) -> bool {
    (score - LEX_FLOOR).abs() < 1e-9
}

/// The mc table of a lexicon (r1.py `floor_mc` over p5.py `mc_logp`), only for the single-character pairs at `LEX_FLOOR` (the only ones read):
/// M(r, w) = 10^s(r, w) + the mass 10^s of every multi-character word of `base` (the text of `mcbpmf-data.txt` alone, the same rows
/// `Lexicon::parse_with` reads), once per position whose character is w and whose syllable is r; the value is log10 M(r, w) minus the largest
/// over w's readings in `lex`. A (reading, word) that repeats in `base` counts once, at its largest score; the masses add up in the order the
/// pairs first occur in the file, which is the order p5.py sums in. Keyed by (syllable id, character).
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
    // The multi-character rows of `base`, one per (reading, word) at its largest score, in first-occurrence order.
    let (mut at, mut rows): (HashMap<(&str, &str), usize>, Vec<(&str, &str, f64)>) = (HashMap::new(), Vec::new());
    for l in base.lines() {
        if l.starts_with(['#', '_']) {
            continue;
        }
        let mut it = l.split_whitespace();
        let (Some(reading), Some(word), Some(score), None) = (it.next(), it.next(), it.next(), it.next()) else { continue };
        let n = word.chars().count();
        if n < 2 || n != reading.split('-').count() {
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

/// What the decoder needs for the reading term besides the lexicon: the mc values of the floor pairs. Built once from the lexicon and the text of
/// `mcbpmf-data.txt`, read-only afterwards.
pub struct ReadingDecode {
    floor_mc: HashMap<(u32, char), f64>,
    /// (syllable, character) pairs whose term is 0 like a `SANDHI` reading (R1g, `std-exempt.tsv`); empty until `with_exempt`.
    exempt: HashSet<(String, char)>,
    /// First 8 hex of the SHA-256 of the exempt list's text (`with_exempt`), for the summary tag; empty until then.
    exempt_sha8: String,
}

impl ReadingDecode {
    pub fn new(lex: &Lexicon, base_text: &str) -> ReadingDecode {
        ReadingDecode { floor_mc: build_floor_mc(lex, base_text), exempt: HashSet::new(), exempt_sha8: String::new() }
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

    /// The summary-line tag of a run that decodes with this table: `+rd`, then `+rx:<sha8 of the exempt list>` once `with_exempt` gave one
    /// (the CLI and the replay print it at the end of every setting's tag).
    pub fn tag(&self) -> String {
        if self.exempt_sha8.is_empty() { "+rd".into() } else { format!("+rd+rx:{}", self.exempt_sha8) }
    }

    /// log10 P(reading | word) of the single-character word `word` read `syl`, relative to the word's best reading (<= 0): the lexicon's score
    /// minus the word's best score (p5.py `lex_logp`), 0 for a `SANDHI` reading, the mc value for a pair at `LEX_FLOOR`. `None` when the
    /// lexicon has no such pair. The scores are the raw lexicon's (largest over duplicate rows), before capping, demotion and learning.
    pub fn logp(&self, lex: &Lexicon, syl: &str, word: &str) -> Option<f64> {
        let (s, best) = (lex.syllable_score(syl, word)?, lex.best_word_score(word)?);
        let mut t = s - best;
        if SANDHI.contains(&(syl, word)) {
            t = 0.0;
        }
        if is_floor(s) {
            let mc = lex.syl_ids.get(syl).zip(word.chars().next()).and_then(|(&id, c)| self.floor_mc.get(&(id, c)));
            t = mc.copied().unwrap_or(t);
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

    /// The tag names the exempt list: `+rd+rx:` and the first 8 hex of the SHA-256 of its text.
    #[test]
    fn the_tag_carries_the_exempt_lists_hash() {
        let (_, rd) = built(LEX, None, "b\t甲\n");
        assert_eq!(rd.tag(), format!("+rd+rx:{}", &crate::eval::sha256_hex("b\t甲\n".as_bytes())[..8]));
        assert_eq!(ReadingDecode::new(&Lexicon::parse(LEX).unwrap(), LEX).tag(), "+rd");
    }
}
