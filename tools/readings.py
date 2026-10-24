"""片 E：替評測句產生「使用者實際會打的」讀音，只把真正有歧義的列交給人確認。

用法：python3 tools/readings.py [--rule-3b [--unihan Unihan_Readings.txt]] <in.txt> <out.txt>
  python3 tools/readings.py --report-inverted [--unihan Unihan_Readings.txt]   （不寫任何檔：印出 9 個倒掛字的現行與建議分數，見下面）
  in.txt ：前文|句子          （也接受已有第三欄的列，原樣保留）
  out.txt：前文|句子|讀音      （讀音以空白分隔音節）
  stderr ：CHECK 列（需要人確認）與 UNREADABLE 列（詞庫拼不出，未寫入 out）

規則（依序）：
  0. tools/reading_overrides.tsv 裡人工確認過的詞：直接用（不在詞庫讀音裡就標 GAP，不寫入）；
  1. 「一」「不」打本調（ㄧ、ㄅㄨˋ），不打變調：含這兩字的詞，只剩一個候選符合時用它
     （要排在萌典前面：萌典詞條把「不要」寫成變調 ㄅㄨˊ ㄧㄠˋ）；
  2. 多字詞在萌典：取萌典第一個讀音（不取「又音」），而且必須是詞庫裡這個詞的讀音之一；
     （單字不套用：萌典把「的」的第一個讀音排成 ㄉㄧˋ）
  3. 單字在小麥注音 heterophony1.list（破音字主要讀音，反映台灣打字習慣）：用它；
  3b.（只在 --rule-3b／Reader(rule_3b=True) 時啟用；預設關，其他建置工具的輸出和以前逐位元組相同）單字的第一名讀音不只一個（分數相同，原本取檔案順序）：用詞庫多字詞裡這個字各讀音出現的分數加權次數（Σ 10^分數）決定，
     要有一個讀音嚴格最多；沒有多字詞證據或平手，就用 Unihan kMandarin 的台灣值（兩個值時取第二個；檔案路徑由 --unihan 或
     SHANJIE_UNIHAN 給（只在啟用時讀），沒給就跳過、不下載），而且那個值要在平手的讀音裡；還是決定不了就走規則 5（CHECK）。
     （SJ-LM-7 契約 §2.3）
  4. 詞庫裡這個詞只有一個讀音，或第一名分數明顯較高（差 ≥ 0.5）：用詞庫第一名；
  5. 其他：用詞庫第一名但標 CHECK。
萌典是 CC BY-ND，只用來決定評測輸入的讀音，不散布其內容。
"""
import collections
import json
import os
import sys
import unicodedata

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "reference", "proto"))
import ime  # noqa: E402

# SHANJIE_MOEDICT／SHANJIE_HETERO：資料不在預設位置的機器（例如 Windows）用環境變數指過去
MOEDICT = os.environ.get("SHANJIE_MOEDICT") or os.path.expanduser("~/side-project/ime-research/repos/moedict-data/dict-revised_bkup.json")
HETERO = os.environ.get("SHANJIE_HETERO") or os.path.expanduser("~/side-project/ime-research/repos/McBopomofo/Source/Data/heterophony1.list")
UNIHAN = os.environ.get("SHANJIE_UNIHAN")   # Unihan_Readings.txt（規則 3b 的後備）；不給就不用，不下載
OVERRIDES = os.path.join(REPO, "tools", "reading_overrides.tsv")
TIE_EPS = 1e-9
INVERTED = "鈸乜掇鉈癘嘁誒溴跩"   # 契約 §0：小麥基底裡又讀（−6.8 底分）高於主讀音的 9 個字
# 建議分數：主讀音 = 最高的其他讀音 + 這個差。取 0.5＝規則 4 的「明顯較高」門檻，改完之後 readings.py 規則 4 直接當作已決定
INVERTED_LEAD = 0.5


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


