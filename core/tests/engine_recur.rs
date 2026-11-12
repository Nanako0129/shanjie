//! The context recurrence term in the engine (docs/spec/decoder.md, the context recurrence section): the set the engine keeps from
//! `set_left_context`, where it is cleared, the key that forgets a stale left text, and that the engine decodes as the evaluation CLI does. On the
//! real lexicon and n-gram model (data/lm/, fail loudly without them); the homophones are 反應 and 反映 (ㄈㄢˇ ㄧㄥˋ): with no left text the
//! n-gram shows 反應, with a left text that holds 反映 the term shows 反映 (both settings, semantic correction off and on, checked with the CLI).
use core::digits::DigitTable;
use core::engine::*;
use core::english_next::EnglishTable;
use core::learn::{context_key, SENTINEL};
use core::lm::{decode_from_fuzzy_scored, history, CappedLexicon, Lm, Profile, RecurGrams, RECUR_CHARS};
use core::{Lexicon, BEAM_S1};
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}
fn data(f: &str) -> PathBuf {
    let p = root().join("data/lm").join(f);
    assert!(p.exists(), "data/lm/{f} is needed: see data/*.sha256 and docs/releases/README.md");
    p
}

struct Shared {
    lex: Arc<Lexicon>,
    lm: Arc<Lm>,
    capped: Arc<CappedLexicon>,
    digits: Arc<DigitTable>,
}
/// Loaded once per test binary; engines get the same objects through `set_lm` (no reading term: the capped lexicon is built without it).
fn shared() -> &'static Shared {
    static S: OnceLock<Shared> = OnceLock::new();
    S.get_or_init(|| {
        let lm = Lm::load(&data("bigram.sjlm")).unwrap();
        let dir = root().join("data/lexicon");
        let (lex, pack_text) = load_lexicon_packs(&dir, None).unwrap();
        let overlay = capping_overlay(&dir, &pack_text).unwrap();
        let capped = Arc::new(CappedLexicon::new(lex.clone(), &overlay, &lm, None).unwrap());
        let digits = Arc::new(DigitTable::parse(&std::fs::read_to_string(dir.join("digits.tsv")).unwrap()).unwrap());
        Shared { lex, lm: Arc::new(lm), capped, digits }
    })
}

/// Standard layout, the shared model, chat.
fn make() -> Engine {
    let s = shared();
    let mut e = Engine::with_lexicon(s.lex.clone(), Layout::Standard);
    e.set_lm(s.lm.clone(), s.capped.clone());
    e
}

/// Holds 反映 (and five other pairs); ends in an English word, so `left_full` holds `Joe` and `left` is empty.
const WITH_A: &str = "昨天看到的反映讓人在意，我說Joe";
/// The same with 反應.
const WITH_B: &str = "昨天看到的反應讓人在意，我說Joe";
const READING: &str = "ㄈㄢˇ ㄧㄥˋ";
const A: &str = "反映";
const B: &str = "反應";

fn keys_of(syl: &str) -> Vec<Key> {
    let layout = Layout::Standard;
    let (mut v, mut toned) = (Vec::new(), false);
    for c in syl.chars() {
        match layout.key_of_tone(c) {
            Some(tk) => {
                v.push(Key::ch(tk, 0));
                toned = true;
            }
            None => v.push(Key::ch(layout.key_of_symbol(c).expect("symbol has a key"), 0)),
        }
    }
    if !toned {
        v.push(Key::new(KeyKind::Space));
    }
    v
}
/// Types the syllables (space separated); every output.
fn type_all(e: &mut Engine, reading: &str) -> Vec<Output> {
    reading.split(' ').flat_map(keys_of).map(|k| e.key(k).unwrap()).collect()
}
fn type_reading(e: &mut Engine, reading: &str) -> Output {
    type_all(e, reading).pop().unwrap()
}
fn key(kind: KeyKind) -> Key {
    Key::new(kind)
}
const CTRL_BS: Key = Key { kind: KeyKind::Char, ch: '\\', modifiers: MOD_CONTROL };
/// (`left_full_chars`, `recur_pairs`): the two things the engine keeps from the left text that a test can count.
fn held(e: &Engine) -> (usize, usize) {
    (e.left_full_chars(), e.recur_pairs())
}
/// What a person sees of an output.
fn seen(o: &Output) -> (String, Vec<String>, Option<usize>, String) {
    (o.preedit.clone(), o.candidates.clone(), o.selected, o.commit.clone())
}

