//! Fuzzy input (docs/contracts/fuzzy-input.md section 3.4): the engine with the real lexicon and the bigram model. Needs
//! data/lm/bigram.sjlm like engine_lm.rs and fails loudly without it (never skips). The tables themselves are tested in
//! core/src/fuzzy.rs; the ABI codes in core/src/ffi.rs and core/tests/c/abi_smoke.c.
use core::engine::*;
use core::eval::{parse_rows, usable};
use core::fuzzy::{FUZZY_ADJACENT, FUZZY_ALL};
use core::lm::{decode_from, decode_from_fuzzy, CappedLexicon, Lm, Profile};
use core::{Lexicon, Syls, BEAM_S1};
use std::path::PathBuf;
use std::sync::{Arc, OnceLock};

#[path = "common/toy.rs"]
mod toy;
use toy::{keys_of, root, type_syls_in};

fn lm_path() -> PathBuf {
    let p = root().join("data/lm/bigram.sjlm");
    assert!(root().join("data/lm/classes.sjc").exists(), "data/lm/classes.sjc is missing: download it with `gh release download classes-v3 -R Nanako0129/shanjie -p classes.sjc -D data/lm` (or build it with tools/build_classes.py)");
    assert!(p.exists(), "data/lm/bigram.sjlm is missing: download it with `gh release download model-v5 -R Nanako0129/shanjie -p bigram.sjlm -D data/lm`");
    p
}

struct Shared {
    lex: Arc<Lexicon>,
    lm: Arc<Lm>,
    capped: Arc<CappedLexicon>,
}
/// Loaded once per test binary; engines get the same objects through `set_lm`.
fn shared() -> &'static Shared {
    static S: OnceLock<Shared> = OnceLock::new();
    S.get_or_init(|| {
        let lex = load_lexicon(&root().join("data/lexicon")).unwrap();
        let lm = Lm::load(&lm_path()).unwrap();
        let overlay = std::fs::read_to_string(root().join("data/lexicon/overlay-add.tsv")).unwrap();
        let capped = Arc::new(CappedLexicon::new(lex.clone(), &overlay, &lm, None).unwrap());
        Shared { lex, lm: Arc::new(lm), capped }
    })
}
const DAY: i64 = 20_000;

/// An engine with the model (chat profile) and `flags`; learning on with a fixed clock when `learn`.
fn engine(layout: Layout, flags: u32, learn: bool) -> Engine {
    let s = shared();
    let mut e = Engine::with_lexicon(s.lex.clone(), layout);
    e.set_lm(s.lm.clone(), s.capped.clone());
    e.set_fuzzy(flags).unwrap();
    if learn {
        e.set_today(Some(DAY));
        e.set_learning(true);
    }
    e
}

/// Types the space-separated syllables; the last output. Every key must be accepted without an internal error.
fn type_syls(e: &mut Engine, layout: Layout, reading: &str) -> Output {
    type_syls_in(e, layout, &reading.split(' ').collect::<Vec<_>>())
}
fn press(e: &mut Engine, kind: KeyKind) -> Output {
    e.key(Key::new(kind)).expect("no internal error")
}
fn is_han(s: &str) -> bool {
    !s.is_empty() && s.chars().all(|c| c as u32 >= 0x3400) // CJK ideographs incl. extension A; zhuyin and tone marks are below
}

// ---------- dev302, the all-off guard ----------

fn dev302() -> Vec<Syls> {
    let lex = &shared().lex;
    let mut files: Vec<PathBuf> = std::fs::read_dir(root().join("eval/dev"))
        .unwrap()
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.extension().is_some_and(|x| x == "txt"))
        .collect();
    files.sort();
    let mut rows = Vec::new();
    for f in files {
        rows.extend(parse_rows(&std::fs::read_to_string(f).unwrap()).unwrap());
    }
    let mut rows = usable(lex, rows);
    rows.truncate(302);
    assert!(rows.len() == 302, "dev set has 302 usable rows");
    rows.into_iter().map(|r| r.reading.clone().unwrap_or_else(|| lex.to_syllables(&r.sent).expect("usable row"))).collect()
}
/// Column 0 (chat) of eval/golden/s2-lm-dev302-top1.tsv, the Python decoder's output.
fn golden_chat() -> Vec<String> {
    let t = std::fs::read_to_string(root().join("eval/golden/s2-lm-dev302-top1.tsv")).unwrap();
    t.lines().filter(|l| !l.starts_with('#')).map(|l| l.split('\t').next().unwrap().to_string()).collect()
}

