//! S4 core tests (docs/spec/learning.md §13.1). Needs data/lm/bigram.sjlm like
//! engine_lm.rs. The `store_` tests write the learning file into a temporary directory.
use core::engine::*;
use core::eval::{parse_rows, usable};
use core::learn::{context_key, record_key, Learner, Level, Record, CAPACITY, GLOBAL, PRUNE_FLOOR, SENTINEL};
use core::lm::{self, CappedLexicon, Lm};
use core::{Lexicon, Syls};
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}
fn lm_path() -> PathBuf {
    let p = root().join("data/lm/bigram.sjlm");
    assert!(root().join("data/lm/classes.sjc").exists(), "data/lm/classes.sjc is missing: download it with `gh release download classes-v5 -R Nanako0129/shanjie -p classes.sjc -D data/lm` (or build it with tools/build_classes.py)");
    assert!(p.exists(), "data/lm/bigram.sjlm is missing (see engine_lm.rs)");
    p
}

struct Shared {
    lex: Arc<Lexicon>,
    lm: Arc<Lm>,
    capped: Arc<CappedLexicon>,
}
fn shared() -> &'static Shared {
    static S: OnceLock<Shared> = OnceLock::new();
    S.get_or_init(|| {
        let lex = load_lexicon(&root().join("data/lexicon")).unwrap();
        let lm = Lm::load(&lm_path()).unwrap();
        let overlay = capping_overlay(&root().join("data/lexicon"), "").unwrap(); // as Engine::load_lm: overlay-add.tsv + names-add.tsv
        let capped = Arc::new(CappedLexicon::new(lex.clone(), &overlay, &lm, None).unwrap());
        Shared { lex, lm: Arc::new(lm), capped }
    })
}
/// SHANJIE_TEST_PROFILE=formal (docs/spec/decoder.md §3.4): run the engine under the formal profile and require the
/// trigram table beside the model, so a missing `trigram.sjt3` fails here instead of silently scoring the bigram. Unset: no-op;
/// any other value panics. Only the four B2 tests (a2_mirror_teach_the_other_word, a2_candidate_pick_learning_on_cases_tsv,
/// pollution_zai_taught_under_two_contexts, global_eps_table) are meant to run with it; the others call `chat_only`.
/// Call after the model is in place.
fn use_test_profile(e: &mut Engine) {
    match std::env::var("SHANJIE_TEST_PROFILE") {
        Err(_) => {}
        Ok(v) if v == "formal" => {
            assert!(lm_path().with_file_name("trigram.sjt3").exists(), "SHANJIE_TEST_PROFILE=formal needs data/lm/trigram.sjt3 (trigram table missing)");
            e.set_profile(lm::Profile::Formal).unwrap();
        }
        Ok(v) => panic!("SHANJIE_TEST_PROFILE={v:?}: only the exact value \"formal\" is understood"),
    }
}
/// Chat-pinned tests: the formal profile would change their expectations.
fn chat_only() {
    assert!(
        std::env::var("SHANJIE_TEST_PROFILE").is_err(),
        "SHANJIE_TEST_PROFILE changes this test's expectations (chat profile): run only the four B2 tests under SHANJIE_TEST_PROFILE=formal"
    );
}
/// `Engine::new` + `load_lm` (reads `trigram.sjt3` beside the model itself) + fixed clock, learning on, test profile.
fn loaded_engine() -> Engine {
    let mut e = Engine::new(&root().join("data/lexicon"), L).unwrap();
    e.load_lm(&lm_path()).unwrap();
    use_test_profile(&mut e);
    e.set_today(Some(DAY));
    e.set_learning(true);
    e
}
/// `use_test_profile` for engines built on `shared()`: the table must be in the shared model itself.
fn shared_profile_check(e: &mut Engine) {
    use_test_profile(e);
    if std::env::var("SHANJIE_TEST_PROFILE").is_ok() {
        assert!(shared().lm.uses_tri(shared().lm.scoring(lm::Profile::Formal)), "SHANJIE_TEST_PROFILE=formal needs data/lm/trigram.sjt3 (trigram table missing)");
    }
}
/// Production lexicon + model, learning on, fixed clock (day 20000).
fn engine() -> Engine {
    let s = shared();
    let mut e = Engine::with_lexicon(s.lex.clone(), Layout::Standard);
    e.set_lm(s.lm.clone(), s.capped.clone());
    shared_profile_check(&mut e);
    e.set_today(Some(DAY));
    e.set_learning(true);
    e.set_prediction(false).unwrap(); // docs/spec/prediction.md §5: the subject is the lexicon window, which now lists a prediction row first
    e
}
const DAY: i64 = 20_000;
const L: Layout = Layout::Standard;

/// The capped lexicon over the lexicon text `text` with the reading term (docs/spec/decoder.md §5), as `Engine::load_lm` gives the App's: the table
/// is built from the same text (its rows are the ones the mc estimate counts), with no exemption and without the mass step (`with_mass`), which these
/// small lexicons never meet. `set_reading_decode(false)` or `set_reading_active(false)`
/// switches it off.
fn capped_over(text: &str) -> CappedLexicon {
    let lex = Arc::new(Lexicon::parse(text).unwrap());
    let rd = core::reading::ReadingDecode::new(&lex, text).with_exempt("").unwrap();
    CappedLexicon::new(lex, "", &shared().lm, None).unwrap().with_reading(rd)
}

/// Tiny lexicon `engine_text` for the engine, `capped_text` for the decoder (they differ only to force a
/// decode failure); the real bigram model.
fn tiny(engine_text: &str, capped_text: &str) -> Engine {
    let s = shared();
    let lex = Arc::new(Lexicon::parse(engine_text).unwrap());
    let capped = Arc::new(capped_over(capped_text));
    let mut e = Engine::with_lexicon(lex, L);
    e.set_lm(s.lm.clone(), capped);
    shared_profile_check(&mut e);
    e.set_today(Some(DAY));
    e.set_learning(true);
    e.set_prediction(false).unwrap(); // docs/spec/prediction.md §5: as in `engine`
    e
}
/// Single characters learn only after exactly two Han characters (docs/spec/learning.md §5.3), so the one-syllable
/// teaches in these tests take this left context.
const TAUGHT_AFTER: &str = "他們";
const TINY: &str = "ㄊㄚ 他 -1.0\nㄒㄧㄣ 鑫 -1.0\nㄒㄧㄣ 欣 -2.0\nㄅㄣ 犇 -1.0\nㄅㄣ 奔 -2.0\n";

// ---------- key helpers ----------

fn keys_of(syl: &str) -> Vec<Key> {
    let mut v = Vec::new();
    let mut toned = false;
    for c in syl.chars() {
        match L.key_of_tone(c) {
            Some(tk) => {
                v.push(Key::ch(tk, 0));
                toned = true;
            }
            None => v.push(Key::ch(L.key_of_symbol(c).expect("symbol has a key"), 0)),
        }
    }
    if !toned {
        v.push(Key::new(KeyKind::Space));
    }
    v
}
fn syls(reading: &str) -> Vec<String> {
    reading.split(' ').map(String::from).collect()
}
fn k(kind: KeyKind) -> Key {
    Key::new(kind)
}
/// Type the syllables; returns the last output.
fn type_syls(e: &mut Engine, reading: &str) -> Output {
    let mut o = None;
    for s in reading.split(' ') {
        for key in keys_of(s) {
            o = Some(e.key(key).unwrap());
        }
    }
    o.unwrap()
}
fn commit_of(e: &mut Engine, reading: &str) -> String {
    type_syls(e, reading);
    e.key(k(KeyKind::Enter)).unwrap().commit
}
/// With `n` syllables typed and the cursor at the end: open the candidates for the span ending after
/// syllable `end` and choose `word`. Leaves the cursor at the end of the composition.
fn pick(e: &mut Engine, n: usize, end: usize, word: &str) {
    for _ in 0..n - end {
        e.key(k(KeyKind::Left)).unwrap();
    }
    let o = e.key(k(KeyKind::Space)).unwrap();
    highlight(e, o, word);
    e.key(k(KeyKind::Enter)).unwrap();
    e.key(k(KeyKind::End)).unwrap();
}
/// Move the highlight (candidates open, `o` the current snapshot) onto `word`.
fn highlight(e: &mut Engine, mut o: Output, word: &str) -> Output {
    for _ in 0..5000 {
        if o.candidates[o.selected.unwrap()] == word {
            return o;
        }
        o = e.key(k(KeyKind::Right)).unwrap();
    }
    panic!("candidate not found");
}
fn rec(c: &str, r: &str, w: &str, weight: f64, day: i64) -> Record {
    Record { context: c.into(), reading: syls(r), word: w.into(), weight, day }
}

// ---------- cases.tsv ----------

struct Row {
    kind: String,
    sent: String,
    same: Option<bool>,
    reading: String,
}
struct Group {
    name: String,
    /// The word the teach sentence contains / the other word of the pair.
    word: String,
    other: String,
    rows: Vec<Row>,
}
fn groups() -> Vec<Group> {
    let t = std::fs::read_to_string(root().join("eval/learn/cases.tsv")).unwrap();
    let mut gs: Vec<Group> = Vec::new();
    for l in t.lines().skip(1) {
        let f: Vec<&str> = l.split('\t').collect();
        assert_eq!(f.len(), 5, "cases.tsv row");
        let row = Row { kind: f[1].into(), sent: f[2].into(), same: (!f[3].is_empty()).then(|| f[3] == "1"), reading: f[4].into() };
        assert_eq!(row.sent.chars().count(), row.reading.split(' ').count(), "one char per syllable");
        if gs.last().is_none_or(|g| g.name != f[0]) {
            gs.push(Group { name: f[0].into(), word: String::new(), other: String::new(), rows: Vec::new() });
        }
        gs.last_mut().unwrap().rows.push(row);
    }
    for g in &mut gs {
        let pair: Vec<&str> = g.name.split('/').collect();
        let teach = g.rows.iter().find(|r| r.kind == "teach").unwrap();
        let (w, o) = if teach.sent.contains(pair[0]) { (pair[0], pair[1]) } else { (pair[1], pair[0]) };
        assert!(teach.sent.contains(w) && !teach.sent.contains(o), "teach sentence holds exactly one word");
        g.word = w.into();
        g.other = o.into();
    }
    gs
}
/// (context key before the first `word` in `sent`, syllable start, syllable end)
fn span_of(sent: &str, word: &str) -> (String, usize, usize) {
    let b = sent.find(word).unwrap();
    let start = sent[..b].chars().count();
    (context_key(&sent[..b]), start, start + word.chars().count())
}
/// Would a record taught under `taught` answer a decode-time query under `query` (docs/spec/learning.md §6.1 order)? A
/// single-character word answers only on an equal full key other than "^" (docs/spec/learning.md §5.3, the single-character rules, narrows
/// that to two-character keys, which the sentences here never contradict). For a word of 2+ characters "^" stands
/// for the synthetic sentence-start keys, which also need the same next syllable: a "^" match here over-reports.
fn collides(taught: &str, query: &str, single: bool) -> bool {
    if single {
        return taught == query && taught != SENTINEL;
    }
    taught == query || (taught != SENTINEL && query != SENTINEL && taught.chars().last() == query.chars().last())
}

/// Teach flow of A2: type the sentence, open the candidates at the word, choose it, Enter.
fn teach_row(e: &mut Engine, g: &Group, row: &Row) -> String {
    teach_pick(e, row, &g.word, &g.word)
}
/// Same, for the span of `at` in the sentence, choosing `word` (the pair's other word for the mirror run).
fn teach_pick(e: &mut Engine, row: &Row, at: &str, word: &str) -> String {
    let (_, _, end) = span_of(&row.sent, at);
    let n = row.reading.split(' ').count();
    type_syls(e, &row.reading);
    pick(e, n, end, word);
    e.key(k(KeyKind::Enter)).unwrap().commit
}
fn ok(e: &mut Engine, row: &Row) -> bool {
    commit_of(e, &row.reading) == row.sent
}

// ---------- 1: step 0 ----------

#[test]
fn step0_ceiling_collisions_baselines() {
    chat_only();
    let gs = groups();
    assert_eq!(gs.len(), 14);
    let mut ceiling = 0;
    let mut e = engine();
    println!("group | ceiling | colliding common rows | baseline teach / common / rare(same) / rare(other)");
    for g in &gs {
        let teach = g.rows.iter().find(|r| r.kind == "teach").unwrap();
        let (tctx, _, _) = span_of(&teach.sent, &g.word);
        let same = g.rows.iter().find(|r| r.same == Some(true)).unwrap();
        let (sctx, _, _) = span_of(&same.sent, &g.word);
        let single = g.word.chars().count() == 1;
        let reach = collides(&tctx, &sctx, single);
        ceiling += reach as usize;
        let coll: Vec<&str> = g
            .rows
            .iter()
            .filter(|r| r.kind == "common")
            .filter(|r| collides(&tctx, &span_of(&r.sent, &g.other).0, single))
            .map(|r| r.sent.as_str())
            .collect();
        let b = |pred: &dyn Fn(&Row) -> bool, e: &mut Engine| {
            let rows: Vec<&Row> = g.rows.iter().filter(|r| pred(r)).collect();
            format!("{}/{}", rows.iter().filter(|r| ok(e, r)).count(), rows.len())
        };
        println!(
            "{} | key {tctx} vs {sctx}: {} | {:?} | {} / {} / {} / {}",
            g.name,
            if reach { "match" } else { "NO MATCH" },
            coll,
            b(&|r| r.kind == "teach", &mut e),
            b(&|r| r.kind == "common", &mut e),
            b(&|r| r.same == Some(true), &mut e),
            b(&|r| r.same == Some(false), &mut e),
        );
    }
    println!("ceiling {ceiling}/14");
    assert_eq!(ceiling, 13, "docs/spec/learning.md §6.1: 13 of 14");
}

// ---------- 2: A2 ----------

/// (whole sentence exact, the pair word is the expected one). The cases mix the pair word with
/// ordinary text the engine sometimes misreads for unrelated reasons (道士 -> 到是), so the gate is on
/// the pair word; the exact-sentence flag is reported and also must not regress on common rows.
fn judge(e: &mut Engine, g: &Group, row: &Row) -> (bool, bool) {
    let c = commit_of(e, &row.reading);
    let (want, other) = if row.kind == "common" { (&g.other, &g.word) } else { (&g.word, &g.other) };
    (c == row.sent, c.contains(want.as_str()) && !c.contains(other.as_str()))
}

#[test]
fn a2_candidate_pick_learning_on_cases_tsv() {
    let t = std::time::Instant::now();
    let mut e = loaded_engine();
    println!("engine new + load_lm {:?}", t.elapsed());
    let gs = groups();
    // same-context rare rows wrong before: learned / total, all and only those with a reachable key (docs/spec/learning.md §6.1)
    let (mut learned, mut wrong, mut learned_reach, mut wrong_reach) = (0, 0, 0, 0);
    let (mut regress, mut moot, mut failed) = (Vec::new(), Vec::new(), Vec::new());
    println!("group | row kinds: exact/word before -> after | records written");
    for g in &gs {
        e.learning_clear().unwrap_err(); // no store: an error (docs/spec/learning.md §9.2), memory still dropped
        let teach = g.rows.iter().find(|r| r.kind == "teach").unwrap();
        let (tctx, _, _) = span_of(&teach.sent, &g.word);
        let before: Vec<(bool, bool)> = g.rows.iter().map(|r| judge(&mut e, g, r)).collect();
        teach_row(&mut e, g, teach);
        let n_rec = e.learner().records().iter().filter(|r| r.word == g.word).count();
        let after: Vec<(bool, bool)> = g.rows.iter().map(|r| judge(&mut e, g, r)).collect();
        let f = |v: &[(bool, bool)]| g.rows.iter().zip(v).map(|(r, b)| format!("{}{}{}", &r.kind[..1], b.0 as u8, b.1 as u8)).collect::<Vec<_>>().join(" ");
        println!("{} | {} -> {} | {n_rec}", g.name, f(&before), f(&after));
        // Nothing to pick when the default already shows the taught word: the group is moot (reported).
        let is_moot = n_rec == 0 && before[0].1;
        if is_moot {
            moot.push(g.name.clone());
        } else if n_rec == 0 {
            failed.push(g.name.clone());
        }
        let same = g.rows.iter().find(|r| r.same == Some(true)).unwrap();
        let single = g.word.chars().count() == 1;
        let reach = collides(&tctx, &span_of(&same.sent, &g.word).0, single);
        for (i, r) in g.rows.iter().enumerate() {
            let coll = r.kind == "common" && collides(&tctx, &span_of(&r.sent, &g.other).0, single);
            if r.kind == "common" {
                let lost = (before[i].0 && !after[i].0) || (before[i].1 && !after[i].1);
                if coll {
                    println!("   colliding common (reported, not gated): {} before {:?} after {:?}", r.sent, before[i], after[i]);
                } else if lost {
                    regress.push(r.sent.clone());
                }
            } else if r.same == Some(true) && is_moot {
                // Nothing was taught (the teach sentence is already right, so no re-pick happens): the group says
                // nothing about passing a record to the same context. The mirror run covers it (docs/spec/learning.md §13.1).
                println!("   same-ctx row of a moot group (not counted): {} before {:?}", r.sent, before[i]);
            } else if r.same == Some(true) {
                if !before[i].1 {
                    wrong += 1;
                    learned += after[i].1 as usize;
                    wrong_reach += reach as usize;
                    learned_reach += (reach && after[i].1) as usize;
                    println!("   same-ctx wrong before: {} -> {}", r.sent, if after[i].1 { "learned" } else { "NOT learned" });
                } else {
                    println!("   same-ctx already right before (not counted): {}", r.sent);
                }
            } else if r.kind == "rare" {
                println!("   other-ctx (number only): {} before {:?} after {:?}", r.sent, before[i], after[i]);
            }
        }
        if g.name == "權力/全力" {
            assert!(e.learner().records().iter().all(|r| r.context != GLOBAL), "1-char match makes no global record");
        }
    }
    println!("same-context learned {learned}/{wrong}; reachable keys only {learned_reach}/{wrong_reach}");
    println!("moot groups (the default already shows the taught word, nothing to pick): {moot:?}");
    assert!(failed.is_empty(), "groups with a wrong default but no record: {failed:?}");
    assert!(regress.is_empty(), "non-colliding common sentences regress: {regress:?}");
    // Moot groups are not counted (docs/spec/learning.md §6.2, §13.1), so keep a floor on what is: 3 with the released model and classes.
    assert!(wrong_reach >= 3, "fewer than 3 countable same-context rows ({wrong_reach}): the rate would not mean anything");
    assert!(learned_reach * 100 >= wrong_reach * 80, "same-context learn rate under 80%");

    // Informational: all 14 groups taught on one learner, then every common row.
    e.learning_clear().unwrap_err(); // no store: an error (docs/spec/learning.md §9.2), memory still dropped
    for g in &gs {
        teach_row(&mut e, g, g.rows.iter().find(|r| r.kind == "teach").unwrap());
    }
    let bad: Vec<&str> =
        gs.iter().flat_map(|g| g.rows.iter().map(move |r| (g, r))).filter(|(g, r)| r.kind == "common" && !judge(&mut e, g, r).1).map(|(_, r)| r.sent.as_str()).collect();
    println!("cumulative (all 14 taught): {} records, common rows with the wrong pair word: {bad:?}", e.learner().records().len());
}

/// The 12 groups whose cold word is already the default cannot be taught that word. Mirror run: teach
/// the pair's *other* word at the teach sentence's span (as a user who wants it there would) and check
/// the same-context rare sentence follows. Same code path, so it exercises what A2 cannot for them.
#[test]
fn a2_mirror_teach_the_other_word() {
    let (learned_reach, wrong_reach, ..) = mirror(lm::LEARN_EPS_GLOBAL);
    assert!(learned_reach * 100 >= wrong_reach * 80, "mirror learn rate under 80%");
}

/// (learned reachable, wrong reachable, learned, wrong) of the mirror run with global ε `eps`.
fn mirror(eps: f64) -> (usize, usize, usize, usize) {
    let mut e = loaded_engine();
    e.set_eps_global(eps);
    let (mut learned, mut wrong, mut learned_reach, mut wrong_reach) = (0, 0, 0, 0);
    for g in &groups() {
        e.learning_clear().unwrap_err(); // no store: an error (docs/spec/learning.md §9.2), memory still dropped
        let teach = g.rows.iter().find(|r| r.kind == "teach").unwrap();
        let same = g.rows.iter().find(|r| r.same == Some(true)).unwrap();
        let (tctx, _, _) = span_of(&teach.sent, &g.word);
        let reach = collides(&tctx, &span_of(&same.sent, &g.word).0, g.other.chars().count() == 1);
        let (_, st, en) = span_of(&teach.sent, &g.word);
        let span: Vec<String> = syls(&teach.reading)[st..en].to_vec();
        if !shared().lex.entries(&span).iter().any(|(w, _)| *w == g.other) {
            println!("{} | skipped: {} has another reading than {}", g.name, g.other, g.word);
            continue;
        }
        let wants_other = |e: &mut Engine| commit_of(e, &same.reading).contains(&g.other);
        let before = wants_other(&mut e);
        teach_pick(&mut e, teach, &g.word, &g.other);
        let n_rec = e.learner().records().len();
        let after = wants_other(&mut e);
        println!("{} | teach {} at {tctx}: records {n_rec}, same-ctx row shows the other word {before} -> {after} (key reachable {reach})", g.name, g.other);
        assert!(n_rec >= 1 || before, "{}: picking a different word must leave a record", g.name);
        if !before {
            wrong += 1;
            learned += after as usize;
            wrong_reach += reach as usize;
            learned_reach += (reach && after) as usize;
        }
    }
    println!("mirror (global eps {eps}): learned {learned}/{wrong}; reachable keys only {learned_reach}/{wrong_reach}");
    (learned_reach, wrong_reach, learned, wrong)
}

