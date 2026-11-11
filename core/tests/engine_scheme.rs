//! Key scheme (docs/spec/keys-and-composition.md §4.1.1, `Engine::set_key_scheme`): every `m` row under both schemes, the whole-page vertical window of scheme 1,
//! the 128-unit limit for the rows that insert units, and a check that setting scheme 0 equals the default (output by output). Each K test asserts the existing
//! rule's result for scheme 0 explicitly. Output/Key hold input text and have no Debug: compare with `assert!(a == b)`. The lexicon is a toy one (no model), so the display of a
//! composition is `base`, whatever the first homophone is; expectations are written against `base` and the window's own list, never against a character.
use core::engine::*;
use core::Lexicon;
use std::sync::Arc;

/// ㄋㄧˇ (`su3`) has `n` one-character homophones (list order from `list_of`); ㄨㄛˇ (`ji3`) has 我.
fn lex(n: usize) -> Arc<Lexicon> {
    let mut text: String = (0..n).map(|i| format!("ㄋㄧˇ {} -1.0\n", char::from_u32(0x4E00 + i as u32).unwrap())).collect();
    text.push_str("ㄨㄛˇ 我 -1.0\n");
    Arc::new(Lexicon::parse(&text).unwrap())
}
fn list_of(l: &Lexicon) -> Vec<String> {
    l.entries(&["ㄋㄧˇ".to_string()]).into_iter().map(|(w, _)| w.to_string()).collect()
}
/// A vertical-window engine; `scheme` None never calls `set_key_scheme`.
fn eng(n: usize, scheme: Option<u32>) -> Engine {
    let mut e = Engine::with_lexicon(lex(n), Layout::Standard);
    e.set_candidate_vertical(true).unwrap();
    if let Some(s) = scheme {
        assert!(e.set_key_scheme(s).unwrap().is_some());
    }
    e
}
fn k(e: &mut Engine, key: Key) -> Output {
    e.key(key).unwrap()
}
fn kk(e: &mut Engine, kind: KeyKind) -> Output {
    k(e, Key::new(kind))
}
fn kn(kind: KeyKind) -> Key {
    Key::new(kind)
}
fn with_mod(kind: KeyKind, modifiers: u32) -> Key {
    Key { kind, ch: '\0', modifiers }
}
fn sh(c: char) -> Key {
    Key::ch(c, MOD_SHIFT)
}
fn ctl(c: char) -> Key {
    Key::ch(c, MOD_CONTROL)
}
/// Plain keys of a string; a space is the Space key.
fn t(s: &str) -> Vec<Key> {
    s.chars().map(|c| if c == ' ' { Key::new(KeyKind::Space) } else { Key::ch(c, 0) }).collect()
}
fn run(e: &mut Engine, keys: &[Key]) -> Output {
    let mut o = None;
    for &key in keys {
        o = Some(k(e, key));
    }
    o.unwrap()
}
fn typ(e: &mut Engine, s: &str) -> Output {
    run(e, &t(s))
}
fn presses(e: &mut Engine, n: usize, kind: KeyKind) -> Output {
    let mut o = None;
    for _ in 0..n {
        o = Some(kk(e, kind));
    }
    o.unwrap()
}
/// `su3`, then Down: the window of ㄋㄧˇ (the same key opens it in both schemes; scheme 1 gives Space to the space unit).
fn open_win(e: &mut Engine) -> Output {
    typ(e, "su3");
    let o = kk(e, KeyKind::Down);
    assert!(o.handled && o.vertical == 1 && o.selected == Some(0) && o.first == 0, "the window is open");
    o
}
fn at(o: &Output) -> usize {
    (o.first + o.selected.unwrap() as u32) as usize
}
/// `su3` typed: the display of the composition.
fn base_of(e: &mut Engine) -> String {
    typ(e, "su3").preedit
}
const SCHEMES: [u32; 2] = [0, 1];

// ---------- the setting ----------

#[test]
fn the_setting_codes_and_the_snapshot() {
    let mut e = eng(30, None);
    for bad in [2u32, 3, u32::MAX] {
        assert!(e.set_key_scheme(bad).unwrap().is_none(), "scheme {bad} changes nothing");
    }
    // Still scheme 0: Space opens the window.
    typ(&mut e, "su3");
    assert!(kk(&mut e, KeyKind::Space).selected == Some(0));
    kk(&mut e, KeyKind::Esc);
    // The snapshot with a window open equals the output before the call, as set_candidate_vertical's does.
    let before = kk(&mut e, KeyKind::Down);
    assert!(before.vertical == 1);
    assert!(e.set_key_scheme(1).unwrap().unwrap() == before);
    assert!(e.set_key_scheme(1).unwrap().unwrap() == before);
    // Nothing shown: handled 1, commit empty.
    let mut f = eng(30, None);
    let s = f.set_key_scheme(1).unwrap().unwrap();
    assert!(s.handled && s.commit.is_empty() && s.preedit.is_empty() && s.candidates.is_empty() && s.selected.is_none());
}

#[test]
fn an_open_window_keeps_the_scheme_it_opened_with() {
    // Opened under 0, then 1: the window still moves by one (no wrap at the first row); a window opened afterwards wraps.
    let mut e = eng(30, Some(0));
    let w = open_win(&mut e);
    e.set_key_scheme(1).unwrap();
    assert!(kk(&mut e, KeyKind::Up) == w, "scheme 0 window: Up at the first row does nothing");
    kk(&mut e, KeyKind::Esc);
    let w = kk(&mut e, KeyKind::Down);
    assert!(w.vertical == 1);
    let o = kk(&mut e, KeyKind::Up);
    assert!(o.first == 27 && at(&o) == 29, "scheme 1 window: Up at the first row goes to the last");
    // Opened under 1, then 0: still whole pages.
    e.set_key_scheme(0).unwrap();
    let o = kk(&mut e, KeyKind::PageUp);
    assert!(o.first == 18 && o.selected == Some(0), "scheme 1 window turns whole pages");
    let o = kk(&mut e, KeyKind::Backspace);
    assert!(o.vertical == 1 && o.first == 18, "scheme 1 window: Backspace is eaten, the window stays");
}

