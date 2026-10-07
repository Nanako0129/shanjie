"""未調參的公開結果：dev302、打字測驗、user-reported，配對比較，另列修好／弄壞的例句與成本。
用法：python3 report.py --K 256 --mu 0.9 [--examples 8]（句子內容只出現在公開集合）"""
import argparse
import os
import random
import time
import classlm as C

ROOT = C.ROOT
TUNE = os.environ.get("S2K_TUNE") or os.path.expanduser("~/.cache/shanjie/work/s2/tune")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--K", type=int, default=256); ap.add_argument("--mu", type=float, required=True)
    ap.add_argument("--N", type=int, default=40000); ap.add_argument("--examples", type=int, default=8)
    a = ap.parse_args()
    base, cl = C.ClassLM(C.LM_PATH), C.ClassLM(C.LM_PATH, a.N, a.K, a.mu)
    lex = C.make_lex(base)
    sets = {"dev302": C.dev302(), "typing76": C.rows_of([os.path.join(ROOT, "eval/dev/user-typing.txt")]),
            "reported17": C.rows_of([os.path.join(ROOT, "eval/dev/user-reported.txt")])}
    for n in ("cvtune", "wikitune"):
        sets[n] = C.rows_of([os.path.join(TUNE, n + ".txt")])
    for n, rows in sets.items():
        for prof in ("chat", "formal"):
            t = time.time(); bt = C.run(lex, base, rows, prof); tb = time.time() - t
            t = time.time(); ct = C.run(lex, cl, rows, prof); tc = time.time() - t
            bo, co = C.ok_of(rows, bt), C.ok_of(rows, ct)
            f, b, net, p = C.paired(bo, co)
            print(f"{n:11s}{prof:7s}n={len(rows)} base={sum(bo)} class={sum(co)} fixed={f} broke={b} net={net:+d} p={p:.3g}  decode_s base={tb:.1f} class={tc:.1f}")
            if n in ("dev302", "typing76", "reported17", "cvtune", "wikitune") and prof == "chat" or n == "dev302":
                fx = [(r[0], x, y) for r, x, y, p_, q_ in zip(rows, bt, ct, bo, co) if not p_ and q_]
                bk = [(r[0], x, y) for r, x, y, p_, q_ in zip(rows, bt, ct, bo, co) if p_ and not q_]
                if n in ("cvtune", "wikitune", "dev302"):
                    random.Random(1).shuffle(fx); random.Random(2).shuffle(bk)
                for tag, lst in (("fixed", fx), ("broke", bk)):
                    for truth, x, y in lst[:a.examples]:
                        print(f"   [{n}/{prof}] {tag}: {truth}  base={x}  class={y}")


if __name__ == "__main__":
    main()
