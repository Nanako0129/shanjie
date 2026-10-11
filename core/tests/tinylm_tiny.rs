//! The small character model on the random toy model of tools/gen_tinylm_tiny.py (docs/contracts/s7c-core-tinylm.md section 3, item 3).
//! The files under eval/golden/tinylm-tiny/ are written by that script; items.tsv holds numpy (float64) LLs on the same restored
//! weights, one column for the f32 file and one for the int8 file; tiny-int8-v2.sjtl is tiny-int8.sjtl as SJTL0002 with chat delta
//! 0.25 and formal delta 2 (docs/contracts/s7c2c-delta.md); pick.tsv is fusegrid.pick on random lists. The bad-file cases patch
//! the files at the header offsets of experiments/s7a-tinylm/export_sjtl.py.
use core::lm::Profile;
use core::tinylm::{pick, Backend, TinyLm, TinyLmError};
use std::path::{Path, PathBuf};

fn dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../eval/golden/tinylm-tiny")
}
fn bytes(name: &str) -> Vec<u8> {
    std::fs::read(dir().join(name)).unwrap()
}
/// (k, c, LL of the f32 file, LL of the int8 file)
fn items() -> Vec<(String, String, f64, f64)> {
    let text = String::from_utf8(bytes("items.tsv")).unwrap();
    text.lines()
        .map(|l| {
            let f: Vec<&str> = l.split('\t').collect();
            (f[0].to_string(), f[1].to_string(), f[2].parse().unwrap(), f[3].parse().unwrap())
        })
        .collect()
}

#[test]
fn ll_matches_numpy_on_both_files() {
    let its = items();
    assert!(its.len() > 8, "more than one batch");
    let pairs: Vec<(&str, &str)> = its.iter().map(|(k, c, ..)| (k.as_str(), c.as_str())).collect();
    let (mut worst, mut spread) = (0f64, 0f64);
    for (file, col) in [("tiny-f32.sjtl", 0), ("tiny-int8.sjtl", 1)] {
        let m = TinyLm::parse(&bytes(file)).unwrap();
        let (a, p) = (m.ll_many_with(Backend::DEFAULT, &pairs).unwrap(), m.ll_many_with(Backend::Plain, &pairs).unwrap());
        for (((k, c, f32_ll, int8_ll), g), g_plain) in its.iter().zip(&a).zip(&p) {
            let want = if col == 0 { f32_ll } else { int8_ll };
            for got in [g, g_plain] {
                assert!((got - want).abs() < 1e-4, "{file} k={k:?} c={c:?}: {got} vs {want}");
                worst = worst.max((got - want).abs());
            }
            spread = spread.max((g - g_plain).abs());
        }
    }
    // the weights are scaled up here (tools/gen_tinylm_tiny.py) and one LL is about -135, so the two paths are held to 1e-4;
    // the 1e-5 bound on ordinary random weights is the unit test `matrix_paths_agree`
    assert!(spread < 1e-4, "default backend vs plain {spread}");
    eprintln!("max |dLL| vs numpy {worst:.3e}; default backend vs plain {spread:.3e}");
}

/// One-by-one scoring gives the same numbers as the batch: the padding and the batch split change nothing.
#[test]
fn batching_does_not_change_ll() {
    let its = items();
    let m = TinyLm::parse(&bytes("tiny-int8.sjtl")).unwrap();
    let pairs: Vec<(&str, &str)> = its.iter().map(|(k, c, ..)| (k.as_str(), c.as_str())).collect();
    let all = m.ll_many(&pairs).unwrap();
    for (p, a) in pairs.iter().zip(&all) {
        assert!((m.ll_many(&[*p]).unwrap()[0] - a).abs() < 1e-5, "{p:?}");
    }
}

