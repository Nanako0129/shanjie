"""SP（issue #44）：前文＋半截注音的離線預測研究。規格在 docs/contracts/sp-partial-zhuyin.md，章節以 §N 標示。

模型：候選 = 詞庫裡和（未完成的）注音按鍵序列相容的字串（§2），分數 = lm.word(λ, v, W, lp_max)（§1）。
只印統計數字，不寫檔、不印任何逐樣本內容（私有集合也用同一支）。
用法：python3 experiments/sp/predict.py --rows <檔> --set-name <名> --profile chat|formal [--sample N --seed S] [--check]
  評測檔格式 `前文|句子|讀音`（和 lm_eval.py 相同）。--check：一致性檢查 §4.2（任一樣本不符就停）。
"""
import argparse
import hashlib
import heapq
import math
import os
import random
import struct
import sys
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "reference", "proto"))
import ime  # noqa: E402
import lm as L  # noqa: E402

TONES = "ˊˇˋ˙"
TONE_KEYS = " " + TONES            # 空白鍵是一聲（§2）
INITIALS = set("ㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓㄔㄕㄖㄗㄘㄙ")
POSITIONS = ("P1", "P2", "P3", "P4", "A1", "A2")
ARMS = ("a", "b", "c")             # §3.3：詞庫分數／LM＋<s>／LM＋前文
SCAN_LIMIT = 9                     # §3.2 (i)


# ---------- §2 相容 ----------

def parse_syl(s):
    """'ㄋㄞˇ' -> (('ㄋ','ㄞ'), 'ˇ')；一聲沒有符號，tone = ''。沒有注音字元的音節回 None。"""
    tone = ""
    if s and s[-1] in TONES:
        s, tone = s[:-1], s[-1]
    elif s and s[0] == "˙":
        s, tone = s[1:], "˙"
    return (tuple(s), tone) if s else None


def units_of(keys):
    """按鍵序列 -> [(字元 tuple, 完成旗標, 聲調)]。聲調鍵把目前單位標成完成；之後的字元開新單位。"""
    out = []
    for k in keys:
        if k in TONE_KEYS:
            if out and not out[-1][1]:
                out[-1] = (out[-1][0], True, "" if k == " " else k)
        elif out and not out[-1][1]:
            out[-1] = (out[-1][0] + (k,), False, "")
        else:
            out.append(((k,), False, ""))
    return out


def unit_ok(u, s):
    c, done, t = u
    if not done:
        return s[0][:len(c)] == c           # 未完成：字元是前綴，不限聲調
    return s[0] == c and s[1] == t


def compat_prefix(units, syls):
    k = len(units)
    if not k or len(syls) < k or not all(u[1] for u in units[:-1]):
        return False
    return all(unit_ok(units[i], syls[i]) for i in range(k - 1)) and unit_ok(units[-1], syls[k - 1])


def compat_abbr(units, syls):
    if not units or any(u[1] for u in units) or len(syls) != len(units):
        return False
    return all(unit_ok(u, s) for u, s in zip(units, syls))


def compat(units, syls):
    return compat_prefix(units, syls) or compat_abbr(units, syls)


def dedupe(ms):
    """ms 依 (−lp, 音節數, 讀音, 詞) 排序，同字串第一個就是最高分（同分取讀音小的）。"""
    seen, out = set(), []
    for e in ms:
        if e[0] not in seen:
            seen.add(e[0])
            out.append(e)
    return out


class Index:
    """詞條 e = (詞, lp, 解析後的音節 tuple, 讀音字串)。依第一個音節字元的每個前綴分桶，桶內依 (−lp, 音節數, 讀音, 詞) 排序。"""

    def __init__(self, lex):
        self.lex = lex
        memo, ents, self.skipped = {}, [], 0
        for syls, entries in lex.by_reading.items():
            ps = []
            for s in syls:
                if s not in memo:
                    memo[s] = parse_syl(s)
                ps.append(memo[s])
            if None in ps:
                self.skipped += len(entries)
                continue
            r, ps = "-".join(syls), tuple(ps)
            ents += [(w, lp, ps, r) for w, lp in entries]
        ents.sort(key=lambda e: (-e[1], len(e[0]), e[3], e[0]))
        self.by_prefix, self.readings = defaultdict(list), defaultdict(list)
        for e in ents:
            c0 = e[2][0][0]
            for j in range(1, len(c0) + 1):
                self.by_prefix[c0[:j]].append(e)
            self.readings[e[0]].append((e[2], e[1]))

    def bucket(self, units):
        return self.by_prefix.get(units[0][0], ()) if units else ()

    def matches(self, units):
        return [e for e in self.bucket(units) if compat(units, e[2])]

    def candidates(self, units):
        return dedupe(self.matches(units))


