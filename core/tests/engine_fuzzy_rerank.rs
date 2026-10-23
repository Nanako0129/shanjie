//! Fuzzy input and the semantic correction (docs/contracts/fuzzy-input.md section 3.6, tests (d) and (e); (a)-(c) are in core/src/lm.rs,
//! (f) in cli/tests/fuzzy_rerank.rs). The engine with the real lexicon and n-gram model, the demotion table on, and the toy small model of
//! eval/golden/tinylm-tiny/ patched like engine_rerank.rs does (alpha 100, tau infinite: its LLs decide). Needs data/lm/bigram.sjlm.
//! The toy reads most Han characters as one unknown, so the candidates of a test that wants the model to choose have their characters added
//! to its vocabulary (`sjtl_with_vocab`); with the toy as it is, every candidate gets the same LL and the choice is always the first.
use core::engine::*;
use core::learn::context_key;
use core::lm::{decode_from_fuzzy_scored, Profile};
use core::tinylm::TinyLm;
use core::BEAM_S1;
use std::path::Path;

#[path = "common/toy.rs"]
mod toy;
use toy::*;

/// An engine with the n-gram model, the toy small model `model` and the rerank on, and the fuzzy `flags`.
fn engine_with(flags: u32, model: &Path) -> Engine {
    let s = shared();
    let mut e = Engine::with_lexicon(s.lex.clone(), Layout::Standard);
    e.set_lm(s.lm.clone(), s.capped.clone());
    e.load_tinylm(model).unwrap();
    e.set_rerank(true).unwrap();
    e.set_fuzzy(flags).unwrap();
    e
}

/// `engine_with` the toy model as it is (see the head of this file): for the tests that do not need the model to choose.
fn engine(flags: u32) -> Engine {
    engine_with(flags, &sjtl_for(100.0, f64::INFINITY, Some(&lm_path())))
}

/// The decoder's n-best of `syls` as the engine decodes a free composition: (surface, has a fuzzy word, n-gram score).
fn nbest(syls: &[&str], flags: u32) -> Vec<(String, bool, f64)> {
    let s = shared();
    let v: Vec<String> = syls.iter().map(|x| x.to_string()).collect();
    decode_from_fuzzy_scored(&s.capped, &v, &s.lm, Profile::Chat, BEAM_S1, "<s>", true, flags, core::fuzzy::DELTA_SOUND)
        .unwrap()
        .into_iter()
        .map(|(sc, ws)| (ws.iter().map(|w| w.0).collect(), ws.iter().any(|w| w.4), sc))
        .collect()
}

/// A free composition whose n-gram first path is the typed reading, with the pool the rule makes (restated here, not read from the core),
/// a toy model that knows the pool's characters, and what `TinyLm::rerank` picks among the pool: an index into the pool.
struct Pooled {
    nb: Vec<(String, bool, f64)>,
    pool: Vec<usize>,
    model: TempFile,
    pick: usize,
}
fn pooled(syls: &[&str], flags: u32) -> Pooled {
    let nb = nbest(syls, flags);
    assert!(!nb[0].1, "premise: the n-gram first path is the typed reading");
    let pool: Vec<usize> = nb.iter().enumerate().filter(|(_, p)| !p.1).map(|(i, _)| i).take(8).collect();
    let cands: Vec<&str> = pool.iter().map(|&i| nb[i].0.as_str()).collect();
    let model = sjtl_with_vocab(100.0, f64::INFINITY, Some(&lm_path()), &cands.concat());
    let tl = TinyLm::load(&model).unwrap();
    let ng: Vec<f64> = pool.iter().map(|&i| nb[i].2).collect();
    let pick = tl.rerank(&ng, &cands, &context_key(""), tl.alpha_tau_delta(Profile::Chat)).unwrap().0;
    Pooled { nb, pool, model, pick }
}

