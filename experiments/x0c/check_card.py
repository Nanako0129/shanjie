"""X0c acceptance 1: the model card's three printed results with score_0c's implementation.
  experiments/x0c/run_sandboxed.sh --out DIR -- experiments/x0c/check_card.py ~/.cache/shanjie/x0c/model
The card prints no numbers, only orders and a choice, so this is a comparison of orders, not a numeric reproduction."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # python -I does not add the script folder
import score_0c as S  # noqa: E402

m = S.load_model(sys.argv[1])


CASES = [("下週就是", ["ㄑㄧˊ", "ㄓㄨㄥ", "ㄎㄠˇ"], ["其中考", "期中考"]),
         ("", ["ㄐㄧㄣ", "ㄊㄧㄢ", "ㄊㄧㄢ", "ㄑㄧˋ", "ㄏㄣˇ", "ㄏㄠˇ"], ["金天天氣很好", "今天天氣很好", "今天天器很好"]),
         ("今天天氣很好我們去", ["ㄍㄨㄥ", "ㄩㄢˊ"], ["公元", "公園"])]
single = [S.score(m, *c, packed=False) for c in CASES]    # the card's way: one run per candidate
packed = [S.score(m, *c, packed=True) for c in CASES]     # prefix tree, used for the real runs
d = max(abs(x - y) for a, b in zip(single, packed) for x, y in zip(a, b))
order = lambda c, sc: [x for _, x in sorted(zip(sc, c[2]), key=lambda t: -t[0])]
o1, o2 = order(CASES[0], single[0]), order(CASES[1], single[1])
fused = [e + 2.0 * s for e, s in zip([-7.0, -7.5], single[2])]
best = max(range(2), key=fused.__getitem__)
choice = CASES[2][2][best if best != 0 and fused[best] - fused[0] > 0.0 else 0]
print("rank 1:", o1, [round(x, 4) for x in single[0]])
print("rank 2:", o2, [round(x, 4) for x in single[1]])
print("choose:", choice, [round(x, 4) for x in single[2]])
print("max |packed - single| (ln) =", d)
ok = d <= 1e-4 and o1 == ["期中考", "其中考"] and o2[0] == "今天天氣很好" and choice == "公園"
print("OK" if ok else "MISMATCH")
sys.exit(0 if ok else 1)
