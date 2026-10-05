//! S3a contract 7.1 behaviour tests. Output/Key hold input text and have no Debug: compare with `assert!(a == b)`.
use core::engine::*;
use std::path::Path;
use std::sync::{Arc, OnceLock};

fn lex() -> Arc<core::Lexicon> {
    static L: OnceLock<Arc<core::Lexicon>> = OnceLock::new();
    L.get_or_init(|| load_lexicon(&Path::new(env!("CARGO_MANIFEST_DIR")).join("../data/lexicon")).unwrap()).clone()
}
fn eng(l: Layout) -> Engine {
    Engine::with_lexicon(lex(), l)
}
fn std() -> Engine {
    eng(Layout::Standard)
}
fn plain(c: char) -> Key {
    if c == ' ' { Key::new(KeyKind::Space) } else { Key::ch(c, 0) }
}
fn k(e: &mut Engine, key: Key) -> Output {
    e.key(key).unwrap()
}
fn kk(e: &mut Engine, kind: KeyKind) -> Output {
    k(e, Key::new(kind))
}
fn typ(e: &mut Engine, s: &str) -> Output {
    let mut last = None;
    for c in s.chars() {
        last = Some(k(e, plain(c)));
    }
    last.unwrap()
}
fn presses(e: &mut Engine, n: usize, kind: KeyKind) {
    for _ in 0..n {
        kk(e, kind);
    }
}
const NIHAO: &str = "su3cl3"; // standard layout: ㄋㄧˇ ㄏㄠˇ
const NI: &str = "ㄋㄧˇ";
const HAO: &str = "ㄏㄠˇ";
fn blank(handled: bool) -> Output {
    Output { handled, commit: String::new(), preedit: String::new(), cursor_utf16: 0, candidates: vec![], selected: None, columns: 0, first: 0, total: 0 }
}
/// Independent expectation of the candidate list. `end`: readings end at `ks.len()`; otherwise they start at 0.
fn expect_cands(ks: &[&str], from_start: bool) -> Vec<String> {
    let l = lex();
    let mut seen = std::collections::HashSet::new();
    let mut out = vec![];
    for n in (1..=l.max_len.min(ks.len())).rev() {
        let part = if from_start { &ks[..n] } else { &ks[ks.len() - n..] };
        let key: Vec<String> = part.iter().map(|s| s.to_string()).collect();
        for (w, _) in l.entries(&key) {
            if seen.insert(w.to_string()) {
                out.push(w.to_string());
            }
        }
    }
    out
}
fn cands(ks: &[&str]) -> Vec<String> {
    expect_cands(ks, false)
}
/// Top-1 of a single syllable (what the engine displays for it).
fn top(s: &str) -> String {
    cands(&[s])[0].clone()
}
/// Open candidates for "ㄕˋ" (many homophones) and return the full expected list.
fn open_shi(e: &mut Engine) -> Vec<String> {
    typ(e, "g4 ");
    let all = cands(&["ㄕˋ"]);
    assert!(all.len() > 27, "need at least 4 pages");
    all
}

// ---------- layouts ----------
const SYMS: &str = "ㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓㄔㄕㄖㄗㄘㄙㄧㄨㄩㄚㄛㄜㄝㄞㄟㄠㄡㄢㄣㄤㄥㄦ";
// Contract table 1 transcribed independently of engine.rs, one key per symbol in SYMS order.
const STD_T: &str = "1qaz2wsxedcrfv5tgbyhnujm8ik,9ol.0p;/-";
const ETEN_T: &str = "bpmfdtnlvkhg7c,./j;'sexuaorwiqzy890-=";

fn layout_covers(l: Layout, table: &str, tones: [char; 4]) {
    assert!(SYMS.chars().count() == 37 && table.chars().count() == 37);
    for (sym, key) in SYMS.chars().zip(table.chars()) {
        let o = k(&mut eng(l), plain(key));
        assert!(o.handled && o.preedit == sym.to_string(), "symbol key");
    }
    let (m, a) = (table.chars().nth(2).unwrap(), if l == Layout::Standard { '8' } else { 'a' }); // ㄇ ㄚ
    for (i, tk) in std::iter::once(' ').chain(tones).enumerate() {
        let mut e = eng(l);
        typ(&mut e, &format!("{m}{a}"));
        let o = k(&mut e, plain(tk));
        assert!(o.handled && o.preedit == top(&format!("ㄇㄚ{}", ["", "ˊ", "ˇ", "ˋ", "˙"][i])), "tone key");
    }
}
#[test]
fn layout_standard_covers_37_symbols_and_5_tones() {
    layout_covers(Layout::Standard, STD_T, ['6', '3', '4', '7']);
}
#[test]
fn layout_eten_covers_37_symbols_and_5_tones() {
    layout_covers(Layout::Eten, ETEN_T, ['2', '3', '4', '1']);
}

// ---------- row 1 ----------
#[test]
fn row1_passthrough_keys_leave_no_trace() {
    let junk = [
        Key::ch('a', MOD_COMMAND),
        Key::ch('s', MOD_OPTION),
        Key::ch('s', MOD_CAPSLOCK),
        Key::ch('a', MOD_CONTROL),
        Key::ch('\\', MOD_CONTROL | MOD_SHIFT),
        Key { kind: KeyKind::Left, ch: '\0', modifiers: MOD_CONTROL },
        Key { kind: KeyKind::Space, ch: '\0', modifiers: MOD_COMMAND },
    ];
    // composition, open candidates, selecting, pending syllable
    let script: Vec<Key> = "su3cl3 ".chars().map(plain).chain([Key::new(KeyKind::Down)]).chain("1su".chars().map(plain)).collect();
    let (mut a, mut b) = (std(), std());
    for key in &script {
        assert!(k(&mut a, *key) == k(&mut b, *key));
        for j in &junk {
            let o = k(&mut b, *j);
            assert!(!o.handled && o.commit.is_empty());
        }
    }
    for key in [plain('3'), Key::new(KeyKind::Left), Key::new(KeyKind::Enter)] {
        assert!(k(&mut a, key) == k(&mut b, key));
    }
}

