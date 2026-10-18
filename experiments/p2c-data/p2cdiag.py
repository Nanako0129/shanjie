"""Distillation C, step 0 (experiments/p2c-data/README.md registration): zero-training diagnosis of the P2 student against 0c.
Numbers only.

  python3 experiments/p2c-data/p2cdiag.py --scores DIR --dumps DIR --train wiki.dump8 pool.dump8 [--sets discordtune chatclause] [--student S]

DIR/STUDENT.SET.jsonl (STUDENT defaults to S, the P2 student; score.py), DIR/X0C.SET.jsonl (0c, score_0c.py), DUMPS/SET.dump (the CLI dump both were scored on).
(a) agreement of the two models' own first picks (argmax of ll), on the fixable rows and on all rows with n >= 2;
(b) on rows 0c alone gets right and the student alone gets wrong, whether the character pair that separates their picks ever
    stands at the same position in two candidates of one training row (the L0 rows' dumps).
"""
import argparse
import itertools
import json


def read_dump(path, topk=8):
    rows = {}
    for line in open(path, encoding="utf-8"):
        i, rank, surf, _ = line.rstrip("\n").split("\t")
        if int(rank) <= topk:
            rows.setdefault(int(i), []).append(surf)
    return rows


def read_scores(path):
    return {r["i"]: r for r in (json.loads(l) for l in open(path, encoding="utf-8") if l.strip())}


def diff_pairs(a, b):
    """Unordered character pairs at the positions where two equal-length strings differ."""
    return [tuple(sorted((x, y))) for x, y in zip(a, b) if x != y]


def train_pairs(paths):
    seen = set()
    for p in paths:
        for cands in read_dump(p).values():
            for a, b in itertools.combinations(cands, 2):
                if len(a) == len(b):
                    seen.update(diff_pairs(a, b))
    return seen


def argmax(xs):
    return max(range(len(xs)), key=lambda j: (xs[j], -j))


def diagnose(s, t, cands, seen):
    """s, t: student and teacher records by row; cands: candidates by row; seen: training pairs."""
    agree_fix = n_fix = agree_all = n_all = 0
    first_seen = all_seen = n_tr = 0
    for i, r in s.items():
        q = t.get(i)
        if r["n"] < 2 or q is None or q.get("skip"):
            continue
        js, jt = argmax(r["ll"]), argmax(q["ll"])
        n_all += 1
        agree_all += js == jt
        if r["ok"][0] or not any(r["ok"]):
            continue
        n_fix += 1
        agree_fix += js == jt
        if r["ok"][jt] and not r["ok"][js]:
            n_tr += 1
            pairs = diff_pairs(cands[i][jt], cands[i][js])
            first_seen += bool(pairs) and pairs[0] in seen
            all_seen += bool(pairs) and all(p in seen for p in pairs)
    return dict(n_all=n_all, agree_all=agree_all, n_fix=n_fix, agree_fix=agree_fix, n_tr=n_tr, first_seen=first_seen,
                all_seen=all_seen)


def pct(a, b):
    return f"{a}/{b} ({a / max(b, 1):.1%})"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--scores", required=True)
    ap.add_argument("--dumps", required=True)
    ap.add_argument("--train", nargs="+", required=True)
    ap.add_argument("--sets", nargs="+", default=["discordtune", "chatclause"])
    ap.add_argument("--student", default="S", help="file prefix of the judged student's score files (S = P2, T / R = position-level arms)")
    a = ap.parse_args(argv)
    seen = train_pairs(a.train)
    print(f"training character pairs (same position, two candidates of one row): {len(seen)}")
    for name in a.sets:
        d = diagnose(read_scores(f"{a.scores}/{a.student}.{name}.jsonl"), read_scores(f"{a.scores}/X0C.{name}.jsonl"),
                     read_dump(f"{a.dumps}/{name}.dump"), seen)
        print(f"{a.student} {name}: student and 0c pick the same candidate on fixable rows {pct(d['agree_fix'], d['n_fix'])}, "
              f"on all rows with n >= 2 {pct(d['agree_all'], d['n_all'])}; 0c right and student wrong on {d['n_tr']} fixable rows: "
              f"first separating pair seen in training {pct(d['first_seen'], d['n_tr'])}, every separating pair seen "
              f"{pct(d['all_seen'], d['n_tr'])}")


if __name__ == "__main__":
    main()
