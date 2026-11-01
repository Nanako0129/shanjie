//! The reading term in the engine (docs/contracts/reading-decode-2.md section 2): the n-gram decoder adds `LAMBDA * max(t, -CAP)` to the path score of every
//! single-character word, unless learning lifted that word. Two groups. The first runs the shipping engine (`Engine::load_lm` on the real
//! lexicon, n-gram, small model and colloquial table; data/lm/ is needed, see data/*.sha256 and fails loudly without it): two sentences main wrote
//! after the rows of wikitune where the term changes the n-gram's first path, row 81 of eval/dev/user-reported.txt as the regression check of the removed
//! reading-channel term, and 搞完這波 as the non-regression. The second runs a toy model (eval/golden/kn-tiny): a learned word keeps its rare reading,
//! and `load_lm` refuses a data directory without `std-exempt.tsv`.
use core::engine::*;
use core::lm::{CappedLexicon, Learn, Lm, Profile, LEARN_EPS_GLOBAL};
use core::tinylm::RerankLevel;
use core::Lexicon;
use std::path::{Path, PathBuf};
use std::sync::Arc;

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}
fn lm_file(f: &str) -> PathBuf {
    let p = root().join("data/lm").join(f);
    assert!(p.exists(), "data/lm/{f} is needed: see data/*.sha256 and docs/releases/README.md");
    p
}

fn keys_of(syl: &str) -> Vec<Key> {
    let layout = Layout::Standard;
    let mut v = Vec::new();
    let mut toned = false;
    for c in syl.chars() {
        match layout.key_of_tone(c) {
            Some(tk) => {
                v.push(Key::ch(tk, 0));
                toned = true;
            }
            None => v.push(Key::ch(layout.key_of_symbol(c).expect("symbol has a key"), 0)),
        }
    }
    if !toned {
        v.push(Key::new(KeyKind::Space));
    }
    v
}

/// The shipping engine of one profile: acg and IT packs, `load_lm`, the small model and the colloquial table (all loaded; the term and the jobs decide per
/// profile what they use), standard strength.
fn shipping(profile: Profile) -> Engine {
    let pdir = root().join("data/packs");
    let mut e = Engine::new_with_packs(&root().join("data/lexicon"), Layout::Standard, Some((&pdir, PACK_ACG | PACK_IT))).unwrap();
    e.load_lm(&lm_file("bigram.sjlm")).unwrap();
    e.load_tinylm(&lm_file("tinylm.sjtl")).unwrap();
    e.load_colloquial(&lm_file("colloquial.sjlm")).unwrap();
    e.set_profile(profile).unwrap();
    e.set_rerank_level(RerankLevel::Standard).unwrap();
    e.set_prediction(false).unwrap();
    e
}

/// What the engine shows for `row` typed from an empty composition: (the n-gram's first path, what is shown once the small model's job is applied).
/// The job exists only with `rerank` on; off, the two are the same.
fn typed(e: &mut Engine, row: &str, rerank: bool, reading: bool) -> (String, String) {
    e.reset(ResetMode::Discard);
    e.set_rerank(rerank).unwrap();
    e.set_reading_decode(reading);
    let mut out = None;
    for syl in row.split(' ') {
        for k in keys_of(syl) {
            out = Some(e.key(k).unwrap());
        }
    }
    let ngram = out.unwrap().preedit;
    let shown = match e.take_rerank_job() {
        Some(job) => e.apply_rerank(job.run()).unwrap().map_or(ngram.clone(), |o| o.preedit),
        None => ngram.clone(),
    };
    (ngram, shown)
}

const WEI: &str = "ㄧˇ ㄉㄧˊ ㄨㄟˋ ㄖㄣˊ ㄩㄢˊ ㄉㄜ˙ ㄇㄧㄥˊ ㄗˋ ㄇㄧㄥˋ ㄇㄧㄥˊ";
const SHU: &str = "ㄓㄜˋ ㄍㄜ˙ ㄕㄨˇ ㄐㄧㄠˋ ㄙㄢˋ ㄅㄞˊ ㄧˇ ㄕㄨˇ";

