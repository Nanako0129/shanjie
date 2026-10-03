"""S2 迭代機制：一個設定跑完全部評測，記一列到 log.tsv，印出調整集最常錯的字對。

角色：
- 調整集：dev302、typing76。可以看錯句，用來決定下一輪改什麼。
- 驗證集：Discord、新聞稿，句子只在本機私有檔，只看數字。調整集進步但驗證集退步，就判定過度擬合，不採用。
- 保留集：不在這裡跑，只有片結束的 fresh verifier 會量。

用法：python3 experiments/s2/iterate.py '<設定 JSON>' [--note 說明]
  設定欄位：lam（bigram 權重）、corpora（[[計數檔, 權重], ...]，路徑相對 ~/.cache/shanjie/work/s2）、
            D（discount，預設 0.75）、order（2 或 3）
"""
import argparse
import collections
import datetime
import glob
import json
import math
import os
import pickle
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "reference", "proto"))
import ime  # noqa: E402

WORK = os.path.expanduser("~/.cache/shanjie/work/s2")
PRIVATE = os.path.expanduser("~/side-project/shanjie-private")
LOG = os.path.join(HERE, "log.tsv")
LENIENT = str.maketrans("她妳它牠嘗周臺裏", "他你他他嚐週台裡")
_cache = {}


def load(name):
    if name not in _cache:
        _cache[name] = pickle.load(open(os.path.join(WORK, name), "rb"))
    return _cache[name]


class LM:
    """插值 absolute discounting；order 3 時再往前看一個詞（用 tri 計數，需要計數檔有 'tri'）。"""

    def __init__(self, cfg):
        self.lam, self.D, self.order = cfg["lam"], cfg.get("D", 0.75), cfg.get("order", 2)
        self.uni, self.bi, self.tri = collections.Counter(), collections.Counter(), collections.Counter()
        for name, w in cfg["corpora"]:
            c = load(name)
            for src, dst in [(c["uni"], self.uni), (c["bi"], self.bi), (c.get("tri", {}), self.tri)]:
                for k, v in src.items():
                    dst[k] += v * w
        self.N = sum(self.uni.values())
        self.ctx1, self.ctx2 = {}, {}
        for (v, w), c in self.bi.items():
            t, n = self.ctx1.get(v, (0, 0)); self.ctx1[v] = (t + c, n + 1)
        for (u, v, w), c in self.tri.items():
            t, n = self.ctx2.get((u, v), (0, 0)); self.ctx2[(u, v)] = (t + c, n + 1)
        self.memo = {}

    def p_bi(self, v, w, pu):
        t, n = self.ctx1.get(v, (0, 0))
        return (max(self.bi.get((v, w), 0) - self.D, 0) / t + self.D * n / t * pu) if t else pu

    def bonus(self, hist, word):
        cu = self.uni.get(word, 0)
        if not cu:
            return 0.0
        key = (hist[-2:] if self.order == 3 else hist[-1:], word)
        if key not in self.memo:
            pu = cu / self.N
            v = hist[-1]
            p = self.p_bi(v, word, pu)
            if self.order == 3 and len(hist) >= 2:
                t, n = self.ctx2.get((hist[-2], v), (0, 0))
                if t:
                    p = max(self.tri.get((hist[-2], v, word), 0) - self.D, 0) / t + self.D * n / t * p
            self.memo[key] = math.log10(p / pu)
        return self.lam * self.memo[key]


def decode(lex, syls, lm, beam=64):
    """原型 decode 的複本，差別只在加分看得到完整的詞歷史（trigram 需要）。"""
    syls = tuple(syls); n = len(syls)
    hyps = [[] for _ in range(n + 1)]; hyps[0] = [(0.0, ())]
    for i in range(1, n + 1):
        cand = {}
        for L in range(1, min(lex.max_len, i) + 1):
            key = syls[i - L:i]
            entries = lex.by_reading.get(key)
            if not entries or not hyps[i - L]:
                continue
            for word, lp in entries[:ime.PER_KEY]:
                for s, ws in hyps[i - L]:
                    sc = s + lp + lm.bonus(("<s>", "<s>") + ws, word)
                    surface = "".join(ws) + word
                    if surface not in cand or sc > cand[surface][0]:
                        cand[surface] = (sc, ws + (word,))
        hyps[i] = sorted(cand.values(), key=lambda x: -x[0])[:beam]
    return hyps[n]


def rows_of(paths, limit=None):
    out = []
    for f in paths:
        for line in open(f, encoding="utf-8"):
            p = line.rstrip("\n").split("|")
            if len(p) == 3:
                out.append((p[1], p[2].split()))
    return out[:limit] if limit else out


def sets():
    dev = sorted(glob.glob(os.path.join(ROOT, "eval", "dev", "*.txt")))
    s = {"dev302": rows_of([f for f in dev if not f.endswith("user-typing.txt")], 302),
         "typing76": rows_of([os.path.join(ROOT, "eval", "dev", "user-typing.txt")])}
    for name, f in [("discord", "discord-rows.txt"), ("news", "news/news-rows-300.txt")]:
        if os.path.exists(os.path.join(PRIVATE, f)):
            s[name] = rows_of([os.path.join(PRIVATE, f)])
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config"); ap.add_argument("--note", default="")
    a = ap.parse_args()
    cfg = json.loads(a.config)
    lex = ime.Lexicon(os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt"),
                      overlay=os.path.join(ROOT, "data", "lexicon", "overlay-add.tsv"))
    lm = LM(cfg)
    score, pairs = {}, collections.Counter()
    for name, rows in sets().items():
        ok = 0
        for truth, syls in rows:
            out = "".join(decode(lex, syls, lm)[0][1]).translate(LENIENT)
            t = truth.translate(LENIENT)
            ok += out == t
            if name in ("dev302", "typing76") and out != t and len(out) == len(t):
                pairs.update(f"{b}→{c}" for b, c in zip(t, out) if b != c)
        score[name] = (ok, len(rows))
    commit = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    cols = [datetime.datetime.now().isoformat(timespec="seconds"), commit, json.dumps(cfg, ensure_ascii=False)]
    cols += [f"{ok}/{n}" for ok, n in score.values()] + [a.note]
    new = not os.path.exists(LOG)
    with open(LOG, "a", encoding="utf-8") as f:
        if new:
            f.write("time\tcommit\tconfig\t" + "\t".join(score) + "\tnote\n")
        f.write("\t".join(cols) + "\n")
    print("  ".join(f"{k} {ok}/{n} ({ok / n:.1%})" for k, (ok, n) in score.items()))
    print("調整集最常錯：", "、".join(f"{k}×{v}" for k, v in pairs.most_common(15)))


if __name__ == "__main__":
    main()
