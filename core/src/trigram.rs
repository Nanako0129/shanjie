//! trigram.sjt3 (SJT30001, docs/contracts/trigram-core.md): the trigram level of the formal profile, read beside bigram.sjlm.
//! Layout (little-endian; tools/build_trigram.py writes it, reference/proto/trigram.py reads it):
//!   magic `SJT30001` | V u32 | D f64 | SHA-256 of the bigram model 32 | C u32 | E u32
//!   | key u64[C] (u * V + v, ascending) | total u64[C] (the context's count before pruning) | off u32[C+1]
//!   | next u32[E] (ascending inside a context) | count u32[E]
//! The file is kept as it is (nothing is copied into typed arrays) and every field is read in place; `parse` checks all the
//! invariants the lookups rely on, so no index can panic. R2: errors carry no content.
//! The bytes are a read-only mapping of the file (docs/contracts/trigram-mmap.md) or, in tests, a `Vec<u8>`.

use crate::lm::LmError;
use std::ffi::c_void;
use std::fs::File;
use std::ops::Deref;
use std::os::fd::AsRawFd;
use std::path::Path;
use std::sync::OnceLock;

// <sys/mman.h>: PROT_READ and MAP_PRIVATE have the same values on macOS and x86_64 Linux (learn_store.rs refuses to build elsewhere);
// `off_t` is 64 bits on both. std has no names for them and the crate has no libc dependency.
const PROT_READ: i32 = 1;
const MAP_PRIVATE: i32 = 2;
/// `mmap` returns this (all bits set), not null, on failure.
const MAP_FAILED: *mut c_void = !0usize as *mut c_void;

extern "C" {
    fn mmap(addr: *mut c_void, len: usize, prot: i32, flags: i32, fd: i32, off: i64) -> *mut c_void;
    fn munmap(addr: *mut c_void, len: usize) -> i32;
}

/// A whole file mapped read-only. The pages are clean file-backed pages: the system can drop them under memory pressure and read
/// them back, and they are not part of the process's phys_footprint (the point of docs/contracts/trigram-mmap.md).
/// If the file is truncated or rewritten in place while it is mapped, reading a page past the new end is a SIGBUS. The install
/// paths never do that: scripts/install-ime.sh, the installer and the Homebrew cask replace the whole bundle by rename.
pub(crate) struct Mapped {
    ptr: *mut c_void,
    len: usize,
}

// SAFETY: the mapping is read-only and never changes through this handle, so sharing it between threads is a plain shared read.
unsafe impl Send for Mapped {}
unsafe impl Sync for Mapped {}

impl Mapped {
    /// An empty file, a file that cannot be opened and a failed `mmap` are all `TriTable` (what a failed read is today).
    pub(crate) fn open(path: &Path) -> Result<Mapped, LmError> {
        let f = File::open(path).map_err(|_| LmError::TriTable)?;
        let len = f.metadata().map_err(|_| LmError::TriTable)?.len() as usize;
        if len == 0 {
            return Err(LmError::TriTable);
        }
        // SAFETY: a valid descriptor and a non-zero length; the kernel keeps the mapping after the descriptor is closed (dropped below).
        let ptr = unsafe { mmap(std::ptr::null_mut(), len, PROT_READ, MAP_PRIVATE, f.as_raw_fd(), 0) };
        if ptr == MAP_FAILED {
            return Err(LmError::TriTable);
        }
        Ok(Mapped { ptr, len })
    }
}

impl Deref for Mapped {
    type Target = [u8];
    fn deref(&self) -> &[u8] {
        // SAFETY: `ptr` maps `len` readable bytes until `drop`.
        unsafe { std::slice::from_raw_parts(self.ptr as *const u8, self.len) }
    }
}

impl Drop for Mapped {
    fn drop(&mut self) {
        // SAFETY: exactly the range `mmap` returned, unmapped once.
        unsafe { munmap(self.ptr, self.len) };
    }
}

