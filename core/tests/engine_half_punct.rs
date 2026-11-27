//! Slice A (`MIXED-PUNCT-A`): partial half-width punctuation (`Engine::set_half_punct`).
//! Covers the 27-bit mask, Standard and ETen layouts, key scheme 1 rule 2m, `set_ascii(1)`,
//! `set_english(0)` / `set_abbreviation(1)` / `set_english(1)` `drun`/`run`, rule 1o and `Down`
//! full-width recovery, learning exclusion, and invalid-mask error handling.

use core::engine::*;
#[cfg(unix)]
use core::learn_store::LearnStore;
use core::Lexicon;
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}

fn lex() -> Arc<Lexicon> {
    static L: OnceLock<Arc<Lexicon>> = OnceLock::new();
    L.get_or_init(|| load_lexicon(&root().join("data/lexicon")).unwrap()).clone()
}

fn eng(layout: Layout) -> Engine {
    Engine::with_lexicon(lex(), layout)
}

fn app() -> Engine {
    let mut e = Engine::new(&root().join("data/lexicon"), Layout::Standard).unwrap();
    e.load_lm(&root().join("data/lm/bigram.sjlm")).unwrap();
    e
}

fn k(e: &mut Engine, key: Key) -> Output {
    e.key(key).unwrap()
}

fn kk(e: &mut Engine, kind: KeyKind) -> Output {
    k(e, Key::new(kind))
}

fn plain(ch: char) -> Key {
    if ch == ' ' { Key::new(KeyKind::Space) } else { Key::ch(ch, 0) }
}

fn shift(ch: char) -> Key {
    Key::ch(ch, MOD_SHIFT)
}

fn opt(ch: char, with_shift: bool) -> Key {
    Key::ch(ch, MOD_OPTION | if with_shift { MOD_SHIFT } else { 0 })
}

fn typ(e: &mut Engine, keys: &str) -> Output {
    let mut last = None;
    for c in keys.chars() {
        last = Some(k(e, plain(c)));
    }
    last.unwrap()
}

struct KeySpec {
    bit: u32,
    keycap: char,
    shifted: bool,
    ascii: char,
    full0: char,
    full1: char,
    sec: Option<char>,
    opt_full: char,
    default_on: bool,
}

