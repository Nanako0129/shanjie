//! New-word B (docs/contracts/newword-b.md): learning words the lexicon lacks from character re-picks (§3.4 tests)
//! and the measurement tool of §3.1 (`measure_newword`, ignored). Needs data/lm/bigram.sjlm like engine_lm.rs. Store
//! tests write only into temporary directories.
use core::engine::*;
use core::learn::{Record, NEWWORD, NEWWORD_SHADOW};
use core::lm::{CappedLexicon, Lm};
use core::Lexicon;
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
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
        let lm = Lm::load(&root().join("data/lm/bigram.sjlm")).expect("data/lm/bigram.sjlm and classes.sjc (see engine_lm.rs)");
        let overlay = std::fs::read_to_string(root().join("data/lexicon/overlay-add.tsv")).unwrap();
        let capped = Arc::new(CappedLexicon::new(lex.clone(), &overlay, &lm, None).unwrap());
        Shared { lex, lm: Arc::new(lm), capped }
    })
}
const DAY: i64 = 20_000;
const L: Layout = Layout::Standard;

/// Production lexicon and model, learning on, fixed clock.
fn engine() -> Engine {
    let s = shared();
    let mut e = Engine::with_lexicon(s.lex.clone(), L);
    e.set_lm(s.lm.clone(), s.capped.clone());
    e.set_today(Some(DAY));
    e.set_learning(true);
    e
}

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
fn k(kind: KeyKind) -> Key {
    Key::new(kind)
}
const FORGET: Key = Key { kind: KeyKind::Backspace, ch: '\0', modifiers: MOD_COMMAND };
fn type_syls(e: &mut Engine, reading: &str) -> Output {
    let mut o = None;
    for s in reading.split(' ') {
        for key in keys_of(s) {
            o = Some(e.key(key).unwrap());
        }
    }
    o.unwrap()
}
/// Open the candidates for the span ending after syllable `end` (of `n` typed, cursor at the end), highlight `word`.
fn open_at(e: &mut Engine, n: usize, end: usize, word: &str) -> Output {
    for _ in 0..n - end {
        e.key(k(KeyKind::Left)).unwrap();
    }
    let mut o = e.key(k(KeyKind::Space)).unwrap();
    for _ in 0..5000 {
        if o.candidates[o.selected.unwrap()] == word {
            return o;
        }
        o = e.key(k(KeyKind::Right)).unwrap();
    }
    panic!("candidate not found");
}
/// Choose `word` over the span ending after syllable `end`; the cursor goes back to the end.
fn pick(e: &mut Engine, n: usize, end: usize, word: &str) -> Output {
    open_at(e, n, end, word);
    e.key(k(KeyKind::Enter)).unwrap();
    e.key(k(KeyKind::End)).unwrap()
}
/// Re-pick the single character at syllable `at` (0-based) to another one-syllable candidate than the one shown;
/// returns the new display.
fn repick_char(e: &mut Engine, n: usize, at: usize) -> String {
    let shown: Vec<char> = e.key(k(KeyKind::End)).unwrap().preedit.chars().collect();
    for _ in 0..n - (at + 1) {
        e.key(k(KeyKind::Left)).unwrap();
    }
    let mut o = e.key(k(KeyKind::Space)).unwrap();
    loop {
        let c = &o.candidates[o.selected.unwrap()];
        if c.chars().count() == 1 && c.chars().next() != Some(shown[at]) {
            break;
        }
        o = e.key(k(KeyKind::Right)).unwrap();
    }
    e.key(k(KeyKind::Enter)).unwrap();
    e.key(k(KeyKind::End)).unwrap().preedit
}
fn new_words(e: &Engine) -> Vec<String> {
    let mut v: Vec<String> = e.learner().records().iter().filter(|r| r.context == NEWWORD).map(|r| r.word.clone()).collect();
    v.sort();
    v
}
fn new_record<'a>(e: &'a Engine, word: &str) -> Option<&'a Record> {
    e.learner().records().iter().find(|r| r.context == NEWWORD && r.word == word)
}
fn tmp_dir(tag: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("shanjie-newword-{}-{tag}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    d
}

const PAIGONG: &str = "ㄆㄞˋ ㄍㄨㄥ";
/// The motivating report: the maintainer's re-picks of the name 正彬 were never learned (no lexicon word under its
/// reading; S4 learns a single character only after exactly two Han characters, rule 7).
const ZHENGBIN: &str = "ㄓㄥˋ ㄅㄧㄣ";

/// Re-pick the first character of the composition that differs from `target` (one character per syllable) until
/// none does, as a user re-picks what the re-decode still shows wrong; the cursor goes back to the end.
fn spell(e: &mut Engine, target: &str) {
    let t: Vec<char> = target.chars().collect();
    for _ in 0..2 * t.len() {
        let shown: Vec<char> = e.key(k(KeyKind::End)).unwrap().preedit.chars().collect();
        assert_eq!(shown.len(), t.len());
        let Some(i) = (0..t.len()).find(|&i| shown[i] != t[i]) else { return };
        pick(e, t.len(), i + 1, &t[i].to_string());
    }
    panic!("could not spell the target");
}

/// The user story of §1 with 派工, one of the reported missing words (contract §0; since model-v6 the model decodes 版控
/// unaided in most contexts, 派工 stays 派公 in every one): after 要進 was sent, type ㄆㄞˋ ㄍㄨㄥ, re-pick each wrong
/// character one by one, Enter.
fn teach_paigong(e: &mut Engine) {
    e.set_left_context("要進");
    let o = type_syls(e, PAIGONG);
    assert_ne!(o.preedit, "派工", "the lexicon lacks 派工 (else this slice has nothing to show)");
    spell(e, "派工");
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, "派工");
}

// ---------- §3.1 measurement tool ----------

