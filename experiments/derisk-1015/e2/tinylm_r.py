"""E2 core, numpy only (no torch): the reading channel on top of experiments/s7a-tinylm/tinylm.py.

Contract docs/contracts/derisk-1015.md section 3 E2. One new input per position: a Bopomofo-syllable id (0 = NOREAD, "no reading").

Alignment (the one decision that matters, see README "Design"): a causal LM predicts token t+1 from the inputs up to t, so the
reading that can help is the one of the character being predicted. Data are therefore stored TOKEN-aligned (`rd_tok[t]` = the
reading of token t, same length as the token sequence), and the model is fed TARGET-aligned: `target_aligned(rd_tok)[t] =
rd_tok[t + 1]` (0 at the end). The model adds `rd_emb[target_aligned[t]]` to the input embedding at position t.
Row 0 of the reading table is zero and never trained, so all-NOREAD input is exactly the plain character LM of S7a.
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
S7A = os.path.join(ROOT, "experiments", "s7a-tinylm")
if S7A not in sys.path:
    sys.path.insert(0, S7A)
import tinylm  # noqa: E402
from tinylm import BOS, CTX, Vocab, _gelu, _ln  # noqa: E402

NOREAD = 0
EXTRA_KEYS = ("cfg", "chars", "rd_syl")     # npz entries that are not float weights


# ------------------------------------------------------------------ reading vocabulary
class RVocab:
    """Syllable strings -> ids 1.. (0 is NOREAD). `syls` is the ordered list stored in rdvocab.txt / the npz."""

    def __init__(self, syls):
        self.syls = list(syls)
        assert len(set(self.syls)) == len(self.syls) and all(self.syls)
        self.ids = {s: i + 1 for i, s in enumerate(self.syls)}
        self.size = len(self.syls) + 1

    def enc(self, syllables):
        """Syllable strings -> ids; a syllable outside the table raises KeyError (it is never silently mapped to NOREAD)."""
        return [self.ids[s] for s in syllables]


def target_aligned(rd_tok):
    """(.., T) token-aligned reading ids -> (.., T) target-aligned: out[t] = rd_tok[t + 1], 0 at the end."""
    rd_tok = np.asarray(rd_tok)
    return np.concatenate([rd_tok[..., 1:], np.zeros_like(rd_tok[..., :1])], axis=-1)


# ------------------------------------------------------------------ training samples
def sample_splits(rng, n, probs):
    """n context lengths s in 0..len(probs)-1, drawn with probabilities `probs` (the left-context length of the evaluation rows)."""
    p = np.asarray(probs, dtype=np.float64)
    return rng.choice(len(p), size=n, p=p / p.sum())


def mask_context(rd_tok, s):
    """Training-sample construction. rd_tok (B, L) token-aligned reading ids of pieces that start at BOS; s (B,) context lengths.
    The BOS and the first s characters are the context (typed text has no reading): their columns 0..s become NOREAD, the rest
    keeps its reading. Returns a copy."""
    rd_tok = np.array(rd_tok, copy=True)
    rd_tok[np.arange(rd_tok.shape[1])[None, :] <= np.asarray(s)[:, None]] = NOREAD
    return rd_tok


# ------------------------------------------------------------------ scoring items
def row_readings(rvocab, syllables, cands):
    """The reading ids of one evaluation row's candidates: `syllables` (the row's reading field) is shared by every candidate.
    Returns (list of per-candidate id lists, reason). If any candidate's length differs from the syllable count, or a syllable is
    not in the table, the whole row gets None for every candidate (reason says which) and is scored as no-reading, so the
    candidates of one row are never compared with and without readings."""
    try:
        ids = rvocab.enc(syllables)
    except KeyError:
        return [None] * len(cands), "unknown syllable"
    if any(len(c) != len(ids) for c in cands):
        return [None] * len(cands), "length differs"
    return [ids] * len(cands), None


def build_item(voc, k, c, rd_c):
    """(token ids, context length incl. BOS, token-aligned reading ids) of the sequence [BOS] + k + c, as tinylm.ll_many builds it.
    The BOS and k are NOREAD; c carries rd_c (None = NOREAD). The context is trimmed on the left exactly as ll_many does."""
    if len(c) > CTX - 1:
        raise ValueError(f"candidate of {len(c)} characters does not fit the context {CTX}")
    k = k[max(0, len(k) - (CTX - 1 - len(c))):]
    rc = [NOREAD] * len(c) if rd_c is None else list(rd_c)
    assert len(rc) == len(c), "reading and character lengths differ"
    return [BOS] + voc.enc(k) + voc.enc(c), 1 + len(k), [NOREAD] * (1 + len(k)) + rc


def ll_many_r(model, items, bs=64):
    """tinylm.ll_many with readings. items: [(k, c, rd_c)] (rd_c: ids of c's characters, or None). LL = sum over c of
    log10 P(c_i | [BOS] + k + c_<i, reading of c_i). A model without `has_readings` is called without readings (control arm), so
    for it this is exactly tinylm.ll_many."""
    voc, use = model.vocab, getattr(model, "has_readings", False)
    out = np.zeros(len(items))
    for s in range(0, len(items), bs):
        seqs = [build_item(voc, k, c, r) for k, c, r in items[s:s + bs]]
        T = max(len(q) for q, _, _ in seqs)
        ids = np.full((len(seqs), T), tinylm.PAD, dtype=np.int64)
        rd = np.zeros((len(seqs), T), dtype=np.int64)
        for b, (q, _, rt) in enumerate(seqs):
            ids[b, :len(q)] = q
            rd[b, :len(q)] = rt
        lp = model.logp10(ids, target_aligned(rd)) if use else model.logp10(ids)
        for b, (q, st, _) in enumerate(seqs):
            if len(q) > st:
                out[s + b] = lp[b, np.arange(st - 1, len(q) - 1), q[st:]].sum()
    return out


# ------------------------------------------------------------------ numpy forward with the reading channel
class NumpyLMR:
    """tinylm.NumpyLM plus the reading table (npz entries `rd` (R, d) and `rd_syl`). Same forward, one more additive term."""
    has_readings = True

    def __init__(self, path, dtype=np.float64):
        z = np.load(path, allow_pickle=False)
        self.n_layer, self.n_head, self.ctx = (int(x) for x in z["cfg"])
        self.vocab = Vocab([str(c) for c in z["chars"]])
        self.rvocab = RVocab([str(s) for s in z["rd_syl"]])
        self.dtype = dtype
        self.p = {k: z[k].astype(dtype) for k in z.files if k not in EXTRA_KEYS}
        assert self.p["emb"].shape[0] == self.vocab.size and self.p["rd"].shape[0] == self.rvocab.size
        assert not self.p["rd"][NOREAD].any(), "row 0 (NOREAD) of the reading table must be zero"
        self.d = self.p["emb"].shape[1]

    def logits(self, ids, rd=None):
        ids = np.asarray(ids)
        B, T = ids.shape
        assert T <= self.ctx
        p, H = self.p, self.n_head
        dh = self.d // H
        x = p["emb"][ids] + p["pos"][:T]
        if rd is not None:
            x = x + p["rd"][np.asarray(rd)]
        mask = np.triu(np.ones((T, T), dtype=bool), 1)
        for i in range(self.n_layer):
            g = lambda k: p[f"l{i}.{k}"]  # noqa: E731
            q, k_, v = np.split(_ln(x, g("ln1_w"), g("ln1_b")) @ g("qkv_w") + g("qkv_b"), 3, axis=-1)
            q, k_, v = (t.reshape(B, T, H, dh).transpose(0, 2, 1, 3) for t in (q, k_, v))
            s = (q @ k_.transpose(0, 1, 3, 2)) / np.sqrt(dh).astype(self.dtype)
            s = np.where(mask, -np.inf, s)
            s = np.exp(s - s.max(-1, keepdims=True))
            a = (s / s.sum(-1, keepdims=True)) @ v
            x = x + a.transpose(0, 2, 1, 3).reshape(B, T, self.d) @ g("proj_w") + g("proj_b")
            x = x + _gelu(_ln(x, g("ln2_w"), g("ln2_b")) @ g("fc1_w") + g("fc1_b")) @ g("fc2_w") + g("fc2_b")
        return _ln(x, p["lnf_w"], p["lnf_b"]) @ p["emb"].T

    def logp10(self, ids, rd=None):
        z = self.logits(ids, rd).astype(np.float64)
        z = z - z.max(-1, keepdims=True)
        return (z - np.log(np.exp(z).sum(-1, keepdims=True))) / tinylm.LN10


def save_params_r(path, params, chars, rsyl, n_layer, n_head, ctx=CTX):
    """tinylm.save_params plus `rd_syl` (the reading table's syllables, so the npz alone defines the model). params has `rd`."""
    d = {k: np.asarray(v, dtype=np.float32) for k, v in params.items()}
    d["cfg"] = np.array([n_layer, n_head, ctx], dtype=np.int64)
    d["chars"] = np.array(list(chars), dtype="<U1")
    d["rd_syl"] = np.array(list(rsyl), dtype="<U8")
    np.savez(path, **d)


def random_params_r(chars, rsyl, n_layer, d, n_head, seed=0, ctx=CTX):
    """tinylm.random_params plus a random reading table whose row 0 is zero (tests, and the numpy smoke path)."""
    p = tinylm.random_params(chars, n_layer, d, n_head, seed, ctx)
    rd = np.random.default_rng(seed + 1).normal(0, 0.2, (len(rsyl) + 1, d)).astype(np.float32)
    rd[NOREAD] = 0
    p["rd"] = rd
    return p
