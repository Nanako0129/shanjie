//! Fuzzy input (docs/spec/decoder.md §6.4): the engine with the real lexicon and the bigram model. Needs
//! data/lm/bigram.sjlm like engine_lm.rs and fails loudly without it (never skips). The tables themselves are tested in
//! core/src/fuzzy.rs; the ABI codes in core/src/ffi.rs and core/tests/c/abi_smoke.c.
use core::engine::*;
use core::eval::{parse_rows, usable};
use core::fuzzy::{FUZZY_ADJACENT, FUZZY_ALL, FUZZY_ONE_SYM};
use core::lm::{decode_from, decode_from_fuzzy_scored, CappedLexicon, Lm, Profile};
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
/// Every bit before the one-symbol bit (what `FUZZY_ALL` was): the older tests below keep their meaning when a bit is added.
const BITS_0_4: u32 = 0b1_1111;

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

/// The n-best of `decode_from_fuzzy_scored` as (score, words) with the words as strings.
fn n_best(s: &Shared, syls: &[String], flags: u32) -> Vec<(f64, Vec<String>)> {
    decode_from_fuzzy_scored(&s.capped, syls, &s.lm, Profile::Chat, BEAM_S1, "<s>", true, flags, core::fuzzy::DELTA_SOUND, None, None)
        .unwrap()
        .into_iter()
        .map(|(sc, ws)| (sc, ws.into_iter().map(|w| w.0.to_string()).collect()))
        .collect()
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

/// docs/spec/decoder.md §6.4, "all off": with the flags 0 the engine decodes dev302 exactly as the Python reference does, and so it does with
/// only bit 4 on, because clean input has no syllable the lexicon lacks (docs/spec/decoder.md §6.1: `top1_sha256` equal to all off).
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

/// docs/spec/decoder.md §6.4, a measurement (run with `--ignored --nocapture` in release on the Linux box): per-key latency of the dev302 replay,
/// all on (63, bit 5 included) against all off. The bound is a p95 ratio of at most 2.
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

/// docs/spec/decoder.md §6.4: with ㄓ/ㄗ on, `ㄗ ㄉㄠˋ` (wanted 知道) shows 知道 and the typed reading stays in the composition; with the pair off
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

/// docs/spec/decoder.md §6.4: with bit 4 on, a syllable the lexicon lacks but has an adjacent-key reading for is accepted and the composition shows
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

/// docs/spec/decoder.md §6.4: without a language model nothing is accepted even with bit 4 on (the unigram decoder reads no alternatives), and
/// nothing errors.
#[test]
fn no_model_never_accepts() {
    let mut e = Engine::with_lexicon(shared().lex.clone(), Layout::Standard);
    e.set_fuzzy(BITS_0_4).expect("setting the flags without a model is fine");
    let o = type_syls(&mut e, Layout::Standard, UNKNOWN);
    assert_eq!(o.preedit, UNKNOWN);
    assert!(e.syllables().is_empty());
    assert_eq!(e.key(Key::new(KeyKind::Enter)).expect("no internal error").commit, UNKNOWN, "Enter sends the unfinished symbols (rule 12a)");
}

/// docs/spec/decoder.md §6.4: an accepted syllable stays decodable after bit 4 is turned off: the composition shows the same, a new syllable
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
/// whose n-gram first path is a rescue (知道你的) has no job (docs/spec/decoder.md §6.5) and shows that first path, one character per syllable.
#[test]
fn shipping_models_take_a_rerank_job_over_tolerant_syllables() {
    let lm = |f: &str| root().join("data/lm").join(f);
    let mut e = Engine::new_with_packs(&root().join("data/lexicon"), Layout::Standard, Some((&root().join("data/packs"), 1))).unwrap();
    e.load_lm(&lm("bigram.sjlm")).unwrap();
    e.load_tinylm(&lm("tinylm.sjtl")).unwrap();
    e.load_colloquial(&lm("colloquial.sjlm")).unwrap();
    e.set_rerank(true).unwrap();
    e.set_left_context("今天");
    e.set_fuzzy(BITS_0_4).unwrap();
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
    let mut e = engine(Layout::Standard, BITS_0_4, false);
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

/// docs/spec/decoder.md §6.4, per syllable: in a sentence with one unknown syllable only that syllable is read through its alternative; a
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
    // included (鱉星 is a lexicon word of ㄅㄧㄝ ㄒㄧㄥ): a span with an unknown syllable uses its adjacent readings only (docs/spec/decoder.md §6.2), whatever a sound pair would allow. The pair itself, on, does read a span of known syllables that way.
    let s = shared();
    let syls: Syls = vec![UNKNOWN.to_string(), "ㄒㄧㄣ".to_string()];
    let surfaces = |flags| -> Vec<String> {
        n_best(s, &syls, flags).into_iter().map(|(_, w)| w.concat()).collect()
    };
    assert!(s.lex.entries(&["ㄅㄧㄝ".to_string(), "ㄒㄧㄥ".to_string()]).iter().any(|e| e.0 == "鱉星"), "premise: 鱉星 is a word of the pair's reading");
    assert!(!surfaces(FUZZY_ADJACENT).iter().any(|w| w == "鱉星"), "bit 4 alone does not read ㄒㄧㄣ as ㄒㄧㄥ");
    assert!(surfaces(BITS_0_4).iter().any(|w| w == "鱉星"), "with the pair on, the span of the known syllable alone is read through it");
}

/// docs/spec/decoder.md §6.4, ETen: the neighbour table is the standard layout's keyboard, so with the ETen layout bit 4 accepts nothing (the
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
    let nb = n_best(s, &v, flags);
    nb.iter().find(|(_, ws)| ws == &[word.to_string()]).unwrap_or_else(|| panic!("no path of the one word {word}")).0
}