/// `前文|句子|讀音` rows as the CLI's `--rows` reads them: only lines with exactly three fields.
fn three_field_rows(text: &str) -> Vec<(String, String, Vec<String>)> {
    text.lines()
        .filter_map(|l| {
            let p: Vec<&str> = l.split('|').collect();
            (p.len() == 3).then(|| (p[0].to_string(), p[1].to_string(), p[2].split_whitespace().map(String::from).collect()))
        })
        .collect()
}
/// One source of rows: `dev302` (the CLI's `--dev 302`: every eval/dev/*.txt in name order, first 302 rows), or a
/// rows file (a path relative to the repository root, or absolute).
fn rows_of(src: &str) -> Vec<(String, String, Vec<String>)> {
    if let Some(n) = src.strip_prefix("dev") {
        let n: usize = n.parse().expect("devN");
        let mut files: Vec<PathBuf> = std::fs::read_dir(root().join("eval/dev"))
            .unwrap()
            .filter_map(|e| e.ok().map(|e| e.path()))
            .filter(|p| p.extension().is_some_and(|x| x == "txt"))
            .collect();
        files.sort();
        let mut rows: Vec<_> = files.iter().flat_map(|f| three_field_rows(&std::fs::read_to_string(f).unwrap())).collect();
        rows.truncate(n);
        return rows;
    }
    three_field_rows(&std::fs::read_to_string(root().join(src)).expect("rows file"))
}
fn sources(var: &str) -> Option<Vec<(String, String, Vec<String>)>> {
    let v = std::env::var(var).ok().filter(|v| !v.is_empty())?;
    Some(v.split(',').flat_map(rows_of).collect())
}
fn levenshtein(a: &str, b: &str) -> usize {
    let b: Vec<char> = b.chars().collect();
    let mut prev: Vec<usize> = (0..=b.len()).collect();
    for (i, x) in a.chars().enumerate() {
        let mut cur = vec![i + 1];
        for (j, y) in b.iter().enumerate() {
            cur.push((prev[j + 1] + 1).min(cur[j] + 1).min(prev[j] + (x != *y) as usize));
        }
        prev = cur;
    }
    prev[b.len()]
}
/// §3.1 teach: type the row after its context; while it is wrong, open the candidates right after the first wrong
/// character and choose the longest candidate ending at the cursor that equals the gold text there; Enter once the
/// row is right or nothing more can be fixed. Rows whose sentence and reading differ in length are not taught.
fn teach_row(e: &mut Engine, ctx: &str, gold: &str, syls: &[String]) {
    use core::eval::char_map;
    let g: Vec<char> = gold.chars().collect();
    let n = syls.len();
    e.set_left_context(ctx);
    if g.len() != n {
        return;
    }
    let gm: Vec<char> = char_map(gold).chars().collect();
    let mut o = type_syls(e, &syls.join(" "));
    for _ in 0..2 * n + 2 {
        let d: Vec<char> = char_map(&o.preedit).chars().collect();
        let Some(i) = (0..n.min(d.len())).find(|&i| d[i] != gm[i]) else { break };
        let j = i + 1;
        let lex = &shared().lex;
        let pick_word = (1..=j.min(8)).rev().map(|l| (l, g[j - l..j].iter().collect::<String>())).find(|(l, w)| {
            let r = &syls[j - *l..j];
            lex.entries(r).iter().any(|(x, _)| x == w) || e.learner().new_words_all(r, DAY).contains(&w.as_str())
        });
        let Some((_, w)) = pick_word else { break };
        e.key(k(KeyKind::End)).unwrap();
        for _ in 0..n - j {
            e.key(k(KeyKind::Left)).unwrap();
        }
        let mut c = e.key(k(KeyKind::Space)).unwrap();
        let mut found = false;
        for _ in 0..c.total as usize + 1 {
            if c.candidates[c.selected.unwrap()] == w {
                found = true;
                break;
            }
            c = e.key(k(KeyKind::Right)).unwrap();
        }
        if !found {
            e.key(k(KeyKind::Esc)).unwrap();
            break;
        }
        e.key(k(KeyKind::Enter)).unwrap();
        o = e.key(k(KeyKind::End)).unwrap();
    }
    e.key(k(KeyKind::Enter)).unwrap();
}

/// The §3.1 run on one engine: teach every `teach` row (learning on, the engine's store), then replay `replay` with
/// learning off. Returns the rowstats text (`row\tok\terrors\tgold_len`, lenient top-1 like the CLI), the top-1 count and
/// the new words learned as `newword\t<reading>\t<word>` lines (reading joined by `-`, as in the learning file). With
/// `dump`, also writes `row\tok\tshown\tgold` per replayed row there (revision one; holds sentence text, so it stays on
/// the machine that ran it, contract §5).
fn measure(
    e: &mut Engine,
    teach: &[(String, String, Vec<String>)],
    replay: &[(String, String, Vec<String>)],
    len: &core::eval::Lenient,
    dump: Option<&Path>,
) -> (String, usize, Vec<String>) {
    for (ctx, gold, syls) in teach {
        teach_row(e, ctx, gold, syls);
    }
    let mut learned: Vec<String> = e
        .learner()
        .records()
        .iter()
        .filter(|r| r.context == NEWWORD)
        .map(|r| format!("newword\t{}\t{}", r.reading.join("-"), r.word))
        .collect();
    learned.sort();
    e.set_learning(false);
    let (mut rs, mut ds, mut top1) = (String::new(), String::new(), 0);
    for (i, (ctx, gold, syls)) in replay.iter().enumerate() {
        e.set_left_context(ctx);
        let shown = type_syls(e, &syls.join(" ")).preedit;
        e.reset(ResetMode::Discard);
        let ok = len.apply(&shown) == len.apply(gold);
        top1 += ok as usize;
        let errors = if ok { 0 } else { levenshtein(&shown, gold) };
        rs += &format!("{}\t{}\t{errors}\t{}\n", i + 1, ok as u8, gold.chars().count());
        ds += &format!("{}\t{}\t{shown}\t{gold}\n", i + 1, ok as u8);
    }
    if let Some(d) = dump {
        std::fs::write(d, ds).unwrap();
    }
    (rs, top1, learned)
}

/// §3.1: `NEWWORD_MODE=base|new` (base: S4 only), `NEWWORD_PROFILE=chat|formal`, `NEWWORD_TEACH` and `NEWWORD_REPLAY`
/// (comma-separated sources, see `rows_of`; replay defaults to the teach set), `NEWWORD_OUT` (the rowstats file:
/// `row\tok\terrors\tgold_len`, lenient top-1 against eval/variants.tsv like the CLI). One engine teaches every row
/// with learning on (in-memory store, fixed day), then replays the replay set with learning off. Prints counts only,
/// unless `NEWWORD_SHOW=1` (the learned words, `newword\t<reading>\t<word>` lines, for experiments/newword/hijack.py).
/// `NEWWORD_DUMP=FILE` writes the replay's `row\tok\tshown\tgold` lines there. Both hold text: they stay on the machine
/// that ran them and are deleted after use (contract §5).
#[test]
#[ignore]
fn measure_newword() {
    let var = |n: &str| std::env::var(n).unwrap_or_default();
    let mode = var("NEWWORD_MODE");
    assert!(mode == "base" || mode == "new", "NEWWORD_MODE=base|new");
    let profile = match var("NEWWORD_PROFILE").as_str() {
        "" | "chat" => core::lm::Profile::Chat,
        "formal" => core::lm::Profile::Formal,
        _ => panic!("NEWWORD_PROFILE=chat|formal"),
    };
    let teach = sources("NEWWORD_TEACH").expect("NEWWORD_TEACH");
    let replay = sources("NEWWORD_REPLAY").unwrap_or_else(|| sources("NEWWORD_TEACH").unwrap());
    let out = var("NEWWORD_OUT");
    assert!(!out.is_empty(), "NEWWORD_OUT");
    let variants = std::env::var_os("SHANJIE_VARIANTS").filter(|v| !v.is_empty()).map(PathBuf::from).unwrap_or_else(|| root().join("eval/variants.tsv"));
    let len = core::eval::Lenient::parse(&std::fs::read_to_string(variants).unwrap()).unwrap();
    let mut e = engine();
    e.set_newword(mode == "new");
    // Sub-spans (contract §2.2 item 5) follow the shipped default; NEWWORD_SUBSPANS=0/1 overrides it to measure rule A.
    if let Ok(v) = std::env::var("NEWWORD_SUBSPANS") {
        e.set_newword_subspans(v == "1");
    }
    e.set_profile(profile).unwrap();
    let dump = std::env::var_os("NEWWORD_DUMP").filter(|v| !v.is_empty()).map(PathBuf::from);
    let (rs, top1, learned) = measure(&mut e, &teach, &replay, &len, dump.as_deref());
    std::fs::write(&out, rs).unwrap();
    println!(
        "measure_newword {mode}: teach {} rows, replay {} rows, top1 {top1}, S4 records {}, new words {}",
        teach.len(),
        replay.len(),
        e.learner().records().len() - learned.len(),
        learned.len()
    );
    if std::env::var_os("NEWWORD_SHOW").is_some() {
        for l in &learned {
            println!("{l}");
        }
    }
}