def det_key(e, s):
    return (-s, len(e[0]), e[3], e[0])        # §1 同分的決定性順序


def top64(cands, sc):
    return heapq.nsmallest(64, range(len(cands)), key=lambda i: det_key(cands[i], sc[i]))


def det_top(cands, sc):
    m = max(sc)
    return min((e for e, s in zip(cands, sc) if s == m), key=lambda e: det_key(e, m))


def scores(cands, lm, lam, v):
    return [lm.word(lam, v, e[0], e[1]) for e in cands]


def pess_rank(cands, sc, word):
    """悲觀名次：同分的對手都算排在前面。"""
    i = next(i for i, e in enumerate(cands) if e[0] == word)
    return sum(1 for s in sc if s >= sc[i])


def succ_words(lm, v, _cache={}):
    key = (id(lm), v)
    if key not in _cache:
        c = lm.ctx.get(lm.ids.get(v, -1))
        _cache[key] = frozenset(lm.vocab[i] for i in c[2]) if c else frozenset()
    return _cache[key]


def scan_depth(idx, units, succ, limit=SCAN_LIMIT):
    """§3.2 (i)：從桶頭掃到 limit 個去重、相容、非後繼詞的字串。回 (掃過的條目數, 是否湊滿)。"""
    seen, depth = set(), 0
    for e in idx.by_prefix.get(units[0][0][:1], ()):
        depth += 1
        if e[0] not in seen and e[0] not in succ and compat(units, e[2]):
            seen.add(e[0])
            if len(seen) == limit:
                return depth, True
    return depth, False


# ---------- 一個樣本（一個詞界）----------

def key_sequence(sp):
    return "".join("".join(c) + (t or " ") for c, t in sp)


def position_units(sp):
    """語意位置 -> 單位序列（§3.2）。P1–P4 是前綴按鍵序列的第 t 鍵；A1、A2 是縮寫。"""
    n, c0 = len(sp), len(sp[0][0])
    ks = key_sequence(sp)
    pt = {"P1": 1, "P2": c0, "P3": c0 + 1}
    if n >= 2:
        pt["P4"] = c0 + 2
    pos = {p: units_of(ks[:t]) for p, t in pt.items()}
    pos["A1"] = [((c[0],), False, "") for c, _ in sp]
    pos["A2"] = [(c[:2], False, "") for c, _ in sp]
    return pos


