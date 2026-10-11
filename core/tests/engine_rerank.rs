//! S7c-2a engine tests (docs/contracts/s7c2a-core-rerank.md section 3, items 1, 2 and the harness of items 4 and 5): the
//! two-phase rerank driven synchronously (take, run, apply), on the real lexicon and n-gram model.
//!
//! The small model is the toy one of eval/golden/tinylm-tiny/ (tools/gen_tinylm_tiny.py), copied to a temporary file with
//! its recorded n-gram hash patched to data/lm/bigram.sjlm's (header offset 68, as cli/tests/tinylm.rs does) and alpha / tau
//! patched (offsets 36 / 44 chat, 52 / 60 formal): alpha 100 with tau infinite lets its LLs decide, so picks other than the
//! first happen (dev302 rows 12 and 27 below); tau 0 always keeps the first. Every expected choice is computed here from the
//! decoder and `TinyLm::rerank`, never read back from the engine. Needs data/lm/bigram.sjlm and fails loudly without it.
use core::engine::*;
use core::eval::{parse_rows, usable};
use core::learn::context_key;
use core::lm::{decode_segment_learned, CappedLexicon, Demote, End, Lm, Profile};
use core::tinylm::{TinyLm, TOPK};
use core::{Lexicon, Syls, BEAM_S1};
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};
use std::time::{Duration, Instant};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}
fn lm_path() -> PathBuf {
    let p = root().join("data/lm/bigram.sjlm");
    assert!(p.exists() && root().join("data/lm/classes.sjc").exists(), "data/lm/bigram.sjlm and classes.sjc are needed: see engine_lm.rs");
    p
}

struct Shared {
    lex: Arc<Lexicon>,
    lm: Arc<Lm>,
    capped: Arc<CappedLexicon>,
}
/// Loaded once per test binary. The capped lexicon has the demotion table, as `Engine::load_lm` builds it.
fn shared() -> &'static Shared {
    static S: OnceLock<Shared> = OnceLock::new();
    S.get_or_init(|| {
        let dir = root().join("data/lexicon");
        let lex = load_lexicon(&dir).unwrap();
        let lm = Lm::load(&lm_path()).unwrap();
        let overlay = std::fs::read_to_string(dir.join("overlay-add.tsv")).unwrap();
        let demote = Demote::parse(&std::fs::read_to_string(dir.join("demote.tsv")).unwrap()).unwrap();
        let capped = Arc::new(CappedLexicon::new(lex.clone(), &overlay, &lm, Some(&demote)).unwrap());
        Shared { lex, lm: Arc::new(lm), capped }
    })
}

fn sha_bytes(file: &Path) -> Vec<u8> {
    let h = core::eval::sha256_hex(&std::fs::read(file).unwrap());
    (0..32).map(|i| u8::from_str_radix(&h[2 * i..2 * i + 2], 16).unwrap()).collect()
}

/// The toy model with `alpha` / `tau` for both profiles and the n-gram hash of `lm` (None: left as recorded, i.e. kn-tiny's).
fn sjtl_for(alpha: f64, tau: f64, lm: Option<&Path>) -> PathBuf {
    let mut b = std::fs::read(root().join("eval/golden/tinylm-tiny/tiny-int8.sjtl")).unwrap();
    if let Some(lm) = lm {
        b[68..100].copy_from_slice(&sha_bytes(lm));
    }
    for at in [36, 52] {
        b[at..at + 8].copy_from_slice(&alpha.to_le_bytes());
        b[at + 8..at + 16].copy_from_slice(&tau.to_le_bytes());
    }
    let tag = format!("{alpha}-{tau}-{}", lm.is_some());
    let p = std::env::temp_dir().join(format!("shanjie-rerank-{}-{tag}.sjtl", std::process::id()));
    std::fs::write(&p, b).unwrap();
    p
}
/// Lets the toy model's LLs decide.
fn rerank_model() -> PathBuf {
    sjtl_for(100.0, f64::INFINITY, Some(&lm_path()))
}
/// tau 0: `pick` always keeps the first.
fn keep_model() -> PathBuf {
    sjtl_for(100.0, 0.0, Some(&lm_path()))
}

