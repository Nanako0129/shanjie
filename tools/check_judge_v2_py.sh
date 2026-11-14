#!/bin/sh
# The Python reference under judge v2 on dev302 (docs/spec/evaluation.md §3.8): its rowstats, with and without the test-only accept file, are
# the committed goldens byte for byte (the Rust CLI is compared with the same goldens in cli/tests/judge_v2.rs), and an accept file whose row
# hash does not match stops the run before any output file exists. Run by `make test-python` and by CI. PYTHON may name the interpreter.
set -eu
PY=${PYTHON:-python3}
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
for p in chat formal; do
  "$PY" reference/proto/lm_eval.py --profile "$p" --dev 302 --judge v2 --rowstats "$T/$p-jv2.rowstats" > /dev/null
  cmp "$T/$p-jv2.rowstats" "eval/golden/s2-lm-dev302-$p-jv2.rowstats"
  "$PY" reference/proto/lm_eval.py --profile "$p" --dev 302 --judge v2 --accept eval/golden/judge-v2-accept-test.tsv --rowstats "$T/$p-jv2a.rowstats" > /dev/null
  cmp "$T/$p-jv2a.rowstats" "eval/golden/s2-lm-dev302-$p-jv2-accept.rowstats"
done
# a row hash that does not match: non-zero exit, no output file
sed 's/\t[0-9a-f]\{64\}\t/\t0000000000000000000000000000000000000000000000000000000000000000\t/' eval/golden/judge-v2-accept-test.tsv > "$T/bad.tsv"
if "$PY" reference/proto/lm_eval.py --profile chat --dev 302 --judge v2 --accept "$T/bad.tsv" --rowstats "$T/bad.rowstats" > /dev/null 2> "$T/bad.err"; then
  echo "FAIL: an accept file with a wrong row hash was accepted"; exit 1; fi
[ ! -e "$T/bad.rowstats" ] || { echo "FAIL: an output file exists after a refused accept file"; exit 1; }
echo "judge v2 (Python reference): dev302 goldens match, a wrong row hash is refused before any output"
