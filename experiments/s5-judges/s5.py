"""S5j driver (docs/contracts/s5j-judges.md): sample, candidates, Apple and Jev judges, scoring.

  s5.py prep  --sets S[,S]   sample rows, generate top-8 candidates with the production CLI
  s5.py apple --sets S[,S]   A-fwd / A-rev (/ A-ctx for discordtune or with --synth-ctx), resumable
  s5.py jev   --sets S[,S]   J-sent-fwd / J-sent-rev / J-pos, resumable; allowlist checked before any network use
  s5.py score --sets S[,S]   all metrics; tau grid chosen on discordtune half A
Common: --limit N takes the first N rows (no sampling; smoke tests, public sets only).
Nothing here prints sentence text: stdout/stderr carry row numbers, counts and fixed strings only.
"""
import argparse
import json
import math
import os
import random
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "experiments", "s2"))
sys.path.insert(0, os.path.join(ROOT, "reference", "proto"))
sys.path.insert(0, os.path.join(ROOT, "experiments", "jev"))

SEED, TOP, BATCH = 20261004, 8, 20
JEV_ALLOW = ("cvtune", "dev302", "typing76")  # hard allowlist, checked before any network connection
PRIV = os.path.expanduser("~/side-project/shanjie-private/s5-judges")
CACHE = os.path.expanduser("~/.cache/shanjie/work/s5-judges")
RESULTS = os.path.join(HERE, "results")
CLI = os.path.join(ROOT, "target", "release", "shanjie-eval")
LM = os.path.join(ROOT, "data", "lm", "bigram.sjlm")
JUDGE = os.path.join(ROOT, "build", "s5-judge")
SIZES = {"discordtune": 1000, "cvtune": 1000}
CTX_SRC = {"discordtune": "discord-tune-rows.txt"}


def set_dir(name, limit):
    base = PRIV if name == "discordtune" else CACHE if name == "cvtune" else RESULTS
    d = os.path.join(base, name + (f"-n{limit}" if limit else ""))
    os.makedirs(d, exist_ok=True)
    return d


def die(msg):
    sys.exit(f"s5: {msg}")


# ---------------------------------------------------------------- prep
def load_rows(name, limit):
    """-> list of dict(i, truth, syls, ctx, half). i is the index in the set's own file order."""
    from iter2 import all_sets, PRIVATE
    sets = all_sets()
    if name not in sets:
        die(f"unknown set {name}")
    rows = sets[name][0]
    ctxs = [""] * len(rows)
    if name in CTX_SRC:  # rows_of drops the context field; re-read it with the same 3-field filter
        c = [l.split("|")[0] for l in open(os.path.join(PRIVATE, CTX_SRC[name]), encoding="utf-8").read().split("\n")
             if l.count("|") == 2]
        if len(c) != len(rows):
            die("context alignment mismatch")
        ctxs = c
    idx = list(range(len(rows)))
    if limit:
        if name in ("discordtune", "cvtune"):
            die("--limit is for public smoke sets only")
        idx = idx[:limit]
    elif name in SIZES:
        idx = random.Random(SEED).sample(idx, min(SIZES[name], len(idx)))
    h = len(idx) // 2
    return [{"i": i, "truth": rows[i][0], "syls": rows[i][1], "ctx": ctxs[i], "half": "A" if k < h else "B"}
            for k, i in enumerate(idx)]