const SPECS: [KeySpec; 27] = [
    KeySpec { bit: 0,  keycap: '`',  shifted: true,  ascii: '~',  full0: '～', full1: '～', sec: None,       opt_full: '～', default_on: true },
    KeySpec { bit: 1,  keycap: '1',  shifted: true,  ascii: '!',  full0: '！', full1: '！', sec: None,       opt_full: '！', default_on: true },
    KeySpec { bit: 2,  keycap: '2',  shifted: true,  ascii: '@',  full0: '＠', full1: '＠', sec: None,       opt_full: '＠', default_on: true },
    KeySpec { bit: 3,  keycap: '3',  shifted: true,  ascii: '#',  full0: '＃', full1: '＃', sec: None,       opt_full: '＃', default_on: true },
    KeySpec { bit: 4,  keycap: '4',  shifted: true,  ascii: '$',  full0: '＄', full1: '＄', sec: None,       opt_full: '＄', default_on: true },
    KeySpec { bit: 5,  keycap: '5',  shifted: true,  ascii: '%',  full0: '％', full1: '％', sec: None,       opt_full: '％', default_on: true },
    KeySpec { bit: 6,  keycap: '6',  shifted: true,  ascii: '^',  full0: '＾', full1: '︿', sec: None,       opt_full: '＾', default_on: true },
    KeySpec { bit: 7,  keycap: '7',  shifted: true,  ascii: '&',  full0: '＆', full1: '＆', sec: None,       opt_full: '＆', default_on: true },
    KeySpec { bit: 8,  keycap: '8',  shifted: true,  ascii: '*',  full0: '＊', full1: '＊', sec: None,       opt_full: '＊', default_on: true },
    KeySpec { bit: 9,  keycap: '9',  shifted: true,  ascii: '(',  full0: '（', full1: '（', sec: None,       opt_full: '（', default_on: false },
    KeySpec { bit: 10, keycap: '0',  shifted: true,  ascii: ')',  full0: '）', full1: '）', sec: None,       opt_full: '）', default_on: false },
    KeySpec { bit: 11, keycap: '-',  shifted: true,  ascii: '_',  full0: '＿', full1: '＿', sec: None,       opt_full: '＿', default_on: true },
    KeySpec { bit: 12, keycap: '=',  shifted: true,  ascii: '+',  full0: '＋', full1: '＋', sec: None,       opt_full: '＋', default_on: true },
    KeySpec { bit: 13, keycap: '[',  shifted: true,  ascii: '{',  full0: '『', full1: '『', sec: Some('｛'), opt_full: '『', default_on: false },
    KeySpec { bit: 14, keycap: ']',  shifted: true,  ascii: '}',  full0: '』', full1: '』', sec: Some('｝'), opt_full: '』', default_on: false },
    KeySpec { bit: 15, keycap: '\\', shifted: true,  ascii: '|',  full0: '｜', full1: '｜', sec: None,       opt_full: '｜', default_on: true },
    KeySpec { bit: 16, keycap: ';',  shifted: true,  ascii: ':',  full0: '：', full1: '：', sec: None,       opt_full: '：', default_on: false },
    KeySpec { bit: 17, keycap: '\'', shifted: true,  ascii: '"',  full0: '“', full1: '；', sec: None,       opt_full: '“', default_on: false },
    KeySpec { bit: 18, keycap: ',',  shifted: true,  ascii: '<',  full0: '，', full1: '，', sec: Some('＜'), opt_full: '《', default_on: false },
    KeySpec { bit: 19, keycap: '.',  shifted: true,  ascii: '>',  full0: '。', full1: '。', sec: Some('＞'), opt_full: '》', default_on: false },
    KeySpec { bit: 20, keycap: '/',  shifted: true,  ascii: '?',  full0: '？', full1: '？', sec: None,       opt_full: '？', default_on: false },
    KeySpec { bit: 21, keycap: '`',  shifted: false, ascii: '`',  full0: '·', full1: '·', sec: Some('｀'), opt_full: '｀', default_on: true },
    KeySpec { bit: 22, keycap: '=',  shifted: false, ascii: '=',  full0: '＝', full1: '＝', sec: None,       opt_full: '＝', default_on: true },
    KeySpec { bit: 23, keycap: '[',  shifted: false, ascii: '[',  full0: '「', full1: '「', sec: Some('［'), opt_full: '「', default_on: false },
    KeySpec { bit: 24, keycap: ']',  shifted: false, ascii: ']',  full0: '」', full1: '」', sec: Some('］'), opt_full: '」', default_on: false },
    KeySpec { bit: 25, keycap: '\\', shifted: false, ascii: '\\', full0: '、', full1: '＼', sec: None,       opt_full: '、', default_on: false },
    KeySpec { bit: 26, keycap: '\'', shifted: false, ascii: '\'', full0: '‘', full1: '、', sec: None,       opt_full: '‘', default_on: false },
];

fn spec_key(s: &KeySpec) -> Key {
    if s.shifted { shift(s.keycap) } else { plain(s.keycap) }
}

