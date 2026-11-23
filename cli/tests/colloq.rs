//! Colloquial sandhi in the evaluation CLI and the replay (docs/spec/decoder.md §6.6 and §8.7, docs/spec/evaluation.md §1.4, §4). Needs data/lm like the
//! other LM tests and python3 (the probe is built by tools/build_probe.py from the public files, into a temporary directory).
use core::engine::{load_lexicon_packs, PACK_ACG, PACK_BEAUTY, PACK_IT, PACK_MEME};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicUsize, Ordering};

static RUN: AtomicUsize = AtomicUsize::new(0);

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}

fn scratch(name: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("shanjie-colloq-{name}-{}-{}", std::process::id(), RUN.fetch_add(1, Ordering::Relaxed)));
    std::fs::create_dir_all(&d).unwrap();
    d
}

fn run(bin: &str, args: &[&str]) -> String {
    let out = Command::new(bin).args(args).output().unwrap();
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    String::from_utf8(out.stdout).unwrap()
}

fn summary(out: &str) -> &str {
    out.lines().find(|l| l.starts_with("## ")).expect("a summary line")
}

fn sha_of(summary: &str) -> &str {
    summary.split("'top1_sha256': '").nth(1).and_then(|s| s.split('\'').next()).expect("a top1_sha256")
}

/// The shipping flags of a profile (docs/spec/evaluation.md §7.3).
fn shipping(profile: &str) -> Vec<String> {
    let r = root();
    let mut a: Vec<String> = ["--lm", r.join("data/lm/bigram.sjlm").to_str().unwrap(), "--profile", profile, "--context", "--packs", "acg,it,meme,beauty", "--tinylm", r.join("data/lm/tinylm.sjtl").to_str().unwrap()]
        .iter()
        .map(|s| s.to_string())
        .collect();
    if profile == "chat" {
        a.extend(["--colloquial-core".to_string(), r.join("data/lm/colloquial.sjlm").to_str().unwrap().to_string()]);
    }
    a
}

/// The CLI's picks (`--picks`): the sentence each row is scored on.
fn picks(rows: &Path, profile: &str, extra: &[&str], dir: &Path) -> Vec<String> {
    let p = dir.join(format!("picks-{}.tsv", RUN.fetch_add(1, Ordering::Relaxed)));
    let mut a = shipping(profile);
    a.extend(["--rows".to_string(), rows.to_str().unwrap().to_string(), "--picks".to_string(), p.to_str().unwrap().to_string()]);
    a.extend(extra.iter().map(|s| s.to_string()));
    let refs: Vec<&str> = a.iter().map(String::as_str).collect();
    run(env!("CARGO_BIN_EXE_shanjie-eval"), &refs);
    std::fs::read_to_string(&p).unwrap().lines().map(|l| l.splitn(3, '\t').nth(2).unwrap_or("").to_string()).collect()
}

