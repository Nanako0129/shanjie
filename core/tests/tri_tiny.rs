//! Trigram level on the toy model (docs/contracts/trigram-core.md, slice A, acceptance 2, 3 and 6).
//! The toy bigram model, classes.sjc and kn.sjkn are eval/golden/kn-tiny/'s; the two tables (formal `trigram.sjt3`, chat `trigram-chat.sjt3`), lexicon and
//! decode.tsv are eval/golden/tri-tiny/'s (tools/gen_tri_tiny.py; decode.tsv is the Python reference decoder's output, compared byte for byte).
//! The engine tests have no word classes, so their hand values are plain bigram arithmetic (tolerance 1e-12: the hand values use `powf`).
use core::engine::{Engine, Key, KeyKind, Layout, Output};
use core::lm::{decode, decode_from, decode_segment_learned, history, CappedLexicon, End, Learn, Lm, LmError, Profile, LEARN_EPS_GLOBAL};
use core::predict::{predict, units_of, Mode};
use core::Lexicon;
use std::path::{Path, PathBuf};
use std::sync::Arc;

fn dir(d: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../eval/golden").join(d)
}
fn bytes(d: &str, name: &str) -> Vec<u8> {
    std::fs::read(dir(d).join(name)).unwrap()
}
/// The toy model with the pieces asked for: word classes, the Kneser-Ney side file, the formal profile's table, the chat profile's table.
fn lm_of(classes: bool, kn: bool, formal: bool, chat: bool) -> Lm {
    let model = bytes("kn-tiny", "bigram.sjlm");
    let mut lm = if classes { Lm::parse_with_classes(&model, &bytes("kn-tiny", "classes.sjc")).unwrap() } else { Lm::parse(&model).unwrap() };
    if kn {
        lm.add_kn(&bytes("kn-tiny", "kn.sjkn")).unwrap();
    }
    if formal {
        lm.add_tri_for(Profile::Formal, bytes("tri-tiny", "trigram.sjt3")).unwrap();
    }
    if chat {
        lm.add_tri_for(Profile::Chat, bytes("tri-tiny", "trigram-chat.sjt3")).unwrap();
    }
    lm
}
#[track_caller]
fn close(a: f64, b: f64) {
    assert!((a - b).abs() < 1e-12, "{a} != {b}");
}
fn syls(s: &str) -> Vec<String> {
    s.split(' ').map(String::from).collect()
}

// ---------- decoding: the Python reference, byte for byte ----------

const SENTENCES: [&str; 6] = ["ㄊㄚ ㄓㄢˋ", "ㄊㄚ ㄓㄢˋ ㄊㄚ ㄓㄢˋ", "ㄑㄧˇ ㄔㄨㄤˊ", "ㄊㄚ ㄑㄧˇ ㄔㄨㄤˊ", "ㄓㄢˋ ㄊㄚ ㄓㄢˋ", "ㄊㄚ ㄓㄢˋ ㄊㄚ"];
const STARTS: [&str; 4] = ["<s>", "他", "占", "起牀"];

fn capped_of(lm: &Lm, lexicon: &[u8]) -> CappedLexicon {
    let lex = Arc::new(Lexicon::parse(std::str::from_utf8(lexicon).unwrap()).unwrap());
    CappedLexicon::new(lex, "", lm, Some(&core::lm::Demote::parse("").unwrap())).unwrap()
}

/// Every config, start word and sentence: the profile's own table with Kneser-Ney on (formal at FORMAL_KN_BETA 0.75, chat at the file's β 1) or without a side
/// file is decode.tsv. Both tables are loaded in every config, so a profile that read the other's table would differ. A surface with two segmentations
/// that differ in the previous word (ㄊㄚ ㄓㄢˋ ㄊㄚ, 他|占|他 and 他占|他) keeps both until the next word, which is what the recombination key buys: without
/// the previous word in the key the top score differs.
#[test]
fn decode_matches_python_byte_for_byte() {
    let want = String::from_utf8(bytes("tri-tiny", "decode.tsv")).unwrap();
    let lexicon = bytes("tri-tiny", "lexicon.txt");
    let mut got = String::new();
    for (config, profile, kn) in [("formal", Profile::Formal, true), ("formal-nokn", Profile::Formal, false), ("chat", Profile::Chat, true), ("chat-nokn", Profile::Chat, false)] {
        let lm = lm_of(true, kn, true, true);
        let capped = capped_of(&lm, &lexicon);
        for start in STARTS {
            for (i, s) in SENTENCES.iter().enumerate() {
                for (r, (sc, ws)) in decode_from(&capped, &syls(s), &lm, profile, core::BEAM_S1, start, true).unwrap().iter().enumerate() {
                    got += &format!("{config}\t{start}\t{}\t{}\t{}\t{sc:?}\n", i + 1, r + 1, ws.concat());
                }
            }
        }
    }
    assert_eq!(got, want);
}

