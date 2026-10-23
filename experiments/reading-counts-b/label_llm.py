#!/usr/bin/env python3
"""The labeller of step 2 (README.md in this directory): one Message Batches request per position, the model picks the character's reading from the
merged lexicon's single-character readings. Standard library only (urllib; every call uses ssl_context(): the default CAs plus the file named by NODE_EXTRA_CA_CERTS or SSL_CERT_FILE); the key is read from the environment variable ANTHROPIC_API_KEY, sent
only as the `x-api-key` header to api.anthropic.com, and never printed, logged or written.

  label_llm.py requests POSITIONS.tsv --out batch.jsonl [--map SIDECAR.json] [--model claude-opus-5-5] [--no-temperature] [--chars step2-chars.txt]
  label_llm.py price batch.jsonl [--sample 200] [--seed 20261023]   POST /v1/messages/count_tokens on a fixed-seed sample, writes batch.jsonl.price.json
  label_llm.py submit batch.jsonl --max-usd X        POST /v1/messages/batches, prints the batch id; needs the price file of this very batch
  label_llm.py status BATCH_ID                       GET /v1/messages/batches/{id}, prints the status and the request counts
  label_llm.py collect BATCH_ID --map SIDECAR.json --out labels.tsv

POSITIONS.tsv: `id, source, sentence, pos, char` (sample_single.py `sample` or `human`; more columns are ignored). `requests` writes one JSONL line
per position (custom_id = the id, max_tokens 8, temperature 0 unless --no-temperature, no thinking) and the sidecar {id: {char, options: {letter:
reading}}} (default <out>.map.json), and prints the request count and a ROUGH input-token estimate (characters, not a tokenizer: 1.5 per non-ASCII
character and 0.3 per ASCII one; get the exact count from the count_tokens endpoint before submitting if the budget is tight).
The sentence is cut to at most 60 characters on each side of the target (pos stays right) and any 【 】 already in it become 「 」 before the target is marked.
`price` counts the input tokens of SAMPLE requests (a fixed-seed draw) with count_tokens, scales to the batch, adds max_tokens per request as the output
upper bound, and prices both at the Batch rate of the model (PRICES) into BATCH.price.json together with the SHA-256 of the batch file. `submit` stops
unless that file exists for the same bytes and the estimate is <= --max-usd.
Options A, B, C... are the character's single-character readings in the merged lexicon (score descending, ties in lexicon order), each with up to 3
words of >= 2 characters that read it that way (highest score first, ties by the word) or just the Zhuyin.
`collect` does not wait: the batch must have ended (exit 1 otherwise). It writes `id, char, reading` for every position of the sidecar; the reading is
empty for an answer that is not exactly one valid option letter after stripping whitespace (also for an errored, expired or missing result), prints the
counts, and exits 3 when more than 5% are invalid (the registration's stop). Exit 0 done, 1 stop or API error.
"""
import argparse, collections, concurrent.futures, hashlib, json, os, random, re, ssl, sys, threading, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sample_single as S  # noqa: E402

API = "https://api.anthropic.com"
VERSION = "2023-06-01"
MODEL = "claude-opus-5-5"
MAX_TOKENS = 1024      # Opus 5.5 cannot turn thinking off: adaptive thinking at low effort, room for the thought and the letter (2026-10-23)
THINKING = {"thinking": {"type": "adaptive"}, "output_config": {"effort": "low"}}
INVALID_STOP = 0.05
CONTEXT = 60   # characters kept on each side of the target
# Batch price, USD per million tokens (input, output), by model
PRICES = {"claude-opus-5-5": (2, 10), "claude-fable-5-1": (5, 25), "claude-sonnet-5-5": (1, 5)}
ID_OK = re.compile(r"^[A-Za-z0-9_-]{1,64}$")   # the API's custom_id rule
PROMPT = ("下面這個句子裡，用【】標出的字讀哪一個音？\n\n句子：{sentence}\n\n選項：\n{options}\n\n只回答一個選項代號（{letters}），不要解釋。")


def mark(sentence, pos, char):
    """The sentence cut to CONTEXT characters each side of the target, existing 【 】 replaced by 「 」, the target marked 【 】."""
    plain = lambda t: t.replace("【", "「").replace("】", "」")
    return plain(sentence[max(0, pos - CONTEXT):pos]) + "【" + char + "】" + plain(sentence[pos + 1:pos + 1 + CONTEXT])