// ---------- row 2 (s3d: punctuation stays in the composition) ----------
const PUNCT_TABLE: [(char, char); 13] = [
    (',', '，'), ('.', '。'), ('/', '？'), ('1', '！'), (';', '：'), ('[', '『'), (']', '』'), ('9', '（'), ('0', '）'),
    ('`', '～'), ('\\', '｜'), ('\'', '“'), ('=', '＋'),
];
/// Unshifted keys that are punctuation (Apple's Zhuyin, key probe 2026-10-05).
const PLAIN_PUNCT_TABLE: [(char, char); 6] = [('[', '「'), (']', '」'), ('\\', '、'), ('\'', '‘'), ('=', '＝'), ('`', '·')];

/// The bracket row without Shift goes into the composition like the Shift table; in Eten, ' and = stay
/// Zhuyin keys (they are symbols there), the others still give punctuation.
#[test]
fn row2_unshifted_bracket_row_is_punctuation() {
    for (c, p) in PLAIN_PUNCT_TABLE {
        let mut e = std();
        typ(&mut e, NIHAO);
        let o = k(&mut e, plain(c));
        assert!(o.handled && o.commit.is_empty() && o.preedit == format!("你好{p}"), "{c}");
        let o = kk(&mut e, KeyKind::Enter);
        assert!(o.commit == format!("你好{p}"), "{c}");
    }
    for (c, p) in PLAIN_PUNCT_TABLE {
        let o = k(&mut eng(Layout::Eten), plain(c));
        if c == '\'' || c == '=' {
            let zy = if c == '\'' { "ㄘ" } else { "ㄦ" };
            assert!(o.handled && o.preedit == zy, "Eten {c} is the Zhuyin key {zy}");
        } else {
            assert!(o.handled && o.preedit == p.to_string(), "Eten {c}");
        }
    }
}

/// s3d acceptance 1: every punctuation key (and Ctrl+\) goes into the composition at the cursor, drops
/// the pending syllable, closes candidates and commits nothing; Enter commits it with the rest.
#[test]
fn row2_punctuation_goes_into_the_composition() {
    for (c, p) in PUNCT_TABLE {
        let mut e = std();
        typ(&mut e, NIHAO);
        typ(&mut e, "c"); // pending ㄏ is dropped
        let o = k(&mut e, Key::ch(c, MOD_SHIFT));
        assert!(o.handled && o.commit.is_empty() && o.preedit == format!("你好{p}") && o.candidates.is_empty(), "{c}");
        assert!(o.cursor_utf16 == 3, "{c}");
        let o = kk(&mut e, KeyKind::Enter);
        assert!(o.handled && o.commit == format!("你好{p}") && o.preedit.is_empty(), "{c}");
        assert!(kk(&mut e, KeyKind::Enter) == blank(false)); // state fully cleared
    }
    // Ctrl+\ with candidates open: they close, 、 goes in after the composition.
    let mut e = std();
    let shown = typ(&mut e, "su3").preedit;
    typ(&mut e, " "); // candidates open
    let o = k(&mut e, Key::ch('\\', MOD_CONTROL));
    assert!(o.handled && o.commit.is_empty() && o.preedit == format!("{shown}、") && o.candidates.is_empty() && o.selected.is_none());
    // Ctrl+\ drops a pending syllable too.
    let mut e = std();
    typ(&mut e, NIHAO);
    typ(&mut e, "c");
    assert!(k(&mut e, Key::ch('\\', MOD_CONTROL)).preedit == "你好、");
    // Empty composition: the punctuation alone is the composition; Enter commits it.
    let mut e = std();
    let o = k(&mut e, Key::ch(',', MOD_SHIFT));
    assert!(o.handled && o.commit.is_empty() && o.preedit == "，");
    assert!(kk(&mut e, KeyKind::Enter).commit == "，");
    // 你好，我是 (contract acceptance 1): both sides differ, so a duplicated stretch would show; the
    // whole sentence is committed by Enter only.
    let woshi = typ(&mut std(), "ji3g4").preedit; // ㄨㄛˇ ㄕˋ alone
    assert!(woshi.chars().count() == 2 && woshi != "你好");
    let mut e = std();
    typ(&mut e, NIHAO);
    k(&mut e, Key::ch(',', MOD_SHIFT));
    let o = typ(&mut e, "ji3g4");
    assert!(o.commit.is_empty() && o.preedit == format!("你好，{woshi}"));
    assert!(kk(&mut e, KeyKind::Enter).commit == format!("你好，{woshi}"));
    // Esc clears it; reset(Commit) returns it.
    let mut e = std();
    typ(&mut e, NIHAO);
    k(&mut e, Key::ch('.', MOD_SHIFT));
    assert!(e.reset(ResetMode::Commit).commit == "你好。");
    typ(&mut e, NIHAO);
    k(&mut e, Key::ch('.', MOD_SHIFT));
    assert!(kk(&mut e, KeyKind::Esc).preedit.is_empty());
}