/// Each profile ignores the other's table and uses its own: with only the formal table, chat decodes bit for bit as with no table (and formal does not);
/// with only the chat table the reverse. Premise for the golden above: each table changes its own profile's scores.
#[test]
fn a_profile_ignores_the_other_profiles_table() {
    let lexicon = bytes("tri-tiny", "lexicon.txt");
    let (none, formal, chat) = (lm_of(true, true, false, false), lm_of(true, true, true, false), lm_of(true, true, false, true));
    let capped = [&none, &formal, &chat].map(|lm| capped_of(lm, &lexicon));
    let (mut formal_differs, mut chat_differs) = (false, false);
    for start in STARTS {
        for s in SENTENCES {
            let run = |i: usize, lm: &Lm, p| decode_from(&capped[i], &syls(s), lm, p, core::BEAM_S1, start, true).unwrap().into_iter().map(|(sc, ws)| (sc.to_bits(), ws)).collect::<Vec<_>>();
            let (base_chat, base_formal) = (run(0, &none, Profile::Chat), run(0, &none, Profile::Formal));
            assert_eq!(run(1, &formal, Profile::Chat), base_chat, "chat with only the formal table {start} {s}");
            assert_eq!(run(2, &chat, Profile::Formal), base_formal, "formal with only the chat table {start} {s}");
            formal_differs |= run(1, &formal, Profile::Formal) != base_formal;
            chat_differs |= run(2, &chat, Profile::Chat) != base_chat;
        }
    }
    assert!(formal_differs && chat_differs);
}

// ---------- Lm::load ----------

fn table_bytes() -> Vec<u8> {
    bytes("tri-tiny", "trigram.sjt3")
}
fn chat_table_bytes() -> Vec<u8> {
    bytes("tri-tiny", "trigram-chat.sjt3")
}

/// The tables beside the model: `trigram.sjt3` is the formal profile's, `trigram-chat.sjt3` the chat profile's. A missing file is that profile's table off and
/// nothing else; a bad one (or a dangling symlink) is an error for `Lm::load` (the engine reads both), so one bad file fails the whole load, while `load_opts`
/// reads only the profiles it is given (the CLI: its profile's table), so the other profile's bad file does not matter there; `--no-tri` (`&[]`) reads neither.
#[test]
fn load_reads_each_profiles_table_beside_the_model() {
    let tmp = std::env::temp_dir().join(format!("shanjie-tri-tiny-{}", std::process::id()));
    std::fs::create_dir_all(&tmp).unwrap();
    for f in ["bigram.sjlm", "classes.sjc"] {
        std::fs::write(tmp.join(f), bytes("kn-tiny", f)).unwrap();
    }
    let m = tmp.join("bigram.sjlm");
    let (formal, chat) = (tmp.join("trigram.sjt3"), tmp.join("trigram-chat.sjt3"));
    let tag = |b: &[u8]| format!("+tri:{}", &core::eval::sha256_hex(b)[..8]);
    let tags = |lm: &Lm| (lm.tri_tag(Profile::Chat).map(String::from), lm.tri_tag(Profile::Formal).map(String::from));
    assert_eq!(tags(&Lm::load(&m).unwrap()), (None, None), "no files: both off");
    // only one file: only that profile's table, whatever the other profile's file is called
    std::fs::write(&formal, table_bytes()).unwrap();
    let only_formal = Lm::load(&m).unwrap();
    assert_eq!(tags(&only_formal), (None, Some(tag(&table_bytes()))));
    assert!(only_formal.uses_tri(only_formal.scoring(Profile::Formal)) && !only_formal.uses_tri(only_formal.scoring(Profile::Chat)));
    std::fs::remove_file(&formal).unwrap();
    std::fs::write(&chat, chat_table_bytes()).unwrap();
    let only_chat = Lm::load(&m).unwrap();
    assert_eq!(tags(&only_chat), (Some(tag(&chat_table_bytes())), None));
    assert!(only_chat.uses_tri(only_chat.scoring(Profile::Chat)) && !only_chat.uses_tri(only_chat.scoring(Profile::Formal)));
    // both files: the tag of each is its own file's sha8
    std::fs::write(&formal, table_bytes()).unwrap();
    let on = Lm::load(&m).unwrap();
    assert_eq!(tags(&on), (Some(tag(&chat_table_bytes())), Some(tag(&table_bytes()))));
    assert_ne!(tags(&on).0, tags(&on).1, "premise: the two toy tables differ");
    assert_eq!(tags(&Lm::load_opts(&m, true, true, &[]).unwrap()), (None, None), "--no-tri");
    assert_eq!(tags(&Lm::load_opts(&m, true, true, &[Profile::Chat]).unwrap()), (Some(tag(&chat_table_bytes())), None), "the CLI's chat reads only the chat file");
    assert_eq!(tags(&Lm::load_opts(&m, true, true, &[Profile::Formal]).unwrap()), (None, Some(tag(&table_bytes()))), "the CLI's formal reads only the formal file");
    assert_eq!(tags(&Lm::load_with(&m, true, true).unwrap()), tags(&on));
    // a bad or dangling file: the engine's load fails for either file; the CLI's profile is fine as long as its own file is
    for (bad, reads_bad, other) in [(&chat, Profile::Chat, Profile::Formal), (&formal, Profile::Formal, Profile::Chat)] {
        let good = std::fs::read(bad).unwrap();
        let mut broken = good.clone();
        broken[0] ^= 1;
        std::fs::write(bad, broken).unwrap();
        assert_eq!(Lm::load(&m).err(), Some(LmError::TriTable), "a bad table is never ignored: {bad:?}");
        assert!(Lm::load_opts(&m, true, true, &[other]).is_ok(), "the other profile does not read it");
        assert!(Lm::load_opts(&m, true, true, &[]).is_ok(), "--no-tri does not read it");
        assert_eq!(Lm::load_opts(&m, true, true, &[reads_bad]).err(), Some(LmError::TriTable), "the profile that reads it fails");
        std::fs::remove_file(bad).unwrap();
        std::os::unix::fs::symlink(tmp.join("missing.sjt3"), bad).unwrap();
        assert_eq!(Lm::load(&m).err(), Some(LmError::TriTable), "a dangling symlink is not 'absent': {bad:?}");
        assert!(Lm::load_opts(&m, true, true, &[other]).is_ok());
        assert_eq!(Lm::load_opts(&m, true, true, &[reads_bad]).err(), Some(LmError::TriTable));
        std::fs::remove_file(bad).unwrap();
        std::fs::write(bad, good).unwrap();
    }
    assert!(Lm::load(&m).is_ok(), "premise: both files are good again");
    std::fs::remove_dir_all(tmp).unwrap();
}

