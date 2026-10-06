"""S5c scorer (offline): C-sent-fwd / C-sent-rev / C-pos against rank 1 and against S5j's Jev, same metrics as S5j.

  clef_score.py --sets S[,S] [--limit N]

Reads S5j's rows and jev-*.jsonl only through clef_run.verified_text (hash checked). Writes only
<clef out dir>/score.json (cvtune and any --limit run: ~/.cache/shanjie/work/s5-clef/, else experiments/s5-clef/results/).
The metric code is S5j's score_set/jev_conds arithmetic, copied (their callers write files); S5j's own
numbers are reproduced exactly, see test_clef.py.
"""
import argparse
import json
import os
import sys

import clef_run as R

s5 = R.s5
SENT_REV = ("J-sent-rev", "C-sent-rev")


def load_conds(texts, rows, clef):
    """texts: {cond: file text or None} -> {cond: dict(sent, first, cover, lat, tokens, mode, fail, agree, models)}.
    The Jev branch is S5j's jev_conds; for Clef a pick of None is a parse failure (the row keeps rank 1)."""
    out = {}
    for cond in R.CONDS:
        if texts.get(cond) is None:
            continue
        recs = [json.loads(l) for l in texts[cond].split("\n") if l.strip()]
        c = {"sent": {}, "first": {}, "lat": [r["secs"] * 1000 for r in recs], "tokens": sum(r["tokens"] for r in recs),
             "mode": "", "fail": 0, "agree": [], "models": sorted({r.get("model") for r in recs if r.get("model")}),
             "tok_est": any(r.get("tok_est") for r in recs)}
        if cond != "pos":
            asked = set()
            for r in recs:
                for n, (key, ans) in enumerate(zip(r["keys"], r["answers"])):
                    k = int(key)
                    asked.add(k)
                    j = r["picks"][n] if clef else int(ans["choice"][1:]) - 1
                    if clef and r["agree"][n] is not None:
                        c["agree"].append(r["agree"][n])
                    if j is None:
                        c["fail"] += 1
                        continue
                    cs = rows[k]["cands"][::-1] if cond == "sent-rev" else rows[k]["cands"]
                    c["sent"][k], c["first"][k] = cs[j], j == 0
            c["cover"] = asked if clef else set(c["sent"])
        else:
            adopt, modes = {}, set()
            for r in recs:
                for key, dec in zip(r["keys"], r["decisions"]):
                    if dec is None:
                        c["fail"] += 1
                        continue
                    j, mode = dec
                    k, p_ = map(int, key.split(":"))
                    modes.add(mode)
                    if j:
                        adopt.setdefault(k, {})[p_] = dict(s5.positions(rows[k]))[p_][j][p_]
            # rows whose sentence questions were sent are covered even with no position question
            covered = {int(k) for r in [json.loads(l) for l in texts["sent-fwd"].split("\n") if l.strip()]
                       for k in r["keys"]} if texts.get("sent-fwd") is not None else set()
            for k in covered:
                s = list(rows[k]["cands"][0])
                for p_, ch in adopt.get(k, {}).items():
                    s[p_] = ch
                c["sent"][k] = "".join(s)
            c["mode"] = "+".join(sorted(modes))
            c["cover"] = set(c["sent"])
        out[cond] = c
    return out


def metrics(name, cn, c, rows, ks, base):
    """S5j score_set's record for one condition on the 'all' subset."""
    L = base["L"]
    cov = sorted(ks & c["cover"])
    fin = {k: c["sent"].get(k, base["s"][k]) for k in cov}
    ok = [int(L(fin[k]) == base["gold"][k]) for k in cov]
    bok = [int(L(base["s"][k]) == base["gold"][k]) for k in cov]
    fixed, broken, p, se = s5.mcnemar(bok, ok)
    o8 = [k for k in cov if k in base["in8"]]
    lat = c["lat"]
    fk = [k for k in cov if k in c["first"]]
    rec = {"set": name, "subset": "all", "cond": cn, "n": len(cov), "base_acc": sum(bok) / len(cov), "acc": sum(ok) / len(cov),
           "oracle8": len(o8) / len(cov),
           "a1b8": (sum(int(L(fin[k]) == base["gold"][k]) for k in o8) / len(o8)) if o8 else None,
           "fixed": fixed, "broken": broken, "net": fixed - broken, "p": p, "se": se,
           "first_pick": (sum(c["first"].get(k, False) for k in fk) / max(1, len(fk))) if c["first"] else None,
           "blocked": 0, "unparsable": c["fail"] if cn[0] == "C" else 0, "errors": 0,
           "lat_p50": s5.pct(lat, .5), "lat_p95": s5.pct(lat, .95), "lat_max": max(lat) if lat else None,
           "lat_note": "per request (s*1000)", "jev_tokens": c["tokens"], "tau": None, "mode": c["mode"] or None}
    return rec, cov, ok


