"""E3 test-data generator (docs/contracts/derisk-1015.md section 3): rows `context|sentence|reading` with part of the syllables
reduced to their initial.

  python3 gen_abbrev.py (--set NAME | --rows FILE --name NAME) --out-dir DIR [--p 0.2 0.5 1.0] [--seed 20261015]

Writes DIR/<name>.p<p>.txt for each p (same rows as the input, only the reading column changes) and DIR/<name>.manifest.json:
generator commit (and whether experiments/derisk-1015 was dirty), seed, p values, the input file's sha256 and the output hashes.
Only rows with exactly 3 fields are kept (what lm_eval reads). The outputs are derived from public sets and stay OUTSIDE the repo
(DIR is a cache directory); the manifest is what is recorded in the README / research log.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "common"))
import abbrev  # noqa: E402
import setnames  # noqa: E402

SEED = 20261015


def generate(lines, name, p, seed=SEED, draws=None):
    """Text of the p-version of the 3-field `lines`. `draws` (draws_for) may be passed to share one draw set across several p."""
    rows = [l.split("|") for l in lines]
    draws = draws or abbrev.draws_for(seed, name, [len(r[2].split()) for r in rows])
    return "".join("|".join([c, s, " ".join(abbrev.abbreviate(r.split(), d, p))]) + "\n"
                   for (c, s, r), d in zip(rows, draws))


def git_state():
    root = setnames.ROOT
    try:
        commit = subprocess.check_output(["git", "-C", root, "rev-parse", "HEAD"], text=True).strip()
        dirty = bool(subprocess.check_output(["git", "-C", root, "status", "--porcelain", "--", "experiments/derisk-1015"], text=True).strip())
    except (OSError, subprocess.CalledProcessError):
        return "unknown", None
    return commit, dirty


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set"); ap.add_argument("--rows"); ap.add_argument("--name")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--p", type=float, nargs="+", default=[0.2, 0.5, 1.0])
    ap.add_argument("--seed", type=int, default=SEED)
    a = ap.parse_args()
    if bool(a.set) == bool(a.rows):
        ap.error("give exactly one of --set / --rows")
    path = setnames.location(a.set)[0] if a.set else a.rows
    name = a.set or a.name or ap.error("--rows needs --name")
    lines = setnames.three_field_rows(path)
    os.makedirs(a.out_dir, exist_ok=True)
    draws = abbrev.draws_for(a.seed, name, [len(l.split("|")[2].split()) for l in lines])
    commit, dirty = git_state()
    man = {"generator_commit": commit, "generator_dirty": dirty, "seed": a.seed, "set": name, "rows": len(lines),
           "input_sha256": hashlib.sha256(open(path, "rb").read()).hexdigest(), "outputs": {}}
    for p in a.p:
        text = generate(lines, name, p, a.seed, draws)
        out = os.path.join(a.out_dir, f"{name}.p{p}.txt")
        with open(out, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        man["outputs"][os.path.basename(out)] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    with open(os.path.join(a.out_dir, f"{name}.manifest.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(json.dumps(man, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
