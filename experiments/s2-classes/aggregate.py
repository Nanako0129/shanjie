"""只印彙總數字，不印任何句子內容，可安全用在私有評測檔。
用法：python3 experiments/s2-classes/aggregate.py --rows <前文|句子|讀音 檔> --K 256 --mu 0.3 [--N 40000] [--profile chat|formal|both]
輸出每個 profile 一行：n、基準 top1、設定 top1、修好／弄壞／淨／McNemar p。
"""
import argparse
import os
import classlm as C


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True); ap.add_argument("--K", type=int, default=256)
    ap.add_argument("--N", type=int, default=40000); ap.add_argument("--mu", type=float, required=True)
    ap.add_argument("--profile", default="both")
    a = ap.parse_args()
    rows = C.rows_of([a.rows])
    lm_path = C.LM_PATH
    base, cl = C.ClassLM(lm_path), C.ClassLM(lm_path, a.N, a.K, a.mu)
    lex = C.make_lex(base)
    for prof in (["chat", "formal"] if a.profile == "both" else [a.profile]):
        bo = C.ok_of(rows, C.run(lex, base, rows, prof)); co = C.ok_of(rows, C.run(lex, cl, rows, prof))
        f, b, net, p = C.paired(bo, co)
        print(f"{prof} n={len(rows)} base_top1={sum(bo)} class_top1={sum(co)} fixed={f} broke={b} net={net:+d} p={p:.3g} (K={a.K} N={a.N} mu={a.mu})")


if __name__ == "__main__":
    main()
