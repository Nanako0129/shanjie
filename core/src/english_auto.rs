//! English or Zhuyin at the closing key of a syllable (docs/spec/english-mixed.md §16): the count table `english.tsv` and the two scores
//! the engine compares at a decision point. A port of reference/proto/english_score.py (`Table`); Python is the baseline and
//! `core/tests/engine_auto_english.rs` compares `lp_e` bit for bit. Every sum is a plain left-to-right float sum in the order written here.

/// The shipping cell of the grid theta in {1, 2, 3} by kappa in {1, 1.5, 2} by kappa_dup in {0, 3} (docs/spec/english-mixed.md §16.4): the cell
/// the registered rule picked (every cell was feasible; this one has the highest English first-place rate on the tuning probe).
pub const THETA: f64 = 1.0;
pub const KAPPA: f64 = 2.0;
pub const KAPPA_DUP: f64 = 3.0;
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
///
/// Every key lives once in `arena`; each section is a flat array sorted by key and read by binary search (see `DigitTable`). A repeated `D` or `L` row
/// keeps its last value; a repeated `E` or `G` row makes the table malformed.
pub struct AutoTable {
    t: u64,
    n: u64,
    a: u64,
    arena: Box<str>,
    d: Box<[(Span, u64, u64)]>,
    e: Box<[(Span, u64)]>,
    /// G(xyz) and G(xy.), over `^`, the letters and `$`.
    g3: Box<[([u8; 3], u64)]>,
    g2: Box<[([u8; 2], u64)]>,
    /// L(n) for n in UNSEEN_MIN..=MAX_LEN (other indices stay 0) and their sum.
    l: [u64; MAX_LEN + 1],
    sum_l: u64,
}

/// A string inside a table's arena.
#[derive(Clone, Copy)]
struct Span {
    off: u32,
    len: u16,
}

fn intern(arena: &mut String, s: &str) -> Option<Span> {
    let len = u16::try_from(s.len()).ok()?;
    let off = u32::try_from(arena.len()).ok()?;
    u32::try_from(arena.len().checked_add(s.len())?).ok()?;
    arena.push_str(s);
    Some(Span { off, len })
}

impl AutoTable {
    pub fn parse(text: &str) -> Option<AutoTable> {
        let (mut t, mut n, mut a, mut l) = (0, 0, 0, [0; MAX_LEN + 1]);
        let (mut d, mut e, mut g3): (Vec<(&str, u64, u64)>, Vec<(&str, u64)>, Vec<([u8; 3], u64)>) = (Vec::new(), Vec::new(), Vec::new());
        for line in text.split('\n') {
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let f: Vec<&str> = line.split('\t').collect();
            // Digits only: `parse` alone would take a leading `+`, which the Python reader refuses.
            let num = |i: usize| f.get(i).filter(|x| x.bytes().all(|b| b.is_ascii_digit())).and_then(|x| x.parse::<u64>().ok());
            match *f.first()? {
                "T" => t = num(1)?,
                "N" => n = num(1)?,
                "A" => a = num(1)?,
                "D" => d.push((*f.get(1)?, num(2)?, num(3)?)),
                "E" => {
                    let k = *f.get(1)?;
                    if !valid(k) {
                        return None;
                    }
                    e.push((k, num(2)?));
                }
                "G" => {
                    let g: [u8; 3] = f.get(1)?.as_bytes().try_into().ok()?;
                    let ok = |c: u8, extra: u8| c.is_ascii_lowercase() || c == extra;
                    if !(ok(g[0], b'^') && ok(g[1], b'^') && ok(g[2], b'$')) {
                        return None;
                    }
                    g3.push((g, num(2)?));
                }
                "L" => {
                    let k = num(1).and_then(|n| usize::try_from(n).ok()).filter(|k| (UNSEEN_MIN..=MAX_LEN).contains(k))?;
                    l[k] = num(2)?;
                }
                _ => return None,
            }
        }
        // A repeated D row: the last one wins. A repeated E or G row: no table.
        d.sort_by_key(|r| r.0);
        d.reverse();
        d.dedup_by_key(|r| r.0);
        d.reverse();
        e.sort_by_key(|r| r.0);
        g3.sort_by_key(|r| r.0);
        if e.windows(2).any(|w| w[0].0 == w[1].0) || g3.windows(2).any(|w| w[0].0 == w[1].0) {
            return None;
        }
        let mut g2: Vec<([u8; 2], u64)> = Vec::new();
        for &(g, c) in &g3 {
            match g2.last_mut() {
                Some(last) if last.0 == [g[0], g[1]] => last.1 = last.1.checked_add(c)?,
                _ => g2.push(([g[0], g[1]], c)),
            }
        }
        let sum_l = l.iter().try_fold(0u64, |s, &x| s.checked_add(x))?;
        if !(t > 0 && n > 0) {
            return None;
        }
        let mut arena = String::with_capacity(d.iter().map(|r| r.0.len()).chain(e.iter().map(|r| r.0.len())).sum());
        let d: Option<Vec<_>> = d.iter().map(|&(k, x, y)| Some((intern(&mut arena, k)?, x, y))).collect();
        let e: Option<Vec<_>> = e.iter().map(|&(k, x)| Some((intern(&mut arena, k)?, x))).collect();
        Some(AutoTable { t, n, a, arena: arena.into_boxed_str(), d: d?.into(), e: e?.into(), g3: g3.into(), g2: g2.into(), l, sum_l })
    }