/// A left text that is empty or one character long gives no pair: every output (the display after each key, the candidate window and its rows) and the
/// total score are bit for bit those of the engine with the term switched off.
#[test]
fn an_empty_or_one_character_left_text_decodes_as_with_the_term_off() {
    let reading = "ㄈㄢˇ ㄧㄥˋ ㄉㄡ ㄏㄣˇ ㄏㄠˇ";
    for left in ["", "反", "映", "a", "😀"] {
        let run = |on: bool| {
            let mut e = make();
            e.set_recur(on);
            e.set_left_context(left);
            assert_eq!(e.recur_pairs(), 0, "{left:?}");
            let mut outs: Vec<_> = type_all(&mut e, reading).iter().map(seen).collect();
            let total = e.total_score().unwrap().to_bits();
            for k in [KeyKind::Left, KeyKind::Left, KeyKind::Space, KeyKind::Right, KeyKind::Esc] {
                outs.push(seen(&e.key(key(k)).unwrap()));
            }
            (outs, total)
        };
        assert!(run(true) == run(false), "{left:?}");
    }
}

/// The homophones follow the left text, semantic correction off: none and B show B, A shows A; with the term off A shows B (the term does it).
#[test]
fn homophones_follow_the_left_text() {
    let first = |left: &str, on: bool| {
        let mut e = make();
        e.set_recur(on);
        e.set_left_context(left);
        type_reading(&mut e, READING).preedit
    };
    assert_eq!(first("", true), B);
    assert_eq!(first(WITH_A, true), A);
    assert_eq!(first(WITH_B, true), B);
    assert_eq!(first(WITH_A, false), B, "premise: without the term A's left text shows B");
}

/// The same with the semantic correction on (the App's engine: data folder, n-gram with the reading term, the small model, rerank at the standard level;
/// the job is run and applied after every key, as the replay's `each`).
#[test]
fn homophones_follow_the_left_text_with_the_semantic_correction() {
    let mut e = Engine::new(&root().join("data/lexicon"), Layout::Standard).unwrap();
    e.load_lm(&data("bigram.sjlm")).unwrap();
    e.load_tinylm(&data("tinylm.sjtl")).unwrap();
    e.set_rerank(true).unwrap();
    let mut first = |left: &str, on: bool| {
        e.set_recur(on);
        e.set_left_context(left);
        let mut shown = String::new();
        let mut jobs = 0;
        for k in READING.split(' ').flat_map(keys_of) {
            shown = e.key(k).unwrap().preedit;
            if let Some(job) = e.take_rerank_job() {
                jobs += 1;
                if let Some(o) = e.apply_rerank(job.run()).unwrap() {
                    shown = o.preedit;
                }
            }
        }
        assert!(jobs > 0, "a job ran for {left:?}");
        assert_eq!(e.reset(ResetMode::Commit).commit, shown);
        shown
    };
    assert_eq!(first("", true), B);
    assert_eq!(first(WITH_A, true), A);
    assert_eq!(first(WITH_B, true), B);
    assert_eq!(first(WITH_A, false), B, "premise: without the term A's left text shows B");
}

/// The set holds the pairs of two Han characters only (ASCII, digits, punctuation and an emoji break them; a 4-byte Han character is one) of the
/// last `RECUR_CHARS` characters, at most 63; what is typed in the composition never joins it.
#[test]
fn the_set_holds_han_pairs_of_the_last_64_characters() {
    let mut e = make();
    e.set_left_context("甲a乙丙1丁，戊😀己\u{20000}庚 the 辛壬");
    assert_eq!(e.recur_pairs(), 4, "乙丙, 己𠀀, 𠀀庚 and 辛壬");
    let han: String = (0..70).map(|i| char::from_u32(0x4E00 + i).unwrap()).collect();
    e.set_left_context(&han);
    assert_eq!(e.recur_pairs(), RECUR_CHARS - 1);
    // A pair repeated in the text is one pair of the set.
    e.set_left_context("反映反映，反映");
    assert_eq!(e.recur_pairs(), 2, "反映 and 映反");
    // The composition's text: A picked in the window, then the same reading again, shows what an engine with the term off shows (B: A typed before
    // it is not a left text).
    let run = |on: bool| {
        let mut e = make();
        e.set_recur(on);
        e.set_left_context("");
        type_reading(&mut e, READING);
        let mut o = e.key(key(KeyKind::Space)).unwrap();
        while o.candidates[o.selected.unwrap()] != A {
            o = e.key(key(KeyKind::Right)).unwrap();
        }
        assert_eq!(e.key(key(KeyKind::Enter)).unwrap().preedit, A);
        (type_reading(&mut e, READING).preedit, e.recur_pairs())
    };
    let on = run(true);
    assert_eq!(on, (format!("{A}{B}"), 0));
    assert_eq!(on, run(false));
}

