//! S4 core tests (docs/contracts/s4-learning.md §6 items 1-9 and 14). Needs data/lm/bigram.sjlm like
//! engine_lm.rs. The `store_` tests write the learning file into a temporary directory.
use core::engine::*;
use core::eval::{parse_rows, usable};
use core::learn::{context_key, Learner, Level, Record, CAPACITY, GLOBAL, PRUNE_FLOOR, SENTINEL};
use core::lm::{self, CappedLexicon, Lm};
use core::{Lexicon, Syls};
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}
fn lm_path() -> PathBuf {
    let p = root().join("data/lm/bigram.sjlm");
    assert!(root().join("data/lm/classes.sjc").exists(), "data/lm/classes.sjc is missing: download it with `gh release download classes-v1 -R Nanako0129/shanjie -p classes.sjc -D data/lm` (or build it with tools/build_classes.py)");
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
        let overlay = std::fs::read_to_string(root().join("data/lexicon/overlay-add.tsv")).unwrap();
        let capped = Arc::new(CappedLexicon::new(lex.clone(), &overlay, &lm, None).unwrap());
        Shared { lex, lm: Arc::new(lm), capped }
    })
}
/// Production lexicon + model, learning on, fixed clock (day 20000).
fn engine() -> Engine {
    let s = shared();
    let mut e = Engine::with_lexicon(s.lex.clone(), Layout::Standard);
    e.set_lm(s.lm.clone(), s.capped.clone());
    e.set_today(Some(DAY));
    e.set_learning(true);
    e
}
const DAY: i64 = 20_000;
const L: Layout = Layout::Standard;

/// Tiny lexicon `engine_text` for the engine, `capped_text` for the decoder (they differ only to force a
/// decode failure); the real bigram model.
fn tiny(engine_text: &str, capped_text: &str) -> Engine {
    let s = shared();
    let lex = Arc::new(Lexicon::parse(engine_text).unwrap());
    let cl = Arc::new(Lexicon::parse(capped_text).unwrap());
    let capped = Arc::new(CappedLexicon::new(cl, "", &s.lm, None).unwrap());
    let mut e = Engine::with_lexicon(lex, L);
    e.set_lm(s.lm.clone(), capped);
    e.set_today(Some(DAY));
    e.set_learning(true);
    e
}
/// Single characters learn only after a Han character (§12 rule 6), so the one-syllable teaches in these
/// tests take this left context.
const TAUGHT_AFTER: &str = "他";
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
/// Would a record taught under `taught` answer a decode-time query under `query` (§1.1 order)? A
/// single-character word answers only on an equal full key other than "^" (§12 rules 5 and 6).
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
    assert_eq!(ceiling, 13, "contract §1.1: 13 of 14");
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
    let mut e = Engine::new(&root().join("data/lexicon"), L).unwrap();
    e.load_lm(&lm_path()).unwrap();
    e.set_today(Some(DAY));
    e.set_learning(true);
    println!("engine new + load_lm {:?}", t.elapsed());
    let gs = groups();
    // same-context rare rows wrong before: learned / total, all and only those with a reachable key (§1.1)
    let (mut learned, mut wrong, mut learned_reach, mut wrong_reach) = (0, 0, 0, 0);
    let (mut regress, mut moot, mut failed) = (Vec::new(), Vec::new(), Vec::new());
    println!("group | row kinds: exact/word before -> after | records written");
    for g in &gs {
        e.learning_clear().unwrap_err(); // no store: an error (§4), memory still dropped
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
                // nothing about passing a record to the same context. The mirror run covers it (contract §6.2).
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
    // Moot groups are not counted (s4-learning §13), so keep a floor on what is: 3 with model-v3 + classes.
    assert!(wrong_reach >= 3, "fewer than 3 countable same-context rows ({wrong_reach}): the rate would not mean anything");
    assert!(learned_reach * 100 >= wrong_reach * 80, "same-context learn rate under 80%");

    // Informational: all 14 groups taught on one learner, then every common row.
    e.learning_clear().unwrap_err(); // no store: an error (§4), memory still dropped
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
    let mut e = Engine::new(&root().join("data/lexicon"), L).unwrap();
    e.load_lm(&lm_path()).unwrap();
    e.set_today(Some(DAY));
    e.set_learning(true);
    e.set_eps_global(eps);
    let (mut learned, mut wrong, mut learned_reach, mut wrong_reach) = (0, 0, 0, 0);
    for g in &groups() {
        e.learning_clear().unwrap_err(); // no store: an error (§4), memory still dropped
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
    // (a) previous word is one character
    let mut e = tiny(TINY, TINY);
    type_syls(&mut e, "ㄊㄚ ㄒㄧㄣ");
    pick(&mut e, 2, 2, "欣");
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, "他欣");
    assert_eq!(keys(&e), ["他"]);
    assert_eq!(type_syls(&mut e, "ㄊㄚ ㄒㄧㄣ").preedit, "他欣");
    e.key(k(KeyKind::Esc)).unwrap();
    assert_eq!(type_syls(&mut e, "ㄒㄧㄣ").preedit, "鑫", "key ^ is not the key 他");
    e.key(k(KeyKind::Esc)).unwrap();

    // (b) after a fixed word (single characters learn only under a Han key, §12 rule 6, so 奔 takes 好)
    let mut e = tiny(TINY, TINY);
    e.set_left_context("好");
    type_syls(&mut e, "ㄅㄣ ㄒㄧㄣ");
    pick(&mut e, 2, 1, "奔");
    pick(&mut e, 2, 2, "欣");
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, "奔欣");
    assert_eq!(keys(&e), ["好", "好奔"], "奔 after 好, 欣 after 好 and the fixed word 奔");
    e.set_left_context("好");
    assert_eq!(type_syls(&mut e, "ㄅㄣ ㄒㄧㄣ").preedit, "奔欣");
    e.key(k(KeyKind::Esc)).unwrap();

    // (c) after punctuation the key is ^: a single character is not learned there (rule 6) ...
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
    assert_eq!(keys(&e), ["^"], "after the punctuation token the key is ^");
    type_syls(&mut e, "ㄊㄚ");
    e.key(Key::ch(',', MOD_SHIFT)).unwrap();
    assert_eq!(type_syls(&mut e, R2).preedit, format!("他，{w}"), "decode-time key after punctuation is the taught ^");
    e.key(k(KeyKind::Esc)).unwrap();
    assert_ne!(type_syls(&mut e, &format!("ㄊㄚ {R2}")).preedit, format!("他{w}"), "no punctuation: key 他, not ^");
    e.key(k(KeyKind::Esc)).unwrap();

    // (d) sentence start: a single character is not learned, a 2-character word is, under ^
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
    assert_eq!(keys(&e), ["^"]);
    assert_eq!(type_syls(&mut e, R2).preedit, w, "decode-time key at sentence start is ^");
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
/// The context keys of the learner's records, in insertion order.
fn keys(e: &Engine) -> Vec<String> {
    e.learner().records().iter().map(|r| r.context.clone()).collect()
}

