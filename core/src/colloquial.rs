//! The colloquial term of the small-model rerank (docs/spec/semantic-rerank.md §4): a second, plain bigram table (the
//! colloquial table, SJ-CL) as a log-linear term, minus a penalty for overlay words. Definitions are those of
//! reference/proto/colloquial_ref.py, word for word; the fusion that uses them is `TinyLm::rerank_colloquial`.
//!
//! The reading-channel term that once rode on this fusion is gone: the decoder carries the reading term now
//! (docs/spec/decoder.md §5, `reading.rs`).
//!
//! The constants MU, GATE_T and gamma = mu / 2 are the cell P4d registered and chatjudge-2 confirmed. The
//! three (alpha, tau, delta) cells, one per strength (docs/spec/semantic-rerank.md §4.4), are the ones docs/spec/semantic-rerank.md §4.4
//! registers for the 會意 SJ-SR-8-15M re-ranker; none of them comes from the `.sjtl` header.
use crate::lm::{Lm, LmError, Profile};
use crate::tinylm::{pick_masked, RerankLevel, TinyLm};
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
/// The fusion's (alpha, tau, delta) under the colloquial term at the standard strength: docs/spec/semantic-rerank.md §4.4, the cell FUSE-1
/// (wikijudge8) confirmed for the 會意 SJ-SR-8-15M re-ranker (docs/spec/semantic-rerank.md §4.4). The three cells here are the chat fusion at each
/// strength; the `.sjtl` header's chat triples only back a col that is not finite.
pub const STANDARD: (f64, f64, f64) = (1.1, 2.0, 0.0);
/// The same at the conservative strength: the cell FUSE-2 (wikijudge9) confirmed.
pub const CONSERVATIVE: (f64, f64, f64) = (1.3, 2.0, 1.0);
/// The same at the aggressive strength: the same cell as the standard one (docs/spec/semantic-rerank.md §4, "aggressive = the new standard").
pub const AGGRESSIVE: (f64, f64, f64) = (1.1, 2.0, 0.0);

/// The (alpha, tau, delta) of the fusion at `level` (docs/spec/semantic-rerank.md §4.4).
pub fn cell(level: RerankLevel) -> (f64, f64, f64) {
    match level {
        RerankLevel::Conservative => CONSERVATIVE,
        RerankLevel::Standard => STANDARD,
        RerankLevel::Aggressive => AGGRESSIVE,
    }
}

/// SHA-256 of the `.sjtl` the cells were measured with (會意 SJ-SR-8-15M, SR-TW-V's V4-s3 weights with the formal cells of docs/spec/semantic-rerank.md §4
/// docs/spec/semantic-rerank.md §4.6; data/tinylm.sjtl.sha256); the term applies to no other small model.
pub const PAIRED_TINYLM_SHA256: &str = "356a37beede1a754cb6e963a52d0f20ae6574cbcd332d0b1bfc9686c0a97c5de";

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
    /// N + V + 1 of the table, V counting `<s>` and `</s>` (colloquial_ref.py `Table.n1`).
    n1: f64,
    /// The words of overlay-add.tsv and of the packs the engine was built with (not names-add.tsv, not sandhi-add.tsv) that mcbpmf-data.txt
    /// lacks: the only words k counts, so the two full sets are never kept (keeping them cost about 10 MiB more peak RSS on a Mac, measured
    /// with 3 runs of `/usr/bin/time -l`).
    overlay_only: HashSet<String>,
    /// SR-TW-C (research): the first word of a split scored by the table's unigram estimate instead of
    /// P(w | `<s>`). Off after `load` and in the engine; only the evaluation CLI's `--colloquial-first-unigram` turns it on.
    first_unigram: bool,
    /// SR-TW-M: the first word scored by the unigram estimate only when the table has no
    /// `<s>` bigram for it (a word seen at the start keeps P(w | `<s>`)). Off after `load`; the shipping setting turns it on
    /// (docs/spec/semantic-rerank.md §4.4): `Engine::load_colloquial` and the evaluation CLI's default, whose research flag
    /// `--colloquial-no-unseen-start-unigram` turns it off.
    unseen_start_unigram: bool,
}