def option_lines(char, readings, examples):
    """-> ([(letter, reading)], the option text): readings in order, each with up to 3 distinct example words."""
    rs = readings[char]
    if len(rs) > 26:
        S.B1.stop(f"{char}: more than 26 readings")
    opts, lines = [], []
    for letter, r in zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", rs):
        words = []
        for _, w in sorted(examples.get((char, r), ()), key=lambda x: (-x[0], x[1])):
            if w not in words:
                words.append(w)
        opts.append((letter, r))
        lines.append(f"{letter}. {r}" + (f"（例：{'、'.join(words[:S.MAX_EXAMPLES])}）" if words else ""))
    return opts, "\n".join(lines)


def build_requests(positions, readings, examples, model=MODEL, temperature=False):
    """-> ([request dict], sidecar, estimated input tokens). A position whose character is not in `readings` stops the build."""
    reqs, side, est = [], {}, 0.0
    for id_, _, sentence, pos, char, *_ in positions:
        if not ID_OK.match(id_) or id_ in side:
            S.B1.stop(f"id {id_!r}: not a valid or not a unique custom_id")
        if sentence[pos:pos + 1] != char or char not in readings:
            S.B1.stop(f"{id_}: the character is not at pos, or not in the lexicon list")
        opts, text = option_lines(char, readings, examples)
        prompt = PROMPT.format(sentence=mark(sentence, pos, char), options=text, letters="、".join(l for l, _ in opts))
        params = {"model": model, "max_tokens": MAX_TOKENS, "messages": [{"role": "user", "content": prompt}], **THINKING}
        if temperature:
            params["temperature"] = 0
        reqs.append({"custom_id": id_, "params": params})
        side[id_] = {"char": char, "options": dict(opts)}
        est += sum(1.5 if ord(c) > 127 else 0.3 for c in prompt)
    return reqs, side, int(est)


def ssl_context():
    """The default SSL context, plus the extra CA file named by NODE_EXTRA_CA_CERTS (or SSL_CERT_FILE) when set: a proxy that re-signs HTTPS (the Claude
    Code sandbox) is verified against its own CA instead of failing with CERTIFICATE_VERIFY_FAILED."""
    ctx = ssl.create_default_context()
    ca = os.environ.get("NODE_EXTRA_CA_CERTS") or os.environ.get("SSL_CERT_FILE")
    if ca:
        ctx.load_verify_locations(ca)
    return ctx


def api(method, url, body=None):
    """-> the response bytes. The key goes only to api.anthropic.com; an HTTP error prints its status code only."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        S.B1.stop("ANTHROPIC_API_KEY is not set")
    if not url.startswith(API + "/"):
        S.B1.stop("refusing to send the key to a host other than api.anthropic.com")
    req = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"x-api-key": key, "anthropic-version": VERSION, "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600, context=ssl_context()) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        S.B1.stop(f"{method} {url[len(API):]}: HTTP {e.code}")
    except urllib.error.URLError as e:
        S.B1.stop(f"{method} {url[len(API):]}: {type(e.reason).__name__}")


def file_sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def estimate(reqs, sampled_tokens, n_sampled, output_tokens=None):
    """-> {model, requests, input_tokens, output_tokens_upper, usd}: the sample's mean input tokens times the batch size; output = max_tokens each."""
    models = {q["params"]["model"] for q in reqs}
    if len(models) != 1 or not models <= set(PRICES):
        S.B1.stop(f"models {sorted(models)}: one model with a price in PRICES is needed")
    (model,) = models
    tin = sampled_tokens / n_sampled * len(reqs)
    tout = sum(q["params"]["max_tokens"] for q in reqs) if output_tokens is None else output_tokens * len(reqs)
    usd = (tin * PRICES[model][0] + tout * PRICES[model][1]) / 1e6
    return {"model": model, "requests": len(reqs), "input_tokens": round(tin), "output_tokens_upper": tout, "usd": round(usd, 4)}


def price(path, sample, seed, output_tokens=None):
    with open(path, encoding="utf-8") as f:
        reqs = [json.loads(l) for l in f]
    if not reqs:
        S.B1.stop("empty batch")
    idx = sorted(random.Random(seed).sample(range(len(reqs)), min(sample, len(reqs))))
    tokens = 0
    for i in idx:
        p = reqs[i]["params"]
        tokens += json.loads(api("POST", API + "/v1/messages/count_tokens", {"model": p["model"], "messages": p["messages"]}))["input_tokens"]
    est = dict(estimate(reqs, tokens, len(idx), output_tokens), sampled=len(idx), sha256=file_sha256(path))
    with open(path + ".price.json", "w", encoding="utf-8") as f:
        json.dump(est, f)
    return est


