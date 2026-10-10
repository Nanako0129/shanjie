//! Candidate-vertical contract (docs/contracts/candidate-vertical.md) section 2.2 / 3.1: the vertical window's key rules,
//! orientation fixed at open, Page Up / Down outside it. Output/Key hold input text and have no Debug: compare with
//! `assert!(a == b)`. Lists come from `Lexicon::entries`, independent of the engine's own windowing.
use core::engine::*;
use core::Lexicon;
use std::path::Path;
use std::sync::{Arc, OnceLock};

fn real() -> Arc<Lexicon> {
    static L: OnceLock<Arc<Lexicon>> = OnceLock::new();
    L.get_or_init(|| load_lexicon(&Path::new(env!("CARGO_MANIFEST_DIR")).join("../data/lexicon")).unwrap()).clone()
}
/// A lexicon whose only syllable ㄋㄧˇ (`su3`) has exactly `n` one-character homophones.
fn tiny(n: usize) -> Arc<Lexicon> {
    let text: String = (0..n).map(|i| format!("ㄋㄧˇ {} -1.0\n", char::from_u32(0x4E00 + i as u32).unwrap())).collect();
    Arc::new(Lexicon::parse(&text).unwrap())
}
fn k(e: &mut Engine, key: Key) -> Output {
    e.key(key).unwrap()
}
fn kk(e: &mut Engine, kind: KeyKind) -> Output {
    k(e, Key::new(kind))
}
fn presses(e: &mut Engine, n: usize, kind: KeyKind) -> Output {
    let mut o = None;
    for _ in 0..n {
        o = Some(kk(e, kind));
    }
    o.unwrap()
}
fn typ(e: &mut Engine, s: &str) -> Output {
    let mut last = None;
    for c in s.chars() {
        last = Some(k(e, if c == ' ' { Key::new(KeyKind::Space) } else { Key::ch(c, 0) }));
    }
    last.unwrap()
}
fn digit(c: char) -> Key {
    Key::ch(c, 0)
}
fn with_mod(kind: KeyKind, modifiers: u32) -> Key {
    Key { kind, ch: '\0', modifiers }
}
/// The whole list of the one-syllable window: every candidate of the syllable, in the lexicon's order.
fn list_of(l: &Lexicon, syl: &str) -> Vec<String> {
    l.entries(&[syl.to_string()]).into_iter().map(|(w, _)| w.to_string()).collect()
}
/// (engine, the window's first output, the whole list) with `vertical` set before the window opens. `n` = 0 uses the real
/// lexicon's ㄕˋ (more than 27 homophones); otherwise `n` homophones of ㄋㄧˇ.
fn open(n: usize, vertical: bool) -> (Engine, Output, Vec<String>) {
    let (lex, keys, syl) = if n == 0 { (real(), "g4 ", "ㄕˋ") } else { (tiny(n), "su3 ", "ㄋㄧˇ") };
    let all = list_of(&lex, syl);
    assert!(n == 0 && all.len() > 27 || all.len() == n, "the list is the size the test asked for");
    let mut e = Engine::with_lexicon(lex, Layout::Standard);
    e.set_candidate_vertical(vertical).unwrap();
    let o = typ(&mut e, keys);
    (e, o, all)
}
fn at(o: &Output) -> usize {
    (o.first + o.selected.unwrap() as u32) as usize
}
/// The vertical window showing nine (or fewer) rows from `first`, the selection at list position `sel`.
fn assert_view(o: &Output, all: &[String], first: usize, sel: usize) {
    assert!(o.handled && o.vertical && o.columns == 0, "vertical window, no grid");
    assert!(o.candidates == all[first..(first + 9).min(all.len())], "visible rows from {first}");
    assert!(o.first as usize == first && o.total as usize == all.len(), "first/total");
    assert!(o.selected == Some(sel - first), "selection at {sel}, first {first}");
}

// ---------- opening ----------

#[test]
fn opens_vertical_with_the_first_rows_and_zero_selected() {
    for n in [5, 9, 10, 30, 0] {
        let (_e, o, all) = open(n, true);
        assert_view(&o, &all, 0, 0);
        assert!(o.candidates.len() == all.len().min(9));
    }
}

