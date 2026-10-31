#!/usr/bin/env python3
"""BRIDGE-2 (research log 2026-10-31), run on the eval VM by bridge2-remote.sh: rescore the CLI's decoder-only n-best with the seen-only bridge term.

  python3 bridge2.py SRC OUT MODELS VARIANTS N GOLD
    SRC       the private folder (tmpfs): SRC/dec/<arm>.chatclause.chat.{dump,words,r} from the CLI, arms base and A (de-glue)
    OUT       the results folder: OUT/res/ is written there (counts and row-number/ok files only)
    MODELS    the shipping model folder (bigram.sjlm, classes.sjc, trigram.sjt3)
    VARIANTS  eval/variants.tsv of the CLI's tree: candidates are judged with the CLI's lenient rule (char map, then greedy longest match)
    N         worker processes (cores)
    GOLD      gold sentences of the unit, one per row (private; the caller deletes it)
Writes OUT/res/cells.tsv (cell, unit, fixed, broken) and OUT/res/rows.<cell>.<unit>.tsv (row, ok). Unit: chatclause.chat only.
Cells (all against the base arm's rowstats; beta 0.125, k 50 fixed, nothing is selected here):
  dg    de-glue A alone (the A rowstats)         br    bridge on the BASE n-best        dgbr  bridge on the A n-best
Prints the registered pass check on br: net > 0 and broken <= fixed / 2.
Term for path word w_i (i >= 3), u = w_{i-2}, c = class(w_{i-1}): beta * log10[P(w|u,c) / P(w|c)] when (u, c, w) was seen and n(u,c,.) >= k;
P(w|u,c) = (n(u,c,w) + P(w|c)) / (n(u,c,.) + 1). The table is the formal trigram table summed over the class of the middle word.
Work split: one job per (arm, row chunk); each worker builds the table itself in the pool initializer (all workers at once, so no step runs on
one core; 2.79 GB peak RSS per build measured on the Mac, so 32 workers need about 90 GB). A line `JOB-DONE arm lo` is printed per finished job.
"""
import math
import multiprocessing as mp
import os
import struct
import sys

import numpy as np

UNIT = "chatclause.chat"
BETA, K = 0.125, 50
ARMS = ["base", "A"]
G = {}

CHAR_FROM, CHAR_TO = "她妳它牠嘗周臺裏", "他你他他嚐週台裡"   # core/src/eval.rs char_map


def load_lenient(path):
    table = {}
    for i, line in enumerate(open(path, encoding="utf-8").read().split("\n"), 1):
        if not line or line.startswith("#"):
            continue
        f = line.split("\t")
        if len(f) != 2 or not f[0] or not f[1] or f[0] in table:
            raise ValueError(f"{path}:{i}: bad variants line")
        table[f[0]] = f[1]
    return table, max((len(k) for k in table), default=0)


def lenient(s):
    table, longest = G["lenient"]
    cs = [CHAR_TO[CHAR_FROM.index(c)] if c in CHAR_FROM else c for c in s]
    out, i = [], 0
    while i < len(cs):
        for n in range(min(longest, len(cs) - i), 0, -1):
            k = "".join(cs[i:i + n])
            if k in table:
                out.append(table[k]); i += n
                break
        else:
            out.append(cs[i]); i += 1
    return "".join(out)


def read_sjlm_vocab(b):
    if b[:8] != b"SJLM0001":
        raise ValueError("bad sjlm magic")
    V = struct.unpack_from("<I", b, 8)[0]
    p = 8 + struct.calcsize("<IQQd")
    vocab = []
    for _ in range(V):
        (n,) = struct.unpack_from("<H", b, p); p += 2
        vocab.append(b[p:p + n].decode("utf-8")); p += n
    return V, vocab


