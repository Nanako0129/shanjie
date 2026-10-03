"""S2 實驗：把維基 bigram 接進原型解碼（learner 掛勾），量 top-1 與 oracle@64。不讀保留集。

加分 = λ · log10( P_bigram(w | v) / P_uni(w) )，P_bigram 是 absolute discounting 插值（D=0.75），
語料沒見過的詞不加分（沿用詞庫 unigram）。
用法：python3 experiments/s2/eval_bigram.py <counts.pkl> [λ ...]
"""
import glob
import math
import os
import pickle
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "reference", "proto"))
import ime  # noqa: E402

LENIENT = str.maketrans("她妳它牠嘗周臺裏", "他你他他嚐週台裡")
D = 0.75


class Bigram(ime.NoLearning):
    def __init__(self, counts, lam):
        self.uni, self.bi, self.lam = counts["uni"], counts["bi"], lam
        self.N = sum(self.uni.values())
        self.ctx = {}       # v -> (c(v·), N1+(v·))
        for (v, w), c in self.bi.items():
            t, k = self.ctx.get(v, (0, 0))
            self.ctx[v] = (t + c, k + 1)
        self.cache = {}

    def bonus(self, prev, syls, word):
        cu = self.uni.get(word, 0)
        if not cu or self.lam == 0:
            return 0.0
        k = (prev, word)
        if k not in self.cache:
            pu = cu / self.N
            t, n1 = self.ctx.get(prev, (0, 0))
            p = (max(self.bi.get(k, 0) - D, 0) / t + D * n1 / t * pu) if t else pu
            self.cache[k] = math.log10(p / pu)
        return self.lam * self.cache[k]


def rows_of(paths, limit=None):
    out = []
    for f in paths:
        for line in open(f, encoding="utf-8"):
            p = line.rstrip("\n").split("|")
            if len(p) == 3:
                out.append((os.path.basename(f), p[1], p[2].split()))
    return out[:limit] if limit else out


def main():
    counts = pickle.load(open(sys.argv[1], "rb"))
    lams = [float(x) for x in sys.argv[2:]] or [0.0, 0.5, 1.0, 1.5]
    lex = ime.Lexicon(os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt"),
                      overlay=os.path.join(ROOT, "data", "lexicon", "overlay-add.tsv"))
    dev = sorted(glob.glob(os.path.join(ROOT, "eval", "dev", "*.txt")))
    sets = {
        "dev302": rows_of([f for f in dev if not f.endswith(("user-typing.txt",))], 302),
        "typing76": rows_of([os.path.join(ROOT, "eval", "dev", "user-typing.txt")]),
        "reported": rows_of([os.path.join(ROOT, "eval", "dev", "user-reported.txt")]),
    }
    print(f"corpus: {counts['articles']} articles, {sum(counts['uni'].values())} tokens")
    for lam in lams:
        lm = Bigram(counts, lam)
        line = [f"λ={lam}"]
        for name, rows in sets.items():
            top = len_ok = o64 = 0
            for _, truth, syls in rows:
                nb = ime.decode(lex, syls, learner=lm, beam=64)
                out = "".join(nb[0][1])
                top += out == truth
                len_ok += out.translate(LENIENT) == truth.translate(LENIENT)
                o64 += truth in {"".join(w) for _, w in nb}
            line.append(f"{name} top1 {top}/{len(rows)} lenient {len_ok} @64 {o64}")
        print(" | ".join(line), flush=True)


if __name__ == "__main__":
    main()
