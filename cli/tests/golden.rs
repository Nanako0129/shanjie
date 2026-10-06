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

const LM_MISSING: &str = "data/lm/bigram.sjlm is missing: download it with `gh release download model-v2 -R Nanako0129/shanjie -p bigram.sjlm -D data/lm` (or rebuild with tools/build_lm.py; see docs/PLAN.md S2c)";

fn lm_path() -> String {
    let p = format!("{}/../data/lm/bigram.sjlm", env!("CARGO_MANIFEST_DIR"));
    assert!(std::path::Path::new(&p).exists(), "{LM_MISSING}");
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
fn lm_file_is_the_documented_build() {
    let bytes = std::fs::read(lm_path()).unwrap();
    assert_eq!(
        core::eval::sha256_hex(&bytes),
        "8847b73a7b9cf127b4882328191c3c5250fe9a55912926d5050e351ab644d240",
        "data/lm/bigram.sjlm differs from the documented build; rebuild with tools/build_lm.py"
    );
}
