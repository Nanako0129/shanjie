//! S7c-1: the small character language model (S7a) in Rust, offline only: the SJTL0001 loader, the forward pass, LL and `pick`
//! (docs/contracts/s7c-core-tinylm.md section 2). Nothing here is reachable from the engine or the FFI yet.
//!
//! The model is `tinylm.NumpyLM` of experiments/s7a-tinylm in f32 (pre-LN decoder, tied embedding, learned positions, GELU tanh,
//! causal attention); `ll_many` is `tinylm.ll_many`. The file is written only by experiments/s7a-tinylm/export_sjtl.py, whose
//! docstring is the format (and the tensor order, which `parse` repeats).
//! R2: errors carry a kind only, never text.

use crate::learn::SENTINEL;
use crate::lm::Profile;
use std::collections::HashMap;
use std::fmt;
use std::path::Path;

const MAGIC: &[u8; 8] = b"SJTL0001";
const HEADER: usize = 100;
const VERSION: u32 = 1;
const PAD: u32 = 0;
const UNK: u32 = 1;
const BOS: u32 = 2;
/// Candidates per forward pass (right-padded with PAD; the causal mask keeps the padding out of the real positions).
pub const BATCH: usize = 8;
/// How many n-best candidates the model reranks (S7a: the first 8).
pub const TOPK: usize = 8;
const LN10: f64 = std::f64::consts::LN_10;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TinyLmError {
    Io,
    Format,
    /// The candidate alone does not fit the context (`ctx - 1` characters after [BOS]).
    TooLong,
}

impl fmt::Display for TinyLmError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            TinyLmError::Io => "cannot read tiny language model file (.sjtl)",
            TinyLmError::Format => "bad tiny language model file (.sjtl)",
            TinyLmError::TooLong => "candidate does not fit the context of the tiny language model (.sjtl)",
        })
    }
}
impl std::error::Error for TinyLmError {}

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
fn gemm(be: Backend, a: &[f32], b: &[f32], c: &mut [f32], (m, k, n): (usize, usize, usize), trans_b: bool) {
    debug_assert!(a.len() == m * k && b.len() == k * n && c.len() == m * n);
    match be {
        #[cfg(target_os = "macos")]
        Backend::Accelerate => {
            assert!(m.max(k).max(n) < i32::MAX as usize);
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
    debug_assert_eq!(x.len(), y.len());
    match be {
        #[cfg(target_os = "macos")]
        Backend::Accelerate => unsafe { vvtanhf(y.as_mut_ptr(), x.as_ptr(), &(x.len() as i32)) },
        Backend::Plain => y.iter_mut().zip(x).for_each(|(o, v)| *o = v.tanh()),
    }
}

/// y = exp(x), elementwise, out of place.
fn exp_into(be: Backend, x: &[f32], y: &mut [f32]) {
    debug_assert_eq!(x.len(), y.len());
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
    chat: (f64, f64),
    formal: (f64, f64),
    ng_sha: [u8; 32],
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
        out
    }
}

impl TinyLm {
    pub fn load(path: &Path) -> Result<TinyLm, TinyLmError> {
        TinyLm::parse(&std::fs::read(path).map_err(|_| TinyLmError::Io)?)
    }

    /// Every check of the format; a bad file is an error, never a partial model.
    pub fn parse(bytes: &[u8]) -> Result<TinyLm, TinyLmError> {
        let bad = TinyLmError::Format;
        if bytes.len() < HEADER || &bytes[..8] != MAGIC {
            return Err(bad);
        }
        let u32_at = |at: usize| u32::from_le_bytes(bytes[at..at + 4].try_into().unwrap());
        let f64_at = |at: usize| f64::from_le_bytes(bytes[at..at + 8].try_into().unwrap());
        let (version, wtype) = (u32_at(8), u32_at(12));
        let [n_layer, n_head, d, ctx, v] = [16, 20, 24, 28, 32].map(|at| u32_at(at) as usize);
        let (chat, formal) = ((f64_at(36), f64_at(44)), (f64_at(52), f64_at(60)));
        if version != VERSION || wtype > 1 || n_layer == 0 || n_head == 0 || d == 0 || d % n_head != 0 || ctx < 2 || v < 3 {
            return Err(bad);
        }
        // Sanity bounds so that the length arithmetic cannot overflow; the real check is the exact file length.
        if n_layer > 1 << 10 || d > 1 << 16 || ctx > 1 << 16 || v > 1 << 24 {
            return Err(bad);
        }
        let int8 = wtype == 1;
        let mat = |r: usize, c: usize| if int8 { r * c + 4 * r } else { 4 * r * c };
        let layer_bytes = 4 * 4 * d + mat(d, 3 * d) + 4 * 3 * d + mat(d, d) + 4 * d + mat(d, 4 * d) + 4 * 4 * d + mat(4 * d, d) + 4 * d;
        let want = HEADER + 4 * (v - 3) + mat(v, d) + mat(ctx, d) + 4 * 2 * d + n_layer * layer_bytes;
        // A NaN alpha or tau would pick nothing sensible; tau may be infinite (no threshold).
        if bytes.len() != want || !chat.0.is_finite() || !formal.0.is_finite() || chat.1.is_nan() || formal.1.is_nan() {
            return Err(bad);
        }
        let mut ids = HashMap::new();
        for i in 0..v - 3 {
            let c = char::from_u32(u32_at(HEADER + 4 * i)).ok_or(bad)?;
            if ids.insert(c, i as u32 + 3).is_some() {
                return Err(bad);
            }
        }
        let mut r = Cursor { b: bytes, at: HEADER + 4 * (v - 3), int8, ok: true };
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
            ng_sha: bytes[68..100].try_into().unwrap(),
            ids,
            emb,
            pos,
            lnf_w,
            lnf_b,
            layers,
        })
    }

    /// (alpha, tau) tuned for the profile.
    pub fn alpha_tau(&self, p: Profile) -> (f64, f64) {
        match p {
            Profile::Chat => self.chat,
            Profile::Formal => self.formal,
        }
    }

    /// SHA-256 (hex) of the n-gram model alpha and tau were tuned with.
    pub fn ngram_sha256(&self) -> String {
        hex(&self.ng_sha)
    }

    /// The S7a rerank of one n-best list, the one rule the CLI measures and the engine will use: the first `TOPK` candidates,
    /// k = the engine's context key (`learn::context_key`; its `SENTINEL`, no Han character before the cursor, is the empty k as
    /// in tinylm.row_key), alpha and tau of the profile, then `pick`. Returns the chosen index and, when the model ran (two or more
    /// candidates), the LLs. A candidate longer than ctx - 1 characters is `TooLong`; the caller decides to keep the first.
    pub fn rerank(&self, ng: &[f64], cands: &[&str], key: &str, p: Profile) -> Result<(usize, Option<Vec<f64>>), TinyLmError> {
        let n = cands.len().min(ng.len()).min(TOPK);
        if n < 2 {
            return Ok((0, None));
        }
        let k = if key == SENTINEL { "" } else { key };
        let items: Vec<(&str, &str)> = cands[..n].iter().map(|c| (k, *c)).collect();
        let ll = self.ll_many(&items)?;
        let (alpha, tau) = self.alpha_tau(p);
        Ok((pick(&ng[..n], &ll, alpha, tau), Some(ll)))
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
            gemm(be, &layer_norm(&x, &l.ln1_w, &l.ln1_b), &l.qkv_w, &mut qkv, (rows, d, 3 * d), false);
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
            gemm(be, &layer_norm(&x, &l.ln2_w, &l.ln2_b), &l.fc1_w, &mut f, (rows, d, 4 * d), false);
            gelu(be, &mut f);
            let mut o = bias_rows(&l.fc2_b, rows);
            gemm(be, &f, &l.fc2_w, &mut o, (rows, 4 * d, d), false);
            x.iter_mut().zip(&o).for_each(|(a, b)| *a += b);
        }
        layer_norm(&x, &self.lnf_w, &self.lnf_b)
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

