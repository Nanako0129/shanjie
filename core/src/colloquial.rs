//! The colloquial term of the small-model rerank (docs/contracts/colloquial-term.md): a second, plain bigram table (the
//! colloquial table, SJ-CL) as a log-linear term, minus a penalty for overlay words. Definitions are those of
//! experiments/p4-colloquial/p4.py, word for word; the fusion that uses them is `TinyLm::rerank_colloquial`.
//!
//! The constants are the cell P4d registered and chatjudge-2 confirmed (experiments/p4d-colloquial/README.md): mu 0.5, soft gate
//! T 1.0, alpha 0.5, tau 2, delta 0.25, gamma = mu / 2. They do not come from the `.sjtl` header.
use crate::lm::{Lm, LmError};
use crate::tinylm::pick;
use std::collections::HashSet;
use std::fmt;
use std::path::Path;

/// Weight of lc (P4d registered cell).
pub const MU: f64 = 0.5;
/// Softness of the gate g_i = sigmoid((ll_i - ll_0) / GATE_T) (P4d registered cell).
pub const GATE_T: f64 = 1.0;
/// gamma / mu: col = lc - GAMMA_PER_MU * k, i.e. gamma = mu / 2 (P4d: P4c's mu 0.5 cell).
pub const GAMMA_PER_MU: f64 = 0.5;
/// The fusion's alpha, tau and delta under the colloquial term (P4d registered cell; the shipping alpha is 0.3).
pub const ALPHA: f64 = 0.5;
pub const TAU: f64 = 2.0;
pub const DELTA: f64 = 0.25;

/// SHA-256 of the `.sjtl` the cell was measured with (data/tinylm.sjtl.sha256); the term applies to no other small model.
pub const PAIRED_TINYLM_SHA256: &str = "e7c179d85405ec42a40bb22adb58a7485c58f77aed89dfc2ca76a0d89568ed5e";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ColloquialError {
    /// The table file could not be read or is malformed.
    Table,
    /// A row of the base lexicon has a score that is not a number.
    Lexicon,
}

impl fmt::Display for ColloquialError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            ColloquialError::Table => "cannot load the colloquial table (.sjlm)",
            ColloquialError::Lexicon => "mcbpmf-data.txt has a row whose score is not a number",
        })
    }
}
impl std::error::Error for ColloquialError {}

pub struct Colloquial {
    lm: Lm,
    /// N + V + 1 of the table, V counting `<s>` and `</s>` (p4.py `Table.n1`).
    n1: f64,
    /// Words of overlay-add.tsv and of the packs the engine was built with (not names-add.tsv, not sandhi-add.tsv).
    overlay: HashSet<String>,
    /// Words of mcbpmf-data.txt.
    base: HashSet<String>,
}

