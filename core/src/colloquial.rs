//! The colloquial term of the small-model rerank (docs/contracts/colloquial-term.md): a second, plain bigram table (the
//! colloquial table, SJ-CL) as a log-linear term, minus a penalty for overlay words. Definitions are those of
//! experiments/p4-colloquial/p4.py, word for word; the fusion that uses them is `TinyLm::rerank_colloquial`.
//!
//! The reading-channel term (docs/contracts/reading-channel.md) rides on the same fusion: `channel_of` is p5.py's `lex` estimator, `NU` and `CAP`
//! its cell `lex,1.0,4` (experiments/p5-reading-channel/README.md); `fused_pick` adds it after `fuse` when it is given.
//!
//! The constants MU, GATE_T and gamma = mu / 2 are the cell P4d registered and chatjudge-2 confirmed (experiments/p4d-colloquial/README.md). The
//! three (alpha, tau, delta) cells, one per strength (docs/contracts/colloquial-levels.md), are the ones docs/contracts/sj-lm-7.md section 2.9
//! selected for the SJ-LM-7 base; none of them comes from the `.sjtl` header.
use crate::lm::{Lm, LmError, Profile};
use crate::tinylm::{pick_masked, RerankLevel, TinyLm};
use crate::Lexicon;
use std::collections::HashSet;
use std::fmt;
use std::io::BufRead;
use std::path::Path;

/// Weight of lc (P4d registered cell).
pub const MU: f64 = 0.5;
/// Softness of the gate g_i = sigmoid((ll_i - ll_0) / GATE_T) (P4d registered cell).
pub const GATE_T: f64 = 1.0;
/// gamma / mu: col = lc - GAMMA_PER_MU * k, i.e. gamma = mu / 2 (P4d: P4c's mu 0.5 cell).
pub const GAMMA_PER_MU: f64 = 0.5;
/// The fusion's (alpha, tau, delta) under the colloquial term at the standard strength: the cell docs/contracts/sj-lm-7.md section 2.9
/// selected on chatclause (n 5,761; 24-cell grid fixed beforehand) for the SJ-LM-7 base, rule "F - 2B largest with F - 2B >= 0": F 167, B 44
/// against the n-gram first choice (docs/research-log.md 2026-10-26, "定案的選值結果"). The three cells here are the chat fusion at each
/// strength; the `.sjtl` header's chat triples only back a col that is not finite.
pub const STANDARD: (f64, f64, f64) = (0.7, 2.0, 0.0);
/// The same at the conservative strength: the section 2.9 rule "B <= half the standard's B, F > B, F - 4B largest": F 67, B 9.
pub const CONSERVATIVE: (f64, f64, f64) = (0.7, 2.0, 1.0);
/// The same at the aggressive strength: the section 2.9 rule "F above the standard's, F - B no lower, F - 2B largest": F 180, B 57.
pub const AGGRESSIVE: (f64, f64, f64) = (1.0, 2.0, 0.1);

/// The (alpha, tau, delta) of the fusion at `level` (docs/contracts/colloquial-levels.md section 1).
pub fn cell(level: RerankLevel) -> (f64, f64, f64) {
    match level {
        RerankLevel::Conservative => CONSERVATIVE,
        RerankLevel::Standard => STANDARD,
        RerankLevel::Aggressive => AGGRESSIVE,
    }
}

/// The reading-channel term's weight: ll_eff gains (NU / alpha) * (ch_i - ch_0), alpha being the level's (P5 registered cell `lex,1.0,4`, selected on chatclause).
pub const NU: f64 = 1.0;
/// The reading-channel term's clip: each single-character word contributes at least -CAP (P5 registered cell `lex,1.0,4`).
pub const CAP: f64 = 4.0;

/// SHA-256 of the `.sjtl` the cells were measured with (SJ-SR-7 R1 final file, data/tinylm.sjtl.sha256); the term applies to no other small model.
pub const PAIRED_TINYLM_SHA256: &str = "bbb387d06625d50de24cb1ed4bfc819bc312dbe68dc5d38b2451eed4d44861a8";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ColloquialError {
    /// The table file could not be read or is malformed.
    Table,
    /// A row of the base lexicon has a score that is not a number.
    Lexicon,
    /// `mcbpmf-data.txt` or `overlay-add.tsv` could not be read (an I/O error or text that is not UTF-8).
    Read,
}

