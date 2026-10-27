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

/// Acceptance 1, the reported case in its first form. The reported 板控 (pick 版) no longer happens: since the merge with
/// main (section 10) the free decode also gives 版控, and the SJ-LM-7 files read it that way with or without the left
/// text 要進. The mechanism is the same the other way round: 版控 shown, 板 picked, 控 stays. So this no longer tells the
/// freeze from its absence (checked: it passes with `freeze_outside` emptied); `freeze_row_133_six_steps` carries section 3
/// item 1 (it fails without `freeze_outside`). Every example below that was 板控 and 版 is 版控 and 板 for the same reason;
/// "gave …" remarks that name a commit are the strings measured in the old direction.
#[test]
fn freeze_reported_case_banhkong() {
    let mut e = engine();
    assert_eq!(shown(&mut e, "ㄅㄢˇ ㄎㄨㄥˋ", "要進"), "版控");
    let o = repick(&mut e, 1, "板");
    assert_eq!(o.preedit, "板控");
}

/// Typo row 133 (docs/contracts/repick-freeze.md section 0 and the 2026-10-17 merge note): 可以做第六步 shows as
/// 可以做第六部; opening the window at the end and choosing the two-character word 六步 used to re-decode the left part
/// ("可以做地六步" in chat, "可以作地六步" in formal, the maintainer's screen). The words on the left stay as shown, whether the
/// window's 六步 is the prediction row's copy (section 12) or the lexicon entry.
#[test]
fn freeze_row_133_six_steps() {
    const ROW: &str = "ㄎㄜˇ ㄧˇ ㄗㄨㄛˋ ㄉㄧˋ ㄌㄧㄡˋ ㄅㄨˋ";
    // Measured on the production lexicon and the SJ-LM-7 model files: formal shows 可以作第六部, chat 可以做第六部 (both
    // showed 可以做第六部 before them); the freeze keeps the left part as shown. The test fails with `freeze_outside` emptied.
    // Prediction on, the window lists the row's copy first (predict-down B.3.1, section 12); off, the lexicon entry.
    for (prof, pred, free) in [(Profile::Formal, true, "可以作第六部"), (Profile::Chat, true, "可以做第六部"), (Profile::Formal, false, "可以作第六部"), (Profile::Chat, false, "可以做第六部")] {
        for pick in ["六步", "步"] {
            let mut e = engine();
            e.set_profile(prof).unwrap();
            e.set_prediction(pred).unwrap();
            assert_eq!(shown(&mut e, ROW, ""), free);
            // The cursor is at the end: the window lists the words that end there.
            let o = e.key(k(KeyKind::Space)).unwrap();
            highlight(&mut e, o, pick);
            assert_eq!(e.key(k(KeyKind::Enter)).unwrap().preedit, free.replace('部', "步"), "pick {pick}, prediction {pred}");
        }
    }
}

/// The learning records, as (context, reading, word).
fn records(e: &Engine) -> Vec<(String, String, String)> {
    e.learner().records().iter().map(|r| (r.context.clone(), r.reading.join(" "), r.word.clone())).collect()
}

/// Acceptance 5 (with 1): the commit teaches the re-picked span only; the frozen word teaches nothing. New-word B
/// (newword-b.md §2.2, merged 2026-10-17) also learns the run 板控 under its own `#` key: a run with at least one
/// re-picked character, including the characters committed with it, by design; that record is not an S4 teach.
#[test]
fn freeze_teaches_only_the_picked_span() {
    let mut e = engine();
    shown(&mut e, "ㄅㄢˇ ㄎㄨㄥˋ", "要進");
    repick(&mut e, 1, "板");
    assert_eq!(commit(&mut e), "板控");
    let r: Vec<_> = records(&e).into_iter().filter(|(context, _, _)| context != "#").collect();
    assert!(r.iter().all(|(_, reading, word)| reading == "ㄅㄢˇ" && word == "板"), "{r:?}");
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

/// Acceptance 3: a pick in the middle leaves the words on both sides as they were. The free decode of 它是由於在野 changes
/// 它 to 他 once 是 is pinned to 事, and 於 to 于 once 由 is pinned, so these two picks fail without the freeze (checked with
/// `freeze_outside` emptied). The pick of 魷魚 changes neither side, as before the SJ-LM-7 files.
#[test]
fn freeze_words_outside_stay() {
    for (end, word, want) in [(2, "事", "它事由於在野"), (3, "由", "它是由於在野"), (4, "魷魚", "它是魷魚在野")] {
        let mut e = engine();
        assert_eq!(shown(&mut e, "ㄊㄚ ㄕˋ ㄧㄡˊ ㄩˊ ㄗㄞˋ ㄧㄝˇ", ""), "它是由於在野");
        assert_eq!(repick(&mut e, end, word).preedit, want, "pick {word}");
    }
}

/// Acceptance 4: overlay entries whose character count differs from their syllable count (the overlay has no such
/// filter). A one-character word over two syllables next to another word: picking the other word freezes it as is.
const MISMATCH: &str = "ㄅㄚ 吧 -3.0\nㄅㄛ 玻 -3.0\nㄉㄚ 大 -0.5\nㄉㄚ 達 -5.0\nㄅㄚ 巴 -9.0\nㄅㄛ 波 -9.0\n";
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
    assert_eq!(shown(&mut e, "ㄊㄚ ㄕˋ", ""), "它是");
    assert_eq!(repick(&mut e, 1, "她").preedit, "她是");
    assert_eq!(insert_at(&mut e, 2, "ㄐㄧㄝˋ"), "她世界");
}

