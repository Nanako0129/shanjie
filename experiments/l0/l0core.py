"""L0 core, numpy / stdlib only (no torch): contexts, option lists, features, tokenised row packs, the decision rule, threshold
selection and the two diagnostics. Registration: README.md in this folder. S7a code is imported, never edited.

Row = (gold, syllables, prefix) from score.load_rows; candidates = the dump's top 8, [(surface, ngram_score)] in rank order.
Options of a row with n candidates: option 0 = "keep" (= rank 1), option i >= 1 = rank i + 1; so option index == candidate index.
Score records (the L0 / M@64 score files): {"i", "n", "ok": [0/1 per candidate], "s": [per-option score]} or {"i", "n", "ok", "noscore": 1}.
"""
import os
import sys
from collections import namedtuple

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
S7A = os.path.join(os.path.dirname(HERE), "s7a-tinylm")
if S7A not in sys.path:
    sys.path.insert(0, S7A)
import tinylm  # noqa: E402  (also puts reference/proto and tools on sys.path)
from tinylm import BOS, PAD, CTX  # noqa: E402,F401
from eval import lenient  # noqa: E402  the evaluation's lenient match
import evalstats  # noqa: E402  tools/evalstats.py

TOPK = 8
NF = 8                                    # features of option i >= 1: [ng_i - ng_0] + one-hot of rank 2..8
MAX_CAND = CTX - 2                        # [BOS] k [BOS] c <= 64 tokens, so |c| + |k| <= 62
T_GRID = (0, 0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 3, 3.5, 4, 5, 6, 8)
CAP = 0.005                               # broken-count 95% Poisson upper limit / n


# ------------------------------------------------------------------ context
def is_han(c):
    return any(a <= ord(c) <= b for a, b in tinylm._HAN)


def k_cross(prefix, n):
    """The last n Han characters of prefix, punctuation and other characters skipped over; "" when n <= 0."""
    if n <= 0:
        return ""
    return "".join(c for c in prefix if is_han(c))[-n:]


def context_for(prefix, cand, context):
    """k for one candidate. context 64: k_cross(prefix, 62 - |c|). context 2: tinylm.context_key (<= 2 Han characters, the App's
    range), cut from the left if [BOS] k [BOS] c would exceed 64. Candidates longer than MAX_CAND have no k (callers skip the row)."""
    room = MAX_CAND - len(cand)
    if context == 64:
        return k_cross(prefix, room)
    k = tinylm.context_key(prefix)
    return k[len(k) - room:] if room < len(k) else k


# ------------------------------------------------------------------ options
def ok_flags(gold, surfaces):
    g = lenient(gold)
    return [int(lenient(s) == g) for s in surfaces]


def too_long(surfaces):
    return any(len(s) > MAX_CAND for s in surfaces)


def features(ng):
    """(n, NF) float32. Row 0 (keep) is all zeros and unused; row i >= 1 is [ng_i - ng_0, one-hot of rank i + 1 over ranks 2..8]."""
    n = len(ng)
    if n > TOPK:
        raise ValueError(f"{n} candidates, at most {TOPK}")
    f = np.zeros((n, NF), dtype=np.float32)
    for i in range(1, n):
        f[i, 0] = ng[i] - ng[0]
        f[i, i] = 1.0                         # rank i + 1 -> column 1 + (i + 1 - 2) = i
    return f


def target_of(ok):
    """Index of the first lenient-correct candidate (0 when rank 1 is right); None when no candidate is."""
    for i, o in enumerate(ok):
        if o:
            return i
    return None


def untrainable_reason(ok, surfaces):
    """None when the row can train; else 'n<2', 'too_long' (a candidate over MAX_CAND characters) or 'no_match' (checked in this order)."""
    if len(surfaces) < 2:
        return "n<2"
    if too_long(surfaces):
        return "too_long"
    if target_of(ok) is None:
        return "no_match"
    return None


# ------------------------------------------------------------------ tokenised rows
# toks: int32, the n sequences back to back; lens: int64 (n,); feat: float32 (n, NF); target: option index (-1 when unknown)
RowPack = namedtuple("RowPack", "toks lens feat target")


