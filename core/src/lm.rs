//! S2c: port of reference/proto/lm.py (SJLM0001 reader, bigram scoring, cap_overlay, LM beam decode).
//! The Python code is ground truth; float operation order is kept on purpose (no fusing or reordering).
//! R2: errors carry no content; nothing here formats input text.

use crate::learn::{context_key, newword_score, record_key, Learner, Level, NEWWORD_MAX, NEWWORD_MIN};
use crate::trigram::{Mapped, Tri, TriBytes, TriCtx, TriMemo};
use crate::{Error, Lexicon, PER_KEY};
use std::collections::{HashMap, HashSet};
use std::fmt;
use std::path::Path;
use std::sync::{Arc, OnceLock};

pub const LAMBDA_CHAT: f64 = 0.5;
pub const LAMBDA_FORMAL: f64 = 0.7;
/// Words the corpus never saw are pushed down by this much (lm.py UNSEEN_OVERLAY_PENALTY).
const UNSEEN_OVERLAY_PENALTY: f64 = 1.0;

/// The formal profile's Kneser-Ney beta (research-log 2026-10-26 stage 3, docs/contracts/sj-lm-7.md section 2.5): not the side file's, which
/// is the chat beta. Chat takes the beta the side file records.
pub const FORMAL_KN_BETA: f64 = 0.75;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Profile {
    Chat,
    Formal,
}

impl Profile {
    pub fn lambda(self) -> f64 {
        match self {
            Profile::Chat => LAMBDA_CHAT,
            Profile::Formal => LAMBDA_FORMAL,
        }
    }
    /// ABI code: 0 chat, 1 formal.
    pub fn from_code(code: u32) -> Option<Profile> {
        match code {
            0 => Some(Profile::Chat),
            1 => Some(Profile::Formal),
            _ => None,
        }
    }
}

/// What the decoder and `Engine::total_score` score a word with (docs/contracts/trigram-core.md, sj-lm-7-pr2.md): lambda and the profile,
/// which picks the trigram table (the profile's, when one is loaded: `Lm::uses_tri`) and the Kneser-Ney beta (`FORMAL_KN_BETA` for
/// formal, the side file's for chat). The Kneser-Ney side file is applied whenever one is loaded. `Lm::scoring` derives it from the
/// profile; a bare lambda (`From<f64>`) has no profile: the bigram with the side file as loaded, which is what every caller scored with
/// before the trigram level existed.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Scoring {
    pub lam: f64,
    pub profile: Option<Profile>,
}

impl From<f64> for Scoring {
    fn from(lam: f64) -> Scoring {
        Scoring { lam, profile: None }
    }
}

/// The trigram table file beside the model for `profile`.
fn tri_file(profile: Profile) -> &'static str {
    match profile {
        Profile::Chat => "trigram-chat.sjt3",
        Profile::Formal => "trigram.sjt3",
    }
}

/// `dir/name` beside the model: `None` when the path does not exist; a path that exists (a dangling symlink included) is `Some`,
/// and one that cannot even be examined is an error.
fn beside(model: &Path, name: &str) -> Result<Option<std::path::PathBuf>, ()> {
    let side = model.with_file_name(name);
    match std::fs::symlink_metadata(&side) {
        Ok(_) => Ok(Some(side)),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(_) => Err(()),
    }
}

/// The Kneser-Ney beta a scoring with `profile` applies: formal `FORMAL_KN_BETA`; chat and a bare lambda the side file's own.
fn kn_beta(kn: &Kn, profile: Option<Profile>) -> f64 {
    if profile == Some(Profile::Formal) {
        FORMAL_KN_BETA
    } else {
        kn.beta
    }
}

/// `beside`, then read whole (an existing path that cannot be read is an error).
fn read_beside(model: &Path, name: &str) -> Result<Option<Vec<u8>>, ()> {
    match beside(model, name)? {
        Some(side) => std::fs::read(side).map(Some).map_err(|_| ()),
        None => Ok(None),
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LmError {
    Io,
    Format,
    /// The Kneser-Ney side file (`kn.sjkn`) exists but cannot be read, or does not match the model (docs/contracts/kn-core.md).
    KnSide,
    /// The trigram table (`trigram.sjt3`) exists but cannot be read, or does not match the model (docs/contracts/trigram-core.md).
    TriTable,
}

impl fmt::Display for LmError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            LmError::Io => "cannot read language model file",
            LmError::Format => "bad language model file",
            LmError::KnSide => "unreadable or mismatching Kneser-Ney side file (kn.sjkn)",
            LmError::TriTable => "unreadable or mismatching trigram table (trigram.sjt3)",
        })
    }
}
impl std::error::Error for LmError {}

/// Bigram model. Context `i` owns `nxt/cnt[off[i]..off[i+1]]`, ascending by next id.
pub struct Lm {
    n: u64,
    d: f64,
    p_eos: f64,
    pool: Vec<u8>,
    voff: Vec<u32>,
    uni: Vec<u64>,
    ctx_id: Vec<u32>,
    ctx_total: Vec<f64>,
    ctx_back: Vec<f64>,
    off: Vec<u32>,
    nxt: Vec<u32>,
    cnt: Vec<u32>,
    /// S2k word-class term (classes.sjc); `None` only for the explicit class-less constructors.
    classes: Option<Classes>,
    /// SHA-256 (hex) of the model bytes, taken once in `parse`: classes.sjc and kn.sjkn are bound to it.
    sha: String,
    /// Kneser-Ney side file (`kn.sjkn` beside the model, docs/contracts/kn-core.md); `None` is off.
    kn: Option<Kn>,
    /// Trigram tables beside the model (docs/contracts/trigram-core.md, sj-lm-7-pr2.md): `trigram.sjt3` is the formal profile's,
    /// `trigram-chat.sjt3` the chat profile's. Each profile scores with its own (`Lm::scoring`); `None` is off.
    tri_formal: Option<Tri>,
    tri_chat: Option<Tri>,
}

/// kn.sjkn (SJKN0002, written by tools/kn_cont.py), indexed by model word id. `total` (sum of N') is computed
/// upstream in Python (lm.kn_total over the variant classes) and only read here.
struct Kn {
    beta: f64,
    theta: u32,
    total: u64,
    /// The whole file, the bytes `add_kn` checked; N is read in place (little-endian u32 at `KN_N + 4 * id`), so nothing is copied.
    raw: Vec<u8>,
    /// SHA-256 of `raw` and `<its sha8>`, computed on first use.
    hashed: OnceLock<([u8; 32], String)>,
}

const KN_N: usize = 64;

impl Kn {
    fn hashed(&self) -> &([u8; 32], String) {
        self.hashed.get_or_init(|| {
            let sha = crate::eval::sha256(&self.raw);
            let sha8: String = sha[..4].iter().map(|b| format!("{b:02x}")).collect();
            (sha, sha8)
        })
    }

    /// N' = N + 1 of word `id`.
    fn np1(&self, id: usize) -> u64 {
        let at = KN_N + 4 * id;
        u32::from_le_bytes([self.raw[at], self.raw[at + 1], self.raw[at + 2], self.raw[at + 3]]) as u64 + 1
    }
}

/// classes.sjc (format in tools/build_classes.py), indexed by model word id.
struct Classes {
    k3: usize,
    mu: f64,
    cls: Vec<u16>,
    emit: Vec<f64>,
    pc: Vec<f64>,
}

const CLASS_MAGIC: &[u8; 8] = b"SJCL0001";
const KN_MAGIC: &[u8; 8] = b"SJKN0002";
const NO_CLASS: u16 = 0xFFFF;

/// classes.sjc bytes for `model` (tests only; the real file is written by tools/build_classes.py).
#[cfg(test)]
pub(crate) fn test_classes(model: &[u8], k: u32, mu: f64, cls: &[u16], emit: &[f64], pc: &[f64]) -> Vec<u8> {
    let sha = crate::eval::sha256_hex(model);
    let mut b = CLASS_MAGIC.to_vec();
    b.extend((0..32).map(|i| u8::from_str_radix(&sha[2 * i..2 * i + 2], 16).unwrap()));
    b.extend(k.to_le_bytes());
    b.extend(mu.to_le_bytes());
    b.extend((cls.len() as u32).to_le_bytes());
    b.extend(cls.iter().flat_map(|c| c.to_le_bytes()));
    b.extend(emit.iter().flat_map(|c| c.to_le_bytes()));
    b.extend(pc.iter().flat_map(|c| c.to_le_bytes()));
    b
}

/// SJT30001 bytes for `model` (tests only; V 4): contexts `((u, v), total before pruning, [(next id, count)])` sorted by (u, v).
#[cfg(test)]
pub(crate) fn test_tri(model: &[u8], d: f64, ctxs: &[((u32, u32), u64, &[(u32, u32)])]) -> Vec<u8> {
    let mut b = b"SJT30001".to_vec();
    b.extend(4u32.to_le_bytes());
    b.extend(d.to_le_bytes());
    b.extend(crate::eval::sha256(model));
    b.extend((ctxs.len() as u32).to_le_bytes());
    b.extend((ctxs.iter().map(|c| c.2.len()).sum::<usize>() as u32).to_le_bytes());
    ctxs.iter().for_each(|c| b.extend((c.0 .0 as u64 * 4 + c.0 .1 as u64).to_le_bytes()));
    ctxs.iter().for_each(|c| b.extend(c.1.to_le_bytes()));
    let mut at = 0u32;
    b.extend(0u32.to_le_bytes());
    for c in ctxs {
        at += c.2.len() as u32;
        b.extend(at.to_le_bytes());
    }
    ctxs.iter().flat_map(|c| c.2).for_each(|e| b.extend(e.0.to_le_bytes()));
    ctxs.iter().flat_map(|c| c.2).for_each(|e| b.extend(e.1.to_le_bytes()));
    b
}

/// A previous word as the probability function needs it: its vocabulary id (for the class term) and its
/// bigram context row, if it has one.
#[derive(Clone, Copy)]
pub struct Ctx {
    id: Option<u32>,
    idx: Option<usize>,
}

struct Rd<'a>(&'a [u8]);

impl<'a> Rd<'a> {
    fn take(&mut self, n: usize) -> Result<&'a [u8], LmError> {
        if n > self.0.len() {
            return Err(LmError::Format);
        }
        let (a, b) = self.0.split_at(n);
        self.0 = b;
        Ok(a)
    }
    fn arr<const K: usize>(&mut self) -> Result<[u8; K], LmError> {
        self.take(K)?.try_into().map_err(|_| LmError::Format)
    }
    fn u16(&mut self) -> Result<u16, LmError> {
        Ok(u16::from_le_bytes(self.arr()?))
    }
    fn u32(&mut self) -> Result<u32, LmError> {
        Ok(u32::from_le_bytes(self.arr()?))
    }
    fn u64(&mut self) -> Result<u64, LmError> {
        Ok(u64::from_le_bytes(self.arr()?))
    }
    fn f64(&mut self) -> Result<f64, LmError> {
        Ok(f64::from_le_bytes(self.arr()?))
    }
    fn vec<T>(&mut self, n: usize, w: usize, f: impl Fn(&[u8]) -> T) -> Result<Vec<T>, LmError> {
        let bytes = self.take(n.checked_mul(w).ok_or(LmError::Format)?)?;
        Ok(bytes.chunks_exact(w).map(f).collect())
    }
    fn u32s(&mut self, n: usize) -> Result<Vec<u32>, LmError> {
        self.vec(n, 4, |c| u32::from_le_bytes([c[0], c[1], c[2], c[3]]))
    }
    fn u64s(&mut self, n: usize) -> Result<Vec<u64>, LmError> {
        self.vec(n, 8, |c| u64::from_le_bytes([c[0], c[1], c[2], c[3], c[4], c[5], c[6], c[7]]))
    }
}

impl Lm {
    /// SHA-256 (hex) of the model file this `Lm` was parsed from.
    pub fn sha256(&self) -> &str {
        &self.sha
    }

    /// Cheap fingerprint of the loaded model (sizes of its tables), to tell two models apart.
    pub fn identity(&self) -> (u64, usize, usize) {
        (self.n, self.voff.len(), self.nxt.len())
    }

    /// The model at `path` and the `classes.sjc` beside it (S2k section 4.3). A missing or mismatching class
    /// file is an error, never a silent fallback. A `kn.sjkn` beside the model switches the Kneser-Ney term on
    /// (docs/contracts/kn-core.md); one that does not match the model is an error too.
    pub fn load(path: &Path) -> Result<Lm, LmError> {
        Lm::load_with(path, true, true)
    }

    /// The model without the class term (`--no-classes`); a `kn.sjkn` beside it is still read.
    pub fn load_without_classes(path: &Path) -> Result<Lm, LmError> {
        Lm::load_with(path, false, true)
    }

    /// `load` with the class file (`classes`) and the Kneser-Ney side file (`kn`) each optional (`--no-classes`, `--no-kn`).
    /// With `kn` on, no `kn.sjkn` path is simply off; one that exists (a dangling symlink included) but cannot be
    /// read or does not match is `KnSide`, never ignored. The trigram tables beside the model (`trigram.sjt3`, `trigram-chat.sjt3`) are read the same way.
    pub fn load_with(path: &Path, classes: bool, kn: bool) -> Result<Lm, LmError> {
        Lm::load_opts(path, classes, kn, &[Profile::Chat, Profile::Formal])
    }

    /// `load_with` with the trigram tables optional too: only the tables of the profiles in `tri` are read (`&[]` for `--no-tri`, one
    /// profile for the CLI, `&[Profile::Chat, Profile::Formal]` for the engine). For such a profile, a table file (`trigram.sjt3`
    /// formal, `trigram-chat.sjt3` chat) that exists (a dangling symlink included) but cannot be read or does not match the model is
    /// `TriTable`; no such path is off.
    pub fn load_opts(path: &Path, classes: bool, kn: bool, tri: &[Profile]) -> Result<Lm, LmError> {
        let model = std::fs::read(path).map_err(|_| LmError::Io)?;
        let mut lm = if classes {
            let c = std::fs::read(path.with_file_name("classes.sjc")).map_err(|_| LmError::Io)?;
            Lm::parse_with_classes(&model, &c)?
        } else {
            Lm::parse(&model)?
        };
        if kn {
            if let Some(side) = read_beside(path, "kn.sjkn").map_err(|_| LmError::KnSide)? {
                lm.add_kn(&side)?;
            }
        }
        for &profile in tri {
            // Mapped, not read: the formal table is 133.3 MB (docs/contracts/trigram-mmap.md). The same "exists" test as `read_beside`.
            if let Some(side) = beside(path, tri_file(profile)).map_err(|_| LmError::TriTable)? {
                // SAFETY: nothing rewrites this file in place while an `Lm` has it mapped. The App's Resources and a user's data/lm are
                // replaced only by rename (scripts/install-ime.sh, the installer, the Homebrew cask), and tools/build_trigram.py writes a
                // temp file and renames it onto the target.
                let map = unsafe { Mapped::open(&side) }.map_err(|()| LmError::TriTable)?;
                lm.add_tri_bytes(profile, TriBytes::Map(map))?;
            }
        }
        Ok(lm)
    }

