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
    // 甲+乙 and 甲乙 tie at -2.0; the first-inserted split must survive (docs/spec/decoder.md §4.1, strict >).
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

/// docs/spec/evaluation.md §3.3: word pairs first, then single characters where the row syllable is in the
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
    // the skip is reported by `apply_checked`, which is where the CLI's count comes from
    assert!(f.apply_checked("濕", &v(&["ㄕ"])).1 && !f.apply_checked("濕a", &v(&["ㄕ"])).1);
    // malformed, duplicate or multi-character `char` lines are errors carrying only the line number
    for bad in ["word\t甲\n", "char\t濕\t溼\n", "char\t濕濕\t溼\tㄕ\n", "char\t濕\t溼\t\n", "other\ta\tb\n", "word\ta\tb\nword\ta\tc\n", "word\t甲乙\t丙\n", "char\t濕\t溼\tㄕ\nchar\t濕\t沉\tㄕ\n"] {
        assert!(matches!(Forms::parse(bad), Err(Error::BadForms { .. })), "{bad:?}");
    }
}

/// The shipped table (eval/forms.tsv) on the cases of docs/spec/evaluation.md §3, judged as the scorer does: Lenient, then Forms.
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

// L 的前置守門：重構前先固定這三個行為，golden 碰不到它們。

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

/// The tolerance tables are built at the first lookup that needs them, not for every lexicon. A lookup that the flags
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

/// At the table: an adjacent-key alternative needs a one-syllable entry. ㄅㄩ is the slip of ㄅㄝ (typed with its neighbour)
/// and ㄅㄩˊ the slip of ㄅㄝˊ; ㄅㄝ has a one-syllable entry, ㄅㄝˊ stands in a two-syllable word only.
#[test]
fn an_adjacent_alternative_without_a_one_syllable_entry_is_dropped() {
    let l = lex("ㄅㄝ 憋 -1.0\nㄅㄝˊ-ㄅㄚ 甲乙 -1.0\n");
    assert!(l.has_syllable("ㄅㄝˊ") && l.entries(&v(&["ㄅㄝˊ"])).is_empty(), "premise: ㄅㄝˊ has no one-syllable entry");
    assert_eq!(l.adjacent_alts("ㄅㄩ"), ["ㄅㄝ"]);
    assert!(l.adjacent_alts("ㄅㄩˊ").is_empty(), "the slip of ㄅㄝˊ alone has no alternative that a one-syllable span could read");
}

// Variant order (docs/spec/decoder.md §2.3): the table's format rules, the order it gives, the failures it makes.

#[test]
fn variant_order_format_rules() {
    let ok = "# c\n\na-b\t甲甲\t乙乙\tt\nc\t丁\t戊\tt\n";
    assert_eq!(VariantOrder::parse(ok).unwrap().rows.len(), 2);
    for (bad, line) in [
        ("a\t甲\t乙\n", 1),                          // 3 fields
        ("a\t甲\t乙\tt\textra\n", 1),                // 5 fields
        ("a\t甲\t甲\tt\n", 1),                       // less == more
        ("a\t 甲\t乙\tt\n", 1),                      // surrounding whitespace
        ("a\t甲\t乙\t\n", 1),                        // empty field
        ("a\t甲\t乙\tt\na\t甲\t丙\tt\n", 2),         // the same (reading, less) twice: the second row
        ("a\t甲\t乙\tt\nb\t乙\t丙\tt\n", 1),         // a chain: row 1's more is row 2's less
    ] {
        assert_eq!(VariantOrder::parse(bad).err(), Some(Error::BadVariantOrder { line }), "{bad:?}");
    }
}

#[test]
fn variant_order_puts_the_common_spelling_first_and_leaves_a_lower_one_alone() {
    let table = VariantOrder::parse("a\t甲\t乙\tt\nb\t丙\t丁\tt\nc\t戊\t己\tt\n").unwrap();
    let base = "a 甲 -0.2\na 乙 -0.5\na 庚 -3.0\nb 丙 -1.0\nb 丁 -1.0\nc 戊 -9.0\nc 己 -1.0\n";
    let l = Lexicon::parse_with_variants(base, None, Some(&table)).unwrap();
    // 甲 was above 乙 and is now 0.01 below it; 庚 (not in the table) stays; the tie goes to the common spelling; 戊 was already lower: unchanged.
    assert_eq!(l.entries(&v(&["a"])), [("乙", -0.5), ("甲", -0.5 - VARIANT_GAP), ("庚", -3.0)]);
    assert_eq!(l.entries(&v(&["b"])).iter().map(|(w, _)| *w).collect::<Vec<_>>(), ["丁", "丙"]);
    assert_eq!(l.entries(&v(&["c"])), [("己", -1.0), ("戊", -9.0)]);
    assert_eq!(l.best_word_score("甲"), Some(-0.5 - VARIANT_GAP), "by_word follows the overridden score");
    // Without the table the lexicon is what it was.
    assert_eq!(Lexicon::parse_with(base, None).unwrap().entries(&v(&["a"]))[0], ("甲", -0.2));
}

