//! docs/spec/english-mixed.md §16: English or Zhuyin at the closing key. The engine as the App builds it (`Engine::new` + `load_lm`, mixing on,
//! overwrite off, key order on, reversal guard on) with the synthetic table core/tests/fixtures/english-tiny.tsv loaded through the tool hook
//! (the tests build their engines on a temporary data folder with the tiny table, not on data/lexicon, which ships english.tsv:
//! `the_data_folder_decides_what_ships` checks that one).

use core::engine::{Engine, Key, KeyKind, Layout, Output, Revert};
use core::english_auto::{lp_z, takes_english, AutoTable, Params, KAPPA, KAPPA_DUP, KAPPA_REV, THETA};
use std::path::{Path, PathBuf};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}
fn fixture() -> PathBuf {
    root().join("core/tests/fixtures/english-tiny.tsv")
}
/// The App's engine without any English table (whatever the data folder holds).
fn plain() -> Engine {
    let mut e = Engine::new(&root().join("data/lexicon"), Layout::Standard).unwrap();
    e.load_lm(&root().join("data/lm/bigram.sjlm")).unwrap();
    e.set_english(true).unwrap();
    e.set_english_overwrite(false).unwrap();
    e.set_auto_table(None);
    e
}
/// With the fixture table and the constants.
fn app() -> Engine {
    let mut e = plain();
    assert!(e.load_auto_english(&fixture()));
    e
}
/// With the fixture table and a theta that lets English win every comparison that has a finite lpE.
fn forced() -> Engine {
    let mut e = app();
    e.set_auto_params(Params { theta: -100.0, ..Params::default() });
    e
}
fn key_of(tok: &str) -> Key {
    match tok {
        "_" => Key::new(KeyKind::Space),
        "Esc" => Key::new(KeyKind::Esc),
        "BS" => Key::new(KeyKind::Backspace),
        "Down" => Key::new(KeyKind::Down),
        "Up" => Key::new(KeyKind::Up),
        "Left" => Key::new(KeyKind::Left),
        "Right" => Key::new(KeyKind::Right),
        "Enter" => Key::new(KeyKind::Enter),
        t => Key::ch(t.chars().next().unwrap(), 0),
    }
}
fn run(e: &mut Engine, script: &str) -> Output {
    script.split_whitespace().map(|t| e.key(key_of(t)).unwrap()).last().expect("a script")
}
/// Every key's (preedit, commit, units, run length): what "the same engine" compares.
fn trace(e: &mut Engine, script: &str) -> Vec<(String, String, Vec<String>, u32)> {
    let mut out = Vec::new();
    for t in script.split_whitespace() {
        let o = e.key(key_of(t)).unwrap();
        out.push((o.preedit, o.commit, e.syllables().to_vec(), o.english_run_utf16_len));
    }
    out
}
/// The whole list of the window Down opens (walked with Right until it wraps).
fn list(e: &mut Engine) -> Vec<String> {
    let mut o = e.key(Key::new(KeyKind::Down)).unwrap();
    let total = o.total as usize;
    let mut out = Vec::new();
    for _ in 0..total {
        out.push(o.candidates[o.selected.unwrap()].clone());
        o = e.key(Key::new(KeyKind::Right)).unwrap();
    }
    out
}
fn first_entry(e: &Engine, syl: &str) -> String {
    let _ = e;
    core::engine::load_lexicon(&root().join("data/lexicon")).unwrap().entries(&[syl.to_string()]).first().unwrap().0.to_string()
}