    /// SJKN0002 bytes (docs/contracts/kn-core.md section 1): magic, V, the model's SHA-256, θ, β, sum of N', N[V]. Checked
    /// (all `KnSide`): magic, V, the hash of the model this `Lm` was parsed from, exact length, β in [0, 1], and the
    /// header sum of N' between the largest N' and the sum of all N' over ids >= 2 (a correct file always is: the sum counts
    /// each variant class once). Turns the Kneser-Ney term on.
    pub fn add_kn(&mut self, side: &[u8]) -> Result<(), LmError> {
        let bad = |_| LmError::KnSide;
        let mut r = Rd(side);
        if r.take(8).map_err(bad)? != KN_MAGIC {
            return Err(LmError::KnSide);
        }
        let v = r.u32().map_err(bad)? as usize;
        let want: String = r.take(32).map_err(bad)?.iter().map(|b| format!("{b:02x}")).collect();
        let (theta, beta, total) = (r.u32().map_err(bad)?, r.f64().map_err(bad)?, r.u64().map_err(bad)?);
        if v != self.uni.len() || want != self.sha || !(0.0..=1.0).contains(&beta) || side.len() != KN_N + 4 * v {
            return Err(LmError::KnSide);
        }
        let kn = Kn { beta, theta, total, raw: side.to_vec(), hashed: OnceLock::new() };
        let (mut max, mut sum) = (0u64, 0u128);
        for id in 2..v {
            let n = kn.np1(id);
            max = max.max(n);
            sum += n as u128;
        }
        if total == 0 || total < max || total as u128 > sum {
            return Err(LmError::KnSide);
        }
        self.kn = Some(kn);
        Ok(())
    }

    /// The summary-line suffix `+kn:<side file sha8>:θ<θ>:β<β>` (same text as reference/proto/lm_eval.py) with the β `profile` scores
    /// with (`kn_beta`); `None` when the Kneser-Ney term is off. The side file is hashed on the first call.
    pub fn kn_tag(&self, profile: Profile) -> Option<String> {
        let k = self.kn.as_ref()?;
        Some(format!("+kn:{}:θ{}:β{}", k.hashed().1, k.theta, fmt_g(kn_beta(k, Some(profile)))))
    }

    /// SHA-256 of the whole side file, from the bytes `add_kn` checked (never read again); `None` when no `kn.sjkn` is
    /// loaded. A version 4 `.sjtl` records this value (docs/contracts/model-v6.md section 2.7; export_sjtl.py `--kn-side` writes
    /// Python's `hashlib.sha256` of the same file), and `TinyLm::load_paired` compares all 32 bytes.
    pub fn kn_sha256(&self) -> Option<[u8; 32]> {
        self.kn.as_ref().map(|k| k.hashed().0)
    }

    /// SJT30001 bytes (docs/contracts/trigram-core.md; layout in core/src/trigram.rs), taken over without a copy (`load_opts` maps the file instead). Checked (all `TriTable`): magic,
    /// V, the hash of the model this `Lm` was parsed from, D, exact length, and the order and range of every key, offset and next id.
    /// Turns the trigram level on for `profile` (the formal table is `trigram.sjt3`, the chat one `trigram-chat.sjt3`).
    pub fn add_tri_for(&mut self, profile: Profile, table: Vec<u8>) -> Result<(), LmError> {
        self.add_tri_bytes(profile, TriBytes::Mem(table))
    }

    fn add_tri_bytes(&mut self, profile: Profile, table: TriBytes) -> Result<(), LmError> {
        let t = Some(Tri::parse(table, self.uni.len(), &self.sha)?);
        match profile {
            Profile::Chat => self.tri_chat = t,
            Profile::Formal => self.tri_formal = t,
        }
        Ok(())
    }

    /// The table `profile` scores with; `None` when none is loaded.
    fn tri_of_profile(&self, profile: Profile) -> Option<&Tri> {
        match profile {
            Profile::Chat => self.tri_chat.as_ref(),
            Profile::Formal => self.tri_formal.as_ref(),
        }
    }

    /// The table `cfg` scores with: its profile's, when one is loaded (a bare lambda has no profile and no table).
    fn tri_table(&self, cfg: Scoring) -> Option<&Tri> {
        self.tri_of_profile(cfg.profile?)
    }

    /// Whether `cfg` scores with a trigram table.
    pub fn uses_tri(&self, cfg: Scoring) -> bool {
        self.tri_table(cfg).is_some()
    }

    /// Whether the loaded trigram table of `profile` is a file mapping (`None`: no table). For the tests of `load_opts`.
    #[cfg(test)]
    pub(crate) fn tri_is_mapped(&self, profile: Profile) -> Option<bool> {
        self.tri_of_profile(profile).map(Tri::is_mapped)
    }

    /// The summary-line suffix `+tri:<table sha8>` of `profile`'s table (same text as reference/proto/lm_eval.py); `None` without a
    /// table. The file is hashed on the first call. Whether a profile scores with the table is `uses_tri(scoring(profile))`.
    pub fn tri_tag(&self, profile: Profile) -> Option<&str> {
        self.tri_of_profile(profile).map(Tri::tag)
    }

    /// How `profile` scores a decoded word (docs/contracts/trigram-core.md, sj-lm-7-pr2.md): the bigram with the side file (β by
    /// `kn_beta`: chat the file's, formal `FORMAL_KN_BETA`), and the trigram level on when the table of that profile is loaded. Only
    /// decoding, `Engine::total_score`, the prediction row (`word_by_id`) and the digit scores (`unigram_back`) take this; the last two use
    /// its lambda and beta, never the table.
    pub fn scoring(&self, profile: Profile) -> Scoring {
        Scoring { lam: profile.lambda(), profile: Some(profile) }
    }

    /// The Kneser-Ney beta `cfg` applies; `None` without a side file.
    fn kn_beta_of(&self, cfg: Scoring) -> Option<f64> {
        self.kn.as_ref().map(|k| kn_beta(k, cfg.profile))
    }

    /// Model bytes plus classes.sjc bytes: magic, the model's SHA-256, the vocabulary size and the exact length must match.
    pub fn parse_with_classes(model: &[u8], classes: &[u8]) -> Result<Lm, LmError> {
        let mut lm = Lm::parse(model)?;
        let sha = lm.sha.clone();
        let mut r = Rd(classes);
        if r.take(8)? != CLASS_MAGIC {
            return Err(LmError::Format);
        }
        let want: String = r.take(32)?.iter().map(|b| format!("{b:02x}")).collect();
        let (k, mu, v) = (r.u32()? as usize, r.f64()?, r.u32()? as usize);
        if want != sha || v != lm.uni.len() || !(0.0..=1.0).contains(&mu) {
            return Err(LmError::Format);
        }
        let k3 = k.checked_add(3).ok_or(LmError::Format)?;
        let cls = r.vec(v, 2, |c| u16::from_le_bytes([c[0], c[1]]))?;
        let emit = r.vec(v, 8, |c| f64::from_le_bytes([c[0], c[1], c[2], c[3], c[4], c[5], c[6], c[7]]))?;
        let pc = r.vec(k3.checked_mul(k3).ok_or(LmError::Format)?, 8, |c| f64::from_le_bytes([c[0], c[1], c[2], c[3], c[4], c[5], c[6], c[7]]))?;
        // The model hash binds the file to the model, not its numbers: a probability outside [0, 1] or a
        // class id out of range is a malformed file, never a silently wrong ranking.
        let prob_ok = |x: &f64| (0.0..=1.0).contains(x);
        if !r.0.is_empty() || cls.iter().any(|&c| c != NO_CLASS && c as usize >= k3) || !emit.iter().all(prob_ok) || !pc.iter().all(prob_ok) {
            return Err(LmError::Format);
        }
        lm.classes = Some(Classes { k3, mu, cls, emit, pc });
        Ok(lm)
    }

    /// Format in tools/build_lm.py. Beyond magic, sizes and trailing bytes, the invariants the lookups
    /// rely on (sorted vocab and entries, offsets in range, ids below V) are checked so no index can panic.
    pub fn parse(bytes: &[u8]) -> Result<Lm, LmError> {
        let mut r = Rd(bytes);
        if r.take(8)? != b"SJLM0001" {
            return Err(LmError::Format);
        }
        let v = r.u32()? as usize;
        let (n, eos_total, d) = (r.u64()?, r.u64()?, r.f64()?);
        if v < 2 || n == 0 || !d.is_finite() {
            return Err(LmError::Format);
        }
        let mut pool = Vec::new();
        let mut voff = Vec::with_capacity(v + 1);
        voff.push(0u32);
        for _ in 0..v {
            let len = r.u16()? as usize;
            let w = r.take(len)?;
            std::str::from_utf8(w).map_err(|_| LmError::Format)?;
            pool.extend_from_slice(w);
            voff.push(u32::try_from(pool.len()).map_err(|_| LmError::Format)?);
        }
        let word = |i: usize| &pool[voff[i] as usize..voff[i + 1] as usize];
        if word(0) != b"<s>" || word(1) != b"</s>" || (3..v).any(|i| word(i - 1) >= word(i)) {
            return Err(LmError::Format);
        }
        let uni = r.u64s(v)?;
        let c = r.u32()? as usize;
        let ctx_id = r.u32s(c)?;
        let ctx_tot = r.u64s(c)?;
        let off = r.u32s(c.checked_add(1).ok_or(LmError::Format)?)?;
        let e = r.u32()? as usize;
        let nxt = r.u32s(e)?;
        let cnt = r.u32s(e)?;
        if !r.0.is_empty() {
            return Err(LmError::Format);
        }
        let vmax = v as u32;
        if ctx_id.iter().any(|&i| i >= vmax) || ctx_id.windows(2).any(|w| w[0] >= w[1]) {
            return Err(LmError::Format);
        }
        if off[0] != 0 || off[c] as usize != e || off.windows(2).any(|w| w[0] > w[1]) || ctx_tot.contains(&0) {
            return Err(LmError::Format);
        }
        if nxt.iter().any(|&i| i >= vmax) {
            return Err(LmError::Format);
        }
        let mut ctx_total = Vec::with_capacity(c);
        let mut ctx_back = Vec::with_capacity(c);
        for i in 0..c {
            let range = off[i] as usize..off[i + 1] as usize;
            if nxt[range.clone()].windows(2).any(|w| w[0] >= w[1]) {
                return Err(LmError::Format);
            }
            // back(v): sum (count - D) in file order, then divide once.
            let mut kept_sum = 0.0f64;
            for j in range {
                kept_sum += cnt[j] as f64 - d;
            }
            let t = ctx_tot[i] as f64;
            ctx_total.push(t);
            ctx_back.push(1.0 - kept_sum / t);
        }
        Ok(Lm {
            n,
            d,
            p_eos: eos_total as f64 / n as f64,
            pool,
            voff,
            uni,
            ctx_id,
            ctx_total,
            ctx_back,
            off,
            nxt,
            cnt,
            classes: None,
            sha: crate::eval::sha256_hex(bytes),
            kn: None,
            tri_formal: None,
            tri_chat: None,
        })
    }

    fn vocab_word(&self, i: usize) -> &[u8] {
        &self.pool[self.voff[i] as usize..self.voff[i + 1] as usize]
    }

    /// Vocabulary id; ids 0 and 1 are the sentence markers, the rest are sorted by UTF-8 bytes.
    pub fn word_id(&self, w: &str) -> Option<u32> {
        match w {
            "<s>" => return Some(0),
            "</s>" => return Some(1),
            _ => {}
        }
        let (mut lo, mut hi) = (2usize, self.uni.len());
        while lo < hi {
            let mid = (lo + hi) / 2;
            match self.vocab_word(mid).cmp(w.as_bytes()) {
                std::cmp::Ordering::Less => lo = mid + 1,
                std::cmp::Ordering::Greater => hi = mid,
                std::cmp::Ordering::Equal => return Some(mid as u32),
            }
        }
        None
    }

    fn ctx_of(&self, id: Option<u32>) -> Ctx {
        Ctx { id, idx: id.and_then(|id| self.ctx_id.binary_search(&id).ok()) }
    }

    /// P(w | context) with the backoff distribution value `pb` (lm.py `prob`): the one probability function every
    /// caller goes through. A kept bigram is unchanged; every other pair (also a context without entries,
    /// `back` = 1) gets `back * ((1 - mu) * pb + mu * Pc[c(v), c(w)] * emit(w))` when both words have a class
    /// (S2k section 4.2; the operation order is the Python one), else `back * pb`.
    ///
    /// Unlike lm.py's `prob`, this includes the Kneser-Ney term below (lm.py applies it in `word()`); decode results are the
    /// same because every word term here goes through this one formula.
    ///
    /// Kneser-Ney (kn.sjkn, docs/contracts/kn-core.md): for a vocabulary word (id >= 2) `pb` first becomes
    /// `beta * N' / sum(N') + (1 - beta) * pb`, so all three branches see it. Sentence end (id 1) and words outside
    /// the vocabulary keep `pb`. Done here once because every word term (decode paths, `word`, `eos`) comes through this function.
    /// The one exception (model-v6 section 2.8 rule 1): a lexicon entry the learner boosted is scored by `prob_learned`, which skips
    /// this substitution so the boosted `pb` is not thrown away.
    fn prob_c(&self, ctx: Ctx, w: Option<u32>, pb: f64) -> f64 {
        self.prob_c_kn(ctx, w, pb, self.kn.as_ref().map(|k| k.beta))
    }

    /// `prob_c` for a learned-boosted entry (model-v6 section 2.8 rule 1): the same formula as with no side file loaded, `pb` the
    /// boosted value. Without a side file it equals `prob_c`.
    fn prob_learned(&self, ctx: Ctx, w: Option<u32>, pb: f64) -> f64 {
        self.prob_c_kn(ctx, w, pb, None)
    }

    /// `prob_c` with the Kneser-Ney substitution at `beta` (`None`: skipped).
    fn prob_c_kn(&self, ctx: Ctx, w: Option<u32>, pb: f64, beta: Option<f64>) -> f64 {
        let pb = match (&self.kn, beta, w) {
            (Some(kn), Some(beta), Some(w)) if w >= 2 => beta * kn.np1(w as usize) as f64 / kn.total as f64 + (1.0 - beta) * pb,
            _ => pb,
        };
        let back = match ctx.idx {
            None => 1.0,
            Some(i) => {
                let range = self.off[i] as usize..self.off[i + 1] as usize;
                let found = w.and_then(|w| self.nxt[range.clone()].binary_search(&w).ok());
                if let Some(k) = found.map(|j| self.cnt[range.start + j]).filter(|&k| k != 0) {
                    return (k as f64 - self.d) / self.ctx_total[i] + self.ctx_back[i] * pb;
                }
                self.ctx_back[i]
            }
        };
        if let (Some(c), Some(v), Some(w)) = (&self.classes, ctx.id, w) {
            let (cv, cw) = (c.cls[v as usize], c.cls[w as usize]);
            if cv != NO_CLASS && cw != NO_CLASS {
                return back * ((1.0 - c.mu) * pb + c.mu * c.pc[cv as usize * c.k3 + cw as usize] * c.emit[w as usize]);
            }
        }
        back * pb
    }

    /// `prob_c` by word strings. Includes the Kneser-Ney term when a side file is loaded; lm.py's `prob` does not (it applies
    /// the term in `word()`), so compare whole word terms (`word`, decode), not this value, across the two.
    pub fn prob(&self, v: &str, w: &str, pb: f64) -> f64 {
        self.prob_c(self.ctx_of(self.word_id(v)), self.word_id(w), pb)
    }

    /// lm.py `unigram_back`: the single-word back-off term of `word` for a word with model id `w` and score `lp`: 10^lp, blended with the
    /// Kneser-Ney side file's continuation probability when the model has one. For callers that need `pb` apart from `prob`
    /// (docs/contracts/digits.md A.3.2: the digit scores sum probabilities over many words, in an order Python fixes).
    /// `cfg` picks the Kneser-Ney beta (`kn_beta`: formal `FORMAL_KN_BETA`; chat and a bare lambda the side file's), as in decoding.
    pub fn unigram_back(&self, cfg: impl Into<Scoring>, w: Option<u32>, lp: f64) -> f64 {
        let pb = pow10(lp);
        match (&self.kn, self.kn_beta_of(cfg.into()), w) {
            (Some(kn), Some(beta), Some(w)) if w >= 2 => beta * kn.np1(w as usize) as f64 / kn.total as f64 + (1.0 - beta) * pb,
            _ => pb,
        }
    }