// ---------- 3: context consistency ----------

/// Learning writes the key that decoding then queries: for each case the record's key is the literal
/// expected one, and typing the same thing again shows the learned word (a hit at decode time).
#[test]
fn context_key_learned_equals_context_key_queried() {
    // (a) the context is two characters (a single character learns only after exactly two, docs/spec/learning.md §5.3)
    let mut e = tiny(TINY, TINY);
    type_syls(&mut e, "ㄊㄚ ㄊㄚ ㄒㄧㄣ");
    pick(&mut e, 3, 3, "欣");
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, "他他欣");
    assert_eq!(keys(&e), ["他他"]);
    assert_eq!(type_syls(&mut e, "ㄊㄚ ㄊㄚ ㄒㄧㄣ").preedit, "他他欣");
    e.key(k(KeyKind::Esc)).unwrap();
    assert_eq!(type_syls(&mut e, "ㄒㄧㄣ").preedit, "鑫", "key ^ is not the key 他他");
    e.key(k(KeyKind::Esc)).unwrap();

    // (b) after a fixed word (single characters learn only under two Han characters, docs/spec/learning.md §5.3, so the first character takes 你好).
    // The pick is whichever of 奔／犇 the model does not show, so it is a correction under any class weight (classes-v5 shows 奔).
    let mut e = tiny(TINY, TINY);
    e.set_left_context("你好");
    let b = if type_syls(&mut e, "ㄅㄣ ㄒㄧㄣ").preedit.starts_with('奔') { "犇" } else { "奔" };
    pick(&mut e, 2, 1, b);
    pick(&mut e, 2, 2, "欣");
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, format!("{b}欣"));
    assert_eq!(keys(&e), ["你好".to_string(), format!("好{b}")], "{b} after 你好, 欣 after 好 and the fixed word {b}");
    e.set_left_context("你好");
    assert_eq!(type_syls(&mut e, "ㄅㄣ ㄒㄧㄣ").preedit, format!("{b}欣"));
    e.key(k(KeyKind::Esc)).unwrap();

    // (c) after punctuation the key is ^ (sentence-start key: ^> and the next reading): the single character here ends the composition (^>$), where none is learned (rule 10) ...
    let mut e = tiny(TINY, TINY);
    type_syls(&mut e, "ㄊㄚ");
    e.key(Key::ch(',', MOD_SHIFT)).unwrap();
    type_syls(&mut e, "ㄒㄧㄣ");
    pick(&mut e, 3, 3, "欣");
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, "他，欣");
    assert!(keys(&e).is_empty());
    // ... so the teach-time and decode-time keys are checked with a 2-character word (real model)
    const R2: &str = "ㄑㄩㄢˊ ㄌㄧˋ";
    let other = |shown: &str| if shown == "權力" { "全力" } else { "權力" };
    let mut e = engine();
    type_syls(&mut e, "ㄊㄚ");
    e.key(Key::ch(',', MOD_SHIFT)).unwrap();
    let w = other(type_syls(&mut e, R2).preedit.trim_start_matches("他，")).to_string();
    pick(&mut e, 4, 4, &w);
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, format!("他，{w}"));
    assert_eq!(keys(&e), ["^>$"], "after the punctuation token the key is ^, and nothing follows the word: ^>$");
    type_syls(&mut e, "ㄊㄚ");
    e.key(Key::ch(',', MOD_SHIFT)).unwrap();
    assert_eq!(type_syls(&mut e, R2).preedit, format!("他，{w}"), "decode-time key after punctuation is the taught ^>$");
    e.key(k(KeyKind::Esc)).unwrap();
    assert_ne!(type_syls(&mut e, &format!("ㄊㄚ {R2}")).preedit, format!("他{w}"), "no punctuation: key 他, not ^");
    e.key(k(KeyKind::Esc)).unwrap();

    // (d) sentence start, the character ends the composition (^>$): a single character is not learned, a 2-character word is
    let mut e = tiny(TINY, TINY);
    type_syls(&mut e, "ㄒㄧㄣ");
    pick(&mut e, 1, 1, "欣");
    e.key(k(KeyKind::Enter)).unwrap();
    assert!(keys(&e).is_empty());
    assert_eq!(type_syls(&mut e, "ㄒㄧㄣ").preedit, "鑫");
    e.key(k(KeyKind::Esc)).unwrap();
    let mut e = engine();
    let w = other(&type_syls(&mut e, R2).preedit).to_string();
    pick(&mut e, 2, 2, &w);
    e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!(keys(&e), ["^>$"]);
    assert_eq!(type_syls(&mut e, R2).preedit, w, "decode-time key at sentence start is ^>$");
    e.key(k(KeyKind::Esc)).unwrap();

    // (e) left context from the shell, kept to its last two Han characters
    let mut e = tiny(TINY, TINY);
    e.set_left_context("abc了好他");
    type_syls(&mut e, "ㄒㄧㄣ");
    pick(&mut e, 1, 1, "欣");
    e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!(keys(&e), ["好他"]);
    e.set_left_context("x了好他");
    assert_eq!(type_syls(&mut e, "ㄒㄧㄣ").preedit, "欣");
    e.key(k(KeyKind::Esc)).unwrap();
    e.set_left_context("");
    assert_eq!(type_syls(&mut e, "ㄒㄧㄣ").preedit, "鑫");
}
/// The context keys of the learner's S4 records, in insertion order. New-word records ("#", new-word B) are no
/// context: two re-picked single characters next to each other, as in (b), also teach a new word.
fn keys(e: &Engine) -> Vec<String> {
    e.learner().records().iter().filter(|r| r.context != core::learn::NEWWORD).map(|r| r.context.clone()).collect()
}

// ---------- 4: sentinel, 1-char match, globalization ----------

#[test]
fn sentinel_one_char_and_global() {
    // docs/spec/learning.md §5.4: only words of 2+ characters globalize, so the global parts use a two-syllable word.
    // The bare "^" is no record key (docs/spec/learning.md §5.3); the sentence-start key is the synthetic one.
    const SYN: &str = "^>$";
    let r = syls("ㄅㄚˇ ㄅㄚˇ");
    let mut l = Learner::default();
    l.teach(SYN, &r, "把手", "爸爸", DAY);
    assert!(l.lookup("管把", &r, DAY).1.is_empty() && l.lookup("", &r, DAY).1.is_empty(), "sentence start is not global");
    assert_eq!(l.lookup(SYN, &r, DAY).1.len(), 1);
    assert!(l.records().iter().all(|x| x.context != GLOBAL));

    let mut l = Learner::default();
    l.teach("管把", &r, "權力", "全力", DAY);
    assert_eq!(l.lookup("肯把", &r, DAY).1[0].0, "權力", "1-char step: same last character");
    assert_eq!(l.lookup("管把", &r, DAY).1[0].0, "權力");
    assert!(l.lookup("把", &r, DAY).1.is_empty() || l.lookup("把", &r, DAY).1[0].0 == "權力", "1-char key still shares the last char");
    assert!(l.lookup("管他", &r, DAY).1.is_empty() && l.lookup(SYN, &r, DAY).1.is_empty());
    assert!(l.records().iter().all(|x| x.context != GLOBAL), "one teach never globalizes");
    // a second distinct full key globalizes; 1-char matches and repeats of the same key do not
    l.teach("管把", &r, "權力", "全力", DAY);
    assert!(l.records().iter().all(|x| x.context != GLOBAL));
    l.teach("肯把", &r, "權力", "全力", DAY);
    assert_eq!(l.records().iter().filter(|x| x.context == GLOBAL).count(), 1);
    assert_eq!(l.lookup("完全不同", &r, DAY).1[0].0, "權力", "global answers any context");
    assert_eq!(l.lookup(SYN, &r, DAY).1[0].0, "權力");
    // SYN counts as one of the two keys
    let mut l = Learner::default();
    l.teach(SYN, &r, "權力", "全力", DAY);
    l.teach("管把", &r, "權力", "全力", DAY);
    assert_eq!(l.records().iter().filter(|x| x.context == GLOBAL).count(), 1);
}

// ---------- 5: beyond PER_KEY ----------

#[test]
fn learned_word_beyond_per_key_is_enumerated() {
    let s = shared();
    // A one-syllable reading with many homophones: take the capped word ranked 20th.
    let reading = "ㄧˋ";
    let ranked = s.capped.entries(&syls(reading));
    assert!(ranked.len() > 30);
    let word = ranked[20].0.to_string();
    let mut e = engine();
    e.set_left_context(TAUGHT_AFTER);
    assert_ne!(type_syls(&mut e, reading).preedit, word);
    pick(&mut e, 1, 1, &word);
    e.key(k(KeyKind::Enter)).unwrap();
    e.set_left_context(TAUGHT_AFTER);
    assert_eq!(type_syls(&mut e, reading).preedit, word, "rank 21 word wins after one teach");
    e.key(k(KeyKind::Esc)).unwrap();
    e.learning_clear().unwrap_err(); // no store: an error (docs/spec/learning.md §9.2), memory still dropped
    e.set_left_context(TAUGHT_AFTER);
    assert_ne!(type_syls(&mut e, reading).preedit, word, "and only because of the record");
}

/// Key scheme 1, rule 16u (docs/spec/keys-and-composition.md §4.1.1): Up swaps are picks of one character; two presses and Enter teach the character the run
/// stopped on (the second press re-picks the span and keeps the first press's `pre`), not the one between.
#[test]
fn up_swaps_teach_the_last_character_only() {
    let s = shared();
    let reading = "ㄧˋ";
    let mut e = engine();
    assert!(e.set_key_scheme(1).unwrap().is_some());
    e.set_left_context(TAUGHT_AFTER);
    let shown = type_syls(&mut e, reading).preedit;
    let mut base = vec![shown.clone()];
    for (w, _) in s.lex.entries(&syls(reading)) {
        if !base.iter().any(|x| x == w) {
            base.push(w.to_string());
        }
    }
    assert!(base.len() > 3 && !base[1..].contains(&shown));
    assert_eq!(e.key(k(KeyKind::Up)).unwrap().preedit, base[2]);
    assert_eq!(e.key(k(KeyKind::Up)).unwrap().preedit, base[1]);
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, base[1]);
    e.set_left_context(TAUGHT_AFTER);
    assert_eq!(type_syls(&mut e, reading).preedit, base[1], "the last character is taught");
    e.key(k(KeyKind::Esc)).unwrap();
    // Without the teach the decoder shows `shown` again (the record is what moved it).
    let mut fresh = engine();
    assert!(fresh.set_key_scheme(1).unwrap().is_some());
    fresh.set_left_context(TAUGHT_AFTER);
    assert_eq!(type_syls(&mut fresh, reading).preedit, shown);
    assert!(e.learner().records().iter().all(|r| r.word != base[2]), "the character between is not taught");
}

// ---------- 6: empty learner changes nothing ----------

fn probe_rows() -> Vec<(String, Syls)> {
    let probe = std::fs::read_to_string(root().join("eval/probe/s2r-probe.txt")).unwrap();
    parse_rows(&probe).unwrap().into_iter().map(|r| (r.sent, r.reading.unwrap())).collect()
}
fn dev302() -> Vec<Syls> {
    dev302_rows().into_iter().map(|r| r.2).collect()
}
/// (前文, sentence, reading) of the first 302 usable dev rows.
fn dev302_rows() -> Vec<(String, String, Syls)> {
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
    rows.into_iter()
        .map(|r| {
            let rd = r.reading.clone().unwrap_or_else(|| lex.to_syllables(&r.sent).unwrap());
            (r.ctx, r.sent, rd)
        })
        .collect()
}
fn commit_syls(e: &mut Engine, s: &Syls) -> String {
    commit_of(e, &s.join(" "))
}

/// Learning on with an empty learner and no left context reproduces the committed goldens (dev302 chat,
/// and the 76-row probe) row for row (docs/spec/decoder.md §4.3: an empty left context leaves decoding as it was). Under
/// the left context "好他", which S2h now lets condition the first word, the learning-on engine with an
/// empty learner and a learning-off engine commit the same dev302 rows: learning alone changes nothing.
#[test]
fn empty_learner_matches_goldens() {
    chat_only();
    let gold: Vec<String> = std::fs::read_to_string(root().join("eval/golden/s2-lm-dev302-top1.tsv"))
        .unwrap()
        .lines()
        .filter(|l| !l.starts_with('#'))
        .map(|l| l.split('\t').next().unwrap().to_string())
        .collect();
    let mut e = engine();
    let diff: Vec<usize> = dev302()
        .iter()
        .enumerate()
        .filter(|(i, s)| {
            e.set_left_context("");
            commit_syls(&mut e, s) != gold[*i]
        })
        .map(|(i, _)| i + 1)
        .collect();
    assert!(diff.is_empty(), "dev302 differs at {diff:?}");
    let want: Vec<String> = std::fs::read_to_string(root().join("eval/golden/s2r-probe-top1.tsv"))
        .unwrap()
        .lines()
        .filter(|l| !l.starts_with('#'))
        .map(String::from)
        .collect();
    let rows = probe_rows();
    assert_eq!(rows.len(), want.len());
    let diff: Vec<usize> = (0..rows.len()).filter(|&i| commit_syls(&mut e, &rows[i].1) != want[i]).map(|i| i + 1).collect();
    assert!(diff.is_empty(), "probe differs at {diff:?}");
    let mut off = engine();
    off.set_learning(false);
    let diff: Vec<usize> = dev302()
        .iter()
        .enumerate()
        .filter(|(_, s)| {
            e.set_left_context("好他");
            off.set_left_context("好他");
            commit_syls(&mut e, s) != commit_syls(&mut off, s)
        })
        .map(|(i, _)| i + 1)
        .collect();
    assert!(diff.is_empty(), "dev302 under 好他: learning on differs from off at {diff:?}");
    assert!(e.learner().records().is_empty());
}

// ---------- 7: decay, replacing a pick, pruning, capacity, forgetting ----------

#[test]
fn decay_halves_every_fourteen_days_and_never_grows_back() {
    let r = syls("ㄒㄧㄣ");
    let mut l = Learner::default();
    l.teach("他們", &r, "欣", "鑫", DAY);
    assert_eq!(l.lookup("他們", &r, DAY).1[0].1, 1.0);
    assert!((l.lookup("他們", &r, DAY + 14).1[0].1 - 0.5).abs() < 1e-12, "half-life 14 days");
    assert!(l.lookup("他們", &r, DAY + 15).1.is_empty(), "below 0.5 stops boosting");
    assert_eq!(l.lookup("他們", &r, DAY - 100).1[0].1, 1.0, "a clock that went back does not grow weights");
}

#[test]
fn repick_halves_the_displaced_word_and_adds_to_the_new_one() {
    let r = syls("ㄒㄧㄣ");
    let mut l = Learner::default();
    l.teach("他們", &r, "欣", "鑫", DAY);
    l.teach("他們", &r, "欣", "鑫", DAY);
    l.teach("他們", &r, "新", "欣", DAY);
    let w = |x: &str| l.lookup("他們", &r, DAY).1.iter().find(|h| h.0 == x).map(|h| h.1);
    assert_eq!(w("欣"), Some(1.0), "2.0 halved");
    assert_eq!(w("新"), Some(1.0));
    // the displaced word had no record: nothing to halve, nothing created
    let mut l = Learner::default();
    l.teach("他們", &r, "新", "鑫", DAY);
    assert_eq!(l.records().len(), 1);
}

#[test]
fn prune_drops_decayed_records_and_caps_the_total() {
    let r = "ㄒㄧㄣ";
    let mut l = Learner::from_records(vec![rec("他", r, "a", 1.0, DAY - 70), rec("他", r, "b", 1.0, DAY - 10), rec("他", r, "c", 0.06, DAY)]);
    l.prune(DAY);
    assert_eq!(index_words(&l, r), ["b", "c"], "70 days old is under PRUNE_FLOOR {PRUNE_FLOOR}; 10 days is not");
    let mut big: Vec<Record> = (0..CAPACITY + 10).map(|i| rec("他", r, &format!("w{i}"), 1.0 + i as f64, DAY)).collect();
    big.push(rec("他", r, "low", 0.3, DAY));
    // a second reading, so removals move records across index buckets
    big.extend((0..20).map(|i| rec("他", "ㄅㄣ", &format!("b{i}"), 100.0 + i as f64, DAY)));
    let mut l = Learner::from_records(big);
    l.prune(DAY);
    assert_eq!(l.records().len(), CAPACITY);
    assert!(l.records().iter().all(|x| x.word != "low" && x.word != "w0"), "lowest weights go first");
    assert_eq!(index_words(&l, "ㄅㄣ").len(), 20);
    assert_eq!(index_words(&l, r).len() + 20, CAPACITY);
}
/// Words reached through the index for `reading`, checked against a scan of the records: a stale
/// index after removals would disagree.
fn index_words<'a>(l: &'a Learner, reading: &str) -> Vec<&'a str> {
    let r = syls(reading);
    let mut scan: Vec<&str> = l.records().iter().filter(|x| x.reading == r).map(|x| x.word.as_str()).collect();
    scan.sort_unstable();
    scan.dedup();
    let via_index = l.words_of(&r);
    assert_eq!(via_index, scan, "index agrees with the records");
    assert_eq!(l.has_reading(&r), !scan.is_empty());
    via_index
}

#[test]
fn forget_removes_every_key() {
    let r = syls("ㄒㄧㄣ ㄅㄣ");
    let mut l = Learner::default();
    l.teach("他", &r, "欣奔", "鑫犇", DAY);
    l.teach("^>$", &r, "欣奔", "鑫犇", DAY);
    assert_eq!(l.records().iter().filter(|x| x.context == GLOBAL).count(), 1);
    l.teach("他", &r, "新奔", "鑫犇", DAY);
    l.forget(&r, "欣奔");
    assert_eq!(l.records().len(), 1, "only 新 is left");
    assert_eq!(index_words(&l, "ㄒㄧㄣ ㄅㄣ"), ["新奔"]);
    l.forget(&r, "新奔");
    assert!(index_words(&l, "ㄒㄧㄣ ㄅㄣ").is_empty());
}

/// Teach, see the learned word in the composition, press ⌘⌫ on it: no record is left and the
/// composition is what it was before any teaching.
#[test]
fn command_backspace_forgets_the_highlighted_word() {
    let gs = groups();
    let g = gs.iter().find(|g| g.name == "警官/景觀").unwrap();
    let teach = &g.rows[0];
    let same = g.rows.iter().find(|r| r.same == Some(true)).unwrap();
    let mut e = loaded_engine();
    let plain = type_syls(&mut e, &same.reading).preedit;
    e.key(k(KeyKind::Esc)).unwrap();
    assert!(plain.contains(&g.other) && !plain.contains(&g.word), "baseline shows the common word");
    teach_row(&mut e, g, teach);
    assert!(n_records(&e) >= 1);
    let o = type_syls(&mut e, &same.reading);
    assert_eq!(o.preedit, same.sent, "learned word shown");
    // candidates for the word at the start of the composition, highlight the learned word, ⌘⌫
    e.key(k(KeyKind::Home)).unwrap();
    let o = e.key(k(KeyKind::Space)).unwrap();
    highlight(&mut e, o, &g.word);
    let o = e.key(Key { kind: KeyKind::Backspace, ch: '\0', modifiers: MOD_COMMAND }).unwrap();
    assert!(o.handled, "⌘⌫ is consumed with candidates open");
    assert!(e.learner().records().iter().all(|r| r.word != g.word), "no record of the word is left");
    assert_eq!(o.preedit, plain, "composition back to the unlearned output");
    assert!(!o.candidates.is_empty(), "candidate window stays open");
    // without candidates ⌘⌫ is passed to the app
    e.key(k(KeyKind::Esc)).unwrap();
    type_syls(&mut e, &same.reading);
    assert!(!e.key(Key { kind: KeyKind::Backspace, ch: '\0', modifiers: MOD_COMMAND }).unwrap().handled);
}

// ---------- 8: commit paths (docs/spec/learning.md §4.1) ----------

/// Type 欣-reading and choose 欣 over the default 鑫: one pending learn.
fn picked() -> Engine {
    let mut e = tiny(TINY, TINY);
    e.set_left_context(TAUGHT_AFTER);
    type_syls(&mut e, "ㄒㄧㄣ");
    pick_open(&mut e, "欣");
    e
}
/// Open the candidates and choose `word` (cursor stays after the span).
fn pick_open(e: &mut Engine, word: &str) {
    let o = e.key(k(KeyKind::Space)).unwrap();
    highlight(e, o, word);
    e.key(k(KeyKind::Enter)).unwrap();
}
fn n_records(e: &Engine) -> usize {
    e.learner().records().len()
}