// ---------- the engine ----------

const LEX: &str = "ㄊㄚ 他 -2.0\nㄓㄢ 他 -2.9\nㄓㄢˋ 占 -3.0\nㄓㄢˋ 佔 -3.5\n";

struct Fx {
    lex: Arc<Lexicon>,
    lm: Arc<Lm>,
    capped: Arc<CappedLexicon>,
}
/// The class-less toy model (hand values are plain bigram arithmetic) with the formal table or not and no Kneser-Ney side file, so the hand values are P2 itself.
fn fx(tri: bool) -> Fx {
    fx_of(false, tri, false)
}
/// `fx` with the pieces named: the Kneser-Ney side file, the formal table, the chat table.
fn fx_of(kn: bool, formal: bool, chat: bool) -> Fx {
    let lm = Arc::new(lm_of(false, kn, formal, chat));
    let lex = Arc::new(Lexicon::parse_with(LEX, None).unwrap());
    let capped = Arc::new(CappedLexicon::new(lex.clone(), "", &lm, None).unwrap());
    Fx { lex, lm, capped }
}
fn engine(f: &Fx, profile: Profile) -> Engine {
    let mut e = Engine::with_lexicon(f.lex.clone(), Layout::Standard);
    e.set_lm(f.lm.clone(), f.capped.clone());
    e.set_profile(profile).unwrap();
    e
}
fn keys_of(syl: &str) -> Vec<Key> {
    let l = Layout::Standard;
    let mut v = Vec::new();
    let mut toned = false;
    for c in syl.chars() {
        match l.key_of_tone(c) {
            Some(tk) => {
                v.push(Key::ch(tk, 0));
                toned = true;
            }
            None => v.push(Key::ch(l.key_of_symbol(c).unwrap(), 0)),
        }
    }
    if !toned {
        v.push(Key::new(KeyKind::Space));
    }
    v
}
fn type_syls(e: &mut Engine, s: &str) -> Output {
    let mut last = None;
    for syl in s.split(' ') {
        for k in keys_of(syl) {
            last = Some(e.key(k).unwrap());
        }
    }
    last.unwrap()
}
/// A fixed one-syllable `word` (the syllable `mid`) with `left` and `right` typed around it. A candidate pick freezes the words
/// outside its span (docs/contracts/repick-freeze.md), so the word is picked over its own syllable first and the stretches are typed
/// afterwards: they stay free, which is the state these tests score.
fn fix_word(e: &mut Engine, left: &str, mid: &str, word: &str, right: &str) -> Output {
    type_syls(e, mid);
    let mut o = fix_at(e, 1, word);
    if !right.is_empty() {
        e.key(Key::new(KeyKind::End)).unwrap();
        o = type_syls(e, right);
    }
    if !left.is_empty() {
        e.key(Key::new(KeyKind::Home)).unwrap();
        o = type_syls(e, left);
    }
    o
}

/// Choose `word` for the syllables ending at `end` of what is typed: the cursor goes Home and Right `end` times, then the candidate window.
fn fix_at(e: &mut Engine, end: usize, word: &str) -> Output {
    e.key(Key::new(KeyKind::Home)).unwrap();
    for _ in 0..end {
        e.key(Key::new(KeyKind::Right)).unwrap();
    }
    let mut o = e.key(Key::new(KeyKind::Space)).unwrap();
    for _ in 0..50 {
        if o.candidates[o.selected.unwrap()] == word {
            return e.key(Key::new(KeyKind::Enter)).unwrap();
        }
        o = e.key(Key::new(KeyKind::Right)).unwrap();
    }
    panic!("candidate not found");
}

