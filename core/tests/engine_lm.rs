//! S2c engine tests (docs/PLAN.md S2c acceptance 4 and 5): replay with the bigram model, fixed words.
//! Everything here needs data/lm/bigram.sjlm and fails loudly without it (never skips).
use core::engine::*;
use core::eval::{parse_rows, usable};
use core::lm::{decode, decode_segment, CappedLexicon, End, Lm, Profile};
use core::{Lexicon, Syls};
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};
use std::time::{Duration, Instant};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}
fn lm_path() -> PathBuf {
    let p = root().join("data/lm/bigram.sjlm");
    assert!(p.exists(), "data/lm/bigram.sjlm is missing: download it with `gh release download model-v2 -R Nanako0129/shanjie -p bigram.sjlm -D data/lm` (or rebuild with tools/build_lm.py; see docs/PLAN.md S2c)");
    p
}

struct Shared {
    lex: Arc<Lexicon>,
    lm: Arc<Lm>,
    capped: Arc<CappedLexicon>,
}
/// Loaded once per test binary; engines get the same objects through `set_lm`.
fn shared() -> &'static Shared {
    static S: OnceLock<Shared> = OnceLock::new();
    S.get_or_init(|| {
        let lex = load_lexicon(&root().join("data/lexicon")).unwrap();
        let lm = Lm::load(&lm_path()).unwrap();
        let overlay = std::fs::read_to_string(root().join("data/lexicon/overlay-add.tsv")).unwrap();
        let capped = Arc::new(CappedLexicon::new(lex.clone(), &overlay, &lm));
        Shared { lex, lm: Arc::new(lm), capped }
    })
}
fn engine(layout: Layout, profile: Profile) -> Engine {
    let s = shared();
    let mut e = Engine::with_lexicon(s.lex.clone(), layout);
    e.set_lm(s.lm.clone(), s.capped.clone());
    e.set_profile(profile).unwrap();
    e
}

