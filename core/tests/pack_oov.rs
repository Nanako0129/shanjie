//! docs/contracts/pack-oov.md section 2 item 5: an optional-pack word is unknown to the model (`CappedLexicon::model_id`) and keeps the
//! pack's score. The model is the Kneser-Ney toy of eval/golden/kn-tiny (他, 佔, 占, 起床, 起牀; bigram 他→占 kept; side file; classes),
//! whose vocabulary holds the pack words used here, with kept bigrams.
use core::engine::{Engine, Key, KeyKind, Layout};
use core::lm::{decode, decode_from, decode_segment_learned, history, CappedLexicon, End, Lm, Profile, PACK_OOV_TAG};
use core::Lexicon;
use std::path::{Path, PathBuf};
use std::sync::Arc;

fn dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../eval/golden/kn-tiny")
}
fn bytes(name: &str) -> Vec<u8> {
    std::fs::read(dir().join(name)).unwrap()
}
fn lm_with(side: bool) -> Lm {
    let mut lm = Lm::parse_with_classes(&bytes("bigram.sjlm"), &bytes("classes.sjc")).unwrap();
    if side {
        lm.add_kn(&bytes("kn.sjkn")).unwrap();
    }
    lm
}
fn close(a: f64, b: f64) {
    assert!((a - b).abs() < 1e-12, "{a} != {b}");
}
/// A word no vocabulary holds: what the model sees for an optional-pack word.
const UNKNOWN: &str = "\u{0}no-such-word";
/// 占 scores -0.05, above any corpus frequency, so the cap would bite.
const LEX: &str = "ㄓㄢˋ 占 -0.05\nㄓㄢˋ 佔 -3.5\nㄊㄚ 他 -2.0\nㄑㄧˇ-ㄔㄨㄤˊ 起床 -4.9\nㄓㄢˋ 站 -3.0\n";
/// Pack rows: reading, word, score, tag. The constructor reads the word and the tag: these rows are forced (`PACK_OOV_TAG`).
fn rows(words: &[&str]) -> String {
    words.iter().map(|w| format!("x\t{w}\t-1.0\t{PACK_OOV_TAG}\n")).collect()
}
/// Pack rows the builder leaves unforced (docs/contracts/pack-oov.md section 1b): capped as in main.
fn plain_rows(words: &[&str]) -> String {
    words.iter().map(|w| format!("x\t{w}\t-1.0\tacg\n")).collect()
}
fn capped(lex: &Arc<Lexicon>, lm: &Lm, pack: &[&str]) -> CappedLexicon {
    let text = rows(pack);
    CappedLexicon::with_packs(lex.clone(), &text, &text, lm, None).unwrap()
}
fn syls(s: &str) -> Vec<String> {
    s.split(' ').map(String::from).collect()
}
/// The n-best of `s` as (score, surface).
fn nbest(c: &CappedLexicon, lm: &Lm, s: &str, start: &str, end: End) -> Vec<(f64, String)> {
    let nb = decode_segment_learned(c, &syls(s), lm, Profile::Chat.lambda(), start, end, 64, None, true).unwrap();
    nb.into_iter().map(|(sc, ws)| (sc, ws.iter().map(|w| w.0).collect())).collect()
}
fn score_of(nb: &[(f64, String)], surface: &str) -> f64 {
    nb.iter().find(|x| x.1 == surface).map(|x| x.0).unwrap()
}

