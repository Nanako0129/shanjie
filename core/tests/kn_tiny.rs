//! Kneser-Ney side file on the toy model (docs/contracts/kn-core.md section 3, items 2 and 4).
//! The files under eval/golden/kn-tiny/ are written by tools/gen_kn_tiny.py; decode.tsv is the Python
//! reference decoder's output, compared here byte for byte. The hand values are the ones of
//! tools/test_kn_cont.py (same toy corpus), with the word classes of the generated classes.sjc.
use core::lm::{decode, CappedLexicon, Demote, Lm, LmError, Profile};
use core::Lexicon;
use std::path::{Path, PathBuf};
use std::sync::Arc;

fn dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../eval/golden/kn-tiny")
}
fn bytes(name: &str) -> Vec<u8> {
    std::fs::read(dir().join(name)).unwrap()
}
/// The toy model with a side file, or none (`None`), through `add_kn`.
fn lm_with(side: Option<&[u8]>, classes: bool) -> Result<Lm, LmError> {
    let model = bytes("bigram.sjlm");
    let mut lm = if classes { Lm::parse_with_classes(&model, &bytes("classes.sjc"))? } else { Lm::parse(&model)? };
    if let Some(s) = side {
        lm.add_kn(s)?;
    }
    Ok(lm)
}
fn close(a: f64, b: f64) {
    assert!((a - b).abs() < 1e-12, "{a} != {b}");
}
/// The toy file with `bytes` overwritten at `at`.
fn patched(at: usize, with: &[u8]) -> Vec<u8> {
    let mut b = bytes("kn.sjkn");
    b[at..at + with.len()].copy_from_slice(with);
    b
}

const D: f64 = 0.75;

/// beta = 1: the three branches of `prob_c`, each with a hand value (sum of N' = 7: 他 1, 佔 2, 占 2, 起床 2, 起牀 2).
#[test]
fn beta_one_three_branches_by_hand() {
    let lm = lm_with(Some(&bytes("kn.sjkn")), true).unwrap();
    assert_eq!(lm.kn_tag(Profile::Chat), Some(format!("+kn:{}:θ1:β1", &core::eval::sha256_hex(&bytes("kn.sjkn"))[..8])));
    let pb = 0.123; // ignored: beta = 1 replaces the backoff value of every vocabulary word
    // 他 has entries 佔 2, 占 5, 起床 4 (total 11).
    let back_ta = 1.0 - ((2.0 - D) + (5.0 - D) + (4.0 - D)) / 11.0;
    // kept bigram (他, 占): (5 - D) / 11 + back * N'(占) / sum
    close(lm.prob("他", "占", pb), (5.0 - D) / 11.0 + back_ta * (2.0 / 7.0));
    // class term (佔, 起床): 佔 has no context entries (back 1), classes 1 and 2, mu 0.5, Pc[1][2] 0.2, emit(起床) 0.5
    close(lm.prob("佔", "起床", pb), 0.5 * (2.0 / 7.0) + 0.5 * 0.2 * 0.5);
    // general backoff (<s>, 占): 占 has no class; <s> has entries 起床 8, 他 15 (total 23)
    let back_s = 1.0 - ((8.0 - D) + (15.0 - D)) / 23.0;
    close(lm.prob("<s>", "占", pb), back_s * (2.0 / 7.0));
    // no context at all and no class: back 1
    close(lm.prob("占", "他", pb), 1.0 / 7.0);
}