/// s3d acceptance 2: the cursor and Backspace/Delete treat punctuation as one cell.
#[test]
fn punctuation_is_one_cell_for_editing() {
    let mut e = std();
    typ(&mut e, NIHAO);
    k(&mut e, Key::ch(',', MOD_SHIFT));
    typ(&mut e, NIHAO); // 你好，你好, cursor 5
    for want in [4, 3, 2] {
        assert!(kk(&mut e, KeyKind::Left).cursor_utf16 == want);
    }
    let o = kk(&mut e, KeyKind::Delete); // the punctuation right of the cursor
    assert!(o.preedit == "你好你好" && o.cursor_utf16 == 2);
    let mut e = std();
    typ(&mut e, NIHAO);
    k(&mut e, Key::ch(',', MOD_SHIFT));
    let o = kk(&mut e, KeyKind::Backspace); // the punctuation left of the cursor
    assert!(o.preedit == "你好" && o.cursor_utf16 == 2);
    k(&mut e, Key::ch(',', MOD_SHIFT));
    assert!(kk(&mut e, KeyKind::Home).cursor_utf16 == 0);
    assert!(kk(&mut e, KeyKind::End).cursor_utf16 == 3);
    // Punctuation inserted in the middle shifts what follows.
    kk(&mut e, KeyKind::Left);
    let o = k(&mut e, Key::ch('.', MOD_SHIFT));
    assert!(o.preedit == "你好。，" && o.cursor_utf16 == 3);
}

/// s3d acceptance 3 as amended by s3e: right after punctuation, space and down list that mark's
/// candidates (the typed mark first); the syllable range after punctuation is unchanged by it.
#[test]
fn candidates_never_span_punctuation() {
    let default_comma: Vec<String> = ["，", "〈", "《", "︿", "︽"].map(String::from).to_vec();
    let mut e = std();
    typ(&mut e, NIHAO);
    k(&mut e, Key::ch(',', MOD_SHIFT));
    assert!(kk(&mut e, KeyKind::Space).candidates == default_comma);
    kk(&mut e, KeyKind::Esc);
    assert!(kk(&mut e, KeyKind::Down).candidates == default_comma);
    kk(&mut e, KeyKind::Esc);
    let o = typ(&mut e, NIHAO);
    let o2 = kk(&mut e, KeyKind::Space);
    assert!(o2.candidates[..] == cands(&[NI, HAO])[..9] && o2.preedit == o.preedit);
    // Cursor 0 with punctuation first: that mark's candidates.
    let mut e = std();
    k(&mut e, Key::ch(',', MOD_SHIFT));
    typ(&mut e, NIHAO);
    kk(&mut e, KeyKind::Home);
    assert!(kk(&mut e, KeyKind::Space).candidates == default_comma);
}

/// s3e acceptance 1: the built-in table (no `set_punctuation`).
#[test]
fn default_punctuation_candidates() {
    let list = |c: char| {
        let mut e = std();
        k(&mut e, if c == '[' { plain(c) } else { Key::ch(c, MOD_SHIFT) });
        kk(&mut e, KeyKind::Space).candidates
    };
    assert!(list('[').contains(&"『".to_string()) && list('[')[0] == "「");
    assert!(list('/') == vec!["？".to_string()]);
    // 『 (Shift+[) and the plain quote reach their pair and the closing quote without Apple's table.
    let mut e = std();
    k(&mut e, Key::ch('[', MOD_SHIFT));
    let c = kk(&mut e, KeyKind::Space).candidates;
    assert!(c[0] == "『" && c.contains(&"「".to_string()));
    let mut e = std();
    k(&mut e, plain('\''));
    assert!(kk(&mut e, KeyKind::Space).candidates == vec!["‘".to_string(), "’".to_string()]);
}

/// s3e acceptance 1: a table from the shell; choosing an alternative replaces the mark, Enter commits
/// it, and reopening lists the same candidates with the typed mark first.
#[test]
fn chosen_punctuation_alternative_replaces_the_mark() {
    let mut e = std();
    assert!(e.set_punctuation("，\t、\t《\n"));
    typ(&mut e, NIHAO);
    k(&mut e, Key::ch(',', MOD_SHIFT));
    let want: Vec<String> = ["，", "、", "《"].map(String::from).to_vec();
    assert!(kk(&mut e, KeyKind::Space).candidates == want);
    let o = k(&mut e, plain('2'));
    assert!(o.preedit == "你好、" && o.candidates.is_empty() && o.cursor_utf16 == 3);
    assert!(kk(&mut e, KeyKind::Space).candidates == want);
    kk(&mut e, KeyKind::Esc);
    assert!(kk(&mut e, KeyKind::Enter).commit == "你好、");
}

/// s3e acceptance 1: a multi-character alternative keeps the caret right.
#[test]
fn multi_char_punctuation_keeps_the_caret() {
    let mut e = std();
    assert!(e.set_punctuation("。\t⋯⋯"));
    typ(&mut e, NIHAO);
    k(&mut e, Key::ch('.', MOD_SHIFT));
    kk(&mut e, KeyKind::Space);
    let o = k(&mut e, plain('2'));
    assert!(o.preedit == "你好⋯⋯" && o.cursor_utf16 == 4);
    let o = typ(&mut e, "su3");
    let ni = typ(&mut std(), "su3").preedit;
    assert!(o.preedit == format!("你好⋯⋯{ni}") && o.cursor_utf16 == 5);
    let o = kk(&mut e, KeyKind::Left);
    assert!(o.cursor_utf16 == 4);
    let o = kk(&mut e, KeyKind::Left);
    assert!(o.cursor_utf16 == 2);
}

