"""V3 核心的 golden（docs/contracts/v3-core-predict.md §3）：用 predict3.reference／order_s 產生 eval/golden/sp-predict.txt。
Rust 的 `shanjie-eval --predict` 讀這個檔的查詢行，輸出同格式，cli/tests/golden.rs 逐項比對。依賴模型（data/lm/bigram.sjlm）；換模型時重產：
  python3 experiments/sp/golden_predict.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import predict as P1  # noqa: E402
import predict3 as P3  # noqa: E402
from predict import L, ROOT, scores, succ_words  # noqa: E402

OUT = os.path.join(ROOT, "eval", "golden", "sp-predict.txt")
LIMIT = 9
HEAD = """# V3 核心預測 golden（experiments/sp/golden_predict.py 產生，chat 設定，lambda 0.5，依賴 model-v2）
# 查詢行：`## <模式>\\t<歷史詞>\\t<按鍵（縮寫單位序列是 -）>\\t<單位序列>`。單位序列：空白分隔的 `字元/完成旗標/聲調`（聲調空白是一聲）。
# 候選行：`詞\\t分數(Python repr)\\t是否後繼詞(1/0)`，依 S 順序的前 9 個。
"""
# 手造：大 的 ㄉㄞˋ 讀音（大夫）分數低於 ㄉㄚˋ；lp_max 取全部讀音的最高分時會不同
HAND = [("P", "<s>", "ㄉㄞˋ"), ("P", "<s>", "ㄉㄚˋ"), ("PA", "<s>", "ㄉㄞˋ")]
# 歷史詞 ZZ 不在模型裡，分數等於詞庫分數，同分才靠決定性順序（掃描 ㄅ…ㄩ 一到兩字元的前綴、P 與 PA、歷史詞 ZZ 與 <s>）：
# ㄧㄞ 的前 9 名裡有同層、同分、字數不同的詞（拿掉字數鍵會變）；ㄆㄡ 的詞有兩個同分的相容讀音（Index 排序拿掉讀音鍵、改取另一個會變）
HAND += [("P", "ZZ", "ㄧㄞ"), ("P", "ZZ", "ㄆㄡ"), ("PA", "ZZ", "ㄆㄡ")]
# Rust 的索引排序拿掉讀音鍵後，同分讀音（如 倒 ㄉㄠˇ／ㄉㄠˋ）改取詞庫順序的第一個；下面三個縮寫查詢（掃描 3,959 個同分多讀音詞
# 的 7,903 個查詢，39 個會變）的前 9 名因此改變
ABBR = [["ㄅㄨ", "ㄉㄧ", "ㄒㄧ"], ["ㄅㄨ", "ㄩ"], ["ㄅ", "ㄉ", "ㄍ"]]


def unit_str(u):
    return "".join(u[0]) + "/" + ("1" if u[1] else "0") + "/" + u[2]


def main():
    lm, lex = P1.build_lm_lex()
    lam, idx = L.PROFILES["chat"], P1.Index(lex)
    samples, _ = P1.samples_of(lex, P1.load_rows(os.path.join(ROOT, "eval", "dev", "user-typing.txt")))
    queries, seen = [], set()

    def add(mode, v, keys, units):
        k = (mode, v, tuple(units))
        if k not in seen:
            seen.add(k)
            queries.append((mode, v, keys, units))

    for c, _, syls in samples:
        v = L.history(L.context_key(c), lm)
        sp = tuple(P1.parse_syl(s) for s in syls)
        ks, pos = P1.key_sequence(sp), P1.position_units(sp)
        first = {"P1": 1, "P2": len(sp[0][0]), "P3": len(sp[0][0]) + 1, "P4": len(sp[0][0]) + 2}
        for p, t in first.items():
            if p in pos:
                add("P", v, ks[:t], pos[p])
        for p in ("A1", "A2"):
            add("PA", v, "-", pos[p])
            add("P", v, "-", pos[p])
    for m, v, ks in HAND:
        add(m, v, ks, P1.units_of(ks))
    for a in ABBR:
        add("PA", "ZZ", "-", [(tuple(c), False, "") for c in a])
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(HEAD)
        for mode, v, keys, units in queries:
            cands = P3.reference(idx, units, mode)
            tiers = P3.order_s(cands, scores(cands, lm, lam, v), succ_words(lm, v))
            f.write(f"## {mode}\t{v}\t{keys}\t{' '.join(map(unit_str, units))}\n")
            rows = [(e, s, 1) for e, s in tiers[0]] + [(e, s, 0) for e, s in tiers[1]]
            for e, s, flag in rows[:LIMIT]:
                f.write(f"{e[0]}\t{s!r}\t{flag}\n")
    print(len(queries), "queries")


if __name__ == "__main__":
    main()
