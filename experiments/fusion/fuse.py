"""融合實驗（離線），規格見 docs/contracts/fusion-offline.md。

只讀已存好的逐列結果，不跑模型、不連網。stdout 只有統計數字與固定字串，不印句子。

  python3 fuse.py --set cvtune dev302 typing76 [--judge Q8-ll Jev Laya]
  python3 fuse.py --repro --set discordtune       # 驗收 3：重現 S5k（私有集合要設 SHANJIE_PRIVATE）
  python3 fuse.py --repro --set cvtune --tau 0.6309

列（row）的內部表示：dict(ng=[n-gram 分數], js=[判斷器分數], margin, half, ok=[各候選對錯 0/1])。
選取函式都是純函式，test_fuse.py 直接餵手造的列。
"""
import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "s5-local"))
import s5k  # noqa: E402  雜湊表、路徑、load_rows（會核對 rows.jsonl 的 SHA-256）
from s5 import lenient_fn, mcnemar  # noqa: E402

W_GRID = [0, 0.02, 0.03, 0.05, 0.07, 0.1, 0.15, 0.2, 0.3, 0.5, 1, 2, 5]
T_GRID = [0.25, 0.5, 1, 2, 4]
FLOOR = 0.005  # Jev 兩位小數量化的半步；Laya 沿用同一個下限（契約 §2）
JUDGES = {"Q8-ll": ("discordtune", "cvtune", "dev302", "typing76"),
          "Jev": ("cvtune", "dev302", "typing76"),
          "Laya": ("discordtune", "cvtune", "dev302", "typing76")}


def die(msg):
    sys.exit(f"fuse: {msg}")


# ------------------------------------------------------------ 純函式（選取與參數）
def pick(s):
    """最大值，同分取名次較前（index 小）的。"""
    return max(range(len(s)), key=lambda i: (s[i], -i))


def avg_logp(p_fwd, p_rev):
    """p_fwd、p_rev 都已依候選（名次）順序排好；平均後取 log(max(p, 0.005))。"""
    return [math.log(max((a + b) / 2, FLOOR)) for a, b in zip(p_fwd, p_rev)]


def jev_probs(answer, n, rev):
    """Jev 一題的 c1..cn 機率 -> 依候選名次排好的機率。反序的 c_j 對到 cands[::-1][j-1]。"""
    pr = answer["probabilities"]
    if set(pr) != {f"c{j}" for j in range(1, n + 1)}:
        die("JUDGE SCORE COUNT")
    v = [pr[f"c{j}"] for j in range(1, n + 1)]
    return v[::-1] if rev else v


def tau_grid(rows):
    """S5k 的索引規則：q = 1..9 的分位數，再加無限大。"""
    ms = sorted(r["margin"] for r in rows)
    n = len(ms)
    return [ms[min(n - 1, int(n * q / 10))] for q in range(1, 10)] + [math.inf]


def pick3(r, tau):
    return pick(r["js"]) if r["margin"] < tau else 0


def pick4(r, w):
    return pick([a + w * b for a, b in zip(r["ng"], r["js"])])


def post5(r, T, a):
    n = len(r["ng"])
    j = pick(r["js"])
    m = max(r["ng"])
    pr = [math.exp((x - m) / T) for x in r["ng"]]
    z = sum(pr)
    post = [p / z * (a if i == j else (1 - a) / (n - 1)) for i, p in enumerate(pr)]
    z = sum(post)
    return [p / z for p in post]


def pick5(r, T, a):
    return pick(post5(r, T, a))


def tau_eq(T, a, n=8):
    """第 5 組等效的 τ：與推薦者的 n-gram 分差 < T·ln((n-1)a/(1-a)) 就採用推薦。"""
    return T * math.log((n - 1) * a / (1 - a))


def pick5_rule(r, T, a):
    """第 5 組的等效門檻寫法（測試用來和 pick5 對照）。"""
    j = pick(r["js"])
    top = pick(r["ng"])
    return j if j != top and r["ng"][top] - r["ng"][j] < tau_eq(T, a, len(r["ng"])) else top


def net(rows, f, base=lambda r: 0):
    return sum(r["ok"][f(r)] - r["ok"][base(r)] for r in rows)


def choose_tau(A, grid):
    """A 半上「修好 − 弄壞」最大；同分取較小的格點（和 S5k score.py 相同）。"""
    return max((net([r for r in A if r["margin"] < t], lambda r: pick(r["js"])), -i, t) for i, t in enumerate(grid))[2]


def choose_w(A):
    return max((sum(r["ok"][pick4(r, w)] for r in A), -i, w) for i, w in enumerate(W_GRID))[2]