/// A pack word that IS in the vocabulary, with a kept bigram and a side file, scores as a word the model never saw: its term is
/// `word_by_id` with no id and the pack's own lp (no cap, no unseen penalty), its successor sees no context, and every other word
/// is bit for bit what it was.
#[test]
fn pack_word_term_is_the_unknown_word_term() {
    let lm = lm_with(true);
    let lex = Arc::new(Lexicon::parse_with(LEX, None).unwrap());
    let (plain, packed) = (CappedLexicon::new(lex.clone(), &rows(&["占", "站"]), &lm, None).unwrap(), capped(&lex, &lm, &["占", "站"]));
    let lam = Profile::Chat.lambda();
    let (a, b) = (nbest(&plain, &lm, "ㄓㄢˋ", "<s>", End::Eos), nbest(&packed, &lm, "ㄓㄢˋ", "<s>", End::Eos));
    // premises: the cap bites on 占 (it would be lower than the pack's -0.05) and the unseen word is penalised without the pack
    assert!(plain.best_lp(&syls("ㄓㄢˋ"), "占").unwrap() < -0.05, "premise: capped");
    assert_eq!(plain.best_lp(&syls("ㄓㄢˋ"), "站"), Some(-4.0), "premise: unseen penalty");
    assert_eq!(packed.best_lp(&syls("ㄓㄢˋ"), "占"), Some(-0.05), "not capped");
    assert_eq!(packed.best_lp(&syls("ㄓㄢˋ"), "站"), Some(-3.0), "no unseen penalty");
    // the term: the unknown word after <s>, then the sentence end after an unknown word
    let want = lm.word_by_id(lam, lm.context_of("<s>"), None, -0.05) + lm.eos(lam, UNKNOWN);
    close(score_of(&b, "占"), want);
    assert!((score_of(&a, "占") - want).abs() > 1e-3, "premise: the model's contribution is visible without the pack");
    // a word that is not in the pack is unchanged, bit for bit
    assert_eq!(score_of(&a, "佔").to_bits(), score_of(&b, "佔").to_bits());
    // the successor of a pack word has no context (ctx_of(None)); 他 after 占 has a kept bigram in the model
    let after = score_of(&nbest(&packed, &lm, "ㄓㄢˋ ㄊㄚ", "<s>", End::Eos), "占他");
    close(after, lm.word_by_id(lam, lm.context_of("<s>"), None, -0.05) + lm.word(lam, UNKNOWN, "他", -2.0) + lm.eos(lam, "他"));
}

/// No pack: `with_packs` with empty rows is `new`, and the pack set is empty so nothing is a pack word.
#[test]
fn without_a_pack_nothing_changes() {
    let lm = lm_with(true);
    let lex = Arc::new(Lexicon::parse_with(LEX, None).unwrap());
    let (a, b) = (CappedLexicon::new(lex.clone(), "", &lm, None).unwrap(), CappedLexicon::with_packs(lex.clone(), "", "", &lm, None).unwrap());
    for w in ["占", "佔", "他", "起床", "站"] {
        assert!(!b.is_pack(w));
        assert_eq!(b.model_id(&lm, w), lm.word_id(w));
    }
    for s in ["ㄊㄚ ㄓㄢˋ", "ㄑㄧˇ ㄔㄨㄤˊ"] {
        let (x, y) = (decode(&a, &syls(s), &lm, Profile::Chat, 64).unwrap(), decode(&b, &syls(s), &lm, Profile::Chat, 64).unwrap());
        assert_eq!(x.iter().map(|r| (r.0.to_bits(), r.1.clone())).collect::<Vec<_>>(), y.iter().map(|r| (r.0.to_bits(), r.1.clone())).collect::<Vec<_>>());
    }
}

fn type_syllable(e: &mut Engine, l: Layout, symbols: &str, tone: Option<char>) -> core::engine::Output {
    for c in symbols.chars() {
        e.key(Key::ch(l.key_of_symbol(c).unwrap(), 0)).unwrap();
    }
    match tone {
        Some(t) => e.key(Key::ch(l.key_of_tone(t).unwrap(), 0)).unwrap(),
        None => e.key(Key::new(KeyKind::Space)).unwrap(),
    }
}
fn pick_word(e: &mut Engine, word: &str) {
    let o = e.key(Key::new(KeyKind::Down)).unwrap();
    let at = o.candidates.iter().position(|c| c == word).unwrap_or_else(|| panic!("{word} is a candidate"));
    e.pick(at).unwrap();
}
fn engine(lm: &Arc<Lm>, lex: &Arc<Lexicon>, pack: &[&str], left: &str) -> Engine {
    let mut e = Engine::with_lexicon(lex.clone(), Layout::Standard);
    e.set_lm(lm.clone(), Arc::new(capped(lex, lm, pack)));
    e.set_prediction(false).unwrap();
    e.set_left_context(left);
    e
}