def check_price(path, max_usd):
    """STOP unless BATCH.price.json was written by `price` for these very bytes and its estimate is <= max_usd; -> the estimate."""
    try:
        with open(path + ".price.json", encoding="utf-8") as f:
            est = json.load(f)
    except OSError:
        S.B1.stop("no price file: run `price` first")
    if est.get("sha256") != file_sha256(path):
        S.B1.stop("the price file is for another batch file: run `price` again")
    if est["usd"] > max_usd:
        S.B1.stop(f"estimated US${est['usd']} is above --max-usd {max_usd}")
    return est


DIRECT_FACTOR = 2      # standard (non-Batch) price = 2 x the Batch price in PRICES (platform.claude.com pricing, 2026-10-23)
RETRY_STATUS = (408, 429, 500, 502, 503, 529)


def send(params, tries=6):
    """POST /v1/messages once (retrying overload and rate limits with backoff) -> a batch-style result {"type": "succeeded", "message": ...} or
    {"type": "errored", "error": {"status": code}}. The key handling is api()'s; an error records the status code only."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        S.B1.stop("ANTHROPIC_API_KEY is not set")
    body = json.dumps(params).encode()
    for i in range(tries):
        req = urllib.request.Request(API + "/v1/messages", method="POST", data=body,
                                     headers={"x-api-key": key, "anthropic-version": VERSION, "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120, context=ssl_context()) as r:
                return {"type": "succeeded", "message": json.loads(r.read())}
        except urllib.error.HTTPError as e:
            if e.code not in RETRY_STATUS or i == tries - 1:
                return {"type": "errored", "error": {"status": e.code}}
        except urllib.error.URLError:
            if i == tries - 1:
                return {"type": "errored", "error": {"status": "network"}}
        time.sleep(2 ** i)


def spent_usd(result, model):
    u = (result.get("message") or {}).get("usage") or {}
    pin, pout = PRICES[model]
    return DIRECT_FACTOR * (u.get("input_tokens", 0) * pin + u.get("output_tokens", 0) * pout) / 1e6


def direct(path, out, workers, sender=send, max_usd=None):
    """Every request of BATCH.jsonl through the plain Messages API, results appended to OUT as batch-style lines {custom_id, result}; ids already in
    OUT are skipped, so a stopped run resumes. -> (sent, errored)."""
    done = set()
    if os.path.exists(out):
        done = {json.loads(l)["custom_id"] for l in open(out, encoding="utf-8") if l.strip()}
    todo = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    todo = [q for q in todo if q["custom_id"] not in done]
    lock, err, spent, sent = threading.Lock(), 0, [0.0], 0
    def one(q):
        if max_usd is not None and spent[0] >= max_usd:
            return None                                  # over the running budget: not sent, not written, a later run resumes here
        res = sender(q["params"])
        with lock:
            spent[0] += spent_usd(res, q["params"]["model"])
        return res
    with open(out, "a", encoding="utf-8", newline="\n") as f, concurrent.futures.ThreadPoolExecutor(workers) as ex:
        for q, res in zip(todo, ex.map(one, todo)):
            if res is None:
                continue
            sent += 1
            err += res["type"] != "succeeded"
            f.write(json.dumps({"custom_id": q["custom_id"], "result": res}, ensure_ascii=False) + "\n")
            f.flush()
    return sent, err, round(spent[0], 4)


def answer_letter(result, options):
    """-> the option letter of one batch result line, or None when it is not exactly one valid letter."""
    r = result.get("result") or {}
    if r.get("type") != "succeeded":
        return None
    text = "".join(b.get("text", "") for b in (r.get("message") or {}).get("content", []) if b.get("type") == "text").strip()
    return text if text in options else None


def collect_labels(results, side):
    """-> ([(id, char, reading)] in sidecar order, invalid count). A position with no result is invalid."""
    got = {}
    for l in results:
        j = json.loads(l)
        got[j["custom_id"]] = j
    rows, bad = [], 0
    for id_, s in side.items():
        letter = answer_letter(got[id_], s["options"]) if id_ in got else None
        bad += letter is None
        rows.append((id_, s["char"], s["options"][letter] if letter else ""))
    return rows, bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("requests")
    r.add_argument("positions")
    r.add_argument("--out", required=True)
    r.add_argument("--map")
    r.add_argument("--model", default=MODEL)
    r.add_argument("--no-temperature", action="store_true")
    r.add_argument("--chars", default=S.DEFAULT_CHARS)
    pr = sub.add_parser("price")
    pr.add_argument("batch")
    pr.add_argument("--sample", type=int, default=200)
    pr.add_argument("--seed", type=int, default=S.SEED)
    pr.add_argument("--output-tokens", type=int, help="expected output tokens per request (default: max_tokens, the upper bound)")
    sb = sub.add_parser("submit")
    sb.add_argument("batch")
    sb.add_argument("--max-usd", type=float, required=True)
    dr = sub.add_parser("direct")
    dr.add_argument("batch")
    dr.add_argument("--max-usd", type=float, required=True)
    dr.add_argument("--out", required=True)
    dr.add_argument("--workers", type=int, default=8)
    sub.add_parser("status").add_argument("batch_id")
    c = sub.add_parser("collect")
    c.add_argument("batch_id", nargs="?")
    c.add_argument("--results", help="a `direct` results file instead of a batch id")
    c.add_argument("--map", required=True)
    c.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "requests":
        side_path = a.map or a.out + ".map.json"
        if os.path.exists(a.out) or os.path.exists(side_path):
            S.B1.stop("--out or the sidecar exists")
        positions = S.read_positions(a.positions)
        readings, examples = S.load_merged(S.load_chars(a.chars))
        reqs, side, est = build_requests(positions, readings, examples, a.model, not a.no_temperature)
        with open(a.out, "x", encoding="utf-8", newline="\n") as f:
            f.writelines(json.dumps(q, ensure_ascii=False) + "\n" for q in reqs)
        with open(side_path, "x", encoding="utf-8", newline="\n") as f:
            json.dump(side, f, ensure_ascii=False)
        print(f"requests {len(reqs)}; ESTIMATED input tokens {est} (characters-based, not a tokenizer count); sidecar {side_path}")
    elif a.cmd == "price":
        e = price(a.batch, a.sample, a.seed, a.output_tokens)
        print(f"requests {e['requests']}; sampled {e['sampled']}; estimated input tokens {e['input_tokens']}; output tokens (upper bound) "
              f"{e['output_tokens_upper']}; estimated US${e['usd']} at the Batch price of {e['model']}")
    elif a.cmd == "submit":
        est = check_price(a.batch, a.max_usd)
        reqs = [json.loads(l) for l in open(a.batch, encoding="utf-8")]
        j = json.loads(api("POST", API + "/v1/messages/batches", {"requests": reqs}))
        print("batch", j["id"], j["processing_status"], "requests", len(reqs), f"estimated US${est['usd']}")
    elif a.cmd == "direct":
        est = check_price(a.batch, a.max_usd / DIRECT_FACTOR)
        n, err, usd = direct(a.batch, a.out, a.workers, max_usd=a.max_usd)
        print(f"sent {n}; errored {err}; spent US${usd} by the responses' usage (estimate US${round(est['usd'] * DIRECT_FACTOR, 4)}); results {a.out}")
    elif a.cmd == "status":
        j = json.loads(api("GET", f"{API}/v1/messages/batches/{a.batch_id}"))
        print(j["processing_status"], json.dumps(j.get("request_counts", {}), sort_keys=True))
    else:
        if a.results:
            lines = open(a.results, encoding="utf-8").read().splitlines()
        else:
            j = json.loads(api("GET", f"{API}/v1/messages/batches/{a.batch_id}"))
            if j["processing_status"] != "ended" or not j.get("results_url"):
                S.B1.stop(f"the batch is {j['processing_status']}, not ended")
            lines = api("GET", j["results_url"]).decode("utf-8").splitlines()
        side = json.load(open(a.map, encoding="utf-8"))
        rows, bad = collect_labels(lines, side)
        with open(a.out, "x", encoding="utf-8", newline="\n") as f:
            f.writelines(f"{i}\t{ch}\t{rd}\n" for i, ch, rd in rows)
        per = collections.Counter(ch for _, ch, rd in rows if not rd)
        print(f"positions {len(rows)}; valid {len(rows) - bad}; invalid {bad} ({bad / max(len(rows), 1):.4f}); characters with an invalid answer {len(per)}")
        if bad / max(len(rows), 1) > INVALID_STOP:
            print("STOP: more than 5% of the answers are not an option letter", file=sys.stderr)
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
