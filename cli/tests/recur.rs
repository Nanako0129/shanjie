//! The context recurrence term in the evaluation CLI (docs/spec/decoder.md, the context recurrence section): with `--context` the CLI builds each row's set
//! from its context column (`RecurGrams::from_text`); `--no-recur` leaves the term out. A context that is empty or one character long has no pair, so every
//! output is what `--no-recur` prints (the tag aside); a context that holds a homophone changes the choice.
use std::path::{Path, PathBuf};
use std::process::Command;

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..")
}

struct Dir(PathBuf);
impl Drop for Dir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}
fn tmp(name: &str) -> Dir {
    let d = std::env::temp_dir().join(format!("shanjie-cli-recur-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    Dir(d)
}

/// The summary line; the CLI must succeed.
fn run(args: &[&str]) -> String {
    assert!(root().join("data/lm/tinylm.sjtl").exists(), "data/lm/ is needed: see tests/golden.rs and data/*.sha256");
    let o = Command::new(env!("CARGO_BIN_EXE_shanjie-eval")).args(args).current_dir(root()).output().unwrap();
    assert!(o.status.success(), "{}", String::from_utf8_lossy(&o.stderr));
    String::from_utf8(o.stdout).unwrap()
}

/// The first 40 rows of eval/dev/user-reported.txt (public, CC0) under the contexts "", 1 Han character, 1 ASCII letter and 1 emoji: the dump (all candidates
/// and their path scores), the rowstats and the summary line with the term are those of `--no-recur`, except `+recur` in the tag; chat and formal, the n-gram
/// alone and with the small model.
#[test]
fn an_empty_or_one_character_context_prints_what_no_recur_prints() {
    let d = tmp("short");
    let src = std::fs::read_to_string(root().join("eval/dev/user-reported.txt")).unwrap();
    let mut rows = String::new();
    for (i, line) in src.lines().take(40).enumerate() {
        let rest = line.split_once('|').unwrap().1;
        rows += &format!("{}|{rest}\n", ["", "反", "a", "😀"][i % 4]);
    }
    let file = d.0.join("rows.txt");
    std::fs::write(&file, rows).unwrap();
    let file = file.to_str().unwrap();
    for profile in ["chat", "formal"] {
        for tinylm in [false, true] {
            let out = |off: bool| {
                let tag = format!("{profile}-{tinylm}-{off}");
                let (dump, rs) = (d.0.join(format!("{tag}.dump")), d.0.join(format!("{tag}.rs")));
                let mut a = vec!["--lm", "data/lm/bigram.sjlm", "--profile", profile, "--context", "--rows", file, "--dump", dump.to_str().unwrap(), "--rowstats", rs.to_str().unwrap()];
                if tinylm {
                    a.extend(["--packs", "acg,it,meme,beauty", "--tinylm", "data/lm/tinylm.sjtl"]);
                }
                if off {
                    a.push("--no-recur");
                }
                let line = run(&a);
                (line, std::fs::read(dump).unwrap(), std::fs::read(rs).unwrap())
            };
            let (on, off) = (out(false), out(true));
            assert!(on.0.contains("+ctx+recur") && !off.0.contains("+recur"), "{}{}", on.0, off.0);
            assert_eq!(on.0.replacen("+recur", "", 1), off.0, "{profile} {tinylm}");
            assert!(on.1 == off.1 && !on.1.is_empty(), "{profile} {tinylm}: the dump");
            assert_eq!(on.2, off.2, "{profile} {tinylm}: the rowstats");
        }
    }
}

/// A context that holds 反映 shows 反映 for ㄈㄢˇ ㄧㄥˋ, and `--no-recur` shows 反應; without `--context` there is no set and no `+recur`.
#[test]
fn a_context_with_the_homophone_turns_the_choice() {
    let d = tmp("turn");
    let file = d.0.join("rows.txt");
    std::fs::write(&file, "昨天看到的反映讓人在意，|反映|ㄈㄢˇ ㄧㄥˋ\n").unwrap();
    let file = file.to_str().unwrap();
    let base = ["--lm", "data/lm/bigram.sjlm", "--rows", file];
    let top1 = |line: &str| line.contains("'top1': 1,");
    for profile in ["chat", "formal"] {
        let with = |extra: &[&str]| run(&[&base[..], &["--profile", profile], extra].concat());
        assert!(top1(&with(&["--context"])), "{profile}");
        assert!(!top1(&with(&["--context", "--no-recur"])), "{profile}");
        let plain = with(&[]);
        assert!(!top1(&plain) && !plain.contains("+recur"), "{profile}: {plain}");
    }
}