def eval_sample(idx, lm, lam, W, syls, v, check=False):
    """回傳這個樣本的原始量測（不含內容）。單調性違反直接 raise。"""
    sp = tuple(parse_syl(s) for s in syls)
    ks, K = key_sequence(sp), sum(len(c) + 1 for c, _ in sp)
    assert len(ks) == K
    pos, n = position_units(sp), len(sp)
    first = {"P1": 1, "P2": len(sp[0][0]), "P3": len(sp[0][0]) + 1, "P4": len(sp[0][0]) + 2}
    pos_at = defaultdict(list)
    for p, t in first.items():
        if p in pos:
            pos_at[t].append(p)
    succ = succ_words(lm, v)
    rec = {"rank": {}, "fil": {}, "ks": {a: {} for a in ARMS}, "times": [], "depth": [], "flips": 0,
           "K": K, "n": n, "sp": sp}
    prev_set, prev_top = None, None
    for t in range(1, K + 1):
        units = units_of(ks[:t])
        t0 = time.perf_counter()
        cands = idx.candidates(units)
        sc_c = scores(cands, lm, lam, v)
        top64(cands, sc_c)
        dt = time.perf_counter() - t0
        words = {e[0] for e in cands}
        if W not in words:
            raise SystemExit(f"monotonicity: W* missing at key {t}")
        if prev_set is not None and not words <= prev_set:
            raise SystemExit(f"monotonicity: set grew at key {t}")
        prev_set = words
        sc_a = [e[1] for e in cands]
        sc_b = sc_c if v == "<s>" else scores(cands, lm, lam, "<s>")
        rk = {"a": pess_rank(cands, sc_a, W), "b": pess_rank(cands, sc_b, W), "c": pess_rank(cands, sc_c, W)}
        for a in ARMS:
            for k in (1, 3, 9):
                if rk[a] <= k and k not in rec["ks"][a]:
                    rec["ks"][a][k] = t
        top = det_top(cands, sc_c)[0]
        if prev_top is not None and top != prev_top:
            rec["flips"] += 1
        prev_top = top
        rec["depth"].append(scan_depth(idx, units, succ))
        for p in pos_at.get(t, ()):
            rec["rank"][p] = rk
            rec["times"].append(dt)
            rec["fil"][p] = pess_rank([e for e in cands if len(e[0]) >= 2],
                                      [s for e, s in zip(cands, sc_c) if len(e[0]) >= 2], W) if n >= 2 else None
            if p == "P3":
                rec["p3_top_multi"] = len(top) >= 2
        if check:
            check_query(idx, lm, lam, v, units, cands, sc_c)
            if t == K:
                full = {e[0] for e in cands if len(e[0]) == n}
                want = {w for w, _ in idx.lex.by_reading[tuple(syls)]}
                if full != want:
                    raise SystemExit("consistency (ii): full-reading set differs")
    for p in ("A1", "A2"):
        units = pos[p]
        t0 = time.perf_counter()
        cands = idx.candidates(units)
        sc_c = scores(cands, lm, lam, v)
        top64(cands, sc_c)
        rec["times"].append(time.perf_counter() - t0)
        if W not in {e[0] for e in cands}:
            raise SystemExit("abbreviation: W* missing")
        sc_a, sc_b = [e[1] for e in cands], scores(cands, lm, lam, "<s>")
        rec["rank"][p] = {"a": pess_rank(cands, sc_a, W), "b": pess_rank(cands, sc_b, W),
                          "c": pess_rank(cands, sc_c, W)}
        rec["fil"][p] = pess_rank([e for e in cands if len(e[0]) >= 2],
                                  [s for e, s in zip(cands, sc_c) if len(e[0]) >= 2], W) if n >= 2 else None
        rec["depth"].append(scan_depth(idx, units, succ))
    return rec


def check_query(idx, lm, lam, v, units, cands, sc):
    """§4.2 (i)：每個候選的分數 = lm.word(λ, v, W, lp_max)（逐位元組），lp_max 由詞庫條目獨立重算。"""
    for e, s in zip(cands, sc):
        lpm = max(lp for sp, lp in idx.readings[e[0]] if compat(units, sp))
        if struct.pack("<d", s) != struct.pack("<d", lm.word(lam, v, e[0], lpm)) or lpm != e[1]:
            raise SystemExit("consistency (i): score differs from lm.word(lp_max)")


# ---------- 資料 ----------

def load_rows(path, sample=None, seed=None):
    rows = []
    for line in open(path, encoding="utf-8"):
        p = line.rstrip("\n").split("|")
        rows.append((p[0], p[1], p[2].split()) if len(p) == 3 else None)
    if sample and sample < len(rows):
        keep = sorted(random.Random(seed).sample(range(len(rows)), sample))
        rows = [rows[i] for i in keep]
    return rows


def samples_of(lex, rows):
    """§3.1：每個詞界一個樣本 (前文, W*, 音節 tuple)；回 (樣本, 丟掉的計數)。"""
    out, drop = [], defaultdict(int)
    drop["rows"] = len(rows)
    for r in rows:
        if r is None or not r[2] or len(r[2]) != len(r[1]):
            drop["rows: no reading / length mismatch"] += 1
            continue
        ctx, sent, rd = r
        words = ime.segment_words(lex, sent)
        if words is None:
            drop["rows: segment_words None"] += 1
            continue
        pos = 0
        for j, (w, _) in enumerate(words):
            syls = tuple(rd[pos:pos + len(w)])
            c = ctx + "".join(x for x, _ in words[:j])
            pos += len(w)
            if not any(x == w for x, _ in lex.by_reading.get(syls, ())):
                drop["samples: W* has no lexicon entry for that reading"] += 1
                continue
            out.append((c, w, syls))
    return out, drop


