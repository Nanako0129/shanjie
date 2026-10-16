"""E3 decoder run: reference/proto/lm_eval.py with abbreviated input expanded (docs/contracts/derisk-1015.md section 3, E3).

  python3 e3_eval.py --abbrev on|off <lm_eval options: --lm --profile --rows --context --rowstats --name ...>

--abbrev off  the null configuration: lm.decode untouched. Its summary line must equal lm_eval.py's (anchor.py), section 2.
--abbrev on   initial-only tokens expand to every lexicon syllable with that initial (abbrev.py); the summary line's profile field
              gets "+abbr". Full-syllable tokens match only themselves, except that a bare ㄓㄔㄕㄖㄗㄘㄙ also matches the whole
              initial group (it cannot be told from an abbreviation).
Both modes print, after the summary line, a `#E3` line: n, top1 (whole row, lm_eval's lenient comparison), top9 (the gold sentence
among the first 9 distinct candidate surfaces), abbreviated tokens, mean lexicon syllables behind an abbreviated token,
lexicon entries scored per row (on only), and the decode time (Python, for relative comparison only).
"""
import argparse
import collections
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "common"))
import abbrev  # noqa: E402
import patched_eval  # noqa: E402

TOP9 = 9


def main(argv=None):
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("--abbrev", choices=["on", "off"], required=True)
    a, rest = ap.parse_known_args(argv)
    stats = collections.Counter()
    nbs, clock = [], [0.0]
    inner = abbrev.make_decode(stats) if a.abbrev == "on" else patched_eval.L.decode

    def decode(*args, **kw):
        t0 = time.perf_counter()
        nb = inner(*args, **kw)
        clock[0] += time.perf_counter() - t0
        nbs.append(["".join(ws) for _, ws in nb])
        return nb

    rows = patched_eval.run_lm_eval(rest, decode=decode, tag="+abbr" if a.abbrev == "on" else "")
    lenient = patched_eval.lm_eval.lenient
    top1 = sum(lenient(s[0]) == lenient(r[0]) for s, r in zip(nbs, rows))
    top9 = sum(lenient(r[0]) in {lenient(x) for x in s[:TOP9]} for s, r in zip(nbs, rows))
    ab = stats["abbr_tokens"]
    per = f"{stats['readings_sum'] / ab:.2f}" if ab else "n/a"
    print(f"#E3 abbrev={a.abbrev} n={len(rows)} top1={top1} top9={top9} abbr_tokens={ab} readings_per_abbr={per} "
          f"entries_per_row={stats['entries_tried'] / len(rows):.1f} decode_s={clock[0]:.2f} ms_per_row={1000 * clock[0] / len(rows):.1f}")


if __name__ == "__main__":
    main()
