#!/usr/bin/env python3
"""The judge of README.md in this directory: T1 against T0, the R1e run (the CLI's `--reading-decode`) as the base and the same run plus
`--reading-table T1` as the candidate, with r1e.py's own code (tags, rows identity, the four shipping clauses, clause 3 pooling, top1 order).

  judge_t1.py judge DIR --table T1.tsv --t0 T0.tsv [--sjtl data/lm/tinylm.sjtl] [--out OUT.json]

DIR holds, for each (setting, set) of r1d.py's ROLES (8 settings, 30 pairs), `base.<setting>.<set>` and `t.<setting>.<set>` (the R1e run and the
candidate), each as `.r` (rowstats), `.cli.txt` (stdout), `.dump` (`--dump`) and, for the rerank-on settings, `.scores` (`--tinylm-scores`);
and, for every set of chat-std and formal-std, the anchor run `a.<setting>.<set>.{cli.txt,dump,scores}` (the base run plus `--reading-table T0`,
T0 exported by the same VM). STOP (exit 1) unless the candidate's tag is the base's tag + `+rt:<sha8 of --table>` and the base's tag is a real
R1e tag (r1e.py check_tags). Added to r1e's output, from the same files:
  - the pick of each row is the dump's `c`-th candidate with `--tinylm-scores` (rerank on) and rank 1 otherwise; a "changed row" is a row whose picked
    string differs between base and candidate (not a different ok or edit distance).
  - the human-reading guard: user-reported (first 154 rows) and typing76, each pooled over chat-std + formal-std: breaks <= GUARD_BREAKS. A missing
    run FAILS the guard. Other settings are judged by the four clauses only.
  - the anchor: each anchor run's tag is the base's tag + `+rt:<sha8 of --t0>`, its summary `top1_sha256` and every row's pick equal the base
    run's. Any difference or missing file fails the anchor.
  - the stop rule: changed rows in chat-std/chatclause and in formal-std/wikitune-clean both < MIN_CHANGED prints "表的形狀已經不是瓶頸" and the
    second step (corpus counts) is not done.
Exit codes: 0 judged and guard and anchor pass (the four clauses may fail), 1 STOP, guard failure or anchor failure. Counts only.
"""
import argparse, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "r1-reading-decode"))
import r1e as E  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "tools"))
import triage as T  # noqa: E402

R, RD, Q, p4c = E.R, E.RD, E.Q, E.p4c
GUARD_BREAKS = 2
MIN_CHANGED = 5
STOP_SETS = (("chat-std", "chatclause"), ("formal-std", "wikitune-clean"))
GUARD_POOL = ("chat-std", "formal-std")
GUARD_NAMES = ("user-reported", "typing76")
ANCHOR_SETTINGS = GUARD_POOL
SHAPE_TEXT = "表的形狀已經不是瓶頸"


def make_tag_check(rt8):
    def check(setting, base_tag, tag, sjtl_sha8):
        # the base is an R1e run (README of r1-reading-decode: chat keeps +colloq, drops +rc), so r1d's rd=True check (no +colloq) does not apply:
        # r1e.check_tags on the shipping tag it implies is the check
        chat_on = RD.SETTINGS[setting][0] == "chat" and RD.SETTINGS[setting][2]
        if not base_tag.endswith("+rd") or "+rc" in base_tag:
            E.stop(f"{setting}: the base tag {base_tag} is not an R1e tag (+rd last, no +rc)")
        E.check_tags(setting, base_tag.removesuffix("+rd") + ("+rc" if chat_on else ""), base_tag, sjtl_sha8)
        if tag != f"{base_tag}+rt:{rt8}":
            E.stop(f"{setting}: candidate tag {tag} is not the base tag {base_tag} with +rt:{rt8}")
    return check


def read_picks(prefix, setting, n):
    """-> the picked string of each of n rows from `prefix`.dump (and `prefix`.scores for a rerank-on setting: its `c`, the index in the dump's order
    of the row's candidates); rank 1 for a rerank-off setting. A row with no candidate picks ""."""
    cands = [[] for _ in range(n)]
    for l in T.lines_of(T.read_text(prefix + ".dump")):
        r, _, s, _ = l.split("\t")
        cands[int(r) - 1].append(s)
    if not RD.SETTINGS[setting][2]:
        return [c[0] if c else "" for c in cands]
    c = {}
    for l in T.lines_of(T.read_text(prefix + ".scores")):
        j = json.loads(l)
        c[j["i"]] = j["c"]
    if len(c) != n:
        E.stop(f"{prefix}.scores has {len(c)} rows, expected {n}")
    return [cs[c[i + 1]] if cs else "" for i, cs in enumerate(cands)]


def top1_sha(path):
    with open(path, encoding="utf-8") as f:
        m = re.search(r"'top1_sha256': '([^']*)'", f.read())
    return m.group(1) if m else None


