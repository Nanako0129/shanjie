#!/usr/bin/env bash
# Checks scripts/render-cask.sh (docs/contracts/s3b.md section 14.5, acceptance 2): good arguments
# change only the version and sha256 lines; every bad argument or template exits 1 with no output.
#
#   scripts/test-render-cask.sh
set -euo pipefail

fail() { echo "FAIL: $*" >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
R="$ROOT/scripts/render-cask.sh"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
SHA=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef

# Good arguments: the output is the template with exactly those two lines replaced, nothing else
# added or removed.
"$R" 1.2.3 "$SHA" > "$T/out.rb"
sed -e 's/^  version "[^"]*"$/  version "1.2.3"/' -e "s/^  sha256 \"[^\"]*\"\$/  sha256 \"$SHA\"/" \
  "$ROOT/packaging/Casks/shanjie.rb" > "$T/expected.rb"
cmp -s "$T/expected.rb" "$T/out.rb" || fail "output is not the template with two lines replaced: $(diff "$T/expected.rb" "$T/out.rb")"
[ "$(diff "$ROOT/packaging/Casks/shanjie.rb" "$T/out.rb" | grep -c '^[<>]')" -eq 4 ] \
  || fail "expected exactly two lines to change"
grep -qx '  version "1.2.3"' "$T/out.rb" || fail "version line not replaced"
grep -qx "  sha256 \"$SHA\"" "$T/out.rb" || fail "sha256 line not replaced"

# Bad arguments and templates: exit 1, nothing on stdout.
refuses() {  # refuses <label> <args...>: exit code exactly 1, empty stdout
  local label="$1" rc=0; shift
  "$@" > "$T/stdout" 2>/dev/null || rc=$?
  [ "$rc" -eq 1 ] || fail "$label: exit code $rc, expected 1"
  [ ! -s "$T/stdout" ] || fail "$label: printed output"
}
refuses "no arguments" "$R"
refuses "one argument" "$R" 1.2.3
refuses "three arguments" "$R" 1.2.3 "$SHA" x
refuses "v prefix" "$R" v1.2.3 "$SHA"
refuses "prerelease" "$R" 1.2.3-beta "$SHA"
refuses "two-part version" "$R" 1.2 "$SHA"
refuses "uppercase sha" "$R" 1.2.3 "$(tr a-f A-F <<<"$SHA")"
refuses "short sha" "$R" 1.2.3 "${SHA:1}"
refuses "sha with quote" "$R" 1.2.3 "${SHA:1}\""
grep -v '^  version ' "$ROOT/packaging/Casks/shanjie.rb" > "$T/no-version.rb"
refuses "template without version" env SHANJIE_CASK_TEMPLATE="$T/no-version.rb" "$R" 1.2.3 "$SHA"
{ cat "$ROOT/packaging/Casks/shanjie.rb"; echo '  sha256 "x"'; } > "$T/two-sha.rb"
refuses "template with two sha256 lines" env SHANJIE_CASK_TEMPLATE="$T/two-sha.rb" "$R" 1.2.3 "$SHA"
refuses "missing template" env SHANJIE_CASK_TEMPLATE="$T/none.rb" "$R" 1.2.3 "$SHA"

echo "render-cask.sh: ok"