/// The formal profile scores with FORMAL_KN_BETA 0.75 whatever the side file records; chat keeps the file's β (docs/contracts/sj-lm-7-pr2.md §2 item 1).
/// The word 占 after 他 has no bigram entry and no class, so its term is P = β / 7 + (1 - β) * pb (hand values below, λ 0.5 chat and 0.7 formal). The summary tags
/// show the β each profile used. With the file at β 0.75 the formal term is the file-β one bit for bit; with the file at β 1 it is not the chat one.
#[test]
fn formal_takes_beta_075_and_chat_the_files() {
    let lp = -3.0;
    let pb = 10f64.powf(lp);
    let (b1, b075) = (lm_with(Some(&bytes("kn.sjkn")), true).unwrap(), lm_with(Some(&bytes("kn-beta075.sjkn")), true).unwrap());
    let term = |lm: &Lm, p: Profile| lm.word3(lm.scoring(p), None, "占", "他", lp);
    close(term(&b1, Profile::Chat), 0.5 * (1.0f64 / 7.0).log10() + 0.5 * lp);
    close(term(&b1, Profile::Formal), 0.7 * (0.75 / 7.0 + 0.25 * pb).log10() + 0.3 * lp);
    close(term(&b075, Profile::Chat), 0.5 * (0.75 / 7.0 + 0.25 * pb).log10() + 0.5 * lp);
    assert_eq!(term(&b075, Profile::Formal).to_bits(), term(&b1, Profile::Formal).to_bits(), "formal ignores the file's β");
    let sha8 = |f: &str| core::eval::sha256_hex(&bytes(f))[..8].to_string();
    assert_eq!(b1.kn_tag(Profile::Chat), Some(format!("+kn:{}:θ1:β1", sha8("kn.sjkn"))));
    assert_eq!(b1.kn_tag(Profile::Formal), Some(format!("+kn:{}:θ1:β0.75", sha8("kn.sjkn"))));
    assert_eq!(b075.kn_tag(Profile::Chat), Some(format!("+kn:{}:θ1:β0.75", sha8("kn-beta075.sjkn"))));
    // no side file: no tag, and the formal term is the plain bigram one
    let plain = lm_with(None, true).unwrap();
    assert_eq!((plain.kn_tag(Profile::Formal), plain.kn_tag(Profile::Chat)), (None, None));
    close(term(&plain, Profile::Formal), 0.7 * pb.log10() + 0.3 * lp);
}

#[test]
fn beta_between_mixes_with_the_lexicon_score() {
    let lm = lm_with(Some(&bytes("kn-beta075.sjkn")), true).unwrap();
    close(lm.prob("占", "他", 0.1), 0.75 * (1.0 / 7.0) + 0.25 * 0.1);
}

/// Sentence end (id 1) and words outside the vocabulary keep their backoff value, bit for bit.
#[test]
fn sentence_end_and_unknown_words_are_unchanged() {
    let (plain, kn) = (lm_with(None, true).unwrap(), lm_with(Some(&bytes("kn.sjkn")), true).unwrap());
    for v in ["<s>", "他", "佔", "占", "起床", "起牀", "沒見過"] {
        for lam in [0.5, 0.7] {
            assert_eq!(plain.eos(lam, v), kn.eos(lam, v), "eos after {v}");
        }
        assert_eq!(plain.prob(v, "</s>", 0.4), kn.prob(v, "</s>", 0.4));
        assert_eq!(plain.prob(v, "沒見過", 0.1), kn.prob(v, "沒見過", 0.1));
    }
}

/// beta = 0 is bit-identical to no side file (both with and without the class term).
#[test]
fn beta_zero_is_bit_identical_to_no_side_file() {
    for classes in [true, false] {
        let plain = lm_with(None, classes).unwrap();
        let zero = lm_with(Some(&patched(48, &0.0f64.to_le_bytes())), classes).unwrap();
        for v in ["<s>", "他", "佔", "占", "起床", "起牀", "沒見過"] {
            for w in ["他", "佔", "占", "起床", "起牀", "</s>", "沒見過"] {
                for pb in [0.1, 1e-4, 3e-7] {
                    assert_eq!(plain.prob(v, w, pb), zero.prob(v, w, pb), "{v} {w}");
                }
                for lp in [-1.5, -4.9, -7.25] {
                    assert_eq!(plain.word(0.5, v, w, lp), zero.word(0.5, v, w, lp));
                }
            }
        }
    }
}

