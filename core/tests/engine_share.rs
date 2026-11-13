//! The engine built from another engine's loaded parts (`Engine::share`, `Engine::new_shared`; docs/spec/c-abi.md §5.4) against the engine
//! built from the files (`new_with_packs` + `load_lm` + `load_tinylm` + `load_colloquial`): every key's whole output is the same, in decode,
//! candidate window (with its expansion) and prediction row. Real lexicon, models and packs (data/); fails loudly without them, never skips.
//! The new engine is built on another thread and used on this one, as the shell does.
use core::engine::*;
use core::lm::Profile;
use std::path::{Path, PathBuf};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}
fn data() -> PathBuf {
    root().join("data/lexicon")
}
fn packs() -> PathBuf {
    root().join("data/packs")
}
fn lm_dir() -> PathBuf {
    root().join("data/lm")
}
const L: Layout = Layout::Standard;

/// `new_with_packs`, `load_lm`, `load_tinylm`, `load_colloquial`: the order the shell builds in.
fn from_files(pack_dir: &Path, mask: u32) -> Engine {
    from_files_in(&data(), pack_dir, mask)
}

fn from_files_in(data: &Path, pack_dir: &Path, mask: u32) -> Engine {
    let mut e = Engine::new_with_packs(data, L, Some((pack_dir, mask))).unwrap();
    e.load_lm(&lm_dir().join("bigram.sjlm")).unwrap();
    e.load_tinylm(&lm_dir().join("tinylm.sjtl")).unwrap();
    e.load_colloquial(&lm_dir().join("colloquial.sjlm")).unwrap();
    e
}

/// `share` on this thread, `new_shared` on another, the engine handed back.
fn share_to(src: &Engine, pack_dir: &Path, mask: u32) -> Engine {
    let shared = src.share().expect("the source has a model");
    std::thread::scope(|s| s.spawn(|| Engine::new_shared(&shared, L, Some((pack_dir, mask))).unwrap()).join().unwrap())
}

fn is_tone(c: char) -> bool {
    "ˊˇˋ˙".contains(c)
}
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

/// (left context, syllables).
type Row = (String, Vec<String>);

fn three_field(text: &str) -> Vec<Row> {
    text.lines()
        .filter_map(|l| {
            let p: Vec<&str> = l.split('|').collect();
            (p.len() == 3).then(|| (p[0].to_string(), p[2].split_whitespace().map(String::from).collect()))
        })
        .collect()
}

fn file_rows(name: &str) -> Vec<Row> {
    three_field(&std::fs::read_to_string(root().join("eval/dev").join(name)).unwrap())
}

/// Smoke size (Windows; unoptimized builds skip the row comparisons altogether, see below). The sampled run (about 100 rows, 7 transitions plus a chain,
/// every profile and rerank setting) is the work of about three minutes in a release build on macOS. Unoptimized it is an order of magnitude more: even the
/// smoke subset took 451 s in the CI `debug` job, whose whole 20-minute limit the core tests then used up, so in a debug build (`debug_assertions`) the tests
/// that build engines from the files and compare rows are `#[ignore]`d (`cargo test -- --ignored` runs them); the cheap ones (the reading table, the
/// one-syllable pack, the missing and bad file paths, the shared parts' addresses) still run. The Windows job runs the release build with this smoke subset.
/// The full sampled comparison is the release build on macOS: `make test-rust` (`cargo test --release`) and the CI `core` job. The whole sets are
/// `SHANJIE_SHARE_STRIDE=1` in a release build.
fn smoke() -> bool {
    cfg!(debug_assertions) || cfg!(windows)
}

/// Every `STRIDE`-th row of a set (stride 8 in a non-Windows release build, 60 in the Windows smoke): the whole matrix of masks, profiles and rerank settings times every row of every set is the work of an hour, and
/// the mask changes only the lexicon, so a row's behavior is as likely to differ in any of them. `STRIDE` 1 is the whole set (SHANJIE_SHARE_STRIDE=1).
fn stride() -> usize {
    std::env::var("SHANJIE_SHARE_STRIDE").ok().and_then(|s| s.parse().ok()).unwrap_or(if smoke() { 60 } else { 8 })
}
fn every(rows: Vec<Row>, offset: usize) -> Vec<Row> {
    rows.into_iter().enumerate().filter(|(i, _)| (i + offset) % stride() == 0).map(|(_, r)| r).collect()
}

