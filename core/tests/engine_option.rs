//! Rule 1o (docs/spec/keys-and-composition.md §4.1, §8.1): Option and Option+Shift with a key of the main block. Every cell of the table measured on Apple's
//! Zhuyin goes into the composition as rule 2 puts a mark in; behind an unfinished syllable, with a window open, the prediction row entered, or an English run or
//! digit run open the key is eaten: handled, and every output after it is the one the same keys give without it. The ASCII state and every other modifier
//! combination stay rule 1. The engine is the App's (`Engine::new`, the model, English mixing on), so the row, the digit layer and the English run are all there.
use core::engine::*;
use std::path::{Path, PathBuf};

#[path = "common/digits_scripts.rs"]
mod scripts;
use scripts::{line, send};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}
fn app() -> Engine {
    let mut e = Engine::new(&root().join("data/lexicon"), Layout::Standard).unwrap();
    e.load_lm(&root().join("data/lm/bigram.sjlm")).unwrap();
    e.set_english(true).unwrap();
    e.set_english_overwrite(false).unwrap();
    e
}
/// A fresh composition, the script's keys; the output of each as a line of every field.
fn lines(e: &mut Engine, script: &str) -> Vec<String> {
    e.reset(ResetMode::Discard);
    script.split_whitespace().map(|t| line(&send(e, t))).collect()
}
/// A line without its first field (`handled`).
fn rest(l: &str) -> &str {
    l.split_once('|').unwrap().1
}

/// The measured table: key, Option, Option+Shift (the contract's §2.1; 21 keys, 42 cells, and the letters a and u make 46).
const CELLS: [(char, char, char); 21] = [
    ('`', '\u{FF40}', '\u{FF5E}'),
    ('1', '\u{FF11}', '\u{FF01}'),
    ('2', '\u{FF12}', '\u{FF20}'),
    ('3', '\u{FF13}', '\u{FF03}'),
    ('4', '\u{FF14}', '\u{FF04}'),
    ('5', '\u{FF15}', '\u{FF05}'),
    ('6', '\u{FF16}', '\u{FF3E}'),
    ('7', '\u{FF17}', '\u{FF06}'),
    ('8', '\u{FF18}', '\u{FF0A}'),
    ('9', '\u{FF19}', '\u{FF08}'),
    ('0', '\u{FF10}', '\u{FF09}'),
    ('-', '\u{2014}', '\u{FF3F}'),
    ('=', '\u{FF1D}', '\u{FF0B}'),
    ('[', '\u{300C}', '\u{300E}'),
    (']', '\u{300D}', '\u{300F}'),
    ('\\', '\u{3001}', '\u{FF5C}'),
    (';', '\u{FF1B}', '\u{FF1A}'),
    ('\'', '\u{2018}', '\u{201C}'),
    (',', '\u{FF0C}', '\u{300A}'),
    ('.', '\u{FF0E}', '\u{300B}'),
    ('/', '\u{FF0F}', '\u{FF1F}'),
];
/// Every cell: the 21 keys, and the letters a-z (full-width lower case, with Shift upper case).
fn cells() -> Vec<(char, bool, char)> {
    let mut v: Vec<(char, bool, char)> = CELLS.iter().flat_map(|&(k, p, s)| [(k, false, p), (k, true, s)]).collect();
    for (i, k) in ('a'..='z').enumerate() {
        v.push((k, false, char::from_u32(0xFF41 + i as u32).unwrap()));
        v.push((k, true, char::from_u32(0xFF21 + i as u32).unwrap()));
    }
    v
}
/// The rule 2 key that puts `c` into the composition, for the characters rule 2 has a key for (docs/spec/keys-and-composition.md §8.1).
fn rule2(c: char) -> Option<&'static str> {
    Some(match c {
        '，' => "S-,",
        '？' => "S-/",
        '！' => "S-1",
        '：' => "S-;",
        '『' => "S-[",
        '』' => "S-]",
        '（' => "S-9",
        '）' => "S-0",
        '～' => "S-`",
        '｜' => "S-\\",
        '“' => "S-'",
        '＋' => "S-=",
        '「' => "[",
        '」' => "]",
        '、' => "\\",
        '‘' => "'",
        '＝' => "=",
        _ => return None,
    })
}