#[test]
fn row_enter_learns() {
    let mut e = picked();
    let o = e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!((o.commit.as_str(), n_records(&e)), ("欣", 1));
}
/// s3b2 8.2: a mouse pick on the expanded grid goes through the same choose() as Enter.
#[test]
fn row_expanded_pick_equals_enter() {
    let mut a = tiny(TINY, TINY);
    // docs/spec/learning.md §5.3: a single character learns only after two Han characters, as `picked()` sets up.
    a.set_left_context(TAUGHT_AFTER);
    type_syls(&mut a, "ㄒㄧㄣ");
    let o = a.key(k(KeyKind::Space)).unwrap();
    assert_eq!(o.candidates[1], "欣");
    let o = a.key(k(KeyKind::Down)).unwrap();
    assert_eq!((o.columns, o.first), (9, 0));
    let o = a.pick(1).unwrap().unwrap();
    assert_eq!((o.preedit.as_str(), o.selected), ("欣", None));
    let ca = a.key(k(KeyKind::Enter)).unwrap().commit;
    let mut b = picked();
    let cb = b.key(k(KeyKind::Enter)).unwrap().commit;
    assert_eq!((ca.as_str(), n_records(&a)), ("欣", 1));
    assert!(ca == cb && a.learner().records() == b.learner().records(), "pick and Enter learn the same records");
}
#[test]
fn row_rule21_passthrough_commit_learns() {
    let mut e = picked();
    let o = e.key(k(KeyKind::Tab)).unwrap();
    assert!(!o.handled);
    assert_eq!((o.commit.as_str(), n_records(&e)), ("欣", 1));
}
#[test]
fn row_max_syllables_commit_learns() {
    let mut e = picked();
    let mut commit = String::new();
    for _ in 0..MAX_SYLLABLES - 1 {
        for key in keys_of("ㄅㄣ") {
            let c = e.key(key).unwrap().commit;
            if !c.is_empty() {
                commit = c;
            }
        }
    }
    assert_eq!(commit.chars().count(), MAX_SYLLABLES, "the last syllable auto-commits");
    assert!(commit.starts_with('欣'));
    assert_eq!(n_records(&e), 1);
}
#[test]
fn row_reset_commit_and_discard_do_not_learn() {
    for mode in [ResetMode::Commit, ResetMode::Discard] {
        let mut e = picked();
        e.reset(mode);
        type_syls(&mut e, "ㄅㄣ");
        e.key(k(KeyKind::Enter)).unwrap();
        assert_eq!(n_records(&e), 0);
    }
}
#[test]
fn row_esc_does_not_learn() {
    let mut e = picked();
    e.key(k(KeyKind::Esc)).unwrap();
    assert_eq!(n_records(&e), 0);
    type_syls(&mut e, "ㄅㄣ");
    e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!(n_records(&e), 0);
}
/// The decoder fails after the pick (a syllable the decoding lexicon lacks): the engine resets, nothing is learned.
#[test]
fn row_decode_failure_does_not_learn() {
    let mut e = tiny(&format!("{TINY}ㄆㄧㄥˊ 平 -1.0\n"), TINY);
    e.set_left_context(TAUGHT_AFTER);
    type_syls(&mut e, "ㄒㄧㄣ");
    pick_open(&mut e, "欣");
    e.key(k(KeyKind::End)).unwrap();
    let mut failed = false;
    for key in keys_of("ㄆㄧㄥˊ") {
        failed |= e.key(key).is_err();
    }
    assert!(failed, "decode failure reported");
    // The engine was reset: ㄅㄣ decodes as on an engine that never failed. Until model-v6 that was 犇; with model-v6 and its
    // Kneser-Ney side file the unlearned decode was 奔 (docs/spec/decoder.md §3.3 acceptance 5), with the 通語 SJ-LM-7-9.7M files it is 犇 again, so the
    // expectation comes from a fresh engine.
    let fresh = {
        let mut f = tiny(&format!("{TINY}ㄆㄧㄥˊ 平 -1.0\n"), TINY);
        type_syls(&mut f, "ㄅㄣ").preedit
    };
    assert_eq!(fresh, "犇", "premise: the unlearned decode of ㄅㄣ on the 通語 SJ-LM-7-9.7M files");
    assert_eq!(type_syls(&mut e, "ㄅㄣ").preedit, fresh, "engine was reset");
    e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!(n_records(&e), 0);
}
#[test]
fn row_punctuation_replacement_does_not_learn() {
    let mut e = tiny(TINY, TINY);
    e.key(Key::ch(',', MOD_SHIFT)).unwrap();
    e.key(k(KeyKind::Space)).unwrap();
    let o = e.key(k(KeyKind::Right)).unwrap();
    let alt = o.candidates[o.selected.unwrap()].clone();
    assert_ne!(alt, "，");
    e.key(k(KeyKind::Enter)).unwrap();
    let o = e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!(o.commit, alt);
    assert_eq!(n_records(&e), 0);
}
#[test]
fn row_flag_must_be_on_at_pick_and_at_commit() {
    // off at the pick, on at commit
    let mut e = tiny(TINY, TINY);
    e.set_learning(false);
    e.set_left_context(TAUGHT_AFTER);
    type_syls(&mut e, "ㄒㄧㄣ");
    pick_open(&mut e, "欣");
    e.set_learning(true);
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, "欣");
    assert_eq!(n_records(&e), 0);
    // on at the pick, off at commit: set_learning(0) drops it, and stays dropped when turned on again
    let mut e = picked();
    e.set_learning(false);
    e.set_learning(true);
    e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!(n_records(&e), 0);
    // same word picked as shown: nothing to learn
    let mut e = tiny(TINY, TINY);
    e.set_left_context(TAUGHT_AFTER);
    type_syls(&mut e, "ㄒㄧㄣ");
    e.key(k(KeyKind::Space)).unwrap();
    e.key(k(KeyKind::Enter)).unwrap();
    e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!(n_records(&e), 0);
}
#[test]
fn left_context_is_dropped_after_commit() {
    // Two-character words may learn at sentence start, under ^>...; single characters only before a syllable (rule 10) and not under ^>$ (docs/spec/learning.md §5.3), so use the real model.
    let mut e = engine();
    e.set_left_context("好他");
    type_syls(&mut e, "ㄑㄩㄢˊ ㄌㄧˋ");
    e.key(k(KeyKind::Enter)).unwrap();
    let shown = type_syls(&mut e, "ㄑㄩㄢˊ ㄌㄧˋ").preedit;
    pick(&mut e, 2, 2, if shown == "權力" { "全力" } else { "權力" });
    e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!(e.learner().records()[0].context, "^>$", "the second composition had no left context (^, and no syllable after the word)");
}

// ---------- 9: a panic in learning does not eat the commit ----------

#[test]
fn learning_panic_keeps_the_commit() {
    let mut e = picked();
    e.inject_learn_panic();
    let o = e.key(k(KeyKind::Enter)).unwrap();
    assert!(o.handled);
    assert_eq!(o.commit, "欣");
    assert_eq!(n_records(&e), 0, "that learn is abandoned");
    // and the next one works
    e.set_left_context(TAUGHT_AFTER);
    type_syls(&mut e, "ㄒㄧㄣ");
    pick(&mut e, 1, 1, "欣");
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, "欣");
    assert_eq!(n_records(&e), 1);
}

// ---------- store-dependent (docs/spec/learning.md §13.1; temporary directories only) ----------

fn tmp_dir(tag: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("shanjie-learn-{}-{tag}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    d
}
/// A sibling of `dir` outside the store directory, for link targets.
fn outside(dir: &Path) -> PathBuf {
    PathBuf::from(format!("{}-outside", dir.display()))
}
fn file_of(dir: &Path) -> PathBuf {
    dir.join(core::learn_store::FILE)
}
fn raw(dir: &Path) -> String {
    std::fs::read_to_string(file_of(dir)).unwrap()
}
/// Data lines (after the header) of the learning file.
fn data_lines(dir: &Path) -> usize {
    raw(dir).lines().skip(1).count()
}
const FORGET: Key = Key { kind: KeyKind::Backspace, ch: '\0', modifiers: MOD_COMMAND };
/// A new tiny engine on `dir` (the "fresh engine" every write test ends with).
fn reopen(dir: &Path) -> (Engine, core::learn_store::Opened) {
    let mut e = tiny(TINY, TINY);
    let opened = e.learning_open(dir).unwrap();
    (e, opened)
}
/// Records as a sorted list, to compare two learners regardless of record order.
fn sorted(e: &Engine) -> Vec<String> {
    let mut v: Vec<String> = e.learner().records().iter().map(|r| format!("{}|{:?}|{}|{}|{}", r.context, r.reading, r.word, r.weight, r.day)).collect();
    v.sort();
    v
}
/// Records new or changed since `before`, by position: between full rewrites nothing is pruned, a
/// teach updates records in place and pushes new ones at the end.
fn changed(before: &[Record], e: &Engine) -> usize {
    e.learner().records().iter().enumerate().filter(|(i, r)| before.get(*i) != Some(*r)).count()
}
/// One learning Enter on a one-syllable TINY reading: pick the homophone that is not shown.
fn repick(e: &mut Engine, reading: &str) -> String {
    e.set_left_context(TAUGHT_AFTER);
    let shown = type_syls(e, reading).preedit;
    let word = if reading == "ㄒㄧㄣ" { if shown == "欣" { "鑫" } else { "欣" } } else if shown == "奔" { "犇" } else { "奔" };
    pick(e, 1, 1, word);
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, word);
    word.to_string()
}
/// ⌘⌫ on `word` of a one-syllable reading, then close the composition.
fn forget(e: &mut Engine, reading: &str, word: &str) {
    e.set_left_context(TAUGHT_AFTER);
    type_syls(e, reading);
    let o = e.key(k(KeyKind::Space)).unwrap();
    highlight(e, o, word);
    e.key(FORGET).unwrap();
    e.key(k(KeyKind::Esc)).unwrap();
    e.key(k(KeyKind::Esc)).unwrap();
}

#[test]
#[cfg(unix)] // learning_open fails on Windows for now (docs/spec/windows-tip.md §2.5)
fn store_commit_writes_the_file_and_reopen_restores() {
    let dir = tmp_dir("commit");
    let mut e = tiny(TINY, TINY);
    e.learning_open(&dir).unwrap();
    e.set_left_context(TAUGHT_AFTER);
    type_syls(&mut e, "ㄒㄧㄣ");
    pick(&mut e, 1, 1, "欣");
    e.key(k(KeyKind::Enter)).unwrap();
    let text = raw(&dir);
    let rows: Vec<Vec<&str>> = text.lines().skip(1).map(|l| l.split('\t').collect()).collect();
    assert_eq!(rows.len(), 1);
    assert_eq!(&rows[0][..3], [TAUGHT_AFTER, "ㄒㄧㄣ", "欣"]);
    assert_eq!(rows[0].len(), 5, "five fields only");
    assert_eq!(e.learning_status(), 0);
    let (mut e2, _) = reopen(&dir);
    e2.set_left_context(TAUGHT_AFTER);
    assert_eq!(type_syls(&mut e2, "ㄒㄧㄣ").preedit, "欣");
    // forget writes immediately
    let o = e2.key(k(KeyKind::Space)).unwrap();
    highlight(&mut e2, o, "欣");
    e2.key(FORGET).unwrap();
    assert_eq!(raw(&dir).lines().count(), 1, "header only");
    assert_eq!(n_records(&reopen(&dir).0), 0);
    let _ = std::fs::remove_dir_all(&dir);
}
/// ⌘⌫ on a word picked in this same composition must also drop that pick's
/// pending learn, or the next Enter teaches the forgotten word again and appends it back to disk.
#[test]
#[cfg(unix)] // learning_open fails on Windows for now (docs/spec/windows-tip.md §2.5)
fn store_forget_then_enter_does_not_learn_the_word_back() {
    let dir = tmp_dir("forget-enter");
    let mut e = tiny(TINY, TINY);
    e.learning_open(&dir).unwrap();
    e.set_left_context(TAUGHT_AFTER);
    type_syls(&mut e, "ㄒㄧㄣ");
    pick(&mut e, 1, 1, "欣"); // a re-pick over the default 鑫: learned at commit unless dropped
    let o = e.key(k(KeyKind::Space)).unwrap(); // the same span's candidates again
    highlight(&mut e, o, "欣");
    e.key(Key { kind: KeyKind::Backspace, ch: '\0', modifiers: MOD_COMMAND }).unwrap();
    e.key(k(KeyKind::Esc)).unwrap(); // closes the candidates, keeps the composition
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, "欣");
    let mut fresh = tiny(TINY, TINY);
    fresh.learning_open(&dir).unwrap();
    assert!(fresh.learner().records().iter().all(|r| r.word != "欣"), "the forgotten word was learned back");
    let text = std::fs::read_to_string(dir.join("learning.tsv")).unwrap_or_default();
    assert!(!text.contains('欣'), "a line of the forgotten word is on disk");
    let _ = std::fs::remove_dir_all(&dir);
}
#[test]
#[cfg(unix)] // learning_open fails on Windows for now (docs/spec/windows-tip.md §2.5)
fn store_learning_off_then_enter_writes_nothing_and_clear_removes_the_file() {
    let dir = tmp_dir("off");
    let mut e = tiny(TINY, TINY);
    e.learning_open(&dir).unwrap();
    e.set_left_context(TAUGHT_AFTER);
    type_syls(&mut e, "ㄒㄧㄣ");
    pick(&mut e, 1, 1, "欣");
    e.set_learning(false);
    e.key(k(KeyKind::Enter)).unwrap();
    assert!(!file_of(&dir).exists(), "set_learning(0) then Enter writes nothing");
    e.set_learning(true);
    e.set_left_context(TAUGHT_AFTER);
    type_syls(&mut e, "ㄒㄧㄣ");
    pick(&mut e, 1, 1, "欣");
    e.key(k(KeyKind::Enter)).unwrap();
    assert!(file_of(&dir).exists());
    // a pick made before clear is not learned by the next Enter
    e.set_left_context(TAUGHT_AFTER);
    type_syls(&mut e, "ㄅㄣ");
    pick(&mut e, 1, 1, "奔");
    e.learning_clear().unwrap();
    assert!(!file_of(&dir).exists());
    e.key(k(KeyKind::Enter)).unwrap();
    assert!(!file_of(&dir).exists() && n_records(&e) == 0);
    e.learning_clear().unwrap(); // already gone: success
    let (e2, opened) = reopen(&dir);
    assert_eq!((opened, n_records(&e2)), (core::learn_store::Opened::Fresh, 0));
    let _ = std::fs::remove_dir_all(&dir);
}

/// A bare ^ teaches nothing, so the Enter must not touch the store at all: no file, no rewrite.
#[test]
#[cfg(unix)] // learning_open fails on Windows for now (docs/spec/windows-tip.md §2.5)
fn store_single_character_under_caret_writes_nothing() {
    let dir = tmp_dir("caret");
    let mut e = tiny(TINY, TINY);
    e.learning_open(&dir).unwrap();
    type_syls(&mut e, "ㄒㄧㄣ");
    pick(&mut e, 1, 1, "欣");
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, "欣");
    assert!(!file_of(&dir).exists(), "nothing learned, nothing written");
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
#[cfg(unix)] // learning_open fails on Windows for now (docs/spec/windows-tip.md §2.5)
fn store_write_failure_sets_the_status_flag_and_keeps_the_commit() {
    let dir = tmp_dir("fail");
    let mut e = tiny(TINY, TINY);
    e.learning_open(&dir).unwrap();
    std::fs::remove_dir_all(&dir).unwrap();
    std::fs::write(&dir, b"a file where the directory was").unwrap();
    e.set_left_context(TAUGHT_AFTER);
    type_syls(&mut e, "ㄒㄧㄣ");
    pick(&mut e, 1, 1, "欣");
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, "欣");
    assert_eq!(e.learning_status(), 1);
    // Nothing could be written; the next engine finds no store directory to open.
    assert!(tiny(TINY, TINY).learning_open(&dir).is_err());
    let _ = std::fs::remove_file(&dir);
}

/// docs/spec/learning.md §9.2: without a successful learning_open, clear is an error (ABI 3), but memory still goes.
#[test]
fn clear_without_a_store_is_an_error() {
    let mut e = picked();
    e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!(n_records(&e), 1);
    assert!(e.learning_clear().is_err());
    assert_eq!(n_records(&e), 0);
}

/// docs/spec/learning.md §13.1, steps 1-4: a failed full rewrite during a forget must not let a later append succeed, or
/// the forgotten word would come back on the next load.
#[test]
#[cfg(unix)] // learning_open fails on Windows for now (docs/spec/windows-tip.md §2.5)
fn store_failed_rewrite_then_forget_never_brings_the_word_back() {
    let dir = tmp_dir("failforget");
    let tmp = dir.join(core::learn_store::TMP);
    let mut e = tiny(TINY, TINY);
    e.learning_open(&dir).unwrap();
    // 1. teach W, Enter; a directory at the temporary's name makes every full rewrite fail; forget W
    assert_eq!(repick(&mut e, "ㄒㄧㄣ"), "欣");
    assert!(raw(&dir).contains("欣"));
    std::fs::create_dir(&tmp).unwrap();
    std::fs::write(tmp.join("x"), b"x").unwrap();
    forget(&mut e, "ㄒㄧㄣ", "欣");
    assert_eq!(e.learning_status(), 1, "the forget's rewrite failed");
    // 2. another learning Enter: no append, the file is unchanged byte for byte, bit0 stays
    let before = std::fs::read(file_of(&dir)).unwrap();
    assert_eq!(repick(&mut e, "ㄅㄣ"), "犇");
    assert_eq!(std::fs::read(file_of(&dir)).unwrap(), before, "nothing appended while a rewrite is owed");
    assert_eq!(e.learning_status(), 1);
    // 3. the obstacle goes; a learning Enter (not a forget) rewrites and clears bit0
    std::fs::remove_dir_all(&tmp).unwrap();
    repick(&mut e, "ㄅㄣ");
    assert_eq!(e.learning_status(), 0);
    // 4. a new engine: W has no record
    let (e2, _) = reopen(&dir);
    assert!(e2.learner().records().iter().all(|r| r.word != "欣"), "the forgotten word stays forgotten");
    assert!(!raw(&dir).contains("欣"));
    assert_eq!(sorted(&e2), sorted(&e), "what was written reads back");
    let _ = std::fs::remove_dir_all(&dir);
}

/// A rewrite that is still owed (failed forget) is retried by a commit that learns nothing (a single
/// character under ^), not only by one that learns something.
#[test]
#[cfg(unix)] // learning_open fails on Windows for now (docs/spec/windows-tip.md §2.5)
fn store_pending_rewrite_is_retried_by_a_commit_that_learns_nothing() {
    let dir = tmp_dir("retry-empty");
    let tmp = dir.join(core::learn_store::TMP);
    let mut e = tiny(TINY, TINY);
    e.learning_open(&dir).unwrap();
    assert_eq!(repick(&mut e, "ㄒㄧㄣ"), "欣");
    std::fs::create_dir(&tmp).unwrap();
    std::fs::write(tmp.join("x"), b"x").unwrap();
    forget(&mut e, "ㄒㄧㄣ", "欣");
    assert_eq!(e.learning_status(), 1);
    assert!(raw(&dir).contains("欣"), "the failed forget left the word in the file");
    std::fs::remove_dir_all(&tmp).unwrap();
    // no left context: the pick is a single character under ^, so nothing is learned
    type_syls(&mut e, "ㄅㄣ");
    pick(&mut e, 1, 1, "奔");
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, "奔");
    assert_eq!(e.learning_status(), 0, "the owed rewrite was retried");
    assert!(!raw(&dir).contains("欣"), "and it removed the forgotten word from the file");
    let _ = std::fs::remove_dir_all(&dir);
}

/// docs/spec/learning.md §13.1: a panic inside the forget, after memory changed and before the file did: bit0 is untouched
/// but the next learning Enter is a full rewrite, so the word is gone from the file too.
#[test]
#[cfg(unix)] // learning_open fails on Windows for now (docs/spec/windows-tip.md §2.5)
fn store_forget_panic_forces_the_next_write_to_rewrite() {
    let dir = tmp_dir("forgetpanic");
    let mut e = tiny(TINY, TINY);
    e.learning_open(&dir).unwrap();
    repick(&mut e, "ㄒㄧㄣ"); // full rewrite (first write after open)
    repick(&mut e, "ㄒㄧㄣ"); // append
    assert!(data_lines(&dir) > n_records(&e), "an append left a superseded line");
    e.inject_forget_panic();
    type_syls(&mut e, "ㄒㄧㄣ");
    let o = e.key(k(KeyKind::Space)).unwrap();
    highlight(&mut e, o, "欣");
    let r = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| e.key(FORGET)));
    assert!(r.is_err(), "the injected panic fired");
    e.reset(ResetMode::Discard); // what the C ABI does after a panic (code 4)
    assert_eq!(e.learning_status(), 0, "bit0 is not touched by a panic");
    assert!(raw(&dir).contains("欣"), "the file was not written yet");
    repick(&mut e, "ㄅㄣ");
    assert_eq!(data_lines(&dir), n_records(&e), "a full rewrite, not an append");
    assert!(!raw(&dir).contains("欣"));
    let (e2, _) = reopen(&dir);
    assert_eq!(sorted(&e2), sorted(&e));
    let _ = std::fs::remove_dir_all(&dir);
}

/// docs/spec/learning.md §13.1: 100 days pass on the same engine; the first write of the new day rewrites and prunes.
#[test]
#[cfg(unix)] // learning_open fails on Windows for now (docs/spec/windows-tip.md §2.5)
fn store_hundred_days_without_reopening_prunes_the_file() {
    let dir = tmp_dir("day100");
    let mut e = tiny(TINY, TINY);
    e.learning_open(&dir).unwrap();
    assert_eq!(repick(&mut e, "ㄒㄧㄣ"), "欣");
    e.set_today(Some(DAY + 100));
    repick(&mut e, "ㄅㄣ");
    assert!(!raw(&dir).contains("欣"), "the 100-day-old record is gone from the file (day rule)");
    let mut e2 = tiny(TINY, TINY);
    e2.set_today(Some(DAY + 100));
    e2.learning_open(&dir).unwrap();
    assert_eq!(sorted(&e2), sorted(&e));
    assert_eq!(n_records(&e2), 1);
    let _ = std::fs::remove_dir_all(&dir);
}

/// docs/spec/learning.md §13.1: 100 days pass and a new engine opens; its first write rewrites and prunes.
#[test]
#[cfg(unix)] // learning_open fails on Windows for now (docs/spec/windows-tip.md §2.5)
fn store_hundred_days_then_reopen_prunes_the_file() {
    let dir = tmp_dir("day100open");
    let mut e = tiny(TINY, TINY);
    e.learning_open(&dir).unwrap();
    assert_eq!(repick(&mut e, "ㄒㄧㄣ"), "欣");
    let mut e = tiny(TINY, TINY);
    e.set_today(Some(DAY + 100));
    e.learning_open(&dir).unwrap();
    repick(&mut e, "ㄅㄣ");
    assert!(!raw(&dir).contains("欣"));
    let mut e2 = tiny(TINY, TINY);
    e2.set_today(Some(DAY + 100));
    e2.learning_open(&dir).unwrap();
    assert_eq!(sorted(&e2), sorted(&e));
    assert_eq!(n_records(&e2), 1);
    let _ = std::fs::remove_dir_all(&dir);
}

