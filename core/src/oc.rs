//! 0c in Rust (docs/contracts/oc-experimental.md section 2, A3): the SJOC0001 loader and `score`, the log10 P(candidate | context,
//! reading) of the 0c model (`Funmula/0c-sequence-d512`, MIT; an external model, not one of ours).
//!
//! The file is written only by tools/oc_ref.py (`write`), whose module docstring is the layout, and `tools/oc_ref.py`'s
//! `Session.run` is the forward this file repeats (float32 here, float64 there). The sequence layout and the scoring rules are
//! experiments/x0c/score_0c.py's (`feeds_for`, `score`, `check_row`), one sequence per candidate (the model card's layout):
//!   [BOS] context[-64:] [SEP] reading x n [OUT] candidate[:-1]   at positions 0..L-1, causal attention,
//! scored at the n positions from [OUT] on. The last syllable is also tried as neutral tone when its base is in the file's
//! `neutral_tolerant_last` (and it is not neutral already); the larger score is kept, per candidate.
//!
//! The file is kept as it is: a read-only mapping (`trigram::Mapped`), the weights read in place as `&[f32]` (never copied into the
//! heap), so the pages count in the process's resident size but not in its phys_footprint. `load` checks the header, the exact total
//! length and the 4-byte alignment of every tensor, and refuses anything else; it does not read the weights (no scan for NaN: that
//! would page the whole file in). R2: errors carry a kind only.

use crate::tinylm::{exp_into, gemm, Backend, BATCH};
use crate::trigram::Mapped;
use std::collections::{HashMap, HashSet};
use std::path::Path;

#[cfg(target_endian = "big")]
compile_error!("SJOC0001 is little-endian and the weights are read in place");

const MAGIC: &[u8; 8] = b"SJOC0001";
const HEADER: usize = 120;
const VERSION: u32 = 1;
/// score_0c.CONTEXT: the last 64 characters of the context are used.
pub const CONTEXT: usize = 64;
/// score_0c.MIN_SYL / MAX_SYL: a row outside 2..=32 syllables is not scored.
const MIN_SYL: usize = 2;
const MAX_SYL: usize = 32;
// Token ids (score_0c.feeds_for): characters from 8, bases from 2 (the file's lists start there), the tone ids are 1 (no mark) and
// 2 to 5 (the marks below).
const BOS: u32 = 1;
const SEP: u32 = 2;
const OUT: u32 = 3;
const UNK: u32 = 4;
const TONES: [(char, u32); 4] = [('ˊ', 2), ('ˇ', 3), ('ˋ', 4), ('˙', 5)];
const NEUTRAL: u32 = 5;
/// Header bounds of the model's order of magnitude (d 512, 8 layers, 27,865 tokens): they keep the length arithmetic far from
/// overflow and the forward's allocations bounded; the exact file length is checked next.
const MAX_LAYERS: usize = 64;
const MAX_D: usize = 4_096;
const MAX_FFN: usize = 16_384;
const MAX_TOK: usize = 1 << 20;
const MAX_BASE: usize = 1 << 16;
const LN10: f64 = std::f64::consts::LN_10;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum OcError {
    /// The file cannot be opened or mapped, or is not a regular file.
    Io,
    /// Not an SJOC0001 file as tools/oc_ref.py writes it.
    Format,
}

impl std::fmt::Display for OcError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(match self {
            OcError::Io => "cannot read 0c model file (.sjoc)",
            OcError::Format => "bad 0c model file (.sjoc)",
        })
    }
}
impl std::error::Error for OcError {}

extern "C" {
    /// libm's single-precision erf (std links libm on Linux and macOS has it in libSystem; the crate has no libc dependency).
    fn erff(x: f32) -> f32;
}

/// A tensor of the file: byte offset and number of f32 values (alignment and range were checked by `load`).
type T = (usize, usize);

