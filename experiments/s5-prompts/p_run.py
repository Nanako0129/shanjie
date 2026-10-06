"""S5p driver (docs/contracts/s5p-cloud-prompts.md): prompt variants V1-V4 on Jev (jev-1.13.0) or Clef-flash.

  p_run.py --provider jev|clef|clef27 --sets S[,S] [--half A|B] [--variants v1,v2,v3,v4] [--limit N]
  p_run.py --print-request          V1-V4 request JSON for dev302 row 0 (CC0), no key, no network
  p_run.py --check-examples         example-sentence overlap check, exit 1 on any hit

One request per row; rows with a single candidate are not sent. Reuses S5c's Client (retry, error text).
Nothing here prints sentence text (except --print-request), keys, account ids or URLs.
"""
import argparse
import datetime
import glob
import hashlib
import http.client
import json
import os
import sys
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "s5-clef"))
import clef_run as R  # noqa: E402

ROOT = R.ROOT
ALLOW = R.ALLOW
CACHE = os.path.expanduser("~/.cache/shanjie/work/s5-prompts")
RESULTS = os.path.join(HERE, "results")
BUDGET_USD = 2.0
JEV_CAP = 6000  # contract section 8: Jev has no published price, so a hard request cap
JEV_MODEL, JEV_HOST, JEV_PATH = "jev-1.13.0", "api.typesafe.ai", "/v1/systemone"
JEV_USD_PER_Q = 0.004 / 300  # S5j's billed J1 (300 questions, about US$0.004); reporting estimate only
JEV_KEY_FILE = "~/.config/typesafe/api_key"
# contract section 12: SHA-256 of ~/.cache/shanjie/work/s5-clef/cvtune/clef-sent-fwd.jsonl. None until main fills it in.
CLEF_V0_SHA = "fa53ba7ccc67d217c5019896539a2dceced13f4be72d7d0fec54926ed834e074"  # recorded 2026-10-06
CLEF_V0_PATH = os.path.join(R.C_CACHE, "cvtune", "clef-sent-fwd.jsonl")
# contract section 13: the 27B's own V0 (clef27- prefix). None until main records it in section 12 after the S5c section 10 cvtune run.
CLEF27_V0_SHA = None
CLEF27_V0_PATH = os.path.join(R.C_CACHE, "cvtune", "clef27-sent-fwd.jsonl")
PRICE = {"clef": R.PRICE_PER_TOKEN, "clef27": R.PRICE_27B}  # USD per input token; Jev is priced per question below
VARIANTS = {"v1": ["v1"], "v2": ["v2f", "v2r"], "v3": ["v3"], "v4": ["v4f", "v4r"]}

# ---------------------------------------------------------------- texts (contract section 4, verbatim)
EX_TRUE = ["我等一下再打給你", "明天的會議改到下午三點"]
EX_FALSE = ["我等一下在打給你", "明天的會意改到下午三點", "這間店的拉面很好吃"]
V1_INS = ("A user in Taiwan typed the Zhuyin (Bopomofo) reading `reading`. The text already written before it is "
          "`context` (it may be empty). Is `candidates.{c}` exactly the sentence the user meant to type?")
V1_CRIT = {
    "true": {"what": "correct Traditional Chinese characters as used in Taiwan, grammatical and sensible given the "
                     "context, and matching the reading", "examples": EX_TRUE},
    "false": {"what": "a same-sounding sentence with a wrong character, a simplified or mainland form, or a phrase "
                      "that does not make sense", "not_for": "sentences that are merely informal but correct",
              "examples": EX_FALSE},
}
V2_INS = ("A user in Taiwan typed the Zhuyin (Bopomofo) reading `reading`. The text already written before it is "
          "`context` (it may be empty). Every option is a sentence with that same reading. Choose the one the user "
          "meant: correct Traditional Chinese as used in Taiwan, grammatical, and sensible in context.")