def pack_row(vocab, prefix, cands, context, target=-1):
    """[BOS] + enc(k) + [BOS] + enc(c) for every candidate of the row (k per context_for), plus its features. Caller guarantees
    no candidate is longer than MAX_CAND."""
    seqs = [[BOS] + vocab.enc(context_for(prefix, s, context)) + [BOS] + vocab.enc(s) for s, _ in cands]
    return RowPack(np.fromiter((t for q in seqs for t in q), dtype=np.int32), np.array([len(q) for q in seqs], dtype=np.int64),
                   features([sc for _, sc in cands]), target)


def collate(packs):
    """Rows -> arrays: ids (S, L) int64 right-padded with PAD, last (S,) index of each sequence's last real token, feat (S, NF),
    opt (B, TOPK) int64 = index into the S sequences of option j (-1: no such option), target (B,)."""
    lens = np.concatenate([p.lens for p in packs])
    ids = np.full((len(lens), int(lens.max())), PAD, dtype=np.int64)
    opt = np.full((len(packs), TOPK), -1, dtype=np.int64)
    s = 0
    for b, p in enumerate(packs):
        off = 0
        for j, l in enumerate(p.lens):
            ids[s + j, :l] = p.toks[off:off + l]
            off += l
        opt[b, :len(p.lens)] = np.arange(s, s + len(p.lens))
        s += len(p.lens)
    return {"ids": ids, "last": lens - 1, "feat": np.concatenate([p.feat for p in packs]), "opt": opt,
            "target": np.array([p.target for p in packs], dtype=np.int64)}


# ------------------------------------------------------------------ decisions
def pick_argmax(s):
    """Highest score; ties go to the lower index (the n-gram rank order, keep first)."""
    return max(range(len(s)), key=lambda i: (s[i], -i)) if s else 0


def pick(rec, t=None):
    """Option chosen for a score record. t None: argmax over all options (no threshold). t given (L0's decision rule): the best of
    options 1.. if its margin over option 0 is > t, else 0. noscore rows and rows with fewer than 2 options keep rank 1."""
    s = rec.get("s")
    if rec.get("noscore") or s is None or len(s) < 2:
        return 0
    if t is None:
        return pick_argmax(s)
    j = max(range(1, len(s)), key=lambda i: (s[i], -i))
    return j if s[j] - s[0] > t else 0


def fixes_breaks(oks, picks):
    """Against the n-gram first candidate: rows turned right, rows turned wrong."""
    fixes = sum(1 for ok, p in zip(oks, picks) if not ok[0] and ok[p])
    breaks = sum(1 for ok, p in zip(oks, picks) if ok[0] and not ok[p])
    return fixes, breaks


_ub = {}


def ub95(breaks):
    if breaks not in _ub:
        _ub[breaks] = evalstats.poisson_upper95(breaks)
    return _ub[breaks]


def stats_at(recs, t):
    """(fixes, breaks) of the rule at threshold t over score records."""
    return fixes_breaks([r["ok"] for r in recs], [pick(r, t) for r in recs])


def select_t(recs, grid=T_GRID, cap=CAP):
    """(t or None, table). table = [(t, fixes, breaks, net, ub95 / n, within_cap)] for every grid point. Among t with ub95(breaks) / n <= cap
    the largest net; ties -> larger t; none within the cap -> None (README: 「無門檻」)."""
    n = len(recs)
    table = []
    for t in grid:
        f, b = stats_at(recs, t)
        table.append((t, f, b, f - b, ub95(b) / n, ub95(b) / n <= cap))
    ok = [row for row in table if row[5]]
    return (max(ok, key=lambda r: (r[3], r[0]))[0] if ok else None), table


def alone_retain(recs):
    """No threshold. alone-fix = among rows whose rank 1 is wrong and some candidate is right, the share where the argmax option is right;
    retain = among rows whose rank 1 is right, the share where the argmax option is 0. Returns (fix_hit, fixable, kept, retainable)."""
    fix_hit = fixable = kept = retainable = 0
    for r in recs:
        p = pick(r)
        if r["ok"][0]:
            retainable += 1
            kept += p == 0
        elif any(r["ok"]):
            fixable += 1
            fix_hit += r["ok"][p]
    return fix_hit, fixable, kept, retainable


def m_view(r):
    """A score.py record (fields n, ok, ll) as a score record: s = ll (no keep node; keep = argmax at index 0). Rows with n < 2 have no ll."""
    return {"i": r["i"], "n": r["n"], "ok": r["ok"], "s": r.get("ll") if r["n"] >= 2 else None}