def choose_T(A):
    """正解在候選內的列，正解先驗的對數機率總和最大。"""
    def ll(T):
        s = 0.0
        for r in A:
            if any(r["ok"]):
                m = max(r["ng"])
                z = sum(math.exp((x - m) / T) for x in r["ng"])
                s += (r["ng"][r["ok"].index(1)] - m) / T - math.log(z)
        return s
    return max((ll(T), -i, T) for i, T in enumerate(T_GRID))[2]


def choose_a(A):
    inn = [r for r in A if any(r["ok"])]
    a = sum(r["ok"][pick(r["js"])] for r in inn) / len(inn)
    # 契約沒寫 a = 0 或 1 時怎麼辦（小集合的 A 半會發生，例如 typing76 的 Jev）；log 會爆，所以拉進半個樣本的距離
    return min(max(a, 0.5 / len(inn)), 1 - 0.5 / len(inn))


def select_params(rows, grid_rows=None):
    """所有參數只從 A 半算出（契約 §2）。grid_rows：τ 格點用的列，預設也只用 A 半；
    只有重現 S5k 時才傳全部列（契約 §5 驗收 3）。"""
    A = [r for r in rows if r["half"] == "A"]
    tau = choose_tau(A, tau_grid(A if grid_rows is None else grid_rows))
    T, a = choose_T(A), choose_a(A)
    return {"tau": tau, "w": choose_w(A), "T": T, "a": a, "tau_eq": tau_eq(T, a)}


def arm_picks(r, p):
    return [0, pick(r["js"]), pick3(r, p["tau"]), pick4(r, p["w"]), pick5(r, p["T"], p["a"])]


# ------------------------------------------------------------ 載入與停止條件
def sdir(name):
    return (s5k.J_PRIV if name == "discordtune" else s5k.J_CACHE if name == "cvtune" else s5k.J_REPO) + "/" + name


def ldir(name):
    return (s5k.L_PRIV if name == "discordtune" else s5k.L_CACHE if name == "cvtune" else s5k.L_REPO) + "/" + name


def load_dump(name, rows):
    """cli.dump：列號（從 1 起）\\t名次\\t候選\\t分數 -> 每列依 cands 順序的 n-gram 分數。對不回就停。"""
    p = sdir(name) + "/cli.dump"
    if not os.path.isfile(p):
        die("INPUT MISSING cli.dump")
    d = {}
    for l in open(p, encoding="utf-8"):
        k, _rk, c, s = l.rstrip("\n").split("\t")
        d.setdefault(int(k) - 1, {})[c] = float(s)
    out = []
    for k, r in enumerate(rows):
        if k not in d or any(c not in d[k] for c in r["cands"]):
            die("CLI.DUMP CANDIDATES DO NOT MATCH rows.jsonl")
        out.append([d[k][c] for c in r["cands"]])
    return out


def read_jsonl(path):
    if not os.path.isfile(path):
        die("INPUT MISSING " + os.path.basename(path))
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def check_scores(by_k, rows, what):
    """每個有 ≥2 個候選的列都要有分數、個數等於候選數。"""
    out = []
    for k, r in enumerate(rows):
        s = by_k.get(k)
        if len(r["cands"]) < 2:
            out.append([0.0] * len(r["cands"]))
            continue
        if s is None or len(s) != len(r["cands"]):
            die(f"JUDGE ROWS INCOMPLETE {what}")
        out.append(s)
    return out


def load_q8(name, rows):
    def one(mode):
        p = ldir(name) + f"/Q8-ll.{mode}.jsonl"
        by = {}
        for r in read_jsonl(p):
            if r["k"] in by or r["scores"] is None:
                die("JUDGE ROWS INCOMPLETE Q8-ll")
            by[r["k"]] = r["scores"]
        return by
    by = one("noctx")
    if name == "discordtune":  # +ctxall：有前文的列用 ctx 檔，其餘用 noctx 檔
        by = {**by, **one("ctx")}
    return check_scores(by, rows, "Q8-ll"), None


def pair_avg(f, r, rows, what):
    return check_scores({k: avg_logp(f[k], r[k]) for k in f}, rows, what), flip_stats(f, r, rows)


def flip_stats(f, r, rows):
    ks = [k for k in f if k in r]
    fp = {k: pick(f[k]) for k in ks}
    rp = {k: pick(r[k]) for k in ks}
    ap = {k: pick(avg_logp(f[k], r[k])) for k in ks}
    n = len(ks)
    return {"n": n, "fwd_rev_differ": sum(fp[k] != rp[k] for k in ks) / n,
            "avg_differs_fwd": sum(ap[k] != fp[k] for k in ks) / n, "avg_differs_rev": sum(ap[k] != rp[k] for k in ks) / n}


