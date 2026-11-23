#!/usr/bin/env bash
# Which core/tests/*.rs file runs in which CI job (docs/spec/release-and-ci.md §3.1): ci.yml's sharded jobs build
# their `cargo test` commands from this script's output, so ci.yml names no test file.
#
#   scripts/ci-shard.sh <platform> <shard>          one line of `--test <name>` arguments for `cargo test -p core`
#                                                   (nothing for macos `cli`, whose job runs `cargo test -p cli`)
#   scripts/ci-shard.sh <platform> --names          the platform's shard names, one per line
#   scripts/ci-shard.sh <platform> --check-matrix   the `shard:` matrix of the platform's job in ci.yml has exactly these names
#
# Platforms and shard names are fixed here. scripts/ci-shards.tsv assigns files to shards; a file it does not list runs
# in the platform's last shard, so a new core/tests/*.rs runs without an edit. cli/tests is not sharded and is not read.
# A file stem is its test target name: core has no [[test]] renames.
#
# Every call first checks the whole table and stops with the file or line at fault: each row's shard is one the platform
# has, each file exists, no file is listed twice for a platform, no shard is empty, and each platform's shards together
# are exactly core/tests/*.rs.
# Runs under macOS's bash 3.2 and Git Bash: no associative arrays, no mapfile.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TABLE="$ROOT/scripts/ci-shards.tsv"
WORKFLOW="$ROOT/.github/workflows/ci.yml"
PLATFORMS="macos windows debug"
fail() { echo "error: ci-shard.sh: $*" >&2; exit 1; }
argc=$#
arg_plat="${1-}"
arg_what="${2-}"

# The shard names of a platform, and the ci.yml job that carries them as its matrix.
names_of() {
  case "$1" in
    macos) echo "cli share rest" ;;
    windows | debug) echo "1 2" ;;
    *) return 1 ;;
  esac
}
job_of() {
  case "$1" in
    macos) echo core ;;
    windows) echo windows-tests ;;
    debug) echo debug-tests ;;
  esac
}
# has "<words>" <word>
has() { case " $1 " in *" $2 "*) return 0 ;; esac; return 1; }

files=""
for f in "$ROOT"/core/tests/*.rs; do
  [ -e "$f" ] || fail "no core/tests/*.rs under $ROOT"
  files="$files $(basename "$f" .rs)"
done

# rows: "<platform>:<shard>:<file>" words; listed: "<platform>:<file>" words.
rows=""
listed=""
n=0
[ -f "$TABLE" ] || fail "no $TABLE"
while IFS= read -r line || [ -n "$line" ]; do
  n=$((n + 1))
  line="${line%$'\r'}"
  case "$line" in '' | '#'*) continue ;; esac
  at="scripts/ci-shards.tsv line $n"
  old_ifs="$IFS"; IFS=$'\t'; set -f
  # shellcheck disable=SC2086
  set -- $line
  set +f; IFS="$old_ifs"
  [ "$#" -eq 3 ] || fail "$at: $# tab-separated fields, not 3 (platform, shard, core/<test file>)"
  plat="$1"; shard="$2"; target="$3"
  names="$(names_of "$plat")" || fail "$at: platform '$plat' is not one of: $PLATFORMS"
  has "$names" "$shard" || fail "$at: shard '$shard' is not one of platform $plat's: $names"
  [ "$shard" != cli ] || fail "$at: shard 'cli' of platform $plat takes no core/tests file (it runs the cli package)"
  case "$target" in core/*) ;; *) fail "$at: '$target' is not core/<test file> (only core/tests is sharded)" ;; esac
  stem="${target#core/}"
  has "$files" "$stem" || fail "$at: core/tests/$stem.rs does not exist"
  ! has "$listed" "$plat:$stem" || fail "$at: core/$stem is listed twice for platform $plat"
  listed="$listed $plat:$stem"
  rows="$rows $plat:$shard:$stem"
done < "$TABLE"

# The files of one shard: the rows that name it; the last shard also takes every file no row of the platform names.
shard_files() {
  local plat="$1" shard="$2" last="" s out=""
  for s in $(names_of "$plat"); do last="$s"; done
  for s in $files; do
    if has "$rows" "$plat:$shard:$s" || { [ "$shard" = "$last" ] && ! has "$listed" "$plat:$s"; }; then
      out="$out $s"
    fi
  done
  echo "$out"
}

total=0
for s in $files; do total=$((total + 1)); done
for plat in $PLATFORMS; do
  sum=0
  for shard in $(names_of "$plat"); do
    [ "$shard" != cli ] || continue
    k=0
    for s in $(shard_files "$plat" "$shard"); do k=$((k + 1)); done
    [ "$k" -gt 0 ] || fail "shard '$shard' of platform $plat has no test file: without a --test argument its job would run every file"
    sum=$((sum + k))
  done
  [ "$sum" -eq "$total" ] || fail "platform $plat: its shards hold $sum files, core/tests has $total"
done

[ "$argc" -eq 2 ] || fail "usage: ci-shard.sh <platform> <shard>|--names|--check-matrix"
plat="$arg_plat"
names="$(names_of "$plat")" || fail "platform '$plat' is not one of: $PLATFORMS"
case "$arg_what" in
  --names)
    # shellcheck disable=SC2086
    printf '%s\n' $names
    ;;
  --check-matrix)
    job="$(job_of "$plat")"
    [ -f "$WORKFLOW" ] || fail "no $WORKFLOW"
    # The first `shard: [a, b]` line inside the job's block (from `  <job>:` to the next key of the same depth).
    found="$(awk -v job="$job" '
      /^  [A-Za-z0-9_-]+:[[:space:]]*$/ { inside = ($1 == job ":") ; next }
      inside && /^[[:space:]]+shard:[[:space:]]*\[.*\]/ { sub(/^[^[]*\[/, ""); sub(/\].*$/, ""); gsub(/[,"\047]/, " "); print; exit }
    ' "$WORKFLOW")"
    # shellcheck disable=SC2086
    set -- $found
    [ "$#" -gt 0 ] || fail "ci.yml: job '$job' has no 'shard: [...]' matrix line"
    # shellcheck disable=SC2086
    want="$(printf '%s\n' $names | sort | tr '\n' ' ')"
    got="$(printf '%s\n' "$@" | sort | tr '\n' ' ')"
    [ "$want" = "$got" ] || fail "ci.yml: job '$job' has matrix shards [ $* ], but platform $plat's shards are [ $names ]"
    echo "ok: ci.yml job '$job' matrix shards: $*"
    ;;
  -*)
    fail "usage: ci-shard.sh <platform> <shard>|--names|--check-matrix"
    ;;
  *)
    has "$names" "$arg_what" || fail "shard '$arg_what' is not one of platform $plat's: $names"
    [ "$arg_what" != cli ] || exit 0
    out=""
    for s in $(shard_files "$plat" "$arg_what"); do out="$out --test $s"; done
    echo "${out# }"
    ;;
esac