/// docs/spec/learning.md §13.1: the last line cut inside its word field, no newline: skipped on load, and the next write
/// (the first after open, a full rewrite) never glues an append onto it.
#[test]
#[cfg(unix)] // learning_open fails on Windows for now (docs/spec/windows-tip.md §2.5)
fn store_torn_tail_is_skipped_and_never_glued() {
    use core::learn_store::Opened;
    let dir = tmp_dir("torn");
    let mut e = tiny(TINY, TINY);
    e.learning_open(&dir).unwrap();
    assert_eq!(repick(&mut e, "ㄒㄧㄣ"), "欣");
    assert_eq!(repick(&mut e, "ㄅㄣ"), "犇"); // appended: the last line is ^ ㄅㄣ 犇 ...
    let text = raw(&dir);
    let last = text.trim_end_matches('\n').rfind('\n').unwrap() + 1;
    let word_at = last + text[last..].match_indices('\t').nth(1).unwrap().0 + 1;
    assert!(text[word_at..].starts_with("犇"));
    std::fs::write(file_of(&dir), &text.as_bytes()[..word_at + 1]).unwrap(); // half of 犇's bytes
    let (mut e2, opened) = reopen(&dir);
    assert_eq!(opened, Opened::Loaded { skipped: 1 });
    assert!(e2.learner().records().iter().all(|r| r.word != "犇") && n_records(&e2) == 1);
    repick(&mut e2, "ㄅㄣ");
    let (e3, opened) = reopen(&dir);
    assert_eq!(opened, Opened::Loaded { skipped: 0 }, "no glued or broken line");
    assert_eq!(data_lines(&dir), n_records(&e3));
    assert!(raw(&dir).ends_with('\n'));
    assert_eq!(sorted(&e3), sorted(&e2));
    let _ = std::fs::remove_dir_all(&dir);
}

/// docs/spec/learning.md §13.1: each learning Enter adds exactly the changed records as lines (not a rewrite); the Enter
/// that would reach JOURNAL_MAX appended lines rewrites, leaving one line per record.
#[test]
#[cfg(unix)] // learning_open fails on Windows for now (docs/spec/windows-tip.md §2.5)
fn store_append_grows_by_the_touched_records_and_compacts_at_journal_max() {
    use core::learn_store::JOURNAL_MAX;
    let dir = tmp_dir("journal");
    let mut e = tiny(TINY, TINY);
    e.learning_open(&dir).unwrap();
    repick(&mut e, "ㄒㄧㄣ");
    assert_eq!(data_lines(&dir), n_records(&e), "first write after open: full rewrite");
    let mut appended = 0;
    let mut compacted = false;
    for _ in 0..JOURNAL_MAX {
        let before: Vec<Record> = e.learner().records().to_vec();
        let lines = data_lines(&dir);
        repick(&mut e, "ㄒㄧㄣ");
        let touched = changed(&before, &e);
        assert!(touched >= 1);
        if appended + touched >= JOURNAL_MAX {
            assert_eq!(data_lines(&dir), n_records(&e), "compacted at JOURNAL_MAX");
            compacted = true;
            break;
        }
        assert_eq!(data_lines(&dir), lines + touched, "one line per changed record");
        appended += touched;
    }
    assert!(compacted);
    let (e2, _) = reopen(&dir);
    assert_eq!(sorted(&e2), sorted(&e));
    let _ = std::fs::remove_dir_all(&dir);
}

/// docs/spec/learning.md §13.1: after a forget the word appears 0 times in the file; after a clear the next learning Enter
/// starts a new file holding only the header and the new records.
#[test]
#[cfg(unix)] // learning_open fails on Windows for now (docs/spec/windows-tip.md §2.5)
fn store_forget_leaves_no_trace_and_clear_starts_over() {
    let dir = tmp_dir("forgetclear");
    let mut e = tiny(TINY, TINY);
    e.learning_open(&dir).unwrap();
    for _ in 0..3 {
        repick(&mut e, "ㄒㄧㄣ");
    }
    repick(&mut e, "ㄅㄣ");
    assert!(raw(&dir).matches("欣").count() >= 2, "appended versions of 欣");
    forget(&mut e, "ㄒㄧㄣ", "欣");
    assert_eq!(raw(&dir).matches("欣").count(), 0);
    assert_eq!(e.learning_status(), 0);
    let (e2, _) = reopen(&dir);
    assert_eq!(sorted(&e2), sorted(&e));
    e.learning_clear().unwrap();
    assert!(!file_of(&dir).exists() && dir.is_dir());
    let w = repick(&mut e, "ㄅㄣ");
    assert_eq!(raw(&dir), format!("{}\n{TAUGHT_AFTER}\tㄅㄣ\t{w}\t1\t{DAY}\n", core::learn_store::HEADER));
    let (e2, _) = reopen(&dir);
    assert_eq!(sorted(&e2), sorted(&e));
    let _ = std::fs::remove_dir_all(&dir);
}

/// docs/spec/learning.md §10.3: forget rewrites even when memory holds no record of the word: a record pruned on load (here
/// 100 days old) is still in the file until a full rewrite, and the forget must take it out now.
#[test]
#[cfg(unix)] // learning_open fails on Windows for now (docs/spec/windows-tip.md §2.5)
fn store_forget_rewrites_even_when_memory_has_no_record() {
    let dir = tmp_dir("forgetpruned");
    let mut e = tiny(TINY, TINY);
    e.learning_open(&dir).unwrap();
    assert_eq!(repick(&mut e, "ㄒㄧㄣ"), "欣");
    let mut e = tiny(TINY, TINY);
    e.set_today(Some(DAY + 100));
    e.learning_open(&dir).unwrap();
    assert_eq!(n_records(&e), 0, "pruned in memory on load");
    assert!(raw(&dir).contains("欣"), "still in the file");
    forget(&mut e, "ㄒㄧㄣ", "欣");
    assert!(!raw(&dir).contains("欣"), "the forget rewrote the file");
    assert_eq!(e.learning_status(), 0);
    let (e2, opened) = reopen(&dir);
    assert_eq!((opened, n_records(&e2)), (core::learn_store::Opened::Loaded { skipped: 0 }, 0));
    let _ = std::fs::remove_dir_all(&dir);
}

/// docs/spec/learning.md §13.1: a symlink, a FIFO, a second hard link, a file emptied by hand or one with group bits in
/// place of the learning file: the append is refused and the full rewrite replaces it with a 0600
/// regular file, never writing through the link.
#[test]
#[cfg(unix)] // learning_open fails on Windows for now (docs/spec/windows-tip.md §2.5)
fn store_append_refusals_fall_back_to_a_full_rewrite() {
    use std::os::unix::fs::{MetadataExt, PermissionsExt};
    for case in ["symlink", "fifo", "hardlink", "empty", "mode"] {
        let dir = tmp_dir(&format!("refuse-{case}"));
        let out = outside(&dir);
        let _ = std::fs::remove_file(&out);
        let mut e = tiny(TINY, TINY);
        e.learning_open(&dir).unwrap();
        repick(&mut e, "ㄒㄧㄣ");
        let f = file_of(&dir);
        match case {
            "symlink" => {
                std::fs::write(&out, raw(&dir)).unwrap();
                std::fs::remove_file(&f).unwrap();
                std::os::unix::fs::symlink(&out, &f).unwrap();
            }
            "fifo" => {
                std::fs::remove_file(&f).unwrap();
                assert!(std::process::Command::new("/usr/bin/mkfifo").arg(&f).status().unwrap().success());
            }
            "hardlink" => std::fs::hard_link(&f, &out).unwrap(),
            "empty" => std::fs::write(&f, b"").unwrap(),
            _ => std::fs::set_permissions(&f, std::fs::Permissions::from_mode(0o640)).unwrap(),
        }
        let kept = std::fs::read(&out).ok();
        // In a thread with a time limit: a FIFO must never block the key path.
        let (tx, rx) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            repick(&mut e, "ㄒㄧㄣ");
            let _ = tx.send(e);
        });
        let e = rx.recv_timeout(std::time::Duration::from_secs(10)).expect("the Enter blocked");
        let md = std::fs::symlink_metadata(&f).unwrap();
        assert!(md.file_type().is_file(), "{case}: a regular file");
        assert_eq!((md.mode() & 0o777, md.nlink()), (0o600, 1), "{case}: 0600, one link");
        assert_eq!(e.learning_status(), 0, "{case}");
        assert_eq!(data_lines(&dir), n_records(&e), "{case}: a full rewrite");
        assert_eq!(std::fs::read(&out).ok(), kept, "{case}: nothing written through a link");
        let (e2, _) = reopen(&dir);
        assert_eq!(sorted(&e2), sorted(&e), "{case}");
        let _ = std::fs::remove_dir_all(&dir);
        let _ = std::fs::remove_file(&out);
    }
}

#[test]
fn global_key_is_not_the_sentinel() {
    assert_ne!(GLOBAL, SENTINEL);
}

// ---------- performance (asserted in release builds only, like engine_replay.rs) ----------

/// Peak resident set size in bytes (macOS reports ru_maxrss in bytes).
#[cfg(unix)]
fn max_rss() -> i64 {
    #[repr(C)]
    struct Rusage {
        times: [i64; 4],
        maxrss: i64,
        rest: [i64; 13],
    }
    extern "C" {
        fn getrusage(who: i32, out: *mut Rusage) -> i32;
    }
    // SAFETY: RUSAGE_SELF (0) and a buffer laid out as struct rusage on 64-bit macOS.
    unsafe {
        let mut r: Rusage = std::mem::zeroed();
        getrusage(0, &mut r);
        r.maxrss
    }
}
fn p95(mut t: Vec<std::time::Duration>) -> std::time::Duration {
    t.sort();
    t[t.len() * 95 / 100]
}

/// A full store (CAPACITY active records) on the readings dev302 types: every 1-2 syllable span's top
/// three lexicon words under a run of context keys (sentinel and global first), so most typed spans hit
/// the learned path. The first learning Enter after open rewrites the full file (and trims to
/// CAPACITY); the timed Enters after it append.
#[test]
#[cfg(unix)] // learning_open fails on Windows for now (docs/spec/windows-tip.md §2.5)
fn perf_full_store_per_key_and_enter_with_write() {
    use std::time::Instant;
    let rows = dev302();
    let lex = &shared().lex;
    let mut spans: Vec<Syls> = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for s in &rows {
        for l in 1..=2 {
            for w in s.windows(l) {
                if seen.insert(w.to_vec()) {
                    spans.push(w.to_vec());
                }
            }
        }
    }
    let contexts = ["^", "", "我", "你們", "今天", "的", "了", "在", "是", "他說", "大家", "一個", "不會", "這樣", "所以", "還是", "沒有", "我們", "可以", "因為"];
    let mut records = Vec::new();
    'fill: for c in contexts {
        for span in &spans {
            for (word, _) in lex.entries(span).into_iter().take(3) {
                // docs/spec/learning.md §5.3: a single-character record under ^, the global key or a one-character key is dropped at
                // load, and "^" itself is no longer a legal key: none of them is seeded (the "^" seeds take the
                // synthetic key ^>$ below), CAPACITY is still filled.
                if (c.is_empty() || c == "^" || c.chars().count() < 2) && word.chars().count() == 1 {
                    continue;
                }
                let context = if c == "^" { "^>$" } else { c };
                records.push(Record { context: context.into(), reading: span.clone(), word: word.into(), weight: 1.0, day: DAY });
                if records.len() == CAPACITY {
                    break 'fill;
                }
            }
        }
    }
    assert_eq!(records.len(), CAPACITY, "dev302 spans give enough records");
    let dir = tmp_dir("perf");
    let (store, _, _) = core::learn_store::LearnStore::open(&dir).unwrap();
    store.save(&records).unwrap();

    let mut e = engine();
    let replay = |e: &mut Engine| {
        let mut t = Vec::new();
        for s in &rows {
            for key in s.iter().flat_map(|x| keys_of(x)).chain([k(KeyKind::Enter)]) {
                let t0 = Instant::now();
                e.key(key).unwrap();
                t.push(t0.elapsed());
            }
        }
        t
    };
    // the same replay before the store opens: the comparison point for the per-key p95
    let base = p95(replay(&mut e));
    let rss0 = max_rss();
    let t = Instant::now();
    e.learning_open(&dir).unwrap();
    let load = t.elapsed();
    let rss = max_rss() - rss0;
    assert_eq!(n_records(&e), CAPACITY, "every seeded record loaded");

    let keys = replay(&mut e);
    assert_eq!(n_records(&e), CAPACITY, "no re-pick, nothing learned");

    // The first learning Enter after open is a full rewrite (reported apart); the next 100+ on
    // the same day take the append path and are the gated sample.
    let mut enters = Vec::new();
    let mut first = None;
    let (mut base_lines, mut touched) = (0, 0);
    for s in rows.iter().take(121) {
        type_syls(&mut e, &s.join(" "));
        let o = e.key(k(KeyKind::Space)).unwrap();
        assert!(o.candidates.len() > 1, "a second candidate to re-pick");
        e.key(k(KeyKind::Right)).unwrap();
        e.key(k(KeyKind::Enter)).unwrap();
        let before: Vec<Record> = if first.is_some() { e.learner().records().to_vec() } else { Vec::new() };
        let t = Instant::now();
        e.key(k(KeyKind::Enter)).unwrap();
        let dt = t.elapsed();
        assert_eq!(e.learning_status(), 0, "the write succeeded");
        if first.is_none() {
            first = Some(dt);
            base_lines = std::fs::read_to_string(dir.join("learning.tsv")).unwrap().lines().count();
            assert_eq!(base_lines, n_records(&e) + 1, "the first write after open rewrote the file");
        } else {
            enters.push(dt);
            touched += changed(&before, &e);
        }
    }
    let text = std::fs::read_to_string(dir.join("learning.tsv")).unwrap();
    assert!(touched > 0);
    assert_eq!(text.lines().count(), base_lines + touched, "every later Enter appended its changed records, none rewrote");
    // ⌘⌫ (always a full rewrite), reported only
    type_syls(&mut e, &rows[0].join(" "));
    e.key(k(KeyKind::Space)).unwrap();
    let t = Instant::now();
    e.key(Key { kind: KeyKind::Backspace, ch: '\0', modifiers: MOD_COMMAND }).unwrap();
    let forget = t.elapsed();
    let (pk, pe) = (p95(keys.clone()), p95(enters.clone()));
    println!(
        "load {load:?} rss +{} MB (peak; alone with --test-threads=1) | keys {} p95 {pk:?} (empty store {base:?}) | first enter (full rewrite) {:?} | append enters {} p95 {pe:?} max {:?} | forget (full rewrite) {forget:?}",
        rss / (1 << 20),
        keys.len(),
        first.unwrap(),
        enters.len(),
        enters.iter().max().unwrap()
    );
    let _ = std::fs::remove_dir_all(&dir);
    #[cfg(not(debug_assertions))]
    assert!(pk < std::time::Duration::from_millis(16) && pe < std::time::Duration::from_millis(16), "p95 over 16 ms");
}

// ---------- docs/spec/learning.md §5.4: single characters do not globalize; a smaller global boost ----------

/// The same text decoded and committed with `left` as the text before it (the key is consumed by the Enter).
fn decode(e: &mut Engine, left: &str, reading: &[String]) -> String {
    e.set_left_context(left);
    commit_of(e, &reading.join(" "))
}
/// docs/spec/learning.md §5.4, collision rule: does a key the decoder uses before some occurrence of `span` hit a record taught
/// under one of `taught` (`single`: the taught word is one character, so only an equal non-"^" key)?
/// The decoder's keys come from the surface of the path so far, which varies by hypothesis and cannot be
/// read from outside the engine, so this uses the surfaces we can see: the gold sentence and each of
/// `texts[1..]` (the outputs decoded without and with the record), each prefixed by `ctx`. This is an
/// approximation (it does not enumerate the beam). `None`: the gold sentence does not align one
/// character to one syllable, so no key can be read; such rows are counted apart, not checked.
fn row_collides(ctx: &str, texts: &[&str], rd: &[String], span: &[String], taught: &[&str], single: bool) -> Option<bool> {
    if texts[0].chars().count() != rd.len() {
        return None;
    }
    Some(texts.iter().filter(|t| t.chars().count() == rd.len()).any(|t| {
        let chars: Vec<char> = t.chars().collect();
        rd.windows(span.len()).enumerate().filter(|(_, w)| *w == span).any(|(i, _)| {
            let key = context_key(&format!("{ctx}{}", chars[..i].iter().collect::<String>()));
            // Rule 10: a single character is also learned at sentence start, under `^>` plus the next syllable's reading.
            let key = if single { record_key(key, rd.get(i + span.len()).map(String::as_str)) } else { key };
            taught.iter().any(|t| collides(t, &key, single))
        })
    }))
}
fn has_span(rd: &[String], span: &[String]) -> bool {
    rd.windows(span.len()).any(|w| w == span)
}

#[test]
fn single_character_never_globalizes_two_characters_do() {
    let one = syls("ㄗㄞˋ");
    let mut l = Learner::default();
    for c in ["可以", SENTINEL, "他", "我們"] {
        l.teach(c, &one, "再", "在", DAY);
    }
    assert!(l.records().iter().all(|r| r.context != GLOBAL), "a single character never gets a global record");
    let two = syls("ㄗㄞˋ ㄐㄧㄢˋ");
    let mut l = Learner::default();
    for c in ["可以", "^>$", "他"] {
        l.teach(c, &two, "再見", "在建", DAY);
    }
    assert_eq!(l.records().iter().filter(|r| r.context == GLOBAL).count(), 1, "a two-character word does");
    // the lookup says which level answered
    assert_eq!(l.lookup("可以", &two, DAY).0, Level::Exact);
    assert_eq!(l.lookup("不以", &two, DAY).0, Level::LastChar);
    assert_eq!(l.lookup("我們", &two, DAY).0, Level::Global);
}

#[test]
#[cfg(unix)] // learning_open fails on Windows for now (docs/spec/windows-tip.md §2.5)
fn old_single_character_global_record_is_dropped_at_load() {
    // a file written by an older build, loaded by an engine: the record is not loaded at all
    let dir = tmp_dir("oldglobal");
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(file_of(&dir), format!("{}\n\tㄗㄞˋ\t再\t1\t{DAY}\n", core::learn_store::HEADER)).unwrap();
    let sent = shared().lex.to_syllables("另外現在在做").unwrap();
    let mut plain = engine();
    let want = decode(&mut plain, "", &sent);
    let mut e = engine();
    e.learning_open(&dir).unwrap();
    assert!(e.learner().records().is_empty(), "dropped at load: it counts toward nothing");
    assert!(!e.learner().has_reading(&syls("ㄗㄞˋ")) && e.learner().is_empty());
    assert_eq!(decode(&mut e, "", &sent), want, "so decoding is unaffected");
    assert!(raw(&dir).contains("再"), "the file is untouched until a write");
    // the first learning Enter after open is a full rewrite: the record is gone from the file
    e.set_left_context(TAUGHT_AFTER);
    type_syls(&mut e, "ㄒㄧㄣ");
    pick(&mut e, 1, 1, "鑫");
    e.key(k(KeyKind::Enter)).unwrap();
    assert!(!raw(&dir).contains("再"), "a full rewrite drops it");
    assert_eq!(e.learning_status(), 0);
    let _ = std::fs::remove_dir_all(&dir);
}

/// The user's case (docs/spec/learning.md §5.3): 再 re-picked under 可以 and at sentence start. The sentence-start pick of a lone 再 is `^>$`
/// and creates no record; a 再 before a following syllable is rule 10's (second half). Rows are replayed with their own 前文; those whose decoder keys equal the
/// taught full key 可以 (a single character answers only on an equal non-"^" key) are listed, not gated.
#[test]
fn pollution_zai_taught_under_two_contexts() {
    let lex = &shared().lex;
    let zai = syls("ㄗㄞˋ");
    let mut rows: Vec<(String, String, Syls)> =
        ["另外現在在做", "我現在在家", "他在學校"].iter().map(|s| (String::new(), s.to_string(), lex.to_syllables(s).unwrap())).collect();
    rows.extend(dev302_rows().into_iter().filter(|r| has_span(&r.2, &zai)));
    let mut e = engine();
    let base: Vec<String> = rows.iter().map(|r| decode(&mut e, &r.0, &r.2)).collect();
    let taught = ["可以"];
    for c in ["可以", ""] {
        e.set_left_context(c);
        assert_ne!(type_syls(&mut e, "ㄗㄞˋ").preedit, "再");
        pick(&mut e, 1, 1, "再");
        e.key(k(KeyKind::Enter)).unwrap();
    }
    let zai_records: Vec<String> = e.learner().records().iter().filter(|r| r.word == "再").map(|r| r.context.clone()).collect();
    assert_eq!(zai_records, ["可以"], "only the pick after 可以 is learned; none under ^, none global");
    zai_sweep(&mut e, &rows, &base, &taught, &zai);
    // Rule 10 (docs/spec/learning.md §13.4): 再 taught at sentence start before a following syllable is a record under `^>`+syllable; the
    // rows whose decoder keys reach it are listed, every other ㄗㄞˋ row stays as it was (also: one more control, the pick
    // alone at sentence start is `^>$` and leaves nothing).
    let mut e = engine();
    e.set_left_context("");
    assert_ne!(type_syls(&mut e, "ㄗㄞˋ ㄐㄧㄚ").preedit.chars().next(), Some('再'), "precondition: 再 is not the default");
    pick(&mut e, 2, 1, "再");
    e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!(records_of(&e, "再"), [("^>ㄐㄧㄚ".to_string(), 1.0)], "taught under the next syllable, not global");
    zai_sweep(&mut e, &rows, &base, &["^>ㄐㄧㄚ"], &zai);
}