def load_jev(name, rows):
    d = sdir(name)
    got = {}
    for fn, rev in (("jev-sent-fwd", False), ("jev-sent-rev", True)):
        by = {}
        recs = ([json.loads(l) for l in s5k.read_verified(name, f"{fn}.jsonl").splitlines() if l.strip()]
                if (name, f"{fn}.jsonl") in s5k.HASHES else read_jsonl(f"{d}/{fn}.jsonl"))   # S5k 有雜湊的就核對
        for rec in recs:
            for key, ans in zip(rec["keys"], rec["answers"]):
                k = int(key)
                if k in by or k >= len(rows):
                    die("JUDGE ROWS INCOMPLETE Jev")
                by[k] = jev_probs(ans, len(rows[k]["cands"]), rev)
        got[rev] = by
    if set(got[False]) != set(got[True]):
        die("JUDGE ROWS INCOMPLETE Jev")
    return pair_avg(got[False], got[True], rows, "Jev")


def load_laya(name, rows):
    got = {}
    for tag in ("fwd", "rev"):
        by = {}
        for r in read_jsonl(ldir(name) + f"/L-choice-{tag}.noctx.jsonl"):
            if r["k"] in by or r["scores"] is None:
                die("JUDGE ROWS INCOMPLETE Laya")
            by[r["k"]] = r["scores"]  # 已依名次順序（反序存檔時已翻回）
        got[tag] = by
    if set(got["fwd"]) != set(got["rev"]):
        die("JUDGE ROWS INCOMPLETE Laya")
    return pair_avg(got["fwd"], got["rev"], rows, "Laya")


LOADERS = {"Q8-ll": load_q8, "Jev": load_jev, "Laya": load_laya}


def build(name, judge, srows, ng):
    js, flip = LOADERS[judge](name, srows)
    L = lenient_fn()
    out = []
    for k, r in enumerate(srows):
        g = L(r["truth"])
        out.append({"ng": ng[k], "js": js[k], "margin": r["margin"], "half": r["half"], "ok": [int(L(c) == g) for c in r["cands"]]})
    return out, flip


# ------------------------------------------------------------ 報告
def acc(rows, f):
    return sum(r["ok"][f(r)] for r in rows) / len(rows)


def ece(items):
    """items = [(信心, 對錯)] -> (10 格 [(n, 平均信心, 正確率)], ECE)"""
    bins = [[] for _ in range(10)]
    for c, o in items:
        bins[min(9, int(c * 10))].append((c, o))
    rel = [(len(b), sum(c for c, _ in b) / len(b), sum(o for _, o in b) / len(b)) if b else (0, None, None) for b in bins]
    return rel, sum(n * abs(c - a) for n, c, a in rel if n) / len(items)


def f2(x):
    return "-" if x is None else f"{x:.3f}"


def report(name, judge, rows, flip):
    p = select_params(rows)
    print(f"== set={name} judge={judge} n={len(rows)} small_sample={name in s5k.PUBLIC}")
    print("params tau={:.4f} w={} T={} a={:.4f} tau_eq={:.4f}".format(p["tau"], p["w"], p["T"], p["a"], p["tau_eq"]))
    if flip:
        print("order_sensitivity n={n} fwd_rev_top_differ={fwd_rev_differ:.3f} avg_differs_fwd={avg_differs_fwd:.3f} avg_differs_rev={avg_differs_rev:.3f}".format(**flip))
    for h in "AB":
        H = [r for r in rows if r["half"] == h]
        pk = [arm_picks(r, p) for r in H]
        ok = [[r["ok"][i] for i in ps] for r, ps in zip(H, pk)]
        for a in range(5):
            line = f"half={h} arm={a + 1} n={len(H)} acc={acc(H, lambda r, a=a: arm_picks(r, p)[a]):.4f}"
            for ref in (0, 2):
                if a != ref:
                    fx, br, pv, _ = mcnemar([o[ref] for o in ok], [o[a] for o in ok])
                    line += f" vs{ref + 1}={fx}/{br}(p={pv:.4f})"
            print(line)
        if h == "B":
            miss = sum(1 for r in H if r["ok"][pick(r["js"])] and not r["ok"][0] and r["margin"] >= p["tau"])
            extra = sum(1 for r in H if not r["ok"][pick(r["js"])] and r["ok"][0] and r["margin"] < p["tau"])
            print(f"error_budget_B arm3_missed(judge right, top wrong, not adopted)={miss} arm3_extra(judge wrong, top right, adopted)={extra}")
            fx, br, pv, _ = mcnemar([o[2] for o in ok], [o[3] for o in ok])
            print(f"primary_form arm4_vs_arm3_B fixed={fx} broken={br} p={pv:.4f}" + ("  <-- PRIMARY TEST" if name == "discordtune" and judge == "Q8-ll" else ""))
            for lab, sub in (("in_cands", [r for r in H if any(r["ok"])]), ("all_rows", H)):
                rel, e = ece([(max(post5(r, p["T"], p["a"])), r["ok"][pick5(r, p["T"], p["a"])]) for r in sub])
                print(f"calibration_arm5_B {lab} n={len(sub)} ECE={e:.4f} bins(n,conf,acc)=" + " ".join(f"({n},{f2(c)},{f2(a)})" for n, c, a in rel))


