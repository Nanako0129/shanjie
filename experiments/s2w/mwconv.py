"""S2w：用 MediaWiki 的台灣正體轉換（zhconv-rs）轉一篇維基條目。契約 docs/contracts/s2w-mediawiki-zhtw.md §3.2。

只有 --mw 會 import 這個檔（需要 zhconv-rs 的 venv）；沒有 --mw 的路徑完全不經過這裡。
"""
import json
import os
import re

DEFAULT_WORK = os.path.expanduser("~/.cache/shanjie/work/s2w")


def work_dir():
    return os.environ.get("S2_WORK") or DEFAULT_WORK


def unescape(t):
    """和 build_counts.count_batch 相同的四個 HTML 實體（順序也相同）。"""
    return t.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"')


def norm_title(t):
    return re.sub(r"[ _]+", " ", t).strip()


def _name_pat(n):
    """MediaWiki 的模板名：只有第一個字母不分大小寫，空白與底線相同。"""
    n = norm_title(n)
    head = f"[{n[0].upper()}{n[0].lower()}]" if n[0].isalpha() else re.escape(n[0])
    return head + re.escape(n[1:]).replace(r"\ ", "[ _]+")


def load(path=None):
    """讀 mwdata.json，預先組好站上轉換表的規則字串與 NoteTA 的 regex。每個 worker 載一次。"""
    d = json.load(open(path or os.path.join(work_dir(), "mwdata.json"), encoding="utf-8"))
    d["_site"] = "".join(f"-{{H|{s}=>zh-tw:{t}}}-" for s, t in d["site"])
    d["_re"] = re.compile(r"\{\{\s*(?:" + "|".join(map(_name_pat, d["noteta"])) + r")\s*(\|.*?)?\}\}", re.S)
    return d


def group_rules(mw, name):
    g = mw["groups"]
    name = norm_title(name)
    for n in (name, name[:1].upper() + name[1:]):
        if n in g:
            return g[n]
    return None


_KEY = re.compile(r"^\s*(G?)(\d+)\s*=(.*)$", re.S)
_NAMED = re.compile(r"^\s*[A-Za-z][\w-]*\s*=(?!>)")


def params(body):
    """NoteTA 參數 → (群組名依 G 編號排序, 規則依編號排序)；G1–G30 與 1–30，其他參數略過。"""
    groups, rules, pos = {}, {}, 0
    for p in body.split("|"):
        m = _KEY.match(p)
        if m:
            kind, num, val = m.group(1), int(m.group(2)), m.group(3).strip()
        elif _NAMED.match(p) or not p.strip():
            continue
        else:
            pos += 1
            kind, num, val = "", pos, p.strip()
        if 1 <= num <= 30 and val:
            (groups if kind else rules)[num] = val
    return [groups[k] for k in sorted(groups)], [rules[k] for k in sorted(rules)]


def noteta_refs(text, mw):
    """條目裡所有 NoteTA（含別名）的 (群組名們, 規則們)。"""
    gs, rs = [], []
    for m in mw["_re"].finditer(text):
        g, r = params(m.group(1)[1:]) if m.group(1) else ([], [])
        gs += g
        rs += r
    return gs, rs


_COMMENT = re.compile(r"<!--.*?-->", re.S)


def _flat(rule):
    """一條規則：去掉 HTML 註解、合併空白，丟掉以 => 開頭（來源是空的）的段落。
    zhconv-rs 0.4.2 遇到這種段落會 panic（rule.rs:313 的 assert），MediaWiki 會略過；2026-10-06 在
    維基前 20 萬篇找到 4 篇，都是 NoteTA 數字參數裡的筆誤（「zh:珠穆朗瑪峰;=>zh-cn:…」）。"""
    rule = re.sub(r"\s+", " ", _COMMENT.sub("", rule)).strip()
    return ";".join(seg for seg in rule.split(";") if not seg.strip().startswith("=>"))


def prefix(text, mw, groups=True, site=True):
    """第 3 步：站上轉換表、群組（G 編號順序，同名只放一次）、數字參數；全部寫成 -{H|…}-。
    groups=False 只供 gate1 的對照（不加站上轉換表與群組規則，數字參數仍保留）。"""
    gs, rs = noteta_refs(text, mw)
    out = [mw["_site"]] if site else []
    if groups:
        for g in dict.fromkeys(gs):
            out += [f"-{{H|{_flat(r)}}}-" for r in group_rules(mw, g) or ()]
    out += [f"-{{H|{_flat(r)}}}-" for r in rs if "{" not in r and "}" not in r]
    return "".join(out)


_TW_FORMS = []


def tw_forms():
    """契約 §7：build_counts.VARIANTS（爲→為、裏→裡、説→說…）的轉換表，同一個常數、不另抄；不含 臺→台。"""
    if not _TW_FORMS:
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "s2"))
        import build_counts
        _TW_FORMS.append(str.maketrans(build_counts.VARIANTS))
    return _TW_FORMS[0]


def convert(text, mw, groups=True, site=True):
    """text 已還原 HTML 實體。回傳 zhconv-rs 轉完、再換成台灣字形（§7）的全文（尚未刪模板與標記）。"""
    import zhconv_rs
    try:
        out = zhconv_rs.zhconv(prefix(text, mw, groups, site) + text, "zh-tw", True)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        # pyo3 的 PanicException 不是 Exception：multiprocessing 的 worker 接不住，整批 200 篇默默消失、主程序最後卡住
        #（2026-10-06 在 188 上發生）。改成一般的錯誤，讓整批明確失敗。
        raise RuntimeError("zhconv-rs panicked on an article") from None
    return out.translate(tw_forms())