impl Colloquial {
    /// The table at `table` read as a plain bigram (no classes, no Kneser-Ney, no trigram, whatever sits beside the file), and the
    /// two word sets of k from the literal texts, with the filters of tools/triage.py `Lex` / experiments/diag-near-miss/d23.py
    /// `load_lex_scores`: base rows (`base_text`, mcbpmf-data.txt) have three whitespace fields, no `#` / `_` first character, one
    /// syllable per character, and a numeric score (a row whose score is not a number is an error, as in the engine); overlay rows
    /// (`overlay_text`, overlay-add.tsv, then `pack_text`, the pack rows the engine was built with) are tab lines with exactly four
    /// fields.
    pub fn load(table: &Path, base_text: &str, overlay_text: &str, pack_text: Option<&str>) -> Result<Colloquial, ColloquialError> {
        let lm = Lm::load_opts(table, false, false, false).map_err(|_: LmError| ColloquialError::Table)?;
        // `Lm` has no vocabulary-size accessor: `identity().1` is V + 1 (the length of the word-offset table), so N + V + 1 is
        // N + identity().1.
        let n1 = (lm.total() + lm.identity().1 as u64) as f64;
        let mut base = HashSet::new();
        for l in base_text.lines() {
            let f: Vec<&str> = l.split_whitespace().collect();
            if l.starts_with(['#', '_']) || f.len() != 3 || f[1].chars().count() != f[0].split('-').count() {
                continue;
            }
            f[2].parse::<f64>().map_err(|_| ColloquialError::Lexicon)?;
            base.insert(f[1].to_string());
        }
        let mut overlay = HashSet::new();
        for l in overlay_text.lines().chain(pack_text.unwrap_or("").lines()) {
            let f: Vec<&str> = l.split('\t').collect();
            if f.len() == 4 {
                overlay.insert(f[1].to_string());
            }
        }
        Ok(Colloquial { lm, n1, overlay, base })
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
        words.iter().filter(|w| self.overlay.contains(w.as_ref()) && !self.base.contains(w.as_ref())).count()
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

/// The fused small-model scores of one n-best list: ll_eff_i = ll_i + g_i * (MU / ALPHA) * (col_i - col_0) with
/// g_i = sigmoid((ll_i - ll_0) / GATE_T). Candidate 0 is the anchor (its own term is 0), and the gate reads ll_0, never ll_i alone.
pub fn fuse(ll: &[f64], col: &[f64]) -> Vec<f64> {
    (0..ll.len()).map(|i| ll[i] + sigmoid((ll[i] - ll[0]) / GATE_T) * (MU / ALPHA) * (col[i] - col[0])).collect()
}

/// The index `rerank_colloquial` chooses: `pick` over the fused scores with the registered cell's alpha, tau and delta (never the
/// `.sjtl` header's). `ng`, `ll` and `col` are the first candidates of one n-best list, in the n-gram order.
pub fn fused_pick(ng: &[f64], ll: &[f64], col: &[f64]) -> usize {
    pick(ng, &fuse(ll, col), ALPHA, TAU, DELTA)
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
        let eff = fuse(&ll, &col);
        close(eff[0], -10.0);
        close(eff[1], -9.0 + sigmoid(1.0) * 1.0 * 1.0);
        close(eff[2], -12.0 + sigmoid(-2.0) * 1.0 * 2.0);
        // adding a constant to every col changes nothing (relative to the anchor)
        let shifted = fuse(&ll, &col.map(|c| c + 100.0));
        for (a, b) in eff.iter().zip(&shifted) {
            close(*a, *b);
        }
        // an extreme gap saturates the gate instead of overflowing
        let eff = fuse(&[-1.0, -5000.0], &[0.0, 40.0]);
        assert!(eff.iter().all(|x| x.is_finite()));
    }

    /// The registered cell (alpha 0.5, tau 2, delta 0.25) decides, not the shipping triple (alpha 0.3): with ng [-10.0, -10.3] and
    /// ll [-5.0, -3.8], candidate 1 gains 0.5 * 1.2 - 0.3 = 0.3 >= delta at alpha 0.5 but 0.3 * 1.2 - 0.3 = 0.06 at alpha 0.3.
    #[test]
    fn fused_pick_uses_the_registered_cell() {
        assert_eq!(fused_pick(&[-10.0, -10.3], &[-5.0, -3.8], &[0.0, 0.0]), 1, "alpha 0.5: gain 0.3 >= delta");
        assert_eq!(pick(&[-10.0, -10.3], &[-5.0, -3.8], 0.3, TAU, DELTA), 0, "premise: the shipping alpha keeps the first");
        assert_eq!(fused_pick(&[-10.0, -10.3], &[-5.0, -4.2], &[0.0, 0.0]), 0, "gain 0.1 < delta");
        // tau: an n-gram margin of 2 or more keeps the first whatever the small model says
        assert_eq!(fused_pick(&[-10.0, -12.0], &[-50.0, 0.0], &[0.0, 0.0]), 0);
        assert_eq!(fused_pick(&[-10.0, -11.9], &[-50.0, 0.0], &[0.0, 0.0]), 1);
    }
}