struct Layer {
    /// Layer 0 has none (the graph's).
    attn_norm: Option<T>,
    wq: T,
    wk: T,
    wv: T,
    wo: T,
    mlp_norm: T,
    w1: T,
    w2: T,
}

pub struct Oc {
    map: Mapped,
    n_head: usize,
    d: usize,
    ffn: usize,
    n_tok: usize,
    n_rope: usize,
    chars: HashMap<char, u32>,
    bases: HashMap<String, u32>,
    neutral: HashSet<String>,
    out_ids: Vec<u32>,
    dec: T,
    dec_b: T,
    base: T,
    tone: T,
    ty: T,
    emb_norm: T,
    inv_freq: T,
    layers: Vec<Layer>,
    final_norm: T,
    head_w: T,
    head_norm: T,
}

/// `n` f32 at byte offset `at` of `b`, or `None` out of range or misaligned (the part of the range that does not fit f32 slices).
fn f32_at(b: &[u8], at: usize, n: usize) -> Option<&[f32]> {
    let part = b.get(at..at.checked_add(n.checked_mul(4)?)?)?;
    // SAFETY: every bit pattern is a valid f32; `align_to` only hands back the aligned middle, and the ends must be empty.
    let (pre, mid, post) = unsafe { part.align_to::<f32>() };
    (pre.is_empty() && post.is_empty()).then_some(mid)
}

/// The tone mark of the last character (2 to 5) or 1, and the base without it (score_0c.syllable).
fn split_tone(s: &str) -> (&str, u32) {
    match s.chars().last().and_then(|c| TONES.iter().find(|(m, _)| *m == c)) {
        Some(&(m, t)) => (&s[..s.len() - m.len_utf8()], t),
        None => (s, 1),
    }
}

impl Oc {
    /// Maps the regular file at `path` and checks it all (`OcError::Io`: cannot open or map; `Format`: anything the layout of
    /// tools/oc_ref.py `load` would not read, or a length that is not the layout's exactly).
    ///
    /// The file must not be truncated or rewritten in place while the model is alive (`Mapped::open`); the App's installs replace
    /// whole files by rename.
    pub fn load(path: &Path) -> Result<Oc, OcError> {
        // SAFETY: the contract above; nothing in this crate writes a file it has mapped.
        let map = unsafe { Mapped::open(path) }.map_err(|()| OcError::Io)?;
        Oc::parse(map)
    }