def repro(name, judge, rows, tau_given):
    """S5k 的格點（全部列的 margin）、τ 選在 A 半；印第 1、2、3 組在全部列與 B 半的數字。"""
    if tau_given is None and name != "discordtune":   # S5k 的 τ 選在 discordtune；其他集合不給 τ 就沒有第 3 組可比
        die(f"--repro --set {name} needs --tau (S5k chose τ on discordtune half A)")
    p = select_params(rows, grid_rows=rows)
    tau = p["tau"] if tau_given is None else tau_given
    print(f"repro set={name} judge={judge} tau={tau:.4f} ({'given' if tau_given is not None else 'chosen on A, grid = all rows'})")
    got = {}
    for lab, sub in (("all", rows), ("B", [r for r in rows if r["half"] == "B"])):
        for arm, f in ((1, lambda r: 0), (2, lambda r: pick(r["js"])), (3, lambda r: pick3(r, tau))):
            fx, br, pv, _ = mcnemar([r["ok"][0] for r in sub], [r["ok"][f(r)] for r in sub])
            a = acc(sub, f)
            got[(lab, arm)] = (round(a * 100, 1), fx, br)
            print(f"repro {lab} arm={arm} n={len(sub)} acc={a * 100:.1f}% fixed/broken={fx}/{br} p={pv:.4f}")
    exp = EXPECT.get(name)
    if not exp:
        return True
    ok = True
    for key, want in exp["arms"].items():
        if key[1] == 3 and tau_given is None and name != "discordtune":
            continue
        if got[key][0] != want[0] or any(w is not None and g != w for g, w in zip(got[key][1:], want[1:])):
            ok = False
            print(f"REPRO MISMATCH {key} got={got[key]} want={want}")
    if name == "discordtune" and round(tau, 4) != 0.6309:
        ok = False
        print(f"REPRO MISMATCH tau got={tau:.4f} want=0.6309")
    print("REPRO OK" if ok else "REPRO MISMATCH")
    return ok


# S5k README 的數字（Q8-ll+ctxall 於 discordtune；cvtune 為 Q8-ll、τ 用 discordtune 的 0.6309，要傳 --tau）
EXPECT = {
    "discordtune": {"arms": {("all", 1): (83.5, None, None), ("B", 1): (84.4, None, None),
                             ("all", 2): (85.3, 89, 71), ("B", 2): (85.2, 40, 36),
                             ("all", 3): (88.3, 76, 28), ("B", 3): (87.6, 34, 18)}},
    "cvtune": {"arms": {("all", 1): (85.1, None, None), ("all", 2): (88.4, 77, 44), ("all", 3): (89.5, 60, 16)}},
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", nargs="+", required=True)
    ap.add_argument("--judge", nargs="+", default=None)
    ap.add_argument("--repro", action="store_true", help="重現 S5k 的第 1、2、3 組（只用 Q8-ll）")
    ap.add_argument("--tau", type=float, default=None, help="重現模式：指定 τ（cvtune 用 discordtune 的 0.6309）")
    a = ap.parse_args()
    status = True
    for name in a.set:
        if name not in s5k.NROWS:
            die("UNKNOWN SET")
        srows = s5k.load_rows(name)  # 核對 rows.jsonl 的 SHA-256
        ng = load_dump(name, srows)
        # 以 dump 分數核對 margin（只印出，不停）
        md = max(abs((x[0] - x[1]) - r["margin"]) for x, r in zip(ng, srows))
        print(f"check set={name} rows={len(srows)} dump_margin_maxdiff={md:.2e}")
        for judge in (["Q8-ll"] if a.repro else a.judge or [j for j, ss in JUDGES.items() if name in ss]):
            if name not in JUDGES[judge]:
                continue
            rows, flip = build(name, judge, srows, ng)
            print(f"check set={name} judge={judge} judge_rows_complete=yes dump_candidates_match=yes")
            if a.repro:
                status &= repro(name, judge, rows, a.tau)
            else:
                report(name, judge, rows, flip)
    if not status:
        sys.exit(1)


if __name__ == "__main__":
    main()
