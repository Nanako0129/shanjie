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
        "c\tㄓˊ\tC值\t100\n",                       // one letter
        "cp\tㄓㄓ\tCP值\t100\n",                    // not a syllable of the lexicon
        "cp\tㄓˊ ㄓˊ ㄓˊ ㄓˊ ㄓˊ\tCP值值值值值\t100\n", // five syllables
        "cp\tㄓˊ\tCP值\t0\n",                       // the count is not positive
        "cp\tㄓˊ\tCP值\n",                          // three fields
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
    assert_eq!(choose(&mut e, "AI").preedit, "cpAI");
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
    assert_eq!(format!("cp{}", o.candidates[1]), shown, "the selected item is the one shown");
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
