//! Digits in the Zhuyin composition (docs/spec/english-mixed.md §13.1): the engine on the real lexicon, model and count table
//! (`data/lexicon/digits.tsv`), standard layout, `set_english(true)`. Rows are named after the table in docs/spec/english-mixed.md §13.1 (#n).
//! Key script, whitespace separated: a single character is that keycap, `_` Space, `Enter`, `Esc`, `BS`, `Left`, `Right`, `Down`, `Tab`,
//! `S-x` with Shift. A script's last output is what a row asserts unless it says otherwise.
use core::digits::DigitTable;
use core::engine::*;
use core::learn::context_key;
use core::lm::{history, CappedLexicon, Lm};
use core::predict::{predict_digits, units_of};
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
    table: Arc<DigitTable>,
}

fn shared() -> &'static Shared {
    static S: OnceLock<Shared> = OnceLock::new();
    S.get_or_init(|| {
        let lm_file = root().join("data/lm/bigram.sjlm");
        assert!(lm_file.exists(), "data/lm/bigram.sjlm is missing: gh release download model-v6 -R Nanako0129/shanjie -p bigram.sjlm -D data/lm");
        let lm = Lm::load(&lm_file).unwrap();
        let dir = root().join("data/lexicon");
        let (lex, pack_text) = load_lexicon_packs(&dir, None).unwrap();
        let overlay = capping_overlay(&dir, &pack_text).unwrap();
        let capped = Arc::new(CappedLexicon::new(lex.clone(), &overlay, &lm, None).unwrap());
        let table = Arc::new(DigitTable::parse(&std::fs::read_to_string(dir.join("digits.tsv")).unwrap()).expect("data/lexicon/digits.tsv parses"));
        Shared { lex, lm: Arc::new(lm), capped, table }
    })
}

/// Standard layout, the model and the switch on, and the count table (`with_table`).
fn make(with_table: bool, english: bool) -> Engine {
    let s = shared();
    let mut e = Engine::with_lexicon(s.lex.clone(), Layout::Standard);
    e.set_lm(s.lm.clone(), s.capped.clone());
    if with_table {
        e.set_digit_table(Some(s.table.clone()));
    }
    e.set_english(english).unwrap();
    e
}
fn eng() -> Engine {
    make(true, true)
}

#[path = "common/digits_scripts.rs"]
mod scripts;
use scripts::{key_of, line, rows, send};

/// Sends the script; every output.
fn run_all(e: &mut Engine, script: &str) -> Vec<Output> {
    script.split_whitespace().map(|t| send(e, t)).collect()
}
/// Sends the script (tokens as in `scripts`); the last output.
fn run(e: &mut Engine, script: &str) -> Output {
    run_all(e, script).pop().expect("a script")
}
/// A fresh engine, the script, and everything committed.
fn typed(script: &str) -> String {
    let mut e = eng();
    run_all(&mut e, script).iter().map(|o| o.commit.as_str()).collect()
}

// ---------- the golden of the scores (#49) ----------

struct GoldenKey {
    key: String,
    flush: usize,
    ctx: String,
    buf: String,
    splits: Vec<(usize, usize, f64)>,
    choice: Option<(usize, usize)>,
}

fn field<'a>(f: &'a [&str], name: &str) -> &'a str {
    f.iter().find_map(|x| x.strip_prefix(name)).unwrap_or_else(|| panic!("no {name}"))
}

/// (left, keys, rows of keys) of eval/golden/digits-score.txt.
fn golden_score() -> Vec<(String, Vec<String>, Vec<GoldenKey>)> {
    golden_rows("digits-score.txt")
}

/// The `##` rows and key lines of a golden file (`L` lines are `late_decision` rows, read by `golden_late`).
fn golden_rows(file: &str) -> Vec<(String, Vec<String>, Vec<GoldenKey>)> {
    let text = std::fs::read_to_string(root().join("eval/golden").join(file)).unwrap();
    let mut rows: Vec<(String, Vec<String>, Vec<GoldenKey>)> = Vec::new();
    for l in text.lines().filter(|l| !l.starts_with("# ") && !l.starts_with("L\t")) {
        let f: Vec<&str> = l.split('\t').collect();
        if f[0].starts_with("## ") {
            let keys = field(&f, "keys=").chars().map(String::from).collect();
            rows.push((field(&f, "left=").to_string(), keys, Vec::new()));
        } else {
            let splits = field(&f, "splits=")
                .split(';')
                .filter(|s| !s.is_empty())
                .map(|s| {
                    let p: Vec<&str> = s.splitn(3, ',').collect();
                    (p[0].parse().unwrap(), p[1].parse().unwrap(), p[2].parse::<f64>().unwrap())
                })
                .collect();
            let choice = match field(&f, "choice=") {
                "-" => None,
                c => c.split_once(',').map(|(m, t)| (m.parse().unwrap(), t.parse().unwrap())),
            };
            rows.last_mut().unwrap().2.push(GoldenKey {
                key: f[1].to_string(),
                flush: field(&f, "flush=").parse().unwrap(),
                ctx: field(&f, "ctx=").to_string(),
                buf: field(&f, "buf=").to_string(),
                splits,
                choice,
            });
        }
    }
    rows
}

fn golden_key(k: &str) -> Key {
    match k {
        "␣" => Key::new(KeyKind::Space),
        "⏎" => Key::new(KeyKind::Enter),
        c => Key::ch(c.chars().next().unwrap(), 0),
    }
}

/// #49: every scoring call of the engine (the splits, their scores as f64 bits, the pick, the buffer, the flushes, the context) is the
/// Python baseline's, key by key, on the 31 rows of eval/golden/digits-score.txt. macOS only (Linux's libm differs in the last bit of
/// log10, as for the other f64 goldens): the Windows machine's WSL skips it, CI's macOS runs it.
#[test]
fn row49_scores_match_the_python_golden_bit_for_bit() {
    if !cfg!(target_os = "macos") {
        return;
    }
    let rows = golden_score();
    assert_eq!(rows.len(), 31);
    compare_scores(&rows, 90);
}

/// Every scoring call of the engine (`eng()`) on the rows, key by key, is the golden's: splits, score bits, pick, buffer, flushes, context. At least `least` calls.
fn compare_scores(rows: &[(String, Vec<String>, Vec<GoldenKey>)], least: usize) {
    let mut compared = 0;
    let mut e = eng();
    e.set_digits_trace(true);
    for (n, (left, _, keys)) in rows.iter().enumerate() {
        e.reset(ResetMode::Discard);
        e.set_left_context(left);
        e.take_digits_trace();
        for (i, g) in keys.iter().enumerate() {
            e.key(golden_key(&g.key)).unwrap();
            let ev = e.take_digits_trace();
            let at = format!("row {} key {} ({})", n + 1, i, g.key);
            if g.splits.is_empty() {
                assert!(ev.is_empty(), "{at}: the engine scored a key the baseline did not");
                continue;
            }
            assert_eq!(ev.len(), 1, "{at}: one scoring call");
            let ev = &ev[0];
            assert_eq!((ev.buf.as_str(), ev.flush, ev.ctx.as_str()), (g.buf.as_str(), g.flush, g.ctx.as_str()), "{at}: buffer, flush, context");
            assert_eq!(ev.splits.len(), g.splits.len(), "{at}: number of splits");
            for (a, b) in ev.splits.iter().zip(&g.splits) {
                assert_eq!((a.0, a.1), (b.0, b.1), "{at}: split");
                assert_eq!(a.2.to_bits(), b.2.to_bits(), "{at}: score of split {},{}: {} vs {}", a.0, a.1, a.2, b.2);
            }
            assert_eq!(ev.choice, g.choice, "{at}: pick");
            compared += 1;
        }
    }
    let want = rows.iter().flat_map(|r| &r.2).filter(|g| !g.splits.is_empty()).count();
    assert!(want > least && compared == want, "compared {compared} of {want} scoring calls");
}


/// The script of row `n` of `scripts::rows`.
fn script(n: &str) -> String {
    rows().into_iter().find(|r| r.0 == n).unwrap().1
}
fn syls(e: &Engine) -> Vec<String> {
    e.digits_syls()
}
fn toks(v: &[&str]) -> Vec<String> {
    v.iter().map(|s| s.to_string()).collect()
}
/// What a Zhuyin syllable such as `ㄒㄧㄚˋ` is typed with (standard layout), as script tokens; tone one ends with Space.
fn zh(syl: &str) -> String {
    let l = Layout::Standard;
    let mut v: Vec<String> = syl.chars().map(|c| l.key_of_tone(c).or_else(|| l.key_of_symbol(c)).unwrap().to_string()).collect();
    if !syl.chars().any(|c| l.key_of_tone(c).is_some()) {
        v.push("_".into());
    }
    v.join(" ")
}

