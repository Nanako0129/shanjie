"""X0c (docs/contracts/x0c-compare.md 2.3): score the candidates of a `shanjie-eval --dump` with the 0c model `Funmula/0c-sequence-d512`.

  experiments/x0c/run_sandboxed.sh --out DIR -- experiments/x0c/score_0c.py --model-dir M --dump D --v3 T (--rows FILE | --dev N) --out OUT.jsonl
  experiments/x0c/run_sandboxed.sh --out DIR -- experiments/x0c/score_0c.py --model-dir M --dump D --v3 T --dev 302 --latency

The input format and score() are rewritten from the model card's example (read, never run): onnxruntime + numpy + the standard library only.
Candidates come from D (top 8 in n-gram order); n / ng / ok / ed / glen are copied from the tinylm score file T of the same run (so they are
equal by construction). One record per row: those five, `skip`, and for rows with n >= 2 `ll` (0c log P(candidate | last 64 characters of
the prefix, reading) in log10), `ll0` (empty prefix; equal to `ll` when the prefix is empty), `llr` (every syllable first tone, no
neutral retry: the card's retry would put the neutral tone back on the last syllable). A row any of whose
candidates cannot be scored (a character outside the vocabulary, a length that is not the syllable count, 2-32 syllables violated, a
syllable base outside the vocabulary) has skip = 1 and ll = ll0 = llr = 0, so fusegrid.pick keeps the n-gram first.

The model is loaded once, from bytes whose sha256 is checked against the pinned values below (commit 66eb4a81 of the model repo; also
against manifest.json, itself pinned); no sys.path tricks, no repo imports (python -I). Every score must be finite.
"""
import argparse
import glob
import hashlib
import json
import math
import os
import time

import numpy as np

LN10 = math.log(10.0)
TONES = {"ˊ": 2, "ˇ": 3, "ˋ": 4, "˙": 5}   # no mark = first tone
CONTEXT = 64
MIN_SYL, MAX_SYL = 2, 32
TOPK = 8
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Funmula/0c-sequence-d512 at commit 66eb4a810c781258fc0cca3fe8b52819cf6a4693 (research log 2026-10-13, X0c).
PINNED = {"manifest.json": "f8d737cf44e9ff8f90e7108f1e9df4fece0cdcae254e050968fc6ef723ee1976",
          "model.onnx": "71af1f0e77b2752cc71cbc04bf19c8e8f2e765662246f766e65a2822bab4e66e",
          "vocab.json": "556dffaffbf4c05211a846dcee55ef735cea68c5754f9bffcc6f9588124172b3"}


class Unscorable(Exception):
    """kind: oov | length | syllables | base"""


class Zuin:
    """vocab + manifest + a session-like object with run(names, feeds) -> [array of scores per batch row]."""

    def __init__(self, session, vocab, manifest):
        self.session = session
        self.chars = {c: i + 8 for i, c in enumerate(vocab["characters"])}   # 0-7 special, 4 = UNK
        self.bases = {b: i + 2 for i, b in enumerate(vocab["bases"])}
        self.neutral = set(manifest.get("neutral_tolerant_last", []))


def load_model(model_dir, threads=None):
    """Read each file once, check it against PINNED (and model / vocab against manifest.json), then InferenceSession(those bytes, CPU only).
    `threads` = 1 for the latency run."""
    import onnxruntime as ort   # only here: the tests use a fake session and never import it
    data = {}
    for name in PINNED:
        with open(os.path.join(model_dir, name), "rb") as f:
            data[name] = f.read()
        if hashlib.sha256(data[name]).hexdigest() != PINNED[name]:
            raise SystemExit(f"score_0c: STOP, sha256 of {name} is not the pinned one")
    manifest = json.loads(data["manifest.json"])
    for name in ("model.onnx", "vocab.json"):
        if manifest["files"][name] != PINNED[name]:
            raise SystemExit(f"score_0c: STOP, manifest.json disagrees with the pinned sha256 of {name}")
    return Zuin(make_session(data["model.onnx"], ort, threads), json.loads(data["vocab.json"]), manifest)


