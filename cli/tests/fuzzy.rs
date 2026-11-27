//! `shanjie-eval --fuzzy` (docs/spec/decoder.md §6.1). Needs data/lm/bigram.sjlm like the other LM tests.
use std::process::{Command, Output};
use std::sync::atomic::{AtomicUsize, Ordering};

static RUN: AtomicUsize = AtomicUsize::new(0);

fn lm(rows: &str, extra: &[&str]) -> Output {
    let dir = std::env::temp_dir().join(format!("shanjie-fuzzy-cli-{}-{}", std::process::id(), RUN.fetch_add(1, Ordering::Relaxed)));
    std::fs::create_dir_all(&dir).unwrap();
    let file = dir.join("rows.txt");
    std::fs::write(&file, rows).unwrap();
    let lm_path = concat!(env!("CARGO_MANIFEST_DIR"), "/../data/lm/bigram.sjlm");
    assert!(std::path::Path::new(lm_path).exists(), "data/lm/bigram.sjlm is missing (see core/tests/engine_lm.rs)");
    let out = Command::new(env!("CARGO_BIN_EXE_shanjie-eval"))
        .args(["--lm", lm_path, "--profile", "chat", "--rows", file.to_str().unwrap()])
        .args(extra)
        .output()
        .unwrap();
    std::fs::remove_dir_all(&dir).unwrap();
    out
}
fn summary(o: &Output) -> String {
    String::from_utf8(o.stdout.clone()).unwrap().lines().find(|l| l.starts_with("## ")).unwrap().to_string()
}

/// `前文|句子|讀音`: 知道 read ㄗ ㄉㄠˋ (a slip of ㄓ), and a row with a syllable nothing covers (ㄅㄅ has no neighbour reading).
const ROWS: &str = "|知道|ㄗ ㄉㄠˋ\n|知道|ㄅㄅ ㄉㄠˋ\n|知道|ㄓ ㄉㄠˋ\n";

/// Without `--fuzzy` the output is as before: no tag, and a row no reading covers stops the run.
#[test]
fn no_flag_no_tag_and_an_uncovered_row_stops_the_run() {
    let o = lm("|知道|ㄗ ㄉㄠˋ\n|知道|ㄓ ㄉㄠˋ\n", &[]);
    assert!(o.status.success());
    let s = summary(&o);
    assert!(!s.contains("+fz"), "{s}");
    assert!(s.contains("'top1': 1,"), "the slip is wrong without the pair: {s}");
    assert!(!lm(ROWS, &[]).status.success(), "an uncovered row stops the run");
}

/// With `--fuzzy`: the tag is `+fz:<flags>`, a row no reading covers counts wrong (empty first) and the run goes on, and the
/// pair's bit makes the slip right.
#[test]
fn fuzzy_tags_the_summary_counts_an_uncovered_row_wrong_and_goes_on() {
    let o = lm(ROWS, &["--fuzzy", "0"]);
    assert!(o.status.success());
    let s = summary(&o);
    assert!(s.contains("+fz:0+rd+rx:") && s.contains("  {'n': 3, 'top1': 1,"), "{s}");
    let o = lm(ROWS, &["--fuzzy", "2"]);
    assert!(o.status.success());
    let s = summary(&o);
    assert!(s.contains("+fz:2+rd+rx:") && s.contains("  {'n': 3, 'top1': 2,"), "{s}");
    // bit 4 alone does not touch the legal slip
    assert!(summary(&lm(ROWS, &["--fuzzy", "16"])).contains("'top1': 1,"));
}

/// Flags outside 0-63 and text are refused before anything runs.
#[test]
fn fuzzy_flags_are_validated() {
    for bad in ["64", "-1", "x", ""] {
        assert!(!lm(ROWS, &["--fuzzy", bad]).status.success(), "--fuzzy {bad:?}");
    }
}

/// The dump of `--fuzzy 15` (or `flags`) on `rows` with the given extra options, as (sentence, score) pairs.
fn dump_of(rows: &str, flags: &str, extra: &[&str]) -> (String, Vec<(String, f64)>) {
    let dump = std::env::temp_dir().join(format!("shanjie-fuzzy-dump-{}-{}", std::process::id(), RUN.fetch_add(1, Ordering::Relaxed)));
    let mut args = vec!["--fuzzy", flags, "--dump", dump.to_str().unwrap()];
    args.extend_from_slice(extra);
    let o = lm(rows, &args);
    assert!(o.status.success());
    let text = std::fs::read_to_string(&dump).unwrap();
    std::fs::remove_file(&dump).unwrap();
    let cands = text.lines().map(|l| { let f: Vec<&str> = l.split('\t').collect(); (f[2].to_string(), f[3].parse().unwrap()) }).collect();
    (summary(&o), cands)
}
/// dev302 row 298 (排骨酥, ㄙㄨ).
fn dump_298(extra: &[&str]) -> (String, Vec<(String, f64)>) {
    dump_of("|這家店的排骨酥很好吃|ㄓㄜˋ ㄐㄧㄚ ㄉㄧㄢˋ ㄉㄜ˙ ㄆㄞˊ ㄍㄨˇ ㄙㄨ ㄏㄣˇ ㄏㄠˇ ㄔ\n", "15", extra)
}
fn score_of(c: &[(String, f64)], w: &str) -> f64 {
    c.iter().find(|x| x.0 == w).unwrap_or_else(|| panic!("{w} is not in {c:?}")).1
}