# --- 規則 3b -----------------------------------------------------------------------------------------------------------------
_INITIALS = {"b": "ㄅ", "p": "ㄆ", "m": "ㄇ", "f": "ㄈ", "d": "ㄉ", "t": "ㄊ", "n": "ㄋ", "l": "ㄌ", "g": "ㄍ", "k": "ㄎ", "h": "ㄏ",
             "j": "ㄐ", "q": "ㄑ", "x": "ㄒ", "zh": "ㄓ", "ch": "ㄔ", "sh": "ㄕ", "r": "ㄖ", "z": "ㄗ", "c": "ㄘ", "s": "ㄙ"}
_FINALS = {"a": "ㄚ", "o": "ㄛ", "e": "ㄜ", "ê": "ㄝ", "ai": "ㄞ", "ei": "ㄟ", "ao": "ㄠ", "ou": "ㄡ", "an": "ㄢ", "en": "ㄣ", "ang": "ㄤ",
           "eng": "ㄥ", "er": "ㄦ", "i": "ㄧ", "ia": "ㄧㄚ", "io": "ㄧㄛ", "ie": "ㄧㄝ", "iao": "ㄧㄠ", "iu": "ㄧㄡ", "ian": "ㄧㄢ",
           "in": "ㄧㄣ", "iang": "ㄧㄤ", "ing": "ㄧㄥ", "iong": "ㄩㄥ", "u": "ㄨ", "ua": "ㄨㄚ", "uo": "ㄨㄛ", "uai": "ㄨㄞ", "ui": "ㄨㄟ",
           "uan": "ㄨㄢ", "un": "ㄨㄣ", "uang": "ㄨㄤ", "ong": "ㄨㄥ", "ü": "ㄩ", "üe": "ㄩㄝ", "üan": "ㄩㄢ", "ün": "ㄩㄣ"}
_YW = {"yi": "i", "ya": "ia", "yo": "io", "ye": "ie", "yao": "iao", "you": "iu", "yan": "ian", "yin": "in", "yang": "iang",
       "ying": "ing", "yong": "iong", "yu": "ü", "yue": "üe", "yuan": "üan", "yun": "ün",
       "wu": "u", "wa": "ua", "wo": "uo", "wai": "uai", "wei": "ui", "wan": "uan", "wen": "un", "wang": "uang", "weng": "ong"}
_TONE_MARKS = {"\u0304": "", "\u0301": "ˊ", "\u030c": "ˇ", "\u0300": "ˋ"}


def pinyin_to_bopomofo(syl):
    """One tone-marked pinyin syllable (Unihan style: ü, ê, no mark = neutral) → McBopomofo spelling (neutral tone as a trailing ˙).
    None for what it does not know (m, n, ng, hm interjections)."""
    d, tone = unicodedata.normalize("NFD", syl.strip().lower()), None
    for m, t in _TONE_MARKS.items():
        if m in d:
            tone, d = t, d.replace(m, "")
    base = unicodedata.normalize("NFC", d)
    if base in _YW:
        init, fin = "", _YW[base]
    else:
        n = 2 if base[:2] in ("zh", "ch", "sh") else 1
        init, fin = base[:n], base[n:]
        if init not in _INITIALS:
            init, fin = "", base
        if init in "jqx" and fin[:1] == "u":
            fin = "ü" + fin[1:]
    if fin == "i" and init in ("zh", "ch", "sh", "r", "z", "c", "s"):
        body = _INITIALS[init]   # 知 吃 師 日 資 雌 思：沒有 ㄧ
    elif fin in _FINALS:
        body = _INITIALS.get(init, "") + _FINALS[fin]
    else:
        return None
    return body + (tone if tone is not None else "˙")


def load_unihan(path):
    """Unihan_Readings.txt → {字: 台灣值（注音）}。kMandarin 兩個值時第一個是中國、第二個是台灣；只有一個值兩邊都用它。讀不懂的值略過。"""
    out = {}
    for line in open(path, encoding="utf-8"):
        p = line.rstrip("\n").split("\t")
        if len(p) == 3 and p[1] == "kMandarin" and p[0].startswith("U+"):
            bpmf = pinyin_to_bopomofo(p[2].split()[-1])
            if bpmf:
                out[chr(int(p[0][2:], 16))] = bpmf
    return out


