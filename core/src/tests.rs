use crate::eval::*;
use crate::*;

fn lex(s: &str) -> Lexicon {
    Lexicon::parse(s).unwrap()
}
fn v(s: &[&str]) -> Vec<String> {
    s.iter().map(|x| x.to_string()).collect()
}
fn top(l: &Lexicon, syls: &[&str]) -> Vec<String> {
    decode(l, &v(syls), &mut NoLearning).unwrap().iter().map(|(_, w)| w.concat()).collect()
}

#[test]
fn lexicon_parse_rules() {
    let l = lex("# c\n_ skip\na 甲 -1.0\nb 乙\na-b 甲乙 -2 extra\na-b 甲 -1\na-b 甲乙 -2.0\na 丙 -3\na 丁 -1.0\na 甲 -1.0\n");
    // comment, underscore, wrong column count, length mismatch skipped
    assert_eq!(l.by_reading.len(), 2); // only a and a-b survive
    assert_eq!(l.by_reading[&v(&["a", "b"])].len(), 1);
    assert_eq!(l.max_len, 2);
    // duplicates kept, stable sort by score desc: 甲 -1, 丁 -1, 甲 -1 (file order), 丙 -3
    let a: Vec<&str> = l.by_reading[&v(&["a"])].iter().map(|(w, _)| w.as_str()).collect();
    assert_eq!(a, ["甲", "丁", "甲", "丙"]);
    // by_word: first occurrence wins on a tie
    assert_eq!(l.by_word["甲"].1, -1.0);
}

#[test]
fn by_word_strictly_greater_replaces() {
    let l = lex("a 甲 -2.0\nb 甲 -1.0\nc 甲 -1.0\n");
    assert_eq!(l.by_word["甲"].0, v(&["b"]));
}

#[test]
fn to_syllables_strict_greater_and_none() {
    let l = lex("a 甲 -1.0\nb 乙 -1.0\na-b 甲乙 -2.0\n");
    // tie between 甲+乙 and 甲乙: first found (L=1 at i=1, then at i=2 L=1 -> -2.0; L=2 -2.0 not strictly greater)
    assert_eq!(l.to_syllables("甲乙").unwrap(), v(&["a", "b"]));
    assert!(l.to_syllables("甲丙").is_none());
}

/// Fails if sorting becomes unstable or a replaced candidate moves to the end.
#[test]
fn decode_tie_and_replacement_order() {
    let l = lex("a 甲 -1.0\na 丙 -1.5\nb 乙 -1.0\nb 丁 -1.0\na-b 丙乙 -2.0\n");
    // all of 甲乙, 丙乙(replaced in place), 甲丁 score -2.0; insertion order decides.
    assert_eq!(top(&l, &["a", "b"]), ["甲乙", "丙乙", "甲丁", "丙丁"]);
    // swapping file order of tied words flips the winner (stable by_reading sort)
    let l2 = lex("a 甲 -1.0\nb 丁 -1.0\nb 乙 -1.0\n");
    assert_eq!(top(&l2, &["a", "b"])[0], "甲丁");
}

#[test]
fn beam_truncates_to_32() {
    let s: String = (0..12).map(|i| format!("a {} -1.0\n", char::from_u32(0x4e00 + i).unwrap())).collect();
    let l = lex(&s);
    let n = decode(&l, &v(&["a", "a", "a"]), &mut NoLearning).unwrap().len();
    assert_eq!(n, BEAM);
}

#[test]
fn replacement_needs_strictly_greater_score() {
    // 甲+乙 and 甲乙 tie at -2.0; the first-inserted split must survive (contract §4, strict >).
    // The surface string is identical either way, so only the word split shows it — and learners key on it.
    let l = lex("a 甲 -1.0\nb 乙 -1.0\na-b 甲乙 -2.0\n");
    assert_eq!(decode(&l, &v(&["a", "b"]), &mut NoLearning).unwrap()[0].1, ["甲", "乙"]);
}

#[test]
fn per_key_expands_twelve_words() {
    let text: String = "一二三四五六七八九十百千萬".chars().enumerate()
        .map(|(i, c)| format!("a {c} -{}.0\n", i + 1)).collect();
    assert_eq!(decode(&lex(&text), &v(&["a"]), &mut NoLearning).unwrap().len(), PER_KEY);
    assert_eq!(PER_KEY, 12);
}

