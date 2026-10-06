"""S2w 契約 §3.1：從同一份 dump 一趟串流抽出公共轉換組、NoteTA 別名與站上轉換表，寫到 $S2_WORK/mwdata.json（預設 ~/.cache/shanjie/work/s2w/）。
用法：python3 experiments/s2w/mwdata.py [--dump PATH] [--limit-pages N]（--limit-pages 只給除錯）
mwdata.json：{"groups": {名: [規則…]}, "noteta": [模板名…], "site": [[來源, 目標]…]（zh-tw 優先，再補 zh-hant）, "stats": {…}}
"""
import argparse
import bz2
import collections
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mwconv  # noqa: E402

DUMP = os.path.expanduser("~/.cache/shanjie/sources/zhwiki-20261001-pages-articles.xml.bz2")
PAGE_NS = re.compile(r"<ns>(\d+)</ns>")
TITLE = re.compile(r"<title>(.*?)</title>")
TEXT = re.compile(r"<text[^>]*>(.*?)</text>", re.S)
REDIR = re.compile(r"^\s*#(?:REDIRECT|重定向)\s*:?\s*\[\[([^\]|#]+)", re.I)
LUA_STR = r"""(?:'((?:[^'\\\n]|\\.)*)'|"((?:[^"\\\n]|\\.)*)")"""
LUA_ITEM = re.compile(r"\bItem\s*\(\s*" + LUA_STR + r"\s*,\s*" + LUA_STR)    # 組 1,2＝原文；3,4＝規則
LUA_RULE = re.compile(r"\brule\s*=\s*" + LUA_STR)                              # { type = 'item', rule = '…' }；{ type = 'text', … } 沒有 rule
LUA_ALIAS = re.compile(r"^\s*return\s+require\s*\(?\s*['\"]Module:CGroup/([^'\"]+)['\"]", re.S)
CITEM = re.compile(r"\{\{\s*CItem\s*\|(.*?)\}\}", re.S)
G_PARAM = re.compile(r"\{\{\s*([^{}|]+?)\s*\|([^{}]*)\}\}")
G_KEY = re.compile(r"(?:^|\|)\s*G(\d+)\s*=\s*([^|}]*)")
PREFIX = {"Module:CGroup/": "M", "Template:CGroup/": "T", "模板:CGroup/": "T", "樣板:CGroup/": "T", "样板:CGroup/": "T"}
TPL_PREFIX = re.compile(r"^(?:Template|模板|樣板|样板):", re.I)


def lua_str(a, b):
    s = a if a is not None else b
    return re.sub(r"\\(.)", lambda m: {"n": " ", "t": " "}.get(m.group(1), m.group(1)), s)


def flat(s):
    return re.sub(r"\s+", " ", s).strip()


def module_rules(text):
    text = re.sub(r"--\[\[.*?\]\]", "", text, flags=re.S)
    text = "\n".join(l for l in text.split("\n") if not l.lstrip().startswith("--"))
    found = [(m.start(), flat(lua_str(m.group(3), m.group(4)))) for m in LUA_ITEM.finditer(text)]
    found += [(m.start(), flat(lua_str(m.group(1), m.group(2)))) for m in LUA_RULE.finditer(text)]
    return [r for r in dict.fromkeys(r for _, r in sorted(found)) if r]   # 照原文順序（同來源時後面的勝出，順序有意義）


def template_rules(text):
    out = [flat(m.group(1).split("|")[0]) for m in CITEM.finditer(text)]
    return [r for r in dict.fromkeys(out) if r and "{{" not in r]


def site_rules(text):
    """MediaWiki:Conversiontable：每行 `*來源=>目標;`；其他行（說明文字）略過。回傳 (條目, 因含 {}|; 而略過的行數)。"""
    out, skipped = [], 0
    for line in text.split("\n"):
        line = line.strip()
        if not line.startswith("*") or "=>" not in line:
            continue
        s, t = (x.strip() for x in line[1:].rstrip().rstrip(";").split("=>", 1))
        if s and t and not re.search(r"[{}|;]", s + t):
            out.append((s, t))
        else:
            skipped += 1
    return out, skipped


