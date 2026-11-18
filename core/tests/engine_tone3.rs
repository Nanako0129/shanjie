//! Third-tone sandhi in the engine (docs/spec/decoder.md §6.6, docs/spec/keys-and-composition.md §3.2): the engine as the App builds it (the data folder,
//! the default packs, the model; English mixing on with the App's switches, adjacent keys off). Needs data/lm like engine_lm.rs and fails loudly without it.
//! The position table and the subsets are tested in core/src/tone3.rs; the evaluation CLI in cli/tests/tone3.rs.
use core::engine::*;
use core::lm::{decode_from_fuzzy_scored, CappedLexicon, Profile};
use core::tone3::{positions, DELTA_TONE3};
use core::BEAM_S1;
use std::sync::OnceLock;

#[path = "common/toy.rs"]
mod toy;
use toy::{keys_of, real_lm_path, root};

const APP_PACKS: u32 = PACK_ACG | PACK_IT | PACK_MEME | PACK_BEAUTY;

/// One engine loaded from the files; every test engine is made from its shared parts (`Engine::new_shared`), with the App's switches.
fn shared() -> &'static Shared {
    static S: OnceLock<Shared> = OnceLock::new();
    S.get_or_init(|| {
        let mut e = Engine::new_with_packs(&root().join("data/lexicon"), Layout::Standard, Some((&root().join("data/packs"), APP_PACKS))).unwrap();
        e.load_lm(&real_lm_path()).unwrap();
        e.share().unwrap()
    })
}

fn app(tone3: bool) -> Engine {
    let mut e = Engine::new_shared(shared(), Layout::Standard, Some((&root().join("data/packs"), APP_PACKS))).unwrap();
    e.set_english(true).unwrap();
    e.set_english_overwrite(false).unwrap();
    e.set_tone3(tone3).unwrap();
    e
}

fn keys(e: &mut Engine, syls: &[&str]) -> Output {
    let mut last = None;
    for s in syls {
        for k in keys_of(Layout::Standard, s) {
            last = Some(e.key(k).expect("no internal error"));
        }
    }
    last.unwrap()
}

fn caps(e: &mut Engine, typed: &str) -> Output {
    let mut last = None;
    for c in typed.chars() {
        last = Some(e.key(Key::ch(c, 0)).expect("no internal error"));
    }
    last.unwrap()
}

fn press(e: &mut Engine, kind: KeyKind) -> Output {
    e.key(Key::new(kind)).expect("no internal error")
}

/// With the window open: pages (Space) until `word` is on the page shown, and picks it; `None` when no page has it.
fn find_and_pick(e: &mut Engine, mut o: Output, word: &str) -> Option<Output> {
    for _ in 0..=(o.total as usize / 9 + 1) {
        if let Some(i) = o.candidates.iter().position(|c| c == word) {
            return e.pick(i).unwrap();
        }
        o = press(e, KeyKind::Space);
    }
    None
}

fn syls(e: &Engine) -> Vec<&str> {
    e.syllables().iter().map(String::as_str).collect()
}

/// Contract tone3-sandhi §5 "收下音節": with adjacent keys off and English mixing on, ㄨㄛˊ (no one-syllable entry, ㄨㄛˇ has one) is accepted and shows
/// 我, not reverted to English; a second tone whose third tone has no entry either still reverts (c) as before. Off, ㄨㄛˊ reverts as it did.
#[test]
fn an_unaccepted_syllable_is_accepted_and_not_reverted() {
    let lex = &*toy::shared().lex;
    assert!(!lex.has_single("ㄨㄛˊ") && lex.has_single("ㄨㄛˇ"), "premise: ㄨㄛˊ is unaccepted");
    let mut e = app(true);
    let o = keys(&mut e, &["ㄨㄛˊ"]);
    assert_eq!(syls(&e), ["ㄨㄛˊ"]);
    assert_eq!(o.english_run_utf16_len, 0, "no English run");
    assert_eq!(o.preedit, "我");
    // ㄅㄧㄚˊ: neither it nor ㄅㄧㄚˇ has an entry.
    assert!(!lex.has_single("ㄅㄧㄚˊ") && !lex.has_single("ㄅㄧㄚˇ"), "premise");
    let mut e = app(true);
    let o = caps(&mut e, "1u86");
    assert!(syls(&e).is_empty());
    assert_eq!(o.preedit, "1u86", "reverted (c) as before");
    let mut off = app(false);
    let o = caps(&mut off, "ji6");
    assert!(syls(&off).is_empty());
    assert_eq!((o.preedit.as_str(), o.english_run_utf16_len), ("ji6", 3), "off: reverted (c), as the engine before the switch");
}

