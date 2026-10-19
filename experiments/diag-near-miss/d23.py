#!/usr/bin/env python3
"""D2 confusion pairs + bias ceiling and D3 Wikipedia bias index (README of this directory), from one `--dump --dump-words --rowstats
--tinylm-scores` run of the shipping chat setting. Prints and writes counts only (character pairs allowed; no sentence, context or word of a row).

  python3 experiments/diag-near-miss/d23.py --name discordtune --rows ROWS --dump D --dump-words W --rowstats R --tinylm-scores S \
      [--lm-dir data/lm] --out OUT.json

Reuses tools/triage.py: row parsing, dump/words/scores reading, Lex, classify (buckets, C1-C5, FUNCTION_CHARS), diff_segments, pick.
Readings of the README that were ambiguous (chosen closest to the text):
  - "first place" of D3 = the finally chosen sentence (rowstats), the one whose difference segments triage computes.
  - Gold segmentation: the gold's own candidate path in the first 64 (triage's rule, because the difference segments are cut on it),
    not the dump-words rank-0 split; the number of C rows where the two differ is printed (`gold_path_not_gold0`, rank-0 `-` included).
  - D3 (ii): a word of data/lexicon/overlay-add.tsv or of the pack (not names-add.tsv, unlike triage's Lex.source) that is not in the
    base lexicon.
  - D3 (iii) corpus value = sum of log10(count) over the words, literally; a word with count 0 makes that side -inf (it can never be
    "higher"). A word with no lexicon entry under its reading makes the row not (iii); a (reading, word) in several files takes the highest
    score. (iii) compares the words overlapping the segment, so unequal word counts favour the side with more words: the (iii) rows whose
    two sides have the same number of words are printed apart (`iii_same_words`).
  - D3 "first place" is the final pick; `c_not_rank1` prints how many C rows have c != 0 (final pick not the n-gram rank 1).
  - Sets other than discordtune: verdict lines are labelled 只報.
  - C4 "equal length" (the README's example is 2 characters against 1 + 1): the chosen and the gold side of the segment hold the same
    number of words (dump-words split), and the two sentences have the same length (else no position map: all its segments count as
    unequal). Only positions where the characters differ become pairs.
  - Bias: a row is "affected" for (pair, b) only if the new top 8 (set and order) differs from the original top 8; a uniform shift
    changes nothing and is skipped. Bias counts only candidates whose surface length equals the syllable count (the position-to-reading map is unknown otherwise).
    A row whose scores record has no `ll` is decided by the biased n-gram first; a pair/b cell with > 10% needs-rescoring rows is
    "unreliable" and is left out of the D2 verdict (listed apart).
"""
import argparse, collections, json, math, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import triage as T  # noqa: E402
import bench  # noqa: E402

SHIP = (0.3, 2.0, 0.25)                       # alpha, tau, delta of the shipped chat profile
BS = (0.1, 0.2, 0.3, 0.5, 0.8, 1.0)
TOP_PAIRS, SHOW_PAIRS = 10, 30
CLASSES = ("i", "ii", "iii", "iv")


# ---------------------------------------------------------------- loading

def load_lex_scores():
    """({(reading tuple, word): score} of the files the engine loads (same filters as triage.Lex), words of overlay-add.tsv and the pack)."""
    r = lambda *p: T.read_text(os.path.join(ROOT, *p))
    out, ovl = {}, set()

    def add(reading, word, sc):
        k = (tuple(reading.split("-")), word)
        out[k] = max(out.get(k, -1e9), sc)
    for l in T.lines_of(r("data/lexicon/mcbpmf-data.txt")):
        f = l.split()
        if l[:1] in ("#", "_") or len(f) != 3 or len(f[1]) != len(f[0].split("-")):
            continue
        add(f[0], f[1], float(f[2]))
    for p in ("data/lexicon/overlay-add.tsv", "data/lexicon/sandhi-add.tsv", "data/packs/acg-add.tsv", "data/lexicon/names-add.tsv"):
        for l in T.lines_of(r(p)):
            f = l.split("\t")
            if len(f) == 4:
                add(f[0], f[1], float(f[2]))
                if p in ("data/lexicon/overlay-add.tsv", "data/packs/acg-add.tsv"):
                    ovl.add(f[1])
    return out, ovl


def build_rows(rows_file, dump, words, rowstats, scores, lenient, par=SHIP):
    """-> (rows as triage.classify wants them, ok list). Checks what triage.run_cell checks: the chosen candidate agrees with rowstats and pick."""
    rows = T.parse_rows(T.read_text(rows_file))
    n = len(rows)
    ok = bench.parse_rowstats(rowstats, n)
    cands, gold0, recs = T.read_cli_files(dump, words, scores, n)
    out = []
    for i, (ctx, truth, syls) in enumerate(rows):
        sc, c = recs[i], recs[i]["c"]
        if int(lenient(cands[i][c][0]) == lenient(truth)) != ok[i] or ("ll" in sc and sc["n"] > 1 and c != T.pick(sc["ng"], sc["ll"], *par)):
            raise RuntimeError(f"row {i + 1}: the chosen candidate disagrees with rowstats or with pick")
        out.append({"ctx": ctx, "truth": truth, "syls": syls, "cands": cands[i], "gold0": gold0[i], "c": c, "scores": sc})
    return out, ok


