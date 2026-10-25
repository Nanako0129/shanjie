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
use core::tinylm::RerankLevel;
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
    e.set_english_inline(false).unwrap();
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
        "Tab" => Key::new(KeyKind::Tab),
        "BS" => Key::new(KeyKind::Backspace),
        "Delete" => Key::new(KeyKind::Delete),
        "Left" => Key::new(KeyKind::Left),
        "Right" => Key::new(KeyKind::Right),
        "Home" => Key::new(KeyKind::Home),
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
    e.set_english_inline(false).unwrap();
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
fn letter_in_window_keeps_the_item_and_starts_a_syllable() {
    // Zhen-inline 1.3 (revision 2): a Zhuyin key that is not an item's digit keeps the selected item as an English token first,
    // then is the first key of a new syllable (rule 9 as for any first key). The old rule closed the window and lost the word.
    let mut e = eng();
    run(&mut e, "r u s t _");
    let o = run(&mut e, "w");
    assert!(!e.english_open() && o.candidates.is_empty());
    assert!(e.english_shadow_len() == 1 && o.preedit == "rustㄊ", "rust stays, w starts a syllable: {}", o.preedit);
    let o = run(&mut e, "_");
    assert!(!e.english_open() && o.preedit == format!("rust{}", shows("ㄊ")), "rust, then the syllable ㄊ completed");
}