    fn parse(map: Mapped) -> Result<Oc, OcError> {
        let bad = OcError::Format;
        let b: &[u8] = &map;
        let u32_at = |at: usize| b.get(at..at + 4).map(|x| u32::from_le_bytes([x[0], x[1], x[2], x[3]]));
        if b.len() < HEADER || &b[..8] != MAGIC || u32_at(8) != Some(VERSION) || u32_at(12) != Some(0) {
            return Err(bad);
        }
        // version, weight type, then n_layer n_head d ffn n_tok n_out n_base n_tone n_type n_rope (the SHA-256s follow)
        let [n_layer, n_head, d, ffn, n_tok, n_out, n_base, n_tone, n_type, n_rope] = [16, 20, 24, 28, 32, 36, 40, 44, 48, 52].map(|at| u32_at(at).unwrap() as usize);
        if !(1..=MAX_LAYERS).contains(&n_layer) || n_head == 0 || d == 0 || d > MAX_D || d % n_head != 0 || 2 * n_rope != d / n_head {
            return Err(bad);
        }
        // 8 special ids, the 2 base ids below the bases, the tone ids 0..=5 and the type ids 0..=2 the forward indexes
        if !(1..=MAX_FFN).contains(&ffn) || !(8..=MAX_TOK).contains(&n_tok) || !(2..=MAX_BASE).contains(&n_base) || n_tone < 6 || n_type < 3 {
            return Err(bad);
        }
        if n_out > n_tok || n_out < n_tok - 6 {
            return Err(bad);
        }
        let mut at = HEADER;
        let mut chars = HashMap::new();
        for i in 0..n_tok - 8 {
            let c = char::from_u32(u32_at(at + 4 * i).ok_or(bad)?).ok_or(bad)?;
            if chars.insert(c, i as u32 + 8).is_some() {
                return Err(bad);
            }
        }
        at += 4 * (n_tok - 8);
        // two length-prefixed UTF-8 lists joined by "\n", each zero-padded to a multiple of 4: the bases, then the neutral-tolerant ones
        let mut words = Vec::new();
        for _ in 0..2 {
            let k = u32_at(at).ok_or(bad)? as usize;
            let s = std::str::from_utf8(b.get(at + 4..at + 4 + k).ok_or(bad)?).map_err(|_| bad)?;
            words.push(if k == 0 { Vec::new() } else { s.split('\n').map(String::from).collect::<Vec<_>>() });
            at += 4 + k + (4 - k % 4) % 4;
        }
        let neutral: HashSet<String> = words.pop().unwrap().into_iter().collect();
        let base_list = words.pop().unwrap();
        if base_list.len() != n_base - 2 {
            return Err(bad);
        }
        let mut bases = HashMap::new();
        for (i, w) in base_list.into_iter().enumerate() {
            if bases.insert(w, i as u32 + 2).is_some() {
                return Err(bad);
            }
        }
        let mut out_ids = Vec::with_capacity(n_out);
        for i in 0..n_out {
            let id = u32_at(at + 4 * i).ok_or(bad)?;
            // ascending ids below n_tok, and every character id c at position c - 6 (so a candidate's column is its own id)
            if id as usize >= n_tok || out_ids.last().is_some_and(|&p| p >= id) {
                return Err(bad);
            }
            out_ids.push(id);
        }
        if (8..n_tok).any(|c| out_ids[c - 6] as usize != c) {
            return Err(bad);
        }
        at += 4 * n_out;
        // The tensors in tools/oc_ref.py `tensors()` order; each must start 4-aligned and lie inside the file, and the last must end at its end.
        let mut take = |n: usize| -> Result<T, OcError> {
            f32_at(b, at, n).ok_or(bad)?;
            let t = (at, n);
            at += 4 * n;
            Ok(t)
        };
        let (dec, dec_b, base, tone, ty) = (take(n_tok * d)?, take(n_tok)?, take(n_base * d)?, take(n_tone * d)?, take(n_type * d)?);
        let (emb_norm, inv_freq) = (take(d)?, take(n_rope)?);
        let mut layers = Vec::with_capacity(n_layer);
        for i in 0..n_layer {
            // struct fields evaluate in source order
            layers.push(Layer {
                attn_norm: if i > 0 { Some(take(d)?) } else { None },
                wq: take(d * d)?,
                wk: take(d * d)?,
                wv: take(d * d)?,
                wo: take(d * d)?,
                mlp_norm: take(d)?,
                w1: take(d * 2 * ffn)?,
                w2: take(ffn * d)?,
            });
        }
        let (final_norm, head_w, head_norm) = (take(d)?, take(d * d)?, take(d)?);
        if at != b.len() {
            return Err(bad);
        }
        Ok(Oc {
            n_head,
            d,
            ffn,
            n_tok,
            n_rope,
            chars,
            bases,
            neutral,
            out_ids,
            dec,
            dec_b,
            base,
            tone,
            ty,
            emb_norm,
            inv_freq,
            layers,
            final_norm,
            head_w,
            head_norm,
            map,
        })
    }

    fn w(&self, t: T) -> &[f32] {
        f32_at(&self.map, t.0, t.1).expect("`load` checked every tensor")
    }

    /// log10 P(candidate | context, reading) per candidate, or all `None` when the row cannot be scored: a candidate character outside
    /// the vocabulary, a candidate whose length is not the syllable count, a syllable count outside 2..=32, or a syllable whose base
    /// the model does not know (score_0c.check_row). Context characters outside the vocabulary are UNK; only the last `CONTEXT`
    /// count. Syllables are zhuyin with an optional tone mark last (`ˊˇˋ˙`, none = first tone).
    pub fn score(&self, context: &str, syllables: &[&str], candidates: &[&str]) -> Vec<Option<f64>> {
        self.score_with(Backend::DEFAULT, context, syllables, candidates)
    }