/// dev302 (the first 302 three-field rows of eval/dev/*.txt in name order, as `shanjie-eval --dev 302` reads them), the typing test, the reported typos.
fn sets() -> Vec<(&'static str, Vec<Row>)> {
    let mut files: Vec<_> = std::fs::read_dir(root().join("eval/dev")).unwrap().map(|e| e.unwrap().path()).filter(|p| p.extension().is_some_and(|x| x == "txt")).collect();
    files.sort();
    let mut dev: Vec<Row> = files.iter().flat_map(|f| three_field(&std::fs::read_to_string(f).unwrap())).collect();
    dev.truncate(302);
    vec![("dev302", every(dev, 0)), ("typing", every(file_rows("user-typing.txt"), 1)), ("reported", every(file_rows("user-reported.txt"), 2))]
}

/// Words of each pack (a row of its `*-add.tsv` every so often), typed as their reading: what a pack switch changes.
fn pack_rows() -> Vec<Row> {
    let mut rows = Vec::new();
    for (_, f) in PACK_FILES {
        let text = std::fs::read_to_string(packs().join(f)).unwrap();
        let ls: Vec<&str> = text.lines().collect();
        assert!(ls.len() > 12, "{f} has rows");
        let per_pack = if smoke() { 3 } else { 6 };
        let step = ls.len() / per_pack;
        for l in ls.iter().step_by(step).take(per_pack) {
            rows.push((String::new(), l.split('\t').next().unwrap().split('-').map(String::from).collect()));
        }
    }
    rows
}

fn k(kind: KeyKind) -> Key {
    Key::new(kind)
}

/// The whole output of every key of every row: decode (each key), the candidate window (Down, then the keys that expand it), and, for a row with a left
/// context, the prediction row (the pending ㄋ). With the rerank on, each key's job is run and applied before the next key, as the shell does.
fn run(e: &mut Engine, rows: &[Row], profile: Profile, rerank: bool) -> Vec<Output> {
    e.set_profile(profile).unwrap();
    e.set_rerank(rerank).unwrap();
    let mut outs = Vec::new();
    let push = |e: &mut Engine, o: Output, outs: &mut Vec<Output>| {
        outs.push(o);
        if rerank {
            if let Some(job) = e.take_rerank_job() {
                outs.extend(e.apply_rerank(job.run()).unwrap());
            }
        }
    };
    for (left, syls) in rows {
        e.reset(ResetMode::Discard);
        e.set_prediction(false).unwrap();
        e.set_left_context(left);
        for key in syls.iter().flat_map(|s| syl_keys(s)) {
            let o = e.key(key).unwrap();
            push(e, o, &mut outs);
        }
        for key in [KeyKind::Down, KeyKind::Down, KeyKind::Down] {
            let o = e.key(k(key)).unwrap();
            push(e, o, &mut outs);
        }
        if !left.is_empty() {
            e.reset(ResetMode::Discard);
            e.set_prediction(true).unwrap();
            e.set_left_context(left);
            let o = e.key(Key::ch(L.key_of_symbol('ㄋ').unwrap(), 0)).unwrap();
            push(e, o, &mut outs);
        }
    }
    e.reset(ResetMode::Discard);
    outs
}

fn all_rows() -> Vec<Row> {
    let mut rows = pack_rows();
    for (_, r) in sets() {
        rows.extend(r);
    }
    rows
}

/// What the new engine must equal: the output of the engine built from the files at the same mask, in both profiles and with the rerank off and on.
fn assert_same(shared: &mut Engine, files: &mut Engine, rows: &[Row], what: &str) {
    for profile in [Profile::Chat, Profile::Formal] {
        for rerank in [false, true] {
            let (a, b) = (run(shared, rows, profile, rerank), run(files, rows, profile, rerank));
            assert_eq!(a.len(), b.len(), "{what}: {profile:?} rerank {rerank}: output count");
            if let Some(i) = (0..a.len()).find(|&i| a[i] != b[i]) {
                panic!("{what}: {profile:?} rerank {rerank}: output {i} of {} differs (preedit {:?} / {:?})", a.len(), a[i].preedit.chars().count(), b[i].preedit.chars().count());
            }
        }
    }
}

