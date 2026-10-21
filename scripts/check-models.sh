#!/bin/bash
# docs/contracts/about-models.md section 2: data/models.tsv is the one list of the bundled models. Rows are
# `file<TAB>SHA-256<TAB>中文名<TAB>對外名稱`, no header, no comments, no blank lines (the Swift parser skips nothing).
# The names are not checked here (they are reviewed against docs/releases/README.md); the hashes are.
#
#   scripts/check-models.sh build [ROOT]        models.tsv vs data/*.sha256, and data/lm/<file> exists and hashes to it
#   scripts/check-models.sh bundle RESOURCES    both ways: every model file in RESOURCES has one row in RESOURCES/models.tsv,
#                                               every row's file is there, and its hash is the file's actual hash
set -euo pipefail

fail() { echo "error: $*" >&2; exit 1; }
sha() { shasum -a 256 "$1" | awk '{ print $1 }'; }

# Prints the file column after checking the shape of every row: four fields, a SHA-256, no duplicate file.
rows() {
  local tsv="$1" n=0 file hash zh en extra seen=$'\n'
  [ -s "$tsv" ] || fail "$tsv is missing or empty"
  while IFS=$'\t' read -r file hash zh en extra || [ -n "$file" ]; do
    n=$((n + 1))
    [ -n "$file" ] && [ -n "$hash" ] && [ -n "$zh" ] && [ -n "$en" ] && [ -z "${extra:-}" ] \
      || fail "$tsv row $n: expected 4 non-empty tab-separated fields (file, SHA-256, 中文名, 對外名稱)"
    [[ "$hash" =~ ^[0-9a-f]{64}$ ]] || fail "$tsv row $n ($file): the hash is not a SHA-256"
    case "$seen" in *$'\n'"$file"$'\n'*) fail "$tsv row $n: $file is listed twice" ;; esac
    seen="$seen$file"$'\n'
  done < "$tsv"
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
  R="${2:?usage: check-models.sh bundle RESOURCES}"
  rows "$R/models.tsv"
  n=0
  while IFS=$'\t' read -r file hash _; do
    n=$((n + 1))
    [ -f "$R/$file" ] || fail "models.tsv row $n ($file): Resources/$file is missing"
    [ "$(sha "$R/$file")" = "$hash" ] || fail "models.tsv row $n ($file): Resources/$file does not hash to the row's SHA-256"
  done < "$R/models.tsv"
  for f in "$R"/*.sjlm "$R"/*.sjc "$R"/*.sjkn "$R"/*.sjtl "$R"/*.sjt3; do
    [ -e "$f" ] || continue
    [ "$(cut -f1 "$R/models.tsv" | grep -cxF "$(basename "$f")")" -eq 1 ] \
      || fail "Resources/$(basename "$f") is a model file without exactly one row in models.tsv"
  done
  ;;
*) fail "usage: check-models.sh build [ROOT] | bundle RESOURCES" ;;
esac
