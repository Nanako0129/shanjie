//! English before Chinese (docs/contracts/english-next.md 2 and 3.4): the engine on the real lexicon and model with a small test table.
//! The key sequence is the contract's: Shift+J, o, e, Space (the run ends and the Space is eaten), g (ㄕ). Key script: whitespace separated, `_` Space, `S-x` Shift+x, a
//! single character is that keycap.
use core::engine::*;
use core::english_next::EnglishTable;
use core::lm::{CappedLexicon, Lm};
use core::Lexicon;
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}

/// 是要對決 is not in the shipped lexicon until the names-creators contract lands, so the test brings it as an evaluation-only extra
/// overlay row (`load_lexicon_extra_detailed`, the same path as the CLI's `--extra-overlay`).
const EXTRA: &str = "ㄨㄛˇ-ㄛ-ㄎㄜ\t我OK\t-4.0\ttest\n";

/// joe: 是要對決 40, 說 30, 水 10 and 不在詞庫的詞 90 (a table word the lexicon does not have: it must never show).
/// N(joe) 200 is larger than the sum of the rows, as in a real table (the rest followed with a word below the cut).
const TABLE: &str = "# test table\nE\tabc\t10\nE\tjoe\t200\nW\tabc\t水\t7\nW\tjoe\t不在詞庫的詞\t90\nW\tjoe\t是要對決\t40\nW\tjoe\t水\t10\nW\tjoe\t說\t30\nE\tok\t50\nW\tok\t水\t40\n";

struct Shared {
    lex: Arc<Lexicon>,
    lm: Arc<Lm>,
    capped: Arc<CappedLexicon>,
    table: Arc<EnglishTable>,
    digits: Arc<core::digits::DigitTable>,
}

fn shared() -> &'static Shared {
    static S: OnceLock<Shared> = OnceLock::new();
    S.get_or_init(|| {
        let lm_file = root().join("data/lm/bigram.sjlm");
        assert!(lm_file.exists(), "data/lm/bigram.sjlm is missing: gh release download model-v6 -R Nanako0129/shanjie -p bigram.sjlm -D data/lm");
        let lm = Lm::load(&lm_file).unwrap();
        let dir = root().join("data/lexicon");
        let extra = std::env::temp_dir().join(format!("shanjie-english-next-extra-{}.tsv", std::process::id()));
        std::fs::write(&extra, EXTRA).unwrap();
        let (lex, pack_text) = load_lexicon_extra_detailed(&dir, None, Some(&extra), true).unwrap();
        std::fs::remove_file(&extra).unwrap();
        let overlay = capping_overlay(&dir, &pack_text).unwrap();
        let capped = Arc::new(CappedLexicon::new(lex.clone(), &overlay, &lm, None).unwrap());
        let digits = Arc::new(core::digits::DigitTable::parse(&std::fs::read_to_string(dir.join("digits.tsv")).unwrap()).unwrap());
        Shared { lex, lm: Arc::new(lm), capped, table: Arc::new(EnglishTable::parse(TABLE).unwrap()), digits }
    })
}

/// Standard layout, the model and the switch on; the table (`with_table`) and the digit table (`with_digits`).
fn make(with_table: bool, with_digits: bool) -> Engine {
    let s = shared();
    let mut e = Engine::with_lexicon(s.lex.clone(), Layout::Standard);
    e.set_lm(s.lm.clone(), s.capped.clone());
    e.set_english(true).unwrap();
    if with_table {
        e.set_english_next(Some(s.table.clone()));
    }
    if with_digits {
        e.set_digit_table(Some(s.digits.clone()));
    }
    e
}

fn key_of(tok: &str) -> Key {
    match tok {
        "_" => Key::new(KeyKind::Space),
        "Enter" => Key::new(KeyKind::Enter),
        "Esc" => Key::new(KeyKind::Esc),
        t if t.starts_with("S-") => Key::ch(t[2..].chars().next().unwrap(), MOD_SHIFT),
        t => Key::ch(t.chars().next().unwrap(), 0),
    }
}
/// Every output of the script.
fn run(e: &mut Engine, script: &str) -> Vec<Output> {
    script.split_whitespace().map(|t| e.key(key_of(t)).unwrap()).collect()
}
/// The passive prediction row of the last output.
fn row(outs: &[Output]) -> Vec<String> {
    outs.last().unwrap().candidates.clone()
}
/// What decoding shows: preedit and commit of every key (the row is not part of it).
fn shown(outs: &[Output]) -> Vec<(String, String, u32)> {
    outs.iter().map(|o| (o.preedit.clone(), o.commit.clone(), o.cursor_utf16)).collect()
}