/// docs/spec/decoder.md §6.2: a word read through one syllable of a sound pair scores `DELTA_SOUND` (2.5, docs/spec/decoder.md §6.1) less than the same word read as
/// the lexicon has it, and through the adjacent reading of a syllable the lexicon lacks, 2 less (log10); and with flags 0
/// `decode_from_fuzzy_scored` is `decode_from`.
#[test]
fn a_sound_pair_costs_its_delta_and_an_adjacent_slip_two() {
    let exact = word_score(&["ㄓ", "ㄉㄠˋ"], 0, "知道");
    assert!((exact - word_score(&["ㄗ", "ㄉㄠˋ"], 0b0010, "知道") - core::fuzzy::DELTA_SOUND).abs() < 1e-9);
    let exact = word_score(&["ㄅㄧㄝ"], 0, "憋");
    assert!((exact - word_score(&[UNKNOWN], 0, "憋") - 2.0).abs() < 1e-9, "whatever the flags say");
    assert!((exact - word_score(&[UNKNOWN], BITS_0_4, "憋") - 2.0).abs() < 1e-9);
    let s = shared();
    let v: Vec<String> = ["ㄓ", "ㄉㄠˋ"].iter().map(|x| x.to_string()).collect();
    let a = decode_from(&s.capped, &v, &s.lm, Profile::Chat, BEAM_S1, "<s>", true).unwrap();
    let b = n_best(s, &v, 0);
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

/// docs/spec/decoder.md §6.4: the window lists the exact words first (longer readings first, as ever), then the words found through the tolerant
/// reading; choosing one makes the span its lexicon reading, so the later re-decode, the learning record and the next typing all
/// use the lexicon reading. Key sequence: ㄓ/ㄗ on, type ㄗ ㄉㄠˋ, open the window, choose, Enter, read the learner. 之道 is chosen
/// (知道 is the first answer already, and choosing what is shown teaches nothing).
#[test]
fn window_lists_tolerant_words_last_and_choosing_one_teaches_the_lexicon_reading() {
    let lex = &shared().lex;
    let mut e = engine(Layout::Standard, 0b0010, true);
    // Without the prediction row, whose items come first in the window (docs/spec/prediction.md §5): this test is about the lexicon's part.
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

// ---------- review fixes ----------

/// `freeze_outside` froze a whole path word outside the picked span without asking whether the typed reading has it.
/// With ㄣ/ㄥ on, ㄅㄧㄣˇ ㄉㄠˋ decodes 餅到 (丙到 on model-v6), 餅 a word of ㄅㄧㄥˇ; picking 道 over the second syllable froze 餅 under ㄅㄧㄣˇ, which has no
/// such entry, and the refresh returned `Internal`. A tolerant word outside the pick is left to the decoder, as `pieces` and
/// `cap_refreeze` do; the pick goes through.
#[test]
fn picking_beside_a_tolerant_word_does_not_freeze_it_under_the_typed_reading() {
    let mut e = engine(Layout::Standard, 0b0001, false);
    e.set_prediction(false).unwrap();
    assert_eq!(type_syls(&mut e, Layout::Standard, "ㄅㄧㄣˇ ㄉㄠˋ").preedit, "餅到");
    let o = press(&mut e, KeyKind::Space);
    select(&mut e, o, "道");
    let o = e.key(Key::new(KeyKind::Enter)).expect("picking beside a tolerant word is no internal error");
    assert!(o.preedit.ends_with('道'), "the picked word is fixed, got {}", o.preedit);
    assert_eq!(e.syllables(), ["ㄅㄧㄣˇ", "ㄉㄠˋ"], "the typed reading stays");
}

/// ⌘⌫ on a learned new word drops its item from the open window; the window's map from list position to lexicon reading
/// (`Cands::alt`) stayed as it was, so every tolerant word behind the forgotten one read its neighbour's reading when chosen.
/// 派弓 (not 派工: on the 通語 SJ-LM-7-9.7M files 派工 is already the n-gram's first path, so picking it re-picks nothing and teaches no new word) is taught with ㄣ/ㄥ on; typing it again lists 派弓 first among the two-syllable items and the one-syllable words of ㄍㄨㄣ
/// (read through the slip ㄍㄨㄥ) last. After forgetting 派弓, choosing the first of those must rewrite ㄍㄨㄥ to ㄍㄨㄣ.
#[test]
fn forgetting_a_new_word_keeps_the_tolerant_readings_in_step() {
    const FORGET: Key = Key { kind: KeyKind::Backspace, ch: '\0', modifiers: MOD_COMMAND };
    let lex = &shared().lex;
    let mut e = engine(Layout::Standard, 0b0001, true);
    e.set_prediction(false).unwrap();
    e.set_left_context("要進");
    let pick = |e: &mut Engine, at: usize, word: &str| {
        press(e, KeyKind::End);
        for _ in 0..2 - (at + 1) {
            press(e, KeyKind::Left);
        }
        let o = press(e, KeyKind::Space);
        select(e, o, word);
        press(e, KeyKind::Enter);
        press(e, KeyKind::End);
    };
    type_syls(&mut e, Layout::Standard, "ㄆㄞˋ ㄍㄨㄥ");
    pick(&mut e, 0, "派");
    pick(&mut e, 1, "弓");
    assert_eq!(press(&mut e, KeyKind::Enter).commit, "派弓", "taught");
    e.set_left_context("加入");
    let shown = type_syls(&mut e, Layout::Standard, "ㄆㄞˋ ㄍㄨㄥ").preedit;
    let o = press(&mut e, KeyKind::Space);
    let before = window_list(&mut e, o.clone());
    assert!(before.iter().any(|w| w == "派弓"), "the taught word is listed, shown {shown}");
    // The one-syllable words found through ㄍㄨㄣ: not an entry of ㄍㄨㄥ, an entry of ㄍㄨㄣ.
    let exact1: Vec<&str> = lex.entries(&["ㄍㄨㄥ".to_string()]).into_iter().map(|x| x.0).collect();
    let slip = before.iter().find(|w| w.chars().count() == 1 && !exact1.contains(&w.as_str()) && lex.has(&["ㄍㄨㄣ".to_string()], w)).expect("a word through ㄍㄨㄣ").clone();
    // Open the window anew on 派弓 and forget it; the same window goes on.
    press(&mut e, KeyKind::Esc);
    let o = press(&mut e, KeyKind::Space);
    select(&mut e, o, "派弓");
    let o = e.key(FORGET).unwrap();
    assert!(o.total > 0 && !o.candidates.iter().any(|c| c == "派弓"), "the window stays open without the forgotten word");
    select(&mut e, o, &slip);
    let o = e.key(Key::new(KeyKind::Enter)).expect("choosing a tolerant word after a forget is no internal error");
    assert_eq!(o.preedit.chars().last().map(String::from).as_deref(), Some(slip.as_str()));
    assert_eq!(e.syllables(), ["ㄆㄞˋ", "ㄍㄨㄣ"], "the span is the word's lexicon reading, whichever item was forgotten before it");
}

/// The learning guard (`Engine::boosted`) returned false at once for an empty learner and judged an adjacent-key word
/// "boosted" otherwise, so the same typing took a rerank job on a fresh profile and none (and a `guard_skips` count) once any record
/// existed. A word read through an accepted adjacent-key syllable is no learned word: the stretch is reranked either way, as the
/// evaluation CLI (which has no learner) reranks it.
#[test]
fn the_learning_guard_does_not_depend_on_whether_the_learner_has_records() {
    let lm = |f: &str| root().join("data/lm").join(f);
    let sentence = format!("ㄨㄛˇ {UNKNOWN} ㄉㄠˋ ㄋㄧˇ");
    let run = |taught: bool| {
        let mut e = Engine::new_with_packs(&root().join("data/lexicon"), Layout::Standard, Some((&root().join("data/packs"), 1))).unwrap();
        e.load_lm(&lm("bigram.sjlm")).unwrap();
        e.load_tinylm(&lm("tinylm.sjtl")).unwrap();
        e.load_colloquial(&lm("colloquial.sjlm")).unwrap();
        e.set_rerank(true).unwrap();
        e.set_fuzzy(BITS_0_4).unwrap();
        e.set_today(Some(DAY));
        e.set_learning(true);
        if taught {
            // One re-pick on a reading the sentence does not use, committed: the learner now holds a record.
            e.set_prediction(false).unwrap();
            e.set_left_context("今天");
            let shown = type_syls(&mut e, Layout::Standard, "ㄅㄞˊ ㄇㄚˇ").preedit;
            let mut o = press(&mut e, KeyKind::Space);
            for _ in 0..o.total {
                let cur = &o.candidates[o.selected.unwrap()];
                if cur.chars().count() == 2 && *cur != shown {
                    break;
                }
                o = press(&mut e, KeyKind::Right);
            }
            press(&mut e, KeyKind::Enter);
            press(&mut e, KeyKind::Enter);
            assert!(!e.learner().records().is_empty(), "premise: the learner holds a record");
            e.reset(ResetMode::Discard);
        }
        e.set_left_context("今天");
        type_syls(&mut e, Layout::Standard, &sentence);
        assert_eq!(e.syllables().len(), 4, "every syllable was accepted");
        (e.take_rerank_job().is_some(), e.guard_skips())
    };
    assert_eq!(run(false), (true, 0), "premise: a fresh profile reranks the stretch");
    assert_eq!(run(true), (true, 0), "a profile with a record reranks it too, and no guard skip is counted");
}

/// `choose` spliced a tolerant word's lexicon reading into the typed syllables before it listed the new words the pick
/// displaces, so the path's new words overlapping the span were looked up under the rewritten reading and 派弓 (taught as ㄆㄞˋ ㄍㄨㄥ)
/// was missed when 棍 (a word of ㄍㄨㄣ) was picked over the ㄍㄨㄥ. Picking any other word there halves 派弓 at the commit (docs/spec/learning.md §8.5);
/// the tolerant pick must too.
#[test]
fn a_tolerant_pick_halves_the_new_words_it_displaces_under_their_own_reading() {
    let reading = ["ㄆㄞˋ".to_string(), "ㄍㄨㄥ".to_string()];
    let pick_one_syllable = |e: &mut Engine, want: &dyn Fn(&str) -> bool| {
        press(e, KeyKind::End);
        let mut o = press(e, KeyKind::Space);
        for _ in 0..o.total {
            let w = o.candidates[o.selected.unwrap()].clone();
            if w.chars().count() == 1 && want(&w) {
                press(e, KeyKind::Enter);
                return w;
            }
            o = press(e, KeyKind::Right);
        }
        panic!("no such candidate");
    };
    let run = |tolerant: bool| {
        let lex = &shared().lex;
        let mut e = engine(Layout::Standard, 0b0001, true);
        e.set_prediction(false).unwrap();
        e.set_left_context("要進");
        type_syls(&mut e, Layout::Standard, "ㄆㄞˋ ㄍㄨㄥ");
        for (at, w) in [(0, "派"), (1, "弓")] {
            press(&mut e, KeyKind::End);
            for _ in 0..1 - at {
                press(&mut e, KeyKind::Left);
            }
            let o = press(&mut e, KeyKind::Space);
            select(&mut e, o, w);
            press(&mut e, KeyKind::Enter);
        }
        assert_eq!(press(&mut e, KeyKind::Enter).commit, "派弓", "taught");
        let before = e.learner().new_word_weight(&reading, "派弓", DAY).expect("派弓 is a new word");
        e.set_left_context("加入");
        assert_eq!(type_syls(&mut e, Layout::Standard, "ㄆㄞˋ ㄍㄨㄥ").preedit, "派弓");
        let exact1: Vec<&str> = lex.entries(&["ㄍㄨㄥ".to_string()]).into_iter().map(|x| x.0).collect();
        let w = pick_one_syllable(&mut e, &|w| if tolerant { !exact1.contains(&w) && lex.has(&["ㄍㄨㄣ".to_string()], w) } else { w != "弓" && exact1.contains(&w) });
        assert_eq!(e.syllables().last().map(String::as_str), Some(if tolerant { "ㄍㄨㄣ" } else { "ㄍㄨㄥ" }), "picked {w}");
        press(&mut e, KeyKind::Enter);
        (before, e.learner().new_word_weight(&reading, "派弓", DAY))
    };
    let (before, after) = run(false);
    assert!(after.is_some_and(|a| a < before), "premise: an exact pick halves the displaced new word: {before} -> {after:?}");
    let (before, after) = run(true);
    assert!(after.is_some_and(|a| a < before), "the tolerant pick halves it too: {before} -> {after:?}");
}

/// The tolerance tables list every interned syllable, the packs' included, but the decoder reads an unknown syllable
/// through the one-syllable entries of its alternatives. ㄧㄟˊ is in a pack word only (ㄧㄟˊ-ㄅㄚ) and has no entry of its own; ㄧㄛˊ,
/// the slip of ㄧㄟˊ (ㄛ for its neighbour ㄟ), is a syllable only ㄧㄟˊ can have been typed as. Accepting ㄧㄛˊ left a composition no
/// path covers (`Internal` at the key). An alternative with no one-syllable entry is no alternative: ㄧㄛˊ is refused like any unknown
/// syllable.
#[test]
fn a_pack_only_syllable_without_a_single_entry_is_not_an_alternative() {
    const PACK_ONLY: &str = "ㄧㄟˊ";
    const SLIP: &str = "ㄧㄛˊ";
    let dir = std::env::temp_dir().join(format!("shanjie-fuzzy-pack-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join("acg-add.tsv"), format!("{PACK_ONLY}-ㄅㄚ\t甲乙\t-7.0\tacg\n")).unwrap();
    let (lex, _) = load_lexicon_packs(&root().join("data/lexicon"), Some((&dir, 1))).unwrap();
    assert!(lex.has_syllable(PACK_ONLY) && lex.entries(&[PACK_ONLY.to_string()]).is_empty(), "premise: a pack-only syllable with no one-syllable entry");
    assert!(!lex.has_syllable(SLIP), "premise: the slip is no lexicon syllable");

    let mut e = Engine::new_with_packs(&root().join("data/lexicon"), Layout::Standard, Some((&dir, 1))).unwrap();
    e.load_lm(&lm_path()).unwrap();
    e.set_fuzzy(FUZZY_ADJACENT).unwrap();
    let o = type_syls(&mut e, Layout::Standard, SLIP);
    assert!(e.syllables().is_empty() && !o.preedit.chars().any(|c| c as u32 >= 0x3400), "refused: no syllable, no decoded character, got {}", o.preedit);
    let _ = std::fs::remove_dir_all(&dir);
}

// ---------- one symbol (bit 5, docs/contracts/typo-one-sym.md) ----------

const ONE: u32 = FUZZY_ONE_SYM;
fn lname(l: Layout) -> &'static str {
    if l == Layout::Eten { "Eten" } else { "Standard" }
}
/// ㄋㄟ: only the one-symbol table has an alternative; ㄐㄝ: only the adjacent-key one; ㄒㄧㄟˇ (a slip of ㄒㄧㄤˇ, 想) and ㄅㄩ (`UNKNOWN`) are in both.
const ONLY_ONE: &str = "ㄋㄟ";
const ONLY_ADJ: &str = "ㄐㄝ";
const SLIP: &str = "ㄒㄧㄟˇ";

fn premises() {
    let lex = &shared().lex;
    for s in [ONLY_ONE, ONLY_ADJ, SLIP, UNKNOWN] {
        assert!(!lex.has_syllable(s), "premise: {s} is no lexicon syllable");
    }
    assert!(lex.adjacent_alts(ONLY_ONE).is_empty() && !lex.one_sym_alts(ONLY_ONE).is_empty(), "premise: {ONLY_ONE} is one-symbol only");
    assert!(!lex.adjacent_alts(ONLY_ADJ).is_empty() && lex.one_sym_alts(ONLY_ADJ).is_empty(), "premise: {ONLY_ADJ} is adjacent only");
    assert!(lex.one_sym_alts(SLIP).contains(&"ㄒㄧㄤˇ") && !lex.adjacent_alts(SLIP).is_empty(), "premise: {SLIP} is in both tables");
    assert!(!lex.one_sym_alts(UNKNOWN).contains(&"ㄅㄧㄝ") && lex.adjacent_alts(UNKNOWN).contains(&"ㄅㄧㄝ"), "premise: {UNKNOWN} is read as ㄅㄧㄝ (憋) by the adjacent table only");
}

/// docs/contracts/typo-one-sym.md §2.3 step 4, §2.4: with bit 5 (alone) a syllable the lexicon lacks is accepted on both layouts and the composition shows the word
/// read through the one-symbol alternatives (想 for ㄒㄧㄟˇ, between 我 and 去); with the bit off it is refused as before; choosing a window word rewrites the
/// span to the lexicon reading.
#[test]
fn one_sym_accepts_a_slip_on_both_layouts_and_shows_the_word() {
    premises();
    let typed = ["ㄨㄛˇ", SLIP, "ㄑㄩˋ"];
    for layout in [Layout::Standard, Layout::Eten] {
        let mut e = engine(layout, ONE, false);
        let o = type_syls_in(&mut e, layout, &typed);
        assert_eq!(o.preedit, "我想去", "{}", lname(layout));
        assert_eq!(e.syllables(), typed);
        // The window lists the words of the other readings; choosing 寫 makes the span ㄒㄧㄝˇ.
        e.set_prediction(false).unwrap();
        press(&mut e, KeyKind::Left);
        let o = press(&mut e, KeyKind::Space);
        let list = window_list(&mut e, o);
        assert!(list.iter().any(|w| w == "小") && list.iter().any(|w| w == "寫"), "{}: the one-symbol words are listed: {list:?}", lname(layout));
        press(&mut e, KeyKind::Esc);
        let o = press(&mut e, KeyKind::Space);
        select(&mut e, o, "寫");
        let o = press(&mut e, KeyKind::Enter);
        assert!(o.preedit.contains('寫'), "{}: {}", lname(layout), o.preedit);
        assert_eq!(e.syllables()[1], "ㄒㄧㄝˇ", "{}: the span is the word's lexicon reading", lname(layout));
    }
    let mut off = engine(Layout::Standard, 0, false);
    let o = type_syls(&mut off, Layout::Standard, &typed.join(" "));
    assert_eq!(off.syllables(), ["ㄨㄛˇ"], "bit 5 off: refused, the unfinished syllable stays: {}", o.preedit);
    // Bit 5 alone does not take what only the adjacent table knows.
    let mut e = engine(Layout::Standard, ONE, false);
    let o = type_syls(&mut e, Layout::Standard, ONLY_ADJ);
    assert!(e.syllables().is_empty(), "{ONLY_ADJ} has no one-symbol alternative: {}", o.preedit);
}

/// Without a model nothing is accepted (the decoder without one reads no alternative), with bit 5 as with bit 4.
#[test]
fn one_sym_needs_a_model() {
    let mut e = Engine::with_lexicon(shared().lex.clone(), Layout::Standard);
    e.set_fuzzy(FUZZY_ONE_SYM).unwrap();
    type_syls(&mut e, Layout::Standard, ONLY_ONE);
    assert!(e.syllables().is_empty());
}

/// docs/contracts/typo-one-sym.md §2.4 item 2 and §4.1 (1): `shanjie-eval --fuzzy 0` and `--fuzzy 16` never read the one-symbol table (the decoder
/// has no path through ㄋㄟ, which only that table knows), `--fuzzy 32` does; and the penalties: a word read through the one-symbol table is
/// `DELTA_ONE_SYM` (2.5) below the lexicon's reading, through the adjacent table `DELTA_ADJACENT` (2), with both bits on as with either alone.
#[test]
fn the_flags_choose_the_table_and_its_penalty() {
    premises();
    let s = shared();
    let nb = |syls: &[&str], flags: u32| {
        let v: Vec<String> = syls.iter().map(|x| x.to_string()).collect();
        decode_from_fuzzy_scored(&s.capped, &v, &s.lm, Profile::Chat, BEAM_S1, "<s>", true, flags, core::fuzzy::DELTA_SOUND, None, None)
    };
    for flags in [0, FUZZY_ADJACENT, 0b1111, BITS_0_4] {
        assert!(nb(&[ONLY_ONE], flags).is_err(), "flags {flags}: no path through {ONLY_ONE}");
    }
    for flags in [ONE, ONE | FUZZY_ADJACENT, FUZZY_ALL] {
        assert!(nb(&[ONLY_ONE], flags).is_ok(), "flags {flags}");
    }
    // 想 (ㄒㄧㄤˇ) is read through the one-symbol table of ㄒㄧㄟˇ only (the adjacent one holds other syllables), at 2.5 in both settings that have it.
    let exact = word_score(&["ㄒㄧㄤˇ"], 0, "想");
    for flags in [ONE, ONE | FUZZY_ADJACENT, FUZZY_ALL] {
        assert!((exact - word_score(&[SLIP], flags, "想") - core::fuzzy::DELTA_ONE_SYM).abs() < 1e-9, "flags {flags}");
    }
    assert!(nb(&[SLIP], FUZZY_ADJACENT).unwrap().iter().all(|(_, ws)| ws.iter().all(|w| w.0 != "想")), "bit 4 alone does not reach 想");
    // The adjacent table's word keeps 2 when bit 5 joins: ㄅㄩ -> ㄅㄧㄝ (憋) is not a one-symbol reading, so with bit 5 alone it is gone.
    let exact = word_score(&["ㄅㄧㄝ"], 0, "憋");
    assert!((exact - word_score(&[UNKNOWN], ONE | FUZZY_ADJACENT, "憋") - core::fuzzy::DELTA_ADJACENT).abs() < 1e-9);
    assert!(nb(&[UNKNOWN], ONE).unwrap().iter().all(|(_, ws)| ws.iter().all(|w| w.0 != "憋")), "bit 5 alone never reads the adjacent table");
    assert!(core::fuzzy::DELTA_ONE_SYM > core::fuzzy::DELTA_ADJACENT, "the adjacent-key reading ranks above the wider net");
}

/// docs/contracts/typo-one-sym.md §2.4 item 1: the ETen layout reads no adjacent key, so a flags value with bit 4 is bit 5 alone there (`effective_fuzzy_for`),
/// and nothing of the standard keyboard shows through: the window of an accepted ㄅㄩ has no 憋 (a word of the adjacent reading ㄅㄧㄝ), and the standard
/// layout with the same flags has it.
#[test]
fn eten_reads_bit_5_without_the_adjacent_table() {
    premises();
    assert_eq!(effective_fuzzy_for(Layout::Eten, 0b11_1111), 0b10_1111);
    assert_eq!(effective_fuzzy_for(Layout::Standard, 0b11_1111), 0b11_1111);
    let list = |layout| {
        let mut e = engine(layout, ONE | FUZZY_ADJACENT, false);
        e.set_prediction(false).unwrap();
        type_syls_in(&mut e, layout, &[UNKNOWN]);
        let o = press(&mut e, KeyKind::Space);
        window_list(&mut e, o)
    };
    assert!(list(Layout::Standard).iter().any(|w| w == "憋"), "premise: the standard layout reads the adjacent table");
    let eten = list(Layout::Eten);
    assert!(!eten.is_empty() && !eten.iter().any(|w| w == "憋"), "ETen: {eten:?}");
}

// ---- the English guard (docs/contracts/typo-one-sym.md §2.3 step 4) ----

/// The engine with the macOS defaults of English mixing (§4.1: english on, overwrite off, key order on, reversal guard on) and the flags.
fn english_engine(flags: u32) -> Engine {
    let mut e = engine(Layout::Standard, flags, false);
    e.set_english(true).unwrap();
    e.set_english_overwrite(false).unwrap();
    e.set_english_key_order(true).unwrap();
    e.set_english_reversal_guard(true).unwrap();
    e
}
fn raw(e: &mut Engine, keys: &str) -> Output {
    let mut last = None;
    for c in keys.chars() {
        last = Some(match c {
            ' ' => press(e, KeyKind::Space),
            c if c.is_ascii_uppercase() => e.key(Key::ch(c, MOD_SHIFT)).unwrap(),
            c => e.key(Key::ch(c, 0)).unwrap(),
        });
    }
    last.unwrap()
}

/// §2.3 step 4: with bit 5 alone, Space does not take a syllable one of whose columns was overwritten while typing (the English words below: `line` is ㄠ ㄛ ㄙ ㄍ),
/// so they revert to English (`revert_missing`); a word typed in column order that the table reads (`go`) is taken; a tone key takes either (`v u o 3` is
/// ㄒㄧㄟˇ); with the bit off the same keys are English; Shift+letter English is English whatever the flags say.
#[test]
fn bit_5_alone_leaves_multi_key_lowercase_english_to_english() {
    for word in ["line", "live", "steam", "wifi", "sony", "mac", "bug", "max", "hello", "paper"] {
        let mut e = english_engine(ONE);
        let o = raw(&mut e, &format!("{word} "));
        assert!(o.preedit.to_lowercase().starts_with(word) && !o.preedit.chars().any(|c| c as u32 >= 0x3400), "{word}: stays English, got {}", o.preedit);
    }
    let mut e = english_engine(ONE);
    let o = raw(&mut e, "go ");
    assert!(is_han(&o.preedit), "go: no column was overwritten, so the one-symbol rule takes it: {}", o.preedit);
    let mut off = english_engine(0);
    assert!(raw(&mut off, "go ").preedit.starts_with("go"), "off: English");
    // A tone key: the Threads case.
    let mut e = english_engine(ONE);
    let o = raw(&mut e, "vuo3");
    assert!(is_han(&o.preedit) && e.syllables() == [SLIP], "{}", o.preedit);
    let mut off = english_engine(0);
    assert_eq!(raw(&mut off, "vuo3").preedit, "vuo3", "off: the (c) revert to English, as before");
    for flags in [ONE, ONE | FUZZY_ADJACENT, FUZZY_ALL] {
        let mut e = english_engine(flags);
        assert_eq!(raw(&mut e, "Go ").preedit.trim_end(), "Go", "flags {flags}");
    }
}

// ---- switching the flags with a composition open (docs/contracts/typo-one-sym.md §2.4 item 3) ----

fn composed(layout: Layout, flags: u32, typed: &[&str]) -> (Engine, String) {
    let mut e = engine(layout, flags, false);
    e.set_prediction(false).unwrap();
    let shown = type_syls_in(&mut e, layout, typed).preedit;
    assert!(is_han(&shown), "premise: accepted, got {shown}");
    (e, shown)
}
/// Sets the flags, which must not fail nor change the text; then types a syllable and sends the composition.
fn switch(e: &mut Engine, flags: u32, shown: &str, what: &str) {
    let o = e.set_fuzzy(flags).unwrap_or_else(|err| panic!("{what}: set_fuzzy({flags}) failed: {err:?}"));
    assert_eq!(o.preedit, shown, "{what}: the text is as it was");
}
fn finish(e: &mut Engine, layout: Layout, what: &str) {
    let o = type_syls_in(e, layout, &["ㄉㄠˋ"]);
    assert!(is_han(&o.preedit), "{what}: typing goes on: {}", o.preedit);
    let commit = e.key(Key::new(KeyKind::Enter)).unwrap_or_else(|err| panic!("{what}: Enter failed: {err:?}")).commit;
    assert!(is_han(&commit), "{what}: {commit}");
}

/// §4.1 (2): ㄐㄝ (only the adjacent table has it) and ㄅㄩ (read as 憋, a reading the one-symbol table lacks), accepted by bit 4 or bits 4 and 5, then bit 5 is turned
/// on without bit 4 (16 -> 32, 48 -> 32, 16 -> 0 -> 32): the text does not change, no Error, and the composition goes on. 16 -> 0 alone keeps the syllables.
#[test]
fn turning_bit_5_on_alone_keeps_what_the_adjacent_table_read() {
    premises();
    for (syl, flags_from) in [(ONLY_ADJ, 16), (UNKNOWN, 16), (ONLY_ADJ, 48), (UNKNOWN, 48)] {
        let what = format!("{syl} {flags_from} -> 32");
        let (mut e, shown) = composed(Layout::Standard, flags_from, &[syl]);
        switch(&mut e, 32, &shown, &what);
        assert!(shared().lex.has_syllable(&e.syllables()[0]), "{what}: stored as a lexicon reading: {:?}", e.syllables());
        finish(&mut e, Layout::Standard, &what);
        if flags_from == 16 {
            let what = format!("{syl} 16 -> 0 -> 32");
            let (mut e, shown) = composed(Layout::Standard, 16, &[syl]);
            switch(&mut e, 0, &shown, &what);
            assert_eq!(e.syllables(), [syl], "{what}: 16 -> 0 keeps the syllable (accepted_syllable_survives_turning_bit_4_off)");
            switch(&mut e, 32, &shown, &what);
            assert!(shared().lex.has_syllable(&e.syllables()[0]), "{what}");
            finish(&mut e, Layout::Standard, &what);
        }
    }
    // 32 -> 33 (a sound bit joins, still bit 5 alone) is no switch of tables: the syllable stays as typed.
    let (mut e, shown) = composed(Layout::Standard, 32, &[ONLY_ONE]);
    switch(&mut e, 33, &shown, "32 -> 33");
    assert_eq!(e.syllables(), [ONLY_ONE]);
}

/// §4.1 (3), standard and ETen: bit 5 turned off (32 -> 0, ETen 48 -> 16) stores what it read as the lexicon reading, so the text stays, the syllables only that table
/// knew (ㄋㄟ) have a path, and the standard keyboard's neighbour table never shows through: no 憋 for ㄅㄩ, no 伈 for ㄒㄧㄟˇ in the window.
#[test]
fn turning_bit_5_off_keeps_the_text_and_never_leaks_the_adjacent_table() {
    premises();
    let typed = ["ㄨㄛˇ", SLIP, "ㄑㄩˋ"];
    for (layout, from, to) in [(Layout::Standard, 32, 0), (Layout::Eten, 32, 0), (Layout::Eten, 48, 16), (Layout::Standard, 48, 0)] {
        let what = format!("{} {from} -> {to}", lname(layout));
        let (mut e, shown) = composed(layout, from, &typed);
        assert_eq!(shown, "我想去", "{what}");
        switch(&mut e, to, &shown, &what);
        assert!(e.syllables().iter().all(|s| shared().lex.has_syllable(s)), "{what}: {:?}", e.syllables());
        press(&mut e, KeyKind::Left);
        let o = press(&mut e, KeyKind::Space);
        let list = window_list(&mut e, o);
        assert!(!list.iter().any(|w| w == "伈" || w == "憋"), "{what}: no standard-keyboard word: {list:?}");
        press(&mut e, KeyKind::Esc);
        finish(&mut e, layout, &what);
    }
    for (layout, from, to) in [(Layout::Eten, 32, 0), (Layout::Eten, 48, 16), (Layout::Standard, 32, 0)] {
        let what = format!("{} {UNKNOWN} {from} -> {to}", lname(layout));
        let (mut e, shown) = composed(layout, from, &[UNKNOWN]);
        switch(&mut e, to, &shown, &what);
        assert!(shown != "憋" || layout == Layout::Standard, "{what}");
        let o = press(&mut e, KeyKind::Space);
        let list = window_list(&mut e, o);
        assert!(layout == Layout::Standard || !list.iter().any(|w| w == "憋"), "{what}: {list:?}");
    }
    // ㄋㄟ, which only the one-symbol table knows, is stored too: turning bit 5 off must not leave a syllable no decoder path covers.
    let (mut e, shown) = composed(Layout::Standard, 32, &[ONLY_ONE]);
    switch(&mut e, 0, &shown, "ㄋㄟ 32 -> 0");
    assert!(shared().lex.has_syllable(&e.syllables()[0]));
    finish(&mut e, Layout::Standard, "ㄋㄟ 32 -> 0");
}

// ---- the learning guard (docs/contracts/typo-one-sym.md §2.4 item 4) ----

/// Re-picks the character of syllable `at` of an `n`-syllable composition: the first one-character word of its window other than the shown one.
fn repick(e: &mut Engine, at: usize, n: usize, shown: &str) {
    press(e, KeyKind::End);
    for _ in 0..n - (at + 1) {
        press(e, KeyKind::Left);
    }
    let cur: String = shown.chars().nth(at).unwrap().to_string();
    let mut o = press(e, KeyKind::Space);
    for _ in 0..o.total {
        let w = o.candidates[o.selected.unwrap()].clone();
        if w.chars().count() == 1 && w != cur {
            press(e, KeyKind::Enter);
            press(e, KeyKind::End);
            return;
        }
        o = press(e, KeyKind::Right);
    }
    panic!("no other one-character word at {at}");
}
/// The learner's records (context, reading, word) after typing `typed`, re-picking the characters at `picks` (standard layout, no left context, so the first
/// syllable's context is the sentence start) and sending the composition.
fn learned(flags: u32, typed: &[&str], picks: &[usize]) -> Vec<(String, Vec<String>, String)> {
    let mut e = engine(Layout::Standard, flags, true);
    e.set_prediction(false).unwrap();
    let shown = type_syls(&mut e, Layout::Standard, &typed.join(" ")).preedit;
    assert_eq!(e.syllables(), typed, "premise: every syllable was accepted");
    assert_eq!(shown.chars().count(), typed.len(), "premise: one character per syllable");
    for &at in picks {
        repick(&mut e, at, typed.len(), &shown);
    }
    press(&mut e, KeyKind::Enter);
    e.learner().records().iter().map(|r| (r.context.clone(), r.reading.clone(), r.word.clone())).collect()
}
/// The three operations of the contract on a composition around syllable `x`: neighbouring one-character re-picks (a new-word run), a re-pick before `x` at the sentence start
/// (the coarse `^>x` key), and a re-pick two syllables before `x` (the mid and fine keys).
fn learn_all(flags: u32, x: &str) -> [Vec<(String, Vec<String>, String)>; 3] {
    [learned(flags, &["ㄨㄛˇ", x, "ㄑㄩˋ"], &[0, 2]), learned(flags, &["ㄨㄛˇ", x], &[0]), learned(flags, &["ㄨㄛˇ", "ㄏㄠˇ", x], &[0])]
}
fn mentions(rs: &[(String, Vec<String>, String)], x: &str) -> bool {
    rs.iter().any(|(c, r, _)| c.contains(x) || r.iter().any(|s| s == x))
}

/// §4.1 (4): with bit 5 on, a syllable only bit 5 accepted (here ㄋㄟ and ㄒㄧㄟˇ, never picked from the window) is in no record: not in a new word's reading
/// (neighbouring one-character re-picks), not in the sentence-start keys (`^>x`, the mid key, the fine key).
#[test]
fn a_syllable_only_bit_5_accepted_is_never_learned() {
    premises();
    for x in [ONLY_ONE, SLIP] {
        for flags in [ONE, ONE | FUZZY_ADJACENT] {
            for (i, rs) in learn_all(flags, x).iter().enumerate() {
                assert!(!mentions(rs, x), "flags {flags}, {x}, operation {i}: {rs:?}");
            }
        }
    }
    // A control: with a legal syllable in the same places, the same operations do record something (the guard is not "no learning at all").
    for (i, rs) in learn_all(ONE, "ㄏㄠˇ").iter().enumerate() {
        assert!(!rs.is_empty(), "operation {i}");
    }
}

/// §4.1 (4a): with the flags 0 (and so no bit 5 position anywhere), a syllable the third-tone rule accepted that is no lexicon syllable is learned as ever: the new word,
/// and the sentence-start keys (coarse, mid, fine). The same under 32, where the position table marks it unaccepted and the guard leaves it alone.
#[test]
fn a_sandhi_syllable_is_learned_as_before_with_the_flags_0_and_with_bit_5() {
    let lex = &shared().lex;
    let t = lex
        .syllables()
        .iter()
        .filter(|s| s.ends_with('ˇ'))
        .map(|s| s.replace('ˇ', "ˊ"))
        .find(|s| !lex.has_syllable(s) && core::tone3::unaccepted(lex, s))
        .expect("a second-tone syllable the third-tone rule accepts that no lexicon reading has");
    let zero = learn_all(0, &t);
    assert!(mentions(&zero[0], &t) && mentions(&zero[1], &t) && mentions(&zero[2], &t), "{t}: the three operations learn it as before: {zero:?}");
    assert_eq!(learn_all(ONE, &t), zero, "{t}: bit 5 does not touch a sandhi syllable's learning");
}

/// §4.1 (4b): with the flags 16, ㄐㄝ (accepted by bit 4, no lexicon syllable) is learned as ever, in the same three operations. Bit 4 alone has no bit 5 position.
#[test]
fn an_adjacent_key_syllable_is_learned_as_before_with_the_flags_16() {
    premises();
    let rs = learn_all(16, ONLY_ADJ);
    assert!(mentions(&rs[0], ONLY_ADJ) && mentions(&rs[1], ONLY_ADJ) && mentions(&rs[2], ONLY_ADJ), "learned as before: {rs:?}");
    assert_eq!(learn_all(0b1_0001, ONLY_ADJ), learn_all(0b1_0001, ONLY_ADJ), "deterministic");
}

/// docs/contracts/typo-one-sym.md §2.4 item 4 and docs/spec/decoder.md §7: a word read through a one-symbol alternative is given under the lexicon reading it was found
/// under (`path_readings_with_fuzzy`), so a manual word the n-gram's first choice holds that way is protected by the rerank mask; without bit 5 the typed syllable
/// is given, as `path_readings` always did; and the sound bits (0-3) never reach it.
#[test]
fn path_readings_with_fuzzy_follows_the_flags() {
    premises();
    let lex = &shared().lex;
    let typed: Vec<String> = ["ㄨㄛˇ", SLIP, "ㄑㄩˋ"].iter().map(|s| s.to_string()).collect();
    let words = [("我", 1), ("想", 1), ("去", 1)];
    let want: Vec<String> = ["ㄨㄛˇ", "ㄒㄧㄤˇ", "ㄑㄩˋ"].iter().map(|s| s.to_string()).collect();
    assert_eq!(lex.path_readings_with_fuzzy(&typed, words, &[], ONE), want);
    assert_eq!(lex.path_readings_with_fuzzy(&typed, words, &[], ONE | FUZZY_ADJACENT), want, "both bits: the one-symbol reading is the second source");
    assert_eq!(lex.path_readings_with_fuzzy(&typed, words, &[], 0), typed, "no bit 5: not the one-symbol table");
    assert_eq!(lex.path_readings_with_fuzzy(&typed, words, &[], FUZZY_ADJACENT), typed);
    assert_eq!(lex.path_readings(&typed, words, &[]), typed, "the three-argument form is flags 0");
    // The sound bits are masked: with ㄓ/ㄗ on, 知 for a typed ㄗ is still given under the typed syllable.
    let z = vec!["ㄗ".to_string()];
    assert_eq!(lex.path_readings_with_fuzzy(&z, [("知", 1)], &[], 0b0010), z);
    assert_eq!(lex.path_readings_with_fuzzy(&z, [("知", 1)], &[], 0b0010 | ONE), z);
}

/// docs/contracts/typo-one-sym.md §4.1: the probe sentences (`fixtures/typo-one-sym-probe.tsv`, with the Threads case first: `v u o 3` for ㄒㄧㄤˇ) all come out as written with bit 5 on,
/// by the engine with the macOS defaults of English mixing; with the bit off the slip is refused or reverts and the text is not the sentence.
#[test]
fn typo_one_sym_probe_hits_with_bit_5_and_not_without() {
    let text = std::fs::read_to_string(root().join("core/tests/fixtures/typo-one-sym-probe.tsv")).unwrap();
    let mut rows = 0;
    for line in text.lines().filter(|l| !l.starts_with('#') && !l.is_empty()) {
        let f: Vec<&str> = line.split('\t').collect();
        assert_eq!(f.len(), 3, "{line}");
        let syls: Vec<&str> = f[1].split(' ').collect();
        let run = |flags: u32| {
            let mut e = english_engine(flags);
            e.set_prediction(false).unwrap();
            e.set_left_context(f[0]);
            type_syls_in(&mut e, Layout::Standard, &syls).preedit
        };
        assert_eq!(run(ONE), f[2], "{line}");
        assert_ne!(run(0), f[2], "{line}: without the bit the slip is no sentence");
        rows += 1;
    }
    assert!(rows >= 6, "{rows} rows");
}
