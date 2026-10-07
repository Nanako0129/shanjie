"""S2 參考評測：用 lm.py 對一個評測檔跑帶 LM 的解碼，印出 Rust 核心必須逐位元組相同的摘要行。

輸出：`## <名稱>  lm-<profile>  {'n': …, 'top1': …, 'oracle@64': …, 'top1_sha256': '…'}`
  top1 用寬鬆對照計數；top1_sha256 是「每列第一名 surface（不套寬鬆）以 \\n 串接」的 SHA-256。
--context：第一個詞的歷史用每列的前文（S2h：context_key 截尾 → history），摘要行的 profile 欄寫成 lm-<profile>+ctx。
--no-demote：不套降權（預設套用 data/lexicon/demote.tsv）；摘要行的 profile 欄後面加 -nodemote（和 +ctx 一樣接在 lm-<profile> 後，兩個都有時 +ctx 在前）。
--dump FILE：每列寫 `列號\\t名次\\tsurface\\t分數(repr)`，前 64 名，給逐分數比對（容許 1e-9 的浮點誤差）。
用法：python3 reference/proto/lm_eval.py --lm data/lm/bigram.sjlm --profile chat|formal --rows <檔> [--limit N] [--name 名稱] [--dump FILE] [--context] [--no-demote]
  評測檔格式 `前文|句子|讀音`；dev 集要先依檔名排序串接（和 CLI 相同），可用 --dev 302 直接取開發集前 N 列。
"""
import argparse
import glob
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import ime  # noqa: E402
import lm as L  # noqa: E402

from eval import lenient  # noqa: E402  單字對照＋異體詞表（§S2v）


def rows_of(paths):
    out = []
    for f in paths:
        for line in open(f, encoding="utf-8"):
            p = line.rstrip("\n").split("|")
            if len(p) == 3:
                out.append((p[1], p[2].split(), p[0]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lm", default=os.path.join(ROOT, "data", "lm", "bigram.sjlm"))
    ap.add_argument("--profile", choices=sorted(L.PROFILES), required=True)
    ap.add_argument("--rows"); ap.add_argument("--dev", type=int); ap.add_argument("--limit", type=int)
    ap.add_argument("--name"); ap.add_argument("--dump"); ap.add_argument("--context", action="store_true"); ap.add_argument("--no-demote", action="store_true")
    a = ap.parse_args()
    if a.dev:
        rows = rows_of(sorted(glob.glob(os.path.join(ROOT, "eval", "dev", "*.txt"))))[:a.dev]
        name = a.name or f"dev{a.dev}"
    else:
        rows = rows_of([a.rows])[:a.limit] if a.limit else rows_of([a.rows])
        name = a.name or os.path.basename(a.rows)
    lm = L.BigramLM(a.lm)
    base = ime.Lexicon(os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt"),
                       overlay=ime.OVERLAYS)
    ov = {l.split("\t")[1] for l in open(os.path.join(ROOT, "data", "lexicon", "overlay-add.tsv"), encoding="utf-8")}
    lex = L.cap_overlay(base, ov, lm)
    top1, o64, firsts = 0, 0, []
    dump = open(a.dump, "w", encoding="utf-8") if a.dump else None
    for i, (t, syls, ctx) in enumerate(rows, 1):
        nb = L.decode(lex, syls, lm, a.profile, start=L.history(L.context_key(ctx), lm) if a.context else "<s>", demote=not a.no_demote)
        surf = ["".join(ws) for _, ws in nb]
        firsts.append(surf[0])
        top1 += lenient(surf[0]) == lenient(t)
        o64 += lenient(t) in {lenient(s) for s in surf}
        if dump:
            for r, (sc, ws) in enumerate(nb, 1):
                dump.write(f"{i}\t{r}\t{''.join(ws)}\t{sc!r}\n")
    sha = hashlib.sha256("\n".join(firsts).encode("utf-8")).hexdigest()
    print(f"## {name}  lm-{a.profile}{'+ctx' if a.context else ''}{'-nodemote' if a.no_demote else ''}  {{'n': {len(rows)}, 'top1': {top1}, 'oracle@64': {o64}, 'top1_sha256': '{sha}'}}")


if __name__ == "__main__":
    main()
