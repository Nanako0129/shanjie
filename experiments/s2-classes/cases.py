"""五句使用者回報的句子：各設定、各 profile 的前 3 名與分數。用法：python3 cases.py 256:0.3,512:0.3"""
import sys
import classlm as C
import lm as L

CASES = [("量心電圖", "ㄌㄧㄤˊ ㄒㄧㄣ ㄉㄧㄢˋ ㄊㄨˊ"), ("照建議", "ㄓㄠˋ ㄐㄧㄢˋ ㄧˋ"), ("已送出", "ㄧˇ ㄙㄨㄥˋ ㄔㄨ"),
         ("打開診斷頁閃退", "ㄉㄚˇ ㄎㄞ ㄓㄣˇ ㄉㄨㄢˋ ㄧㄝˋ ㄕㄢˇ ㄊㄨㄟˋ"), ("剛剛兩個都有綁", "ㄍㄤ ㄍㄤ ㄌㄧㄤˇ ㄍㄜˋ ㄉㄡ ㄧㄡˇ ㄅㄤˇ")]
base = C.ClassLM(C.LM_PATH); lex = C.make_lex(base)
for spec in ["base"] + sys.argv[1].split(","):
    lm = base if spec == "base" else C.ClassLM(C.LM_PATH, 40000, int(spec.split(":")[0]), float(spec.split(":")[1]))
    for prof in ("chat", "formal"):
        for truth, rd in CASES:
            nb = L.decode(lex, rd.split(), lm, prof)
            top = "".join(nb[0][1]); mark = "OK " if top == truth else "BAD"
            rk = next((i + 1 for i, (_, ws) in enumerate(nb) if "".join(ws) == truth), None)
            print(f"{spec:9s}{prof:7s}{mark} {truth} -> {top} ({nb[0][0]:.2f}) | truth rank {rk}" + (f" ({[s for s, ws in nb if ''.join(ws) == truth][0]:.2f})" if rk else ""))