#[test]
fn learners_follow_prototype() {
    let l = lex("a 甲 -1.0\na 乙 -2.0\n");
    let key = v(&["a"]);
    let mut g = GlobalBoost::new(&l);
    g.observe("<s>", &key, "乙");
    assert!((g.bonus("x", &key, "乙") - 1.01).abs() < 1e-12);
    let mut c = ContextKeyed::new(&l);
    c.observe("p", &key, "乙");
    assert_eq!(c.bonus("q", &key, "乙"), 0.0);
    assert!(c.bonus("p", &key, "乙") > 1.0);
    let mut p = Promotion::new(&l);
    p.observe("", &key, "乙");
    assert_eq!(p.bonus("", &key, "乙"), 0.5);
    p.observe("", &key, "乙");
    p.observe("", &key, "乙");
    assert!((p.bonus("", &key, "乙") - 1.01).abs() < 1e-12);
}

#[test]
fn python_round_and_repr() {
    assert_eq!(pyround(1.0, 3), "1.0");
    assert_eq!(pyround(0.0, 3), "0.0");
    assert_eq!(pyround(41.0 / 64.0, 3), "0.641");
    assert_eq!(pyround(0.5, 1), "0.5");
    // 0.125 is exactly representable: Python round(0.125, 2) == 0.12 (half-even)
    assert_eq!(pyround(0.125, 2), "0.12");
    // 2.675 is below the tie in binary: Python round(2.675, 2) == 2.67
    assert_eq!(pyround(2.675, 2), "2.67");
}

#[test]
fn lenient_comparison() {
    assert_eq!(lenient("她妳它牠嘗周臺裏"), "他你他他嚐週台裡");
    assert_eq!(lenient("abc甲"), "abc甲");
}

#[test]
fn three_column_rows_and_check_readings() {
    let l = lex("a 甲 -1.0\nb 乙 -1.0\n");
    let rows = parse_rows("# c\n\n|甲乙\nctx|甲乙|a b\n|甲乙|b a\n").unwrap();
    assert_eq!(rows.len(), 3);
    assert!(rows[0].reading.is_none());
    assert_eq!(rows[1].ctx, "ctx");
    assert_eq!(rows[1].reading, Some(v(&["a", "b"])));
    // second reading disagrees with to_syllables: caught
    assert_eq!(check_readings(&l, &rows), (2, 1));
    // confirmed reading is used as-is (not run through to_syllables)
    let (m, _) = evaluate(&l, &rows[1..2], |_| {}).unwrap();
    assert_eq!(m.sent_acc, 1.0);
    // rows with a reading survive the usable() filter even if to_syllables fails
    let r = parse_rows("|丙|a\n").unwrap();
    assert_eq!(usable(&l, r).len(), 1);
}

#[test]
fn r2_errors_never_contain_input() {
    const MARK: &str = "ZQXMARKER";
    // bad score token carrying the marker, with a marker reading and word as well
    let e = Lexicon::parse(&format!("{MARK} {MARK} {MARK}\n")).err().unwrap();
    assert!(!format!("{e} {e:?}").contains(MARK));
    assert!(!e.to_string().contains(MARK));
    assert!(!lex_err_text(&format!("{MARK}-{MARK} 甲乙 {MARK}")).contains(MARK));
    // empty lexicon
    assert!(!Lexicon::parse("").err().unwrap().to_string().contains(MARK));
    // decode with an unknown syllable
    let l = lex("a 甲 -1.0\n");
    let e = decode(&l, &v(&[MARK]), &mut NoLearning).unwrap_err();
    assert!(!e.to_string().contains(MARK));
    // row with no separator
    let e = parse_rows(&format!("{MARK}\n")).err().unwrap();
    assert!(!e.to_string().contains(MARK));
    // reading failure on a sentence
    let e = row_syllables(&l, &parse_rows(&format!("|{MARK}\n")).unwrap()[0]).unwrap_err();
    assert!(!e.to_string().contains(MARK));
}

fn lex_err_text(s: &str) -> String {
    Lexicon::parse(s).err().map(|e| e.to_string()).unwrap_or_default()
}

#[test]
fn overlay_duplicate_of_base_entry_is_error() {
    let e = Lexicon::parse_with("a 甲 -1.0\n", Some("a\t甲\t-2.0\twikt\n")).err().unwrap();
    assert_eq!(e, Error::OverlayDuplicate { word_len: 1 });
    assert!(!e.to_string().contains('甲'));
}

#[test]
fn overlay_tie_sorts_base_first_then_file_order() {
    let l = Lexicon::parse_with("a 甲 -1.0\na 丙 -3.0\n", Some("a\t乙\t-1.0\twikt\na\t丁\t-1.0\twikt\na\t戊\t-0.5\twikt\n")).unwrap();
    let a: Vec<&str> = l.by_reading[&v(&["a"])].iter().map(|(w, _)| w.as_str()).collect();
    assert_eq!(a, ["戊", "甲", "乙", "丁", "丙"]);
}