/// Acceptance 6, rules (a), (b), (c): ㄓㄢˋ ㄊㄚ ㄓㄢˋ with 他 fixed in the middle. The first word of the first stretch starts from (<s>, <s>), the
/// transition into the fixed word is scored from (the word before, the last word), the stretch after the fixed word starts from (unknown, 他) so its
/// first word is bigram only, and its closing term is the bigram too: the stretch has one word, so slice A2 (b') takes u of the closing term as
/// unknown. `total_score` is the hand value and the sum of the two stretch decodes; the premises at the end show that a known u after the fixed
/// word, or the table in the closing term, would score differently. The first word's term is floored (slice A2 (e): back3 0.175 < 0.5).
#[test]
fn fixed_word_in_the_middle_hand_values_and_total_score() {
    let f = fx(true);
    let plain = lm_of(false, false, false, false); // P2 without Kneser-Ney
    let mut e = engine(&f, Profile::Formal);
    let o = fix_word(&mut e, "ㄓㄢˋ", "ㄊㄚ", "他", "ㄓㄢˋ");
    assert_eq!(o.preedit, "占他占");
    let (lam, d) = (Profile::Formal.lambda(), 0.75);
    let pb = |lp: f64| 10f64.powf(lp);
    // (a) the first word, (<s>, <s>) -> 占: the context is stored, the entry is not; back3 is 0.175, below the floor, so the term is 0.5 * P2
    let back3 = 1.0 - ((12.0 - d) + (6.0 - d)) / 20.0;
    assert!(back3 < 0.5, "premise: the floor is active");
    let t1 = lam * (0.5 * plain.prob("<s>", "占", pb(-3.0))).log10() + (1.0 - lam) * -3.0;
    // (b) the transition into the fixed word, (<s>, 占) -> 他 5 of 6 is kept
    let back3 = 1.0 - (5.0 - d) / 6.0;
    let t2 = lam * ((5.0 - d) / 6.0 + back3 * plain.prob("占", "他", pb(-2.0))).log10() + (1.0 - lam) * -2.0;
    // (a) after the fixed word: (unknown, 他) -> 占 is the bigram, although (占, 他) -> 占 is stored (9 of 10)
    let t3 = plain.word(lam, "他", "占", -3.0);
    // (b') the closing term of the one-word stretch: (他, 占) -> </s> is kept in the table, but u is unknown here, so it is the bigram
    let t4 = lam * plain.prob("占", "</s>", 14.0 / 64.0).log10();
    let total = e.total_score().unwrap();
    close(total, t1 + t2 + t3 + t4);
    // (c) the engine's total is the decoders' scores: stretch one closes into 他, stretch two starts after it
    let cfg = f.lm.scoring(Profile::Formal);
    let next = End::Next { word: "他", lp: -2.0, delta: 0.0 };
    let a = decode_segment_learned(&f.capped, &syls("ㄓㄢˋ"), &f.lm, cfg, "<s>", next, core::BEAM_S1, None, true).unwrap();
    let b = decode_segment_learned(&f.capped, &syls("ㄓㄢˋ"), &f.lm, cfg, "他", End::Eos, core::BEAM_S1, None, true).unwrap();
    assert_eq!((a[0].1[0].0, b[0].1[0].0), ("占", "占"));
    close(a[0].0 + b[0].0, total);
    // the premise that makes (a) and (b) tell something: the table changes these terms
    let wrong_u = f.lm.word3(cfg, Some("占"), "他", "占", -3.0);
    assert!((wrong_u - t3).abs() > 1e-3, "premise: a known u after the fixed word would score differently");
    assert_eq!(f.lm.eos3(cfg, None, "占"), t4);
    assert!((f.lm.eos3(cfg, Some("他"), "占") - t4).abs() > 1e-3, "premise: the table's closing term (u = the start word) would score differently");
}