/// S7a fusion (experiments/s7a-tinylm/fusegrid.py `pick`): the index of the chosen candidate. Fewer than two candidates, or an
/// n-gram margin (first minus second) of at least `tau`, keeps the first; otherwise the highest `ng + alpha * ll`, ties to the
/// better n-gram rank.
pub fn pick(ng: &[f64], ll: &[f64], alpha: f64, tau: f64) -> usize {
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

    /// `rerank` is the rule the CLI measured: the key reaches the model as k (a Han key changes the LLs, and they are exactly
    /// ll_many's with that k); the engine's SENTINEL is the empty k; fewer than two candidates run nothing; only the first TOPK
    /// are scored; the choice is `pick` with the profile's alpha and tau.
    #[test]
    fn rerank_is_the_measured_rule() {
        let m = TinyLm::parse(&random_file(false)).unwrap();
        let cands: Vec<String> = (0..10).map(|i| (0..4).map(|j| char::from_u32(0x4e00 + ((i * 7 + j * 3) % 110) as u32).unwrap()).collect()).collect();
        let cands: Vec<&str> = cands.iter().map(String::as_str).collect();
        let ng: Vec<f64> = (0..10).map(|i| -0.05 * i as f64).collect();
        let with = |k: &str| m.ll_many(&cands[..TOPK].iter().map(|c| (k, *c)).collect::<Vec<_>>()).unwrap();
        let key = "\u{4e01}\u{4e02}";
        let (chosen, ll) = m.rerank(&ng, &cands, key, Profile::Chat).unwrap();
        let ll = ll.unwrap();
        assert_eq!(ll, with(key));
        assert_ne!(ll, with(""), "the key is the model's context");
        assert_eq!(chosen, pick(&ng[..TOPK], &ll, 0.7, 2.0));
        assert_eq!(m.rerank(&ng, &cands, SENTINEL, Profile::Chat).unwrap().1.unwrap(), with(""), "SENTINEL is the empty k");
        assert_eq!(m.rerank(&ng[..1], &cands[..1], key, Profile::Chat).unwrap(), (0, None));
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
