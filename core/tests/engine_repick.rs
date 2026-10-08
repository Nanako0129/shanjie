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

const FORGET: Key = Key { kind: KeyKind::Backspace, ch: '\0', modifiers: MOD_COMMAND };

/// Section 7.1, measured: 要進 + ㄅㄢˇ ㄎㄨㄥˋ re-picked to 版控 (the cursor stays after 版); ㄐㄧㄢ typed there merges
/// with 控 into 監控, so 控 is released; Backspace deletes that syllable and fixes 控 again (d36a7c0 gave 版空).
#[test]
fn backspace_after_a_merge_fixes_the_word_again() {
    let mut e = engine();
    shown(&mut e, "ㄅㄢˇ ㄎㄨㄥˋ", "要進");
    assert_eq!(repick(&mut e, 1, "版").preedit, "版控");
    assert_eq!(type_syls(&mut e, "ㄐㄧㄢ").preedit, "版監控");
    assert_eq!(e.key(k(KeyKind::Backspace)).unwrap().preedit, "版控");
}

/// Section 7.2, measured: ⌘⌫ on a word with no record forgets nothing, so the frozen words stay (d36a7c0 gave 版空).
#[test]
fn forget_without_records_keeps_the_frozen_words() {
    let mut e = engine();
    shown(&mut e, "ㄅㄢˇ ㄎㄨㄥˋ", "要進");
    assert_eq!(repick(&mut e, 1, "版").preedit, "版控");
    e.key(k(KeyKind::End)).unwrap();
    let o = e.key(k(KeyKind::Space)).unwrap();
    highlight(&mut e, o, "控");
    let before = e.learner().records().len();
    let o = e.key(FORGET).unwrap();
    assert_eq!(e.learner().records().len(), before);
    assert_eq!(o.preedit, "版控");
}

/// Prediction pick of 控制 after typing ㄎㄨㄥˋ and ㄓ with no left text: one record that does not touch 要進 + 版控.
fn teach_kongzhi(e: &mut Engine) {
    shown(e, "ㄎㄨㄥˋ", "");
    e.key(Key::ch(L.key_of_symbol('ㄓ').unwrap(), 0)).unwrap();
    let o = e.key(k(KeyKind::Tab)).unwrap();
    assert_eq!(o.candidates[o.selected.unwrap()], "控制");
    e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!(commit(e), "控制");
    assert_eq!(e.learner().records().len(), 1);
}

/// Sections 7.2 and 8: ⌘⌫ on the prediction row forgets an item that has a record (records 1 -> 0) and never touches
/// fixed words: 控 stays frozen (freely it decodes to 空 after the picked 版).
#[test]
fn prediction_row_forget_keeps_the_frozen_words() {
    let mut e = engine();
    teach_kongzhi(&mut e);
    shown(&mut e, "ㄅㄢˇ ㄎㄨㄥˋ", "要進");
    assert_eq!(repick(&mut e, 1, "版").preedit, "版控");
    e.key(k(KeyKind::End)).unwrap();
    // The row shows from the first key of the next syllable (V3).
    e.key(Key::ch(L.key_of_symbol('ㄓ').unwrap(), 0)).unwrap();
    let o = e.key(k(KeyKind::Tab)).unwrap();
    assert_eq!(o.candidates[o.selected.unwrap()], "控制");
    let o = e.key(FORGET).unwrap();
    assert_eq!(e.learner().records().len(), 0);
    assert!(o.preedit.starts_with("版控"), "{}", o.preedit);
}

/// Section 7.3: inserting between the syllables of a frozen two-syllable word keeps both characters (they are not
/// release candidates for this insertion).
#[test]
fn insert_inside_a_frozen_word_keeps_both_halves() {
    let mut e = engine();
    assert_eq!(shown(&mut e, "ㄗㄞˋ ㄧㄝˇ ㄧㄡˊ ㄩˊ ㄗㄞˋ ㄧㄝˇ", ""), "在也由於在野");
    assert_eq!(repick(&mut e, 6, "再也").preedit, "再也由於再也".replacen("再也", "在也", 1));
    for syl in ["ㄋㄧㄢˊ", "ㄕㄡˇ", "ㄐㄧㄝˋ"] {
        let mut e = engine();
        shown(&mut e, "ㄗㄞˋ ㄧㄝˇ ㄧㄡˊ ㄩˊ ㄗㄞˋ ㄧㄝˇ", "");
        repick(&mut e, 6, "再也");
        let got = insert_at(&mut e, 3, syl);
        let ch: Vec<char> = got.chars().collect();
        assert_eq!((ch[2], ch[4]), ('由', '於'), "{got}");
    }
    // measured: 在也由年於再也, 在也由首於再也, 在也由介於再也
}