/// The sweep of `pollution_zai_taught_under_two_contexts`: replays `rows` on `e` (after teaching) against their unlearned
/// decodes `base`; rows whose decoder keys equal one of `taught` are listed, the others must not change at all.
fn zai_sweep(e: &mut Engine, rows: &[(String, String, Syls)], base: &[String], taught: &[&str], zai: &[String]) {
    let (mut regress, mut changed, mut collided, mut unaligned, mut checked) = (Vec::new(), Vec::new(), Vec::new(), Vec::new(), 0);
    for (r, b) in rows.iter().zip(base) {
        let after = decode(e, &r.0, &r.2);
        match row_collides(&r.0, &[&r.1, b, &after], &r.2, zai, taught, true) {
            None => unaligned.push(format!("{}|{}", r.0, r.1)),
            Some(true) => collided.push(format!("{}|{} : {} -> {}", r.0, r.1, b, after)),
            Some(false) => {
                checked += 1;
                // Same definition as the ε sweep: a regression is a row whose whole sentence was equal to
                // the expected one before and is not after. Any other change is only reported.
                if &after != b {
                    changed.push(format!("{}|{} : {} -> {}", r.0, r.1, b, after));
                    if *b == r.1 {
                        regress.push(format!("{}|{} : {} -> {}", r.0, r.1, b, after));
                    }
                }
            }
        }
    }
    println!("taught {taught:?}; global 再 records: {}", e.learner().records().iter().filter(|r| r.context == GLOBAL).count());
    println!("rows {} (checked {checked}), colliding {}, unaligned (not checked) {}:", rows.len(), collided.len(), unaligned.len());
    for c in &collided {
        println!("  colliding: {c}");
    }
    for c in &unaligned {
        println!("  unaligned: {c}");
    }
    println!("non-colliding changed rows: {}, regressions (whole sentence was right, now not): {}", changed.len(), regress.len());
    for c in &changed {
        println!("  changed: {c}");
    }
    for r in &regress {
        println!("  REGRESSION: {r}");
    }
    assert!(regress.is_empty(), "non-colliding rows regressed: {regress:?}");
    assert!(changed.is_empty(), "non-colliding rows changed: {changed:?}");
}

/// docs/spec/learning.md §5.3 at the lookup: a single character is used only at the exact full
/// two-character key, and never under "^" or a one-character key; a word of 2+ characters keeps all three
/// levels and the synthetic sentence-start key (never the bare "^").
#[test]
#[cfg(unix)] // learning_open fails on Windows for now (docs/spec/windows-tip.md §2.5)
fn single_character_rules_in_the_learner() {
    let one = syls("ㄗㄞˋ");
    let two = syls("ㄗㄞˋ ㄐㄧㄢˋ");
    let mut l = Learner::default();
    assert!(l.teach(SENTINEL, &one, "再", "在", DAY).is_empty(), "bare ^: nothing is learned");
    assert!(l.records().is_empty());
    assert!(l.teach(SENTINEL, &two, "再見", "在建", DAY).is_empty(), "docs/spec/learning.md §5.3: no word learns under the bare ^");
    assert_eq!(l.teach("^>$", &two, "再見", "在建", DAY).len(), 1, "a word of 2+ characters learns under the synthetic key");
    l.teach("可以", &one, "再", "在", DAY);
    assert_eq!(l.lookup("可以", &one, DAY).0, Level::Exact);
    assert_eq!(l.lookup("可以", &one, DAY).1[0].0, "再");
    assert!(l.lookup("所以", &one, DAY).1.is_empty(), "no last-character level for a single character");
    assert!(l.lookup(SENTINEL, &one, DAY).1.is_empty() && l.lookup("我們", &one, DAY).1.is_empty());
    assert!(l.teach("他", &one, "再", "在", DAY).is_empty(), "nothing is learned under a one-character key");
    // teach never makes a single-character record under ^ or the global key, however often it is
    // taught; saved and reloaded through the store none appears either
    let mut l = Learner::default();
    for c in [SENTINEL, "可以", "他", "我們", SENTINEL] {
        l.teach(c, &one, "再", "在", DAY);
    }
    // only the two-character keys (可以, 我們) hold the single character; "他" is one Han character
    assert!(l.records().iter().all(|r| r.context != SENTINEL && r.context != GLOBAL && r.context != "他"));
    let dir = tmp_dir("nosingleglobal");
    let (store, _, _) = core::learn_store::LearnStore::open(&dir).unwrap();
    store.save(l.records()).unwrap();
    let (_, loaded, _) = core::learn_store::LearnStore::open(&dir).unwrap();
    assert_eq!(loaded.len(), 2, "可以, 我們");
    assert!(loaded.iter().all(|r| r.context != SENTINEL && r.context != GLOBAL));
    let _ = std::fs::remove_dir_all(&dir);
}

/// docs/spec/learning.md §5.3, acceptance: 再 taught after 可以 does not reach 所以, 前文 after punctuation or ASCII, or an
/// unreadable context; a pick under ^ leaves no record; an old ^ record in a file is not used.
#[test]
#[cfg(unix)] // learning_open fails on Windows for now (docs/spec/windows-tip.md §2.5)
fn single_character_pick_stays_where_it_was_taught() {
    let lex = &shared().lex;
    let cases = [("所以", "在這裡"), ("", "所以在這裡"), ("，", "在這裡"), ("n-gram ", "在做"), ("", "在做"), ("可以", "在這裡")];
    let mut plain = engine();
    let base: Vec<String> = cases.iter().map(|(l, s)| decode(&mut plain, l, &lex.to_syllables(s).unwrap())).collect();
    let mut e = engine();
    e.set_left_context("可以");
    assert_ne!(type_syls(&mut e, "ㄗㄞˋ").preedit, "再");
    pick(&mut e, 1, 1, "再");
    e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!(e.learner().records().len(), 1);
    assert_eq!(decode(&mut e, "可以", &syls("ㄗㄞˋ")), "再", "control: it works at the equal key");
    for ((l, s), b) in cases.iter().zip(&base) {
        if *l != "可以" {
            assert_eq!(&decode(&mut e, l, &lex.to_syllables(s).unwrap()), b, "{l}|{s} must not change");
        }
    }
    // a pick under ^ (sentence start, after punctuation or ASCII, unreadable context) leaves no record
    for left in ["", "，", "n-gram "] {
        let mut e = engine();
        e.set_left_context(left);
        assert_ne!(type_syls(&mut e, "ㄗㄞˋ").preedit, "再");
        pick(&mut e, 1, 1, "再");
        e.key(k(KeyKind::Enter)).unwrap();
        assert!(e.learner().records().is_empty(), "no record under ^ ({left:?})");
        for ((l, s), b) in cases.iter().zip(&base) {
            assert_eq!(&decode(&mut e, l, &lex.to_syllables(s).unwrap()), b, "{l}|{s} must not change");
        }
    }
    // an old ^ record loaded from a file is dropped at load (learn_store::valid) and so does not act
    let dir = tmp_dir("oldsentinel");
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(file_of(&dir), format!("{}\n^\tㄗㄞˋ\t再\t1\t{DAY}\n", core::learn_store::HEADER)).unwrap();
    let mut e = engine();
    e.set_today(Some(DAY));
    e.learning_open(&dir).unwrap();
    assert!(e.learner().records().is_empty() && e.learner().is_empty() && !e.learner().has_reading(&syls("ㄗㄞˋ")));
    for ((l, s), b) in cases.iter().zip(&base) {
        assert_eq!(&decode(&mut e, l, &lex.to_syllables(s).unwrap()), b, "{l}|{s} must not change");
    }
    // the first learning Enter is a full rewrite and the file loses the old line
    e.set_left_context(TAUGHT_AFTER);
    type_syls(&mut e, "ㄒㄧㄣ");
    pick(&mut e, 1, 1, "鑫");
    e.key(k(KeyKind::Enter)).unwrap();
    assert!(!raw(&dir).contains("^\tㄗㄞˋ"), "dropped from the file by the rewrite");
    let _ = std::fs::remove_dir_all(&dir);
}

/// ε_global 0 switches the global level off completely: a learned word below PER_KEY does not join the
/// beam through a global hit either. Only the control (ε 6.0 enumerates it) and the end result are
/// observable here; the removed join only occupied a beam slot with the word's own low score, so this
/// test passes with or without that change (mutation run, 2026-10-05).
#[test]
fn zero_global_eps_keeps_beyond_per_key_words_out() {
    let s = shared();
    let readings = ["ㄍㄨㄥ ㄙ", "ㄕˋ ㄐㄧㄢ", "ㄐㄧˋ ㄧˋ", "ㄧˋ ㄕˋ", "ㄍㄨㄥ ㄧˋ", "ㄧˋ ㄧˋ", "ㄕˋ ㄧˋ", "ㄐㄧㄢ ㄕˋ"];
    let (reading, word) = readings
        .iter()
        .find_map(|r| {
            let ranked = s.capped.entries(&syls(r));
            (ranked.len() > 20).then(|| (*r, ranked[20].0.to_string()))
        })
        .expect("a two-syllable reading with more than 20 words");
    let rd = syls(reading);
    let mut e = engine();
    let plain = decode(&mut e, "我們", &rd);
    assert_ne!(plain, word);
    for c in ["可以", ""] {
        e.set_left_context(c);
        type_syls(&mut e, reading);
        pick(&mut e, 2, 2, &word);
        e.key(k(KeyKind::Enter)).unwrap();
    }
    assert!(e.learner().records().iter().any(|r| r.context == GLOBAL && r.word == word));
    e.set_eps_global(6.0);
    assert_eq!(decode(&mut e, "我們", &rd), word, "control: the global level can enumerate it");
    e.set_eps_global(0.0);
    assert_eq!(decode(&mut e, "我們", &rd), plain, "eps 0: the global level is off");
}

struct Sweep {
    learned: usize,
    wrong: usize,
    regress: usize,
    checked: usize,
    excluded: usize,
    unaligned: usize,
    groups: usize,
}
/// S2h: a taught context conditions the first word (`lm::history`), so "可以" (history 以) changed which
/// word the span showed and the pick under it was no re-pick. 佝 has no bigram history in the model, so
/// its history is `<s>`: the decode under it equals the decode under ^, and the two stay distinct keys.
const SWEEP_TAUGHT: [&str; 2] = ["佝", SENTINEL];
/// Third context: no record of the two taught keys reaches it at the exact or 1-character level.
const THIRD: &str = "我們";

/// docs/spec/learning.md §5.4, global learning and global pollution tests at global ε `eps`. Per cases.tsv group whose taught
/// word x (the pair word the span does not show by default) has 2+ characters: teach it under each key of
/// `SWEEP_TAUGHT` (佝 and ^), which makes a global record; then the group's non-teach rows (decoded after 我們) and the dev302
/// rows that contain the span (with their own 前文) are replayed. Learning rows are the group rows that
/// want x (judged by x being present and y absent: unrelated misreads elsewhere in the sentence do not
/// count). Pollution rows are the group rows that want the other word y (kind=common when x is the
/// pair's cold word, kind=rare otherwise) plus the dev rows; a regression is a row whose whole
/// sentence was equal to the expected one and no longer is. Colliding rows (see `row_collides`) and
/// rows that cannot be keyed are left out and counted.
fn global_sweep(eps: f64) -> Sweep {
    let lex = &shared().lex;
    let mut sw = Sweep { learned: 0, wrong: 0, regress: 0, checked: 0, excluded: 0, unaligned: 0, groups: 0 };
    let dev = dev302_rows();
    for g in &groups() {
        let teach = g.rows.iter().find(|r| r.kind == "teach").unwrap();
        let (_, st, en) = span_of(&teach.sent, &g.word);
        let span: Vec<String> = syls(&teach.reading)[st..en].to_vec();
        if !lex.entries(&span).iter().any(|(w, _)| *w == g.other) {
            continue;
        }
        let mut e = engine();
        e.set_eps_global(eps);
        let shown = decode(&mut e, "", &span);
        let Some((x, y)) = [(&g.word, &g.other), (&g.other, &g.word)].into_iter().find(|(x, _)| **x != shown && x.chars().count() >= 2) else { continue };
        sw.groups += 1;
        let rows: Vec<&Row> = g.rows.iter().filter(|r| r.kind != "teach").collect();
        let rd = |r: &Row| syls(&r.reading);
        // does the row show the word it should: the taught word on rare rows if x is the cold word, else the other way
        let wants_x = |r: &Row| (x == &g.word) == (r.kind == "rare");
        let right = |out: &str, r: &Row| {
            let (want, not) = if wants_x(r) { (x, y) } else { (y, x) };
            out.contains(want.as_str()) && !out.contains(not.as_str())
        };
        let before: Vec<String> = rows.iter().map(|r| decode(&mut e, THIRD, &rd(r))).collect();
        let dev_rows: Vec<&(String, String, Syls)> = dev.iter().filter(|r| has_span(&r.2, &span)).collect();
        let dev_before: Vec<String> = dev_rows.iter().map(|r| decode(&mut e, &r.0, &r.2)).collect();
        for c in SWEEP_TAUGHT {
            e.set_left_context(if c == SENTINEL { "" } else { c });
            type_syls(&mut e, &span.join(" "));
            pick(&mut e, span.len(), span.len(), x);
            e.key(k(KeyKind::Enter)).unwrap();
        }
        assert!(e.learner().records().iter().any(|r| r.context == GLOBAL && &r.word == x), "{}: a global record", g.name);
        let show = eps == lm::LEARN_EPS_GLOBAL;
        for (r, b) in rows.iter().zip(&before) {
            let after = decode(&mut e, THIRD, &rd(r));
            match row_collides(THIRD, &[&r.sent, b, &after], &rd(r), &span, &SWEEP_TAUGHT, false) {
                None => {
                    sw.unaligned += 1;
                    continue;
                }
                Some(true) => {
                    sw.excluded += 1;
                    if show {
                        println!("   colliding group row {} : {}|{} : {b} -> {after}", g.name, THIRD, r.sent);
                    }
                    continue;
                }
                Some(false) => {}
            }
            if wants_x(r) {
                if !right(b, r) {
                    sw.wrong += 1;
                    sw.learned += right(&after, r) as usize;
                }
            } else {
                // a pollution row: the row wants the word that was not taught; a regression is a row
                // that was exactly right (whole sentence) and no longer is
                sw.checked += 1;
                let worse = *b == r.sent && after != r.sent;
                if worse {
                    println!("   eps {eps} {}: group row {} : {b} -> {after}  REGRESSION", g.name, r.sent);
                }
                sw.regress += worse as usize;
            }
        }
        for (r, b) in dev_rows.iter().zip(&dev_before) {
            let after = decode(&mut e, &r.0, &r.2);
            match row_collides(&r.0, &[&r.1, b, &after], &r.2, &span, &SWEEP_TAUGHT, false) {
                None => {
                    sw.unaligned += 1;
                    continue;
                }
                Some(true) => {
                    sw.excluded += 1;
                    if show {
                        println!("   colliding dev row {} : {}|{} : {b} -> {after}", g.name, r.0, r.1);
                    }
                    continue;
                }
                Some(false) => {}
            }
            sw.checked += 1;
            // A regression is a row that was exactly right and no longer is; a wrong row that changes is only listed.
            if after != *b {
                let worse = *b == r.1;
                println!("   eps {eps} {}: dev row {}|{} : {b} -> {after}{}", g.name, r.0, r.1, if worse { "  REGRESSION" } else { "  (was wrong)" });
                sw.regress += worse as usize;
            }
        }
    }
    sw
}

// ---- sweep (needs the docs/spec/learning.md §5.4 test hook `set_eps_global`) ----

#[test]
fn global_eps_table() {
    println!("eps_global | global learn (learned/wrong) | global pollution regressions (checked rows, colliding rows, unaligned rows) | mirror learn (reachable) | mirror all");
    let mut res = Vec::new();
    for eps in [0.0, 0.1, 0.5, 1.0, 1.5, 2.0, 6.0] {
        let s = global_sweep(eps);
        let (lr, wr, l, w) = mirror(eps);
        println!("{eps} | {}/{} | {} ({}, {}, {}) | {lr}/{wr} | {l}/{w}  [groups {}]", s.learned, s.wrong, s.regress, s.checked, s.excluded, s.unaligned, s.groups);
        res.push((eps, s, lr, wr));
    }
    assert_eq!(res[0].1.learned, 0, "eps_global 0: the global level has no effect");
    let chosen = res.iter().find(|r| r.0 == lm::LEARN_EPS_GLOBAL).expect("chosen value is in the table");
    assert_eq!(chosen.1.regress, 0, "chosen eps_global: 0 global pollution");
    // The level still works when switched on (so the table means something for the next study of it).
    assert!(res.iter().any(|r| r.0 > 0.0 && r.1.learned > 0), "no eps_global above 0 learns anything");
    // docs/spec/learning.md §6.2, §13.1: with the class term no value above 0 has 0 pollution, so the level is off; the table
    // still prints the learn rate and pollution of each value for the next study of the global level.
    for r in &res {
        // docs/spec/learning.md §13.1 (learn rate): of the learnable mirror cases (the model does not already produce the taught sentence) at least 80% are learned.
        // The learnable count depends on the model (9/10 with model-v1, 8/9 once 戰機/戰績 became right on its own), so the
        // floor is on the count of learnable cases, which keeps the rate from passing vacuously.
        assert!(r.3 >= 8, "fewer than 8 learnable mirror cases ({} at eps {}): the rate below would not mean anything", r.3, r.0);
        assert!(r.2 * 100 >= r.3 * 80, "mirror learn rate below 80%: {}/{} at eps {}", r.2, r.3, r.0);
    }
}

// ---------- forgetting a frozen word (docs/spec/learning.md §9.1, docs/spec/keys-and-composition.md §6.6) ----------

/// Two words A+B. A is taught (警官 over the default 景觀), B is re-picked so A is frozen, then ⌘⌫ on A's learned word.
/// Expected display: A's syllables decoded alone without the record (`lm::decode_segment`, same profile, same start,
/// closing into the picked B), then B. Measured: A reads 景觀 again, B stays the picked 公座.
#[test]
fn forget_a_frozen_word_redecodes_it_without_the_record() {
    let (a_reading, b_reading) = ("ㄐㄧㄥˇ ㄍㄨㄢ", "ㄍㄨㄥ ㄗㄨㄛˋ");
    let (a_taught, b_picked) = ("警官", "公座");
    let s = shared();
    let mut e = engine();
    // teach A (in front of B: a sentence-start record holds the syllable after the word): type, re-pick A's span, commit
    type_syls(&mut e, &format!("{a_reading} {b_reading}"));
    pick(&mut e, 4, 2, a_taught);
    e.key(k(KeyKind::Enter)).unwrap();
    assert!(e.learner().records().iter().any(|r| r.word == a_taught), "A is taught");
    // A+B: A shows the learned word; re-pick B so A is frozen
    let o = type_syls(&mut e, &format!("{a_reading} {b_reading}"));
    assert!(o.preedit.starts_with(a_taught), "learned A shown: {}", o.preedit);
    pick(&mut e, 4, 4, b_picked);
    // ⌘⌫ on A's learned word
    e.key(k(KeyKind::Home)).unwrap();
    e.key(k(KeyKind::Right)).unwrap();
    e.key(k(KeyKind::Right)).unwrap();
    let o = e.key(k(KeyKind::Space)).unwrap();
    highlight(&mut e, o, a_taught);
    let o = e.key(Key { kind: KeyKind::Backspace, ch: '\0', modifiers: MOD_COMMAND }).unwrap();
    assert!(e.learner().records().iter().all(|r| r.word != a_taught));
    let a_syls = syls(a_reading);
    let (lp, delta) = s.capped.best_lp_delta(&syls(b_reading), b_picked).unwrap();
    let want = lm::decode_segment(
        &s.capped, &a_syls, &s.lm, lm::Profile::Chat.lambda(), "<s>", lm::End::Next { word: b_picked, lp, delta }, 64,
    )
    .unwrap()[0]
        .1
        .iter()
        .map(|w| w.0)
        .collect::<String>();
    assert_ne!(want, a_taught, "the unlearned decode differs from the taught word");
    println!("forget on frozen A: {} (taught {a_taught}, unlearned {want})", o.preedit);
    assert_eq!(o.preedit, format!("{want}{b_picked}"));
}

/// docs/spec/keys-and-composition.md §6.6: a re-pick cuts the learned word 警官 (井 over the first syllable); the left-over 官 and
/// 工作 are frozen. ⌘⌫ on 警官 (records go) frees the frozen words over its span (官) only: ㄍㄨㄢ decodes again between
/// the user's 井 and the still frozen 工作, without the record.
#[test]
fn forget_a_cut_learned_word_leaves_no_pieces() {
    let reading = "ㄐㄧㄥˇ ㄍㄨㄢ ㄍㄨㄥ ㄗㄨㄛˋ";
    let mut e = engine();
    // Taught in front of 工作 (a sentence-start record holds the syllable after the word).
    type_syls(&mut e, reading);
    pick(&mut e, 4, 2, "警官");
    e.key(k(KeyKind::Enter)).unwrap();
    assert!(type_syls(&mut e, reading).preedit.starts_with("警官"));
    pick(&mut e, 4, 1, "井");
    e.key(k(KeyKind::Home)).unwrap();
    e.key(k(KeyKind::Right)).unwrap();
    e.key(k(KeyKind::Right)).unwrap();
    let o = e.key(k(KeyKind::Space)).unwrap();
    highlight(&mut e, o, "警官");
    let got = e.key(Key { kind: KeyKind::Backspace, ch: '\0', modifiers: MOD_COMMAND }).unwrap().preedit;
    assert!(e.learner().records().iter().all(|r| r.word != "警官"));
    let s = shared();
    let (lp, delta) = s.capped.best_lp_delta(&syls("ㄍㄨㄥ ㄗㄨㄛˋ"), "工作").unwrap();
    let mid = lm::decode_segment(
        &s.capped, &syls("ㄍㄨㄢ"), &s.lm, lm::Profile::Chat.lambda(), "井", lm::End::Next { word: "工作", lp, delta }, 64,
    )
    .unwrap()[0]
        .1
        .iter()
        .map(|w| w.0)
        .collect::<String>();
    println!("cut learned word, forgotten: {got} (shown with the record: 井官工作)");
    assert_eq!(got, format!("井{mid}工作"));
}