/// The four worked examples (§16.1): the counts, that each is a decision point, and that with the core switch off the keys are the Chinese
/// syllable of the engine before (no token, no revert).
#[test]
fn the_worked_examples_and_their_counts() {
    for (keys, syl, n) in [("s p e e c h _", "ㄘㄣ", (4, 1, 0)), ("k d l _", "ㄎㄠ", (1, 0, 1)), ("l s _", "ㄋㄠ", (0, 0, 1)), ("d e k _", "ㄍㄜ", (1, 0, 0))] {
        let mut off = app();
        off.set_english_auto(false).unwrap();
        run(&mut off, keys);
        assert!(off.english_auto_point() == Some(n), "{keys}: {:?}", off.english_auto_point());
        assert!(off.syllables() == [syl] && off.english_reverted().is_none() && off.english_auto_event().is_none(), "{keys}: switch off is Chinese");
        // The counts do not need a model or a table: the lexicon-only engine reports the same point.
        let mut bare = Engine::with_lexicon(core::engine::load_lexicon(&root().join("data/lexicon")).unwrap(), Layout::Standard);
        bare.set_english(true).unwrap();
        bare.set_english_overwrite(false).unwrap();
        run(&mut bare, keys);
        assert!(bare.english_auto_point() == Some(n) && bare.syllables() == [syl], "{keys}: no model");
    }
    // The key-order switch off: no (b), so a key out of column order is no decision point; a replaced key still is.
    let mut e = app();
    e.set_english_key_order(false).unwrap();
    run(&mut e, "l s _");
    assert!(e.english_auto_point().is_none() && e.syllables() == ["ㄋㄠ"]);
    run(&mut e, "Esc d e k _");
    assert!(e.english_auto_point() == Some((1, 0, 0)));
}

/// Closing keys that are not decision points (§16.1), under a theta that would turn any decision point with a finite lpE into English.
#[test]
fn what_is_no_decision_point() {
    let mut e = forced();
    // Typed in column order, nothing replaced.
    run(&mut e, "a i _");
    assert!(e.english_auto_point().is_none() && e.syllables() == ["ㄇㄛ"]);
    // Backspace mends Zhuyin: the record and the counts forget the replaced keys.
    run(&mut e, "Esc s p e e c h BS p _");
    assert!(e.english_auto_point().is_none() && e.syllables() == ["ㄘㄣ"], "{:?}", e.syllables());
    // So does the key out of column order: `k d` (ㄎ before ㄜ), Backspace takes the ㄜ, `l` puts ㄠ behind the ㄎ in order.
    run(&mut e, "Esc k d BS l _");
    assert!(e.english_auto_point().is_none() && e.syllables() == ["ㄎㄠ"], "{:?} {:?}", e.syllables(), e.english_auto_point());
    // The digit layer's syllables: a tail it closed, late keys it took back (`8 w` Space is ㄊㄚ), the tone moved up (`q 6 8`).
    for keys in ["8 4", "8 w _", "q 6 8"] {
        run(&mut e, "Esc");
        run(&mut e, keys);
        assert!(e.english_auto_point().is_none() && e.english_reverted() != Some(Revert::Auto), "{keys}");
    }
    // Late keys taken back into a syllable that also had a key replaced (`8 w s` Space: ㄋ over ㄊ, then the 8 rejoins as ㄚ): still the digit layer's.
    run(&mut e, "Esc");
    run(&mut e, "8 w s _");
    assert!(e.syllables() == ["ㄋㄚ"] && e.english_auto_point().is_none(), "{:?} {:?}", e.syllables(), e.english_auto_point());
    // A syllable the lexicon lacks is revert (c)'s, as before.
    run(&mut e, "Esc");
    run(&mut e, "c u p _");
    assert!(e.english_auto_point().is_none() && e.english_reverted() == Some(Revert::Missing));
    // Esc on a run a revert opened brings the Zhuyin back: its closing key is not a decision point.
    let mut e = forced();
    e.set_english_overwrite(true).unwrap();
    run(&mut e, "s p e e c h Esc");
    assert!(e.english_columns().iter().flatten().collect::<String>() == "ㄘㄣ" && e.english_record() == "speech");
    run(&mut e, "_");
    assert!(e.english_auto_point().is_none() && e.syllables() == ["ㄘㄣ"], "{:?}", e.syllables());
    // A decision point whose keys are no English string, or no word the table can score: no comparison, Zhuyin.
    let mut e = forced();
    run(&mut e, ", k _"); // ㄝ replaced by ㄜ: the keys start with a comma
    assert!(e.english_auto_point() == Some((1, 0, 0)) && e.english_auto_event().is_none() && e.syllables() == ["ㄜ"]);
    run(&mut e, "Esc y h _"); // ㄗ replaced by ㄘ: `yh` is not in the table and too short for the letter model
    assert!(e.english_auto_point() == Some((1, 0, 0)) && e.english_auto_event().is_none() && e.syllables() == ["ㄘ"]);
}