/// Every call replaces the set (A then B decodes as B alone; the count is B's); "" (what the C ABI passes for NULL and for text that is not UTF-8,
/// tested in core/src/ffi.rs) empties it and decodes as no left text.
#[test]
fn each_left_text_replaces_the_last() {
    let mut e = make();
    e.set_left_context(WITH_A);
    assert_eq!(e.recur_pairs(), RecurGrams::from_text(WITH_A).len());
    e.set_left_context(WITH_B);
    assert_eq!(e.recur_pairs(), RecurGrams::from_text(WITH_B).len());
    assert_eq!(type_reading(&mut e, READING).preedit, B, "B's left text alone");
    e.reset(ResetMode::Discard);
    e.set_left_context(WITH_B);
    e.set_left_context(WITH_A);
    assert_eq!(type_reading(&mut e, READING).preedit, A, "A's left text alone");
    e.reset(ResetMode::Discard);
    e.set_left_context(WITH_A);
    e.set_left_context("");
    assert_eq!(held(&e), (0, 0));
    assert_eq!(type_reading(&mut e, READING).preedit, B, "as with no left text");
}

/// Every clear point empties `left_full` and the set together: a commit, Esc, both resets, the `MAX_SYLLABLES` auto-commit, a prediction row pick that
/// reaches `MAX_SYLLABLES`, and the four pass-throughs (key scheme 1's Shift+, behind an unfinished syllable and on nothing, its Shift+letter behind an
/// unfinished syllable, rule 22). Code 4 is core/src/ffi.rs `child_panic`. A new small model empties `left_full` only.
#[test]
fn every_clear_point_empties_both() {
    let set = |e: &mut Engine| {
        e.set_left_context(WITH_A);
        let h = held(e);
        assert!(h.0 == 3 && h.1 > 0, "premise: both held {h:?}");
        h
    };
    let mut e = make();
    set(&mut e);
    type_reading(&mut e, READING);
    assert!(!e.key(key(KeyKind::Enter)).unwrap().commit.is_empty());
    assert_eq!(held(&e), (0, 0), "commit");
    set(&mut e);
    type_reading(&mut e, READING);
    e.key(key(KeyKind::Esc)).unwrap();
    assert_eq!(held(&e), (0, 0), "Esc");
    for mode in [ResetMode::Commit, ResetMode::Discard] {
        set(&mut e);
        e.key(Key::ch('g', 0)).unwrap();
        e.reset(mode);
        assert_eq!(held(&e), (0, 0), "reset");
    }
    // The auto-commit: MAX_SYLLABLES units (Ctrl+\ types 、, one unit each).
    let h = set(&mut e);
    for _ in 1..MAX_SYLLABLES {
        assert!(e.key(CTRL_BS).unwrap().commit.is_empty());
    }
    assert_eq!(held(&e), h, "held while composing");
    let o = e.key(CTRL_BS).unwrap();
    assert!(o.commit.chars().count() == MAX_SYLLABLES && o.preedit.is_empty(), "premise: the auto-commit");
    assert_eq!(held(&e), (0, 0), "MAX_SYLLABLES");
    // A row pick that reaches MAX_SYLLABLES: MAX_SYLLABLES - 1 units, an unfinished ㄕ, Tab, Enter on the row's first item.
    let h = set(&mut e);
    for _ in 1..MAX_SYLLABLES {
        e.key(CTRL_BS).unwrap();
    }
    let o = e.key(Key::ch('g', 0)).unwrap();
    assert!(!o.candidates.is_empty(), "premise: the row");
    assert!(e.key(key(KeyKind::Tab)).unwrap().selected == Some(0), "premise: the row is entered");
    assert_eq!(held(&e), h, "held while composing");
    let o = e.key(key(KeyKind::Enter)).unwrap();
    assert!(o.commit.chars().count() >= MAX_SYLLABLES && o.preedit.is_empty(), "premise: the row pick sent everything");
    assert_eq!(held(&e), (0, 0), "row pick");
    // Key scheme 1, K9 and K3: Shift+, and Shift+letter behind an unfinished syllable commit the character; K8: Shift+, on nothing passes on.
    e.set_key_scheme(1).unwrap();
    for k in [Key::ch(',', MOD_SHIFT), Key::ch('k', MOD_SHIFT)] {
        set(&mut e);
        e.key(Key::ch('g', 0)).unwrap();
        let o = e.key(k).unwrap();
        assert!(o.handled && o.preedit.is_empty() && !o.commit.is_empty(), "premise: committed");
        assert_eq!(held(&e), (0, 0), "key scheme 1 behind an unfinished syllable");
    }
    set(&mut e);
    let o = e.key(Key::ch(',', MOD_SHIFT)).unwrap();
    assert!(!o.handled && o.preedit.is_empty(), "premise: K8 passes on");
    assert_eq!(held(&e), (0, 0), "K8");
    e.set_key_scheme(0).unwrap();
    // Rule 22: an unmapped character on an empty composition.
    set(&mut e);
    let o = e.key(Key::ch('é', 0)).unwrap();
    assert!(!o.handled && o.preedit.is_empty(), "premise: rule 22");
    assert_eq!(held(&e), (0, 0), "rule 22");
    // A new small model: `left_full` goes (it was cut for the model), the set stays.
    let h = set(&mut e);
    e.load_tinylm(&data("tinylm.sjtl")).unwrap();
    assert_eq!(held(&e), (0, h.1), "set_tinylm");
}