/// With the shipping flags, chat and formal, the words typed as said come first: the reduplicated words of the `colloq` rows (熱騰騰, 迷迷糊糊, 紅彤彤), the words
/// the neutral source reads (厲害, 燈籠, 賠不是; 早上, 便當, 窗戶, 運氣, 喉嚨, which the two dictionaries read with a neutral tone; 知道, 漂亮, which only
/// the revised one does). Typed in their lexicon reading they are as with `--no-tone3`, and with `--no-tone3` none of the colloquial readings gives its word.
#[test]
fn colloquial_readings_decode_to_the_word_in_both_profiles() {
    let dir = scratch("examples");
    let cases = [
        ("熱騰騰", "ㄖㄜˋ ㄊㄥ ㄊㄥ", "ㄖㄜˋ ㄊㄥˊ ㄊㄥˊ"),
        ("迷迷糊糊", "ㄇㄧˊ ㄇㄧ˙ ㄏㄨ ㄏㄨ", "ㄇㄧˊ ㄇㄧˊ ㄏㄨˊ ㄏㄨˊ"),
        ("厲害", "ㄌㄧˋ ㄏㄞ˙", "ㄌㄧˋ ㄏㄞˋ"),
        ("燈籠", "ㄉㄥ ㄌㄨㄥ˙", "ㄉㄥ ㄌㄨㄥˊ"),
        ("賠不是", "ㄆㄟˊ ㄅㄨˊ ㄕ˙", "ㄆㄟˊ ㄅㄨˊ ㄕˋ"),
        ("早上", "ㄗㄠˇ ㄕㄤ˙", "ㄗㄠˇ ㄕㄤˋ"),
        ("便當", "ㄅㄧㄢˋ ㄉㄤ˙", "ㄅㄧㄢˋ ㄉㄤ"),
        ("窗戶", "ㄔㄨㄤ ㄏㄨ˙", "ㄔㄨㄤ ㄏㄨˋ"),
        ("運氣", "ㄩㄣˋ ㄑㄧ˙", "ㄩㄣˋ ㄑㄧˋ"),
        ("喉嚨", "ㄏㄡˊ ㄌㄨㄥ˙", "ㄏㄡˊ ㄌㄨㄥˊ"),
        ("紅彤彤", "ㄏㄨㄥˊ ㄊㄨㄥ ㄊㄨㄥ", "ㄏㄨㄥˊ ㄊㄨㄥˊ ㄊㄨㄥˊ"),
        ("知道", "ㄓ ㄉㄠ˙", "ㄓ ㄉㄠˋ"),
        ("漂亮", "ㄆㄧㄠˋ ㄌㄧㄤ˙", "ㄆㄧㄠˋ ㄌㄧㄤˋ"),
    ];
    let (said, plain) = (dir.join("said.txt"), dir.join("plain.txt"));
    std::fs::write(&said, cases.iter().map(|(w, r, _)| format!("|{w}|{r}\n")).collect::<String>()).unwrap();
    std::fs::write(&plain, cases.iter().map(|(w, _, r)| format!("|{w}|{r}\n")).collect::<String>()).unwrap();
    let want: Vec<String> = cases.iter().map(|(w, ..)| w.to_string()).collect();
    // 紅彤彤 and 紅通通 are two common writings of one word under one reading (docs/spec contract §4): either is right.
    let norm = |v: Vec<String>| v.into_iter().map(|w| if w == "紅通通" { "紅彤彤".to_string() } else { w }).collect::<Vec<_>>();
    for profile in ["chat", "formal"] {
        assert_eq!(norm(picks(&said, profile, &["--fuzzy", "0"], &dir)), want, "{profile}");
        assert_eq!(norm(picks(&plain, profile, &[], &dir)), want, "{profile}: the lexicon readings");
        assert_eq!(norm(picks(&plain, profile, &["--no-tone3"], &dir)), want, "{profile}: the lexicon readings, off");
        // Off: no `colloq` row, no neutral source, and a neutral syllable without an entry has no path (wrong with `--fuzzy 0`, the run goes on).
        let off = picks(&said, profile, &["--no-tone3", "--fuzzy", "0"], &dir);
        assert!(off.iter().zip(&want).all(|(a, b)| a != b), "{profile}: {off:?}");
    }
    std::fs::remove_dir_all(&dir).unwrap();
}

/// docs/spec/evaluation.md §1.4: the tag ends `+t3c` with the switch on; `--neutral-delta D` (evaluation only) adds `:nD` when D is not `DELTA_NEUTRAL`, after
/// `:dD` of `--tone3-delta`; it is an error with `--no-tone3`, which leaves the mark out.
#[test]
fn tags() {
    let dir = scratch("tags");
    let r = root();
    let rows = dir.join("rows.txt");
    std::fs::write(&rows, "|厲害|ㄌㄧˋ ㄏㄞ˙\n|眨眼|ㄓㄚˇ ㄧㄢˇ\n").unwrap();
    let lm = r.join("data/lm/bigram.sjlm");
    let base = ["--lm", lm.to_str().unwrap(), "--profile", "chat", "--rows", rows.to_str().unwrap()];
    let with = |extra: &[&str]| Command::new(env!("CARGO_BIN_EXE_shanjie-eval")).args(base).args(extra).output().unwrap();
    let line = |extra: &[&str]| {
        let o = with(extra);
        assert!(o.status.success(), "{}", String::from_utf8_lossy(&o.stderr));
        summary(&String::from_utf8(o.stdout).unwrap()).to_string()
    };
    assert!(line(&[]).contains("+rm+rn+t3c  {'n': 2, 'top1': 2,"), "{}", line(&[]));
    assert!(line(&["--neutral-delta", "4"]).contains("+rm+rn+t3c  {"), "the default: no mark");
    assert!(line(&["--neutral-delta", "1.5"]).contains("+rm+rn+t3c:n1.5  {"));
    assert!(line(&["--tone3-delta", "2", "--neutral-delta", "1.5"]).contains("+rm+rn+t3c:d2:n1.5  {"));
    assert!(!with(&["--no-tone3", "--neutral-delta", "1.5"]).status.success());
    assert!(!with(&["--neutral-delta", "-1"]).status.success());
    let off = line(&["--no-tone3", "--fuzzy", "0"]);
    assert!(off.contains("+fz:0") && !off.contains("+t3") && off.contains("'n': 2, 'top1': 1,"), "{off}");
    std::fs::remove_dir_all(&dir).unwrap();
}