/// The four settings: (profile, small model on). Rerank "on" is the standard strength, "off" is semantic correction off (the n-gram alone).
const SETTINGS: [(Profile, bool); 4] = [(Profile::Formal, true), (Profile::Formal, false), (Profile::Chat, true), (Profile::Chat, false)];

/// 以敵偽人員的名字命名 (main's sentence, after wikitune): without the reading term 偽 read ㄨㄟˋ goes to 為 and 敵 to 狄 in the written settings (formal, rerank on
/// and off: 以狄為…), with it the whole sentence is right. In chat it is right either way (the colloquial term and the small model get it), so the term changes nothing there.
#[test]
fn the_term_makes_the_written_sentence_right() {
    let gold = "以敵偽人員的名字命名";
    for (profile, tl) in [(Profile::Formal, true), (Profile::Formal, false)] {
        let mut e = shipping(profile);
        let (off_ng, off) = typed(&mut e, WEI, tl, false);
        let (on_ng, on) = typed(&mut e, WEI, tl, true);
        assert!(off.starts_with("以狄為") && off_ng.starts_with("以狄為"), "premise, {profile:?} rerank {tl}: without the term {off} / {off_ng}");
        assert_eq!((on_ng.as_str(), on.as_str()), (gold, gold), "{profile:?} rerank {tl}");
    }
    for tl in [true, false] {
        let mut e = shipping(Profile::Chat);
        assert_eq!(typed(&mut e, WEI, tl, false).1, gold, "chat rerank {tl}, term off: right already");
        assert_eq!(typed(&mut e, WEI, tl, true).1, gold, "chat rerank {tl}, term on: unchanged");
    }
}

/// 這個屬叫散白蟻屬: the n-gram's first path reads 屬 (ㄕㄨˇ) as 數 without the term, as 屬 with it, in the four settings. The rest of the sentence is not all right
/// (叫 comes out 較), so what is asserted is the first path's third character. Shown after the small model's job the sentence may change again; that is not asserted.
#[test]
fn the_term_changes_the_first_path_to_shu() {
    for (profile, tl) in SETTINGS {
        let mut e = shipping(profile);
        let (off, _) = typed(&mut e, SHU, tl, false);
        let (on, _) = typed(&mut e, SHU, tl, true);
        assert_eq!(off.chars().nth(2), Some('數'), "premise, {profile:?} rerank {tl}: without the term {off}");
        assert_eq!(on.chars().nth(2), Some('屬'), "{profile:?} rerank {tl}: with the term {on}");
    }
}

/// Row 81 of eval/dev/user-reported.txt, 長歪了 (ㄓㄤˇ ㄨㄞ ㄌㄜ˙), chat standard: v0.6.1 (main) showed candidate 2 here, the wrong one, because the reading-channel term
/// was added to the colloquial term's fusion (cli/tests/colloquial.rs pins `[2, 0, 0]` on main). The term is gone from the fusion, so the pick is candidate 0, and the
/// sentence is right, with the decoder's reading term on and off alike: the regression check of the removal, not an effect of the reading term.
#[test]
fn row_81_is_right_without_the_reading_channel() {
    const MAIN_PIN: usize = 2;
    let (row, gold) = ("ㄓㄤˇ ㄨㄞ ㄌㄜ˙", "長歪了");
    let mut e = shipping(Profile::Chat);
    for reading in [true, false] {
        e.reset(ResetMode::Discard);
        e.set_rerank(true).unwrap();
        e.set_reading_decode(reading);
        for syl in row.split(' ') {
            for k in keys_of(syl) {
                e.key(k).unwrap();
            }
        }
        let job = e.take_rerank_job().expect("a job");
        let picks = job.run().picks();
        assert_ne!(picks, vec![MAIN_PIN], "premise, reading term {reading}: main's pin was wrong");
        assert_eq!(picks, vec![0], "reading term {reading}");
        assert_eq!(typed(&mut e, row, true, reading).1, gold, "reading term {reading}");
    }
}