/// 版控 re-picked to 板控; ㄐㄧㄢ does not form a word with 控, so 控 stays.
#[test]
fn insert_that_does_not_merge_keeps_the_frozen_word() {
    let mut e = engine();
    shown(&mut e, "ㄅㄢˇ ㄎㄨㄥˋ", "要進");
    assert_eq!(repick(&mut e, 1, "板").preedit, "板控");
    assert_eq!(insert_at(&mut e, 2, "ㄐㄧㄢ"), "板控間");
}

/// 它是由於在野, the last word re-picked to 再也: frozen 它, 是 and 由於 (3-4) on the left of the pick. (The test used 在也由於在野
/// until the SJ-LM-7 files, which read ㄗㄞˋ ㄧㄝˇ as the one word 在野, so the left no longer held two separate words.)
/// Inserting at 2 (between 是 and 由於): ㄈㄡˇ merges with 是 only (是否): 它 and 由於 stay (the free decode reads 他是否).
/// ㄌㄧˇ merges with 由 of the two-syllable 由於 (the free decode reads 理由於), not with all of it: 由於 stays whole and the
/// gap decodes as 裡. The premises of both are asserted on the free decode first.
#[test]
fn insert_with_a_frozen_word_on_each_side() {
    let base = "ㄊㄚ ㄕˋ ㄧㄡˊ ㄩˊ ㄗㄞˋ ㄧㄝˇ";
    let with = |syl: &str| format!("ㄊㄚ ㄕˋ {syl} ㄧㄡˊ ㄩˊ ㄗㄞˋ ㄧㄝˇ");
    assert_eq!(shown(&mut engine(), base, ""), "它是由於在野");
    assert_eq!(shown(&mut engine(), &with("ㄈㄡˇ"), ""), "他是否由於在野", "free decode: 是否 merges and 它 changes");
    assert_eq!(shown(&mut engine(), &with("ㄌㄧˇ"), ""), "他是理由於在野", "free decode: 理由 merges, 是 does not");
    let run = |syl: &str| {
        let mut e = engine();
        shown(&mut e, base, "");
        assert_eq!(repick(&mut e, 6, "再也").preedit, "它是由於再也");
        insert_at(&mut e, 2, syl)
    };
    assert_eq!(run("ㄈㄡˇ"), "它是否由於再也", "left merges, the other left word and the right word unchanged");
    assert_eq!(run("ㄌㄧˇ"), "它是裡由於再也", "partial merge of 由於 keeps it whole");
    assert_eq!(run("ㄋㄧㄢˊ"), "它是年由於再也", "no merge on either side");
}

const FORGET: Key = Key { kind: KeyKind::Backspace, ch: '\0', modifiers: MOD_COMMAND };

