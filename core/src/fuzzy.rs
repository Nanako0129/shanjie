//! Fuzzy input tolerance tables (docs/contracts/fuzzy-input.md section 2.1), built once with the lexicon.
//! The same definitions as `experiments/fuzzy/fuzzy.py` (`Tolerance`, `compose`, `NEIGHBOURS`) so the two agree.
//!
//! * fuzzy sound: four pairs, one bit each (`FUZZY_PAIRS` order); a syllable's alternatives under a set of enabled pairs are the
//!   other lexicon syllables that read the same once every enabled pair is merged (tone marks are kept).
//! * adjacent key: the reverse of a typing slip. Replacing one symbol key of a lexicon syllable by a left/right neighbour in the
//!   same keyboard row (symbol keys of the standard layout only) and re-composing the keys through the composer's columns
//!   (s3a section 2: a key fills its own column, a second key of the same column replaces) shows some reading; the table maps
//!   that shown reading to the lexicon syllables it may have come from.

use crate::engine::Layout;
use std::collections::{BTreeMap, BTreeSet, HashMap};

/// Bits of the fuzzy flags (`shanjie_engine_set_fuzzy`, `--fuzzy`): 0-3 the sound pairs, 4 adjacent-key slips.
pub const FUZZY_PAIRS: [(char, char); 4] = [('ㄣ', 'ㄥ'), ('ㄓ', 'ㄗ'), ('ㄔ', 'ㄘ'), ('ㄕ', 'ㄙ')];
pub const FUZZY_SOUND: u32 = 0b1111;
pub const FUZZY_ADJACENT: u32 = 1 << 4;
pub const FUZZY_ALL: u32 = FUZZY_SOUND | FUZZY_ADJACENT;
/// Score taken off a word read through one tolerant syllable (log10, as the demotion deltas). The sound pairs' value is 2.5, the
/// cell docs/contracts/fuzzy-input.md section 3.5 chose with the shipping setting (the research's 1 was chosen with no word pack,
/// no small model and no Kneser-Ney, and let a tolerant common word cover the typed reading); the adjacent-key value is the research's.
pub const DELTA_SOUND: f64 = 2.5;
pub const DELTA_ADJACENT: f64 = 2.0;

/// Keyboard rows (research contract section 2): a key's neighbours are the keys left and right in its row.
const ROWS: [&str; 4] = ["1234567890-=", "qwertyuiop[]", "asdfghjkl;'", "zxcvbnm,./"];

fn is_symbol(c: char) -> bool {
    Layout::Standard.key_of_symbol(c).is_some()
}

/// Column (0 initial, 1 medial, 2 final) of a Zhuyin symbol.
fn column(sym: char) -> usize {
    let key = Layout::Standard.key_of_symbol(sym).unwrap_or('\0');
    Layout::Standard.symbol_of(key).map_or(2, |x| x.0)
}

/// Symbol -> the symbols on the standard-layout keys left and right of its key (tone keys and non-symbol keys dropped).
fn neighbours() -> HashMap<char, Vec<char>> {
    let sym = |k: char| Layout::Standard.symbol_of(k).map(|x| x.1);
    let mut out = HashMap::new();
    for row in ROWS {
        let keys: Vec<char> = row.chars().collect();
        for (i, &k) in keys.iter().enumerate() {
            let Some(s) = sym(k) else { continue };
            let near: Vec<char> = [i.checked_sub(1), Some(i + 1)].into_iter().flatten().filter_map(|j| keys.get(j).and_then(|&c| sym(c))).collect();
            out.insert(s, near);
        }
    }
    out
}

/// Splits a syllable into its symbols and its tone marks (everything else, in order).
fn split(syl: &str) -> (Vec<char>, String) {
    (syl.chars().filter(|&c| is_symbol(c)).collect(), syl.chars().filter(|&c| !is_symbol(c)).collect())
}

/// The reading the composer shows after these symbol keys, typed in order.
fn compose(keys: &[char]) -> String {
    let mut cols = [None; 3];
    for &c in keys {
        cols[column(c)] = Some(c);
    }
    cols.iter().flatten().collect()
}

/// `syl` with every enabled pair merged to the first symbol of the pair (the second maps to the first).
fn normal(syl: &str, pairs: u32) -> String {
    syl.chars()
        .map(|c| FUZZY_PAIRS.iter().enumerate().find(|&(b, p)| pairs >> b & 1 == 1 && c == p.1).map_or(c, |(_, p)| p.0))
        .collect()
}

/// The two tables. Syllables are the lexicon's interned ids.
pub struct Tolerance {
    /// Index = the enabled pairs (1..=15): syllable id -> the other syllables that read the same, sorted by name. Only syllables
    /// with at least one alternative are present.
    sound: Vec<HashMap<u32, Box<[u32]>>>,
    /// Shown reading -> lexicon syllables it may have been typed from, sorted by name.
    adjacent: HashMap<String, Box<[u32]>>,
}