def prep(name, limit):
    d = set_dir(name, limit)
    out = os.path.join(d, "rows.jsonl")
    if os.path.exists(out):
        return
    rows = load_rows(name, limit)
    rp, dp = os.path.join(d, "cli.rows"), os.path.join(d, "cli.dump")
    with open(rp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(f"|{r['truth']}|{' '.join(r['syls'])}\n")
    r = subprocess.run([CLI, "--lm", LM, "--profile", "chat", "--rows", rp, "--dump", dp], capture_output=True, text=True)
    if r.returncode != 0:
        die(f"cli failed ({r.returncode})")
    cands = {}
    for line in open(dp, encoding="utf-8"):
        i, _rank, surface, score = line.rstrip("\n").split("\t")
        c = cands.setdefault(int(i) - 1, {})
        if surface not in c:
            c[surface] = float(score)
    with open(out + ".tmp", "w", encoding="utf-8") as f:
        for k, r in enumerate(rows):
            items = list(cands[k].items())[:TOP]
            r["cands"] = [s for s, _ in items]
            r["margin"] = items[0][1] - items[1][1] if len(items) > 1 else 1e9
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(out + ".tmp", out)
    print(f"prep {name}: n={len(rows)}")


def read_rows(name, limit):
    prep(name, limit)
    return [json.loads(l) for l in open(os.path.join(set_dir(name, limit), "rows.jsonl"), encoding="utf-8")]


# ---------------------------------------------------------------- apple
def apple(name, limit, a):
    d = set_dir(name, limit)
    rows = read_rows(name, limit)
    use_ctx = name == "discordtune" or a.synth_ctx
    if a.synth_ctx and name in ("discordtune", "cvtune"):
        die("--synth-ctx is for public sets only")
    for r in rows:
        if a.synth_ctx and not r["ctx"]:
            r["ctx"] = a.synth_ctx
    conds = [("fwd", False, False), ("rev", True, False)] + ([("ctx", False, True)] if use_ctx else [])
    for cond, rev, ctx in conds:
        outp = os.path.join(d, f"apple-{cond}.tsv")
        done = set()
        if os.path.exists(outp):
            done = {int(l.split("\t")[0]) for l in open(outp, encoding="utf-8") if l.strip()}
        pend = [(k, r) for k, r in enumerate(rows) if k not in done and len(r["cands"]) > 1 and (r["ctx"] or not ctx)]
        if not pend:
            continue
        inp = os.path.join(d, f"apple-{cond}.in")
        with open(inp, "w", encoding="utf-8") as f:
            for k, r in pend:
                cs = r["cands"][::-1] if rev else r["cands"]
                f.write("\t".join([str(k), r["ctx"] if ctx else ""] + cs) + "\n")
        cmd = [JUDGE, inp, outp] + (["--force-fail"] if a.force_fail else [])
        p = subprocess.run(cmd, capture_output=True, text=True)
        print(f"apple {name} {cond}: rc={p.returncode} {p.stdout.strip()[:80]}")
        if p.returncode != 0:
            die("judge failed")
        with open(os.path.join(d, "apple-meta.json"), "w") as f:
            json.dump({"idle_window": bool(a.idle)}, f)


# ---------------------------------------------------------------- jev
INSTR = ("A user in Taiwan typed a Zhuyin (Bopomofo) phonetic input. Every option below is a sentence with "
         "exactly the same pronunciation. The text the user had already written just before it is: "
         "`rows[{i}].context` (it may be empty). Choose the option that is the sentence the user most likely "
         "meant: correct Traditional Chinese characters as used in Taiwan, grammatical, and sensible in context.")
POS_INSTR = ("A user in Taiwan typed a Zhuyin (Bopomofo) phonetic input. Every option below is the same sentence with "
             "one character position varied, and all options are pronounced the same. Choose the option the user "
             "most likely meant: correct Traditional Chinese characters as used in Taiwan, grammatical, and sensible.")


def positions(r):
    """-> [(position, options)] with options[0] = the unchanged top-1 sentence."""
    top = r["cands"][0]
    same = [c for c in r["cands"] if len(c) == len(top)]
    out = []
    for p in range(len(top)):
        opts = [top]
        for c in same:
            s = top[:p] + c[p] + top[p + 1:]
            if s not in opts:
                opts.append(s)
        if len(opts) > 1:
            out.append((p, opts))
    return out


def pos_decision(ans, crit):
    """Jevboard rule when per-option probabilities exist (p >= 0.5 and >= 2x unchanged); else the chosen option."""
    probs = next((v for v in ans.values() if isinstance(v, dict) and set(crit) <= set(v)
                  and all(isinstance(x, (int, float)) for x in v.values())), None)
    if probs is None:
        return int(ans["choice"][1:]) - 1, "choice"
    best = max(crit, key=lambda c: probs[c])
    j = int(best[1:]) - 1
    return (j if j > 0 and probs[best] >= 0.5 and probs[best] >= 2 * probs["c1"] else 0), "prob"


def jev(name, limit, a, client):
    d = set_dir(name, limit)
    rows = read_rows(name, limit)
    cap = a.jev_cap or None
    todo = list(enumerate(rows))[:cap]
    jobs = {"sent-fwd": [], "sent-rev": [], "pos": []}
    for k, r in todo:
        if len(r["cands"]) < 2:
            continue
        jobs["sent-fwd"].append((k, r["ctx"], r["cands"], INSTR))
        jobs["sent-rev"].append((k, r["ctx"], r["cands"][::-1], INSTR))
        for p, opts in positions(r):
            jobs["pos"].append((f"{k}:{p}", "", opts, POS_INSTR))
    for cond, items in jobs.items():
        outp = os.path.join(d, f"jev-{cond}.jsonl")
        done = set()
        if os.path.exists(outp):
            done = {x for l in open(outp, encoding="utf-8") for x in json.loads(l)["keys"]}
        items = [it for it in items if str(it[0]) not in done]
        for b in range(0, len(items), BATCH):
            chunk = items[b:b + BATCH]
            state = {"rows": [{"context": c} for _, c, _, _ in chunk]}
            qs = {f"q{n}": {"type": "choice", "instructions": ins.format(i=n) if "{i}" in ins else ins,
                            "criteria": {f"c{j + 1}": s for j, s in enumerate(cs)}}
                  for n, (_, _, cs, ins) in enumerate(chunk)}
            answers, usage, secs = client.ask(state, qs)
            rec = {"keys": [str(k) for k, *_ in chunk], "secs": secs, "tokens": usage.get("input_tokens", 0),
                   "answers": [answers[f"q{n}"] for n in range(len(chunk))]}
            if cond == "pos":
                rec["decisions"] = [pos_decision(rec["answers"][n], list(qs[f"q{n}"]["criteria"])) for n in range(len(chunk))]
            with open(outp, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"jev {name} {cond}: items={len(jobs[cond])} new_requests={math.ceil(len(items) / BATCH)}")


# ---------------------------------------------------------------- score
def mcnemar(base, new):
    fixed = sum(1 for x, y in zip(base, new) if x == 0 and y == 1)
    broken = sum(1 for x, y in zip(base, new) if x == 1 and y == 0)
    n = fixed + broken
    p = 1.0 if n == 0 else min(1.0, 2 * sum(math.comb(n, i) for i in range(min(fixed, broken) + 1)) / 2 ** n)
    d = [y - x for x, y in zip(base, new)]
    m = sum(d) / len(d) if d else 0
    se = math.sqrt(sum((v - m) ** 2 for v in d) / max(1, len(d) - 1) / max(1, len(d)))
    return fixed, broken, p, se


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, int(len(v) * q))] if v else None