def char_evidence(lex):
    """{字: {單音節讀音: Σ 10^分數}}，只看多字詞（詞的每個讀音各自加權，所以又讀詞也算）。"""
    ev = {}
    for syls, entries in lex.by_reading.items():
        if len(syls) > 1:
            for w, s in entries:
                for ch, syl in zip(w, syls):
                    d = ev.setdefault(ch, {})
                    d[syl] = d.get(syl, 0.0) + 10 ** s
    return ev


def decide_reading(ch, candidates, evidence, unihan=None):
    """規則 3b：candidates 是單音節讀音（字串）。回傳 (讀音, 依據) 或 (None, None)。依據是 "multi-char" 或 "unihan"。
    多字詞的加權次數要有一個嚴格最多（而且大於 0）；否則用 Unihan 台灣值（要在 candidates 裡）。"""
    w = sorted(((evidence.get(ch, {}).get(c, 0.0), c) for c in candidates), reverse=True)
    if w and w[0][0] > 0 and (len(w) == 1 or w[0][0] > w[1][0]):
        return w[0][1], "multi-char"
    tw = (unihan or {}).get(ch)
    if tw in candidates:
        return tw, "unihan"
    return None, None


def inverted_report(lex, unihan=None, chars=INVERTED):
    """The `chars` rows: (字, 讀音, 現行分數, 建議分數, 角色, 依據). 主讀音（decide_reading 對全部讀音的結果）的建議分數是
    現行已高於所有其他讀音就不動，否則 最高的其他讀音 + INVERTED_LEAD，其他讀音不動；決定不了的字，所有讀音角色寫 CHECK、建議分數 = 現行。
    只建議，不寫 mcbpmf-data.txt。"""
    ev, rows, readings = char_evidence(lex), [], {ch: {} for ch in chars}
    for syls, entries in lex.by_reading.items():
        if len(syls) == 1:
            for w, s in entries:
                if w in readings:
                    readings[w][syls[0]] = s
    for ch in chars:
        cur = readings[ch]
        main, how = decide_reading(ch, list(cur), ev, unihan)
        for r, s in sorted(cur.items(), key=lambda x: -x[1]):
            if main is None:
                rows.append((ch, r, s, s, "CHECK", ""))
            elif r == main:
                top = max([v for k, v in cur.items() if k != r], default=-1e9)
                rows.append((ch, r, s, s if s > top else top + INVERTED_LEAD, "main", how))
            else:
                rows.append((ch, r, s, s, "alt", how))
    return rows


class Reader:
    """規則 0–5 套在一個句子（或一個詞）上。main() 與 build_overlay.py 共用。read() 回傳 (音節 list 或 None＝讀不出, CHECK 備註, GAP 的詞)。"""

    def __init__(self, lex, moe, hetero, overrides, evidence=None, unihan=None, rule_3b=False):
        self.lex, self.moe, self.hetero, self.overrides, self.rule_3b = lex, moe, hetero, overrides, rule_3b
        self.evidence = (evidence if evidence is not None else char_evidence(lex)) if rule_3b else {}
        self.unihan = unihan or {}
        self.alts = {}
        for syls, entries in lex.by_reading.items():
            for w, s in entries:
                self.alts.setdefault(w, []).append((s, syls))
        for v in self.alts.values():
            v.sort(key=lambda x: -x[0])

    def read(self, sent):
        alts, moe, hetero, overrides = self.alts, self.moe, self.hetero, self.overrides
        words = ime.segment_words(self.lex, sent)
        if words is None:
            return None, [], []
        out, notes, gaps = [], [], []
        for w, auto in words:
            cands = [syls for _, syls in alts.get(w, [])]
            if w in overrides:
                if overrides[w] not in cands:
                    gaps.append(w)
                out.extend(overrides[w])
                continue
            citation = [c for c in cands
                        if all(r == {"一": "ㄧ", "不": "ㄅㄨˋ"}.get(ch, r) for ch, r in zip(w, c))]
            if len(citation) == 1 and len(citation) < len(cands) and any(ch in "一不" for ch in w):
                pick = citation[0]
            elif len(w) > 1 and w in moe and moe[w] in cands:
                pick = moe[w]
            elif len(w) == 1 and w in hetero and (hetero[w],) in cands:
                pick = (hetero[w],)
            elif self.rule_3b and len(w) == 1 and len(cands) > 1 and alts[w][0][0] - alts[w][1][0] < TIE_EPS and (
                    t := decide_reading(w, [c[0] for s_, c in alts[w] if alts[w][0][0] - s_ < TIE_EPS], self.evidence, self.unihan)[0]):
                pick = (t,)
            elif len(cands) == 1 or alts[w][0][0] - alts[w][1][0] >= 0.5:
                pick = cands[0]
            else:
                pick = cands[0]
                notes.append(f"{w}: 暫用 {' '.join(pick)}；其他 " +
                             "、".join(" ".join(c) for c in cands[1:]))
            out.extend(pick)
        return out, notes, gaps