/// Where a table's bytes live: the mapping the shipping path uses, or memory (tests, `Lm::add_tri`).
pub(crate) enum TriBytes {
    Map(Mapped),
    Mem(Vec<u8>),
}

impl Deref for TriBytes {
    type Target = [u8];
    fn deref(&self) -> &[u8] {
        match self {
            TriBytes::Map(m) => m,
            TriBytes::Mem(v) => v,
        }
    }
}

const MAGIC: &[u8; 8] = b"SJT30001";
const HEAD: usize = 60;
/// The discount (the contract's constant): a table with any other D is refused, so the back-off sums stay exact.
const D: f64 = 0.75;
/// The floor (docs/contracts/trigram-core.md slice A2; docs/research-log.md "2026-10-16：trigram-core 第 C 片暫停，書面有表的退步與修法"):
/// a stored context never gives a word less than this share of its P2, which caps the penalty for a word the context did not keep.
/// reference/proto/trigram.py has the same value (`TRI_FLOOR`); it is not in the table file.
const TRI_FLOOR: f64 = 0.5;

pub(crate) struct Tri {
    v: u64,
    d: f64,
    c: usize,
    raw: TriBytes,
    /// Section offsets into `raw`: key, total, off, next, count.
    at: [usize; 5],
    /// `+tri:<sha8 of the file>` (the summary-line tag), hashed on first use.
    tag: OnceLock<String>,
}

/// A stored context (u, v) with what the score needs from it: the pre-prune total and the back-off weight.
#[derive(Clone, Copy)]
pub(crate) struct TriCtx {
    idx: usize,
    tot: f64,
    back: f64,
}

/// The contexts one decode (or one `total_score` pass) has looked up: (u, v) -> what `Tri::context` found. Not kept between calls.
#[derive(Default)]
pub(crate) struct TriMemo(pub(crate) std::collections::HashMap<(u32, u32), Option<TriCtx>>);

impl Tri {
    /// `raw` is the whole file; `v` and `model_sha` (hex) are the bigram model it must be bound to.
    pub(crate) fn parse(raw: TriBytes, v: usize, model_sha: &str) -> Result<Tri, LmError> {
        let bad = LmError::TriTable;
        let u32_at = |p: usize| raw.get(p..p + 4).map(|b| u32::from_le_bytes([b[0], b[1], b[2], b[3]]));
        if raw.len() < HEAD + 4 || &raw[..8] != MAGIC {
            return Err(bad);
        }
        let (vv, d) = (u32_at(8).ok_or(bad)? as usize, f64::from_le_bytes(raw[12..20].try_into().map_err(|_| bad)?));
        let sha: String = raw[20..52].iter().map(|b| format!("{b:02x}")).collect();
        let (c, e) = (u32_at(52).ok_or(bad)? as usize, u32_at(56).ok_or(bad)? as usize);
        if vv != v || sha != model_sha || d != D {
            return Err(bad);
        }
        let want = c.checked_mul(20).and_then(|x| x.checked_add(e.checked_mul(8)?)).and_then(|x| x.checked_add(HEAD + 4)).ok_or(bad)?;
        if raw.len() != want {
            return Err(bad);
        }
        let key = HEAD;
        let tot = key + 8 * c;
        let off = tot + 8 * c;
        let nxt = off + 4 * (c + 1);
        let cnt = nxt + 4 * e;
        let t = Tri { v: v as u64, d, c, raw, at: [key, tot, off, nxt, cnt], tag: OnceLock::new() };
        let vv = v as u64 * v as u64;
        if t.off(0) != 0 || t.off(c) as usize != e {
            return Err(bad);
        }
        for i in 0..c {
            let (lo, hi) = (t.off(i) as usize, t.off(i + 1) as usize);
            if (i > 0 && t.key(i - 1) >= t.key(i)) || t.key(i) >= vv || t.total(i) == 0 || lo > hi || hi > e {
                return Err(bad);
            }
            let mut sum = 0u64;
            for j in lo..hi {
                if t.next(j) as usize >= v || (j > lo && t.next(j - 1) >= t.next(j)) {
                    return Err(bad);
                }
                sum += t.count(j) as u64;
            }
            // The total is taken before pruning, so the kept counts cannot exceed it (a correct file always passes);
            // a larger sum would make the back-off weight negative.
            if sum > t.total(i) {
                return Err(bad);
            }
        }
        Ok(t)
    }