const JOE: &str = "S-j o e _";

#[test]
fn the_key_sequence_makes_an_english_token() {
    let mut e = make(true, false);
    run(&mut e, JOE);
    assert_eq!(e.digits_syls(), ["_punct_Joe"]);
    // Zhen-inline 1.3e: the Space that ended the run is eaten; a second Space is a unit of its own (2.1 allows both).
    run(&mut e, "_");
    assert_eq!(e.digits_syls(), ["_punct_Joe", "_punct_ "]);
}

#[test]
fn a_one_letter_token_is_no_english_word() {
    // Shift+J, Space makes the token `_punct_J`; contract 2.1 takes 2-32 letters only, so even a table row for `j` stays unused.
    let mut e = make(false, false);
    e.set_english_next(Some(Arc::new(EnglishTable::parse("E\tj\t100\nW\tj\t水\t50\n").unwrap())));
    let outs = run(&mut e, "S-j _");
    assert_eq!(e.digits_syls(), ["_punct_J"]);
    assert!(row(&outs).is_empty());
}

#[test]
fn the_row_behind_joe() {
    let mut e = make(true, false);
    // After the Space, before any Zhuyin: the table's words the lexicon has, best P(w | e) first; the word the lexicon lacks is skipped.
    let outs = run(&mut e, JOE);
    assert_eq!(row(&outs), ["是要對決", "說", "水"]);
    assert_eq!(outs.last().unwrap().selected, None);
    // The same behind the space unit.
    assert_eq!(row(&run(&mut e, "_")), ["是要對決", "說", "水"]);
    // With a Zhuyin key: the table's compatible words first, then the other compatible words as today.
    let mut e = make(true, false);
    let outs = run(&mut e, &format!("{JOE} g"));
    assert_eq!(e.digits_syls(), ["_punct_Joe"]);
    assert_eq!(outs.last().unwrap().preedit, "Joeㄕ");
    let r = row(&outs);
    assert!(r.len() <= 9);
    // Scored lam * log10 P(w | e) + (1 - lam) * lp, so a rare word (是要對決) sits behind common ones (說, 水); all three before the rest.
    let mut top = r[..3].to_vec();
    top.sort();
    assert_eq!(top, ["是要對決", "水", "說"]);
    let base = row(&run(&mut make(false, false), &format!("{JOE} g")));
    assert!(!base.contains(&"是要對決".to_string()));
    assert_eq!(base.len(), 9);
    // Tier two is today's row without the table's words, in today's order.
    let rest: Vec<&String> = base.iter().filter(|w| !["是要對決", "說", "水"].contains(&w.as_str())).collect();
    assert_eq!(r[3..].iter().collect::<Vec<_>>(), rest[..r.len() - 3]);
}

#[test]
fn no_table_or_no_row_for_the_word_is_today() {
    // Without the table: nothing right behind English (today).
    assert!(row(&run(&mut make(false, false), JOE)).is_empty());
    // A word the table has no row for: the same rows as an engine that never saw a table.
    let (mut a, mut b) = (make(true, false), make(false, false));
    for script in ["S-m a x _", "S-m a x _ g", "S-m a x _ _ g", "S-j _ g"] {
        assert_eq!(row(&run(&mut a, script)), row(&run(&mut b, script)), "{script}");
        run(&mut a, "Esc");
        run(&mut b, "Esc");
    }
    // A table that does not parse leaves the feature off.
}

