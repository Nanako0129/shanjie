"""S5k shared code (docs/contracts/s5k-local-scorers.md): hash-checked inputs, rows, output paths, checks.

Nothing here prints sentence text. Never calls s5.py's prep(), read_rows() or set_dir().
"""
import hashlib
import json
import os
import sys
import time

os.environ["HF_HUB_OFFLINE"] = "1"  # contract §1: models load from local paths only
os.environ["TRANSFORMERS_OFFLINE"] = "1"

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "experiments", "s5-judges"))
sys.path.insert(0, os.path.join(ROOT, "reference", "proto"))

HOME = os.path.expanduser("~")
PRIVATE = os.environ.get("SHANJIE_PRIVATE", "/nonexistent-set-SHANJIE_PRIVATE")  # root of the private data dir; only the discordtune runs need it
J_PRIV = PRIVATE + "/s5-judges"
J_CACHE = HOME + "/.cache/shanjie/work/s5-judges"
J_REPO = ROOT + "/experiments/s5-judges/results"
L_PRIV = PRIVATE + "/s5-local"
L_CACHE = HOME + "/.cache/shanjie/work/s5-local"
L_REPO = HERE + "/results"
MODELS = HOME + "/.cache/shanjie/models"
SYNTH_CTX = "我們等一下要去吃飯，"  # contract §8.2(h); public smoke sets only
TOP = 8

# contract §2: (set, file) -> (path, sha256)
HASHES = {
    ("discordtune", "rows.jsonl"): (J_PRIV + "/discordtune/rows.jsonl", "8217e3242c3b302b55485f9d65ffba9e5dcca47130dca0b885da63e8d761bbd9"),
    ("cvtune", "rows.jsonl"): (J_CACHE + "/cvtune/rows.jsonl", "98eec57ff74ca1cc423804c3cfa1afb250314f3eb32c869f728e60a1ce4e170d"),
    ("dev302", "rows.jsonl"): (J_REPO + "/dev302/rows.jsonl", "70aaee641fa78b757683c47595197745075d69b92ccb0b9d5846cd39e7f65c39"),
    ("typing76", "rows.jsonl"): (J_REPO + "/typing76/rows.jsonl", "ed0bb91342ad9bc64cefe4189e809725e0fca5b3acbe7eb8ef2919640c7bd34a"),
    ("discordtune", "apple-fwd.tsv"): (J_PRIV + "/discordtune/apple-fwd.tsv", "bcaddc91ec8bdcfb78b7a3111058a74d4e185d885de03ba471e832c723d235e9"),
    ("cvtune", "apple-fwd.tsv"): (J_CACHE + "/cvtune/apple-fwd.tsv", "a53445736473f92ed9e60840217126ba9d3aba8c6db09ba236426ed4bbeabedd"),
    ("cvtune", "jev-sent-fwd.jsonl"): (J_CACHE + "/cvtune/jev-sent-fwd.jsonl", "ea8b5637606e0589758b7d51356ce1a29d21af343fbd02e1e6bcf3d64b925594"),
    ("dev302", "apple-fwd.tsv"): (J_REPO + "/dev302/apple-fwd.tsv", "11698af04c35aea6764bd421152747811634b644e183428826646fa65431a96f"),
    ("dev302", "jev-sent-fwd.jsonl"): (J_REPO + "/dev302/jev-sent-fwd.jsonl", "5a909cf09c3539609306614ebf3b0a658df18e460935380615f4dc3ac3b5fd5f"),
    ("typing76", "apple-fwd.tsv"): (J_REPO + "/typing76/apple-fwd.tsv", "26b29dd0d6d370d8a73804705501cf87fb219abcad157618aa2701d22f48e47b"),
    ("typing76", "jev-sent-fwd.jsonl"): (J_REPO + "/typing76/jev-sent-fwd.jsonl", "0577a26da9a7a77b6e6ca23c45a4cf9ace25e31f60c9174246548e17ca534e61"),
}
NROWS = {"discordtune": 1000, "cvtune": 1000, "dev302": 302, "typing76": 76}
PUBLIC = ("dev302", "typing76")
CONDS = ["L-noul", "L-choice-fwd", "L-choice-rev", "L-pos", "B1-ll", "B4-ll", "Q-ll"]


def die(msg):
    sys.exit(f"s5k: {msg}")


def read_verified(name, fname):
    """Bytes of an S5j file, only after its SHA-256 matches the contract table."""
    path, want = HASHES[(name, fname)]
    if not os.path.isfile(path):
        die("INPUT MISSING")
    data = open(path, "rb").read()
    if hashlib.sha256(data).hexdigest() != want:
        die("INPUT HASH MISMATCH")
    return data.decode("utf-8")


def load_rows(name, limit=0):
    if name in ("discordtune", "cvtune") and limit:
        die("--limit is for public smoke sets only")
    rows = [json.loads(l) for l in read_verified(name, "rows.jsonl").splitlines() if l.strip()]
    if len(rows) != NROWS[name]:
        die("INPUT ROW COUNT")
    return rows[:limit] if limit else rows


def out_dir(name, limit=0):
    base = L_PRIV if name == "discordtune" else L_CACHE if name == "cvtune" else L_REPO
    d = os.path.join(base, name + (f"-n{limit}" if limit else ""))
    os.makedirs(d, exist_ok=True)
    return d


def row_ctx(r, mode, name):
    """mode: none | real | synth. -> context string for the row, or None when the row is skipped."""
    if mode == "none":
        return ""
    if mode == "synth":
        if name not in PUBLIC:
            die("--ctx synth is for public sets only")
        return SYNTH_CTX
    return r["ctx"].translate({9: 32, 10: 32, 13: 32}) if r["ctx"] else None  # real: only rows that have one


def jobs(rows, mode, name):
    """-> [(k, ctx)] rows with >= 2 candidates (contract §3) and a context for this mode."""
    out = []
    for k, r in enumerate(rows):
        c = row_ctx(r, mode, name)
        if len(r["cands"]) > 1 and c is not None:
            out.append((k, c))
    return out


def result_path(d, cond, mode):
    return os.path.join(d, f"{cond}.{'noctx' if mode == 'none' else 'ctx'}.jsonl")


def done_keys(path):
    return {json.loads(l)["k"] for l in open(path, encoding="utf-8")} if os.path.exists(path) else set()


def append(path, rec):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def pick(scores):
    """argmax, ties to the better-ranked (lower index)."""
    return max(range(len(scores)), key=lambda i: (scores[i], -i))


def degeneracy(recs, tol, cond, mode):
    """Contract §9: >1 rows with all-equal scores, or first-pick ratio 100% -> stop. Returns True when it fires."""
    same = sum(1 for s in recs if max(s) - min(s) < tol)
    first = sum(1 for s in recs if pick(s) == 0) / max(1, len(recs))
    print(f"check {cond} ctx={mode} rows={len(recs)} allsame={same} first={first:.3f}")
    if same > 1 or first >= 1.0:
        print(f"s5k: DEGENERATE {cond} ctx={mode}", file=sys.stderr)
        return True
    return False


def write_meta(d, **kw):
    append(os.path.join(d, "meta.jsonl"), kw)


class Timer:
    def __enter__(self):
        self.t = time.perf_counter()
        return self

    def __exit__(self, *a):
        self.ms = (time.perf_counter() - self.t) * 1000