/// A key that starts a composition on an empty one without `set_left_context` before it (Ctrl+\ after Backspace to empty, or after Esc took the unfinished
/// symbols) decodes as with no left text; right after `set_left_context` the same keys use it.
#[test]
fn a_composition_started_without_a_fresh_left_text_has_none() {
    let shown = |left: &str, empty_it: &[KeyKind]| {
        let mut e = make();
        e.set_left_context(left);
        if !empty_it.is_empty() {
            e.key(Key::ch('z', 0)).unwrap(); // ㄈ
            for &k in empty_it {
                e.key(key(k)).unwrap();
            }
        }
        let o = e.key(CTRL_BS).unwrap();
        assert_eq!(o.preedit, "、");
        let held_then = held(&e);
        (type_reading(&mut e, READING).preedit, held_then)
    };
    assert_eq!(shown(WITH_A, &[]).0, format!("、{A}"), "premise: right after set_left_context, A's left text decides");
    let none = shown("", &[]).0;
    assert_eq!(none, format!("、{B}"));
    assert_eq!(shown(WITH_A, &[KeyKind::Backspace]), (none.clone(), (0, 0)), "Backspace to empty");
    assert_eq!(shown(WITH_A, &[KeyKind::Esc]), (none, (0, 0)), "Esc on the unfinished symbol alone");
}

