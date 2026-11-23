//! Mixed words (docs/spec/english-mixed.md §5.7): an English token followed by syllables the table `mixed-words.tsv` has as one word
//! (`CP值`) adds that word to the candidate list; choosing it puts one token of the punctuation kind in their place. Real lexicon
//! and model, standard layout, English mixing on, adjacent-key slips off; a small test table except in the check set's test.
//!
//! Key script, whitespace separated: a single character is that keycap, `_` Space, `Enter`, `Esc`, `BS` Backspace, `Left`, `Right`,
//! `Down`, `S-x` with Shift, `Cmd-BS` with Command.
use core::engine::*;
use core::learn::{NEWWORD, PUNCT_PREFIX};
use core::lm::{CappedLexicon, Lm};
use core::Lexicon;
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}
struct Shared {
    lex: Arc<Lexicon>,
    lm: Arc<Lm>,
    capped: Arc<CappedLexicon>,
}
fn shared() -> &'static Shared {
    static S: OnceLock<Shared> = OnceLock::new();
    S.get_or_init(|| {
        let lex = load_lexicon(&root().join("data/lexicon")).unwrap();
        let lm = Lm::load(&root().join("data/lm/bigram.sjlm")).expect("data/lm/bigram.sjlm and classes.sjc (see engine_lm.rs)");
        let overlay = std::fs::read_to_string(root().join("data/lexicon/overlay-add.tsv")).unwrap();
        let capped = Arc::new(CappedLexicon::new(lex.clone(), &overlay, &lm, None).unwrap());
        Shared { lex, lm: Arc::new(lm), capped }
    })
}
const DAY: i64 = 20_000;
const L: Layout = Layout::Standard;