def read_apple(d, cond, rows):
    """-> {row: (presented pick or 0, ms, status)}"""
    p = os.path.join(d, f"apple-{cond}.tsv")
    out = {}
    if os.path.exists(p):
        for l in open(p, encoding="utf-8"):
            k, pk, ms, st = l.rstrip("\n").split("\t")
            out[int(k)] = (int(pk), int(ms), st)
    return out


def pick_sent(rows, res, rev):
    """-> per row: picked sentence or None (no valid pick)."""
    out = []
    for k, r in enumerate(rows):
        pk = res.get(k, (0, 0, ""))[0]
        c = r["cands"][::-1] if rev else r["cands"]
        out.append(c[pk - 1] if pk else None)
    return out


def lenient_fn():
    from eval import lenient
    return lenient


def jev_conds(d, rows):
    """-> {cond: {"sent": {row: sentence}, "first": {row: bool}, "secs": [...], "tokens": int, "mode": str}}"""
    out = {}
    for cond in ("sent-fwd", "sent-rev", "pos"):
        p = os.path.join(d, f"jev-{cond}.jsonl")
        if not os.path.exists(p):
            continue
        recs = [json.loads(l) for l in open(p, encoding="utf-8")]
        c = {"sent": {}, "first": {}, "secs": [r["secs"] for r in recs], "tokens": sum(r["tokens"] for r in recs), "mode": ""}
        if cond != "pos":
            for r in recs:
                for key, ans in zip(r["keys"], r["answers"]):
                    k, j = int(key), int(ans["choice"][1:]) - 1
                    cs = rows[k]["cands"][::-1] if cond == "sent-rev" else rows[k]["cands"]
                    c["sent"][k], c["first"][k] = cs[j], j == 0
        else:
            adopt, covered, modes = {}, set(), set()
            for r in recs:
                for key, (j, mode) in zip(r["keys"], r["decisions"]):
                    k, p_ = map(int, key.split(":"))
                    modes.add(mode)
                    if j:
                        adopt.setdefault(k, {})[p_] = dict(positions(rows[k]))[p_][j][p_]
            # rows whose sentence questions were sent are covered even with no position question
            covered = {int(k) for r in [json.loads(l) for l in open(os.path.join(d, "jev-sent-fwd.jsonl"), encoding="utf-8")]
                       for k in r["keys"]} if os.path.exists(os.path.join(d, "jev-sent-fwd.jsonl")) else set()
            for k in covered:
                top = rows[k]["cands"][0]
                s = list(top)
                for p_, ch in adopt.get(k, {}).items():
                    s[p_] = ch
                c["sent"][k] = "".join(s)
            c["mode"] = "+".join(sorted(modes))
        out[cond] = c
    return out