fn engine_with(model: Option<&Path>) -> Engine {
    let s = shared();
    let mut e = Engine::with_lexicon(s.lex.clone(), Layout::Standard);
    e.set_lm(s.lm.clone(), s.capped.clone());
    if let Some(m) = model {
        e.load_tinylm(m).unwrap();
    }
    e
}
fn engine() -> Engine {
    engine_with(Some(&rerank_model()))
}

fn keys_of(syl: &str) -> Vec<Key> {
    let layout = Layout::Standard;
    let mut v = Vec::new();
    let mut toned = false;
    for c in syl.chars() {
        match layout.key_of_tone(c) {
            Some(tk) => {
                v.push(Key::ch(tk, 0));
                toned = true;
            }
            None => v.push(Key::ch(layout.key_of_symbol(c).expect("symbol has a key"), 0)),
        }
    }
    if !toned {
        v.push(Key::new(KeyKind::Space));
    }
    v
}
fn k(kind: KeyKind) -> Key {
    Key::new(kind)
}

fn type_syls(e: &mut Engine, syls: &[String]) -> Output {
    let mut last = None;
    for syl in syls {
        for key in keys_of(syl) {
            last = Some(e.key(key).unwrap());
        }
    }
    last.unwrap()
}

/// The 302 rows of `--set dev --limit 302` as (sentence, reading), as engine_lm.rs reads them.
fn dev302() -> Vec<(String, Syls)> {
    let lex = &shared().lex;
    let mut files: Vec<PathBuf> = std::fs::read_dir(root().join("eval/dev"))
        .unwrap()
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.extension().is_some_and(|x| x == "txt"))
        .collect();
    files.sort();
    let mut rows = Vec::new();
    for f in files {
        rows.extend(parse_rows(&std::fs::read_to_string(f).unwrap()).unwrap());
    }
    let mut rows = usable(lex, rows);
    rows.truncate(302);
    assert!(rows.len() == 302, "dev set has 302 usable rows");
    rows.into_iter()
        .map(|r| {
            let syls = r.reading.clone().unwrap_or_else(|| lex.to_syllables(&r.sent).expect("usable row"));
            (r.sent, syls)
        })
        .collect()
}
fn row(n: usize) -> Syls {
    dev302().swap_remove(n - 1).1
}

/// The reference for one free stretch: its n-best's first TOPK surfaces and scores, and the model's choice.
struct Expect {
    surfaces: Vec<String>,
    ng: Vec<f64>,
    pick: usize,
}
fn expect(model: &Path, syls: &[String], start: &str, end: End<'_>, before: &str) -> Expect {
    let s = shared();
    let nb = decode_segment_learned(&s.capped, syls, &s.lm, Profile::Chat.lambda(), start, end, BEAM_S1, None, true).unwrap();
    let top = &nb[..nb.len().min(TOPK)];
    let surfaces: Vec<String> = top.iter().map(|(_, ws)| ws.iter().map(|w| w.0).collect()).collect();
    let ng: Vec<f64> = top.iter().map(|x| x.0).collect();
    let cands: Vec<&str> = surfaces.iter().map(String::as_str).collect();
    let pick = TinyLm::load(model).unwrap().rerank(&ng, &cands, &context_key(before), Profile::Chat).unwrap().0;
    Expect { surfaces, ng, pick }
}

/// take, run, apply.
fn cycle(e: &mut Engine) -> Option<Output> {
    let job = e.take_rerank_job().expect("a job");
    e.apply_rerank(job.run()).unwrap()
}

/// Type `syls`, put the cursor after syllable `end`, open the candidates and choose `word`; the snapshot after the choice.
fn fix_word(e: &mut Engine, syls: &[String], end: usize, word: &str) -> Output {
    type_syls(e, syls);
    for _ in 0..syls.len() - end {
        e.key(k(KeyKind::Left)).unwrap();
    }
    choose_at_cursor(e, word)
}
fn choose_at_cursor(e: &mut Engine, word: &str) -> Output {
    let mut o = e.key(k(KeyKind::Space)).unwrap();
    for _ in 0..5000 {
        if o.candidates[o.selected.unwrap()] == word {
            return e.key(k(KeyKind::Enter)).unwrap();
        }
        o = e.key(k(KeyKind::Right)).unwrap();
    }
    panic!("candidate not found");
}