/// Section 3.4, "all off": with the flags 0 the engine decodes dev302 exactly as the Python reference does, and so it does with
/// only bit 4 on, because clean input has no syllable the lexicon lacks (section 3.2: `top1_sha256` equal to all off).
#[test]
fn all_off_and_adjacent_only_replay_the_python_golden() {
    let want = golden_chat();
    assert_eq!(want.len(), 302);
    for flags in [0, FUZZY_ADJACENT] {
        let mut e = engine(Layout::Standard, flags, false);
        let mut diff = Vec::new();
        for (i, syls) in dev302().iter().enumerate() {
            let mut keys: Vec<Key> = syls.iter().flat_map(|s| keys_of(Layout::Standard, s)).collect();
            keys.push(Key::new(KeyKind::Enter));
            let got: String = keys.into_iter().map(|k| e.key(k).unwrap().commit).collect();
            if got != want[i] {
                diff.push(i + 1);
            }
        }
        assert!(diff.is_empty(), "flags {flags}: replay differs from the Python top-1 at rows {diff:?}");
    }
}

/// Section 3.3, a measurement (run with `--ignored --nocapture` in release on the Linux box): per-key latency of the dev302 replay,
/// all on (31) against all off. The contract's bound is a p95 ratio of at most 2; the numbers go to the research log.
#[test]
#[ignore]
fn per_key_latency_all_on_against_all_off() {
    let rows = dev302();
    let mut p95s = Vec::new();
    for flags in [0, FUZZY_ALL] {
        let mut e = engine(Layout::Standard, flags, false);
        let mut t = Vec::new();
        for syls in &rows {
            let mut keys: Vec<Key> = syls.iter().flat_map(|s| keys_of(Layout::Standard, s)).collect();
            keys.push(Key::new(KeyKind::Enter));
            for k in keys {
                let t0 = std::time::Instant::now();
                e.key(k).unwrap();
                t.push(t0.elapsed());
            }
        }
        t.sort();
        let p = t[t.len() * 95 / 100];
        println!("flags {flags}: keys {} p50 {:?} p95 {:?} max {:?}", t.len(), t[t.len() / 2], p, t[t.len() - 1]);
        p95s.push(p);
    }
    println!("p95 ratio {:.2}", p95s[1].as_secs_f64() / p95s[0].as_secs_f64());
}

// ---------- fuzzy sound ----------

/// Section 3.4: with ㄓ/ㄗ on, `ㄗ ㄉㄠˋ` (wanted 知道) shows 知道 and the typed reading stays in the composition; with the pair off
/// (all off, or every other pair on) the exact reading decides.
#[test]
fn zhi_dao_with_the_pair_on() {
    let mut off = engine(Layout::Standard, 0, false);
    let shown_off = type_syls(&mut off, Layout::Standard, "ㄗ ㄉㄠˋ").preedit;
    assert_ne!(shown_off, "知道");
    assert!(is_han(&shown_off));
    for flags in [0b1101, FUZZY_ADJACENT] {
        let mut e = engine(Layout::Standard, flags, false);
        assert_eq!(type_syls(&mut e, Layout::Standard, "ㄗ ㄉㄠˋ").preedit, shown_off, "flags {flags}: the pair is off");
    }
    let mut on = engine(Layout::Standard, 0b0010, false);
    assert_eq!(type_syls(&mut on, Layout::Standard, "ㄗ ㄉㄠˋ").preedit, "知道");
    assert_eq!(on.syllables(), ["ㄗ", "ㄉㄠˋ"], "typing keeps the typed reading; only a choice rewrites it");
}

// ---------- adjacent key ----------

