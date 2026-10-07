"""S2f 契約 §3.4 守門：同一份列檔上，模型 E 與模型 F 的 `lm_eval.py --dump` 配對比較。
對錯照 lm_eval 的第一名寬鬆對照；McNemar 精確檢定（雙尾，experiments/s2h/report.py 的 mcnemar）。
用法：python3 experiments/s2f/guard.py <列檔|dev302> <E.dump> <F.dump>
印：n、E → F 答對數、修好、弄壞、p。淨值為負而且 p < 0.05 就是否決（exit 1）。
"""
import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "reference", "proto"))
sys.path.insert(0, os.path.join(ROOT, "experiments", "s2h"))
from eval import lenient  # noqa: E402
from lm_eval import rows_of  # noqa: E402
from report import mcnemar  # noqa: E402


def top1(path):
    return {int(i): s for i, r, s, _ in (l.rstrip("\n").split("\t") for l in open(path, encoding="utf-8")) if r == "1"}


def main():
    src, de, df = sys.argv[1:4]
    rows = rows_of(sorted(glob.glob(os.path.join(ROOT, "eval", "dev", "*.txt"))))[:302] if src == "dev302" else rows_of([src])
    e, f = top1(de), top1(df)
    assert len(e) == len(f) == len(rows), (len(e), len(f), len(rows))
    a = [int(lenient(e[i]) == lenient(t)) for i, (t, _, _) in enumerate(rows, 1)]
    b = [int(lenient(f[i]) == lenient(t)) for i, (t, _, _) in enumerate(rows, 1)]
    fixed, broken, p = mcnemar(a, b)
    veto = fixed < broken and p < 0.05
    print(f"n={len(rows)} {sum(a)} -> {sum(b)} fixed={fixed} broken={broken} p={p:.3g}{' VETO' if veto else ''}")
    sys.exit(1 if veto else 0)


if __name__ == "__main__":
    main()
