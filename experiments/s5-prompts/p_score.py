"""S5p scorer (offline), contract section 5.

  p_score.py select                       A half: whole-sentence accuracy of V1-V4 per provider, best variant (ties to the lower number)
  p_score.py test --jev vN --clef vN      B half: exactly the 5 (jev+clef) or 8 (all three providers) pre-named tests, plus flip rate and latency/cost for the report

V0 files are hash checked: Jev's through clef_run.verified_text, Clef's against p_run.CLEF_V0_SHA.
Nothing here prints sentence text.
"""
import argparse
import hashlib
import json
import os
import sys

sys.dont_write_bytecode = True
import p_run as P  # noqa: E402

R, s5 = P.R, P.R.s5
D = os.path.join(P.CACHE, "cvtune")


def recs(provider, variant):
    return {r["key"]: r for r in P.read_recs(os.path.join(D, f"{provider}-{variant}.jsonl"))}


def v0_picks(provider):
    """-> {row: index into cands} from the hash-checked V0 file (forward order)."""
    if provider == "jev":
        text = R.verified_text("cvtune", "jev-sent-fwd.jsonl")
    else:
        path = P.CLEF_V0_PATH if provider == "clef" else P.CLEF27_V0_PATH
        (P.check_clef_v0 if provider == "clef" else P.check_clef27_v0)()
        with open(path, encoding="utf-8") as f:
            text = f.read()
    out = {}
    for l in text.split("\n"):
        if l.strip():
            r = json.loads(l)
            for key, n in zip(r["keys"], range(len(r["keys"]))):
                j = r["picks"][n] if provider != "jev" else int(r["answers"][n]["choice"][1:]) - 1
                if j is not None:
                    out[int(key)] = j
    return out


def correct(rows, ks, picks, L):
    """-> {row: 0/1}. A row without a pick (single candidate, V0 parse failure) keeps rank 1."""
    return {k: int(L(rows[k]["cands"][picks.get(k, 0)]) == L(rows[k]["truth"])) for k in ks}


def variant_picks(provider, variant, rows, ks):
    rs = recs(provider, variant)
    miss = [k for k in ks if len(rows[k]["cands"]) > 1 and k not in rs]
    if miss:
        R.die(f"{provider} {variant}: {len(miss)} rows without a result (run incomplete)")
    return {k: r["pick"] for k, r in rs.items() if r["pick"] is not None and k in ks}


def half(rows, h):
    return [k for k, r in enumerate(rows) if r["half"] == h]


def select(rows, L, provs=("jev", "clef")):
    ks, best = half(rows, "A"), {}
    for prov in provs:
        accs = {}
        for v in ("v1", "v2f", "v3", "v4f"):  # choice variants: forward only
            accs[v] = sum(correct(rows, ks, variant_picks(prov, v, rows, ks), L).values()) / len(ks)
        for v, a in accs.items():
            print(f"A-half {prov} {v} acc={a:.4f} n={len(ks)}")
        best[prov] = min(accs, key=lambda v: (-accs[v], v))[:2]  # ties: lower number
        print(f"selected {prov}: {best[prov]}")
    return best


def sig(r, s):
    return r["p"] < 0.05 and r["net"] * s > 0


def word(r, better, worse, same):
    return better if sig(r, 1) else worse if sig(r, -1) else same


def pct_cost(provider, rs, nrows):
    lat = [r["secs"] * 1000 for r in rs.values()]
    if provider in P.PRICE:
        usd = sum(r["tokens"] for r in rs.values()) * P.PRICE[provider]
        note = f"tokens x {P.PRICE[provider] * 1e6:.2f}/M" + (" (token counts estimated from characters)" if any(r["tok_est"] for r in rs.values()) else "")
    else:
        usd = sum(r["nq"] for r in rs.values()) * P.JEV_USD_PER_Q
        note = "ESTIMATE from S5j's J1 rate (price unverified)"
    return s5.pct(lat, .5), s5.pct(lat, .95), usd / max(1, nrows) * 1000, note