// ---------- 4: sentinel, 1-char match, globalization ----------

#[test]
fn sentinel_one_char_and_global() {
    // §12: only words of 2+ characters globalize, so the global parts use a two-syllable word.
    let r = syls("ㄅㄚˇ ㄅㄚˇ");
    let mut l = Learner::default();
    l.teach(SENTINEL, &r, "把手", "爸爸", DAY);
    assert!(l.lookup("管把", &r, DAY).1.is_empty() && l.lookup("", &r, DAY).1.is_empty(), "sentence start is not global");
    assert_eq!(l.lookup(SENTINEL, &r, DAY).1.len(), 1);
    assert!(l.records().iter().all(|x| x.context != GLOBAL));

    let mut l = Learner::default();
    l.teach("管把", &r, "權力", "全力", DAY);
    assert_eq!(l.lookup("肯把", &r, DAY).1[0].0, "權力", "1-char step: same last character");
    assert_eq!(l.lookup("管把", &r, DAY).1[0].0, "權力");
    assert!(l.lookup("把", &r, DAY).1.is_empty() || l.lookup("把", &r, DAY).1[0].0 == "權力", "1-char key still shares the last char");
    assert!(l.lookup("管他", &r, DAY).1.is_empty() && l.lookup(SENTINEL, &r, DAY).1.is_empty());
    assert!(l.records().iter().all(|x| x.context != GLOBAL), "one teach never globalizes");
    // a second distinct full key globalizes; 1-char matches and repeats of the same key do not
    l.teach("管把", &r, "權力", "全力", DAY);
    assert!(l.records().iter().all(|x| x.context != GLOBAL));
    l.teach("肯把", &r, "權力", "全力", DAY);
    assert_eq!(l.records().iter().filter(|x| x.context == GLOBAL).count(), 1);
    assert_eq!(l.lookup("完全不同", &r, DAY).1[0].0, "權力", "global answers any context");
    assert_eq!(l.lookup(SENTINEL, &r, DAY).1[0].0, "權力");
    // SENTINEL counts as one of the two keys
    let mut l = Learner::default();
    l.teach(SENTINEL, &r, "權力", "全力", DAY);
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
    e.learning_clear().unwrap_err(); // no store: an error (§4), memory still dropped
    e.set_left_context(TAUGHT_AFTER);
    assert_ne!(type_syls(&mut e, reading).preedit, word, "and only because of the record");
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
/// and the 76-row probe) row for row (S2h §5.2: an empty left context leaves decoding as it was). Under
/// the left context "好他", which S2h now lets condition the first word, the learning-on engine with an
/// empty learner and a learning-off engine commit the same dev302 rows: learning alone changes nothing.
#[test]
fn empty_learner_matches_goldens() {
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
    l.teach("他", &r, "欣", "鑫", DAY);
    assert_eq!(l.lookup("他", &r, DAY).1[0].1, 1.0);
    assert!((l.lookup("他", &r, DAY + 14).1[0].1 - 0.5).abs() < 1e-12, "half-life 14 days");
    assert!(l.lookup("他", &r, DAY + 15).1.is_empty(), "below 0.5 stops boosting");
    assert_eq!(l.lookup("他", &r, DAY - 100).1[0].1, 1.0, "a clock that went back does not grow weights");
}

#[test]
fn repick_halves_the_displaced_word_and_adds_to_the_new_one() {
    let r = syls("ㄒㄧㄣ");
    let mut l = Learner::default();
    l.teach("他", &r, "欣", "鑫", DAY);
    l.teach("他", &r, "欣", "鑫", DAY);
    l.teach("他", &r, "新", "欣", DAY);
    let w = |x: &str| l.lookup("他", &r, DAY).1.iter().find(|h| h.0 == x).map(|h| h.1);
    assert_eq!(w("欣"), Some(1.0), "2.0 halved");
    assert_eq!(w("新"), Some(1.0));
    // the displaced word had no record: nothing to halve, nothing created
    let mut l = Learner::default();
    l.teach("他", &r, "新", "鑫", DAY);
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
    l.teach(SENTINEL, &r, "欣奔", "鑫犇", DAY);
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
    let mut e = Engine::new(&root().join("data/lexicon"), L).unwrap();
    e.load_lm(&lm_path()).unwrap();
    e.set_today(Some(DAY));
    e.set_learning(true);
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

// ---------- 8: commit paths (§1.2) ----------

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
    // §12 rule 6: a single character learns only after Han text, as `picked()` sets up.
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
    assert_eq!(commit.chars().count(), MAX_SYLLABLES, "the 40th syllable auto-commits");
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
    assert_eq!(type_syls(&mut e, "ㄅㄣ").preedit, "犇", "engine was reset");
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
    // Two-character words may learn under ^ (single characters may not, §12), so use the real model.
    let mut e = engine();
    e.set_left_context("好他");
    type_syls(&mut e, "ㄑㄩㄢˊ ㄌㄧˋ");
    e.key(k(KeyKind::Enter)).unwrap();
    let shown = type_syls(&mut e, "ㄑㄩㄢˊ ㄌㄧˋ").preedit;
    pick(&mut e, 2, 2, if shown == "權力" { "全力" } else { "權力" });
    e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!(e.learner().records()[0].context, SENTINEL, "the second composition had no left context");
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

// ---------- store-dependent (§6.13; temporary directories only) ----------

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
/// Code review 2026-10-05: ⌘⌫ on a word picked in this same composition must also drop that pick's
/// pending learn, or the next Enter teaches the forgotten word again and appends it back to disk.
#[test]
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

/// Rule 6 teaches nothing, so the Enter must not touch the store at all: no file, no rewrite.
#[test]
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

/// §4: without a successful learning_open, clear is an error (ABI 3), but memory still goes.
#[test]
fn clear_without_a_store_is_an_error() {
    let mut e = picked();
    e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!(n_records(&e), 1);
    assert!(e.learning_clear().is_err());
    assert_eq!(n_records(&e), 0);
}

/// §6.13 steps 1-4: a failed full rewrite during a forget must not let a later append succeed, or
/// the forgotten word would come back on the next load.
#[test]
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
    assert_eq!(repick(&mut e, "ㄅㄣ"), "奔");
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
/// character under ^, rule 6), not only by one that learns something.
#[test]
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

/// §6.13: a panic inside the forget, after memory changed and before the file did: bit0 is untouched
/// but the next learning Enter is a full rewrite, so the word is gone from the file too.
#[test]
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

/// §6.13: 100 days pass on the same engine; the first write of the new day rewrites and prunes.
#[test]
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

/// §6.13: 100 days pass and a new engine opens; its first write rewrites and prunes.
#[test]
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

/// §6.13: the last line cut inside its word field, no newline: skipped on load, and the next write
/// (the first after open, a full rewrite) never glues an append onto it.
#[test]
fn store_torn_tail_is_skipped_and_never_glued() {
    use core::learn_store::Opened;
    let dir = tmp_dir("torn");
    let mut e = tiny(TINY, TINY);
    e.learning_open(&dir).unwrap();
    assert_eq!(repick(&mut e, "ㄒㄧㄣ"), "欣");
    assert_eq!(repick(&mut e, "ㄅㄣ"), "奔"); // appended: the last line is ^ ㄅㄣ 奔 ...
    let text = raw(&dir);
    let last = text.trim_end_matches('\n').rfind('\n').unwrap() + 1;
    let word_at = last + text[last..].match_indices('\t').nth(1).unwrap().0 + 1;
    assert!(text[word_at..].starts_with("奔"));
    std::fs::write(file_of(&dir), &text.as_bytes()[..word_at + 1]).unwrap(); // half of 奔's bytes
    let (mut e2, opened) = reopen(&dir);
    assert_eq!(opened, Opened::Loaded { skipped: 1 });
    assert!(e2.learner().records().iter().all(|r| r.word != "奔") && n_records(&e2) == 1);
    repick(&mut e2, "ㄅㄣ");
    let (e3, opened) = reopen(&dir);
    assert_eq!(opened, Opened::Loaded { skipped: 0 }, "no glued or broken line");
    assert_eq!(data_lines(&dir), n_records(&e3));
    assert!(raw(&dir).ends_with('\n'));
    assert_eq!(sorted(&e3), sorted(&e2));
    let _ = std::fs::remove_dir_all(&dir);
}

/// §6.13: each learning Enter adds exactly the changed records as lines (not a rewrite); the Enter
/// that would reach JOURNAL_MAX appended lines rewrites, leaving one line per record.
#[test]
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

/// §6.13: after a forget the word appears 0 times in the file; after a clear the next learning Enter
/// starts a new file holding only the header and the new records.
#[test]
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

/// §4: forget rewrites even when memory holds no record of the word: a record pruned on load (here
/// 100 days old) is still in the file until a full rewrite, and the forget must take it out now.
#[test]
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

/// §6.13: a symlink, a FIFO, a second hard link, a file emptied by hand or one with group bits in
/// place of the learning file: the append is refused and the full rewrite replaces it with a 0600
/// regular file, never writing through the link.
#[test]
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

// ---------- §6.14 performance (asserted in release builds only, like engine_replay.rs) ----------

/// Peak resident set size in bytes (macOS reports ru_maxrss in bytes).
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
/// CAPACITY); the timed Enters after it append (revision one, §4 and §11).
#[test]
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
    let contexts = ["^", "", "我", "你們", "今天", "的", "了", "在", "是", "他說", "大家", "一個", "不會", "這樣", "所以", "還是", "沒有"];
    let mut records = Vec::new();
    'fill: for c in contexts {
        for span in &spans {
            for (word, _) in lex.entries(span).into_iter().take(3) {
                // §12: a single-character record under ^ or the global key is dropped at load, so none is seeded.
                if (c.is_empty() || c == "^") && word.chars().count() == 1 {
                    continue;
                }
                records.push(Record { context: c.into(), reading: span.clone(), word: word.into(), weight: 1.0, day: DAY });
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

    // §6.14: the first learning Enter after open is a full rewrite (reported apart); the next 100+ on
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

// ---------- §12: single characters do not globalize; a smaller global boost ----------

/// The same text decoded and committed with `left` as the text before it (the key is consumed by the Enter).
fn decode(e: &mut Engine, left: &str, reading: &[String]) -> String {
    e.set_left_context(left);
    commit_of(e, &reading.join(" "))
}
/// §12 collision rule: does a key the decoder uses before some occurrence of `span` hit a record taught
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
    for c in ["可以", SENTINEL, "他"] {
        l.teach(c, &two, "再見", "在建", DAY);
    }
    assert_eq!(l.records().iter().filter(|r| r.context == GLOBAL).count(), 1, "a two-character word does");
    // the lookup says which level answered
    assert_eq!(l.lookup("可以", &two, DAY).0, Level::Exact);
    assert_eq!(l.lookup("不以", &two, DAY).0, Level::LastChar);
    assert_eq!(l.lookup("我們", &two, DAY).0, Level::Global);
}

#[test]
fn old_single_character_global_record_is_dropped_at_load() {
    // a file written before §12, loaded by an engine: the record is not loaded at all
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

/// The user's case (§12): 再 re-picked under 可以 and at sentence start. Under rule 6 the sentence-start
/// pick creates no record. Rows are replayed with their own 前文; those whose decoder keys equal the
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
    let (mut regress, mut changed, mut collided, mut unaligned, mut checked) = (Vec::new(), Vec::new(), Vec::new(), Vec::new(), 0);
    for (r, b) in rows.iter().zip(&base) {
        let after = decode(&mut e, &r.0, &r.2);
        match row_collides(&r.0, &[&r.1, b, &after], &r.2, &zai, &taught, true) {
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
    println!("global 再 records: {}", e.learner().records().iter().filter(|r| r.context == GLOBAL).count());
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

/// §12 rules 5 and 6 at the lookup: a single character is used only at the exact full key, and never
/// under "^"; a word of 2+ characters keeps all three levels and "^".
#[test]
fn single_character_rules_in_the_learner() {
    let one = syls("ㄗㄞˋ");
    let two = syls("ㄗㄞˋ ㄐㄧㄢˋ");
    let mut l = Learner::default();
    assert!(l.teach(SENTINEL, &one, "再", "在", DAY).is_empty(), "rule 6: nothing is learned under ^");
    assert!(l.records().is_empty());
    assert_eq!(l.teach(SENTINEL, &two, "再見", "在建", DAY).len(), 1, "a word of 2+ characters still learns under ^");
    l.teach("可以", &one, "再", "在", DAY);
    assert_eq!(l.lookup("可以", &one, DAY).0, Level::Exact);
    assert_eq!(l.lookup("可以", &one, DAY).1[0].0, "再");
    assert!(l.lookup("所以", &one, DAY).1.is_empty(), "rule 5: no last-character level for a single character");
    assert!(l.lookup(SENTINEL, &one, DAY).1.is_empty() && l.lookup("我們", &one, DAY).1.is_empty());
    // teach never makes a single-character record under ^ or the global key, however often it is
    // taught; saved and reloaded through the store none appears either
    let mut l = Learner::default();
    for c in [SENTINEL, "可以", "他", "我們", SENTINEL] {
        l.teach(c, &one, "再", "在", DAY);
    }
    assert!(l.records().iter().all(|r| r.context != SENTINEL && r.context != GLOBAL));
    let dir = tmp_dir("nosingleglobal");
    let (store, _, _) = core::learn_store::LearnStore::open(&dir).unwrap();
    store.save(l.records()).unwrap();
    let (_, loaded, _) = core::learn_store::LearnStore::open(&dir).unwrap();
    assert_eq!(loaded.len(), 3, "可以, 他, 我們");
    assert!(loaded.iter().all(|r| r.context != SENTINEL && r.context != GLOBAL));
    let _ = std::fs::remove_dir_all(&dir);
}

/// §12 acceptance: 再 taught after 可以 does not reach 所以, 前文 after punctuation or ASCII, or an
/// unreadable context; a pick under ^ leaves no record; an old ^ record in a file is not used.
#[test]
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

/// §12 global learning and global pollution tests at global ε `eps`. Per cases.tsv group whose taught
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

// ---- sweep (needs the §12 test hook `set_eps_global`) ----

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
    // s4-learning §13: with the class term no value above 0 has 0 pollution, so the level is off; the table
    // still prints the learn rate and pollution of each value for the next study of the global level.
    for r in &res {
        // PLAN A2: of the learnable mirror cases (the model does not already produce the taught sentence) at least 80% are learned.
        // The learnable count depends on the model (9/10 with model-v1, 8/9 once 戰機/戰績 became right on its own), so the
        // floor is on the count of learnable cases, which keeps the rate from passing vacuously.
        assert!(r.3 >= 8, "fewer than 8 learnable mirror cases ({} at eps {}): the rate below would not mean anything", r.3, r.0);
        assert!(r.2 * 100 >= r.3 * 80, "mirror learn rate below 80%: {}/{} at eps {}", r.2, r.3, r.0);
    }
}