    fn s(&self, sp: Span) -> &str {
        &self.arena[sp.off as usize..sp.off as usize + sp.len as usize]
    }

    /// The row of `key` in a section sorted by key.
    fn find<'a, T>(&self, rows: &'a [T], span: impl Fn(&T) -> Span, key: &str) -> Option<&'a T> {
        rows.binary_search_by(|r| self.s(span(r)).cmp(key)).ok().map(|i| &rows[i])
    }

    /// (N(C->E) + beta P(E)) / (N(C) + beta), P(E) = N(E) / T; no D row for `ctx`: P(E).
    pub fn p_e_given(&self, ctx: &str) -> f64 {
        let p = self.n as f64 / self.t as f64;
        match self.find(&self.d, |r| r.0, ctx) {
            None => p,
            Some(&(_, a, b)) => (a as f64 + BETA * p) / (b as f64 + BETA),
        }
    }

    /// log10 P_char(e): log10 P_L(|e|), then the log10 of each of the |e| + 1 trigram probabilities, left to right (`e` is letters only).
    fn lp_char(&self, e: &[u8]) -> f64 {
        let mut lp = ((self.l[e.len()] as f64 + 1.0) / (self.sum_l as f64 + (MAX_LEN - UNSEEN_MIN + 1) as f64)).log10();
        let (mut x, mut y) = (b'^', b'^');
        for &z in e.iter().chain(std::iter::once(&b'$')) {
            let g3 = self.g3.binary_search_by_key(&[x, y, z], |r| r.0).ok().map_or(0, |i| self.g3[i].1) as f64;
            let g2 = self.g2.binary_search_by_key(&[x, y], |r| r.0).ok().map_or(0, |i| self.g2[i].1) as f64;
            lp += ((g3 + TRI_ADD) / (g2 + TRI_ADD * TRI_KINDS)).log10();
            (x, y) = (y, z);
        }
        lp
    }

    /// log10 P(e | E): a word of the table by its count; a word of five or more letters the table lacks by the pruned mass and its letters;
    /// anything else (shorter, or with a digit) is minus infinity.
    pub fn lp_word(&self, e: &str) -> f64 {
        let den = self.n as f64 + self.a as f64;
        if let Some(&(_, n)) = self.find(&self.e, |r| r.0, e) {
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
        self.find(&self.e, |r| r.0, e).is_some()
    }

    /// Every `E` row: the word and its count (docs/spec/prediction.md §3.5: the English completions of an open run).
    pub fn words(&self) -> impl Iterator<Item = (&str, u64)> {
        self.e.iter().map(|&(w, n)| (self.s(w), n))
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
    use std::collections::{HashMap, HashSet};

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

    /// `AutoTable` as it was before the flat arrays (a `HashMap` per section), kept so that the test below can compare the two bit for bit on the shipped table.
    struct AutoTableOracle {
        t: u64,
        n: u64,
        a: u64,
        d: HashMap<String, (u64, u64)>,
        e: HashMap<String, u64>,
        g3: HashMap<[u8; 3], u64>,
        g2: HashMap<[u8; 2], u64>,
        l: [u64; MAX_LEN + 1],
        sum_l: u64,
    }

    impl AutoTableOracle {
        fn parse(text: &str) -> Option<AutoTableOracle> {
            let mut tb = AutoTableOracle { t: 0, n: 0, a: 0, d: HashMap::new(), e: HashMap::new(), g3: HashMap::new(), g2: HashMap::new(), l: [0; MAX_LEN + 1], sum_l: 0 };
            for line in text.split('\n') {
                if line.is_empty() || line.starts_with('#') {
                    continue;
                }
                let f: Vec<&str> = line.split('\t').collect();
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

        fn p_e_given(&self, ctx: &str) -> f64 {
            let p = self.n as f64 / self.t as f64;
            match self.d.get(ctx) {
                None => p,
                Some(&(a, b)) => (a as f64 + BETA * p) / (b as f64 + BETA),
            }
        }

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

        fn lp_word(&self, e: &str) -> f64 {
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

        fn lp_e(&self, ctx: &str, e: &str) -> f64 {
            self.p_e_given(ctx).log10() + self.lp_word(e)
        }
    }

    fn shipped() -> String {
        std::fs::read_to_string(std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../data/lexicon/english.tsv")).unwrap()
    }

    /// A key, the key plus a trailing U+1, and the key without its last character.
    fn near(keys: &[&str]) -> Vec<String> {
        let mut out = vec![String::new()];
        for k in keys {
            let mut cut = k.to_string();
            cut.pop();
            out.extend([k.to_string(), format!("{k}\u{1}"), cut]);
        }
        out
    }

    #[test]
    fn auto_table_flat_equals_hashmap_oracle() {
        let text = shipped();
        let (new, old) = (AutoTable::parse(&text).unwrap(), AutoTableOracle::parse(&text).unwrap());
        let (mut dkeys, mut ekeys): (Vec<&str>, Vec<&str>) = (old.d.keys().map(String::as_str).collect(), old.e.keys().map(String::as_str).collect());
        dkeys.sort();
        ekeys.sort();
        assert!(new.d.len() == old.d.len() && new.e.len() == old.e.len() && new.g3.len() == old.g3.len() && new.g2.len() == old.g2.len());
        // (h) p_e_given
        let mut n_h = 0;
        for k in near(&dkeys).iter().chain(&near(&ekeys)) {
            assert_eq!(new.p_e_given(k).to_bits(), old.p_e_given(k).to_bits(), "p_e_given {k:?}");
            n_h += 1;
        }
        // (i) lp_word and (k) has: E keys, E keys + q, E keys minus the last letter, unseen strings of 1 to 33 characters
        let mut strings: Vec<String> = ekeys.iter().flat_map(|k| [k.to_string(), format!("{k}q"), k[..k.len() - 1].to_string()]).collect();
        strings.extend((1..=33).flat_map(|n| ["zqxjv", "abcde", "spell", "q"].map(|s| s.chars().cycle().take(n).collect::<String>())));
        let mut n_i = 0;
        for e in &strings {
            assert_eq!(new.lp_word(e).to_bits(), old.lp_word(e).to_bits(), "lp_word {e:?}");
            assert_eq!(new.has(e), old.e.contains_key(e), "has {e:?}");
            n_i += 1;
        }
        // (j) lp_e: every D key x 8 fixed strings
        let fixed = [ekeys[0].to_string(), ekeys[ekeys.len() - 1].to_string(), "zqxjv".to_string(), "zqxj".to_string(), "gta6".to_string(), String::new(), "a".repeat(32), "a".repeat(33)];
        let mut n_j = 0;
        for c in &dkeys {
            for e in &fixed {
                assert_eq!(new.lp_e(c, e).to_bits(), old.lp_e(c, e).to_bits(), "lp_e {c:?} {e:?}");
                n_j += 1;
            }
        }
        // (l) words
        let got: Vec<(String, u64)> = new.words().map(|(w, n)| (w.to_string(), n)).collect();
        let (got_set, old_set): (HashSet<_>, HashSet<_>) = (got.iter().cloned().collect(), old.e.iter().map(|(w, n)| (w.clone(), *n)).collect());
        assert_eq!(got.len(), got_set.len());
        assert_eq!(got_set, old_set);
        assert!(!dkeys.is_empty() && !ekeys.is_empty(), "the shipped english.tsv has D and E rows: an empty section would make the counts below vacuous");
        assert!(n_h >= dkeys.len() + ekeys.len() && n_i >= ekeys.len() && n_j == dkeys.len() * 8 && got.len() == old.e.len());
        println!("oracle-compared english p_e_given={n_h} lp_word/has={n_i} lp_e={n_j} words={} (D={} E={} G3={} G2={})", got.len(), dkeys.len(), ekeys.len(), new.g3.len(), new.g2.len());
    }

    #[test]
    fn repeated_rows_and_limits() {
        let head = "T\t100\nN\t10\nA\t5\n";
        // D and L: the last row wins; E and G repeated: no table.
        let t = AutoTable::parse(&format!("{head}D\ta\t1\t2\nD\ta\t3\t4\nL\t5\t7\nL\t5\t8\nE\tok\t3\nG\t^^s\t2\nG\t^^t\t3\n")).unwrap();
        assert_eq!(t.p_e_given("a").to_bits(), ((3.0 + 0.1) / 5.0f64).to_bits());
        assert_eq!((t.sum_l, t.g2.len(), t.g2[0].1), (8, 1, 5), "G rows of one (x, y) pair add up in `g2`");
        assert!(AutoTable::parse(&format!("{head}E\tok\t1\nE\tok\t2\n")).is_none());
        assert!(AutoTable::parse(&format!("{head}G\t^^s\t1\nG\t^^s\t2\n")).is_none());
        assert!(AutoTable::parse("T\t100\r\nN\t10\n").is_none(), "CRLF keeps the CR in the number");
        let big = AutoTable::parse(&format!("{head}E\tok\t{}\n", u64::from(u32::MAX) + 5)).unwrap();
        assert_eq!(big.words().collect::<Vec<_>>(), [("ok", u64::from(u32::MAX) + 5)]);
        assert!(AutoTable::parse(&format!("{head}L\t5\t{}\n", u64::from(u32::MAX) + 5)).is_some());
        assert!(AutoTable::parse(&format!("{head}L\t{}\t1\n", u64::from(u32::MAX) + 5)).is_none(), "an L length past u32::MAX is no table");
        assert!(AutoTable::parse(&format!("{head}L\t5\t{}\nL\t6\t1\n", u64::MAX)).is_none(), "sum_l overflow is a None, not a panic");
        let key = |n: usize| "a".repeat(n);
        assert!(AutoTable::parse(&format!("{head}D\t{}\t1\t1\n", key(u16::MAX as usize))).is_some());
        assert!(AutoTable::parse(&format!("{head}D\t{}\t1\t1\n", key(u16::MAX as usize + 1))).is_none());
    }
}
