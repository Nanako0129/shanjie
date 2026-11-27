//! S7c-1: the small character language model (S7a) in Rust: the SJTL0001 / SJTL0002 / SJTL0003 / SJTL0004 / SJTL0005 loader, the forward
//! pass, LL and `pick` (docs/spec/semantic-rerank.md §3; SJTL0002, the per-profile delta, is
//! docs/spec/semantic-rerank.md §2.1; SJTL0003, a conservative and an aggressive (alpha, tau, delta) per profile beside
//! the standard one, is docs/spec/semantic-rerank.md §2.1; SJTL0004, the SHA-256 of the Kneser-Ney side file the
//! parameters were tuned with, is docs/spec/models.md §3.5, §8.1; SJTL0005, the declared left-context length, is
//! docs/spec/semantic-rerank.md §5.2).
//! S7c-2a (docs/spec/semantic-rerank.md §6) reaches it from the engine's rerank
//! jobs and the C ABI, so the loader is hardened for in-process use (docs/spec/semantic-rerank.md §2.2): a size cap, a regular-file check, header
//! bounds and finite restored weights.
//!
//! The model is `tinylm.NumpyLM` of tools/lib/tinylm.py in f32 (pre-LN decoder, tied embedding, learned positions, GELU tanh,
//! causal attention); `ll_many` is `tinylm.ll_many`. The file is written only by tools/lib/export_sjtl.py, whose
//! docstring is the format (and the tensor order, which `parse` repeats).
//! R2: errors carry a kind only, never text.
#![deny(unsafe_op_in_unsafe_fn)]

use crate::colloquial::{self, Colloquial};
use crate::learn::{context_key, is_han, last_chars, SENTINEL};
use crate::lm::{Lm, Profile, SinitLayer, EPS_SINIT_COARSE, EPS_SINIT_FINE, EPS_SINIT_MID};
use crate::mmap::{MapError, Mapped};
use crate::ManualSet;
use std::collections::HashMap;
use std::fmt;
use std::path::Path;

const MAGIC: &[u8; 8] = b"SJTL0001";
const HEADER: usize = 100;
const VERSION: u32 = 1;
/// S7c-2c: version 2 is version 1's 100 header bytes (magic and version field changed) plus the chat and formal delta (f64).
const MAGIC_V2: &[u8; 8] = b"SJTL0002";
const HEADER_V2: usize = 116;
const VERSION_V2: u32 = 2;
/// Rerank strength: version 3 is version 2's 116 header bytes (magic and version field changed) plus four (alpha, tau, delta)
/// f64 triples: chat conservative at 116, chat aggressive at 140, formal conservative at 164, formal aggressive at 188.
const MAGIC_V3: &[u8; 8] = b"SJTL0003";
const HEADER_V3: usize = 212;
const VERSION_V3: u32 = 3;
/// docs/spec/models.md §3.5, §8.1: version 4 is version 3's 212 header bytes (magic and version field changed) plus, at 212, the 32-byte
/// SHA-256 of the Kneser-Ney side file (kn.sjkn) alpha, tau and delta were tuned with; all zero = tuned without one. Versions
/// 1 to 3 count as all zero.
const MAGIC_V4: &[u8; 8] = b"SJTL0004";
const HEADER_V4: usize = 244;
const VERSION_V4: u32 = 4;
/// docs/spec/semantic-rerank.md §5.2: version 5 is version 4's 244 header bytes (magic and version field changed) plus, at 244, a u32: the
/// left-context length L in characters the model was trained to read, 1 <= L <= ctx - 2 (out of range: the file is refused).
/// Versions 1 to 4 declare none.
const MAGIC_V5: &[u8; 8] = b"SJTL0005";
const HEADER_V5: usize = 248;
const VERSION_V5: u32 = 5;
/// docs/spec/semantic-rerank.md §2.1, §5.2: version 6 is version 5's 248 header bytes (magic and version field changed) plus, at 248, a u32 of
/// format flags (bit 0: the boundary format; every other bit must be 0) and, at 252, a u32 policy-key threshold K (Han characters,
/// 1 <= K <= ctx - 2 with bit 0 set, else 0). With bit 0 the vocabulary is V - 4 characters, then [SEP] as id V - 1 (not in the code point list).
const MAGIC_V6: &[u8; 8] = b"SJTL0006";
const HEADER_V6: usize = 256;
const VERSION_V6: u32 = 6;
/// The boundary of a key string (`TinyLm::key_for_len`): U+0000 stands for [SEP]; every other non-Han character is merged into it first.
const SEP_CHAR: char = '\0';
/// docs/spec/semantic-rerank.md §2.1: a guard on delta, far above the S7e grid (at most 4).
const MAX_DELTA: f64 = 1000.0;
const PAD: u32 = 0;
const UNK: u32 = 1;
const BOS: u32 = 2;
/// Candidates per forward pass (right-padded with PAD; the causal mask keeps the padding out of the real positions).
pub const BATCH: usize = 8;
/// How many n-best candidates the model reranks (S7a: the first 8).
pub const TOPK: usize = 8;
/// docs/spec/semantic-rerank.md §2.2: the largest `.sjtl` `load` reads. The shipped M model in int8 is about 15 MB.
pub const MAX_FILE: u64 = 64 << 20;
// docs/spec/semantic-rerank.md §2.2: header bounds of the shipped model's order of magnitude, so that the forward pass's allocations are
// bounded too. The M model (S7c-1, docs/spec/semantic-rerank.md) is V 9,717 (9,714 characters + PAD, UNK, BOS), ctx 64, d 384, 6 layers.
const MAX_V: usize = 65_536;
const MAX_CTX: usize = 1_024;
const MAX_D: usize = 2_048;
const MAX_LAYERS: usize = 64;
const LN10: f64 = std::f64::consts::LN_10;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TinyLmError {
    Io,
    Format,
    /// The candidate alone does not fit the context (`ctx - 1` characters after [BOS]).
    TooLong,
    /// `load_paired`: the file was tuned with another n-gram model (its recorded SHA-256 differs).
    OtherNgram,
    /// `load_paired`: the Kneser-Ney side file of the n-gram model is not the one the file was tuned with (docs/spec/models.md §3.5, §8.1).
    KneserNey(KnPairing),
}

/// Which way the Kneser-Ney side file and the `.sjtl` disagree (docs/spec/models.md §3.5, §8.1: each needs another remedy).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum KnPairing {
    /// The side file is loaded, but the `.sjtl` was tuned without one (all-zero hash, or a version 1 to 3 file).
    TunedWithout,
    /// The side file is loaded, and the `.sjtl` was tuned with a different one.
    OtherSideFile,
    /// The side file is not loaded, but the `.sjtl` was tuned with one.
    TunedWith,
}

impl fmt::Display for TinyLmError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            TinyLmError::Io => "cannot read tiny language model file (.sjtl)",
            TinyLmError::Format => "bad tiny language model file (.sjtl)",
            TinyLmError::TooLong => "candidate does not fit the context of the tiny language model (.sjtl)",
            TinyLmError::OtherNgram => "this .sjtl was tuned with another n-gram model (SHA-256 differs)",
            TinyLmError::KneserNey(KnPairing::TunedWithout) => "the Kneser-Ney side file (kn.sjkn) is loaded, but this .sjtl was tuned without one",
            TinyLmError::KneserNey(KnPairing::OtherSideFile) => {
                "this .sjtl was tuned with another Kneser-Ney side file (the SHA-256 of kn.sjkn differs)"
            }
            TinyLmError::KneserNey(KnPairing::TunedWith) => "this .sjtl was tuned with a Kneser-Ney side file (kn.sjkn), but none is loaded",
        })
    }
}
impl std::error::Error for TinyLmError {}

/// Which of a profile's (alpha, tau, delta) the rerank uses (docs/spec/semantic-rerank.md §7). The codes are the C
/// ABI's (`shanjie_engine_set_rerank_level`); a version 1 or 2 file has only the standard one and gives it for every level.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RerankLevel {
    Conservative = 0,
    Standard = 1,
    Aggressive = 2,
}

impl RerankLevel {
    pub fn from_code(code: u32) -> Option<RerankLevel> {
        match code {
            0 => Some(RerankLevel::Conservative),
            1 => Some(RerankLevel::Standard),
            2 => Some(RerankLevel::Aggressive),
            _ => None,
        }
    }
}

/// The one check of every (alpha, tau, delta) in the header (docs/spec/semantic-rerank.md §10): alpha finite (a NaN alpha would pick
/// nothing sensible), tau not NaN (it may be infinite: no threshold), delta in [0, `MAX_DELTA`] (the range test also rejects NaN,
/// the infinities and negatives; -0.0 passes and compares as 0 in `pick`).
fn valid_params((alpha, tau, delta): (f64, f64, f64)) -> bool {
    alpha.is_finite() && !tau.is_nan() && (0.0..=MAX_DELTA).contains(&delta)
}

/// How the matrix products and the tanh / exp vectors are computed. `Plain` is the portable path; `Accelerate` is the one used on Apple
/// platforms (macOS ships; iOS is cross-compiled and tested in the simulator; Accelerate has the same calls there).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Backend {
    Plain,
    #[cfg(target_vendor = "apple")]
    Accelerate,
}

impl Backend {
    #[cfg(target_vendor = "apple")]
    pub const DEFAULT: Backend = Backend::Accelerate;
    #[cfg(not(target_vendor = "apple"))]
    pub const DEFAULT: Backend = Backend::Plain;
}

#[cfg(target_vendor = "apple")]
#[link(name = "Accelerate", kind = "framework")]
extern "C" {
    fn cblas_sgemm(
        order: i32, ta: i32, tb: i32, m: i32, n: i32, k: i32, alpha: f32, a: *const f32, lda: i32, b: *const f32, ldb: i32, beta: f32,
        c: *mut f32, ldc: i32,
    );
    fn vvtanhf(y: *mut f32, x: *const f32, n: *const i32);
    fn vvexpf(y: *mut f32, x: *const f32, n: *const i32);
}

/// The kernels of `Backend::Plain` (docs/spec/semantic-rerank.md §2.4). Every one computes the same numbers, bit for bit:
/// - not transposed: c[i][j] takes a[i][p] * b[p][j] for p = 0, 1, ... in that order, one multiplication and one addition each (no FMA);
/// - transposed: c[i][j] += dot(a[i], b[j]) with `dot` as `scalar::dot` defines it (16 partial sums, a fixed merge order, a scalar tail).
/// The vector kernels only change how many elements one instruction handles, never the order of the operations on one element.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Kernel {
    Scalar,
    #[cfg(target_arch = "x86_64")]
    Avx2,
    #[cfg(target_arch = "x86_64")]
    Avx512,
}

/// The kernel this CPU runs: decided by the standard library's runtime detection (cached there), never by the caller or the environment.
fn selected_kernel() -> Kernel {
    #[cfg(target_arch = "x86_64")]
    {
        if std::arch::is_x86_feature_detected!("avx512f") {
            return Kernel::Avx512;
        }
        if std::arch::is_x86_feature_detected!("avx2") {
            return Kernel::Avx2;
        }
    }
    Kernel::Scalar
}

/// The length precondition of every matrix product here, checked in release too: the Accelerate call and the vector kernels' loads
/// rely on it. `checked_mul`, so a product that wraps cannot pass.
fn check_gemm(a: &[f32], b: &[f32], c: &[f32], (m, k, n): (usize, usize, usize)) {
    assert!(
        m.checked_mul(k) == Some(a.len()) && k.checked_mul(n) == Some(b.len()) && m.checked_mul(n) == Some(c.len()),
        "gemm: matrix lengths do not match"
    );
}

/// c (m x n) += a (m x k) . b, with b (k x n), or b (n x k) transposed when `trans_b`. All row-major.
fn gemm(be: Backend, a: &[f32], b: &[f32], c: &mut [f32], (m, k, n): (usize, usize, usize), trans_b: bool) {
    check_gemm(a, b, c, (m, k, n));
    match be {
        #[cfg(target_vendor = "apple")]
        Backend::Accelerate => {
            assert!(m.max(k).max(n) < i32::MAX as usize, "gemm: dimension too large");
            // CblasRowMajor 101, CblasNoTrans 111, CblasTrans 112
            unsafe {
                cblas_sgemm(
                    101, 111, if trans_b { 112 } else { 111 }, m as i32, n as i32, k as i32, 1.0, a.as_ptr(), k as i32, b.as_ptr(),
                    if trans_b { k } else { n } as i32, 1.0, c.as_mut_ptr(), n as i32,
                );
            }
        }
        Backend::Plain => {
            let ran = gemm_kernel(selected_kernel(), a, b, c, (m, k, n), trans_b);
            debug_assert!(ran, "the selected kernel is one this CPU has");
        }
    }
}

/// `check_gemm` for a product whose b is int8 rows plus one f32 scale per row (`gemm_q8`): b holds k * n bytes, and the scales are one per
/// row of b as stored, n when transposed (b is n x k), else k. In release too.
fn check_gemm_q8(a: &[f32], bq: &[u8], b_scale: &[f32], c: &[f32], (m, k, n): (usize, usize, usize), trans_b: bool) {
    assert!(
        m.checked_mul(k) == Some(a.len())
            && k.checked_mul(n) == Some(bq.len())
            && m.checked_mul(n) == Some(c.len())
            && b_scale.len() == if trans_b { n } else { k },
        "gemm: matrix lengths do not match"
    );
}

/// Rows of int8 `q` (each `cols` bytes) restored into `out` as f32, row r times `scales[r]`: `(q as i8 as f32) * scale`, the one product the
/// export's dequantization defines (and what the f32 matrices of earlier builds held), computed once per element and never folded into another
/// product (docs/spec/semantic-rerank.md §2.4).
fn restore_rows(q: &[u8], scales: &[f32], cols: usize, out: &mut [f32]) {
    assert!(q.len() == out.len() && cols.checked_mul(scales.len()) == Some(q.len()), "restore: lengths do not match");
    if cols == 0 {
        return;
    }
    for ((row, o), s) in q.chunks_exact(cols).zip(out.chunks_exact_mut(cols)).zip(scales) {
        o.iter_mut().zip(row).for_each(|(o, &x)| *o = x as i8 as f32 * s);
    }
}

/// docs/spec/semantic-rerank.md §2.4: the portable product with an int8 b (the matrices are never expanded whole). A few rows of b at a time are
/// restored (`restore_rows`) into one heap tile of at most 128 KiB (`MAX_D` wide: never on the stack of a 512 KiB worker thread), then every row of a
/// goes through `gemm_kernel` against that tile: not transposed, 4 rows of b (as the vector kernels block them), so each c[i][j] still takes its
/// products with p in order; transposed, `32_768 / k` rows (1 to 32), each c[i][j] one independent dot product. Every element therefore sees the
/// same operations in the same order as the product with the expanded f32 b: the same bits on every kernel. False when this CPU lacks `kernel`.
fn gemm_q8(kernel: Kernel, a: &[f32], bq: &[u8], b_scale: &[f32], c: &mut [f32], (m, k, n): (usize, usize, usize), trans_b: bool) -> bool {
    check_gemm_q8(a, bq, b_scale, c, (m, k, n), trans_b);
    if m == 0 {
        return true;
    }
    // (rows of b per tile, length of a row of b, rows of b)
    let (per, len, rows) = if trans_b { ((32_768 / k.max(1)).clamp(1, 32), k, n) } else { (4, n, k) };
    let mut tile = vec![0f32; per * len];
    for r0 in (0..rows).step_by(per) {
        let r1 = (r0 + per).min(rows);
        let t = &mut tile[..(r1 - r0) * len];
        restore_rows(&bq[r0 * len..r1 * len], &b_scale[r0..r1], len, t);
        for i in 0..m {
            let ran = if trans_b {
                gemm_kernel(kernel, &a[i * k..][..k], t, &mut c[i * n + r0..i * n + r1], (1, k, r1 - r0), true)
            } else {
                gemm_kernel(kernel, &a[i * k + r0..i * k + r1], t, &mut c[i * n..][..n], (1, r1 - r0, n), false)
            };
            if !ran {
                return false;
            }
        }
    }
    true
}

/// Runs one kernel; false (and nothing computed) when this CPU does not have it. The one place the vector kernels are called from: it
/// checks the lengths and the CPU feature itself, so no caller can run a vector kernel on a CPU without it.
fn gemm_kernel(kernel: Kernel, a: &[f32], b: &[f32], c: &mut [f32], (m, k, n): (usize, usize, usize), trans_b: bool) -> bool {
    check_gemm(a, b, c, (m, k, n));
    match kernel {
        Kernel::Scalar => scalar::gemm(a, b, c, (m, k, n), trans_b),
        #[cfg(target_arch = "x86_64")]
        Kernel::Avx2 => {
            if !std::arch::is_x86_feature_detected!("avx2") {
                return false;
            }
            // SAFETY: the CPU has AVX2 (checked on the line above).
            unsafe { x86::gemm_avx2(a, b, c, (m, k, n), trans_b) }
        }
        #[cfg(target_arch = "x86_64")]
        Kernel::Avx512 => {
            if !std::arch::is_x86_feature_detected!("avx512f") {
                return false;
            }
            // SAFETY: the CPU has AVX-512F (checked on the line above).
            unsafe { x86::gemm_avx512(a, b, c, (m, k, n), trans_b) }
        }
    }
    true
}

/// The reference kernel: plain Rust, the definition the vector kernels are compared with bit for bit, and what every target other than
/// x86-64 (and an x86-64 CPU without AVX2) runs. The compiler vectorizes it with the target's baseline (SSE2, NEON).
mod scalar {
    /// dot(x, y): 16 partial sums over the whole blocks of 16 (element q goes to sum q mod 16), merged 16 -> 8 -> 4 -> 2 -> 1 by adding
    /// sum l + half to sum l, then the tail (fewer than 16 elements) summed in order and added last.
    #[inline]
    pub(super) fn dot(x: &[f32], y: &[f32]) -> f32 {
        let mut s = [0f32; 16];
        let (xc, yc) = (x.chunks_exact(16), y.chunks_exact(16));
        let (xr, yr) = (xc.remainder(), yc.remainder());
        for (cx, cy) in xc.zip(yc) {
            for l in 0..16 {
                s[l] += cx[l] * cy[l];
            }
        }
        for half in [8, 4, 2, 1] {
            for l in 0..half {
                s[l] += s[l + half];
            }
        }
        let mut t = 0f32;
        for (p, q) in xr.iter().zip(yr) {
            t += p * q;
        }
        s[0] + t
    }

    pub(super) fn gemm(a: &[f32], b: &[f32], c: &mut [f32], (m, k, n): (usize, usize, usize), trans_b: bool) {
        if trans_b {
            for i in 0..m {
                let ai = &a[i * k..][..k];
                for (j, cj) in c[i * n..][..n].iter_mut().enumerate() {
                    *cj += dot(ai, &b[j * k..][..k]);
                }
            }
        } else {
            for i in 0..m {
                for p in 0..k {
                    let av = a[i * k + p];
                    for (cj, bj) in c[i * n..][..n].iter_mut().zip(&b[p * n..][..n]) {
                        *cj += av * bj;
                    }
                }
            }
        }
    }
}

/// The AVX2 and AVX-512F kernels. Each is a safe `#[target_feature]` function: an intrinsic of a feature that is not enabled does not
/// compile. `unsafe` is only the unaligned loads and stores, each on a slice of exactly the vector's length (`chunks_exact`, or a
/// range of a checked slice), so no load or store can leave its slice; tails are scalar. No aligned access, no masked load, no pointer
/// arithmetic on the matrices, no `get_unchecked`, no FMA (it rounds once where the definition rounds twice).
#[cfg(target_arch = "x86_64")]
mod x86 {
    use std::arch::x86_64::*;

    /// Rows of b per tile of the transposed product (32 rows of the model's width are 48 KiB; 128 rows measured no faster).
    const TILE: usize = 32;
    /// Rows of b per block of the product that is not transposed: a multiple of 4 (the row kernels take four rows at a time, so only the
    /// last block has single rows). 4 rows of the widest layer (1,536 columns) are 24 KiB and stay in the first-level cache while every
    /// row of c takes them. Measured on an i5-12600K (AVX2), 200 rows in one process: 4 rows 11.2-11.3 s, 8 rows 11.6-11.8 s, 16 rows
    /// 12.4-12.6 s, 32 rows 12.7-12.9 s; the old loop 40.1-41.7 s.
    const BLOCK: usize = 4;