/// Which parts are shared and which built again, as addresses against the source engine's.
fn assert_parts(src: &Engine, new: &Engine, reading_shared: bool, what: &str) {
    let (s, n) = (src.part_ptrs(), new.part_ptrs());
    assert!(n.lm != 0 && n.lm == s.lm, "{what}: the n-gram model is shared");
    assert!(n.tinylm != 0 && n.tinylm == s.tinylm, "{what}: the small model is shared");
    assert!(n.colloquial != 0 && n.colloquial == s.colloquial, "{what}: the colloquial table is shared");
    assert!(n.digit_table != 0 && n.digit_table == s.digit_table, "{what}: the digit table is shared");
    assert!(n.en_table == s.en_table, "{what}: the English follower table is shared (or absent in both)");
    assert!(n.reading != 0, "{what}: the new engine has a reading table");
    assert_eq!(n.reading == s.reading, reading_shared, "{what}: the reading table is {}", if reading_shared { "shared" } else { "built again" });
}

const TRANSITIONS: [(u32, u32); 7] = [(29, 28), (28, 29), (29, 31), (31, 0), (0, 29), (29, 1), (1, 16)];
const SMOKE_TRANSITIONS: [(u32, u32); 2] = [(29, 28), (31, 0)];

#[test]
#[cfg_attr(debug_assertions, ignore = "builds engines from the files and compares rows: far too slow unoptimized; the release core job (macOS) and make test-rust run it")]
fn the_shared_engine_equals_the_engine_built_from_the_files() {
    let rows = all_rows();
    assert!(rows.len() >= if smoke() { 5 } else { 90 }, "the check rows: {}", rows.len());
    let transitions: &[(u32, u32)] = if smoke() { &SMOKE_TRANSITIONS } else { &TRANSITIONS };
    for &(a, b) in transitions {
        let src = from_files(&packs(), a);
        let mut shared = share_to(&src, &packs(), b);
        assert_parts(&src, &shared, true, &format!("{a}->{b}"));
        drop(src);
        // Built per transition, not kept per mask: at most three engines are alive in this test (memory, with the other tests running beside it).
        assert_same(&mut shared, &mut from_files(&packs(), b), &rows, &format!("{a}->{b}"));
    }
}

#[test]
#[cfg_attr(debug_assertions, ignore = "builds engines from the files and compares rows: far too slow unoptimized; the release core job (macOS) and make test-rust run it")]
fn consecutive_switches_each_equal_the_engine_built_from_the_files() {
    let rows = all_rows();
    let masks: &[u32] = if smoke() { &[29, 28, 31] } else { &[29, 28, 29, 31, 0, 29] };
    let mut cur = from_files(&packs(), masks[0]);
    for w in masks.windows(2) {
        let mut next = share_to(&cur, &packs(), w[1]);
        assert_parts(&cur, &next, true, &format!("{}->{}", w[0], w[1]));
        drop(cur);
        assert_same(&mut next, &mut from_files(&packs(), w[1]), &rows, &format!("chain {}->{}", w[0], w[1]));
        cur = next;
    }
}

/// Each pack's check word is the first candidate when its bit is on and not when off, so the rows above do test a pack switch (a shared engine that kept the
/// old mask's words would fail here as well as above).
#[test]
#[cfg_attr(debug_assertions, ignore = "builds engines from the files and compares rows: far too slow unoptimized; the release core job (macOS) and make test-rust run it")]
fn the_check_words_of_a_pack_follow_the_mask() {
    let src = from_files(&packs(), 31);
    let words = pack_rows();
    let top = |e: &mut Engine, r: &Row| {
        e.reset(ResetMode::Discard);
        e.set_prediction(false).unwrap();
        e.set_left_context("");
        let mut o = None;
        for key in r.1.iter().flat_map(|s| syl_keys(s)) {
            o = Some(e.key(key).unwrap());
        }
        o.unwrap().preedit
    };
    let mut off = share_to(&src, &packs(), 0);
    let mut on = share_to(&src, &packs(), 31);
    let differing = words.iter().filter(|r| top(&mut off, r) != top(&mut on, r)).count();
    assert!(differing >= 5, "the packs' check words change the decode: {differing} of {}", words.len());
}

