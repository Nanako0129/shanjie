"""S5k scoring (contract §4/§5). Pure python (any interpreter). stdout: numbers and fixed strings only.

  score.py --set dev302 [--limit 20] [--tau-self]
Pairs with S5j's A-fwd (and J-sent-fwd where the file exists); S5j files are hash-checked before opening.
"""
import argparse
import json
import math
import os

import s5k
from s5 import lenient_fn, mcnemar, pct, positions  # S5j pure functions only

BASE_CONDS = ("L-noul", "B1-ll", "B4-ll", "Q-ll")  # paired with S5j's A-fwd / J-sent-fwd


def load_cond(d, rows, cn, mode):
    p = s5k.result_path(d, cn, mode)
    if not os.path.exists(p):
        return None
    recs = [json.loads(l) for l in open(p, encoding="utf-8")]
    c = {"sent": {}, "first": {}, "scores": {}, "cover": {r["k"] for r in recs}, "lat": [r["ms"] for r in recs]}
    for r in recs:
        k, cs = r["k"], rows[r["k"]]["cands"]
        if "adopt" in r:
            s, opts = list(cs[0]), dict(positions(rows[k]))
            for p_, j in r["adopt"]:
                s[p_] = opts[p_][j][p_]
            c["sent"][k] = "".join(s)
        elif r["scores"] is not None:
            j = s5k.pick(r["scores"])
            c["sent"][k], c["first"][k], c["scores"][k] = cs[j], j == 0, r["scores"]
    return c


