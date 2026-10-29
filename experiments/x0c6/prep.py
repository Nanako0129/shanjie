"""X0C-6 plumbing between the shipping CLI and score_0c.py, numbers out only (README).

  prep.py build --out DIR --set NAME ROWS DUMP NGSTATS [--set ...]   several sets -> one rows / dump / v3 file, so 0c scores them in one wave
  prep.py split --dir DIR --scores ALL.jsonl --out OUT               score_0c.py's output -> OUT/X0C.<set>.jsonl, rows numbered 1..n again
  prep.py check FILE...                                              score files hold numbers only (public sets: they may leave the VM)
  prep.py digits FILE...                                             only [0-9 tab newline] (what leaves the VM for the private sets)

NGSTATS is the rowstats of the same rows run without --tinylm (the n-gram first); `build` stops when its ok column differs from the
dump's rank 1, which is what the readout calls "n-gram first".
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path[:0] = [os.path.join(ROOT, "experiments", "s7a-tinylm")]
import score  # noqa: E402  (puts reference/proto and tools on sys.path; lenient and levenshtein come with it)

TOPK = 8
KEYS = {"i", "n", "ng", "ok", "ed", "glen", "skip", "ll", "ll0", "llr"}   # score_0c.py's record; every value a number or a list of numbers


def records(rows, cands):
    """The fields score_0c.py copies from a tinylm score file, from the rows and the dump alone (same definitions as score.build_records)."""
    for i, ((gold, _syl, _prefix), cs) in enumerate(zip(rows, cands), 1):
        g = score.lenient(gold)
        ok = [int(score.lenient(s) == g) for s, _ in cs]
        yield {"i": i, "n": len(cs), "ng": [sc for _, sc in cs], "ok": ok,
               "ed": [0 if o else score.levenshtein(s, gold) for (s, _), o in zip(cs, ok)], "glen": len(gold)}


def three_fields(path):
    """The lines the CLI and score_0c.read_rows keep: `prefix|sentence|reading`."""
    return [l.rstrip("\n") for l in open(path, encoding="utf-8", newline="\n") if len(l.rstrip("\n").split("|")) == 3]


def read_ngstats(path):
    return [int(l.split("\t")[1]) for l in open(path, encoding="utf-8") if l.strip()]


def build(out, sets):
    os.makedirs(out, exist_ok=True)
    order, rows_out, dump_out, v3_out, off = [], [], [], [], 0
    for name, rows_f, dump_f, ng_f in sets:
        lines = three_fields(rows_f)
        rows = score.load_rows(rows_f)
        if len(rows) != len(lines):
            sys.exit(f"prep: STOP, {name}: score.load_rows read {len(rows)} rows, the 3-field filter {len(lines)}")
        cands = score.load_dump(open(dump_f, encoding="utf-8"), len(rows))
        recs = list(records(rows, cands))
        ng = read_ngstats(ng_f)
        if ng != [r["ok"][0] for r in recs]:
            bad = sum(a != r["ok"][0] for a, r in zip(ng, recs)) + abs(len(ng) - len(recs))
            sys.exit(f"prep: STOP, {name}: the dump's rank 1 and the n-gram-only run disagree on {bad} rows")
        for i, cs in enumerate(cands, 1):
            for rank, (surf, sc) in enumerate(cs, 1):
                dump_out.append(f"{off + i}\t{rank}\t{surf}\t{sc!r}\n")
        for r in recs:
            r["i"] += off
            v3_out.append(json.dumps(r) + "\n")
        rows_out += [l + "\n" for l in lines]
        order.append([name, len(rows)])
        off += len(rows)
        print(f"prep: {name}: {len(rows)} rows, n-gram first right {sum(ng)}")
    for fn, body in (("all.rows.txt", rows_out), ("all.dump", dump_out), ("all.v3.jsonl", v3_out)):
        with open(os.path.join(out, fn), "w", encoding="utf-8", newline="\n") as f:
            f.writelines(body)
    with open(os.path.join(out, "sets.json"), "w") as f:
        json.dump(order, f)


def split(d, scores, out):
    order = json.load(open(os.path.join(d, "sets.json")))
    recs = [json.loads(l) for l in open(scores, encoding="utf-8") if l.strip()]
    if len(recs) != sum(n for _, n in order) or [r["i"] for r in recs] != list(range(1, len(recs) + 1)):
        sys.exit("prep: STOP, the score file does not cover rows 1..N of the combined input")
    os.makedirs(out, exist_ok=True)
    off = 0
    for name, n in order:
        with open(os.path.join(out, f"X0C.{name}.jsonl"), "w", encoding="utf-8", newline="\n") as f:
            for r in recs[off:off + n]:
                f.write(json.dumps({**r, "i": r["i"] - off}) + "\n")
        off += n


def numbers_only(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) or (isinstance(x, list) and all(numbers_only(v) for v in x))


def check(files):
    for p in files:
        for k, l in enumerate(open(p, encoding="utf-8"), 1):
            r = json.loads(l)
            if not set(r) <= KEYS or not all(numbers_only(v) for v in r.values()):
                sys.exit(f"prep: STOP, {p} line {k} holds more than numbers under the known keys")


def check_digits(files):
    """The files that leave the VM for the private sets: only digits, tabs and newlines."""
    for p in files:
        if not re.fullmatch(rb"[0-9\t\n]*", open(p, "rb").read()):
            sys.exit(f"prep: STOP, {p} holds more than digits, tabs and newlines")


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("--out", required=True)
    b.add_argument("--set", nargs=4, action="append", metavar=("NAME", "ROWS", "DUMP", "NGSTATS"), required=True)
    s = sub.add_parser("split"); s.add_argument("--dir", required=True); s.add_argument("--scores", required=True); s.add_argument("--out", required=True)
    c = sub.add_parser("check"); c.add_argument("files", nargs="+")
    g = sub.add_parser("digits"); g.add_argument("files", nargs="+")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        build(a.out, a.set)
    elif a.cmd == "split":
        split(a.dir, a.scores, a.out)
    elif a.cmd == "digits":
        check_digits(a.files)
    else:
        check(a.files)


if __name__ == "__main__":
    main()
