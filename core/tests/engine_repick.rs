//! repick-freeze (docs/contracts/repick-freeze.md): after a candidate re-pick the words outside the picked span stay.
//! Needs data/lm/bigram.sjlm (fails loudly without it).
use core::engine::*;
use core::lm::{CappedLexicon, Lm, Profile};
use core::Lexicon;
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}
struct Shared {
    lex: Arc<Lexicon>,
    lm: Arc<Lm>,
    capped: Arc<CappedLexicon>,
}
fn shared() -> &'static Shared {
    static S: OnceLock<Shared> = OnceLock::new();
    S.get_or_init(|| {
        let lex = load_lexicon(&root().join("data/lexicon")).unwrap();
        let lm = Lm::load(&root().join("data/lm/bigram.sjlm")).expect("data/lm/bigram.sjlm is missing");
        let overlay = std::fs::read_to_string(root().join("data/lexicon/overlay-add.tsv")).unwrap();
        let capped = Arc::new(CappedLexicon::new(lex.clone(), &overlay, &lm, None).unwrap());
        Shared { lex, lm: Arc::new(lm), capped }
    })
}
const L: Layout = Layout::Standard;
const DAY: i64 = 20_000;

/// Production lexicon and model, formal profile, learning on, fixed clock.
fn engine() -> Engine {
    let s = shared();
    let mut e = Engine::with_lexicon(s.lex.clone(), L);
    e.set_lm(s.lm.clone(), s.capped.clone());
    e.set_profile(Profile::Formal).unwrap();
    e.set_today(Some(DAY));
    e.set_learning(true);
    e
}
fn k(kind: KeyKind) -> Key {
    Key::new(kind)
}
fn type_syls(e: &mut Engine, reading: &str) -> Output {
    let mut o = None;
    for syl in reading.split(' ') {
        let mut toned = false;
        for c in syl.chars() {
            match L.key_of_tone(c) {
                Some(tk) => {
                    o = Some(e.key(Key::ch(tk, 0)).unwrap());
                    toned = true;
                }
                None => o = Some(e.key(Key::ch(L.key_of_symbol(c).unwrap(), 0)).unwrap()),
            }
        }
        if !toned {
            o = Some(e.key(k(KeyKind::Space)).unwrap());
        }
    }
    o.unwrap()
}
/// Move the highlight onto `word`.
fn highlight(e: &mut Engine, mut o: Output, word: &str) -> Output {
    for _ in 0..5000 {
        if o.candidates[o.selected.unwrap()] == word {
            return o;
        }
        o = e.key(k(KeyKind::Right)).unwrap();
    }
    panic!("candidate {word} not found");
}
/// Cursor after `end` syllables (counted from the start), open candidates, choose `word`; returns the preedit.
fn repick(e: &mut Engine, end: usize, word: &str) -> Output {
    e.key(k(KeyKind::Home)).unwrap();
    for _ in 0..end {
        e.key(k(KeyKind::Right)).unwrap();
    }
    let o = e.key(k(KeyKind::Space)).unwrap();
    highlight(e, o, word);
    e.key(k(KeyKind::Enter)).unwrap()
}
fn shown(e: &mut Engine, reading: &str, left: &str) -> String {
    e.set_left_context(left);
    type_syls(e, reading).preedit
}
fn commit(e: &mut Engine) -> String {
    e.key(k(KeyKind::Enter)).unwrap().commit
}

/// Acceptance 1: the reported case. Before the change the second word re-decoded to 空 (版空).
#[test]
fn freeze_reported_case_banhkong() {
    let mut e = engine();
    assert_eq!(shown(&mut e, "ㄅㄢˇ ㄎㄨㄥˋ", "要進"), "板控");
    let o = repick(&mut e, 1, "版");
    assert_eq!(o.preedit, "版控");
}

/// The learning records, as (context, reading, word).
fn records(e: &Engine) -> Vec<(String, String, String)> {
    e.learner().records().iter().map(|r| (r.context.clone(), r.reading.join(" "), r.word.clone())).collect()
}

/// Acceptance 5 (with 1): the commit teaches the re-picked span only; the frozen word teaches nothing.
#[test]
fn freeze_teaches_only_the_picked_span() {
    let mut e = engine();
    shown(&mut e, "ㄅㄢˇ ㄎㄨㄥˋ", "要進");
    repick(&mut e, 1, "版");
    assert_eq!(commit(&mut e), "版控");
    let r = records(&e);
    assert!(r.iter().all(|(_, reading, word)| reading == "ㄅㄢˇ" && word == "版"), "{r:?}");
    assert!(!r.is_empty());
}

/// Acceptance 2: 在野 is one word; picking its first character 再 used to re-decode the second (再也). Measured on
/// the production model, formal and chat alike: default 在野, before the change 再也.
#[test]
fn freeze_partial_overlap_keeps_the_second_character() {
    for prof in [Profile::Formal, Profile::Chat] {
        let mut e = engine();
        e.set_profile(prof).unwrap();
        assert_eq!(shown(&mut e, "ㄗㄞˋ ㄧㄝˇ", ""), "在野");
        assert_eq!(repick(&mut e, 1, "再").preedit, "再野");
    }
}

/// Acceptance 3: a pick in the middle leaves the words on both sides as they were.
#[test]
fn freeze_words_outside_stay() {
    let mut e = engine();
    assert_eq!(shown(&mut e, "ㄗㄞˋ ㄧㄝˇ ㄧㄡˊ ㄩˊ ㄗㄞˋ ㄧㄝˇ", ""), "在也由於在野");
    assert_eq!(repick(&mut e, 4, "魷魚").preedit, "在也魷魚在野");
}