/// Contract tone3-sandhi §5 "引擎層級的按鍵測試": ㄒㄧㄤˊ ㄒㄧㄤ˙ and ㄌㄠˊ ㄏㄨ˙ keep both syllables (the unaccepted neutral tail after a second tone is accepted, not reverted)
/// and show 想想 and 老虎. Off, each key's output is the engine's before the switch, field for field, as the step-1 tree's `english-sim --keys` printed it
/// (祥 and the English run vu;7, 勞 and cj7: rule 10 (c) at the 7).
#[test]
fn a_neutral_tail_after_a_second_tone_is_kept() {
    for (typed, want, off_preedit) in [("vu;6vu;7", "想想", "祥vu;7"), ("xl6cj7", "老虎", "勞cj7")] {
        let mut e = app(true);
        let o = caps(&mut e, typed);
        assert_eq!(e.syllables().len(), 2, "{typed}: both syllables stay");
        assert_eq!((o.preedit.as_str(), o.english_run_utf16_len), (want, 0), "{typed}");
        let (mut off, mut fresh) = (app(false), app(true));
        fresh.set_tone3(false).unwrap();
        let mut last = None;
        for c in typed.chars() {
            let (a, b) = (off.key(Key::ch(c, 0)).unwrap(), fresh.key(Key::ch(c, 0)).unwrap());
            assert!(a == b, "{typed}: an engine switched off after creation is the one created off");
            last = Some(a);
        }
        let o = last.unwrap();
        let run = typed.len() - typed.find('6').unwrap() - 1;
        assert_eq!((o.preedit.as_str(), o.english_run_utf16_len as usize), (off_preedit, run), "{typed}: off, as before the switch");
        assert_eq!(off.syllables().len(), 1);
    }
}

/// Contract tone3-sandhi §5: after the neutral tail was accepted, the syllable before it changes, App defaults: (a) ㄌㄠˊ ㄏㄨ˙ with 老 picked alone over the
/// first syllable; (b) ㄒㄧㄤˊ ㄒㄧㄤ˙ with the cursor moved between them and a fourth tone typed there; (c) ㄒㄧㄤˊ ㄒㄧㄤ˙ with the first syllable deleted. No
/// `NoPath`, no reset: the tail stays and shows the character of Xˇ.
#[test]
fn the_tail_keeps_a_path_when_the_syllable_before_it_changes() {
    // (a)
    let mut e = app(true);
    caps(&mut e, "xl6cj7");
    press(&mut e, KeyKind::Home);
    let o = press(&mut e, KeyKind::Down);
    let o = find_and_pick(&mut e, o, "老").expect("老 is listed for ㄌㄠˊ (through ㄌㄠˇ)");
    assert_eq!(e.syllables().len(), 2);
    assert!(o.preedit.starts_with('老') && o.preedit.chars().count() == 2, "(a) {}", o.preedit);
    assert!(o.preedit.ends_with('虎') || o.preedit.ends_with('琥') || o.preedit.chars().nth(1).is_some_and(|c| c != 'ㄏ'), "(a) the tail decodes");
    // (b)
    let mut e = app(true);
    caps(&mut e, "vu;6vu;7");
    press(&mut e, KeyKind::Left);
    let o = keys(&mut e, &["ㄉㄚˋ"]);
    assert_eq!(syls(&e), ["ㄒㄧㄤˊ", "ㄉㄚˋ", "ㄒㄧㄤ˙"], "(b)");
    assert_eq!(o.preedit.chars().count(), 3, "(b) every syllable decodes: {}", o.preedit);
    assert!(!o.preedit.contains('ㄒ'));
    // (c)
    let mut e = app(true);
    caps(&mut e, "vu;6vu;7");
    press(&mut e, KeyKind::Left);
    let o = press(&mut e, KeyKind::Backspace);
    assert_eq!(syls(&e), ["ㄒㄧㄤ˙"], "(c)");
    assert_eq!(o.preedit, "想", "(c) the tail alone reads as ㄒㄧㄤˇ");
}