// ---------- item 1 ----------

/// Item 1 and section 2.1: no model, or the switch off, never makes a job; with a model loaded every key's output is the same as
/// without one (the job only waits to be taken).
#[test]
fn no_model_or_switch_off_makes_no_job_and_keys_are_unchanged() {
    let syls = row(12);
    let (mut plain, mut with) = (engine_with(None), engine());
    let mut jobs = 0;
    for syl in &syls {
        for key in keys_of(syl) {
            assert!(plain.key(key).unwrap() == with.key(key).unwrap(), "a loaded model changed a key's output");
            assert!(plain.take_rerank_job().is_none(), "a job without a model");
            jobs += with.take_rerank_job().is_some() as usize;
        }
    }
    assert!(jobs > 0, "the model made jobs");
    let o = with.set_rerank(false).unwrap();
    assert!(o.preedit == plain.key(k(KeyKind::Left)).unwrap().preedit && with.take_rerank_job().is_none(), "off: no job");
    with.key(k(KeyKind::Right)).unwrap();
    assert!(with.take_rerank_job().is_none());
    with.set_rerank(true).unwrap();
    assert!(with.take_rerank_job().is_some(), "on again: set_rerank recomputes and makes one");
}

// ---------- item 2 ----------

/// After apply the display, the path and the prediction row are those of `cands[the model's choice]`: the total score of the
/// spliced path is that candidate's n-gram score, and the row equals the one recomputed from the new state (and differs from the
/// n-gram row, so an apply that skipped the recompute fails here).
#[test]
fn apply_shows_the_models_choice() {
    let model = rerank_model();
    let syls = row(12);
    let x = expect(&model, &syls, "<s>", End::Eos, "");
    assert!(x.pick != 0, "premise: the model changes row 12");
    let mut e = engine_with(Some(&model));
    let before = type_syls(&mut e, &syls);
    assert!(before.preedit == x.surfaces[0], "the key shows the n-gram first");
    let job = e.take_rerank_job().unwrap();
    assert_eq!(job.run().picks(), vec![x.pick]);
    let o = e.apply_rerank(job.run()).unwrap().expect("a change");
    assert!(o.preedit == x.surfaces[x.pick] && o.handled && o.commit.is_empty(), "display is the chosen candidate");
    assert!((e.total_score().unwrap() - x.ng[x.pick]).abs() < 1e-9, "path holds the chosen words with their lp and delta");
    let row = e.set_prediction(true).unwrap();
    assert!(o.candidates == row.candidates && o.selected.is_none(), "row recomputed from the new display");
    assert!(o.candidates != before.candidates, "premise: the prediction row depends on the shown text");
    assert!(e.key(k(KeyKind::Enter)).unwrap().commit == x.surfaces[x.pick], "Enter sends what is shown");
}

/// Stale results change nothing: a key (even one that does not refresh) or a setting after the take, and Enter before the apply
/// (it sends the shown n-gram text).
#[test]
fn stale_results_are_dropped() {
    let syls = row(12);
    let mut e = engine();
    let shown = type_syls(&mut e, &syls).preedit;
    let r = e.take_rerank_job().unwrap().run();
    assert!(e.key(k(KeyKind::Left)).unwrap().preedit == shown);
    assert!(e.apply_rerank(r).unwrap().is_none(), "a key after the take");
    e.key(k(KeyKind::Right)).unwrap();
    for change in [0, 1] {
        e.set_rerank(true).unwrap(); // a fresh job for the shown composition
        let r = e.take_rerank_job().unwrap().run();
        match change {
            0 => drop(e.set_candidate_vertical(false).unwrap()),
            _ => drop(e.set_profile(Profile::Chat).unwrap()),
        }
        assert!(e.apply_rerank(r).unwrap().is_none(), "a setting after the take");
    }
    e.set_rerank(true).unwrap();
    let r = e.take_rerank_job().unwrap().run();
    assert!(r.picks() != vec![0], "premise: this result would change the display");
    assert!(e.key(k(KeyKind::Enter)).unwrap().commit == shown, "Enter sends the shown text");
    assert!(e.apply_rerank(r).unwrap().is_none(), "applied after the commit");
    assert!(e.key(k(KeyKind::Left)).unwrap().preedit.is_empty());
}

