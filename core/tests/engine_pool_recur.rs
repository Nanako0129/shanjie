//! The context recurrence weight on the semantic correction's pool in the engine (docs/spec/semantic-rerank.md §3.1.1): the real lexicon, n-gram
//! model, small model and colloquial table (data/lm/, fail loudly without them), the App's settings (rerank on, standard level), the job run and applied
//! after every key as the replay's `each`. The fixture is ㄕㄣˊ ㄇㄧˋ ㄧㄝˇ ㄘㄨㄛˋ ㄗˋ after a left text of 44 characters that ends in one holding 神祕 and
//! 錯字: with the weight 神祕也錯字 stays, without it the small model takes 神秘也錯字 (the
//! decoder's first is 神祕也錯字 in chat and 神秘也錯字 in formal).
use core::engine::*;
use core::lm::Profile;
use std::path::{Path, PathBuf};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}
fn data(f: &str) -> PathBuf {
    let p = root().join("data/lm").join(f);
    assert!(p.exists(), "data/lm/{f} is needed: see data/*.sha256 and docs/releases/README.md");
    p
}

const LEFT: &str = "我們今天下午在會議室討論了很多事情最後大家都同意先這樣做。錯字，這是不是神祕模型太爛啊（";
const READING: &str = "ㄕㄣˊ ㄇㄧˋ ㄧㄝˇ ㄘㄨㄛˋ ㄗˋ";
const PROFILES: [(Profile, &str); 2] = [(Profile::Chat, "chat"), (Profile::Formal, "formal")];
/// What the fixture shows where the weight does not act, per profile (chat, formal): with the weight off, with `set_recur(false)`, with the left text cut to
/// its last 31 characters, and with no small model. Written from the evaluation CLI's `--picks` (not from the engine), measured 2026-11-23 on the 會意
/// SJ-SR-10-31M file: `target/release/shanjie-eval --lm data/lm/bigram.sjlm --profile P --context --tinylm data/lm/tinylm.sjtl [--colloquial-core
/// data/lm/colloquial.sjlm in chat] --rows <the fixture row> --picks OUT` with `--no-pool-recur`, with `--no-recur`, on the 31-character row, and without
/// `--tinylm`. (Until 會意 SJ-SR-10-31M the fixture was 語義也錯字 against 語意也錯字 and the constants were the merge base's, 7d12b7d1, from before the term; the new
/// model reads the left text and keeps 語義 in chat without the weight. With 神祕 the model takes 神秘 back whenever
/// the weight is off, also when it reads the left text that holds 神祕.)
const BASE_DEFAULT: [&str; 2] = ["神秘也錯字", "神秘也錯字"];
const BASE_NO_RECUR: [&str; 2] = ["神秘也錯字", "神秘也錯字"];
const BASE_CUT_31: [&str; 2] = ["神秘也錯字", "神秘也錯字"];
const BASE_NO_TINYLM: [&str; 2] = ["神祕也錯字", "神秘也錯字"];