/// Sections 6.1 and 9, measured: 要進 + ㄅㄢˇ ㄎㄨㄥˋ re-picked to 板控 (the cursor stays after 板); ㄐㄧㄢ typed there
/// merges with 控 into 監控, which is frozen in its place; Backspace deletes 監 and keeps 控 (d36a7c0 gave 版空).
#[test]
fn backspace_after_a_merge_fixes_the_word_again() {
    let mut e = engine();
    shown(&mut e, "ㄅㄢˇ ㄎㄨㄥˋ", "要進");
    assert_eq!(repick(&mut e, 1, "板").preedit, "板控");
    assert_eq!(type_syls(&mut e, "ㄐㄧㄢ").preedit, "板監控");
    assert_eq!(e.key(k(KeyKind::Backspace)).unwrap().preedit, "板控");
}

/// Section 7.2, measured: ⌘⌫ on a word with no record forgets nothing, so the frozen words stay (d36a7c0 gave 版空).
#[test]
fn forget_without_records_keeps_the_frozen_words() {
    let mut e = engine();
    shown(&mut e, "ㄅㄢˇ ㄎㄨㄥˋ", "要進");
    assert_eq!(repick(&mut e, 1, "板").preedit, "板控");
    e.key(k(KeyKind::End)).unwrap();
    let o = e.key(k(KeyKind::Space)).unwrap();
    highlight(&mut e, o, "控");
    let before = e.learner().records().len();
    let o = e.key(FORGET).unwrap();
    assert_eq!(e.learner().records().len(), before);
    assert_eq!(o.preedit, "板控");
}

/// Prediction pick of 控制 after typing ㄎㄨㄥˋ and ㄓ with no left text: one record that does not touch 要進 + 板控.
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
/// fixed words: 控 stays.
#[test]
fn prediction_row_forget_keeps_the_frozen_words() {
    let mut e = engine();
    teach_kongzhi(&mut e);
    shown(&mut e, "ㄅㄢˇ ㄎㄨㄥˋ", "要進");
    assert_eq!(repick(&mut e, 1, "板").preedit, "板控");
    e.key(k(KeyKind::End)).unwrap();
    // The row shows from the first key of the next syllable (V3).
    e.key(Key::ch(L.key_of_symbol('ㄓ').unwrap(), 0)).unwrap();
    let o = e.key(k(KeyKind::Tab)).unwrap();
    assert_eq!(o.candidates[o.selected.unwrap()], "控制");
    let o = e.key(FORGET).unwrap();
    assert_eq!(e.learner().records().len(), 0);
    assert!(o.preedit.starts_with("板控"), "{}", o.preedit);
}

/// Section 7.3: inserting between the syllables of a frozen two-syllable word keeps both characters (they are not
/// release candidates for this insertion).
#[test]
fn insert_inside_a_frozen_word_keeps_both_halves() {
    let mut e = engine();
    assert_eq!(shown(&mut e, "ㄗㄞˋ ㄧㄝˇ ㄧㄡˊ ㄩˊ ㄗㄞˋ ㄧㄝˇ", ""), "在野由於在野");
    assert_eq!(repick(&mut e, 6, "再也").preedit, "在野由於再也");
    for syl in ["ㄋㄧㄢˊ", "ㄕㄡˇ", "ㄐㄧㄝˋ"] {
        let mut e = engine();
        shown(&mut e, "ㄗㄞˋ ㄧㄝˇ ㄧㄡˊ ㄩˊ ㄗㄞˋ ㄧㄝˇ", "");
        repick(&mut e, 6, "再也");
        let got = insert_at(&mut e, 3, syl);
        let ch: Vec<char> = got.chars().collect();
        assert_eq!((ch[2], ch[4]), ('由', '於'), "{got}");
    }
    // measured: 在野由年於再也, 在野由首於再也, 在野由介於再也
}