def test(rows, L, chosen):
    """The 5 tests of section 5 item 5, on the B half. chosen = {'jev': 'v2', 'clef': 'v3'}."""
    ks = half(rows, "B")
    ok, out = {}, {}
    for prov, base in chosen.items():
        v = base + ("f" if base in ("v2", "v4") else "")
        picks = variant_picks(prov, v, rows, ks)
        ok[prov] = correct(rows, ks, picks, L)
        v0 = correct(rows, ks, v0_picks(prov), L)
        top = correct(rows, ks, {}, L)
        rs = {k: r for k, r in recs(prov, v).items() if k in ks}
        ties = sum(bool(r["tie"]) for r in rs.values())
        flip = None
        if base in ("v2", "v4"):
            rv = recs(prov, base + "r")
            both = [k for k in rs if k in rv and rv[k]["pick"] is not None and rs[k]["pick"] is not None]
            flip = sum(rs[k]["pick"] != rv[k]["pick"] for k in both) / max(1, len(both))
        for name, ref, better, worse, same in (
                (f"{prov} {base} vs V0", v0, "prompt improvement holds", "rewrite worse than V0", "no significant difference"),
                (f"{prov} {base} vs rank1", top, "CANDIDATE for H/S6", "worse than rank 1", "not a candidate")):
            f, b, p, _ = s5.mcnemar([ref[k] for k in ks], [ok[prov][k] for k in ks])
            r = {"p": p, "net": f - b}
            print(f"{name}: n={len(ks)} fixed={f} broken={b} net={f - b} p={p:.4g} -> {word(r, better, worse, same)}")
        p50, p95, per1k, note = pct_cost(prov, rs, len(rs))
        print(f"{prov} {base} B-half: acc={sum(ok[prov].values()) / len(ks):.4f} ties={ties} flip={flip} "
              f"lat_ms_p50={p50} p95={p95} usd_per_1000_requests={per1k:.4f} ({note})")
    for prov, label in (("clef", "Clef"), ("clef27", "Clef 27B")):  # the 2 pre-named Jev-vs-Clef tests (section 13)
        if "jev" in ok and prov in ok:
            f, b, p, _ = s5.mcnemar([ok["jev"][k] for k in ks], [ok[prov][k] for k in ks])
            r = {"p": p, "net": f - b}
            print(f"{prov} vs jev: n={len(ks)} fixed={f} broken={b} net={f - b} p={p:.4g} -> "
                  f"{word(r, label + ' better than Jev', label + ' worse than Jev', 'no significant difference')}")
    if "clef" in ok and "clef27" in ok:  # report only, never a test: no " vs " in the line
        f, b, p, _ = s5.mcnemar([ok["clef"][k] for k in ks], [ok["clef27"][k] for k in ks])
        print(f"report-only flash->27B (not a test): n={len(ks)} fixed={f} broken={b} net={f - b} p={p:.4g}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("select", "test"))
    ap.add_argument("--jev", choices=("v1", "v2", "v3", "v4"))
    ap.add_argument("--clef", choices=("v1", "v2", "v3", "v4"))
    ap.add_argument("--clef27", choices=("v1", "v2", "v3", "v4"))
    ap.add_argument("--providers", default="jev,clef,clef27", help="select: which providers' A-half files to score")
    a = ap.parse_args(argv)
    rows, L = R.load_rows("cvtune"), s5.lenient_fn()
    if a.cmd == "select":
        select(rows, L, tuple(a.providers.split(",")))
    else:
        chosen = {p: v for p, v in (("jev", a.jev), ("clef", a.clef), ("clef27", a.clef27)) if v}
        if not chosen:
            R.die("give --jev and/or --clef")
        test(rows, L, chosen)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        sys.exit(f"s5p: unexpected {type(e).__name__}")