    /// lm.py `prob`: P(w | context) for a `pb` that is already `unigram_back`'s value (the side file is not applied again, unlike `prob`).
    pub fn prob_back(&self, ctx: Ctx, w: Option<u32>, pb: f64) -> f64 {
        self.prob_c_kn(ctx, w, pb, None)
    }

    /// lm.py `word`: lam * log10 P(w|v) + (1 - lam) * lp.
    pub fn word(&self, lam: f64, v: &str, w: &str, lp: f64) -> f64 {
        word_term(lam, self.prob(v, w, pow10(lp)), lp)
    }

    /// `word` for a word the learner boosted (model-v6 section 2.8 rule 1): no Kneser-Ney substitution. `Engine::total_score` uses it
    /// for the path words `decode_segment_learned` scored that way, so the total still equals the top decode score.
    pub fn word_learned(&self, lam: f64, v: &str, w: &str, lp: f64) -> f64 {
        self.word_learned_by_id(lam, self.word_id(v), self.word_id(w), lp)
    }

    /// `word_learned` for ids the caller resolved (`CappedLexicon::model_id`: an optional-pack word is `None`).
    pub(crate) fn word_learned_by_id(&self, lam: f64, v: Option<u32>, w: Option<u32>, lp: f64) -> f64 {
        word_term(lam, self.prob_learned(self.ctx_of(v), w, pow10(lp)), lp)
    }

    /// The stored trigram context (u, v), if `cfg` scores with the table. `u` `None` is unknown (a left context supplies one word).
    /// `memo` keeps what was found in this call (a context's back-off weight sums all its entries; the same pair comes back at
    /// many positions), so the answer is the one `Tri::context` gives, computed once.
    fn tri_of(&self, cfg: Scoring, u: Option<u32>, v: Option<u32>, memo: &mut TriMemo) -> Option<TriCtx> {
        let t = self.tri_table(cfg)?;
        let key = (u?, v?);
        *memo.0.entry(key).or_insert_with(|| t.context(key.0, key.1))
    }

    /// P3(w | u, v) (trimodel.py `prob`) with the P2 of this model at `cfg`'s Kneser-Ney beta; `tri` is the context `tri_of` found, `None`
    /// is P2 itself, bit for bit what `prob_c` returns with the side file on.
    fn prob_tri(&self, cfg: Scoring, tri: Option<TriCtx>, ctx: Ctx, w: Option<u32>, pb: f64) -> f64 {
        let p2 = self.prob_c_kn(ctx, w, pb, self.kn_beta_of(cfg));
        match (self.tri_table(cfg), tri) {
            (Some(t), Some(c)) => t.prob(c, w, p2),
            _ => p2,
        }
    }

    /// `word` under `cfg` with two history words (`u` first, `None` unknown): trimodel.py `_word`. With `cfg` from a plain lambda
    /// it is `word`.
    pub fn word3(&self, cfg: Scoring, u: Option<&str>, v: &str, w: &str, lp: f64) -> f64 {
        let id = |s: &str| self.word_id(s);
        self.word3_in(&mut TriMemo::default(), cfg, u.and_then(id), id(v), id(w), lp)
    }

    /// `eos` under `cfg` with two history words: trimodel.py `eos`.
    pub fn eos3(&self, cfg: Scoring, u: Option<&str>, v: &str) -> f64 {
        let id = |s: &str| self.word_id(s);
        self.eos3_in(&mut TriMemo::default(), cfg, u.and_then(id), id(v))
    }

    /// `word3` sharing `memo` with the other terms of one pass (`Engine::total_score`). The words come as ids so the caller can
    /// pass `None` for a word the model must not know (`CappedLexicon::model_id`: an optional-pack word).
    pub(crate) fn word3_in(&self, memo: &mut TriMemo, cfg: Scoring, u: Option<u32>, vid: Option<u32>, wid: Option<u32>, lp: f64) -> f64 {
        let tri = self.tri_of(cfg, u, vid, memo);
        word_term(cfg.lam, self.prob_tri(cfg, tri, self.ctx_of(vid), wid, pow10(lp)), lp)
    }

    /// `eos3` sharing `memo`, with ids like `word3_in`.
    pub(crate) fn eos3_in(&self, memo: &mut TriMemo, cfg: Scoring, u: Option<u32>, vid: Option<u32>) -> f64 {
        let tri = self.tri_of(cfg, u, vid, memo);
        cfg.lam * self.prob_tri(cfg, tri, self.ctx_of(vid), Some(1), self.p_eos).log10()
    }

    /// lm.py `eos`: lam * log10 P(</s>|v).
    pub fn eos(&self, lam: f64, v: &str) -> f64 {
        lam * self.prob(v, "</s>", self.p_eos).log10()
    }

    /// Unigram count (0 for unknown words).
    pub fn count(&self, w: &str) -> u64 {
        self.word_id(w).map_or(0, |i| self.uni[i as usize])
    }

    /// Total unigram count N.
    pub fn total(&self) -> u64 {
        self.n
    }

    /// Ids of the words listed after `v` in the model, ascending; empty when `v` has no context entry.
    pub fn successor_ids(&self, v: &str) -> &[u32] {
        let range = self.ctx_of(self.word_id(v)).idx.map_or(0..0, |i| self.off[i] as usize..self.off[i + 1] as usize);
        &self.nxt[range]
    }

    /// `word` for a caller that looked the ids up once: `ctx` from `context_of(v)`, `w` from `word_id`. `cfg` as in `unigram_back`: the
    /// prediction row of a formal engine scores with `FORMAL_KN_BETA` like its decoding (research CLI `--kn-beta 0.75` and lm.profile_lm
    /// did the same); only `cfg.lam` and its beta count, never its trigram table.
    pub fn word_by_id(&self, cfg: impl Into<Scoring>, ctx: Ctx, w: Option<u32>, lp: f64) -> f64 {
        let cfg = cfg.into();
        word_term(cfg.lam, self.prob_c_kn(ctx, w, pow10(lp), self.kn_beta_of(cfg)), lp)
    }

    pub fn context_of(&self, v: &str) -> Ctx {
        self.ctx_of(self.word_id(v))
    }
}

/// Python `10 ** lp` is libm `pow`. With a constant base LLVM rewrites `powf` to `exp10`, which rounds
/// differently in the last bit (seen as 1-ulp score differences), so the base is hidden from the optimizer.
fn pow10(lp: f64) -> f64 {
    std::hint::black_box(10.0f64).powf(lp)
}

/// Python `f"{x:g}"` for a number in [0, 1] (6 significant digits, no trailing zeros): `1`, `0.5`, `0.123457`, `1e-05`.
fn fmt_g(x: f64) -> String {
    let e = format!("{x:.5e}");
    let (m, exp) = e.split_once('e').unwrap_or((&e, "0"));
    let exp: i32 = exp.parse().unwrap_or(0);
    let trim = |s: &str| if s.contains('.') { s.trim_end_matches('0').trim_end_matches('.').to_string() } else { s.to_string() };
    if exp < -4 { format!("{}e-{:02}", trim(m), -exp) } else { trim(&format!("{:.*}", (5 - exp) as usize, x)) }
}

fn word_term(lam: f64, p: f64, lp: f64) -> f64 {
    lam * p.log10() + (1.0 - lam) * lp
}

/// Demotion table (docs/contracts/sw-sensitive-demote.md section 2): `reading<TAB>word<TAB>delta<TAB>
/// category<TAB>source` rows. A word under a reading (syllables joined by `-`), or under `*` (every
/// reading), loses `delta` from its term score in decoding. A specific reading wins over `*`.
/// Only the format is checked here; `CappedLexicon::new` and `Demote::check` also require every row to
/// name an entry of the lexicon. The strict rules are mirrored by `load_demote` in reference/proto/lm.py.
#[derive(Clone, Default)]
pub struct Demote {
    /// (reading or "*", word, delta), in file order.
    rows: Vec<(String, String, f64)>,
}

/// Characters a field may not start or end with (the same set in lm.py): no stray whitespace.
const DEMOTE_EDGE: [char; 6] = [' ', '\t', '\r', '\x0b', '\x0c', '\u{a0}'];

/// `[0-9]+` or `[0-9]+.[0-9]+`, ASCII only (Python's `float` would also take `2_0`, other scripts' digits, ` 2`, `1e1`).
fn plain_decimal(s: &str) -> bool {
    let digits = |t: &str| !t.is_empty() && t.bytes().all(|b| b.is_ascii_digit());
    match s.split_once('.') {
        Some((a, b)) => digits(a) && digits(b),
        None => digits(s),
    }
}

impl Demote {
    /// `None` on a malformed row: not 5 non-empty fields, a field with surrounding whitespace, delta not
    /// a plain decimal that is finite and positive, a repeated (reading, word) key. Lines end at `\n`
    /// with an optional `\r` before it; empty lines and `#` lines are skipped.
    pub fn parse(text: &str) -> Option<Demote> {
        let mut rows: Vec<(String, String, f64)> = Vec::new();
        for line in text.lines().filter(|l| !l.is_empty() && !l.starts_with('#')) {
            let f: Vec<&str> = line.split('\t').collect();
            let [reading, word, delta, category, source] = f[..] else { return None };
            let edge = |s: &str| s.is_empty() || s.starts_with(DEMOTE_EDGE) || s.ends_with(DEMOTE_EDGE);
            if [reading, word, delta, category, source].into_iter().any(edge) || !plain_decimal(delta) {
                return None;
            }
            let delta: f64 = delta.parse().ok().filter(|d: &f64| d.is_finite() && *d > 0.0)?;
            if rows.iter().any(|(r, w, _)| r == reading && w == word) {
                return None;
            }
            rows.push((reading.to_string(), word.to_string(), delta));
        }
        Some(Demote { rows })
    }

    /// The table's rows as positions of `ents` (which must be laid out like `base`): `(position, delta)`
    /// sorted by position, a specific reading overriding `*`. `None` when a row names no entry: a
    /// reading the lexicon lacks, or a word absent under it (for `*`: absent under every reading).
    fn resolve(&self, base: &Lexicon, ents: &[crate::Ent]) -> Option<Vec<(usize, f64)>> {
        let word_of = |e: &crate::Ent| &base.words[e.off as usize..(e.off + e.len) as usize];
        let mut at: std::collections::BTreeMap<usize, f64> = std::collections::BTreeMap::new();
        let stars: HashMap<&str, f64> =
            self.rows.iter().filter(|(r, _, _)| r == "*").map(|(_, w, d)| (w.as_str(), *d)).collect();
        if !stars.is_empty() {
            let mut seen: HashSet<&str> = HashSet::new();
            for (p, e) in ents.iter().enumerate() {
                if let Some((&w, &d)) = stars.get_key_value(word_of(e)) {
                    at.insert(p, d);
                    seen.insert(w);
                }
            }
            if seen.len() != stars.len() {
                return None;
            }
        }
        for (reading, word, d) in self.rows.iter().filter(|(r, _, _)| r != "*") {
            let syls: Vec<String> = reading.split('-').map(str::to_string).collect();
            let r = base.find(&base.ids(&syls))?;
            let mut found = false;
            for p in base.range(r) {
                if word_of(&ents[p]) == word {
                    at.insert(p, *d);
                    found = true;
                }
            }
            if !found {
                return None;
            }
        }
        Some(at.into_iter().collect())
    }

    /// Whether every row names an entry of `lex` (what `Engine::new` requires of demote.tsv).
    pub fn check(&self, lex: &Lexicon) -> bool {
        self.resolve(lex, &lex.ents).is_some()
    }
}

/// The lexicon as decoding sees it: same readings and string pool as the original (shared), with the
/// overlay cap applied to scores and every reading re-sorted (cap_overlay in lm.py). Decoding only;
/// syllable generation and candidate lists keep using the original lexicon.
pub struct CappedLexicon {
    /// V3 prediction index, built on first use, with the identity of the model that built it. Its `&str`s point into
    /// `base`'s heap strings, so this field MUST stay declared before `base`: fields drop in declaration order, and the
    /// index has to go before the strings it borrows.
    index: OnceLock<(crate::predict::Index<'static>, (u64, usize, usize))>,
    base: Arc<Lexicon>,
    ents: Vec<crate::Ent>,
    /// Demotion resolved once per entry: `(position in ents, delta)` sorted by position; empty without a table.
    deltas: Vec<(usize, f64)>,
    /// The forced pack words (docs/contracts/pack-oov.md section 1b: pack rows tagged `PACK_OOV_TAG`): `(off, len)` into
    /// `base.words`, one per distinct string, sorted by the string's bytes. Empty without a pack (the lexicon then behaves exactly as before).
    packs: Vec<(u32, u32)>,
    /// One bit per entry position whose word is in the overlay (`overlay_bits`), or whose score is not main's capped score
    /// (`pack_bits`: a forced pack word, or a pack word the model never saw), so `readings` can redo main's capping; both empty
    /// without a pack.
    overlay_bits: Vec<u64>,
    pack_bits: Vec<u64>,
    /// `Lm::identity` of the model the lexicon was capped with: `readings` must get the same one.
    built_for: (u64, usize, usize),
    /// Evaluation only (`with_reading_decode`, the CLI's `--reading-decode`): the decoder then adds the reading term to every single-character
    /// word. `None` for the engine and every other caller, whose arithmetic is then untouched.
    reading: Option<crate::reading::ReadingDecode>,
}

/// Column 4 of a pack row that the pack builder marks as forced (docs/contracts/pack-oov.md section 1b): the word is unknown to the
/// model when decoding and keeps the pack's score. Other pack rows are capped as in main.
pub const PACK_OOV_TAG: &str = "acg-oov";

/// An overlay word's score capped at its corpus frequency, or lowered by the unseen penalty when the model never saw it.
fn capped_score(lm: &Lm, score: f64, w: &str) -> f64 {
    let c = lm.count(w);
    if c != 0 { score.min((c as f64 / lm.n as f64).log10()) } else { score - UNSEEN_OVERLAY_PENALTY }
}

/// Best first, the one order both the decoder's entries and `readings` sort by (stable, NaN as equal).
fn desc(a: f64, b: f64) -> std::cmp::Ordering {
    b.partial_cmp(&a).unwrap_or(std::cmp::Ordering::Equal)
}

fn bit(bits: &[u64], p: usize) -> bool {
    bits[p / 64] >> (p % 64) & 1 == 1
}

impl CappedLexicon {
    /// Switches the reading term on (experiments/r1-reading-decode, R1d): `base_text` is the text of `mcbpmf-data.txt`, the rows the mc estimate
    /// of the lexicon's floor-score readings is counted from (`reading::ReadingDecode::new`).
    pub fn with_reading_decode(mut self, base_text: &str) -> CappedLexicon {
        self.reading = Some(crate::reading::ReadingDecode::new(&self.base, base_text));
        self
    }

    /// R1g (evaluation only, the CLI's `--reading-exempt`): the (syllable, character) pairs of `exempt_text` get the term 0 (`ReadingDecode::with_exempt`).
    /// Needs `with_reading_decode` first; without it the pairs have nothing to exempt from (`Err`).
    pub fn with_reading_exempt(mut self, exempt_text: &str) -> Result<CappedLexicon, String> {
        let rd = self.reading.take().ok_or("--reading-exempt needs --reading-decode")?;
        self.reading = Some(rd.with_exempt(exempt_text)?);
        Ok(self)
    }

    /// Whether `word` is a forced pack word (a pack row tagged `PACK_OOV_TAG`, docs/contracts/pack-oov.md section 1b).
    pub fn is_pack(&self, word: &str) -> bool {
        let at = |&(off, len): &(u32, u32)| &self.base.words[off as usize..(off + len) as usize];
        self.packs.binary_search_by(|e| at(e).cmp(word)).is_ok()
    }

