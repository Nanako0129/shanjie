"""片 E：替評測句產生「使用者實際會打的」讀音，只把真正有歧義的列交給人確認。

用法：python3 tools/readings.py <in.txt> <out.txt>
  in.txt ：前文|句子          （也接受已有第三欄的列，原樣保留）
  out.txt：前文|句子|讀音      （讀音以空白分隔音節）
  stderr ：CHECK 列（需要人確認）與 UNREADABLE 列（詞庫拼不出，未寫入 out）

規則（依序）：
  0. tools/reading_overrides.tsv 裡人工確認過的詞：直接用（不在詞庫讀音裡就標 GAP，不寫入）；
  1. 多字詞在萌典：取萌典第一個讀音（不取「又音」），而且必須是詞庫裡這個詞的讀音之一；
     （單字不套用：萌典把「的」的第一個讀音排成 ㄉㄧˋ）
  2. 單字在小麥注音 heterophony1.list（破音字主要讀音，反映台灣打字習慣）：用它；
  3. 「一」「不」打本調（ㄧ、ㄅㄨˋ），不打變調：只剩一個候選符合時用它；
  4. 詞庫裡這個詞只有一個讀音，或第一名分數明顯較高（差 ≥ 0.5）：用詞庫第一名；
  5. 其他：用詞庫第一名但標 CHECK。
萌典是 CC BY-ND，只用來決定評測輸入的讀音，不散布其內容。
"""
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "reference", "proto"))
import ime  # noqa: E402

MOEDICT = os.path.expanduser("~/side-project/ime-research/repos/moedict-data/dict-revised_bkup.json")
HETERO = os.path.expanduser("~/side-project/ime-research/repos/McBopomofo/Source/Data/heterophony1.list")
OVERRIDES = os.path.join(REPO, "tools", "reading_overrides.tsv")


def moe_primary():
    """詞 → 第一個讀音（音節 tuple）。萌典把輕聲寫成前置 ˙，轉成小麥的後置寫法。"""
    out = {}
    for e in json.load(open(MOEDICT, encoding="utf-8")):
        t, hs = e.get("title"), e.get("heteronyms") or []
        if not t or t in out or not hs or not hs[0].get("bopomofo"):
            continue
        first = hs[0]["bopomofo"].split("（")[0].replace("　", " ").split()
        out[t] = tuple(s[1:] + "˙" if s.startswith("˙") else s for s in first)
    return out


def main(src, dst):
    lex = ime.Lexicon(os.path.join(REPO, "data", "lexicon", "mcbpmf-data.txt"))
    alts = {}
    for syls, entries in lex.by_reading.items():
        for w, s in entries:
            alts.setdefault(w, []).append((s, syls))
    for v in alts.values():
        v.sort(key=lambda x: -x[0])
    moe = moe_primary()
    hetero = dict(l.split() for l in open(HETERO, encoding="utf-8") if len(l.split()) == 2)
    overrides = {w: tuple(r.split()) for w, r in
                 (l.rstrip("\n").split("\t") for l in open(OVERRIDES, encoding="utf-8") if not l.startswith("#"))}
    checks = 0
    with open(dst, "w", encoding="utf-8") as f:
        for line in open(src, encoding="utf-8"):
            line = line.rstrip("\n")
            if not line or line.startswith("#") or line.count("|") >= 2:
                f.write(line + "\n")
                continue
            ctx, sent = line.split("|", 1)
            words = ime.segment_words(lex, sent)
            if words is None:
                print(f"UNREADABLE\t{sent}", file=sys.stderr)
                continue
            out, notes, gap = [], [], False
            for w, auto in words:
                cands = [syls for _, syls in alts.get(w, [])]
                if w in overrides:
                    if overrides[w] not in cands:
                        print(f"GAP\t{sent}\t{w} 的確認讀音不在詞庫", file=sys.stderr)
                        gap = True
                    out.extend(overrides[w])
                    continue
                citation = [c for c in cands
                            if all(r == {"一": "ㄧ", "不": "ㄅㄨˋ"}.get(ch, r) for ch, r in zip(w, c))]
                if len(w) > 1 and w in moe and moe[w] in cands:
                    pick = moe[w]
                elif len(w) == 1 and w in hetero and (hetero[w],) in cands:
                    pick = (hetero[w],)
                elif len(citation) == 1 and len(citation) < len(cands) and any(ch in "一不" for ch in w):
                    pick = citation[0]
                elif len(cands) == 1 or alts[w][0][0] - alts[w][1][0] >= 0.5:
                    pick = cands[0]
                else:
                    pick = cands[0]
                    notes.append(f"{w}: 暫用 {' '.join(pick)}；其他 " +
                                 "、".join(" ".join(c) for c in cands[1:]))
                out.extend(pick)
            if gap:
                continue
            if notes:
                checks += 1
                print(f"CHECK\t{sent}\n\t" + "\n\t".join(notes), file=sys.stderr)
            f.write(f"{ctx}|{sent}|{' '.join(out)}\n")
    print(f"CHECK rows: {checks}", file=sys.stderr)


if __name__ == "__main__":
    main(*sys.argv[1:3])
