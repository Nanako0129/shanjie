//! SW first slice (docs/contracts/sw-sensitive-demote.md section 4 (b), (c)): the demotion table through
//! the engine. Needs data/lm/bigram.sjlm and fails loudly without it (never skips).
use core::engine::*;
use core::lm::{decode, decode_segment, CappedLexicon, Demote, End, Lm, Profile};
use core::{Lexicon, Syls};
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}

struct Shared {
    lex: Arc<Lexicon>,
    lm: Arc<Lm>,
    table: Demote,
    /// The capped lexicon with data/lexicon/demote.tsv, and without it (the lexicon as before this slice).
    on: Arc<CappedLexicon>,
    off: Arc<CappedLexicon>,
}

fn shared() -> &'static Shared {
    static S: OnceLock<Shared> = OnceLock::new();
    S.get_or_init(|| {
        let dir = root().join("data/lexicon");
        let lex = load_lexicon(&dir).unwrap();
        let lm_file = root().join("data/lm/bigram.sjlm");
        assert!(lm_file.exists(), "data/lm/bigram.sjlm is missing: download it with `gh release download model-v2 -R Nanako0129/shanjie -p bigram.sjlm -D data/lm`");
        let lm = Lm::load(&lm_file).unwrap();
        let overlay = std::fs::read_to_string(dir.join("overlay-add.tsv")).unwrap();
        let table = Demote::parse(&std::fs::read_to_string(dir.join("demote.tsv")).unwrap()).unwrap();
        let on = Arc::new(CappedLexicon::new(lex.clone(), &overlay, &lm).with_demote(table.clone()));
        let off = Arc::new(CappedLexicon::new(lex.clone(), &overlay, &lm));
        Shared { lex, lm: Arc::new(lm), table, on, off }
    })
}

