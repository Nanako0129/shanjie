"""J1：Jev 當同音候選的語意判斷器。只用開發集（CC0），不讀保留集。

每列取 unigram N-best 前 8 名（去重），用 Choice 讓 Jev 挑；另外量單題請求的冷／熱延遲。
用法：python3 experiments/jev/j1_choice.py
"""
import glob
import json
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "reference", "proto"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ime  # noqa: E402
from jev import MODEL, Client  # noqa: E402

TOP = 8
BATCH = 20
LENIENT = str.maketrans("她妳它牠嘗周臺裏", "他你他他嚐週台裡")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
INSTR = ("A user in Taiwan typed a Zhuyin (Bopomofo) phonetic input. Every option below is a sentence with "
         "exactly the same pronunciation. The text the user had already written just before it is: "
         "`rows[{i}].context` (it may be empty). Choose the option that is the sentence the user most likely "
         "meant: correct Traditional Chinese characters as used in Taiwan, grammatical, and sensible in context.")


def load_rows():
    rows = []
    for f in sorted(glob.glob(os.path.join(REPO, "eval", "dev", "*.txt"))):
        for line in open(f, encoding="utf-8"):
            line = line.rstrip("\n")
            if line.count("|") == 2:
                ctx, sent, rd = line.split("|")
                rows.append((os.path.basename(f)[:-4], ctx, sent, rd.split()))
    return rows


def metrics(pairs):
    n = len(pairs)
    chars = sum(len(t) for t, _ in pairs)
    return {"n": n,
            "sent_acc": round(sum(o == t for t, o in pairs) / n, 3),
            "lenient_acc": round(sum(o.translate(LENIENT) == t.translate(LENIENT) for t, o in pairs) / n, 3),
            "char_acc": round(sum(a == b for t, o in pairs for a, b in zip(o, t)) / chars, 4)}


def main():
    os.makedirs(OUT, exist_ok=True)
    lex = ime.Lexicon(os.path.join(REPO, "data", "lexicon", "mcbpmf-data.txt"))
    rows = load_rows()
    cands = []
    for _, _, _, syls in rows:
        seen, top = set(), []
        for _, ws in ime.decode(lex, syls):
            s = "".join(ws)
            if s not in seen:
                seen.add(s); top.append(s)
            if len(top) == TOP:
                break
        cands.append(top)

    client, picks, usage_in, raw = Client(), [], 0, []
    for b in range(0, len(rows), BATCH):
        chunk = list(range(b, min(b + BATCH, len(rows))))
        state = {"rows": [{"context": rows[i][1]} for i in chunk]}
        qs = {f"q{k}": {"type": "choice", "instructions": INSTR.format(i=k),
                        "criteria": {f"c{j + 1}": s for j, s in enumerate(cands[i])}}
              for k, i in enumerate(chunk)}
        answers, usage, _ = client.ask(state, qs)
        usage_in += usage.get("input_tokens", 0)
        for k, i in enumerate(chunk):
            a = answers[f"q{k}"]
            picks.append(cands[i][int(a["choice"][1:]) - 1])
            raw.append({"file": rows[i][0], "truth": rows[i][2], "cands": cands[i],
                        "choice": a["choice"], "confidence": a["confidence"]})

    with open(os.path.join(OUT, "j1_raw.jsonl"), "w", encoding="utf-8") as f:
        for r in raw:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    lines = []
    for name in sorted({r[0] for r in rows}) + ["ALL"]:
        idx = [i for i, r in enumerate(rows) if name in ("ALL", r[0])]
        jev = metrics([(rows[i][2], picks[i]) for i in idx])
        jev[f"oracle@{TOP}"] = round(sum(rows[i][2] in cands[i] for i in idx) / len(idx), 3)
        uni = metrics([(rows[i][2], cands[i][0]) for i in idx])
        lines.append(f"## {name}  unigram  {uni}")
        lines.append(f"## {name}  {MODEL}-choice{TOP}  {jev}")
    for i, r in enumerate(rows):
        if r[0] in ("oov", "user-reported") and picks[i] != r[2]:
            lines.append(f"   ✗ [{r[0]}] {r[2]} → {picks[i]}")

    # 延遲：單題請求。熱＝同一條連線連續送；冷＝每次重新連線（含 TLS 交握）。
    lat = {"warm": [], "cold": []}
    for mode, n in (("cold", 10), ("warm", 30)):
        c = Client()
        c.reconnect()
        for k in range(n):
            if mode == "cold":
                c.reconnect()
            i = k % len(rows)
            _, _, secs = c.ask({"rows": [{"context": rows[i][1]}]},
                               {"q0": {"type": "choice", "instructions": INSTR.format(i=0),
                                       "criteria": {f"c{j + 1}": s for j, s in enumerate(cands[i])}}})
            if not (mode == "warm" and k == 0):   # 熱連線的第一次其實是冷的，不算
                lat[mode].append(secs * 1000)
            time.sleep(0.2)
    for mode, v in lat.items():
        v.sort()
        lines.append(f"## latency  {mode}  {{'n': {len(v)}, 'p50_ms': {v[len(v) // 2]:.0f}, "
                     f"'p95_ms': {v[min(len(v) - 1, int(len(v) * 0.95))]:.0f}, 'min_ms': {v[0]:.0f}}}")
    lines.append(f"## usage  batch_input_tokens={usage_in}  est_cost_usd={usage_in * 0.042 / 1e6:.4f}")
    open(os.path.join(OUT, "j1.txt"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