/// The whole list of the open window, in order (walking the selection with Right, then back to the first item).
fn items(e: &mut Engine, o: &Output) -> Vec<String> {
    let total = o.total as usize;
    let mut all = o.candidates.clone();
    for _ in 1..total {
        let n = e.key(key_of("Right")).unwrap();
        let first = n.first as usize;
        if first + n.candidates.len() > all.len() {
            all.extend(n.candidates[all.len() - first..].iter().cloned());
        }
    }
    for _ in 1..total {
        e.key(key_of("Left")).unwrap();
    }
    assert_eq!(all.len(), total);
    all
}
/// Chooses item `i` of the open window (selection on the first item).
fn pick_item(e: &mut Engine, i: usize) -> Output {
    for _ in 0..i {
        e.key(key_of("Right")).unwrap();
    }
    e.key(key_of("Enter")).unwrap()
}

// ---------- structure rows: no score decides ----------

#[test]
fn row01_a_digit_before_a_boundary_mark_is_kept() {
    let mut e = eng();
    assert_eq!(run(&mut e, &script("r01")).preedit, "1、");
    assert_eq!(syls(&e), toks(&["_punct_1", "_punct_、"]));
}

#[test]
fn row02_marks_and_english_around_digits() {
    assert_eq!(typed(&script("r02")), "1、A，2、B");
}

#[test]
fn row03_digits_after_a_phrase_are_not_syllables() {
    assert_eq!(typed(&script("r03")), "測試123");
}

#[test]
fn row04_a_year() {
    assert_eq!(typed(&script("r04")), "2026年");
}

#[test]
fn row05_a_key_outside_the_alphabet_splits_the_buffer() {
    assert_eq!(typed(&script("r05")), "1月");
}

#[test]
fn row06_pi() {
    assert_eq!(typed(&script("r06")), "3.14");
}

#[test]
fn row07_shift_five_is_a_boundary_and_passes_on() {
    let mut e = eng();
    let o = run(&mut e, &script("r07"));
    assert!(!o.handled && o.commit == "100", "the digits go out and the key goes on (rule 21)");
}

#[test]
fn row08_a_date_with_slashes() {
    assert_eq!(typed(&script("r08")), "2026/10/15");
}

#[test]
fn row09_hyphen() {
    assert_eq!(typed(&script("r09")), "1-2");
}

#[test]
fn row14_another_year() {
    assert_eq!(typed(&script("r14")), "2004年");
}

#[test]
fn row17_three_points() {
    assert_eq!(typed(&script("r17")), "3點");
}

#[test]
fn row19_the_key_outside_the_alphabet_can_only_be_in_the_tail() {
    let mut e = eng();
    run(&mut e, &script("r19").replace(" Enter", ""));
    assert_eq!(syls(&e), toks(&["_punct_1", "_punct_0", "_punct_0", "ㄩㄢˊ"]));
}

#[test]
fn row20_to_row25_numbers_and_measure_words() {
    assert_eq!(typed(&script("r20")), "100個");
    assert_eq!(typed(&script("r21")), "28號");
    let mut e = eng();
    run(&mut e, &script("r22").replace(" Enter", ""));
    assert_eq!(syls(&e), toks(&["_punct_1", "_punct_.", "_punct_5", "ㄅㄟˋ"]), "錯字回報 112 列");
    assert_eq!(typed(&script("r23")), "2.5");
    let mut e = eng();
    run(&mut e, &script("r24"));
    assert_eq!(syls(&e), toks(&["ㄉㄡ"]), "都 (scored: the golden's choice of its row)");
    assert_eq!(typed(&script("r25")), "0.5");
}

#[test]
fn row26_english_then_a_syllable() {
    assert_eq!(typed(&script("r26")), "ATM轉");
}

/// #27: from docs/spec/english-mixed.md §4.2, §4.3, §4.5 an English run takes the Zhuyin keys that are not letters, so `1 2 5 8 9 0 ...` after `S-A` stay on the run
/// (the digit layer does not step in while a run is open) and the commit is the same `A1258901258`: the units of the composition stay at none
/// until the run ends. (Before, each digit key ended the run and the buffer flushed its oldest key as a token from the fifth key on.)
#[test]
fn row27_the_fifth_key_flushes_the_oldest() {
    let mut e = eng();
    let keys = "1 2 5 8 9 0 1 2 5 8";
    run(&mut e, "S-A");
    for (n, k) in keys.split(' ').enumerate() {
        let o = e.key(key_of(k)).unwrap();
        assert_eq!(syls(&e).len(), 0, "key {}: the run holds the keys, no token yet", n + 1);
        assert_eq!(o.english_run_utf16_len as usize, n + 2, "key {}: the run grows", n + 1);
    }
    assert_eq!(run(&mut e, "Enter").commit, "A1258901258");
}

#[test]
fn row28_one_letter_then_a_digit() {
    assert_eq!(typed("S-P 4 Enter"), "P4");
    assert_eq!(typed("S-O 7 Enter"), "O7");
}

#[test]
fn row29_enter_flushes_the_buffer() {
    assert_eq!(typed(&script("r29")), "2020");
}

#[test]
fn row31_a_syllable_that_is_only_a_letter_word_is_not_a_syllable() {
    let mut e = eng();
    run(&mut e, &script("r31"));
    assert_eq!(syls(&e), toks(&["_punct_1", "_punct_ "]));
}

#[test]
fn row33_a_key_that_cannot_follow_ends_the_digits() {
    let mut e = eng();
    let o = run(&mut e, &script("r33"));
    assert_eq!(syls(&e), toks(&["_punct_2", "_punct_0"]));
    assert_eq!(o.preedit, "20ㄧ", "the unfinished syllable is ㄧ behind the two tokens");
}

#[test]
fn row34_escape_clears_the_buffer() {
    let mut e = eng();
    let o = run(&mut e, &script("r34"));
    assert!(o.preedit.is_empty() && syls(&e).is_empty());
}

#[test]
fn row35_a_digit_after_a_syllable() {
    assert_eq!(typed(&script("r35")), "你3");
}

#[test]
fn row36_marks_after_a_digit() {
    let mut e = eng();
    let o = run(&mut e, "1 S-,");
    assert_eq!(o.preedit, "1，");
    e.key(key_of("Esc")).unwrap();
    e.key(key_of("Esc")).unwrap();
    let mut e = eng();
    assert_eq!(run(&mut e, "1 S-1").preedit, "1！");
}

#[test]
fn row38_reset_commit_sends_the_digits() {
    let mut e = eng();
    run(&mut e, "2 0 2 6");
    assert_eq!(e.reset(ResetMode::Commit).commit, "2026");
}

// ---------- scored rows: the last `syls` and the display of the golden ----------

/// #10 (golden row 5): at the sentence start 8 is a digit, then 84 is the syllable ㄚˋ.
#[test]
fn row10_eight_four_is_a_syllable_at_the_start() {
    let mut e = eng();
    let os = run_all(&mut e, "8 4");
    assert_eq!(os[0].preedit, "8", "the first key shows as the digit (golden row 5)");
    assert_eq!(syls(&e), toks(&["ㄚˋ"]));
}

/// #11: Down lists the digits as the last item; choosing it puts the keys back as tokens.
#[test]
fn row11_the_digits_are_the_last_item_of_the_window() {
    let mut e = eng();
    let o = run(&mut e, &script("r11"));
    let list = items(&mut e, &o);
    assert_eq!(list.last().map(String::as_str), Some("84"));
    let o = pick_item(&mut e, list.len() - 1);
    assert_eq!(syls(&e), toks(&["_punct_8", "_punct_4"]));
    assert_eq!(o.preedit, "84");
}

#[test]
fn row12_a_mark_after_a_syllable_made_of_digit_keys() {
    let mut e = eng();
    run(&mut e, &script("r12"));
    assert_eq!(syls(&e), toks(&["ㄚˋ", "_punct_，"]));
}

/// #13: 1984 stays digits (golden row 8: the 8 does not fit after ㄅㄞ, and at the 4 the digits beat 19 + ㄚˋ).
#[test]
fn row13_nineteen_eighty_four() {
    assert_eq!(typed(&script("r13")), "1984");
}

/// #15 (golden row 7): 504 is the syllable ㄓㄢˋ; Down ends with the digits.
#[test]
fn row15_five_oh_four() {
    let mut e = eng();
    let o = run(&mut e, &script("r15"));
    assert_eq!(syls(&e), toks(&["ㄓㄢˋ"]));
    let list = items(&mut e, &o);
    assert_eq!(list.last().map(String::as_str), Some("504"));
}

