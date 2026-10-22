#!/usr/bin/env bash
# strength-colloq on the cloud VM (README of this directory, "資料與工具"): hash checks, an incremental build, 18 CLI runs (6 sets x chat
# standard / conservative / aggressive), then sc.py: chatclause --cells, select, the five judged sets with --sel, final. The maintainer's
# main session reads and runs this; agents do not. Nothing private leaves the VM: the CLI outputs (dumps, scores) stay in a temp dir and
# the three private rows files are deleted on exit, so a failed run needs them uploaded again; only the numbers-only JSONs go to $OUT.
#
#   REPO=<checkout at the commit the README names, with data/lm/ models>  OUT=<new or empty dir for the JSONs>
#   [ROWS_DIR=~/sets] [THREADS=$(nproc)] [PY="$HOME/venv/bin/python -W ignore"]  bash experiments/strength-colloq/vm.sh
#
# ROWS_DIR holds the VM's private rows files under their own names (mapping in PRIVATE below). All six rows files and the models are
# checked against their registered sha256 before any CLI run, and sc.py checks them again. Run it on one machine (the comparison arms must
# share a CPU). Budget: 3 hours; stop and ask beyond 1.5 times that.
set -euo pipefail

REPO=${REPO:?set REPO to the checkout}
OUT=${OUT:?set OUT to the output directory}
ROWS_DIR=${ROWS_DIR:-$HOME/sets}
THREADS=${THREADS:-$(nproc)}
PY=${PY:-$HOME/venv/bin/python -W ignore}      # numpy is only in the VM's venv; $PY is deliberately unquoted below (the flag is part of the command)
CLI=${CLI:-$REPO/target/release/shanjie-eval}

# The VM's names for the three private rows files; the work dir holds the public user-reported rows (its first 157 lines).
declare -A PRIVATE=([chatclause]=tune.rows.txt [discordtune]=discord-tune-rows.txt [cvtune-clean]=cvtune-clean.txt)

cd "$REPO"
mkdir -p "$OUT"
[ -z "$(ls -A "$OUT")" ] || { echo "OUT is not empty: $OUT"; exit 1; }
for n in "${!PRIVATE[@]}"; do [ -f "$ROWS_DIR/${PRIVATE[$n]}" ] || { echo "missing $ROWS_DIR/${PRIVATE[$n]}"; exit 1; }; done

W=$(mktemp -d)
scrub() {
  rm -rf "${W:?}"
  for n in "${!PRIVATE[@]}"; do rm -f "${ROWS_DIR:?}/${PRIVATE[$n]}"; done
}
trap scrub EXIT

rows_of() {
  case "$1" in
    chatclause | discordtune | cvtune-clean) echo "$ROWS_DIR/${PRIVATE[$1]}" ;;
    typing76 | dev302) echo "$REPO/eval/bench/suite-v1/$1.txt" ;;
    user-reported) echo "$W/user-reported.txt" ;;
  esac
}
head -n 157 eval/dev/user-reported.txt > "$W/user-reported.txt"

# Hash checks first, before any CLI run: every rows file against the hash registered in sc.py (the one source), then the models.
# shellcheck disable=SC2086
$PY -c 'import sys; sys.path.insert(0, "experiments/strength-colloq"); import sc; [print(h, n) for n, h in sc.ROW_SHA256.items()]' > "$W/registered.txt"
while read -r h n; do echo "$h  $(rows_of "$n")"; done < "$W/registered.txt" > "$W/rows.sha256"
[ "$(wc -l < "$W/rows.sha256")" -eq 6 ] || { echo "STOP: expected 6 registered rows hashes"; exit 1; }
sha256sum -c "$W/rows.sha256" || { echo "STOP: a rows file is not the registered one"; exit 1; }
sha256sum -c data/bigram.sjlm.sha256 data/classes.sjc.sha256 data/kn.sjkn.sha256 data/tinylm.sjtl.sha256 data/colloquial.sjlm.sha256 \
  || { echo "STOP: a model is not the registered one"; exit 1; }
[ -f eval/variants.tsv ] || { echo "missing eval/variants.tsv"; exit 1; }

# The 18 runs are from this commit's binary: incremental build, then the commit and the binary's hash in the log.
git diff --quiet || { echo "STOP: tracked files differ from HEAD"; exit 1; }
cargo build --release -q -p cli
[ -x "$CLI" ] || { echo "no CLI at $CLI"; exit 1; }
export SHANJIE_VARIANTS="$REPO/eval/variants.tsv"
echo "commit $(git rev-parse HEAD) cli $(sha256sum "$CLI" | cut -c1-12) threads $THREADS cpu $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2)"

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

# sc.py with the venv python.
sc() {
  # shellcheck disable=SC2086
  $PY experiments/strength-colloq/sc.py "$@"
}

# sc.py run on one set: --cells (chatclause) or --sel SEL.json.
sc_run() {
  local set=$1 p="$W/$1"; shift
  sc run --name "$set" --rows "$(rows_of "$set")" --sjtl data/lm/tinylm.sjtl --t2 data/lm/colloquial.sjlm \
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
sc select "$OUT/chatclause.cells.json" --out "$OUT/sel.json"
for set in discordtune typing76 cvtune-clean user-reported dev302; do
  sc_run "$set" --sel "$OUT/sel.json" --out "$OUT/$set.run.json"
done
sc final "$OUT/sel.json" "$OUT"/{discordtune,typing76,cvtune-clean,user-reported,dev302}.run.json --out "$OUT/final.json"

# Nothing but ASCII numbers and labels may be in the results (json.dumps escapes any Han character to \u....).
if grep -l '\\u' "$OUT"/*.json; then echo "STOP: a result file has escaped non-ASCII text"; exit 1; fi
echo "$(date +%T) done; results in $OUT (the temp dir and the private rows files are removed on exit)"
