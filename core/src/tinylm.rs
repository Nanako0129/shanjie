//! S7c-1: the small character language model (S7a) in Rust: the SJTL0001 / SJTL0002 / SJTL0003 / SJTL0004 loader, the forward
//! pass, LL and `pick` (docs/contracts/s7c-core-tinylm.md section 2; SJTL0002, the per-profile delta, is
//! docs/contracts/s7c2c-delta.md section 2.1; SJTL0003, a conservative and an aggressive (alpha, tau, delta) per profile beside
//! the standard one, is docs/contracts/rerank-strength.md section 2.1; SJTL0004, the SHA-256 of the Kneser-Ney side file the
//! parameters were tuned with, is docs/contracts/model-v6.md section 2.7).
//! S7c-2a (docs/contracts/s7c2a-core-rerank.md) reaches it from the engine's rerank
//! jobs and the C ABI, so the loader is hardened for in-process use (section 2.6): a size cap, a regular-file check, header
//! bounds and finite restored weights.
//!
//! The model is `tinylm.NumpyLM` of experiments/s7a-tinylm in f32 (pre-LN decoder, tied embedding, learned positions, GELU tanh,
//! causal attention); `ll_many` is `tinylm.ll_many`. The file is written only by experiments/s7a-tinylm/export_sjtl.py, whose
//! docstring is the format (and the tensor order, which `parse` repeats).
//! R2: errors carry a kind only, never text.

use crate::colloquial::{self, Colloquial, ReadingChannel};
use crate::learn::SENTINEL;
use crate::lm::{Lm, Profile};
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
/// model-v6 section 2.7: version 4 is version 3's 212 header bytes (magic and version field changed) plus, at 212, the 32-byte
/// SHA-256 of the Kneser-Ney side file (kn.sjkn) alpha, tau and delta were tuned with; all zero = tuned without one. Versions
/// 1 to 3 count as all zero.
const MAGIC_V4: &[u8; 8] = b"SJTL0004";
const HEADER_V4: usize = 244;
const VERSION_V4: u32 = 4;
/// S7c-2c section 2.1: a guard on delta, far above the S7e grid (at most 4).
const MAX_DELTA: f64 = 1000.0;
const PAD: u32 = 0;
const UNK: u32 = 1;
const BOS: u32 = 2;
/// Candidates per forward pass (right-padded with PAD; the causal mask keeps the padding out of the real positions).
pub const BATCH: usize = 8;
/// How many n-best candidates the model reranks (S7a: the first 8).
pub const TOPK: usize = 8;
/// S7c-2a section 2.6 (F3): the largest `.sjtl` `load` reads. The shipped M model in int8 is about 15 MB.
pub const MAX_FILE: u64 = 64 << 20;
// S7c-2a section 2.6 (F4): header bounds of the shipped model's order of magnitude, so that the forward pass's allocations are
// bounded too. The M model (S7c-1, docs/contracts/s7c-core-tinylm.md) is V 9,717 (9,714 characters + PAD, UNK, BOS), ctx 64, d 384, 6 layers.
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
    /// `load_paired`: the Kneser-Ney side file of the n-gram model is not the one the file was tuned with (model-v6 section 2.7).
    KneserNey(KnPairing),
}

/// Which way the Kneser-Ney side file and the `.sjtl` disagree (model-v6 section 7, P4-9: each needs another remedy).
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

/// Which of a profile's (alpha, tau, delta) the rerank uses (docs/contracts/rerank-strength.md section 2.2). The codes are the C
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

/// The one check of every (alpha, tau, delta) in the header (rerank-strength section 9, P2-4): alpha finite (a NaN alpha would pick
/// nothing sensible), tau not NaN (it may be infinite: no threshold), delta in [0, `MAX_DELTA`] (the range test also rejects NaN,
/// the infinities and negatives; -0.0 passes and compares as 0 in `pick`).
fn valid_params((alpha, tau, delta): (f64, f64, f64)) -> bool {
    alpha.is_finite() && !tau.is_nan() && (0.0..=MAX_DELTA).contains(&delta)
}

