"""SP 第三片（issue #44）：後繼詞優先、其餘相容詞補在後面。規格在 docs/contracts/sp3-successor-first.md（章節記 §N）；
相容、分數、母體照第二片（predict2.py，記 SP2），候選集合是第一片 c 組的集合（predict.py，記 SP1）。
只印統計數字，不寫檔、不印任何逐樣本內容。
用法：python3 experiments/sp/predict3.py --rows <檔> --set-name <名> --profile chat|formal [--sample N --seed S] [--mode P|PA|both] [--check]
"""
import argparse
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import predict as P1  # noqa: E402
import predict2 as P2  # noqa: E402
from predict import L, compat, compat_prefix, dedupe, det_key, mcnemar, pct, q, scores, succ_words  # noqa: E402

POSITIONS = P2.POSITIONS
POPS = P2.POPS


def reference(idx, units, mode):
    """c 組的候選（該模式的解讀；PA 是聯集＝SP1 的 c 組，P 只有前綴解讀）。"""
    ok = compat if mode == "PA" else compat_prefix
    return dedupe([e for e in idx.bucket(units) if ok(units, e[2])])


def order_s(cands, sc, succ):
    """§1 的 S：後繼詞一層、其餘一層，各層依決定性順序。回傳 (第一層 [(詞, 分數)], 第二層)。"""
    tiers = ([], [])
    for e, s in zip(cands, sc):
        tiers[0 if e[0] in succ else 1].append((e, s))
    return tuple(sorted(t, key=lambda x: det_key(x[0], x[1])) for t in tiers)


def rank_s(tiers, W):
    """S 的悲觀名次：同一層同分的對手算在前面；第二層加上第一層的個數。W 不在候選裡回 None。"""
    for i, tier in enumerate(tiers):
        for e, s in tier:
            if e[0] == W:
                return (len(tiers[0]) if i else 0) + sum(1 for _, x in tier if x >= s)
    return None


def rank_c(cands, sc, W):
    for e, s in zip(cands, sc):
        if e[0] == W:
            return sum(1 for x in sc if x >= s)
    return None


def top_of(tiers):
    for t in tiers:
        if t:
            return t[0][0][0]
    return None


def check_query(idx, lm, lam, v, units, mode, succ, tiers, cands):
    """§3.3：同一集合；第一層的字串集合與分數等於 SP2 的 candidates（第一層由 S 自己的路徑建出）。"""
    if {e[0] for t in tiers for e, _ in t} != {e[0] for e in cands}:
        raise SystemExit("consistency: S and c sets differ")
    ref = P2.candidates(idx, units, mode, succ)
    if {e[0] for e, _ in tiers[0]} != {e[0] for e in ref}:
        raise SystemExit("consistency: first tier differs from SP2")
    rs = dict(zip((e[0] for e in ref), scores(ref, lm, lam, v)))
    for e, s in tiers[0]:
        if struct.pack("<d", s) != struct.pack("<d", rs[e[0]]):
            raise SystemExit("consistency: first-tier score differs from SP2")


def eval_sample(idx, lm, lam, W, syls, v, modes, check=False):
    sp = tuple(P1.parse_syl(s) for s in syls)
    ks, K = P1.key_sequence(sp), sum(len(c) + 1 for c, _ in sp)
    pos = P1.position_units(sp)
    succ = succ_words(lm, v)
    rec = {"n": len(sp), "K": K, "pos": {}, "ks": {}, "flips": {}, "pairs": {}, "times": []}
    for m in modes:
        rec["pos"][m] = {}
        for p in POSITIONS[m]:
            if p not in pos:
                continue
            t0 = time.perf_counter()
            cands = reference(idx, pos[p], m)
            sc = scores(cands, lm, lam, v)
            tiers = order_s(cands, sc, succ)
            rec["times"].append(time.perf_counter() - t0)
            v3 = next((r for r in [rank_s((tiers[0], []), W)] if r is not None), None)
            rec["pos"][m][p] = (rank_s(tiers, W), rank_c(cands, sc, W), v3)
            if check:
                check_query(idx, lm, lam, v, pos[p], m, succ, tiers, cands)
    # P 模式沿前綴按鍵序列：KS 與第一名翻轉率，S 與 c 各自的排序
    first = {"S": {}, "c": {}}
    prev = {"S": None, "c": None}
    for arm in ("S", "c"):
        rec["flips"][arm] = rec["pairs"][arm] = 0
    for t in range(1, K + 1):
        units = P1.units_of(ks[:t])
        cands = reference(idx, units, "P")
        sc = scores(cands, lm, lam, v)
        tiers = order_s(cands, sc, succ)
        rk = {"S": rank_s(tiers, W), "c": rank_c(cands, sc, W)}
        tops = {"S": top_of(tiers), "c": P1.det_top(cands, sc)[0] if cands else None}
        for arm in ("S", "c"):
            for k in (1, 3, 9):
                if rk[arm] is not None and rk[arm] <= k and k not in first[arm]:
                    first[arm][k] = t
            if prev[arm] is not None and tops[arm] is not None:
                rec["pairs"][arm] += 1
                rec["flips"][arm] += tops[arm] != prev[arm]
            prev[arm] = tops[arm]
    rec["ks"] = {arm: {k: max(0, K - first[arm][k] - 1) if k in first[arm] else 0 for k in (1, 3, 9)} for arm in ("S", "c")}
    return rec


