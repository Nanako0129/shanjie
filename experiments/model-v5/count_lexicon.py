"""model-v5 契約 §9：計數用的詞包詞表（`--extra-lexicon` 的輸入）。出貨的詞包不變。

衝突讀音＝acg-collisions.tsv 的讀音，加上用加法計數的 model-v5 重驗時新冒出的未處置讀音
（collision-readings-v5.txt，由 tools/build_acg_pack.py 的 collisions.txt 取出，每行一個讀音、音節以空白分隔）。
詞包詞的讀音只要是某個衝突讀音裡連續的兩個以上音節（含整個讀音），整個詞就不進計數，
那些讀音的解碼不會用到有計數的詞包詞，使用者對同音名字的決定照樣成立。只排除整個讀音不夠：
「艾莉卡」排除了，「艾莉」＋「卡」仍有計數，拼出來還是同一個名字（契約 §9，2026-10-10 重驗量到 15 個讀音因此換了第一名）。
用法：python3 experiments/model-v5/count_lexicon.py PACK.tsv COLLISIONS.tsv EXTRA_READINGS.txt OUT.tsv"""
import sys


def syllables(r):
    return tuple(r.replace("-", " ").split())


def main(pack, collisions, extra, out):
    conf = {syllables(l.split("\t")[0]) for l in open(collisions, encoding="utf-8") if l.strip() and not l.startswith("#")}
    conf |= {syllables(l) for l in open(extra, encoding="utf-8") if l.strip()}
    runs = {c[i:j] for c in conf for i in range(len(c)) for j in range(i + 2, len(c) + 1)}
    rows = [l.rstrip("\n").split("\t") for l in open(pack, encoding="utf-8") if l.strip() and not l.startswith("#")]
    bad = {r[1] for r in rows if syllables(r[0]) in runs}
    keep = [r for r in rows if r[1] not in bad]
    with open(out, "w", encoding="utf-8") as f:
        f.writelines("\t".join(r) + "\n" for r in keep)
    print(f"conflict readings {len(conf)}, excluded words {len(bad)} of {len({r[1] for r in rows})}, rows kept {len(keep)}")


if __name__ == "__main__":
    main(*sys.argv[1:5])