#[test]
fn bad_side_files_are_format_errors() {
    let good = bytes("kn.sjkn");
    let model = bytes("bigram.sjlm");
    assert!(lm_with(Some(&good), true).is_ok());
    let hex_bytes = |h: String| (0..32).map(|i| u8::from_str_radix(&h[2 * i..2 * i + 2], 16).unwrap()).collect::<Vec<u8>>();
    let flip = |at: usize| {
        let mut b = good.clone();
        b[at] ^= 1;
        b
    };
    let cases: Vec<(&str, Vec<u8>)> = vec![
        ("magic", flip(7)),
        ("old magic", [b"SJKN0001".as_slice(), &good[8..]].concat()),
        ("V", patched(8, &99u32.to_le_bytes())),
        ("model hash", flip(12)),
        ("beta above 1", patched(48, &1.5f64.to_le_bytes())),
        ("beta below 0", patched(48, &(-0.1f64).to_le_bytes())),
        ("beta NaN", patched(48, &f64::NAN.to_le_bytes())),
        ("zero total", patched(56, &0u64.to_le_bytes())),
        ("total below the largest N'", patched(56, &1u64.to_le_bytes())), // largest N' is 2
        ("total above the sum of N'", patched(56, &10u64.to_le_bytes())), // N' of 他, 佔, 占, 起床, 起牀 is 1 + 2 + 2 + 2 + 2 = 9
        ("short", good[..good.len() - 4].to_vec()),
        ("trailing byte", [good.as_slice(), &[0]].concat()),
        ("header only", good[..64].to_vec()),
        ("empty", vec![]),
    ];
    for (name, b) in &cases {
        assert_eq!(lm_with(Some(b), true).err(), Some(LmError::KnSide), "{name}");
    }
    assert!(lm_with(Some(&patched(56, &9u64.to_le_bytes())), true).is_ok(), "the sum of all N' is the largest total that passes");
    // A side file built for model B (same V, one other field) is refused by an Lm parsed from model A, and accepted for B.
    let mut model_b = model.clone();
    model_b[20] ^= 1; // eos_total
    let side_b = patched(12, &hex_bytes(core::eval::sha256_hex(&model_b)));
    assert_eq!(Lm::parse(&model).unwrap().add_kn(&side_b), Err(LmError::KnSide), "another model with the same V");
    assert!(Lm::parse(&model_b).unwrap().add_kn(&side_b).is_ok());
}

/// Lm::load: a kn.sjkn beside the model is read, a missing one is off, a bad one is an error; load_with(kn: false) ignores it.
#[test]
fn load_reads_the_side_file_beside_the_model() {
    let tmp = std::env::temp_dir().join(format!("shanjie-kn-tiny-{}", std::process::id()));
    std::fs::create_dir_all(&tmp).unwrap();
    for f in ["bigram.sjlm", "classes.sjc"] {
        std::fs::write(tmp.join(f), bytes(f)).unwrap();
    }
    let m = tmp.join("bigram.sjlm");
    assert!(Lm::load(&m).unwrap().kn_tag(Profile::Chat).is_none(), "no kn.sjkn: off");
    std::fs::write(tmp.join("kn.sjkn"), bytes("kn.sjkn")).unwrap();
    let on = Lm::load(&m).unwrap();
    assert!(on.kn_tag(Profile::Chat).unwrap().starts_with("+kn:") && Lm::load_without_classes(&m).unwrap().kn_tag(Profile::Chat).is_some());
    assert!(Lm::load_with(&m, true, false).unwrap().kn_tag(Profile::Chat).is_none(), "--no-kn");
    assert_eq!(on.prob("占", "他", 0.1), lm_with(Some(&bytes("kn.sjkn")), true).unwrap().prob("占", "他", 0.1));
    std::fs::remove_file(tmp.join("kn.sjkn")).unwrap();
    std::os::unix::fs::symlink(tmp.join("missing.sjkn"), tmp.join("kn.sjkn")).unwrap();
    assert_eq!(Lm::load(&m).err(), Some(LmError::KnSide), "a dangling kn.sjkn symlink is not 'absent'");
    assert!(Lm::load_with(&m, true, false).is_ok());
    std::fs::remove_file(tmp.join("kn.sjkn")).unwrap();
    std::fs::write(tmp.join("kn.sjkn"), patched(48, &2.0f64.to_le_bytes())).unwrap();
    assert_eq!(Lm::load(&m).err(), Some(LmError::KnSide), "a bad side file is never ignored");
    assert!(Lm::load_with(&m, true, false).is_ok(), "--no-kn does not read it");
    std::fs::remove_dir_all(tmp).unwrap();
}

