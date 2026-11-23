//! Third-tone sandhi in the evaluation CLI and the replay (docs/spec/decoder.md §6.6, docs/spec/evaluation.md §1.4, §4). Needs data/lm like the
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
    let d = std::env::temp_dir().join(format!("shanjie-tone3-{name}-{}-{}", std::process::id(), RUN.fetch_add(1, Ordering::Relaxed)));
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

/// Contract tone3-sandhi §4: with the shipping flags, chat and formal, the words typed as said come first; typed in their lexicon reading they are as
/// before (the same words with `--no-tone3`, which is the step-1 tree's output).
#[test]
fn sandhi_readings_decode_to_the_word_in_both_profiles() {
    let dir = scratch("examples");
    let cases = [
        ("眨眼", "ㄓㄚˊ ㄧㄢˇ"),
        ("展覽館", "ㄓㄢˊ ㄌㄢˊ ㄍㄨㄢˇ"),
        ("小老虎", "ㄒㄧㄠˇ ㄌㄠˊ ㄏㄨˇ"),
        ("營養品", "ㄧㄥˊ ㄧㄤˊ ㄆㄧㄣˇ"),
        ("我想", "ㄨㄛˊ ㄒㄧㄤˇ"),
        ("給我", "ㄍㄟˊ ㄨㄛˇ"),
        ("小姐", "ㄒㄧㄠˊ ㄐㄧㄝ˙"),
        ("想想", "ㄒㄧㄤˊ ㄒㄧㄤ˙"),
        ("我想想", "ㄨㄛˊ ㄒㄧㄤˊ ㄒㄧㄤ˙"),
    ];
    let rows = dir.join("rows.txt");
    std::fs::write(&rows, cases.iter().map(|(w, r)| format!("|{w}|{r}\n")).collect::<String>()).unwrap();
    for profile in ["chat", "formal"] {
        let got = picks(&rows, profile, &[], &dir);
        let want: Vec<String> = cases.iter().map(|(w, _)| w.to_string()).collect();
        assert_eq!(got, want, "{profile}");
    }
    // The same words in their lexicon readings: unchanged by the switch.
    let base = dir.join("base.txt");
    std::fs::write(&base, "|眨眼|ㄓㄚˇ ㄧㄢˇ\n|展覽館|ㄓㄢˇ ㄌㄢˇ ㄍㄨㄢˇ\n|我想|ㄨㄛˇ ㄒㄧㄤˇ\n|小姐|ㄒㄧㄠˇ ㄐㄧㄝˇ\n").unwrap();
    for profile in ["chat", "formal"] {
        assert_eq!(picks(&base, profile, &[], &dir), picks(&base, profile, &["--no-tone3"], &dir), "{profile}");
    }
    std::fs::remove_dir_all(&dir).unwrap();
}

/// Contract tone3-sandhi §4: row 192 of the typo reports (眨眼 typed ㄓㄚˊ ㄧㄢˇ) is right in both settings.
#[test]
fn typo_report_row_192_is_right() {
    let dir = scratch("row192");
    let line = std::fs::read_to_string(root().join("eval/dev/user-reported.txt")).unwrap().lines().nth(191).unwrap().to_string();
    assert_eq!(line, "|眨眼|ㄓㄚˊ ㄧㄢˇ", "premise: row 192");
    let rows = dir.join("rows.txt");
    std::fs::write(&rows, format!("{line}\n")).unwrap();
    for profile in ["chat", "formal"] {
        assert_eq!(picks(&rows, profile, &[], &dir), ["眨眼"], "{profile}");
        assert_ne!(picks(&rows, profile, &["--no-tone3"], &dir), ["眨眼"], "{profile}: premise, wrong without the switch");
    }
    std::fs::remove_dir_all(&dir).unwrap();
}

