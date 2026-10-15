"""E1b: the trigram level T_k adds to the bigram model B0, built from integer trigram counts and looked up with numpy.

Smoothing (interpolated absolute discounting, round 10's rule in experiments/s2/iter2.py LM._p, kept pre-prune totals):
  P3(w | u, v) = max(c(u,v,w) - D, 0) / t3(u,v) + back3(u,v) * P2(w | v)        if the context (u, v) is stored
  P3(w | u, v) = P2(w | v)                                                       otherwise
  back3(u,v)   = 1 - sum over KEPT entries (c - D) / t3(u,v),   t3 = the context's total BEFORE pruning
P2 is exactly B0's own probability (lm.py prob, including its KN back-off and class term when loaded), so the table is "B0 plus a
trigram level": with no table, or a context that is not stored, the score is B0's, bit for bit. Because back3 is defined from the
pre-prune total, the distribution stays normalised after any pruning whenever P2 is (test_e1 checks the sum over the whole vocabulary).
Why not KN-style continuation counts: that would replace B0's lower-order distribution (N1+(. v w) instead of c(v,w)), so T_k would no
longer be B0 plus a table and the "null configuration = B0" anchor could not hold; the null configuration is what lets the E1 comparison
isolate the trigram level.

The two history words (u, v): u is None when it is unknown (a left context supplies one word only); then P3 = P2.
"""
import functools
import math
import mmap

import numpy as np

import common  # noqa: F401
import sjlm_io

D_TRI = 0.75   # trigram discount, the same D as the bigram level (round 10)


def merge_variants_tri(tri, cls):
    """build_lm.merge_variants for trigrams: (u, v, w) counts summed by (class, class, class); the last word sits on the
    representative member only, the two history words are written to every member (contexts are identical for all members)."""
    rep = lambda w: cls[w][0] if w in cls else w
    moved = {}
    for k in [k for k in tri if k[0] in cls or k[1] in cls or k[2] in cls]:
        r = (rep(k[0]), rep(k[1]), rep(k[2]))
        moved[r] = moved.get(r, 0) + tri.pop(k)
    for (u, v, w), c in moved.items():
        for mu in cls.get(u, (u,)):
            for mv in cls.get(v, (v,)):
                tri[(mu, mv, w)] = c
    return tri


def tri_arrays(tri, ids):
    """Counter / dict {(u, v, w): int count} -> sorted numpy arrays (u, v, w, c). Trigrams with a word outside `ids` are dropped
    (like build_lm drops bigrams whose context is not in the vocabulary)."""
    V = len(ids)
    if V ** 3 >= 2 ** 64:
        raise ValueError("vocabulary too large for the packed trigram key")
    ok = lambda k: k[0] in ids and k[1] in ids and k[2] in ids
    keys = np.fromiter(((ids[a] * V + ids[b]) * V + ids[c] for (a, b, c) in tri if ok((a, b, c))), dtype=np.uint64)
    cnt = np.fromiter((c for k, c in tri.items() if ok(k)), dtype=np.int64, count=len(keys))
    order = np.argsort(keys, kind="stable")
    keys, cnt = keys[order], cnt[order]
    VV = np.uint64(V) * np.uint64(V)
    u = (keys // VV).astype(np.uint32)
    v = ((keys // np.uint64(V)) % np.uint64(V)).astype(np.uint32)
    w = (keys % np.uint64(V)).astype(np.uint32)
    return u, v, w, cnt


class TriBuild:
    """All trigram entries sorted by (u, v, w); serialises the table for any prune threshold from the same arrays."""

    def __init__(self, u, v, w, c, V, D=D_TRI):
        self.V, self.D, self.u, self.v, self.w, self.c = V, D, u, v, w, c
        key = u.astype(np.uint64) * np.uint64(V) + v.astype(np.uint64)
        self.start = np.flatnonzero(np.concatenate(([True], key[1:] != key[:-1]))) if len(key) else np.zeros(0, np.int64)
        self.total = np.add.reduceat(c.astype(np.uint64), self.start) if len(key) else np.zeros(0, np.uint64)   # pre-prune t3
        self.cmax = np.maximum.reduceat(c, self.start) if len(key) else np.zeros(0, np.int64)

    def stats(self, thr):
        """(contexts, entries, bytes) of the table kept at c >= thr."""
        return int((self.cmax >= thr).sum()), int((self.c >= thr).sum()), self.size(thr)

    def size(self, thr):
        C, E = int((self.cmax >= thr).sum()), int((self.c >= thr).sum())
        return sjlm_io.TRI_FIXED + common.TRI_BYTES_PER_CONTEXT * C + common.TRI_BYTES_PER_ENTRY * E

    def tables(self, thr, model_sha):
        """The sjlm_io.read_tri dict for threshold thr. Contexts keep their pre-prune totals; contexts without a kept entry are not stored."""
        keep = self.c >= thr
        per_ctx = np.add.reduceat(keep.astype(np.int64), self.start) if len(self.c) else np.zeros(0, np.int64)
        sel = per_ctx > 0
        off = np.concatenate(([0], np.cumsum(per_ctx[sel]))).astype(np.uint32)
        first = self.start[sel]
        key = self.u[first].astype(np.uint64) * np.uint64(self.V) + self.v[first].astype(np.uint64)
        return {"V": self.V, "D": self.D, "model_sha": model_sha, "key": key,
                "tot": self.total[sel], "off": off, "nxt": self.w[keep], "cnt": self.c[keep].astype(np.uint32)}


class TriTable:
    """A loaded SJT30001 table. The arrays are read-only views of the file (np.frombuffer; `from_path` memory-maps it, so worker processes
    share one copy in the page cache); lookups are numpy binary searches, the back-off weight of a context is computed when first used."""

    def __init__(self, data, model_sha=None):
        d = sjlm_io.read_tri(data)
        if model_sha is not None and d["model_sha"] != model_sha:
            raise ValueError("trigram table was built for another bigram model")
        self.V, self.D = d["V"], d["D"]
        self.key, self.tot, self.off, self.nxt, self.cnt = d["key"], d["tot"], d["off"], d["nxt"], d["cnt"]
        self.sha_bound = d["model_sha"]
        self._data = data        # keeps a memory map alive
        if self.key.ctypes.data % 8:
            # The key array starts 60 bytes into the file, so it is not 8-byte aligned. np.searchsorted silently copies an unaligned
            # array on EVERY call (192 MB for the unpruned table: 8.5 ms per lookup, measured). One aligned copy at load removes that;
            # the other arrays are only sliced or indexed per context, so their alignment costs nothing.
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

    def context_words(self, idx):
        """(u id, v id) of context idx."""
        return divmod(int(self.key[idx]), self.V)

    @property
    def entries(self):
        return len(self.nxt)

    @property
    def contexts(self):
        return len(self.key)


class TriLM:
    """B0 (a lm.BigramLM) plus an optional trigram table; the scoring functions of lm.py with a second history word."""

    def __init__(self, lm, tri=None):
        self.lm, self.tri = lm, tri
        self._ctx = functools.lru_cache(maxsize=1 << 20)(self._ctx_uncached)
        self.word = functools.lru_cache(maxsize=1 << 20)(self._word)   # pure in (lam, u, v, w, lp) for a fixed KN beta: clear it when beta changes

    def _ctx_uncached(self, u, v):
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

    def _word(self, lam, u, v, w, lp):
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