/// Acceptance 4: overlay entries whose character count differs from their syllable count (the overlay has no such
/// filter). A one-character word over two syllables next to another word: picking the other word freezes it as is.
const MISMATCH: &str = "ㄅㄚ 吧 -3.0\nㄅㄛ 玻 -3.0\nㄉㄚ 大 -1.0\nㄉㄚ 達 -2.0\nㄅㄚ 巴 -9.0\nㄅㄛ 波 -9.0\n";
fn mismatch_engine(overlay: &str) -> Engine {
    let s = shared();
    let lex = Arc::new(Lexicon::parse_with(MISMATCH, Some(overlay)).unwrap());
    let capped = Arc::new(CappedLexicon::new(lex.clone(), "", &s.lm, None).unwrap());
    let mut e = Engine::with_lexicon(lex, L);
    e.set_lm(s.lm.clone(), capped);
    e.set_today(Some(DAY));
    e.set_learning(true);
    e
}

#[test]
fn freeze_keeps_a_word_with_fewer_characters_than_syllables() {
    let mut e = mismatch_engine("ㄅㄚ-ㄅㄛ\t巴\t-0.1\twikt\n");
    let before = shown(&mut e, "ㄅㄚ ㄅㄛ ㄉㄚ", "");
    assert_eq!(before, "巴大", "the one-character word covers two syllables");
    let o = repick(&mut e, 3, "達");
    assert!(o.handled && !o.preedit.is_empty());
    assert_eq!(o.preedit, "巴達");
}

/// Partial overlap whose outside syllable has no single-character entry for the shown character: it is not frozen and
/// decodes again (to 玻, the only entry); the engine does not fail and the composition stays.
#[test]
fn freeze_leaves_a_syllable_without_a_single_entry_to_the_decoder() {
    let s = shared();
    let lex = Arc::new(Lexicon::parse_with("ㄅㄚ 吧 -3.0\nㄅㄛ 玻 -3.0\n", Some("ㄅㄚ-ㄅㄛ\t巴波\t-0.1\twikt\n")).unwrap());
    let capped = Arc::new(CappedLexicon::new(lex.clone(), "", &s.lm, None).unwrap());
    let mut e = Engine::with_lexicon(lex, L);
    e.set_lm(s.lm.clone(), capped);
    assert_eq!(shown(&mut e, "ㄅㄚ ㄅㄛ", ""), "巴波");
    let o = repick(&mut e, 1, "吧");
    assert!(o.handled);
    assert_eq!(o.preedit, "吧玻");
}

/// Partial overlap of a word whose character count differs from its syllable count: nothing outside is frozen.
#[test]
fn freeze_partial_overlap_of_a_mismatched_word_is_left_to_the_decoder() {
    let mut e = mismatch_engine("ㄅㄚ-ㄅㄛ\t巴\t-0.1\twikt\n");
    assert_eq!(shown(&mut e, "ㄅㄚ ㄅㄛ", ""), "巴");
    let o = repick(&mut e, 1, "吧");
    assert!(o.handled);
    assert_eq!(o.preedit, "吧玻");
}

/// Type `syl` with the cursor after `at` syllables.
fn insert_at(e: &mut Engine, at: usize, syl: &str) -> String {
    e.key(k(KeyKind::Home)).unwrap();
    for _ in 0..at {
        e.key(k(KeyKind::Right)).unwrap();
    }
    type_syls(e, syl).preedit
}

/// Section 6.1, measured: 他是 shows after the re-pick 她是; the new syllable merges with 是 into 世界 (Apple Zhuyin:
/// 她世界). The previous commit gave 她是界.
#[test]
fn insert_after_repick_merges_with_the_frozen_neighbour() {
    let mut e = engine();
    assert_eq!(shown(&mut e, "ㄊㄚ ㄕˋ", ""), "他是");
    assert_eq!(repick(&mut e, 1, "她").preedit, "她是");
    assert_eq!(insert_at(&mut e, 2, "ㄐㄧㄝˋ"), "她世界");
}

/// 板控 re-picked to 版控; ㄐㄧㄢ does not form a word with 控, so 控 stays.
#[test]
fn insert_that_does_not_merge_keeps_the_frozen_word() {
    let mut e = engine();
    shown(&mut e, "ㄅㄢˇ ㄎㄨㄥˋ", "要進");
    assert_eq!(repick(&mut e, 1, "版").preedit, "版控");
    assert_eq!(insert_at(&mut e, 2, "ㄐㄧㄢ"), "版控間");
}

/// 在也由於在野, the last word re-picked to 再也: frozen 在, 也 and 由於 (3-5) on the left of the pick.
/// Inserting at 2 (between 也 and 由於): ㄕㄡˇ merges with 也 only (野手): 由於 stays. ㄐㄧㄝˋ merges with 由 of the
/// two-syllable 由於 (the free decode reads 藉由), not with all of it: 由於 stays whole and the gap decodes as 借.
#[test]
fn insert_with_a_frozen_word_on_each_side() {
    let base = "ㄗㄞˋ ㄧㄝˇ ㄧㄡˊ ㄩˊ ㄗㄞˋ ㄧㄝˇ";
    let run = |syl: &str| {
        let mut e = engine();
        assert_eq!(shown(&mut e, base, ""), "在也由於在野");
        assert_eq!(repick(&mut e, 6, "再也").preedit, "在也由於再也");
        insert_at(&mut e, 2, syl)
    };
    assert_eq!(run("ㄕㄡˇ"), "在野手由於再也", "left merges, right word unchanged");
    assert_eq!(run("ㄐㄧㄝˋ"), "在也借由於再也", "partial merge of 由於 keeps it whole");
    assert_eq!(run("ㄋㄧㄢˊ"), "在也年由於再也", "no merge on either side");
}