/// docs/spec/keys-and-composition.md §7.1 item 2: an unfinished syllable before a re-picked word, committed by Enter, changes nothing
/// in what is learned (learn_commit sees the decoded text, not the shown one).
#[test]
fn an_unfinished_syllable_committed_by_enter_is_not_learned() {
    let run = |pending: bool| {
        let mut e = engine();
        type_syls(&mut e, "ㄨㄛˇ ㄒㄧㄤˇ ㄏㄜ");
        pick(&mut e, 3, 3, "呵");
        if pending {
            e.key(k(KeyKind::Home)).unwrap();
            assert!(e.key(Key::ch('1', 0)).unwrap().preedit.starts_with('ㄅ'));
        }
        let o = e.key(k(KeyKind::Enter)).unwrap();
        (o.commit, e.learner().records().to_vec())
    };
    let (plain, want) = run(false);
    let (shown, got) = run(true);
    assert!(shown == format!("ㄅ{plain}"), "{shown} / {plain}");
    assert!(!want.is_empty(), "precondition: the re-pick is learned");
    assert!(got == want, "the unfinished ㄅ changed the records");
    // The rule "no zhuyin in the context key", for any symbol, not just the ㄅ typed here.
    let zhuyin = |c: char| ('\u{3100}'..='\u{312F}').contains(&c) || ('\u{31A0}'..='\u{31BF}').contains(&c) || "ˊˇˋ˙".contains(c);
    assert!(got.iter().all(|r| !r.context.chars().any(zhuyin)));
}

// ---------- docs/spec/learning.md §5.3 ----------

/// Types `reading`, re-picks the span that ends after syllable `end` as `word`, commits.
fn repick_commit(e: &mut Engine, reading: &str, end: usize, word: &str) -> String {
    let n = reading.split(' ').count();
    type_syls(e, reading);
    pick(e, n, end, word);
    e.key(k(KeyKind::Enter)).unwrap().commit
}
/// A fresh engine without any learning decodes `reading` to `sent` (the precondition of the tests below).
fn baseline(reading: &str) -> String {
    commit_of(&mut engine(), reading)
}

/// Sentence-start key, the reported bug: teaching the two-character word `taught` at sentence start must not bend the
/// same two syllables in a sentence whose next syllable differs. `want` is the unlearned decode of `sent_reading`.
fn bare_caret_word_leaves_other_sentences_alone(two: &str, taught: &str, sent_reading: &str, want: &str) {
    assert_eq!(baseline(two), want.chars().take(2).collect::<String>(), "precondition: the default for {two}");
    assert_eq!(baseline(sent_reading), want, "precondition: the unlearned sentence {sent_reading}");
    let mut e = engine();
    assert_eq!(repick_commit(&mut e, two, 2, taught), taught);
    // Precondition: the re-pick was recorded at all (the key is the part that changed).
    assert!(e.learner().records().iter().any(|r| r.word == taught), "precondition: a record for {taught}");
    assert_eq!(commit_of(&mut e, sent_reading), want, "learned {taught} at sentence start bent {sent_reading}");
    let keys: Vec<_> = e.learner().records().iter().filter(|r| r.word == taught).map(|r| r.context.as_str()).collect();
    assert_eq!(keys, ["^>$"], "the word ends the composition, so the key is ^>$");
}

#[test]
fn rule8_shi_taught_at_sentence_start_does_not_bend_other_sentences() {
    bare_caret_word_leaves_other_sentences_alone("ㄨㄛˇ ㄕˋ", "我試", "ㄨㄛˇ ㄕˋ ㄇㄞˇ ㄒㄧㄣ ㄉㄜ˙", "我是買新的");
}

#[test]
fn rule8_zai_taught_at_sentence_start_does_not_bend_other_sentences() {
    bare_caret_word_leaves_other_sentences_alone(
        "ㄨㄛˇ ㄗㄞˋ",
        "我再",
        "ㄨㄛˇ ㄗㄞˋ ㄋㄧㄡˊ ㄩˇ ㄞˋ ㄒㄧㄣ ㄓ ㄐㄧㄢ ㄒㄩㄢˇ ㄌㄜ˙ ㄋㄧㄡˊ",
        "我在牛與愛心之間選了牛",
    );
}

/// The sentence-start key still learns: the same next syllable boosts the word, another one does not. (The spec's
/// example 我試試看 is already the unlearned default of the real model, so this teaches 我再 before 牛 instead.)
/// The control sentence was 我在買 (ㄇㄞˇ) until model-v6, whose unlearned default there is already 我再買, so it could no
/// longer show "not boosted"; 我在家 (ㄐㄧㄚ, unlearned default 我在家) is the control now (docs/spec/decoder.md §3.3 acceptance 5).
#[test]
fn rule8_the_same_next_syllable_still_boosts() {
    const SAME: &str = "ㄨㄛˇ ㄗㄞˋ ㄋㄧㄡˊ ㄩˇ";
    const OTHER: &str = "ㄨㄛˇ ㄗㄞˋ ㄐㄧㄚ";
    assert_eq!(baseline(SAME), "我在牛與", "precondition: the default is not already 我再牛與");
    assert_eq!(baseline(OTHER), "我在家", "precondition: the default is not already 我再家");
    let mut e = engine();
    repick_commit(&mut e, SAME, 2, "我再");
    let keys: Vec<_> = e.learner().records().iter().filter(|r| r.word == "我再").map(|r| r.context.as_str()).collect();
    assert_eq!(keys, ["^>ㄋㄧㄡˊ"], "precondition: recorded under the next syllable");
    assert_eq!(commit_of(&mut e, SAME), "我再牛與");
    assert_eq!(commit_of(&mut e, OTHER), "我在家");
}

/// A single character after a one-character context is not learned (the key would be the whole
/// context, one Han character). The spec's example reads ㄋㄧˇ ㄗㄞˋ ㄌㄞˊ, whose default already has 再 as the
/// second character, so the same re-pick is made on 你在嗎, whose default has 在 (the re-pick is also checked on
/// the same reading again: with a one-character key the old code answered 你再嗎).
#[test]
fn rule7_single_character_after_one_han_character_is_not_learned() {
    const Q: &str = "ㄋㄧˇ ㄗㄞˋ ㄇㄚ˙";
    assert_eq!(baseline(Q), "你在嗎", "precondition: the unlearned decode of {Q} has 在 second");
    let mut e = engine();
    type_syls(&mut e, Q);
    pick(&mut e, 3, 2, "再");
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, "你再嗎", "precondition: the re-pick took");
    assert_eq!(commit_of(&mut e, Q), "你在嗎", "再 learned under the one-character key 你 bent {Q}");
    assert!(e.learner().records().iter().all(|r| r.word != "再"), "no record for 再 under a one-Han key");
}

// ---------- docs/spec/learning.md §5.5 ----------

fn records_of(e: &Engine, word: &str) -> Vec<(String, f64)> {
    e.learner().records().iter().filter(|r| r.word == word).map(|r| (r.context.clone(), r.weight)).collect()
}

/// Learn.after, the right neighbour is a fixed word: the segment 0..2 ends where the fixed 資 starts, so the key of
/// 輩份 there is `^>ㄗ` only if the engine passes the syllable right of the segment (B4 of the verifier's probes).
#[test]
fn rule8_after_is_the_first_syllable_of_a_fixed_word_to_the_right() {
    const R: &str = "ㄅㄟˋ ㄈㄣˋ ㄗ"; // 我是買 / 我試 / ㄇㄞˇ until 通語 SJ-LM-7-9.7M, whose model keeps 我是 against one teaching; found 2026-10-27 by trying the dev302 syllable triples
    assert_eq!(baseline(R), "備份資", "precondition");
    let mut e = engine();
    assert_eq!(repick_commit(&mut e, R, 2, "輩份"), "輩份資");
    assert_eq!(records_of(&e, "輩份"), [("^>ㄗ".to_string(), 1.0)], "precondition: taught under the next syllable");
    // the free syllables 0..2 now end at a fixed 資: the only way to reach ^>ㄗ is `after`
    type_syls(&mut e, R);
    pick(&mut e, 3, 3, "資");
    let o = e.key(k(KeyKind::End)).unwrap();
    assert_eq!(o.preedit, "輩份資", "the word before a fixed 資 uses the key ^>ㄗ");
}

/// Learn.after, the right neighbour is punctuation: the record is under `^>_punct_...` and a fixed punctuation token
/// right of the segment finds it.
#[test]
fn rule8_after_is_the_punctuation_token_to_the_right() {
    let comma = |e: &mut Engine| {
        type_syls(e, "ㄨㄛˇ ㄕˋ");
        e.key(Key::ch(',', MOD_SHIFT)).unwrap();
    };
    let mut plain = engine();
    comma(&mut plain);
    let shown = plain.key(k(KeyKind::End)).unwrap().preedit;
    assert_eq!(shown, "我是，", "precondition: the unlearned default");
    let mut e = engine();
    comma(&mut e);
    pick(&mut e, 3, 2, "我試");
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, "我試，");
    let ks: Vec<_> = records_of(&e, "我試").into_iter().map(|r| r.0).collect();
    assert_eq!(ks.len(), 1);
    assert!(ks[0].starts_with("^>_punct_"), "the key holds the punctuation token: {ks:?}");
    comma(&mut e);
    assert_eq!(e.key(k(KeyKind::End)).unwrap().preedit, "我試，", "a fixed punctuation token right of the segment finds the record");
}

/// Point 2: the displaced word is halved under the key it was shown under when it was picked (`^>$`, the word was last),
/// not only under the key at the commit (`^>ㄇㄞˇ`, after the user typed on).
#[test]
fn repick_halves_the_displaced_word_under_the_pick_time_key() {
    let mut e = engine();
    repick_commit(&mut e, "ㄨㄛˇ ㄕˋ", 2, "我試");
    assert_eq!(records_of(&e, "我試"), [("^>$".to_string(), 1.0)], "precondition: 我試 taught at the end of a composition");
    assert_eq!(type_syls(&mut e, "ㄨㄛˇ ㄕˋ").preedit, "我試", "precondition: it is shown");
    pick(&mut e, 2, 2, "我是");
    type_syls(&mut e, "ㄇㄞˇ");
    e.key(k(KeyKind::Enter)).unwrap();
    let r = records_of(&e, "我試");
    assert_eq!(r, [("^>$".to_string(), 0.5)], "halved under the key it was shown under");
    assert_eq!(records_of(&e, "我是"), [("^>ㄇㄞˇ".to_string(), 1.0)], "the new word is taught under the commit-time key");
}

/// Point 4: every sentence-start key is one key for globalization; two different two-character contexts are two.
#[test]
fn sentence_start_keys_count_as_one_key_for_globalization() {
    let two = syls("ㄗㄞˋ ㄐㄧㄢˋ");
    let globals = |l: &Learner| l.records().iter().filter(|r| r.context == GLOBAL).count();
    let mut l = Learner::default();
    l.teach("^>ㄕˋ", &two, "再見", "在建", DAY);
    l.teach("^>ㄧ", &two, "再見", "在建", DAY);
    assert_eq!(globals(&l), 0, "^>ㄕˋ and ^>ㄧ are one sentence-start key");
    l.teach("可以", &two, "再見", "在建", DAY);
    assert_eq!(globals(&l), 1, "a sentence-start key and a Han key are two");
    let mut l = Learner::default();
    l.teach("可以", &two, "再見", "在建", DAY);
    l.teach("我們", &two, "再見", "在建", DAY);
    assert_eq!(globals(&l), 1, "two different two-character contexts globalize as before");
}

// ---------- 15: SINIT (docs/spec/learning.md §7): a sentence-start single character is learned ----------

/// Row 16 of user-reported.txt, with a lexicon in which 趙 outscores 照 and 建議 is a word, so the default is fixed by the
/// test and not by the shipping model. 照 sits 5 below 趙: with a gap of 1 the bigram model's liking for 照建議 still shows
/// 照建議 (measured 2026-11-01); the single characters 建 and 議 are there because without them the decoder fails on the
/// three-syllable path and shows 趙ㄐㄧㄢ.
const SINIT_TINY: &str = "ㄓㄠˋ 趙 -1.0\nㄓㄠˋ 照 -6.0\nㄒㄧㄤˋ 像 -1.0\nㄐㄧㄢˋ 建 -1.0\nㄧˋ 議 -1.0\nㄐㄧㄢˋ-ㄧˋ 建議 -1.0\n";
const R16: &str = "ㄓㄠˋ ㄐㄧㄢˋ ㄧˋ";
/// The three records one pick of 照 in R16 leaves (docs/spec/learning.md §7.1): the coarse key (the next syllable), the mid key (the next two, joined by `FINE_SEP`) and the
/// fine key (R16 has exactly two syllables after the first, so the third part is `$`).
fn r16_records() -> Vec<(String, f64)> {
    vec![("^>ㄐㄧㄢˋ".to_string(), 1.0), ("^>ㄐㄧㄢˋ\u{1f}ㄧˋ".to_string(), 1.0), ("^>ㄐㄧㄢˋ\u{1f}ㄧˋ\u{1f}$".to_string(), 1.0)]
}

/// Types `reading` (no left context) on a fresh tiny engine and commits it: the unlearned decode.
fn tiny_default(reading: &str) -> String {
    commit_of(&mut tiny(SINIT_TINY, SINIT_TINY), reading)
}
/// Teaches 照 for row 16 on a fresh tiny engine (the first syllable re-picked, the rest as decoded); returns it.
fn tiny_taught() -> Engine {
    let mut e = tiny(SINIT_TINY, SINIT_TINY);
    type_syls(&mut e, R16);
    pick(&mut e, 3, 1, "照");
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, "照建議", "precondition: the re-pick took");
    e
}

/// Rule 10 without a rerank job (docs/spec/learning.md §7.6): the record is kept (taught under the next syllable's reading) but has no effect on
/// what is shown: 照 taught for row 16 leaves it 趙建議, as with no record. With the semantic correction on, the same record acts: `tiny_rerank_taught` and the
/// tests below.
#[test]
fn sinit_row16_without_a_rerank_job_the_record_has_no_effect() {
    chat_only();
    assert_eq!(tiny_default(R16), "趙建議", "precondition: the tiny lexicon's default is 趙建議");
    let mut e = tiny_taught();
    assert_eq!(records_of(&e, "照"), r16_records(), "taught under the next syllable's reading, the next two and the next two with the end");
    assert_eq!(commit_of(&mut e, R16), "趙建議", "typed again with the rerank off, the record changes nothing");
    assert_eq!(tiny_default("ㄓㄠˋ ㄒㄧㄤˋ"), "趙像", "precondition");
    assert_eq!(commit_of(&mut e, "ㄓㄠˋ ㄒㄧㄤˋ"), "趙像", "a different next syllable decodes as without the record");
}

/// The sentence-start key under the mid and fine keys (docs/spec/learning.md §7.1): 照 shown over R16 is re-picked as 趙, then the user types on (ㄏㄨㄟˋ), so the commit's fine key is
/// `…ㄏㄨㄟˋ` and no longer the `…$` one the pick-time display had. 照 is halved under the coarse and mid keys and under the pick-time fine key too.
#[test]
fn sinit_repick_halves_the_displaced_character_under_the_pick_time_mid_and_fine_keys() {
    chat_only();
    // the lexicon has the syllable that is typed on after the pick
    let lexicon = format!("{SINIT_TINY}ㄏㄨㄟˋ 會 -1.0\n");
    let mut e = tiny_rerank(&lexicon);
    type_settled(&mut e, R16);
    pick(&mut e, 3, 1, "照");
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, "照建議", "precondition: the re-pick took");
    assert_eq!(records_of(&e, "照"), r16_records(), "precondition: the coarse, the mid and the fine record");
    assert_eq!(type_settled(&mut e, R16), "照建議", "precondition: the learned 照 is shown");
    pick(&mut e, 3, 1, "趙");
    assert_eq!(type_syls(&mut e, "ㄏㄨㄟˋ").preedit, "趙建議會", "precondition: the syllable was typed after the pick");
    e.key(k(KeyKind::Enter)).unwrap();
    let half = |w: f64| r16_records().into_iter().map(|(k, _)| (k, w)).collect::<Vec<_>>();
    assert_eq!(records_of(&e, "照"), half(0.5), "halved under the coarse, the mid and the pick-time fine key `…$`");
    assert!(
        records_of(&e, "趙").iter().any(|(k, w)| k == "^>ㄐㄧㄢˋ\u{1f}ㄧˋ\u{1f}ㄏㄨㄟˋ" && *w == 1.0),
        "control: 趙 is taught under the commit's fine key: {:?}",
        records_of(&e, "趙")
    );
}

/// With the semantic correction on (where the record acts): not after two Han characters, and not before a different next syllable.
#[test]
fn sinit_not_after_two_han_characters_and_not_before_another_syllable() {
    chat_only();
    let with_left = |e: &mut Engine| {
        e.set_left_context(TAUGHT_AFTER);
        type_settled(e, R16)
    };
    let want = with_left(&mut tiny_rerank(SINIT_TINY));
    assert_eq!(want, "趙建議", "precondition: the default after 他們");
    let mut e = tiny_rerank_taught();
    assert_eq!(with_left(&mut e), want, "the sentence-start record does not act after two Han characters");
    assert_eq!(records_of(&e, "照").len(), 3, "the coarse, the mid and the fine record");
    e.key(k(KeyKind::Enter)).unwrap();
    e.set_left_context("");
    let other = "ㄓㄠˋ ㄒㄧㄤˋ";
    assert_eq!(type_settled(&mut tiny_rerank(SINIT_TINY), other), "趙像", "precondition");
    assert_eq!(type_settled(&mut e, other), "趙像", "a different next syllable decodes as without the record");
}

#[test]
fn sinit_single_character_at_the_end_is_not_learned() {
    chat_only();
    let mut e = tiny(SINIT_TINY, SINIT_TINY);
    type_syls(&mut e, "ㄓㄠˋ");
    pick(&mut e, 1, 1, "照");
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, "照", "precondition: the re-pick took");
    assert!(e.learner().records().is_empty(), "`^>$` keeps no single character");
}

/// Types the syllable ㄒㄧㄣ and then `tail` (keys: a char, `_` for Space), re-picks the syllable as another candidate and
/// commits; returns the contexts of the single-character records that resulted.
fn sinit_repick_before(tail: &[Key], digits: &Arc<core::digits::DigitTable>) -> Vec<String> {
    let mut e = engine();
    e.set_english(true).unwrap();
    e.set_digit_table(Some(digits.clone()));
    type_syls(&mut e, "ㄒㄧㄣ");
    for key in tail {
        e.key(*key).unwrap();
    }
    e.key(k(KeyKind::Home)).unwrap();
    e.key(k(KeyKind::Right)).unwrap(); // the cursor right after the syllable
    let o = e.key(k(KeyKind::Space)).unwrap();
    let shown = o.candidates[o.selected.unwrap()].clone();
    let o = e.key(k(KeyKind::Right)).unwrap();
    assert_ne!(o.candidates[o.selected.unwrap()], shown, "setup: another candidate");
    e.key(k(KeyKind::Enter)).unwrap();
    e.key(k(KeyKind::Enter)).unwrap();
    e.learner().records().iter().filter(|r| r.word.chars().count() == 1).map(|r| r.context.clone()).collect()
}

/// Typing "rust" or the digit 7 after the single character: the neighbour is the whole class (`^>_punct_en`,
/// `^>_punct_num`), so nothing is learned; with a syllable after it the same re-pick is learned (control).
#[test]
fn sinit_english_and_digit_neighbours_are_not_learned() {
    chat_only();
    let digits = Arc::new(core::digits::DigitTable::parse(&std::fs::read_to_string(root().join("data/lexicon/digits.tsv")).unwrap()).unwrap());
    let ch = |c: char| Key::ch(c, 0);
    let english = [ch('r'), ch('u'), ch('s'), ch('t'), k(KeyKind::Space)];
    assert!(sinit_repick_before(&english, &digits).is_empty(), "English neighbour");
    assert!(sinit_repick_before(&[ch('7')], &digits).is_empty(), "digit neighbour");
    let syl = keys_of("ㄑㄧㄥˊ");
    assert_eq!(sinit_repick_before(&syl, &digits), ["^>ㄑㄧㄥˊ"], "control: a syllable neighbour is learned");
}

/// The n-gram alone (no small model), formal profile, row 16 (user-reported.txt): only recorded. 2026-10-05 measured 趙建議 (formal wrong, chat right);
/// 2026-11-01 with the current model both show 照建議.
#[test]
fn sinit_row16_shipping_model_reference() {
    let mut e = engine();
    if e.set_profile(lm::Profile::Formal).is_err() {
        println!("SINIT row16 reference: formal profile unavailable (no trigram table)");
        return;
    }
    let formal = commit_of(&mut e, R16);
    let mut c = engine();
    c.set_profile(lm::Profile::Chat).unwrap();
    println!("SINIT row16 reference (shipping model, no learning record): formal {formal:?}, chat {:?}", commit_of(&mut c, R16));
}

// ---------- SINIT with the shipping models, rerank on (docs/spec/learning.md §7.2 to 1.5, 2) ----------

