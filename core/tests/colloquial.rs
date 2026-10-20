//! The colloquial term (docs/contracts/colloquial-term.md section 2.2): lc, k and col on a synthetic table (expected values computed
//! by hand below and by experiments/p4-colloquial/p4.py), the sets read from literal texts, and `TinyLm::rerank_colloquial`.
use core::colloquial::{fused_pick, Colloquial, ColloquialError};
use core::tinylm::{TinyLm, TOPK};
use std::path::{Path, PathBuf};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}

fn strs(v: &[&str]) -> Vec<String> {
    v.iter().map(|s| s.to_string()).collect()
}

/// The toy table of core/src/lm.rs's tests: vocabulary `<s> </s> a b` (V 4), N 10, eos total 4 (p_eos 0.4), D 0.75; unigrams a 6, b 4;
/// context `<s>` (total 5: a 3, b 2), context `a` (total 4: `</s>` 2). So N + V + 1 = 15, back(`<s>`) = 0.3, back(a) = 0.6875.
fn tiny() -> Vec<u8> {
    let mut b = b"SJLM0001".to_vec();
    b.extend(4u32.to_le_bytes());
    b.extend(10u64.to_le_bytes());
    b.extend(4u64.to_le_bytes());
    b.extend(0.75f64.to_le_bytes());
    for w in ["<s>", "</s>", "a", "b"] {
        b.extend((w.len() as u16).to_le_bytes());
        b.extend(w.as_bytes());
    }
    for u in [0u64, 0, 6, 4] {
        b.extend(u.to_le_bytes());
    }
    b.extend(2u32.to_le_bytes());
    for c in [0u32, 2] {
        b.extend(c.to_le_bytes());
    }
    for t in [5u64, 4] {
        b.extend(t.to_le_bytes());
    }
    for o in [0u32, 2, 3] {
        b.extend(o.to_le_bytes());
    }
    b.extend(3u32.to_le_bytes());
    for n in [2u32, 3, 1] {
        b.extend(n.to_le_bytes());
    }
    for c in [3u32, 2, 2] {
        b.extend(c.to_le_bytes());
    }
    b
}

