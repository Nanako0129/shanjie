//! S2c: port of reference/proto/lm.py (SJLM0001 reader, bigram scoring, cap_overlay, LM beam decode).
//! The Python code is ground truth; float operation order is kept on purpose (no fusing or reordering).
//! R2: errors carry no content; nothing here formats input text.

use crate::learn::{context_key, Learner, Level};
use crate::{Error, Lexicon, PER_KEY};
use std::collections::{HashMap, HashSet};
use std::fmt;
use std::path::Path;
use std::sync::Arc;

pub const LAMBDA_CHAT: f64 = 0.5;
pub const LAMBDA_FORMAL: f64 = 0.7;
/// Words the corpus never saw are pushed down by this much (lm.py UNSEEN_OVERLAY_PENALTY).
const UNSEEN_OVERLAY_PENALTY: f64 = 1.0;

#[derive(Clone, Copy, PartialEq, Eq)]
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

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LmError {
    Io,
    Format,
}

impl fmt::Display for LmError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            LmError::Io => "cannot read language model file",
            LmError::Format => "bad language model file",
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
    pub fn load(path: &Path) -> Result<Lm, LmError> {
        Lm::parse(&std::fs::read(path).map_err(|_| LmError::Io)?)
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

    fn ctx_of(&self, id: Option<u32>) -> Option<usize> {
        self.ctx_id.binary_search(&id?).ok()
    }

    /// P(w | context) with the backoff distribution value `pb` (lm.py `prob`).
    fn prob_c(&self, ctx: Option<usize>, w: Option<u32>, pb: f64) -> f64 {
        let Some(i) = ctx else { return pb };
        let back = self.ctx_back[i];
        let range = self.off[i] as usize..self.off[i + 1] as usize;
        let found = w.and_then(|w| self.nxt[range.clone()].binary_search(&w).ok());
        match found.map(|j| self.cnt[range.start + j]) {
            Some(k) if k != 0 => (k as f64 - self.d) / self.ctx_total[i] + back * pb,
            _ => back * pb,
        }
    }

    pub fn prob(&self, v: &str, w: &str, pb: f64) -> f64 {
        self.prob_c(self.ctx_of(self.word_id(v)), self.word_id(w), pb)
    }

    /// lm.py `word`: lam * log10 P(w|v) + (1 - lam) * lp.
    pub fn word(&self, lam: f64, v: &str, w: &str, lp: f64) -> f64 {
        word_term(lam, self.prob(v, w, pow10(lp)), lp)
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
        let range = self.ctx_of(self.word_id(v)).map_or(0..0, |i| self.off[i] as usize..self.off[i + 1] as usize);
        &self.nxt[range]
    }

    /// `word` for a caller that looked the ids up once: `ctx` from `context_of(v)`, `w` from `word_id`.
    pub fn word_by_id(&self, lam: f64, ctx: Option<usize>, w: Option<u32>, lp: f64) -> f64 {
        word_term(lam, self.prob_c(ctx, w, pow10(lp)), lp)
    }

    pub fn context_of(&self, v: &str) -> Option<usize> {
        self.ctx_of(self.word_id(v))
    }
}

/// Python `10 ** lp` is libm `pow`. With a constant base LLVM rewrites `powf` to `exp10`, which rounds
/// differently in the last bit (seen as 1-ulp score differences), so the base is hidden from the optimizer.
fn pow10(lp: f64) -> f64 {
    std::hint::black_box(10.0f64).powf(lp)
}

fn word_term(lam: f64, p: f64, lp: f64) -> f64 {
    lam * p.log10() + (1.0 - lam) * lp
}

/// The lexicon as decoding sees it: same readings and string pool as the original (shared), with the
/// overlay cap applied to scores and every reading re-sorted (cap_overlay in lm.py). Decoding only;
/// syllable generation and candidate lists keep using the original lexicon.
pub struct CappedLexicon {
    base: Arc<Lexicon>,
    ents: Vec<crate::Ent>,
}

