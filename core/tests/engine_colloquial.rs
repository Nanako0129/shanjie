//! The colloquial term in the engine (docs/contracts/colloquial-term.md sections 2.2 and 3, item 1): on the real lexicon, n-gram model,
//! small model and colloquial table (data/lm/, fail loudly without them). The sentence is row 202 of eval/bench/suite-v1/dev302.txt: with
//! the context `桌子一直在搖` the shipping rerank keeps the n-gram first (candidate 0, `羅斯松`), the term picks candidate 4 (`螺絲鬆`).
use core::engine::*;
use core::lm::Profile;
use core::tinylm::RerankLevel;
use std::path::{Path, PathBuf};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}
fn data(f: &str) -> PathBuf {
    let p = root().join("data/lm").join(f);
    assert!(p.exists(), "data/lm/{f} is needed: see data/*.sha256 and docs/releases/README.md");
    p
}

const CONTEXT: &str = "桌子一直在搖";
const ROW: &str = "ㄓㄨㄛ ㄐㄧㄠˇ ㄉㄜ˙ ㄌㄨㄛˊ ㄙ ㄙㄨㄥ ㄉㄧㄠˋ ㄌㄜ˙";
/// Candidate indices of the row's n-best: what the shipping rerank keeps and what the term picks.
const SHIPPING: usize = 0;
const WITH_TERM: usize = 4;

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

/// An engine with the real models, the context set and the row typed; `table` loads the colloquial table first or last.
fn engine(packs: u32, tinylm: &Path, table: Option<(&Path, bool)>) -> Engine {
    let pdir = root().join("data/packs");
    let mut e = Engine::new_with_packs(&root().join("data/lexicon"), Layout::Standard, Some((&pdir, packs))).unwrap();
    if let Some((t, true)) = table {
        e.load_colloquial(t).unwrap();
    }
    e.load_lm(&data("bigram.sjlm")).unwrap();
    e.load_tinylm(tinylm).unwrap();
    if let Some((t, false)) = table {
        e.load_colloquial(t).unwrap();
    }
    e.set_rerank(true).unwrap();
    e.set_left_context(CONTEXT);
    for syl in ROW.split(' ') {
        for k in keys_of(syl) {
            e.key(k).unwrap();
        }
    }
    e
}

/// The picks of the job the current composition has (a fresh one: `set_rerank` recomputes and makes it).
fn picks(e: &mut Engine) -> Vec<usize> {
    e.set_rerank(true).unwrap();
    e.take_rerank_job().expect("a job").run().picks()
}

fn engine_picks(table: Option<&Path>, tinylm: &Path) -> Vec<usize> {
    picks(&mut engine(0, tinylm, table.map(|t| (t, false))))
}

/// A scratch file that is removed when it goes out of scope, an assertion failure included.
struct Scratch(PathBuf);
impl std::ops::Deref for Scratch {
    type Target = Path;
    fn deref(&self) -> &Path {
        &self.0
    }
}
impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}

/// The same `.sjtl` with its last bit flipped: it loads, pairs with the n-gram model and ranks as before, but its SHA-256 is another one.
fn other_sjtl(name: &str) -> Scratch {
    let mut b = std::fs::read(data("tinylm.sjtl")).unwrap();
    *b.last_mut().unwrap() ^= 1;
    let p = std::env::temp_dir().join(format!("shanjie-colloq-{name}-{}.sjtl", std::process::id()));
    let p = Scratch(p);
    std::fs::write(&*p, b).unwrap();
    p
}

