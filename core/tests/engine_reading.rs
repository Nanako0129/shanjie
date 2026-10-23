//! The reading term in the engine (docs/contracts/reading-decoder.md section 2.3): learning on top of it, the candidate window's order without a
//! language model, and the other three places that list one-syllable entries (the English window's Chinese item, the English token window's, the
//! digit layer's second reading). Needs data/lm/bigram.sjlm like engine_learn.rs for the learning test; the window tests need only data/lexicon.
use core::engine::*;
use core::lm::{CappedLexicon, Lm};
use core::Lexicon;
use std::path::{Path, PathBuf};
use std::sync::Arc;

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}

fn keys_of(syl: &str) -> Vec<Key> {
    let l = Layout::Standard;
    let mut v = Vec::new();
    let mut toned = false;
    for c in syl.chars() {
        match l.key_of_tone(c) {
            Some(tk) => {
                v.push(Key::ch(tk, 0));
                toned = true;
            }
            None => v.push(Key::ch(l.key_of_symbol(c).expect("symbol has a key"), 0)),
        }
    }
    if !toned {
        v.push(Key::new(KeyKind::Space));
    }
    v
}

fn type_syls(e: &mut Engine, s: &str) -> Output {
    let mut o = None;
    for syl in s.split(' ') {
        for k in keys_of(syl) {
            o = Some(e.key(k).unwrap());
        }
    }
    o.unwrap()
}

fn k(kind: KeyKind) -> Key {
    Key::new(kind)
}

/// 欣 reads ㄒㄧㄣ at -2.0 and ㄏㄨ at -0.5, so the reading term charges 欣 under ㄒㄧㄣ 2.0 * -1.5 = -3.0; 鑫 has only the one reading.
const TERM_TEXT: &str = "ㄒㄧㄣ 鑫 -1.0\nㄒㄧㄣ 欣 -2.0\nㄏㄨ 欣 -0.5\n";

/// An engine on the toy lexicon (with the table) and the real bigram model, learning on, the left context `他們` (a single character learns after
/// exactly two Han characters).
fn toy_engine(terms: bool) -> Engine {
    let lm = Arc::new(Lm::load(&root().join("data/lm/bigram.sjlm")).expect("data/lm/bigram.sjlm"));
    let lex = Lexicon::parse(TERM_TEXT).unwrap();
    let lex = Arc::new(if terms { lex.with_reading_terms(TERM_TEXT) } else { lex });
    let capped = Arc::new(CappedLexicon::new(lex.clone(), "", &lm, None).unwrap());
    let mut e = Engine::with_lexicon(lex, Layout::Standard);
    e.set_lm(lm, capped);
    e.set_today(Some(20_000));
    e.set_learning(true);
    e.set_prediction(false).unwrap();
    e.set_left_context("他們");
    e
}

/// The reading term is added on top of the learned boost, which is untouched: 欣 under ㄒㄧㄣ loses to 鑫 for the reading term (the lexicon's own order
/// has 鑫 first too, so the term only widens the gap), the window lists 欣 second, and once the user has chosen 欣 there the stretch shows 欣 again
/// although the term still charges it. The same flow without the table (what the version before the term did) shows 欣 as well.
#[test]
fn a_taught_word_shows_although_its_reading_is_charged() {
    for terms in [false, true] {
        let mut e = toy_engine(terms);
        assert_eq!(type_syls(&mut e, "ㄒㄧㄣ").preedit, "鑫", "terms {terms}: premise");
        let o = e.key(k(KeyKind::Space)).unwrap();
        assert_eq!(o.candidates[..2], ["鑫".to_string(), "欣".to_string()], "terms {terms}: the window");
        e.key(k(KeyKind::Right)).unwrap();
        assert_eq!(e.key(k(KeyKind::Enter)).unwrap().preedit, "欣");
        e.key(k(KeyKind::Enter)).unwrap();
        e.set_left_context("他們");
        assert_eq!(type_syls(&mut e, "ㄒㄧㄣ").preedit, "欣", "terms {terms}: learned");
    }
}

// ---------- the candidate window (docs/contracts/reading-decoder.md section 1, "Candidate window") ----------

fn real() -> Arc<Lexicon> {
    static L: std::sync::OnceLock<Arc<Lexicon>> = std::sync::OnceLock::new();
    L.get_or_init(|| load_lexicon(&root().join("data/lexicon")).unwrap()).clone()
}

