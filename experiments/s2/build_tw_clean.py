"""Builds the tw-clean table of docs/contracts/tw-conv.md section 11: `convert() output <TAB> corrected sentence`, one row per sentence, an empty second
column = the sentence is not counted. Inputs: the gap file (JSON {"gap1": [[a, j, current, ...], ...], "gap2": [...]}, a = article index), the
proofreading result (JSON {"accepted": {id: corrected sentence}, "review_issues": {id: [...]}}; ids g1-<index into gap1>, g2-<index into gap2>) and the
SHA-256 of tw-conv.tsv and mcbpmf-data.txt of the gap measurement. Refuses when this checkout's two files differ from those hashes. Never overwrites --out.

  python3 experiments/s2/build_tw_clean.py --gaps GAPS.json --final FINAL.json --batches BATCH_DIR --gap-tw-conv-sha HEX --gap-lexicon-sha HEX --out TABLE.tsv

BATCH_DIR holds the bNNNN.tsv files sent for proofreading (id<TAB>sentence); a sent id with no accepted or review_issues entry came back
unchanged, which counts as a result for the conflict check. Refuses a BATCH_DIR that is not a folder or holds no .tsv file.

Prints the rows, the empty rows, the conflicts and the expected hit counts (distinct kept keys that occur in a gap row with a < 2000 and a < 200000),
all computed from the inputs, not from the written file."""
import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TW_CONV = os.path.join(HERE, "tw-conv.tsv")
LEXICON = os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt")
FIRST_ARTICLES = (2000, 200_000)


def sha256(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def build(gaps, final, sent=None):
    """-> (rows {key: value} without the conflicts, number of conflicts, expected hits for FIRST_ARTICLES). The value is "" for a review_issues id (it
    wins over an accepted value of the same id). `sent` = every id that was proofread (None: only the accepted and review_issues ids); a sent id
    in neither came back unchanged, which is a result too (contract 11.1: a key with two different results is dropped, e.g. 后髮座 changed in one
    batch and kept in another). Rows whose value equals the key are not written."""
    rows_of = {"g1": gaps["gap1"], "g2": gaps["gap2"], "g3": gaps.get("gap3", [])}   # gap3: Traditional sentences with an S character (contract 11, 2026-11-07)
    values = {}
    for i in sorted(set(final["accepted"]) | set(final["review_issues"]) | set(sent or ())):
        g, _, n = i.partition("-")
        key = rows_of[g][int(n)][2]
        v = "" if i in final["review_issues"] else final["accepted"].get(i, key)
        values.setdefault(key, set()).add(v)
    conflicts = {k for k, v in values.items() if len(v) > 1}
    rows = {k: next(iter(v)) for k, v in values.items() if k not in conflicts and next(iter(v)) != k}
    for k, v in rows.items():
        if "\t" in k or "\n" in k or "\t" in v or "\n" in v:
            sys.exit(f"a key or value with a tab or newline would break the table: {k!r}")
    first = {}   # the smallest article index of any gap row with this sentence: the table is looked up by the string, whichever row it was proofread from
    for r in gaps["gap1"] + gaps["gap2"] + gaps.get("gap3", []):
        if r[2] in rows and r[0] < first.get(r[2], float("inf")):
            first[r[2]] = r[0]
    return rows, len(conflicts), tuple(sum(a < n for a in first.values()) for n in FIRST_ARTICLES)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gaps", required=True)
    ap.add_argument("--final", required=True)
    ap.add_argument("--gap-tw-conv-sha", required=True)
    ap.add_argument("--gap-lexicon-sha", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--batches", required=True, help="folder of the proofreading batch files (id<TAB>sentence): their ids are the sentences that were proofread")
    a = ap.parse_args(argv)
    if (sha256(TW_CONV), sha256(LEXICON)) != (a.gap_tw_conv_sha.lower(), a.gap_lexicon_sha.lower()):
        sys.exit("轉換表或詞庫改過：要重跑缺口量測與校對")
    names = sorted(f for f in os.listdir(a.batches) if f.endswith(".tsv")) if os.path.isdir(a.batches) else []
    if not names:   # an empty --batches would silently drop the unchanged sentences from the conflict check
        sys.exit(f"--batches {a.batches!r}: not a folder with the proofreading batch files (*.tsv)")
    sent = [l.split("\t", 1)[0] for f in names for l in open(os.path.join(a.batches, f), encoding="utf-8")]
    rows, conflicts, expected = build(json.load(open(a.gaps, encoding="utf-8")), json.load(open(a.final, encoding="utf-8")), sent)
    with open(a.out, "x", encoding="utf-8", newline="\n") as f:   # "x": never overwrite
        f.write(f"# tw-conv.tsv {a.gap_tw_conv_sha.lower()}\n# mcbpmf-data.txt {a.gap_lexicon_sha.lower()}\n# entries {len(rows)}\n")
        for k in sorted(rows):
            f.write(f"{k}\t{rows[k]}\n")
    print(f"rows {len(rows)}, empty rows {sum(not v for v in rows.values())}, conflicts {conflicts}")
    print(" ".join(f"expected hits (a < {n}): {e}" for n, e in zip(FIRST_ARTICLES, expected)))


if __name__ == "__main__":
    main()