    /// The 8 sums `v` merged as `scalar::dot` merges its sums 0..8: 8 -> 4 -> 2 -> 1.
    #[inline]
    #[target_feature(enable = "avx2")]
    fn merge8(v: __m256) -> f32 {
        let q = _mm_add_ps(_mm256_castps256_ps128(v), _mm256_extractf128_ps::<1>(v)); // s[l] += s[l + 4]
        let d = _mm_add_ps(q, _mm_movehl_ps(q, q)); // s[l] += s[l + 2]
        _mm_cvtss_f32(_mm_add_ss(d, _mm_shuffle_ps::<1>(d, d))) // s[0] += s[1]
    }

    #[inline]
    fn tail(xr: &[f32], yr: &[f32]) -> f32 {
        let mut t = 0f32;
        for (p, q) in xr.iter().zip(yr) {
            t += p * q;
        }
        t
    }

    #[inline]
    #[target_feature(enable = "avx2")]
    fn dot_avx2(x: &[f32], y: &[f32]) -> f32 {
        let (mut lo, mut hi) = (_mm256_setzero_ps(), _mm256_setzero_ps()); // sums 0..8 and 8..16
        let (xc, yc) = (x.chunks_exact(16), y.chunks_exact(16));
        let (xr, yr) = (xc.remainder(), yc.remainder());
        for (cx, cy) in xc.zip(yc) {
            let (x0, x1) = cx.split_at(8);
            let (y0, y1) = cy.split_at(8);
            // SAFETY: each of x0, x1, y0, y1 is a slice of exactly 8 f32; loadu reads 8 f32 and needs no alignment.
            let (a0, a1, b0, b1) = unsafe { (_mm256_loadu_ps(x0.as_ptr()), _mm256_loadu_ps(x1.as_ptr()), _mm256_loadu_ps(y0.as_ptr()), _mm256_loadu_ps(y1.as_ptr())) };
            lo = _mm256_add_ps(lo, _mm256_mul_ps(a0, b0));
            hi = _mm256_add_ps(hi, _mm256_mul_ps(a1, b1));
        }
        merge8(_mm256_add_ps(lo, hi)) + tail(xr, yr) // s[l] += s[l + 8], then the rest of the merge, then the tail
    }

    #[inline]
    #[target_feature(enable = "avx512f")]
    fn dot_avx512(x: &[f32], y: &[f32]) -> f32 {
        let mut s = _mm512_setzero_ps(); // the 16 sums
        let (xc, yc) = (x.chunks_exact(16), y.chunks_exact(16));
        let (xr, yr) = (xc.remainder(), yc.remainder());
        for (cx, cy) in xc.zip(yc) {
            // SAFETY: cx and cy are slices of exactly 16 f32; loadu reads 16 f32 and needs no alignment.
            let (a, b) = unsafe { (_mm512_loadu_ps(cx.as_ptr()), _mm512_loadu_ps(cy.as_ptr())) };
            s = _mm512_add_ps(s, _mm512_mul_ps(a, b));
        }
        // the upper 8 sums with AVX-512F only (extractf32x8 would need AVX-512DQ)
        let hi = _mm256_castpd_ps(_mm512_extractf64x4_pd::<1>(_mm512_castps_pd(s)));
        merge8(_mm256_add_ps(_mm512_castps512_ps256(s), hi)) + tail(xr, yr)
    }

    /// One row of the product that is not transposed: ci[j] takes ai[p] * b[p][j] for p in order, as the scalar kernel does; the
    /// rows of b are read front to back, four at a time.
    #[inline]
    #[target_feature(enable = "avx2")]
    fn row_avx2(ai: &[f32], b: &[f32], ci: &mut [f32], n: usize) {
        const W: usize = 8;
        let whole = n - n % W;
        let (cv, ct) = ci.split_at_mut(whole);
        let mut p = 0;
        // four rows of b per pass over the row of c: each element still takes its products one at a time, p in order
        while p + 4 <= ai.len() {
            let (a0, a1, a2, a3) = (_mm256_set1_ps(ai[p]), _mm256_set1_ps(ai[p + 1]), _mm256_set1_ps(ai[p + 2]), _mm256_set1_ps(ai[p + 3]));
            let (b0, b1, b2, b3) = (&b[p * n..][..n], &b[(p + 1) * n..][..n], &b[(p + 2) * n..][..n], &b[(p + 3) * n..][..n]);
            let rows = b0[..whole].chunks_exact(W).zip(b1[..whole].chunks_exact(W)).zip(b2[..whole].chunks_exact(W).zip(b3[..whole].chunks_exact(W)));
            for (cc, ((x0, x1), (x2, x3))) in cv.chunks_exact_mut(W).zip(rows) {
                // SAFETY: cc, x0, x1, x2 and x3 are slices of exactly 8 f32 (chunks_exact); the loads and the store need no alignment.
                unsafe {
                    let mut acc = _mm256_loadu_ps(cc.as_ptr());
                    acc = _mm256_add_ps(acc, _mm256_mul_ps(a0, _mm256_loadu_ps(x0.as_ptr())));
                    acc = _mm256_add_ps(acc, _mm256_mul_ps(a1, _mm256_loadu_ps(x1.as_ptr())));
                    acc = _mm256_add_ps(acc, _mm256_mul_ps(a2, _mm256_loadu_ps(x2.as_ptr())));
                    acc = _mm256_add_ps(acc, _mm256_mul_ps(a3, _mm256_loadu_ps(x3.as_ptr())));
                    _mm256_storeu_ps(cc.as_mut_ptr(), acc);
                }
            }
            for (q, cj) in ct.iter_mut().enumerate() {
                let j = whole + q;
                *cj += ai[p] * b0[j];
                *cj += ai[p + 1] * b1[j];
                *cj += ai[p + 2] * b2[j];
                *cj += ai[p + 3] * b3[j];
            }
            p += 4;
        }
        while p < ai.len() {
            let (av, va, brow) = (ai[p], _mm256_set1_ps(ai[p]), &b[p * n..][..n]);
            for (cc, x) in cv.chunks_exact_mut(W).zip(brow[..whole].chunks_exact(W)) {
                // SAFETY: cc and x are slices of exactly 8 f32 (chunks_exact).
                unsafe { _mm256_storeu_ps(cc.as_mut_ptr(), _mm256_add_ps(_mm256_loadu_ps(cc.as_ptr()), _mm256_mul_ps(va, _mm256_loadu_ps(x.as_ptr())))) };
            }
            for (cj, bj) in ct.iter_mut().zip(&brow[whole..]) {
                *cj += av * bj;
            }
            p += 1;
        }
    }

    /// As `row_avx2` with 16-element vectors.
    #[inline]
    #[target_feature(enable = "avx512f")]
    fn row_avx512(ai: &[f32], b: &[f32], ci: &mut [f32], n: usize) {
        const W: usize = 16;
        let whole = n - n % W;
        let (cv, ct) = ci.split_at_mut(whole);
        let mut p = 0;
        // four rows of b per pass over the row of c: each element still takes its products one at a time, p in order
        while p + 4 <= ai.len() {
            let (a0, a1, a2, a3) = (_mm512_set1_ps(ai[p]), _mm512_set1_ps(ai[p + 1]), _mm512_set1_ps(ai[p + 2]), _mm512_set1_ps(ai[p + 3]));
            let (b0, b1, b2, b3) = (&b[p * n..][..n], &b[(p + 1) * n..][..n], &b[(p + 2) * n..][..n], &b[(p + 3) * n..][..n]);
            let rows = b0[..whole].chunks_exact(W).zip(b1[..whole].chunks_exact(W)).zip(b2[..whole].chunks_exact(W).zip(b3[..whole].chunks_exact(W)));
            for (cc, ((x0, x1), (x2, x3))) in cv.chunks_exact_mut(W).zip(rows) {
                // SAFETY: cc, x0, x1, x2 and x3 are slices of exactly 16 f32 (chunks_exact); the loads and the store need no alignment.
                unsafe {
                    let mut acc = _mm512_loadu_ps(cc.as_ptr());
                    acc = _mm512_add_ps(acc, _mm512_mul_ps(a0, _mm512_loadu_ps(x0.as_ptr())));
                    acc = _mm512_add_ps(acc, _mm512_mul_ps(a1, _mm512_loadu_ps(x1.as_ptr())));
                    acc = _mm512_add_ps(acc, _mm512_mul_ps(a2, _mm512_loadu_ps(x2.as_ptr())));
                    acc = _mm512_add_ps(acc, _mm512_mul_ps(a3, _mm512_loadu_ps(x3.as_ptr())));
                    _mm512_storeu_ps(cc.as_mut_ptr(), acc);
                }
            }
            for (q, cj) in ct.iter_mut().enumerate() {
                let j = whole + q;
                *cj += ai[p] * b0[j];
                *cj += ai[p + 1] * b1[j];
                *cj += ai[p + 2] * b2[j];
                *cj += ai[p + 3] * b3[j];
            }
            p += 4;
        }
        while p < ai.len() {
            let (av, va, brow) = (ai[p], _mm512_set1_ps(ai[p]), &b[p * n..][..n]);
            for (cc, x) in cv.chunks_exact_mut(W).zip(brow[..whole].chunks_exact(W)) {
                // SAFETY: cc and x are slices of exactly 16 f32 (chunks_exact).
                unsafe { _mm512_storeu_ps(cc.as_mut_ptr(), _mm512_add_ps(_mm512_loadu_ps(cc.as_ptr()), _mm512_mul_ps(va, _mm512_loadu_ps(x.as_ptr())))) };
            }
            for (cj, bj) in ct.iter_mut().zip(&brow[whole..]) {
                *cj += av * bj;
            }
            p += 1;
        }
    }

    /// The caller has checked the lengths (`check_gemm`) and that the CPU has AVX2.
    #[target_feature(enable = "avx2")]
    pub(super) fn gemm_avx2(a: &[f32], b: &[f32], c: &mut [f32], (m, k, n): (usize, usize, usize), trans_b: bool) {
        if trans_b {
            // tiles of rows of b stay in the cache while every row of a passes over them; each c[i][j] is one independent dot product
            for j0 in (0..n).step_by(TILE) {
                let j1 = (j0 + TILE).min(n);
                for i in 0..m {
                    let ai = &a[i * k..][..k];
                    for (j, cj) in (j0..j1).zip(&mut c[i * n + j0..i * n + j1]) {
                        *cj += dot_avx2(ai, &b[j * k..][..k]);
                    }
                }
            }
        } else {
            // blocks of BLOCK rows of b stay in the cache while every row of c takes them; an element still gets its products with p in order
            for p0 in (0..k).step_by(BLOCK) {
                let p1 = (p0 + BLOCK).min(k);
                for i in 0..m {
                    row_avx2(&a[i * k + p0..i * k + p1], &b[p0 * n..p1 * n], &mut c[i * n..][..n], n);
                }
            }
        }
    }

    /// The caller has checked the lengths (`check_gemm`) and that the CPU has AVX-512F.
    #[target_feature(enable = "avx512f")]
    pub(super) fn gemm_avx512(a: &[f32], b: &[f32], c: &mut [f32], (m, k, n): (usize, usize, usize), trans_b: bool) {
        if trans_b {
            // tiles of rows of b stay in the cache while every row of a passes over them; each c[i][j] is one independent dot product
            for j0 in (0..n).step_by(TILE) {
                let j1 = (j0 + TILE).min(n);
                for i in 0..m {
                    let ai = &a[i * k..][..k];
                    for (j, cj) in (j0..j1).zip(&mut c[i * n + j0..i * n + j1]) {
                        *cj += dot_avx512(ai, &b[j * k..][..k]);
                    }
                }
            }
        } else {
            // blocks of BLOCK rows of b stay in the cache while every row of c takes them; an element still gets its products with p in order
            for p0 in (0..k).step_by(BLOCK) {
                let p1 = (p0 + BLOCK).min(k);
                for i in 0..m {
                    row_avx512(&a[i * k + p0..i * k + p1], &b[p0 * n..p1 * n], &mut c[i * n..][..n], n);
                }
            }
        }
    }
}

#[cfg(all(target_vendor = "apple", not(target_pointer_width = "64")))]
compile_error!("PurgeableSlab requires 64-bit Apple targets");

/// docs/spec/semantic-rerank.md §2.4, B1: one int8 matrix restored to f32 in Mach purgeable memory, for the Accelerate path. Anonymous memory of
/// this process only (no file, nothing written to disk, no entitlement): allocated on first use, restored, pinned non-volatile for one
/// `cblas_sgemm`, then volatile again, so between uses it is not in the process's phys_footprint and the system may empty it under pressure;
/// a pin that finds it emptied restores it again. Only `<mach/vm_map.h>` calls (`vm_allocate`, `vm_deallocate`, `vm_purgable_control`), which
/// macOS and iOS both have (`mach_vm.h` is unsupported on iOS).
///
/// The state, all under one `Mutex` (SEC-03): `addr` (0: not allocated), `pins` (how many `PinGuard`s are alive: non-volatile and restored
/// while > 0), `populated` (the region holds the restored matrix as of the last restore). A restore runs with the lock held, so a second
/// thread pinning the same matrix waits on the lock until it is done, and the region is written only while nobody holds a pin. A panic out
/// of a restore leaves `populated` false and the region volatile (`Unwind`), so the poisoned lock is recovered safely and the next pin
/// restores again. The state read back from the kernel is masked with `VM_PURGABLE_STATE_MASK` (the higher bits carry order, behavior and
/// group). A Mach call that does not return `KERN_SUCCESS`, or a pin count that would overflow, never reads the region: the pin is refused
/// (`None`), `fallbacks` counts it, and the caller computes that product on the portable path instead.
#[cfg(target_vendor = "apple")]
mod purgeable {
    use std::sync::{Mutex, MutexGuard};

    // <mach/mach_init.h>, <mach/vm_page_size.h>, <mach/vm_map.h> (checked against the macOS and iOS SDKs): mach_port_t and vm_map_t are u32,
    // vm_address_t and vm_size_t are pointer-sized, kern_return_t, vm_purgable_t and the state are int.
    extern "C" {
        static mach_task_self_: u32;
        static vm_page_size: usize;
        fn vm_allocate(target_task: u32, address: *mut usize, size: usize, flags: i32) -> i32;
        fn vm_deallocate(target_task: u32, address: usize, size: usize) -> i32;
        fn vm_purgable_control(target_task: u32, address: usize, control: i32, state: *mut i32) -> i32;
    }
    const KERN_SUCCESS: i32 = 0;
    /// What the test-only fault injection returns in place of a Mach call.
    const KERN_FAILURE: i32 = 5;
    const VM_FLAGS_ANYWHERE: i32 = 0x0001;
    const VM_FLAGS_PURGABLE: i32 = 0x0002;
    const VM_PURGABLE_SET_STATE: i32 = 0;
    const VM_PURGABLE_GET_STATE: i32 = 1;
    const VM_PURGABLE_STATE_MASK: i32 = 3;
    const VM_PURGABLE_NONVOLATILE: i32 = 0;
    const VM_PURGABLE_VOLATILE: i32 = 1;
    const VM_PURGABLE_EMPTY: i32 = 2;

    fn task() -> u32 {
        // SAFETY: a plain read of a port name libSystem sets before main and never changes.
        unsafe { mach_task_self_ }
    }

    /// `elem_len` f32 rounded up to whole pages, or `None` (an overflow, an empty matrix, or a page size that is not a power of two).
    fn alloc_bytes(elem_len: usize) -> Option<usize> {
        // SAFETY: a plain read of a value libSystem sets before main and never changes.
        let page = unsafe { vm_page_size };
        let bytes = elem_len.checked_mul(4)?;
        if bytes == 0 || !page.is_power_of_two() {
            return None;
        }
        let rounded = bytes.checked_add(page - 1)? & !(page - 1);
        (rounded <= isize::MAX as usize).then_some(rounded)
    }

    /// `vm_purgable_control` on the region at `addr`: `(kern_return_t, state)` (for SET_STATE the state returned is the old one).
    fn control(addr: usize, op: i32, state: i32) -> (i32, i32) {
        let mut st = state;
        // SAFETY: `addr` is a region this slab allocated and has not deallocated; `st` is a valid in/out pointer for the call.
        let kr = unsafe { vm_purgable_control(task(), addr, op, &mut st) };
        (kr, st)
    }

    /// Test-only fault injection (never compiled into the library): make the next Mach calls fail, or panic out of a restore.
    #[cfg(test)]
    #[derive(Default)]
    pub(super) struct Inject {
        pub(super) fail_alloc: bool,
        pub(super) fail_control: bool,
        pub(super) panic_restore: bool,
    }

    #[derive(Default)]
    struct SlabInner {
        addr: usize,
        alloc_bytes: usize,
        pins: usize,
        populated: bool,
        alloc_ok: usize,
        pin_ok: usize,
        volatile_ok: usize,
        empty_rebuilds: usize,
        fallbacks: usize,
        #[cfg(test)]
        inject: Inject,
    }

    pub(super) struct PurgeableSlab {
        elem_len: usize,
        inner: Mutex<SlabInner>,
    }

    /// The counters of one slab (`TinyLm::purgeable_stats` sums them).
    pub(super) struct SlabCounts {
        pub(super) alloc_ok: usize,
        pub(super) pin_ok: usize,
        pub(super) volatile_ok: usize,
        pub(super) empty_rebuilds: usize,
        pub(super) fallbacks: usize,
        pub(super) volatile_now: bool,
    }