/// Revision one: the `NEWWORD_DUMP` lines are `row\tok\tshown\tgold`, one per replayed row in order, and the learned list
/// is `newword\t<reading>\t<word>` (what experiments/newword/hijack.py reads).
#[test]
fn measure_dump_and_show_formats() {
    let row = |c: &str, g: &str, r: &str| (c.to_string(), g.to_string(), r.split(' ').map(String::from).collect::<Vec<_>>());
    let teach = vec![row("要進", "派工", PAIGONG)];
    let replay = vec![row("加入", "派工", PAIGONG), row("", "正彬", ZHENGBIN)];
    let len = core::eval::Lenient::parse(&std::fs::read_to_string(root().join("eval/variants.tsv")).unwrap()).unwrap();
    let dir = tmp_dir("dump");
    std::fs::create_dir_all(&dir).unwrap();
    let dump = dir.join("dump.tsv");
    let mut e = engine();
    let (rs, top1, learned) = measure(&mut e, &teach, &replay, &len, Some(&dump));
    assert_eq!(std::fs::read_to_string(&dump).unwrap(), "1\t1\t派工\t派工\n2\t0\t正賓\t正彬\n");
    assert_eq!(rs, "1\t1\t0\t2\n2\t0\t1\t2\n");
    assert_eq!(top1, 1);
    assert_eq!(learned, vec!["newword\tㄆㄞˋ-ㄍㄨㄥ\t派工".to_string()]);
    let _ = std::fs::remove_dir_all(&dir);
}

// ---------- tiny lexicons: the §2.2 rules one by one ----------

/// A tiny lexicon (every syllable a single-character pair unless the text says otherwise) with the real model.
fn tiny(text: &str) -> Engine {
    let s = shared();
    let lex = Arc::new(Lexicon::parse(text).unwrap());
    let capped = Arc::new(CappedLexicon::new(lex.clone(), "", &s.lm, None).unwrap());
    let mut e = Engine::with_lexicon(lex, L);
    e.set_lm(s.lm.clone(), capped);
    e.set_today(Some(DAY));
    e.set_learning(true);
    e
}
/// Syllables of T (each one character, two homophones apart from 的 and Ａ).
const T: &str = "ㄅㄢˇ 板 -1.0\nㄅㄢˇ 版 -1.5\nㄎㄨㄥˋ 控 -1.0\nㄎㄨㄥˋ 空 -1.5\nㄕㄨ 書 -1.0\nㄕㄨ 舒 -1.5\nㄏㄨㄚ 花 -1.0\nㄏㄨㄚ 嘩 -1.5\nㄉㄜ˙ 的 -1.0\nㄟ Ａ -1.0\nㄐㄧˇ 己 -1.0\nㄐㄧˇ 幾 -1.5\n";
/// Type `reading` on a T engine, re-pick the character at `at`, Enter; the new words learned. Sub-spans (§2.2 item 5)
/// are off here: these tests pin the whole-run rules of items 1–4; item 5 has its own tests (`boundary_*`).
fn learn_t(text: &str, reading: &str, at: usize) -> (Vec<String>, String) {
    let mut e = tiny(text);
    e.set_newword_subspans(false);
    let n = type_syls(&mut e, reading).preedit.chars().count();
    let shown = repick_char(&mut e, n, at);
    let c = e.key(k(KeyKind::Enter)).unwrap().commit;
    assert_eq!(c, shown);
    (new_words(&e), c)
}
fn chars(s: &str, r: std::ops::Range<usize>) -> String {
    s.chars().skip(r.start).take(r.len()).collect()
}

/// §2.2: a function character splits the run; only the part holding the re-picked character is learned (the other
/// part holds none). Without the split the run is 5 long and nothing is learned.
#[test]
fn function_characters_split_and_only_the_repicked_part_is_learned() {
    let (learned, c) = learn_t(T, "ㄅㄢˇ ㄎㄨㄥˋ ㄉㄜ˙ ㄕㄨ ㄏㄨㄚ", 1);
    assert_eq!(learned, vec![chars(&c, 0..2)], "{c}");
}

/// §2.2: 2..=4 syllables. A run of 5 or of 1 is not learned; 4 is.
#[test]
fn runs_of_two_to_four() {
    assert_eq!(learn_t(T, "ㄅㄢˇ ㄎㄨㄥˋ ㄕㄨ ㄏㄨㄚ ㄐㄧˇ", 2).0, Vec::<String>::new(), "5 long");
    let (learned, c) = learn_t(T, "ㄅㄢˇ ㄎㄨㄥˋ ㄕㄨ ㄏㄨㄚ", 2);
    assert_eq!(learned, vec![c], "4 long");
    assert_eq!(learn_t(T, "ㄉㄜ˙ ㄕㄨ ㄉㄜ˙", 1).0, Vec::<String>::new(), "1 long");
}

/// §2.2: punctuation and non-Han characters split runs.
#[test]
fn punctuation_and_non_han_split() {
    let mut e = tiny(T);
    type_syls(&mut e, "ㄅㄢˇ ㄕㄨ");
    e.key(Key::ch(',', MOD_SHIFT)).unwrap();
    let shown = type_syls(&mut e, "ㄎㄨㄥˋ").preedit;
    assert_eq!(shown.chars().nth(2), Some('，'));
    let after = repick_char(&mut e, 4, 3);
    e.key(k(KeyKind::Enter)).unwrap();
    assert!(new_words(&e).is_empty(), "，{} alone is one character: {after}", chars(&after, 3..4));
    let (learned, c) = learn_t(T, "ㄅㄢˇ ㄕㄨ ㄟ ㄎㄨㄥˋ", 1);
    assert_eq!(learned, vec![chars(&c, 0..2)], "Ａ splits: {c}");
}

/// §2.2: a word the lexicon has under that reading is not learned (here 版控, scored too low to show).
#[test]
fn a_lexicon_word_is_not_learned() {
    let text = format!("{T}ㄅㄢˇ-ㄎㄨㄥˋ 版控 -9.0\n");
    let mut e = tiny(&text);
    assert_ne!(type_syls(&mut e, "ㄅㄢˇ ㄎㄨㄥˋ").preedit, "版控");
    spell(&mut e, "版控");
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, "版控");
    assert!(new_words(&e).is_empty());
    // the same without the lexicon word is learned
    let mut e = tiny(T);
    type_syls(&mut e, "ㄅㄢˇ ㄎㄨㄥˋ");
    spell(&mut e, "版控");
    e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!(new_words(&e), vec!["版控"]);
}

