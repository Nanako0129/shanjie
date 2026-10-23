#!/usr/bin/env bash
# IT+AI pack: build the pack and run the public checks of docs/contracts/it-pack.md section 3 (3.3, 3.4 public sets, 3.5), on the GCP VM (Linux).
# No private data is read. Prints numbers and the public rows that differ; everything is written under $OUT (copy it back).
#
#   REPO=<checkout of feat/it-pack>  OUT=<new or empty dir>  SOURCES=<the sources/it cache dir>  [BASE_REF=67651c73]
#   [THREADS=n]  [MIN_COUNT=n BONUS=x]  (exploration: frequency floor and pack score bonus)
#    [SHANJIE_MOEDICT=... SHANJIE_HETERO=...]   (the two reading-tool inputs, see tools/readings.py)
#   bash experiments/it-pack/run.sh build      pack + report (stops there; read $OUT/report/collisions.txt)
#   bash experiments/it-pack/run.sh checks     3.3, 3.4, 3.5 with the pack that `build` wrote (or a later one in $OUT/packs, see below)
#   bash experiments/it-pack/run.sh all        both
#
# `checks` loads $OUT/packs/{acg-add.tsv,it-add.tsv}; `build` fills it from the checkout's acg-add.tsv and the new it-add.tsv. After the
# maintainer's collision decisions are in data/packs/it-collisions.tsv, run `build` again (it overwrites $OUT/pack and $OUT/packs, so use a new OUT).
set -euo pipefail

REPO=${REPO:?set REPO to the checkout}
OUT=${OUT:?set OUT to the output directory}
SOURCES=${SOURCES:-$HOME/.cache/shanjie/sources/it}
BASE_REF=${BASE_REF:-67651c73}      # main before this branch: the engine the "pack off" outputs are compared with
THREADS=${THREADS:-$(nproc 2>/dev/null || sysctl -n hw.ncpu)}
PHASE=${1:-all}
cd "$REPO"
mkdir -p "$OUT"
sha() { if command -v sha256sum >/dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }
for f in bigram.sjlm classes.sjc kn.sjkn trigram.sjt3 tinylm.sjtl colloquial.sjlm; do
  want=$(cut -d' ' -f1 "data/$f.sha256"); got=$(sha "data/lm/$f" | cut -d' ' -f1)
  [ "$want" = "$got" ] || { echo "STOP: data/lm/$f is not the registered one"; exit 1; }
done
git diff --quiet HEAD || { echo "STOP: tracked files differ from HEAD"; exit 1; }
echo "commit $(git rev-parse HEAD)"

build() {
  [ -z "$(ls -A "$OUT" 2>/dev/null | grep -v '^base$')" ] || { echo "OUT is not empty: $OUT"; exit 1; }
  [ -d "$SOURCES" ] || { echo "no sources cache: $SOURCES"; exit 1; }
  cargo build --release --locked -q -p cli
  python3 -W ignore tools/build_it_pack.py build --offline --cache "$SOURCES" --counts build/it-pack/wiki-counts.tsv \
    --out "$OUT/pack" --report "$OUT/report" ${MIN_COUNT:+--min-count "$MIN_COUNT"} ${BONUS:+--bonus "$BONUS"} 2>&1 | tee "$OUT/build.log"
  mkdir -p "$OUT/packs"
  cp data/packs/acg-add.tsv "$OUT/pack/it-add.tsv" "$OUT/packs/"
  sha "$OUT/packs/"* | tee "$OUT/packs.sha256"
  echo "unresolved collisions and CHECK readings: $OUT/report/collisions.txt, $OUT/report/check-readings.txt"
}

# run CLI PROFILE PACKS(none|acg|acg,it) PACKSDIR NAME ROWS PREFIX   (the shipping flags: --context --tinylm --colloquial-core)
run() {
  local cli=$1 prof=$2 packs=$3 pdir=$4 name=$5 rows=$6 p=$7 pk=()
  if [ "$packs" != none ]; then pk=(--packs "$packs" --packs-dir "$pdir"); fi
  "$cli" --lm data/lm/bigram.sjlm --profile "$prof" --context --tinylm data/lm/tinylm.sjtl --colloquial-core data/lm/colloquial.sjlm \
    --rows "$rows" --name "$name" --rowstats "$p.rs" --dump "$p.dump" --tinylm-scores "$p.sc" --threads "$THREADS" \
    ${pk[@]+"${pk[@]}"} > "$p.sum"
}
top1sha() { sed -n "s/.*'top1_sha256': '\([0-9a-f]*\)'.*/\1/p" "$1"; }

