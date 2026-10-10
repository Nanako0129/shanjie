"""P1a sample extraction and training (188). Subcommands:

  extract   runs-*.txt.gz + classes.json -> samples-<reading>.npz (features of the masked position, class, source, character)
  fit       samples-<reading>.npz -> weights/<reading>.sjw, trained twice; the two SHA-256 must be equal (exit 1 otherwise)
Both take --readings (comma-separated, default all six): stage 1 is --readings ㄗㄞˋ, stage 2 adds ㄗㄨㄛˋ, stage 3 the rest.

Sample rules: p1a.target_positions (section 2.1), p1a.sample_features (section 2.2), at most 150,000 per (reading, class, source)
in stream order (wiki first, then colloquial), colloquial weight 5. Needs the corpus-derived runs; see README.md.
"""
import argparse
import collections
import itertools
import json
import multiprocessing as mp
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import p1a  # noqa: E402
from p1a import bc  # noqa: E402
import prep  # noqa: E402

SRC_FILES = (("w", "runs-wiki.txt.gz"), ("c", "runs-colloq.txt.gz"))
_G = {}


def _init(model_path, tables):
    _G["lex"], _G["cls_of"], _G["tables"] = p1a.training_lexicon(), p1a.load_cls_of(model_path), tables


def _extract(args):
    runs, src, full = args
    lex, cls_of, tables = _G["lex"], _G["cls_of"], _G["tables"]
    out = {r: ([], [], []) for r in tables}   # label, codepoint, hashed ids
    for run in runs:
        for i, ch, r in p1a.target_positions(lex, bc.segment(lex, run)):
            if r not in tables:
                continue
            label = tables[r].get(ch, len(tables[r]))   # tables[r]: class char -> index; OTHER = len(table)
            if (r, label, src) in full:
                continue
            out[r][0].append(label); out[r][1].append(ord(ch)); out[r][2].append(p1a.sample_features(lex, cls_of, run, i))
    return out


def cmd_extract(a):
    classes = json.load(open(a.classes, encoding="utf-8"))
    use = [r for r in a.readings.split(",") if not classes[r]["skip"]]
    tables = {r: {c: j for j, c in enumerate(classes[r]["classes"])} for r in use}
    quota = p1a.Quota([(r, j, s) for r in use for j in range(len(tables[r]) + 1) for s in ("w", "c")])
    got = {r: {"y": [], "src": [], "ch": [], "ids": []} for r in use}
    for src, fname in SRC_FILES:
        with mp.Pool(a.procs, initializer=_init, initargs=(a.model, tables)) as pool:
            it, n = bc.batches(prep.iter_runs(os.path.join(a.runs, fname)), 500), 0
            while not quota.done(src):
                group = list(itertools.islice(it, a.procs * 4))
                if not group:
                    break
                full = quota.full()   # classes already full before this group: workers skip them (they would be rejected anyway)
                for res in pool.map(_extract, [(c, src, full) for c in group]):
                    for r, (labels, chars, ids) in res.items():
                        for lab, cp, x in zip(labels, chars, ids):
                            if quota.accept((r, lab, src)):
                                g = got[r]; g["y"].append(lab); g["src"].append(src == "c"); g["ch"].append(cp); g["ids"].append(x)
                n += len(group)
                if n % 100 < len(group):
                    print(f"{src}: {n * 500} runs", flush=True)
        print(f"{src}: stopped after ~{n * 500} runs, every class full: {quota.done(src)}", flush=True)
    os.makedirs(a.out, exist_ok=True)
    for r in use:
        g = got[r]
        np.savez(os.path.join(a.out, f"samples-{r}.npz"), y=np.array(g["y"], np.int8), src=np.array(g["src"], np.int8),
                 ch=np.array(g["ch"], np.int32), ids=np.array(g["ids"], np.int32).reshape(-1, p1a.NF))
        cnt = collections.Counter(g["y"])
        print(f"{r}: {len(g['y'])} samples, per class {[cnt[j] for j in range(len(tables[r]) + 1)]} (last = OTHER)")


def fit_one(reading, d, classes, lex):
    z = np.load(os.path.join(d, f"samples-{reading}.npz"))
    y, src, ch, ids = z["y"].astype(np.int64), z["src"], z["ch"], z["ids"]
    cl = classes[reading]["classes"]
    C = len(cl) + 1
    keep, cols = p1a.compact(ids)
    sw = np.where(src == 1, p1a.SRC_WEIGHT["c"], p1a.SRC_WEIGHT["w"])
    W, b = p1a.train_lr(cols, y, sw, C, len(keep) + 1)
    other = collections.Counter(chr(c) for c in ch[y == len(cl)].tolist())
    v = sum(1 for w, _ in lex.by_reading[(reading,)] if len(w) == 1 and w not in set(cl))
    meta = {"reading": reading, "classes": cl, "majority": cl[0], "other_counts": dict(other), "other_total": int(sum(other.values())),
            "v_other": v, "n_keep": int(len(keep)), "n_samples": int(len(y)), "hash_bits": p1a.HASH_BITS}
    return meta, keep, W, b


def cmd_fit(a):
    classes = json.load(open(a.classes, encoding="utf-8"))
    lex = p1a.training_lexicon()
    os.makedirs(a.out, exist_ok=True)
    bad = 0
    for r in a.readings.split(","):
        if classes[r]["skip"] or not os.path.exists(os.path.join(a.samples, f"samples-{r}.npz")):
            continue
        shas = []
        for run in (1, 2):
            meta, keep, W, b = fit_one(r, a.samples, classes, lex)
            shas.append(p1a.save_weights(os.path.join(a.out, f"{r}.sjw"), meta, keep, W, b))
        nz, size = p1a.int8_sparse_bytes(W.astype(np.float32), b)
        same = shas[0] == shas[1]
        bad += not same
        print(f"{r}: classes {meta['classes']}+OTHER, samples {meta['n_samples']}, features kept {meta['n_keep']}, non-zero {nz}, "
              f"int8 sparse {size} bytes, sha256 {shas[0]}, second run {'identical' if same else 'DIFFERENT ' + shas[1]}")
    sys.exit(1 if bad else 0)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract")
    e.add_argument("--runs", required=True); e.add_argument("--classes", required=True); e.add_argument("--model", required=True)
    e.add_argument("--out", required=True); e.add_argument("--readings", default=",".join(p1a.READINGS))
    e.add_argument("--procs", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    f = sub.add_parser("fit")
    f.add_argument("--samples", required=True); f.add_argument("--classes", required=True); f.add_argument("--out", required=True)
    f.add_argument("--readings", default=",".join(p1a.READINGS))
    a = ap.parse_args()
    {"extract": cmd_extract, "fit": cmd_fit}[a.cmd](a)


if __name__ == "__main__":
    main()