/// The whole list of the open window, as the grid shows it.
fn window_list(e: &mut Engine) -> Vec<String> {
    let o = e.key(k(KeyKind::Down)).unwrap();
    assert!(o.columns == 9, "the grid is open");
    let total = o.total as usize;
    let mut all = o.candidates.clone();
    let mut last = o;
    while all.len() < total {
        for _ in 0..9 {
            last = e.key(k(KeyKind::Down)).unwrap();
        }
        let from = last.first as usize;
        all.extend(last.candidates[all.len() - from..].iter().cloned());
    }
    all
}

/// Typing ㄎㄨㄞˋ opens a window with 會 (whose ㄎㄨㄞˋ is a floor-score reading, the secondary one) behind where the lexicon's own order has it, off the first
/// page; the order is the lexicon's with the switch off, and no language model is needed (the table is built when the lexicon is loaded).
#[test]
fn the_window_lists_a_rare_reading_behind_where_the_lexicon_has_it() {
    let syl = "ㄎㄨㄞˋ".to_string();
    let raw: Vec<String> = real().entries(std::slice::from_ref(&syl)).into_iter().map(|e| e.0.to_string()).collect();
    let at = |list: &[String]| list.iter().position(|w| w == "會").expect("會 is a candidate of ㄎㄨㄞˋ");
    let mut on = Engine::with_lexicon(real(), Layout::Standard);
    let mut off = Engine::with_lexicon(real(), Layout::Standard);
    off.set_reading_decode(false);
    for e in [&mut on, &mut off] {
        type_syls(e, "ㄎㄨㄞˋ");
        e.key(k(KeyKind::Space)).unwrap();
    }
    let (list_on, list_off) = (window_list(&mut on), window_list(&mut off));
    assert_eq!(list_off, raw, "the switch off: the lexicon's order");
    assert!(at(&raw) < 9 && at(&list_on) > at(&raw) && at(&list_on) >= 9, "會: place {} in the lexicon, {} with the term", at(&raw), at(&list_on));
    // the same words, in the same list length
    let (mut a, mut b) = (list_on.clone(), raw.clone());
    a.sort();
    b.sort();
    assert_eq!(a, b);
    // the first three are untouched
    assert_eq!(list_on[..3], raw[..3]);
}

/// A toy lexicon where the term flips the first entry of ㄍㄠ (高 reads ㄍㄠˋ better, 膏 has the one reading) and of ㄚˋ (亞 reads ㄚ better).
const WIN_TEXT: &str = "ㄍㄠ 高 -1.0\nㄍㄠ 膏 -1.5\nㄍㄠˋ 高 -0.2\nㄚˋ 亞 -1.0\nㄚˋ 啞 -1.6\nㄚ 亞 -0.1\n";

fn win_lexicon() -> Arc<Lexicon> {
    Arc::new(Lexicon::parse(WIN_TEXT).unwrap().with_reading_terms(WIN_TEXT))
}

fn english_engine(on: bool) -> Engine {
    let mut e = Engine::with_lexicon(win_lexicon(), Layout::Standard);
    e.load_english(&root().join("eval/golden/english-tiny.txt")).unwrap();
    e.set_english(true).unwrap();
    e.set_reading_decode(on);
    e
}

fn run(e: &mut Engine, script: &str) -> Output {
    let mut last = None;
    for t in script.split_whitespace() {
        let key = match t {
            "_" => Key::new(KeyKind::Space),
            "BS" => Key::new(KeyKind::Backspace),
            "Down" => Key::new(KeyKind::Down),
            t => Key::ch(t.chars().next().unwrap(), 0),
        };
        last = Some(e.key(key).unwrap());
    }
    last.unwrap()
}

/// The English window's Chinese item (`open_english`): the first of the leftover syllable's entries in the reading term's order. `google _` leaves ㄍㄠ.
#[test]
fn the_english_windows_chinese_item_is_the_first_by_the_reading_term() {
    for (on, want) in [(true, "膏"), (false, "高")] {
        let mut e = english_engine(on);
        let o = run(&mut e, "g o o g l e _");
        assert!(e.english_open() && o.total == 4, "four items");
        assert_eq!(o.candidates[3], want, "reading term {on}");
    }
}

/// The English token window's Chinese item: a token an auto-keep made remembers the syllable (`google` then `j`), Backspace and Space bring the window back.
#[test]
fn the_english_token_windows_chinese_item_is_the_first_by_the_reading_term() {
    for (on, want) in [(true, "膏"), (false, "高")] {
        let mut e = english_engine(on);
        assert_eq!(run(&mut e, "g o o g l e j").preedit, "googleㄨ");
        run(&mut e, "BS _");
        let o = run(&mut e, "Down");
        assert_eq!(o.candidates, ["google", "Google", "GOOGLE", want], "reading term {on}");
    }
}

