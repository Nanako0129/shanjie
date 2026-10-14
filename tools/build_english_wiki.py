#!/usr/bin/env python3
"""中文維基裡夾在中文之間的英文字（docs/contracts/zhen-words.md §2.2）。

  count --out COUNTS.tsv [--dump FILE] [--articles N]   讀 dump（只在 Mac 以外的機器跑），寫每個小寫鍵的 df 與各種寫法的次數（repo 外）
  emit COUNTS.tsv --min-df N --out FILE                  df >= N 的鍵，每行一個正式寫法（次數最多的寫法，同次數取位元組序最小的），依鍵排序

文章文字照 experiments/s2/build_counts.py：articles() 取主名字空間的條目，同一套去標記（TEMPLATE、MARKUP）；在那之前先拿掉
分類與檔案連結、網址（不然 Category、thumb、https 這類字每篇都有）。只數含漢字的句子裡的字：英文字＝前後都不是
[A-Za-z0-9] 或拉丁重音字母的最長 [A-Za-z]+，長度 3–32（GPT4、x86 這種黏著數字的整個丟掉；Édouard 不會變成 douard）。
另外丟掉幾種不是混打的英文（第一次抽樣過半是雜訊，研究紀錄 2026-10-14）：括號裡的原文註記（「（德語：Altschwendt）」、
不含漢字的整個括號「珍妮特（Janet Shearon）」）、資訊框與表格沒拿乾淨的行（以 | { } ! 開頭，「| established_date1 = 1911年」）
與句子（還有 {{ }} | 的整句不數）、HTML 實體（&nbsp;）、繁簡轉換的語言代碼（zh-hans）、<math> 與程式碼區塊、緊接在 . / 後面的字（a.png、g/mol），
以及夾在漢字之間超過 MAX_RUN_WORDS 個英文字的一段（書名、機構全名這類英文片語）。df＝出現過的條目數。
"""
import argparse, collections, hashlib, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "experiments", "s2"))

# 分類、檔案、圖片連結整個拿掉（含說明文字）：中文維基兩種寫法都有。
NS_LINK = re.compile(r"\[\[\s*(?:category|file|image|media|分類|分类|文件|檔案|档案|圖像|图像)\s*:[^\[\]]*(?:\[\[[^\]]*\]\][^\[\]]*)*\]\]", re.I)
URL = re.compile(r"https?://\S+|www\.\S+", re.I)
WORD = re.compile(r"(?<![A-Za-z0-9_./\u00C0-\u024F])[A-Za-z]+(?![A-Za-z0-9_\u00C0-\u024F]|[./][A-Za-z])")
# 括號裡「某語：」或「某文：」開頭的原文註記，整個括號拿掉（全形、半形都有）。
GLOSS = re.compile(r"[（(][^（）()]*?[語语文][：:][^（）()]*[）)]|[（(][^（）()㐀-䶿一-鿿]*[）)]")
RESIDUE = re.compile(r"^\s*[|{}!]")
# 去標記之後還有 {{ }} | 的句子是沒拿乾淨的模板（巢狀超過三層）：整句不數（archiveurl、accessdate、Cite 這類參數名）。
TEMPLATE_LEFT = re.compile(r"\{\{|\}\}|\|")
BLOCK = re.compile(r"<(math|chem|code|syntaxhighlight|source|score|timeline)\b[^>]*>.*?</\1>", re.S | re.I)
ENTITY = re.compile(r"&[A-Za-z]+;|&#\d+;")
LANG = re.compile(r"(?<![A-Za-z])zh(?:-[A-Za-z]+)+")   # 不用 \b：漢字也算 \w，「命题zh-hans:里」的 zh 前面沒有 \b
# 漢字與全形標點之間的一段；英文字超過 MAX_RUN_WORDS 個就是英文片語，不是混打。3：「Microsoft Office 365」這種留著，
# 「The Lord of the Rings」這種丟掉。
RUN_SPLIT = re.compile(r"[㐀-䶿一-鿿\u3000-\u303f\uff00-\uffef]+")
MAX_RUN_WORDS = 3
HAN = re.compile(r"[㐀-䶿一-鿿]")
SENT = re.compile(r"[^。！？；\n]+")
LEN = range(3, 33)


def clean(raw):
    """原始 wikitext -> 去標記後的文字（build_counts.count_batch 的前半，不做繁簡轉換：英文字不受影響）。"""
    import build_counts as bc
    t = raw.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"')
    t = URL.sub(" ", NS_LINK.sub(" ", BLOCK.sub(" ", t)))
    for _ in range(3):
        t = bc.TEMPLATE.sub("", t)
    for pat, rep in bc.MARKUP:
        t = pat.sub(rep, t)
    return LANG.sub(" ", ENTITY.sub(" ", t))


def words(text):
    """去標記後的一篇 -> 含漢字的句子裡的英文字（原寫法）。"""
    out = []
    text = "\n".join(l for l in text.split("\n") if not RESIDUE.match(l))
    for s in SENT.findall(text):
        if HAN.search(s):
            if TEMPLATE_LEFT.search(s):
                continue
            for run in RUN_SPLIT.split(GLOSS.sub("。", s)):
                ws = WORD.findall(run)
                if len(ws) <= MAX_RUN_WORDS:
                    out += [w for w in ws if len(w) in LEN]
    return out


def count(texts):
    """-> {小寫鍵: [df, Counter(寫法 -> 次數)]}"""
    acc = {}
    for t in texts:
        ws = words(t)
        for k in {w.lower() for w in ws}:
            acc.setdefault(k, [0, collections.Counter()])[0] += 1
        for w in ws:
            acc[w.lower()][1][w] += 1
    return acc


def canonical(forms):
    return min(forms.items(), key=lambda kv: (-kv[1], kv[0].encode()))[0]


def emit(acc, min_df):
    return [canonical(forms) for k, (df, forms) in sorted(acc.items(), key=lambda kv: kv[0].encode()) if df >= min_df]


def write_counts(acc, path, header):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(header)
        for k in sorted(acc, key=str.encode):
            df, forms = acc[k]
            f.write(k + "\t" + str(df) + "\t" + " ".join(f"{w}:{n}" for w, n in sorted(forms.items())) + "\n")


def read_counts(path):
    acc, header = {}, []
    for l in open(path, encoding="utf-8"):
        if l.startswith("#"):
            header.append(l)
            continue
        k, df, forms = l.rstrip("\n").split("\t")
        acc[k] = [int(df), collections.Counter({w: int(n) for w, n in (x.rsplit(":", 1) for x in forms.split(" "))})]
    return acc, "".join(header)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("count")
    c.add_argument("--out", required=True)
    c.add_argument("--dump")
    c.add_argument("--articles", type=int, default=10 ** 9)
    e = sub.add_parser("emit")
    e.add_argument("counts")
    e.add_argument("--min-df", type=int, required=True)
    e.add_argument("--out", required=True)
    a = p.parse_args()
    if a.cmd == "count":
        import build_counts as bc
        if a.dump:
            bc.DUMP = a.dump
        acc = count(clean(t) for t in bc.articles(a.articles))
        write_counts(acc, a.out, f"# input {os.path.basename(bc.DUMP)} sha256 {sha256(bc.DUMP)}\n")
        print(len(acc), "keys")
    else:
        acc, header = read_counts(a.counts)
        out = emit(acc, a.min_df)
        with open(a.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(header + f"# min_df {a.min_df}\n" + "".join(w + "\n" for w in out))
        print(len(out), "words")


if __name__ == "__main__":
    main()