#[test]
fn default_is_horizontal_and_a_horizontal_window_is_unchanged() {
    // set_candidate_vertical never called.
    let mut e = Engine::with_lexicon(tiny(30), Layout::Standard);
    let o = typ(&mut e, "su3 ");
    assert!(!o.vertical && o.columns == 0 && o.candidates.len() == 9 && o.selected == Some(0));
    let o = kk(&mut e, KeyKind::Down);
    assert!(!o.vertical && o.columns == 9, "horizontal Down still expands the grid");
}

// ---------- keys ----------

#[test]
fn digits_pick_the_nth_visible_row() {
    let (mut e, _, all) = open(0, true);
    // Scroll one row: the visible rows are list[1..10], so 1 picks list[1].
    presses(&mut e, 9, KeyKind::Down);
    let o = k(&mut e, digit('1'));
    assert!(o.handled && o.selected.is_none() && o.candidates.is_empty() && !o.vertical && o.preedit == all[1], "1 = first visible row");
    let (mut e, _, all) = open(0, true);
    presses(&mut e, 9, KeyKind::Down);
    let o = k(&mut e, digit('9'));
    assert!(o.selected.is_none() && o.preedit == all[9], "9 = ninth visible row");
    // After Page Down the first row is list[9].
    let (mut e, _, all) = open(0, true);
    kk(&mut e, KeyKind::PageDown);
    let o = k(&mut e, digit('4'));
    assert!(o.selected.is_none() && o.preedit == all[9 + 3]);
    // The selection does not matter, only the row.
    let (mut e, _, all) = open(0, true);
    presses(&mut e, 5, KeyKind::Down);
    let o = k(&mut e, digit('2'));
    assert!(o.preedit == all[1]);
}

#[test]
fn digit_beyond_the_visible_rows_is_ignored() {
    for n in [5, 9, 10] {
        let (mut e, o0, all) = open(n, true);
        let shown = all.len().min(9);
        if shown < 9 {
            let o = k(&mut e, digit('9'));
            assert!(o == Output { ..o0.clone() }, "n {n}: 9 with {shown} rows changes nothing");
            assert_view(&o, &all, 0, 0);
        }
    }
    // Five candidates: 6 is ignored, 5 picks the last.
    let (mut e, _, all) = open(5, true);
    let o = k(&mut e, digit('6'));
    assert_view(&o, &all, 0, 0);
    let o = k(&mut e, digit('5'));
    assert!(o.selected.is_none() && o.preedit == all[4], "5 of 5 picks the last");
}

#[test]
fn down_right_space_select_next_and_scroll_one_row_at_the_bottom() {
    for kind in [KeyKind::Down, KeyKind::Right, KeyKind::Space] {
        let (mut e, _, all) = open(0, true);
        // Rows 1..9: the selection moves down inside the visible rows.
        for i in 1..=8 {
            assert_view(&kk(&mut e, kind), &all, 0, i);
        }
        // On the ninth row: the window scrolls by one, the selection stays on the ninth row.
        assert_view(&kk(&mut e, kind), &all, 1, 9);
        assert_view(&kk(&mut e, kind), &all, 2, 10);
        // To the very end: the selection reaches the last candidate and stays there.
        let last = all.len() - 1;
        let o = presses(&mut e, last - 10, kind);
        assert_view(&o, &all, last - 8, last);
        assert_view(&kk(&mut e, kind), &all, last - 8, last);
        assert_view(&kk(&mut e, kind), &all, last - 8, last);
    }
}

#[test]
fn up_left_select_previous_and_scroll_one_row_at_the_top() {
    for kind in [KeyKind::Up, KeyKind::Left] {
        let (mut e, _, all) = open(0, true);
        // At the very start: nothing moves.
        assert_view(&kk(&mut e, kind), &all, 0, 0);
        // Scrolled by 3 (selection on the ninth row, list position 11, first 3).
        presses(&mut e, 11, KeyKind::Down);
        assert_view(&kk(&mut e, KeyKind::Down), &all, 4, 12);
        // Up inside the visible rows moves only the selection.
        assert_view(&kk(&mut e, kind), &all, 4, 11);
        // Back to the first visible row, then one more: the window scrolls up by one.
        let o = presses(&mut e, 7, kind);
        assert_view(&o, &all, 4, 4);
        assert_view(&kk(&mut e, kind), &all, 3, 3);
        assert_view(&kk(&mut e, kind), &all, 2, 2);
        let o = presses(&mut e, 2, kind);
        assert_view(&o, &all, 0, 0);
        assert_view(&kk(&mut e, kind), &all, 0, 0);
    }
}

