#!/usr/bin/env bash
# colloq-levels parity (docs/contracts/colloquial-levels.md section 2.2): per set, three CLI runs of this checkout's binary (chat standard with the
# dump, conservative and aggressive with scores and rowstats; all with --colloquial-core, all tags must end in +rc), then expect_core.py, which
# recomputes both levels offline (sc.py's cell_pick) and compares every row's pick and right/wrong. Prints counts only. The maintainer's main
# session runs this on the cloud VM for the private sets; the public sets run on the Mac the same way. Agents do not run it on the VM.
#
#   REPO=<checkout at the commit under test, with data/lm/ models, target/release/shanjie-eval built or buildable>  OUT=<new or empty dir>
#   [SETS="chatclause discordtune cvtune-clean typing76 user-reported dev302"] [ROWS_DIR=~/sets] [DELETE_ROWS=1] [THREADS=n] [PY="$HOME/venv/bin/python -W ignore"]
#   bash experiments/colloq-levels/parity.sh
#
# ROWS_DIR holds the private rows files under their own names (mapping in PRIVATE). They are deleted on exit (a failed run needs them uploaded
# again), only for the sets in SETS, and ONLY with DELETE_ROWS=1, which the VM wrapper sets for its uploaded copies; without it nothing in ROWS_DIR
# is ever removed (so a run on the Mac never deletes originals), and DELETE_ROWS=1 is refused when ROWS_DIR is inside ~/side-project.
# Every rows file and model is checked against its registered sha256 before any CLI run; expect_core.py checks them again. One machine for all three levels of a set (the arms must share a CPU).
set -euo pipefail