#[test]
fn context_length_rules() {
    let m = TinyLm::parse(&bytes("tiny-f32.sjtl")).unwrap();
    assert!(m.ll_many(&[("你好", &"我".repeat(63))]).is_ok(), "63 characters fit after [BOS]");
    assert_eq!(m.ll_many(&[("", &"我".repeat(64))]).err(), Some(TinyLmError::TooLong));
    // the oldest characters of k are the ones dropped: a k longer than the room scores like its tail
    let k: String = "我你他好嗎的是不了在".repeat(8);
    let tail: String = k.chars().skip(k.chars().count() - (63 - 3)).collect();
    assert_eq!(m.ll_many(&[(&k, "你好嗎")]).unwrap(), m.ll_many(&[(&tail, "你好嗎")]).unwrap());
    assert_ne!(m.ll_many(&[(&k, "你好嗎")]).unwrap(), m.ll_many(&[("", "你好嗎")]).unwrap(), "the context is used");
    assert_eq!(m.ll_many(&[("你", "")]).unwrap(), vec![0.0]);
}

#[test]
fn header_fields_are_read() {
    let m = TinyLm::parse(&bytes("tiny-int8.sjtl")).unwrap();
    assert_eq!(m.alpha_tau_delta(Profile::Chat), (0.7, 2.0, 0.0), "version 1: delta 0");
    assert_eq!(m.alpha_tau_delta(Profile::Formal), (0.5, 1.0, 0.0), "version 1: delta 0");
    let kn_tiny = std::fs::read(dir().join("../kn-tiny/bigram.sjlm")).unwrap();
    assert_eq!(m.ngram_sha256(), core::eval::sha256_hex(&kn_tiny));
}

/// Every bad file is an error naming the format, for both weight types.
#[test]
fn bad_files_are_format_errors() {
    for file in ["tiny-f32.sjtl", "tiny-int8.sjtl"] {
        let good = bytes(file);
        let at = |o: usize, v: &[u8]| {
            let mut b = good.clone();
            b[o..o + v.len()].copy_from_slice(v);
            b
        };
        let u32_at = |o: usize| u32::from_le_bytes(good[o..o + 4].try_into().unwrap());
        let (d, v) = (u32_at(24) as usize, u32_at(32) as usize);
        // the first tensor is emb: f32 values there, or int8 values followed by one f32 scale per row
        let nan_at = 100 + 4 * (v - 3) + if file.contains("int8") { v * d } else { 0 };
        let cases: Vec<(&str, Vec<u8>)> = vec![
            // the two crosses of magic and version field (s7c2c section 2.1): SJTL0002 with version 1, SJTL0001 with version 2
            ("magic", at(7, b"2")),
            ("version", at(8, &2u32.to_le_bytes())),
            ("unknown version SJTL0003 + 3", {
                let mut b = at(7, b"3");
                b[8..12].copy_from_slice(&3u32.to_le_bytes());
                b
            }),
            ("weight type", at(12, &2u32.to_le_bytes())),
            ("n_layer 0", at(16, &0u32.to_le_bytes())),
            ("n_layer 3 (length)", at(16, &3u32.to_le_bytes())),
            ("n_head 0", at(20, &0u32.to_le_bytes())),
            ("n_head not dividing d", at(20, &3u32.to_le_bytes())),
            ("d 0", at(24, &0u32.to_le_bytes())),
            ("huge d", at(24, &u32::MAX.to_le_bytes())),
            ("ctx 1", at(28, &1u32.to_le_bytes())),
            ("V 2", at(32, &2u32.to_le_bytes())),
            ("V + 1 (length)", at(32, &(v as u32 + 1).to_le_bytes())),
            ("alpha NaN", at(36, &f64::NAN.to_le_bytes())),
            ("alpha inf", at(52, &f64::INFINITY.to_le_bytes())),
            ("tau NaN", at(44, &f64::NAN.to_le_bytes())),
            ("duplicate in the table", at(104, &good[100..104])),
            ("code point beyond Unicode", at(100, &0x110000u32.to_le_bytes())),
            ("surrogate", at(100, &0xD800u32.to_le_bytes())),
            ("NaN weight", at(nan_at, &f32::NAN.to_le_bytes())),
            ("short", good[..good.len() - 4].to_vec()),
            ("trailing byte", [good.as_slice(), &[0]].concat()),
            ("header only", good[..100].to_vec()),
            ("empty", vec![]),
        ];
        for (name, b) in &cases {
            assert_eq!(TinyLm::parse(b).err(), Some(TinyLmError::Format), "{file}: {name}");
        }
        assert!(TinyLm::parse(&at(44, &f64::INFINITY.to_le_bytes())).is_ok(), "tau may be infinite (no threshold)");
    }
    assert!(TinyLmError::Format.to_string().contains(".sjtl") && TinyLmError::Io.to_string().contains(".sjtl"));
    assert_eq!(TinyLm::load(Path::new("/nonexistent/x.sjtl")).err(), Some(TinyLmError::Io));
}

