//! zhen-mixed engine tests (docs/contracts/zhen-mixed.md section 5.1): the shadow, the trigger, the English window and
//! its keys, on the real lexicon and the synthetic list eval/golden/english-tiny.txt.
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
/// Standard layout, the list loaded, the switch on.
fn eng() -> Engine {
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
fn opens_on_space_commits_without_space() {
    let mut e = eng();
    let o = run(&mut e, "r u s t _");
    assert!(e.english_open());
    assert!(o.candidates == ["rust", "Rust", "RUST"] && o.selected == Some(0) && o.total == 3, "three items");
    assert!(o.preedit == "ㄔㄧ", "the unfinished syllable stays in the preedit");
    let o = run(&mut e, "1");
    assert!(o.handled && o.commit == "rust", "the word, no space");
    assert!(o.preedit.is_empty() && o.candidates.is_empty() && o.selected.is_none() && !e.english_open());
    assert!(is_empty(&mut e), "syls are empty");
    assert!(e.english_shadow_len() == 0);
}

#[test]
fn other_items_commit_their_own_text() {
    for (digit, want) in [("2", "Rust"), ("3", "RUST")] {
        let mut e = eng();
        run(&mut e, "r u s t _");
        assert!(run(&mut e, digit).commit == want);
    }
    // Right moves the selection, Enter takes it.
    let mut e = eng();
    run(&mut e, "r u s t _ Right");
    assert!(run(&mut e, "Enter").commit == "Rust");
}

#[test]
fn enter_and_shift_enter_pick() {
    for last in ["Enter", "S-Enter"] {
        let mut e = eng();
        let o = run(&mut e, &format!("r u s t _ {last}"));
        assert!(o.handled && o.commit == "rust", "{last}");
    }
}

#[test]
fn pick_equals_digit() {
    let (mut a, mut b) = (eng(), eng());
    run(&mut a, "r u s t _");
    run(&mut b, "r u s t _");
    let by_pick = a.pick(0).unwrap().unwrap();
    assert!(by_pick == b.key(Key::ch('1', 0)).unwrap());
    assert!(by_pick.commit == "rust");
}

#[test]
fn left_composition_is_committed_too() {
    let mut e = eng();
    e.set_left_context("他");
    run(&mut e, "s u 3");
    let you = shows("ㄋㄧˇ");
    let o = run(&mut e, "r u s t _ 1");
    assert!(o.commit == format!("{you}rust"), "the shown composition, then the word");
    assert!(o.preedit.is_empty() && o.candidates.is_empty() && is_empty(&mut e));
    assert!(you == "你", "premise: the composition shows the character");
    // The left context goes with the commit (take_commit, as for Enter): a left context set before is not kept for the next one.
    // (No observable difference without a model, so the engine state is only the empty composition above.)
}

/// Types ㄊㄞˊ ㄨㄢ, opens the candidates and takes the first two-character word that is not the one shown, so a learn is
/// pending (`pre` is the shown text). The output of that pick.
fn repick_two(e: &mut Engine) -> Output {
    syllable(e, "ㄊㄞˊ");
    let shown = syllable(e, "ㄨㄢ").preedit;
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
    assert!(o.commit.is_empty() && o.preedit != shown, "premise: the pick is fixed over the span");
    o
}

#[test]
fn learning_follows_the_commit_rules() {
    // The same two picks, then Enter (control) or `rust` Space Enter: the learning file must be the same bytes.
    let (da, db) = (tmp_dir("learn-a"), tmp_dir("learn-b"));
    let mut commits = Vec::new();
    for (dir, english) in [(&da, false), (&db, true)] {
        let mut e = eng();
        e.set_today(Some(20_000));
        e.set_learning(true);
        e.learning_open(dir).unwrap();
        repick_two(&mut e);
        commits.push(run(&mut e, if english { "r u s t _ Enter" } else { "Enter" }).commit);
    }
    assert!(commits[1] == format!("{}rust", commits[0]), "English commit is the composition plus the word: {} / {}", commits[0], commits[1]);
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
    repick_two(&mut e);
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
