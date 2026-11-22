#!/usr/bin/env python3
"""The two latency gates of .github/workflows/latency-gate.yml, measured on one macOS runner with the arms alternating.

(A) one tree, one set of binaries, two small models (old = the tree's data/lm/tinylm.sjtl, new = the candidate),
    old, new, old, new:
    memory    peak RSS of shanjie-eval on dev302 (chat, the shipping flags, one thread; `/usr/bin/time -l`), and the
              CLI's own `tinylm: rerank` line of that run (per-row rerank on short contexts: a proxy). Reported.
    long ctx  the same flags over a row file whose every context is at least 64 Han characters (scripts/ci/longctx_rows.py),
              the rerank p95 read from stderr's `tinylm: rerank n=.. p50=.. p95=..` line. Gate: new p95 < 300 ms.
    128 syl   core/tests/engine_predict.rs `timing_composition_cap_128_syllables`, the test executable run directly.
              It reads data/lm/tinylm.sjtl of the tree, so the model under test is copied onto that path (hash checked)
              and the old one put back afterwards. Gate: with the new model the test's own assertions pass in every round.
    Every shanjie-eval run names the model with `--tinylm FILE --tinylm-paired`; its summary tag must carry
    `+tl:<sha8>` and `+pair:<sha8>` of that model and `+sep:K` exactly when the file's header has a boundary threshold K,
    and stderr must carry the rerank line: otherwise the model did not run as intended.
(B) base tree against head tree, base, head, base, head:
    memory    peak RSS of shanjie-eval on dev302 (summary tag ends `+t3` for base, `+t3c` for head). Reported.
    latency   shanjie-replay over eval/dev/user-typing.txt and user-reported.txt with `--key-times`; p95 is the
              nearest-rank 95th percentile of the `k` lines, in ms. Gate per set and round: head - base <= 1 ms and head < 16 ms.

A failed or timed-out run of the old model or the base tree is a broken setup (exit 2, no verdict); of the new model or
the head tree a failed gate (the other runs go on). Exit 0: every gate passes; 1: a gate fails.
Writes OUT/raw.tsv (every run's values), OUT/result.txt, every run's stdout/stderr/key times, and the result table to
$GITHUB_STEP_SUMMARY when that is set. `--self-test` checks the parsers on fixed text and exits.
"""
import argparse, hashlib, os, platform, re, shutil, signal, struct, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import longctx_rows  # noqa: E402

PACKS = "acg,it,meme,beauty"   # the shipping packs
SETS = ["user-typing", "user-reported"]
LC_GATE_MS, DELTA_GATE_MS, P95_GATE_MS = 300.0, 1.0, 16.0
T_DEV, T_LC, T_TEST, T_REPLAY = 300, 1800, 1800, 600   # seconds per run
TEST_NAME = "timing_composition_cap_128_syllables"
TEST_ENV_DROP = ["ACG_PACK", "PACKS", "PACKS_DIR", "SHANJIE_TIMING_OUT", "SHANJIE_TIMING_ROWS"]

RERANK = re.compile(r"^tinylm: rerank n=(\d+) p50=([0-9.]+) ms p95=([0-9.]+) ms; rows kept first \(candidate over the context\) (\d+);"
                    r" load (\d+) ms, rss (?:Some\((\d+)\)|None) -> (?:Some\((\d+)\)|None) KiB", re.M)
MEDIAN = re.compile(r"^median of 5: rerank on key p95 ([0-9.]+) ms, RerankJob::run p95 ([0-9.]+) ms; rerank off key p95 ([0-9.]+) ms", re.M)
SLOWEST = re.compile(r"^slowest single key over the 5 rounds: rerank on ([0-9.]+) ms.*rerank off ([0-9.]+) ms", re.M)
MAXRSS = re.compile(r"^\s*(\d+)\s+maximum resident set size", re.M)
SUMMARY = re.compile(r"^## .*?  (\S+)  \{", re.M)


class Broken(Exception):
    """The setup is broken: no verdict."""


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def model_k(path):
    """The boundary threshold K of an SJTL0006 header (flags at byte 248 with bit 0 set, K at byte 252, little-endian), else None."""
    with open(path, "rb") as f:
        head = f.read(256)
    if len(head) < 256 or head[:8] != b"SJTL0006":
        return None
    flags, k = struct.unpack_from("<II", head, 248)
    return k if flags & 1 else None


