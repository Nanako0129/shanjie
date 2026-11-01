//! `--colloquial-core` (docs/contracts/colloquial-term.md sections 2.3 and 3): the CLI applies the term under the engine's four
//! conditions with the one `TinyLm::rerank_colloquial`, and changes nothing anywhere else. The sentence is row 25 of
//! eval/bench/suite-v1/dev302.txt; the real table is data/lm/colloquial.sjlm (fails loudly without it).
use core::eval::sha256_hex;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..")
}

/// A scratch directory that is removed when it goes out of scope, an assertion failure included (the flipped `.sjtl` alone is 14.6 MB).
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
    let d = std::env::temp_dir().join(format!("shanjie-cli-colloquial-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    Dir(d)
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

fn row25(dir: &Path) -> String {
    let p = dir.join("row25.txt");
    let line = std::fs::read_to_string(root().join("eval/bench/suite-v1/dev302.txt")).unwrap().lines().nth(24).unwrap().to_string();
    assert!(line.starts_with("|執行這個程式|"), "row 25 of dev302");
    std::fs::write(&p, line + "\n").unwrap();
    p.to_string_lossy().into_owned()
}

/// Without `--colloquial-core` every output is what the CLI wrote before the option existed (hashes of `--dev 16 --context --packs acg --tinylm
/// data/lm/tinylm.sjtl`, `--dump --dump-words --rowstats --tinylm-scores`; taken 2026-10-27 on the 通語 SJ-LM-7-9.7M files from the measurement CLI of the
/// sj-lm-7 selection, which has no colloquial option in this path, run with its research flag `--chat-tri on-kn`, and equal to this binary's;
/// before that, from the binary that had no option at all, on the model-v6 files).
// macOS only: the dump and scores carry f64 values that the Linux libm rounds differently in the last bit (the WSL runner skips the existing
// tests of that kind too); this is the one check against the binary from before the option existed.
#[cfg(target_os = "macos")]
#[test]
fn no_option_is_byte_identical_to_before() {
    let d = tmp("golden");
    // `--no-reading-decode`: the decoder without the reading term (docs/contracts/reading-decode-2.md), the binary these hashes were taken from
    let o = full_run(&d, &BASE, &["16"], &["--no-reading-decode"]);
    let h = |s: &str| sha256_hex(s.as_bytes());
    assert_eq!(h(&o.dump), "ea7dac7b34a98ba85be7cab65e0c67cc41331981c127af618a50b12b08b184c7");
    assert_eq!(h(&o.words), "5c8e89a037daa1056ac9295d95995c8504c70a529272621cc45dcaf312a26bc9");
    assert_eq!(h(&o.rs), "a795b9d2c943e64587906438de9439fc5302cff79341a63684328ba44f9b960b");
    // The scores gained a `masked` field in sj-lm-7-pr2.md section 2.7 (empty on every one of these 16 rows); without it they are what they were.
    assert_eq!(h(&o.sc), "066e26f8d1f6d1e900efa987dd524a005262da404d02d3c6b59ad1519e7b25c1");
    assert_eq!(h(&o.sc.replace(", \"masked\": []", "")), "3762df4df12f06e84754f2d952dde28389a4e838821105db43a04d13fe9fe99e");
    // The summary line carries the model tags; with the fingerprint entries (new bigram, kn and tinylm hashes) only those 8-character tags changed
    // (the old hash 03876095… is this line with the old tags put back).
    assert_eq!(h(&o.stdout), "da55bd37457e24ef2f6c0bd2fe2334d45f84911a37cc30f8584e318b66f72642");
}

/// The acceptance row: the shipping rerank keeps `城市` (c 0; it was row 202, 羅斯松 against 螺絲鬆, until 通語 SJ-LM-7-9.7M, whose n-gram gets that one right), the
/// term picks `程式` (c 1), and the summary line is tagged with the table's SHA-256 prefix (the files of the two runs are otherwise the same except the
/// choice: the dump is the decoder's order in both).
#[test]
fn the_term_changes_row_25() {
    let d = tmp("r25");
    let rows = row25(&d);
    let (a, b) = (tmp("r25a"), tmp("r25b"));
    let ship = full_run(&a, &BASE[..9], &["--rows", &rows], &[]);
    let new = full_run(&b, &BASE[..9], &["--rows", &rows], &["--colloquial-core", TABLE]);
    assert!(ship.sc.contains("\"c\": 0,") && ship.stdout.contains("'top1': 0,"), "{}", ship.sc);
    assert!(new.sc.contains("\"c\": 1,") && new.stdout.contains("'top1': 1,"), "{}", new.sc);
    assert!(new.stdout.contains("+colloq:81c2470d"), "{}", new.stdout);
    assert_eq!(ship.dump, new.dump, "the dump keeps the decoder's order");
    assert_eq!(ship.words, new.words);
    let ll = |s: &str| s[s.find("\"ll\":").unwrap()..].to_string();
    assert_eq!(ll(&ship.sc), ll(&new.sc), "the small model's LLs are the same numbers");
}

/// The first 60 rows of dev302 and rows 196-230, which hold 202, 218 and 223: the three rows only the term fixed in dev302 under 通語 SJ-LM-6-9.1M (P4d). Rows 25, 36 and
/// others the term fixes under 通語 SJ-LM-7-9.7M are in the first 60 too; it only has to be a set on which the term changes picks.
fn rows_with_the_term_rows(dir: &Path) -> String {
    let all = std::fs::read_to_string(root().join("eval/bench/suite-v1/dev302.txt")).unwrap();
    let lines: Vec<&str> = all.lines().collect();
    let p = dir.join("rows.txt");
    std::fs::write(&p, [&lines[..60], &lines[195..230]].concat().join("\n") + "\n").unwrap();
    p.to_string_lossy().into_owned()
}

/// Everywhere else the option changes nothing: the formal profile, no small model, and another small model (one bit differs in the file, so its
/// SHA-256 is not the measured one). The conservative and the aggressive strength are no longer here (colloquial-levels): see
/// `the_term_applies_at_every_strength`.
#[test]
fn nothing_changes_elsewhere() {
    let flip = tmp("flip");
    let flipped = {
        let mut b = std::fs::read(root().join("data/lm/tinylm.sjtl")).unwrap();
        *b.last_mut().unwrap() ^= 1;
        let p = flip.join("other.sjtl");
        std::fs::write(&p, b).unwrap();
        p.to_string_lossy().into_owned()
    };
    let no_tl = ["--lm", "data/lm/bigram.sjlm", "--profile", "chat", "--context", "--packs", "acg"];
    let formal = ["--lm", "data/lm/bigram.sjlm", "--profile", "formal", "--context", "--packs", "acg", "--tinylm", "data/lm/tinylm.sjtl"];
    let other = ["--lm", "data/lm/bigram.sjlm", "--profile", "chat", "--context", "--packs", "acg", "--tinylm", &flipped];
    let rows_dir = tmp("elrows");
    let rows = rows_with_the_term_rows(&rows_dir);
    let cases: [(&str, &[&str], &[&str]); 3] = [
        ("formal", &formal, &[]),
        ("no small model", &no_tl, &[]),
        ("another small model", &other, &[]),
    ];
    for (i, (what, base, extra)) in cases.iter().enumerate() {
        let (a, b) = (tmp(&format!("el{i}a")), tmp(&format!("el{i}b")));
        let plain = full_run(&a, base, &["--rows", &rows], extra);
        let mut with: Vec<&str> = extra.to_vec();
        with.extend(["--colloquial-core", TABLE]);
        let tagged = full_run(&b, base, &["--rows", &rows], &with);
        same_outputs(&tagged, &plain, what);
    }
}

/// Four public rows: 25, 119 and 22 of eval/bench/suite-v1/dev302.txt, 19 of eval/dev/user-reported.txt.
fn level_rows(dir: &Path) -> String {
    let dev = std::fs::read_to_string(root().join("eval/bench/suite-v1/dev302.txt")).unwrap();
    let user = std::fs::read_to_string(root().join("eval/dev/user-reported.txt")).unwrap();
    let (dev, user): (Vec<&str>, Vec<&str>) = (dev.lines().collect(), user.lines().collect());
    let rows = [dev[24], dev[118], dev[21], user[18]];
    assert!(rows[0].starts_with("|執行這個程式|") && rows[1].starts_with("|政府決定廢止這項舊規定|") && rows[2].starts_with("|台北是一個很大的城市|") && rows[3].starts_with("|打開診斷頁閃退|"));
    let p = dir.join("level-rows.txt");
    std::fs::write(&p, rows.join("\n") + "\n").unwrap();
    p.to_string_lossy().into_owned()
}

/// docs/contracts/colloquial-levels.md: with `--colloquial-core` the terms apply at every strength, each with its own (alpha, tau, delta), and the summary
/// tag says so (`+colloq:`, after `+rl:` at the other two; then the reading term's `+rd+rx:`). The picks are those the CLI gives on the final 通語 SJ-LM-7-9.7M files with the cells of sj-lm-7.md section 2.9
/// (measured 2026-10-27; the cell arithmetic itself is `core/src/colloquial.rs`'s unit tests): standard (0.7, 2, 0) [1, 0, 0, 3]; conservative (0.7, 2, 1) keeps the
/// n-gram first on the three dev302 rows and picks 3 on the user-reported one, where the header triple without the term picks [0, 0, 0, 0]; aggressive (1.0, 2, 0.1)
/// picks [1, 5, 2, 3], the header triple without the term [0, 0, 0, 0].
#[test]
fn the_term_applies_at_every_strength() {
    let d = tmp("lvrows");
    let rows = level_rows(&d);
    let set = ["--rows", rows.as_str()];
    for (i, (level, with, without)) in [
        ("standard", vec![1, 0, 0, 3], None),
        ("conservative", vec![0, 0, 0, 3], Some(vec![0, 0, 0, 0])),
        ("aggressive", vec![1, 5, 2, 3], Some(vec![0, 0, 0, 0])),
    ]
    .into_iter()
    .enumerate()
    {
        let (a, b) = (tmp(&format!("lv{i}a")), tmp(&format!("lv{i}b")));
        let extra = ["--rerank-level", level];
        let new = full_run(&a, &BASE[..9], &set, &[&extra[..], &["--colloquial-core", TABLE]].concat());
        assert_eq!(chosen(&new.sc), with, "{level}: the level's own cell");
        let rl = if level == "standard" { String::new() } else { format!("+rl:{level}") };
        assert!(new.stdout.contains(&format!("{rl}+acg+tri:24ee8645+colloq:81c2470d+rd+rx:a821a206  {{")), "{level}: {}", new.stdout);
        if let Some(plain) = without {
            let old = full_run(&b, &BASE[..9], &set, &extra);
            assert_eq!(chosen(&old.sc), plain, "{level}: the header triple, no term");
        }
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

/// Rows 81, 159 and 10 of eval/dev/user-reported.txt (public): written to a rows file.
fn channel_rows(dir: &Path) -> String {
    let all = std::fs::read_to_string(root().join("eval/dev/user-reported.txt")).unwrap();
    let lines: Vec<&str> = all.lines().collect();
    assert!(lines[80].starts_with("|長歪了|") && lines[158].starts_with("|有點累贅|") && lines[9].starts_with("|你有東西要先進版控嗎|"));
    let p = dir.join("channel-rows.txt");
    std::fs::write(&p, [lines[80], lines[158], lines[9]].join("\n") + "\n").unwrap();
    p.to_string_lossy().into_owned()
}

/// The chosen index (`c`) of each row of a `--tinylm-scores` file.
fn chosen(sc: &str) -> Vec<usize> {
    sc.lines().map(|l| l.split("\"c\": ").nth(1).unwrap().split(',').next().unwrap().parse().unwrap()).collect()
}

/// The reading-channel term is gone (docs/contracts/reading-decode-2.md): `--no-reading-channel` is an unknown argument, the summary tag has no `+rc`, and the
/// colloquial term stands alone, so rows 81, 159 and 10 pick the P4d picks [0, 4, 0] (main's `--no-reading-channel`; main's default was [2, 0, 0], the channel's
/// doing, and row 81 is wrong there: the regression check of the removal). The decoder's reading term (the default) and `--no-reading-decode` pick the same here.
#[test]
fn the_colloquial_term_stands_alone() {
    let d = tmp("rc");
    let rows = channel_rows(&d);
    let set = ["--rows", rows.as_str()];
    let (a, b) = (tmp("rc-a"), tmp("rc-b"));
    let with = full_run(&a, &BASE[..9], &set, &["--colloquial-core", TABLE]);
    let off = full_run(&b, &BASE[..9], &set, &["--colloquial-core", TABLE, "--no-reading-decode"]);
    assert_eq!(chosen(&with.sc), vec![0, 4, 0], "the colloquial term alone, the reading term on");
    assert_ne!(chosen(&with.sc), vec![2, 0, 0], "premise: main's pick for these rows came from the channel");
    assert_eq!(chosen(&off.sc), vec![0, 4, 0], "the colloquial term alone, the reading term off");
    for out in [&with.stdout, &off.stdout] {
        assert!(out.contains("+colloq:81c2470d") && !out.contains("+rc"), "{out}");
    }
    assert!(with.stdout.contains("+colloq:81c2470d+rd+rx:a821a206  {") && off.stdout.contains("+colloq:81c2470d  {"), "{}{}", with.stdout, off.stdout);
    let mut args: Vec<String> = BASE[..9].iter().map(|s| s.to_string()).collect();
    args.extend(["--rows".to_string(), rows.clone(), "--colloquial-core".to_string(), TABLE.to_string(), "--no-reading-channel".to_string()]);
    let o = run(&args);
    assert!(!o.status.success() && text(&o.stderr).contains("unknown argument"), "{}", text(&o.stderr));
}
