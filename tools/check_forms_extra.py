"""逐行檢查 eval/forms-extra.tsv 的收錄條件（docs/spec/evaluation.md §3.6；判準 v2 的審定詞對表）。

每行 `word<TAB>寫法<TAB>標準形<TAB>讀音<TAB>次數<TAB>依據`，六個條件每一條都要成立：
  1 辭典關係：兩種寫法等長，逐字對齊後不同的每個位置上，兩個字在教育部《重編國語辭典修訂本》裡其中一個的某個 heteronym 的某一條釋義
    （`def` 或 `link`）以「也作、亦作、同、通」連到另一個字（「同」前面不是「如、等、相、不」），或寫明是它的異體字；讀音欄在那個位置的音節在那個 heteronym 的讀音裡。
  2 兩種寫法都有人用：次數 `a/b` 兩個都 >= 1，少的那個至少佔合計的 10%。
  3 差異字不在排除清單（EXCLUDED，靠句子才分得出來的字）。
  4 不和產品的寫法慣例衝突：兩種寫法不是 data/lexicon/variant-order.tsv 裡的一對（少用寫法、常用寫法，順序不拘）。
    比契約嚴的地方：契約說「兩種寫法（同讀音）」，這裡只比兩個寫法、不比讀音，所以多擋的只有一種情形：同樣兩個寫法在寫法慣例表裡以另一個讀音出現
    （同形、讀音不同的詞對）；寫法不同的詞對不受影響。
  5 不是兩個各自成條的詞：兩種寫法都是辭典詞條，而且釋義（def 或 link，和條件 1 一樣）沒有任何一邊用「也作、亦作」連到對方的，不收。
  6 和現有的表合得起來：與 forms.tsv 的詞對聯集分組後，每行的標準形是它那組最小的字串、沒有標準形是另一行的寫法；每行的寫法與標準形等於自己套寬鬆對照之後的結果；寫法不重複。
條件 1、5 要辭典（SHANJIE_MOEDICT，SHA-256 照 tools/build_variants.py）；沒設就略過這兩條、印 `skipped: conditions 1, 5 (SHANJIE_MOEDICT not set)`、結束碼 0。
用法：python3 tools/check_forms_extra.py [--forms F] [--extra F] [--order F]；有違反結束碼 1。
"""
import argparse
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "reference", "proto"))
import eval as E  # noqa: E402
from build_forms import ALSO, readings_of, to_row_form  # noqa: E402
from build_variants import MOEDICT_SHA256  # noqa: E402

EXCLUDED = set("的地得在再做作他她它牠那哪嗎嘛吧罷象像向")
# 也作「Z」、亦作「Z」、通「Z」、同「Z」（「同」前面不是 如、等、相、不），或「Z」的異體字；Z 可以是以「、」分隔的幾個。
RELATION = re.compile(r"(?:也作|亦作|通|(?<![如等相不])同)「([^」]+)」|「([^」]+)」的異體字")
SKIPPED = "skipped: conditions 1, 5 (SHANJIE_MOEDICT not set)"


def load_entries(path):
    with open(path, "rb") as f:
        raw = f.read()
    if hashlib.sha256(raw).hexdigest() != MOEDICT_SHA256:
        sys.exit("moedict dump SHA-256 mismatch")
    return json.loads(raw)


def by_title(entries):
    out = {}
    for e in entries:
        out.setdefault(e.get("title", ""), []).append(e)
    return out


def _texts(d):
    link = d.get("link", [])
    return [d.get("def", "")] + ([link] if isinstance(link, str) else list(link))


def char_related(titles, x, y, syl):
    """條件 1 的一個位置：x 或 y 的條目有一個 heteronym，讀音含 syl，它的某條釋義（def 或 link）以關係詞連到另一個字。回傳依據的說明或 None。"""
    for a, b in ((x, y), (y, x)):
        for e in titles.get(a, []):
            for i, h in enumerate(e.get("heteronyms", [])):
                if syl not in {to_row_form(r) for r in readings_of(h)}:
                    continue
                for d in h.get("definitions", []):
                    for text in _texts(d):
                        for m in RELATION.finditer(text):
                            if b in (m.group(1) or m.group(2)).split("」、「"):
                                return f"{a} heteronym {i}: {m.group(0)}"
    return None


def word_linked(titles, a, b):
    """條件 5：a 或 b 的條目有一條釋義（def 或 link，和條件 1 一樣）以「也作、亦作」連到另一個詞。"""
    for s, t in ((a, b), (b, a)):
        for e in titles.get(s, []):
            for h in e.get("heteronyms", []):
                for d in h.get("definitions", []):
                    for text in _texts(d):
                        for m in ALSO.finditer(text):
                            if t in m.group(1).split("」、「"):
                                return True
    return False


def rows_of(text):
    """(行號, 欄位) 的清單，不含 # 開頭的說明行。"""
    return [(i, l.split("\t")) for i, l in enumerate(text.split("\n"), 1) if l and not l.startswith("#")]


