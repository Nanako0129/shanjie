"""E2 selection and judgement glue (contract section 3 E2). Reads only score files (numbers), never decodes; runs on the machine that holds
the private score files (chatclause, discordtune), or on the Mac for the selection only (it reads score files, no decoding).

  python judge_e2.py select  --scores DIR [DIR ...] --model E2C --out OUT_C       # one judge.py call per setting, STD, values only recorded
  python judge_e2.py select  --scores DIR [DIR ...] --model E2N --out OUT_N
  python judge_e2.py compare --base OUT_C --cand OUT_N --work TMP                   # the judgement tables, new arm vs control arm

select = exactly the two judge.py invocations of the contract (each setting once, selection frozen by that call):
  chat:   judge.py --scores DIR.. --sets <nine sets> --setting chat   --model M --out OUT --select STD --tuning chatclause --weight 1 --cap 0.005
  formal: judge.py --scores DIR.. --sets <seven sets> --setting formal --model M --out OUT --select STD --tuning cvtune wikitune --weight 10
The selected (alpha, tau, delta) is printed and only recorded: the shipped values are not checked (rerank-strength 11.2).

compare writes, per judgement, the concatenation of the arms' STD rowstats (`STD.<setting>.<set>.new.txt` = the picks of the selected point)
in ONE fixed order, the same for both arms, then prints `tools/evalstats.py compare --net`:
  MAIN   chat   discordtune + the five judge sets  (dev302 typing76 user-reported lytune wikitypo, then discordtune); pass: significantly positive, a = 0.05
  GUARD  formal the five judge sets, concatenated;                                          pass: not significantly negative
  GUARD  lytune chat and formal, each alone;                                                pass: not significantly negative
  report chat five sets without discordtune, each set alone, and the selection sets (chatclause cvtune wikitune): reported, not judged.
"significantly positive": fixed > broken and exact McNemar p < a; "significantly negative": broken > fixed and p < 0.05 (contract section 2).
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tinylm_r  # noqa: E402,F401  (paths)
sys.path.insert(0, os.path.join(tinylm_r.ROOT, "experiments", "e2-chateval"))
import judge  # noqa: E402  experiments/e2-chateval/judge.py (not edited)
import evalstats  # noqa: E402

FIVE = ["dev302", "typing76", "user-reported", "lytune", "wikitypo"]      # contract section 2 order
CHAT_SETS = ["chatclause", "cvtune", "wikitune"] + FIVE + ["discordtune"]
FORMAL_SETS = ["cvtune", "wikitune"] + FIVE
LABEL = "STD"
CHAT_ARGS = ["--select", LABEL, "--tuning", "chatclause", "--weight", "1", "--cap", "0.005"]
FORMAL_ARGS = ["--select", LABEL, "--tuning", "cvtune", "wikitune", "--weight", "10"]


def judge_argv(setting, scores, model, out):
    sets, extra = (CHAT_SETS, CHAT_ARGS) if setting == "chat" else (FORMAL_SETS, FORMAL_ARGS)
    return ["--scores", *scores, "--sets", *sets, "--setting", setting, "--model", model, "--out", out, *extra]


def select(scores, model, out):
    return {s: judge.main(judge_argv(s, scores, model, out)) for s in ("chat", "formal")}


def rowstats_path(out, setting, name):
    return os.path.join(out, "rowstats", f"{LABEL}.{setting}.{name}.new.txt")


def concat_rowstats(paths, dst):
    """The rowstats files in `paths` order as one file, rows renumbered from 1 (evalstats wants line i = row i). Returns the row count."""
    n = 0
    with open(dst, "w", newline="") as f:
        for p in paths:
            for line in open(p, encoding="utf-8"):
                c = line.rstrip("\n").split("\t")
                n += 1
                f.write("\t".join([str(n)] + c[1:]) + "\n")
    return n


def table(base_out, cand_out, setting, names, label, work):
    """One evalstats --net table row for the concatenation of `names` (in that order, the same for both arms)."""
    paths, stem = {}, re.sub(r"\W+", "_", label)
    for side, out in (("base", base_out), ("cand", cand_out)):
        paths[side] = os.path.join(work, f"{stem}.{side}.txt")
        concat_rowstats([rowstats_path(out, setting, n) for n in names], paths[side])
    return evalstats.compare(paths["base"], paths["cand"], label, net=True)


def compare(base_out, cand_out, work):
    os.makedirs(work, exist_ok=True)
    plan = [("MAIN chat discordtune+five (a=0.05, significantly positive)", "chat", FIVE + ["discordtune"]),
            ("GUARD formal five (not significantly negative)", "formal", FIVE),
            ("GUARD lytune chat (not significantly negative)", "chat", ["lytune"]),
            ("GUARD lytune formal (not significantly negative)", "formal", ["lytune"]),
            ("report chat five", "chat", FIVE)]
    plan += [(f"report {s} {n}", s, [n]) for s, names in (("chat", CHAT_SETS), ("formal", FORMAL_SETS)) for n in names]
    for k, (label, setting, names) in enumerate(plan):
        t = table(base_out, cand_out, setting, names, label, work).split("\n")
        print("\n".join(t if k == 0 else t[2:]))


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("select")
    s.add_argument("--scores", nargs="+", required=True); s.add_argument("--model", required=True); s.add_argument("--out", required=True)
    c = sub.add_parser("compare")
    c.add_argument("--base", required=True, help="judge output dir of the BASE (control arm)")
    c.add_argument("--cand", required=True, help="judge output dir of the CAND (new arm)")
    c.add_argument("--work", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "select":
        select(a.scores, a.model, a.out)
    else:
        compare(a.base, a.cand, a.work)


if __name__ == "__main__":
    main()
