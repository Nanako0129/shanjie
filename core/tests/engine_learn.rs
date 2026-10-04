//! S4 core tests (docs/contracts/s4-learning.md §6 items 1-9). Needs data/lm/bigram.sjlm like engine_lm.rs.
//! Tests that need the learning file (`learn_store`, written by another executor) are `#[ignore]`d with
//! the reason; main runs them after integration with `cargo test --release -- --ignored`.
use core::engine::*;
use core::eval::{parse_rows, usable};
use core::learn::{context_key, Learner, Record, CAPACITY, GLOBAL, PRUNE_FLOOR, SENTINEL};
use core::lm::{CappedLexicon, Lm};
use core::{Lexicon, Syls};
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}
fn lm_path() -> PathBuf {
    let p = root().join("data/lm/bigram.sjlm");
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
        let capped = Arc::new(CappedLexicon::new(lex.clone(), &overlay, &lm));
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
    let capped = Arc::new(CappedLexicon::new(cl, "", &s.lm));
    let mut e = Engine::with_lexicon(lex, L);
    e.set_lm(s.lm.clone(), capped);
    e.set_today(Some(DAY));
    e.set_learning(true);
    e
}
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
        o = e.key(k(KeyKind::Down)).unwrap();
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
/// Would a record taught under `taught` answer a decode-time query under `query` (§1.1 order)?
fn collides(taught: &str, query: &str) -> bool {
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
        let reach = collides(&tctx, &sctx);
        ceiling += reach as usize;
        let coll: Vec<&str> = g
            .rows
            .iter()
            .filter(|r| r.kind == "common")
            .filter(|r| collides(&tctx, &span_of(&r.sent, &g.other).0))
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
        e.learning_clear().unwrap();
        let teach = g.rows.iter().find(|r| r.kind == "teach").unwrap();
        let (tctx, _, _) = span_of(&teach.sent, &g.word);
        let before: Vec<(bool, bool)> = g.rows.iter().map(|r| judge(&mut e, g, r)).collect();
        teach_row(&mut e, g, teach);
        let n_rec = e.learner().records().iter().filter(|r| r.word == g.word).count();
        let after: Vec<(bool, bool)> = g.rows.iter().map(|r| judge(&mut e, g, r)).collect();
        let f = |v: &[(bool, bool)]| g.rows.iter().zip(v).map(|(r, b)| format!("{}{}{}", &r.kind[..1], b.0 as u8, b.1 as u8)).collect::<Vec<_>>().join(" ");
        println!("{} | {} -> {} | {n_rec}", g.name, f(&before), f(&after));
        // Nothing to pick when the default already shows the taught word: the group is moot (reported).
        if n_rec == 0 && before[0].1 {
            moot.push(g.name.clone());
        } else if n_rec == 0 {
            failed.push(g.name.clone());
        }
        let same = g.rows.iter().find(|r| r.same == Some(true)).unwrap();
        let reach = collides(&tctx, &span_of(&same.sent, &g.word).0);
        for (i, r) in g.rows.iter().enumerate() {
            let coll = r.kind == "common" && collides(&tctx, &span_of(&r.sent, &g.other).0);
            if r.kind == "common" {
                let lost = (before[i].0 && !after[i].0) || (before[i].1 && !after[i].1);
                if coll {
                    println!("   colliding common (reported, not gated): {} before {:?} after {:?}", r.sent, before[i], after[i]);
                } else if lost {
                    regress.push(r.sent.clone());
                }
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
    assert!(learned_reach * 100 >= wrong_reach * 80, "same-context learn rate under 80%");

    // Informational: all 14 groups taught on one learner, then every common row.
    e.learning_clear().unwrap();
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
    let mut e = Engine::new(&root().join("data/lexicon"), L).unwrap();
    e.load_lm(&lm_path()).unwrap();
    e.set_today(Some(DAY));
    e.set_learning(true);
    let (mut learned, mut wrong, mut learned_reach, mut wrong_reach) = (0, 0, 0, 0);
    for g in &groups() {
        e.learning_clear().unwrap();
        let teach = g.rows.iter().find(|r| r.kind == "teach").unwrap();
        let same = g.rows.iter().find(|r| r.same == Some(true)).unwrap();
        let (tctx, _, _) = span_of(&teach.sent, &g.word);
        let reach = collides(&tctx, &span_of(&same.sent, &g.word).0);
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
    println!("mirror: learned {learned}/{wrong}; reachable keys only {learned_reach}/{wrong_reach}");
    assert!(learned_reach * 100 >= wrong_reach * 80, "mirror learn rate under 80%");
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

    // (b) after a fixed word
    let mut e = tiny(TINY, TINY);
    type_syls(&mut e, "ㄅㄣ ㄒㄧㄣ");
    pick(&mut e, 2, 1, "奔");
    pick(&mut e, 2, 2, "欣");
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, "奔欣");
    assert_eq!(keys(&e), ["^", "奔"], "奔 at sentence start, 欣 after the fixed word 奔");
    assert_eq!(type_syls(&mut e, "ㄅㄣ ㄒㄧㄣ").preedit, "奔欣");
    e.key(k(KeyKind::Esc)).unwrap();

    // (c) after punctuation
    let mut e = tiny(TINY, TINY);
    type_syls(&mut e, "ㄊㄚ");
    e.key(Key::ch(',', MOD_SHIFT)).unwrap();
    type_syls(&mut e, "ㄒㄧㄣ");
    pick(&mut e, 3, 3, "欣");
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, "他，欣");
    assert_eq!(keys(&e), ["^"]);
    type_syls(&mut e, "ㄊㄚ");
    e.key(Key::ch(',', MOD_SHIFT)).unwrap();
    assert_eq!(type_syls(&mut e, "ㄒㄧㄣ").preedit, "他，欣");
    e.key(k(KeyKind::Esc)).unwrap();
    assert_eq!(type_syls(&mut e, "ㄊㄚ ㄒㄧㄣ").preedit, "他鑫", "no punctuation: key 他, not ^");
    e.key(k(KeyKind::Esc)).unwrap();

    // (d) sentence start
    let mut e = tiny(TINY, TINY);
    type_syls(&mut e, "ㄒㄧㄣ");
    pick(&mut e, 1, 1, "欣");
    e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!(keys(&e), ["^"]);
    assert_eq!(type_syls(&mut e, "ㄒㄧㄣ").preedit, "欣");
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
    let r = syls("ㄅㄚˇ");
    let mut l = Learner::default();
    l.teach(SENTINEL, &r, "把", "爸", DAY);
    assert!(l.lookup("管把", &r, DAY).is_empty() && l.lookup("", &r, DAY).is_empty(), "sentence start is not global");
    assert_eq!(l.lookup(SENTINEL, &r, DAY).len(), 1);
    assert!(l.records().iter().all(|x| x.context != GLOBAL));

    let mut l = Learner::default();
    l.teach("管把", &r, "權", "全", DAY);
    assert_eq!(l.lookup("肯把", &r, DAY)[0].0, "權", "1-char step: same last character");
    assert_eq!(l.lookup("管把", &r, DAY)[0].0, "權");
    assert!(l.lookup("把", &r, DAY).is_empty() || l.lookup("把", &r, DAY)[0].0 == "權", "1-char key still shares the last char");
    assert!(l.lookup("管他", &r, DAY).is_empty() && l.lookup(SENTINEL, &r, DAY).is_empty());
    assert!(l.records().iter().all(|x| x.context != GLOBAL), "one teach never globalizes");
    // a second distinct full key globalizes; 1-char matches and repeats of the same key do not
    l.teach("管把", &r, "權", "全", DAY);
    assert!(l.records().iter().all(|x| x.context != GLOBAL));
    l.teach("肯把", &r, "權", "全", DAY);
    assert_eq!(l.records().iter().filter(|x| x.context == GLOBAL).count(), 1);
    assert_eq!(l.lookup("完全不同", &r, DAY)[0].0, "權", "global answers any context");
    assert_eq!(l.lookup(SENTINEL, &r, DAY)[0].0, "權");
    // SENTINEL counts as one of the two keys
    let mut l = Learner::default();
    l.teach(SENTINEL, &r, "權", "全", DAY);
    l.teach("管把", &r, "權", "全", DAY);
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
    assert_ne!(type_syls(&mut e, reading).preedit, word);
    pick(&mut e, 1, 1, &word);
    e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!(type_syls(&mut e, reading).preedit, word, "rank 21 word wins after one teach");
    e.key(k(KeyKind::Esc)).unwrap();
    e.learning_clear().unwrap();
    assert_ne!(type_syls(&mut e, reading).preedit, word, "and only because of the record");
}

// ---------- 6: empty learner changes nothing ----------

fn probe_rows() -> Vec<(String, Syls)> {
    let probe = std::fs::read_to_string(root().join("eval/probe/s2r-probe.txt")).unwrap();
    parse_rows(&probe).unwrap().into_iter().map(|r| (r.sent, r.reading.unwrap())).collect()
}
fn dev302() -> Vec<Syls> {
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
    rows.into_iter().map(|r| r.reading.clone().unwrap_or_else(|| lex.to_syllables(&r.sent).unwrap())).collect()
}
fn commit_syls(e: &mut Engine, s: &Syls) -> String {
    commit_of(e, &s.join(" "))
}

/// Learning on with a left context but an empty learner reproduces the committed goldens (dev302 chat,
/// and the 76-row probe) row for row.
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
            e.set_left_context("好他");
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
    assert!(e.learner().records().is_empty());
}