impl fmt::Display for ColloquialError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            ColloquialError::Table => "cannot load the colloquial table (.sjlm)",
            ColloquialError::Lexicon => "mcbpmf-data.txt has a row whose score is not a number",
            ColloquialError::Read => "cannot read mcbpmf-data.txt or overlay-add.tsv",
        })
    }
}
impl std::error::Error for ColloquialError {}

pub struct Colloquial {
    lm: Lm,
    /// N + V + 1 of the table, V counting `<s>` and `</s>` (p4.py `Table.n1`).
    n1: f64,
    /// The words of overlay-add.tsv and of the packs the engine was built with (not names-add.tsv, not sandhi-add.tsv) that mcbpmf-data.txt
    /// lacks: the only words k counts, so the two full sets are never kept (keeping them cost about 10 MiB more peak RSS on a Mac, measured:
    /// docs/research-log.md, the section 3.5 memory fix).
    overlay_only: HashSet<String>,
}

impl Colloquial {
    /// The table at `table` read as a plain bigram (no classes, no Kneser-Ney, no trigram, whatever sits beside the file), and the set of
    /// k, with the filters of tools/triage.py `Lex` / experiments/diag-near-miss/d23.py `load_lex_scores`: base rows (`base_text`,
    /// mcbpmf-data.txt) have three whitespace fields, no `#` / `_` first character, one syllable per character, and a numeric score (a row
    /// whose score is not a number is an error, as in the engine); overlay rows (`overlay`, overlay-add.tsv, read line by line, then
    /// `pack_text`, the pack rows the engine was built with) are tab lines with exactly four fields.
    ///
    /// Memory: only the words that are in the overlay and not in the base are kept. The base words are a short-lived set that borrows
    /// from `base_text`, and the 17 MB overlay file is never held as text (peak RSS with the table: +27 to +30 MiB on a Mac before this,
    /// +16 to +21 MiB after; 3 runs each, `/usr/bin/time -l`, docs/research-log.md).
    pub fn load(table: &Path, base_text: &str, overlay: impl BufRead, pack_text: Option<&str>) -> Result<Colloquial, ColloquialError> {
        let lm = Lm::load_opts(table, false, false, &[]).map_err(|_: LmError| ColloquialError::Table)?;
        // `Lm` has no vocabulary-size accessor: `identity().1` is V + 1 (the length of the word-offset table), so N + V + 1 is
        // N + identity().1.
        let n1 = (lm.total() + lm.identity().1 as u64) as f64;
        let mut base: HashSet<&str> = HashSet::new();
        for l in base_text.lines() {
            let f: Vec<&str> = l.split_whitespace().collect();
            if l.starts_with(['#', '_']) || f.len() != 3 || f[1].chars().count() != f[0].split('-').count() {
                continue;
            }
            f[2].parse::<f64>().map_err(|_| ColloquialError::Lexicon)?;
            base.insert(f[1]);
        }
        let mut overlay_only = HashSet::new();
        let mut take = |l: &str| {
            let f: Vec<&str> = l.split('\t').collect();
            if f.len() == 4 && !base.contains(f[1]) {
                overlay_only.insert(f[1].to_string());
            }
        };
        for l in overlay.lines() {
            take(&l.map_err(|_| ColloquialError::Read)?);
        }
        pack_text.unwrap_or("").lines().for_each(&mut take);
        Ok(Colloquial { lm, n1, overlay_only })
    }

