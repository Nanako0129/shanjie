//! acg-pack engine tests (docs/contracts/acg-pack.md A.2 and A.5): the shipping toggle path (`Engine::new_with_packs`, the
//! same constructor `shanjie_engine_new_packs` calls) with a pack that changes the result. Real lexicon and model
//! (data/lm/bigram.sjlm); fails loudly without them, never skips. The pack is a fixture written here, so the test does not
//! depend on the generated data/packs/acg-add.tsv.
use core::engine::*;
use core::lm::Profile;
use std::path::{Path, PathBuf};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}
fn lm_path() -> PathBuf {
    let p = root().join("data/lm/bigram.sjlm");
    assert!(root().join("data/lm/classes.sjc").exists(), "data/lm/classes.sjc is missing: gh release download classes-v5 -R Nanako0129/shanjie -p classes.sjc -D data/lm");
    assert!(p.exists(), "data/lm/bigram.sjlm is missing: gh release download model-v6 -R Nanako0129/shanjie -p bigram.sjlm -D data/lm");
    p
}

/// Two names the base lexicon lacks, with the scores build_acg_pack.py gives 3- and 5-character words.
const PACK: &str = "ㄉㄧㄥˋ-ㄩㄢˊ-ㄊㄤˊ\t碇源堂\t-7.04116568\tacg\nㄧㄥˊ-ㄏㄨㄛˇ-ㄔㄨㄥˊ-ㄓ-ㄇㄨˋ\t螢火蟲之墓\t-6.17843815\tacg\n";