#[test]
fn page_down_moves_the_window_by_nine_with_the_selection_on_the_first_row() {
    let (mut e, _, all) = open(0, true);
    let len = all.len();
    assert!(len > 27);
    assert_view(&kk(&mut e, KeyKind::PageDown), &all, 9, 9);
    assert_view(&kk(&mut e, KeyKind::PageDown), &all, 18, 18);
    // Never past the position that shows the last candidate in the ninth row.
    let last_first = len - 9;
    let mut first = 18;
    while first + 9 < last_first {
        first += 9;
        assert_view(&kk(&mut e, KeyKind::PageDown), &all, first, first);
    }
    assert_view(&kk(&mut e, KeyKind::PageDown), &all, last_first, last_first);
    // Already at the end: nothing moves, not even the selection.
    presses(&mut e, 2, KeyKind::Down);
    let before = kk(&mut e, KeyKind::Down);
    assert_view(&before, &all, last_first, last_first + 3);
    let o = kk(&mut e, KeyKind::PageDown);
    assert!(o == before, "Page Down on the last page changes nothing");
}

#[test]
fn page_up_moves_back_by_nine_to_zero_with_the_selection_on_the_first_row() {
    let (mut e, _, all) = open(0, true);
    // Scroll by two rows (the apple measurement: two Downs past the ninth), then Page Down / Up.
    let o = presses(&mut e, 10, KeyKind::Down);
    assert_view(&o, &all, 2, 10);
    assert_view(&kk(&mut e, KeyKind::PageDown), &all, 11, 11);
    assert_view(&kk(&mut e, KeyKind::PageUp), &all, 2, 2);
    // From 2 back past the start clamps at 0.
    assert_view(&kk(&mut e, KeyKind::PageUp), &all, 0, 0);
    // On the first page nothing moves, even with the selection further down.
    presses(&mut e, 4, KeyKind::Down);
    let before = kk(&mut e, KeyKind::Down);
    assert_view(&before, &all, 0, 5);
    assert!(kk(&mut e, KeyKind::PageUp) == before, "Page Up on the first page changes nothing");
}

#[test]
fn page_keys_with_short_lists_and_the_near_end() {
    for n in [5, 9] {
        let (mut e, o0, all) = open(n, true);
        presses(&mut e, 2, KeyKind::Down);
        let before = kk(&mut e, KeyKind::Down);
        assert_view(&before, &all, 0, 3);
        assert!(kk(&mut e, KeyKind::PageDown) == before && kk(&mut e, KeyKind::PageUp) == before, "n {n}: one page, nothing to turn");
        assert!(o0.handled);
    }
    // Ten candidates: Page Down shows list[1..10], the selection on its first row; Page Up goes back.
    let (mut e, _, all) = open(10, true);
    assert_view(&kk(&mut e, KeyKind::PageDown), &all, 1, 1);
    let end = kk(&mut e, KeyKind::PageDown);
    assert_view(&end, &all, 1, 1);
    assert_view(&kk(&mut e, KeyKind::PageUp), &all, 0, 0);
    // Eleven candidates scrolled by one, then Page Down goes to the end (2), not past it.
    let (mut e, _, all) = open(11, true);
    presses(&mut e, 9, KeyKind::Down);
    assert_view(&kk(&mut e, KeyKind::PageDown), &all, 2, 2);
}

#[test]
fn enter_picks_the_selected_candidate_and_esc_backspace_close() {
    let (mut e, _, all) = open(0, true);
    presses(&mut e, 12, KeyKind::Down); // first 4, selection 12
    let o = kk(&mut e, KeyKind::Enter);
    assert!(o.handled && o.commit.is_empty() && o.selected.is_none() && o.candidates.is_empty() && !o.vertical && o.preedit == all[12]);
    for kind in [KeyKind::Esc, KeyKind::Backspace] {
        let (mut e, _, all) = open(0, true);
        presses(&mut e, 12, KeyKind::Down);
        let o = kk(&mut e, kind);
        assert!(o.handled && o.selected.is_none() && o.candidates.is_empty() && !o.vertical && o.preedit == all[0] && o.cursor_utf16 == 1);
    }
}