/// Section 9: after a merge (監控 frozen), a candidate pick over 監 freezes 控 again, and a prediction pick after the
/// word leaves 監控 alone; either way deleting 監 keeps 控. (Name kept from amendment three; there is no list now.)
#[test]
fn a_candidate_pick_clears_and_a_prediction_pick_keeps_left_records() {
    // candidate pick
    let mut e = engine();
    shown(&mut e, "ㄅㄢˇ ㄎㄨㄥˋ", "要進");
    repick(&mut e, 1, "板");
    let merged = type_syls(&mut e, "ㄐㄧㄢ").preedit;
    let o = e.key(k(KeyKind::Space)).unwrap(); // candidates for the syllable before the cursor (the trigger)
    let first = o.candidates[0].clone();
    let o = e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!((merged.as_str(), first.as_str(), o.preedit.as_str()), ("板監控", "間", "板間控"));
    // The pick froze 控 anew (section 1).
    let o = e.key(k(KeyKind::Backspace)).unwrap();
    assert!(o.handled);
    assert_eq!(o.preedit, "板控");
    // prediction pick
    let mut e = engine();
    shown(&mut e, "ㄅㄢˇ ㄎㄨㄥˋ", "要進");
    repick(&mut e, 1, "板");
    type_syls(&mut e, "ㄐㄧㄢ");
    e.key(k(KeyKind::End)).unwrap();
    let o = e.key(Key::ch(L.key_of_symbol('ㄓ').unwrap(), 0)).unwrap();
    println!("prediction row after the merge + ㄓ: {:?}", o.candidates);
    assert!(!o.candidates.is_empty(), "the example needs a prediction row");
    {
        e.key(k(KeyKind::Tab)).unwrap();
        let o = e.key(k(KeyKind::Enter)).unwrap();
        assert_eq!(o.preedit, "板監控中");
        e.key(k(KeyKind::Home)).unwrap();
        e.key(k(KeyKind::Right)).unwrap();
        e.key(k(KeyKind::Right)).unwrap();
        // The pick replaced only the span after the cursor; deleting 監 splits the frozen 監控 and keeps 控 (aa8456c
        // gave 版空中).
        let o = e.key(k(KeyKind::Backspace)).unwrap();
        assert!(o.handled);
        assert_eq!(o.preedit, "板控中");
    }
}

/// Section 11, measured: 世界 merges with ㄑㄩㄢˊ into 全世界 (free: it is decoded again, not frozen); ㄉㄜ˙ inserted inside
/// re-decodes the three as 詮釋的界 (the frozen-merge version kept 全世的界). Deleting 詮 of the displayed 詮釋 keeps 釋.
#[test]
fn an_insert_inside_a_merged_word_redecodes_it() {
    let mut e = engine();
    assert_eq!(shown(&mut e, "ㄊㄚ ㄕˋ ㄐㄧㄝˋ", ""), "他世界");
    assert_eq!(repick(&mut e, 1, "她").preedit, "她世界");
    assert_eq!(type_syls(&mut e, "ㄑㄩㄢˊ").preedit, "她全世界");
    assert_eq!(insert_at(&mut e, 3, "ㄉㄜ˙"), "她詮釋的解");
    e.key(k(KeyKind::Home)).unwrap();
    e.key(k(KeyKind::Right)).unwrap();
    e.key(k(KeyKind::Right)).unwrap();
    let o = e.key(k(KeyKind::Backspace)).unwrap();
    assert!(o.handled);
    assert_eq!(o.preedit, "她釋的解");
}


