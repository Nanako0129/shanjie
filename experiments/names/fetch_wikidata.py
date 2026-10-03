"""從 Wikidata（CC0）抓人名與作品名的中文標籤，當可開關的分類詞包候選。

每類一個 SPARQL 查詢：條目類別（P31）＋知名度門檻（sitelinks）＋中文標籤（zh-tw 優先，其次 zh-hant、zh）。
輸出 ~/.cache/shanjie/sources/wikidata/<類別>.tsv：`QID\\tsitelinks\\tzh-tw\\tzh-hant\\tzh`。
用法：python3 experiments/names/fetch_wikidata.py [類別 ...]
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

OUT = os.path.expanduser("~/.cache/shanjie/sources/wikidata")
EP = "https://query.wikidata.org/sparql"
UA = "shanjie-ime-research/0.1 (open-source Zhuyin IME lexicon experiment)"
# 類別：(P31 的類，額外條件，sitelinks 門檻)
CLASSES = {
    "anime_tv": ("wd:Q63952888", "", 3),
    "anime_film": ("wd:Q20650540", "", 3),
    "manga": ("wd:Q14406742", "", 3),
    "film": ("wd:Q11424", "", 30),
    "tv_series": ("wd:Q5398426", "", 10),
    "video_game": ("wd:Q7889", "", 10),
    "band": ("wd:Q215380", "", 10),
    "person_tw": ("wd:Q5", "?item wdt:P27 wd:Q865.", 3),
    "person_world": ("wd:Q5", "", 150),
    "character": ("wd:Q15773347", "", 5),   # 虛構角色（動畫、漫畫、電玩角色的上層類）
}


def run(name):
    cls, extra, sl = CLASSES[name]
    query = f"""SELECT ?item ?sl ?tw ?hant ?zh WHERE {{
      ?item wdt:P31 {cls}; wikibase:sitelinks ?sl. FILTER(?sl >= {sl}) {extra}
      OPTIONAL {{ ?item rdfs:label ?tw. FILTER(LANG(?tw) = "zh-tw") }}
      OPTIONAL {{ ?item rdfs:label ?hant. FILTER(LANG(?hant) = "zh-hant") }}
      OPTIONAL {{ ?item rdfs:label ?zh. FILTER(LANG(?zh) = "zh") }}
      FILTER(BOUND(?tw) || BOUND(?hant) || BOUND(?zh))
    }}"""
    req = urllib.request.Request(EP + "?" + urllib.parse.urlencode({"query": query, "format": "json"}), headers={"User-Agent": UA})
    d = json.load(urllib.request.urlopen(req, timeout=120))
    rows = {}
    for b in d["results"]["bindings"]:
        q = b["item"]["value"].rsplit("/", 1)[1]
        rows[q] = (b["sl"]["value"], b.get("tw", {}).get("value", ""), b.get("hant", {}).get("value", ""), b.get("zh", {}).get("value", ""))
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, f"{name}.tsv"), "w", encoding="utf-8") as f:
        for q, r in sorted(rows.items()):
            f.write("\t".join((q,) + r) + "\n")
    return len(rows)


def main():
    for name in sys.argv[1:] or CLASSES:
        try:
            print(name, run(name), flush=True)
        except Exception as e:
            print(name, "error", type(e).__name__, flush=True)
        time.sleep(2)


if __name__ == "__main__":
    main()
