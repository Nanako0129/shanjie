//! `--colloquial-core` (docs/spec/semantic-rerank.md §4.6, §10): the CLI applies the term under the engine's four
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
/// before that, from the binary that had no option at all, on the model-v6 files). The dump, words and rowstats hashes are those of 2026-10-27 still with the
/// 會意 SJ-SR-8-15M file (the n-gram's candidates and the plain rerank's picks on these 16 rows do not change); the scores and the summary line carry the new
/// small model's numbers and its `tl:` tag, so their hashes were taken again on 2026-11-06 from this binary and checked equal to the same run of main's binary
/// (the one built before SR-TW, which has no colloquial default and no cell change in this path) on the same `data/lm/tinylm.sjtl`.
// macOS only: the dump and scores carry f64 values that the Linux libm rounds differently in the last bit (the WSL runner skips the existing
// tests of that kind too); this is the one check against the binary from before the option existed.
#[cfg(target_os = "macos")]
#[test]
fn no_option_is_byte_identical_to_before() {
    let d = tmp("golden");
    // `--no-reading-decode`: the decoder without the reading term (docs/spec/decoder.md §5), the binary these hashes were taken from
    let o = full_run(&d, &BASE, &["16"], &["--no-reading-decode"]);
    let h = |s: &str| sha256_hex(s.as_bytes());
    assert_eq!(h(&o.dump), "ea7dac7b34a98ba85be7cab65e0c67cc41331981c127af618a50b12b08b184c7");
    assert_eq!(h(&o.words), "5c8e89a037daa1056ac9295d95995c8504c70a529272621cc45dcaf312a26bc9");
    assert_eq!(h(&o.rs), "a795b9d2c943e64587906438de9439fc5302cff79341a63684328ba44f9b960b");
    // The scores gained a `masked` field in docs/spec/decoder.md §7 (empty on every one of these 16 rows); without it they are what they were.
    assert_eq!(h(&o.sc), "729efe518ebc940400134b92d7e3a9d263de9dc4e3595ae780dab70b9bb9983d");
    assert_eq!(h(&o.sc.replace(", \"masked\": []", "")), "0a8e3d25171398c95806afe96ec6b781c89174f7422947ce69a06b52bf81800c");
    // The summary line carries the model tags; with the fingerprint entries (new bigram, kn and tinylm hashes) only those 8-character tags changed
    // (the old hash 03876095… is this line with the old tags put back).
    assert_eq!(h(&o.stdout), "bcbd01639fc673ad390e1e2a9609cae9674a3f3bd8bdd8c141afdd473e83e2f7");
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
    let ll = |s: &str| s[s.find("\"ll\":").unwrap()..s.find("\"masked\":").unwrap()].to_string();   // the term adds `col` after `masked`
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

/// docs/spec/semantic-rerank.md §4.4: with `--colloquial-core` the terms apply at every strength, each with its own (alpha, tau, delta), and the summary
/// tag says so (`+colloq:`, after `+rl:` at the other two; then the reading term's `+rd+rx:…+rm`). The picks are those the CLI gives on the final 會意 SJ-SR-8-15M
/// file with the cells of docs/spec/semantic-rerank.md §4.4 (measured 2026-11-06 with `--rows` over the four rows, `--tinylm-scores` `c`; the cell arithmetic itself is
/// `core/src/colloquial.rs`'s unit tests): standard (1.1, 2, 0) [1, 5, 2, 3]; conservative (1.3, 2, 1) keeps the n-gram first on rows 25 and 22 of dev302 where
/// the standard cell picks 1 and 2 (the conservative delta), and picks the standard's 5 and 3 on the other two; aggressive is the standard cell, so the same
/// [1, 5, 2, 3]. The header triple without the term picks [0, 0, 0, 3] at every strength (only the user-reported row is right without it), so the term changes
/// the pick of all three dev302 rows at standard and aggressive and of row 119 at conservative.
#[test]
fn the_term_applies_at_every_strength() {
    let d = tmp("lvrows");
    let rows = level_rows(&d);
    let set = ["--rows", rows.as_str()];
    let mut by_level = Vec::new();
    for (i, (level, with, without)) in [
        ("standard", vec![1, 5, 2, 3], None),
        ("conservative", vec![0, 5, 0, 3], Some(vec![0, 0, 0, 3])),
        ("aggressive", vec![1, 5, 2, 3], Some(vec![0, 0, 0, 3])),
    ]
    .into_iter()
    .enumerate()
    {
        let (a, b) = (tmp(&format!("lv{i}a")), tmp(&format!("lv{i}b")));
        let extra = ["--rerank-level", level];
        let new = full_run(&a, &BASE[..9], &set, &[&extra[..], &["--colloquial-core", TABLE]].concat());
        assert_eq!(chosen(&new.sc), with, "{level}: the level's own cell");
        let rl = if level == "standard" { String::new() } else { format!("+rl:{level}") };
        assert!(new.stdout.contains(&format!("{rl}+acg+tri:24ee8645+colloq:81c2470d+rd+rx:a821a206+rm  {{")), "{level}: {}", new.stdout);
        if let Some(plain) = without {
            let old = full_run(&b, &BASE[..9], &set, &extra);
            assert_eq!(chosen(&old.sc), plain, "{level}: the header triple, no term");
            assert_ne!(chosen(&new.sc), plain, "{level}: the term changes picks");
        }
        by_level.push(chosen(&new.sc));
    }
    assert_ne!(by_level[1], by_level[0], "premise: the conservative cell picks differently from the standard one");
    assert_eq!(by_level[2], by_level[0], "the aggressive cell is the standard one (docs/spec/semantic-rerank.md §4.4)");
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

/// The reading-channel term is gone (docs/spec/decoder.md §5): `--no-reading-channel` is an unknown argument, the summary tag has no `+rc`, and the
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
    assert!(with.stdout.contains("+colloq:81c2470d+rd+rx:a821a206+rm  {") && off.stdout.contains("+colloq:81c2470d  {"), "{}{}", with.stdout, off.stdout);
    let mut args: Vec<String> = BASE[..9].iter().map(|s| s.to_string()).collect();
    args.extend(["--rows".to_string(), rows.clone(), "--colloquial-core".to_string(), TABLE.to_string(), "--no-reading-channel".to_string()]);
    let o = run(&args);
    assert!(!o.status.success() && text(&o.stderr).contains("unknown argument"), "{}", text(&o.stderr));
}

/// SR-TW product (docs/spec/semantic-rerank.md §4.5): the mild first-word estimate is on by default, with no tag suffix (the shipping tag stays
/// `+colloq:81c2470d`); `--colloquial-no-unseen-start-unigram` turns it off and the tag gets `+us0`; `--colloquial-first-unigram` still runs (it used to be
/// refused with the estimate flag) and is tagged `+fu`; the research flag needs the table. Row 179 of eval/dev/user-reported.txt (`權限加好了`, public): the
/// estimate changes the colloquial scores of its candidates (`col`). It used to change the pick too (`c` 0 by default, 3 = 全線 without the estimate, measured
/// 2026-11-06); since 權限 is a rerank-protect word (data/lexicon/rerank-protect.tsv) the mask keeps candidate 0 either way.
#[test]
fn the_first_word_estimate_is_on_by_default_and_the_flag_turns_it_off() {
    let d = tmp("us0");
    let line = std::fs::read_to_string(root().join("eval/dev/user-reported.txt")).unwrap().lines().nth(178).unwrap().to_string();
    assert!(line.starts_with("|權限加好了|"), "row 179");
    let rows = d.join("row179.txt");
    std::fs::write(&rows, line + "\n").unwrap();
    let set = ["--rows", rows.to_str().unwrap()];
    let run_with = |name: &str, extra: &[&str]| full_run(&tmp(name), &BASE[..9], &set, &[&["--colloquial-core", TABLE][..], extra].concat());
    let (on, off, fu) = (run_with("us-on", &[]), run_with("us-off", &["--colloquial-no-unseen-start-unigram"]), run_with("us-fu", &["--colloquial-first-unigram"]));
    let col = |sc: &str| sc.split("\"col\": ").nth(1).map(|t| t.split(']').next().unwrap().to_string()).expect("a col field");
    assert_ne!(col(&on.sc), col(&off.sc), "the estimate changes this row's colloquial scores");
    assert_eq!((chosen(&on.sc), chosen(&off.sc)), (vec![0], vec![0]), "the protect word 權限 keeps candidate 0 with and without the estimate");
    assert!(on.stdout.contains("+colloq:81c2470d+rd+rx:") && !on.stdout.contains("+us"), "{}", on.stdout);
    assert!(off.stdout.contains("+colloq:81c2470d+us0+rd+rx:"), "{}", off.stdout);
    assert!(fu.stdout.contains("+colloq:81c2470d+fu+rd+rx:"), "{}", fu.stdout);
    let mut a: Vec<String> = BASE[..9].iter().map(|s| s.to_string()).collect();
    a.extend(["--dev".to_string(), "2".to_string(), "--colloquial-no-unseen-start-unigram".to_string()]);
    let o = run(&a);
    assert!(!o.status.success() && text(&o.stderr).contains("--colloquial-no-unseen-start-unigram needs --colloquial-core"), "{}", text(&o.stderr));
}

/// The numbers of the list under `"key": [...]` in one `--tinylm-scores` line (`null` is NaN); None when the key is not there.
fn list_of(line: &str, key: &str) -> Option<Vec<f64>> {
    let rest = line.split(&format!("\"{key}\": [")).nth(1)?;
    let body = rest.split(']').next().unwrap();
    Some(body.split(',').map(str::trim).filter(|t| !t.is_empty()).map(|t| if t == "null" { f64::NAN } else { t.parse().unwrap() }).collect())
}

/// FUSE-1: with the colloquial core in chat, every record that has an `ll` ends with `col` (one entry per candidate), and `colloquial::fused_pick` over the
/// record's ng, ll, col and masked with the level's `cell` is the record's `c`; without the core the line has no `col` key and is the line it was.
#[test]
fn scores_carry_col_and_recompute_the_pick() {
    use core::colloquial::{cell, fused_pick};
    use core::tinylm::RerankLevel;
    let d = tmp("colscores");
    let rows = level_rows(&d);
    let set = ["--rows", rows.as_str()];
    let mut checked = 0;
    for (i, (name, level)) in [("standard", RerankLevel::Standard), ("conservative", RerankLevel::Conservative), ("aggressive", RerankLevel::Aggressive)].into_iter().enumerate() {
        let (a, b) = (tmp(&format!("colsc{i}a")), tmp(&format!("colsc{i}b")));
        let extra = ["--rerank-level", name];
        let with = full_run(&a, &BASE[..9], &set, &[&extra[..], &["--colloquial-core", TABLE]].concat());
        let without = full_run(&b, &BASE[..9], &set, &extra);
        assert!(without.sc.lines().all(|l| !l.contains("\"col\"")), "{name}: no core, no col");
        assert_eq!(with.sc.lines().count(), without.sc.lines().count());
        for l in with.sc.lines() {
            let n = list_of(l, "ng").unwrap().len();
            assert!(l.ends_with("]}"), "{l}");
            let col = list_of(l, "col").unwrap_or_else(|| panic!("{name}: no col on {l}"));
            assert_eq!(col.len(), n, "{l}");
            assert!(col.iter().all(|x| x.is_finite()), "premise: these rows have finite col: {l}");
            let ll = list_of(l, "ll").unwrap();
            let mut masked = vec![false; n];
            for k in list_of(l, "masked").unwrap() {
                masked[k as usize] = true;
            }
            let c: usize = l.split("\"c\": ").nth(1).unwrap().split(',').next().unwrap().parse().unwrap();
            assert_eq!(fused_pick(&list_of(l, "ng").unwrap(), &ll, &col, cell(level), &masked, &[]), c, "{name}: {l}");
            checked += 1;
        }
    }
    assert!(checked >= 9, "{checked}");
}