/// §2.2: a run made only of NEWWORD_DIGITS is not learned.
#[test]
fn digits_are_not_learned() {
    let d = "ㄧ 一 -1.0\nㄧ 壹 -1.5\nㄦˋ 二 -1.0\nㄦˋ 貳 -1.5\nㄙㄢ 三 -1.0\nㄙㄢ 參 -1.5\nㄅㄢˇ 板 -1.0\nㄅㄢˇ 版 -1.5\n";
    let (learned, c) = learn_t(d, "ㄧ ㄦˋ ㄙㄢ", 1);
    assert!(learned.is_empty(), "{c}");
    let (learned, c) = learn_t(d, "ㄧ ㄦˋ ㄅㄢˇ", 1);
    assert_eq!(learned, vec![c], "a digit next to another character is learned");
}

// ---------- §2.2 item 3, revisions one and two: the shadow gate over the one lexicon ----------

/// The best file score (before capping and demotion) under `reading` in the lexicon the engine loaded.
fn lexicon_best(reading: &str) -> Option<f64> {
    let r: Vec<String> = reading.split(' ').map(String::from).collect();
    shared().lex.entries(&r).iter().map(|x| x.1).reduce(f64::max)
}
/// Type `reading` after `ctx` on a production engine, spell `target` by single-character re-picks, Enter; the new words.
fn spell_and_commit(ctx: &str, reading: &str, target: &str) -> Vec<String> {
    let mut e = engine();
    e.set_left_context(ctx);
    assert_ne!(type_syls(&mut e, reading).preedit, target);
    spell(&mut e, target);
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, target);
    new_words(&e)
}

/// Revision one: 為期 (-4.85) is above NEWWORD_SHADOW, so re-picking 期 to 祁 learns no 為祁 that would cover it.
#[test]
fn a_strong_lexicon_word_blocks_the_new_word() {
    assert!(lexicon_best("ㄨㄟˊ ㄑㄧˊ").is_some_and(|b| b > NEWWORD_SHADOW), "fixture: a strong word under the reading");
    assert_eq!(spell_and_commit("", "ㄨㄟˊ ㄑㄧˊ", "為祁"), Vec::<String>::new());
}

/// Revision one: 根音 (-7.47, a count-less base mode) is the only word under ㄍㄣ ㄧㄣ and below NEWWORD_SHADOW, so 根因 is
/// learned.
#[test]
fn a_weak_lexicon_word_does_not_block_the_new_word() {
    assert!(lexicon_best("ㄍㄣ ㄧㄣ").is_some_and(|b| b <= NEWWORD_SHADOW), "fixture: only weak words under the reading");
    assert_eq!(spell_and_commit("", "ㄍㄣ ㄧㄣ", "根因"), vec!["根因"]);
}

/// Revision two: the lexicon of the gate includes the names layer. 湯臣 (names-add.tsv, -5.17) is the only lexicon word
/// under ㄊㄤ ㄔㄣˊ, so re-picking 臣 to 陳 learns no 湯陳 that would cover the name.
#[test]
fn a_names_layer_word_blocks_the_new_word() {
    assert_eq!(spell_and_commit("", "ㄊㄤ ㄔㄣˊ", "湯陳"), Vec::<String>::new());
    let names = std::fs::read_to_string(root().join("data/lexicon").join(NAMES_FILE)).unwrap();
    assert!(names.lines().any(|l| l.starts_with("ㄊㄤ-ㄔㄣˊ\t湯臣\t")), "fixture: 湯臣 comes from the names layer");
    assert!(lexicon_best("ㄊㄤ ㄔㄣˊ").is_some_and(|b| b > NEWWORD_SHADOW), "fixture: the engine's lexicon has it");
}

/// The motivating report: at sentence start, re-picking 正彬 character by character (ㄓㄥˋ ㄅㄧㄣ, no lexicon word) learns
/// it as a new word, so the next ㄓㄥˋ ㄅㄧㄣ, at sentence start or after other text, shows 正彬. S4 alone never learned it:
/// the same re-picks without new-word B leave the next composition at 正賓.
#[test]
fn zhengbin_repicked_at_sentence_start_is_learned() {
    assert_eq!(lexicon_best(ZHENGBIN), None, "fixture: no lexicon word under the reading");
    for newword in [false, true] {
        let mut e = engine();
        e.set_newword(newword);
        e.set_left_context("");
        assert_eq!(type_syls(&mut e, ZHENGBIN).preedit, "正賓");
        spell(&mut e, "正彬");
        assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, "正彬");
        for ctx in ["", "我們", "跟"] {
            e.set_left_context(ctx);
            let shown = type_syls(&mut e, ZHENGBIN).preedit;
            e.key(k(KeyKind::Esc)).unwrap();
            assert_eq!(shown, if newword { "正彬" } else { "正賓" }, "new-word B {newword}, after {ctx:?}");
        }
        assert_eq!(new_words(&e), if newword { vec!["正彬"] } else { vec![] });
    }
}

// ---------- §2.1 when ----------

fn t_composition(e: &mut Engine) {
    type_syls(e, "ㄅㄢˇ ㄎㄨㄥˋ");
}

#[test]
fn no_repick_learns_nothing() {
    let mut e = tiny(T);
    t_composition(&mut e);
    e.key(k(KeyKind::Enter)).unwrap();
    assert!(new_words(&e).is_empty());
}

#[test]
fn a_multi_character_pick_or_the_same_character_learns_nothing() {
    let mut e = tiny(&format!("{T}ㄎㄨㄥˋ-ㄕㄨ 空書 -9.0\n"));
    type_syls(&mut e, "ㄅㄢˇ ㄎㄨㄥˋ ㄕㄨ");
    pick(&mut e, 3, 3, "空書");
    e.key(k(KeyKind::Enter)).unwrap();
    assert!(new_words(&e).is_empty(), "a two-character pick");
    let mut e = tiny(T);
    let shown = type_syls(&mut e, "ㄅㄢˇ ㄎㄨㄥˋ").preedit;
    pick(&mut e, 2, 1, &chars(&shown, 0..1));
    e.key(k(KeyKind::Enter)).unwrap();
    assert!(new_words(&e).is_empty(), "the character already shown");
}

#[test]
fn learning_off_at_the_pick_or_at_the_commit_learns_nothing() {
    let mut e = tiny(T);
    e.set_learning(false); // nothing composed yet: not a pause of this composition
    t_composition(&mut e);
    repick_char(&mut e, 2, 0);
    e.set_learning(true);
    e.key(k(KeyKind::Enter)).unwrap();
    assert!(new_words(&e).is_empty(), "off at the pick");
    let mut e = tiny(T);
    t_composition(&mut e);
    repick_char(&mut e, 2, 0);
    e.set_learning(false);
    e.key(k(KeyKind::Enter)).unwrap();
    assert!(new_words(&e).is_empty(), "off at the commit");
}