/// Section 3.6 (d): the n-gram first path reads the typed syllable and fuzzy-sound paths stand among the first 8 of the n-best: the
/// job's candidates are the first 8 paths with no fuzzy word (the n-gram first included, in n-best order), so no fuzzy path is in it and
/// one that stood in the top 8 is replaced by a later path. With fewer than 8 such paths the pool is all of them, in n-best order. The
/// model's choice is an entry of the pool, and the composition shows that path: `pick` is a pool index, not an n-best index, so it
/// matters that the pool skips a fuzzy path before the choice and that the model chooses something other than the first.
#[test]
fn the_pool_is_the_first_eight_non_fuzzy_paths_in_nbest_order_and_the_choice_is_one_of_them() {
    for (syls, flags, all_fewer_than_8) in [(["ㄙㄨ"], 15, false), (["ㄆㄧㄥˋ"], 15, true)] {
        let p = pooled(&syls, flags);
        assert!(p.nb[..8.min(p.nb.len())].iter().any(|x| x.1), "premise: a fuzzy path stands among the first 8");
        let want: Vec<String> = p.pool.iter().map(|&i| p.nb[i].0.clone()).collect();
        assert_eq!(want.len() < 8, all_fewer_than_8, "premise: {syls:?}");
        assert!(want.len() >= 2 && p.nb.iter().take(8).any(|x| !want.contains(&x.0)), "premise: the pool differs from the first 8");
        assert!(p.pick != 0 && p.pool[p.pick] != p.pick, "premise: the model leaves the first, and its pool index is not its n-best index: {syls:?}");
        let mut e = engine_with(flags, &p.model);
        type_syls(&mut e, &syls);
        let job = e.take_rerank_job().expect("a job");
        assert_eq!(job.candidates(), [want.clone()], "{syls:?}");
        assert_eq!(e.fuzzy_skips(), 0);
        let shown = e.apply_rerank(job.run()).unwrap().expect("the model's choice changes the display").preedit;
        assert_eq!(shown, p.nb[p.pool[p.pick]].0, "{syls:?}: the shown text is the model's choice");
    }
}

/// Section 3.6 (e): the n-gram first path has a fuzzy word (a rescue): the stretch is not reranked. No job, `fuzzy_skips` counts it
/// (`guard_skips` does not), and the composition shows the n-gram first.
#[test]
fn a_fuzzy_first_path_is_not_reranked() {
    let syls = ["ㄗ", "ㄉㄠˋ"];
    let nb = nbest(&syls, 15);
    assert!(nb[0].1 && nb.len() >= 2, "premise: the n-gram first path is a fuzzy path, with others behind it");
    let mut e = engine(15);
    let o = type_syls(&mut e, &syls);
    assert!(e.take_rerank_job().is_none(), "no job for the stretch");
    assert_eq!((e.fuzzy_skips(), e.guard_skips()), (1, 0));
    assert_eq!(o.preedit, nb[0].0);
    // The same input with the pair off has no fuzzy path, so nothing is skipped and the stretch is reranked.
    let mut off = engine(0);
    type_syls(&mut off, &syls);
    assert!(off.take_rerank_job().is_some() && off.fuzzy_skips() == 0);
}

