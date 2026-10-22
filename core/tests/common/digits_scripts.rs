//! Key scripts shared by the digits tests (digits.md A.4.1): `engine_digits.rs` and `engine_digits_off.rs`. A token is a keycap, `_`
//! Space, `Enter`, `Esc`, `BS`, `Left`, `Right`, `Down`, `Tab`, `C-\` (Control+backslash), `S-x` (Shift), or `@commit` (`reset(Commit)`).
#![allow(dead_code)]
use core::engine::*;

pub fn key_of(tok: &str) -> Key {
    match tok {
        "_" => Key::new(KeyKind::Space),
        "Enter" => Key::new(KeyKind::Enter),
        "S-Enter" => Key { kind: KeyKind::Enter, ch: '\0', modifiers: MOD_SHIFT },
        "Esc" => Key::new(KeyKind::Esc),
        "BS" => Key::new(KeyKind::Backspace),
        "Left" => Key::new(KeyKind::Left),
        "Right" => Key::new(KeyKind::Right),
        "Down" => Key::new(KeyKind::Down),
        "Up" => Key::new(KeyKind::Up),
        "Tab" => Key::new(KeyKind::Tab),
        "C-\\" => Key::ch('\\', MOD_CONTROL),
        t if t.starts_with("S-") => Key::ch(t[2..].chars().next().unwrap(), MOD_SHIFT),
        t => {
            assert!(t.chars().count() == 1, "unknown token {t}");
            Key::ch(t.chars().next().unwrap(), 0)
        }
    }
}

/// One token to the engine: a key, or `@commit`.
pub fn send(e: &mut Engine, tok: &str) -> Output {
    if tok == "@commit" {
        e.reset(ResetMode::Commit)
    } else {
        e.key(key_of(tok)).unwrap()
    }
}

/// 127 units in the composition (full-width commas), for the 128-unit row.
pub fn one_short_of_full() -> String {
    vec!["S-,"; core::engine::MAX_SYLLABLES - 1].join(" ")
}

/// The scripts of rows #1-#39 of the contract's table (digits.md A.4.1), by row number; the keys of the row, nothing else.
pub fn rows() -> Vec<(&'static str, String)> {
    let r = |n: &'static str, s: &str| (n, s.to_string());
    vec![
        r("r01", "1 C-\\"),
        r("r02", "1 C-\\ S-A S-, 2 C-\\ S-B Enter"),
        r("r03", "h k 4 g 4 1 2 3 Enter"),
        r("r04", "2 0 2 6 s u 0 6 Enter"),
        r("r05", "1 m , 4 Enter"),
        r("r06", "3 . 1 4 Enter"),
        r("r07", "1 0 0 S-5"),
        r("r08", "2 0 2 6 / 1 0 / 1 5 Enter"),
        r("r09", "1 - 2 Enter"),
        r("r10", "8 4"),
        r("r11", "8 4 Down"),
        r("r12", "8 4 S-,"),
        r("r13", "1 9 8 4 Enter"),
        r("r14", "2 0 0 4 s u 0 6 Enter"),
        r("r15", "5 0 4 Down"),
        r("r16", "1 0 1 o 4 Enter"),
        r("r17", "3 2 u 0 3 Enter"),
        r("r18", "2 5 _"),
        r("r19", "1 0 0 m 0 6 Enter"),
        r("r20", "1 0 0 e k 4 Enter"),
        r("r21", "2 8 c l 4 Enter"),
        r("r22", "1 . 5 1 o 4 Enter"),
        r("r23", "2 . 5 Enter"),
        r("r24", "2 . _"),
        r("r25", "0 . 5 Enter"),
        r("r26", "S-A S-T S-M 5 j 0 3 Enter"),
        r("r27", "S-A 1 2 5 8 9 0 1 2 5 8 Enter"),
        r("r28", "S-P 4 Enter S-O 7 Enter"),
        r("r29", "2 0 2 0 Enter"),
        r("r30", "2 0 2 0 _"),
        r("r31", "1 _"),
        r("r32", "1 2 BS BS"),
        r("r33", "2 0 u"),
        r("r34", "1 Esc"),
        r("r35", "s u 3 3 Enter"),
        r("r36", "1 S-, Esc 1 S-1"),
        ("r37", format!("{} 1 2 3 4 5", one_short_of_full())),
        r("r38", "2 0 2 6 @commit"),
        r("r39", "1 0 Down"),
    ]
}

/// An output as one text line (every field, R2 does not matter in a test).
pub fn line(o: &Output) -> String {
    format!(
        "{}|{}|{}|{}|{}|{:?}|{}|{}|{}|{}|{}|{}",
        o.handled as u8, o.commit, o.preedit, o.cursor_utf16, o.candidates.join(","), o.selected, o.columns, o.first, o.total, o.vertical,
        o.english_run_utf16_start, o.english_run_utf16_len
    )
}
