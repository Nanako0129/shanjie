#!/usr/bin/env python3
"""R1e (README of this directory, section "R1e"): the reading term inside the n-gram decoder only. Every setting is a plain CLI run, so there
is no offline recomputation: this tool checks each run's summary tag and row count, then judges the four shipping clauses against the
setting's shipping baseline. Counts and row numbers only.

  r1e.py judge DIR [--sjtl data/lm/tinylm.sjtl] [--out OUT.json]

DIR holds, for each (setting, set) of ROLES, `base.<setting>.<set>.r` / `.cli.txt` (the shipping run) and `e.<setting>.<set>.r` / `.cli.txt`
(the R1e run). Exit codes: 0 judged (pass or fail), 1 STOP.
"""
import argparse, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import r1 as R  # noqa: E402  (read_summary, ROW_COUNTS, ROLES, p4c, evalstats, Q)
import r1d as RD  # noqa: E402  (SETTINGS and ROLES with the two -off settings, CLAUSE3_PAIRS)

Q, p4c, evalstats = R.Q, R.p4c, R.evalstats
stop = R.stop
ROLES, SETTINGS = RD.ROLES, RD.SETTINGS


def check_tags(setting, base_tag, tag, sjtl_sha8):
    """STOP unless the baseline tag is the setting's shipping run (r1d's own check) and the R1e tag is that tag with `+rd` last; chat with the
    rerank on drops `+rc` (the reading channel is replaced by the decoder term), everything else is identical, so every other tag
    (+tl:, +rl:, +kn:, +colloq:, +tri:, +acg, +ctx, the demote / class / name switches) is the baseline's."""
    RD.check_tag(base_tag, setting, sjtl_sha8, rd=False)
    chat_on = SETTINGS[setting][0] == "chat" and SETTINGS[setting][2]
    want = (base_tag.removesuffix("+rc") if chat_on else base_tag) + "+rd"
    if tag != want:
        stop(f"{setting}: R1e tag {tag} is not the baseline tag {base_tag} with +rd ({want})")


def judge_one(d, setting, name, sjtl_sha8, cand="e", tag_check=None):
    """Both rowstats files must be the same rows: same count and the same gold length per row (the rows-file hashes are checked by the run
    script, not here). `cand` is the candidate run's file prefix and `tag_check(setting, base_tag, cand_tag, sjtl_sha8)` its tag test (default:
    this tool's; experiments/reading-counts-b/judge_t1.py judges another candidate against the R1e run with the same code)."""
    role = ROLES[setting][name]
    paths = {k: os.path.join(d, f"{k}.{setting}.{name}") for k in ("base", cand)}
    tags, rows = {}, {}
    for k, p in paths.items():
        tags[k], n, top1 = R.read_summary(p + ".cli.txt")
        rows[k] = evalstats.read_rowstats(p + ".r")
        if not (n == len(rows[k]) == R.ROW_COUNTS[name]) or sum(r[0] for r in rows[k]) != top1:
            stop(f"{setting} {name} {k}: {n} summary rows, {len(rows[k])} rowstats, expected {R.ROW_COUNTS[name]} (or top1 mismatch)")
    (tag_check or check_tags)(setting, tags["base"], tags[cand], sjtl_sha8)
    if [r[2] for r in rows["base"]] != [r[2] for r in rows[cand]]:
        stop(f"{setting} {name}: baseline and {'R1e' if cand == 'e' else 'candidate'} rowstats differ in the gold lengths (not the same rows)")
    base_ok, new_ok = [bool(r[0]) for r in rows["base"]], [bool(r[0]) for r in rows[cand]]
    s = p4c.stats(base_ok, new_ok)
    res = {"setting": setting, "name": name, "role": role, "n": len(base_ok), **p4c.jsonable(s),
           "top1_before": sum(base_ok), "top1_after": sum(new_ok),
           "changed": sum(a[:2] != b[:2] for a, b in zip(rows["base"], rows[cand])),
           "base_ok": "".join(str(int(o)) for o in base_ok), "cell_ok": "".join(str(int(o)) for o in new_ok)}
    if role in ("1", "2a"):
        res["verdict"] = bool(Q.verdict(role, s))
    elif role == "2b":
        (lo, _), _ = evalstats.paired_bootstrap(rows["base"], rows[cand], level=0.90)
        res.update({"bootstrap90_lower_pp": 100 * lo / len(base_ok), "verdict": 100 * lo / len(base_ok) >= -1.0})
    return res


def judge(d, sjtl_sha8, **kw):
    runs = {(s, n): judge_one(d, s, n, sjtl_sha8, **kw) for s, roles in ROLES.items() for n in roles}
    out = {"clauses": {"1": {}, "2a": {}, "2b": {}, "3": {}}, "runs": {f"{s}/{n}": r for (s, n), r in runs.items()}}
    for (s, n), r in sorted(runs.items()):
        if "verdict" in r:
            out["clauses"][r["role"]][f"{s}/{n}"] = r["verdict"]
    for pair in RD.CLAUSE3_PAIRS:
        base = "".join(runs[s, "user-reported"]["base_ok"] for s in pair)
        cell = "".join(runs[s, "user-reported"]["cell_ok"] for s in pair)
        st = p4c.stats([c == "1" for c in base], [c == "1" for c in cell])
        out["clauses"]["3"][" + ".join(pair)] = bool(Q.verdict("3", st))
        out.setdefault("clause3_stats", {})[" + ".join(pair)] = p4c.jsonable({k: st[k] for k in ("n", "fixes", "breaks", "net", "p", "error_ratio")})
    out["all_pass"] = all(v for c in out["clauses"].values() for v in c.values())
    out["level_order"] = {f"{p}/{n}": {"before": [runs[f"{p}-{l}", n]["top1_before"] for l in ("cons", "std", "aggr")],
                                       "after": [runs[f"{p}-{l}", n]["top1_after"] for l in ("cons", "std", "aggr")]}
                          for p, n in (("chat", "discordtune"), ("chat", "typing76"), ("formal", "typing76"), ("formal", "wikitune-clean"))
                          if all((f"{p}-{l}", n) in runs for l in ("cons", "std", "aggr"))}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    j = sub.add_parser("judge")
    j.add_argument("dir")
    j.add_argument("--out")
    j.add_argument("--sjtl", default="data/lm/tinylm.sjtl")
    a = ap.parse_args(argv)
    Q.check_out(a.out, None) if a.out else None
    res = judge(a.dir, R.sha_file(a.sjtl)[:8])
    for k, r in sorted(res["runs"].items()):
        print(f"{k} n {r['n']} fixes {r['fixes']} breaks {r['breaks']} net {r['net']} top1 {r['top1_before']} -> {r['top1_after']}"
              + (f" verdict {'PASS' if r['verdict'] else 'FAIL'}" if "verdict" in r else ""))
    for k, st in res["clause3_stats"].items():
        print(f"clause 3 {k}: fixes {st['fixes']} breaks {st['breaks']} net {st['net']}")
    for role, c in res["clauses"].items():
        for k, v in c.items():
            print(f"clause {role} {k} {'PASS' if v else 'FAIL'}")
    for k, v in res["level_order"].items():
        print(f"top1 cons/std/aggr {k}: before {v['before']} after {v['after']}")
    print("ALL FOUR CLAUSES PASS" if res["all_pass"] else "NOT ALL CLAUSES PASS: 照登記不過")
    if a.out:
        Q.write_atomic(a.out, json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
