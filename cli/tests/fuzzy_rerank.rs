//! Fuzzy input and the semantic correction on both sides (docs/contracts/fuzzy-input.md section 3.6, test (f)): the same input with
//! `--fuzzy 15` goes through the engine's rerank job and through `shanjie-eval --tinylm-scores`, and the candidates the small model is asked
//! about and the choice are the same. The toy small model of eval/golden/tinylm-tiny/ (alpha 100, tau infinite: its LLs decide, as
//! core/tests/engine_fuzzy_rerank.rs). Needs data/lm/bigram.sjlm.
use core::engine::*;
use core::lm::{CappedLexicon, Demote, Lm};
use std::path::{Path, PathBuf};
use std::process::Command;

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..")
}

/// data/lm/bigram.sjlm and classes.sjc symlinked into a temporary directory without kn.sjkn (the toy is a version 1 file), and the toy
/// model beside them with that model's hash and alpha / tau patched. Returns (n-gram model, small model).
// ponytail: the directory (two symlinks and the toy file) is left in the temp dir; a OnceLock never drops.
fn files() -> (PathBuf, PathBuf) {
    static F: std::sync::OnceLock<(PathBuf, PathBuf)> = std::sync::OnceLock::new();
    F.get_or_init(make_files).clone()
}
fn make_files() -> (PathBuf, PathBuf) {
    let real = root().join("data/lm");
    assert!(real.join("bigram.sjlm").exists() && real.join("classes.sjc").exists(), "data/lm/bigram.sjlm and classes.sjc are needed: see tests/golden.rs");
    let d = std::env::temp_dir().join(format!("shanjie-cli-fuzzy-rerank-{}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    for f in ["bigram.sjlm", "classes.sjc"] {
        let _ = std::fs::remove_file(d.join(f));
        std::os::unix::fs::symlink(real.join(f), d.join(f)).unwrap();
    }
    let lm = d.join("bigram.sjlm");
    let mut b = std::fs::read(root().join("eval/golden/tinylm-tiny/tiny-int8.sjtl")).unwrap();
    let h = core::eval::sha256_hex(&std::fs::read(&lm).unwrap());
    let sha: Vec<u8> = (0..32).map(|i| u8::from_str_radix(&h[2 * i..2 * i + 2], 16).unwrap()).collect();
    b[68..100].copy_from_slice(&sha);
    for at in [36, 52] {
        b[at..at + 8].copy_from_slice(&100.0f64.to_le_bytes());
        b[at + 8..at + 16].copy_from_slice(&f64::INFINITY.to_le_bytes());
    }
    std::fs::write(d.join("tiny.sjtl"), b).unwrap();
    (lm, d.join("tiny.sjtl"))
}

/// What the CLI says about one row: the dump's surfaces (n-best order), and from the scores record `ng` count, `c`, `idx` and whether `ll` is there.
struct CliRow {
    nbest: Vec<String>,
    n: usize,
    c: usize,
    idx: Vec<usize>,
    ll: bool,
}
fn field(line: &str, key: &str) -> String {
    let at = line.find(&format!("\"{key}\": ")).unwrap() + key.len() + 4;
    let rest = &line[at..];
    let end = if rest.starts_with('[') { rest.find(']').unwrap() + 1 } else { rest.find([',', '}']).unwrap() };
    rest[..end].to_string()
}
fn ints(v: &str) -> Vec<usize> {
    v.trim_matches(['[', ']']).split(", ").filter(|x| !x.is_empty()).map(|x| x.parse().unwrap()).collect()
}
static RUN: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
fn cli_row(syls: &str, flags: &str) -> CliRow {
    let (lm, sjtl) = files();
    let dir = std::env::temp_dir().join(format!("shanjie-cli-fuzzy-rerank-run-{}-{}", std::process::id(), RUN.fetch_add(1, std::sync::atomic::Ordering::Relaxed)));
    std::fs::create_dir_all(&dir).unwrap();
    let (rows, dump, scores) = (dir.join("rows.txt"), dir.join("dump.tsv"), dir.join("scores.jsonl"));
    std::fs::write(&rows, format!("|x|{syls}\n")).unwrap();
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_shanjie-eval"));
    cmd.args(["--lm", lm.to_str().unwrap(), "--profile", "chat", "--rows", rows.to_str().unwrap(), "--tinylm", sjtl.to_str().unwrap()])
        .args(["--dump", dump.to_str().unwrap(), "--tinylm-scores", scores.to_str().unwrap()]);
    if !flags.is_empty() {
        cmd.args(["--fuzzy", flags]);
    }
    let o = cmd.output().unwrap();
    assert!(o.status.success(), "{}", String::from_utf8_lossy(&o.stderr));
    let nbest = std::fs::read_to_string(&dump).unwrap().lines().map(|l| l.split('\t').nth(2).unwrap().to_string()).collect();
    let line = std::fs::read_to_string(&scores).unwrap();
    let idx = if line.contains("\"idx\"") { ints(&field(&line, "idx")) } else { Vec::new() };
    let row = CliRow { nbest, n: field(&line, "ng").trim_matches(['[', ']']).split(", ").count(), c: field(&line, "c").parse().unwrap(), idx, ll: line.contains("\"ll\"") };
    std::fs::remove_dir_all(&dir).unwrap();
    row
}

/// What the engine's rerank job says about the same input: its candidates and the pool entry the model chose (None: no job).
fn engine_job(syls: &str, flags: u32, sjtl: &Path, lm: &Path) -> Option<(Vec<String>, usize)> {
    let dir = root().join("data/lexicon");
    let lex = load_lexicon(&dir).unwrap();
    let lm = std::sync::Arc::new(Lm::load(lm).unwrap());
    let demote = Demote::parse(&std::fs::read_to_string(dir.join("demote.tsv")).unwrap()).unwrap();
    let capped = std::sync::Arc::new(CappedLexicon::new(lex.clone(), &capping_overlay(&dir, "").unwrap(), &lm, Some(&demote)).unwrap());
    let mut e = Engine::with_lexicon(lex, Layout::Standard);
    e.set_lm(lm, capped);
    e.load_tinylm(sjtl).unwrap();
    e.set_rerank(true).unwrap();
    e.set_fuzzy(flags).unwrap();
    let layout = Layout::Standard;
    for syl in syls.split(' ') {
        let mut toned = false;
        for c in syl.chars() {
            let key = match layout.key_of_tone(c) {
                Some(tk) => {
                    toned = true;
                    tk
                }
                None => layout.key_of_symbol(c).unwrap(),
            };
            e.key(Key::ch(key, 0)).unwrap();
        }
        if !toned {
            e.key(Key::new(KeyKind::Space)).unwrap();
        }
    }
    let job = e.take_rerank_job()?;
    let cands = job.candidates().remove(0);
    Some((cands, job.run().picks()[0]))
}

/// (f), the typed reading first with fuzzy paths among the top 8: the CLI's pool (`idx` into its dump) is the engine job's candidate list,
/// the scores record lists the pool (n is its length), and `c`, an index into the dump, is the pool entry the engine's choice names.
/// (f), a fuzzy path first: neither side reranks (no job; a record of one candidate, `c` 0, no `ll`).
#[test]
fn the_engine_and_the_cli_rerank_the_same_pool_and_choose_alike() {
    let (lm, sjtl) = files();
    for syls in ["ㄙㄨ", "ㄆㄧㄥˋ"] {
        let cli = cli_row(syls, "15");
        let (cands, pick) = engine_job(syls, 15, &sjtl, &lm).expect("a job");
        let pool: Vec<String> = cli.idx.iter().map(|&i| cli.nbest[i].clone()).collect();
        assert_eq!(pool, cands, "{syls}: the candidates");
        assert_eq!((cli.n, cli.ll), (cands.len(), true), "{syls}: the record lists the pool");
        assert_eq!(cli.c, cli.idx[pick], "{syls}: the choice, as an n-best index");
        assert!(cli.idx.len() < cli.nbest.len() && cli.idx != (0..cli.idx.len()).collect::<Vec<_>>(), "{syls}: premise: the pool skips a path");
    }
    let cli = cli_row("ㄗ ㄉㄠˋ", "15");
    assert!(engine_job("ㄗ ㄉㄠˋ", 15, &sjtl, &lm).is_none(), "the engine makes no job");
    assert_eq!((cli.n, cli.c, cli.idx, cli.ll), (1, 0, vec![0], false), "the CLI's record is the n-gram first alone");
}

/// Without a sound bit nothing changes: no `idx`, the record lists the first 8 (or all, if fewer) of the n-best, and `c` is as ever.
#[test]
fn without_a_sound_bit_the_record_is_as_before() {
    for flags in ["", "0", "16"] {
        let cli = cli_row("ㄙㄨ", flags);
        assert!(cli.idx.is_empty(), "flags {flags:?}: no idx column");
        assert_eq!(cli.n, cli.nbest.len().min(8), "flags {flags:?}");
    }
}
