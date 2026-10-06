#!/usr/bin/env python3
"""S-bench: cross-version benchmark (docs/contracts/sbench.md).

  tools/bench.py run <ref>... [--label L] [--base REF] [--lm FILE] [--private-root DIR]
  tools/bench.py reference [--private-root DIR]
  tools/bench.py check-static [--json FILE] [--md FILE]
  tools/bench.py table

`run` exports each ref with `git archive`, builds its CLI and the key replay, scores the frozen suite with that
version's own CLI (always `--rows`, never `--dev`), compares with the previous row and writes
eval/bench/results/<label>.json. `reference` (sbench 7.1) scores the CURRENT checkout's unigram decoder on the base
lexicon only (`--no-overlay`, no --lm) into eval/bench/results/reference-mcbpmf.json, recomputing only when the commit or a
fingerprint changed. `check-static` verifies eval/bench/static/typing-test.json against docs/typing-test.md by cell position.
`table` regenerates docs/benchmark.md from every results JSON (version rows), the reference row and the static section.
Never reads eval/holdout/. Every private path is derived from --private-root.
"""
import argparse, datetime, functools, hashlib, json, math, os, platform, re, shutil, statistics, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITE = os.path.join(ROOT, "eval/bench/suite-v1")
RESULTS = os.path.join(ROOT, "eval/bench/results")
REFERENCE = "reference-mcbpmf.json"  # not a version file: `run` bookkeeping and the version rows skip it
STATIC = os.path.join(ROOT, "eval/bench/static/typing-test.json")
TYPING_MD = os.path.join(ROOT, "docs/typing-test.md")
BASE_LEXICON = os.path.join(ROOT, "data/lexicon/mcbpmf-data.txt")
CACHE = os.path.expanduser("~/.cache/shanjie/bench")
TUNE = os.path.expanduser("~/.cache/shanjie/work/s2/tune")
DEFAULT_PRIVATE = "~/side-project/shanjie-private"
DEFAULT_LM = [os.path.join(ROOT, "data/lm/bigram.sjlm"), os.path.expanduser("~/side-project/shanjie/data/lm/bigram.sjlm")]

# Suite v1 (frozen). Changing any file means a new suite version, not an edit of these hashes.
VARIANTS_SHA = "50cb44bd0f1fa33c48624bc0185d03d4e87e99107bc15971d638528bcf69cdb3"
# name -> (file, sha256, profiles, private). dev302 = eval/dev/*.txt sorted, minus user-typing.txt, 3-field rows, first 302.
SETS = {
    "dev302": (os.path.join(SUITE, "dev302.txt"), "603850e444efee38b8c121e3d666d6c540acd3a27d5a6a5070a78a962dd98456", ["chat", "formal"], False),
    "typing76": (os.path.join(SUITE, "typing76.txt"), "c061f66dfade21996803137320aa986a81a3e72e3e4e1dd559abd5dc04b68d08", ["chat", "formal"], False),
    "probe": (os.path.join(SUITE, "probe.txt"), "75aac33877ddd7907cdd6789c6da6a0f3b7c775c8c6dfa6c752a5f3e87ab6801", ["chat"], False),
    "cvtune": (os.path.join(TUNE, "cvtune.txt"), "fd632479fa3819c4a6d8f21e8b9358fc636f65e9c308c58ec303afb7dc887f89", ["chat"], False),
    "wikitune": (os.path.join(TUNE, "wikitune.txt"), "f76c49887b6e0b3c527c71a305cd8f2eec470ea3207dd6ee80b793cc45beea76", ["chat"], False),
    "discordtune": ("discord-tune-rows.txt", None, ["chat"], True),  # file name under --private-root; hash recorded, not pinned
}
VARIANTS = os.path.join(SUITE, "variants.tsv")


def die(msg):
    print(f"bench: {msg}", file=sys.stderr)
    sys.exit(1)