/// ㄅㄩ: the lexicon has no such syllable; ㄅㄧㄝ (憋) typed with ㄝ (key ,) as its neighbour ㄩ (key m) shows it (ㄩ takes the medial
/// column over ㄧ).
const UNKNOWN: &str = "ㄅㄩ";

/// Section 3.4: with bit 4 on, a syllable the lexicon lacks but has an adjacent-key reading for is accepted and the composition shows
/// the decoded word; off, it is refused as before (unfinished syllable kept, composition unchanged). The candidate window lists the
/// words of the alternative reading.
#[test]
fn adjacent_accepts_when_on_and_refuses_when_off() {
    assert!(!shared().lex.has_syllable(UNKNOWN), "the lexicon lacks the test syllable");
    let mut on = engine(Layout::Standard, FUZZY_ADJACENT, false);
    let o = type_syls(&mut on, Layout::Standard, UNKNOWN);
    assert!(is_han(&o.preedit), "accepted: the decoded word is shown, got a non-Han preedit");
    assert_eq!(on.syllables(), [UNKNOWN]);
    let w = press(&mut on, KeyKind::Space);
    let list: Vec<String> = w.candidates.clone();
    assert!(!list.is_empty() && list.iter().all(|c| is_han(c)), "the window lists words of the alternative reading");
    let from: Vec<String> = shared().lex.adjacent_alts(UNKNOWN).iter().map(|s| s.to_string()).collect();
    assert!(from.iter().any(|s| s == "ㄅㄧㄝ"), "ㄅㄧㄝ is one of the readings it may come from");
    let alt: Vec<&str> = from.iter().flat_map(|s| shared().lex.entries(std::slice::from_ref(s))).map(|e| e.0).collect();
    assert!(list.iter().all(|c| alt.contains(&c.as_str())), "every word is an entry of a reading the slip may have come from");

    let mut off = engine(Layout::Standard, 0b1111, false);
    let o = type_syls(&mut off, Layout::Standard, UNKNOWN);
    assert_eq!(o.preedit, UNKNOWN, "bit 4 off: refused, the unfinished syllable stays");
    assert!(off.syllables().is_empty());
    assert!(o.candidates.is_empty());
}

/// Section 3.4: without a language model nothing is accepted even with bit 4 on (the unigram decoder reads no alternatives), and
/// nothing errors.
#[test]
fn no_model_never_accepts() {
    let mut e = Engine::with_lexicon(shared().lex.clone(), Layout::Standard);
    e.set_fuzzy(FUZZY_ALL).expect("setting the flags without a model is fine");
    let o = type_syls(&mut e, Layout::Standard, UNKNOWN);
    assert_eq!(o.preedit, UNKNOWN);
    assert!(e.syllables().is_empty());
    assert_eq!(e.key(Key::new(KeyKind::Enter)).expect("no internal error").commit, UNKNOWN, "Enter sends the unfinished symbols (rule 12a)");
}

/// Section 3.4: an accepted syllable stays decodable after bit 4 is turned off: the composition shows the same, a new syllable
/// can follow, and nothing errors (the switch decides only what is accepted).
#[test]
fn accepted_syllable_survives_turning_bit_4_off() {
    let mut e = engine(Layout::Standard, FUZZY_ADJACENT, false);
    let shown = type_syls(&mut e, Layout::Standard, UNKNOWN).preedit;
    let o = e.set_fuzzy(0).expect("turning bit 4 off re-decodes without an internal error");
    assert_eq!(o.preedit, shown, "display unchanged");
    let o = type_syls(&mut e, Layout::Standard, "ㄉㄠˋ");
    assert!(is_han(&o.preedit) && o.preedit.chars().count() == 2, "typing goes on, one character per syllable");
    assert_eq!(e.syllables(), [UNKNOWN, "ㄉㄠˋ"]);
    // A new unknown syllable is refused again.
    let o = type_syls(&mut e, Layout::Standard, UNKNOWN);
    assert!(o.preedit.ends_with(UNKNOWN));
    assert_eq!(e.syllables().len(), 2);
    assert!(e.key(Key::new(KeyKind::Enter)).is_ok());
}

