//! 0c on the toy model of tools/gen_oc_tiny.py (docs/contracts/oc-experimental.md section 3). eval/golden/oc-tiny/tiny.sjoc is an
//! SJOC0001 file written by tools/oc_ref.py; scores.tsv holds experiments/x0c/score_0c.py's `score` (one sequence per candidate) over
//! oc_ref's float64 forward on the same weights, in log10, or `-` for a row it cannot score. Rust must match within 1e-5 on both matrix
//! backends, give `None` exactly where the table does, and refuse every damaged file.
use core::oc::{Oc, OcError};
use core::tinylm::Backend;
use std::path::{Path, PathBuf};

fn dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../eval/golden/oc-tiny")
}

/// (context, syllables, candidates, expected scores or None)
type Row = (String, Vec<String>, Vec<String>, Option<Vec<f64>>);

fn rows() -> Vec<Row> {
    let text = std::fs::read_to_string(dir().join("scores.tsv")).unwrap();
    text.lines()
        .map(|l| {
            let f: Vec<&str> = l.split('\t').collect();
            assert_eq!(f.len(), 4, "{l:?}");
            let list = |s: &str, sep: char| s.split(sep).map(String::from).collect::<Vec<_>>();
            let want = if f[3] == "-" { None } else { Some(f[3].split(',').map(|x| x.parse().unwrap()).collect()) };
            (f[0].to_string(), list(f[1], ' '), list(f[2], '|'), want)
        })
        .collect()
}

fn refs(v: &[String]) -> Vec<&str> {
    v.iter().map(String::as_str).collect()
}

fn model() -> Oc {
    Oc::load(&dir().join("tiny.sjoc")).unwrap()
}

#[test]
fn scores_match_the_numpy_reference_on_both_backends() {
    let m = model();
    let (mut worst, mut spread, mut scored, mut none) = (0f64, 0f64, 0, 0);
    for (ctx, syl, cands, want) in rows() {
        let (a, p) = (m.score_with(Backend::DEFAULT, &ctx, &refs(&syl), &refs(&cands)), m.score_with(Backend::Plain, &ctx, &refs(&syl), &refs(&cands)));
        assert_eq!((a.len(), p.len()), (cands.len(), cands.len()));
        match want {
            None => {
                assert!(a.iter().chain(&p).all(Option::is_none), "{ctx:?} {syl:?} {cands:?} must not be scored");
                none += 1;
            }
            Some(want) => {
                for ((g, gp), w) in a.iter().zip(&p).zip(&want) {
                    let (g, gp) = (g.expect("scored"), gp.expect("scored"));
                    assert!((g - w).abs() < 1e-5, "{ctx:?} {syl:?} {cands:?}: {g} vs {w}");
                    assert!((gp - w).abs() < 1e-5, "plain backend {ctx:?} {syl:?} {cands:?}: {gp} vs {w}");
                    worst = worst.max((g - w).abs()).max((gp - w).abs());
                    spread = spread.max((g - gp).abs());
                }
                scored += 1;
            }
        }
    }
    assert!(scored >= 10 && none >= 5, "scored {scored}, unscorable {none}");
    eprintln!("max |dlog10| vs numpy {worst:.3e}; default backend vs plain {spread:.3e}");
}

/// The candidates do not influence each other: one by one gives the same scores as the batch (and the row with 10 candidates
/// crosses the batch size of 8).
#[test]
fn batching_does_not_change_scores() {
    let m = model();
    for (ctx, syl, cands, want) in rows() {
        if want.is_none() {
            continue;
        }
        let all = m.score(&ctx, &refs(&syl), &refs(&cands));
        for (c, a) in cands.iter().zip(&all) {
            let one = m.score(&ctx, &refs(&syl), &[c.as_str()])[0].unwrap();
            assert!((one - a.unwrap()).abs() < 1e-5, "{ctx:?} {syl:?} {c:?}");
        }
    }
}