    /// The model id the decoder scores `word` with: `None` (unknown) for a forced pack word, whose score then comes from the
    /// pack alone; `Lm::word_id` for every other word, unforced pack words included. The decoder (`decode*`, the start word) and `total_score` resolve their word ids
    /// here; prediction, `history()`, the digits layer and 常談 still use the string, unchanged by design (docs/contracts/pack-oov.md).
    pub fn model_id(&self, lm: &Lm, word: &str) -> Option<u32> {
        if self.is_pack(word) { None } else { lm.word_id(word) }
    }

    /// The prediction index (V3), built from this lexicon and `lm` the first time (hundreds of ms, tens of MB). Later
    /// calls must pass the same model (checked in debug builds by `Lm::identity`); the index holds its word ids.
    pub fn predict_index(&self, lm: &Lm) -> &crate::predict::Index<'_> {
        let (idx, built_for) = self.index.get_or_init(|| {
            let idx = crate::predict::Index::new(self, lm);
            // SAFETY: the index only borrows strings owned by `self.base` (an Arc whose heap strings never move while
            // `self` lives). `index` is declared before `base`, so it drops first and nothing dangles during drop. It is
            // handed out only for the lifetime of `&self`.
            let idx = unsafe { std::mem::transmute::<crate::predict::Index<'_>, crate::predict::Index<'static>>(idx) };
            (idx, lm.identity())
        });
        debug_assert!(*built_for == lm.identity(), "the prediction index was built for another model");
        idx
    }

    /// The constructor without packs (`with_packs` is the one with them). `overlay` is the text of overlay-add.tsv; every word in its second
    /// column (whether the entry came from the base or the overlay) is capped at its corpus frequency.
    pub fn new(base: Arc<Lexicon>, overlay: &str, lm: &Lm, demote: Option<&Demote>) -> Option<CappedLexicon> {
        Self::with_packs(base, overlay, "", lm, demote)
    }

    /// `new` with the rows of the enabled optional packs (`pack_text`, the same rows `overlay` also holds, `--extra-overlay` rows
    /// not among them; docs/contracts/pack-oov.md section 1b): the words of rows tagged `PACK_OOV_TAG` keep the pack's score (no cap,
    /// no unseen penalty) and score as unknown to the model (`model_id`); the other pack words are capped as in main, except that a word
    /// the model never saw keeps the pack's score instead of losing `UNSEEN_OVERLAY_PENALTY`. An empty `pack_text` is `new`.
    pub fn with_packs(base: Arc<Lexicon>, overlay: &str, pack_text: &str, lm: &Lm, demote: Option<&Demote>) -> Option<CappedLexicon> {
        let words: HashSet<&str> = overlay.lines().filter_map(|l| l.split('\t').nth(1)).collect();
        let pack: HashSet<&str> = pack_text.lines().filter_map(|l| l.split('\t').nth(1)).collect();
        let forced: HashSet<&str> = pack_text.lines().filter(|l| l.split('\t').nth(3) == Some(PACK_OOV_TAG)).filter_map(|l| l.split('\t').nth(1)).collect();
        let mut packs: Vec<(u32, u32)> = Vec::new();
        let mut ents = base.ents.clone();
        let bits = if pack.is_empty() { 0 } else { ents.len().div_ceil(64) };
        let (mut overlay_bits, mut pack_bits) = (vec![0u64; bits], vec![0u64; bits]);
        for i in 0..base.readings.len() {
            let range = base.range(i);
            for (p, e) in range.clone().zip(&mut ents[range.clone()]) {
                let w = &base.words[e.off as usize..(e.off + e.len) as usize];
                let (in_overlay, in_forced) = (words.contains(w), forced.contains(w));
                let unseen_pack = !in_forced && in_overlay && pack.contains(w) && lm.count(w) == 0;
                if bits != 0 {
                    overlay_bits[p / 64] |= (in_overlay as u64) << (p % 64);
                    pack_bits[p / 64] |= ((in_forced || unseen_pack) as u64) << (p % 64);
                }
                if in_forced {
                    packs.push((e.off, e.len));
                } else if in_overlay && !unseen_pack {
                    e.score = capped_score(lm, e.score, w);
                }
            }
            ents[range].sort_by(|a, b| desc(a.score, b.score));
        }
        let deltas = match demote {
            Some(d) => d.resolve(&base, &ents)?,
            None => Vec::new(),
        };
        packs.sort_by(|a, b| base.words[a.0 as usize..(a.0 + a.1) as usize].cmp(&base.words[b.0 as usize..(b.0 + b.1) as usize]));
        packs.dedup_by(|a, b| base.words[a.0 as usize..(a.0 + a.1) as usize] == base.words[b.0 as usize..(b.0 + b.1) as usize]);
        Some(CappedLexicon { index: OnceLock::new(), base, ents, deltas, packs, overlay_bits, pack_bits, built_for: lm.identity(), reading: None })
    }

    /// Demotion deltas of the entries at positions `range` (usually none): `(position, delta)`.
    fn deltas_in(&self, range: &std::ops::Range<usize>) -> &[(usize, f64)] {
        let lo = self.deltas.partition_point(|&(p, _)| p < range.start);
        let hi = self.deltas.partition_point(|&(p, _)| p < range.end);
        &self.deltas[lo..hi]
    }

    /// Highest capped score of `word` under the reading `key` together with that entry's demotion delta
    /// (0.0 when the table has none; callers apply the on/off flag).
    pub fn best_lp_delta(&self, key: &[String], word: &str) -> Option<(f64, f64)> {
        let r = self.base.find(&self.base.ids(key))?;
        let range = self.base.range(r);
        let dl = self.deltas_in(&range);
        let mut best: Option<(f64, f64)> = None;
        for p in range {
            let e = &self.ents[p];
            if self.word_of(e) == word && best.is_none_or(|(b, _)| e.score > b) {
                best = Some((e.score, dl.iter().find(|&&(q, _)| q == p).map_or(0.0, |x| x.1)));
            }
        }
        best
    }

    pub fn base(&self) -> &Arc<Lexicon> {
        &self.base
    }

    fn word_of(&self, e: &crate::Ent) -> &str {
        &self.base.words[e.off as usize..(e.off + e.len) as usize]
    }

    /// Capped (word, score) list of one reading, best first; empty when the reading is unknown.
    pub fn entries(&self, key: &[String]) -> Vec<(&str, f64)> {
        match self.base.find(&self.base.ids(key)) {
            Some(r) => self.base.range(r).map(|p| (self.word_of(&self.ents[p]), self.ents[p].score)).collect(),
            None => Vec::new(),
        }
    }

    /// Every reading with its capped (word, score) list, best first (build-time use, allocates). Pack words are capped here like any
    /// overlay word: the prediction index and the digits layer read this, and they are unchanged by the pack. A reading that holds a
    /// pack word is rebuilt here the way `new` without the exemption orders it (base order, pack words capped with `lm`, the model the
    /// lexicon was built with, then a stable sort), so the engine keeps no second copy of those lists.
    pub fn readings<'s, 'm>(&'s self, lm: &'m Lm) -> impl Iterator<Item = (Vec<&'s str>, Vec<(&'s str, f64)>)> + use<'s, 'm> {
        debug_assert!(self.built_for == lm.identity(), "readings() needs the model the lexicon was capped with");
        (0..self.base.readings.len()).map(move |r| {
            let key = self.base.key_of(&self.base.readings[r]).iter().map(|&i| self.base.syl_names[i as usize].as_str());
            let range = self.base.range(r);
            // `pack_bits` is set per position; `ents` permutes positions only within a reading, so `range` covers the same bits.
            let list = if !self.pack_bits.is_empty() && range.clone().any(|p| bit(&self.pack_bits, p)) {
                let mut l: Vec<(&str, f64)> = range
                    .map(|p| {
                        let (b, w) = (&self.base.ents[p], self.word_of(&self.base.ents[p]));
                        (w, if bit(&self.overlay_bits, p) { capped_score(lm, b.score, w) } else { b.score })
                    })
                    .collect();
                l.sort_by(|a, b| desc(a.1, b.1));
                l
            } else {
                range.map(|p| (self.word_of(&self.ents[p]), self.ents[p].score)).collect()
            };
            (key.collect(), list)
        })
    }

    /// Highest capped score of `word` under the reading `key` (duplicate entries: the maximum).
    pub fn best_lp(&self, key: &[String], word: &str) -> Option<f64> {
        let mut best: Option<f64> = None;
        for (w, s) in self.entries(key) {
            if w == word && best.is_none_or(|b| s > b) {
                best = Some(s);
            }
        }
        best
    }
}

/// How a decoded segment ends: the sentence end, or the transition into a fixed word on its right.
pub enum End<'a> {
    Eos,
    /// `delta` is the fixed word's demotion (0.0 for none).
    Next { word: &'a str, lp: f64, delta: f64 },
}

/// A word of a decoded path: the text, the lp it was scored with, its demotion delta, the count of syllables it covers (`.3`), and the
/// fuzzy-sound marker (`.4`, docs/contracts/fuzzy-input.md section 3.6): the word was read through the fuzzy-sound alternative of a span
/// with no unknown syllable. The decoder sets the marker from the word's `Source`, never from the delta or the scores, and a learning
/// boost does not take it off.
pub type DecodedWord<'a> = (&'a str, f64, f64, usize, bool);

/// A path: total score and its words.
pub type Scored<'a> = (f64, Vec<DecodedWord<'a>>);

/// A path with a fuzzy word is a fuzzy path.
fn fuzzy_path(ws: &[DecodedWord]) -> bool {
    ws.iter().any(|w| w.4)
}

/// The fuzzy-sound rule of the semantic correction (docs/contracts/fuzzy-input.md section 3.6), the one function the engine's rerank job
/// and the evaluation CLI both use. From the decoder's n-best (one path per surface): `None` when the n-gram first path is a fuzzy path
/// (a rescue: no rerank, the segment shows the n-gram first) or `topk` is 0 (nothing to rerank), otherwise the indices into the n-best of
/// the first `topk` paths that are not fuzzy paths, in n-best order (all of them if fewer; the n-gram first is the first). Without a
/// fuzzy-sound bit no word is a fuzzy word, so this is the first `topk` indices, as before.
pub fn rerank_pool(best: &[Scored<'_>], topk: usize) -> Option<Vec<usize>> {
    if fuzzy_path(&best.first()?.1) {
        return None;
    }
    let pool: Vec<usize> = best.iter().enumerate().filter(|(_, (_, ws))| !fuzzy_path(ws)).map(|(i, _)| i).take(topk).collect();
    (!pool.is_empty()).then_some(pool)
}

struct Hyp<'a> {
    score: f64,
    surface: String,
    words: Vec<DecodedWord<'a>>,
    /// The two history words (docs/contracts/trigram-core.md): `prev` the one before `last`, `None` when unknown.
    prev: Option<u32>,
    last: Option<u32>,
    ctx: Ctx,
    /// The stored trigram context (`prev`, `last`), looked up once per hypothesis; always `None` when the table is not in use.
    tri: Option<TriCtx>,
}

/// Learned boost (S4 §1.4, §12, §14): which records may boost is decided by `Learner::lookup` (single
/// characters only at the exact full two-character key). A learned word scores
/// `best of its reading + eps * w / (w + 1)`, eps being `LEARN_EPS` (6.0) at the exact and last-character
/// levels and `LEARN_EPS_GLOBAL` (0, `Learn::eps_global`) at the global level, for weight `w`
/// (so a heavier record outranks a lighter one: a re-pick halves the displaced word), never
/// less than its own score. The boost enters the score only through `(1 - lambda) * lp` and the
/// backoff term, so the bigram's liking for the common word survives a small value. Measured on the
/// mirror run of eval/learn/cases.tsv (core/tests/engine_learn.rs, 2026-10-05; same-context sentences
/// that follow a re-pick of the pair's other word, key reachable): 2.0 learned 4 of 10, 4.0 learned
/// 8 of 10, 6.0 and 8.0 learned 9 of 10 (smallest value that gets there). The unlearned
/// remainder is a 3-syllable word winning over the taught 2-syllable one.
pub const LEARN_EPS: f64 = 6.0;
/// Boost size for the global level only (§12): enough to break a near tie, not to override a confident
/// language model. 0 switches the level off (s4-learning §13, user decision 2026-10-08): with the word-class
/// term (S2k) every value tried from 0.1 to 0.5 turned at least one correct common sentence wrong, because
/// the class model leaves some of them near ties (`global_eps_table`). Global records are still written.
pub const LEARN_EPS_GLOBAL: f64 = 0.0;

/// What decoding needs to apply learning: the learner, the text just before the segment (only its
/// last two characters matter) and today's day number. The decoded path borrows new words (new-word B) from the
/// learner, so the learner lives as long as the lexicon borrow of the result.
pub struct Learn<'a> {
    pub learner: &'a Learner,
    pub before: &'a str,
    /// The first syllable right of the segment (`None`: the segment ends the composition). A word that
    /// ends the segment is followed by it; under the sentence-start key the reading of the next syllable
    /// is part of the key (rule 8).
    pub after: Option<&'a str>,
    pub today: i64,
    /// ε of the global level: `LEARN_EPS_GLOBAL` in production; the integration tests sweep it through
    /// `Engine::set_eps_global` (a feature or cfg(test) cannot reach integration tests, and it is one
    /// float read per hypothesis key).
    pub eps_global: f64,
}

/// lm.decode generalized to a segment: `start` is the word before the segment (`<s>` for a sentence),
/// `end` the closing term. Best first; `Err(NoPath)` when nothing covers the segment. `scoring` is a `Scoring` (from
/// `Lm::scoring`) or a bare lambda.
pub fn decode_segment<'a>(
    lex: &'a CappedLexicon,
    syls: &[String],
    lm: &Lm,
    scoring: impl Into<Scoring>,
    start: &str,
    end: End<'_>,
    beam: usize,
) -> Result<Vec<Scored<'a>>, Error> {
    decode_segment_learned(lex, syls, lm, scoring, start, end, beam, None, true)
}

/// `decode_segment` with learning (S4 §1.4). With `learn` `None`, or a learner without a record for a
/// span's reading, the arithmetic is exactly that of the unlearned decoder. New words (new-word B §2.4) are
/// enumerated on their reading whatever the context, after the lexicon's entries, scored `newword_score` of their
/// decayed weight (no S4 boost, no demotion; the model sees them like a pack word it never saw), unless the
/// lexicon already has that word under that reading. `demote` switches the
/// lexicon's demotion table on (a delta is subtracted from the word's term, after learning; lp, the
/// backoff and the entries tried are unchanged) or off (bit-identical to a lexicon without the table).
///
/// Trigram level (a profile whose table is loaded, docs/contracts/trigram-core.md): the state is the last two words, so paths
/// are recombined on (surface, previous word, last word). `start` is the history (u, v) of the first word: `<s>` is (`<s>`, `<s>`),
/// any other word is (unknown, that word), so the first word is scored by the bigram alone. The closing term uses the table too:
/// `Eos` and `End::Next` are scored from (second last word, last word); a segment of one word has the start word as the second last,
/// except that a one-word segment with an unknown start (`start` is not `<s>`) closes with the bigram (slice A2 (b'): u is unknown there too).
/// A learned-boosted entry skips both the Kneser-Ney substitution and the table (model-v6 section 2.8 rule 1 extended: the table's
/// back-off weight would shrink the boost the same way).
#[allow(clippy::too_many_arguments)]
pub fn decode_segment_learned<'a>(
    lex: &'a CappedLexicon,
    syls: &[String],
    lm: &Lm,
    scoring: impl Into<Scoring>,
    start: &str,
    end: End<'_>,
    beam: usize,
    learn: Option<&Learn<'a>>,
    demote: bool,
) -> Result<Vec<Scored<'a>>, Error> {
    decode_segment_fuzzy(lex, syls, lm, scoring, start, end, beam, learn, demote, 0, crate::fuzzy::DELTA_SOUND)
}

