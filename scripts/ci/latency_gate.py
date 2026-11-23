#!/usr/bin/env python3
"""The two latency measurements of .github/workflows/latency-gate.yml, each on one macOS runner with its arms alternating.
Either one alone or both: (A) runs when the --a group of arguments is given, (B) when --base and --head are.

(A) one tree, one set of binaries, two small models (old = the tree's data/lm/tinylm.sjtl, new = the candidate),
    old, new, old, new:
    memory    peak RSS of shanjie-eval on dev302 (chat, the shipping flags, one thread; `/usr/bin/time -l`), and the
              CLI's own `tinylm: rerank` line of that run (per-row rerank on short contexts: a proxy). Reported.
    long ctx  the same flags over a row file whose every context is at least 64 Han characters (scripts/ci/longctx_rows.py),
              the rerank p95 read from stderr's `tinylm: rerank n=.. p50=.. p95=..` line. Gate: new p95 < 300 ms.
    128 syl   core/tests/engine_predict.rs `timing_composition_cap_128_syllables`, the test executable run directly.
              It reads data/lm/tinylm.sjtl of the tree, so the model under test is copied onto that path (hash checked)
              and the old one put back afterwards. Gate per round: the new model's exit code is 0, judged only when
              the old model's is 0 in that round. The test asserts absolute limits (key p95 < 16 ms) that hold on an
              idle Mac; where the old model fails them too, the machine is too slow to tell the models apart by them,
              so the round's numbers are reported, the result says so, and the test is to be run on an idle Mac.
              A new-model run that prints no numbers or hits the time limit fails the item either way.
    Every shanjie-eval run names the model with `--tinylm FILE --tinylm-paired`; its summary tag must carry
    `+tl:<sha8>` and `+pair:<sha8>` of that model and `+sep:K` exactly when the file's header has a boundary threshold K,
    and stderr must carry the rerank line: otherwise the model did not run as intended.
(B) base tree against head tree, base, head, base, head:
    memory    peak RSS of shanjie-eval on dev302 (each tree's own data/lm; the summary tags seen are in the result). Reported.
    latency   shanjie-replay over eval/dev/user-typing.txt and user-reported.txt with `--key-times`; p95 is the
              nearest-rank 95th percentile of the `k` lines, in ms. Gate per set and round: head - base <= 1 ms and head < 16 ms.

A failed or timed-out run of the old model or the base tree is a broken setup (exit 2, no verdict); of the new model or
the head tree a failed gate (the other runs go on). Exit 0: every gate passes; 1: a gate fails.
Writes OUT/raw.tsv (every run's values), OUT/result.txt, every run's stdout/stderr/key times, and the result table to
$GITHUB_STEP_SUMMARY when that is set. `--self-test` checks the parsers, the input check and the verdict on fixed values
and exits. `--check-inputs` checks the workflow's inputs (environment IN_A_TREE, IN_A_RELEASE_ID, IN_A_ASSET, IN_A_SHA256,
IN_B_BASE, IN_B_HEAD, IN_ROUNDS), writes `a=true|false`, `b=true|false` to $GITHUB_OUTPUT and exits 1 on a bad one.
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


HEX40, HEX64 = re.compile(r"[0-9a-f]{40}"), re.compile(r"[0-9a-f]{64}")
INPUTS = {"a_tree": HEX40, "a_release_id": re.compile(r"[1-9][0-9]{0,18}"), "a_asset": re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}"),
          "a_sha256": HEX64, "b_base": HEX40, "b_head": HEX40, "rounds": re.compile(r"[2-9]")}
GROUP_A, GROUP_B = ("a_tree", "a_release_id", "a_asset", "a_sha256"), ("b_base", "b_head")


def check_inputs(v):
    """v: input name -> text ("" or missing = not given). Returns (run A, run B); ValueError names every bad input.
    A commit is 40 lowercase hex digits, a hash 64, the release id a number, the asset name has no `/` (letters, digits, `.`, `_`, `-`),
    rounds 2 to 9; a group is given whole or not at all, at least one is given, and base is not head."""
    v = {k: v.get(k) or "" for k in INPUTS}
    bad = ["%s=%r" % (k, v[k]) for k, pat in INPUTS.items() if v[k] and not pat.fullmatch(v[k])]
    if not v["rounds"]:
        bad.append("rounds is empty")
    run = []
    for name, group in (("A", GROUP_A), ("B", GROUP_B)):
        given = [k for k in group if v[k]]
        if given and len(given) < len(group):
            bad.append("(%s) needs all of %s, missing %s" % (name, ", ".join(group), ", ".join(k for k in group if not v[k])))
        run.append(bool(given))
    if not any(run):
        bad.append("neither (A) nor (B) is given")
    if v["b_base"] and v["b_base"] == v["b_head"]:
        bad.append("b_base and b_head are one commit")
    if bad:
        raise ValueError("; ".join(bad))
    return tuple(run)


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
        self.failed, self.vals, self.tags = {"A": False, "B": False}, {}, {"base": [], "head": []}
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
    def b_tag(self, arm, out):
        """The summary tag of a (B) run, kept for the result: the two trees' flags and models are their own, so no tag is expected here."""
        tag = summary_tag(out)
        if tag and tag not in self.tags[arm]:
            self.tags[arm].append(tag)
        return tag

    def b_rss(self, rnd, arm):
        d = self.a.base if arm == "base" else self.a.head
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
        if rc != 0 or kib is None:   # a failed run: a failed gate for the head tree, no verdict for the base tree
            self.rec("b.rss_failed", rnd, arm, "dev302", rc, "rc", l0, l1)
            self.fail("B", "head", arm, "(B) %s: exit %d, peak RSS %s" % (name, rc, kib))
            return
        if not tag_ok(self.b_tag(arm, out), tl8):   # the wrong model in the tree: a broken setup on either side
            raise Broken("(B) %s: summary tag %s (wanted +tl:%s)" % (name, summary_tag(out), tl8))
        self.rec("b.rss", rnd, arm, "dev302", kib, "KiB", l0, l1)

    def b_lat(self, rnd, arm, s):
        d = self.a.base if arm == "base" else self.a.head
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
        if rc != 0 or p95 is None or kib is None or self.b_tag(arm, out) is None:   # a failed gate for the head tree, no verdict for the base tree
            self.rec("b.lat_failed", rnd, arm, s, rc, "rc", l0, l1)
            self.fail("B", "head", arm, "(B) %s: exit %d, p95 %s over %d keys, peak RSS %s, summary tag %s (wanted one summary line)" % (name, rc, p95, n, kib, summary_tag(out)))
            return
        self.rec("b.p95", rnd, arm, s, "%.3f" % p95, "ms", l0, l1)
        self.rec("b.keys", rnd, arm, s, n, "keys", l0, l1)
        self.rec("b.rss_replay", rnd, arm, s, kib, "KiB", l0, l1)

    # ---- the whole measurement
    def measure(self):
        a = self.a
        rev = lambda d: subprocess.run(["git", "-C", d, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        self.header = [
            "latency gate: %s; macOS %s, %s, %s logical CPUs" % (time.strftime("%Y-%m-%d %H:%M %Z"), platform.mac_ver()[0],
                subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip(), os.cpu_count()),
            "trees: " + "; ".join((["(A) %s" % rev(a.a)] if a.do_a else []) + (["(B) base %s, head %s" % (rev(a.base), rev(a.head))] if a.do_b else [])),
            "rounds: %d, arms alternating; one thread; the runner is shared hardware, the load before and after every run is in raw.tsv" % a.rounds,
        ]
        if a.do_a:
            self.measure_a()
        if a.do_b:
            self.measure_b()

    def measure_a(self):
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
        self.header += [
            "models: old %s (boundary K %s), new %s (boundary K %s)" % (old_sha, self.sepk["old"], new_sha, self.sepk["new"]),
            "long-context rows: %d rows, shortest context %d Han characters, SHA-256 %s (scripts/ci/longctx_rows.py)" % self.lc,
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

    def measure_b(self):
        R = range(1, self.a.rounds + 1)
        for r in R:
            for arm in ("base", "head"):
                self.b_rss(r, arm)
        for s in SETS:
            for r in R:
                for arm in ("base", "head"):
                    self.b_lat(r, arm, s)

    # ---- verdict
    def verdict(self):
        """Rows (item, what, old/base, new/head, difference, gate, PASS/FAIL/-), whether every gate passes, and notes for the end of the result."""
        v, rows, ok, notes = self.vals, [], True, []

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

        R = range(1, self.a.rounds + 1)
        if self.a.do_a:
            self.verdict_a(R, pair, add, notes)
        if self.a.do_b:
            self.verdict_b(R, pair, add)
        return rows, ok, notes

    def verdict_a(self, R, pair, add, notes):
        v, AB = self.vals, ("old", "new")
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
        unjudged = []
        for r in R:
            for key, what in (("key_on", "key p95, rerank on"), ("job", "RerankJob::run p95 (PROXY for long windows)"), ("key_off", "key p95, rerank off"), ("max_on", "slowest key, rerank on")):
                add("A", "128-syllable test, %s, round %d" % (what, r), *pair("a.test_%s%s" % (key, "" if key.startswith("max") else "_p95"), r, "128syl", AB), "ms")
            x, y, d = pair("a.test_rc", r, "128syl", AB)
            what = "128-syllable test exit code (its own limits: key p95 < 16 ms, RerankJob::run p95 < 300 ms), round %d" % r
            if x == 0:
                add("A", what, x, y, d, "rc", "new exit code 0", y == 0)
            else:   # the old model fails the absolute limits here too: this machine cannot judge them
                add("A", what, x, y, d, "rc", "NOT JUDGED (the old model's exit code is not 0)")
                unjudged.append(r)
        if unjudged:
            notes.append("ITEM A, 128-syllable test: NOT JUDGED in round%s %s. The old model fails the test's absolute limits on this machine too, "
                         "so its exit code says nothing about the new model here: run the test on an idle Mac for this item."
                         % ("" if len(unjudged) == 1 else "s", ", ".join(map(str, unjudged))))
        if self.failed["A"]:
            add("A", "a run with the new model failed before it printed numbers (see the log)", None, None, None, "rc", "every run completes", False)

    def verdict_b(self, R, pair, add):
        v, BH = self.vals, ("base", "head")
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
            add("B", "a run of the head tree failed or hit its time limit (see the log)", None, None, None, "rc", "every run completes", False)

    def write(self):
        rows, ok, notes = self.verdict()
        if self.a.do_b:
            self.header.append("(B) summary tags: base %s; head %s" % tuple(", ".join(self.tags[arm]) or "none" for arm in ("base", "head")))
        tail = ["ITEM %s: %s" % (name, "PASS" if all(r[6] != "FAIL" for r in rows if r[0] == i) else "FAIL")
                for i, name, do in (("A", "A (new small model against the old one, same binaries)", self.a.do_a),
                                    ("B", "B (head tree against base tree)", self.a.do_b)) if do] + notes
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
    c, h = "0123456789abcdef0123456789abcdef01234567", "5dce15a6077561aae15e3b6166aaf1cca0a9184ee03032b912d5700f91c37730"
    full = {"a_tree": c, "a_release_id": "454336190", "a_asset": "blp-20261012-int8-v6.sjtl", "a_sha256": h, "b_base": c, "b_head": c[::-1], "rounds": "2"}
    only = lambda keys: {k: full[k] for k in keys + ("rounds",)}
    assert check_inputs(full) == (True, True) and check_inputs(only(GROUP_A)) == (True, False) and check_inputs(only(GROUP_B)) == (False, True)
    for change in ({"a_tree": c[:39]}, {"a_tree": c.upper()}, {"a_tree": "main"}, {"a_tree": c + "\n"}, {"a_release_id": "v1.0"}, {"a_release_id": "0"},
                   {"a_asset": "a/b.sjtl"}, {"a_asset": "../b.sjtl"}, {"a_asset": "a b"}, {"a_asset": 'a"b'}, {"a_sha256": h[:63]}, {"a_sha256": c},
                   {"b_head": c}, {"b_head": ""}, {"a_asset": ""}, {"rounds": "1"}, {"rounds": ""}, {"rounds": "2 "}, {"rounds": "10"}):
        try:
            check_inputs({**full, **change})
        except ValueError:
            continue
        raise AssertionError("accepted %r" % change)
    for none in ({"rounds": "2"}, {}):
        try:
            check_inputs(none)
        except ValueError as e:
            assert "neither" in str(e)
        else:
            raise AssertionError("accepted no group")
    g = Gate.__new__(Gate)   # the verdict alone, on fixed values
    g.a, g.failed = argparse.Namespace(rounds=1, do_a=True, do_b=False), {"A": False, "B": False}
    g.vals = {("a.lc_p95", 1, "old", "longctx"): 120.0, ("a.lc_p95", 1, "new", "longctx"): 139.0,
              ("a.test_rc", 1, "old", "128syl"): 101, ("a.test_rc", 1, "new", "128syl"): 101}
    rows, ok, notes = g.verdict()   # both models fail the test's limits: reported, not judged, said so
    assert ok and len(notes) == 1 and "NOT JUDGED" in notes[0] and all(r[0] == "A" for r in rows) and [r[6] for r in rows if "exit code" in r[1]] == ["-"]
    g.vals[("a.test_rc", 1, "old", "128syl")] = 0   # the old model passes them: the new one must too
    rows, ok, notes = g.verdict()
    assert not ok and not notes and [r[6] for r in rows if "exit code" in r[1]] == ["FAIL"]
    g.vals[("a.test_rc", 1, "new", "128syl")] = 0
    assert g.verdict()[1]
    g.vals[("a.lc_p95", 1, "new", "longctx")] = 300.0
    assert not g.verdict()[1]
    g.a = argparse.Namespace(rounds=1, do_a=False, do_b=True)
    g.vals = {("b.p95", 1, "base", s): "3.000" for s in SETS}
    g.vals.update({("b.p95", 1, "head", s): "3.900" for s in SETS})
    rows, ok, notes = g.verdict()
    assert ok and not notes and all(r[0] == "B" for r in rows)
    g.vals[("b.p95", 1, "head", SETS[0])] = "4.001"
    assert not g.verdict()[1]
    g.vals[("b.p95", 1, "head", SETS[0])] = "3.900"
    g.fail("B", "head", "head", "(B) a head run: exit 101")   # a failed head run: a failed gate (exit 1), not a broken setup
    rows, ok, notes = g.verdict()
    assert not ok and g.failed["B"] and [r[6] for r in rows if "failed or hit its time limit" in r[1]] == ["FAIL"]
    try:
        g.fail("B", "head", "base", "(B) a base run: exit 101")   # a failed base run: no verdict (exit 2)
    except Broken:
        pass
    else:
        raise AssertionError("a failed base run did not stop the measurement")
    print("self-test ok")


def main():
    if "--self-test" in sys.argv:
        return self_test()
    if "--check-inputs" in sys.argv:
        try:
            run = check_inputs({k: os.environ.get("IN_" + k.upper(), "") for k in INPUTS})
        except ValueError as e:
            sys.exit("error: bad inputs: %s" % e)
        text = "a=%s\nb=%s\n" % tuple(str(x).lower() for x in run)
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                f.write(text)
        return print(text, end="")
    ARGS_A, ARGS_B = ("a", "old_model", "new_model", "new_sha256", "lc_rows", "test_exe"), ("base", "head")
    ap = argparse.ArgumentParser()
    for name in ARGS_A + ARGS_B:
        ap.add_argument("--" + name.replace("_", "-"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--rounds", type=int, default=2)
    a = ap.parse_args()
    if a.rounds < 2:
        sys.exit("error: at least two rounds")
    for item, names in (("a", ARGS_A), ("b", ARGS_B)):   # each group whole or not at all
        given = [k for k in names if getattr(a, k) is not None]
        if given and len(given) < len(names):
            sys.exit("error: (%s) needs all of %s" % (item.upper(), ", ".join("--" + k.replace("_", "-") for k in names)))
        setattr(a, "do_" + item, bool(given))
    if not (a.do_a or a.do_b):
        sys.exit("error: give the (A) arguments, the (B) arguments, or both")
    for k in ("a", "base", "head", "old_model", "new_model", "lc_rows", "test_exe", "out"):
        if getattr(a, k) is not None:
            setattr(a, k, os.path.abspath(getattr(a, k)))
    g = Gate(a)
    try:
        g.measure()
    except (Broken, OSError, subprocess.CalledProcessError, ValueError) as e:   # a missing file or a failed git call is a broken setup (2), not a failed gate (1)
        print("STOP %s: no verdict (the raw values so far are in %s/raw.tsv)" % (e, a.out), file=sys.stderr)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
                f.write("## Latency gate\n\nNo verdict: %s\n" % e)
        sys.exit(2)
    sys.exit(0 if g.write() else 1)


if __name__ == "__main__":
    main()