def check(forms_text, extra_text, order_text, entries=None):
    """回傳 (違反清單, 略過的條件)；違反是 (行號, 條件, 說明)，格式與欄位的錯誤記成條件 0。entries 是辭典（None 就略過條件 1、5）。"""
    bad, skipped = [], [] if entries is not None else [1, 5]
    titles = by_title(entries) if entries is not None else None
    forms_words = {f[1]: f[2] for _, f in rows_of(forms_text) if f[0] == "word"}
    order = set()
    for _, f in rows_of(order_text):
        if len(f) >= 3:
            order.add(frozenset((f[1], f[2])))
    extra = []
    for ln, f in rows_of(extra_text):
        if len(f) != 6 or f[0] != "word" or not all(f[1:]) or len(f[1]) != len(f[2]) or f[1] == f[2]:
            bad.append((ln, 0, "not `word<TAB>spelling<TAB>canonical<TAB>reading<TAB>counts<TAB>basis` with two different spellings of one length"))
            continue
        extra.append((ln, f))
    spell_seen = set(forms_words)
    for ln, f in extra:
        _, a, b, reading, counts, basis = f
        diff = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        # 1
        if titles is not None:
            syls = reading.split()
            for i in diff:
                if len(syls) != len(a):
                    bad.append((ln, 1, "the reading has a different number of syllables than the spelling"))
                    break
                if char_related(titles, a[i], b[i], syls[i]) is None:
                    bad.append((ln, 1, f"no dictionary relation between {a[i]} and {b[i]} at reading {syls[i]}"))
        # 2
        m = re.fullmatch(r"(\d+)/(\d+)", counts)
        if not m or int(m[1]) < 1 or int(m[2]) < 1 or min(int(m[1]), int(m[2])) * 10 < int(m[1]) + int(m[2]):
            bad.append((ln, 2, f"counts {counts}: both need to be at least 1 and the smaller at least 10% of the sum"))
        # 3
        if any(a[i] in EXCLUDED or b[i] in EXCLUDED for i in diff):
            bad.append((ln, 3, "a differing character is on the exclusion list"))
        # 4
        if frozenset((a, b)) in order:
            bad.append((ln, 4, "the pair is in data/lexicon/variant-order.tsv"))
        # 5
        if titles is not None and a in titles and b in titles and not word_linked(titles, a, b):
            bad.append((ln, 5, "two dictionary entries that do not link to each other with 也作 or 亦作"))
        # 6: each side is its own lenient form, the spelling is new
        if E.lenient(a) != a or E.lenient(b) != b:
            bad.append((ln, 6, "the spelling or the canonical form changes under the lenient comparison"))
        if a in spell_seen:
            bad.append((ln, 6, "the spelling is already a spelling of forms.tsv or of an earlier line"))
        spell_seen.add(a)
    # 6: the union groups of forms.tsv and this table
    pairs = [(a, b, f"forms.tsv word {a}") for a, b in forms_words.items()] + [(f[1], f[2], f"line {ln}") for ln, f in extra]
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, _ in pairs:
        parent[find(a)] = find(b)
    groups = {}
    for w in list(parent):
        groups.setdefault(find(w), []).append(w)
    canon = {w: min(ws) for ws in groups.values() for w in ws}
    spellings = {a for a, _, _ in pairs}
    for ln, f in extra:
        a, b = f[1], f[2]
        if canon[a] != b:
            bad.append((ln, 6, f"the canonical form {b} is not the smallest string of its group ({canon[a]})"))
        if b in spellings:
            bad.append((ln, 6, f"the canonical form {b} is the spelling of another line (a chain)"))
    for a, b, src in pairs[:len(forms_words)]:
        if canon[a] != b:
            owner = next((ln for ln, f in extra if find(f[1]) == find(a)), 0)
            bad.append((owner, 6, f"joining with {src} makes its canonical form {b} no longer the smallest of the group"))
    return sorted(set(bad)), skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--forms", default=os.path.join(ROOT, "eval", "forms.tsv"))
    ap.add_argument("--extra", default=os.path.join(ROOT, "eval", "forms-extra.tsv"))
    ap.add_argument("--order", default=os.path.join(ROOT, "data", "lexicon", "variant-order.tsv"))
    a = ap.parse_args()
    rd = lambda p: open(p, encoding="utf-8").read()
    moedict = os.environ.get("SHANJIE_MOEDICT", "")
    bad, skipped = check(rd(a.forms), rd(a.extra), rd(a.order), load_entries(moedict) if moedict else None)
    for ln, cond, msg in bad:
        print(f"{os.path.basename(a.extra)} line {ln}: condition {cond}: {msg}")
    n = len(rows_of(rd(a.extra)))
    print(f"{n} lines checked, {len(bad)} violations")
    if skipped:
        print(SKIPPED)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
