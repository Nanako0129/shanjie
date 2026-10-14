//! `shanjie-eval --fuzzy` (docs/contracts/fuzzy-input.md section 2.3). Needs data/lm/bigram.sjlm like the other LM tests.
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
    assert!(s.contains("lm-chat+fz:0  {'n': 3, 'top1': 1,"), "{s}");
    let o = lm(ROWS, &["--fuzzy", "2"]);
    assert!(o.status.success());
    let s = summary(&o);
    assert!(s.contains("lm-chat+fz:2  {'n': 3, 'top1': 2,"), "{s}");
    // bit 4 alone does not touch the legal slip
    assert!(summary(&lm(ROWS, &["--fuzzy", "16"])).contains("'top1': 1,"));
}

/// Flags outside 0-31 and text are refused before anything runs.
#[test]
fn fuzzy_flags_are_validated() {
    for bad in ["32", "-1", "x", ""] {
        assert!(!lm(ROWS, &["--fuzzy", bad]).status.success(), "--fuzzy {bad:?}");
    }
}