/// Nothing composed: the cell's character is in the composition (not committed), Enter sends it; where rule 2 has a key for the same character, every
/// field of both outputs is the same as that key's.
#[test]
fn every_cell_goes_into_an_empty_composition_as_rule_2_puts_it() {
    let mut e = app();
    let (mut n, mut same) = (0, 0);
    for (k, shift, want) in cells() {
        let tok = format!("{}{k}", if shift { "OS-" } else { "O-" });
        e.reset(ResetMode::Discard);
        let o = send(&mut e, &tok);
        assert!(o.handled && o.commit.is_empty(), "{tok}");
        assert_eq!(o.preedit, want.to_string(), "{tok}");
        assert_eq!(o.cursor_utf16, want.len_utf16() as u32, "{tok}");
        assert!(o.selected.is_none() && o.columns == 0, "{tok}: no window");
        let enter = send(&mut e, "Enter");
        assert!(enter.handled && enter.preedit.is_empty(), "{tok}: Enter is eaten");
        assert_eq!(enter.commit, want.to_string(), "{tok}");
        if let Some(r2) = rule2(want) {
            assert_eq!(lines(&mut e, &format!("{tok} Enter")), lines(&mut e, &format!("{r2} Enter")), "{tok} against {r2}");
            same += 1;
        }
        n += 1;
    }
    assert_eq!((n, same), (42 + 52, 17));
    assert_eq!(cells().iter().filter(|c| !c.0.is_ascii_alphabetic() || c.0 == 'a' || c.0 == 'u').count(), 46, "the measured cells");
}

/// A complete character and no unfinished syllable (measured: ㄘㄜˋ then Option+; then Return gives 測；): the character goes in at the cursor, in the
/// middle too, and the outputs are rule 2's for the same character.
#[test]
fn behind_a_complete_character_the_cell_is_inserted() {
    let mut e = app();
    let base = lines(&mut e, "h k 4").pop().unwrap();
    let ce = base.split('|').nth(2).unwrap().to_string();
    assert_eq!(ce.chars().count(), 1);
    e.reset(ResetMode::Discard);
    let outs: Vec<Output> = "h k 4 O-; Enter".split_whitespace().map(|t| send(&mut e, t)).collect();
    assert!(outs[3].handled && outs[3].commit.is_empty());
    assert_eq!(outs[3].preedit, format!("{ce}；"));
    assert_eq!(outs[4].commit, format!("{ce}；"));
    for (tok, r2) in [("OS-;", "S-;"), ("O-,", "S-,"), ("O-[", "["), ("OS-/", "S-/")] {
        for (pre, post) in [("h k 4", "Enter"), ("h k 4 s u 3 Left", "j i 3 Enter"), ("h k 4", "Down Esc BS BS Enter")] {
            assert_eq!(lines(&mut e, &format!("{pre} {tok} {post}")), lines(&mut e, &format!("{pre} {r2} {post}")), "{pre} {tok} {post}");
        }
    }
}

/// A syllable the digit keys just closed (`8 4`, docs/spec/english-mixed.md §7.7) is a complete character with nothing unfinished: the cell is inserted, and
/// what the next keys list is what they list after rule 2's key for the same character.
#[test]
fn behind_a_syllable_the_digit_keys_closed_the_cell_is_inserted_as_rule_2_does() {
    let mut e = app();
    let plain = lines(&mut e, "8 4 Down");
    assert!(plain[2].contains(",84|"), "the other reading closes the list: {}", plain[2]);
    for post in ["Down", "s u 3 Down", "Left Down"] {
        assert_eq!(lines(&mut e, &format!("8 4 O-, {post}")), lines(&mut e, &format!("8 4 S-, {post}")), "{post}");
    }
}