/// #16: 10 then the tail ㄅㄟˋ (倍 is not the first word of that reading, so the row does not name the character).
#[test]
fn row16_a_hundred_and_one_is_ten_and_a_syllable() {
    let mut e = eng();
    let os = run_all(&mut e, &script("r16").replace(" Enter", ""));
    assert_eq!(syls(&e), toks(&["_punct_1", "_punct_0", "ㄅㄟˋ"]));
    assert_eq!(os[3].preedit, "10ㄅㄟ", "after the o: two digits, then the unfinished syllable");
}

/// #18 (golden row 14): 25 and Space: digits, and the Space a separator.
#[test]
fn row18_twenty_five_space() {
    let mut e = eng();
    run(&mut e, &script("r18"));
    assert_eq!(syls(&e), toks(&["_punct_2", "_punct_5", "_punct_ "]));
}

/// #30 (golden row 30): 2020 and Space.
#[test]
fn row30_year_and_space() {
    let mut e = eng();
    run(&mut e, &script("r30"));
    assert_eq!(syls(&e), toks(&["_punct_2", "_punct_0", "_punct_2", "_punct_0", "_punct_ "]));
}

/// #32: Backspace takes the last buffered key and scores again; the display equals a run that only had the first key.
#[test]
fn row32_backspace_in_the_buffer() {
    let mut e = eng();
    let os = run_all(&mut e, "1 2 BS BS");
    let first = run_all(&mut eng(), "1");
    assert_eq!(os[2].preedit, first[0].preedit, "after the first Backspace the buffer is the first key again");
    assert!(os[3].preedit.is_empty() && os[3].handled, "after the second the buffer is empty (and the key was ours)");
    assert!(syls(&e).is_empty());
}

/// #37: with 127 units, the fifth key of a run pushes the oldest key out as the 128th unit: the whole composition is sent, ending with that digit.
#[test]
fn row37_full_composition() {
    let mut e = eng();
    let os = run_all(&mut e, &script("r37"));
    let n = os.len();
    assert!(os[..n - 1].iter().all(|o| o.commit.is_empty()));
    let o = &os[n - 1];
    assert_eq!(o.commit.chars().count(), MAX_SYLLABLES);
    assert!(o.commit.ends_with('1') && o.commit.starts_with('，'));
    assert!(syls(&e).is_empty(), "the composition is empty: only the buffer is left");
    assert_eq!(o.preedit, "234ㄓ", "the buffer shows as the baseline scores 12345: 234, then the unfinished ㄓ");
}

/// #39: Down is a boundary; the window lists the row of the digit context, then the digit and its full-width form; choosing the latter fixes it.
#[test]
fn row39_down_after_digits() {
    let mut e = eng();
    let o = run(&mut e, &script("r39"));
    assert_eq!(syls(&e), toks(&["_punct_1", "_punct_0"]), "Down ended the run");
    let list = items(&mut e, &o);
    assert_eq!(&list[list.len() - 2..], ["0", "０"]);
    let row = &list[..list.len() - 2];
    assert!(!row.is_empty() && row.contains(&"年".to_string()), "the row of the digit context first: {row:?}");
    let o = pick_item(&mut e, list.len() - 1);
    assert_eq!(o.preedit, "1０");
    assert_eq!(syls(&e), toks(&["_punct_1", "_punct_0"]), "the reading stays");
}

/// docs/spec/english-mixed.md §7.7: after digits whose last keys could be a syllable, Down ends with that syllable's first word; choosing it replaces the tokens.
#[test]
fn a36_the_syllable_of_the_last_digits() {
    let mut e = eng();
    let o = run(&mut e, "1 9 8 4 Down");
    let list = items(&mut e, &o);
    let n = list.len();
    assert_eq!(&list[n - 3..n - 1], ["4", "４"]);
    let word = list[n - 1].clone();
    assert!(word.chars().count() == 1 && word != "4", "the first word of ㄚˋ: {word}");
    let o = pick_item(&mut e, n - 1);
    assert_eq!(syls(&e), toks(&["_punct_1", "_punct_9", "ㄚˋ"]));
    assert_eq!(o.preedit, format!("19{word}"));
}

// ---------- switch, layout, learning ----------

/// #41: with the abbreviation composer on the digit keys are the keys of the slice before (a second unit opens).
#[test]
fn row41_abbreviation_excludes_digits() {
    let mut e = eng();
    e.set_abbreviation(true).unwrap();
    let o = run(&mut e, "1 2");
    assert_eq!(o.preedit, "ㄅㄉ");
    assert!(syls(&e).is_empty());
}

/// #42: the Eten engine behaves the same with the switch on and off (every output of the rows).
#[test]
fn row42_eten_ignores_the_switch() {
    let s = shared();
    let mk = |on: bool| {
        let mut e = Engine::with_lexicon(s.lex.clone(), Layout::Eten);
        e.set_lm(s.lm.clone(), s.capped.clone());
        e.set_digit_table(Some(s.table.clone()));
        e.set_english(on).unwrap();
        e
    };
    let (mut on, mut off) = (mk(true), mk(false));
    for (name, sc) in rows() {
        on.reset(ResetMode::Discard);
        off.reset(ResetMode::Discard);
        for t in sc.split_whitespace() {
            let (a, b) = (send(&mut on, t), send(&mut off, t));
            assert!(a == b, "{name} {t}: {} vs {}", line(&a), line(&b));
        }
    }
}

/// #43: a digit string after a re-picked word is the neighbour `^>_punct_num` in the learning key, whichever digit it is.
#[test]
fn row43_the_learning_key_of_a_digit_neighbour() {
    let mut found = false;
    for pick in 0..4 {
        let mut e = eng();
        e.set_learning(true);
        e.set_prediction(false).unwrap(); // no row items in front of the lexicon's words
        run(&mut e, &format!("{} {} Down", zh("ㄒㄧㄣ"), zh("ㄑㄧㄥˊ")));
        for _ in 0..pick {
            e.key(key_of("Right")).unwrap();
        }
        e.key(key_of("Enter")).unwrap();
        run(&mut e, "7 Enter");
        let ctxs: Vec<String> = e.learner().records().iter().map(|r| r.context.clone()).collect();
        assert!(ctxs.iter().all(|c| c != "^>_punct_7"), "the digit itself is never the key: {ctxs:?}");
        found |= ctxs.iter().any(|c| c == "^>_punct_num");
        if found {
            break;
        }
    }
    assert!(found, "a re-pick before a digit token is learned under ^>_punct_num");
}

// ---------- the prediction row after digits (docs/spec/english-mixed.md §7.6) ----------

/// (left, digits, symbols, [(word, score)]) of eval/golden/digits-pred.txt.
fn golden_pred() -> Vec<(String, String, String, Vec<(String, f64)>)> {
    let text = std::fs::read_to_string(root().join("eval/golden/digits-pred.txt")).unwrap();
    let mut out: Vec<(String, String, String, Vec<(String, f64)>)> = Vec::new();
    for l in text.lines().filter(|l| !l.starts_with("# ")) {
        let f: Vec<&str> = l.split('\t').collect();
        if f[0].starts_with("## ") {
            out.push((field(&f, "left=").into(), field(&f, "digits=").into(), field(&f, "syms=").into(), Vec::new()));
        } else {
            out.last_mut().unwrap().3.push((f[1].to_string(), f[2].parse().unwrap()));
        }
    }
    out
}

/// The keys of a prediction situation: the digits, then the key of the unfinished symbol (ㄩ: a key outside the alphabet).
fn pred_script(digits: &str, syms: &str) -> String {
    let mut v: Vec<String> = digits.chars().map(String::from).collect();
    v.extend(syms.chars().map(|c| Layout::Standard.key_of_symbol(c).unwrap().to_string()));
    v.join(" ")
}

/// The core function equals the Python `digit_row` on the 12 situations: words and score bits. macOS only (as #49).
#[test]
fn predict_digits_matches_the_python_golden_bit_for_bit() {
    if !cfg!(target_os = "macos") {
        return;
    }
    let s = shared();
    let idx = s.capped.predict_index(&s.lm);
    let words = idx.digit_words(&s.table.all_words());
    let lam = core::lm::Profile::Chat.lambda();
    let g = golden_pred();
    assert_eq!(g.len(), 12);
    for (left, digits, syms, want) in &g {
        let key = context_key(left);
        let v = history(if key == "^" { "" } else { &key }, &s.lm);
        let units = if syms.is_empty() { Vec::new() } else { units_of(syms) };
        let got = predict_digits(idx, &s.lm, &s.table, &words, lam, v, &units, 9);
        let at = format!("{left}|{digits}|{syms}");
        assert_eq!(got.len(), want.len(), "{at}: length");
        for (i, (a, b)) in got.iter().zip(want).enumerate() {
            assert_eq!(a.0, b.0, "{at}: word {i}");
            assert_eq!(a.1.to_bits(), b.1.to_bits(), "{at}: score of {}", b.0);
        }
    }
}