    /// `load` with the base lexicon and the overlay read from `lexicon_dir` (`mcbpmf-data.txt`, `overlay-add.tsv`): the one place the engine
    /// (`Engine::load_colloquial`) and the evaluation CLI (`--colloquial-core`) build the word sets of k, so they cannot differ.
    /// `pack_text` is the pack rows the caller's lexicon was built with.
    pub fn load_from_dir(table: &Path, lexicon_dir: &Path, pack_text: Option<&str>) -> Result<Colloquial, ColloquialError> {
        let base = std::fs::read_to_string(lexicon_dir.join("mcbpmf-data.txt")).map_err(|_| ColloquialError::Read)?;
        let overlay = std::fs::File::open(lexicon_dir.join("overlay-add.tsv")).map_err(|_| ColloquialError::Read)?;
        Colloquial::load(table, &base, std::io::BufReader::new(overlay), pack_text)
    }

    /// SHA-256 (hex) of the table file.
    pub fn sha256(&self) -> &str {
        self.lm.sha256()
    }

    /// lc: sum of log10 P(w_i | w_{i-1}) from `<s>`, plus log10 P(`</s>` | w_k). P backs off to the table's own (c(w) + 1) / (N + V + 1)
    /// (0 for a word outside the table); the end term backs off to the table's end-of-sentence rate (`Lm::eos`, lam 1).
    pub fn lc<S: AsRef<str>>(&self, words: &[S]) -> f64 {
        let (mut prev, mut tot) = ("<s>", 0.0);
        for w in words {
            let w = w.as_ref();
            let pb = (self.lm.count(w) + 1) as f64 / self.n1;
            tot += self.lm.prob(prev, w, pb).log10();
            prev = w;
        }
        tot + self.lm.eos(1.0, prev)
    }

    /// k: words of the split in the overlay set and not in the base lexicon, repeats counted.
    pub fn k<S: AsRef<str>>(&self, words: &[S]) -> usize {
        words.iter().filter(|w| self.overlay_only.contains(w.as_ref())).count()
    }

    /// col = lc - GAMMA_PER_MU * k (the P4d colloquial score of one candidate's word split).
    pub fn col<S: AsRef<str>>(&self, words: &[S]) -> f64 {
        self.lc(words) - GAMMA_PER_MU * self.k(words) as f64
    }
}

/// 1 / (1 + e^-x) without overflow for large |x| (p4d.py `sigmoid`).
pub fn sigmoid(x: f64) -> f64 {
    if x >= 0.0 {
        1.0 / (1.0 + (-x).exp())
    } else {
        let e = x.exp();
        e / (1.0 + e)
    }
}

/// The fused small-model scores of one n-best list: ll_eff_i = ll_i + g_i * (MU / alpha) * (col_i - col_0) with
/// g_i = sigmoid((ll_i - ll_0) / GATE_T), `alpha` being the level's. The weight of the term in the fused score (alpha * ll_eff) is MU * g_i *
/// (col_i - col_0) whatever alpha is. Candidate 0 is the anchor (its own term is 0), and the gate reads ll_0, never ll_i alone.
pub fn fuse(ll: &[f64], col: &[f64], alpha: f64) -> Vec<f64> {
    (0..ll.len()).map(|i| ll[i] + sigmoid((ll[i] - ll[0]) / GATE_T) * (MU / alpha) * (col[i] - col[0])).collect()
}

/// Whether the term is used for a rerank (docs/contracts/colloquial-term.md section 2.2, docs/contracts/colloquial-levels.md): the chat profile,
/// any of the three strengths (so no level parameter; each strength has its own `cell`) and the small model the registered cells were measured with (`PAIRED_TINYLM_SHA256`).
/// The fourth condition, a loaded table, is the caller having one. The engine (per job) and the evaluation CLI both decide with this function.
pub fn applies(profile: Profile, model: &TinyLm) -> bool {
    profile == Profile::Chat && model.file_sha256() == PAIRED_TINYLM_SHA256
}

/// What the reading-channel term reads besides the words of a path: the engine's lexicon and the stretch's syllables (one per character).
#[derive(Clone, Copy)]
pub struct ReadingChannel<'a> {
    pub lex: &'a Lexicon,
    pub syls: &'a [String],
}