/// 搞完這波 (row 37 of the same set): the lexicon ranks 波's standard reading ㄅㄛ behind ㄆㄛ, so the bare term (R1f) lost this row and the exemptions (R1g) keep it:
/// right in all four settings with the term on, and with it off.
#[test]
fn gaowan_zhe_bo_does_not_regress() {
    let (row, gold) = ("ㄍㄠˇ ㄨㄢˊ ㄓㄜˋ ㄅㄛ", "搞完這波");
    for (profile, tl) in SETTINGS {
        let mut e = shipping(profile);
        for reading in [true, false] {
            let (ng, shown) = typed(&mut e, row, tl, reading);
            assert_eq!((ng.as_str(), shown.as_str()), (gold, gold), "{profile:?} rerank {tl}, reading term {reading}");
        }
    }
}

// ---------- toy model ----------

fn toy_dir() -> PathBuf {
    root().join("eval/golden/kn-tiny")
}
fn toy_lm() -> Arc<Lm> {
    let b = |n: &str| std::fs::read(toy_dir().join(n)).unwrap();
    let mut lm = Lm::parse_with_classes(&b("bigram.sjlm"), &b("classes.sjc")).unwrap();
    lm.add_kn(&b("kn.sjkn")).unwrap();
    Arc::new(lm)
}