/// The Python decoder's output (tools/gen_kn_tiny.py) reproduced byte for byte: top candidates and scores, both profiles,
/// without a side file and with beta 1 and 0.75, class term on.
#[test]
fn decode_matches_python_byte_for_byte() {
    let want = String::from_utf8(bytes("decode.tsv")).unwrap();
    let lex = Arc::new(Lexicon::parse(&String::from_utf8(bytes("lexicon.txt")).unwrap()).unwrap());
    let sentences = ["ㄊㄚ ㄑㄧˇ ㄔㄨㄤˊ", "ㄊㄚ ㄓㄢˋ", "ㄑㄧˇ ㄔㄨㄤˊ", "ㄓㄢˋ ㄊㄚ", "ㄊㄚ ㄓㄢ", "ㄕˋ ㄐㄧㄝˋ ㄒㄧㄢˋ ㄊㄚ", "ㄊㄚ ㄓㄢˋ ㄑㄧˇ ㄔㄨㄤˊ"];
    let (b1, b075) = (bytes("kn.sjkn"), bytes("kn-beta075.sjkn"));
    let mut got = String::new();
    for (config, side) in [("plain", None), ("beta1", Some(&b1)), ("beta075", Some(&b075))] {
        let lm = lm_with(side.map(|b| b.as_slice()), true).unwrap();
        let capped = CappedLexicon::new(lex.clone(), "", &lm, Some(&Demote::parse("").unwrap())).unwrap();
        for (profile, name) in [(Profile::Chat, "chat"), (Profile::Formal, "formal")] {
            for (i, s) in sentences.iter().enumerate() {
                let syls: Vec<String> = s.split(' ').map(String::from).collect();
                for (r, (sc, ws)) in decode(&capped, &syls, &lm, profile, core::BEAM_S1).unwrap().iter().enumerate() {
                    got += &format!("{config}\t{name}\t{}\t{}\t{}\t{sc:?}\n", i + 1, r + 1, ws.concat());
                }
            }
        }
    }
    assert_eq!(got, want);
}

/// model-v6 section 2.8 rule 1 (acceptance 2): in one decode, one segment and one context, the reading ㄓㄢˋ has two vocabulary
/// entries; only 佔 has a learned record (boosted), 占 has none. The boosted entry's word term is the one without the side file
/// (the boosted pb is kept), the other one's is the Kneser-Ney one. Each one-word path's score is word term + eos.
#[test]
fn learned_entry_skips_kneser_ney_and_its_neighbour_does_not() {
    use core::learn::{context_key, Learner};
    use core::lm::{decode_segment_learned, End, Learn, LEARN_EPS_GLOBAL};
    let (on, off) = (lm_with(Some(&bytes("kn.sjkn")), true).unwrap(), lm_with(None, true).unwrap());
    let lex = Arc::new(Lexicon::parse_with("ㄓㄢˋ 占 -3.0\nㄓㄢˋ 佔 -3.5\n", None).unwrap());
    let capped = CappedLexicon::new(lex, "", &on, None).unwrap();
    let reading = vec!["ㄓㄢˋ".to_string()];
    let (before, today) = ("我們", 20_000);
    let mut learner = Learner::from_records(Vec::new());
    assert!(!learner.teach(&context_key(before), &reading, "佔", "占", today).is_empty(), "premise: a context record for 佔");
    let learn = Learn { learner: &learner, before, after: None, today, eps_global: LEARN_EPS_GLOBAL };
    let lam = Profile::Chat.lambda();
    let nb = decode_segment_learned(&capped, &reading, &on, lam, "<s>", End::Eos, 64, Some(&learn), true).unwrap();
    let path = |w: &str| nb.iter().find(|(_, ws)| ws.len() == 1 && ws[0].0 == w).map(|(s, ws)| (*s, ws[0].1)).unwrap();
    let ((s_learned, lp_learned), (s_plain, lp_plain)) = (path("佔"), path("占"));
    assert!(lp_learned > -3.5 && lp_plain == -3.0, "premise: only 佔 is boosted ({lp_learned}, {lp_plain})");
    let want_learned = off.word(lam, "<s>", "佔", lp_learned) + on.eos(lam, "佔");
    let want_plain = on.word(lam, "<s>", "占", lp_plain) + on.eos(lam, "占");
    assert!((on.word(lam, "<s>", "佔", lp_learned) - off.word(lam, "<s>", "佔", lp_learned)).abs() > 1e-3, "premise: KN changes the term");
    assert!((on.word(lam, "<s>", "占", lp_plain) - off.word(lam, "<s>", "占", lp_plain)).abs() > 1e-3, "premise: KN changes the term");
    close(s_learned, want_learned);
    close(s_plain, want_plain);
    close(on.word_learned(lam, "<s>", "佔", lp_learned), off.word(lam, "<s>", "佔", lp_learned));
}