#[test]
fn reset_and_esc_learn_nothing() {
    for end in [0, 1, 2] {
        let mut e = tiny(T);
        t_composition(&mut e);
        repick_char(&mut e, 2, 0);
        match end {
            0 => drop(e.reset(ResetMode::Commit)),
            1 => drop(e.reset(ResetMode::Discard)),
            _ => drop(e.key(k(KeyKind::Esc)).unwrap()),
        }
        e.key(k(KeyKind::Enter)).ok();
        assert!(new_words(&e).is_empty(), "end {end}");
    }
}

/// §2.1: learning turned off and back on during the composition: a later re-pick next to the text typed meanwhile
/// learns no new word at this commit; the next composition learns again.
#[test]
fn a_pause_during_the_composition_blocks_new_words() {
    let mut e = tiny(T);
    type_syls(&mut e, "ㄅㄢˇ");
    e.set_learning(false);
    type_syls(&mut e, "ㄎㄨㄥˋ");
    e.set_learning(true);
    repick_char(&mut e, 2, 0);
    e.key(k(KeyKind::Enter)).unwrap();
    assert!(new_words(&e).is_empty());
    t_composition(&mut e);
    repick_char(&mut e, 2, 0);
    e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!(new_words(&e).len(), 1, "the flag went with the composition");
}

/// §2.1 (review of PR #196, finding 1): the shell sends set_learning before every key, so learning can be off for the
/// first key of a composition, when set_learning(false) still sees it empty. That key marks the composition too.
#[test]
fn a_key_handled_while_learning_is_off_marks_the_composition() {
    let mut e = tiny(T);
    let first = keys_of("ㄅㄢˇ");
    e.set_learning(false);
    e.key(first[0]).unwrap();
    e.set_learning(true);
    for key in &first[1..] {
        e.key(*key).unwrap();
    }
    type_syls(&mut e, "ㄎㄨㄥˋ");
    repick_char(&mut e, 2, 0);
    e.key(k(KeyKind::Enter)).unwrap();
    assert!(new_words(&e).is_empty(), "a '#' record from a composition begun with learning off");
}

/// §2.1 (finding 5): the mark goes when the composition empties by any path, here Backspace over everything, so the
/// next composition, typed with learning on, learns again.
#[test]
fn the_pause_goes_when_backspace_empties_the_composition() {
    let mut e = tiny(T);
    type_syls(&mut e, "ㄅㄢˇ");
    e.set_learning(false);
    type_syls(&mut e, "ㄎㄨㄥˋ");
    e.set_learning(true);
    let mut emptied = false;
    for _ in 0..10 {
        if e.key(k(KeyKind::Backspace)).unwrap().preedit.is_empty() {
            emptied = true;
            break;
        }
    }
    assert!(emptied);
    t_composition(&mut e);
    repick_char(&mut e, 2, 0);
    e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!(new_words(&e).len(), 1, "the pause outlived the composition Backspace emptied");
}

// ---------- §2.3 storage ----------

fn raw(dir: &Path) -> String {
    std::fs::read_to_string(dir.join(core::learn_store::FILE)).unwrap_or_default()
}

/// §2.3: a learned new word is one "#" line; a fresh engine reads it back and decodes with it.
#[test]
fn store_writes_the_new_word_line_and_reopen_restores() {
    let dir = tmp_dir("store");
    let mut e = engine();
    e.learning_open(&dir).unwrap();
    teach_paigong(&mut e);
    let text = raw(&dir);
    assert!(text.lines().any(|l| l == "#\tㄆㄞˋ-ㄍㄨㄥ\t派工\t1\t20000"), "the # line");
    let mut f = engine();
    f.learning_open(&dir).unwrap();
    assert!(new_record(&f, "派工").is_some());
    f.set_left_context("加入");
    assert_eq!(type_syls(&mut f, PAIGONG).preedit, "派工");
    let _ = std::fs::remove_dir_all(&dir);
}

// ---------- §2.4 decoding and the candidate window ----------

/// §2.4: after 派工 is learned, ㄆㄞˋ ㄍㄨㄥ decodes as 派工 in other contexts and inside other sentences, and the
/// candidate window lists it ahead of the lexicon's two-syllable words.
#[test]
fn a_learned_word_wins_in_any_context_and_leads_its_length_in_the_window() {
    let mut e = engine();
    e.set_left_context("要進");
    assert_eq!(type_syls(&mut e, PAIGONG).preedit, "派公", "before");
    e.key(k(KeyKind::Esc)).unwrap();
    teach_paigong(&mut e);
    e.set_left_context("記得加入");
    assert_eq!(type_syls(&mut e, PAIGONG).preedit, "派工");
    e.key(k(KeyKind::Esc)).unwrap();
    e.set_left_context("");
    e.set_prediction(false).unwrap(); // predict-down B.4: the window's lexicon part is the subject; the row's items come first
    assert_eq!(type_syls(&mut e, &format!("ㄐㄧˋ ㄉㄜ˙ ㄐㄧㄚ ㄖㄨˋ {PAIGONG}")).preedit, "記得加入派工");
    let o = e.key(k(KeyKind::Space)).unwrap();
    assert_eq!(o.candidates[0], "派工", "first among the two-syllable candidates (none longer here)");
}

/// §2.4: choosing the new word from the window in a new context fixes it; the composition shows and commits it
/// without any error; no S4 or global record is made for it. ⌘⌫ on it while it is fixed unfixes the span and
/// decodes again, without an error.
#[test]
fn choosing_and_forgetting_a_fixed_new_word() {
    let mut e = engine();
    teach_paigong(&mut e);
    e.set_left_context("我們");
    assert_eq!(type_syls(&mut e, PAIGONG).preedit, "派工", "decoded with the record");
    e.key(k(KeyKind::Esc)).unwrap();
    // fix it from the window: after 我們 the record decides; pick it explicitly
    e.set_left_context("我們");
    type_syls(&mut e, PAIGONG);
    let o = pick(&mut e, 2, 2, "派工");
    assert_eq!(o.preedit, "派工");
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, "派工");
    assert!(e.learner().records().iter().filter(|r| r.word == "派工").all(|r| r.context == NEWWORD), "no S4 or global record");
    // fixed, then forgotten
    e.set_left_context("我們");
    type_syls(&mut e, PAIGONG);
    pick(&mut e, 2, 2, "派工");
    open_at(&mut e, 2, 2, "派工");
    let o = e.key(FORGET).unwrap();
    assert!(new_record(&e, "派工").is_none());
    assert_ne!(o.preedit, "派工", "decoded again without the record");
    e.key(k(KeyKind::Esc)).unwrap();
    let o = e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!(o.commit, "派公");
    assert!(new_words(&e).is_empty());
}