def make_session(blob, ort, threads):
    so = ort.SessionOptions()
    if threads:
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = 1
    return ort.InferenceSession(blob, so, providers=["CPUExecutionProvider"])


def syllable(m, s):
    base = s[:-1] if s[-1] in TONES else s
    if base not in m.bases:
        raise Unscorable("base")
    return m.bases[base], TONES.get(s[-1], 1)


def feeds_for(m, context, reading, cands):
    """Model inputs, the model card's layout: [BOS] context[-64:] [SEP] reading x n [OUT] candidate[:-1], scored at the n positions from [OUT].
    Several candidates are packed into one sequence as a prefix tree (the card's closing note: `mask` = ancestor mask, one `target_node` /
    `target_ids` row per candidate, positions = depth); one candidate gives exactly the card's input. The model's batch size is 1."""
    head = [1] + [m.chars.get(c, 4) for c in context[-CONTEXT:]] + [2]
    sounds = [syllable(m, s) for s in reading]
    n, out = len(reading), len(head) + len(reading)
    tok, depth, parent, node_of = [3], [0], [-1], {(): 0}   # node 0 = [OUT]; node k predicts the character after its path
    tg, tid = [], []
    for c in cands:
        if len(c) != n:
            raise Unscorable("length")
        try:
            cid = [m.chars[ch] for ch in c]
        except KeyError:
            raise Unscorable("oov") from None
        path = ()
        row = [0]
        for j in range(n - 1):
            path += (cid[j],)
            if path not in node_of:
                node_of[path] = len(tok)
                tok.append(cid[j]); depth.append(j + 1); parent.append(node_of[path[:-1]])
            row.append(node_of[path])
        tg.append(row); tid.append([x - 6 for x in cid])
    L = out + len(tok)
    ids = head + [0] * n + tok
    bases = [0] * len(head) + [b for b, _ in sounds] + [0] * len(tok)
    tones = [0] * len(head) + [t for _, t in sounds] + [0] * len(tok)
    types = [0] * len(head) + [1] * n + [0] + [2] * (len(tok) - 1)
    pos = list(range(out)) + [out + d for d in depth]
    mask = np.tril(np.ones((L, L), dtype=bool))
    if len(tok) > 1:   # tree nodes see the head, [OUT] and their own ancestors only
        mask[out:, out:] = False
        for k in range(len(tok)):
            i = k
            while i >= 0:
                mask[out + k, out + i] = True
                i = parent[i]
    row = lambda x: np.array([x], dtype=np.int64)
    return {"ids": row(ids), "bases": row(bases), "tones": row(tones), "types": row(types), "positions": row(pos),
            "mask": mask[None, None], "proj_pos": np.arange(out, L, dtype=np.int64),
            "target_node": np.array(tg, dtype=np.int64), "target_ids": np.array(tid, dtype=np.int64)}


def log_prob(m, context, reading, cands, packed=True):
    """ln P(candidate | context, reading) for each candidate: one run for all (prefix tree), or one run per candidate (the card's example)."""
    if not packed:
        return [log_prob(m, context, reading, [c])[0] for c in cands]
    return [float(x) for x in np.asarray(m.session.run(["scores"], feeds_for(m, context, reading, cands))[0]).reshape(-1)]


def score(m, context, reading, cands, packed=True):
    """The model card's score(): the last syllable is also tried as neutral tone when its base is in neutral_tolerant_last (and it is not
    already neutral), the larger one is kept. manifest's neutral_expand_penalty is NOT used by the card's example (see the research log)."""
    s = log_prob(m, context, reading, cands, packed)
    last = reading[-1]
    base = last[:-1] if last[-1] in TONES else last
    if base in m.neutral and last[-1] != "˙":
        s = [max(a, b) for a, b in zip(s, log_prob(m, context, reading[:-1] + [base + "˙"], cands, packed))]
    return s


