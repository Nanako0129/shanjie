//! English or Zhuyin at the closing key of a syllable (docs/spec/english-mixed.md §16): the count table `english.tsv` and the two scores
//! the engine compares at a decision point. A port of reference/proto/english_score.py (`Table`); Python is the baseline and
//! `core/tests/engine_auto_english.rs` compares `lp_e` bit for bit. Every sum is a plain left-to-right float sum in the order written here.

use std::collections::HashMap;

/// The shipping cell of the grid theta in {1, 2, 3} by kappa in {1, 1.5, 2} by kappa_dup in {0, 3} (docs/spec/english-mixed.md §16.4). Until the
/// grid's measurement picks one this is the cell that takes English least often: the highest theta and the lowest penalties (a penalty lowers
/// lpZ, so a larger kappa means more English, not less).
pub const THETA: f64 = 3.0;
pub const KAPPA: f64 = 1.0;
pub const KAPPA_DUP: f64 = 0.0;
/// Not in the grid: a key the reversal guard took out of column order.
pub const KAPPA_REV: f64 = 2.0;
/// beta of P(E | C).
const BETA: f64 = 1.0;
/// An English string is at most this long, and a word the table lacks needs at least `UNSEEN_MIN` letters to be scored by its letters.
pub const MAX_LEN: usize = 32;
pub const UNSEEN_MIN: usize = 5;
/// Add-0.5 over the 26 letters and the end mark.
const TRI_ADD: f64 = 0.5;
const TRI_KINDS: f64 = 27.0;

/// The knobs of the comparison (the CLIs' `--auto-theta`, `--auto-kappa`, `--auto-kappa-dup`); the constants in an engine nothing has set.
#[derive(Clone, Copy, PartialEq, Debug)]
pub struct Params {
    pub theta: f64,
    pub kappa: f64,
    pub kappa_dup: f64,
    pub kappa_rev: f64,
}

impl Default for Params {
    fn default() -> Params {
        Params { theta: THETA, kappa: KAPPA, kappa_dup: KAPPA_DUP, kappa_rev: KAPPA_REV }
    }
}

/// Whether `e` is a string the comparison is made for: `[a-z][a-z0-9]{0,31}`.
pub fn valid(e: &str) -> bool {
    let b = e.as_bytes();
    !b.is_empty() && b.len() <= MAX_LEN && b[0].is_ascii_lowercase() && b.iter().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit())
}

/// The count table (docs/spec/english-mixed.md §16.5). Absent or malformed means the feature is off: `parse` returns `None` and says nothing else.
pub struct AutoTable {
    t: u64,
    n: u64,
    a: u64,
    d: HashMap<String, (u64, u64)>,
    e: HashMap<String, u64>,
    /// G(xyz) and G(xy.), over `^`, the letters and `$`.
    g3: HashMap<[u8; 3], u64>,
    g2: HashMap<[u8; 2], u64>,
    /// L(n) for n in UNSEEN_MIN..=MAX_LEN (other indices stay 0) and their sum.
    l: [u64; MAX_LEN + 1],
    sum_l: u64,
}

impl AutoTable {
    pub fn parse(text: &str) -> Option<AutoTable> {
        let mut tb = AutoTable { t: 0, n: 0, a: 0, d: HashMap::new(), e: HashMap::new(), g3: HashMap::new(), g2: HashMap::new(), l: [0; MAX_LEN + 1], sum_l: 0 };
        for line in text.split('\n') {
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let f: Vec<&str> = line.split('\t').collect();
            // Digits only: `parse` alone would take a leading `+`, which the Python reader refuses.
            let num = |i: usize| f.get(i).filter(|x| x.bytes().all(|b| b.is_ascii_digit())).and_then(|x| x.parse::<u64>().ok());
            match *f.first()? {
                "T" => tb.t = num(1)?,
                "N" => tb.n = num(1)?,
                "A" => tb.a = num(1)?,
                "D" => {
                    tb.d.insert(f.get(1)?.to_string(), (num(2)?, num(3)?));
                }
                "E" => {
                    let e = *f.get(1)?;
                    if !valid(e) || tb.e.insert(e.to_string(), num(2)?).is_some() {
                        return None;
                    }
                }
                "G" => {
                    let g: [u8; 3] = f.get(1)?.as_bytes().try_into().ok()?;
                    let ok = |c: u8, extra: u8| c.is_ascii_lowercase() || c == extra;
                    if !(ok(g[0], b'^') && ok(g[1], b'^') && ok(g[2], b'$')) || tb.g3.insert(g, num(2)?).is_some() {
                        return None;
                    }
                    *tb.g2.entry([g[0], g[1]]).or_default() += tb.g3[&g];
                }
                "L" => {
                    let n = num(1).map(|n| n as usize).filter(|n| (UNSEEN_MIN..=MAX_LEN).contains(n))?;
                    tb.l[n] = num(2)?;
                }
                _ => return None,
            }
        }
        tb.sum_l = tb.l.iter().sum();
        (tb.t > 0 && tb.n > 0).then_some(tb)
    }

    /// (N(C->E) + beta P(E)) / (N(C) + beta), P(E) = N(E) / T; no D row for `ctx`: P(E).
    pub fn p_e_given(&self, ctx: &str) -> f64 {
        let p = self.n as f64 / self.t as f64;
        match self.d.get(ctx) {
            None => p,
            Some(&(a, b)) => (a as f64 + BETA * p) / (b as f64 + BETA),
        }
    }