fn tempdir(name: &str, pack: Option<&str>) -> PathBuf {
    let d = std::env::temp_dir().join(format!("shanjie-pack-test-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    if let Some(p) = pack {
        std::fs::write(d.join("acg-add.tsv"), p).unwrap();
    }
    d
}

fn is_tone(c: char) -> bool {
    "ˊˇˋ˙".contains(c)
}
const L: Layout = Layout::Standard;
fn syl_keys(syl: &str) -> Vec<Key> {
    let mut v = Vec::new();
    let mut toned = false;
    for c in syl.chars() {
        match L.key_of_tone(c) {
            Some(tk) if is_tone(c) => {
                v.push(Key::ch(tk, 0));
                toned = true;
            }
            _ => v.push(Key::ch(L.key_of_symbol(c).expect("symbol has a key"), 0)),
        }
    }
    if !toned {
        v.push(Key::new(KeyKind::Space));
    }
    v
}

/// Every output of the same key sequences, per profile: the decode (preedit), the candidate window (Down opens it) and
/// the V3 prediction row (pending ㄋ after a left context).
fn run(packs: Option<(&Path, u32)>) -> Vec<Output> {
    let mut e = Engine::new_with_packs(&root().join("data/lexicon"), L, packs).unwrap();
    e.load_lm(&lm_path()).unwrap();
    let mut outs = Vec::new();
    for profile in [Profile::Chat, Profile::Formal] {
        e.set_profile(profile).unwrap();
        for (left, syls, pending) in [
            ("", vec!["ㄉㄧㄥˋ", "ㄩㄢˊ", "ㄊㄤˊ"], ""),
            ("", vec!["ㄧㄥˊ", "ㄏㄨㄛˇ", "ㄔㄨㄥˊ", "ㄓ", "ㄇㄨˋ"], ""),
            ("", vec!["ㄋㄧˇ", "ㄏㄠˇ", "ㄕˋ", "ㄐㄧㄝˋ"], ""),
            ("我想喝一杯", vec![], "ㄋ"),
        ] {
            e.reset(ResetMode::Discard);
            // Predict-down contract B.4: the window is the subject of the first three sequences, and it now lists a prediction
            // row first; only the last sequence (the row, with a pending syllable) keeps the row on.
            e.set_prediction(!pending.is_empty()).unwrap();
            e.set_left_context(left);
            let mut keys: Vec<Key> = syls.iter().flat_map(|s| syl_keys(s)).collect();
            keys.extend(pending.chars().map(|c| Key::ch(L.key_of_symbol(c).unwrap(), 0)));
            for k in keys {
                outs.push(e.key(k).unwrap());
            }
            outs.push(e.key(Key::new(KeyKind::Down)).unwrap());
        }
    }
    outs
}

#[test]
fn the_pack_switch_changes_the_result_and_off_is_identical_to_no_pack_file() {
    let (none, with) = (tempdir("none", None), tempdir("with", Some(PACK)));
    let plain = run(None); // Engine::new, as before the pack existed
    assert!(plain.iter().any(|o| !o.candidates.is_empty()), "the sequences must open a candidate window");
    assert!(plain.iter().any(|o| o.selected.is_none() && !o.candidates.is_empty()), "the sequences must show a prediction row");
    // Off: bit 0 clear with the file present, and bit 0 set with no file: both are the engine without the pack.
    assert!(run(Some((&with, 0))) == plain, "mask 0 with the pack file present differs from no pack");
    assert!(run(Some((&none, PACK_ACG))) == plain, "a missing pack file changes the engine");
    // On: the pack word is the window's first candidate, in both profiles. Without the pack the window's first
    // candidate is only the last word (元堂, 汁木; measured), also in formal, where the sentence decode alone
    // already spells both names (chat spells 定元堂 and 螢火蟲之目; measured with shanjie-eval --dump).
    let on = run(Some((&with, PACK_ACG)));
    assert!(on != plain);
    let firsts = |outs: &[Output], word: &str| outs.iter().filter(|o| o.selected == Some(0) && o.candidates.first().map(String::as_str) == Some(word)).count();
    for word in ["碇源堂", "螢火蟲之墓"] {
        assert_eq!(firsts(&on, word), 2, "{word} is not the first candidate in chat and formal with the pack");
        assert_eq!(firsts(&plain, word), 0, "{word} is already the first candidate without the pack");
    }
    for d in [none, with] {
        let _ = std::fs::remove_dir_all(d);
    }
}

/// it-pack contract section 3.1: the IT+AI pack is bit 2 (`PACK_IT`) and reads `it-add.tsv`, appended after the anime pack. Its own switch
/// through the shipping constructor, alone and with the anime pack; off (the bit clear, or no file) is the engine without it.
/// 圖像生成 is one of the hand-written words the shipping decoder does not already type right (chat and formal both spell 圖象生成; measured with the CLI)
/// and that the pack does win back in the sentence decode, with the score build_it_pack.py gives 4-character words.
const IT_PACK: &str = "ㄊㄨˊ-ㄒㄧㄤˋ-ㄕㄥ-ㄔㄥˊ\t圖像生成\t-6.60980192\tit\n";

/// The first candidate of the window after typing `syls`, per profile.
fn window_first(packs: Option<(&Path, u32)>, syls: &[&str]) -> Vec<String> {
    let mut e = Engine::new_with_packs(&root().join("data/lexicon"), L, packs).unwrap();
    e.load_lm(&lm_path()).unwrap();
    [Profile::Chat, Profile::Formal]
        .iter()
        .map(|&p| {
            e.set_profile(p).unwrap();
            e.reset(ResetMode::Discard);
            e.set_prediction(false).unwrap();
            for k in syls.iter().flat_map(|s| syl_keys(s)) {
                e.key(k).unwrap();
            }
            e.key(Key::new(KeyKind::Down)).unwrap().candidates[0].clone()
        })
        .collect()
}

#[test]
fn the_it_pack_has_its_own_bit_and_file() {
    assert_eq!(PACK_IT, 4);
    assert_eq!(PACK_ALL, PACK_ACG | PACK_IT);
    let syls = ["ㄊㄨˊ", "ㄒㄧㄤˋ", "ㄕㄥ", "ㄔㄥˊ"];
    let (both, none) = (tempdir("it-both", Some(PACK)), tempdir("it-none", None));
    std::fs::write(both.join("it-add.tsv"), IT_PACK).unwrap();
    let plain = window_first(None, &syls);
    assert!(plain.iter().all(|w| w != "圖像生成"), "the word is already the first candidate without the pack: {plain:?}");
    // Bit clear with the file present, and the bit set with no file: both are the engine without the IT pack.
    assert_eq!(window_first(Some((&both, PACK_ACG)), &syls), plain, "it-add.tsv was read without the IT bit");
    assert_eq!(window_first(Some((&none, PACK_IT)), &syls), plain, "a missing it-add.tsv changes the engine");
    assert_eq!(window_first(Some((&both, PACK_IT)), &syls), vec!["圖像生成"; 2]);
    assert_eq!(window_first(Some((&both, PACK_ACG | PACK_IT)), &syls), vec!["圖像生成"; 2]);
    // The anime pack's word still works beside it.
    let acg = window_first(Some((&both, PACK_ACG | PACK_IT)), &["ㄉㄧㄥˋ", "ㄩㄢˊ", "ㄊㄤˊ"]);
    assert_eq!(acg, vec!["碇源堂"; 2]);
    for d in [both, none] {
        let _ = std::fs::remove_dir_all(d);
    }
}

#[test]
fn an_unknown_pack_bit_is_refused() {
    let d = tempdir("bit", None);
    assert!(matches!(Engine::new_with_packs(&root().join("data/lexicon"), L, Some((&d, 2))), Err(EngineError::LoadFailed)));
    let _ = std::fs::remove_dir_all(d);
}

/// model-v6 section 7 P2-3: the always-on names layer reaches the engine's capping text, not only the CLI's. Through the
/// shipping path (`Engine::new_with_packs`, then `load_lm`) with a data directory whose names-add.tsv holds one synthetic word
/// the corpus never saw: its lexicon score is the file score minus 1.0 (UNSEEN_OVERLAY_PENALTY), checked through
/// `total_score` against the model's own arithmetic. Without names-add.tsv the engine is not created.
#[test]
fn the_names_layer_is_capped_on_the_engine_path() {
    let d = tempdir("names", None);
    for f in ["mcbpmf-data.txt", "overlay-add.tsv", "sandhi-add.tsv", "demote.tsv"] {
        std::os::unix::fs::symlink(root().join("data/lexicon").join(f), d.join(f)).unwrap();
    }
    assert!(matches!(Engine::new_with_packs(&d, L, None), Err(EngineError::LoadFailed)), "a missing names-add.tsv must fail");
    std::fs::write(d.join("names-add.tsv"), "ㄒㄧㄣ-ㄅㄣ\t鑫犇\t-0.5\tnames\n").unwrap();
    let mut e = Engine::new_with_packs(&d, L, None).unwrap();
    e.load_lm(&lm_path()).unwrap();
    e.set_profile(Profile::Chat).unwrap();
    let mut out = None;
    for k in ["ㄒㄧㄣ", "ㄅㄣ"].iter().flat_map(|s| syl_keys(s)) {
        out = Some(e.key(k).unwrap());
    }
    assert_eq!(out.unwrap().preedit, "鑫犇", "the synthetic name must be the decode");
    let lm = core::lm::Lm::load(&lm_path()).unwrap();
    assert_eq!(lm.count("鑫犇"), 0, "the fixture word must be one the corpus never saw");
    let lam = Profile::Chat.lambda();
    let total = |lp: f64| lm.word(lam, "<s>", "鑫犇", lp) + lm.eos(lam, "鑫犇");
    let got = e.total_score().unwrap();
    assert!((got - total(-1.5)).abs() < 1e-12, "names row not capped: {got} vs {} (uncapped {})", total(-1.5), total(-0.5));
    let _ = std::fs::remove_dir_all(d);
}
