"""S7f generated sentences (contract 2.2; used by A2 only).

  python generate.py --provider cerebras --model gpt-oss-120b --price-in P --price-out Q \
      --seeds seeds/*.txt --invade INVADE_CLONE --invade-sha256 HEX --tune TUNEDIR --chars 2500000 \
      --ledger DIR/spend.json --budget-usd 20 --out DIR [--workers 8] [--resume] [--dedupe-against FILE ...]
  python generate.py --provider openrouter --model MODEL --or-provider NAME[,NAME] ...   (allow_fallbacks is always false)
  python generate.py ... --dry-run 5          # prints the first 5 prompts; no key is read, nothing is sent

Prompts: 3-5 seed words drawn with a fixed seed from the seed files, put into the fixed INSTRUCTION; nothing else is sent (no system
message). The key comes only from the provider's environment variable (experiments/providers/n1_providers.PROVIDERS) and is never
printed or written. Spend: every call's cost (the provider's usage.cost, else --price-in/--price-out per 1M tokens) is added to the
ledger file ({"usd": x}, the n1_providers format) after each batch of --workers calls; no batch starts once the ledger is at or above
--budget-usd. Run the two models one after the other against the same ledger.

Filters, all deterministic (contract 2.2): empty / multi_line response; no_seed (none of THIS prompt's seed words verbatim); invade
(contains a term of invade.load); then clean.Filter (simplified, no_han, length 4-60, duplicate, eval_overlap). Generation stops once
the kept sentences reach --chars + SURPLUS; they are then sampled to exactly --chars with the fixed seed. Exit 3 if the budget or an HTTP error
stops it first, or if the exact quota cannot be met; the raw responses stay in DIR/<tag>.jsonl and --resume continues from them.
"""
import argparse
import collections
import concurrent.futures
import http.client
import json
import os
import random
import sys

import clean
import invade

sys.path.insert(0, os.path.join(clean.ROOT, "experiments", "providers"))
from n1_providers import PROVIDERS  # noqa: E402  host, path, key environment variable

INSTRUCTION = ("請寫一句台灣人在聊天軟體裡會打的話：口語、自然，用台灣的繁體中文，並且至少用到下面其中一個詞：{words}。"
               "只輸出這一句話，不要引號、說明或翻譯。")
QUOTES = "「」『』\"'“”‘’ \t"
SURPLUS = 1000    # characters collected beyond --chars, so clean.sample_to_cap has enough lines to hit the quota exactly


def load_seeds(paths):
    words, info = [], {}
    for p in paths:
        info[os.path.basename(p)] = clean.sha256_file(p)
        for line in open(p, encoding="utf-8"):
            w = line.strip()
            if w and not w.startswith("#") and w not in words:
                words.append(w)
    if len(words) < 5:
        sys.exit(f"generate: {len(words)} seed words; at least 5 are needed")
    return words, info


def prompts(words, seed):
    """Endless, deterministic: (seed words of prompt i) for i = 0, 1, ..."""
    rng = random.Random(seed)
    while True:
        yield rng.sample(words, rng.randint(3, 5))


def prompt_text(ws):
    return INSTRUCTION.format(words="、".join(ws))


def request_body(a, text):
    body = {"model": a.model, "messages": [{"role": "user", "content": text}], "max_tokens": a.max_tokens, "temperature": a.temperature}
    if a.provider == "openrouter":
        body["usage"] = {"include": True}
        body["provider"] = {"order": a.or_provider.split(","), "allow_fallbacks": False}
        if a.reasoning != "default":
            body["reasoning"] = {"effort": a.reasoning}
    elif a.reasoning != "default":
        body["reasoning_effort"] = a.reasoning
    return body