/// A learning directory (a temporary one, removed on drop) with `records` written as its learning.tsv: (context, reading, word, weight),
/// day `DAY`. The directory is 0700 and the file 0600, as the store requires.
const DAY: i64 = 20_000;
struct LearnDir(std::path::PathBuf);
impl LearnDir {
    fn new(tag: &str, records: &[(&str, &str, &str, u32)]) -> LearnDir {
        use std::os::unix::fs::PermissionsExt;
        let d = std::env::temp_dir().join(format!("shanjie-fz-learn-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir(&d).unwrap();
        std::fs::set_permissions(&d, std::fs::Permissions::from_mode(0o700)).unwrap();
        let mut text = String::from("#shanjie-learning v1\n");
        for (context, reading, word, weight) in records {
            text.push_str(&format!("{context}\t{reading}\t{word}\t{weight}\t{DAY}\n"));
        }
        std::fs::write(d.join("learning.tsv"), text).unwrap();
        std::fs::set_permissions(d.join("learning.tsv"), std::fs::Permissions::from_mode(0o600)).unwrap();
        LearnDir(d)
    }
    /// A fuzzy-15 engine with the toy model as it is, learning on and this directory open.
    fn engine(&self) -> Engine {
        let mut e = engine(15);
        e.set_today(Some(DAY));
        e.learning_open(&self.0).unwrap();
        e.set_learning(true);
        e
    }
}
impl Drop for LearnDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// Section 3.6, a fuzzy word keeps its marker under a learning boost: 之道 is taught for ㄓ ㄉㄠˋ (the record of a pick made in the window,
/// as engine_fuzzy.rs does), and ㄗ ㄉㄠˋ is typed again. The learned boost puts 之道 first and it is read through the fuzzy-sound
/// alternative still, so the stretch is a rescue: skipped by the fuzzy rule (`fuzzy_skips` 1), never by the learning guard
/// (`guard_skips` 0, though the guard would take the word for a boosted one).
#[test]
fn a_fuzzy_first_path_with_a_learned_boost_counts_as_a_fuzzy_skip_only() {
    let dir = LearnDir::new("fuzzy-first", &[("^>$", "ㄓ-ㄉㄠˋ", "之道", 1)]);
    let syls = ["ㄗ", "ㄉㄠˋ"];
    let mut e = dir.engine();
    assert!(!e.learner().is_empty(), "premise: the record is loaded");
    let o = type_syls(&mut e, &syls);
    assert_eq!(o.preedit, "之道", "premise: the learned word is first (it is 知道 without the record)");
    assert!(e.take_rerank_job().is_none(), "no job");
    assert_eq!((e.fuzzy_skips(), e.guard_skips()), (1, 0));
}

/// Section 3.6 and the learning guard: `guard_skips` counts a stretch the guard kept out of a job. A stretch whose pool has fewer than 2
/// paths has no job to keep out, taught or not. ㄋㄥˋ ㄋㄥˋ has one path without a fuzzy word (濘濘, which a new-word record of the
/// learner makes a taught word), the rest are fuzzy ones: nothing is guarded. 身為 for ㄕㄣ ㄨㄟˊ is a taught word of a stretch with a
/// full pool: the guard keeps the job out and counts it.
#[test]
fn the_guard_counts_only_a_job_it_kept_out() {
    let alone = nbest(&["ㄋㄥˋ", "ㄋㄥˋ"], 15);
    assert!(!alone[0].1 && alone.iter().filter(|p| !p.1).count() == 1 && alone.len() > 1, "premise: one path without a fuzzy word, others behind it");
    let dir = LearnDir::new("guard", &[("#", "ㄋㄥˋ-ㄋㄥˋ", "濘濘", 5), ("^>$", "ㄕㄣ-ㄨㄟˊ", "身為", 1)]);
    let mut e = dir.engine();
    let o = type_syls(&mut e, &["ㄋㄥˋ", "ㄋㄥˋ"]);
    assert_eq!(o.preedit, "濘濘");
    assert!(e.take_rerank_job().is_none(), "no job: the pool has one path");
    assert_eq!((e.guard_skips(), e.fuzzy_skips()), (0, 0));
    let mut e = dir.engine();
    type_syls(&mut e, &["ㄕㄣ", "ㄨㄟˊ"]);
    assert!(e.take_rerank_job().is_none(), "the guard keeps the taught stretch out");
    assert_eq!((e.guard_skips(), e.fuzzy_skips()), (1, 0));
    // Without a record the same stretch has a job.
    let mut e = engine(15);
    type_syls(&mut e, &["ㄕㄣ", "ㄨㄟˊ"]);
    assert!(e.take_rerank_job().is_some() && e.guard_skips() == 0);
}

/// Without a fuzzy-sound bit the job is what it was: the first 8 paths of the n-best, whatever bit 4 says.
#[test]
fn without_a_sound_bit_the_pool_is_the_first_eight() {
    for flags in [0, core::fuzzy::FUZZY_ADJACENT] {
        let nb = nbest(&["ㄙㄨ"], flags);
        let want: Vec<String> = nb.iter().take(8).map(|p| p.0.clone()).collect();
        let mut e = engine(flags);
        type_syls(&mut e, &["ㄙㄨ"]);
        assert_eq!(e.take_rerank_job().unwrap().candidates(), [want], "flags {flags}");
        assert_eq!(e.fuzzy_skips(), 0);
    }
}

