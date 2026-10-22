#!/bin/bash
# docs/contracts/about-models.md section 2: data/models.tsv is the one list of the bundled models. Rows are
# `file<TAB>SHA-256<TAB>中文名<TAB>對外名稱`, no header, no comments, no blank lines (the Swift parser skips nothing).
# The names are not checked here (they are reviewed against docs/releases/README.md); the hashes are.
#
#   scripts/check-models.sh build [ROOT]        models.tsv vs data/*.sha256, and data/lm/<file> exists and hashes to it
#   scripts/check-models.sh bundle RESOURCES [ROOT]
#                                               both ways: every model file in RESOURCES has one row in RESOURCES/models.tsv,
#                                               every row's file is there, its hash is the file's actual hash and the one in ROOT/data/<file>.sha256
set -euo pipefail

fail() { echo "error: $*" >&2; exit 1; }
sha() { shasum -a 256 "$1" | awk '{ print $1 }'; }

# Checks the shape of models.tsv, strictly (the Swift parser, macos/Sources/ShanjieKit/ModelList.swift, has the same rules):
# the last byte is a newline, no CR anywhere, every line exactly four non-empty tab-separated fields (so `read` below cannot
# collapse a tab or drop a last row), the second a SHA-256, no file listed twice. Prints nothing; fails on the first bad row.
rows() {
  local tsv="$1"
  [ -s "$tsv" ] || fail "$tsv is missing or empty"
  [ "$(tail -c 1 "$tsv" | od -An -c | tr -d ' ')" = '\n' ] || fail "$tsv does not end with a newline"
  ! grep -q $'\r' "$tsv" || fail "$tsv holds a CR (CRLF line ends)"
  awk -F'\t' -v t="$tsv" '
    function bad(m) { print "error: " t " row " NR ": " m > "/dev/stderr"; exit 1 }
    NF != 4 { bad("expected 4 tab-separated fields (file, SHA-256, 中文名, 對外名稱)") }
    $1 == "" || $2 == "" || $3 == "" || $4 == "" { bad("an empty field") }
    $2 !~ /^[0-9a-f]{64}$/ { bad($1 " has a hash that is not a SHA-256") }
    seen[$1]++ { bad($1 " is listed twice") }' "$tsv"
}

mode="${1:-}"
case "$mode" in
build)
  ROOT="${2:-$(cd "$(dirname "$0")/.." && pwd)}"
  T="$ROOT/data/models.tsv"
  rows "$T"
  n=0
  while IFS=$'\t' read -r file hash _; do
    n=$((n + 1))
    recorded="$(awk 'NR == 1 { print $1 }' "$ROOT/data/$file.sha256" 2>/dev/null || true)"
    [ "$recorded" = "$hash" ] || fail "models.tsv row $n ($file): its hash is not the one in data/$file.sha256; update the row with the model"
    [ -f "$ROOT/data/lm/$file" ] || fail "models.tsv row $n ($file): data/lm/$file is missing"
    [ "$(sha "$ROOT/data/lm/$file")" = "$hash" ] || fail "models.tsv row $n ($file): data/lm/$file does not hash to the row's SHA-256"
  done < "$T"
  ;;
bundle)
  R="${2:?usage: check-models.sh bundle RESOURCES [ROOT]}"
  ROOT="${3:-$(cd "$(dirname "$0")/.." && pwd)}"
  rows "$R/models.tsv"
  n=0
  while IFS=$'\t' read -r file hash _; do
    n=$((n + 1))
    recorded="$(awk 'NR == 1 { print $1 }' "$ROOT/data/$file.sha256" 2>/dev/null || true)"
    [ "$recorded" = "$hash" ] || fail "models.tsv row $n ($file): its hash is not the one in data/$file.sha256"
    [ -f "$R/$file" ] || fail "models.tsv row $n ($file): Resources/$file is missing"
    [ "$(sha "$R/$file")" = "$hash" ] || fail "models.tsv row $n ($file): Resources/$file does not hash to the row's SHA-256"
  done < "$R/models.tsv"
  # A model with a new file extension must add it to this list, or the "file without a row" check cannot see it.
  for f in "$R"/*.sjlm "$R"/*.sjc "$R"/*.sjkn "$R"/*.sjtl "$R"/*.sjt3; do
    [ -e "$f" ] || continue
    [ "$(cut -f1 "$R/models.tsv" | grep -cxF "$(basename "$f")")" -eq 1 ] \
      || fail "Resources/$(basename "$f") is a model file without exactly one row in models.tsv"
  done
  ;;
*) fail "usage: check-models.sh build [ROOT] | bundle RESOURCES" ;;
esac