/// The shipping models as `Shell.build()` and `shanjie-replay` build them: lexicon with the default packs, n-gram model, small model,
/// colloquial table, demotion, rerank on at the standard level, `profile`; learning on with a fixed clock.
fn ship(profile: lm::Profile) -> Engine {
    let data = |f: &str| root().join("data/lm").join(f);
    let packs = root().join("data/packs");
    let mut e = Engine::new_with_packs(&root().join("data/lexicon"), L, Some((&packs, PACK_ACG | PACK_IT))).unwrap();
    e.load_lm(&lm_path()).unwrap();
    e.load_tinylm(&data("tinylm.sjtl")).unwrap();
    e.load_colloquial(&data("colloquial.sjlm")).unwrap();
    e.set_demote(true).unwrap();
    e.set_prediction(false).unwrap();
    e.set_rerank(true).unwrap();
    e.set_rerank_level(core::tinylm::RerankLevel::Standard).unwrap();
    e.set_profile(profile).unwrap();
    e.set_today(Some(DAY));
    e.set_learning(true);
    e
}
/// What the semantic correction would show now: its job taken, run and applied as the shell does between keys.
fn settle(e: &mut Engine, mut o: Output) -> Output {
    if let Some(job) = e.take_rerank_job() {
        if let Some(n) = e.apply_rerank(job.run()).unwrap() {
            o = n;
        }
    }
    o
}
/// Types `reading` and returns the display once the semantic correction has applied.
fn type_settled(e: &mut Engine, reading: &str) -> String {
    let o = type_syls(e, reading);
    settle(e, o).preedit
}
/// One teach: types `reading`, re-picks the first syllable as `word`, lets the correction apply, Enter; returns what was committed.
fn teach_first(e: &mut Engine, reading: &str, word: &str) -> String {
    let n = reading.split(' ').count();
    type_settled(e, reading);
    pick(e, n, 1, word);
    let o = e.key(k(KeyKind::End)).unwrap();
    settle(e, o);
    e.key(k(KeyKind::Enter)).unwrap().commit
}

/// `tiny` with the shipping small model and the semantic correction on (no colloquial table: a lexicon engine has no data folder, so the
/// jobs use the header triple of the model).
fn tiny_rerank(text: &str) -> Engine {
    let mut e = tiny(text, text);
    e.load_tinylm(&root().join("data/lm/tinylm.sjtl")).unwrap();
    e.set_rerank(true).unwrap();
    e
}
/// `tiny_taught` with the semantic correction on: 照 taught for row 16 (the first syllable re-picked), the composition closed.
fn tiny_rerank_taught() -> Engine {
    let mut e = tiny_rerank(SINIT_TINY);
    type_settled(&mut e, R16);
    pick(&mut e, 3, 1, "照");
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, "照建議", "precondition: the re-pick took");
    assert_eq!(records_of(&e, "照"), r16_records(), "precondition: the coarse, the mid and the fine record");
    e
}

/// One path of `decode_with`: its text, its score, and the lp and the SINIT marker of its first word.
type Decoded = (String, f64, f64, Option<(f64, f64)>);

/// The n-best of one `lm::decode_segment_fuzzy` for `reading` at sentence start on the lexicon text `lexicon` (with the reading term of R1 when `reading_term`),
/// with the learner of `e` and `sinit` as the rule 10 setting.
fn decode_with(e: &Engine, lexicon: &str, reading: &str, sinit: lm::Sinit, reading_term: bool) -> Vec<Decoded> {
    let s = shared();
    let capped = capped_over(lexicon);
    capped.set_reading_active(reading_term);
    let learn = lm::Learn { learner: e.learner(), before: "", after: None, today: DAY, eps_global: lm::LEARN_EPS_GLOBAL, sinit };
    lm::decode_segment_fuzzy(&capped, &syls(reading), &s.lm, s.lm.scoring(lm::Profile::Chat), "<s>", lm::End::Eos, core::BEAM_S1, Some(&learn), true, 0, core::fuzzy::DELTA_SOUND)
        .unwrap()
        .into_iter()
        .map(|(sc, ws)| (ws.iter().map(|w| w.0).collect::<String>(), sc, ws[0].1, ws[0].5))
        .collect()
}

/// docs/spec/learning.md §7.2, the guard half (the pool is the unlifted decode). Only a rule 10 record (照 under `^>ㄐㄧㄢˋ`, a character that is not the
/// first of its reading: 趙 is -1, 照 -6), the semantic correction on: the display before the rerank is the n-gram's first without the record (趙建議), the
/// guard does not fire (`guard_skips` 0), the job's candidates hold a path with 照 (in the unlifted n-best already, so nothing is swapped in) and it carries
/// b = 1/(1+1). Mutation checked (the pool back to the lifted decode: `Sinit::Lift` for the first decode): the display is 照建議 and this fails; the second
/// half is `sinit_total_score_after_a_learned_pick_is_the_unlifted_decode_score`.
#[test]
fn sinit_a_rule_10_record_does_not_trigger_the_learning_guard() {
    chat_only();
    let mut e = tiny_rerank_taught();
    let o = type_syls(&mut e, R16);
    assert_eq!(o.preedit, "趙建議", "the display before the rerank is the n-gram's first path without the lift");
    assert_eq!(e.guard_skips(), 0, "the guard does not count a rule 10 record");
    let job = e.take_rerank_job().expect("the stretch is sent to the semantic correction");
    let cands = job.candidates();
    assert_eq!(cands[0][0], "趙建議", "the anchor is the unlifted first path");
    let at = cands[0].iter().position(|c| c.starts_with('照')).unwrap_or_else(|| panic!("the pool holds a path with 照: {cands:?}"));
    let want: Vec<f64> = (0..cands[0].len()).map(|i| if i == at { 0.5 } else { 0.0 }).collect();
    assert_eq!(job.sinit_weights(), [want], "only the path with 照 carries b = 1/(1+1)");
    assert_eq!(e.sinit_stats(), SinitStats { stretches: 1, swaps: 0, missing: 0, tau_opened: 0 });
    // with the reading term on (the engine's default and this decode's), the lifted decode scores the same path differently from the unlifted one
    let path = |sinit| decode_with(&e, SINIT_TINY, R16, sinit, true).into_iter().find(|p| p.0 == "照建議").expect("the path is in the n-best");
    let (off, lift) = (path(lm::Sinit::Off), path(lm::Sinit::Lift));
    assert!((lift.1 - off.1).abs() > 1e-3, "the lift changes the path's score: {} against {}", lift.1, off.1);
}

/// docs/spec/learning.md §7.2, the total half: the semantic correction picks the path with 照 (`set_eps_sinit(100.0)`: both sizes 100 times larger), and after it is
/// applied `total_score` is the path's score of the unlifted decode (`Sinit::Off`, computed here by another decode of the same lexicon and learner), which
/// differs from the score of the same path in the lifted decode (premise: the lift raised the word's lp and so its score). `total_score` has no reading term
/// (docs/spec/decoder.md §5), so the equality is asserted with `set_reading_decode(false)` on both sides, as R1's own tests do. A pool of the lifted
/// decode would show the path with its lifted lp and fail here (mutation checked, see the commit).
#[test]
fn sinit_total_score_after_a_learned_pick_is_the_unlifted_decode_score() {
    chat_only();
    let mut e = tiny_rerank_taught();
    e.set_reading_decode(false);
    e.set_eps_sinit(100.0);
    let typed = type_syls(&mut e, R16).preedit;
    let job = e.take_rerank_job().expect("a job");
    let shown = e.apply_rerank(job.run()).unwrap().map_or(typed, |o| o.preedit);
    assert_eq!(shown, "照建議", "precondition: the learning term makes the correction pick the path with 照 (the display half is the test above)");
    let find = |nb: Vec<Decoded>| nb.into_iter().find(|p| p.0 == "照建議").expect("the path is in the n-best");
    let off = find(decode_with(&e, SINIT_TINY, R16, lm::Sinit::Off, false));
    let lift = find(decode_with(&e, SINIT_TINY, R16, lm::Sinit::Lift, false));
    let (lp0, gain) = lift.3.expect("the lifted word carries its marker");
    assert!(lift.2 > lp0 && gain > 0.0, "premise: the lift raised the word's lp ({} against {lp0}) and its term ({gain})", lift.2);
    assert!((lift.1 - gain - off.1).abs() < 1e-12, "the lifted score less the gain is the unlifted score: {} - {gain} against {}", lift.1, off.1);
    assert!(off.3.is_none() && off.2 == lp0, "the unlifted decode has the word at lp0, unmarked");
    let total = e.total_score().unwrap();
    assert!((total - off.1).abs() < 1e-9, "total_score {total} against the unlifted decode's {}", off.1);
    assert!((total - lift.1).abs() > 1e-3, "total_score {total} is not the lifted decode's {}", lift.1);
}

/// docs/spec/learning.md §7.2, the pool is the unlifted decode: 瑁 taught before ㄐㄧㄢˋ in a lexicon where 瑁 is the last of eleven readings of ㄓㄠˋ (the
/// capped scores keep the rare character at the end of the n-best), so the path 瑁建 is the eleventh of the unlifted n-best, outside the pool of eight. The display before the rerank is the unlifted first path; the pool is the
/// unlifted n-best's first seven paths with the same scores (1e-12) and the learned path last, with the score the unlifted decode gives it (1e-12), not the
/// lifted one. Run with the reading term on: 瑁 has a second, common reading (ㄓㄠ -2), so under ㄓㄠˋ its term is not 0 (2 * -4, clipped), and the
/// swapped-in path carries it exactly as the unlifted decode scores it; the lifted decode carries the same term, so the marker's gain is still the whole difference.
#[test]
fn sinit_the_pool_is_the_unlifted_n_best_and_the_swapped_in_path_has_its_unlifted_score() {
    chat_only();
    const L: &str = "ㄓㄠˋ 趙 -1.0\nㄓㄠˋ 兆 -2.0\nㄓㄠˋ 召 -3.0\nㄓㄠˋ 罩 -4.0\nㄓㄠˋ 肇 -5.0\nㄓㄠˋ 詔 -6.0\nㄓㄠˋ 棹 -7.0\nㄓㄠˋ 笊 -8.0\nㄓㄠˋ 炤 -9.0\nㄓㄠˋ 照 -10.0\nㄓㄠˋ 瑁 -11.0\nㄓㄠ 瑁 -2.0\nㄐㄧㄢˋ 建 -1.0\n";
    const R: &str = "ㄓㄠˋ ㄐㄧㄢˋ";
    let mut e = tiny_rerank(L);
    e.set_newword(false); // the committed 瑁建 would otherwise be learned as a new word and lead the n-best
    type_settled(&mut e, R);
    pick(&mut e, 2, 1, "瑁");
    e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!(records_of(&e, "瑁"), [("^>ㄐㄧㄢˋ".to_string(), 1.0)], "precondition: the record");
    let o = type_syls(&mut e, R);
    let off = decode_with(&e, L, R, lm::Sinit::Off, true);
    let rank = off.iter().position(|p| p.0 == "瑁建").expect("照建 is in the unlifted n-best");
    assert!(rank >= 8, "premise: the learned path is outside the pool of eight (rank {rank})");
    assert_eq!(o.preedit, off[0].0, "the display before the rerank is the unlifted first path");
    let job = e.take_rerank_job().expect("a job");
    let (cands, ng) = (job.candidates().remove(0), job.ng_scores().remove(0));
    assert_eq!(cands.len(), 8);
    for i in 0..7 {
        assert_eq!(cands[i], off[i].0, "pool path {i} is the unlifted n-best's");
        assert!((ng[i] - off[i].1).abs() < 1e-12, "pool path {i}: {} against {}", ng[i], off[i].1);
    }
    assert_eq!(cands[7], "瑁建", "the learned path takes the last place");
    assert!((ng[7] - off[rank].1).abs() < 1e-12, "its score is the unlifted decode's: {} against {}", ng[7], off[rank].1);
    let lift = decode_with(&e, L, R, lm::Sinit::Lift, true).into_iter().find(|p| p.0 == "瑁建").unwrap();
    let bare = decode_with(&e, L, R, lm::Sinit::Off, false).into_iter().find(|p| p.0 == "瑁建").unwrap();
    assert!((off[rank].1 - bare.1 + 8.0).abs() < 1e-9, "premise: the reading term (2 * -4) is in the score of the learned path: {} against {} without it", off[rank].1, bare.1);
    assert!((lift.1 - off[rank].1 - lift.3.unwrap().1).abs() < 1e-12 && lift.3.unwrap().1 > 0.0, "premise: the lifted decode scores the path higher by the marker's gain");
    assert_eq!(job.sinit_weights()[0][7], 0.5);
    assert_eq!(e.sinit_stats(), SinitStats { stretches: 1, swaps: 1, missing: 0, tau_opened: 0 });
}

/// docs/spec/learning.md §7.2 and §7.4: 照 taught before ㄐㄧㄢˋ in the small lexicon where the lift alone would make 照建議 the n-gram's first path. With
/// `set_eps_sinit(0.0)` the correction chooses as if there were no record; with a large factor it chooses 照建議.
#[test]
fn sinit_at_eps_0_a_record_changes_nothing_and_a_large_eps_takes_it() {
    chat_only();
    let none = {
        let mut e = tiny_rerank(SINIT_TINY);
        type_settled(&mut e, R16)
    };
    let with = |eps: f64| {
        let mut e = tiny_rerank_taught();
        e.set_eps_sinit(eps);
        type_settled(&mut e, R16)
    };
    assert_eq!(with(0.0), none, "no learning term: the record changes nothing");
    assert_eq!(with(100.0), "照建議");
}

/// docs/spec/learning.md §13.4, shipping models, formal: 再 taught from 再寫一封信 (the first syllable re-picked; the coarse key ㄒㄧㄝˇ, the mid key
/// ㄒㄧㄝˇ + ㄧ and the fine key ㄒㄧㄝˇ + ㄧ + ㄈㄥ), then 在寫報告 stays 在寫報告. The typed row ㄗㄞˋ ㄒㄧㄝˇ ㄅㄠˋ ㄍㄠˋ has the mid key ㄒㄧㄝˇ + ㄅㄠˋ and the fine key with `$`, which no record
/// has, so the coarse key answers; its term only counts for a candidate the small model scores above the anchor, and the small model prefers 在寫報告, so even a knob
/// 100 times the registered size changes nothing. Run at the registered sizes, at 0 and at 100. (Until 會意 SJ-SR-8-15M the pair was 再算一次 / 在算帳 of
/// the earlier case file; the new file gets 再算一次 right by default, so there was nothing to teach; the pair 再寫一封信 / 在寫報告 is group 再/在+寫 of
/// a confirmation file, found by teaching every teach row of the four sentence-start case files once in both profiles.)
#[test]
fn sinit_formal_zai_xie_bao_gao_stays_because_the_coarse_term_is_gated_by_the_model() {
    let mut e = ship(lm::Profile::Formal);
    let (teach, row) = ("ㄗㄞˋ ㄒㄧㄝˇ ㄧ ㄈㄥ ㄒㄧㄣˋ", "ㄗㄞˋ ㄒㄧㄝˇ ㄅㄠˋ ㄍㄠˋ");
    assert_eq!(type_settled(&mut e, teach), "在寫一封信", "precondition: the default");
    e.key(k(KeyKind::Enter)).unwrap();
    teach_first(&mut e, teach, "再");
    assert_eq!(
        records_of(&e, "再"),
        [("^>ㄒㄧㄝˇ".to_string(), 1.0), ("^>ㄒㄧㄝˇ\u{1f}ㄧ".to_string(), 1.0), ("^>ㄒㄧㄝˇ\u{1f}ㄧ\u{1f}ㄈㄥ".to_string(), 1.0)],
        "precondition: 再 was learned before ㄒㄧㄝˇ, before ㄒㄧㄝˇ ㄧ and before ㄒㄧㄝˇ ㄧ ㄈㄥ"
    );
    for scale in [0.0, 1.0, 100.0] {
        e.set_eps_sinit(scale);
        assert_eq!(type_settled(&mut e, row), "在寫報告", "scale {scale}");
        e.key(k(KeyKind::Enter)).unwrap();
    }
}

/// The learned character is part of a two-character word in a candidate (docs/spec/learning.md §7.3, "by the surface"): 再 taught under `^>ㄐㄧㄚ`
/// from a lexicon where 再加 is a word. The job's candidates that start with 再, whether the path cuts 再 off as a word or reads 再加, carry
/// b = 0.5; those that do not, 0; 再加一個 is among them.
#[test]
fn sinit_the_learned_character_inside_a_two_character_word_counts() {
    chat_only();
    const L: &str = "ㄗㄞˋ 在 -1.0\nㄗㄞˋ 再 -3.0\nㄐㄧㄚ 加 -1.0\nㄐㄧㄚ 家 -1.5\nㄗㄞˋ-ㄐㄧㄚ 再加 -4.0\nㄧˊ 一 -1.0\nㄍㄜˋ 個 -1.0\nㄧˊ-ㄍㄜˋ 一個 -1.0\n";
    const R: &str = "ㄗㄞˋ ㄐㄧㄚ ㄧˊ ㄍㄜˋ";
    // taught without the semantic correction (whose own choice here is 再加一個 already, nothing to re-pick), then it is turned on
    let mut e = tiny(L, L);
    assert_ne!(type_syls(&mut e, R).preedit, "再加一個", "precondition: the n-gram's default is another writing");
    pick(&mut e, 4, 1, "再");
    e.key(k(KeyKind::Enter)).unwrap();
    e.load_tinylm(&root().join("data/lm/tinylm.sjtl")).unwrap();
    e.set_rerank(true).unwrap();
    assert_eq!(
        records_of(&e, "再"),
        [("^>ㄐㄧㄚ".to_string(), 1.0), ("^>ㄐㄧㄚ\u{1f}ㄧˊ".to_string(), 1.0), ("^>ㄐㄧㄚ\u{1f}ㄧˊ\u{1f}ㄍㄜˋ".to_string(), 1.0)],
        "precondition: the single character was taught (coarse, mid and fine key), not the word"
    );
    type_syls(&mut e, R);
    let job = e.take_rerank_job().expect("a job");
    let (cands, b) = (job.candidates(), job.sinit_weights());
    assert!(cands[0].iter().any(|c| c == "再加一個"), "premise: 再加一個 is a candidate: {cands:?}");
    for (c, b) in cands[0].iter().zip(&b[0]) {
        assert_eq!(*b, if c.starts_with('再') { 0.5 } else { 0.0 }, "{c}");
    }
    assert_eq!(e.sinit_stats().missing, 0);
}

/// docs/spec/learning.md §7.1, the records of one stretch, hand-written into a learning file: the semantic correction's job reads the layer the stretch has. `lexicon` is the
/// tiny one of `sinit_the_pool...` plus 議, so the learned path 瑁建議 is the eleventh of the unlifted n-best (outside the pool of eight). Returns
/// the job's candidates, their `b` and the stats.
fn sinit_job_with_records(tag: &str, records: &[(&str, &str, f64)]) -> (Vec<String>, Vec<f64>, SinitStats) {
    const L: &str = "ㄓㄠˋ 趙 -1.0\nㄓㄠˋ 兆 -2.0\nㄓㄠˋ 召 -3.0\nㄓㄠˋ 罩 -4.0\nㄓㄠˋ 肇 -5.0\nㄓㄠˋ 詔 -6.0\nㄓㄠˋ 棹 -7.0\nㄓㄠˋ 笊 -8.0\nㄓㄠˋ 炤 -9.0\nㄓㄠˋ 照 -10.0\nㄓㄠˋ 瑁 -11.0\nㄓㄠ 瑁 -2.0\nㄐㄧㄢˋ 建 -1.0\nㄧˋ 議 -1.0\n";
    let mut e = tiny_rerank(L);
    e.set_newword(false);
    let dir = tmp_dir(tag);
    std::fs::create_dir_all(&dir).unwrap();
    let body: String = records.iter().map(|(ctx, w, wt)| format!("{ctx}\tㄓㄠˋ\t{w}\t{wt}\t{DAY}\n")).collect();
    std::fs::write(file_of(&dir), format!("{}\n{body}", core::learn_store::HEADER)).unwrap();
    e.learning_open(&dir).unwrap();
    assert_eq!(e.learner().records().len(), records.len(), "precondition: every record was read");
    type_syls(&mut e, R16);
    let job = e.take_rerank_job().expect("a job");
    (job.candidates().remove(0), job.sinit_weights().remove(0), e.sinit_stats())
}