fn keys_of(syl: &str) -> Vec<Key> {
    let layout = Layout::Standard;
    let (mut v, mut toned) = (Vec::new(), false);
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
/// The App's engine (packs aside): n-gram, small model and colloquial table, the profile, rerank on.
fn engine(profile: Profile, tinylm: bool) -> Engine {
    let mut e = Engine::new(&root().join("data/lexicon"), Layout::Standard).unwrap();
    e.load_lm(&data("bigram.sjlm")).unwrap();
    if tinylm {
        e.load_tinylm(&data("tinylm.sjtl")).unwrap();
        e.load_colloquial(&data("colloquial.sjlm")).unwrap();
    }
    e.set_profile(profile).unwrap();
    e.set_rerank(true).unwrap();
    e
}
/// Types `reading` key by key, running and applying the job after every key; (what is shown, the pool weights of the last job).
fn type_reading(e: &mut Engine, reading: &str) -> (String, Vec<Vec<f64>>) {
    let (mut shown, mut bonus) = (String::new(), Vec::new());
    for k in reading.split(' ').flat_map(keys_of) {
        shown = e.key(k).unwrap().preedit;
        if let Some(job) = e.take_rerank_job() {
            bonus = job.pool_bonus();
            if let Some(o) = e.apply_rerank(job.run()).unwrap() {
                shown = o.preedit;
            }
        }
    }
    (shown, bonus)
}
/// Sets the left text, types the reading and commits; what was committed.
fn shown(e: &mut Engine, left: &str, reading: &str) -> String {
    e.set_left_context(left);
    let (shown, _) = type_reading(e, reading);
    assert_eq!(e.reset(ResetMode::Commit).commit, shown);
    shown
}
fn last(s: &str, n: usize) -> String {
    s.chars().skip(s.chars().count() - n).collect()
}
fn no_weight(bonus: &[Vec<f64>]) -> bool {
    !bonus.is_empty() && bonus.iter().flatten().all(|&b| b == 0.0)
}

/// The four settings where the weight does not act show what the merge base showed (the committed constants); the premise is that the same
/// fixture changes with the weight on.
#[test]
fn where_the_weight_does_not_act_the_choice_is_the_merge_bases() {
    assert_eq!(LEFT.chars().count(), 44);
    let cut = last(LEFT, 31);
    for (i, (p, name)) in PROFILES.into_iter().enumerate() {
        let on = shown(&mut engine(p, true), LEFT, READING);
        assert_ne!(on, BASE_DEFAULT[i], "{name}: premise, the weight changes the choice");
        assert_eq!(on, "神祕也錯字", "{name}");
        let mut e = engine(p, true);
        e.set_pool_recur(false);
        assert_eq!(shown(&mut e, LEFT, READING), BASE_DEFAULT[i], "{name}: set_pool_recur(false)");
        let mut e = engine(p, true);
        e.set_recur(false);
        assert_eq!(shown(&mut e, LEFT, READING), BASE_NO_RECUR[i], "{name}: set_recur(false)");
        assert_eq!(shown(&mut engine(p, true), &cut, READING), BASE_CUT_31[i], "{name}: 31 characters");
        assert_eq!(shown(&mut engine(p, false), LEFT, READING), BASE_NO_TINYLM[i], "{name}: no small model");
    }
}

/// The threshold counts Unicode scalar values after the 64 cut: 神祕 and 30 ASCII letters acts, with 29 it does not; 40 ASCII letters hold no pair; 神祕
/// and six family emoji (five scalar values each, eight characters on screen) acts, five and four letters (31) do not. "Does not act" is what the engine
/// with `set_pool_recur(false)` shows, and the job's weights are all 0.
#[test]
fn the_threshold_counts_scalar_values() {
    let fam = "\u{1F468}\u{200D}\u{1F469}\u{200D}\u{1F467}";
    let cases = [
        (format!("神祕{}", "a".repeat(30)), true),
        (format!("神祕{}", "a".repeat(29)), false),
        ("a".repeat(40), false),
        (format!("神祕{}", fam.repeat(6)), true),
        (format!("神祕{}aaaa", fam.repeat(5)), false),
    ];
    for (p, name) in PROFILES {
        for (left, acts) in &cases {
            let run = |pool: bool| {
                let mut e = engine(p, true);
                e.set_pool_recur(pool);
                e.set_left_context(left);
                type_reading(&mut e, READING)
            };
            let ((on, bonus), (off, _)) = (run(true), run(false));
            assert_eq!(on != off, *acts, "{name} {} scalar values: {on} {off}", left.chars().count());
            assert_eq!(!no_weight(&bonus), *acts, "{name}: the job's weights {bonus:?}");
        }
    }
}

/// Every call replaces the set and its length: a ≥ 32 left text and then a < 32 one does not act; then the ≥ 32 one again acts.
#[test]
fn a_shorter_left_text_replaces_a_longer_one() {
    let cut = last(LEFT, 31);
    for (p, name) in PROFILES {
        let mut e = engine(p, true);
        e.set_left_context(LEFT);
        e.set_left_context(&cut);
        let (s, bonus) = type_reading(&mut e, READING);
        assert!(s != "神祕也錯字" && no_weight(&bonus), "{name}: {s} {bonus:?}");
        e.reset(ResetMode::Discard);
        e.set_left_context(&cut);
        e.set_left_context(LEFT);
        assert_eq!(type_reading(&mut e, READING).0, "神祕也錯字", "{name}: premise, the long one acts");
    }
}

/// After every clear point (a commit, Esc, both resets, two pass-throughs: rule 22 and key scheme 1's Shift+, on nothing) and on a composition
/// started without a fresh left text, the next composition shows what the engine with the weight off shows and its jobs carry no weight. (Code 4 and the
/// C ABI's NULL, "" and non-UTF-8 left texts: core/src/ffi.rs.)
#[test]
fn after_every_clear_point_the_weight_does_not_act() {
    let p = Profile::Formal;
    let off = {
        let mut e = engine(p, true);
        e.set_pool_recur(false);
        type_reading(&mut e, READING).0
    };
    let clears: [(&str, &dyn Fn(&mut Engine)); 7] = [
        ("commit", &|e| {
            type_reading(e, READING);
            assert!(!e.key(Key::new(KeyKind::Enter)).unwrap().commit.is_empty());
        }),
        ("Esc", &|e| {
            type_reading(e, READING);
            e.key(Key::new(KeyKind::Esc)).unwrap();
        }),
        ("reset commit", &|e| {
            e.key(Key::ch('g', 0)).unwrap();
            e.reset(ResetMode::Commit);
        }),
        ("reset discard", &|e| {
            e.key(Key::ch('g', 0)).unwrap();
            e.reset(ResetMode::Discard);
        }),
        ("rule 22", &|e| {
            let o = e.key(Key::ch('é', 0)).unwrap();
            assert!(!o.handled && o.preedit.is_empty(), "premise: rule 22");
        }),
        ("K8", &|e| {
            e.set_key_scheme(1).unwrap();
            let o = e.key(Key::ch(',', MOD_SHIFT)).unwrap();
            assert!(!o.handled && o.preedit.is_empty(), "premise: K8 passes on");
            e.set_key_scheme(0).unwrap();
        }),
        ("no fresh left text", &|e| {
            // ㄈ then Backspace: the composition is empty again without a set_left_context after it.
            e.key(Key::ch('z', 0)).unwrap();
            e.key(Key::new(KeyKind::Backspace)).unwrap();
        }),
    ];
    for (what, clear) in clears {
        let mut e = engine(p, true);
        e.set_left_context(LEFT);
        clear(&mut e);
        let (s, bonus) = type_reading(&mut e, READING);
        assert_eq!(s, off, "{what}");
        assert!(no_weight(&bonus), "{what}: {bonus:?}");
        // Premise: the same engine with the left text set again acts.
        e.reset(ResetMode::Discard);
        assert_eq!(shown(&mut e, LEFT, READING), "神祕也錯字", "{what}: premise");
    }
}

/// A new small model in the middle of a composition keeps the set (as for the decoder's term): the weight still acts.
#[test]
fn a_model_change_while_composing_keeps_the_weight() {
    for (i, (p, name)) in PROFILES.into_iter().enumerate() {
        let mut e = engine(p, true);
        e.set_left_context(LEFT);
        let syls: Vec<&str> = READING.split(' ').collect();
        type_reading(&mut e, &syls[..2].join(" "));
        e.load_tinylm(&data("tinylm.sjtl")).unwrap();
        let (s, bonus) = type_reading(&mut e, &syls[2..].join(" "));
        assert_eq!(s, "神祕也錯字", "{name}");
        assert!(!no_weight(&bonus), "{name}");
        assert_ne!(s, BASE_DEFAULT[i], "premise: {name}");
    }
}

/// The job's accessors keep the decoder's order and `pool_order` is a permutation of it whose first is not the decoder's first in formal (the
/// small model's pool order: 神祕也錯字 is not the decoder's first).
#[test]
fn the_job_keeps_the_decoders_order() {
    let mut e = engine(Profile::Formal, true);
    e.set_left_context(LEFT);
    let mut job = None;
    for k in READING.split(' ').flat_map(keys_of) {
        e.key(k).unwrap();
        job = e.take_rerank_job().or(job);
    }
    let job = job.expect("a job");
    let (cands, ng, order, bonus) = (job.candidates(), job.ng_scores(), job.pool_order(), job.pool_bonus());
    assert_eq!(cands.len(), 1);
    let mut sorted = order[0].clone();
    sorted.sort();
    assert_eq!(sorted, (0..cands[0].len()).collect::<Vec<_>>(), "a permutation");
    assert!(ng[0].windows(2).all(|w| w[0] >= w[1]), "the decoder's scores, best first: {:?}", ng[0]);
    assert_eq!(cands[0][order[0][0]], "神祕也錯字");
    assert_ne!(order[0][0], 0);
    let s: Vec<f64> = order[0].iter().map(|&i| ng[0][i] + bonus[0][i]).collect();
    assert!(s.windows(2).all(|w| w[0] >= w[1]), "the order sorts ng + bonus");
    assert_eq!(job.sinit_weights()[0].len(), cands[0].len());
}