/// §2.4: a fixed new word whose record goes some other way (clear) is unfixed, not an error.
#[test]
fn a_fixed_new_word_without_a_record_is_unfixed() {
    let dir = tmp_dir("unfix");
    let mut e = engine();
    e.learning_open(&dir).unwrap();
    teach_paigong(&mut e);
    e.set_left_context("加入");
    type_syls(&mut e, PAIGONG);
    pick(&mut e, 2, 2, "派工");
    e.learning_clear().unwrap();
    let o = type_syls(&mut e, "ㄉㄜ˙"); // the next key decodes again
    assert!(o.preedit.starts_with("派公"), "unfixed and decoded without the record");
    let _ = std::fs::remove_dir_all(&dir);
}

/// §2.4: with only "#" records whose readings dev302 types under ACTIVE (so never enumerated) plus active ones on
/// readings it never types, every key's output (composition and prediction row) and every commit is the one of an
/// engine without any record: `has_reading`, `words_of` and the prediction row's gate all see the "#" rows.
#[test]
fn new_word_records_that_are_never_enumerated_change_nothing() {
    use core::learn_store::LearnStore;
    let rows = rows_of("dev302");
    let lex = &shared().lex;
    let mut records = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for (_, sent, syls) in &rows {
        let c: Vec<char> = sent.chars().collect();
        for l in 2..=4 {
            for a in 0..c.len().saturating_sub(l - 1) {
                let (r, w): (Vec<String>, String) = (syls[a..a + l].to_vec(), c[a..a + l].iter().collect());
                if !lex.entries(&r).iter().any(|(x, _)| *x == w) && w.chars().all(|x| ('\u{4e00}'..='\u{9fff}').contains(&x)) && seen.insert((r.clone(), w.clone())) {
                    records.push(Record { context: NEWWORD.into(), reading: r, word: w, weight: 0.3, day: DAY });
                }
            }
        }
    }
    let n_low = records.len();
    assert!(n_low > 100, "{n_low}");
    records.truncate(1999);
    records.push(Record { context: NEWWORD.into(), reading: vec!["ㄆㄞˋ".into(), "ㄍㄨㄥ".into()], word: "派工".into(), weight: 3.0, day: DAY });
    let dir = tmp_dir("never");
    let (store, _, _) = LearnStore::open(&dir).unwrap();
    store.save(&records).unwrap();
    let (mut a, mut b) = (engine(), engine());
    a.set_learning(false);
    b.set_learning(false);
    b.learning_open(&dir).unwrap();
    assert!(b.learner().records().iter().all(|r| r.context == NEWWORD) && b.learner().records().len() == records.len().min(2000));
    let mut diff = Vec::new();
    for (i, (ctx, _, syls)) in rows.iter().enumerate() {
        assert!(!syls.windows(2).any(|w| w == ["ㄆㄞˋ", "ㄍㄨㄥ"]), "dev302 types the active one");
        a.set_left_context(ctx);
        b.set_left_context(ctx);
        for key in syls.iter().flat_map(|s| keys_of(s)).chain([k(KeyKind::Enter)]) {
            if a.key(key).unwrap() != b.key(key).unwrap() {
                diff.push(i + 1);
                break;
            }
        }
        a.reset(ResetMode::Discard);
        b.reset(ResetMode::Discard);
    }
    assert!(diff.is_empty(), "rows differ: {diff:?}");
    let _ = std::fs::remove_dir_all(&dir);
}

// ---------- §2.5 used and re-picked away ----------

/// §2.5: a commit with the new word on the path counts once per local day (+1 after decay, one appended line);
/// a second use the same day writes nothing.
#[test]
fn a_use_counts_once_a_day() {
    let dir = tmp_dir("use");
    let mut e = engine();
    e.learning_open(&dir).unwrap();
    teach_paigong(&mut e);
    let lines = |d: &Path| raw(d).lines().count();
    let n0 = lines(&dir);
    for day in [DAY, DAY + 1, DAY + 1] {
        e.set_today(Some(day));
        e.set_left_context("加入");
        type_syls(&mut e, PAIGONG);
        assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, "派工");
    }
    let r = new_record(&e, "派工").unwrap();
    assert_eq!(r.day, DAY + 1);
    assert!((r.weight - (0.5f64.powf(1.0 / 14.0) + 1.0)).abs() < 1e-12, "decayed one day, plus one");
    // DAY + 1's first Enter is the day's full rewrite; the second Enter that day adds nothing either
    let text = raw(&dir);
    assert_eq!(text.lines().filter(|l| l.contains("派工")).count(), 1, "rewritten once on the new day, no second line");
    assert!(lines(&dir) <= n0, "no line added by the uses");
    // the same day, append path: a use of an already counted word appends nothing
    let before = raw(&dir);
    e.set_left_context("加入");
    type_syls(&mut e, PAIGONG);
    e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!(raw(&dir), before);
    let _ = std::fs::remove_dir_all(&dir);
}

/// §2.5: on the append path (a second commit the same day), a use the next day appends exactly one line.
#[test]
fn a_use_on_a_later_day_appends_one_line() {
    let dir = tmp_dir("use-append");
    let mut e = engine();
    e.learning_open(&dir).unwrap();
    e.set_today(Some(DAY - 1));
    teach_paigong(&mut e); // the full rewrite of DAY - 1
    e.set_today(Some(DAY));
    // a re-pick commit on DAY (a single character after two Han ones, so S4 learns it, rule 7): that day's full rewrite
    e.set_left_context("他們");
    type_syls(&mut e, "ㄒㄧㄣ");
    repick_char(&mut e, 1, 0);
    e.key(k(KeyKind::Enter)).unwrap();
    let before = raw(&dir).lines().count();
    e.set_left_context("加入");
    type_syls(&mut e, PAIGONG);
    e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!(raw(&dir).lines().count(), before + 1, "one appended line for the use");
    e.set_left_context("加入");
    type_syls(&mut e, PAIGONG);
    e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!(raw(&dir).lines().count(), before + 1, "nothing for a second use the same day");
    let _ = std::fs::remove_dir_all(&dir);
}

/// §2.5: a use counts only at a commit with learning on: off for the whole composition (no pause flag either,
/// since it was off before typing), the record does not change.
#[test]
fn a_use_with_learning_off_counts_nothing() {
    let mut e = engine();
    teach_paigong(&mut e);
    e.set_today(Some(DAY + 1));
    e.set_learning(false);
    e.set_left_context("加入");
    type_syls(&mut e, PAIGONG);
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, "派工");
    let r = new_record(&e, "派工").unwrap();
    assert_eq!((r.weight, r.day), (1.0, DAY));
}

/// §2.5: a re-pick over a new word on the path halves it.
#[test]
fn a_repick_over_a_new_word_halves_it() {
    let mut e = engine();
    teach_paigong(&mut e);
    e.set_left_context("加入");
    assert_eq!(type_syls(&mut e, PAIGONG).preedit, "派工");
    pick(&mut e, 2, 2, "公");
    e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!(new_record(&e, "派工").unwrap().weight, 0.5);
}

/// §2.5 (finding 2): choosing the character already shown over part of a new word changes no text (S4's pre == word),
/// so it re-picks nothing away: 派工 keeps its weight.
#[test]
fn choosing_the_shown_character_inside_a_new_word_does_not_halve_it() {
    let mut e = engine();
    teach_paigong(&mut e);
    e.set_left_context("加入");
    assert_eq!(type_syls(&mut e, PAIGONG).preedit, "派工");
    pick(&mut e, 2, 1, "派");
    e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!(new_record(&e, "派工").unwrap().weight, 1.0);
}