/// The digit layer's second reading (digits.md A.3.6): after `1 9 8 4` the last item is the first word of ㄚˋ, in the reading term's order.
#[test]
fn the_digit_layers_syllable_is_the_first_by_the_reading_term() {
    let lm = Arc::new(Lm::load(&root().join("data/lm/bigram.sjlm")).expect("data/lm/bigram.sjlm"));
    let table = Arc::new(core::digits::DigitTable::parse(&std::fs::read_to_string(root().join("data/lexicon/digits.tsv")).unwrap()).expect("digits.tsv"));
    for (on, want) in [(true, "啞"), (false, "亞")] {
        let lex = win_lexicon();
        let capped = Arc::new(CappedLexicon::new(lex.clone(), "", &lm, None).unwrap());
        let mut e = Engine::with_lexicon(lex, Layout::Standard);
        e.set_lm(lm.clone(), capped);
        e.set_digit_table(Some(table.clone()));
        e.set_english(true).unwrap();
        e.set_reading_decode(on);
        let o = run(&mut e, "1 9 8 4 Down");
        let all = {
            let mut all = o.candidates.clone();
            for _ in 1..o.total {
                let n = e.key(k(KeyKind::Right)).unwrap();
                if n.first as usize + n.candidates.len() > all.len() {
                    all.extend(n.candidates[all.len() - n.first as usize..].iter().cloned());
                }
            }
            all
        };
        assert_eq!(all.last().map(String::as_str), Some(want), "reading term {on}: {all:?}");
    }
}

/// The real lexicon's table (built at load) against the lookup by string, over every syllable the standard layout can type: `entries_by_reading` is
/// `entries` stably sorted by score plus `ReadingDecode::term`.
#[test]
fn the_real_lexicons_table_matches_the_lookup_by_string() {
    let lex = real();
    let base = std::fs::read_to_string(root().join("data/lexicon/mcbpmf-data.txt")).unwrap();
    let rd = core::reading::ReadingDecode::new(&lex, &base);
    let (initials, medials) = (["", "ㄅ", "ㄆ", "ㄇ", "ㄈ", "ㄉ", "ㄊ", "ㄋ", "ㄌ", "ㄍ", "ㄎ", "ㄏ", "ㄐ", "ㄑ", "ㄒ", "ㄓ", "ㄔ", "ㄕ", "ㄖ", "ㄗ", "ㄘ", "ㄙ"], ["", "ㄧ", "ㄨ", "ㄩ"]);
    let finals = ["", "ㄚ", "ㄛ", "ㄜ", "ㄝ", "ㄞ", "ㄟ", "ㄠ", "ㄡ", "ㄢ", "ㄣ", "ㄤ", "ㄥ", "ㄦ"];
    let (mut syllables, mut moved) = (0, 0);
    for (i, m, f, t) in itertools(&initials, &medials, &finals) {
        let syl = format!("{i}{m}{f}{t}");
        let key = vec![syl.clone()];
        let raw = lex.entries(&key);
        if raw.is_empty() {
            continue;
        }
        syllables += 1;
        let mut want: Vec<(&str, f64, f64)> = raw.iter().map(|&(w, s)| (w, s, s + rd.term(&lex, &syl, w))).collect();
        want.sort_by(|a, b| b.2.partial_cmp(&a.2).unwrap());
        let got = lex.entries_by_reading(&key);
        assert_eq!(got.iter().map(|e| e.0).collect::<Vec<_>>(), want.iter().map(|e| e.0).collect::<Vec<_>>(), "{syl}");
        moved += (got.iter().map(|e| e.0).collect::<Vec<_>>() != raw.iter().map(|e| e.0).collect::<Vec<_>>()) as usize;
    }
    assert!(syllables > 1000 && moved > 100, "{syllables} syllables, {moved} reordered");
}

fn itertools<'a>(i: &'a [&'a str], m: &'a [&'a str], f: &'a [&'a str]) -> impl Iterator<Item = (&'a str, &'a str, &'a str, &'a str)> {
    i.iter().flat_map(move |&a| m.iter().flat_map(move |&b| f.iter().flat_map(move |&c| ["", "ˊ", "ˇ", "ˋ", "˙"].into_iter().map(move |t| (a, b, c, t)))))
}
