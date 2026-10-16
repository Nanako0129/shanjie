"""E4 (a) decoder run: reference/proto/lm_eval.py --context with or without the mid-sentence-punctuation rule
(docs/contracts/derisk-1015.md section 3, E4).

  python3 e4_eval.py --punct reset|mid <lm_eval options; --context is required>

--punct reset  the control arm and the null configuration: lm.context_key untouched; the summary line equals lm_eval.py's (anchor.py).
--punct mid    punct_ctx.context_key_mid (see its docstring for the exact punctuation sets); "+punct" is appended to the profile field.
Both arms use the same model; pass the shipped one (--lm data/lm/bigram.sjlm, kn.sjkn / classes.sjc next to it).
After the summary line a `#E4` line says how many rows have a context whose key differs between the two rules (the rows this
change can touch at all).
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "common"))
import patched_eval  # noqa: E402
import punct_ctx  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("--punct", choices=["reset", "mid"], required=True)
    a, rest = ap.parse_known_args(argv)
    if "--context" not in rest:   # without it lm_eval never reads the left context and both arms would silently be identical
        ap.error("--context is required")
    rows = patched_eval.run_lm_eval(rest, context_key=punct_ctx.context_key_mid if a.punct == "mid" else None,
                                    tag="+punct" if a.punct == "mid" else "")
    changed = sum(punct_ctx.context_key_mid(c) != punct_ctx.BASE_KEY(c) for _, _, c in rows)
    print(f"#E4 punct={a.punct} n={len(rows)} rows_with_changed_key={changed}")


if __name__ == "__main__":
    main()