// ---------- 7: decay, replacing a pick, pruning, capacity, forgetting ----------

#[test]
fn decay_halves_every_fourteen_days_and_never_grows_back() {
    let r = syls("ㄒㄧㄣ");
    let mut l = Learner::default();
    l.teach("他", &r, "欣", "鑫", DAY);
    assert_eq!(l.lookup("他", &r, DAY)[0].1, 1.0);
    assert!((l.lookup("他", &r, DAY + 14)[0].1 - 0.5).abs() < 1e-12, "half-life 14 days");
    assert!(l.lookup("他", &r, DAY + 15).is_empty(), "below 0.5 stops boosting");
    assert_eq!(l.lookup("他", &r, DAY - 100)[0].1, 1.0, "a clock that went back does not grow weights");
}

#[test]
fn repick_halves_the_displaced_word_and_adds_to_the_new_one() {
    let r = syls("ㄒㄧㄣ");
    let mut l = Learner::default();
    l.teach("他", &r, "欣", "鑫", DAY);
    l.teach("他", &r, "欣", "鑫", DAY);
    l.teach("他", &r, "新", "欣", DAY);
    let w = |x: &str| l.lookup("他", &r, DAY).iter().find(|h| h.0 == x).map(|h| h.1);
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
    let words: Vec<&str> = l.records().iter().map(|x| x.word.as_str()).collect();
    assert_eq!(words, ["b", "c"], "70 days old is under PRUNE_FLOOR {PRUNE_FLOOR}; 10 days is not");
    let mut big: Vec<Record> = (0..CAPACITY + 10).map(|i| rec("他", r, &format!("w{i}"), 1.0 + i as f64, DAY)).collect();
    big.push(rec("他", r, "low", 0.3, DAY));
    let mut l = Learner::from_records(big);
    l.prune(DAY);
    assert_eq!(l.records().len(), CAPACITY);
    assert!(l.records().iter().all(|x| x.word != "low" && x.word != "w0"), "lowest weights go first");
}