impl Colloquial {
    /// The table at `table` read as a plain bigram (no classes, no Kneser-Ney, no trigram, whatever sits beside the file), and the set of
    /// k, with the filters of reference/proto/colloquial_ref.py `word_sets`: base rows (`base_text`,
    /// mcbpmf-data.txt) have three whitespace fields, no `#` / `_` first character, one syllable per character, and a numeric score (a row
    /// whose score is not a number is an error, as in the engine); overlay rows (`overlay`, overlay-add.tsv, read line by line, then
    /// `pack_text`, the pack rows the engine was built with) are tab lines with exactly four fields.
    ///
    /// Memory: only the words that are in the overlay and not in the base are kept. The base words are a short-lived set that borrows
    /// from `base_text`, and the 17 MB overlay file is never held as text (peak RSS with the table: +27 to +30 MiB on a Mac before this,
    /// +16 to +21 MiB after; 3 runs each, `/usr/bin/time -l`).
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
        Ok(Colloquial { lm, n1, overlay_only, first_unigram: false, unseen_start_unigram: false })
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

    /// Turn the SR-TW-C first-word estimate on or off (`first_unigram`; off after `load`).
    pub fn set_first_unigram(&mut self, on: bool) {
        self.first_unigram = on;
    }

    /// Whether the SR-TW-C first-word estimate is on.
    pub fn first_unigram(&self) -> bool {
        self.first_unigram
    }

    /// Turn the SR-TW-M first-word estimate on or off (`unseen_start_unigram`; off after `load`, on in the shipping setting).
    pub fn set_unseen_start_unigram(&mut self, on: bool) {
        self.unseen_start_unigram = on;
    }

    /// Whether the SR-TW-M first-word estimate is on.
    pub fn unseen_start_unigram(&self) -> bool {
        self.unseen_start_unigram
    }

