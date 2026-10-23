//! Fuzzy input and the semantic correction on both sides (docs/contracts/fuzzy-input.md section 3.6, test (f)): the same input with
//! `--fuzzy 15` goes through the engine's rerank job and through `shanjie-eval --tinylm-scores`, and the candidates the small model is asked
//! about and the choice are the same. The toy small model of eval/golden/tinylm-tiny/ (alpha 100, tau infinite: its LLs decide, as
//! core/tests/engine_fuzzy_rerank.rs). Needs data/lm/bigram.sjlm.
use core::engine::*;
use std::process::Command;

// The helpers core/tests share (the toy model's patch, the n-gram model without its side file, the syllable keys); this crate's tests
// reach them by path, the repository root being the same one above either manifest.
#[path = "../../core/tests/common/toy.rs"]
mod toy;
use toy::{lm_path, sjtl_with_vocab};

/// The characters of the pools of the two inputs below (ㄙㄨ: 蘇酥甦穌疎踈囌䔫, ㄆㄧㄥˋ: 聘䀻), added to the toy's vocabulary: the toy reads
/// every other Han character as one unknown and gives all of them the same LL, so a choice among them would always be the first.
const VOCAB: &str = "蘇酥甦穌疎踈囌䔫聘䀻";

/// What the CLI says about one row: the dump's surfaces (n-best order), from the scores record `ng` count, `c`, `idx` and whether `ll` is
/// there, and from the rowstats whether the row's chosen candidate is the row's text.
struct CliRow {
    nbest: Vec<String>,
    n: usize,
    c: usize,
    idx: Vec<usize>,
    ll: bool,
    ok: bool,
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
/// The CLI on one row `|truth|syls` with the shipping-style flags (`--fuzzy flags` unless empty) and the toy small model.
fn cli_row(syls: &str, truth: &str, flags: &str) -> CliRow {
    let sjtl = sjtl_with_vocab(100.0, f64::INFINITY, Some(&lm_path()), VOCAB);
    let dir = std::env::temp_dir().join(format!("shanjie-cli-fuzzy-rerank-run-{}-{}", std::process::id(), RUN.fetch_add(1, std::sync::atomic::Ordering::Relaxed)));
    std::fs::create_dir_all(&dir).unwrap();
    let (rows, dump, scores, rowstats) = (dir.join("rows.txt"), dir.join("dump.tsv"), dir.join("scores.jsonl"), dir.join("rowstats.tsv"));
    std::fs::write(&rows, format!("|{truth}|{syls}\n")).unwrap();
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_shanjie-eval"));
    cmd.args(["--lm", lm_path().to_str().unwrap(), "--profile", "chat", "--rows", rows.to_str().unwrap(), "--tinylm", sjtl.to_str().unwrap()])
        .args(["--dump", dump.to_str().unwrap(), "--tinylm-scores", scores.to_str().unwrap(), "--rowstats", rowstats.to_str().unwrap()]);
    if !flags.is_empty() {
        cmd.args(["--fuzzy", flags]);
    }
    let o = cmd.output().unwrap();
    assert!(o.status.success(), "{}", String::from_utf8_lossy(&o.stderr));
    let nbest = std::fs::read_to_string(&dump).unwrap().lines().map(|l| l.split('\t').nth(2).unwrap().to_string()).collect();
    let line = std::fs::read_to_string(&scores).unwrap();
    let idx = if line.contains("\"idx\"") { ints(&field(&line, "idx")) } else { Vec::new() };
    // rowstats: row, ok, ... (numbers only)
    let ok = std::fs::read_to_string(&rowstats).unwrap().split('\t').nth(1).unwrap() == "1";
    let row = CliRow { nbest, n: field(&line, "ng").trim_matches(['[', ']']).split(", ").count(), c: field(&line, "c").parse().unwrap(), idx, ll: line.contains("\"ll\""), ok };
    std::fs::remove_dir_all(&dir).unwrap();
    row
}

/// What the engine's rerank job says about the same input: its candidates, the pool entry the model chose, and the text the composition
/// shows after the choice is applied (None: no job).
fn engine_job(syls: &str, flags: u32) -> Option<(Vec<String>, usize, String)> {
    let sjtl = sjtl_with_vocab(100.0, f64::INFINITY, Some(&lm_path()), VOCAB);
    let s = toy::shared();
    let mut e = Engine::with_lexicon(s.lex.clone(), Layout::Standard);
    e.set_lm(s.lm.clone(), s.capped.clone());
    e.load_tinylm(&sjtl).unwrap();
    e.set_rerank(true).unwrap();
    e.set_fuzzy(flags).unwrap();
    let typed = toy::type_syls(&mut e, &syls.split(' ').collect::<Vec<_>>());
    let job = e.take_rerank_job()?;
    let cands = job.candidates().remove(0);
    let result = job.run();
    let pick = result.picks()[0];
    let shown = e.apply_rerank(result).unwrap().map_or(typed.preedit, |o| o.preedit);
    Some((cands, pick, shown))
}

/// (f), the typed reading first with fuzzy paths among the top 8: the CLI's pool (`idx` into its dump) is the engine job's candidate list,
/// the scores record lists the pool (n is its length), and `c`, an index into the dump, is the pool entry the engine's choice names. The
/// toy model knows the pool's characters, so its choice is not the first and the pool index of it is not its n-best index (the pool
/// skips fuzzy paths before it): the engine's shown text, the CLI's `c` and the CLI's rowstats `ok` (the row's text is the engine's shown
/// text) all point to n-best[pool[pick]].
/// (f), a fuzzy path first: neither side reranks (no job; a record of one candidate, `c` 0, no `ll`).
#[test]
fn the_engine_and_the_cli_rerank_the_same_pool_and_choose_alike() {
    for syls in ["ㄙㄨ", "ㄆㄧㄥˋ"] {
        let (cands, pick, shown) = engine_job(syls, 15).expect("a job");
        let cli = cli_row(syls, &shown, "15");
        let pool: Vec<String> = cli.idx.iter().map(|&i| cli.nbest[i].clone()).collect();
        assert_eq!(pool, cands, "{syls}: the candidates");
        assert_eq!((cli.n, cli.ll), (cands.len(), true), "{syls}: the record lists the pool");
        assert!(cli.idx.len() < cli.nbest.len() && cli.idx != (0..cli.idx.len()).collect::<Vec<_>>(), "{syls}: premise: the pool skips a path");
        assert!(pick != 0 && cli.idx[pick] != pick, "{syls}: premise: the model leaves the first and its pool index is not its n-best index");
        assert_eq!(cli.c, cli.idx[pick], "{syls}: the choice, as an n-best index");
        assert_eq!(shown, cli.nbest[cli.c], "{syls}: the engine shows the path the CLI chose");
        assert!(cli.ok, "{syls}: the CLI's chosen candidate is the text the engine shows");
    }
    let cli = cli_row("ㄗ ㄉㄠˋ", "知道", "15");
    assert!(engine_job("ㄗ ㄉㄠˋ", 15).is_none(), "the engine makes no job");
    assert_eq!((cli.n, cli.c, cli.idx, cli.ll, cli.ok), (1, 0, vec![0], false, true), "the CLI's record is the n-gram first alone");
}

/// Without a sound bit nothing changes: no `idx`, the record lists the first 8 (or all, if fewer) of the n-best, and `c` is as ever.
#[test]
fn without_a_sound_bit_the_record_is_as_before() {
    for flags in ["", "0", "16"] {
        let cli = cli_row("ㄙㄨ", "蘇", flags);
        assert!(cli.idx.is_empty(), "flags {flags:?}: no idx column");
        assert_eq!(cli.n, cli.nbest.len().min(8), "flags {flags:?}");
    }
}
