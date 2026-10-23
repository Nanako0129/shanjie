#!/usr/bin/env python3
"""The judge of README.md in this directory: T1 against T0, the R1e run (the CLI's `--reading-decode`) as the base and the same run plus
`--reading-table T1` as the candidate, with r1e.py's own code (tags, rows identity, the four shipping clauses, clause 3 pooling, top1 order).

  judge_t1.py judge DIR --table T1.tsv [--sjtl data/lm/tinylm.sjtl] [--out OUT.json]

DIR holds, for each (setting, set) of r1d.py's ROLES (8 settings, 30 pairs), `base.<setting>.<set>.r` / `.cli.txt` (the R1e run) and
`t.<setting>.<set>.r` / `.cli.txt` (the candidate). STOP (exit 1) unless the candidate's tag is the base's tag + `+rt:<sha8 of --table>` and the
base's tag is a real R1e tag (r1e.py check_tags). Added to r1e's output, from the same files:
  - the human-reading guard: user-reported (the first 154 rows, chat-std + formal-std pooled and chat-off + formal-off pooled) and typing76
    (every setting's run): breaks <= GUARD_BREAKS each. A failure stops the step (README: 超過就停, look row by row at which secondary reading).
  - the stop rule: rows whose (ok, errors) changed, in chat-std/chatclause and in formal-std/wikitune-clean (the selection sets; the rowstats
    keep no pick text, so a wrong pick that changed to another wrong pick with the same edit distance is not seen); both < MIN_CHANGED prints
    "表的形狀已經不是瓶頸" and the second step (corpus counts) is not done.
Exit codes: 0 judged (pass or fail), 1 STOP. Counts only.
"""
import argparse, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "r1-reading-decode"))
import r1e as E  # noqa: E402

R, RD, Q, p4c = E.R, E.RD, E.Q, E.p4c
GUARD_BREAKS = 2
MIN_CHANGED = 5
STOP_SETS = (("chat-std", "chatclause"), ("formal-std", "wikitune-clean"))
GUARD_SETS = {"user-reported": RD.CLAUSE3_PAIRS, "typing76": tuple((s,) for s in RD.ROLES if "typing76" in RD.ROLES[s])}
SHAPE_TEXT = "表的形狀已經不是瓶頸"


def make_tag_check(rt8):
    def check(setting, base_tag, tag, sjtl_sha8):
        chat_on = RD.SETTINGS[setting][0] == "chat" and RD.SETTINGS[setting][2]
        if not base_tag.endswith("+rd") or "+rc" in base_tag:
            E.stop(f"{setting}: the base tag {base_tag} is not an R1e tag (+rd last, no +rc)")
        # the base is an R1e run: its tag is the shipping tag of the setting with +rd (chat with the reading channel on drops +rc)
        E.check_tags(setting, base_tag.removesuffix("+rd") + ("+rc" if chat_on else ""), base_tag, sjtl_sha8)
        if tag != f"{base_tag}+rt:{rt8}":
            E.stop(f"{setting}: candidate tag {tag} is not the base tag {base_tag} with +rt:{rt8}")
    return check


def guard(runs):
    """-> {label: {"breaks": n, "pass": bool}} of the human-reading sets."""
    out = {}
    for name, groups in GUARD_SETS.items():
        for g in groups:
            if not all((s, name) in runs for s in g):
                continue
            base = "".join(runs[s, name]["base_ok"] for s in g)
            cell = "".join(runs[s, name]["cell_ok"] for s in g)
            st = p4c.stats([c == "1" for c in base], [c == "1" for c in cell])
            out[f"{name}: {' + '.join(g)}"] = {"n": st["n"], "fixes": st["fixes"], "breaks": st["breaks"], "pass": st["breaks"] <= GUARD_BREAKS}
    return out


def stop_rule(runs):
    """-> ({set: changed rows}, whether every selection set changed fewer than MIN_CHANGED rows)."""
    ch = {f"{s}/{n}": runs[s, n]["changed"] for s, n in STOP_SETS if (s, n) in runs}
    return ch, len(ch) == len(STOP_SETS) and all(v < MIN_CHANGED for v in ch.values())


def judge(d, sjtl8, rt8):
    res = E.judge(d, sjtl8, cand="t", tag_check=make_tag_check(rt8))
    runs = {tuple(k.split("/")): v for k, v in res["runs"].items()}
    res["guard"] = guard(runs)
    res["guard_pass"] = all(v["pass"] for v in res["guard"].values())
    res["changed_rows"], res["shape_not_the_bottleneck"] = stop_rule(runs)
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    j = sub.add_parser("judge")
    j.add_argument("dir")
    j.add_argument("--table", required=True)
    j.add_argument("--out")
    j.add_argument("--sjtl", default="data/lm/tinylm.sjtl")
    a = ap.parse_args(argv)
    Q.check_out(a.out, None) if a.out else None
    res = judge(a.dir, R.sha_file(a.sjtl)[:8], R.sha_file(a.table)[:8])
    for k, r in sorted(res["runs"].items()):
        print(f"{k} n {r['n']} fixes {r['fixes']} breaks {r['breaks']} net {r['net']} changed {r['changed']} top1 {r['top1_before']} -> {r['top1_after']}"
              + (f" verdict {'PASS' if r['verdict'] else 'FAIL'}" if "verdict" in r else ""))
    for role, c in res["clauses"].items():
        for k, v in c.items():
            print(f"clause {role} {k} {'PASS' if v else 'FAIL'}")
    for k, v in res["guard"].items():
        print(f"guard {k}: fixes {v['fixes']} breaks {v['breaks']} {'PASS' if v['pass'] else 'FAIL'}")
    print("ALL FOUR CLAUSES PASS" if res["all_pass"] else "NOT ALL CLAUSES PASS: 照登記不過")
    print("GUARD PASS" if res["guard_pass"] else f"GUARD FAIL (breaks > {GUARD_BREAKS}): 停，逐列看是哪個次要讀音被扣")
    print("changed rows " + " ".join(f"{k} {v}" for k, v in res["changed_rows"].items()))
    if res["shape_not_the_bottleneck"]:
        print(SHAPE_TEXT)
    if a.out:
        Q.write_atomic(a.out, json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