/// Slice A2 (b') in the engine: a stretch of one word whose start is unknown closes with the bigram (with `Eos`:
/// `fixed_word_in_the_middle_hand_values_and_total_score`). Each case checks the decode against a hand value and `total_score` against the decodes,
/// with a premise that the table's closing term from (the start word, the last word) is a different number. Closed by `End::Next`: 他 ㄓㄢˋ 他 with
/// both 他 fixed; stretch 占 starts after the first 他 and is closed by the second.
#[test]
fn one_word_stretch_between_two_fixed_words_closes_with_the_bigram_next() {
    let f = fx(true);
    let mut e = engine(&f, Profile::Formal);
    type_syls(&mut e, "ㄊㄚ ㄓㄢˋ ㄊㄚ");
    fix_at(&mut e, 1, "他");
    let o = fix_at(&mut e, 3, "他");
    assert_eq!(o.preedit, "他占他", "premise: both 他 are fixed and 占 is between them");
    let cfg = f.lm.scoring(Profile::Formal);
    let next = End::Next { word: "他", lp: -2.0, delta: 0.0 };
    let s = decode_segment_learned(&f.capped, &syls("ㄓㄢˋ"), &f.lm, cfg, "他", next, core::BEAM_S1, None, true).unwrap();
    assert_eq!(s[0].1[0].0, "占");
    // the closing term: the transition 占 -> 他; the bigram has u unknown, the table's would be (他, 占) -> 他 (a stored context without that entry)
    let bigram = f.lm.word3(cfg, None, "占", "他", -2.0);
    let table = f.lm.word3(cfg, Some("他"), "占", "他", -2.0);
    assert!((bigram - table).abs() > 1e-3, "premise: the table changes the closing term");
    close(s[0].0, f.lm.word3(cfg, None, "他", "占", -3.0) + bigram);
    // total_score: the first fixed word's own term (it is in no stretch), the stretch, and the last fixed word's closing term
    let first = f.lm.word3(cfg, Some("<s>"), "<s>", "他", -2.0);
    // a fixed word at the end has no stretch after it: only `total_score` scores its closing term (u unknown, bigram)
    let last = f.lm.eos3(cfg, None, "他");
    close(e.total_score().unwrap(), first + s[0].0 + last);
}

/// The unknown start that comes from the input-box text before the composition (left context 他): one word closes with the bigram, with `Eos` and with
/// `End::Next`.
#[test]
fn one_word_stretch_after_an_input_box_context_closes_with_the_bigram() {
    let f = fx(true);
    let cfg = f.lm.scoring(Profile::Formal);
    // Eos: 占 alone
    let mut e = engine(&f, Profile::Formal);
    e.set_left_context("他");
    assert_eq!(type_syls(&mut e, "ㄓㄢˋ").preedit, "占");
    let s = decode_segment_learned(&f.capped, &syls("ㄓㄢˋ"), &f.lm, cfg, "他", End::Eos, core::BEAM_S1, None, true).unwrap();
    let (bigram, table) = (f.lm.eos3(cfg, None, "占"), f.lm.eos3(cfg, Some("他"), "占"));
    assert!((bigram - table).abs() > 1e-3, "premise: the table changes the closing term");
    close(s[0].0, f.lm.word3(cfg, None, "他", "占", -3.0) + bigram);
    close(e.total_score().unwrap(), s[0].0);
    // End::Next: 占 then a fixed 他
    let mut e = engine(&f, Profile::Formal);
    e.set_left_context("他");
    type_syls(&mut e, "ㄓㄢˋ ㄊㄚ");
    assert_eq!(fix_at(&mut e, 2, "他").preedit, "占他");
    let next = End::Next { word: "他", lp: -2.0, delta: 0.0 };
    let s = decode_segment_learned(&f.capped, &syls("ㄓㄢˋ"), &f.lm, cfg, "他", next, core::BEAM_S1, None, true).unwrap();
    let (bigram, table) = (f.lm.word3(cfg, None, "占", "他", -2.0), f.lm.word3(cfg, Some("他"), "占", "他", -2.0));
    assert!((bigram - table).abs() > 1e-3, "premise: the table changes the closing term");
    close(s[0].0, f.lm.word3(cfg, None, "他", "占", -3.0) + bigram);
    // a fixed word at the end has no stretch after it: only `total_score` scores its closing term (u unknown, bigram)
    let last = f.lm.eos3(cfg, None, "他");
    close(e.total_score().unwrap(), s[0].0 + last);
}

/// (b') belongs to the stretch: after punctuation or an open English run the next stretch starts from (<s>, <s>), so its one word closes with the
/// table again. Left context 他, then 占 | 占 with "，" or an English run between: the left part closes with the bigram (unknown start, one word), the
/// right part with the table ((<s>, 占) is stored without </s>, so the floored table term is half the bigram's).
#[test]
fn unknown_start_ends_at_punctuation_and_at_an_english_run() {
    let f = fx(true);
    let cfg = f.lm.scoring(Profile::Formal);
    let part = |start: &str| decode_segment_learned(&f.capped, &syls("ㄓㄢˋ"), &f.lm, cfg, start, End::Eos, core::BEAM_S1, None, true).unwrap()[0].0;
    assert!((f.lm.eos3(cfg, Some("<s>"), "占") - f.lm.eos3(cfg, None, "占")).abs() > 1e-3, "premise: the right part's closing term is the table's");
    let want = part("他") + part("<s>");
    // punctuation
    let mut e = engine(&f, Profile::Formal);
    e.set_left_context("他");
    type_syls(&mut e, "ㄓㄢˋ");
    e.key(Key::ch(',', core::engine::MOD_SHIFT)).unwrap();
    assert_eq!(type_syls(&mut e, "ㄓㄢˋ").preedit, "占，占");
    close(e.total_score().unwrap(), want);
    // an open English run at the cursor
    let mut e = engine(&f, Profile::Formal);
    e.load_english(&dir("").join("english-tiny.txt")).unwrap();
    e.set_english(true).unwrap();
    e.set_left_context("他");
    type_syls(&mut e, "ㄓㄢˋ ㄓㄢˋ");
    e.key(Key::new(KeyKind::Left)).unwrap();
    let o = e.key(Key::ch('x', core::engine::MOD_SHIFT)).unwrap();
    assert_eq!(o.preedit.replace('X', ""), "占占", "premise: the run is open between the two syllables ({})", o.preedit);
    close(e.total_score().unwrap(), want);
}