/// s3e acceptance 2 at the Rust level: every invalid table is refused and keeps the previous one;
/// blank lines and a trailing newline are fine; a repeated mark overrides.
#[test]
fn set_punctuation_validates_and_keeps_the_old_table() {
    let mut e = std();
    let comma = |e: &mut Engine| {
        e.reset(ResetMode::Discard);
        k(e, Key::ch(',', MOD_SHIFT));
        kk(e, KeyKind::Space).candidates
    };
    assert!(e.set_punctuation("，\t、\n\n"));
    let kept = comma(&mut e);
    assert!(kept == vec!["，".to_string(), "、".to_string()]);
    let too_many_lines = "，\t、\n".repeat(1001);
    let too_big = format!("，\t{}", "、".repeat(30_000));
    // The last one has a valid line before the invalid one: nothing of it may apply.
    for bad in ["", "\n\n", "，\n", "，\t", "，\t、\t", "，，\t、", too_many_lines.as_str(), too_big.as_str(), "，\t《\n，\n"] {
        assert!(!e.set_punctuation(bad), "accepted an invalid table ({} bytes)", bad.len());
        assert!(comma(&mut e) == kept, "a refused table changed the old one");
    }
    assert!(e.set_punctuation("，\t、\n，\t《\n"));
    assert!(comma(&mut e) == vec!["，".to_string(), "《".to_string()]);
    // Success replaces the whole table: a mark the new table does not list has no alternatives
    // left (the built-in 「 list is gone).
    e.reset(ResetMode::Discard);
    k(&mut e, plain('['));
    assert!(kk(&mut e, KeyKind::Space).candidates == vec!["「".to_string()]);
}

/// s3d acceptance 5: punctuation counts toward the 40-token limit.
#[test]
fn punctuation_counts_toward_the_limit() {
    let mut e = std();
    let mut shown = String::new();
    for _ in 1..40 {
        shown = typ(&mut e, "su3").preedit;
    }
    let o = k(&mut e, Key::ch(',', MOD_SHIFT));
    assert!(o.handled && o.commit == format!("{shown}，") && o.preedit.is_empty() && o.cursor_utf16 == 0);
}

// ---------- rows 3-8, 15 ----------
#[test]
fn row15_candidate_range_order_and_dedup() {
    // end of two syllables: 2-syllable words first, then 1-syllable words of the last syllable
    let mut e = std();
    typ(&mut e, NIHAO);
    let o = kk(&mut e, KeyKind::Down);
    let want = cands(&[NI, HAO]);
    assert!(o.handled && o.selected == Some(0) && o.candidates[..] == want[..9] && want[0] == "你好");
    // cursor in the middle of three syllables: range ends at the cursor
    kk(&mut e, KeyKind::Esc);
    kk(&mut e, KeyKind::Esc);
    typ(&mut e, "su3cl3su3");
    kk(&mut e, KeyKind::Left);
    let o = kk(&mut e, KeyKind::Space);
    assert!(o.candidates[..] == cands(&[NI, HAO])[..9]);
    // cursor 0: range starts at 0
    kk(&mut e, KeyKind::Esc);
    kk(&mut e, KeyKind::Home);
    let o = kk(&mut e, KeyKind::Down);
    assert!(o.candidates[..] == expect_cands(&[NI, HAO, NI], true)[..9]);
    // dedup: no string twice
    let all = cands(&[NI, HAO]);
    let set: std::collections::HashSet<_> = all.iter().collect();
    assert!(set.len() == all.len());
}
#[test]
fn rows3_to_8_candidate_keys() {
    let mut e = std();
    let all = open_shi(&mut e);
    let page = |p: usize| all[p * 9..((p + 1) * 9).min(all.len())].to_vec();
    // 4 (s3b2 8.2): collapsed down expands the grid and leaves the selection alone; up on the first
    // row collapses again
    let o = kk(&mut e, KeyKind::Down);
    assert!(o.selected == Some(0) && o.columns == 6 && o.first == 0 && o.total as usize == all.len() && o.candidates == all[..30]);
    let o = kk(&mut e, KeyKind::Up);
    assert!(o.selected == Some(0) && o.columns == 0 && o.candidates == page(0));
    // 4: up/right move across pages, clamped at both ends
    for i in 1..=10 {
        let o = kk(&mut e, KeyKind::Right);
        assert!(o.selected == Some(i % 9) && o.candidates == page(i / 9));
    }
    for i in (0..10).rev() {
        let o = kk(&mut e, KeyKind::Up);
        assert!(o.selected == Some(i % 9) && o.candidates == page(i / 9));
    }
    assert!(kk(&mut e, KeyKind::Up).selected == Some(0));
    // 4: left/right move like up/down (horizontal bar), across pages, clamped at both ends
    for i in 1..=10 {
        let o = kk(&mut e, KeyKind::Right);
        assert!(o.selected == Some(i % 9) && o.candidates == page(i / 9));
    }
    for i in (0..10).rev() {
        let o = kk(&mut e, KeyKind::Left);
        assert!(o.selected == Some(i % 9) && o.candidates == page(i / 9));
    }
    assert!(kk(&mut e, KeyKind::Left).selected == Some(0));
    // 5: space = next page, wraps from the last page to the first
    let pages = (all.len() + 8) / 9;
    for p in 1..pages {
        let o = kk(&mut e, KeyKind::Space);
        assert!(o.candidates == page(p) && o.selected == Some(0));
    }
    assert!(kk(&mut e, KeyKind::Space).candidates == page(0));
    // 3: digit picks the n-th of the current page and closes
    kk(&mut e, KeyKind::Space);
    let o = k(&mut e, plain('3'));
    assert!(o.handled && o.selected.is_none() && o.candidates.is_empty() && o.preedit == all[9 + 2]);
    // 3: a digit beyond the (short) last page is ignored, candidates stay open
    let mut e = std();
    open_shi(&mut e);
    presses(&mut e, pages - 1, KeyKind::Space);
    let n_last = all.len() - (pages - 1) * 9;
    if n_last < 9 {
        let o = k(&mut e, plain('9'));
        assert!(o.handled && o.selected == Some(0) && o.candidates.len() == n_last);
    }
    // 6: enter takes the selected one
    let mut e = std();
    open_shi(&mut e);
    presses(&mut e, 2, KeyKind::Right);
    let o = kk(&mut e, KeyKind::Enter);
    assert!(o.handled && o.commit.is_empty() && o.selected.is_none() && o.preedit == all[2]);
    // 7: esc and backspace close without changing anything
    for kind in [KeyKind::Esc, KeyKind::Backspace] {
        let mut e = std();
        open_shi(&mut e);
        let o = kk(&mut e, kind);
        assert!(o.handled && o.selected.is_none() && o.candidates.is_empty() && o.preedit == top("ㄕˋ") && o.cursor_utf16 == 1);
    }
    // 8: any other key closes, then runs from rule 9: a zhuyin key starts a syllable
    let mut e = std();
    open_shi(&mut e);
    let o = k(&mut e, plain('c'));
    assert!(o.handled && o.selected.is_none() && o.preedit == format!("{}ㄏ", top("ㄕˋ")));
    // 8 then 21: Tab closes, commits the composition, passes through
    let mut e = std();
    open_shi(&mut e);
    let o = kk(&mut e, KeyKind::Tab);
    assert!(!o.handled && o.commit == top("ㄕˋ") && o.selected.is_none());
    // 8 then 17: Left closes candidates... Left is a page key (rule 5), Home is "other": closes and moves the cursor
    let mut e = std();
    open_shi(&mut e);
    let o = kk(&mut e, KeyKind::Home);
    assert!(o.handled && o.selected.is_none() && o.cursor_utf16 == 0);
}