    pub fn score_with(&self, be: Backend, context: &str, syllables: &[&str], candidates: &[&str]) -> Vec<Option<f64>> {
        let none = vec![None; candidates.len()];
        let n = syllables.len();
        if candidates.is_empty() || !(MIN_SYL..=MAX_SYL).contains(&n) {
            return none;
        }
        let Some(mut sounds) = syllables.iter().map(|s| self.syllable(s)).collect::<Option<Vec<_>>>() else { return none };
        let mut cands = Vec::with_capacity(candidates.len());
        for c in candidates {
            let ids: Option<Vec<u32>> = c.chars().map(|ch| self.chars.get(&ch).copied()).collect();
            match ids {
                Some(ids) if ids.len() == n => cands.push(ids),
                _ => return none,
            }
        }
        let mut ctx: Vec<u32> = context.chars().rev().take(CONTEXT).map(|c| self.chars.get(&c).copied().unwrap_or(UNK)).collect();
        ctx.reverse();
        let run = |sounds: &[(u32, u32)]| -> Vec<f64> { cands.chunks(BATCH).flat_map(|ch| self.log_probs(be, &ctx, sounds, ch)).collect() };
        let mut ll = run(&sounds);
        let (base, _) = split_tone(syllables[n - 1]);
        if self.neutral.contains(base) && !syllables[n - 1].ends_with('˙') {
            sounds[n - 1].1 = NEUTRAL;
            ll = ll.iter().zip(run(&sounds)).map(|(a, b)| a.max(b)).collect();
        }
        ll.into_iter().map(|x| Some(x / LN10)).collect()
    }

    /// (base id, tone id) of one syllable; `None` when the base is not in the file.
    fn syllable(&self, s: &str) -> Option<(u32, u32)> {
        let (base, tone) = split_tone(s);
        Some((*self.bases.get(base)?, tone))
    }

