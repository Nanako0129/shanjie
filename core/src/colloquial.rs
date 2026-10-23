//! The colloquial term of the small-model rerank (docs/contracts/colloquial-term.md): a second, plain bigram table (the
//! colloquial table, SJ-CL) as a log-linear term, minus a penalty for overlay words. Definitions are those of
//! experiments/p4-colloquial/p4.py, word for word; the fusion that uses them is `TinyLm::rerank_colloquial`.
//!
//! There is no reading term in the fusion (docs/contracts/reading-decoder.md): the reading term the P5 cell `lex,1.0,4` put here went into the
//! n-gram decoder (core/src/reading.rs), where it reaches every setting and every strength.
//!
//! The constants are the cell P4d registered and chatjudge-2 confirmed (experiments/p4d-colloquial/README.md): mu 0.5, soft gate
//! T 1.0, gamma = mu / 2, and at the standard strength alpha 0.5, tau 2, delta 0.25. The conservative and aggressive strengths use the two
//! (alpha, tau, delta) of experiments/strength-colloq/README.md (docs/contracts/colloquial-levels.md); none of them comes from the `.sjtl`
//! header.
use crate::lm::{Lm, LmError, Profile};
use crate::tinylm::{pick, RerankLevel, TinyLm};
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
/// The fusion's (alpha, tau, delta) under the colloquial term at the standard strength (P4d registered cell; the shipping alpha without the
/// term is 0.3).
pub const STANDARD: (f64, f64, f64) = (0.5, 2.0, 0.25);
/// The same at the conservative strength: experiments/strength-colloq/README.md, the R2 registration ("conservative fixed to its own triple
/// (1.0, 2, 3.0)", the `.sjtl` header's conservative triple), result "R2 結果": passes the four shipping clauses and the order gates.
pub const CONSERVATIVE: (f64, f64, f64) = (1.0, 2.0, 3.0);
/// The same at the aggressive strength: experiments/strength-colloq/README.md, the grid cell (0.5, 2, 0.1) that R1 selected on chatclause and R2
/// repeated, result "結果" / "R2 結果": passes the four shipping clauses and the order gates. Not the header's aggressive triple (0.3, 2, 0.25).
pub const AGGRESSIVE: (f64, f64, f64) = (0.5, 2.0, 0.1);

/// The (alpha, tau, delta) of the fusion at `level` (docs/contracts/colloquial-levels.md section 1).
pub fn cell(level: RerankLevel) -> (f64, f64, f64) {
    match level {
        RerankLevel::Conservative => CONSERVATIVE,
        RerankLevel::Standard => STANDARD,
        RerankLevel::Aggressive => AGGRESSIVE,
    }
}

/// SHA-256 of the `.sjtl` the cell was measured with (data/tinylm.sjtl.sha256); the term applies to no other small model.
pub const PAIRED_TINYLM_SHA256: &str = "e7c179d85405ec42a40bb22adb58a7485c58f77aed89dfc2ca76a0d89568ed5e";

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
        let lm = Lm::load_opts(table, false, false, false).map_err(|_: LmError| ColloquialError::Table)?;
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

/// The index `rerank_colloquial` chooses: `pick` over the fused scores with `cell`, the level's (alpha, tau, delta) (never the `.sjtl`
/// header's). `ng`, `ll` and `col` are the first candidates of one n-best list, in the n-gram order.
pub fn fused_pick(ng: &[f64], ll: &[f64], col: &[f64], cell: (f64, f64, f64)) -> usize {
    let (alpha, tau, delta) = cell;
    pick(ng, &fuse(ll, col, alpha), alpha, tau, delta)
}