def build(models, variants):
    G["lenient"] = load_lenient(variants)
    V, vocab = read_sjlm_vocab(open(f"{models}/bigram.sjlm", "rb").read())
    c = open(f"{models}/classes.sjc", "rb").read()
    NC, _, V2 = struct.unpack_from("<IdI", c, 40)   # NC classes (K is the n(u,c,.) threshold)
    if V2 != V:
        raise ValueError("classes.sjc vocabulary differs from the model")
    cls = np.frombuffer(c, "<u2", V, 40 + struct.calcsize("<IdI")).astype(np.int64)
    t = open(f"{models}/trigram.sjt3", "rb").read()
    magic, TV, _, _, C, E = struct.unpack_from("<8sId32sII", t, 0)
    if magic != b"SJT30001" or TV != V:
        raise ValueError("trigram table magic or vocabulary")
    p = struct.calcsize("<8sId32sII")
    key = np.frombuffer(t, "<u8", C, p).astype(np.int64); p += 8 * C
    p += 8 * C
    off = np.frombuffer(t, "<u4", C + 1, p).astype(np.int64); p += 4 * (C + 1)
    nxt = np.frombuffer(t, "<u4", E, p).astype(np.int64); p += 4 * E
    cnt = np.frombuffer(t, "<u4", E, p).astype(np.int64); p += 4 * E
    if p != len(t):
        raise ValueError("trailing bytes in the trigram table")
    k = np.repeat(key, np.diff(off))
    u, v = k // V, k % V
    cv = cls[v]
    keep = cv != 0xFFFF
    u, cv, w, cnt = u[keep], cv[keep], nxt[keep], cnt[keep]
    K3 = NC + 3
    uk, inv = np.unique((u * K3 + cv) * V + w, return_inverse=True)
    n_ucw = np.bincount(inv, weights=cnt)
    ucu, ucinv = np.unique(uk // V, return_inverse=True)
    n_uc = np.bincount(ucinv, weights=n_ucw)
    cwu, cwinv = np.unique((uk // V % K3) * V + uk % V, return_inverse=True)
    n_cw = np.bincount(cwinv, weights=n_ucw)
    cu, cinv = np.unique(cwu // V, return_inverse=True)
    n_c = np.bincount(cinv, weights=n_cw)
    G.update(ids={w: i for i, w in enumerate(vocab)}, cls=cls, V=V, K3=K3, tab=dict(zip(uk.tolist(), n_ucw.tolist())),
             ctx=dict(zip(ucu.tolist(), n_uc.tolist())), pcw=dict(zip(cwu.tolist(), (n_cw / n_c[cinv]).tolist())))


def corr(words):
    """Bridge sum (without beta) of one candidate's word list."""
    ids, cls, V, K3, tab, ctx, pcw = G["ids"], G["cls"], G["V"], G["K3"], G["tab"], G["ctx"], G["pcw"]
    s = 0.0
    for i in range(2, len(words)):
        u, v, w = ids.get(words[i - 2]), ids.get(words[i - 1]), ids.get(words[i])
        if u is None or v is None or w is None or cls[v] == 0xFFFF:
            continue
        c = int(cls[v])
        n_uc = ctx.get(u * K3 + c, 0.0)
        if n_uc < K:
            continue
        n = tab.get((u * K3 + c) * V + w, 0.0)
        pc = pcw.get(c * V + w)
        if n <= 0 or not pc:
            continue
        s += math.log10((n + pc) / (n_uc + 1.0) / pc)
    return s


def read_ok(path):
    return [l.split("\t")[1] == "1" for l in open(path)]


def load_rows(out, arm, unit, lo, hi):
    """dump and words of rows lo..hi-1 (1-based)."""
    b = f"{out}/dec/{arm}.{unit}"
    D, W = {}, {}
    for l in open(f"{b}.dump", encoding="utf-8"):
        r, k, surf, sc = l.rstrip("\n").split("\t")
        if lo <= int(r) < hi:
            D.setdefault(int(r), {})[int(k)] = (surf, float(sc))
    for l in open(f"{b}.words", encoding="utf-8"):
        r, k, ws = l.rstrip("\n").split("\t", 2)
        if lo <= int(r) < hi:
            W.setdefault(int(r), {})[int(k)] = ws.split()
    return D, W


def job(a):
    """Bridge-rescored ok of rows lo..hi-1 of one arm's n-best."""
    out, gold_path, arm, lo, hi = a
    gold = open(gold_path, encoding="utf-8").read().splitlines()
    ok = read_ok(f"{out}/dec/{arm}.{UNIT}.r")
    D, W = load_rows(out, arm, UNIT, lo, hi)
    res = []
    for i in range(lo, hi):
        ranks = sorted(D.get(i, {}))
        if not ranks:   # no candidate: the CLI's verdict stays
            res.append(int(ok[i - 1]))
            continue
        g = lenient(gold[i - 1])
        if (lenient(D[i][ranks[0]][0]) == g) != ok[i - 1]:
            raise ValueError(f"{arm} row {i}: the lenient rule here and the CLI's rowstats disagree on the first candidate")
        if set(ranks) != set(W.get(i, {})) - {0}:
            raise ValueError(f"{arm} row {i}: the words dump does not hold every candidate of the dump")
        if max((D[i][q][1], -q, q) for q in ranks)[2] != ranks[0]:   # beta 0 must give the CLI's first candidate back
            raise ValueError(f"{arm} row {i}: the dumped scores do not rank the first candidate first")
        sc = [(D[i][q][1] + BETA * corr(W[i][q]), -q, q) for q in ranks]
        r = max(sc)[2]
        res.append(int(ok[i - 1]) if r == ranks[0] else int(lenient(D[i][r][0]) == g))
    return arm, lo, res


def summarize(src, out, parts, nrows):
    """parts: [(arm, lo, ok list)]. Writes res/, prints the cells and the pass check; returns {cell: (fixed, broken)}."""
    os.makedirs(f"{out}/res", exist_ok=True)
    chunks = {}
    for arm, lo, v in parts:
        chunks.setdefault(arm, {})[lo] = v
    base = read_ok(f"{src}/dec/base.{UNIT}.r")
    cells = {"dg": [int(x) for x in read_ok(f"{src}/dec/A.{UNIT}.r")],
             "br": [x for lo in sorted(chunks["base"]) for x in chunks["base"][lo]],
             "dgbr": [x for lo in sorted(chunks["A"]) for x in chunks["A"][lo]]}
    by = {}
    with open(f"{out}/res/cells.tsv", "w") as o:
        for name, v in cells.items():
            if len(v) != nrows:
                sys.exit(f"STOP: {name}: {len(v)} rows for {nrows}")
            f = sum(1 for a, b in zip(base, v) if b and not a)
            b = sum(1 for a, b in zip(base, v) if a and not b)
            by[name] = (f, b)
            o.write(f"{name}\t{UNIT}\t{f}\t{b}\n")
            with open(f"{out}/res/rows.{name}.{UNIT}.tsv", "w") as r:
                r.write("".join(f"{i}\t{x}\n" for i, x in enumerate(v, 1)))
    for name, (f, b) in by.items():
        print(f"{name:5s} {UNIT}: fixed {f} broken {b} net {f - b:+d}")
    f, b = by["br"]
    print(f"REGISTERED CHECK on br (bridge alone, beta {BETA} k {K}): net {f - b:+d} > 0: {f - b > 0}; broken {b} <= fixed/2 {f / 2}: {b <= f / 2} -> "
          f"{'PASS' if f - b > 0 and b <= f / 2 else 'NOT PASSED'}")
    return by


def make_tasks(src, gold_path, nrows, n):
    """floor(n / 2) row chunks per arm, so about n jobs start together on n workers (imap hands a worker that finishes early another job,
    so one round is not guaranteed)."""
    step = -(-nrows // max(1, n // len(ARMS)))
    return [(src, gold_path, arm, lo, min(lo + step, nrows + 1)) for arm in ARMS for lo in range(1, nrows + 1, step)]


def main():
    src, out, models, variants, n, gold_path = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]), sys.argv[6]
    nrows = len(open(gold_path, encoding="utf-8").read().splitlines())
    for arm in ARMS:
        if nrows != len(read_ok(f"{src}/dec/{arm}.{UNIT}.r")):
            sys.exit(f"STOP: row counts differ between gold and {arm}")
    tasks = make_tasks(src, gold_path, nrows, n)
    print(f"POOL-START {len(tasks)} jobs on {n} processes (each worker builds the table first)", flush=True)
    with mp.get_context("fork").Pool(n, initializer=build, initargs=(models, variants)) as pool:
        parts = []
        for part in pool.imap_unordered(job, tasks, chunksize=1):
            parts.append(part)
            print(f"JOB-DONE {part[0]} {part[1]} ({len(parts)} of {len(tasks)})", flush=True)
    summarize(src, out, parts, nrows)


if __name__ == "__main__":
    main()