/// How the matrix products and the tanh / exp vectors are computed. `Plain` is the portable path; `Accelerate` is the one used on macOS.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Backend {
    Plain,
    #[cfg(target_os = "macos")]
    Accelerate,
}

impl Backend {
    #[cfg(target_os = "macos")]
    pub const DEFAULT: Backend = Backend::Accelerate;
    #[cfg(not(target_os = "macos"))]
    pub const DEFAULT: Backend = Backend::Plain;
}

#[cfg(target_os = "macos")]
#[link(name = "Accelerate", kind = "framework")]
extern "C" {
    fn cblas_sgemm(
        order: i32, ta: i32, tb: i32, m: i32, n: i32, k: i32, alpha: f32, a: *const f32, lda: i32, b: *const f32, ldb: i32, beta: f32,
        c: *mut f32, ldc: i32,
    );
    fn vvtanhf(y: *mut f32, x: *const f32, n: *const i32);
    fn vvexpf(y: *mut f32, x: *const f32, n: *const i32);
}

/// c (m x n) += a (m x k) . b, with b (k x n), or b (n x k) transposed when `trans_b`. All row-major.
pub(crate) fn gemm(be: Backend, a: &[f32], b: &[f32], c: &mut [f32], (m, k, n): (usize, usize, usize), trans_b: bool) {
    // Checked in release too (F6): the Accelerate call reads and writes through raw pointers with these lengths.
    assert!(a.len() == m * k && b.len() == k * n && c.len() == m * n, "gemm: matrix lengths do not match");
    match be {
        #[cfg(target_os = "macos")]
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
        Backend::Plain if trans_b => {
            for i in 0..m {
                for j in 0..n {
                    c[i * n + j] += a[i * k..(i + 1) * k].iter().zip(&b[j * k..(j + 1) * k]).map(|(x, y)| x * y).sum::<f32>();
                }
            }
        }
        Backend::Plain => {
            for i in 0..m {
                for p in 0..k {
                    let av = a[i * k + p];
                    for (cj, bj) in c[i * n..(i + 1) * n].iter_mut().zip(&b[p * n..(p + 1) * n]) {
                        *cj += av * bj;
                    }
                }
            }
        }
    }
}

/// y = tanh(x), elementwise (out of place: vForce is not documented to allow y == x).
fn tanh_into(be: Backend, x: &[f32], y: &mut [f32]) {
    assert!(x.len() == y.len() && x.len() < i32::MAX as usize, "tanh: lengths do not match");
    match be {
        #[cfg(target_os = "macos")]
        Backend::Accelerate => unsafe { vvtanhf(y.as_mut_ptr(), x.as_ptr(), &(x.len() as i32)) },
        Backend::Plain => y.iter_mut().zip(x).for_each(|(o, v)| *o = v.tanh()),
    }
}

/// y = exp(x), elementwise, out of place.
pub(crate) fn exp_into(be: Backend, x: &[f32], y: &mut [f32]) {
    assert!(x.len() == y.len() && x.len() < i32::MAX as usize, "exp: lengths do not match");
    match be {
        #[cfg(target_os = "macos")]
        Backend::Accelerate => unsafe { vvexpf(y.as_mut_ptr(), x.as_ptr(), &(x.len() as i32)) },
        Backend::Plain => y.iter_mut().zip(x).for_each(|(o, v)| *o = v.exp()),
    }
}