def strip_tones(reading):
    return [s[:-1] if s[-1] in TONES else s for s in reading]


def check_row(m, reading, cands):
    """Raises Unscorable(kind) for the first problem found (oov, length, syllables, base order)."""
    for c in cands:
        if any(ch not in m.chars for ch in c):
            raise Unscorable("oov")
    if any(len(c) != len(reading) for c in cands):
        raise Unscorable("length")
    if not MIN_SYL <= len(reading) <= MAX_SYL:
        raise Unscorable("syllables")
    for s in reading:
        syllable(m, s)


def record(m, t, prefix, reading, cands):
    """One output record from the T row `t` (n, ng, ok, ed, glen copied), the row's prefix, reading syllables and the n candidates."""
    r = {k: t[k] for k in ("i", "n", "ng", "ok", "ed", "glen")}
    r["skip"] = 0
    if t["n"] < 2:
        return r, None
    try:
        check_row(m, reading, cands)
    except Unscorable as e:
        r.update(skip=1, ll=[0.0] * t["n"], ll0=[0.0] * t["n"], llr=[0.0] * t["n"])
        return r, e.args[0]
    r["ll"] = finite([x / LN10 for x in score(m, prefix, reading, cands)])
    r["ll0"] = r["ll"] if not prefix[-CONTEXT:] else finite([x / LN10 for x in score(m, "", reading, cands)])
    r["llr"] = finite([x / LN10 for x in log_prob(m, prefix, strip_tones(reading), cands)])
    return r, None


def finite(xs):
    """The scores, or a stop: a NaN or an infinity would make fusegrid.pick's comparisons silently order-dependent."""
    if not all(math.isfinite(x) for x in xs):
        raise SystemExit(f"score_0c: STOP, non-finite score {xs}")
    return xs


# ------------------------------------------------------------------ inputs (same readers as lm_eval.py / score.py, standalone)
def read_rows(rows=None, dev=None):
    """[(gold, reading syllables, prefix)]: lm_eval.py's rows_of (3 fields only); --dev N = first N rows of eval/dev/*.txt, sorted."""
    paths = sorted(glob.glob(os.path.join(ROOT, "eval", "dev", "*.txt"))) if dev else [rows]
    out = []
    for f in paths:
        for line in open(f, encoding="utf-8", newline="\n"):   # split on \n only, as the CLI does
            p = line.rstrip("\n").split("|")
            if len(p) == 3:
                out.append((p[1], p[2].split(), p[0]))
    return out[:dev] if dev else out


def read_dump(path, n_rows, topk=TOPK):
    """`row<TAB>rank<TAB>surface<TAB>score` -> per row the surfaces of ranks 1..topk."""
    out = [[] for _ in range(n_rows)]
    for line in open(path, encoding="utf-8"):
        i, rank, surf, _ = line.rstrip("\n").split("\t")
        if int(rank) <= topk:
            out[int(i) - 1].append(surf)
    return out