/// Whether the engine's `finish_syllable` takes every syllable of a reading as the replay types it: the lexicon has it; or the sandhi rule or K accepts it
/// (K: a neutral syllable after a syllable, not one typed with digit keys alone, which is the digit layer's).
fn typable(lex: &core::Lexicon, syls: &[&str]) -> bool {
    let digits = |s: &str| s.chars().all(|c| "ㄅㄉㄓㄚㄞㄢˇˋˊ˙".contains(c));
    syls.iter().enumerate().all(|(i, s)| {
        lex.has_single(s)
            || core::tone3::unaccepted(lex, s)
            || (i > 0 && syls[i - 1].ends_with('ˊ') && core::tone3::unaccepted_tail(lex, s))
            || (i > 0 && core::tone3::neutral_single(lex, s) && !digits(s))
    })
}

/// On, `shanjie-replay` and the CLI pick the same first candidate row for row on the colloquial probes of the typo reports and the typing test (built here
/// from the public files and eval/probe/colloq-probe-words.tsv): the two share the one position table and the one lexicon. Only the rows every syllable of
/// which `finish_syllable` takes are compared; the others are counted on stderr.
#[test]
fn replay_and_cli_agree_on_the_colloquial_probes() {
    let dir = scratch("probe");
    let r = root();
    let lex = load_lexicon_packs(&r.join("data/lexicon"), Some((&r.join("data/packs"), PACK_ACG | PACK_IT | PACK_MEME | PACK_BEAUTY))).unwrap().0;
    let (replay, eval) = (env!("CARGO_BIN_EXE_shanjie-replay"), env!("CARGO_BIN_EXE_shanjie-eval"));
    for set in ["user-reported", "user-typing"] {
        let probe = dir.join(format!("{set}.txt"));
        let st = Command::new("python3")
            .args(["tools/build_probe.py", "--colloq", "eval/probe/colloq-probe-words.tsv", "--rows", &format!("eval/dev/{set}.txt"), "--out", probe.to_str().unwrap()])
            .current_dir(&r)
            .status()
            .unwrap();
        assert!(st.success());
        let text = std::fs::read_to_string(&probe).unwrap();
        let (kept, dropped): (Vec<&str>, Vec<&str>) = text.lines().partition(|l| typable(&lex, &l.rsplit('|').next().unwrap().split(' ').collect::<Vec<_>>()));
        eprintln!("{set}: {} rows compared, {} rows not compared (a syllable finish_syllable does not take)", kept.len(), dropped.len());
        assert!(!kept.is_empty(), "{set}: rows to compare");
        let rows = dir.join(format!("{set}-kept.txt"));
        std::fs::write(&rows, kept.join("\n") + "\n").unwrap();
        let rows = rows.to_str().unwrap();
        let mut a = shipping("chat");
        a.extend(["--rows".to_string(), rows.to_string()]);
        let a: Vec<&str> = a.iter().map(String::as_str).collect();
        let (cli, rep) = (run(eval, &a), run(replay, &["--rows", rows]));
        assert_eq!(sha_of(summary(&cli)), sha_of(summary(&rep)), "{set}: {}\n{}", summary(&cli), summary(&rep));
        assert!(summary(&rep).contains("+t3c  {"));
    }
    std::fs::remove_dir_all(&dir).unwrap();
}
