"""S5c driver (docs/contracts/s5c-clef-cloud.md): Cloudflare Clef-flash on S5j's public rows.

  clef_run.py --sets S[,S] [--limit N]    C-sent-fwd / C-sent-rev / C-pos, resumable

Env: CF_AI_TOKEN, CF_ACCOUNT_ID (read only after the set allowlist and the input hashes pass).
Nothing here prints sentence text, the token, the account id or the URL: stdout/stderr carry
row counts, numbers, HTTP status codes and fixed strings only.
"""
import argparse
import datetime
import glob
import hashlib
import http.client
import json
import math
import os
import sys
import time

sys.dont_write_bytecode = True  # importing s5 must not leave __pycache__ in S5j's folders
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "experiments", "s5-judges"))
import s5  # noqa: E402  constants and pure functions only: INSTR, POS_INSTR, BATCH, positions, pos_decision, mcnemar, lenient_fn

ALLOW = ("cvtune", "dev302", "typing76")  # contract section 4; discordtune never reaches the key or the network
HOST = "api.cloudflare.com"
MODEL = "clef-flash"
GATEWAY = "default"  # AI Gateway id; Workers AI billing there is set to Unified billing (2026-10-06)
PRICE_PER_TOKEN = 0.09 / 1e6
# contract section 10: --model clef is the 27B; its files carry the clef27- prefix so flash's stay untouched
PRICE_27B = 0.24 / 1e6
MODELS = {"clef-flash": ("clef", PRICE_PER_TOKEN), "clef": ("clef27", PRICE_27B)}  # model -> (file prefix, USD per token)
BUDGET_USD = 1.0
BACKOFF = (2, 4, 8)  # seconds before retry 1, 2, 3
J_CACHE = os.path.expanduser("~/.cache/shanjie/work/s5-judges")
J_RESULTS = os.path.join(ROOT, "experiments", "s5-judges", "results")
C_CACHE = os.path.expanduser("~/.cache/shanjie/work/s5-clef")
C_RESULTS = os.path.join(HERE, "results")
CONDS = ("sent-fwd", "sent-rev", "pos")

# contract section 2: S5j inputs, read-only, hash checked before they are opened
SHA = {
    "cvtune": {
        "rows.jsonl": "98eec57ff74ca1cc423804c3cfa1afb250314f3eb32c869f728e60a1ce4e170d",
        "jev-sent-fwd.jsonl": "ea8b5637606e0589758b7d51356ce1a29d21af343fbd02e1e6bcf3d64b925594",
        "jev-sent-rev.jsonl": "9cb4e99dcd1b18707b0c8cf1e64c4dcde8646a102a3b24cd55349ce36b735462",
        "jev-pos.jsonl": "cfea93c3db4154a1f391c4c5d4e81fa7a7a8073256ad4929906e12eadcdd70a0",
        "score.json": "e6420bd25f37bbf9fa43079fcb3a797b1641ecb6e4ca471cae1e72e69cf3cef0",
    },
    "dev302": {
        "rows.jsonl": "70aaee641fa78b757683c47595197745075d69b92ccb0b9d5846cd39e7f65c39",
        "jev-sent-fwd.jsonl": "5a909cf09c3539609306614ebf3b0a658df18e460935380615f4dc3ac3b5fd5f",
        "jev-sent-rev.jsonl": "a0a2d6c3a832b7319bbf3853fdefc48406e3ff1b433b206248d9567e768cab53",
        "jev-pos.jsonl": "844f3a43033df8a6b7abb7703d9b42ac636c3a88567138a4f4fb29aa411f9195",
        "score.json": "2a2ebeb3f5b03ed447021cf7ff7c7deba7f98977bb47d543a79ac57a7f711373",
    },
    "typing76": {
        "rows.jsonl": "ed0bb91342ad9bc64cefe4189e809725e0fca5b3acbe7eb8ef2919640c7bd34a",
        "jev-sent-fwd.jsonl": "0577a26da9a7a77b6e6ca23c45a4cf9ace25e31f60c9174246548e17ca534e61",
        "jev-sent-rev.jsonl": "32bb17c4c20eca5382439a6a2a7c7e55cef2abc383b49b6e5511e334fbd75fca",
        "jev-pos.jsonl": "37f22a6a86589dbf3c554bc60bc23eb79e7d9cf6eebd8be445c46ab45aab04a0",
        "score.json": "599873911d9d3f2447c51ab1b2a0077506c9e8d6eed3a27015bd1d3ae77ee5fb",
    },
}