/// docs/spec/evaluation.md §1.4: the tag ends `+t3c` with the switch on, `+t3c:d<δ>` with `--tone3-delta`, and has no mark with `--no-tone3`; the two options
/// together are an error. Contract tone3-sandhi §3.4: the arm off with `--fuzzy 0` counts a row that holds an unaccepted syllable with no adjacent-key
/// reading as wrong, rc 0, and goes on.
#[test]
fn tags_and_the_off_arm_with_fuzzy_0() {
    let dir = scratch("tags");
    let r = root();
    let lex = load_lexicon_packs(&r.join("data/lexicon"), None).unwrap().0;
    // An unaccepted Xˊ no adjacent-key reading covers, and the first word of its Xˇ.
    let (syl, word) = lex
        .syllables()
        .iter()
        .filter_map(|t| Some(format!("{}ˊ", t.strip_suffix('ˇ')?)))
        .filter(|s| core::tone3::unaccepted(&lex, s) && lex.adjacent_alts(s).is_empty())
        .find_map(|s| {
            let w = lex.entries(&[core::tone3::third(&s).unwrap()]).first().map(|(w, _)| w.to_string())?;
            Some((s, w))
        })
        .expect("premise: an unaccepted syllable without adjacent-key readings");
    let rows = dir.join("rows.txt");
    std::fs::write(&rows, format!("|{word}|{syl}\n|眨眼|ㄓㄚˇ ㄧㄢˇ\n")).unwrap();
    let lm = r.join("data/lm/bigram.sjlm");
    let base = ["--lm", lm.to_str().unwrap(), "--profile", "chat", "--rows", rows.to_str().unwrap()];
    let eval = env!("CARGO_BIN_EXE_shanjie-eval");
    let with = |extra: &[&str]| Command::new(eval).args(base).args(extra).output().unwrap();
    let on = with(&[]);
    assert!(on.status.success());
    let s = String::from_utf8(on.stdout).unwrap();
    assert!(summary(&s).contains("+rm+rn+t3c  {"), "{s}");
    let d = String::from_utf8(with(&["--tone3-delta", "1.5"]).stdout).unwrap();
    assert!(summary(&d).contains("+rm+rn+t3c:d1.5  {"), "{d}");
    assert!(!with(&["--no-tone3", "--tone3-delta", "1.5"]).status.success());
    assert!(!with(&["--no-tone3"]).status.success(), "premise: off without --fuzzy, the row stops the run as before");
    let off = with(&["--no-tone3", "--fuzzy", "0"]);
    assert!(off.status.success());
    let s = String::from_utf8(off.stdout).unwrap();
    assert!(summary(&s).contains("+fz:0") && !summary(&s).contains("+t3") && summary(&s).contains("'n': 2, 'top1': 1,"), "{s}");
    std::fs::remove_dir_all(&dir).unwrap();
}

/// Whether the engine's `finish_syllable` takes every syllable of a reading (the lexicon has it, or it is an unaccepted syllable, or an unaccepted tail after a
/// second tone): the rows the replay can type as the CLI reads them.
fn typable(lex: &core::Lexicon, syls: &[&str]) -> bool {
    syls.iter().enumerate().all(|(i, s)| {
        lex.has_single(s) || core::tone3::unaccepted(lex, s) || (i > 0 && syls[i - 1].ends_with('ˊ') && core::tone3::unaccepted_tail(lex, s))
    })
}

/// Contract tone3-sandhi §5: on, `shanjie-replay` and the CLI pick the same first candidate row for row on the sandhi probes of the typo reports and the typing
/// test (both versions of the flip, built here from the public files): the evidence that the two share the one position table. Only the rows every syllable of
/// which `finish_syllable` takes are compared; the others are counted on stderr. Off (`--no-tone3` on both), the same on the rows of lexicon syllables only.
#[test]
fn replay_and_cli_agree_on_the_sandhi_probes() {
    let dir = scratch("probe");
    let r = root();
    let lex = load_lexicon_packs(&r.join("data/lexicon"), Some((&r.join("data/packs"), PACK_ACG | PACK_IT | PACK_MEME | PACK_BEAUTY))).unwrap().0;
    let (replay, eval) = (env!("CARGO_BIN_EXE_shanjie-replay"), env!("CARGO_BIN_EXE_shanjie-eval"));
    for set in ["user-reported", "user-typing"] {
        for variant in ["ds", "sd"] {
            let probe = dir.join(format!("{set}-{variant}.txt"));
            let st = Command::new("python3")
                .args(["tools/build_probe.py", "--tone3", variant, "--rows", &format!("eval/dev/{set}.txt"), "--out", probe.to_str().unwrap()])
                .current_dir(&r)
                .status()
                .unwrap();
            assert!(st.success());
            let text = std::fs::read_to_string(&probe).unwrap();
            let (kept, dropped): (Vec<&str>, Vec<&str>) = text.lines().partition(|l| typable(&lex, &l.rsplit('|').next().unwrap().split(' ').collect::<Vec<_>>()));
            eprintln!("{set} {variant}: {} rows compared, {} rows not compared (a syllable finish_syllable does not take)", kept.len(), dropped.len());
            assert!(kept.len() > 20, "{set} {variant}: enough rows");
            // Off, only the rows of syllables the lexicon has (the others have no path in the CLI, and the replay drops them).
            let plain: Vec<&str> = kept.iter().copied().filter(|l| l.rsplit('|').next().unwrap().split(' ').all(|s| lex.has_single(s))).collect();
            for off in [false, true] {
                let rows = dir.join(format!("{set}-{variant}-{off}.txt"));
                std::fs::write(&rows, if off { &plain } else { &kept }.join("\n") + "\n").unwrap();
                let rows = rows.to_str().unwrap();
                let mut a = shipping("chat");
                a.extend(["--rows".to_string(), rows.to_string()]);
                let mut b = vec!["--rows".to_string(), rows.to_string()];
                if off {
                    a.push("--no-tone3".into());
                    b.push("--no-tone3".into());
                }
                let a: Vec<&str> = a.iter().map(String::as_str).collect();
                let b: Vec<&str> = b.iter().map(String::as_str).collect();
                let (cli, rep) = (run(eval, &a), run(replay, &b));
                assert_eq!(sha_of(summary(&cli)), sha_of(summary(&rep)), "{set} {variant} off {off}: {}\n{}", summary(&cli), summary(&rep));
                assert_eq!(summary(&rep).contains("+t3c  {"), !off);
            }
        }
    }
    std::fs::remove_dir_all(&dir).unwrap();
}