/// A scratch data directory removed on drop.
struct Dir(PathBuf);
impl Drop for Dir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}
fn scratch(tag: &str) -> Dir {
    let d = std::env::temp_dir().join(format!("shanjie-reading-decode-{}-{tag}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    Dir(d)
}

fn type_syllable(e: &mut Engine, symbols: &str, tone: char) -> Output {
    let l = Layout::Standard;
    for c in symbols.chars() {
        e.key(Key::ch(l.key_of_symbol(c).unwrap(), 0)).unwrap();
    }
    e.key(Key::ch(l.key_of_tone(tone).unwrap(), 0)).unwrap()
}

/// Learning lifts a word above the reading term. In this lexicon 佔 reads ㄓㄢˋ rarely (-3.5, against -1.0 under ㄓㄢ: t = -2.5, a term of -5 at LAMBDA 2) and 占 has only that
/// reading. Taught once (pick 佔 over 占 after 我們, Enter), 佔 is lifted by up to LEARN_EPS * 1/2 = 3 over the top entry; the term would take 5 back, so only the
/// exemption of a learned word (`was_boosted` in the decoder) keeps it first. Mutation check: dropping the condition (`rd_term` added for a lifted word too) makes
/// the last assertion fail (recorded in the commit message).
#[test]
fn a_learned_word_keeps_its_rare_reading() {
    let lm = toy_lm();
    let text = "ㄓㄢˋ 占 -3.0\nㄓㄢˋ 佔 -3.5\nㄓㄢ 佔 -1.0\n";
    let lex = Arc::new(Lexicon::parse_with(text, None).unwrap());
    let d = scratch("learned");
    std::fs::write(d.0.join("mcbpmf-data.txt"), text).unwrap();
    std::fs::write(d.0.join(READING_EXEMPT_FILE), "").unwrap();
    let rd = load_reading_decode(&lex, &d.0).unwrap();
    assert!(rd.term(&lex, "ㄓㄢˋ", "佔") <= -4.99, "premise: the rare reading costs the term");
    assert_eq!(rd.term(&lex, "ㄓㄢˋ", "占"), 0.0);
    let capped = Arc::new(CappedLexicon::new(lex.clone(), "", &lm, None).unwrap().with_reading(rd));
    let (left, today) = ("我們", 20_000);
    let mut e = Engine::with_lexicon(lex.clone(), Layout::Standard);
    e.set_lm(lm.clone(), capped.clone());
    e.set_prediction(false).unwrap();
    e.set_learning(true);
    e.set_today(Some(today));
    let zhan = |e: &mut Engine| {
        e.set_left_context(left);
        type_syllable(e, "ㄓㄢ", 'ˋ')
    };
    assert_eq!(zhan(&mut e).preedit, "占", "premise: unlearned, 占 is first");
    let o = e.key(Key::new(KeyKind::Down)).unwrap();
    let at = o.candidates.iter().position(|c| c == "佔").expect("佔 is a candidate");
    e.pick(at).unwrap();
    e.key(Key::new(KeyKind::Enter)).unwrap();
    assert!(e.learner().records().iter().any(|r| r.word == "佔"), "premise: 佔 was learned");
    // Premise: the lift alone is what puts 佔 first, the term (a lexicon without it) agrees.
    let plain = Arc::new(CappedLexicon::new(lex.clone(), "", &lm, None).unwrap());
    let mut p = Engine::with_lexicon(lex.clone(), Layout::Standard);
    p.set_lm(lm.clone(), plain);
    p.set_prediction(false).unwrap();
    p.set_learning(true);
    p.set_today(Some(today));
    zhan(&mut p);
    let o = p.key(Key::new(KeyKind::Down)).unwrap();
    p.pick(o.candidates.iter().position(|c| c == "佔").unwrap()).unwrap();
    p.key(Key::new(KeyKind::Enter)).unwrap();
    assert_eq!(zhan(&mut p).preedit, "佔", "premise: without the reading term the learned word is first");
    // The unlifted path of the same engine is penalised by the term (the learned word is first only because it was lifted): ask the decoder without learning.
    let learn = Learn { learner: e.learner(), before: left, after: None, today, eps_global: LEARN_EPS_GLOBAL };
    let syls = vec!["ㄓㄢˋ".to_string()];
    let first = |learn: Option<&Learn>| -> String {
        let nb = core::lm::decode_segment_learned(&capped, &syls, &lm, Profile::Chat.lambda(), core::lm::history(left, &lm), core::lm::End::Eos, 64, learn, true).unwrap();
        nb[0].1[0].0.to_string()
    };
    assert_eq!(first(None), "占", "premise: without the lift the term keeps 占 first");
    assert_eq!(first(Some(&learn)), "佔", "the lifted word is not penalised by the term");
    assert_eq!(zhan(&mut e).preedit, "佔", "through the engine");
}

/// `load_lm` reads `std-exempt.tsv` from the data directory: absent, it fails (never a table without the exemptions); a malformed row fails too; an empty file loads.
/// A failed load leaves the engine without a model, as every failed load does.
#[test]
fn load_lm_needs_std_exempt() {
    let d = scratch("exempt");
    for f in std::fs::read_dir(root().join("data/lexicon")).unwrap() {
        let f = f.unwrap().path();
        if f.file_name().unwrap() != READING_EXEMPT_FILE {
            std::os::unix::fs::symlink(&f, d.0.join(f.file_name().unwrap())).unwrap();
        }
    }
    let new = || Engine::new(&d.0, Layout::Standard).unwrap();
    assert!(matches!(new().load_lm(&lm_file("bigram.sjlm")), Err(EngineError::LoadFailed)), "no std-exempt.tsv");
    std::fs::write(d.0.join(READING_EXEMPT_FILE), "ㄅㄛ\t波\textra\n").unwrap();
    assert!(matches!(new().load_lm(&lm_file("bigram.sjlm")), Err(EngineError::LoadFailed)), "a row with three columns");
    std::fs::write(d.0.join(READING_EXEMPT_FILE), "ㄅㄛ\t波波\n").unwrap();
    assert!(matches!(new().load_lm(&lm_file("bigram.sjlm")), Err(EngineError::LoadFailed)), "a word of two characters");
    std::fs::write(d.0.join(READING_EXEMPT_FILE), "# nothing\n").unwrap();
    assert!(new().load_lm(&lm_file("bigram.sjlm")).is_ok(), "an empty list loads");
    // and the shipped list is the one the data directory holds
    let shipped = std::fs::read_to_string(root().join("data/lexicon").join(READING_EXEMPT_FILE)).unwrap();
    assert_eq!(shipped.lines().filter(|l| !l.starts_with('#') && !l.trim().is_empty()).count(), 12, "the 12 pairs of R1g");
}