fn engine(capped: &Arc<CappedLexicon>, profile: Profile) -> Engine {
    let s = shared();
    let mut e = Engine::with_lexicon(s.lex.clone(), Layout::Standard);
    e.set_lm(s.lm.clone(), capped.clone());
    e.set_profile(profile).unwrap();
    e
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

fn type_syls(e: &mut Engine, syls: &Syls) -> Output {
    let mut last = None;
    for syl in syls {
        for k in keys_of(syl) {
            last = Some(e.key(k).unwrap());
        }
    }
    last.unwrap()
}

fn syls(s: &str) -> Syls {
    s.split(' ').map(str::to_string).collect()
}

/// Premise of the slice: the user report, `ㄍㄠˇ ㄨㄢˊ ㄓㄜˋ ㄅㄛ`, is 睪丸這波 without the table and 搞完這波 with it.
#[test]
fn user_report_flips_with_the_table() {
    let s = shared();
    let r = syls("ㄍㄠˇ ㄨㄢˊ ㄓㄜˋ ㄅㄛ");
    for profile in [Profile::Chat, Profile::Formal] {
        assert_eq!(type_syls(&mut engine(&s.off, profile), &r).preedit, "睪丸這波");
        assert_eq!(type_syls(&mut engine(&s.on, profile), &r).preedit, "搞完這波");
    }
}

/// 4 (b): `set_demote(false)` gives the same display and the same scores, bit for bit, as a lexicon without
/// the table: through the engine (display, total_score) and through the decoder (every score of the list).
#[test]
fn set_demote_false_equals_no_table() {
    let s = shared();
    let rows = ["ㄍㄠˇ ㄨㄢˊ ㄓㄜˋ ㄅㄛ", "ㄑㄧㄝ ㄔㄨˊ ㄍㄠˇ ㄨㄢˊ", "ㄑㄧㄝ ㄔㄨˊ ㄍㄠ ㄨㄢˊ", "ㄗㄨㄛˇ ㄘㄜˋ ㄍㄠ ㄨㄢˊ", "ㄐㄧㄣ ㄊㄧㄢ ㄍㄠˇ ㄨㄢˊ"];
    for profile in [Profile::Chat, Profile::Formal] {
        for r in rows {
            let r = syls(r);
            let mut a = engine(&s.on, profile);
            a.set_demote(false);
            let mut b = engine(&s.off, profile);
            let (oa, ob) = (type_syls(&mut a, &r), type_syls(&mut b, &r));
            assert_eq!(oa.preedit, ob.preedit);
            assert_eq!(a.total_score().unwrap().to_bits(), b.total_score().unwrap().to_bits());
            let da = core::lm::decode_from(&s.on, &r, &s.lm, profile, 64, "<s>", false).unwrap();
            let db = decode(&s.off, &r, &s.lm, profile, 64).unwrap();
            assert_eq!(da.len(), db.len());
            for (x, y) in da.iter().zip(&db) {
                assert!(x.0.to_bits() == y.0.to_bits() && x.1 == y.1);
            }
        }
    }
}

/// 4 (c): a demoted word fixed by the user. Row 切除睪丸 typed with the non-standard reading keeps the
/// demoted word first even with the table (probe row 11), so the whole-sentence top-1 contains it. Fixing
/// 睪丸 on the right (End::Next for the free 切除) makes the engine's total equal that top-1 score; a
/// `total_score` that forgot the delta is off by exactly the delta.
#[test]
fn fixed_demoted_word_total_equals_top1() {
    let s = shared();
    let r = syls("ㄑㄧㄝ ㄔㄨˊ ㄍㄠˇ ㄨㄢˊ");
    for profile in [Profile::Chat, Profile::Formal] {
        let (score, words) = decode(&s.on, &r, &s.lm, profile, 64).unwrap().swap_remove(0);
        assert_eq!(words.concat(), "切除睪丸");
        // The premise: the delta really is charged in that score.
        let plain = decode(&s.off, &r, &s.lm, profile, 64).unwrap().swap_remove(0).0;
        assert!((plain - score - 2.0).abs() < 1e-9, "{plain} vs {score}");
        let mut e = engine(&s.on, profile);
        for syl in &r {
            for k in keys_of(syl) {
                e.key(k).unwrap();
            }
        }
        let mut o = e.key(Key::new(KeyKind::Space)).unwrap();
        // The cursor is at the end: the candidates are the words ending there; walk to 睪丸.
        let mut found = false;
        for _ in 0..5000 {
            if o.candidates[o.selected.unwrap()] == "睪丸" {
                found = true;
                break;
            }
            o = e.key(Key::new(KeyKind::Right)).unwrap();
        }
        assert!(found, "candidate 睪丸 exists");
        let o = e.key(Key::new(KeyKind::Enter)).unwrap();
        assert_eq!(o.preedit, "切除睪丸");
        let total = e.total_score().unwrap();
        assert!((total - score).abs() <= 1e-9, "total {total} vs top-1 {score}");
    }
}

/// 4 (c), the decoder side: the closing transition into a fixed word charges that word's delta.
#[test]
fn next_end_charges_the_fixed_words_delta() {
    let s = shared();
    let r = syls("ㄑㄧㄝ ㄔㄨˊ");
    let lp = s.on.best_lp(&syls("ㄍㄠˇ ㄨㄢˊ"), "睪丸").unwrap();
    let d = s.on.delta(&syls("ㄍㄠˇ ㄨㄢˊ"), "睪丸");
    assert_eq!(d, 2.0);
    assert_eq!(s.on.delta(&syls("ㄍㄠ ㄨㄢˊ"), "睪丸"), 0.0);
    let top = |delta| decode_segment(&s.on, &r, &s.lm, 0.5, "<s>", End::Next { word: "睪丸", lp, delta }, 64).unwrap()[0].0;
    assert!((top(0.0) - top(d) - d).abs() < 1e-12);
    assert_eq!(s.table.delta("ㄍㄠˇ-ㄨㄢˊ", "睪丸"), 2.0);
}