/// Two words or more are unchanged: the second word's u is the start word and the closing term is (second last, last), from the table. Left context 他
/// with 他占 (Eos), and 他占 between two fixed 他 (End::Next).
#[test]
fn two_word_stretches_keep_the_table_closing_term() {
    let f = fx(true);
    let cfg = f.lm.scoring(Profile::Formal);
    // Eos, from the input-box context: (unknown, 他) -> 他, (他, 他) -> 占, (他, 占) -> </s>
    let mut e = engine(&f, Profile::Formal);
    e.set_left_context("他");
    assert_eq!(type_syls(&mut e, "ㄊㄚ ㄓㄢˋ").preedit, "他占");
    let s = decode_segment_learned(&f.capped, &syls("ㄊㄚ ㄓㄢˋ"), &f.lm, cfg, "他", End::Eos, core::BEAM_S1, None, true).unwrap();
    let (table, bigram) = (f.lm.eos3(cfg, Some("他"), "占"), f.lm.eos3(cfg, None, "占"));
    assert!((table - bigram).abs() > 1e-3, "premise: the table changes the closing term");
    close(s[0].0, f.lm.word3(cfg, None, "他", "他", -2.0) + f.lm.word3(cfg, Some("他"), "他", "占", -3.0) + table);
    close(e.total_score().unwrap(), s[0].0);
    // End::Next, between two fixed words: 他 | 他占 | 他 would put the stretch 他占 between fixed 他, so use ㄊㄚ ㄊㄚ ㄓㄢˋ ㄊㄚ with the ends fixed
    let mut e = engine(&f, Profile::Formal);
    // (typed around the picks, see fix_word: the second pick would freeze the stretch between)
    fix_word(&mut e, "", "ㄊㄚ", "他", "");
    e.key(Key::new(KeyKind::Home)).unwrap();
    type_syls(&mut e, "ㄊㄚ");
    fix_at(&mut e, 1, "他");
    assert_eq!(type_syls(&mut e, "ㄊㄚ ㄓㄢˋ").preedit, "他他占他");
    let next = End::Next { word: "他", lp: -2.0, delta: 0.0 };
    let s = decode_segment_learned(&f.capped, &syls("ㄊㄚ ㄓㄢˋ"), &f.lm, cfg, "他", next, core::BEAM_S1, None, true).unwrap();
    let (table, bigram) = (f.lm.word3(cfg, Some("他"), "占", "他", -2.0), f.lm.word3(cfg, None, "占", "他", -2.0));
    assert!((table - bigram).abs() > 1e-3, "premise: the table changes the closing term");
    close(s[0].0, f.lm.word3(cfg, None, "他", "他", -2.0) + f.lm.word3(cfg, Some("他"), "他", "占", -3.0) + table);
    let first = f.lm.word3(cfg, Some("<s>"), "<s>", "他", -2.0);
    // a fixed word at the end has no stretch after it: only `total_score` scores its closing term (u unknown, bigram)
    let last = f.lm.eos3(cfg, None, "他");
    close(e.total_score().unwrap(), first + s[0].0 + last);
}

