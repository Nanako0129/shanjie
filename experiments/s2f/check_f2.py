"""S2f 修訂一 6.3 的模型層檢查（只讀模型與詞庫，印統計）。用法：python3 experiments/s2f/check_f2.py <模型.sjlm>
(1) 被字形表改壞過的基底詞有次數；(2) 每個前文裡，同一類作為「後一個詞」最多一個成員有條目；(3) 占、佔 沒有合併。不過就 exit 1。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for sub in ("reference/proto", "experiments/s2", "tools"):
    sys.path.insert(0, os.path.join(ROOT, sub))
import build_lm  # noqa: E402
import ime  # noqa: E402
import lm as L  # noqa: E402

PROTECTED = ["排泄", "排泄物", "棱錐", "泄殖腔"]


def main():
    lm = L.BigramLM(sys.argv[1])
    lex = ime.Lexicon(os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt"), overlay=ime.OVERLAYS)
    cls = build_lm.variant_classes(lex.by_reading)
    ok = True
    for w in PROTECTED:
        print(f"count {w} = {lm.count(w)}")
        ok &= lm.count(w) > 0
    member = {lm.ids[w]: g for w, g in cls.items() if w in lm.ids}
    dup = 0
    for _, (_, _, entries) in lm.ctx.items():
        seen = {}
        for i in entries:
            if i in member:
                g = member[i]
                dup += g in seen
                seen[g] = True
    print(f"contexts {len(lm.ctx)}; classes {len(set(cls.values()))}; contexts with two members of one class as next word: {dup}")
    ok &= dup == 0
    print(f"count 占 = {lm.count('占')}, 佔 = {lm.count('佔')}")
    ok &= lm.count("占") != lm.count("佔")
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
