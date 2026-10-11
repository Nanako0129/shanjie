"""S7a: run the same step for every evaluation set, so the README's commands are one line per machine.

  python run_sets.py dumps --work DIR --tune TUNEDIR --sets cvtune wikitune dev302 typing76 user-reported [discordtune --discord FILE]
                         [--reported-rows 66]
      -> DIR/dumps/<set>.<setting>.dump and <set>.<setting>.top1 (the summary line's top1) from reference/proto/lm_eval.py
         (shipping model, --context, no word pack; --lm defaults to data/lm/bigram.sjlm, classes.sjc next to it)
  python run_sets.py score --work DIR --out SCORES --models S M --backend numpy|torch --sets ... [--ppl]
      -> SCORES/<model>.<setting>.<set>.jsonl (score.py, --expect-top1 read from the .top1 file); --ppl adds DIR/ppl/<model>.<set>.json
         for cvtune and wikitune (chat setting only: the gold perplexity does not depend on the setting)
"""
import argparse
import hashlib
import os
import re
import subprocess
import sys

import tinylm
from tinylm import ROOT

import score

SETTINGS = ("chat", "formal")
EVAL_DEV = os.path.join(ROOT, "eval", "dev")
# S7b section 2.2: `--reported-rows 66` keeps user-reported at S7a's first 66 rows (new reports are appended at the end), so n
# matches the models scored on them; without it the whole file is used, as every other evaluation does.
REPORTED_ROWS = 66
REPORTED_SHA = "6e338dc1293060162585c09e02bbb252c93148c9928d61007faea5e8860369e8"


def check_reported(path):
    # Universal newlines, as lm_eval.rows_of reads the rows: a CRLF checkout (git on Windows) hashes like the LF one.
    with open(path, encoding="utf-8") as f:
        head = [next(f, "") for _ in range(REPORTED_ROWS)]
    got = hashlib.sha256("".join(head).encode()).hexdigest()
    if got != REPORTED_SHA:
        sys.exit(f"run_sets: the first {REPORTED_ROWS} rows of {path} changed (sha256 {got[:16]}…, expected {REPORTED_SHA[:16]}…)")


def rows_args(name, a):
    if name in ("cvtune", "wikitune"):
        return ["--rows", os.path.join(a.tune, f"{name}.txt")]
    if name == "dev302":
        return ["--dev", "302"]
    if name == "typing76":
        return ["--rows", os.path.join(EVAL_DEV, "user-typing.txt")]
    if name == "user-reported":
        path = os.path.join(EVAL_DEV, "user-reported.txt")
        return ["--rows", path] + (["--limit", str(REPORTED_ROWS)] if getattr(a, "reported_rows", None) else [])
    if name == "discordtune":
        if not a.discord:
            sys.exit("run_sets: discordtune needs --discord FILE")
        return ["--rows", a.discord]
    sys.exit(f"run_sets: unknown set {name}")


def check_model(lm):
    """The model lm_eval will load and the classes.sjc beside it must be the pinned ones (data/*.sha256); score.py's --expect-top1
    comes from the same lm_eval run, so it cannot notice a wrong model."""
    lm = lm or os.path.join(ROOT, "data", "lm", "bigram.sjlm")
    for f, pin in ((lm, "bigram.sjlm.sha256"), (os.path.join(os.path.dirname(lm), "classes.sjc"), "classes.sjc.sha256")):
        if not os.path.isfile(f):
            sys.exit(f"run_sets: {f} not found")
        want = open(os.path.join(ROOT, "data", pin)).read().split()[0]
        if hashlib.sha256(open(f, "rb").read()).hexdigest() != want:
            sys.exit(f"run_sets: SHA-256 of {f} does not match data/{pin}")


def cmd_dumps(a):
    check_model(a.lm)
    os.makedirs(os.path.join(a.work, "dumps"), exist_ok=True)
    for name in a.sets:
        for setting in SETTINGS:
            dump = os.path.join(a.work, "dumps", f"{name}.{setting}.dump")
            cmd = [sys.executable, os.path.join(ROOT, "reference", "proto", "lm_eval.py"), "--profile", setting, "--context",
                   "--dump", dump, "--name", name] + rows_args(name, a) + (["--lm", a.lm] if a.lm else [])
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
            if r.returncode:
                sys.exit(f"run_sets: lm_eval failed for {name} {setting}:\n{r.stderr[-2000:]}")
            m = re.search(r"'n': (\d+), 'top1': (\d+)", r.stdout)
            if not m:
                sys.exit(f"run_sets: no summary line ('n': .., 'top1': ..) in lm_eval output for {name} {setting}:\n{r.stdout[-500:]}")
            open(os.path.join(a.work, "dumps", f"{name}.{setting}.top1"), "w").write(m.group(2))
            print(r.stdout.strip(), flush=True)


def cmd_score(a):
    os.makedirs(a.out, exist_ok=True)
    os.makedirs(os.path.join(a.work, "ppl"), exist_ok=True)
    for model in a.models:
        npz = os.path.join(a.work, f"tinylm-{model}.npz")
        for name in a.sets:
            for setting in SETTINGS:
                base = os.path.join(a.work, "dumps", f"{name}.{setting}")
                args = ["--npz", npz, "--backend", a.backend, "--dump", base + ".dump", "--name", f"{model} {setting} {name}",
                        "--expect-top1", open(base + ".top1").read().strip(), "--out", os.path.join(a.out, f"{model}.{setting}.{name}.jsonl")] + rows_args(name, a)
                if a.ppl and setting == "chat" and name in ("cvtune", "wikitune"):
                    args += ["--ppl-out", os.path.join(a.work, "ppl", f"{model}.{name}.json")]
                score.main(args)


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for cmd in ("dumps", "score"):
        p = sub.add_parser(cmd)
        p.add_argument("--work", required=True); p.add_argument("--sets", nargs="+", required=True)
        p.add_argument("--tune", help="directory holding cvtune.txt and wikitune.txt"); p.add_argument("--discord")
        p.add_argument("--reported-rows", type=int, choices=[REPORTED_ROWS],
                       help="user-reported: only S7a's first 66 rows, their SHA-256 checked before anything runs (S7b, S7d)")
        if cmd == "dumps":
            p.add_argument("--lm")
        else:
            p.add_argument("--out", required=True); p.add_argument("--models", nargs="+", default=["S", "M"])
            p.add_argument("--backend", choices=["numpy", "torch"], default="numpy"); p.add_argument("--ppl", action="store_true")
    a = ap.parse_args(argv)
    if a.reported_rows and "user-reported" in a.sets:
        check_reported(os.path.join(EVAL_DEV, "user-reported.txt"))      # before any set runs, not when the loop reaches it
    {"dumps": cmd_dumps, "score": cmd_score}[a.cmd](a)


if __name__ == "__main__":
    main()