    /// ln P(candidate | context, reading) for each candidate id list (all of length `sounds.len()`), one sequence per candidate, all in
    /// one batch.
    fn log_probs(&self, be: Backend, ctx: &[u32], sounds: &[(u32, u32)], cands: &[Vec<u32>]) -> Vec<f64> {
        let (d, n, nb) = (self.d, sounds.len(), cands.len());
        let head = ctx.len() + 2; // [BOS] context [SEP]
        let l = head + 2 * n; // ... reading x n, [OUT], candidate[:-1]
        let rows = nb * l;
        let (dec, base, tone, ty) = (self.w(self.dec), self.w(self.base), self.w(self.tone), self.w(self.ty));
        let mut x = vec![0f32; rows * d];
        for (bi, cand) in cands.iter().enumerate() {
            for p in 0..l {
                // (the row to start from, the type id): the reading positions take base + tone embeddings instead of a character's
                let (e, t): (Vec<f32>, usize) = if p < head {
                    let id = if p == 0 { BOS } else if p == head - 1 { SEP } else { ctx[p - 1] };
                    (dec[id as usize * d..][..d].to_vec(), 0)
                } else if p < head + n {
                    let (bs, tn) = sounds[p - head];
                    (base[bs as usize * d..][..d].iter().zip(&tone[tn as usize * d..][..d]).map(|(a, b)| a + b).collect(), 1)
                } else if p == head + n {
                    (dec[OUT as usize * d..][..d].to_vec(), 0)
                } else {
                    (dec[cand[p - head - n - 1] as usize * d..][..d].to_vec(), 2)
                };
                x[(bi * l + p) * d..][..d].iter_mut().zip(e.iter().zip(&ty[t * d..][..d])).for_each(|(o, (a, b))| *o = a + b);
            }
        }
        let mut x = layer_norm(&x, self.w(self.emb_norm));
        let nr = self.n_rope;
        let inv_freq = self.w(self.inv_freq);
        let (mut cos, mut sin) = (vec![0f32; l * nr], vec![0f32; l * nr]);
        for p in 0..l {
            for j in 0..nr {
                let f = p as f64 * inv_freq[j] as f64;
                (cos[p * nr + j], sin[p * nr + j]) = (f.cos() as f32, f.sin() as f32);
            }
        }
        let ffn = self.ffn;
        for ly in &self.layers {
            let h = match ly.attn_norm {
                Some(w) => layer_norm(&x, self.w(w)),
                None => x.clone(),
            };
            let (q, k, v) = (mm(be, &h, self.w(ly.wq), (rows, d, d)), mm(be, &h, self.w(ly.wk), (rows, d, d)), mm(be, &h, self.w(ly.wv), (rows, d, d)));
            let att = self.attention(be, (&q, &k, &v), (&cos, &sin), (nb, l));
            let o = mm(be, &att, self.w(ly.wo), (rows, d, d));
            x.iter_mut().zip(&o).for_each(|(a, b)| *a += b);
            let u = mm(be, &layer_norm(&x, self.w(ly.mlp_norm)), self.w(ly.w1), (rows, d, 2 * ffn));
            // the first `ffn` columns through GELU, times the other `ffn` (the graph's Split)
            let g: Vec<f32> = u.chunks_exact(2 * ffn).flat_map(|r| r[..ffn].iter().zip(&r[ffn..]).map(|(a, b)| gelu(*a) * b)).collect();
            let o = mm(be, &g, self.w(ly.w2), (rows, ffn, d));
            x.iter_mut().zip(&o).for_each(|(a, b)| *a += b);
        }
        // the n positions from [OUT] on, of each sequence
        let proj: Vec<f32> = (0..nb).flat_map(|bi| x[(bi * l + l - n) * d..(bi * l + l) * d].iter().copied()).collect();
        let h = layer_norm(&proj, self.w(self.final_norm));
        let mut h = mm(be, &h, self.w(self.head_w), (nb * n, d, d));
        h.iter_mut().for_each(|v| *v = gelu(*v));
        let h = layer_norm(&h, self.w(self.head_norm));
        let nt = self.n_tok;
        let mut logits = vec![0f32; nb * n * nt];
        gemm(be, &h, dec, &mut logits, (nb * n, d, nt), true);
        let dec_b = self.w(self.dec_b);
        let (mut ll, mut z, mut e) = (vec![0f64; nb], vec![0f32; self.out_ids.len()], vec![0f32; self.out_ids.len()]);
        for (r, row) in logits.chunks_exact_mut(nt).enumerate() {
            row.iter_mut().zip(dec_b).for_each(|(a, b)| *a += b);
            // log-softmax over the output columns; a candidate's character id is its own column (checked by `parse`)
            z.iter_mut().zip(&self.out_ids).for_each(|(o, &id)| *o = row[id as usize]);
            let m = z.iter().copied().fold(f32::NEG_INFINITY, f32::max);
            z.iter_mut().for_each(|a| *a -= m);
            exp_into(be, &z, &mut e);
            let lse = m as f64 + e.iter().map(|&a| a as f64).sum::<f64>().ln();
            ll[r / n] += row[cands[r / n][r % n] as usize] as f64 - lse;
        }
        ll
    }

