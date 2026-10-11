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

/// Alpha and tau were tuned without the Kneser-Ney side file: with one beside the model, `--tinylm` refuses (and `--no-kn` runs).
#[test]
fn kneser_ney_side_file_is_refused() {
    let toy = root().join("eval/golden/kn-tiny");
    let (dir, sjtl) = tmp_with_sjtl("kn", &toy.join("bigram.sjlm"));
    for f in ["bigram.sjlm", "classes.sjc", "kn.sjkn"] {
        std::fs::copy(toy.join(f), dir.join(f)).unwrap();
    }
    let rows = dir.join("rows.txt");
    std::fs::write(&rows, "|你好|ㄋㄧˇ ㄏㄠˇ\n").unwrap();
    let lm = dir.join("bigram.sjlm");
    let base = ["--lm", lm.to_str().unwrap(), "--profile", "chat", "--rows", rows.to_str().unwrap(), "--tinylm", &sjtl];
    let refused = run(&base);
    assert!(!refused.status.success() && refused.stdout.is_empty());
    assert!(text(&refused.stderr).contains(".sjtl") && text(&refused.stderr).contains("Kneser-Ney"), "{}", text(&refused.stderr));
    let off = run(&[&base[..], &["--no-kn"]].concat());
    assert!(off.status.success(), "{}", text(&off.stderr));
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
