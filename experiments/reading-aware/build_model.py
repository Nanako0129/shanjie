"""Build the SJLM bigram model from count_units.py's counts (contract docs/contracts/reading-aware-0.md 3.4, "用 tools/build_lm.py 建 B0u").

tools/build_lm.py is called as is (PRUNE 2, D 0.75, no fingerprint, no private entries, no KN side file, no classes, no trigram). The only
addition is the variant classes of the units: a character in a variant class (build_lm.variant_classes) merges its units the way build_lm
merges the bare word, per reading (臺U+0001R and 台U+0001R become one class, written back to every member, counted once in N and
eos_total). Variant classes need identical lexicon reading sets, so a member has the same readings as the representative.
With identity counts (no unit has U+0001) the classes are build_lm's own and the file is byte for byte what build_lm builds.

  python3 build_model.py --counts DIR --out DIR2        DIR holds counts-200000.pkl and counts-colloquial3.pkl (count_units.py's unmapped/)
Writes DIR2/bigram.sjlm and DIR2/build.json (counts, SHA-256 of the inputs and of the model).
  --expect-sha256 PREFIX   the counting anchor: exit 2 unless the model's SHA-256 starts with PREFIX (B0: f7777be3)
  --b0 B0.sjlm             vocabulary check of contract section 5: prints the ratio V / V(B0), exit 4 when it is above 1.05
"""
import argparse
import hashlib
import os
import pickle
import sys

import rcommon
import build_lm  # noqa: E402
import common  # noqa: E402  (derisk-1015/e1/common.py)
import ime  # noqa: E402
import lm as L  # noqa: E402


def unit_classes(cls, keys):
    """cls (word -> member tuple, representative first) extended with {unit -> member units} for every unit key in `keys` whose character
    is in a class."""
    full = dict(cls)
    for k in keys:
        if rcommon.U in k:
            c, r = k.split(rcommon.U, 1)
            if c in cls:
                members = tuple(m + rcommon.U + r for m in cls[c])
                for m in members:
                    full[m] = members
    return full


def count_unit_keys(counts):
    keys = set()
    for name, _ in build_lm.CORPORA:
        with open(os.path.join(counts, name), "rb") as f:
            keys |= {k for k in pickle.load(f)["uni"] if rcommon.U in k}
    return keys


def build_bytes(counts, extra_overlay=None):
    """(model bytes, V, contexts, entries, number of unit types)."""
    old, build_lm.WORK = build_lm.WORK, os.path.abspath(counts)      # build_lm reads its counts from this module global
    try:
        lex = ime.Lexicon(os.path.join(build_lm.LEXDIR, "mcbpmf-data.txt"), overlay=ime.overlays(extra_overlay))
        keys = count_unit_keys(build_lm.WORK)
        cls = unit_classes(build_lm.variant_classes(lex.by_reading), keys)
        return (*build_lm.build(cls=cls), len(keys))
    finally:
        build_lm.WORK = old


VOCAB_LIMIT = 1.05      # contract section 5: more than 5% more words than B0 means the labelling is splitting too much


def vocab_size(model_bytes):
    return len(L.read_header(model_bytes)[4])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--counts", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--expect-sha256")
    ap.add_argument("--b0")
    a = ap.parse_args(argv)
    data, V, C, E, nunits = build_bytes(a.counts)
    os.makedirs(a.out, exist_ok=True)
    model = os.path.join(a.out, "bigram.sjlm")
    with open(model, "wb") as f:
        f.write(data)
    info = common.provenance([os.path.join(a.counts, n) for n, _ in build_lm.CORPORA], {
        "V": V, "contexts": C, "entries": E, "unit_types": nunits, "prune": build_lm.PRUNE, "D": build_lm.D,
        "bigram_bytes": len(data), "bigram_sha256": hashlib.sha256(data).hexdigest()})
    common.write_json(os.path.join(a.out, "build.json"), info)
    print(f"V={V} contexts={C} entries={E} unit_types={nunits} bytes={len(data)} sha256={info['bigram_sha256']}")
    if a.expect_sha256 and not info["bigram_sha256"].startswith(a.expect_sha256):
        print(f"STOP: counting anchor: sha256 {info['bigram_sha256']} does not start with {a.expect_sha256}", file=sys.stderr)
        sys.exit(2)
    if a.b0:
        with open(a.b0, "rb") as f:
            b0 = vocab_size(f.read())
        print(f"vocabulary {V} vs B0 {b0}: ratio {V / b0:.4f}")
        if V / b0 > VOCAB_LIMIT:
            print("STOP: vocabulary more than 5% above B0 (contract section 5)", file=sys.stderr)
            sys.exit(4)


if __name__ == "__main__":
    main()