/// #44, #45: the engine's prediction row after the digits equals the golden's words (it is the cursor start's row: no other start is queried).
#[test]
fn row44_row45_the_row_after_digits_is_the_golden_row() {
    let mut compared = 0;
    for (left, digits, syms, want) in golden_pred() {
        let mut e = eng();
        e.set_left_context(&left);
        let o = run(&mut e, &pred_script(&digits, &syms));
        let shown = format!("{digits}{}", if syms.is_empty() { String::new() } else { "ㄩ".into() });
        assert_eq!(o.preedit, shown, "{left}|{digits}|{syms}: the situation is digits (and the tail) on screen");
        let words: Vec<String> = want.iter().map(|w| w.0.clone()).collect();
        assert_eq!(o.candidates, words, "{left}|{digits}|{syms}");
        compared += 1;
    }
    assert_eq!(compared, 12);
    // #44's example: after 下午 and a 3 the row has 點.
    let mut e = eng();
    e.set_left_context("下午");
    assert!(run(&mut e, "3").candidates.contains(&"點".to_string()));
}

/// #45a: the digit situation with nothing shown as a digit: the row is today's, and choosing its first item leaves no digit token.
#[test]
fn row45a_choosing_a_row_item_with_only_a_tail_makes_no_digit_token() {
    let mut e = eng();
    let o = run(&mut e, "2");
    assert_eq!(o.preedit, "ㄉ");
    let first = o.candidates[0].clone();
    run(&mut e, "Tab");
    let o = run(&mut e, "Enter");
    assert!(syls(&e).iter().all(|s| !s.starts_with("_punct_")) && syls(&e).len() == first.chars().count(), "no digit token: {:?}", syls(&e));
    assert!(o.preedit.starts_with(&first));
}

/// #45b: digits shown and a tail: choosing the first item keeps the digits as tokens in front of the word.
#[test]
fn row45b_choosing_a_row_item_after_buffered_digits() {
    let mut e = eng();
    let o = run(&mut e, "1 0 m");
    assert_eq!(o.preedit, "10ㄩ");
    let first = o.candidates[0].clone();
    run(&mut e, "Tab");
    let o = run(&mut e, "Enter");
    let s = syls(&e);
    assert_eq!(&s[..2], ["_punct_1", "_punct_0"]);
    assert!(s.len() == 2 + first.chars().count() && s[2..].iter().all(|x| !x.starts_with("_punct_")), "{s:?}");
    assert_eq!(o.preedit, format!("10{first}"));
}

/// #45b, the tail made of buffered keys: 12345 shows 234 and the unfinished ㄓ (the 5 is still in the buffer); choosing the first item
/// makes tokens of the shown digits only, and the word replaces the ㄓ (no token for the 5).
#[test]
fn row45b_a_tail_that_is_still_in_the_buffer_is_consumed_not_flushed() {
    let mut e = eng();
    let o = run(&mut e, "1 2 3 4 5");
    assert_eq!(o.preedit, "1234ㄓ", "one token (the 1), 234 shown as digits, the 5 is the unfinished ㄓ");
    let first = o.candidates[0].clone();
    run(&mut e, "Tab");
    run(&mut e, "Enter");
    let s = syls(&e);
    assert_eq!(&s[..4], ["_punct_1", "_punct_2", "_punct_3", "_punct_4"], "{s:?}");
    assert!(s.len() == 4 + first.chars().count() && s[4..].iter().all(|x| !x.starts_with("_punct_")), "{s:?}");
}

/// #45c: digits in the buffer after two typed syllables: the row is the digit-context row only (no start at 下午).
#[test]
fn row45c_buffered_digits_count_as_tokens_for_the_long_starts() {
    let mut e = eng();
    run(&mut e, &format!("{} {}", zh("ㄒㄧㄚˋ"), zh("ㄨˇ")));
    let o = run(&mut e, "2");
    assert_eq!(o.preedit, "下午2");
    let g = golden_pred();
    let want: Vec<String> = g.iter().find(|r| r.0 == "下午" && r.1 == "2").unwrap().3.iter().map(|w| w.0.clone()).collect();
    assert_eq!(o.candidates, want);
}

/// #46 (golden rows 28 and 29): after 下午 the 2 and the 0 show as digits; the 3 makes the syllable ㄉㄢˇ (scored like the baseline).
#[test]
fn row46_digits_after_a_word() {
    let mut e = eng();
    e.set_left_context("下午");
    let os = run_all(&mut e, "2 0 3");
    assert_eq!((os[0].preedit.as_str(), os[1].preedit.as_str()), ("2", "20"));
    assert_eq!(syls(&e), toks(&["ㄉㄢˇ"]));
}

/// #47: turning the switch off with digits buffered makes them tokens (and the output says so); later keys are the old ones.
#[test]
fn row47_switch_off_with_a_buffer() {
    let mut e = eng();
    run(&mut e, "2 0 2 6");
    let o = e.set_english(false).unwrap();
    assert_eq!(o.preedit, "2026");
    assert_eq!(syls(&e).len(), 4);
    assert_eq!(run(&mut e, "1").preedit, "2026ㄅ", "a digit key is a Zhuyin key again");
    let mut e = eng();
    run(&mut e, "2 0 2 6");
    let o = e.set_abbreviation(true).unwrap();
    assert_eq!((o.preedit.as_str(), syls(&e).len()), ("2026", 4));
}

// ---------- the table is optional, the golden's header ----------

/// The golden files name the table and the models they were made with.
#[test]
fn goldens_name_the_files_they_were_made_with() {
    let table = std::fs::read(root().join("data/lexicon/digits.tsv")).unwrap();
    let sha = core::eval::sha256_hex(&table);
    for f in ["digits-score.txt", "digits-pred.txt"] {
        let text = std::fs::read_to_string(root().join("eval/golden").join(f)).unwrap();
        assert!(text.contains(&format!("digits.tsv sha256={sha}")), "{f}: the table changed; regenerate the golden on macOS");
    }
}

// ---------- regressions ----------

fn commas(n: usize) -> String {
    vec!["S-,"; n].join(" ")
}
fn all_commit(os: &[Output]) -> String {
    os.iter().map(|o| o.commit.as_str()).collect()
}
fn cmd_bs() -> Key {
    Key { kind: KeyKind::Backspace, ch: '\0', modifiers: MOD_COMMAND }
}

/// the last unit (`MAX_SYLLABLES`) that a flushed digit sends does not take the previous key's unfinished tail along (the 5 still in `pend` as ㄓ).
#[test]
fn review_p2_1_the_last_unit_sends_no_stale_tail() {
    let mut e = eng();
    let os = run_all(&mut e, &format!("{} 1 2 3 4 5 8", commas(MAX_SYLLABLES - 2)));
    assert!(os[os.len() - 2].preedit.ends_with("234ㄓ"), "premise: the tail of the 5 is on screen before the 8");
    let o = os.last().unwrap();
    assert_eq!(o.commit, format!("{}12", "，".repeat(MAX_SYLLABLES - 2)), "{:?}", o.commit);
    assert!(!o.commit.contains('ㄓ') && !o.preedit.contains('ㄓ'));
    assert_eq!(o.preedit, "3458", "the rest of the buffer, shown as the baseline scores it");
    // Space then Enter behind 1 2 3 4 5: nothing but the keys typed (the ㄓ of the 5 is a digit key, not a syllable that was finished).
    let mut e = eng();
    let os = run_all(&mut e, &format!("{} 1 2 3 4 5 _ Enter", commas(MAX_SYLLABLES - 2)));
    let all = all_commit(&os);
    assert!(!all.contains('ㄓ'), "{all:?}");
    assert!(all.starts_with(&"，".repeat(MAX_SYLLABLES - 2)) && all.contains("12345"), "{all:?}");
    // docs/spec/english-mixed.md §7.5 (was: no ㄓ either): Enter sends what the screen shows, the ㄓ of the 5 included.
    let mut e = eng();
    let os = run_all(&mut e, &format!("{} 1 2 3 4 5 Enter", commas(MAX_SYLLABLES - 2)));
    let shown = &os[os.len() - 2].preedit;
    assert!(shown.ends_with("234ㄓ"), "premise: {shown:?}");
    assert_eq!(all_commit(&os), format!("{}{}", "，".repeat(MAX_SYLLABLES - 2), shown.trim_start_matches('，')), "{:?}", all_commit(&os));
}

