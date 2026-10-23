//! `--colloquial-core` (docs/contracts/colloquial-term.md sections 2.3 and 3): the CLI applies the term under the engine's four
//! conditions with the one `TinyLm::rerank_colloquial`, and changes nothing anywhere else. The sentence is row 202 of
//! eval/bench/suite-v1/dev302.txt, with its context; the real table is data/lm/colloquial.sjlm (fails loudly without it).
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

/// The decoder without the reading term (docs/contracts/reading-decoder.md): the pins below are the colloquial term's own, measured before the
/// term was in the decoder.
const NORD: &str = "--no-reading-decode";

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
/// `--dev 16 --context --packs acg --tinylm data/lm/tinylm.sjtl`, `--dump --dump-words --rowstats --tinylm-scores`, model-v6 files; with
/// `--no-reading-decode`, the term of docs/contracts/reading-decoder.md being what the default run now adds).
// macOS only: the dump and scores carry f64 values that the Linux libm rounds differently in the last bit (the WSL runner skips the existing
// tests of that kind too); this is the one check against the binary from before the option existed.
#[cfg(target_os = "macos")]
#[test]
fn no_option_is_byte_identical_to_before() {
    let d = tmp("golden");
    let o = full_run(&d, &BASE, &["16"], &[NORD]);
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

/// The first 60 rows of dev302 and rows 196-230, which hold 202, 218 and 223: the three rows only the term fixes in dev302 (P4d).
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

/// Four public rows: 127 and 202 of eval/bench/suite-v1/dev302.txt, 54 and 132 of eval/dev/user-reported.txt.
fn level_rows(dir: &Path) -> String {
    let dev = std::fs::read_to_string(root().join("eval/bench/suite-v1/dev302.txt")).unwrap();
    let user = std::fs::read_to_string(root().join("eval/dev/user-reported.txt")).unwrap();
    let (dev, user): (Vec<&str>, Vec<&str>) = (dev.lines().collect(), user.lines().collect());
    let rows = [dev[126], dev[201], user[53], user[131]];
    assert!(rows[0].starts_with("廟裡有場法會|道士在廟裡設壇作法|") && rows[2].starts_with("|可以準備做|") && rows[3].starts_with("|那要遍歷所有端點的分類"));
    let p = dir.join("level-rows.txt");
    std::fs::write(&p, rows.join("\n") + "\n").unwrap();
    p.to_string_lossy().into_owned()
}

/// docs/contracts/colloquial-levels.md: with `--colloquial-core` the terms apply at every strength, each with its own (alpha, tau, delta), and the summary
/// tag says so (`+colloq:<sha8>`, after `+rl:` at the other two). The picks are those of the offline arithmetic (experiments/strength-colloq `cell_pick`, checked
/// row by row on the whole public sets by experiments/colloq-levels): standard [5, 4, 0, 3]; conservative (1.0, 2, 3.0) keeps the n-gram first on all four,
/// where the header triple without the term picks [1, 0, 0, 7]; aggressive (0.5, 2, 0.1) picks [5, 4, 1, 3], the header triple (0.3, 2, 0.25) [1, 0, 0, 7].
/// All runs are `--no-reading-decode`: the offline arithmetic has no reading term.
#[test]
fn the_term_applies_at_every_strength() {
    let d = tmp("lvrows");
    let rows = level_rows(&d);
    let set = ["--rows", rows.as_str()];
    for (i, (level, with, without)) in [
        ("standard", vec![5, 4, 0, 3], None),
        ("conservative", vec![0, 0, 0, 0], Some(vec![1, 0, 0, 7])),
        ("aggressive", vec![5, 4, 1, 3], Some(vec![1, 0, 0, 7])),
    ]
    .into_iter()
    .enumerate()
    {
        let (a, b) = (tmp(&format!("lv{i}a")), tmp(&format!("lv{i}b")));
        let extra = ["--rerank-level", level, NORD];
        let new = full_run(&a, &BASE[..9], &set, &[&extra[..], &["--colloquial-core", TABLE]].concat());
        assert_eq!(chosen(&new.sc), with, "{level}: the level's own cell");
        let rl = if level == "standard" { String::new() } else { format!("+rl:{level}") };
        assert!(new.stdout.contains(&format!("{rl}+acg+colloq:2984eeb5  {{")), "{level}: {}", new.stdout);
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

/// Rows 10, 81 and 102 of eval/dev/user-reported.txt (public): written to a rows file.
fn channel_rows(dir: &Path) -> String {
    let all = std::fs::read_to_string(root().join("eval/dev/user-reported.txt")).unwrap();
    let lines: Vec<&str> = all.lines().collect();
    assert!(lines[9].starts_with("|你有東西要先進版控嗎|") && lines[80].starts_with("|長歪了|") && lines[101].starts_with("幹opencc|是不是很雷啊|"));
    let p = dir.join("channel-rows.txt");
    std::fs::write(&p, [lines[9], lines[80], lines[101]].join("\n") + "\n").unwrap();
    p.to_string_lossy().into_owned()
}

/// The chosen index (`c`) of each row of a `--tinylm-scores` file.
fn chosen(sc: &str) -> Vec<usize> {
    sc.lines().map(|l| l.split("\"c\": ").nth(1).unwrap().split(',').next().unwrap().parse().unwrap()).collect()
}

/// docs/contracts/reading-decoder.md: the reading term is in the decoder and the fusion has none. With `--colloquial-core` the three rows of the set where
/// the term matters pick [0, 1, 0] by default (row 10's decoder first is the right `版控`) and the colloquial term alone, `--no-reading-decode`,
/// picks [0, 1, 1]; the version before the term picked [2, 0, 0] with a reading-channel term in the fusion. The default tag ends in `+rd`
/// (`+colloq:<sha8>+rd`, no `+rc`), the other has neither; the decoder's order (the dump) is what differs.
#[test]
fn the_reading_term_is_in_the_decoder_and_the_fusion_has_none() {
    let d = tmp("rd");
    let rows = channel_rows(&d);
    let set = ["--rows", rows.as_str()];
    let (a, b) = (tmp("rd-a"), tmp("rd-b"));
    let with = full_run(&a, &BASE[..9], &set, &["--colloquial-core", TABLE]);
    let alone = full_run(&b, &BASE[..9], &set, &["--colloquial-core", TABLE, NORD]);
    assert_eq!(chosen(&with.sc), vec![0, 1, 0], "with the term in the decoder");
    assert_eq!(chosen(&alone.sc), vec![0, 1, 1], "the colloquial term alone");
    assert!(with.stdout.contains("+colloq:2984eeb5+rd  {") && alone.stdout.contains("+colloq:2984eeb5  {"), "{}{}", with.stdout, alone.stdout);
    assert!(!with.stdout.contains("+rc") && !alone.stdout.contains("+rc"), "no reading-channel tag any more");
    assert_ne!(with.dump, alone.dump, "the decoder's order is what the term changes");
    let first = |dump: &str| dump.lines().find(|l| l.starts_with("1\t1\t")).unwrap().split('\t').nth(2).unwrap().to_string();
    assert_eq!((first(&with.dump).as_str(), first(&alone.dump).as_str()), ("你有東西要先進版控嗎", "你有東西要先進版空嗎"));
}