/// A pack word in each place a path word can stand: the history word of the left context, a fixed word at the start of a
/// segment (here: the word the segment follows), and a fixed word that closes the segment (`End::Next`). `Engine::total_score`
/// equals the decoder's first score plus the terms the decoder does not hold, each computed with the pack word as unknown.
#[test]
fn total_score_treats_a_pack_word_as_unknown_in_every_place() {
    let lm = Arc::new(lm_with(true));
    let lex = Arc::new(Lexicon::parse_with(LEX, None).unwrap());
    let l = Layout::Standard;
    let lam = Profile::Chat.lambda();
    let cap = capped(&lex, &lm, &["他"]);
    // (1) the left context ends with the pack word 他 (history keeps its string; the scoring sees an unknown word)
    let mut e = engine(&lm, &lex, &["他"], "他");
    type_syllable(&mut e, l, "ㄓㄢ", Some('ˋ'));
    assert_eq!(history("他", &lm), "他", "premise: history returns the pack word");
    let top = nbest(&cap, &lm, "ㄓㄢˋ", "他", End::Eos);
    close(e.total_score().unwrap(), top[0].0);
    let plain = nbest(&CappedLexicon::new(lex.clone(), "", &lm, None).unwrap(), &lm, "ㄓㄢˋ", "他", End::Eos);
    assert!((plain[0].0 - top[0].0).abs() > 1e-3, "premise: the model's bigram 他→占 changes the score without the pack");
    // (2) a fixed pack word at the start of the segment: 他 picked, then ㄓㄢˋ typed after it
    let mut e = engine(&lm, &lex, &["他"], "");
    type_syllable(&mut e, l, "ㄊㄚ", None);
    pick_word(&mut e, "他");
    type_syllable(&mut e, l, "ㄓㄢ", Some('ˋ'));
    let top = nbest(&cap, &lm, "ㄓㄢˋ", "他", End::Eos);
    let fixed_term = lm.word_by_id(lam, lm.context_of("<s>"), None, -2.0);
    close(e.total_score().unwrap(), fixed_term + top[0].0);
    // (3) a fixed pack word that closes the segment: 他 picked, ㄓㄢˋ typed before it
    let mut e = engine(&lm, &lex, &["他"], "");
    type_syllable(&mut e, l, "ㄊㄚ", None);
    pick_word(&mut e, "他");
    e.key(Key::new(KeyKind::Home)).unwrap();
    type_syllable(&mut e, l, "ㄓㄢ", Some('ˋ'));
    let next = End::Next { word: "他", lp: -2.0, delta: 0.0 };
    let top = nbest(&cap, &lm, "ㄓㄢˋ", "<s>", next);
    close(e.total_score().unwrap(), top[0].0 + lm.eos(lam, UNKNOWN));
}