/// Enter ends a run whose flush reaches `MAX_SYLLABLES` while more of the buffer is left: everything is sent, nothing stays behind.
#[test]
fn review_p2_2_enter_sends_the_rest_of_the_buffer_after_the_last_unit() {
    let mut e = eng();
    let os = run_all(&mut e, &format!("{} 1 2 3 4 Enter", commas(MAX_SYLLABLES - 2)));
    let o = os.last().unwrap();
    assert_eq!(all_commit(&os), format!("{}1234", "，".repeat(MAX_SYLLABLES - 2)));
    assert_eq!(o.preedit, "");
    assert!(syls(&e).is_empty());
}

/// ⌘⌫ on a row item of the window after `8 4 _` rebuilds the list; the digits (docs/spec/english-mixed.md §7.7) stay its last item, so the last lexicon item
/// is still a lexicon item.
#[test]
fn review_p3_1_forget_in_the_window_keeps_the_digits_item() {
    let mut e = eng();
    let o = run(&mut e, "8 4 _");
    let list = items(&mut e, &o);
    assert_eq!(list.last().map(String::as_str), Some("84"));
    assert!(list.len() > 5, "premise: row items before the lexicon's: {list:?}");
    let o = e.key(cmd_bs()).unwrap();
    let after = items(&mut e, &o);
    assert_eq!(after.last().map(String::as_str), Some("84"), "{after:?}");
    assert_eq!(after.len() + 1, list.len(), "one row item gone: {after:?}");
    // Choosing the word before the digits item is the lexicon's candidate, not the other reading.
    let at = after.iter().position(|w| w.chars().count() == 1 && w != "84").expect("a single-character lexicon item");
    let o = e.pick(at).unwrap().unwrap();
    assert_eq!(syls(&e), toks(&["ㄚˋ"]), "{:?}", o.preedit);
    assert_eq!(o.preedit, after[at]);
}

/// a mouse pick on the prediction row ends the "digits of the syllable that just closed" reading: the Space after it lists no `84`.
#[test]
fn review_p3_2_a_row_pick_clears_the_digits_of_the_last_syllable() {
    let mut e = eng();
    let o = run(&mut e, "8 4");
    assert_eq!(syls(&e), toks(&["ㄚˋ"]));
    assert!(!o.candidates.is_empty(), "premise: a prediction row");
    let word = o.candidates[0].clone();
    let o = e.pick(0).unwrap().unwrap();
    assert!(o.preedit.starts_with(&word));
    let o = run(&mut e, "_");
    assert!(!o.candidates.is_empty() && !o.candidates.contains(&"84".to_string()), "{:?}", o.candidates);
    assert_ne!(items(&mut e, &o).last().map(String::as_str), Some("84"));
}

/// a row item chosen after buffered digits is learned under the same key as after the digits became tokens (SENTINEL, the digits are
/// not Han), not under the word in front of them.
#[test]
fn review_p3_3_the_learning_key_of_an_item_after_buffered_digits() {
    let learned = |digits: &str| -> Vec<String> {
        let mut e = eng();
        e.set_learning(true);
        run(&mut e, &format!("{} {}", zh("ㄒㄧㄚˋ"), zh("ㄨˇ")));
        let o = run(&mut e, digits);
        assert!(!o.candidates.is_empty() && o.preedit.starts_with("下午"), "{:?}", o.preedit);
        run(&mut e, "Tab");
        run(&mut e, "Enter");
        run(&mut e, "Enter");
        e.learner().records().iter().map(|r| r.context.clone()).collect()
    };
    // The 2 is still in the buffer (shown as a digit): the item sits behind it.
    let buffered = learned("2");
    // Under the sentinel key a one-character item (the first item is one) teaches nothing, so the right answer here is no record at all.
    assert!(buffered.iter().all(|c| !c.starts_with("下午")), "the word before the digits is not the key: {buffered:?}");
}

/// ↑ is not in docs/spec/english-mixed.md §7.4's list of boundary keys; behind an unfinished syllable it changes nothing today, so it changes nothing in a run.
#[test]
fn review_p4_1_up_does_nothing_in_a_run() {
    let mut e = eng();
    let before = run(&mut e, "1 0");
    let o = run(&mut e, "Up");
    assert!(o.handled && o.commit.is_empty());
    assert_eq!((o.preedit.as_str(), o.cursor_utf16), (before.preedit.as_str(), before.cursor_utf16));
    assert!(syls(&e).is_empty(), "no token was made: {:?}", syls(&e));
    assert!(e.digits_typed().is_some(), "the run is still open");
    // The same key with the feature off, behind an unfinished syllable: handled, nothing changes.
    let mut off = make(true, false);
    let before = run(&mut off, "1 0");
    let o = run(&mut off, "Up");
    assert!(o.handled && o.commit.is_empty() && o.preedit == before.preedit);
}

/// the digits of a syllable that just closed do not survive turning the switch off and on (or the abbreviation composer on and off).
#[test]
fn review_p4_2_settings_clear_the_last_digits() {
    let list_after = |toggle: &dyn Fn(&mut Engine)| -> Vec<String> {
        let mut e = eng();
        run(&mut e, "8 4");
        toggle(&mut e);
        let o = run(&mut e, "_");
        items(&mut e, &o)
    };
    for (name, t) in [
        ("english off/on", Box::new(|e: &mut Engine| {
            e.set_english(false).unwrap();
            e.set_english(true).unwrap();
        }) as Box<dyn Fn(&mut Engine)>),
        ("abbreviation on/off", Box::new(|e: &mut Engine| {
            e.set_abbreviation(true).unwrap();
            e.set_abbreviation(false).unwrap();
            e.set_english(true).unwrap();
        })),
    ] {
        let l = list_after(&*t);
        assert!(!l.is_empty() && !l.contains(&"84".to_string()), "{name}: {l:?}");
    }
}

// ---------- docs/spec/english-mixed.md §7.5: Down, Enter and Shift+Enter end the run as the screen shows it ----------

/// docs/spec/english-mixed.md §7.5: `1` alone shows as ㄅ; Down opens the list of the unfinished ㄅ, the same one the engine without the feature opens.
#[test]
fn a12_down_on_a_lone_key_opens_the_list_of_the_unfinished_syllable() {
    let mut e = eng();
    let shown = run(&mut e, "1");
    assert_eq!(shown.preedit, "ㄅ", "premise: the screen shows the Zhuyin");
    let o = run(&mut e, "Down");
    let mut off = make(true, false);
    run(&mut off, "1");
    let want = run(&mut off, "Down");
    assert!(!want.candidates.is_empty(), "premise: the list of ㄅ");
    assert_eq!((o.candidates.clone(), o.total, o.selected, o.preedit.clone()), (want.candidates.clone(), want.total, want.selected, want.preedit.clone()));
    assert_eq!(items(&mut e, &o), items(&mut off, &want));
    assert!(syls(&e).is_empty(), "no digit token was made: {:?}", syls(&e));
}

/// docs/spec/english-mixed.md §7.5: `2` alone shows as ㄉ; Enter sends what the screen showed, not "2".
#[test]
fn a12_enter_on_a_lone_key_sends_what_the_screen_showed() {
    let mut e = eng();
    let shown = run(&mut e, "2");
    assert_eq!(shown.preedit, "ㄉ", "premise");
    let o = run(&mut e, "Enter");
    assert_eq!(o.commit, shown.preedit);
    assert_ne!(o.commit, "2");
    assert!(syls(&e).is_empty() && o.preedit.is_empty());
}

/// docs/spec/english-mixed.md §7.5: with t = 0 (all digits on the screen) nothing changes.
#[test]
fn a12_enter_on_digits_on_the_screen_sends_the_digits() {
    assert_eq!(typed("2 0 2 6 Enter"), "2026");
}

/// docs/spec/english-mixed.md §7.5: the screen shows digits and then a Zhuyin tail; Enter sends exactly that, Down turns the digits into tokens and lists the tail.
#[test]
fn a12_digits_then_a_tail_on_the_screen() {
    let mut e = eng();
    let shown = run(&mut e, "1 2 3 4 5");
    assert!(shown.preedit.starts_with("1234") && shown.preedit.ends_with('ㄓ'), "premise: digits then a tail: {:?}", shown.preedit);
    let o = run(&mut e, "Enter");
    assert_eq!(o.commit, shown.preedit);
    assert!(syls(&e).is_empty() && o.preedit.is_empty());

    let mut e = eng();
    let shown = run(&mut e, "1 2 3 4 5");
    let o = run(&mut e, "Down");
    assert_eq!(syls(&e), toks(&["_punct_1", "_punct_2", "_punct_3", "_punct_4"]), "the digit part became tokens");
    assert!(!o.candidates.is_empty(), "the list of the tail");
    assert!(o.preedit.starts_with("1234") && o.preedit.ends_with('ㄓ'), "{:?} (was {:?})", o.preedit, shown.preedit);
}