/// Section 11, measured: 她是 + ㄐㄧㄝˋ gives 她世界 (世界 is free, 她 frozen). Another ㄐㄧㄝˋ typed between 世 and 界 decodes
/// freely (界界; the frozen-merge version gave 她世介界); deleting the last syllable leaves 她世界.
#[test]
fn an_insert_inside_a_merged_word_decodes_freely() {
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

/// Section 9: the candidate window's ⌘⌫ frees frozen words only for an entry in effect over the span. 空 is taught for
/// ㄎㄨㄥˋ after 遙遠 (a one-character word has the exact-context level only and is learned only under a full
/// two-character context, s4-learning section 12 and rule 7: after 遙 alone nothing is learned), so it is not in effect for 要進 + 板監控: forgetting it (records 1 -> 0) changes nothing,
/// and deleting 監 still keeps 控 (fa0ae06 gave 版空).
#[test]
fn candidate_forget_of_an_entry_not_in_effect_keeps_the_frozen_words() {
    let mut e = engine();
    assert_eq!(shown(&mut e, "ㄎㄨㄥˋ", "遙遠"), "控");
    assert_eq!(repick(&mut e, 1, "空").preedit, "空");
    assert_eq!(commit(&mut e), "空");
    assert_eq!(e.learner().records().len(), 1);
    assert_eq!(shown(&mut e, "ㄅㄢˇ ㄎㄨㄥˋ", "要進"), "版控");
    assert_eq!(repick(&mut e, 1, "板").preedit, "板控");
    assert_eq!(type_syls(&mut e, "ㄐㄧㄢ").preedit, "板監控");
    e.key(k(KeyKind::End)).unwrap();
    let o = e.key(k(KeyKind::Space)).unwrap();
    highlight(&mut e, o, "空");
    let o = e.key(FORGET).unwrap();
    assert_eq!(e.learner().records().len(), 0);
    assert_eq!(o.preedit, "板監控");
    e.key(k(KeyKind::Esc)).unwrap();
    e.key(k(KeyKind::Home)).unwrap();
    e.key(k(KeyKind::Right)).unwrap();
    e.key(k(KeyKind::Right)).unwrap();
    let o = e.key(k(KeyKind::Backspace)).unwrap();
    assert!(o.handled);
    assert_eq!(o.preedit, "板控");
}

/// Section 9: 她 is taught for ㄊㄚ after 我們 (records: that one; a one-character word is learned only under a full
/// two-character context), so the next 我們 + ㄊㄚ ㄕˋ shows 她是; re-picking 是 freezes 她. ⌘⌫ on 她 forgets an entry in
/// effect there (exact level under 我們), so 她 decodes again without it: 他是.
#[test]
fn candidate_forget_of_an_entry_in_effect_frees_the_frozen_word() {
    let mut e = engine();
    assert_eq!(shown(&mut e, "ㄊㄚ ㄕˋ", "我們"), "他是");
    repick(&mut e, 1, "她");
    assert_eq!(commit(&mut e), "她是");
    assert_eq!(records(&e), vec![("我們".to_string(), "ㄊㄚ".to_string(), "她".to_string())]);
    assert_eq!(shown(&mut e, "ㄊㄚ ㄕˋ", "我們"), "她是");
    assert_eq!(repick(&mut e, 2, "是").preedit, "她是");
    e.key(k(KeyKind::Home)).unwrap();
    e.key(k(KeyKind::Right)).unwrap();
    let o = e.key(k(KeyKind::Space)).unwrap();
    highlight(&mut e, o, "她");
    let o = e.key(FORGET).unwrap();
    assert_eq!(e.learner().records().len(), 0);
    assert_eq!(o.preedit, "他是");
}

fn backspace(e: &mut Engine) -> String {
    let o = e.key(k(KeyKind::Backspace)).unwrap();
    assert!(o.handled);
    o.preedit
}

/// Review finding 1 (fa0ae06), as section 11 changes it: after the merge 監控 the words are not frozen again, so the extra
/// syllable typed between them stays free and deleting it decodes 監 and 控 afresh (no longer 板監控). Each Backspace
/// keeps the other syllables of the displayed word it deletes from (監控 shown: 控 stays when 監 goes). Measured: the
/// first Backspace gives 板監控 for 督 and 視 and re-decodes 板間空 for 有; the second gives 板控, 板控, and 板空 (the
/// decoder reads 板間空 minus 間 as 板空; the frozen merge of fa0ae06 gave 版控 here, in the old direction).
#[test]
fn typing_on_after_a_merge_then_deleting_back_follows_the_display() {
    let mut got = Vec::new();
    for syl in ["ㄉㄨ", "ㄕˋ", "ㄧㄡˇ"] {
        let mut e = engine();
        shown(&mut e, "ㄅㄢˇ ㄎㄨㄥˋ", "要進");
        repick(&mut e, 1, "板");
        assert_eq!(type_syls(&mut e, "ㄐㄧㄢ").preedit, "板監控");
        let typed = type_syls(&mut e, syl).preedit;
        got.push((typed, backspace(&mut e), backspace(&mut e)));
    }
    let want = [("板監督控", "板監控", "板控"), ("板監視控", "板監控", "板控"), ("板間有空", "板間空", "板空")];
    assert_eq!(got, want.map(|(a, b, c)| (a.to_string(), b.to_string(), c.to_string())));
}

/// Review finding 2: deleting a syllable inside a frozen multi-syllable word keeps its other characters (fa0ae06 gave
/// 版空 and 版間).
#[test]
fn deleting_inside_a_frozen_word_keeps_the_rest() {
    for (del, want) in [(2, "板控"), (3, "板監")] {
        let mut e = engine();
        assert_eq!(shown(&mut e, "ㄅㄢˇ ㄐㄧㄢ ㄎㄨㄥˋ", "要進"), "版監控");
        assert_eq!(repick(&mut e, 1, "板").preedit, "板監控");
        e.key(k(KeyKind::Home)).unwrap();
        for _ in 0..del {
            e.key(k(KeyKind::Right)).unwrap();
        }
        assert_eq!(backspace(&mut e), want);
    }
}

/// Apple Zhuyin, measured with imeshot (section 9): 他是 re-picked to 她是, ㄐㄧㄝˋ at the end gives 她世界, Backspace gives
/// 她世 (not 她是); 他世界 with the first character re-picked, Backspace at the end keeps 世.
#[test]
fn backspace_in_a_merged_or_frozen_word_like_apple() {
    let mut e = engine();
    shown(&mut e, "ㄊㄚ ㄕˋ", "");
    repick(&mut e, 1, "她");
    e.key(k(KeyKind::End)).unwrap();
    assert_eq!(type_syls(&mut e, "ㄐㄧㄝˋ").preedit, "她世界");
    assert_eq!(backspace(&mut e), "她世");
    let mut e = engine();
    assert_eq!(shown(&mut e, "ㄊㄚ ㄕˋ ㄐㄧㄝˋ", ""), "他世界");
    assert_eq!(repick(&mut e, 1, "她").preedit, "她世界");
    e.key(k(KeyKind::End)).unwrap();
    assert_eq!(backspace(&mut e), "她世");
}



/// Section 11, Apple Zhuyin case 1 (measured with imeshot 2026-10-17; formal now shows 它是 where Apple and chat show 他是): 他是, first character re-picked to 她, cursor to the
/// end, ㄐㄧˋ then ㄓㄜˇ gives 她是繼 and then 她是記者. With the merge re-frozen on insertion (revision four) it was 她世紀者.
#[test]
fn apple_case_1_typing_on_after_a_repick_rewrites_the_neighbour() {
    for (name, prof, free) in [("formal", Profile::Formal, "它是"), ("chat", Profile::Chat, "他是")] {
        let mut e = engine();
        e.set_profile(prof).unwrap();
        assert_eq!(shown(&mut e, "ㄊㄚ ㄕˋ", ""), free, "{name}");
        assert_eq!(repick(&mut e, 1, "她").preedit, "她是");
        e.key(k(KeyKind::End)).unwrap();
        assert_eq!(type_syls(&mut e, "ㄐㄧˋ").preedit, "她世紀");
        assert_eq!(type_syls(&mut e, "ㄓㄜˇ").preedit, "她是記者", "{name}");
    }
}

/// Section 11, Apple case 4: 找到, first character re-picked to 找, then ㄉㄚˊ ㄢˋ gives 找到答案 (revision four: 找到達案).
#[test]
fn apple_case_4_typing_on_after_a_repick_gives_the_new_word() {
    for (name, prof) in [("formal", Profile::Formal), ("chat", Profile::Chat)] {
        let mut e = engine();
        e.set_profile(prof).unwrap();
        assert_eq!(shown(&mut e, "ㄓㄠˇ ㄉㄠˋ", ""), "找到");
        repick(&mut e, 1, "找");
        e.key(k(KeyKind::End)).unwrap();
        assert_eq!(type_syls(&mut e, "ㄉㄚˊ").preedit, "找到達");
        assert_eq!(type_syls(&mut e, "ㄢˋ").preedit, "找到答案", "{name}");
    }
}

/// Section 11, Apple case 2: 他世界 without any re-pick, Backspace at the end gives 他世 (main re-decodes the rest): the
/// displayed word 世界 keeps its first character when its last syllable goes.
#[test]
fn apple_case_2_backspace_keeps_the_rest_of_a_decoded_word() {
    for (name, prof) in [("formal", Profile::Formal), ("chat", Profile::Chat)] {
        let mut e = engine();
        e.set_profile(prof).unwrap();
        assert_eq!(shown(&mut e, "ㄊㄚ ㄕˋ ㄐㄧㄝˋ", ""), "他世界");
        assert_eq!(backspace(&mut e), "他世", "{name}");
    }
}

/// Section 15, Apple Zhuyin (measured with imeshot 2026-10-17): 他世界, Backspace shows 他世, and the next syllable ㄏㄣˇ gives
/// 他是很 (the pieces the deletion split off are free again). The same engine must show what a fresh one shows for
/// ㄊㄚ ㄕˋ ㄏㄣˇ typed without any deletion, and not 他世很 (the pieces kept fixed, section 14's P2).
#[test]
fn section_15_a_new_syllable_frees_the_pieces_a_backspace_split_off() {
    for (name, prof) in [("formal", Profile::Formal), ("chat", Profile::Chat)] {
        let mut fresh = engine();
        fresh.set_profile(prof).unwrap();
        let want = shown(&mut fresh, "ㄊㄚ ㄕˋ ㄏㄣˇ", "");
        let mut e = engine();
        e.set_profile(prof).unwrap();
        assert_eq!(shown(&mut e, "ㄊㄚ ㄕˋ ㄐㄧㄝˋ", ""), "他世界");
        assert_eq!(backspace(&mut e), "他世", "{name}");
        let got = type_syls(&mut e, "ㄏㄣˇ").preedit;
        assert_eq!(got, want, "{name}");
        assert_ne!(got, "他世很", "{name}");
    }
}

/// Section 15: only a typed syllable frees the pieces. Arrow keys leave them (the screen still shows 他世 after Left and
/// Right), and the next syllable then frees them as without the arrows.
#[test]
fn section_15_arrow_keys_do_not_free_the_pieces_but_the_next_syllable_does() {
    for (name, prof) in [("formal", Profile::Formal), ("chat", Profile::Chat)] {
        let mut fresh = engine();
        fresh.set_profile(prof).unwrap();
        let want = shown(&mut fresh, "ㄊㄚ ㄕˋ ㄏㄣˇ", "");
        let mut e = engine();
        e.set_profile(prof).unwrap();
        assert_eq!(shown(&mut e, "ㄊㄚ ㄕˋ ㄐㄧㄝˋ", ""), "他世界");
        assert_eq!(backspace(&mut e), "他世", "{name}");
        e.key(k(KeyKind::Left)).unwrap();
        e.key(k(KeyKind::Home)).unwrap();
        assert_eq!(e.key(k(KeyKind::End)).unwrap().preedit, "他世", "{name}");
        assert_eq!(type_syls(&mut e, "ㄏㄣˇ").preedit, want, "{name}");
    }
}

/// Section 15 does not touch a re-pick's freeze (section 1): 經歷 re-picked to 精 shows 精歷, and a syllable typed on the
/// right leaves 歷 as it was shown (the two pieces of that freeze are not Backspace pieces).
#[test]
fn section_15_a_repick_freeze_is_not_released_by_a_new_syllable() {
    for (name, prof) in [("formal", Profile::Formal), ("chat", Profile::Chat)] {
        let mut e = engine();
        e.set_profile(prof).unwrap();
        assert_eq!(shown(&mut e, "ㄐㄧㄥ ㄌㄧˋ", ""), "經歷");
        assert_eq!(repick(&mut e, 1, "精").preedit, "精歷");
        e.key(k(KeyKind::End)).unwrap();
        let got = type_syls(&mut e, "ㄏㄣˇ").preedit;
        assert_eq!(got.chars().take(2).collect::<String>(), "精歷", "{name}: {got}");
    }
}

/// Section 11: a prediction start may lie inside a frozen word (only a word the user chose keeps its inside). 國家 is
/// learned for ㄍㄨㄛˊ ㄐㄧㄚ after 是美; in 他是美國, re-picked at 是 (美國 frozen), the first key of the next syllable lists
/// 國家 first, from the start inside 美國 (picking it would take the frozen word apart). Measured: row [國家, 美國籍, 美國軍, ...].
#[test]
fn prediction_row_starts_inside_a_frozen_word() {
    let mut e = engine();
    shown(&mut e, "ㄍㄨㄛˊ", "是美");
    e.key(Key::ch(L.key_of_symbol('ㄐ').unwrap(), 0)).unwrap();
    let o = e.key(k(KeyKind::Tab)).unwrap();
    assert_eq!(o.candidates[0], "國家");
    e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!(commit(&mut e), "國家");
    assert_eq!(records(&e), vec![("是美".to_string(), "ㄍㄨㄛˊ ㄐㄧㄚ".to_string(), "國家".to_string())]);
    assert_eq!(shown(&mut e, "ㄊㄚ ㄕˋ ㄇㄟˇ ㄍㄨㄛˊ", ""), "他是美國");
    repick(&mut e, 2, "是");
    e.key(k(KeyKind::End)).unwrap();
    let o = e.key(Key::ch(L.key_of_symbol('ㄐ').unwrap(), 0)).unwrap();
    assert_eq!(o.candidates[..3], ["國家", "美國籍", "美國軍"]);
}