/// `--fuzzy-delta-sound` (docs/spec/decoder.md §6.1, evaluation only): 2.5 is the default (`fuzzy::DELTA_SOUND`), so the summary line (tag and
/// top1_sha256) and the dump are the same with and without it; another D appends `:dD` to the tag and moves the score of a candidate read
/// through a fuzzy-sound syllable (書 for ㄙㄨ) by exactly the delta difference, while the exact candidate (酥) does not move.
#[test]
fn fuzzy_delta_sound_default_is_identical_and_other_values_shift_fuzzy_candidates() {
    let (s0, c0) = dump_298(&[]);
    let (s1, c1) = dump_298(&["--fuzzy-delta-sound", "2.5"]);
    assert_eq!((&s0, &c0), (&s1, &c1));
    assert!(s0.contains("+fz:15+rd+rx:"), "{s0}"); // the reading term's tag ends every setting (docs/spec/decoder.md §5)
    let (s2, c2) = dump_298(&["--fuzzy-delta-sound", "1"]);
    assert!(s2.contains("+fz:15:d1+rd+rx:"), "{s2}");
    // The first choice of this row no longer changes with D on 通語 SJ-LM-7-9.7M (it did on SJ-LM-6-9.1M); the exact score shift below
    // is what shows D is applied.
    let (shu, su) = ("這家店的排骨書很好吃", "這家店的排骨酥很好吃");
    assert!((score_of(&c2, shu) - score_of(&c0, shu) - 1.5).abs() < 1e-9, "{c0:?} {c2:?}");
    assert_eq!(score_of(&c0, su), score_of(&c2, su));
}

/// The adjacent-key penalty (delta 2) is not the sound penalty: with `--fuzzy-delta-sound 1` a candidate read through an adjacent-key
/// syllable (ㄅㄩ is not a lexicon syllable; 鱉星 is a word of ㄅㄧㄝ ㄒㄧㄥ) keeps exactly its default score.
#[test]
fn fuzzy_delta_sound_leaves_the_adjacent_key_penalty_alone() {
    let rows = "|鱉星|ㄅㄩ ㄒㄧㄥ\n";
    let (_, c0) = dump_of(rows, "31", &[]);
    let (_, c2) = dump_of(rows, "31", &["--fuzzy-delta-sound", "1"]);
    assert_eq!(score_of(&c0, "鱉星"), score_of(&c2, "鱉星"), "{c0:?} {c2:?}");
}

/// The option is refused without a fuzzy-sound bit (it would change nothing) and for a value that is not a number above 0.
#[test]
fn fuzzy_delta_sound_is_validated() {
    let msg = |extra: &[&str]| {
        let o = lm("|知道|ㄗ ㄉㄠˋ\n", extra);
        assert!(!o.status.success(), "{extra:?}");
        String::from_utf8_lossy(&o.stderr).to_string()
    };
    assert!(msg(&["--fuzzy-delta-sound", "2"]).contains("needs --fuzzy with a sound bit"));
    assert!(msg(&["--fuzzy", "16", "--fuzzy-delta-sound", "2"]).contains("needs --fuzzy with a sound bit"));
    for bad in ["x", "0", "-1", "inf", "NaN"] {
        assert!(msg(&["--fuzzy", "2", "--fuzzy-delta-sound", bad]).contains("takes a number above 0"), "{bad}");
    }
    assert!(lm("|知道|ㄗ ㄉㄠˋ\n", &["--fuzzy", "2", "--fuzzy-delta-sound", "2"]).status.success());
}

/// docs/contracts/typo-one-sym.md §2.4 item 2 and §4.1 (1): ㄋㄟ is a syllable only the one-symbol table has an alternative for. With the flags 0, 15, 16 and 31 (no bit 5) the
/// decoder never reads that table: no path, the row counts wrong with an empty dump, as before bit 5 existed. With 32, 48 and 63 the row has candidates (非 is one), and the single
/// syllable keeps the larger cap of one syllable alone (24 words, `fuzzy::ONE_SYM_SINGLE_TOTAL`, against 12 for a span of several).
#[test]
fn a_one_symbol_only_syllable_has_no_path_below_bit_5_and_a_capped_list_with_it() {
    let rows = "|非|ㄋㄟ\n";
    for flags in ["0", "15", "16", "31"] {
        let (s, c) = dump_of(rows, flags, &[]);
        assert!(c.is_empty() && s.contains("'top1': 0,"), "--fuzzy {flags}: {s} {c:?}");
    }
    for flags in ["32", "48", "63"] {
        let (_, c) = dump_of(rows, flags, &[]);
        assert!(c.iter().any(|x| x.0 == "非"), "--fuzzy {flags}: {c:?}");
        assert_eq!(c.len(), core::fuzzy::ONE_SYM_SINGLE_TOTAL, "--fuzzy {flags}");
    }
    let (s, _) = dump_of("|我想去|ㄨㄛˇ ㄒㄧㄟˇ ㄑㄩˋ\n", "32", &[]);
    assert!(s.contains("+fz:32+") && s.contains("'top1': 1,"), "{s}");
    assert!(summary(&lm("|我想去|ㄨㄛˇ ㄒㄧㄟˇ ㄑㄩˋ\n", &["--fuzzy", "63"])).contains("'top1': 1,"));
}