    /// Multi-head causal attention with RoPE on q and k, per sequence and head: scores by one product, softmax over the lower triangle,
    /// then the product with v. `qkv` are the (nb * l) x d projections, `cs` the cos and sin tables (l x n_rope).
    fn attention(&self, be: Backend, (q, k, v): (&[f32], &[f32], &[f32]), (cos, sin): (&[f32], &[f32]), (nb, l): (usize, usize)) -> Vec<f32> {
        let (d, nh, nr) = (self.d, self.n_head, self.n_rope);
        let hd = d / nh;
        let scale = 1.0 / (hd as f32).sqrt();
        let mut att = vec![0f32; nb * l * d];
        let (mut qh, mut kh, mut vh) = (vec![0f32; l * hd], vec![0f32; l * hd], vec![0f32; l * hd]);
        let (mut s, mut e, mut o) = (vec![0f32; l * l], vec![0f32; l * l], vec![0f32; l * hd]);
        for bi in 0..nb {
            for h in 0..nh {
                for p in 0..l {
                    let at = (bi * l + p) * d + h * hd;
                    rope(&q[at..at + hd], &cos[p * nr..][..nr], &sin[p * nr..][..nr], &mut qh[p * hd..][..hd]);
                    rope(&k[at..at + hd], &cos[p * nr..][..nr], &sin[p * nr..][..nr], &mut kh[p * hd..][..hd]);
                    vh[p * hd..][..hd].copy_from_slice(&v[at..at + hd]);
                }
                s.fill(0.0);
                gemm(be, &qh, &kh, &mut s, (l, hd, l), true);
                for i in 0..l {
                    let row = &mut s[i * l..(i + 1) * l];
                    row[..=i].iter_mut().for_each(|a| *a *= scale);
                    let m = row[..=i].iter().copied().fold(f32::NEG_INFINITY, f32::max);
                    row[..=i].iter_mut().for_each(|a| *a -= m);
                    row[i + 1..].fill(f32::NEG_INFINITY);
                }
                exp_into(be, &s, &mut e);
                o.fill(0.0);
                gemm(be, &e, &vh, &mut o, (l, l, hd), false);
                for p in 0..l {
                    let sum: f32 = e[p * l..(p + 1) * l].iter().sum();
                    att[(bi * l + p) * d + h * hd..][..hd].iter_mut().zip(&o[p * hd..][..hd]).for_each(|(a, b)| *a = b / sum);
                }
            }
        }
        att
    }
}

/// a (m x k) . w (k x n) as a new matrix.
fn mm(be: Backend, a: &[f32], w: &[f32], dims: (usize, usize, usize)) -> Vec<f32> {
    let mut c = vec![0f32; dims.0 * dims.2];
    gemm(be, a, w, &mut c, dims, false);
    c
}

/// LayerNorm over the last axis (the row length is `w`'s), eps 1e-5, weight only: (x - mean) / sqrt(var + eps) * w, as numpy.
fn layer_norm(x: &[f32], w: &[f32]) -> Vec<f32> {
    let d = w.len();
    let mut out = Vec::with_capacity(x.len());
    for row in x.chunks_exact(d) {
        let mu = row.iter().map(|&v| v as f64).sum::<f64>() / d as f64;
        let var = row.iter().map(|&v| (v as f64 - mu).powi(2)).sum::<f64>() / d as f64;
        let inv = 1.0 / (var + 1e-5).sqrt();
        out.extend(row.iter().zip(w).map(|(&v, &w)| ((v as f64 - mu) * inv) as f32 * w));
    }
    out
}

/// Exact (erf) GELU: x * 0.5 * (1 + erf(x / sqrt 2)).
fn gelu(x: f32) -> f32 {
    // SAFETY: a pure libm function of one value.
    x * 0.5 * (1.0 + unsafe { erff(x / std::f32::consts::SQRT_2) })
}

/// Rotate-half RoPE of one head vector `t` (2 * n_rope values) at one position: t * cos + rotate_half(t) * sin, where the cos and sin
/// tables repeat over the two halves and rotate_half(t) = [-t[half..], t[..half]].
fn rope(t: &[f32], cos: &[f32], sin: &[f32], out: &mut [f32]) {
    let h = t.len() / 2;
    for j in 0..h {
        out[j] = t[j] * cos[j] - t[j + h] * sin[j];
        out[j + h] = t[j + h] * cos[j] + t[j] * sin[j];
    }
}