#[test]
fn variant_order_names_a_spelling_the_lexicon_lacks_is_an_error() {
    let base = "a 甲 -0.2\na 乙 -0.5\nb 丙 -1.0\n";
    for row in ["a\t甲\t不在\tt\n", "a\t不在\t乙\tt\n", "b\t甲\t乙\tt\n", "z\t甲\t乙\tt\n"] {
        let table = VariantOrder::parse(row).unwrap();
        assert_eq!(Lexicon::parse_with_variants(base, None, Some(&table)).err(), Some(Error::BadVariantOrder { line: 1 }), "{row:?}");
    }
}

// ---- pack-switch: the two rewrites that must not change a result (docs/spec/lexicon-and-packs.md §4.2) ----

fn data_root() -> std::path::PathBuf {
    std::path::Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}

/// Every field of two lexicons, the scores bit for bit.
fn assert_same_lexicon(a: &Lexicon, b: &Lexicon, what: &str) {
    assert_eq!(a.max_len, b.max_len, "{what}: max_len");
    assert_eq!(a.syl_ids, b.syl_ids, "{what}: syl_ids");
    assert_eq!(a.syl_names, b.syl_names, "{what}: syl_names");
    assert_eq!(a.key_pool, b.key_pool, "{what}: key_pool");
    assert_eq!(a.readings.len(), b.readings.len(), "{what}: reading count");
    for (i, (x, y)) in a.readings.iter().zip(&b.readings).enumerate() {
        assert_eq!((x.key_off, x.key_len, x.start), (y.key_off, y.key_len, y.start), "{what}: reading {i}");
    }
    assert_eq!(a.ents.len(), b.ents.len(), "{what}: entry count");
    for (i, (x, y)) in a.ents.iter().zip(&b.ents).enumerate() {
        assert_eq!((x.off, x.len, x.score.to_bits()), (y.off, y.len, y.score.to_bits()), "{what}: entry {i}");
    }
    assert_eq!(a.words, b.words, "{what}: words");
    assert_eq!(a.by_word, b.by_word, "{what}: by_word");
    assert_eq!(*a.manual, *b.manual, "{what}: manual set");
    assert_eq!(a.variants, b.variants, "{what}: variant rows");
}

/// Both ways of the variant-order step on the same small texts: the same lexicon, or the same error.
fn assert_variant_steps_agree(base: &str, table: &str) -> Result<Lexicon, Error> {
    let t = VariantOrder::parse(table).unwrap();
    let (new, old) = (Lexicon::parse_with_variants(base, None, Some(&t)), Lexicon::parse_applying(base, None, Some(&t), apply_variant_order_scanning));
    match (&new, &old) {
        (Ok(n), Ok(o)) => assert_same_lexicon(n, o, "small"),
        (Err(n), Err(o)) => assert_eq!(n, o),
        _ => panic!("one way failed, the other did not: {:?} / {:?}", new.as_ref().err(), old.as_ref().err()),
    }
    new
}

