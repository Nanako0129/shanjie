#!/usr/bin/env python3
"""R1e / R1f (README of this directory, sections "R1e" and "R1f"): the reading term inside the n-gram decoder only. Every setting is a plain
CLI run, so there is no offline recomputation: this tool checks each run's summary tag and row count, then judges the four shipping clauses
against the setting's shipping baseline. Counts and row numbers only. "Shipping" is the CURRENT one, read from its sources, nothing frozen
here: tags from tools/bench.py VERSIONS2["v0.6.1"] and data/*.sha256 (r1.ship_tag), flags from the same row (r1.ship_flags), the
colloquial cells from core/src/colloquial.rs (r1.COLLOQ_CELLS), the rows of each set from DIR/counts.json.

  r1e.py judge DIR [--sjtl data/lm/tinylm.sjtl] [--out OUT.json] [--arm r1f|r1g]
  r1e.py commands [--rows-dir ROWS] [--out-dir OUT] [--cli target/release/shanjie-eval] [--threads N] [--arm r1f|r1g]     (the 60 CLI commands, one per line)

`--arm r1g` (README section "R1g"): the same 60 runs, the R1f arm adds `--reading-exempt experiments/r1-reading-decode/std-exempt.tsv` and its tag
must end `+rd+rx:<first 8 hex of that file's SHA-256>` (the committed file's). Labels (`base.*`, `e.*`) are the same. R1f is the default.

DIR holds, for each (setting, set) of ROLES, `base.<setting>.<set>.r` / `.cli.txt` (the shipping run) and `e.<setting>.<set>.r` / `.cli.txt`
(the R1e / R1f run), and `counts.json`: {set name: row count} written by the run script from the rows files it ran (after its own hash
check), every set of ROLES present. Exit codes: 0 judged (pass or fail), 1 STOP.
"""
import argparse, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import r1 as R  # noqa: E402  (read_summary, ROW_COUNTS, ROLES, p4c, evalstats, Q)
import r1d as RD  # noqa: E402  (SETTINGS and ROLES with the two -off settings, CLAUSE3_PAIRS)

Q, p4c, evalstats = R.Q, R.p4c, R.evalstats
stop = R.stop
ROLES, SETTINGS = RD.ROLES, RD.SETTINGS


EXEMPT = "experiments/r1-reading-decode/std-exempt.tsv"   # the R1g exemption list, as the CLI is given it (relative to the repository root)


def exempt_sha8():
    """First 8 hex of the committed std-exempt.tsv's SHA-256 (the CLI's `+rx:` tag)."""
    return R.sha_file(os.path.join(HERE, "std-exempt.tsv"))[:8]


def check_tags(setting, base_tag, tag, sjtl_sha8=None, rx=None):
    """STOP unless the baseline tag is the setting's current shipping run (r1.ship_tag: tools/bench.py VERSIONS2[v0.6.1] + data/*.sha256) and
    the R1f tag is that tag with `+rd` last; chat with the rerank on drops `+rc` (the reading channel is replaced by the decoder term),
    everything else is identical, so every other tag part (+kn:, +tl:, +rl:, +acg+it, +tri:, +colloq:) is the baseline's. `rx` (R1g): the
    tag then ends `+rd+rx:<rx>`, rx being the first 8 hex of the exemption list's SHA-256."""
    RD.check_tag(base_tag, setting, sjtl_sha8, rd=False)
    chat_on = SETTINGS[setting][0] == "chat" and SETTINGS[setting][2]
    want = (base_tag.removesuffix("+rc") if chat_on else base_tag) + "+rd" + (f"+rx:{rx}" if rx else "")
    if tag != want:
        stop(f"{setting}: R1f tag {tag} is not the baseline tag {base_tag} with +rd{'+rx' if rx else ''} ({want})")


def judge_one(d, setting, name, counts, sjtl_sha8=None, rx=None):
    """Both rowstats files must be the same rows: same count and the same gold length per row (the rows-file hashes are checked by the run
    script, not here)."""
    role = ROLES[setting][name]
    paths = {k: os.path.join(d, f"{k}.{setting}.{name}") for k in ("base", "e")}
    tags, rows = {}, {}
    for k, p in paths.items():
        tags[k], n, top1 = R.read_summary(p + ".cli.txt")
        rows[k] = evalstats.read_rowstats(p + ".r")
        if not (n == len(rows[k]) == counts[name]) or sum(r[0] for r in rows[k]) != top1:
            stop(f"{setting} {name} {k}: {n} summary rows, {len(rows[k])} rowstats, expected {counts[name]} (or top1 mismatch)")
    check_tags(setting, tags["base"], tags["e"], sjtl_sha8, rx)
    if [r[2] for r in rows["base"]] != [r[2] for r in rows["e"]]:
        stop(f"{setting} {name}: baseline and R1f rowstats differ in the gold lengths (not the same rows)")
    base_ok, new_ok = [bool(r[0]) for r in rows["base"]], [bool(r[0]) for r in rows["e"]]
    s = p4c.stats(base_ok, new_ok)
    res = {"setting": setting, "name": name, "role": role, "n": len(base_ok), **p4c.jsonable(s),
           "top1_before": sum(base_ok), "top1_after": sum(new_ok),
           "base_ok": "".join(str(int(o)) for o in base_ok), "cell_ok": "".join(str(int(o)) for o in new_ok)}
    if role in ("1", "2a"):
        res["verdict"] = bool(Q.verdict(role, s))
        if role == "1":     # the registered clause 1 also passes on "breaks <= 26" (README 判讀); CLAUDE.local.md 出貨標準 has the ratio alone
            res["verdict_ratio_only"] = bool((s["p"] >= 0.05 or s["net"] >= 0) and p4c.ratio_ok(s, Q.RATIO))
    elif role == "2b":
        (lo, _), _ = evalstats.paired_bootstrap(rows["base"], rows["e"], level=0.90)
        res.update({"bootstrap90_lower_pp": 100 * lo / len(base_ok), "verdict": 100 * lo / len(base_ok) >= -1.0})
    return res