/// Only the last 64 characters of the context count; a character outside the vocabulary is the unknown token, whatever it is.
#[test]
fn context_window_and_unknown_characters() {
    let m = model();
    let long: String = "天氣很好真的我你他們是不了在有人這個說都就要會對可以時候來去吃飯".chars().cycle().take(100).collect();
    let tail: String = long.chars().skip(36).collect();
    let syl = ["ㄊㄧㄢ", "ㄑㄧ", "ㄏㄣˋ", "ㄏㄠˇ"];
    let cands = ["天氣很好", "真的我你"];
    assert_eq!(tail.chars().count(), 64);
    let (a, b) = (m.score(&long, &syl, &cands), m.score(&tail, &syl, &cands));
    assert!(a[0].is_some() && a == b, "100 characters score like their last 64");
    assert_ne!(m.score(&tail[3..], &syl, &cands), b, "the 64th character from the end counts");
    assert_eq!(m.score("A我", &syl, &cands), m.score("龘我", &syl, &cands), "UNK is one token");
    assert_ne!(m.score("A我", &syl, &cands), m.score("我", &syl, &cands), "an unknown character is still a position");
}

#[test]
fn damaged_files_are_refused() {
    let good = std::fs::read(dir().join("tiny.sjoc")).unwrap();
    let tmp = std::env::temp_dir().join(format!("shanjie-oc-tiny-{}", std::process::id()));
    std::fs::create_dir_all(&tmp).unwrap();
    let load = |name: &str, b: &[u8]| {
        let p = tmp.join(name);
        std::fs::write(&p, b).unwrap();
        Oc::load(&p).err()
    };
    assert_eq!(load("good", &good), None, "control");
    assert_eq!(load("short", &good[..good.len() - 4]), Some(OcError::Format), "one value short");
    assert_eq!(load("long", &[&good[..], &[0u8; 4]].concat()), Some(OcError::Format), "four bytes more");
    assert_eq!(load("odd", &good[..good.len() - 1]), Some(OcError::Format), "one byte short: not a whole value");
    assert_eq!(load("empty", &[]), Some(OcError::Io), "an empty file cannot be mapped");
    assert_eq!(load("header", &good[..100]), Some(OcError::Format), "inside the header");
    let patched = |at: usize, v: u32| {
        let mut b = good.clone();
        b[at..at + 4].copy_from_slice(&v.to_le_bytes());
        b
    };
    let mut magic = good.clone();
    magic[7] = b'2';
    assert_eq!(load("magic", &magic), Some(OcError::Format));
    // header fields (tools/oc_ref.py HEADER): version 8, weight type 12, n_layer 16, n_head 20, d 24, ffn 28, n_tok 32, n_out 36,
    // n_base 40, n_tone 44, n_type 48, n_rope 52
    for (what, at, v) in [("version", 8, 2), ("weight type", 12, 1), ("n_layer", 16, 3), ("n_head", 20, 3), ("d", 24, 18), ("ffn", 28, 13), ("n_tok", 32, 41), ("n_base", 40, 14), ("n_rope", 52, 3)] {
        assert_eq!(load(what, &patched(at, v)), Some(OcError::Format), "{what}");
    }
    // the character table: a duplicate refuses
    let mut dup = good.clone();
    dup.copy_within(120..124, 124);
    assert_eq!(load("dup", &dup), Some(OcError::Format), "duplicate character");
    assert_eq!(Oc::load(&tmp).err(), Some(OcError::Io), "a directory");
    assert_eq!(Oc::load(&tmp.join("missing")).err(), Some(OcError::Io), "no such file");
    std::fs::remove_dir_all(tmp).unwrap();
}