/// One reading a span's words are looked up under (fuzzy input, docs/contracts/fuzzy-input.md section 2.1).
struct Source {
    /// Index of the reading in the lexicon (`None`: the typed reading is no lexicon reading).
    r: Option<usize>,
    /// Taken off the score of every word of this source (`fuzzy::DELTA_*`; 0 for the exact reading).
    pen: f64,
    /// The reading's syllables when it is a tolerant one (what learning is looked up under); `None` for the typed one.
    names: Option<Vec<String>>,
    /// The fuzzy-word marker (fuzzy-input section 3.6): a tolerant source of a span with no unknown syllable, i.e. a fuzzy-sound
    /// alternative. An adjacent-key source (a span with an unknown syllable) and the typed reading are not.
    sound: bool,
    /// A tolerant source's words (entry positions), already chosen; unused for the exact one.
    picked: Vec<usize>,
}

impl CappedLexicon {
    /// The readings the words of one span are looked up under: the typed reading, then (a span with an unknown syllable, or the
    /// sound pairs in `fuzzy` on) the tolerant ones: words of a reading with one syllable replaced, not already words of the typed
    /// reading, each word at its best score, the best `PER_KEY` over all of them (the research's `tolerant_entries`).
    fn sources(&self, ids: &[u32], span: &[String], fuzzy: u32, delta_sound: f64) -> Vec<Source> {
        let base = &*self.base;
        let exact = base.find(ids);
        let mut out = vec![Source { r: exact, pen: 0.0, names: None, sound: false, picked: Vec::new() }];
        if fuzzy & crate::fuzzy::FUZZY_SOUND == 0 && !ids.contains(&u32::MAX) {
            return out;
        }
        let pen = if ids.contains(&u32::MAX) { crate::fuzzy::DELTA_ADJACENT } else { delta_sound };
        // The words and the readings are chosen by the one function the candidate window uses too (`Lexicon::tolerant_pick`), here with the
        // capped scores and the cap per key.
        let (readings, words) = base.tolerant_pick(ids, span, fuzzy, PER_KEY, PER_KEY, |p| (self.word_of(&self.ents[p]), self.ents[p].score));
        for (a, (r, k)) in readings.iter().enumerate() {
            let picked: Vec<usize> = words.iter().filter(|w| w.3 == a).map(|w| w.2).collect();
            if !picked.is_empty() {
                let names = k.iter().map(|&i| base.syl_names[i as usize].clone()).collect();
                out.push(Source { r: Some(*r), pen, names: Some(names), sound: !ids.contains(&u32::MAX), picked });
            }
        }
        out
    }
}

/// `decode_segment_learned` with fuzzy input (docs/contracts/fuzzy-input.md section 2.1). `fuzzy` holds the sound-pair bits 0-3: a
/// word may also be read through one syllable replaced by its fuzzy-sound alternative, scored `delta_sound` lower (the engine and the app
/// always pass `fuzzy::DELTA_SOUND`; another value is for the evaluation CLI, section 3.5). A syllable the lexicon does not have is
/// always read through its adjacent-key alternatives (`fuzzy::DELTA_ADJACENT` lower), whatever `fuzzy` says: the switch only decides
/// whether such a syllable is accepted when typed. With `fuzzy` 0 and only known syllables the arithmetic is exactly that of
/// `decode_segment_learned` before this existed.
#[allow(clippy::too_many_arguments)]
pub fn decode_segment_fuzzy<'a>(
    lex: &'a CappedLexicon,
    syls: &[String],
    lm: &Lm,
    scoring: impl Into<Scoring>,
    start: &str,
    end: End<'_>,
    beam: usize,
    learn: Option<&Learn<'a>>,
    demote: bool,
    fuzzy: u32,
    delta_sound: f64,
) -> Result<Vec<Scored<'a>>, Error> {
    let base = &*lex.base;
    // New words are 2..=NEWWORD_MAX syllables and their reading may be no lexicon reading at all.
    let fresh_on = learn.filter(|ln| ln.learner.has_new_words());
    let max_l = if fresh_on.is_some() { base.max_len.max(NEWWORD_MAX) } else { base.max_len };
    let cfg: Scoring = scoring.into();
    let lam = cfg.lam;
    let tri_on = lm.tri_table(cfg).is_some();
    let mut memo = TriMemo::default();
    let n = syls.len();
    let ids = base.ids(syls);
    let start_id = lex.model_id(lm, start);
    let start_prev = if start == "<s>" { start_id } else { None };
    let before_tail: String = {
        let t: Vec<char> = learn.map_or("", |l| l.before).chars().rev().take(crate::learn::MAX_CONTEXT).collect();
        t.into_iter().rev().collect()
    };
    let mut hyps: Vec<Vec<Hyp<'a>>> = (0..=n).map(|_| Vec::new()).collect();
    hyps[0].push(Hyp {
        score: 0.0,
        surface: String::new(),
        words: Vec::new(),
        prev: start_prev,
        last: start_id,
        ctx: lm.ctx_of(start_id),
        tri: lm.tri_of(cfg, start_prev, start_id, &mut memo),
    });
    for i in 1..=n {
        let mut cand: Vec<Hyp<'a>> = Vec::new();
        // One path per (surface, last word): the next word's score depends on the last word, so a path that
        // ends in a different word must not be merged away (dec-recombine section 1). With the trigram level it depends on the last
        // two, so the previous word is part of the key too (empty without the table: the key is the old one).
        let mut idx: HashMap<(String, &str, &str), usize> = HashMap::new();
        for l in 1..=max_l.min(i) {
            if hyps[i - l].is_empty() {
                continue;
            }
            for src in lex.sources(&ids[i - l..i], &syls[i - l..i], fuzzy, delta_sound) {
                let found = src.r;
                // A tolerant source is learned under its lexicon reading (fuzzy-input section 2.1): a pick of its word was recorded there.
                let span = src.names.as_deref().unwrap_or(&syls[i - l..i]);
                // New words of this span the lexicon lacks under it (new-word B §2.4), tried after the lexicon's entries.
                let fresh: Vec<(&'a str, f64)> = match fresh_on {
                    Some(ln) if src.names.is_none() && (NEWWORD_MIN..=NEWWORD_MAX).contains(&l) => ln
                        .learner
                        .new_words(span, ln.today)
                        .into_iter()
                        .filter(|(w, _)| found.is_none() || !base.has(span, w))
                        .map(|(w, wt)| (w, newword_score(wt)))
                        .collect(),
                    _ => Vec::new(),
                };
                if found.is_none() && fresh.is_empty() {
                    continue;
                }
                let learned = learn.filter(|ln| found.is_some() && ln.learner.has_reading(span));
                let range = found.map_or(0..0, |r| base.range(r));
                let best = lex.ents.get(range.start).map_or(0.0, |e| e.score);
                let dl = if demote { lex.deltas_in(&range) } else { &[] };
                // Entries to try: the top PER_KEY, plus learned words ranked below it (`extra`: only
                // usable on a path whose context has a learned record for them). A tolerant source has its chosen words and no
                // extras (ponytail: a learned word ranked below its top PER_KEY is not reached through a tolerant reading).
                let mut entries: Vec<(usize, bool)> = match src.names {
                    None => range.clone().take(PER_KEY).map(|p| (p, false)).collect(),
                    Some(_) => src.picked.iter().map(|&p| (p, false)).collect(),
                };
                if let (Some(ln), None) = (learned, &src.names) {
                    let words = ln.learner.words_of(span);
                    let mut seen: HashSet<&str> = entries.iter().map(|&(p, _)| lex.word_of(&lex.ents[p])).collect();
                    for p in range.clone().skip(PER_KEY) {
                        let w = lex.word_of(&lex.ents[p]);
                        if words.binary_search(&w).is_ok() && seen.insert(w) {
                            entries.push((p, true));
                        }
                    }
                }
                // Each hypothesis's learned hits depend only on its context key, not on the entry: look
                // them up once per hypothesis (shared by key) before trying the entries.
                let hits_of: Vec<usize>;
                let mut hits: Vec<(Level, Vec<(&str, f64)>)> = Vec::new();
                if let Some(ln) = learned {
                    let mut by_ctx: HashMap<String, usize> = HashMap::new();
                    // Rule 8: at sentence start the key also holds the syllable after the word.
                    let next = syls.get(i).map(String::as_str).or(ln.after);
                    hits_of = hyps[i - l]
                        .iter()
                        .map(|h| {
                            let key = record_key(context_key(&format!("{before_tail}{}", h.surface)), next);
                            *by_ctx.entry(key).or_insert_with_key(|k| {
                                hits.push(ln.learner.lookup(k, span, ln.today));
                                hits.len() - 1
                            })
                        })
                        .collect();
                } else {
                    hits_of = Vec::new();
                }
                // Lexicon entries (`Some(position)`), then the new words (`None`: no learned boost, no demotion).
                let items = entries.into_iter().map(|(p, extra)| (Some(p), extra, lex.word_of(&lex.ents[p]), lex.ents[p].score));
                for (p, extra, word, lp0) in items.chain(fresh.iter().map(|&(w, lp)| (None, false, w, lp))) {
                    let (wid, pb0) = (lex.model_id(lm, word), pow10(lp0));
                    // The tolerance penalty rides in the delta (subtracted from the word's term, kept in the path), so that
                    // `Engine::total_score` and the rerank job see the score the decoder used.
                    let delta = p.and_then(|p| dl.iter().find(|&&(q, _)| q == p)).map_or(0.0, |x| x.1) + src.pen;
                    // R1d (evaluation only): a single-character word of a lexicon entry adds its reading term to the path score, so the beam prunes on it.
                    // The reading is the one the entry is under (`span`: the typed syllable, or a tolerant source's lexicon reading).
                    let rd_term = match (&lex.reading, p, l) {
                        (Some(rd), Some(_), 1) => rd.term(base, &span[0], word),
                        _ => 0.0,
                    };
                    for (hi, h) in hyps[i - l].iter().enumerate() {
                        let (mut lp, mut pb, mut was_boosted) = (lp0, pb0, false);
                        if let Some(ln) = learned.filter(|_| p.is_some()) {
                            let (lv, ws) = &hits[hits_of[hi]];
                            let eps = if *lv == Level::Global { ln.eps_global } else { LEARN_EPS };
                            // eps 0 switches the level off, for `extra` entries too: `best + 0` would
                            // still lift a word to a tie with the top.
                            match ws.iter().find(|(w, _)| *w == word) {
                                Some(&(_, w)) if eps > 0.0 => {
                                    let boosted = best + eps * (w / (w + 1.0));
                                    if boosted > lp {
                                        lp = boosted;
                                        pb = pow10(lp);
                                        was_boosted = true;
                                    }
                                }
                                _ if extra => continue,
                                _ => {}
                            }
                        }
                        // model-v6 section 2.8 rule 1: a boosted entry skips the Kneser-Ney substitution (its boosted pb would be replaced
                        // by N'/sum(N') and the boost would shrink to the (1 - lam) * lp part); every other entry is scored as before.
                        let prob = if was_boosted { lm.prob_learned(h.ctx, wid, pb) } else { lm.prob_tri(cfg, h.tri, h.ctx, wid, pb) };
                        let sc = h.score + (word_term(lam, prob, lp) - delta) + rd_term;
                        let before = if tri_on { h.words.last().map_or(start, |w| w.0) } else { "" };
                        let key = (format!("{}{word}", h.surface), before, word);
                        match idx.get(&key).copied() {
                            Some(q) if !(sc > cand[q].score) => {}
                            found => {
                                let mut words = h.words.clone();
                                words.push((word, lp, delta, l, src.sound));
                                // The surface is cloned only for a new slot (the index keeps one copy); a replacement moves it.
                                let surface = match found {
                                    Some(_) => key.0,
                                    None => {
                                        let s = key.0.clone();
                                        idx.insert(key, cand.len());
                                        s
                                    }
                                };
                                let nh = Hyp { score: sc, surface, words, prev: h.last, last: wid, ctx: lm.ctx_of(None), tri: None };
                                match found {
                                    Some(q) => cand[q] = nh,
                                    None => cand.push(nh),
                                }
                            }
                        }
                    }
                }
            }
        }
        cand.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal));
        cand.truncate(beam);
        for h in &mut cand {
            h.ctx = lm.ctx_of(h.last);
            h.tri = lm.tri_of(cfg, h.prev, h.last, &mut memo);
        }
        hyps[i] = cand;
    }
    let last = std::mem::take(&mut hyps[n]);
    let closing = match end {
        End::Eos => None,
        End::Next { word, lp, delta } => Some((lex.model_id(lm, word), pow10(lp), lp, delta)),
    };
    let mut out: Vec<(f64, Vec<DecodedWord<'a>>, String)> = last
        .into_iter()
        .map(|h| {
            // (b') a one-word segment from an unknown start closes without the table.
            let tri = if start != "<s>" && h.words.len() == 1 { None } else { h.tri };
            let term = match closing {
                None => lam * lm.prob_tri(cfg, tri, h.ctx, Some(1), lm.p_eos).log10(),
                Some((wid, pb, lp, delta)) => word_term(lam, lm.prob_tri(cfg, tri, h.ctx, wid, pb), lp) - delta,
            };
            (h.score + term, h.words, h.surface)
        })
        .collect();
    if out.is_empty() {
        return Err(Error::NoPath { len: n });
    }
    out.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
    // The n-best has distinct surfaces: keep each surface's best path (dec-recombine section 1.4).
    let mut seen: HashSet<String> = HashSet::new();
    Ok(out.into_iter().filter_map(|(sc, ws, s)| seen.insert(s).then_some((sc, ws))).collect())
}

/// S2h §1: the word that conditions the first word of a composition with no fixed word on its left.
/// `left` is the stored context (`context_key` result, at most 2 Han characters, "" for none). Tries
/// the whole of `left`, then its last character; the first one with bigram history in the model wins.
/// None: `<s>`. A word without history still has a class (S2k), so it could condition through the class
/// term; it is not used, because S2k phase one was measured with this rule (a change is a new experiment).
pub fn history<'a>(left: &'a str, lm: &Lm) -> &'a str {
    let last = left.char_indices().next_back().map_or(0, |(i, _)| i);
    [left, &left[last..]]
        .into_iter()
        .find(|w| !w.is_empty() && lm.ctx_of(lm.word_id(w)).idx.is_some())
        .unwrap_or("<s>")
}

/// lm.decode: whole sentence, beam `beam` (the contract uses `BEAM_S1`).
pub fn decode(
    lex: &CappedLexicon,
    syls: &[String],
    lm: &Lm,
    profile: Profile,
    beam: usize,
) -> Result<Vec<(f64, Vec<String>)>, Error> {
    decode_from(lex, syls, lm, profile, beam, "<s>", true)
}

/// `decode` with the first word conditioned on `start` (lm.py `decode(..., start=)`) and the demotion
/// table on or off (`demote`).
pub fn decode_from(
    lex: &CappedLexicon,
    syls: &[String],
    lm: &Lm,
    profile: Profile,
    beam: usize,
    start: &str,
    demote: bool,
) -> Result<Vec<(f64, Vec<String>)>, Error> {
    Ok(decode_from_fuzzy_scored(lex, syls, lm, profile, beam, start, demote, 0, crate::fuzzy::DELTA_SOUND)?
        .into_iter()
        .map(|(s, ws)| (s, ws.into_iter().map(|(w, ..)| w.to_string()).collect()))
        .collect())
}

