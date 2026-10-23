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

/// The numbers pinned below are those of the Python reference (reference/proto/lm_eval.py), which has no reading term
/// (docs/contracts/reading-decoder.md): these runs leave it out. The default run's picks are checked in cli/tests/reading_decode.rs.
const NORD: &str = "--no-reading-decode";

const LM_MISSING: &str = "data/lm/bigram.sjlm is missing: download it with `gh release download model-v6 -R Nanako0129/shanjie -p bigram.sjlm -D data/lm`";

const CLASSES_MISSING: &str = "data/lm/classes.sjc is missing: download it with `gh release download classes-v5 -R Nanako0129/shanjie -p classes.sjc -D data/lm` (or build it with tools/build_classes.py)";

const TRIGRAM_MISSING: &str = "data/lm/trigram.sjt3 is missing (the formal profile's trigram table): download it with `gh release download sj-cx-1-7.7m -R Nanako0129/shanjie -p trigram.sjt3 -D data/lm`";

fn lm_path() -> String {
    let p = format!("{}/../data/lm/bigram.sjlm", env!("CARGO_MANIFEST_DIR"));
    assert!(std::path::Path::new(&p).exists(), "{LM_MISSING}");
    assert!(std::path::Path::new(&format!("{}/../data/lm/classes.sjc", env!("CARGO_MANIFEST_DIR"))).exists(), "{CLASSES_MISSING}");
    assert!(std::path::Path::new(&format!("{}/../data/lm/trigram.sjt3", env!("CARGO_MANIFEST_DIR"))).exists(), "{TRIGRAM_MISSING}");
    p
}

/// S2c acceptance 2: the four argument sets of the contract's golden-generation block.
#[test]
fn lm_mode_matches_golden_byte_for_byte() {
    let (lm, root) = (lm_path(), concat!(env!("CARGO_MANIFEST_DIR"), "/.."));
    let typing = format!("{root}/eval/dev/user-typing.txt");
    let mut got = String::new();
    for p in ["chat", "formal"] {
        got += &run(&["--lm", &lm, "--profile", p, "--dev", "302", NORD]);
        got += &run(&["--lm", &lm, "--profile", p, "--rows", &typing, "--name", "typing76", NORD]);
    }
    assert_eq!(got, golden("s2-lm.txt"));
}