/// Hand cases of the fusion rule (fusegrid.pick): the gate is strict, ties go to the better n-gram rank.
#[test]
fn pick_hand_cases() {
    // margin exactly tau: keep the first, although fusion would move to candidate 1 (s0 = -1 - 9 = -10, s1 = -3)
    assert_eq!(pick(&[-1.0, -3.0], &[-9.0, 0.0], 1.0, 2.0, 0.0), 0);
    assert_eq!(pick(&[-1.0, -3.0], &[-9.0, 0.0], 1.0, 2.0 + 1e-9, 0.0), 1, "margin just inside the gate: reranked");
    assert_eq!(pick(&[-1.0, -3.0], &[-9.0, 0.0], 1.0, f64::INFINITY, 0.0), 1, "no threshold");
    assert_eq!(pick(&[-1.0, -3.0], &[-9.0, 0.0], 0.0, f64::INFINITY, 0.0), 0, "alpha 0 keeps the n-gram order");
    // ties: s0 = -1 + (-1) = -2 = s1 = -2 + 0
    assert_eq!(pick(&[-1.0, -2.0], &[-1.0, 0.0], 1.0, f64::INFINITY, 0.0), 0);
    // candidates 1 and 2 tie for the best: the earlier one
    assert_eq!(pick(&[-1.0, -1.5, -1.5], &[-9.0, -1.0, -1.0], 1.0, f64::INFINITY, 0.0), 1);
    // fewer than two candidates
    assert_eq!(pick(&[-1.0], &[-5.0], 1.0, f64::INFINITY, 0.0), 0);
    assert_eq!(pick(&[], &[], 1.0, f64::INFINITY, 0.0), 0);
    // the best one may be any rank
    assert_eq!(pick(&[-1.0, -1.1, -1.2, -1.3], &[-5.0, -4.0, -3.0, 0.0], 1.0, f64::INFINITY, 0.0), 3);
}


// ---------- S7c-2c: SJTL0002 and delta (docs/contracts/s7c2c-delta.md section 3, item 1) ----------

const V2: &str = "tiny-int8-v2.sjtl";

/// parse that also turns a panic into a test failure on an assertion (a bad file must be an error, never a panic).
fn parse_no_panic(b: &[u8]) -> Result<Result<TinyLm, TinyLmError>, String> {
    std::panic::catch_unwind(|| TinyLm::parse(b)).map_err(|_| "panicked".to_string())
}
fn is_format(b: &[u8]) -> bool {
    matches!(parse_no_panic(b), Ok(Err(TinyLmError::Format)))
}

/// The version 2 file reads its deltas; everything else equals the version 1 int8 file: alpha, tau, the n-gram hash, and
/// ll_many on items.tsv bit for bit (a vocabulary read from 100 or tensors read from 116 would change the LLs).
#[test]
fn v2_reads_delta_and_otherwise_equals_v1() {
    let (v1, v2) = (TinyLm::parse(&bytes("tiny-int8.sjtl")).unwrap(), TinyLm::parse(&bytes(V2)).unwrap());
    assert_eq!(v2.alpha_tau_delta(Profile::Chat), (0.7, 2.0, 0.25));
    assert_eq!(v2.alpha_tau_delta(Profile::Formal), (0.5, 1.0, 2.0));
    assert_eq!(v1.alpha_tau_delta(Profile::Chat), (0.7, 2.0, 0.0));
    assert_eq!(v1.alpha_tau_delta(Profile::Formal), (0.5, 1.0, 0.0));
    assert_eq!(v1.ngram_sha256(), v2.ngram_sha256());
    assert_eq!(bytes(V2).len(), bytes("tiny-int8.sjtl").len() + 16);
    let its = items();
    let pairs: Vec<(&str, &str)> = its.iter().map(|(k, c, ..)| (k.as_str(), c.as_str())).collect();
    let (a, b) = (v1.ll_many(&pairs).unwrap(), v2.ll_many(&pairs).unwrap());
    assert!(a.iter().zip(&b).all(|(x, y)| x.to_bits() == y.to_bits()), "LLs differ: {a:?} vs {b:?}");
}