/// docs/spec/english-mixed.md §7.5: Shift+Enter sends the shown string (rule 12b) and passes the key through.
#[test]
fn a12_shift_enter_sends_the_shown_string_and_passes_through() {
    let mut e = eng();
    let shown = run(&mut e, "1 2 3 4 5");
    let o = run(&mut e, "S-Enter");
    assert_eq!(o.commit, shown.preedit);
    assert!(!o.handled);
    assert!(syls(&e).is_empty());
}

/// The digits goldens are made on macOS by reference/proto/golden_digits.py, and their bit-for-bit checks run only on macOS (the Windows
/// machine's WSL skips them). Their headers record the SHA-256 of every input file (models, lexicon, digits.tsv): a file changed since the
/// golden was made fails here on every platform, with the command that remakes it (docs/spec/decoder.md §2.3: overlay-add.tsv gained 7 rows and only
/// the macOS CI noticed).
#[test]
fn digits_goldens_were_made_from_the_current_inputs() {
    let lexicon_files = ["mcbpmf-data.txt", "overlay-add.tsv", "sandhi-add.tsv", "names-add.tsv", "demote.tsv", "variant-order.tsv"];
    for g in ["digits-score.txt", "digits-pred.txt", "digits-reorder.txt"] {
        let text = std::fs::read_to_string(root().join("eval/golden").join(g)).unwrap();
        let mut seen = Vec::new();
        for line in text.lines().take_while(|l| l.starts_with('#')) {
            let toks: Vec<&str> = line.trim_start_matches('#').split_whitespace().collect();
            for w in toks.windows(2) {
                let Some(want) = w[1].strip_prefix("sha256=") else { continue };
                let dir = if line.starts_with("# models:") { "data/lm" } else { "data/lexicon" };
                let got = core::eval::sha256_hex(&std::fs::read(root().join(dir).join(w[0])).unwrap());
                assert_eq!(
                    got, want,
                    "eval/golden/{g} was made from another {dir}/{}: remake all three on macOS with `python3 reference/proto/golden_digits.py --table \
                     data/lexicon/digits.tsv --theta 3 --out eval/golden/digits-score.txt`, the same with `--pred --out eval/golden/digits-pred.txt` and with `--reorder --out eval/golden/digits-reorder.txt`",
                    w[0]
                );
                seen.push(w[0].to_string());
            }
        }
        for f in lexicon_files.iter().chain(&["digits.tsv", "bigram.sjlm", "classes.sjc", "kn.sjkn"]) {
            assert!(seen.iter().any(|s| s == f), "eval/golden/{g}: the header records no hash of {f}");
        }
    }
}

// ---------- reversal-digits (docs/spec/english-mixed.md §4.2, §7.3, §7.4): the engine as the App builds it ----------
//
// The tests above build `Engine::with_lexicon` (no packs, nothing the App adds); these use `Engine::new` + `load_lm` and the App's defaults for English
// mixing (mixing on, overwrite off, key order on, reversal guard on).

fn app() -> Engine {
    let mut e = Engine::new(&root().join("data/lexicon"), Layout::Standard).unwrap();
    e.load_lm(&root().join("data/lm/bigram.sjlm")).unwrap();
    e.set_english(true).unwrap();
    e.set_english_overwrite(false).unwrap();
    e
}

/// What one key left on the screen: the preedit, then `｜`, then the composition's units (blank separated).
fn shot(e: &Engine, o: &Output) -> String {
    format!("{}｜{}", o.preedit, e.syllables().join(" "))
}

/// A fresh composition after `left` (the text before the cursor), the script's keys; the shot after each key.
fn shots(e: &mut Engine, left: &str, script: &str) -> Vec<String> {
    e.reset(ResetMode::Discard);
    e.set_left_context(left);
    let mut out = Vec::new();
    for t in script.split_whitespace() {
        let o = send(e, t);
        out.push(shot(e, &o));
    }
    out
}

/// The preedits only, and everything committed along the way.
fn screens(e: &mut Engine, left: &str, script: &str) -> (Vec<String>, String) {
    e.reset(ResetMode::Discard);
    e.set_left_context(left);
    let (mut pre, mut commit) = (Vec::new(), String::new());
    for t in script.split_whitespace() {
        let o = send(e, t);
        commit.push_str(&o.commit);
        pre.push(o.preedit);
    }
    (pre, commit)
}

/// A hurried syllable (two keys swapped) that starts on a digit-alphabet key reads as the syllable the person meant, every key's screen included.
/// Pinned, not wished away: the three at the sentence start that stay numbers (the next test).
#[test]
fn hurried_syllables_are_read_as_typed_in_order() {
    let mut e = app();
    for (left, keys, want, units) in [
        ("", ". u 3", vec![".", ".ㄧ", "有"], "ㄧㄡˇ"),
        ("我", ". u 3", vec!["ㄡ", ".ㄧ", "有"], "ㄧㄡˇ"),
        ("", ". w 6", vec![".", ".ㄊ", "頭"], "ㄊㄡˊ"),
        ("", "8 w _", vec!["8", "8ㄊ", "他"], "ㄊㄚ"),
        ("", "5 / j _", vec!["ㄓ", "ㄓㄥ", "5/ㄨ", "中"], "ㄓㄨㄥ"),
        ("", "/ 5 4", vec!["/", "/5", "正"], "ㄓㄥˋ"),
        ("", "8 q 6", vec!["8", "8ㄆ", "爬"], "ㄆㄚˊ"),
        ("", "2 i j 6", vec!["ㄉ", "2ㄛ", "2ㄨㄛ", "奪"], "ㄉㄨㄛˊ"),
        ("", "q 6 8", vec!["ㄆ", "q6", "爬"], "ㄆㄚˊ"), // T: the tone key came a place early
    ] {
        let (pre, _) = screens(&mut e, left, keys);
        assert_eq!(pre, want, "{keys} after {left:?}");
        assert_eq!(e.syllables(), [units.to_string()], "{keys} after {left:?}");
        assert!(e.english_reverted().is_none(), "{keys}: the last key reverted");
    }
    // After a syllable of the same composition (the text before the run is the composition's own).
    let mut e = app();
    let (pre, _) = screens(&mut e, "", "j i 3 . u 3");
    assert_eq!(pre.last().unwrap(), "我有");
    assert_eq!(e.syllables(), ["ㄨㄛˇ", "ㄧㄡˇ"]);
}

/// Known and pinned (docs/spec/english-mixed.md §14): at the sentence start the context says digits, the price of the column order is too high.
#[test]
fn hurried_syllables_that_stay_numbers_at_the_sentence_start() {
    let mut e = app();
    for (keys, last, units) in [("9 y 4", "9自", "_punct_9 ㄗˋ"), ("2 0 u 4", "20意", "_punct_2 _punct_0 ㄧˋ"), ("0 1 4", "014", "")] {
        let (pre, _) = screens(&mut e, "", keys);
        assert_eq!(pre.last().unwrap(), last, "{keys}");
        assert_eq!(e.syllables().join(" "), units, "{keys}");
    }
}

/// A number and the Chinese after it stay a number and Chinese (what the column order must not eat).
#[test]
fn a_number_before_chinese_stays_a_number() {
    let mut e = app();
    for (keys, text) in [
        ("8 s u 0 6", "8年"), ("8 e k 7", "8個"), ("9 2 u 0 3", "9點"), ("2 0 2 6 s u 0 6", "2026年"), ("8 g 4", "8是"), ("9 h 4", "9次"), ("3 . 5", "3.5"),
        ("9 5 5 k 6", "95折"), (". . .", "..."), ("- -", "--"), ("1 / 2", "1/2"),
    ] {
        let (pre, _) = screens(&mut e, "", keys);
        assert_eq!(pre.last().unwrap(), text, "{keys}");
    }
    // The four keys of a year are still in the run's buffer; Enter sends them.
    let (_, commit) = screens(&mut e, "", "1 9 8 4 Enter");
    assert_eq!(commit, "1984");
}

/// A is the guard's: with `english_key_order` off and the guard on, `. u 3` and `8 w ␣` still read as the syllable; T needs the revert that the switch
/// turns off, so `q 6 8` does what it did before (the fixture below holds that line).
#[test]
fn the_rejoin_does_not_depend_on_the_key_order_switch() {
    let mut e = app();
    e.set_english_key_order(false).unwrap();
    for (keys, want) in [(". u 3", "有"), ("8 w _", "他")] {
        let (pre, _) = screens(&mut e, "", keys);
        assert_eq!(pre.last().unwrap(), want, "{keys} with the key-order switch off");
    }
}