/// §16.2, §16.3 with the constants: the comparison the engine made is the formula on the table, the model's sum and the reading term.
#[test]
fn the_scores_are_the_formula() {
    let table = AutoTable::parse(&std::fs::read_to_string(fixture()).unwrap()).unwrap();
    let p = Params::default();
    assert!(p == Params { theta: THETA, kappa: KAPPA, kappa_dup: KAPPA_DUP, kappa_rev: KAPPA_REV });
    for (left, keys, e_text, ctx, n) in [("", "s p e e c h _", "speech", "<s>", (4, 1, 0)), ("我們用", "s p e e c h _", "speech", "用", (4, 1, 0)), ("", "d e k _", "dek", "<s>", (1, 0, 0)), ("", "l s _", "ls", "<s>", (0, 0, 1))] {
        let mut e = app();
        e.set_left_context(left);
        run(&mut e, keys);
        let ev = e.english_auto_event().expect("a comparison").clone();
        assert!(ev.e == e_text && ev.ctx == ctx && ev.n == n, "{keys} after {left:?}");
        assert!(ev.lp_e.to_bits() == table.lp_e(ctx, e_text).to_bits() && ev.lp_e.is_finite());
        assert!(ev.sum.is_finite() && ev.sum < 0.0 && ev.term <= 0.0, "a probability sum and a reading term: {} {}", ev.sum, ev.term);
        assert!(ev.lp_z.to_bits() == lp_z(ev.sum, ev.term, n.0, n.1, n.2, &p).to_bits());
        assert!(ev.english == takes_english(ev.lp_e, ev.lp_z, &p) && ev.english == (e.english_reverted() == Some(Revert::Auto)));
        // With the reading term off, t is 0 and the sum is the same.
        let mut off = app();
        off.set_reading_decode(false);
        off.set_left_context(left);
        run(&mut off, keys);
        let ev0 = off.english_auto_event().unwrap();
        assert!(ev0.term == 0.0 && ev0.sum.to_bits() == ev.sum.to_bits());
    }
    // On this table: `speech` (four keys replaced) is English with the constants, `d e k` (one neighbour slip mended by overwriting) stays Chinese
    // although the table has a `dek` row.
    for (keys, units) in [("s p e e c h _", "_punct_speech"), ("d e k _", "ㄍㄜ")] {
        let mut e = app();
        run(&mut e, keys);
        assert!(e.syllables() == [units], "{keys}: {:?}", e.syllables());
    }
}

/// §16.3: English at Space is revert (c)'s token (the space eaten, the Chinese item last in its list), marked for Esc (§16.6).
#[test]
fn space_makes_the_token_and_esc_right_after_brings_the_syllable_back() {
    let mut e = app();
    let o = run(&mut e, "s p e e c h _");
    assert!(e.syllables() == ["_punct_speech"] && o.preedit == "speech" && o.commit.is_empty() && o.english_run_utf16_len == 0);
    assert!(e.english_reverted() == Some(Revert::Auto));
    let l = list(&mut e);
    assert!(l.first().map(String::as_str) == Some("speech") && l.last() == Some(&first_entry(&e, "ㄘㄣ")), "{l:?}");
    // Esc right after: the syllable those keys are today, the cursor behind it, and the two English items at the end of its list.
    let mut e = app();
    run(&mut e, "s p e e c h _");
    let o = run(&mut e, "Esc");
    let mut before = plain();
    let want = run(&mut before, "s p e e c h _");
    assert!(e.syllables() == ["ㄘㄣ"] && o.preedit == want.preedit && o.cursor_utf16 == want.cursor_utf16 && o.commit.is_empty(), "{}", o.preedit);
    let l = list(&mut e);
    assert!(l[l.len() - 2..] == ["speech", "SPEECH"] && l == list(&mut before), "{l:?}");
    // In the middle of a sentence, in front of other units: only the token is replaced.
    let mut e = app();
    run(&mut e, "j i 3 s u 3 Left"); // 我 你, the cursor between them
    run(&mut e, "s p e e c h _");
    assert!(e.syllables() == ["ㄨㄛˇ", "_punct_speech", "ㄋㄧˇ"]);
    run(&mut e, "Esc");
    assert!(e.syllables() == ["ㄨㄛˇ", "ㄘㄣ", "ㄋㄧˇ"]);
    // A key that changes nothing keeps the mark; a key that changes the state ends it, and Esc clears the composition as after any token.
    let mut e = app();
    run(&mut e, "s p e e c h _ Up Esc");
    assert!(e.syllables() == ["ㄘㄣ"]);
    for between in ["Left Right", "a BS", "_"] {
        let mut e = app();
        run(&mut e, "s p e e c h _");
        run(&mut e, between);
        let o = run(&mut e, "Esc");
        assert!(e.syllables().is_empty() && o.preedit.is_empty(), "{between}: {:?}", e.syllables());
    }
    // Setting a switch ends the mark too (the short-word mark's rule).
    let mut e = app();
    run(&mut e, "s p e e c h _");
    e.set_english_key_order(true).unwrap();
    run(&mut e, "Esc");
    assert!(e.syllables().is_empty());
    // Other tokens keep their Esc: revert (c)'s own token, and a token the comparison did not make.
    let mut e = app();
    run(&mut e, "c u p _ Esc");
    assert!(e.syllables().is_empty());
}