#[test]
fn forget_removes_every_key() {
    let r = syls("ㄒㄧㄣ");
    let mut l = Learner::default();
    l.teach("他", &r, "欣", "鑫", DAY);
    l.teach(SENTINEL, &r, "欣", "鑫", DAY);
    assert_eq!(l.records().iter().filter(|x| x.context == GLOBAL).count(), 1);
    l.teach("他", &r, "新", "鑫", DAY);
    l.forget(&r, "欣");
    assert_eq!(l.records().len(), 1, "only 新 is left");
    assert_eq!(l.records()[0].word, "新");
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
    let o = e.key(k(KeyKind::Down)).unwrap();
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
    type_syls(&mut e, "ㄒㄧㄣ");
    e.key(k(KeyKind::Space)).unwrap();
    e.key(k(KeyKind::Enter)).unwrap();
    e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!(n_records(&e), 0);
}
#[test]
fn left_context_is_dropped_after_commit() {
    let mut e = tiny(TINY, TINY);
    e.set_left_context("好他");
    type_syls(&mut e, "ㄒㄧㄣ");
    e.key(k(KeyKind::Enter)).unwrap();
    type_syls(&mut e, "ㄒㄧㄣ");
    pick(&mut e, 1, 1, "欣");
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
    type_syls(&mut e, "ㄒㄧㄣ");
    pick(&mut e, 1, 1, "欣");
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, "欣");
    assert_eq!(n_records(&e), 1);
}