def scan(path, limit=None):
    pages = {"M": {}, "T": {}}          # 群組頁：名 → ("rules", […]) | ("alias", 名)
    tpl_redir, site_pages = {}, {}      # Template 重新導向：標題 → 目標；MediaWiki:Conversiontable/*：標題 → 文字或 ("redirect", 目標)
    refs = collections.Counter()        # (模板名, 群組名) → 條目數；之後依別名過濾
    gparams = collections.Counter()     # (模板名, 群組名) → G 參數個數
    n, buf = 0, []
    with bz2.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            buf.append(line)
            if "</page>" not in line:
                continue
            page, buf = "".join(buf), []
            n += 1
            if limit and n > limit:
                break
            mns = PAGE_NS.search(page)
            ns = int(mns.group(1)) if mns else -1
            title = mwconv.norm_title(mwconv.unescape(TITLE.search(page).group(1)))
            m = TEXT.search(page)
            text = mwconv.unescape(m.group(1)) if m else ""
            rd = REDIR.match(text)
            if ns == 0:
                if rd or "G1" not in text:
                    continue
                seen = set()
                for t in G_PARAM.finditer(text):
                    for g in G_KEY.finditer(t.group(2)):
                        key = (mwconv.norm_title(t.group(1)), mwconv.norm_title(g.group(2)))
                        gparams[key] += 1
                        if key not in seen:
                            seen.add(key); refs[key] += 1
                continue
            for pre, kind in PREFIX.items():
                if title.startswith(pre):
                    name = title[len(pre):]
                    if "/" in name:
                        break
                    if rd:
                        tgt = mwconv.norm_title(rd.group(1))
                        for p2, k2 in PREFIX.items():
                            if tgt.startswith(p2) and k2 == kind:
                                pages[kind][name] = ("alias", tgt[len(p2):])
                    elif kind == "M":
                        a = LUA_ALIAS.match(text)
                        pages[kind][name] = ("alias", mwconv.norm_title(a.group(1))) if a else ("rules", module_rules(text))
                    else:
                        pages[kind][name] = ("rules", template_rules(text))
                    break
            else:
                if ns == 10 and rd:
                    tpl_redir[title] = mwconv.norm_title(rd.group(1))
                elif ns == 8 and title.startswith("MediaWiki:Conversiontable/"):
                    site_pages[title] = ("redirect", mwconv.norm_title(rd.group(1))) if rd else text
    return pages, tpl_redir, site_pages, refs, gparams, n


def resolve(pages, kind, name, depth=0):
    p = pages[kind].get(name)
    if p is None or depth > 3:
        return None
    return p[1] if p[0] == "rules" else resolve(pages, kind, p[1], depth + 1)


def site_text(site_pages, title):
    for _ in range(4):
        p = site_pages.get(title)
        if not isinstance(p, tuple):
            return p
        title = p[1]
    return None


def up1(s):
    return s[:1].upper() + s[1:]


def is_noteta_alias(tpl_redir, t):
    for _ in range(3):
        t = tpl_redir.get(t)
        if t is None:
            return False
        if up1(TPL_PREFIX.sub("", t)) == "NoteTA":
            return True
    return False


def build(pages, tpl_redir, site_pages, refs, gparams, n):
    """回傳 (mwdata dict, unresolved Counter)。"""
    groups = {}
    for name in set(pages["M"]) | set(pages["T"]):
        r = resolve(pages, "M", name)       # Module 先，沒有才找 Template（Module:NoteTA 的順序）
        if r is None:
            r = resolve(pages, "T", name)
        if r is not None:
            groups[name] = r
    al = {"NoteTA"} | {up1(TPL_PREFIX.sub("", t)) for t in tpl_redir if is_noteta_alias(tpl_redir, t)}
    site, nskip, cnt = {}, 0, {}
    for variant in ("zh-tw", "zh-hant"):    # zh-tw 的條目優先，再補 zh-hant
        rules, sk = site_rules(site_text(site_pages, "MediaWiki:Conversiontable/" + variant) or "")
        cnt[variant] = len(rules); nskip += sk
        for s, t in rules:
            site.setdefault(s, t)
    unresolved, nref, nparam = collections.Counter(), 0, 0
    for (t, g), c in refs.items():
        if up1(t) in al:
            nref += c; nparam += gparams[(t, g)]
            if mwconv.group_rules({"groups": groups}, g) is None:
                unresolved[g] += c
    stats = {"pages": n, "groups": len(groups), "rules": sum(map(len, groups.values())), "empty_groups": sum(not v for v in groups.values()),
             "aliases": len(al), "site_zh_tw": cnt["zh-tw"], "site_zh_hant": cnt["zh-hant"], "site_merged": len(site), "site_skipped": nskip,
             "refs_article_group_pairs": nref, "g_params": nparam, "unresolved_groups": len(unresolved), "unresolved_articles": sum(unresolved.values())}
    return {"groups": groups, "noteta": sorted(al), "site": sorted(site.items()), "stats": stats}, unresolved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default=DUMP)
    ap.add_argument("--limit-pages", type=int, default=0)
    a = ap.parse_args()
    pages, tpl_redir, site_pages, refs, gparams, n = scan(a.dump, a.limit_pages)
    if not pages["M"] or site_text(site_pages, "MediaWiki:Conversiontable/zh-tw") is None:
        sys.exit(f"STOP: no Module:CGroup/* ({len(pages['M'])}) or no MediaWiki:Conversiontable/zh-tw in {n} pages")
    data, unresolved = build(pages, tpl_redir, site_pages, refs, gparams, n)
    out = os.path.join(mwconv.work_dir(), "mwdata.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(data, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps(data["stats"], ensure_ascii=False, indent=1))
    print("unresolved (group, articles) top 30:", unresolved.most_common(30))
    print("wrote", out)


if __name__ == "__main__":
    main()
