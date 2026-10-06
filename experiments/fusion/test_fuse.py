"""fuse.py 的單元檢查（契約 §5 驗收 1）。手造小列，不讀任何資料檔。  python3 test_fuse.py"""
import copy
import math
import random

import fuse


def row(half, margin, ng, js, ok):
    return {"half": half, "margin": margin, "ng": ng, "js": js, "ok": ok}


def test_jev_reverse_aligns_to_candidates():
    # 三個候選。正序：判斷器把 0.7 給 c1（= cands[0]）。
    # 反序題目裡 cands[::-1] = [cands[2], cands[1], cands[0]]：同一個意見要寫成 c3 = 0.7。
    fwd = {"probabilities": {"c1": 0.7, "c2": 0.2, "c3": 0.1}}
    rev = {"probabilities": {"c3": 0.7, "c2": 0.2, "c1": 0.1}}
    f, r = fuse.jev_probs(fwd, 3, False), fuse.jev_probs(rev, 3, True)
    assert f == [0.7, 0.2, 0.1], f
    assert r == [0.7, 0.2, 0.1], r  # 若照位置對齊會是 [0.1, 0.2, 0.7]
    s = fuse.avg_logp(f, r)
    assert fuse.pick(s) == 0 and abs(s[0] - math.log(0.7)) < 1e-12
    # 不對稱的情況：正序和反序各給不同的人 0.8，平均後各 0.4 + 其他
    f2 = fuse.jev_probs({"probabilities": {"c1": 0.8, "c2": 0.1, "c3": 0.1}}, 3, False)
    r2 = fuse.jev_probs({"probabilities": {"c1": 0.8, "c2": 0.1, "c3": 0.1}}, 3, True)  # 反序的 c1 是 cands[2]
    assert r2 == [0.1, 0.1, 0.8]
    assert fuse.avg_logp(f2, r2)[0] == fuse.avg_logp(f2, r2)[2]  # cands[0] 與 cands[2] 平手
    assert fuse.avg_logp([0.0, 1.0], [0.0, 1.0])[0] == math.log(fuse.FLOOR)  # 下限


def _toy():
    A = [row("A", m, ng, js, ok) for m, ng, js, ok in [
        (0.2, [-1.0, -1.2, -3.0], [-2.0, -0.5, -3.0], [0, 1, 0]),
        (0.4, [-1.0, -1.4, -2.0], [-0.5, -2.0, -3.0], [1, 0, 0]),
        (0.6, [-1.0, -1.6, -2.0], [-2.0, -0.5, -3.0], [0, 1, 0]),
        (0.8, [-1.0, -1.8, -2.5], [-1.0, -2.0, -0.5], [1, 0, 0]),
        (1.0, [-1.0, -2.0, -2.5], [-0.2, -2.0, -3.0], [1, 0, 0]),
        (1.2, [-1.0, -2.2, -2.5], [-2.0, -0.2, -3.0], [0, 1, 0]),
        (1.4, [-1.0, -2.4, -3.0], [-1.0, -0.5, -3.0], [1, 0, 0]),
        (1.6, [-1.0, -2.6, -3.0], [-1.0, -0.5, -3.0], [1, 0, 0]),
    ]]
    B = [row("B", m, ng, js, ok) for m, ng, js, ok in [
        (0.3, [-1.0, -1.3, -2.0], [-2.0, -0.5, -3.0], [0, 1, 0]),
        (0.9, [-1.0, -1.9, -2.0], [-0.5, -2.0, -3.0], [1, 0, 0]),
        (1.5, [-1.0, -2.5, -3.0], [-2.0, -0.5, -3.0], [1, 0, 0]),
    ]]
    return A + B


def test_params_only_from_A():
    rows = _toy()
    base = fuse.select_params(rows)
    assert base["tau"] != 0.0 and base["tau"] < math.inf  # 這組資料的最佳 τ 是有限格點，B 半改動才看得出格點被帶走
    changed = copy.deepcopy(rows)
    for r in changed:
        if r["half"] == "B":
            r["margin"] = 0.01 * r["margin"]  # 改動 B 半的 margin（把「全部列」的分位數格點往下拉）
            r["js"] = r["js"][::-1]
            r["ng"] = [x * 3 for x in r["ng"]]
            r["ok"] = r["ok"][::-1]
    changed += [row("B", 0.0, [-1.0, -2.0, -3.0], [-1.0, -2.0, -3.0], [1, 0, 0])] * 60  # 再加一批 B 半的列，把全部列的分位數壓到 0 附近
    assert fuse.select_params(changed) == base
    assert fuse.tau_grid([r for r in rows if r["half"] == "A"]) != fuse.tau_grid(rows)  # 格點確實對 B 半有感


def test_tau_grid_s5k_rule_and_ties():
    rows = [row("A", m, [0, -1], [0, -1], [1, 0]) for m in range(10)]
    assert fuse.tau_grid(rows) == [1, 2, 3, 4, 5, 6, 7, 8, 9, math.inf]
    assert fuse.pick([1.0, 1.0, 0.5]) == 0
    # 判斷器完全沒用：每個 τ 的淨值都是 0，取最小的格點
    flat = [row("A", 0.1 * i, [0, -1], [0, -1], [1, 0]) for i in range(10)]
    assert fuse.choose_tau(flat, fuse.tau_grid(flat)) == fuse.tau_grid(flat)[0]


def test_w_zero_equals_ngram_top():
    rng = random.Random(1)
    for _ in range(300):
        ng = sorted((rng.uniform(-30, 0) for _ in range(8)), reverse=True)
        r = row("A", ng[0] - ng[1], ng, [rng.uniform(-6, 0) for _ in range(8)], [0] * 8)
        assert fuse.pick4(r, 0) == 0


def test_arm5_equals_equivalent_tau_rule():
    # 只在 a > 1/8（推薦者的似然高過其他候選）時成立；a 更小時推薦者被扣分，等效 τ 為負、沒有「採用」這回事
    rng = random.Random(2)
    for _ in range(2000):
        ng = sorted((rng.uniform(-12, 0) for _ in range(8)), reverse=True)
        r = row("A", ng[0] - ng[1], ng, [rng.uniform(-6, 0) for _ in range(8)], [0] * 8)
        T, a = rng.choice(fuse.T_GRID), rng.choice([0.13, 0.3, 0.6, 0.9, 0.97])
        assert fuse.pick5(r, T, a) == fuse.pick5_rule(r, T, a), (T, a)
    assert abs(fuse.tau_eq(1, 0.5) - math.log(7)) < 1e-12


if __name__ == "__main__":
    for name, f in sorted(globals().items()):
        if name.startswith("test_"):
            f()
            print("ok", name)
