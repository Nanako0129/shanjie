//! The reading term in the engine (docs/contracts/reading-decoder.md section 2.3): learning on top of it. Needs data/lm/bigram.sjlm like
//! engine_learn.rs.
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
