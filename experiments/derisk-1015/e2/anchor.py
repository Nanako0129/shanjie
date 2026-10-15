"""E2 anchor (contract section 3 E2 and section 5): the extension, in its control configuration (the weights tinylm-v5 was exported from,
reading input off), must give the same LL as experiments/s7a-tinylm/score.py on the same candidates: max |dLL| < 1e-4 by parity.py
(columns ll, ll0, ll16). A mismatch is a STOP (exit code 1).

  python anchor.py --npz tinylm-M.npz --backend torch --dump cvtune.chat.dump --rows cvtune.txt --name cvtune --expect-top1 N \
         --out DIR [--reference DIR2/M.chat.cvtune.jsonl]

--reference is an existing score.py output for the same dump (for instance the S7a scores of the machine that trained M); without it
score.py is run here, with the same backend, to DIR/anchor.reference.jsonl. Both files and the verdict stay in DIR.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import score_r  # noqa: E402  (puts experiments/s7a-tinylm on sys.path)
import parity  # noqa: E402
import score  # noqa: E402


def check(reference_rows, control_rows):
    """(passed, {column: (max |dLL|, candidates compared)}). Raises ValueError when the files do not hold the same candidates."""
    d = parity.max_delta(reference_rows, control_rows)
    return d["ll"][1] > 0 and max(m for m, _ in d.values()) < parity.LIMIT, d


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--npz", required=True); ap.add_argument("--backend", choices=["numpy", "torch"], default="numpy")
    ap.add_argument("--dump", required=True); ap.add_argument("--name", required=True)
    ap.add_argument("--rows"); ap.add_argument("--dev", type=int); ap.add_argument("--limit", type=int)
    ap.add_argument("--expect-top1", type=int, required=True)
    ap.add_argument("--out", required=True); ap.add_argument("--reference"); ap.add_argument("--bs", type=int, default=64)
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    ctl = os.path.join(a.out, "anchor.control.jsonl")
    score_r.main(["one", "--arm", "control", "--npz", a.npz, "--backend", a.backend, "--dump", a.dump, "--name", a.name,
                  "--expect-top1", str(a.expect_top1), "--out", ctl, "--bs", str(a.bs)]
                 + (["--rows", a.rows] if a.rows else []) + (["--dev", str(a.dev)] if a.dev else []) + (["--limit", str(a.limit)] if a.limit else []))
    ref = a.reference
    if not ref:
        ref = os.path.join(a.out, "anchor.reference.jsonl")
        score.main(["--npz", a.npz, "--backend", a.backend, "--dump", a.dump, "--name", a.name, "--expect-top1", str(a.expect_top1),
                    "--out", ref, "--bs", str(a.bs)]
                   + (["--rows", a.rows] if a.rows else []) + (["--dev", str(a.dev)] if a.dev else []) + (["--limit", str(a.limit)] if a.limit else []))
    ok, d = check(parity.load(ref), parity.load(ctl))
    for c, (m, n) in d.items():
        print(f"{c}: max |dLL| {m:.3e} over {n} candidates")
    if not ok:
        sys.exit(f"anchor: STOP, max |dLL| >= {parity.LIMIT} (or nothing compared)")
    print("anchor: OK")


if __name__ == "__main__":
    main()
