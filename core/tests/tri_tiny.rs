//! Trigram level on the toy model (docs/contracts/trigram-core.md, slice A, acceptance 2, 3 and 6).
//! The toy bigram model, classes.sjc and kn.sjkn are eval/golden/kn-tiny/'s; the table, lexicon and decode.tsv are
//! eval/golden/tri-tiny/'s (tools/gen_tri_tiny.py; decode.tsv is the Python reference decoder's output, compared byte for byte).
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
/// The toy model with the pieces asked for: word classes, the Kneser-Ney side file, the trigram table.
fn lm_of(classes: bool, kn: bool, tri: bool) -> Lm {
    let model = bytes("kn-tiny", "bigram.sjlm");
    let mut lm = if classes { Lm::parse_with_classes(&model, &bytes("kn-tiny", "classes.sjc")).unwrap() } else { Lm::parse(&model).unwrap() };
    if kn {
        lm.add_kn(&bytes("kn-tiny", "kn.sjkn")).unwrap();
    }
    if tri {
        lm.add_tri(bytes("tri-tiny", "trigram.sjt3")).unwrap();
    }
    lm
}
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

/// Every start word and sentence: formal with the table (Kneser-Ney off) is decode.tsv. It also holds when the side file is loaded as well
/// (the profile turns it off), and a surface with two segmentations that differ in the previous word (ㄊㄚ ㄓㄢˋ ㄊㄚ, 他|占|他 and 他占|他) keeps
/// both until the next word, which is what the recombination key buys: without the previous word in the key the top score differs.
#[test]
fn formal_decode_matches_python_byte_for_byte() {
    let want = String::from_utf8(bytes("tri-tiny", "decode.tsv")).unwrap();
    let lexicon = bytes("tri-tiny", "lexicon.txt");
    for kn in [false, true] {
        let lm = lm_of(true, kn, true);
        let capped = capped_of(&lm, &lexicon);
        let mut got = String::new();
        for start in STARTS {
            for (i, s) in SENTENCES.iter().enumerate() {
                for (r, (sc, ws)) in decode_from(&capped, &syls(s), &lm, Profile::Formal, core::BEAM_S1, start, true).unwrap().iter().enumerate() {
                    got += &format!("{start}\t{}\t{}\t{}\t{sc:?}\n", i + 1, r + 1, ws.concat());
                }
            }
        }
        assert_eq!(got, want, "kn side file loaded: {kn}");
    }
}

/// Premise for the golden: the table changes formal scores (so equality with the Python output means something), and chat does not use it.
#[test]
fn chat_ignores_the_table_and_formal_does_not() {
    let lexicon = bytes("tri-tiny", "lexicon.txt");
    let (with, without) = (lm_of(true, true, true), lm_of(true, true, false));
    let (cw, cwo) = (capped_of(&with, &lexicon), capped_of(&without, &lexicon));
    let mut formal_differs = false;
    for start in STARTS {
        for s in SENTENCES {
            let run = |lm: &Lm, c: &CappedLexicon, p| decode_from(c, &syls(s), lm, p, core::BEAM_S1, start, true).unwrap();
            let bits = |r: Vec<(f64, Vec<String>)>| r.into_iter().map(|(sc, ws)| (sc.to_bits(), ws)).collect::<Vec<_>>();
            assert_eq!(bits(run(&with, &cw, Profile::Chat)), bits(run(&without, &cwo, Profile::Chat)), "chat {start} {s}");
            formal_differs |= bits(run(&with, &cw, Profile::Formal)) != bits(run(&without, &cwo, Profile::Formal));
        }
    }
    assert!(formal_differs);
}

// ---------- Lm::load ----------

fn table_bytes() -> Vec<u8> {
    bytes("tri-tiny", "trigram.sjt3")
}