#[cfg(unix)]
fn tmp_dir(tag: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("shanjie-half-punct-{}-{tag}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    d
}

#[test]
fn mask_constants_match_contract() {
    assert_eq!(HALF_PUNCT_ALL_MASK, 0x07FF_FFFF);
    assert_eq!(HALF_PUNCT_DEFAULT_MASK, 0x0060_99FF);
    let mut default_calc = 0u32;
    let mut count = 0u32;
    for s in &SPECS {
        if s.default_on {
            default_calc |= 1u32 << s.bit;
            count += 1;
        }
    }
    assert_eq!(count, 14);
    assert_eq!(default_calc, HALF_PUNCT_DEFAULT_MASK);
}

#[test]
fn mask_zero_matches_baseline_across_punct_layouts_and_guards_ascii_and_rule2m() {
    for punct_layout in [0u32, 1] {
        for s in &SPECS {
            let key = spec_key(s);
            for pre in ["", "su3", "s"] {
                let mut base = eng(Layout::Standard);
                let mut zero = eng(Layout::Standard);
                base.set_punct_layout(punct_layout).unwrap();
                zero.set_punct_layout(punct_layout).unwrap();
                assert!(zero.set_half_punct(0).unwrap().is_some());
                if !pre.is_empty() {
                    typ(&mut base, pre);
                    typ(&mut zero, pre);
                }
                assert!(k(&mut zero, key) == k(&mut base, key), "layout {punct_layout} bit {} pre {pre}", s.bit);
                assert!(kk(&mut zero, KeyKind::Enter) == kk(&mut base, KeyKind::Enter), "enter bit {}", s.bit);
            }
        }
    }

    // `set_ascii(1)`: `~ Down` has only `["~"]` (no full-width expansion), even when half_punct_mask is non-zero.
    for mask in [0u32, HALF_PUNCT_ALL_MASK] {
        let mut e = app();
        e.set_half_punct(mask).unwrap();
        e.set_ascii(true).unwrap();
        let o = k(&mut e, plain('~'));
        assert!(o.handled && o.preedit == "~" && o.commit.is_empty());
        let d = kk(&mut e, KeyKind::Down);
        assert_eq!(d.candidates, vec!["~"], "mask {mask:#x}: set_ascii(1) keeps single candidate");
    }

    // `key_scheme == 1` Rule 2m: `< Down` after a syllable has only `["<"]` when bit 18 is unchecked.
    let mut e = eng(Layout::Standard);
    e.set_key_scheme(1).unwrap();
    e.set_half_punct(0).unwrap();
    typ(&mut e, "su3");
    let o = k(&mut e, shift(','));
    assert!(o.handled && o.preedit.ends_with('<') && o.commit.is_empty());
    let d = kk(&mut e, KeyKind::Down);
    assert_eq!(d.candidates, vec!["<"]);
}

#[test]
fn all_mask_inserts_all_27_keys_respects_eten_and_overrides_rule2m() {
    // Standard: all 21 Shift+ keys and 6 plain keys insert half-width without premature commit.
    for s in &SPECS {
        let mut e = eng(Layout::Standard);
        e.set_half_punct(HALF_PUNCT_ALL_MASK).unwrap();
        typ(&mut e, "su3");
        let prefix = e.key(Key::new(KeyKind::Right)).unwrap().preedit;
        let o = k(&mut e, spec_key(s));
        assert!(o.handled && o.commit.is_empty(), "bit {}: premature commit", s.bit);
        assert_eq!(o.preedit, format!("{prefix}{}", s.ascii), "bit {} preedit", s.bit);
        assert_eq!(e.syllables().last().unwrap(), &format!("_punct_{}", s.ascii));
        let ent = kk(&mut e, KeyKind::Enter);
        assert_eq!(ent.commit, format!("{prefix}{}", s.ascii));
    }

    // ETen: all 21 Shift+ keys and 4 non-Zhuyin plain keys (` [ ] \) become half-width;
    // plain `'` stays ㄘ and plain `=` stays ㄦ.
    for s in &SPECS {
        let mut e = eng(Layout::Eten);
        e.set_half_punct(HALF_PUNCT_ALL_MASK).unwrap();
        let o = k(&mut e, spec_key(s));
        if !s.shifted && s.keycap == '\'' {
            assert_eq!(o.preedit, "ㄘ", "ETen ' must stay Zhuyin");
            assert!(e.syllables().is_empty());
        } else if !s.shifted && s.keycap == '=' {
            assert_eq!(o.preedit, "ㄦ", "ETen = must stay Zhuyin");
            assert!(e.syllables().is_empty());
        } else {
            assert!(o.handled && o.commit.is_empty() && o.preedit == s.ascii.to_string(), "ETen bit {}", s.bit);
            assert_eq!(e.syllables(), &[format!("_punct_{}", s.ascii)]);
            assert_eq!(kk(&mut e, KeyKind::Enter).commit, s.ascii.to_string());
        }
    }

    // `key_scheme == 1`: checked bits 18/19 insert `<`/`>` via Rule 2 even on empty composition and with `from_half_punct == true`.
    for (ch, ascii, full, sec) in [(',', '<', "，", "＜"), ('.', '>', "。", "＞")] {
        let mut e = eng(Layout::Standard);
        e.set_key_scheme(1).unwrap();
        e.set_half_punct(HALF_PUNCT_ALL_MASK).unwrap();
        let o = k(&mut e, shift(ch));
        assert!(o.handled && o.commit.is_empty() && o.preedit == ascii.to_string());
        let d = kk(&mut e, KeyKind::Down);
        assert!(d.candidates.starts_with(&[ascii.to_string(), full.to_string(), sec.to_string()]), "cands: {:?}", d.candidates);
    }

    // `key_scheme == 1`: unchecked bits 18/19 preserve Rule 2m even when other bits are set.
    let without_angles = HALF_PUNCT_ALL_MASK & !((1u32 << 18) | (1u32 << 19));
    for ch in [',', '.'] {
        let mut e = eng(Layout::Standard);
        e.set_key_scheme(1).unwrap();
        e.set_half_punct(without_angles).unwrap();
        let empty = k(&mut e, shift(ch));
        assert!(!empty.handled && empty.commit.is_empty() && empty.preedit.is_empty(), "empty composition passes through");
        typ(&mut e, "s");
        let pend = k(&mut e, shift(ch));
        assert!(pend.handled && pend.preedit.is_empty());
        assert_eq!(pend.commit, if ch == ',' { "<" } else { ">" });
        typ(&mut e, "su3");
        let after = k(&mut e, shift(ch));
        assert!(after.handled && after.commit.is_empty());
        let d = kk(&mut e, KeyKind::Down);
        assert_eq!(d.candidates, vec![if ch == ',' { "<" } else { ">" }]);
    }
}

#[test]
fn default_mask_splits_14_half_width_and_13_baseline_under_both_punct_layouts() {
    for punct_layout in [0u32, 1] {
        for s in &SPECS {
            let key = spec_key(s);
            let mut def = eng(Layout::Standard);
            def.set_punct_layout(punct_layout).unwrap();
            def.set_half_punct(HALF_PUNCT_DEFAULT_MASK).unwrap();

            let mut base = eng(Layout::Standard);
            base.set_punct_layout(punct_layout).unwrap();

            let o = k(&mut def, key);
            let ob = k(&mut base, key);
            if s.default_on {
                assert!(o.handled && o.commit.is_empty() && o.preedit == s.ascii.to_string(), "layout {punct_layout} bit {} should be half-width", s.bit);
                assert_eq!(def.syllables(), &[format!("_punct_{}", s.ascii)]);
            } else {
                assert!(o == ob, "layout {punct_layout} bit {} should preserve baseline", s.bit);
                assert!(kk(&mut def, KeyKind::Enter) == kk(&mut base, KeyKind::Enter));
            }
        }
    }
}

#[test]
fn chinese_composition_with_english_off_and_abbreviation_on() {
    let mut e = app();
    e.set_english(false).unwrap();
    e.set_abbreviation(true).unwrap();
    e.set_half_punct(HALF_PUNCT_DEFAULT_MASK).unwrap();

    for (punct_key, mark) in [
        (shift('`'), '~'),
        (shift('1'), '!'),
        (shift('-'), '_'),
        (shift('='), '+'),
        (plain('='), '='),
        (shift('5'), '%'),
    ] {
        e.reset(ResetMode::Discard);
        let o1 = typ(&mut e, "hk4g4");
        assert_eq!(o1.preedit, "測試");
        let om = k(&mut e, punct_key);
        assert!(om.handled && om.commit.is_empty(), "{mark} must not commit prematurely");
        assert_eq!(om.preedit, format!("測試{mark}"));
        let o2 = typ(&mut e, "hk4g4");
        assert!(o2.commit.is_empty());
        assert_eq!(o2.preedit, format!("測試{mark}測試"));
        let ent = kk(&mut e, KeyKind::Enter);
        assert_eq!(ent.commit, format!("測試{mark}測試"));
    }

    // Unfinished syllable before Shift+- is dropped without premature commit.
    e.reset(ResetMode::Discard);
    typ(&mut e, "hk4g4h");
    let om = k(&mut e, shift('-'));
    assert!(om.handled && om.commit.is_empty() && om.preedit == "測試_");
    assert_eq!(kk(&mut e, KeyKind::Enter).commit, "測試_");
}

#[test]
fn english_mixing_drun_and_run_interactions() {
    let cases: &[(&[Key], &str)] = &[
        (&[plain('1'), plain('0'), plain('0'), shift('5')], "100%"),
        (&[plain('3'), shift('='), plain('4')], "3+4"),
        (&[plain('1'), shift('`'), plain('3'), plain('4')], "1~34"),
        (&[shift('o'), plain('k'), shift('1')], "Ok!"),
        (&[shift('f'), plain('o'), plain('o'), shift('-'), shift('b'), plain('a'), plain('r')], "Foo_Bar"),
        (&[shift('`'), shift('t'), plain('e'), plain('s'), plain('t')], "~Test"),
    ];
    for overwrite in [true, false] {
        let mut e = app();
        e.set_english(true).unwrap();
        e.set_english_overwrite(overwrite).unwrap();
        e.set_half_punct(HALF_PUNCT_DEFAULT_MASK).unwrap();
        for &(seq, want) in cases {
            e.reset(ResetMode::Discard);
            for &key in seq {
                let o = k(&mut e, key);
                assert!(o.handled && o.commit.is_empty(), "overwrite={overwrite} {want}: premature commit");
            }
            let ent = kk(&mut e, KeyKind::Enter);
            assert!(ent.handled && ent.preedit.is_empty());
            assert_eq!(ent.commit, want, "overwrite={overwrite}");
        }
    }
}

#[test]
fn full_width_recovery_via_rule1o_and_down_list() {
    // 1. Rule 1o (Option / Option+Shift) still outputs full-width for every default-checked key.
    let mut e = app();
    e.set_half_punct(HALF_PUNCT_DEFAULT_MASK).unwrap();
    for s in SPECS.iter().filter(|s| s.default_on) {
        e.reset(ResetMode::Discard);
        let o = k(&mut e, opt(s.keycap, s.shifted));
        assert!(o.handled && o.commit.is_empty() && o.preedit == s.opt_full.to_string(), "rule 1o bit {}", s.bit);
        assert_eq!(kk(&mut e, KeyKind::Enter).commit, s.opt_full.to_string());
    }

    // 2. Down on every Slice A half-width token lists `[half, full_base, (sec?), ...]`, picking 1 gives full_base,
    //    and pressing Down a second time still shows the list.
    for punct_layout in [0u32, 1] {
        let mut e = eng(Layout::Standard);
        e.set_punct_layout(punct_layout).unwrap();
        e.set_half_punct(HALF_PUNCT_ALL_MASK).unwrap();
        for s in &SPECS {
            e.reset(ResetMode::Discard);
            let o = k(&mut e, spec_key(s));
            assert_eq!(o.preedit, s.ascii.to_string());
            let d = kk(&mut e, KeyKind::Down);
            let full = if punct_layout == 1 { s.full1 } else { s.full0 };
            let ascii_s = s.ascii.to_string();
            let full_s = full.to_string();
            assert_eq!(d.candidates.first().map(String::as_str), Some(ascii_s.as_str()), "layout {punct_layout} bit {}", s.bit);
            assert_eq!(d.candidates.get(1).map(String::as_str), Some(full_s.as_str()), "layout {punct_layout} bit {}", s.bit);
            if let Some(sec) = s.sec {
                let sec_s = sec.to_string();
                assert_eq!(d.candidates.get(2).map(String::as_str), Some(sec_s.as_str()), "layout {punct_layout} bit {} secondary", s.bit);
            }

            // Pick index 1 (full_base), verify preedit replaced, then press Down a second time.
            let picked = e.pick(1).unwrap().unwrap();
            assert_eq!(picked.preedit, full.to_string(), "layout {punct_layout} bit {}", s.bit);
            let d2 = kk(&mut e, KeyKind::Down);
            assert_eq!(d2.candidates, d.candidates, "second Down after picking full-width keeps alternatives for bit {}", s.bit);

            // Pick secondary if present, else close the second candidate window with Esc, and verify Enter commits.
            if let Some(sec) = s.sec {
                let p2 = e.pick(2).unwrap().unwrap();
                assert_eq!(p2.preedit, sec.to_string());
                assert_eq!(kk(&mut e, KeyKind::Enter).commit, sec.to_string());
            } else {
                let closed = kk(&mut e, KeyKind::Esc);
                assert_eq!(closed.preedit, full.to_string());
                assert_eq!(kk(&mut e, KeyKind::Enter).commit, full.to_string());
            }
        }
    }

    // Explicit layout 0 vs 1 checks for Shift+', \, ', Shift+6.
    for (key, l0, l1) in [
        (shift('\''), "“", "；"),
        (plain('\\'), "、", "＼"),
        (plain('\''), "‘", "、"),
        (shift('6'), "＾", "︿"),
    ] {
        for (layout, want) in [(0u32, l0), (1u32, l1)] {
            let mut e = eng(Layout::Standard);
            e.set_punct_layout(layout).unwrap();
            e.set_half_punct(HALF_PUNCT_ALL_MASK).unwrap();
            k(&mut e, key);
            let d = kk(&mut e, KeyKind::Down);
            assert_eq!(d.candidates[1], want, "layout {layout} want {want}");
        }
    }
}

#[test]
fn picking_full_width_from_half_punct_does_not_learn() {
    let mut e = app();
    // learning_open / LearnStore::open return StoreError::Io on Windows (docs/spec/windows-tip.md §2.5);
    // in-memory learning exclusion runs on every platform.
    #[cfg(unix)]
    let dir = {
        let dir = tmp_dir("learn");
        e.learning_open(&dir).unwrap();
        dir
    };
    e.set_learning(true);
    e.set_half_punct(HALF_PUNCT_ALL_MASK).unwrap();

    // Standalone `~` -> Down -> pick `～` -> Enter.
    k(&mut e, shift('`'));
    let d = kk(&mut e, KeyKind::Down);
    assert_eq!(d.candidates, vec!["~", "～"]);
    let p = e.pick(1).unwrap().unwrap();
    assert_eq!(p.preedit, "～");
    assert_eq!(kk(&mut e, KeyKind::Enter).commit, "～");
    assert!(e.learner().records().is_empty());

    // After Chinese word `測試` -> `~` -> Down -> pick `～` -> Enter.
    typ(&mut e, "hk4g4");
    k(&mut e, shift('`'));
    kk(&mut e, KeyKind::Down);
    e.pick(1).unwrap().unwrap();
    assert_eq!(kk(&mut e, KeyKind::Enter).commit, "測試～");
    assert!(e.learner().records().is_empty());

    #[cfg(unix)]
    {
        let (_, on_disk, _) = LearnStore::open(&dir).unwrap();
        assert!(on_disk.is_empty());
        let _ = std::fs::remove_dir_all(&dir);
    }
}

#[test]
fn invalid_mask_returns_none_and_keeps_previous_mask() {
    let mut e = eng(Layout::Standard);
    for bad in [HALF_PUNCT_ALL_MASK + 1, 1u32 << 27, 1u32 << 31, u32::MAX] {
        assert!(e.set_half_punct(bad).unwrap().is_none(), "mask {bad:#x} must fail");
    }
    assert_eq!(k(&mut e, shift('`')).preedit, "～");
    e.reset(ResetMode::Discard);

    assert!(e.set_half_punct(HALF_PUNCT_DEFAULT_MASK).unwrap().is_some());
    for bad in [HALF_PUNCT_ALL_MASK + 1, 1u32 << 27, 1u32 << 31, u32::MAX] {
        assert!(e.set_half_punct(bad).unwrap().is_none(), "mask {bad:#x} must fail");
    }
    assert_eq!(k(&mut e, shift('`')).preedit, "~");
}