#[test]
fn variant_order_one_pass_equals_the_scanning_step_on_small_tables() {
    // Two rows under one reading (and a third under another): each keeps to its own `less`.
    let l = assert_variant_steps_agree("a 甲 -1.0\na 乙 -0.5\na 丙 -0.2\na 丁 -4.0\nb 戊 -1\nb 甲 -3\n", "a\t乙\t甲\tt\na\t丙\t丁\tt\nb\t戊\t甲\tt\n").unwrap();
    let es: Vec<(&str, f64)> = l.entries(&v(&["a"])).to_vec();
    assert_eq!(es.iter().map(|e| e.0).collect::<Vec<_>>(), ["甲", "乙", "丁", "丙"], "{es:?}");
    // `more` twice in the reading: its highest score counts, whichever row comes first in the file.
    let l = assert_variant_steps_agree("a 甲 -3.0\na 甲 -1.0\na 乙 -0.5\n", "a\t乙\t甲\tt\n").unwrap();
    assert_eq!(l.entries(&v(&["a"]))[0].0, "甲");
    assert!((l.entries(&v(&["a"])).iter().find(|e| e.0 == "乙").unwrap().1 - (-1.0 - VARIANT_GAP)).abs() < 1e-12, "the gap is kept to the highest 甲");
    // `less` under two readings, the table naming one: the other stays.
    let l = assert_variant_steps_agree("a 甲 -1.0\na 乙 -0.5\nb 甲 -1.0\nb 乙 -0.5\n", "b\t乙\t甲\tt\n").unwrap();
    assert_eq!(l.entries(&v(&["a"]))[0].0, "乙");
    assert_eq!(l.entries(&v(&["b"]))[0].0, "甲");
    // `less` twice in a reading: both rows are lowered.
    assert_variant_steps_agree("a 甲 -1.0\na 乙 -0.5\na 乙 -0.4\n", "a\t乙\t甲\tt\n").unwrap();
    // An unknown syllable, and a spelling not found under the reading: the same error and line as the scanning step.
    assert_eq!(assert_variant_steps_agree("a 甲 -1\na 乙 -2\n", "z\t乙\t甲\tt\n").err(), Some(Error::BadVariantOrder { line: 1 }));
    assert_eq!(assert_variant_steps_agree("a 甲 -1\na 乙 -2\n", "a\t乙\t不在\tt\n").err(), Some(Error::BadVariantOrder { line: 1 }));
    // Both errors in one table: the row whose turn comes first is reported, in either order.
    assert_eq!(assert_variant_steps_agree("a 甲 -1\na 乙 -2\n", "a\t乙\t不在\tt\nz\t乙\t甲\tt\n").err(), Some(Error::BadVariantOrder { line: 1 }));
    assert_eq!(assert_variant_steps_agree("a 甲 -1\na 乙 -2\n", "z\t乙\t甲\tt\na\t乙\t不在\tt\n").err(), Some(Error::BadVariantOrder { line: 1 }));
    assert_eq!(assert_variant_steps_agree("a 甲 -1\na 乙 -2\n", "a\t乙\t甲\tt\nz\t乙\t甲\tt\na\t丙\t不在\tt\n").err(), Some(Error::BadVariantOrder { line: 2 }));
}

/// The texts `Engine::new_with_packs` parses (docs/spec/c-abi.md §8): base, the overlay rows (overlay, sandhi, names, the packs of `mask`), the variant-order table.
fn real_lexicon_texts(mask: u32) -> (String, String, VariantOrder, std::time::Duration) {
    let t0 = std::time::Instant::now();
    let (dir, packs) = (data_root().join("data/lexicon"), data_root().join("data/packs"));
    let read = |f: &str| std::fs::read_to_string(dir.join(f)).unwrap();
    let mut overlay = read("overlay-add.tsv");
    for part in [read("sandhi-add.tsv"), read("names-add.tsv"), engine::read_packs_detailed(&packs, mask).unwrap()] {
        if !overlay.is_empty() && !overlay.ends_with('\n') {
            overlay.push('\n');
        }
        overlay.push_str(&part);
    }
    (read("mcbpmf-data.txt"), overlay, VariantOrder::parse(&read("variant-order.tsv")).unwrap(), t0.elapsed())
}

#[test]
fn variant_order_one_pass_equals_the_scanning_step_on_the_real_lexicon() {
    for mask in [0, engine::PACK_ALL] {
        let (base, overlay, table, _) = real_lexicon_texts(mask);
        let new = Lexicon::parse_with_variants(&base, Some(&overlay), Some(&table)).unwrap();
        let old = Lexicon::parse_applying(&base, Some(&overlay), Some(&table), apply_variant_order_scanning).unwrap();
        assert!(new.variants.len() > 10, "the shipped table has rows");
        assert_same_lexicon(&new, &old, &format!("mask {mask}"));
    }
}

fn spread(mut ms: Vec<f64>) -> (f64, f64, f64) {
    ms.sort_by(|a, b| a.partial_cmp(b).unwrap());
    (ms[0], ms[ms.len() / 2], ms[ms.len() - 1])
}

