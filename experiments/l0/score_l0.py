"""L0 scoring: candidates (top 8 of the Rust CLI --dump) -> numbers-only score files (JSON lines, no text) that gate.py reads.

  python score_l0.py l0  --model l0-w1.pt --rows R.txt --dump R.dump --name NAME --expect-top1 N --context 64|2 --out L0w1.c64.NAME.jsonl [--device cpu] [--bs 64]
  python score_l0.py m64 --npz tinylm-M.npz --rows R.txt --dump R.dump --name NAME --expect-top1 N --out M64.NAME.jsonl [--backend numpy|torch] [--bs 64]

Record: i (1-based), n (candidates, <= 8), ok (lenient-correct 0/1 per candidate), s (per-option scores: `l0` = keep node then ranks 2..n;
`m64` = M's LL per candidate, no keep node). noscore: 1 (and no s) when a candidate is too long to encode (`l0`: > 62 characters,
`m64`: > 63); rows with one candidate get s = [0.0]. --expect-top1 is the top1 of the CLI summary line for the same rows and settings;
it is recomputed from the dump and the run stops if it differs (as score.py does). `m64` uses k = the last 63 Han characters of the
prefix, punctuation skipped (ll_many cuts it further when [BOS] k c would exceed 64). M's shipped-context LL is experiments/s7a-tinylm/score.py.
`l0 --context 2` uses k = tinylm.context_key(prefix) (<= 2 Han characters, what the App passes today) instead of the long context.
"""
import argparse
import hashlib
import json
import sys

import l0core
import score        # experiments/s7a-tinylm/score.py: load_rows, load_dump, top1_from_dump, load_model
import tinylm


def prepare(a):
    rows = score.load_rows(rows=a.rows)
    with open(a.dump, encoding="utf-8") as f:
        cands = score.load_dump(f, len(rows))
    t1 = score.top1_from_dump(rows, cands)
    with open(a.dump, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    print(f"{a.name}: rows {len(rows)}, dump sha256 {sha}, dump candidates {sum(map(len, cands))}, "
          f"n-gram top1 from the dump {t1}, summary line {a.expect_top1}")
    if t1 != a.expect_top1:
        sys.exit("score_l0: STOP, top1 recomputed from the dump differs from the summary line")
    recs = []
    for i, ((gold, _syl, _prefix), cs) in enumerate(zip(rows, cands), 1):
        recs.append({"i": i, "n": len(cs), "ok": l0core.ok_flags(gold, [s for s, _ in cs])})
    return rows, cands, recs


def write(recs, out, name):
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    print(f"{name}: rows {len(recs)}, rows with one candidate {sum(r['n'] < 2 for r in recs)}, "
          f"noscore {sum('noscore' in r for r in recs)}; wrote {out}")


def cmd_l0(a):
    rows, cands, recs = prepare(a)
    import torch
    import torch_l0
    device = a.device or ("cuda" if torch.cuda.is_available() else "cpu")
    model, vocab, _meta = torch_l0.load_l0(a.model, device)
    packs, todo = [], []
    for (_g, _s, prefix), cs, r in zip(rows, cands, recs):
        if r["n"] < 2:
            r["s"] = [0.0] * r["n"]
        elif l0core.too_long([s for s, _ in cs]):
            r["noscore"] = 1
        else:
            packs.append(l0core.pack_row(vocab, prefix, cs, a.context))
            todo.append(r)
    for r, s in zip(todo, torch_l0.score_packs(model, packs, device, a.bs)):
        r["s"] = s
    write(recs, a.out, a.name)


def cmd_m64(a):
    rows, cands, recs = prepare(a)
    model = score.load_model(a.npz, a.backend)
    items, todo = [], []
    for (_g, _s, prefix), cs, r in zip(rows, cands, recs):
        if r["n"] < 2:
            r["s"] = [0.0] * r["n"]
        elif any(len(s) > tinylm.CTX - 1 for s, _ in cs):
            r["noscore"] = 1
        else:
            k = l0core.k_cross(prefix, tinylm.CTX - 1)
            items += [(k, s) for s, _ in cs]
            todo.append(r)
    ll = tinylm.ll_many(model, items, a.bs)
    pos = 0
    for r in todo:
        r["s"] = ll[pos:pos + r["n"]].tolist()
        pos += r["n"]
    write(recs, a.out, a.name)


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for cmd in ("l0", "m64"):
        p = sub.add_parser(cmd)
        p.add_argument("--rows", required=True); p.add_argument("--dump", required=True); p.add_argument("--name", required=True)
        p.add_argument("--expect-top1", type=int, required=True); p.add_argument("--out", required=True)
        p.add_argument("--bs", type=int, default=64)
    p = sub.choices["l0"]
    p.add_argument("--model", required=True); p.add_argument("--context", type=int, choices=[64, 2], required=True)
    p.add_argument("--device")
    p = sub.choices["m64"]
    p.add_argument("--npz", required=True); p.add_argument("--backend", choices=["numpy", "torch"], default="numpy")
    a = ap.parse_args(argv)
    {"l0": cmd_l0, "m64": cmd_m64}[a.cmd](a)


if __name__ == "__main__":
    main()
