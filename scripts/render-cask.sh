#!/usr/bin/env bash
# Print the Homebrew cask for one release (docs/contracts/s3b.md section 14.2): the template
# packaging/Casks/shanjie.rb with its version and sha256 lines replaced. Used by CI (brew style on
# placeholder values) and by release.yml (the cask pushed to Nanako0129/homebrew-tap).
#
#   scripts/render-cask.sh <MAJOR.MINOR.PATCH> <sha256, 64 lowercase hex>
#
# Prints nothing and exits 1 on a malformed argument or a template without exactly one of each
# line. SHANJIE_CASK_TEMPLATE overrides the template path (tests only).
set -euo pipefail

fail() { echo "error: $*" >&2; exit 1; }
[ "$#" -eq 2 ] || fail "usage: $0 <version> <sha256>"
version="$1"
sha="$2"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "version is not MAJOR.MINOR.PATCH"
[[ "$sha" =~ ^[0-9a-f]{64}$ ]] || fail "sha256 is not 64 lowercase hex characters"
template="${SHANJIE_CASK_TEMPLATE:-$(dirname "$0")/../packaging/Casks/shanjie.rb}"
[ -f "$template" ] || fail "template not found: $template"
[ "$(grep -c '^  version "[^"]*"$' "$template")" -eq 1 ] || fail "template needs exactly one version line"
[ "$(grep -c '^  sha256 "[^"]*"$' "$template")" -eq 1 ] || fail "template needs exactly one sha256 line"

# Both values are limited to [0-9.] and [0-9a-f] above, so they cannot break the sed expression.
sed -e "s/^  version \"[^\"]*\"\$/  version \"$version\"/" \
    -e "s/^  sha256 \"[^\"]*\"\$/  sha256 \"$sha\"/" "$template"