/// model-v6 section 2.8 acceptance 2b: with the side file on and a learned (boosted) word on the best path, `Engine::total_score`
/// still equals the score of the decoder's first path (s3a), because it applies the same exception. The word is taught through the
/// engine (pick 佔 over 占 after the context 我們, Enter), then typed again: the decode shows it with a boosted lp.
#[test]
fn total_score_equals_the_top_decode_with_a_learned_word_and_kn_on() {
    use core::engine::{Engine, Key, KeyKind, Layout};
    use core::lm::{decode_segment_learned, history, End, Learn, LEARN_EPS_GLOBAL};
    let on = Arc::new(lm_with(Some(&bytes("kn.sjkn")), true).unwrap());
    let lex = Arc::new(Lexicon::parse_with("ㄓㄢˋ 占 -3.0\nㄓㄢˋ 佔 -3.5\n", None).unwrap());
    let capped = Arc::new(CappedLexicon::new(lex.clone(), "", &on, None).unwrap());
    let (left, today, l) = ("我們", 20_000, Layout::Standard);
    let mut e = Engine::with_lexicon(lex, l);
    e.set_lm(on.clone(), capped.clone());
    e.set_learning(true);
    e.set_today(Some(today));
    e.set_prediction(false).unwrap(); // predict-down B.4: ↓ lists the lexicon only, so 佔 is a re-pick (model-v6 §2.8)
    let type_zhan = |e: &mut Engine| {
        e.set_left_context(left);
        e.key(Key::ch(l.key_of_symbol('ㄓ').unwrap(), 0)).unwrap();
        e.key(Key::ch(l.key_of_symbol('ㄢ').unwrap(), 0)).unwrap();
        e.key(Key::ch(l.key_of_tone('ˋ').unwrap(), 0)).unwrap()
    };
    assert_eq!(type_zhan(&mut e).preedit, "占", "premise: unlearned, 占 is first");
    let o = e.key(Key::new(KeyKind::Down)).unwrap();
    let at = o.candidates.iter().position(|c| c == "佔").expect("佔 is a candidate");
    e.pick(at).unwrap();
    e.key(Key::new(KeyKind::Enter)).unwrap();
    assert!(e.learner().records().iter().any(|r| r.word == "佔"), "premise: 佔 was learned");
    assert_eq!(type_zhan(&mut e).preedit, "佔", "premise: the learned word is on the best path");
    let learn = Learn { learner: e.learner(), before: left, after: None, today, eps_global: LEARN_EPS_GLOBAL };
    let lam = Profile::Chat.lambda();
    let reading = vec!["ㄓㄢˋ".to_string()];
    let nb = decode_segment_learned(&capped, &reading, &on, lam, history(left, &on), End::Eos, 64, Some(&learn), true).unwrap();
    assert_eq!(nb[0].1[0].0, "佔");
    assert!(nb[0].1[0].1 > -3.5, "premise: boosted");
    close(e.total_score().unwrap(), nb[0].0);
}
