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
const EXTRA: &str = "ㄕˋ-ㄧㄠˋ-ㄉㄨㄟˋ-ㄐㄩㄝˊ\t是要對決\t-6.60980192\ttest\n";

/// joe: 是要對決 40, 說 30, 水 10 and 不在詞庫的詞 90 (a table word the lexicon does not have: it must never show).
/// N(joe) 200 is larger than the sum of the rows, as in a real table (the rest followed with a word below the cut).
const TABLE: &str = "# test table\nE\tabc\t10\nE\tjoe\t200\nW\tabc\t水\t7\nW\tjoe\t不在詞庫的詞\t90\nW\tjoe\t是要對決\t40\nW\tjoe\t水\t10\nW\tjoe\t說\t30\n";

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
    assert!(EnglishTable::parse("E\tjoe\n").is_none());
    assert!(EnglishTable::parse("E\tjoe\t0\n").is_none());
    assert!(EnglishTable::parse("W\tjoe\tx\t1\n").is_none());
    assert!(EnglishTable::parse("# nothing\n").is_none());
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
