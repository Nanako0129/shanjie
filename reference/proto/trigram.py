"""Trigram level of the formal profile (docs/contracts/trigram-core.md): reader, writer and scorer of the SJT30001 table, and the decoder
with two history words. core/src/trigram.rs and core/src/lm.rs must agree with it score for score; the formulas are those of
experiments/derisk-1015/e1/trimodel.py and harness.py (the E1 reference the contract's numbers were measured with).

SJT30001 (little-endian), the table bound to one bigram model (tools/build_trigram.py writes it):
  magic        8 bytes  b"SJT30001"
  V            u32      vocabulary size of the bigram model it is bound to
  D            f64      absolute discount of the trigram level
  model_sha    32 bytes SHA-256 of the bigram .sjlm file (the table is meaningless for another model, like kn.sjkn)
  C            u32      number of (u, v) contexts that keep at least one entry
  E            u32      number of entries
  ctx_key      u64[C]   u * V + v for the two history word ids (u first), sorted ascending: one binary search finds a context
  ctx_total    u64[C]   the context's total count BEFORE pruning (the pruned mass goes to the back-off, like SJLM)
  ctx_off      u32[C+1] entry intervals
  next_id      u32[E]   ascending inside a context
  count        u32[E]   count (the table keeps counts >= the prune threshold)
Size = 64 + 20 * C + 8 * E bytes.

Smoothing (interpolated absolute discounting, kept pre-prune totals):
  P3(w | u, v) = max(c(u,v,w) - D, 0) / t3(u,v) + back3(u,v) * P2(w | v)        if the context (u, v) is stored
  P3(w | u, v) = P2(w | v)                                                       otherwise (also when u is unknown)
  back3(u,v)   = 1 - sum over KEPT entries (c - D) / t3(u,v),   t3 = the context's total BEFORE pruning
P2 is the bigram model's own probability (lm.py `prob`, class term included); with the Kneser-Ney side file loaded its pb is
substituted first, exactly as lm.py `word` does. The decoder: `decode3` is lm.decode with the state (u, v) = the last two words and
paths recombined on (surface, previous word, last word); the closing term is scored from (u, v) too.
"""
import hashlib
import math
import mmap
import struct

import numpy as np

import lm as L

MAGIC = b"SJT30001"
HEAD = struct.calcsize("<8sId32sII")   # 60
D_TRI = 0.75   # the trigram discount, the same D as the bigram level


def read_tri(b):
    magic, V, D, sha, C, E = struct.unpack_from("<8sId32sII", b, 0)
    if magic != MAGIC:
        raise ValueError("bad trigram magic")
    if D != D_TRI:   # the contract's constant (core/src/trigram.rs refuses any other value too)
        raise ValueError("trigram table has a discount other than 0.75")
    p = HEAD
    d = {"V": V, "D": D, "model_sha": sha}
    d["key"] = np.frombuffer(b, "<u8", C, p); p += 8 * C
    d["tot"] = np.frombuffer(b, "<u8", C, p); p += 8 * C
    d["off"] = np.frombuffer(b, "<u4", C + 1, p); p += 4 * (C + 1)
    d["nxt"] = np.frombuffer(b, "<u4", E, p); p += 4 * E
    d["cnt"] = np.frombuffer(b, "<u4", E, p); p += 4 * E
    if p != len(b):
        raise ValueError("trailing bytes in trigram table")
    return d


def write_tri(d):
    C, E = len(d["key"]), len(d["nxt"])
    return b"".join([
        struct.pack("<8sId32sII", MAGIC, d["V"], d["D"], d["model_sha"], C, E),
        np.asarray(d["key"], "<u8").tobytes(), np.asarray(d["tot"], "<u8").tobytes(),
        np.asarray(d["off"], "<u4").tobytes(), np.asarray(d["nxt"], "<u4").tobytes(), np.asarray(d["cnt"], "<u4").tobytes()])