def make_reader(lex, unihan_path=UNIHAN, rule_3b=False):
    """Reader over the files MOEDICT, HETERO, OVERRIDES (and, with rule_3b, the Unihan file if there is one)."""
    hetero = dict(l.split() for l in open(HETERO, encoding="utf-8") if len(l.split()) == 2)
    overrides = {w: tuple(r.split()) for w, r in
                 (l.rstrip("\n").split("\t") for l in open(OVERRIDES, encoding="utf-8") if not l.startswith("#"))}
    return Reader(lex, moe_primary(), hetero, overrides, unihan=load_unihan(unihan_path) if (rule_3b and unihan_path) else None, rule_3b=rule_3b)


def main(src, dst, unihan_path=UNIHAN, rule_3b=False):
    lex = ime.Lexicon(os.path.join(REPO, "data", "lexicon", "mcbpmf-data.txt"))
    reader = make_reader(lex, unihan_path, rule_3b)
    checks = 0
    with open(dst, "w", encoding="utf-8", newline="\n") as f:
        for line in open(src, encoding="utf-8"):
            line = line.rstrip("\n")
            if not line or line.startswith("#") or line.count("|") >= 2:
                f.write(line + "\n")
                continue
            ctx, sent = line.split("|", 1)
            out, notes, gaps = reader.read(sent)
            if out is None:
                print(f"UNREADABLE\t{sent}", file=sys.stderr)
                continue
            for w in gaps:
                print(f"GAP\t{sent}\t{w} 的確認讀音不在詞庫", file=sys.stderr)
            if gaps:
                continue
            if notes:
                checks += 1
                print(f"CHECK\t{sent}\n\t" + "\n\t".join(notes), file=sys.stderr)
            f.write(f"{ctx}|{sent}|{' '.join(out)}\n")
    print(f"CHECK rows: {checks}", file=sys.stderr)


if __name__ == "__main__":
    args = sys.argv[1:]
    unihan_path, rule_3b = UNIHAN, "--rule-3b" in args
    args = [x for x in args if x != "--rule-3b"]
    if "--unihan" in args:
        i = args.index("--unihan")
        unihan_path = args[i + 1]
        del args[i:i + 2]
    if args == ["--report-inverted"]:
        lex = ime.Lexicon(os.path.join(REPO, "data", "lexicon", "mcbpmf-data.txt"))
        print("字\t讀音\t現行分數\t建議分數\t角色\t依據")
        for ch, r, cur, new, role, how in inverted_report(lex, load_unihan(unihan_path) if unihan_path else None):
            print(f"{ch}\t{r}\t{cur}\t{new}\t{role}\t{how}")
    else:
        main(*args[:2], unihan_path=unihan_path, rule_3b=rule_3b)
