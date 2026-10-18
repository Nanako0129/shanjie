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

/// `--limit 70` keeps 70 rows: two full blocks of 32 and a short one of 6, so 2 threads take turns on the blocks and 3 threads
/// leave one worker without a full block. Each output is compared with the one-thread run.
#[test]
fn threads_leave_every_output_unchanged() {
    let tl = model("tinylm.sjtl");
    let chat = ["--profile", "chat", "--context", "--packs", "acg", "--tinylm", &tl, "--limit", "70"];
    let one = lm_run("c", "user-reported.txt", &chat, true, "1");
    assert!(one.iter().all(|s| !s.is_empty()), "chat: an output file is empty");
    for t in ["2", "3"] {
        assert_eq!(one, lm_run("c", "user-reported.txt", &chat, true, t), "chat, {t} threads");
    }
    let formal = ["--profile", "formal", "--limit", "70"];
    assert_eq!(lm_run("f", "user-typing.txt", &formal, false, "1"), lm_run("f", "user-typing.txt", &formal, false, "3"), "formal, 3 threads");
}

#[test]
fn bad_threads_exit_2_and_other_modes_refuse_it() {
    let lm = model("bigram.sjlm");
    let rows = root().join("eval/dev/user-typing.txt").to_string_lossy().into_owned();
    let head = ["--lm", lm.as_str(), "--profile", "chat", "--rows", rows.as_str()];
    // the last: `--threads` with no value
    for bad in [Some("0"), Some("x"), Some("-1"), Some(""), None] {
        let out = run(&[&head[..], &["--threads"], bad.as_slice()].concat());
        assert_eq!(out.status.code(), Some(2), "--threads {bad:?}");
        assert!(out.stdout.is_empty() && String::from_utf8_lossy(&out.stderr).contains("--threads"));
    }
    // The unigram rows mode and --predict do not take it.
    for args in [vec!["--rows", &rows, "--threads", "2"], vec!["--predict", &rows, "--lm", &lm, "--profile", "chat", "--threads", "2"]] {
        let out = run(&args);
        assert!(!out.status.success() && out.stdout.is_empty(), "{args:?}");
        assert!(String::from_utf8_lossy(&out.stderr).contains("unknown argument"), "{args:?}");
    }
}

/// Two rows with no decode path (a nonsense syllable): one in block 0 (row 10, one syllable) and one in block 1 (row 40, two syllables,
/// so its message differs). With any number of threads the error is the first in row order (row 10's), nothing is printed on stdout,
/// and the partial --dump, --dump-words and --tinylm-scores files are those of the one-thread run (rows 1 to 9).
#[test]
fn the_first_error_in_row_order_wins_with_the_same_partial_files() {
    let tl = model("tinylm.sjtl");
    let dir = std::env::temp_dir().join(format!("shanjie-cli-threads-err-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let mut n = 0;
    let rows: String = std::fs::read_to_string(root().join("eval/dev/user-typing.txt"))
        .unwrap()
        .lines()
        .map(|l| {
            let mut f: Vec<&str> = l.split('|').collect();
            if f.len() == 3 {
                n += 1;
                match n {
                    10 => f[2] = "zzz",
                    40 => f[2] = "zzz zzz",
                    _ => {}
                }
            }
            f.join("|") + "\n"
        })
        .collect();
    assert!(n >= 64, "the rows file needs a block 1 and a row 40");
    let rows_path = dir.join("rows.txt");
    std::fs::write(&rows_path, rows).unwrap();
    let lm = model("bigram.sjlm");
    let one_run = |threads: &str| {
        let f = |name: &str| dir.join(format!("{threads}-{name}")).to_string_lossy().into_owned();
        let (dump, words, sc) = (f("dump"), f("words"), f("scores"));
        let out = run(&[
            "--threads", threads, "--lm", &lm, "--profile", "chat", "--context", "--tinylm", &tl, "--rows", rows_path.to_str().unwrap(),
            "--dump", &dump, "--dump-words", &words, "--tinylm-scores", &sc,
        ]);
        let stderr: Vec<String> = String::from_utf8_lossy(&out.stderr).lines().filter(|l| !l.starts_with("tinylm:")).map(String::from).collect();
        let files: Vec<String> = [&dump, &words, &sc].iter().map(|p| std::fs::read_to_string(p).unwrap()).collect();
        (out.status.code(), out.stdout, stderr, files)
    };
    let one = one_run("1");
    assert_eq!(one.0, Some(1));
    assert!(one.1.is_empty() && one.2 == ["error: no decode path (input length 1)"], "{:?}", one.2);
    assert!(one.3.iter().all(|s| s.lines().next().is_some()), "the rows before the error are written");
    assert_eq!(one, one_run("3"));
    assert_eq!(one, one_run("2"));
    std::fs::remove_dir_all(dir).unwrap();
}
