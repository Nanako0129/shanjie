//! `--reading-decode` (experiments/r1-reading-decode, section R1d; evaluation only): the n-gram decoder adds the reading term to the path
//! score of every single-character word. Without the option the CLI is what it was: the summary lines of eight settings (chat and formal,
//! with and without `--tinylm`, on dev302 and typing76 of eval/bench/suite-v1, `--context --packs acg`) and the dump of the four n-gram ones
//! are pinned to what the binary before the option printed (recorded 2026-10-23, Mac arm64, data/lm as in data/*.sha256). With it, a public
//! row's decoder first changes and the scores file's `ng` is the decoder's score with the term in it.
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..")
}

const NEEDS: &str = "data/lm/ (bigram.sjlm, classes.sjc, kn.sjkn, trigram.sjt3, tinylm.sjtl) is needed: see tests/golden.rs and data/*.sha256";

fn run(args: &[&str]) -> Output {
    assert!(root().join("data/lm/tinylm.sjtl").exists(), "{NEEDS}");
    Command::new(env!("CARGO_BIN_EXE_shanjie-eval")).args(args).current_dir(root()).output().unwrap()
}

fn text(b: &[u8]) -> String {
    String::from_utf8(b.to_vec()).unwrap()
}

struct Dir(PathBuf);
impl Drop for Dir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn tmp(name: &str) -> Dir {
    let d = std::env::temp_dir().join(format!("shanjie-cli-reading-decode-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    Dir(d)
}

/// (profile, with --tinylm, set, n, top1, oracle@64, top1_sha256) of the binary before the option existed.
const BEFORE: [(&str, bool, &str, usize, usize, usize, &str); 8] = [
    ("chat", false, "dev302", 302, 246, 300, "d8ac58795e5649a557bcd62e7639cee2a9bbdbbe13860f13794ff95092ce5be8"),
    ("chat", false, "typing76", 76, 70, 76, "192b2bf3c1d59ba9a35586cf21292ffee7c54590dba16ef0e246a45557bb292c"),
    ("chat", true, "dev302", 302, 262, 300, "7a5949c5ce3248428c60dd0b351de79c8dd91228e9e6a587957f806e66b92736"),
    ("chat", true, "typing76", 76, 72, 76, "fc046ae3ec70787d02de90c96ffa1e4f276305cfd08e46808fcc46bb1f82ff7a"),
    ("formal", false, "dev302", 302, 253, 300, "f2fe56415ff1a62424487fd8cf8b61c0bf0e8f3cd59b5e9429e2aa1ef5559afd"),
    ("formal", false, "typing76", 76, 68, 76, "7d53f185972097902f0046f50cec94c5b9a3676a035fa2656e70cf76bdb65075"),
    ("formal", true, "dev302", 302, 265, 300, "2fe9bbfb5ac32d4c01433ba66bc0d1f40d9cecc70390bc1b294c473a9dceb3b0"),
    ("formal", true, "typing76", 76, 69, 76, "1311928bb7f109a478ccc145c2f3e4f7139749a8081a39c61db33b6b870decaa"),
];

/// sha256 of the `--dump` file (all 64 candidates and their path scores) of the n-gram-only runs before the option existed.
const DUMP_BEFORE: [(&str, &str, &str); 4] = [
    ("chat", "dev302", "def3f2f9abb5de591955ec2b16a46177b571e57405f69479b647f5800f4dbd73"),
    ("chat", "typing76", "ade53bfc6a06c6db8ab488b18b4c82e57a3ec63ad04d7172b59e4d6eb400c371"),
    ("formal", "dev302", "c5293337e060318afcc64343f0c21bdac5cf061a11c3399111cc5766ae883a8e"),
    ("formal", "typing76", "f7632bbf1e183a3791f5f5e3d3c2e42bdba0d051ec3bca82f33b86c9078b775b"),
];

fn args<'a>(profile: &'a str, tinylm: bool, rows: &'a str) -> Vec<&'a str> {
    let mut a = vec!["--lm", "data/lm/bigram.sjlm", "--profile", profile, "--context", "--packs", "acg", "--rows", rows];
    if tinylm {
        a.extend(["--tinylm", "data/lm/tinylm.sjtl"]);
    }
    a
}

/// The default path is untouched: the summary line (tag, n, top1, oracle@64, top1_sha256) of eight settings and the dump of four are those of
/// the binary before `--reading-decode`.
#[test]
fn without_the_option_every_output_is_what_it_was() {
    let dir = tmp("off");
    for (profile, tinylm, set, n, top1, o64, sha) in BEFORE {
        let rows = format!("eval/bench/suite-v1/{set}.txt");
        let dump = dir.0.join(format!("{profile}-{set}-{tinylm}.dump")).to_string_lossy().into_owned();
        let mut a = args(profile, tinylm, &rows);
        a.extend(["--dump", &dump]);
        let o = run(&a);
        assert!(o.status.success(), "{}", text(&o.stderr));
        let line = text(&o.stdout);
        assert!(line.contains(&format!("{{'n': {n}, 'top1': {top1}, 'oracle@64': {o64}, 'top1_sha256': '{sha}'}}")), "{profile} {tinylm} {set}: {line}");
        assert!(!line.contains("+rd"), "{line}");
        if !tinylm {
            let want = DUMP_BEFORE.iter().find(|d| d.0 == profile && d.1 == set).unwrap().2;
            assert_eq!(core::eval::sha256_hex(&std::fs::read(&dump).unwrap()), want, "{profile} {set}: dump");
        }
    }
}

/// With the option: the tag ends in `+rd`; row 10 of eval/dev/user-reported.txt (`版控`, whose `空` reading ㄎㄨㄥˋ is a lexicon floor score) has
/// another decoder first than without it, the right one; and the scores file's `ng` of that row is the decoder's score with the term in it (the
/// dump's), not the score without. A reading that changes nothing for a row keeps the row's first and its score.
#[test]
fn with_the_option_the_decoder_first_changes_and_ng_carries_the_term() {
    let dir = tmp("on");
    let src = std::fs::read_to_string(root().join("eval/dev/user-reported.txt")).unwrap();
    let rows = dir.0.join("rows.txt");
    // row 10 (版控) and row 1 (nothing to change in a plain sentence), both public
    std::fs::write(&rows, format!("{}\n{}\n", src.lines().nth(9).unwrap(), src.lines().next().unwrap())).unwrap();
    let rows = rows.to_string_lossy().into_owned();
    let go = |profile: &str, rd: bool| {
        let (d, s) = (dir.0.join(format!("{profile}-{rd}.dump")).to_string_lossy().into_owned(), dir.0.join(format!("{profile}-{rd}.scores")).to_string_lossy().into_owned());
        let mut a = args(profile, true, &rows);
        a.extend(["--dump", &d, "--tinylm-scores", &s]);
        if rd {
            a.push("--reading-decode");
        }
        let o = run(&a);
        assert!(o.status.success(), "{}", text(&o.stderr));
        let line = text(&o.stdout);
        assert_eq!(line.contains("+rd  {"), rd, "{line}");
        assert!(!rd || line.contains("+rd  {'n': 2,"), "the tag ends with +rd: {line}");
        let dump = std::fs::read_to_string(d).unwrap();
        // (surface, score) of rank 1 per row
        let firsts: Vec<(String, f64)> = dump.lines().map(|l| l.split('\t').collect::<Vec<_>>()).filter(|f| f[1] == "1").map(|f| (f[2].to_string(), f[3].parse().unwrap())).collect();
        let scores = std::fs::read_to_string(s).unwrap();
        let ng0: Vec<f64> = scores
            .lines()
            .map(|l| l.split("\"ng\": [").nth(1).unwrap().split([',', ']']).next().unwrap().parse().unwrap())
            .collect();
        (firsts, ng0)
    };
    for profile in ["chat", "formal"] {
        let (off, off_ng) = go(profile, false);
        let (on, on_ng) = go(profile, true);
        assert_eq!(off[0].0, "你有東西要先進版空嗎", "{profile}: premise, the n-gram's first is the wrong 空");
        assert_eq!(on[0].0, "你有東西要先進版控嗎", "{profile}: the term moves the decoder's first to the right 控");
        assert_eq!(on[0].1, on_ng[0], "{profile}: ng is the decoder's score with the term");
        assert_eq!(off[0].1, off_ng[0]);
        assert_eq!(off[1].0, on[1].0, "{profile}: row 2 keeps its first");
    }
}
