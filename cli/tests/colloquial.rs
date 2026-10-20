//! `--colloquial-core` (docs/contracts/colloquial-term.md sections 2.3 and 3): the CLI applies the term under the engine's four
//! conditions with the one `TinyLm::rerank_colloquial`, and changes nothing anywhere else. The sentence is row 202 of
//! eval/bench/suite-v1/dev302.txt, with its context; the real table is data/lm/colloquial.sjlm (fails loudly without it).
use core::eval::sha256_hex;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..")
}

fn tmp(name: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("shanjie-cli-colloquial-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    d
}

fn text(b: &[u8]) -> String {
    String::from_utf8(b.to_vec()).unwrap()
}

const TABLE: &str = "data/lm/colloquial.sjlm";
const BASE: [&str; 10] = ["--lm", "data/lm/bigram.sjlm", "--profile", "chat", "--context", "--packs", "acg", "--tinylm", "data/lm/tinylm.sjtl", "--dev"];
const NEEDS: &str = "data/lm/ (bigram.sjlm, classes.sjc, kn.sjkn, tinylm.sjtl, colloquial.sjlm) is needed: see tests/golden.rs and data/*.sha256";

fn run(args: &[String]) -> Output {
    for f in ["tinylm.sjtl", "colloquial.sjlm"] {
        assert!(root().join("data/lm").join(f).exists(), "{NEEDS}");
    }
    Command::new(env!("CARGO_BIN_EXE_shanjie-eval")).args(args).current_dir(root()).output().unwrap()
}

/// What one run wrote: the summary line, dump, words, rowstats, scores.
struct Out {
    stdout: String,
    dump: String,
    words: String,
    rs: String,
    sc: String,
}

/// One run with every output file. `set` is `--dev N` (the last of BASE takes N) or the options that replace it; `extra` goes at the end.
fn full_run(dir: &Path, base: &[&str], set: &[&str], extra: &[&str]) -> Out {
    let f = |n: &str| dir.join(n).to_string_lossy().into_owned();
    let mut a: Vec<String> = base.iter().map(|s| s.to_string()).collect();
    a.extend(set.iter().map(|s| s.to_string()));
    for (k, n) in [("--dump", "dump"), ("--dump-words", "words"), ("--rowstats", "rs")] {
        a.extend([k.to_string(), f(n)]);
    }
    if base.contains(&"--tinylm") || extra.contains(&"--tinylm") {
        a.extend(["--tinylm-scores".to_string(), f("sc")]);
    }
    a.extend(extra.iter().map(|s| s.to_string()));
    let o = run(&a);
    assert!(o.status.success(), "{}", text(&o.stderr));
    let rd = |n: &str| std::fs::read_to_string(dir.join(n)).unwrap_or_default();
    Out { stdout: text(&o.stdout), dump: rd("dump"), words: rd("words"), rs: rd("rs"), sc: rd("sc") }
}

/// The summary line without the tag the option adds.
fn untagged(out: &str) -> String {
    let i = out.find("+colloq:").expect("the tag");
    format!("{}{}", &out[..i], &out[i + "+colloq:".len() + 8..])
}

/// The files and the summary line are equal, the summary tag aside (stderr holds timings).
fn same_outputs(a: &Out, b: &Out, what: &str) {
    assert_eq!((&a.dump, &a.words, &a.rs, &a.sc), (&b.dump, &b.words, &b.rs, &b.sc), "{what}: files");
    assert_eq!(untagged(&a.stdout), b.stdout, "{what}: summary line");
}

fn row202(dir: &Path) -> String {
    let p = dir.join("row202.txt");
    let line = std::fs::read_to_string(root().join("eval/bench/suite-v1/dev302.txt")).unwrap().lines().nth(201).unwrap().to_string();
    assert!(line.starts_with("桌子一直在搖|桌腳的螺絲鬆掉了|"), "row 202 of dev302");
    std::fs::write(&p, line + "\n").unwrap();
    p.to_string_lossy().into_owned()
}

/// Without `--colloquial-core` every output is what the CLI wrote before the option existed (hashes taken from the binary before this change:
/// `--dev 16 --context --packs acg --tinylm data/lm/tinylm.sjtl`, `--dump --dump-words --rowstats --tinylm-scores`, model-v6 files).
// macOS only: the dump and scores carry f64 values that the Linux libm rounds differently in the last bit (the WSL runner skips the existing
// tests of that kind too); this is the one check against the binary from before the option existed.
#[cfg(target_os = "macos")]
#[test]
fn no_option_is_byte_identical_to_before() {
    let o = full_run(&tmp("golden"), &BASE, &["16"], &[]);
    let h = |s: &str| sha256_hex(s.as_bytes());
    assert_eq!(h(&o.dump), "7cd2988473895b8df271d8276489349809d2d6f50f761c56f3c96424b298c95a");
    assert_eq!(h(&o.words), "42152c06fa5c3b55a2e765ed9103128d4c780bfed4ff35e9f192220e3dce2a72");
    assert_eq!(h(&o.rs), "a795b9d2c943e64587906438de9439fc5302cff79341a63684328ba44f9b960b");
    assert_eq!(h(&o.sc), "b83921b3fdc3b8ea61a4b8f669383901d4308e1b02cb7c0e5d0153da43c50d3a");
    assert_eq!(h(&o.stdout), "c22805512788cbc3ad46acce4975f9f0202561aa1f5e6a3053c3dd32eadc67a5");
}

/// The acceptance row: the shipping rerank keeps `羅斯松` (c 0), the term picks `螺絲鬆` (c 4), and the summary line is tagged with the
/// table's SHA-256 prefix (the files of the two runs are otherwise the same except the choice: the dump is the decoder's order in both).
#[test]
fn the_term_changes_row_202() {
    let d = tmp("r202");
    let rows = row202(&d);
    let (a, b) = (tmp("r202a"), tmp("r202b"));
    let ship = full_run(&a, &BASE[..9], &["--rows", &rows], &[]);
    let new = full_run(&b, &BASE[..9], &["--rows", &rows], &["--colloquial-core", TABLE]);
    assert!(ship.sc.contains("\"c\": 0,") && ship.stdout.contains("'top1': 0,"), "{}", ship.sc);
    assert!(new.sc.contains("\"c\": 4,") && new.stdout.contains("'top1': 1,"), "{}", new.sc);
    assert!(new.stdout.contains("+colloq:2984eeb5"), "{}", new.stdout);
    assert_eq!(ship.dump, new.dump, "the dump keeps the decoder's order");
    assert_eq!(ship.words, new.words);
    let ll = |s: &str| s[s.find("\"ll\":").unwrap()..].to_string();
    assert_eq!(ll(&ship.sc), ll(&new.sc), "the small model's LLs are the same numbers");
}

/// Everywhere else the option changes nothing: the formal profile, the conservative and the aggressive strength, no small model, and
/// another small model (one bit differs in the file, so its SHA-256 is not the measured one).
#[test]
fn nothing_changes_elsewhere() {
    let flipped = {
        let mut b = std::fs::read(root().join("data/lm/tinylm.sjtl")).unwrap();
        *b.last_mut().unwrap() ^= 1;
        let p = tmp("flip").join("other.sjtl");
        std::fs::write(&p, b).unwrap();
        p.to_string_lossy().into_owned()
    };
    let no_tl = ["--lm", "data/lm/bigram.sjlm", "--profile", "chat", "--context", "--packs", "acg", "--dev"];
    let formal = ["--lm", "data/lm/bigram.sjlm", "--profile", "formal", "--packs", "acg", "--tinylm", "data/lm/tinylm.sjtl", "--dev"];
    let other = ["--lm", "data/lm/bigram.sjlm", "--profile", "chat", "--context", "--packs", "acg", "--tinylm", &flipped, "--dev"];
    let cases: [(&str, &[&str], &[&str]); 5] = [
        ("formal", &formal, &[]),
        ("conservative", &BASE, &["--rerank-level", "conservative"]),
        ("aggressive", &BASE, &["--rerank-level", "aggressive"]),
        ("no small model", &no_tl, &[]),
        ("another small model", &other, &[]),
    ];
    for (i, (what, base, extra)) in cases.iter().enumerate() {
        let (a, b) = (tmp(&format!("el{i}a")), tmp(&format!("el{i}b")));
        let plain = full_run(&a, base, &["40"], extra);
        let mut with: Vec<&str> = extra.to_vec();
        with.extend(["--colloquial-core", TABLE]);
        let tagged = full_run(&b, base, &["40"], &with);
        same_outputs(&tagged, &plain, what);
    }
}

/// In the shipping setting the option does change choices on a set of rows (so the checks above are not vacuously true), and `--threads 2`
/// writes what `--threads 1` writes.
#[test]
fn the_shipping_setting_applies_and_threads_agree() {
    let (a, b, c) = (tmp("th-plain"), tmp("th1"), tmp("th2"));
    let plain = full_run(&a, &BASE, &["302"], &[]);
    let one = full_run(&b, &BASE, &["302"], &["--colloquial-core", TABLE]);
    let two = full_run(&c, &BASE, &["302"], &["--colloquial-core", TABLE, "--threads", "2"]);
    assert_ne!(plain.rs, one.rs, "the term changes some rows of dev302");
    assert_eq!((&one.stdout, &one.dump, &one.words, &one.rs, &one.sc), (&two.stdout, &two.dump, &two.words, &two.rs, &two.sc));
}

#[test]
fn options_are_checked() {
    let args = |extra: &[&str]| -> Vec<String> {
        let mut a: Vec<String> = ["--lm", "data/lm/bigram.sjlm", "--profile", "chat", "--dev", "2"].map(String::from).to_vec();
        a.extend(extra.iter().map(|s| s.to_string()));
        a
    };
    let refused = |a: Vec<String>, needle: &str| {
        let o = run(&a);
        assert!(!o.status.success() && o.stdout.is_empty() && text(&o.stderr).contains(needle), "{needle}: {}", text(&o.stderr));
    };
    refused(args(&["--colloquial-core", "/nonexistent.sjlm"]), "--colloquial-core: file not found");
    // the P4b options are gone
    refused(args(&["--colloquial", TABLE, "--colloquial-mu", "0.5", "--colloquial-gamma", "0.25"]), "unknown argument");
    // no small model, or formal: accepted, the term simply does not apply
    assert!(run(&args(&["--colloquial-core", TABLE])).status.success());
}