/// The Python decoder's output (tools/gen_pack_oov.py) reproduced byte for byte, for three pack sets, both starts (`<s>` and the
/// history word of a left context that ends with a pack word), both profiles, with and without the side file.
#[test]
fn pack_oov_decode_matches_python_golden() {
    // The golden's f64 bits come from a Mac Python run; Linux's libm differs in the last bit of log10 (as for engine_digits.rs's goldens).
    // CI's core job runs on macOS, so this is checked there.
    if cfg!(target_os = "linux") {
        return;
    }
    let want = String::from_utf8(std::fs::read(Path::new(env!("CARGO_MANIFEST_DIR")).join("../eval/golden/pack-oov/decode.tsv")).unwrap()).unwrap();
    let golden = Path::new(env!("CARGO_MANIFEST_DIR")).join("../eval/golden/pack-oov");
    let lex = Arc::new(Lexicon::parse(&std::fs::read_to_string(golden.join("lexicon.txt")).unwrap()).unwrap());
    let sentences = ["ㄊㄚ ㄑㄧˇ ㄔㄨㄤˊ", "ㄊㄚ ㄓㄢˋ", "ㄑㄧˇ ㄔㄨㄤˊ", "ㄓㄢˋ ㄊㄚ", "ㄊㄚ ㄓㄢ", "ㄊㄚ ㄓㄢˋ ㄑㄧˇ ㄔㄨㄤˊ", "ㄊㄚ ㄕˋ ㄐㄧㄝˋ ㄒㄧㄢˋ"];
    // (name, forced rows, unforced rows, overlay-only words): tools/gen_pack_oov.py's PACKS, UNFORCED and OVERLAY_ONLY.
    let sets: [(&str, &[&str], &[&str], &[&str]); 5] =
        [("none", &[], &[], &[]), ("A", &["占", "起床"], &[], &[]), ("B", &["他"], &[], &[]), ("C", &[], &["世界線", "占"], &[]), ("D", &[], &[], &["世界線"])];
    let mut got = String::new();
    for (side, on) in [("plain", false), ("beta1", true)] {
        let lm = lm_with(on);
        for (pack, forced, unforced, overlay_only) in sets {
            let pack_text = rows(forced) + &plain_rows(unforced);
            let c = CappedLexicon::with_packs(lex.clone(), &(pack_text.clone() + &plain_rows(overlay_only)), &pack_text, &lm, None).unwrap();
            for start in ["<s>", history("占他", &lm)] {
                for (profile, name) in [(Profile::Chat, "chat"), (Profile::Formal, "formal")] {
                    for (i, s) in sentences.iter().enumerate() {
                        for (r, (sc, ws)) in decode_from(&c, &syls(s), &lm, profile, core::BEAM_S1, start, true).unwrap().iter().enumerate() {
                            got += &format!("{side}\t{pack}\t{start}\t{name}\t{}\t{}\t{}\t{sc:?}\n", i + 1, r + 1, ws.concat());
                        }
                    }
                }
            }
        }
    }
    assert_eq!(got, want);
}

/// A learned (boosted) pack word is scored without the Kneser-Ney term and as an unknown word, in the decoder and in `total_score`
/// alike (`Lm::word_learned_by_id`). Taught through the engine as in kn_tiny.rs: pick 佔 over 占 after 我們, Enter, type it again.
#[test]
fn total_score_equals_the_top_decode_with_a_learned_pack_word() {
    use core::lm::{Learn, LEARN_EPS_GLOBAL};
    let lm = Arc::new(lm_with(true));
    let lex = Arc::new(Lexicon::parse_with("ㄓㄢˋ 占 -3.0\nㄓㄢˋ 佔 -3.5\n", None).unwrap());
    let (l, left, today) = (Layout::Standard, "我們", 20_000);
    let mut e = engine(&lm, &lex, &["佔"], left);
    e.set_learning(true);
    e.set_today(Some(today));
    let zhan = |e: &mut Engine| {
        e.set_left_context(left);
        type_syllable(e, l, "ㄓㄢ", Some('ˋ'))
    };
    zhan(&mut e);
    pick_word(&mut e, "佔");
    e.key(Key::new(KeyKind::Enter)).unwrap();
    assert_eq!(zhan(&mut e).preedit, "佔", "premise: the learned pack word is on the best path");
    let cap = capped(&lex, &lm, &["佔"]);
    let learn = Learn { learner: e.learner(), before: left, after: None, today, eps_global: LEARN_EPS_GLOBAL };
    let nb = decode_segment_learned(&cap, &syls("ㄓㄢˋ"), &lm, Profile::Chat.lambda(), history(left, &lm), End::Eos, 64, Some(&learn), true).unwrap();
    assert!(nb[0].1[0].1 > -3.5, "premise: boosted");
    close(e.total_score().unwrap(), nb[0].0);
}