def sha_file(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def sha_str(s):
    return hashlib.sha256(s.encode()).hexdigest()


def sh(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        raise RuntimeError(f"{cmd[0]} {cmd[1] if len(cmd) > 1 else ''} failed ({r.returncode}): {(r.stderr or r.stdout)[-600:]}")
    return r.stdout


# ---------- scoring: one rule (reference/proto/eval.py `lenient` over the suite's variants.tsv) ----------
CHARMAP = str.maketrans("她妳它牠嘗周臺裏", "他你他他嚐週台裡")


@functools.lru_cache(None)
def _variants():
    t = {}
    for line in open(VARIANTS, encoding="utf-8"):
        if line.startswith("#"):
            continue
        p = line.rstrip("\n").split("\t")
        if len(p) != 2 or not p[0] or not p[1] or p[0] in t:
            die("variants.tsv: bad line")
        t[p[0]] = p[1]
    return t, max(map(len, t), default=0)


@functools.lru_cache(None)
def lenient(s):
    table, longest = _variants()
    s = s.translate(CHARMAP)
    out, i = [], 0
    while i < len(s):
        for n in range(min(longest, len(s) - i), 0, -1):
            c = table.get(s[i:i + n])
            if c is not None:
                out.append(c)
                i += n
                break
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def truths(rows_file):
    return [l.split("|")[1] for l in open(rows_file, encoding="utf-8").read().split("\n") if l.count("|") == 2]


def read_dump(dump):
    """-> per row (1-based, in order) list of surfaces by rank."""
    rows = {}
    for l in open(dump, encoding="utf-8"):
        i, _, s, _ = l.rstrip("\n").split("\t")
        rows.setdefault(int(i), []).append(s)
    return [rows[i] for i in sorted(rows)]


def score(rows_file, dump):
    cands, tr = read_dump(dump), truths(rows_file)
    if len(cands) != len(tr):
        raise RuntimeError(f"dump has {len(cands)} rows, set has {len(tr)}")
    ok, o64 = [], 0
    for t, c in zip(tr, cands):
        lt = lenient(t)
        ok.append(lenient(c[0]) == lt)
        o64 += any(lenient(s) == lt for s in c)
    return {"n": len(tr), "top1": sum(ok), "oracle64": o64, "top1_sha256": sha_str("\n".join(c[0] for c in cands))}, ok


def mcnemar(b, c):
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, k) for k in range(min(b, c) + 1)) / 2 ** n)


# ---------- versions ----------
class Ver:
    def __init__(self, ref):
        self.ref = ref
        self.sha = sh(["git", "-C", ROOT, "rev-parse", "--verify", ref + "^{commit}"]).strip()
        self.dir = os.path.join(CACHE, "src", self.sha)
        self.built = False

    def build(self):
        if self.built:
            return
        if not os.path.exists(os.path.join(self.dir, ".exported")):
            shutil.rmtree(self.dir, ignore_errors=True)
            os.makedirs(self.dir)
            tarball = self.dir + ".tar"
            try:  # two steps so a failed archive can never leave a marker behind
                sh(["git", "-C", ROOT, "archive", "-o", tarball, self.sha, "Cargo.toml", "Cargo.lock", "core", "cli", "data", "eval/variants.tsv"])
                sh(["tar", "-x", "-f", tarball, "-C", self.dir])
            except Exception:
                shutil.rmtree(self.dir, ignore_errors=True)
                raise
            finally:
                if os.path.exists(tarball):
                    os.remove(tarball)
            open(os.path.join(self.dir, ".exported"), "w").close()
        sh(["cargo", "build", "--release", "-p", "cli"], cwd=self.dir)
        rd = os.path.join(self.dir, "replay")
        os.makedirs(os.path.join(rd, "src"), exist_ok=True)
        open(os.path.join(rd, "Cargo.toml"), "w").write(
            f'[package]\nname = "replay"\nversion = "0.0.0"\nedition = "2021"\n\n[workspace]\n\n[dependencies]\ncore = {{ path = "{self.dir}/core" }}\n')
        shutil.copy(os.path.join(ROOT, "tools/bench/replay/src/main.rs"), os.path.join(rd, "src/main.rs"))
        sh(["cargo", "build", "--release"], cwd=rd)
        self.cli = os.path.join(self.dir, "target/release/shanjie-eval")
        self.replay = os.path.join(rd, "target/release/replay")
        self.lexicon_sha = sha_str("".join(f"{f}\n" + open(os.path.join(self.dir, "data/lexicon", f), encoding="utf-8").read()
                                           for f in sorted(os.listdir(os.path.join(self.dir, "data/lexicon")))))
        self.built = True


def cell_cache(ctx, ver, name, prof):
    base = os.path.join(ctx.private_root, "bench/rows") if SETS[name][3] else os.path.join(CACHE, "rows")
    key = sha_str(f"{ctx.fp[name]}|{VARIANTS_SHA}|{ctx.model_sha}")[:16]  # set, variants and model all key the cache
    return os.path.join(base, ver.sha, key, f"{name}.{prof}.tsv")