/// The shipping engine (real lexicon with packs, small model, colloquial table, chat context) with all the flags on: a sentence with an
/// accepted adjacent-key syllable goes through the rerank job (the job reads the composition's syllables, which here include one the
/// lexicon lacks) without an internal error, and the composition stays one character per syllable. A sentence with a sound-pair slip
/// whose n-gram first path is a rescue (知道你的) has no job (section 3.6) and shows that first path, one character per syllable.
#[test]
fn shipping_models_take_a_rerank_job_over_tolerant_syllables() {
    let lm = |f: &str| root().join("data/lm").join(f);
    let mut e = Engine::new_with_packs(&root().join("data/lexicon"), Layout::Standard, Some((&root().join("data/packs"), 1))).unwrap();
    e.load_lm(&lm("bigram.sjlm")).unwrap();
    e.load_tinylm(&lm("tinylm.sjtl")).unwrap();
    e.load_colloquial(&lm("colloquial.sjlm")).unwrap();
    e.set_rerank(true).unwrap();
    e.set_left_context("今天");
    e.set_fuzzy(FUZZY_ALL).unwrap();
    for (reading, rescue) in [("ㄗ ㄉㄠˋ ㄋㄧˇ ㄉㄜ˙", true), (&*format!("ㄨㄛˇ {UNKNOWN} ㄉㄠˋ ㄋㄧˇ"), false)] {
        let o = type_syls(&mut e, Layout::Standard, reading);
        let n = reading.split(' ').count();
        assert_eq!(e.syllables().len(), n, "every syllable was accepted: {reading}");
        let job = e.take_rerank_job();
        assert_eq!((job.is_none(), e.fuzzy_skips()), (rescue, usize::from(rescue)), "{reading}");
        let shown = match job {
            None => o.preedit,
            Some(job) => match e.apply_rerank(job.run()).expect("no internal error") {
                Some(o) => o.preedit,
                None => press(&mut e, KeyKind::Right).preedit,
            },
        };
        assert!(is_han(&shown) && shown.chars().count() == n, "one character per syllable after the rerank: {shown}");
        e.reset(ResetMode::Discard);
    }
}

/// Composition cap (128 units, the head freezes and is sent): an accepted adjacent-key syllable at the head of a composition that
/// outgrows the cap is frozen and committed like any other, without an internal error; everything typed comes out as characters.
#[test]
fn an_accepted_syllable_in_the_head_survives_the_composition_cap() {
    let mut e = engine(Layout::Standard, FUZZY_ALL, false);
    let mut sent = String::new();
    let mut typed = 0;
    for reading in std::iter::once(UNKNOWN).chain(std::iter::repeat("ㄋㄧˇ ㄏㄠˇ").take(80)) {
        for syl in reading.split(' ') {
            for k in keys_of(Layout::Standard, syl) {
                sent.push_str(&e.key(k).expect("no internal error").commit);
            }
            typed += 1;
        }
    }
    sent.push_str(&press(&mut e, KeyKind::Enter).commit);
    assert!(typed > 128 && sent.chars().all(|c| c as u32 >= 0x3400), "all {typed} syllables came out as characters: {sent}");
    assert_eq!(sent.chars().count(), typed, "one character per syllable");
}