/// Contract tone3-sandhi §5: a second tone that only occurs in multi-syllable words (Xˊ has a syllable id, no one-syllable entry; Xˇ has one), typed
/// alone, is accepted and shows Xˇ's first character: no `NoPath`, no reset. The shipped lexicon has no such syllable (every unaccepted Xˊ there has no id at
/// all, and is read through its adjacent-key readings too), so the base gets one overlay row 子子 ㄗˊ-ㄗˇ for it.
#[test]
fn a_second_tone_known_only_inside_words_shows_its_third_tone() {
    let dir = root().join("data/lexicon");
    let base = std::fs::read_to_string(dir.join("mcbpmf-data.txt")).unwrap();
    let lex = std::sync::Arc::new(core::Lexicon::parse_with(&base, Some("ㄗˊ-ㄗˇ\t子子\t-6.0\textra\n")).unwrap());
    assert!(lex.has_syllable("ㄗˊ") && core::tone3::unaccepted(&lex, "ㄗˊ"), "premise: ㄗˊ has an id and no one-syllable entry");
    let st = toy::shared();
    let capped = std::sync::Arc::new(CappedLexicon::new(lex.clone(), "", &st.lm, None).unwrap());
    let mut e = Engine::with_lexicon(lex.clone(), Layout::Standard);
    e.set_lm(st.lm.clone(), capped);
    let o = keys(&mut e, &["ㄗˊ"]);
    assert_eq!(syls(&e), ["ㄗˊ"]);
    assert_eq!(o.preedit, lex.entries(&["ㄗˇ".to_string()])[0].0, "the first word of ㄗˇ");
}

/// Contract tone3-sandhi §5 (and §2 B, docs/spec/english-mixed.md §7): with the digit layer on, an unaccepted syllable typed with digit-alphabet keys only
/// (ㄓㄢˊ `5 0 6`) is Zhuyin; off, it stays the digits 506, as before.
#[test]
fn an_unaccepted_syllable_of_digit_keys_is_zhuyin() {
    let lex = &*toy::shared().lex;
    assert!(core::tone3::unaccepted(lex, "ㄓㄢˊ"), "premise");
    let mut e = app(true);
    let o = caps(&mut e, "506");
    assert_eq!(syls(&e), ["ㄓㄢˊ"]);
    assert!(lex.entries(&["ㄓㄢˇ".to_string()]).iter().any(|(w, _)| *w == o.preedit), "{}", o.preedit);
    let mut off = app(false);
    let o = caps(&mut off, "506");
    assert_eq!(o.preedit, "506");
}

/// Contract tone3-sandhi §5 "候選窗": the list of ㄓㄚˊ ㄧㄢˇ holds 眨眼 (after the exact words); picking it makes the span ㄓㄚˇ ㄧㄢˇ. Off, it is not listed.
#[test]
fn the_window_lists_the_sandhi_word_and_picking_it_rewrites_the_span() {
    for on in [true, false] {
        let mut e = app(on);
        keys(&mut e, &["ㄓㄚˊ", "ㄧㄢˇ"]);
        let o = press(&mut e, KeyKind::Down);
        let picked = find_and_pick(&mut e, o, "眨眼");
        assert_eq!(picked.is_some(), on, "on {on}");
        if picked.is_some() {
            assert_eq!(syls(&e), ["ㄓㄚˇ", "ㄧㄢˇ"]);
        }
    }
}