// ---------- s3b2 8.2: expanded grid ----------
/// Position of the selection in the whole list.
fn at(o: &Output) -> usize {
    (o.first + o.selected.unwrap() as u32) as usize
}
/// Candidates for ㄕˋ opened and expanded (6 columns, more than 4 pages).
fn expanded_shi(e: &mut Engine) -> Vec<String> {
    let all = open_shi(e);
    let o = kk(e, KeyKind::Down);
    assert!(o.columns == 6 && at(&o) == 0);
    all
}
#[test]
fn grid_expanded_keys() {
    let mut e = std();
    let all = expanded_shi(&mut e);
    assert!(at(&kk(&mut e, KeyKind::Down)) == 6);
    assert!(at(&kk(&mut e, KeyKind::Space)) == 12);
    assert!(at(&kk(&mut e, KeyKind::Up)) == 6);
    assert!(at(&kk(&mut e, KeyKind::Left)) == 5);
    assert!(at(&kk(&mut e, KeyKind::Right)) == 6);
    assert!(at(&kk(&mut e, KeyKind::Right)) == 7);
    // up on the first row collapses with the selection unchanged
    let mut e = std();
    expanded_shi(&mut e);
    let o = kk(&mut e, KeyKind::Up);
    assert!(o.columns == 0 && o.selected == Some(0) && o.candidates == all[..9]);
    // enter takes the selected one
    let mut e = std();
    expanded_shi(&mut e);
    kk(&mut e, KeyKind::Down);
    kk(&mut e, KeyKind::Right);
    let o = kk(&mut e, KeyKind::Enter);
    assert!(o.selected.is_none() && o.columns == 0 && o.total == 0 && o.preedit == all[7]);
    // esc and backspace close, they do not collapse
    for kind in [KeyKind::Esc, KeyKind::Backspace] {
        let mut e = std();
        expanded_shi(&mut e);
        let o = kk(&mut e, kind);
        assert!(o.selected.is_none() && o.candidates.is_empty() && o.columns == 0 && o.preedit == top("ㄕˋ"));
    }
}
#[test]
fn grid_scrolls_to_keep_the_selected_row_visible() {
    let mut e = std();
    let all = expanded_shi(&mut e);
    let o = kk(&mut e, KeyKind::Down);
    assert!(o.first == 0 && o.candidates == all[..30] && o.selected == Some(6) && o.total as usize == all.len());
    presses(&mut e, 3, KeyKind::Down);
    let o = kk(&mut e, KeyKind::Down); // row 5: top moves to 1
    assert!(o.first == 6 && at(&o) == 30 && o.selected == Some(24));
    assert!(o.candidates[..] == all[6..(6 + 30).min(all.len())]);
    assert!(kk(&mut e, KeyKind::Up).first == 6); // row 4 is still visible
    presses(&mut e, 2, KeyKind::Up);
    let o = kk(&mut e, KeyKind::Up); // row 1
    assert!(o.first == 6 && at(&o) == 6 && o.selected == Some(0));
    let o = kk(&mut e, KeyKind::Up); // row 0
    assert!(o.first == 0 && at(&o) == 0 && o.columns == 6, "row 1 up moves to row 0");
    // collapsed on page 3 then expanded: the selected row is visible at once
    let mut e = std();
    open_shi(&mut e);
    presses(&mut e, 2, KeyKind::Space);
    let o = kk(&mut e, KeyKind::Down); // sel 18, row 3
    assert!(o.columns == 6 && at(&o) == 18 && o.first == 0);
}
#[test]
fn grid_digits_pick_within_the_selected_row() {
    let mut e = std();
    let all = expanded_shi(&mut e);
    kk(&mut e, KeyKind::Down);
    let o = k(&mut e, plain('3'));
    assert!(o.selected.is_none() && o.preedit == all[8]);
    // 7, 8, 9 on six columns: consumed, nothing changes, candidates stay open
    let mut e = std();
    expanded_shi(&mut e);
    let before = kk(&mut e, KeyKind::Down);
    for d in ['7', '8', '9'] {
        let o = k(&mut e, plain(d));
        assert!(o.handled && o == before);
    }
}
#[test]
fn grid_short_last_row() {
    let mut e = std();
    let all = expanded_shi(&mut e);
    let len = all.len();
    let (last, rem) = ((len - 1) / 6, len - (len - 1) / 6 * 6);
    assert!(rem < 6 && last >= 2, "fixture: a short last row");
    // go to the penultimate row, last column
    let target = (last - 1) * 6 + 5;
    presses(&mut e, target, KeyKind::Right);
    let o = kk(&mut e, KeyKind::Down);
    assert!(at(&o) == len - 1, "down into a shorter row ends at its last candidate");
    assert!(at(&kk(&mut e, KeyKind::Down)) == len - 1, "down on the last row does nothing");
    // space on the last row wraps to the first row, same column
    assert!(at(&kk(&mut e, KeyKind::Space)) == rem - 1);
    // space from the penultimate row also lands on the last candidate
    let mut e = std();
    expanded_shi(&mut e);
    presses(&mut e, target, KeyKind::Right);
    assert!(at(&kk(&mut e, KeyKind::Space)) == len - 1);
    // a digit beyond the short row is consumed
    let before = kk(&mut e, KeyKind::Left);
    let o = k(&mut e, plain(char::from_digit(rem as u32 + 1, 10).unwrap()));
    assert!(o == before && o.selected.is_some());
    // a digit inside it picks
    let o = k(&mut e, plain('1'));
    assert!(o.selected.is_none() && o.preedit == all[last * 6]);
}
#[test]
fn grid_punctuation_has_three_columns() {
    let alts: Vec<String> = ["，", "〈", "《", "︿", "︽"].map(String::from).to_vec();
    let mut e = std();
    k(&mut e, Key::ch(',', MOD_SHIFT));
    kk(&mut e, KeyKind::Space);
    let o = kk(&mut e, KeyKind::Down);
    assert!(o.columns == 3 && o.candidates == alts && o.total == 5 && o.first == 0 && at(&o) == 0);
    let before = kk(&mut e, KeyKind::Down);
    assert!(at(&before) == 3);
    let o = k(&mut e, plain('4'));
    assert!(o == before, "digit above the column count is consumed");
    let o = k(&mut e, plain('3'));
    assert!(o == before, "digit past a short last row is consumed");
    let o = k(&mut e, plain('2'));
    assert!(o.selected.is_none() && o.preedit == alts[4]);
}
/// Types the syllables (standard layout, a first tone is the space key) and expands the candidates.
fn expanded_for(syls: &[&str]) -> Output {
    let mut e = std();
    let layout = Layout::Standard;
    for syl in syls {
        let mut toned = false;
        for c in syl.chars() {
            match layout.key_of_tone(c) {
                Some(tk) => {
                    k(&mut e, Key::ch(tk, 0));
                    toned = true;
                }
                None => {
                    k(&mut e, Key::ch(layout.key_of_symbol(c).unwrap(), 0));
                }
            }
        }
        if !toned {
            kk(&mut e, KeyKind::Space);
        }
    }
    kk(&mut e, KeyKind::Space);
    kk(&mut e, KeyKind::Down)
}
#[test]
fn grid_columns_follow_the_longest_candidate() {
    // s3b2 §8.7. The longest candidate in the whole list decides, in characters.
    for (syls, longest, columns) in [
        (&["ㄕˋ"][..], 1, 6),
        (&["ㄋㄧˇ", "ㄏㄠˇ"][..], 2, 6),
        (&["ㄅㄚ", "ㄅㄚ", "ㄅㄚ"][..], 3, 3),
        // exactly 4 characters (an idiom): the 3-4 boundary, so moving it to 3 or 5 fails here
        (&["ㄧ", "ㄒㄧㄣ", "ㄧ", "ㄧˋ"][..], 4, 3),
        (&["ㄅㄚ", "ㄅㄠˇ", "ㄩㄢˊ", "ㄗˇ", "ㄅㄧㄥ"][..], 5, 2),
    ] {
        let o = expanded_for(syls);
        let max = o.candidates.iter().map(|w| w.chars().count()).max().unwrap();
        assert!(max <= longest && o.columns == columns, "{syls:?}: longest shown {max}, columns {}", o.columns);
        // the rule looks at the whole list, so the longest word of the whole list is what matters
        let all = cands(syls);
        assert!(all.iter().map(|w| w.chars().count()).max().unwrap() == longest, "{syls:?}");
    }
}
#[test]
fn pick_follows_candidate_first() {
    let mut e = std();
    // closed
    assert!(e.pick(0).unwrap().is_none());
    let all = open_shi(&mut e);
    // collapsed page 2: index counts from the page start
    kk(&mut e, KeyKind::Space);
    assert!(e.pick(9).unwrap().is_none(), "outside the page");
    let o = e.pick(2).unwrap().unwrap();
    assert!(o.handled && o.selected.is_none() && o.preedit == all[9 + 2]);
    // expanded and scrolled
    let mut e = std();
    expanded_shi(&mut e);
    presses(&mut e, 5, KeyKind::Down);
    let first = kk(&mut e, KeyKind::Left).first as usize;
    assert!(first == 6);
    let n = all_len_visible(&mut e, first);
    assert!(e.pick(n).unwrap().is_none(), "outside the visible rows");
    let o = e.pick(7).unwrap().unwrap();
    assert!(o.preedit == all[first + 7]);
}
fn all_len_visible(e: &mut Engine, first: usize) -> usize {
    let o = kk(e, KeyKind::Right);
    assert!(o.first as usize == first);
    o.candidates.len()
}

