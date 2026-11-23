#!/usr/bin/env bash
# scripts/ci-shard.sh: the repo's own table and ci.yml (every platform's matrix has the script's shard names, every
# core/tests file is in exactly one shard), then a copy of the script in fake trees: the output, a new test file landing
# in the last shard, a new cli/tests file changing nothing, and each refusal (a row for a missing file, a file listed
# twice, a shard the platform does not have, an empty shard, a matrix that differs).
#
#   scripts/test-ci-shard.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
fail() { echo "error: $*" >&2; exit 1; }

# ---- the repo itself
for plat in macos windows debug; do
  "$ROOT/scripts/ci-shard.sh" "$plat" --check-matrix
  : > "$T/union"
  for shard in $("$ROOT/scripts/ci-shard.sh" "$plat" --names); do
    args="$("$ROOT/scripts/ci-shard.sh" "$plat" "$shard")"
    if [ "$shard" = cli ]; then
      [ -z "$args" ] || fail "$plat cli printed: $args"
      continue
    fi
    [ -n "$args" ] || fail "$plat $shard printed nothing"
    # shellcheck disable=SC2086
    printf '%s\n' $args | grep -v -x -e '--test' >> "$T/union"
  done
  sort "$T/union" > "$T/got"
  for f in "$ROOT"/core/tests/*.rs; do basename "$f" .rs; done | sort > "$T/want"
  diff "$T/want" "$T/got" || fail "platform $plat: its shards are not core/tests/*.rs, each once"
done
echo "ok: the repo's table: every core/tests file once on each platform"

# ---- fake trees
S="$T/tree/scripts/ci-shard.sh"
tree() {   # tree <table rows...>: a fresh tree with core/tests a-d, cli/tests x, and the rows as its table
  rm -rf "$T/tree"
  mkdir -p "$T/tree/scripts" "$T/tree/core/tests" "$T/tree/cli/tests" "$T/tree/.github/workflows"
  cp "$ROOT/scripts/ci-shard.sh" "$S"
  for f in a b c d; do : > "$T/tree/core/tests/$f.rs"; done
  : > "$T/tree/cli/tests/x.rs"
  mkdir "$T/tree/core/tests/common"   # a helper directory is not a test file
  printf '# comment\n\n' > "$T/tree/scripts/ci-shards.tsv"
  local row
  for row in "$@"; do printf '%s\n' "$row" | tr ' ' '\t' >> "$T/tree/scripts/ci-shards.tsv"; done
  cat > "$T/tree/.github/workflows/ci.yml" <<'EOF'
jobs:
  other:
    strategy:
      matrix:
        shard: [9]
  core:
    strategy:
      matrix:
        # a comment
        shard: [cli, share, rest]
  windows-tests:
    strategy:
      matrix:
        shard: [1, 2]
  debug-tests:
    strategy:
      matrix:
        shard: [ "2", '1' ]
EOF
}
is() {   # is "<expected stdout>" <args...>
  local want="$1" got; shift
  got="$("$S" "$@")" || fail "ci-shard.sh $* failed"
  [ "$got" = "$want" ] || fail "ci-shard.sh $*: printed '$got', not '$want'"
}
refused() {   # refused "<text of the message>" <args...>
  local text="$1" rc=0; shift
  "$S" "$@" > "$T/out" 2> "$T/err" || rc=$?
  [ "$rc" -ne 0 ] || fail "ci-shard.sh $* was not refused (wanted: $text)"
  [ ! -s "$T/out" ] || fail "ci-shard.sh $* printed arguments before failing: $(cat "$T/out")"
  grep -q -F -- "$text" "$T/err" || fail "ci-shard.sh $*: the message is '$(cat "$T/err")', without '$text'"
}
BASE=("macos share core/a" "windows 1 core/a" "windows 1 core/c" "debug 1 core/b")

tree "${BASE[@]}"
is "--test a" macos share
is "--test b --test c --test d" macos rest
is "" macos cli
is "--test a --test c" windows 1
is "--test b --test d" windows 2
is "--test b" debug 1
is "--test a --test c --test d" debug 2
is "cli
share
rest" macos --names
is "1
2" windows --names
for plat in macos windows debug; do "$S" "$plat" --check-matrix > /dev/null || fail "the fake ci.yml's $plat matrix was refused"; done
refused "usage" macos
refused "platform 'linux' is not one of" linux 1
refused "shard '3' is not one of" windows 3
echo "ok: the output and the names"

# A new core test file runs in the last shard of every platform; the other shards do not change.
: > "$T/tree/core/tests/e.rs"
is "--test a" macos share
is "--test b --test c --test d --test e" macos rest
is "--test a --test c" windows 1
is "--test b --test d --test e" windows 2
is "--test b" debug 1
is "--test a --test c --test d --test e" debug 2
echo "ok: a new core/tests file lands in the last shard"

# A new cli test file needs no row and changes nothing.
tree "${BASE[@]}"
: > "$T/tree/cli/tests/brand_new.rs"
is "--test b --test c --test d" macos rest
is "" macos cli
is "--test b --test d" windows 2
echo "ok: a new cli/tests file changes nothing"

# Refusals: whichever shard is asked for, the whole table is checked first.
tree "${BASE[@]}" "debug 1 core/gone"
refused "line 7: core/tests/gone.rs does not exist" macos rest
refused "core/tests/gone.rs does not exist" debug --names
tree "${BASE[@]}" "windows 2 core/a"
refused "line 7: core/a is listed twice for platform windows" macos share
tree "${BASE[@]}" "macos shar core/b"
refused "line 7: shard 'shar' is not one of platform macos's: cli share rest" windows 1
tree "${BASE[@]}" "windows rest core/b"
refused "shard 'rest' is not one of platform windows's: 1 2" windows 1
tree "${BASE[@]}" "macos cli core/b"
refused "shard 'cli' of platform macos takes no core/tests file" macos rest
tree "${BASE[@]}" "linux 1 core/b"
refused "platform 'linux' is not one of" macos rest
tree "${BASE[@]}" "macos share cli/x"
refused "'cli/x' is not core/<test file>" macos rest
tree "${BASE[@]}" "macos share"
refused "2 tab-separated fields, not 3" macos rest
tree "${BASE[@]}" "macos share core/b" "macos share core/c" "macos share core/d"
refused "shard 'rest' of platform macos has no test file" macos share
tree "debug 1 core/b"
refused "shard 'share' of platform macos has no test file" debug 1
tree "${BASE[@]}"
rm "$T/tree/scripts/ci-shards.tsv"
refused "ci-shards.tsv" macos rest
echo "ok: a missing file, a file listed twice, an unknown shard, an empty shard and a malformed row are refused"

# The matrix check: a name missing, a name added, a name misspelt, and a job without the matrix.
tree "${BASE[@]}"
W="$T/tree/.github/workflows/ci.yml"
sed 's/shard: \[cli, share, rest\]/shard: [cli, rest]/' "$W" > "$W.new" && mv "$W.new" "$W"
refused "job 'core' has matrix shards [ cli rest ], but platform macos's shards are [ cli share rest ]" macos --check-matrix
"$S" windows --check-matrix > /dev/null || fail "the windows matrix was refused for the core job's fault"
tree "${BASE[@]}"
sed 's/shard: \[1, 2\]/shard: [1, 2, 3]/' "$W" > "$W.new" && mv "$W.new" "$W"
refused "job 'windows-tests' has matrix shards [ 1 2 3 ]" windows --check-matrix
tree "${BASE[@]}"
sed 's/shard: \[cli, share, rest\]/shard: [cli, shared, rest]/' "$W" > "$W.new" && mv "$W.new" "$W"
refused "job 'core' has matrix shards [ cli shared rest ]" macos --check-matrix
tree "${BASE[@]}"
grep -v "\"2\", '1'" "$W" > "$W.new" && mv "$W.new" "$W"
refused "job 'debug-tests' has no 'shard: [...]' matrix line" debug --check-matrix
echo "ok: a matrix that differs from the names is refused"

echo "test-ci-shard.sh: all passed"