/// Section 3.4, per syllable: in a sentence with one unknown syllable only that syllable is read through its alternative; a
/// legal syllable beside it is read as typed even when it could be a slip (here ㄗ for ㄓ with the pair on would be a fuzzy
/// sound, and the pair is off).
#[test]
fn only_the_unknown_syllable_uses_its_alternative() {
    let mut e = engine(Layout::Standard, FUZZY_ADJACENT, false);
    let o = type_syls(&mut e, Layout::Standard, &format!("{UNKNOWN} ㄗ"));
    assert_eq!(e.syllables(), [UNKNOWN, "ㄗ"]);
    let last = o.preedit.chars().last().unwrap().to_string();
    let exact: Vec<&str> = shared().lex.entries(&["ㄗ".to_string()]).iter().map(|e| e.0).collect();
    assert!(exact.contains(&last.as_str()), "the legal syllable stays an entry of its own reading");

    // Whole n-best with bit 4 alone: the legal ㄒㄧㄣ beside the unknown ㄅㄩ is not read through ㄒㄧㄥ, in a word spanning both syllables
    // included (鱉星 is a lexicon word of ㄅㄧㄝ ㄒㄧㄥ): a span with an unknown syllable uses its adjacent readings only (section
    // 2.1b), whatever a sound pair would allow. The pair itself, on, does read a span of known syllables that way.
    let s = shared();
    let syls: Syls = vec![UNKNOWN.to_string(), "ㄒㄧㄣ".to_string()];
    let surfaces = |flags| -> Vec<String> {
        decode_from_fuzzy(&s.capped, &syls, &s.lm, Profile::Chat, BEAM_S1, "<s>", true, flags, core::fuzzy::DELTA_SOUND).unwrap().into_iter().map(|(_, w)| w.concat()).collect()
    };
    assert!(s.lex.entries(&["ㄅㄧㄝ".to_string(), "ㄒㄧㄥ".to_string()]).iter().any(|e| e.0 == "鱉星"), "premise: 鱉星 is a word of the pair's reading");
    assert!(!surfaces(FUZZY_ADJACENT).iter().any(|w| w == "鱉星"), "bit 4 alone does not read ㄒㄧㄣ as ㄒㄧㄥ");
    assert!(surfaces(FUZZY_ALL).iter().any(|w| w == "鱉星"), "with the pair on, the span of the known syllable alone is read through it");
}

/// Section 3.4, ETen: the neighbour table is the standard layout's keyboard, so with the ETen layout bit 4 accepts nothing (the
/// reading ㄅㄩ typed with ETen's keys would be accepted by a standard engine).
#[test]
fn eten_does_not_accept() {
    let mut std = engine(Layout::Standard, FUZZY_ADJACENT, false);
    assert!(is_han(&type_syls(&mut std, Layout::Standard, UNKNOWN).preedit), "the standard engine accepts it");
    let mut eten = engine(Layout::Eten, FUZZY_ADJACENT, false);
    let o = type_syls(&mut eten, Layout::Eten, UNKNOWN);
    assert_eq!(o.preedit, UNKNOWN);
    assert!(eten.syllables().is_empty());
}

// ---------- scores ----------

/// The score of the path that is the single word `word` in the n-best of `syls`.
fn word_score(syls: &[&str], flags: u32, word: &str) -> f64 {
    let s = shared();
    let v: Vec<String> = syls.iter().map(|x| x.to_string()).collect();
    let nb = decode_from_fuzzy(&s.capped, &v, &s.lm, Profile::Chat, BEAM_S1, "<s>", true, flags, core::fuzzy::DELTA_SOUND).unwrap();
    nb.iter().find(|(_, ws)| ws == &[word.to_string()]).unwrap_or_else(|| panic!("no path of the one word {word}")).0
}

/// Section 2.1: a word read through one syllable of a sound pair scores `DELTA_SOUND` (2.5, section 3.5) less than the same word read as
/// the lexicon has it, and through the adjacent reading of a syllable the lexicon lacks, 2 less (log10); and with flags 0
/// `decode_from_fuzzy` is `decode_from`.
#[test]
fn a_sound_pair_costs_its_delta_and_an_adjacent_slip_two() {
    let exact = word_score(&["ㄓ", "ㄉㄠˋ"], 0, "知道");
    assert!((exact - word_score(&["ㄗ", "ㄉㄠˋ"], 0b0010, "知道") - core::fuzzy::DELTA_SOUND).abs() < 1e-9);
    let exact = word_score(&["ㄅㄧㄝ"], 0, "憋");
    assert!((exact - word_score(&[UNKNOWN], 0, "憋") - 2.0).abs() < 1e-9, "whatever the flags say");
    assert!((exact - word_score(&[UNKNOWN], FUZZY_ALL, "憋") - 2.0).abs() < 1e-9);
    let s = shared();
    let v: Vec<String> = ["ㄓ", "ㄉㄠˋ"].iter().map(|x| x.to_string()).collect();
    let a = decode_from(&s.capped, &v, &s.lm, Profile::Chat, BEAM_S1, "<s>", true).unwrap();
    let b = decode_from_fuzzy(&s.capped, &v, &s.lm, Profile::Chat, BEAM_S1, "<s>", true, 0, core::fuzzy::DELTA_SOUND).unwrap();
    assert!(a == b, "flags 0 is the plain decoder");
}