/// The eaten states: `pre` leaves the state, `post` goes on from it. With any number of Option keys of the rule after `pre` (and between the keys of `post`),
/// each is handled, commits nothing and returns the fields of the output before it; the outputs of `post`, the units and the late keys are the ones
/// without them.
fn eaten(e: &mut Engine, pre: &str, post: &str, state: impl Fn(&Engine, &Output) -> bool) {
    const OPT: [&str; 6] = ["O-;", "OS-;", "O-,", "O-.", "O-a", "OS-1"];
    e.reset(ResetMode::Discard);
    let mut plain = Vec::new();
    for t in pre.split_whitespace().chain(post.split_whitespace()) {
        let o = send(e, t);
        plain.push((line(&o), e.syllables().join(" "), e.late_keys()));
    }
    let n_pre = pre.split_whitespace().count();
    e.reset(ResetMode::Discard);
    let mut last = String::new();
    for (i, t) in pre.split_whitespace().chain(post.split_whitespace()).enumerate() {
        let o = send(e, t);
        assert_eq!((line(&o), e.syllables().join(" "), e.late_keys()), plain[i], "{pre} / {post}: key {i} ({t})");
        last = line(&o);
        if i + 1 == n_pre {
            assert!(state(e, &o), "{pre}: not the state the test is about ({last})");
            for opt in OPT {
                let x = send(e, opt);
                assert!(x.handled && x.commit.is_empty(), "{pre} {opt}");
                assert_eq!(rest(&line(&x)), rest(&last), "{pre} {opt}: the output's fields");
                assert_eq!((e.syllables().join(" "), e.late_keys()), (plain[i].1.clone(), plain[i].2.clone()), "{pre} {opt}: units and late keys");
            }
        }
    }
    assert!(!last.is_empty());
}

#[test]
fn behind_an_unfinished_syllable_the_key_is_eaten() {
    let mut e = app();
    let unfinished = |_: &Engine, o: &Output| o.preedit.ends_with(['ㄘ', 'ㄋ', 'ㄧ']);
    // Measured: only ㄘ, then the Option keys, then Return sends ㄘ; 測 and ㄘ sends 測ㄘ.
    eaten(&mut e, "h", "Enter", unfinished);
    eaten(&mut e, "h k 4 h", "Enter", unfinished);
    eaten(&mut e, "s", "u 3 j i 3 Down Enter", unfinished);
    eaten(&mut e, "s u", "3 Tab Enter Enter", unfinished);
    e.reset(ResetMode::Discard);
    assert_eq!("h O-; OS-; O-, O-. O-a Enter".split_whitespace().map(|t| send(&mut e, t).commit).collect::<String>(), "ㄘ");
}

/// docs/spec/english-mixed.md §7.4: the late keys (`.` after `. u`) wait for the syllable's closing key; the eaten key leaves them, so the tone key decides
/// as it does without it (有, the `.` rejoined).
#[test]
fn an_eaten_key_keeps_the_late_tokens() {
    let mut e = app();
    eaten(&mut e, ". u", "3 Enter", |e, _| e.late_keys() == ".");
    e.reset(ResetMode::Discard);
    let outs: Vec<Output> = ". u O-; 3".split_whitespace().map(|t| send(&mut e, t)).collect();
    assert_eq!(e.syllables().join(" "), "ㄧㄡˇ");
    assert_eq!(outs[3].preedit, "有");
    eaten(&mut e, "1 . u", "s _ Enter", |e, _| e.late_keys() == "1.");
}

#[test]
fn with_a_window_open_or_the_row_entered_the_key_is_eaten() {
    let mut e = app();
    let window = |_: &Engine, o: &Output| o.selected.is_some() && o.total > 1 && o.first == 0;
    eaten(&mut e, "s u 3 Down", "Down Enter Enter", window);
    eaten(&mut e, "s u 3 j i 3 Down", "2 Enter", window);
    // The entered row: `selected` is set and the candidates are the row's, no window opened (Tab leaves the preedit alone).
    let mut probe = app();
    let before = send(&mut probe, "s");
    let row = |_: &Engine, o: &Output| o.selected == Some(0) && !o.candidates.is_empty();
    assert!(!before.candidates.is_empty() && before.selected.is_none(), "a row shows behind ㄋ");
    eaten(&mut e, "s Tab", "Right Enter Enter", row);
    eaten(&mut e, "s Tab", "Tab 1 Enter", row);
}