/// Contract tone3-sandhi §2 F, §5: turning the switch off stores the accepted unaccepted syllables (and tails) as Xˇ, so the composition keeps a path and is
/// not cleared.
#[test]
fn turning_it_off_stores_the_accepted_syllables_as_their_third_tone() {
    let mut e = app(true);
    caps(&mut e, "ji6");
    caps(&mut e, "xl6cj7");
    assert_eq!(syls(&e), ["ㄨㄛˊ", "ㄌㄠˊ", "ㄏㄨ˙"]);
    let o = e.set_tone3(false).unwrap();
    assert_eq!(syls(&e), ["ㄨㄛˇ", "ㄌㄠˊ", "ㄏㄨˇ"]);
    assert_eq!(o.preedit.chars().count(), 3, "{}", o.preedit);
    assert!(o.preedit.starts_with('我'));
}

/// Contract tone3-sandhi §2 C, §5: a sandhi word is no fuzzy word (no marker, no `fuzzy_skips`) and its reading term is its lexicon reading's; the penalty is
/// taken once per word. 眨眼 typed ㄓㄚˊ ㄧㄢˇ scores exactly 眨眼 typed ㄓㄚˇ ㄧㄢˇ minus `DELTA_TONE3`.
#[test]
fn a_sandhi_word_is_no_fuzzy_word_and_pays_the_penalty_once() {
    let st = toy::shared();
    let (lex, capped, lm): (&core::Lexicon, &CappedLexicon, &core::lm::Lm) = (&st.lex, &st.capped, &st.lm);
    let v = |s: &[&str]| s.iter().map(|x| x.to_string()).collect::<Vec<_>>();
    let typed = v(&["ㄓㄚˊ", "ㄧㄢˇ"]);
    let t3 = positions(&typed, lex, true);
    let nb = decode_from_fuzzy_scored(capped, &typed, lm, Profile::Chat, BEAM_S1, "<s>", true, 0, core::fuzzy::DELTA_SOUND, None, Some((&t3, DELTA_TONE3))).unwrap();
    let (score, ws) = nb.iter().find(|(_, ws)| ws.len() == 1 && ws[0].0 == "眨眼").expect("眨眼 is decoded");
    assert!(!ws[0].4, "no fuzzy marker");
    assert_eq!(ws[0].2, DELTA_TONE3, "the penalty, once");
    let exact = decode_from_fuzzy_scored(capped, &v(&["ㄓㄚˇ", "ㄧㄢˇ"]), lm, Profile::Chat, BEAM_S1, "<s>", true, 0, core::fuzzy::DELTA_SOUND, None, None).unwrap();
    let (s0, _) = exact.iter().find(|(_, ws)| ws.len() == 1 && ws[0].0 == "眨眼").unwrap();
    assert!((score - (s0 - DELTA_TONE3)).abs() < 1e-9, "{score} vs {s0}");
    // Two positions replaced (展覽館 typed ㄓㄢˊ ㄌㄢˊ ㄍㄨㄢˇ): still one penalty.
    let typed = v(&["ㄓㄢˊ", "ㄌㄢˊ", "ㄍㄨㄢˇ"]);
    let t3 = positions(&typed, lex, true);
    let nb = decode_from_fuzzy_scored(capped, &typed, lm, Profile::Chat, BEAM_S1, "<s>", true, 0, core::fuzzy::DELTA_SOUND, None, Some((&t3, DELTA_TONE3))).unwrap();
    let ws = &nb.iter().find(|(_, ws)| ws.len() == 1 && ws[0].0 == "展覽館").expect("展覽館").1;
    assert_eq!(ws[0].2, DELTA_TONE3);
    // The rerank pool keeps a sandhi path (it is not a fuzzy path).
    assert!(core::lm::rerank_pool(&nb, 8).is_some());
}