/// §16.3: English at a tone key is revert (c)'s open run with the digit on it and the restore point; Esc is the Zhuyin replay; the tone moved up
/// still works.
#[test]
fn a_tone_key_opens_the_run() {
    let mut e = forced();
    let o = run(&mut e, "t e s t 6");
    assert!(o.preedit == "test6" && o.english_run_utf16_len == 5 && e.syllables().is_empty() && e.english_reverted() == Some(Revert::Auto));
    assert!(e.english_auto_point() == Some((3, 0, 0)));
    // Esc: the replay of `test6` is the closed syllable (the lexicon has it), by rule 10.
    let o = run(&mut e, "Esc");
    assert!(e.syllables() == ["ㄔˊ"] && o.english_run_utf16_len == 0);
    // The replay left nothing unfinished: the next syllable is judged like any other.
    run(&mut e, "s p e e c h _");
    assert!(e.syllables() == ["ㄔˊ", "_punct_speech"], "{:?}", e.syllables());
    // Space ends the run as a token with the Chinese item.
    let mut e = forced();
    run(&mut e, "t e s t 6 _");
    assert!(e.syllables() == ["_punct_test6"]);
    assert!(list(&mut e).last() == Some(&first_entry(&e, "ㄔˊ")));
    // The tone moved up (§4.2): `i k 6` is ㄜˊ after ㄛ was replaced, English here; `d` fills the free initial column and ㄎㄜˊ is a syllable.
    let mut e = forced();
    let o = run(&mut e, "i k 6");
    assert!(o.preedit == "ik6" && e.english_reverted() == Some(Revert::Auto));
    run(&mut e, "d");
    assert!(e.syllables() == ["ㄎㄜˊ"], "{:?}", e.syllables());
    // The run was typed on: no restore point, the key goes on the run.
    let mut e = forced();
    run(&mut e, "i k 6 x d");
    assert!(e.syllables().is_empty() && run(&mut e, "_").preedit == "ik6xd");
}

/// §16.7, §16.5: with the core switch off, or without the table, every key's output is the engine's without the comparison.
#[test]
fn the_switch_off_and_no_table_are_the_engine_before() {
    let scripts = ["s p e e c h _ Esc", "t e s t 6 Esc _", "k d l _ Down", "l s _ j 3", "j 3 s p e e c h _ Enter", "i k 6 d", "o k _ _", "d e k 7 Esc"];
    for script in scripts {
        let mut base = plain();
        let want = trace(&mut base, script);
        let mut off = forced();
        off.set_english_auto(false).unwrap();
        assert!(trace(&mut off, script) == want, "switch off: {script}");
        let mut none = plain();
        none.set_auto_params(Params { theta: -100.0, ..Params::default() });
        assert!(trace(&mut none, script) == want, "no table: {script}");
        // And the forced engine does differ on the scripts with a decision point it can score (the premise of this test).
        let mut on = forced();
        let got = trace(&mut on, script);
        assert!((got != want) == ["s p e", "t e s", "k d l", "l s _", "j 3 s", "i k 6", "o k _"].iter().any(|p| script.starts_with(p)), "{script}");
    }
}

