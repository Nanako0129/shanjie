"""SP 第二片（issue #44）：候選只由前文歷史詞的後繼詞產生、已打的注音只過濾。規格在 docs/contracts/sp2-successor-prediction.md
（章節記 §N）；第一片的定義與函式照用（predict.py，記 SP1）。
只印統計數字，不寫檔、不印任何逐樣本內容（私有集合也用同一支）。
用法：python3 experiments/sp/predict2.py --rows <檔> --set-name <名> --profile chat|formal [--sample N --seed S] [--mode P|PA|both] [--check]
"""
import argparse
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import predict as P1  # noqa: E402  第一片
from predict import L, compat, compat_prefix, dedupe, det_top, pess_rank, q, pct, mcnemar, scores, succ_words, top64  # noqa: E402

POSITIONS = {"P": ("P1", "P2", "P3", "P4"), "PA": ("P1", "P2", "P3", "P4", "A1", "A2")}
POPS = ("M", "S0", "S1", "U")


def candidates(idx, units, mode, succ):
    """§1：C(v, z)。P 只用前綴解讀，PA 用聯集；bucket 已依 (−lp, 音節數, 讀音, 詞) 排序，dedupe 取到最高 lp。"""
    ok = compat if mode == "PA" else compat_prefix
    return [e for e in dedupe([e for e in idx.bucket(units) if ok(units, e[2])]) if e[0] in succ]


def population(n, v, ctx):
    """§2 的母體：每個樣本剛好一個。"""
    if n == 1:
        return "U"
    if v != "<s>":
        return "M"
    return "S1" if ctx else "S0"


def query(idx, lm, lam, v, units, mode, succ, W):
    """一次查詢的量測：(有顯示, W* 的悲觀名次或 None, |C|, 秒, 第一名)。"""
    t0 = time.perf_counter()
    cands = candidates(idx, units, mode, succ)
    sc = scores(cands, lm, lam, v)
    top64(cands, sc)
    dt = time.perf_counter() - t0
    rank = pess_rank(cands, sc, W) if any(e[0] == W for e in cands) else None
    return bool(cands), rank, len(cands), dt, (det_top(cands, sc)[0] if cands else None), cands, sc


def check_query(idx, lm, lam, v, units, mode, succ, cands, sc):
    """§3.3：C 等於「該模式的相容集合（PA 即 SP1 的 c 組集合）∩ succ(v)」，分數等於 lm.word(λ, v, W, 該模式相容讀音的最高 lp）。"""
    ok = compat if mode == "PA" else compat_prefix
    # 另一條路算：從後繼詞出發、看詞庫的全部讀音（不經過分桶與 dedupe）
    want = {w for w in succ if any(ok(units, sp) for sp, _ in idx.readings.get(w, ()))}
    if {e[0] for e in cands} != want:
        raise SystemExit("consistency: candidate set differs")
    ref = {}
    if mode == "PA":
        rc = idx.candidates(units)
        ref = dict(zip((x[0] for x in rc), scores(rc, lm, lam, v)))
    for e, s in zip(cands, sc):
        lpm = max(lp for sp, lp in idx.readings[e[0]] if ok(units, sp))
        if struct.pack("<d", s) != struct.pack("<d", lm.word(lam, v, e[0], lpm)):
            raise SystemExit("consistency: score differs")
        if ref and struct.pack("<d", ref[e[0]]) != struct.pack("<d", s):
            raise SystemExit("consistency: score differs from the reference arm")