def run_cell(ctx, ver, name, prof):
    """Run the version's CLI on one set; write the dump to the cache; return (result, ok vector)."""
    rows = ctx.files[name]
    dump = cell_cache(ctx, ver, name, prof)
    os.makedirs(os.path.dirname(dump), exist_ok=True)
    tmp = dump + ".tmp"
    out = sh([ver.cli, "--lm", ctx.lm, "--profile", prof, "--rows", rows, "--dump", tmp],
             cwd=ver.dir, env={**os.environ, "SHANJIE_VARIANTS": VARIANTS})
    os.replace(tmp, dump)
    res, ok = score(rows, dump)
    want = f"{{'n': {res['n']}, 'top1': {res['top1']}, 'oracle@64': {res['oracle64']}, 'top1_sha256': '{res['top1_sha256']}'}}"
    if want not in out:
        raise RuntimeError(f"{name}/{prof}: tool score differs from the CLI summary: {out.strip()[-200:]}")
    return res, ok


def matches(ctx, rc, name):
    """Does a recorded cell (with the fingerprints it was measured under) match the current suite?"""
    return bool(rc) and rc.get("fp") == ctx.fp[name] and rc.get("variants") == VARIANTS_SHA and rc.get("model") == ctx.model_sha


def prev_ok(ctx, ver, name, prof, known):
    """Previous row's per-row correctness under the CURRENT suite. Uses the cached dump only if the record's
    fingerprints match and the dump still scores to the recorded hash; otherwise reruns the version (and says why)."""
    cell = f"{name}/{prof}"
    rc = known.get(ver.sha, {}).get(cell)
    fresh = matches(ctx, rc, name)
    dump = cell_cache(ctx, ver, name, prof)
    why = "no recorded result" if not rc else None if fresh else "recorded set/variants/model fingerprints differ from the current suite"
    if fresh and os.path.exists(dump):
        try:
            res, ok = score(ctx.files[name], dump)
            if res["top1_sha256"] == rc["top1_sha256"]:
                return ok, rc
            why = "cached rows do not match the recorded hash"
        except Exception as e:
            why = f"cached rows unreadable ({e})"
    elif fresh:
        why = "cached rows missing"
    print(f"bench: previous row {ver.ref} {cell}: {why}, rerunning under the current suite", file=sys.stderr)
    ver.build()
    res, ok = run_cell(ctx, ver, name, prof)
    if fresh and res["top1_sha256"] != rc["top1_sha256"]:
        die(f"{ver.ref} {cell}: rerun top1_sha256 differs from the recorded one (not deterministic?)")
    return ok, (rc if fresh else None)


class Ctx:
    pass


def preflight(args, need_lm=True):
    ctx = Ctx()
    if sha_file(VARIANTS) != VARIANTS_SHA:
        die("suite variants.tsv does not match its pinned SHA-256 (the suite changed; bump the suite version)")
    ctx.private_root = os.path.abspath(os.path.expanduser(args.private_root))
    ctx.files, ctx.fp = {}, {}
    for name, (f, want, _, private) in SETS.items():
        if private:
            f = os.path.join(ctx.private_root, f)
            if not os.path.isfile(f):
                print(f"bench: {name}: no private data under --private-root, column skipped", file=sys.stderr)
                continue
        elif not os.path.isfile(f):
            die(f"{name}: missing set file {f}")
        got = sha_file(f)
        if want and got != want:
            die(f"{name}: SHA-256 of {f} does not match the pinned value")
        ctx.files[name], ctx.fp[name] = f, got
    if not need_lm:
        return ctx
    ctx.lm = next((p for p in ([args.lm] if args.lm else DEFAULT_LM) if os.path.isfile(p)), None)
    if not ctx.lm:
        die("model file not found; pass --lm <absolute path to bigram.sjlm>")
    ctx.lm = os.path.abspath(ctx.lm)
    ctx.model_sha = sha_file(ctx.lm)
    if ctx.model_sha != open(os.path.join(ROOT, "data/bigram.sjlm.sha256")).read().split()[0]:
        die("model SHA-256 does not match data/bigram.sjlm.sha256")
    return ctx


def known_from_results():
    k = {}
    for f in sorted(os.listdir(RESULTS)) if os.path.isdir(RESULTS) else []:
        if f.endswith(".json") and f != REFERENCE:
            j = json.load(open(os.path.join(RESULTS, f), encoding="utf-8"))
            for v in j["versions"]:  # per cell, the latest file that actually measured it wins
                for cell, r in v.get("accuracy", {}).items():
                    if "top1_sha256" in r:
                        k.setdefault(v["sha"], {})[cell] = {**r, "fp": v["fingerprints"][cell.split("/")[0]], "variants": j["variants_sha256"], "model": j["model_sha256"]}
    return k



