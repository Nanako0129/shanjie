//! Third-tone sandhi (docs/spec/decoder.md §6.6): a user who types what is said (眨眼 as ㄓㄚˊ ㄧㄢˇ, 小姐 as ㄒㄧㄠˊ ㄐㄧㄝ˙) still gets the
//! word, whose lexicon reading has the third tone. Colloquial sandhi rides the same switch and the same table: a neutral syllable typed as said (厲害 as
//! ㄌㄧˋ ㄏㄞ˙) reads the word under its lexicon tone (the neutral source), and one the lexicon has no one-syllable entry for keeps a path (K).
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

/// Colloquial sandhi (docs/spec/decoder.md §6.6, §8.7): column 4 of the rows `tools/build_sandhi.py` and `tools/build_overlay.py` write for the colloquial
/// reading of a reduplicated word (熱騰騰 as ㄖㄜˋ ㄊㄥ ㄊㄥ). The lexicon keeps them apart (`Lexicon::colloq_range`) and they are read only while the switch is on.
pub const COLLOQ_TAG: &str = "colloq";
/// Taken once off the score of a word found through the neutral source or through a neutral syllable's one-syllable alternatives (log10); the `colloq` rows
/// are written this much under the row they come from (`COLLOQ_PENALTY` of tools/build_sandhi.py, checked equal by tools/test_build_sandhi.py).
/// 格點 {1.0,2.0,3.0,4.0}（docs/spec/decoder.md §6.6）：3.0 與 4.0 讓 cvtune-clean-tw0、wikitune-clean-tw0 原讀音都不退步，探針 top1 同為 94／99，同分取大的。
pub const DELTA_NEUTRAL: f64 = 4.0;
/// The most readings of a span the neutral source looks up.
pub const NEUTRAL_MAX_READINGS: usize = 15;

// Evaluation only (`shanjie-eval --neutral-delta D`): another `DELTA_NEUTRAL` for the whole process, set before the lexicon is loaded (the `colloq` rows
// move with it). The engine and the App never set it.
static NEUTRAL_DELTA: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(u64::MAX);

/// Evaluation only: replaces `DELTA_NEUTRAL` (`None`: the constant again). Call it before loading the lexicon.
pub fn set_neutral_delta(delta: Option<f64>) {
    NEUTRAL_DELTA.store(delta.map_or(u64::MAX, f64::to_bits), std::sync::atomic::Ordering::Relaxed);
}

/// `DELTA_NEUTRAL`, or what `set_neutral_delta` put in its place.
pub fn neutral_delta() -> f64 {
    match NEUTRAL_DELTA.load(std::sync::atomic::Ordering::Relaxed) {
        u64::MAX => DELTA_NEUTRAL,
        b => f64::from_bits(b),
    }
}

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
    /// Also a neutral syllable with `single` alternatives (K below): accepted by rule, so it has no adjacent-key reading either.
    pub unaccepted: bool,
    pub alt: Option<u32>,
    /// K (docs/spec/decoder.md §6.6): ˙X without a one-syllable entry, wherever it stands: the ids of X, Xˊ, Xˇ, Xˋ that have a one-syllable entry, in that
    /// order. They are looked up for one-syllable words only (`singles`), never in a multi-syllable reading.
    pub single: [Option<u32>; 4],
    /// The neutral source (docs/spec/decoder.md §6.6): ˙X after some syllable of the input, with or without an entry of its own: the ids of X, Xˊ, Xˇ, Xˋ
    /// that are lexicon syllables, in that order. They are looked up in multi-syllable readings only (`neutral_readings`).
    pub rule: [Option<u32>; 4],
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

/// X, Xˊ, Xˇ, Xˋ for ˙X; `None` for anything else.
pub fn four(s: &str) -> Option<[String; 4]> {
    let body = s.strip_suffix('˙').filter(|b| !b.is_empty() && !b.starts_with('_'))?;
    Some([body.to_string(), format!("{body}ˊ"), format!("{body}ˇ"), format!("{body}ˋ")])
}