struct Layer {
    ln1_w: Vec<f32>,
    ln1_b: Vec<f32>,
    qkv_w: Vec<f32>,
    qkv_b: Vec<f32>,
    proj_w: Vec<f32>,
    proj_b: Vec<f32>,
    ln2_w: Vec<f32>,
    ln2_b: Vec<f32>,
    fc1_w: Vec<f32>,
    fc1_b: Vec<f32>,
    fc2_w: Vec<f32>,
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
    ids: HashMap<char, u32>,
    emb: Vec<f32>,
    pos: Vec<f32>,
    lnf_w: Vec<f32>,
    lnf_b: Vec<f32>,
    layers: Vec<Layer>,
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
    /// A 2-D matrix: f32 as is; int8 as `q as f32 * scale` per row (the f32 product int8check.dequant takes).
    fn mat(&mut self, rows: usize, cols: usize) -> Vec<f32> {
        if !self.int8 {
            return self.f32s(rows * cols);
        }
        let q = &self.b[self.at..self.at + rows * cols];
        self.at += rows * cols;
        let scale = self.f32s(rows);
        let mut out = Vec::with_capacity(rows * cols);
        for (row, s) in q.chunks_exact(cols).zip(&scale) {
            out.extend(row.iter().map(|&x| x as i8 as f32 * s));
        }
        // F5: finite scales can still restore to inf (127 * f32::MAX).
        self.ok &= out.iter().all(|x| x.is_finite());
        out
    }
}

/// `O_NONBLOCK`: opening a FIFO for reading must not wait for a writer (it is rejected right after, as not a regular file).
#[cfg(target_os = "linux")]
pub(crate) const O_NONBLOCK: i32 = 0o4000;
#[cfg(all(unix, not(target_os = "linux")))]
pub(crate) const O_NONBLOCK: i32 = 0x0004; // macOS and the BSDs

/// S7c-2a section 2.6 (F3): opens first, then checks the opened handle (a regular file of at most `max` bytes) and reads at
/// most the cap from that same handle, so the file checked is the file read. Also the zhen-mixed word list's reader
/// (`Engine::load_english`, with its own cap). `Io`: unopenable or not a regular file; `Format`: over the cap.
pub(crate) fn read_bounded(path: &Path, max: u64) -> Result<Vec<u8>, TinyLmError> {
    use std::io::Read;
    let mut opts = std::fs::OpenOptions::new();
    opts.read(true);
    #[cfg(unix)]
    std::os::unix::fs::OpenOptionsExt::custom_flags(&mut opts, O_NONBLOCK);
    let file = opts.open(path).map_err(|_| TinyLmError::Io)?;
    let meta = file.metadata().map_err(|_| TinyLmError::Io)?;
    if !meta.is_file() {
        return Err(TinyLmError::Io);
    }
    if meta.len() > max {
        return Err(TinyLmError::Format);
    }
    let mut bytes = Vec::with_capacity(meta.len() as usize);
    file.take(max + 1).read_to_end(&mut bytes).map_err(|_| TinyLmError::Io)?;
    if bytes.len() as u64 > max {
        return Err(TinyLmError::Format);
    }
    Ok(bytes)
}

impl TinyLm {
    /// Reads only a regular file of at most `MAX_FILE` bytes (`read_bounded`).
    pub fn load(path: &Path) -> Result<TinyLm, TinyLmError> {
        TinyLm::parse(&read_bounded(path, MAX_FILE)?)
    }