    /// The restored matrix, non-volatile for as long as this lives; the slice cannot outlive the guard.
    pub(super) struct PinGuard<'a> {
        slab: &'a PurgeableSlab,
        data: &'a [f32],
    }

    impl std::ops::Deref for PinGuard<'_> {
        type Target = [f32];
        fn deref(&self) -> &[f32] {
            self.data
        }
    }

    impl Drop for PinGuard<'_> {
        fn drop(&mut self) {
            let mut g = self.slab.lock();
            g.pins -= 1;
            // The last pin: volatile again (out of phys_footprint). A failure leaves it non-volatile, visible as volatile_ok falling behind and
            // in `currently_volatile`.
            if g.pins == 0 && control(g.addr, VM_PURGABLE_SET_STATE, VM_PURGABLE_VOLATILE).0 == KERN_SUCCESS {
                g.volatile_ok += 1;
            }
        }
    }

    impl PurgeableSlab {
        /// Nothing is allocated until the first `pin`.
        pub(super) fn new(elem_len: usize) -> PurgeableSlab {
            PurgeableSlab { elem_len, inner: Mutex::new(SlabInner::default()) }
        }

        /// The lock, recovered if a panic poisoned it (the state is consistent at every point a panic can leave it: see the module comment).
        fn lock(&self) -> MutexGuard<'_, SlabInner> {
            self.inner.lock().unwrap_or_else(|e| e.into_inner())
        }

        /// The restored matrix, pinned non-volatile until the guard drops; `restore` fills the region when it does not hold the matrix (first
        /// use, emptied by the system, or a restore that panicked). `None`, with nothing read from the region, when a Mach call fails.
        pub(super) fn pin(&self, restore: impl FnOnce(&mut [f32])) -> Option<PinGuard<'_>> {
            let mut g = self.lock();
            #[cfg(test)]
            let (fail_alloc, fail_control) = (g.inject.fail_alloc, g.inject.fail_control);
            #[cfg(not(test))]
            let (fail_alloc, fail_control) = (false, false);
            let restore_needed = if g.addr == 0 {
                let Some(bytes) = alloc_bytes(self.elem_len) else {
                    g.fallbacks += 1;
                    return None;
                };
                let mut addr = 0usize;
                let kr = if fail_alloc {
                    KERN_FAILURE
                } else {
                    // SAFETY: `addr` is a valid out-pointer; the kernel picks the address (ANYWHERE). Fresh memory is non-volatile and zero.
                    unsafe { vm_allocate(task(), &mut addr, bytes, VM_FLAGS_ANYWHERE | VM_FLAGS_PURGABLE) }
                };
                if kr != KERN_SUCCESS || addr == 0 {
                    g.fallbacks += 1;
                    return None;
                }
                (g.addr, g.alloc_bytes, g.populated) = (addr, bytes, false);
                g.alloc_ok += 1;
                true
            } else if g.pins > 0 {
                // Pinned by another caller: non-volatile and restored already.
                let Some(pins) = g.pins.checked_add(1) else {
                    g.fallbacks += 1;
                    return None;
                };
                g.pins = pins;
                g.pin_ok += 1;
                return Some(self.guard(&g));
            } else {
                let (kr, old) =
                    if fail_control { (KERN_FAILURE, 0) } else { control(g.addr, VM_PURGABLE_SET_STATE, VM_PURGABLE_NONVOLATILE) };
                if kr != KERN_SUCCESS {
                    g.fallbacks += 1;
                    return None;
                }
                let emptied = old & VM_PURGABLE_STATE_MASK == VM_PURGABLE_EMPTY;
                if emptied && g.populated {
                    g.empty_rebuilds += 1;
                }
                emptied || !g.populated
            };
            if restore_needed {
                g.populated = false;
                /// Unwinding out of `restore`: the region holds a partial restore; `populated` stays false and the region goes back to volatile.
                struct Unwind(usize);
                impl Drop for Unwind {
                    fn drop(&mut self) {
                        let _ = control(self.0, VM_PURGABLE_SET_STATE, VM_PURGABLE_VOLATILE);
                    }
                }
                let unwind = Unwind(g.addr);
                // SAFETY: the region is `alloc_bytes >= 4 * elem_len` bytes, page-aligned (so f32-aligned), allocated by this slab, now
                // non-volatile, and written by nobody else: no pin is alive (pins == 0) and the lock is held for the whole restore.
                let dst = unsafe { std::slice::from_raw_parts_mut(g.addr as *mut f32, self.elem_len) };
                #[cfg(test)]
                if g.inject.panic_restore {
                    dst[..self.elem_len / 2].fill(f32::NAN);
                    panic!("injected restore panic");
                }
                restore(dst);
                std::mem::forget(unwind);
                g.populated = true;
            }
            g.pins = 1;
            g.pin_ok += 1;
            Some(self.guard(&g))
        }

        fn guard(&self, g: &SlabInner) -> PinGuard<'_> {
            // SAFETY: called with pins >= 1 just counted under the lock: the region is non-volatile (the kernel cannot empty it) and restored,
            // and nobody writes it until the last pin drops; the slice is bound to the guard's lifetime, which ends before that.
            let data = unsafe { std::slice::from_raw_parts(g.addr as *const f32, self.elem_len) };
            PinGuard { slab: self, data }
        }

        pub(super) fn counts(&self) -> SlabCounts {
            let g = self.lock();
            let volatile_now = g.addr != 0 && g.pins == 0 && {
                let (kr, st) = control(g.addr, VM_PURGABLE_GET_STATE, 0);
                kr == KERN_SUCCESS && matches!(st & VM_PURGABLE_STATE_MASK, VM_PURGABLE_VOLATILE | VM_PURGABLE_EMPTY)
            };
            SlabCounts {
                alloc_ok: g.alloc_ok,
                pin_ok: g.pin_ok,
                volatile_ok: g.volatile_ok,
                empty_rebuilds: g.empty_rebuilds,
                fallbacks: g.fallbacks,
                volatile_now,
            }
        }

        #[cfg(test)]
        pub(super) fn inject(&self, f: impl FnOnce(&mut Inject)) {
            f(&mut self.lock().inject)
        }

        /// Test only: empty the region as the system would under memory pressure (only when unpinned: the system only empties volatile
        /// memory); true when the kernel then reports it empty.
        #[cfg(test)]
        pub(super) fn force_empty(&self) -> bool {
            let g = self.lock();
            if g.addr == 0 || g.pins > 0 {
                return false;
            }
            let (kr, _) = control(g.addr, VM_PURGABLE_SET_STATE, VM_PURGABLE_EMPTY);
            let (kr2, st) = control(g.addr, VM_PURGABLE_GET_STATE, 0);
            kr == KERN_SUCCESS && kr2 == KERN_SUCCESS && st & VM_PURGABLE_STATE_MASK == VM_PURGABLE_EMPTY
        }
    }

    impl Drop for PurgeableSlab {
        fn drop(&mut self) {
            let g = self.inner.get_mut().unwrap_or_else(|e| e.into_inner());
            if g.addr != 0 && g.pins == 0 {
                // SAFETY: exactly the region `vm_allocate` returned, deallocated once; no guard can be alive (`&mut self`).
                let _ = unsafe { vm_deallocate(task(), g.addr, g.alloc_bytes) };
            }
        }
    }
}

/// docs/spec/semantic-rerank.md §2.4: the B1 counters of a model (`TinyLm::purgeable_stats`), integers only (no path, no content; not in the C ABI).
/// `slab_count`: int8 matrices (one slab each; 33 for the shipped model: the embedding and 4 per layer). `alloc_ok`: slabs allocated. `pin_ok`:
/// pins granted. `volatile_ok`: times the last pin made a slab volatile again. `empty_rebuilds`: restores because the system had emptied a slab.
/// `fallbacks`: pins refused because a Mach call failed (that product ran on the portable path). `currently_volatile`: slabs the kernel reports
/// volatile or emptied right now (`VM_PURGABLE_GET_STATE`).
#[cfg(target_vendor = "apple")]
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct PurgeableStats {
    pub slab_count: usize,
    pub alloc_ok: usize,
    pub pin_ok: usize,
    pub volatile_ok: usize,
    pub empty_rebuilds: usize,
    pub fallbacks: usize,
    pub currently_volatile: usize,
}

#[cfg(target_vendor = "apple")]
impl PurgeableStats {
    /// SEC-12: whether B1 really ran rather than falling back silently: at least one slab, no fallback, every slab allocated once, pinned and made
    /// volatile at least once each, and every slab volatile now. The probe and the tests judge with this one function; `Err` names the first
    /// condition that fails.
    pub fn check(&self) -> Result<(), &'static str> {
        let n = self.slab_count;
        if n == 0 {
            return Err("slab_count");
        }
        if self.fallbacks != 0 {
            return Err("fallbacks");
        }
        if self.alloc_ok != n {
            return Err("alloc_ok");
        }
        if self.pin_ok < n {
            return Err("pin_ok");
        }
        if self.volatile_ok < n {
            return Err("volatile_ok");
        }
        if self.currently_volatile != n {
            return Err("currently_volatile");
        }
        Ok(())
    }
}

/// y = tanh(x), elementwise (out of place: vForce is not documented to allow y == x).
fn tanh_into(be: Backend, x: &[f32], y: &mut [f32]) {
    assert!(x.len() == y.len() && x.len() < i32::MAX as usize, "tanh: lengths do not match");
    match be {
        #[cfg(target_vendor = "apple")]
        Backend::Accelerate => unsafe { vvtanhf(y.as_mut_ptr(), x.as_ptr(), &(x.len() as i32)) },
        Backend::Plain => y.iter_mut().zip(x).for_each(|(o, v)| *o = v.tanh()),
    }
}

/// y = exp(x), elementwise, out of place.
fn exp_into(be: Backend, x: &[f32], y: &mut [f32]) {
    assert!(x.len() == y.len() && x.len() < i32::MAX as usize, "exp: lengths do not match");
    match be {
        #[cfg(target_vendor = "apple")]
        Backend::Accelerate => unsafe { vvexpf(y.as_mut_ptr(), x.as_ptr(), &(x.len() as i32)) },
        Backend::Plain => y.iter_mut().zip(x).for_each(|(o, v)| *o = v.exp()),
    }
}

/// Where the `.sjtl` bytes live (docs/spec/semantic-rerank.md §2.2): the read-only mapping `load` and `load_paired` make, or a copy (`parse`). The
/// int8 matrices are read from here for as long as the model lives.
enum SjtlBytes {
    Map(Mapped),
    Mem(Vec<u8>),
}

impl std::ops::Deref for SjtlBytes {
    type Target = [u8];
    fn deref(&self) -> &[u8] {
        match self {
            SjtlBytes::Map(m) => m,
            SjtlBytes::Mem(v) => v,
        }
    }
}

/// A 2-D weight matrix as the file stores it, `rows` x `cols` row-major.
struct Matrix {
    rows: usize,
    cols: usize,
    w: Weights,
}

enum Weights {
    /// wtype 0 (test and golden files, never shipped): the f32 values, copied out of the file and checked finite.
    F32(Vec<f32>),
    /// wtype 1: the int8 rows stay in the file bytes (`at`, rows * cols bytes into `TinyLm::raw`); the per-row scales are copied to the heap
    /// (never a `&[f32]` cast of the bytes, which need not be aligned) and were checked so that every int8 value restores finite.
    Q8 {
        at: usize,
        scales: Vec<f32>,
        /// docs/spec/semantic-rerank.md §2.4: where the Accelerate path keeps this matrix restored, purgeable between uses (B1).
        #[cfg(target_vendor = "apple")]
        slab: purgeable::PurgeableSlab,
    },
}

struct Layer {
    ln1_w: Vec<f32>,
    ln1_b: Vec<f32>,
    qkv_w: Matrix,
    qkv_b: Vec<f32>,
    proj_w: Matrix,
    proj_b: Vec<f32>,
    ln2_w: Vec<f32>,
    ln2_b: Vec<f32>,
    fc1_w: Matrix,
    fc1_b: Vec<f32>,
    fc2_w: Matrix,
    fc2_b: Vec<f32>,
}

pub struct TinyLm {
    n_head: usize,
    d: usize,
    ctx: usize,
    /// (alpha, tau, delta) per `RerankLevel` (indexed by its code); version 1 files have delta 0, and version 1 and 2 files repeat
    /// the standard triple for all three levels.
    chat: [(f64, f64, f64); 3],
    formal: [(f64, f64, f64); 3],
    /// A version 3 or 4 file (its own conservative and aggressive triples).
    levels: bool,
    ng_sha: [u8; 32],
    /// SHA-256 (hex) of the file `load_paired` read, made right there from the exact bytes it parsed; empty for a model made by `parse` or
    /// `load`, which the colloquial term never pairs with.
    file_sha: String,
    /// Version 4: the SHA-256 of the side file it was tuned with; `None` for all zero and for versions 1 to 3.
    kn_sha: Option<[u8; 32]>,
    /// Version 5: the declared left-context length L (characters); `None` for versions 1 to 4.
    left_len: Option<usize>,
    /// Version 6 with the boundary flag: the policy-key threshold K (Han characters); `None` otherwise. [SEP] is then id V - 1.
    boundary_k: Option<usize>,
    ids: HashMap<char, u32>,
    /// The vocabulary size V (rows of `emb`).
    v: usize,
    emb: Matrix,
    /// ctx x d, restored to f32 at parse (128 KiB for the shipped model).
    pos: Vec<f32>,
    lnf_w: Vec<f32>,
    lnf_b: Vec<f32>,
    layers: Vec<Layer>,
    /// The file the int8 matrices point into; declared last so it outlives nothing that reads it.
    raw: SjtlBytes,
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

/// The tensor part of the file, read front to back. `ok` turns false on a non-finite value; the length was checked up front.
struct Cursor<'a> {
    b: &'a [u8],
    at: usize,
    int8: bool,
    ok: bool,
}

impl Cursor<'_> {
    fn f32s(&mut self, n: usize) -> Vec<f32> {
        let out: Vec<f32> = self.b[self.at..self.at + 4 * n].chunks_exact(4).map(|c| f32::from_le_bytes([c[0], c[1], c[2], c[3]])).collect();
        self.at += 4 * n;
        self.ok &= out.iter().all(|x| x.is_finite());
        out
    }
    /// The per-row scales of an int8 matrix: finite, and finite times 128 (SEC-08: `i8::MIN` is -128, so a scale near `f32::MAX / 127` that 127
    /// keeps finite would still restore -128 to -inf); every int8 value then restores to a finite f32.
    fn scales(&mut self, rows: usize) -> Vec<f32> {
        let s = self.f32s(rows);
        self.ok &= s.iter().all(|s| (128.0_f32 * s).is_finite());
        s
    }
    /// A 2-D matrix: f32 copied out; int8 left in the file (its offset) with its scales on the heap.
    fn mat(&mut self, rows: usize, cols: usize) -> Matrix {
        let w = if self.int8 {
            let at = self.at;
            self.at += rows * cols;
            let scales = self.scales(rows);
            #[cfg(target_vendor = "apple")]
            let slab = purgeable::PurgeableSlab::new(rows * cols);
            Weights::Q8 {
                at,
                scales,
                #[cfg(target_vendor = "apple")]
                slab,
            }
        } else {
            Weights::F32(self.f32s(rows * cols))
        };
        Matrix { rows, cols, w }
    }
    /// A 2-D matrix restored to f32 on the heap (`pos`): int8 as `(q as i8 as f32) * scale` per row (the f32 product int8check.dequant takes).
    fn dense(&mut self, rows: usize, cols: usize) -> Vec<f32> {
        if !self.int8 {
            return self.f32s(rows * cols);
        }
        let at = self.at;
        self.at += rows * cols;
        let scales = self.scales(rows);
        let mut out = vec![0f32; rows * cols];
        restore_rows(&self.b[at..at + rows * cols], &scales, cols, &mut out);
        out
    }
}

/// docs/spec/semantic-rerank.md §2.2: maps a regular file of at most `MAX_FILE` bytes read-only (`Mapped::open_bounded`: one open, the checks on that
/// handle, no `O_NOFOLLOW`). `Io`: unopenable, not a regular file, or the mapping failed; `Format`: empty or over the cap.
fn map_bounded(path: &Path) -> Result<Mapped, TinyLmError> {
    // SAFETY: the model files are never rewritten in place while loaded: every install path replaces them by rename or with the host stopped
    // (docs/spec/models.md §2.2); the mapping lives as long as the model.
    unsafe { Mapped::open_bounded(path, Some(MAX_FILE)) }.map_err(|e| match e {
        MapError::Io => TinyLmError::Io,
        MapError::Empty | MapError::TooLarge => TinyLmError::Format,
    })
}

/// The exact length of a file of these sizes (`int8`: wtype 1), in u64 with every step checked: `None` when it does not even fit in 64 bits.
/// u64, not usize, so a 32-bit target cannot wrap a huge header back to a small length that a short file matches (SEC-05); the caller compares
/// it with the file's length as u64.
#[allow(clippy::too_many_arguments)]
fn sjtl_len(header: usize, n_chars: usize, n_layer: usize, d: usize, ctx: usize, v: usize, int8: bool) -> Option<u64> {
    let u = |x: usize| x as u64;
    let (d, four) = (u(d), 4u64);
    let mat = |r: u64, c: u64| -> Option<u64> {
        let rc = r.checked_mul(c)?;
        if int8 { rc.checked_add(r.checked_mul(four)?) } else { rc.checked_mul(four) }
    };
    let vec = |n: u64| n.checked_mul(four);
    let layer = [
        vec(d.checked_mul(4)?)?,                          // ln1_w, ln1_b, ln2_w, ln2_b
        mat(d, d.checked_mul(3)?)?,                       // qkv_w
        vec(d.checked_mul(3)?)?,                          // qkv_b
        mat(d, d)?,                                       // proj_w
        vec(d)?,                                          // proj_b
        mat(d, d.checked_mul(4)?)?,                       // fc1_w
        vec(d.checked_mul(4)?)?,                          // fc1_b
        mat(d.checked_mul(4)?, d)?,                       // fc2_w
        vec(d)?,                                          // fc2_b
    ]
    .into_iter()
    .try_fold(0u64, u64::checked_add)?;
    [u(header), vec(u(n_chars))?, mat(u(v), d)?, mat(u(ctx), d)?, vec(d.checked_mul(2)?)?, u(n_layer).checked_mul(layer)?]
        .into_iter()
        .try_fold(0u64, u64::checked_add)
}

impl TinyLm {
    /// Maps only a regular file of at most `MAX_FILE` bytes (`map_bounded`) and parses that mapping in place; the model keeps it.
    pub fn load(path: &Path) -> Result<TinyLm, TinyLmError> {
        TinyLm::parse_backed(SjtlBytes::Map(map_bounded(path)?))
    }

    /// The one loader of the engine and the evaluation CLI (S7c-2a): `load`'s checks, then the pairing with `lm` — the n-gram
    /// model hash the file records must be `lm`'s, and the Kneser-Ney side file hash it records (docs/spec/models.md §3.5, §8.1: none for
    /// versions 1 to 3 and for an all-zero version 4 field) must be the one `lm` has loaded, all 32 bytes, or none when `lm` has none ("loaded" is about the file beside the model, not whether the formal profile scores with it).
    /// A pairing check only, not an integrity check of the `.sjtl`. `file_sha256` is the hash of exactly the bytes parsed: the one mapping `load`
    /// made (one open, so the file hashed is the file parsed), hashed in place 64 bytes at a time with no copy of the file. Measured: release build,
    /// the real tinylm.sjtl (14,638,352 bytes), 7 runs: the core's software SHA-256 takes 35 to 52 ms (median about 37 ms), `parse` 5 to 9 ms.
    pub fn load_paired(path: &Path, lm: &Lm) -> Result<TinyLm, TinyLmError> {
        let mut model = TinyLm::load(path)?;
        model.file_sha = crate::eval::sha256_hex(&model.raw);
        if model.ngram_sha256() != lm.sha256() {
            return Err(TinyLmError::OtherNgram);
        }
        match (model.kn_sha, lm.kn_sha256()) {
            (tuned, on) if tuned == on => Ok(model),
            (None, _) => Err(TinyLmError::KneserNey(KnPairing::TunedWithout)),
            (Some(_), None) => Err(TinyLmError::KneserNey(KnPairing::TunedWith)),
            (Some(_), Some(_)) => Err(TinyLmError::KneserNey(KnPairing::OtherSideFile)),
        }
    }

    /// Every check of the format; a bad file is an error, never a partial model. The model keeps a copy of `bytes` (its int8 matrices are read
    /// from there); `load` maps the file instead.
    pub fn parse(bytes: &[u8]) -> Result<TinyLm, TinyLmError> {
        TinyLm::parse_backed(SjtlBytes::Mem(bytes.to_vec()))
    }

    fn parse_backed(raw: SjtlBytes) -> Result<TinyLm, TinyLmError> {
        let bytes: &[u8] = &raw;
        let bad = TinyLmError::Format;
        if bytes.len() < 12 {
            return Err(bad);
        }
        let u32_at = |at: usize| u32::from_le_bytes(bytes[at..at + 4].try_into().unwrap());
        let f64_at = |at: usize| f64::from_le_bytes(bytes[at..at + 8].try_into().unwrap());
        // docs/spec/semantic-rerank.md §2.1, docs/spec/models.md §3.5, §8.1: magic and version field together
        // choose, in this one match, the header length (the one length the vocabulary start, the tensor start and `want` use),
        // whether the deltas are there, whether the levels are and whether the side file hash is; the file must hold the whole
        // header before any field past the version is read.
        let (header, deltas, levels, kn) = match (&bytes[..8], u32_at(8)) {
            (m, VERSION) if m == MAGIC => (HEADER, false, false, false),
            (m, VERSION_V2) if m == MAGIC_V2 => (HEADER_V2, true, false, false),
            (m, VERSION_V3) if m == MAGIC_V3 => (HEADER_V3, true, true, false),
            (m, VERSION_V4) if m == MAGIC_V4 => (HEADER_V4, true, true, true),
            (m, VERSION_V5) if m == MAGIC_V5 => (HEADER_V5, true, true, true),
            (m, VERSION_V6) if m == MAGIC_V6 => (HEADER_V6, true, true, true),
            _ => return Err(bad),
        };
        if bytes.len() < header {
            return Err(bad);
        }
        let kn_sha: Option<[u8; 32]> = if kn { Some(bytes[HEADER_V3..HEADER_V4].try_into().unwrap()) } else { None };
        let kn_sha = kn_sha.filter(|h| *h != [0; 32]);
        let wtype = u32_at(12);
        let [n_layer, n_head, d, ctx, v] = [16, 20, 24, 28, 32].map(|at| u32_at(at) as usize);
        let (chat_delta, formal_delta) = if deltas { (f64_at(100), f64_at(108)) } else { (0.0, 0.0) };
        let (chat_std, formal_std) = ((f64_at(36), f64_at(44), chat_delta), (f64_at(52), f64_at(60), formal_delta));
        let triple = |at: usize| (f64_at(at), f64_at(at + 8), f64_at(at + 16));
        // [conservative, standard, aggressive], `RerankLevel`'s codes.
        let (chat, formal) = if levels {
            ([triple(116), chat_std, triple(140)], [triple(164), formal_std, triple(188)])
        } else {
            ([chat_std; 3], [formal_std; 3])
        };
        if !chat.iter().chain(&formal).all(|&t| valid_params(t)) {
            return Err(bad);
        }
        if wtype > 1 || n_layer == 0 || n_head == 0 || d == 0 || d % n_head != 0 || ctx < 2 || v < 3 {
            return Err(bad);
        }
        // Bounds of the shipped model's order of magnitude (also keep the length arithmetic far from overflow); the exact file
        // length is checked next.
        if n_layer > MAX_LAYERS || d > MAX_D || ctx > MAX_CTX || v > MAX_V {
            return Err(bad);
        }
        // docs/spec/semantic-rerank.md §5.2: 1 <= L <= ctx - 2 (ctx >= 2 was checked above).
        let left_len = (header >= HEADER_V5).then(|| u32_at(HEADER_V4) as usize);
        if left_len.is_some_and(|l| l == 0 || l > ctx - 2) {
            return Err(bad);
        }
        // docs/spec/semantic-rerank.md §2.1: version 6's flags (bit 0 only) and K (1 <= K <= ctx - 2 with bit 0, else 0).
        let boundary_k = if header == HEADER_V6 {
            let (flags, k) = (u32_at(HEADER_V5), u32_at(HEADER_V5 + 4) as usize);
            match flags {
                0 if k == 0 => None,
                1 if (1..=ctx - 2).contains(&k) => Some(k),
                _ => return Err(bad),
            }
        } else {
            None
        };
        // PAD, UNK, BOS and (boundary format) [SEP] are ids too: fewer than that cannot even hold the specials.
        let specials = 3 + boundary_k.is_some() as usize;
        if v < specials {
            return Err(bad);
        }
        let n_chars = v - specials;
        let int8 = wtype == 1;
        // SEC-05: the exact length in checked u64; once it equals the file's, every offset below is at most the file length (a slice length,
        // so it fits in usize on any target).
        if sjtl_len(header, n_chars, n_layer, d, ctx, v, int8) != Some(bytes.len() as u64) {
            return Err(bad);
        }
        let mut ids = HashMap::new();
        for i in 0..n_chars {
            let c = char::from_u32(u32_at(header + 4 * i)).ok_or(bad)?;
            if ids.insert(c, i as u32 + 3).is_some() {
                return Err(bad);
            }
        }
        let mut r = Cursor { b: bytes, at: header + 4 * n_chars, int8, ok: true };
        // The tensor order of export_sjtl.py: emb, pos, lnf_w, lnf_b, then for each layer ln1_w, ln1_b, qkv_w, qkv_b, proj_w, proj_b,
        // ln2_w, ln2_b, fc1_w, fc1_b, fc2_w, fc2_b (tinylm.LAYER_KEYS). Struct fields evaluate in source order.
        let (emb, pos, lnf_w, lnf_b) = (r.mat(v, d), r.dense(ctx, d), r.f32s(d), r.f32s(d));
        let layers = (0..n_layer)
            .map(|_| Layer {
                ln1_w: r.f32s(d),
                ln1_b: r.f32s(d),
                qkv_w: r.mat(d, 3 * d),
                qkv_b: r.f32s(3 * d),
                proj_w: r.mat(d, d),
                proj_b: r.f32s(d),
                ln2_w: r.f32s(d),
                ln2_b: r.f32s(d),
                fc1_w: r.mat(d, 4 * d),
                fc1_b: r.f32s(4 * d),
                fc2_w: r.mat(4 * d, d),
                fc2_b: r.f32s(d),
            })
            .collect();
        if !r.ok || r.at != bytes.len() {
            return Err(bad);
        }
        let ng_sha = bytes[68..100].try_into().unwrap();
        Ok(TinyLm {
            n_head,
            d,
            ctx,
            chat,
            formal,
            levels,
            ng_sha,
            file_sha: String::new(),
            kn_sha,
            left_len,
            boundary_k,
            ids,
            v,
            emb,
            pos,
            lnf_w,
            lnf_b,
            layers,
            raw,
        })
    }