# ---------------------------------------------------------------- shared pieces

def gold_path(row, lenient):
    """The gold words triage.classify uses: the first rank of the first 64 that matches, else rank 0 (`gold0`), else single characters."""
    cands, t = row["cands"][:T.BEAM], lenient(row["truth"])
    rank = next((r for r, (s, _, _) in enumerate(cands, 1) if lenient(s) == t), None)
    if rank:
        return cands[rank - 1][2]
    return row["gold0"] if row["gold0"] is not None else list(row["truth"])


def spans(words):
    out, p = [], 0
    for w in words:
        out.append((p, p + len(w), w))
        p += len(w)
    return out


def words_in(words, segs):
    """[(start, end, word)] of the words overlapping a difference segment."""
    return [(a, b, w) for a, b, w in spans(words) if any(a < e and b > s for s, e in segs)]


# ---------------------------------------------------------------- D2

def c_rows(rows, ok, lex, lenient, par=SHIP):
    """The C rows: (row, classify result, difference segments, gold words, chosen surface, gold string)."""
    out = []
    for i, row in enumerate(rows):
        if ok[i]:
            continue
        r = T.classify(row, lex, par, lenient)
        if r["bucket"] != "C":
            continue
        gw = gold_path(row, lenient)
        sel = row["cands"][row["c"]][0]
        out.append((row, r, T.diff_segments(sel, gw, lenient), gw, sel, "".join(gw)))
    return out


def pair_tables(crows):
    c1 = collections.defaultdict(lambda: {"n": 0, "margin": dict.fromkeys(T.MARGIN_BANDS, 0)})
    c4, n_c1, c1_unpaired, c4_unequal = collections.Counter(), 0, 0, 0
    for row, r, segs, gw, sel, gold in crows:
        syls, same = row["syls"], len(sel) == len(gold)
        if r["sub"] == "C1":
            n_c1 += 1
            if not same or segs[0][1] > len(syls):
                c1_unpaired += 1
                continue
            s = segs[0][0]
            d = c1[(sel[s], gold[s], syls[s])]
            d["n"] += 1
            d["margin"][r["margin"]] += 1
        elif r["sub"] == "C4":
            if not same:
                c4_unequal += len(segs)   # sentences of different length: no position map
                continue
            for s, e in segs:
                if len(words_in(row["cands"][row["c"]][2], [(s, e)])) != len(words_in(gw, [(s, e)])):
                    c4_unequal += 1   # e.g. a 2-character word against 1 + 1: counted, not split
                    continue
                for p in range(s, e):
                    if sel[p] != gold[p] and p < len(syls):
                        c4[(sel[p], gold[p], syls[p])] += 1
    return c1, n_c1, c1_unpaired, c4, c4_unequal


def ranked(counts):
    """[(pair, n)] by count desc, pair as a tie-break (deterministic)."""
    return sorted(((k, v["n"] if isinstance(v, dict) else v) for k, v in counts.items()), key=lambda kv: (-kv[1], kv[0]))


def unreliable(rescoring, affected):
    return rescoring * 10 > affected      # strictly more than 10% of the affected rows


def bias_cells(rows, ok, pair, lenient, bs=BS, par=SHIP):
    """pair = (wrong, gold, reading). Every candidate of every row gets +b per position that reads `reading` and shows `gold`; top 8 by the
    new n-gram score, shipped fusion; fixes/breaks against rowstats over the whole set. -> {b: {...}}"""
    alpha, tau, delta = par
    _, g, rd = pair
    out = {b: {"fixes": 0, "breaks": 0, "rescoring": 0, "affected": 0} for b in bs}
    for i, row in enumerate(rows):
        syls, cands, sc = row["syls"], row["cands"][:T.BEAM], row["scores"]
        cn = [sum(1 for p, ch in enumerate(s) if ch == g and syls[p] == rd) if len(s) == len(syls) else 0 for s, _, _ in cands]
        if not any(cn):
            continue
        ng = [sc["ng"][j] if j < len(sc["ng"]) else cands[j][1] for j in range(len(cands))]
        ll, t = sc.get("ll"), lenient(row["truth"])
        orig = sorted(range(len(cands)), key=lambda j: (-ng[j], j))[:T.TOPK]
        for b in bs:
            o = out[b]
            new = [ng[j] + b * cn[j] for j in range(len(cands))]
            order = sorted(range(len(cands)), key=lambda j: (-new[j], j))[:T.TOPK]
            if order == orig:      # same top 8, same order (a uniform shift too): nothing can change
                continue
            o["affected"] += 1
            if ll is None:
                choice = order[0]
            elif any(j >= len(ll) for j in order):
                o["rescoring"] += 1
                continue
            else:
                choice = order[T.pick([new[j] for j in order], [ll[j] for j in order], alpha, tau, delta)]
            now = lenient(cands[choice][0]) == t
            o["fixes"] += (not ok[i]) and now
            o["breaks"] += ok[i] and not now
    for o in out.values():
        o["net"] = o["fixes"] - o["breaks"]
        o["unreliable"] = unreliable(o["rescoring"], o["affected"])
    return out


