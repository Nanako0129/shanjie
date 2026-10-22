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
    engine_row(packs, tinylm, table, CONTEXT, ROW)
}

/// `engine` for another context and row (syllables separated by spaces; `，` is Shift+comma).
fn engine_row(packs: u32, tinylm: &Path, table: Option<(&Path, bool)>, context: &str, row: &str) -> Engine {
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
    e.set_left_context(context);
    for syl in row.split(' ') {
        if syl == "，" {
            e.key(Key::ch(',', MOD_SHIFT)).unwrap();
            continue;
        }
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

/// The four conditions (docs/contracts/colloquial-term.md section 2.2, any strength since colloquial-levels): all hold -> the term picks `螺絲鬆`
/// (standard, and aggressive with its own cell); the table missing, the formal profile and another small model each leave the jobs those of an
/// engine without a table (the plain `rerank`). At the conservative strength this row is kept by both (its own cell is tested by
/// `each_level_uses_its_own_cell`).
#[test]
fn the_term_applies_only_under_its_four_conditions() {
    let (tl, table) = (data("tinylm.sjtl"), data("colloquial.sjlm"));
    assert_eq!(engine_picks(None, &tl), vec![SHIPPING], "premise: shipping keeps the n-gram first");
    let mut with = engine(0, &tl, Some((&table, false)));
    assert_eq!(picks(&mut with), vec![WITH_TERM], "chat, standard, the paired model, the table loaded");
    let mut without = engine(0, &tl, None);
    for e in [&mut with, &mut without] {
        e.set_profile(Profile::Formal).unwrap();
    }
    assert_eq!(picks(&mut with), picks(&mut without), "formal: the term is not used");
    for e in [&mut with, &mut without] {
        e.set_profile(Profile::Chat).unwrap();
        e.set_rerank_level(RerankLevel::Aggressive).unwrap();
    }
    assert_eq!(picks(&mut without), vec![SHIPPING], "premise: the aggressive header triple keeps the n-gram first");
    assert_eq!(picks(&mut with), vec![WITH_TERM], "aggressive: the term is used with its own cell");
    for e in [&mut with, &mut without] {
        e.set_rerank_level(RerankLevel::Conservative).unwrap();
    }
    assert_eq!(picks(&mut with), picks(&mut without), "conservative: this row is kept either way");
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

/// Rows 10, 81 and 102 of eval/dev/user-reported.txt (public), the three rows of that set where the reading-channel term (docs/contracts/reading-channel.md)
/// changes the colloquial term's choice: (context, syllables, the pick with the colloquial term alone, the pick with the channel too). The indices
/// are those of the CLI's `--tinylm-scores` `c`, which `p5.py --cell lex,1.0,4` reproduces row for row.
const CHANNEL_ROWS: [(&str, &str, usize, usize); 3] = [
    ("", "ㄋㄧˇ ㄧㄡˇ ㄉㄨㄥ ㄒㄧ ㄧㄠˋ ㄒㄧㄢ ㄐㄧㄣˋ ㄅㄢˇ ㄎㄨㄥˋ ㄇㄚ", 0, 2),
    ("", "ㄓㄤˇ ㄨㄞ ㄌㄜ˙", 1, 0),
    ("幹opencc", "ㄕˋ ㄅㄨˋ ㄕˋ ㄏㄣˇ ㄌㄟˊ ㄚ˙", 1, 0),
];

/// The term rides on the colloquial term: with the table loaded and the four conditions holding the channel decides (and `set_reading_channel(false)`
/// leaves the colloquial term alone); wherever the colloquial term does not apply (no table, formal, another small model) the jobs are those of an
/// engine without the table, the channel included. At the aggressive strength the channel decides as at the standard one (the same alpha).
#[test]
fn the_reading_channel_applies_only_where_the_colloquial_term_does() {
    let (tl, table) = (data("tinylm.sjtl"), data("colloquial.sjlm"));
    let other = other_sjtl("rc-other");
    for (i, (ctx, row, alone, with)) in CHANNEL_ROWS.iter().enumerate() {
        let mut on = engine_row(PACK_ACG, &tl, Some((&table, false)), ctx, row);
        let mut off = engine_row(PACK_ACG, &tl, Some((&table, false)), ctx, row);
        off.set_reading_channel(false);
        let mut none = engine_row(PACK_ACG, &tl, None, ctx, row);
        assert_ne!(alone, with);
        assert_eq!(picks(&mut on), vec![*with], "row {i}: the channel's pick");
        assert_eq!(picks(&mut off), vec![*alone], "row {i}: the colloquial term alone");
        for e in [&mut on, &mut none] {
            e.set_profile(Profile::Formal).unwrap();
        }
        assert_eq!(picks(&mut on), picks(&mut none), "row {i}, formal: neither term is used");
        for e in [&mut on, &mut none] {
            e.set_profile(Profile::Chat).unwrap();
            e.set_rerank_level(RerankLevel::Aggressive).unwrap();
        }
        assert_eq!(picks(&mut on), vec![*with], "row {i}, aggressive: the channel decides as at the standard strength");
        let (a, b) = (picks(&mut engine_row(PACK_ACG, &other, Some((&table, false)), ctx, row)), picks(&mut engine_row(PACK_ACG, &other, None, ctx, row)));
        assert_eq!(a, b, "row {i}, another .sjtl: neither term is used");
    }
}

/// A composition with punctuation is several stretches, and each is cut over its own syllables: rows 10 and 81 of the set above on either side of
/// a comma give two stretches whose picks are the channel's (the second one in the first's context, so it is compared with the term off, not with
/// the row alone). Cut over the whole composition's syllables, no path would cover them and the term would be 0 in both.
#[test]
fn each_stretch_is_cut_over_its_own_syllables() {
    let (tl, table) = (data("tinylm.sjtl"), data("colloquial.sjlm"));
    let row = format!("{} ， {}", CHANNEL_ROWS[0].1, CHANNEL_ROWS[1].1);
    let mut on = engine_row(PACK_ACG, &tl, Some((&table, false)), "", &row);
    let mut off = engine_row(PACK_ACG, &tl, Some((&table, false)), "", &row);
    off.set_reading_channel(false);
    let (a, b) = (picks(&mut on), picks(&mut off));
    assert_eq!(b.len(), 2, "two stretches");
    assert_eq!((a[0], b[0]), (CHANNEL_ROWS[0].3, CHANNEL_ROWS[0].2), "the first stretch");
    assert_eq!((a[1], b[1]), (CHANNEL_ROWS[1].3, CHANNEL_ROWS[1].2), "the second stretch");
}

/// Public rows where a level's own cell picks a candidate that neither the standard cell nor the level's header triple (v0.5.0: the plain
/// `rerank`) picks, with the picks of `--rerank-level L --colloquial-core` and the two it must differ from (the CLI's `--tinylm-scores` `c`):
/// (level, context, syllables, cell's pick, standard cell's pick, header triple's pick). Row 127 of eval/bench/suite-v1/dev302.txt at the conservative
/// strength; row 54 of eval/dev/user-reported.txt at the aggressive one (there the old pick was wrong and the new one is right).
const LEVEL_ROWS: [(RerankLevel, &str, &str, usize, usize, usize); 2] = [
    (RerankLevel::Conservative, "廟裡有場法會", "ㄉㄠˋ ㄕˋ ㄗㄞˋ ㄇㄧㄠˋ ㄌㄧˇ ㄕㄜˋ ㄊㄢˊ ㄗㄨㄛˋ ㄈㄚˇ", 0, 5, 1),
    (RerankLevel::Aggressive, "", "ㄎㄜˇ ㄧˇ ㄓㄨㄣˇ ㄅㄟˋ ㄗㄨㄛˋ", 1, 0, 0),
];

/// colloquial-levels section 2.1, the engine path: the job takes the engine's rerank level when it is made, and its colloquial and reading-channel
/// terms use that level's (alpha, tau, delta). The pick at each level is the CLI's index, and differs from the standard cell's (an engine that fuses
/// with the standard values, or a job that does not remember the level, picks that) and from the header triple's (no term).
#[test]
fn each_level_uses_its_own_cell() {
    let (tl, table) = (data("tinylm.sjtl"), data("colloquial.sjlm"));
    for (level, ctx, row, cell, standard, plain) in LEVEL_ROWS {
        assert!(cell != standard && cell != plain, "{level:?}: premise, three different picks");
        let at = |level: RerankLevel, table: Option<&Path>| {
            let mut e = engine_row(PACK_ACG, &tl, table.map(|t| (t, false)), ctx, row);
            e.set_rerank_level(level).unwrap();
            picks(&mut e)
        };
        assert_eq!(at(RerankLevel::Standard, Some(&table)), vec![standard], "{level:?}: the standard cell");
        assert_eq!(at(level, None), vec![plain], "{level:?}: the header triple, no table");
        assert_eq!(at(level, Some(&table)), vec![cell], "{level:?}: the level's own cell");
    }
}

/// `colloquial::applies` is the engine's and the CLI's one decision: chat, whatever the strength, and the measured small model only.
#[test]
fn applies_is_chat_and_the_paired_model_at_every_level() {
    let lm = core::lm::Lm::load(&data("bigram.sjlm")).unwrap();
    let real = core::tinylm::TinyLm::load_paired(&data("tinylm.sjtl"), &lm).unwrap();
    let other = other_sjtl("applies");
    let flipped = core::tinylm::TinyLm::load_paired(&other, &lm).unwrap();
    for level in [RerankLevel::Conservative, RerankLevel::Standard, RerankLevel::Aggressive] {
        assert!(core::colloquial::applies(Profile::Chat, level, &real), "chat {level:?}");
        assert!(!core::colloquial::applies(Profile::Formal, level, &real), "formal {level:?}");
        assert!(!core::colloquial::applies(Profile::Chat, level, &flipped), "another .sjtl {level:?}");
    }
}
