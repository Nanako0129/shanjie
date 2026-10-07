"""S2f 修訂二 7.3 的疊加層檢查（讀重產後的檔案、詞庫與轉換；只印統計）。
用法：python3 experiments/s2f/check_overlay.py [overlay-add.tsv overlay-removed.tsv overlay-variant-removed.tsv]
留下的疊加層裡要找不到：(1) fold(w) ≠ w 而且 fold(w) 在同讀音列的基底或留下的疊加層裡；(2) 有 S2n 目標、目標有落點，卻沒被拿掉。
另外每一筆拿掉的列，替代寫法都要在基底或留下的疊加層裡。有任何違規就 exit 1。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for sub in ("reference/proto", "experiments/s2", "tools"):
    sys.path.insert(0, os.path.join(ROOT, sub))
import build_counts as bc  # noqa: E402
import build_overlay as bo  # noqa: E402
import ime  # noqa: E402


def main():
    lex = os.path.join(ROOT, "data", "lexicon")
    paths = sys.argv[1:4] or [bo.OUT, bo.REMOVED, bo.VARIANT_REMOVED]
    base = ime.Lexicon(bo.BASE)
    words = set(base.by_word)
    conv = bc.load_conv()
    fold = {**bc.MERGE, **bc.TW_VARIANTS}
    fold_w = lambda t: "".join(fold.get(c, c) for c in t)
    kept = {}                                       # 詞 → 主要讀音（每個詞的第一列；變調列跳過）
    for line in open(paths[0], encoding="utf-8"):
        r, w = line.split("\t")[:2]
        kept.setdefault(w, tuple(r.split("-")))
    gone = {}
    for p in paths[1:]:
        for line in open(p, encoding="utf-8"):
            w, to, _ = line.rstrip("\n").split("\t")
            gone[w] = to
    moe = bo.moe_titles()
    bad1 = [w for w, r in kept.items() if fold_w(w) != w and (any(b == fold_w(w) for b, _ in base.by_reading.get(r, ())) or kept.get(fold_w(w)) == r)]
    bad2 = []
    for w in kept:
        if bc.is_simplified(w) and w not in words and w not in moe:
            t = bc.convert(w, *conv)
            if t != w and (t in words or t in kept or t in gone):
                bad2.append(w)
    bad3 = [w for w, to in gone.items() if to not in words and to not in kept]
    print(f"kept {len(kept)} words; removed {len(gone)}; (1) fold still present: {len(bad1)}; (2) S2n target with a landing: {len(bad2)}; replacement missing: {len(bad3)}")
    sys.exit(1 if bad1 or bad2 or bad3 else 0)


if __name__ == "__main__":
    main()