class Stop(Exception):
    """A contract section 7 stop. The message is a fixed string: never row text, token, id or URL."""


def die(msg):
    sys.exit(f"s5c: {msg}")


def verified_text(name, fn):
    """Read an S5j file only if its SHA-256 matches the table; hash and parse the same bytes."""
    base = os.path.join(J_CACHE, name) if name == "cvtune" else os.path.join(J_RESULTS, name)
    try:
        with open(os.path.join(base, fn), "rb") as f:
            data = f.read()
    except OSError:
        data = None
    if data is None or hashlib.sha256(data).hexdigest() != SHA[name][fn]:
        die(f"input {name}/{fn} missing or hash mismatch")  # never regenerated
    return data.decode("utf-8")


def load_rows(name):
    return [json.loads(l) for l in verified_text(name, "rows.jsonl").split("\n") if l.strip()]


def out_dir(name, limit):
    base = C_CACHE if name == "cvtune" or limit else C_RESULTS  # smoke (-nN) never goes into the repo
    return os.path.join(base, name + (f"-n{limit}" if limit else ""))


# ---------------------------------------------------------------- HTTP
def default_post(path, headers, body):
    conn = http.client.HTTPSConnection(HOST, timeout=60)
    try:
        conn.request("POST", path, body, headers)
        r = conn.getresponse()
        return r.status, r.read()
    finally:
        conn.close()


class Client:
    """post(path, headers, body) -> (status, bytes) is injectable; timeouts and socket errors raise OSError."""

    def __init__(self, token, account, post=default_post, sleep=time.sleep, model=MODEL, gateway=None):
        self.path = f"/client/v4/accounts/{account}/ai/run/@cf/cloudflare/{model}"
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        if gateway:  # route through AI Gateway so prepaid Unified Billing credits pay once the free allocation is used up
            self.headers["cf-aig-gateway-id"] = gateway
        self.post, self.sleep = post, sleep
        self.model = model  # S5p overrides model, path and headers to reuse this retry/parse path for Jev

    def ask(self, state, questions):
        """-> (answers, input tokens, tokens are an estimate, response model, seconds of the successful attempt)."""
        body = json.dumps({"state": state, "model": self.model, "questions": questions}, ensure_ascii=False).encode("utf-8")
        for attempt in range(len(BACKOFF) + 1):
            t = time.perf_counter()
            try:
                status, data = self.post(self.path, self.headers, body)
            except (OSError, http.client.HTTPException):
                status, data = None, b""  # never keep the exception: its text can carry the URL
            secs = time.perf_counter() - t
            if status == 200:
                break
            if status in (401, 403):
                raise Stop(f"HTTP {status}: token or permission")
            if status == 404:
                raise Stop("HTTP 404: model or endpoint not found")
            if status is not None and status != 429 and status < 500:
                raise Stop(f"HTTP {status}")
            if attempt == len(BACKOFF):
                raise Stop(f"{status or 'timeout/connection error'} after {len(BACKOFF)} retries")
            self.sleep(BACKOFF[attempt])
        try:
            j = json.loads(data)
            res = j.get("result", j)
            if j.get("success") is False or not isinstance(res.get("answers"), dict):
                raise ValueError
        except (ValueError, AttributeError):
            raise Stop("response is not the expected JSON (no answers)") from None
        usage = res.get("usage") or {}
        tok = usage.get("input_tokens", usage.get("prompt_tokens")) if isinstance(usage, dict) else None
        est = not isinstance(tok, int)
        model = res.get("model", j.get("model"))
        return (res["answers"], len(body.decode("utf-8")) if est else tok, est,
                model if isinstance(model, str) and len(model) < 80 else None, secs)