/// `fused_pick` with `cell`, unless a col is not finite (a word the table scores as log10 0, or a NaN): then no colloquial term at all, the plain
/// `pick` with `plain` (the `.sjtl` header's alpha, tau, delta of the same level), so an infinity or NaN never decides.
pub fn guarded_pick(ng: &[f64], ll: &[f64], col: &[f64], cell: (f64, f64, f64), plain: (f64, f64, f64)) -> usize {
    if col.iter().all(|c| c.is_finite()) {
        return fused_pick(ng, ll, col, cell);
    }
    let (alpha, tau, delta) = plain;
    pick(ng, ll, alpha, tau, delta)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn close(a: f64, b: f64) {
        assert!((a - b).abs() < 1e-12, "{a} vs {b}");
    }

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
        let eff = fuse(&ll, &col, STANDARD.0);
        close(eff[0], -10.0);
        close(eff[1], -9.0 + sigmoid(1.0) * 1.0 * 1.0);
        close(eff[2], -12.0 + sigmoid(-2.0) * 1.0 * 2.0);
        // adding a constant to every col changes nothing (relative to the anchor)
        let shifted = fuse(&ll, &col.map(|c| c + 100.0), STANDARD.0);
        for (a, b) in eff.iter().zip(&shifted) {
            close(*a, *b);
        }
        // an extreme gap saturates the gate instead of overflowing
        let eff = fuse(&[-1.0, -5000.0], &[0.0, 40.0], STANDARD.0);
        assert!(eff.iter().all(|x| x.is_finite()));
    }

    /// The registered cell (alpha 0.5, tau 2, delta 0.25) decides, not the shipping triple (alpha 0.3): with ng [-10.0, -10.3] and
    /// ll [-5.0, -3.8], candidate 1 gains 0.5 * 1.2 - 0.3 = 0.3 >= delta at alpha 0.5 but 0.3 * 1.2 - 0.3 = 0.06 at alpha 0.3.
    #[test]
    fn fused_pick_uses_the_registered_cell() {
        assert_eq!(fused_pick(&[-10.0, -10.3], &[-5.0, -3.8], &[0.0, 0.0], STANDARD), 1, "alpha 0.5: gain 0.3 >= delta");
        assert_eq!(pick(&[-10.0, -10.3], &[-5.0, -3.8], 0.3, STANDARD.1, STANDARD.2), 0, "premise: the shipping alpha keeps the first");
        assert_eq!(fused_pick(&[-10.0, -10.3], &[-5.0, -4.2], &[0.0, 0.0], STANDARD), 0, "gain 0.1 < delta");
        // tau: an n-gram margin of 2 or more keeps the first whatever the small model says
        assert_eq!(fused_pick(&[-10.0, -12.0], &[-50.0, 0.0], &[0.0, 0.0], STANDARD), 0);
        assert_eq!(fused_pick(&[-10.0, -11.9], &[-50.0, 0.0], &[0.0, 0.0], STANDARD), 1);
    }

    /// A col that is +inf, -inf or NaN (the anchor's or another candidate's) must not decide: the plain pick with the given triple is used.
    /// With ng [-10.0, -10.3], ll [-5.0, -3.8] the plain triple (0.3, 2, 0.25) keeps candidate 0; a +inf col would make the fused score of
    /// candidate 1 infinite (and a -inf anchor col makes col_1 - col_0 infinite) and so pick it.
    #[test]
    fn non_finite_col_falls_back_to_the_plain_pick() {
        let (ng, ll, plain) = ([-10.0, -10.3], [-5.0, -3.8], (0.3, STANDARD.1, STANDARD.2));
        assert_eq!(fused_pick(&ng, &ll, &[0.0, f64::INFINITY], STANDARD), 1, "premise: unguarded, +inf decides");
        assert_eq!(guarded_pick(&ng, &ll, &[0.0, f64::INFINITY], STANDARD, plain), 0);
        assert_eq!(guarded_pick(&ng, &ll, &[f64::NEG_INFINITY, 0.0], STANDARD, plain), 0, "-inf anchor");
        assert_eq!(guarded_pick(&ng, &ll, &[0.0, f64::NAN], STANDARD, plain), 0);
        assert_eq!(guarded_pick(&ng, &ll, &[0.0, 0.0], STANDARD, plain), fused_pick(&ng, &ll, &[0.0, 0.0], STANDARD), "finite cols: the term as before");
    }

    /// colloquial-levels section 2.1: each level's `fused_pick` uses its own (alpha, tau, delta). With no colloquial term (equal col) the
    /// candidates gain, over candidate 0, 1 gains 0.5 * 1.2 - 0.4 = 0.2 at alpha 0.5 (delta 0.1 takes it, delta 0.25 does not) and 2 gains 8 - 4 = 4 at
    /// alpha 1.0 against 0.5 * 8 - 4 = 0 at alpha 0.5 (delta 3 takes it): standard keeps 0, conservative picks 2, aggressive picks 1.
    #[test]
    fn each_level_picks_with_its_own_cell() {
        let (ng, ll, col) = ([0.0, -0.4, -4.0], [0.0, 1.2, 8.0], [0.0; 3]);
        let by_level = |l| fused_pick(&ng, &ll, &col, cell(l));
        assert_eq!([by_level(RerankLevel::Standard), by_level(RerankLevel::Conservative), by_level(RerankLevel::Aggressive)], [0, 2, 1]);
        assert_eq!(STANDARD, (0.5, 2.0, 0.25));
        assert_eq!(CONSERVATIVE, (1.0, 2.0, 3.0));
        assert_eq!(AGGRESSIVE, (0.5, 2.0, 0.1));
        assert_eq!([cell(RerankLevel::Standard), cell(RerankLevel::Conservative), cell(RerankLevel::Aggressive)], [STANDARD, CONSERVATIVE, AGGRESSIVE]);
    }

    /// The colloquial term weighs the same in the fused score at every alpha (s = ng + alpha * ll + g * MU * dcol), so the coefficient in ll_eff is
    /// MU / alpha of the level's alpha: at alpha 1.0 a divisor of 0.5 (the standard alpha) doubles the term. With ng [0, -0.5], ll equal (gate 0.5)
    /// and the conservative cell (alpha 1, delta 3): a col gap of 10 gains -0.5 + 0.5 * 0.5 * 10 = 2 < 3 (a doubled term would gain 4.5) and a gap
    /// of 16 gains 3.5.
    #[test]
    fn the_term_weighs_the_same_at_every_alpha() {
        let (ng, ll) = ([0.0, -0.5], [-5.0, -5.0]);
        let c = |col: f64| fused_pick(&ng, &ll, &[0.0, col], CONSERVATIVE);
        assert_eq!([c(10.0), c(16.0)], [0, 1], "the colloquial term");
        // fuse itself: the coefficient is MU / alpha with the alpha given
        let eff = fuse(&[-5.0, -5.0], &[0.0, 10.0], 1.0);
        close(eff[1], -5.0 + sigmoid(0.0) * (MU / 1.0) * 10.0);
    }

    /// The level's header triple is the fallback of a non-finite col, whatever the level's cell is.
    #[test]
    fn the_fallback_is_the_plain_pick_whatever_the_cell() {
        let (ng, ll) = ([-10.0, -10.3], [-5.0, -3.8]);
        for cell in [STANDARD, CONSERVATIVE, AGGRESSIVE] {
            assert_eq!(guarded_pick(&ng, &ll, &[0.0, f64::NAN], cell, (0.3, 2.0, 0.25)), 0);
            assert_eq!(guarded_pick(&ng, &ll, &[0.0, f64::NAN], cell, (0.5, 2.0, 0.25)), 1, "the plain triple decides: gain 0.5 * 1.2 - 0.3 = 0.3");
        }
    }
}
