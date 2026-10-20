//! P4b `--colloquial` (experiments/p4b-colloquial/README.md): lc, k and s on a synthetic table (expected values computed by hand
//! below and by experiments/p4-colloquial/p4.py), the option checks, and that a run without the option is byte for byte what it was.
use cli::colloquial::{read_sets, Colloq};
use core::eval::sha256_hex;
use core::lm::Lm;
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..")
}

fn tmp(name: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("shanjie-cli-colloquial-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    d
}

fn text(b: &[u8]) -> String {
    String::from_utf8(b.to_vec()).unwrap()
}

fn strs(v: &[&str]) -> Vec<String> {
    v.iter().map(|s| s.to_string()).collect()
}

fn set(v: &[&str]) -> HashSet<String> {
    v.iter().map(|s| s.to_string()).collect()
}

/// The toy table of core/src/lm.rs's tests: vocabulary `<s> </s> a b` (V 4), N 10, eos total 4 (p_eos 0.4), D 0.75; unigrams a 6, b 4;
/// context `<s>` (total 5: a 3, b 2), context `a` (total 4: `</s>` 2). So N + V + 1 = 15, back(`<s>`) = 0.3, back(a) = 0.6875.
fn tiny() -> Vec<u8> {
    let mut b = b"SJLM0001".to_vec();
    b.extend(4u32.to_le_bytes());
    b.extend(10u64.to_le_bytes());
    b.extend(4u64.to_le_bytes());
    b.extend(0.75f64.to_le_bytes());
    for w in ["<s>", "</s>", "a", "b"] {
        b.extend((w.len() as u16).to_le_bytes());
        b.extend(w.as_bytes());
    }
    for u in [0u64, 0, 6, 4] {
        b.extend(u.to_le_bytes());
    }
    b.extend(2u32.to_le_bytes());
    for c in [0u32, 2] {
        b.extend(c.to_le_bytes());
    }
    for t in [5u64, 4] {
        b.extend(t.to_le_bytes());
    }
    for o in [0u32, 2, 3] {
        b.extend(o.to_le_bytes());
    }
    b.extend(3u32.to_le_bytes());
    for n in [2u32, 3, 1] {
        b.extend(n.to_le_bytes());
    }
    for c in [3u32, 2, 2] {
        b.extend(c.to_le_bytes());
    }
    b
}

fn tiny_file(dir: &Path) -> PathBuf {
    let p = dir.join("tiny.sjlm");
    std::fs::write(&p, tiny()).unwrap();
    p
}

fn colloq(mu: f64, gamma: f64, ovl: &[&str], base: &[&str]) -> Colloq {
    Colloq::new(Lm::parse(&tiny()).unwrap(), mu, gamma, set(ovl), set(base))
}

fn close(a: f64, b: f64) {
    assert!((a - b).abs() < 1e-9, "{a} vs {b}");
}

/// lc by hand: back-off to (c + 1) / 15, the end term to p_eos 0.4 when the last word has no entry for `</s>`.
#[test]
fn lc_by_hand() {
    let c = colloq(0.0, 0.0, &[], &[]);
    let (back_s, back_a, pe): (f64, f64, f64) = (1.0 - ((3.0 - 0.75) + (2.0 - 0.75)) / 5.0, 1.0 - (2.0 - 0.75) / 4.0, 0.4);
    // a b: P(a|<s>) seen; P(b|a) unseen after a context with entries; P(</s>|b): b has no context, so the end rate itself
    let ab = ((3.0 - 0.75) / 5.0 + back_s * (7.0f64 / 15.0)).log10() + (back_a * (5.0f64 / 15.0)).log10() + pe.log10();
    close(c.lc(&strs(&["a", "b"])), ab);
    // a: the end term of a context that has `</s>`
    let a = ((3.0 - 0.75) / 5.0 + back_s * (7.0f64 / 15.0)).log10() + ((2.0f64 - 0.75) / 4.0 + back_a * pe).log10();
    close(c.lc(&strs(&["a"])), a);
    // a word outside the table: c = 0, no context of its own, so back(`<s>`) (its predecessor has entries) * 1/15, then the end rate
    let z = (back_s * (1.0f64 / 15.0)).log10() + pe.log10();
    close(c.lc(&strs(&["zzz"])), z);
    // after an unknown word the next word has no context: pb alone
    close(c.lc(&strs(&["zzz", "a"])), (back_s * (1.0f64 / 15.0)).log10() + (7.0 / 15.0f64).log10() + ((2.0f64 - 0.75) / 4.0 + back_a * pe).log10());
}

#[test]
fn k_and_s_and_order() {
    let ovl = ["ovl", "dup", "both"];
    let c = colloq(0.5, 0.25, &ovl, &["both", "a"]);
    assert_eq!(c.k(&strs(&["dup", "a", "dup"])), 2, "a repeated overlay word counts twice");
    assert_eq!(c.k(&strs(&["both", "a", "names-only"])), 0, "in the base lexicon, or in no set: 0");
    assert_eq!(c.k(&strs(&["ovl"])), 1);
    let words = [strs(&["dup", "a", "dup"]), strs(&["a"]), strs(&["a"]), strs(&["a", "b"])];
    let w: Vec<&[String]> = words.iter().map(Vec::as_slice).collect();
    let ng = [-1.0, -2.0, -2.0, -2.5];
    let (order, s) = c.order(&ng, &w);
    for j in 0..4 {
        close(s[j], ng[j] + 0.5 * c.lc(&words[j]) - 0.25 * c.k(&words[j]) as f64);
    }
    // candidate 0 pays 0.5 for its two overlay words and gets its lc; 1 and 2 tie and keep their order
    assert_eq!(s[1], s[2]);
    let pos = |j: usize| order.iter().position(|&x| x == j).unwrap();
    assert!(pos(1) < pos(2), "a tie keeps the n-gram order");
    assert!(order.windows(2).all(|p| s[p[0]] >= s[p[1]]));
    // mu 0, gamma 0: s is ng and the order is the n-gram order (the input order for sorted input)
    let z = colloq(0.0, 0.0, &ovl, &["both"]);
    let (o, s0) = z.order(&ng, &w);
    assert_eq!((o, s0), (vec![0, 1, 2, 3], ng.to_vec()));
}

/// The two sets from literal files: names-add.tsv and sandhi-add.tsv words are in neither; an overlay word that is also in the base
/// lexicon is in the overlay set (k then leaves it out), and a repeat in the split is counted twice.
#[test]
fn sets_from_the_files() {
    let (lexd, packd) = (tmp("lex"), tmp("pack"));
    let w = |d: &Path, f: &str, t: &str| std::fs::write(d.join(f), t).unwrap();
    w(&lexd, "mcbpmf-data.txt", "# comment\n_ctl x y\nㄋㄧˇ-ㄏㄠˇ 你好 -3.0\nㄋㄧˇ 壞詞 -1.0\n");
    w(&lexd, "overlay-add.tsv", "ㄋㄧˇ-ㄏㄠˇ\t你好\t-3.0\t0\nㄔㄨㄥˊ-ㄉㄧㄝˊ\t重疊\t-4.0\t0\n");
    w(&lexd, "names-add.tsv", "ㄇㄧㄥˊ-ㄗˋ\t名字\t-3.0\t0\n");
    w(&lexd, "sandhi-add.tsv", "ㄅㄧㄢˋ-ㄧㄣ\t變調\t-3.0\t0\n");
    w(&packd, "acg-add.tsv", "ㄘˊ-ㄅㄠ\t詞包\t-3.0\t0\n");
    let (ovl, base) = read_sets(&lexd, &packd).unwrap();
    assert_eq!(ovl, set(&["你好", "重疊", "詞包"]));
    assert_eq!(base, set(&["你好"]), "a row whose syllable count differs from the characters, a comment and a control line are skipped");
    let c = Colloq::new(Lm::parse(&tiny()).unwrap(), 0.0, 1.0, ovl, base);
    assert_eq!(c.k(&strs(&["重疊", "你好", "重疊"])), 2);
    assert_eq!(c.k(&strs(&["名字", "變調", "壞詞"])), 0, "names-add.tsv, sandhi-add.tsv and unknown words are not penalised");
    assert_eq!(c.k(&strs(&["詞包"])), 1);
}

/// The sets from the repository's literal files are those the Python reference builds (tools/triage.py `Lex.base`, d23 `load_lex_scores`).
fn python(code: &str) -> String {
    let o = Command::new("python3").args(["-I", "-c", code]).current_dir(root()).output().expect("python3 is needed for the p4.py alignment");
    assert!(o.status.success(), "{}", text(&o.stderr));
    text(&o.stdout)
}

fn digest(s: &HashSet<String>) -> String {
    let mut v: Vec<&str> = s.iter().map(String::as_str).collect();
    v.sort();
    format!("{} {}", v.len(), sha256_hex(v.join("\n").as_bytes()))
}

#[test]
fn aligned_with_p4_py() {
    let dir = tmp("align");
    let table = tiny_file(&dir);
    let cases: [&[&str]; 5] = [&["a", "b"], &["a"], &["zzz"], &["zzz", "a"], &["b", "a", "a"]];
    let py = python(&format!(
        r#"
import sys, json, hashlib
sys.path.insert(0, "experiments/p4-colloquial")
import p4
tb = p4.Table({table:?})
print(json.dumps([tb.lc(c) for c in {cases}]))
lexscore, ovl = p4.D.load_lex_scores()
lex = p4.T.Lex.load()
for s in (ovl, lex.base):
    v = sorted(s)
    print(len(v), hashlib.sha256("\n".join(v).encode()).hexdigest())
print(json.dumps([p4.k_of(c, {{"a", "b"}}, {{"b"}}) for c in {cases}]))
"#,
        table = table.to_str().unwrap(),
        cases = format!("{cases:?}")
    ));
    let lines: Vec<&str> = py.lines().collect();
    let lcs: Vec<f64> = lines[0].trim_matches(['[', ']']).split(", ").map(|x| x.parse().unwrap()).collect();
    let c = Colloq::new(Lm::load_opts(&table, false, false, false).unwrap(), 0.0, 0.0, set(&["a", "b"]), set(&["b"]));
    for (case, want) in cases.iter().zip(&lcs) {
        close(c.lc(&strs(case)), *want);
    }
    let (ovl, base) = read_sets(&root().join("data/lexicon"), &root().join("data/packs")).unwrap();
    assert_eq!(digest(&ovl), lines[1], "overlay set differs from d23.load_lex_scores");
    assert_eq!(digest(&base), lines[2], "base set differs from triage.Lex.base");
    let ks: Vec<usize> = lines[3].trim_matches(['[', ']']).split(", ").map(|x| x.parse().unwrap()).collect();
    assert_eq!(cases.iter().map(|case| c.k(&strs(case))).collect::<Vec<_>>(), ks);
}

// ---- the binary

const BASE: [&str; 11] = ["--lm", "data/lm/bigram.sjlm", "--profile", "chat", "--dev", "16", "--context", "--packs", "acg", "--tinylm", "data/lm/tinylm.sjtl"];
const NO_LM: &str = "data/lm/ (bigram.sjlm, classes.sjc, kn.sjkn, tinylm.sjtl) is needed: see tests/golden.rs";

fn run(args: &[String]) -> Output {
    assert!(root().join("data/lm/tinylm.sjtl").exists(), "{NO_LM}");
    Command::new(env!("CARGO_BIN_EXE_shanjie-eval")).args(args).current_dir(root()).output().unwrap()
}

/// One run with every output file; -> (stdout, stderr, dump, words, rowstats, scores).
fn full_run(dir: &Path, extra: &[&str], tinylm: bool) -> (String, String, String, String, String, String) {
    let f = |n: &str| dir.join(n).to_string_lossy().into_owned();
    let mut a: Vec<String> = BASE[..9].iter().map(|s| s.to_string()).collect();
    for (k, n) in [("--dump", "dump"), ("--dump-words", "words"), ("--rowstats", "rs")] {
        a.extend([k.to_string(), f(n)]);
    }
    if tinylm {
        a.extend(["--tinylm", "data/lm/tinylm.sjtl", "--tinylm-scores", &f("sc")].map(String::from));
    }
    a.extend(extra.iter().map(|s| s.to_string()));
    let o = run(&a);
    assert!(o.status.success(), "{}", text(&o.stderr));
    let rd = |n: &str| std::fs::read_to_string(dir.join(n)).unwrap_or_default();
    (text(&o.stdout), text(&o.stderr), rd("dump"), rd("words"), rd("rs"), rd("sc"))
}

/// Without `--colloquial` every output is what the CLI wrote before the option existed (hashes taken from the binary before this change:
/// `--dev 16 --context --packs acg --tinylm data/lm/tinylm.sjtl`, `--dump --dump-words --rowstats --tinylm-scores`, model-v6 files).
#[test]
fn no_option_is_byte_identical_to_before() {
    let dir = tmp("golden");
    let (out, _, dump, words, rs, sc) = full_run(&dir, &[], true);
    let h = |s: &str| sha256_hex(s.as_bytes());
    assert_eq!(h(&dump), "7cd2988473895b8df271d8276489349809d2d6f50f761c56f3c96424b298c95a");
    assert_eq!(h(&words), "42152c06fa5c3b55a2e765ed9103128d4c780bfed4ff35e9f192220e3dce2a72");
    assert_eq!(h(&rs), "a795b9d2c943e64587906438de9439fc5302cff79341a63684328ba44f9b960b");
    assert_eq!(h(&sc), "b83921b3fdc3b8ea61a4b8f669383901d4308e1b02cb7c0e5d0153da43c50d3a");
    assert_eq!(h(&out), "c22805512788cbc3ad46acce4975f9f0202561aa1f5e6a3053c3dd32eadc67a5");
}

/// mu 0, gamma 0: s is ng, so every file is that of no option; only the summary tag and the stderr latency line are new.
#[test]
fn mu_zero_gamma_zero_changes_nothing() {
    let (dir, base_dir) = (tmp("zero"), tmp("zero-base"));
    let table = tiny_file(&dir);
    let t = table.to_str().unwrap();
    let (out0, _, d0, w0, r0, s0) = full_run(&base_dir, &[], true);
    let (out, err, d, w, r, s) = full_run(&dir, &["--colloquial", t, "--colloquial-mu", "0", "--colloquial-gamma", "0"], true);
    assert_eq!((d, w, r, s), (d0, w0, r0, s0));
    let tag = format!("+co:{}", &sha256_hex(&std::fs::read(&table).unwrap())[..8]);
    assert!(out.contains(&tag), "{out}");
    assert_eq!(out.replace(&tag, ""), out0);
    assert!(err.contains("colloquial: lc+k+sort n=16 p50=") && err.contains("table load") && err.contains("rss"), "{err}");
}

fn rows_of(dump: &str) -> Vec<(usize, usize, String, f64)> {
    dump.lines().map(|l| { let p: Vec<&str> = l.split('\t').collect(); (p[0].parse().unwrap(), p[1].parse().unwrap(), p[2].to_string(), p[3].parse().unwrap()) }).collect()
}

/// A strong weight: the dump is a permutation of the baseline's candidates, every score is ng + mu lc - gamma k of the same candidate
/// (the split from `--dump-words`, which moved with it), in descending order within the row; the rowstats and the choices follow.
#[test]
fn rerank_end_to_end() {
    let (dir, base_dir) = (tmp("e2e"), tmp("e2e-base"));
    let table = tiny_file(&dir);
    let (_, _, d0, w0, _, _) = full_run(&base_dir, &[], false);
    let (out, _, d, w, _, _) = full_run(&dir, &["--colloquial", table.to_str().unwrap(), "--colloquial-mu", "7", "--colloquial-gamma", "3"], false);
    assert!(out.contains("+co:"));
    let (ovl, base) = read_sets(&root().join("data/lexicon"), &root().join("data/packs")).unwrap();
    let c = Colloq::new(Lm::load_opts(&table, false, false, false).unwrap(), 7.0, 3.0, ovl, base);
    let words_of = |wd: &str| -> Vec<(usize, usize, Vec<String>)> {
        wd.lines().map(|l| l.split('\t').collect::<Vec<_>>()).filter(|p| p[1] != "0").map(|p| (p[0].parse().unwrap(), p[1].parse().unwrap(), p[2].split(' ').map(String::from).collect())).collect()
    };
    let (base_dump, base_words, new_dump, new_words) = (rows_of(&d0), words_of(&w0), rows_of(&d), words_of(&w));
    assert_eq!((new_dump.len(), new_words.len()), (base_dump.len(), base_words.len()));
    let mut moved = 0;
    for ((nd, nw), (bd, bw)) in new_dump.iter().zip(&new_words).zip(base_dump.iter().zip(&base_words)) {
        assert_eq!((nd.0, nd.1, nw.0, nw.1), (nd.0, nd.1, nd.0, nd.1), "ranks stay 1..64 in row order");
        assert_eq!(nw.2.concat(), nd.2, "a candidate's split moves with its surface");
        // the baseline candidate this one is: same row, same split
        let found = base_dump.iter().zip(&base_words).find(|((r, ..), (_, _, ws))| *r == nd.0 && *ws == nw.2);
        let ((_, _, bsurf, ng), _) = found.expect("a permutation of the baseline candidates");
        assert_eq!(*bsurf, nd.2);
        assert_eq!(nd.3, *ng + 7.0 * c.lc(&nw.2) - 3.0 * c.k(&nw.2) as f64);
        moved += (nd.2 != bd.2 || nw.2 != bw.2) as usize;
    }
    assert!(moved > 0, "a weight of 7 must reorder something");
    for row in new_dump.windows(2).filter(|p| p[0].0 == p[1].0) {
        assert!(row[0].3 >= row[1].3, "descending by s");
    }
}

#[test]
fn options_are_checked() {
    let dir = tmp("opts");
    let table = tiny_file(&dir);
    let t = table.to_str().unwrap().to_string();
    let args = |extra: &[&str]| -> Vec<String> {
        let mut a: Vec<String> = ["--lm", "data/lm/bigram.sjlm", "--dev", "2"].map(String::from).to_vec();
        a.extend(extra.iter().map(|s| s.to_string()));
        a
    };
    let co = |profile: &str| args(&["--profile", profile, "--colloquial", &t, "--colloquial-mu", "0.5", "--colloquial-gamma", "0.25"]);
    let refused = |a: Vec<String>, needle: &str| {
        let o = run(&a);
        assert!(!o.status.success() && o.stdout.is_empty() && text(&o.stderr).contains(needle), "{needle}: {}", text(&o.stderr));
    };
    refused(co("formal"), "chat only");
    refused(
        ["--lm", "data/lm/bigram.sjlm", "--profile", "chat", "--set", "holdout", "--colloquial", &t, "--colloquial-mu", "1", "--colloquial-gamma", "1"].map(String::from).to_vec(),
        "holdout",
    );
    refused(args(&["--profile", "chat", "--colloquial", &t, "--colloquial-mu", "0.5"]), "both");
    refused(args(&["--profile", "chat", "--colloquial", &t]), "both");
    refused(args(&["--profile", "chat", "--colloquial-mu", "0.5"]), "need --colloquial");
    refused(args(&["--profile", "chat", "--colloquial", &t, "--colloquial-mu", "x", "--colloquial-gamma", "1"]), "number");
    refused(args(&["--profile", "chat", "--colloquial", "/nonexistent.sjlm", "--colloquial-mu", "1", "--colloquial-gamma", "1"]), "--colloquial");
}