/// The baseline's outputs (the engine before this change) for the scripts of this feature, per key: `preedit|units|commit|candidates|run length`
/// (`core/tests/fixtures/reversal-guard-off.tsv`, made at 98385de9 with the same `app()`). With the guard off every key is the baseline's, with the key order on
/// and off; with the guard on and the key order off the `q 6 8` family (T needs the key-order revert, which that switch turns off); with both on the five
/// lines T must not turn into Chinese (`q 6 g`, `q 6 m`, `S-A 3 o`, `c p ␣`, `q 6 z 8`).
#[test]
fn with_the_guard_off_every_key_is_the_baselines() {
    let text = std::fs::read_to_string(root().join("core/tests/fixtures/reversal-guard-off.tsv")).unwrap();
    let (mut lines, mut keys) = (0, 0);
    let mut e = app();
    for l in text.lines() {
        let f: Vec<&str> = l.split('\t').collect();
        let (guard, key_order) = match f[0] {
            "guard_off_key_order_on" => (false, true),
            "guard_off_key_order_off" => (false, false),
            "guard_on_key_order_off" => (true, false),
            "guard_on_key_order_on_t_negatives" => (true, true),
            c => panic!("unknown configuration {c}"),
        };
        e.set_english_reversal_guard(guard).unwrap();
        e.set_english_key_order(key_order).unwrap();
        e.reset(ResetMode::Discard);
        e.set_left_context(f[1]);
        for (tok, want) in f[2].split_whitespace().zip(&f[3..]) {
            let o = send(&mut e, tok);
            let got = format!("{}|{}|{}|{}|{}", o.preedit, e.syllables().join(" "), o.commit, o.candidates.join(","), o.english_run_utf16_len);
            assert_eq!(&got, want, "{} {:?} key {tok} of {}", f[0], f[1], f[2]);
            keys += 1;
        }
        lines += 1;
    }
    assert!(lines > 50 && keys > 250, "{lines} lines, {keys} keys");
}

/// Rule 1 and rule 5q pass a key on without touching anything: the late tokens survive it, the syllable is read as if it had not been pressed.
#[test]
fn a_key_that_is_passed_on_keeps_the_late_tokens() {
    let mut e = app();
    let plain: Vec<Output> = {
        e.reset(ResetMode::Discard);
        ". u 3".split_whitespace().map(|t| send(&mut e, t)).collect()
    };
    let plain_last = line(plain.last().unwrap());
    for pass in ["Cmd-c", "PgDn"] {
        e.reset(ResetMode::Discard);
        let mut outs: Vec<(Output, String)> = Vec::new();
        for t in format!(". u {pass} 3").split_whitespace() {
            let o = send(&mut e, t);
            let s = shot(&e, &o);
            outs.push((o, s));
        }
        assert_eq!(outs[1].1, ".ㄧ｜_punct_.", "{pass}");
        let third = &outs[2];
        assert!(!third.0.handled && third.0.commit.is_empty(), "{pass} is passed on");
        assert_eq!(third.1, outs[1].1, "{pass}: the screen and the units are the second key's");
        assert_eq!(line(&outs[3].0).split('|').skip(1).collect::<Vec<_>>(), plain_last.split('|').skip(1).collect::<Vec<_>>(), "{pass}: the fourth key");
        assert_eq!(outs[3].1, "有｜ㄧㄡˇ", "{pass}");
    }
}

/// Every other key lets the late tokens go: after the third key `late` is empty and the tone key reads what is on the screen. Each row is the keys of
/// `. u X 3` and the screen and units after each (the baseline's, measured at 98385de9).
#[test]
fn any_other_key_makes_the_late_tokens_final() {
    let mut e = app();
    for (script, want) in [
        (". u BS 3", vec![".｜", ".ㄧ｜_punct_.", ".｜_punct_.", ".3｜_punct_. _punct_3"]),
        (". u Left Right 3", vec![".｜", ".ㄧ｜_punct_.", ".ㄧ｜_punct_.", ".ㄧ｜_punct_.", ".以｜_punct_. ㄧˇ"]),
        (". u Esc 3", vec![".｜", ".ㄧ｜_punct_.", ".｜_punct_.", ".3｜_punct_. _punct_3"]),
        (". u [ 3", vec![".｜", ".ㄧ｜_punct_.", ".「｜_punct_. _punct_「", ".「3｜_punct_. _punct_「 _punct_3"]),
        (". u S-1 3", vec![".｜", ".ㄧ｜_punct_.", ".！｜_punct_. _punct_！", ".！3｜_punct_. _punct_！ _punct_3"]),
        (". u S-A 3", vec![".｜", ".ㄧ｜_punct_.", ".uA｜_punct_.", ".uA3｜_punct_."]),
        (". u @commit 3", vec![".｜", ".ㄧ｜_punct_.", "｜", "3｜_punct_3"]),
    ] {
        let got = shots(&mut e, "", script);
        assert_eq!(got, want, "{script}");
        // `late` is empty after the third key (the one before the tone key; for the arrow keys the key after the pair).
        let mut f = app();
        f.reset(ResetMode::Discard);
        let toks: Vec<&str> = script.split_whitespace().collect();
        for t in &toks[..toks.len() - 1] {
            send(&mut f, t);
        }
        assert_eq!(f.late_keys(), "", "{script}: late tokens left");
    }
    // The run of an English letter keeps its length through the tone key (the engine's English run, not the digit layer).
    let mut e = app();
    e.reset(ResetMode::Discard);
    let lens: Vec<u32> = ". u S-A 3".split_whitespace().map(|t| send(&mut e, t).english_run_utf16_len).collect();
    assert_eq!(lens, [0, 0, 2, 3]);
}

/// Two late keys and the next key only hits the first one's column: `1 . u s ␣` (the baseline's last key gives `1.us`).
#[test]
fn two_late_keys_and_the_next_key_hits_one_column() {
    let mut e = app();
    let got = shots(&mut e, "", "1 . u s _");
    assert_eq!(
        got,
        ["ㄅ｜", "1.｜", "1.ㄧ｜_punct_1 _punct_.", "1.ㄋㄧ｜_punct_1 _punct_.", "1妞｜_punct_1 ㄋㄧㄡ"]
    );
    // `late` after the `s`: only the `.` is left (the `1` is a digit for good).
    e.reset(ResetMode::Discard);
    for t in ["1", ".", "u"] {
        send(&mut e, t);
    }
    assert_eq!(e.late_keys(), "1.");
    send(&mut e, "s");
    assert_eq!(e.late_keys(), ".");
}

/// 127 units in the composition and a late token that is the 128th unit: the key that makes it sends the whole composition, nothing is lost and the
/// syllable is not rejoined (the token is no longer in the composition). 126 units: the rejoin works.
#[test]
fn the_128th_unit_is_sent_and_not_taken_back() {
    let wo = |n: usize| vec!["j i 3"; n].join(" ");
    for (keys, base, tail_units, tail) in [(". u 3", ".", ["ㄧˇ"], "以"), ("8 w _", "8", ["ㄊ"], "ㄊ")] {
        let mut e = app();
        e.reset(ResetMode::Discard);
        let (mut sent, mut last) = (String::new(), String::new());
        for t in format!("{} {keys}", wo(127)).split_whitespace() {
            let o = send(&mut e, t);
            sent.push_str(&o.commit);
            last = o.preedit;
        }
        // Nothing lost: what was sent, then what is on the screen, is 127 times 我 and the keys as the syllable (not) rejoined.
        assert_eq!(format!("{sent}{last}"), format!("{}{base}{tail}", "我".repeat(127)), "{keys}");
        assert_eq!(e.syllables(), tail_units, "{keys}: the syllable is not rejoined");
    }
    // One unit fewer: the token is not the 128th and goes back into the syllable.
    for (keys, unit) in [(". u 3", "ㄧㄡˇ"), ("8 w _", "ㄊㄚ")] {
        let mut e = app();
        e.reset(ResetMode::Discard);
        for t in format!("{} {keys}", wo(126)).split_whitespace() {
            send(&mut e, t);
        }
        assert_eq!(e.syllables().last().map(String::as_str), Some(unit), "{keys} after 126 units");
        assert_eq!(e.syllables().len(), 127);
    }
}

/// The cursor is in the middle of the composition: the digit run opens at the cursor, the tokens sit next to it, and the syllable is rejoined there
/// (the right-hand `們` does not move). Screens and units are the measured ones; the baseline stops at `我.以們` and `我8ㄊ們`.
#[test]
fn the_cursor_in_the_middle() {
    let mut e = app();
    assert_eq!(
        shots(&mut e, "", "j i 3 a p 7 Left . u 3")[6..],
        ["我們｜ㄨㄛˇ ㄇㄣ˙", "我ㄡ們｜ㄨㄛˇ ㄇㄣ˙", "我.ㄧ們｜ㄨㄛˇ _punct_. ㄇㄣ˙", "我有們｜ㄨㄛˇ ㄧㄡˇ ㄇㄣ˙"]
    );
    assert_eq!(
        shots(&mut e, "", "j i 3 a p 7 Left 8 w _")[6..],
        ["我們｜ㄨㄛˇ ㄇㄣ˙", "我ㄚ們｜ㄨㄛˇ ㄇㄣ˙", "我8ㄊ們｜ㄨㄛˇ _punct_8 ㄇㄣ˙", "我他們｜ㄨㄛˇ ㄊㄚ ㄇㄣ˙"]
    );
}

