//! S3a contract 7.2: replay the 302 rows of `--set dev --limit 302` as key presses, both layouts.
use core::engine::*;
use core::eval::{parse_rows, usable};
use core::{decode_beam, NoLearning, BEAM_S1};
use std::path::{Path, PathBuf};
use std::time::Instant;

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}

/// Reading -> key presses: symbols in initial/medial/final order (already so in the string), then the tone key.
fn keys_of(layout: Layout, syl: &str) -> Vec<Key> {
    let mut v = Vec::new();
    let mut toned = false;
    for c in syl.chars() {
        match layout.key_of_tone(c) {
            Some(tk) => {
                v.push(Key::ch(tk, 0));
                toned = true;
            }
            None => v.push(Key::ch(layout.key_of_symbol(c).expect("symbol has a key"), 0)),
        }
    }
    if !toned {
        v.push(Key::new(KeyKind::Space));
    }
    v
}

/// `key_order`: also the zhen-keyorder switch. `english`: the standard layout with the zhen-mixed switch on and the synthetic list loaded (docs/contracts/zhen-mixed.md
/// section 5.1) and the zhen-inline display on (its default): the same 302 rows, and neither the English window nor the letters
/// (rule D) may ever show: the rows are Chinese typed right.
fn replay(layout: Layout, english: bool, key_order: bool) {
    let lex = load_lexicon(&root().join("data/lexicon")).unwrap();
    let mut files: Vec<PathBuf> = std::fs::read_dir(root().join("eval/dev"))
        .unwrap()
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.extension().is_some_and(|x| x == "txt"))
        .collect();
    files.sort();
    let mut rows = Vec::new();
    for f in files {
        rows.extend(parse_rows(&std::fs::read_to_string(f).unwrap()).unwrap());
    }
    let mut rows = usable(&lex, rows);
    rows.truncate(302);
    assert!(rows.len() == 302, "dev set has 302 usable rows");

    let mut e = Engine::with_lexicon(lex.clone(), layout);
    if english {
        e.load_english(&root().join("eval/golden/english-tiny.txt")).unwrap();
        e.set_english(true).unwrap();
        e.set_english_key_order(key_order).unwrap(); // zhen-keyorder section 5.4: the same rows, clean, with the switch on
    }
    let mut times = Vec::new();
    for (i, r) in rows.iter().enumerate() {
        let syls = match &r.reading {
            Some(s) => s.clone(),
            None => lex.to_syllables(&r.sent).expect("usable row"),
        };
        let want = decode_beam(&lex, &syls, &mut NoLearning, BEAM_S1).unwrap()[0].1.concat();
        let mut keys: Vec<Key> = syls.iter().flat_map(|s| keys_of(layout, s)).collect();
        keys.push(Key::new(KeyKind::Enter));
        let mut got = String::new();
        for key in keys {
            let t = Instant::now();
            let o = e.key(key).unwrap();
            times.push(t.elapsed());
            assert!(!e.english_open(), "the English window opened at row {}", i + 1);
            assert!(!e.english_inline_shown(), "letters showed instead of Zhuyin at row {}", i + 1);
            got.push_str(&o.commit);
        }
        assert!(got == want, "replay mismatch at row {}", i + 1);
    }
    times.sort();
    let p95 = times[times.len() * 95 / 100];
    println!("keys {} p95 {:?} max {:?}", times.len(), p95, times[times.len() - 1]);
    #[cfg(not(debug_assertions))]
    assert!(p95 < std::time::Duration::from_millis(16), "p95 over 16 ms");
}

#[test]
fn replay_standard_302_of_302() {
    replay(Layout::Standard, false, false);
}
#[test]
fn replay_eten_302_of_302() {
    replay(Layout::Eten, false, false);
}
#[test]
fn replay_with_english_on() {
    replay(Layout::Standard, true, false);
}
#[test]
fn replay_with_english_and_key_order_on() {
    replay(Layout::Standard, true, true);
}

/// Digits.md A.4.1 #48 (C's R2 on the real engine): the 302 rows are Chinese typed right. With the English-mixing switch on, the word list
/// and the digit table loaded (and the model: the digit feature needs the scores), every row commits what the same engine commits with the
/// switch off, no key puts a digit token into the composition, and the keys stay under 16 ms at p95 (release).
#[test]
fn replay_with_english_and_digits_on() {
    use core::digits::DigitTable;
    use core::engine::capping_overlay;
    use core::lm::{CappedLexicon, Lm};
    use std::sync::Arc;
    let lex = load_lexicon(&root().join("data/lexicon")).unwrap();
    let lm = Lm::load(&root().join("data/lm/bigram.sjlm")).unwrap();
    let overlay = capping_overlay(&root().join("data/lexicon"), "").unwrap();
    let capped = Arc::new(CappedLexicon::new(lex.clone(), &overlay, &lm, None).unwrap());
    let lm = Arc::new(lm);
    let table = Arc::new(DigitTable::parse(&std::fs::read_to_string(root().join("data/lexicon/digits.tsv")).unwrap()).unwrap());
    let mut files: Vec<PathBuf> = std::fs::read_dir(root().join("eval/dev"))
        .unwrap()
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.extension().is_some_and(|x| x == "txt"))
        .collect();
    files.sort();
    let mut rows = Vec::new();
    for f in files {
        rows.extend(parse_rows(&std::fs::read_to_string(f).unwrap()).unwrap());
    }
    let mut rows = usable(&lex, rows);
    rows.truncate(302);
    assert!(rows.len() == 302, "dev set has 302 usable rows");

    let build = |on: bool| {
        let mut e = Engine::with_lexicon(lex.clone(), Layout::Standard);
        e.set_lm(lm.clone(), capped.clone());
        e.set_digit_table(Some(table.clone()));
        e.load_english(&root().join("eval/golden/english-tiny.txt")).unwrap();
        e.set_english(on).unwrap();
        e
    };
    let (mut on, mut off) = (build(true), build(false));
    let (mut times, mut tokens) = (Vec::new(), 0);
    for (i, r) in rows.iter().enumerate() {
        let syls = match &r.reading {
            Some(s) => s.clone(),
            None => lex.to_syllables(&r.sent).expect("usable row"),
        };
        let mut keys: Vec<Key> = syls.iter().flat_map(|s| keys_of(Layout::Standard, s)).collect();
        keys.push(Key::new(KeyKind::Enter));
        let (mut got, mut want) = (String::new(), String::new());
        for key in keys {
            want.push_str(&off.key(key).unwrap().commit);
            let t = Instant::now();
            let o = on.key(key).unwrap();
            times.push(t.elapsed());
            tokens += on.digits_emitted();
            got.push_str(&o.commit);
        }
        assert!(got == want, "replay mismatch at row {}", i + 1);
    }
    assert_eq!(tokens, 0, "a digit token was made while typing Chinese");
    times.sort();
    let p95 = times[times.len() * 95 / 100];
    println!("digits on: keys {} p95 {:?} max {:?}", times.len(), p95, times[times.len() - 1]);
    #[cfg(not(debug_assertions))]
    assert!(p95 < std::time::Duration::from_millis(16), "p95 over 16 ms");
}