def score_set(name, limit, a, taus):
    d = set_dir(name, limit)
    rows = read_rows(name, limit)
    L = lenient_fn()
    n_all = len(rows)
    base_s = {k: r["cands"][0] for k, r in enumerate(rows)}
    gold = {k: L(r["truth"]) for k, r in enumerate(rows)}
    in8 = {k for k in range(n_all) if gold[k] in [L(c) for c in rows[k]["cands"]]}
    cond = {}  # name -> dict(sent, first, status counts, lat, tokens)
    idle = False
    mp = os.path.join(d, "apple-meta.json")
    if os.path.exists(mp):
        idle = json.load(open(mp)).get("idle_window", False)
    ar = {c: read_apple(d, c, rows) for c in ("fwd", "rev", "ctx")}
    for c, rev in (("fwd", False), ("rev", True), ("ctx", False)):
        res = ar[c]
        if not res:
            continue
        sent = pick_sent(rows, res, rev)
        cond["A-" + c] = {"sent": {k: s for k, s in enumerate(sent) if s}, "first": {k: res[k][0] == 1 for k in res if res[k][0]},
                         "cover": set(res) if c == "ctx" else None,
                         "st": [v[2] for v in res.values()], "lat": [v[1] for v in res.values()]}
    if "A-fwd" in cond and "A-rev" in cond:
        f, r = cond["A-fwd"]["sent"], cond["A-rev"]["sent"]
        cond["A-both"] = {"sent": {k: f[k] for k in f if k in r and r[k] == f[k]}, "first": {}, "st": [], "lat": []}
    for cn, cj in jev_conds(d, rows).items():
        cond["J-" + cn] = {"sent": cj["sent"], "first": cj["first"], "cover": set(cj["sent"]), "st": [], "lat": [x * 1000 for x in cj["secs"]],
                          "tokens": cj["tokens"], "mode": cj["mode"]}
    # gated variants
    for g in ("A-fwd", "A-both"):
        if g in cond and taus.get(g) is not None:
            t = taus[g]
            cond[f"{g}@tau"] = {**cond[g], "sent": {k: s for k, s in cond[g]["sent"].items() if rows[k]["margin"] < t}, "tau": t}
    subsets = {"all": set(range(n_all))}
    if name == "discordtune":
        subsets.update({"A": {k for k, r in enumerate(rows) if r["half"] == "A"}, "B": {k for k, r in enumerate(rows) if r["half"] == "B"}})
    out = []
    for sub, ks in subsets.items():
        for cn, c in cond.items():
            if c.get("cover") is not None:
                cov = ks & c["cover"]
            else:
                cov = ks
            cov = sorted(cov)
            if not cov:
                continue
            fin = {k: c["sent"].get(k, base_s[k]) for k in cov}
            ok = [int(L(fin[k]) == gold[k]) for k in cov]
            ref = cond["A-fwd"]["sent"] if cn == "A-ctx" else {}  # A-ctx is paired with A-fwd, not with rank 1
            bok = [int(L(ref.get(k, base_s[k])) == gold[k]) for k in cov]
            fixed, broken, p, se = mcnemar(bok, ok)
            o8 = [k for k in cov if k in in8]
            st = c.get("st") or []
            lat = c["lat"]
            rec = {"set": name, "subset": sub, "cond": cn, "n": len(cov), "base_acc": sum(bok) / len(cov), "acc": sum(ok) / len(cov),
                   "oracle8": len(o8) / len(cov),
                   "a1b8": (sum(int(L(fin[k]) == gold[k]) for k in o8) / len(o8)) if o8 else None,
                   "fixed": fixed, "broken": broken, "net": fixed - broken, "p": p, "se": se,
                   "first_pick": (sum(c["first"].get(k, False) for k in cov if k in c["first"]) / max(1, len([k for k in cov if k in c["first"]]))) if c["first"] else None,
                   "blocked": st.count("blocked"), "unparsable": st.count("unparsable"), "errors": st.count("error"),
                   "lat_p50": pct(lat, .5), "lat_p95": pct(lat, .95), "lat_max": max(lat) if lat else None,
                   "lat_note": ("idle window" if idle else "load unknown") if cn.startswith("A-") else "per request (s*1000)",
                   "jev_tokens": c.get("tokens"), "tau": c.get("tau"), "mode": c.get("mode") or None}
            if cn in ("A-rev", "J-sent-rev", "A-both"):
                fw = cond["A-fwd" if cn[0] == "A" else "J-sent-fwd"]["sent"]
                rv = cond["A-rev" if cn[0] == "A" else "J-sent-rev"]["sent"]
                both = [k for k in cov if k in fw and k in rv]
                rec["flip"] = sum(fw[k] != rv[k] for k in both) / len(both) if both else None
            out.append(rec)
    with open(os.path.join(d, "score.json"), "w") as f:
        json.dump(out, f, indent=1)
    for r in out:
        print(" ".join(f"{k}={round(v, 4) if isinstance(v, float) else v}" for k, v in r.items() if v is not None))
    return rows, cond