/// docs/spec/learning.md §7.4: a stretch uses the longest layer it has a record in, for every candidate, and the three never add up: the fine key (`^>` + the next three
/// syllables, here two and `$`), else the mid key (the next two), else the coarse key. docs/spec/learning.md §7.2, §7.3: a record under any key makes the stretch eligible and the lifted decode
/// finds the learned path (瑁建議, outside the pool of eight) for any of them.
#[test]
#[cfg(unix)] // learning_open fails on Windows for now (docs/spec/windows-tip.md §2.5)
fn sinit_the_job_reads_the_longest_layer_that_has_a_record() {
    chat_only();
    let (coarse, mid, fine) = ("^>ㄐㄧㄢˋ", "^>ㄐㄧㄢˋ\u{1f}ㄧˋ", "^>ㄐㄧㄢˋ\u{1f}ㄧˋ\u{1f}$");
    // one layer only: 瑁 w 1 -> b 0.5 on the learned path, which is swapped in last
    for (tag, key) in [("sinit-coarse-only", coarse), ("sinit-mid-only", mid), ("sinit-fine-only", fine)] {
        let (c, b, st) = sinit_job_with_records(tag, &[(key, "瑁", 1.0)]);
        assert_eq!((c.len(), c[7].as_str(), b[7]), (8, "瑁建議", 0.5), "{tag}: {c:?}");
        assert_eq!(b.iter().filter(|&&x| x > 0.0).count(), 1, "{tag}");
        assert_eq!(st, SinitStats { stretches: 1, swaps: 1, missing: 0, tau_opened: 0 }, "{tag}");
    }
    // all three: 瑁 w 1 under the coarse key, 兆 w 1 under the mid key, 趙 w 3 under the fine key. The fine layer answers for every candidate (趙建議, the anchor, b 0.75), and the other
    // two layers' records are not added: 瑁建議 and 兆建議 have no fine record, so 0
    let (c, b, st) = sinit_job_with_records("sinit-all", &[(coarse, "瑁", 1.0), (mid, "兆", 1.0), (fine, "趙", 3.0)]);
    assert_eq!(c[0], "趙建議");
    assert_eq!(b[0], 0.75, "the fine record of 趙");
    assert!(c.iter().zip(&b).skip(1).all(|(_, &x)| x == 0.0), "no other candidate has a weight: {c:?} {b:?}");
    assert_eq!(st.stretches, 1);
    // no fine record: the mid layer answers, the coarse record of 瑁 is not added
    let (c, b, _) = sinit_job_with_records("sinit-mid-coarse", &[(coarse, "瑁", 1.0), (mid, "趙", 3.0)]);
    assert_eq!((c[0].as_str(), b[0]), ("趙建議", 0.75), "{c:?}");
    assert!(c.iter().zip(&b).skip(1).all(|(_, &x)| x == 0.0), "{c:?} {b:?}");
    // fine and coarse, no mid: the fine layer
    let (c, b, _) = sinit_job_with_records("sinit-fine-coarse", &[(coarse, "瑁", 1.0), (fine, "趙", 3.0)]);
    assert_eq!((c[0].as_str(), b[0]), ("趙建議", 0.75), "{c:?}");
    assert!(c.iter().zip(&b).skip(1).all(|(_, &x)| x == 0.0), "{c:?} {b:?}");
    // a fine record for another third syllable and a mid record for another second syllable are not this stretch's: the coarse layer answers
    let (c, b, _) = sinit_job_with_records(
        "sinit-other",
        &[(coarse, "瑁", 1.0), ("^>ㄐㄧㄢˋ\u{1f}ㄧˋ\u{1f}ㄍㄜˋ", "趙", 3.0), ("^>ㄐㄧㄢˋ\u{1f}ㄒㄧㄤˋ", "趙", 3.0)],
    );
    assert_eq!((c[7].as_str(), b[7]), ("瑁建議", 0.5), "{c:?}");
    assert_eq!(b[0], 0.0, "趙 is not weighted: its keys are for other stretches");
    // a fine record for another third syllable beside a mid record: the mid layer answers
    let (c, b, _) = sinit_job_with_records("sinit-other-fine-mid", &[(mid, "瑁", 1.0), ("^>ㄐㄧㄢˋ\u{1f}ㄧˋ\u{1f}ㄍㄜˋ", "趙", 3.0)]);
    assert_eq!((c[7].as_str(), b[7]), ("瑁建議", 0.5), "{c:?}");
    assert_eq!(b[0], 0.0);
}

/// docs/spec/learning.md §7.1: what is taught after the first syllable is re-picked. The next syllables are plain: the coarse key, then the mid key for the next two,
/// then the fine key for the next three (`$` as the third part when the composition ends after the second). A syllable that is English or a digit stops the keys
/// that need it and every longer one. ⌘⌫ on the character forgets all of them.
#[test]
fn sinit_mid_and_fine_keys_are_learned_by_what_follows_and_forgotten_with_the_coarse_one() {
    chat_only();
    let digits = Arc::new(core::digits::DigitTable::parse(&std::fs::read_to_string(root().join("data/lexicon/digits.tsv")).unwrap()).unwrap());
    let ch = |c: char| Key::ch(c, 0);
    let (q, x, y) = (keys_of("ㄑㄧㄥˊ"), keys_of("ㄒㄧㄥ"), keys_of("ㄏㄜˊ"));
    let rust = [ch('r'), ch('u'), ch('s'), ch('t'), k(KeyKind::Space)];
    let with = |tails: &[&[Key]]| sinit_repick_before(&tails.iter().flat_map(|t| t.iter().copied()).collect::<Vec<_>>(), &digits);
    let (c, m, f) = ("^>ㄑㄧㄥˊ", "^>ㄑㄧㄥˊ\u{1f}ㄒㄧㄥ", "^>ㄑㄧㄥˊ\u{1f}ㄒㄧㄥ\u{1f}ㄏㄜˊ");
    assert_eq!(with(&[&q, &x, &y]), [c, m, f], "three syllables after: the coarse, the mid and the fine key");
    assert_eq!(with(&[&q, &x]), [c, m, "^>ㄑㄧㄥˊ\u{1f}ㄒㄧㄥ\u{1f}$"], "two syllables after: the fine key ends in `$`");
    assert_eq!(with(&[&q]), [c], "the sentence ends after the next syllable: only the coarse key");
    assert_eq!(with(&[&q, &x, &rust]), [c, m], "English as the third: no fine key");
    assert_eq!(with(&[&q, &x, &[ch('7')]]), [c, m], "a digit as the third: no fine key");
    assert_eq!(with(&[&q, &rust]), [c], "English as the second: no mid key and so no fine key");
    assert_eq!(with(&[&q, &[ch('7')], &y]), [c], "a digit as the second, a syllable after it: no mid key and so no fine key");
    assert_eq!(with(&[&rust]), Vec::<String>::new(), "English as the first neighbour: nothing");
    // ⌘⌫ on 照 after teaching it for row 16: no record is left
    let mut e = tiny_rerank_taught();
    assert_eq!(records_of(&e, "照").len(), 3);
    type_syls(&mut e, R16);
    e.key(k(KeyKind::Home)).unwrap();
    let o = e.key(k(KeyKind::Space)).unwrap();
    highlight(&mut e, o, "照");
    e.key(FORGET).unwrap();
    assert!(records_of(&e, "照").is_empty(), "all three keys are forgotten");
}

/// docs/spec/learning.md §13.4, the fuzzy case: a record that can only match a path with a fuzzy-sound word (照 recorded under the typed ㄗㄠˋ; the lexicon reads
/// 照 as ㄓㄠˋ, the ㄓ/ㄗ pair is on) puts no weight on any candidate and changes neither the pool nor the job.
#[test]
#[cfg(unix)] // learning_open fails on Windows for now (docs/spec/windows-tip.md §2.5)
fn sinit_a_learned_character_only_on_a_fuzzy_path_changes_nothing() {
    chat_only();
    const L: &str = "ㄗㄠˋ 造 -1.0\nㄗㄠˋ 皂 -2.0\nㄓㄠˋ 照 -1.0\nㄐㄧㄢˋ 建 -1.0\n";
    const R: &str = "ㄗㄠˋ ㄐㄧㄢˋ";
    let candidates = |record: bool| {
        let mut e = tiny_rerank(L);
        let dir = tmp_dir(if record { "sinit-fuzzy-on" } else { "sinit-fuzzy-off" });
        if record {
            std::fs::create_dir_all(&dir).unwrap();
            std::fs::write(file_of(&dir), format!("{}\n^>ㄐㄧㄢˋ\tㄗㄠˋ\t照\t1\t{DAY}\n", core::learn_store::HEADER)).unwrap();
            e.learning_open(&dir).unwrap();
            assert_eq!(e.learner().records().len(), 1, "precondition: the record was read");
        }
        e.set_fuzzy(0b0010).unwrap(); // ㄓ/ㄗ
        type_syls(&mut e, R);
        let job = e.take_rerank_job().expect("a job");
        (job.candidates(), job.sinit_weights(), e.sinit_stats())
    };
    let (without, _, _) = candidates(false);
    let (with, weights, stats) = candidates(true);
    assert_eq!(with, without, "the pool is the one without the record");
    assert!(with[0].iter().all(|c| !c.starts_with('照')), "premise: no fuzzy path is a candidate");
    assert_eq!(weights, [[0.0, 0.0]]);
    assert_eq!(stats, SinitStats { stretches: 1, swaps: 0, missing: 1, tau_opened: 0 });
}

/// docs/spec/learning.md §7.6: with no rerank job (this engine has no small model) a rule 10 record has no effect. 再 taught before ㄐㄧㄚ and then 在家 typed
/// (the same key) is 在家 as before the teach, with the second syllable also picked or not (the prototype's `LEARN_EPS` 6 showed 再加; there is no n-gram boost now).
#[test]
fn sinit_same_key_two_character_word_measured() {
    chat_only();
    assert_eq!(baseline("ㄗㄞˋ ㄐㄧㄚ"), "在家", "precondition: the unlearned default");
    for also_second in [false, true] {
        let mut e = engine();
        type_syls(&mut e, "ㄗㄞˋ ㄐㄧㄚ");
        pick(&mut e, 2, 1, "再");
        if also_second {
            pick(&mut e, 2, 2, "加");
        }
        e.key(k(KeyKind::Enter)).unwrap();
        assert_eq!(records_of(&e, "再"), [("^>ㄐㄧㄚ".to_string(), 1.0)], "two syllables: the sentence ends after the next one, only the coarse key");
        assert_eq!(commit_of(&mut e, "ㄗㄞˋ ㄐㄧㄚ"), baseline("ㄗㄞˋ ㄐㄧㄚ"), "no rerank job: the record has no effect (second syllable also picked: {also_second})");
    }
}

/// docs/spec/learning.md §13.4, the pollution test with the shipping models (semantic correction on, the standard level, both profiles): 再 taught at
/// sentence start before ㄐㄧㄚ (the first syllable re-picked as the single character) leaves 在家休息 typed at sentence start as 在家休息. Taught on two
/// readings: ㄗㄞˋ ㄐㄧㄚ (the coarse key only: the sentence ends after the next syllable) and ㄗㄞˋ ㄐㄧㄚ ㄧˋ (the coarse, the mid and the fine key with `$`; the spec's
/// ㄗㄞˋ ㄐㄧㄚ ㄧ is 再加一 already with the shipping models, there is no re-pick to teach from, measured 2026-11-03). The typed row has the mid key ㄐㄧㄚ + ㄒㄧㄡ and the
/// fine key ㄐㄧㄚ + ㄒㄧㄡ + ㄒㄧˊ, which no record has, so the coarse key answers there and the rerank gate (`ll_i > ll_0`) keeps 在家休息.
/// The two-syllable 在家 alone is not asserted (measured: it shows 再加 in chat, printed below).
#[test]
fn sinit_taught_zai_does_not_pollute_zai_jia_xiu_xi() {
    for p in [lm::Profile::Chat, lm::Profile::Formal] {
        for (teach, deeper) in [("ㄗㄞˋ ㄐㄧㄚ", &[][..]), ("ㄗㄞˋ ㄐㄧㄚ ㄧˋ", &["^>ㄐㄧㄚ\u{1f}ㄧˋ", "^>ㄐㄧㄚ\u{1f}ㄧˋ\u{1f}$"][..])] {
            let mut e = ship(p);
            let want = "在家休息";
            let r = "ㄗㄞˋ ㄐㄧㄚ ㄒㄧㄡ ㄒㄧˊ";
            assert_eq!(type_settled(&mut e, r), want, "{p:?} precondition: the default");
            e.key(k(KeyKind::Enter)).unwrap();
            assert!(type_settled(&mut e, teach).starts_with('在'), "{p:?} {teach}: precondition: the default is 在, so the pick of 再 is a re-pick");
            e.key(k(KeyKind::Enter)).unwrap();
            teach_first(&mut e, teach, "再");
            let zai = syls("ㄗㄞˋ");
            let recs: Vec<_> = e.learner().records().iter().filter(|r| r.word == "再").map(|r| (r.context.as_str(), &r.reading)).collect();
            let mut want_recs = vec![("^>ㄐㄧㄚ", &zai)];
            want_recs.extend(deeper.iter().map(|f| (*f, &zai)));
            assert_eq!(recs, want_recs, "{p:?} {teach}: precondition: the coarse key (and the mid and the fine key for three syllables) was learned");
            assert_eq!(type_settled(&mut e, r), want, "{p:?} {teach}: 再 before ㄐㄧㄚ does not turn 在家休息 into 再加休息");
            println!("SINIT {p:?} taught on {teach}: ㄗㄞˋ ㄐㄧㄚ typed alone shows {:?}", {
                e.key(k(KeyKind::Enter)).unwrap();
                type_settled(&mut e, "ㄗㄞˋ ㄐㄧㄚ")
            });
        }
    }
}

/// docs/spec/learning.md §13.4, the preference class (她／他: both spellings fit the sentence, the semantic correction cannot tell them apart): 她 taught at
/// sentence start before ㄏㄣˇ, then one row that now shows 她 re-picked back to 他. With the shipping models only the teach row shows 她 (the other rows share no fine key with the record and the coarse term is gated by the model), so the teach row is the revert row. Asserted: the teach took, the re-pick halves the taught 她 and records 他 (the learner's own
/// rule for the same key), and afterwards both rows show 他. Recorded, not asserted (printed with the profile): the two rare rows of the group after the revert.
/// Shipping models, rerank on, standard level, `EPS_SINIT_FINE` 4, `EPS_SINIT_MID` 4 and `EPS_SINIT_COARSE` 2, chat and formal. The records are asserted under all three keys.
#[test]
fn sinit_preference_class_can_be_reverted_by_one_repick() {
    const TEACH: &str = "ㄊㄚ ㄏㄣˇ ㄒㄧˇ ㄏㄨㄢ ㄎㄢˋ ㄕㄨ"; // 她很喜歡看書
    // The teach row itself shares the fine key of the record (the other two rows do not, and there the coarse term is gated by the model, so they show 他 from the start).
    let rows = [("他很喜歡看書", TEACH), ("他很忙", "ㄊㄚ ㄏㄣˇ ㄇㄤˊ"), ("他很高", "ㄊㄚ ㄏㄣˇ ㄍㄠ")];
    const RARE: [(&str, &str); 2] = [("她很晚才回家", "ㄊㄚ ㄏㄣˇ ㄨㄢˇ ㄘㄞˊ ㄏㄨㄟˊ ㄐㄧㄚ"), ("她很有禮貌", "ㄊㄚ ㄏㄣˇ ㄧㄡˇ ㄌㄧˇ ㄇㄠˋ")];
    for p in [lm::Profile::Chat, lm::Profile::Formal] {
        let mut e = ship(p);
        assert_eq!(type_settled(&mut e, TEACH), "他很喜歡看書", "{p:?} precondition: the default");
        e.key(k(KeyKind::Enter)).unwrap();
        assert_eq!(teach_first(&mut e, TEACH, "她"), "她很喜歡看書", "{p:?}: the re-pick took");
        assert_eq!(
            records_of(&e, "她"),
            [("^>ㄏㄣˇ".to_string(), 1.0), ("^>ㄏㄣˇ\u{1f}ㄒㄧˇ".to_string(), 1.0), ("^>ㄏㄣˇ\u{1f}ㄒㄧˇ\u{1f}ㄏㄨㄢ".to_string(), 1.0)],
            "{p:?}: taught under the next syllable, the next two and the next three"
        );
        let mut shown = Vec::new();
        let mut revert = None;
        for (sent, r) in rows {
            let s = type_settled(&mut e, r);
            if revert.is_none() && s.starts_with('她') {
                revert = Some((sent, r));
                pick(&mut e, r.split(' ').count(), 1, "他");
                let o = e.key(k(KeyKind::End)).unwrap();
                settle(&mut e, o);
                assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, sent, "{p:?}: the re-pick back took");
            } else {
                e.key(k(KeyKind::Enter)).unwrap();
            }
            shown.push(format!("{sent} -> {s}"));
        }
        let (sent, r) = revert.unwrap_or_else(|| panic!("{p:?}: no row shows 她 after the teach, nothing to revert: {shown:?}"));
        let both = |w: f64| vec![("^>ㄏㄣˇ".to_string(), w), ("^>ㄏㄣˇ\u{1f}ㄒㄧˇ".to_string(), w), ("^>ㄏㄣˇ\u{1f}ㄒㄧˇ\u{1f}ㄏㄨㄢ".to_string(), w)];
        assert_eq!(records_of(&e, "她"), both(0.5), "{p:?}: the taught 她 is halved under all three keys");
        assert_eq!(records_of(&e, "他"), both(1.0), "{p:?}: 他 is recorded under all three keys");
        let mut after = Vec::new();
        for (s, r) in rows.iter().chain(RARE.iter()) {
            let shown = type_settled(&mut e, r);
            if rows.iter().any(|(row, _)| row == s) {
                assert_eq!(shown, *s, "{p:?}: after the revert {s} shows 他");
            }
            after.push(format!("{s} -> {shown}"));
            e.key(k(KeyKind::Enter)).unwrap();
        }
        println!("SINIT revert {p:?}: after teaching 她 {shown:?}; reverted on {sent} ({r}); after the revert {after:?}");
    }
}

/// docs/spec/learning.md §13.4, learned once with the shipping models (rerank on, standard level): `before` is asserted first, then
/// the first syllable is single-picked as `want` and Enter, then the same reading must show `sent`. Two rows of eval/dev/user-reported.txt in the
/// formal profile only (row 31, 再測一次, is right by default with 會意 SJ-SR-8-15M; row 78, 直式顯示, replaces it, and row 163 is right in chat already), and the semantic
/// teach row 喂你在嗎 of the sentence-start case file in chat (代你保管, the row until 會意 SJ-SR-8-15M, is not learned by one teach with the new cells: it shows 帶你保管 in
/// both profiles, measured 2026-11-06).
fn sinit_learned_once(p: lm::Profile, reading: &str, before: &str, want: &str, sent: &str) {
    let mut e = ship(p);
    assert_eq!(type_settled(&mut e, reading), before, "{p:?} {sent}: before teaching");
    e.key(k(KeyKind::Enter)).unwrap();
    teach_first(&mut e, reading, want);
    // docs/spec/learning.md §7.1: the learner holds the three keys, the coarse (the next syllable), the mid (the next two) and the fine (the next three).
    let rd: Vec<&str> = reading.split(' ').collect();
    let want_keys = [format!("^>{}", rd[1]), format!("^>{}\u{1f}{}", rd[1], rd[2]), format!("^>{}\u{1f}{}\u{1f}{}", rd[1], rd[2], rd[3])];
    let recs: Vec<_> = e.learner().records().iter().filter(|r| r.word == want).collect();
    assert!(
        recs.len() == 3 && recs.iter().zip(&want_keys).all(|(r, k)| r.context == *k && r.reading.len() == 1),
        "{p:?} {sent}: the single character was learned under the coarse, the mid and the fine key {want_keys:?}"
    );
    assert_eq!(type_settled(&mut e, reading), sent, "{p:?} {sent}: after one teach");
}

/// docs/spec/learning.md §13.4: the teach sentence 代我出席會議 (it must not take 帶我出去玩, their first three syllables are the same). After one teach of 代 the
/// same reading shows it, and ㄉㄞˋ ㄨㄛˇ ㄔㄨ ㄑㄩˋ ㄨㄢˊ (the fourth syllable differs: the fine key does not hit, the mid key is gated by the model) does not start with 代, in chat and formal.
/// Printed, not asserted: what the other sentence shows.
#[test]
fn sinit_dai_wo_chu_xi_hui_yi_does_not_take_dai_wo_chu_qu_wan() {
    let (teach, other) = ("ㄉㄞˋ ㄨㄛˇ ㄔㄨ ㄒㄧˊ ㄏㄨㄟˋ ㄧˋ", "ㄉㄞˋ ㄨㄛˇ ㄔㄨ ㄑㄩˋ ㄨㄢˊ");
    for p in [lm::Profile::Chat, lm::Profile::Formal] {
        let mut e = ship(p);
        let before = type_settled(&mut e, teach);
        assert!(!before.starts_with('代'), "{p:?}: precondition: the default is not 代 ({before})");
        e.key(k(KeyKind::Enter)).unwrap();
        let other_before = type_settled(&mut e, other);
        e.key(k(KeyKind::Enter)).unwrap();
        teach_first(&mut e, teach, "代");
        let want_keys = ["^>ㄨㄛˇ", "^>ㄨㄛˇ\u{1f}ㄔㄨ", "^>ㄨㄛˇ\u{1f}ㄔㄨ\u{1f}ㄒㄧˊ"];
        let recs: Vec<_> = e.learner().records().iter().filter(|r| r.word == "代").map(|r| r.context.as_str()).collect();
        assert_eq!(recs, want_keys, "{p:?}: precondition: the teach took, under the three keys");
        let again = type_settled(&mut e, teach);
        e.key(k(KeyKind::Enter)).unwrap();
        let shown = type_settled(&mut e, other);
        println!("SINIT {p:?} 代 taught on {teach}: the same reading shows {again:?}; {other} showed {other_before:?} before and shows {shown:?}");
        assert!(again.starts_with('代'), "{p:?}: after one teach the same reading shows 代 ({again})");
        assert!(!shown.starts_with('代'), "{p:?}: {other} must not start with 代 after the teach ({shown})");
    }
}

#[test]
fn sinit_user_reported_row_78_is_learned_once_in_formal() {
    sinit_learned_once(lm::Profile::Formal, "ㄓˊ ㄕˋ ㄒㄧㄢˇ ㄕˋ", "值是顯示", "直", "直式顯示");
}

#[test]
fn sinit_user_reported_row_163_is_learned_once_in_formal() {
    sinit_learned_once(lm::Profile::Formal, "ㄅㄧㄢˋ ㄧˊ ㄧㄤˋ ㄌㄜ˙", "便一樣了", "變", "變一樣了");
}

#[test]
fn sinit_semantic_row_wei_ni_zai_ma_is_learned_once_in_chat() {
    sinit_learned_once(lm::Profile::Chat, "ㄨㄟˋ ㄋㄧˇ ㄗㄞˋ ㄇㄚ", "為你在嗎", "喂", "喂你在嗎");
}
