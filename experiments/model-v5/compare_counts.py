"""model-v5 契約 §2.1：維基計數的內容比對（imap_unordered 讓 pkl 位元組無法重現，所以比內容）。
鍵集合（uni、bi，以及有 tri 時的 tri）、runs、articles 要相同；每個值的相對誤差 <= --tol（預設 1e-9）。
用法：python3 compare_counts.py A.pkl B.pkl [--tol 1e-9]；相同印 OK、結束碼 0，否則印第一批差異、結束碼 1。
"""
import argparse
import pickle
import sys


def compare(a, b, tol=1e-9):
    """回傳差異說明的 list；空 list = 相同。"""
    bad = []
    for k in ("runs", "articles"):
        if a.get(k) != b.get(k):
            bad.append(f"{k}: {a.get(k)} != {b.get(k)}")
    for name in sorted((set(a) | set(b)) & {"uni", "bi", "tri"}):
        x, y = a.get(name), b.get(name)
        if x is None or y is None:
            bad.append(f"{name}: only in one file"); continue
        if set(x) != set(y):
            only = sorted(set(x) ^ set(y), key=str)[:5]
            bad.append(f"{name}: key sets differ ({len(set(x) ^ set(y))} keys, e.g. {only})"); continue
        worst = max((abs(x[k] - y[k]) / max(abs(x[k]), abs(y[k])) for k in x if x[k] != y[k]), default=0.0)
        if worst > tol:
            bad.append(f"{name}: max relative error {worst:.3g} > {tol}")
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("a"); ap.add_argument("b")
    ap.add_argument("--tol", type=float, default=1e-9)
    n = ap.parse_args(argv)
    with open(n.a, "rb") as f, open(n.b, "rb") as g:
        bad = compare(pickle.load(f), pickle.load(g), n.tol)
    print("\n".join(bad) if bad else "OK")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