/// The scheme in force for a key is the open window's, read once when the key arrives (docs/spec/keys-and-composition.md §4.1.1): the setting changed with a window open
/// reaches no row, not even one the window closes under and lets the key go on to.
#[test]
fn the_keys_of_an_open_window_follow_the_scheme_it_opened_with() {
    // (a) A scheme 1 window, the setting then 0: every key still takes its scheme 1 row.
    let fresh = |open: u32, then: u32| {
        let mut e = eng(30, Some(open));
        let w = open_win(&mut e);
        e.set_key_scheme(then).unwrap();
        (e, w)
    };
    for key in [sh(','), sh('.'), sh('a'), kn(KeyKind::Tab), with_mod(KeyKind::Tab, MOD_SHIFT), kn(KeyKind::Backspace)] {
        let (mut e, w) = fresh(1, 0);
        assert!(k(&mut e, key) == w, "scheme 1 window: K24a, K24, K17, K12 eat the key and the window stays");
    }
    for (key, mark) in [(',', '，'), ('.', '。')] {
        let (mut e, w) = fresh(1, 0);
        let o = k(&mut e, ctl(key));
        assert!(o.handled && o.selected.is_none() && o.preedit == format!("{}{mark}", w.preedit), "scheme 1 window: K11");
    }
    // (b) A scheme 0 window, the setting then 1: every key still takes its scheme 0 row.
    for (key, full) in [(',', '，'), ('.', '。')] {
        let (mut e, w) = fresh(0, 1);
        let o = k(&mut e, sh(key));
        assert!(o.handled && o.selected.is_none() && o.preedit == format!("{}{full}", w.preedit), "scheme 0 window: rule 2 closes the window and inserts the full-width mark");
        let (mut e, w) = fresh(0, 1);
        let o = k(&mut e, ctl(key));
        assert!(!o.handled && o.candidates == w.candidates && o.selected == Some(0), "scheme 0 window: rule 1 passes Ctrl+{key} on, the window stays");
    }
    let (mut e, w) = fresh(0, 1);
    let o = k(&mut e, sh('a'));
    assert!(!o.handled && o.commit == w.preedit && o.selected.is_none(), "scheme 0 window: the window closes, rule 21 sends the composition and passes Shift+a on (not K7)");
    for key in [kn(KeyKind::Tab), with_mod(KeyKind::Tab, MOD_SHIFT)] {
        let (mut e, w) = fresh(0, 1);
        let o = k(&mut e, key);
        assert!(!o.handled && o.commit == w.preedit && o.selected.is_none(), "scheme 0 window: the window closes, rule 21 sends the composition and passes Tab on (not K6)");
    }
    // The next window opens under the new setting.
    let (mut e, _) = fresh(0, 1);
    kk(&mut e, KeyKind::Esc);
    kk(&mut e, KeyKind::Down);
    assert!(kk(&mut e, KeyKind::Backspace).vertical == 1, "a window opened after the change is a scheme 1 window");
}

// ---------- S1 ----------

#[test]
fn k1_k2_enter_on_an_unfinished_syllable() {
    for scheme in SCHEMES {
        let mut e = eng(30, Some(scheme));
        let before = typ(&mut e, "s");
        assert!(before.preedit == "ㄋ");
        let o = kk(&mut e, KeyKind::Enter);
        if scheme == 0 {
            assert!(o.handled && o.commit == "ㄋ" && o.preedit.is_empty(), "12a");
        } else {
            assert!(o == before, "K1: eaten, nothing changes");
        }
        let mut e = eng(30, Some(scheme));
        typ(&mut e, "s");
        let o = k(&mut e, with_mod(KeyKind::Enter, MOD_SHIFT));
        if scheme == 0 {
            assert!(!o.handled && o.commit == "ㄋ", "12b");
        } else {
            assert!(o.handled && o.commit.is_empty() && o.preedit == "ㄋ", "K2");
        }
    }
}

#[test]
fn k3_shift_letter_on_an_unfinished_syllable() {
    for scheme in SCHEMES {
        let mut e = eng(30, Some(scheme));
        typ(&mut e, "s");
        let o = k(&mut e, sh('a'));
        if scheme == 0 {
            assert!(o.handled && o.commit.is_empty() && o.preedit == "ㄋ", "13: ignored");
        } else {
            assert!(o.handled && o.commit == "A" && o.preedit.is_empty() && o.cursor_utf16 == 0, "K3: the character is committed, never passed on");
            // Composition empty again: a plain key starts a new syllable.
            assert!(typ(&mut e, "s").preedit == "ㄋ");
        }
    }
}

#[test]
fn k3a_shift_letter_after_a_unit() {
    for scheme in SCHEMES {
        let mut e = eng(30, Some(scheme));
        let base = base_of(&mut e);
        typ(&mut e, "s");
        let o = k(&mut e, sh('a'));
        if scheme == 0 {
            assert!(o.handled && o.commit.is_empty() && o.preedit == format!("{base}ㄋ"), "13: ignored");
        } else {
            assert!(o.handled && o.commit.is_empty() && o.preedit == format!("{base}A"), "K3a: ㄋ goes, the letter is a unit, the earlier unit stays");
        }
    }
}

