//! SW first slice (docs/contracts/sw-sensitive-demote.md section 4 (b), (c), and the load checks of section 2):
//! the demotion table through the engine. Needs data/lm/bigram.sjlm and fails loudly without it (never skips).
use core::engine::*;
use core::lm::{decode, decode_from, decode_segment, CappedLexicon, Demote, End, Lm, Profile};
use core::{Lexicon, Syls};
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}

struct Shared {
    lex: Arc<Lexicon>,
    lm: Arc<Lm>,
    /// The capped lexicon with data/lexicon/demote.tsv, and without any table (the lexicon as before this slice).
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
        assert!(root().join("data/lm/classes.sjc").exists(), "data/lm/classes.sjc is missing: download it with `gh release download classes-v2 -R Nanako0129/shanjie -p classes.sjc -D data/lm` (or build it with tools/build_classes.py)");
        let lm = Lm::load(&lm_file).unwrap();
        let overlay = std::fs::read_to_string(dir.join("overlay-add.tsv")).unwrap();
        let table = Demote::parse(&std::fs::read_to_string(dir.join("demote.tsv")).unwrap()).unwrap();
        let on = Arc::new(CappedLexicon::new(lex.clone(), &overlay, &lm, Some(&table)).unwrap());
        let off = Arc::new(CappedLexicon::new(lex.clone(), &overlay, &lm, None).unwrap());
        Shared { lex, lm: Arc::new(lm), on, off }
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

const REPORT: &str = "ㄍㄠˇ ㄨㄢˊ ㄓㄜˋ ㄅㄛ";

/// Premise of the slice: the user report is 睪丸這波 without the table and 搞完這波 with it.
#[test]
fn user_report_flips_with_the_table() {
    let s = shared();
    for profile in [Profile::Chat, Profile::Formal] {
        assert_eq!(type_syls(&mut engine(&s.off, profile), &syls(REPORT)).preedit, "睪丸這波");
        assert_eq!(type_syls(&mut engine(&s.on, profile), &syls(REPORT)).preedit, "搞完這波");
    }
}

/// Contract section 3 (setting): toggling during a composition recomputes it and returns the snapshot, so
/// the preedit changes at once, both ways.
#[test]
fn toggle_mid_composition_reranks() {
    let s = shared();
    for profile in [Profile::Chat, Profile::Formal] {
        let mut e = engine(&s.on, profile);
        assert_eq!(type_syls(&mut e, &syls(REPORT)).preedit, "搞完這波");
        let o = e.set_demote(false).unwrap();
        assert!(o.handled && o.commit.is_empty());
        assert_eq!(o.preedit, "睪丸這波");
        assert_eq!(e.set_demote(true).unwrap().preedit, "搞完這波");
    }
}

/// 4 (b): `set_demote(false)` gives the same display and the same scores, bit for bit, as a lexicon without
/// the table: through the engine (display, total_score) and through the decoder (every score of the list).
#[test]
fn set_demote_false_equals_no_table() {
    let s = shared();
    let rows = [REPORT, "ㄑㄧㄝ ㄔㄨˊ ㄍㄠˇ ㄨㄢˊ", "ㄑㄧㄝ ㄔㄨˊ ㄍㄠ ㄨㄢˊ", "ㄗㄨㄛˇ ㄘㄜˋ ㄍㄠ ㄨㄢˊ", "ㄐㄧㄣ ㄊㄧㄢ ㄍㄠˇ ㄨㄢˊ"];
    for profile in [Profile::Chat, Profile::Formal] {
        for r in rows {
            let r = syls(r);
            let mut a = engine(&s.on, profile);
            a.set_demote(false).unwrap();
            let mut b = engine(&s.off, profile);
            let (oa, ob) = (type_syls(&mut a, &r), type_syls(&mut b, &r));
            assert_eq!(oa.preedit, ob.preedit);
            assert_eq!(a.total_score().unwrap().to_bits(), b.total_score().unwrap().to_bits());
            let da = decode_from(&s.on, &r, &s.lm, profile, 64, "<s>", false).unwrap();
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
        type_syls(&mut e, &r);
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

/// 4 (c), the decoder side: the closing transition into a fixed word charges that word's delta, which the
/// fixed word finds from the same entry as the decoder (`best_lp_delta`).
#[test]
fn next_end_charges_the_fixed_words_delta() {
    let s = shared();
    let r = syls("ㄑㄧㄝ ㄔㄨˊ");
    let (lp, d) = s.on.best_lp_delta(&syls("ㄍㄠˇ ㄨㄢˊ"), "睪丸").unwrap();
    assert_eq!(d, 2.0);
    assert_eq!(s.on.best_lp_delta(&syls("ㄍㄠ ㄨㄢˊ"), "睪丸").unwrap().1, 0.0);
    assert_eq!(s.off.best_lp_delta(&syls("ㄍㄠˇ ㄨㄢˊ"), "睪丸").unwrap(), (lp, 0.0));
    let top = |delta| decode_segment(&s.on, &r, &s.lm, 0.5, "<s>", End::Next { word: "睪丸", lp, delta }, 64).unwrap()[0].0;
    assert!((top(0.0) - top(d) - d).abs() < 1e-12);
}

/// A copy of data/lexicon with the given demote.tsv (symlinks for the big files).
fn data_dir_with(tag: &str, demote: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("shanjie-demote-{}-{tag}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    for f in ["mcbpmf-data.txt", "overlay-add.tsv", "sandhi-add.tsv"] {
        std::os::unix::fs::symlink(root().join("data/lexicon").join(f), d.join(f)).unwrap();
    }
    std::fs::write(d.join("demote.tsv"), demote).unwrap();
    d
}

/// Contract section 2: a row that matches no lexicon entry fails engine creation: a reading typed with a
/// space, a reading the lexicon lacks, a word absent under that reading (the standard one), and a `*` word
/// absent everywhere. A good row and an empty table load.
#[test]
fn engine_new_rejects_rows_that_match_nothing() {
    let new = |tag: &str, text: &str| {
        let d = data_dir_with(tag, text);
        let ok = Engine::new(&d, Layout::Standard).is_ok();
        std::fs::remove_dir_all(&d).unwrap();
        ok
    };
    assert!(new("good", "ㄍㄠˇ-ㄨㄢˊ\t睪丸\t2.0\treading\tx\n"));
    assert!(new("star", "*\t睪丸\t2.0\treading\tx\n"));
    assert!(new("empty", "# nothing\n"));
    assert!(!new("space", "ㄍㄠˇ ㄨㄢˊ\t睪丸\t2.0\treading\tx\n"), "a mistyped reading");
    assert!(!new("absent-word", "ㄍㄠˇ-ㄨㄢˊ\t睪丸癌\t2.0\treading\tx\n"), "a word absent under that reading");
    assert!(!new("no-reading", "ㄍㄠˇ-ㄍㄠˇ\t睪丸\t2.0\treading\tx\n"), "a reading the lexicon lacks");
    assert!(!new("star-absent", "*\t睪丸睪丸\t2.0\treading\tx\n"), "a `*` word the lexicon lacks");
}

/// Measurement for the research log (contract section 3, not an acceptance gate): after one re-pick of
/// 睪丸 under ㄍㄠˇ ㄨㄢˊ (learning on, memory only), the top-1 of the same typing with demotion on.
/// Run: cargo test --release --test engine_demote -- --ignored --nocapture
#[test]
#[ignore]
fn measure_repick_of_the_demoted_word() {
    let s = shared();
    let repick = |ctx: &str, profile: Profile, reading: &str| -> String {
        let mut e = engine(&s.on, profile);
        e.set_today(Some(20_000));
        e.set_learning(true);
        let r = syls(reading);
        e.set_left_context(ctx);
        type_syls(&mut e, &r);
        // Teach: open the candidates of the span ending at the first two syllables, choose 睪丸, commit.
        for _ in 0..r.len() - 2 {
            e.key(Key::new(KeyKind::Left)).unwrap();
        }
        let mut o = e.key(Key::new(KeyKind::Space)).unwrap();
        for _ in 0..5000 {
            if o.candidates[o.selected.unwrap()] == "睪丸" {
                break;
            }
            o = e.key(Key::new(KeyKind::Right)).unwrap();
        }
        e.key(Key::new(KeyKind::Enter)).unwrap();
        e.key(Key::new(KeyKind::End)).unwrap();
        let taught = e.key(Key::new(KeyKind::Enter)).unwrap().commit;
        e.set_left_context(ctx);
        let again = type_syls(&mut e, &r).preedit;
        format!("{taught} -> {again}")
    };
    for (name, ctx) in [("切除", "切除"), ("none", "")] {
        for reading in ["ㄍㄠˇ ㄨㄢˊ", REPORT] {
            for (pn, p) in [("chat", Profile::Chat), ("formal", Profile::Formal)] {
                println!("MEASURE ctx={name} reading=[{reading}] {pn}: {}", repick(ctx, p, reading));
            }
        }
    }
}