#[test]
fn a_table_that_does_not_parse_is_an_error() {
    use core::english_next::ParseError::*;
    let bad = |t: &str| EnglishTable::parse(t).err();
    assert_eq!(bad("E\tjoe\n"), Some(BadRow(1)));
    assert_eq!(bad("# h\nE\tjoe\t0\n"), Some(BadRow(2)));
    assert_eq!(bad("E\tjoe\t5\nE\tjoe\t6\n"), Some(BadRow(2)));
    assert_eq!(bad("W\tjoe\tx\t1\n"), Some(BadRow(1)));                  // a W row needs its E row first
    assert_eq!(bad("E\tjoe\t5\nW\tjoe\tx\t0\n"), Some(BadRow(2)));        // a zero count
    assert_eq!(bad("E\tjoe\t5\nX\tjoe\n"), Some(BadRow(2)));
    assert_eq!(bad("E\tjoe\t5\nW\tjoe\tx\t3\nW\tjoe\ty\t3\n"), Some(TooSmall(1)));   // 6 > 5
    assert_eq!(bad("# nothing\n"), Some(Empty));
    // CRLF is read; a sum equal to N(e) is fine.
    let t = EnglishTable::parse("E\tjoe\t6\r\nW\tjoe\tx\t3\r\nW\tjoe\ty\t3\r\n").unwrap();
    assert!(t.get("joe").is_some() && t.get("jo").is_none());
    // An engine hook says why; a data directory with an invalid file does not build an engine (a missing file is fine).
    let dir = std::env::temp_dir().join(format!("shanjie-english-next-bad-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let f = dir.join("english-next.tsv");
    std::fs::write(&f, "E\tjoe\t5\nW\tjoe\tx\t0\n").unwrap();
    let mut e = make(false, false);
    assert!(e.load_english_next(&f).unwrap_err().contains("line 2"));
    assert!(e.load_english_next(&dir.join("missing.tsv")).unwrap_err().contains("cannot read"));
    let tmp = std::env::temp_dir().join(format!("shanjie-english-next-data-{}", std::process::id()));
    std::fs::create_dir_all(&tmp).unwrap();
    for name in std::fs::read_dir(root().join("data/lexicon")).unwrap().map(|e| e.unwrap().file_name()) {
        std::os::unix::fs::symlink(root().join("data/lexicon").join(&name), tmp.join(&name)).unwrap();
    }
    assert!(Engine::new(&tmp, Layout::Standard).is_ok());
    std::fs::copy(&f, tmp.join("english-next.tsv")).unwrap();
    assert!(Engine::new(&tmp, Layout::Standard).is_err());
    // Not UTF-8: there but unreadable, also a load failure (not a silent off).
    std::fs::write(tmp.join("english-next.tsv"), b"E\tjoe\t5\n\xff\xfe\n").unwrap();
    assert!(Engine::new(&tmp, Layout::Standard).is_err());
    std::fs::write(tmp.join("english-next.tsv"), TABLE).unwrap();
    assert!(Engine::new(&tmp, Layout::Standard).is_ok());
    std::fs::remove_dir_all(&tmp).unwrap();
    std::fs::remove_dir_all(&dir).unwrap();
}

#[test]
fn the_cache_follows_the_table() {
    // Table A, no table, table B with another follower: the empty-unit row shows B's word, never A's.
    let table = |w: &str| Arc::new(EnglishTable::parse(&format!("E\tjoe\t100\nW\tjoe\t{w}\t50\n")).unwrap());
    let mut e = make(false, false);
    e.set_english_next(Some(table("水")));
    assert_eq!(row(&run(&mut e, JOE)), ["水"]);
    e.set_english_next(None);
    e.reset(ResetMode::Discard);
    assert!(row(&run(&mut e, JOE)).is_empty());
    e.set_english_next(Some(table("說")));
    e.reset(ResetMode::Discard);
    assert_eq!(row(&run(&mut e, JOE)), ["說"]);
}

#[test]
fn a_lexicon_word_that_ends_in_letters_is_no_english_token() {
    // 我OK (the extra row) is a word of the lexicon; the table has "ok", but the unit before ㄕ is a syllable, not an English token.
    let script = "j i 3 i _ d k _ g";
    let (mut a, mut b) = (make(true, false), make(false, false));
    let (x, y) = (run(&mut a, script), run(&mut b, script));
    assert!(x[x.len() - 2].preedit.ends_with("OK"), "the word is on screen: {}", x[x.len() - 2].preedit);
    assert_eq!(row(&x), row(&y));
    assert_eq!(a.digits_syls(), b.digits_syls());
    // The same letters typed as an English token do use the table.
    assert_eq!(row(&run(&mut make(true, false), "S-o k _")), ["水"]);
}

#[test]
fn prediction_off_has_no_effect() {
    let mut e = make(true, false);
    e.set_prediction(false).unwrap();
    assert!(row(&run(&mut e, JOE)).is_empty());
    assert!(row(&run(&mut e, "g")).is_empty());
    e.set_prediction(true).unwrap();
    e.reset(ResetMode::Discard);
    assert!(row(&run(&mut e, &format!("{JOE} g"))).contains(&"是要對決".to_string()));
}

#[test]
fn the_digit_context_comes_first() {
    // Behind digits the english table changes nothing: the same rows and the same display as with the digit table alone.
    let (mut a, mut b) = (make(true, true), make(false, true));
    run(&mut a, JOE);
    run(&mut b, JOE);
    for t in "1 2 3 _ 5 g".split_whitespace() {
        let (x, y) = (a.key(key_of(t)).unwrap(), b.key(key_of(t)).unwrap());
        assert_eq!((x.preedit, x.candidates, x.commit), (y.preedit, y.candidates, y.commit), "{t}");
    }
}

#[test]
fn decoding_is_the_same_with_and_without_the_table() {
    let script = "S-j o e _ g 4 s u 3 c l 3 _ S-j o e _ _ g 4 Enter";
    let (with, without) = (run(&mut make(true, false), script), run(&mut make(false, false), script));
    assert_eq!(shown(&with), shown(&without));
    // The table did change the row somewhere, or the check proves nothing.
    assert_ne!(with.iter().map(|o| o.candidates.clone()).collect::<Vec<_>>(), without.iter().map(|o| o.candidates.clone()).collect::<Vec<_>>());
}

/// zhen-revert section 2.3: typed in lower case, j o e is not Chinese either: `e` (ㄍ) is in a column before the one `o` (ㄟ) holds, so it
/// reverts (b) to the raw keys, and Space makes the token `joe`. Zhen-inline 1.3 kept it as Zhuyin, and the
/// first test's token needed Shift.
#[test]
fn lower_case_joe_reverts_to_the_keys() {
    let mut e = make(true, false);
    for t in "j o e".split_whitespace() {
        e.key(key_of(t)).unwrap();
    }
    assert_eq!(e.english_reverted(), Some(core::engine::Revert::Order));
    e.key(key_of("_")).unwrap();
    assert_eq!(e.digits_syls(), ["_punct_joe"]);
}

// ---------- context-return: the English word the app's text ends with (docs/contracts/context-return.md sections 2 and 4) ----------

/// `set_left_context(text)`, then ㄕ on an empty composition: the row, and what decoding shows.
fn after_left(text: &str) -> (Vec<String>, Vec<(String, String, u32)>) {
    let mut e = make(true, false);
    e.set_left_context(text);
    let outs = run(&mut e, "g");
    (row(&outs), shown(&outs))
}

/// Section 2: the shell now sends the app's text; an English word at its end (after at most one space, 2-32 ASCII letters, no
/// letter or digit before it) starts the composition like a token inside it does. Decoding is the same as with "" (what the shell
/// sent for this text before: `LeftContext.han("我說Joe")` is nil).
#[test]
fn the_row_behind_an_english_left_text() {
    let (with, shown_with) = after_left("我說Joe");
    let (base, shown_base) = after_left("");
    assert_eq!(with, row(&run(&mut make(true, false), &format!("{JOE} g"))), "as behind the token inside the composition");
    assert!(with.contains(&"是要對決".to_string()) && !base.contains(&"是要對決".to_string()));
    assert_eq!(shown_with, shown_base);
    for text in ["Joe ", "JOE", "我說 Joe", "（Joe"] {
        assert_eq!(after_left(text).0, with, "{text:?}");
    }
    for text in ["Joe  ", "Joe1", "J", "1Joe", "我說Joe。", "abcdefghijklmnopqrstuvwxyzabcdefgJoe"] {
        assert_eq!(after_left(text), (base.clone(), shown_base.clone()), "{text:?}");
    }
    // 32 letters count, 33 do not (each with a table row of its own).
    for (n, counts) in [(32, true), (33, false)] {
        let word = "a".repeat(n);
        let mut e = make(false, false);
        e.set_english_next(Some(Arc::new(EnglishTable::parse(&format!("E\t{word}\t100\nW\t{word}\t水\t50\n")).unwrap())));
        e.set_left_context(&word);
        assert_eq!(row(&run(&mut e, "g"))[0] == "水", counts, "{n} letters");
    }
    // The row comes with the first unit; an empty composition has none (Backspace takes the unit away).
    let mut e = make(true, false);
    e.set_left_context("Joe");
    run(&mut e, "g");
    let o = e.key(Key::new(KeyKind::Backspace)).unwrap();
    assert!(o.preedit.is_empty() && o.candidates.is_empty());
}

/// Section 2: `left_full` keeps the English word and one space after it, nothing else (no declared length without a small model).
#[test]
fn only_the_english_word_is_kept() {
    let mut e = make(true, false);
    for (text, n) in [("我說Joe", 3), ("我說Joe ", 4), ("今天天氣很好", 0), ("我說Joe。", 0), ("", 0)] {
        e.set_left_context(text);
        assert_eq!(e.left_full_chars(), n, "{text:?}");
    }
}

/// Section 2, clearing: a commit, Esc, either reset and a rule 22 pass-through (an unmapped character on an empty composition)
/// leave `left_full` empty. Code 4 is core/src/ffi.rs `child_panic`.
#[test]
fn the_left_text_goes_with_the_composition() {
    let set = || {
        let mut e = make(true, false);
        e.set_left_context("Joe");
        assert_eq!(e.left_full_chars(), 3);
        e
    };
    let mut e = set();
    let outs = run(&mut e, "s u 3 Enter");
    assert_eq!(outs.last().unwrap().commit, "你");
    assert_eq!(e.left_full_chars(), 0, "commit");
    let mut e = set();
    run(&mut e, "s u 3 Esc");
    assert_eq!(e.left_full_chars(), 0, "Esc");
    for mode in [ResetMode::Commit, ResetMode::Discard] {
        let mut e = set();
        run(&mut e, "g");
        e.reset(mode);
        assert_eq!(e.left_full_chars(), 0, "reset");
    }
    let mut e = set();
    let o = e.key(Key::ch('é', 0)).unwrap();
    assert!(!o.handled && o.preedit.is_empty(), "rule 22");
    assert_eq!(e.left_full_chars(), 0, "pass-through");
    assert!(after_left("").0 == row(&run(&mut e, "g")), "the row after it is the one without a left text");
}

/// The learning file after a row pick of 使用 (a single character is not learned at sentence start) behind `left` (learning on, a fixed day), as text.
fn learned_from_the_row(left: &str, tag: &str) -> String {
    let dir = std::env::temp_dir().join(format!("shanjie-english-next-learn-{}-{tag}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    let mut e = make(true, false);
    e.set_today(Some(20_000));
    e.set_learning(true);
    e.learning_open(&dir).unwrap();
    e.set_left_context(left);
    let r = row(&run(&mut e, "g"));
    let i = r.iter().position(|w| w == "使用").unwrap_or_else(|| panic!("使用 is in the row behind {left:?}: {r:?}"));
    for _ in 0..=i {
        e.key(Key::new(KeyKind::Tab)).unwrap();
    }
    assert_eq!(e.key(Key::new(KeyKind::Enter)).unwrap().preedit, "使用");
    assert_eq!(e.key(Key::new(KeyKind::Enter)).unwrap().commit, "使用");
    let text = std::fs::read_to_string(dir.join(core::learn_store::FILE)).unwrap();
    std::fs::remove_dir_all(&dir).unwrap();
    text
}

/// Section 4 (P3-6): a row pick behind the English left text learns line for line what the same pick behind "" learns; the
/// English word is nowhere in the file.
#[test]
fn the_english_left_text_never_reaches_the_learning_file() {
    let (with, without) = (learned_from_the_row("我說Joe", "with"), learned_from_the_row("", "without"));
    assert!(with.lines().count() > 1, "the pick was learned: {} lines", with.lines().count());
    assert_eq!(with.lines().collect::<Vec<_>>(), without.lines().collect::<Vec<_>>());
    assert!(!with.to_ascii_lowercase().contains("joe"));
}

/// Section 4: a left text ending in Han ("……好") decodes, shows, predicts and learns byte for byte as its Han tail alone ("好"):
/// a re-pick in the candidate window, then Enter, with learning on.
#[test]
fn a_long_left_text_decodes_and_learns_as_its_han_tail() {
    let session = |left: &str, tag: &str| {
        let dir = std::env::temp_dir().join(format!("shanjie-english-next-tail-{}-{tag}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let mut e = make(true, false);
        e.set_today(Some(20_000));
        e.set_learning(true);
        e.learning_open(&dir).unwrap();
        e.set_left_context(left);
        let mut outs = run(&mut e, "g 4 c j 3 _");
        outs.extend(run(&mut e, "_"));
        outs.push(e.key(Key::new(KeyKind::Right)).unwrap());
        outs.extend(run(&mut e, "Enter Enter"));
        let all: Vec<_> = outs.iter().map(|o| (o.preedit.clone(), o.candidates.clone(), o.commit.clone(), o.selected)).collect();
        let records = e.learner().records().to_vec();
        let file = std::fs::read_to_string(dir.join(core::learn_store::FILE)).unwrap();
        std::fs::remove_dir_all(&dir).unwrap();
        (all, records, file)
    };
    let (a, b) = (session("……好", "long"), session("好", "short"));
    assert!(!a.1.is_empty(), "the re-pick was learned");
    assert!(a == b);
    let (c, d) = (session("我說 Joe 覺得這樣很好", "long2"), session("很好", "short2"));
    assert!(c == d);
}
