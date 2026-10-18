"""Position-level 0c teacher labels (experiments/p2pos-distill/README.md registration, arm T). Runs on GCP: onnxruntime + onnx + numpy.

  python3 teacher_pos.py --model-dir M --rows ROWS.txt --out ROWS.pos.jsonl[.gz] [--workers N] [--limit 1000]
         [--lexicon data/lexicon/mcbpmf-data.txt]

For each row `prefix|sentence|reading` (the very reader of score_0c.py), 0c scores the GOLD sentence once, with the context cut to the
student's context_key (tinylm.row_key(prefix, 2): <= 2 Han characters), the row's reading as given (no neutral-tone retry). The pinned
model.onnx only outputs the summed `scores`; its graph holds the per-position log-softmax as the tensor `log_softmax` ([u, 27859], row j =
the distribution of character j, column of character id c = c - 6). It is added as a graph output IN MEMORY (onnx package) after score_0c
checked the pinned sha256 of the original bytes; no modified model file is written. For position j the distribution is restricted to
H(r_j) = the single-character entries of reading r_j in the lexicon (lines `reading char logp`) that 0c's vocabulary has, and renormalised.

One JSONL line per row (in rows order, `i` = 1-based row number):
  {"i": i, "skip": 0, "pos": [[chars, [log q_c ...]] per position], "out": [mass outside H(r_j) before renormalising, per position]}
  {"i": i, "skip": 1, "why": "oov"|"length"|"syllables"|"base"|"empty_h"}
`chars` is a string (one character per entry of the list next to it), log q rounded to 5 decimals. A `.gz` --out is gzip text.
Skipped rows are counted by reason. The median and p90 of the outside-H mass over all positions are printed; median > 20% is the
registered stop condition (H built wrong): the file is still written and the exit code is 3.
"""
import argparse
import gzip
import json
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
EXP = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(EXP, "x0c"), os.path.join(EXP, "s7a-tinylm")]    # both are imported, never edited
import score_0c as z   # noqa: E402
import tinylm          # noqa: E402

LS = "log_softmax"
COL = 6                      # output column of 0c character id c is c - COL
STOP_MEDIAN = 0.20
DEFAULT_LEXICON = os.path.join(z.ROOT, "data", "lexicon", "mcbpmf-data.txt")


# ------------------------------------------------------------------ H(r) and the restriction (no onnx)
def load_homophones(lines):
    """`reading char logp` lines -> {reading: [char, ...]} of the single-character entries (a one-character entry has a one-syllable
    reading), in file order, duplicates dropped. Comment lines (#) and lines of another shape are ignored."""
    h = {}
    for line in lines:
        p = line.rstrip("\n").split(" ")
        if len(p) == 3 and len(p[1]) == 1 and not line.startswith("#"):
            if p[1] not in h.setdefault(p[0], []):
                h[p[0]].append(p[1])
    return h


def restrict(lp_row, cols):
    """lp_row: log-softmax over 0c's output vocabulary; cols: columns of H. -> (log q renormalised over cols, mass outside cols)."""
    lp = np.asarray(lp_row, dtype=np.float64)[cols]
    m = lp.max()
    lse = m + math.log(np.exp(lp - m).sum())            # log of the mass inside H
    return lp - lse, max(0.0, -math.expm1(lse))


def teacher_row(m, H, key, gold, reading, logsm):
    """-> (record without `i`, skip kind or None). logsm(context, reading, gold) -> (n, V) log-probs of the gold sentence's n positions."""
    try:
        z.check_row(m, reading, [gold])
    except z.Unscorable as e:
        return {"skip": 1, "why": e.args[0]}, e.args[0]
    chars = [[c for c in H.get(r, ()) if c in m.chars] for r in reading]
    if not all(chars):
        return {"skip": 1, "why": "empty_h"}, "empty_h"
    lp = logsm(key, reading, gold)
    if len(lp) != len(gold):
        raise SystemExit(f"teacher_pos: STOP, {len(lp)} output rows for a {len(gold)}-character sentence")
    pos, out = [], []
    for row, cs in zip(lp, chars):
        lq, mass = restrict(row, [m.chars[c] - COL for c in cs])
        pos.append(["".join(cs), [round(float(x), 5) for x in lq]])
        out.append(round(mass, 5))
    return {"skip": 0, "pos": pos, "out": out}, None


# ------------------------------------------------------------------ the model (onnx)
_make_session = z.make_session


