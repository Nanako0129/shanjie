//! The context recurrence term in the evaluation CLI (docs/spec/decoder.md §4.6): with `--context` the CLI builds each row's set
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
/// and their path scores), the rowstats and the summary line with the term are those of `--no-recur`, except `+recur` in the tag (`+recur+pr` with the small
/// model: the pool weight of docs/spec/semantic-rerank.md §3.1.1 is on); chat and formal, the n-gram alone and with the small model.
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
            let mark = if tinylm { "+recur+pr" } else { "+recur" };
            assert!(on.0.contains(&format!("+ctx{mark}+kn")) && !off.0.contains("+recur") && !off.0.contains("+pr"), "{}{}", on.0, off.0);
            assert_eq!(on.0.replacen(mark, "", 1), off.0, "{profile} {tinylm}");
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

/// The context recurrence weight on the pool (docs/spec/semantic-rerank.md §3.1.1) in the CLI, on a hand-written row whose left text holds 44 characters,
/// 語義 and 錯字: `+recur+pr` with `--context` and `--tinylm`, none with `--no-pool-recur`, `--no-recur` or no `--context`; without `--tinylm` there is no
/// pool and the option changes nothing (byte for byte). The reordered row's `--tinylm-scores` lists the pool as the rerank saw it (`idx`, the n-best index
/// of each entry; `ng` the decoder's score plus the weight, best first); `--pool-mask-diff` writes numbers only and the summary counts them.
#[test]
fn the_pool_weight_marks_and_records() {
    let d = tmp("pool");
    let file = d.0.join("rows.txt");
    std::fs::write(&file, "我們今天下午在會議室討論了很多事情最後大家都同意先這樣做。錯字，這是不是語義模型太爛啊（|語意也錯字|ㄩˇ ㄧˋ ㄧㄝˇ ㄘㄨㄛˋ ㄗˋ\n").unwrap();
    let file = file.to_str().unwrap();
    let path = |n: &str| d.0.join(n).to_str().unwrap().to_string();
    let base = ["--lm", "data/lm/bigram.sjlm", "--profile", "formal", "--rows", file];
    let with = |extra: &[&str]| run(&[&base[..], extra].concat());
    let tl = ["--context", "--tinylm", "data/lm/tinylm.sjtl"];
    let on = with(&tl);
    let off = with(&[&tl[..], &["--no-pool-recur"]].concat());
    assert!(on.contains("+ctx+recur+pr+kn") && off.contains("+ctx+recur+kn"), "{on}{off}");
    assert_ne!(on.replacen("+pr", "", 1), off, "premise: the weight changes the choice");
    assert!(with(&[&tl[..], &["--no-recur"]].concat()).contains("lm-formal+ctx+kn"));
    let plain = with(&["--tinylm", "data/lm/tinylm.sjtl"]);
    assert!(!plain.contains("+recur") && !plain.contains("+pr"), "{plain}");
    // Without a small model: no pool, so no weight and no mark; the option changes no byte of the summary, the dump or the rowstats.
    let ngram = |extra: &[&str], tag: &str| {
        let (dump, rs) = (path(&format!("{tag}.dump")), path(&format!("{tag}.rs")));
        let line = with(&[&["--context", "--dump", &dump, "--rowstats", &rs][..], extra].concat());
        (line, std::fs::read(dump).unwrap(), std::fs::read(rs).unwrap())
    };
    let (a, b) = (ngram(&[], "a"), ngram(&["--no-pool-recur"], "b"));
    assert!(a == b && a.0.contains("+ctx+recur+kn"), "{}{}", a.0, b.0);
    // The scoring record of the reordered row, against the dump's scores.
    let (scores, dump, md) = (path("s.jsonl"), path("s.dump"), path("md.txt"));
    let line = with(&[&tl[..], &["--tinylm-scores", &scores, "--dump", &dump, "--pool-mask-diff", &md]].concat());
    assert!(line.ends_with(", 'pool_mask_diff': 0}\n"), "{line}");
    assert!(std::fs::read(&md).unwrap().is_empty());
    let rec = std::fs::read_to_string(&scores).unwrap();
    let list = |key: &str| -> Vec<f64> {
        let at = rec.find(&format!("\"{key}\": [")).unwrap_or_else(|| panic!("{key} in {rec}")) + key.len() + 5;
        rec[at..at + rec[at..].find(']').unwrap()].split(", ").map(|x| x.parse().unwrap()).collect()
    };
    let (ng, idx) = (list("ng"), list("idx"));
    let nbest: Vec<f64> = std::fs::read_to_string(&dump).unwrap().lines().map(|l| l.rsplit('\t').next().unwrap().parse().unwrap()).collect();
    assert!(idx[0] != 0.0 && ng.windows(2).all(|w| w[0] >= w[1]), "{rec}");
    let gains: Vec<f64> = ng.iter().zip(&idx).map(|(s, &i)| s - nbest[i as usize]).collect();
    assert!(gains.iter().all(|g| (g / 2.0 - (g / 2.0).round()).abs() < 1e-9 && *g >= 0.0) && gains.iter().any(|&g| g > 0.0), "{gains:?}");
    assert!(rec.contains(&format!("\"c\": {}", idx[0] as usize)), "c is the n-best index of the pick: {rec}");
    // Without the weight: the record of before (no idx without a sound bit, the decoder's scores).
    with(&[&tl[..], &["--no-pool-recur", "--tinylm-scores", &scores]].concat());
    let rec = std::fs::read_to_string(&scores).unwrap();
    assert!(!rec.contains("\"idx\""), "{rec}");
    // Refused without a small model.
    let o = Command::new(env!("CARGO_BIN_EXE_shanjie-eval")).args([&base[..], &["--context", "--pool-mask-diff", &md]].concat()).current_dir(root()).output().unwrap();
    assert!(!o.status.success() && String::from_utf8_lossy(&o.stderr).contains("--pool-mask-diff needs --tinylm"));
}