/// S2h acceptance 1: `--context` summary lines (Python reference output), both profiles.
#[test]
fn lm_context_mode_matches_golden_byte_for_byte() {
    let (lm, root) = (lm_path(), concat!(env!("CARGO_MANIFEST_DIR"), "/.."));
    let mut got = String::new();
    for p in ["chat", "formal"] {
        got += &run(&["--lm", &lm, "--profile", p, "--dev", "302", "--context", NORD]);
        for (f, name) in [("user-typing", "typing76"), ("user-reported", "user-reported")] {
            got += &run(&["--lm", &lm, "--profile", p, "--rows", &format!("{root}/eval/dev/{f}.txt"), "--name", name, "--context", NORD]);
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
        // `lm-chat` or `lm-chat+kn:…` (model-v6: kn.sjkn beside the model); s2-lm.txt has one dev302 line per profile.
        let want = summary.lines().find(|l| l.starts_with(&format!("## dev302  lm-{p}"))).unwrap();
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
        "fc2bddd1f0c2029deb7e7568f64868c11f535b3d5c5c7c97c3d0599c8b2cde42",
        "data/lm/bigram.sjlm differs from the model-v6 release; download it again (a model rebuilt with tools/build_lm.py has no fingerprint entries and never matches)"
    );
}

/// S2k section 4.4: the class table is the classes-v5 release asset (data/classes.sjc.sha256).
#[test]
fn classes_file_is_the_released_build() {
    let p = std::path::Path::new(&lm_path()).with_file_name("classes.sjc");
    let bytes = std::fs::read(&p).unwrap_or_else(|_| panic!("{} is missing: gh release download classes-v5 -R Nanako0129/shanjie -p classes.sjc -D data/lm", p.display()));
    assert_eq!(
        core::eval::sha256_hex(&bytes),
        "9e36fa1be4518adb34bcddc2c79761006b0b06c539aa64a4564ba00397cf0262",
        "data/lm/classes.sjc differs from the classes-v5 release; download it again or rebuild with tools/build_classes.py"
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
                let mut args = vec!["--lm", &lm, "--profile", p, "--rows", &probe, "--name", &name, "--dump", dump.to_str().unwrap(), NORD];
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

/// S2k acceptance 3: `--no-classes` is the arithmetic without the word-class term. The two summary lines are what the Python
/// `lm_eval.py --no-classes --profile P --dev 302 --context` prints (regenerated 2026-10-14 for model-v6: kn.sjkn beside the model is
/// on in both, names layer on; the formal line regenerated again for trigram-core slice C3: with trigram.sjt3 beside the model the formal
/// profile decodes with the table and KN is off there, hence `+tri:` instead of `+kn:`; before it for dec-recombine, and before that they were the ones main's s2h-lm-context.txt held before S2k).
#[test]
fn no_classes_flag_matches_its_pinned_numbers() {
    let lm = lm_path();
    let line = |p: &str| run(&["--lm", &lm, "--no-classes", "--profile", p, "--dev", "302", "--context", NORD]);
    assert_eq!(
        line("chat"),
        "## dev302  lm-chat+ctx-noclasses+kn:cbefbced:θ1:β1  {'n': 302, 'top1': 246, 'oracle@64': 300, 'top1_sha256': 'f061b257afcd622c8eb7375ac143f026a31cd6a062c685603ba63ff7366da4ca'}\n"
    );
    assert_eq!(
        line("formal"),
        "## dev302  lm-formal+ctx-noclasses+tri:fdd3b05d  {'n': 302, 'top1': 252, 'oracle@64': 300, 'top1_sha256': '22ff9dc530e4b3dd098f824546784c47eafb775b92b0f9e20702d9c0ee33ab0c'}\n"
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

/// kn-core: the CLI turns the Kneser-Ney term on when a kn.sjkn sits beside the model and prints lm_eval.py's `+kn:` tag;
/// `--no-kn` turns it off (the toy model of eval/golden/kn-tiny, the real lexicon).
#[test]
fn kn_side_file_beside_the_model_is_announced_and_can_be_turned_off() {
    let root = concat!(env!("CARGO_MANIFEST_DIR"), "/..");
    let (lm, rows) = (format!("{root}/eval/golden/kn-tiny/bigram.sjlm"), std::env::temp_dir().join(format!("shanjie-kn-rows-{}.txt", std::process::id())));
    std::fs::write(&rows, "|他占|ㄊㄚ ㄓㄢˋ\n").unwrap();
    let args = |extra: &[&'static str]| {
        let mut a = vec!["--lm", lm.as_str(), "--profile", "chat", "--rows", rows.to_str().unwrap(), "--name", "t", NORD];
        a.extend(extra);
        run(&a)
    };
    let side = std::fs::read(format!("{root}/eval/golden/kn-tiny/kn.sjkn")).unwrap();
    let tag = format!("+kn:{}:θ1:β1", &core::eval::sha256_hex(&side)[..8]);
    let (on, off) = (args(&[]), args(&["--no-kn"]));
    std::fs::remove_file(&rows).unwrap();
    assert!(on.starts_with(&format!("## t  lm-chat{tag}  {{'n': 1,")), "{on}");
    assert!(off.starts_with("## t  lm-chat  {'n': 1,"), "{off}");
}

/// trigram-core: the table beside the model is read only for `--profile formal` without `--no-tri` (chat and `--predict` never read it, so a bad one cannot
/// break them); formal with a table prints `+tri:` and no `+kn:` but still reads and checks kn.sjkn; reference/proto/lm_eval.py mirrors every case
/// (tools/test_build_trigram.py). The toy model of eval/golden/kn-tiny with the toy table of eval/golden/tri-tiny.
#[test]
fn trigram_table_is_read_for_formal_only() {
    let root = concat!(env!("CARGO_MANIFEST_DIR"), "/..");
    let dir = std::env::temp_dir().join(format!("shanjie-tri-cli-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    for (src, f) in [("kn-tiny", "bigram.sjlm"), ("kn-tiny", "classes.sjc"), ("kn-tiny", "kn.sjkn"), ("tri-tiny", "trigram.sjt3")] {
        std::fs::copy(format!("{root}/eval/golden/{src}/{f}"), dir.join(f)).unwrap();
    }
    let (lm, rows) = (dir.join("bigram.sjlm"), dir.join("rows.txt"));
    std::fs::write(&rows, "|他占|ㄊㄚ ㄓㄢˋ\n").unwrap();
    let go = |extra: &[&str]| {
        let mut a = vec!["--lm", lm.to_str().unwrap(), "--rows", rows.to_str().unwrap(), "--name", "t"];
        a.extend(extra);
        let out = Command::new(env!("CARGO_BIN_EXE_shanjie-eval")).args(&a).output().unwrap();
        (out.status.success(), String::from_utf8(out.stdout).unwrap())
    };
    let tag = |f: &str| format!("+tri:{}", &core::eval::sha256_hex(&std::fs::read(dir.join(f)).unwrap())[..8]);
    let want = tag("trigram.sjt3");
    let (ok, formal) = go(&["--profile", "formal"]);
    assert!(ok && formal.contains(&want) && !formal.contains("+kn:"), "{formal}");
    let (ok, no_tri) = go(&["--profile", "formal", "--no-tri"]);
    assert!(ok && !no_tri.contains("+tri:") && no_tri.contains("+kn:"), "{no_tri}");
    let (ok, chat) = go(&["--profile", "chat"]);
    assert!(ok && !chat.contains("+tri:") && chat.contains("+kn:"), "{chat}");
    // a bad table: formal refuses it, everything that does not read it still runs
    let table = dir.join("trigram.sjt3");
    std::fs::write(&table, b"junk").unwrap();
    assert!(!go(&["--profile", "formal"]).0, "a bad table is never ignored");
    assert!(go(&["--profile", "formal", "--no-tri"]).0);
    assert!(go(&["--profile", "chat"]).0, "chat does not read the table");
    let predict = format!("{root}/eval/golden/sp-predict.txt");
    let predict_runs = Command::new(env!("CARGO_BIN_EXE_shanjie-eval")).args(["--predict", &predict, "--lm", lm.to_str().unwrap(), "--profile", "formal"]).output().unwrap();
    assert!(predict_runs.status.success(), "--predict does not read the table: {}", String::from_utf8_lossy(&predict_runs.stderr));
    // a bad kn.sjkn beside a good table still fails the formal run
    std::fs::copy(format!("{root}/eval/golden/tri-tiny/trigram.sjt3"), &table).unwrap();
    std::fs::write(dir.join("kn.sjkn"), b"junk").unwrap();
    assert!(!go(&["--profile", "formal"]).0, "kn.sjkn is read and checked when the table is on");
    assert!(go(&["--profile", "formal", "--no-kn"]).0);
    std::fs::remove_dir_all(&dir).unwrap();
}

/// `--predict` loads the kn.sjkn beside the model like the other modes (the App's prediction row scores with it); `--no-kn` turns it
/// off. The golden is the KN run; the same queries without it must score differently.
#[test]
fn predict_no_kn_scores_differently_from_the_golden() {
    let path = format!("{}/../eval/golden/sp-predict.txt", env!("CARGO_MANIFEST_DIR"));
    let (on, off) = (run(&["--predict", &path, "--lm", &lm_path(), "--profile", "chat"]), run(&["--predict", &path, "--lm", &lm_path(), "--profile", "chat", "--no-kn"]));
    let scores = |t: &str| t.lines().filter(|l| !l.starts_with("#") && !l.starts_with("## ")).map(|l| l.split('\t').nth(1).unwrap().to_string()).collect::<Vec<_>>();
    let (a, b) = (scores(&on), scores(&off));
    assert_eq!(a.len(), b.len());
    assert!(a.iter().zip(&b).filter(|(x, y)| x != y).count() > a.len() / 2, "KN did not change most prediction scores");
}

/// data/lexicon/overlay-manual.tsv: every manual word comes first in the shipping setting, alone and in a sentence, in both profiles
/// (eval/golden/manual-words-rows.txt; MANUAL_BONUS in tools/build_overlay.py was chosen on its first 9 rows; the last 6 (喵菜子, 哈那口, and 菜菜子 which is a base word) are
/// first even without the entry, so they only pin the behaviour). A retrained model or a new
/// rerank that pushes another name below its homophones fails here instead of shipping silently; the one known miss is listed below.
/// Known exception since classes-v5 (μ 0.8 → 0.2, docs/contracts/classes-v5.md): in formal, row 8 `米雅歌唱得很好` comes out as
/// 米亞 after the rerank. The test pins exactly that miss (rowstats, i.e. after the rerank) so any other row dropping still fails.
#[test]
fn manual_words_come_first_in_the_shipping_setting() {
    let root = concat!(env!("CARGO_MANIFEST_DIR"), "/..");
    let rows = format!("{root}/eval/golden/manual-words-rows.txt");
    let tl = format!("{root}/data/lm/tinylm.sjtl");
    let stats = std::env::temp_dir().join(format!("shanjie-manual-words-{}.rowstats", std::process::id()));
    for (profile, misses) in [("chat", vec![]), ("formal", vec!["8"])] {
        let _ = std::fs::remove_file(&stats);
        run(&["--lm", &lm_path(), "--profile", profile, "--context", "--packs", "acg", "--tinylm", &tl, "--rows", &rows, "--rowstats", stats.to_str().unwrap()]);
        let got = std::fs::read_to_string(&stats).unwrap();
        assert_eq!(got.lines().count(), 15, "{profile}");
        let got: Vec<&str> = got.lines().map(|l| l.split('\t').collect::<Vec<_>>()).filter(|f| f[1] == "0").map(|f| f[0]).collect();
        assert_eq!(got, misses, "{profile}: rows whose manual word is not first");
    }
    std::fs::remove_file(&stats).unwrap();
}

/// names-lexicon 3.5: `--extra-overlay FILE` makes the file's word the first candidate (and only then), and a
/// missing file is a non-zero exit that names the path. (The contract says "appears among the candidates"; the string
/// 酷澎 is also composable from two single-character words, so it appears in the list either way and only its rank tells.)
/// 酷澎 is in the shipped names layer since model-v6, so every run here is `--no-names` (marked `-nonames`); the shipped layer
/// itself is `names_layer_is_on_by_default_and_no_names_turns_it_off`.
#[test]
fn extra_overlay_adds_words_and_fails_loudly_when_missing() {
    let dir = std::env::temp_dir().join(format!("shanjie-extra-overlay-cli-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let (rows, extra, dump) = (dir.join("rows.txt"), dir.join("names-add.tsv"), dir.join("dump.tsv"));
    std::fs::write(&rows, "|酷澎|ㄎㄨˋ ㄆㄥˊ\n").unwrap();
    std::fs::write(&extra, "ㄎㄨˋ-ㄆㄥˊ\t酷澎\t-3.0\tnames\n").unwrap();
    let lm = lm_path();
    let first = |extra_args: &[&str]| {
        let out = Command::new(env!("CARGO_BIN_EXE_shanjie-eval"))
            .args(["--lm", &lm, "--profile", "chat", "--no-names", "--rows", rows.to_str().unwrap(), "--dump", dump.to_str().unwrap(), NORD])
            .args(extra_args)
            .output()
            .unwrap();
        assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
        // The summary line is marked `+xo:<sha8 of the file>` only when --extra-overlay is given.
        let head = String::from_utf8(out.stdout).unwrap();
        let head = head.lines().next().unwrap().split("  ").nth(1).unwrap().to_string();
        let marked = extra_args.contains(&"--extra-overlay");
        assert_eq!(head.contains("+xo:"), marked, "{head}");
        if marked {
            let h = head.split("+xo:").nth(1).unwrap();
            assert!(h.len() == 8 && h.bytes().all(|b| b.is_ascii_hexdigit()), "{head}");
        }
        let dump = std::fs::read_to_string(&dump).unwrap();
        let top = dump.lines().find(|l| l.split('\t').nth(1) == Some("1")).unwrap();
        top.split('\t').nth(2).unwrap().to_string()
    };
    assert_eq!(first(&["--extra-overlay", extra.to_str().unwrap()]), "酷澎");
    assert_ne!(first(&[]), "酷澎");
    let missing = dir.join("no-such.tsv");
    let out = Command::new(env!("CARGO_BIN_EXE_shanjie-eval"))
        .args(["--lm", &lm, "--profile", "chat", "--rows", rows.to_str().unwrap(), "--extra-overlay", missing.to_str().unwrap()])
        .output()
        .unwrap();
    let err = String::from_utf8(out.stderr).unwrap();
    assert!(!out.status.success() && err.contains(missing.to_str().unwrap()), "{err}");
    std::fs::remove_dir_all(&dir).unwrap();
}

/// model-v6 section 2.5: the names layer (data/lexicon/names-add.tsv) is loaded by default, so 酷澎 (a names-only word, in no other
/// lexicon file) is the first candidate in both profiles with no option; `--no-names` leaves it out and marks the summary line.
#[test]
fn names_layer_is_on_by_default_and_no_names_turns_it_off() {
    let dir = std::env::temp_dir().join(format!("shanjie-names-cli-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let (rows, dump) = (dir.join("rows.txt"), dir.join("dump.tsv"));
    std::fs::write(&rows, "|酷澎|ㄎㄨˋ ㄆㄥˊ\n").unwrap();
    let names = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/../data/lexicon/names-add.tsv")).unwrap();
    assert!(names.lines().any(|l| l.split('\t').nth(1) == Some("酷澎")), "premise: 酷澎 is a names-layer word");
    let lm = lm_path();
    for profile in ["chat", "formal"] {
        let first = |extra: &[&str]| {
            let head = run(&[&["--lm", lm.as_str(), "--profile", profile, "--rows", rows.to_str().unwrap(), "--dump", dump.to_str().unwrap()], extra].concat());
            let top = std::fs::read_to_string(&dump).unwrap().lines().find(|l| l.split('\t').nth(1) == Some("1")).unwrap().split('\t').nth(2).unwrap().to_string();
            (head.split("  ").nth(1).unwrap().to_string(), top)
        };
        let ((on_tag, on), (off_tag, off)) = (first(&[]), first(&["--no-names"]));
        assert_eq!(on, "酷澎", "{profile}");
        assert_ne!(off, "酷澎", "{profile}");
        // `-nonames` goes after `-noclasses`, before the `+kn:` tag: lm-chat+kn:… becomes lm-chat-nonames+kn:…
        assert_eq!(off_tag, on_tag.replacen(&format!("lm-{profile}"), &format!("lm-{profile}-nonames"), 1), "{profile}");
        assert!(!on_tag.contains("nonames"), "{on_tag}");
    }
    std::fs::remove_dir_all(&dir).unwrap();
}
