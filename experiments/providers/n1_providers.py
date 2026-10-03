"""N1：雲端供應商當同音候選判斷器（給一鍵校正 H 用）。只用 CC0 開發集，不讀保留集。

每句取 unigram N-best 前 K 名（去重），模型只能回候選編號（安全規則 R1）；不合法的回應記為 invalid，退回第一名。
key 只從環境變數讀（由呼叫者從鑰匙圈讀進單一程序），絕不印出或寫檔。
用法：python3 n1_providers.py <provider> <model> [--k 16] [--limit N] [--budget-usd 5]
  provider: openrouter | cerebras | gemini | opengateway | groq
"""
import argparse
import glob
import http.client
import json
import os
import re
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "reference", "proto"))
import ime  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
LEDGER = os.environ.get("N1_LEDGER") or os.path.join(OUT, "spend.json")   # 跨模型累計花費，超過 --budget-usd 就停；平行跑不同供應商時各用一份
LENIENT = str.maketrans("她妳它牠嘗周臺裏", "他你他他嚐週台裡")
PROVIDERS = {  # host, path, key 的環境變數
    "openrouter": ("openrouter.ai", "/api/v1/chat/completions", "OPENROUTER_API_KEY"),
    "cerebras": ("api.cerebras.ai", "/v1/chat/completions", "CEREBRAS_API_KEY"),
    "gemini": ("generativelanguage.googleapis.com", "/v1beta/openai/chat/completions", "GEMINI_API_KEY"),
    "opengateway": ("apis.opengateway.ai", "/v1/chat/completions", "OPENGATEWAY_API_KEY"),
    "groq": ("api.groq.com", "/openai/v1/chat/completions", "GROQ_API_KEY"),
}
SYSTEM = ("You help a Traditional Chinese (Taiwan) Zhuyin input method. The user typed a phonetic input; every "
          "candidate below is a sentence with exactly the same pronunciation. Pick the candidate the user most likely "
          "meant: correct Traditional Chinese characters as used in Taiwan, grammatical, and sensible after the "
          "preceding text. Reply with JSON {\"index\": n} only.")


def load_rows():
    rows = []
    for f in sorted(glob.glob(os.path.join(REPO, "eval", "dev", "*.txt"))):
        for line in open(f, encoding="utf-8"):
            line = line.rstrip("\n")
            if line.count("|") == 2:
                ctx, sent, rd = line.split("|")
                rows.append((os.path.basename(f)[:-4], ctx, sent, rd.split()))
    return rows


def candidates(lex, syls, k):
    seen, top = set(), []
    for _, ws in ime.decode(lex, syls):
        s = "".join(ws)
        if s not in seen:
            seen.add(s); top.append(s)
        if len(top) == k:
            break
    return top