    fn u32_at(&self, p: usize) -> u32 {
        u32::from_le_bytes([self.raw[p], self.raw[p + 1], self.raw[p + 2], self.raw[p + 3]])
    }
    fn u64_at(&self, p: usize) -> u64 {
        u64::from_le_bytes(self.raw[p..p + 8].try_into().unwrap())
    }
    fn key(&self, i: usize) -> u64 {
        self.u64_at(self.at[0] + 8 * i)
    }
    fn total(&self, i: usize) -> u64 {
        self.u64_at(self.at[1] + 8 * i)
    }
    fn off(&self, i: usize) -> u32 {
        self.u32_at(self.at[2] + 4 * i)
    }
    fn next(&self, j: usize) -> u32 {
        self.u32_at(self.at[3] + 4 * j)
    }
    fn count(&self, j: usize) -> u32 {
        self.u32_at(self.at[4] + 4 * j)
    }

    /// Whether the bytes are a file mapping (what `Lm::load_opts` must give; the tests look at it).
    #[cfg(test)]
    pub(crate) fn is_mapped(&self) -> bool {
        matches!(self.raw, TriBytes::Map(_))
    }

    /// The tag `+tri:<sha8 of the file>` (same text as reference/proto/lm_eval.py).
    pub(crate) fn tag(&self) -> &str {
        self.tag.get_or_init(|| {
            let sha = crate::eval::sha256_hex(&self.raw);
            format!("+tri:{}", &sha[..8])
        })
    }

    /// The context (u, v) if it is stored. Back-off weight as trimodel.py `back`: 1 - sum over the kept entries (c - D) / total,
    /// summed in file order (counts are integers and D is 3/4, so the sum is exact whatever
    /// the order and equals numpy's).
    pub(crate) fn context(&self, u: u32, v: u32) -> Option<TriCtx> {
        let k = u as u64 * self.v + v as u64;
        let (mut lo, mut hi) = (0, self.c);
        while lo < hi {
            let mid = (lo + hi) / 2;
            match self.key(mid).cmp(&k) {
                std::cmp::Ordering::Less => lo = mid + 1,
                std::cmp::Ordering::Greater => hi = mid,
                std::cmp::Ordering::Equal => {
                    let mut kept = 0.0f64;
                    for j in self.off(mid) as usize..self.off(mid + 1) as usize {
                        kept += self.count(j) as f64 - self.d;
                    }
                    let tot = self.total(mid) as f64;
                    return Some(TriCtx { idx: mid, tot, back: 1.0 - kept / tot });
                }
            }
        }
        None
    }

    /// P3(w | u, v) given P2 = `p2` (trimodel.py `prob`): an entry adds `(c - D) / total`, and every word gets `back * P2`; the result
    /// is never below `TRI_FLOOR * p2`.
    pub(crate) fn prob(&self, ctx: TriCtx, w: Option<u32>, p2: f64) -> f64 {
        let (lo, hi) = (self.off(ctx.idx) as usize, self.off(ctx.idx + 1) as usize);
        let found = w.and_then(|w| {
            let (mut a, mut b) = (lo, hi);
            while a < b {
                let mid = (a + b) / 2;
                match self.next(mid).cmp(&w) {
                    std::cmp::Ordering::Less => a = mid + 1,
                    std::cmp::Ordering::Greater => b = mid,
                    std::cmp::Ordering::Equal => return Some(self.count(mid)),
                }
            }
            None
        });
        let p3 = match found.filter(|&k| k != 0) {
            Some(k) => (k as f64 - self.d) / ctx.tot + ctx.back * p2,
            None => ctx.back * p2,
        };
        p3.max(TRI_FLOOR * p2)
    }
}
