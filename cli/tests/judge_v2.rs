//! `--judge v2`, `--accept` and `--rejudge` (docs/spec/evaluation.md §3.5–§3.7).
//! The default judge is v1 and nothing it prints changes; v2 reads forms.tsv, forms-extra.tsv and the accept file before any output
//! file is created; its summary tag `+jv2:<A>:<B>:<C>` follows the three files; `--rejudge` of a run's own `--picks` equals that run.
use core::eval::{sha256_hex, Accept};
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..")
}

/// A scratch directory removed on drop, so a failed assert leaves nothing behind either.
struct Tmp(PathBuf);

impl Tmp {
    fn new(name: &str) -> Tmp {
        let d = std::env::temp_dir().join(format!("shanjie-cli-judge-v2-{name}-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        Tmp(d)
    }
    fn file(&self, f: &str) -> String {
        self.0.join(f).to_string_lossy().into_owned()
    }
    fn write(&self, f: &str, text: &str) -> String {
        let p = self.file(f);
        std::fs::write(&p, text).unwrap();
        p
    }
}

impl Drop for Tmp {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// The decoder the Python reference implements (no reading term, no recurrence term), as the golden tests run it; `profile` chat or formal.
fn run_in(profile: &str, args: &[&str], env: &[(&str, &str)]) -> Output {
    let lm = root().join("data/lm/bigram.sjlm");
    assert!(lm.exists(), "data/lm/bigram.sjlm is needed: see tests/golden.rs");
    Command::new(env!("CARGO_BIN_EXE_shanjie-eval"))
        .args(["--lm", lm.to_str().unwrap(), "--profile", profile, "--no-reading-decode", "--no-recur"])
        .args(args)
        .envs(env.iter().copied())
        .output()
        .unwrap()
}

fn run(args: &[&str], env: &[(&str, &str)]) -> Output {
    run_in("chat", args, env)
}

fn ok_run(o: Output) -> String {
    assert!(o.status.success(), "{}", String::from_utf8_lossy(&o.stderr));
    String::from_utf8(o.stdout).unwrap()
}

fn oks(file: &str) -> Vec<u8> {
    std::fs::read_to_string(file).unwrap().lines().map(|l| l.split('\t').nth(1).unwrap().parse().unwrap()).collect()
}

/// The three segments of the summary line's `+jv2:A:B:C` tag.
fn jv2(summary: &str) -> Vec<String> {
    let tag = summary.split_whitespace().nth(2).unwrap();
    let t = &tag[tag.find("+jv2:").unwrap_or_else(|| panic!("no +jv2 in {tag}")) + 5..];
    t.split(':').map(String::from).collect()
}

const EMPTY_FORMS: &str = "# header only\n";
const EXTRA_PAIR: &str = "# header\nword\t潮濕\t潮溼\tㄔㄠˊ ㄕ\t30/70\t字條 0\n";
/// 潮濕 and 潮溼 read the same and the decoder gives one spelling.
const TWO_SPELLINGS: &str = "|潮濕|ㄔㄠˊ ㄕ\n|潮溼|ㄔㄠˊ ㄕ\n";

/// Acceptance: without `--judge` nothing changes, and `--forms-rowstats` reads forms.tsv alone: an extra table next to it (here one that would
/// make both spellings right) leaves its bytes as they are. Under `--judge v2` the same table is what makes both right.
#[test]
fn default_judge_is_v1_and_forms_rowstats_ignores_the_extra_table() {
    let t = Tmp::new("v1");
    let rows = t.write("rows.txt", TWO_SPELLINGS);
    let (forms, extra) = (t.write("forms.tsv", EMPTY_FORMS), t.write("extra.tsv", EXTRA_PAIR));
    let base = [("SHANJIE_FORMS", forms.as_str())];
    let with_extra = [("SHANJIE_FORMS", forms.as_str()), ("SHANJIE_FORMS_EXTRA", extra.as_str())];

    let (fa, fb) = (t.file("fa"), t.file("fb"));
    let (sa, sb) = (ok_run(run(&["--rows", &rows, "--forms-rowstats", &fa], &base)), ok_run(run(&["--rows", &rows, "--forms-rowstats", &fb], &with_extra)));
    assert_eq!(std::fs::read(&fa).unwrap(), std::fs::read(&fb).unwrap());
    assert_eq!(sa, sb, "no +jv2 tag, the same summary line");
    assert!(!sa.contains("+jv2"));
    assert_eq!(oks(&fa).iter().map(|&x| x as u32).sum::<u32>(), 1, "forms.tsv alone: only one of the two spellings");

    // `--judge v1` is the default spelled out; `--rowstats` and the summary line are the same bytes
    let (ra, rb) = (t.file("ra"), t.file("rb"));
    let (s1, s2) = (ok_run(run(&["--rows", &rows, "--rowstats", &ra], &with_extra)), ok_run(run(&["--rows", &rows, "--rowstats", &rb, "--judge", "v1"], &with_extra)));
    assert_eq!((s1, std::fs::read(&ra).unwrap()), (s2, std::fs::read(&rb).unwrap()));

    // v2 with the extra table: both spellings are right; with an empty extra table: one
    let (rc, rd) = (t.file("rc"), t.file("rd"));
    let s = ok_run(run(&["--rows", &rows, "--judge", "v2", "--rowstats", &rc], &with_extra));
    assert_eq!(oks(&rc), vec![1, 1], "{s}");
    let empty = t.write("empty.tsv", "# none\n");
    ok_run(run(&["--rows", &rows, "--judge", "v2", "--rowstats", &rd], &[("SHANJIE_FORMS", forms.as_str()), ("SHANJIE_FORMS_EXTRA", empty.as_str())]));
    assert_eq!(oks(&rd).iter().map(|&x| x as u32).sum::<u32>(), 1);
    // top1 of the summary line follows the judge too
    assert!(s.contains("'top1': 2,"), "{s}");
}

/// Acceptance, the tag: changing only forms.tsv, only forms-extra.tsv or only the accept file changes exactly that segment of `+jv2:`.
#[test]
fn tag_follows_each_of_the_three_files() {
    let t = Tmp::new("tag");
    let rows = t.write("rows.txt", TWO_SPELLINGS);
    let repo_forms = std::fs::read(root().join("eval/forms.tsv")).unwrap();
    let repo_extra = std::fs::read(root().join("eval/forms-extra.tsv")).unwrap();
    let forms2 = {
        let mut b = repo_forms.clone();
        b.extend_from_slice("# one more comment line\n".as_bytes());
        t.write("forms2.tsv", &String::from_utf8(b).unwrap())
    };
    let extra2 = {
        let mut b = repo_extra.clone();
        b.extend_from_slice("# one more comment line\n".as_bytes());
        t.write("extra2.tsv", &String::from_utf8(b).unwrap())
    };
    let (acc1, acc2) = (t.write("acc1.tsv", "# one\n"), t.write("acc2.tsv", "# two\n"));
    let tag = |env: &[(&str, &str)], accept: Option<&str>| {
        let mut a = vec!["--rows", rows.as_str(), "--judge", "v2"];
        if let Some(f) = accept {
            a.extend(["--accept", f]);
        }
        jv2(&ok_run(run(&a, env)))
    };
    let base = tag(&[], None);
    assert_eq!(base[0], sha256_hex(&repo_forms)[..8]);
    assert_eq!(base[1], sha256_hex(&repo_extra)[..8]);
    assert_eq!(base[2], "none");
    // only forms.tsv
    let f = tag(&[("SHANJIE_FORMS", forms2.as_str())], None);
    assert!(f[0] != base[0] && f[1] == base[1] && f[2] == base[2], "{f:?} {base:?}");
    // only forms-extra.tsv
    let e = tag(&[("SHANJIE_FORMS_EXTRA", extra2.as_str())], None);
    assert!(e[0] == base[0] && e[1] != base[1] && e[2] == base[2], "{e:?} {base:?}");
    // only the accept file (and two different accept files differ from each other)
    let (c1, c2) = (tag(&[], Some(&acc1)), tag(&[], Some(&acc2)));
    assert!(c1[0] == base[0] && c1[1] == base[1] && c1[2] != base[2] && c1[2] == sha256_hex(b"# one\n")[..8], "{c1:?}");
    assert_ne!(c1[2], c2[2]);
}

/// Acceptance: a forms-extra table or an accept file that cannot be read or is malformed stops the run before any output file is created
/// (an existing `--rowstats` file is left as it was); `--accept` without `--judge v2` is refused; and a row whose SHA-256 differs from the file's
/// (the set changed) is an error too, row number only.
#[test]
fn bad_tables_and_accept_files_stop_the_run_before_any_output_file() {
    let t = Tmp::new("bad");
    let rows = t.write("rows.txt", TWO_SPELLINGS);
    let (rs, picks) = (t.write("rs.tsv", "keep\n"), t.file("picks.tsv"));
    let try_run = |env: &[(&str, &str)], extra_args: &[&str]| {
        let mut a = vec!["--rows", rows.as_str(), "--rowstats", rs.as_str(), "--picks", picks.as_str()];
        a.extend(extra_args);
        let o = run(&a, env);
        assert!(!o.status.success() && o.stdout.is_empty(), "{}", String::from_utf8_lossy(&o.stderr));
        assert_eq!(std::fs::read_to_string(&rs).unwrap(), "keep\n", "an existing rowstats file is not touched");
        assert!(!Path::new(&picks).exists(), "no picks file is created");
        String::from_utf8_lossy(&o.stderr).into_owned()
    };
    let missing = try_run(&[("SHANJIE_FORMS_EXTRA", "/nonexistent/forms-extra.tsv")], &["--judge", "v2"]);
    assert!(missing.contains("forms-extra table"), "{missing}");
    let bad_extra = t.write("bad-extra.tsv", "word\t甲\t乙\n");
    let e = try_run(&[("SHANJIE_FORMS_EXTRA", bad_extra.as_str())], &["--judge", "v2"]);
    assert!(e.contains("bad forms-extra table (line 1)"), "{e}");
    // forms.tsv not found: under v2 too
    let e = try_run(&[("SHANJIE_FORMS", "/nonexistent/forms.tsv")], &["--judge", "v2"]);
    assert!(e.contains("forms table"), "{e}");
    // an unreadable or malformed accept file
    let e = try_run(&[], &["--judge", "v2", "--accept", "/nonexistent/accept.tsv"]);
    assert!(e.contains("accept file"), "{e}");
    let bad_acc = t.write("bad-acc.tsv", "1\tnot-a-sha\t句子\t依據\n");
    let e = try_run(&[], &["--judge", "v2", "--accept", &bad_acc]);
    assert!(e.contains("bad accept file (line 1)"), "{e}");
    // the SHA-256 of the row differs: the accept file belongs to other rows (row 1's recorded hash is row 2's)
    let line = |row: usize, sha: &str| format!("{row}\t{sha}\t潮溼\t字條 0\n");
    let row_sha = |i: usize| sha256_hex(TWO_SPELLINGS.lines().nth(i).unwrap().as_bytes());
    let wrong = t.write("wrong.tsv", &line(1, &row_sha(1)));
    let e = try_run(&[], &["--judge", "v2", "--accept", &wrong]);
    assert!(e.contains("does not match the rows (row 1)"), "{e}");
    assert!(!e.contains("潮"), "no row content in the message: {e}");
    let beyond = t.write("beyond.tsv", &line(3, &row_sha(0)));
    assert!(try_run(&[], &["--judge", "v2", "--accept", &beyond]).contains("(row 3)"));
    // --accept needs --judge v2
    let good = t.write("good.tsv", &line(2, &row_sha(1)));
    assert!(try_run(&[], &["--accept", &good]).contains("--accept needs --judge v2"));
    // and a matching file runs
    let o = run(&["--rows", &rows, "--judge", "v2", "--accept", &good], &[]);
    assert!(o.status.success(), "{}", String::from_utf8_lossy(&o.stderr));
}

/// `--rejudge` reads the picks and decodes nothing: the same input as `--picks` writes, for rows, a limit or `--dev`; it needs a rows source and writes only a rowstats file.
#[test]
fn rejudge_takes_a_picks_file_and_refuses_what_it_cannot_do() {
    let t = Tmp::new("rejudge-opts");
    let rows = t.write("rows.txt", TWO_SPELLINGS);
    let (picks, direct, again) = (t.file("picks.tsv"), t.file("direct.rs"), t.file("again.rs"));
    let extra = t.write("extra.tsv", EXTRA_PAIR);
    let env = [("SHANJIE_FORMS", ""), ("SHANJIE_FORMS_EXTRA", extra.as_str())];
    let s1 = ok_run(run(&["--rows", &rows, "--judge", "v2", "--picks", &picks, "--rowstats", &direct], &env));
    let s2 = ok_run(run(&["--rows", &rows, "--judge", "v2", "--rejudge", &picks, "--rowstats", &again], &env));
    assert_eq!(std::fs::read(&direct).unwrap(), std::fs::read(&again).unwrap());
    // the summary shares the tag and the top1 and top1_sha256 of the decoding run
    let (tag1, tag2) = (jv2(&s1), jv2(&s2));
    assert_eq!(tag1, tag2);
    // (a rejudge has no oracle@64: nothing is decoded)
    let tail = |s: &str| {
        let (n, o, h) = (s.find("{'n'").unwrap(), s.find(", 'oracle@64'"), s.find(", 'top1_sha256'").unwrap());
        format!("{}{}", &s[n..o.unwrap_or(h)], &s[h..])
    };
    assert_eq!(tail(&s1), tail(&s2), "{s1}{s2}");
    // a bare sentence per line is a picks file too
    let bare = t.write("bare.txt", &std::fs::read_to_string(&picks).unwrap().lines().map(|l| format!("{}\n", l.rsplit('\t').next().unwrap())).collect::<String>());
    let third = t.file("third.rs");
    ok_run(run(&["--rows", &rows, "--judge", "v2", "--rejudge", &bare, "--rowstats", &third], &env));
    assert_eq!(std::fs::read(&direct).unwrap(), std::fs::read(&third).unwrap());
    // a picks file of another length, a missing one, no rows source, a decoding output: errors
    let short = t.write("short.tsv", "1\t0\t潮濕\n");
    for (args, what) in [
        (vec!["--rows", rows.as_str(), "--rejudge", short.as_str()], "line count"),
        (vec!["--rows", rows.as_str(), "--rejudge", "/nonexistent/picks.tsv"], "missing file"),
        (vec!["--rejudge", picks.as_str()], "no rows source"),
        (vec!["--rows", rows.as_str(), "--dev", "5", "--rejudge", picks.as_str()], "two rows sources"),
        (vec!["--rows", rows.as_str(), "--rejudge", picks.as_str(), "--dump", "/nonexistent/dir/d"], "--dump"),
        (vec!["--rows", rows.as_str(), "--rejudge", picks.as_str(), "--picks", "/nonexistent/dir/p"], "--picks"),
        (vec!["--set", "dev", "--rejudge", picks.as_str()], "--set"),
        (vec!["--set", "holdout", "--rejudge", picks.as_str()], "holdout"),
        (vec!["--set", "holdout", "--judge", "v2", "--accept", picks.as_str()], "holdout --accept"),
        (vec!["--rows", rows.as_str(), "--judge", "v3"], "unknown judge"),
    ] {
        let o = run(&args, &env);
        assert!(!o.status.success() && o.stdout.is_empty(), "{what}: {}", String::from_utf8_lossy(&o.stderr));
    }
    // the picks of row 1 named row 2: refused
    let swapped = t.write("swapped.tsv", "2\t0\t潮濕\n1\t0\t潮溼\n");
    assert!(!run(&["--rows", &rows, "--rejudge", &swapped], &env).status.success());
}

/// The accept files of the repository belong to the sets they name (their rows hash as recorded; empty files tie to anything).
#[test]
fn repository_accept_files_tie_to_their_sets() {
    use cli::common::{dev_rows_raw, read_rows_raw};
    for (name, rows) in [
        ("dev302", dev_rows_raw(302).unwrap()),
        ("user-typing", read_rows_raw(root().join("eval/dev/user-typing.txt").to_str().unwrap(), None).unwrap()),
        ("user-reported", read_rows_raw(root().join("eval/dev/user-reported.txt").to_str().unwrap(), None).unwrap()),
    ] {
        let text = std::fs::read_to_string(root().join(format!("eval/accept/{name}.tsv"))).unwrap();
        Accept::parse(&text).unwrap_or_else(|e| panic!("{name}: {e}")).check(&rows).unwrap_or_else(|e| panic!("{name}: {e}"));
    }
}

// ---- L3 on dev302 (the test accept file, not the repository's) ----

/// A test-only accept file for dev302: lines for rows the decoder gets wrong under v2 in `ACCEPT_ROWS_*`, with the decoder's own sentence (or, for
/// a few, that sentence with one character replaced by its L1 source, e.g. 他 -> 她), and a decoy line whose sentence is not what the decoder gives.
const ACCEPT_TEST: &str = "eval/golden/judge-v2-accept-test.tsv";

/// The rows (1-based) the test accept file turns from wrong into right under v2, per profile: the lines that match what the decoder gives, the L1 variants among them included.
const FLIPPED: [(&str, &[usize]); 2] = [("chat", &[22, 27, 36, 277, 294]), ("formal", &[22, 27, 277, 294])];

fn dev302(profile: &str, args: &[&str]) -> String {
    ok_run(run_in(profile, &[&["--dev", "302"], args].concat(), &[]))
}

fn rowstats_of(profile: &str, args: &[&str]) -> String {
    let t = Tmp::new(&format!("dev302-{profile}"));
    let f = t.file("rs");
    dev302(profile, &[args, &["--rowstats", &f]].concat());
    std::fs::read_to_string(f).unwrap()
}

fn golden(name: &str) -> String {
    std::fs::read_to_string(root().join("eval/golden").join(name)).unwrap()
}

/// Acceptance, L3 (a): a row turns from wrong to right only when the candidate, after L1 and L2, equals an acceptable sentence after L1 and L2.
/// Exactly the rows in FLIPPED change, nothing else moves, and the decoy row (a line whose sentence is not the candidate) stays wrong.
/// Acceptance, L3 (c): the v2 rowstats with and without the accept file are the Python reference's, byte for byte, and `--rejudge` equals decoding.
#[test]
fn l3_flips_exactly_the_accepted_rows_and_matches_python_and_rejudge() {
    let accept = root().join(ACCEPT_TEST);
    let accept = accept.to_str().unwrap();
    let t = Tmp::new("l3");
    for (profile, flipped) in FLIPPED {
        let without = rowstats_of(profile, &["--judge", "v2"]);
        let with = rowstats_of(profile, &["--judge", "v2", "--accept", accept]);
        assert_eq!(without, golden(&format!("s2-lm-dev302-{profile}-jv2.rowstats")), "{profile}: v2 without accept vs Python");
        assert_eq!(with, golden(&format!("s2-lm-dev302-{profile}-jv2-accept.rowstats")), "{profile}: v2 with accept vs Python");
        let (a, b): (Vec<&str>, Vec<&str>) = (without.lines().collect(), with.lines().collect());
        let diff: Vec<usize> = (0..a.len()).filter(|&i| a[i] != b[i]).map(|i| i + 1).collect();
        assert_eq!(diff, flipped, "{profile}: the rows that change");
        for &r in flipped {
            assert!(a[r - 1].split('\t').nth(1) == Some("0") && b[r - 1].split('\t').nth(1) == Some("1"), "{profile} row {r}: wrong to right");
        }
        // v1 is not touched by the file or by v2 existing
        assert_eq!(rowstats_of(profile, &[]), golden(&format!("s2-lm-dev302-{profile}.rowstats")));
        // --rejudge of the picks of the direct run equals it, with and without the accept file
        let picks = t.file(&format!("picks-{profile}"));
        dev302(profile, &["--judge", "v2", "--accept", accept, "--picks", &picks]);
        for (extra, want) in [(&["--accept", accept][..], &with), (&[][..], &without)] {
            let out = t.file(&format!("rejudged-{profile}"));
            let args = [&["--dev", "302", "--judge", "v2", "--rejudge", picks.as_str(), "--rowstats", out.as_str()][..], extra].concat();
            ok_run(run_in(profile, &args, &[]));
            assert_eq!(&std::fs::read_to_string(&out).unwrap(), want, "{profile}: --rejudge");
        }
    }
}

/// Acceptance, L3 (b): the accept file's row hash that differs from the row's is an error and no output file is created (on dev302: the test file with one hash changed).
#[test]
fn l3_wrong_row_hash_is_an_error_and_creates_no_file() {
    let t = Tmp::new("l3-hash");
    let text = std::fs::read_to_string(root().join(ACCEPT_TEST)).unwrap();
    let first = text.lines().find(|l| !l.starts_with('#')).expect("the test accept file has lines");
    let sha = first.split('\t').nth(1).unwrap();
    let altered = t.write("altered.tsv", &text.replacen(sha, &"0".repeat(64), 1));
    let (rs, picks) = (t.file("rs"), t.file("picks"));
    let o = run(&["--dev", "302", "--judge", "v2", "--accept", &altered, "--rowstats", &rs, "--picks", &picks], &[]);
    assert!(!o.status.success() && o.stdout.is_empty());
    assert!(String::from_utf8_lossy(&o.stderr).contains("does not match the rows"));
    assert!(!Path::new(&rs).exists() && !Path::new(&picks).exists());
}
