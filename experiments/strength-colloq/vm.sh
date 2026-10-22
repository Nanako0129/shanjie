#!/usr/bin/env bash
# strength-colloq on the cloud VM (README of this directory, "資料與工具"): 18 CLI runs (6 sets x chat standard / conservative / aggressive),
# then sc.py: chatclause --cells, select, the five judged sets with --sel, final. The maintainer's main session reads and runs this;
# agents do not. Nothing private leaves the VM: the CLI outputs (dumps, scores) stay in a temp dir that is removed on exit, and only the
# numbers-only JSONs go to $OUT.
#
#   REPO=<checkout at the commit the README names, with data/lm/ models and a release build of cli>  ROWS_DIR=<dir with chatclause.txt,
#   discordtune.txt, cvtune-clean.txt>  OUT=<new or empty dir for the JSONs>  [THREADS=16]  bash experiments/strength-colloq/vm.sh
#
# The three private row files are checked against their registered sha256 by sc.py (a wrong file is a STOP). The public sets come from the
# checkout. Run it on one machine (the comparison arms must share a CPU). Budget: 3 hours; stop and ask beyond 1.5 times that.
set -euo pipefail

REPO=${REPO:?set REPO to the checkout}
ROWS_DIR=${ROWS_DIR:?set ROWS_DIR to the directory of the private row files}
OUT=${OUT:?set OUT to the output directory}
THREADS=${THREADS:-16}
CLI=${CLI:-$REPO/target/release/shanjie-eval}

cd "$REPO"
[ -x "$CLI" ] || { echo "no CLI at $CLI (cargo build --release -p cli)"; exit 1; }
mkdir -p "$OUT"
[ -z "$(ls -A "$OUT")" ] || { echo "OUT is not empty: $OUT"; exit 1; }
for n in chatclause discordtune cvtune-clean; do [ -f "$ROWS_DIR/$n.txt" ] || { echo "missing $ROWS_DIR/$n.txt"; exit 1; }; done
for f in data/lm/bigram.sjlm data/lm/tinylm.sjtl data/lm/colloquial.sjlm eval/variants.tsv; do [ -f "$f" ] || { echo "missing $f"; exit 1; }; done

W=$(mktemp -d)
trap 'rm -rf "${W:?}"' EXIT
export SHANJIE_VARIANTS="$REPO/eval/variants.tsv"
echo "commit $(git rev-parse HEAD) cli $(sha256sum "$CLI" | cut -c1-12) cpu $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2)"

rows_of() {
  case "$1" in
    chatclause | discordtune | cvtune-clean) echo "$ROWS_DIR/$1.txt" ;;
    typing76 | dev302) echo "$REPO/eval/bench/suite-v1/$1.txt" ;;
    user-reported) echo "$REPO/eval/dev/user-reported.txt" ;;
  esac
}

# One CLI run of the chat setting: the shipping flags, at the given --rerank-level. Only the standard run needs the dump and the word
# split (the colloquial and reading-channel terms read them); the two other levels need rowstats, scores and the summary line.
cli() {
  local set=$1 lv=$2 p="$W/$1.$2" extra=()
  if [ "$lv" = standard ]; then extra=(--dump "$p.dump" --dump-words "$p.words"); fi
  echo "$(date +%T) cli $set $lv"
  "$CLI" --lm data/lm/bigram.sjlm --profile chat --context --packs acg --tinylm data/lm/tinylm.sjtl --colloquial-core data/lm/colloquial.sjlm \
    --rerank-level "$lv" --rows "$(rows_of "$set")" --name "$set" --rowstats "$p.rs" --tinylm-scores "$p.sc" --threads "$THREADS" \
    ${extra[@]+"${extra[@]}"} > "$p.sum" 2>> "$W/cli.err" || { echo "CLI failed: $set $lv"; tail -n 3 "$W/cli.err"; exit 1; }
}

# sc.py run on one set: --cells (chatclause) or --sel SEL.json.
sc_run() {
  local set=$1 p="$W/$1"; shift
  python3 experiments/strength-colloq/sc.py run --name "$set" --rows "$(rows_of "$set")" --sjtl data/lm/tinylm.sjtl --t2 data/lm/colloquial.sjlm \
    --dump "$p.standard.dump" --dump-words "$p.standard.words" --rowstats "$p.standard.rs" --tinylm-scores "$p.standard.sc" --summary "$p.standard.sum" \
    --cons-rowstats "$p.conservative.rs" --cons-scores "$p.conservative.sc" --cons-summary "$p.conservative.sum" \
    --aggr-rowstats "$p.aggressive.rs" --aggr-scores "$p.aggressive.sc" --aggr-summary "$p.aggressive.sum" "$@"
}

SETS="chatclause discordtune cvtune-clean typing76 user-reported dev302"
for set in $SETS; do
  for lv in standard conservative aggressive; do cli "$set" "$lv"; done
done
echo "$(date +%T) 18 CLI runs done"

# Selection on chatclause only, then the judged sets with the selected cells, then the four clauses and the gates.
sc_run chatclause --cells --out "$OUT/chatclause.cells.json"
python3 experiments/strength-colloq/sc.py select "$OUT/chatclause.cells.json" --out "$OUT/sel.json"
for set in discordtune typing76 cvtune-clean user-reported dev302; do
  sc_run "$set" --sel "$OUT/sel.json" --out "$OUT/$set.run.json"
done
python3 experiments/strength-colloq/sc.py final "$OUT/sel.json" "$OUT"/{discordtune,typing76,cvtune-clean,user-reported,dev302}.run.json --out "$OUT/final.json"

# Nothing but ASCII numbers and labels may be in the results (json.dumps escapes any Han character to \u....).
if grep -l '\\u' "$OUT"/*.json; then echo "STOP: a result file has escaped non-ASCII text"; exit 1; fi
echo "$(date +%T) done; results in $OUT (the temp dir with the dumps is removed on exit)"