def s5j_pairs(name, rows):
    a = {}
    for l in s5k.read_verified(name, "apple-fwd.tsv").splitlines():
        k, pk, _ms, _st = l.split("\t")
        if int(k) < len(rows) and int(pk):
            a[int(k)] = rows[int(k)]["cands"][int(pk) - 1]
    out = {"A-fwd": a}
    if (name, "jev-sent-fwd.jsonl") in s5k.HASHES:
        j = {}
        for l in s5k.read_verified(name, "jev-sent-fwd.jsonl").splitlines():
            r = json.loads(l)
            for key, ans in zip(r["keys"], r["answers"]):
                if int(key) < len(rows):
                    j[int(key)] = rows[int(key)]["cands"][int(ans["choice"][1:]) - 1]
        out["J-sent-fwd"] = j
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tau-self", action="store_true", help="choose tau on the set itself (smoke only)")
    a = ap.parse_args()
    name = a.set
    rows = s5k.load_rows(name, a.limit)
    L = lenient_fn()
    d = s5k.out_dir(name, a.limit)
    n = len(rows)
    base = {k: r["cands"][0] for k, r in enumerate(rows)}
    gold = {k: L(r["truth"]) for k, r in enumerate(rows)}
    in8 = {k for k in range(n) if gold[k] in [L(c) for c in rows[k]["cands"]]}
    ok = lambda s, k: int(L(s) == gold[k])
    cond = {}  # "X" = no context; "X+ctx" = context (rows that have one); "X+ctxall" = context where present, else none
    for cn in s5k.CONDS:
        for mode, suf in (("none", ""), ("real" if name == "discordtune" else "synth", "+ctx")):
            c = load_cond(d, rows, cn, mode)
            if c:
                cond[cn + suf] = c
    for cn in s5k.CONDS:  # contract §8.2(h) and the with/without-context question (not a candidate test)
        if cn in cond and cn + "+ctx" in cond:
            w, wo = cond[cn + "+ctx"], cond[cn]
            both = [k for k in w["cover"] if k in wo["cover"]]
            key = "scores" if w["scores"] else "sent"
            diff = sum(1 for k in both if w[key].get(k) != wo[key].get(k))
            f, b, p, _ = mcnemar([ok(wo["sent"].get(k, base[k]), k) for k in both], [ok(w["sent"].get(k, base[k]), k) for k in both])
            print(f"ctxdiff {cn} rows={len(both)} score_differs={diff} ctx_fixed={f} ctx_broken={b} p={p:.4f}")
            if name == "discordtune":
                cond[cn + "+ctxall"] = {"sent": {**wo["sent"], **w["sent"]}, "first": {**wo["first"], **w["first"]},
                                        "scores": {}, "cover": wo["cover"], "lat": w["lat"]}
    pairs = s5j_pairs(name, rows)
    mp = os.path.join(d, "meta.jsonl")
    meta = [json.loads(l) for l in open(mp)] if os.path.exists(mp) else []
    idle = bool(meta) and all(m.get("load_note") == "idle window" for m in meta)
    ms = sorted(r["margin"] for r in rows)
    grid = [ms[min(n - 1, int(n * q / 10))] for q in range(1, 10)] + [math.inf]
    tpath = s5k.L_PRIV + "/tau.json"
    taus = {}
    if a.tau_self or name == "discordtune":  # choose on discordtune half A (or the smoke set itself), per condition
        halfA = [k for k in range(n) if a.tau_self or rows[k]["half"] == "A"]
        for cn, c in cond.items():
            taus[cn] = max(((sum(ok(c["sent"][k], k) - ok(base[k], k) for k in halfA if k in c["sent"] and rows[k]["margin"] < t), -i, t)
                            for i, t in enumerate(grid)))[2]
        if name == "discordtune" and not a.tau_self:
            os.makedirs(s5k.L_PRIV, exist_ok=True)
            json.dump({k: ("inf" if v == math.inf else v) for k, v in taus.items()}, open(tpath, "w"))
    elif os.path.exists(tpath):
        taus = {k: math.inf if v == "inf" else v for k, v in json.load(open(tpath)).items()}
    else:
        s5k.die("TAU MISSING")  # gated rows must not vanish silently (set SHANJIE_PRIVATE, score discordtune first)
    for cn in list(cond):
        if cn in taus:
            c = cond[cn]
            g = {k: s for k, s in c["sent"].items() if rows[k]["margin"] < taus[cn]}
            # final picks: a row the gate leaves alone keeps rank 1
            cond[cn + "@tau"] = {**c, "sent": g, "first": {k: (c["first"][k] if k in g else True) for k in c["first"]}, "tau": taus[cn]}
    subs = {"all": set(range(n))}
    if name == "discordtune":
        subs |= {h: {k for k, r in enumerate(rows) if r["half"] == h} for h in "AB"}
    out = []
    for sub, ks in subs.items():
        for cn, c in cond.items():
            cov = sorted(ks & c["cover"])
            if not cov:
                continue
            fin = {k: c["sent"].get(k, base[k]) for k in cov}
            okc = [ok(fin[k], k) for k in cov]
            bok = [ok(base[k], k) for k in cov]
            fx, br, p, se = mcnemar(bok, okc)
            o8 = [k for k in cov if k in in8]
            rec = {"set": name, "subset": sub, "cond": cn, "n": len(cov), "base_acc": sum(bok) / len(cov),
                   "acc": sum(okc) / len(cov), "oracle8": len(o8) / len(cov),
                   "a1b8": sum(ok(fin[k], k) for k in o8) / len(o8) if o8 else None,
                   "fixed": fx, "broken": br, "net": fx - br, "p": p, "se": se,
                   "first_pick": sum(c["first"][k] for k in cov if k in c["first"]) / max(1, sum(1 for k in cov if k in c["first"])) if c["first"] else None,
                   "tau": c.get("tau"), "lat_note": "idle window" if idle else "load unknown"}
            if cn.split("+")[0].split("@")[0] in BASE_CONDS:
                for ref, rs in pairs.items():
                    com = [k for k in cov if k in rs]
                    if com:
                        f2, b2, p2, _ = mcnemar([ok(rs[k], k) for k in com], [ok(fin[k], k) for k in com])
                        rec[f"vs_{ref}"] = f"n={len(com)} fixed={f2} broken={b2} p={p2:.4f}"
            lat = cond[cn.split("@")[0]]["lat"]
            if sub == "all" and len(lat) > 1:
                # lat[0] is "first row after load" only for the condition that starts its process (L-noul; every ll model)
                rec.update(lat_first=lat[0] if cn.split("+")[0].split("@")[0] in BASE_CONDS else "—", lat_p50=pct(lat[1:], .5), lat_p95=pct(lat[1:], .95), lat_max=max(lat[1:]))
            if cn.startswith("L-choice-rev"):
                fw = cond[cn.replace("rev", "fwd")]["sent"]
                both = [k for k in cov if k in fw and k in c["sent"]]
                rec["flip"] = sum(fw[k] != c["sent"][k] for k in both) / len(both) if both else None
            out.append(rec)
    json.dump({"rows": out, "load": meta}, open(os.path.join(d, "score.json"), "w"), indent=1)
    for r in out:
        print(" ".join(f"{k}={round(v, 4) if isinstance(v, float) else v}" for k, v in r.items() if v is not None))
    for m in meta:
        print("meta", " ".join(f"{k}={v}" for k, v in m.items()))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        raise SystemExit(f"s5k: unexpected {type(e).__name__}")