#[test]
fn other_keys_close_then_run_from_rule_9_like_horizontal() {
    let (mut e, _, all) = open(0, true);
    let o = k(&mut e, Key::ch('c', 0)); // ㄏ: closes, starts a syllable
    assert!(o.handled && o.selected.is_none() && !o.vertical && o.preedit == format!("{}ㄏ", all[0]));
    let (mut e, _, all) = open(0, true);
    let o = kk(&mut e, KeyKind::Tab); // closes, commits, passes through
    assert!(!o.handled && o.commit == all[0] && o.selected.is_none());
    let (mut e, _, _) = open(0, true);
    let o = kk(&mut e, KeyKind::Home);
    assert!(o.handled && o.selected.is_none() && o.cursor_utf16 == 0);
}

#[test]
fn command_backspace_forgets_and_keeps_the_vertical_window() {
    let (mut e, _, all) = open(0, true);
    presses(&mut e, 10, KeyKind::Down);
    let before = kk(&mut e, KeyKind::Down);
    assert_view(&before, &all, 3, 11);
    let o = k(&mut e, with_mod(KeyKind::Backspace, MOD_COMMAND));
    assert_view(&o, &all, 3, 11); // same window; the engine has nothing learned to drop
}

#[test]
fn mouse_pick_counts_from_the_first_visible_row() {
    let (mut e, _, all) = open(0, true);
    presses(&mut e, 9, KeyKind::Down); // first 1
    let o = e.pick(2).unwrap().unwrap();
    assert!(o.handled && o.selected.is_none() && !o.vertical && o.preedit == all[3]);
    let (mut e, _, all) = open(5, true);
    assert!(e.pick(5).unwrap().is_none(), "outside the five rows");
    assert!(e.pick(4).unwrap().unwrap().preedit == all[4]);
}

#[test]
fn punctuation_window_follows_the_orientation() {
    for vertical in [false, true] {
        let mut e = Engine::with_lexicon(real(), Layout::Standard);
        e.set_candidate_vertical(vertical).unwrap();
        k(&mut e, Key::ch(',', MOD_SHIFT)); // ，
        let o = kk(&mut e, KeyKind::Space);
        assert!(o.selected == Some(0) && o.candidates.len() == 5, "typed mark and its four alternatives");
        assert!(o.vertical == vertical && o.columns == 0, "punctuation window vertical {vertical}");
    }
    let mut e = Engine::with_lexicon(real(), Layout::Standard);
    e.set_candidate_vertical(true).unwrap();
    k(&mut e, Key::ch(',', MOD_SHIFT));
    kk(&mut e, KeyKind::Space);
    let o = kk(&mut e, KeyKind::Down);
    assert!(o.selected == Some(1) && o.vertical, "Down in the punctuation window moves the selection");
}

// ---------- orientation is fixed at open ----------

#[test]
fn switching_while_a_horizontal_window_is_open_changes_nothing_until_it_reopens() {
    let (mut e, o0, _) = open(30, false);
    assert!(!o0.vertical);
    let snap = e.set_candidate_vertical(true).unwrap();
    assert!(snap == o0, "the snapshot equals the output before the call");
    let o = kk(&mut e, KeyKind::Down);
    assert!(!o.vertical && o.columns == 9 && o.candidates.len() == 30, "still the horizontal grid");
    // Still expanded: switching again leaves the grid as it is.
    assert!(e.set_candidate_vertical(false).unwrap() == o && e.set_candidate_vertical(true).unwrap() == o);
    kk(&mut e, KeyKind::Esc);
    let o = kk(&mut e, KeyKind::Space);
    assert!(o.vertical && o.columns == 0 && o.selected == Some(0) && o.candidates.len() == 9, "reopened vertical");
}