// ---------- rows 9-14 ----------
#[test]
fn rows9_to_14_pending_syllable() {
    let mut e = std();
    assert!(k(&mut e, plain('s')).preedit == "ㄋ"); // 14
    assert!(k(&mut e, plain('u')).preedit == "ㄋㄧ"); // 9
    assert!(k(&mut e, plain('c')).preedit == "ㄏㄧ"); // initial column replaced
    assert!(k(&mut e, plain('s')).preedit == "ㄋㄧ");
    // 11: removes the last placed symbol (the replaced initial counts as last)
    assert!(kk(&mut e, KeyKind::Backspace).preedit == "ㄧ");
    assert!(kk(&mut e, KeyKind::Backspace).preedit.is_empty());
    // 13: other keys are handled and ignored
    typ(&mut e, "s");
    for key in [KeyKind::Enter, KeyKind::Left, KeyKind::Tab, KeyKind::Up, KeyKind::Delete, KeyKind::Down] {
        let o = kk(&mut e, key);
        assert!(o.handled && o.commit.is_empty() && o.preedit == "ㄋ");
    }
    assert!(k(&mut e, Key::ch('a', MOD_SHIFT)).preedit == "ㄋ");
    // 12
    let o = kk(&mut e, KeyKind::Esc);
    assert!(o.handled && o.preedit.is_empty());
    // 10: space is tone 1 (ㄧ alone is a lexicon syllable)
    typ(&mut e, "u");
    assert!(kk(&mut e, KeyKind::Space).preedit == top("ㄧ"));
    // new syllable goes in at the cursor, cursor to its right (UTF-16 units)
    let o = typ(&mut e, "su3");
    assert!(o.preedit.chars().count() == 2 && o.cursor_utf16 == 2);
    kk(&mut e, KeyKind::Home);
    let o = typ(&mut e, "cl3");
    assert!(o.cursor_utf16 == 1 && o.preedit.chars().count() == 3);
    // the pending syllable shows at the cursor
    let o = typ(&mut e, "c");
    assert!(o.preedit.chars().nth(1) == Some('ㄏ') && o.cursor_utf16 == 2);
}
#[test]
fn syllable_not_in_lexicon_is_rejected_composition_unchanged() {
    assert!(lex().entries(&["ㄅㄩ".to_string()]).is_empty());
    let mut e = std();
    typ(&mut e, "su3");
    let before = typ(&mut e, "1m"); // ㄅㄩ
    for tk in [' ', '6', '3', '4', '7'] {
        let o = k(&mut e, plain(tk));
        assert!(o.handled && o.commit.is_empty() && o.preedit == before.preedit && o.cursor_utf16 == before.cursor_utf16);
    }
    let o = kk(&mut e, KeyKind::Backspace); // pending intact: ㄩ removed, ㄅ left
    assert!(o.preedit.ends_with("ㄅ") && o.preedit.starts_with('你'));
}