V3_INS = "台灣的使用者用注音輸入法打了讀音 `reading`，之前已經寫的文字是 `context`（可能是空的）。`candidates.{c}` 是不是使用者真正要打的那一句？"
V3_CRIT = {
    "true": {"what": "台灣使用的正確繁體字、合乎文法，也符合前文與讀音", "examples": EX_TRUE},
    "false": {"what": "讀音相同但有錯字、簡體或大陸用語，或意思不通的句子", "not_for": "只是口語、但用字正確的句子",
              "examples": EX_FALSE},
}
V4_INS = ("台灣的使用者用注音輸入法打了讀音 `reading`，之前已經寫的文字是 `context`（可能是空的）。每個選項都是同一個讀音的句子。"
          "選出使用者真正要打的那一句：台灣使用的正確繁體字、合乎文法、在前文下說得通。")


def die(msg):
    sys.exit(f"s5p: {msg}")


# ---------------------------------------------------------------- requests
def build(variant, row):
    """-> (state, questions). Forward only for v1/v3; v2r/v4r present the candidates reversed (c1 = last candidate)."""
    cands = row["cands"][::-1] if variant.endswith("r") else row["cands"]
    ids = [f"c{j + 1}" for j in range(len(cands))]
    state = {"context": row["ctx"], "reading": " ".join(row["syls"]), "candidates": dict(zip(ids, cands))}
    if variant in ("v1", "v3"):
        ins, crit = (V1_INS, V1_CRIT) if variant == "v1" else (V3_INS, V3_CRIT)
        return state, {f"q_{c}": {"type": "noul", "instructions": ins.format(c=c), "criteria": crit} for c in ids}
    ins = V2_INS if variant.startswith("v2") else V4_INS
    return state, {"q": {"type": "choice", "instructions": ins, "criteria": dict(zip(ids, cands))}}


# ---------------------------------------------------------------- decoding
def p_true(ans):
    """P(true) of a noul answer, else None. Jev returns it as the numeric `noul` field ({"type": "noul", "noul": 0.75},
    measured 2026-10-06 with a self-written sentence); a `true` entry of a probabilities dict is also accepted."""
    num = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)  # noqa: E731
    if not isinstance(ans, dict):
        return None
    if num(ans.get("noul")):
        return float(ans["noul"])
    if num(ans.get("true")):
        return float(ans["true"])
    return next((float(v["true"]) for v in ans.values() if isinstance(v, dict) and num(v.get("true"))), None)


def decode(variant, answers, k):
    """-> dict(pick = index into the ORIGINAL candidate order or None = parse failure, pj = presented index, tie, all_same).
    v1/v3: highest P(true), ties to the earlier-ranked candidate, flagged. v2/v4: S5c's parse_sent, reversed order mapped back."""
    if variant in ("v1", "v3"):
        ps = [p_true(answers.get(f"q_c{n + 1}")) for n in range(k)]
        if any(p is None for p in ps):
            return {"pick": None, "pj": None, "tie": None, "all_same": None}
        top = max(ps)
        j = ps.index(top)
        return {"pick": j, "pj": j, "tie": ps.count(top) > 1, "all_same": len(set(ps)) == 1}
    j, _ = R.parse_sent(answers.get("q"), [f"c{n + 1}" for n in range(k)])
    if j is None:
        return {"pick": None, "pj": None, "tie": None, "all_same": None}
    return {"pick": k - 1 - j if variant.endswith("r") else j, "pj": j, "tie": None, "all_same": None}


# ---------------------------------------------------------------- budget
def read_recs(path):
    return R.read_recs(path)


def spent(dirs):
    """-> (US$ estimate, Jev request count) over every s5p provider file in the dirs. Clef: input tokens x price; Jev: questions x S5j's J1 rate."""
    usd, jev_req = 0.0, 0
    for d in dirs:
        for f in glob.glob(os.path.join(d, "*-v*.jsonl")):
            prov = os.path.basename(f).split("-")[0]
            for r in read_recs(f):
                if prov in PRICE:
                    usd += r["tokens"] * PRICE[prov]
                else:
                    usd += r["nq"] * JEV_USD_PER_Q
                    jev_req += 1
    return usd, jev_req


def known_dirs():
    return sorted(glob.glob(os.path.join(CACHE, "*")) + glob.glob(os.path.join(RESULTS, "*")))


def out_dir(name, limit):
    base = CACHE if name == "cvtune" or limit else RESULTS  # smoke (-nN) and cvtune stay out of the repo
    return os.path.join(base, name + (f"-n{limit}" if limit else ""))