/// T plus a weak two-syllable lexicon word (板空 -9.0, under NEWWORD_SHADOW) under ㄅㄢˇ ㄎㄨㄥˋ, with 版控 learned as a
/// new word; a fresh ㄅㄢˇ ㄎㄨㄥˋ shows it.
fn banpkong() -> Engine {
    let mut e = tiny(&format!("{T}ㄅㄢˇ-ㄎㄨㄥˋ 板空 -9.0\n"));
    assert_ne!(type_syls(&mut e, "ㄅㄢˇ ㄎㄨㄥˋ").preedit, "版控");
    spell(&mut e, "版控");
    e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!(new_words(&e), vec!["版控"]);
    assert_eq!(type_syls(&mut e, "ㄅㄢˇ ㄎㄨㄥˋ").preedit, "版控");
    e
}

/// §2.5 (finding 3): picking the lexicon's 板空 over the new word 版控 displaces it; choosing 版控 back over the same
/// span takes that back, so the commit does not halve it. Picking 板空 alone does.
#[test]
fn choosing_a_new_word_back_over_its_span_does_not_halve_it() {
    let mut e = banpkong();
    pick(&mut e, 2, 2, "板空");
    assert_eq!(pick(&mut e, 2, 2, "版控").preedit, "版控");
    e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!(new_record(&e, "版控").unwrap().weight, 1.0, "chosen back");
    assert_eq!(type_syls(&mut e, "ㄅㄢˇ ㄎㄨㄥˋ").preedit, "版控");
    assert_eq!(pick(&mut e, 2, 2, "板空").preedit, "板空");
    e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!(new_record(&e, "版控").unwrap().weight, 0.5, "re-picked away");
}

/// §2.5 (finding 6): 板 over the 版 of 版控 displaces 版控; a later pick of 板空 over both syllables replaces that pick
/// (it overlaps, not the same span) and must hand on what it displaced: the commit halves 版控.
#[test]
fn a_pick_over_an_earlier_overlapping_pick_keeps_what_it_displaced() {
    let mut e = banpkong();
    assert_eq!(pick(&mut e, 2, 1, "板").preedit.chars().next(), Some('板'));
    assert_eq!(pick(&mut e, 2, 2, "板空").preedit, "板空");
    e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!(new_record(&e, "版控").unwrap().weight, 0.5);
}

/// §2.2 item 5 (finding 7): with variant B, a run that spells the same sub-span twice (版空版空 → 版空 at 0..2 and
/// 2..4) learns it once, at weight 1, not bumped twice by one commit.
#[test]
fn a_sub_span_spelled_twice_in_one_commit_is_learned_once() {
    let mut e = tiny(T);
    e.set_newword_subspans(true);
    let shown = type_syls(&mut e, "ㄅㄢˇ ㄎㄨㄥˋ ㄅㄢˇ ㄎㄨㄥˋ").preedit;
    assert_eq!(shown, "板控板空", "measured: spelling 版空版空 re-picks 版 in both halves");
    spell(&mut e, "版空版空");
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, "版空版空");
    assert!(new_words(&e).contains(&"版空版空".to_string()), "{:?}", new_words(&e));
    assert_eq!(new_record(&e, "版空").unwrap().weight, 1.0);
}

// ---------- §2.6 forgetting and clearing ----------

/// §2.6: ⌘⌫ on a new word removes its "#" line (full rewrite) and decoding goes back to the unlearned output; the
/// open window stops listing it.
#[test]
fn forgetting_a_new_word_removes_its_line() {
    let dir = tmp_dir("forget");
    let mut e = engine();
    e.learning_open(&dir).unwrap();
    teach_paigong(&mut e);
    e.set_left_context("加入");
    type_syls(&mut e, PAIGONG);
    open_at(&mut e, 2, 2, "派工");
    let o = e.key(FORGET).unwrap();
    assert_eq!(o.preedit, "派公");
    assert!(!o.candidates.is_empty(), "the window stays open");
    assert!(!o.candidates.iter().any(|c| c == "派工"), "a forgotten new word is no longer listed");
    assert!(!raw(&dir).contains("派工"));
    let _ = std::fs::remove_dir_all(&dir);
}

/// §2.6 (security review P2-1): re-pick 工 → ⌘⌫ on 派工 → Esc → Enter must not learn 派工 back. The word is below
/// ACTIVE here (30 days old), so the composition shows 派公 while the window still lists it.
#[test]
fn forget_then_enter_does_not_learn_the_word_back() {
    let dir = tmp_dir("forget-enter");
    let mut e = engine();
    e.learning_open(&dir).unwrap();
    teach_paigong(&mut e);
    e.set_today(Some(DAY + 30));
    e.set_left_context("加入");
    assert_eq!(type_syls(&mut e, PAIGONG).preedit, "派公", "under ACTIVE: not enumerated");
    pick(&mut e, 2, 2, "工");
    open_at(&mut e, 2, 2, "派工");
    e.key(FORGET).unwrap();
    e.key(k(KeyKind::Esc)).unwrap();
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, "派工");
    let mut f = engine();
    f.learning_open(&dir).unwrap();
    assert!(new_record(&f, "派工").is_none(), "learned back");
    assert!(!raw(&dir).contains("派工"));
    let _ = std::fs::remove_dir_all(&dir);
}

/// §2.6: clearing removes every "#" record and the file.
#[test]
fn clear_removes_new_words() {
    let dir = tmp_dir("clear");
    let mut e = engine();
    e.learning_open(&dir).unwrap();
    teach_paigong(&mut e);
    e.learning_clear().unwrap();
    assert!(new_words(&e).is_empty());
    assert!(!dir.join(core::learn_store::FILE).exists());
    let _ = std::fs::remove_dir_all(&dir);
}

// ---------- R2 marker ----------

