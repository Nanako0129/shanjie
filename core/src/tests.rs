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
    assert_eq!(l.reading_count(), 2); // only a and a-b survive
    assert_eq!(l.entries(&v(&["a", "b"])).len(), 1);
    assert_eq!(l.max_len, 2);
    // duplicates kept, stable sort by score desc: 甲 -1, 丁 -1, 甲 -1 (file order), 丙 -3
    let a: Vec<&str> = l.entries(&v(&["a"])).iter().map(|(w, _)| *w).collect();
    assert_eq!(a, ["甲", "丁", "甲", "丙"]);
    // by_word: first occurrence wins on a tie
    assert_eq!(l.word_info("甲").unwrap().1, -1.0);
}

#[test]
fn by_word_strictly_greater_replaces() {
    let l = lex("a 甲 -2.0\nb 甲 -1.0\nc 甲 -1.0\n");
    assert_eq!(l.word_info("甲").unwrap().0, v(&["b"]));
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

/// docs/contracts/forms-score.md section 1.3: word pairs first, then single characters where the row syllable is in the
/// pair's reading condition; the order is observable on a table where each step undoes the other.
#[test]
fn forms_apply_order_and_reading_condition() {
    let f = Forms::parse("# h\nword\t溼地\t濕地\nchar\t濕\t溼\tㄕ,ㄕ˙\n").unwrap();
    // word step first: 濕地 is not a word key, so the char step makes 溼地; the other order would end at 濕地
    assert_eq!(f.apply("濕地", &v(&["ㄕ", "ㄉㄧˋ"])), "溼地");
    assert_eq!(f.apply("溼地", &v(&["ㄕ", "ㄉㄧˋ"])), "溼地");
    // the reading condition decides per position; a syllable count that differs from the sentence skips the char step only
    assert_eq!(f.apply("濕", &v(&["ㄒㄧˊ"])), "濕");
    assert_eq!(f.apply("濕", &v(&["ㄕ˙"])), "溼");
    assert_eq!(f.apply("濕a", &v(&["ㄕ"])), "濕a");
    assert_eq!(f.apply("溼地a", &v(&["ㄕ"])), "濕地a");
    // malformed, duplicate or multi-character `char` lines are errors carrying only the line number
    for bad in ["word\t甲\n", "char\t濕\t溼\n", "char\t濕濕\t溼\tㄕ\n", "char\t濕\t溼\t\n", "other\ta\tb\n", "word\ta\tb\nword\ta\tc\n", "char\t濕\t溼\tㄕ\nchar\t濕\t沉\tㄕ\n"] {
        assert!(matches!(Forms::parse(bad), Err(Error::BadForms { .. })), "{bad:?}");
    }
}

/// The shipped table (eval/forms.tsv) on the contract's cases (section 3.4), judged as the scorer does: Lenient, then Forms.
#[test]
fn forms_shipped_table_cases() {
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..");
    let f = Forms::parse(&std::fs::read_to_string(root.join("eval/forms.tsv")).unwrap()).unwrap();
    let l = Lenient::parse(&std::fs::read_to_string(root.join("eval/variants.tsv")).unwrap()).unwrap();
    let same = |a: &str, b: &str, syls: &[&str]| f.apply(&l.apply(a), &v(syls)) == f.apply(&l.apply(b), &v(syls));
    assert!(same("潮濕", "潮溼", &["ㄔㄠˊ", "ㄕ"]));
    assert!(!same("潮濕", "潮溼", &["ㄔㄠˊ", "ㄒㄧˊ"]), "濕 read ㄒㄧˊ is not swapped");
    assert!(!same("沈", "沉", &["ㄕㄣˇ"]), "surname 沈 stays strict");
    assert!(same("沈", "沉", &["ㄔㄣˊ"]));
    assert!(same("姐姐", "姊姊", &["ㄐㄧㄝˇ", "ㄐㄧㄝ˙"]));
    assert!(!same("小姐", "小姊", &["ㄒㄧㄠˇ", "ㄐㄧㄝˇ"]));
}

#[test]
fn lenient_comparison() {
    assert_eq!(char_map("她妳它牠嘗周臺裏"), "他你他他嚐週台裡");
    assert_eq!(char_map("abc甲"), "abc甲");
    // longest match wins; the char map runs first; untouched text passes through
    let l = Lenient::parse("# note\n甲乙\t丙丁\n甲乙戊\t己己己\n台北\t北台\n").unwrap();
    assert_eq!(l.apply("甲乙戊甲乙x"), "己己己丙丁x");
    assert_eq!(l.apply("臺北"), "北台");
    assert_eq!(l.apply("abc"), "abc");
    // malformed or duplicate lines are errors carrying only the line number
    assert_eq!(Lenient::parse("# h\n甲乙\n").err(), Some(Error::BadVariants { line: 2 }));
    assert_eq!(Lenient::parse("甲乙\t丙\t丁\n").err(), Some(Error::BadVariants { line: 1 }));
    assert_eq!(Lenient::parse("甲乙\t丙丁\n甲乙\t戊己\n").err(), Some(Error::BadVariants { line: 2 }));
    // an empty table leaves only the char map
    assert_eq!(Lenient::parse("# only a header\n").unwrap().apply("臺甲乙"), "台甲乙");
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
    let (m, _) = evaluate(&l, &Lenient::parse("").unwrap(), &rows[1..2], |_| {}).unwrap();
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
    let a: Vec<&str> = l.entries(&v(&["a"])).iter().map(|(w, _)| *w).collect();
    assert_eq!(a, ["戊", "甲", "乙", "丁", "丙"]);
}

// L 的前置守門（plan-verifier 2026-10-03）：重構前先固定這三個行為，golden 碰不到它們。

#[test]
fn learner_word_score_takes_last_duplicate() {
    // dict(by_reading[key])[word]：同讀音重複的詞取最後一筆（-3.0），不是第一筆（-2.0）
    let l = lex("a 甲 -1.0\na 乙 -2.0\na 乙 -3.0\n");
    let key = v(&["a"]);
    let mut g = GlobalBoost::new(&l);
    g.observe("<s>", &key, "乙");
    assert!((g.bonus("x", &key, "乙") - 2.01).abs() < 1e-12);
}

#[test]
fn overlay_duplicate_within_overlay_is_error() {
    let e = Lexicon::parse_with("a 甲 -1.0\n", Some("a\t乙\t-2.0\twikt\na\t乙\t-3.0\tzhwiki\n")).err().unwrap();
    assert_eq!(e, Error::OverlayDuplicate { word_len: 1 });
}

#[test]
fn overlay_bad_rows_report_length_only() {
    const MARK: &str = "ZQXMARKER";
    for row in [format!("{MARK}\t{MARK}\n"), format!("a\t{MARK}\t{MARK}\ttag\n")] {
        let e = Lexicon::parse_with("a 甲 -1.0\n", Some(&row)).err().unwrap();
        assert_eq!(e, Error::BadOverlayRow { line_len: row.trim_end_matches('\n').chars().count() });
        assert!(!format!("{e} {e:?}").contains(MARK));
    }
}

/// Review item 7: the tolerance tables are built at the first lookup that needs them, not for every lexicon. A lookup that the flags
/// and the span allow no tolerant reading for does not build them (the candidate window asks on every open, with the flags 0 as ever).
#[test]
fn tolerance_tables_are_built_on_first_use() {
    let l = lex("ㄓ 之 -1.0\nㄗ 子 -1.0\nㄅㄝ 憋 -1.0\n");
    assert!(l.tol.get().is_none(), "parsing builds nothing");
    assert!(l.tolerant_words(&v(&["ㄗ"]), 0).is_empty());
    assert!(l.tolerant_words(&v(&["ㄗ"]), fuzzy::FUZZY_ADJACENT).is_empty(), "bit 4 alone tolerates no known syllable");
    assert!(l.tol.get().is_none(), "flags 0 (or the adjacent bit) and known syllables: no table");
    assert_eq!(l.tolerant_words(&v(&["ㄗ"]), 0b10).iter().map(|x| x.0).collect::<Vec<_>>(), ["之"]);
    assert!(l.tol.get().is_some(), "a sound pair needs them");
}

/// Review item 5, at the table: an adjacent-key alternative needs a one-syllable entry. ㄅㄩ is the slip of ㄅㄝ (typed with its neighbour)
/// and ㄅㄩˊ the slip of ㄅㄝˊ; ㄅㄝ has a one-syllable entry, ㄅㄝˊ stands in a two-syllable word only.
#[test]
fn an_adjacent_alternative_without_a_one_syllable_entry_is_dropped() {
    let l = lex("ㄅㄝ 憋 -1.0\nㄅㄝˊ-ㄅㄚ 甲乙 -1.0\n");
    assert!(l.has_syllable("ㄅㄝˊ") && l.entries(&v(&["ㄅㄝˊ"])).is_empty(), "premise: ㄅㄝˊ has no one-syllable entry");
    assert_eq!(l.adjacent_alts("ㄅㄩ"), ["ㄅㄝ"]);
    assert!(l.adjacent_alts("ㄅㄩˊ").is_empty(), "the slip of ㄅㄝˊ alone has no alternative that a one-syllable span could read");
}