class TriTable:
    """A loaded SJT30001 table (read-only views of the bytes); lookups are binary searches, a context's back-off weight is computed when first used."""

    def __init__(self, data, model_sha=None):
        d = read_tri(data)
        if model_sha is not None and d["model_sha"] != model_sha:
            raise ValueError("trigram table was built for another bigram model")
        self.V, self.D = d["V"], d["D"]
        self.key, self.tot, self.off, self.nxt, self.cnt = d["key"], d["tot"], d["off"], d["nxt"], d["cnt"]
        self._data = data        # keeps a memory map alive
        if self.key.ctypes.data % 8:
            # The key array starts 60 bytes into the file, so it is not 8-byte aligned; np.searchsorted would copy it on every call.
            self.key = np.array(self.key)
        self._back = {}

    @classmethod
    def from_path(cls, path, model_sha=None):
        with open(path, "rb") as f:
            return cls(mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ), model_sha)

    def find(self, ui, vi):
        """Index of context (ui, vi) or -1."""
        k = np.uint64(ui) * np.uint64(self.V) + np.uint64(vi)
        i = int(np.searchsorted(self.key, k))
        return i if i < len(self.key) and self.key[i] == k else -1

    def count(self, idx, wi):
        lo, hi = int(self.off[idx]), int(self.off[idx + 1])
        j = lo + int(np.searchsorted(self.nxt[lo:hi], wi))
        return int(self.cnt[j]) if j < hi and self.nxt[j] == wi else 0

    def back(self, idx):
        """1 - sum over the kept entries of (c - D) / t, t the pre-prune total of the context."""
        b = self._back.get(idx)
        if b is None:
            lo, hi = int(self.off[idx]), int(self.off[idx + 1])
            b = self._back[idx] = 1.0 - float((self.cnt[lo:hi].astype(np.float64) - self.D).sum()) / float(self.tot[idx])
        return b


class TriLM:
    """A lm.BigramLM plus an optional trigram table: lm.py's scoring functions with a second history word (u first; None is unknown)."""

    def __init__(self, lm, tri=None):
        self.lm, self.tri = lm, tri

    def _ctx(self, u, v):
        if self.tri is None or u is None:
            return -1
        ui, vi = self.lm.ids.get(u, -1), self.lm.ids.get(v, -1)
        return -1 if ui < 0 or vi < 0 else self.tri.find(ui, vi)

    def prob(self, u, v, w, pb):
        p = self.lm.prob(v, w, pb)
        idx = self._ctx(u, v)
        if idx < 0:
            return p
        tri = self.tri
        k = tri.count(idx, self.lm.ids.get(w, -1))
        t, back = float(tri.tot[idx]), tri.back(idx)
        return (k - tri.D) / t + back * p if k else back * p

    def word(self, lam, u, v, w, lp):
        """lm.py BigramLM.word with the trigram level (same KN mix of the back-off distribution)."""
        lm = self.lm
        pb = 10 ** lp
        if lm.kn is not None:
            wi = lm.ids.get(w, -1)
            if wi >= 2:
                beta, np1, total = lm.kn
                pb = beta * np1[wi] / total + (1 - beta) * pb
        return lam * math.log10(self.prob(u, v, w, pb)) + (1 - lam) * lp

    def eos(self, lam, u, v):
        return lam * math.log10(self.prob(u, v, "</s>", self.lm.p_eos))


def start_state(start):
    """The history (u, v) of the first word: `<s>` is (<s>, <s>); any other word is (unknown, that word), so the first word is scored by the
    bigram alone (a left context supplies one word). `start` is what lm.decode takes as its `start`."""
    return ("<s>", "<s>") if start == "<s>" else (None, start)


def sha8(path):
    """The summary line's `+tri:<sha8>` is the SHA-256 of the table file (core/src/trigram.rs `tag`)."""
    return hashlib.sha256(open(path, "rb").read()).hexdigest()[:8]


def decode3(lex, syls, tri_lm, profile, beam=64, start="<s>", demote=True):
    """lm.decode with the history (u, v). Same state, arithmetic and order of operations; the recombination key adds the previous word."""
    table = getattr(lex, "demote", None) if demote else None
    lm, lam = tri_lm.lm, L.PROFILES[profile]
    su, sv = start_state(start)
    syls = tuple(syls); n = len(syls)
    hist = lambda ws: (su, sv) if not ws else ((ws[-2] if len(ws) >= 2 else sv), ws[-1])
    hyps = [[] for _ in range(n + 1)]; hyps[0] = [(0.0, ())]
    for i in range(1, n + 1):
        cand = {}
        for Ln in range(1, min(lex.max_len, i) + 1):
            key = syls[i - Ln:i]
            entries = lex.by_reading.get(key)
            if not entries or not hyps[i - Ln]:
                continue
            reading = "-".join(key) if table else None
            for word, lp in entries[:L.ime.PER_KEY]:
                d = L.demote_delta(table, reading, word)
                for s, ws in hyps[i - Ln]:
                    u, v = hist(ws)
                    sc = s + (tri_lm.word(lam, u, v, word, lp) - d)
                    ck = ("".join(ws) + word, v, word)   # the next score depends on the last two words
                    if ck not in cand or sc > cand[ck][0]:
                        cand[ck] = (sc, ws + (word,))
        hyps[i] = sorted(cand.values(), key=lambda x: -x[0])[:beam]
    out = []
    for s, ws in hyps[n]:
        u, v = hist(ws)
        out.append((s + tri_lm.eos(lam, u, v), ws))
    best, seen = [], set()
    for x in sorted(out, key=lambda x: -x[0]):
        surface = "".join(x[1])
        if surface not in seen:
            seen.add(surface)
            best.append(x)
    return best