// ---------- candidate window, choosing, learning ----------

/// The whole candidate list of the open window, read by stepping the selection.
fn window_list(e: &mut Engine, mut o: Output) -> Vec<String> {
    let mut list = Vec::new();
    for _ in 0..o.total {
        list.push(o.candidates[o.selected.expect("open window")].clone());
        o = press(e, KeyKind::Right);
    }
    list
}
/// Steps the selection of the window `o` to `word`.
fn select(e: &mut Engine, o: Output, word: &str) {
    let mut o = o;
    for _ in 0..o.total {
        if o.candidates[o.selected.unwrap()] == word {
            return;
        }
        o = press(e, KeyKind::Right);
    }
    panic!("the window does not list the wanted word");
}

/// Section 3.4: the window lists the exact words first (longer readings first, as ever), then the words found through the tolerant
/// reading; choosing one makes the span its lexicon reading, so the later re-decode, the learning record and the next typing all
/// use the lexicon reading. Key sequence: ㄓ/ㄗ on, type ㄗ ㄉㄠˋ, open the window, choose, Enter, read the learner. 之道 is chosen
/// (知道 is the first answer already, and choosing what is shown teaches nothing).
#[test]
fn window_lists_tolerant_words_last_and_choosing_one_teaches_the_lexicon_reading() {
    let lex = &shared().lex;
    let mut e = engine(Layout::Standard, 0b0010, true);
    // Without the prediction row, whose items come first in the window (predict-down B.3.1): this test is about the lexicon's part.
    e.set_prediction(false).unwrap();
    assert_eq!(type_syls(&mut e, Layout::Standard, "ㄗ ㄉㄠˋ").preedit, "知道");
    let o = press(&mut e, KeyKind::Space);
    let list = window_list(&mut e, o);
    press(&mut e, KeyKind::Esc); // closes the window; the composition stays
    let o = press(&mut e, KeyKind::Space);
    // The exact words, as the window lists them without fuzzy input.
    let mut exact: Vec<String> = Vec::new();
    for key in [vec!["ㄗ", "ㄉㄠˋ"], vec!["ㄉㄠˋ"]] {
        let key: Vec<String> = key.into_iter().map(String::from).collect();
        for (w, _) in lex.entries(&key) {
            if !exact.iter().any(|x| x == w) {
                exact.push(w.to_string());
            }
        }
    }
    assert_eq!(list[..exact.len()], exact[..], "the exact words come first, in their order");
    let tolerant = &list[exact.len()..];
    for w in ["知道", "之道"] {
        assert!(tolerant.iter().any(|t| t == w), "{w} is listed through the tolerant reading");
    }
    assert!(tolerant.iter().all(|t| !exact.contains(t)), "no word twice");

    // Choose 之道: the span becomes its lexicon reading, the composition shows it, and Enter teaches it under that reading.
    select(&mut e, o, "之道");
    let o = press(&mut e, KeyKind::Enter);
    assert_eq!(o.preedit, "之道");
    assert_eq!(e.syllables(), ["ㄓ", "ㄉㄠˋ"], "the span is rewritten to the word's lexicon reading");
    assert_eq!(press(&mut e, KeyKind::Enter).commit, "之道");
    let taught: Vec<_> = e.learner().records().iter().filter(|r| r.word == "之道").collect();
    assert!(!taught.is_empty(), "choosing a word other than the shown one teaches it");
    assert!(taught.iter().all(|r| r.reading == ["ㄓ", "ㄉㄠˋ"]), "the learned reading is the lexicon's, not the typed one");
    assert!(e.learner().records().iter().all(|r| r.reading.first().map(String::as_str) != Some("ㄗ")), "nothing under the typed reading");

    // The lookup also goes by the lexicon reading: typing the slip again now shows the taught word.
    assert_eq!(type_syls(&mut e, Layout::Standard, "ㄗ ㄉㄠˋ").preedit, "之道");
}