def measure_speed(ctx, vers):
    """3 interleaved rounds; replay commits must equal the CLI dev302/chat top-1 row for row."""
    rows = ctx.files["dev302"]
    runs = {v.sha: [] for v in vers}
    errs = {}
    for rnd in range(3):
        for v in vers:
            if v.sha in errs:
                continue
            want = [c[0] for c in read_dump(cell_cache(ctx, v, "dev302", "chat"))]
            with tempfile.TemporaryDirectory() as td, tempfile.TemporaryFile() as out:
                p = subprocess.Popen([v.replay, os.path.join(v.dir, "data/lexicon"), ctx.lm, rows, td + "/c"], stdout=out, stderr=subprocess.PIPE, text=True)
                _, st, ru = os.wait4(p.pid, 0)
                p.returncode = os.waitstatus_to_exitcode(st)
                if p.returncode:
                    errs[v.sha] = f"replay failed ({p.returncode}): {p.stderr.read()[-300:]}"
                    continue
                out.seek(0)
                m = json.loads(out.read())
                got = open(td + "/c", encoding="utf-8").read().split("\n")[:-1]
            bad = [i + 1 for i in range(max(len(got), len(want))) if i >= len(got) or i >= len(want) or got[i] != want[i]]
            if bad:
                errs[v.sha] = f"replay differs from the CLI at rows {bad[:20]} ({len(bad)} of {len(want)})"
                continue
            m["rss_mb"] = ru.ru_maxrss / 2 ** 20  # macOS reports bytes
            runs[v.sha].append(m)
    out = {}
    for v in vers:
        if v.sha in errs:
            out[v.sha] = {"error": errs[v.sha]}
            continue
        r = runs[v.sha]
        out[v.sha] = {"rows_match": f"{len(want)}/{len(want)}", "keys": r[0]["keys"], "rounds": len(r),
                      "p95_us": statistics.median(x["p95_us"] for x in r), "max_us": max(x["max_us"] for x in r),
                      "load_ms": statistics.median(x["load_ms"] for x in r), "rss_mb": max(x["rss_mb"] for x in r)}
    return out


