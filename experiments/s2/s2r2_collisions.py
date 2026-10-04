"""S2r-2 round 3：疊加層變調列（同一個詞的第二列）和其他詞共用讀音時的排名（unigram，同分依檔案順序，和核心相同）。

印出：變調列總數、和別的詞共用讀音的列數、排第一的列數；排第一的再分成「有基底詞同讀音」與「只有疊加層詞同讀音」，
並分成同分（靠檔案順序）與嚴格較高。用法：python3 experiments/s2/s2r2_collisions.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "reference", "proto"))
import ime  # noqa: E402

LEX = os.path.join(ROOT, "data", "lexicon")


def main():
    base = ime.Lexicon(os.path.join(LEX, "mcbpmf-data.txt"))
    basew = {(s, w) for s, ents in base.by_reading.items() for w, _ in ents}
    lex = ime.Lexicon(os.path.join(LEX, "mcbpmf-data.txt"), ime.OVERLAYS)
    seen, var = set(), []
    for l in open(ime.OVERLAYS[0], encoding="utf-8"):
        r, w, sc, _ = l.rstrip("\n").split("\t")
        (var.append((tuple(r.split("-")), w, float(sc))) if w in seen else None)
        seen.add(w)
    shared = first = tie = 0
    kinds = {"beats base word": 0, "overlay competitor only": 0}
    for syls, w, sc in var:
        ents = lex.by_reading[syls]
        others = [(x, s) for x, s in ents if x != w]
        if not others:
            continue
        shared += 1
        if ents[0][0] == w:   # 穩定排序：同分時先載入的在前
            first += 1
            tie += ents[0][1] == others[0][1]
            kinds["beats base word" if any((syls, x) in basew for x, _ in others) else "overlay competitor only"] += 1
    print(f"variant rows {len(var)}; share a reading with another word {shared}; rank first {first} (exact ties {tie}); {kinds}")


if __name__ == "__main__":
    main()
