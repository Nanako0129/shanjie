//! The reading term of the n-gram decoder (docs/contracts/reading-decode-2.md; it is the CLI's default now, `--no-reading-decode` leaves it out). With the
//! option the CLI is what main printed before the term existed: the summary lines of eight settings (chat and formal, with and without `--tinylm`, on
//! dev302 and typing76 of eval/bench/suite-v1, `--context --packs acg`) and the dump of the four n-gram ones are pinned to what origin/main 533a029d printed
//! (recorded 2026-11-01; Mac arm64, data/lm as in data/*.sha256). By default every tag ends `+rd+rx:<sha8 of data/lexicon/std-exempt.tsv>`, a public row's
//! decoder first changes and the scores file's `ng` is the decoder's score with the term in it.
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..")
}

const NEEDS: &str = "data/lm/ (bigram.sjlm, classes.sjc, kn.sjkn, trigram.sjt3, trigram-chat.sjt3, tinylm.sjtl) is needed: see tests/golden.rs and data/*.sha256";

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
    ("chat", false, "dev302", 302, 249, 300, "3f8a405ff0de1a327f6bfa1935b10bf24cd54b15305c1cf79b09d406da9de17c"),
    ("chat", false, "typing76", 76, 70, 75, "0b9e970b7ed1c0897711257c45ceb0e6dc7c9d8458fc3747a46d96ee752deed9"),
    ("chat", true, "dev302", 302, 265, 300, "56e6bfb69d02de85d98f5000499173b96652e2d15f91c47d6e6bffb5156f26da"),
    ("chat", true, "typing76", 76, 74, 75, "18cc1a1c71f6096a1383168226abdd8539043a3b30739adb679e749c455defd2"),
    ("formal", false, "dev302", 302, 260, 300, "3c6f177cdd786ae2b28c852a065e99318bc22693c1a87bbc8b7f6f1386dee66c"),
    ("formal", false, "typing76", 76, 69, 75, "0d5dbcbb7f2913a8f8a98c6cb30a7ad0d75791f9ac6966fa63c58d7828c306a9"),
    ("formal", true, "dev302", 302, 271, 300, "14308598adef27505ec096209a134e3a05c93f238369eea2b9a82dcaf2bc4ae1"),
    ("formal", true, "typing76", 76, 74, 75, "4a493930e622f30eb106ee143b456a054349e9f47ca5ac28e23055c5cee27c73"),
];

/// sha256 of the `--dump` file (all 64 candidates and their path scores) of the n-gram-only runs before the option existed.
const DUMP_BEFORE: [(&str, &str, &str); 4] = [
    ("chat", "dev302", "0ee1bb923a6729b5f74c02d7e4a0c75e1e1c1fe1913e6b455eb5b89b33f6e90f"),
    ("chat", "typing76", "cb58ab34d6319882e7e1b448236895bf75e9bbd5f503254109b14b2af92842d5"),
    ("formal", "dev302", "aab77cf71ad8f0ee3676db9458b26008aad0f6d5d093b07d273d8fb625322047"),
    ("formal", "typing76", "fb7dcfce1892466c26af8f48bb4b2f807149d3cccd3bd5adb9e533311b074710"),
];

fn args<'a>(profile: &'a str, tinylm: bool, rows: &'a str) -> Vec<&'a str> {
    let mut a = vec!["--lm", "data/lm/bigram.sjlm", "--profile", profile, "--context", "--packs", "acg", "--rows", rows];
    if tinylm {
        a.extend(["--tinylm", "data/lm/tinylm.sjtl"]);
    }
    a
}

/// `--no-reading-decode`: the summary line (tag, n, top1, oracle@64, top1_sha256) of eight settings is that of main before the term existed, and (macOS only:
/// the f64 dump values round differently under the Linux libm) so is the dump of four. The tag has neither `+rd` nor `+rx`.
#[test]
fn no_reading_decode_is_what_main_printed() {
    let dir = tmp("off");
    for (profile, tinylm, set, n, top1, o64, sha) in BEFORE {
        let rows = format!("eval/bench/suite-v1/{set}.txt");
        let dump = dir.0.join(format!("{profile}-{set}-{tinylm}.dump")).to_string_lossy().into_owned();
        let mut a = args(profile, tinylm, &rows);
        a.extend(["--dump", &dump, "--no-reading-decode"]);
        let o = run(&a);
        assert!(o.status.success(), "{}", text(&o.stderr));
        let line = text(&o.stdout);
        assert!(line.contains(&format!("{{'n': {n}, 'top1': {top1}, 'oracle@64': {o64}, 'top1_sha256': '{sha}'}}")), "{profile} {tinylm} {set}: {line}");
        assert!(!line.contains("+rd") && !line.contains("+rx"), "{line}");
        // macOS only: the dump carries f64 values that the Linux libm rounds differently in the last bit (as in colloquial.rs)
        #[cfg(target_os = "macos")]
        if !tinylm {
            let want = DUMP_BEFORE.iter().find(|d| d.0 == profile && d.1 == set).unwrap().2;
            assert_eq!(core::eval::sha256_hex(&std::fs::read(&dump).unwrap()), want, "{profile} {set}: dump");
        }
    }
}

