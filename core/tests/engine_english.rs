//! zhen-mixed engine tests (docs/contracts/zhen-mixed.md section 5.1): the shadow, the trigger, the English window and
//! its keys, on the real lexicon and the synthetic list eval/golden/english-tiny.txt. And the zhen-inline tests
//! (docs/contracts/zhen-inline.md section 4.1): the English token that stays in the composition, and the display rule D.
//! `eng()` is the "show on Space" setting (the zhen-mixed display, Zhuyin until the window opens); `eng_inline()` the default,
//! "show as typed".
//!
//! What each word of the list is for (the contract keeps this out of the file). Keys are the standard layout; "overwrites" is
//! how many times a key replaces a symbol in an occupied column (rule 9); the leftover is the unfinished syllable with tone 1:
//!   rust    leftover ㄔㄧ (not in the lexicon), 2 overwrites: opens, three items.
//!   google  leftover ㄍㄠ (in the lexicon), 4 overwrites: opens, a Chinese fourth item.
//!   law     leftover ㄊㄠ (in the lexicon), 1 overwrite: a valid syllable needs two, so it does not open. (The contract names
//!           `bug`, whose leftover ㄕㄧ is not a syllable of the lexicon; contract section 5.1 says to swap the word then.)
//!   dip     leftover ㄎㄣ (not in the lexicon), 1 overwrite: opens.
//!   cup     0 overwrites, leftover ㄏㄧㄣ (not in the lexicon): never opens.
//!   hello   unused by the keys below (a list of six).
//! `tiny_list_leftovers_are_as_documented` checks the leftovers against the lexicon, so a lexicon change shows up there.
//!
//! Key script, whitespace separated: a single character is that keycap, `_` Space, `Enter`, `Esc`, `BS` Backspace, `Left`,
//! `Right`, `Down`, `S-Enter` and `S-,` with Shift, `Cmd-BS` with Command.
use core::engine::*;
use core::lm::{CappedLexicon, Lm};
use core::Lexicon;
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}
fn tiny_list() -> PathBuf {
    root().join("eval/golden/english-tiny.txt")
}
fn real() -> Arc<Lexicon> {
    static L: OnceLock<Arc<Lexicon>> = OnceLock::new();
    L.get_or_init(|| load_lexicon(&root().join("data/lexicon")).unwrap()).clone()
}
/// Standard layout, the list loaded, the switch on, the display "show on Space" (zhen-mixed).
fn eng() -> Engine {
    let mut e = eng_inline();
    e.set_english_inline(false);
    e
}
/// The same with the default display, "show as typed" (zhen-inline).
fn eng_inline() -> Engine {
    let mut e = Engine::with_lexicon(real(), Layout::Standard);
    e.load_english(&tiny_list()).unwrap();
    e.set_english(true).unwrap();
    e
}
fn key_of(tok: &str) -> Key {
    let with = |kind, modifiers| Key { kind, ch: '\0', modifiers };
    match tok {
        "_" => Key::new(KeyKind::Space),
        "Enter" => Key::new(KeyKind::Enter),
        "Esc" => Key::new(KeyKind::Esc),
        "BS" => Key::new(KeyKind::Backspace),
        "Left" => Key::new(KeyKind::Left),
        "Right" => Key::new(KeyKind::Right),
        "Down" => Key::new(KeyKind::Down),
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
#[allow(dead_code)]
fn run_all(e: &mut Engine, script: &str) -> Vec<Output> {
    script.split_whitespace().map(|t| e.key(key_of(t)).unwrap()).collect()
}
/// The keys of one syllable ("ㄍㄠ": Zhuyin keys, then the tone key or Space), as the replays send it.
fn syllable(e: &mut Engine, syl: &str) -> Output {
    let layout = Layout::Standard;
    let mut last = None;
    let mut toned = false;
    for c in syl.chars() {
        let k = match layout.key_of_tone(c) {
            Some(tk) => {
                toned = true;
                Key::ch(tk, 0)
            }
            None => Key::ch(layout.key_of_symbol(c).unwrap(), 0),
        };
        last = Some(e.key(k).unwrap());
    }
    if !toned {
        last = Some(e.key(Key::new(KeyKind::Space)).unwrap());
    }
    last.unwrap()
}
/// What the composition shows when it is the one syllable `syl`: the proof that `syls == [syl]` without an accessor.
fn shows(syl: &str) -> String {
    let mut e = Engine::with_lexicon(real(), Layout::Standard);
    syllable(&mut e, syl).preedit
}
/// No window and the unfinished syllable `pend` still in the preedit.
fn assert_pend_only(o: &Output, pend: &str) {
    assert!(o.candidates.is_empty() && o.selected.is_none() && o.total == 0, "no window");
    assert!(o.preedit == pend, "the unfinished syllable is unchanged");
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

#[test]
fn tiny_list_leftovers_are_as_documented() {
    let lex = real();
    let has = |syl: &str| !lex.entries(&[syl.to_string()]).is_empty();
    assert!(!has("ㄔㄧ") && has("ㄍㄠ") && has("ㄊㄠ") && !has("ㄎㄣ") && !has("ㄏㄧㄣ"));
    // And the overwrite counts: the words' own keys against the rule 9 columns.
    let layout = Layout::Standard;
    let over = |w: &str| {
        let mut col = [false; 3];
        let mut n = 0;
        for c in w.chars() {
            let (i, _) = layout.symbol_of(c).expect("a Zhuyin key");
            n += col[i] as usize;
            col[i] = true;
        }
        n
    };
    assert_eq!([over("rust"), over("google"), over("law"), over("dip"), over("cup")], [2, 4, 1, 1, 0]);
}

#[test]
fn opens_on_space_keeps_the_word_in_the_composition() {
    let mut e = eng();
    let o = run(&mut e, "r u s t _");
    assert!(e.english_open());
    assert!(o.candidates == ["rust", "Rust", "RUST"] && o.selected == Some(0) && o.total == 3, "three items");
    assert!(o.preedit == "ㄔㄧ", "the unfinished syllable stays in the preedit");
    let o = run(&mut e, "1");
    assert!(o.handled && o.commit.is_empty(), "nothing is sent");
    assert!(o.preedit == "rust" && o.cursor_utf16 == 4, "the word is in the composition, the cursor behind it");
    assert!(o.candidates.is_empty() && o.selected.is_none() && !e.english_open());
    assert!(!is_empty(&mut e), "the composition is not empty");
    assert!(e.english_shadow_len() == 0);
    assert!(run(&mut e, "Enter").commit == "rust", "Enter sends it, with no space");
}

#[test]
fn other_items_stay_as_their_own_text() {
    for (digit, want) in [("2", "Rust"), ("3", "RUST")] {
        let mut e = eng();
        run(&mut e, "r u s t _");
        let o = run(&mut e, digit);
        assert!(o.commit.is_empty() && o.preedit == want);
    }
    // Right moves the selection, Enter takes it.
    let mut e = eng();
    run(&mut e, "r u s t _ Right");
    let o = run(&mut e, "Enter");
    assert!(o.commit.is_empty() && o.preedit == "Rust");
}

#[test]
fn enter_and_shift_enter_pick() {
    // With the window open Enter is the window's: the first item stays in the composition, nothing is sent (zhen-inline 1.3).
    for last in ["Enter", "S-Enter"] {
        for mut e in [eng(), eng_inline()] {
            let o = run(&mut e, &format!("r u s t _ {last}"));
            assert!(o.handled && o.commit.is_empty() && o.preedit == "rust" && !e.english_open(), "{last}");
        }
    }
}

#[test]
fn pick_equals_digit() {
    let (mut a, mut b) = (eng(), eng());
    run(&mut a, "r u s t _");
    run(&mut b, "r u s t _");
    let by_pick = a.pick(0).unwrap().unwrap();
    assert!(by_pick == b.key(Key::ch('1', 0)).unwrap());
    assert!(by_pick.commit.is_empty() && by_pick.preedit == "rust");
}

#[test]
fn left_composition_stays_with_the_word() {
    let mut e = eng();
    e.set_left_context("他");
    run(&mut e, "s u 3");
    let you = shows("ㄋㄧˇ");
    let o = run(&mut e, "r u s t _ 1");
    assert!(o.commit.is_empty() && o.preedit == format!("{you}rust"), "the shown composition, then the word, not sent");
    assert!(o.cursor_utf16 as usize == o.preedit.encode_utf16().count(), "the cursor is last");
    assert!(you == "你", "premise: the composition shows the character");
    assert!(run(&mut e, "Enter").commit == format!("{you}rust"));
}

/// Types ㄊㄞˊ ㄨㄢ, opens the candidates and takes the first two-character word that is not the one shown, so a learn is
/// pending (`pre` is the shown text). The output of that pick. `before` is what the composition already shows.
fn repick_two(e: &mut Engine, before: &str) -> Output {
    syllable(e, "ㄊㄞˊ");
    let shown = syllable(e, "ㄨㄢ").preedit.strip_prefix(before).unwrap().to_string();
    let mut o = e.key(Key::new(KeyKind::Space)).unwrap(); // the two-syllable candidates first
    let mut found = false;
    for _ in 0..200 {
        let w = o.candidates[o.selected.unwrap()].clone();
        if w.chars().count() == 2 && w != shown {
            found = true;
            break;
        }
        o = e.key(Key::new(KeyKind::Right)).unwrap();
    }
    assert!(found, "premise: a two-character word other than {shown} exists");
    let o = e.key(Key::new(KeyKind::Enter)).unwrap();
    assert!(o.commit.is_empty() && o.preedit != format!("{before}{shown}"), "premise: the pick is fixed over the span");
    o
}

#[test]
fn learning_follows_the_commit_rules() {
    // The same re-pick, alone (control) or after `rust` kept in the composition: the learning file must be the same bytes (the
    // English word is never learned, and the word after it starts a sentence, so its key is the same `^>$`).
    let (da, db) = (tmp_dir("learn-a"), tmp_dir("learn-b"));
    let mut commits = Vec::new();
    for (dir, english) in [(&da, false), (&db, true)] {
        let mut e = eng();
        e.set_today(Some(20_000));
        e.set_learning(true);
        e.learning_open(dir).unwrap();
        if english {
            run(&mut e, "r u s t _ 1");
        }
        repick_two(&mut e, if english { "rust" } else { "" });
        commits.push(run(&mut e, "Enter").commit);
    }
    assert!(commits[1] == format!("rust{}", commits[0]), "the commit is the word plus the composition: {} / {}", commits[0], commits[1]);
    let file = |d: &Path| std::fs::read_to_string(d.join("learning.tsv")).unwrap();
    assert!(file(&da).lines().count() >= 2, "premise: the control learned a record");
    assert!(file(&da) == file(&db), "the learning file is the one Enter writes");
    let _ = std::fs::remove_dir_all(&da);
    let _ = std::fs::remove_dir_all(&db);
}

#[test]
fn valid_residual_needs_two_overwrites() {
    let mut e = eng();
    let o = run(&mut e, "l a w _");
    assert!(!e.english_open() && o.candidates.is_empty(), "one overwrite and a valid leftover: no window");
    assert!(o.preedit == shows("ㄊㄠ"), "rule 10 took the syllable");
    assert!(run(&mut e, "Left").preedit == o.preedit);
}

#[test]
fn valid_residual_with_two_overwrites_opens() {
    let mut e = eng();
    let o = run(&mut e, "g o o g l e _");
    assert!(e.english_open() && o.total == 4, "four items");
    assert!(o.candidates[..3] == ["google", "Google", "GOOGLE"]);
    let first = real().entries(&["ㄍㄠ".to_string()])[0].0.to_string();
    assert!(o.candidates[3] == first, "the lexicon's first entry of the leftover");
}

#[test]
fn choose_chinese_item() {
    // Real lexicon: the character shows, no window, no unfinished syllable.
    let mut e = eng();
    run(&mut e, "g o o g l e _");
    let item = real().entries(&["ㄍㄠ".to_string()])[0].0.to_string();
    let o = run(&mut e, "4");
    assert!(o.candidates.is_empty() && o.selected.is_none() && !e.english_open() && e.english_shadow_len() == 0);
    assert!(o.preedit == item && o.commit.is_empty(), "the shown character is the fourth item");

    // A tiny lexicon tells a fixed word from a free one: free, ㄅㄚ ㄍㄠ would decode as the word 芭膏; fixed, the item 高
    // stays and ㄅㄚ decodes alone.
    let lex = Arc::new(Lexicon::parse("ㄅㄚ 爸 -1.0\nㄍㄠ 高 -1.0\nㄍㄠ 膏 -3.0\nㄅㄚ-ㄍㄠ 芭膏 -1.0\n").unwrap());
    let dir = tmp_dir("choose-zh");
    let mut e = Engine::with_lexicon(lex, Layout::Standard);
    e.load_english(&tiny_list()).unwrap();
    e.set_english(true).unwrap();
    e.set_english_inline(false);
    e.set_learning(true);
    e.learning_open(&dir).unwrap();
    assert!(run(&mut e, "1 8 _").preedit == "爸");
    run(&mut e, "g o o g l e _");
    let o = run(&mut e, "4");
    assert!(o.preedit == "爸高", "a fixed word of length 1 (free decoding would show 芭膏)");
    assert!(run(&mut e, "Enter").commit == "爸高");
    assert!(!dir.join("learning.tsv").exists(), "pre is None: nothing is learned");
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn invalid_residual_one_overwrite_opens() {
    let mut e = eng();
    let o = run(&mut e, "d i p _");
    assert!(e.english_open() && o.total == 3 && o.candidates == ["dip", "Dip", "DIP"]);
}

#[test]
fn zero_overwrite_never_opens() {
    let mut e = eng();
    let o = run(&mut e, "c u p _");
    assert!(!e.english_open());
    assert_pend_only(&o, "ㄏㄧㄣ");
}

#[test]
fn not_in_list_never_opens() {
    let mut e = eng();
    let o = run(&mut e, "r u s t y _");
    assert!(!e.english_open());
    assert_pend_only(&o, "ㄗㄧ");
}

#[test]
fn esc_then_space_is_tone_one() {
    let mut e = eng();
    run(&mut e, "g o o g l e _");
    let o = run(&mut e, "Esc");
    assert_pend_only(&o, "ㄍㄠ");
    assert!(e.english_shadow_len() == 0 && !e.english_open());
    let o = run(&mut e, "_");
    assert!(!e.english_open() && o.preedit == shows("ㄍㄠ"), "syls == [ㄍㄠ]");
}

#[test]
fn backspace_in_window_clears_shadow() {
    let mut e = eng();
    run(&mut e, "g o o g l e _");
    let o = run(&mut e, "BS");
    assert_pend_only(&o, "ㄍㄠ");
    assert!(e.english_shadow_len() == 0 && !e.english_open());
    let o = run(&mut e, "_");
    assert!(!e.english_open() && o.preedit == shows("ㄍㄠ"));
}

#[test]
fn space_in_window_wraps() {
    let mut e = eng();
    let o = run(&mut e, "r u s t _ _");
    assert!(e.english_open() && o.selected == Some(0) && o.total == 3);
}

#[test]
fn digit_beyond_total_is_ignored() {
    let mut e = eng();
    let before = run(&mut e, "r u s t _");
    let o = run(&mut e, "5");
    assert!(e.english_open() && o == before, "state unchanged");
}

#[test]
fn letter_in_window_closes_and_restarts() {
    let mut e = eng();
    run(&mut e, "r u s t _");
    let o = run(&mut e, "w");
    assert!(!e.english_open() && o.candidates.is_empty());
    assert!(e.english_shadow_len() == 1 && o.preedit == "ㄊㄧ", "w replaced ㄔ and started a new shadow");
    let o = run(&mut e, "_");
    assert!(!e.english_open() && o.preedit == shows("ㄊㄧ"), "syls == [ㄊㄧ]");
}

#[test]
fn punctuation_in_window() {
    let mut e = eng();
    run(&mut e, "r u s t _");
    let o = run(&mut e, "S-,");
    assert!(o.preedit == "，" && o.candidates.is_empty() && !e.english_open() && e.english_shadow_len() == 0);
}

#[test]
fn cmd_backspace_is_eaten() {
    // The learning file exists first: teach one record and commit it.
    let dir = tmp_dir("cmd-bs");
    let mut e = eng();
    e.set_today(Some(20_000));
    e.set_learning(true);
    e.learning_open(&dir).unwrap();
    repick_two(&mut e, "");
    run(&mut e, "Enter");
    let file = dir.join("learning.tsv");
    let bytes = std::fs::read(&file).unwrap();
    assert!(bytes.iter().filter(|&&b| b == b'\n').count() >= 2, "premise: the file has a record");

    let before = run(&mut e, "r u s t _");
    assert!(e.english_open());
    let o = run(&mut e, "Cmd-BS");
    assert!(o.handled && e.english_open() && o == before, "eaten: handled, nothing changes");
    assert!(std::fs::read(&file).unwrap() == bytes, "the learning file is untouched");
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn backspace_pops_shadow() {
    let mut e = eng();
    let o = run(&mut e, "r u s t t BS");
    assert!(o.preedit == "ㄔ" && e.english_shadow_len() == 4, "rule 11 took ㄧ; the shadow lost its last key");
    let o = run(&mut e, "_");
    assert!(e.english_open() && o.total == 4, "leftover ㄔ is in the lexicon, three overwrites");
}

#[test]
fn backspace_to_empty_clears_shadow() {
    let mut e = eng();
    run(&mut e, "r u BS BS");
    assert!(e.english_shadow_len() == 0);
    let o = run(&mut e, "s t _");
    assert!(!e.english_open() && o.preedit == shows("ㄔ"), "syls == [ㄔ]");
}

#[test]
fn thirty_third_key_clears_shadow() {
    let mut e = eng();
    let thirty_two = "a ".repeat(32);
    run(&mut e, &thirty_two);
    assert!(e.english_shadow_len() == 32);
    run(&mut e, "a");
    assert!(e.english_shadow_len() == 0, "the 33rd key empties it");
    let o = run(&mut e, "_");
    assert!(!e.english_open());
    assert_pend_only(&o, "ㄇ");
    run(&mut e, "r u s t _");
    assert!(e.english_open(), "the shadow starts again");
}

#[test]
fn cursor_not_at_end() {
    let mut e = eng();
    run(&mut e, "s u 3 c l 3 Left");
    let o = run(&mut e, "r u s t _");
    assert!(!e.english_open() && o.candidates.is_empty() && o.selected.is_none());
    assert!(o.preedit.contains("ㄔㄧ"), "the unfinished syllable is unchanged");
}

#[test]
fn off_means_no_shadow() {
    let mut e = Engine::with_lexicon(real(), Layout::Standard);
    e.load_english(&tiny_list()).unwrap();
    let o = run(&mut e, "r u s t");
    assert!(e.english_shadow_len() == 0);
    let o2 = run(&mut e, "_");
    assert!(!e.english_open() && o2.candidates.is_empty());
    assert!(o.preedit == "ㄔㄧ" && o2.preedit == "ㄔㄧ", "rule 10 did not take it");
}

#[test]
fn no_list_means_no_window() {
    let mut e = Engine::with_lexicon(real(), Layout::Standard);
    e.set_english(true).unwrap();
    run(&mut e, "r u s t");
    assert!(e.english_shadow_len() == 0);
    let o = run(&mut e, "_");
    assert!(!e.english_open());
    assert_pend_only(&o, "ㄔㄧ");
}

#[test]
fn eten_is_bitwise_off() {
    let mk = |on: bool| {
        let mut e = Engine::with_lexicon(real(), Layout::Eten);
        e.load_english(&tiny_list()).unwrap();
        e.set_english(on).unwrap();
        e
    };
    let (mut on, mut off) = (mk(true), mk(false));
    for tok in "r u s t _ 1 r u s t _ Esc".split_whitespace() {
        let k = key_of(tok);
        assert!(on.key(k).unwrap() == off.key(k).unwrap(), "{tok}");
        assert!(on.english_shadow_len() == 0 && !on.english_open());
    }
}

#[test]
fn turning_off_closes_window() {
    let mut e = eng();
    run(&mut e, "r u s t _");
    let o = e.set_english(false).unwrap();
    assert_pend_only(&o, "ㄔㄧ");
    assert!(e.english_shadow_len() == 0 && !e.english_open());
    let o = run(&mut e, "_");
    assert_pend_only(&o, "ㄔㄧ"); // rule 10: not in the lexicon
}

#[test]
fn abbreviation_on_closes_window() {
    let mut e = eng();
    run(&mut e, "r u s t _");
    let o = e.set_abbreviation(true).unwrap();
    assert_pend_only(&o, "ㄔㄧ");
    assert!(e.english_shadow_len() == 0 && !e.english_open());
    run(&mut e, "x");
    assert!(e.english_shadow_len() == 0, "never recorded while the abbreviation composer is on");
}

#[test]
fn vertical_window() {
    let mut e = eng();
    e.set_candidate_vertical(true).unwrap();
    let o = run(&mut e, "r u s t _ Down");
    assert!(o.vertical == 1 && o.selected == Some(1) && o.candidates == ["rust", "Rust", "RUST"]);
}

#[test]
fn pick_chinese_item_equals_digit() {
    // The mouse path chooses the same item as the digit.
    let (mut a, mut b) = (eng(), eng());
    run(&mut a, "g o o g l e _");
    run(&mut b, "g o o g l e _");
    assert!(a.pick(3).unwrap().unwrap() == b.key(Key::ch('4', 0)).unwrap());
}

// ---------- zhen-inline (docs/contracts/zhen-inline.md section 4.1) ----------

/// The real lexicon with the real bigram model (the English word list, the switch and learning on, the display "show on
/// Space"), a fixed clock; for the tests where the decoder's learned boost matters.
fn lm_eng() -> Engine {
    static S: OnceLock<(Arc<Lm>, Arc<CappedLexicon>)> = OnceLock::new();
    let (lm, capped) = S
        .get_or_init(|| {
            let lm = Lm::load(&root().join("data/lm/bigram.sjlm")).unwrap();
            let overlay = std::fs::read_to_string(root().join("data/lexicon/overlay-add.tsv")).unwrap();
            let capped = Arc::new(CappedLexicon::new(real(), &overlay, &lm, None).unwrap());
            (Arc::new(lm), capped)
        })
        .clone();
    let mut e = eng();
    e.set_lm(lm, capped);
    e.set_today(Some(20_000));
    e.set_learning(true);
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
fn records(e: &Engine) -> Vec<(String, String)> {
    e.learner().records().iter().map(|r| (r.context.clone(), r.word.clone())).collect()
}

#[test]
fn token_next_key_is_the_fixed_en_for_learning_and_lookup() {
    // 我是 (the default) re-picked as 我試, then `rust` kept after it: the record's key holds the fixed reading, not the word.
    let dir = tmp_dir("token-key");
    let mut e = lm_eng();
    e.learning_open(&dir).unwrap();
    syllable(&mut e, "ㄨㄛˇ");
    let shown = syllable(&mut e, "ㄕˋ").preedit;
    assert!(shown == "我是", "premise: the unlearned default is {shown}");
    choose_word(&mut e, "我試");
    run(&mut e, "r u s t _ 1");
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
    let o = run(&mut e, "r u s t _ 1");
    assert!(o.preedit == "我試rust", "the lookup key matches the record's: {}", o.preedit);
    // And the word alone (no English after it) is the unlearned default: the record is under the key for an English neighbour.
    e.key(Key::new(KeyKind::Esc)).unwrap();
    e.key(Key::new(KeyKind::Esc)).unwrap();
    syllable(&mut e, "ㄨㄛˇ");
    assert!(syllable(&mut e, "ㄕˋ").preedit == "我是", "no English after it is another key");
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn space_after_the_token_lists_its_three_writings() {
    let mut e = eng();
    run(&mut e, "r u s t _ 1");
    let o = run(&mut e, "_");
    assert!(o.candidates == ["rust", "Rust", "RUST"] && o.selected == Some(0) && o.total == 3, "{:?}", o.candidates);
    let o = run(&mut e, "2");
    assert!(o.preedit == "Rust" && o.commit.is_empty() && o.candidates.is_empty());
    // Again on the new writing: the same three, in the same order.
    assert!(run(&mut e, "_").candidates == ["rust", "Rust", "RUST"]);
    // Down opens it too (open_candidates), a punctuation mark still lists its own table.
    let mut e = eng();
    run(&mut e, "r u s t _ 1");
    assert!(run(&mut e, "Down").candidates == ["rust", "Rust", "RUST"]);
    let mut e = eng();
    run(&mut e, "S-,");
    assert!(run(&mut e, "_").candidates.first().map(String::as_str) == Some("，"), "a mark's table");
}

#[test]
fn commit_paths_send_what_is_shown() {
    // reset(Commit) after `r u s`: the letters, not the Zhuyin.
    let mut e = eng_inline();
    run(&mut e, "r u s");
    assert!(e.reset(ResetMode::Commit).commit == "rus");
    // "Show on Space": the Zhuyin, as in zhen-mixed.
    let mut e = eng();
    run(&mut e, "r u s");
    assert!(e.reset(ResetMode::Commit).commit == "ㄋㄧ");
    // With the window open, Enter is the window's: the first item stays in the composition.
    let mut e = eng_inline();
    let o = run(&mut e, "r u s t _ Enter");
    assert!(o.commit.is_empty() && o.preedit == "rust" && !e.english_open());
}

#[test]
fn english_stays_and_the_composition_goes_on() {
    let mut e = eng();
    let zh = {
        syllable(&mut e, "ㄨㄛˇ");
        syllable(&mut e, "ㄗㄞˋ").preedit
    };
    let o = run(&mut e, "r u s t _ 1");
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

    // Backspace behind `rust` takes the whole token; Left skips it as one cell.
    let mut e = eng();
    syllable(&mut e, "ㄨㄛˇ");
    let zh = syllable(&mut e, "ㄗㄞˋ").preedit;
    run(&mut e, "r u s t _ 1");
    let o = run(&mut e, "Left");
    assert!(o.preedit == format!("{zh}rust") && o.cursor_utf16 as usize == zh.encode_utf16().count(), "one Left jumps over the word");
    let o = run(&mut e, "Right");
    assert!(o.cursor_utf16 as usize == format!("{zh}rust").encode_utf16().count());
    let o = run(&mut e, "BS");
    assert!(o.preedit == zh && o.cursor_utf16 as usize == zh.encode_utf16().count(), "one Backspace deletes the whole word: {}", o.preedit);
}

#[test]
fn english_token_is_a_boundary_for_the_context_and_for_learning() {
    // Like the punctuation test of engine_learn.rs: the word after `rust` has the key ^>$ (the context is not a Han character),
    // so what was taught there is found there, and the same word after 他 alone (key 他) is another key.
    const R2: &str = "ㄑㄩㄢˊ ㄌㄧˋ";
    let other = |shown: &str| if shown == "權力" { "全力" } else { "權力" };
    let mut e = lm_eng();
    syllable(&mut e, "ㄊㄚ");
    run(&mut e, "r u s t _ 1");
    syllable(&mut e, "ㄑㄩㄢˊ");
    let shown = syllable(&mut e, "ㄌㄧˋ").preedit;
    let w = other(shown.trim_start_matches("他rust")).to_string();
    choose_word(&mut e, &w);
    let o = run(&mut e, "Enter");
    assert!(o.commit == format!("他rust{w}"));
    assert!(records(&e) == [("^>$".to_string(), w.clone())], "after the English the key is ^, nothing follows the word: {:?}", records(&e));
    syllable(&mut e, "ㄊㄚ");
    run(&mut e, "r u s t _ 1");
    syllable(&mut e, "ㄑㄩㄢˊ");
    assert!(syllable(&mut e, "ㄌㄧˋ").preedit == format!("他rust{w}"), "decoding after the word finds it");
    e.key(Key::new(KeyKind::Esc)).unwrap();
    syllable(&mut e, "ㄊㄚ");
    syllable(&mut e, "ㄑㄩㄢˊ");
    assert!(syllable(&mut e, "ㄌㄧˋ").preedit != format!("他{w}"), "without the word between, the key is 他, not ^");
}

#[test]
fn the_unit_limit_counts_the_word() {
    // 39 syllables then the English token is the 40th unit: everything is sent (s3d section 1).
    let mut e = eng();
    for _ in 0..39 {
        syllable(&mut e, "ㄅㄚ");
    }
    let o = run(&mut e, "r u s t _ 1");
    assert!(o.commit.chars().count() == 39 + 4 && o.commit.ends_with("rust"), "{}", o.commit);
    assert!(o.preedit.is_empty() && is_empty(&mut e));
}

#[test]
fn inline_shows_the_letters_as_typed() {
    let mut e = eng_inline();
    let o = run(&mut e, "r u s");
    assert!(o.preedit == "rus" && o.cursor_utf16 == 3 && e.english_inline_shown(), "{}", o.preedit);
    assert!(e.english_shadow_len() == 3);
    let o = run(&mut e, "t");
    assert!(o.preedit == "rust" && e.english_inline_shown());
    let o = run(&mut e, "_");
    assert!(e.english_open() && o.preedit == "rust" && o.candidates == ["rust", "Rust", "RUST"], "the window is open over the letters");
    let o = run(&mut e, "1");
    assert!(!e.english_open() && o.preedit == "rust" && o.commit.is_empty(), "the word stays in the composition");
    assert!(!e.english_inline_shown(), "nothing is unfinished");
}

#[test]
fn show_on_space_shows_the_zhuyin() {
    let mut e = eng();
    for (keys, want) in [("r u s", "ㄋㄧ"), ("t", "ㄔㄧ")] {
        let o = run(&mut e, keys);
        assert!(o.preedit == want && !e.english_inline_shown(), "{keys}: {}", o.preedit);
    }
    // The option can change between keys; the next display follows (a key that does nothing still shows the composition).
    e.set_english_inline(true);
    assert!(run(&mut e, "Left").preedit == "rust");
    e.set_english_inline(false);
    assert!(run(&mut e, "Left").preedit == "ㄔㄧ");
}

#[test]
fn enter_sends_the_letters_without_a_space() {
    let mut e = eng_inline();
    run(&mut e, "s u 3");
    let you = shows("ㄋㄧˇ");
    let o = run(&mut e, "r u s");
    assert!(o.preedit == format!("{you}rus"));
    let o = run(&mut e, "Enter");
    assert!(o.handled && o.commit == format!("{you}rus"), "the cell, then the letters, no space: {}", o.commit);
    assert!(o.preedit.is_empty() && e.english_shadow_len() == 0 && is_empty(&mut e), "unfinished syllable and shadow are cleared");
    // Shift+Enter too (the key goes on to the app).
    run(&mut e, "r u s t");
    let o = run(&mut e, "S-Enter");
    assert!(o.commit == "rust" && !o.handled);
    // Not D (two letters): the Zhuyin, as always.
    run(&mut e, "r u");
    assert!(run(&mut e, "Enter").commit == "ㄐㄧ");
}

#[test]
fn escape_backspace_tone_and_space_follow_the_old_rules() {
    // Esc with the window open: the window and the shadow go, the Zhuyin shows, Space is rule 10 (google's leftover is a syllable).
    let mut e = eng_inline();
    let o = run(&mut e, "g o o g l e _");
    assert!(e.english_open() && o.preedit == "google");
    let o = run(&mut e, "Esc");
    assert!(!e.english_open() && o.preedit == "ㄍㄠ" && !e.english_inline_shown());
    let o = run(&mut e, "_");
    assert!(o.preedit == shows("ㄍㄠ"), "rule 10 completed it: {}", o.preedit);
    // Backspace: the shadow is shorter by a letter and D is judged again.
    let mut e = eng_inline();
    assert!(run(&mut e, "r u s t").preedit == "rust");
    let o = run(&mut e, "BS");
    assert!(o.preedit == "rus" && e.english_inline_shown(), "the shadow lost `t`: {}", o.preedit);
    assert!(run(&mut e, "BS").preedit.is_empty(), "the syllable is gone");
    // A tone key finishes the syllable: the shadow goes, the character shows.
    let mut e = eng_inline();
    run(&mut e, "r u s");
    assert!(run(&mut e, "3").preedit == shows("ㄋㄧˇ"));
    assert!(e.english_shadow_len() == 0);
    // Space that is not the trigger: rule 10 (the syllable is not in the lexicon, so nothing changes), D is judged again.
    let mut e = eng_inline();
    run(&mut e, "r u s");
    let o = run(&mut e, "_");
    assert!(!e.english_open() && o.preedit == "rus");
    // Esc without a window clears the unfinished syllable altogether.
    assert!(run(&mut e, "Esc").preedit.is_empty());
}

#[test]
fn rule_d_conditions_one_by_one() {
    let lex = real();
    let has = |syl: &str| !lex.entries(&[syl.to_string()]).is_empty();
    // A prefix of a word, not a word, and a word that is no prefix of another: `rust` + `y` is no prefix of anything.
    let mut e = eng_inline();
    let o = run(&mut e, "r u s t y");
    assert!(o.preedit == "ㄗㄧ" && !e.english_inline_shown(), "not the start of a listed word: {}", o.preedit);
    // Fewer than 3 letters. The real lexicon has every single symbol as a syllable, so two letters with one overwrite never
    // pass the overwrite condition there; a lexicon without ㄍ and ㄍㄠ shows the 3-letter rule alone: `h e` (one overwrite,
    // a leftover that is no syllable) shows the Zhuyin, `h e l` the letters.
    let tiny = Arc::new(Lexicon::parse("ㄅㄚ 爸 -1.0\n").unwrap());
    let mut e = Engine::with_lexicon(tiny, Layout::Standard);
    e.load_english(&tiny_list()).unwrap();
    e.set_english(true).unwrap();
    let o = run(&mut e, "h e");
    assert!(o.preedit == "ㄍ" && !e.english_inline_shown(), "two letters: {}", o.preedit);
    let o = run(&mut e, "l");
    assert!(o.preedit == "hel" && e.english_inline_shown(), "three: {}", o.preedit);
    let mut e = eng_inline();
    let o = run(&mut e, "h e");
    assert!(!e.english_inline_shown() && o.preedit == "ㄍ");
    // The overwrite condition: `h e l` has one overwrite and the leftover ㄍㄠ is a syllable of the lexicon: not yet; the
    // next `l` is the second overwrite.
    assert!(has("ㄍㄠ"), "premise");
    let o = run(&mut e, "l");
    assert!(o.preedit == "ㄍㄠ" && !e.english_inline_shown(), "one overwrite, a valid leftover: {}", o.preedit);
    let o = run(&mut e, "l");
    assert!(o.preedit == "hell" && e.english_inline_shown(), "two overwrites: {}", o.preedit);
    // No overwrite: `c u p` is typed right (ㄏㄧㄣ, no overwrite at all) and never shows letters.
    let mut e = eng_inline();
    let o = run(&mut e, "c u p");
    assert!(o.preedit == "ㄏㄧㄣ" && !e.english_inline_shown(), "{}", o.preedit);
    // The cursor must be at the end.
    let mut e = eng_inline();
    run(&mut e, "s u 3 c l 3 Left");
    let o = run(&mut e, "r u s t");
    assert!(o.preedit.contains("ㄔㄧ") && !o.preedit.contains("rust") && !e.english_inline_shown(), "{}", o.preedit);
}

#[test]
fn rule_d_needs_the_standard_layout_the_switch_and_no_abbreviation() {
    // Eten: the keys differ and no shadow is recorded; the display is the same with the option on and off.
    let mk = |inline: bool| {
        let mut e = Engine::with_lexicon(real(), Layout::Eten);
        e.load_english(&tiny_list()).unwrap();
        e.set_english(true).unwrap();
        e.set_english_inline(inline);
        e
    };
    let (mut a, mut b) = (mk(true), mk(false));
    for tok in "r u s t y g o o g l e".split_whitespace() {
        let k = key_of(tok);
        assert!(a.key(k).unwrap() == b.key(k).unwrap(), "{tok}");
        assert!(!a.english_inline_shown());
    }
    // The switch off: no shadow, so Zhuyin.
    let mut e = Engine::with_lexicon(real(), Layout::Standard);
    e.load_english(&tiny_list()).unwrap();
    assert!(run(&mut e, "r u s t").preedit == "ㄔㄧ" && !e.english_inline_shown());
    // The abbreviation composer on (the hidden "optional finals" setting): never.
    let mut e = eng_inline();
    e.set_abbreviation(true).unwrap();
    assert!(run(&mut e, "r u s t").preedit == "ㄔㄧ" && !e.english_inline_shown());
    // The list not loaded.
    let mut e = Engine::with_lexicon(real(), Layout::Standard);
    e.set_english(true).unwrap();
    assert!(run(&mut e, "r u s t").preedit == "ㄔㄧ" && !e.english_inline_shown());
}

#[test]
fn the_word_list_lookups_do_not_scan() {
    // Section 1.5: a full word and a prefix are binary searches on a sorted list. The behaviour: with a long list the prefix
    // test is right at both ends and in the middle, whatever order the file had its lines in.
    let dir = tmp_dir("sorted");
    let mut words: Vec<String> = (0..2000).map(|i| format!("{}{}", ["zzz", "aaa", "mmm"][i % 3], "abcdefghijklmnopqrstuvwxyz".chars().nth(i % 26).unwrap())).collect();
    words.sort();
    words.dedup();
    words.reverse(); // unsorted on disk
    words.push("rust".into());
    std::fs::write(dir.join("list.txt"), words.join("\n") + "\n").unwrap();
    let mut e = Engine::with_lexicon(real(), Layout::Standard);
    e.load_english(&dir.join("list.txt")).unwrap();
    e.set_english(true).unwrap();
    assert!(run(&mut e, "r u s").preedit == "rus", "rust sits among the others");
    assert!(run(&mut e, "t").preedit == "rust");
    let _ = std::fs::remove_dir_all(&dir);
}