/// ch of one candidate's word split (p5.py `word_terms` / `ch_of`, estimator `lex`): the syllables are cut over the words in order, one per
/// character; each single-character word w read r adds max(s(r, w) - max_r' s(r', w), -CAP), s being the lexicon's score (largest over
/// duplicate rows, before demotion and learning). A multi-character word adds 0, a single-character (r, w) the lexicon lacks adds 0, and
/// words that do not cover the syllables exactly make the whole path 0.
///
/// Guarantees that hold by type, so no test can observe them: s is read from the raw `Lexicon` (demotion lives in `CappedLexicon`, a separate
/// type, and learning never mutates the `Lexicon`), so it is before demotion and learning. The `len(w) == 1` guard states the contract; removing it
/// is an equivalent mutant (a one-syllable lookup never holds a multi-character word). Cost, measured on the cloud VM at 093fc11c (CLI --threads 1,
/// dev302 chat, the `tinylm: rerank` line): p95 248.844 ms without the term, 248.740 ms with it; replay dev302 peak RSS 436,144 vs 436,148 KB, so
/// the lookups are not memoized.
pub fn channel_of<S: AsRef<str>>(lex: &Lexicon, syls: &[String], words: &[S]) -> f64 {
    let len = |w: &str| w.chars().count();
    if words.iter().map(|w| len(w.as_ref())).sum::<usize>() != syls.len() {
        return 0.0;
    }
    let (mut at, mut ch) = (0, 0.0);
    for w in words {
        let w = w.as_ref();
        if len(w) == 1 {
            if let (Some(s), Some(best)) = (lex.syllable_score(&syls[at], w), lex.best_word_score(w)) {
                ch += (s - best).max(-CAP);
            }
        }
        at += len(w);
    }
    ch
}

/// The index `rerank_colloquial` chooses: `pick` over the fused scores with `cell`, the level's (alpha, tau, delta) (never the `.sjtl`
/// header's). `ng`, `ll`, `col` and `ch` (the reading-channel term, None for the colloquial term alone) are the first candidates of one
/// n-best list, in the n-gram order: ll_eff_i = `fuse` + (NU / alpha) * (ch_i - ch_0).
pub fn fused_pick(ng: &[f64], ll: &[f64], col: &[f64], ch: Option<&[f64]>, cell: (f64, f64, f64), masked: &[bool]) -> usize {
    let (alpha, tau, delta) = cell;
    let mut eff = fuse(ll, col, alpha);
    if let Some(ch) = ch {
        // Candidate 0 is the anchor: its own term is 0, as in `fuse`.
        for (e, c) in eff.iter_mut().zip(ch).skip(1) {
            *e += (NU / alpha) * (c - ch[0]);
        }
    }
    pick_masked(ng, &eff, alpha, tau, delta, masked)
}