/// Prediction and the digits layer read `readings()`: with the pack on they see what they saw before the pack was exempt from the cap
/// (the pack text as plain overlay), while the decoder's `entries` keep the pack's uncapped score. Only rows of `pack_text` are exempt;
/// an overlay row that is not among them (another pack, `--extra-overlay`) stays capped for the decoder too.
#[test]
fn prediction_and_digits_see_the_capped_score_and_only_pack_rows_are_exempt() {
    let lm = lm_with(true);
    let lex = Arc::new(Lexicon::parse_with(LEX, None).unwrap());
    let plain = CappedLexicon::new(lex.clone(), &rows(&["占", "站"]), &lm, None).unwrap();
    let packed = CappedLexicon::with_packs(lex.clone(), &rows(&["占", "站"]), &rows(&["占"]), &lm, None).unwrap();
    assert_eq!(packed.readings(&lm).collect::<Vec<_>>(), plain.readings(&lm).collect::<Vec<_>>(), "readings() (prediction index, digits layer) is the capped list");
    assert_eq!(packed.best_lp(&syls("ㄓㄢˋ"), "占"), Some(-0.05), "decoder: the pack row keeps its score");
    assert!(plain.best_lp(&syls("ㄓㄢˋ"), "占").unwrap() < -0.05, "premise: capped without the exemption");
    assert_eq!(packed.best_lp(&syls("ㄓㄢˋ"), "站"), Some(-4.0), "decoder: an overlay row outside pack_text is capped (unseen penalty)");
    // `readings()` rebuilds the reading that holds the pack word: a word of the same reading that is not in the overlay stays uncapped.
    let plain = CappedLexicon::new(lex.clone(), &rows(&["占"]), &lm, None).unwrap();
    let packed = CappedLexicon::with_packs(lex.clone(), &rows(&["占"]), &rows(&["占"]), &lm, None).unwrap();
    assert_eq!(packed.readings(&lm).collect::<Vec<_>>(), plain.readings(&lm).collect::<Vec<_>>(), "a non-overlay word next to a pack word");
}

/// docs/contracts/pack-oov.md section 1b: a pack row the builder did not force (`acg`) is capped as in main. A word the model knows
/// keeps its model id and its capped score; a word the model never saw keeps the pack's score instead of losing the unseen penalty
/// (its id is unknown either way). `readings()` (prediction, digits) still sees main's capped list for both.
#[test]
fn unforced_pack_words_are_capped_as_in_main_except_the_unseen_penalty() {
    let lm = lm_with(true);
    let lex = Arc::new(Lexicon::parse_with(&format!("{LEX}ㄒㄧㄣ 鑫 -2.0\n"), None).unwrap());
    assert!(lm.count("占") > 0 && lm.count("鑫") == 0, "premise: 占 is known to the model, 鑫 is not");
    let overlay = plain_rows(&["占", "鑫"]);
    let plain = CappedLexicon::new(lex.clone(), &overlay, &lm, None).unwrap();
    let packed = CappedLexicon::with_packs(lex.clone(), &overlay, &overlay, &lm, None).unwrap();
    assert_eq!(packed.best_lp(&syls("ㄓㄢˋ"), "占"), plain.best_lp(&syls("ㄓㄢˋ"), "占"), "a known unforced word is capped as in main");
    assert_eq!(packed.model_id(&lm, "占"), lm.word_id("占"), "a known unforced word keeps its model id");
    assert!(packed.model_id(&lm, "占").is_some());
    assert_eq!(packed.best_lp(&syls("ㄒㄧㄣ"), "鑫"), Some(-2.0), "an unseen unforced pack word keeps the pack score");
    assert_eq!(plain.best_lp(&syls("ㄒㄧㄣ"), "鑫"), Some(-3.0), "premise: main takes the unseen penalty");
    assert_eq!(packed.readings(&lm).collect::<Vec<_>>(), plain.readings(&lm).collect::<Vec<_>>(), "readings() is main's capped list");
    // The same word tagged as forced is unknown to the model and uncapped.
    let forced = CappedLexicon::with_packs(lex.clone(), &overlay, &rows(&["占"]), &lm, None).unwrap();
    assert_eq!(forced.model_id(&lm, "占"), None);
    assert_eq!(forced.best_lp(&syls("ㄓㄢˋ"), "占"), Some(-0.05));
}