def d2_verdict(top10, n_c1, positive, positive_unreliable=False):
    if not (n_c1 > 0 and top10 * 5 >= n_c1 * 2):             # >= 40%
        return "D2: 前 10 對 < 40% 的 C1 → 單字同音整類只能靠語意模型，結案"
    if positive:
        return "D2: 前 10 對 ≥ 40% 的 C1，而且至少一對在某個 b 淨值 > 0 → 開「偏置表」小片"
    if positive_unreliable:
        return "D2: 前 10 對 ≥ 40% 的 C1，淨值 > 0 只出現在不可靠的格子 → 報告，維護者決定"
    return "D2: 前 10 對 ≥ 40% 的 C1 但淨值都 ≤ 0（兩個方向都錯）→ 只有看語境的窄分類器才有意義，另議"


def d2(rows, ok, crows, lenient):
    c1, n_c1, c1_unpaired, c4, c4_unequal = pair_tables(crows)
    top = ranked(c1)
    top10 = sum(n for _, n in top[:TOP_PAIRS])
    cells = {k: bias_cells(rows, ok, k, lenient) for k, _ in top[:TOP_PAIRS]}
    good = [(k, b) for k, bc in cells.items() for b, o in bc.items() if o["net"] > 0 and not o["unreliable"]]
    odd = [(k, b) for k, bc in cells.items() for b, o in bc.items() if o["net"] > 0 and o["unreliable"]]
    verdict = d2_verdict(top10, n_c1, bool(good), bool(odd))
    return {"c1": n_c1, "c1_unpaired": c1_unpaired, "top10": top10, "top10_share": top10 / n_c1 if n_c1 else 0.0,
            "pairs": [{"pair": list(k), "n": n, "margin": c1[k]["margin"]} for k, n in top[:SHOW_PAIRS]],
            "bias": [{"pair": list(k), "n": c1[k]["n"], "cells": {str(b): o for b, o in bc.items()}} for k, bc in cells.items()],
            "c4_pairs": [{"pair": list(k), "n": n} for k, n in ranked(c4)[:SHOW_PAIRS]], "c4_unequal_segments": c4_unequal,
            "positive": [[list(k), b] for k, b in good], "positive_unreliable": [[list(k), b] for k, b in odd], "verdict": verdict}


# ---------------------------------------------------------------- D3

def d3_class(row, r, segs, gw, lex, ovl, count, lexscore):
    """-> ("i" | "ii" | "iii" | "iv", (iii) could not be scored, both sides have the same number of words in the segment)."""
    if r["func_any"]:
        return "i", False, False
    sel_w, gold_w, syls = words_in(row["cands"][row["c"]][2], segs), words_in(gw, segs), row["syls"]
    if any(w in ovl and w not in lex.base for _, _, w in sel_w):
        return "ii", False, False
    corpus = lambda ws: sum(math.log10(count(w)) if count(w) > 0 else -math.inf for _, _, w in ws)
    same = len(sel_w) == len(gold_w)
    try:
        prior = lambda ws: sum(lexscore[(tuple(syls[a:b]), w)] for a, b, w in ws)
        return ("iii" if corpus(sel_w) > corpus(gold_w) and prior(sel_w) < prior(gold_w) else "iv"), False, same
    except KeyError:
        return "iv", True, same


def d3_verdict(hit, n):
    if n and hit * 5 >= n:
        return "D3: (ii)＋(iii) ≥ 20% → n-gram 資料側有真實目標，開 P4（口語計數表當獨立項、聊天設定對疊加層詞扣分）的探索片"
    if not n or hit * 10 < n:
        return "D3: (ii)＋(iii) < 10% → n-gram 資料側結案"
    return "D3: (ii)＋(iii) 在 10～20% → 報告，維護者決定"