    /// The one loader of the engine and the evaluation CLI (S7c-2a): `load`'s checks, then the pairing with `lm` — the n-gram
    /// model hash the file records must be `lm`'s, and the Kneser-Ney side file hash it records (model-v6 section 2.7: none for
    /// versions 1 to 3 and for an all-zero version 4 field) must be the one `lm` has loaded, all 32 bytes, or none when `lm` has none ("loaded" is about the file beside the model, not whether the formal profile scores with it).
    /// A pairing check only, not an integrity check of the `.sjtl`. `file_sha256` is the hash of exactly the bytes parsed, made here and the bytes
    /// dropped on return (so a table loaded later never finds them alive). Measured: release build, the real tinylm.sjtl (14,638,352 bytes), 7 runs:
    /// the core's software SHA-256 takes 35 to 52 ms (median about 37 ms), `parse` 5 to 9 ms.
    pub fn load_paired(path: &Path, lm: &Lm) -> Result<TinyLm, TinyLmError> {
        let bytes = read_bounded(path, MAX_FILE)?;
        let mut model = TinyLm::parse(&bytes)?;
        model.file_sha = crate::eval::sha256_hex(&bytes);
        drop(bytes);
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

    /// Every check of the format; a bad file is an error, never a partial model.
    pub fn parse(bytes: &[u8]) -> Result<TinyLm, TinyLmError> {
        let bad = TinyLmError::Format;
        if bytes.len() < 12 {
            return Err(bad);
        }
        let u32_at = |at: usize| u32::from_le_bytes(bytes[at..at + 4].try_into().unwrap());
        let f64_at = |at: usize| f64::from_le_bytes(bytes[at..at + 8].try_into().unwrap());
        // S7c-2c section 2.1, rerank-strength section 9 (P2-2), model-v6 section 7 (P2-1): magic and version field together
        // choose, in this one match, the header length (the one length the vocabulary start, the tensor start and `want` use),
        // whether the deltas are there, whether the levels are and whether the side file hash is; the file must hold the whole
        // header before any field past the version is read.
        let (header, deltas, levels, kn) = match (&bytes[..8], u32_at(8)) {
            (m, VERSION) if m == MAGIC => (HEADER, false, false, false),
            (m, VERSION_V2) if m == MAGIC_V2 => (HEADER_V2, true, false, false),
            (m, VERSION_V3) if m == MAGIC_V3 => (HEADER_V3, true, true, false),
            (m, VERSION_V4) if m == MAGIC_V4 => (HEADER_V4, true, true, true),
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
        let int8 = wtype == 1;
        let mat = |r: usize, c: usize| if int8 { r * c + 4 * r } else { 4 * r * c };
        let layer_bytes = 4 * 4 * d + mat(d, 3 * d) + 4 * 3 * d + mat(d, d) + 4 * d + mat(d, 4 * d) + 4 * 4 * d + mat(4 * d, d) + 4 * d;
        let want = header + 4 * (v - 3) + mat(v, d) + mat(ctx, d) + 4 * 2 * d + n_layer * layer_bytes;
        if bytes.len() != want {
            return Err(bad);
        }
        let mut ids = HashMap::new();
        for i in 0..v - 3 {
            let c = char::from_u32(u32_at(header + 4 * i)).ok_or(bad)?;
            if ids.insert(c, i as u32 + 3).is_some() {
                return Err(bad);
            }
        }
        let mut r = Cursor { b: bytes, at: header + 4 * (v - 3), int8, ok: true };
        // The tensor order of export_sjtl.py: emb, pos, lnf_w, lnf_b, then for each layer ln1_w, ln1_b, qkv_w, qkv_b, proj_w, proj_b,
        // ln2_w, ln2_b, fc1_w, fc1_b, fc2_w, fc2_b (tinylm.LAYER_KEYS). Struct fields evaluate in source order.
        let (emb, pos, lnf_w, lnf_b) = (r.mat(v, d), r.mat(ctx, d), r.f32s(d), r.f32s(d));
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
        Ok(TinyLm {
            n_head,
            d,
            ctx,
            chat,
            formal,
            levels,
            ng_sha: bytes[68..100].try_into().unwrap(),
            file_sha: String::new(),
            kn_sha,
            ids,
            emb,
            pos,
            lnf_w,
            lnf_b,
            layers,
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

    /// Whether the file is version 3 or 4, i.e. has its own conservative and aggressive triples (rerank-strength section 2.2).
    pub fn has_levels(&self) -> bool {
        self.levels
    }

    /// The SHA-256 of the Kneser-Ney side file the parameters were tuned with (version 4); `None` when tuned without one (an
    /// all-zero field, or a version 1 to 3 file).
    pub fn kn_sha256(&self) -> Option<[u8; 32]> {
        self.kn_sha
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
    /// k = the engine's context key (`learn::context_key`; its `SENTINEL`, no Han character before the cursor, is the empty k as
    /// in tinylm.row_key), the caller's (alpha, tau, delta) (`params` of the profile and level), then `pick`. Returns the chosen index and, when the model ran (two or more
    /// candidates), the LLs. A candidate longer than ctx - 1 characters is `TooLong`; the caller decides to keep the first.
    pub fn rerank(&self, ng: &[f64], cands: &[&str], key: &str, params: (f64, f64, f64)) -> Result<(usize, Option<Vec<f64>>), TinyLmError> {
        // The header's check, for every caller: a NaN alpha or a negative delta from outside the file must not reach `pick`.
        if !valid_params(params) {
            return Err(TinyLmError::Format);
        }
        let n = cands.len().min(ng.len()).min(TOPK);
        let Some(ll) = self.rerank_ll(&cands[..n], key)? else { return Ok((0, None)) };
        let (alpha, tau, delta) = params;
        Ok((pick(&ng[..n], &ll, alpha, tau, delta), Some(ll)))
    }

    /// What `rerank` and `rerank_colloquial` share: the LLs of the (already cut to `TOPK`) candidates under the context key (`learn::context_key`;
    /// its `SENTINEL` is the empty k, as in tinylm.row_key), or `None` when there are fewer than two and the model does not run.
    fn rerank_ll(&self, cands: &[&str], key: &str) -> Result<Option<Vec<f64>>, TinyLmError> {
        if cands.len() < 2 {
            return Ok(None);
        }
        let k = if key == SENTINEL { "" } else { key };
        let items: Vec<(&str, &str)> = cands.iter().map(|c| (k, *c)).collect();
        self.ll_many(&items).map(Some)
    }

    /// `rerank` with the colloquial term (docs/contracts/colloquial-term.md section 2.2): the same first `TOPK` candidates and `ll_many`,
    /// then col_i = `co.col(words[i])` for the word split of each candidate, ll_eff = `colloquial::fuse(ll, col)`, and `pick` with the
    /// registered cell's alpha, tau and delta (`colloquial::ALPHA` ...), not the file's. With `rc` (docs/contracts/reading-channel.md) ll_eff
    /// also gains (NU / ALPHA) * (ch_i - ch_0), ch_i = `colloquial::channel_of` of the stretch's syllables over the candidate's words. A col that
    /// is not finite drops both terms for this call: the plain `pick` with the file's chat standard triple (`colloquial::guarded_pick`). The
    /// engine and the evaluation CLI both call this one function. Returns the chosen index and the raw LLs, as `rerank` does.
    pub fn rerank_colloquial(
        &self, ng: &[f64], cands: &[&str], words: &[Vec<&str>], key: &str, co: &Colloquial, rc: Option<ReadingChannel>,
    ) -> Result<(usize, Option<Vec<f64>>), TinyLmError> {
        let n = cands.len().min(ng.len()).min(words.len()).min(TOPK);
        let Some(ll) = self.rerank_ll(&cands[..n], key)? else { return Ok((0, None)) };
        let col: Vec<f64> = words[..n].iter().map(|w| co.col(w)).collect();
        let ch: Option<Vec<f64>> = rc.map(|rc| words[..n].iter().map(|w| colloquial::channel_of(rc.lex, rc.syls, w)).collect());
        let plain = self.params(Profile::Chat, RerankLevel::Standard);
        Ok((colloquial::guarded_pick(&ng[..n], &ll, &col, ch.as_deref(), plain), Some(ll)))
    }

    /// LL of each (k, c): the sum over the characters of c of log10 P(c_i | [BOS] + k + c_<i), as `tinylm.ll_many`
    /// (characters outside the table are UNK; the oldest characters of k are dropped when [BOS] + k + c exceeds the context;
    /// a c longer than ctx - 1 characters is an error; an empty c is 0).
    pub fn ll_many(&self, items: &[(&str, &str)]) -> Result<Vec<f64>, TinyLmError> {
        self.ll_many_with(Backend::DEFAULT, items)
    }

    pub fn ll_many_with(&self, be: Backend, items: &[(&str, &str)]) -> Result<Vec<f64>, TinyLmError> {
        let enc = |s: &str| s.chars().map(|c| self.ids.get(&c).copied().unwrap_or(UNK)).collect::<Vec<u32>>();
        let mut out = Vec::with_capacity(items.len());
        for chunk in items.chunks(BATCH) {
            // ([BOS] + k + c, index of the first token of c)
            let mut seqs = Vec::with_capacity(chunk.len());
            for (k, c) in chunk {
                let (k, c) = (enc(k), enc(c));
                if c.len() > self.ctx - 1 {
                    return Err(TinyLmError::TooLong);
                }
                let k = &k[k.len().saturating_sub(self.ctx - 1 - c.len())..];
                seqs.push(([&[BOS], k, &c[..]].concat(), 1 + k.len()));
            }
            out.extend(self.ll_batch(be, &seqs));
        }
        Ok(out)
    }

    /// One right-padded batch: the hidden states, then the logits of only the positions that predict a token of c.
    fn ll_batch(&self, be: Backend, seqs: &[(Vec<u32>, usize)]) -> Vec<f64> {
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
        let v = self.emb.len() / d;
        let mut logits = vec![0f32; targets.len() * v];
        gemm(be, &rows, &self.emb, &mut logits, (targets.len(), d, v), true);
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
        for (r, &id) in ids.iter().enumerate() {
            let (e, p) = (&self.emb[id as usize * d..][..d], &self.pos[r % t * d..][..d]);
            x[r * d..(r + 1) * d].iter_mut().zip(e.iter().zip(p)).for_each(|(o, (a, b))| *o = a + b);
        }
        let scale = (dh as f32).sqrt();
        let mut s = vec![0f32; t];
        for l in &self.layers {
            let mut qkv = bias_rows(&l.qkv_b, rows);
            gemm(be, &layer_norm(&x, &l.ln1_w, Some(&l.ln1_b)), &l.qkv_w, &mut qkv, (rows, d, 3 * d), false);
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
            gemm(be, &att, &l.proj_w, &mut proj, (rows, d, d), false);
            x.iter_mut().zip(&proj).for_each(|(a, b)| *a += b);
            let mut f = bias_rows(&l.fc1_b, rows);
            gemm(be, &layer_norm(&x, &l.ln2_w, Some(&l.ln2_b)), &l.fc1_w, &mut f, (rows, d, 4 * d), false);
            gelu(be, &mut f);
            let mut o = bias_rows(&l.fc2_b, rows);
            gemm(be, &f, &l.fc2_w, &mut o, (rows, 4 * d, d), false);
            x.iter_mut().zip(&o).for_each(|(a, b)| *a += b);
        }
        layer_norm(&x, &self.lnf_w, Some(&self.lnf_b))
    }
}

/// `rows` copies of `b` (the bias; the product is then accumulated onto it).
fn bias_rows(b: &[f32], rows: usize) -> Vec<f32> {
    b.iter().cloned().cycle().take(rows * b.len()).collect()
}

/// LayerNorm over the last axis, eps 1e-5, as numpy: (x - mean) / sqrt(var + eps) * w + b (no `+ b` when `b` is `None`; oc.rs).
pub(crate) fn layer_norm(x: &[f32], w: &[f32], b: Option<&[f32]>) -> Vec<f32> {
    let d = w.len();
    let mut out = Vec::with_capacity(x.len());
    for row in x.chunks_exact(d) {
        let mu = row.iter().map(|&v| v as f64).sum::<f64>() / d as f64;
        let var = row.iter().map(|&v| (v as f64 - mu).powi(2)).sum::<f64>() / d as f64;
        let inv = 1.0 / (var + 1e-5).sqrt();
        out.extend(row.iter().enumerate().map(|(j, &v)| {
            let y = ((v as f64 - mu) * inv) as f32 * w[j];
            b.map_or(y, |b| y + b[j])
        }));
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

/// The S7a fusion with the S7e gate, experiments/s7a-tinylm/fusegrid.py `pick` with its `delta`: the index of the chosen
/// candidate. Fewer than two candidates, or an n-gram margin (first minus second) of at least `tau`, keeps the first; otherwise
/// j = the highest s = `ng + alpha * ll` (ties to the better n-gram rank), and j replaces the first only when `s[j] - s[0] >= delta`.
/// Delta 0 is the S7a rule.
pub fn pick(ng: &[f64], ll: &[f64], alpha: f64, tau: f64, delta: f64) -> usize {
    if ng.len() < 2 || ng[0] - ng[1] >= tau {
        return 0;
    }
    let s0 = ng[0] + alpha * ll[0];
    let (mut best, mut best_s) = (0, s0);
    for i in 1..ng.len() {
        let s = ng[i] + alpha * ll[i];
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

    /// A random matrix as the file stores it (int8 rows plus f32 scales, or f32).
    fn put_mat(b: &mut Vec<u8>, st: &mut u64, int8: bool, r: usize, c: usize) {
        let w: Vec<f32> = (0..r * c).map(|_| rnd(st, 0.3)).collect();
        if int8 {
            let scales: Vec<f32> = w.chunks(c).map(|row| row.iter().fold(0f32, |m, x| m.max(x.abs())).max(1e-12) / 127.0).collect();
            for (row, s) in w.chunks(c).zip(&scales) {
                b.extend(row.iter().map(|x| (x / s).round() as i8 as u8));
            }
            scales.iter().for_each(|s| b.extend_from_slice(&s.to_le_bytes()));
        } else {
            w.iter().for_each(|x| b.extend_from_slice(&x.to_le_bytes()));
        }
    }

    /// A vector of `n` values near `g` (LayerNorm gains near 1, the rest near 0).
    fn put_vec(b: &mut Vec<u8>, st: &mut u64, n: usize, g: f32) {
        (0..n).for_each(|_| b.extend_from_slice(&(g + rnd(st, 0.1)).to_le_bytes()));
    }

    /// A random SJTL0001 file written the way export_sjtl.py lays it out (d = 64, 2 layers, 4 heads, V = 120): wide enough for
    /// the two matrix paths to differ in summation order.
    fn random_file(int8: bool) -> Vec<u8> {
        let (n_layer, n_head, d, ctx, v) = (2usize, 4usize, 64usize, 64usize, 120usize);
        let mut st = 0x2545_f491_4f6c_dd1du64;
        let mut b = Vec::new();
        b.extend_from_slice(MAGIC);
        for x in [VERSION, int8 as u32, n_layer as u32, n_head as u32, d as u32, ctx as u32, v as u32] {
            b.extend_from_slice(&x.to_le_bytes());
        }
        for x in [0.7f64, 2.0, 0.7, 2.0] {
            b.extend_from_slice(&x.to_le_bytes());
        }
        b.extend_from_slice(&[7u8; 32]);
        (0..v - 3).for_each(|i| b.extend_from_slice(&(0x4e00 + i as u32).to_le_bytes()));
        put_mat(&mut b, &mut st, int8, v, d);
        put_mat(&mut b, &mut st, int8, ctx, d);
        put_vec(&mut b, &mut st, d, 1.0);
        put_vec(&mut b, &mut st, d, 0.0);
        for _ in 0..n_layer {
            put_vec(&mut b, &mut st, d, 1.0);
            put_vec(&mut b, &mut st, d, 0.0);
            put_mat(&mut b, &mut st, int8, d, 3 * d);
            put_vec(&mut b, &mut st, 3 * d, 0.0);
            put_mat(&mut b, &mut st, int8, d, d);
            put_vec(&mut b, &mut st, d, 0.0);
            put_vec(&mut b, &mut st, d, 1.0);
            put_vec(&mut b, &mut st, d, 0.0);
            put_mat(&mut b, &mut st, int8, d, 4 * d);
            put_vec(&mut b, &mut st, 4 * d, 0.0);
            put_mat(&mut b, &mut st, int8, 4 * d, d);
            put_vec(&mut b, &mut st, d, 0.0);
        }
        b
    }

    /// A file of exactly the length `parse` expects for these sizes, all tensors zero (finite), f32.
    fn zero_file(n_layer: usize, d: usize, ctx: usize, v: usize) -> Vec<u8> {
        let mut b = Vec::new();
        b.extend_from_slice(MAGIC);
        for x in [VERSION, 0, n_layer as u32, 1, d as u32, ctx as u32, v as u32] {
            b.extend_from_slice(&x.to_le_bytes());
        }
        for x in [0.7f64, 2.0, 0.7, 2.0] {
            b.extend_from_slice(&x.to_le_bytes());
        }
        b.extend_from_slice(&[7u8; 32]);
        // From U+10000: 65,533 characters from U+4E00 would cross the surrogates.
        (0..v - 3).for_each(|i| b.extend_from_slice(&(0x10000 + i as u32).to_le_bytes()));
        // Per layer: four matrices (3 d^2 + d^2 + 4 d^2 + 4 d^2) and the vectors ln1 2d, qkv_b 3d, proj_b d, ln2 2d, fc1_b 4d, fc2_b d.
        let layer = 4 * (12 * d * d + 13 * d);
        b.resize(b.len() + 4 * (v * d + ctx * d + 2 * d) + n_layer * layer, 0);
        b
    }

    /// S7c-2a section 2.6 (F4): a header past any bound is `Format` even when the length matches it; at the bound it parses
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

    /// F5: a finite int8 scale that restores to inf (127 * f32::MAX) is `Format`; a length off by one byte is `Format`.
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

    /// F3: `load` reads only a regular file of at most `MAX_FILE` bytes.
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

    /// F3 with the handle checked after opening: a FIFO with no writer is refused at once instead of blocking the open (without
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
        let (chosen, ll) = m.rerank(&ng, &cands, key, chat).unwrap();
        let ll = ll.unwrap();
        assert_eq!(ll, with(key));
        assert_ne!(ll, with(""), "the key is the model's context");
        assert_eq!(m.alpha_tau_delta(Profile::Chat), (0.7, 2.0, 0.0), "a version 1 file");
        assert_eq!(chosen, pick(&ng[..TOPK], &ll, 0.7, 2.0, 0.0));
        assert_eq!(m.rerank(&ng, &cands, SENTINEL, chat).unwrap().1.unwrap(), with(""), "SENTINEL is the empty k");
        assert_eq!(m.rerank(&ng[..1], &cands[..1], key, chat).unwrap(), (0, None));
        // params the header check would refuse are refused here too, even when fewer than two candidates would run nothing
        for bad in [(f64::NAN, 2.0, 0.0), (f64::INFINITY, 2.0, 0.0), (0.7, f64::NAN, 0.0), (0.7, 2.0, -0.25), (0.7, 2.0, f64::NAN), (0.7, 2.0, MAX_DELTA + 1.0)] {
            assert_eq!(m.rerank(&ng, &cands, key, bad).unwrap_err(), TinyLmError::Format, "{bad:?}");
            assert_eq!(m.rerank(&ng[..1], &cands[..1], key, bad).unwrap_err(), TinyLmError::Format, "{bad:?}");
        }
        assert!(m.rerank(&ng, &cands, key, (0.7, f64::INFINITY, 0.0)).is_ok(), "an infinite tau is allowed: no threshold");
    }

    /// Contract 2.2: the Accelerate path (macOS) and the plain path agree to 1e-5 in LL on random weights; on other targets both
    /// are the plain path and the test only checks that scoring is deterministic.
    #[test]
    fn matrix_paths_agree() {
        let items: Vec<(String, String)> = (0..20)
            .map(|i| {
                let text = |n: usize, a: usize, b: usize| -> String { (0..n).map(|j| char::from_u32(0x4e00 + ((i * a + j * b) % 125) as u32).unwrap()).collect() };
                (text((i * 3) % 40, 31, 7), text(1 + i % 17, 13, 5))
            })
            .collect();
        let pairs: Vec<(&str, &str)> = items.iter().map(|(k, c)| (k.as_str(), c.as_str())).collect();
        for int8 in [false, true] {
            let m = TinyLm::parse(&random_file(int8)).unwrap();
            let (a, p) = (m.ll_many_with(Backend::DEFAULT, &pairs).unwrap(), m.ll_many_with(Backend::Plain, &pairs).unwrap());
            assert!(a.iter().any(|x| *x != 0.0));
            for (x, y) in a.iter().zip(&p) {
                assert!((x - y).abs() < 1e-5, "int8={int8}: {x} vs {y}");
            }
        }
    }
}