/// Section 7.1 lifetime (a), with section 8: after a release, a candidate pick clears the list, so deleting the old
/// trigger brings nothing back; a prediction pick keeps the records wholly left of the span it replaces.
#[test]
fn a_candidate_pick_clears_and_a_prediction_pick_keeps_left_records() {
    // candidate pick
    let mut e = engine();
    shown(&mut e, "ㄅㄢˇ ㄎㄨㄥˋ", "要進");
    repick(&mut e, 1, "版");
    let merged = type_syls(&mut e, "ㄐㄧㄢ").preedit;
    let o = e.key(k(KeyKind::Space)).unwrap(); // candidates for the syllable before the cursor (the trigger)
    let first = o.candidates[0].clone();
    let o = e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!((merged.as_str(), first.as_str(), o.preedit.as_str()), ("版監控", "間", "版間控"));
    // The pick froze 控 anew (section 1); the released list was cleared, so this is not a restore.
    let o = e.key(k(KeyKind::Backspace)).unwrap();
    assert!(o.handled);
    assert_eq!(o.preedit, "版控");
    // prediction pick
    let mut e = engine();
    shown(&mut e, "ㄅㄢˇ ㄎㄨㄥˋ", "要進");
    repick(&mut e, 1, "版");
    type_syls(&mut e, "ㄐㄧㄢ");
    e.key(k(KeyKind::End)).unwrap();
    let o = e.key(Key::ch(L.key_of_symbol('ㄓ').unwrap(), 0)).unwrap();
    println!("prediction row after the merge + ㄓ: {:?}", o.candidates);
    assert!(!o.candidates.is_empty(), "the example needs a prediction row");
    {
        e.key(k(KeyKind::Tab)).unwrap();
        let o = e.key(k(KeyKind::Enter)).unwrap();
        assert_eq!(o.preedit, "版監控中");
        e.key(k(KeyKind::Home)).unwrap();
        e.key(k(KeyKind::Right)).unwrap();
        e.key(k(KeyKind::Right)).unwrap();
        // The pick replaced the span after the cursor only (section 8): 控's record (link 1..3) holds, so deleting the
        // trigger fixes 控 again (aa8456c cleared the list and gave 版空中).
        let o = e.key(k(KeyKind::Backspace)).unwrap();
        assert!(o.handled);
        assert_eq!(o.preedit, "版控中");
    }
}

/// Section 7.1 lifetime (b): a released two-syllable word, then a syllable inserted inside it, then the trigger
/// deleted: the record was dropped, nothing comes back over the changed span.
#[test]
fn an_insert_inside_a_released_word_drops_its_record() {
    let mut e = engine();
    assert_eq!(shown(&mut e, "ㄊㄚ ㄕˋ ㄐㄧㄝˋ", ""), "他世界");
    assert_eq!(repick(&mut e, 1, "她").preedit, "她世界");
    assert_eq!(type_syls(&mut e, "ㄑㄩㄢˊ").preedit, "她全世界", "at 1: merges with the frozen 世界, released");
    assert_eq!(insert_at(&mut e, 3, "ㄉㄜ˙"), "她詮釋的界", "inside the released 世界 (now 2..4): its record goes");
    e.key(k(KeyKind::Home)).unwrap();
    e.key(k(KeyKind::Right)).unwrap();
    e.key(k(KeyKind::Right)).unwrap();
    let o = e.key(k(KeyKind::Backspace)).unwrap(); // the old trigger ㄑㄩㄢˊ
    assert!(o.handled);
    assert_eq!(o.preedit, "她是的界", "nothing restored over the changed span");
}


/// Section 8, measured: 她是 + ㄐㄧㄝˋ gives 她世界 (是 released, trigger 2, link 1..3). Another ㄐㄧㄝˋ typed between
/// 世 and 界 is inside the link: the record goes, so deleting the original trigger (now 3) does not fix 是 again.
#[test]
fn an_insert_between_a_released_word_and_its_trigger_drops_its_record() {
    let mut e = engine();
    shown(&mut e, "ㄊㄚ ㄕˋ", "");
    assert_eq!(repick(&mut e, 1, "她").preedit, "她是");
    assert_eq!(insert_at(&mut e, 2, "ㄐㄧㄝˋ"), "她世界");
    assert_eq!(insert_at(&mut e, 2, "ㄐㄧㄝˋ"), "她世界界");
    e.key(k(KeyKind::End)).unwrap();
    let o = e.key(k(KeyKind::Backspace)).unwrap();
    assert!(o.handled);
    assert_eq!(o.preedit, "她世界");
}

/// Section 8: the candidate window's ⌘⌫ that removes records also drops the release records over its span. 空 is
/// taught for ㄎㄨㄥˋ after 遙 (a one-character word has the exact-context level only, so 要進 + 板控 is unchanged; with
/// no left text it is not learned at all, s4-learning section 12); 控 is released
/// by 監; forgetting 空 on 控's span (records 1 -> 0) drops that record, so deleting 監 leaves 控 to the decoder.
#[test]
fn candidate_forget_drops_the_release_records_over_its_span() {
    let mut e = engine();
    assert_eq!(shown(&mut e, "ㄎㄨㄥˋ", "遙"), "控");
    assert_eq!(repick(&mut e, 1, "空").preedit, "空");
    assert_eq!(commit(&mut e), "空");
    assert_eq!(e.learner().records().len(), 1);
    assert_eq!(shown(&mut e, "ㄅㄢˇ ㄎㄨㄥˋ", "要進"), "板控");
    assert_eq!(repick(&mut e, 1, "版").preedit, "版控");
    assert_eq!(type_syls(&mut e, "ㄐㄧㄢ").preedit, "版監控");
    e.key(k(KeyKind::End)).unwrap();
    let o = e.key(k(KeyKind::Space)).unwrap();
    highlight(&mut e, o, "空");
    e.key(FORGET).unwrap();
    assert_eq!(e.learner().records().len(), 0);
    e.key(k(KeyKind::Esc)).unwrap();
    e.key(k(KeyKind::Home)).unwrap();
    e.key(k(KeyKind::Right)).unwrap();
    e.key(k(KeyKind::Right)).unwrap();
    let o = e.key(k(KeyKind::Backspace)).unwrap();
    assert!(o.handled);
    assert_eq!(o.preedit, "版空", "measured; without the drop 控 is fixed again (版控)");
}