    /// log10 P_char(e): log10 P_L(|e|), then the log10 of each of the |e| + 1 trigram probabilities, left to right (`e` is letters only).
    fn lp_char(&self, e: &[u8]) -> f64 {
        let mut lp = ((self.l[e.len()] as f64 + 1.0) / (self.sum_l as f64 + (MAX_LEN - UNSEEN_MIN + 1) as f64)).log10();
        let (mut x, mut y) = (b'^', b'^');
        for &z in e.iter().chain(std::iter::once(&b'$')) {
            let g3 = self.g3.get(&[x, y, z]).copied().unwrap_or(0) as f64;
            let g2 = self.g2.get(&[x, y]).copied().unwrap_or(0) as f64;
            lp += ((g3 + TRI_ADD) / (g2 + TRI_ADD * TRI_KINDS)).log10();
            (x, y) = (y, z);
        }
        lp
    }

    /// log10 P(e | E): a word of the table by its count; a word of five or more letters the table lacks by the pruned mass and its letters;
    /// anything else (shorter, or with a digit) is minus infinity.
    pub fn lp_word(&self, e: &str) -> f64 {
        let den = self.n as f64 + self.a as f64;
        if let Some(&n) = self.e.get(e) {
            return (n as f64 / den).log10();
        }
        let b = e.as_bytes();
        if b.len() < UNSEEN_MIN || b.len() > MAX_LEN || !b.iter().all(u8::is_ascii_lowercase) || self.a == 0 {
            return f64::NEG_INFINITY;
        }
        (self.a as f64 / den).log10() + self.lp_char(b)
    }

    /// lpE = log10 P(E | C) + log10 P(e | E).
    pub fn lp_e(&self, ctx: &str, e: &str) -> f64 {
        self.p_e_given(ctx).log10() + self.lp_word(e)
    }

    /// Whether the table has an `E` row for `e` (tools: "not in the table").
    pub fn has(&self, e: &str) -> bool {
        self.e.contains_key(e)
    }
}

/// lpZ of a decision point: the log10 sum of the compatible words' probabilities, plus the reading term of the best of them, minus the
/// penalties of the keys that were replaced, replaced by the same key, and taken out of column order. The subtractions in this order.
pub fn lp_z(sum: f64, term: f64, n_ow: u32, n_dup: u32, n_rev: u32, p: &Params) -> f64 {
    sum + term - p.kappa * n_ow as f64 - p.kappa_dup * n_dup as f64 - p.kappa_rev * n_rev as f64
}

/// English wins only when strictly higher than Zhuyin plus theta.
pub fn takes_english(lp_e: f64, lp_z: f64, p: &Params) -> bool {
    lp_e > lp_z + p.theta
}

#[cfg(test)]
mod tests {
    use super::*;

    const TINY: &str = "# t\nT\t1000\nN\t40\nA\t10\nD\t用\t5\t20\nE\tok\t30\nE\tspeech\t10\nG\t^^s\t2\nG\t^sp\t1\nG\tspe\t1\nG\tee$\t1\nL\t5\t3\nL\t6\t1\n";

    #[test]
    fn the_string_rule() {
        for (e, ok) in [("ok", true), ("gta6", true), ("a", true), ("", false), ("6a", false), ("Ok", false), ("a.b", false), (&"a".repeat(32), true), (&"a".repeat(33), false)] {
            assert_eq!(valid(e), ok, "{e}");
        }
    }

    #[test]
    fn the_three_cases_of_a_word() {
        let t = AutoTable::parse(TINY).unwrap();
        assert_eq!(t.lp_word("ok"), (30.0f64 / 50.0).log10());
        assert!(t.lp_word("spell").is_finite() && t.lp_word("zzzzz").is_finite(), "an unseen trigram is still a number");
        assert!(t.lp_word("spell") > t.lp_word("zzzzz"));
        for e in ["zzzz", "abcd9", "ls"] {
            assert_eq!(t.lp_word(e), f64::NEG_INFINITY, "{e}");
        }
        assert_eq!(t.p_e_given("用"), (5.0 + 0.04) / 21.0);
        assert_eq!(t.p_e_given("我"), 0.04);
    }

    #[test]
    fn a_bad_table_is_no_table() {
        for bad in ["", "T\t0\nN\t1\n", "T\t9\nN\t1\nX\t1\n", "T\t9\nN\t1\nE\tOk\t1\n", "T\t9\nN\t1\nE\tok\t1\nE\tok\t2\n", "T\t9\nN\t1\nG\tab\t1\n", "T\t9\nN\t1\nL\t4\t1\n", "T\t9\nN\t1\nG\ta$b\t1\n"] {
            assert!(AutoTable::parse(bad).is_none(), "{bad:?}");
        }
    }

    #[test]
    fn a_tie_is_zhuyin() {
        let p = Params { theta: 1.0, kappa: 1.0, kappa_dup: 3.0, kappa_rev: 2.0 };
        assert_eq!(lp_z(-2.0, -0.5, 4, 1, 1, &p), -2.0 - 0.5 - 4.0 - 3.0 - 2.0);
        assert!(!takes_english(-1.0, -2.0, &p) && takes_english(-0.9, -2.0, &p));
        assert!(!takes_english(f64::NEG_INFINITY, f64::NEG_INFINITY, &p));
    }
}