def read_jsonl(path):
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True); ap.add_argument("--dump", required=True); ap.add_argument("--v3", required=True)
    ap.add_argument("--rows"); ap.add_argument("--dev", type=int); ap.add_argument("--out")
    ap.add_argument("--latency", action="store_true", help="time score() per row (8 candidates, ll only), one thread, 3 rounds")
    ap.add_argument("--workers", type=int, default=1, help="processes, each loading the model with one thread and scoring a contiguous "
                    "slice of the rows; the output is the same file, in row order")
    a = ap.parse_args(argv)
    if bool(a.rows) == bool(a.dev):
        ap.error("exactly one of --rows / --dev")
    rows = read_rows(a.rows, a.dev)
    ts = read_jsonl(a.v3)
    cands = read_dump(a.dump, len(rows))
    if len(ts) != len(rows) or any(len(c) != t["n"] or len(r[0]) != t["glen"] for r, c, t in zip(rows, cands, ts)):
        raise SystemExit("score_0c: STOP, the dump, the rows and the tinylm score file do not describe the same candidates")
    with open(a.dump, "rb") as f:
        print(f"dump sha256 {hashlib.sha256(f.read()).hexdigest()}, rows {len(rows)}", flush=True)
    if a.latency:
        return latency(load_model(a.model_dir, 1), rows, cands, ts)
    if not a.out:
        ap.error("--out is required (or --latency)")
    kinds, t0 = {}, time.time()
    if a.workers > 1:
        import multiprocessing as mp
        n = len(rows)
        cuts = [n * w // a.workers for w in range(a.workers + 1)]
        jobs = [(a.model_dir, rows[i:j], cands[i:j], ts[i:j], f"{a.out}.part{w}") for w, (i, j) in enumerate(zip(cuts, cuts[1:]))]
        with mp.get_context("spawn").Pool(a.workers) as pool:
            parts = pool.map(_score_slice, jobs)
        with open(a.out, "w", encoding="utf-8", newline="\n") as f:
            for (_, _, _, _, part), pk in zip(jobs, parts):
                f.write(open(part, encoding="utf-8").read())
                os.remove(part)
                for k, v in pk.items():
                    kinds[k] = kinds.get(k, 0) + v
        print(f"  {n} rows, {a.workers} workers, {time.time() - t0:.0f} s", flush=True)
    else:
        m = load_model(a.model_dir)
        with open(a.out, "w", encoding="utf-8", newline="\n") as f:
            for k, ((gold, syl, prefix), c, t) in enumerate(zip(rows, cands, ts), 1):
                r, kind = record(m, t, prefix, syl, c)
                kinds[kind] = kinds.get(kind, 0) + 1
                f.write(json.dumps(r) + "\n")
                if k % 500 == 0:
                    print(f"  {k} rows, {time.time() - t0:.0f} s", flush=True)
    print(f"unscorable rows (skip = 1) of {len(rows)}: oov {kinds.get('oov', 0)}, length {kinds.get('length', 0)}, "
          f"syllables {kinds.get('syllables', 0)}, base {kinds.get('base', 0)}; wrote {a.out}")


def _score_slice(job):
    """One --workers process: its own model (one thread) and a contiguous slice of rows -> a part file; returns the skip kinds."""
    model_dir, rows, cands, ts, part = job
    m, kinds = load_model(model_dir, 1), {}
    with open(part, "w", encoding="utf-8", newline="\n") as f:
        for (gold, syl, prefix), c, t in zip(rows, cands, ts):
            r, kind = record(m, t, prefix, syl, c)
            kinds[kind] = kinds.get(kind, 0) + 1
            f.write(json.dumps(r) + "\n")
    return kinds


def latency(m, rows, cands, ts):
    """Per-row score() time (8 candidates, ll column, with the neutral retry), one thread, 3 rounds. End-to-end Python time: building the
    feeds (numpy, the L x L mask) is inside the timed region, unlike the Rust tinylm number. p95 is element int(n * 0.95), as the CLI's."""
    items = []
    for (g, syl, prefix), c, t in zip(rows, cands, ts):
        if t["n"] >= 2:
            try:
                check_row(m, syl, c)
                items.append((prefix, syl, c))
            except Unscorable:
                pass
    print(f"timed rows {len(items)} of {len(rows)} (scorable, n >= 2)")
    for packed in (True, False):
        p50, p95 = [], []
        for _ in range(3):
            ms = []
            for prefix, syl, c in items:
                t = time.perf_counter()
                score(m, prefix, syl, c, packed)
                ms.append(1000 * (time.perf_counter() - t))
            ms.sort()
            p50.append(ms[len(ms) // 2]); p95.append(ms[int(len(ms) * 0.95)])
        print(f"{'packed (one run per row, prefix tree)' if packed else 'one run per candidate (card)'}: rounds p50 {[round(x, 2) for x in p50]} "
              f"p95 {[round(x, 2) for x in p95]} ms; median p50 {sorted(p50)[1]:.2f} ms, p95 {sorted(p95)[1]:.2f} ms")


if __name__ == "__main__":
    main()