// ---------- store-dependent (run after integration with learn_store) ----------

fn tmp_dir(tag: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("shanjie-learn-{}-{tag}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    d
}

#[test]
#[ignore = "needs learn_store (security-executor); run after integration with --ignored"]
fn store_commit_writes_the_file_and_reopen_restores() {
    let dir = tmp_dir("commit");
    let mut e = tiny(TINY, TINY);
    e.learning_open(&dir).unwrap();
    type_syls(&mut e, "ㄒㄧㄣ");
    pick(&mut e, 1, 1, "欣");
    e.key(k(KeyKind::Enter)).unwrap();
    let text = std::fs::read_to_string(dir.join("learning.tsv")).unwrap();
    let rows: Vec<Vec<&str>> = text.lines().skip(1).map(|l| l.split('\t').collect()).collect();
    assert_eq!(rows.len(), 1);
    assert_eq!(&rows[0][..3], ["^", "ㄒㄧㄣ", "欣"]);
    assert_eq!(rows[0].len(), 5, "five fields only");
    assert_eq!(e.learning_status(), 0);
    let mut e2 = tiny(TINY, TINY);
    e2.learning_open(&dir).unwrap();
    assert_eq!(type_syls(&mut e2, "ㄒㄧㄣ").preedit, "欣");
    // forget writes immediately
    let o = e2.key(k(KeyKind::Space)).unwrap();
    highlight(&mut e2, o, "欣");
    e2.key(Key { kind: KeyKind::Backspace, ch: '\0', modifiers: MOD_COMMAND }).unwrap();
    let text = std::fs::read_to_string(dir.join("learning.tsv")).unwrap();
    assert_eq!(text.lines().count(), 1, "header only");
    let _ = std::fs::remove_dir_all(&dir);
}
#[test]
#[ignore = "needs learn_store (security-executor); run after integration with --ignored"]
fn store_learning_off_then_enter_writes_nothing_and_clear_removes_the_file() {
    let dir = tmp_dir("off");
    let mut e = tiny(TINY, TINY);
    e.learning_open(&dir).unwrap();
    type_syls(&mut e, "ㄒㄧㄣ");
    pick(&mut e, 1, 1, "欣");
    e.set_learning(false);
    e.key(k(KeyKind::Enter)).unwrap();
    assert!(!dir.join("learning.tsv").exists(), "set_learning(0) then Enter writes nothing");
    e.set_learning(true);
    type_syls(&mut e, "ㄒㄧㄣ");
    pick(&mut e, 1, 1, "欣");
    e.key(k(KeyKind::Enter)).unwrap();
    assert!(dir.join("learning.tsv").exists());
    // a pick made before clear is not learned by the next Enter
    type_syls(&mut e, "ㄅㄣ");
    pick(&mut e, 1, 1, "奔");
    e.learning_clear().unwrap();
    assert!(!dir.join("learning.tsv").exists());
    e.key(k(KeyKind::Enter)).unwrap();
    assert!(!dir.join("learning.tsv").exists() && n_records(&e) == 0);
    e.learning_clear().unwrap(); // already gone: success
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
#[ignore = "needs learn_store (security-executor); run after integration with --ignored"]
fn store_write_failure_sets_the_status_flag_and_keeps_the_commit() {
    let dir = tmp_dir("fail");
    let mut e = tiny(TINY, TINY);
    e.learning_open(&dir).unwrap();
    std::fs::remove_dir_all(&dir).unwrap();
    std::fs::write(&dir, b"a file where the directory was").unwrap();
    type_syls(&mut e, "ㄒㄧㄣ");
    pick(&mut e, 1, 1, "欣");
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, "欣");
    assert_eq!(e.learning_status(), 1);
    let _ = std::fs::remove_file(&dir);
}

#[test]
fn global_key_is_not_the_sentinel() {
    assert_ne!(GLOBAL, SENTINEL);
}
