"""S2n 契約 §3 的突變檢查，作用在簡體句轉換完成後的輸出（不是字表），在小樣本上量字串次數（不經斷詞）：
樣本＝維基第 300–800 篇的句子 ＋ Tatoeba cmn 全部例句。
  none：改前的轉換（沒有簡體句路徑）　real：這一片的轉換
  (a) 簡體句輸出的 後 改回 后：殘留 5% 門檻必須不過
  (b) 簡體句輸出的 後 改成 候：殘留下降但七個詞合計的正確寫法比例必須掉到 80% 以下
  (c) 簡體句輸出不做 裏→裡（TW_CHAR 拿掉它）：哪里→哪裡 單獨的比例必須掉到 80% 以下
用法：python3 experiments/s2n/mutations.py
"""
import bz2
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "s2"))
import build_counts as bc  # noqa: E402

conv = bc.load_conv()
sents = []
for raw in list(bc.articles(800))[300:800]:
    t = raw.replace("&lt;", "<")
    for _ in range(3):
        t = bc.TEMPLATE.sub("", t)
    for pat, rep in bc.MARKUP:
        t = pat.sub(rep, t)
    sents += bc.SENT.findall(t)
with bz2.open(os.path.join(bc.SRC, "colloquial", "cmn_sentences.tsv.bz2"), "rt", encoding="utf-8") as f:
    sents += [l.rstrip("\n").split("\t")[-1] for l in f]
K7 = ["之后", "由于", "最后", "然后", "以后", "此后", "哪里"]
G7 = ["之後", "由於", "最後", "然後", "以後", "此後", "哪裡"]
simp = bc.is_simplified


def run(label, post=None, drop_li=False, off=False):
    tw = dict(bc.TW_CHAR)
    if drop_li:
        bc.TW_CHAR.pop("裏")
    bc.is_simplified = (lambda s: False) if off else simp
    out = []
    for s in sents:
        c = bc.convert(s, *conv)
        out.append(post(c) if post and simp(s) else c)
    bc.TW_CHAR.clear(); bc.TW_CHAR.update(tw)
    return "".join(out)


base = run("none", off=True)
rb = [base.count(k) for k in K7]; gb = [base.count(k) for k in G7]
print("none  ", rb, sum(rb), gb, sum(gb))
for label, kw in (("real", {}), ("(a)", {"post": lambda c: c.replace("後", "后")}), ("(b)", {"post": lambda c: c.replace("後", "候")}),
                  ("(c)", {"drop_li": True})):
    t = run(label, **kw)
    r = [t.count(k) for k in K7]; g = [t.count(k) for k in G7]
    dec, inc = sum(rb) - sum(r), sum(g) - sum(gb)
    li = (g[6] - gb[6]) / max(rb[6] - r[6], 1)
    print(f"{label:6s}", r, sum(r), g, sum(g), f"| residue {sum(r) / sum(rb):.2%} ({'gate FAIL' if sum(r) > 0.05 * sum(rb) else 'gate pass'});"
          f" gain/decrease {inc / max(dec, 1):.0%} ({'<80% FAIL' if inc < 0.8 * dec else '>=80% pass'}); 哪里->哪裡 {li:.0%}")