/// A trigram.sjt3 beside the model is read, none is off, a bad one (or a dangling symlink) is an error, `load_opts(.., tri: false)` ignores it.
#[test]
fn load_reads_the_table_beside_the_model() {
    let tmp = std::env::temp_dir().join(format!("shanjie-tri-tiny-{}", std::process::id()));
    std::fs::create_dir_all(&tmp).unwrap();
    for f in ["bigram.sjlm", "classes.sjc"] {
        std::fs::write(tmp.join(f), bytes("kn-tiny", f)).unwrap();
    }
    let m = tmp.join("bigram.sjlm");
    assert!(Lm::load(&m).unwrap().tri_tag().is_none(), "no trigram.sjt3: off");
    std::fs::write(tmp.join("trigram.sjt3"), table_bytes()).unwrap();
    let on = Lm::load(&m).unwrap();
    let want = format!("+tri:{}", &core::eval::sha256_hex(&table_bytes())[..8]);
    assert_eq!(on.tri_tag(), Some(want.as_str()), "the tag is the table file's sha8");
    assert!(on.scoring(Profile::Formal).tri);
    assert!(Lm::load_opts(&m, true, true, false).unwrap().tri_tag().is_none(), "--no-tri");
    assert!(Lm::load_with(&m, true, true).unwrap().tri_tag().is_some());
    std::fs::remove_file(tmp.join("trigram.sjt3")).unwrap();
    std::os::unix::fs::symlink(tmp.join("missing.sjt3"), tmp.join("trigram.sjt3")).unwrap();
    assert_eq!(Lm::load(&m).err(), Some(LmError::TriTable), "a dangling trigram.sjt3 symlink is not 'absent'");
    assert!(Lm::load_opts(&m, true, true, false).is_ok());
    std::fs::remove_file(tmp.join("trigram.sjt3")).unwrap();
    let mut bad = table_bytes();
    bad[0] ^= 1;
    std::fs::write(tmp.join("trigram.sjt3"), bad).unwrap();
    assert_eq!(Lm::load(&m).err(), Some(LmError::TriTable), "a bad table is never ignored");
    assert!(Lm::load_opts(&m, true, true, false).is_ok(), "--no-tri does not read it");
    std::fs::remove_dir_all(tmp).unwrap();
}

// ---------- the engine ----------

const LEX: &str = "ㄊㄚ 他 -2.0\nㄓㄢ 他 -2.9\nㄓㄢˋ 占 -3.0\nㄓㄢˋ 佔 -3.5\n";