/// The shipped list's hash, as the tag prints it.
fn exempt_sha8() -> String {
    core::eval::sha256_hex(&std::fs::read(root().join("data/lexicon/std-exempt.tsv")).unwrap())[..8].to_string()
}

/// By default every one of the eight settings ends its tag `+rd+rx:<sha8 of std-exempt.tsv>` (the numbers of these two public sets do not move: the term acts on
/// other rows, see the next test).
#[test]
fn every_setting_ends_its_tag_with_rd_and_rx() {
    let want = format!("+rd+rx:{}  {{'n': ", exempt_sha8());
    for (profile, tinylm, set, _n, _top1, _o64, sha) in BEFORE {
        let rows = format!("eval/bench/suite-v1/{set}.txt");
        let o = run(&args(profile, tinylm, &rows));
        assert!(o.status.success(), "{}", text(&o.stderr));
        let line = text(&o.stdout);
        assert!(line.contains(&want), "{profile} {tinylm} {set}: {line}");
        assert!(line.contains(sha), "{profile} {tinylm} {set}: public sets are not moved by the term: {line}");
    }
}

/// The effect on a public row, through the CLI (main's sentence, after wikitune): 以敵偽人員的名字命名 has the decoder first 以狄為… without the term in the written
/// setting (formal; chat has it right already) and the right sentence with it; the scores file's `ng` of the first candidate is the decoder's path score with the term in it, the dump's (not the score without). 搞完這波 (row 37
/// of eval/dev/user-reported.txt) keeps 波 as the first: ㄅㄛ 波 is one of the 12 exempt pairs.
#[test]
fn the_default_decoder_first_carries_the_term_and_ng_is_its_score() {
    let dir = tmp("on");
    let rows = dir.0.join("rows.txt");
    let src = std::fs::read_to_string(root().join("eval/dev/user-reported.txt")).unwrap();
    std::fs::write(&rows, format!("|以敵偽人員的名字命名|ㄧˇ ㄉㄧˊ ㄨㄟˋ ㄖㄣˊ ㄩㄢˊ ㄉㄜ˙ ㄇㄧㄥˊ ㄗˋ ㄇㄧㄥˋ ㄇㄧㄥˊ\n{}\n", src.lines().nth(36).unwrap())).unwrap();
    let rows = rows.to_string_lossy().into_owned();
    let go = |profile: &str, off: bool| {
        let (d, s) = (dir.0.join(format!("{profile}-{off}.dump")).to_string_lossy().into_owned(), dir.0.join(format!("{profile}-{off}.scores")).to_string_lossy().into_owned());
        let mut a = args(profile, true, &rows);
        a.extend(["--dump", &d, "--tinylm-scores", &s]);
        if off {
            a.push("--no-reading-decode");
        }
        let o = run(&a);
        assert!(o.status.success(), "{}", text(&o.stderr));
        let dump = std::fs::read_to_string(d).unwrap();
        let firsts: Vec<(String, f64)> = dump.lines().map(|l| l.split('\t').collect::<Vec<_>>()).filter(|f| f[1] == "1").map(|f| (f[2].to_string(), f[3].parse().unwrap())).collect();
        let scores = std::fs::read_to_string(s).unwrap();
        let ng0: Vec<f64> = scores.lines().map(|l| l.split("\"ng\": [").nth(1).unwrap().split([',', ']']).next().unwrap().parse().unwrap()).collect();
        (firsts, ng0)
    };
    for profile in ["chat", "formal"] {
        let (off, off_ng) = go(profile, true);
        let (on, on_ng) = go(profile, false);
        assert_eq!(off[0].0.starts_with("以狄為"), profile == "formal", "{profile}: premise, without the term {}", off[0].0);
        assert_eq!(on[0].0, "以敵偽人員的名字命名", "{profile}: the term makes the decoder first right");
        assert_eq!((on[0].1, off[0].1), (on_ng[0], off_ng[0]), "{profile}: ng is the decoder's score (with the term, or without)");
        // chat has the right sentence either way, and its words all read standard readings (term 0): the same score; formal's first path changed
        assert_eq!(on[0].1 != off[0].1, profile == "formal", "{profile}: the term is in the score");
        assert_eq!((off[1].0.as_str(), on[1].0.as_str()), ("搞完這波", "搞完這波"), "{profile}: the exempt pair keeps 波");
    }
}