impl CappedLexicon {
    /// The single constructor. `overlay` is the text of overlay-add.tsv; every word in its second
    /// column (whether the entry came from the base or the overlay) is capped at its corpus frequency.
    pub fn new(base: Arc<Lexicon>, overlay: &str, lm: &Lm) -> CappedLexicon {
        let words: HashSet<&str> = overlay.lines().filter_map(|l| l.split('\t').nth(1)).collect();
        let mut ents = base.ents.clone();
        for i in 0..base.readings.len() {
            let range = base.range(i);
            for e in &mut ents[range.clone()] {
                let w = &base.words[e.off as usize..(e.off + e.len) as usize];
                if words.contains(w) {
                    let c = lm.count(w);
                    e.score = if c != 0 {
                        e.score.min((c as f64 / lm.n as f64).log10())
                    } else {
                        e.score - UNSEEN_OVERLAY_PENALTY
                    };
                }
            }
            ents[range].sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal));
        }
        CappedLexicon { base, ents }
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

    /// Every reading with its capped (word, score) list, best first (build-time use, allocates).
    pub fn readings(&self) -> impl Iterator<Item = (Vec<&str>, Vec<(&str, f64)>)> + '_ {
        (0..self.base.readings.len()).map(|r| {
            let key = self.base.key_of(&self.base.readings[r]).iter().map(|&i| self.base.syl_names[i as usize].as_str());
            (key.collect(), self.base.range(r).map(|p| (self.word_of(&self.ents[p]), self.ents[p].score)).collect())
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
    Next { word: &'a str, lp: f64 },
}

/// A path: total score and its words with the lp each was scored with.
pub type Scored<'a> = (f64, Vec<(&'a str, f64)>);

struct Hyp<'a> {
    score: f64,
    surface: String,
    words: Vec<(&'a str, f64)>,
    last: Option<u32>,
    ctx: Option<usize>,
}

/// Learned boost (S4 §1.4, §12): which records may boost is decided by `Learner::lookup` (single
/// characters only at the exact full key and never under "^"). A learned word scores
/// `best of its reading + eps * w / (w + 1)`, eps being `LEARN_EPS` (6.0) at the exact and last-character
/// levels and `LEARN_EPS_GLOBAL` (0.5, `Learn::eps_global`) at the global level, for weight `w`
/// (so a heavier record outranks a lighter one: a re-pick halves the displaced word), never
/// less than its own score. The boost enters the score only through `(1 - lambda) * lp` and the
/// backoff term, so the bigram's liking for the common word survives a small value. Measured on the
/// mirror run of eval/learn/cases.tsv (core/tests/engine_learn.rs, 2026-10-05; same-context sentences
/// that follow a re-pick of the pair's other word, key reachable): 2.0 learned 4 of 10, 4.0 learned
/// 8 of 10, 6.0 and 8.0 learned 9 of 10 (smallest value that gets there). The unlearned
/// remainder is a 3-syllable word winning over the taught 2-syllable one.
pub const LEARN_EPS: f64 = 6.0;
/// Boost size for the global level only (§12): enough to break a near tie, not to override a confident
/// language model. Chosen from the table in the contract §12 (smallest value with 0 global pollution
/// and a non-zero global learn rate).
pub const LEARN_EPS_GLOBAL: f64 = 0.5;

/// What decoding needs to apply learning: the learner, the text just before the segment (only its
/// last two characters matter) and today's day number.
pub struct Learn<'a> {
    pub learner: &'a Learner,
    pub before: &'a str,
    pub today: i64,
    /// ε of the global level: `LEARN_EPS_GLOBAL` in production; the integration tests sweep it through
    /// `Engine::set_eps_global` (a feature or cfg(test) cannot reach integration tests, and it is one
    /// float read per hypothesis key).
    pub eps_global: f64,
}

/// lm.decode generalized to a segment: `start` is the word before the segment (`<s>` for a sentence),
/// `end` the closing term. Best first; `Err(NoPath)` when nothing covers the segment.
pub fn decode_segment<'a>(
    lex: &'a CappedLexicon,
    syls: &[String],
    lm: &Lm,
    lam: f64,
    start: &str,
    end: End<'_>,
    beam: usize,
) -> Result<Vec<Scored<'a>>, Error> {
    decode_segment_learned(lex, syls, lm, lam, start, end, beam, None)
}