/// PLAN §7 R2 (new-word B wording): re-pick one character in the middle of a marker string the lexicon has no word
/// for and commit; the file holds marker characters only inside the ≤ 4 run holding the re-picked one and the ≤ 2
/// characters before it. Typing the same marker again without a re-pick adds no line holding any of them.
#[test]
fn r2_marker_characters_on_disk() {
    r2_marker(false);
}
/// The same under variant B: sub-spans lie inside the run, so the bound is unchanged.
#[test]
fn r2_marker_characters_on_disk_with_subspans() {
    r2_marker(true);
}
fn r2_marker(subspans: bool) {
    let marker = "ㄐㄧㄚˇ 甲 -1.0\nㄐㄧㄚˇ 假 -1.5\nㄧˇ 乙 -1.0\nㄧˇ 以 -1.5\nㄉㄜ˙ 的 -1.0\nㄅㄧㄥˇ 丙 -1.0\nㄅㄧㄥˇ 餅 -1.5\nㄉㄧㄥ 丁 -1.0\nㄉㄧㄥ 叮 -1.5\nㄨˋ 戊 -1.0\nㄨˋ 物 -1.5\nㄌㄜ˙ 了 -1.0\nㄐㄧˇ 己 -1.0\nㄐㄧˇ 幾 -1.5\nㄍㄥ 庚 -1.0\nㄍㄥ 耕 -1.5\n";
    let reading = "ㄐㄧㄚˇ ㄧˇ ㄉㄜ˙ ㄅㄧㄥˇ ㄉㄧㄥ ㄨˋ ㄌㄜ˙ ㄐㄧˇ ㄍㄥ";
    let dir = tmp_dir(if subspans { "r2-b" } else { "r2" });
    let mut e = tiny(marker);
    e.set_newword_subspans(subspans);
    e.learning_open(&dir).unwrap();
    type_syls(&mut e, reading);
    let at = 4;
    repick_char(&mut e, 9, at);
    let c: Vec<char> = e.key(k(KeyKind::Enter)).unwrap().commit.chars().collect();
    assert_eq!(c.len(), 9);
    let distinct: std::collections::HashSet<char> = c.iter().copied().collect();
    assert_eq!(distinct.len(), 9, "marker characters are distinct");
    // the run holding the re-pick: 3..6 (的 at 2 and 了 at 6 split it); the context: 2..4
    let allowed: Vec<char> = c[2..6].to_vec();
    let text = raw(&dir);
    for line in text.lines().skip(1) {
        let f: Vec<&str> = line.split('\t').collect();
        assert_eq!(f.len(), 5);
        for ch in f[0].chars().chain(f[2].chars()) {
            assert!(!distinct.contains(&ch) || allowed.contains(&ch), "a marker character outside the allowed span is on disk");
        }
    }
    assert!(text.lines().any(|l| l.starts_with("#\t")), "the run was learned");
    if subspans {
        assert!(text.lines().filter(|l| l.starts_with("#\t")).count() >= 2, "variant B learned sub-spans too");
    }
    let lines_before: Vec<String> = text.lines().map(String::from).collect();
    type_syls(&mut e, reading);
    e.key(k(KeyKind::Enter)).unwrap();
    let after = raw(&dir);
    let added: Vec<&str> = after.lines().filter(|l| !lines_before.iter().any(|b| b == l)).collect();
    assert!(added.iter().all(|l| !l.chars().any(|ch| distinct.contains(&ch))), "a commit without a re-pick added a marker line");
    let _ = std::fs::remove_dir_all(&dir);
}

// ---------- the boundary case: 要進 and ㄆㄞˋ ㄍㄨㄥ in one composition ----------

/// Type 要進 + ㄆㄞˋ ㄍㄨㄥ in ONE composition, re-pick 派 if needed and 工, Enter; then type ㄆㄞˋ ㄍㄨㄥ after
/// 記得加入. Returns the new words and the second display.
fn boundary(subspans: bool) -> (Vec<String>, String) {
    let mut e = engine();
    e.set_newword_subspans(subspans);
    e.set_left_context("");
    let o = type_syls(&mut e, &format!("ㄧㄠˋ ㄐㄧㄣˋ {PAIGONG}"));
    assert_eq!(o.preedit, "要進派公");
    spell(&mut e, "要進派工");
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, "要進派工");
    let learned = new_words(&e);
    e.set_left_context("記得加入");
    (learned, type_syls(&mut e, PAIGONG).preedit)
}

/// Rule A (the approved contract): 要 is a function character and 進 is not, so the run is 進派工 and only that is
/// learned; ㄆㄞˋ ㄍㄨㄥ alone never meets it, so 記得加入 + ㄆㄞˋ ㄍㄨㄥ stays 派公 (the user's original report).
#[test]
fn boundary_rule_a_learns_only_the_whole_run() {
    let (learned, shown) = boundary(false);
    assert_eq!(learned, vec!["進派工"]);
    assert_eq!(shown, "派公");
}

/// Variant B: the run's sub-spans holding the re-pick whose reading has no lexicon word are learned too (派工 here),
/// so 記得加入 + ㄆㄞˋ ㄍㄨㄥ gives 派工.
#[test]
fn boundary_variant_b_learns_the_sub_span() {
    let (learned, shown) = boundary(true);
    assert!(learned.contains(&"派工".to_string()) && learned.contains(&"進派工".to_string()), "{learned:?}");
    assert_eq!(shown, "派工");
}

// ---------- §3.4 per-key time with 2,000 new words ----------

fn p95(mut t: Vec<std::time::Duration>) -> std::time::Duration {
    t.sort();
    t[t.len() * 95 / 100]
}

/// §3.4 / §5: per-key p95 over the dev302 replay with NEWWORD_CAP active new words on readings dev302 types, against
/// the same replay without records (rows interleaved, so load on the machine hits both alike). Stop condition: more
/// than 1 ms over.
#[test]
fn perf_two_thousand_new_words_per_key() {
    use core::learn::NEWWORD_CAP;
    use core::learn_store::LearnStore;
    use std::time::Instant;
    let rows = rows_of("dev302");
    let lex = &shared().lex;
    let mut records = Vec::new();
    let mut seen = std::collections::HashSet::new();
    'fill: for l in [2, 3, 4] {
        for (_, sent, syls) in &rows {
            let c: Vec<char> = sent.chars().collect();
            for a in 0..c.len().saturating_sub(l - 1) {
                let (r, w): (Vec<String>, String) = (syls[a..a + l].to_vec(), c[a..a + l].iter().collect());
                if !lex.entries(&r).iter().any(|(x, _)| *x == w) && w.chars().all(|x| ('\u{4e00}'..='\u{9fff}').contains(&x)) && seen.insert(w.clone()) {
                    records.push(Record { context: NEWWORD.into(), reading: r, word: w, weight: 1.0, day: DAY });
                    if records.len() == NEWWORD_CAP {
                        break 'fill;
                    }
                }
            }
        }
    }
    assert_eq!(records.len(), NEWWORD_CAP);
    let dir = tmp_dir("perf");
    let (store, _, _) = LearnStore::open(&dir).unwrap();
    store.save(&records).unwrap();
    let (mut a, mut b) = (engine(), engine());
    b.learning_open(&dir).unwrap();
    assert_eq!(new_words(&b).len(), NEWWORD_CAP);
    let (mut ta, mut tb) = (Vec::new(), Vec::new());
    for (ctx, _, syls) in &rows {
        for (e, t) in [(&mut a, &mut ta), (&mut b, &mut tb)] {
            e.set_left_context(ctx);
            for key in syls.iter().flat_map(|s| keys_of(s)) {
                let t0 = Instant::now();
                e.key(key).unwrap();
                t.push(t0.elapsed());
            }
            e.reset(ResetMode::Discard);
        }
    }
    let (pa, pb) = (p95(ta.clone()), p95(tb.clone()));
    println!("per-key p95: no records {pa:?}, {NEWWORD_CAP} new words {pb:?} ({} keys each)", ta.len());
    let _ = std::fs::remove_dir_all(&dir);
    #[cfg(not(debug_assertions))]
    assert!(pb < pa + std::time::Duration::from_millis(1), "p95 more than 1 ms over (stop condition §5)");
}