def eval_sample(idx, lm, lam, W, syls, v, modes, check=False):
    sp = tuple(P1.parse_syl(s) for s in syls)
    ks, K = P1.key_sequence(sp), sum(len(c) + 1 for c, _ in sp)
    pos = P1.position_units(sp)
    c0 = len(sp[0][0])
    at = {"P1": 1, "P2": c0, "P3": c0 + 1, "P4": c0 + 2}
    succ = succ_words(lm, v)
    rec = {"n": len(sp), "K": K, "pos": {}, "ref": {}, "ks": {}, "flips": 0, "pairs": 0}
    for p in ("P1", "P3"):                    # 對照：SP1 的 c 組（聯集、全部相容字串）
        if p in pos:
            ref = idx.candidates(pos[p])
            rec["ref"][p] = pess_rank(ref, scores(ref, lm, lam, v), W)
    for m in modes:
        rec["pos"][m] = {}
        for p in POSITIONS[m]:
            if p in pos:
                shown, rank, size, dt, _, cands, sc = query(idx, lm, lam, v, pos[p], m, succ, W)
                rec["pos"][m][p] = (shown, rank, size, dt)
                if check:
                    check_query(idx, lm, lam, v, pos[p], m, succ, cands, sc)
    # P 模式沿前綴按鍵序列：單調性、KS、翻轉率（§2、§3.4）
    prev, prev_top, first = None, None, {}
    for t in range(1, K + 1):
        units = P1.units_of(ks[:t])
        shown, rank, size, dt, top, cands, sc = query(idx, lm, lam, v, units, "P", succ, W)
        words = {e[0] for e in cands}
        if prev is not None and not words <= prev:
            raise SystemExit(f"monotonicity: set grew at key {t}")
        prev = words
        for k in (1, 3, 9):
            if rank is not None and rank <= k and k not in first:
                first[k] = t
        if prev_top is not None and top is not None:
            rec["pairs"] += 1
            rec["flips"] += top != prev_top
        prev_top = top
        if check and ("P" not in modes or t not in at.values()):   # 位置上的 P 查詢只有 modes 含 P 時才在上面查過
            check_query(idx, lm, lam, v, units, "P", succ, cands, sc)
    rec["ks"] = {k: max(0, K - first[k] - 1) if k in first else 0 for k in (1, 3, 9)}
    return rec


def summarize(recs, mode, p):
    """§2 的每個位置指標（recs 是同一母體、在這個位置有定義的樣本）。"""
    have = [r["pos"][mode][p] for r in recs if p in r["pos"][mode]]
    n = len(have)
    shown = [x for x in have if x[0]]
    hit = {k: sum(1 for x in have if x[1] is not None and x[1] <= k) for k in (1, 5, 9)}
    empty = sum(1 for x in shown if x[1] is None)
    sizes = [x[2] for x in shown]
    return {"n": n, "shown": len(shown), "hit": hit, "empty": empty,
            "size": (q(sizes, .5), q(sizes, .95), max(sizes)) if sizes else None}


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
            print("| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |")
            print("|---|---|---|---|---|---|---|---|---|---|---|")
            for p in POSITIONS[m]:
                s = summarize(rs, m, p)
                if not s["n"]:
                    continue
                size = "/".join(str(x) for x in s["size"]) if s["size"] else "-"
                print(f"| {p} | {s['n']} | {pct(s['shown'], s['n'])} | " + " | ".join(pct(s["hit"][k], s["n"]) for k in (1, 5, 9))
                      + " | " + " | ".join(pct(s["hit"][k], s["shown"]) for k in (1, 5, 9)) + f" | {pct(s['empty'], s['shown'])} | {size} |")
        if g != "U":
            ks = " | ".join(f"KS({k}) {100 * sum(r['ks'][k] / r['K'] for r in rs) / len(rs):.1f}% / {100 * sum(r['ks'][k] for r in rs) / len(rs):.1f}"
                            for k in (1, 3, 9))
            fl, pr = sum(r["flips"] for r in rs), sum(r["pairs"] for r in rs)
            print(f"\n{g} mode P: {ks} (mean KS / net keys saved per 100 words); top-1 flip rate {fl}/{pr} = {pct(fl, pr)} "
                  f"(key pairs where both keys display)")
    m_recs = [r for r, x in zip(recs, pops) if x == "M"]
    for p in ("P1", "P3"):
        pairs = [((r["pos"]["P"][p][1] or 99) <= 9, r["ref"][p] <= 9) for r in m_recs if p in r["pos"].get("P", {})]
        b = sum(1 for x, y in pairs if x and not y)
        c = sum(1 for x, y in pairs if y and not x)
        if "P" in modes:
            print(f"McNemar {p} hit@9, V3-P vs reference arm c (population M, n={len(pairs)}): V3-only={b} c-only={c} "
                  f"p={mcnemar(b, c):.4g}; hit V3={sum(x for x, _ in pairs)} c={sum(y for _, y in pairs)}")
    ts = [x[3] * 1000 for r in recs for m in modes for x in r["pos"][m].values()]
    print(f"query time (ms, {len(ts)} position queries, C+score+top64; varies 20-30% between runs): "
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
        pops.append(population(len(syls), v, c))
    print(f"monotonicity (mode P): 0 violations in {len(recs)} samples" + ("; consistency: 0 mismatches" if a.check else ""))
    if report(a.set_name, a.profile, modes, recs, pops, drop):
        print("STOP: query p95 > 2 s")
        sys.exit(3)


if __name__ == "__main__":
    main()
