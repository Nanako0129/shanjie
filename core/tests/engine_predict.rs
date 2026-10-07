//! V3 engine tests (docs/contracts/v3-engine.md section 7.1): the prediction row, its keys, selection, clearing, the
//! C-facing fields. Real lexicon and model (data/lm/bigram.sjlm); fails loudly without them, never skips.
use core::engine::*;
use core::learn::context_key;
use core::lm::{decode_segment, history, CappedLexicon, End, Lm, Profile};
use core::predict::{predict, units_of, Mode};
use core::Lexicon;
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};
use std::time::{Duration, Instant};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}
fn lm_path() -> PathBuf {
    let p = root().join("data/lm/bigram.sjlm");
    assert!(p.exists(), "data/lm/bigram.sjlm is missing: gh release download model-v3 -R Nanako0129/shanjie -p bigram.sjlm -D data/lm");
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
    /// Start positions that were queried (far to near, cursor start last), and those skipped for punctuation.
    queried: Vec<usize>,
    skipped: Vec<usize>,
    /// Long-start items after step 2 (before the cap), and items that the filter of step 1 removed.
    long_total: usize,
    filtered: Vec<String>,
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
    for (_, c, _) in path.iter().rev().take(2) {
        pos -= c;
        long_starts.push(pos);
    }
    long_starts.reverse();
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
    let mut merged: Vec<_> = long.iter().take(3).cloned().collect();
    merged.extend(cursor_items);
    merged.extend(long.iter().skip(3).cloned());
    let mut seen = std::collections::HashSet::new();
    merged.retain(|x| seen.insert(x.0.clone()));
    merged.truncate(9);
    Oracle { items: merged, queried, skipped, long_total, filtered }
}

fn words(o: &Oracle) -> Vec<String> {
    o.items.iter().map(|i| i.0.clone()).collect()
}

/// Replays `toks` into a fresh engine (left context set), returns the engine and the last output.
fn build(left: &str, toks: &[Tok], pending: &str) -> (Engine, Output) {
    let mut e = engine();
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
    // (e) two long starts plus the cursor start with more than three long items: the cap works.
    let e = check_state("e", E);
    assert!(e.long_total > 3 && e.queried.len() == 3, "e: long items {} queried {:?}", e.long_total, e.queried);
    let f = check_state("f", F);
    assert!(f.long_total > 3 && f.queried.len() == 3, "f: long items {} queried {:?}", f.long_total, f.queried);
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
fn a_prediction_pick_is_not_learned() {
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
    let o = e.key(kind(KeyKind::Enter)).unwrap();
    assert_eq!(o.commit, "奶茶");
    assert!(e.learner().records().is_empty(), "a prediction pick must not be recorded");

    // Positive control: re-picking the same span in the candidate window is learned.
    e.set_left_context("我想喝一杯");
    typ(&mut e, &["ㄋㄞˇ", "ㄔㄚˊ"], "");
    send(&mut e, vec![kind(KeyKind::Space)]);
    let o = e.key(kind(KeyKind::Right)).unwrap();
    let second = o.candidates[o.selected.unwrap()].clone();
    e.key(kind(KeyKind::Enter)).unwrap();
    let o = e.key(kind(KeyKind::Enter)).unwrap();
    assert_ne!(o.commit, "", "something was committed ({second})");
    assert!(!e.learner().records().is_empty(), "control: a candidate-window re-pick is learned");
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
    let s = shared();
    let rss0 = rss_kb();
    let t = Instant::now();
    s.capped.predict_index(&s.lm);
    println!("index build {:?}; rss {} -> {} KiB", t.elapsed(), rss0, rss_kb());
    let text = std::fs::read_to_string(root().join("eval/dev/user-typing.txt")).unwrap();
    let mut times: Vec<Duration> = Vec::new();
    let mut rows = 0;
    for line in text.lines().filter(|l| !l.is_empty()) {
        let f: Vec<&str> = line.split('|').collect();
        let (left, reading) = (f[0], f[2]);
        let mut e = engine();
        e.set_left_context(left);
        let mut keys: Vec<Key> = reading.split(' ').flat_map(syl_keys).collect();
        keys.push(kind(KeyKind::Enter));
        for k in keys {
            let t = Instant::now();
            e.key(k).unwrap();
            times.push(t.elapsed());
        }
        rows += 1;
    }
    times.sort();
    let ms = |d: Duration| d.as_secs_f64() * 1000.0;
    println!(
        "typing76: rows {rows} keys {} p50 {:.3} ms p95 {:.3} ms max {:.3} ms",
        times.len(),
        ms(times[times.len() / 2]),
        ms(times[times.len() * 95 / 100]),
        ms(times[times.len() - 1])
    );
    println!("rss after: {} KiB", rss_kb());
    assert!(times[times.len() * 95 / 100] < Duration::from_millis(16), "p95 over 16 ms");
}