def _session_with_log_softmax(blob, ort, threads):
    """score_0c.load_model has checked the pinned hashes of `blob`; add the log_softmax tensor to a copy of the graph and open that."""
    import onnx
    g = onnx.load_from_string(blob)
    if not any(LS in n.output for n in g.graph.node):
        raise SystemExit(f"teacher_pos: STOP, the graph has no tensor named {LS}")
    vi = [v for v in g.graph.value_info if v.name == LS]
    if vi:                                        # the pinned model carries its type and shape ([u, 27859], float)
        g.graph.output.add().CopyFrom(vi[0])
        g.graph.value_info.remove(vi[0])
    else:
        g.graph.output.append(onnx.helper.make_tensor_value_info(LS, onnx.TensorProto.FLOAT, None))
    return _make_session(g.SerializeToString(), ort, threads)


def load_model(model_dir, threads=None):
    z.make_session = _session_with_log_softmax    # score_0c.load_model calls it with the bytes it verified
    return z.load_model(model_dir, threads)


def logsm_onnx(m, context, reading, gold):
    out = m.session.run([LS], z.feeds_for(m, context, reading, [gold]))[0]
    return np.asarray(out).reshape(len(reading), -1)


# ------------------------------------------------------------------ driver
def open_text(path, mode="rt"):
    return gzip.open(path, mode, encoding="utf-8", newline="\n") if path.endswith(".gz") else open(path, mode, encoding="utf-8", newline="\n")


def _slice(job):
    """One worker: its own model (one thread), a contiguous slice of rows -> a part file; returns (skip kinds, outside masses)."""
    model_dir, lexicon, rows, start, part = job
    with open(lexicon, encoding="utf-8") as f:
        H = load_homophones(f)
    m, kinds, masses = load_model(model_dir, 1), {}, []
    with open(part, "w", encoding="utf-8", newline="\n") as f:
        for k, (gold, syl, prefix) in enumerate(rows):
            r, kind = teacher_row(m, H, tinylm.row_key(prefix, 2), gold, syl, lambda *a: logsm_onnx(m, *a))
            kinds[kind] = kinds.get(kind, 0) + 1
            masses += r.get("out", [])
            f.write(json.dumps({"i": start + k + 1, **r}, ensure_ascii=False) + "\n")
    return kinds, masses


def summary(kinds, masses):
    s = ", ".join(f"{k} {v}" for k, v in sorted(kinds.items(), key=lambda kv: str(kv[0])) if k is not None)
    q = np.percentile(masses, [50, 90]) if len(masses) else [float("nan")] * 2
    return (f"rows {sum(kinds.values())}, scored {kinds.get(None, 0)}, skipped: {s or 'none'}; outside-H mass over {len(masses)} positions: "
            f"median {q[0]:.2%}, p90 {q[1]:.2%}"), float(q[0])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True); ap.add_argument("--rows", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--lexicon", default=DEFAULT_LEXICON)
    ap.add_argument("--workers", type=int, default=1, help="processes, each its own session (one thread), contiguous slices, same order")
    ap.add_argument("--limit", type=int, help="only the first N rows (timing run)")
    a = ap.parse_args(argv)
    rows = z.read_rows(rows=a.rows)[:a.limit]
    t0, n = time.time(), len(rows)
    cuts = [n * w // a.workers for w in range(a.workers + 1)]
    jobs = [(a.model_dir, a.lexicon, rows[i:j], i, f"{a.out}.part{w}") for w, (i, j) in enumerate(zip(cuts, cuts[1:]))]
    if a.workers > 1:
        import multiprocessing as mp
        with mp.get_context("spawn").Pool(a.workers) as pool:
            res = pool.map(_slice, jobs)
    else:
        res = [_slice(jobs[0])]
    kinds, masses = {}, []
    with open_text(a.out, "wt") as f:
        for (_, _, _, _, part), (pk, pm) in zip(jobs, res):
            with open(part, encoding="utf-8") as g:
                f.write(g.read())
            os.remove(part)
            masses += pm
            for k, v in pk.items():
                kinds[k] = kinds.get(k, 0) + v
    text, median = summary(kinds, masses)
    dt = time.time() - t0
    print(f"{text}; {dt:.0f} s, {n / max(dt, 1e-9):.1f} rows/s ({a.workers} worker(s)); wrote {a.out}")
    if median > STOP_MEDIAN:
        print(f"STOP: median outside-H mass above {STOP_MEDIAN:.0%} (registered stop condition: H is built wrong)")
        sys.exit(3)


if __name__ == "__main__":
    main()