# ---------------------------------------------------------------- decoding
def probs_of(ans, crit):
    """The per-option probability dict, found exactly as s5.pos_decision finds it; None when any option lacks one."""
    if not isinstance(ans, dict):
        return None
    return next((v for v in ans.values() if isinstance(v, dict) and set(crit) <= set(v)
                 and all(isinstance(x, (int, float)) for x in v.values())), None)


def parse_sent(ans, crit):
    """-> (index into the presented order, choice agrees with the top probability) or (None, None) = parse failure.
    The `choice` field wins when it is valid (S5j's J-sent used it); otherwise the highest probability, ties to the lowest number."""
    probs = probs_of(ans, crit)
    if probs is None:
        return None, None
    top = max(range(len(crit)), key=lambda j: (probs[crit[j]], -j))
    ch = ans.get("choice")
    if isinstance(ch, str) and ch in crit:
        j = crit.index(ch)
        return j, j == top
    return top, None


def parse_pos(ans, crit):
    """-> [index, "prob"] by S5j's J-pos rule, or None. pos_decision silently falls back to `choice` without probabilities: check first."""
    if probs_of(ans, crit) is None:
        return None
    j, mode = s5.pos_decision(ans, crit)
    return [j, mode]


# ---------------------------------------------------------------- run
def read_recs(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f.read().split("\n") if l.strip()]


def jobs_of(rows):
    jobs = {c: [] for c in CONDS}
    for k, r in enumerate(rows):
        if len(r["cands"]) < 2:
            continue
        jobs["sent-fwd"].append((k, r["ctx"], r["cands"], s5.INSTR))
        jobs["sent-rev"].append((k, r["ctx"], r["cands"][::-1], s5.INSTR))
        for p, opts in s5.positions(r):
            jobs["pos"].append((f"{k}:{p}", "", opts, s5.POS_INSTR))
    return jobs


def spent_usd(dirs):
    """Total input-token cost of every clef-*.jsonl (0.09/M) and clef27-*.jsonl (0.24/M) in the given dirs (one shared cap)."""
    return sum(r["tokens"] * price for d in dirs for prefix, price in MODELS.values() for c in CONDS
               for r in read_recs(os.path.join(d, f"{prefix}-{c}.jsonl")))


def known_dirs():
    """Every Clef output this experiment may have produced: cache (cvtune, smoke -nN) and repo results."""
    return sorted(glob.glob(os.path.join(C_CACHE, "*")) + glob.glob(os.path.join(C_RESULTS, "*")))


PARSE_CHECK_MIN = 200  # contract section 7's 1% rule needs a sample: checked from 200 answered items, or at the condition's end