/// Generations come from one process-wide counter: a result never applies to another engine.
#[test]
fn two_engines_do_not_share_generations() {
    let syls = row(12);
    let (mut a, mut b) = (engine(), engine());
    type_syls(&mut a, &syls);
    type_syls(&mut b, &syls);
    let ra = a.take_rerank_job().unwrap().run();
    let rb = b.take_rerank_job().unwrap().run();
    assert!(b.apply_rerank(ra).unwrap().is_none(), "a's result on b");
    assert!(b.apply_rerank(rb).unwrap().is_some(), "b's own result");
}

/// A result applies once: the same job run twice gives two results, and the second is stale after the first applied. The job
/// owns its data, so it runs after the engine is gone.
#[test]
fn a_job_applies_once_and_outlives_the_engine() {
    let syls = row(12);
    let mut e = engine();
    type_syls(&mut e, &syls);
    let job = e.take_rerank_job().unwrap();
    assert!(e.take_rerank_job().is_none(), "take hands the job out once");
    let (r1, r2) = (job.run(), job.run());
    let o = e.apply_rerank(r1).unwrap().expect("first apply");
    assert!(e.apply_rerank(r2).unwrap().is_none(), "second apply");
    assert!(e.key(k(KeyKind::Left)).unwrap().preedit == o.preedit, "the second apply changed nothing");
    e.key(k(KeyKind::Right)).unwrap();
    e.set_rerank(true).unwrap();
    let job = e.take_rerank_job().unwrap();
    drop(e);
    assert!(job.run().picks() != vec![0]);
}

/// Every stretch keeps its first: no output, no change.
#[test]
fn all_first_picks_change_nothing() {
    let syls = row(12);
    let mut e = engine_with(Some(&keep_model()));
    let shown = type_syls(&mut e, &syls).preedit;
    let r = e.take_rerank_job().expect("a job (the stretch has candidates)").run();
    assert_eq!(r.picks(), vec![0]);
    assert!(e.apply_rerank(r).unwrap().is_none());
    assert!(e.key(k(KeyKind::Left)).unwrap().preedit == shown);
}

/// Two free stretches around a fixed word are both reranked with their own ends and keys (section 2.2); the fixed word stays.
#[test]
fn fixed_word_between_two_stretches() {
    let model = rerank_model();
    let s = shared();
    let syls = row(27);
    let (a, b, word) = (7, 9, "郵件");
    let (lp, delta) = s.capped.best_lp_delta(&syls[a..b], word).unwrap();
    let left = expect(&model, &syls[..a], "<s>", End::Next { word, lp, delta }, "");
    let right = expect(&model, &syls[b..], word, End::Eos, &format!("{}{word}", left.surfaces[0]));
    assert!(left.pick != 0 && right.pick != 0, "premise: both stretches change");
    let mut e = engine_with(Some(&model));
    let fixed = fix_word(&mut e, &syls, b, word);
    assert!(fixed.preedit == format!("{}{word}{}", left.surfaces[0], right.surfaces[0]));
    let job = e.take_rerank_job().unwrap();
    assert_eq!(job.run().picks(), vec![left.pick, right.pick]);
    let o = e.apply_rerank(job.run()).unwrap().unwrap();
    assert!(o.preedit == format!("{}{word}{}", left.surfaces[left.pick], right.surfaces[right.pick]), "both reranked, word kept");
    assert!((e.total_score().unwrap() - (left.ng[left.pick] + right.ng[right.pick])).abs() < 1e-9, "both ranges spliced");
    assert!(e.set_rerank(false).unwrap().preedit == fixed.preedit, "off: the n-gram stretches around the same fixed word");
}

