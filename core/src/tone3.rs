//! Third-tone sandhi (docs/spec/decoder.md §6.6): a user who types what is said (眨眼 as ㄓㄚˊ ㄧㄢˇ, 小姐 as ㄒㄧㄠˊ ㄐㄧㄝ˙) still gets the
//! word, whose lexicon reading has the third tone.
//!
//! `positions` is the one position table: the engine (decode, candidate window, `path_readings`, the learning guard), the evaluation CLI
//! and `shanjie-replay` (through the engine) all call it on the whole typed input; nothing else works the positions out.

use crate::Lexicon;

/// Taken once off the score of a word found through the sandhi source (log10, as `fuzzy::DELTA_SOUND`).
/// tone3-sandhi §3.2 格點（{0.5,1.0,1.5,2.0,2.5,3.0}），只有 3.0 讓 wikitune-clean-tw0 原讀音不退步；量測 commit 3354810e
pub const DELTA_TONE3: f64 = 3.0;
/// The most subsets of a span's sandhi positions that are looked up (smallest first, then left to right): with k ≤ 4 positions that is all of them
/// (2^4 − 1 = 15); 營養品 typed ㄧㄥˊ ㄧㄤˊ ㄆㄧㄣˇ needs {ㄧㄤˊ} and 展覽館 needs {ㄓㄢˊ, ㄌㄢˊ}, both among the first 15 (contract tone3-sandhi §2 C).
pub const TONE3_MAX_SUBSETS: usize = 15;

/// What the table says about one typed syllable. `alt` is the syllable id of the replacement reading (ˊ → ˇ at a sandhi position, ˙X → Xˇ at a
/// neutral tail); `None` when the position has neither, or the replacement is no lexicon syllable at all.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Pos {
    /// A sandhi position: typed with the second tone, and the first syllable after it that is not second tone is third tone or a neutral tail;
    /// or an unaccepted syllable, wherever it stands.
    pub sandhi: bool,
    /// A neutral tail: ˙X right after a second-tone syllable, with Xˇ a one-syllable entry; or an unaccepted neutral tail, whatever stands before it.
    pub tail: bool,
    /// An unaccepted syllable (Xˊ without a one-syllable entry, Xˇ with one) or an unaccepted neutral tail (˙X without, Xˇ with).
    pub unaccepted: bool,
    pub alt: Option<u32>,
}

fn rise(s: &str) -> bool {
    s.ends_with('ˊ')
}

fn neutral(s: &str) -> bool {
    s.ends_with('˙')
}

/// Xˇ for Xˊ or X˙ (the mark is the last character); `None` for anything else.
pub fn third(s: &str) -> Option<String> {
    let body = s.strip_suffix('ˊ').or_else(|| s.strip_suffix('˙'))?;
    (!body.is_empty() && !body.starts_with('_')).then(|| format!("{body}ˇ"))
}

/// docs/spec/decoder.md §6.6: Xˊ the lexicon has no one-syllable entry for while Xˇ has one (ㄨㄛˊ, ㄍㄟˊ, ㄓㄢˊ). `finish_syllable` accepts it (with a
/// model and the switch on); its score is Xˇ's.
pub fn unaccepted(lex: &Lexicon, s: &str) -> bool {
    rise(s) && !lex.has_single(s) && third(s).is_some_and(|t| lex.has_single(&t))
}

/// An unaccepted neutral tail: ˙X without a one-syllable entry, Xˇ with one (˙ㄒㄧㄤ, ˙ㄏㄨ). `finish_syllable` accepts it only right after a second-tone
/// syllable; once in the composition it keeps its replacement whatever comes to stand before it.
pub fn unaccepted_tail(lex: &Lexicon, s: &str) -> bool {
    neutral(s) && !lex.has_single(s) && third(s).is_some_and(|t| lex.has_single(&t))
}

/// The position table of the typed syllables `syls` (punctuation and English tokens are no syllables: they end a run of second tones like any other
/// syllable that is not second tone). `on` false (the switch): every position empty.
pub fn positions(syls: &[String], lex: &Lexicon, on: bool) -> Vec<Pos> {
    let mut out = vec![Pos::default(); syls.len()];
    if !on {
        return out;
    }
    let alt = |s: &str| third(s).and_then(|t| lex.syl_id(&t));
    for j in 0..syls.len() {
        let s = &syls[j];
        let ut = unaccepted_tail(lex, s);
        let tail = ut || (neutral(s) && j > 0 && rise(&syls[j - 1]) && third(s).is_some_and(|t| lex.has_single(&t)));
        if tail {
            out[j] = Pos { sandhi: false, tail: true, unaccepted: ut, alt: alt(s) };
        }
    }
    for i in 0..syls.len() {
        let s = &syls[i];
        if !rise(s) || s.starts_with('_') {
            continue;
        }
        let u = unaccepted(lex, s);
        // The first syllable after i that is not second tone: third tone, or a neutral tail.
        let next = (i + 1..syls.len()).find(|&k| !rise(&syls[k]) || syls[k].starts_with('_'));
        let sandhi = u || next.is_some_and(|k| syls[k].ends_with('ˇ') && !syls[k].starts_with('_') || out[k].tail);
        if sandhi {
            out[i] = Pos { sandhi: true, tail: false, unaccepted: u, alt: alt(s) };
        }
    }
    out
}