/// `fused_pick` with `cell`, unless a col is not finite (a word the table scores as log10 0, or a NaN): then no colloquial term at all and no
/// reading-channel term, the plain `pick` with `plain` (the `.sjtl` header's alpha, tau, delta of the same level), so an infinity or NaN never decides.
pub fn guarded_pick(
    ng: &[f64], ll: &[f64], col: &[f64], ch: Option<&[f64]>, cell: (f64, f64, f64), plain: (f64, f64, f64), masked: &[bool],
) -> usize {
    if col.iter().all(|c| c.is_finite()) {
        return fused_pick(ng, ll, col, ch, cell, masked);
    }
    let (alpha, tau, delta) = plain;
    pick_masked(ng, ll, alpha, tau, delta, masked)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tinylm::pick;

    fn close(a: f64, b: f64) {
        assert!((a - b).abs() < 1e-12, "{a} vs {b}");
    }

    /// The `.sjtl` header's chat standard triple of the SJ-SR-6 files (the fallback of a col that is not finite): not one of the three cells.
    const HEADER_STANDARD: (f64, f64, f64) = (0.3, 2.0, 0.25);

    #[test]
    fn sigmoid_does_not_overflow() {
        close(sigmoid(0.0), 0.5);
        close(sigmoid(2.0), 1.0 / (1.0 + (-2.0f64).exp()));
        assert_eq!(sigmoid(2000.0), 1.0);
        assert_eq!(sigmoid(-2000.0), 0.0);
        assert!(sigmoid(-800.0).is_finite() && sigmoid(800.0).is_finite());
    }

    /// The term is a difference to the anchor: col_0 drops out, a candidate equal to the anchor gets nothing, and the gate reads ll_0.
    #[test]
    fn fuse_is_relative_to_the_anchor_and_gated_by_ll_0() {
        let (ll, col) = ([-10.0, -9.0, -12.0], [-5.0, -4.0, -3.0]);
        let k = MU / STANDARD.0;
        let eff = fuse(&ll, &col, STANDARD.0);
        close(eff[0], -10.0);
        close(eff[1], -9.0 + sigmoid(1.0) * k * 1.0);
        close(eff[2], -12.0 + sigmoid(-2.0) * k * 2.0);
        // adding a constant to every col changes nothing (relative to the anchor)
        let shifted = fuse(&ll, &col.map(|c| c + 100.0), STANDARD.0);
        for (a, b) in eff.iter().zip(&shifted) {
            close(*a, *b);
        }
        // an extreme gap saturates the gate instead of overflowing
        let eff = fuse(&[-1.0, -5000.0], &[0.0, 40.0], STANDARD.0);
        assert!(eff.iter().all(|x| x.is_finite()));
    }

    /// The registered cell decides, not the header triple: with ng [-10.0, -10.3] and ll [-5.0, -3.8], candidate 1 gains 0.7 * 1.2 - 0.3 = 0.54 at the
    /// standard alpha 0.7 (delta 0 takes it; the conservative delta 1 does not) but 0.3 * 1.2 - 0.3 = 0.06 at the header's alpha 0.3 (below its delta).
    #[test]
    fn fused_pick_uses_the_registered_cell() {
        assert_eq!(fused_pick(&[-10.0, -10.3], &[-5.0, -3.8], &[0.0, 0.0], None, STANDARD, &[]), 1, "alpha 0.7: gain 0.54 >= delta 0");
        assert_eq!(fused_pick(&[-10.0, -10.3], &[-5.0, -3.8], &[0.0, 0.0], None, CONSERVATIVE, &[]), 0, "gain 0.54 < delta 1");
        assert_eq!(pick(&[-10.0, -10.3], &[-5.0, -3.8], HEADER_STANDARD.0, HEADER_STANDARD.1, HEADER_STANDARD.2), 0, "premise: the header triple keeps the first");
        assert_eq!(fused_pick(&[-10.0, -10.3], &[-5.0, -4.7], &[0.0, 0.0], None, STANDARD, &[]), 0, "gain 0.7 * 0.3 - 0.3 < delta 0");
        // tau: an n-gram margin of 2 or more keeps the first whatever the small model says
        assert_eq!(fused_pick(&[-10.0, -12.0], &[-50.0, 0.0], &[0.0, 0.0], None, STANDARD, &[]), 0);
        assert_eq!(fused_pick(&[-10.0, -11.9], &[-50.0, 0.0], &[0.0, 0.0], None, STANDARD, &[]), 1);
    }

    /// A col that is +inf, -inf or NaN (the anchor's or another candidate's) must not decide: the plain pick with the given triple is used.
    /// With ng [-10.0, -10.3], ll [-5.0, -3.8] the plain (header) triple (0.3, 2, 0.25) keeps candidate 0; a +inf col would make the fused score of
    /// candidate 1 infinite (and a -inf anchor col makes col_1 - col_0 infinite) and so pick it.
    #[test]
    fn non_finite_col_falls_back_to_the_plain_pick() {
        let (ng, ll, plain) = ([-10.0, -10.3], [-5.0, -3.8], HEADER_STANDARD);
        assert_eq!(fused_pick(&ng, &ll, &[0.0, f64::INFINITY], None, STANDARD, &[]), 1, "premise: unguarded, +inf decides");
        assert_eq!(guarded_pick(&ng, &ll, &[0.0, f64::INFINITY], None, STANDARD, plain, &[]), 0);
        assert_eq!(guarded_pick(&ng, &ll, &[f64::NEG_INFINITY, 0.0], None, STANDARD, plain, &[]), 0, "-inf anchor");
        assert_eq!(guarded_pick(&ng, &ll, &[0.0, f64::NAN], None, STANDARD, plain, &[]), 0);
        assert_eq!(guarded_pick(&ng, &ll, &[0.0, 0.0], None, STANDARD, plain, &[]), fused_pick(&ng, &ll, &[0.0, 0.0], None, STANDARD, &[]), "finite cols: the term as before");
    }

    /// The reading channel's lexicon: 甲 has three readings (a -1.0 twice over, b -3.0, c -9.0: the duplicate row a -5.0 loses to a -1.0), 乙 ties
    /// under a and b, 丁 has one reading, and 甲甲 is a two-character word whose characters would score -2 each if they were counted.
    const LEX: &str = "a 甲 -1.0\nb 甲 -3.0\nc 甲 -9.0\na 甲 -5.0\na 乙 -2.0\nb 乙 -2.0\nd 丁 -7.0\na-a 甲甲 -5.0\n";

    fn ch(syls: &[&str], words: &[&str]) -> f64 {
        let lex = Lexicon::parse(LEX).unwrap();
        channel_of(&lex, &syls.iter().map(|s| s.to_string()).collect::<Vec<_>>(), words)
    }

    /// A single-character word is scored against its own best reading (the largest over its rows, duplicates included); a reading tied with
    /// the best, or the only one, is 0. Rule: t = s(r, w) - max_r' s(r', w).
    #[test]
    fn channel_is_relative_to_the_words_best_reading() {
        close(ch(&["b"], &["甲"]), -2.0);
        close(ch(&["a"], &["甲"]), 0.0);
        close(ch(&["a"], &["乙"]), 0.0);
        close(ch(&["b"], &["乙"]), 0.0);
        close(ch(&["d"], &["丁"]), 0.0);
    }

    /// Each word's term is clipped at -CAP before the sum (-8 becomes -4) and the terms add up.
    #[test]
    fn channel_clips_each_word_at_the_cap_and_sums() {
        assert_eq!(CAP, 4.0);
        close(ch(&["c"], &["甲"]), -4.0);
        close(ch(&["c", "b"], &["甲", "甲"]), -6.0);
        close(ch(&["b", "b", "b"], &["甲", "甲", "甲"]), -6.0);
    }

    /// A multi-character word adds nothing, whatever its characters would score alone.
    #[test]
    fn channel_counts_single_character_words_only() {
        close(ch(&["b", "b"], &["甲甲"]), 0.0);
        close(ch(&["b", "a", "a"], &["甲", "甲甲"]), -2.0);
        close(ch(&["b", "b"], &["甲", "甲"]), -4.0);
    }

    /// A single-character (r, w) the lexicon lacks adds 0 (an unknown syllable, a reading of the syllable that has no such word, a word it
    /// lacks), and the other words of the path still count.
    #[test]
    fn channel_missing_pair_is_zero() {
        close(ch(&["z"], &["甲"]), 0.0);
        close(ch(&["a"], &["丁"]), 0.0);
        close(ch(&["a"], &["戊"]), 0.0);
        close(ch(&["a", "b"], &["丁", "甲"]), -2.0);
    }

    /// Words that do not cover the syllables exactly (too many or too few characters) make the whole path 0, the words that would score
    /// included.
    #[test]
    fn channel_syllable_mismatch_zeroes_the_whole_path() {
        close(ch(&["b", "b"], &["甲", "甲"]), -4.0);
        close(ch(&["b"], &["甲", "甲"]), 0.0);
        close(ch(&["b", "b", "b"], &["甲", "甲"]), 0.0);
        close(ch(&["b", "b"], &["甲"]), 0.0);
    }

    /// ll_eff_i = fuse + (NU / alpha) (ch_i - ch_0): the term enters the pick's score as NU * d whatever the level's alpha. With ng [-10.0, -10.3] and equal
    /// ll, candidate 1 gains NU * d - 0.3, so each cell has a gap d that stays under its delta and one that passes it (standard delta 0: 0.2 / 0.4;
    /// conservative delta 1: 1.2 / 1.4; aggressive delta 0.1 at alpha 1.0: 0.3 / 0.5). A factor taken from another level's alpha moves the thresholds.
    #[test]
    fn channel_factor_is_nu_over_alpha() {
        let (ng, ll, col) = ([-10.0, -10.3], [-5.0, -5.0], [0.0, 0.0]);
        assert_eq!(NU, 1.0);
        for (cell, under, over) in [(STANDARD, 0.2, 0.4), (CONSERVATIVE, 1.2, 1.4), (AGGRESSIVE, 0.3, 0.5)] {
            assert_eq!(fused_pick(&ng, &ll, &col, Some(&[0.0, under]), cell, &[]), 0, "{cell:?} under");
            assert_eq!(fused_pick(&ng, &ll, &col, Some(&[0.0, over]), cell, &[]), 1, "{cell:?} over");
            assert_eq!(fused_pick(&ng, &ll, &col, None, cell, &[]), 0, "premise: without the term candidate 1 loses");
        }
    }

    /// The term is a difference to the anchor: adding a constant to every ch changes nothing, and a candidate's gap to ch_0 decides (-2.4 against
    /// the anchor's -3.0 is the same gap of 0.6 as above).
    #[test]
    fn channel_is_relative_to_the_anchor() {
        let (ng, ll, col) = ([-10.0, -10.3], [-5.0, -5.0], [0.0, 0.0]);
        assert_eq!(fused_pick(&ng, &ll, &col, Some(&[-3.0, -2.4]), STANDARD, &[]), 1);
        assert_eq!(fused_pick(&ng, &ll, &col, Some(&[97.0, 97.6]), STANDARD, &[]), 1);
        assert_eq!(fused_pick(&ng, &ll, &col, Some(&[-3.0, -3.0]), STANDARD, &[]), 0);
    }

    /// A non-finite col drops the reading-channel term with the colloquial term: the plain pick, whatever ch says.
    #[test]
    fn non_finite_col_drops_the_channel_too() {
        let (ng, ll, plain) = ([-10.0, -10.3], [-5.0, -5.0], HEADER_STANDARD);
        assert_eq!(guarded_pick(&ng, &ll, &[0.0, 0.0], Some(&[0.0, 5.0]), STANDARD, plain, &[]), 1, "premise: finite cols, the term decides");
        assert_eq!(guarded_pick(&ng, &ll, &[0.0, f64::NAN], Some(&[0.0, 5.0]), STANDARD, plain, &[]), 0);
    }

    /// colloquial-levels section 2.1: each level's `fused_pick` uses its own (alpha, tau, delta). With no colloquial or channel term (equal col) a
    /// candidate gains alpha * dll - dng over candidate 0. List 1 (dng 1.0, dll 1.2) gains 0.84 - 1.0 = -0.16 at the standard alpha 0.7 (below delta 0)
    /// and 0.2 at the aggressive alpha 1.0 (delta 0.1 takes it); list 2 (dng 0.4) gains 0.44, which the standard delta 0 takes and the conservative
    /// delta 1 does not (aggressive 0.8); list 3 (dng 1.9, dll 5) gains 1.6, 1.6 and 3.1: all three take it.
    #[test]
    fn each_level_picks_with_its_own_cell() {
        let lists = [([0.0, -1.0], [0.0, 1.2]), ([0.0, -0.4], [0.0, 1.2]), ([0.0, -1.9], [0.0, 5.0])];
        let by_level = |l| lists.map(|(ng, ll)| fused_pick(&ng, &ll, &[0.0; 2], None, cell(l), &[]));
        assert_eq!(by_level(RerankLevel::Standard), [0, 1, 1]);
        assert_eq!(by_level(RerankLevel::Conservative), [0, 0, 1]);
        assert_eq!(by_level(RerankLevel::Aggressive), [1, 1, 1]);
        assert_eq!(STANDARD, (0.7, 2.0, 0.0));
        assert_eq!(CONSERVATIVE, (0.7, 2.0, 1.0));
        assert_eq!(AGGRESSIVE, (1.0, 2.0, 0.1));
        assert_eq!([cell(RerankLevel::Standard), cell(RerankLevel::Conservative), cell(RerankLevel::Aggressive)], [STANDARD, CONSERVATIVE, AGGRESSIVE]);
    }

    /// The colloquial and reading-channel terms weigh the same in the fused score at every alpha (s = ng + alpha * ll + g * MU * dcol + NU * dch), so
    /// the coefficients in ll_eff are MU / alpha and NU / alpha of the level's alpha. With ng [0, -0.5] and ll equal (gate 0.5) candidate 1 gains
    /// -0.5 + 0.25 * dcol, or -0.5 + dch: it passes a level's delta at dcol = 2 + 4 * delta and at dch = 0.5 + delta, whatever the level's alpha (a
    /// divisor from another level's alpha moves the thresholds); each cell is checked just below and just above.
    #[test]
    fn the_terms_weigh_the_same_at_every_alpha() {
        let (ng, ll) = ([0.0, -0.5], [-5.0, -5.0]);
        for cell in [STANDARD, CONSERVATIVE, AGGRESSIVE] {
            let (col_at, ch_at) = (2.0 + 4.0 * cell.2, 0.5 + cell.2);
            let c = |col: f64, ch: f64| fused_pick(&ng, &ll, &[0.0, col], Some(&[0.0, ch]), cell, &[]);
            assert_eq!([c(col_at - 0.2, 0.0), c(col_at + 0.2, 0.0)], [0, 1], "the colloquial term {cell:?}");
            assert_eq!([c(0.0, ch_at - 0.05), c(0.0, ch_at + 0.05)], [0, 1], "the reading-channel term {cell:?}");
        }
        // fuse itself: the coefficient is MU / alpha with the alpha given
        let eff = fuse(&[-5.0, -5.0], &[0.0, 10.0], 1.0);
        close(eff[1], -5.0 + sigmoid(0.0) * (MU / 1.0) * 10.0);
    }

    /// docs/contracts/sj-lm-7-pr2.md section 2.7, test 5: the mask holds in `guarded_pick`'s fallback too. A non-finite col makes the plain `pick` decide (here with
    /// triple (1, 2, 0): candidate 1 scores -0.5 against -5 and wins, candidate 2 -4 would win without candidate 1); the fallback never goes to a masked candidate,
    /// and the fused path (finite cols) masks the same way.
    #[test]
    fn the_fallback_does_not_fall_back_to_a_masked_candidate() {
        let (ng, ll, plain) = ([0.0, -0.5, -3.0], [-5.0, 0.0, -1.0], (1.0, 2.0, 0.0));
        let nan = [0.0, f64::NAN, 0.0];
        assert_eq!(guarded_pick(&ng, &ll, &nan, None, STANDARD, plain, &[]), 1, "premise: unmasked, the fallback takes candidate 1");
        assert_eq!(guarded_pick(&ng, &ll, &nan, None, STANDARD, plain, &[false, true, false]), 2);
        assert_eq!(guarded_pick(&ng, &[-1.0, 0.0, -9.0], &nan, None, STANDARD, plain, &[false, true, false]), 0, "back to the first when it is the best left");
        let (finite, ll) = ([0.0; 3], [-5.0, 0.0, -0.5]);
        assert_eq!(fused_pick(&ng, &ll, &finite, None, STANDARD, &[]), 1, "premise: the fused path takes candidate 1 too");
        assert_eq!(guarded_pick(&ng, &ll, &finite, None, STANDARD, plain, &[false, true, false]), 2, "the fused path masks as well");
    }

    /// The level's header triple is the fallback of a non-finite col, whatever the level's cell is.
    #[test]
    fn the_fallback_is_the_plain_pick_whatever_the_cell() {
        let (ng, ll) = ([-10.0, -10.3], [-5.0, -3.8]);
        for cell in [STANDARD, CONSERVATIVE, AGGRESSIVE] {
            assert_eq!(guarded_pick(&ng, &ll, &[0.0, f64::NAN], None, cell, (0.3, 2.0, 0.25), &[]), 0);
            assert_eq!(guarded_pick(&ng, &ll, &[0.0, f64::NAN], None, cell, (0.5, 2.0, 0.25), &[]), 1, "the plain triple decides: gain 0.5 * 1.2 - 0.3 = 0.3");
        }
    }
}
