//! English before Chinese (docs/contracts/english-next.md 2 and 3.4): the engine on the real lexicon and model with a small test table.
//! The key sequence is the contract's: Shift+J, o, e, Space (the run ends and the Space is eaten), g (ㄕ). The word list is the synthetic
//! eval/golden/english-tiny.txt, as in the other English engine tests. Key script: whitespace separated, `_` Space, `S-x` Shift+x, a
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

/// Standard layout, the model, the word list and the switch on; the table (`with_table`) and the digit table (`with_digits`).
fn make(with_table: bool, with_digits: bool) -> Engine {
    let s = shared();
    let mut e = Engine::with_lexicon(s.lex.clone(), Layout::Standard);
    e.set_lm(s.lm.clone(), s.capped.clone());
    e.load_english(&root().join("eval/golden/english-tiny.txt")).unwrap();
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

/// zhen-inline 1.3: typed in lower case, j o e does not become a token, whatever the word list holds (no key overwrites a symbol, and
/// ㄍㄨㄟ is a syllable), so the first test's token needs Shift.
#[test]
fn lower_case_joe_is_not_kept() {
    let list = std::env::temp_dir().join(format!("shanjie-english-next-list-{}.txt", std::process::id()));
    std::fs::write(&list, "joe\n").unwrap();
    let mut e = make(true, false);
    e.load_english(&list).unwrap();
    std::fs::remove_file(&list).unwrap();
    for t in "j o e _ g".split_whitespace() {
        e.key(key_of(t)).unwrap();
        assert!(!e.english_autokept());
    }
    assert!(!e.digits_syls().iter().any(|s| s.starts_with("_punct_")));
}