/// Contract tone3-sandhi §2 C "上限": the sandhi source keeps at most 12 words over its readings, counted apart from the fuzzy-sound ones; no word is read
/// through sandhi and a fuzzy pair at once (its delta would be the sum).
#[test]
fn the_sandhi_source_has_its_own_caps() {
    let st = toy::shared();
    let (lex, capped, lm): (&core::Lexicon, &CappedLexicon, &core::lm::Lm) = (&st.lex, &st.capped, &st.lm);
    // ㄧㄥˊ before a third tone: the sandhi reading ㄧㄥˇ, and with ㄣ/ㄥ on the fuzzy reading ㄧㄣˊ.
    let typed: Vec<String> = ["ㄧㄥˊ", "ㄏㄠˇ"].iter().map(|s| s.to_string()).collect();
    let t3 = positions(&typed, lex, true);
    let one = &typed[..1];
    let count = |flags: u32, t3: Option<(&[core::tone3::Pos], f64)>, delta: f64| {
        let nb = decode_from_fuzzy_scored(capped, one, lm, Profile::Chat, BEAM_S1, "<s>", true, flags, core::fuzzy::DELTA_SOUND, None, t3).unwrap();
        assert!(nb.iter().all(|(_, ws)| ws[0].2 != DELTA_TONE3 + core::fuzzy::DELTA_SOUND), "no word through both");
        nb.iter().filter(|(_, ws)| ws[0].2 == delta).count()
    };
    assert!(lex.entries(&["ㄧㄥˇ".to_string()]).len() > 12 && lex.entries(&["ㄧㄣˊ".to_string()]).len() > 12, "premise: both readings have more than 12 words");
    assert_eq!(count(0, Some((&t3[..1], DELTA_TONE3)), DELTA_TONE3), 12, "at most 12 sandhi words");
    assert_eq!(count(0b0001, None, core::fuzzy::DELTA_SOUND), 12, "premise: 12 fuzzy words alone");
    assert_eq!(count(0b0001, Some((&t3[..1], DELTA_TONE3)), DELTA_TONE3), 12, "the sandhi cap is its own with the fuzzy pairs on");
    assert_eq!(count(0b0001, Some((&t3[..1], DELTA_TONE3)), core::fuzzy::DELTA_SOUND), 12, "and the fuzzy cap is unchanged");
}

/// Contract tone3-sandhi §2 C, §5 "手動詞保護": `path_readings` gives a sandhi word under its lexicon reading, so a manual word that is the n-gram's first through
/// sandhi is protected (the candidate that drops it is masked); without the table it is matched against the typed reading and nothing is protected.
#[test]
fn a_manual_word_first_through_sandhi_is_protected() {
    let lex = &*toy::shared().lex;
    let typed: Vec<String> = ["ㄓㄚˊ", "ㄧㄢˇ"].iter().map(|s| s.to_string()).collect();
    let t3 = positions(&typed, lex, true);
    assert_eq!(lex.path_readings(&typed, [("眨眼", 2)], &t3), ["ㄓㄚˇ", "ㄧㄢˇ"]);
    assert_eq!(lex.path_readings(&typed, [("眨眼", 2)], &[]), typed, "off: the typed reading");
    let manual = core::ManualSet::from_pairs([("眨眼", "ㄓㄚˇ-ㄧㄢˇ")]);
    let used = lex.path_readings(&typed, [("眨眼", 2)], &t3);
    assert_eq!(core::tinylm::manual_mask(&used, &["眨眼", "炸眼"], &manual), [false, true]);
    assert_eq!(core::tinylm::manual_mask(&typed, &["眨眼", "炸眼"], &manual), [false, false]);
}

/// Contract tone3-sandhi §2 C, §5: with the small model and the rerank on, a stretch whose n-gram first is a sandhi word goes to the rerank (it is no fuzzy path):
/// a job holds it and `fuzzy_skips` stays 0.
#[test]
fn a_sandhi_first_path_is_reranked_not_skipped() {
    let mut e = app(true);
    e.load_tinylm(&root().join("data/lm/tinylm.sjtl")).unwrap();
    e.set_rerank(true).unwrap();
    let o = keys(&mut e, &["ㄓㄚˊ", "ㄧㄢˇ"]);
    assert_eq!(o.preedit, "眨眼", "premise: the n-gram first is the sandhi word");
    assert_eq!(e.fuzzy_skips(), 0);
    let job = e.take_rerank_job().expect("a rerank job");
    assert!(job.candidates().iter().any(|c| c.first().is_some_and(|f| f == "眨眼")), "the stretch is in the job, 眨眼 first");
}