fn keys_of(syl: &str) -> Vec<Key> {
    let mut v = Vec::new();
    let mut toned = false;
    for c in syl.chars() {
        match L.key_of_tone(c) {
            Some(tk) => {
                v.push(Key::ch(tk, 0));
                toned = true;
            }
            None => v.push(Key::ch(L.key_of_symbol(c).expect("symbol has a key"), 0)),
        }
    }
    if !toned {
        v.push(Key::new(KeyKind::Space));
    }
    v
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

const TABLE: &str = "# test table\ncp\tㄓˊ\tCP值\t100\nsim\tㄎㄚˇ\tSIM卡\t50\nget\tㄏㄠˇ ㄉㄤˇ\tget好檔\t10\ncp\tㄇㄛ\tCP摸\t5\n";
const FORGET: &str = "Cmd-BS";

/// Production lexicon and model, English mixing on, learning on, fixed clock; no table.
fn plain() -> Engine {
    let s = shared();
    let mut e = Engine::with_lexicon(s.lex.clone(), Layout::Standard);
    e.set_lm(s.lm.clone(), s.capped.clone());
    e.set_today(Some(DAY));
    e.set_learning(true);
    e.set_prediction(false).unwrap();
    e.set_english(true).unwrap();
    e
}
/// The same with the test table.
fn eng() -> Engine {
    let mut e = plain();
    assert!(e.set_mixed_words(TABLE) && e.mixed_words_loaded());
    e
}
fn type_syls(e: &mut Engine, reading: &str) -> Output {
    let mut o = None;
    for s in reading.split(' ') {
        for key in keys_of(s) {
            o = Some(e.key(key).unwrap());
        }
    }
    o.unwrap()
}
/// Every item of the window `o` just opened, in list order (the selection walks the list once).
fn items(e: &mut Engine, o: &Output) -> Vec<String> {
    let mut v = vec![o.candidates[o.selected.unwrap()].clone()];
    for _ in 1..o.total {
        let o = run(e, "Right");
        v.push(o.candidates[o.selected.unwrap()].clone());
    }
    v
}
/// Opens the window with Down and returns its items; the window is closed again.
fn listed(e: &mut Engine) -> Vec<String> {
    let o = run(e, "Down");
    if o.candidates.is_empty() {
        return Vec::new();
    }
    let v = items(e, &o);
    run(e, "Esc");
    v
}
/// Opens the window with Down and moves the selection to `word`.
fn open_on(e: &mut Engine, word: &str) -> Output {
    let mut o = run(e, "Down");
    for _ in 0..o.total {
        if o.candidates[o.selected.unwrap()] == word {
            return o;
        }
        o = run(e, "Right");
    }
    panic!("candidate {word} not listed");
}
fn choose(e: &mut Engine, word: &str) -> Output {
    open_on(e, word);
    run(e, "Enter")
}
fn units(e: &Engine) -> Vec<String> {
    e.syllables().to_vec()
}
fn token(text: &str) -> String {
    format!("{PUNCT_PREFIX}{text}")
}
fn records(e: &Engine) -> Vec<(String, String)> {
    e.learner().records().iter().map(|r| (r.context.clone(), r.word.clone())).collect()
}
fn is_empty(e: &mut Engine) -> bool {
    !e.key(Key::new(KeyKind::Left)).unwrap().handled
}

/// The token a revert made (`c p` Space: the syllable is missing) and one syllable: the list is the mixed word, then the list
/// without a table.
#[test]
fn revert_token_then_syllable_lists_the_mixed_word() {
    let mut e = eng();
    run(&mut e, "c p _");
    assert_eq!(units(&e), [token("cp")], "premise: the keys reverted to an English token");
    run(&mut e, "5 6");
    assert_eq!(units(&e), [token("cp"), "ㄓˊ".to_string()]);
    let with = listed(&mut e);
    let mut p = plain();
    run(&mut p, "c p _ 5 6");
    let mut without = listed(&mut p);
    assert!(!without.is_empty() && !without.contains(&"CP值".to_string()), "premise: a list without the table: {without:?}");
    without.insert(0, "CP值".to_string());
    assert_eq!(with, without);
    // Down opens on it: Enter (or 1) takes it.
    let o = run(&mut e, "Down");
    assert!(o.selected == Some(0) && o.candidates[0] == "CP值");
    assert_eq!(run(&mut e, "Enter").preedit, "CP值");
}

/// A token Shift opened, whatever its case: the table is asked in lower case.
#[test]
fn shift_token_then_syllables() {
    for (script, typed) in [("S-s S-i S-m _ d 8 3", "SIM"), ("S-s i m _ d 8 3", "Sim")] {
        let mut e = eng();
        run(&mut e, script);
        assert_eq!(units(&e), [token(typed), "ㄎㄚˇ".to_string()], "{script}");
        let list = listed(&mut e);
        assert_eq!(list.first().map(String::as_str), Some("SIM卡"), "{script}: {list:?}");
    }
}

/// Choosing it: one token in place of the English token and the syllable.
#[test]
fn pick_makes_one_token() {
    let mut e = eng();
    run(&mut e, "c p _ 5 6");
    let o = choose(&mut e, "CP值");
    assert_eq!(units(&e), [token("CP值")]);
    assert!(o.preedit == "CP值" && o.cursor_utf16 == 3 && o.commit.is_empty() && o.candidates.is_empty(), "{} {}", o.preedit, o.cursor_utf16);
    assert_eq!(run(&mut e, "Enter").commit, "CP值");
    assert!(is_empty(&mut e));
}

/// What is typed after it decodes as it does after a punctuation mark.
#[test]
fn pick_then_more_syllables_decode_alone() {
    let mut e = eng();
    run(&mut e, "c p _ 5 6");
    choose(&mut e, "CP值");
    let after = type_syls(&mut e, "ㄏㄣˇ ㄍㄠ").preedit;
    let mut p = plain();
    run(&mut p, "S-,");
    let mark = type_syls(&mut p, "ㄏㄣˇ ㄍㄠ").preedit;
    assert!(after.starts_with("CP值") && mark.starts_with('，'), "{after} {mark}");
    assert_eq!(after["CP值".len()..], mark["，".len()..]);
    assert_eq!(units(&e), [token("CP值"), "ㄏㄣˇ".to_string(), "ㄍㄠ".to_string()]);
}

#[test]
fn pick_is_not_learned() {
    let mut e = eng();
    run(&mut e, "c p _ 5 6");
    let before = records(&e);
    choose(&mut e, "CP值");
    assert_eq!(run(&mut e, "Enter").commit, "CP值");
    assert_eq!(records(&e), before);
}

/// The keys after it are the keys after a punctuation token: it goes whole, Esc clears the composition.
#[test]
fn edit_after_pick() {
    let picked = || {
        let mut e = eng();
        run(&mut e, "c p _ 5 6");
        choose(&mut e, "CP值");
        e
    };
    let mut e = picked();
    assert_eq!(run(&mut e, "BS").preedit, "");
    assert!(is_empty(&mut e));
    let mut e = picked();
    let o = run(&mut e, "Left");
    assert!(o.cursor_utf16 == 0 && o.preedit == "CP值", "the arrow takes it whole");
    assert_eq!(run(&mut e, "Delete").preedit, "");
    assert!(is_empty(&mut e));
    let mut e = picked();
    run(&mut e, "Esc");
    assert!(is_empty(&mut e));
}

/// Down or Space right after it: a window with the writing alone; choosing it changes nothing.
#[test]
fn down_and_space_after_pick() {
    for key in ["Down", "_"] {
        let mut e = eng();
        run(&mut e, "c p _ 5 6");
        choose(&mut e, "CP值");
        let o = run(&mut e, key);
        assert!(o.candidates == ["CP值"] && o.total == 1, "{key}: {:?}", o.candidates);
        let o = run(&mut e, "Enter");
        assert!(o.candidates.is_empty() && o.preedit == "CP值" && o.cursor_utf16 == 3 && o.commit.is_empty(), "{key}");
        assert_eq!(units(&e), [token("CP值")], "{key}");
        let o = run(&mut e, key);
        assert_eq!(o.candidates, ["CP值"], "{key}");
        let o = run(&mut e, "Esc");
        assert!(o.candidates.is_empty() && o.preedit == "CP值", "{key}: Esc closes the window");
    }
}

#[test]
fn forget_on_mixed_item_does_nothing() {
    let mut e = eng();
    run(&mut e, "c p _ 5 6");
    let before = records(&e);
    let o = open_on(&mut e, "CP值");
    let o2 = run(&mut e, FORGET);
    assert!(o2.candidates == o.candidates && o2.selected == o.selected && o2.total == o.total);
    assert_eq!(records(&e), before);
    assert_eq!(run(&mut e, "Enter").preedit, "CP值", "the item is still the mixed word");
}

/// A new word of the same list is forgotten: the mixed word is still the first item and is still chosen as one token. The reading
/// ㄏㄠˇ ㄉㄤˇ has no lexicon word, so a new word can be learned on it.
#[test]
fn forgetting_a_new_word_leaves_the_mixed_item() {
    let reading = ["ㄏㄠˇ".to_string(), "ㄉㄤˇ".to_string()];
    assert!(shared().lex.entries(&reading).is_empty(), "premise: no lexicon word on the reading");
    let taught = || {
        let mut e = eng();
        // Teach 好檔: re-pick the characters the decode shows otherwise, Enter.
        let shown: Vec<char> = type_syls(&mut e, "ㄏㄠˇ ㄉㄤˇ").preedit.chars().collect();
        let want: Vec<char> = "好檔".chars().collect();
        assert!(shown != want, "premise: the decode does not show the word by itself");
        for i in 0..2 {
            if run(&mut e, "End").preedit.chars().nth(i) != Some(want[i]) {
                for _ in 0..2 - (i + 1) {
                    run(&mut e, "Left");
                }
                choose_span(&mut e, &want[i].to_string());
                run(&mut e, "End");
            }
        }
        assert_eq!(run(&mut e, "Enter").commit, "好檔");
        assert!(e.learner().records().iter().any(|r| r.context == NEWWORD && r.word == "好檔"), "premise: the new word is learned");
        run(&mut e, "S-g e t _");
        type_syls(&mut e, "ㄏㄠˇ ㄉㄤˇ");
        assert_eq!(units(&e), [token("Get"), "ㄏㄠˇ".to_string(), "ㄉㄤˇ".to_string()]);
        e
    };
    let mut e = taught();
    let list = listed(&mut e);
    assert!(list[0] == "get好檔" && list.contains(&"好檔".to_string()), "premise: the mixed word first, the new word in the list: {list:?}");
    open_on(&mut e, "好檔");
    let o = run(&mut e, FORGET);
    assert!(!o.candidates.is_empty(), "the window stays open");
    assert!(!e.learner().records().iter().any(|r| r.context == NEWWORD && r.word == "好檔"), "the new word is forgotten");
    run(&mut e, "Esc");
    let list = listed(&mut e);
    assert!(list[0] == "get好檔" && !list.contains(&"好檔".to_string()), "{list:?}");
    choose(&mut e, "get好檔");
    assert_eq!(units(&e), [token("get好檔")]);
    // The item after the mixed word is an ordinary pick.
    let mut e = taught();
    let list = listed(&mut e);
    choose(&mut e, &list[1]);
    assert!(units(&e).len() > 1 && e.syllables()[0] == token("Get"), "{:?}", units(&e));
}
/// With the cursor right after the syllable to re-pick: Space opens its window, the single character `word` is chosen.
fn choose_span(e: &mut Engine, word: &str) {
    let mut o = run(e, "_");
    for _ in 0..o.total {
        if o.candidates[o.selected.unwrap()] == word {
            run(e, "Enter");
            return;
        }
        o = run(e, "Right");
    }
    panic!("candidate {word} not listed");
}

/// The cursor in the middle, no table, no match: the list is the list without the feature.
#[test]
fn middle_cursor_no_table_and_no_match_are_today() {
    // The cursor not at the end.
    let (mut e, mut p) = (eng(), plain());
    for x in [&mut e, &mut p] {
        run(x, "c p _ 5 6");
        type_syls(x, "ㄋㄧˇ");
        run(x, "Left");
    }
    let (with, without) = (listed(&mut e), listed(&mut p));
    assert!(!with.is_empty() && with == without, "{with:?}");
    // No table.
    let mut p = plain();
    assert!(!p.mixed_words_loaded());
    run(&mut p, "c p _ 5 6");
    assert!(!listed(&mut p).contains(&"CP值".to_string()));
    // A reading the table lacks after the token.
    let (mut e, mut p) = (eng(), plain());
    for x in [&mut e, &mut p] {
        run(x, "c p _");
        type_syls(x, "ㄉㄜ˙");
    }
    let (with, without) = (listed(&mut e), listed(&mut p));
    assert!(!with.is_empty() && with == without, "{with:?}");
    // Two syllables after the token when the table has the first alone: the pair is asked, not its tail.
    let mut e = eng();
    run(&mut e, "c p _ 5 6");
    type_syls(&mut e, "ㄋㄧˇ");
    assert!(!listed(&mut e).contains(&"CP值".to_string()));
}

/// A table with one bad row is not loaded at all, and takes the loaded one with it.
#[test]
fn bad_table_is_not_loaded() {
    let many: String = (0..5001).map(|i| format!("a{i}\tㄓˊ\ta{i}值\t1\n")).collect();
    let bad = [
        "cp\tㄓˊ\tCP值\t100\ncp\tㄓˊ\tCP質\t1\n",  // the same English and reading twice
        "cp\tㄓˊ\tDP值\t100\n",                     // the writing does not start with the English part
        "cp\tㄓˊ\tCP值值\t100\n",                   // more characters than syllables
        "cp\tㄓˊ\tCPa\t100\n",                      // not a Han character
        "4g\tㄎㄚˇ\t4G卡\t100\n",                   // the English part starts with a digit
        "CP\tㄓˊ\tCP值\t100\n",                     // the English part is not lower case
        "abcdefghijklmnopq\tㄓˊ\tabcdefghijklmnopq值\t100\n", // seventeen letters
        "cp\tㄓㄓ\tCP值\t100\n",                    // not a syllable of the lexicon
        "cp\tㄓˊ ㄓˊ ㄓˊ ㄓˊ ㄓˊ\tCP值值值值值\t100\n", // five syllables
        "cp\tㄓˊ\tCP值\t0\n",                       // the count is not positive
        "cp\tㄓˊ\tCP值\n",                          // three fields
        "cp\tㄓˊ\tCP值\t100\thidden\n",             // a fifth field that is not the mark
        "cp\tㄓˊ\tCP值\t100\t\n",                   // an empty fifth field
        "cp\tㄓˊ\tCP值\t100\tnopredict\tx\n",        // six fields
        many.as_str(),                              // over the row limit
    ];
    for t in bad {
        let mut e = eng();
        assert!(!e.set_mixed_words(t) && !e.mixed_words_loaded(), "{}", &t[..t.len().min(40)]);
        run(&mut e, "c p _ 5 6");
        assert!(!listed(&mut e).contains(&"CP值".to_string()));
    }
    let mut e = plain();
    assert!(e.set_mixed_words("# only a comment\n\n") && e.mixed_words_loaded(), "an empty table is a table");
    assert!(e.set_mixed_words("c\tㄓˊ\tC值\t100\nc4\tㄓˊ\tC4值\t1\n") && e.mixed_words().len() == 2, "one letter, and digits after the first letter, are rows");
}

/// The mixed word goes first; the two items of a short word (`ai`, typed in column order: ㄇㄛ) still close the list.
#[test]
fn mixed_item_sits_before_the_short_items() {
    let (mut e, mut p) = (eng(), plain());
    for x in [&mut e, &mut p] {
        run(x, "c p _ a i _");
        assert_eq!(units(x), [token("cp"), "ㄇㄛ".to_string()]);
    }
    let (with, without) = (listed(&mut e), listed(&mut p));
    let n = without.len();
    assert!(n >= 3 && without[n - 2..] == ["ai", "AI"], "premise: the short word's two items close the list: {without:?}");
    let mut want = vec!["CP摸".to_string()];
    want.extend(without.iter().cloned());
    assert_eq!(with, want);
    // Each of the three still does what it is for.
    assert_eq!(choose(&mut e, "CP摸").preedit, "CP摸");
    let mut e = eng();
    run(&mut e, "c p _ a i _");
    // The token keeps the table's writing of the English part once the table wrote it (docs/spec/english-mixed.md §5.7).
    assert_eq!(choose(&mut e, "AI").preedit, "CPAI");
}

/// The check set: every row of the shipped table, typed with Shift on the first letter, lists its writing first; the check set and
/// the table hold the same rows.
#[test]
fn every_check_set_word_is_listed() {
    let table = std::fs::read_to_string(root().join("data/lexicon/mixed-words.tsv")).unwrap();
    let check = std::fs::read_to_string(root().join("eval/dev/mixed-words/words.txt")).unwrap();
    let mut e = plain();
    assert!(e.set_mixed_words(&table), "the shipped table loads");
    let mut rows: Vec<String> = e.mixed_words().into_iter().map(|(en, r, w)| format!("{en}|{}|{w}", r.join(" "))).collect();
    rows.sort();
    let mut lines: Vec<String> = check.lines().map(String::from).collect();
    lines.sort();
    assert!(rows.len() >= 20 && rows == lines, "the check set is the table");
    for line in check.lines() {
        let f: Vec<&str> = line.split('|').collect();
        let (english, reading, writing) = (f[0], f[1], f[2]);
        e.reset(ResetMode::Discard);
        let mut script = format!("S-{}", &english[..1]);
        for c in english[1..].chars() {
            script.push_str(&format!(" {c}"));
        }
        script.push_str(" _");
        run(&mut e, &script);
        let u = units(&e);
        assert!(u.len() == 1 && u[0].strip_prefix(PUNCT_PREFIX).is_some_and(|t| t.eq_ignore_ascii_case(english)), "{line}: premise: one English token, got {u:?}");
        type_syls(&mut e, reading);
        assert_eq!(e.syllables().len(), 1 + reading.split(' ').count(), "{line}: premise: the syllables follow the token");
        let list = listed(&mut e);
        assert_eq!(list.first().map(String::as_str), Some(writing), "{line}: not the first item of {list:?}");
        // docs/spec/english-mixed.md §5.7: and the composition already shows it (the first way of typing: the token, then the reading).
        assert_eq!(run(&mut e, "End").preedit, writing, "{line}: token then reading");
        // The second way: every letter with Shift and the reading right behind, no Space (docs/spec/english-mixed.md §4.3).
        if english.bytes().all(|b| b.is_ascii_lowercase()) {
            e.reset(ResetMode::Discard);
            let caps: Vec<String> = english.chars().map(|c| format!("S-{c}")).collect();
            run(&mut e, &caps.join(" "));
            assert!(units(&e).is_empty(), "{line}: premise: the letters are an open run");
            assert_eq!(type_syls(&mut e, reading).preedit, writing, "{line}: letters then reading");
            assert_eq!(units(&e).len(), 1 + reading.split(' ').count(), "{line}");
        }
    }
}

/// `Engine::new` reads the table from the data folder, as the App and the CLI get it; a folder without it has no table.
#[test]
fn the_table_comes_with_the_data_folder() {
    let e = Engine::new(&root().join("data/lexicon"), Layout::Standard).expect("the data folder loads");
    assert!(e.mixed_words_loaded() && e.mixed_words().len() >= 20);
    let dir = std::env::temp_dir().join(format!("shanjie-mixed-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    for f in std::fs::read_dir(root().join("data/lexicon")).unwrap().map(|f| f.unwrap()) {
        if f.file_name() != "mixed-words.tsv" {
            #[cfg(unix)]
            std::os::unix::fs::symlink(f.path(), dir.join(f.file_name())).unwrap();
            #[cfg(windows)]
            std::fs::copy(f.path(), dir.join(f.file_name())).unwrap();
        }
    }
    let e = Engine::new(&dir, Layout::Standard).expect("an engine builds without mixed-words.tsv");
    assert!(!e.mixed_words_loaded());
    // A table with a bad row: the engine builds and has no table.
    std::fs::write(dir.join("mixed-words.tsv"), "cp\tㄓˊ\tDP值\t1\n").unwrap();
    let e = Engine::new(&dir, Layout::Standard).expect("an engine builds with a bad mixed-words.tsv");
    assert!(!e.mixed_words_loaded());
    let _ = std::fs::remove_dir_all(&dir);
}

/// With a prediction row showing when Down is pressed, the mixed word is still the first item: that window carries no row items.
#[test]
fn the_mixed_word_is_first_with_a_prediction_row_showing() {
    let (mut e, mut p) = (eng(), plain());
    e.set_prediction(true).unwrap();
    p.set_prediction(true).unwrap();
    let row = run(&mut p, "c p _ 5 6").candidates;
    let o = run(&mut e, "c p _ 5 6");
    assert!(!row.is_empty() && o.candidates == row && o.selected.is_none(), "premise: the same prediction row is showing: {row:?}");
    let without = listed(&mut p);
    println!("row {row:?}\nwithout {:?}", &without[..without.len().min(12)]);
    assert!(without.len() > row.len() && without[..row.len()] == row[..], "premise: without the table the window starts with the row's items");
    let with = listed(&mut e);
    assert_eq!(with[0], "CP值");
    assert!(with.len() < without.len() + 1, "the row's items are not carried: {} against {}", with.len(), without.len());
    assert_eq!(choose(&mut e, "CP值").preedit, "CP值");
}

/// Key scheme 1 (rule 15d): the window opens with the word the composition shows selected; it goes right after the mixed word.
#[test]
fn key_scheme_1_keeps_the_mixed_word_first() {
    let mut e = eng();
    e.set_key_scheme(1).unwrap();
    let shown = run(&mut e, "c p _ 5 6").preedit;
    let o = run(&mut e, "Down");
    assert!(o.candidates[0] == "CP值" && o.selected == Some(1), "{:?} {:?}", o.candidates, o.selected);
    assert_eq!(format!("CP{}", o.candidates[1]), shown, "the selected item is the one shown");
    assert_eq!(run(&mut e, "Enter").preedit, shown, "Enter on the shown word changes nothing");
}

/// What a re-pick right after the mixed word is learned under (docs/spec/english-mixed.md §5.7): the decode starts a sentence after the
/// token, as after a mark, but the learning context is the text shown, and the mixed word ends in a Han character.
#[test]
fn the_learning_context_after_the_mixed_word() {
    let learned = |after_mixed: bool| {
        let mut e = eng();
        if after_mixed {
            run(&mut e, "c p _ 5 6");
            choose(&mut e, "CP值");
        } else {
            run(&mut e, "S-,");
        }
        let before = records(&e);
        let text = type_syls(&mut e, "ㄕˋ ㄕˊ").preedit;
        let shown: String = text.chars().rev().take(2).collect::<Vec<_>>().into_iter().rev().collect();
        let o = run(&mut e, "_");
        let other = items(&mut e, &o).into_iter().find(|w| *w != shown && w.chars().count() == 2).expect("another word of the two syllables");
        run(&mut e, "Esc");
        choose_span(&mut e, &other);
        run(&mut e, "Enter");
        let new: Vec<(String, String)> = records(&e).into_iter().filter(|r| !before.contains(r)).collect();
        (other, new)
    };
    let (w_mixed, mixed) = learned(true);
    let (w_mark, mark) = learned(false);
    assert_eq!(w_mixed, w_mark, "premise: the same character was picked in both");
    // After a mark the word is learned at the sentence start; after the mixed word, under its last Han character.
    assert!(mark.len() == 1 && mark[0].1 == w_mark && mark[0].0.starts_with('^'), "{mark:?}");
    assert_eq!(mixed, [("值".to_string(), w_mixed)]);
}

// ---- The letter words: the split at the closing key, the automatic mixed word, the run's row (docs/spec/english-mixed.md §4.3, §5.7, docs/spec/prediction.md §3.5) ----

/// A synthetic table: the tests below do not read the shipped rows' counts. `K歌` and `K金` tie on the count; `get不到` has two readings.
const LETTERS: &str = "t\tㄒㄩˋ\tT恤\t5\nt\tㄗˋ\tT字\t3\nk\tㄕㄨ\tK書\t9\nk\tㄍㄜ\tK歌\t7\nk\tㄐㄧㄣ\tK金\t7\nok\tㄅㄥ\tOK繃\t2\ncp\tㄓˊ\tCP值\t100\n\
                       get\tㄅㄨˊ ㄉㄠˋ\tget不到\t50\nget\tㄅㄨˋ ㄉㄠˋ\tget不到\t50\nhold\tㄅㄨˊ ㄓㄨˋ\thold不住\t8\nhold\tㄅㄨˋ ㄓㄨˋ\thold不住\t9\n";
/// The same without the rows a letter word needs (one letter, `ok`): what the table was before them.
const NO_LETTERS: &str = "cp\tㄓˊ\tCP值\t100\nget\tㄅㄨˊ ㄉㄠˋ\tget不到\t50\nget\tㄅㄨˋ ㄉㄠˋ\tget不到\t50\n";
fn letters() -> Engine {
    let mut e = plain();
    assert!(e.set_mixed_words(LETTERS));
    e
}
/// Every output of the script, key by key.
fn outputs(e: &mut Engine, script: &str) -> Vec<Output> {
    script.split_whitespace().map(|t| e.key(key_of(t)).unwrap()).collect()
}
fn same(a: &[Output], b: &[Output]) -> bool {
    a.len() == b.len() && a.iter().zip(b).all(|(x, y)| x == y)
}

/// Shift+K, ㄕㄨ, Space: the run splits at Space into the token `K` and the syllable, and the table's word shows.
#[test]
fn shift_letter_and_a_reading_split_at_space() {
    let mut p = plain();
    run(&mut p, "S-k g j _");
    assert_eq!(units(&p), [token("Kgj")], "premise: without the table the keys are one English token");
    let mut e = letters();
    let o = run(&mut e, "S-k g j _");
    assert_eq!(units(&e), [token("K"), "ㄕㄨ".to_string()]);
    assert!(o.preedit == "K書" && o.commit.is_empty() && e.english_split(), "{}", o.preedit);
    // Same-column keys replace each other, as in the Zhuyin replay: `e k e` is ㄍㄜ.
    let mut e = letters();
    assert_eq!(run(&mut e, "S-k e k e _").preedit, "K歌");
}

/// Shift+T, ㄒㄩˋ: the tone key splits (as it did), and the syllable is 恤, not what the decoder shows by itself.
#[test]
fn shift_letter_and_a_reading_split_at_a_tone_key() {
    let mut p = plain();
    let before = run(&mut p, "S-t v m 4").preedit;
    assert!(units(&p) == [token("T"), "ㄒㄩˋ".to_string()] && before != "T恤", "premise: the split is today's, the word is not: {before}");
    let mut e = letters();
    assert_eq!(run(&mut e, "S-t v m 4").preedit, "T恤");
    assert_eq!(units(&e), [token("T"), "ㄒㄩˋ".to_string()]);
}

/// A tail syllable of one symbol splits at the tone key when the table has the row (`T y 4`); without the row the digit goes on the run.
#[test]
fn a_one_symbol_syllable_splits_with_a_row() {
    let mut p = plain();
    let o = run(&mut p, "S-t y 4");
    assert!(units(&p).is_empty() && o.preedit == "Ty4" && o.english_run_utf16_len == 3, "premise: one symbol does not split");
    let mut e = letters();
    assert_eq!(run(&mut e, "S-t y 4").preedit, "T字");
    assert_eq!(units(&e), [token("T"), "ㄗˋ".to_string()]);
    // The same key with a syllable the table lacks for T is today's.
    let (mut e, mut p) = (letters(), plain());
    assert!(same(&outputs(&mut e, "S-t s 4"), &outputs(&mut p, "S-t s 4")));
}

/// Two letters: `O K 1 /` Space is OK繃.
#[test]
fn two_letters_and_a_reading() {
    let mut p = plain();
    run(&mut p, "S-o S-k 1 / _");
    assert_eq!(units(&p), [token("OK1/")], "premise");
    let mut e = letters();
    assert_eq!(run(&mut e, "S-o S-k 1 / _").preedit, "OK繃");
    assert_eq!(units(&e), [token("OK"), "ㄅㄥ".to_string()]);
}

/// The fixed Han part is never released (docs/spec/english-mixed.md §5.7): the syllables typed behind it decode apart from it, where a frozen word
/// of any other kind would merge with the next syllable into a lexicon word (docs/spec/keys-and-composition.md §6.4). ㄒㄩˋ ㄕˋ is 敘事 to the
/// decoder; behind `T` with the row, 恤 stays.
#[test]
fn the_han_part_is_not_released() {
    let mut p = plain();
    let free = run(&mut p, "S-t v m 4 g 4").preedit;
    assert_eq!(free, "T敘事", "premise: without the table the two syllables are one word");
    let mut e = letters();
    assert_eq!(run(&mut e, "S-t v m 4").preedit, "T恤", "premise: the table's word is fixed");
    assert_eq!(e.frozen_words(), [(1, 2, "恤".to_string())], "premise: as a frozen word");
    let o = run(&mut e, "g 4");
    assert!(o.preedit.starts_with("T恤") && o.preedit.chars().count() == 3 && o.preedit != free, "{}", o.preedit);
    assert_eq!(e.frozen_words(), [(1, 2, "恤".to_string())]);
    assert!(run(&mut e, "2 k 7").preedit.starts_with("T恤"));
    // Backspace and the syllable again: the word again; a new token starts over.
    run(&mut e, "BS BS BS");
    assert_eq!(units(&e), [token("T")]);
    assert_eq!(run(&mut e, "v m 4").preedit, "T恤");
    // The shipped rows that a following syllable took apart while the release applied: the fixed characters stay.
    let table = std::fs::read_to_string(root().join("data/lexicon/mixed-words.tsv")).unwrap();
    for (script, word) in [("S-k g j _ g 4", "K書"), ("S-t y 4 d j 4", "T字"), ("S-q 1 0 3 1 p 3", "Q版"), ("S-k g j _ 5 j 4 j 3", "K書")] {
        let mut e = plain();
        assert!(e.set_mixed_words(&table));
        let o = run(&mut e, script);
        assert!(o.preedit.starts_with(word) && o.preedit.chars().count() > 2, "{script}: {}", o.preedit);
    }
    // A longer row of the same letters takes the place of the shorter one when its reading is complete.
    let mut e = plain();
    assert!(e.set_mixed_words("cp\tㄓˊ\tCP直\t1\ncp\tㄓˊ ㄑㄧㄢˊ\tCP值錢\t1\n"));
    assert_eq!(run(&mut e, "c p _ 5 6").preedit, "CP直");
    assert_eq!(run(&mut e, "f u 0 6").preedit, "CP值錢");
}

/// The English part takes the table's writing and keeps it: `cp` is `CP` in CP值, and still when more is typed behind it.
#[test]
fn the_token_takes_the_tables_writing_and_keeps_it() {
    let mut e = letters();
    run(&mut e, "c p _");
    assert_eq!(units(&e), [token("cp")], "premise: the keys are an English token, its reading as typed");
    assert_eq!(run(&mut e, "5 6").preedit, "CP值");
    assert_eq!(units(&e), [token("cp"), "ㄓˊ".to_string()], "the reading stays as typed");
    let o = run(&mut e, "f u 0 6");
    assert!(o.preedit.starts_with("CP值") && o.preedit.chars().count() == 4, "值 stays and the next syllable decodes behind it: {}", o.preedit);
    assert_eq!(e.frozen_words(), [(1, 2, "值".to_string())]);
    assert!(run(&mut e, "Enter").commit.starts_with("CP值"));
    // The Shift letters of the user are written by the table too (`Beta版` for `BETA`).
    let mut e = plain();
    assert!(e.set_mixed_words("beta\tㄅㄢˇ\tBeta版\t1\n"));
    assert_eq!(run(&mut e, "S-b S-e S-t S-a 1 0 3").preedit, "Beta版");
}

/// English words typed with Shift are what they were: every output of `Kevin`, `OK`, `Windows` and Space is the same with the letter rows and with
/// the table without them (the prediction row is off here: with it on, `OK` shows the row of docs/spec/prediction.md §3.5).
#[test]
fn english_words_are_untouched() {
    for script in ["S-k e v i n _", "S-o S-k _", "S-w i n d o w s _", "S-k e v i n _ g j _", "S-o S-k _ _ s u 3"] {
        let (mut e, mut p) = (letters(), plain());
        assert!(p.set_mixed_words(NO_LETTERS));
        assert!(same(&outputs(&mut e, script), &outputs(&mut p, script)), "{script}");
    }
    let mut e = letters();
    run(&mut e, "S-k e v i n _");
    assert_eq!(units(&e), [token("Kevin")]);
}

/// A run typed in the ASCII state does not split, and neither does one a revert opened or one in the middle of the composition.
#[test]
fn only_a_shift_run_at_the_end_splits() {
    let mut e = letters();
    e.set_ascii(true).unwrap();
    for c in ['K', 'g', 'j'] {
        e.key(Key::ch(c, 0)).unwrap();
    }
    assert_eq!(run(&mut e, "_").preedit, "Kgj");
    assert_eq!(units(&e), [token("Kgj")], "the ASCII state's run stays as typed");
    // The cursor in the middle: the keys are today's.
    let (mut e, mut p) = (letters(), plain());
    for x in [&mut e, &mut p] {
        type_syls(x, "ㄋㄧˇ ㄏㄠˇ");
        run(x, "Left");
    }
    assert!(same(&outputs(&mut e, "S-k g j _"), &outputs(&mut p, "S-k g j _")));
    assert_eq!(units(&e), ["ㄋㄧˇ".to_string(), token("Kgj"), "ㄏㄠˇ".to_string()]);
    // The cursor in the middle, the token then the syllable: nothing is fixed.
    let (mut e, mut p) = (letters(), plain());
    for x in [&mut e, &mut p] {
        type_syls(x, "ㄋㄧˇ ㄏㄠˇ");
        run(x, "Left");
    }
    assert!(same(&outputs(&mut e, "S-t v m 4"), &outputs(&mut p, "S-t v m 4")));
    // No table: every key is today's (the engine `plain` is the one the premises above read).
    let mut p = plain();
    assert!(!p.mixed_words_loaded());
    assert_eq!(run(&mut p, "c p _ 5 6").preedit, "cp值");
    // English mixing off (key scheme 1 makes the token `T` from Shift+T on a composition): the table fixes nothing.
    let (mut e, mut p) = (letters(), plain());
    for x in [&mut e, &mut p] {
        x.set_key_scheme(1).unwrap();
        type_syls(x, "ㄋㄧˇ");
        x.set_english(false).unwrap();
    }
    assert!(same(&outputs(&mut e, "S-t v m 4"), &outputs(&mut p, "S-t v m 4")));
    assert_eq!(units(&e), ["ㄋㄧˇ".to_string(), token("T"), "ㄒㄩˋ".to_string()], "premise: the token and the row's reading");
}

/// Keys the table has no row for give the outputs of the engine without a table, key by key.
#[test]
fn without_a_hit_every_key_is_as_without_the_table() {
    for script in ["S-t g j _", "S-k v m 4", "S-z g j _", "c p _ 5 4", "S-g S-e S-t _ 1 j 6 2 k 7", "s u 3 c l 3 S-k BS BS", "S-k g BS j _"] {
        let (mut e, mut p) = (letters(), plain());
        e.set_prediction(false).unwrap();
        assert!(same(&outputs(&mut e, script), &outputs(&mut p, script)), "{script}");
    }
}

/// Choosing another word for the Han part: only the Han part changes, and the table does not put its word back.
#[test]
fn another_word_stays() {
    let mut e = letters();
    assert_eq!(run(&mut e, "S-t v m 4").preedit, "T恤", "premise");
    let list = listed(&mut e);
    assert!(list[0] == "T恤" && list.len() > 2, "the list is as before: the mixed word first: {list:?}");
    let other = list.iter().find(|w| w.chars().count() == 1 && *w != "恤").unwrap().clone();
    assert_eq!(choose(&mut e, &other).preedit, format!("T{other}"));
    assert_eq!(run(&mut e, "2 k 7").preedit, format!("T{other}的"));
    // The syllable typed again behind the same token is the decoder's.
    let mut p = plain();
    let today = run(&mut p, "S-t v m 4").preedit;
    assert_eq!(run(&mut e, "BS BS v m 4").preedit, today);
    // The English part stays the table's when only the Han part is chosen again.
    let mut e = letters();
    run(&mut e, "c p _ 5 6");
    let list = listed(&mut e);
    let other = list.iter().find(|w| w.chars().count() == 1 && *w != "值").unwrap().clone();
    assert_eq!(choose(&mut e, &other).preedit, format!("CP{other}"));
}

/// Backspace takes one unit at a time; the token goes back to what was typed only when Backspace reopens it.
#[test]
fn backspace_goes_unit_by_unit() {
    let mut e = letters();
    run(&mut e, "S-t v m 4");
    assert_eq!(run(&mut e, "BS").preedit, "T");
    assert_eq!(units(&e), [token("T")]);
    assert_eq!(run(&mut e, "v m 4").preedit, "T恤", "typed again, the word is back");
    run(&mut e, "BS BS");
    assert!(is_empty(&mut e), "a one-letter token goes whole");
    let mut e = letters();
    run(&mut e, "c p _ 5 6");
    let o = run(&mut e, "BS");
    assert!(o.preedit == "CP" && units(&e) == [token("cp")], "the token keeps the table's writing: {}", o.preedit);
    let o = run(&mut e, "BS");
    assert!(o.preedit == "c" && o.english_run_utf16_len == 1 && units(&e).is_empty(), "reopened as typed: {}", o.preedit);
    assert_eq!(run(&mut e, "p _").preedit, "cp");
    // Two syllables: the first alone is no row, the second completes it; Backspace and the syllable again complete it again.
    let mut e = letters();
    run(&mut e, "S-g e t _");
    let o = type_syls(&mut e, "ㄅㄨˊ");
    assert!(o.preedit.starts_with("Get") && e.frozen_words().is_empty(), "premise: one syllable of two is not fixed: {}", o.preedit);
    assert_eq!(type_syls(&mut e, "ㄉㄠˋ").preedit, "get不到");
    run(&mut e, "BS");
    assert_eq!(type_syls(&mut e, "ㄉㄠˋ").preedit, "get不到");
    // Arrows take the token whole.
    let o = run(&mut e, "Left Left Left");
    assert!(o.cursor_utf16 == 0 && o.preedit == "get不到");
}

/// The automatic word is never learned.
#[test]
fn the_automatic_word_is_not_learned() {
    let mut e = letters();
    let before = records(&e);
    run(&mut e, "S-k g j _");
    assert_eq!(run(&mut e, "Enter").commit, "K書");
    run(&mut e, "c p _ 5 6 f u 0 6");
    assert!(run(&mut e, "Enter").commit.starts_with("CP值"));
    assert_eq!(records(&e), before);
    // Nor in a commit that learns something else: a re-pick behind the automatic word is recorded, the word is not.
    run(&mut e, "S-k g j _ 2 k 7");
    let text = type_syls(&mut e, "ㄕˋ ㄕˊ").preedit;
    assert!(text.starts_with("K書的"), "premise: 書 stays fixed: {text}");
    let shown: String = text.chars().skip(3).collect();
    let o = run(&mut e, "_");
    let other = items(&mut e, &o).into_iter().find(|w| *w != shown && w.chars().count() == 2).expect("another word of the two syllables");
    run(&mut e, "Esc");
    choose_span(&mut e, &other);
    assert_eq!(run(&mut e, "Enter").commit, format!("K書的{other}"));
    let new: Vec<(String, String)> = records(&e).into_iter().filter(|r| !before.contains(r)).collect();
    assert!(new.len() == 1 && new[0].1 == other && !new[0].0.starts_with("^en:"), "premise: the re-pick is learned, nothing under an en key: {new:?}");
}

/// The whole path of the App's engine (`Engine::new`: the digit table is read, so `5 6` goes through the digit layer) gives the same words.
#[test]
fn the_digit_layer_closes_the_syllable_too() {
    let mut e = Engine::new(&root().join("data/lexicon"), Layout::Standard).unwrap();
    e.load_lm(&root().join("data/lm/bigram.sjlm")).unwrap();
    e.set_english(true).unwrap();
    assert!(e.set_mixed_words(LETTERS));
    assert_eq!(run(&mut e, "c p _ 5 6").preedit, "CP值");
    e.reset(ResetMode::Discard);
    assert_eq!(run(&mut e, "S-c S-p 5 6").preedit, "CP值");
    e.reset(ResetMode::Discard);
    assert_eq!(run(&mut e, "S-k g j _").preedit, "K書");
}

// ---- The row of an open English run (docs/spec/prediction.md §3.5) and its learning (docs/spec/learning.md §4.3) ----

/// The row switch on, the English completions off (the test hook): the mixed words of the row alone.
fn row_engine() -> Engine {
    let mut e = letters();
    e.set_prediction(true).unwrap();
    e.set_english_complete(false);
    e
}
/// As the App has it: the shipped table of mixed words, the shipped English table, the row with its completions.
fn app_row_engine() -> Engine {
    let mut e = plain();
    e.set_prediction(true).unwrap();
    assert!(e.set_mixed_words(&std::fs::read_to_string(root().join("data/lexicon/mixed-words.tsv")).unwrap()));
    assert!(e.load_auto_english(&root().join("data/lexicon/english.tsv")));
    e
}
fn en_records(e: &Engine) -> Vec<(String, String, String, f64)> {
    e.learner().records().iter().filter(|r| r.context.starts_with("^en:")).map(|r| (r.context.clone(), r.reading.join(" "), r.word.clone(), r.weight)).collect()
}

/// Shift+K: the row is the writings of the K rows, by count, then by writing; the hint's place is the row's (the output has candidates and
/// the run's range).
#[test]
fn the_run_row_lists_the_rows_by_count() {
    let mut p = plain();
    p.set_prediction(true).unwrap();
    p.set_english_complete(false);
    let o = run(&mut p, "S-k");
    assert!(o.candidates.is_empty() && o.english_run_utf16_len == 1, "premise: without the table an open run has no mixed words");
    let mut e = row_engine();
    let o = run(&mut e, "S-k");
    assert_eq!(o.candidates, ["K書", "K歌", "K金"]);
    assert!(o.selected.is_none() && o.total == 3 && o.english_run_utf16_len == 1 && o.preedit == "K");
    // A lower-case letter or a Zhuyin key: the run is no longer the letters alone, the row goes.
    let o = run(&mut e, "e");
    assert!(o.candidates.is_empty() && o.preedit == "Ke" && o.english_run_utf16_len == 2);
    // Backspace to the letters again: the row is back.
    assert_eq!(run(&mut e, "BS").candidates, ["K書", "K歌", "K金"]);
    // Another upper-case letter: the rows of both letters, none here.
    assert!(run(&mut e, "S-t").candidates.is_empty());
    e.reset(ResetMode::Discard);
    assert_eq!(run(&mut e, "S-o S-k").candidates, ["OK繃"]);
}

/// One writing is listed once however many rows have it; choosing it takes the row with the largest count, and of equal counts the
/// smallest reading string.
#[test]
fn one_writing_is_listed_once() {
    let mut e = row_engine();
    let o = run(&mut e, "S-g S-e S-t");
    assert_eq!(o.candidates, ["get不到"]);
    let o = run(&mut e, "Tab Enter");
    assert!(o.preedit == "get不到" && o.commit.is_empty(), "{}", o.preedit);
    assert!("ㄅㄨˊ ㄉㄠˋ" < "ㄅㄨˋ ㄉㄠˋ", "premise: the smaller reading string");
    assert_eq!(units(&e), [token("GET"), "ㄅㄨˊ".to_string(), "ㄉㄠˋ".to_string()]);
    let mut e = row_engine();
    assert_eq!(run(&mut e, "S-h S-o S-l S-d").candidates, ["hold不住"]);
    run(&mut e, "Tab 1");
    assert_eq!(units(&e), [token("HOLD"), "ㄅㄨˋ".to_string(), "ㄓㄨˋ".to_string()], "the row with the larger count");
}

/// A row marked `nopredict` is left out of the run's row and is otherwise a row: the split at the closing key, the automatic word and the list's
/// first item are as without the mark. Of two rows with one writing, the unmarked one is still listed. The shipped table marks `A片`.
#[test]
fn a_nopredict_row_is_not_in_the_run_row() {
    const T: &str = "a\tㄆㄧㄢˋ\tA片\t9\tnopredict\na\tㄎㄚ\tA咖\t5\nget\tㄅㄨˊ ㄉㄠˋ\tget不到\t50\tnopredict\nget\tㄅㄨˋ ㄉㄠˋ\tget不到\t50\n";
    let unmarked = T.replace("\tnopredict", "");
    let make = |t: &str| {
        let mut e = plain();
        e.set_prediction(true).unwrap();
        e.set_english_complete(false);
        assert!(e.set_mixed_words(t) && e.mixed_words().len() == 4);
        e
    };
    assert_eq!(run(&mut make(&unmarked), "S-a").candidates, ["A片", "A咖"], "premise: without the mark the row leads by its count");
    let mut e = make(T);
    assert_eq!(run(&mut e, "S-a").candidates, ["A咖"]);
    let o = run(&mut e, "q u 0 4");
    assert!(o.preedit == "A片" && o.commit.is_empty(), "the automatic word: {}", o.preedit);
    assert_eq!(units(&e), [token("A"), "ㄆㄧㄢˋ".to_string()]);
    assert_eq!(listed(&mut e).first().map(String::as_str), Some("A片"), "the list's first item");
    let mut e = make(T);
    assert_eq!(run(&mut e, "S-g S-e S-t").candidates, ["get不到"]);
    run(&mut e, "Tab Enter");
    assert_eq!(units(&e), [token("GET"), "ㄅㄨˋ".to_string(), "ㄉㄠˋ".to_string()], "the unmarked row's reading");
    // The shipped table, as the App has it.
    let mut e = app_row_engine();
    let o = run(&mut e, "S-a");
    assert!(o.candidates.contains(&"A卡".to_string()) && !o.candidates.contains(&"A片".to_string()), "{:?}", o.candidates);
    assert_eq!(run(&mut e, "q u 0 4").preedit, "A片");
    assert_eq!(listed(&mut e).first().map(String::as_str), Some("A片"));
}

/// Choosing the first item (Tab, Enter), another by its digit, or by a click: the token, the row's syllables, the writing shown, nothing sent.
#[test]
fn choosing_from_the_run_row() {
    let mut e = row_engine();
    run(&mut e, "S-k");
    let o = run(&mut e, "Tab");
    assert!(o.selected == Some(0) && o.candidates.len() == 3 && o.preedit == "K");
    let o = run(&mut e, "Enter");
    assert!(o.preedit == "K書" && o.commit.is_empty() && o.english_run_utf16_len == 0 && o.cursor_utf16 == 2, "{}", o.preedit);
    assert_eq!(units(&e), [token("K"), "ㄕㄨ".to_string()]);
    assert_eq!(e.frozen_words(), [(1, 2, "書".to_string())], "fixed as the automatic word is");
    assert_eq!(run(&mut e, "Enter").commit, "K書");
    run(&mut e, "S-k Tab");
    assert_eq!(run(&mut e, "2").preedit, "K歌");
    assert_eq!(units(&e), [token("K"), "ㄍㄜ".to_string()]);
    e.reset(ResetMode::Discard);
    run(&mut e, "S-k");
    let o = e.pick(2).unwrap().expect("a click on the row");
    assert!(o.preedit == "K金" && o.commit.is_empty());
    // Not choosing: the keys are today's, and the row never changes what they do.
    let (mut e, mut p) = (row_engine(), plain());
    let (a, b) = (outputs(&mut e, "S-k e v i n _"), outputs(&mut p, "S-k e v i n _"));
    assert!(same(&a[1..], &b[1..]) && a[0].preedit == b[0].preedit && a[0].commit == b[0].commit);
}

/// Off, in the ASCII state, or with no row for the letters: every output is today's (no candidates: the shell shows the hint).
#[test]
fn no_run_row_without_its_conditions() {
    let (mut e, mut p) = (letters(), plain());
    assert!(same(&outputs(&mut e, "S-k S-o BS"), &outputs(&mut p, "S-k S-o BS")), "the row switch is off in both");
    let (mut e, mut p) = (row_engine(), plain());
    p.set_prediction(true).unwrap();
    p.set_english_complete(false);
    assert!(same(&outputs(&mut e, "S-z S-z BS"), &outputs(&mut p, "S-z S-z BS")), "no row of the table for Z");
    for x in [&mut e, &mut p] {
        x.reset(ResetMode::Discard);
        x.set_ascii(true).unwrap();
    }
    let (a, b) = (e.key(Key::ch('K', 0)).unwrap(), p.key(Key::ch('K', 0)).unwrap());
    assert!(a == b && a.candidates.is_empty() && a.english_run_utf16_len == 1, "the ASCII state's run");
    // A setting that ends the run takes the row with it.
    let mut e = row_engine();
    assert_eq!(run(&mut e, "S-k").candidates, ["K書", "K歌", "K金"], "premise");
    let o = e.set_english(false).unwrap();
    assert!(o.candidates.is_empty() && o.preedit == "K" && o.english_run_utf16_len == 0 && units(&e) == [token("K")]);
    // The cursor not at the end: no row.
    let mut e = row_engine();
    type_syls(&mut e, "ㄋㄧˇ ㄏㄠˇ");
    assert!(run(&mut e, "Left S-k").candidates.is_empty());
}

/// With learning on, a chosen item that is sent as chosen is recorded under `^en:` + the letters, weight 1; the next row has it first even with
/// the smaller count. Learning off, Esc, a changed word: nothing is recorded. Clearing puts the row back in count order.
#[cfg(unix)] // `learning_open` (docs/spec/windows-tip.md §2.5)
#[test]
fn a_chosen_item_is_learned_under_the_en_key() {
    let dir = std::env::temp_dir().join(format!("shanjie-mixed-learn-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    let file = dir.join("learning.tsv");
    let mut e = row_engine();
    e.learning_open(&dir).unwrap();
    let lines = |f: &Path| -> Vec<String> { std::fs::read_to_string(f).map(|t| t.lines().skip(1).map(String::from).collect()).unwrap_or_default() };
    assert!(lines(&file).is_empty() && en_records(&e).is_empty(), "premise: nothing learned yet");
    // Learning off at the pick, or at the commit: nothing.
    e.set_learning(false);
    run(&mut e, "S-k Tab 2");
    assert_eq!(run(&mut e, "Enter").commit, "K歌");
    e.set_learning(true);
    run(&mut e, "S-k Tab 2");
    e.set_learning(false);
    assert_eq!(run(&mut e, "Enter").commit, "K歌");
    e.set_learning(true);
    assert!(lines(&file).is_empty() && en_records(&e).is_empty(), "learning off: the file and the memory are as they were");
    // Esc, a reset, and a word changed before the commit: nothing.
    run(&mut e, "S-k Tab 2 Esc");
    assert!(is_empty(&mut e));
    run(&mut e, "S-k Tab 2");
    e.reset(ResetMode::Commit);
    run(&mut e, "S-k Tab 2");
    let other = listed(&mut e).into_iter().find(|w| w.chars().count() == 1 && w != "歌").unwrap();
    choose(&mut e, &other);
    assert_eq!(run(&mut e, "Enter").commit, format!("K{other}"));
    run(&mut e, "S-k Tab 2 BS");
    assert_eq!(run(&mut e, "Enter").commit, "K");
    // Deleted and typed again: the word stands again, but it is no longer the pick.
    assert_eq!(run(&mut e, "S-k Tab 2 BS e k _").preedit, "K歌");
    assert_eq!(run(&mut e, "Enter").commit, "K歌");
    assert!(lines(&file).is_empty() && en_records(&e).is_empty(), "Esc, reset, another word, a deleted word: nothing recorded");
    // Chosen and sent.
    assert_eq!(run(&mut e, "S-k").candidates, ["K書", "K歌", "K金"], "premise: the count order");
    run(&mut e, "Tab 2");
    assert_eq!(run(&mut e, "Enter").commit, "K歌");
    assert_eq!(en_records(&e), [("^en:k".to_string(), "ㄍㄜ".to_string(), "K歌".to_string(), 1.0)]);
    assert_eq!(lines(&file), [format!("^en:k\tㄍㄜ\tK歌\t1\t{DAY}")]);
    assert_eq!(records(&e).len(), 1, "nothing else: no global record, nothing halved");
    assert_eq!(run(&mut e, "S-k").candidates, ["K歌", "K書", "K金"], "the learned item first, the smaller count notwithstanding");
    run(&mut e, "Esc");
    // The record is read back by a new engine, and pruning keeps it as it is.
    let mut again = row_engine();
    again.learning_open(&dir).unwrap();
    assert_eq!(en_records(&again), en_records(&e));
    assert_eq!(run(&mut again, "S-k").candidates, ["K歌", "K書", "K金"]);
    // Only this row reads it: typing ㄍㄜ alone decodes, lists and predicts as with no record.
    let mut none = row_engine();
    for script in ["e k _", "e k _ 2 k 7", "e k", "s u 3 e k _"] {
        for x in [&mut e, &mut none] {
            x.reset(ResetMode::Discard);
        }
        assert!(same(&outputs(&mut e, script), &outputs(&mut none, script)), "{script}");
        assert_eq!(listed(&mut e), listed(&mut none), "{script}");
    }
    e.reset(ResetMode::Discard);
    // Clearing forgets it: the count order again.
    e.learning_clear().unwrap();
    assert!(en_records(&e).is_empty());
    assert_eq!(run(&mut e, "S-k").candidates, ["K書", "K歌", "K金"]);
    let _ = std::fs::remove_dir_all(&dir);
}

/// ⌘⌫ on an entered item of the run's row forgets its record like any other.
#[test]
fn forgetting_an_item_of_the_run_row() {
    let mut e = row_engine();
    run(&mut e, "S-k Tab 2 Enter");
    assert_eq!(en_records(&e).len(), 1, "premise: learned");
    let o = run(&mut e, "S-k Tab");
    assert!(o.candidates[0] == "K歌" && o.selected == Some(0));
    let o = e.key(key_of(FORGET)).unwrap();
    assert!(en_records(&e).is_empty());
    assert_eq!(o.candidates, ["K書", "K歌", "K金"]);
}

// ---- The check when the table is built (docs/spec/english-mixed.md §5.7): no English word, typed with Shift, runs into a row ----

/// The words of `words` that, typed with the first letter or every letter in upper case and then Space, come out otherwise with `table` than
/// without one: the split of docs/spec/english-mixed.md §4.3 took them (the engines here are the lexicon alone: the split reads nothing else).
fn collisions(table: &str, words: &[String]) -> Vec<String> {
    let s = shared();
    let make = |t: Option<&str>| {
        let mut e = Engine::with_lexicon(s.lex.clone(), Layout::Standard);
        e.set_prediction(false).unwrap();
        e.set_english(true).unwrap();
        assert!(t.is_none_or(|t| e.set_mixed_words(t)), "the table loads");
        e
    };
    let (mut with, mut without) = (make(Some(table)), make(None));
    let mut hit = Vec::new();
    for w in words {
        let lower = w.to_ascii_lowercase();
        let capital = format!("{}{}", lower[..1].to_ascii_uppercase(), &lower[1..]);
        for form in [capital, lower.to_ascii_uppercase()] {
            let typed = |e: &mut Engine| {
                e.reset(ResetMode::Discard);
                for c in form.chars() {
                    e.key(Key::ch(c.to_ascii_lowercase(), if c.is_ascii_uppercase() { MOD_SHIFT } else { 0 })).unwrap();
                }
                e.key(Key::new(KeyKind::Space)).unwrap();
                e.syllables().to_vec()
            };
            if typed(&mut with) != typed(&mut without) {
                hit.push(form);
            }
        }
    }
    hit
}

#[test]
fn the_table_check_finds_a_word_that_replays_into_a_row() {
    assert_eq!(collisions("k\tㄍㄜ\tK歌\t1\n", &["keke".to_string(), "kevin".to_string()]), ["Keke"], "`Keke` and Space replays to ㄍㄜ");
    assert!(collisions("k\tㄕㄨ\tK書\t1\n", &["keke".to_string()]).is_empty());
}

/// The shipped table against the English words the core knows (the `E` rows of `english.tsv` that start with a letter, and the two lists of official
/// writings), row by row: the (row, word) pairs that run into each other are exactly the list of docs/spec/english-mixed.md §14, and so is whether the
/// word is still to be had (typed with Shift on its first letter and then Space, the window Down opens lists it).
#[test]
fn the_words_that_run_into_shipped_rows_are_the_documented_ones() {
    let read = |p: &str| std::fs::read_to_string(root().join(p)).unwrap();
    let table = read("data/lexicon/mixed-words.tsv");
    let mut words: Vec<String> =
        read("data/lexicon/english.tsv").lines().filter_map(|l| l.strip_prefix("E\t")).map(|l| l.split('\t').next().unwrap().to_string()).collect();
    for p in ["data/english-extra.txt", "data/lexicon/names-english.txt"] {
        words.extend(read(p).lines().filter(|l| !l.is_empty() && !l.starts_with('#')).map(String::from));
    }
    words.retain(|w| w.as_bytes()[0].is_ascii_alphabetic() && w.is_ascii());
    assert!(words.len() > 30_000, "premise: the word lists are read ({})", words.len());
    let mut e = Engine::with_lexicon(shared().lex.clone(), Layout::Standard);
    e.set_prediction(false).unwrap();
    e.set_english(true).unwrap();
    assert!(e.set_mixed_words(&table) && e.load_auto_english(&root().join("data/lexicon/english.tsv")));
    let mut got: Vec<String> = Vec::new();
    for row in table.lines().filter(|l| !l.starts_with('#')) {
        let f: Vec<&str> = row.split('\t').collect();
        let first = row.as_bytes()[0];
        let of_letter: Vec<String> = words.iter().filter(|w| w.as_bytes()[0].to_ascii_lowercase() == first).cloned().collect();
        for form in collisions(&format!("{row}\n"), &of_letter) {
            e.reset(ResetMode::Discard);
            for c in form.chars() {
                e.key(Key::ch(c.to_ascii_lowercase(), if c.is_ascii_uppercase() { MOD_SHIFT } else { 0 })).unwrap();
            }
            e.key(Key::new(KeyKind::Space)).unwrap();
            let kept = listed(&mut e).contains(&form);
            got.push(format!("{} {} {}|{form}|{}", f[0], f[1], f[2], if kept { "是" } else { "否" }));
        }
    }
    got.sort();
    // The list of §14: table lines `| `row` | word | 是 or 否 ... |` under the marker line.
    let spec = read("docs/spec/english-mixed.md");
    let part = spec.split("<!-- mixed-collisions -->").nth(1).expect("the marker of the list in docs/spec/english-mixed.md §14");
    let mut want: Vec<String> = part
        .lines()
        .skip(1)
        .take_while(|l| l.trim_start().starts_with('|'))
        .filter(|l| l.contains('`'))
        .map(|l| {
            let c: Vec<&str> = l.trim().trim_matches('|').split('|').map(str::trim).collect();
            format!("{}|{}|{}", c[0].trim_matches('`'), c[1], c[2].chars().next().unwrap())
        })
        .collect();
    want.sort();
    assert!(got.len() >= 6, "premise: the shipped table has rows English words run into");
    assert_eq!(got, want, "the list of docs/spec/english-mixed.md §14 is not what the check finds");
}

/// The English word a split took apart is in the window (docs/spec/english-mixed.md §4.3): behind the mixed word, or first when the row has more
/// syllables to come; choosing it gives the word back as one token that the table leaves alone. The three are shipped rows that English words of
/// `english.tsv` run into: `Karaoke` and Space replays to ㄍㄜ (K歌), `Nh4` closes ㄘˋ with its own `4` (N次), `Cm3` closes ㄩˇ with its `3`
/// (the first syllable of C語言).
#[test]
fn the_word_a_split_took_apart_is_listed() {
    let mut e = app_row_engine();
    e.set_prediction(false).unwrap();
    let o = run(&mut e, "S-k a r a o k e _");
    assert!(o.preedit == "K歌" && units(&e) == [token("K"), "ㄍㄜ".to_string()], "premise: the split took the word: {}", o.preedit);
    let list = listed(&mut e);
    assert!(list[0] == "K歌" && list[1] == "Karaoke", "{:?}", &list[..3]);
    let o = choose(&mut e, "Karaoke");
    assert!(o.preedit == "Karaoke" && o.commit.is_empty() && units(&e) == [token("Karaoke")]);
    let before = records(&e);
    assert_eq!(run(&mut e, "e k _").preedit.chars().take(7).collect::<String>(), "Karaoke");
    assert!(e.frozen_words().is_empty(), "the table applies behind no such token");
    assert_eq!(run(&mut e, "Enter").commit.chars().take(7).collect::<String>(), "Karaoke");
    assert_eq!(records(&e), before, "not learned");
    // A tone key closed the run: the key is part of the word.
    let o = run(&mut e, "S-n h 4");
    assert!(o.preedit == "N次" && units(&e) == [token("N"), "ㄘˋ".to_string()], "premise: {}", o.preedit);
    let list = listed(&mut e);
    assert!(list[0] == "N次" && list[1] == "Nh4", "{:?}", &list[..3]);
    assert_eq!(choose(&mut e, "Nh4").preedit, "Nh4");
    assert_eq!(units(&e), [token("Nh4")]);
    e.reset(ResetMode::Discard);
    // The first syllable of a longer row: no mixed word yet, the English word is the first item.
    let o = run(&mut e, "S-c m 3");
    assert!(units(&e) == [token("C"), "ㄩˇ".to_string()] && e.frozen_words().is_empty(), "premise: split, nothing fixed: {}", o.preedit);
    assert_eq!(listed(&mut e)[0], "Cm3");
    // Gone once another syllable is typed, or the cursor has left.
    run(&mut e, "u 0 6");
    assert!(!listed(&mut e).contains(&"Cm3".to_string()));
    e.reset(ResetMode::Discard);
    run(&mut e, "S-k a r a o k e _ Left Right");
    assert!(!listed(&mut e).contains(&"Karaoke".to_string()));
    // Keys that are no English word leave nothing: `Kgj` is none.
    e.reset(ResetMode::Discard);
    run(&mut e, "S-k g j _");
    let list = listed(&mut e);
    assert!(list[0] == "K書" && !list.iter().any(|w| w.eq_ignore_ascii_case("kgj")), "{:?}", &list[..3]);
}

/// English completions (docs/spec/prediction.md §3.5): behind the mixed words of the letters, the English words that start with the run's letters, by
/// count; at least three of the nine places are theirs.
#[test]
fn the_run_row_completes_english_words() {
    let mut e = app_row_engine();
    let table = std::fs::read_to_string(root().join("data/lexicon/mixed-words.tsv")).unwrap();
    let k_rows = table.lines().filter(|l| l.starts_with("k\t")).count();
    let o = run(&mut e, "S-k");
    assert!(k_rows >= 2 && o.candidates.len() == 9 && o.selected.is_none() && o.english_run_utf16_len == 1, "{:?}", o.candidates);
    let (mixed, words) = o.candidates.split_at(k_rows.min(6));
    assert!(mixed.iter().all(|w| w.chars().any(|c| !c.is_ascii())) && words.len() >= 3, "the mixed words first: {:?}", o.candidates);
    assert!(words.iter().all(|w| w.starts_with('K') && w.is_ascii() && w.len() > 1), "then English words of K: {words:?}");
    assert!(words[0] == "Kd" && words[1] == "Kt", "by the count of the E rows (kd 24134, kt 18684): {words:?}");
    // A lower-case letter: the mixed words go, the completions of `Ke` stay; the typed letters are kept as typed.
    let o = run(&mut e, "e");
    assert!(!o.candidates.is_empty() && o.candidates.iter().all(|w| w.starts_with("Ke") && w.len() > 2), "{:?}", o.candidates);
    assert_eq!(o.candidates[0], "Kerr", "the most counted word of `ke` (kerr 8426)");
    assert!(run(&mut e, "v").candidates.contains(&"Kevin".to_string()));
    // `Kar...`: every item starts with what is typed; `Karaoke` is reached.
    e.reset(ResetMode::Discard);
    let o = run(&mut e, "S-k a r");
    assert!(o.candidates.len() >= 3 && o.candidates.iter().all(|w| w.starts_with("Kar")), "{:?}", o.candidates);
    assert!(run(&mut e, "a o").candidates.contains(&"Karaoke".to_string()));
    // Choosing one: the run is that word as an English token; nothing is sent, nothing learned.
    let before = records(&e);
    let o = run(&mut e, "Tab");
    let first = o.candidates[0].clone();
    let o = run(&mut e, "Enter");
    assert!(o.preedit == first && o.commit.is_empty() && o.english_run_utf16_len == 0 && o.candidates.is_empty(), "{}", o.preedit);
    assert_eq!(units(&e), [token(&first)]);
    assert_eq!(run(&mut e, "Enter").commit, first);
    assert_eq!(records(&e), before);
    // A click chooses too; ⌘⌫ on an entered completion forgets nothing and changes nothing.
    run(&mut e, "S-w");
    let o = run(&mut e, "Tab");
    assert!(!o.candidates.is_empty() && o.candidates.iter().all(|w| w.starts_with('W') && w.is_ascii()), "a letter without mixed words lists English only: {:?}", o.candidates);
    let again = e.key(key_of(FORGET)).unwrap();
    assert!(again.candidates == o.candidates && again.selected == o.selected);
    let word = o.candidates[1].clone();
    assert_eq!(e.pick(1).unwrap().unwrap().preedit, word);
    e.reset(ResetMode::Discard);
    // Two upper-case letters first: a word without an official writing is all upper case; one with an official writing keeps it.
    let o = run(&mut e, "S-o S-k");
    let ok_rows = table.lines().filter(|l| l.starts_with("ok\t")).count();
    assert!(o.candidates[..ok_rows].iter().all(|w| w.starts_with("OK") && !w.is_ascii()), "{:?}", o.candidates);
    assert!(o.candidates[ok_rows..].iter().all(|w| w.starts_with("OK") && *w == w.to_ascii_uppercase()) && o.candidates.contains(&"OKAY".to_string()), "{:?}", o.candidates);
    e.reset(ResetMode::Discard);
    assert!(run(&mut e, "S-c h a t g").candidates.contains(&"ChatGPT".to_string()), "the official writing");
    // No completion at all: no row (the shell shows its hint).
    e.reset(ResetMode::Discard);
    let o = run(&mut e, "S-z q x j z");
    assert!(o.candidates.is_empty() && o.english_run_utf16_len == 5);
    // Not in the ASCII state, not with the row off, not for a run a revert opened, not with a digit on the run.
    e.reset(ResetMode::Discard);
    assert!(run(&mut e, "S-k 8").candidates.is_empty());
    e.reset(ResetMode::Discard);
    assert!(run(&mut e, "s p e e").candidates.is_empty(), "premise: a revert's run `spee`: no row");
    e.reset(ResetMode::Discard);
    let mut p = plain();
    for x in [&mut e, &mut p] {
        x.set_prediction(false).unwrap();
    }
    assert!(same(&outputs(&mut e, "S-k e v i n _"), &outputs(&mut p, "S-k e v i n _")), "the row off: today's outputs");
    for x in [&mut e, &mut p] {
        x.set_prediction(true).unwrap();
        x.set_ascii(true).unwrap();
    }
    let (a, b) = (e.key(Key::ch('K', 0)).unwrap(), p.key(Key::ch('K', 0)).unwrap());
    assert!(a.candidates.is_empty() && a.preedit == b.preedit && a.english_run_utf16_len == 1, "the ASCII state's run has no row");
}

/// The places of the row: nine in all; the English completions get three when the mixed words alone would fill it.
#[test]
fn the_run_row_keeps_three_places_for_english() {
    let rows: String = "書歌金卡車站線路片".chars().zip(["ㄕㄨ", "ㄍㄜ", "ㄐㄧㄣ", "ㄎㄚˇ", "ㄔㄜ", "ㄓㄢˋ", "ㄒㄧㄢˋ", "ㄌㄨˋ", "ㄆㄧㄢˋ"]).enumerate().map(|(i, (c, r))| format!("k\t{r}\tK{c}\t{}\n", 100 - i)).collect();
    let mut e = plain();
    e.set_prediction(true).unwrap();
    assert!(e.set_mixed_words(&rows) && e.load_auto_english(&root().join("data/lexicon/english.tsv")));
    let o = run(&mut e, "S-k");
    assert_eq!(o.candidates[..6], ["K書", "K歌", "K金", "K卡", "K車", "K站"], "six of the nine mixed words");
    assert!(o.candidates.len() == 9 && o.candidates[6..].iter().all(|w| w.is_ascii()), "{:?}", o.candidates);
    e.set_english_complete(false);
    e.reset(ResetMode::Discard);
    assert_eq!(run(&mut e, "S-k").candidates.len(), 9, "without completions all nine are mixed words");
}

/// docs/spec/prediction.md §3.5, the cost of the completions per key: the 200 most counted words of `english.tsv`, each typed with Shift on its first
/// letter, the other letters, Space, then a reset, on the App's engine. Five rounds, in each the completions on and off in turn; the p95 of the key
/// time with them on is not more than 1 ms above the p95 with them off (the medians of the rounds). macOS only: the CI job that measures.
#[cfg(target_os = "macos")]
#[test]
fn timing_english_completions() {
    use std::time::{Duration, Instant};
    let text = std::fs::read_to_string(root().join("data/lexicon/english.tsv")).unwrap();
    let mut words: Vec<(u64, &str)> = text
        .lines()
        .filter_map(|l| l.strip_prefix("E\t"))
        .filter_map(|l| l.split_once('\t'))
        .filter(|(w, _)| w.bytes().all(|b| b.is_ascii_lowercase()))
        .map(|(w, n)| (n.parse().unwrap(), w))
        .collect();
    words.sort_by(|a, b| b.0.cmp(&a.0).then(a.1.cmp(b.1)));
    words.truncate(200);
    assert_eq!(words.len(), 200);
    let mut e = Engine::new(&root().join("data/lexicon"), Layout::Standard).unwrap();
    e.load_lm(&root().join("data/lm/bigram.sjlm")).unwrap();
    e.set_english(true).unwrap();
    assert!(e.mixed_words_loaded() && e.auto_table_loaded(), "premise: the App's tables");
    let pass = |e: &mut Engine, on: bool| -> (Duration, usize) {
        e.set_english_complete(on);
        let (mut times, mut rows) = (Vec::new(), 0);
        for (_, w) in &words {
            let mut keys: Vec<Key> = w.chars().enumerate().map(|(i, c)| Key::ch(c, if i == 0 { MOD_SHIFT } else { 0 })).collect();
            keys.push(Key::new(KeyKind::Space));
            for k in keys {
                let t = Instant::now();
                let o = e.key(k).unwrap();
                times.push(t.elapsed());
                rows += usize::from(!o.candidates.is_empty() && o.english_run_utf16_len > 0);
            }
            e.reset(ResetMode::Discard);
        }
        times.sort();
        (times[times.len() * 95 / 100], rows)
    };
    pass(&mut e, true); // warm: the word list is built at the first use
    let (mut on, mut off) = (Vec::new(), Vec::new());
    for _ in 0..5 {
        let (t, rows) = pass(&mut e, true);
        assert!(rows > 400, "premise: the keys show rows with the completions on ({rows})");
        on.push(t);
        off.push(pass(&mut e, false).0);
    }
    on.sort();
    off.sort();
    let (on, off) = (on[2], off[2]);
    println!("english completions: key p95 {:.3} ms on, {:.3} ms off", on.as_secs_f64() * 1e3, off.as_secs_f64() * 1e3);
    assert!(on <= off + Duration::from_millis(1), "the completions add more than 1 ms to the key p95");
}
