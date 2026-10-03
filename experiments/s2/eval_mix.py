"""S2 實驗：維基 bigram 混口語 bigram（口語次數乘 k），在 dev302、打字測驗、Discord（本機私有）量 top-1。

用法：python3 experiments/s2/eval_mix.py <wiki.pkl> <colloquial.pkl> <λ> <k>...
Discord 列只在本機檔案存在時才量，只印數字。
"""
import collections
import glob
import os
import pickle
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eval_bigram import ROOT, Bigram, ime, rows_of  # noqa: E402

LENIENT = str.maketrans("她妳它牠嘗周臺裏", "他你他他嚐週台裡")
PRIVATE = os.path.expanduser("~/side-project/shanjie-private")


def main():
    wiki, coll = pickle.load(open(sys.argv[1], "rb")), pickle.load(open(sys.argv[2], "rb"))
    lam, ks = float(sys.argv[3]), [float(x) for x in sys.argv[4:]]
    lex = ime.Lexicon(os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt"),
                      overlay=os.path.join(ROOT, "data", "lexicon", "overlay-add.tsv"))
    dev = sorted(glob.glob(os.path.join(ROOT, "eval", "dev", "*.txt")))
    sets = {"dev302": rows_of([f for f in dev if not f.endswith("user-typing.txt")], 302),
            "typing76": rows_of([os.path.join(ROOT, "eval", "dev", "user-typing.txt")])}
    for name, f in [("discord845", "discord-rows.txt"), ("news294", "news/news-rows-300.txt")]:
        if os.path.exists(os.path.join(PRIVATE, f)):
            sets[name] = rows_of([os.path.join(PRIVATE, f)])
    for k in ks:
        uni, bi = collections.Counter(wiki["uni"]), collections.Counter(wiki["bi"])
        if k:
            for w, c in coll["uni"].items():
                uni[w] += c * k
            for b, c in coll["bi"].items():
                bi[b] += c * k
        lm = Bigram({"uni": uni, "bi": bi}, lam)
        line = [f"k={k:g} λ={lam}"]
        for name, rows in sets.items():
            ok = sum("".join(ime.decode(lex, syls, learner=lm, beam=64)[0][1]).translate(LENIENT) == t.translate(LENIENT)
                     for _, t, syls in rows)
            line.append(f"{name} {ok}/{len(rows)}")
        print(" | ".join(line), flush=True)


if __name__ == "__main__":
    main()