    /// The context window in characters (a candidate longer than `ctx - 1` is `TooLong`).
    pub fn ctx(&self) -> usize {
        self.ctx
    }

    /// The standard (alpha, tau, delta) tuned for the profile; delta is 0 in a version 1 file.
    pub fn alpha_tau_delta(&self, p: Profile) -> (f64, f64, f64) {
        self.params(p, RerankLevel::Standard)
    }

    /// (alpha, tau, delta) of the profile at `level`; the standard triple for every level of a version 1 or 2 file.
    pub fn params(&self, p: Profile, level: RerankLevel) -> (f64, f64, f64) {
        let by_level = match p {
            Profile::Chat => &self.chat,
            Profile::Formal => &self.formal,
        };
        by_level[level as usize]
    }

    /// Whether the file is version 3 or 4, i.e. has its own conservative and aggressive triples (docs/spec/semantic-rerank.md §7).
    pub fn has_levels(&self) -> bool {
        self.levels
    }

    /// The SHA-256 of the Kneser-Ney side file the parameters were tuned with (version 4); `None` when tuned without one (an
    /// all-zero field, or a version 1 to 3 file).
    pub fn kn_sha256(&self) -> Option<[u8; 32]> {
        self.kn_sha
    }

    /// The left-context length the file declares (version 5, docs/spec/semantic-rerank.md §5.2); `None` for versions 1 to 4, which the
    /// engine keys with `learn::context_key` as before.
    pub fn left_len(&self) -> Option<usize> {
        self.left_len
    }

    /// The key a stretch is scored with, `before` being the text in front of it (the left context, then the display before the
    /// stretch): for a model declaring L (SJTL0005) its last L characters as they are; for versions 1 to 4 `learn::context_key` of it.
    /// The one rule of the engine's rerank jobs and the evaluation CLI (docs/spec/semantic-rerank.md §5.2). Of a declared key
    /// the model sees at most ctx - 1 - |candidate| characters (`ll_many`'s window), i.e. min(L, ctx - 1 - |c|).
    pub fn key_for(&self, before: &str) -> String {
        match self.left_len {
            Some(l) => last_chars(before, l),
            None => context_key(before),
        }
    }

    /// The policy-key threshold K of a version 6 file with the boundary flag; `None` for every other file.
    pub fn boundary_k(&self) -> Option<usize> {
        self.boundary_k
    }

    /// The key a stretch is scored with when the longest candidate of its pool has `n` characters (docs/spec/semantic-rerank.md §5.2). Versions
    /// 1 to 5 and a version 6 without the boundary flag ignore `n`: `key_for`. With the boundary flag (the research side's
    /// `keys.policy_key_b(before[-L:], n, K)`): the last L characters of `before`, each run of non-Han characters one `SEP_CHAR`
    /// (a leading one dropped), cut to the last 63 - n tokens (then a leading `SEP_CHAR` dropped; empty when n >= 63); when that holds at
    /// least K Han characters it is the key, else the shipped key (`context_key` of those L characters, `SENTINEL` being empty) cut the same way.
    pub fn key_for_len(&self, before: &str, n: usize) -> String {
        let (Some(k), Some(l)) = (self.boundary_k, self.left_len) else { return self.key_for(before) };
        let raw = last_chars(before, l);
        let fit = |mut t: Vec<char>| -> Vec<char> {
            let keep = (self.ctx - 1).saturating_sub(n);
            t.drain(..t.len().saturating_sub(keep));
            if t.first() == Some(&SEP_CHAR) {
                t.remove(0);
            }
            t
        };
        let mut long: Vec<char> = Vec::new();
        for c in raw.chars() {
            if is_han(c) {
                long.push(c);
            } else if !long.is_empty() && long.last() != Some(&SEP_CHAR) {
                long.push(SEP_CHAR);
            }
        }
        let long = fit(long);
        if long.iter().filter(|&&c| c != SEP_CHAR).count() >= k {
            return long.into_iter().collect();
        }
        let ship = context_key(&raw);
        fit(if ship == SENTINEL { Vec::new() } else { ship.chars().collect() }).into_iter().collect()
    }

    /// SHA-256 (hex) of the `.sjtl` bytes `load_paired` parsed (empty for `parse` and `load`).
    pub fn file_sha256(&self) -> &str {
        &self.file_sha
    }

    /// SHA-256 (hex) of the n-gram model alpha and tau were tuned with.
    pub fn ngram_sha256(&self) -> String {
        hex(&self.ng_sha)
    }

    /// The S7a rerank of one n-best list, the one rule the CLI measures and the engine will use: the first `TOPK` candidates,
    /// k = `key_for` of the text before the stretch: for versions 1 to 4 the engine's context key (`learn::context_key`; its
    /// `SENTINEL`, no Han character before the cursor, is the empty k as in tinylm.row_key), for a model declaring `left_len` the last
    /// L characters as they are ("^" included; docs/spec/semantic-rerank.md §5.2), the caller's (alpha, tau, delta) (`params` of the profile and level), then `pick`. Returns the chosen index and, when the model ran (two or more
    /// candidates and not a confident span: ng[0] - ng[1] >= tau with no learning bias skips the forward pass and returns (0, None),
    /// docs/spec/semantic-rerank.md §3.3), the LLs. A candidate longer than ctx - 1 characters is `TooLong`; the caller decides to keep the first.
    /// `masked` (same order as `cands`; empty, or a missing entry, is false) marks the candidates that cannot be chosen: the caller's
    /// `manual_mask`, made once with the decoder's first candidate as the base (docs/spec/decoder.md §7); the threshold and the scores still use the
    /// whole list.
    pub fn rerank(
        &self, ng: &[f64], cands: &[&str], key: &str, params: (f64, f64, f64), masked: &[bool], learn: &Learning,
    ) -> Result<(usize, Option<Vec<f64>>), TinyLmError> {
        self.rerank_inner(ng, cands, key, params, masked, learn, true)
    }

    /// Like [`TinyLm::rerank`], but never skips the forward pass on confident spans so callers such as `--tinylm-scores` always get `ll`.
    pub fn rerank_with_ll(
        &self, ng: &[f64], cands: &[&str], key: &str, params: (f64, f64, f64), masked: &[bool], learn: &Learning,
    ) -> Result<(usize, Option<Vec<f64>>), TinyLmError> {
        self.rerank_inner(ng, cands, key, params, masked, learn, false)
    }

    #[allow(clippy::too_many_arguments)]
    fn rerank_inner(
        &self, ng: &[f64], cands: &[&str], key: &str, params: (f64, f64, f64), masked: &[bool], learn: &Learning, skip_confident: bool,
    ) -> Result<(usize, Option<Vec<f64>>), TinyLmError> {
        // The header's check, for every caller: a NaN alpha or a negative delta from outside the file must not reach `pick`.
        if !valid_params(params) {
            return Err(TinyLmError::Format);
        }
        let n = cands.len().min(ng.len()).min(TOPK);
        if n < 2 {
            return Ok((0, None));
        }
        if cands[..n].iter().any(|c| c.chars().count() > self.ctx - 1) {
            return Err(TinyLmError::TooLong);
        }
        let (alpha, tau, delta) = params;
        if skip_confident && ng[0] - ng[1] >= tau && !learn.b.iter().take(n).any(|&x| x != 0.0) {
            return Ok((0, None));
        }
        let Some(ll) = self.rerank_ll(&cands[..n], key)? else { return Ok((0, None)) };
        let term = learn.term(&ll);
        Ok((pick_masked(&ng[..n], &ll, alpha, tau, delta, masked, &term), Some(ll)))
    }

    /// What `rerank` and `rerank_colloquial` share: the LLs of the (already cut to `TOPK`) candidates under the key (`key_for`; for versions
    /// 1 to 4 its `SENTINEL` is the empty k, as in tinylm.row_key, while a declared-length key is used as it is), or `None` when there are
    /// fewer than two and the model does not run.
    fn rerank_ll(&self, cands: &[&str], key: &str) -> Result<Option<Vec<f64>>, TinyLmError> {
        if cands.len() < 2 {
            return Ok(None);
        }
        let k = if key == SENTINEL && self.left_len.is_none() { "" } else { key };
        let items: Vec<(&str, &str)> = cands.iter().map(|c| (k, *c)).collect();
        self.ll_many(&items).map(Some)
    }

    /// `rerank` with the colloquial term (docs/spec/semantic-rerank.md §4, §4.4): the same first `TOPK`
    /// candidates and `ll_many`, then col_i = `co.col(words[i])` for the word split of each candidate, ll_eff = `colloquial::fuse(ll, col, alpha)`, and
    /// `pick` with `colloquial::cell(level)`, the level's registered alpha, tau and delta, not the file's. A col that is not finite drops the term for this call: the plain `pick` with the file's chat triple of the same
    /// `level` (`colloquial::guarded_pick`). `masked` masks the candidates as in `rerank`, in the fused pick and in its fallback. The engine
    /// and the evaluation CLI both call this one function. Returns the chosen index and the raw LLs, as `rerank` does.
    #[allow(clippy::too_many_arguments)]
    pub fn rerank_colloquial(
        &self, ng: &[f64], cands: &[&str], words: &[Vec<&str>], key: &str, co: &Colloquial, level: RerankLevel,
        masked: &[bool], learn: &Learning,
    ) -> Result<(usize, Option<Vec<f64>>), TinyLmError> {
        self.rerank_colloquial_inner(ng, cands, words, key, co, level, masked, learn, true)
    }

    /// Like [`TinyLm::rerank_colloquial`], but never skips the forward pass on confident spans so callers such as `--tinylm-scores` always get `ll`.
    #[allow(clippy::too_many_arguments)]
    pub fn rerank_colloquial_with_ll(
        &self, ng: &[f64], cands: &[&str], words: &[Vec<&str>], key: &str, co: &Colloquial, level: RerankLevel,
        masked: &[bool], learn: &Learning,
    ) -> Result<(usize, Option<Vec<f64>>), TinyLmError> {
        self.rerank_colloquial_inner(ng, cands, words, key, co, level, masked, learn, false)
    }

    #[allow(clippy::too_many_arguments)]
    fn rerank_colloquial_inner(
        &self, ng: &[f64], cands: &[&str], words: &[Vec<&str>], key: &str, co: &Colloquial, level: RerankLevel,
        masked: &[bool], learn: &Learning, skip_confident: bool,
    ) -> Result<(usize, Option<Vec<f64>>), TinyLmError> {
        let n = cands.len().min(ng.len()).min(words.len()).min(TOPK);
        if n < 2 {
            return Ok((0, None));
        }
        if cands[..n].iter().any(|c| c.chars().count() > self.ctx - 1) {
            return Err(TinyLmError::TooLong);
        }
        let col: Vec<f64> = words[..n].iter().map(|w| co.col(w)).collect();
        let plain = self.params(Profile::Chat, level);
        let cell = colloquial::cell(level);
        let eff_tau = if col.iter().all(|c| c.is_finite()) { cell.1 } else { plain.1 };
        if skip_confident && ng[0] - ng[1] >= eff_tau && !learn.b.iter().take(n).any(|&x| x != 0.0) {
            return Ok((0, None));
        }
        let Some(ll) = self.rerank_ll(&cands[..n], key)? else { return Ok((0, None)) };
        let term = learn.term(&ll);
        Ok((colloquial::guarded_pick(&ng[..n], &ll, &col, cell, plain, masked, &term), Some(ll)))
    }

    /// LL of each (k, c): the sum over the characters of c of log10 P(c_i | [BOS] + k + c_<i), as `tinylm.ll_many`
    /// (characters outside the table are UNK; the oldest characters of k are dropped when [BOS] + k + c exceeds the context, so the
    /// model sees the last min(|k|, ctx - 1 - |c|) characters of k;
    /// a c longer than ctx - 1 characters is an error; an empty c is 0).
    pub fn ll_many(&self, items: &[(&str, &str)]) -> Result<Vec<f64>, TinyLmError> {
        self.ll_many_with(Backend::DEFAULT, items)
    }

    /// The token sequence `ll_many` scores for (k, c): ([BOS] + k + c, index of the first token of c). A version 6 boundary-format model reads
    /// `SEP_CHAR` in k (and only in k) as [SEP], id V - 1; every character outside the table, a candidate's NUL included, is UNK. The oldest
    /// tokens of k are dropped when the whole exceeds the context; a c longer than ctx - 1 is `TooLong`.
    pub fn sequence(&self, k: &str, c: &str) -> Result<(Vec<u32>, usize), TinyLmError> {
        let sep = self.boundary_k.map(|_| (self.v - 1) as u32);
        let id = |c: char| self.ids.get(&c).copied().unwrap_or(UNK);
        let c: Vec<u32> = c.chars().map(id).collect();
        if c.len() > self.ctx - 1 {
            return Err(TinyLmError::TooLong);
        }
        let k: Vec<u32> = k.chars().map(|ch| match sep { Some(sep) if ch == SEP_CHAR => sep, _ => id(ch) }).collect();
        let k = &k[k.len().saturating_sub(self.ctx - 1 - c.len())..];
        Ok(([&[BOS], k, &c[..]].concat(), 1 + k.len()))
    }

    pub fn ll_many_with(&self, be: Backend, items: &[(&str, &str)]) -> Result<Vec<f64>, TinyLmError> {
        let mut out = Vec::with_capacity(items.len());
        for chunk in items.chunks(BATCH) {
            let seqs = chunk.iter().map(|(k, c)| self.sequence(k, c)).collect::<Result<Vec<_>, _>>()?;
            out.extend(self.ll_batch(be, &seqs));
        }
        Ok(out)
    }

    /// One right-padded batch: the hidden states, then the logits of only the positions that predict a token of c.
    fn ll_batch(&self, be: Backend, seqs: &[(Vec<u32>, usize)]) -> Vec<f64> {
        // SEC-11: no scored position (every c empty): every LL is 0, as the products over zero rows would give, without running the model
        // (no matrix restored or pinned, no m = 0 product).
        if seqs.iter().all(|(q, st)| q.len() <= *st) {
            return vec![0.0; seqs.len()];
        }
        let (d, t) = (self.d, seqs.iter().map(|(q, _)| q.len()).max().unwrap());
        let mut ids = vec![PAD; seqs.len() * t];
        for (b, (q, _)) in seqs.iter().enumerate() {
            ids[b * t..b * t + q.len()].copy_from_slice(q);
        }
        let lens: Vec<usize> = seqs.iter().map(|(q, _)| q.len()).collect();
        let x = self.hidden(be, &ids, &lens, t);
        // Position p of sequence b predicts token p + 1; the scored ones are p = st - 1 ..= len - 2.
        let (mut rows, mut targets) = (Vec::new(), Vec::new());
        for (b, (q, st)) in seqs.iter().enumerate() {
            for p in st - 1..q.len() - 1 {
                rows.extend_from_slice(&x[(b * t + p) * d..(b * t + p + 1) * d]);
                targets.push((b, q[p + 1] as usize));
            }
        }
        let v = self.v;
        let mut logits = vec![0f32; targets.len() * v];
        self.mul(be, &rows, &self.emb, &mut logits, (targets.len(), d, v), true);
        let (mut ll, mut e) = (vec![0f64; seqs.len()], vec![0f32; v]);
        for (z, &(b, target)) in logits.chunks_exact_mut(v).zip(&targets) {
            let m = z.iter().cloned().fold(f32::NEG_INFINITY, f32::max);
            let zt = z[target] - m;
            z.iter_mut().for_each(|x| *x -= m);
            exp_into(be, z, &mut e);
            let sum: f64 = e.iter().map(|&x| x as f64).sum();
            ll[b] += (zt as f64 - sum.ln()) / LN10;
        }
        ll
    }

    /// The final-LayerNorm hidden states (B * T rows of d) of the padded batch `ids` (B * T) with real lengths `lens`.
    /// Attention only runs for real positions and only looks backwards (the causal mask); padded rows are never read.
    fn hidden(&self, be: Backend, ids: &[u32], lens: &[usize], t: usize) -> Vec<f32> {
        let (d, h) = (self.d, self.n_head);
        let (dh, rows) = (d / h, ids.len());
        let mut x = vec![0f32; rows * d];
        let mut e = vec![0f32; d];
        for (r, &id) in ids.iter().enumerate() {
            self.emb_row(id as usize, &mut e);
            let p = &self.pos[r % t * d..][..d];
            x[r * d..(r + 1) * d].iter_mut().zip(e.iter().zip(p)).for_each(|(o, (a, b))| *o = a + b);
        }
        let scale = (dh as f32).sqrt();
        let mut s = vec![0f32; t];
        for l in &self.layers {
            let mut qkv = bias_rows(&l.qkv_b, rows);
            self.mul(be, &layer_norm(&x, &l.ln1_w, &l.ln1_b), &l.qkv_w, &mut qkv, (rows, d, 3 * d), false);
            let mut att = vec![0f32; rows * d];
            for (b, &len) in lens.iter().enumerate() {
                for hd in 0..h {
                    for ti in 0..len {
                        let q = &qkv[(b * t + ti) * 3 * d + hd * dh..][..dh];
                        for tj in 0..=ti {
                            let k = &qkv[(b * t + tj) * 3 * d + d + hd * dh..][..dh];
                            s[tj] = q.iter().zip(k).map(|(a, b)| a * b).sum::<f32>() / scale;
                        }
                        let m = s[..=ti].iter().cloned().fold(f32::NEG_INFINITY, f32::max);
                        // Scalar exp on both paths: at most ctx values per row, where a vForce call per row costs more than it saves.
                        s[..=ti].iter_mut().for_each(|e| *e = (*e - m).exp());
                        let sum: f32 = s[..=ti].iter().sum();
                        let o = &mut att[(b * t + ti) * d + hd * dh..][..dh];
                        for tj in 0..=ti {
                            let v = &qkv[(b * t + tj) * 3 * d + 2 * d + hd * dh..][..dh];
                            o.iter_mut().zip(v).for_each(|(o, v)| *o += s[tj] / sum * v);
                        }
                    }
                }
            }
            let mut proj = bias_rows(&l.proj_b, rows);
            self.mul(be, &att, &l.proj_w, &mut proj, (rows, d, d), false);
            x.iter_mut().zip(&proj).for_each(|(a, b)| *a += b);
            let mut f = bias_rows(&l.fc1_b, rows);
            self.mul(be, &layer_norm(&x, &l.ln2_w, &l.ln2_b), &l.fc1_w, &mut f, (rows, d, 4 * d), false);
            gelu(be, &mut f);
            let mut o = bias_rows(&l.fc2_b, rows);
            self.mul(be, &f, &l.fc2_w, &mut o, (rows, 4 * d, d), false);
            x.iter_mut().zip(&o).for_each(|(a, b)| *a += b);
        }
        layer_norm(&x, &self.lnf_w, &self.lnf_b)
    }