// ---------- rows 16-22 ----------
#[test]
fn rows16_to_20_composition_editing() {
    let mut e = std();
    typ(&mut e, NIHAO);
    for key in [plain('6'), Key::new(KeyKind::Up)] {
        let o = k(&mut e, key); // 16
        assert!(o.handled && o.preedit == "你好" && o.cursor_utf16 == 2);
    }
    // 17
    assert!(kk(&mut e, KeyKind::Left).cursor_utf16 == 1);
    assert!(kk(&mut e, KeyKind::Home).cursor_utf16 == 0);
    assert!(kk(&mut e, KeyKind::Left).cursor_utf16 == 0);
    assert!(kk(&mut e, KeyKind::Right).cursor_utf16 == 1);
    assert!(kk(&mut e, KeyKind::End).cursor_utf16 == 2);
    let o = kk(&mut e, KeyKind::Right);
    assert!(o.handled && o.cursor_utf16 == 2);
    // 18: Delete at the right edge ignored; Backspace removes left; edges ignored
    assert!(kk(&mut e, KeyKind::Delete).preedit == "你好");
    let o = kk(&mut e, KeyKind::Backspace);
    assert!(o.preedit.chars().count() == 1 && o.cursor_utf16 == 1 && o.preedit.starts_with('你'));
    kk(&mut e, KeyKind::Home);
    let o = kk(&mut e, KeyKind::Backspace);
    assert!(o.handled && o.preedit.chars().count() == 1 && o.cursor_utf16 == 0);
    let o = kk(&mut e, KeyKind::Delete);
    assert!(o.handled && o.preedit.is_empty());
    // 19
    typ(&mut e, NIHAO);
    let o = kk(&mut e, KeyKind::Enter);
    assert!(o.handled && o.commit == "你好" && o.preedit.is_empty());
    // 20
    typ(&mut e, "su3");
    let o = kk(&mut e, KeyKind::Esc);
    assert!(o.handled && o.commit.is_empty() && o.preedit.is_empty());
}
#[test]
fn row21_other_key_commits_then_passes_through() {
    for key in [Key::new(KeyKind::Tab), Key::ch('a', MOD_SHIFT), Key::ch('x', MOD_SHIFT), Key::ch('-', MOD_SHIFT), Key::ch('5', MOD_SHIFT)] {
        let mut e = std();
        typ(&mut e, NIHAO);
        let o = k(&mut e, key);
        assert!(!o.handled && o.commit == "你好" && o.preedit.is_empty());
        assert!(kk(&mut e, KeyKind::Enter) == blank(false));
    }
}
#[test]
fn row22_empty_composition_passes_through() {
    let mut e = std();
    for key in [Key::new(KeyKind::Tab), Key::new(KeyKind::Enter), Key::new(KeyKind::Esc), Key::new(KeyKind::Space), Key::new(KeyKind::Left), Key::new(KeyKind::Backspace), plain('6'), Key::ch('a', MOD_SHIFT)] {
        assert!(k(&mut e, key) == blank(false));
    }
}