/// `decode_segment` with learning (S4 §1.4). With `learn` `None`, or a learner without a record for a
/// span's reading, the arithmetic is exactly that of the unlearned decoder.
#[allow(clippy::too_many_arguments)]
pub fn decode_segment_learned<'a>(
    lex: &'a CappedLexicon,
    syls: &[String],
    lm: &Lm,
    lam: f64,
    start: &str,
    end: End<'_>,
    beam: usize,
    learn: Option<&Learn<'_>>,
) -> Result<Vec<Scored<'a>>, Error> {
    let base = &*lex.base;
    let n = syls.len();
    let ids = base.ids(syls);
    let start_id = lm.word_id(start);
    let before_tail: String = {
        let t: Vec<char> = learn.map_or("", |l| l.before).chars().rev().take(crate::learn::MAX_CONTEXT).collect();
        t.into_iter().rev().collect()
    };
    let mut hyps: Vec<Vec<Hyp<'a>>> = (0..=n).map(|_| Vec::new()).collect();
    hyps[0].push(Hyp { score: 0.0, surface: String::new(), words: Vec::new(), last: start_id, ctx: lm.ctx_of(start_id) });
    for i in 1..=n {
        let mut cand: Vec<Hyp<'a>> = Vec::new();
        let mut idx: HashMap<String, usize> = HashMap::new();
        for l in 1..=base.max_len.min(i) {
            let Some(r) = base.find(&ids[i - l..i]) else { continue };
            if hyps[i - l].is_empty() {
                continue;
            }
            let span = &syls[i - l..i];
            let learned = learn.filter(|ln| ln.learner.has_reading(span));
            let range = base.range(r);
            let best = lex.ents[range.start].score;
            // Entries to try: the top PER_KEY, plus learned words ranked below it (`extra`: only
            // usable on a path whose context has a learned record for them).
            let mut entries: Vec<(usize, bool)> = range.clone().take(PER_KEY).map(|p| (p, false)).collect();
            if let Some(ln) = learned {
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
                hits_of = hyps[i - l]
                    .iter()
                    .map(|h| {
                        let key = context_key(&format!("{before_tail}{}", h.surface));
                        *by_ctx.entry(key).or_insert_with_key(|k| {
                            hits.push(ln.learner.lookup(k, span, ln.today));
                            hits.len() - 1
                        })
                    })
                    .collect();
            } else {
                hits_of = Vec::new();
            }
            for (p, extra) in entries {
                let e = &lex.ents[p];
                let (word, lp0) = (lex.word_of(e), e.score);
                let (wid, pb0) = (lm.word_id(word), pow10(lp0));
                for (hi, h) in hyps[i - l].iter().enumerate() {
                    let (mut lp, mut pb) = (lp0, pb0);
                    if let Some(ln) = learned {
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
                                }
                            }
                            _ if extra => continue,
                            _ => {}
                        }
                    }
                    let sc = h.score + word_term(lam, lm.prob_c(h.ctx, wid, pb), lp);
                    let surface = format!("{}{word}", h.surface);
                    match idx.get(&surface) {
                        Some(&q) if !(sc > cand[q].score) => {}
                        found => {
                            let mut words = h.words.clone();
                            words.push((word, lp));
                            let nh = Hyp { score: sc, surface, words, last: wid, ctx: None };
                            match found {
                                Some(&q) => cand[q] = nh,
                                None => {
                                    idx.insert(nh.surface.clone(), cand.len());
                                    cand.push(nh);
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
        }
        hyps[i] = cand;
    }
    let last = std::mem::take(&mut hyps[n]);
    let closing = match end {
        End::Eos => None,
        End::Next { word, lp } => Some((lm.word_id(word), pow10(lp), lp)),
    };
    let mut out: Vec<Scored<'a>> = last
        .into_iter()
        .map(|h| {
            let term = match closing {
                None => lam * lm.prob_c(h.ctx, Some(1), lm.p_eos).log10(),
                Some((wid, pb, lp)) => word_term(lam, lm.prob_c(h.ctx, wid, pb), lp),
            };
            (h.score + term, h.words)
        })
        .collect();
    if out.is_empty() {
        return Err(Error::NoPath { len: n });
    }
    out.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
    Ok(out)
}

/// S2h §1: the word that conditions the first word of a composition with no fixed word on its left.
/// `left` is the stored context (`context_key` result, at most 2 Han characters, "" for none). Tries
/// the whole of `left`, then its last character; the first one with bigram history in the model wins
/// (a word without history makes `prob` ignore it, so it would be no condition at all). None: `<s>`.
pub fn history<'a>(left: &'a str, lm: &Lm) -> &'a str {
    let last = left.char_indices().next_back().map_or(0, |(i, _)| i);
    [left, &left[last..]]
        .into_iter()
        .find(|w| !w.is_empty() && lm.ctx_of(lm.word_id(w)).is_some())
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
    decode_from(lex, syls, lm, profile, beam, "<s>")
}

/// `decode` with the first word conditioned on `start` (lm.py `decode(..., start=)`).
pub fn decode_from(
    lex: &CappedLexicon,
    syls: &[String],
    lm: &Lm,
    profile: Profile,
    beam: usize,
    start: &str,
) -> Result<Vec<(f64, Vec<String>)>, Error> {
    Ok(decode_segment(lex, syls, lm, profile.lambda(), start, End::Eos, beam)?
        .into_iter()
        .map(|(s, ws)| (s, ws.into_iter().map(|(w, _)| w.to_string()).collect()))
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

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
}