/// K's test (docs/spec/keys-and-composition.md §3.2): ˙X without a one-syllable entry while one of X, Xˊ, Xˇ, Xˋ has one. `finish_syllable` accepts it after
/// a syllable; once in the composition it keeps its alternatives whatever comes to stand before it.
pub fn neutral_single(lex: &Lexicon, s: &str) -> bool {
    neutral(s) && !lex.has_single(s) && four(s).is_some_and(|f| f.iter().any(|t| lex.has_single(t)))
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
            out[j] = Pos { sandhi: false, tail: true, unaccepted: ut, alt: alt(s), ..Pos::default() };
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
            out[i] = Pos { sandhi: true, tail: false, unaccepted: u, alt: alt(s), ..Pos::default() };
        }
    }
    // K and the neutral source: after the table above, which they do not change (a neutral syllable K marks is no tail for the second tone before it).
    for j in 0..syls.len() {
        let Some(f) = four(&syls[j]) else { continue };
        if !lex.has_single(&syls[j]) {
            let single = f.clone().map(|t| lex.has_single(&t).then(|| lex.syl_id(&t)).flatten());
            if single.iter().any(Option::is_some) {
                out[j].single = single;
                out[j].unaccepted = true;
            }
        }
        if syls[..j].iter().any(|p| !p.starts_with('_')) {
            out[j].rule = f.map(|t| lex.syl_id(&t));
        }
    }
    out
}

/// K: the readings a one-syllable span is looked up under besides `readings`: its `single` alternatives in the order X, Xˊ, Xˇ, Xˋ, without the one
/// `readings` already gives (`alt`). Empty for a longer span: the alternatives are never part of a multi-syllable reading.
pub fn singles(t3: &[Pos]) -> Vec<Vec<u32>> {
    match t3 {
        [p] => p.single.iter().flatten().filter(|&&a| Some(a) != p.alt).map(|&a| vec![a]).collect(),
        _ => Vec::new(),
    }
}