/// The text a candidate pick remembers as displaced (`pre`) is the reranked text on screen: picking exactly that word teaches
/// nothing. Control: the same pick over the n-gram text teaches.
#[test]
fn pre_after_apply_is_the_reranked_text() {
    let model = rerank_model();
    let syls = row(12);
    let x = expect(&model, &syls, "<s>", End::Eos, "");
    let mut e = engine_with(Some(&model));
    e.set_learning(true);
    type_syls(&mut e, &syls);
    let shown = cycle(&mut e).unwrap().preedit;
    let last = *e.path_starts().last().unwrap();
    let word: String = shown.chars().skip(last).collect();
    let ngram: String = x.surfaces[0].chars().skip(last).collect();
    // A Han character before it, so even a single character is learnable (the control below shows it is).
    assert!(word != ngram && last > 0, "premise: the last word changed and has a Han character before it");
    choose_at_cursor(&mut e, &word);
    assert!(!e.key(k(KeyKind::Enter)).unwrap().commit.is_empty());
    assert!(e.learner().is_empty(), "the pick displaced nothing");
    let mut c = engine_with(Some(&model));
    c.set_learning(true);
    type_syls(&mut c, &syls);
    choose_at_cursor(&mut c, &word);
    c.key(k(KeyKind::Enter)).unwrap();
    assert!(!c.learner().is_empty(), "control: over the n-gram text the same pick teaches");
}

/// Section 2.3: the 40-syllable auto-commit voids the job of the refresh just before it, both for a key and for a prediction pick
/// that reaches 40.
#[test]
fn auto_commit_voids_the_job() {
    let ni: Syls = vec!["ㄋㄧˇ".to_string(); 39];
    let mut e = engine();
    type_syls(&mut e, &ni);
    let r = e.take_rerank_job().expect("a job at 39").run();
    let mut o = None;
    for key in keys_of("ㄋㄧˇ") {
        o = Some(e.key(key).unwrap());
    }
    assert!(!o.unwrap().commit.is_empty(), "premise: the 40th syllable commits");
    assert!(e.take_rerank_job().is_none(), "no job after the auto-commit");
    assert!(e.apply_rerank(r).unwrap().is_none());
    // A prediction pick: 39 syllables, then ㄋ shows a row of words for it; picking one makes 40.
    type_syls(&mut e, &ni);
    let row = e.key(Key::ch('s', 0)).unwrap();
    assert!(!row.candidates.is_empty() && row.selected.is_none(), "premise: a prediction row");
    let o = e.pick(0).unwrap().unwrap();
    assert!(!o.commit.is_empty(), "premise: the pick reached 40 and committed");
    assert!(e.take_rerank_job().is_none(), "no job after the prediction pick's commit");
}

/// Section 2.2: with the demotion table off there are no jobs (alpha and tau hold only with it on).
#[test]
fn demote_off_makes_no_job() {
    let syls = row(12);
    let mut e = engine();
    e.set_demote(false).unwrap();
    type_syls(&mut e, &syls);
    assert!(e.take_rerank_job().is_none());
    e.set_demote(true).unwrap();
    assert!(e.take_rerank_job().is_some(), "control: on again");
}

/// Section 2.3: a new n-gram model drops the small one (its `.sjtl` names the previous model), through `set_lm` and through
/// a successful `load_lm`; a failed `load_lm` keeps it.
#[test]
fn new_ngram_model_drops_the_small_model() {
    let s = shared();
    let syls = row(12);
    let mut e = engine();
    e.set_lm(s.lm.clone(), s.capped.clone());
    type_syls(&mut e, &syls);
    assert!(e.take_rerank_job().is_none(), "set_lm");

    let mut e = Engine::new(&root().join("data/lexicon"), Layout::Standard).unwrap();
    e.load_lm(&lm_path()).unwrap();
    e.load_tinylm(&rerank_model()).unwrap();
    assert!(e.load_lm(Path::new("/nonexistent/shanjie.sjlm")).is_err());
    type_syls(&mut e, &syls);
    assert!(e.take_rerank_job().is_some(), "a failed load_lm keeps the model");
    e.key(k(KeyKind::Esc)).unwrap();
    e.load_lm(&lm_path()).unwrap();
    type_syls(&mut e, &syls);
    assert!(e.take_rerank_job().is_none(), "load_lm");
}

