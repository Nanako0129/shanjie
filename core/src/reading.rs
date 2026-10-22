//! Reading decode, evaluation only (experiments/r1-reading-decode/README.md, section "R1d"): the n-gram decoder adds a reading term to the path
//! score of every single-character word, `LAMBDA * max(t(r, w), -CAP)`, t being P5's reading term (experiments/p5-reading-channel/p5.py
//! `lex_logp`) with the two exceptions of r1.py (`sandhi_exempt`, `floor_mc`), the registered arm B-f of section R1c. It is switched on only by
//! `CappedLexicon::with_reading_decode` (the CLI's `--reading-decode`); the engine never calls it, and without it the decoder's arithmetic is untouched.
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

    /// The term one single-character word adds to a path: `LAMBDA * max(logp, -CAP)`; 0 for a pair the lexicon lacks. A word of more than one
    /// character never reaches this (the decoder asks for spans of one syllable only).
    pub fn term(&self, lex: &Lexicon, syl: &str, word: &str) -> f64 {
        self.logp(lex, syl, word).map_or(0.0, |t| LAMBDA * t.max(-CAP))
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
}