/// Release only (`#[ignore]`; docs/spec/lexicon-and-packs.md §4.2): the lexicon built from text, the step as it was and as it is, alternating in one process, 10 each.
/// `cargo test --release --locked -p core --lib tests::timing_variant_order_one_pass -- --ignored --nocapture --exact`
#[test]
#[ignore]
fn timing_variant_order_one_pass() {
    for mask in [0, 29] {
        let (base, overlay, table, load) = real_lexicon_texts(mask);
        let (mut old, mut new) = (Vec::new(), Vec::new());
        for _ in 0..10 {
            let t = std::time::Instant::now();
            let a = Lexicon::parse_applying(&base, Some(&overlay), Some(&table), apply_variant_order_scanning).unwrap();
            old.push(t.elapsed().as_secs_f64() * 1e3);
            let t = std::time::Instant::now();
            let b = Lexicon::parse_with_variants(&base, Some(&overlay), Some(&table)).unwrap();
            new.push(t.elapsed().as_secs_f64() * 1e3);
            assert_eq!(a.ents.len(), b.ents.len());
        }
        let (o, n) = (spread(old), spread(new));
        println!("pack-switch timing: variant_order mask {mask} old min/median/max {:.0}/{:.0}/{:.0} ms  new min/median/max {:.0}/{:.0}/{:.0} ms  (load {:.0} ms)", o.0, o.1, o.2, n.0, n.1, n.2, load.as_secs_f64() * 1e3);
        assert!(n.1 <= o.1 * 0.85, "mask {mask}: the median must be at least 15% less (old {:.0}, new {:.0})", o.1, n.1);
    }
}

/// The capped lexicon of the shipping masks, for `Legal::new`.
fn real_capped(mask: u32) -> (lm::Lm, lm::CappedLexicon) {
    let (dir, packs) = (data_root().join("data/lexicon"), data_root().join("data/packs"));
    let (lex, pack_text) = engine::load_lexicon_packs(&dir, Some((&packs, mask))).unwrap();
    let overlay = engine::capping_overlay(&dir, &pack_text).unwrap();
    let demote = lm::Demote::parse(&std::fs::read_to_string(dir.join("demote.tsv")).unwrap()).unwrap();
    let lm = lm::Lm::load(&data_root().join("data/lm/bigram.sjlm")).unwrap();
    let oov = engine::read_packs_detailed(&packs, mask & engine::PACK_OOV).unwrap();
    let capped = lm::CappedLexicon::with_packs(lex, &overlay, &oov, &lm, Some(&demote)).unwrap();
    (lm, capped)
}

#[test]
fn legal_once_per_syllable_equals_the_each_reading_step() {
    // Small: one syllable in several readings, a reading with no Han word (a letter word only), a toned and a toneless syllable.
    let rows: Vec<(Vec<&str>, Vec<(&str, f64)>)> = vec![
        (vec!["ㄅ"], vec![("ㄅ", -1.0)]),
        (vec!["ㄆ", "ㄚˊ"], vec![("爬", -1.0)]),
        (vec!["ㄚˊ"], vec![("牙", -1.0), ("a", -2.0)]),
        (vec!["ㄚˊ", "ㄅ"], vec![("ab", -1.0)]),
    ];
    let (a, b) = (digits::Legal::new(rows.clone().into_iter()), digits::Legal::new_each(rows.into_iter()));
    assert!(a.sets() == b.sets(), "small: the two sets differ");
    assert!(a.has_han_syllable("ㄚˊ") && !a.has_han_syllable("ㄅ"), "premise: a letter-word reading does not count");
    for mask in [0, 29] {
        let (lm, capped) = real_capped(mask);
        let (a, b) = (digits::Legal::new(capped.readings(&lm)), digits::Legal::new_each(capped.readings(&lm)));
        assert!(a.sets().0.len() > 100 && a.sets().1.len() > 100, "mask {mask}: the sets are not empty");
        assert!(a.sets() == b.sets(), "mask {mask}: the two sets differ");
    }
}

/// Release only (`#[ignore]`): `Legal::new` on the capped lexicon of mask 29, as it was and as it is, alternating, 10 each.
/// `cargo test --release --locked -p core --lib tests::timing_legal_once_per_syllable -- --ignored --nocapture --exact`
#[test]
#[ignore]
fn timing_legal_once_per_syllable() {
    let t0 = std::time::Instant::now();
    let (lm, capped) = real_capped(29);
    let load = t0.elapsed().as_secs_f64() * 1e3;
    let (mut old, mut new) = (Vec::new(), Vec::new());
    for _ in 0..10 {
        let t = std::time::Instant::now();
        let a = digits::Legal::new_each(capped.readings(&lm));
        old.push(t.elapsed().as_secs_f64() * 1e3);
        let t = std::time::Instant::now();
        let b = digits::Legal::new(capped.readings(&lm));
        new.push(t.elapsed().as_secs_f64() * 1e3);
        assert!(a.sets() == b.sets());
    }
    let (o, n) = (spread(old), spread(new));
    println!("pack-switch timing: legal_new mask 29 old min/median/max {:.0}/{:.0}/{:.0} ms  new min/median/max {:.0}/{:.0}/{:.0} ms  (load {load:.0} ms)", o.0, o.1, o.2, n.0, n.1, n.2);
    assert!(n.1 <= o.1 * 0.5, "the median must be at least 50% less (old {:.0}, new {:.0})", o.1, n.1);
}
