"""g0v #rand0m messages -> cleaned sentences for the SJ-LM-7 colloquial counts (docs/contracts/sj-lm-7.md section 2.4; weight x5, unit = one message).
Same rules as ptt_clean.py (p2clean's split and filters, UNIT_CAP sentences per unit, dedupe on p2clean's key).
Usage: python3 experiments/sj-lm-7/g0v_clean.py MESSAGES OUT_TSV   (MESSAGES: UTF-8, one message per line; writes unit<TAB>sentence, unit = the
1-based line number; prints counts only). The test is test_g0v_clean.py."""
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ptt_clean  # noqa: E402


def clean(P, lines):
    """[(unit, sentence)], deduplicated in line order, and the rejection counts."""
    why, seen, rows = collections.Counter(), set(), []
    for i, line in enumerate(lines, 1):
        for s in ptt_clean.unit_sentences(P, [line.rstrip("\n")], why):
            k = P.dedupe_key(s)
            if k in seen:
                why["duplicate"] += 1
            else:
                seen.add(k)
                rows.append((str(i), s))
    return rows, why


def main(src, out):
    P = ptt_clean.load_p2clean()
    with open(src, encoding="utf-8") as fh:
        lines = fh.readlines()
    rows, why = clean(P, lines)
    with open(out, "w", encoding="utf-8") as fh:
        fh.writelines(f"{u}\t{s}\n" for u, s in rows)
    print(json.dumps({"units": len(lines), "sentences": len(rows), "han": sum(P.han_count(s) for _, s in rows),
                      "dropped_by": dict(why.most_common())}, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