def run_set(name, rows, d, client, dirs=(), prefix="clef", price=PRICE_PER_TOKEN):
    os.makedirs(d, exist_ok=True)
    spent = spent_usd(sorted({*dirs, d}))
    for cond, items in jobs_of(rows).items():
        outp = os.path.join(d, f"{prefix}-{cond}.jsonl")
        recs = read_recs(outp)
        done = {x for r in recs for x in r["keys"]}
        fails = sum(x is None for r in recs for x in r.get("decisions", r.get("picks", [])))
        total = sum(len(r["keys"]) for r in recs)
        todo = [it for it in items if str(it[0]) not in done]
        for b in range(0, len(todo), s5.BATCH):
            if spent >= BUDGET_USD:  # before sending: a resumed run that is already over budget sends nothing
                raise Stop("budget exceeded")
            chunk = todo[b:b + s5.BATCH]
            state = {"rows": [{"context": c} for _, c, _, _ in chunk]}
            qs = {f"q{n}": {"type": "choice", "instructions": ins.format(i=n) if "{i}" in ins else ins,
                            "criteria": {f"c{j + 1}": s for j, s in enumerate(cs)}}
                  for n, (_, _, cs, ins) in enumerate(chunk)}
            answers, tok, est, model, secs = client.ask(state, qs)
            rec = {"keys": [str(k) for k, *_ in chunk], "secs": secs, "tokens": tok, "tok_est": est, "model": model,
                   "date": datetime.date.today().isoformat(),
                   "answers": [answers.get(f"q{n}") for n in range(len(chunk))]}
            crits = [list(qs[f"q{n}"]["criteria"]) for n in range(len(chunk))]
            if cond == "pos":
                rec["decisions"] = [parse_pos(a, c) for a, c in zip(rec["answers"], crits)]
                parsed = rec["decisions"]
            else:
                pa = [parse_sent(a, c) for a, c in zip(rec["answers"], crits)]
                rec["picks"], rec["agree"] = [p for p, _ in pa], [g for _, g in pa]
                parsed = rec["picks"]
            if all(x is None for x in parsed):  # the format differs from the assumption: report, do not interpret, do not record
                raise Stop("no per-option probabilities in the response")
            with open(outp, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fails += sum(x is None for x in parsed)
            total += len(chunk)
            spent += tok * price
            if (total >= PARSE_CHECK_MIN or b + s5.BATCH >= len(todo)) and fails / total > 0.01:
                raise Stop(f"parse failures above 1% in {cond} ({fails}/{total})")
        print(f"{prefix} {name} {cond}: items={len(items)} new_requests={math.ceil(len(todo) / s5.BATCH)} spent_usd={spent:.4f}")


def smoke_report(d, prefix="clef"):
    """Print answer field names, first-pick ratios (presented-first, as S5j) and apply the 0.95 stop. Reads only <prefix>-* files."""
    fp = {}
    for c in ("sent-fwd", "sent-rev"):
        recs = read_recs(os.path.join(d, f"{prefix}-{c}.jsonl"))
        pk = [x for r in recs for x in r["picks"] if x is not None]
        fp[c] = sum(x == 0 for x in pk) / len(pk) if pk else None
    a0 = (read_recs(os.path.join(d, f"{prefix}-sent-fwd.jsonl")) or [{"answers": [None]}])[0]["answers"][0]
    ok = lambda ks: sorted(k for k in ks if isinstance(k, str) and k.isascii() and k.isidentifier() and len(k) < 32)  # noqa: E731
    sub = next((v for v in a0.values() if isinstance(v, dict)), {}) if isinstance(a0, dict) else {}
    print(f"smoke answer_fields={ok(a0 if isinstance(a0, dict) else [])} prob_keys={ok(sub)} "
          f"first_pick fwd={fp['sent-fwd']} rev={fp['sent-rev']}")
    if all(v is not None and v >= 0.95 for v in fp.values()):
        raise Stop("C-sent first-pick ratio >= 0.95 in both orders (parser broken, or the model picks by position)")


def main(argv=None, post=default_post, sleep=time.sleep, rows_for=load_rows, out_for=out_dir, dirs_for=known_dirs):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", required=True)
    ap.add_argument("--model", choices=tuple(MODELS), default=MODEL, help="clef-flash (default, 9B) or clef (27B, clef27- files)")
    ap.add_argument("--limit", type=int, default=0, help="first N rows of the set (smoke; dev302 or typing76 only)")
    a = ap.parse_args(argv)
    sets = a.sets.split(",")
    if any(s not in ALLOW for s in sets):  # FIRST: before the environment is read or any connection opened
        die("set not on the allowlist (cvtune, dev302, typing76)")
    if a.limit and "cvtune" in sets:
        die("--limit is for dev302 and typing76 only")
    rows = {s: rows_for(s) for s in sets}  # hashes verified here
    token, account = os.environ.get("CF_AI_TOKEN"), os.environ.get("CF_ACCOUNT_ID")
    if not token or not account:
        die("CF_AI_TOKEN and CF_ACCOUNT_ID must be set in the environment")
    prefix, price = MODELS[a.model]
    client = Client(token, account, post, sleep, a.model, gateway=GATEWAY)
    for s in sets:
        d = out_for(s, a.limit)
        run_set(s, rows[s][:a.limit or None], d, client, dirs_for(), prefix, price)
        if a.limit:
            smoke_report(d, prefix)


def cli(argv=None, **kw):
    try:
        main(argv, **kw)
    except SystemExit:
        raise
    except Stop as e:
        sys.exit(f"s5c: stop: {e}")
    except Exception as e:  # never print e: messages can carry row text, the URL or the token
        sys.exit(f"s5c: unexpected {type(e).__name__}")


if __name__ == "__main__":
    cli()
