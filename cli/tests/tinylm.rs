//! `--tinylm` / `--tinylm-scores` (docs/contracts/s7c-core-tinylm.md section 2.3, acceptance 2) on the toy model of
//! eval/golden/tinylm-tiny/. Its .sjtl records the hash of eval/golden/kn-tiny/bigram.sjlm, so it is refused with data/lm/bigram.sjlm
//! as it is; the tests that need it accepted patch the 32 hash bytes (header offset 68) to the model they run with.
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

const HASH_AT: usize = 68;
const NO_LM: &str = "data/lm/bigram.sjlm and classes.sjc are needed: see tests/golden.rs";

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..")
}

fn run(args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_shanjie-eval")).args(args).output().unwrap()
}

fn lm_path() -> String {
    let p = root().join("data/lm/bigram.sjlm");
    assert!(p.exists() && root().join("data/lm/classes.sjc").exists(), "{NO_LM}");
    p.to_string_lossy().into_owned()
}

fn sha_bytes(file: &Path) -> Vec<u8> {
    let h = core::eval::sha256_hex(&std::fs::read(file).unwrap());
    (0..32).map(|i| u8::from_str_radix(&h[2 * i..2 * i + 2], 16).unwrap()).collect()
}

/// A scratch directory with the toy .sjtl whose recorded n-gram hash is that of `lm`.
fn tmp_with_sjtl(name: &str, lm: &Path) -> (PathBuf, String) {
    let dir = std::env::temp_dir().join(format!("shanjie-cli-tinylm-{name}-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let mut b = std::fs::read(root().join("eval/golden/tinylm-tiny/tiny-int8.sjtl")).unwrap();
    b[HASH_AT..HASH_AT + 32].copy_from_slice(&sha_bytes(lm));
    let sjtl = dir.join("tiny.sjtl");
    std::fs::write(&sjtl, b).unwrap();
    (dir.clone(), sjtl.to_string_lossy().into_owned())
}

fn text(b: &[u8]) -> String {
    String::from_utf8(b.to_vec()).unwrap()
}

/// A `.sjtl` tuned with another n-gram model is refused: non-zero exit, a message naming `.sjtl`, nothing on stdout.
#[test]
fn another_ngram_model_is_refused() {
    let lm = lm_path();
    let toy = root().join("eval/golden/tinylm-tiny/tiny-int8.sjtl");
    let rows = std::env::temp_dir().join(format!("shanjie-cli-tinylm-rows-{}.txt", std::process::id()));
    std::fs::write(&rows, "|你好|ㄋㄧˇ ㄏㄠˇ\n").unwrap();
    let args = |tl: &str| -> Vec<String> {
        ["--lm", &lm, "--profile", "chat", "--rows", rows.to_str().unwrap(), "--tinylm", tl].map(String::from).to_vec()
    };
    let refused = run(&args(toy.to_str().unwrap()).iter().map(String::as_str).collect::<Vec<_>>());
    assert!(!refused.status.success());
    assert!(text(&refused.stderr).contains(".sjtl") && refused.stdout.is_empty(), "{}", text(&refused.stderr));

    // the same file with the right hash is accepted, so the refusal above was about the hash
    let (dir, patched) = tmp_with_sjtl("hash", Path::new(&lm));
    let ok = run(&args(&patched).iter().map(String::as_str).collect::<Vec<_>>());
    assert!(ok.status.success(), "{}", text(&ok.stderr));
    let line = text(&ok.stdout);
    let sha8 = &core::eval::sha256_hex(&std::fs::read(&patched).unwrap())[..8];
    assert!(line.starts_with(&format!("## {}  lm-chat+tl:{sha8}  {{", rows.file_name().unwrap().to_string_lossy())), "{line}");
    std::fs::remove_dir_all(dir).unwrap();
    std::fs::remove_file(rows).unwrap();
}

/// A broken `.sjtl` and `--tinylm-scores` without `--tinylm` stop the run too.
#[test]
fn bad_file_and_orphan_scores_option_are_errors() {
    let lm = lm_path();
    let bad = run(&["--lm", &lm, "--profile", "chat", "--dev", "1", "--tinylm", "/nonexistent/x.sjtl"]);
    assert!(!bad.status.success() && text(&bad.stderr).contains(".sjtl"));
    let orphan = run(&["--lm", &lm, "--profile", "chat", "--dev", "1", "--tinylm-scores", "/nonexistent/scores.jsonl"]);
    assert!(!orphan.status.success() && text(&orphan.stderr).contains("--tinylm"));
}

/// model-v6 sections 2.7 and 7 (P4-9): the Kneser-Ney side file beside the model must be the one the `.sjtl` records. Each
/// mismatch is refused with its own message and remedy: a file tuned without KN (version 1 here) with the side file on suggests
/// `--no-kn` (which then runs); the version 4 toy (recording kn-tiny/kn.sjkn) runs with that side file on, is refused with another
/// side file (no `--no-kn` advice) and with `--no-kn` (advice to drop it).
#[test]
fn kneser_ney_side_file_must_be_the_recorded_one() {
    let toy = root().join("eval/golden/kn-tiny");
    let (dir, sjtl) = tmp_with_sjtl("kn", &toy.join("bigram.sjlm"));
    for f in ["bigram.sjlm", "classes.sjc", "kn.sjkn"] {
        std::fs::copy(toy.join(f), dir.join(f)).unwrap();
    }
    let rows = dir.join("rows.txt");
    std::fs::write(&rows, "|你好|ㄋㄧˇ ㄏㄠˇ\n").unwrap();
    let lm = dir.join("bigram.sjlm");
    let v4 = root().join("eval/golden/tinylm-tiny/tiny-int8-v4.sjtl");
    let args = |tl: &str, extra: &[&str]| -> Output {
        let base = ["--lm", lm.to_str().unwrap(), "--profile", "chat", "--rows", rows.to_str().unwrap(), "--tinylm", tl];
        run(&[&base[..], extra].concat())
    };
    let refused = |o: &Output, has: &[&str], lacks: &[&str]| {
        let err = text(&o.stderr);
        assert!(!o.status.success() && o.stdout.is_empty(), "{err}");
        assert!(err.contains(".sjtl") && err.contains("Kneser-Ney"), "{err}");
        assert!(has.iter().all(|h| err.contains(h)) && !lacks.iter().any(|l| err.contains(l)), "{err}");
    };
    // Tuned without KN, side file on.
    refused(&args(&sjtl, &[]), &["tuned without", "--no-kn"], &[]);
    let off = args(&sjtl, &["--no-kn"]);
    assert!(off.status.success(), "{}", text(&off.stderr));
    // Tuned with this side file: runs with it on.
    let on = args(v4.to_str().unwrap(), &[]);
    assert!(on.status.success(), "{}", text(&on.stderr));
    assert!(text(&on.stdout).contains("+kn:"), "{}", text(&on.stdout));
    // Tuned with this side file, but it is off.
    refused(&args(v4.to_str().unwrap(), &["--no-kn"]), &["none is on", "drop --no-kn"], &[]);
    // Another side file beside the model.
    std::fs::copy(toy.join("kn-beta075.sjkn"), dir.join("kn.sjkn")).unwrap();
    refused(&args(v4.to_str().unwrap(), &[]), &["another Kneser-Ney side file"], &["--no-kn"]);
    std::fs::remove_dir_all(dir).unwrap();
}

/// The engine's context key is "^" when no Han character precedes the cursor; the model must see an empty k then, as score.py does
/// (tinylm.row_key). With --context, a row with an empty prefix and a row whose prefix ends in a non-Han character therefore score
/// exactly like the same rows without --context (k empty); fed "^" as a character (an UNK token), they would differ. A row with a
/// Han prefix scores its own top 8 (read from --dump) with k = that prefix: its LLs are exactly TinyLm::ll_many's with k "我們", so a
/// CLI that dropped the key would fail here.
#[test]
fn sentinel_context_is_an_empty_k() {
    let lm = lm_path();
    let (dir, sjtl) = tmp_with_sjtl("sentinel", Path::new(&lm));
    let rows = dir.join("rows.txt");
    std::fs::write(&rows, "|你好|ㄋㄧˇ ㄏㄠˇ\n好,|我們|ㄨㄛˇ ㄇㄣˊ\n我們|你好嗎|ㄋㄧˇ ㄏㄠˇ ㄇㄚ˙\n").unwrap();
    let scores = |name: &str, ctx: bool| -> Vec<String> {
        let out = dir.join(name);
        let mut a = vec!["--lm", &lm, "--profile", "chat", "--rows", rows.to_str().unwrap(), "--tinylm", &sjtl, "--tinylm-scores", out.to_str().unwrap()];
        if ctx {
            a.push("--context");
        }
        let o = run(&a);
        assert!(o.status.success(), "{}", text(&o.stderr));
        std::fs::read_to_string(out).unwrap().lines().map(String::from).collect()
    };
    let (plain, with_ctx) = (scores("plain.jsonl", false), scores("ctx.jsonl", true));
    let ll = |l: &String| l[l.find("\"ll\": ").expect("n >= 2: an ll list")..].to_string();
    assert_eq!(ll(&plain[0]), ll(&with_ctx[0]), "empty prefix");
    assert_eq!(ll(&plain[1]), ll(&with_ctx[1]), "prefix ending in a non-Han character");
    // Row 3's own candidates, from the dump of a --context run (same decode as the scores above).
    let dump = dir.join("ctx.dump");
    let o = run(&["--lm", &lm, "--profile", "chat", "--rows", rows.to_str().unwrap(), "--context", "--dump", dump.to_str().unwrap()]);
    assert!(o.status.success(), "{}", text(&o.stderr));
    let cands: Vec<String> = std::fs::read_to_string(&dump).unwrap().lines()
        .filter_map(|l| { let f: Vec<&str> = l.split('\t').collect(); (f[0] == "3" && f[1].parse::<usize>().unwrap() <= 8).then(|| f[2].to_string()) })
        .collect();
    let model = core::tinylm::TinyLm::load(Path::new(&sjtl)).unwrap();
    let want = model.ll_many(&cands.iter().map(|c| ("我們", c.as_str())).collect::<Vec<_>>()).unwrap();
    let l = &with_ctx[2];
    let got: Vec<f64> = l[l.find("\"ll\": [").unwrap() + 7..l.rfind(']').unwrap()].split(", ").map(|x| x.parse().unwrap()).collect();
    assert_eq!(got, want, "a Han prefix is the model's context");
    // numbers only: no candidate text in the file
    assert!(plain.iter().all(|l| l.is_ascii()));
    std::fs::remove_dir_all(dir).unwrap();
}

/// The holdout never gets a per-candidate record (it is what alpha and tau are tuned on), and alpha / tau are refused under
/// n-gram settings other than the tuned ones. Each refusal comes before any model or row is read.
#[test]
fn refusals_before_anything_is_read() {
    let lm = lm_path();
    let (dir, sjtl) = tmp_with_sjtl("refuse", Path::new(&lm));
    let o = run(&["--lm", &lm, "--profile", "chat", "--set", "holdout", "--tinylm", "no-such.sjtl", "--tinylm-scores", "x.jsonl"]);
    assert!(!o.status.success() && text(&o.stderr).contains("holdout"), "{}", text(&o.stderr));
    let rows = dir.join("rows.txt");
    std::fs::write(&rows, "|你好|ㄋㄧˇ ㄏㄠˇ\n").unwrap();
    for flag in ["--no-demote", "--no-classes"] {
        let o = run(&["--lm", &lm, "--profile", "chat", "--rows", rows.to_str().unwrap(), "--tinylm", &sjtl, flag]);
        assert!(!o.status.success() && text(&o.stderr).contains(".sjtl") && o.stdout.is_empty(), "{flag}: {}", text(&o.stderr));
    }
    std::fs::remove_dir_all(dir).unwrap();
}

/// The synthetic version 3 toy (eval/golden/tinylm-tiny/tiny-int8-v3.sjtl) with the hash of `lm` and, for both profiles, alpha 100 /
/// tau infinite / delta 0 (its LLs decide: rows move) at level `moving` (0 conservative, 1 standard, 2 aggressive) and alpha 0
/// (the n-gram first always stays) at the other two. Offsets: docs/contracts/rerank-strength.md section 2.1.
fn v3_with_moving(dir: &Path, lm: &Path, moving: u32) -> String {
    let mut b = std::fs::read(root().join("eval/golden/tinylm-tiny/tiny-int8-v3.sjtl")).unwrap();
    b[HASH_AT..HASH_AT + 32].copy_from_slice(&sha_bytes(lm));
    // (alpha, tau, delta offsets, level): chat and formal standard, chat conservative / aggressive, formal conservative / aggressive
    for (a, t, d, level) in [(36, 44, 100, 1), (52, 60, 108, 1), (116, 124, 132, 0), (140, 148, 156, 2), (164, 172, 180, 0), (188, 196, 204, 2)] {
        let alpha: f64 = if level == moving { 100.0 } else { 0.0 };
        b[a..a + 8].copy_from_slice(&alpha.to_le_bytes());
        b[t..t + 8].copy_from_slice(&f64::INFINITY.to_le_bytes());
        b[d..d + 8].copy_from_slice(&0f64.to_le_bytes());
    }
    let p = dir.join(format!("v3-{moving}.sjtl"));
    std::fs::write(&p, b).unwrap();
    p.to_string_lossy().into_owned()
}

/// The `top1_sha256` of a successful run's summary line.
fn top1_sha(o: &Output) -> String {
    assert!(o.status.success(), "{}", text(&o.stderr));
    let line = text(&o.stdout);
    line[line.find("'top1_sha256': '").expect("summary line") + 16..].chars().take(64).collect()
}

/// Rerank strength section 2.3: `--rerank-level` picks the file's set of that level. With the moving set at one level only, that
/// level changes the chosen candidates (top1 hash differs from the n-gram run) and the other two give exactly the n-gram run,
/// in both profiles.
#[test]
fn rerank_level_uses_that_levels_set() {
    let lm = lm_path();
    let (dir, _) = tmp_with_sjtl("level", Path::new(&lm));
    let names = ["conservative", "standard", "aggressive"];
    for profile in ["chat", "formal"] {
        let base = ["--lm", lm.as_str(), "--profile", profile, "--dev", "40"];
        let ngram = top1_sha(&run(&base));
        for moving in [0u32, 2] {
            let sjtl = v3_with_moving(&dir, Path::new(&lm), moving);
            for (level, name) in names.iter().enumerate() {
                let sha = top1_sha(&run(&[&base[..], &["--tinylm", &sjtl, "--rerank-level", name]].concat()));
                assert_eq!(sha == ngram, level as u32 != moving, "{profile}: moving set at {moving}, --rerank-level {name}");
            }
        }
        // the default is standard
        let sjtl = v3_with_moving(&dir, Path::new(&lm), 1);
        assert_ne!(top1_sha(&run(&[&base[..], &["--tinylm", &sjtl]].concat())), ngram, "{profile}: default standard");
    }
    std::fs::remove_dir_all(dir).unwrap();
}

/// Section 2.3 and 9 (P3-8): a level other than standard on a version 1 or 2 file exits with 2 and says why, before any row;
/// standard on it runs. Without `--tinylm`, or with an unknown name, it is an error too.
#[test]
fn rerank_level_refusals() {
    let lm = lm_path();
    let (dir, v1) = tmp_with_sjtl("level-refuse", Path::new(&lm));
    let base = ["--lm", lm.as_str(), "--profile", "chat", "--dev", "1"];
    for name in ["conservative", "aggressive"] {
        let o = run(&[&base[..], &["--tinylm", &v1, "--rerank-level", name]].concat());
        assert_eq!(o.status.code(), Some(2), "{name}: {}", text(&o.stderr));
        assert!(text(&o.stderr).contains("--rerank-level") && text(&o.stderr).contains("version") && o.stdout.is_empty());
        let o = run(&[&base[..], &["--rerank-level", name]].concat());
        assert!(!o.status.success() && text(&o.stderr).contains("needs --tinylm") && o.stdout.is_empty(), "{name}: {}", text(&o.stderr));
    }
    let o = run(&[&base[..], &["--tinylm", &v1, "--rerank-level", "standard"]].concat());
    assert!(o.status.success(), "{}", text(&o.stderr));
    let o = run(&[&base[..], &["--tinylm", &v1, "--rerank-level", "bold"]].concat());
    assert!(!o.status.success() && text(&o.stderr).contains("--rerank-level") && o.stdout.is_empty());
    std::fs::remove_dir_all(dir).unwrap();
}

/// The numbers of `"key": [a, b, ...]` in one `--tinylm-scores` line.
fn list_of(line: &str, key: &str) -> Vec<f64> {
    let at = line.find(&format!("\"{key}\": [")).unwrap() + key.len() + 5;
    let end = at + line[at..].find(']').unwrap();
    line[at..end].split(", ").map(|x| x.parse().unwrap()).collect()
}

/// tools/triage.py's two additions. `--dump-words` has, per row, rank 0 (the gold split) and then one line per `--dump` line, same
/// rows and ranks in the same order, whose words concatenate to that line's sentence. `--tinylm-scores` has `"c"`, the 0-based index
/// the rerank chose: `core::tinylm::pick` over the line's own `ng` and `ll`, and the sentence the run's rowstats judged (rank c + 1 of the dump).
#[test]
fn dump_words_align_with_dump_and_c_is_the_chosen_index() {
    let lm = lm_path();
    let (dir, sjtl) = tmp_with_sjtl("words", Path::new(&lm));
    let rows = dir.join("rows.txt");
    let src = std::fs::read_to_string(root().join("eval/dev/homophones.txt")).unwrap();
    std::fs::write(&rows, src.lines().filter(|l| l.split('|').count() == 3).take(40).collect::<Vec<_>>().join("\n") + "\n").unwrap();
    let p = |n: &str| dir.join(n).to_string_lossy().into_owned();
    let (dump, words, scores, stats) = (p("d.tsv"), p("w.tsv"), p("s.jsonl"), p("r.tsv"));
    let o = run(&["--lm", &lm, "--profile", "chat", "--rows", rows.to_str().unwrap(), "--tinylm", &sjtl, "--dump", &dump, "--dump-words", &words,
        "--tinylm-scores", &scores, "--rowstats", &stats]);
    assert!(o.status.success(), "{}", text(&o.stderr));
    let (dump, words) = (std::fs::read_to_string(&dump).unwrap(), std::fs::read_to_string(&words).unwrap());
    let w: Vec<Vec<&str>> = words.lines().map(|l| l.splitn(3, '\t').collect()).collect();
    let mut at = 0;
    let (mut nrows, mut rerank_moved, mut multi) = (0, 0, false);
    let scores = std::fs::read_to_string(&scores).unwrap();
    let stats = std::fs::read_to_string(&stats).unwrap();
    let len = core::eval::Lenient::parse(&std::fs::read_to_string(root().join("eval/variants.tsv")).unwrap()).unwrap();
    let gold: Vec<String> = std::fs::read_to_string(&rows).unwrap().lines().map(|l| l.split('|').nth(1).unwrap().to_string()).collect();
    let (a, t, d) = core::tinylm::TinyLm::load(Path::new(&sjtl)).unwrap().alpha_tau_delta(core::lm::Profile::Chat);
    for (i, (sline, stat)) in scores.lines().zip(stats.lines()).enumerate() {
        assert_eq!(w[at][..2], [(i + 1).to_string(), "0".to_string()], "rank 0 first");
        at += 1;
        let cands: Vec<Vec<&str>> = dump.lines().map(|l| l.split('\t').collect::<Vec<_>>()).filter(|f| f[0] == (i + 1).to_string()).collect();
        for (r, f) in cands.iter().enumerate() {
            assert_eq!(w[at][..2], [f[0].to_string(), f[1].to_string()], "rank {}", r + 1);
            assert_eq!(w[at][2].split(' ').collect::<String>(), f[2], "the words of a rank make its sentence");
            multi |= w[at][2].contains(' ');
            at += 1;
        }
        let c = sline[sline.find("\"c\": ").unwrap() + 5..].split(|ch| ch == ',' || ch == '}').next().unwrap().parse::<usize>().unwrap();
        // A row has `ll` only when the rerank ran (two or more candidates, none longer than the model's context); otherwise c is 0.
        if sline.contains("\"ll\": [") {
            assert_eq!(c, core::tinylm::pick(&list_of(sline, "ng"), &list_of(sline, "ll"), a, t, d), "row {}", i + 1);
        } else {
            assert_eq!(c, 0, "row {}: no ll means the rerank did not run, so c must be 0", i + 1);
        }
        let ok = len.apply(cands[c][2]) == len.apply(&gold[i]);
        assert_eq!(stat.split('\t').nth(1).unwrap(), if ok { "1" } else { "0" }, "row {}: rank c + 1 is the sentence the run judged", i + 1);
        rerank_moved += (c != 0) as usize;
        nrows += 1;
    }
    assert_eq!((nrows, at), (40, w.len()), "every row and every word line accounted for");
    assert!(multi, "words are separated by a space");
    assert!(rerank_moved > 0, "the toy model moves at least one row, or the c check proves nothing about c != 0");
    std::fs::remove_dir_all(dir).unwrap();
}

/// The holdout never gets sentences in a file: `--dump-words` is refused there like `--dump`.
#[test]
fn dump_words_refused_with_holdout() {
    let o = run(&["--lm", &lm_path(), "--profile", "chat", "--set", "holdout", "--dump-words", "x.tsv"]);
    assert!(!o.status.success() && text(&o.stderr).contains("holdout"), "{}", text(&o.stderr));
}