/// Acceptance 6, learned words: the word the learner boosted is scored by P2 alone (the value without the table), the unboosted entry of the same
/// reading in the same segment by the table; `total_score` equals the top decode score. 他 then 占/佔: (<s>, 他) -> 占 3, 佔 1 are stored.
#[test]
fn learned_word_skips_the_table_and_its_neighbour_does_not() {
    let f = fx(true);
    let plain = lm_of(false, false, false, false);
    let mut e = engine(&f, Profile::Formal);
    e.set_learning(true);
    e.set_today(Some(20_000));
    e.set_prediction(false).unwrap(); // predict-down B.4: ↓ lists the lexicon only, so 佔 is a re-pick
    e.set_left_context("我們"); // outside the toy vocabulary: the first word starts from (<s>, <s>)
    let o = type_syls(&mut e, "ㄊㄚ ㄓㄢˋ");
    assert_eq!(o.preedit, "他占", "premise: unlearned, 占 is first");
    // teach 佔 after 他: choose it for the second syllable, commit
    let o = e.key(Key::new(KeyKind::Down)).unwrap();
    let at = o.candidates.iter().position(|c| c == "佔").expect("佔 is a candidate");
    e.pick(at).unwrap();
    e.key(Key::new(KeyKind::Enter)).unwrap();
    assert!(e.learner().records().iter().any(|r| r.word == "佔"), "premise: 佔 was learned");
    e.set_left_context("我們");
    let o = type_syls(&mut e, "ㄊㄚ ㄓㄢˋ");
    assert_eq!(o.preedit, "他佔", "premise: the learned word is on the best path");
    let today = 20_000;
    let learn = Learn { learner: e.learner(), before: "我們", after: None, today, eps_global: LEARN_EPS_GLOBAL };
    let cfg = f.lm.scoring(Profile::Formal);
    let nb = decode_segment_learned(&f.capped, &syls("ㄊㄚ ㄓㄢˋ"), &f.lm, cfg, "<s>", End::Eos, core::BEAM_S1, Some(&learn), true).unwrap();
    let path = |w: &str| nb.iter().find(|(_, ws)| ws.len() == 2 && ws[1].0 == w).map(|(s, ws)| (*s, ws[1].1)).unwrap();
    let ((s_learned, lp_learned), (s_plain, lp_plain)) = (path("佔"), path("占"));
    assert!(lp_learned > -3.0 && lp_plain == -3.0, "premise: only 佔 is boosted ({lp_learned}, {lp_plain})");
    let lam = cfg.lam;
    let first = f.lm.word3(cfg, Some("<s>"), "<s>", "他", -2.0);
    // the boosted 佔: P2 with the boosted pb, no side file, no table; its closing term (他, 佔) -> </s> is the table's
    let want_learned = first + plain.word(lam, "他", "佔", lp_learned) + f.lm.eos3(cfg, Some("他"), "佔");
    // the other entry: the table's (<s>, 他) -> 占
    let want_plain = first + f.lm.word3(cfg, Some("<s>"), "他", "占", lp_plain) + f.lm.eos3(cfg, Some("他"), "占");
    assert!((plain.word(lam, "他", "佔", lp_learned) - f.lm.word3(cfg, Some("<s>"), "他", "佔", lp_learned)).abs() > 1e-3, "premise: the table changes the term");
    close(s_learned, want_learned);
    close(s_plain, want_plain);
    close(e.total_score().unwrap(), nb[0].0);
    assert_eq!(nb[0].1[1].0, "佔");
}

/// An open English run inside a stretch (zhen-inline 1.3h): `refresh_lm` decodes the part on the left of the run to its end with `eos` and the part
/// on the right from (<s>, <s>), so `total_score` is the sum of those two decodes. 他占 | run | 他占 on ㄊㄚ ㄓㄢˋ ㄊㄚ ㄓㄢˋ with the cursor in the middle.
/// The premise is that one continuous decode scores differently (the table has (占, 他) -> 占 and the end of the left part is not the end of the sentence),
/// so a `total_score` that ignored the run would not equal the sum.
#[test]
fn english_run_inside_a_stretch_cuts_the_total_like_the_decoder() {
    let f = fx(true);
    let mut e = engine(&f, Profile::Formal);
    e.load_english(&dir("").join("english-tiny.txt")).unwrap();
    e.set_english(true).unwrap();
    type_syls(&mut e, "ㄊㄚ ㄓㄢˋ ㄊㄚ ㄓㄢˋ");
    for _ in 0..2 {
        e.key(Key::new(KeyKind::Left)).unwrap();
    }
    let o = e.key(Key::ch('x', core::engine::MOD_SHIFT)).unwrap();
    assert!(o.preedit.contains('X'), "premise: the run is open at the cursor ({})", o.preedit);
    assert_eq!(o.preedit.replace('X', ""), "他占他占", "premise: both parts decode as one word pair each");
    let cfg = f.lm.scoring(Profile::Formal);
    let part = |start: &str| decode_segment_learned(&f.capped, &syls("ㄊㄚ ㄓㄢˋ"), &f.lm, cfg, start, End::Eos, core::BEAM_S1, None, true).unwrap()[0].0;
    let continuous = decode_segment_learned(&f.capped, &syls("ㄊㄚ ㄓㄢˋ ㄊㄚ ㄓㄢˋ"), &f.lm, cfg, "<s>", End::Eos, core::BEAM_S1, None, true).unwrap()[0].0;
    let want = part("<s>") + part("<s>");
    assert!((want - continuous).abs() > 1e-3, "premise: the cut changes the score ({want} vs {continuous})");
    close(e.total_score().unwrap(), want);
    // Closing the run (Esc) decodes one stretch again, and the total is the continuous decode.
    e.key(Key::new(KeyKind::Esc)).unwrap();
    close(e.total_score().unwrap(), continuous);
}

