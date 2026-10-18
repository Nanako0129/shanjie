//! `--threads N` of the LM mode: every output (stdout, `--dump`, `--dump-words`, `--rowstats`, `--tinylm-scores`) is byte for byte
//! that of `--threads 1`. Needs the models of tests/golden.rs plus data/lm/tinylm.sjtl; like there, a missing one fails the test.
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..")
}

fn run(args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_shanjie-eval")).args(args).output().unwrap()
}

fn model(name: &str) -> String {
    let p = root().join("data/lm").join(name);
    assert!(p.exists(), "{} is missing: see tests/golden.rs (tinylm.sjtl: the S7 small-model release)", p.display());
    p.to_string_lossy().into_owned()
}

/// Runs the LM mode over `rows` with `extra` options and `threads`; returns stdout and the contents of the four output files
/// (an empty string for one the run did not ask for).
fn lm_run(tag: &str, rows: &str, extra: &[&str], scores: bool, threads: &str) -> Vec<String> {
    let dir = std::env::temp_dir().join(format!("shanjie-cli-threads-{tag}-{threads}-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let f = |n: &str| dir.join(n).to_string_lossy().into_owned();
    let (dump, words, rs, sc) = (f("dump"), f("words"), f("rowstats"), f("scores"));
    let rows = root().join("eval/dev").join(rows).to_string_lossy().into_owned();
    let lm = model("bigram.sjlm");
    let mut args = vec!["--threads", threads, "--lm", &lm, "--rows", &rows, "--dump", &dump, "--dump-words", &words, "--rowstats", &rs];
    args.extend(extra);
    if scores {
        args.extend(["--tinylm-scores", &sc]);
    }
    let out = run(&args);
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    let mut got = vec![String::from_utf8(out.stdout).unwrap()];
    for p in [&dump, &words, &rs, &sc] {
        got.push(std::fs::read_to_string(p).unwrap_or_default());
    }
    std::fs::remove_dir_all(dir).unwrap();
    got
}

/// user-reported has 135 rows, so 4 threads make two batches (128 + 7 rows) with a short last block, and 3 threads make two as well.
#[test]
fn threads_leave_every_output_unchanged() {
    let tl = model("tinylm.sjtl");
    for rows in ["user-reported.txt", "user-typing.txt"] {
        let chat = ["--profile", "chat", "--context", "--packs", "acg", "--tinylm", &tl];
        let formal = ["--profile", "formal"];
        let (one, four, three) = (lm_run("c", rows, &chat, true, "1"), lm_run("c", rows, &chat, true, "4"), lm_run("c", rows, &chat, true, "3"));
        assert!(one.iter().all(|s| !s.is_empty()), "chat {rows}: an output file is empty");
        assert_eq!(one, four, "chat {rows}, 4 threads");
        assert_eq!(one, three, "chat {rows}, 3 threads");
        let (one, four) = (lm_run("f", rows, &formal, false, "1"), lm_run("f", rows, &formal, false, "4"));
        assert_eq!(one, four, "formal {rows}, 4 threads");
    }
}

#[test]
fn bad_threads_exit_2_and_other_modes_refuse_it() {
    let lm = model("bigram.sjlm");
    let rows = root().join("eval/dev/user-typing.txt").to_string_lossy().into_owned();
    for bad in ["0", "x", "-1", ""] {
        let out = run(&["--lm", &lm, "--profile", "chat", "--rows", &rows, "--threads", bad]);
        assert_eq!(out.status.code(), Some(2), "--threads {bad:?}");
        assert!(out.stdout.is_empty() && String::from_utf8_lossy(&out.stderr).contains("--threads"));
    }
    // The unigram rows mode and --predict do not take it.
    for args in [vec!["--rows", &rows, "--threads", "2"], vec!["--predict", &rows, "--lm", &lm, "--profile", "chat", "--threads", "2"]] {
        let out = run(&args);
        assert!(!out.status.success() && out.stdout.is_empty(), "{args:?}");
    }
}
