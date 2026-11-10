//! The rerank-protect list (docs/spec/lexicon-and-packs.md §2.12, docs/spec/decoder.md §7): `Lexicon::with_protected` and the load of
//! `data/lexicon/rerank-protect.tsv` by `engine::load_lexicon_split`. The protect words join the manual mask and change no score.

use core::engine::*;
use core::lm::Profile;
use core::tinylm::{manual_mask, RerankLevel};
use core::Lexicon;
use std::path::{Path, PathBuf};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}

fn strs(v: &[&str]) -> Vec<String> {
    v.iter().map(|s| s.to_string()).collect()
}

/// A toy lexicon: 權限 is a base word, 全線 its homophone.
fn toy() -> Lexicon {
    Lexicon::parse("ㄑㄩㄢˊ-ㄒㄧㄢˋ 權限 -5.0\nㄑㄩㄢˊ-ㄒㄧㄢˋ 全線 -6.0\nㄑㄩㄢˊ 權 -4.0\nㄒㄧㄢˋ 限 -4.0\n").unwrap()
}

const QX: [&str; 2] = ["ㄑㄩㄢˊ", "ㄒㄧㄢˋ"];

#[test]
fn a_protected_base_word_is_masked_like_a_manual_word() {
    let lex = toy();
    let syl = strs(&QX);
    assert_eq!(manual_mask(&syl, &["權限", "全線"], lex.manual()), [false, false], "premise: no protection without the list");
    let lex = lex.with_protected("# a comment\n\n權限\tㄑㄩㄢˊ ㄒㄧㄢˋ\tnote\n").unwrap();
    assert_eq!(manual_mask(&syl, &["權限", "全線"], lex.manual()), [false, true], "the candidate that drops 權限 is masked");
    assert_eq!(manual_mask(&syl, &["全線", "權限"], lex.manual()), [false, false], "the first does not hold it: nothing protected");
}

#[test]
fn protection_changes_no_score_or_entry() {
    let before = toy();
    let after = toy().with_protected("權限\tㄑㄩㄢˊ ㄒㄧㄢˋ\tnote\n").unwrap();
    let key = strs(&QX);
    assert_eq!(before.entries(&key), after.entries(&key));
    assert_eq!(before.reading_count(), after.reading_count());
}

#[test]
fn a_comment_only_list_protects_nothing() {
    let lex = toy().with_protected("# nothing here\n\n").unwrap();
    assert!(lex.manual().is_empty());
}

#[test]
fn bad_rows_are_errors_naming_the_line() {
    for (text, line, what) in [
        ("權限\tㄑㄩㄢˊ ㄒㄧㄢˋ\n", 1, "two fields"),
        ("# c\n權限\tㄑㄩㄢˊ ㄒㄧㄢˋ\tn\textra\n", 2, "four fields"),
        ("權a\tㄑㄩㄢˊ ㄒㄧㄢˋ\tn\n", 1, "not all Han"),
        ("權限\tㄑㄩㄢˊ\tn\n", 1, "length differs from the syllables"),
        ("全限\tㄑㄩㄢˊ ㄒㄧㄢˋ\tn\n", 1, "not in the lexicon"),
        ("權限\tㄑㄩㄢˊ ㄒㄧㄢ\tn\n", 1, "not under this reading"),
    ] {
        let err = toy().with_protected(text).err().unwrap_or_else(|| panic!("{what}: accepted"));
        assert!(err.starts_with(&format!("line {line}:")), "{what}: {err}");
    }
}

/// The data directory's list loads, and every row names a word the lexicon has under that reading (else the load fails).
#[test]
fn the_shipped_list_loads() {
    let e = Engine::new_with_packs(&root().join("data/lexicon"), Layout::Standard, None);
    assert!(e.is_ok(), "the shipped rerank-protect.tsv loads");
}

/// A data directory without rerank-protect.tsv is a load failure, not an engine without protection.
#[test]
fn a_missing_list_is_a_load_failure() {
    let d = scratch("missing");
    link_lexicon_dir(&d.0, None);
    assert!(Engine::new_with_packs(&d.0, Layout::Standard, None).is_err());
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

/// The shipping engine of one profile on the lexicon directory `dir` (acg and IT packs, the small model and the colloquial table), standard strength.
fn shipping(dir: &Path, profile: Profile) -> Engine {
    let pdir = root().join("data/packs");
    let mut e = Engine::new_with_packs(dir, Layout::Standard, Some((&pdir, PACK_ACG | PACK_IT))).unwrap();
    e.load_lm(&lm_file("bigram.sjlm")).unwrap();
    e.load_tinylm(&lm_file("tinylm.sjtl")).unwrap();
    e.load_colloquial(&lm_file("colloquial.sjlm")).unwrap();
    e.set_profile(profile).unwrap();
    e.set_rerank_level(RerankLevel::Standard).unwrap();
    e.set_prediction(false).unwrap();
    e
}

/// (the n-gram's first path, what is shown once the small model's job is applied) for `row` typed from an empty composition, rerank on.
fn typed(e: &mut Engine, row: &str) -> (String, String) {
    e.reset(ResetMode::Discard);
    e.set_rerank(true).unwrap();
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

/// Row 179 of eval/dev/user-reported.txt (權限加好了, empty context), formal, standard strength: the n-gram's first path is right, and the small
/// model prefers the homophone 全線 (measured 2026-11-10: ll −12.557 against −13.224, fused +0.43). With 權限 protected the shown sentence stays
/// right; with a comment-only list (the same lexicon directory otherwise) the rerank puts 全線 in its place (the premise).
#[test]
fn row_179_is_right_with_the_protect_list() {
    const ROW: &str = "ㄑㄩㄢˊ ㄒㄧㄢˋ ㄐㄧㄚ ㄏㄠˇ ㄌㄜ˙";
    let d = scratch("empty");
    link_lexicon_dir(&d.0, Some("# no protect words\n"));
    let mut bare = shipping(&d.0, Profile::Formal);
    assert_eq!(typed(&mut bare, ROW), ("權限加好了".to_string(), "全線加好了".to_string()), "premise: without the list the rerank takes 權限 out");
    let mut e = shipping(&root().join("data/lexicon"), Profile::Formal);
    assert_eq!(typed(&mut e, ROW), ("權限加好了".to_string(), "權限加好了".to_string()));
}

struct Dir(PathBuf);
impl Drop for Dir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}
fn scratch(tag: &str) -> Dir {
    let d = std::env::temp_dir().join(format!("shanjie-rerank-protect-{}-{tag}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    Dir(d)
}

/// `dir` gets every file of data/lexicon (symlinked, or copied without symlinks) except rerank-protect.tsv, which is written with `protect`
/// (left out when `None`).
fn link_lexicon_dir(dir: &Path, protect: Option<&str>) {
    for f in std::fs::read_dir(root().join("data/lexicon")).unwrap() {
        let f = f.unwrap();
        let name = f.file_name();
        if name != RERANK_PROTECT_FILE {
            // a symlink where the platform has one (cheap), a copy elsewhere (the Windows CI job builds and runs this test too)
            #[cfg(unix)]
            std::os::unix::fs::symlink(f.path(), dir.join(&name)).unwrap();
            #[cfg(not(unix))]
            std::fs::copy(f.path(), dir.join(&name)).unwrap();
        }
    }
    if let Some(t) = protect {
        std::fs::write(dir.join(RERANK_PROTECT_FILE), t).unwrap();
    }
}