def select_taus(rows, cond, whole=False):
    """tau grid = 10..90% quantiles of the sample's margins plus inf; best net fixed-broken per cond on half A."""
    L = lenient_fn()
    ms = sorted(r["margin"] for r in rows)
    grid = [ms[min(len(ms) - 1, int(len(ms) * q / 10))] for q in range(1, 10)] + [math.inf]
    ks = [k for k, r in enumerate(rows) if whole or r["half"] == "A"]
    taus = {}
    for g in ("A-fwd", "A-both"):
        if g not in cond:
            continue
        best = None
        for t in grid:
            net = 0
            for k in ks:
                s = cond[g]["sent"].get(k)
                if s is not None and rows[k]["margin"] < t:
                    net += int(L(s) == L(rows[k]["truth"])) - int(L(rows[k]["cands"][0]) == L(rows[k]["truth"]))
            if best is None or net > best[0]:
                best = (net, t)
        taus[g] = best[1]
        print(f"tau {g}: grid={[round(x, 3) for x in grid]} chosen={best[1]} net_on_A={best[0]}")
    return taus


def score(sets, limit, a):
    taus_path = os.path.join(PRIV, "tau.json")
    taus = {}
    if a.tau_self:
        pass
    elif os.path.exists(taus_path):
        taus = {k: (math.inf if v == "inf" else v) for k, v in json.load(open(taus_path)).items()}
    for name in sets:
        if name == "discordtune" or a.tau_self:
            # first pass without gating to choose tau, then rescore with it
            rows, cond = score_set_quiet(name, limit, a)
            taus = select_taus(rows, cond, whole=a.tau_self)
            if name == "discordtune":
                os.makedirs(PRIV, exist_ok=True)
                json.dump({k: ("inf" if v == math.inf else v) for k, v in taus.items()}, open(taus_path, "w"))
        score_set(name, limit, a, taus)


def score_set_quiet(name, limit, a):
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        return score_set(name, limit, a, {})


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["prep", "apple", "jev", "score"])
    ap.add_argument("--sets", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--synth-ctx", default="", help="apple: synthetic context for public smoke rows without one")
    ap.add_argument("--idle", action="store_true", help="apple: the Mac is idle (agreed window); latency counts as valid")
    ap.add_argument("--force-fail", action="store_true", help="apple: make every answer unparsable (error-path test)")
    ap.add_argument("--jev-cap", type=int, default=0, help="jev: only the first N rows")
    ap.add_argument("--tau-self", action="store_true", help="score: choose tau on the set itself (smoke only)")
    a = ap.parse_args()
    sets = a.sets.split(",")
    if a.cmd == "jev":  # allowlist FIRST: before the key is read or any connection opened
        bad = [s for s in sets if s not in JEV_ALLOW]
        if bad:
            die("set not on the Jev allowlist (cvtune, dev302, typing76)")
        from jev import Client
        client = Client()
    for name in sets:
        if a.cmd == "prep":
            prep(name, a.limit)
        elif a.cmd == "apple":
            apple(name, a.limit, a)
        elif a.cmd == "jev":
            jev(name, a.limit, a, client)
    if a.cmd == "score":
        score(sets, a.limit, a)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # never print e: messages can carry row text; keep only an HTTP status
        msg = str(e)
        sys.exit(f"s5: unexpected {type(e).__name__}" + (" " + " ".join(msg.split()[:3]) if msg.startswith("jev HTTP") else ""))
