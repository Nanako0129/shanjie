"""S2 迭代機制第二版（採納 experiments/s2/review-fable.md）。

和第一版的差別：
- 逐句紀錄：每列寫下 ok、正解名次、和第一名的分數差、對數損失。公開集合寫到 ~/.cache/shanjie/work/s2/runs/<run>/，
  私有集合寫到 ~/side-project/shanjie-private/s2-runs/<run>/。兩次 run 之間用 compare.py 做配對比較。
- 連續指標：64 名候選的分數取 softmax，記正解的對數損失；正解不在候選裡記為上限 CAP。
- 模型：model="bonus"（第一版的加分）或 "interp"（統一先驗：λ·log10 P(w|v) + (1−λ)·lp，
  其中 P 的 backoff 分布用詞庫機率 10^lp，未見詞一樣吃 backoff 權重）；eos=true 時句尾加 λ·log10 P(</s>|最後一詞)；
  floor 是 bonus 模型的下限。
- 一次可以跑多組設定（JSON 陣列），共用已載入的計數；分開計時載入和解碼。

集合：調整集 cvtune、wikitune（不參與計數的真人句，見 build_tune.py）；挑戰集 dev302、typing76（按類別看，不當門檻）；
驗證集 discord、news（私有，只看數字；每累積幾個候選才看一次）。保留集不在這裡。
用法：python3 experiments/s2/iter2.py '<設定 JSON 或陣列>' [--sets cvtune,wikitune,dev302,typing76] [--note …]
"""
import argparse
import collections
import datetime
import glob
import hashlib
import json
import math
import os
import pickle
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "reference", "proto"))
import ime  # noqa: E402

WORK = os.path.expanduser("~/.cache/shanjie/work/s2")
PRIVATE = os.path.expanduser("~/side-project/shanjie-private")
LOG = os.path.join(HERE, "log2.tsv")
LENIENT = str.maketrans("她妳它牠嘗周臺裏", "他你他他嚐週台裡")
CAP = 10.0
LN10 = math.log(10)
_counts = {}


def counts(name):
    if name not in _counts:
        _counts[name] = pickle.load(open(os.path.join(WORK, name), "rb"))
    return _counts[name]


def merged(corpora):
    uni, bi = collections.Counter(), collections.Counter()
    for name, w in corpora:
        c = counts(name)
        for k, v in c["uni"].items():
            uni[k] += v * w
        for k, v in c["bi"].items():
            bi[k] += v * w
    return uni, bi


class LM:
    def __init__(self, cfg, uni, bi):
        self.cfg, self.uni, self.bi = cfg, uni, bi
        self.lam, self.D = cfg.get("lam", 0.5), cfg.get("D", 0.75)
        self.model, self.eos_on, self.floor = cfg.get("model", "bonus"), cfg.get("eos", False), cfg.get("floor")
        self.N = sum(uni.values())
        self.ctx = {}
        eos_total = 0
        for (v, w), c in bi.items():
            t, n = self.ctx.get(v, (0, 0)); self.ctx[v] = (t + c, n + 1)
            if w == "</s>":
                eos_total += c
        self.p_eos = max(eos_total, 1) / self.N
        self.memo = {}

    def _p(self, v, w, pb):
        t, n = self.ctx.get(v, (0, 0))
        return (max(self.bi.get((v, w), 0) - self.D, 0) / t + self.D * n / t * pb) if t else pb

    def word(self, hist, word, lp):
        key = (hist[-1], word, lp)
        if key in self.memo:
            return self.memo[key]
        if self.model == "interp":
            sc = self.lam * math.log10(self._p(hist[-1], word, 10 ** lp)) + (1 - self.lam) * lp
        else:
            cu = self.uni.get(word, 0)
            b = 0.0
            if cu and self.lam:
                pu = cu / self.N
                b = self.lam * math.log10(self._p(hist[-1], word, pu) / pu)
                if self.floor is not None:
                    b = max(b, self.floor)
            sc = lp + b
        self.memo[key] = sc
        return sc

    def eos(self, last):
        if not self.eos_on:
            return 0.0
        return self.lam * math.log10(self._p(last, "</s>", self.p_eos))


def decode(lex, syls, lm, beam=64):
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
                    sc = s + lm.word(("<s>",) + ws, word, lp)
                    surface = "".join(ws) + word
                    if surface not in cand or sc > cand[surface][0]:
                        cand[surface] = (sc, ws + (word,))
        hyps[i] = sorted(cand.values(), key=lambda x: -x[0])[:beam]
    out = [(s + lm.eos(ws[-1] if ws else "<s>"), ws) for s, ws in hyps[n]]
    return sorted(out, key=lambda x: -x[0])