/// Section 2.4: with the prediction row entered (⌘⌫ there re-decodes and makes a job while the row stays entered) apply changes
/// nothing and the selection stays.
#[test]
fn entered_prediction_row_blocks_apply() {
    let syls = row(12);
    let mut e = engine();
    type_syls(&mut e, &syls);
    assert!(e.key(k(KeyKind::Tab)).unwrap().selected == Some(0), "premise: the row is entered");
    e.key(k(KeyKind::Right)).unwrap();
    let o = e.key(Key { kind: KeyKind::Backspace, ch: '\0', modifiers: MOD_COMMAND }).unwrap();
    let sel = o.selected;
    assert!(sel.is_some(), "premise: still entered after the forget");
    let r = e.take_rerank_job().expect("the forget re-decoded").run();
    assert!(r.picks() != vec![0], "premise: this result would change the display");
    assert!(e.apply_rerank(r).unwrap().is_none());
    let snap = e.set_candidate_vertical(false).unwrap();
    assert!(snap.selected == sel && snap.preedit == o.preedit && snap.candidates == o.candidates, "row and selection unchanged");
}

/// Section 2.2, the learning guard: a stretch whose first path has a learned-boosted word is left out, the other stretch is still
/// reranked. Taught here: 回覆 for ㄏㄨㄟˊ ㄈㄨˋ after 已經 (row 27 shows 回復 by the n-gram); the fixed 郵件 splits the row.
#[test]
fn learned_stretch_is_not_reranked() {
    let syls = row(27);
    let mut e = engine();
    e.set_learning(true);
    fix_word(&mut e, &syls, 5, "回覆");
    e.key(k(KeyKind::Enter)).unwrap();
    assert!(!e.learner().is_empty(), "premise: taught");
    let o = fix_word(&mut e, &syls, 9, "郵件");
    assert!(o.preedit.starts_with("我已經回覆你的郵件"), "premise: the learned word is in the first path");
    let job = e.take_rerank_job().unwrap();
    let picks = job.run().picks();
    assert!(picks.len() == 1 && picks[0] != 0, "only the right stretch, and it changes");
    let o = e.apply_rerank(job.run()).unwrap().unwrap();
    assert!(o.preedit.starts_with("我已經回覆你的郵件"), "the taught stretch is untouched");
    // Control: without the teach the same fix reranks both stretches (fixed_word_between_two_stretches).
}

/// Section 2.5: load errors keep the previous state (none or the old model); `load_tinylm` never touches the switch.
#[test]
fn load_tinylm_errors_and_the_switch() {
    let s = shared();
    let syls = row(12);
    // No n-gram model.
    let mut e = Engine::with_lexicon(s.lex.clone(), Layout::Standard);
    assert!(e.load_tinylm(&rerank_model()).is_err(), "no n-gram model");
    // A missing file, another model's hash, a directory: errors, and no model afterwards.
    let mut e = engine_with(None);
    for bad in [PathBuf::from("/nonexistent/x.sjtl"), sjtl_for(100.0, f64::INFINITY, None), std::env::temp_dir()] {
        assert!(e.load_tinylm(&bad).is_err());
    }
    type_syls(&mut e, &syls);
    assert!(e.take_rerank_job().is_none(), "failed loads leave no model");
    e.key(k(KeyKind::Esc)).unwrap();
    // A failed load after a good one keeps the good one.
    e.load_tinylm(&rerank_model()).unwrap();
    assert!(e.load_tinylm(Path::new("/nonexistent/x.sjtl")).is_err());
    type_syls(&mut e, &syls);
    assert!(e.take_rerank_job().is_some(), "old model kept");
    e.key(k(KeyKind::Esc)).unwrap();
    // The switch survives a load.
    e.set_rerank(false).unwrap();
    e.load_tinylm(&rerank_model()).unwrap();
    type_syls(&mut e, &syls);
    assert!(e.take_rerank_job().is_none(), "load_tinylm left the switch off");
}

