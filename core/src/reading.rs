//! The reading term of the decoder (docs/contracts/reading-decoder.md; exploration: experiments/r1-reading-decode/README.md, R1c to R1e): the
//! n-gram decoder adds `LAMBDA * max(t(r, w), -CAP)` to the path score of every single-character word w read r, and the candidate window orders
//! the one-syllable entries by it. t is P5's reading term (experiments/p5-reading-channel/p5.py `lex_logp`) with the two exceptions of r1.py
//! (`SANDHI`, `LEX_FLOOR` read as mc), the registered arm B-f of section R1c. The term is a function of the lexicon alone, so it is computed once
//! when the lexicon is loaded (`Lexicon::with_reading_terms`, called by `load_lexicon_packs`) into an array aligned with the entries of the
//! one-syllable readings, and the decoder and the window read it from there; nothing is looked up by string while decoding.
use crate::Lexicon;
use std::collections::HashMap;

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
    // The multi-character rows of `base`, one per (reading, word) at its largest score, in first-occurrence order. Only a row with a position
    // that adds to a floor character's mass is kept (the sum below skips every other position): the same sums, and the 150,000 other rows are never held
    // (keeping them cost about 18 MiB of peak RSS at load, measured).
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
        if !word.chars().zip(reading.split('-')).any(|(c, syl)| by_char.contains_key(&c) && lex.syl_ids.contains_key(syl)) {
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
}

