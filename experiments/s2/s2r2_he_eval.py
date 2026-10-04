"""S2r-2 §4.7：含疊加層詞「和」的列（原讀音）A（main）vs B（本分支），只報數字。私有集合的逐列檔照 s2r_eval 寫到 shanjie-private。

用法：python3 experiments/s2/s2r2_he_eval.py <cliA> <cliB> [--sets public,cvtune,wikitune,discordtune]
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from s2r_eval import ROOT, mcnemar, pairs, run_cli  # noqa: E402

OVERLAY = os.path.join(ROOT, "data", "lexicon", "overlay-add.tsv")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cli", nargs=2)
    ap.add_argument("--sets", default="public,cvtune,wikitune")
    a = ap.parse_args()
    he = {l.split("\t")[1] for l in open(OVERLAY, encoding="utf-8") if "和" in l.split("\t")[1]}
    for name in a.sets.split(","):
        ps, private = pairs(name, every=True)
        ps = [(s, o) for s, o, _ in ps if any(s[i:j] in he for i in range(len(s)) for j in range(i + 2, min(len(s), i + 4) + 1))]
        x = run_cli(a.cli[0], ps, private, f"{name}-he-A") if ps else []
        y = run_cli(a.cli[1], ps, private, f"{name}-he-B") if ps else []
        f, b, p = mcnemar(x, y)
        print(f"{name}: rows with an overlay 和 word n={len(ps)}: A {sum(x)} -> B {sum(y)} fixed={f} broken={b} p={p:.3g}")


if __name__ == "__main__":
    main()