#[test]
fn switching_while_a_vertical_window_is_open_changes_nothing_until_it_reopens() {
    let (mut e, _, all) = open(30, true);
    presses(&mut e, 10, KeyKind::Down);
    let before = kk(&mut e, KeyKind::Down);
    assert_view(&before, &all, 3, 11);
    assert!(e.set_candidate_vertical(false).unwrap() == before, "snapshot equals the output before the call");
    assert_view(&kk(&mut e, KeyKind::Down), &all, 4, 12); // still scrolls, not a grid
    assert_view(&kk(&mut e, KeyKind::PageDown), &all, 13, 13);
    kk(&mut e, KeyKind::Esc);
    let o = kk(&mut e, KeyKind::Space);
    assert!(!o.vertical && o.columns == 0 && o.selected == Some(0));
    assert!(kk(&mut e, KeyKind::Down).columns == 9, "reopened horizontal");
}

#[test]
fn no_candidates_and_the_closed_window_carry_no_orientation() {
    let mut e = Engine::with_lexicon(tiny(5), Layout::Standard);
    assert!(!e.set_candidate_vertical(true).unwrap().vertical, "nothing open");
    assert!(!typ(&mut e, "su3").vertical, "composing");
    assert!(typ(&mut e, " ").vertical);
    assert!(!kk(&mut e, KeyKind::Esc).vertical, "closed");
    assert!(!e.reset(ResetMode::Discard).vertical);
}

// ---------- Page Up / Down outside the vertical window ----------

/// Sends Page Up and Page Down (alone, and with Command) and checks each is handled 0, commits nothing and leaves every
/// field of the output and the next key's result as they were.
fn page_keys_do_nothing(e: &mut Engine, state: &Output) {
    for key in [Key::new(KeyKind::PageDown), Key::new(KeyKind::PageUp), with_mod(KeyKind::PageDown, MOD_COMMAND), with_mod(KeyKind::PageUp, MOD_SHIFT)] {
        let o = k(e, key);
        assert!(o == Output { handled: false, ..state.clone() }, "page key passes through, state unchanged");
        assert!(o.commit.is_empty());
    }
}

#[test]
fn page_keys_do_nothing_without_a_vertical_window() {
    // No composition.
    let mut e = Engine::with_lexicon(tiny(30), Layout::Standard);
    e.set_candidate_vertical(true).unwrap();
    let s = e.reset(ResetMode::Discard);
    page_keys_do_nothing(&mut e, &Output { handled: true, ..s });
    // Composition, a pending syllable.
    let s = typ(&mut e, "su3");
    page_keys_do_nothing(&mut e, &s);
    let s = typ(&mut e, "c");
    assert!(s.preedit.ends_with('ㄏ'));
    page_keys_do_nothing(&mut e, &s);
    kk(&mut e, KeyKind::Esc);
    // A horizontal window, then its grid (vertical was set after the window opened).
    let (mut e, s, _) = open(30, false);
    page_keys_do_nothing(&mut e, &s);
    e.set_candidate_vertical(true).unwrap();
    page_keys_do_nothing(&mut e, &s);
    let grid = kk(&mut e, KeyKind::Down);
    assert!(grid.columns == 9);
    page_keys_do_nothing(&mut e, &grid);
    // The window is intact: collapse it again with Up.
    let o = kk(&mut e, KeyKind::Up);
    assert!(o.columns == 0 && o.selected == Some(0));
}

#[test]
fn page_keys_with_a_modifier_in_the_vertical_window_pass_through_and_keep_it() {
    let (mut e, _, all) = open(30, true);
    presses(&mut e, 10, KeyKind::Down);
    let s = kk(&mut e, KeyKind::Down);
    assert_view(&s, &all, 3, 11);
    for m in [MOD_COMMAND, MOD_OPTION, MOD_CONTROL, MOD_CAPSLOCK, MOD_SHIFT] {
        for kind in [KeyKind::PageDown, KeyKind::PageUp] {
            let o = k(&mut e, with_mod(kind, m));
            assert!(o == Output { handled: false, ..s.clone() }, "modifier {m}: not handled, window and selection as they were");
        }
    }
    assert_view(&kk(&mut e, KeyKind::PageDown), &all, 12, 12);
}