/// `decode_from` with the fuzzy flags of `decode_segment_fuzzy` (the evaluation CLI's `--fuzzy`) and its fuzzy-sound penalty `delta_sound`
/// (the CLI's evaluation-only `--fuzzy-delta-sound`; `fuzzy::DELTA_SOUND` by default), and the paths as the decoder has them, fuzzy-word
/// markers included (what `rerank_pool` reads).
#[allow(clippy::too_many_arguments)]
pub fn decode_from_fuzzy_scored<'a>(
    lex: &'a CappedLexicon,
    syls: &[String],
    lm: &Lm,
    profile: Profile,
    beam: usize,
    start: &str,
    demote: bool,
    fuzzy: u32,
    delta_sound: f64,
) -> Result<Vec<Scored<'a>>, Error> {
    decode_segment_fuzzy(lex, syls, lm, lm.scoring(profile), start, End::Eos, beam, None, demote, fuzzy, delta_sound)
}

#[cfg(test)]
mod tests {
    use super::test_tri as tri_bytes;
    use super::*;
    use crate::Lexicon;

    /// Vocab `<s> </s> a b`; contexts `<s>` (total 5: a 3, b 2) and `a` (total 4: `</s>` 2).
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

    /// Python `f"{x:g}"` (the summary line's beta).
    #[test]
    fn fmt_g_matches_python() {
        for (x, want) in [(1.0, "1"), (0.5, "0.5"), (0.0, "0"), (0.25, "0.25"), (0.75, "0.75"), (0.123456789, "0.123457"), (0.0001, "0.0001"), (0.00001234, "1.234e-05"), (1e-5, "1e-05"), (0.999999999, "1")] {
            assert_eq!(fmt_g(x), want, "{x}");
        }
    }

    #[test]
    fn scores_follow_the_formulas() {
        let lm = Lm::parse(&tiny()).unwrap();
        let back = 1.0 - ((3.0 - 0.75) + (2.0 - 0.75)) / 5.0;
        // seen bigram, unseen next word, context without entries, unknown word
        assert_eq!(lm.prob("<s>", "a", 0.1), (3.0 - 0.75) / 5.0 + back * 0.1);
        assert_eq!(lm.prob("a", "b", 0.1), (1.0 - (2.0 - 0.75) / 4.0) * 0.1);
        assert_eq!(lm.prob("b", "a", 0.1), 0.1);
        assert_eq!(lm.prob("zzz", "qqq", 0.1), 0.1);
        let p_eos: f64 = 4.0 / 10.0;
        assert_eq!(lm.eos(0.5, "a"), 0.5 * ((2.0 - 0.75) / 4.0 + (1.0 - (2.0 - 0.75) / 4.0) * p_eos).log10());
        assert_eq!(lm.eos(0.5, "b"), 0.5 * p_eos.log10());
        assert_eq!((lm.count("a"), lm.count("<s>"), lm.count("zzz"), lm.total()), (6, 0, 0, 10));
    }

    /// Classes of the tiny model (K 2, so 5 classes; `<s>` is 3 and `</s>` is 4): a 0, b 1. Emission a 0.6, b 0.4.
    const MU: f64 = 0.75;
    fn pc() -> Vec<f64> {
        (0..25).map(|i| 0.01 * (i + 1) as f64).collect()
    }
    fn tiny_classes(model: &[u8]) -> Vec<u8> {
        test_classes(model, 2, MU, &[3, 4, 0, 1], &[1.0, 1.0, 0.6, 0.4], &pc())
    }
    fn classed() -> Lm {
        Lm::parse_with_classes(&tiny(), &tiny_classes(&tiny())).unwrap()
    }
    /// The class term as the contract writes it, with the operation order of lm.py.
    fn class_p(back: f64, pb: f64, cv: usize, cw: usize, emit: f64) -> f64 {
        back * ((1.0 - MU) * pb + MU * pc()[cv * 5 + cw] * emit)
    }

    /// S2k 4.2 on the tiny model: kept bigrams are untouched; every other pair (also `b`, which has no
    /// context entry) gets the class term, `back` being 1 there; a word without a class or without an id keeps `back * pb`.
    #[test]
    fn class_term_applies_to_unkept_pairs_only() {
        let lm = classed();
        let back_a = 1.0 - (2.0 - 0.75) / 4.0;
        assert_eq!(lm.prob("<s>", "a", 0.1), (3.0 - 0.75) / 5.0 + (1.0 - (3.0 - 0.75 + 2.0 - 0.75) / 5.0) * 0.1, "kept bigram unchanged");
        assert_eq!(lm.prob("a", "b", 0.1), class_p(back_a, 0.1, 0, 1, 0.4), "context without this entry: back * class mix");
        assert_eq!(lm.prob("b", "a", 0.1), class_p(1.0, 0.1, 1, 0, 0.6), "context without entries: back is 1");
        assert_ne!(lm.prob("b", "a", 0.1), 0.1);
        assert_eq!(lm.prob("zzz", "a", 0.1), 0.1, "unknown previous word");
        assert_eq!(lm.prob("b", "qqq", 0.1), 0.1, "unknown word");
        let p_eos: f64 = 4.0 / 10.0;
        assert_eq!(lm.eos(0.5, "a"), 0.5 * ((2.0 - 0.75) / 4.0 + back_a * p_eos).log10(), "kept </s> unchanged");
        assert_eq!(lm.eos(0.5, "b"), 0.5 * class_p(1.0, p_eos, 1, 4, 1.0).log10(), "eos goes through the class term");
        assert_eq!(lm.word(0.5, "b", "a", -1.0), 0.5 * class_p(1.0, 10f64.powf(-1.0), 1, 0, 0.6).log10() + 0.5 * -1.0);
        let c = lm.context_of("b");
        assert_eq!(lm.word_by_id(0.5, c, lm.word_id("a"), -1.0), lm.word(0.5, "b", "a", -1.0), "word_by_id is the same function");
        // A model without classes is the old arithmetic (--no-classes).
        let plain = Lm::parse(&tiny()).unwrap();
        assert_eq!(plain.prob("b", "a", 0.1), 0.1);
    }

    /// The decoder's word terms and both closing terms (sentence end, a fixed next word) use the class term.
    #[test]
    fn decoder_goes_through_the_class_term() {
        let lm = classed();
        let lex = Arc::new(Lexicon::parse(TINY_LEX).unwrap());
        let capped = CappedLexicon::new(lex, "", &lm, Some(&Demote::parse("").unwrap())).unwrap();
        let syls = ["ㄅ".to_string()];
        let run = |end| decode_segment_learned(&capped, &syls, &lm, 0.5, "<s>", end, 64, None, true).unwrap();
        let score = |v: &[Scored], w: &str| v.iter().find(|(_, ws)| ws[0].0 == w).unwrap().0;
        let eos = run(End::Eos);
        assert_eq!(score(&eos, "b"), lm.word(0.5, "<s>", "b", -2.0) + lm.eos(0.5, "b"));
        assert_eq!(score(&eos, "a"), lm.word(0.5, "<s>", "a", -1.0) + lm.eos(0.5, "a"));
        let next = run(End::Next { word: "a", lp: -1.0, delta: 0.0 });
        assert_eq!(score(&next, "b"), lm.word(0.5, "<s>", "b", -2.0) + lm.word(0.5, "b", "a", -1.0));
        // Starting from `b` (no context entry): the first word itself is class-scored.
        let from_b = decode_segment_learned(&capped, &syls, &lm, 0.5, "b", End::Eos, 64, None, true).unwrap();
        assert_eq!(score(&from_b, "a"), lm.word(0.5, "b", "a", -1.0) + lm.eos(0.5, "a"));
    }

    /// R1d (evaluation only): `with_reading_decode` adds LAMBDA * max(t, -CAP) to the score of a single-character word's path, so a rare reading
    /// can lose the first place; the sandhi reading of 一 costs nothing; a path without the switch is the decoder as it was.
    #[test]
    fn reading_decode_adds_the_term_to_the_path_score() {
        use crate::reading::{CAP, LAMBDA};
        let lm = Lm::parse(&tiny()).unwrap();
        // a: best reading x, rare reading y (t -1.1); b has only y; 乙 reads ㄧˊ rarely (t -5.5, clipped to -CAP); 一 reads ㄧˊ as a sandhi reading.
        let text = "x a -0.1\ny a -1.2\ny b -1.5\nㄧ 一 -0.1\nㄧˊ 一 -3.0\nㄧ 乙 -0.5\nㄧˊ 乙 -6.0\n";
        let capped = || CappedLexicon::new(Arc::new(Lexicon::parse(text).unwrap()), "", &lm, Some(&Demote::parse("").unwrap())).unwrap();
        let (off, on) = (capped(), capped().with_reading_decode(text));
        let run = |c: &CappedLexicon, s: &str| -> Vec<(f64, String)> {
            decode_segment_learned(c, &[s.to_string()], &lm, 0.5, "<s>", End::Eos, 64, None, true).unwrap().into_iter().map(|(sc, ws)| (sc, ws[0].0.to_string())).collect()
        };
        let score = |v: &[(f64, String)], w: &str| v.iter().find(|(_, x)| x == w).unwrap().0;
        let (o, n) = (run(&off, "y"), run(&on, "y"));
        assert_eq!(o[0].1, "a", "premise: without the term the common reading's word wins");
        assert_eq!(n[0].1, "b", "the rare reading of a loses the first place");
        assert!((score(&n, "a") - (score(&o, "a") + LAMBDA * -1.1)).abs() < 1e-9);
        assert!((score(&n, "b") - score(&o, "b")).abs() < 1e-12, "b's only reading: its own best, 0");
        let (o, n) = (run(&off, "ㄧˊ"), run(&on, "ㄧˊ"));
        assert!((score(&n, "一") - score(&o, "一")).abs() < 1e-12, "the sandhi reading is free");
        assert!((score(&n, "乙") - (score(&o, "乙") - LAMBDA * CAP)).abs() < 1e-9, "clipped at -CAP");
        // the switch alone changes nothing about a reading with no single-character word, or a word the lexicon has one reading of
        assert_eq!(run(&off, "x").iter().map(|r| r.0.to_bits()).collect::<Vec<_>>(), run(&on, "x").iter().map(|r| r.0.to_bits()).collect::<Vec<_>>());
    }

    /// 4.3: classes.sjc must be this model's (SHA-256), with the right magic and exact length; the parse never panics.
    #[test]
    fn class_file_must_match_the_model() {
        let model = tiny();
        let good = tiny_classes(&model);
        assert!(Lm::parse_with_classes(&model, &good).is_ok());
        let mut other = model.clone();
        other[8] = 5; // another vocabulary size: another model, same everything else
        assert_eq!(Lm::parse_with_classes(&model, &tiny_classes(&other)).err(), Some(LmError::Format), "another model's hash");
        let mut magic = good.clone();
        magic[7] = b'2';
        let mut trailing = good.clone();
        trailing.push(0);
        let mut bad_class = good.clone();
        let at = 8 + 32 + 4 + 8 + 4;
        bad_class[at..at + 2].copy_from_slice(&5u16.to_le_bytes()); // class 5 is outside K + 3
        // Numbers the hash cannot vouch for: mu outside [0, 1], a negative emission, a NaN in P(d|c).
        let v = u32::from_le_bytes(good[52..56].try_into().unwrap()) as usize;
        let mut bad_mu = good.clone();
        bad_mu[44..52].copy_from_slice(&1.5f64.to_le_bytes());
        let mut bad_emit = good.clone();
        bad_emit[56 + 2 * v..56 + 2 * v + 8].copy_from_slice(&(-0.1f64).to_le_bytes());
        let mut bad_pc = good.clone();
        let n = bad_pc.len();
        bad_pc[n - 8..].copy_from_slice(&f64::NAN.to_le_bytes());
        for (name, bytes) in [("magic", &magic), ("trailing byte", &trailing), ("short", &good[..good.len() - 1].to_vec()), ("class out of range", &bad_class), ("empty", &vec![]), ("mu 1.5", &bad_mu), ("negative emit", &bad_emit), ("NaN in Pc", &bad_pc)] {
            assert_eq!(Lm::parse_with_classes(&model, bytes).err(), Some(LmError::Format), "{name}");
        }
    }