/// The neutral source: the readings of a span of two or more syllables with every one of its `rule` positions replaced by one of
/// its alternatives (the sandhi positions stay as typed), in the lexicographic order of the positions left to right, each in the order X, Xˊ, Xˇ, Xˋ;
/// at most `NEUTRAL_MAX_READINGS`. Empty without a `rule` position.
pub fn neutral_readings(ids: &[u32], t3: &[Pos]) -> Vec<Vec<u32>> {
    let at: Vec<(usize, Vec<u32>)> = (0..ids.len().min(t3.len())).map(|j| (j, t3[j].rule.iter().flatten().copied().collect::<Vec<u32>>())).filter(|(_, a)| !a.is_empty()).collect();
    if ids.len() < 2 || at.is_empty() {
        return Vec::new();
    }
    let mut out = Vec::new();
    let mut c = vec![0usize; at.len()];
    loop {
        let mut r = ids.to_vec();
        for (x, (j, alts)) in at.iter().enumerate() {
            r[*j] = alts[c[x]];
        }
        out.push(r);
        if out.len() == NEUTRAL_MAX_READINGS {
            return out;
        }
        // Next combination: the rightmost position that has another alternative moves on, those right of it start over.
        let Some(i) = (0..at.len()).rev().find(|&i| c[i] + 1 < at[i].1.len()) else { return out };
        c[i] += 1;
        c[i + 1..].iter_mut().for_each(|x| *x = 0);
    }
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
        let p = |alt: u32, tail: bool| Pos { sandhi: !tail, tail, unaccepted: false, alt: Some(alt), ..Pos::default() };
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

    /// K (docs/spec/decoder.md §6.6): ˙X without a one-syllable entry has the alternatives X, Xˊ, Xˇ, Xˋ that have one, in that order,
    /// wherever it stands; they are read for a one-syllable span only, never in a subset of a longer one, and they make no tail for the second tone before.
    #[test]
    fn neutral_single_alternatives() {
        // ㄏㄞ: three of the four have an entry, ㄏㄞˇ does not, so the table of the sandhi rule alone would leave ˙ㄏㄞ unmarked.
        let l = lex(&["ㄌㄧˋ", "ㄌㄠˊ", "ㄌㄠˇ", "ㄏㄞ", "ㄏㄞˊ", "ㄏㄞˋ"], &[]);
        let id = |s: &str| l.syl_id(s);
        let t = positions(&v(&["ㄌㄧˋ", "ㄏㄞ˙"]), &l, true);
        assert_eq!(t[1], Pos { unaccepted: true, single: [id("ㄏㄞ"), id("ㄏㄞˊ"), None, id("ㄏㄞˋ")], rule: [id("ㄏㄞ"), id("ㄏㄞˊ"), None, id("ㄏㄞˋ")], ..Pos::default() });
        assert_eq!(singles(&t[1..]), [vec![id("ㄏㄞ").unwrap()], vec![id("ㄏㄞˊ").unwrap()], vec![id("ㄏㄞˋ").unwrap()]], "three alternatives, in order");
        assert!(singles(&t).is_empty(), "never for a span of two syllables");
        assert!(readings(&l.ids(&v(&["ㄌㄧˋ", "ㄏㄞ˙"])), &t).is_empty(), "and no subset of the sandhi source holds one");
        assert_eq!(positions(&v(&["ㄏㄞ˙"]), &l, true)[0], Pos { rule: [None; 4], ..t[1] }, "K's alternatives as the first syllable too; the neutral source needs a syllable before");
        assert_eq!(kinds(&positions(&v(&["ㄌㄠˊ", "ㄏㄞ˙"]), &l, true)), "..", "no tail: the second tone before it is no sandhi position");
        assert!(positions(&v(&["ㄌㄧˋ", "ㄏㄞ˙"]), &l, false).iter().all(|p| *p == Pos::default()), "off: empty");
        // With an entry for ㄏㄞˇ it is the unaccepted tail the sandhi rule knows: Xˇ stays that rule's (`alt`, in `readings`), K adds the other three.
        let l = lex(&["ㄌㄧˋ", "ㄏㄞ", "ㄏㄞˊ", "ㄏㄞˇ", "ㄏㄞˋ"], &[]);
        let id = |s: &str| l.syl_id(s).unwrap();
        let t = positions(&v(&["ㄌㄧˋ", "ㄏㄞ˙"]), &l, true);
        assert!(t[1].tail && t[1].unaccepted && t[1].alt == Some(id("ㄏㄞˇ")));
        assert_eq!(readings(&l.ids(&v(&["ㄏㄞ˙"])), &t[1..]), [vec![id("ㄏㄞˇ")]]);
        assert_eq!(singles(&t[1..]), [vec![id("ㄏㄞ")], vec![id("ㄏㄞˊ")], vec![id("ㄏㄞˋ")]]);
        // A neutral syllable with an entry of its own, or none of whose four tones has one, is no K position.
        // (The neutral source takes the first all the same: ˙ㄏㄞ after a syllable, ㄏㄞˋ a lexicon syllable.)
        let l = lex(&["ㄌㄧˋ", "ㄏㄞ˙", "ㄏㄞˋ"], &["ㄌㄧˋ-ㄉㄠ˙ 力道"]);
        let t = positions(&v(&["ㄌㄧˋ", "ㄏㄞ˙", "ㄉㄠ˙", "_punct_，", "ㄏㄞ˙"]), &l, true);
        assert!(t.iter().all(|p| p.single == [None; 4] && !p.unaccepted));
        assert_eq!(t[1].rule, [None, None, None, l.syl_id("ㄏㄞˋ")]);
        assert_eq!((t[2].rule, t[4].rule), ([None; 4], t[1].rule), "no lexicon syllable among the four: nothing; a token before it is no syllable, the ones before that are");
    }

    /// The neutral source (docs/spec/decoder.md §6.6): every neutral position replaced at once, positions left to right, each X, Xˊ, Xˇ, Xˋ; at
    /// most 15 readings; a sandhi position is never replaced with it, and the sandhi subsets never hold a neutral alternative.
    #[test]
    fn neutral_source_readings() {
        let n = |a: &[u32]| Pos { rule: [a.first().copied(), a.get(1).copied(), a.get(2).copied(), a.get(3).copied()], ..Pos::default() };
        // One position, three alternatives.
        assert_eq!(neutral_readings(&[1, 2], &[Pos::default(), n(&[21, 22, 23])]), [vec![1, 21], vec![1, 22], vec![1, 23]]);
        assert!(neutral_readings(&[2], &[n(&[21, 22])]).is_empty(), "multi-syllable words only");
        // Two positions: both replaced in every reading, the left one slowest.
        let two = [n(&[11, 12]), Pos::default(), n(&[31, 32])];
        assert_eq!(neutral_readings(&[1, 2, 3], &two), [vec![11, 2, 31], vec![11, 2, 32], vec![12, 2, 31], vec![12, 2, 32]]);
        let full = [n(&[11, 12, 13, 14]), n(&[21, 22, 23, 24])];
        let r = neutral_readings(&[1, 2], &full);
        assert_eq!((r.len(), r[14].clone()), (NEUTRAL_MAX_READINGS, vec![14, 23]), "15 of the 16");
        // A sandhi position and a neutral position: each source replaces its own kind only.
        let mixed = [Pos { sandhi: true, alt: Some(91), ..Pos::default() }, n(&[21, 22])];
        assert_eq!(readings(&[1, 2], &mixed), [vec![91, 2]]);
        assert_eq!(neutral_readings(&[1, 2], &mixed), [vec![1, 21], vec![1, 22]]);
        let five = [Pos { sandhi: true, alt: Some(91), ..Pos::default() }; 5];
        assert_eq!(readings(&[1, 2, 3, 4, 5], &five).len(), TONE3_MAX_SUBSETS, "the sandhi subsets keep their cap");
    }
}
