//! V3 engine tests (docs/contracts/v3-engine.md section 7.1): the prediction row, its keys, selection, clearing, the
//! C-facing fields. Real lexicon and model (data/lm/bigram.sjlm); fails loudly without them, never skips.
use core::engine::*;
use core::learn::context_key;
use core::lm::{decode_segment, history, CappedLexicon, End, Lm, Profile};
use core::predict::{predict, reading_matches, reading_matches_in, units_of, Mode, Unit};
use core::Lexicon;
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};
use std::time::{Duration, Instant};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}
fn lm_path() -> PathBuf {
    let p = root().join("data/lm/bigram.sjlm");
    assert!(root().join("data/lm/classes.sjc").exists(), "data/lm/classes.sjc is missing: download it with `gh release download classes-v2 -R Nanako0129/shanjie -p classes.sjc -D data/lm` (or build it with tools/build_classes.py)");
    assert!(p.exists(), "data/lm/bigram.sjlm is missing: gh release download model-v4 -R Nanako0129/shanjie -p bigram.sjlm -D data/lm");
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
        let lm = Lm::load(&lm_path()).unwrap();
        let dir = root().join("data/lexicon");
        // `ACG_PACK` (only for `measure_start_range_acg`) is the word pack's acg-add.tsv; its directory is the packs dir.
        let pack = std::env::var("ACG_PACK").ok().map(PathBuf::from);
        let packs = pack.as_deref().map(|f| (f.parent().unwrap(), PACK_ACG));
        let (lex, pack_text) = load_lexicon_packs(&dir, packs).unwrap();
        let overlay = capping_overlay(&dir, &pack_text).unwrap();
        let capped = Arc::new(CappedLexicon::new(lex.clone(), &overlay, &lm, None).unwrap());
        Shared { lex, lm: Arc::new(lm), capped }
    })
}
const L: Layout = Layout::Standard;
const DAY: i64 = 20_000;

fn engine() -> Engine {
    let s = shared();
    let mut e = Engine::with_lexicon(s.lex.clone(), L);
    e.set_lm(s.lm.clone(), s.capped.clone());
    e.set_today(Some(DAY));
    e
}

// ---------- key helpers ----------

fn is_tone(c: char) -> bool {
    "ˊˇˋ˙".contains(c)
}
/// Key presses of a syllable text such as `ㄋㄧˇ`, `ㄏㄠˇ`, `ㄨㄛˇ` or `˙ㄅㄚ`; tone one ends with the space bar.
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
/// Presses without the closing tone key: a pending unit.
fn pend_keys(chars: &str) -> Vec<Key> {
    chars.chars().map(|c| Key::ch(L.key_of_symbol(c).unwrap(), 0)).collect()
}
fn send(e: &mut Engine, keys: Vec<Key>) -> Output {
    let mut last = None;
    for k in keys {
        last = Some(e.key(k).unwrap());
    }
    last.unwrap()
}
/// Types the syllables (each closed) and then the pending characters.
fn typ(e: &mut Engine, syls: &[&str], pending: &str) -> Output {
    let mut keys: Vec<Key> = syls.iter().flat_map(|s| syl_keys(s)).collect();
    keys.extend(pend_keys(pending));
    send(e, keys)
}
fn kind(k: KeyKind) -> Key {
    Key::new(k)
}
fn digit(n: usize) -> Key {
    Key::ch(char::from_digit(n as u32, 10).unwrap(), 0)
}
fn shift_tab() -> Key {
    Key { kind: KeyKind::Tab, ch: '\0', modifiers: MOD_SHIFT }
}
fn cmd_bs() -> Key {
    Key { kind: KeyKind::Backspace, ch: '\0', modifiers: MOD_COMMAND }
}
fn comma() -> Key {
    Key::ch(',', MOD_SHIFT) // ，
}

/// The row an output shows (the passive row has `selected == None`).
fn row(o: &Output) -> Vec<String> {
    o.candidates.clone()
}
/// Output fields of a state with a passive prediction row.
fn assert_passive(o: &Output) {
    assert!(!o.candidates.is_empty() && o.candidates.len() <= 9);
    assert_eq!((o.selected, o.columns, o.first, o.total as usize), (None, 0, 0, o.candidates.len()));
}
fn assert_no_row(o: &Output) {
    assert_eq!((o.candidates.len(), o.selected, o.columns, o.first, o.total), (0, None, 0, 0, 0));
}

// ---------- the independent oracle (contract section 1.2) ----------