/// docs/spec/english-mixed.md §13.1 again with the App's engine: no reversed key order of any lexicon syllable opens an English run or leaves an English
/// token, and the numbers that the scores still choose stay under the bound the constants give (410 at `RHO` 7, `RHO_SYM` 4: measured 372, plus 10% rounded up).
/// The sibling of `every_reversed_key_order_of_every_syllable_is_never_english` in `engine_english.rs`, which has no digit layer.
#[test]
fn every_reversed_key_order_is_never_an_english_run_with_the_digit_layer_on() {
    let lex = load_lexicon(&root().join("data/lexicon")).unwrap();
    let mut e = app();
    let seen: std::collections::BTreeSet<&str> = (0..lex.reading_count()).flat_map(|r| lex.reading_syllables(r)).collect();
    let (mut typed, mut english, mut digits) = (0usize, Vec::new(), 0usize);
    for syl in &seen {
        let mut keys: Vec<String> = Vec::new();
        let mut tone = "_".to_string();
        for c in syl.chars() {
            match Layout::Standard.key_of_tone(c) {
                Some(t) => tone = t.to_string(),
                None => match Layout::Standard.key_of_symbol(c) {
                    Some(k) => keys.push(k.to_string()),
                    None => keys.clear(),
                },
            }
        }
        let perms: Vec<Vec<usize>> = match keys.len() {
            2 => vec![vec![1, 0]],
            3 => vec![vec![0, 2, 1], vec![1, 0, 2], vec![1, 2, 0], vec![2, 0, 1], vec![2, 1, 0]],
            _ => continue,
        };
        for p in perms {
            let script = p.iter().map(|&i| keys[i].as_str()).chain([tone.as_str()]).collect::<Vec<_>>().join(" ");
            e.reset(ResetMode::Discard);
            let mut reverted = false;
            for t in script.split_whitespace() {
                send(&mut e, t);
                reverted |= e.english_reverted().is_some();
            }
            typed += 1;
            let units = e.syllables();
            if reverted || units.iter().any(|u| u.starts_with("_punct_") && u.chars().count() > 8) {
                english.push(format!("{syl}: {script}"));
            } else if units != [syl.to_string()] {
                digits += 1;
            }
        }
    }
    println!("typed {typed} reversed orders; {} English, {digits} not the syllable (digits)", english.len());
    assert!(typed > 3000, "only {typed} reversed orders");
    assert!(english.is_empty(), "{} English, first {:?}", english.len(), &english[..english.len().min(8)]);
    assert!(digits <= 410, "{digits} reversed orders stay numbers: the constants lost ground");
}

/// The tool hook `set_reorder_penalty`: not called, or called with the constants, every key is the same.
#[test]
fn the_penalty_hook_with_the_constants_changes_nothing() {
    for script in [". u 3", "8 w _", "8 g 4", "0 1 4", "5 / j _", "2 0 2 6 8 w _"] {
        let (mut a, mut b) = (app(), app());
        b.set_reorder_penalty(core::digits::RHO, core::digits::RHO_SYM);
        assert_eq!(shots(&mut a, "", script), shots(&mut b, "", script), "{script}");
    }
}

// ---------- the golden of the column-order reading and of the rejoin test (eval/golden/digits-reorder.txt) ----------

struct LateRow {
    ctx: String,
    before: String,
    keys: String,
    pend: String,
    closing: char,
    rho: f64,
    rho_sym: f64,
    take: Option<usize>,
    merge: bool,
    s_z: Option<f64>,
    s_d: Option<f64>,
    left: Option<String>,
    script: Option<String>,
}

fn golden_late() -> Vec<LateRow> {
    let text = std::fs::read_to_string(root().join("eval/golden/digits-reorder.txt")).unwrap();
    let none = |s: &str| if s == "∅" { String::new() } else { s.to_string() };
    let opt = |s: &str| (s != "∅").then(|| s.to_string());
    text.lines()
        .filter(|l| l.starts_with("L\t"))
        .map(|l| {
            let f: Vec<&str> = l.split('\t').skip(1).collect();
            let get = |name: &str| f.iter().find_map(|x| x.strip_prefix(name)).unwrap_or_else(|| panic!("no {name} in {l}"));
            LateRow {
                ctx: get("ctx=").to_string(),
                before: none(get("before=")),
                keys: get("keys=").to_string(),
                pend: none(get("pend=")),
                closing: if get("closing=") == "␣" { ' ' } else { get("closing=").chars().next().unwrap() },
                rho: get("rho=").parse().unwrap(),
                rho_sym: get("rho_sym=").parse().unwrap(),
                take: opt(get("take=")).map(|t| t.parse().unwrap()),
                merge: get("merge=") == "1",
                s_z: opt(get("s_z=")).map(|t| t.parse().unwrap()),
                s_d: opt(get("s_d=")).map(|t| t.parse().unwrap()),
                left: f.iter().find_map(|x| x.strip_prefix("left=")).map(|x| none(x)),
                script: f.iter().find_map(|x| x.strip_prefix("script=")).map(String::from),
            }
        })
        .collect()
}

fn bits(a: Option<f64>) -> Option<u64> {
    a.map(f64::to_bits)
}

/// The `decide` rows of the new golden (the reversal guard on: the column-order split) and the `late_decision` rows, both against Python's bits.
/// macOS only, like the other f64 goldens.
#[test]
fn reorder_scores_match_the_python_golden_bit_for_bit() {
    if !cfg!(target_os = "macos") {
        return;
    }
    let rows = golden_rows("digits-reorder.txt");
    assert_eq!(rows.len(), 9);
    compare_scores(&rows, 20);
    let late = golden_late();
    assert!(late.len() >= 26, "{} late rows", late.len());
    let mut e = eng();
    for (n, r) in late.iter().enumerate() {
        let got = e.late_decision(&r.ctx, &r.before, &r.keys, &r.pend, r.closing, r.rho, r.rho_sym).expect("digit layer");
        let at = format!("late row {n}: keys {} pend {} closing {:?} before {}", r.keys, r.pend, r.closing, r.before);
        assert_eq!((got.0, got.1), (r.take, r.merge), "{at}: take, merge");
        assert_eq!(bits(got.2), bits(r.s_z), "{at}: s_z {:?} vs {:?}", got.2, r.s_z);
        assert_eq!(bits(got.3), bits(r.s_d), "{at}: s_d {:?} vs {:?}", got.3, r.s_d);
    }
}

/// What the engine hands to `late_decision` is what the golden's rows say, for each row with a `script`: the context, the digits before, the late keys,
/// the unfinished syllable in column order, the closing key and the two penalties; and what comes back. This is the test that stops a right function
/// from being fed the wrong things.
#[test]
fn late_inputs_in_the_engine_are_the_golden_rows() {
    let late = golden_late();
    let mut seen = 0;
    let mut e = app();
    e.set_late_trace(true);
    for (n, r) in late.iter().enumerate() {
        let (Some(left), Some(script)) = (&r.left, &r.script) else { continue };
        e.reset(ResetMode::Discard);
        e.take_late_trace();
        e.set_left_context(left);
        for t in script.split_whitespace() {
            send(&mut e, &t.replace('␣', "_"));
        }
        let ev = e.take_late_trace();
        let at = format!("late row {n} (script {script})");
        assert_eq!(ev.len(), 1, "{at}: one rejoin test");
        let ev = &ev[0];
        assert_eq!((ev.ctx.as_str(), ev.before.as_str(), ev.keys.as_str(), ev.pend.as_str(), ev.closing), (r.ctx.as_str(), r.before.as_str(), r.keys.as_str(), r.pend.as_str(), r.closing), "{at}: inputs");
        assert_eq!((ev.rho, ev.rho_sym), (r.rho, r.rho_sym), "{at}: penalties");
        assert_eq!((ev.result.0, ev.result.1), (r.take, r.merge), "{at}: take, merge");
        if cfg!(target_os = "macos") {
            assert_eq!((bits(ev.result.2), bits(ev.result.3)), (bits(r.s_z), bits(r.s_d)), "{at}: scores");
        }
        seen += 1;
    }
    assert!(seen >= 17, "{seen} rows with a script");
}