    /// The int8 matrices in file order: the embedding, then each layer's qkv, proj, fc1 and fc2.
    #[cfg(target_vendor = "apple")]
    fn slabs(&self) -> impl Iterator<Item = &purgeable::PurgeableSlab> {
        std::iter::once(&self.emb)
            .chain(self.layers.iter().flat_map(|l| [&l.qkv_w, &l.proj_w, &l.fc1_w, &l.fc2_w]))
            .filter_map(|m| match &m.w {
                Weights::Q8 { slab, .. } => Some(slab),
                Weights::F32(_) => None,
            })
    }

    /// docs/spec/semantic-rerank.md §2.4: the B1 counters summed over this model's slabs (`PurgeableStats`), for the SEC-12 probe and the tests.
    #[cfg(target_vendor = "apple")]
    pub fn purgeable_stats(&self) -> PurgeableStats {
        self.slabs().fold(PurgeableStats::default(), |mut s, slab| {
            let c = slab.counts();
            s.slab_count += 1;
            s.alloc_ok += c.alloc_ok;
            s.pin_ok += c.pin_ok;
            s.volatile_ok += c.volatile_ok;
            s.empty_rebuilds += c.empty_rebuilds;
            s.fallbacks += c.fallbacks;
            s.currently_volatile += usize::from(c.volatile_now);
            s
        })
    }

    /// Row `id` of the embedding (d values) into `out`. An int8 embedding restores just that row from the file (SEC-09: the lookup never pins
    /// the 20 MiB restored embedding); the values are the ones the whole restored matrix holds.
    fn emb_row(&self, id: usize, out: &mut [f32]) {
        let d = self.d;
        match &self.emb.w {
            Weights::F32(w) => out.copy_from_slice(&w[id * d..][..d]),
            Weights::Q8 { at, scales, .. } => restore_rows(&self.raw[at + id * d..][..d], &scales[id..id + 1], d, out),
        }
    }

    /// c (m x n) += a (m x k) . w, as `gemm` with the matrix `w` (b x n, or n x k transposed when `trans_b`). docs/spec/semantic-rerank.md §2.4:
    /// an f32 matrix goes to `gemm` as it is. An int8 matrix: on Accelerate, restored whole into its purgeable slab (B1) and pinned for exactly this
    /// one `cblas_sgemm`, the same call on the same values as with the expanded matrix; one pin at a time, released before return. When the slab is
    /// not available (a Mach call failed, counted in `purgeable_stats().fallbacks`), and always on `Backend::Plain`, `gemm_q8`.
    fn mul(&self, be: Backend, a: &[f32], w: &Matrix, c: &mut [f32], dims: (usize, usize, usize), trans_b: bool) {
        match &w.w {
            Weights::F32(b) => gemm(be, a, b, c, dims, trans_b),
            Weights::Q8 { at, scales, #[cfg(target_vendor = "apple")] slab } => {
                let q = &self.raw[*at..*at + w.rows * w.cols];
                #[cfg(target_vendor = "apple")]
                if be == Backend::Accelerate {
                    if let Some(pin) = slab.pin(|dst| restore_rows(q, scales, w.cols, dst)) {
                        return gemm(be, a, &pin, c, dims, trans_b);
                    }
                }
                let ran = gemm_q8(selected_kernel(), a, q, scales, c, dims, trans_b);
                debug_assert!(ran, "the selected kernel is one this CPU has");
            }
        }
    }
}

/// `rows` copies of `b` (the bias; the product is then accumulated onto it).
fn bias_rows(b: &[f32], rows: usize) -> Vec<f32> {
    b.iter().cloned().cycle().take(rows * b.len()).collect()
}

/// LayerNorm over the last axis, eps 1e-5, as numpy: (x - mean) / sqrt(var + eps) * w + b.
fn layer_norm(x: &[f32], w: &[f32], b: &[f32]) -> Vec<f32> {
    let d = w.len();
    let mut out = Vec::with_capacity(x.len());
    for row in x.chunks_exact(d) {
        let mu = row.iter().map(|&v| v as f64).sum::<f64>() / d as f64;
        let var = row.iter().map(|&v| (v as f64 - mu).powi(2)).sum::<f64>() / d as f64;
        let inv = 1.0 / (var + 1e-5).sqrt();
        out.extend(row.iter().zip(w.iter().zip(b)).map(|(&v, (&w, &b))| ((v as f64 - mu) * inv) as f32 * w + b));
    }
    out
}

/// GELU, tanh form: 0.5 x (1 + tanh(0.7978845608028654 (x + 0.044715 x^3))).
fn gelu(be: Backend, x: &mut [f32]) {
    let u: Vec<f32> = x.iter().map(|&v| 0.797_884_560_802_865_4_f32 * (v + 0.044715_f32 * v * v * v)).collect();
    let mut t = vec![0f32; u.len()];
    tanh_into(be, &u, &mut t);
    x.iter_mut().zip(&t).for_each(|(v, t)| *v = 0.5 * *v * (1.0 + t));
}

/// Which of `cands` (the n-gram's first candidates of one stretch; `syl` are the readings its first one used, `Lexicon::path_readings`) the rerank must not choose (docs/spec/decoder.md §3.6
/// docs/spec/decoder.md §7): the hand-added words (`ManualSet`) that the first candidate holds on their syllables are protected, and a candidate that does not hold
/// every one of them at the same place is masked. A manual word on other syllables, or that the first candidate does not hold, protects nothing; with nothing
/// protected no candidate is masked. The first candidate holds all it protects, so it is never masked. The one place this is decided: the engine calls it
/// once when it makes a rerank job and the evaluation CLI once per row, always with the decoder's order (its first candidate the base), and hand the result to
/// `rerank` / `rerank_colloquial` (permuted with the pool order of docs/spec/semantic-rerank.md §3.1.1 when that applies).
pub fn manual_mask(syl: &[String], cands: &[&str], manual: &ManualSet) -> Vec<bool> {
    let mut masked = vec![false; cands.len()];
    let Some(first) = cands.first().filter(|_| !manual.is_empty()) else { return masked };
    let first: Vec<char> = first.chars().collect();
    let mut protected: Vec<(usize, &[char])> = Vec::new();
    for (w, r) in manual.iter() {
        for i in 0..(syl.len() + 1).saturating_sub(r.len()) {
            if syl[i..i + r.len()] == *r && first.get(i..i + w.len()) == Some(w) {
                protected.push((i, w));
            }
        }
    }
    if protected.is_empty() {
        return masked;
    }
    for (m, c) in masked.iter_mut().zip(cands) {
        let c: Vec<char> = c.chars().collect();
        *m = !protected.iter().all(|&(i, w)| c.get(i..i + w.len()) == Some(w));
    }
    masked
}

/// The S7a fusion with the S7e gate, tools/lib/fusegrid.py `pick` with its `delta`: the index of the chosen
/// candidate. Fewer than two candidates, or an n-gram margin (first minus second) of at least `tau`, keeps the first; otherwise
/// j = the highest s = `ng + alpha * ll` (ties to the better n-gram rank), and j replaces the first only when `s[j] - s[0] >= delta`.
/// Delta 0 is the S7a rule.
pub fn pick(ng: &[f64], ll: &[f64], alpha: f64, tau: f64, delta: f64) -> usize {
    pick_masked(ng, ll, alpha, tau, delta, &[], &[])
}

/// docs/spec/learning.md §7.4: what the sentence-start learning gives one stretch of a rerank job. `layer`: the longest layer
/// (fine, mid, coarse) that has a record at the stretch's start; only that layer's `b` is used. `b[i]` is
/// `w / (w + 1)` of the layer's record of the first character of candidate `i` (a missing entry is 0). `scale` multiplies all three registered
/// constants: 1 in production, the tests' knob (`Engine::set_eps_sinit`; 0 is no learning). `Learning::default()` is no learning.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Learning {
    pub layer: SinitLayer,
    pub b: Vec<f64>,
    pub scale: f64,
}

impl Learning {
    /// The learning term of every candidate, relative to the first (the anchor's is 0), for the small model's log-likelihoods `ll`. Fine layer:
    /// `EPS_SINIT_FINE * (b_i - b_0)`, both signs. Mid layer: `EPS_SINIT_MID * max(b_i - b_0, 0)` and only where `ll_i > ll_0`, else 0. Coarse layer: the same with
    /// `EPS_SINIT_COARSE`. The layers are never added. The result goes to `pick_masked` (and `colloquial::fused_pick`, `guarded_pick`) as their `learn`.
    pub fn term(&self, ll: &[f64]) -> Vec<f64> {
        let b = |i: usize| self.b.get(i).copied().unwrap_or(0.0);
        let gated = |eps: f64, i: usize| if ll[i] > ll[0] { self.scale * eps * (b(i) - b(0)).max(0.0) } else { 0.0 };
        (0..ll.len())
            .map(|i| match self.layer {
                SinitLayer::Fine => self.scale * EPS_SINIT_FINE * (b(i) - b(0)),
                SinitLayer::Mid => gated(EPS_SINIT_MID, i),
                SinitLayer::Coarse => gated(EPS_SINIT_COARSE, i),
            })
            .collect()
    }
}