def rows_of(path, limit=None):
    out = []
    for line in open(path, encoding="utf-8"):
        p = line.rstrip("\n").split("|")
        if len(p) == 3:
            out.append((p[1], p[2].split()))
    return out[:limit] if limit else out


def all_sets():
    dev = sorted(glob.glob(os.path.join(ROOT, "eval", "dev", "*.txt")))
    devrows = []
    for f in dev:
        if not f.endswith("user-typing.txt"):
            devrows += rows_of(f)
    s = {"cvtune": (os.path.join(WORK, "tune", "cvtune.txt"), False), "wikitune": (os.path.join(WORK, "tune", "wikitune.txt"), False),
         "dev302": (None, False), "typing76": (os.path.join(ROOT, "eval", "dev", "user-typing.txt"), False),
         "discord": (os.path.join(PRIVATE, "discord-rows.txt"), True), "news": (os.path.join(PRIVATE, "news", "news-rows-300.txt"), True)}
    out = {}
    for name, (path, private) in s.items():
        if name == "dev302":
            out[name] = (devrows[:302], False)
        elif os.path.exists(path):
            out[name] = (rows_of(path), private)
    return out


def score_row(lex, lm, truth, syls):
    nb = decode(lex, syls, lm)
    t = truth.translate(LENIENT)
    surf = ["".join(ws).translate(LENIENT) for _, ws in nb]
    rank = surf.index(t) if t in surf else -1
    top = nb[0][0]
    z = sum(math.exp((s - top) * LN10) for s, _ in nb)
    ll = CAP if rank < 0 else -((nb[rank][0] - top) * LN10 - math.log(z))
    margin = (top - nb[rank][0]) if rank >= 0 else None
    return int(rank == 0), rank, margin, min(ll, CAP), surf[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config"); ap.add_argument("--note", default="")
    ap.add_argument("--sets", default="cvtune,wikitune,dev302,typing76")
    a = ap.parse_args()
    cfgs = json.loads(a.config)
    cfgs = cfgs if isinstance(cfgs, list) else [cfgs]
    t0 = time.time()
    lex = ime.Lexicon(os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt"),
                      overlay=os.path.join(ROOT, "data", "lexicon", "overlay-add.tsv"))
    sets = {k: v for k, v in all_sets().items() if k in a.sets.split(",")}
    commit = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", ROOT, "status", "--porcelain", "--", "experiments", "data", "reference"], capture_output=True, text=True).stdout.strip()
    prev_corpora, uni, bi = None, None, None
    for cfg in cfgs:
        t1 = time.time()
        if cfg["corpora"] != prev_corpora:
            uni, bi = merged(cfg["corpora"]); prev_corpora = cfg["corpora"]
        lm = LM(cfg, uni, bi)
        t2 = time.time()
        run = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + hashlib.md5(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:6]
        summary = []
        for name, (rows, private) in sets.items():
            d = os.path.join(PRIVATE if private else WORK, "s2-runs" if private else "runs", run)
            os.makedirs(d, exist_ok=True)
            res = [score_row(lex, lm, t, s) for t, s in rows]
            with open(os.path.join(d, f"{name}.tsv"), "w", encoding="utf-8") as f:
                for i, (ok, rank, margin, ll, top1) in enumerate(res):
                    f.write(f"{i}\t{ok}\t{rank}\t{'' if margin is None else round(margin, 4)}\t{ll:.4f}\t{'' if private else top1}\n")
            acc = sum(r[0] for r in res); ll = sum(r[3] for r in res) / len(res); o64 = sum(r[1] >= 0 for r in res)
            summary.append((name, acc, len(res), ll, o64))
        t3 = time.time()
        cols = [run, commit + ("+dirty" if dirty else ""), json.dumps(cfg, ensure_ascii=False)]
        cols += [f"{n}:{acc}/{tot}:ll={ll:.4f}:o64={o64}" for n, acc, tot, ll, o64 in summary]
        cols += [f"load={t2 - t1:.0f}s decode={t3 - t2:.0f}s", a.note]
        new = not os.path.exists(LOG)
        with open(LOG, "a", encoding="utf-8") as f:
            if new:
                f.write("run\tcommit\tconfig\tsets…\ttiming\tnote\n")
            f.write("\t".join(cols) + "\n")
        print(run, " ".join(f"{n} {acc}/{tot} ({acc / tot:.1%}) ll={ll:.3f}" for n, acc, tot, ll, o64 in summary), f"[load {t2 - t1:.0f}s, decode {t3 - t2:.0f}s]", flush=True)
    print(f"total {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
