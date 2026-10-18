"""tools/shardcli.py against a fake CLI that writes the real CLI's output formats (cli/src/main.rs): sharded and unsharded runs must
write the same bytes for every output and the same summary line."""
import hashlib, os, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SHARD = os.path.join(HERE, "shardcli.py")

# The formats of cli/src/main.rs's LM mode: summary line, rowstats `row\tok\terrors\tgold_len`, dump `row\trank\tsurface\tscore`,
# word dump `row\t0\tgold` then `row\trank\twords`, scores `{"i": row, ..., "c": chosen, ...}`. Three candidates per row; with
# --tinylm the chosen one depends on the row's own text only, as in the real CLI.
FAKE = r'''#!/usr/bin/env python3
import hashlib, os, sys
a = sys.argv[1:]
opt = lambda f: a[a.index(f) + 1] if f in a else None
if opt("--rows") is None:
    print("## dev  lm-chat  {'n': 0, 'top1': 0, 'oracle@64': 0, 'top1_sha256': 'x'}"); sys.exit(0)
if os.environ.get("FAKE_FAIL") and "rows1" in opt("--rows"):
    sys.stderr.write("boom\n"); sys.exit(7)
text = open(opt("--rows"), encoding="utf-8").read()
rows = [l.split("|") for l in [x[:-1] if x.endswith("\r") else x for x in text.split("\n")] if len(l.split("|")) == 3]
tl = "--tinylm" in a
outs = {f: open(opt(f), "w", encoding="utf-8") for f in ("--rowstats", "--dump", "--dump-words", "--tinylm-scores") if opt(f)}
top1 = o64 = 0; firsts = []
for i, (ctx, truth, syl) in enumerate(rows, 1):
    cands = [truth + "甲", truth, "乙" + truth]
    c = (len(truth) + len(ctx)) % 3 if tl else 0
    ok = cands[c] == truth; top1 += ok; o64 += 1; firsts.append(cands[c])
    if "--rowstats" in outs: outs["--rowstats"].write(f"{i}\t{int(ok)}\t{0 if ok else 1}\t{len(truth)}\n")
    for r, s in enumerate(cands, 1):
        if "--dump" in outs: outs["--dump"].write(f"{i}\t{r}\t{s}\t{-1.5 * r!r}\n")
    if "--dump-words" in outs:
        outs["--dump-words"].write(f"{i}\t0\t{truth}\n")
        for r, s in enumerate(cands, 1): outs["--dump-words"].write(f"{i}\t{r}\t{' '.join(s)}\n")
    if "--tinylm-scores" in outs:
        outs["--tinylm-scores"].write('{"i": %d, "n": 3, "ng": [-1.5, -3.0, -4.5], "ok": [0, 1, 0], "c": %d, "ll": [-2.0, -1.0, -3.0]}\n' % (i, c))
sha = hashlib.sha256("\n".join(firsts).encode()).hexdigest()
name = opt("--name") or os.path.basename(opt("--rows"))
print(f"## {name}  lm-chat+ctx{'+tl:abcd1234' if tl else ''}  {{'n': {len(rows)}, 'top1': {top1}, 'oracle@64': {o64}, 'top1_sha256': '{sha}'}}")
'''


class ShardCli(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        d = self.td.name
        self.bin = os.path.join(d, "fake-cli")
        with open(self.bin, "w", encoding="utf-8") as f:
            f.write(FAKE)
        os.chmod(self.bin, 0o755)
        self.rows = os.path.join(d, "rows.txt")
        body = "".join(f"前{i % 4}|句子{'甲乙丙'[i % 3] * (i % 5 + 1)}|ㄐㄩˋ ㄗ\n" for i in range(47))
        with open(self.rows, "w", encoding="utf-8") as f:   # a comment, a blank line and a CRLF row: not rows / rows, as the CLI reads them
            f.write("# comment\n\n" + body[:200] + "前|句子|ㄐㄩˋ ㄗ˙\r\n" + body[200:])

    def tearDown(self):
        self.td.cleanup()

    def run_cli(self, tag, extra, shard_rows=None, env_extra=None):
        d = self.td.name
        outs = []
        for f in ("--rowstats", "--dump", "--dump-words", "--tinylm-scores"):
            if f in extra:
                outs.append(f)
        args = [a for a in extra if a not in outs]
        cmd_outs = sum(([f, os.path.join(d, f"{tag}.{f[2:]}")] for f in outs), [])
        env = dict(os.environ, SHANJIE_EVAL_BIN=self.bin, **(env_extra or {}))
        if shard_rows is None:
            cmd = [self.bin] + args + ["--rows", self.rows] + cmd_outs
        else:
            env["SHARD_ROWS"] = str(shard_rows)
            cmd = [sys.executable, SHARD] + args + ["--rows", self.rows] + cmd_outs
        p = subprocess.run(cmd, capture_output=True, text=True, env=env)
        files = {f: open(os.path.join(d, f"{tag}.{f[2:]}"), encoding="utf-8").read() for f in outs}
        return p, files

    def test_sharded_outputs_equal_one_run(self):
        for extra in (["--context", "--tinylm", "m", "--rowstats", "--dump", "--dump-words", "--tinylm-scores"],
                      ["--rowstats"], ["--tinylm", "m", "--rowstats"], ["--name", "given", "--rowstats", "--dump"]):
            ref, ref_files = self.run_cli("ref", extra)
            self.assertEqual(ref.returncode, 0, ref.stderr)
            for per in (1, 5, 13, 47, 100):
                got, files = self.run_cli(f"s{per}", extra, per)
                self.assertEqual(got.returncode, 0, got.stderr)
                self.assertEqual(got.stdout, ref.stdout, (extra, per))
                self.assertEqual(files, ref_files, (extra, per))

    def test_without_rows_runs_the_cli_unchanged(self):
        env = dict(os.environ, SHANJIE_EVAL_BIN=self.bin)
        p = subprocess.run([sys.executable, SHARD, "--dev", "302"], capture_output=True, text=True, env=env)
        self.assertEqual((p.returncode, p.stdout), (0, "## dev  lm-chat  {'n': 0, 'top1': 0, 'oracle@64': 0, 'top1_sha256': 'x'}\n"))

    def test_a_failing_shard_fails_the_run(self):
        out = os.path.join(self.td.name, "f.rowstats")
        env = dict(os.environ, SHANJIE_EVAL_BIN=self.bin, SHARD_ROWS="10", FAKE_FAIL="1")
        p = subprocess.run([sys.executable, SHARD, "--rows", self.rows, "--rowstats", out], capture_output=True, text=True, env=env)
        self.assertEqual(p.returncode, 7)
        self.assertIn("boom", p.stderr)
        self.assertFalse(os.path.exists(out), "no merged output after a failure")


if __name__ == "__main__":
    unittest.main()