/// Section 2.1: every prefix of the version 2 file up to its whole header, the delta range, and one byte more or less are
/// `Format`, never a panic. -0.0 and the bound 1,000 are accepted.
#[test]
fn v2_bad_files_are_format_errors() {
    let good = bytes(V2);
    assert!(parse_no_panic(&good).unwrap().is_ok(), "control");
    for n in 0..=116 {
        assert!(is_format(&good[..n]), "prefix of {n} bytes");
    }
    let with = |at: usize, d: f64| {
        let mut b = good.clone();
        b[at..at + 8].copy_from_slice(&d.to_le_bytes());
        b
    };
    for at in [100, 108] {
        for d in [f64::NAN, -f64::NAN, -0.25, -1e-300, f64::INFINITY, f64::NEG_INFINITY, 1000.0 + 1e-9, 1e300] {
            assert!(is_format(&with(at, d)), "delta {d} at {at}");
        }
        for d in [-0.0, 0.0, 1000.0] {
            assert_eq!(parse_no_panic(&with(at, d)).unwrap().map(|_| ()), Ok(()), "delta {d} at {at} is accepted");
        }
    }
    assert!(is_format(&[good.as_slice(), &[0]].concat()), "one byte more");
    assert!(is_format(&good[..good.len() - 1]), "one byte less");
    // a version 2 file relabelled as version 1 (both fields) is 16 bytes too long for a 100-byte header
    let mut v1 = good.clone();
    v1[7] = b'1';
    v1[8..12].copy_from_slice(&1u32.to_le_bytes());
    assert!(is_format(&v1), "a version 2 body under a version 1 header");
}

/// The delta gate: equality switches (`s[j] - s[0] >= delta`, values exact in binary), a huge delta always keeps the first, and
/// delta 0 is the S7a rule (kept here as it was) on random lists.
#[test]
fn pick_delta_gate() {
    // s0 = -1 - 1 = -2, s1 = -1.25 - 0.5 = -1.75: s1 - s0 = 0.25 exactly
    let (ng, ll) = ([-1.0, -1.25], [-1.0, -0.5]);
    assert_eq!(pick(&ng, &ll, 1.0, f64::INFINITY, 0.25), 1, "equal to delta: switch");
    assert_eq!(pick(&ng, &ll, 1.0, f64::INFINITY, 0.375), 0, "below delta: keep");
    // s0 = -1 - 4 = -5, s1 = -2 - 1 = -3: exactly 2
    assert_eq!(pick(&[-1.0, -2.0], &[-4.0, -1.0], 1.0, f64::INFINITY, 2.0), 1);
    assert_eq!(pick(&[-1.0, -2.0], &[-4.0, -1.0], 1.0, f64::INFINITY, 2.0 + 1e-12), 0);
    assert_eq!(pick(&[-1.0, -2.0], &[-4.0, -1.0], 1.0, f64::INFINITY, -0.0), 1, "-0.0 is 0");
    // the S7a rule as it was before delta
    fn old(ng: &[f64], ll: &[f64], alpha: f64, tau: f64) -> usize {
        if ng.len() < 2 || ng[0] - ng[1] >= tau {
            return 0;
        }
        let (mut best, mut best_s) = (0, ng[0] + alpha * ll[0]);
        for i in 1..ng.len() {
            let s = ng[i] + alpha * ll[i];
            if s > best_s {
                (best, best_s) = (i, s);
            }
        }
        best
    }
    let mut st = 0x9e37_79b9_7f4a_7c15u64;
    let mut r = |s: f64| {
        st = st.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        (st >> 11) as f64 / (1u64 << 53) as f64 * s
    };
    let (mut moved, mut n) = (0, 0);
    for _ in 0..2000 {
        let len = 2 + (r(7.0) as usize);
        let ng: Vec<f64> = (0..len).map(|_| -r(10.0)).collect();
        let ll: Vec<f64> = (0..len).map(|_| -r(30.0)).collect();
        for (alpha, tau) in [(0.2, 1.0), (0.7, 2.0), (1.5, f64::INFINITY)] {
            let want = old(&ng, &ll, alpha, tau);
            assert_eq!(pick(&ng, &ll, alpha, tau, 0.0), want, "delta 0 is the old rule");
            assert_eq!(pick(&ng, &ll, alpha, tau, 1e9), 0, "a huge delta keeps the first");
            moved += (want != 0) as usize;
            n += 1;
        }
    }
    assert!(moved > n / 10, "the random lists move the choice often enough ({moved} of {n})");
}