#[test]
fn punctuation_in_window() {
    // Zhen-inline 1.3h (revision 5): a mark with the window open keeps the selected item first, as a Zhuyin key does (PR #149 lost
    // the word here). Both displays; the full cases are in `punctuation_with_the_window_open_keeps_the_selected_item`.
    for mut e in [eng(), eng_inline()] {
        run(&mut e, "r u s t _");
        let o = run(&mut e, "S-,");
        assert!(o.preedit == "rust，" && o.candidates.is_empty() && !e.english_open() && e.english_shadow_len() == 0, "{}", o.preedit);
    }
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
/// `lm_eng` for the tests that pick from the lexicon window with `choose_word`: that window now lists a prediction row first
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
fn down_after_the_token_lists_its_three_writings_and_space_types_a_space() {
    let mut e = eng();
    run(&mut e, "r u s t _ 1");
    let o = run(&mut e, "Down");
    assert!(o.candidates == ["rust", "Rust", "RUST"] && o.selected == Some(0) && o.total == 3, "{:?}", o.candidates);
    let o = run(&mut e, "2");
    assert!(o.preedit == "Rust" && o.commit.is_empty() && o.candidates.is_empty());
    // Again on the new writing (zhen-inline 1.0, revision 2): the text as it stands first, then lower, Capitalised, UPPER, no repeat.
    assert!(run(&mut e, "Down").candidates == ["Rust", "rust", "RUST"]);
    // Space at the same place opens nothing (zhen-inline 1.3e, revision 3): it types a space unit after the token.
    let mut e = eng();
    run(&mut e, "r u s t _ 1");
    let o = run(&mut e, "_");
    assert!(o.handled && o.candidates.is_empty() && o.selected.is_none() && o.preedit == "rust " && o.commit.is_empty(), "{:?}", o.preedit);
    // A punctuation mark still lists its own table with Space.
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

    // Left skips it as one cell; Backspace behind `rust` takes one letter (zhen-inline section 1.3g).
    let mut e = eng();
    syllable(&mut e, "ㄨㄛˇ");
    let zh = syllable(&mut e, "ㄗㄞˋ").preedit;
    run(&mut e, "r u s t _ 1");
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
fn the_prediction_row_after_the_token_is_recomputed_and_empty() {
    // Section 1.0: the row follows the rule for a chosen candidate (recomputed). With the token at the cursor every start would
    // span it, and a start across a token reads nothing, so what is recomputed is empty - while the row before the token was not.
    let mut e = lm_eng();
    let before = syllable(&mut e, "ㄨㄛˇ");
    assert!(!before.candidates.is_empty(), "premise: a syllable has a prediction row");
    run(&mut e, "r u s t _");
    let o = run(&mut e, "1");
    assert!(o.candidates.is_empty() && o.selected.is_none() && o.total == 0 && o.preedit.ends_with("rust"), "{:?}", o.candidates);
}

#[test]
fn the_unit_limit_counts_the_word() {
    // 127 syllables then the English token is the 128th unit: everything is sent (s3d section 1).
    let mut e = eng();
    for _ in 0..MAX_SYLLABLES - 1 {
        syllable(&mut e, "ㄅㄚ");
    }
    let o = run(&mut e, "r u s t _ 1");
    assert!(o.commit.chars().count() == MAX_SYLLABLES - 1 + 4 && o.commit.ends_with("rust"), "{}", o.commit);
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
    e.set_english_inline(true).unwrap();
    assert!(run(&mut e, "Left").preedit == "rust");
    e.set_english_inline(false).unwrap();
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
fn backspace_under_d_makes_the_letters_left_an_english_run() {
    // zhen-inline section 1.3g: while letters are on screen Backspace takes the last letter and the rest become a run (no replay,
    // no Zhuyin). Fields are the run's (start, len) in UTF-16 units of the preedit.
    let fields = |o: &Output| (o.english_run_utf16_start, o.english_run_utf16_len);
    let mut e = eng_inline();
    let zh = three(&mut e);
    assert!(run(&mut e, "r u s t").preedit == format!("{zh}rust") && e.english_inline_shown());
    let o = run(&mut e, "BS");
    assert!(o.preedit == format!("{zh}rus") && o.commit.is_empty(), "{}", o.preedit);
    assert!(fields(&o) == (3, 3) && !e.english_inline_shown() && e.english_shadow_len() == 0, "an open run, no shadow");
    let o = run(&mut e, "t");
    assert!(o.preedit == format!("{zh}rust") && fields(&o) == (3, 4), "the run takes letters: {}", o.preedit);
    let o = run(&mut e, "_");
    assert!(o.preedit == format!("{zh}rust") && fields(&o) == (0, 0) && !e.english_open(), "Space ends it as a token");
    syllable(&mut e, "ㄅㄧㄢ");
    let o = syllable(&mut e, "ㄧˋ");
    let tail = o.preedit.strip_prefix(format!("{zh}rust").as_str()).unwrap().to_string();
    assert!(tail.chars().count() == 2);
    assert!(run(&mut e, "Enter").commit == format!("{zh}rust{tail}"));

    // `h e l l` is two replacements; the letters left are not replayed (the old replay showed the Zhuyin of `h e l`).
    let mut e = eng_inline();
    assert!(run(&mut e, "h e l l").preedit == "hell");
    let o = run(&mut e, "BS");
    assert!(o.preedit == "hel" && fields(&o) == (0, 3), "{}", o.preedit);
    // Esc takes the whole run.
    assert!(run(&mut e, "Esc").preedit.is_empty());

    // Not under D (Zhuyin on screen) it is rule 11 as ever: `c u p` has no replacement, so it shows ㄏㄧㄣ; Backspace takes ㄣ.
    let mut e = eng_inline();
    assert!(run(&mut e, "c u p").preedit == "ㄏㄧㄣ");
    let o = run(&mut e, "BS");
    assert!(o.preedit == "ㄏㄧ" && fields(&o) == (0, 0));
    // And "show on Space" never has letters on screen: rule 11.
    let mut e = eng();
    run(&mut e, "r u s t");
    let o = run(&mut e, "BS");
    assert!(o.preedit == "ㄔ" && fields(&o) == (0, 0));
}

#[test]
fn backspace_after_an_english_token_deletes_one_letter_at_a_time() {
    let fields = |o: &Output| (o.english_run_utf16_start, o.english_run_utf16_len);
    // A token from the English window: the last letter goes and the rest is a run; three more take the run away.
    let mut e = eng_inline();
    let zh = three(&mut e);
    run(&mut e, "r u s t _ 1");
    let o = run(&mut e, "BS");
    assert!(o.preedit == format!("{zh}rus") && fields(&o) == (3, 3), "{}", o.preedit);
    let o = run(&mut e, "BS BS");
    assert!(o.preedit == format!("{zh}r") && fields(&o) == (3, 1));
    let o = run(&mut e, "BS");
    assert!(o.preedit == zh && fields(&o) == (0, 0), "an emptied run closes: {}", o.preedit);
    assert!(run(&mut e, "Enter").commit == zh);
    // The reopened run is a run: Esc takes the whole word, Space makes it a token again.
    let mut e = eng_inline();
    run(&mut e, "r u s t _ 1 BS");
    assert!(run(&mut e, "Esc").preedit.is_empty() && is_empty(&mut e));
    let mut e = eng_inline();
    run(&mut e, "r u s t _ 1 BS");
    let o = run(&mut e, "_");
    assert!(o.preedit == "rus" && fields(&o) == (0, 0));
    // The case is as it stands: the writing `Rust` chosen with Down gives `Rus`.
    let mut e = eng_inline();
    run(&mut e, "r u s t _ 1 Down 2");
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
    let mut e = eng_inline();
    let zh = three(&mut e);
    run(&mut e, "r u s t _ 1");
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

    // An auto-kept token (the Chinese item goes with it): `google`, then `j` auto-keeps it; Backspace takes the ㄨ, the next one a letter.
    let mut e = eng_inline();
    assert!(run(&mut e, "g o o g l e j").preedit == "googleㄨ");
    assert!(run(&mut e, "BS").preedit == "google");
    let o = run(&mut e, "BS");
    assert!(o.preedit == "googl" && fields(&o) == (0, 5));
    run(&mut e, "_");
    let o = run(&mut e, "Down");
    assert!(o.candidates == ["googl", "Googl", "GOOGL"], "writings only, no Chinese item: {:?}", o.candidates);

    // A run's token (Shift+letter) too.
    let mut e = win();
    run(&mut e, "S-w i n d o w s _");
    let o = run(&mut e, "BS");
    assert!(o.preedit == "Window" && fields(&o) == (0, 6));

    // "Show on Space" (D never holds): the token rule applies all the same; the pending syllable keeps rule 11.
    let mut e = eng();
    run(&mut e, "r u s t _ 1");
    let o = run(&mut e, "BS");
    assert!(o.preedit == "rus" && fields(&o) == (0, 3));
}

#[test]
fn backspace_still_takes_space_units_punctuation_and_tokens_without_mixing() {
    // A space unit and a punctuation token go in one Backspace, without opening a run.
    let fields = |o: &Output| (o.english_run_utf16_start, o.english_run_utf16_len);
    let mut e = eng_inline();
    run(&mut e, "r u s t _ 1 _");
    let o = run(&mut e, "BS");
    assert!(o.preedit == "rust" && fields(&o) == (0, 0));
    let mut e = eng_inline();
    run(&mut e, "r u s t _ 1 S-,");
    let o = run(&mut e, "BS");
    assert!(o.preedit == "rust" && fields(&o) == (0, 0));
    // Mixed typing off: a token that is still there goes whole.
    let mut e = eng_inline();
    run(&mut e, "r u s t _ 1");
    e.set_english(false).unwrap();
    let o = run(&mut e, "BS");
    assert!(o.preedit.is_empty() && fields(&o) == (0, 0), "{}", o.preedit);
}

#[test]
fn rule_d_conditions_one_by_one() {
    let lex = real();
    let has = |syl: &str| !lex.entries(&[syl.to_string()]).is_empty();
    // A prefix of a word, not a word, and a word that is no prefix of another: `rust` + `y` is no prefix of anything.
    let mut e = eng_inline();
    let o = run(&mut e, "r u s h");
    assert!(o.preedit == "ㄘㄧ" && !e.english_inline_shown(), "not the start of a listed word: {}", o.preedit);
    // `rust` is a whole word and `y` goes nowhere from it: the auto-keep takes over (zhen-inline 1.3), not the Zhuyin.
    let mut e = eng_inline();
    let o = run(&mut e, "r u s t y");
    assert!(o.preedit == "rustㄗ" && !e.english_inline_shown(), "{}", o.preedit);
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
        e.set_english_inline(inline).unwrap();
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
fn the_word_list_is_sorted_on_load() {
    // Section 1.5: the lookups are binary searches, which need a sorted list. What this proves is only that the list is sorted
    // on load, whatever order the file had its lines in (the file here is descending); it does not detect a linear scan.
    let dir = tmp_dir("sorted");
    let mut words: Vec<String> = (0..2000).map(|i| format!("{}{}", ["zzz", "aaa", "mmm"][i % 3], "abcdefghijklmnopqrstuvwxyz".chars().nth(i % 26).unwrap())).collect();
    words.push("rust".into());
    words.sort();
    words.dedup();
    words.reverse(); // descending on disk: a lookup that trusts the file's order finds nothing
    std::fs::write(dir.join("list.txt"), words.join("\n") + "\n").unwrap();
    let mut e = Engine::with_lexicon(real(), Layout::Standard);
    e.load_english(&dir.join("list.txt")).unwrap();
    e.set_english(true).unwrap();
    assert!(run(&mut e, "r u s").preedit == "rus", "rust sits among the others");
    assert!(run(&mut e, "t").preedit == "rust");
    let _ = std::fs::remove_dir_all(&dir);
}

// ---------- zhen-inline revision 2 (docs/contracts/zhen-inline.md sections 1.3, 1.3b, 1.3c and 4.1) ----------

/// The tiny list plus `win` and `windows` (the contract's examples need a word that is a prefix of a longer one). A file of its
/// own in the temp directory, so eval/golden/english-tiny.txt, which the Swift tests and the simulator's recall test pin by hash,
/// stays as it is.
fn win_list() -> PathBuf {
    static P: OnceLock<PathBuf> = OnceLock::new();
    P.get_or_init(|| {
        let dir = tmp_dir("win-list");
        let text = std::fs::read_to_string(tiny_list()).unwrap() + "win\nwindows\n";
        std::fs::write(dir.join("list.txt"), text).unwrap();
        dir.join("list.txt")
    })
    .clone()
}
/// The default display ("show as typed") with the extended list.
fn win() -> Engine {
    let mut e = Engine::with_lexicon(real(), Layout::Standard);
    e.load_english(&win_list()).unwrap();
    e.set_english(true).unwrap();
    e
}
/// "Show on Space" with the extended list.
fn win_space() -> Engine {
    let mut e = win();
    e.set_english_inline(false).unwrap();
    e
}
/// Types the three syllables of 我在用 (whatever the decoder shows for them) and returns what is shown.
fn three(e: &mut Engine) -> String {
    syllable(e, "ㄨㄛˇ");
    syllable(e, "ㄗㄞˋ");
    let zh = syllable(e, "ㄩㄥˋ").preedit;
    assert!(zh.chars().count() == 3, "premise: three syllables show three characters: {zh}");
    zh
}
/// The first lexicon entry of a syllable: the Chinese item of a window or token list.
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

#[test]
fn shift_letter_starts_a_run_on_an_empty_composition() {
    for mut e in [win(), win_space()] {
        let o = run(&mut e, "S-w");
        assert!(o.handled && o.commit.is_empty() && o.preedit == "W" && o.cursor_utf16 == 1, "{}", o.preedit);
        let o = run(&mut e, "i n d o w s");
        assert!(o.commit.is_empty() && o.preedit == "Windows" && o.cursor_utf16 == 7 && !e.english_open(), "{}", o.preedit);
        assert!(o.candidates.is_empty() && e.english_shadow_len() == 0, "no window, no shadow");
    }
}

#[test]
fn run_after_chinese_ends_with_space_and_the_composition_goes_on() {
    let mut e = win();
    let zh = three(&mut e);
    let o = run(&mut e, "S-w i n d o w s");
    assert!(o.preedit == format!("{zh}Windows") && o.commit.is_empty());
    let o = run(&mut e, "_");
    assert!(o.handled && o.commit.is_empty() && o.preedit == format!("{zh}Windows") && !e.english_open(), "the space is eaten: {}", o.preedit);
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
    for mk in [win as fn() -> Engine, win_space] {
        let mut e = mk();
        let zh = three(&mut e);
        run(&mut e, "S-w i n d o w s");
        let o = run(&mut e, "c");
        assert!(o.preedit == format!("{zh}Windowsc") && o.commit.is_empty() && !e.english_autokept(), "{}", o.preedit);
        // One Backspace takes one letter, not the whole word.
        let o = run(&mut e, "BS");
        assert!(o.preedit == format!("{zh}Windows"), "{}", o.preedit);
        // Space ends the run (eaten); 很好 and Enter send everything, no space between.
        let o = run(&mut e, "_");
        assert!(o.preedit == format!("{zh}Windows") && o.commit.is_empty() && !e.english_open());
        syllable(&mut e, "ㄏㄣˇ");
        let shown = syllable(&mut e, "ㄏㄠˇ").preedit;
        let tail = shown.strip_prefix(&format!("{zh}Windows")).expect("Windows stays in front").to_string();
        assert!(tail.chars().count() == 2);
        let o = run(&mut e, "Enter");
        assert!(o.commit == format!("{zh}Windows{tail}"), "{}", o.commit);
        assert!(!e.english_autokept());
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
    assert!(!e.english_open() && o.candidates == ["Windows", "windows", "WINDOWS"], "premise: the writings list is open: {:?}", o.candidates);
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
    run(&mut e, "r u s t _ 1 _");
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
    // Every way the run ends leaves 0 and 0: Space, Enter (no preedit), a mark, a tone key.
    for end in ["_", "S-,", "3", "Down"] {
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
    assert!(o.preedit == "WindowsX" && !e.english_autokept(), "{}", o.preedit);
    // While the run is a prefix of a longer word (`win` of `windows`) a plain letter goes on.
    let mut e = win();
    let o = run(&mut e, "S-w i n d");
    assert!(o.preedit == "Wind" && !e.english_autokept(), "{}", o.preedit);
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
    assert!(o.preedit == "iPhone" && o.candidates.is_empty() && !e.english_open());
    // Letters on screen (D): `rust` + Shift+W is the run `rustW`.
    let mut e = win();
    let o = run(&mut e, "r u s t S-w");
    assert!(o.preedit == "rustW" && e.english_shadow_len() == 0 && !e.english_inline_shown(), "{}", o.preedit);
    // "Show on Space": the Zhuyin is on screen, the letters typed still are the run's start.
    let mut e = win_space();
    assert!(run(&mut e, "r u s t").preedit == "ㄔㄧ");
    assert!(run(&mut e, "S-w").preedit == "rustW");
    // The window open: it closes, the shadow's letters start the run.
    let mut e = win();
    run(&mut e, "r u s t _");
    assert!(e.english_open());
    let o = run(&mut e, "S-w");
    assert!(!e.english_open() && o.candidates.is_empty() && o.preedit == "rustW", "{}", o.preedit);
}

#[test]
fn run_drops_an_unfinished_syllable_that_is_no_letters() {
    // `1` is ㄅ, a key that is no letter: Shift+W throws the ㄅ away and starts a run with `W`.
    let mut e = win();
    assert!(run(&mut e, "1").preedit == "ㄅ");
    let o = run(&mut e, "S-w");
    assert!(o.preedit == "W" && e.english_shadow_len() == 0, "{}", o.preedit);
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
    // A tone key: the run ends, the key does nothing more (no syllable to finish).
    let mut e = win();
    assert!(run(&mut e, "S-w i n 3").preedit == "Win");
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
    // The same with the switch on but no list (shadow_on is false), and with Eten.
    let mut e = Engine::with_lexicon(real(), Layout::Standard);
    e.set_english(true).unwrap();
    assert!(!run(&mut e, "S-w").handled);
    let mut e = Engine::with_lexicon(real(), Layout::Eten);
    e.load_english(&win_list()).unwrap();
    e.set_english(true).unwrap();
    assert!(!run(&mut e, "S-w").handled);
}

#[test]
fn autokeep_keeps_the_word_and_starts_the_next_syllable() {
    // Two Chinese syllables, `rust`, then ㄨ (j) which cannot go on from `rust`: the same state as choosing item 1 and typing j.
    let build = |keys: &str| {
        let mut e = eng_inline();
        syllable(&mut e, "ㄨㄛˇ");
        let zh = syllable(&mut e, "ㄗㄞˋ").preedit;
        (e, zh, keys.to_string())
    };
    let (mut a, zh, ka) = build("r u s t j");
    let (mut b, _, kb) = build("r u s t _ 1 j");
    let (oa, ob) = (run(&mut a, &ka), run(&mut b, &kb));
    assert!(oa == ob, "{} / {}", oa.preedit, ob.preedit);
    assert!(oa.preedit == format!("{zh}rustㄨ") && oa.commit.is_empty() && a.english_autokept() && !b.english_autokept());
    // Finish a Chinese syllable (ㄨㄛˇ), one Enter sends it all.
    let (ca, cb) = (run(&mut a, "i 3 Enter").commit, run(&mut b, "i 3 Enter").commit);
    assert!(ca == cb && ca.starts_with(&format!("{zh}rust")) && ca.chars().count() == 2 + 4 + 1, "{ca}");
    // The kept word is a token: Backspace takes the ㄨ first, then one letter of it (zhen-inline section 1.3g).
    let (mut a, zh, _) = build("");
    run(&mut a, "r u s t j");
    assert!(run(&mut a, "BS").preedit == format!("{zh}rust"));
    assert!(run(&mut a, "BS").preedit == format!("{zh}rus"));
}

#[test]
fn autokeep_needs_a_whole_word_and_a_key_that_cannot_go_on() {
    // `windo` is the start of `windows` but no word: `j` does not keep it (the old rule); the Zhuyin shows again.
    let mut e = win();
    let o = run(&mut e, "w i n d o");
    assert!(o.preedit == "windo" && e.english_inline_shown());
    let o = run(&mut e, "j");
    assert!(!e.english_autokept() && !o.preedit.contains("windo") && !e.english_inline_shown(), "{}", o.preedit);
    // `win` is a whole word and D holds at it (w->ㄊ, i->ㄛ, n->ㄙ is one overwrite and the leftover ㄙㄛ is not in the lexicon), but
    // `wind` is still the start of `windows`: the `d` goes on, no auto-keep, the letters stay.
    let mut e = win();
    run(&mut e, "w i n");
    assert!(e.english_inline_shown() && !e.english_autokept());
    let o = run(&mut e, "d");
    assert!(o.preedit == "wind" && e.english_inline_shown() && !e.english_autokept(), "{}", o.preedit);
    // `windows` is a whole word and `c` (ㄏ) starts no word after it: kept.
    let mut e = win();
    let o = run(&mut e, "w i n d o w s");
    assert!(o.preedit == "windows" && !e.english_autokept());
    let o = run(&mut e, "c");
    assert!(e.english_autokept() && o.preedit == "windowsㄏ", "{}", o.preedit);
}

#[test]
fn autokeep_is_not_for_tones_space_or_show_on_space() {
    // A tone key and Space are not Zhuyin keys: nothing kept (the window opens at Space).
    let mut e = eng_inline();
    run(&mut e, "r u s t 3");
    assert!(!e.english_autokept());
    assert!(run(&mut e, "BS").preedit == "rus", "the letters were still unfinished");
    let mut e = eng_inline();
    run(&mut e, "r u s t _");
    assert!(e.english_open() && !e.english_autokept());
    // "Show on Space": D never holds, so there is no auto-keep: j replaces the ㄧ.
    let mut e = eng();
    let o = run(&mut e, "r u s t j");
    assert!(o.preedit == "ㄔㄨ" && !e.english_autokept(), "{}", o.preedit);
}

#[test]
fn window_open_and_a_zhuyin_key_keeps_the_selected_item() {
    // `rust`, Space, then j: the state of choosing item 1 and typing j, the window closed, rust a token.
    let mut a = eng_inline();
    let oa = run(&mut a, "r u s t _ j");
    let mut b = eng_inline();
    let ob = run(&mut b, "r u s t _ 1 j");
    assert!(oa == ob && oa.preedit == "rustㄨ" && oa.commit.is_empty() && !a.english_open(), "{}", oa.preedit);
    assert!(a.english_autokept());
    assert!(run(&mut a, "BS").preedit == "rust");
    assert!(run(&mut a, "BS").preedit == "rus", "one Backspace takes one letter of the token");
    // The selected item, not the first.
    let mut e = eng_inline();
    assert!(run(&mut e, "r u s t _ Right j").preedit == "Rustㄨ");
    // A digit is an item's key, 0 is not (ㄢ).
    let mut e = eng_inline();
    assert!(run(&mut e, "r u s t _ 0").preedit == "rustㄢ");
    let mut e = eng_inline();
    assert!(run(&mut e, "r u s t _ 3").preedit == "RUST");
    // Item 4 of google's window is the Chinese one (a digit selects, it keeps nothing).
    let mut e = eng();
    run(&mut e, "g o o g l e _");
    let o = run(&mut e, "4");
    assert!(o.preedit == first_entry("ㄍㄠ"), "item 4 of google's window is the Chinese one: {}", o.preedit);
}

#[test]
fn autokeep_at_the_unit_limit_sends_in_this_keys_output() {
    for keys in ["r u s t j", "r u s t _ j"] {
        let mut e = eng_inline();
        for _ in 0..MAX_SYLLABLES - 1 {
            syllable(&mut e, "ㄅㄚ");
        }
        let o = run(&mut e, keys);
        assert!(o.handled && o.commit.chars().count() == MAX_SYLLABLES - 1 + 4 && o.commit.ends_with("rust"), "{keys}: {}", o.commit);
        assert!(o.preedit == "ㄨ", "{keys}: the key began a syllable in the emptied composition: {}", o.preedit);
    }
}

#[test]
fn token_lists_follow_how_the_token_was_made() {
    // Kept by a Zhuyin key after `google`: the leftover ㄍㄠ is a syllable, so the list has a Chinese item last.
    let zh = first_entry("ㄍㄠ");
    for keys in ["g o o g l e j", "g o o g l e _ j"] {
        let mut e = eng_inline();
        assert!(run(&mut e, keys).preedit == "googleㄨ");
        // Backspace reopens the word as a run (section 1.3i); Space ends it unchanged, and the Chinese item comes back with it.
        run(&mut e, "BS _");
        let o = run(&mut e, "Down");
        assert!(o.candidates == ["google", "Google", "GOOGLE", zh.as_str()], "{keys}: {:?}", o.candidates);
        // Choosing the Chinese item turns the token into the character.
        let o = run(&mut e, "4");
        assert!(o.preedit == zh && o.commit.is_empty(), "{keys}: {}", o.preedit);
        // And the list of the character is the character's (an ordinary candidate window), not a token's.
        let o = run(&mut e, "_");
        assert!(!o.candidates.iter().any(|c| c == "google"), "{keys}");
    }
    // `rust`: its leftover ㄔㄧ is no syllable of the lexicon, so there is no Chinese item.
    let mut e = eng_inline();
    run(&mut e, "r u s t j BS");
    assert!(run(&mut e, "Down").candidates == ["rust", "Rust", "RUST"]);
    // Choosing another writing keeps the keys: the Chinese item is still there, the list starts from the text as it is.
    let mut e = eng_inline();
    run(&mut e, "g o o g l e j BS Down");
    let o = run(&mut e, "2");
    assert!(o.preedit == "Google");
    let o = run(&mut e, "Down");
    assert!(o.candidates == ["Google", "google", "GOOGLE", zh.as_str()], "{:?}", o.candidates);
    // A token chosen from the English window with Enter has no keys: three writings only.
    let mut e = eng_inline();
    run(&mut e, "g o o g l e _ Enter");
    assert!(run(&mut e, "Down").candidates == ["google", "Google", "GOOGLE"]);
    // The Space key lists nothing there (zhen-inline 1.3e): it types a space unit after the token.
    let mut e = eng_inline();
    // Backspace reopened the word as a run (section 1.3i): the first Space ends it, the second types the space.
    run(&mut e, "g o o g l e j BS _");
    let o = run(&mut e, "_");
    assert!(o.candidates.is_empty() && o.preedit == "google ", "{:?}", o.preedit);
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

// ---------- amendment two, review fixes ----------

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
fn the_chinese_item_of_a_kept_token_is_the_syllable_that_was_on_the_columns() {
    // "Show on Space": `r u s`, Backspace (rule 11 takes the last symbol in display order, not the shadow's last key), `s t`: the
    // columns are ㄔ while the shadow is `rust`. The window's fourth item is the first entry of ㄔ, and so must the kept token's
    // Chinese item be (replaying `rust` would give ㄔㄧ, which has none).
    let zh = first_entry("ㄔ");
    assert!(real().entries(&["ㄔㄧ".to_string()]).is_empty(), "premise: replaying the shadow gives ㄔㄧ, which has no entry");
    let mut e = eng();
    let o = run(&mut e, "r u s BS s t _");
    assert!(e.english_open() && o.candidates.len() == 4 && o.candidates[3] == zh, "premise: the window's fourth item: {:?}", o.candidates);
    run(&mut e, "j");
    assert!(e.english_autokept());
    run(&mut e, "BS"); // "show on Space": section 1.3i does not apply, the token stays
    let o = run(&mut e, "Down");
    assert!(o.candidates == ["rust", "Rust", "RUST", zh.as_str()], "{:?}", o.candidates);
    assert!(run(&mut e, "4").preedit == zh);
}

#[test]
fn a_zhuyin_key_over_the_chinese_item_is_not_an_autokeep() {
    // `google`'s window, fourth item (Chinese) selected, then ㄨ: the Chinese item is kept, no English token was made.
    let mut e = eng_inline();
    run(&mut e, "g o o g l e _ Right Right Right");
    let o = run(&mut e, "j");
    assert!(o.preedit == format!("{}ㄨ", first_entry("ㄍㄠ")), "{}", o.preedit);
    assert!(!e.english_autokept());
    // The English item selected: an auto-keep.
    let mut e = eng_inline();
    run(&mut e, "g o o g l e _ j");
    assert!(e.english_autokept());
}

#[test]
fn autokept_describes_only_the_latest_key() {
    // Only `key()` sets and clears the flag (finding 7 of the revision-five review: set_rerank_level had been missed, so a clear
    // spread over the entry points could leave it stale). A pick, a reset or a setting neither sets nor clears it; the next key does.
    let inline_lm = || {
        let mut e = lm_eng();
        e.set_english_inline(true).unwrap();
        e
    };
    let mut e = inline_lm();
    let o = run(&mut e, "r u s t j");
    assert!(e.english_autokept() && !o.candidates.is_empty(), "premise: kept, and the row shows: {:?}", o.candidates);
    assert!(e.pick(0).unwrap().is_some());
    assert!(e.english_autokept(), "a pick is not a key: the latest key's flag stays");
    run(&mut e, "Esc");
    assert!(!e.english_autokept(), "the next key clears it");
    for call in 0..5 {
        let mut e = inline_lm();
        run(&mut e, "r u s t j");
        assert!(e.english_autokept());
        match call {
            0 => drop(e.reset(ResetMode::Discard)),
            1 => drop(e.set_candidate_vertical(true).unwrap()),
            2 => drop(e.set_english_inline(false).unwrap()),
            3 => drop(e.set_rerank_level(RerankLevel::Standard).unwrap()),
            _ => drop(e.set_prediction(false).unwrap()),
        }
        assert!(e.english_autokept(), "call {call} leaves the latest key's flag");
        run(&mut e, "Esc");
        assert!(!e.english_autokept(), "call {call}: the next key clears it");
    }
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

// ---------- revision five (docs/contracts/zhen-inline.md section 1.3h) ----------

#[test]
fn punctuation_keeps_the_letters_that_are_shown() {
    let fields = |o: &Output| (o.english_run_utf16_start, o.english_run_utf16_len);
    // `rust` on screen (rule D), then a mark: after 我在用 the composition is 我在用rust，, and `rust` is a token, not a run.
    let mut e = eng_inline();
    let zh = three(&mut e);
    assert!(run(&mut e, "r u s t").preedit == format!("{zh}rust") && e.english_inline_shown());
    let o = run(&mut e, "S-,");
    assert!(o.preedit == format!("{zh}rust，") && fields(&o) == (0, 0) && e.english_shadow_len() == 0 && o.commit.is_empty(), "{}", o.preedit);
    assert!(e.english_autokept(), "kept as an auto-keep is");
    // A token: Backspace takes the mark (one unit), the next one a letter of the token and opens a run (section 1.3g).
    let mut t = eng_inline();
    three(&mut t);
    run(&mut t, "r u s t S-, BS");
    let o = run(&mut t, "BS");
    assert!(o.preedit == format!("{zh}rus") && fields(&o) == (3, 3), "rust was a token: {}", o.preedit);
    assert!(run(&mut e, "Enter").commit == format!("{zh}rust，"));
    // Not a whole word: `windo` stays as typed.
    let mut e = win();
    run(&mut e, "w i n d o");
    assert!(e.english_inline_shown(), "premise: the letters show");
    let o = run(&mut e, "S-,");
    assert!(o.preedit == "windo，" && !e.english_inline_shown(), "{}", o.preedit);
    assert!(run(&mut e, "Enter").commit == "windo，");
    // A mark on a plain key (`[`) goes the same way as one typed with Shift.
    let mut e = eng_inline();
    run(&mut e, "r u s t");
    assert!(run(&mut e, "[").preedit == "rust「");
    // Letters not on screen (D false: two letters) are a syllable, as before: the mark drops it.
    let mut e = eng_inline();
    run(&mut e, "r u");
    assert!(!e.english_inline_shown());
    assert!(run(&mut e, "S-,").preedit == "，");
    // "Show on Space": the Zhuyin is on screen, not letters, and the mark drops the unfinished syllable as it always did.
    let mut e = eng();
    run(&mut e, "r u s t");
    assert!(run(&mut e, "S-,").preedit == "，");
}

#[test]
fn punctuation_with_the_window_open_keeps_the_selected_item() {
    for inline in [true, false] {
        let mut e = if inline { eng_inline() } else { eng() };
        let zh = three(&mut e);
        run(&mut e, "r u s t _");
        assert!(e.english_open());
        let o = run(&mut e, "S-,");
        assert!(o.preedit == format!("{zh}rust，") && !e.english_open() && o.candidates.is_empty(), "inline={inline}: {}", o.preedit);
        assert!(run(&mut e, "Enter").commit == format!("{zh}rust，"));
        // The selected item, not the first: Right selects `Rust`.
        let mut e = if inline { eng_inline() } else { eng() };
        run(&mut e, "r u s t _ Right");
        assert!(run(&mut e, "S-,").preedit == "Rust，", "inline={inline}");
        // A Chinese item stays a Chinese item (the window of `google` has ㄍㄠ's first entry as its fourth).
        let mut e = if inline { eng_inline() } else { eng() };
        run(&mut e, "g o o g l e _ Right Right Right");
        assert!(run(&mut e, "S-,").preedit == format!("{}，", first_entry("ㄍㄠ")), "inline={inline}");
    }
}

#[test]
fn punctuation_after_letters_at_the_unit_limit_sends_in_this_keys_output() {
    for keys in ["r u s t", "r u s t _"] {
        let mut e = eng_inline();
        let marks = nearly_full(&mut e);
        run(&mut e, keys);
        let o = run(&mut e, "S-,");
        assert!(o.handled && o.commit == format!("{marks}rust") && o.preedit == "，", "{keys}: {} / {}", o.commit, o.preedit);
    }
}

#[test]
fn backspace_with_the_window_open_closes_it_and_the_letters_come_back() {
    let fields = |o: &Output| (o.english_run_utf16_start, o.english_run_utf16_len);
    let mut e = eng_inline();
    let zh = three(&mut e);
    run(&mut e, "r u s t _");
    assert!(e.english_open());
    let o = run(&mut e, "BS");
    assert!(o.preedit == format!("{zh}rust") && fields(&o) == (0, 0) && !e.english_open() && e.english_inline_shown(), "{}", o.preedit);
    assert!(o.candidates.is_empty() && e.english_shadow_len() == 4);
    let o = run(&mut e, "BS");
    assert!(o.preedit == format!("{zh}rus") && fields(&o) == (3, 3), "the next one is section 1.3g's: {}", o.preedit);
    // "Show on Space": PR #149 stays (the window closes and the shadow goes; the Zhuyin is on screen).
    let mut e = eng();
    run(&mut e, "r u s t _");
    let o = run(&mut e, "BS");
    assert!(o.preedit == "ㄔㄧ" && fields(&o) == (0, 0) && !e.english_open() && e.english_shadow_len() == 0, "{}", o.preedit);
}

#[test]
fn a_run_in_the_middle_decodes_its_two_sides_apart() {
    let fields = |o: &Output| (o.english_run_utf16_start, o.english_run_utf16_len);
    // 編譯 is one word; with something between 編 and 譯 the sides decode alone (邊, 意), as with a mark between them.
    for lm in [false, true] {
        let mk = || if lm { lm_eng() } else { eng_inline() };
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
        run(&mut e, "r u s t _ 1");
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
        let mk = || if lm { lm_eng() } else { eng_inline() };
        for cancel in ["Esc", "BS", "token"] {
            let mut e = mk();
            syllable(&mut e, "ㄅㄧㄢ");
            let o = if cancel == "token" {
                run(&mut e, "r u s t _ 1");
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
fn backspace_after_an_auto_keep_opens_the_word_again() {
    // zhen-inline section 1.3i (maintainer 2026-10-15 on the device: `rusr` kept `rus` and Backspace could not get the English
    // back): the key that auto-kept the word is the only thing in the new syllable, so Backspace takes it and the kept word
    // opens again as a run with all its letters.
    let fields = |o: &Output| (o.english_run_utf16_start, o.english_run_utf16_len);
    let mut e = eng_inline();
    let o = run(&mut e, "r u s t r");
    assert!(o.preedit == "rustㄐ" && fields(&o) == (0, 0), "auto-kept, a new syllable: {}", o.preedit);
    let o = run(&mut e, "BS");
    assert!(o.preedit == "rust" && fields(&o) == (0, 4) && o.commit.is_empty(), "the run again: {}", o.preedit);
    assert!(run(&mut e, "BS").preedit == "rus", "and the next Backspace takes a letter of it");
    let o = run(&mut e, "t _");
    assert!(o.preedit == "rust" && fields(&o) == (0, 0), "Space ends it as a token");
    assert!(run(&mut e, "Enter").commit == "rust");

    // A syllable of two keys keeps the token until both are gone.
    let mut e = eng_inline();
    run(&mut e, "r u s t r u");
    assert!(run(&mut e, "BS").preedit == "rustㄐ");
    assert!(fields(&run(&mut e, "BS")) == (0, 4));

    // Only an auto-keep's token reopens: a word chosen from the window stays a token when Backspace takes a Zhuyin key typed
    // after it, so the next key is Zhuyin again (`j` is ㄨ, not a letter of the word).
    let mut e = eng_inline();
    run(&mut e, "r u s t _ 1 5");
    let o = run(&mut e, "BS");
    assert!(o.preedit == "rust" && fields(&o) == (0, 0), "the chosen token stays: {}", o.preedit);
    assert!(run(&mut e, "j").preedit == "rustㄨ");
    // And "show on Space" never opens a run here (section 1.4: the display of PR #149).
    let mut e = eng();
    run(&mut e, "r u s t _ j");
    assert!(e.english_autokept(), "premise: the window's item kept by a Zhuyin key");
    let o = run(&mut e, "BS");
    assert!(o.preedit == "rust" && fields(&o) == (0, 0), "{}", o.preedit);

    // The kept word's Chinese item survives the reopening only when the run ends with the same letters.
    let zh = first_entry("ㄍㄠ");
    let mut e = eng_inline();
    run(&mut e, "g o o g l e j BS _");
    assert!(run(&mut e, "Down").candidates.last() == Some(&zh), "unchanged: the Chinese item is back");
    let mut e = eng_inline();
    run(&mut e, "g o o g l e j BS BS e _");
    assert!(!run(&mut e, "Down").candidates.contains(&zh), "letters changed: a run's token, writings only");
}

// ---- zhen-keyorder (docs/contracts/zhen-keyorder.md section 5.1): the key-order switch ----
//
// Keys of the words below, standard layout, column 0 initial / 1 medial / 2 final:
//   l s   ㄠ(2) ㄋ(0): out of order, E 1.           o k   ㄟ(2) ㄜ(2): a replacement, E 1.
//   c d   ㄏ(0) ㄎ(0): a replacement, E 1.          c u p ㄏ ㄧ ㄣ: in order, E 0, the syllable ㄏㄧㄣ does not exist.
//   u i   ㄧ(1) ㄛ(2): in order, E 0 (a `TWO_LETTER` word without evidence).   z z   ㄈ ㄈ: a replacement, not in `TWO_LETTER`.

/// `eng_inline()` with the key-order switch on.
fn ko() -> Engine {
    let mut e = eng_inline();
    e.set_english_key_order(true).unwrap();
    e
}

#[test]
fn key_order_opens_the_two_letter_words_on_space() {
    let mut e = ko();
    let o = run(&mut e, "l s _");
    assert!(e.english_open() && o.candidates[..3] == ["ls", "Ls", "LS"], "ls: {:?}", o.candidates);
    assert!(o.preedit == "ㄋㄠ", "the unfinished syllable stays: {}", o.preedit);
    let mut e = ko();
    let o = run(&mut e, "o k _");
    assert!(e.english_open() && o.candidates[..3] == ["ok", "Ok", "OK"], "ok: {:?}", o.candidates);
    // ㄜ is a syllable of the lexicon, so the window has its first entry as the fourth item.
    assert!(o.candidates.len() == 4 && o.candidates[3] == first_entry("ㄜ"), "ok: the Chinese item");
    // 1 keeps the word in the composition, as every English item.
    assert!(run(&mut e, "1").preedit == "ok" && !e.english_open());
}

#[test]
fn key_order_opens_a_listed_word_without_evidence_when_the_syllable_does_not_exist() {
    let mut e = ko();
    let o = run(&mut e, "c u p _");
    assert!(e.english_open() && o.candidates == ["cup", "Cup", "CUP"] && e.english_evidence() == 0, "{:?}", o.candidates);
    assert!(o.preedit == "ㄏㄧㄣ");
    // Space only: the letters are not shown while typing (rule D is not relaxed), and no auto-keep.
    let mut e = ko();
    for k in ["c", "u", "p"] {
        let o = run(&mut e, k);
        assert!(!e.english_inline_shown() && !e.english_autokept(), "after {k}");
        assert!(o.preedit.chars().all(|c| !c.is_ascii_alphabetic()), "Zhuyin after {k}: {}", o.preedit);
    }
    // And a tone key closes the syllable as it always did.
    let mut e = ko();
    let o = run(&mut e, "c u p 3");
    assert!(!e.english_open() && o.candidates.is_empty() && o.preedit == shows("ㄏㄧㄣˇ"));
}

#[test]
fn key_order_off_is_today() {
    // The same keys with the switch off (the default), after it was on, and on an engine that never heard of it: identical outputs.
    for script in ["l s _", "o k _", "c u p _", "l s s _", "r u s t _", "d i p _", "l a w _", "u i _", "z z _"] {
        let (mut never, mut off, mut again) = (eng_inline(), eng_inline(), ko());
        again.set_english_key_order(false).unwrap();
        off.set_english_key_order(false).unwrap();
        let (a, b, c) = (run_all(&mut never, script), run_all(&mut off, script), run_all(&mut again, script));
        assert!(a == b && a == c, "{script}");
        assert!(never.english_open() == again.english_open() && never.english_evidence() == again.english_evidence(), "{script}");
    }
    // The documented case: today `l s` Space is rule 10 (the syllable ㄋㄠ is taken) and `c u p` Space does nothing.
    let mut e = eng_inline();
    let o = run(&mut e, "l s _");
    assert!(!e.english_open() && o.preedit == shows("ㄋㄠ"));
    let mut e = eng_inline();
    assert_pend_only(&run(&mut e, "c u p _"), "ㄏㄧㄣ");
}

#[test]
fn key_order_evidence_counts() {
    let ev = |on: bool, keys: &str| {
        let mut e = if on { ko() } else { eng_inline() };
        run(&mut e, keys);
        e.english_evidence()
    };
    assert!(ev(true, "l s") == 1, "out of order");
    assert!(ev(true, "c d") == 1, "a replacement");
    assert!(ev(true, "o k") == 1);
    assert!(ev(true, "c u p") == 0 && ev(true, "u i") == 0, "in order");
    // A key that replaces and is out of order at once counts once: ㄠ(2), ㄋ(0) out of order, ㄋ again replaces (and is below ㄠ).
    assert!(ev(true, "l s s") == 2, "the replacing key counts as a replacement only");
    // Off: the replacements only, today's number.
    assert!(ev(false, "l s") == 0 && ev(false, "c d") == 1 && ev(false, "l s s") == 1);
    // Backspace drops the last key's column and leaves the count: ㄍ(0) ㄠ(2), Backspace takes ㄠ, ㄨ(1) is in order again.
    assert!(ev(true, "e l BS u") == 0, "the dropped key's column is forgotten");
    assert!(ev(true, "l s BS") == 1, "the count stays");
    // The syllable that is finished starts again.
    assert!(ev(true, "l s 3 c u p") == 0);
}

#[test]
fn key_order_clean_typing_is_not_evidence() {
    for syl in ["ㄋㄧˇ", "ㄓㄨㄛ", "ㄏㄠˇ", "ㄕ", "ㄩㄥˊ"] {
        let mut e = ko();
        let layout = Layout::Standard;
        let mut toned = false;
        let mut keys: Vec<Key> = syl
            .chars()
            .map(|c| match layout.key_of_tone(c) {
                Some(tk) => {
                    toned = true;
                    Key::ch(tk, 0)
                }
                None => Key::ch(layout.key_of_symbol(c).unwrap(), 0),
            })
            .collect();
        if !toned {
            keys.push(Key::new(KeyKind::Space));
        }
        for k in keys {
            e.key(k).unwrap();
            assert!(!e.english_open() && !e.english_inline_shown() && !e.english_autokept() && e.english_evidence() == 0, "{syl}");
        }
        assert!(e.english_shadow_len() == 0, "{syl}: finished");
    }
}

#[test]
fn key_order_two_letters_do_not_show_or_keep() {
    // A list with a word that starts with `cd`: `c d` is a replacement (E 1) and ㄎ is not a syllable, so everything but the length of
    // the shadow lets rule D show the letters; two letters never show (D needs 3), and the third key is no auto-keep.
    let dir = tmp_dir("keyorder-two");
    std::fs::write(dir.join("words.txt"), "cdrom\nlsof\n").unwrap();
    let mut e = Engine::with_lexicon(real(), Layout::Standard);
    e.load_english(&dir.join("words.txt")).unwrap();
    e.set_english(true).unwrap();
    e.set_english_key_order(true).unwrap();
    for k in ["c", "d"] {
        let o = run(&mut e, k);
        assert!(!e.english_inline_shown(), "two letters never show");
        assert!(o.preedit.chars().all(|c| !c.is_ascii_alphabetic()), "{}", o.preedit);
    }
    assert!(e.english_evidence() == 1 && e.english_shadow_len() == 2, "premise: one piece of evidence, two letters");
    let o = run(&mut e, "r");
    assert!(e.english_inline_shown() && o.preedit == "cdr", "premise: the third letter shows, the list has cdrom: {}", o.preedit);
    // And no auto-keep for two letters: the keep needs rule D on screen and a whole listed word, and the list has no two-letter word.
    assert!(!e.english_autokept());
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn key_order_two_letters_need_the_list_and_a_piece_of_evidence() {
    let mut e = ko();
    let o = run(&mut e, "z z _");
    assert!(!e.english_open(), "zz is not in TWO_LETTER");
    assert!(o.candidates.is_empty());
    let mut e = ko();
    run(&mut e, "u i _");
    assert!(!e.english_open(), "ui is in TWO_LETTER but typed in order: no evidence");
    // Three letters (not a listed word, not a two-letter word) with evidence: nothing.
    let mut e = ko();
    run(&mut e, "l s a _");
    assert!(!e.english_open());
    assert!(TWO_LETTER.iter().all(|w| w.len() == 2 && w.bytes().all(|b| b.is_ascii_lowercase())));
    assert!(TWO_LETTER.contains(&"ok") && TWO_LETTER.contains(&"ls"));
}

#[test]
fn key_order_turned_off_closes_the_window() {
    let mut e = ko();
    run(&mut e, "l s _");
    assert!(e.english_open());
    let o = e.set_english_key_order(false).unwrap();
    assert!(!e.english_open() && o.candidates.is_empty() && o.selected.is_none(), "closed");
    assert!(e.english_shadow_len() == 0, "the shadow is cleared");
    assert!(o.preedit == "ㄋㄠ", "the unfinished syllable stays: {}", o.preedit);
    // So Space is rule 10 now.
    assert!(run(&mut e, "_").preedit == shows("ㄋㄠ") && !e.english_open());
    // A window the switch did not open stays when the switch is set to off again.
    let mut e = eng_inline();
    run(&mut e, "r u s t _");
    e.set_english_key_order(false).unwrap();
    assert!(e.english_open());
}

#[test]
fn key_order_has_no_effect_where_the_shadow_is_not_recorded() {
    let build = |kind: &str, ko: bool| {
        let layout = if kind == "eten" { Layout::Eten } else { Layout::Standard };
        let mut e = Engine::with_lexicon(real(), layout);
        if kind != "nolist" {
            e.load_english(&tiny_list()).unwrap();
        }
        e.set_english(kind != "english-off").unwrap();
        if kind == "abbr" {
            e.set_abbreviation(true).unwrap();
        }
        e.set_english_key_order(ko).unwrap();
        e
    };
    for kind in ["eten", "abbr", "english-off", "nolist"] {
        for script in ["l s _", "o k _", "c u p _", "l s s _"] {
            let (mut a, mut b) = (build(kind, false), build(kind, true));
            assert!(run_all(&mut a, script) == run_all(&mut b, script), "{kind} {script}");
            assert!(!b.english_open(), "{kind} {script}");
        }
    }
}

/// zhen-keyorder section 4, the exhaustive clean typing (the Izanami test of vChewing): readings of the lexicon typed in column order
/// with the switch on and a word list of every three-letter word (so any three keys that are evidence could open a window), each
/// syllable ended by its tone key or Space. Never an English window, never the letters (rule D), never an auto-keep, never a piece
/// of evidence (E stays 0 after every key).
///
/// Every distinct syllable of every reading is typed alone (the whole syllable table, 1,417 at the time of writing). The readings
/// themselves are a sample: the full set is 416,227 readings and one pass took about 75 s in a release build on the maintainer's Mac
/// (measured 2026-10-26), over the 60 s the contract allows, so `SAMPLE` readings are typed (splitmix64 shuffle from `SEED`: the same
/// readings on every run; the contract asks for at least 20,000).
#[test]
fn key_order_clean_typing_of_every_reading_is_never_english() {
    const SEED: u64 = 20261026;
    const SAMPLE: usize = 40_000;
    let lex = real();
    let dir = tmp_dir("keyorder-exhaustive");
    let list: String = ('a'..='z').flat_map(|a| ('a'..='z').flat_map(move |b| ('a'..='z').map(move |c| format!("{a}{b}{c}\n")))).collect();
    std::fs::write(dir.join("words.txt"), list).unwrap();
    let mut e = Engine::with_lexicon(lex.clone(), Layout::Standard);
    e.load_english(&dir.join("words.txt")).unwrap();
    e.set_english(true).unwrap();
    e.set_english_key_order(true).unwrap();
    let layout = Layout::Standard;
    let keys_of = |syl: &str| -> Vec<Key> {
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
    };
    let type_one = |e: &mut Engine, syls: &[&str]| {
        e.reset(ResetMode::Discard);
        for syl in syls {
            for k in keys_of(syl) {
                e.key(k).unwrap();
                assert!(
                    !e.english_open() && !e.english_inline_shown() && !e.english_autokept() && e.english_evidence() == 0,
                    "clean typing looked like English"
                );
            }
        }
    };
    let n = lex.reading_count();
    let seen: std::collections::BTreeSet<&str> = (0..n).flat_map(|r| lex.reading_syllables(r)).collect();
    for s in &seen {
        type_one(&mut e, &[s]);
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
        type_one(&mut e, &lex.reading_syllables(r));
    }
    println!("typed {} of {n} readings and {} distinct syllables", chosen.len(), seen.len());
    assert!(n >= 20_000 && seen.len() > 1000 && chosen.len() >= 20_000, "the test saw enough");
    let _ = std::fs::remove_dir_all(&dir);
}
