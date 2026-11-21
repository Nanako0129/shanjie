//! The reading-mass rule on the shipping lexicon (docs/spec/decoder.md §5; `ReadingDecode::with_mass`, variant T3b of the reading-counts B research):
//! the table after the rule (before the syllable normalisation, which reads it) differs from the table without the rule in exactly the pairs of
//! fixtures/reading-mass-pins.tsv, and there it has the pinned t. Then the syllable normalisation (`ReadingDecode::with_syllable_norm`): the table
//! `load_reading_decode` builds differs from the one before it in exactly the pairs of fixtures/reading-norm-pins.tsv, with the pinned t.
//! No data/lm is needed: only the lexicon in data/lexicon.
use core::engine::{load_lexicon_packs_detailed, load_reading_decode, READING_EXEMPT_FILE};
use core::reading::{ReadingDecode, MASS_MIN_DEDUCTION};
use std::collections::HashMap;
use std::path::{Path, PathBuf};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}

/// Rows `character TAB reading TAB t` of a dump, in order, `#` lines and blanks skipped.
fn rows(text: &str) -> Vec<(String, String, String)> {
    text.lines()
        .filter(|l| !l.is_empty() && !l.starts_with('#'))
        .map(|l| {
            let f: Vec<&str> = l.split('\t').collect();
            assert_eq!(f.len(), 3, "{l:?}");
            (f[0].to_string(), f[1].to_string(), f[2].to_string())
        })
        .collect()
}

#[test]
fn the_shipping_table_differs_from_the_table_without_the_rule_in_the_pinned_pairs_only() {
    let dir = root().join("data/lexicon");
    let (lex, _) = load_lexicon_packs_detailed(&dir, None).unwrap();
    let base = std::fs::read_to_string(dir.join("mcbpmf-data.txt")).unwrap();
    let exempt = std::fs::read_to_string(dir.join(READING_EXEMPT_FILE)).unwrap();
    let without = ReadingDecode::new(&lex, &base).with_exempt(&exempt).unwrap();
    // the table before the syllable normalisation: 11 of the 132 pinned pairs are moved by it (the second test pins those)
    let with = ReadingDecode::new(&lex, &base).with_exempt(&exempt).unwrap().with_mass(&lex, &base);
    assert_eq!(with.tag(), format!("{}+rm", without.tag()));

    let pins: HashMap<(String, String), f64> =
        rows(&std::fs::read_to_string(root().join("core/tests/fixtures/reading-mass-pins.tsv")).unwrap()).into_iter().map(|(c, r, t)| ((c, r), t.parse().unwrap())).collect();
    assert_eq!(pins.len(), 132);
    assert!(pins.values().all(|&t| t <= -MASS_MIN_DEDUCTION), "a pinned t is a deduction of at least {MASS_MIN_DEDUCTION}");

    let (new, old) = (rows(&with.dump_table(&lex)), rows(&without.dump_table(&lex)));
    assert_eq!(new.len(), old.len());
    assert!(new.len() > 8000, "the dump lists every pair of the characters with two readings");
    let mut seen = 0;
    for ((c, r, t), (c0, r0, t0)) in new.iter().zip(&old) {
        assert_eq!((c, r), (c0, r0));
        match pins.get(&(c.clone(), r.clone())) {
            Some(&want) => {
                seen += 1;
                let got: f64 = t.parse().unwrap();
                assert!((got - want).abs() < 1e-9, "{c} {r}: {got} vs {want}");
                assert_eq!(t0, "0.0", "{c} {r}: a pinned pair was 0 before the rule");
            }
            None => assert_eq!(t, t0, "{c} {r}: not pinned, so unchanged"),
        }
    }
    assert_eq!(seen, pins.len(), "every pinned pair is in the dump");
}

/// The syllable normalisation on the shipping lexicon: 61 pairs on 59 syllables move, each to the pinned t (59 to 0, two keep a smaller deduction);
/// 11 of them are pairs the mass rule set. Every other pair of the dump is, text for text, the table before the rule.
#[test]
fn the_syllable_normalisation_moves_the_pinned_pairs_only() {
    let dir = root().join("data/lexicon");
    let (lex, _) = load_lexicon_packs_detailed(&dir, None).unwrap();
    let with = load_reading_decode(&lex, &dir).unwrap();
    let base = std::fs::read_to_string(dir.join("mcbpmf-data.txt")).unwrap();
    let exempt = std::fs::read_to_string(dir.join(READING_EXEMPT_FILE)).unwrap();
    let before = ReadingDecode::new(&lex, &base).with_exempt(&exempt).unwrap().with_mass(&lex, &base);
    assert_eq!(with.tag(), format!("{}+rn", before.tag()));

    let pin_rows = rows(&std::fs::read_to_string(root().join("core/tests/fixtures/reading-norm-pins.tsv")).unwrap());
    let pins: HashMap<(String, String), f64> = pin_rows.iter().map(|(c, r, t)| ((c.clone(), r.clone()), t.parse().unwrap())).collect();
    assert_eq!((pin_rows.len(), pins.len()), (61, 61));
    assert_eq!(pins.keys().map(|(_, r)| r).collect::<std::collections::HashSet<_>>().len(), 59);
    let mass: Vec<(String, String, String)> = rows(&std::fs::read_to_string(root().join("core/tests/fixtures/reading-mass-pins.tsv")).unwrap());
    assert_eq!(mass.iter().filter(|(c, r, _)| pins.contains_key(&(c.clone(), r.clone()))).count(), 11);

    let (new, old) = (rows(&with.dump_table(&lex)), rows(&before.dump_table(&lex)));
    assert_eq!(new.len(), old.len());
    let mut seen = 0;
    for ((c, r, t), (c0, r0, t0)) in new.iter().zip(&old) {
        assert_eq!((c, r), (c0, r0));
        match pins.get(&(c.clone(), r.clone())) {
            Some(&want) => {
                seen += 1;
                let (got, was): (f64, f64) = (t.parse().unwrap(), t0.parse().unwrap());
                assert!((got - want).abs() < 1e-9, "{c} {r}: {got} vs {want}");
                assert!(was < got && got <= 0.0, "{c} {r}: the rule only takes a deduction away ({was} -> {got})");
            }
            None => assert_eq!(t, t0, "{c} {r}: not pinned, so unchanged"),
        }
    }
    assert_eq!(seen, pins.len(), "every pinned pair is in the dump");
}
