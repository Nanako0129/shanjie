#!/usr/bin/env bash
# scripts/check-ci-gate.sh against a stub `gh` (no network): a tag on the appcast.xml commit that
# release.yml's publish job writes to main (no ci.yml run of its own) is judged by its parent's run;
# a commit that changes appcast.xml and anything else is not walked over.
#
#   scripts/test-check-ci-gate.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
fail() { echo "error: $*" >&2; exit 1; }

A=$(printf 'a%.0s' {1..40})   # appcast.xml only, parent B
B=$(printf 'b%.0s' {1..40})   # a code commit, parent C
C=$(printf 'c%.0s' {1..40})
D=$(printf 'd%.0s' {1..40})   # appcast.xml and README.md, parent B
E=$(printf 'e%.0s' {1..40})   # appcast.xml only, parent A (two feed commits in a row)

mkdir -p "$T/commits" "$T/runs"
commit() { printf '{"parents":[{"sha":"%s"}],"files":%s}' "$2" "$3" > "$T/commits/$1.json"; }
commit "$A" "$B" '[{"filename":"appcast.xml"}]'
commit "$B" "$C" '[{"filename":"macos/Sources/Shanjie/main.swift"}]'
commit "$D" "$B" '[{"filename":"appcast.xml"},{"filename":"README.md"}]'
commit "$E" "$A" '[{"filename":"appcast.xml"}]'
run() { printf '{"workflow_runs":[{"head_sha":"%s","head_branch":"main","event":"push","status":"completed","conclusion":"%s","created_at":"2026-10-16T00:00:00Z","html_url":"https://example.invalid/%s"}]}' "$1" "$2" "$1" > "$T/runs/$1.json"; }

cat > "$T/gh" <<'EOF'
#!/usr/bin/env bash
# Stub of `gh api <path>`: commits and ci.yml runs from the fixture directory.
set -euo pipefail
dir="$(dirname "$0")"
[ "$1" = api ] || exit 9
case "$2" in
  */commits/*) cat "$dir/commits/${2##*/}.json" ;;
  */actions/workflows/ci.yml/runs\?head_sha=*)
    sha="${2#*head_sha=}"; sha="${sha%%&*}"
    if [ -f "$dir/runs/$sha.json" ]; then cat "$dir/runs/$sha.json"; else echo '{"workflow_runs":[]}'; fi ;;
  *) exit 9 ;;
esac
EOF
chmod +x "$T/gh"

gate() {  # gate <sha>: the gate's exit code, output in $T/out
  rc=0
  CI_GATE_GH="$T/gh" GITHUB_REPOSITORY=o/r CI_GATE_INTERVAL=0 CI_GATE_GRACE=0 CI_GATE_TIMEOUT=0 \
    "$ROOT/scripts/check-ci-gate.sh" "$1" > "$T/out" 2>&1 || rc=$?
  return "$rc"
}

run "$B" success
gate "$A" || fail "a tag on the appcast commit did not use its parent's green run: $(cat "$T/out")"
grep -q "using its parent $B" "$T/out" || fail "the walk is not reported"
gate "$E" || fail "two appcast commits in a row were not both walked over: $(cat "$T/out")"
gate "$B" || fail "a green code commit failed the gate"
! gate "$D" || fail "a commit changing appcast.xml and README.md was walked over"

run "$B" failure
! gate "$A" || fail "a tag on the appcast commit passed although its parent's run failed"
grep -q "concluded 'failure'" "$T/out" || fail "the failure is not reported: $(cat "$T/out")"

run "$A" success
run "$B" failure
! gate "$A" || fail "the appcast commit's own run was used instead of its parent's"
echo "check-ci-gate.sh: ok"