#[derive(Clone)]
enum Tok {
    Syl(&'static str),
    Punct(&'static str),
}
use Tok::*;

struct Oracle {
    /// Final row: (word, reading, start).
    items: Vec<(String, Vec<String>, usize)>,
    /// Start positions in the order they were queried (long starts far to near, then the cursor start, then with `mid`
    /// 3 the positions inside words), and those skipped for punctuation.
    queried: Vec<usize>,
    skipped: Vec<usize>,
    /// Long-start items after step 2 (before the cap), and items that the filter of step 1 removed.
    long_total: usize,
    filtered: Vec<String>,
    /// L (contract section 10.2 step 4: the long starts' items far to near, duplicates removed) and the cursor start's.
    long: Vec<(String, Vec<String>, usize)>,
    cursor: Vec<(String, Vec<String>, usize)>,
    /// Section 11: the decoded words' starts used as long starts, and the positions inside words.
    word_starts: Vec<usize>,
    mid_starts: Vec<usize>,
}

/// Syllable keys for `predict`'s `units_of`: neutral tone goes last.
fn units_keys(syl: &str) -> String {
    let body: String = syl.chars().filter(|&c| c != '˙').collect();
    if syl.contains('˙') {
        format!("{body}˙")
    } else if syl.chars().any(is_tone) {
        body
    } else {
        format!("{body} ")
    }
}

/// Decode the tokens the way the engine's best path is made (no fixed words): each stretch between punctuation is a
/// sentence, the first starts from the left context's history word. Returns (word, syllable count, is punctuation).
fn ref_path(left: &str, toks: &[Tok]) -> Vec<(String, usize, bool)> {
    let s = shared();
    let lam = Profile::Chat.lambda();
    let lkey = context_key(left);
    let first_prev = history(if lkey == "^" { "" } else { &lkey }, &s.lm).to_string();
    let mut path = Vec::new();
    let mut seg: Vec<String> = Vec::new();
    let mut prev = first_prev;
    let flush = |seg: &mut Vec<String>, prev: &str, path: &mut Vec<(String, usize, bool)>| {
        if !seg.is_empty() {
            let best = decode_segment(&s.capped, seg, &s.lm, lam, prev, End::Eos, 64).unwrap();
            for (w, _, _) in &best[0].1 {
                path.push((w.to_string(), w.chars().count(), false));
            }
            seg.clear();
        }
    };
    for t in toks {
        match t {
            Syl(y) => seg.push(y.to_string()),
            Punct(p) => {
                flush(&mut seg, &prev, &mut path);
                path.push((p.to_string(), 1, true));
                prev = "<s>".to_string();
            }
        }
    }
    flush(&mut seg, &prev, &mut path);
    path
}

fn oracle(left: &str, toks: &[Tok], pending: &str) -> Oracle {
    oracle_with(left, toks, pending, PREDICT_BACK, 3)
}

/// `back`: the start of every decoded word that begins at most that many tokens before the cursor is a long start
/// too (the last two words always are). `mid`: positions inside words within `back`; 0 none, 1 merged with the word
/// starts far to near, 2 after all word starts (far to near), 3 after everything (far to near; section 11, what
/// `oracle` and the engine use). No fixed words: the oracle decodes free text only.
fn oracle_with(left: &str, toks: &[Tok], pending: &str, back: usize, mid: u8) -> Oracle {
    let s = shared();
    let idx = s.capped.predict_index(&s.lm);
    let lam = Profile::Chat.lambda();
    let n = toks.len();
    let path = ref_path(left, toks);
    // Token text: syllables show as one char, a punctuation mark as itself.
    let shown: Vec<String> = path.iter().flat_map(|(w, c, p)| if *p { vec![w.clone()] } else { w.chars().map(String::from).take(*c).collect() }).collect();
    assert_eq!(shown.len(), n);
    let is_punct = |i: usize| matches!(toks[i], Punct(_));
    let mut long_starts = Vec::new();
    let mut pos = n;
    for (i, (_, c, _)) in path.iter().rev().enumerate() {
        pos -= c;
        if i < 2 || pos + back >= n {
            long_starts.push(pos);
        }
    }
    long_starts.reverse();
    let extra: Vec<usize> = (n.saturating_sub(back)..n).filter(|p| !long_starts.contains(p)).collect();
    let (word_starts, mid_starts) = (long_starts.clone(), extra.clone());
    match mid {
        1 => {
            long_starts.extend(extra.iter().copied());
            long_starts.sort_unstable();
        }
        2 => long_starts.extend(extra.iter().copied()),
        _ => {}
    }
    let (mut skipped, mut queried, mut filtered) = (Vec::new(), Vec::new(), Vec::new());
    let mut query = |st: usize, skipped: &mut Vec<usize>, queried: &mut Vec<usize>| -> Vec<(String, Vec<String>, usize)> {
        if (st..n).any(is_punct) {
            skipped.push(st);
            return Vec::new();
        }
        queried.push(st);
        let mut keys: String = toks[st..n].iter().map(|t| if let Syl(y) = t { units_keys(y) } else { unreachable!() }).collect();
        keys.push_str(pending);
        let before: String = shown[..st].concat();
        let ck = context_key(&format!("{}{before}", context_key(left).replace('^', "")));
        let v = history(if ck == "^" { "" } else { &ck }, &s.lm);
        let here: String = shown[st..].concat();
        let units = units_of(&keys);
        predict(idx, &s.lm, lam, v, &units, Mode::P, 9)
            .into_iter()
            .filter_map(|(w, _, _, r)| {
                if w == here {
                    filtered.push(w);
                    None
                } else {
                    Some((w, r, st))
                }
            })
            .collect()
    };
    let mut long: Vec<(String, Vec<String>, usize)> = Vec::new();
    let mut seen_pos = Vec::new();
    for st in long_starts {
        if !seen_pos.contains(&st) {
            seen_pos.push(st);
            long.extend(query(st, &mut skipped, &mut queried));
        }
    }
    let mut seen = std::collections::HashSet::new();
    long.retain(|x| seen.insert(x.0.clone()));
    let long_total = long.len();
    let cursor_items = if pending.is_empty() { Vec::new() } else { query(n, &mut skipped, &mut queried) };
    let cursor = cursor_items.clone();
    let mut merged: Vec<_> = long.iter().take(3).cloned().collect();
    merged.extend(cursor_items);
    merged.extend(long.iter().skip(3).cloned());
    if mid == 3 {
        for st in extra {
            merged.extend(query(st, &mut skipped, &mut queried));
        }
    }
    let mut seen = std::collections::HashSet::new();
    merged.retain(|x| seen.insert(x.0.clone()));
    merged.truncate(9);
    Oracle { items: merged, queried, skipped, long_total, filtered, long, cursor, word_starts, mid_starts }
}

fn words(o: &Oracle) -> Vec<String> {
    o.items.iter().map(|i| i.0.clone()).collect()
}

/// Replays `toks` into a fresh engine (left context set), returns the engine and the last output.
fn build(left: &str, toks: &[Tok], pending: &str) -> (Engine, Output) {
    build_in(engine(), left, toks, pending)
}
fn build_in(mut e: Engine, left: &str, toks: &[Tok], pending: &str) -> (Engine, Output) {
    e.set_left_context(left);
    let mut keys = Vec::new();
    for t in toks {
        match t {
            Syl(y) => keys.extend(syl_keys(y)),
            Punct(_) => keys.push(comma()),
        }
    }
    keys.extend(pend_keys(pending));
    let o = send(&mut e, keys);
    (e, o)
}

// States (a)-(e) of contract section 7.1.
const A: (&str, &[Tok], &str) = ("我想喝一杯", &[], "ㄋ");
const B: (&str, &[Tok], &str) = ("我想", &[Syl("ㄏㄜ"), Syl("ㄧ"), Syl("ㄅㄟ")], "ㄋ");
const C: (&str, &[Tok], &str) = ("", &[Syl("ㄋㄧˇ"), Syl("ㄏㄠˇ"), Punct("，"), Syl("ㄨㄛˇ")], "ㄒ");
const D: (&str, &[Tok], &str) = ("我想喝一杯", &[Syl("ㄋㄞˇ"), Syl("ㄔㄚˊ")], "");
const E: (&str, &[Tok], &str) = ("", &[Syl("ㄇㄧㄥˊ"), Syl("ㄊㄧㄢ"), Syl("ㄗㄠˇ")], "ㄕ");
/// Two long starts and a history word from the left context on the first of them.
const F: (&str, &[Tok], &str) = ("明天早上我要先去銀行辦事", &[Syl("ㄏㄨㄟˊ"), Syl("ㄌㄞˊ"), Syl("ㄗㄞˋ")], "ㄕ");

fn check_state(name: &str, st: (&str, &[Tok], &str)) -> Oracle {
    let (left, toks, pending) = st;
    let want = oracle(left, toks, pending);
    let (_, o) = build(left, toks, pending);
    assert!(!want.items.is_empty(), "{name}: the oracle row is empty, the state proves nothing");
    assert_eq!(row(&o), words(&want), "{name}: the row differs from the independent computation");
    assert_passive(&o);
    want
}

#[test]
fn row_equals_the_independent_computation_in_states_a_to_e() {
    // (a) empty composition: only the cursor start, history from the left context.
    let a = check_state("a", A);
    assert_eq!(a.queried, vec![0]);
    // (b) words decoded before the cursor: long starts above 0, history from the display text.
    let b = check_state("b", B);
    assert!(b.queried.iter().any(|&s| s > 0 && s < 3), "b: a long start in the middle of the composition was queried: {:?}", b.queried);
    // (c) punctuation inside a long start's range: that start is skipped, the others are not.
    let c = check_state("c", C);
    assert!(!c.skipped.is_empty() && !c.queried.is_empty(), "c: skipped {:?} queried {:?}", c.skipped, c.queried);
    // (d) no pending unit, the decoded word is itself an item: it is filtered out.
    let d = check_state("d", D);
    assert!(d.filtered.iter().any(|w| w == "奶茶"), "d: the filter removed nothing: {:?}", d.filtered);
    assert!(!words(&d).contains(&"奶茶".to_string()));
    // (e) two word starts plus the cursor start with more than three long items: the cap works. Section 11: the
    // position inside the first word is queried too.
    for (name, st) in [("e", E), ("f", F)] {
        let o = check_state(name, st);
        assert!(o.long_total > 3 && o.word_starts.len() == 2, "{name}: long items {} word starts {:?}", o.long_total, o.word_starts);
        assert!(!o.mid_starts.is_empty() && o.mid_starts.iter().all(|s| o.queried.contains(s)), "{name}: {:?} {:?}", o.mid_starts, o.queried);
    }
}

#[test]
fn enter_commits_with_a_passive_row_and_selects_with_an_entered_one() {
    // enter-pending contract §2: with the row passive, Enter is rule 12a (commit what is shown); entered, rule 1d.
    let (mut e, o) = build(A.0, A.1, A.2);
    assert_passive(&o);
    let o = e.key(kind(KeyKind::Enter)).unwrap();
    assert!(o.handled && o.commit == "ㄋ" && o.preedit.is_empty(), "{}", o.commit);
    assert_no_row(&o);
    let (mut e, o) = build(A.0, A.1, A.2);
    let first = row(&o)[0].clone();
    e.key(kind(KeyKind::Tab)).unwrap();
    let o = e.key(kind(KeyKind::Enter)).unwrap();
    assert!(o.commit.is_empty() && o.preedit == first, "{} {}", o.preedit, first);
}

#[test]
fn after_a_cup_the_row_has_milk_tea() {
    let (_, o) = build("我想喝一杯", &[], "ㄋ");
    assert!(row(&o).contains(&"奶茶".to_string()), "{:?}", row(&o));
    assert_passive(&o);
}

#[test]
fn no_compatible_candidate_means_no_row() {
    let mut e = engine();
    let o = typ(&mut e, &[], "ㄩㄛ");
    assert_no_row(&o);
    assert_eq!(o.preedit, "ㄩㄛ");
}

#[test]
fn no_model_no_row() {
    let mut e = Engine::with_lexicon(shared().lex.clone(), L);
    let o = typ(&mut e, &[], "ㄋ");
    assert_no_row(&o);
    let o = send(&mut e, vec![kind(KeyKind::Tab)]);
    assert!(o.handled, "Tab with a pending unit and no row is eaten (rule 13), not entered");
    assert_no_row(&o);
}

// ---------- entering and leaving ----------

#[test]
fn tab_enters_and_shift_tab_leaves() {
    let (mut e, o) = build(A.0, A.1, A.2);
    let r = row(&o);
    assert!(r.len() >= 3);
    let o = e.key(kind(KeyKind::Tab)).unwrap();
    assert!(o.handled);
    assert_eq!((row(&o), o.selected, o.columns, o.first), (r.clone(), Some(0), 0, 0));
    let o = e.key(kind(KeyKind::Tab)).unwrap();
    assert_eq!(o.selected, Some(1));
    let o = e.key(Key::new(KeyKind::Right)).unwrap();
    assert_eq!(o.selected, Some(2));
    let o = e.key(Key::new(KeyKind::Left)).unwrap();
    assert_eq!(o.selected, Some(1));
    let o = e.key(shift_tab()).unwrap();
    assert!(o.handled);
    assert_eq!((row(&o), o.selected), (r.clone(), None), "Shift+Tab leaves, the row stays");
    // Tab again re-enters at the first item.
    assert_eq!(e.key(kind(KeyKind::Tab)).unwrap().selected, Some(0));
    // Left at the first item stays; Tab stops at the last.
    assert_eq!(e.key(kind(KeyKind::Left)).unwrap().selected, Some(0));
    let mut last = None;
    for _ in 0..20 {
        last = e.key(kind(KeyKind::Tab)).unwrap().selected;
    }
    assert_eq!(last, Some(r.len() - 1));
    // Esc leaves too, the row stays, nothing is committed.
    let o = e.key(kind(KeyKind::Esc)).unwrap();
    assert_eq!((row(&o), o.selected, o.commit.as_str()), (r, None, ""));
}

#[test]
fn another_key_leaves_and_is_handled_as_usual() {
    let (mut e, _) = build(A.0, A.1, A.2);
    e.key(kind(KeyKind::Tab)).unwrap();
    // A zhuyin key: leaves, then replaces the pending initial (rule 9) and recomputes.
    let o = e.key(Key::ch('v', 0)).unwrap(); // standard layout: ㄒ
    assert_eq!(o.selected, None);
    assert_eq!(o.preedit, "ㄒ");
    let want = oracle(A.0, &[], "ㄒ");
    assert_eq!(row(&o), words(&want));
    // Shift is punctuation: leaves, then the mark enters the composition and the row is gone.
    e.key(kind(KeyKind::Tab)).unwrap();
    let o = e.key(comma()).unwrap();
    assert_eq!((o.preedit.as_str(), o.selected), ("，", None));
    assert_no_row(&o);
}

#[test]
fn digits_select_and_out_of_range_digits_are_swallowed() {
    let (mut e, o) = build(A.0, A.1, A.2);
    let r = row(&o);
    assert_eq!(r.len(), 9, "the state has a full row");
    e.key(kind(KeyKind::Tab)).unwrap();
    let o = e.key(digit(2)).unwrap();
    assert_eq!(o.preedit, r[1], "digit 2 picks the second item");
    assert_no_row(&o);
    // A short row: digit 9 is beyond it and does nothing (still entered, same row).
    let (mut e, o) = build(A.0, A.1, "ㄌㄧㄚ");
    let r = row(&o);
    assert!((1..9).contains(&r.len()));
    e.key(kind(KeyKind::Tab)).unwrap();
    let o = e.key(digit(9)).unwrap();
    assert!(o.handled);
    assert_eq!((row(&o), o.selected, o.preedit.as_str()), (r, Some(0), "ㄌㄧㄚ"));
}

#[test]
fn enter_selects_the_highlighted_item() {
    let (mut e, o) = build(A.0, A.1, A.2);
    let r = row(&o);
    send(&mut e, vec![kind(KeyKind::Tab), kind(KeyKind::Right)]);
    let o = e.key(kind(KeyKind::Enter)).unwrap();
    assert_eq!((o.preedit, o.commit.as_str()), (r[1].clone(), ""));
}

#[test]
fn backspace_with_a_pending_unit_leaves_deletes_the_symbol_and_recomputes() {
    // ㄋ then ㄧ pending: Tab, Backspace removes ㄧ, the row is the one for ㄋ again.
    let (mut e, _) = build(A.0, A.1, "ㄋㄧ");
    let o = e.key(kind(KeyKind::Tab)).unwrap();
    assert_eq!(o.selected, Some(0));
    let o = e.key(kind(KeyKind::Backspace)).unwrap();
    assert_eq!((o.preedit.as_str(), o.selected), ("ㄋ", None));
    assert_eq!(row(&o), words(&oracle(A.0, &[], "ㄋ")));
    assert_passive(&o);
}

#[test]
fn backspace_with_only_whole_syllables_leaves_deletes_the_syllable_and_clears_the_row() {
    let mut e = engine();
    e.set_left_context("我想");
    let o = typ(&mut e, &["ㄋㄧˇ"], "");
    assert_passive(&o);
    let o = e.key(kind(KeyKind::Tab)).unwrap();
    assert_eq!(o.selected, Some(0));
    let o = e.key(kind(KeyKind::Backspace)).unwrap();
    assert_eq!(o.preedit, "");
    assert_no_row(&o);
}

#[test]
fn cmd_backspace_is_swallowed_while_entered_and_cmd_c_passes_through() {
    let (mut e, o) = build(A.0, A.1, A.2);
    let r = row(&o);
    e.key(kind(KeyKind::Tab)).unwrap();
    e.key(kind(KeyKind::Right)).unwrap();
    let o = e.key(cmd_bs()).unwrap();
    assert!(o.handled);
    assert_eq!((row(&o), o.selected, o.preedit.as_str()), (r.clone(), Some(1), "ㄋ"));
    let o = e.key(Key::ch('c', MOD_COMMAND)).unwrap();
    assert!(!o.handled);
    assert_eq!((row(&o), o.selected, o.preedit.as_str()), (r, Some(1), "ㄋ"));
}

// ---------- selection ----------

#[test]
fn selection_replaces_the_readings_moves_the_cursor_and_fixes_the_word() {
    // Pending-only start: ㄋ -> 奶茶 (two syllables): the pending unit is dropped, the reading goes in.
    let (mut e, o) = build(A.0, A.1, A.2);
    let r = row(&o);
    let i = r.iter().position(|w| w == "奶茶").unwrap();
    let o = e.pick(i).unwrap().unwrap();
    assert_eq!((o.preedit.as_str(), o.cursor_utf16), ("奶茶", 2));
    assert_no_row(&o);
    // The cursor is behind the word: one more syllable goes to its right.
    let o = send(&mut e, syl_keys("ㄅㄚ"));
    assert!(o.preedit.starts_with("奶茶"), "the fixed word stays: {}", o.preedit);
    assert_eq!(o.cursor_utf16, 3);

    // Long start: fix a one-character word over ㄗㄠˇ through the candidate window, type ㄕ, then pick 早上, which starts at
    // that syllable: the fixed word is overlapped and removed, the reading ㄗㄠˇ ㄕㄤˋ replaces the syllable and the pending unit.
    let mut e = engine();
    typ(&mut e, &["ㄇㄧㄥˊ", "ㄊㄧㄢ", "ㄗㄠˇ"], "");
    let mut o = e.key(kind(KeyKind::Space)).unwrap();
    for _ in 0..30 {
        if o.candidates[o.selected.unwrap()].chars().count() == 1 {
            break;
        }
        o = e.key(kind(KeyKind::Right)).unwrap();
    }
    let fixed = o.candidates[o.selected.unwrap()].clone();
    assert_eq!(fixed.chars().count(), 1);
    let o = e.key(kind(KeyKind::Enter)).unwrap();
    assert_eq!(o.preedit, format!("明天{fixed}"));
    let o = typ(&mut e, &[], "ㄕ");
    let i = row(&o).iter().position(|w| w == "早上").expect("早上 in the row");
    let o = e.pick(i).unwrap().unwrap();
    assert_eq!((o.preedit.as_str(), o.cursor_utf16), ("明天早上", 4));
    assert_no_row(&o);
    // The word is fixed: a further syllable does not change it.
    let o = send(&mut e, syl_keys("ㄗㄞˋ"));
    assert!(o.preedit.starts_with("明天早上"), "{}", o.preedit);
}

#[test]
fn a_prediction_pick_is_learned_like_a_repick() {
    let s = shared();
    let mut e = Engine::with_lexicon(s.lex.clone(), L);
    e.set_lm(s.lm.clone(), s.capped.clone());
    e.set_today(Some(DAY));
    e.set_learning(true);
    e.set_left_context("我想喝一杯");
    let o = typ(&mut e, &[], "ㄋ");
    let i = row(&o).iter().position(|w| w == "奶茶").unwrap();
    send(&mut e, vec![kind(KeyKind::Tab)]);
    for _ in 0..i {
        e.key(kind(KeyKind::Right)).unwrap();
    }
    let o = e.key(kind(KeyKind::Enter)).unwrap();
    assert_eq!(o.preedit, "奶茶");
    assert!(e.learner().records().is_empty(), "nothing is learned before the commit");
    let o = e.key(kind(KeyKind::Enter)).unwrap();
    assert_eq!(o.commit, "奶茶");
    let r = e.learner().records();
    assert_eq!(r.len(), 1, "a prediction pick is recorded");
    assert_eq!((r[0].context.as_str(), r[0].reading.join("-"), r[0].word.as_str(), r[0].weight), ("一杯", "ㄋㄞˇ-ㄔㄚˊ".to_string(), "奶茶", 1.0));

    // Re-picking the same span in the candidate window is learned as before.
    e.set_left_context("我想喝一杯");
    typ(&mut e, &["ㄋㄞˇ", "ㄔㄚˊ"], "");
    send(&mut e, vec![kind(KeyKind::Space)]);
    let o = e.key(kind(KeyKind::Right)).unwrap();
    let second = o.candidates[o.selected.unwrap()].clone();
    e.key(kind(KeyKind::Enter)).unwrap();
    let o = e.key(kind(KeyKind::Enter)).unwrap();
    assert_ne!(o.commit, "", "something was committed ({second})");
    assert!(e.learner().records().iter().any(|r| r.word == second), "a candidate-window re-pick is learned");
}

#[test]
fn pick_selects_from_the_passive_row_and_fails_without_any_row() {
    let (mut e, o) = build(A.0, A.1, A.2);
    let r = row(&o);
    assert!(e.pick(r.len()).unwrap().is_none(), "outside the row");
    let o = e.pick(1).unwrap().expect("click on the not-entered row selects at once");
    assert_eq!(o.preedit, r[1]);
    assert!(e.pick(0).unwrap().is_none(), "no row and no candidate window");
    // Entered row: same.
    let (mut e, o) = build(A.0, A.1, A.2);
    let r = row(&o);
    e.key(kind(KeyKind::Tab)).unwrap();
    assert_eq!(e.pick(2).unwrap().unwrap().preedit, r[2]);
}

// ---------- clearing ----------

/// "我想" before the cursor, ㄋㄧˇ typed, ㄏ pending: a state with a row.
fn with_row() -> Engine {
    let (e, o) = build("我想", &[Syl("ㄋㄧˇ")], "ㄏ");
    assert_passive(&o);
    e
}

#[test]
fn the_row_is_cleared_by_other_state_changes() {
    // cursor move (rule 17; with a pending unit Left does nothing and the row stays)
    let (mut e, o) = build("我想", &[Syl("ㄋㄧˇ"), Syl("ㄏㄠˇ")], "");
    assert_passive(&o);
    assert_no_row(&e.key(kind(KeyKind::Left)).unwrap());
    // punctuation
    assert_no_row(&with_row().key(comma()).unwrap());
    // Esc drops the pending unit (rule 12)
    assert_no_row(&with_row().key(kind(KeyKind::Esc)).unwrap());
    // the candidate window: the row is gone while it is open and does not come back after it closes
    let mut e = with_row();
    send(&mut e, vec![kind(KeyKind::Esc), kind(KeyKind::Space)]); // Esc: no row; Space opens the window
    let o = e.key(kind(KeyKind::Esc)).unwrap();
    assert_no_row(&o);
    // Space with the pending unit is not a tone key of a known syllable... use a state with a row and open the window
    let (mut e, o) = build("我想", &[Syl("ㄋㄧˇ"), Syl("ㄏㄠˇ")], "");
    assert_passive(&o);
    let o = e.key(kind(KeyKind::Space)).unwrap();
    assert!(o.selected == Some(0) && o.candidates.len() > 1, "the candidate window is open and is not the row");
    let o = e.key(kind(KeyKind::Esc)).unwrap();
    assert_no_row(&o);
    // Enter commits
    let (mut e, _) = build("我想", &[Syl("ㄋㄧˇ"), Syl("ㄏㄠˇ")], "");
    let o = e.key(kind(KeyKind::Enter)).unwrap();
    assert_eq!(o.commit, "你好");
    assert_no_row(&o);
    // delete a syllable (rule 18)
    let (mut e, _) = build("我想", &[Syl("ㄋㄧˇ"), Syl("ㄏㄠˇ")], "");
    assert_no_row(&e.key(kind(KeyKind::Backspace)).unwrap());
    // reset
    let mut e = with_row();
    assert_no_row(&e.reset(ResetMode::Discard));
    // set_profile and set_demote snapshots
    assert_no_row(&with_row().set_profile(Profile::Formal).unwrap());
    assert_no_row(&with_row().set_demote(false).unwrap());
    // 40 syllables auto-commit
    let mut e = engine();
    let mut last = None;
    for _ in 0..40 {
        last = Some(typ(&mut e, &["ㄋㄧˇ"], ""));
    }
    let o = last.unwrap();
    assert!(!o.commit.is_empty(), "40 syllables commit");
    assert_no_row(&o);
}

#[test]
fn keys_that_do_nothing_keep_the_row() {
    let mut e = with_row();
    let r = row(&e.key(kind(KeyKind::Up)).unwrap());
    assert!(!r.is_empty());
    // a rule 1 pass-through keeps it as well
    let o = e.key(Key::ch('c', MOD_COMMAND)).unwrap();
    assert!(!o.handled);
    assert_eq!(row(&o), r);
}

#[test]
fn a_syllable_the_lexicon_lacks_leaves_the_row_alone() {
    let mut e = engine();
    e.set_left_context("我想");
    let o = typ(&mut e, &[], "ㄋ");
    let r = row(&o);
    // tone key after ㄋ alone: "ㄋˇ" is not a syllable; rule 10 returns early and the row stays
    let o = e.key(Key::ch('3', 0)).unwrap();
    assert_eq!((o.preedit.as_str(), row(&o)), ("ㄋ", r));
}

#[test]
fn the_row_is_empty_when_the_cursor_is_not_at_the_end() {
    // ㄋㄧˇ ㄏㄠˇ, cursor between them, then a new initial: the cursor start is not the end of the composition.
    let mut e = engine();
    e.set_left_context("我想");
    typ(&mut e, &["ㄋㄧˇ", "ㄏㄠˇ"], "");
    e.key(kind(KeyKind::Left)).unwrap();
    let o = e.key(Key::ch('s', 0)).unwrap(); // ㄋ
    assert_eq!(o.preedit, "你ㄋ好");
    assert_no_row(&o);
}

#[test]
fn right_and_end_at_the_end_keep_the_row() {
    let (mut e, o) = build("我想", &[Syl("ㄋㄧˇ")], "");
    let r = row(&o);
    assert_passive(&o);
    for k in [KeyKind::Right, KeyKind::End] {
        let o = e.key(kind(k)).unwrap();
        assert!(o.handled);
        assert_eq!((row(&o), o.selected), (r.clone(), None), "{}: nothing moved, the row stays", k as u32);
    }
    // Left does move the cursor and clears it.
    assert_no_row(&e.key(kind(KeyKind::Left)).unwrap());
}

/// The production path: `Engine::new` (demotion table loaded) and `load_lm` (index built eagerly).
#[test]
fn production_path_shows_milk_tea_and_equals_the_oracle() {
    let mut e = Engine::new(&root().join("data/lexicon"), L).unwrap();
    e.load_lm(&lm_path()).unwrap();
    e.set_left_context(A.0);
    let o = typ(&mut e, &[], "ㄋ");
    assert!(row(&o).contains(&"奶茶".to_string()), "{:?}", row(&o));
    assert_passive(&o);
    assert_eq!(row(&o), words(&oracle(A.0, A.1, A.2)));
    // and a state with long starts, through the same engine
    e.reset(ResetMode::Discard);
    e.set_left_context(F.0);
    let o = typ(&mut e, &["ㄏㄨㄟˊ", "ㄌㄞˊ", "ㄗㄞˋ"], "ㄕ");
    assert_eq!(row(&o), words(&oracle(F.0, F.1, F.2)));
}

// ---------- timing (contract section 7.4; `cargo test --release -- --ignored --nocapture timing`) ----------

fn rss_kb() -> u64 {
    let out = std::process::Command::new("ps").args(["-o", "rss=", "-p", &std::process::id().to_string()]).output().unwrap();
    String::from_utf8(out.stdout).unwrap().trim().parse().unwrap()
}

/// Every key of every typing76 sample (chat profile, with its left context), through `Engine::key`.
#[test]
#[ignore]
fn timing_typing76_keys() {
    time_typing76(None);
}

/// The same with the abbreviation composer on (section 12.4 item 6).
#[test]
#[ignore]
fn timing_typing76_keys_abbreviation() {
    time_typing76_in(None, true);
}

/// Real picks from the typing76 rows: for each sample, two short sessions (after the first key of the first
/// syllable, and after the first key of the second) pick the row's second item, then commit, so the store holds
/// records that match the situations the timed pass visits and the promotion and sorting run on them.
fn teach_typing76_picks(dir: &Path) -> usize {
    let text = std::fs::read_to_string(root().join("eval/dev/user-typing.txt")).unwrap();
    let mut e = engine();
    e.set_learning(true);
    e.learning_open(dir).unwrap();
    let mut checks: Vec<(String, String, String)> = Vec::new();
    for line in text.lines().filter(|l| !l.is_empty()) {
        let f: Vec<&str> = line.split('|').collect();
        let syls: Vec<&str> = f[2].split(' ').collect();
        for upto in 0..syls.len().min(2) {
            e.set_left_context(f[0]);
            let mut keys: Vec<Key> = syls[..upto].iter().flat_map(|y| syl_keys(y)).collect();
            keys.extend(syl_keys(syls[upto]).into_iter().take(1));
            let o = send(&mut e, keys);
            if o.candidates.len() > 1 {
                e.key(kind(KeyKind::Tab)).unwrap();
                e.key(kind(KeyKind::Right)).unwrap();
                e.key(kind(KeyKind::Enter)).unwrap();
                e.key(kind(KeyKind::Enter)).unwrap();
                // Not every pick is read back (a later pick under the same key can outrank it); one is checked below.
                if upto == 0 && checks.len() < 5 {
                    checks.push((f[0].to_string(), syls[0].to_string(), o.candidates[1].clone()));
                }
            } else {
                e.reset(ResetMode::Discard);
            }
        }
    }
    // The store is read back: in the situation of the first keys of several samples the taught item is now first.
    let mut first = 0;
    for (left, syl, word) in &checks {
        e.set_left_context(left);
        let o = send(&mut e, syl_keys(syl).into_iter().take(1).collect());
        first += (o.candidates.first() == Some(word)) as usize;
        e.reset(ResetMode::Discard);
    }
    println!("read back: {first} of {} checked picks are first", checks.len());
    assert!(first > 0, "the taught picks must show up in the rows the timed pass visits");
    e.learner().records().len()
}

/// Taught picks only (the store the timed rows really hit).
#[test]
#[ignore]
fn timing_typing76_keys_with_taught_picks() {
    let dir = tmp_dir("timing-taught");
    println!("taught records: {}", teach_typing76_picks(&dir));
    time_typing76(Some(&dir));
    let _ = std::fs::remove_dir_all(&dir);
}

/// Taught picks plus synthetic records up to the capacity: matching records are in long buckets.
#[test]
#[ignore]
fn timing_typing76_keys_with_taught_picks_and_50k() {
    let dir = tmp_dir("timing-taught50k");
    let taught = teach_typing76_picks(&dir);
    let (store, mut records, _) = LearnStore::open(&dir).unwrap();
    records.extend(synthetic_records(core::learn::CAPACITY - taught));
    store.save(&records).unwrap();
    println!("taught records: {taught}, total {}", records.len());
    time_typing76(Some(&dir));
    let _ = std::fs::remove_dir_all(&dir);
}

/// Taught picks plus synthetic records up to the capacity, with the abbreviation composer on.
#[test]
#[ignore]
fn timing_typing76_keys_with_taught_picks_and_50k_abbreviation() {
    let dir = tmp_dir("timing-taught50k-abbr");
    let taught = teach_typing76_picks(&dir);
    let (store, mut records, _) = LearnStore::open(&dir).unwrap();
    records.extend(synthetic_records(core::learn::CAPACITY - taught));
    store.save(&records).unwrap();
    println!("taught records: {taught}, total {}", records.len());
    time_typing76_in(Some(&dir), true);
    let _ = std::fs::remove_dir_all(&dir);
}

fn synthetic_records(count: usize) -> Vec<Record> {
    let text = std::fs::read_to_string(root().join("eval/dev/user-typing.txt")).unwrap();
    let mut readings: Vec<Vec<String>> = Vec::new();
    for line in text.lines().filter(|l| !l.is_empty()) {
        let syls: Vec<String> = line.split('|').nth(2).unwrap().split(' ').map(String::from).collect();
        for n in 1..=syls.len().min(4) {
            for st in 0..=(syls.len() - n) {
                if !readings.contains(&syls[st..st + n].to_vec()) {
                    readings.push(syls[st..st + n].to_vec());
                }
            }
        }
    }
    let keys: Vec<String> = "我你他想要去吃喝一杯這那有是不了的們".chars().flat_map(|a| "我你他想要去吃喝一杯這那".chars().map(move |b| format!("{a}{b}"))).collect();
    println!("synthetic: {count} records on {} readings, {} keys", readings.len(), keys.len());
    (0..count).map(|i| rec(&keys[i % keys.len()], &readings[i % readings.len()], &format!("測{i}"))).collect()
}

/// A learning file of 50,000 synthetic records (the capacity) on the readings the samples type: prefixes of the
/// samples' syllables under varied two-character keys, so every lookup scans a long bucket and none matches an item.
#[test]
#[ignore]
fn timing_typing76_keys_with_50k_records() {
    let dir = tmp_dir("timing");
    seed(&dir, &synthetic_records(core::learn::CAPACITY));
    time_typing76(Some(&dir));
    let _ = std::fs::remove_dir_all(&dir);
}

fn time_typing76(dir: Option<&Path>) {
    time_typing76_in(dir, false);
}

/// Section 12.4 item 6: the keys of the abbreviation variant, fully determined. The reading is cut into groups of two
/// syllables (the last group one when the count is odd); each group's target is the sentence's characters at those
/// positions. Per group: the first zhuyin key of each syllable, then, if the target is in the row, Tab and its digit,
/// else Esc and the group's full syllables. The sentence ends with Enter.
fn abbreviation_keys(e: &mut Engine, sent: &str, reading: &str, mut each: impl FnMut(&mut Engine, Key) -> Output) {
    let syls: Vec<&str> = reading.split(' ').collect();
    let chars: Vec<char> = sent.chars().collect();
    assert_eq!(chars.len(), syls.len());
    for (g, group) in syls.chunks(2).enumerate() {
        let target: String = chars[g * 2..g * 2 + group.len()].iter().collect();
        let mut o = None;
        for y in group {
            // The first zhuyin key; a neutral tone written in front (`˙ㄅㄚ`) is a tone key, not a symbol.
            let k = syl_keys(y).into_iter().find(|k| L.symbol_of(k.ch).is_some()).unwrap();
            o = Some(each(e, k));
        }
        let row = o.unwrap().candidates;
        match row.iter().position(|w| *w == target) {
            Some(i) => {
                each(e, kind(KeyKind::Tab));
                each(e, digit(i + 1));
            }
            None => {
                each(e, kind(KeyKind::Esc));
                group.iter().flat_map(|y| syl_keys(y)).for_each(|k| drop(each(e, k)));
            }
        }
    }
    each(e, kind(KeyKind::Enter));
}

fn time_typing76_in(dir: Option<&Path>, abbr: bool) {
    let s = shared();
    let rss0 = rss_kb();
    let t = Instant::now();
    s.capped.predict_index(&s.lm);
    println!("index build {:?}; rss {} -> {} KiB", t.elapsed(), rss0, rss_kb());
    let text = std::fs::read_to_string(root().join("eval/dev/user-typing.txt")).unwrap();
    let mut times: Vec<Duration> = Vec::new();
    let mut slow: Vec<(Duration, usize, usize)> = Vec::new();
    let mut rows = 0;
    for line in text.lines().filter(|l| !l.is_empty()) {
        let f: Vec<&str> = line.split('|').collect();
        let (left, reading) = (f[0], f[2]);
        let mut e = engine();
        if let Some(d) = dir {
            e.set_learning(true);
            e.learning_open(d).unwrap();
        }
        e.set_left_context(left);
        let mut ki = 0;
        let mut timed = |e: &mut Engine, k: Key| {
            let t = Instant::now();
            let o = e.key(k).unwrap();
            let d = t.elapsed();
            times.push(d);
            slow.push((d, rows, ki));
            ki += 1;
            o
        };
        if abbr {
            e.set_abbreviation(true).unwrap();
            abbreviation_keys(&mut e, f[1], reading, &mut timed);
        } else {
            let mut keys: Vec<Key> = reading.split(' ').flat_map(syl_keys).collect();
            keys.push(kind(KeyKind::Enter));
            keys.into_iter().for_each(|k| drop(timed(&mut e, k)));
        }
        rows += 1;
    }
    times.sort();
    let ms = |d: Duration| d.as_secs_f64() * 1000.0;
    slow.sort();
    for (d, r, k) in slow.iter().rev().take(3) {
        println!("slowest: {:.3} ms at row {r} key {k}", ms(*d));
    }
    println!("keys over 16 ms: {}", times.iter().filter(|d| **d > Duration::from_millis(16)).count());
    println!(
        "typing76{}{}: rows {rows} keys {} p50 {:.3} ms p95 {:.3} ms max {:.3} ms",
        if dir.is_some() { " + store" } else { "" },
        if abbr { " + abbreviation" } else { "" },
        times.len(),
        ms(times[times.len() / 2]),
        ms(times[times.len() * 95 / 100]),
        ms(times[times.len() - 1])
    );
    println!("rss after: {} KiB", rss_kb());
    assert!(times[times.len() * 95 / 100] < Duration::from_millis(16), "p95 over 16 ms");
}

// ---------- amendment one (contract section 10): learning from prediction picks, reordering, switch ----------

use core::learn::Record;
use core::learn_store::LearnStore;

fn tmp_dir(tag: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("shanjie-predict-{}-{tag}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    d
}
fn rec(context: &str, reading: &[String], word: &str) -> Record {
    Record { context: context.to_string(), reading: reading.to_vec(), word: word.to_string(), weight: 1.0, day: DAY }
}
/// Writes a learning file holding `records` (a temporary directory only).
fn seed(dir: &Path, records: &[Record]) {
    let (store, _, _) = LearnStore::open(dir).unwrap();
    store.save(records).unwrap();
}
/// A learning engine; with `dir` it reads that directory's file and writes into it.
fn learner_engine(profile: Profile, dir: Option<&Path>) -> Engine {
    let mut e = engine();
    e.set_profile(profile).unwrap();
    e.set_learning(true);
    if let Some(d) = dir {
        e.learning_open(d).unwrap();
    }
    e
}
/// Enters the row and moves the selection to `word`.
fn enter_on(e: &mut Engine, word: &str) {
    let o = e.key(kind(KeyKind::Tab)).unwrap();
    let i = row(&o).iter().position(|w| w == word).unwrap_or_else(|| panic!("{word} is not in {:?}", row(&o)));
    for _ in 0..i {
        e.key(kind(KeyKind::Right)).unwrap();
    }
}
/// Tab to the item, Enter: selects `word` from the row.
fn pick_word(e: &mut Engine, word: &str) -> Output {
    enter_on(e, word);
    e.key(kind(KeyKind::Enter)).unwrap()
}
/// The row for `left` + `pending` in a fresh engine with an empty learner.
fn fresh_row(profile: Profile, left: &str, pending: &str) -> Vec<String> {
    let mut e = engine();
    e.set_profile(profile).unwrap();
    e.set_left_context(left);
    row(&typ(&mut e, &[], pending))
}
fn milk_reading() -> Vec<String> {
    vec!["ㄋㄧㄡˊ".to_string(), "ㄋㄞˇ".to_string()]
}
/// Pick `word` from the row for `left` + `pending` and commit it.
fn teach(e: &mut Engine, left: &str, pending: &str, word: &str) {
    e.set_left_context(left);
    typ(e, &[], pending);
    pick_word(e, word);
    assert_eq!(e.key(kind(KeyKind::Enter)).unwrap().commit, word);
}

#[test]
fn t1_the_written_milk_case() {
    let mut e = learner_engine(Profile::Formal, None);
    e.set_left_context("我想喝一杯");
    let base = row(&typ(&mut e, &[], "ㄋ"));
    assert_eq!(&base[..2], &["奶茶", "牛奶"], "precondition: the written row is [奶茶, 牛奶, ...]");
    send(&mut e, vec![kind(KeyKind::Tab), kind(KeyKind::Right)]);
    let o = e.key(kind(KeyKind::Enter)).unwrap();
    assert_eq!(o.preedit, "牛奶");
    let o = e.key(kind(KeyKind::Enter)).unwrap();
    assert_eq!(o.commit, "牛奶");
    let r = e.learner().records();
    assert_eq!(r.len(), 1);
    assert_eq!((r[0].context.as_str(), r[0].reading.clone(), r[0].word.as_str(), r[0].weight), ("一杯", milk_reading(), "牛奶", 1.0));

    e.set_left_context("我想喝一杯");
    let again = row(&typ(&mut e, &[], "ㄋ"));
    let mut want = vec!["牛奶".to_string()];
    want.extend(base.iter().filter(|w| *w != "牛奶").cloned());
    assert_eq!(again, want, "the learned item first, the rest in the model order");
    assert!(again.len() <= 9);
    assert_eq!(again.iter().collect::<std::collections::HashSet<_>>().len(), again.len(), "no duplicates");
}

#[test]
fn t2_gates_record_nothing() {
    let sets = |e: &mut Engine| {
        e.set_left_context("我想喝一杯");
        typ(e, &[], "ㄋ");
    };
    // learning off when picked, on again at the commit
    let mut e = learner_engine(Profile::Formal, None);
    e.set_learning(false);
    sets(&mut e);
    pick_word(&mut e, "牛奶");
    e.set_learning(true);
    assert_eq!(e.key(kind(KeyKind::Enter)).unwrap().commit, "牛奶");
    assert_eq!(e.learner().records().len(), 0, "off at the pick");
    // on at the pick, off at the commit
    let mut e = learner_engine(Profile::Formal, None);
    sets(&mut e);
    pick_word(&mut e, "牛奶");
    e.set_learning(false);
    assert_eq!(e.key(kind(KeyKind::Enter)).unwrap().commit, "牛奶");
    assert_eq!(e.learner().records().len(), 0, "off at the commit");
    // Esc
    let mut e = learner_engine(Profile::Formal, None);
    sets(&mut e);
    pick_word(&mut e, "牛奶");
    let o = e.key(kind(KeyKind::Esc)).unwrap();
    assert_eq!(o.commit, "");
    assert_eq!(e.learner().records().len(), 0, "Esc");
    // reset, both modes
    for mode in [ResetMode::Commit, ResetMode::Discard] {
        let mut e = learner_engine(Profile::Formal, None);
        sets(&mut e);
        pick_word(&mut e, "牛奶");
        e.reset(mode);
        assert_eq!(e.learner().records().len(), 0, "reset");
    }
    // the control: the same steps with the gates open record one
    let mut e = learner_engine(Profile::Formal, None);
    sets(&mut e);
    pick_word(&mut e, "牛奶");
    e.key(kind(KeyKind::Enter)).unwrap();
    assert_eq!(e.learner().records().len(), 1);
}

#[test]
fn t3_a_single_character_needs_a_han_context() {
    // No Han character before: the key is "^", a single character is not recorded.
    let empty = fresh_row(Profile::Chat, "", "ㄋ");
    assert!(empty.iter().any(|w| w == "你"), "{empty:?}");
    let mut e = learner_engine(Profile::Chat, None);
    teach(&mut e, "", "ㄋ", "你");
    assert_eq!(e.learner().records().len(), 0);
    // After 我想 it is: 念 comes first there, and not after 他想 (the last-character level is barred for one character).
    let base = fresh_row(Profile::Chat, "我想", "ㄋ");
    assert!(base.iter().position(|w| w == "念").unwrap() > 0, "precondition: 念 is not first by itself: {base:?}");
    let other = fresh_row(Profile::Chat, "他想", "ㄋ");
    let mut e = learner_engine(Profile::Chat, None);
    teach(&mut e, "我想", "ㄋ", "念");
    assert_eq!(e.learner().records().len(), 1);
    e.set_left_context("我想");
    assert_eq!(row(&typ(&mut e, &[], "ㄋ"))[0], "念");
    e.reset(ResetMode::Discard);
    e.set_left_context("他想");
    assert_eq!(row(&typ(&mut e, &[], "ㄋ")), other, "another key is untouched");
}

/// Index of `word` in `predict(..., 27)` for the situation "after `left`, `pending` typed", chat profile.
fn reach(left: &str, pending: &str, word: &str) -> Option<usize> {
    let s = shared();
    let key = context_key(left);
    predict(s.capped.predict_index(&s.lm), &s.lm, Profile::Chat.lambda(), history(if key == "^" { "" } else { &key }, &s.lm), &units_of(pending), Mode::P, 27)
        .iter()
        .position(|x| x.0 == word)
}

#[test]
fn t4_another_context_is_untouched() {
    // 牛奶 taught after 喝熱. After 想買 neither character matches (no exact, no last-character level), yet 牛奶 is
    // reachable there (index 14 of 27), so a lookup that ignored the key would move it.
    let i = reach("我想買", "ㄋ", "牛奶").expect("reachable");
    assert!(i >= 9, "precondition: 牛奶 is outside the first slice's nine at index {i}");
    let other = fresh_row(Profile::Chat, "我想買", "ㄋ");
    let (mut e, dir) = seeded("t4", &[rec("喝熱", &milk_reading(), "牛奶")]);
    e.set_left_context("我想買");
    assert_eq!(row(&typ(&mut e, &[], "ㄋ")), other);
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn t4b_a_context_sharing_the_last_character_is_promoted() {
    // 喝熱 taught; after 杯熱 only the last character matches (the last-character level of s4-learning section 1.1).
    let i = reach("一杯熱", "ㄋ", "牛奶").expect("reachable");
    assert!(i >= 9, "precondition: 牛奶 is outside the first slice's nine at index {i}");
    let base = fresh_row(Profile::Chat, "一杯熱", "ㄋ");
    let (mut e, dir) = seeded("t4b", &[rec("喝熱", &milk_reading(), "牛奶")]);
    e.set_left_context("一杯熱");
    let got = row(&typ(&mut e, &[], "ㄋ"));
    assert_eq!(got[0], "牛奶");
    assert_eq!(without(&got, "牛奶"), base[..8].to_vec());
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn t5_a_global_record_does_not_move_the_row() {
    // 能夠 is in the row after 這杯, 學校 and (at position 7) 請問你, a third key with no last character in common.
    let third = fresh_row(Profile::Chat, "請問你", "ㄋ");
    assert_eq!(third.iter().position(|w| w == "能夠"), Some(7), "precondition: {third:?}");
    let mut e = learner_engine(Profile::Chat, None);
    teach(&mut e, "我想喝這杯", "ㄋ", "能夠");
    teach(&mut e, "我想去學校", "ㄋ", "能夠");
    assert!(e.learner().records().iter().any(|r| r.context == "" && r.word == "能夠"), "precondition: the two keys made a global record");
    e.set_left_context("請問你");
    assert_eq!(row(&typ(&mut e, &[], "ㄋ")), third);
}

#[test]
fn t6_the_order_survives_a_restart() {
    let dir = tmp_dir("t6");
    let mut e = learner_engine(Profile::Formal, Some(&dir));
    teach(&mut e, "我想喝一杯", "ㄋ", "牛奶");
    drop(e);
    let mut e2 = learner_engine(Profile::Formal, Some(&dir));
    assert_eq!(e2.learner().records().len(), 1);
    e2.set_left_context("我想喝一杯");
    assert_eq!(row(&typ(&mut e2, &[], "ㄋ"))[0], "牛奶");
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn t7_cmd_backspace_on_the_entered_row_forgets_the_item() {
    let dir = tmp_dir("t7");
    let base = fresh_row(Profile::Formal, "我想喝一杯", "ㄋ");
    let mut e = learner_engine(Profile::Formal, Some(&dir));
    teach(&mut e, "我想喝一杯", "ㄋ", "牛奶");
    assert_eq!(e.learner().records().len(), 1);
    e.set_learning(false); // forgetting works while learning is paused
    e.set_left_context("我想喝一杯");
    let o = typ(&mut e, &[], "ㄋ");
    assert_eq!(row(&o)[0], "牛奶");
    e.key(kind(KeyKind::Tab)).unwrap();
    let o = e.key(cmd_bs()).unwrap();
    assert!(o.handled);
    assert_eq!(e.learner().records().len(), 0);
    assert_eq!(row(&o), base, "the row is back in its original order");
    assert_eq!(o.selected, Some(base.iter().position(|w| w == "牛奶").unwrap()), "still entered, on the same word");
    drop(e);
    let e2 = learner_engine(Profile::Formal, Some(&dir));
    assert_eq!(e2.learner().records().len(), 0, "the file was rewritten");
    let _ = std::fs::remove_dir_all(&dir);

    // A pending learn of the same word and reading is dropped: pick 牛奶, type a comma and ㄋㄧㄡ (a sentence start,
    // where 牛奶 is in the row again), forget it there; the commit then records nothing.
    let mut e = learner_engine(Profile::Formal, None);
    e.set_left_context("我想喝一杯");
    typ(&mut e, &[], "ㄋ");
    pick_word(&mut e, "牛奶");
    send(&mut e, vec![comma()]);
    let o = send(&mut e, pend_keys("ㄋㄧㄡ"));
    assert!(row(&o).iter().any(|w| w == "牛奶"), "precondition: {:?}", row(&o));
    enter_on(&mut e, "牛奶");
    let o = e.key(cmd_bs()).unwrap();
    assert!(o.handled && o.selected.is_some());
    e.key(kind(KeyKind::Esc)).unwrap(); // leave the row
    e.key(kind(KeyKind::Esc)).unwrap(); // drop the pending ㄋㄧㄡ
    let o = e.key(kind(KeyKind::Enter)).unwrap();
    assert_eq!(o.commit, "牛奶，");
    assert_eq!(e.learner().records().len(), 0, "the pending learn of 牛奶 was dropped");
}

#[test]
fn t8_the_switch() {
    let mut e = engine();
    e.set_left_context(A.0);
    let o = e.set_prediction(false).unwrap();
    assert!(o.handled && o.commit.is_empty());
    // rules 14, 9, 10, 14 and 11 leave no row
    assert_no_row(&send(&mut e, pend_keys("ㄋ"))); // 14
    assert_no_row(&send(&mut e, pend_keys("ㄧ"))); // 9
    assert_no_row(&send(&mut e, vec![Key::ch(L.key_of_tone('ˇ').unwrap(), 0)])); // 10: ㄋㄧˇ
    let o = send(&mut e, pend_keys("ㄏ")); // 14
    assert_no_row(&o);
    let shown = o.preedit.clone();
    let o = send(&mut e, pend_keys("ㄠ")); // 9
    assert_no_row(&o);
    assert_no_row(&send(&mut e, vec![kind(KeyKind::Backspace)])); // 11
    // Tab with a pending unit and no row is eaten (rule 13), nothing entered
    let o = e.key(kind(KeyKind::Tab)).unwrap();
    assert!(o.handled);
    assert_eq!((o.selected, o.preedit.as_str()), (None, shown.as_str()));
    // on again with a pending unit: the snapshot carries the row, equal to the independent computation
    let o = e.set_prediction(true).unwrap();
    assert!(o.handled && o.commit.is_empty());
    assert_passive(&o);
    assert_eq!(row(&o), words(&oracle(A.0, &[Syl("ㄋㄧˇ")], "ㄏ")));
    // entered, then off: the selection goes with the row, and Tab is rule 13 again
    assert_eq!(e.key(kind(KeyKind::Tab)).unwrap().selected, Some(0));
    let o = e.set_prediction(false).unwrap();
    assert_no_row(&o);
    let o = e.key(kind(KeyKind::Tab)).unwrap();
    assert!(o.handled && o.selected.is_none() && o.preedit == shown);
}

// ---------- reordering by what was learned (section 10.2), with records seeded through a file ----------

/// Context key of the word start `st` of `toks`: the left context plus the decoded text before it.
fn start_key(left: &str, toks: &[Tok], st: usize) -> String {
    let before: String = ref_path(left, toks).iter().flat_map(|(w, c, p)| if *p { vec![w.clone()] } else { w.chars().map(String::from).take(*c).collect() }).take(st).collect::<Vec<_>>().concat();
    context_key(&format!("{}{before}", context_key(left).replace('^', "")))
}
/// `predict` at the cursor start (all syllables of `toks` before the cursor are not part of it): the pending unit only.
fn raw_at_cursor(left: &str, toks: &[Tok], pending: &str, limit: usize) -> Vec<(String, Vec<String>)> {
    let s = shared();
    let key = start_key(left, toks, toks.len());
    let v = history(if key == "^" { "" } else { &key }, &s.lm);
    predict(s.capped.predict_index(&s.lm), &s.lm, Profile::Chat.lambda(), v, &units_of(pending), Mode::P, limit)
        .into_iter()
        .map(|(w, _, _, r)| (w, r))
        .collect()
}
/// The first-slice row for the state (an empty learner).
fn slice_one(st: (&str, &[Tok], &str)) -> Vec<String> {
    words(&oracle(st.0, st.1, st.2))
}
/// A chat engine reading `records` from a fresh directory; returns the engine and the directory to remove.
fn seeded(tag: &str, records: &[Record]) -> (Engine, PathBuf) {
    let dir = tmp_dir(tag);
    seed(&dir, records);
    (learner_engine(Profile::Chat, Some(&dir)), dir)
}
fn row_of(e: Engine, st: (&str, &[Tok], &str)) -> Vec<String> {
    row(&build_in(e, st.0, st.1, st.2).1)
}
fn without(r: &[String], w: &str) -> Vec<String> {
    r.iter().filter(|x| *x != w).cloned().collect()
}

#[test]
fn t9_slice_one_states_hold_with_unrelated_records() {
    // Records that match nothing in these rows leave every state of section 7.1 as the first slice had it.
    let unrelated = rec("我想", &["ㄇㄧㄥˊ".to_string(), "ㄊㄧㄢ".to_string()], "明天");
    for (name, st) in [("a", A), ("b", B), ("c", C), ("d", D), ("e", E), ("f", F)] {
        let (e, dir) = seeded(&format!("t9{name}"), &[unrelated.clone()]);
        assert_eq!(row_of(e, st), slice_one(st), "state {name}");
        let _ = std::fs::remove_dir_all(&dir);
    }
}

#[test]
fn t10_a_learned_item_beats_the_long_start_cap_and_the_cursor_start() {
    let want = oracle(F.0, F.1, F.2);
    let base = words(&want);
    assert!(want.long.len() > 5 && want.cursor.len() > 5, "precondition: long {} cursor {}", want.long.len(), want.cursor.len());
    let long_words: Vec<&String> = want.long.iter().map(|x| &x.0).collect();
    // a cursor-start item that is not also a long-start item
    let c = want.cursor.iter().skip(4).find(|x| !long_words.contains(&&x.0)).expect("a cursor item outside L").clone();
    let (e, dir) = seeded("t10c", &[rec(&start_key(F.0, F.1, c.2), &c.1, &c.0)]);
    let got = row_of(e, F);
    assert_eq!(got[0], c.0, "{got:?}");
    assert_eq!(without(&got, &c.0), without(&base, &c.0)[..got.len() - 1].to_vec(), "the rest keeps the first-slice order");
    assert_eq!(got.iter().collect::<std::collections::HashSet<_>>().len(), got.len(), "no duplicates");
    let _ = std::fs::remove_dir_all(&dir);
    // a long-start item at position 4 of L, beyond the cap of 3
    let x = want.long[4].clone();
    let (e, dir) = seeded("t10l", &[rec(&start_key(F.0, F.1, x.2), &x.1, &x.0)]);
    let got = row_of(e, F);
    assert_eq!(got[0], x.0, "{got:?}");
    assert_eq!(got.iter().collect::<std::collections::HashSet<_>>().len(), got.len());
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn t11_a_word_ranked_10_to_27_comes_first_once_taught() {
    let all = raw_at_cursor(A.0, A.1, "ㄋ", 27);
    let base = slice_one(A);
    let (word, reading) = all[11].clone();
    assert!(all.len() > 11 && !base.contains(&word), "precondition: rank 12 of {} is outside the slice-one row", all.len());
    let (e, dir) = seeded("t11", &[rec(&start_key(A.0, A.1, 0), &reading, &word)]);
    let got = row_of(e, A);
    let mut want = vec![word.clone()];
    want.extend(base.iter().take(8).cloned());
    assert_eq!(got, want);
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn t12_records_that_match_nothing_leave_a_short_row_short() {
    // 我想 + ㄋㄧˇ typed: 你 is the decoded word and is filtered out, so the first slice shows fewer than 9.
    let st: (&str, &[Tok], &str) = ("我想", &[Syl("ㄋㄧˇ")], "");
    let base = slice_one(st);
    let s = shared();
    let key = context_key("我想");
    let at_start = predict(s.capped.predict_index(&s.lm), &s.lm, Profile::Chat.lambda(), history(&key, &s.lm), &units_of(&units_keys("ㄋㄧˇ")), Mode::P, 27);
    assert!(base.len() < 9 && at_start.len() > 9, "precondition: row {} and predict(27) {}", base.len(), at_start.len());
    // The record's reading is compatible with the typed ㄋㄧˇ (so the start asks 27), its word is none of the items.
    let (e, dir) = seeded("t12", &[rec("我想", &["ㄋㄧˇ".to_string(), "ㄇㄣˊ".to_string()], "你門")]);
    assert_eq!(row_of(e, st), base);
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn t13_a_learned_item_deep_in_the_merge_still_comes_first() {
    let want = oracle(F.0, F.1, F.2);
    let all = raw_at_cursor(F.0, F.1, F.2, 27);
    let long_words: Vec<&String> = want.long.iter().map(|x| &x.0).collect();
    let k = (9..all.len()).find(|&k| !long_words.contains(&&all[k].0)).expect("a cursor item of raw index >= 9");
    let (word, reading) = all[k].clone();
    // The position after step 5: L[0..3], then the cursor start's kept items (index < 9, or the taught word).
    let mut step5: Vec<String> = want.long.iter().take(3).map(|x| x.0.clone()).collect();
    for (i, (w, _)) in all.iter().enumerate() {
        if (i < 9 || *w == word) && !step5.contains(w) {
            step5.push(w.clone());
        }
    }
    let pos = step5.iter().position(|w| *w == word).unwrap();
    assert!(pos >= 9, "precondition: the word is at position {pos} of the merge");
    let (e, dir) = seeded("t13", &[rec(&start_key(F.0, F.1, F.1.len()), &reading, &word)]);
    let got = row_of(e, F);
    assert_eq!(got[0], word, "{got:?}");
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn t14_a_learned_item_among_the_first_three_keeps_the_others_in_place() {
    let want = oracle(F.0, F.1, F.2);
    let base = words(&want);
    assert!(want.long.len() >= 4 && !want.cursor.is_empty() && base.len() == 9, "precondition");
    let x = want.long[1].clone();
    assert!(base.contains(&x.0));
    let (e, dir) = seeded("t14", &[rec(&start_key(F.0, F.1, x.2), &x.1, &x.0)]);
    let got = row_of(e, F);
    assert_eq!(got[0], x.0);
    assert_eq!(without(&got, &x.0), without(&base, &x.0), "without the learned item the row is the first slice's");
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn t15_a_word_found_from_two_starts_is_listed_once() {
    // ㄋㄧㄡˊ typed, ㄋ pending: 牛奶 comes from the long start (ㄋㄧㄡˊ + ㄋ) and from the cursor start (ㄋ) alike. Taught
    // under the cursor start's key it is first, and the long start's copy (weight 0, in L[0..3]) must not be listed again.
    let st: (&str, &[Tok], &str) = ("我想喝一杯", &[Syl("ㄋㄧㄡˊ")], "ㄋ");
    let want = oracle(st.0, st.1, st.2);
    assert!(want.long.iter().take(3).any(|x| x.0 == "牛奶") && want.cursor.iter().any(|x| x.0 == "牛奶"), "precondition");
    let (_, reading) = raw_at_cursor(st.0, st.1, st.2, 27).into_iter().find(|x| x.0 == "牛奶").unwrap();
    let (e, dir) = seeded("t15", &[rec(&start_key(st.0, st.1, 1), &reading, "牛奶")]);
    let got = row_of(e, st);
    assert_eq!(got[0], "牛奶");
    assert_eq!(got.iter().filter(|w| *w == "牛奶").count(), 1, "{got:?}");
    assert_eq!(got.iter().collect::<std::collections::HashSet<_>>().len(), got.len(), "{got:?}");
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn t16_a_prediction_pick_is_recorded_under_the_key_of_the_pick() {
    // ㄧˇ ㄅㄟˋ decode to 以備; picking 時 for the pending ㄕ re-decodes the free syllables to 已被. The record must carry
    // the key the row was built with (以備), not the one the display has at the commit (已被).
    let mut e = learner_engine(Profile::Chat, None);
    typ(&mut e, &["ㄧˇ", "ㄅㄟˋ"], "ㄕ");
    let o = pick_word(&mut e, "時");
    assert!(o.preedit.starts_with("已被"), "precondition: the pick changed the text before it: {}", o.preedit);
    assert_eq!(e.key(kind(KeyKind::Enter)).unwrap().commit, "已被時");
    let r = e.learner().records();
    assert_eq!((r.len(), r[0].context.as_str(), r[0].word.as_str()), (1, "以備", "時"));
    let o = typ(&mut e, &["ㄧˇ", "ㄅㄟˋ"], "ㄕ");
    assert_eq!(row(&o)[0], "時", "the taught item is first in the same situation: {:?}", row(&o));
}

#[test]
fn t16b_the_users_own_edit_before_the_pick_moves_the_record_to_the_commit_key() {
    // As in T16 the row is built under 以備. The user then changes the character before 時 in the candidate window
    // (倍 for ㄅㄟˋ): the text before 時 is the user's own choice now, so the record goes under the key at the commit.
    let mut e = learner_engine(Profile::Chat, None);
    typ(&mut e, &["ㄧˇ", "ㄅㄟˋ"], "ㄕ");
    pick_word(&mut e, "時");
    e.key(kind(KeyKind::Left)).unwrap();
    let mut o = e.key(kind(KeyKind::Space)).unwrap();
    for _ in 0..60 {
        if o.candidates[o.selected.unwrap()] == "倍" {
            break;
        }
        o = e.key(kind(KeyKind::Right)).unwrap();
    }
    assert_eq!(o.candidates[o.selected.unwrap()], "倍", "precondition: 倍 is in the window");
    e.key(kind(KeyKind::Enter)).unwrap();
    e.key(kind(KeyKind::End)).unwrap();
    let commit = e.key(kind(KeyKind::Enter)).unwrap().commit;
    assert!(commit.ends_with("倍時"), "{commit}");
    let before: String = commit.chars().rev().skip(1).take(2).collect::<Vec<_>>().into_iter().rev().collect();
    assert_ne!(before, "以備", "precondition: the user's edit changed the text before 時");
    let r = e.learner().records();
    let pred = r.iter().find(|x| x.word == "時").expect("the prediction pick is learned");
    assert_eq!(pred.context, before, "records: {:?}", r.iter().map(|x| (&x.context, &x.word)).collect::<Vec<_>>());
}

/// The two characters before the last character of `commit` (the context a record for that last word must carry).
fn key_before_last(commit: &str) -> String {
    let c: Vec<char> = commit.chars().collect();
    c[c.len().saturating_sub(3)..c.len() - 1].iter().collect()
}

#[test]
fn t16c_inserting_or_removing_a_syllable_before_the_pick_moves_the_record_to_the_commit_key() {
    for insert in [true, false] {
        let mut e = learner_engine(Profile::Chat, None);
        typ(&mut e, &["ㄧˇ", "ㄅㄟˋ"], "ㄕ");
        pick_word(&mut e, "時");
        e.key(kind(KeyKind::Left)).unwrap();
        if insert {
            typ(&mut e, &["ㄉㄚˋ"], ""); // a syllable typed just before 時
        } else {
            e.key(kind(KeyKind::Backspace)).unwrap(); // the syllable just before 時 removed
        }
        e.key(kind(KeyKind::End)).unwrap();
        let commit = e.key(kind(KeyKind::Enter)).unwrap().commit;
        assert!(commit.ends_with('時'), "{commit}");
        let before = key_before_last(&commit);
        assert_ne!(before, "以備", "precondition: the edit changed the text before 時 ({commit})");
        let r = e.learner().records();
        let pred = r.iter().find(|x| x.word == "時").expect("the prediction pick is learned");
        assert_eq!(pred.context, before, "insert={insert}: records {:?}", r.iter().map(|x| (&x.context, &x.word)).collect::<Vec<_>>());
    }
}

#[test]
fn t17_choosing_the_same_word_again_in_the_window_keeps_the_prediction_learn() {
    let mut e = learner_engine(Profile::Formal, None);
    e.set_left_context("我想喝一杯");
    typ(&mut e, &[], "ㄋ");
    let o = pick_word(&mut e, "牛奶");
    assert_eq!(o.preedit, "牛奶");
    let mut o = e.key(kind(KeyKind::Space)).unwrap();
    for _ in 0..30 {
        if o.candidates[o.selected.unwrap()] == "牛奶" {
            break;
        }
        o = e.key(kind(KeyKind::Right)).unwrap();
    }
    assert_eq!(o.candidates[o.selected.unwrap()], "牛奶", "牛奶 is in the window");
    assert_eq!(e.key(kind(KeyKind::Enter)).unwrap().preedit, "牛奶");
    assert_eq!(e.key(kind(KeyKind::Enter)).unwrap().commit, "牛奶");
    let r = e.learner().records();
    assert_eq!((r.len(), r[0].context.as_str(), r[0].word.as_str()), (1, "一杯", "牛奶"));
}

#[test]
fn t18_cmd_backspace_on_the_row_re_decodes_the_composition() {
    // 她們 taught after 她們: the free syllables ㄊㄚ ㄇㄣˊ decode to 她們 only because of the record. The row (cursor
    // start ㄊㄚ) lists 她們 first; forgetting it must show 他們 at once.
    let reading = vec!["ㄊㄚ".to_string(), "ㄇㄣˊ".to_string()];
    let (mut e, dir) = seeded("t18", &[rec("她們", &reading, "她們")]);
    e.set_left_context("她們");
    let o = typ(&mut e, &["ㄊㄚ", "ㄇㄣˊ", "ㄊㄚ"], "");
    assert!(o.preedit.starts_with("她們"), "precondition: the record decides the display: {}", o.preedit);
    assert_eq!(row(&o)[0], "她們", "{:?}", row(&o));
    e.key(kind(KeyKind::Tab)).unwrap();
    let o = e.key(cmd_bs()).unwrap();
    assert_eq!(e.learner().records().len(), 0);
    assert!(o.preedit.starts_with("他們"), "the display is decoded again: {}", o.preedit);
    assert!(o.selected.is_some(), "still entered");
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn t19_a_word_in_two_long_starts_keeps_the_largest_weight() {
    // Two fixed 爸 (picked in the window) make two long starts: 0 (ㄅㄚˋ ㄅㄚˋ ㄅ) and 1 (ㄅㄚˋ ㄅ). 爸爸爸 is the far
    // start's only item (weight 0) and the near start has it taught twice; 爸比 is taught once at the near start. The
    // deduped 爸爸爸 keeps its first position and the weight 2, so it beats 爸比.
    let r3: Vec<String> = vec!["ㄅㄚˋ".to_string(); 3];
    let r2 = vec!["ㄅㄚˋ".to_string(), "ㄅㄧˇ".to_string()];
    let mut learned = rec("想爸", &r3, "爸爸爸");
    learned.weight = 2.0;
    let state = |mut e: Engine| {
        e.set_left_context("我想");
        for _ in 0..2 {
            typ(&mut e, &["ㄅㄚˋ"], "");
            let mut o = e.key(kind(KeyKind::Space)).unwrap();
            while o.candidates[o.selected.unwrap()].chars().count() != 1 {
                o = e.key(kind(KeyKind::Right)).unwrap();
            }
            e.key(kind(KeyKind::Enter)).unwrap();
        }
        let o = send(&mut e, pend_keys("ㄅ"));
        assert_eq!(o.preedit, "爸爸ㄅ", "precondition: two fixed words");
        row(&o)
    };
    let base = state(learner_engine(Profile::Chat, None));
    assert_eq!(base[0], "爸爸爸");
    let s = shared();
    let near = predict(s.capped.predict_index(&s.lm), &s.lm, Profile::Chat.lambda(), history("想爸", &s.lm), &units_of(&format!("{}ㄅ", units_keys("ㄅㄚˋ"))), Mode::P, 27);
    assert!(near.iter().any(|x| x.0 == "爸比") && near.iter().any(|x| x.0 == "爸爸爸"), "precondition: both are items of the near start");
    let (e, dir) = seeded("t19", &[learned, rec("想爸", &r2, "爸比")]);
    let got = state(e);
    assert_eq!(&got[..2], &["爸爸爸", "爸比"], "{got:?}");
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn t20_the_scan_gate_changes_nothing_but_time() {
    // Same rows with the gate and without it (every start asks 27 once there is any record), over states whose records
    // match the start, match only another start, or match nothing.
    let milk = |ctx: &str| rec(ctx, &milk_reading(), "牛奶");
    let tian = rec("我想", &["ㄇㄧㄥˊ".to_string(), "ㄊㄧㄢ".to_string()], "明天");
    let cases: Vec<(&str, (&str, &[Tok], &str), Vec<Record>)> = vec![
        ("a milk", A, vec![milk("一杯")]),
        ("a unrelated", A, vec![tian.clone()]),
        ("b", B, vec![milk("想喝"), tian.clone()]),
        ("d", D, vec![tian.clone()]),
        ("f", F, vec![tian.clone(), milk("來再")]),
    ];
    for (name, st, recs) in cases {
        let dir = tmp_dir(&format!("t20{}", name.replace(' ', "")));
        seed(&dir, &recs);
        let (on, mut off) = (learner_engine(Profile::Chat, Some(&dir)), learner_engine(Profile::Chat, Some(&dir)));
        off.set_scan_gate(false);
        assert_eq!(row_of(on, st), row_of(off, st), "state {name}");
        let _ = std::fs::remove_dir_all(&dir);
    }
}

// ---------- section 11: starts up to five syllables back ----------

/// A long word decoded as three words: its start is the third word back.
const G: (&str, &[Tok], &str) = ("", &[Syl("ㄅㄚ"), Syl("ㄐㄧ"), Syl("ㄙ")], "ㄊ");
/// Five syllables before the cursor, the fourth word back.
const H: (&str, &[Tok], &str) = ("", &[Syl("ㄅㄚ"), Syl("ㄍㄢ"), Syl("ㄗ˙"), Syl("ㄉㄚˇ"), Syl("ㄅㄨˋ")], "ㄓ");
/// typing76 「週末本來想在家耍廢」 up to 在: 在 sits inside the last decoded word.
const I: (&str, &[Tok], &str) = ("", &[Syl("ㄓㄡ"), Syl("ㄇㄛˋ"), Syl("ㄅㄣˇ"), Syl("ㄌㄞˊ"), Syl("ㄒㄧㄤˇ"), Syl("ㄗㄞˋ")], "");
/// typing76 「我們中午可以…」: a position inside 中午 has items, the cursor start has 可以.
const J: (&str, &[Tok], &str) =
    ("明天早上我要先去銀行辦事回來再順便買早餐你如果有空的話", &[Syl("ㄨㄛˇ"), Syl("ㄇㄣ˙"), Syl("ㄓㄨㄥ"), Syl("ㄨˇ")], "ㄎ");

fn has(o: &Oracle, w: &str) -> bool {
    words(o).iter().any(|x| x == w)
}

#[test]
fn a_word_start_three_words_back_is_a_long_start() {
    let path: Vec<String> = ref_path(G.0, G.1).into_iter().map(|x| x.0).collect();
    assert_eq!(path, ["巴", "基", "斯"], "precondition: three decoded words");
    let g = check_state("g", G);
    assert!(has(&g, "巴基斯坦"), "{:?}", words(&g));
    assert!(!has(&oracle_with(G.0, G.1, G.2, 0, 0), "巴基斯坦"), "section 1.2 alone already finds it: the state proves nothing");
}

#[test]
fn a_word_start_exactly_five_syllables_back_is_a_long_start() {
    let path = ref_path(H.0, H.1);
    assert!(path.len() >= 4, "precondition: the start at 0 is the fourth word back or further: {path:?}");
    let h = check_state("h", H);
    assert!(has(&h, "八竿子打不著"), "{:?}", words(&h));
    let h4 = oracle_with(H.0, H.1, H.2, 4, 3);
    assert!(!has(&h4, "八竿子打不著") && words(&h4) != words(&h), "four back: {:?}", words(&h4));
}

#[test]
fn a_position_inside_a_word_adds_its_items_after_the_others() {
    let path = ref_path(I.0, I.1);
    let (w, c, _) = path.last().unwrap();
    assert!(6 - c == 4 && *c >= 2, "precondition: the last word {w} starts at 4 and covers 5: {path:?}");
    let i = check_state("i", I);
    assert!(i.mid_starts.contains(&5) && has(&i, "在家"), "{:?} {:?}", i.mid_starts, words(&i));
    assert!(!has(&oracle_with(I.0, I.1, I.2, PREDICT_BACK, 0), "在家"), "without the positions inside words");
}

#[test]
fn positions_inside_words_come_last_so_the_cursor_start_keeps_its_items() {
    let j = check_state("j", J);
    assert!(has(&j, "可以"), "{:?}", words(&j));
    let merged = oracle_with(J.0, J.1, J.2, PREDICT_BACK, 1);
    assert!(words(&merged) != words(&j) && !has(&merged, "可以"), "merged far to near: {:?}", words(&merged));
}

#[test]
fn a_learned_item_of_a_position_inside_a_word_comes_first() {
    let reading = vec!["ㄗㄞˋ".to_string(), "ㄐㄧㄚ".to_string()];
    // (b) untaught, 在家 is in the row but not first.
    let plain = row_of(engine(), I);
    let at = plain.iter().position(|w| w == "在家").expect("在家 is in the untaught row");
    assert!(at > 0, "{plain:?}");
    let (e, dir) = seeded("s11-mid", &[rec("來想", &reading, "在家")]);
    let (mut e, o) = build_in(e, I.0, I.1, I.2);
    // (a) in this decode the position of 在 (5) is not a word start, so not a long start; no pending unit, so no cursor
    // start either.
    assert!(!e.path_starts().contains(&5), "{:?}", e.path_starts());
    assert_eq!(row(&o).first().map(String::as_str), Some("在家"), "{:?}", row(&o));
    // The item is that position's: picking it ends the composition at 5 + 2.
    let picked = pick_word(&mut e, "在家");
    assert_eq!(picked.cursor_utf16, 7, "{}", picked.preedit);
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn positions_inside_a_word_the_user_fixed_are_not_starts() {
    // 我們中午, then ㄎ and Backspace: a recompute with no pending unit. Free, 中午 is a decoded word and the position
    // of 午 (3) inside it is a start (section 11). Fixed through the candidate window, 中午 is the user's choice: no item
    // starts inside it (an item from the fixed word's own start replaces it whole, as before section 11).
    let syls = ["ㄨㄛˇ", "ㄇㄣ˙", "ㄓㄨㄥ", "ㄨˇ"];
    let state = |fix: bool| {
        let mut e = engine();
        typ(&mut e, &syls, "");
        if fix {
            let mut o = e.key(kind(KeyKind::Space)).unwrap();
            while o.candidates[o.selected.unwrap()] != "中午" {
                o = e.key(kind(KeyKind::Right)).unwrap();
            }
            e.key(kind(KeyKind::Enter)).unwrap();
        }
        typ(&mut e, &[], "ㄎ");
        let o = e.key(kind(KeyKind::Backspace)).unwrap();
        assert_eq!(o.preedit, "我們中午");
        (e, row(&o))
    };
    // Each item's start: picking it leaves the cursor at start + length.
    let starts = |fix: bool| -> Vec<(String, usize)> {
        let (_, items) = state(fix);
        assert!(!items.is_empty(), "fixed {fix}: no row");
        (0..items.len())
            .map(|i| {
                let (mut e, _) = state(fix);
                let p = e.pick(i).unwrap().unwrap();
                (items[i].clone(), p.cursor_utf16 as usize - items[i].chars().count())
            })
            .collect()
    };
    let free = starts(false);
    assert!(free.iter().any(|x| x.1 == 3), "precondition: free, an item starts at 3: {free:?}");
    let fixed = starts(true);
    assert!(fixed.iter().all(|x| x.1 != 3), "an item starts inside the fixed 中午: {fixed:?}");
}

#[test]
fn reading_matches_compares_the_first_syllable_as_before() {
    // Section 11.2: the char comparison against the old `starts_with` on a collected String, with expected values.
    let old = |units: &[core::predict::Unit], reading: &[String]| {
        let head: String = units[0].chars.iter().collect();
        reading.len() >= units.len() && reading[0].trim_start_matches('˙').starts_with(&head)
    };
    let r = |s: &str| s.split(' ').map(String::from).collect::<Vec<_>>();
    // (keys, reading, expected): the same syllable, a prefix, a shorter first syllable, the neutral tone in front,
    // the last character differing, a different initial, too few syllables.
    let cases = [
        ("ㄋㄧㄡˊ", "ㄋㄧㄡˊ ㄋㄞˇ", true),
        ("ㄋ", "ㄋㄧㄡˊ ㄋㄞˇ", true),
        ("ㄋㄧㄡ", "ㄋㄧ", false),
        ("ㄉㄜ", "˙ㄉㄜ", true),
        ("ㄋㄧㄡ", "ㄋㄧㄠˇ", false),
        ("ㄇ", "ㄋㄧ", false),
        ("ㄋㄧㄡˊㄋ", "ㄋㄧㄡˊ", false),
    ];
    for (keys, reading, want) in cases {
        let u = units_of(keys);
        assert_eq!(reading_matches(&u, &r(reading)), want, "{keys} {reading}");
        assert_eq!(old(&u, &r(reading)), want, "the old first-syllable test: {keys} {reading}");
    }
}

// ---------- research: how far back a word start is guessed (amendment two, contract section 11.3) ----------

/// (name, back, mid) for `oracle_with`; "now" is section 1.2, "w5m3" is section 11 (the shipping rule).
const RULES: [(&str, usize, u8); 7] =
    [("now", 0, 0), ("w5", 5, 0), ("w4m3", 4, 3), ("w5m3", 5, 3), ("w6m3", 6, 3), ("w5m2", 5, 2), ("w5m1", 5, 1)];
const SHIPPING: usize = 3;

/// hit@9 of every word of two or more characters (the answer segmented by `segment_words`), at the first key and at
/// the end of each of its syllables but the last. Chat profile, the row's own left context, the whole sentence in one
/// composition. `SHOW_CHANGES=1` prints every state where the shipping rule and "now" differ.
/// `cargo test --release -p core --test engine_predict -- --ignored --nocapture --exact measure_start_range`
#[test]
#[ignore]
fn measure_start_range() {
    assert!(RULES[SHIPPING].1 == PREDICT_BACK && RULES[SHIPPING].2 == 3);
    assert!(std::env::var("ACG_PACK").is_err(), "ACG_PACK puts the word pack into every test of this binary: unset it");
    for set in ["user-typing", "user-reported"] {
        let text = std::fs::read_to_string(root().join(format!("eval/dev/{set}.txt"))).unwrap();
        let mut table = Table::default();
        for line in text.lines().filter(|l| !l.is_empty()) {
            let f: Vec<&str> = line.split('|').collect();
            let (left, sent) = (f[0], f[1]);
            let syls = leak_syls(f[2].split(' '));
            let seg = shared().lex.segment_words(sent).filter(|_| sent.chars().count() == syls.len()).expect("every row segments");
            let mut a = 0;
            for (w, _) in seg {
                let m = w.chars().count();
                if m >= 2 {
                    table.word(&format!("{set} {left}|{sent}"), left, &w, &syls[..a + m], a, 0);
                }
                a += m;
            }
        }
        table.print(set);
    }
}

/// The word pack's names of three or more characters (every 20th), typed alone and after `我最喜歡`, from the second
/// syllable on. `ACG_PACK` is a pack file named `acg-add.tsv` (its directory is the packs dir `load_lexicon_packs`
/// reads), e.g. the shipped one:
/// `ACG_PACK=$PWD/data/packs/acg-add.tsv cargo test --release -p core --test engine_predict -- --ignored --nocapture --exact measure_start_range_acg`
#[test]
#[ignore]
fn measure_start_range_acg() {
    let pack = std::env::var("ACG_PACK").expect("set ACG_PACK to the word pack's acg-add.tsv");
    let text = std::fs::read_to_string(pack).unwrap();
    let names: Vec<(String, Vec<&'static str>)> = text
        .lines()
        .filter(|l| !l.starts_with('#'))
        .filter_map(|l| {
            let f: Vec<&str> = l.split('\t').collect();
            let syls = leak_syls(f[0].split('-'));
            (f[1].chars().count() >= 3 && f[1].chars().count() == syls.len()).then(|| (f[1].to_string(), syls))
        })
        .step_by(20)
        .collect();
    let lead = leak_syls(["ㄨㄛˇ", "ㄗㄨㄟˋ", "ㄒㄧˇ", "ㄏㄨㄢ"].into_iter());
    for (label, pre) in [("alone", Vec::new()), ("after 我最喜歡", lead)] {
        let mut table = Table::default();
        for (w, syls) in &names {
            let all: Vec<&'static str> = pre.iter().chain(syls.iter()).copied().collect();
            table.word(&format!("{label} {w}"), "", w, &all, pre.len(), 1);
        }
        table.print(&format!("acg names ({}) {label}", names.len()));
    }
}

fn leak_syls<'a>(it: impl Iterator<Item = &'a str>) -> Vec<&'static str> {
    it.map(|x| &*Box::leak(x.to_string().into_boxed_str())).collect()
}

#[derive(Default)]
struct Table {
    /// stage -> per state, hit under each rule
    hits: std::collections::BTreeMap<String, Vec<Vec<bool>>>,
    queries: [usize; RULES.len()],
    changed: [usize; RULES.len()],
    /// States whose row is empty under "now" and not under the rule (Tab then enters the row instead of rule 13/21).
    filled: [usize; RULES.len()],
    states: usize,
}

impl Table {
    /// The word `w` over `syls[a..]` (all of `syls` is typed in one composition after `left`): its states from syllable
    /// `from` on.
    fn word(&mut self, label: &str, left: &str, w: &str, syls: &[&'static str], a: usize, from: usize) {
        let m = syls.len() - a;
        for k in from..m {
            for done in [false, true] {
                if done && k == m - 1 {
                    continue;
                }
                let (upto, pending) = if done {
                    (a + k + 1, String::new())
                } else {
                    (a + k, syls[a + k].chars().find(|c| !is_tone(*c)).unwrap().to_string())
                };
                let toks: Vec<Tok> = syls[..upto].iter().map(|y| Syl(y)).collect();
                let rows: Vec<Oracle> = RULES.iter().map(|&(_, b, md)| oracle_with(left, &toks, &pending, b, md)).collect();
                let hv: Vec<bool> = rows.iter().map(|o| words(o).iter().any(|x| x == w)).collect();
                for (i, o) in rows.iter().enumerate() {
                    self.queries[i] += o.queried.len();
                    self.changed[i] += (words(o) != words(&rows[0])) as usize;
                    self.filled[i] += (words(&rows[0]).is_empty() && !words(o).is_empty()) as usize;
                }
                if std::env::var("SHOW_CHANGES").is_ok() && hv[0] != hv[SHIPPING] {
                    let tag = if hv[SHIPPING] { "GAIN" } else { "LOSS" };
                    println!("{tag} {label} {w} upto {upto} pending {pending}\n  now  {:?}\n  ship {:?}", words(&rows[0]), words(&rows[SHIPPING]));
                }
                let stage = format!("s{}{}", (k + 1).min(5), if done { " done" } else { " key1" });
                self.hits.entry(stage).or_default().push(hv);
                self.states += 1;
            }
        }
    }

    fn print(&self, name: &str) {
        let per = |v: &[usize]| RULES.iter().zip(v).map(|(r, x)| format!("{} {:.2}", r.0, *x as f64 / self.states as f64)).collect::<Vec<_>>();
        println!("\n## {name}: states {}", self.states);
        println!("predict calls per state: {:?}", per(&self.queries));
        println!("rows that differ from now: {:?}", RULES.iter().zip(&self.changed).map(|(r, c)| format!("{} {c}", r.0)).collect::<Vec<_>>());
        println!("rows empty under now, not empty under the rule: {:?}", RULES.iter().zip(&self.filled).map(|(r, c)| format!("{} {c}", r.0)).collect::<Vec<_>>());
        println!("stage\tn\t{}", RULES.iter().map(|r| format!("{} (+/-)", r.0)).collect::<Vec<_>>().join("\t"));
        let mut all = Vec::new();
        for (stage, v) in &self.hits {
            println!("{stage}\t{}", cells(v));
            all.extend(v.iter().cloned());
        }
        println!("all\t{}", cells(&all));
    }
}

/// n, then hit% (gained/lost against "now") for each rule.
fn cells(v: &[Vec<bool>]) -> String {
    let c: Vec<String> = (0..RULES.len())
        .map(|i| {
            let hit = v.iter().filter(|h| h[i]).count();
            let (gain, loss) = (v.iter().filter(|h| h[i] && !h[0]).count(), v.iter().filter(|h| !h[i] && h[0]).count());
            format!("{:.1} (+{gain}/-{loss})", 100.0 * hit as f64 / v.len() as f64)
        })
        .collect();
    format!("{}\t{}", v.len(), c.join("\t"))
}

// ---------- the abbreviation composer (contract section 12) ----------

/// A chat engine with the abbreviation setting on and `left` before the insertion point.
fn abbr_engine(left: &str) -> Engine {
    let mut e = engine();
    e.set_abbreviation(true).unwrap();
    e.set_left_context(left);
    e
}
/// `predict` in `PA` mode for these unfinished units, as the row of an empty composition (no long starts), first nine.
fn abbr_oracle(left: &str, units: &[&str]) -> Vec<(String, Vec<String>)> {
    let s = shared();
    let key = context_key(left);
    let v = history(if key == "^" { "" } else { &key }, &s.lm);
    let u: Vec<Unit> = units.iter().map(|c| Unit { chars: c.chars().collect(), done: false, tone: None }).collect();
    predict(s.capped.predict_index(&s.lm), &s.lm, Profile::Chat.lambda(), v, &u, Mode::PA, 9).into_iter().map(|x| (x.0, x.3)).collect()
}
fn milk_tea_reading() -> Vec<String> {
    vec!["ㄋㄞˇ".to_string(), "ㄔㄚˊ".to_string()]
}

#[test]
fn abbreviation_two_units_show_and_select_milk_tea() {
    let mut e = abbr_engine(A.0);
    e.set_learning(true);
    let o = typ(&mut e, &[], "ㄋㄔ");
    assert_eq!(o.preedit, "ㄋㄔ", "two units show in order");
    assert_passive(&o);
    let want = abbr_oracle(A.0, &["ㄋ", "ㄔ"]);
    assert_eq!(row(&o), want.iter().map(|x| x.0.clone()).collect::<Vec<_>>(), "the row is predict in PA mode");
    assert!(row(&o).contains(&"奶茶".to_string()), "{:?}", row(&o));
    let o = pick_word(&mut e, "奶茶");
    assert_eq!((o.preedit.as_str(), o.cursor_utf16), ("奶茶", 2));
    assert_no_row(&o);
    assert_eq!(e.key(kind(KeyKind::Enter)).unwrap().commit, "奶茶");
    let r = e.learner().records();
    assert_eq!(r.len(), 1);
    assert_eq!((r[0].reading.clone(), r[0].word.as_str()), (milk_tea_reading(), "奶茶"), "the syllables are the word's reading");
    assert_eq!(want.iter().find(|x| x.0 == "奶茶").unwrap().1, milk_tea_reading());
}

#[test]
fn abbreviation_needs_the_setting_the_row_and_the_end_of_the_composition() {
    // setting off: the second key replaces the first
    let mut e = engine();
    e.set_left_context(A.0);
    assert_eq!(typ(&mut e, &[], "ㄋㄔ").preedit, "ㄔ");
    // setting on, row off
    let mut e = abbr_engine(A.0);
    e.set_prediction(false).unwrap();
    assert_eq!(typ(&mut e, &[], "ㄋㄔ").preedit, "ㄔ");
    // setting on, cursor not at the end
    let mut e = abbr_engine("");
    typ(&mut e, &["ㄋㄧˇ", "ㄏㄠˇ"], "");
    e.key(kind(KeyKind::Left)).unwrap();
    let o = send(&mut e, pend_keys("ㄋㄔ"));
    assert!(o.preedit.contains('ㄔ') && !o.preedit.contains('ㄋ'), "{}", o.preedit);
}

#[test]
fn abbreviation_keys_with_two_units() {
    let mut e = abbr_engine(A.0);
    let before = typ(&mut e, &[], "ㄋㄔ");
    for k in [Key::ch('3', 0), kind(KeyKind::Space)] {
        assert!(e.key(k).unwrap() == before, "tone and space do nothing");
    }
    assert_eq!(e.key(kind(KeyKind::Backspace)).unwrap().preedit, "ㄋ", "one Backspace leaves one unit");
    // Enter and Shift+Enter send the shown symbols
    let mut e = abbr_engine(A.0);
    typ(&mut e, &[], "ㄋㄔ");
    let o = e.key(kind(KeyKind::Enter)).unwrap();
    assert_eq!((o.commit.as_str(), o.handled), ("ㄋㄔ", true));
    typ(&mut e, &[], "ㄋㄔ");
    let o = e.key(Key { kind: KeyKind::Enter, ch: '\0', modifiers: MOD_SHIFT }).unwrap();
    assert_eq!((o.commit.as_str(), o.handled), ("ㄋㄔ", false));
    // punctuation drops the units, then inserts the mark (rule 2)
    typ(&mut e, &[], "ㄋㄔ");
    assert_eq!(e.key(comma()).unwrap().preedit, "，");
    // the known gap: a syllable without an initial joins the unit before it
    e.reset(ResetMode::Discard);
    let o = typ(&mut e, &[], "ㄋㄢ");
    assert_eq!(o.preedit, "ㄋㄢ");
    let o = e.key(Key::ch('6', 0)).unwrap(); // ˊ: one unit, so the tone completes ㄋㄢˊ
    assert!(!o.preedit.contains('ㄋ') && !o.preedit.is_empty(), "{}", o.preedit);
}

#[test]
fn abbreviation_esc_drops_only_the_units() {
    let mut e = abbr_engine(A.0);
    e.set_learning(true);
    typ(&mut e, &[], "ㄋ");
    let o = pick_word(&mut e, "奶茶");
    assert_eq!(o.preedit, "奶茶");
    let o = send(&mut e, pend_keys("ㄋㄔ"));
    assert_eq!(o.preedit, "奶茶ㄋㄔ");
    let o = e.key(kind(KeyKind::Esc)).unwrap();
    assert_eq!((o.preedit.as_str(), o.cursor_utf16), ("奶茶", 2), "the word and the cursor stay");
    assert_eq!(e.key(kind(KeyKind::Enter)).unwrap().commit, "奶茶");
    assert_eq!(e.learner().records().len(), 1, "the fixed word is still there to be learned");
}

#[test]
fn abbreviation_off_or_row_off_drops_two_units_only() {
    for row_off in [false, true] {
        let mut e = abbr_engine(A.0);
        let two = typ(&mut e, &[], "ㄋㄔ");
        assert_eq!(two.preedit, "ㄋㄔ");
        let o = if row_off { e.set_prediction(false) } else { e.set_abbreviation(false) }.unwrap();
        assert_eq!(o.preedit, "", "row_off {row_off}");
        assert_no_row(&o);
    }
    let mut e = abbr_engine(A.0);
    let one = typ(&mut e, &[], "ㄋ");
    assert!(e.set_abbreviation(false).unwrap() == one, "one unit: nothing changes");
    let mut e = abbr_engine(A.0);
    let one = typ(&mut e, &[], "ㄋ");
    assert!(e.set_prediction(false).unwrap().preedit == one.preedit);
    // switching it on changes nothing
    let mut e = engine();
    e.set_left_context(A.0);
    let one = typ(&mut e, &[], "ㄋ");
    assert!(e.set_abbreviation(true).unwrap() == one);
}

#[test]
fn abbreviation_picks_are_learned_like_prefix_picks_and_come_first_next_time() {
    // the same record as the prefix completion
    let mut e = learner_engine(Profile::Chat, None);
    e.set_abbreviation(true).unwrap();
    teach(&mut e, A.0, "ㄋㄔ", "奶茶");
    let abbr_rec = e.learner().records().to_vec();
    let mut p = learner_engine(Profile::Chat, None);
    teach(&mut p, A.0, "ㄋ", "奶茶");
    assert_eq!(abbr_rec.len(), 1);
    assert_eq!(abbr_rec[0].context, p.learner().records()[0].context);
    assert_eq!(abbr_rec[0].reading, p.learner().records()[0].reading);
    assert_eq!(abbr_rec[0].word, p.learner().records()[0].word);
    // an item that was not first comes first after it was chosen
    let mut e = learner_engine(Profile::Chat, None);
    e.set_abbreviation(true).unwrap();
    e.set_left_context(A.0);
    let base = row(&typ(&mut e, &[], "ㄋㄔ"));
    assert!(base.len() > 2, "{base:?}");
    let word = base[2].clone();
    pick_word(&mut e, &word);
    assert_eq!(e.key(kind(KeyKind::Enter)).unwrap().commit, word);
    e.set_left_context(A.0);
    let again = row(&typ(&mut e, &[], "ㄋㄔ"));
    assert_eq!(again[0], word, "{base:?} -> {again:?}");
}

#[test]
fn reading_matches_follows_the_query_mode() {
    let r = |s: &str| s.split(' ').map(String::from).collect::<Vec<_>>();
    let u = |ch: &[&str]| -> Vec<Unit> { ch.iter().map(|c| Unit { chars: c.chars().collect(), done: false, tone: None }).collect() };
    // (units, reading, in P, in PA)
    let cases = [
        (u(&["ㄋ", "ㄔ"]), "ㄋㄞˇ ㄔㄚˊ", false, true),
        (u(&["ㄋ", "ㄔ"]), "ㄋㄞˇ ㄔㄚˊ ㄗ", false, false), // an abbreviation is as long as its word
        (u(&["ㄋ", "ㄔ"]), "ㄋㄞˇ ㄇㄚ", false, false),
        (u(&["ㄋ"]), "ㄋㄞˇ ㄔㄚˊ", true, true),
        (u(&["ㄋ", "ㄔ", "ㄗ"]), "˙ㄋㄜ ㄔㄚˊ ㄗㄨㄛˋ", false, true),
    ];
    for (units, reading, p, pa) in cases {
        assert_eq!(reading_matches_in(&units, &r(reading), Mode::P), p, "P {reading}");
        assert_eq!(reading_matches_in(&units, &r(reading), Mode::PA), pa, "PA {reading}");
    }
    // done + undone: only the prefix reading
    let mixed = units_of("ㄋㄧˇㄏ");
    assert_eq!(reading_matches_in(&mixed, &r("ㄋㄧˇ ㄏㄠˇ"), Mode::PA), true);
    assert_eq!(reading_matches_in(&mixed, &r("ㄋㄧㄡˊ ㄏㄠˇ"), Mode::PA), false);
    // never false for a reading predict returns, in either mode
    let s = shared();
    let idx = s.capped.predict_index(&s.lm);
    let sep = |keys: &str| -> Vec<Unit> { keys.chars().map(|c| u(&[&c.to_string()])).flatten().collect() };
    for (units, abbr_only) in [(sep("ㄋ"), false), (sep("ㄋㄔ"), true), (sep("ㄅㄐㄙ"), true), (units_of("ㄇㄧㄥˊㄊ"), false), (units_of("ㄉㄜ"), false)] {
        let mut returned = 0;
        for mode in [Mode::P, Mode::PA] {
            let got = predict(idx, &s.lm, Profile::Chat.lambda(), "", &units, mode, 200);
            returned += got.len();
            assert!(mode == Mode::PA || !abbr_only || got.is_empty());
            for (w, _, _, reading) in got {
                assert!(reading_matches_in(&units, &reading, mode), "{w} {reading:?}");
            }
        }
        assert!(returned > 0);
    }
}