# ---------------------------------------------------------------- HTTP / keys
def jev_post(path, headers, body):
    conn = http.client.HTTPSConnection(JEV_HOST, timeout=60)
    try:
        conn.request("POST", path, body, headers)
        r = conn.getresponse()
        return r.status, r.read()
    finally:
        conn.close()


def read_key_file():
    with open(os.path.expanduser(JEV_KEY_FILE), encoding="utf-8") as f:
        return f.read()


def make_client(provider, post, sleep):
    """Keys are read here, after the allowlist and the hash checks."""
    if provider in PRICE:
        token, account = os.environ.get("CF_AI_TOKEN"), os.environ.get("CF_ACCOUNT_ID")
        if not token or not account:
            die("CF_AI_TOKEN and CF_ACCOUNT_ID must be set in the environment")
        return R.Client(token, account, post or R.default_post, sleep, "clef" if provider == "clef27" else R.MODEL,
                        gateway=R.GATEWAY)
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        try:
            key = read_key_file()
        except OSError:
            die("TYPESAFE_API_KEY is not set and the key file is not readable")
    key = key.strip()
    if not key:
        die("empty Jev key")
    c = R.Client(key, "", post or jev_post, sleep)
    c.path, c.model = JEV_PATH, JEV_MODEL
    return c


def _check_v0(sha, path, label, fname):
    if sha is None:
        die(f"{label} V0 hash is not recorded in contract section 12 yet")
    try:
        with open(path, "rb") as f:
            ok = hashlib.sha256(f.read()).hexdigest() == sha
    except OSError:
        ok = False
    if not ok:
        die(f"input {fname} missing or hash mismatch")


def check_clef_v0():
    """Contract section 12: refuse to run until the hash is recorded, and exit non-zero when the file differs."""
    _check_v0(CLEF_V0_SHA, CLEF_V0_PATH, "Clef", "clef-sent-fwd.jsonl")


def check_clef27_v0():
    """Contract section 13: the same check for the 27B's own V0 constant and path."""
    _check_v0(CLEF27_V0_SHA, CLEF27_V0_PATH, "Clef27", "clef27-sent-fwd.jsonl")