#[test]
fn k4_space_on_an_unfinished_syllable_that_is_not_one() {
    // ㄋ alone is no syllable of the lexicon: Space does nothing under rule 10 already, so no `m` row. Checked on the toy and the real lexicon.
    let real = load_lexicon(&std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../data/lexicon")).unwrap();
    for lx in [lex(30), real] {
        for scheme in SCHEMES {
            let mut e = Engine::with_lexicon(lx.clone(), Layout::Standard);
            e.set_key_scheme(scheme).unwrap();
            let before = typ(&mut e, "s");
            let o = kk(&mut e, KeyKind::Space);
            assert!(o == before && o.commit.is_empty() && o.preedit == "ㄋ", "scheme {scheme}");
        }
    }
}

#[test]
fn k8_k9_k9a_k10_shift_comma_and_period() {
    for (key, full, half) in [(',', '，', '<'), ('.', '。', '>')] {
        for scheme in SCHEMES {
            // K8: nothing composed.
            let mut e = eng(30, Some(scheme));
            let o = k(&mut e, sh(key));
            if scheme == 0 {
                assert!(o.handled && o.preedit == full.to_string() && o.commit.is_empty(), "rule 2");
            } else {
                assert!(!o.handled && o.commit.is_empty() && o.preedit.is_empty(), "K8: passed on");
            }
            // K9: only an unfinished syllable.
            let mut e = eng(30, Some(scheme));
            typ(&mut e, "s");
            let o = k(&mut e, sh(key));
            if scheme == 0 {
                assert!(o.handled && o.commit.is_empty() && o.preedit == full.to_string(), "rule 2");
            } else {
                assert!(o.handled && o.commit == half.to_string() && o.preedit.is_empty(), "K9: committed, never passed on");
            }
            // K9a: a unit before the unfinished syllable.
            let mut e = eng(30, Some(scheme));
            let base = base_of(&mut e);
            typ(&mut e, "s");
            let o = k(&mut e, sh(key));
            let mark = if scheme == 0 { full } else { half };
            assert!(o.handled && o.commit.is_empty() && o.preedit == format!("{base}{mark}"), "K9a, scheme {scheme}");
            // K10: after a unit.
            let mut e = eng(30, Some(scheme));
            let base = base_of(&mut e);
            let o = k(&mut e, sh(key));
            assert!(o.handled && o.commit.is_empty() && o.preedit == format!("{base}{mark}"), "K10, scheme {scheme}");
            // The half-width unit is one unit: Backspace takes it whole.
            let o = kk(&mut e, KeyKind::Backspace);
            assert!(o.preedit == base);
        }
    }
}

// ---------- S2 ----------

#[test]
fn k5_space_in_a_composition() {
    for scheme in SCHEMES {
        let mut e = eng(30, Some(scheme));
        let base = base_of(&mut e);
        let o = kk(&mut e, KeyKind::Space);
        if scheme == 0 {
            assert!(o.vertical == 1 && o.selected == Some(0), "rule 15 opens the window");
            continue;
        }
        assert!(o.handled && o.commit.is_empty() && o.selected.is_none() && o.preedit == format!("{base} "), "K5: a space unit");
        // Typing goes on, Down opens the window of the syllable before the cursor, picking and sending keep the space.
        typ(&mut e, "ji3");
        let o = kk(&mut e, KeyKind::Down);
        assert!(o.selected == Some(0) && o.preedit == format!("{base} 我"));
        let o = kk(&mut e, KeyKind::Enter);
        assert!(o.selected.is_none() && o.preedit == format!("{base} 我"), "the pick closes the window");
        let o = kk(&mut e, KeyKind::Enter);
        assert!(o.handled && o.commit == format!("{base} 我") && o.preedit.is_empty());
    }
}

#[test]
fn k6_tab_in_a_composition() {
    for scheme in SCHEMES {
        for key in [kn(KeyKind::Tab), with_mod(KeyKind::Tab, MOD_SHIFT)] {
            let mut e = eng(30, Some(scheme));
            let base = base_of(&mut e);
            let o = k(&mut e, key);
            if scheme == 0 {
                assert!(!o.handled && o.commit == base, "rule 21: sent, then passed on");
            } else {
                assert!(o.handled && o.commit.is_empty() && o.preedit == base, "K6: eaten");
            }
        }
    }
}

#[test]
fn k7_shift_letter_in_a_composition_and_backspace() {
    for scheme in SCHEMES {
        let mut e = eng(30, Some(scheme));
        let base = base_of(&mut e);
        let o = k(&mut e, sh('a'));
        if scheme == 0 {
            assert!(!o.handled && o.commit == base, "rule 21");
            continue;
        }
        assert!(o.handled && o.commit.is_empty() && o.preedit == format!("{base}A"), "K7");
        let o = typ(&mut e, "ji3");
        assert!(o.preedit == format!("{base}A我"));
        let o = kk(&mut e, KeyKind::Backspace);
        assert!(o.preedit == format!("{base}A"), "the syllable goes");
        let o = kk(&mut e, KeyKind::Backspace);
        assert!(o.preedit == base, "the letter goes whole");
    }
}

#[test]
fn k25_tone_keys_in_a_composition() {
    for scheme in SCHEMES {
        for (key, mark) in [('3', 'ˇ'), ('4', 'ˋ'), ('6', 'ˊ'), ('7', '˙')] {
            let mut e = eng(30, Some(scheme));
            let base = base_of(&mut e);
            let o = k(&mut e, Key::ch(key, 0));
            if scheme == 0 {
                assert!(o.handled && o.commit.is_empty() && o.preedit == base, "rule 16: ignored");
            } else {
                assert!(o.handled && o.commit.is_empty() && o.preedit == format!("{base}{mark}"), "K25 {key}");
            }
        }
    }
}

// ---------- K18: rules 16u and 15d (Up swaps the character before the cursor; Down opens with the shown item first) ----------

/// The toy lexicon plus ㄉㄚˋ 大 with the word 大小 over ㄉㄚˋ ㄉㄚˋ (its second character is not a single of ㄉㄚˋ), ㄇㄚ only in 媽媽 (no single), and two
/// two-syllable words over ㄋㄧˇ ㄨㄛˇ.
fn lex_k18(n: usize) -> Arc<Lexicon> {
    let mut text: String = (0..n).map(|i| format!("ㄋㄧˇ {} -1.0\n", char::from_u32(0x4E00 + i as u32).unwrap())).collect();
    text.push_str("ㄨㄛˇ 我 -1.0\nㄉㄚˋ 大 -1.0\nㄉㄚˋ-ㄉㄚˋ 大小 -0.1\nㄇㄚ-ㄇㄚ 媽媽 -0.1\nㄋㄧˇ-ㄨㄛˇ 甲乙 -0.1\nㄋㄧˇ-ㄨㄛˇ 丙丁 -0.2\n");
    Arc::new(Lexicon::parse(&text).unwrap())
}
fn eng_k18(n: usize, scheme: u32) -> Engine {
    let mut e = Engine::with_lexicon(lex_k18(n), Layout::Standard);
    e.set_candidate_vertical(true).unwrap();
    assert!(e.set_key_scheme(scheme).unwrap().is_some());
    e
}
fn singles(n: usize) -> Vec<String> {
    list_of(&lex_k18(n))
}
fn up(e: &mut Engine) -> Output {
    kk(e, KeyKind::Up)
}
/// The window is open on `first_item`, selected at the top of the first page.
fn opened_on(o: &Output, first_item: &str) -> bool {
    o.selected == Some(0) && o.first == 0 && o.candidates.first().map(String::as_str) == Some(first_item)
}
/// Every item of the open window, page by page (Space turns the page in scheme 1; the window stays open).
fn whole_list(e: &mut Engine, mut o: Output) -> Vec<String> {
    let mut v = Vec::new();
    loop {
        v.extend(o.candidates.clone());
        if o.first as usize + 9 >= o.total as usize {
            return v;
        }
        o = kk(e, KeyKind::Space);
    }
}

#[test]
fn k18_scheme0_ignores_up_in_a_composition() {
    // Rule 16 in scheme 0, field by field, however many presses.
    let mut e = eng_k18(30, 0);
    let before = typ(&mut e, "su3");
    for _ in 0..4 {
        let o = up(&mut e);
        assert!(o == before && o.handled && o.commit.is_empty());
    }
}

#[test]
fn k18_three_presses_swap_third_then_second_then_open_with_the_shown_first() {
    let l = singles(30);
    let mut e = eng_k18(30, 1);
    assert_eq!(typ(&mut e, "su3").preedit, l[0]);
    let o = up(&mut e);
    assert!(o.handled && o.commit.is_empty() && o.selected.is_none() && o.preedit == l[2], "1st: the 3rd item of [shown, rest]");
    let o = up(&mut e);
    assert!(o.selected.is_none() && o.preedit == l[1], "2nd: the 2nd item");
    let o = up(&mut e);
    assert!(opened_on(&o, &l[1]) && o.preedit == l[1], "3rd: the window, the shown character first");
    // The others keep the lexicon order behind it.
    let full = whole_list(&mut e, o);
    let rest: Vec<String> = l.iter().filter(|w| **w != l[1]).cloned().collect();
    assert!(full.len() == l.len() && full[0] == l[1] && full[1..] == rest[..], "the rest keep their order");
    // Back on the first page (Space after the last page), a 4th Up is K13: the last item of the last page.
    kk(&mut e, KeyKind::Space);
    let o = up(&mut e);
    assert!(o.selected.is_some() && at(&o) == l.len() - 1, "4th: K13");
}

#[test]
fn k18_a_shown_character_that_is_not_the_first_single() {
    // Pick the 6th single first: the base is [it, the rest in order], so the presses take the 2nd and 1st singles of the lexicon.
    let l = singles(30);
    let mut e = eng_k18(30, 1);
    open_win(&mut e);
    let o = k(&mut e, Key::ch('6', 0));
    assert!(o.selected.is_none() && o.preedit == l[5]);
    assert!(up(&mut e).preedit == l[1], "1st: base [l5, l0, l1, ...][2]");
    assert!(up(&mut e).preedit == l[0], "2nd: base[1]");
    assert!(opened_on(&up(&mut e), &l[0]), "3rd: the shown one first");
}

#[test]
fn k18_short_lists() {
    // Two singles: the first press takes the 2nd, the second opens. One single: the first press opens.
    let l = singles(2);
    let mut e = eng_k18(2, 1);
    typ(&mut e, "su3");
    assert!(up(&mut e).preedit == l[1]);
    assert!(opened_on(&up(&mut e), &l[1]));
    let mut e = eng_k18(1, 1);
    typ(&mut e, "su3");
    assert!(opened_on(&up(&mut e), &singles(1)[0]));
}

#[test]
fn k18_only_the_character_before_the_cursor_changes() {
    let l = singles(30);
    // At the end of 我 + ㄋㄧˇ.
    let mut e = eng_k18(30, 1);
    typ(&mut e, "ji3su3");
    assert_eq!(up(&mut e).preedit, format!("我{}", l[2]));
    // In the middle: ㄋㄧˇ ㄋㄧˇ, cursor between them.
    let mut e = eng_k18(30, 1);
    let shown = typ(&mut e, "su3su3").preedit;
    kk(&mut e, KeyKind::Left);
    let o = up(&mut e);
    let second: String = shown.chars().nth(1).unwrap().to_string();
    assert_eq!(o.preedit, format!("{}{second}", l[2]));
    assert_eq!(o.cursor_utf16, 1);
}

#[test]
fn k18_ignored_like_rule_16() {
    // Cursor at 0; punctuation (Ctrl+, in scheme 1) before the cursor; a shown character that is not one of the syllable's singles (the 小 of 大小 over
    // ㄉㄚˋ). Each: the output is the one before the key, field by field. A syllable with no single at all cannot be typed: one that only words have
    // (ㄇㄚ in 媽媽) is not accepted (docs/spec/keys-and-composition.md §3.2), checked here too.
    let mut e = eng_k18(30, 1);
    assert_eq!(typ(&mut e, "a8 ").preedit, "ㄇㄚ", "not accepted: still unfinished");
    let mut cases: Vec<(&str, Engine, Output)> = Vec::new();
    let mut e = eng_k18(30, 1);
    typ(&mut e, "su3");
    let o = kk(&mut e, KeyKind::Home);
    cases.push(("cursor 0", e, o));
    let mut e = eng_k18(30, 1);
    typ(&mut e, "su3");
    let o = k(&mut e, ctl(','));
    cases.push(("punctuation", e, o));
    let mut e = eng_k18(30, 1);
    let o = typ(&mut e, "284284");
    assert_eq!(o.preedit, "大小");
    cases.push(("not a single", e, o));
    for (name, mut e, before) in cases {
        let o = up(&mut e);
        assert!(o == before && o.handled, "{name}");
        assert!(up(&mut e) == before, "{name}, again");
    }
}

#[test]
fn k18_a_key_in_between_starts_a_new_run_except_rule_1() {
    let l = singles(30);
    // Up, (Left Right | Down Esc), Up, Up: the run starts again from [l2, l0, l1, ...], so the last Up swaps (to l0) instead of opening.
    for between in [vec![kn(KeyKind::Left), kn(KeyKind::Right)], vec![kn(KeyKind::Down), kn(KeyKind::Esc)]] {
        let mut e = eng_k18(30, 1);
        typ(&mut e, "su3");
        assert!(up(&mut e).preedit == l[2]);
        run(&mut e, &between);
        assert!(up(&mut e).preedit == l[1]);
        let o = up(&mut e);
        assert!(o.selected.is_none() && o.preedit == l[0], "a new run, not the 3rd press");
    }
    // After the 3rd press opened the window and Esc closed it, Up starts a new run (a swap, not K13).
    let mut e = eng_k18(30, 1);
    typ(&mut e, "su3");
    presses(&mut e, 3, KeyKind::Up);
    kk(&mut e, KeyKind::Esc);
    let o = up(&mut e);
    assert!(o.selected.is_none() && o.preedit == l[2], "base [l1, l0, l2, ...][2]");
    // A rule 1 key (Ctrl+A) changes nothing: the third Up still opens.
    let mut e = eng_k18(30, 1);
    typ(&mut e, "su3");
    up(&mut e);
    assert!(!k(&mut e, ctl('a')).handled);
    up(&mut e);
    assert!(opened_on(&up(&mut e), &l[1]));
}

#[test]
fn k18_calls_that_are_not_keys() {
    let l = singles(30);
    // set_profile changes neither the syllables, the cursor nor the shown character: the run goes on, and the third Up opens.
    let mut e = eng_k18(30, 1);
    typ(&mut e, "su3");
    up(&mut e);
    e.set_profile(core::lm::Profile::Formal).unwrap();
    assert!(up(&mut e).preedit == l[1]);
    assert!(opened_on(&up(&mut e), &l[1]));
    // reset and the same syllable typed again: the shown character is not the one the last press put there, so a new run.
    let mut e = eng_k18(30, 1);
    typ(&mut e, "su3");
    up(&mut e);
    e.reset(ResetMode::Discard);
    typ(&mut e, "su3");
    assert!(up(&mut e).preedit == l[2], "a new run");
    assert!(up(&mut e).preedit == l[1]);
    assert!(opened_on(&up(&mut e), &l[1]));
}

#[test]
fn k18_down_then_esc_then_up_is_up() {
    let mut a = eng_k18(30, 1);
    typ(&mut a, "su3");
    let direct = up(&mut a);
    let mut b = eng_k18(30, 1);
    typ(&mut b, "su3");
    kk(&mut b, KeyKind::Down);
    kk(&mut b, KeyKind::Esc);
    assert!(up(&mut b) == direct);
}

#[test]
fn k18_down_opens_with_the_shown_item_first() {
    let l = singles(30);
    // After one Up the window starts with the shown character, selected (rule 15d).
    let mut e = eng_k18(30, 1);
    typ(&mut e, "su3");
    up(&mut e);
    assert!(opened_on(&kk(&mut e, KeyKind::Down), &l[2]));
    // Scheme 0: the 3rd single picked through the window, then Down keeps the lexicon order.
    let mut e = eng_k18(30, 0);
    open_win(&mut e);
    assert!(k(&mut e, Key::ch('3', 0)).preedit == l[2]);
    assert!(opened_on(&kk(&mut e, KeyKind::Down), &l[0]), "scheme 0: the lexicon order");
    // Two syllables: 甲乙 is shown and first already; pick 丙丁 (the window's 2nd item), then Down starts with 丙丁, 甲乙 behind it.
    let mut e = eng_k18(30, 1);
    assert_eq!(typ(&mut e, "su3ji3").preedit, "甲乙");
    let o = kk(&mut e, KeyKind::Down);
    assert!(opened_on(&o, "甲乙") && o.candidates.get(1).map(String::as_str) == Some("丙丁"));
    assert!(k(&mut e, Key::ch('2', 0)).preedit == "丙丁");
    let o = kk(&mut e, KeyKind::Down);
    assert!(opened_on(&o, "丙丁") && o.candidates.get(1).map(String::as_str) == Some("甲乙"));
}

#[test]
fn k19_shift_enter_in_a_composition() {
    for scheme in SCHEMES {
        let mut e = eng(30, Some(scheme));
        let base = base_of(&mut e);
        let o = k(&mut e, with_mod(KeyKind::Enter, MOD_SHIFT));
        assert!(o.commit == base && o.preedit.is_empty(), "the composition is sent as it is");
        assert!(o.handled == (scheme == 1), "19a passes the line break on; K19 does not");
    }
}

// ---------- any state ----------

#[test]
fn k11_ctrl_comma_and_period() {
    for scheme in SCHEMES {
        for (key, mark) in [(',', '，'), ('.', '。')] {
            // S0
            let mut e = eng(30, Some(scheme));
            let o = k(&mut e, ctl(key));
            if scheme == 0 {
                assert!(!o.handled && o.commit.is_empty() && o.preedit.is_empty(), "rule 1");
            } else {
                assert!(o.handled && o.commit.is_empty() && o.preedit == mark.to_string(), "K11 in S0");
            }
            // S1: the unfinished syllable goes.
            let mut e = eng(30, Some(scheme));
            let before = typ(&mut e, "s");
            let o = k(&mut e, ctl(key));
            if scheme == 0 {
                assert!(!o.handled && o == Output { handled: false, ..before.clone() }, "rule 1 changes nothing");
            } else {
                assert!(o.handled && o.preedit == mark.to_string(), "K11 in S1");
            }
            // S2: after the composition.
            let mut e = eng(30, Some(scheme));
            let base = base_of(&mut e);
            let o = k(&mut e, ctl(key));
            if scheme == 0 {
                assert!(!o.handled && o.preedit == base);
            } else {
                assert!(o.handled && o.preedit == format!("{base}{mark}"), "K11 in S2");
            }
            // S3: the window closes first.
            let mut e = eng(30, Some(scheme));
            let w = open_win(&mut e);
            let base = w.preedit.clone();
            let o = k(&mut e, ctl(key));
            if scheme == 0 {
                assert!(!o.handled && o.candidates == w.candidates && o.selected == Some(0), "rule 1: the window stays");
            } else {
                assert!(o.handled && o.selected.is_none() && o.preedit == format!("{base}{mark}"), "K11 in S3");
            }
        }
    }
    // Other Control keys still pass through, in scheme 1.
    let mut e = eng(30, Some(1));
    let before = typ(&mut e, "su3");
    for key in [ctl('a'), ctl('/'), Key::ch(',', MOD_CONTROL | MOD_SHIFT)] {
        let o = k(&mut e, key);
        assert!(!o.handled && o.preedit == before.preedit);
    }
}

// ---------- S3 ----------

#[test]
fn digits_pick_the_nth_row_in_both_schemes() {
    for scheme in SCHEMES {
        let mut e = eng(30, Some(scheme));
        let all = list_of(&lex(30));
        open_win(&mut e);
        let o = k(&mut e, Key::ch('2', 0));
        assert!(o.selected.is_none() && o.preedit == all[1], "scheme {scheme}");
    }
}

#[test]
fn k12_backspace_in_a_window() {
    for scheme in SCHEMES {
        let mut e = eng(30, Some(scheme));
        let w = open_win(&mut e);
        let o = kk(&mut e, KeyKind::Backspace);
        if scheme == 0 {
            assert!(o.selected.is_none() && o.preedit == w.preedit, "rule 7: the window closes");
        } else {
            assert!(o == w, "K12: eaten, the window stays");
        }
    }
}

#[test]
fn k13_up_in_a_window() {
    for scheme in SCHEMES {
        // At the very first row.
        let mut e = eng(30, Some(scheme));
        let w = open_win(&mut e);
        let o = kk(&mut e, KeyKind::Up);
        if scheme == 0 {
            assert!(o == w, "4w: nothing");
        } else {
            assert!(o.first == 27 && o.selected == Some(2) && at(&o) == 29 && o.candidates.len() == 3, "K13: the last row of the last page");
        }
        // Across a page boundary.
        let mut e = eng(30, Some(scheme));
        open_win(&mut e);
        kk(&mut e, KeyKind::PageDown);
        let o = kk(&mut e, KeyKind::Up);
        if scheme == 0 {
            assert!(at(&o) == 8 && o.first == 8, "4w scrolls by one row");
        } else {
            assert!(at(&o) == 8 && o.first == 0 && o.selected == Some(8), "K13: the previous page");
        }
    }
}

#[test]
fn k14_space_and_page_down_in_a_window() {
    for scheme in SCHEMES {
        let mut e = eng(30, Some(scheme));
        open_win(&mut e);
        presses(&mut e, 3, KeyKind::Down);
        let o = kk(&mut e, KeyKind::PageDown);
        assert!(o.first == 9 && at(&o) == 9, "scheme {scheme}: Page Down ends on the first row of the next page");
    }
    let mut e = eng(30, Some(0));
    open_win(&mut e);
    assert!(at(&kk(&mut e, KeyKind::Space)) == 1, "5v: Space is Down");
    // Scheme 1: Space and Page Down turn the page, the position inside it is not kept, and after the last page comes the first.
    for key in [KeyKind::Space, KeyKind::PageDown] {
        let mut e = eng(30, Some(1));
        open_win(&mut e);
        presses(&mut e, 3, KeyKind::Down);
        let o = kk(&mut e, key);
        assert!(o.first == 9 && o.selected == Some(0), "K14");
        presses(&mut e, 2, KeyKind::PageDown);
        let o = kk(&mut e, key);
        assert!(o.first == 0 && o.selected == Some(0), "K14: after the last page (27) the first");
    }
}

#[test]
fn k15_page_up_in_a_window() {
    let mut e = eng(30, Some(0));
    let w = open_win(&mut e);
    assert!(kk(&mut e, KeyKind::PageUp) == w, "5p: nothing at the first page");
    let mut e = eng(30, Some(1));
    open_win(&mut e);
    let o = kk(&mut e, KeyKind::PageUp);
    assert!(o.first == 27 && o.selected == Some(0), "K15: from the first page to the last, its first row");
    let o = kk(&mut e, KeyKind::PageUp);
    assert!(o.first == 18 && o.selected == Some(0));
    kk(&mut e, KeyKind::PageDown);
    let o = kk(&mut e, KeyKind::PageUp);
    assert!(o.first == 18 && o.selected == Some(0), "the previous page, its first row");
}

#[test]
fn k16_down_in_a_window() {
    for scheme in SCHEMES {
        // Across a page boundary.
        let mut e = eng(30, Some(scheme));
        open_win(&mut e);
        presses(&mut e, 8, KeyKind::Down);
        let o = kk(&mut e, KeyKind::Down);
        if scheme == 0 {
            assert!(at(&o) == 9 && o.first == 1, "4v scrolls by one row");
        } else {
            assert!(at(&o) == 9 && o.first == 9 && o.selected == Some(0), "K16: the next page");
        }
        // At the very last row.
        let mut e = eng(30, Some(scheme));
        open_win(&mut e);
        let last = presses(&mut e, 29, KeyKind::Down);
        assert!(at(&last) == 29);
        let o = kk(&mut e, KeyKind::Down);
        if scheme == 0 {
            assert!(o == last, "4v: nothing");
        } else {
            assert!(o.first == 0 && o.selected == Some(0), "K16: back to the first row");
        }
    }
}

#[test]
fn k17_k21_tab_right_left_home_delete_in_a_window() {
    for scheme in SCHEMES {
        // Tab, Shift+Tab: rule 8 closes the window and rule 21 sends the composition.
        for key in [kn(KeyKind::Tab), with_mod(KeyKind::Tab, MOD_SHIFT)] {
            let mut e = eng(30, Some(scheme));
            let w = open_win(&mut e);
            let o = k(&mut e, key);
            if scheme == 0 {
                assert!(!o.handled && !o.commit.is_empty() && o.selected.is_none());
            } else {
                assert!(o == w, "K17: eaten, the window stays");
            }
        }
        // Right and Left move by one in scheme 0, nothing in scheme 1.
        let mut e = eng(30, Some(scheme));
        let w = open_win(&mut e);
        let o = kk(&mut e, KeyKind::Right);
        if scheme == 0 {
            assert!(at(&o) == 1);
            assert!(at(&kk(&mut e, KeyKind::Left)) == 0);
        } else {
            assert!(o == w, "K17: →");
            let down = kk(&mut e, KeyKind::Down);
            assert!(kk(&mut e, KeyKind::Left) == down, "K21: ←");
        }
        for kind in [KeyKind::Home, KeyKind::Delete] {
            let mut e = eng(30, Some(scheme));
            let w = open_win(&mut e);
            let o = kk(&mut e, kind);
            if scheme == 0 {
                assert!(o.selected.is_none() && o.vertical == 0, "rule 8: the window closes");
            } else {
                assert!(o == w, "K21");
            }
        }
    }
}

#[test]
fn k22_end_in_a_window() {
    for scheme in SCHEMES {
        let mut e = eng(30, Some(scheme));
        open_win(&mut e);
        let o = kk(&mut e, KeyKind::End);
        if scheme == 0 {
            assert!(o.selected.is_none(), "rule 8: the window closes");
        } else {
            assert!(o.first == 27 && o.selected == Some(2) && at(&o) == 29, "K22: the last row");
        }
    }
}

#[test]
fn k23_shift_enter_in_a_window() {
    for scheme in SCHEMES {
        let mut e = eng(30, Some(scheme));
        let all = list_of(&lex(30));
        let w = open_win(&mut e);
        kk(&mut e, KeyKind::Down);
        let o = k(&mut e, with_mod(KeyKind::Enter, MOD_SHIFT));
        assert!(o.handled && o.commit.is_empty() && o.selected.is_none());
        if scheme == 0 {
            assert!(o.preedit == all[1], "rule 6: the highlighted row is chosen");
        } else {
            assert!(o.preedit == w.preedit, "K23: the window closes, nothing is chosen, the composition stays");
        }
    }
}

#[test]
fn k24_zhuyin_keys_and_shift_letter_in_a_window() {
    for scheme in SCHEMES {
        for key in [Key::ch('a', 0), Key::ch(';', 0), Key::ch('0', 0), Key::ch('-', 0), Key::ch(',', 0), Key::ch('.', 0), sh('a'), sh('z')] {
            let mut e = eng(30, Some(scheme));
            let w = open_win(&mut e);
            let o = k(&mut e, key);
            if scheme == 0 {
                assert!(o.selected.is_none(), "rule 8: the window closes");
            } else {
                assert!(o == w, "K24: eaten, the window stays");
            }
        }
    }
}

#[test]
fn k24a_shift_comma_and_period_in_a_window() {
    for scheme in SCHEMES {
        for (key, full) in [(',', '，'), ('.', '。')] {
            let mut e = eng(30, Some(scheme));
            let w = open_win(&mut e);
            let o = k(&mut e, sh(key));
            if scheme == 0 {
                assert!(o.selected.is_none() && o.preedit == format!("{}{full}", w.preedit), "rule 2");
            } else {
                assert!(o == w, "K24a: eaten, the window stays, the composition is unchanged");
            }
        }
    }
}

// ---------- the whole-page model ----------

#[test]
fn scheme_1_windows_always_start_on_a_page_boundary() {
    let pool: Vec<Key> = vec![
        kn(KeyKind::Down), kn(KeyKind::Up), kn(KeyKind::Space), kn(KeyKind::PageDown), kn(KeyKind::PageUp), kn(KeyKind::End), kn(KeyKind::Home),
        kn(KeyKind::Left), kn(KeyKind::Right), kn(KeyKind::Tab), kn(KeyKind::Backspace), kn(KeyKind::Delete), kn(KeyKind::Esc), kn(KeyKind::Enter),
        with_mod(KeyKind::Enter, MOD_SHIFT), Key::ch('a', 0), Key::ch('3', 0), Key::ch('9', 0), sh('a'), sh(','),
    ];
    for n in [1, 8, 9, 10, 18, 19, 30, 100] {
        let mut e = eng(n, Some(1));
        let mut seed = 12345u32 + n as u32;
        let mut last = e.reset(ResetMode::Discard);
        let mut windows = 0;
        for _ in 0..3000 {
            if last.vertical != 1 {
                e.reset(ResetMode::Discard);
                typ(&mut e, "su3");
                last = kk(&mut e, KeyKind::Down);
                assert!(last.vertical == 1, "n = {n}: the window opens");
                windows += 1;
            }
            seed = seed.wrapping_mul(1_664_525).wrapping_add(1_013_904_223);
            let key = pool[(seed >> 16) as usize % pool.len()];
            let o = k(&mut e, key);
            if o.vertical == 1 {
                let (first, total) = (o.first as usize, o.total as usize);
                assert!(first % 9 == 0 && total == list_of(&lex(n)).len(), "n = {n}: first {first}");
                assert!(o.candidates.len() == (total - first).min(9) && o.selected.is_some_and(|s| s < o.candidates.len()), "n = {n}: a whole page with a selection");
            }
            last = o;
        }
        assert!(windows > 5, "the walk reopened windows");
    }
}

// ---------- 128 units ----------

/// `su3` then `key` until the 128th unit, which sends everything (`unit` is the character each press adds).
fn fills_128(key: Key, unit: char) {
    let mut e = eng(30, Some(1));
    let base = base_of(&mut e);
    for i in 0..126 {
        let o = k(&mut e, key);
        assert!(o.handled && o.commit.is_empty(), "press {i}");
    }
    let o = k(&mut e, key);
    assert!(o.handled && o.preedit.is_empty(), "the 128th unit sends everything");
    assert!(o.commit.starts_with(&base) && o.commit.chars().count() == 128 && o.commit.ends_with(unit), "{} units sent", o.commit.chars().count());
    assert!(o.commit.chars().skip(1).all(|c| c == unit));
}

#[test]
fn the_rows_that_insert_units_stop_at_128() {
    fills_128(kn(KeyKind::Space), ' '); // K5
    fills_128(sh('a'), 'A'); // K7
    fills_128(sh(','), '<'); // K10
    fills_128(Key::ch('3', 0), 'ˇ'); // K25
    fills_128(ctl(','), '，'); // K11
}

// ---------- scheme 0 is what it was ----------

/// One entry per row: the keys of the K tests above. Used to check that setting scheme 0 equals the default (both are the new code; it cannot see a regression of scheme 0 itself: that is
/// the explicit scheme 0 assertion of each K test and the unchanged suites of the other test files).
fn control_rows() -> Vec<(&'static str, Vec<Key>)> {
    let enter = kn(KeyKind::Enter);
    let senter = with_mod(KeyKind::Enter, MOD_SHIFT);
    let c = |parts: &[Vec<Key>]| parts.concat();
    let su3 = t("su3");
    let win = c(&[t("su3"), vec![kn(KeyKind::Down)]]);
    vec![
        ("K1", c(&[t("s"), vec![enter]])),
        ("K2", c(&[t("s"), vec![senter]])),
        ("K3", c(&[t("s"), vec![sh('a')]])),
        ("K3a", c(&[su3.clone(), t("s"), vec![sh('a')]])),
        ("K4", c(&[t("s"), t(" ")])),
        ("K5", c(&[su3.clone(), t(" "), t("ji3"), vec![kn(KeyKind::Down), enter, enter]])),
        ("K6", c(&[su3.clone(), vec![kn(KeyKind::Tab)]])),
        ("K6s", c(&[su3.clone(), vec![with_mod(KeyKind::Tab, MOD_SHIFT)]])),
        ("K7", c(&[su3.clone(), vec![sh('a')]])),
        ("K8", vec![sh(','), sh('.')]),
        ("K9", c(&[t("s"), vec![sh(',')]])),
        ("K9a", c(&[su3.clone(), t("s"), vec![sh('.')]])),
        ("K10", c(&[su3.clone(), vec![sh(','), sh('.')]])),
        ("K11 S0", vec![ctl(','), ctl('.')]),
        ("K11 S1", c(&[t("s"), vec![ctl(',')]])),
        ("K11 S2", c(&[su3.clone(), vec![ctl('.')]])),
        ("K11 S3", c(&[win.clone(), vec![ctl(',')]])),
        ("K12", c(&[win.clone(), vec![kn(KeyKind::Backspace)]])),
        ("K13", c(&[win.clone(), vec![kn(KeyKind::Up), kn(KeyKind::PageDown), kn(KeyKind::Up)]])),
        ("K14", c(&[win.clone(), vec![kn(KeyKind::Down), kn(KeyKind::PageDown), kn(KeyKind::Space), kn(KeyKind::PageDown), kn(KeyKind::PageDown), kn(KeyKind::Space)]])),
        ("K15", c(&[win.clone(), vec![kn(KeyKind::PageUp), kn(KeyKind::PageDown), kn(KeyKind::PageUp)]])),
        ("K16", c(&[win.clone(), vec![kn(KeyKind::Down); 31]])),
        ("K17 Tab", c(&[win.clone(), vec![kn(KeyKind::Tab)]])),
        ("K17 Right", c(&[win.clone(), vec![kn(KeyKind::Right), kn(KeyKind::Right)]])),
        ("K18", c(&[su3.clone(), vec![kn(KeyKind::Up), kn(KeyKind::Up)]])),
        ("K19", c(&[su3.clone(), vec![senter]])),
        ("K21 Left", c(&[win.clone(), vec![kn(KeyKind::Down), kn(KeyKind::Left)]])),
        ("K21 Home", c(&[win.clone(), vec![kn(KeyKind::Home)]])),
        ("K21 Delete", c(&[win.clone(), vec![kn(KeyKind::Delete)]])),
        ("K22", c(&[win.clone(), vec![kn(KeyKind::End)]])),
        ("K23", c(&[win.clone(), vec![kn(KeyKind::Down), senter]])),
        ("K24", c(&[win.clone(), t("a;0-,."), vec![sh('a')]])),
        ("K24a", c(&[win.clone(), vec![sh(','), sh('.')]])),
        ("K25", c(&[su3.clone(), t("3467")])),
    ]
}

#[test]
fn setting_scheme_0_equals_the_default_field_by_field() {
    for (name, keys) in control_rows() {
        let mut a = eng(30, Some(0));
        let mut b = eng(30, None);
        for (i, &key) in keys.iter().enumerate() {
            assert!(k(&mut a, key) == k(&mut b, key), "{name}: output {i} differs");
        }
    }
}

#[test]
fn every_control_row_runs_to_the_end_in_scheme_1() {
    // The same keys never fail in scheme 1 (an error from `key` would unwrap-panic).
    for (_, keys) in control_rows() {
        let mut e = eng(30, Some(1));
        run(&mut e, &keys);
    }
}