/// A composition keeps the left text whatever it starts with: an English run (Shift+letter), an unfinished symbol, a digit buffer whose key shows as a
/// digit (English mixing on, the digit table loaded); then a syllable. The prediction row behind the English run is the one with the term off.
#[test]
fn a_composition_keeps_the_left_text() {
    let s = shared();
    let english = |on: bool| {
        let mut e = make();
        e.set_recur(on);
        e.set_english(true).unwrap();
        e.set_english_next(Some(Arc::new(EnglishTable::parse("E\tjoe\t200\nW\tjoe\t說\t30\nW\tjoe\t水\t10\n").unwrap())));
        let h = {
            e.set_left_context(WITH_A);
            held(&e)
        };
        let mut rows = Vec::new();
        for k in [Key::ch('j', MOD_SHIFT), Key::ch('o', 0), Key::ch('e', 0), key(KeyKind::Space), Key::ch('g', 0)] {
            rows.push(seen(&e.key(k).unwrap()));
            assert_eq!(held(&e), h, "English run");
        }
        rows
    };
    let rows = english(true);
    assert_eq!(rows.last().unwrap().1.first().map(String::as_str), Some("說"), "premise: the English row");
    assert_eq!(rows, english(false));
    let mut e = make();
    e.set_left_context(WITH_A);
    let h = held(&e);
    for o in type_all(&mut e, "ㄕˋ ㄏㄨˇ") {
        assert!(!o.preedit.is_empty());
        assert_eq!(held(&e), h, "unfinished symbol first");
    }
    let mut e = make();
    e.set_english(true).unwrap();
    e.set_digit_table(Some(s.digits.clone()));
    e.set_left_context(WITH_A);
    let h = held(&e);
    let o = e.key(Key::ch('8', 0)).unwrap();
    assert!(o.preedit == "8" && e.digits_syls().is_empty() && e.digits_typed().is_some(), "premise: only the digit buffer shows");
    assert_eq!(held(&e), h, "digit buffer");
    e.key(Key::ch('4', 0)).unwrap();
    assert_eq!(held(&e), h, "the key after the digit buffer");
    for _ in type_all(&mut e, "ㄋㄧˇ") {
        assert_eq!(held(&e), h, "a syllable after it");
    }
}

/// The engine decodes as the evaluation CLI's function (`decode_from_fuzzy_scored` with the set `RecurGrams::from_text` builds from the same text and the
/// start the CLI takes): the same count, the same first path, and `total_score` is that path's score (the gain rides in the words' deltas).
#[test]
fn the_engine_and_the_cli_decode_alike() {
    let s = shared();
    let left = "昨天看到的反映讓人在意，";
    let reading = "ㄈㄢˇ ㄧㄥˋ ㄏㄣˇ ㄎㄨㄞˋ";
    let g = RecurGrams::from_text(left);
    let k = context_key(left);
    let start = history(if k == SENTINEL { "" } else { &k }, &s.lm);
    let syls: Vec<String> = reading.split(' ').map(String::from).collect();
    let cli = |g: Option<&RecurGrams>| {
        let nb = decode_from_fuzzy_scored(&s.capped, &syls, &s.lm, Profile::Chat, BEAM_S1, start, true, 0, core::fuzzy::DELTA_SOUND, g).unwrap();
        (nb[0].0, nb[0].1.iter().map(|w| w.0).collect::<String>())
    };
    let (score, text) = cli(Some(&g));
    assert_ne!(text, cli(None).1, "premise: the set changes the first path");
    let mut e = make();
    e.set_left_context(left);
    assert_eq!(e.recur_pairs(), g.len());
    assert_eq!(type_reading(&mut e, reading).preedit, text);
    let total = e.total_score().unwrap();
    assert!((total - score).abs() < 1e-9, "{total} {score}");
}

/// Two engines share one model (`set_lm`); a left text set on one does not reach the other's decode.
#[test]
fn engines_sharing_a_model_keep_their_own_set() {
    let (mut one, mut two) = (make(), make());
    one.set_left_context(WITH_A);
    two.set_left_context("");
    assert_eq!(type_reading(&mut two, READING).preedit, B);
    assert_eq!(type_reading(&mut one, READING).preedit, A, "premise: the set decides in its own engine");
    two.reset(ResetMode::Discard);
    two.set_left_context("");
    assert_eq!(type_reading(&mut two, READING).preedit, B, "decoding the other engine after it");
    assert_eq!(two.recur_pairs(), 0);
}

/// A terminal-like app passes only the Han tail (≤ 2 characters): at most one pair. When it is the word typed next, it counts: 反映 alone shows 反映.
#[test]
fn a_two_character_han_tail_is_one_pair() {
    let mut e = make();
    e.set_left_context(A);
    assert_eq!(e.recur_pairs(), 1);
    assert_eq!(type_reading(&mut e, READING).preedit, A);
    e.reset(ResetMode::Discard);
    e.set_left_context(B);
    assert_eq!(type_reading(&mut e, READING).preedit, B);
}
