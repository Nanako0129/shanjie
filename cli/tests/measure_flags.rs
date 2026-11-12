//! The two measurement flags of `shanjie-eval` (docs/spec/semantic-rerank.md §10, docs/spec/evaluation.md §1):
//! `--tinylm-paired` takes the loaded small model as the colloquial term's paired model, and `--picks FILE` writes the sentence
//! each row is scored on. Public rows and the repo's models only (data/lm/, see tests/golden.rs).
use cli::common::{format_rowstats, load_lenient};
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..")
}

/// A scratch directory removed when it goes out of scope (the flipped `.sjtl` alone is 14.6 MB).
struct Dir(PathBuf);
impl std::ops::Deref for Dir {
    type Target = Path;
    fn deref(&self) -> &Path {
        &self.0
    }
}
impl Drop for Dir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}
fn tmp(name: &str) -> Dir {
    let d = std::env::temp_dir().join(format!("shanjie-cli-measure-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    Dir(d)
}
fn text(b: &[u8]) -> String {
    String::from_utf8(b.to_vec()).unwrap()
}
fn s(v: &[&str]) -> Vec<String> {
    v.iter().map(|x| x.to_string()).collect()
}
fn run(args: &[String]) -> Output {
    for f in ["bigram.sjlm", "tinylm.sjtl", "colloquial.sjlm"] {
        assert!(root().join("data/lm").join(f).exists(), "data/lm/{f} is needed: see tests/golden.rs and data/*.sha256");
    }
    Command::new(env!("CARGO_BIN_EXE_shanjie-eval")).args(args).current_dir(root()).output().unwrap()
}
fn ok_run(args: &[String]) -> String {
    let o = run(args);
    assert!(o.status.success(), "{}", text(&o.stderr));
    text(&o.stdout)
}

const TABLE: &str = "data/lm/colloquial.sjlm";
const MODEL: &str = "data/lm/tinylm.sjtl";

/// The first 60 rows of dev302 and rows 196-230 (95 rows, the set of tests/colloquial.rs `rows_with_the_term_rows`); row 25
/// (`執行這個程式`) is one where the shipping term picks the second candidate (c 1).
fn term_rows(dir: &Path) -> String {
    let all = std::fs::read_to_string(root().join("eval/bench/suite-v1/dev302.txt")).unwrap();
    let lines: Vec<&str> = all.lines().collect();
    assert!(lines[24].starts_with("|執行這個程式|"), "row 25 of dev302");
    let p = dir.join("rows.txt");
    std::fs::write(&p, [&lines[..60], &lines[195..230]].concat().join("\n") + "\n").unwrap();
    p.to_string_lossy().into_owned()
}

/// The small model with its last byte flipped: it loads, and its SHA-256 is not the paired one (as tests/colloquial.rs builds it).
fn flipped(dir: &Path) -> String {
    let mut b = std::fs::read(root().join(MODEL)).unwrap();
    *b.last_mut().unwrap() ^= 1;
    let p = dir.join("other.sjtl");
    std::fs::write(&p, b).unwrap();
    p.to_string_lossy().into_owned()
}

fn sha8(path: &str) -> String {
    core::eval::sha256_hex(&std::fs::read(root().join(path)).unwrap())[..8].to_string()
}

/// One chat run over `rows` with the given small model and the table, writing rowstats, dump and scores (and picks if `picks`).
struct Out {
    stdout: String,
    rs: String,
    dump: String,
    sc: String,
    picks: String,
}
fn chat_run(dir: &Path, rows: &str, model: &str, extra: &[&str], picks: bool) -> Out {
    let f = |n: &str| dir.join(n).to_string_lossy().into_owned();
    let mut a = s(&["--lm", "data/lm/bigram.sjlm", "--profile", "chat", "--context", "--rows", rows, "--tinylm", model, "--colloquial-core", TABLE]);
    a.extend([("--rowstats", "rs"), ("--dump", "dump"), ("--tinylm-scores", "sc")].iter().flat_map(|(k, n)| [k.to_string(), f(n)]));
    if picks {
        a.extend(["--picks".to_string(), f("picks")]);
    }
    a.extend(s(extra));
    let stdout = ok_run(&a);
    let rd = |n: &str| std::fs::read_to_string(dir.join(n)).unwrap_or_default();
    Out { stdout, rs: rd("rs"), dump: rd("dump"), sc: rd("sc"), picks: rd("picks") }
}

/// The chosen index (`c`) of each row of a `--tinylm-scores` file, keyed by row number.
fn chosen(sc: &str) -> Vec<(usize, usize)> {
    sc.lines()
        .map(|l| {
            let num = |k: &str| l.split(&format!("\"{k}\": ")).nth(1).unwrap().split([',', '}']).next().unwrap().trim().parse::<usize>().unwrap();
            (num("i"), num("c"))
        })
        .collect()
}

/// §4.5 (the automated form of §4.1): on a small model that is not the paired one, `--tinylm-paired` turns the term on (some row's `c`
/// changes, every scores line carries `col`) and tags the summary `+pair:<sha8>` after `+colloq:`; without the flag the term is off there.
#[test]
fn paired_flag_turns_the_term_on_for_another_model() {
    let (r, x, a, b) = (tmp("p-rows"), tmp("p-x"), tmp("p-a"), tmp("p-b"));
    let rows = term_rows(&r);
    let other = flipped(&x);
    let plain = chat_run(&a, &rows, &other, &[], false);
    let paired = chat_run(&b, &rows, &other, &["--tinylm-paired"], false);
    assert!(!plain.sc.contains("\"col\""), "another model without the flag: the term does not run");
    assert!(paired.sc.lines().all(|l| l.contains("\"col\": [")), "with the flag every scored row carries col");
    assert_ne!(chosen(&plain.sc), chosen(&paired.sc), "the flag changes some pick");
    let x8 = &core::eval::sha256_hex(&std::fs::read(&other).unwrap())[..8];
    assert!(paired.stdout.contains(&format!("+colloq:{}+pair:{x8}", sha8(TABLE))), "{}", paired.stdout);
    assert!(!plain.stdout.contains("+pair:"), "{}", plain.stdout);
}

/// §4.5: on the shipping (paired) model the flag changes no file and the summary line only by `+pair:<sha8>`.
#[test]
fn paired_flag_changes_nothing_on_the_paired_model() {
    let (r, a, b) = (tmp("q-rows"), tmp("q-a"), tmp("q-b"));
    let rows = term_rows(&r);
    let plain = chat_run(&a, &rows, MODEL, &[], false);
    let paired = chat_run(&b, &rows, MODEL, &["--tinylm-paired"], false);
    assert_eq!((&plain.rs, &plain.dump, &plain.sc), (&paired.rs, &paired.dump, &paired.sc));
    let tag = format!("+pair:{}", sha8(MODEL));
    assert!(paired.stdout.contains(&tag), "{}", paired.stdout);
    assert_eq!(paired.stdout.replacen(&tag, "", 1), plain.stdout);
}

/// §4.3: the rank is the scores' `c` on every reranked row (row 25 has c 1, so "rank is always 0" fails), the sentence is the dump's line
/// at rank + 1, the rowstats recomputed from the picks' sentences equal the rowstats, and there is one line per row.
#[test]
fn picks_match_the_scores_the_dump_and_the_rowstats() {
    let (r, a) = (tmp("k-rows"), tmp("k-a"));
    let rows = term_rows(&r);
    let o = chat_run(&a, &rows, MODEL, &[], true);
    let picks: Vec<(usize, usize, String)> = o
        .picks
        .lines()
        .map(|l| {
            let f: Vec<&str> = l.splitn(3, '\t').collect();
            (f[0].parse().unwrap(), f[1].parse().unwrap(), f[2].to_string())
        })
        .collect();
    assert_eq!(picks.len(), o.rs.lines().count(), "one line per row");
    assert_eq!(picks.iter().map(|p| p.0).collect::<Vec<_>>(), (1..=picks.len()).collect::<Vec<_>>(), "row numbers in order");
    let c = chosen(&o.sc);
    assert!(c.iter().any(|&(_, c)| c >= 1), "the set has a row the rerank moves (row 25)");
    for (i, ci) in &c {
        assert_eq!(picks[i - 1].1, *ci, "row {i}: the rank is the scores' c");
    }
    for (i, rank, sent) in &picks {
        let want = format!("{i}\t{}\t{sent}\t", rank + 1);
        assert!(o.dump.lines().any(|l| l.starts_with(&want)), "row {i}: the dump's line at rank {}", rank + 1);
    }
    let len = load_lenient().unwrap();
    let gold: Vec<String> = std::fs::read_to_string(&rows).unwrap().lines().map(|l| l.split('|').nth(1).unwrap().to_string()).collect();
    let again: Vec<(bool, String, String)> = picks.iter().zip(&gold).map(|((_, _, p), g)| (len.apply(p) == len.apply(g), p.clone(), g.clone())).collect();
    assert_eq!(format_rowstats(&again), o.rs, "the picks' sentences give the rowstats");
}

/// §2.2: with `--fuzzy`, a row no reading covers gets exactly `row\t-\t`, and the line count still equals the rowstats'.
#[test]
fn picks_of_a_row_without_candidates() {
    let d = tmp("n");
    let rows = d.join("rows.txt");
    std::fs::write(&rows, "|知道|ㄗ ㄉㄠˋ\n|知道|ㄅㄅ ㄉㄠˋ\n|知道|ㄓ ㄉㄠˋ\n").unwrap();
    let f = |n: &str| d.join(n).to_string_lossy().into_owned();
    ok_run(&s(&["--lm", "data/lm/bigram.sjlm", "--profile", "chat", "--rows", &f("rows.txt"), "--fuzzy", "0", "--picks", &f("picks"), "--rowstats", &f("rs")]));
    let picks = std::fs::read_to_string(d.join("picks")).unwrap();
    let rs = std::fs::read_to_string(d.join("rs")).unwrap();
    assert_eq!(picks.lines().count(), rs.lines().count());
    assert_eq!(picks.lines().nth(1), Some("2\t-\t"), "{picks}");
}

/// §4.3: blocks of 32 rows decoded by 4 workers are written in row order: the whole dev302 (10 blocks) gives the same picks file as one thread.
#[test]
fn picks_threads_agree_on_the_whole_dev302() {
    let (a, b) = (tmp("t1"), tmp("t4"));
    let one = |d: &Path, n: &str| {
        let p = d.join("picks").to_string_lossy().into_owned();
        ok_run(&s(&["--lm", "data/lm/bigram.sjlm", "--profile", "chat", "--context", "--dev", "302", "--threads", n, "--picks", &p]));
        std::fs::read_to_string(&p).unwrap()
    };
    let (p1, p4) = (one(&a, "1"), one(&b, "4"));
    assert_eq!(p1.lines().count(), 302);
    assert_eq!(p1, p4);
}

/// §4.4: `--set holdout --picks` fails before any file is read or created; `--tinylm-paired` needs `--tinylm` and `--colloquial-core`;
/// the messages carry no row content.
#[test]
fn the_flags_are_refused_where_they_do_not_apply() {
    let d = tmp("refuse");
    let p = d.join("picks").to_string_lossy().into_owned();
    let refused = |a: Vec<String>, needle: &str| {
        let o = run(&a);
        let err = text(&o.stderr);
        assert!(!o.status.success() && o.stdout.is_empty() && err.contains(needle), "{needle}: {err}");
        assert!(err.lines().count() <= 1, "one line, no row content: {err}");
    };
    refused(s(&["--lm", "data/lm/bigram.sjlm", "--profile", "chat", "--set", "holdout", "--picks", &p]), "--picks is not allowed with --set holdout");
    assert!(!Path::new(&p).exists(), "nothing was created");
    let base = s(&["--lm", "data/lm/bigram.sjlm", "--profile", "chat", "--dev", "2", "--tinylm-paired"]);
    refused(base.clone(), "--tinylm-paired needs --tinylm and --colloquial-core");
    refused([base.clone(), s(&["--tinylm", MODEL])].concat(), "--tinylm-paired needs --tinylm and --colloquial-core");
    refused([base, s(&["--colloquial-core", TABLE])].concat(), "--tinylm-paired needs --tinylm and --colloquial-core");
}
