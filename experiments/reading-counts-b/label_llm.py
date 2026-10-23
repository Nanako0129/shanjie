#!/usr/bin/env python3
"""The labeller of step 2 (README.md in this directory): one Message Batches request per position, the model picks the character's reading from the
merged lexicon's single-character readings. Standard library only (urllib); the key is read from the environment variable ANTHROPIC_API_KEY, sent
only as the `x-api-key` header to api.anthropic.com, and never printed, logged or written.

  label_llm.py requests POSITIONS.tsv --out batch.jsonl [--map SIDECAR.json] [--model claude-opus-5-5] [--no-temperature] [--chars step2-chars.txt]
  label_llm.py submit batch.jsonl                    POST /v1/messages/batches, prints the batch id
  label_llm.py status BATCH_ID                       GET /v1/messages/batches/{id}, prints the status and the request counts
  label_llm.py collect BATCH_ID --map SIDECAR.json --out labels.tsv

POSITIONS.tsv: `id, source, sentence, pos, char` (sample_single.py `sample` or `human`; more columns are ignored). `requests` writes one JSONL line
per position (custom_id = the id, max_tokens 8, temperature 0 unless --no-temperature, no thinking) and the sidecar {id: {char, options: {letter:
reading}}} (default <out>.map.json), and prints the request count and a ROUGH input-token estimate (characters, not a tokenizer: 1.5 per non-ASCII
character and 0.3 per ASCII one; get the exact count from the count_tokens endpoint before submitting if the budget is tight).
Options A, B, C... are the character's single-character readings in the merged lexicon (score descending, ties in lexicon order), each with up to 3
words of >= 2 characters that read it that way (highest score first, ties by the word) or just the Zhuyin.
`collect` does not wait: the batch must have ended (exit 1 otherwise). It writes `id, char, reading` for every position of the sidecar; the reading is
empty for an answer that is not exactly one valid option letter after stripping whitespace (also for an errored, expired or missing result), prints the
counts, and exits 3 when more than 5% are invalid (the registration's stop). Exit 0 done, 1 stop or API error.
"""
import argparse, collections, json, os, re, sys, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sample_single as S  # noqa: E402

API = "https://api.anthropic.com"
VERSION = "2023-06-01"
MODEL = "claude-opus-5-5"
MAX_TOKENS = 8
INVALID_STOP = 0.05
ID_OK = re.compile(r"^[A-Za-z0-9_-]{1,64}$")   # the API's custom_id rule
PROMPT = ("下面這個句子裡，用【】標出的字讀哪一個音？\n\n句子：{sentence}\n\n選項：\n{options}\n\n只回答一個選項代號（{letters}），不要解釋。")


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


def build_requests(positions, readings, examples, model=MODEL, temperature=True):
    """-> ([request dict], sidecar, estimated input tokens). A position whose character is not in `readings` stops the build."""
    reqs, side, est = [], {}, 0.0
    for id_, _, sentence, pos, char, *_ in positions:
        if not ID_OK.match(id_) or id_ in side:
            S.B1.stop(f"id {id_!r}: not a valid or not a unique custom_id")
        if sentence[pos:pos + 1] != char or char not in readings:
            S.B1.stop(f"{id_}: the character is not at pos, or not in the lexicon list")
        opts, text = option_lines(char, readings, examples)
        prompt = PROMPT.format(sentence=sentence[:pos] + "【" + char + "】" + sentence[pos + 1:], options=text, letters="、".join(l for l, _ in opts))
        params = {"model": model, "max_tokens": MAX_TOKENS, "messages": [{"role": "user", "content": prompt}]}
        if temperature:
            params["temperature"] = 0
        reqs.append({"custom_id": id_, "params": params})
        side[id_] = {"char": char, "options": dict(opts)}
        est += sum(1.5 if ord(c) > 127 else 0.3 for c in prompt)
    return reqs, side, int(est)


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
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        S.B1.stop(f"{method} {url[len(API):]}: HTTP {e.code}")
    except urllib.error.URLError as e:
        S.B1.stop(f"{method} {url[len(API):]}: {type(e.reason).__name__}")


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
    sub.add_parser("submit").add_argument("batch")
    sub.add_parser("status").add_argument("batch_id")
    c = sub.add_parser("collect")
    c.add_argument("batch_id")
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
    elif a.cmd == "submit":
        reqs = [json.loads(l) for l in open(a.batch, encoding="utf-8")]
        j = json.loads(api("POST", API + "/v1/messages/batches", {"requests": reqs}))
        print("batch", j["id"], j["processing_status"], "requests", len(reqs))
    elif a.cmd == "status":
        j = json.loads(api("GET", f"{API}/v1/messages/batches/{a.batch_id}"))
        print(j["processing_status"], json.dumps(j.get("request_counts", {}), sort_keys=True))
    else:
        j = json.loads(api("GET", f"{API}/v1/messages/batches/{a.batch_id}"))
        if j["processing_status"] != "ended" or not j.get("results_url"):
            S.B1.stop(f"the batch is {j['processing_status']}, not ended")
        side = json.load(open(a.map, encoding="utf-8"))
        rows, bad = collect_labels(api("GET", j["results_url"]).decode("utf-8").splitlines(), side)
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
