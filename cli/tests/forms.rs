//! `--forms-rowstats` (docs/contracts/forms-score.md section 1.3 and acceptance 4): the forms score is written beside the
//! current score in one run, the table is read only with the flag, and the current score does not depend on it.
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..")
}

fn run(args: &[&str], env: &[(&str, &str)]) -> Output {
    let lm = root().join("data/lm/bigram.sjlm");
    assert!(lm.exists(), "data/lm/bigram.sjlm is needed: see tests/golden.rs");
    Command::new(env!("CARGO_BIN_EXE_shanjie-eval"))
        .args(["--lm", lm.to_str().unwrap(), "--profile", "chat"])
        .args(args)
        .envs(env.iter().copied())
        .output()
        .unwrap()
}

fn oks(file: &Path) -> Vec<u8> {
    std::fs::read_to_string(file).unwrap().lines().map(|l| l.split('\t').nth(1).unwrap().parse().unwrap()).collect()
}

/// 潮濕 and 潮溼 read the same; the decoder gives one spelling, so with both as gold exactly one row is a miss for the current
/// score, and the forms score takes both (the single-character pair 濕→溼 at the syllable ㄕ).
#[test]
fn forms_score_accepts_the_other_spelling_and_the_current_score_does_not() {
    let dir = std::env::temp_dir().join(format!("shanjie-cli-forms-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let (rows, rs, frs) = (dir.join("rows.txt"), dir.join("rs.tsv"), dir.join("frs.tsv"));
    std::fs::write(&rows, "|潮濕|ㄔㄠˊ ㄕ\n|潮溼|ㄔㄠˊ ㄕ\n").unwrap();
    let o = run(&["--rows", rows.to_str().unwrap(), "--rowstats", rs.to_str().unwrap(), "--forms-rowstats", frs.to_str().unwrap()], &[]);
    assert!(o.status.success(), "{}", String::from_utf8_lossy(&o.stderr));
    assert_eq!(oks(&rs).iter().map(|&x| x as u32).sum::<u32>(), 1, "current score: one of the two spellings");
    assert_eq!(oks(&frs), vec![1, 1], "forms score: both");
    assert!(String::from_utf8_lossy(&o.stderr).contains("forms: 0 rows"));
    std::fs::remove_dir_all(dir).unwrap();
}

/// Without the flag the table is neither read nor checked; with it a missing table is an error and nothing is written to stdout.
#[test]
fn forms_table_is_read_only_with_the_flag() {
    let rows = std::env::temp_dir().join(format!("shanjie-cli-forms-rows-{}.txt", std::process::id()));
    std::fs::write(&rows, "|你好|ㄋㄧˇ ㄏㄠˇ\n").unwrap();
    let missing = [("SHANJIE_FORMS", "/nonexistent/forms.tsv")];
    let ok = run(&["--rows", rows.to_str().unwrap()], &missing);
    assert!(ok.status.success(), "{}", String::from_utf8_lossy(&ok.stderr));
    let out = std::env::temp_dir().join(format!("shanjie-cli-forms-out-{}.tsv", std::process::id()));
    let bad = run(&["--rows", rows.to_str().unwrap(), "--forms-rowstats", out.to_str().unwrap()], &missing);
    assert!(!bad.status.success() && bad.stdout.is_empty());
    assert!(String::from_utf8_lossy(&bad.stderr).contains("forms table"));
    std::fs::remove_file(rows).unwrap();
}
