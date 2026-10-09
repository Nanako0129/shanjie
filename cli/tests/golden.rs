use std::process::Command;

fn run(args: &[&str]) -> String {
    let out = Command::new(env!("CARGO_BIN_EXE_shanjie-eval")).args(args).output().unwrap();
    assert!(out.status.success());
    String::from_utf8(out.stdout).unwrap()
}

#[test]
fn matches_golden_byte_for_byte() {
    let golden = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/../eval/golden/unigram.txt")).unwrap();
    assert_eq!(run(&["--set", "trap", "daily", "moedict", "--learn-sim", "--no-overlay"]), golden);
}

#[test]
fn check_readings_flag_adds_nothing_without_readings_columns() {
    // No shipped set has a third column, so the flag must add no lines yet.
    let a = run(&["--set", "trap", "daily", "moedict"]);
    assert_eq!(run(&["--set", "trap", "daily", "moedict", "--check-readings"]), a);
}

fn golden(name: &str) -> String {
    std::fs::read_to_string(format!("{}/../eval/golden/{name}", env!("CARGO_MANIFEST_DIR"))).unwrap()
}

#[test]
fn matches_s1_references_byte_for_byte() {
    assert_eq!(run(&["--set", "dev", "--limit", "302"]), golden("s1-dev302.txt"));
    assert_eq!(run(&["--set", "dev", "--limit", "302", "--no-overlay"]), golden("s1-dev302-nooverlay.txt"));
    assert_eq!(run(&["--set", "trap", "daily", "moedict", "--learn-sim"]), golden("s1-overlay-sets.txt"));
}

/// S2r: the probe rows only decode right with sandhi-add.tsv loaded; reference/proto/check_unigram_overlay.py
/// checks the same golden from Python.
#[test]
fn matches_s2r_probe_unigram_byte_for_byte() {
    assert_eq!(run(&["--set", "probe"]), golden("s2r-probe-unigram.txt"));
}