def metrics(pairs):
    n, chars = len(pairs), sum(len(t) for t, _ in pairs)
    return {"n": n, "sent_acc": round(sum(o == t for t, o in pairs) / n, 3),
            "lenient_acc": round(sum(o.translate(LENIENT) == t.translate(LENIENT) for t, o in pairs) / n, 3),
            "char_acc": round(sum(a == b for t, o in pairs for a, b in zip(o, t)) / chars, 4)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("provider"); ap.add_argument("model")
    ap.add_argument("--k", type=int, default=16); ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--budget-usd", type=float, default=5.0)
    ap.add_argument("--price-in", type=float, default=0.0, help="USD per 1M input tokens（供應商不回報 cost 時用）")
    ap.add_argument("--price-out", type=float, default=0.0)
    ap.add_argument("--reasoning", default="low", help="low｜default（default＝不送推理參數）")
    ap.add_argument("--resume", action="store_true", help="接著上次存檔的列數往下跑（換 key 之後用）")
    ap.add_argument("--or-provider", default="", help="OpenRouter 只走這一家（例如 groq），不准退回其他家")
    ap.add_argument("--json-object", action="store_true", help="改用 json_object（Groq 上的 Llama 不支援 json_schema）")
    a = ap.parse_args()
    host, path, env = PROVIDERS[a.provider]
    key = os.environ.get(env, "").strip()
    if not key:
        sys.exit(f"missing {env}")
    os.makedirs(OUT, exist_ok=True)
    spent = json.load(open(LEDGER)) if os.path.exists(LEDGER) else {"usd": 0.0}
    lex = ime.Lexicon(os.path.join(REPO, "data", "lexicon", "mcbpmf-data.txt"))
    rows = load_rows()[: a.limit or None]
    conn = http.client.HTTPSConnection(host, timeout=60)
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    schema = {"type": "json_schema", "json_schema": {"name": "pick", "strict": True, "schema": {
        "type": "object", "properties": {"index": {"type": "integer"}}, "required": ["index"],
        "additionalProperties": False}}}
    prov = f"{a.provider}@{a.or_provider}" if a.or_provider else a.provider
    if a.json_object:
        schema = {"type": "json_object"}
    tag = f"{prov}-{a.model.replace('/', '_')}-k{a.k}-r{a.reasoning}"
    raw, picks, lat, invalid, usd, tin, tout = [], [], [], 0, 0.0, 0, 0
    prev_usd, prev_in, prev_out = 0.0, 0, 0
    jl = os.path.join(OUT, f"{tag}.jsonl")
    if a.resume and os.path.exists(jl):   # 已存檔的列直接沿用（花費已記在帳本裡）
        raw = [json.loads(l) for l in open(jl, encoding="utf-8")]
        picks = [r["cands"][r["index"] - 1] for r in raw]; lat = [r["ms"] for r in raw]
        print(f"resume from row {len(raw)}", file=sys.stderr)
        txt = os.path.join(OUT, f"{tag}.txt")   # 前幾段的花費與 token 接著累計（帳本只加這次新呼叫的）
        if os.path.exists(txt):
            for line in open(txt, encoding="utf-8"):
                if line.startswith("## usage"):
                    prev = json.loads(re.search(r"\{.*\}", line).group(0).replace("'", '"'))
                    prev_usd, prev_in, prev_out = prev["usd"], prev["in_tokens"], prev["out_tokens"]
    stopped = None
    for i, (name, ctx, truth, syls) in enumerate(rows):
        if i < len(raw):
            continue
        cands = candidates(lex, syls, a.k)
        user = f"Preceding text: {ctx or '(none)'}\nCandidates:\n" + "\n".join(f"{j + 1}. {c}" for j, c in enumerate(cands))
        body = {"model": a.model, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                "response_format": schema, "max_tokens": 800, "temperature": 0}
        if a.provider == "openrouter":
            body["usage"] = {"include": True}
            if a.or_provider:
                body["provider"] = {"order": [a.or_provider], "allow_fallbacks": False}
            if a.reasoning != "default":
                body["reasoning"] = {"effort": a.reasoning}
        elif a.reasoning != "default":   # Cerebras、Gemini 的 OpenAI 相容端點
            body["reasoning_effort"] = a.reasoning
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        t = time.perf_counter()
        for attempt in range(2):
            try:
                conn.request("POST", path, data, headers)
                resp = conn.getresponse(); payload = resp.read()
                break
            except (http.client.HTTPException, OSError):
                conn = http.client.HTTPSConnection(host, timeout=60)
        secs = time.perf_counter() - t
        if resp.status != 200:   # 先存已完成的列再停；429 印 RATE_LIMIT，換 key 後用 --resume 接著跑
            kind = "RATE_LIMIT" if resp.status == 429 else "HTTP_ERROR"
            stopped = f"{kind} HTTP {resp.status} at row {i} ({len(raw)} rows saved): {payload[:200]!r}"
            break
        out = json.loads(payload)
        u = out.get("usage", {})
        tin += u.get("prompt_tokens", 0); tout += u.get("completion_tokens", 0)
        usd += u.get("cost", 0.0) or (u.get("prompt_tokens", 0) * a.price_in + u.get("completion_tokens", 0) * a.price_out) / 1e6
        # 被安全過濾擋掉等情況會沒有 message：當成不合法回應（R1：退回第一名），不讓整批崩潰
        content = ((out.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        m = re.search(r'"index"\s*:\s*(\d+)', content) or re.fullmatch(r"\s*(\d+)\s*", content)
        idx = int(m.group(1)) if m else 0
        if not 1 <= idx <= len(cands):
            invalid += 1; idx = 1
        picks.append(cands[idx - 1]); lat.append(secs * 1000)
        raw.append({"file": name, "truth": truth, "cands": cands, "index": idx, "content": content[:200], "ms": round(secs * 1000), "served_by": out.get("provider")})
        if spent["usd"] + usd > a.budget_usd:
            print(f"budget reached after {i + 1} rows", file=sys.stderr); break
    spent["usd"] += usd
    json.dump(spent, open(LEDGER, "w"))
    with open(os.path.join(OUT, f"{tag}.jsonl"), "w", encoding="utf-8") as f:
        for r in raw:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    done = rows[: len(picks)]
    lines = []
    if not done:
        print(stopped or "no rows", file=sys.stderr); sys.exit(3)
    for name in sorted({r[0] for r in done}) + ["ALL"]:
        idx = [i for i, r in enumerate(done) if name in ("ALL", r[0])]
        m = metrics([(done[i][2], picks[i]) for i in idx])
        m[f"oracle@{a.k}"] = round(sum(done[i][2] in raw[i]["cands"] for i in idx) / len(idx), 3)
        lines.append(f"## {name}  {tag}  {m}")
    s = sorted(lat)
    lines.append(f"## latency  {tag}  {{'n': {len(s)}, 'p50_ms': {s[len(s) // 2]:.0f}, 'p95_ms': {s[min(len(s) - 1, int(len(s) * .95))]:.0f}}}")
    lines.append(f"## usage  {tag}  {{'invalid': {invalid}, 'in_tokens': {tin + prev_in}, 'out_tokens': {tout + prev_out}, 'usd': {usd + prev_usd:.4f}, 'ledger_usd': {spent['usd']:.4f}, 'served_by': {sorted({str(r.get('served_by')) for r in raw})!r}}}")
    open(os.path.join(OUT, f"{tag}.txt"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    if stopped:
        print(stopped)
        sys.exit(3)


if __name__ == "__main__":
    main()