/// `pick` where the candidates `masked` marks (`manual_mask`; a missing entry is false) cannot be chosen, and with the sentence-start learning
/// term `learn` (docs/spec/learning.md §7.4, §7.5; empty or all equal: none). The threshold `ng[0] - ng[1] >= tau`
/// and every score are those of the whole list; the first candidate is never masked, so there is always one to choose.
///
/// `learn[i]` is the learning term of candidate `i` (`Learning::term`; a missing entry is 0): candidate `i` scores `ng + alpha * ll + (learn[i] - learn[0])`, relative to the
/// first like the colloquial term. When the first minus second n-gram margin is `tau` or more and a candidate that can be chosen has
/// `learn[i] > learn[0]`, only such candidates may replace the first (a taught choice would otherwise be out of reach exactly where the n-gram
/// is sure), and by `delta` like any other; the others are out. Without a learned candidate nothing changes, and with `learn` all 0 every result is the
/// one `pick` always gave.
pub fn pick_masked(ng: &[f64], ll: &[f64], alpha: f64, tau: f64, delta: f64, masked: &[bool], learn: &[f64]) -> usize {
    if ng.len() < 2 {
        return 0;
    }
    let l = |i: usize| learn.get(i).copied().unwrap_or(0.0);
    let usable = |i: usize| masked.get(i) != Some(&true);
    let sure = ng[0] - ng[1] >= tau;
    let taught = |i: usize| l(i) > l(0);
    if sure && !(1..ng.len()).any(|i| usable(i) && taught(i)) {
        return 0;
    }
    let s0 = ng[0] + alpha * ll[0];
    let (mut best, mut best_s) = (0, s0);
    for i in 1..ng.len() {
        if !usable(i) || (sure && !taught(i)) {
            continue;
        }
        let s = ng[i] + alpha * ll[i] + (l(i) - l(0));
        if s > best_s {
            (best, best_s) = (i, s);
        }
    }
    if best_s - s0 >= delta {
        best
    } else {
        0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Uniform in +-s from a fixed LCG.
    fn rnd(state: &mut u64, s: f32) -> f32 {
        *state = state.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        ((*state >> 40) as f32 / (1u64 << 24) as f32 * 2.0 - 1.0) * s
    }

    /// How a random matrix is stored: f32, int8 rows plus f32 scales, or (`Dequant`) f32 holding exactly what the int8 form restores to, so an int8
    /// file and its `Dequant` twin are the same model, one through the int8 path and one through the f32 path.
    #[derive(Clone, Copy, PartialEq, Eq)]
    enum Store {
        F32,
        Int8,
        Dequant,
    }

    /// A random matrix as the file stores it.
    fn put_mat(b: &mut Vec<u8>, st: &mut u64, store: Store, r: usize, c: usize) {
        let w: Vec<f32> = (0..r * c).map(|_| rnd(st, 0.3)).collect();
        if store == Store::F32 {
            w.iter().for_each(|x| b.extend_from_slice(&x.to_le_bytes()));
            return;
        }
        let scales: Vec<f32> = w.chunks(c).map(|row| row.iter().fold(0f32, |m, x| m.max(x.abs())).max(1e-12) / 127.0).collect();
        let q: Vec<i8> = w.chunks(c).zip(&scales).flat_map(|(row, s)| row.iter().map(move |x| (x / s).round() as i8)).collect();
        if store == Store::Int8 {
            b.extend(q.iter().map(|&x| x as u8));
            scales.iter().for_each(|s| b.extend_from_slice(&s.to_le_bytes()));
        } else {
            for (row, s) in q.chunks(c).zip(&scales) {
                row.iter().for_each(|&x| b.extend_from_slice(&(x as f32 * s).to_le_bytes()));
            }
        }
    }

    /// A vector of `n` values near `g` (LayerNorm gains near 1, the rest near 0).
    fn put_vec(b: &mut Vec<u8>, st: &mut u64, n: usize, g: f32) {
        (0..n).for_each(|_| b.extend_from_slice(&(g + rnd(st, 0.1)).to_le_bytes()));
    }

    /// A random SJTL0001 file written the way export_sjtl.py lays it out (d = 64, 2 layers, 4 heads, V = 120): wide enough for
    /// the two matrix paths to differ in summation order.
    fn random_file(int8: bool) -> Vec<u8> {
        random_file_as(if int8 { Store::Int8 } else { Store::F32 })
    }

    fn random_file_as(store: Store) -> Vec<u8> {
        random_file_dims(store, 2, 4, 64, 64, 120)
    }

    fn random_file_dims(store: Store, n_layer: usize, n_head: usize, d: usize, ctx: usize, v: usize) -> Vec<u8> {
        let mut st = 0x2545_f491_4f6c_dd1du64;
        let mut b = Vec::new();
        b.extend_from_slice(MAGIC);
        for x in [VERSION, (store == Store::Int8) as u32, n_layer as u32, n_head as u32, d as u32, ctx as u32, v as u32] {
            b.extend_from_slice(&x.to_le_bytes());
        }
        for x in [0.7f64, 2.0, 0.7, 2.0] {
            b.extend_from_slice(&x.to_le_bytes());
        }
        b.extend_from_slice(&[7u8; 32]);
        (0..v - 3).for_each(|i| b.extend_from_slice(&(0x4e00 + i as u32).to_le_bytes()));
        put_mat(&mut b, &mut st, store, v, d);
        put_mat(&mut b, &mut st, store, ctx, d);
        put_vec(&mut b, &mut st, d, 1.0);
        put_vec(&mut b, &mut st, d, 0.0);
        for _ in 0..n_layer {
            put_vec(&mut b, &mut st, d, 1.0);
            put_vec(&mut b, &mut st, d, 0.0);
            put_mat(&mut b, &mut st, store, d, 3 * d);
            put_vec(&mut b, &mut st, 3 * d, 0.0);
            put_mat(&mut b, &mut st, store, d, d);
            put_vec(&mut b, &mut st, d, 0.0);
            put_vec(&mut b, &mut st, d, 1.0);
            put_vec(&mut b, &mut st, d, 0.0);
            put_mat(&mut b, &mut st, store, d, 4 * d);
            put_vec(&mut b, &mut st, 4 * d, 0.0);
            put_mat(&mut b, &mut st, store, 4 * d, d);
            put_vec(&mut b, &mut st, d, 0.0);
        }
        b
    }

    /// The 100 header bytes of a version 1 file of these sizes (wtype 0, one head).
    fn header_v1(n_layer: usize, d: usize, ctx: usize, v: usize) -> Vec<u8> {
        let mut b = Vec::new();
        b.extend_from_slice(MAGIC);
        for x in [VERSION, 0, n_layer as u32, 1, d as u32, ctx as u32, v as u32] {
            b.extend_from_slice(&x.to_le_bytes());
        }
        for x in [0.7f64, 2.0, 0.7, 2.0] {
            b.extend_from_slice(&x.to_le_bytes());
        }
        b.extend_from_slice(&[7u8; 32]);
        b
    }

    /// A file of exactly the length `parse` expects for these sizes, all tensors zero (finite), f32.
    fn zero_file(n_layer: usize, d: usize, ctx: usize, v: usize) -> Vec<u8> {
        let mut b = header_v1(n_layer, d, ctx, v);
        // From U+10000: 65,533 characters from U+4E00 would cross the surrogates.
        (0..v - 3).for_each(|i| b.extend_from_slice(&(0x10000 + i as u32).to_le_bytes()));
        // Per layer: four matrices (3 d^2 + d^2 + 4 d^2 + 4 d^2) and the vectors ln1 2d, qkv_b 3d, proj_b d, ln2 2d, fc1_b 4d, fc2_b d.
        let layer = 4 * (12 * d * d + 13 * d);
        b.resize(b.len() + 4 * (v * d + ctx * d + 2 * d) + n_layer * layer, 0);
        b
    }

    /// docs/spec/semantic-rerank.md §2.2: a header past any bound is `Format` even when the length matches it; at the bound it parses
    /// (so the length helper is right and only the bound rejects).
    #[test]
    fn header_bounds() {
        for (n_layer, d, ctx, v) in [(MAX_LAYERS, 4, 2, 3), (1, 4, MAX_CTX, 3), (1, 4, 2, MAX_V)] {
            assert!(TinyLm::parse(&zero_file(n_layer, d, ctx, v)).is_ok(), "at the bound");
        }
        for (n_layer, d, ctx, v) in [(MAX_LAYERS + 1, 4, 2, 3), (1, 4, MAX_CTX + 1, 3), (1, 4, 2, MAX_V + 1), (1, MAX_D + 1, 2, 3)] {
            assert_eq!(TinyLm::parse(&zero_file(n_layer, d, ctx, v)).err(), Some(TinyLmError::Format), "past the bound");
        }
    }

    /// a finite int8 scale that restores to inf (127 * f32::MAX) is `Format`; a length off by one byte is `Format`.
    #[test]
    fn restored_inf_and_length_off_by_one() {
        let good = random_file(true);
        assert!(TinyLm::parse(&good).is_ok());
        // The first matrix (emb, V 120 x d 64) follows the vocabulary; its scales follow its V * d int8 bytes.
        let scale0 = HEADER + 4 * (120 - 3) + 120 * 64;
        let mut b = good.clone();
        b[scale0..scale0 + 4].copy_from_slice(&f32::MAX.to_le_bytes());
        assert_eq!(TinyLm::parse(&b).err(), Some(TinyLmError::Format), "restored inf");
        let mut longer = good.clone();
        longer.push(0);
        assert_eq!(TinyLm::parse(&longer).err(), Some(TinyLmError::Format), "one byte more");
        assert_eq!(TinyLm::parse(&good[..good.len() - 1]).err(), Some(TinyLmError::Format), "one byte less");
    }

    /// `load` reads only a regular file of at most `MAX_FILE` bytes.
    #[test]
    fn load_needs_a_regular_file_within_the_cap() {
        let dir = std::env::temp_dir().join(format!("shanjie-tinylm-load-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let ok = dir.join("ok.sjtl");
        std::fs::write(&ok, random_file(true)).unwrap();
        assert!(TinyLm::load(&ok).is_ok(), "control");
        assert_eq!(TinyLm::load(&dir).err(), Some(TinyLmError::Io), "a directory");
        // Sparse: no disk space used. Its content would also be a bad file, so only the cap can reject it as Format before reading.
        let big = dir.join("big.sjtl");
        std::fs::File::create(&big).unwrap().set_len(MAX_FILE + 1).unwrap();
        assert_eq!(TinyLm::load(&big).err(), Some(TinyLmError::Format), "over the cap");
        std::fs::remove_dir_all(dir).unwrap();
    }

    /// With the handle checked after opening: a FIFO with no writer is refused at once instead of blocking the open (without
    /// O_NONBLOCK the open would wait for a writer forever; the receive below would then time out).
    #[cfg(unix)]
    #[test]
    fn load_refuses_a_fifo_without_blocking() {
        let dir = std::env::temp_dir().join(format!("shanjie-tinylm-fifo-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let fifo = dir.join("fifo.sjtl");
        assert!(std::process::Command::new("mkfifo").arg(&fifo).status().unwrap().success(), "mkfifo");
        let (tx, rx) = std::sync::mpsc::channel();
        let p = fifo.clone();
        std::thread::spawn(move || tx.send(TinyLm::load(&p).err()).unwrap());
        let got = rx.recv_timeout(std::time::Duration::from_secs(10));
        std::fs::remove_dir_all(dir).unwrap();
        assert_eq!(got.expect("load blocked on the FIFO"), Some(TinyLmError::Io), "not a regular file");
    }

    fn strs(v: &[&str]) -> Vec<String> {
        v.iter().map(|s| s.to_string()).collect()
    }
    /// 米雅歌 as a manual word.
    fn mia() -> ManualSet {
        ManualSet::from_pairs([("米雅歌", "ㄇㄧˇ-ㄧㄚˇ-ㄍㄜ")])
    }
    const MIA: [&str; 3] = ["ㄇㄧˇ", "ㄧㄚˇ", "ㄍㄜ"];

    /// docs/spec/decoder.md §7, test 1: when the first candidate holds a manual word on its syllables, the candidates that do not hold it
    /// there are masked; a manual word the first candidate does not hold protects nothing; the word on other syllables of the stretch is not "held".
    #[test]
    fn manual_mask_masks_what_takes_the_word_out() {
        let syl = strs(&MIA);
        assert_eq!(manual_mask(&syl, &["米雅歌", "米亞哥", "米雅歌"], &mia()), [false, true, false]);
        assert_eq!(manual_mask(&syl, &["米雅歌", "米亞哥"], &ManualSet::default()), [false, false], "no manual word, nothing masked");
        assert_eq!(manual_mask(&syl, &["米亞哥", "米雅歌", "米亞歌"], &mia()), [false; 3], "the first does not hold it: nothing protected");
        assert_eq!(manual_mask(&strs(&["ㄇㄧˇ", "ㄧㄚˇ", "ㄍㄜ", "ㄇㄧˇ", "ㄧㄚˇ", "ㄍㄜ"]), &["米雅歌米亞哥", "米亞哥米雅歌", "米雅歌米雅歌"], &mia()), [false, true, false],
            "holding the word at the other place (the second candidate) is not holding it at the protected one");
        assert_eq!(manual_mask(&strs(&["ㄇㄧˇ", "ㄧㄚˇ", "ㄍㄜ"]), &["米雅歌"], &mia()), [false], "a single candidate");
        assert_eq!(manual_mask(&[], &[], &mia()), Vec::<bool>::new());
        // the same characters over other syllables are not the manual word
        assert_eq!(manual_mask(&strs(&["ㄇㄧˇ", "ㄧㄚˋ", "ㄍㄜ"]), &["米雅歌", "米亞哥"], &mia()), [false, false]);
    }

    /// Test 2: ng [0, -0.5, -3] with tau 2: the gate (0.5 < 2) and every score are those of the whole list, candidate 1 would win (-0.5 against -5 and -4) and is
    /// masked, so the better of candidates 0 and 2 is chosen (2: -4 against -5); without the mask candidate 1 is chosen.
    #[test]
    fn a_masked_candidate_cannot_win_but_still_counts_for_the_gate() {
        let (ng, ll) = ([0.0, -0.5, -3.0], [-5.0, 0.0, -1.0]);
        assert_eq!(pick(&ng, &ll, 1.0, 2.0, 0.0), 1, "premise: unmasked, candidate 1 is chosen");
        assert_eq!(pick_masked(&ng, &ll, 1.0, 2.0, 0.0, &[false, true, false], &[]), 2);
        assert_eq!(pick_masked(&ng, &[-1.0, 0.0, -9.0], 1.0, 2.0, 0.0, &[false, true, false], &[]), 0, "the better of 0 and 2");
        assert_eq!(pick_masked(&ng, &ll, 1.0, 2.0, 0.0, &[false, false, false], &[]), 1, "no mask is `pick`");
        // the gate uses the unmasked second place: ng[0] - ng[1] = 3 >= tau keeps the first although candidate 1 is masked and candidate 2 would win
        assert_eq!(pick_masked(&[0.0, -3.0, -3.5], &[-9.0, 0.0, 0.0], 1.0, 2.0, 0.0, &[false, true, false], &[]), 0);
        // delta still applies to the best unmasked candidate
        assert_eq!(pick_masked(&ng, &ll, 1.0, 2.0, 1.5, &[false, true, false], &[]), 0, "gain 1 below delta 1.5");
        // the real rerank: the toy model scores, the mask follows `manual_mask`
        let toy = TinyLm::parse(&std::fs::read(Path::new(env!("CARGO_MANIFEST_DIR")).join("../eval/golden/tinylm-tiny/tiny-int8.sjtl")).unwrap()).unwrap();
        let cands = ["米雅歌", "米亞哥", "米雅歌"];
        let (a, b) = toy.rerank(&[0.0, -0.5, -3.0], &cands, "", (100.0, f64::INFINITY, 0.0), &manual_mask(&strs(&MIA), &cands, &mia()), &Default::default()).unwrap();
        let (c, _) = toy.rerank(&[0.0, -0.5, -3.0], &cands, "", (100.0, f64::INFINITY, 0.0), &manual_mask(&strs(&MIA), &cands, &ManualSet::default()), &Default::default()).unwrap();
        let ll = b.unwrap();
        assert_eq!(c, pick(&[0.0, -0.5, -3.0], &ll, 100.0, f64::INFINITY, 0.0));
        assert_eq!(a, pick_masked(&[0.0, -0.5, -3.0], &ll, 100.0, f64::INFINITY, 0.0, &[false, true, false], &[]));
        assert_ne!(a, 1, "the masked candidate is never chosen");
    }

    /// `pick_masked` as it was before the SINIT learning term: the reference that an empty or all-zero `learn` must equal bit for bit in result.
    fn pick_before_sinit(ng: &[f64], ll: &[f64], alpha: f64, tau: f64, delta: f64, masked: &[bool]) -> usize {
        if ng.len() < 2 || ng[0] - ng[1] >= tau {
            return 0;
        }
        let s0 = ng[0] + alpha * ll[0];
        let (mut best, mut best_s) = (0, s0);
        for i in 1..ng.len() {
            if masked.get(i) == Some(&true) {
                continue;
            }
            let s = ng[i] + alpha * ll[i];
            if s > best_s {
                (best, best_s) = (i, s);
            }
        }
        if best_s - s0 >= delta { best } else { 0 }
    }

    /// docs/spec/learning.md §13.4: with `learn` empty, all 0, or any constant (the term is relative to the first candidate), the choice is the one before the term,
    /// on 20000 random lists with random masks, gates and thresholds (the LCG of the other tests); most of them choose something other than the first.
    #[test]
    fn a_zero_or_constant_learning_term_changes_nothing() {
        let mut st = 11u64;
        let (mut moved, mut gated) = (0, 0);
        for _ in 0..20_000 {
            let n = 2 + (((rnd(&mut st, 1.0) + 1.0) * 3.0) as usize).min(5);
            let ng: Vec<f64> = (0..n).map(|_| rnd(&mut st, 3.0) as f64).collect();
            let ll: Vec<f64> = (0..n).map(|_| rnd(&mut st, 5.0) as f64).collect();
            let masked: Vec<bool> = (0..n).map(|i| i > 0 && rnd(&mut st, 1.0) > 0.5).collect();
            let alpha = 0.3 + (rnd(&mut st, 1.0) + 1.0) as f64 * 0.35;
            let tau = [0.5, 2.0, f64::INFINITY][(((rnd(&mut st, 1.0) + 1.0) * 1.5) as usize).min(2)];
            let delta = [0.0, 0.25, 1.0][(((rnd(&mut st, 1.0) + 1.0) * 1.5) as usize).min(2)];
            let want = pick_before_sinit(&ng, &ll, alpha, tau, delta, &masked);
            moved += usize::from(want != 0);
            gated += usize::from(ng[0] - ng[1] >= tau);
            for learn in [Vec::new(), vec![0.0; n], vec![2.0; n], vec![0.5; n]] {
                assert_eq!(pick_masked(&ng, &ll, alpha, tau, delta, &masked, &learn), want, "{ng:?} {ll:?} {masked:?} {learn:?}");
            }
            assert_eq!(pick(&ng, &ll, alpha, tau, delta), pick_before_sinit(&ng, &ll, alpha, tau, delta, &[]));
        }
        assert!(moved > 3_000 && gated > 3_000, "premise: the lists move the choice ({moved}) and meet the gate ({gated}) often");
    }

    /// docs/spec/learning.md §7.4 and §7.5. The learning term is relative to the first candidate: the same constant on every candidate changes nothing, and a taught
    /// candidate gains its difference. Where the n-gram is sure (margin 3 >= tau 2) and candidate 2 is taught, candidate 1 (no learning, the better
    /// small-model score) is out: the result is 0 or 2, never 1; candidate 2 still has to win by delta; a masked taught candidate opens nothing; and
    /// where the margin is under tau everyone competes with the learning term added.
    #[test]
    fn the_learning_term_is_relative_and_a_taught_candidate_opens_the_gate() {
        let (ng, ll) = ([0.0, -3.0, -3.2], [-5.0, 0.0, -1.0]);
        let pick = |learn: &[f64], delta: f64, masked: &[bool]| pick_masked(&ng, &ll, 1.0, 2.0, delta, masked, learn);
        assert_eq!(pick(&[], 0.0, &[]), 0, "premise: no learning, the margin keeps the first");
        assert_eq!(pick(&[0.0, 0.0, 1.0], 0.0, &[]), 2, "taught candidate 2: -3.2 - 1 + 1 beats -5, candidate 1 (-3, the better score) is out");
        assert_eq!(pick(&[3.0, 3.0, 4.0], 0.0, &[]), 2, "a constant on every candidate changes nothing");
        assert_eq!(pick(&[0.0, 0.0, 0.5], 0.0, &[]), 2);
        assert_eq!(pick(&[0.0, 0.0, 0.5], 2.0, &[]), 0, "candidate 2 gains 1.3 over the first, under delta 2");
        assert_eq!(pick(&[0.0, 0.0, 0.5], 1.0, &[]), 2, "and over delta 1");
        assert_eq!(pick(&[0.0, 0.0, 1.0], 0.0, &[false, false, true]), 0, "a masked taught candidate cannot win and opens nothing");
        assert_eq!(pick(&[0.0, 1.0, 0.0], 0.0, &[]), 1, "taught candidate 1 is the one the gate lets through");
        assert_eq!(pick(&[0.5, 0.5, 0.0], 0.0, &[]), 0, "the first taught and the rest not: nothing is above it, the gate keeps the first");
        // under tau (margin 1): everyone competes, the learning term is added to the taught
        let ng_close = [0.0, -1.0, -1.2];
        assert_eq!(pick_masked(&ng_close, &ll, 1.0, 2.0, 0.0, &[], &[0.0, 0.0, 1.0]), 1, "-1 beats -1.2 - 1 + 1");
        assert_eq!(pick_masked(&ng_close, &ll, 1.0, 2.0, 0.0, &[], &[0.0, 0.0, 3.0]), 2, "-1.2 - 1 + 3 beats -1");
    }

    /// docs/spec/learning.md §7.4 and §7.5. The fine layer: `EPS_SINIT_FINE * (b_i - b_0)`, both signs, whatever the small model says. The mid layer:
    /// `EPS_SINIT_MID * max(b_i - b_0, 0)`, and only where `ll_i > ll_0` (equal is 0 too); the coarse layer the same with `EPS_SINIT_COARSE`; a taught anchor against an
    /// unlearned candidate is 0 (it only lifts). A `Learning` has one layer, so the layers never add (that the engine hands over the longest one is `engine_learn.rs`,
    /// `sinit_the_job_reads_the_longest_layer_that_has_a_record`). `scale` multiplies all three; no `b` is no term.
    #[test]
    fn the_learning_term_is_the_fine_layer_or_the_gated_mid_or_coarse_layer() {
        use SinitLayer::{Coarse, Fine, Mid};
        let ll = [-5.0, -4.0, -5.0, -6.0];
        let term = |layer: SinitLayer, b: &[f64]| Learning { layer, b: b.to_vec(), scale: 1.0 }.term(&ll);
        assert_eq!(term(Fine, &[0.0, 0.5, 0.5, 0.5]), [0.0, 2.0, 2.0, 2.0], "fine: 4 * (b_i - b_0), the model's scores do not matter");
        assert_eq!(term(Fine, &[0.5, 0.0, 0.25, 0.0]), [0.0, -2.0, -1.0, -2.0], "fine: a learned anchor is held against the others, both signs");
        assert_eq!(term(Mid, &[0.0, 0.5, 0.5, 0.5]), [0.0, 2.0, 0.0, 0.0], "mid: 4 * b_i only for ll_i > ll_0 (candidate 2 ties, candidate 3 is lower)");
        assert_eq!(term(Coarse, &[0.0, 0.5, 0.5, 0.5]), [0.0, 1.0, 0.0, 0.0], "coarse: 2 * b_i only for ll_i > ll_0");
        for layer in [Mid, Coarse] {
            assert_eq!(term(layer, &[0.5, 0.0, 0.25, 0.0]), [0.0; 4], "{layer:?}: a learned anchor lifts nothing and presses nothing");
        }
        assert_eq!(term(Mid, &[0.25, 0.5, 0.0, 0.0]), [0.0, 1.0, 0.0, 0.0], "mid: max(b_i - b_0, 0)");
        assert_eq!(term(Coarse, &[0.25, 0.5, 0.0, 0.0]), [0.0, 0.5, 0.0, 0.0], "coarse: max(b_i - b_0, 0)");
        let low = [-4.0, -5.0, -4.0, -6.0]; // ll_i <= ll_0 for every candidate
        for layer in [Mid, Coarse] {
            assert_eq!(Learning { layer, b: vec![0.0, 1.0, 1.0, 1.0], scale: 1.0 }.term(&low), [0.0; 4], "{layer:?}: 0 when ll_i <= ll_0");
        }
        assert_eq!(Learning { layer: Fine, b: vec![0.0, 1.0, 1.0, 1.0], scale: 1.0 }.term(&low), [0.0, 4.0, 4.0, 4.0], "fine is not gated");
        for layer in [Fine, Mid, Coarse] {
            assert_eq!(Learning { layer, b: vec![0.0, 0.5], scale: 0.0 }.term(&[-5.0, -4.0]), [0.0, 0.0], "{layer:?}: scale 0 is no learning");
        }
        assert_eq!(Learning { layer: Fine, b: vec![0.0, 0.5], scale: 3.0 }.term(&[-5.0, -4.0]), [0.0, 6.0]);
        assert_eq!(Learning { layer: Mid, b: vec![0.0, 0.5], scale: 3.0 }.term(&[-5.0, -4.0]), [0.0, 6.0]);
        assert_eq!(Learning { layer: Coarse, b: vec![0.0, 0.5], scale: 3.0 }.term(&[-5.0, -4.0]), [0.0, 3.0]);
        assert_eq!(Learning::default().term(&ll), [0.0; 4]);
        assert_eq!(Learning { layer: Coarse, b: vec![0.0, 0.5], scale: 1.0 }.term(&[-5.0, -4.0, -9.0]), [0.0, 1.0, 0.0], "a short `b` is 0 for the rest");
        // end to end through `pick_masked`. N-gram sure (margin 3 >= tau 2): a taught candidate 2 is let through by any layer (the gated ones because the model
        // scores it above the anchor).
        let (ng, ll3) = ([0.0, -3.0, -3.2], [-5.0, 0.0, -1.0]);
        let go = |l: Learning, ng: &[f64], ll: &[f64]| pick_masked(ng, ll, 1.0, 2.0, 0.0, &[], &l.term(ll));
        for layer in [Fine, Mid, Coarse] {
            assert_eq!(go(Learning { layer, b: vec![0.0, 0.0, 0.5], scale: 1.0 }, &ng, &ll3), 2, "{layer:?}");
        }
        // the model scores the taught candidate 2 below the anchor: the gated terms are 0, so the sure n-gram keeps the first; the fine term still lets it through
        let ll_low = [-1.0, 0.0, -1.5];
        for layer in [Mid, Coarse] {
            assert_eq!(go(Learning { layer, b: vec![0.0, 0.0, 1.0], scale: 1.0 }, &ng, &ll_low), 0, "{layer:?}: gated by the model");
        }
        assert_eq!(go(Learning { layer: Fine, b: vec![0.0, 0.0, 1.0], scale: 1.0 }, &ng, &ll_low), 2, "fine: not gated, -3.2 - 1.5 + 4 against -1");
        // margin under tau: a taught anchor (b_0 = 0.5) against an unlearned candidate 1 the model likes better. Fine holds it (the other sign), the others do not.
        let (ng_close, ll_pref) = ([0.0, -0.5], [-1.0, 0.5]);
        for layer in [Mid, Coarse] {
            assert_eq!(go(Learning { layer, b: vec![0.5, 0.0], scale: 1.0 }, &ng_close, &ll_pref), 1, "{layer:?}: only lifts, the unlearned candidate wins on the model");
        }
        assert_eq!(go(Learning { layer: Fine, b: vec![0.5, 0.0], scale: 1.0 }, &ng_close, &ll_pref), 0, "fine: the learned anchor is held, -0.5 + 0.5 - 2 against -1");
    }

    /// Test 3: another candidate that holds the same manual word at the same place may be chosen, and can beat the first.
    #[test]
    fn another_candidate_holding_the_word_can_win() {
        let syl = strs(&["ㄇㄧˇ", "ㄧㄚˇ", "ㄍㄜ", "ㄏㄠˇ"]);
        let mask = manual_mask(&syl, &["米雅歌好", "米亞哥好", "米雅歌號"], &mia());
        assert_eq!(mask, [false, true, false]);
        assert_eq!(pick_masked(&[0.0, -0.5, -1.0], &[-5.0, 0.0, -1.0], 1.0, 2.0, 0.0, &mask, &[]), 2, "candidate 2 holds the word and scores higher than 0");
    }

    /// Test 4: only `manual` rows make a `ManualSet`; a word of another tag (`names`) is not protected and the rerank may still replace it.
    #[test]
    fn only_manual_tagged_rows_are_protected() {
        let base = "ㄇㄧˇ 米 -3.0\nㄧㄚˇ 雅 -3.0\nㄍㄜ 歌 -3.0\n";
        let overlay = "ㄇㄧˇ-ㄧㄚˇ-ㄍㄜ\t米雅歌\t-5.0\tnames\nㄇㄧˇ-ㄧㄚˇ-ㄍㄜ\t咪雅歌\t-5.0\tmanual\n";
        let lex = crate::Lexicon::parse_with(base, Some(overlay)).unwrap();
        assert_eq!(lex.manual().len(), 1);
        let set = lex.manual();
        let syl = strs(&MIA);
        assert_eq!(manual_mask(&syl, &["咪雅歌", "咪亞哥"], set), [false, true], "the manual row protects");
        assert_eq!(manual_mask(&syl, &["米雅歌", "米亞哥"], set), [false, false], "the names row does not");
        assert_eq!(pick_masked(&[0.0, -0.5], &[-5.0, 0.0], 1.0, 2.0, 0.0, &manual_mask(&syl, &["米雅歌", "米亞哥"], set), &[]), 1, "so it is replaced");
        assert!(crate::Lexicon::parse_with(base, Some("ㄇㄧˇ-ㄧㄚˇ-ㄍㄜ\t米雅\t-5.0\tmanual\n")).unwrap().manual().is_empty(), "a word that is not one character per syllable is not kept");
    }

    /// `rerank` is the rule the CLI measured: the key reaches the model as k (a Han key changes the LLs, and they are exactly
    /// ll_many's with that k); the engine's SENTINEL is the empty k; fewer than two candidates run nothing; only the first TOPK
    /// are scored; the choice is `pick` with the profile's alpha, tau and delta (a nonzero delta: tests/tinylm_tiny.rs
    /// `rerank_uses_the_files_delta`).
    #[test]
    fn rerank_is_the_measured_rule() {
        let m = TinyLm::parse(&random_file(false)).unwrap();
        let cands: Vec<String> = (0..10).map(|i| (0..4).map(|j| char::from_u32(0x4e00 + ((i * 7 + j * 3) % 110) as u32).unwrap()).collect()).collect();
        let cands: Vec<&str> = cands.iter().map(String::as_str).collect();
        let ng: Vec<f64> = (0..10).map(|i| -0.05 * i as f64).collect();
        let with = |k: &str| m.ll_many(&cands[..TOPK].iter().map(|c| (k, *c)).collect::<Vec<_>>()).unwrap();
        let key = "\u{4e01}\u{4e02}";
        let chat = m.alpha_tau_delta(Profile::Chat);
        let (chosen, ll) = m.rerank(&ng, &cands, key, chat, &[], &Default::default()).unwrap();
        let ll = ll.unwrap();
        assert_eq!(ll, with(key));
        assert_ne!(ll, with(""), "the key is the model's context");
        assert_eq!(m.alpha_tau_delta(Profile::Chat), (0.7, 2.0, 0.0), "a version 1 file");
        assert_eq!(chosen, pick(&ng[..TOPK], &ll, 0.7, 2.0, 0.0));
        assert_eq!(m.rerank(&ng, &cands, SENTINEL, chat, &[], &Default::default()).unwrap().1.unwrap(), with(""), "SENTINEL is the empty k");
        assert_eq!(m.rerank(&ng[..1], &cands[..1], key, chat, &[], &Default::default()).unwrap(), (0, None));
        // params the header check would refuse are refused here too, even when fewer than two candidates would run nothing
        for bad in [(f64::NAN, 2.0, 0.0), (f64::INFINITY, 2.0, 0.0), (0.7, f64::NAN, 0.0), (0.7, 2.0, -0.25), (0.7, 2.0, f64::NAN), (0.7, 2.0, MAX_DELTA + 1.0)] {
            assert_eq!(m.rerank(&ng, &cands, key, bad, &[], &Default::default()).unwrap_err(), TinyLmError::Format, "{bad:?}");
            assert_eq!(m.rerank(&ng[..1], &cands[..1], key, bad, &[], &Default::default()).unwrap_err(), TinyLmError::Format, "{bad:?}");
        }
        assert!(m.rerank(&ng, &cands, key, (0.7, f64::INFINITY, 0.0), &[], &Default::default()).is_ok(), "an infinite tau is allowed: no threshold");
    }

    /// Every kernel of `Backend::Plain`, with the name the tests print.
    fn kernels() -> Vec<(Kernel, &'static str)> {
        #[allow(unused_mut)]
        let mut v = vec![(Kernel::Scalar, "scalar")];
        #[cfg(target_arch = "x86_64")]
        v.extend([(Kernel::Avx2, "avx2"), (Kernel::Avx512, "avx512f")]);
        v
    }

    /// The portable matrix product before the vector kernels (one running sum per element, the transposed dot product summed in order).
    fn old_plain(a: &[f32], b: &[f32], c: &mut [f32], (m, k, n): (usize, usize, usize), trans_b: bool) {
        for i in 0..m {
            if trans_b {
                for j in 0..n {
                    c[i * n + j] += a[i * k..(i + 1) * k].iter().zip(&b[j * k..(j + 1) * k]).map(|(x, y)| x * y).sum::<f32>();
                }
            } else {
                for p in 0..k {
                    let av = a[i * k + p];
                    for (cj, bj) in c[i * n..(i + 1) * n].iter_mut().zip(&b[p * n..(p + 1) * n]) {
                        *cj += av * bj;
                    }
                }
            }
        }
    }

    const GUARD: u32 = 0xdead_beef;

    /// One product on matrices that are the middle of larger buffers: `pad` elements on each side, NaN around a and b (a read outside
    /// them makes the output NaN) and a fixed bit pattern around c (a write outside it changes the pattern). Returns c's bits, or None
    /// when this CPU does not have the kernel.
    fn guarded(kernel: Kernel, a: &[f32], b: &[f32], c0: &[f32], dims: (usize, usize, usize), trans_b: bool, pad: usize) -> Option<Vec<u32>> {
        let wrap = |x: &[f32], fill: f32| -> Vec<f32> { [vec![fill; pad].as_slice(), x, vec![fill; pad].as_slice()].concat() };
        let (ga, gb, mut gc) = (wrap(a, f32::NAN), wrap(b, f32::NAN), wrap(c0, f32::from_bits(GUARD)));
        let ran = gemm_kernel(kernel, &ga[pad..pad + a.len()], &gb[pad..pad + b.len()], &mut gc[pad..pad + c0.len()], dims, trans_b);
        if !ran {
            return None;
        }
        assert!(gc[..pad].iter().chain(&gc[pad + c0.len()..]).all(|x| x.to_bits() == GUARD), "{kernel:?} wrote outside c");
        let out = &gc[pad..pad + c0.len()];
        assert!(out.iter().all(|x| !x.is_nan()), "{kernel:?} read outside a or b");
        Some(out.iter().map(|x| x.to_bits()).collect())
    }

    /// `guarded` for `gemm_q8`: bq inside -128 bytes and the scales inside NaN (a read outside them changes or NaNs the output), c inside the
    /// guard pattern; `pad` elements on each side. None when this CPU does not have the kernel.
    #[allow(clippy::too_many_arguments)]
    fn guarded_q8(kernel: Kernel, a: &[f32], bq: &[u8], sc: &[f32], c0: &[f32], dims: (usize, usize, usize), trans_b: bool, pad: usize) -> Option<Vec<u32>> {
        let gq = [vec![0x80u8; pad].as_slice(), bq, vec![0x80u8; pad].as_slice()].concat();
        let gs = [vec![f32::NAN; pad].as_slice(), sc, vec![f32::NAN; pad].as_slice()].concat();
        let mut gc = [vec![f32::from_bits(GUARD); pad].as_slice(), c0, vec![f32::from_bits(GUARD); pad].as_slice()].concat();
        let ran = gemm_q8(kernel, a, &gq[pad..pad + bq.len()], &gs[pad..pad + sc.len()], &mut gc[pad..pad + c0.len()], dims, trans_b);
        if !ran {
            return None;
        }
        assert!(gc[..pad].iter().chain(&gc[pad + c0.len()..]).all(|x| x.to_bits() == GUARD), "q8 {kernel:?} wrote outside c");
        let out = &gc[pad..pad + c0.len()];
        assert!(out.iter().all(|x| !x.is_nan()), "q8 {kernel:?} read outside the scales");
        Some(out.iter().map(|x| x.to_bits()).collect())
    }

    /// docs/spec/semantic-rerank.md §2.4: every kernel this CPU has gives the scalar kernel's result bit for bit, on both products, over
    /// the sizes around the vector widths and the model's, with the matrices at every alignment; the product that is not transposed is
    /// also the old portable product bit for bit. Prints which kernels ran.
    #[test]
    fn kernels_agree_bit_for_bit() {
        let sizes = [0usize, 1, 7, 8, 9, 15, 16, 17, 31, 32, 33, 384, 1536];
        let mut st = 7u64;
        let mut ran = vec![0usize; kernels().len()];
        let mut case = 0usize;
        for trans_b in [false, true] {
            for &m in &[0usize, 1, 2, 9] {
                for &k in &sizes {
                    for &n in &sizes {
                        let a: Vec<f32> = (0..m * k).map(|_| rnd(&mut st, 1.0)).collect();
                        let b: Vec<f32> = (0..k * n).map(|_| rnd(&mut st, 1.0)).collect();
                        // c starts at -0.0 in one case of four: an empty sum must still turn it into +0.0 on every kernel
                        let c0: Vec<f32> = (0..m * n).map(|q| if case % 4 == 0 && q % 3 == 0 { -0.0 } else { rnd(&mut st, 1.0) }).collect();
                        let pad = 64 + case % 4; // the matrices start 0, 1, 2 or 3 elements off
                        case += 1;
                        let want = guarded(Kernel::Scalar, &a, &b, &c0, (m, k, n), trans_b, pad).unwrap();
                        if !trans_b {
                            let mut old = c0.clone();
                            old_plain(&a, &b, &mut old, (m, k, n), false);
                            assert!(old.iter().map(|x| x.to_bits()).eq(want.iter().copied()), "scalar vs the old product, m={m} k={k} n={n}");
                        }
                        for (q, (kernel, name)) in kernels().into_iter().enumerate() {
                            if let Some(got) = guarded(kernel, &a, &b, &c0, (m, k, n), trans_b, pad) {
                                assert!(got == want, "{name} vs scalar, trans_b={trans_b} m={m} k={k} n={n}");
                                ran[q] += 1;
                            }
                        }
                        // the dispatch itself (what `Backend::Plain` runs)
                        let mut c = c0.clone();
                        gemm(Backend::Plain, &a, &b, &mut c, (m, k, n), trans_b);
                        assert!(c.iter().map(|x| x.to_bits()).eq(want.iter().copied()), "dispatch vs scalar, trans_b={trans_b} m={m} k={k} n={n}");
                        // the int8 product (`gemm_q8`) against the f32 product on the restored b, every kernel, b and its scales 0 to 15 elements off
                        let (rows, len) = if trans_b { (n, k) } else { (k, n) };
                        let bq: Vec<u8> = (0..k * n).map(|_| (rnd(&mut st, 128.0) as i32).clamp(-128, 127) as i8 as u8).collect();
                        let sc: Vec<f32> = (0..rows).map(|_| rnd(&mut st, 0.01).abs() + 1e-4).collect();
                        let mut bx = vec![0f32; k * n];
                        restore_rows(&bq, &sc, len, &mut bx);
                        let want_q = guarded(Kernel::Scalar, &a, &bx, &c0, (m, k, n), trans_b, pad).unwrap();
                        for (kernel, name) in kernels() {
                            if let Some(got) = guarded_q8(kernel, &a, &bq, &sc, &c0, (m, k, n), trans_b, 64 + case % 16) {
                                assert!(got == want_q, "q8 {name} vs f32 scalar, trans_b={trans_b} m={m} k={k} n={n}");
                            }
                        }
                    }
                }
            }
        }
        for ((_, name), r) in kernels().into_iter().zip(&ran) {
            if *r > 0 {
                assert_eq!(*r, case, "{name} ran on some sizes only");
                println!("ran {name} ({r} products)");
            } else {
                println!("skipped {name} (this CPU does not have it)");
            }
        }
        println!("selected {:?}", selected_kernel());
        assert!(kernels().into_iter().zip(&ran).any(|((kn, _), r)| kn == selected_kernel() && *r > 0), "the selected kernel ran");
    }

    /// The transposed dot product on an empty row adds +0.0: a -0.0 in c becomes +0.0.
    #[test]
    fn an_empty_dot_adds_positive_zero() {
        for (kernel, _) in kernels() {
            let mut c = [-0.0f32; 3];
            if gemm_kernel(kernel, &[], &[], &mut c, (1, 0, 3), true) {
                assert!(c.iter().all(|x| x.to_bits() == 0), "{kernel:?}");
            }
        }
    }

    #[test]
    #[should_panic(expected = "gemm: matrix lengths do not match")]
    fn a_short_a_is_refused() {
        gemm_kernel(Kernel::Scalar, &[0.0; 5], &[0.0; 6], &mut [0.0; 4], (2, 3, 2), false);
    }

    #[test]
    #[should_panic(expected = "gemm: matrix lengths do not match")]
    fn a_short_b_is_refused() {
        gemm_kernel(Kernel::Scalar, &[0.0; 6], &[0.0; 5], &mut [0.0; 4], (2, 3, 2), false);
    }

    #[test]
    #[should_panic(expected = "gemm: matrix lengths do not match")]
    fn a_short_c_is_refused() {
        gemm(Backend::Plain, &[0.0; 6], &[0.0; 6], &mut [0.0; 3], (2, 3, 2), true);
    }

    #[test]
    #[should_panic(expected = "gemm: matrix lengths do not match")]
    fn a_product_of_sizes_that_wraps_is_refused() {
        // m * k wraps to 0 = a.len(); without checked_mul the lengths would pass
        gemm(Backend::DEFAULT, &[], &[], &mut [], (usize::MAX / 2 + 1, 2, 0), false);
    }

    /// docs/spec/semantic-rerank.md §2.4: the Accelerate path (macOS) and the plain path agree to 1e-5 in LL on random weights; on other targets both
    /// are the plain path and the test only checks that scoring is deterministic.
    #[test]
    fn matrix_paths_agree() {
        let items = test_items();
        let pairs = as_pairs(&items);
        for int8 in [false, true] {
            let m = TinyLm::parse(&random_file(int8)).unwrap();
            let (a, p) = (m.ll_many_with(Backend::DEFAULT, &pairs).unwrap(), m.ll_many_with(Backend::Plain, &pairs).unwrap());
            assert!(a.iter().any(|x| *x != 0.0));
            for (x, y) in a.iter().zip(&p) {
                assert!((x - y).abs() < 1e-5, "int8={int8}: {x} vs {y}");
            }
        }
    }

    /// 20 (k, c) pairs over the random files' vocabulary (and a few characters outside it), keys of 0 to 39 characters.
    fn test_items() -> Vec<(String, String)> {
        (0..20)
            .map(|i| {
                let text = |n: usize, a: usize, b: usize| -> String { (0..n).map(|j| char::from_u32(0x4e00 + ((i * a + j * b) % 125) as u32).unwrap()).collect() };
                (text((i * 3) % 40, 31, 7), text(1 + i % 17, 13, 5))
            })
            .collect()
    }

    fn as_pairs(items: &[(String, String)]) -> Vec<(&str, &str)> {
        items.iter().map(|(k, c)| (k.as_str(), c.as_str())).collect()
    }

    fn bits(v: &[f64]) -> Vec<u64> {
        v.iter().map(|x| x.to_bits()).collect()
    }

    /// docs/spec/semantic-rerank.md §2.4: the int8 model scores exactly (bit for bit) as its `Dequant` twin, whose f32 matrices hold what the int8 ones
    /// restore to (the matrices of earlier builds): on the portable path (`gemm_q8` against the expanded product) and on the default path (macOS:
    /// Accelerate, through the purgeable slabs, against `cblas_sgemm` on the expanded matrices).
    #[test]
    fn q8_matches_dequantized_f32_bit_for_bit() {
        let (q, f) = (TinyLm::parse(&random_file_as(Store::Int8)).unwrap(), TinyLm::parse(&random_file_as(Store::Dequant)).unwrap());
        let items = test_items();
        let pairs = as_pairs(&items);
        for be in [Backend::Plain, Backend::DEFAULT] {
            let (a, b) = (q.ll_many_with(be, &pairs).unwrap(), f.ll_many_with(be, &pairs).unwrap());
            assert!(a.iter().any(|x| *x != 0.0));
            assert_eq!(bits(&a), bits(&b), "{be:?}");
        }
        #[cfg(target_vendor = "apple")]
        q.purgeable_stats().check().unwrap();
    }

    /// Apple platforms (macOS, and iOS for the simulator run) multiply through Accelerate, never the portable path.
    #[test]
    fn the_default_backend_is_accelerate_on_apple_platforms() {
        #[cfg(target_vendor = "apple")]
        assert_eq!(Backend::DEFAULT, Backend::Accelerate);
        #[cfg(not(target_vendor = "apple"))]
        assert_eq!(Backend::DEFAULT, Backend::Plain);
    }

    fn test_cands(n: usize) -> Vec<String> {
        (0..n).map(|i| (0..4).map(|j| char::from_u32(0x4e00 + ((i * 7 + j * 3) % 110) as u32).unwrap()).collect()).collect()
    }

    #[test]
    fn rerank_confident_skip_and_with_ll_equivalence() {
        let m = TinyLm::parse(&random_file(false)).unwrap();
        let owned = test_cands(4);
        let cands: Vec<&str> = owned.iter().map(String::as_str).collect();
        let chat = m.alpha_tau_delta(Profile::Chat); // (0.7, 2.0, 0.0)
        let ng = [0.0, -2.5, -3.0, -4.0];
        let key = "\u{4e01}\u{4e02}";

        assert_eq!(m.rerank(&ng, &cands, key, chat, &[], &Default::default()).unwrap(), (0, None));
        let (chosen, ll) = m.rerank_with_ll(&ng, &cands, key, chat, &[], &Default::default()).unwrap();
        let ll = ll.expect("rerank_with_ll must always score ll when n >= 2");
        assert_eq!(chosen, 0);
        assert_eq!(ll, m.ll_many(&cands.iter().map(|c| (key, *c)).collect::<Vec<_>>()).unwrap());
    }

    #[test]
    fn rerank_confident_skip_boundary() {
        let m = TinyLm::parse(&random_file(false)).unwrap();
        let owned = test_cands(3);
        let cands: Vec<&str> = owned.iter().map(String::as_str).collect();
        let chat = m.alpha_tau_delta(Profile::Chat); // tau = 2.0
        let tau = chat.1;

        assert_eq!(m.rerank(&[0.0, -tau, -5.0], &cands, "", chat, &[], &Default::default()).unwrap(), (0, None));
        let just_below = [0.0, -(tau - 1e-9), -5.0];
        let (_, ll) = m.rerank(&just_below, &cands, "", chat, &[], &Default::default()).unwrap();
        assert!(ll.is_some(), "ng[0] - ng[1] == tau - 1e-9 must run forward pass");
    }

    #[test]
    fn rerank_too_long_and_format_checked_before_skip() {
        let m = TinyLm::parse(&random_file(false)).unwrap();
        let long = "\u{4e00}".repeat(m.ctx);
        let short = "\u{4e00}\u{4e01}";
        let chat = m.alpha_tau_delta(Profile::Chat);
        let mut ng_confident = [0.0; 10];
        for (i, v) in ng_confident.iter_mut().enumerate().skip(1) {
            *v = -5.0 * i as f64;
        }

        // TooLong on candidate 0 or candidate 1 even when confident and no SINIT.
        assert_eq!(m.rerank(&ng_confident[..2], &[&long, short], "", chat, &[], &Default::default()).unwrap_err(), TinyLmError::TooLong);
        assert_eq!(m.rerank(&ng_confident[..2], &[short, &long], "", chat, &[], &Default::default()).unwrap_err(), TinyLmError::TooLong);

        // Candidate past TOPK (index TOPK) is outside `cands[..n]`, so it does not trigger TooLong and skips normally.
        let mut nine = vec![short; TOPK + 1];
        nine[TOPK] = &long;
        assert_eq!(m.rerank(&ng_confident, &nine, "", chat, &[], &Default::default()).unwrap(), (0, None));

        // Invalid params still return Format even when confident.
        for bad in [(f64::NAN, 2.0, 0.0), (f64::INFINITY, 2.0, 0.0), (0.7, f64::NAN, 0.0), (0.7, 2.0, -0.25), (0.7, 2.0, f64::NAN), (0.7, 2.0, MAX_DELTA + 1.0)] {
            assert_eq!(m.rerank(&ng_confident[..2], &[short, short], "", bad, &[], &Default::default()).unwrap_err(), TinyLmError::Format);
        }
    }

    #[test]
    fn rerank_nan_ng_and_inf_tau_do_not_skip() {
        let m = TinyLm::parse(&random_file(false)).unwrap();
        let owned = test_cands(2);
        let cands: Vec<&str> = owned.iter().map(String::as_str).collect();
        let chat = m.alpha_tau_delta(Profile::Chat);

        assert!(m.rerank(&[f64::NAN, -5.0], &cands, "", chat, &[], &Default::default()).unwrap().1.is_some());
        assert!(m.rerank(&[0.0, f64::NAN], &cands, "", chat, &[], &Default::default()).unwrap().1.is_some());
        assert!(m.rerank(&[0.0, -10.0], &cands, "", (0.7, f64::INFINITY, 0.0), &[], &Default::default()).unwrap().1.is_some());
    }

    #[test]
    fn rerank_sinit_nonzero_b_does_not_skip() {
        let m = TinyLm::parse(&random_file(false)).unwrap();
        let owned = test_cands(3);
        let cands: Vec<&str> = owned.iter().map(String::as_str).collect();
        let chat = m.alpha_tau_delta(Profile::Chat);
        let ng = [0.0, -5.0, -6.0];

        for layer in [SinitLayer::Fine, SinitLayer::Mid, SinitLayer::Coarse] {
            for b in [vec![1.0, 0.0, 0.0], vec![0.0, 1.0, 0.0], vec![0.0, 0.0, f64::NAN]] {
                let learn = Learning { layer, scale: 1.0, b };
                assert!(m.rerank(&ng, &cands, "", chat, &[], &learn).unwrap().1.is_some(), "{layer:?} must not skip with non-zero b");
            }
        }

        // Non-zero entry past `n` (cands.len()) is outside `b[..n]`, so it still skips.
        let learn_past_n = Learning { layer: SinitLayer::Fine, scale: 1.0, b: vec![0.0, 0.0, 0.0, 1.0] };
        assert_eq!(m.rerank(&ng, &cands, "", chat, &[], &learn_past_n).unwrap(), (0, None));
    }

    #[test]
    fn rerank_masked_second_candidate_does_not_change_skip() {
        let m = TinyLm::parse(&random_file(false)).unwrap();
        let owned = test_cands(3);
        let cands: Vec<&str> = owned.iter().map(String::as_str).collect();
        let chat = m.alpha_tau_delta(Profile::Chat);

        assert_eq!(m.rerank(&[0.0, -2.5, -3.0], &cands, "", chat, &[false, true, false], &Default::default()).unwrap(), (0, None));
        assert!(m.rerank(&[0.0, -1.5, -3.0], &cands, "", chat, &[false, true, false], &Default::default()).unwrap().1.is_some());
    }

    #[test]
    fn rerank_colloquial_skip_eff_tau_too_long_sinit_and_with_ll() {
        let dir = std::env::temp_dir().join(format!("shanjie-tinylm-colloq-skip-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let sjlm = dir.join("tiny.sjlm");
        let mut bytes = b"SJLM0001".to_vec();
        bytes.extend(4u32.to_le_bytes());
        bytes.extend(10u64.to_le_bytes());
        bytes.extend(4u64.to_le_bytes());
        bytes.extend(0.0f64.to_le_bytes()); // d = 0 -> unknown word at start gives -inf col
        for w in ["<s>", "</s>", "a", "b"] {
            bytes.extend((w.len() as u16).to_le_bytes());
            bytes.extend(w.as_bytes());
        }
        for u in [0u64, 0, 6, 4] {
            bytes.extend(u.to_le_bytes());
        }
        bytes.extend(2u32.to_le_bytes());
        for c in [0u32, 2] {
            bytes.extend(c.to_le_bytes());
        }
        for t in [5u64, 4] {
            bytes.extend(t.to_le_bytes());
        }
        for o in [0u32, 2, 3] {
            bytes.extend(o.to_le_bytes());
        }
        bytes.extend(3u32.to_le_bytes());
        for n in [2u32, 3, 1] {
            bytes.extend(n.to_le_bytes());
        }
        for c in [3u32, 2, 2] {
            bytes.extend(c.to_le_bytes());
        }
        std::fs::write(&sjlm, &bytes).unwrap();
        let co = Colloquial::load(&sjlm, "", &b""[..], None).unwrap();
        let _ = std::fs::remove_dir_all(&dir);

        let mut m = TinyLm::parse(&random_file(false)).unwrap();
        // Make plain.tau (4.0) differ from colloquial::cell(Standard).1 (2.0).
        m.chat[RerankLevel::Standard as usize].1 = 4.0;
        let owned = test_cands(2);
        let cands: Vec<&str> = owned.iter().map(String::as_str).collect();
        let words_finite = [vec!["a"], vec!["b"]];
        let words_nonfinite = [vec!["a"], vec!["unk"]];

        // Finite col uses cell.tau = 2.0.
        assert_eq!(m.rerank_colloquial(&[0.0, -2.0], &cands, &words_finite, "", &co, RerankLevel::Standard, &[], &Default::default()).unwrap(), (0, None));
        assert!(m.rerank_colloquial(&[0.0, -(2.0 - 1e-9)], &cands, &words_finite, "", &co, RerankLevel::Standard, &[], &Default::default()).unwrap().1.is_some());

        // Non-finite col falls back to plain.tau = 4.0.
        assert!(m.rerank_colloquial(&[0.0, -2.5], &cands, &words_nonfinite, "", &co, RerankLevel::Standard, &[], &Default::default()).unwrap().1.is_some());
        assert_eq!(m.rerank_colloquial(&[0.0, -4.0], &cands, &words_nonfinite, "", &co, RerankLevel::Standard, &[], &Default::default()).unwrap(), (0, None));

        // rerank_colloquial_with_ll forces ll even when confident.
        let (chosen, ll) = m.rerank_colloquial_with_ll(&[0.0, -2.5], &cands, &words_finite, "", &co, RerankLevel::Standard, &[], &Default::default()).unwrap();
        assert_eq!(chosen, 0);
        assert!(ll.is_some());

        // n < 2 returns (0, None); TooLong checked before skip; SINIT prevents skip.
        assert_eq!(m.rerank_colloquial(&[0.0, -5.0], &cands, &words_finite[..1], "", &co, RerankLevel::Standard, &[], &Default::default()).unwrap(), (0, None));
        let long = "\u{4e00}".repeat(m.ctx);
        assert_eq!(m.rerank_colloquial(&[0.0, -10.0], &[cands[0], &long], &words_finite, "", &co, RerankLevel::Standard, &[], &Default::default()).unwrap_err(), TinyLmError::TooLong);
        let learn = Learning { layer: SinitLayer::Fine, scale: 1.0, b: vec![0.0, 1.0] };
        assert!(m.rerank_colloquial(&[0.0, -5.0], &cands, &words_finite, "", &co, RerankLevel::Standard, &[], &learn).unwrap().1.is_some());
    }

    // ---- item C (docs/spec/semantic-rerank.md §2.2-§2.4, §10): the mapped int8 weights, the portable tiles and the B1 slabs ----

    /// SEC-05: the security review's case, wtype 0 with 64 layers, d 2048, V 3, ctx 2, is 12,891,775,076 bytes; 32-bit usize arithmetic wraps
    /// it to 6,873,188, under the cap, so a file of that many bytes would have passed the length check on i686. In checked u64 it cannot, on any
    /// target; and the largest sizes the header bounds allow fit in u64.
    #[test]
    fn parse_rejects_32bit_want_overflow() {
        let want = sjtl_len(HEADER, 0, 64, 2048, 2, 3, false).unwrap();
        assert_eq!(want, 12_891_775_076);
        assert_eq!(want % (1u64 << 32), 6_873_188, "what a 32-bit usize would have compared");
        let mut b = header_v1(64, 2048, 2, 3);
        b.resize(6_873_188, 0);
        assert_eq!(TinyLm::parse(&b).err(), Some(TinyLmError::Format));
        assert!(sjtl_len(HEADER_V6, MAX_V, MAX_LAYERS, MAX_D, MAX_CTX, MAX_V, true).is_some());
        assert_eq!(sjtl_len(HEADER, usize::MAX, 1, 1, 2, 3, false), None, "4 * usize::MAX does not fit in u64 on a 64-bit host");
        // the helper is the old formula where nothing wraps
        let (n_layer, d, ctx, v) = (2usize, 64usize, 64usize, 120usize);
        assert_eq!(sjtl_len(HEADER, v - 3, n_layer, d, ctx, v, true), Some(random_file(true).len() as u64));
        assert_eq!(sjtl_len(HEADER, v - 3, n_layer, d, ctx, v, false), Some(random_file(false).len() as u64));
    }

    /// SEC-08: a scale whose 127 multiple is finite but whose 128 multiple is not (-128 would restore to -inf) is `Format`, whether or not the row
    /// holds a -128; the largest scale that passes is f32::MAX / 128, with a -128 in its row.
    #[test]
    fn parse_rejects_i8_min_scale_overflow() {
        let good = random_file(true);
        // the first matrix (emb, V 120 x d 64) follows the vocabulary; its first row is the first d bytes, its scales follow its V * d bytes
        let (row0, scale0) = (HEADER + 4 * (120 - 3), HEADER + 4 * (120 - 3) + 120 * 64);
        let s = f32::MAX / 127.5;
        assert!((127.0f32 * s).is_finite() && !(128.0f32 * s).is_finite(), "premise");
        for (with_min, scale) in [(false, s), (true, s), (true, -s)] {
            let mut b = good.clone();
            b[scale0..scale0 + 4].copy_from_slice(&scale.to_le_bytes());
            b[row0] = if with_min { 0x80 } else { 0x7f };
            assert_eq!(TinyLm::parse(&b).err(), Some(TinyLmError::Format), "{with_min} {scale}");
        }
        let mut b = good.clone();
        b[scale0..scale0 + 4].copy_from_slice(&(f32::MAX / 128.0).to_le_bytes());
        b[row0] = 0x80;
        assert!(TinyLm::parse(&b).is_ok(), "-128 * (f32::MAX / 128) is -f32::MAX, finite");
    }

    /// SEC-08: odd sizes put the scales at offsets that are not a multiple of 4 (read with `from_le_bytes`, never cast), from a slice that does not
    /// start aligned either; the model loads (parse and the mapped `load`) and scores exactly as its `Dequant` twin.
    #[test]
    fn parse_accepts_unaligned_slice_and_odd_dims() {
        let (n_layer, n_head, d, ctx, v) = (1, 1, 3, 6, 5);
        let file = random_file_dims(Store::Int8, n_layer, n_head, d, ctx, v);
        let shifted = [&[0xaa][..], &file].concat();
        let a = TinyLm::parse(&shifted[1..]).unwrap();
        let f = TinyLm::parse(&random_file_dims(Store::Dequant, n_layer, n_head, d, ctx, v)).unwrap();
        let dir = std::env::temp_dir().join(format!("shanjie-tinylm-odd-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("odd.sjtl");
        std::fs::write(&path, &file).unwrap();
        let mapped = TinyLm::load(&path).unwrap();
        let pairs = [("\u{4e00}\u{4e01}", "\u{4e01}"), ("", "\u{4e00}\u{9fff}"), ("\u{4e01}", "\u{4e00}\u{4e00}\u{4e01}")];
        for be in [Backend::Plain, Backend::DEFAULT] {
            let want = bits(&f.ll_many_with(be, &pairs).unwrap());
            assert_eq!(bits(&a.ll_many_with(be, &pairs).unwrap()), want, "{be:?} parse");
            assert_eq!(bits(&mapped.ll_many_with(be, &pairs).unwrap()), want, "{be:?} load");
        }
        drop(mapped); // Windows refuses to delete a mapped file
        std::fs::remove_dir_all(&dir).unwrap();
    }

    /// SEC-10: `gemm_q8` checks every length in release, the scales included (one per stored row of b: k, or n when transposed), with
    /// `checked_mul`, before any read.
    #[test]
    fn gemm_q8_rejects_short_scale_and_overflow() {
        let run = |a: Vec<f32>, bq: Vec<u8>, sc: Vec<f32>, c: usize, dims: (usize, usize, usize), trans_b: bool| {
            std::panic::catch_unwind(move || {
                let mut c = vec![0f32; c];
                gemm_q8(Kernel::Scalar, &a, &bq, &sc, &mut c, dims, trans_b)
            })
            .is_ok()
        };
        assert!(run(vec![0.0; 6], vec![0; 6], vec![1.0; 3], 4, (2, 3, 2), false), "control: k scales");
        assert!(run(vec![0.0; 6], vec![0; 6], vec![1.0; 2], 4, (2, 3, 2), true), "control: n scales");
        assert!(!run(vec![0.0; 6], vec![0; 6], vec![1.0; 2], 4, (2, 3, 2), false), "short scales");
        assert!(!run(vec![0.0; 6], vec![0; 6], vec![1.0; 3], 4, (2, 3, 2), true), "k scales where n are needed");
        assert!(!run(vec![0.0; 6], vec![0; 5], vec![1.0; 3], 4, (2, 3, 2), false), "short b");
        assert!(!run(vec![], vec![], vec![], 0, (usize::MAX / 2 + 1, 2, 0), false), "m * k wraps to 0");
    }

    /// SEC-10: the portable int8 product keeps its tile on the heap: a model at `MAX_D` scores on a thread with a 512 KiB stack (the GCD worker
    /// size), on both paths.
    #[test]
    fn plain_q8_fits_512kib_worker_stack() {
        let m = TinyLm::parse(&random_file_dims(Store::Int8, 1, 1, MAX_D, 2, 3)).unwrap();
        let got = std::thread::Builder::new()
            .stack_size(512 * 1024)
            .spawn(move || [Backend::Plain, Backend::DEFAULT].map(|be| m.ll_many_with(be, &[("", "\u{4e00}")]).unwrap()[0]))
            .unwrap()
            .join()
            .unwrap();
        assert!(got.iter().all(|x| x.is_finite() && *x < 0.0), "{got:?}");
    }

    /// SEC-11: a batch with no scored position (every candidate empty) is all 0, as before, without running the model: no slab is pinned.
    #[test]
    fn ll_many_empty_candidates_short_circuits() {
        let m = TinyLm::parse(&random_file(true)).unwrap();
        for be in [Backend::Plain, Backend::DEFAULT] {
            assert_eq!(m.ll_many_with(be, &[("\u{4e01}\u{4e02}", ""), ("", "")]).unwrap(), [0.0, 0.0]);
        }
        #[cfg(target_vendor = "apple")]
        assert_eq!(m.purgeable_stats().pin_ok, 0, "no slab was pinned");
        // a batch with one scored candidate still scores it, and the empty one stays 0
        let mixed = m.ll_many(&[("", ""), ("", "\u{4e00}")]).unwrap();
        assert_eq!(mixed[0], 0.0);
        assert!(mixed[1] < 0.0);
    }

    /// docs/spec/semantic-rerank.md §2.3: `load_paired` hashes the mapping it parses; on the shipped files that is the hash of the file on disk and the
    /// one the colloquial term pairs with (`colloquial::PAIRED_TINYLM_SHA256`). Needs data/lm (CI fetches it; see data/*.sha256).
    #[test]
    fn load_paired_preserves_colloquial_pairing() {
        let lm_dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../data/lm");
        let lm = Lm::load(&lm_dir.join("bigram.sjlm")).unwrap();
        let path = lm_dir.join("tinylm.sjtl");
        let m = TinyLm::load_paired(&path, &lm).unwrap();
        assert_eq!(m.file_sha256(), colloquial::PAIRED_TINYLM_SHA256);
        assert_eq!(m.file_sha256(), crate::eval::sha256_hex(&std::fs::read(&path).unwrap()));
        assert!(colloquial::applies(Profile::Chat, &m));
        assert!(TinyLm::load(&path).unwrap().file_sha256().is_empty(), "`load` pairs with nothing");
    }

    /// The slab of every int8 matrix of `m`.
    #[cfg(target_vendor = "apple")]
    fn slabs(m: &TinyLm) -> Vec<&purgeable::PurgeableSlab> {
        m.slabs().collect()
    }

    /// SEC-03: a slab the system emptied (forced here; only an unpinned one, as the system only empties volatile memory) is restored on its next pin;
    /// the scores stay bit for bit those before, and the B1 judge still passes (one restore per emptied slab counted).
    #[cfg(target_vendor = "apple")]
    #[test]
    fn b1_purge_empty_recovers_bit_for_bit() {
        let m = TinyLm::parse(&random_file(true)).unwrap();
        let items = test_items();
        let pairs = as_pairs(&items);
        let first = bits(&m.ll_many(&pairs).unwrap());
        let s = m.purgeable_stats();
        assert_eq!(s.slab_count, 1 + 4 * 2);
        s.check().unwrap();
        assert_eq!(s.empty_rebuilds, 0);
        assert!(slabs(&m).iter().all(|s| s.force_empty()), "every slab emptied");
        assert_eq!(bits(&m.ll_many(&pairs).unwrap()), first);
        let s = m.purgeable_stats();
        assert_eq!(s.empty_rebuilds, s.slab_count);
        s.check().unwrap();
    }

    /// SEC-03: 6 threads scoring through one model while another empties every unpinned slab as fast as it can: every result is bit for bit the
    /// single-thread one, nothing falls back, and slabs really were emptied and restored.
    #[cfg(target_vendor = "apple")]
    #[test]
    fn b1_concurrent_threads_and_purge_stress() {
        use std::sync::atomic::{AtomicBool, Ordering};
        let m = TinyLm::parse(&random_file(true)).unwrap();
        let items = test_items();
        let pairs = as_pairs(&items);
        let want = bits(&m.ll_many(&pairs).unwrap());
        let stop = AtomicBool::new(false);
        let emptied = std::thread::scope(|sc| {
            let purger = sc.spawn(|| {
                let mut n = 0usize;
                while !stop.load(Ordering::Relaxed) {
                    n += slabs(&m).iter().filter(|s| s.force_empty()).count();
                    std::thread::yield_now();
                }
                n
            });
            let workers: Vec<_> = (0..6)
                .map(|_| {
                    sc.spawn(|| {
                        for _ in 0..15 {
                            assert_eq!(bits(&m.ll_many(&pairs).unwrap()), want);
                        }
                    })
                })
                .collect();
            let ok = workers.into_iter().all(|w| w.join().is_ok());
            stop.store(true, Ordering::Relaxed);
            let n = purger.join().unwrap();
            assert!(ok, "a worker saw other scores");
            n
        });
        let s = m.purgeable_stats();
        s.check().unwrap();
        assert!(emptied > 0 && s.empty_rebuilds > 0, "emptied {emptied}, rebuilt {}", s.empty_rebuilds);
    }

    /// SEC-03: a panic out of a restore (half the region already overwritten) poisons that slab's lock; it is recovered, the slab restores
    /// again on the next pin, and the scores are bit for bit those of a model that never panicked.
    #[cfg(target_vendor = "apple")]
    #[test]
    fn b1_populate_panic_unwind_recovers() {
        let items = test_items();
        let pairs = as_pairs(&items);
        let want = bits(&TinyLm::parse(&random_file(true)).unwrap().ll_many(&pairs).unwrap());
        let m = TinyLm::parse(&random_file(true)).unwrap();
        for at in [0, 3] {
            slabs(&m)[at].inject(|i| i.panic_restore = true);
            let r = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| m.ll_many(&pairs)));
            assert!(r.is_err(), "slab {at}: the restore panicked");
            slabs(&m)[at].inject(|i| i.panic_restore = false);
            assert_eq!(bits(&m.ll_many(&pairs).unwrap()), want, "slab {at}");
            // the next round finds slab `at` populated again: empty it so the injected panic is reached on the next one
            slabs(&m).iter().for_each(|s| assert!(s.force_empty()));
        }
        assert_eq!(m.purgeable_stats().fallbacks, 0);
    }

    /// SEC-03, SEC-12: when the kernel refuses (`vm_allocate`, or `vm_purgable_control` on a later pin), the pin is refused, the product runs on the
    /// portable path (the scores stay within the 1e-5 of docs/spec/semantic-rerank.md §2.4), `fallbacks` counts it, and the B1 judge rejects the
    /// stats; synthetic stats with one fallback are rejected as well, and the same stats without it pass.
    #[cfg(target_vendor = "apple")]
    #[test]
    fn b1_kernel_error_falls_back_to_plain() {
        let items = test_items();
        let pairs = as_pairs(&items);
        let want = TinyLm::parse(&random_file(true)).unwrap().ll_many(&pairs).unwrap();
        let close = |got: &[f64]| got.iter().zip(&want).all(|(x, y)| (x - y).abs() < 1e-5);
        let m = TinyLm::parse(&random_file(true)).unwrap();
        slabs(&m).iter().for_each(|s| s.inject(|i| i.fail_alloc = true));
        assert!(close(&m.ll_many(&pairs).unwrap()));
        let s = m.purgeable_stats();
        assert!(s.fallbacks > 0 && s.alloc_ok == 0 && s.pin_ok == 0, "{s:?}");
        assert_eq!(s.check(), Err("fallbacks"));
        slabs(&m).iter().for_each(|s| s.inject(|i| i.fail_alloc = false));
        assert!(close(&m.ll_many(&pairs).unwrap()));
        let before = m.purgeable_stats().fallbacks;
        slabs(&m).iter().for_each(|s| s.inject(|i| i.fail_control = true));
        assert!(close(&m.ll_many(&pairs).unwrap()));
        let s = m.purgeable_stats();
        assert!(s.fallbacks > before, "{s:?}");
        assert_eq!(s.check(), Err("fallbacks"));
        let synthetic =
            PurgeableStats { slab_count: 33, alloc_ok: 33, pin_ok: 66, volatile_ok: 66, empty_rebuilds: 0, fallbacks: 1, currently_volatile: 33 };
        assert_eq!(synthetic.check(), Err("fallbacks"));
        assert_eq!(PurgeableStats { fallbacks: 0, ..synthetic }.check(), Ok(()));
        assert_eq!(PurgeableStats { currently_volatile: 32, fallbacks: 0, ..synthetic }.check(), Err("currently_volatile"));
        assert_eq!(PurgeableStats::default().check(), Err("slab_count"));
    }
}