def d3(crows, lex, ovl, count, lexscore):
    tot = dict.fromkeys(CLASSES, 0)
    by_sub = {s: dict.fromkeys(CLASSES, 0) for s in T.C_SUBS}
    unscored = same_iii = not_rank1 = gold_differs = 0
    for row, r, segs, gw, _, _ in crows:
        k, u, same = d3_class(row, r, segs, gw, lex, ovl, count, lexscore)
        tot[k] += 1
        by_sub[r["sub"]][k] += 1
        unscored += u
        same_iii += k == "iii" and same
        not_rank1 += row["c"] != 0
        gold_differs += row["gold0"] is None or gw != row["gold0"]
    n, hit = len(crows), tot["ii"] + tot["iii"]
    return {"c_rows": n, "classes": tot, "share_ii_iii": hit / n if n else 0.0, "by_sub": by_sub, "iii_unscored": unscored, "iii_same_words": same_iii,
            "c_not_rank1": not_rank1, "gold_path_not_gold0": gold_differs, "verdict": d3_verdict(hit, n)}


# ---------------------------------------------------------------- output

def render(name, n, res):
    d, e = res["d2"], res["d3"]
    L = [f"## {name} (n = {n})", f"\n### D2: C1 rows {d['c1']} (not paired: {d['c1_unpaired']}); top-10 pairs {d['top10']} = {100 * d['top10_share']:.1f}% of C1",
         "\n| wrong → gold | reading | rows | " + " | ".join(T.MARGIN_BANDS) + " |\n|---|---|---|" + "---|" * len(T.MARGIN_BANDS)]
    L += [f"| {p['pair'][0]} → {p['pair'][1]} | {p['pair'][2]} | {p['n']} | " + " | ".join(str(p["margin"][m]) for m in T.MARGIN_BANDS) + " |" for p in d["pairs"]]
    L += ["\n| pair | reading | b | fixes | breaks | net | needs rescoring | affected | |\n|---|---|---|---|---|---|---|---|---|"]
    for x in d["bias"]:
        for b, o in x["cells"].items():
            L.append(f"| {x['pair'][0]} → {x['pair'][1]} | {x['pair'][2]} | {b} | {o['fixes']} | {o['breaks']} | {o['net']:+d} | {o['rescoring']} | {o['affected']} | {'unreliable' if o['unreliable'] else ''} |")
    L += [f"\nC4 equal-length pairs (top {SHOW_PAIRS}); unequal-length C4 segments: {d['c4_unequal_segments']}", "\n| wrong → gold | reading | count |\n|---|---|---|"]
    L += [f"| {p['pair'][0]} → {p['pair'][1]} | {p['pair'][2]} | {p['n']} |" for p in d["c4_pairs"]]
    L += ["\n" + d["verdict"], f"unreliable cells with net > 0 (left out of the verdict): {d['positive_unreliable']}",
          f"\n### D3: C rows {e['c_rows']}; (ii)+(iii) = {100 * e['share_ii_iii']:.1f}%; (iii) not scorable: {e['iii_unscored']}; (iii) with equal word counts: {e['iii_same_words']}; "
          f"C rows with c != 0: {e['c_not_rank1']}; gold path differs from rank 0: {e['gold_path_not_gold0']}",
          "\n| set | " + " | ".join(CLASSES) + " |\n|---|---|---|---|---|", f"| all C | " + " | ".join(str(e["classes"][k]) for k in CLASSES) + " |"]
    L += [f"| {s} | " + " | ".join(str(e["by_sub"][s][k]) for k in CLASSES) + " |" for s in T.C_SUBS]
    return "\n".join(L + ["\n" + e["verdict"], ""])


def analyze(name, rows, ok, lex, lenient, count, lexscore, ovl):
    crows = c_rows(rows, ok, lex, lenient)
    res = {"name": name, "n": len(rows), "errors": len(rows) - sum(ok), "setting": list(SHIP), "d2": d2(rows, ok, crows, lenient),
           "d3": d3(crows, lex, ovl, count, lexscore)}
    if name != "discordtune":          # only discordtune decides; the other sets are reported
        for k in ("d2", "d3"):
            res[k]["verdict"] = "只報：" + res[k]["verdict"]
    return res


def main(argv=None):
    a = argparse.ArgumentParser()
    for k in ("name", "rows", "dump", "dump-words", "rowstats", "tinylm-scores", "out"):
        a.add_argument("--" + k, required=True)
    a.add_argument("--lm-dir", default=os.path.join(ROOT, "data/lm"))
    a = a.parse_args(argv)
    sys.path.insert(0, os.path.join(ROOT, "reference", "proto"))
    import lm as protolm
    count = protolm.BigramLM(os.path.join(a.lm_dir, "bigram.sjlm"), classes=False).count
    lenient = T.load_lenient()
    rows, ok = build_rows(a.rows, a.dump, getattr(a, "dump_words"), a.rowstats, getattr(a, "tinylm_scores"), lenient)
    res = analyze(a.name, rows, ok, T.Lex.load(), lenient, count, *load_lex_scores())
    print(render(a.name, len(rows), res))
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