#[test]
fn in_an_english_run_or_a_digit_run_the_key_is_eaten() {
    let mut e = app();
    eaten(&mut e, "S-A b", "c _ Enter", |_, o| o.english_run_utf16_len == 2);
    eaten(&mut e, "s u 3 S-A", "i Enter", |_, o| o.english_run_utf16_len == 1);
    // Digits buffered (shown as digits or as a tail), and right behind digit tokens with the run still open (a tone key alone is a token at once).
    eaten(&mut e, "2 0", "2 6 Enter", |e, _| e.digits_typed().is_some());
    eaten(&mut e, "2 0 2 6", "s u 0 6 Enter", |e, _| e.digits_typed().is_some());
    eaten(&mut e, "3", "4 Down Esc Enter", |e, o| e.digits_typed().is_some() && o.preedit == "3");
    eaten(&mut e, "8", "4 Down", |e, _| e.digits_typed().is_some());
}

/// The ASCII state: rule 1o does not apply, the key is passed on as rule 1 says (nothing composed, and with an English run open).
#[test]
fn in_the_ascii_state_the_key_is_passed_on() {
    let mut e = app();
    e.set_ascii(true).unwrap();
    for tok in ["O-;", "OS-;", "O-a", "O-1"] {
        let o = send(&mut e, tok);
        assert!(!o.handled && o.commit.is_empty() && o.preedit.is_empty(), "{tok}");
    }
    let run = send(&mut e, "a");
    assert_eq!(run.preedit, "a");
    for tok in ["O-;", "OS-;", "O-a", "O-1"] {
        let o = send(&mut e, tok);
        assert!(!o.handled, "{tok}");
        assert_eq!(rest(&line(&o)), rest(&line(&run)), "{tok}");
    }
    assert_eq!(send(&mut e, "b").preedit, "ab");
}

/// Everything else with Option stays rule 1: with Command, Control or Caps Lock too, and the keys outside the rule (arrows, Backspace, Space, Enter, Tab).
/// Not handled, nothing committed, and the next key's output is the one without it, on an empty composition and behind a character.
#[test]
fn other_option_combinations_are_passed_on() {
    let mut e = app();
    let with = |kind, modifiers| Key { kind, ch: '\0', modifiers };
    let mut keys = Vec::new();
    for extra in [MOD_COMMAND, MOD_CONTROL, MOD_CAPSLOCK, MOD_COMMAND | MOD_SHIFT, MOD_CAPSLOCK | MOD_SHIFT] {
        for c in [';', ',', 'a', '1'] {
            keys.push(Key::ch(c, MOD_OPTION | extra));
        }
    }
    for kind in [KeyKind::Left, KeyKind::Right, KeyKind::Up, KeyKind::Down, KeyKind::Backspace, KeyKind::Delete, KeyKind::Space, KeyKind::Enter, KeyKind::Tab] {
        keys.push(with(kind, MOD_OPTION));
        keys.push(with(kind, MOD_OPTION | MOD_SHIFT));
    }
    for pre in ["", "s u 3", "s u"] {
        let plain = lines(&mut e, &format!("{pre} j i 3 Enter"));
        for &key in &keys {
            e.reset(ResetMode::Discard);
            let before: Vec<String> = pre.split_whitespace().map(|t| line(&send(&mut e, t))).collect();
            let o = e.key(key).unwrap();
            assert!(!o.handled && o.commit.is_empty(), "{pre}: modifiers {} kind {}", key.modifiers, key.kind as u32);
            if let Some(b) = before.last() {
                assert_eq!(rest(&line(&o)), rest(b), "{pre}: modifiers {}", key.modifiers);
            }
            let after: Vec<String> = "j i 3 Enter".split_whitespace().map(|t| line(&send(&mut e, t))).collect();
            assert_eq!(before.iter().chain(&after).collect::<Vec<_>>(), plain.iter().collect::<Vec<_>>(), "{pre}: modifiers {}", key.modifiers);
        }
    }
}