/// The four conditions: all hold -> the term picks `螺絲鬆`; the table missing, the formal profile, the conservative and the aggressive strength
/// and another small model each leave the jobs those of an engine without a table (the plain `rerank`).
#[test]
fn the_term_applies_only_under_its_four_conditions() {
    let (tl, table) = (data("tinylm.sjtl"), data("colloquial.sjlm"));
    assert_eq!(engine_picks(None, &tl), vec![SHIPPING], "premise: shipping keeps the n-gram first");
    let mut with = engine(0, &tl, Some((&table, false)));
    assert_eq!(picks(&mut with), vec![WITH_TERM], "chat, standard, the paired model, the table loaded");
    let mut without = engine(0, &tl, None);
    for (name, profile, level) in [
        ("formal", Profile::Formal, RerankLevel::Standard),
        ("conservative", Profile::Chat, RerankLevel::Conservative),
        ("aggressive", Profile::Chat, RerankLevel::Aggressive),
    ] {
        for e in [&mut with, &mut without] {
            e.set_profile(profile).unwrap();
            e.set_rerank_level(level).unwrap();
        }
        assert_eq!(picks(&mut with), picks(&mut without), "{name}: the term is not used");
    }
    // back to the shipping setting: the term is on again, with no reload
    for e in [&mut with, &mut without] {
        e.set_profile(Profile::Chat).unwrap();
        e.set_rerank_level(RerankLevel::Standard).unwrap();
    }
    assert_eq!(picks(&mut with), vec![WITH_TERM]);
    // another small model (one bit differs in the file): the pairing fails, so the term is off
    let other = other_sjtl("other");
    let (a, b) = (engine_picks(Some(&table), &other), engine_picks(None, &other));
    assert_eq!(a, b, "another .sjtl: the term is not used");
    assert_eq!(b, vec![SHIPPING], "premise: the flipped file ranks as the real one");
}

/// The table may be loaded before or after the n-gram and the small model.
#[test]
fn load_order_does_not_matter() {
    let (tl, table) = (data("tinylm.sjtl"), data("colloquial.sjlm"));
    assert_eq!(picks(&mut engine(0, &tl, Some((&table, true)))), vec![WITH_TERM]);
}

/// A failed load leaves the engine as it was (the previous table, or none) and drops the pending job.
#[test]
fn a_failed_load_changes_nothing_but_the_job() {
    let (tl, table) = (data("tinylm.sjtl"), data("colloquial.sjlm"));
    let bad = Scratch(std::env::temp_dir().join(format!("shanjie-colloq-bad-{}.sjlm", std::process::id())));
    std::fs::write(&*bad, b"not a table").unwrap();
    let mut e = engine(0, &tl, None);
    assert!(e.colloquial().is_none());
    assert!(e.load_colloquial(&bad).is_err() && e.load_colloquial(Path::new("/nonexistent/x.sjlm")).is_err());
    assert!(e.colloquial().is_none(), "no table before, none after");
    assert_eq!(picks(&mut e), vec![SHIPPING]);
    e.load_colloquial(&table).unwrap();
    let sha = e.colloquial().unwrap().sha256().to_string();
    let job = e.take_rerank_job().or_else(|| {
        e.set_rerank(true).unwrap();
        e.take_rerank_job()
    });
    assert!(e.load_colloquial(&bad).is_err());
    assert_eq!(e.colloquial().map(|c| c.sha256()), Some(sha.as_str()), "the previous table stays");
    assert!(e.apply_rerank(job.unwrap().run()).unwrap().is_none(), "the pending job was dropped");
    assert_eq!(picks(&mut e), vec![WITH_TERM], "and the table still works");
}

/// k counts the words of the packs the engine was built with: a word only the acg pack has is in k with the pack and not without it.
/// The row below is the first row of the pack, which overlay-add.tsv and mcbpmf-data.txt do not have.
#[test]
fn k_counts_the_pack_words_the_engine_was_built_with() {
    let (tl, table) = (data("tinylm.sjtl"), data("colloquial.sjlm"));
    let pack = std::fs::read_to_string(root().join("data/packs/acg-add.tsv")).unwrap();
    let word = pack.lines().next().unwrap().split('\t').nth(1).unwrap().to_string();
    let k = |packs: u32| engine(packs, &tl, Some((&table, false))).colloquial().unwrap().k(&[word.as_str()]);
    assert_eq!((k(PACK_ACG), k(0)), (1, 0));
}
