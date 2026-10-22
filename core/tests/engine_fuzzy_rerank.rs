//! Fuzzy input and the semantic correction (docs/contracts/fuzzy-input.md section 3.6, tests (d) and (e); (a)-(c) are in core/src/lm.rs,
//! (f) in cli/tests/fuzzy_rerank.rs). The engine with the real lexicon and n-gram model, the demotion table on, and the toy small model of
//! eval/golden/tinylm-tiny/ patched like engine_rerank.rs does (alpha 100, tau infinite: its LLs decide). Needs data/lm/bigram.sjlm.
use core::engine::*;
use core::lm::{decode_from_fuzzy_scored, CappedLexicon, Demote, Lm, Profile};
use core::{Lexicon, BEAM_S1};
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}
/// bigram.sjlm and classes.sjc symlinked into a temporary directory without kn.sjkn, for the version 1 toy (as engine_rerank.rs).
// ponytail: the directory (two symlinks) is left in the temp dir; a OnceLock never drops.
fn lm_path() -> PathBuf {
    static P: OnceLock<PathBuf> = OnceLock::new();
    P.get_or_init(|| {
        let real = root().join("data/lm");
        assert!(real.join("bigram.sjlm").exists() && real.join("classes.sjc").exists(), "data/lm/bigram.sjlm and classes.sjc are needed: see engine_lm.rs");
        let d = std::env::temp_dir().join(format!("shanjie-fuzzy-rerank-nokn-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        for f in ["bigram.sjlm", "classes.sjc"] {
            let _ = std::fs::remove_file(d.join(f));
            std::os::unix::fs::symlink(real.join(f), d.join(f)).unwrap();
        }
        d.join("bigram.sjlm")
    })
    .clone()
}

struct Shared {
    lex: Arc<Lexicon>,
    lm: Arc<Lm>,
    capped: Arc<CappedLexicon>,
}
/// The capped lexicon has the demotion table, as `Engine::load_lm` builds it.
fn shared() -> &'static Shared {
    static S: OnceLock<Shared> = OnceLock::new();
    S.get_or_init(|| {
        let dir = root().join("data/lexicon");
        let lex = load_lexicon(&dir).unwrap();
        let lm = Lm::load(&lm_path()).unwrap();
        let overlay = capping_overlay(&dir, "").unwrap();
        let demote = Demote::parse(&std::fs::read_to_string(dir.join("demote.tsv")).unwrap()).unwrap();
        let capped = Arc::new(CappedLexicon::new(lex.clone(), &overlay, &lm, Some(&demote)).unwrap());
        Shared { lex, lm: Arc::new(lm), capped }
    })
}

/// The toy model with alpha 100 / tau infinite for both profiles and the n-gram hash of `lm_path()`.
// ponytail: the file (14 KB, named by the process id) is left in the temp dir; a OnceLock never drops.
fn model() -> &'static Path {
    static P: OnceLock<PathBuf> = OnceLock::new();
    P.get_or_init(|| {
        let mut b = std::fs::read(root().join("eval/golden/tinylm-tiny/tiny-int8.sjtl")).unwrap();
        let h = core::eval::sha256_hex(&std::fs::read(lm_path()).unwrap());
        let sha: Vec<u8> = (0..32).map(|i| u8::from_str_radix(&h[2 * i..2 * i + 2], 16).unwrap()).collect();
        b[68..100].copy_from_slice(&sha);
        for at in [36, 52] {
            b[at..at + 8].copy_from_slice(&100.0f64.to_le_bytes());
            b[at + 8..at + 16].copy_from_slice(&f64::INFINITY.to_le_bytes());
        }
        let p = std::env::temp_dir().join(format!("shanjie-fuzzy-rerank-{}.sjtl", std::process::id()));
        std::fs::write(&p, b).unwrap();
        p
    })
}

/// An engine with the n-gram model, the toy small model and the rerank on, and the fuzzy `flags`.
fn engine(flags: u32) -> Engine {
    let s = shared();
    let mut e = Engine::with_lexicon(s.lex.clone(), Layout::Standard);
    e.set_lm(s.lm.clone(), s.capped.clone());
    e.load_tinylm(model()).unwrap();
    e.set_rerank(true).unwrap();
    e.set_fuzzy(flags).unwrap();
    e
}

fn type_syls(e: &mut Engine, syls: &[&str]) -> Output {
    let layout = Layout::Standard;
    let mut last = None;
    for syl in syls {
        let mut toned = false;
        for c in syl.chars() {
            let key = match layout.key_of_tone(c) {
                Some(tk) => {
                    toned = true;
                    tk
                }
                None => layout.key_of_symbol(c).expect("symbol has a key"),
            };
            last = Some(e.key(Key::ch(key, 0)).unwrap());
        }
        if !toned {
            last = Some(e.key(Key::new(KeyKind::Space)).unwrap());
        }
    }
    last.unwrap()
}

/// The decoder's n-best of `syls` as the engine decodes a free composition: (surface, has a fuzzy word).
fn nbest(syls: &[&str], flags: u32) -> Vec<(String, bool)> {
    let s = shared();
    let v: Vec<String> = syls.iter().map(|x| x.to_string()).collect();
    decode_from_fuzzy_scored(&s.capped, &v, &s.lm, Profile::Chat, BEAM_S1, "<s>", true, flags, core::fuzzy::DELTA_SOUND)
        .unwrap()
        .into_iter()
        .map(|(_, ws)| (ws.iter().map(|w| w.0).collect(), ws.iter().any(|w| w.4)))
        .collect()
}

/// Section 3.6 (d): the n-gram first path reads the typed syllable and fuzzy-sound paths stand among the first 8 of the n-best: the
/// job's candidates are the first 8 paths with no fuzzy word (the n-gram first included, in n-best order), so no fuzzy path is in it and
/// one that stood in the top 8 is replaced by a later path. With fewer than 8 such paths the pool is all of them, in n-best order. The
/// model's choice is one of them.
#[test]
fn the_pool_is_the_first_eight_non_fuzzy_paths_in_nbest_order() {
    for (syls, flags, all_fewer_than_8) in [(["ㄙㄨ"], 15, false), (["ㄆㄧㄥˋ"], 15, true)] {
        let nb = nbest(&syls, flags);
        assert!(!nb[0].1, "premise: the n-gram first path is the typed reading");
        assert!(nb[..8.min(nb.len())].iter().any(|p| p.1), "premise: a fuzzy path stands among the first 8");
        let want: Vec<String> = nb.iter().filter(|p| !p.1).take(8).map(|p| p.0.clone()).collect();
        assert_eq!(want.len() < 8, all_fewer_than_8, "premise: {syls:?}");
        assert!(want.len() >= 2 && nb.iter().take(8).any(|p| !want.contains(&p.0)), "premise: the pool differs from the first 8");
        let mut e = engine(flags);
        type_syls(&mut e, &syls);
        let job = e.take_rerank_job().expect("a job");
        assert_eq!(job.candidates(), [want.clone()], "{syls:?}");
        assert_eq!(e.fuzzy_skips(), 0);
        let shown = e.apply_rerank(job.run()).unwrap();
        let shown = shown.map_or_else(|| want[0].clone(), |o| o.preedit);
        assert!(want.contains(&shown), "{syls:?}: the shown text {shown} is one of the pool");
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