def judge(d, sjtl_sha8=None, rx=None):
    with open(os.path.join(d, "counts.json"), encoding="utf-8") as f:
        counts = json.load(f)
    missing = sorted({n for roles in ROLES.values() for n in roles} - set(counts))
    if missing:
        stop(f"counts.json has no row count for {missing}")
    runs = {(s, n): judge_one(d, s, n, counts, sjtl_sha8, rx) for s, roles in ROLES.items() for n in roles}
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


def cli_args(setting, arm):
    """The CLI options (after `--lm data/lm/bigram.sjlm --profile P`, before `--threads / --rows / --rowstats`) of one run of `setting`: arm
    "base" is the current shipping run, "r1f" the same plus the decoder's reading term, "r1g" the r1f run plus `--reading-exempt` (the std-exempt list). The shipping part is built from the sources: flags from
    tools/bench.py VERSIONS2["v0.6.1"] (`--context --packs acg,it`), `--tinylm` only with the rerank on, `--rerank-level` only off standard,
    `--colloquial-core` in chat with the rerank on (bench's cell table); r1f adds `--reading-decode`, and in chat with the rerank on
    `--no-reading-channel` (the colloquial term stays in the fusion, the reading-channel term goes)."""
    profile, level, on = SETTINGS[setting]
    a = R.ship_flags()
    if on:
        a += ["--tinylm", "data/lm/tinylm.sjtl"] + (["--rerank-level", level] if level != "standard" else [])
        a += ["--colloquial-core", "data/lm/colloquial.sjlm"] if profile == "chat" else []
    if arm in ("r1f", "r1g"):
        a += ["--reading-decode"] + (["--no-reading-channel"] if profile == "chat" and on else [])
    if arm == "r1g":
        a += ["--reading-exempt", EXEMPT]
    return a


def commands(rows_dir, out_dir, cli, threads, arm="r1f"):
    """-> [(file stem, shell command)] for every (setting, set, arm) of ROLES: stdout (the summary line) goes to <stem>.cli.txt, rowstats to <stem>.r."""
    import shlex
    q = lambda x: f'"{x}"' if "$" in x else shlex.quote(x)      # $ROWS / $OUT stay expandable
    out = []
    for setting, roles in ROLES.items():
        for name in roles:
            for a, k in (("base", "base"), (arm, "e")):
                stem = os.path.join(out_dir, f"{k}.{setting}.{name}")
                argv = [cli, "--lm", "data/lm/bigram.sjlm", "--profile", SETTINGS[setting][0], *cli_args(setting, a), "--threads", str(threads),
                        "--rows", os.path.join(rows_dir, f"{name}.txt"), "--rowstats", stem + ".r"]
                out.append((stem, " ".join(q(x) for x in argv) + f" > {q(stem + '.cli.txt')}"))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    j = sub.add_parser("judge")
    j.add_argument("dir")
    j.add_argument("--out")
    j.add_argument("--sjtl", default="data/lm/tinylm.sjtl")
    j.add_argument("--arm", choices=("r1f", "r1g"), default="r1f")
    c = sub.add_parser("commands")
    c.add_argument("--rows-dir", default="$ROWS")
    c.add_argument("--out-dir", default="$OUT")
    c.add_argument("--cli", default="target/release/shanjie-eval")
    c.add_argument("--threads", type=int, default=32)
    c.add_argument("--arm", choices=("r1f", "r1g"), default="r1f")
    a = ap.parse_args(argv)
    if a.cmd == "commands":
        for _, line in commands(a.rows_dir, a.out_dir, a.cli, a.threads, a.arm):
            print(line)
        return 0
    Q.check_out(a.out, None) if a.out else None
    if R.sha_file(a.sjtl)[:8] != R.model_sha8("tinylm.sjtl"):
        stop(f"{a.sjtl} is not data/tinylm.sjtl.sha256's small model")
    res = judge(a.dir, rx=exempt_sha8() if a.arm == "r1g" else None)
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