def build_lm_lex():
    """和 reference/proto/lm_eval.py 第 49–52 行完全相同的詞庫與模型。"""
    path = os.path.join(ROOT, "data", "lm", "bigram.sjlm")
    want = open(os.path.join(ROOT, "data", "bigram.sjlm.sha256")).read().split()[0]
    assert hashlib.sha256(open(path, "rb").read()).hexdigest() == want, "bigram.sjlm hash mismatch"
    lm = L.BigramLM(path)
    base = ime.Lexicon(os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt"), overlay=ime.OVERLAYS)
    ov = {l.split("\t")[1] for l in open(os.path.join(ROOT, "data", "lexicon", "overlay-add.tsv"), encoding="utf-8")}
    return lm, L.cap_overlay(base, ov, lm)


# ---------- 統計 ----------

def mcnemar(b, c):
    """精確雙尾：b、c 是不一致對的兩種方向的次數。"""
    n = b + c
    if n == 0:
        return 1.0
    m = min(b, c)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(m + 1)) / 2 ** n)


def pct(a, b):
    return f"{100 * a / b:.1f}%" if b else "-"


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else float("nan")


def ks_of(rec, arm, k):
    i = rec["ks"][arm].get(k)
    return max(0, rec["K"] - i - 1) if i else 0


def report(name, profile, recs, ctxs, ws, vs, lm, drop, nlex_skipped):
    multi = [i for i, r in enumerate(recs) if r["n"] >= 2]
    single = [i for i, r in enumerate(recs) if r["n"] == 1]
    print(f"\n## {name}  {profile}")
    print(f"rows total={drop.pop('rows')}; dropped:", ", ".join(f"{k}={v}" for k, v in sorted(drop.items())) or "none",
          f"| samples kept={len(recs)} (>=2 chars {len(multi)}, 1 char {len(single)})")
    for label, ids, plist in (("W* >= 2 chars", multi, POSITIONS), ("W* 1 char", single, POSITIONS[:3])):
        print(f"\n### hit@k, {label} (n={len(ids)})")
        print("| pos | a@1 | a@5 | a@9 | b@1 | b@5 | b@9 | c@1 | c@5 | c@9 | filtered(>=2 syl) c@1/5/9 |")
        print("|---|---|---|---|---|---|---|---|---|---|---|")
        for p in plist:
            have = [recs[i] for i in ids if p in recs[i]["rank"]]
            row = [pct(sum(r["rank"][p][a] <= k for r in have), len(have)) for a in ARMS for k in (1, 5, 9)]
            fil = " / ".join(pct(sum(r["fil"][p] <= k for r in have), len(have)) for k in (1, 5, 9)) if label[3] == ">" else "-"
            print(f"| {p} (n={len(have)}) | " + " | ".join(row) + f" | {fil} |")
        print(f"\n### KS(k), {label}: mean KS / net keys saved per 100 words")
        print("| arm | KS(1) | KS(3) | KS(9) |")
        print("|---|---|---|---|")
        for a in ARMS:
            cells = []
            for k in (1, 3, 9):
                tot = sum(ks_of(recs[i], a, k) for i in ids)
                mean = sum(ks_of(recs[i], a, k) / recs[i]["K"] for i in ids) / len(ids) if ids else float("nan")
                cells.append(f"{100 * mean:.1f}% / {100 * tot / len(ids) if ids else float('nan'):.1f}")
            print(f"| {a} | " + " | ".join(cells) + " |")
    ctx_ids = [i for i in range(len(recs)) if ctxs[i]]
    cov = sum(ws[i] in succ_words(lm, vs[i]) for i in ctx_ids)
    cov2 = sum(ws[i] in succ_words(lm, vs[i]) for i in ctx_ids if recs[i]["n"] >= 2)
    n2 = sum(recs[i]["n"] >= 2 for i in ctx_ids)
    print(f"\nsuccessor coverage (non-empty context): all {cov}/{len(ctx_ids)} = {pct(cov, len(ctx_ids))}; "
          f">=2 chars {cov2}/{n2} = {pct(cov2, n2)}; samples whose v is <s> despite non-empty context: "
          f"{sum(vs[i] == '<s>' for i in ctx_ids)}")
    sn = [len(succ_words(lm, vs[i])) for i in ctx_ids]
    print(f"successor count of v (non-empty context): p50={q(sn, .5)} p95={q(sn, .95)} max={max(sn) if sn else '-'}")
    m3 = sum(recs[i]["p3_top_multi"] for i in single)
    print(f"single-char interference (ii): P3 top-1 is >=2 syllables: {m3}/{len(single)} = {pct(m3, len(single))}")
    noini = sum(any(s[0][0] not in INITIALS for s in sp[1:]) for sp in (recs[i]["sp"] for i in multi))
    print(f"abbreviation not typable (>=2 chars, some later syllable has no initial): {noini}/{len(multi)} = {pct(noini, len(multi))}")
    fl = sum(recs[i]["flips"] for i in multi)
    dn = sum(recs[i]["K"] - 1 for i in multi)
    print(f"top-1 flip rate (>=2 chars): {fl}/{dn} = {pct(fl, dn)}")
    dep = [d for r in recs for d, _ in r["depth"]]
    unf = sum(1 for r in recs for _, ok in r["depth"] if not ok)
    print(f"scan depth (all keys + A1/A2, {len(dep)} queries): p50={q(dep, .5)} p95={q(dep, .95)} max={max(dep)}; "
          f"bucket exhausted before 9: {unf}")
    fd = [d for r in recs for d, ok in r["depth"] if ok]
    print(f"scan depth, only queries that reached 9 ({len(fd)}): p50={q(fd, .5)} p95={q(fd, .95)} max={max(fd) if fd else '-'}")
    for p in ("P1", "P3"):
        pairs = [(recs[i]["rank"][p]["b"] <= 9, recs[i]["rank"][p]["c"] <= 9) for i in multi if ctxs[i]]
        b = sum(1 for x, y in pairs if y and not x)
        c = sum(1 for x, y in pairs if x and not y)
        print(f"McNemar {p} hit@9, c vs b (>=2 chars, non-empty context, n={len(pairs)}): "
              f"c-only={b} b-only={c} p={mcnemar(b, c):.4g}; hit c={sum(y for _, y in pairs)} b={sum(x for x, _ in pairs)}")
    ts = [x * 1000 for r in recs for x in r["times"]]
    print(f"query time (ms, {len(ts)} position queries, candidates+score+top64): "
          f"p50={q(ts, .5):.1f} p95={q(ts, .95):.1f} max={max(ts):.1f}")
    return q(ts, .95) > 2000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True)
    ap.add_argument("--set-name", required=True)
    ap.add_argument("--profile", choices=sorted(L.PROFILES), required=True)
    ap.add_argument("--sample", type=int)
    ap.add_argument("--seed", type=int, default=20261007)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    lm, lex = build_lm_lex()
    lam = L.PROFILES[a.profile]
    idx = Index(lex)
    samples, drop = samples_of(lex, load_rows(a.rows, a.sample, a.seed))
    recs, ctxs, ws, vs = [], [], [], []
    for c, w, syls in samples:
        v = L.history(L.context_key(c), lm)
        r = eval_sample(idx, lm, lam, w, syls, v, check=a.check)
        recs.append(r); ctxs.append(c); ws.append(w); vs.append(v)
    print(f"monotonicity: 0 violations in {len(recs)} samples" + (f"; consistency (i)(ii): 0 mismatches" if a.check else ""))
    if report(a.set_name, a.profile, recs, ctxs, ws, vs, lm, drop, idx.skipped):
        print("STOP: query p95 > 2 s")
        sys.exit(3)


if __name__ == "__main__":
    main()