checks() {
  [ -f "$OUT/packs/it-add.tsv" ] || { echo "no $OUT/packs/it-add.tsv: run build first"; exit 1; }
  export SHANJIE_VARIANTS="$REPO/eval/variants.tsv"
  local CLI="$REPO/target/release/shanjie-eval" W="$OUT/runs" PK="$OUT/packs" S=eval/bench/suite-v1
  cargo build --release --locked -q -p cli
  mkdir -p "$W"
  declare -A ROWS=([dev302]=$S/dev302.txt [typing76]=$S/typing76.txt [user-reported]=eval/dev/user-reported.txt)

  echo "== 3.3 pack off: top1_sha256 of main ($BASE_REF) and this branch, no pack and acg only, both profiles =="
  if [ ! -x "$OUT/base/target/release/shanjie-eval" ]; then
    git worktree add -f "$OUT/base" "$BASE_REF" >/dev/null 2>&1
    ln -sfn "$REPO/data/lm" "$OUT/base/data/lm"
    (cd "$OUT/base" && cargo build --release --locked -q -p cli)
  fi
  local BASECLI="$OUT/base/target/release/shanjie-eval" diff=0
  for set in dev302 typing76 user-reported; do for prof in chat formal; do for packs in none acg; do
    (cd "$OUT/base" && run "$BASECLI" "$prof" "$packs" "$REPO/data/packs" "$set" "$REPO/${ROWS[$set]}" "$W/off-base-$set-$prof-$packs")
    run "$CLI" "$prof" "$packs" "$REPO/data/packs" "$set" "${ROWS[$set]}" "$W/off-new-$set-$prof-$packs"
    a=$(top1sha "$W/off-base-$set-$prof-$packs.sum"); b=$(top1sha "$W/off-new-$set-$prof-$packs.sum")
    if [ -n "$a" ] && [ "$a" = "$b" ]; then echo "same  $set $prof $packs $a"; else echo "DIFF  $set $prof $packs base=$a new=$b"; diff=1; fi
  done; done; done
  [ "$diff" -eq 0 ] || echo "STOP (3.3): the pack-off outputs differ from main"

  echo "== 3.4 public sets: base = anime pack only, new = anime + IT =="
  for set in dev302 typing76 user-reported; do for prof in chat formal; do
    run "$CLI" "$prof" acg "$REPO/data/packs" "$set" "${ROWS[$set]}" "$W/acg-$set-$prof"
    run "$CLI" "$prof" acg,it "$PK" "$set" "${ROWS[$set]}" "$W/acgit-$set-$prof"
  done; done
  for set in typing76 dev302; do for prof in chat formal; do
    python3 experiments/it-pack/compare.py "$set $prof" "${ROWS[$set]}" "$W/acg-$set-$prof" "$W/acgit-$set-$prof"
  done; done
  python3 experiments/it-pack/compare.py "user-reported chat+formal" "${ROWS[user-reported]}" "$W/acg-user-reported-chat" "$W/acgit-user-reported-chat" \
    --pool "$W/acg-user-reported-formal" "$W/acgit-user-reported-formal"

  echo "== 3.4 anime sets (0 broken): chat and formal =="
  local fail=0
  for f in works characters character-parts recent interpunct; do for prof in chat formal; do
    run "$CLI" "$prof" acg "$REPO/data/packs" "acg-$f" "eval/dev/acg/$f.txt" "$W/acg-set-$f-$prof"
    run "$CLI" "$prof" acg,it "$PK" "acg-$f" "eval/dev/acg/$f.txt" "$W/acgit-set-$f-$prof"
    python3 experiments/it-pack/compare.py "acg-$f $prof" "eval/dev/acg/$f.txt" "$W/acg-set-$f-$prof" "$W/acgit-set-$f-$prof" || fail=1
  done; done
  [ "$fail" -eq 0 ] || echo "STOP (3.4): an anime set has a broken row"

  echo "== 3.5 check set eval/dev/it/words.txt: first-place counts, pack off vs on =="
  local CS=eval/dev/it/words.txt
  [ -f "$CS" ] || CS="$OUT/report/words.txt"
  for prof in chat formal; do
    run "$CLI" "$prof" acg "$REPO/data/packs" it-words "$CS" "$W/words-off-$prof"
    run "$CLI" "$prof" acg,it "$PK" it-words "$CS" "$W/words-on-$prof"
    echo "$prof: off $(sed -n "s/.*'top1': \([0-9]*\),.*/\1/p" "$W/words-off-$prof.sum") / on $(sed -n "s/.*'top1': \([0-9]*\),.*/\1/p" "$W/words-on-$prof.sum") of $(grep -c . "$CS")"
    python3 - "$CS" "$W/words-on-$prof" <<'PY'
import json, sys
rows = [l.rstrip("\n").split("|") for l in open(sys.argv[1], encoding="utf-8") if l.strip()]
dump = {}
for l in open(sys.argv[2] + ".dump", encoding="utf-8"):
    r, k, s, _ = l.rstrip("\n").split("\t")
    dump.setdefault(int(r), {})[int(k)] = s
for i, l in enumerate(open(sys.argv[2] + ".sc")):
    j = json.loads(l)
    if j["ok"][j["c"]] != 1:
        print(f"  not first with the pack: {rows[i][1]} -> {dump[i + 1][j['c'] + 1]}")
PY
  done
  echo "== done; results in $OUT =="
}

case "$PHASE" in
  build) build ;;
  checks) checks ;;
  all) build; checks ;;
  *) echo "usage: run.sh build|checks|all"; exit 1 ;;
esac