def parse_rerank(err):
    m = RERANK.search(err)
    if not m:
        return None
    n, p50, p95, kept, load, r0, r1 = m.groups()
    return {"n": int(n), "p50": float(p50), "p95": float(p95), "kept": int(kept), "load": int(load),
            "load_rss": int(r1) - int(r0) if r0 and r1 else None}


def peak_rss_kib(err):
    m = MAXRSS.search(err)
    return int(m.group(1)) // 1024 if m else None


def summary_tag(out):
    """The tag of the one `## name  tag  {...}` line, None when there is not exactly one."""
    if len(re.findall(r"^## ", out, re.M)) != 1:
        return None
    m = SUMMARY.search(out)
    return m.group(1) if m else None


def tag_ok(tag, tl8=None, pair=False, sepk="skip", end=None):
    """sepk: "skip" = not checked, None = the tag has no +sep:, a number = it has +sep:<that number>."""
    if tag is None:
        return False
    if tl8 and "+tl:" + tl8 not in tag:
        return False
    if pair and "+pair:" + tl8 not in tag:
        return False
    if sepk is None and "+sep:" in tag:
        return False
    if sepk not in ("skip", None) and not re.search(r"\+sep:%d($|[^0-9])" % sepk, tag):
        return False
    return end is None or tag.endswith(end)