def hit(r, k):
    return r is not None and r <= k


def report(name, profile, modes, recs, pops, drop):
    print(f"\n## {name}  {profile}")
    print(f"rows total={drop.pop('rows')}; dropped:", ", ".join(f"{k}={v}" for k, v in sorted(drop.items())) or "none",
          f"| samples kept={len(recs)}; populations:", ", ".join(f"{g}={sum(x == g for x in pops)}" for g in POPS))
    for g in POPS:
        rs = [r for r, x in zip(recs, pops) if x == g]
        if not rs:
            continue
        for m in modes:
            print(f"\n### {g}  mode {m}  (n={len(rs)})")
            print("| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |")
            print("|---|---|---|---|---|---|---|---|---|")
            for p in POSITIONS[m]:
                have = [r["pos"][m][p] for r in rs if p in r["pos"][m]]
                if not have:
                    continue
                cells = [pct(sum(hit(x[0], k) for x in have), len(have)) for k in (1, 5, 9)]
                cells += [pct(sum(hit(x[1], k) for x in have), len(have)) for k in (1, 5, 9)]
                cells.append(pct(sum(hit(x[2], 9) for x in have), len(have)))
                print(f"| {p} | {len(have)} | " + " | ".join(cells) + " |")
        if g != "U":
            for arm in ("S", "c"):
                ks = " | ".join(f"KS({k}) {100 * sum(r['ks'][arm][k] / r['K'] for r in rs) / len(rs):.1f}% / {100 * sum(r['ks'][arm][k] for r in rs) / len(rs):.1f}"
                                for k in (1, 3, 9))
                fl, pr = sum(r["flips"][arm] for r in rs), sum(r["pairs"][arm] for r in rs)
                print(f"{g} {arm} (mode P): {ks} (mean KS / net keys saved per 100 words); top-1 flip rate {fl}/{pr} = {pct(fl, pr)}")
    m_recs = [r for r, x in zip(recs, pops) if x == "M"]
    tests = [("P", "P1", 0, 1, "S vs c"), ("P", "P3", 0, 1, "S vs c"), ("PA", "A1", 0, 1, "S vs c"), ("P", "P3", 0, 2, "S vs V3")]
    for m, p, a, b, lab in tests:
        if m not in modes:
            continue
        pairs = [(hit(r["pos"][m][p][a], 9), hit(r["pos"][m][p][b], 9)) for r in m_recs if p in r["pos"][m]]
        x = sum(1 for u, w in pairs if u and not w)
        y = sum(1 for u, w in pairs if w and not u)
        print(f"McNemar {m} {p} hit@9, {lab} (population M, n={len(pairs)}): first-only={x} second-only={y} p={mcnemar(x, y):.4g}; "
              f"hit first={sum(u for u, _ in pairs)} second={sum(w for _, w in pairs)}")
    ts = [t * 1000 for r in recs for t in r["times"]]
    print(f"query time (ms, {len(ts)} position queries, candidates+score+tiers; varies 20-30% between runs): "
          f"p50={q(ts, .5):.1f} p95={q(ts, .95):.1f} max={max(ts):.1f}")
    return q(ts, .95) > 2000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True)
    ap.add_argument("--set-name", required=True)
    ap.add_argument("--profile", choices=sorted(L.PROFILES), required=True)
    ap.add_argument("--sample", type=int)
    ap.add_argument("--seed", type=int, default=20261007)
    ap.add_argument("--mode", choices=("P", "PA", "both"), default="both")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    modes = ("P", "PA") if a.mode == "both" else (a.mode,)
    lm, lex = P1.build_lm_lex()
    lam = L.PROFILES[a.profile]
    idx = P1.Index(lex)
    samples, drop = P1.samples_of(lex, P1.load_rows(a.rows, a.sample, a.seed))
    recs, pops = [], []
    for c, w, syls in samples:
        v = L.history(L.context_key(c), lm)
        recs.append(eval_sample(idx, lm, lam, w, syls, v, modes, check=a.check))
        pops.append(P2.population(len(syls), v, c))
    print(f"samples {len(recs)}" + ("; consistency: 0 mismatches" if a.check else ""))
    if report(a.set_name, a.profile, modes, recs, pops, drop):
        print("STOP: query p95 > 2 s")
        sys.exit(3)


if __name__ == "__main__":
    main()
