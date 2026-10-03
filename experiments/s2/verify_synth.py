"""S2：用同一個模型（Cerebras gpt-oss-120b）檢查合成句有沒有同音錯字，丟掉被標出的句子。

每次送 40 句（編號），要求只回 {"wrong": [編號...]}；回應不合法的那一批整批丟掉（寧可少、不要錯）。
輸入檔每行一句；輸出寫到 <輸入>.ok；花費記在同一個 synth-spend.json 帳本。
用法：python3 experiments/s2/verify_synth.py <句子檔> [--workers 6] [--budget-usd 18]
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from synth_colloquial import OUT, PRICE_IN, PRICE_OUT, call  # noqa: E402

ASK = ("下面每一句都是台灣繁體中文的口語句。請找出含有「同音或近音錯字」（例如把「再」寫成「在」、"
       "把「帶」寫成「代」、把「得」寫成「的」）或用字不自然的句子。"
       "只回 JSON：{\"wrong\": [有問題的句子編號]}，沒有就回 {\"wrong\": []}。\n\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path"); ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--budget-usd", type=float, default=18.0)
    a = ap.parse_args()
    key = os.environ.get("CEREBRAS_API_KEY", "").strip()
    if not key:
        raise SystemExit("missing CEREBRAS_API_KEY")
    ledger = os.path.join(OUT, "synth-spend.json")
    spent = json.load(open(ledger))["usd"] if os.path.exists(ledger) else 0.0
    lines = open(a.path, encoding="utf-8").read().splitlines()
    batches = [lines[i:i + 40] for i in range(0, len(lines), 40)]
    prompts = [ASK + "\n".join(f"{j + 1}. {s}" for j, s in enumerate(b)) for b in batches]
    keep, dropped, failed = [], 0, 0
    with cf.ThreadPoolExecutor(a.workers) as ex:
        for b, out in zip(batches, ex.map(lambda p: call(key, p), prompts)):
            if not out:
                failed += len(b); continue
            u = out.get("usage", {})
            spent += (u.get("prompt_tokens", 0) * PRICE_IN + u.get("completion_tokens", 0) * PRICE_OUT) / 1e6
            text = ((out.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            m = re.search(r"\{.*\}", text, re.S)
            try:
                wrong = {int(x) for x in json.loads(m.group(0))["wrong"]}
            except Exception:
                failed += len(b); continue
            for j, s in enumerate(b):
                if j + 1 in wrong:
                    dropped += 1
                else:
                    keep.append(s)
            if spent > a.budget_usd:
                print(f"budget reached: US${spent:.2f}"); break
    json.dump({"usd": spent}, open(ledger, "w"))
    open(a.path + ".ok", "w", encoding="utf-8").write("\n".join(keep) + "\n")
    print(f"kept {len(keep)}, dropped {dropped} flagged, {failed} in failed batches; ledger US${spent:.3f} (estimate)")


if __name__ == "__main__":
    main()
