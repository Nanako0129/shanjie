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

# Good arguments: exactly the two lines differ from the template, and they carry the values.
"$R" 1.2.3 "$SHA" > "$T/out.rb"
diff "$ROOT/packaging/Casks/shanjie.rb" "$T/out.rb" | grep '^>' > "$T/added" || true
[ "$(wc -l < "$T/added")" -eq 2 ] || fail "expected two changed lines: $(cat "$T/added")"
grep -qx '>   version "1.2.3"' "$T/added" || fail "version line not replaced"
grep -qx ">   sha256 \"$SHA\"" "$T/added" || fail "sha256 line not replaced"

# Bad arguments and templates: exit 1, nothing on stdout.
refuses() {  # refuses <label> <args...>
  local label="$1"; shift
  if "$@" > "$T/stdout" 2>/dev/null; then fail "$label: accepted"; fi
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