impl Tolerance {
    pub(crate) fn build(names: &[String]) -> Tolerance {
        let by_name = |ids: BTreeSet<u32>| -> Box<[u32]> {
            let mut v: Vec<u32> = ids.into_iter().collect();
            v.sort_by(|&a, &b| names[a as usize].cmp(&names[b as usize]));
            v.into_boxed_slice()
        };
        let mut sound = vec![HashMap::new()];
        for pairs in 1..=FUZZY_SOUND {
            let mut groups: HashMap<String, BTreeSet<u32>> = HashMap::new();
            for (id, n) in names.iter().enumerate() {
                groups.entry(normal(n, pairs)).or_default().insert(id as u32);
            }
            let mut table = HashMap::new();
            for group in groups.into_values().filter(|g| g.len() > 1) {
                for &id in &group {
                    let mut others = group.clone();
                    others.remove(&id);
                    table.insert(id, by_name(others));
                }
            }
            sound.push(table);
        }
        let near = neighbours();
        let mut shown: BTreeMap<String, BTreeSet<u32>> = BTreeMap::new();
        for (id, n) in names.iter().enumerate() {
            let (keys, tone) = split(n);
            for i in 0..keys.len() {
                for &nb in near.get(&keys[i]).into_iter().flatten() {
                    let mut k = keys.clone();
                    k[i] = nb;
                    let s = compose(&k) + &tone;
                    if s != *n {
                        shown.entry(s).or_default().insert(id as u32);
                    }
                }
            }
        }
        Tolerance { sound, adjacent: shown.into_iter().map(|(s, ids)| (s, by_name(ids))).collect() }
    }

    /// Alternatives of syllable `id` with the pairs in `flags` (bits 0-3) enabled.
    pub(crate) fn sound(&self, id: u32, flags: u32) -> &[u32] {
        self.sound.get((flags & FUZZY_SOUND) as usize).and_then(|t| t.get(&id)).map_or(&[], |b| b)
    }