/// Acceptance 6, chat: with and without the formal table (Kneser-Ney on in both) the chat decode output, `total_score`, the candidates and the prediction row
/// are bit for bit the same.
#[test]
fn chat_is_the_same_with_and_without_the_formal_table() {
    let (with, without) = (fx_of(true, true, false), fx_of(true, false, false));
    let run = |f: &Fx| {
        let mut e = engine(f, Profile::Chat);
        e.set_prediction(true).unwrap();
        e.set_left_context("起牀");
        let mut seen = Vec::new();
        for s in ["ㄊㄚ ㄓㄢˋ", "ㄓㄢˋ ㄊㄚ ㄓㄢˋ", "ㄊㄚ ㄑㄧˇ ㄔㄨㄤˊ"] {
            let o = type_syls(&mut e, s);
            seen.push((o.preedit.clone(), o.candidates.clone(), e.total_score().map(f64::to_bits)));
            e.reset(core::engine::ResetMode::Discard);
        }
        let o = e.key(Key::ch(Layout::Standard.key_of_symbol('ㄓ').unwrap(), 0)).unwrap();
        assert!(!o.candidates.is_empty(), "premise: a prediction row is shown");
        seen.push((o.preedit, o.candidates, None));
        let direct: Vec<_> = decode(&f.capped, &syls("ㄊㄚ ㄓㄢˋ"), &f.lm, Profile::Chat, core::BEAM_S1).unwrap().into_iter().map(|(s, w)| (s.to_bits(), w)).collect();
        (seen, direct)
    };
    assert_eq!(run(&with), run(&without));
    // the same for the fixed word case
    let fixed = |f: &Fx| {
        let mut e = engine(f, Profile::Chat);
        let o = fix_word(&mut e, "ㄓㄢˋ", "ㄊㄚ", "他", "ㄓㄢˋ");
        (o.preedit, e.total_score().map(f64::to_bits))
    };
    assert_eq!(fixed(&with), fixed(&without));
}

/// sj-lm-7-pr2.md §2 item 1 in the engine: the chat profile scores with `trigram-chat.sjt3`, Kneser-Ney on. The engine's `total_score` of a typed sentence is the
/// decode with the chat scoring (not the bigram's), the top decode's score changes with the table, and the formal engine is unchanged by a chat table.
#[test]
fn chat_engine_scores_with_the_chat_table() {
    let (with, without) = (fx_of(true, false, true), fx_of(true, false, false));
    let total = |f: &Fx, p: Profile, s: &str| {
        let mut e = engine(f, p);
        type_syls(&mut e, s);
        e.total_score().unwrap()
    };
    let mut changed = false;
    for s in ["ㄊㄚ ㄓㄢˋ", "ㄓㄢˋ ㄊㄚ ㄓㄢˋ"] {
        let cfg = with.lm.scoring(Profile::Chat);
        assert!(with.lm.uses_tri(cfg), "premise: chat scores with the table");
        let top = decode_segment_learned(&with.capped, &syls(s), &with.lm, cfg, "<s>", End::Eos, core::BEAM_S1, None, true).unwrap()[0].0;
        close(total(&with, Profile::Chat, s), top);
        changed |= (total(&with, Profile::Chat, s) - total(&without, Profile::Chat, s)).abs() > 1e-3;
        assert_eq!(total(&with, Profile::Formal, s).to_bits(), total(&without, Profile::Formal, s).to_bits(), "formal ignores the chat table: {s}");
    }
    assert!(changed, "premise: the chat table changes chat scores");
}

/// Acceptance 6, rule (d): the prediction row of the formal profile is the v0.4.6 one whether the table is loaded or not, scored with the
/// Kneser-Ney side file on and without the table. After 起牀 nothing is a listed successor, so the row is ordered by score alone; there Kneser-Ney
/// puts 占 before 他 (the premise below), and a table term (<s>, 起牀) -> 佔 would put 佔 first.
#[test]
fn formal_prediction_row_ignores_the_table_and_keeps_kneser_ney() {
    let (with, without) = (fx_of(true, true, false), fx_of(true, false, false));
    let row = |f: &Fx| {
        let mut e = engine(f, Profile::Formal);
        e.set_prediction(true).unwrap();
        e.set_left_context("起牀");
        e.key(Key::ch(Layout::Standard.key_of_symbol('ㄓ').unwrap(), 0)).unwrap().candidates
    };
    let (a, b) = (row(&with), row(&without));
    assert_eq!(a, b);
    // the reference: predict() with the side file, and without it (the premise: the two orders differ)
    let idx_of = |f: &Fx| {
        let units = units_of("ㄓ");
        predict(f.capped.predict_index(&f.lm), &f.lm, f.lm.scoring(Profile::Formal), history("起牀", &f.lm), &units, Mode::P, 9)
            .into_iter()
            .map(|x| x.0)
            .collect::<Vec<_>>()
    };
    let with_kn = idx_of(&without);
    let no_kn = {
        let lm = Arc::new(lm_of(false, false, false, false));
        let capped = Arc::new(CappedLexicon::new(Arc::new(Lexicon::parse_with(LEX, None).unwrap()), "", &lm, None).unwrap());
        let units = units_of("ㄓ");
        predict(capped.predict_index(&lm), &lm, lm.scoring(Profile::Formal), "起牀", &units, Mode::P, 9).into_iter().map(|x| x.0).collect::<Vec<_>>()
    };
    assert_ne!(with_kn, no_kn, "premise: Kneser-Ney changes the order");
    assert_eq!(a, with_kn);
    assert_eq!(idx_of(&with), with_kn);
}