/// Section 2.5: the Kneser-Ney side file on refuses the model (alpha and tau were tuned without it); the same model without
/// the side file is accepted (eval/golden/kn-tiny, with a one-entry lexicon: only the model's hash and KN state matter here).
#[test]
fn kneser_ney_side_file_refuses_the_model() {
    let kn = root().join("eval/golden/kn-tiny");
    let dir = std::env::temp_dir().join(format!("shanjie-rerank-kn-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    for f in ["bigram.sjlm", "classes.sjc"] {
        std::fs::copy(kn.join(f), dir.join(f)).unwrap();
    }
    let lex = Arc::new(Lexicon::parse_with("ㄋㄧˇ 你 -1.0\n", None).unwrap());
    let model = sjtl_for(0.7, 2.0, Some(&kn.join("bigram.sjlm")));
    for (lm_dir, ok) in [(kn.clone(), false), (dir.clone(), true)] {
        let lm = Lm::load(&lm_dir.join("bigram.sjlm")).unwrap();
        assert_eq!(lm.kn_tag().is_some(), !ok, "premise: the side file is loaded only from kn-tiny");
        let capped = Arc::new(CappedLexicon::new(lex.clone(), "", &lm, None).unwrap());
        let mut e = Engine::with_lexicon(lex.clone(), Layout::Standard);
        e.set_lm(Arc::new(lm), capped);
        assert_eq!(e.load_tinylm(&model).is_ok(), ok);
    }
    std::fs::remove_dir_all(dir).unwrap();
}

// ---------- items 4 and 5: run by the main session ----------

/// Item 4 (parity) and item 5 (measurements). Skipped unless SHANJIE_TINYLM names the real `.sjtl` (never copied into data/lm).
/// The engine is built as the contract says: `Engine::new(data/lexicon)` + `load_lm(data/lm/bigram.sjlm)` + `load_tinylm`, chat,
/// demotion on, no left context. Every dev302 row is typed key by key with take, run and apply after every key, then Enter.
/// The commits are compared with SHANJIE_TINYLM_FIRSTS (302 lines, row order: the CLI's chosen candidate of
/// `--lm data/lm/bigram.sjlm --profile chat --dev 302 --tinylm <.sjtl>`), and always with the CLI's rule computed here
/// (`decode_from` + `TinyLm::rerank`, as cli/src/main.rs does it). Prints to stderr: key-path p50 / p95 (key plus take, so the
/// job's creation is inside), run p50 / p95, CPU / wall time of the runs (process CPU from `clock`, so run this test alone),
/// jobs, changed applies, keys, and the A4 measure.
///
/// A4 measure (section 1): a key "reverted then changed back" when some character position that the previous key's apply had
/// changed (the shown text after key k-1 differs there from key k-1's n-gram text) shows something else in key k's n-gram text
/// and the shown text after key k's apply is again what it was after key k-1.
#[test]
fn replay_dev302_parity_and_measurements() {
    let Some(model) = std::env::var_os("SHANJIE_TINYLM") else {
        eprintln!("SKIPPED replay_dev302_parity_and_measurements: set SHANJIE_TINYLM to the .sjtl (and SHANJIE_TINYLM_FIRSTS to the CLI firsts)");
        return;
    };
    extern "C" {
        fn clock() -> std::ffi::c_ulong;
    }
    const CLOCKS_PER_SEC: f64 = 1_000_000.0; // macOS
    let mut e = Engine::new(&root().join("data/lexicon"), Layout::Standard).unwrap();
    e.load_lm(&lm_path()).unwrap();
    e.load_tinylm(Path::new(&model)).unwrap();
    let rows = dev302();
    // The CLI's rule in process.
    let s = shared();
    let tl = TinyLm::load(Path::new(&model)).unwrap();
    let rule: Vec<String> = rows
        .iter()
        .map(|(_, syls)| {
            let nb = core::lm::decode_from(&s.capped, syls, &s.lm, Profile::Chat, BEAM_S1, "<s>", true).unwrap();
            let surf: Vec<String> = nb.iter().take(TOPK).map(|(_, ws)| ws.concat()).collect();
            let ng: Vec<f64> = nb.iter().take(TOPK).map(|x| x.0).collect();
            let top: Vec<&str> = surf.iter().map(String::as_str).collect();
            let pick = tl.rerank(&ng, &top, "", Profile::Chat).map_or(0, |r| r.0);
            surf[pick].clone()
        })
        .collect();
    let han = |o: &Output| -> Vec<char> {
        // The cursor is at the end, so the unfinished symbols are the tail of the preedit.
        let mut v: Vec<char> = o.preedit.chars().collect();
        while v.last().is_some_and(|c| ('\u{3105}'..='\u{3129}').contains(c) || "ˊˇˋ˙".contains(*c)) {
            v.pop();
        }
        v
    };
    let (mut keys, mut runs, mut got) = (Vec::new(), Vec::new(), Vec::new());
    let (mut jobs, mut changed, mut back, mut cpu, mut wall) = (0usize, 0usize, 0usize, 0u64, Duration::ZERO);
    for (_, syls) in &rows {
        let mut list: Vec<Key> = syls.iter().flat_map(|s| keys_of(s)).collect();
        list.push(k(KeyKind::Enter));
        let mut commit = String::new();
        let (mut prev_ng, mut prev_shown): (Vec<char>, Vec<char>) = (Vec::new(), Vec::new());
        for key in list {
            let t = Instant::now();
            let o = e.key(key).unwrap();
            let job = e.take_rerank_job();
            keys.push(t.elapsed());
            commit.push_str(&o.commit);
            let ng = han(&o);
            let mut shown = ng.clone();
            if let Some(job) = job {
                jobs += 1;
                let (c0, t) = (unsafe { clock() }, Instant::now());
                let r = job.run();
                runs.push(t.elapsed());
                wall += t.elapsed();
                cpu += (unsafe { clock() } - c0) as u64;
                if let Some(a) = e.apply_rerank(r).unwrap() {
                    changed += 1;
                    shown = han(&a);
                }
            }
            // Only a key that completes a syllable (or commits) re-decodes; a symbol key shows the previous text unchanged.
            if ng.len() != prev_shown.len() {
                let n = prev_shown.len().min(prev_ng.len()).min(ng.len()).min(shown.len());
                back += (0..n).any(|i| prev_shown[i] != prev_ng[i] && ng[i] != prev_shown[i] && shown[i] == prev_shown[i]) as usize;
                (prev_ng, prev_shown) = (ng, shown);
            }
        }
        got.push(commit);
    }
    let diff_rule: Vec<usize> = (0..rows.len()).filter(|&i| got[i] != rule[i]).map(|i| i + 1).collect();
    let pct = |mut t: Vec<Duration>, p: usize| {
        t.sort();
        t[(t.len() * p / 100).min(t.len() - 1)]
    };
    eprintln!(
        "rerank replay: keys {} jobs {jobs} changed {changed} reverted-then-back {back} ({:.2}% of keys); key path p50 {:?} p95 {:?}; run n {} p50 {:?} p95 {:?}; run cpu/wall {:.2}; rows differing from the CLI rule {:?}",
        keys.len(),
        100.0 * back as f64 / keys.len() as f64,
        pct(keys.clone(), 50),
        pct(keys.clone(), 95),
        runs.len(),
        pct(runs.clone(), 50),
        pct(runs.clone(), 95),
        cpu as f64 / CLOCKS_PER_SEC / wall.as_secs_f64(),
        diff_rule
    );
    // Item 5's baseline: the same keys on an engine without the small model, for "job creation adds at most 1 ms to key p95".
    let mut b = Engine::new(&root().join("data/lexicon"), Layout::Standard).unwrap();
    b.load_lm(&lm_path()).unwrap();
    let mut base = Vec::new();
    for (_, syls) in &rows {
        for key in syls.iter().flat_map(|s| keys_of(s)).chain([k(KeyKind::Enter)]) {
            let t = Instant::now();
            b.key(key).unwrap();
            assert!(b.take_rerank_job().is_none());
            base.push(t.elapsed());
        }
    }
    eprintln!("baseline (no small model): key path p50 {:?} p95 {:?}", pct(base.clone(), 50), pct(base, 95));
    let firsts = std::env::var_os("SHANJIE_TINYLM_FIRSTS").expect("SHANJIE_TINYLM_FIRSTS: the CLI firsts file is needed for item 4");
    let want: Vec<String> = std::fs::read_to_string(firsts).unwrap().lines().map(String::from).collect();
    assert_eq!(want.len(), 302, "302 CLI firsts");
    let diff: Vec<usize> = (0..302).filter(|&i| got[i] != want[i]).map(|i| i + 1).collect();
    assert!(diff.is_empty() && diff_rule.is_empty(), "replay differs from the CLI at rows {diff:?} (rule: {diff_rule:?})");
}
