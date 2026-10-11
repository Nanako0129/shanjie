//! The small character model on the random toy model of tools/gen_tinylm_tiny.py (docs/contracts/s7c-core-tinylm.md section 3, item 3).
//! The files under eval/golden/tinylm-tiny/ are written by that script; items.tsv holds numpy (float64) LLs on the same restored
//! weights, one column for the f32 file and one for the int8 file. The bad-file cases patch the files at the header offsets of
//! experiments/s7a-tinylm/export_sjtl.py.
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
    assert_eq!(m.alpha_tau(Profile::Chat), (0.7, 2.0));
    assert_eq!(m.alpha_tau(Profile::Formal), (0.5, 1.0));
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
            ("magic", at(7, b"2")),
            ("version", at(8, &2u32.to_le_bytes())),
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
    assert_eq!(pick(&[-1.0, -3.0], &[-9.0, 0.0], 1.0, 2.0), 0);
    assert_eq!(pick(&[-1.0, -3.0], &[-9.0, 0.0], 1.0, 2.0 + 1e-9), 1, "margin just inside the gate: reranked");
    assert_eq!(pick(&[-1.0, -3.0], &[-9.0, 0.0], 1.0, f64::INFINITY), 1, "no threshold");
    assert_eq!(pick(&[-1.0, -3.0], &[-9.0, 0.0], 0.0, f64::INFINITY), 0, "alpha 0 keeps the n-gram order");
    // ties: s0 = -1 + (-1) = -2 = s1 = -2 + 0
    assert_eq!(pick(&[-1.0, -2.0], &[-1.0, 0.0], 1.0, f64::INFINITY), 0);
    // candidates 1 and 2 tie for the best: the earlier one
    assert_eq!(pick(&[-1.0, -1.5, -1.5], &[-9.0, -1.0, -1.0], 1.0, f64::INFINITY), 1);
    // fewer than two candidates
    assert_eq!(pick(&[-1.0], &[-5.0], 1.0, f64::INFINITY), 0);
    assert_eq!(pick(&[], &[], 1.0, f64::INFINITY), 0);
    // the best one may be any rank
    assert_eq!(pick(&[-1.0, -1.1, -1.2, -1.3], &[-5.0, -4.0, -3.0, 0.0], 1.0, f64::INFINITY), 3);
}