// ---------- fixed words ----------
fn pick(e: &mut Engine, list: &[String], word: &str) -> Output {
    let idx = list.iter().position(|w| w == word).unwrap();
    kk(e, KeyKind::Down);
    presses(e, idx, KeyKind::Right);
    kk(e, KeyKind::Enter)
}
#[test]
fn fixed_one_syllable_word_survives_edits_and_is_removed_with_its_syllable() {
    let two = cands(&[NI, HAO]);
    let alt = two.iter().find(|w| w.chars().count() == 1 && **w != top(HAO)).unwrap().clone();
    let mut e = std();
    typ(&mut e, NIHAO);
    let o = pick(&mut e, &two, &alt);
    assert!(o.preedit == format!("{}{alt}", top(NI)) && o.cursor_utf16 == 2 && o.selected.is_none());
    // insert at the cursor, right of the fixed word: unchanged
    let o = typ(&mut e, "su3");
    assert!(o.preedit == format!("{}{alt}{}", top(NI), top(NI)));
    // insert at the left edge: the fixed word moves right
    kk(&mut e, KeyKind::Home);
    let o = typ(&mut e, "su3");
    assert!(o.preedit.chars().count() == 4 && o.preedit.chars().nth(2) == alt.chars().next());
    // delete the syllable on the right of the cursor (index 1, left of the fixed word): fixed shifts back
    let o = kk(&mut e, KeyKind::Delete);
    assert!(o.preedit.chars().count() == 3 && o.preedit.chars().nth(1) == alt.chars().next());
    // delete the fixed word's own syllable: it is gone, a new syllable decodes freely
    kk(&mut e, KeyKind::Esc);
    kk(&mut e, KeyKind::Esc);
    typ(&mut e, NIHAO);
    pick(&mut e, &two, &alt);
    kk(&mut e, KeyKind::Backspace);
    let o = typ(&mut e, "cl3");
    assert!(o.preedit == format!("{}{}", top(NI), top(HAO)));
}
#[test]
fn fixed_two_syllable_word_shifts_and_is_removed_when_cut() {
    let two = cands(&[NI, HAO]);
    let w = two.iter().find(|w| w.chars().count() == 2 && **w != "你好").unwrap().clone();
    let build = || {
        let mut e = std();
        typ(&mut e, NIHAO);
        let o = pick(&mut e, &two, &w);
        assert!(o.preedit == w);
        e
    };
    let fresh = |s: &str| typ(&mut std(), s).preedit;
    // insert on the right of it: kept
    let mut e = build();
    assert!(typ(&mut e, "su3").preedit == format!("{w}{}", top(NI)));
    // insert at the left edge: shifts right
    let mut e = build();
    kk(&mut e, KeyKind::Home);
    let o = typ(&mut e, "su3");
    assert!(o.preedit.chars().skip(1).collect::<String>() == w);
    // insert inside the span: removed, everything decoded freely
    let mut e = build();
    kk(&mut e, KeyKind::Left);
    assert!(typ(&mut e, "su3").preedit == fresh("su3su3cl3"));
    // delete inside the span (Backspace at the right end, Delete mid-way): removed
    let mut e = build();
    assert!(kk(&mut e, KeyKind::Backspace).preedit == fresh("su3"));
    let mut e = build();
    kk(&mut e, KeyKind::Left);
    assert!(kk(&mut e, KeyKind::Delete).preedit == fresh("su3"));
}

// ---------- reset ----------
#[test]
fn reset_both_modes_equal_fresh_engine() {
    let probe = |e: &mut Engine| -> Vec<Output> {
        let keys: Vec<Key> = "su3 ".chars().map(plain).chain([Key::new(KeyKind::Left), Key::new(KeyKind::Esc), Key::new(KeyKind::Tab)]).chain("cl3".chars().map(plain)).chain([Key::new(KeyKind::Down), Key::new(KeyKind::Enter), Key::new(KeyKind::Home), Key::new(KeyKind::Enter)]).collect();
        keys.into_iter().map(|key| e.key(key).unwrap()).collect()
    };
    for mode in [ResetMode::Commit, ResetMode::Discard] {
        let mut e = std();
        typ(&mut e, NIHAO);
        kk(&mut e, KeyKind::Down);
        kk(&mut e, KeyKind::Right);
        kk(&mut e, KeyKind::Enter); // a fixed word exists
        kk(&mut e, KeyKind::Left);
        typ(&mut e, "c"); // and a pending symbol
        let o = e.reset(mode);
        match mode {
            ResetMode::Commit => assert!(o.commit.chars().count() == 2 && o.preedit.is_empty()),
            ResetMode::Discard => assert!(o == blank(true)),
        }
        assert!(o.candidates.is_empty() && o.selected.is_none() && o.cursor_utf16 == 0);
        let mut fresh = std();
        let (a, b) = (probe(&mut e), probe(&mut fresh));
        assert!(a.len() == b.len() && a.iter().zip(&b).all(|(x, y)| x == y));
    }
    // with candidates open, Commit returns the display string, not a candidate
    let mut e = std();
    typ(&mut e, "su3 ");
    let o = e.reset(ResetMode::Commit);
    assert!(o.commit == top(NI) && o.selected.is_none() && o.candidates.is_empty());
    let o = e.reset(ResetMode::Discard);
    assert!(o == blank(true));
}

// ---------- 40-syllable auto commit ----------
#[test]
fn auto_commit_at_40th_syllable() {
    let mut e = std();
    for i in 1..40 {
        let o = typ(&mut e, "su3");
        assert!(o.handled && o.commit.is_empty() && o.preedit.chars().count() == i);
    }
    let o = typ(&mut e, "su3");
    assert!(o.handled && o.commit.chars().count() == 40 && o.preedit.is_empty() && o.cursor_utf16 == 0);
    let o = typ(&mut e, "su3");
    assert!(o.commit.is_empty() && o.preedit == top(NI));
}