def post(host, path, body, key):
    """One HTTPS POST; (status, payload bytes). Network errors come back as status 0. Tests replace this function."""
    # ponytail: a new connection per call (one TLS handshake each); keep one per worker thread if that ever dominates
    try:
        conn = http.client.HTTPSConnection(host, timeout=120)
        conn.request("POST", path, json.dumps(body, ensure_ascii=False).encode("utf-8"),
                     {"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        resp = conn.getresponse()
        return resp.status, resp.read()
    except (http.client.HTTPException, OSError) as e:
        return 0, type(e).__name__.encode()


def cost_of(usage, a):
    if usage.get("cost") is not None:
        return float(usage["cost"])
    return (usage.get("prompt_tokens", 0) * a.price_in + usage.get("completion_tokens", 0) * a.price_out) / 1e6


def ledger_add(path, usd):
    """Re-reads the ledger, adds usd, writes it atomically; returns the new total."""
    total = json.load(open(path))["usd"] if os.path.exists(path) else 0.0
    total += usd
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"usd": total}, f)
    os.replace(tmp, path)
    return total


def judge(content, ws, terms, flt):
    """The kept sentence, or None (the reason is counted in flt.removed)."""
    s = (content or "").strip().strip(QUOTES)
    r = ("empty" if not s else "multi_line" if "\n" in s else "no_seed" if not any(w in s for w in ws)
         else "invade" if terms.any(s) else None)
    if r:
        flt.removed[r] += 1
        return None
    return s if flt.keep(s) else None