def p95_ms(text):
    """Nearest-rank 95th percentile of the `k<TAB>microseconds` lines, in ms; (None, 0) when there is none."""
    v = sorted(int(f[1]) for f in (l.split("\t") for l in text.splitlines()) if len(f) == 2 and f[0] == "k" and f[1].isdigit())
    if not v:
        return None, 0
    i = -(-len(v) * 95 // 100)   # ceil(n * 0.95), 1-based
    return v[i - 1] / 1000.0, len(v)


def descendants(pid):
    kids = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout.split()
    return [p for k in kids for p in descendants(int(k))] + [pid]


class Gate:
    def __init__(self, a):
        self.a, self.out = a, a.out
        self.failed, self.vals = {"A": False, "B": False}, {}
        os.makedirs(self.out)
        self.raw = open(os.path.join(self.out, "raw.tsv"), "w")
        self.raw.write("kind\tround\tarm\tsubject\tvalue\tunit\tload_before\tload_after\n")

    def rec(self, kind, rnd, arm, subject, value, unit, l0, l1):
        self.raw.write("\t".join(str(x) for x in (kind, rnd, arm, subject, value, unit, l0, l1)) + "\n")
        self.raw.flush()
        self.vals[(kind, rnd, arm, subject)] = value

    def run(self, name, secs, cwd, cmd, env=None):
        """Runs cmd under `/usr/bin/time -l`; returns (exit code, stdout, stderr, load before, load after); 142 at the time limit."""
        o, e = os.path.join(self.out, name + ".out"), os.path.join(self.out, name + ".err")
        l0 = "%.2f" % os.getloadavg()[0]
        t0 = time.time()
        with open(o, "w") as fo, open(e, "w") as fe:
            p = subprocess.Popen(["/usr/bin/time", "-l"] + cmd, cwd=cwd, stdout=fo, stderr=fe, env=env)
            try:
                rc = p.wait(timeout=secs)
            except subprocess.TimeoutExpired:
                for pid in descendants(p.pid):   # the tool under `time`, then `time`: one pid at a time
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                p.wait()
                rc = 142
        l1 = "%.2f" % os.getloadavg()[0]
        print("%s: exit %d in %.1f s (load %s -> %s)" % (name, rc, time.time() - t0, l0, l1), flush=True)
        return rc, open(o).read(), open(e).read(), l0, l1

    def fail(self, item, weak_arm, arm, msg):
        """A failed run: a failed gate for the new model / head tree, a broken setup for the old model / base tree."""
        if arm != weak_arm:
            raise Broken(msg)
        print("FAILED " + msg, flush=True)
        self.failed[item] = True

    # ---- (A)
    def a_eval(self, kind, subject, name, secs, rnd, arm, rows):
        model = self.a.new_model if arm == "new" else self.a.old_model
        rc, out, err, l0, l1 = self.run(name, secs, self.a.a, [
            os.path.join(self.a.a, "target/release/shanjie-eval"), "--lm", "data/lm/bigram.sjlm", "--profile", "chat", *rows, "--context",
            "--packs", PACKS, "--tinylm", model, "--tinylm-paired", "--colloquial-core", "data/lm/colloquial.sjlm", "--threads", "1"])
        rr, kib = parse_rerank(err), peak_rss_kib(err)
        if rc != 0 or rr is None or kib is None or not tag_ok(summary_tag(out), self.sha8[arm], True, self.sepk[arm]):
            self.rec(kind + "_failed", rnd, arm, subject, rc, "rc", l0, l1)
            self.fail("A", "new", arm, "(A) %s: exit %d (142 = stopped after %d s), rerank line %s, peak RSS %s, summary tag %s (wanted +tl:/+pair: %s, +sep: %s)"
                      % (name, rc, secs, "found" if rr else "missing", kib, summary_tag(out), self.sha8[arm], self.sepk[arm]))
            return
        self.rec(kind + "_rss", rnd, arm, subject, kib, "KiB", l0, l1)
        self.rec(kind + "_p50", rnd, arm, subject, rr["p50"], "ms", l0, l1)
        self.rec(kind + "_p95", rnd, arm, subject, rr["p95"], "ms", l0, l1)
        self.rec(kind + "_n", rnd, arm, subject, rr["n"], "rows", l0, l1)
        self.rec(kind + "_kept", rnd, arm, subject, rr["kept"], "rows", l0, l1)
        self.rec(kind + "_model_load_ms", rnd, arm, subject, rr["load"], "ms", l0, l1)
        if rr["load_rss"] is not None:
            self.rec(kind + "_model_load_rss", rnd, arm, subject, rr["load_rss"], "KiB", l0, l1)

    def a_test(self, rnd, arm):
        dst = os.path.join(self.a.a, "data/lm/tinylm.sjtl")
        shutil.copyfile(self.a.new_model if arm == "new" else self.a.old_model, dst)
        if sha256(dst)[:8] != self.sha8[arm]:
            raise Broken("the %s model on %s has another hash after the copy" % (arm, dst))
        env = {k: v for k, v in os.environ.items() if k not in TEST_ENV_DROP}
        rc, out, err, l0, l1 = self.run("a-test-%d-%s" % (rnd, arm), T_TEST, os.path.join(self.a.a, "core"),
                                        [self.a.test_exe, TEST_NAME, "--ignored", "--nocapture", "--exact"], env)
        self.rec("a.test_rc", rnd, arm, "128syl", rc, "rc", l0, l1)
        m = MEDIAN.search(out)
        if not m or rc == 142:
            self.fail("A", "new", arm, "(A) 128-syllable test round %d %s: no median line or stopped after %d s (exit %d)" % (rnd, arm, T_TEST, rc))
            return
        for key, v in zip(("key_on", "job", "key_off"), m.groups()):
            self.rec("a.test_%s_p95" % key, rnd, arm, "128syl", float(v), "ms", l0, l1)
        kib = peak_rss_kib(err)
        if kib is not None:
            self.rec("a.test_rss", rnd, arm, "128syl", kib, "KiB", l0, l1)
        s = SLOWEST.search(out)
        if s:
            self.rec("a.test_max_on", rnd, arm, "128syl", float(s.group(1)), "ms", l0, l1)
            self.rec("a.test_max_off", rnd, arm, "128syl", float(s.group(2)), "ms", l0, l1)

    # ---- (B)
    def b_tree(self, arm):
        return (self.a.base, "+t3") if arm == "base" else (self.a.head, "+t3c")

    def b_rss(self, rnd, arm):
        d, end = self.b_tree(arm)
        name = "b-rss-%d-%s" % (rnd, arm)
        rc, out, err, l0, l1 = self.run(name, T_DEV, d, [
            os.path.join(d, "target/release/shanjie-eval"), "--lm", "data/lm/bigram.sjlm", "--profile", "chat", "--dev", "302", "--context",
            "--packs", PACKS, "--tinylm", "data/lm/tinylm.sjtl", "--colloquial-core", "data/lm/colloquial.sjlm", "--threads", "1"])
        kib = peak_rss_kib(err)
        tl8 = open(os.path.join(d, "data/tinylm.sjtl.sha256")).read()[:8]
        if rc == 142:
            self.rec("b.rss_failed", rnd, arm, "dev302", rc, "rc", l0, l1)
            self.fail("B", "head", arm, "(B) %s stopped after %d s" % (name, T_DEV))
            return
        if rc != 0 or kib is None or not tag_ok(summary_tag(out), tl8, end=end):
            raise Broken("(B) %s: exit %d, peak RSS %s, summary tag %s (wanted +tl:%s and the end %s)" % (name, rc, kib, summary_tag(out), tl8, end))
        self.rec("b.rss", rnd, arm, "dev302", kib, "KiB", l0, l1)

    def b_lat(self, rnd, arm, s):
        d, end = self.b_tree(arm)
        name = "b-lat-%s-%d-%s" % (s, rnd, arm)
        kt = os.path.join(self.out, "b-kt-%s-%d-%s.tsv" % (s, rnd, arm))
        rc, out, err, l0, l1 = self.run(name, T_REPLAY, d, [
            os.path.join(d, "target/release/shanjie-replay"), "--rows", "eval/dev/%s.txt" % s, "--profile", "chat", "--key-times", kt])
        if rc == 142:
            self.rec("b.lat_failed", rnd, arm, s, rc, "rc", l0, l1)
            self.fail("B", "head", arm, "(B) %s stopped after %d s" % (name, T_REPLAY))
            return
        p95, n = p95_ms(open(kt).read()) if os.path.exists(kt) else (None, 0)
        kib = peak_rss_kib(err)
        if rc != 0 or p95 is None or kib is None or not tag_ok(summary_tag(out), end=end):
            raise Broken("(B) %s: exit %d, p95 %s over %d keys, peak RSS %s, summary tag %s (wanted the end %s)" % (name, rc, p95, n, kib, summary_tag(out), end))
        self.rec("b.p95", rnd, arm, s, "%.3f" % p95, "ms", l0, l1)
        self.rec("b.keys", rnd, arm, s, n, "keys", l0, l1)
        self.rec("b.rss_replay", rnd, arm, s, kib, "KiB", l0, l1)

    # ---- the whole measurement
    def measure(self):
        a = self.a
        old_sha, new_sha = sha256(a.old_model), sha256(a.new_model)
        if open(os.path.join(a.a, "data/tinylm.sjtl.sha256")).read().split()[0] != old_sha:
            raise Broken("the old model is not the file data/tinylm.sjtl.sha256 of the (A) tree names")
        if new_sha != a.new_sha256:
            raise Broken("the new model has SHA-256 %s, not %s" % (new_sha, a.new_sha256))
        if old_sha == new_sha:
            raise Broken("the old and the new model are one file")
        self.sha8 = {"old": old_sha[:8], "new": new_sha[:8]}
        self.sepk = {"old": model_k(a.old_model), "new": model_k(a.new_model)}
        self.lc = longctx_rows.stats(a.lc_rows)
        if self.lc[1] < 64:
            raise Broken("the long-context row file has a context of %d Han characters (fewer than 64)" % self.lc[1])
        self.header = [
            "latency gate: %s; macOS %s, %s, %s logical CPUs" % (time.strftime("%Y-%m-%d %H:%M %Z"), platform.mac_ver()[0],
                subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip(), os.cpu_count()),
            "trees: (A) %s; (B) base %s, head %s" % tuple(subprocess.run(["git", "-C", d, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip() for d in (a.a, a.base, a.head)),
            "models: old %s (boundary K %s), new %s (boundary K %s)" % (old_sha, self.sepk["old"], new_sha, self.sepk["new"]),
            "long-context rows: %d rows, shortest context %d Han characters, SHA-256 %s (scripts/ci/longctx_rows.py)" % self.lc,
            "rounds: %d, arms alternating; one thread; the runner is shared hardware, the load before and after every run is in raw.tsv" % a.rounds,
            "the 128-syllable test goes through the engine without --tinylm-paired: with the new model the colloquial term does not act there",
        ]
        R = range(1, a.rounds + 1)
        for r in R:
            for arm in ("old", "new"):
                self.a_eval("a.dev", "dev302", "a-rss-%d-%s" % (r, arm), T_DEV, r, arm, ["--dev", "302"])
        for r in R:
            for arm in ("old", "new"):
                self.a_eval("a.lc", "longctx", "a-lc-%d-%s" % (r, arm), T_LC, r, arm, ["--rows", a.lc_rows])
        dst = os.path.join(a.a, "data/lm/tinylm.sjtl")
        try:
            for r in R:
                for arm in ("old", "new"):
                    self.a_test(r, arm)
        finally:   # the tree is its commit's again for (B) and for whoever looks at it afterwards
            shutil.copyfile(a.old_model, dst)
        if sha256(dst) != old_sha:
            raise Broken("the old model is not back on %s" % dst)
        for r in R:
            for arm in ("base", "head"):
                self.b_rss(r, arm)
        for s in SETS:
            for r in R:
                for arm in ("base", "head"):
                    self.b_lat(r, arm, s)

    # ---- verdict
    def verdict(self):
        """Rows (item, what, old/base, new/head, difference, gate, PASS/FAIL/-) and whether every gate passes."""
        v, rows, ok = self.vals, [], True

        def pair(kind, r, subject, arms):
            x, y = v.get((kind, r, arms[0], subject)), v.get((kind, r, arms[1], subject))
            x, y = (float(x) if x is not None else None), (float(y) if y is not None else None)
            return x, y, (y - x if x is not None and y is not None else None)

        def add(item, what, x, y, d, unit, gate="", passed=None):
            nonlocal ok
            f = (lambda z: "none" if z is None else ("%.3f" % z if unit == "ms" else "%d" % z) + " " + unit)
            rows.append((item, what, f(x), f(y), "none" if d is None else ("%+.3f" % d if unit == "ms" else "%+d" % d) + " " + unit,
                         gate or "reported", "-" if passed is None else ("PASS" if passed else "FAIL")))
            if passed is False:
                ok = False

        R, AB = range(1, self.a.rounds + 1), ("old", "new")
        for r in R:
            add("A", "shanjie-eval peak RSS, dev302, round %d" % r, *pair("a.dev_rss", r, "dev302", AB), "KiB")
        for r in R:
            add("A", "RSS growth around the .sjtl load, round %d" % r, *pair("a.dev_model_load_rss", r, "dev302", AB), "KiB")
        for r in R:
            x, y, d = pair("a.lc_p95", r, "longctx", AB)
            add("A", "rerank p95, 64-character contexts x up to 8 candidates, round %d (n %s rows)" % (r, v.get(("a.lc_n", r, "new", "longctx"))),
                x, y, d, "ms", "new < %g ms" % LC_GATE_MS, y is not None and y < LC_GATE_MS)
            add("A", "rerank p50, same run, round %d" % r, *pair("a.lc_p50", r, "longctx", AB), "ms")
            add("A", "shanjie-eval peak RSS, same run, round %d" % r, *pair("a.lc_rss", r, "longctx", AB), "KiB")
        for r in R:
            add("A", "PROXY: CLI rerank p95 on dev302 (short contexts), round %d" % r, *pair("a.dev_p95", r, "dev302", AB), "ms")
        for r in R:
            for key, what in (("key_on", "key p95, rerank on"), ("job", "RerankJob::run p95 (PROXY for long windows)"), ("key_off", "key p95, rerank off"), ("max_on", "slowest key, rerank on")):
                add("A", "128-syllable test, %s, round %d" % (what, r), *pair("a.test_%s%s" % (key, "" if key.startswith("max") else "_p95"), r, "128syl", AB), "ms")
            x, y, d = pair("a.test_rc", r, "128syl", AB)
            add("A", "128-syllable test exit code (its own limits: key p95 < 16 ms, RerankJob::run p95 < 300 ms), round %d" % r, x, y, d, "rc", "new exit code 0", y == 0)
        if self.failed["A"]:
            add("A", "a run with the new model failed before it printed numbers (see the log)", None, None, None, "rc", "every run completes", False)
        BH = ("base", "head")
        for r in R:
            add("B", "shanjie-eval peak RSS, dev302, round %d" % r, *pair("b.rss", r, "dev302", BH), "KiB")
        for s in SETS:
            for r in R:
                add("B", "shanjie-replay peak RSS, %s, round %d" % (s, r), *pair("b.rss_replay", r, s, BH), "KiB")
        for s in SETS:
            for r in R:
                x, y, d = pair("b.p95", r, s, BH)
                add("B", "key p95, %s, round %d (%s keys)" % (s, r, v.get(("b.keys", r, "head", s))), x, y, d, "ms",
                    "head - base <= %g ms and head < %g ms" % (DELTA_GATE_MS, P95_GATE_MS), d is not None and d <= DELTA_GATE_MS and y < P95_GATE_MS)
        if self.failed["B"]:
            add("B", "a run of the head tree hit its time limit (see the log)", None, None, None, "rc", "every run completes", False)
        return rows, ok

    def write(self):
        rows, ok = self.verdict()
        items = {i: all(r[6] != "FAIL" for r in rows if r[0] == i) for i in ("A", "B")}
        tail = ["ITEM A (new small model against the old one, same binaries): %s" % ("PASS" if items["A"] else "FAIL"),
                "ITEM B (head tree against base tree): %s" % ("PASS" if items["B"] else "FAIL")]
        cols = ("item", "measure", "old / base", "new / head", "difference", "gate", "verdict")
        with open(os.path.join(self.out, "result.txt"), "w") as f:
            f.write("\n".join(self.header) + "\n\n" + "\n".join("\t".join(r) for r in [cols] + rows) + "\n\n" + "\n".join(tail) + "\n")
        md = "## Latency gate\n\n" + "\n".join("- " + h for h in self.header) + "\n\n| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
        md += "".join("| " + " | ".join(r) + " |\n" for r in rows) + "\n" + "\n".join("**%s**" % t for t in tail) + "\n"
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
                f.write(md)
        print(open(os.path.join(self.out, "result.txt")).read(), flush=True)
        return ok


def self_test():
    err = ("tinylm: rerank n=298 p50=1.234 ms p95=5.678 ms; rows kept first (candidate over the context) 3; load 12 ms, rss Some(1000) -> Some(1500) KiB around the .sjtl load\n"
           "        3.10 real         3.00 user         0.10 sys\n           209715200  maximum resident set size\n")
    assert parse_rerank(err) == {"n": 298, "p50": 1.234, "p95": 5.678, "kept": 3, "load": 12, "load_rss": 500}
    assert parse_rerank("tinylm: nothing") is None and peak_rss_kib(err) == 204800 and peak_rss_kib("") is None
    out = "## dev302  lm-chat+ctx+tl:5dce15a6+sep:12+p:acg+colloq:81c2470d+pair:5dce15a6+t3c  {'n': 302, 'top1': 280}\n"
    tag = summary_tag(out)
    assert tag and tag_ok(tag, "5dce15a6", True, 12, "+t3c")
    assert not tag_ok(tag, "5dce15a6", True, 1) and not tag_ok(tag, "5dce15a6", True, None) and not tag_ok(tag, "aaaaaaaa")
    assert not tag_ok(tag, end="+t3") and tag_ok(tag.replace("+sep:12", ""), "5dce15a6", True, None) and summary_tag(out + out) is None
    assert p95_ms("".join("k\t%d\n" % (i * 1000) for i in range(1, 101)) + "c\t999999\n") == (95.0, 100)
    assert p95_ms("k\t7000\n") == (7.0, 1) and p95_ms("c\t1\n") == (None, 0)
    m = MEDIAN.search("round 1 ...\nmedian of 5: rerank on key p95 3.100 ms, RerankJob::run p95 40.000 ms; rerank off key p95 2.000 ms\n")
    assert m and m.groups() == ("3.100", "40.000", "2.000")
    assert SLOWEST.search("slowest single key over the 5 rounds: rerank on 9.000 ms (syllable 3), rerank off 8.000 ms (syllable 4)\n").groups() == ("9.000", "8.000")
    print("self-test ok")


def main():
    if "--self-test" in sys.argv:
        return self_test()
    ap = argparse.ArgumentParser()
    for name in ("a", "base", "head", "old-model", "new-model", "new-sha256", "lc-rows", "test-exe", "out"):
        ap.add_argument("--" + name, required=True)
    ap.add_argument("--rounds", type=int, default=2)
    a = ap.parse_args()
    if a.rounds < 2:
        sys.exit("error: at least two rounds")
    for k in ("a", "base", "head", "old_model", "new_model", "lc_rows", "test_exe", "out"):
        setattr(a, k, os.path.abspath(getattr(a, k)))
    g = Gate(a)
    try:
        g.measure()
    except Broken as e:
        print("STOP %s: no verdict (the raw values so far are in %s/raw.tsv)" % (e, a.out), file=sys.stderr)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
                f.write("## Latency gate\n\nNo verdict: %s\n" % e)
        sys.exit(2)
    sys.exit(0 if g.write() else 1)


if __name__ == "__main__":
    main()
