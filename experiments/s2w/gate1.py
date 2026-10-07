"""S2w 契約 §4.1 第一關：兩種轉換跑維基前 N 篇，對齊、列差異、抽樣、量作用。不判讀任何一處（判讀由 main 填 review-100.tsv）。
  python experiments/s2w/gate1.py [--articles 2000]          # 跑全部，印出每個數字，寫 diff-top100.tsv、review-100.tsv、$S2_WORK/review-100-items.tsv
  python experiments/s2w/gate1.py --decide                   # 讀填好的 review-100.tsv，算雙尾精確符號檢定
需要有 zhconv-rs 的 venv 與 $S2_WORK/mwdata.json（先跑 mwdata.py）。
"""
import argparse
import collections
import difflib
import os
import random
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "experiments", "s2"))
import mwconv  # noqa: E402
import build_counts as bc  # noqa: E402

REVIEW = os.path.join(HERE, "review-100.tsv")
VERDICTS = ("MW 對", "S2n 對", "兩者都可", "都不對")
FIVE = "喫着爲説裏"
SEVEN = ["之后", "由于", "最后", "然后", "以后", "此后", "哪里"]
LEAK = ["-{", "}-", "zh-cn:", "zh-tw:", "zh-hans", "zh-hant"]
SAMPLE_SEED = 20261006
MULTILINE = re.compile(r"-\{.*?\}-", re.S)


def clean_sentences(t):
    for _ in range(3):
        t = bc.TEMPLATE.sub("", t)
    for pat, rep in bc.MARKUP:
        t = pat.sub(rep, t)
    return bc.SENT.findall(t)


def runs_of(sents, conv=None):
    return [r for s in sents for r in bc.HAN.findall(conv(s) if conv else s) if len(r) >= 2]


def align(A, B):
    """契約 §4.1：回傳 (相同段數, 不同的段 [(a, b)…], 對不上的 A 段數, 對不上的 B 段數)。"""
    same, pairs, ua, ub = 0, [], 0, 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, A, B, autojunk=False).get_opcodes():
        if tag == "equal":
            same += i2 - i1
        elif tag == "replace" and i2 - i1 == j2 - j1:
            for a, b in zip(A[i1:i2], B[j1:j2]):
                if a == b:
                    same += 1
                else:
                    pairs.append((a, b))
        else:
            ua += i2 - i1; ub += j2 - j1
    return same, pairs, ua, ub


def diffs_of(a, b):
    return [(a[i1:i2], b[j1:j2]) for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if tag != "equal"]


def count_sub(text, words):
    return {w: text.count(w) for w in words}


def sign_test(k1, k2):
    from math import comb
    n = k1 + k2
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(comb(n, i) for i in range(min(k1, k2) + 1)) / 2 ** n)