impl ReadingDecode {
    pub fn new(lex: &Lexicon, base_text: &str) -> ReadingDecode {
        ReadingDecode { floor_mc: build_floor_mc(lex, base_text) }
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

/// `ReadingTerms::start` of a reading that is not a one-syllable one: it has no terms.
const NONE: u32 = u32::MAX;

/// The reading term of every entry of the one-syllable readings, in the order of `Lexicon::ents` (a multi-character entry of such a reading,
/// which only an overlay row can be, carries 0). `start[r]` is where reading r's entries begin in `terms`, `NONE` for a longer reading.
pub struct ReadingTerms {
    start: Vec<u32>,
    terms: Vec<f64>,
}

impl Lexicon {
    /// Builds the reading terms from this lexicon and `base_text`, the text of `mcbpmf-data.txt` (what the mc estimate of the floor readings
    /// is counted from, `ReadingDecode::new`). The term of an entry is `ReadingDecode::term`: it reads the raw lexicon scores, before capping,
    /// demotion and learning.
    pub fn with_reading_terms(mut self, base_text: &str) -> Lexicon {
        let rd = ReadingDecode::new(&self, base_text);
        let mut start = vec![NONE; self.readings.len()];
        let mut terms = Vec::new();
        for (r, reading) in self.readings.iter().enumerate().filter(|(_, x)| x.key_len == 1) {
            start[r] = terms.len() as u32;
            let syl = &self.syl_names[self.key_pool[reading.key_off as usize] as usize];
            terms.extend(self.range(r).map(|p| rd.term(&self, syl, self.word(p))));
        }
        self.rd = Some(ReadingTerms { start, terms });
        self
    }

    /// Where reading `r`'s terms begin in the table (`terms_of`, the capped lexicon's copy of it): `Some` for a one-syllable reading of a
    /// lexicon that has the table.
    pub(crate) fn reading_term_start(&self, r: usize) -> Option<usize> {
        self.rd.as_ref().map(|t| t.start[r]).filter(|&s| s != NONE).map(|s| s as usize)
    }

    /// The table as loaded, in `ents` order (empty without it); `CappedLexicon::new` copies it and sorts the copy with its entries.
    pub(crate) fn reading_terms(&self) -> &[f64] {
        self.rd.as_ref().map_or(&[], |t| &t.terms)
    }

    /// The candidate window's one-syllable list (docs/contracts/reading-decoder.md section 1, "Candidate window"): `entries(key)` for a key of
    /// one syllable with the entries ordered by raw score plus the reading term, best first, a stable sort (equal keys keep the lexicon's
    /// order). A longer key, and a lexicon without the table, give `entries(key)` unchanged. The one ordering of the window's l = 1 segment,
    /// of the English window's Chinese item and of the digit layer's second reading.
    pub fn entries_by_reading(&self, key: &[String]) -> Vec<(&str, f64)> {
        let Some(r) = self.find(&self.ids(key)) else { return Vec::new() };
        let mut list: Vec<(&str, f64)> = self.reading_entries(r).collect();
        if let Some(s) = self.reading_term_start(r) {
            let terms = &self.reading_terms()[s..s + list.len()];
            let mut keyed: Vec<(&str, f64, f64)> = list.iter().zip(terms).map(|(&(w, sc), t)| (w, sc, sc + t)).collect();
            keyed.sort_by(|a, b| b.2.partial_cmp(&a.2).unwrap_or(std::cmp::Ordering::Equal));
            list = keyed.into_iter().map(|(w, sc, _)| (w, sc)).collect();
        }
        list
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn close(a: f64, b: f64) {
        assert!((a - b).abs() < 1e-9, "{a} vs {b}");
    }

    fn mk(base: &str) -> (Lexicon, ReadingDecode) {
        let lex = Lexicon::parse(base).unwrap();
        let rd = ReadingDecode::new(&lex, base);
        (lex, rd)
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
        // the word has two one-syllable readings, so its own lookup would charge the rarer one (-8, clipped to -4); the guard is what makes it 0
        let lex = Lexicon::parse_with(base, Some("b\t甲乙\t-9.0\tx\na\t甲乙\t-1.0\tx\n")).unwrap();
        let rd = ReadingDecode::new(&lex, base);
        close(rd.term(&lex, "b", "甲"), LAMBDA * -2.0);
        close(rd.logp(&lex, "b", "甲乙").unwrap(), -8.0);
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

    /// The table built at load (`with_reading_terms`) is the term of each entry looked up by string: for every entry of every one-syllable reading, in
    /// the lexicon's order, whatever the entry is (a floor pair, a sandhi pair, a duplicate row, a multi-character overlay word); a longer reading has none.
    #[test]
    fn the_table_equals_the_lookup_by_string() {
        let base = "ㄧˊ 一 -5.0\nㄧ 一 -1.0\na 長 -1.0\nb 長 -6.8\nb-a 長長 -2.0\na 長 -4.0\nb 短 -1.0\nb 乙 -2.0\na 乙 -2.0\na-b 丙丁 -3.0\n";
        let lex = Lexicon::parse_with(base, Some("b\t長乙\t-2.5\tx\n")).unwrap().with_reading_terms(base);
        let rd = ReadingDecode::new(&lex, base);
        let (mut one, mut longer) = (0, 0);
        for r in 0..lex.readings.len() {
            match lex.reading_term_start(r) {
                Some(at) => {
                    let syl = &lex.syl_names[lex.key_pool[lex.readings[r].key_off as usize] as usize];
                    for (k, p) in lex.range(r).enumerate() {
                        assert_eq!(lex.reading_terms()[at + k].to_bits(), rd.term(&lex, syl, lex.word(p)).to_bits(), "{syl} {}", lex.word(p));
                        one += 1;
                    }
                }
                None => {
                    assert!(lex.readings[r].key_len != 1);
                    longer += 1;
                }
            }
        }
        assert!(one >= 9 && longer == 2, "{one} entries of one-syllable readings, {longer} longer readings");
        // the cases are really there: the sandhi reading is free, the floor reading takes mc, and the overlay word carries 0
        assert_eq!(rd.term(&lex, "ㄧˊ", "一"), 0.0);
        assert!(rd.term(&lex, "b", "長") < 0.0 && rd.term(&lex, "b", "長") > -LAMBDA * CAP);
        assert_eq!(rd.term(&lex, "b", "長乙"), 0.0);
        // a lexicon parsed bare has no table: every entry is 0 and the window order is the lexicon's
        let bare = Lexicon::parse(base).unwrap();
        assert!(bare.reading_term_start(0).is_none() && bare.reading_terms().is_empty());
    }

    /// The window's order (`Lexicon::entries_by_reading`): raw score plus the term, best first, stable (equal keys keep the lexicon's order), only a
    /// single-character entry's key moves (the multi-character overlay word keeps its raw score), the scores returned are the raw ones, and a key of
    /// more than one syllable, an unknown reading and a lexicon without the table give `entries`.
    #[test]
    fn the_window_order_is_score_plus_term_and_stable() {
        let base = "x a -0.5\ny a -0.6\nx b -0.1\ny b -0.55\ny c -0.55\ny d -0.55\nx-y ab -1.0\n";
        let key = |s: &str| s.split(' ').map(String::from).collect::<Vec<_>>();
        let lex = Lexicon::parse_with(base, Some("y\tbc\t-0.5\tx\n")).unwrap().with_reading_terms(base);
        fn words(v: Vec<(&str, f64)>) -> Vec<&str> {
            v.into_iter().map(|e| e.0).collect()
        }
        // raw: bc -0.5, then b, c, d tied at -0.55 in file order, a -0.6. Terms under y: a -0.1 * 2, b -0.45 * 2, c and d (one reading) 0.
        assert_eq!(words(lex.entries(&key("y"))), ["bc", "b", "c", "d", "a"]);
        assert_eq!(words(lex.entries_by_reading(&key("y"))), ["bc", "c", "d", "a", "b"]);
        let raw: HashMap<&str, f64> = lex.entries(&key("y")).into_iter().collect();
        assert!(lex.entries_by_reading(&key("y")).iter().all(|&(w, sc)| raw[w] == sc), "the scores are the raw ones");
        assert_eq!(lex.entries_by_reading(&key("x-y")), lex.entries(&key("x-y")));
        assert!(lex.entries_by_reading(&key("zz")).is_empty());
        let bare = Lexicon::parse_with(base, Some("y\tbc\t-0.5\tx\n")).unwrap();
        assert_eq!(bare.entries_by_reading(&key("y")), bare.entries(&key("y")));
    }
}