declare -A PRIVATE=([chatclause]=tune.rows.txt [discordtune]=discord-tune-rows.txt [cvtune-clean]=cvtune-clean.txt)
W=""
ROWS_DIR=${ROWS_DIR:-$HOME/sets}
SETS=${SETS:-"chatclause discordtune cvtune-clean typing76 user-reported dev302"}
# The trap comes first, so that every later exit (a missing variable, a non-empty OUT, a failed hash check) also removes the temp dir and,
# with DELETE_ROWS=1, the uploaded private rows files.
scrub() {
  if [ -n "$W" ]; then rm -rf "${W:?}"; fi
  if [ "${DELETE_ROWS:-}" = 1 ]; then
    for n in $SETS; do if [ -n "${PRIVATE[$n]:-}" ]; then rm -f "${ROWS_DIR:?}/${PRIVATE[$n]}"; fi; done
  fi
}
trap scrub EXIT
if [ "${DELETE_ROWS:-}" = 1 ]; then
  case "$(cd "$ROWS_DIR" 2>/dev/null && pwd -P)/" in
    "$HOME"/side-project/*) echo "STOP: DELETE_ROWS=1 is refused for a ROWS_DIR inside ~/side-project"; exit 1 ;;
  esac
fi

REPO=${REPO:?set REPO to the checkout}
OUT=${OUT:?set OUT to the output directory}
THREADS=${THREADS:-$(nproc 2>/dev/null || sysctl -n hw.ncpu)}
PY=${PY:-python3 -W ignore}      # numpy is needed (the VM's venv); $PY is deliberately unquoted below (the flag is part of the command)
CLI=${CLI:-$REPO/target/release/shanjie-eval}
sha() { if command -v sha256sum >/dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }

cd "$REPO"
mkdir -p "$OUT"
[ -z "$(ls -A "$OUT")" ] || { echo "OUT is not empty: $OUT"; exit 1; }
for n in $SETS; do
  if [ -n "${PRIVATE[$n]:-}" ]; then [ -f "$ROWS_DIR/${PRIVATE[$n]}" ] || { echo "missing $ROWS_DIR/${PRIVATE[$n]}"; exit 1; }; fi
done

W=$(mktemp -d)

rows_of() {
  case "$1" in
    chatclause | discordtune | cvtune-clean) echo "$ROWS_DIR/${PRIVATE[$1]}" ;;
    typing76 | dev302) echo "$REPO/eval/bench/suite-v1/$1.txt" ;;
    user-reported) echo "$W/user-reported.txt" ;;
  esac
}
head -n 157 eval/dev/user-reported.txt > "$W/user-reported.txt"

# Hash checks before any CLI run: the rows files against the hashes registered in expect_core.py, then the models.
# shellcheck disable=SC2086
$PY -c 'import sys; sys.path.insert(0, "experiments/colloq-levels"); import expect_core as e; [print(h, n) for n, h in e.ROW_SHA256.items()]' > "$W/registered.txt"
while read -r h n; do case " $SETS " in *" $n "*) echo "$h  $(rows_of "$n")" ;; esac; done < "$W/registered.txt" > "$W/rows.sha256"
[ "$(wc -l < "$W/rows.sha256")" -eq "$(echo $SETS | wc -w)" ] || { echo "STOP: a set has no registered hash"; exit 1; }
if command -v sha256sum >/dev/null; then sha256sum -c "$W/rows.sha256"; else shasum -a 256 -c "$W/rows.sha256"; fi || { echo "STOP: a rows file is not the registered one"; exit 1; }
for f in bigram.sjlm classes.sjc kn.sjkn tinylm.sjtl colloquial.sjlm; do
  want=$(cut -d' ' -f1 "data/$f.sha256"); got=$(sha "data/lm/$f" | cut -d' ' -f1)
  [ "$want" = "$got" ] || { echo "STOP: data/lm/$f is not the registered one"; exit 1; }
done
[ -f eval/variants.tsv ] || { echo "missing eval/variants.tsv"; exit 1; }

git diff --quiet HEAD || { echo "STOP: tracked files differ from HEAD"; exit 1; }
cargo build --release -q -p cli
[ -x "$CLI" ] || { echo "no CLI at $CLI"; exit 1; }
export SHANJIE_VARIANTS="$REPO/eval/variants.tsv"
echo "commit $(git rev-parse HEAD) cli $(sha "$CLI" | cut -c1-12) threads $THREADS"
sha data/lm/tinylm.sjtl data/lm/colloquial.sjlm data/lm/bigram.sjlm data/lm/classes.sjc data/lm/kn.sjkn

cli() {
  local set=$1 lv=$2 p="$W/$1.$2" extra=()
  if [ "$lv" = standard ]; then extra=(--dump "$p.dump" --dump-words "$p.words"); fi
  echo "$(date +%T) cli $set $lv"
  "$CLI" --lm data/lm/bigram.sjlm --profile chat --context --packs acg --tinylm data/lm/tinylm.sjtl --colloquial-core data/lm/colloquial.sjlm \
    --rerank-level "$lv" --rows "$(rows_of "$set")" --name "$set" --rowstats "$p.rs" --tinylm-scores "$p.sc" --threads "$THREADS" \
    ${extra[@]+"${extra[@]}"} > "$p.sum" 2>> "$W/cli.err" || { echo "CLI failed: $set $lv"; tail -n 3 "$W/cli.err"; exit 1; }
}

fail=0
for set in $SETS; do
  for lv in standard conservative aggressive; do cli "$set" "$lv"; done
  p="$W/$set"
  # shellcheck disable=SC2086
  $PY experiments/colloq-levels/expect_core.py --name "$set" --rows "$(rows_of "$set")" --sjtl data/lm/tinylm.sjtl --t2 data/lm/colloquial.sjlm \
    --dump "$p.standard.dump" --dump-words "$p.standard.words" --rowstats "$p.standard.rs" --tinylm-scores "$p.standard.sc" --summary "$p.standard.sum" \
    --cons-rowstats "$p.conservative.rs" --cons-scores "$p.conservative.sc" --cons-summary "$p.conservative.sum" \
    --aggr-rowstats "$p.aggressive.rs" --aggr-scores "$p.aggressive.sc" --aggr-summary "$p.aggressive.sum" \
    --out "$OUT/$set.parity.json" | tee -a "$OUT/parity.log" || fail=1
done
# Nothing but ASCII numbers and labels may be in the results.
if grep -l '\\u' "$OUT"/*.json; then echo "STOP: a result file has escaped non-ASCII text"; exit 1; fi
[ "$fail" -eq 0 ] || { echo "STOP: some set differs"; exit 1; }
echo "$(date +%T) done: 0 differing rows in every set; results in $OUT"
