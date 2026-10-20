//! P4b `--colloquial` (experiments/p4b-colloquial/README.md; evaluation only, not shipped): a second, plain bigram table as a
//! log-linear term plus a penalty for overlay words. Definitions are those of experiments/p4-colloquial/p4.py, word for word:
//! s = ng + mu * lc(words) - gamma * k(words).
use core::lm::Lm;
use std::collections::HashSet;
use std::fs;
use std::path::Path;

pub struct Colloq {
    lm: Lm,
    /// N + V + 1 of the table, V counting `<s>` and `</s>` (p4.py `Table.n1`).
    n1: f64,
    pub mu: f64,
    pub gamma: f64,
    /// Words of overlay-add.tsv and the acg pack (not names-add.tsv, not sandhi-add.tsv).
    overlay: HashSet<String>,
    /// Words of mcbpmf-data.txt.
    base: HashSet<String>,
}

impl Colloq {
    /// The table as a plain bigram: no classes, no Kneser-Ney, no trigram, whatever sits beside the file.
    pub fn load(path: &Path) -> Result<Lm, String> {
        Lm::load_opts(path, false, false, false).map_err(|e| format!("--colloquial: {e}"))
    }

    pub fn new(lm: Lm, mu: f64, gamma: f64, overlay: HashSet<String>, base: HashSet<String>) -> Colloq {
        // `identity().1` is the vocabulary size + 1 (the offsets table).
        let n1 = (lm.total() + (lm.identity().1 as u64 - 1) + 1) as f64;
        Colloq { lm, n1, mu, gamma, overlay, base }
    }

    /// lc: sum of log10 P(w_i | w_{i-1}) from `<s>`, plus log10 P(`</s>` | w_k). P backs off to the table's own (c(w) + 1) / (N + V + 1)
    /// (0 for a word outside the table); the end term backs off to the table's end-of-sentence rate (`Lm::eos`, lam 1).
    pub fn lc(&self, words: &[String]) -> f64 {
        let (mut prev, mut tot) = ("<s>", 0.0);
        for w in words {
            let pb = (self.lm.count(w) + 1) as f64 / self.n1;
            tot += self.lm.prob(prev, w, pb).log10();
            prev = w;
        }
        tot + self.lm.eos(1.0, prev)
    }

    /// k: words of the split in the overlay set and not in the base lexicon, repeats counted.
    pub fn k(&self, words: &[String]) -> usize {
        words.iter().filter(|w| self.overlay.contains(*w) && !self.base.contains(*w)).count()
    }

    /// The candidates' order by s, descending, ties keeping the n-gram order (stable), and every candidate's s (by original index).
    pub fn order(&self, ng: &[f64], words: &[&[String]]) -> (Vec<usize>, Vec<f64>) {
        let s: Vec<f64> = ng.iter().zip(words).map(|(&ng, w)| ng + self.mu * self.lc(w) - self.gamma * self.k(w) as f64).collect();
        let mut order: Vec<usize> = (0..s.len()).collect();
        order.sort_by(|&a, &b| s[b].partial_cmp(&s[a]).unwrap_or(std::cmp::Ordering::Equal));
        (order, s)
    }
}

/// (overlay set, base set) from the literal files, with the filters of tools/triage.py `Lex` / experiments/diag-near-miss/d23.py
/// `load_lex_scores`: base rows have three whitespace fields, no `#`/`_` first character, one syllable per character, a numeric score;
/// overlay rows are tab lines with exactly four fields. The overlay is `lexicon_dir/overlay-add.tsv` and `packs_dir/acg-add.tsv`.
pub fn read_sets(lexicon_dir: &Path, packs_dir: &Path) -> Result<(HashSet<String>, HashSet<String>), String> {
    let read = |p: &Path| fs::read_to_string(p).map_err(|e| format!("--colloquial: cannot read {} ({:?})", p.display(), e.kind()));
    let mut base = HashSet::new();
    for l in read(&lexicon_dir.join("mcbpmf-data.txt"))?.lines() {
        let f: Vec<&str> = l.split_whitespace().collect();
        if l.starts_with(['#', '_']) || f.len() != 3 || f[1].chars().count() != f[0].split('-').count() || f[2].parse::<f64>().is_err() {
            continue;
        }
        base.insert(f[1].to_string());
    }
    let mut overlay = HashSet::new();
    for p in [lexicon_dir.join("overlay-add.tsv"), packs_dir.join("acg-add.tsv")] {
        for l in read(&p)?.lines() {
            let f: Vec<&str> = l.split('\t').collect();
            if f.len() == 4 {
                overlay.insert(f[1].to_string());
            }
        }
    }
    Ok((overlay, base))
}