    /// 4.3 on disk: `Lm::load` needs classes.sjc beside the model; `load_without_classes` does not.
    #[test]
    fn load_needs_the_class_file_beside_the_model() {
        let dir = std::env::temp_dir().join(format!("shanjie-lm-classes-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let (m, c) = (dir.join("tiny.sjlm"), dir.join("classes.sjc"));
        std::fs::write(&m, tiny()).unwrap();
        assert_eq!(Lm::load(&m).err(), Some(LmError::Io), "no classes.sjc");
        assert!(Lm::load_without_classes(&m).is_ok());
        std::fs::write(&c, tiny_classes(&tiny())).unwrap();
        assert_eq!(Lm::load(&m).unwrap().prob("b", "a", 0.1), classed().prob("b", "a", 0.1), "file round trip");
        std::fs::remove_dir_all(dir).unwrap();
    }

    /// S2h §1 on the tiny model (history only for `<s>` and `a`): whole, then last character, else `<s>`.
    #[test]
    fn history_prefers_whole_then_last_char_then_sentence_start() {
        let lm = Lm::parse(&tiny()).unwrap();
        assert_eq!(history("", &lm), "<s>");
        assert_eq!(history("a", &lm), "a");
        assert_eq!(history("ba", &lm), "a");
        assert_eq!(history("ab", &lm), "<s>");
        assert_eq!(history("b", &lm), "<s>");
        assert_eq!(history("zz", &lm), "<s>");
    }

    const TINY_LEX: &str = "ㄅ a -1.0\nㄆ a -1.0\nㄅ b -2.0\nㄆ b -2.0\n";

    /// Word "a" under two readings, word "b" under both too; "a" is the better word everywhere.
    fn capped_with(table: &str, lm: &Lm) -> Option<CappedLexicon> {
        let lex = Arc::new(Lexicon::parse(TINY_LEX).unwrap());
        CappedLexicon::new(lex, "", lm, Some(&Demote::parse(table).unwrap()))
    }

    fn scores(lex: &CappedLexicon, lm: &Lm, reading: &str, demote: bool) -> Vec<(String, f64)> {
        let syls = [reading.to_string()];
        decode_segment_learned(lex, &syls, lm, 0.5, "<s>", End::Eos, 64, None, demote)
            .unwrap()
            .into_iter()
            .map(|(s, ws)| (ws.iter().map(|w| w.0).collect(), s))
            .collect()
    }

    fn score_of(v: &[(String, f64)], w: &str) -> f64 {
        v.iter().find(|(x, _)| x == w).unwrap().1
    }

    /// Contract sw-sensitive-demote section 4 (a): delta applies only under the listed reading (the same
    /// word under another reading keeps its score, bit for bit); `*` applies under every reading; a
    /// specific reading wins over `*`; the unlisted word is untouched.
    #[test]
    fn delta_applies_only_under_the_listed_reading() {
        let lm = Lm::parse(&tiny()).unwrap();
        let lex = capped_with("ㄅ\ta\t5.0\treading\tt\n*\tb\t0.25\treading\tt\nㄅ\tb\t1.0\treading\tt\n", &lm).unwrap();
        let (on_b, off_b) = (scores(&lex, &lm, "ㄅ", true), scores(&lex, &lm, "ㄅ", false));
        let (on_p, off_p) = (scores(&lex, &lm, "ㄆ", true), scores(&lex, &lm, "ㄆ", false));
        // The standard reading ㄆ of "a" is not charged.
        assert_eq!(score_of(&on_p, "a").to_bits(), score_of(&off_p, "a").to_bits());
        // ㄅ: "a" loses 5.0, "b" loses the specific 1.0 (not the `*` 0.25).
        assert!((score_of(&off_b, "a") - score_of(&on_b, "a") - 5.0).abs() < 1e-12);
        assert!((score_of(&off_b, "b") - score_of(&on_b, "b") - 1.0).abs() < 1e-12);
        // ㄆ: "b" has only the `*` row.
        assert!((score_of(&off_p, "b") - score_of(&on_p, "b") - 0.25).abs() < 1e-12);
        assert_eq!(on_b[0].0, "b", "demoted 'a' no longer first under ㄅ");
        assert_eq!(on_p[0].0, "a", "still first under ㄆ");
        // A fixed word finds its delta from the same entry.
        let key = |s: &str| [s.to_string()];
        assert_eq!(lex.best_lp_delta(&key("ㄅ"), "a"), Some((-1.0, 5.0)));
        assert_eq!(lex.best_lp_delta(&key("ㄆ"), "a"), Some((-1.0, 0.0)));
        assert_eq!(lex.best_lp_delta(&key("ㄆ"), "b"), Some((-2.0, 0.25)));
        assert_eq!(lex.best_lp_delta(&key("ㄆ"), "zz"), None);
    }

    /// Contract section 2: a row naming no lexicon entry fails the load (a reading typed with a space
    /// instead of `-`, a reading the lexicon lacks, a word absent under that reading, a `*` word the
    /// lexicon lacks), and `Demote::check` (what `Engine::new` runs) says the same.
    #[test]
    fn rows_must_name_a_lexicon_entry() {
        let lm = Lm::parse(&tiny()).unwrap();
        let lex = Arc::new(Lexicon::parse("ㄅ-ㄆ ab -1.0\nㄅ a -1.0\n").unwrap());
        let new = |t: &str| {
            let d = Demote::parse(t).unwrap();
            let (built, checked) = (CappedLexicon::new(lex.clone(), "", &lm, Some(&d)).is_some(), d.check(&lex));
            assert_eq!(built, checked);
            built
        };
        assert!(new("ㄅ-ㄆ\tab\t1\treading\ts\n"));
        assert!(new("*\tab\t1\treading\ts\n"));
        assert!(!new("ㄅ ㄆ\tab\t1\treading\ts\n"), "a mistyped reading");
        assert!(!new("ㄆ-ㄆ\tab\t1\treading\ts\n"), "a reading the lexicon lacks");
        assert!(!new("ㄅ\tab\t1\treading\ts\n"), "a word absent under that reading");
        assert!(!new("*\tzz\t1\treading\ts\n"), "a `*` word the lexicon lacks");
        assert!(new(""));
    }

    #[test]
    fn demote_table_parsing() {
        assert!(Demote::parse("# c\n\nㄅ-ㄆ\tab\t2.5\treading\ts\n*\tab\t0.5\treading\ts\n").is_some());
        assert!(Demote::parse("ㄅ\ta\t2\treading\ts\r\n\r\n").is_some(), "CRLF and a blank line");
        for bad in [
            "ㄅ\ta\t1\treading\n",
            "ㄅ\ta\t1\treading\ts\textra\n",
            "ㄅ\ta\t0\treading\ts\n",
            "ㄅ\ta\t0.0\treading\ts\n",
            "ㄅ\ta\t-1\treading\ts\n",
            "ㄅ\ta\tinf\treading\ts\n",
            "ㄅ\ta\tnan\treading\ts\n",
            "ㄅ\ta\tx\treading\ts\n",
            "ㄅ\ta\t2_0\treading\ts\n",
            "ㄅ\ta\t 2\treading\ts\n",
            "ㄅ\ta\t2 \treading\ts\n",
            "ㄅ\ta\t+2\treading\ts\n",
            "ㄅ\ta\t1e1\treading\ts\n",
            "ㄅ\ta\t.5\treading\ts\n",
            "ㄅ\ta\t2.\treading\ts\n",
            "ㄅ\ta\t２\treading\ts\n",
            "ㄅ\ta\t٢\treading\ts\n",
            " ㄅ\ta\t1\treading\ts\n",
            "ㄅ\ta \t1\treading\ts\n",
            "ㄅ\ta\t1\t\ts\n",
            "ㄅ\ta\t1\treading\ts \n",
            "ㄅ\ta\t1\treading\ts\nㄅ\ta\t2\treading\ts\n",
        ] {
            assert!(Demote::parse(bad).is_none(), "{bad:?}");
        }
    }

    #[test]
    fn malformed_files_are_rejected() {
        let good = tiny();
        let mut trailing = good.clone();
        trailing.push(0);
        let mut magic = good.clone();
        magic[7] = b'2';
        assert!(Lm::parse(&trailing).err() == Some(LmError::Format));
        assert!(Lm::parse(&magic).err() == Some(LmError::Format));
        assert!(Lm::parse(&good[..good.len() - 1]).err() == Some(LmError::Format));
        assert!(Lm::parse(&[]).err() == Some(LmError::Format));
    }

    /// A model file from vocabulary (ids from 0; `<s>`, `</s>`, then the words in byte order), unigram counts and
    /// contexts `(id, total, [(next id, count)])` sorted by id. N 20, `</s>` total 4, D 0.75.
    fn lm_bytes(words: &[&str], uni: &[u64], ctxs: &[(u32, u64, &[(u32, u32)])]) -> Vec<u8> {
        let mut b = b"SJLM0001".to_vec();
        b.extend((words.len() as u32).to_le_bytes());
        b.extend(20u64.to_le_bytes());
        b.extend(4u64.to_le_bytes());
        b.extend(0.75f64.to_le_bytes());
        for w in words {
            b.extend((w.len() as u16).to_le_bytes());
            b.extend(w.as_bytes());
        }
        uni.iter().for_each(|u| b.extend(u.to_le_bytes()));
        b.extend((ctxs.len() as u32).to_le_bytes());
        ctxs.iter().for_each(|c| b.extend(c.0.to_le_bytes()));
        ctxs.iter().for_each(|c| b.extend(c.1.to_le_bytes()));
        let mut at = 0u32;
        b.extend(0u32.to_le_bytes());
        for c in ctxs {
            at += c.2.len() as u32;
            b.extend(at.to_le_bytes());
        }
        b.extend(at.to_le_bytes());
        ctxs.iter().flat_map(|c| c.2).for_each(|e| b.extend(e.0.to_le_bytes()));
        ctxs.iter().flat_map(|c| c.2).for_each(|e| b.extend(e.1.to_le_bytes()));
        b
    }

    /// dec-recombine section 1: one path per (surface, last word). `下來分析` has two segmentations. After
    /// two syllables `下來` alone (-0.66) beats `下 | 來` (-1.24), so a decoder that keeps one path per surface
    /// drops `下 | 來`; but `來 -> 分析` is a seen bigram and `下來 -> 分析` is not, so with `分析` the dropped path wins
    /// (-2.76 against -3.66). The n-best still lists the surface once.
    #[test]
    fn keeps_the_path_whose_last_word_scores_the_next_word_better() {
        // ids: <s> </s> 下 下來 來 分析
        let words = ["<s>", "</s>", "下", "下來", "來", "分析"];
        let model = lm_bytes(
            &words,
            &[0, 0, 4, 5, 6, 8],
            &[(0, 9, &[(2, 4), (3, 5)]), (2, 6, &[(4, 6)]), (4, 8, &[(5, 8)])],
        );
        let lm = Lm::parse(&model).unwrap();
        let lex = Arc::new(Lexicon::parse("ㄒㄧㄚˋ 下 -1.0\nㄌㄞˊ 來 -1.0\nㄒㄧㄚˋ-ㄌㄞˊ 下來 -1.0\nㄈㄣ-ㄒㄧ 分析 -3.0\n").unwrap());
        let capped = CappedLexicon::new(lex, "", &lm, Some(&Demote::parse("").unwrap())).unwrap();
        let w = |v, x, lp| lm.word(0.5, v, x, lp);
        // The premise, straight from the model: the merge in the old rule would keep `下來`, and then lose.
        let (split, joined) = (w("<s>", "下", -1.0) + w("下", "來", -1.0), w("<s>", "下來", -1.0));
        assert!(joined > split, "{joined} {split}");
        let (split_all, joined_all) = (split + w("來", "分析", -3.0), joined + w("下來", "分析", -3.0));
        assert!(split_all > joined_all, "{split_all} {joined_all}");
        let syls: Vec<String> = ["ㄒㄧㄚˋ", "ㄌㄞˊ", "ㄈㄣ", "ㄒㄧ"].iter().map(|s| s.to_string()).collect();
        let out = decode_segment_learned(&capped, &syls, &lm, 0.5, "<s>", End::Eos, 64, None, true).unwrap();
        assert_eq!(out.len(), 1, "the surface is listed once");
        let words: Vec<&str> = out[0].1.iter().map(|x| x.0).collect();
        assert_eq!(words, ["下", "來", "分析"]);
        assert_eq!(out[0].0, split_all + lm.eos(0.5, "分析"));
    }

    /// dec-recombine section 1.4: `下來` and `下 | 來` end in different words, so both paths reach the end of
    /// `ㄒㄧㄚˋ ㄌㄞˊ`; the n-best lists the surface once, with the better of the two closed scores.
    #[test]
    fn n_best_lists_a_surface_reached_by_two_last_words_once() {
        let words = ["<s>", "</s>", "下", "下來", "來", "分析"];
        let model = lm_bytes(
            &words,
            &[0, 0, 4, 5, 6, 8],
            &[(0, 9, &[(2, 4), (3, 5)]), (2, 6, &[(4, 6)]), (4, 8, &[(5, 8)])],
        );
        let lm = Lm::parse(&model).unwrap();
        let lex = Arc::new(Lexicon::parse("ㄒㄧㄚˋ 下 -1.0\nㄌㄞˊ 來 -1.0\nㄒㄧㄚˋ-ㄌㄞˊ 下來 -1.0\n").unwrap());
        let capped = CappedLexicon::new(lex, "", &lm, Some(&Demote::parse("").unwrap())).unwrap();
        let w = |v, x, lp| lm.word(0.5, v, x, lp);
        let split = w("<s>", "下", -1.0) + w("下", "來", -1.0) + lm.eos(0.5, "來");
        let joined = w("<s>", "下來", -1.0) + lm.eos(0.5, "下來");
        let syls: Vec<String> = ["ㄒㄧㄚˋ", "ㄌㄞˊ"].iter().map(|s| s.to_string()).collect();
        let out = decode_segment_learned(&capped, &syls, &lm, 0.5, "<s>", End::Eos, 64, None, true).unwrap();
        assert_eq!(out.len(), 1, "two paths, one surface");
        assert_eq!(out[0].0, split.max(joined));
    }
    /// The tiny model's table: (<s>,<s>) -> a 3, b 1 (total 6, two counts pruned); (<s>,a) -> b 2 (total 4); (a,b) -> </s> 1 (total 2).
    fn tiny_tri(model: &[u8]) -> Vec<u8> {
        tri_bytes(model, 0.75, &[((0, 0), 6, &[(2, 3), (3, 1)]), ((0, 2), 4, &[(3, 2)]), ((2, 3), 2, &[(1, 1)])])
    }

    /// docs/contracts/trigram-core.md section 2, acceptance 2: the four situations by hand (kept entry, stored context without the entry,
    /// context not stored, u unknown), for a word and for the closing term. Without the back-off term `back3 * P2` the first two fail.
    #[test]
    fn trigram_terms_follow_the_formula() {
        let model = tiny();
        let mut lm = Lm::parse(&model).unwrap();
        lm.add_tri_for(Profile::Formal, tiny_tri(&model)).unwrap();
        let cfg = Scoring { lam: 0.5, profile: Some(Profile::Formal) };
        let (lp, d) = (-1.0, 0.75);
        let pb = pow10(lp);
        // kept entry: (<s>, <s>) -> a
        let back3 = 1.0 - ((3.0 - d) + (1.0 - d)) / 6.0;
        assert_eq!(lm.word3(cfg, Some("<s>"), "<s>", "a", lp), word_term(0.5, (3.0 - d) / 6.0 + back3 * lm.prob("<s>", "a", pb), lp));
        // the context is stored, the entry is not: (<s>, a) -> a
        let back3 = 1.0 - (2.0 - d) / 4.0;
        assert_eq!(lm.word3(cfg, Some("<s>"), "a", "a", lp), word_term(0.5, back3 * lm.prob("a", "a", pb), lp));
        // the context is not stored: (b, a) -> a, and u unknown: (?, <s>) -> a; both are the bigram term, bit for bit
        assert_eq!(lm.word3(cfg, Some("b"), "a", "a", lp), lm.word(0.5, "a", "a", lp));
        assert_eq!(lm.word3(cfg, None, "<s>", "a", lp), lm.word(0.5, "<s>", "a", lp));
        assert_eq!(lm.word3(cfg, Some("zzz"), "<s>", "a", lp), lm.word(0.5, "<s>", "a", lp), "a word outside the vocabulary is unknown too");
        // the closing term: (a, b) -> </s> is kept, (<s>, a) -> </s> is not
        let back3 = 1.0 - (1.0 - d) / 2.0;
        assert_eq!(lm.eos3(cfg, Some("a"), "b"), 0.5 * ((1.0 - d) / 2.0 + back3 * lm.prob("b", "</s>", lm.p_eos)).log10());
        let back3 = 1.0 - (2.0 - d) / 4.0;
        assert_eq!(lm.eos3(cfg, Some("<s>"), "a"), 0.5 * (back3 * lm.prob("a", "</s>", lm.p_eos)).log10());
        assert_eq!(lm.eos3(cfg, None, "b"), lm.eos(0.5, "b"));
        // a model without the table, or a bare lambda, is the bigram
        let off = Lm::parse(&model).unwrap();
        assert_eq!(off.word3(cfg, Some("<s>"), "<s>", "a", lp), lm.word(0.5, "<s>", "a", lp));
        assert_eq!(lm.word3(Scoring::from(0.5), Some("<s>"), "<s>", "a", lp), lm.word(0.5, "<s>", "a", lp));
        assert_eq!(lm.eos3(Scoring::from(0.5), Some("a"), "b"), lm.eos(0.5, "b"));
    }

    /// Slice A2 (e), the floor: P3 = max(formula, 0.5 * P2). (<s>, <s>) keeps a 6 of 6, so back3 = 1 - (6 - 0.75) / 6 = 0.125: a word the context did not
    /// keep (b, and the closing term </s>) would get 0.125 * P2, and the floor lifts it to 0.5 * P2 (active); the kept word a gets 0.875 + 0.125 * P2, far above
    /// the floor (inactive, the formula is unchanged). The cases above (back3 0.58 to 0.88) are floor-inactive too. The literal 0.5 is on purpose (a changed
    /// constant must fail here).
    #[test]
    fn trigram_floor_lifts_only_what_falls_below_it() {
        let model = tiny();
        let mut lm = Lm::parse(&model).unwrap();
        lm.add_tri_for(Profile::Formal, tri_bytes(&model, 0.75, &[((0, 0), 6, &[(2, 6)])])).unwrap();
        let cfg = Scoring { lam: 0.5, profile: Some(Profile::Formal) };
        let (lp, d) = (-1.0, 0.75);
        let pb = pow10(lp);
        let back3 = 1.0 - (6.0 - d) / 6.0;
        // inactive: the kept word
        let formula = (6.0 - d) / 6.0 + back3 * lm.prob("<s>", "a", pb);
        assert!(formula > 0.5 * lm.prob("<s>", "a", pb), "premise: the floor is below the formula");
        assert_eq!(lm.word3(cfg, Some("<s>"), "<s>", "a", lp), word_term(0.5, formula, lp));
        // active: a word the context did not keep, and the closing term
        let below = back3 * lm.prob("<s>", "b", pb);
        assert!(below < 0.5 * lm.prob("<s>", "b", pb), "premise: the formula is below the floor");
        assert_eq!(lm.word3(cfg, Some("<s>"), "<s>", "b", lp), word_term(0.5, 0.5 * lm.prob("<s>", "b", pb), lp));
        assert_eq!(lm.eos3(cfg, Some("<s>"), "<s>"), 0.5 * (0.5 * lm.prob("<s>", "</s>", lm.p_eos)).log10());
        // context not stored: the bigram, bit for bit (no floor)
        assert_eq!(lm.word3(cfg, Some("a"), "<s>", "b", lp), lm.word(0.5, "<s>", "b", lp));
    }

    /// The setting each profile scores with: the profile's λ and the profile itself (which picks the Kneser-Ney β and the table), and its own
    /// table when it has one (formal `trigram.sjt3`, chat `trigram-chat.sjt3`). One profile's table never switches the other on.
    #[test]
    fn scoring_follows_the_profile() {
        let model = tiny();
        let mut lm = Lm::parse(&model).unwrap();
        let sc = |lam, p| Scoring { lam, profile: Some(p) };
        let on = |lm: &Lm, p| lm.uses_tri(lm.scoring(p));
        assert_eq!(lm.scoring(Profile::Formal), sc(LAMBDA_FORMAL, Profile::Formal));
        assert_eq!(lm.scoring(Profile::Chat), sc(LAMBDA_CHAT, Profile::Chat));
        assert!(!on(&lm, Profile::Formal) && !on(&lm, Profile::Chat), "no table");
        lm.add_tri_for(Profile::Formal, tiny_tri(&model)).unwrap();
        assert!(on(&lm, Profile::Formal) && !on(&lm, Profile::Chat), "the formal table does not switch chat on");
        lm.add_tri_for(Profile::Chat, tiny_tri(&model)).unwrap();
        assert!(on(&lm, Profile::Formal) && on(&lm, Profile::Chat));
        let mut chat_only = Lm::parse(&model).unwrap();
        chat_only.add_tri_for(Profile::Chat, tiny_tri(&model)).unwrap();
        assert!(!on(&chat_only, Profile::Formal) && on(&chat_only, Profile::Chat), "the chat table does not switch formal on");
        assert!(lm.tri_tag(Profile::Formal).unwrap().starts_with("+tri:") && lm.tri_tag(Profile::Formal).unwrap().len() == 13);
        assert_eq!(chat_only.tri_tag(Profile::Formal), None);
        assert_eq!(Scoring::from(0.5), Scoring { lam: 0.5, profile: None }, "a bare lambda: the bigram with the side file as loaded");
        assert!(!lm.uses_tri(Scoring::from(0.5)), "a bare lambda has no table");
    }

    /// Each profile scores with its own table: chat with a table that keeps (<s>,<s>) -> a and formal with one that keeps (<s>,<s>) -> b get
    /// different word terms for the same words, and each equals the model that has only that table. A swapped lookup fails all three.
    #[test]
    fn each_profile_scores_with_its_own_table() {
        let model = tiny();
        let (chat_t, formal_t) = (tri_bytes(&model, 0.75, &[((0, 0), 6, &[(2, 6)])]), tri_bytes(&model, 0.75, &[((0, 0), 6, &[(3, 6)])]));
        let mut both = Lm::parse(&model).unwrap();
        both.add_tri_for(Profile::Chat, chat_t.clone()).unwrap();
        both.add_tri_for(Profile::Formal, formal_t.clone()).unwrap();
        let (mut chat_only, mut formal_only) = (Lm::parse(&model).unwrap(), Lm::parse(&model).unwrap());
        chat_only.add_tri_for(Profile::Chat, chat_t).unwrap();
        formal_only.add_tri_for(Profile::Formal, formal_t).unwrap();
        let (c, f) = (both.scoring(Profile::Chat), both.scoring(Profile::Formal));
        let term = |lm: &Lm, cfg, w| lm.word3(cfg, Some("<s>"), "<s>", w, -1.0).to_bits();
        for w in ["a", "b"] {
            assert_eq!(term(&both, c, w), term(&chat_only, chat_only.scoring(Profile::Chat), w), "chat {w}");
            assert_eq!(term(&both, f, w), term(&formal_only, formal_only.scoring(Profile::Formal), w), "formal {w}");
        }
        assert_ne!(term(&both, c, "a"), term(&both, Scoring { lam: LAMBDA_CHAT, ..f }, "a"), "the two tables score a differently");
        assert_ne!(term(&both, c, "b"), term(&both, Scoring { lam: LAMBDA_CHAT, ..f }, "b"));
    }

    /// A table that is not exactly what the format and the model allow is `TriTable`, never read.
    #[test]
    fn bad_trigram_tables_are_refused() {
        let model = tiny();
        let good = tiny_tri(&model);
        assert!(Lm::parse(&model).unwrap().add_tri_for(Profile::Formal, good.clone()).is_ok());
        let patch = |at: usize, with: &[u8]| {
            let mut b = good.clone();
            b[at..at + with.len()].copy_from_slice(with);
            b
        };
        // Layout: head 60, key u64[3] at 60, total u64[3] at 84, off u32[4] at 108, next u32[4] at 124, count u32[4] at 140.
        let cases: Vec<(&str, Vec<u8>)> = vec![
            ("magic", patch(7, b"2")),
            ("V", patch(8, &5u32.to_le_bytes())),
            ("model hash", patch(20, &[0xAB])),
            ("D above 1", patch(12, &1.5f64.to_le_bytes())),
            ("D negative", patch(12, &(-0.1f64).to_le_bytes())),
            ("D NaN", patch(12, &f64::NAN.to_le_bytes())),
            ("D 0.5", patch(12, &0.5f64.to_le_bytes())),
            ("D 0.76", patch(12, &0.76f64.to_le_bytes())),
            ("short", good[..good.len() - 4].to_vec()),
            ("trailing byte", [good.as_slice(), &[0]].concat()),
            ("header only", good[..60].to_vec()),
            ("empty", vec![]),
            ("keys not ascending", patch(60, &9u64.to_le_bytes())),
            ("key out of range", patch(76, &16u64.to_le_bytes())),
            ("zero total", patch(84, &0u64.to_le_bytes())),
            ("counts above the total", patch(84, &3u64.to_le_bytes())), // context 0 keeps 3 + 1
            ("first offset", patch(108, &1u32.to_le_bytes())),
            ("last offset", patch(120, &3u32.to_le_bytes())),
            ("offsets decrease", patch(112, &4u32.to_le_bytes())),
            ("next id out of range", patch(124, &4u32.to_le_bytes())),
            ("next ids not ascending", patch(124, &3u32.to_le_bytes())),
        ];
        for (name, b) in cases {
            assert_eq!(Lm::parse(&model).unwrap().add_tri_for(Profile::Formal, b).err(), Some(LmError::TriTable), "{name}");
        }
        // Another model with the same V.
        let mut other = model.clone();
        other[20] ^= 1;
        assert_eq!(Lm::parse(&other).unwrap().add_tri_for(Profile::Formal, good).err(), Some(LmError::TriTable));
    }

    /// docs/contracts/trigram-mmap.md section 3.2: `load_opts` maps `trigram.sjt3` (the source kind is checked, not only the result), the mapped
    /// table scores exactly like the same bytes in memory, and a file that is empty, cut short or has a bad magic is `TriTable` through the mapping.
    #[test]
    fn load_opts_maps_the_trigram_table() {
        let dir = std::env::temp_dir().join(format!("shanjie-lm-mmap-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let (m, t) = (dir.join("tiny.sjlm"), dir.join("trigram.sjt3"));
        let model = tiny();
        let good = tiny_tri(&model);
        std::fs::write(&m, &model).unwrap();
        let _ = std::fs::remove_file(&t);
        assert_eq!(Lm::load_opts(&m, false, true, &[Profile::Formal]).unwrap().tri_is_mapped(Profile::Formal), None, "no file: no table");
        std::fs::write(&t, &good).unwrap();
        let mapped = Lm::load_opts(&m, false, true, &[Profile::Formal]).unwrap();
        assert_eq!(mapped.tri_is_mapped(Profile::Formal), Some(true), "load_opts must map the table, not read it");
        assert_eq!(Lm::load_opts(&m, false, true, &[]).unwrap().tri_is_mapped(Profile::Formal), None, "--no-tri: not read");
        assert_eq!(Lm::load_opts(&m, false, true, &[Profile::Chat]).unwrap().tri_is_mapped(Profile::Formal), None, "only the named profile's file is read");
        let tc = dir.join("trigram-chat.sjt3");
        std::fs::write(&tc, &good).unwrap();
        let chat = Lm::load_opts(&m, false, true, &[Profile::Chat]).unwrap();
        assert_eq!((chat.tri_is_mapped(Profile::Chat), chat.tri_is_mapped(Profile::Formal)), (Some(true), None), "chat maps trigram-chat.sjt3 only");
        drop(chat);
        std::fs::remove_file(&tc).unwrap();
        let mut mem = Lm::parse(&model).unwrap();
        mem.add_tri_for(Profile::Formal, good.clone()).unwrap();
        assert_eq!(mem.tri_is_mapped(Profile::Formal), Some(false));
        assert_eq!(mapped.tri_tag(Profile::Formal), mem.tri_tag(Profile::Formal));
        let cfg = Scoring { lam: 0.5, profile: Some(Profile::Formal) };
        for u in [None, Some("<s>"), Some("a"), Some("b")] {
            for v in ["<s>", "a", "b"] {
                for w in ["a", "b", "<s>"] {
                    assert_eq!(mapped.word3(cfg, u, v, w, -1.0).to_bits(), mem.word3(cfg, u, v, w, -1.0).to_bits(), "{u:?} {v} {w}");
                }
                assert_eq!(mapped.eos3(cfg, u, v).to_bits(), mem.eos3(cfg, u, v).to_bits(), "eos {u:?} {v}");
            }
        }
        drop(mapped); // the loop below rewrites the file in place: nothing may still map it
        let mut bad_magic = good.clone();
        bad_magic[7] = b'2';
        for (name, bytes) in [("empty", vec![]), ("truncated", good[..good.len() - 4].to_vec()), ("bad magic", bad_magic)] {
            std::fs::write(&t, &bytes).unwrap();
            assert_eq!(Lm::load_opts(&m, false, true, &[Profile::Formal]).err(), Some(LmError::TriTable), "{name}");
        }
        std::fs::remove_file(&t).unwrap();
        std::os::unix::fs::symlink(dir.join("nowhere"), &t).unwrap();
        assert_eq!(Lm::load_opts(&m, false, true, &[Profile::Formal]).err(), Some(LmError::TriTable), "dangling symlink");
        std::fs::remove_file(&t).unwrap();
        std::fs::create_dir(&t).unwrap();
        assert_eq!(Lm::load_opts(&m, false, true, &[Profile::Formal]).err(), Some(LmError::TriTable), "directory");
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// The words of the n-best of one typed syllable with their fuzzy markers: (word, delta, fuzzy), best path first.
    fn marked(lex_text: &str, demote: &str, syl: &str, flags: u32) -> Vec<(String, f64, bool)> {
        let lm = Lm::parse(&tiny()).unwrap();
        let lex = Arc::new(Lexicon::parse(lex_text).unwrap());
        let capped = CappedLexicon::new(lex, "", &lm, Some(&Demote::parse(demote).unwrap())).unwrap();
        decode_from_fuzzy_scored(&capped, &[syl.to_string()], &lm, Profile::Chat, 64, "<s>", true, flags, crate::fuzzy::DELTA_SOUND)
            .unwrap()
            .into_iter()
            .map(|(_, ws)| (ws[0].0.to_string(), ws[0].2, ws[0].4))
            .collect()
    }

    /// Fuzzy-input section 3.6, the marker. (a) A word of the typed reading is no fuzzy word whatever its demotion: here the demotion is
    /// exactly the sound penalty (2.5), so a marker inferred from the delta would take it for one. (c) The same word under the fuzzy-sound
    /// alternative reading is one (and carries that same delta).
    #[test]
    fn marker_a_typed_reading_word_with_the_sound_penalty_as_its_demotion_is_not_fuzzy_and_a_sound_alternative_is() {
        assert_eq!(crate::fuzzy::DELTA_SOUND, 2.5, "premise: the demotion below equals the sound penalty");
        let v = marked("ㄓ a -1.0\nㄗ b -1.0\n", "ㄓ\ta\t2.5\treading\tt\n", "ㄓ", 0b0010);
        let get = |w: &str| v.iter().find(|x| x.0 == w).unwrap_or_else(|| panic!("no path {w}: {v:?}")).clone();
        assert_eq!(get("a"), ("a".to_string(), 2.5, false), "(a) demoted, typed reading");
        assert_eq!(get("b"), ("b".to_string(), 2.5, true), "(c) the sound alternative");
    }

    /// Section 3.6 (b): a word read through the adjacent-key alternative of a syllable the lexicon lacks (it too carries a penalty, 2) is
    /// no fuzzy word, with the sound pairs on or off.
    #[test]
    fn marker_an_adjacent_key_word_is_not_fuzzy() {
        for flags in [0b10000, 0b11111] {
            let v = marked("ㄉ b -1.0\n", "", "ㄅ", flags);
            assert_eq!(v, [("b".to_string(), crate::fuzzy::DELTA_ADJACENT, false)], "flags {flags}");
        }
    }

    /// Review item 6: the decoder's tolerant words (`CappedLexicon::sources`) and the candidate window's (`Lexicon::tolerant_words`) come
    /// from one function. Here fourteen words stand under the alternative ㄓ of the typed ㄗ (and one, the typed reading's own, under ㄗ):
    /// the window lists the fourteen, best first, and the decoder takes the same words in the same order up to its `PER_KEY` cap; the
    /// typed reading's word is in neither.
    #[test]
    fn the_decoder_and_the_window_list_the_same_tolerant_words() {
        let chars: Vec<char> = (0..14).map(|i| char::from_u32(0x4e00 + i).unwrap()).collect();
        let mut text: String = chars.iter().enumerate().map(|(i, c)| format!("ㄓ {c} -{}\n", 1.0 + i as f64 / 10.0)).collect();
        text.push_str("ㄗ 乙 -1.0\nㄓ 乙 -1.5\n");
        let lm = Lm::parse(&tiny()).unwrap();
        let lex = Arc::new(Lexicon::parse(&text).unwrap());
        let capped = CappedLexicon::new(lex.clone(), "", &lm, None).unwrap();
        let syls = vec!["ㄗ".to_string()];
        let window: Vec<&str> = lex.tolerant_words(&syls, 0b0010).into_iter().map(|(w, _)| w).collect();
        assert_eq!(window.len(), 14, "the window lists every tolerant word: {window:?}");
        assert!(!window.contains(&"乙"), "a word of the typed reading is not a tolerant one");
        let picked: Vec<&str> = capped
            .sources(&lex.ids(&syls), &syls, 0b0010, crate::fuzzy::DELTA_SOUND)
            .iter()
            .flat_map(|src| src.picked.iter().map(|&p| capped.word_of(&capped.ents[p])))
            .collect();
        assert_eq!(picked, window[..PER_KEY], "the decoder takes the window's best PER_KEY, in the same order");
    }

    /// The pool rule on hand-made n-bests: a fuzzy first path is a skip; otherwise the first `topk` paths without a fuzzy word, in order.
    #[test]
    fn rerank_pool_takes_the_first_non_fuzzy_paths_and_skips_a_fuzzy_first() {
        let path = |f: bool| (0.0, vec![("x", 0.0, 0.0, 1, false), ("y", 0.0, 0.0, 1, f)]);
        let nb = |fz: &[bool]| fz.iter().map(|&f| path(f)).collect::<Vec<Scored>>();
        assert_eq!(rerank_pool(&nb(&[false, true, false, false, true, false]), 3), Some(vec![0, 2, 3]));
        assert_eq!(rerank_pool(&nb(&[false, true, false]), 8), Some(vec![0, 2]), "fewer than topk: all of them");
        assert_eq!(rerank_pool(&nb(&[true, false, false]), 8), None, "fuzzy first: no rerank");
        assert_eq!(rerank_pool(&nb(&[false, false, false, false]), 2), Some(vec![0, 1]));
        assert_eq!(rerank_pool(&[], 8), None);
        assert_eq!(rerank_pool(&nb(&[false, false]), 0), None, "topk 0: no rerank, not an empty pool");
    }
}