    /// Lexicon syllables a shown reading may have been typed from.
    pub(crate) fn adjacent(&self, shown: &str) -> &[u32] {
        self.adjacent.get(shown).map_or(&[], |b| b)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::Lexicon;

    /// A lexicon whose syllables are exactly `syls` (one word each).
    fn lex(syls: &[&str]) -> Lexicon {
        let text: String = syls.iter().enumerate().map(|(i, s)| format!("{s} {} -1.0\n", char::from_u32(0x4e00 + i as u32).unwrap())).collect();
        Lexicon::parse(&text).unwrap()
    }

    /// Each pair has its own bit, in the order ㄣ/ㄥ, ㄓ/ㄗ, ㄔ/ㄘ, ㄕ/ㄙ; a syllable's alternatives under a set of bits merge only the
    /// enabled pairs; the tone mark is kept; a syllable without a counterpart in the lexicon has no alternative.
    #[test]
    fn sound_pairs_by_bit() {
        let l = lex(&["ㄇㄣ", "ㄇㄥ", "ㄓ", "ㄗ", "ㄔ", "ㄘ", "ㄕ", "ㄙ", "ㄓㄣˊ", "ㄗㄥˊ", "ㄓㄥˊ", "ㄗㄣˊ", "ㄇㄚ"]);
        for (bit, (a, b)) in [("ㄇㄣ", "ㄇㄥ"), ("ㄓ", "ㄗ"), ("ㄔ", "ㄘ"), ("ㄕ", "ㄙ")].into_iter().enumerate() {
            assert_eq!(l.sound_alts(a, 1 << bit), [b], "bit {bit}: {a}");
            assert_eq!(l.sound_alts(b, 1 << bit), [a], "bit {bit}: {b}");
            for other in (0..4).filter(|&o| o != bit) {
                assert!(l.sound_alts(a, 1 << other).is_empty(), "bit {other} does not touch {a}");
            }
        }
        assert!(l.sound_alts("ㄇㄣ", 0).is_empty(), "all off");
        assert!(l.sound_alts("ㄇㄚ", 0b1111).is_empty(), "no counterpart");
        assert!(l.sound_alts("ㄅㄩ", 0b1111).is_empty(), "not a lexicon syllable");
        // ㄓㄣˊ under ㄣ/ㄥ only: ㄓㄥˊ; under ㄓ/ㄗ only: ㄗㄣˊ; under both: the other three, sorted by name.
        assert_eq!(l.sound_alts("ㄓㄣˊ", 0b01), ["ㄓㄥˊ"]);
        assert_eq!(l.sound_alts("ㄓㄣˊ", 0b10), ["ㄗㄣˊ"]);
        assert_eq!(l.sound_alts("ㄓㄣˊ", 0b11), ["ㄓㄥˊ", "ㄗㄣˊ", "ㄗㄥˊ"]);
        // Bits above the sound pairs are not part of the lookup.
        assert_eq!(l.sound_alts("ㄓㄣˊ", FUZZY_ADJACENT | 0b01), ["ㄓㄥˊ"]);
    }

    /// The research's example: ㄇㄧㄥ with ㄧ (key u) typed as the neighbour ㄛ (key i) shows ㄇㄥ (the final column takes ㄥ over ㄛ),
    /// and as ㄗ (key y) shows ㄗㄥ (the initial column takes ㄗ over ㄇ). The tone stays.
    #[test]
    fn adjacent_follows_the_composer_columns() {
        let l = lex(&["ㄇㄧㄥˊ", "ㄇㄥˊ", "ㄗㄥˊ", "ㄅㄚ", "ㄉㄚ", "ㄊㄚ", "ㄆㄚ", "ㄍㄚ"]);
        assert_eq!(l.adjacent_alts("ㄇㄥˊ"), ["ㄇㄧㄥˊ"]);
        assert_eq!(l.adjacent_alts("ㄗㄥˊ"), ["ㄇㄧㄥˊ"]);
        assert!(l.adjacent_alts("ㄇㄥ").is_empty(), "a different tone is a different reading");
        // Left and right in the row only: ㄅ (1) has ㄉ (2) on its right and nothing on its left; ㄉ's right is the tone key 3,
        // which is no symbol, so ㄉㄚ comes only from ㄅㄚ and ㄅㄚ only from ㄉㄚ.
        assert_eq!(l.adjacent_alts("ㄉㄚ"), ["ㄅㄚ"]);
        assert_eq!(l.adjacent_alts("ㄅㄚ"), ["ㄉㄚ"]);
        // ㄊ (w) is between ㄆ (q) and ㄍ (e): either neighbour shows, and each shown reading maps back to ㄊㄚ.
        assert_eq!(l.adjacent_alts("ㄆㄚ"), ["ㄊㄚ"]);
        assert_eq!(l.adjacent_alts("ㄍㄚ"), ["ㄊㄚ"]);
        // A syllable is never its own alternative, and an unrelated reading has none.
        assert!(l.adjacent_alts("ㄏㄚ").is_empty());
    }

    /// Section 2.1 "one tolerant syllable per word": a span with one unknown syllable replaces only that one (the legal wrong
    /// syllable next to it is read as typed); two unknown syllables have no tolerant reading; a span of known syllables changes
    /// one syllable at a time, and only for the enabled pairs.
    #[test]
    fn tolerant_readings_change_one_syllable_per_word() {
        // ㄔ and ㄋㄣ are here so that the legal ㄗ and ㄇㄣ below do have adjacent readings (ㄔ typed with its neighbour ㄗ; ㄋ with ㄇ).
        let l = lex(&["ㄅㄝ", "ㄗ", "ㄓ", "ㄉㄠˋ", "ㄇㄣ", "ㄇㄥ", "ㄔ", "ㄋㄣ"]);
        let s = |v: &[&str]| v.iter().map(|x| x.to_string()).collect::<Vec<_>>();
        let name = |r: &[u32]| r.iter().map(|&i| l.syl_names[i as usize].as_str()).collect::<Vec<_>>().join("-");
        let readings = |v: &[&str], flags: u32| {
            let syls = s(v);
            l.tolerant_readings(&l.ids(&syls), &syls, flags).iter().map(|r| name(r)).collect::<Vec<_>>()
        };
        // ㄅㄩ is not a lexicon syllable (ㄅㄝ typed with ㄝ as its neighbour ㄩ); ㄗ beside it is legal and stays, even with every flag on.
        assert_eq!(readings(&["ㄅㄩ", "ㄗ"], FUZZY_ALL), ["ㄅㄝ-ㄗ"]);
        assert_eq!(readings(&["ㄅㄩ", "ㄅㄩ"], FUZZY_ALL), Vec::<String>::new());
        assert_eq!(readings(&["ㄗ", "ㄇㄣ"], 0b11), ["ㄓ-ㄇㄣ", "ㄗ-ㄇㄥ"]);
        assert_eq!(readings(&["ㄗ", "ㄇㄣ"], 0b10), ["ㄓ-ㄇㄣ"]);
        assert_eq!(readings(&["ㄗ", "ㄇㄣ"], 0), Vec::<String>::new());
        assert_eq!(readings(&["ㄗ", "ㄇㄣ"], FUZZY_ADJACENT), Vec::<String>::new(), "bit 4 alone tolerates no known syllable");
    }
}
