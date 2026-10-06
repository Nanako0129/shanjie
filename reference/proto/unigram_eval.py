"""S-bench 7.1 參考評測：只用基底詞庫（等於 CLI 的 `--no-overlay`）、unigram beam 64，印出 Rust CLI 必須逐位元組相同的輸出。

摘要行：`## <集合名>  unigram  {'n': …, 'top1': …, 'oracle@64': …, 'top1_sha256': '…'}`（名稱是 rows 檔的主檔名）。
--dump FILE：每列寫 `列號\\t名次\\tsurface\\t分數(repr)`，前 64 名。寬鬆對照同 lm_eval.py（SHANJIE_VARIANTS 指定異體表）。
產生 golden（在 repo 根目錄）：
  SHANJIE_VARIANTS=eval/bench/suite-v1/variants.tsv python3 reference/proto/unigram_eval.py \\
    --rows eval/bench/suite-v1/typing76.txt --dump <檔>
  eval/golden/sbench-unigram-typing76.txt = 摘要行 + <檔> 全文。
"""
import argparse
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import ime  # noqa: E402

from eval import lenient  # noqa: E402
from lm_eval import rows_of  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True)
    ap.add_argument("--dump")
    a = ap.parse_args()
    rows = rows_of([a.rows])
    lex = ime.Lexicon(os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt"), overlay=None)
    top1, o64, firsts = 0, 0, []
    dump = open(a.dump, "w", encoding="utf-8") if a.dump else None
    for i, (t, syls) in enumerate(rows, 1):
        nb = ime.decode(lex, syls, beam=64)[:64]
        surf = ["".join(ws) for _, ws in nb]
        firsts.append(surf[0])
        top1 += lenient(surf[0]) == lenient(t)
        o64 += lenient(t) in {lenient(s) for s in surf}
        if dump:
            for r, (sc, ws) in enumerate(nb, 1):
                dump.write(f"{i}\t{r}\t{''.join(ws)}\t{sc!r}\n")
    sha = hashlib.sha256("\n".join(firsts).encode("utf-8")).hexdigest()
    name = os.path.splitext(os.path.basename(a.rows))[0]
    print(f"## {name}  unigram  {{'n': {len(rows)}, 'top1': {top1}, 'oracle@64': {o64}, 'top1_sha256': '{sha}'}}")


if __name__ == "__main__":
    main()
