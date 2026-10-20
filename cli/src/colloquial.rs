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
        // `Lm` has no vocabulary-size accessor (and core stays untouched for this evaluation option): `identity().1` is V + 1 (the
        // length of the word-offset table), so N + V + 1 is N + identity().1.
        let n1 = (lm.total() + lm.identity().1 as u64) as f64;
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

    /// s = ng + mu * lc - gamma * k; a zero weight leaves its term out, as p4.py does (`0 * lc` would be NaN for an infinite lc).
    pub fn score(&self, ng: f64, words: &[String]) -> f64 {
        let lc = if self.mu != 0.0 { self.mu * self.lc(words) } else { 0.0 };
        let k = if self.gamma != 0.0 { self.gamma * self.k(words) as f64 } else { 0.0 };
        ng + lc - k
    }

    /// Replaces every candidate's score by s and sorts by s, descending; the sort is stable, so ties keep the n-gram order (p4.py's
    /// `(-s, j)`). A non-finite s is an error (the row's message is added by the caller).
    pub fn rescore(&self, cands: &mut [(f64, Vec<String>)]) -> Result<(), String> {
        for (sc, ws) in cands.iter_mut() {
            *sc = self.score(*sc, ws);
            if !sc.is_finite() {
                return Err("a candidate's colloquial score is not finite".into());
            }
        }
        cands.sort_by(|a, b| b.0.partial_cmp(&a.0).expect("finite scores")); // equal scores (-0.0 included) stay in order
        Ok(())
    }
}

/// (overlay set, base set) from the literal files, with the filters of tools/triage.py `Lex` / experiments/diag-near-miss/d23.py
/// `load_lex_scores`: base rows have three whitespace fields, no `#`/`_` first character, one syllable per character, a numeric score;
/// overlay rows are tab lines with exactly four fields. The overlay is `lexicon_dir/overlay-add.tsv`, plus `packs_dir/acg-add.tsv` only when
/// `acg` (the pack is enabled, `--packs acg`); `packs_dir` is the directory the engine reads the pack from. A base row whose score is not a
/// number is an error, as in triage.Lex and the engine.
pub fn read_sets(lexicon_dir: &Path, packs_dir: &Path, acg: bool) -> Result<(HashSet<String>, HashSet<String>), String> {
    let read = |p: &Path| fs::read_to_string(p).map_err(|e| format!("--colloquial: cannot read {} ({:?})", p.display(), e.kind()));
    let mut base = HashSet::new();
    for l in read(&lexicon_dir.join("mcbpmf-data.txt"))?.lines() {
        let f: Vec<&str> = l.split_whitespace().collect();
        if l.starts_with(['#', '_']) || f.len() != 3 || f[1].chars().count() != f[0].split('-').count() {
            continue;
        }
        f[2].parse::<f64>().map_err(|_| "--colloquial: mcbpmf-data.txt has a row whose score is not a number".to_string())?;
        base.insert(f[1].to_string());
    }
    let mut overlay = HashSet::new();
    let mut files = vec![lexicon_dir.join("overlay-add.tsv")];
    if acg {
        files.push(packs_dir.join("acg-add.tsv"));
    }
    for p in files {
        for l in read(&p)?.lines() {
            let f: Vec<&str> = l.split('\t').collect();
            if f.len() == 4 {
                overlay.insert(f[1].to_string());
            }
        }
    }
    Ok((overlay, base))
}