/// Python parity: pick.tsv (tools/gen_tinylm_tiny.py) holds random ng / ll lists, plus hand lists whose fused gap lands exactly on
/// a grid delta or on 0 (PICK_HAND lists them), written with repr, and fusegrid.pick's choice at every (alpha, tau, delta) of its grid lines.
#[test]
fn pick_matches_fusegrid() {
    let text = String::from_utf8(bytes("pick.tsv")).unwrap();
    let mut lines = text.lines();
    let mut grid = || -> Vec<f64> {
        let l = lines.next().unwrap();
        l.split_once('\t').unwrap().1.split(',').map(|x| x.parse().unwrap()).collect()
    };
    let (alphas, taus, deltas) = (grid(), grid(), grid());
    assert!(deltas.contains(&0.25) && deltas.contains(&2.0) && taus.contains(&f64::INFINITY));
    let (mut rows, mut gated) = (0, 0);
    for l in lines {
        let f: Vec<&str> = l.split('\t').collect();
        let nums = |s: &str| -> Vec<f64> { s.split(',').map(|x| x.parse().unwrap()).collect() };
        let (ng, ll, want) = (nums(f[0]), nums(f[1]), f[2].split(',').map(|x| x.parse::<usize>().unwrap()).collect::<Vec<_>>());
        assert_eq!(want.len(), alphas.len() * taus.len() * deltas.len());
        let mut i = 0;
        for &a in &alphas {
            for &t in &taus {
                for &d in &deltas {
                    assert_eq!(pick(&ng, &ll, a, t, d), want[i], "ng {ng:?} ll {ll:?} alpha {a} tau {t} delta {d}");
                    gated += (d > 0.0 && want[i] == 0 && pick(&ng, &ll, a, t, 0.0) != 0) as usize;
                    i += 1;
                }
            }
        }
        rows += 1;
    }
    assert!(rows >= 40 && gated > 0, "{rows} lists, {gated} choices the gate kept");
}

/// `rerank` passes the profile's delta to `pick`: two candidates whose fused gap is about 0.1 (chat alpha 0.7) move under the
/// version 1 file and stay under the version 2 file (chat delta 0.25), with the same LLs.
#[test]
fn rerank_uses_the_files_delta() {
    let (v1, v2) = (TinyLm::parse(&bytes("tiny-int8.sjtl")).unwrap(), TinyLm::parse(&bytes(V2)).unwrap());
    let cands = ["你好嗎", "我們都"];
    let ll = v1.ll_many(&cands.iter().map(|c| ("", *c)).collect::<Vec<_>>()).unwrap();
    // s1 - s0 = (ng1 - ng0) + 0.7 (ll1 - ll0) = 0.1; the n-gram margin ng0 - ng1 stays below tau 2
    let ng = [0.0, 0.1 - 0.7 * (ll[1] - ll[0])];
    assert!(ng[0] - ng[1] < 2.0, "premise: inside the tau gate");
    let (c1, l1) = v1.rerank(&ng, &cands, "", Profile::Chat).unwrap();
    let (c2, l2) = v2.rerank(&ng, &cands, "", Profile::Chat).unwrap();
    assert_eq!(l1, l2, "same LLs");
    assert_eq!((c1, c2), (1, 0), "delta 0.25 keeps the first, delta 0 moves");
    assert_eq!(c2, pick(&ng, &l2.unwrap(), 0.7, 2.0, 0.25));
}
