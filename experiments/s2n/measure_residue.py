"""S2n 契約 §3 的語料量測：殘留（簡體寫法）、正確寫法、總句段數與總詞數、正確用法保留；每份計數分開報，再報加權總和。
次數是計數檔 `uni`（斷詞後的詞次）；「句段」是計數檔的 `runs`，總詞數是 `uni` 的總和。

用法：python3 experiments/s2n/measure_residue.py --before 檔:權重 檔:權重 --after 檔:權重 檔:權重
例：  … --before ~/.cache/shanjie/work/s2/counts-200000.pkl:1 ~/.cache/shanjie/work/s2/counts-colloquial3.pkl:5 \
          --after  ~/.cache/shanjie/work/s2n/counts-200000.pkl:1 ~/.cache/shanjie/work/s2n/counts-colloquial3.pkl:5
結尾印驗收：殘留是否降到 5% 以下、正確寫法增加量是否 ≥ 殘留減少量的 80%（逐組與合計）、總句段／總詞數降幅（>1% 是停止條件）、
保留詞降幅（>20% 要說明）。
"""
import argparse
import os
import pickle

RESIDUE = ["后", "于", "里", "之后", "由于", "最后", "然后", "以后", "此后", "哪里"]
CORRECT = {"后": "後", "于": "於", "之后": "之後", "由于": "由於", "最后": "最後", "然后": "然後", "以后": "以後", "此后": "此後", "哪里": "哪裡"}
EXTRA_CORRECT = ["裡"]   # 里 的對照，不列入 80% 的算式（契約只列到 哪裡）
KEEP = ["皇后", "王后", "太后", "公里", "里長", "鄰里", "台灣", "干涉", "只有", "系統"]


def load(specs):
    out = []
    for s in specs:
        path, w = s.rsplit(":", 1)
        c = pickle.load(open(os.path.expanduser(path), "rb"))
        out.append((os.path.basename(path), int(w), c["uni"], c["runs"]))
    return out


def stats(files):
    """每份 (名稱, 權重, 詞→次數 的取用函式, 句段, 詞數)。"""
    return [(n, w, u, r, sum(u.values())) for n, w, u, r in files]


def row(label, b, a, fb, fa):
    per = "  ".join(f"{n[:12]}: {x[0].get(label, 0):>9,} -> {x[1].get(label, 0):>9,}" for n, x in zip([f[0] for f in fb], zip([f[2] for f in fb], [f[2] for f in fa])))
    wb = sum(f[1] * f[2].get(label, 0) for f in fb); wa = sum(f[1] * f[2].get(label, 0) for f in fa)
    return per, wb, wa


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", nargs="+", required=True)
    ap.add_argument("--after", nargs="+", required=True)
    a = ap.parse_args()
    fb, fa = stats(load(a.before)), stats(load(a.after))
    print("== 總量（改前 -> 改後）")
    for (n, w, _, rb, tb), (_, _, _, ra, ta) in zip(fb, fa):
        print(f"{n}: 句段 {rb:,} -> {ra:,} ({(ra - rb) / rb:+.2%})  詞數 {tb:,} -> {ta:,} ({(ta - tb) / tb:+.2%})")
    rb_w, ra_w = sum(f[1] * f[3] for f in fb), sum(f[1] * f[3] for f in fa)
    tb_w, ta_w = sum(f[1] * f[4] for f in fb), sum(f[1] * f[4] for f in fa)
    print(f"加權: 句段 {rb_w:,} -> {ra_w:,} ({(ra_w - rb_w) / rb_w:+.2%})  詞數 {tb_w:,} -> {ta_w:,} ({(ta_w - tb_w) / tb_w:+.2%})")
    stop = max(-(ra - rb) / rb for (_, _, _, rb, _), (_, _, _, ra, _) in zip(fb, fa)) > 0.01 or (rb_w - ra_w) / rb_w > 0.01 or (tb_w - ta_w) / tb_w > 0.01
    wt = {}
    for title, items in (("殘留", RESIDUE), ("正確寫法", list(CORRECT.values()) + EXTRA_CORRECT), ("正確用法（保留）", KEEP)):
        print(f"== {title}")
        for it in items:
            per, wb, wa = row(it, None, None, fb, fa)
            wt[it] = (wb, wa)
            print(f"{it:4s} {per}   加權: {wb:>10,} -> {wa:>10,} ({(wa / wb - 1) if wb else 0.0:+.1%})")
    print("== 驗收（契約 §3：七個詞合計）")
    SEVEN = [r for r in RESIDUE if r not in ("后", "于", "里")]
    for it in SEVEN:
        wb, wa = wt[it]
        print(f"殘留 {it}: 剩 {wa / wb:.2%}（個別超過 5% 可接受，要列出）")
    rb7, ra7 = sum(wt[r][0] for r in SEVEN), sum(wt[r][1] for r in SEVEN)
    dec7 = rb7 - ra7
    inc7 = sum(wt[CORRECT[r]][1] - wt[CORRECT[r]][0] for r in SEVEN)
    print(f"七詞合計殘留: {rb7:,} -> {ra7:,} ({ra7 / rb7:.2%}) {'PASS' if ra7 <= 0.05 * rb7 else 'FAIL'} (<=5%)")
    print(f"七詞正確寫法增加 {inc7:,} / 殘留減少 {dec7:,} = {inc7 / max(dec7, 1):.0%} {'PASS' if inc7 >= 0.8 * dec7 else 'FAIL'} (>=80%)")
    for res in ("后", "于", "里"):   # 單獨成詞：只報不判
        good = CORRECT.get(res)
        dec = wt[res][0] - wt[res][1]
        extra = f"；{good} 增加 {wt[good][1] - wt[good][0]:,}（{(wt[good][1] - wt[good][0]) / max(dec, 1):.0%}）" if good else ""
        print(f"單獨 {res}: {wt[res][0]:,} -> {wt[res][1]:,}（剩 {wt[res][1] / wt[res][0]:.1%}）{extra}（資訊）")
    for r in ("哪里",):
        dec = wt[r][0] - wt[r][1]; inc = wt[CORRECT[r]][1] - wt[CORRECT[r]][0]
        print(f"{r}->{CORRECT[r]} 單獨: 減少 {dec:,} 增加 {inc:,} = {inc / max(dec, 1):.0%}")
    for it in KEEP:
        wb, wa = wt[it]
        print(f"保留 {it}: {wb:,} -> {wa:,} ({(wa / wb - 1) if wb else 0.0:+.1%}) {'OK' if wa >= 0.8 * wb else 'EXPLAIN (>20% drop)'}")
    print(f"總句段／總詞數降幅 >1%（停止條件）: {'YES' if stop else 'no'}")


if __name__ == "__main__":
    main()