/// Reading -> key presses (same as engine_replay.rs).
fn keys_of(layout: Layout, syl: &str) -> Vec<Key> {
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

/// The 302 rows of `--set dev --limit 302` as (sentence, reading).
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

/// Column of eval/golden/s2-lm-dev302-top1.tsv (Python output): 0 chat, 1 formal.
fn golden_top1(col: usize) -> Vec<String> {
    let t = std::fs::read_to_string(root().join("eval/golden/s2-lm-dev302-top1.tsv")).unwrap();
    let v: Vec<String> = t.lines().filter(|l| !l.starts_with('#')).map(|l| l.split('\t').nth(col).unwrap().to_string()).collect();
    assert!(v.len() == 302);
    v
}

/// Type every row and press Enter; returns the committed strings and every key's latency.
fn replay(e: &mut Engine, layout: Layout) -> (Vec<String>, Vec<Duration>) {
    let (mut got, mut times) = (Vec::new(), Vec::new());
    for (_, syls) in dev302() {
        let mut keys: Vec<Key> = syls.iter().flat_map(|s| keys_of(layout, s)).collect();
        keys.push(Key::new(KeyKind::Enter));
        let mut s = String::new();
        for key in keys {
            let t = Instant::now();
            let o = e.key(key).unwrap();
            times.push(t.elapsed());
            s.push_str(&o.commit);
        }
        got.push(s);
    }
    (got, times)
}

fn check_replay(got: &[String], want: &[String]) {
    let diff: Vec<usize> = (0..302).filter(|&i| got[i] != want[i]).map(|i| i + 1).collect();
    assert!(diff.is_empty(), "replay differs from the Python top-1 at rows {diff:?}");
}

fn p95(mut t: Vec<Duration>) -> Duration {
    t.sort();
    let p = t[t.len() * 95 / 100];
    println!("keys {} p95 {:?} max {:?}", t.len(), p, t[t.len() - 1]);
    p
}

/// Acceptance 4, production path: `Engine::new` + `load_lm` (the capped lexicon is built inside).
#[test]
fn replay_standard_chat_production_path() {
    let t = Instant::now();
    let mut e = Engine::new(&root().join("data/lexicon"), Layout::Standard).unwrap();
    e.load_lm(&lm_path()).unwrap();
    println!("engine new + load_lm: {:?}", t.elapsed());
    let (got, times) = replay(&mut e, Layout::Standard);
    check_replay(&got, &golden_top1(0));
    #[cfg(not(debug_assertions))]
    assert!(p95(times) < Duration::from_millis(16), "p95 over 16 ms");
    #[cfg(debug_assertions)]
    let _ = p95(times);
}

#[test]
fn replay_standard_formal() {
    let mut e = engine(Layout::Standard, Profile::Formal);
    check_replay(&replay(&mut e, Layout::Standard).0, &golden_top1(1));
}

#[test]
fn replay_eten_chat() {
    let mut e = engine(Layout::Eten, Profile::Chat);
    check_replay(&replay(&mut e, Layout::Eten).0, &golden_top1(0));
}

// ---------- acceptance 5: fixed words ----------

/// Type `syls`, put the cursor after syllable `end`, open the candidates and choose `word`.
/// Returns the snapshot after the choice. The candidate list is the S3a one (original lexicon).
fn fix_word(e: &mut Engine, layout: Layout, syls: &Syls, end: usize, word: &str) -> Output {
    for syl in syls {
        for k in keys_of(layout, syl) {
            e.key(k).unwrap();
        }
    }
    for _ in 0..syls.len() - end {
        e.key(Key::new(KeyKind::Left)).unwrap();
    }
    let mut o = e.key(Key::new(KeyKind::Space)).unwrap();
    for _ in 0..5000 {
        if o.candidates[o.selected.unwrap()] == word {
            return e.key(Key::new(KeyKind::Enter)).unwrap();
        }
        o = e.key(Key::new(KeyKind::Right)).unwrap();
    }
    panic!("candidate not found");
}

/// Whole-sentence LM top-1: (surface, score, words with their (start, end) syllable spans).
fn best_path(syls: &Syls, profile: Profile) -> (String, f64, Vec<(String, usize, usize)>) {
    let s = shared();
    let (score, words) = decode(&s.capped, syls, &s.lm, profile, 64).unwrap().swap_remove(0);
    let mut pos = 0;
    let spans = words
        .iter()
        .map(|w| {
            let l = w.chars().count();
            pos += l;
            (w.clone(), pos - l, pos)
        })
        .collect();
    (words.concat(), score, spans)
}

fn row(n: usize) -> (String, Syls) {
    dev302().swap_remove(n - 1)
}

/// 5(a), overlay fixed word: the cap really lowers its score, and the engine's total path score with the
/// fixed word equals the whole-sentence top-1 score (<= 1e-9) with the same string. Using the raw lexicon
/// score for `lp_F` instead of the capped one makes `total_score` differ, so this fails.
#[test]
fn fixed_overlay_word_total_equals_whole_sentence_top1() {
    let s = shared();
    // rows whose chat top-1 contains an overlay word: 洗衣球, 隔熱墊, 防滑墊, 手搖杯, 空氣炸鍋
    for (n, word) in [(276, "洗衣球"), (278, "隔熱墊"), (281, "防滑墊"), (290, "手搖杯"), (292, "空氣炸鍋")] {
        let (_, syls) = row(n);
        let (surface, score, spans) = best_path(&syls, Profile::Chat);
        let &(_, a, b) = spans.iter().find(|(w, _, _)| w == word).expect("word is in the top-1 path");
        let key = &syls[a..b];
        let raw = s.lex.entries(key).iter().filter(|(w, _)| *w == word).map(|x| x.1).fold(f64::MIN, f64::max);
        let capped = s.capped.best_lp(key, word).unwrap();
        assert!(raw - capped > 0.1, "the cap must change this word's score (row {n})");
        let mut e = engine(Layout::Standard, Profile::Chat);
        let o = fix_word(&mut e, Layout::Standard, &syls, b, word);
        assert!(o.preedit == surface, "string differs at row {n}");
        let total = e.total_score().unwrap();
        assert!((total - score).abs() <= 1e-9, "total {total} vs top-1 {score} at row {n}");
    }
}

/// 5(a), breadth: every word of the top-1 path of the first rows, both profiles. A beam of 64 is an
/// approximation, so a row may legitimately differ; at most 2 of the checked words may.
#[test]
fn fixed_word_total_equals_top1_on_first_rows() {
    let (mut ok, mut bad) = (0, 0);
    for profile in [Profile::Chat, Profile::Formal] {
        for n in 1..=12 {
            let (_, syls) = row(n);
            let (surface, score, spans) = best_path(&syls, profile);
            for (w, a, b) in spans.iter().filter(|(_, a, b)| !(*a == 0 && *b == syls.len())) {
                let _ = a;
                let mut e = engine(Layout::Standard, profile);
                let o = fix_word(&mut e, Layout::Standard, &syls, *b, w);
                if o.preedit == surface && (e.total_score().unwrap() - score).abs() <= 1e-9 {
                    ok += 1;
                } else {
                    bad += 1;
                }
            }
        }
    }
    assert!(ok > 50 && bad <= 2, "ok {ok} bad {bad}");
}

/// 5(b): the transition into the right fixed word changes the left stretch. Chat row 14 fixes 一次 on
/// the right; with the sentence-end term instead of the transition the left stretch would be 請你在說.
#[test]
fn right_fixed_word_transition_changes_left_choice() {
    let s = shared();
    for (n, profile, word, with_t, with_eos) in [
        (14, Profile::Chat, "一次", "請你再說", "請你在說"),
        (10, Profile::Formal, "報告", "期中", "其中"),
    ] {
        let (_, syls) = row(n);
        let (surface, _, spans) = best_path(&syls, profile);
        let &(_, a, b) = spans.iter().find(|(w, _, _)| w == word).unwrap();
        // The premise, computed straight from the decoder: the two endings disagree.
        let lam = profile.lambda();
        let lp = s.capped.best_lp(&syls[a..b], word).unwrap();
        let top = |end| {
            let r = decode_segment(&s.capped, &syls[..a], &s.lm, lam, "<s>", end, 64).unwrap();
            r[0].1.iter().map(|x| x.0).collect::<String>()
        };
        assert_eq!(top(End::Next { word, lp }), with_t);
        assert_eq!(top(End::Eos), with_eos);
        let mut e = engine(Layout::Standard, profile);
        let o = fix_word(&mut e, Layout::Standard, &syls, b, word);
        assert!(o.preedit.starts_with(with_t) && !o.preedit.starts_with(with_eos), "row {n}");
        assert!(o.preedit == surface, "row {n}");
    }
}

// ---------- engine state ----------

fn type_row(e: &mut Engine, layout: Layout, syls: &Syls) -> Output {
    let mut last = None;
    for syl in syls {
        for k in keys_of(layout, syl) {
            last = Some(e.key(k).unwrap());
        }
    }
    last.unwrap()
}

/// Acceptance 6 sentence (dev302 row 10): unigram, chat and formal top-1 are pairwise different.
#[test]
fn acceptance6_sentence_has_three_different_top1() {
    let (truth, syls) = row(10);
    let uni = type_row(&mut Engine::with_lexicon(shared().lex.clone(), Layout::Standard), Layout::Standard, &syls).preedit;
    let chat = type_row(&mut engine(Layout::Standard, Profile::Chat), Layout::Standard, &syls).preedit;
    let formal = type_row(&mut engine(Layout::Standard, Profile::Formal), Layout::Standard, &syls).preedit;
    assert_eq!((uni.as_str(), chat.as_str(), formal.as_str()), ("其中報告明天要教", "其中報告明天要交", "期中報告明天要交"));
    assert!(formal == truth);
    assert!(chat == golden_top1(0)[9] && formal == golden_top1(1)[9]);
}

/// set_profile recomputes the composition and returns the snapshot; reset keeps the model and the profile.
#[test]
fn set_profile_snapshot_and_reset_keeps_lm_and_profile() {
    let (_, syls) = row(10);
    let mut e = engine(Layout::Standard, Profile::Chat);
    assert!(type_row(&mut e, Layout::Standard, &syls).preedit == "其中報告明天要交");
    let o = e.set_profile(Profile::Formal).unwrap();
    assert!(o.handled && o.commit.is_empty() && o.preedit == "期中報告明天要交");
    assert!(e.key(Key::new(KeyKind::Enter)).unwrap().commit == "期中報告明天要交");
    for mode in [ResetMode::Commit, ResetMode::Discard] {
        type_row(&mut e, Layout::Standard, &syls);
        e.reset(mode);
        // Same output as a fresh engine with the same model and profile.
        let o = type_row(&mut e, Layout::Standard, &syls);
        assert!(o.preedit == "期中報告明天要交", "reset must keep the model and the profile");
        assert!(e.total_score().is_some());
        e.reset(ResetMode::Discard);
        assert!(e.total_score().is_none());
    }
}

/// Without a model the profile is only remembered; loading does not touch the display, the next change decodes with it.
#[test]
fn profile_before_load_and_load_does_not_refresh() {
    let s = shared();
    let (_, syls) = row(10);
    let mut e = Engine::new(&root().join("data/lexicon"), Layout::Standard).unwrap();
    let o = e.set_profile(Profile::Formal).unwrap();
    assert!(o.handled && o.preedit.is_empty());
    assert!(e.total_score().is_none());
    let before = type_row(&mut e, Layout::Standard, &syls[..3].to_vec()).preedit; // unigram display
    e.load_lm(&lm_path()).unwrap();
    let o = e.key(Key::new(KeyKind::End)).unwrap();
    assert!(o.preedit == before, "load_lm must not recompute the display");
    // the next composition change decodes with the model and the remembered formal profile
    let full = type_row(&mut e, Layout::Standard, &syls[3..].to_vec()).preedit;
    assert!(full == "期中報告明天要交");
    // an engine without a data_dir cannot load; a failed load leaves the model in place
    let mut bare = Engine::with_lexicon(s.lex.clone(), Layout::Standard);
    assert!(bare.load_lm(&lm_path()) == Err(EngineError::LoadFailed));
    assert!(e.load_lm(&root().join("data/lm/missing.sjlm")) == Err(EngineError::LoadFailed));
    assert!(e.total_score().is_some());
}

/// Candidate lists keep the original lexicon order whatever the model says.
#[test]
fn candidates_ignore_the_model() {
    let (_, syls) = row(10);
    let mut with = engine(Layout::Standard, Profile::Chat);
    let mut without = Engine::with_lexicon(shared().lex.clone(), Layout::Standard);
    for e in [&mut with, &mut without] {
        type_row(e, Layout::Standard, &syls);
        e.key(Key::new(KeyKind::Left)).unwrap();
    }
    let (a, b) = (with.key(Key::new(KeyKind::Space)).unwrap(), without.key(Key::new(KeyKind::Space)).unwrap());
    assert!(a.candidates == b.candidates && a.selected == b.selected && !a.candidates.is_empty());
}

/// s3d acceptance 4: punctuation is a sentence boundary for the bigram model. Premises straight from
/// the decoder (measured 2026-10-04 by a search over dev302): X = ㄊㄚ ㄔㄤˊ ends 他長 with the
/// sentence end but 他常 with a transition into "，"; Y = ㄗㄞˋ ㄏㄨㄢˋ starts 在換 from <s> but 再換
/// from "，". So the composition must read 他長，在換.
#[test]
fn punctuation_is_a_sentence_boundary() {
    let s = shared();
    let profile = Profile::Chat;
    let lam = profile.lambda();
    let syls = |r: &[&str]| r.iter().map(|x| x.to_string()).collect::<Syls>();
    let (x, y) = (syls(&["ㄊㄚ", "ㄔㄤˊ"]), syls(&["ㄗㄞˋ", "ㄏㄨㄢˋ"]));
    let top = |r: &Syls, start: &str, end: End| {
        let best = decode_segment(&s.capped, r, &s.lm, lam, start, end, 64).unwrap();
        best[0].1.iter().map(|w| w.0).collect::<String>()
    };
    assert_eq!(top(&x, "<s>", End::Eos), "他長");
    assert_eq!(top(&x, "<s>", End::Next { word: "，", lp: 0.0 }), "他常");
    assert_eq!(top(&y, "<s>", End::Eos), "在換");
    assert_eq!(top(&y, "，", End::Eos), "再換");

    let alone = |r: &Syls| {
        let mut e = engine(Layout::Standard, profile);
        type_row(&mut e, Layout::Standard, r).preedit
    };
    let mut e = engine(Layout::Standard, profile);
    type_row(&mut e, Layout::Standard, &x);
    e.key(Key::ch(',', MOD_SHIFT)).unwrap();
    let o = type_row(&mut e, Layout::Standard, &y);
    assert_eq!(o.preedit, "他長，在換");
    assert_eq!(o.preedit, format!("{}，{}", alone(&x), alone(&y)));
    // The total is the sum of the two sentences' scores.
    let (_, tx, _) = best_path(&x, profile);
    let (_, ty, _) = best_path(&y, profile);
    assert!((e.total_score().unwrap() - (tx + ty)).abs() < 1e-9);
    // s3e: an alternative (《, outside the s3a §4 set) is still a boundary, with the same total.
    let before = e.total_score().unwrap();
    let mut e2 = engine(Layout::Standard, profile);
    type_row(&mut e2, Layout::Standard, &x);
    e2.key(Key::ch(',', MOD_SHIFT)).unwrap();
    let o = e2.key(Key::new(KeyKind::Space)).unwrap();
    assert_eq!(o.candidates[2], "《");
    e2.key(Key::ch('3', 0)).unwrap();
    let o = type_row(&mut e2, Layout::Standard, &y);
    assert_eq!(o.preedit, "他長《在換");
    assert!((e2.total_score().unwrap() - before).abs() < 1e-9);
    // Only punctuation: no words, no total (contract §4).
    let mut e = engine(Layout::Standard, profile);
    e.key(Key::ch(',', MOD_SHIFT)).unwrap();
    e.key(Key::ch('.', MOD_SHIFT)).unwrap();
    assert!(e.total_score().is_none());
}

/// S2r (docs/contracts/s2r-sandhi-variants.md §4.3): the reading-flip probe through the production
/// path (`Engine::new` reads sandhi-add.tsv, then `load_lm`) matches the Python top-1, and two rows
/// the variants fix (typing the other MOE-standard reading of 不 and of 一) read correctly.
#[test]
fn sandhi_probe_production_path() {
    let probe = std::fs::read_to_string(root().join("eval/probe/s2r-probe.txt")).unwrap();
    let rows: Vec<(String, Syls)> = parse_rows(&probe)
        .unwrap()
        .into_iter()
        .map(|r| (r.sent, r.reading.expect("probe rows carry readings")))
        .collect();
    let want: Vec<String> = std::fs::read_to_string(root().join("eval/golden/s2r-probe-top1.tsv"))
        .unwrap()
        .lines()
        .filter(|l| !l.starts_with('#'))
        .map(String::from)
        .collect();
    assert!(rows.len() == want.len() && !rows.is_empty(), "probe and expected file differ in length");
    let mut e = Engine::new(&root().join("data/lexicon"), Layout::Standard).unwrap();
    e.load_lm(&lm_path()).unwrap();
    let commit = |e: &mut Engine, syls: &Syls| {
        let mut s = String::new();
        for k in syls.iter().flat_map(|x| keys_of(Layout::Standard, x)).chain([Key::new(KeyKind::Enter)]) {
            s.push_str(&e.key(k).unwrap().commit);
        }
        s
    };
    let diff: Vec<usize> = (0..rows.len()).filter(|&i| commit(&mut e, &rows[i].1) != want[i]).map(|i| i + 1).collect();
    assert!(diff.is_empty(), "probe replay differs from the Python top-1 at rows {diff:?}");
    // Wrong before sandhi-add.tsv existed (不事業配喔, 務會議場), right with it.
    let syls = |r: &str| r.split(' ').map(String::from).collect::<Syls>();
    assert_eq!(commit(&mut e, &syls("ㄅㄨˋ ㄕˋ ㄧㄝˋ ㄆㄟˋ ㄛ")), "不是業配喔");
    assert_eq!(commit(&mut e, &syls("ㄓㄜˋ ㄐㄧㄢˋ ㄕˋ ㄅㄣˇ ㄕˋ ㄨˋ ㄏㄨㄟˋ ㄧˋ ㄔㄤˇ")), "這件事本是誤會一場");
}

fn syls_of(r: &str) -> Syls {
    r.split(' ').map(String::from).collect()
}

/// Types a reading and commits with Enter, like the replay tests.
fn commit_reading(e: &mut Engine, reading: &str) -> String {
    let mut s = String::new();
    for k in syls_of(reading).iter().flat_map(|x| keys_of(Layout::Standard, x)).chain([Key::new(KeyKind::Enter)]) {
        s.push_str(&e.key(k).unwrap().commit);
    }
    s
}

/// S2r-2 (docs/contracts/s2r2-overlay-readings.md §4.5): overlay words read with the MOE readings
/// through the production path (`Engine::new`, then `load_lm`). Each reading was wrong on main:
/// the overlay had 三不沾 and 不乾膠 with ㄅㄨˊ before a non-falling tone, 上原和 and 上和下睦 with
/// ㄏㄢˋ, 賡和 with ㄏㄢˋ (now ㄏㄜˋ), and no sandhi row for 一丈青 (ㄧˊ).
#[test]
fn overlay_readings_production_path() {
    let mut e = Engine::new(&root().join("data/lexicon"), Layout::Standard).unwrap();
    e.load_lm(&lm_path()).unwrap();
    let cases = [
        ("ㄙㄢ ㄅㄨˋ ㄓㄢ", "三不沾"),
        ("ㄅㄨˋ ㄍㄢ ㄐㄧㄠ", "不乾膠"),
        ("ㄕㄤˋ ㄩㄢˊ ㄏㄜˊ", "上原和"),
        ("ㄕㄤˋ ㄏㄜˊ ㄒㄧㄚˋ ㄇㄨˋ", "上和下睦"),
        ("ㄍㄥ ㄏㄜˋ", "賡和"),
        ("ㄧˊ ㄓㄤˋ ㄑㄧㄥ", "一丈青"),
        ("ㄧ ㄓㄤˋ ㄏㄨㄥˊ", "一丈紅"),
    ];
    let bad: Vec<String> = cases
        .iter()
        .filter_map(|(r, want)| {
            let got = commit_reading(&mut e, r);
            (got != *want).then(|| format!("{r} -> {got} (want {want})"))
        })
        .collect();
    assert!(bad.is_empty(), "wrong commits: {bad:?}");
}

/// S2r-2 §4.6: the core needs no change for sandhi rows in overlay-add.tsv. 一丈紅 has two rows
/// (primary ㄧ first, variant ㄧˊ right after, same word, variant score 0.5 lower):
/// (a) the real files parse and `word_info` returns the primary reading (the higher score decides),
/// (b) the variant row scores the primary minus 0.5 and `CappedLexicon` caps both readings (at most the penalty apart), below the raw one,
/// (c) Python's `by_word` agrees (reference/proto/check_overlay_variants.py).
#[test]
fn overlay_sandhi_rows_load_and_cap() {
    let s = shared();
    let (primary, variant) = (syls_of("ㄧ ㄓㄤˋ ㄏㄨㄥˊ"), syls_of("ㄧˊ ㄓㄤˋ ㄏㄨㄥˊ"));
    let (got, raw) = s.lex.word_info("一丈紅").expect("一丈紅 is in the lexicon");
    assert_eq!(got, primary, "word_info must return the primary reading (the higher-scored row)");
    let vraw = s.lex.entries(&variant).iter().find(|(w, _)| *w == "一丈紅").map(|(_, sc)| *sc).expect("the variant row is loaded");
    assert!((raw - vraw - 0.5).abs() < 1e-9, "the variant row scores the primary minus the 0.5 penalty ({raw} vs {vraw})");
    let (cp, cv) = (s.capped.best_lp(&primary, "一丈紅").unwrap(), s.capped.best_lp(&variant, "一丈紅").unwrap());
    assert!(cv <= cp && cp - cv <= 0.5 + 1e-9, "capped variant is at most the penalty below the primary ({cp} vs {cv})");
    assert!(cp < raw, "the cap lowers the raw overlay score {raw} (got {cp})");
}
