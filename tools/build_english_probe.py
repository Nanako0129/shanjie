#!/usr/bin/env python3
"""Builds the probes of "English or Zhuyin at the closing key" (docs/spec/english-mixed.md §16.8): English tokens beside Chinese, with the text in
front of them, from the documents the table never reads (tools/lib/english_tokens.py: bucket 8 the tuning probe, bucket 9 the confirmation probe).

Inputs and options are tools/build_english.py's (the same corpus, the same token rule and exclusions through english_tokens.tokens; the document
frequency rule is the table's alone). A sentence with a held-out Han run gives no row. For every token: `left` = the Han characters right before it
in its sentence (blanks before the token skipped), 16 at most; `keys` = the token's keycaps and `_` (Space).

Each probe: every occurrence in input order, then 5,000 drawn with random.Random(20261117) (all of them when fewer), kept in input order, written as
  <name>.rows      id|left|keys                       the sample
  <name>.all.keys  id<TAB>left<TAB><TAB>keys          the same rows as `english-sim --keys` reads them
Then `english-sim --keys <name>.all.keys --out <name>.all.out --auto-english off` (the engine without the comparison) picks the decision-point class:
the rows whose keys end as Chinese (every new unit a syllable, nothing unfinished, no run) and whose last key, the Space, closed a syllable at a
decision point (the tenth column names that key). Those rows are <name>.keys, the file the grid and the confirmation are measured on.

The rows hold text of sources that are not licensed for redistribution: the output folder is outside the repository (default
~/.cache/shanjie/english-probe/), and this tool prints only counts and SHA-256.

  python3 tools/build_english_probe.py --wiki DUMP --pool ... --g0v ... --forum-push ... --forum-body ... [--out-dir DIR] [--cli target/release/shanjie-eval]
"""
import argparse
import multiprocessing as mp
import os
import random
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_english as be  # noqa: E402

bc, et = be.bc, be.et
SEED = 20261117
SAMPLE = 5000
NAMES = {8: "tune", 9: "confirm"}
PUNCT = "_punct_"


def probe_batch(kind, items, conv, guard):
    """-> [(bucket, left, token)] of the batch's documents in buckets 8 and 9, in input order."""
    out = []
    for digest, sents in be.documents(kind, items, conv):
        b = digest[0] % 10
        if b not in NAMES:
            continue
        for s in sents:
            if guard is not None and any(guard.any(bc.fold(run)) for run in bc.HAN.findall(s)):
                continue
            out += [(b, et.left_text(s, a), t) for a, _, t in et.tokens(s, push=kind == "forum-push")]
    return out


def _work(job):
    return probe_batch(job[0], job[1], bc._W["conv"], bc._W.get("guard"))


def _init(guard):
    bc._W["conv"] = bc.load_conv()
    bc._W["guard"] = guard


def sample(rows, n=SAMPLE):
    """n of the rows with random.Random(SEED), in their input order (all of them when there are no more than n)."""
    if len(rows) <= n:
        return list(rows)
    return [rows[i] for i in sorted(random.Random(SEED).sample(range(len(rows)), n))]


def keys_line(rid, left, token):
    return f"{rid}\t{left}\t\t{token}_\n"


def decision_rows(keys_text, out_text):
    """The `--keys` input lines whose output line is Chinese closed at a decision point by the last key (the module docstring). -> (lines, multi):
    multi = how many of them are more than one syllable (the keys held a tone key before the end)."""
    keep, multi = [], 0
    ins, outs = keys_text.splitlines(), out_text.splitlines()
    if len(ins) != len(outs):
        raise ValueError("the probe and its output differ in length")
    for line, o in zip(ins, outs):
        f, g = line.split("\t"), o.split("\t")
        if f[0] != g[0] or len(g) != 10:
            raise ValueError("the output is not this probe's")
        units, last = g[2].split(), len(f[3]) - 1
        chinese = units and not any(u.startswith(PUNCT) for u in units) and g[3] == "" and g[4] == "0"
        if chinese and any(p.split(":")[0] == str(last) for p in g[9].split()):
            keep.append(line + "\n")
            multi += len(units) > 1
    return keep, multi


def main(argv=None):
    ap = argparse.ArgumentParser()
    be.add_inputs(ap)
    ap.add_argument("--out-dir", default=os.path.join(bc.CACHE_ROOT, "english-probe"))
    ap.add_argument("--cli", default=os.path.join(be.ROOT, "target", "release", "shanjie-eval"))
    a = ap.parse_args(argv)
    if not be.input_files(a):
        ap.error("no input")
    if os.path.abspath(a.out_dir).startswith(be.ROOT + os.sep):
        sys.exit("the probe rows hold text that does not go into the repository: give an --out-dir outside it")
    os.makedirs(a.out_dir, exist_ok=True)
    path = lambda name, ext: os.path.join(a.out_dir, f"{name}.{ext}")   # noqa: E731
    exts = ("rows", "all.keys", "all.out", "keys")
    for name in NAMES.values():
        for ext in exts:
            if os.path.exists(path(name, ext)):
                sys.exit(f"{name}.{ext} is already in the output folder: nothing is overwritten")
    guard = bc.guard_matcher(list(a.holdout_rows))
    found = {8: [], 9: []}
    with mp.Pool(a.procs, initializer=_init, initargs=(guard,)) as pool:
        for rows in be.in_order(pool, _work, be.jobs(a), 4 * a.procs):
            for b, left, token in rows:
                found[b].append((left, token))
    for b, name in NAMES.items():
        rows = [(f"{name[0]}{i + 1:05d}", left, token) for i, (left, token) in enumerate(sample(found[b]))]
        with open(path(name, "rows"), "w", encoding="utf-8", newline="\n") as f:
            f.write("".join(f"{rid}|{left}|{token}_\n" for rid, left, token in rows))
        keys_text = "".join(keys_line(*r) for r in rows)
        with open(path(name, "all.keys"), "w", encoding="utf-8", newline="\n") as f:
            f.write(keys_text)
        r = subprocess.run([a.cli, "english-sim", "--keys", path(name, "all.keys"), "--out", path(name, "all.out"), "--auto-english", "off"],
                           cwd=be.ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"english-sim failed on the {name} probe (exit {r.returncode})")
        with open(path(name, "all.out"), encoding="utf-8") as f:
            keep, multi = decision_rows(keys_text, f.read())
        with open(path(name, "keys"), "w", encoding="utf-8", newline="\n") as f:
            f.write("".join(keep))
        print(f"{name}: occurrences={len(found[b])} sampled={len(rows)} decision_point_rows={len(keep)} of_them_multi_syllable={multi} "
              f"rows_sha256={be.sha256_file(path(name, 'rows'))} keys_sha256={be.sha256_file(path(name, 'keys'))}")


if __name__ == "__main__":
    main()
