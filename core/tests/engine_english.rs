//! zhen-revert engine tests (docs/contracts/zhen-revert.md section 5.1): the direct revert to the raw keys (an overwrite, a key
//! out of column order, a syllable the lexicon lacks), the English run and its keys, the tone-key split, Esc back to the Zhuyin, the
//! short-word items at the end of the candidate list, the two switches, and the English token itself (zhen-inline sections 1.0-1.3h,
//! which this contract keeps), on the real lexicon.
//!
//! Key script, whitespace separated: a single character is that keycap, `_` Space, `Enter`, `Esc`, `BS` Backspace, `Left`,
//! `Right`, `Down`, `S-Enter` and `S-,` with Shift, `Cmd-BS` with Command.
//!
//! What the keys of the tiny examples are (standard layout, column 0 initial / 1 medial / 2 final):
//!   ok    ㄟ(2) ㄜ(2): the same column, (a).        ls   ㄠ(2) ㄋ(0): ㄋ is in a column before ㄠ's, (b).
//!   cup   ㄏㄧㄣ in order, and the syllable is missing: (c) at Space.      rust  ㄐㄧ then ㄋ over ㄐ: (a) at the third key.
use core::engine::*;
use core::lm::{CappedLexicon, Lm, Profile};
use core::Lexicon;
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}
fn real() -> Arc<Lexicon> {
    static L: OnceLock<Arc<Lexicon>> = OnceLock::new();
    L.get_or_init(|| load_lexicon(&root().join("data/lexicon")).unwrap()).clone()
}
/// Standard layout, English mixing on, the three switches at their defaults.
fn eng() -> Engine {
    let mut e = Engine::with_lexicon(real(), Layout::Standard);
    e.set_english(true).unwrap();
    e
}
/// The engine for the tests of the English runs that Shift opens (the name stays from the contract's examples).
fn win() -> Engine {
    eng()
}
fn key_of(tok: &str) -> Key {
    let with = |kind, modifiers| Key { kind, ch: '\0', modifiers };
    match tok {
        "_" => Key::new(KeyKind::Space),
        "Enter" => Key::new(KeyKind::Enter),
        "Esc" => Key::new(KeyKind::Esc),
        "Tab" => Key::new(KeyKind::Tab),
        "BS" => Key::new(KeyKind::Backspace),
        "Delete" => Key::new(KeyKind::Delete),
        "Left" => Key::new(KeyKind::Left),
        "Right" => Key::new(KeyKind::Right),
        "Home" => Key::new(KeyKind::Home),
        "End" => Key::new(KeyKind::End),
        "Down" => Key::new(KeyKind::Down),
        "Up" => Key::new(KeyKind::Up),
        "S-Enter" => with(KeyKind::Enter, MOD_SHIFT),
        "Cmd-BS" => with(KeyKind::Backspace, MOD_COMMAND),
        t if t.starts_with("S-") => Key::ch(t[2..].chars().next().unwrap(), MOD_SHIFT),
        t => {
            assert!(t.chars().count() == 1, "unknown token {t}");
            Key::ch(t.chars().next().unwrap(), 0)
        }
    }
}
/// Sends the script; the last output.
fn run(e: &mut Engine, script: &str) -> Output {
    let mut last = None;
    for tok in script.split_whitespace() {
        last = Some(e.key(key_of(tok)).unwrap());
    }
    last.expect("a script")
}
/// Every output of the script.
fn run_all(e: &mut Engine, script: &str) -> Vec<Output> {
    script.split_whitespace().map(|t| e.key(key_of(t)).unwrap()).collect()
}
/// The keys of one syllable ("ㄍㄠ": Zhuyin keys, then the tone key or Space), as the replays send it.
fn syllable(e: &mut Engine, syl: &str) -> Output {
    let mut last = None;
    for k in keys_of(syl) {
        last = Some(e.key(k).unwrap());
    }
    last.unwrap()
}
/// The keys of one syllable ("ㄋㄧˇ": Zhuyin keys, then the tone key; Space when it has no tone mark), standard layout.
fn keys_of(syl: &str) -> Vec<Key> {
    let layout = Layout::Standard;
    let mut v = Vec::new();
    let mut toned = false;
    for c in syl.chars() {
        match layout.key_of_tone(c) {
            Some(tk) => {
                v.push(Key::ch(tk, 0));
                toned = true;
            }
            None => v.push(Key::ch(layout.key_of_symbol(c).expect("a symbol with a key"), 0)),
        }
    }
    if !toned {
        v.push(Key::new(KeyKind::Space));
    }
    v
}
/// What the composition shows when it is the one syllable `syl`: the proof that `syls == [syl]` without an accessor.
fn shows(syl: &str) -> String {
    let mut e = Engine::with_lexicon(real(), Layout::Standard);
    syllable(&mut e, syl).preedit
}
/// Whether the composition is empty: a key on an empty composition passes through (rule 22).
fn is_empty(e: &mut Engine) -> bool {
    !e.key(Key::new(KeyKind::Left)).unwrap().handled
}
fn tmp_dir(name: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("shanjie-english-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    d
}
/// The run's (start, length) fields of an output.
fn fields(o: &Output) -> (u32, u32) {
    (o.english_run_utf16_start, o.english_run_utf16_len)
}
/// The first lexicon entry of a syllable: the Chinese item of a token list.
fn first_entry(syl: &str) -> String {
    real().entries(&[syl.to_string()]).first().expect("a syllable of the lexicon").0.to_string()
}
/// 127 punctuation marks: 127 units, one short of the limit.
fn nearly_full(e: &mut Engine) -> String {
    for _ in 0..MAX_SYLLABLES - 1 {
        e.key(Key::ch(',', MOD_SHIFT)).unwrap();
    }
    "，".repeat(MAX_SYLLABLES - 1)
}
/// Types the three syllables of 我在用 (whatever the decoder shows for them) and returns what is shown.
fn three(e: &mut Engine) -> String {
    syllable(e, "ㄨㄛˇ");
    syllable(e, "ㄗㄞˋ");
    let zh = syllable(e, "ㄩㄥˋ").preedit;
    assert!(zh.chars().count() == 3, "premise: three syllables show three characters: {zh}");
    zh
}

/// The real lexicon with the real bigram model (the switch and learning on), a fixed clock; for the tests where the decoder's learned
/// boost matters.
fn lm_parts() -> (Arc<Lm>, Arc<CappedLexicon>) {
    static S: OnceLock<(Arc<Lm>, Arc<CappedLexicon>)> = OnceLock::new();
    S.get_or_init(|| {
        let lm = Lm::load(&root().join("data/lm/bigram.sjlm")).unwrap();
        let overlay = std::fs::read_to_string(root().join("data/lexicon/overlay-add.tsv")).unwrap();
        let capped = Arc::new(CappedLexicon::new(real(), &overlay, &lm, None).unwrap());
        (Arc::new(lm), capped)
    })
    .clone()
}
fn lm_eng() -> Engine {
    let (lm, capped) = lm_parts();
    let mut e = eng();
    e.set_lm(lm, capped);
    e.set_today(Some(20_000));
    e.set_learning(true);
    e
}
/// `lm_eng` for the tests that pick from the lexicon window with `choose_word`: that window lists a prediction row first
/// (predict-down contract B.4), so the row is off.
fn lm_eng_lexicon() -> Engine {
    let mut e = lm_eng();
    e.set_prediction(false).unwrap();
    e
}
/// Opens the candidates for the syllables before the cursor (the longest words first) and chooses `word`: it is fixed in the
/// composition.
fn choose_word(e: &mut Engine, word: &str) {
    let mut o = e.key(Key::new(KeyKind::Space)).unwrap();
    for _ in 0..5000 {
        if o.candidates[o.selected.unwrap()] == word {
            e.key(Key::new(KeyKind::Enter)).unwrap();
            return;
        }
        o = e.key(Key::new(KeyKind::Right)).unwrap();
    }
    panic!("candidate {word} not found");
}
/// With a candidate window open: moves the selection to `word` and presses Enter.
fn select_in_window(e: &mut Engine, word: &str) {
    let mut o = e.key(Key::new(KeyKind::Right)).unwrap();
    for _ in 0..5000 {
        if o.candidates.is_empty() {
            break;
        }
        if o.candidates[o.selected.unwrap()] == word {
            e.key(Key::new(KeyKind::Enter)).unwrap();
            return;
        }
        o = e.key(Key::new(KeyKind::Right)).unwrap();
    }
    panic!("candidate {word} not found");
}
fn records(e: &Engine) -> Vec<(String, String)> {
    e.learner().records().iter().map(|r| (r.context.clone(), r.word.clone())).collect()
}

#[test]
fn token_next_key_is_the_fixed_en_for_learning_and_lookup() {
    // 我是 (the default) re-picked as 我試, then `rust` kept after it: the record's key holds the fixed reading, not the word.
    let dir = tmp_dir("token-key");
    let mut e = lm_eng_lexicon();
    e.learning_open(&dir).unwrap();
    syllable(&mut e, "ㄨㄛˇ");
    let shown = syllable(&mut e, "ㄕˋ").preedit;
    assert!(shown == "我是", "premise: the unlearned default is {shown}");
    choose_word(&mut e, "我試");
    run(&mut e, "r u s t _");
    let o = run(&mut e, "Enter");
    assert!(o.commit == "我試rust", "{}", o.commit);
    // The learning file's bytes: one record, under ^>_punct_en, for 我試; no trace of the English word.
    let bytes = std::fs::read(dir.join("learning.tsv")).unwrap();
    let text = String::from_utf8(bytes).unwrap();
    assert!(text.contains("我試"), "the word is in the file: {text}");
    assert!(text.contains("^>_punct_en") && !text.contains("rust"), "the key is the fixed one: {text}");
    assert!(records(&e) == [("^>_punct_en".to_string(), "我試".to_string())], "{:?}", records(&e));
    // Typing the same again (no re-pick) shows 我試: the decoder looked the record up under the same key.
    syllable(&mut e, "ㄨㄛˇ");
    syllable(&mut e, "ㄕˋ");
    let o = run(&mut e, "r u s t _");
    assert!(o.preedit == "我試rust", "the lookup key matches the record's: {}", o.preedit);
    // And the word alone (no English after it) is the unlearned default: the record is under the key for an English neighbour.
    e.key(Key::new(KeyKind::Esc)).unwrap();
    e.key(Key::new(KeyKind::Esc)).unwrap();
    syllable(&mut e, "ㄨㄛˇ");
    assert!(syllable(&mut e, "ㄕˋ").preedit == "我是", "no English after it is another key");
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn down_after_the_token_lists_its_three_writings_and_space_types_a_space() {
    let mut e = eng();
    run(&mut e, "r u s t _");
    let o = run(&mut e, "Down");
    assert!(o.candidates == ["rust", "Rust", "RUST"] && o.selected == Some(0) && o.total == 3, "{:?}", o.candidates);
    let o = run(&mut e, "2");
    assert!(o.preedit == "Rust" && o.commit.is_empty() && o.candidates.is_empty());
    // Again on the new writing (zhen-inline 1.0, revision 2): the text as it stands first, then lower, Capitalised, UPPER, no repeat.
    assert!(run(&mut e, "Down").candidates == ["Rust", "rust", "RUST"]);
    // Space at the same place opens nothing (zhen-inline 1.3e, revision 3): it types a space unit after the token.
    let mut e = eng();
    run(&mut e, "r u s t _");
    let o = run(&mut e, "_");
    assert!(o.handled && o.candidates.is_empty() && o.selected.is_none() && o.preedit == "rust " && o.commit.is_empty(), "{:?}", o.preedit);
    // A punctuation mark still lists its own table with Space.
    let mut e = eng();
    run(&mut e, "S-,");
    assert!(run(&mut e, "_").candidates.first().map(String::as_str) == Some("，"), "a mark's table");
}

#[test]
fn english_stays_and_the_composition_goes_on() {
    let mut e = eng();
    let zh = {
        syllable(&mut e, "ㄨㄛˇ");
        syllable(&mut e, "ㄗㄞˋ").preedit
    };
    let o = run(&mut e, "r u s t _");
    let want = format!("{zh}rust");
    assert!(o.commit.is_empty() && o.preedit == want, "not sent: {}", o.preedit);
    assert!(o.cursor_utf16 as usize == want.encode_utf16().count(), "the cursor is last");
    // Keep typing: 編譯 after it, then one Enter sends the lot.
    syllable(&mut e, "ㄅㄧㄢ");
    let o = syllable(&mut e, "ㄧˋ");
    assert!(o.preedit.starts_with(&want) && o.commit.is_empty());
    let tail = o.preedit.strip_prefix(want.as_str()).unwrap().to_string();
    assert!(tail.chars().count() == 2);
    assert!(run(&mut e, "Enter").commit == format!("{want}{tail}"));

    // Left skips it as one cell; Backspace behind `rust` takes one letter (zhen-inline section 1.3g).
    let mut e = eng();
    syllable(&mut e, "ㄨㄛˇ");
    let zh = syllable(&mut e, "ㄗㄞˋ").preedit;
    run(&mut e, "r u s t _");
    let o = run(&mut e, "Left");
    assert!(o.preedit == format!("{zh}rust") && o.cursor_utf16 as usize == zh.encode_utf16().count(), "one Left jumps over the word");
    let o = run(&mut e, "Right");
    assert!(o.cursor_utf16 as usize == format!("{zh}rust").encode_utf16().count());
    let o = run(&mut e, "BS");
    assert!(o.preedit == format!("{zh}rus") && (o.english_run_utf16_start, o.english_run_utf16_len) == (zh.encode_utf16().count() as u32, 3), "one Backspace deletes one letter: {}", o.preedit);
}

#[test]
fn english_token_is_a_boundary_for_the_context_and_for_learning() {
    // Like the punctuation test of engine_learn.rs: the word after `rust` has the key ^>$ (the context is not a Han character),
    // so what was taught there is found there, and the same word after 他 alone (key 他) is another key.
    let other = |shown: &str| if shown == "權力" { "全力" } else { "權力" };
    let mut e = lm_eng();
    e.set_prediction(false).unwrap(); // predict-down B.4: choose_word picks the lexicon entry, not the row's copy
    syllable(&mut e, "ㄊㄚ");
    run(&mut e, "r u s t _");
    syllable(&mut e, "ㄑㄩㄢˊ");
    let shown = syllable(&mut e, "ㄌㄧˋ").preedit;
    let w = other(shown.trim_start_matches("他rust")).to_string();
    choose_word(&mut e, &w);
    let o = run(&mut e, "Enter");
    assert!(o.commit == format!("他rust{w}"));
    assert!(records(&e) == [("^>$".to_string(), w.clone())], "after the English the key is ^, nothing follows the word: {:?}", records(&e));
    syllable(&mut e, "ㄊㄚ");
    run(&mut e, "r u s t _");
    syllable(&mut e, "ㄑㄩㄢˊ");
    assert!(syllable(&mut e, "ㄌㄧˋ").preedit == format!("他rust{w}"), "decoding after the word finds it");
    e.key(Key::new(KeyKind::Esc)).unwrap();
    syllable(&mut e, "ㄊㄚ");
    syllable(&mut e, "ㄑㄩㄢˊ");
    assert!(syllable(&mut e, "ㄌㄧˋ").preedit != format!("他{w}"), "without the word between, the key is 他, not ^");
}

#[test]
fn the_prediction_row_after_the_token_is_recomputed_and_empty() {
    // Section 1.0: the row follows the rule for a chosen candidate (recomputed). With the token at the cursor every start would
    // span it, and a start across a token reads nothing, so what is recomputed is empty - while the row before the token was not.
    let mut e = lm_eng();
    let before = syllable(&mut e, "ㄨㄛˇ");
    assert!(!before.candidates.is_empty(), "premise: a syllable has a prediction row");
    let o = run(&mut e, "r u s t _");
    assert!(o.candidates.is_empty() && o.selected.is_none() && o.total == 0 && o.preedit.ends_with("rust"), "{:?}", o.candidates);
}

#[test]
fn the_unit_limit_counts_the_word() {
    // 127 syllables then the English token is the 128th unit: everything is sent (s3d section 1).
    let mut e = eng();
    for _ in 0..MAX_SYLLABLES - 1 {
        syllable(&mut e, "ㄅㄚ");
    }
    let o = run(&mut e, "r u s t _");
    assert!(o.commit.chars().count() == MAX_SYLLABLES - 1 + 4 && o.commit.ends_with("rust"), "{}", o.commit);
    assert!(o.preedit.is_empty() && is_empty(&mut e));
}

#[test]
fn backspace_after_an_english_token_deletes_one_letter_at_a_time() {
    let fields = |o: &Output| (o.english_run_utf16_start, o.english_run_utf16_len);
    // A token from the English window: the last letter goes and the rest is a run; three more take the run away.
    let mut e = eng();
    let zh = three(&mut e);
    run(&mut e, "r u s t _");
    let o = run(&mut e, "BS");
    assert!(o.preedit == format!("{zh}rus") && fields(&o) == (3, 3), "{}", o.preedit);
    let o = run(&mut e, "BS BS");
    assert!(o.preedit == format!("{zh}r") && fields(&o) == (3, 1));
    let o = run(&mut e, "BS");
    assert!(o.preedit == zh && fields(&o) == (0, 0), "an emptied run closes: {}", o.preedit);
    assert!(run(&mut e, "Enter").commit == zh);
    // The reopened run is a run: Esc takes the whole word, Space makes it a token again.
    let mut e = eng();
    run(&mut e, "r u s t _ BS");
    assert!(run(&mut e, "Esc").preedit.is_empty() && is_empty(&mut e));
    let mut e = eng();
    run(&mut e, "r u s t _ BS");
    let o = run(&mut e, "_");
    assert!(o.preedit == "rus" && fields(&o) == (0, 0));
    // The case is as it stands: the writing `Rust` chosen with Down gives `Rus`.
    let mut e = eng();
    run(&mut e, "r u s t _ Down 2");
    assert!(run(&mut e, "BS").preedit == "Rus");

    // A one-letter token just goes; no run.
    let mut e = win();
    let zh = three(&mut e);
    run(&mut e, "S-a _");
    let o = run(&mut e, "BS");
    assert!(o.preedit == zh && fields(&o) == (0, 0));
    assert!(run(&mut e, "c").preedit == format!("{zh}ㄏ"), "no run is open");
    let mut e = win();
    run(&mut e, "S-a _");
    run(&mut e, "BS");
    assert!(is_empty(&mut e));

    // The token has units after it (Left moved the cursor): the run opens in the middle and they stay.
    let mut e = eng();
    let zh = three(&mut e);
    run(&mut e, "r u s t _");
    syllable(&mut e, "ㄅㄧㄢ");
    let o = syllable(&mut e, "ㄧˋ");
    let tail = o.preedit.strip_prefix(format!("{zh}rust").as_str()).unwrap().to_string();
    run(&mut e, "Left Left");
    let o = run(&mut e, "BS");
    assert!(o.preedit == format!("{zh}rus{tail}") && fields(&o) == (3, 3), "{}", o.preedit);
    assert!(o.cursor_utf16 == 6, "the cursor is after the run");
    let o = run(&mut e, "_");
    assert!(o.preedit == format!("{zh}rus{tail}") && fields(&o) == (0, 0));
    assert!(run(&mut e, "Enter").commit == format!("{zh}rus{tail}"));

    // A run's token (Shift+letter) too.
    let mut e = win();
    run(&mut e, "S-w i n d o w s _");
    let o = run(&mut e, "BS");
    assert!(o.preedit == "Window" && fields(&o) == (0, 6));
}

#[test]
fn backspace_still_takes_space_units_punctuation_and_tokens_without_mixing() {
    // A space unit and a punctuation token go in one Backspace, without opening a run.
    let fields = |o: &Output| (o.english_run_utf16_start, o.english_run_utf16_len);
    let mut e = eng();
    run(&mut e, "r u s t _ _");
    let o = run(&mut e, "BS");
    assert!(o.preedit == "rust" && fields(&o) == (0, 0));
    let mut e = eng();
    run(&mut e, "r u s t _ S-,");
    let o = run(&mut e, "BS");
    assert!(o.preedit == "rust" && fields(&o) == (0, 0));
    // Mixed typing off: a token that is still there goes whole.
    let mut e = eng();
    run(&mut e, "r u s t _");
    e.set_english(false).unwrap();
    let o = run(&mut e, "BS");
    assert!(o.preedit.is_empty() && fields(&o) == (0, 0), "{}", o.preedit);
}

#[test]
fn shift_letter_starts_a_run_on_an_empty_composition() {
    for mut e in [win()] {
        let o = run(&mut e, "S-w");
        assert!(o.handled && o.commit.is_empty() && o.preedit == "W" && o.cursor_utf16 == 1, "{}", o.preedit);
        let o = run(&mut e, "i n d o w s");
        assert!(o.commit.is_empty() && o.preedit == "Windows" && o.cursor_utf16 == 7, "{}", o.preedit);
        assert!(o.candidates.is_empty() && e.english_shadow().is_empty(), "no window, no shadow");
    }
}

#[test]
fn run_after_chinese_ends_with_space_and_the_composition_goes_on() {
    let mut e = win();
    let zh = three(&mut e);
    let o = run(&mut e, "S-w i n d o w s");
    assert!(o.preedit == format!("{zh}Windows") && o.commit.is_empty());
    let o = run(&mut e, "_");
    assert!(o.handled && o.commit.is_empty() && o.preedit == format!("{zh}Windows"), "the space is eaten: {}", o.preedit);
    assert!(o.candidates.is_empty(), "no window");
    // 很好 after it, one Enter sends the lot.
    syllable(&mut e, "ㄏㄣˇ");
    let shown = syllable(&mut e, "ㄏㄠˇ").preedit;
    let tail = shown.strip_prefix(&format!("{zh}Windows")).expect("Windows stays in front").to_string();
    assert!(tail.chars().count() == 2);
    let o = run(&mut e, "Enter");
    assert!(o.commit == format!("{zh}Windows{tail}"), "{}", o.commit);
}

#[test]
fn run_is_never_auto_kept_and_backspace_takes_one_letter() {
    // Amendment three (1.3b): `Windows` then an unshifted `c` is the run `Windowsc`, not the token `Windows` and a syllable ㄏ.
    // Both displays.
    for mk in [win as fn() -> Engine] {
        let mut e = mk();
        let zh = three(&mut e);
        run(&mut e, "S-w i n d o w s");
        let o = run(&mut e, "c");
        assert!(o.preedit == format!("{zh}Windowsc") && o.commit.is_empty(), "{}", o.preedit);
        // One Backspace takes one letter, not the whole word.
        let o = run(&mut e, "BS");
        assert!(o.preedit == format!("{zh}Windows"), "{}", o.preedit);
        // Space ends the run (eaten); 很好 and Enter send everything, no space between.
        let o = run(&mut e, "_");
        assert!(o.preedit == format!("{zh}Windows") && o.commit.is_empty());
        syllable(&mut e, "ㄏㄣˇ");
        let shown = syllable(&mut e, "ㄏㄠˇ").preedit;
        let tail = shown.strip_prefix(&format!("{zh}Windows")).expect("Windows stays in front").to_string();
        assert!(tail.chars().count() == 2);
        let o = run(&mut e, "Enter");
        assert!(o.commit == format!("{zh}Windows{tail}"), "{}", o.commit);
    }
}

#[test]
fn space_twice_types_a_space_unit_and_one_backspace_takes_it() {
    // The first Space breaks the run (eaten), the second types a half-width space (1.3e): 我在用Windows 很好.
    let mut e = win();
    let zh = three(&mut e);
    run(&mut e, "S-w i n d o w s _");
    let o = run(&mut e, "_");
    assert!(o.handled && o.commit.is_empty() && o.preedit == format!("{zh}Windows ") && o.candidates.is_empty(), "{}", o.preedit);
    assert!(o.cursor_utf16 as usize == o.preedit.encode_utf16().count(), "the cursor is after the space");
    syllable(&mut e, "ㄏㄣˇ");
    let shown = syllable(&mut e, "ㄏㄠˇ").preedit;
    let tail = shown.strip_prefix(&format!("{zh}Windows ")).expect("the space stays in front").to_string();
    assert!(tail.chars().count() == 2);
    let o = run(&mut e, "Enter");
    assert!(o.commit == format!("{zh}Windows {tail}"), "{}", o.commit);
    // One Backspace takes the space unit whole; the token is still there.
    let mut e = win();
    let zh = three(&mut e);
    run(&mut e, "S-w i n d o w s _ _");
    assert!(run(&mut e, "BS").preedit == format!("{zh}Windows"));
    // A space after a space unit is another space unit; each is one unit.
    let o = run(&mut e, "_ _ _");
    assert!(o.preedit == format!("{zh}Windows   "), "{:?}", o.preedit);
    assert!(run(&mut e, "BS").preedit == format!("{zh}Windows  "));
    // A single Space (no second one) is not a space: 我在用Windows很好.
    let mut e = win();
    let zh = three(&mut e);
    let o = run(&mut e, "S-w i n d o w s _");
    assert!(o.preedit == format!("{zh}Windows"));
    // reset(Commit) sends the space unit like Enter does (a focus loss after the second Space).
    let mut e = win();
    run(&mut e, "S-w i n _ _");
    assert!(e.reset(ResetMode::Commit).commit == "Win ");
}

#[test]
fn space_after_chinese_a_mark_or_an_unfinished_syllable_is_as_before() {
    // Chinese in front: Space lists the candidates (rules 15 and 16), no space unit.
    let mut e = win();
    three(&mut e);
    let o = run(&mut e, "_");
    assert!(!o.candidates.is_empty(), "the Chinese candidate window");
    // A mark in front: the mark's own table (s3e).
    let mut e = win();
    run(&mut e, "S-,");
    assert!(run(&mut e, "_").candidates.first().map(String::as_str) == Some("，"));
    // An unfinished syllable after a token: Space is the tone-one key (rule 10), not a space unit.
    let mut e = win();
    run(&mut e, "S-w i n d o w s _");
    let o = run(&mut e, "c _");
    assert!(!o.preedit.contains(' ') && o.preedit.starts_with("Windows"), "{:?}", o.preedit);
    // An open window: Space is the window's.
    let mut e = win();
    let o = run(&mut e, "S-w i n d o w s _ Down");
    assert!(o.candidates == ["Windows", "windows", "WINDOWS"], "premise: the writings list is open: {:?}", o.candidates);
    let o = run(&mut e, "_");
    assert!(o.preedit == "Windows" && o.commit.is_empty(), "no space unit: {:?}", o.preedit);
    assert!(o.candidates == ["Windows", "windows", "WINDOWS"] && o.selected == Some(0), "the list stays open on its first page: {:?}", o.candidates);
}

#[test]
fn space_unit_at_the_unit_limit_and_in_the_middle() {
    // 126 marks, the token `Win` (the 127th unit): the second Space types the 128th unit, so everything is sent with this key.
    let mut e = win();
    let marks = "，".repeat(MAX_SYLLABLES - 2);
    for _ in 0..MAX_SYLLABLES - 2 {
        e.key(Key::ch(',', MOD_SHIFT)).unwrap();
    }
    run(&mut e, "S-w i n _");
    let o = run(&mut e, "_");
    assert!(o.handled && o.commit == format!("{marks}Win ") && o.preedit.is_empty(), "{:?}", o.commit);
    assert!(is_empty(&mut e));
    // The space unit goes in at the cursor, between the token and what follows it, and the arrow keys jump over it whole.
    let mut e = win();
    run(&mut e, "S-w i n _ S-, Left");
    let o = run(&mut e, "_");
    assert!(o.preedit == "Win ，", "{:?}", o.preedit);
    let o = run(&mut e, "Left");
    assert!(o.preedit == "Win ，" && o.cursor_utf16 == 3, "the cursor is before the space unit: {}", o.cursor_utf16);
    let o = run(&mut e, "Left");
    assert!(o.cursor_utf16 == 0, "one Left jumps the whole token `Win`: {}", o.cursor_utf16);
    let o = run(&mut e, "Right Right");
    assert!(o.cursor_utf16 == 4, "and one Right the whole space unit: {}", o.cursor_utf16);
}

#[test]
fn space_unit_is_a_punctuation_token_for_learning() {
    // 我是 re-picked as 我試, then a space unit: the record is under `^>_punct_en`, the key every English token
    // has, and the lookup after typing the same finds it.
    let dir = tmp_dir("space-unit-key");
    let mut e = lm_eng_lexicon();
    e.learning_open(&dir).unwrap();
    syllable(&mut e, "ㄨㄛˇ");
    syllable(&mut e, "ㄕˋ");
    choose_word(&mut e, "我試");
    run(&mut e, "r u s t _ _");
    let o = run(&mut e, "Enter");
    assert!(o.commit == "我試rust ", "{}", o.commit);
    assert!(records(&e) == [("^>_punct_en".to_string(), "我試".to_string())], "{:?}", records(&e));
    // The space unit straight after the word (the token between them deleted): it is the same kind of neighbour as an English token.
    let dir2 = tmp_dir("space-unit-key2");
    let mut e = lm_eng_lexicon();
    e.learning_open(&dir2).unwrap();
    syllable(&mut e, "ㄨㄛˇ");
    syllable(&mut e, "ㄕˋ");
    choose_word(&mut e, "我試");
    run(&mut e, "S-w _ _ Left Left Delete");
    let o = run(&mut e, "Enter");
    assert!(o.commit == "我試 ", "{:?}", o.commit);
    assert!(records(&e) == [("^>_punct_en".to_string(), "我試".to_string())], "{:?}", records(&e));
    // And it is read back from the file: a new engine on the same directory looks the record up under that key.
    let mut e = lm_eng();
    e.learning_open(&dir2).unwrap();
    assert!(records(&e) == [("^>_punct_en".to_string(), "我試".to_string())], "after reopening: {:?}", records(&e));
    let _ = std::fs::remove_dir_all(&dir2);
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn run_fields_point_at_the_run() {
    let mut e = win();
    let zh = three(&mut e);
    let o = run(&mut e, "S-,");
    assert!((o.english_run_utf16_start, o.english_run_utf16_len) == (0, 0), "no run: 0 and 0");
    // Chinese and a mark in front: the run starts after both, in UTF-16 units of the preedit.
    let o = run(&mut e, "S-w");
    assert!(o.preedit == format!("{zh}，W") && (o.english_run_utf16_start, o.english_run_utf16_len) == (4, 1), "{}", o.preedit);
    let o = run(&mut e, "i n d o w s");
    assert!((o.english_run_utf16_start, o.english_run_utf16_len) == (4, 7), "{} {}", o.english_run_utf16_start, o.english_run_utf16_len);
    // The same text with the run's letters as the preedit slice.
    let units: Vec<u16> = o.preedit.encode_utf16().collect();
    let (st, len) = (o.english_run_utf16_start as usize, o.english_run_utf16_len as usize);
    assert!(String::from_utf16(&units[st..st + len]).unwrap() == "Windows");
    // Backspace shortens it; Backspace down to nothing and Esc close it: 0 and 0.
    let o = run(&mut e, "BS");
    assert!((o.english_run_utf16_start, o.english_run_utf16_len) == (4, 6));
    let o = run(&mut e, "Esc");
    assert!((o.english_run_utf16_start, o.english_run_utf16_len) == (0, 0) && o.preedit == format!("{zh}，"));
    run(&mut e, "S-w");
    let o = run(&mut e, "BS");
    assert!((o.english_run_utf16_start, o.english_run_utf16_len) == (0, 0));
    // Every way the run ends leaves 0 and 0: Space, a mark, an arrow key, Down. (A tone key goes on the run: zhen-revert section 2.4.)
    for end in ["_", "S-,", "Left", "Down"] {
        let mut e = win();
        run(&mut e, "S-w i n");
        let o = run(&mut e, end);
        assert!((o.english_run_utf16_start, o.english_run_utf16_len) == (0, 0), "{end}");
    }
    // The cursor in the middle: the run sits at the cursor, the rest follows it.
    let mut e = win();
    let zh = three(&mut e);
    run(&mut e, "Left");
    let o = run(&mut e, "S-w i n");
    let before = zh.chars().take(2).collect::<String>();
    assert!(o.preedit.starts_with(&format!("{before}Win")) && (o.english_run_utf16_start, o.english_run_utf16_len) == (2, 3), "{}", o.preedit);
    // A window and the prediction row never carry a run.
    let mut e = win();
    three(&mut e);
    let o = run(&mut e, "_");
    assert!(!o.candidates.is_empty() && (o.english_run_utf16_start, o.english_run_utf16_len) == (0, 0));
}

#[test]
fn run_goes_on_with_shifted_and_prefix_letters() {
    // `Windows` + Shift+X: a Shift letter always goes on the run, no keep.
    let mut e = win();
    let o = run(&mut e, "S-w i n d o w s S-x");
    assert!(o.preedit == "WindowsX", "{}", o.preedit);
    // While the run is a prefix of a longer word (`win` of `windows`) a plain letter goes on.
    let mut e = win();
    let o = run(&mut e, "S-w i n d");
    assert!(o.preedit == "Wind", "{}", o.preedit);
    // `win` and `windows` are both words, and still a plain letter goes on after either (amendment three: a run never auto-keeps).
    // Then the run is ended by Space as a token, still `WindowsX`.
    let mut e = win();
    run(&mut e, "S-w i n d o w s S-x");
    let o = run(&mut e, "_");
    assert!(o.preedit == "WindowsX" && o.commit.is_empty());
}

#[test]
fn run_takes_the_shadow_letters_and_the_shift_letter() {
    // `i` + Shift+P + `hone` + Space: iPhone, an English token.
    let mut e = win();
    let o = run(&mut e, "i S-p h o n e");
    assert!(o.preedit == "iPhone", "{}", o.preedit);
    let o = run(&mut e, "_");
    assert!(o.preedit == "iPhone" && o.candidates.is_empty());
    // A revert opened the run (`r u s` is `rus`, zhen-revert section 2.3): Shift+W goes on it, as it goes on a Shift run: `rustW`.
    let mut e = win();
    let o = run(&mut e, "r u s t S-w");
    assert!(o.preedit == "rustW" && e.english_shadow().is_empty(), "{}", o.preedit);
    // An unfinished syllable in order has no run yet: its keys are the shadow, and they start the run with the Shift letter.
    let mut e = win();
    assert!(run(&mut e, "c u p").preedit == "ㄏㄧㄣ");
    let o = run(&mut e, "S-w");
    assert!(o.preedit == "cupW" && o.candidates.is_empty(), "{}", o.preedit);
}

#[test]
fn run_keeps_every_unfinished_key_a_run_accepts() {
    // A run takes the Zhuyin keys that are not letters (zhen-revert section 2.4), so the keys of the unfinished syllable all stay:
    // `1` (ㄅ) then Shift+W is `1W`, `5` is `5W`, `1 u` is `1uW`, and `u .` is `u.W` (ㄧ, ㄡ).
    for (keys, want) in [("1", "1W"), ("5", "5W"), ("1 u", "1uW"), ("u .", "u.W"), ("m ,", "m,W")] {
        let mut e = win();
        run(&mut e, keys);
        let o = run(&mut e, "S-w");
        assert!(o.preedit == want && fields(&o) == (0, want.len() as u32) && e.english_shadow().is_empty(), "{keys}: {}", o.preedit);
        // And the run goes on as a run: the tail `W` then a tone key is no syllable, so the digit goes on the run.
        assert!(run(&mut e, "3").preedit == format!("{want}3"), "{keys}");
    }
    // The same through a Chinese candidate window: it closes.
    let mut e = win();
    let zh = three(&mut e);
    let o = run(&mut e, "_");
    assert!(!o.candidates.is_empty(), "premise: the window is open");
    let o = run(&mut e, "S-w");
    assert!(o.candidates.is_empty() && o.preedit == format!("{zh}W"), "{}", o.preedit);
}

#[test]
fn run_backspace_and_escape() {
    let mut e = win();
    let zh = three(&mut e);
    assert!(run(&mut e, "S-w i n").preedit == format!("{zh}Win"));
    assert!(run(&mut e, "BS").preedit == format!("{zh}Wi"));
    let o = run(&mut e, "Esc");
    assert!(o.preedit == zh, "the whole run goes, the rest stays: {}", o.preedit);
    // The run is closed: a letter starts a syllable, it does not go on a run.
    assert!(run(&mut e, "c").preedit == format!("{zh}ㄏ"));
    // Backspace down to nothing closes the run and takes nothing else.
    let mut e = win();
    let zh = three(&mut e);
    run(&mut e, "S-w");
    let o = run(&mut e, "BS");
    assert!(o.preedit == zh);
    assert!(run(&mut e, "c").preedit == format!("{zh}ㄏ"));
}

#[test]
fn run_is_32_letters_and_the_33rd_starts_the_next() {
    let mut e = win();
    run(&mut e, "S-z");
    let o = run(&mut e, &"z ".repeat(31));
    assert!(o.preedit == format!("Z{}", "z".repeat(31)));
    let o = run(&mut e, "q");
    assert!(o.preedit == format!("Z{}q", "z".repeat(31)) && o.commit.is_empty());
    // The 32 letters are a token now and `q` the new run: Backspace takes the `q`, the next one a letter of the token.
    assert!(run(&mut e, "BS").preedit == format!("Z{}", "z".repeat(31)));
    assert!(run(&mut e, "BS").preedit == format!("Z{}", "z".repeat(30)));
}

#[test]
fn other_keys_end_the_run_first() {
    // Punctuation: the run ends, then the mark is inserted.
    let mut e = win();
    let o = run(&mut e, "S-w i n d o w s S-,");
    assert!(o.preedit == "Windows，", "{}", o.preedit);
    // Enter: the run ends, then Enter sends it all.
    let mut e = win();
    let zh = three(&mut e);
    let o = run(&mut e, "S-w i n Enter");
    assert!(o.handled && o.commit == format!("{zh}Win") && o.preedit.is_empty());
    // A tone key goes on the run when there is no syllable to split off (zhen-revert section 2.4); an arrow key ends it first.
    let mut e = win();
    assert!(run(&mut e, "S-w i n 3").preedit == "Win3");
    let mut e = win();
    let o = run(&mut e, "S-w i n Left");
    assert!(o.preedit == "Win" && (o.english_run_utf16_start, o.english_run_utf16_len) == (0, 0) && o.cursor_utf16 == 0, "{}", o.preedit);
    // The commit on focus loss shows the letters as they are.
    let mut e = win();
    run(&mut e, "S-w i n");
    assert!(e.reset(ResetMode::Commit).commit == "Win");
    let mut e = win();
    run(&mut e, "S-w i n");
    assert!(e.reset(ResetMode::Discard).commit.is_empty() && is_empty(&mut e));
}

#[test]
fn run_at_the_unit_limit_sends_in_the_output_of_the_ending_key() {
    // 127 units, `Windows`, Space: the 128th unit is the token, everything goes in this output, the space is eaten.
    let mut e = win();
    let marks = nearly_full(&mut e);
    run(&mut e, "S-w i n d o w s");
    let o = run(&mut e, "_");
    assert!(o.handled && o.commit == format!("{marks}Windows") && o.preedit.is_empty(), "{}", o.commit);
    assert!(is_empty(&mut e));
    // The same ended by a mark (amendment three: a letter no longer ends it): the same text, the mark starts the emptied composition.
    let mut e = win();
    let marks = nearly_full(&mut e);
    run(&mut e, "S-w i n d o w s");
    let o = run(&mut e, "S-,");
    assert!(o.handled && o.commit == format!("{marks}Windows") && o.preedit == "，", "{} / {}", o.commit, o.preedit);
    // Enter that ends it: the text is sent, Enter is not passed on.
    let mut e = win();
    let marks = nearly_full(&mut e);
    run(&mut e, "S-w i n d o w s");
    let o = run(&mut e, "Enter");
    assert!(o.handled && o.commit == format!("{marks}Windows") && o.preedit.is_empty(), "{}", o.commit);
}

#[test]
fn shift_letter_is_the_old_rules_without_mixing() {
    // Rule 21: Shift+letter commits the composition and passes the key on. Rule 22: nothing to commit, passes through.
    let mut e = Engine::with_lexicon(real(), Layout::Standard);
    let zh = three(&mut e);
    let o = run(&mut e, "S-w");
    assert!(!o.handled && o.commit == zh, "{} {}", o.handled, o.commit);
    let o = run(&mut e, "S-w");
    assert!(!o.handled && o.commit.is_empty());
    // With Eten the shadow is not recorded and the run does not open, whatever the switch says.
    let mut e = Engine::with_lexicon(real(), Layout::Eten);
    e.set_english(true).unwrap();
    assert!(!run(&mut e, "S-w").handled);
}

#[test]
fn run_tokens_list_their_writings_without_a_chinese_item() {
    let mut e = win();
    run(&mut e, "S-w i n d o w s _");
    assert!(run(&mut e, "Down").candidates == ["Windows", "windows", "WINDOWS"]);
    let mut e = win();
    run(&mut e, "i S-p h o n e _");
    assert!(run(&mut e, "Down").candidates == ["iPhone", "iphone", "Iphone", "IPHONE"]);
    // Down ends the run and opens the list in the same output (the output must show the window the engine opened).
    let mut e = win();
    let o = run(&mut e, "S-w i n d o w s Down");
    assert!(o.candidates == ["Windows", "windows", "WINDOWS"] && o.selected == Some(0), "{:?}", o.candidates);
    // Ended by Space, then ㄏ typed and deleted: the same list, no Chinese item.
    let mut e = win();
    run(&mut e, "S-w i n d o w s _ c BS");
    assert!(run(&mut e, "Down").candidates == ["Windows", "windows", "WINDOWS"]);
    // With an unfinished syllable Down does nothing to the token.
    let mut e = win();
    run(&mut e, "S-w i n d o w s _ c");
    assert!(run(&mut e, "Down").candidates.is_empty());
}

#[test]
fn switching_mixing_off_keeps_the_run_as_a_token() {
    // The run is text on screen: set_english(0) and set_abbreviation(1) turn it into a token (the output shows it), nothing is lost.
    let mut e = win();
    let zh = three(&mut e);
    run(&mut e, "S-w i n");
    let o = e.set_english(false).unwrap();
    assert!(o.handled && o.commit.is_empty() && o.preedit == format!("{zh}Win"), "{}", o.preedit);
    assert!(run(&mut e, "Enter").commit == format!("{zh}Win"));
    let mut e = win();
    run(&mut e, "S-w i n");
    let o = e.set_abbreviation(true).unwrap();
    assert!(o.preedit == "Win");
    assert!(run(&mut e, "Enter").commit == "Win");
    // At the limit the text goes out in that call's snapshot.
    let mut e = win();
    let marks = nearly_full(&mut e);
    run(&mut e, "S-w i n");
    let o = e.set_english(false).unwrap();
    assert!(o.commit == format!("{marks}Win") && o.preedit.is_empty(), "{}", o.commit);
}

#[test]
fn no_prediction_row_while_the_run_is_open_and_tab_ends_the_run_first() {
    // With a model loaded, Backspace inside a run must not bring the row back: Tab would enter it before the run (and a pick
    // would put the word in front of the run's letters). The row comes back when the run is gone.
    let mut e = lm_eng();
    syllable(&mut e, "ㄨㄛˇ");
    syllable(&mut e, "ㄗㄞˋ");
    let o = syllable(&mut e, "ㄩㄥˋ");
    let zh = o.preedit.clone();
    assert!(!o.candidates.is_empty() && zh.chars().count() == 3, "premise: the row shows after the three syllables");
    let o = run(&mut e, "S-w i n");
    assert!(o.candidates.is_empty(), "no row while the run is open");
    let o = run(&mut e, "BS");
    assert!(o.preedit == format!("{zh}Wi") && o.candidates.is_empty() && o.selected.is_none(), "{:?}", o.candidates);
    // Tab ends the run first (the token `Wi` joins the composition), then is an ordinary Tab: with no row to enter it commits the
    // composition, letters included, and passes on. It does not enter a row, and nothing is picked from one.
    let o = run(&mut e, "Tab");
    assert!(!o.handled && o.commit == format!("{zh}Wi") && o.selected.is_none(), "{:?} {:?} {}", o.commit, o.candidates, o.handled);
    // Backspace that empties the run gives the row back.
    let mut e = lm_eng();
    three(&mut e);
    let o = run(&mut e, "S-w BS");
    assert!(o.preedit == zh && !o.candidates.is_empty(), "the row is back: {:?}", o.candidates);
}

#[test]
fn a_space_unit_has_no_list_and_space_in_front_of_a_token_opens_nothing() {
    // Down right after a space unit opens nothing (it used to list the one item " ").
    let mut e = win();
    let zh = three(&mut e);
    let o = run(&mut e, "S-w i n _ _ Down");
    assert!(o.handled && o.candidates.is_empty(), "{:?}", o.candidates);
    assert!(o.preedit == format!("{zh}Win "), "{:?}", o.preedit);
    // Space and Down at cursor 0 in front of a space unit: the same.
    let mut e = win();
    run(&mut e, "S-w _ _ Left Left Delete");
    assert!(run(&mut e, "Home").preedit == " ");
    for key in ["_", "Down"] {
        let o = run(&mut e, key);
        assert!(o.handled && o.candidates.is_empty() && o.preedit == " ", "{key}: {:?}", o.candidates);
    }
    // Space at cursor 0 in front of a token does not open its writings list; Down does.
    let mut e = win();
    run(&mut e, "S-w i n d o w s _");
    let zh = syllable(&mut e, "ㄏㄠˇ").preedit["Windows".len()..].to_string();
    let o = run(&mut e, "Home");
    assert!(o.preedit == format!("Windows{zh}"), "{:?}", o.preedit);
    let o = run(&mut e, "_");
    assert!(o.handled && o.candidates.is_empty() && o.preedit == format!("Windows{zh}"), "Space: {:?}", o.candidates);
    let o = run(&mut e, "Down");
    assert!(o.candidates == ["Windows", "windows", "WINDOWS"], "Down: {:?}", o.candidates);
}

#[test]
fn space_unit_rule_needs_english_mixing_on() {
    // The token `Win` is left by switching the mixing off; Space after it is what it was before the amendment
    // (rule 16: the writings list of the token), not a space unit.
    let mut e = win();
    run(&mut e, "S-w i n");
    let o = e.set_english(false).unwrap();
    assert!(o.preedit == "Win", "premise: the run became a token: {:?}", o.preedit);
    let o = run(&mut e, "_");
    assert!(o.preedit == "Win" && o.candidates == ["Win", "win", "WIN"], "{:?} {:?}", o.preedit, o.candidates);
}

#[test]
fn punctuation_after_letters_at_the_unit_limit_sends_in_this_keys_output() {
    // The run `rust` is the 128th unit when the mark ends it; the mark starts the emptied composition. (With Space the token is
    // the 128th unit and goes out at that Space, so the mark finds an empty composition.)
    let mut e = eng();
    let marks = nearly_full(&mut e);
    run(&mut e, "r u s t");
    let o = run(&mut e, "S-,");
    assert!(o.handled && o.commit == format!("{marks}rust") && o.preedit == "，", "{} / {}", o.commit, o.preedit);
}

#[test]
fn a_run_in_the_middle_decodes_its_two_sides_apart() {
    let fields = |o: &Output| (o.english_run_utf16_start, o.english_run_utf16_len);
    // 編譯 is one word; with something between 編 and 譯 the sides decode alone (邊, 意), as with a mark between them.
    for lm in [false, true] {
        let mk = || if lm { lm_eng() } else { eng() };
        let mut merged = mk();
        syllable(&mut merged, "ㄅㄧㄢ");
        let joined = syllable(&mut merged, "ㄧˋ").preedit;
        let mut marked = mk();
        syllable(&mut marked, "ㄅㄧㄢ");
        run(&mut marked, "S-,");
        let split = syllable(&mut marked, "ㄧˋ").preedit;
        let (left, right) = split.split_once('，').unwrap();
        assert!(joined == "編譯" && split == "邊，意", "premise (lm={lm}): {joined} / {split}");
        let width = left.encode_utf16().count() as u32;

        // Opened by Backspace behind a token: the run's two sides are what the mark's two sides are.
        let mut e = mk();
        syllable(&mut e, "ㄅㄧㄢ");
        run(&mut e, "r u s t _");
        syllable(&mut e, "ㄧˋ");
        run(&mut e, "Left");
        let o = run(&mut e, "BS");
        assert!(o.preedit == format!("{left}rus{right}") && fields(&o) == (width, 3), "lm={lm}: {}", o.preedit);
        // Letters and Backspace in the run change nothing outside it.
        let o = run(&mut e, "t");
        assert!(o.preedit == format!("{left}rust{right}") && fields(&o) == (width, 4), "lm={lm}: {}", o.preedit);
        let o = run(&mut e, "BS BS");
        assert!(o.preedit == format!("{left}ru{right}"), "lm={lm}: {}", o.preedit);
        run(&mut e, "s t");
        let o = run(&mut e, "_");
        assert!(o.preedit == format!("{left}rust{right}") && fields(&o) == (0, 0), "lm={lm}: {}", o.preedit);
        assert!(run(&mut e, "Enter").commit == format!("{left}rust{right}"));

        // Opened by Shift+letter in the middle (the cursor between 編 and 譯).
        let mut e = mk();
        syllable(&mut e, "ㄅㄧㄢ");
        syllable(&mut e, "ㄧˋ");
        run(&mut e, "Left");
        let o = run(&mut e, "S-w");
        assert!(o.preedit == format!("{left}W{right}") && fields(&o) == (width, 1), "lm={lm}: {}", o.preedit);
        let o = run(&mut e, "i n");
        assert!(o.preedit == format!("{left}Win{right}"), "lm={lm}: {}", o.preedit);
        // At the very start, too: the first character is not after the (Han) left context of the app.
        let mut e = mk();
        syllable(&mut e, "ㄅㄧㄢ");
        syllable(&mut e, "ㄧˋ");
        run(&mut e, "Home");
        let o = run(&mut e, "S-w");
        assert!(o.preedit == "W編譯" && fields(&o) == (0, 1), "lm={lm}: {}", o.preedit);
    }
}

#[test]
fn a_run_that_is_cancelled_in_the_middle_leaves_one_stretch_again() {
    // The split of section 1.3h holds while the run is open. Esc, Backspace to empty and a token-reopened run emptied all
    // leave no run and no token: the composition decodes as one stretch again, on screen and in what Enter sends.
    let fields = |o: &Output| (o.english_run_utf16_start, o.english_run_utf16_len);
    for lm in [false, true] {
        let mk = || if lm { lm_eng() } else { eng() };
        for cancel in ["Esc", "BS", "token"] {
            let mut e = mk();
            syllable(&mut e, "ㄅㄧㄢ");
            let o = if cancel == "token" {
                run(&mut e, "r u s t _");
                syllable(&mut e, "ㄧˋ");
                run(&mut e, "Left");
                run(&mut e, "BS") // the run `rus` opens between the two sides
            } else {
                syllable(&mut e, "ㄧˋ");
                run(&mut e, "Left");
                run(&mut e, "S-w")
            };
            assert!(o.preedit.starts_with('邊') && o.preedit.ends_with('意') && fields(&o).1 > 0, "premise (lm={lm}): {}", o.preedit);
            let o = match cancel {
                "BS" => run(&mut e, "BS"),
                "Esc" => run(&mut e, "Esc"),
                _ => run(&mut e, "BS BS BS"),
            };
            assert!(o.preedit == "編譯" && fields(&o) == (0, 0), "lm={lm} {cancel}: {}", o.preedit);
            assert!(run(&mut e, "Enter").commit == "編譯", "lm={lm} {cancel}");
        }
    }
}

#[test]
fn pick_chinese_item_equals_digit() {
    // The mouse path chooses the same item as the digit: the Chinese item of `ok` (zhen-revert section 2.3).
    let (mut a, mut b) = (eng(), eng());
    run(&mut a, "o k _ Down");
    run(&mut b, "o k _ Down");
    assert!(a.pick(3).unwrap().unwrap() == b.key(Key::ch('4', 0)).unwrap());
}

#[test]
fn commit_paths_send_what_is_shown() {
    // reset(Commit) after `r u s`: the run's letters, not the Zhuyin.
    let mut e = eng();
    run(&mut e, "r u s");
    assert!(e.reset(ResetMode::Commit).commit == "rus");
    // Before the revert the unfinished syllable is Zhuyin, as it always was.
    let mut e = eng();
    run(&mut e, "r u");
    assert!(e.reset(ResetMode::Commit).commit == "ㄐㄧ");
    // Enter sends the token with the rest.
    let mut e = eng();
    let o = run(&mut e, "r u s t _ Enter");
    assert!(o.commit == "rust" && o.preedit.is_empty());
}

// ---------- zhen-revert section 5.1: the reverts ----------

#[test]
fn overwrite_reverts_to_the_keys() {
    let mut e = eng();
    let o = run(&mut e, "o k");
    assert!(o.preedit == "ok" && fields(&o) == (0, 2) && o.cursor_utf16 == 2 && o.commit.is_empty(), "{}", o.preedit);
    assert!(e.english_columns() == [None; 3] && e.english_reverted() == Some(Revert::Overwrite) && e.english_shadow().is_empty());
    assert!(o.candidates.is_empty(), "no prediction row while the run is open");
    let o = run(&mut e, "_");
    assert!(o.preedit == "ok" && fields(&o) == (0, 0) && o.candidates.is_empty() && o.commit.is_empty());
    assert!(e.syllables() == ["_punct_ok"]);
    let o = run(&mut e, "Down");
    assert!(o.candidates == ["ok", "Ok", "OK", first_entry("ㄜ").as_str()], "{:?}", o.candidates);
}

#[test]
fn order_reverts_to_the_keys() {
    let mut e = eng();
    e.set_english_reversal_guard(false).unwrap();
    run(&mut e, "l s");
    assert!(e.english_reverted() == Some(Revert::Order));
    let o = run(&mut e, "_ Down");
    assert!(e.syllables() == ["_punct_ls"]);
    assert!(o.candidates == ["ls", "Ls", "LS", first_entry("ㄋㄠ").as_str()], "{:?}", o.candidates);
    let mut e = eng();
    e.set_english_reversal_guard(false).unwrap();
    run(&mut e, "p r _");
    assert!(e.syllables() == ["_punct_pr"], "{:?}", e.syllables());
}

#[test]
fn missing_syllable_on_space_is_one_token() {
    for (keys, word) in [("c u p _", "cup"), ("g o _", "go"), ("c p _", "cp")] {
        let mut e = eng();
        let os = run_all(&mut e, keys);
        let o = os.last().unwrap();
        assert!(o.preedit == word && o.candidates.is_empty() && fields(o) == (0, 0) && o.commit.is_empty(), "{keys}: {}", o.preedit);
        assert!(e.english_reverted() == Some(Revert::Missing) && e.syllables() == [format!("_punct_{word}")], "{keys}");
        // Only the three writings: a syllable that does not exist has no Chinese item.
        let o = run(&mut e, "Down");
        let lower = word.to_string();
        let title = format!("{}{}", &word[..1].to_uppercase(), &word[1..]);
        assert!(o.candidates == [lower, title, word.to_uppercase()], "{keys}: {:?}", o.candidates);
    }
}

#[test]
fn missing_syllable_on_tone_opens_a_run() {
    for (keys, word) in [("s 7", "s7"), ("a 4", "a4"), ("i 7", "i7")] {
        let mut e = eng();
        let o = run(&mut e, keys);
        assert!(o.preedit == word && fields(&o) == (0, 2) && e.english_reverted() == Some(Revert::Missing), "{keys}: {}", o.preedit);
        assert!(e.english_columns() == [None; 3]);
        let o = run(&mut e, "_");
        assert!(o.preedit == word && fields(&o) == (0, 0) && e.syllables() == [format!("_punct_{word}")], "{keys}");
    }
}

#[test]
fn missing_syllable_on_tone_needs_key_order() {
    let mut e = eng();
    e.set_english_key_order(false).unwrap();
    let o = run(&mut e, "s 7");
    assert!(o.preedit == "ㄋ" && fields(&o) == (0, 0) && e.english_reverted().is_none(), "{}", o.preedit);
    assert!(e.english_columns() == [Some('ㄋ'), None, None], "today: the tone key is ignored and the Zhuyin stays");
}

#[test]
fn letters_and_digits_go_on_the_run() {
    for (keys, word) in [("g t a 6 _", "gta6"), ("r d r 2 _", "rdr2"), ("p o e 2 _", "poe2"), ("w i n 1 1 _", "win11"), ("e . g _", "e.g")] {
        let mut e = eng();
        let o = run(&mut e, keys);
        assert!(o.preedit == word && o.candidates.is_empty() && fields(&o) == (0, 0), "{keys}: {}", o.preedit);
        assert!(e.syllables() == [format!("_punct_{word}")], "{keys}: {:?}", e.syllables());
    }
}

#[test]
fn tone_splits_a_two_symbol_tail() {
    let cases: [(&str, &[&str], usize); 3] = [
        ("o k j i 3", &["_punct_ok", "ㄨㄛˇ"], 4),
        ("o k 1 8 7", &["_punct_ok", "ㄅㄚ˙"], 4),
        ("r u s t 1 u 0 3 u 4", &["_punct_rust", "ㄅㄧㄢˇ", "ㄧˋ"], 7),
    ];
    for (keys, want, split_at) in cases {
        let mut e = eng();
        for (i, tok) in keys.split_whitespace().enumerate() {
            e.key(key_of(tok)).unwrap();
            assert!(e.english_split() == (i == split_at), "{keys}: key {i} split={}", e.english_split());
        }
        assert!(e.syllables() == want, "{keys}: {:?}", e.syllables());
        assert!(e.english_reverted().is_none(), "the last key reverted nothing");
    }
    // The split syllable is a syllable: the run is closed and the next key starts a new one.
    let mut e = eng();
    let o = run(&mut e, "o k j i 3");
    assert!(fields(&o) == (0, 0) && o.preedit.starts_with("ok"), "{}", o.preedit);
}

#[test]
fn tone_does_not_split_one_symbol() {
    let mut e = eng();
    let o = run(&mut e, "S-a 1 2 3 4 5 6 7 8 9 Enter");
    assert!(o.commit == "A123456789", "digits.md #27: {}", o.commit);
    for (keys, word) in [("r u s t 3 _", "rust3"), ("o k g 4 _", "okg4")] {
        let mut e = eng();
        run(&mut e, keys);
        assert!(e.syllables() == [format!("_punct_{word}")], "{keys}: {:?}", e.syllables());
        assert!(!e.english_split(), "{keys}");
    }
}

#[test]
fn tone_split_needs_the_lexicon() {
    // The tail `go` (ㄕㄟ, two symbols, columns 0 and 2) with ˋ is a syllable the lexicon lacks: no split, the digit goes on the run.
    let lex = real();
    assert!(lex.entries(&["ㄕㄟˋ".to_string()]).is_empty() && lex.entries(&["ㄕㄟ".to_string()]).is_empty(), "premise: ㄕㄟˋ is not a syllable");
    let mut e = eng();
    let mut o = None;
    for tok in "o k g o 4".split_whitespace() {
        let r = e.key(key_of(tok));
        assert!(r.is_ok(), "{tok}: the engine failed (a split made a syllable the lexicon lacks)");
        o = r.ok();
    }
    let o = o.unwrap();
    assert!(o.preedit == "okgo4" && fields(&o) == (0, 5) && !e.english_split() && e.syllables().is_empty(), "{}", o.preedit);
}

#[test]
fn missing_syllable_defers_to_adjacent_key_fuzzy() {
    // Adjacent-key fuzzy input (bit 4) on: ㄕㄟ is rescued by ㄕㄣ (o -> p), so the Space makes a Chinese syllable, not the token `go`.
    let mut e = lm_eng();
    e.set_fuzzy(16).unwrap();
    let o = run(&mut e, "g o _");
    assert!(e.syllables() == ["ㄕㄟ"] && fields(&o) == (0, 0) && e.english_reverted().is_none(), "{:?}", e.syllables());
    assert!(!o.preedit.contains("go"), "{}", o.preedit);
    // The example of the settings note (`MenuEntry.Text.englishDigitsNote`) is a word the adjacent-key rule cannot rescue: with bit 4 on,
    // `s o` Space (ㄋㄟ, no one-key neighbour is a syllable) is still the English token `so`, where `go` and `cup` become Chinese.
    let mut e = lm_eng();
    e.set_fuzzy(16).unwrap();
    run(&mut e, "s o _");
    assert!(e.syllables() == ["_punct_so"] && e.english_reverted() == Some(Revert::Missing), "{:?}", e.syllables());
    let mut e = lm_eng();
    e.set_fuzzy(16).unwrap();
    run(&mut e, "c u p _");
    assert!(e.syllables().len() == 1 && !e.syllables()[0].starts_with("_punct_"), "cup is Chinese with the neighbour keys on: {:?}", e.syllables());
    let mut e = lm_eng();
    e.set_fuzzy(0).unwrap();
    let o = run(&mut e, "g o _");
    assert!(o.preedit == "go" && e.syllables() == ["_punct_go"] && e.english_reverted() == Some(Revert::Missing), "{}", o.preedit);
}

#[test]
fn space_never_splits() {
    let mut e = eng();
    run(&mut e, "o k w 8 _");
    assert!(e.syllables() == ["_punct_okw8"], "{:?}", e.syllables());
    let mut e = eng();
    run(&mut e, "r u s t _");
    assert!(e.syllables() == ["_punct_rust"], "ㄔ is in the lexicon and the tail still does not split: {:?}", e.syllables());
}

#[test]
fn shift_run_takes_digits_too() {
    let mut e = eng();
    run(&mut e, "S-p 4 _");
    assert!(e.syllables() == ["_punct_P4"], "{:?}", e.syllables());
    assert!(run(&mut e, "Enter").commit == "P4");
    let mut e = eng();
    run(&mut e, "S-a S-t S-m 5 j 0 3");
    assert!(e.syllables() == ["_punct_ATM", "ㄓㄨㄢˇ"], "{:?}", e.syllables());
    let o = run(&mut e, "Enter");
    assert!(o.commit == format!("ATM{}", first_entry("ㄓㄨㄢˇ")) || o.commit.starts_with("ATM"), "{}", o.commit);
    assert!(o.commit.chars().count() == 4, "ATM and one character: {}", o.commit);
}

#[test]
fn chinese_item_follows_the_revert() {
    let zh = first_entry("ㄜ");
    let mut e = eng();
    let o = run(&mut e, "o k _ Down");
    assert!(o.candidates == ["ok", "Ok", "OK", zh.as_str()], "{:?}", o.candidates);
    // Choosing it turns the token into the syllable ㄜ with that character fixed, and nothing is learned.
    let o = run(&mut e, "4");
    assert!(o.preedit == zh && e.syllables() == ["ㄜ"], "{} {:?}", o.preedit, e.syllables());
    // Another letter or Backspace on the run drops the Chinese item.
    let mut e = eng();
    run(&mut e, "o k s _ Down");
    assert!(run(&mut e, "Esc").candidates.is_empty());
    let mut e = eng();
    assert!(run(&mut e, "o k s _ Down").candidates == ["oks", "Oks", "OKS"]);
    // ㄋㄧ is not in the lexicon: no Chinese item.
    let mut e = eng();
    assert!(run(&mut e, "r u s _ Down").candidates == ["rus", "Rus", "RUS"]);
    // Not learned: the character is fixed with no pending learn.
    let mut e = lm_eng();
    run(&mut e, "o k _ Down 4");
    run(&mut e, "Enter");
    assert!(records(&e).is_empty(), "{:?}", records(&e));
}

#[test]
fn revert_in_the_middle_decodes_apart() {
    // 編譯 is one word; with an English run between 編 and 譯 the sides decode alone (邊, 意), as with a mark between them.
    for lm in [false, true] {
        let mk = || if lm { lm_eng() } else { eng() };
        let mut marked = mk();
        syllable(&mut marked, "ㄅㄧㄢ");
        run(&mut marked, "S-,");
        let split = syllable(&mut marked, "ㄧˋ").preedit;
        let (left, right) = split.split_once('，').unwrap();
        assert!(split == "邊，意", "premise (lm={lm}): {split}");
        let width = left.encode_utf16().count() as u32;
        let mut e = mk();
        syllable(&mut e, "ㄅㄧㄢ");
        syllable(&mut e, "ㄧˋ");
        run(&mut e, "Left");
        let o = run(&mut e, "o k");
        assert!(o.preedit == format!("{left}ok{right}") && fields(&o) == (width, 2), "lm={lm}: {}", o.preedit);
        let o = run(&mut e, "_");
        assert!(o.preedit == format!("{left}ok{right}") && fields(&o) == (0, 0) && e.syllables().len() == 3, "lm={lm}: {}", o.preedit);
        assert!(run(&mut e, "Enter").commit == format!("{left}ok{right}"));
    }
}

#[test]
fn backspace_and_escape_on_a_reverted_run() {
    // Backspace takes one key at a time and closes the run with the last.
    let mut e = eng();
    run(&mut e, "g t a");
    assert!(run(&mut e, "BS").preedit == "gt");
    run(&mut e, "BS");
    let o = run(&mut e, "BS");
    assert!(o.preedit.is_empty() && fields(&o) == (0, 0) && is_empty(&mut e));
    // Esc right after the revert brings the Zhuyin back: g then t over it is ㄔ.
    let mut e = eng();
    let o = run(&mut e, "g t");
    assert!(o.preedit == "gt" && fields(&o) == (0, 2));
    let o = run(&mut e, "Esc");
    assert!(o.preedit == "ㄔ" && fields(&o) == (0, 0) && e.english_columns() == [Some('ㄔ'), None, None] && e.english_shadow() == "t", "{}", o.preedit);
    // After the run was changed (a typed key), Esc takes the whole run.
    let mut e = eng();
    run(&mut e, "g t a");
    assert!(run(&mut e, "Esc").preedit.is_empty() && is_empty(&mut e));
    // `r u s` Esc `3` is 你: the Zhuyin the key would have made, then the tone.
    let mut e = eng();
    run(&mut e, "r u s");
    let o = run(&mut e, "Esc");
    assert!(o.preedit == "ㄋㄧ", "{}", o.preedit);
    assert!(run(&mut e, "3").preedit == shows("ㄋㄧˇ"));
    // A Shift run has no point to go back to: Esc takes the whole run.
    let mut e = eng();
    run(&mut e, "S-a b");
    assert!(run(&mut e, "Esc").preedit.is_empty() && is_empty(&mut e));
    // (c) on a tone key: Esc is the columns before the tone, and Backspace then fixes the wrong symbol.
    let mut e = eng();
    run(&mut e, "s 7");
    let o = run(&mut e, "Esc");
    assert!(o.preedit == "ㄋ" && e.english_columns() == [Some('ㄋ'), None, None], "{}", o.preedit);
    // After Esc the next key is judged again: ㄜ is in the final column, so `k` over it reverts again (the shadow holds the first `k`).
    let mut e = eng();
    run(&mut e, "o k Esc");
    assert!(e.english_columns() == [None, None, Some('ㄜ')] && e.english_shadow() == "k");
    let o = run(&mut e, "k");
    assert!(o.preedit == "kk" && e.english_reverted() == Some(Revert::Overwrite), "{}", o.preedit);
}

#[test]
fn backspace_before_a_revert_clears_the_column_key() {
    let mut e = eng();
    run(&mut e, "s u BS");
    assert!(e.english_shadow() == "s", "the key of the symbol that went is gone: {:?}", e.english_shadow());
    let o = run(&mut e, "s");
    assert!(o.preedit == "ss" && e.english_reverted() == Some(Revert::Overwrite), "{}", o.preedit);
}

#[test]
fn overwrite_switch_off_is_today() {
    let mut e = eng();
    e.set_english_overwrite(false).unwrap();
    run(&mut e, "r u s 3");
    assert!(e.syllables() == ["ㄋㄧˇ"], "the key replaces: {:?}", e.syllables());
    let mut e = eng();
    e.set_english_overwrite(false).unwrap();
    run(&mut e, "o k _");
    assert!(e.syllables() == ["ㄜ"], "{:?}", e.syllables());
    assert!(e.english_reverted().is_none());
}

#[test]
fn key_order_switch_off_is_today() {
    let mut e = eng();
    e.set_english_key_order(false).unwrap();
    run(&mut e, "l s 3");
    assert!(e.syllables() == ["ㄋㄠˇ"], "the symbol goes into its column: {:?}", e.syllables());
    let mut e = eng();
    e.set_english_key_order(false).unwrap();
    run(&mut e, "c u p _");
    assert!(e.syllables() == ["_punct_cup"], "(c) at Space is under the mixing switch: {:?}", e.syllables());
}

#[test]
fn english_short_is_as_documented() {
    // Each word is typed in column order, its one-tone syllable is in the lexicon, so no trigger reverts it; the others of the contract
    // (section 2.6) are missing syllables, which the Space key turns into a token.
    let lex = real();
    for w in ENGLISH_SHORT {
        let mut e = eng();
        let keys: Vec<char> = w.chars().collect();
        let (body, tone) = if w.ends_with('3') { (&keys[..keys.len() - 1], Some(2)) } else { (&keys[..], None) };
        let syl: String = body.iter().map(|&c| Layout::Standard.symbol_of(c).expect("a Zhuyin key").1).chain(tone.map(|_| 'ˇ')).collect();
        assert!(!lex.entries(&[syl.clone()]).is_empty(), "{w}: {syl} is in the lexicon");
        for c in &keys {
            e.key(Key::ch(*c, 0)).unwrap();
        }
        if tone.is_none() {
            e.key(Key::new(KeyKind::Space)).unwrap();
        }
        assert!(e.english_reverted().is_none() && e.syllables() == [syl.clone()], "{w}: {:?}", e.syllables());
    }
    for w in ["go", "cp", "so", "to", "do", "hi", "am", "zi", "cup"] {
        let mut e = eng();
        for c in w.chars() {
            e.key(Key::ch(c, 0)).unwrap();
        }
        e.key(Key::new(KeyKind::Space)).unwrap();
        assert!(e.syllables() == [format!("_punct_{w}")], "{w}: {:?}", e.syllables());
    }
    assert!(ENGLISH_SHORT.len() >= 5);
}

#[test]
fn short_list_adds_two_items_at_the_end() {
    let list_of = |e: &mut Engine, keys: &str| -> (Vec<String>, u32) {
        let o = run(e, keys);
        (o.candidates.clone(), o.total)
    };
    for (keys, syl, lower, upper) in [("a i _ Down", "ㄇㄛ", "ai", "AI"), ("m p 3 Down", "ㄩㄣˇ", "mp3", "MP3"), ("u p _ _", "ㄧㄣ", "up", "UP")] {
        let mut e = eng();
        let (page, total) = list_of(&mut e, keys);
        let mut plain = eng();
        plain.set_english(false).unwrap();
        let (plain_page, plain_total) = list_of(&mut plain, keys);
        assert!(total == plain_total + 2 && !page.is_empty(), "{keys}: {total} vs {plain_total}");
        assert!(page[0] == first_entry(syl) && page.first() == plain_page.first(), "{keys}: the first item is unchanged: {page:?}");
        // The last two items, whatever page they are on: select them with Right.
        let mut o = run(&mut e, "Right");
        for _ in 0..total {
            o = run(&mut e, "Right");
        }
        assert!(o.candidates[o.selected.unwrap()] == upper, "{keys}: {:?}", o.candidates);
        let o = run(&mut e, "Left");
        assert!(o.candidates[o.selected.unwrap()] == lower, "{keys}: {:?}", o.candidates);
        // Choosing the last one: the one syllable becomes the token, and its list is the writings.
        let mut e = eng();
        run(&mut e, keys);
        select_in_window(&mut e, upper);
        assert!(e.syllables() == [format!("_punct_{upper}")], "{keys}: {:?}", e.syllables());
        let o = run(&mut e, "Down");
        assert!(o.candidates[0] == upper && o.candidates.contains(&lower.to_string()), "{keys}: {:?}", o.candidates);
    }
}

#[test]
fn short_list_after_preceding_syllables() {
    let mut e = eng();
    run(&mut e, "j i 3 u l 4");
    let before = e.syllables().to_vec();
    assert!(before == ["ㄨㄛˇ", "ㄧㄠˋ"]);
    let o = run(&mut e, "a i _ Down");
    let mut plain = eng();
    plain.set_english(false).unwrap();
    let p = run(&mut plain, "j i 3 u l 4 a i _ Down");
    assert!(o.total == p.total + 2 && o.candidates[..p.candidates.len()] == p.candidates[..], "the earlier items are unchanged: {:?} / {:?}", o.candidates, p.candidates);
    assert!(o.candidates[o.candidates.len() - 2..] == ["ai", "AI"]);
    select_in_window(&mut e, "AI");
    assert!(e.syllables() == ["ㄨㄛˇ", "ㄧㄠˋ", "_punct_AI"], "only the syllable the short word stood for goes: {:?}", e.syllables());
}

#[test]
fn short_list_is_only_for_the_syllable_just_made() {
    let total_of = |english: bool, keys: &str| {
        let mut e = eng();
        e.set_english(english).unwrap();
        run(&mut e, keys).total
    };
    for keys in ["a i _ s u 3 Down", "a i _ Left Down", "a i _ Left Right Down"] {
        assert!(total_of(true, keys) == total_of(false, keys), "{keys}: no ai/AI");
    }
    // A key that changes nothing (Up) leaves the mark, so the list still gets the items.
    let (with, without) = (total_of(true, "a i _ Up Down"), total_of(false, "a i _ Up Down"));
    assert!(with == without + 2, "Up changes nothing: {with} vs {without}");
}

#[test]
fn english_token_definition_covers_digits() {
    // Backspace behind the token opens the run `gta` (zhen-inline section 1.3g); the writings list of the token is the three writings.
    let mut e = eng();
    run(&mut e, "g t a 6 _");
    let o = run(&mut e, "BS");
    assert!(o.preedit == "gta" && fields(&o) == (0, 3), "{}", o.preedit);
    let mut e = eng();
    let o = run(&mut e, "g t a 6 _ Down");
    assert!(o.candidates == ["gta6", "Gta6", "GTA6"], "{:?}", o.candidates);
    // The learning key of a neighbour is the English one, as for `rust`.
    let dir = tmp_dir("gta6-key");
    let mut e = lm_eng_lexicon();
    e.learning_open(&dir).unwrap();
    syllable(&mut e, "ㄨㄛˇ");
    syllable(&mut e, "ㄕˋ");
    choose_word(&mut e, "我試");
    run(&mut e, "g t a 6 _");
    let o = run(&mut e, "Enter");
    assert!(o.commit == "我試gta6", "{}", o.commit);
    assert!(records(&e) == [("^>_punct_en".to_string(), "我試".to_string())], "{:?}", records(&e));
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn revert_hook_describes_only_the_latest_key() {
    let mut e = eng();
    run(&mut e, "o k");
    assert!(e.english_reverted() == Some(Revert::Overwrite));
    run(&mut e, "j");
    assert!(e.english_reverted().is_none(), "the next key clears it");
    // A pick, a reset or a setting neither sets nor clears it.
    let mut e = lm_eng();
    run(&mut e, "o k");
    drop(e.set_candidate_vertical(true).unwrap());
    drop(e.set_english_overwrite(true).unwrap());
    assert!(e.english_reverted() == Some(Revert::Overwrite), "settings leave the latest key's value");
    drop(e.reset(ResetMode::Discard));
    assert!(e.english_reverted() == Some(Revert::Overwrite), "so does a reset");
    run(&mut e, "Esc");
    assert!(e.english_reverted().is_none());
}

#[test]
fn eten_abbreviation_and_off_are_bitwise_today() {
    let build = |kind: &str, on: bool| {
        let layout = if kind == "eten" { Layout::Eten } else { Layout::Standard };
        let mut e = Engine::with_lexicon(real(), layout);
        if kind == "abbr" {
            e.set_abbreviation(true).unwrap();
        }
        e.set_english(on && kind != "off").unwrap();
        e
    };
    for kind in ["eten", "abbr", "off"] {
        for script in ["o k _", "l s _", "g t a 6 _", "c u p _", "s 7"] {
            let (mut with, mut without) = (build(kind, true), build(kind, false));
            let (a, b) = (run_all(&mut with, script), run_all(&mut without, script));
            assert!(a == b, "{kind} {script}");
            assert!(with.english_reverted().is_none() && !with.english_split(), "{kind} {script}");
            assert!(with.english_shadow().is_empty(), "{kind} {script}: no shadow where the reverts cannot happen");
        }
    }
}

#[test]
fn digits_layer_is_untouched() {
    use core::digits::DigitTable;
    let lex = real();
    let lm = Lm::load(&root().join("data/lm/bigram.sjlm")).unwrap();
    let overlay = capping_overlay(&root().join("data/lexicon"), "").unwrap();
    let capped = Arc::new(CappedLexicon::new(lex.clone(), &overlay, &lm, None).unwrap());
    let table = Arc::new(DigitTable::parse(&std::fs::read_to_string(root().join("data/lexicon/digits.tsv")).unwrap()).unwrap());
    let build = || {
        let mut e = Engine::with_lexicon(lex.clone(), Layout::Standard);
        e.set_lm(Arc::new(Lm::load(&root().join("data/lm/bigram.sjlm")).unwrap()), capped.clone());
        e.set_digit_table(Some(table.clone()));
        e.set_english(true).unwrap();
        e
    };
    for (keys, want) in [("2 0 2 6 s u 0 6 Enter", "2026年"), ("1 m , 4 Enter", "1月"), ("3 d s _ Enter", "3ds"), ("1 9 8 4 Enter", "1984")] {
        let mut e = build();
        let os = run_all(&mut e, keys);
        let commit: String = os.iter().map(|o| o.commit.as_str()).collect();
        assert!(commit == want, "{keys}: {commit}");
    }
}

// ---------- zhen-revert section 4.3: the exhaustive clean typing ----------

/// The Izanami test of vChewing (zhen-keyorder section 4, widened by zhen-revert section 4.3): every distinct syllable of the lexicon typed
/// alone, and a fixed-seed sample of its readings typed whole, in column order, each syllable ended by its tone key or Space, with the
/// three switches on, the digit layer off (an engine without a model): never a revert, never a split, and the composition is the one the
/// same keys make with English mixing off. So this is NOT every reading: only the sample is. The sample: the full set
/// is 416,227 readings, over the 60 s the contract allows, so `SAMPLE` readings are typed (splitmix64 shuffle from `SEED`: the same
/// readings on every run; the contract asks for at least 20,000).
#[test]
fn every_syllable_alone_and_sampled_readings_never_revert() {
    const SEED: u64 = 20261026;
    const SAMPLE: usize = 40_000;
    let lex = real();
    let mut e = Engine::with_lexicon(lex.clone(), Layout::Standard);
    e.set_english(true).unwrap();
    let mut plain = Engine::with_lexicon(lex.clone(), Layout::Standard);
    let type_one = |e: &mut Engine, plain: &mut Engine, syls: &[&str]| {
        e.reset(ResetMode::Discard);
        plain.reset(ResetMode::Discard);
        for syl in syls {
            for k in keys_of(syl) {
                e.key(k).unwrap();
                plain.key(k).unwrap();
                assert!(e.english_reverted().is_none() && !e.english_split(), "clean typing of {syls:?} looked like English");
            }
        }
        assert!(e.syllables() == plain.syllables(), "{syls:?}: {:?} / {:?}", e.syllables(), plain.syllables());
    };
    let n = lex.reading_count();
    let seen: std::collections::BTreeSet<&str> = (0..n).flat_map(|r| lex.reading_syllables(r)).collect();
    for s in &seen {
        type_one(&mut e, &mut plain, &[s]);
    }
    let mut chosen: Vec<usize> = (0..n).collect();
    let mut state = SEED;
    let mut next = || {
        state = state.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = state;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    };
    for i in (1..n).rev() {
        chosen.swap(i, (next() % (i as u64 + 1)) as usize);
    }
    chosen.truncate(SAMPLE);
    for &r in &chosen {
        type_one(&mut e, &mut plain, &lex.reading_syllables(r));
    }
    println!("typed {} of {n} readings and {} distinct syllables", chosen.len(), seen.len());
    assert!(n >= 20_000 && seen.len() > 1000 && chosen.len() >= 20_000, "the test saw enough");
}

/// zhen-reapply section 3.2: with the default switches (the reversal guard on), every REVERSED key order of every distinct syllable of the
/// lexicon (the symbol keys in any order but the column order, then the tone key or Space) is never English: no revert, no split, and the
/// composition is the one the same keys make with English mixing off. `reversed` must be non-trivial, or the test proves nothing.
#[test]
fn every_reversed_key_order_of_every_syllable_is_never_english() {
    let lex = real();
    let mut e = eng();
    let mut plain = Engine::with_lexicon(lex.clone(), Layout::Standard);
    let n = lex.reading_count();
    let seen: std::collections::BTreeSet<&str> = (0..n).flat_map(|r| lex.reading_syllables(r)).collect();
    let mut reversed = 0usize;
    for syl in &seen {
        let keys = keys_of(syl);
        let (tail, head) = keys.split_last().unwrap();
        let perms: Vec<Vec<Key>> = match head.len() {
            2 => vec![vec![head[1], head[0]]],
            3 => [[0, 2, 1], [1, 0, 2], [1, 2, 0], [2, 0, 1], [2, 1, 0]].iter().map(|p| p.iter().map(|&i| head[i]).collect()).collect(),
            _ => continue,
        };
        for p in perms {
            e.reset(ResetMode::Discard);
            plain.reset(ResetMode::Discard);
            for k in p.iter().chain(std::iter::once(tail)) {
                e.key(*k).unwrap();
                plain.key(*k).unwrap();
                assert!(e.english_reverted().is_none() && !e.english_split(), "a reversed {syl} looked like English");
            }
            assert!(e.syllables() == plain.syllables(), "{syl}: {:?} / {:?}", e.syllables(), plain.syllables());
            reversed += 1;
        }
    }
    println!("typed {reversed} reversed key orders of {} syllables", seen.len());
    assert!(reversed > 3000, "the test saw enough: {reversed}");
}

/// zhen-reapply section 3.1: the reversal guard (default on). (a) the six keyed-in-a-hurry examples are Chinese; (b) the four words
/// that need (a) overwrite or (c) are English tokens equal to the keys; (c) `l s` Space is `ls` with the guard off and Chinese with it on.
#[test]
fn reversal_guard_chinese_in_a_hurry_english_words_still_come() {
    for (keys, word) in [("k e 7", "個"), ("u v , _", "些"), ("j n o 6", "隨"), ("u r . 4", "就"), ("i a 7", "麼"), ("k x 7", "了")] {
        let mut e = eng();
        let o = run(&mut e, keys);
        assert!(e.english_reverted().is_none(), "{keys}");
        assert!(o.preedit.starts_with(word) || o.candidates.iter().any(|c| c == word), "{keys}: {} {:?}", o.preedit, o.candidates);
    }
    for (keys, word) in [("o k _", "ok"), ("g t a 6 _", "gta6"), ("r d r 2 _", "rdr2"), ("p o e 2 _", "poe2")] {
        let mut e = eng();
        let o = run(&mut e, keys);
        assert!(o.preedit == word && e.syllables() == [format!("_punct_{word}")], "{keys}: {} {:?}", o.preedit, e.syllables());
    }
    let mut e = eng();
    e.set_english_reversal_guard(false).unwrap();
    let o = run(&mut e, "l s _");
    assert!(o.preedit == "ls" && e.syllables() == ["_punct_ls"], "guard off: {} {:?}", o.preedit, e.syllables());
    let mut e = eng();
    let o = run(&mut e, "l s _");
    assert!(e.english_reverted().is_none() && o.preedit != "ls" && !e.syllables()[0].starts_with("_punct_"), "guard on: {}", o.preedit);
}

/// zhen-reapply: with the reversal guard on (default) a key can sit in a column before a filled one, so the shadow is read in the order
/// TYPED, not the column order. Every way a run is made from the shadow (a revert (a) or (c), Shift+letter, the ASCII state) must give the
/// letters as typed: `time`, never `tmie`.
#[test]
fn reversal_guard_keeps_the_typed_order_in_every_run() {
    for word in ["time", "home", "make", "user", "law"] {
        let keys = format!("{} _", word.chars().map(String::from).collect::<Vec<_>>().join(" "));
        let mut e = eng();
        let o = run(&mut e, &keys);
        assert!(o.preedit == word && e.syllables() == [format!("_punct_{word}")], "{word}: {} {:?}", o.preedit, e.syllables());
    }
    // (c) on Space: neither `m e` nor `i t` is a syllable of the lexicon, so each is one token, in the order typed.
    for (keys, word) in [("m e _", "me"), ("i t _", "it")] {
        let mut e = eng();
        let o = run(&mut e, keys);
        assert!(o.preedit == word && e.english_reverted() == Some(Revert::Missing), "{keys}: {}", o.preedit);
    }
    // Shift+letter and the ASCII state take the shadow in typed order too.
    let mut e = eng();
    let o = run(&mut e, "m e S-x");
    assert!(o.preedit == "meX", "{}", o.preedit);
    let mut e = eng();
    run(&mut e, "m e");
    let o = e.set_ascii(true).unwrap();
    assert!(o.preedit == "me", "{}", o.preedit);
    let mut e = eng();
    run(&mut e, "m e");
    e.set_ascii(true).unwrap();
    let o = run(&mut e, "1");
    assert!(o.preedit == "me1", "ASCII branch: {}", o.preedit);
    // The short-word list reads the typed order as well: `i a` Space is the Chinese ㄇㄛ typed back to front, and the keys typed are `ia`,
    // not the short word `ai`, so the candidate list has no `ai`/`AI` item (column order would have made it).
    let mut e = eng();
    run(&mut e, "i a _");
    assert!(e.english_reverted().is_none());
    let o = run(&mut e, "Down");
    assert!(!o.candidates.iter().any(|c| c.eq_ignore_ascii_case("ai")), "{:?}", o.candidates);
}

/// Section 4.3, the second exhaustive test: every word of `ENGLISH_SHORT` typed key by key and ended (`mp3` by its tone key, the rest by
/// Space) never reverts, makes the one syllable, and the candidate list ends with the lower and upper case of the word.
#[test]
fn short_list_never_reverts() {
    for w in ENGLISH_SHORT {
        let mut e = eng();
        for c in w.chars() {
            e.key(Key::ch(c, 0)).unwrap();
            assert!(e.english_reverted().is_none(), "{w}");
        }
        if !w.ends_with('3') {
            e.key(Key::new(KeyKind::Space)).unwrap();
        }
        assert!(e.syllables().len() == 1 && !e.syllables()[0].starts_with("_punct_") && e.english_reverted().is_none(), "{w}");
        let mut o = run(&mut e, "Down");
        assert!(o.total as usize >= 3, "{w}");
        for _ in 0..o.total {
            o = run(&mut e, "Right");
        }
        assert!(o.candidates[o.selected.unwrap()] == w.to_ascii_uppercase(), "{w}: {:?}", o.candidates);
        let o = run(&mut e, "Left");
        assert!(o.candidates[o.selected.unwrap()] == *w, "{w}: {:?}", o.candidates);
    }
}

#[test]
fn turning_mixing_on_mid_syllable_catches_the_shadow_up() {
    // The shadow always matches the columns (zhen-revert section 1): `o` typed with mixing off, the switch on, then `k` is `ok`.
    let mut e = Engine::with_lexicon(real(), Layout::Standard);
    run(&mut e, "o");
    e.set_english(true).unwrap();
    assert!(e.english_shadow() == "o");
    let o = run(&mut e, "k");
    assert!(o.preedit == "ok" && e.english_reverted() == Some(Revert::Overwrite), "{}", o.preedit);
    // The abbreviation composer turned off mid-syllable catches up the same way.
    let mut e = eng();
    e.set_abbreviation(true).unwrap();
    run(&mut e, "o");
    e.set_abbreviation(false).unwrap();
    assert!(e.english_shadow() == "o");
}

// ---------- review fixes: settings clear the short-word mark, no-op keys keep it ----------

/// Zhen-revert section 2.6: every settings function clears the short-word mark. `a i ␣`, a setter, ↓: no `ai`/`AI` items, and the same
/// setter on an engine with mixing off gives the same total. Without the setter the items are there (the premise).
#[test]
fn every_setting_clears_the_short_word_mark() {
    use core::tinylm::RerankLevel;
    let setters: Vec<(&str, Box<dyn Fn(&mut Engine)>)> = vec![
        ("set_fuzzy", Box::new(|e| drop(e.set_fuzzy(0).unwrap()))),
        ("set_profile", Box::new(|e| drop(e.set_profile(Profile::Formal).unwrap()))),
        ("set_prediction", Box::new(|e| drop(e.set_prediction(false).unwrap()))),
        ("set_demote", Box::new(|e| drop(e.set_demote(true).unwrap()))),
        ("set_candidate_vertical", Box::new(|e| drop(e.set_candidate_vertical(true).unwrap()))),
        ("set_rerank", Box::new(|e| drop(e.set_rerank(true).unwrap()))),
        ("set_rerank_level", Box::new(|e| drop(e.set_rerank_level(RerankLevel::Standard).unwrap()))),
        ("set_abbreviation", Box::new(|e| drop(e.set_abbreviation(false).unwrap()))),
        ("set_english_overwrite", Box::new(|e| drop(e.set_english_overwrite(true).unwrap()))),
        ("set_english_key_order", Box::new(|e| drop(e.set_english_key_order(true).unwrap()))),
        ("set_english", Box::new(|e| drop(e.set_english(true).unwrap()))),
        ("set_learn_guard", Box::new(|e| e.set_learn_guard(true))),
        ("set_reading_channel", Box::new(|e| e.set_reading_channel(true))),
        ("set_lm", Box::new(|e| {
            let (lm, capped) = lm_parts();
            e.set_lm(lm, capped);
        })),
    ];
    let total = |english: bool, setter: Option<&dyn Fn(&mut Engine)>| {
        let mut e = eng();
        e.set_english(english).unwrap();
        run(&mut e, "a i _");
        if let Some(f) = setter {
            f(&mut e);
        }
        run(&mut e, "Down").total
    };
    assert!(total(true, None) == total(false, None) + 2, "premise: the items are there");
    for (name, f) in &setters {
        // The expected total is the one of an engine where the mark is gone and nothing else differs: the same setter on the mixing-off
        // engine. `set_english(true)` would turn that engine on, so for it the baseline is the mixing-off engine as it is.
        let plain = if *name == "set_english" { total(false, None) } else { total(false, Some(f.as_ref())) };
        assert!(total(true, Some(f.as_ref())) == plain, "{name} left the short-word items");
    }
}

/// The exceptions to that rule (`Engine::setting_changed`'s comment): `set_learning` and `set_left_context` are called by the shell before
/// every key, and the table and learning knobs change neither the composition nor the short word's items, so none of them clears the mark.
#[test]
fn the_calls_the_shell_makes_before_every_key_keep_the_short_word_mark() {
    let total_with = |english: bool, call: Option<&dyn Fn(&mut Engine)>| {
        let mut e = eng();
        e.set_english(english).unwrap();
        run(&mut e, "a i _");
        if let Some(f) = call {
            f(&mut e);
        }
        run(&mut e, "Down").total
    };
    let base = total_with(false, None);
    let calls: Vec<(&str, Box<dyn Fn(&mut Engine)>)> = vec![
        ("set_learning", Box::new(|e| e.set_learning(true))),
        ("set_left_context", Box::new(|e| e.set_left_context(""))),
        ("set_punctuation", Box::new(|e| drop(e.set_punctuation("")))),
        ("set_newword", Box::new(|e| e.set_newword(true))),
        ("set_today", Box::new(|e| e.set_today(Some(20_000)))),
    ];
    for (name, f) in &calls {
        assert!(total_with(true, Some(f.as_ref())) == base + 2, "{name} cleared the mark");
    }
}

/// Every arm of `dispatch` that changes nothing gives the mark back (one rule in `key()`, fed by `Engine::nothing`): Up, Right and End at
/// the end, Delete at the end, a tone key with nothing unfinished, and a key passed on to the app (Cmd+C) all leave the two items.
#[test]
fn keys_that_change_nothing_keep_the_short_word_mark() {
    let total_of = |english: bool, keys: &str| {
        let mut e = eng();
        e.set_english(english).unwrap();
        run(&mut e, keys).total
    };
    for keys in ["a i _ Up Down", "a i _ Right Down", "a i _ Delete Down", "a i _ 3 Down", "a i _ End Down", "a i _ Up Up Down"] {
        assert!(total_of(true, keys) == total_of(false, keys) + 2, "{keys}: the mark was lost");
    }
    // Cmd+C is passed on (handled false, nothing sent): the mark stays.
    let mut e = eng();
    run(&mut e, "a i _");
    let o = e.key(Key::ch('c', MOD_COMMAND)).unwrap();
    assert!(!o.handled && o.commit.is_empty());
    let plain = total_of(false, "a i _ Down");
    assert!(run(&mut e, "Down").total == plain + 2, "a passed-on key lost the mark");
    // And keys that do change something still clear it: Left, Backspace, a Zhuyin key.
    for keys in ["a i _ Left Down", "a i _ BS Down", "a i _ s Esc Down"] {
        assert!(total_of(true, keys) == total_of(false, keys), "{keys}: the mark should be gone");
    }
}

/// Review fix 1: a missing syllable at Space makes a finished token with no restore point (zhen-revert section 2.4: only an unedited run
/// that a revert opened is restorable). Esc right after it is Esc after any English token: today that clears the whole composition, and
/// this pins that it stays so (the settings notes no longer promise an Esc back for it). The tone-key case keeps its Esc back.
#[test]
fn esc_after_a_space_token_is_esc_after_any_token() {
    let after = |keys: &str| {
        let mut e = eng();
        run(&mut e, "j i 3 u l 4"); // 我要
        let o = run(&mut e, keys);
        (o.preedit, is_empty(&mut e))
    };
    let (cup, cup_empty) = after("c u p _ Esc");
    let (shift, shift_empty) = after("S-a b _ Esc");
    assert!(cup.is_empty() && cup_empty && shift.is_empty() && shift_empty, "cup: {cup:?}, Shift run: {shift:?}");
    // The tone key keeps the run open with a restore point: Esc brings the Zhuyin, the second clears the unfinished syllable, 我要 stays.
    let mut e = eng();
    run(&mut e, "j i 3 u l 4");
    let before = run(&mut e, "Left Right").preedit;
    run(&mut e, "s 7");
    assert!(run(&mut e, "Esc").preedit == format!("{before}ㄋ"));
    assert!(run(&mut e, "Esc").preedit == before);
}

// ---------- shift-toggle section 5: the ASCII state (`set_ascii`) ----------

/// The engine with the model and the digit table, for the rows and the digit run (as `digits_layer_is_untouched` builds it).
fn asc_eng() -> Engine {
    use core::digits::DigitTable;
    let lex = real();
    let lm = Lm::load(&root().join("data/lm/bigram.sjlm")).unwrap();
    let overlay = capping_overlay(&root().join("data/lexicon"), "").unwrap();
    let capped = Arc::new(CappedLexicon::new(lex.clone(), &overlay, &lm, None).unwrap());
    let table = Arc::new(DigitTable::parse(&std::fs::read_to_string(root().join("data/lexicon/digits.tsv")).unwrap()).unwrap());
    let mut e = Engine::with_lexicon(lex, Layout::Standard);
    e.set_lm(Arc::new(lm), capped);
    e.set_digit_table(Some(table));
    e.set_english(true).unwrap();
    e
}
fn commits(os: &[Output]) -> String {
    os.iter().map(|o| o.commit.as_str()).collect()
}

#[test]
fn ascii_keeps_the_composition_and_the_letters_are_english() {
    let mut e = eng();
    run(&mut e, "j i 3 u l 4"); // 我要
    e.set_ascii(true).unwrap();
    let os = run_all(&mut e, "o k Enter");
    assert!(commits(&os) == "我要ok", "{}", commits(&os));
    assert!(e.english_reverted().is_none(), "no revert in the ASCII state");
}

#[test]
fn ascii_off_returns_the_zhuyin_rules_and_ends_the_run_without_a_space() {
    let mut e = eng();
    run(&mut e, "j i 3 u l 4");
    e.set_ascii(true).unwrap();
    run(&mut e, "o k");
    let o = e.set_ascii(false).unwrap();
    assert!(fields(&o) == (0, 0) && o.preedit.ends_with("ok"), "{}", o.preedit);
    let os = run_all(&mut e, "j i 3 Enter");
    let c = commits(&os);
    assert!(c.starts_with("我要ok") && c.chars().count() == 5 && c != "我要okji3", "ok then the decoded ㄨㄛˇ: {c}");
}

#[test]
fn ascii_turns_the_unfinished_syllable_into_the_run() {
    let mut e = eng();
    run(&mut e, "c"); // ㄏ
    let o = e.set_ascii(true).unwrap();
    assert!(o.handled && o.preedit == "c" && fields(&o) == (0, 1), "the snapshot shows the run: {} {:?}", o.preedit, fields(&o));
    let o = run(&mut e, "o");
    assert!(o.preedit == "co" && fields(&o) == (0, 2), "{} {:?}", o.preedit, fields(&o));
}

#[test]
fn ascii_closes_the_window_without_choosing() {
    let mut e = eng();
    let before = syllable(&mut e, "ㄨㄛˇ").preedit;
    let o = run(&mut e, "Down");
    assert!(!o.candidates.is_empty(), "premise: the window is open");
    let o = e.set_ascii(true).unwrap();
    assert!(o.candidates.is_empty() && o.selected.is_none() && o.preedit == before, "{} / {before}", o.preedit);
}

#[test]
fn ascii_closes_the_prediction_row_and_one_is_a_character() {
    let build = || {
        let mut e = asc_eng();
        e.set_left_context("我想喝一杯");
        run(&mut e, "s"); // ㄋ: the row is passive
        e
    };
    // Entered row, then ASCII: `1` does not pick.
    let mut e = build();
    let o = run(&mut e, "Tab");
    assert!(o.selected == Some(0) && !o.candidates.is_empty(), "premise: the row is entered");
    let o = e.set_ascii(true).unwrap();
    assert!(o.candidates.is_empty() && o.selected.is_none(), "the snapshot has no row");
    let o = run(&mut e, "1");
    assert!(o.commit.is_empty() && o.preedit == "s1" && o.candidates.is_empty(), "{} {}", o.commit, o.preedit);
    // A row that comes back while ASCII is on (Backspace empties the run) and is entered: `1` is still a character, which is the
    // order of the branch before the entered row.
    let mut e = asc_eng();
    e.set_left_context("我想喝一杯");
    syllable(&mut e, "ㄋㄞˇ");
    e.set_ascii(true).unwrap();
    run(&mut e, "x BS");
    let o = run(&mut e, "Tab");
    assert!(o.selected == Some(0), "premise: the row is entered while ASCII is on");
    let o = run(&mut e, "1");
    assert!(o.commit.is_empty() && o.preedit.ends_with('1') && o.selected.is_none(), "{} {:?}", o.preedit, o.selected);
}

#[test]
fn ascii_ends_the_digit_run_as_tokens() {
    let mut e = asc_eng();
    run(&mut e, "1 2");
    let o = e.set_ascii(true).unwrap();
    assert!(o.preedit == "12" && fields(&o) == (0, 0), "{}", o.preedit);
    let o = run(&mut e, "a");
    assert!(o.preedit == "12a" && fields(&o) == (2, 1), "{} {:?}", o.preedit, fields(&o));
    let o = run(&mut e, "3");
    assert!(o.preedit == "12a3" && fields(&o) == (2, 2), "the digit goes on the run: {} {:?}", o.preedit, fields(&o));
}

#[test]
fn ascii_characters_go_on_the_run_as_typed() {
    let mut e = eng();
    run(&mut e, "j i 3");
    e.set_ascii(true).unwrap();
    run(&mut e, "a");
    let o = run(&mut e, "3 / O ,");
    assert!(o.preedit.ends_with("a3/O,"), "{}", o.preedit);
    assert!(fields(&o).1 == 5 && e.english_reverted().is_none() && !e.english_split(), "{:?}", fields(&o));
    let o = run(&mut e, "Enter");
    assert!(o.commit.ends_with("a3/O,"), "{}", o.commit);
}

/// Implementation revision 2026-10-28: a character `english_of` does not accept settles the open run and is its own half-width unit.
#[test]
fn ascii_other_punctuation_settles_the_run_and_is_its_own_unit() {
    let mut e = eng();
    e.set_ascii(true).unwrap();
    run(&mut e, "w o w");
    let o = run(&mut e, "!");
    assert!(o.preedit == "wow!" && fields(&o) == (0, 0), "{} {:?}", o.preedit, fields(&o));
    assert!(e.syllables() == ["_punct_wow", "_punct_!"], "{:?}", e.syllables());
    // Space after it types a space unit (nothing is open), and Backspace takes that unit off first.
    let o = run(&mut e, "_");
    assert!(o.preedit == "wow! ", "{}", o.preedit);
    run(&mut e, "BS");
    // Backspace removes the unit whole; the token before it stays, and one more Backspace reopens it minus its last letter.
    let o = run(&mut e, "BS");
    assert!(o.preedit == "wow" && e.syllables() == ["_punct_wow"], "{} {:?}", o.preedit, e.syllables());
    let o = run(&mut e, "BS");
    assert!(o.preedit == "wo" && fields(&o) == (0, 2), "{} {:?}", o.preedit, fields(&o));
    // Every other printable character that is not in the English alphabet behaves the same.
    for c in ['?', '@', '\'', '(', ':', '"', '_'] {
        let mut e = eng();
        e.set_ascii(true).unwrap();
        run(&mut e, "a b");
        let o = e.key(Key::ch(c, 0)).unwrap();
        assert!(o.preedit == format!("ab{c}") && fields(&o) == (0, 0), "{c}: {}", o.preedit);
        assert!(e.syllables() == ["_punct_ab".to_string(), format!("_punct_{c}")], "{c}: {:?}", e.syllables());
    }
}

/// A one-character run `3` settles as the digit token `_punct_3` (what `english_of` leaves to `digit_of`); it shows `3`, as any run of
/// that one character did before.
#[test]
fn ascii_lone_digit_run_settles_as_the_digit_token() {
    let mut e = eng();
    e.set_ascii(true).unwrap();
    run(&mut e, "3");
    let o = run(&mut e, "_");
    assert!(o.preedit == "3" && e.syllables() == ["_punct_3"], "{} {:?}", o.preedit, e.syllables());
}

#[test]
fn ascii_without_a_shadow_leaves_the_composition_alone() {
    let mut e = Engine::with_lexicon(real(), Layout::Eten);
    e.set_english(true).unwrap();
    let before = run(&mut e, "s e").preedit; // two Zhuyin keys pending in the Eten layout
    assert!(!before.is_empty());
    let o = e.set_ascii(true).unwrap();
    assert!(o.preedit == before && fields(&o) == (0, 0), "{} / {before}", o.preedit);
    // The ASCII branch does not apply either: the key is a Zhuyin key as ever.
    let o = run(&mut e, "a");
    assert!(fields(&o) == (0, 0) && o.preedit != before, "{}", o.preedit);
}

#[test]
fn ascii_run_is_limited_to_32_and_one_after_down_is_a_character() {
    let mut e = eng();
    run(&mut e, "j i 3");
    e.set_ascii(true).unwrap();
    let o = run(&mut e, &"a ".repeat(33));
    assert!(fields(&o).1 == 1, "the 33rd starts a new run: {:?}", fields(&o));
    // The run ends with Space, Down opens the writings list of the token, `1` closes it and opens a new run.
    let mut e = eng();
    run(&mut e, "j i 3");
    e.set_ascii(true).unwrap();
    run(&mut e, "o k _ Down");
    let o = run(&mut e, "1");
    assert!(o.candidates.is_empty() && o.commit.is_empty() && o.preedit.ends_with("ok1") && fields(&o).1 == 1, "{} {:?}", o.preedit, fields(&o));
}

#[test]
fn ascii_on_an_empty_composition_changes_nothing() {
    let mut e = eng();
    let o = e.set_ascii(true).unwrap();
    assert!(o.handled && o.preedit.is_empty() && o.commit.is_empty() && fields(&o) == (0, 0));
    let o = e.set_ascii(false).unwrap();
    assert!(o.preedit.is_empty() && o.commit.is_empty());
    assert!(is_empty(&mut e));
}

#[test]
fn ascii_space_after_a_token_types_a_space() {
    for (name, mut e) in [("no digit table", eng()), ("digit table", asc_eng())] {
        e.set_ascii(true).unwrap();
        run(&mut e, "H i ! _ t h e r e");
        let o = run(&mut e, "Enter");
        assert!(o.commit == "Hi! there", "{name}: {}", o.commit);
    }
    for (name, mut e) in [("no digit table", eng()), ("digit table", asc_eng())] {
        e.set_ascii(true).unwrap();
        run(&mut e, "3 _ _ a p p l e s");
        let o = run(&mut e, "Enter");
        assert!(o.commit == "3 apples", "{name}: {}", o.commit);
    }
    // With a run open Space only ends it: `ok` Space Enter is `ok`, not `ok `.
    let mut e = eng();
    e.set_ascii(true).unwrap();
    run(&mut e, "o k _");
    assert!(run(&mut e, "Enter").commit == "ok");
}

/// A one-character run of `.`, `/` or `-` is in the digit alphabet, so it settles as a digit token (`digit_of`), as `3` does.
#[test]
fn ascii_lone_dot_run_settles_as_a_digit_token() {
    let mut e = eng();
    e.set_ascii(true).unwrap();
    run(&mut e, ".");
    let o = run(&mut e, "_");
    assert!(o.preedit == "." && e.syllables() == ["_punct_."], "{} {:?}", o.preedit, e.syllables());
}

/// The state is recorded without a shadow; when the shadow comes back the next character does the conversion itself.
#[test]
fn ascii_recorded_without_a_shadow_converts_when_the_shadow_returns() {
    let mut e = eng();
    e.set_abbreviation(true).unwrap();
    run(&mut e, "c"); // ㄏ, no shadow recorded while the abbreviation composer is on
    e.set_ascii(true).unwrap();
    e.set_abbreviation(false).unwrap();
    let o = run(&mut e, "o");
    assert!(o.preedit == "co" && fields(&o) == (0, 2), "{} {:?}", o.preedit, fields(&o));
}

#[test]
fn ascii_33rd_character_in_front_of_units_decodes_again() {
    let mut e = eng();
    run(&mut e, "j i 3 u l 4 Left Left");
    e.set_ascii(true).unwrap();
    let o = run(&mut e, &"a ".repeat(33));
    assert!(o.preedit.starts_with(&"a".repeat(33)) && fields(&o) == (32, 1), "{} {:?}", o.preedit, fields(&o));
}