def stop(msg):
    print(f"STOP: {msg}", file=sys.stderr)
    sys.exit(3)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["cerebras", "openrouter"], required=True); ap.add_argument("--model", required=True)
    ap.add_argument("--or-provider", default="", help="OpenRouter: provider order (comma-separated); fallbacks are always off")
    ap.add_argument("--seeds", nargs="+", required=True); ap.add_argument("--invade", required=True)
    ap.add_argument("--invade-sha256", default="", help="the frozen list's SHA-256 (invade.py prints it); a mismatch stops")
    ap.add_argument("--chars", type=int, required=True, help="the exact character quota for this model")
    ap.add_argument("--out", required=True); ap.add_argument("--ledger", required=True)
    ap.add_argument("--budget-usd", type=float, required=True)
    ap.add_argument("--price-in", type=float, default=0.0, help="USD per 1M input tokens, when the provider reports no cost")
    ap.add_argument("--price-out", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=clean.SEED); ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--temperature", type=float, default=1.0); ap.add_argument("--max-tokens", type=int, default=1000)
    ap.add_argument("--reasoning", default="low", help="low | medium | high | default (default = no reasoning parameter)")
    ap.add_argument("--dedupe-against", nargs="*", default=[], help="sentence files already in the mix (e.g. the other model's output)")
    ap.add_argument("--resume", action="store_true"); ap.add_argument("--dry-run", type=int, default=0, metavar="N")
    clean.add_eval_args(ap)
    a = ap.parse_args(argv)
    if a.provider == "openrouter" and not a.or_provider:
        ap.error("openrouter needs --or-provider (an explicit provider order; fallbacks are off)")
    words, seed_info = load_seeds(a.seeds)
    gen = prompts(words, a.seed)
    if a.dry_run:
        for _ in range(a.dry_run):
            print(prompt_text(next(gen)))
        return
    host, path, env = PROVIDERS[a.provider]
    key = os.environ.get(env, "").strip()
    if not key:
        sys.exit(f"generate: missing {env}")
    terms, inv_info = invade.load(a.invade)
    if a.invade_sha256 and inv_info["sha256"] != a.invade_sha256:
        stop(f"invade list SHA-256 {inv_info['sha256']} is not the frozen {a.invade_sha256}")
    ev, ev_info = clean.eval_sentences(a.tune, a.eval)
    flt, term_m = clean.Filter(clean.load_simp_only(), ev), clean.Matcher(terms)
    for p in a.dedupe_against:
        flt.seen.update(clean.read_lines(p))
    os.makedirs(a.out, exist_ok=True)
    tag = f"{a.provider}-{a.model.replace('/', '_')}"
    raw_path = os.path.join(a.out, f"{tag}.jsonl")
    if os.path.exists(raw_path) and not a.resume:
        sys.exit(f"generate: {raw_path} exists; pass --resume to continue it")
    kept, n_chars, i, tokens, served = [], 0, 0, collections.Counter(), set()

    def take(rec):
        nonlocal n_chars
        s = judge(rec["content"], rec["seeds"], term_m, flt)
        if s is not None:
            kept.append(s); n_chars += len(s)
        tokens.update(in_tokens=rec["in_tokens"], out_tokens=rec["out_tokens"]); served.add(str(rec.get("served_by")))
    if a.resume and os.path.exists(raw_path):
        for line in open(raw_path, encoding="utf-8"):
            rec = json.loads(line)
            if rec["i"] != i or rec["seeds"] != next(gen):
                stop(f"{raw_path} row {i} does not match the prompts of these seed files and --seed")
            take(rec); i += 1
    print(f"{tag}: {len(words)} seed words, {i} earlier responses, {n_chars:,}/{a.chars:,} chars kept", flush=True)

    def call(job):
        j, ws = job
        status, payload = post(host, path, request_body(a, prompt_text(ws)), key)
        if status != 200:
            return {"i": j, "status": status, "error": payload[:200].decode("utf-8", "replace")}
        try:
            out = json.loads(payload)
        except ValueError:
            return {"i": j, "status": status, "error": "the response is not JSON"}
        u = out.get("usage") or {}
        if u.get("cost") is None and not (a.price_in or a.price_out):
            return {"i": j, "status": status, "error": "no usage.cost and no --price-in/--price-out: the budget cannot be enforced"}
        content = ((out.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        return {"i": j, "seeds": ws, "content": content, "in_tokens": u.get("prompt_tokens", 0),
                "out_tokens": u.get("completion_tokens", 0), "usd": cost_of(u, a), "served_by": out.get("provider")}

    spent, halt = (json.load(open(a.ledger))["usd"] if os.path.exists(a.ledger) else 0.0), None
    with open(raw_path, "a", encoding="utf-8") as raw, concurrent.futures.ThreadPoolExecutor(a.workers) as ex:
        while n_chars < a.chars + SURPLUS and halt is None:
            if spent >= a.budget_usd:
                halt = f"budget: the ledger is at US${spent:.4f} >= US${a.budget_usd}"
                break
            jobs = [(i + k, next(gen)) for k in range(a.workers)]
            results = list(ex.map(call, jobs))
            spent = ledger_add(a.ledger, sum(r.get("usd", 0.0) for r in results))
            for r in results:      # responses after the first failure are paid for but not kept; --resume asks again
                if "error" in r:
                    halt = f"HTTP {r['status']} at prompt {r['i']}: {r['error']}"
                    break
                raw.write(json.dumps(r, ensure_ascii=False) + "\n")
                take(r); i += 1
            raw.flush()
    final = clean.sample_to_cap(kept, a.chars, a.seed)
    m = {"provider": a.provider, "model": a.model, "provider_order": a.or_provider.split(",") if a.or_provider else None,
         "allow_fallbacks": False if a.provider == "openrouter" else None, "instruction": INSTRUCTION, "seed": a.seed,
         "temperature": a.temperature, "max_tokens": a.max_tokens, "reasoning": a.reasoning, "seeds": seed_info,
         "seed_words": len(words), "invade": inv_info, "eval": ev_info, "responses": i, "kept_before_sampling": n_chars,
         "removed": dict(sorted(flt.removed.items())), "ledger_usd": spent, **tokens, "served_by": sorted(served),
         "quota": a.chars, "rows": len(final), "chars": sum(map(len, final)), "stopped": halt}
    if m["chars"] == a.chars:
        path_txt = os.path.join(a.out, f"{tag}.txt")
        clean.write_lines(path_txt, final)
        m["sha256"] = clean.sha256_file(path_txt)
    clean.write_manifest(os.path.join(a.out, f"{tag}.manifest.json"), m)
    print(f"{tag}: {i} responses, kept {n_chars:,} chars, final {m['chars']:,}/{a.chars:,}, ledger US${spent:.4f}", flush=True)
    if m["chars"] != a.chars:
        stop(halt or f"under quota: {m['chars']:,} of {a.chars:,} characters")


if __name__ == "__main__":
    main()