/// A table in the data folder is read by `Engine::new`; a malformed one is no table and no error.
#[test]
fn the_table_comes_with_the_data_folder() {
    let dir = std::env::temp_dir().join(format!("shanjie-auto-english-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    for f in std::fs::read_dir(root().join("data/lexicon")).unwrap() {
        let f = f.unwrap().path();
        if f.file_name().unwrap() != "english.tsv" {
            std::fs::copy(&f, dir.join(f.file_name().unwrap())).unwrap();
        }
    }
    let build = |dir: &Path| {
        let mut e = Engine::new(dir, Layout::Standard).unwrap();
        e.load_lm(&root().join("data/lm/bigram.sjlm")).unwrap();
        e.set_english(true).unwrap();
        e.set_english_overwrite(false).unwrap();
        e
    };
    assert!(!build(&dir).auto_table_loaded());
    std::fs::write(dir.join("english.tsv"), "T\t10\nN\t1\nE\tOK\t1\n").unwrap();
    assert!(!build(&dir).auto_table_loaded(), "a table that does not parse is no table");
    std::fs::copy(fixture(), dir.join("english.tsv")).unwrap();
    let mut e = build(&dir);
    assert!(e.auto_table_loaded());
    run(&mut e, "s p e e c h _");
    assert!(e.syllables() == ["_punct_speech"]);
    // A shared engine carries the table.
    let mut e2 = Engine::new_shared(&e.share().unwrap(), Layout::Standard, None).unwrap();
    e2.set_english(true).unwrap();
    e2.set_english_overwrite(false).unwrap();
    run(&mut e2, "s p e e c h _");
    assert!(e2.auto_table_loaded() && e2.syllables() == ["_punct_speech"]);
    let _ = std::fs::remove_dir_all(&dir);
}

/// What ships: the constants are a cell of the registered grid, and when data/lexicon/english.tsv exists it parses and fits 2,000,000 bytes.
#[test]
fn the_data_folder_decides_what_ships() {
    assert!([1.0, 2.0, 3.0].contains(&THETA) && [1.0, 1.5, 2.0].contains(&KAPPA) && [0.0, 3.0].contains(&KAPPA_DUP) && KAPPA_REV == 2.0);
    match std::fs::read_to_string(root().join("data/lexicon/english.tsv")) {
        Ok(t) => assert!(t.len() <= 2_000_000 && AutoTable::parse(&t).is_some(), "data/lexicon/english.tsv must parse and fit"),
        Err(e) => assert!(e.kind() == std::io::ErrorKind::NotFound),
    }
}

/// Python is the baseline (reference/proto/english_score.py --golden): lpE on the fixture table and the lpZ arithmetic, bit for bit. The golden was made on
/// macOS; Linux's libm differs in the last place, so this runs on the macOS CI only.
#[test]
fn scores_match_the_python_golden_bit_for_bit() {
    if !cfg!(target_os = "macos") {
        return;
    }
    let table = AutoTable::parse(&std::fs::read_to_string(fixture()).unwrap()).unwrap();
    let golden = std::fs::read_to_string(root().join("eval/golden/english-score.txt")).unwrap();
    let f64_of = |h: &str| f64::from_bits(u64::from_str_radix(h, 16).unwrap());
    let (mut e_rows, mut z_rows, mut unseen) = (0, 0, 0);
    for line in golden.lines().filter(|l| !l.starts_with('#')) {
        let f: Vec<&str> = line.split('\t').collect();
        match f[0] {
            "E" => {
                let got = table.lp_e(f[1], f[2]);
                assert!(got.to_bits() == u64::from_str_radix(f[3], 16).unwrap(), "lpE of row {e_rows}");
                unseen += (!table.has(f[2]) && got.is_finite()) as usize;
                e_rows += 1;
            }
            "Z" => {
                let n: Vec<u32> = f[3..6].iter().map(|x| x.parse().unwrap()).collect();
                let p = Params { theta: f64_of(f[6]), kappa: f64_of(f[7]), kappa_dup: f64_of(f[8]), kappa_rev: f64_of(f[9]) };
                let z = lp_z(f64_of(f[1]), f64_of(f[2]), n[0], n[1], n[2], &p);
                assert!(z.to_bits() == u64::from_str_radix(f[11], 16).unwrap(), "lpZ of row {z_rows}");
                assert!(takes_english(f64_of(f[10]), z, &p) == (f[12] == "1"), "the pick of row {z_rows}");
                z_rows += 1;
            }
            _ => panic!("an unknown golden row"),
        }
    }
    assert!(e_rows == 14 && z_rows == 7 && unseen >= 4, "{e_rows} {z_rows} {unseen}");
}