#[test]
fn lenient_dump_uses_the_variant_table() {
    // S2v acceptance 6. SHANJIE_VARIANTS is neither set nor cleared here, so pointing it at an
    // empty table must make this fail (the verifier's mutation check).
    let dir = std::env::temp_dir().join(format!("shanjie-lenient-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let probe = dir.join("probe.txt");
    std::fs::write(&probe, "唸書\n念書\n散佈\n散布\n").unwrap();
    let out = run(&["--lenient-dump", probe.to_str().unwrap()]);
    std::fs::remove_dir_all(&dir).unwrap();
    let lines: Vec<&str> = out.lines().collect();
    assert_eq!(lines.len(), 4);
    assert_eq!(lines[0], lines[1], "念書 and 唸書 must compare equal");
    assert_ne!(lines[2], lines[3], "散佈 has no dictionary entry, so it is not a listed variant");
}

const LM_MISSING: &str = "data/lm/bigram.sjlm is missing: download it with `gh release download model-v4 -R Nanako0129/shanjie -p bigram.sjlm -D data/lm`";

const CLASSES_MISSING: &str = "data/lm/classes.sjc is missing: download it with `gh release download classes-v2 -R Nanako0129/shanjie -p classes.sjc -D data/lm` (or build it with tools/build_classes.py)";

fn lm_path() -> String {
    let p = format!("{}/../data/lm/bigram.sjlm", env!("CARGO_MANIFEST_DIR"));
    assert!(std::path::Path::new(&p).exists(), "{LM_MISSING}");
    assert!(std::path::Path::new(&format!("{}/../data/lm/classes.sjc", env!("CARGO_MANIFEST_DIR"))).exists(), "{CLASSES_MISSING}");
    p
}

/// S2c acceptance 2: the four argument sets of the contract's golden-generation block.
#[test]
fn lm_mode_matches_golden_byte_for_byte() {
    let (lm, root) = (lm_path(), concat!(env!("CARGO_MANIFEST_DIR"), "/.."));
    let typing = format!("{root}/eval/dev/user-typing.txt");
    let mut got = String::new();
    for p in ["chat", "formal"] {
        got += &run(&["--lm", &lm, "--profile", p, "--dev", "302"]);
        got += &run(&["--lm", &lm, "--profile", p, "--rows", &typing, "--name", "typing76"]);
    }
    assert_eq!(got, golden("s2-lm.txt"));
}

/// S2h acceptance 1: `--context` summary lines (Python reference output), both profiles.
#[test]
fn lm_context_mode_matches_golden_byte_for_byte() {
    let (lm, root) = (lm_path(), concat!(env!("CARGO_MANIFEST_DIR"), "/.."));
    let mut got = String::new();
    for p in ["chat", "formal"] {
        got += &run(&["--lm", &lm, "--profile", p, "--dev", "302", "--context"]);
        for (f, name) in [("user-typing", "typing76"), ("user-reported", "user-reported")] {
            got += &run(&["--lm", &lm, "--profile", p, "--rows", &format!("{root}/eval/dev/{f}.txt"), "--name", name, "--context"]);
        }
    }
    assert_eq!(got, golden("s2h-lm-context.txt"));
}

/// S2c acceptance 2, second half: the top1 file's columns hash to the summary lines' top1_sha256.
#[test]
fn lm_top1_tsv_hashes_match_summary_lines() {
    let tsv = golden("s2-lm-dev302-top1.tsv");
    let rows: Vec<Vec<&str>> = tsv.lines().filter(|l| !l.starts_with('#')).map(|l| l.split('\t').collect()).collect();
    assert_eq!(rows.len(), 302);
    let summary = golden("s2-lm.txt");
    for (col, p) in ["chat", "formal"].iter().enumerate() {
        let joined = rows.iter().map(|r| r[col]).collect::<Vec<_>>().join("\n");
        let want = summary.lines().find(|l| l.starts_with(&format!("## dev302  lm-{p}  "))).unwrap();
        assert!(want.contains(&format!("'top1_sha256': '{}'", core::eval::sha256_hex(joined.as_bytes()))));
    }
}

#[test]
fn sha256_known_vectors() {
    assert_eq!(core::eval::sha256_hex(b""), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    assert_eq!(core::eval::sha256_hex(b"abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
}

#[test]
fn lm_file_is_the_released_model() {
    let bytes = std::fs::read(lm_path()).unwrap();
    assert_eq!(
        core::eval::sha256_hex(&bytes),
        "06768f2949cf8b135d1f591056ffb16f3ae3f6d70aef5911ffd55de134250322",
        "data/lm/bigram.sjlm differs from the model-v4 release; download it again (a model rebuilt with tools/build_lm.py has no fingerprint entries and never matches)"
    );
}

/// S2k section 4.4: the class table is the classes-v2 release asset (data/classes.sjc.sha256).
#[test]
fn classes_file_is_the_released_build() {
    let p = std::path::Path::new(&lm_path()).with_file_name("classes.sjc");
    let bytes = std::fs::read(&p).unwrap_or_else(|_| panic!("{} is missing: gh release download classes-v2 -R Nanako0129/shanjie -p classes.sjc -D data/lm", p.display()));
    assert_eq!(
        core::eval::sha256_hex(&bytes),
        "9e343d3e3ce83f1008e371f62de5d8e62721df97e7b02562503ef2cc57226f3f",
        "data/lm/classes.sjc differs from the classes-v2 release; download it again or rebuild with tools/build_classes.py"
    );
}

/// S-bench 7.1: the unigram row mode (`--no-overlay --rows --dump`, no `--lm`) equals the golden written by
/// `reference/proto/unigram_eval.py` (summary line, then the full top-64 dump), byte for byte.
/// Regenerate: see the docstring of reference/proto/unigram_eval.py.
#[test]
fn unigram_rows_mode_matches_python_golden_byte_for_byte() {
    let root = concat!(env!("CARGO_MANIFEST_DIR"), "/..");
    let dump = std::env::temp_dir().join(format!("shanjie-unigram-dump-{}.tsv", std::process::id()));
    let out = Command::new(env!("CARGO_BIN_EXE_shanjie-eval"))
        .env("SHANJIE_VARIANTS", format!("{root}/eval/bench/suite-v1/variants.tsv"))
        .args(["--no-overlay", "--rows", &format!("{root}/eval/bench/suite-v1/typing76.txt"), "--dump", dump.to_str().unwrap()])
        .output()
        .unwrap();
    assert!(out.status.success());
    let got = String::from_utf8(out.stdout).unwrap() + &std::fs::read_to_string(&dump).unwrap();
    std::fs::remove_file(&dump).unwrap();
    assert_eq!(got, golden("sbench-unigram-typing76.txt"));
}

/// V3 core: `--predict` over the golden's own query lines. Candidate order, strings and successor flags equal the
/// Python reference (experiments/sp/golden_predict.py); scores are compared as f64 bit patterns.
#[test]
fn predict_matches_python_golden() {
    let path = format!("{}/../eval/golden/sp-predict.txt", env!("CARGO_MANIFEST_DIR"));
    let want = golden("sp-predict.txt");
    let got = run(&["--predict", &path, "--lm", &lm_path(), "--profile", "chat"]);
    let rows = |t: &str| t.lines().filter(|l| !l.starts_with("# ")).map(String::from).collect::<Vec<_>>();
    let (want, got) = (rows(&want), rows(&got));
    assert_eq!(got.len(), want.len());
    assert!(want.iter().filter(|l| l.starts_with("## ")).count() > 2000);
    for (g, w) in got.iter().zip(&want) {
        let (g, w): (Vec<&str>, Vec<&str>) = (g.split('\t').collect(), w.split('\t').collect());
        assert_eq!(g.len(), w.len());
        if w[0] == "## P" || w[0] == "## PA" {
            assert_eq!(g, w);
        } else {
            assert_eq!((g[0], g[2]), (w[0], w[2]), "candidate order or successor flag differs");
            assert_eq!(g[1].parse::<f64>().unwrap().to_bits(), w[1].parse::<f64>().unwrap().to_bits(), "score differs for {}", w[0]);
        }
    }
}

/// SW first slice (docs/contracts/sw-sensitive-demote.md section 4.2): the frozen probe, both profiles, with
/// and without `--context`, demotion on and off: the summary line, then each row's first candidate with
/// its score. Python output (lm_eval.py); the Rust scores equal it bit for bit. Regenerate by running the
/// same loop with `python3 reference/proto/lm_eval.py` instead of this binary:
/// for each ctx in ("", --context), profile in (chat, formal), d in (on, off) run
/// `--profile P --rows experiments/sw/sensitive-reading.txt --name sw-probe-D [ctx] [--no-demote when off]
/// --dump F` and keep the summary line and the dump lines whose second column is 1.
#[test]
fn sw_probe_matches_python_golden_byte_for_byte() {
    let (lm, root) = (lm_path(), concat!(env!("CARGO_MANIFEST_DIR"), "/.."));
    let probe = format!("{root}/experiments/sw/sensitive-reading.txt");
    let dump = std::env::temp_dir().join(format!("shanjie-sw-probe-{}.tsv", std::process::id()));
    let mut got = String::new();
    for ctx in [None, Some("--context")] {
        for p in ["chat", "formal"] {
            for d in ["on", "off"] {
                let name = format!("sw-probe-{d}");
                let mut args = vec!["--lm", &lm, "--profile", p, "--rows", &probe, "--name", &name, "--dump", dump.to_str().unwrap()];
                args.extend(ctx);
                if d == "off" {
                    args.push("--no-demote");
                }
                got += &run(&args);
                for l in std::fs::read_to_string(&dump).unwrap().lines().filter(|l| l.split('\t').nth(1) == Some("1")) {
                    got += l;
                    got += "\n";
                }
            }
        }
    }
    std::fs::remove_file(&dump).unwrap();
    assert_eq!(got, golden("sw-probe.txt"));
}

/// S2k acceptance 3: `--no-classes` is the arithmetic before the word-class term. The two summary lines are the
/// ones main's s2h-lm-context.txt held before S2k (dev302, `--context`), and the Python `lm_eval.py --no-classes` prints them too.
#[test]
fn no_classes_flag_restores_the_pre_s2k_numbers() {
    let lm = lm_path();
    let line = |p: &str| run(&["--lm", &lm, "--no-classes", "--profile", p, "--dev", "302", "--context"]);
    assert_eq!(
        line("chat"),
        "## dev302  lm-chat+ctx-noclasses  {'n': 302, 'top1': 235, 'oracle@64': 300, 'top1_sha256': 'fb9e1f2a8bdd27922f74a80ca479d55cb55e068194a955204f4b7a01c070c5b4'}\n"
    );
    assert_eq!(
        line("formal"),
        "## dev302  lm-formal+ctx-noclasses  {'n': 302, 'top1': 240, 'oracle@64': 300, 'top1_sha256': '4f24d0f8a5bf3b188525b54444f92939ae7c5e3bac04bee799c27a6721a427bf'}\n"
    );
}

/// eval-stats §4.2: `--rowstats` is byte-identical to the file lm_eval.py wrote (eval/golden/*.rowstats), holds only
/// digits, tabs and newlines, and its ok column sums to the summary line's top1.
#[test]
fn lm_rowstats_match_python_byte_for_byte() {
    let lm = lm_path();
    let dir = std::env::temp_dir().join(format!("shanjie-rowstats-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    for p in ["chat", "formal"] {
        let out = dir.join(format!("{p}.rowstats"));
        let summary = run(&["--lm", &lm, "--profile", p, "--dev", "302", "--rowstats", out.to_str().unwrap()]);
        let got = std::fs::read_to_string(&out).unwrap();
        assert_eq!(got, golden(&format!("s2-lm-dev302-{p}.rowstats")), "{p}");
        assert!(got.chars().all(|c| c.is_ascii_digit() || c == '\t' || c == '\n'));
        let ok: usize = got.lines().map(|l| l.split('\t').nth(1).unwrap().parse::<usize>().unwrap()).sum();
        assert!(summary.contains(&format!("'top1': {ok},")), "{p}: {summary}");
    }
    std::fs::remove_dir_all(&dir).unwrap();
}

/// acg-pack A.2: a run that asks for a pack must fail loudly when the file is missing, and `--packs-dir` alone is an error.
#[test]
fn packs_options_fail_loudly() {
    let rows = std::env::temp_dir().join(format!("shanjie-packs-rows-{}.txt", std::process::id()));
    std::fs::write(&rows, "|風之谷|ㄈㄥ ㄓ ㄍㄨˇ\n").unwrap();
    let fail = |extra: &[&str]| {
        let out = Command::new(env!("CARGO_BIN_EXE_shanjie-eval"))
            .args(["--lm", "/nonexistent/bigram.sjlm", "--profile", "chat", "--rows", rows.to_str().unwrap()]) // option errors come before the model is read
            .args(extra)
            .output()
            .unwrap();
        assert!(!out.status.success());
        String::from_utf8(out.stderr).unwrap()
    };
    let missing = std::env::temp_dir().join("shanjie-no-such-packs");
    let err = fail(&["--packs", "acg", "--packs-dir", missing.to_str().unwrap()]);
    assert!(err.contains(missing.join("acg-add.tsv").to_str().unwrap()), "{err}");
    assert!(fail(&["--packs-dir", missing.to_str().unwrap()]).contains("--packs-dir needs --packs"));
    std::fs::remove_file(&rows).unwrap();
}

/// acg-pack A2.5: a pack file that exists but cannot be read is named with its path and ErrorKind, not "cannot load lexicon".
#[cfg(unix)]
#[test]
fn unreadable_pack_file_names_the_path_and_kind() {
    use std::os::unix::fs::PermissionsExt;
    let dir = std::env::temp_dir().join(format!("shanjie-packs-unreadable-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let pack = dir.join("acg-add.tsv");
    std::fs::write(&pack, "").unwrap();
    std::fs::set_permissions(&pack, std::fs::Permissions::from_mode(0o000)).unwrap();
    let rows = dir.join("rows.txt");
    std::fs::write(&rows, "|風之谷|ㄈㄥ ㄓ ㄍㄨˇ\n").unwrap();
    let out = Command::new(env!("CARGO_BIN_EXE_shanjie-eval"))
        .args(["--lm", &lm_path(), "--profile", "chat", "--rows", rows.to_str().unwrap(), "--packs", "acg", "--packs-dir", dir.to_str().unwrap()])
        .output()
        .unwrap();
    std::fs::set_permissions(&pack, std::fs::Permissions::from_mode(0o644)).unwrap();
    std::fs::remove_dir_all(&dir).unwrap();
    let err = String::from_utf8(out.stderr).unwrap();
    assert!(!out.status.success() && err.contains("acg-add.tsv") && err.contains("PermissionDenied"), "{err}");
}

/// acg-pack A2.5: a pack that reads but does not parse is reported as a parse failure naming the data directory.
#[test]
fn unparsable_pack_is_a_parse_error() {
    let dir = std::env::temp_dir().join(format!("shanjie-packs-badrows-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join("acg-add.tsv"), "not a row\n").unwrap();
    let rows = dir.join("rows.txt");
    std::fs::write(&rows, "|風之谷|ㄈㄥ ㄓ ㄍㄨˇ\n").unwrap();
    let out = Command::new(env!("CARGO_BIN_EXE_shanjie-eval"))
        .args(["--lm", &lm_path(), "--profile", "chat", "--rows", rows.to_str().unwrap(), "--packs", "acg", "--packs-dir", dir.to_str().unwrap()])
        .output()
        .unwrap();
    std::fs::remove_dir_all(&dir).unwrap();
    let err = String::from_utf8(out.stderr).unwrap();
    assert!(!out.status.success() && err.contains("cannot parse the lexicon in") && err.contains("data/lexicon"), "{err}");
}
