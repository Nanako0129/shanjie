#!/usr/bin/env python3
"""Row-sharded stand-in for the CLI (`target/release/shanjie-eval`, LM mode).

  SHANJIE_EVAL_BIN=target/release/shanjie-eval [SHARD_ROWS=300] tools/shardcli.py <the CLI's arguments>

Usable wherever a tool takes `--cli` (tools/triage.py) or runs the CLI itself. With `--rows FILE`, it cuts the file's rows into
contiguous shards of about SHARD_ROWS rows, runs the real CLI on every shard at the same time and merges the shards' outputs
(`--rowstats`, `--tinylm-scores`, `--dump`, `--dump-words`, the summary line) so that they are byte for byte what one unsharded run
writes: the CLI decodes every row on its own (cli/src/main.rs keeps no state across rows), so only the row numbers are shifted, the
counts are summed and `top1_sha256` is recomputed from the dump and the small model's choice (a dump, and with `--tinylm` a scores
file, are written to a private temporary folder when the caller did not ask for them, and deleted with it). Without `--rows`
(`--dev`, `--set holdout`) or with one shard, the real CLI runs unchanged. All shards run at once: SHARD_ROWS sets the parallelism.
The rows file is split into lines the way the CLI does (Rust `str::lines`: on `\n`, dropping one trailing `\r`)."""
import ast, hashlib, os, re, subprocess, sys, tempfile

BIN = os.environ["SHANJIE_EVAL_BIN"]
PER = int(os.environ.get("SHARD_ROWS", "300"))
OUTS = ("--rowstats", "--tinylm-scores", "--dump", "--dump-words")


def main(argv):
    if "--rows" not in argv:
        os.execv(BIN, [BIN] + argv)
    args, outs = [], {}
    it = iter(argv)
    for a in it:
        if a in OUTS:
            outs[a] = next(it)
        elif a == "--rows":
            rows_file = next(it)
        else:
            args.append(a)
    if "--limit" in args:
        sys.exit("shardcli: --limit is not supported")
    text_in = open(rows_file, encoding="utf-8").read()
    lines = [l[:-1] if l.endswith("\r") else l for l in text_in.split("\n")]
    lines = [l for l in lines if len(l.split("|")) == 3]   # cli/src/main.rs three_field_rows
    n = len(lines)
    k = max(1, -(-n // PER))
    if k == 1:
        os.execv(BIN, [BIN] + argv)
    tl = "--tinylm" in args
    with tempfile.TemporaryDirectory() as td:   # holds shard rows and dumps (sentences): deleted on exit
        want = dict(outs)
        want.setdefault("--dump", os.path.join(td, "dump"))
        if tl:
            want.setdefault("--tinylm-scores", os.path.join(td, "scores"))
        procs, bounds = [], []
        for s in range(k):
            lo, hi = s * n // k, (s + 1) * n // k
            bounds.append(lo)
            rf = os.path.join(td, f"rows{s}.txt")
            with open(rf, "w", encoding="utf-8") as o:
                o.write("".join(l + "\n" for l in lines[lo:hi]))
            cmd = [BIN] + args + ["--rows", rf]
            for flag in want:
                cmd += [flag, os.path.join(td, f"{flag[2:]}.{s}")]
            procs.append(subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        res = [(p.returncode, out, err) for p in procs for out, err in [p.communicate()]]
        for rc, _out, err in res:
            if rc != 0:
                sys.stderr.write(err)
                sys.exit(rc)
        # Summary: the tag and file name come from the real file name; n, top1, oracle@64 are sums.
        heads, nums, names = [], [], []
        for _rc, out, _err in res:
            line = [l for l in out.splitlines() if l.startswith("## ")]
            if len(line) != 1:
                sys.exit("shardcli: a shard did not print exactly one summary line")
            m = re.match(r"^## (.+)  (\S+)  (\{.*\})$", line[0])
            names.append(m.group(1))
            heads.append(m.group(2))
            nums.append(ast.literal_eval(m.group(3)))
        if len(set(heads)) != 1:
            sys.exit("shardcli: shards printed different setting tags")

        def merged(flag, shift):
            parts = []
            for s, lo in enumerate(bounds):
                with open(os.path.join(td, f"{flag[2:]}.{s}"), encoding="utf-8") as f:
                    parts.append("".join(shift(l, lo) for l in f))
            return "".join(parts)

        tab = lambda l, lo: (lambda i, rest: f"{int(i) + lo}\t{rest}")(*l.split("\t", 1))
        js = lambda l, lo: re.sub(r'^\{"i": (\d+),', lambda m: '{"i": %d,' % (int(m.group(1)) + lo), l, count=1)
        text = {"--rowstats": merged("--rowstats", tab) if "--rowstats" in want else None,
                "--dump": merged("--dump", tab), "--dump-words": merged("--dump-words", tab) if "--dump-words" in want else None,
                "--tinylm-scores": merged("--tinylm-scores", js) if "--tinylm-scores" in want else None}
        chosen = [0] * n
        if tl:
            for l in text["--tinylm-scores"].splitlines():
                m = re.match(r'^\{"i": (\d+),.*"c": (\d+)', l)
                chosen[int(m.group(1)) - 1] = int(m.group(2))
        surf = {}
        for l in text["--dump"].splitlines():
            i, r, s, _sc = l.split("\t")
            surf[(int(i), int(r))] = s
        firsts = [surf[(i + 1, chosen[i] + 1)] for i in range(n)]
        for flag, path in outs.items():
            with open(path, "w", encoding="utf-8") as o:
                o.write(text[flag])
        tot = {key: sum(x[key] for x in nums) for key in ("n", "top1", "oracle@64")}
        sha = hashlib.sha256("\n".join(firsts).encode()).hexdigest()
        name = os.path.basename(rows_file) if names[0] == "rows0.txt" else names[0]   # a --name given by the caller wins
        print(f"## {name}  {heads[0]}  {{'n': {tot['n']}, 'top1': {tot['top1']}, 'oracle@64': {tot['oracle@64']}, 'top1_sha256': '{sha}'}}")
        sys.stderr.write(f"shardcli: {k} shards of {n} rows\n")


if __name__ == "__main__":
    main(sys.argv[1:])