// ---- the reading table (docs/spec/decoder.md §5) ----

fn lexicon_of(pack_dir: &Path, mask: u32) -> std::sync::Arc<core::Lexicon> {
    load_lexicon_packs(&data(), Some((pack_dir, mask))).unwrap().0
}

/// A table of the base reading list alone queried over the lexicon of `mask`, against the table built from the files at that mask: the same value for every
/// (character, reading) pair, and the same tag.
#[test]
fn a_reused_reading_table_answers_every_single_syllable_entry_as_the_built_one() {
    let src = from_files(&packs(), 0);
    let shared = src.share().unwrap();
    let rd = shared.reading_table().expect("a table");
    for mask in [0, 1, 29, 31] {
        let lex = lexicon_of(&packs(), mask);
        let built = load_reading_decode(&lex, &data()).unwrap();
        let (a, b) = (rd.dump_table(&lex), built.dump_table(&lex));
        assert!(a.lines().count() > 1000, "mask {mask}: the table has pairs ({})", a.lines().count());
        assert!(a == b, "mask {mask}: the reused table differs from the built one");
        assert_eq!(rd.tag(), built.tag());
    }
}

/// A pack file with a one-syllable reading (a new reading for a character the base has): the table is built again, not reused, and the outputs equal
/// the files' (the premise: the table does change, so a reused one would answer wrongly).
#[test]
fn a_pack_with_a_one_syllable_reading_rebuilds_the_reading_table() {
    let dir = std::env::temp_dir().join(format!("shanjie-share-test-{}-packs", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    // 妳 read ㄌㄧˇ at the floor score, and a two-syllable word so the pack is not only that row.
    std::fs::write(dir.join("acg-add.tsv"), "ㄌㄧˇ\t妳\t-6.8\tacg\nㄉㄧㄥˋ-ㄩㄢˊ-ㄊㄤˊ\t碇源堂\t-7.04116568\tacg\n").unwrap();
    // Source without a pack, target with one; and the other way round.
    for (a, b) in [(0u32, 1u32), (1, 0), (1, 1)] {
        let src = from_files(&dir, a);
        let mut shared = share_to(&src, &dir, b);
        let n = shared.part_ptrs();
        assert!(n.lm == src.part_ptrs().lm, "the model is shared");
        assert!(n.reading != src.part_ptrs().reading, "{a}->{b}: the reading table is built again");
        let mut files = from_files(&dir, b);
        let mut rows = vec![(String::new(), vec!["ㄌㄧˇ".to_string()]), (String::new(), vec!["ㄉㄧㄥˋ".into(), "ㄩㄢˊ".into(), "ㄊㄤˊ".into()])];
        rows.extend(all_rows().into_iter().take(if smoke() { 6 } else { 40 }));
        assert_same(&mut shared, &mut files, &rows, &format!("one-syllable pack {a}->{b}"));
    }
    // The premise: the table of the lexicon with that row differs from the table of the lexicon without it.
    let (without, with) = (lexicon_of(&dir, 0), lexicon_of(&dir, 1));
    let (t0, t1) = (load_reading_decode(&without, &data()).unwrap(), load_reading_decode(&with, &data()).unwrap());
    assert!(t0.dump_table(&with) != t1.dump_table(&with), "premise: the pack's row changes the reading table");
    let _ = std::fs::remove_dir_all(&dir);
}

/// The reading table is reused only when neither side's packs hold a one-syllable row: the shipping packs have none.
#[test]
fn the_shipping_packs_have_no_one_syllable_row() {
    for (_, f) in PACK_FILES {
        let text = std::fs::read_to_string(packs().join(f)).unwrap();
        assert!(text.lines().all(|l| l.split('\t').next().unwrap().contains('-')), "{f} has a one-syllable row: reading tables are then built again at every switch");
    }
}

// ---- failure: nothing half-built, and the source engine is not touched ----

fn data_copy(name: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("shanjie-share-test-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    for f in std::fs::read_dir(data()).unwrap() {
        let f = f.unwrap();
        if f.file_type().unwrap().is_file() {
            std::fs::copy(f.path(), d.join(f.file_name())).unwrap();
        }
    }
    d
}

#[test]
fn a_missing_late_file_fails_the_whole_build_and_it_loads_again_once_the_file_is_back() {
    let dir = data_copy("late");
    let mut src = Engine::new_with_packs(&dir, L, Some((&packs(), 29))).unwrap();
    src.load_lm(&lm_dir().join("bigram.sjlm")).unwrap();
    src.load_tinylm(&lm_dir().join("tinylm.sjtl")).unwrap();
    let shared = src.share().unwrap();
    let keep = std::fs::read(dir.join(READING_EXEMPT_FILE)).unwrap();
    std::fs::remove_file(dir.join(READING_EXEMPT_FILE)).unwrap();
    for mask in [0, 29] {
        assert!(Engine::new_shared(&shared, L, Some((&packs(), mask))).is_err(), "mask {mask}: the lexicon builds, the reading step fails, no engine");
    }
    std::fs::write(dir.join(READING_EXEMPT_FILE), keep).unwrap();
    let mut e = Engine::new_shared(&shared, L, Some((&packs(), 29))).unwrap();
    let rows = pack_rows();
    let a = run(&mut e, &rows, Profile::Chat, false);
    assert!(a.iter().any(|o| !o.preedit.is_empty()), "the engine types");
    let _ = std::fs::remove_dir_all(&dir);
}

/// The English follower table (`english-next.tsv`, optional, not in the shipped data folder yet): shared when the source loaded one.
#[test]
#[cfg_attr(debug_assertions, ignore = "builds engines from the files and compares rows: far too slow unoptimized; the release core job (macOS) and make test-rust run it")]
fn the_english_follower_table_is_shared() {
    let dir = data_copy("english");
    std::fs::write(dir.join("english-next.tsv"), "E\tiphone\t10\nW\tiphone\t手機\t5\n").unwrap();
    let src = from_files_in(&dir, &packs(), 29);
    assert!(src.part_ptrs().en_table != 0, "premise: the source loaded the table");
    let mut shared = share_to(&src, &packs(), 28);
    assert!(shared.part_ptrs().en_table == src.part_ptrs().en_table, "the table is the source's, not read again");
    let mut files = from_files_in(&dir, &packs(), 28);
    let rows: Vec<Row> = all_rows().into_iter().take(if smoke() { 6 } else { 30 }).collect();
    assert_same(&mut shared, &mut files, &rows, "english table");
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn a_bad_exempt_list_fails_a_build_that_would_have_reused_the_table() {
    let dir = data_copy("exempt");
    let mut src = Engine::new_with_packs(&dir, L, Some((&packs(), 0))).unwrap();
    src.load_lm(&lm_dir().join("bigram.sjlm")).unwrap();
    let shared = src.share().unwrap();
    assert!(Engine::new_shared(&shared, L, Some((&packs(), 0))).is_ok());
    std::fs::write(dir.join(READING_EXEMPT_FILE), "ㄅ\tab\n").unwrap();
    assert!(Engine::new_shared(&shared, L, Some((&packs(), 0))).is_err());
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn the_shared_parts_outlive_the_source_engine() {
    let src = from_files(&packs(), 29);
    let shared = src.share().unwrap();
    drop(src);
    let mut e = Engine::new_shared(&shared, L, Some((&packs(), 28))).unwrap();
    let rows = pack_rows();
    assert!(run(&mut e, &rows, Profile::Chat, true).iter().any(|o| !o.preedit.is_empty()));
}

#[test]
fn an_engine_without_a_model_shares_nothing() {
    let e = Engine::new_with_packs(&data(), L, None).unwrap();
    assert!(e.share().is_none());
}

/// The compile-time rules (docs/spec/c-abi.md §5.2): an engine moves between threads, the shared parts are read from several.
#[test]
fn engine_is_send_and_the_shared_parts_are_send_and_sync() {
    fn send<T: Send>() {}
    fn send_sync<T: Send + Sync>() {}
    send::<Engine>();
    send_sync::<Shared>();
}