/// A row with more candidates than one batch (BATCH is 8) still gives the golden's scores: the golden has a 10-candidate row, and the
/// neutral-retry row of 6 candidates is cycled to 14 so the retry also crosses a batch.
#[test]
fn rows_with_more_than_a_batch_match_the_reference() {
    let m = model();
    let mut seen = (false, false);
    for (ctx, syl, cands, want) in rows() {
        let Some(want) = want else { continue };
        let retry = ctx == "你好" && cands.len() == 6 && syl[1].ends_with('ˊ');
        let (cands, want): (Vec<_>, Vec<_>) = if retry { (0..14).map(|i| (cands[i % 6].clone(), want[i % 6])).unzip() } else { (cands, want) };
        if cands.len() <= 8 {
            continue;
        }
        let got = m.score(&ctx, &refs(&syl), &refs(&cands));
        for (g, w) in got.iter().zip(&want) {
            assert!((g.expect("scored") - w).abs() < 1e-5, "{ctx:?} {syl:?}: {g:?} vs {w}");
        }
        if retry { seen.1 = true } else { seen.0 = true }
    }
    assert!(seen.0 && seen.1, "the 10-candidate row and the cycled retry row both ran");
}

/// A NaN in a weight is `NonFinite` from `try_score` and all-None from `score`. The decoder bias reaches the first attempt of every
/// row; the neutral tone's embedding only the retry (a merge by `f64::max` would have hidden it behind the finite first attempt).
#[test]
fn a_non_finite_weight_is_refused() {
    let good = std::fs::read(dir().join("tiny.sjoc")).unwrap();
    let h = |at: usize| u32::from_le_bytes(good[at..at + 4].try_into().unwrap()) as usize;
    // header (tools/oc_ref.py HEADER): n_layer 16, d 24, ffn 28, n_tok 32, n_base 40, n_tone 44, n_type 48, n_rope 52
    let (nl, d, ffn, nt, nb, ntone, nty, nr) = (h(16), h(24), h(28), h(32), h(40), h(44), h(48), h(52));
    let total = nt * d + nt + nb * d + ntone * d + nty * d + d + nr + nl * (4 * d * d + d + 3 * ffn * d) + (nl - 1) * d + 2 * d + d * d;
    let start = good.len() - 4 * total; // the tensors are the end of the file: dec, dec_b, base, tone, ...
    let dec_b = start + 4 * nt * d;
    let tone5 = dec_b + 4 * (nt + nb * d + 5 * d);
    let tmp = std::env::temp_dir().join(format!("shanjie-oc-nan-{}", std::process::id()));
    std::fs::create_dir_all(&tmp).unwrap();
    let nan = |name: &str, at: usize| {
        let mut b = good.clone();
        b[at..at + 4].copy_from_slice(&f32::NAN.to_le_bytes());
        let p = tmp.join(name);
        std::fs::write(&p, b).unwrap();
        Oc::load(&p).unwrap()
    };
    let all = rows();
    let row = |i: usize| (all[i].0.as_str(), refs(&all[i].1), refs(&all[i].2));
    let (c0, s0, k0) = row(0); // no retry
    let (c8, s8, k8) = row(8); // ends in a tone mark and a neutral-tolerant base: retried
    let k8_long: Vec<&str> = (0..14).map(|i| k8[i % k8.len()]).collect();

    let m = nan("dec_b", dec_b + 4 * 8);
    for (c, s, k) in [(c0, &s0, &k0), (c8, &s8, &k8)] {
        assert_eq!(m.try_score(c, s, k), Err(OcError::NonFinite));
        assert_eq!(m.score(c, s, k), vec![None; k.len()]);
    }

    let m = nan("tone5", tone5);
    assert!(m.try_score(c0, &s0, &k0).unwrap().iter().all(Option::is_some), "a row without the retry never reads the neutral tone");
    for k in [&k8, &k8_long] {
        assert_eq!(m.try_score(c8, &s8, k), Err(OcError::NonFinite), "the retry's NaN is not hidden by the first attempt");
        assert_eq!(m.score(c8, &s8, k), vec![None; k.len()]);
    }
    std::fs::remove_dir_all(tmp).unwrap();
}