/// The readings (syllable ids) a span is looked up under through the sandhi source: the subsets of its positions with a replacement, smallest first and
/// left to right within a size, each with its positions replaced; at most `TONE3_MAX_SUBSETS` subsets are looked up. A neutral tail is replaced only
/// together with the sandhi position right before it when that one is in the span too. Empty when the span has no replacement.
pub fn readings(ids: &[u32], t3: &[Pos]) -> Vec<Vec<u32>> {
    let elems: Vec<usize> = (0..ids.len().min(t3.len())).filter(|&j| t3[j].alt.is_some()).collect();
    let k = elems.len();
    let mut out = Vec::new();
    for size in 1..=k {
        // Combinations of `size` out of `k`, in lexicographic order of the element indices.
        let mut c: Vec<usize> = (0..size).collect();
        loop {
            let set: Vec<usize> = c.iter().map(|&x| elems[x]).collect();
            let tied = set.iter().all(|&j| !(t3[j].tail && j > 0 && t3[j - 1].sandhi && t3[j - 1].alt.is_some()) || set.contains(&(j - 1)));
            if tied {
                if out.len() == TONE3_MAX_SUBSETS {
                    return out;
                }
                let mut r = ids.to_vec();
                for &j in &set {
                    r[j] = t3[j].alt.unwrap_or(r[j]);
                }
                out.push(r);
            }
            // Next combination.
            let Some(i) = (0..size).rev().find(|&i| c[i] != i + k - size) else { break };
            c[i] += 1;
            for x in i + 1..size {
                c[x] = c[x - 1] + 1;
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A lexicon whose one-syllable readings are `singles` (one word each) plus the multi-syllable rows `multi` ("ㄅ-ㄆ 詞").
    fn lex(singles: &[&str], multi: &[&str]) -> Lexicon {
        let mut text: String = singles.iter().enumerate().map(|(i, s)| format!("{s} {} -1.0\n", char::from_u32(0x4e00 + i as u32).unwrap())).collect();
        for m in multi {
            text.push_str(&format!("{m} -2.0\n"));
        }
        Lexicon::parse(&text).unwrap()
    }

    fn v(s: &[&str]) -> Vec<String> {
        s.iter().map(|x| x.to_string()).collect()
    }

    fn kinds(t: &[Pos]) -> String {
        t.iter().map(|p| if p.sandhi { 'S' } else if p.tail { 'T' } else { '.' }).collect()
    }

    /// Contract tone3-sandhi §5: ˊˇ, ˊˊˇ, ˇˊˇ, ˊˇˊˇ, ˊˊ˙; a ˊ before a syllable that is not third tone is no position; the last syllable is none; an
    /// unaccepted syllable always is.
    #[test]
    fn sandhi_positions() {
        let l = lex(&["ㄓㄚˊ", "ㄓㄚˇ", "ㄧㄢˇ", "ㄧㄢˊ", "ㄌㄢˊ", "ㄌㄢˇ", "ㄍㄨㄢˇ", "ㄒㄧㄤˊ", "ㄒㄧㄤˇ", "ㄏㄠˇ", "ㄏㄠˋ", "ㄨㄛˇ", "ㄒㄧㄠˇ"], &[]);
        let k = |s: &[&str]| kinds(&positions(&v(s), &l, true));
        assert_eq!(k(&["ㄓㄚˊ", "ㄧㄢˇ"]), "S.");
        assert_eq!(k(&["ㄧㄢˊ", "ㄌㄢˊ", "ㄍㄨㄢˇ"]), "SS.");
        assert_eq!(k(&["ㄒㄧㄠˇ", "ㄌㄢˊ", "ㄏㄠˇ"]), ".S.");
        assert_eq!(k(&["ㄓㄚˊ", "ㄧㄢˇ", "ㄌㄢˊ", "ㄏㄠˇ"]), "S.S.");
        assert_eq!(k(&["ㄒㄧㄤˊ", "ㄒㄧㄤˊ", "ㄒㄧㄤ˙"]), "SST");
        assert_eq!(k(&["ㄓㄚˊ", "ㄏㄠˋ"]), "..", "a ˊ before a fourth tone");
        assert_eq!(k(&["ㄓㄚˊ", "ㄧㄢˊ", "ㄏㄠˋ"]), "...", "the first syllable after the run of ˊ decides");
        assert_eq!(k(&["ㄏㄠˋ", "ㄓㄚˊ"]), "..", "the last syllable is no position");
        // ㄨㄛˊ has no one-syllable entry and ㄨㄛˇ has one: a position wherever it stands, the last one and before a fourth tone included.
        assert_eq!(k(&["ㄏㄠˋ", "ㄨㄛˊ"]), ".S");
        assert_eq!(k(&["ㄨㄛˊ", "ㄏㄠˋ"]), "S.");
        let t = positions(&v(&["ㄨㄛˊ"]), &l, true);
        assert!(t[0].unaccepted && t[0].alt == l.syl_id("ㄨㄛˇ"));
        // A second tone whose third tone is no lexicon syllable has no replacement (nothing to look up, no error).
        let t = positions(&v(&["ㄧㄢˊ", "ㄇㄧㄠˊ", "ㄏㄠˇ"]), &l, true);
        assert_eq!(kinds(&t), "SS.");
        assert_eq!(t[1].alt, None);
        // A punctuation token ends a run like any syllable that is not third tone.
        assert_eq!(k(&["ㄓㄚˊ", "_punct_，", "ㄧㄢˇ"]), "...");
        // Off: every position empty.
        assert!(positions(&v(&["ㄓㄚˊ", "ㄧㄢˇ", "ㄨㄛˊ"]), &l, false).iter().all(|p| *p == Pos::default()));
    }

    /// A neutral tail needs a second tone before it and Xˇ with a one-syllable entry; an unaccepted one (˙X without an entry) is a tail whatever stands before it.
    #[test]
    fn neutral_tails() {
        let l = lex(&["ㄒㄧㄠˊ", "ㄒㄧㄠˇ", "ㄐㄧㄝˇ", "ㄐㄧㄝ˙", "ㄌㄠˇ", "ㄏㄨˇ", "ㄏㄠˋ"], &[]);
        let k = |s: &[&str]| kinds(&positions(&v(s), &l, true));
        assert_eq!(k(&["ㄒㄧㄠˊ", "ㄐㄧㄝ˙"]), "ST");
        assert_eq!(k(&["ㄏㄠˋ", "ㄐㄧㄝ˙"]), "..", "an accepted ˙X after a fourth tone is no tail");
        // ˙ㄏㄨ has no entry, ㄏㄨˇ has one: unaccepted, a tail even after a fourth tone or alone.
        let t = positions(&v(&["ㄏㄠˋ", "ㄏㄨ˙"]), &l, true);
        assert_eq!(kinds(&t), ".T");
        assert!(t[1].unaccepted && t[1].alt == l.syl_id("ㄏㄨˇ"));
        assert_eq!(k(&["ㄏㄨ˙"]), "T");
    }

    /// Subsets: smallest first, left to right; a tail goes only with the position before it (when that one is in the span); at most 15 looked up.
    #[test]
    fn subset_order_ties_and_the_cap() {
        let p = |alt: u32, tail: bool| Pos { sandhi: !tail, tail, unaccepted: false, alt: Some(alt) };
        let ids = [1, 2, 3];
        let t3 = [p(11, false), p(12, false), Pos::default()];
        assert_eq!(readings(&ids, &t3), [vec![11, 2, 3], vec![1, 12, 3], vec![11, 12, 3]]);
        // ㄒㄧㄤˊ ㄒㄧㄤ˙: the tail only together with the position before it.
        assert_eq!(readings(&[1, 2], &[p(11, false), p(12, true)]), [vec![11, 2], vec![11, 12]]);
        // A tail whose position before it is outside the span goes alone.
        assert_eq!(readings(&[2], &[p(12, true)]), [vec![12]]);
        // k = 4: all 15; k = 5: only 15 of the 31 are looked up (the 5 singles, the 10 pairs).
        let four = [p(11, false), p(12, false), p(13, false), p(14, false)];
        assert_eq!(readings(&[1, 2, 3, 4], &four).len(), 15);
        let five = [p(11, false), p(12, false), p(13, false), p(14, false), p(15, false)];
        let r = readings(&[1, 2, 3, 4, 5], &five);
        assert_eq!(r.len(), TONE3_MAX_SUBSETS);
        assert_eq!(r[4], [1, 2, 3, 4, 15]);
        assert_eq!(r[14], [1, 2, 3, 14, 15], "the last pair");
        assert!(readings(&[1, 2], &[Pos::default(), Pos::default()]).is_empty());
    }
}