struct Fx {
    lex: Arc<Lexicon>,
    lm: Arc<Lm>,
    capped: Arc<CappedLexicon>,
}
/// The class-less toy model (hand values are plain bigram arithmetic) with the Kneser-Ney side file, and the table or not.
fn fx(tri: bool) -> Fx {
    let lm = Arc::new(lm_of(false, true, tri));
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
/// Type `s`, put the cursor after syllable `end`, open the candidates and choose `word` (as engine_lm.rs does).
fn fix_word(e: &mut Engine, s: &str, end: usize, word: &str) -> Output {
    type_syls(e, s);
    for _ in 0..s.split(' ').count() - end {
        e.key(Key::new(KeyKind::Left)).unwrap();
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
/// first word is bigram only, and its closing term is scored from (the start word, the last word). `total_score` is the hand value and the sum of
/// the two stretch decodes; the premises at the end show that a known u after the fixed word, or no u in the closing term, would score differently.
#[test]
fn fixed_word_in_the_middle_hand_values_and_total_score() {
    let f = fx(true);
    let plain = lm_of(false, false, false); // P2 without Kneser-Ney
    let mut e = engine(&f, Profile::Formal);
    let o = fix_word(&mut e, "ㄓㄢˋ ㄊㄚ ㄓㄢˋ", 2, "他");
    assert_eq!(o.preedit, "占他占");
    let (lam, d) = (Profile::Formal.lambda(), 0.75);
    let pb = |lp: f64| 10f64.powf(lp);
    // (a) the first word, (<s>, <s>) -> 占: the context is stored, the entry is not
    let back3 = 1.0 - ((12.0 - d) + (6.0 - d)) / 20.0;
    let t1 = lam * (back3 * plain.prob("<s>", "占", pb(-3.0))).log10() + (1.0 - lam) * -3.0;
    // (b) the transition into the fixed word, (<s>, 占) -> 他 5 of 6 is kept
    let back3 = 1.0 - (5.0 - d) / 6.0;
    let t2 = lam * ((5.0 - d) / 6.0 + back3 * plain.prob("占", "他", pb(-2.0))).log10() + (1.0 - lam) * -2.0;
    // (a) after the fixed word: (unknown, 他) -> 占 is the bigram, although (占, 他) -> 占 is stored (9 of 10)
    let t3 = plain.word(lam, "他", "占", -3.0);
    // (b) the closing term, (他, 占) -> </s> is kept: the start word 他 is the second last word of a one-word stretch
    let back3 = 1.0 - (2.0 - d) / 9.0;
    let t4 = lam * ((2.0 - d) / 9.0 + back3 * plain.prob("占", "</s>", 14.0 / 64.0)).log10();
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
    assert!((f.lm.eos3(cfg, None, "占") - t4).abs() > 1e-3, "premise: the closing term depends on u");
}

/// Acceptance 6, learned words: the word the learner boosted is scored by P2 alone (the value without the table), the unboosted entry of the same
/// reading in the same segment by the table; `total_score` equals the top decode score. 他 then 占/佔: (<s>, 他) -> 占 3, 佔 1 are stored.
#[test]
fn learned_word_skips_the_table_and_its_neighbour_does_not() {
    let f = fx(true);
    let plain = lm_of(false, false, false);
    let mut e = engine(&f, Profile::Formal);
    e.set_learning(true);
    e.set_today(Some(20_000));
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

/// Acceptance 6, chat: with and without the table the decode output, `total_score`, the candidates and the prediction row are bit for bit the same.
#[test]
fn chat_is_the_same_with_and_without_the_table() {
    let (with, without) = (fx(true), fx(false));
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
        let o = fix_word(&mut e, "ㄓㄢˋ ㄊㄚ ㄓㄢˋ", 2, "他");
        (o.preedit, e.total_score().map(f64::to_bits))
    };
    assert_eq!(fixed(&with), fixed(&without));
}

/// Acceptance 6, rule (d): the prediction row of the formal profile is the v0.4.6 one whether the table is loaded or not, scored with the
/// Kneser-Ney side file on and without the table. After 起牀 nothing is a listed successor, so the row is ordered by score alone; there Kneser-Ney
/// puts 占 before 他 (the premise below), and a table term (<s>, 起牀) -> 佔 would put 佔 first.
#[test]
fn formal_prediction_row_ignores_the_table_and_keeps_kneser_ney() {
    let (with, without) = (fx(true), fx(false));
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
        predict(f.capped.predict_index(&f.lm), &f.lm, Profile::Formal.lambda(), history("起牀", &f.lm), &units, Mode::P, 9)
            .into_iter()
            .map(|x| x.0)
            .collect::<Vec<_>>()
    };
    let with_kn = idx_of(&without);
    let no_kn = {
        let lm = Arc::new(lm_of(false, false, false));
        let capped = Arc::new(CappedLexicon::new(Arc::new(Lexicon::parse_with(LEX, None).unwrap()), "", &lm, None).unwrap());
        let units = units_of("ㄓ");
        predict(capped.predict_index(&lm), &lm, Profile::Formal.lambda(), "起牀", &units, Mode::P, 9).into_iter().map(|x| x.0).collect::<Vec<_>>()
    };
    assert_ne!(with_kn, no_kn, "premise: Kneser-Ney changes the order");
    assert_eq!(a, with_kn);
    assert_eq!(idx_of(&with), with_kn);
}
