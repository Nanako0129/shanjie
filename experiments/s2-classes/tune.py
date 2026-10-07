"""在 cvtune、wikitune 上掃 K 與 μ，和已出貨的 bigram 做配對比較。用法：python3 tune.py --Ks 256,512 --mus 0.1,0.2,0.3,0.5 [--N 40000]"""
import argparse
import os
import time
import classlm as C

TUNE = os.environ.get("S2K_TUNE") or os.path.expanduser("~/.cache/shanjie/work/s2/tune")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--Ks", default="256,512"); ap.add_argument("--mus", default="0.1,0.2,0.3,0.5")
    ap.add_argument("--N", type=int, default=40000)
    a = ap.parse_args()
    lm_path = C.LM_PATH
    base = C.ClassLM(lm_path); lex = C.make_lex(base)
    sets = {n: C.rows_of([os.path.join(TUNE, n + ".txt")]) for n in ("cvtune", "wikitune")}
    bo = {(n, p): C.ok_of(r, C.run(lex, base, r, p)) for n, r in sets.items() for p in ("chat", "formal")}
    for k, v in bo.items():
        print("base", k, sum(v), "/", len(v), flush=True)
    print("K\tmu\tset\tprofile\ttop1\tfixed\tbroke\tnet\tp\tsec", flush=True)
    for K in map(int, a.Ks.split(",")):
        for mu in map(float, a.mus.split(",")):
            cl = C.ClassLM(lm_path, a.N, K, mu)
            for n, r in sets.items():
                for p in ("chat", "formal"):
                    t = time.time(); ok = C.ok_of(r, C.run(lex, cl, r, p))
                    f, b, net, pv = C.paired(bo[(n, p)], ok)
                    print(f"{K}\t{mu}\t{n}\t{p}\t{sum(ok)}\t{f}\t{b}\t{net:+d}\t{pv:.3g}\t{time.time() - t:.0f}", flush=True)


if __name__ == "__main__":
    main()
