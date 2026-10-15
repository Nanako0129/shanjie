"""Contract §2 anchor: in the null configuration (no trigram, no lambda/beta override, no x-context) harness.py must print the same
summary line as reference/proto/lm_eval.py, top1_sha256 included, for the same model and options. Different = stop condition (§5).

  python3 anchor.py --lm data/lm/bigram.sjlm                      (dev302, chat and formal, --context; the shipped model)
  python3 anchor.py --lm B0/bigram.sjlm --no-classes              (B0: no classes.sjc was built for it)
  python3 anchor.py --lm M --rows FILE --limit 20 -- --extra-overlay F    (smoke; everything after -- goes to both programs)

Also compares the --rowstats files byte for byte. Exit code 0 only when every comparison is identical.
"""
import argparse
import os
import subprocess
import sys
import tempfile

import common

REFERENCE = os.path.join(common.ROOT, "reference", "proto", "lm_eval.py")
HARNESS = os.path.join(common.HERE, "harness.py")


def compare_lines(ref, got):
    """[] when the two summary texts are identical, else one description per difference (the first differing column is shown)."""
    if ref == got:
        return []
    for i, (x, y) in enumerate(zip(ref, got)):
        if x != y:
            return [f"differ at column {i}: reference ...{ref[max(0, i - 20):i + 20]!r} vs extension ...{got[max(0, i - 20):i + 20]!r}"]
    return [f"one is a prefix of the other (lengths {len(ref)} and {len(got)})"]


def run(script, args):
    p = subprocess.run([sys.executable, script, *args], capture_output=True, text=True, encoding="utf-8")
    if p.returncode:
        raise RuntimeError(f"{script} exited {p.returncode}: {p.stderr[-500:]}")
    return p.stdout


def check(lm, profiles, base_args, work):
    """Runs both programs per profile; returns (list of problems, list of (profile, summary line))."""
    bad, lines = [], []
    for prof in profiles:
        rs = {}
        outs = {}
        for tag, script in (("reference", REFERENCE), ("extension", HARNESS)):
            rs[tag] = os.path.join(work, f"{tag}-{prof}.rowstats")
            outs[tag] = run(script, ["--lm", lm, "--profile", prof, *base_args, "--rowstats", rs[tag]])
        d = compare_lines(outs["reference"], outs["extension"])
        bad += [f"{prof} summary: {x}" for x in d]
        if open(rs["reference"], "rb").read() != open(rs["extension"], "rb").read():
            bad.append(f"{prof} rowstats differ")
        lines.append((prof, outs["reference"].strip()))
    return bad, lines


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    rest = []
    if "--" in argv:
        i = argv.index("--")
        argv, rest = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser()
    ap.add_argument("--lm", required=True)
    ap.add_argument("--profiles", nargs="+", default=["chat", "formal"])
    ap.add_argument("--dev", type=int)
    ap.add_argument("--rows"); ap.add_argument("--limit", type=int)
    ap.add_argument("--no-classes", action="store_true")
    ap.add_argument("--no-context", action="store_true", help="the baseline always uses --context (contract §2); this is for experiments")
    a = ap.parse_args(argv)
    args = (["--dev", str(a.dev or 302)] if not a.rows else ["--rows", a.rows] + (["--limit", str(a.limit)] if a.limit else []))
    args += [] if a.no_context else ["--context"]
    args += ["--no-classes"] if a.no_classes else []
    with tempfile.TemporaryDirectory() as work:
        bad, lines = check(a.lm, a.profiles, args + rest, work)
    commit, dirty = common.git_state()
    print(f"# anchor  commit={commit}{'+dirty' if dirty else ''}  lm={a.lm} sha256={common.sha256_file(a.lm)}  args={' '.join(args + rest)}")
    for prof, line in lines:
        print(f"{prof}: {line}")
    print("ANCHOR OK" if not bad else "ANCHOR FAIL\n" + "\n".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