/// Choosing the word that is shown rewrites the span too, and teaches nothing.
#[test]
fn choosing_the_shown_tolerant_word_rewrites_the_span() {
    let mut e = engine(Layout::Standard, 0b0010, true);
    type_syls(&mut e, Layout::Standard, "ㄗ ㄉㄠˋ");
    let o = press(&mut e, KeyKind::Space);
    select(&mut e, o, "知道");
    let o = press(&mut e, KeyKind::Enter);
    assert_eq!(o.preedit, "知道");
    assert_eq!(e.syllables(), ["ㄓ", "ㄉㄠˋ"]);
    press(&mut e, KeyKind::Enter);
    assert!(e.learner().records().is_empty());
}

/// A slip accepted through bit 4: choosing from the window rewrites the unknown syllable to a lexicon reading, so the composition
/// no longer depends on the switch.
#[test]
fn choosing_an_adjacent_word_rewrites_the_unknown_syllable() {
    let mut e = engine(Layout::Standard, FUZZY_ADJACENT, false);
    type_syls(&mut e, Layout::Standard, UNKNOWN);
    let o = press(&mut e, KeyKind::Space);
    let first = o.candidates[0].clone();
    let o = press(&mut e, KeyKind::Enter);
    assert_eq!(o.preedit, first);
    assert!(shared().lex.has_syllable(&e.syllables()[0]) && e.syllables()[0] != UNKNOWN);
}

/// Contract fuzzy-input.md 2.1b: an adjacent-key syllable is accepted without checking that an alternative reading has a
/// one-syllable entry, because every syllable of the lexicon (base, overlay, sandhi, names and the word pack) has one. This is
/// what makes an accepted syllable always decodable; if it fails, restore the check in `Engine::adjacent_accepts`.
#[test]
fn every_syllable_has_a_single_entry() {
    let (lex, _) = load_lexicon_packs(&root().join("data/lexicon"), Some((&root().join("data/packs"), PACK_ALL))).unwrap();
    assert!(lex.syllables().len() > 1000, "the lexicon did not load");
    let missing: Vec<&String> = lex.syllables().iter().filter(|s| lex.entries(std::slice::from_ref(*s)).is_empty()).collect();
    assert!(missing.is_empty(), "syllables without a one-syllable entry: {missing:?}");
}

// ---------- review fixes ----------

/// Review item 1: `freeze_outside` froze a whole path word outside the picked span without asking whether the typed reading has it.
/// With ㄣ/ㄥ on, ㄅㄧㄣˇ ㄉㄠˋ decodes 丙到, 丙 a word of ㄅㄧㄥˇ; picking 道 over the second syllable froze 丙 under ㄅㄧㄣˇ, which has no
/// such entry, and the refresh returned `Internal`. A tolerant word outside the pick is left to the decoder, as `pieces` and
/// `cap_refreeze` do; the pick goes through.
#[test]
fn picking_beside_a_tolerant_word_does_not_freeze_it_under_the_typed_reading() {
    let mut e = engine(Layout::Standard, 0b0001, false);
    e.set_prediction(false).unwrap();
    assert_eq!(type_syls(&mut e, Layout::Standard, "ㄅㄧㄣˇ ㄉㄠˋ").preedit, "丙到");
    let o = press(&mut e, KeyKind::Space);
    select(&mut e, o, "道");
    let o = e.key(Key::new(KeyKind::Enter)).expect("picking beside a tolerant word is no internal error");
    assert!(o.preedit.ends_with('道'), "the picked word is fixed, got {}", o.preedit);
    assert_eq!(e.syllables(), ["ㄅㄧㄣˇ", "ㄉㄠˋ"], "the typed reading stays");
}