def cmd_run(args):
    ctx = preflight(args)
    cells = [(n, p) for n, (_, _, ps, _) in SETS.items() if n in ctx.files for p in ps]
    load0 = os.getloadavg()[0]
    vers = [Ver(r) for r in args.refs]
    base = Ver(args.base) if args.base else None
    known = known_from_results()
    result = {"label": args.label, "suite": "v1", "date": datetime.date.today().isoformat(),
              "model_sha256": ctx.model_sha, "variants_sha256": VARIANTS_SHA,
              "sets": {n: {"sha256": ctx.fp.get(n), "private": SETS[n][3]} if n in ctx.files else {"missing": True} for n in SETS},
              "versions": []}
    oks, failed = {}, False

    def one(v, n, p):
        try:
            return run_cell(ctx, v, n, p)
        except Exception as e:
            return {"error": str(e)[-400:]}, None

    for v in vers:
        rec = {"ref": v.ref, "sha": v.sha}
        result["versions"].append(rec)
        try:
            v.build()
            rec["lexicon_sha256"] = v.lexicon_sha
        except Exception as e:
            rec["error"] = str(e)
            failed = True
            print(f"bench: {v.ref}: {e}", file=sys.stderr)
            continue
        with ThreadPoolExecutor(max_workers=3) as ex:
            futs = {c: ex.submit(one, v, *c) for c in cells}
        rec["accuracy"] = {}
        for (n, p), f in futs.items():
            res, ok = f.result()
            rec["accuracy"][f"{n}/{p}"] = res
            if ok is None:
                failed = True
                print(f"bench: {v.ref} {n}/{p}: {res['error']}", file=sys.stderr)
            else:
                oks[(v.sha, n, p)] = ok
        for n in SETS:
            if n not in ctx.files:
                rec["accuracy"][f"{n}/chat"] = {"missing": True}
        rec["fingerprints"] = dict(ctx.fp)
        known[v.sha] = {c: {**r, "fp": ctx.fp[c.split("/")[0]], "variants": VARIANTS_SHA, "model": ctx.model_sha}
                        for c, r in rec["accuracy"].items() if "top1_sha256" in r}
    # compare with the previous row (failed cells are skipped, not raised)
    order = ([base] if base else []) + vers
    for i, v in enumerate(vers):
        rec = result["versions"][i]
        j = order.index(v)
        if j == 0 or "accuracy" not in rec:
            continue
        pv = order[j - 1]
        rec["vs"] = pv.ref
        rec["vs_prev"] = {}
        for (n, p) in cells:
            cell = f"{n}/{p}"
            if (v.sha, n, p) not in oks:
                continue
            prec = next((r for r in result["versions"] if r["sha"] == pv.sha), None)
            if prec and "error" in prec.get("accuracy", {}).get(cell, {}):
                rec["vs_prev"][cell] = {"error": "previous row failed: " + prec["accuracy"][cell]["error"][-200:]}
                continue
            try:
                old, rc = prev_ok(ctx, pv, n, p, known)
            except Exception as e:
                rec["vs_prev"][cell] = {"error": str(e)[-400:]}
                failed = True
                continue
            new = oks[(v.sha, n, p)]
            nrows = rec["accuracy"][cell]["n"]
            if not len(new) == len(old) == nrows:
                die(f"{v.ref} {cell}: row counts differ (new {len(new)}, previous {len(old)}, set {nrows})")
            if rc and sum(old) != rc["top1"]:
                die(f"{pv.ref} {cell}: per-row top1 {sum(old)} != recorded top1 {rc['top1']}")
            fixed = sum(a and not b for a, b in zip(new, old))
            broken = sum(b and not a for a, b in zip(new, old))
            rec["vs_prev"][cell] = {"fixed": fixed, "broken": broken, "p": mcnemar(fixed, broken)}
    ok_vers = [v for v in vers if (v.sha, "dev302", "chat") in oks]
    speed = measure_speed(ctx, ok_vers) if ok_vers else {}
    for rec in result["versions"]:
        if rec["sha"] in speed:
            rec["speed"] = speed[rec["sha"]]
        elif "accuracy" in rec and "dev302" in ctx.files:
            rec["speed"] = {"error": "dev302/chat failed, no replay check possible"}
        if "error" in rec.get("speed", {}):
            failed = True
            print(f"bench: {rec['ref']}: {rec['speed']['error']}", file=sys.stderr)
    result["env"] = {"machine": sh(["sysctl", "-n", "hw.model"]).strip(), "macos": platform.mac_ver()[0],
                     "cargo": sh(["cargo", "--version"]).strip(), "load_avg_start": load0, "load_avg_end": os.getloadavg()[0]}
    os.makedirs(RESULTS, exist_ok=True)
    with open(os.path.join(RESULTS, args.label + ".json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
        f.write("\n")
    sys.exit(1 if failed else 0)



# ---------- reference row (sbench 7.1): the current checkout's unigram decoder on the McBopomofo base lexicon ----------
def head_sha():
    return sh(["git", "-C", ROOT, "rev-parse", "HEAD"]).strip()


def tree_dirty():
    return bool(sh(["git", "-C", ROOT, "status", "--porcelain", "--", "core", "cli", "data", "Cargo.toml", "Cargo.lock"]).strip())


def reference_cmd(cli, rows, dump):
    """Unigram path: base lexicon only, no --lm, no --profile."""
    return [cli, "--no-overlay", "--rows", rows, "--dump", dump]


def reference_fingerprints(ctx):
    return {"sets": dict(ctx.fp), "variants": VARIANTS_SHA, "lexicon": sha_file(BASE_LEXICON)}


def reference_stale(old, sha, fp):
    """-> reason to recompute, or None when the recorded result is current."""
    if not old:
        return "no recorded reference result"
    if old.get("commit") != sha:
        return f"commit changed ({str(old.get('commit'))[:7]} -> {sha[:7]})"
    if old.get("fingerprints") != fp:
        return "a fingerprint (set, variants.tsv or base lexicon) changed or a private set appeared/disappeared"
    failed = sorted(k for k, v in (old.get("accuracy") or {}).items() if isinstance(v, dict) and "error" in v)
    if failed:
        return f"these sets failed last time: {', '.join(failed)}"
    return None


def cmd_reference(args):
    ctx = preflight(args, need_lm=False)
    sha = head_sha()
    fp = reference_fingerprints(ctx)
    path = os.path.join(RESULTS, REFERENCE)
    old = json.load(open(path, encoding="utf-8")) if os.path.isfile(path) else None
    why = reference_stale(old, sha, fp)
    if not why:
        print(f"bench: reference is current at {sha[:7]}, nothing to do", file=sys.stderr)
        return
    if tree_dirty():
        die("core/cli/data have uncommitted changes; the recorded commit SHA must be the one that produced the result")
    print(f"bench: recomputing the reference row: {why}", file=sys.stderr)
    sh(["cargo", "build", "--release", "-p", "cli"], cwd=ROOT)
    cli = os.path.join(ROOT, "target/release/shanjie-eval")
    acc, failed = {}, False
    for name in SETS:
        if name not in ctx.files:
            acc[name] = {"missing": True}
            continue
        base = os.path.join(ctx.private_root, "bench/reference") if SETS[name][3] else os.path.join(CACHE, "reference")
        os.makedirs(base, exist_ok=True)
        dump = os.path.join(base, f"{sha}.{name}.tsv")
        try:
            out = sh(reference_cmd(cli, ctx.files[name], dump), cwd=ROOT, env={**os.environ, "SHANJIE_VARIANTS": VARIANTS})
            res, _ = score(ctx.files[name], dump)
            want = f"{{'n': {res['n']}, 'top1': {res['top1']}, 'oracle@64': {res['oracle64']}, 'top1_sha256': '{res['top1_sha256']}'}}"
            if want not in out:
                raise RuntimeError(f"tool score differs from the CLI summary: {out.strip()[-200:]}")
            acc[name] = res
        except Exception as e:
            acc[name] = {"error": str(e)[-400:]}
            failed = True
            print(f"bench: reference {name}: {e}", file=sys.stderr)
    result = {"label": "reference-mcbpmf", "suite": "v1", "date": datetime.date.today().isoformat(), "commit": sha,
              "fingerprints": fp, "accuracy": acc}
    os.makedirs(RESULTS, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
        f.write("\n")
    if failed:
        sys.exit(1)


# ---------- static hand-typed table (sbench 7.2) ----------
def _cells(md_line):
    """Table cells of a markdown row split on `|` (outer pipes dropped, cells stripped)."""
    parts = md_line.strip().split("|")
    if parts and parts[0] == "":
        parts = parts[1:]
    if parts and parts[-1] == "":
        parts = parts[:-1]
    return [c.strip() for c in parts]


def _refs(node, path=""):
    """Every dict that carries a "value" is one cell reference."""
    if isinstance(node, dict):
        if "value" in node:
            yield path, node
        else:
            for k, v in node.items():
                yield from _refs(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _refs(v, f"{path}[{i}]")


def check_static(json_path=STATIC, md_path=TYPING_MD):
    """Compare each referenced cell of docs/typing-test.md by position: line (1-based), column (0-based after
    splitting on `|`), part (0-based after splitting on `／`, required when the cell has one). -> list of problems."""
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    with open(md_path, encoding="utf-8") as f:
        md = f.read().split("\n")
    errs, n = [], 0
    for path, ref in _refs(data):
        n += 1
        if not all(isinstance(ref.get(k), int) for k in ("line", "col", "value")):
            errs.append(f"{path}: needs integer line, col and value")
            continue
        if not 1 <= ref["line"] <= len(md):
            errs.append(f"{path}: line {ref['line']} is outside the file")
            continue
        cells = _cells(md[ref["line"] - 1])
        if not 0 <= ref["col"] < len(cells):
            errs.append(f"{path}: line {ref['line']} has {len(cells)} columns, no column {ref['col']}")
            continue
        cell, part = cells[ref["col"]], ref.get("part")
        pieces = cell.split("／")
        if part is None and len(pieces) > 1:
            errs.append(f"{path}: line {ref['line']} column {ref['col']} is \"{cell}\" and needs a part")
            continue
        if part is not None and not 0 <= part < len(pieces):
            errs.append(f"{path}: line {ref['line']} column {ref['col']} \"{cell}\" has no part {part}")
            continue
        m = re.search(r"\d[\d,]*", pieces[part or 0])
        got = int(m.group().replace(",", "")) if m else None
        if got != ref["value"]:
            errs.append(f"{path}: line {ref['line']} column {ref['col']} part {part} is {got} in docs/typing-test.md, JSON says {ref['value']}")
    if n == 0:
        errs.append("no cell references found")
    return errs


def cmd_check_static(args):
    errs = check_static(args.json, args.md)
    for e in errs:
        print(f"bench: static: {e}", file=sys.stderr)
    print(f"bench: static table check {'FAILED' if errs else 'passed'}", file=sys.stderr)
    sys.exit(1 if errs else 0)


# ---------- docs/benchmark.md ----------
def pct(r):
    if "error" in r:
        return "失敗：" + r["error"].replace("|", "/").replace("\n", " ")[:200]
    return "—（沒有私有資料）" if r.get("missing") else f"{100 * r['top1'] / r['n']:.2f}%"


def cell_text(rec, cell):
    r = rec["accuracy"].get(cell, {"missing": True})
    t = pct(r)
    if "missing" in r or "error" in r:
        return t
    t += f"<br>{r['top1']}/{r['n']}"
    d = rec.get("vs_prev", {}).get(cell)
    if d:
        t += "<br>集合變動" if d.get("set_changed") else f"<br>失敗：{d['error']}" if "error" in d else f"<br>+{d['fixed']} / −{d['broken']}，p={d['p']:.3g}"
    return t


REF_NOTE = "善解的 unigram 解碼器加小麥的基底詞庫，近似小麥的資料；不是小麥本身的解碼器（小麥現在有沒有用 bigram 沒有核對）"


def reference_row(ref, cells):
    """First row of the main table: not a version, no fixed/broken. unigram has no profile, so chat and formal show the same number."""
    acc = ref["accuracy"]
    rec = {"accuracy": {c: acc.get(c.split("/")[0], {"missing": True}) for c in cells}}  # no vs_prev: nothing to compare with
    return f"| 小麥資料基準<br>`{ref['commit'][:7]}`<br>{REF_NOTE} | " + " | ".join(cell_text(rec, c) for c in cells) + " |"


def static_section(st):
    L = ["", f"## 實打對照（靜態，{st['date']}）", "",
         "從 `eval/bench/static/typing-test.json` 產生，數字逐格抄自 `docs/typing-test.md`（行號與欄位位置見 JSON，`tools/bench.py check-static` 核對）。"
         "實打是當天的產品快照；蘋果注音在測驗中會學習（`docs/typing-test.md` 第 202 行）；條件和上面的版本列不同，不做統計比較。"]
    for g in st["groups"]:
        n = g.get("n", {}).get("value")
        L += ["", f"### {g['name']}" + (f"（{n} 句）" if n is not None else ""), "", "| 輸入法 | 錯的句數 | 錯字數 | 來源行 |", "|---|---|---|---|"]
        for ime, r in g["ime"].items():
            lines = sorted({c["line"] for c in r.values()})
            L.append(f"| {ime} | {r['sentences']['value'] if 'sentences' in r else '—'} | {r['chars']['value'] if 'chars' in r else '—'} | {'、'.join(map(str, lines))} |")
    return L


def render_table(results, ref=None, static=None):
    cells = [f"{n}/{p}" for n, (_, _, ps, _) in SETS.items() for p in ps]
    L = ["# 跨版本基準測試", "", "由 `tools/bench.py table` 從 `eval/bench/results/*.json` 產生，請勿手改。契約：`docs/contracts/sbench.md`。", "",
         "## 準確率（top1，寬鬆對照）", "", "每格：top1%、答對／列數、和上一列比的「修好 / 弄壞，McNemar 雙尾精確 p」。", "",
         "| 版本 | " + " | ".join(cells) + " |", "|---|" + "---|" * len(cells)]
    if ref:
        L.append(reference_row(ref, cells))
    for res in results:
        for rec in res["versions"]:
            row = rec["ref"] + f"<br>`{rec['sha'][:7]}`"
            if "accuracy" not in rec:
                L.append(f"| {row} | " + " | ".join([f"失敗：{rec.get('error', '')}".replace("|", "/").replace("\n", " ")[:300]] * len(cells)) + " |")
            else:
                L.append(f"| {row} | " + " | ".join(cell_text(rec, c) for c in cells) + " |")
    L += ["", "## 保留集", "", "由 fresh verifier 在該版 tag 的 worktree 跑 `--set holdout` 後，把摘要行填進 results JSON 的 `holdout`（`{\"chat\": {n, top1, oracle64, top1_sha256}, \"formal\": ...}`）。", "",
          "| 版本 | chat | formal |", "|---|---|---|"]
    for res in results:
        for rec in res["versions"]:
            h = rec.get("holdout", {})
            f = lambda p: f"{100 * h[p]['top1'] / h[p]['n']:.2f}%<br>{h[p]['top1']}/{h[p]['n']}，oracle@64 {h[p]['oracle64']}" if p in h else "—（尚未量測）"
            L.append(f"| {rec['ref']}<br>`{rec['sha'][:7]}` | {f('chat')} | {f('formal')} |")
    L += ["", "## 速度與記憶體（同場）", "", "每版 3 輪輪流跑，p95 取三輪中位數，max 取三輪最大，載入（`Engine::new` + `load_lm`）取中位數，RSS 取最大。標準排列、chat profile、dev302 重播。速度只在同一列「同場」裡比較。", "",
          "| 版本 | 日期 | 機型／macOS／load avg | p95 | max | 載入 | RSS | 按鍵數 | 重播＝CLI |", "|---|---|---|---|---|---|---|---|---|"]
    for res in results:
        e = res["env"]
        for rec in res["versions"]:
            s = rec.get("speed")
            head = f"| {rec['ref']} | {res['date']} | {e['machine']} / {e['macos']} / {e['load_avg_start']:.1f}→{e['load_avg_end']:.1f} |"
            if not s:
                L.append(head + " 失敗：" + rec.get("error", "").replace("|", "/").replace("\n", " ")[:300] + " | | | | | |")
            elif "error" in s:
                L.append(head + " 失敗：" + s["error"].replace("|", "/")[:300] + " | | | | | |")
            else:
                L.append(head + f" {s['p95_us'] / 1000:.3f} ms | {s['max_us'] / 1000:.2f} ms | {s['load_ms']:.0f} ms | {s['rss_mb']:.0f} MB | {s['keys']} | {s['rows_match']} |")
    if static:
        L += static_section(static)
    L += ["", "## 量測規則", "",
          "- 版本只決定程式與詞庫；集合一律來自套件 v1（`eval/bench/suite-v1/`，SHA-256 寫死在工具裡），所有版本讀同一份，一律用 `--rows`。",
          "- 計分只有一套：套件的 `variants.tsv` 寬鬆對照，top1 與 oracle@64 由工具從逐列輸出算出，CLI 摘要行只做交叉檢查。",
          "- dev302 是挑戰集，S5j 證明它會誤導，只當參考。typing76 可和 `docs/typing-test.md` 的實打結果並列。",
          "- cvtune、wikitune、discordtune 在調參數時被看過，不是未見資料；唯一沒被看過的是保留集。",
          "- 保留集不由工具跑（main 不看內容），由 verifier 量好填入。",
          "- 速度不設門檻，只在同場比較。",
          "- 重跑：`tools/bench.py run <ref>... --label <名稱>`，再 `tools/bench.py table`。模型換了就是新的套件版本。",
          "- 「小麥資料基準」列不是版本：用目前 checkout 的 CLI（`--no-overlay`，不帶 `--lm`）量基底詞庫的 unigram。重跑：`tools/bench.py reference --private-root <目錄>`，commit 或任一指紋（集合、`variants.tsv`、基底詞庫）和 `reference-mcbpmf.json` 不同時才重算並在 stderr 說明原因；rebase 或改到 `core/`、`cli/`、`data/` 之後要重跑，再 `tools/bench.py table`。",
          "- 實打對照表是靜態的：新增或更新後跑 `tools/bench.py check-static`，每格依 `docs/typing-test.md` 的行號與欄位位置比對。", ""]
    return "\n".join(L)


def cmd_table(_):
    results = [json.load(open(os.path.join(RESULTS, f), encoding="utf-8")) for f in sorted(os.listdir(RESULTS)) if f.endswith(".json") and f != REFERENCE]
    load = lambda p: json.load(open(p, encoding="utf-8")) if os.path.isfile(p) else None
    open(os.path.join(ROOT, "docs/benchmark.md"), "w", encoding="utf-8").write(render_table(results, load(os.path.join(RESULTS, REFERENCE)), load(STATIC)))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("refs", nargs="+")
    r.add_argument("--label")
    r.add_argument("--base", help="compare the first ref with this one (not measured for speed)")
    r.add_argument("--lm")
    r.add_argument("--private-root", default=DEFAULT_PRIVATE)
    sub.add_parser("table")
    rf = sub.add_parser("reference")
    rf.add_argument("--private-root", default=DEFAULT_PRIVATE)
    cs = sub.add_parser("check-static")
    cs.add_argument("--json", default=STATIC)
    cs.add_argument("--md", default=TYPING_MD)
    a = ap.parse_args()
    if a.cmd == "run":
        a.label = a.label or "+".join(x.replace("/", "_") for x in a.refs)
        cmd_run(a)
    elif a.cmd == "reference":
        cmd_reference(a)
    elif a.cmd == "check-static":
        cmd_check_static(a)
    else:
        cmd_table(a)


if __name__ == "__main__":
    main()