def decide():
    rows = [l.rstrip("\n").split("\t") for l in open(REVIEW, encoding="utf-8")][1:]
    bad = [r[0] for r in rows if len(r) < 4 or r[3] not in VERDICTS]
    if bad:
        sys.exit(f"review-100.tsv: {len(bad)} rows without a valid verdict {VERDICTS}, first ids {bad[:5]}")
    c = collections.Counter(r[3] for r in rows)
    mw, s2n = c["MW 對"], c["S2n 對"]
    p = sign_test(mw, s2n)
    print(f"rows {len(rows)}: {dict(c)}; two-sided exact sign test (MW 對 {mw} vs S2n 對 {s2n}) p = {p:.6f}")
    print("GATE 1 PASS" if mw > s2n and p < 0.05 else "GATE 1 FAIL")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--articles", type=int, default=2000)
    ap.add_argument("--decide", action="store_true")
    a = ap.parse_args()
    if a.decide:
        return decide()
    mw = mwconv.load()
    phrase, char, maxp = bc.load_conv()
    stop = []
    arts = list(bc.articles(a.articles))
    t_cpu = {"s2n": 0.0, "mw": 0.0, "nogroup": 0.0}
    t_wall = dict(t_cpu)
    R = {"s2n": [], "mw": [], "nogroup": []}
    leak_sents = tot_sents = multiline = 0
    art_with_g = gparams = gunres = 0
    for raw in arts:
        t = mwconv.unescape(raw)
        for k, f in (("s2n", lambda: runs_of(clean_sentences(t), lambda s: bc.convert(s, phrase, char, maxp))),
                     ("mw", lambda: runs_of(clean_sentences(mwconv.convert(t, mw)))),
                     ("nogroup", lambda: runs_of(clean_sentences(mwconv.convert(t, mw, False, False))))):
            c0, w0 = time.process_time(), time.perf_counter()
            R[k].append(f())
            t_cpu[k] += time.process_time() - c0; t_wall[k] += time.perf_counter() - w0
        sents = clean_sentences(mwconv.convert(t, mw))   # 規則外洩：轉換後、刪完標記的句子（不計時）
        tot_sents += len(sents)
        leak_sents += sum(any(x in s for x in LEAK) for s in sents)
        multiline += sum("\n" in m.group(0) for m in MULTILINE.finditer(t))
        gs, _ = mwconv.noteta_refs(t, mw)
        art_with_g += bool(gs); gparams += len(gs)
        gunres += sum(mwconv.group_rules(mw, g) is None for g in gs)

    # 對齊與差異處
    same = ndiff = ua = ub = 0
    diffs = []   # (篇序, 段序, s2n 詞, mw 詞, s2n 整段, mw 整段)
    for ai, (A, B) in enumerate(zip(R["s2n"], R["mw"])):
        s, pairs, x, y = align(A, B)
        same += s; ndiff += len(pairs); ua += x; ub += y
        for si, (p, q) in enumerate(pairs):
            diffs += [(ai, si, u, v, p, q) for u, v in diffs_of(p, q)]
    print(f"articles {len(arts)}; segments s2n {sum(map(len, R['s2n']))} mw {sum(map(len, R['mw']))}")
    print(f"align: same {same}, different {ndiff}, unmatched s2n {ua}, unmatched mw {ub}; difference spots {len(diffs)}")
    top = collections.Counter((d[2], d[3]) for d in diffs)
    top100 = sorted(top.items(), key=lambda kv: (-kv[1], kv[0]))[:100]
    with open(os.path.join(HERE, "diff-top100.tsv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("s2n\tmw\tcount\n" + "".join(f"{p}\t{q}\t{n}\n" for (p, q), n in top100))
    print("top 10 pairs:", top100[:10])

    # 抽樣 100 處
    sample = random.Random(SAMPLE_SEED).sample(diffs, min(100, len(diffs)))
    os.makedirs(mwconv.work_dir(), exist_ok=True)
    with open(os.path.join(mwconv.work_dir(), "review-100-items.tsv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("id\tarticle\tsegment\ts2n\tmw\ts2n_run\tmw_run\n" + "".join(f"{i}\t{d[0]}\t{d[1]}\t{d[2]}\t{d[3]}\t{d[4]}\t{d[5]}\n" for i, d in enumerate(sample, 1)))
    filled = os.path.exists(REVIEW) and any(len(l.split("\t")) > 3 and l.split("\t")[3].strip() for l in list(open(REVIEW, encoding="utf-8"))[1:])
    if filled:
        print("review-100.tsv already has verdicts; left untouched")
    else:
        with open(REVIEW, "w", encoding="utf-8", newline="\n") as f:
            f.write("id\ts2n\tmw\tverdict\treason\n" + "".join(f"{i}\t{d[2]}\t{d[3]}\t\t\n" for i, d in enumerate(sample, 1)))
    print(f"sample {len(sample)} items → {mwconv.work_dir()}/review-100-items.tsv, {REVIEW}")

    # 不加站上轉換表與群組規則（數字參數仍保留）對完整 --mw
    s0 = d0 = x0 = y0 = 0
    for A, B in zip(R["mw"], R["nogroup"]):
        s, pairs, x, y = align(A, B)
        s0 += s; d0 += len(pairs); x0 += x; y0 += y
    print(f"mw vs mw-without-groups-and-site: same {s0}, different {d0}, unmatched full {x0}, unmatched without {y0}; segments that differ {d0 + x0}")
    if d0 + x0 == 0:
        stop.append("with/without groups and site tables: 0 differences")

    # mwdata 抽查
    it = mw["groups"].get("IT")
    ok_it = it is not None and any("zh-cn:账号" in r and "zh-tw:帳號" in r for r in (x.replace(" ", "") for x in it))
    ok_site = dict(map(tuple, mw["site"])).get("通用电气") == "奇異"
    print(f"mwdata spot checks: group IT with 账号/帳號 {ok_it}; site 通用电气 => 奇異 {ok_site}")
    if not (ok_it and ok_site):
        stop.append("mwdata spot check failed")

    # 群組引用
    share = gunres / gparams if gparams else 0.0
    print(f"NoteTA group refs: articles {art_with_g}/{len(arts)}, G params {gparams}, unresolved {gunres} ({share:.2%})")
    if share > 0.05:
        stop.append("unresolved G params > 5%")

    # 五個字、七個殘留詞
    text = {k: "\n".join(r for rr in R[k] for r in rr) for k in ("s2n", "mw")}
    han = {k: sum(map(len, v.split("\n"))) for k, v in text.items()}
    for ch in FIVE:
        c = {k: text[k].count(ch) for k in text}
        pm = c["mw"] / han["mw"] * 1e6
        print(f"five chars {ch}: s2n {c['s2n']}, mw {c['mw']} ({pm:.2f} per million Han chars, Han chars mw {han['mw']})")
        if pm > 5:
            stop.append(f"{ch} > 5 per million")
    for k in text:
        c = count_sub(text[k], SEVEN)
        print(f"seven residue words [{k}]: {c} total {sum(c.values())}")

    # 速度、外洩、多行
    n = len(arts)
    for k in t_cpu:
        print(f"time [{k}]: wall {t_wall[k]:.1f}s, user CPU {t_cpu[k]:.1f}s")
    est = t_cpu["mw"] / n * 200_000 / 10 / 3600
    print(f"estimate 200k articles x 10 workers (this Mac's CPU, conversion + cleaning only, excluding counting): {est:.2f} h; mw/s2n CPU ratio {t_cpu['mw'] / max(t_cpu['s2n'], 1e-9):.2f}")
    print(f"rule leakage: {leak_sents}/{tot_sents} sentences ({leak_sents / max(tot_sents, 1):.4%})")
    if leak_sents / max(tot_sents, 1) > 0.001:
        stop.append("rule leakage > 0.1%")
    print(f"multi-line -{{…}}- rules in the {n} articles: {multiline}")
    print("STOP CONDITIONS HIT: " + "; ".join(stop) if stop else "no stop condition hit by the numbers above (the sign test is --decide)")


if __name__ == "__main__":
    main()