fn tiny_file(name: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("shanjie-core-colloquial-{name}-{}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    let p = d.join("tiny.sjlm");
    std::fs::write(&p, tiny()).unwrap();
    p
}

/// The toy table with the given sets: `ovl` as overlay rows (four tab fields), `base` as base rows (three fields, one syllable a character).
fn colloq(name: &str, ovl: &[&str], base: &[&str], pack: Option<&[&str]>) -> Colloquial {
    let rows = |w: &[&str]| w.iter().map(|w| format!("ㄅ\t{w}\t-3.0\t0\n")).collect::<String>();
    let base: String = base.iter().map(|w| format!("{} {w} -3.0\n", vec!["ㄅ"; w.chars().count()].join("-"))).collect();
    Colloquial::load(&tiny_file(name), &base, rows(ovl).as_bytes(), pack.map(rows).as_deref()).unwrap()
}

fn close(a: f64, b: f64) {
    assert!((a - b).abs() < 1e-9, "{a} vs {b}");
}

/// lc by hand: back-off to (c + 1) / 15, the end term to p_eos 0.4 when the last word has no entry for `</s>`.
#[test]
fn lc_by_hand() {
    let c = colloq("lc", &[], &[], None);
    let (back_s, back_a, pe): (f64, f64, f64) = (1.0 - ((3.0 - 0.75) + (2.0 - 0.75)) / 5.0, 1.0 - (2.0 - 0.75) / 4.0, 0.4);
    // a b: P(a|<s>) seen; P(b|a) unseen after a context with entries; P(</s>|b): b has no context, so the end rate itself
    let ab = ((3.0 - 0.75) / 5.0 + back_s * (7.0f64 / 15.0)).log10() + (back_a * (5.0f64 / 15.0)).log10() + pe.log10();
    close(c.lc(&strs(&["a", "b"])), ab);
    // a: the end term of a context that has `</s>`
    let a = ((3.0 - 0.75) / 5.0 + back_s * (7.0f64 / 15.0)).log10() + ((2.0f64 - 0.75) / 4.0 + back_a * pe).log10();
    close(c.lc(&strs(&["a"])), a);
    // a word outside the table: c = 0, no context of its own, so back(`<s>`) (its predecessor has entries) * 1/15, then the end rate
    let z = (back_s * (1.0f64 / 15.0)).log10() + pe.log10();
    close(c.lc(&strs(&["zzz"])), z);
    // after an unknown word the next word has no context: pb alone
    close(c.lc(&strs(&["zzz", "a"])), (back_s * (1.0f64 / 15.0)).log10() + (7.0 / 15.0f64).log10() + ((2.0f64 - 0.75) / 4.0 + back_a * pe).log10());
}

/// col = lc - 0.5 k; k counts overlay words that the base lexicon lacks, repeats twice.
#[test]
fn col_and_k() {
    let c = colloq("k", &["ovl", "dup", "both"], &["both", "a"], None);
    assert_eq!(c.k(&strs(&["dup", "a", "dup"])), 2, "a repeated overlay word counts twice");
    assert_eq!(c.k(&strs(&["both", "a", "names-only"])), 0, "in the base lexicon, or in no set: 0");
    assert_eq!(c.k(&strs(&["ovl"])), 1);
    let w = strs(&["dup", "a", "dup"]);
    close(c.col(&w), c.lc(&w) - 0.5 * 2.0);
    let w = strs(&["a"]);
    close(c.col(&w), c.lc(&w));
}

/// The difference set: a word in both overlay and base is not counted, an overlay-only word is, a base-only word is not, and a base row is
/// removed from the overlay wherever it comes from (overlay-add.tsv or the pack rows).
#[test]
fn a_word_in_overlay_and_base_is_not_counted() {
    let base = "ㄅ-ㄅ 共有 -3.0\nㄅ-ㄅ 包共 -3.0\n";
    let overlay = "ㄅ-ㄅ\t共有\t-3.0\t0\nㄅ-ㄅ\t只有疊加\t-3.0\t0\n";
    let pack = "ㄅ-ㄅ\t包共\t-3.0\tacg\nㄅ-ㄅ\t只有詞包\t-3.0\tacg\n";
    let c = Colloquial::load(&tiny_file("diff"), base, overlay.as_bytes(), Some(pack)).unwrap();
    assert_eq!(c.k(&strs(&["共有", "包共"])), 0, "in both sets");
    assert_eq!(c.k(&strs(&["只有疊加", "只有詞包"])), 2, "overlay-only words count, from overlay-add.tsv and from the pack");
    assert_eq!(c.k(&strs(&["不在任何表"])), 0);
}

/// The two sets from literal texts: a row with the wrong field count, a comment, a control line and a base row whose syllable count differs
/// from the characters are skipped; an overlay word that is also in the base lexicon is in the overlay set (k then leaves it out).
#[test]
fn sets_from_the_texts() {
    let base = "# comment\n_ctl x y\nㄋㄧˇ-ㄏㄠˇ 你好 -3.0\nㄋㄧˇ 壞詞 -1.0\n";
    let overlay = "ㄋㄧˇ-ㄏㄠˇ\t你好\t-3.0\t0\nㄔㄨㄥˊ-ㄉㄧㄝˊ\t重疊\t-4.0\t0\nthree\tfields\tonly\n";
    let table = tiny_file("sets");
    let c = Colloquial::load(&table, base, overlay.as_bytes(), None).unwrap();
    assert_eq!(c.k(&strs(&["你好", "重疊", "壞詞", "三"])), 1, "你好 is in both sets, 壞詞 has no row, 三 is in neither");
    assert_eq!(c.k(&strs(&["重疊", "重疊"])), 2);
    assert_eq!(Colloquial::load(&table, "ㄋㄧˇ-ㄏㄠˇ 你好 not-a-number\n", overlay.as_bytes(), None).err(), Some(ColloquialError::Lexicon));
    assert_eq!(Colloquial::load(Path::new("/nonexistent/x.sjlm"), base, overlay.as_bytes(), None).err(), Some(ColloquialError::Table));
    assert_eq!(c.sha256().len(), 64);
    // an overlay that is not UTF-8 is a read error, not a silently shorter set
    assert_eq!(Colloquial::load(&table, base, &[0xff, 0xfe, b'\n'][..], None).err(), Some(ColloquialError::Read));
}

/// k counts a pack word only when the pack text is given (the engine passes the rows of the packs it was built with).
#[test]
fn pack_rows_count_only_when_given() {
    let (with, without) = (colloq("p1", &["ovl"], &[], Some(&["詞包"])), colloq("p2", &["ovl"], &[], None));
    assert_eq!((with.k(&strs(&["詞包"])), without.k(&strs(&["詞包"]))), (1, 0));
    assert_eq!((with.k(&strs(&["ovl"])), without.k(&strs(&["ovl"]))), (1, 1));
}

/// Numbers computed once with experiments/p4-colloquial/p4.py (`python3 -I /path/gen.py` from the repo root, gen.py being: write `tiny()`'s
/// bytes to a file, `tb = p4.Table(file)`, rows = one dict {"cands": [(surface, ng, words)], "scores": {"ng": ng}}, then
/// `pre = p4.precompute([row], {"T": tb}, {"a", "b", "zzz"}, {"b"})[0]`, `pre["lc"]["T"]` for lc). No Python is needed to run this test.
#[test]
fn aligned_with_p4_py_committed() {
    let words: Vec<Vec<String>> = [&["a", "b"][..], &["a"], &["zzz"], &["zzz", "a"], &["b", "a", "a"]].iter().map(|w| strs(w)).collect();
    let lc = [-1.2669365492472555, -0.46014011741408184, -2.0969100130080562, -2.2609553524336694, -1.5116378202864988];
    let c = colloq("p4", &["a", "b", "zzz"], &["b"], None);
    for (w, want) in words.iter().zip(lc) {
        close(c.lc(w), want);
    }
    assert_eq!(words.iter().map(|w| c.k(w)).collect::<Vec<_>>(), [1, 1, 1, 2, 2]);
}

fn toy_sjtl() -> TinyLm {
    TinyLm::load(&root().join("eval/golden/tinylm-tiny/tiny-int8.sjtl")).unwrap()
}

/// `rerank_colloquial` is `ll_many` of the first TOPK candidates, then `fused_pick` over their `col`: the returned LLs are the raw ones, the
/// pick is the one `fused_pick` gives for the table's cols, fewer than two candidates run nothing, and words beyond TOPK are not looked at.
#[test]
fn rerank_colloquial_is_ll_then_fused_pick() {
    let co = colloq("rr", &["x"], &["a"], None);
    let tl = toy_sjtl();
    let cands = ["甲乙", "甲丙", "丁乙", "丁丙", "甲丁", "乙丙", "乙丁", "丙丁", "丙乙"];
    let words: Vec<Vec<&str>> = vec![vec!["a", "b"], vec!["x"], vec!["x", "x"], vec!["b"], vec!["a"], vec!["zzz"], vec!["a", "a"], vec!["b", "a"], vec!["x"]];
    let ng = [-10.0, -10.1, -10.2, -10.3, -10.4, -10.5, -10.6, -10.7, -10.8];
    let (pick, ll) = tl.rerank_colloquial(&ng, &cands, &words, "你", &co).unwrap();
    let ll = ll.unwrap();
    let want_ll = tl.ll_many(&cands[..TOPK].iter().map(|c| ("你", *c)).collect::<Vec<_>>()).unwrap();
    assert_eq!(ll, want_ll);
    let col: Vec<f64> = words[..TOPK].iter().map(|w| co.col(w)).collect();
    assert_eq!(pick, fused_pick(&ng[..TOPK], &ll, &col));
    assert_eq!(tl.rerank_colloquial(&ng[..1], &cands[..1], &words[..1], "你", &co).unwrap(), (0, None));
    assert_eq!(tl.rerank_colloquial(&ng, &cands, &words[..1], "你", &co).unwrap(), (0, None), "one split: fewer than two candidates");
}