def guard(runs):
    """-> {label: {"breaks": n, "pass": bool}}: user-reported and typing76 each pooled over GUARD_POOL; a missing run fails (no entry is skipped)."""
    out = {}
    for name in GUARD_NAMES:
        label = f"{name}: {' + '.join(GUARD_POOL)}"
        if not all((s, name) in runs for s in GUARD_POOL):
            out[label] = {"n": 0, "fixes": 0, "breaks": None, "pass": False, "missing": True}
            continue
        base = "".join(runs[s, name]["base_ok"] for s in GUARD_POOL)
        cell = "".join(runs[s, name]["cell_ok"] for s in GUARD_POOL)
        st = p4c.stats([c == "1" for c in base], [c == "1" for c in cell])
        out[label] = {"n": st["n"], "fixes": st["fixes"], "breaks": st["breaks"], "pass": st["breaks"] <= GUARD_BREAKS}
    return out


def anchor(d, t08, picks):
    """-> (failures, runs checked): the anchor runs (base + --reading-table T0) of ANCHOR_SETTINGS x every set against the base runs. `picks` is
    {(setting, name): base picks}."""
    fails, n = [], 0
    for s in ANCHOR_SETTINGS:
        for name in RD.ROLES[s]:
            n += 1
            p = os.path.join(d, f"a.{s}.{name}")
            try:
                tag, rows, _ = R.read_summary(p + ".cli.txt")
                base_tag, _, _ = R.read_summary(os.path.join(d, f"base.{s}.{name}.cli.txt"))
                ok = (tag == f"{base_tag}+rt:{t08}", top1_sha(p + ".cli.txt") == top1_sha(os.path.join(d, f"base.{s}.{name}.cli.txt")),
                      read_picks(p, s, rows) == picks[s, name])
            except (OSError, SystemExit) as e:
                fails.append(f"{s}/{name}: {type(e).__name__}")
                continue
            for what, good in zip(("tag", "top1_sha256", "picks"), ok):
                if not good:
                    fails.append(f"{s}/{name}: {what}")
    return fails, n


def stop_rule(runs):
    """-> ({set: changed rows}, whether every selection set changed fewer than MIN_CHANGED rows)."""
    ch = {f"{s}/{n}": runs[s, n]["changed"] for s, n in STOP_SETS if (s, n) in runs}
    return ch, len(ch) == len(STOP_SETS) and all(v < MIN_CHANGED for v in ch.values())


def judge(d, sjtl8, rt8, t08):
    res = E.judge(d, sjtl8, cand="t", tag_check=make_tag_check(rt8))
    runs = {tuple(k.split("/")): v for k, v in res["runs"].items()}
    base_picks = {}
    for (s, n), r in runs.items():
        base_picks[s, n] = read_picks(os.path.join(d, f"base.{s}.{n}"), s, r["n"])
        r["changed"] = sum(a != b for a, b in zip(base_picks[s, n], read_picks(os.path.join(d, f"t.{s}.{n}"), s, r["n"])))
    res["guard"] = guard(runs)
    res["guard_pass"] = all(v["pass"] for v in res["guard"].values())
    res["anchor_failures"], res["anchor_runs"] = anchor(d, t08, base_picks)
    res["anchor_pass"] = not res["anchor_failures"]
    res["changed_rows"], res["shape_not_the_bottleneck"] = stop_rule(runs)
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    j = sub.add_parser("judge")
    j.add_argument("dir")
    j.add_argument("--table", required=True)
    j.add_argument("--t0", required=True)
    j.add_argument("--out")
    j.add_argument("--sjtl", default="data/lm/tinylm.sjtl")
    a = ap.parse_args(argv)
    Q.check_out(a.out, None) if a.out else None
    res = judge(a.dir, R.sha_file(a.sjtl)[:8], R.sha_file(a.table)[:8], R.sha_file(a.t0)[:8])
    for k, r in sorted(res["runs"].items()):
        print(f"{k} n {r['n']} fixes {r['fixes']} breaks {r['breaks']} net {r['net']} changed {r['changed']} top1 {r['top1_before']} -> {r['top1_after']}"
              + (f" verdict {'PASS' if r['verdict'] else 'FAIL'}" if "verdict" in r else ""))
    for role, c in res["clauses"].items():
        for k, v in c.items():
            print(f"clause {role} {k} {'PASS' if v else 'FAIL'}")
    for k, v in res["guard"].items():
        print(f"guard {k}: " + ("MISSING RUN FAIL" if v.get("missing") else f"fixes {v['fixes']} breaks {v['breaks']} {'PASS' if v['pass'] else 'FAIL'}"))
    print("GUARD PASS" if res["guard_pass"] else f"GUARD FAIL (breaks > {GUARD_BREAKS} or a run missing): 停，逐列看是哪個次要讀音被扣")
    print(f"ANCHOR PASS ({res['anchor_runs']} runs)" if res["anchor_pass"] else "ANCHOR FAIL, 停: " + "; ".join(res["anchor_failures"]))
    print("ALL FOUR CLAUSES PASS" if res["all_pass"] else "NOT ALL CLAUSES PASS: 照登記不過")
    print("changed rows " + " ".join(f"{k} {v}" for k, v in res["changed_rows"].items()))
    if res["shape_not_the_bottleneck"]:
        print(SHAPE_TEXT)
    if a.out:
        Q.write_atomic(a.out, json.dumps(res, indent=1))
    return 0 if res["guard_pass"] and res["anchor_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
