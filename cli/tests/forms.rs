//! `--forms-rowstats` (docs/contracts/forms-score.md section 1.3 and acceptance 4): the forms score is written beside the
//! current score in one run, the table is read only with the flag, and the current score does not depend on it.
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..")
}

/// A scratch directory removed on drop, so a failed assert leaves nothing behind either.
struct Tmp(PathBuf);

impl Tmp {
    fn new(name: &str) -> Tmp {
        let d = std::env::temp_dir().join(format!("shanjie-cli-forms-{name}-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        Tmp(d)
    }
    fn file(&self, f: &str) -> String {
        self.0.join(f).to_string_lossy().into_owned()
    }
}

impl Drop for Tmp {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
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

fn oks(file: &str) -> Vec<u8> {
    std::fs::read_to_string(file).unwrap().lines().map(|l| l.split('\t').nth(1).unwrap().parse().unwrap()).collect()
}

fn stderr(o: &Output) -> String {
    String::from_utf8_lossy(&o.stderr).into_owned()
}

/// 潮濕 and 潮溼 read the same; the decoder gives one spelling, so with both as gold exactly one row is a miss for the current
/// score, and the forms score takes both (the single-character pair 濕→溼 at the syllable ㄕ).
#[test]
fn forms_score_accepts_the_other_spelling_and_the_current_score_does_not() {
    let t = Tmp::new("score");
    let (rows, rs, frs) = (t.file("rows.txt"), t.file("rs.tsv"), t.file("frs.tsv"));
    std::fs::write(&rows, "|潮濕|ㄔㄠˊ ㄕ\n|潮溼|ㄔㄠˊ ㄕ\n").unwrap();
    let o = run(&["--rows", &rows, "--rowstats", &rs, "--forms-rowstats", &frs], &[]);
    assert!(o.status.success(), "{}", stderr(&o));
    assert_eq!(oks(&rs).iter().map(|&x| x as u32).sum::<u32>(), 1, "current score: one of the two spellings");
    assert_eq!(oks(&frs), vec![1, 1], "forms score: both");
    assert!(stderr(&o).contains("forms: 0 rows"));
}

/// The stderr count is the rows whose single-character step was skipped: here the gold has 2 characters for 1 syllable.
#[test]
fn rows_without_the_single_character_step_are_counted() {
    let t = Tmp::new("count");
    let (rows, frs) = (t.file("rows.txt"), t.file("frs.tsv"));
    std::fs::write(&rows, "|A好|ㄏㄠˇ\n|好|ㄏㄠˇ\n").unwrap();
    let o = run(&["--rows", &rows, "--forms-rowstats", &frs], &[]);
    assert!(o.status.success(), "{}", stderr(&o));
    assert!(stderr(&o).contains("forms: 1 rows"), "{}", stderr(&o));
}

/// Without the flag the table is neither read nor checked; with it a missing table is an error, nothing is written to stdout,
/// and an existing --rowstats file is left as it was (the table is loaded before any output file is created).
#[test]
fn forms_table_is_read_only_with_the_flag_and_before_any_output_file() {
    let t = Tmp::new("flag");
    let (rows, rs, frs) = (t.file("rows.txt"), t.file("rs.tsv"), t.file("frs.tsv"));
    std::fs::write(&rows, "|你好|ㄋㄧˇ ㄏㄠˇ\n").unwrap();
    std::fs::write(&rs, "keep\n").unwrap();
    let missing = [("SHANJIE_FORMS", "/nonexistent/forms.tsv")];
    let ok = run(&["--rows", &rows], &missing);
    assert!(ok.status.success(), "{}", stderr(&ok));
    let bad = run(&["--rows", &rows, "--rowstats", &rs, "--forms-rowstats", &frs], &missing);
    assert!(!bad.status.success() && bad.stdout.is_empty());
    assert!(stderr(&bad).contains("forms table"));
    assert_eq!(std::fs::read_to_string(&rs).unwrap(), "keep\n");
    assert!(!Path::new(&frs).exists());
}