def score_set(name, texts_j, texts_c, rows, limit=0):
    """-> list of records: J-*, C-*, then the C-vs-J pairs. No file is touched."""
    L = s5.lenient_fn()
    ks = set(range(limit or len(rows)))
    base = {"L": L, "s": {k: r["cands"][0] for k, r in enumerate(rows)}, "gold": {k: L(r["truth"]) for k, r in enumerate(rows)}}
    base["in8"] = {k for k in range(len(rows)) if base["gold"][k] in [L(c) for c in rows[k]["cands"]]}
    conds = {}
    for pre, texts, clef in (("J", texts_j, False), ("C", texts_c, True)):
        for cn, c in load_conds(texts, rows, clef).items():
            conds[f"{pre}-{cn}"] = c
    out, oks = [], {}
    for cn, c in conds.items():
        rec, cov, ok = metrics(name, cn, c, rows, ks, base)
        oks[cn] = dict(zip(cov, ok))
        if cn in SENT_REV and cn.replace("rev", "fwd") in conds:
            fw, rv = conds[cn.replace("rev", "fwd")]["sent"], c["sent"]
            both = [k for k in cov if k in fw and k in rv]
            rec["flip"] = sum(fw[k] != rv[k] for k in both) / len(both) if both else None
        if cn[0] == "C":
            rec["choice_agree"] = sum(c["agree"]) / len(c["agree"]) if c["agree"] else None
            rec["models"] = c["models"]
            rec["tokens_estimated"] = c["tok_est"]
        out.append(rec)
    for cn in ("sent-fwd", "sent-rev", "pos"):
        if f"C-{cn}" in oks and f"J-{cn}" in oks:
            both = sorted(oks[f"C-{cn}"].keys() & oks[f"J-{cn}"].keys())
            fixed, broken, p, se = s5.mcnemar([oks[f"J-{cn}"][k] for k in both], [oks[f"C-{cn}"][k] for k in both])
            out.append({"set": name, "pair": f"C-{cn} vs J-{cn}", "n": len(both), "fixed": fixed, "broken": broken,
                        "net": fixed - broken, "p": p})
    return out


def verdict(recs):
    """Contract section 3, cvtune only. Returns printable lines."""
    sig = lambda r, s: r["p"] < 0.05 and r["net"] * s > 0  # noqa: E731
    lines = [f"vs rank 1: {r['cond']} {'CANDIDATE for H/S6' if sig(r, 1) else 'not a candidate'} (net={r['net']} p={r['p']:.4g})"
             for r in recs if r.get("cond", "")[:1] == "C"]
    pairs = [r for r in recs if "pair" in r]
    better, worse = any(sig(r, 1) for r in pairs), any(sig(r, -1) for r in pairs)
    word = ("mixed" if better and worse else "Clef better than Jev" if better else "Clef worse than Jev" if worse
            else "no significant difference" if pairs else "no pairs")
    return lines + [f"vs Jev: {word}"]


def main(argv=None, out_for=R.out_dir):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", required=True)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args(argv)
    sets = a.sets.split(",")
    if any(s not in R.ALLOW for s in sets):
        R.die("set not on the allowlist (cvtune, dev302, typing76)")
    for s in sets:
        rows = R.load_rows(s)
        tj = {c: R.verified_text(s, f"jev-{c}.jsonl") for c in R.CONDS}
        d = out_for(s, a.limit)
        tc = {c: (open(p, encoding="utf-8").read() if os.path.exists(p := os.path.join(d, f"clef-{c}.jsonl")) else None) for c in R.CONDS}
        recs = score_set(s, tj, tc, rows, a.limit)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "score.json"), "w") as f:
            json.dump(recs, f, indent=1)
        for r in recs:
            print(" ".join(f"{k}={round(v, 4) if isinstance(v, float) else v}" for k, v in r.items() if v is not None))
        if s == "cvtune":
            print("\n".join(verdict(recs)))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        sys.exit(f"s5c: unexpected {type(e).__name__}")