# ---------------------------------------------------------------- run
def run_variant(provider, variant, rows, d, client, half, state):
    os.makedirs(d, exist_ok=True)
    outp = os.path.join(d, f"{provider}-{variant}.jsonl")
    recs = read_recs(outp)
    done = {r["key"] for r in recs}
    fails, total = sum(r["pick"] is None for r in recs), len(recs)
    todo = [k for k, r in enumerate(rows) if len(r["cands"]) >= 2 and k not in done and half in (None, r["half"])]
    for n, k in enumerate(todo):
        if state["jev_req"] >= JEV_CAP and provider == "jev":
            raise R.Stop(f"Jev request cap {JEV_CAP} reached")
        if state["usd"] >= BUDGET_USD:
            raise R.Stop("budget exceeded")
        st, qs = build(variant, rows[k])
        answers, tok, est, model, secs = client.ask(st, qs)
        rec = {"key": k, "variant": variant, "nq": len(qs), "tokens": tok, "tok_est": est, "secs": secs, "model": model,
               "date": datetime.date.today().isoformat(), "answers": answers,
               **decode(variant, answers, len(rows[k]["cands"]))}
        with open(outp, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        total += 1
        fails += rec["pick"] is None
        if fails == total == 3:  # the format differs from the assumption: report, do not interpret
            raise R.Stop("no per-option probabilities in the response")
        if provider == "jev":
            state["jev_req"] += 1
            state["usd"] += len(qs) * JEV_USD_PER_Q
        else:
            state["usd"] += tok * PRICE[provider]
        if (total >= R.PARSE_CHECK_MIN or n + 1 == len(todo)) and fails / total > 0.01:
            raise R.Stop(f"parse failures above 1% in {variant} ({fails}/{total})")
    print(f"{provider} {os.path.basename(d)} {variant}: requests_new={len(todo)} spent_usd_est={state['usd']:.4f} "
          f"jev_requests={state['jev_req']}")


def smoke_report(provider, d, variants):
    """Contract section 9 smoke stops. Ratios are printed first."""
    bad = []
    for v in variants:
        recs = read_recs(os.path.join(d, f"{provider}-{v}.jsonl"))
        if not recs:
            continue
        pk = [r for r in recs if r["pick"] is not None]
        if v in ("v1", "v3"):
            c1 = sum(r["pick"] == 0 for r in pk) / max(1, len(pk))
            same = sum(bool(r["all_same"]) for r in pk) / max(1, len(pk))
            print(f"smoke {v}: pick_c1={c1:.3f} all_p_true_identical={same:.3f}")
            if c1 >= 0.95 or same >= 0.95:
                bad.append(v)
        else:
            print(f"smoke {v}: first_presented={sum(r['pj'] == 0 for r in pk) / max(1, len(pk)):.3f}")
    for base in ("v2", "v4"):
        fr = [read_recs(os.path.join(d, f"{provider}-{base}{s}.jsonl")) for s in "fr"]
        if all(fr) and all(sum(r["pj"] == 0 for r in x if r["pick"] is not None) / max(1, len(x)) >= 0.95 for x in fr):
            bad.append(base)
    if bad:
        raise R.Stop(f"smoke: variant(s) {','.join(bad)} pick the first option at least 95% of the time (or identical P(true))")


def main(argv=None, post=None, sleep=time.sleep, rows_for=R.load_rows, out_for=out_dir, dirs_for=known_dirs):
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=("jev", "clef", "clef27"))
    ap.add_argument("--sets")
    ap.add_argument("--half", choices=("A", "B"))
    ap.add_argument("--variants", default="v1,v2,v3,v4")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--print-request", action="store_true")
    ap.add_argument("--check-examples", action="store_true")
    a = ap.parse_args(argv)
    if a.check_examples:
        n = example_overlaps()
        print(f"example overlaps: {n}")
        sys.exit(1 if n else 0)
    if a.print_request:
        row = rows_for("dev302")[0]
        for v in ("v1", "v2f", "v2r", "v3", "v4f", "v4r"):
            print(v, json.dumps(dict(zip(("state", "questions"), build(v, row))), ensure_ascii=False))
        return
    if not a.provider or not a.sets:
        die("--provider and --sets are required")
    sets = a.sets.split(",")
    if any(s not in ALLOW for s in sets):  # FIRST: before any key, hash or connection
        die("set not on the allowlist (cvtune, dev302, typing76)")
    if a.limit and "cvtune" in sets:
        die("--limit is for dev302 and typing76 only")
    if "cvtune" in sets and not a.half:
        die("cvtune needs --half A or B")
    if any(x not in VARIANTS for x in a.variants.split(",")):
        die("unknown variant")
    vs = [v for x in a.variants.split(",") for v in VARIANTS[x]]
    if a.provider == "clef":
        check_clef_v0()
    elif a.provider == "clef27":
        check_clef27_v0()
    rows = {s: rows_for(s) for s in sets}  # hashes verified here
    client = make_client(a.provider, post, sleep)
    for s in sets:
        d = out_for(s, a.limit)
        usd, jr = spent(sorted({*dirs_for(), d}))
        state = {"usd": usd, "jev_req": jr}
        for v in vs:
            run_variant(a.provider, v, rows[s][:a.limit or None], d, client, a.half if s == "cvtune" else None, state)
        if a.limit:
            smoke_report(a.provider, d, vs)


# ---------------------------------------------------------------- example overlap (contract section 8 g)
def example_overlaps():
    """Count example sentences found in eval/dev, eval/sets (any substring) or a prep file's truth/cands. Never reads eval/holdout."""
    texts = []
    for sub in ("dev", "sets"):
        for f in glob.glob(os.path.join(ROOT, "eval", sub, "*")):
            if os.path.isfile(f):
                with open(f, encoding="utf-8", errors="replace") as fh:
                    texts.append(fh.read())
    for name in ALLOW:
        for r in R.load_rows(name):
            texts.append(r["truth"] + "\n" + "\n".join(r["cands"]))
    blob = "\n".join(texts)
    return sum(s in blob for s in EX_TRUE + EX_FALSE)


def cli(argv=None, **kw):
    try:
        main(argv, **kw)
    except SystemExit:
        raise
    except R.Stop as e:
        sys.exit(f"s5p: stop: {e}")
    except Exception as e:  # never print e: messages can carry row text, the URL or the key
        sys.exit(f"s5p: unexpected {type(e).__name__}")


if __name__ == "__main__":
    cli()