    /// lc: sum of log10 P(w_i | w_{i-1}) from `<s>`, plus log10 P(`</s>` | w_k). P backs off to the table's own (c(w) + 1) / (N + V + 1)
    /// (0 for a word outside the table); the end term backs off to the table's end-of-sentence rate (`Lm::eos`, lam 1). With `first_unigram`
    /// the first word's term is that back-off estimate (c(w) + 1) / (N + V + 1) itself, not P(w | `<s>`); with `unseen_start_unigram` the same
    /// holds only for a first word the table has no `<s>` bigram for (`Lm::has_bigram`). The other terms are unchanged.
    pub fn lc<S: AsRef<str>>(&self, words: &[S]) -> f64 {
        let (mut prev, mut tot) = ("<s>", 0.0);
        for (i, w) in words.iter().enumerate() {
            let w = w.as_ref();
            let pb = (self.lm.count(w) + 1) as f64 / self.n1;
            tot += if i == 0 && (self.first_unigram || (self.unseen_start_unigram && !self.lm.has_bigram(prev, w))) { pb } else { self.lm.prob(prev, w, pb) }.log10();
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

/// 1 / (1 + e^-x) without overflow for large |x| (colloquial_ref.py `sigmoid`).
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

/// Whether the term is used for a rerank (docs/spec/semantic-rerank.md §4, §4.4): the chat profile,
/// any of the three strengths (so no level parameter; each strength has its own `cell`) and the small model the registered cells were measured with (`PAIRED_TINYLM_SHA256`).
/// The fourth condition, a loaded table, is the caller having one. The engine (per job) and the evaluation CLI both decide with this function;
/// the CLI's `--tinylm-paired` (evaluation only) takes the model condition as met and keeps the profile one.
pub fn applies(profile: Profile, model: &TinyLm) -> bool {
    profile == Profile::Chat && model.file_sha256() == PAIRED_TINYLM_SHA256
}

/// The index `rerank_colloquial` chooses: `pick` over the fused scores with `cell`, the level's (alpha, tau, delta) (never the `.sjtl`
/// header's). `ng`, `ll` and `col` are the first candidates of one n-best list, in the n-gram order: ll_eff_i = `fuse`.
pub fn fused_pick(ng: &[f64], ll: &[f64], col: &[f64], cell: (f64, f64, f64), masked: &[bool], learn: &[f64]) -> usize {
    let (alpha, tau, delta) = cell;
    pick_masked(ng, &fuse(ll, col, alpha), alpha, tau, delta, masked, learn)
}

/// `fused_pick` with `cell`, unless a col is not finite (a word the table scores as log10 0, or a NaN): then no colloquial term at all, the plain
/// `pick` with `plain` (the `.sjtl` header's alpha, tau, delta of the same level), so an infinity or NaN never decides.
pub fn guarded_pick(ng: &[f64], ll: &[f64], col: &[f64], cell: (f64, f64, f64), plain: (f64, f64, f64), masked: &[bool], learn: &[f64]) -> usize {
    if col.iter().all(|c| c.is_finite()) {
        return fused_pick(ng, ll, col, cell, masked, learn);
    }
    let (alpha, tau, delta) = plain;
    pick_masked(ng, ll, alpha, tau, delta, masked, learn)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tinylm::pick;

    fn close(a: f64, b: f64) {
        assert!((a - b).abs() < 1e-12, "{a} vs {b}");
    }

    /// The `.sjtl` header's chat standard triple of the 會意 SJ-SR-8-15M file (the fallback of a col that is not finite): not one of the three cells.
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

    /// The registered cell decides, not the header triple: with ng [-10.0, -10.3] and ll [-5.0, -4.2], candidate 1 gains 1.1 * 0.8 - 0.3 = 0.58 at the
    /// standard alpha 1.1 (delta 0 takes it) but 1.3 * 0.8 - 0.3 = 0.74 at the conservative cell (below its delta 1) and 0.3 * 0.8 - 0.3 = -0.06 at the
    /// header's alpha 0.3. With ll [-5.0, -3.8] the conservative gain is 1.3 * 1.2 - 0.3 = 1.26, which its delta 1 takes.
    #[test]
    fn fused_pick_uses_the_registered_cell() {
        assert_eq!(fused_pick(&[-10.0, -10.3], &[-5.0, -4.2], &[0.0, 0.0], STANDARD, &[], &[]), 1, "alpha 1.1: gain 0.58 >= delta 0");
        assert_eq!(fused_pick(&[-10.0, -10.3], &[-5.0, -4.2], &[0.0, 0.0], CONSERVATIVE, &[], &[]), 0, "gain 0.74 < delta 1");
        assert_eq!(fused_pick(&[-10.0, -10.3], &[-5.0, -3.8], &[0.0, 0.0], CONSERVATIVE, &[], &[]), 1, "gain 1.26 >= delta 1");
        assert_eq!(pick(&[-10.0, -10.3], &[-5.0, -4.2], HEADER_STANDARD.0, HEADER_STANDARD.1, HEADER_STANDARD.2), 0, "premise: the header triple keeps the first");
        assert_eq!(fused_pick(&[-10.0, -10.3], &[-5.0, -4.8], &[0.0, 0.0], STANDARD, &[], &[]), 0, "gain 1.1 * 0.2 - 0.3 < delta 0");
        // tau: an n-gram margin of 2 or more keeps the first whatever the small model says
        assert_eq!(fused_pick(&[-10.0, -12.0], &[-50.0, 0.0], &[0.0, 0.0], STANDARD, &[], &[]), 0);
        assert_eq!(fused_pick(&[-10.0, -11.9], &[-50.0, 0.0], &[0.0, 0.0], STANDARD, &[], &[]), 1);
    }

    /// docs/spec/learning.md §7.4 and §7.5 in the fused routes. `learn` empty or all 0 is the pick before the term (the plain `pick` over `fuse`); where the n-gram
    /// is sure (margin 3 >= tau 2) and candidate 2 is taught, candidate 1 (the better small-model score) cannot be chosen in `fused_pick`, in
    /// `guarded_pick`'s fused route, or in its plain fallback (a col that is not finite), and candidate 2 still has to win by delta.
    #[test]
    fn the_learning_term_reaches_every_route_and_a_taught_candidate_opens_the_gate() {
        let (ng, ll, col) = ([0.0, -3.0, -3.2], [-5.0, 0.0, -1.0], [0.0, 0.0, 0.0]);
        let (plain, cell) = ((1.0, 2.0, 0.0), (1.0, 2.0, 0.0));
        let learn = [0.0, 0.0, 1.0];
        let nan = [0.0, f64::NAN, 0.0];
        assert_eq!(fused_pick(&ng, &ll, &col, cell, &[], &[]), 0, "premise: the margin keeps the first");
        assert_eq!(fused_pick(&ng, &ll, &col, cell, &[], &[0.0; 3]), 0, "all 0 is no term");
        assert_eq!(fused_pick(&ng, &ll, &col, cell, &[], &learn), 2);
        assert_eq!(fused_pick(&ng, &ll, &col, (1.0, 2.0, 2.0), &[], &[0.0, 0.0, 0.5]), 0, "delta 2: gain 1.3 is not enough");
        assert_eq!(fused_pick(&ng, &ll, &col, (1.0, 2.0, 1.0), &[], &[0.0, 0.0, 0.5]), 2, "delta 1: it is");
        assert_eq!(guarded_pick(&ng, &ll, &col, cell, plain, &[], &learn), 2, "the fused route");
        assert_eq!(guarded_pick(&ng, &ll, &nan, cell, plain, &[], &[]), 0, "premise: the fallback keeps the first");
        assert_eq!(guarded_pick(&ng, &ll, &nan, cell, plain, &[], &learn), 2, "the fallback route (plain pick) has the term too");
        assert_eq!(guarded_pick(&ng, &ll, &nan, cell, plain, &[false, false, true], &learn), 0, "a masked taught candidate opens nothing");
        // the term is as `pick_masked` has it on the fused scores
        let eff = fuse(&ll, &[0.0, -2.0, 1.0], 0.7);
        assert_eq!(fused_pick(&ng, &ll, &[0.0, -2.0, 1.0], (0.7, 2.0, 0.0), &[], &[0.0, 0.0, 1.0]), crate::tinylm::pick_masked(&ng, &eff, 0.7, 2.0, 0.0, &[], &[0.0, 0.0, 1.0]));
    }

    /// A col that is +inf, -inf or NaN (the anchor's or another candidate's) must not decide: the plain pick with the given triple is used.
    /// With ng [-10.0, -10.3], ll [-5.0, -3.8] the plain (header) triple (0.3, 2, 0.25) keeps candidate 0; a +inf col would make the fused score of
    /// candidate 1 infinite (and a -inf anchor col makes col_1 - col_0 infinite) and so pick it.
    #[test]
    fn non_finite_col_falls_back_to_the_plain_pick() {
        let (ng, ll, plain) = ([-10.0, -10.3], [-5.0, -3.8], HEADER_STANDARD);
        assert_eq!(fused_pick(&ng, &ll, &[0.0, f64::INFINITY], STANDARD, &[], &[]), 1, "premise: unguarded, +inf decides");
        assert_eq!(guarded_pick(&ng, &ll, &[0.0, f64::INFINITY], STANDARD, plain, &[], &[]), 0);
        assert_eq!(guarded_pick(&ng, &ll, &[f64::NEG_INFINITY, 0.0], STANDARD, plain, &[], &[]), 0, "-inf anchor");
        assert_eq!(guarded_pick(&ng, &ll, &[0.0, f64::NAN], STANDARD, plain, &[], &[]), 0);
        assert_eq!(guarded_pick(&ng, &ll, &[0.0, 0.0], STANDARD, plain, &[], &[]), fused_pick(&ng, &ll, &[0.0, 0.0], STANDARD, &[], &[]), "finite cols: the term as before");
    }

    /// docs/spec/semantic-rerank.md §4.4: each level's `fused_pick` uses its own (alpha, tau, delta). With no colloquial or channel term (equal col) a
    /// candidate gains alpha * dll - dng over candidate 0. List 1 (dng 1.0, dll 0.8) gains 0.88 - 1.0 < 0 at the standard (and aggressive) alpha 1.1 and
    /// 1.04 - 1.0 = 0.04 at the conservative alpha 1.3 (delta 1): no level takes it; list 2 (dng 0.4, dll 0.8) gains 0.48, which the standard delta 0
    /// takes and the conservative delta 1 does not (0.64); list 3 (dng 1.9, dll 5) gains 3.6 and 4.6: all take it. The aggressive cell is the standard
    /// one (docs/spec/semantic-rerank.md §4.4), so those two rows are equal.
    #[test]
    fn each_level_picks_with_its_own_cell() {
        let lists = [([0.0, -1.0], [0.0, 0.8]), ([0.0, -0.4], [0.0, 0.8]), ([0.0, -1.9], [0.0, 5.0])];
        let by_level = |l| lists.map(|(ng, ll)| fused_pick(&ng, &ll, &[0.0; 2], cell(l), &[], &[]));
        assert_eq!(by_level(RerankLevel::Standard), [0, 1, 1]);
        assert_eq!(by_level(RerankLevel::Conservative), [0, 0, 1]);
        assert_eq!(by_level(RerankLevel::Aggressive), [0, 1, 1]);
        assert_eq!(STANDARD, (1.1, 2.0, 0.0));
        assert_eq!(CONSERVATIVE, (1.3, 2.0, 1.0));
        assert_eq!(AGGRESSIVE, STANDARD);
        assert_eq!([cell(RerankLevel::Standard), cell(RerankLevel::Conservative), cell(RerankLevel::Aggressive)], [STANDARD, CONSERVATIVE, AGGRESSIVE]);
    }

    /// The colloquial term weighs the same in the fused score at every alpha (s = ng + alpha * ll + g * MU * dcol), so the coefficient in ll_eff is
    /// MU / alpha of the level's alpha. With ng [0, -0.5] and ll equal (gate 0.5) candidate 1 gains -0.5 + 0.25 * dcol: it passes a level's delta at
    /// dcol = 2 + 4 * delta, whatever the level's alpha (a divisor from another level's alpha moves the threshold); each cell is checked just below
    /// and just above.
    #[test]
    fn the_term_weighs_the_same_at_every_alpha() {
        let (ng, ll) = ([0.0, -0.5], [-5.0, -5.0]);
        for cell in [STANDARD, CONSERVATIVE, AGGRESSIVE] {
            let col_at = 2.0 + 4.0 * cell.2;
            let c = |col: f64| fused_pick(&ng, &ll, &[0.0, col], cell, &[], &[]);
            assert_eq!([c(col_at - 0.2), c(col_at + 0.2)], [0, 1], "the colloquial term {cell:?}");
        }
        // fuse itself: the coefficient is MU / alpha with the alpha given
        let eff = fuse(&[-5.0, -5.0], &[0.0, 10.0], 1.0);
        close(eff[1], -5.0 + sigmoid(0.0) * (MU / 1.0) * 10.0);
    }

    /// docs/spec/decoder.md §7, test 5: the mask holds in `guarded_pick`'s fallback too. A non-finite col makes the plain `pick` decide (here with
    /// triple (1, 2, 0): candidate 1 scores -0.5 against -5 and wins, candidate 2 -4 would win without candidate 1); the fallback never goes to a masked candidate,
    /// and the fused path (finite cols) masks the same way.
    #[test]
    fn the_fallback_does_not_fall_back_to_a_masked_candidate() {
        let (ng, ll, plain) = ([0.0, -0.5, -3.0], [-5.0, 0.0, -1.0], (1.0, 2.0, 0.0));
        let nan = [0.0, f64::NAN, 0.0];
        assert_eq!(guarded_pick(&ng, &ll, &nan, STANDARD, plain, &[], &[]), 1, "premise: unmasked, the fallback takes candidate 1");
        assert_eq!(guarded_pick(&ng, &ll, &nan, STANDARD, plain, &[false, true, false], &[]), 2);
        assert_eq!(guarded_pick(&ng, &[-1.0, 0.0, -9.0], &nan, STANDARD, plain, &[false, true, false], &[]), 0, "back to the first when it is the best left");
        let (finite, ll) = ([0.0; 3], [-5.0, 0.0, -0.5]);
        assert_eq!(fused_pick(&ng, &ll, &finite, STANDARD, &[], &[]), 1, "premise: the fused path takes candidate 1 too");
        assert_eq!(guarded_pick(&ng, &ll, &finite, STANDARD, plain, &[false, true, false], &[]), 2, "the fused path masks as well");
    }

    /// The level's header triple is the fallback of a non-finite col, whatever the level's cell is.
    #[test]
    fn the_fallback_is_the_plain_pick_whatever_the_cell() {
        let (ng, ll) = ([-10.0, -10.3], [-5.0, -3.8]);
        for cell in [STANDARD, CONSERVATIVE, AGGRESSIVE] {
            assert_eq!(guarded_pick(&ng, &ll, &[0.0, f64::NAN], cell, (0.3, 2.0, 0.25), &[], &[]), 0);
            assert_eq!(guarded_pick(&ng, &ll, &[0.0, f64::NAN], cell, (0.5, 2.0, 0.25), &[], &[]), 1, "the plain triple decides: gain 0.5 * 1.2 - 0.3 = 0.3");
        }
    }
}
