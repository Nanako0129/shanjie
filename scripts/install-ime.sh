#!/bin/bash
# Install shanjie.app as the current user's input method (docs/contracts/s3b.md section 4).
# Run by the user only, never by an agent or CI. No sudo: it writes only to the user's
# ~/Library/Input Methods.
#
#   scripts/install-ime.sh <path to shanjie.app>
#
# The argument is usually the unzipped release asset; a local build/shanjie.app (ad-hoc signed)
# works too.
set -euo pipefail

fail() { echo "error: $*" >&2; exit 1; }

[ -n "${HOME:-}" ] || fail "HOME is empty"
[ "$(id -u)" -ne 0 ] || fail "do not run this with sudo or as root"
[ "$#" -eq 1 ] || fail "usage: $0 <path to shanjie.app>"
SRC="$1"
[ -d "$SRC" ] && [ -x "$SRC/Contents/MacOS/shanjie" ] || fail "$SRC is not a shanjie.app"
SRC="$(cd "$SRC" && pwd -P)"
[ "$SRC" != "$(cd "$HOME/Library/Input Methods/shanjie.app" 2>/dev/null && pwd -P)" ] \
  || fail "give the downloaded or built app, not the installed copy"

# 1. Stage the new bundle next to the destination first (same volume, so step 2 is a rename). If
#    the copy fails, the installed bundle is untouched. The staging name is not *.app, so the
#    input method system never sees it. These two literal paths are the only ones rm -rf touches.
mkdir -p "$HOME/Library/Input Methods"
rm -rf "$HOME/Library/Input Methods/.shanjie-staging"
trap 'rm -rf "$HOME/Library/Input Methods/.shanjie-staging"' EXIT
ditto "$SRC" "$HOME/Library/Input Methods/.shanjie-staging"

# 2. Replace: remove the old bundle, then rename the staged copy into place.
rm -rf "$HOME/Library/Input Methods/shanjie.app"
[ ! -e "$HOME/Library/Input Methods/shanjie.app" ] || fail "the old bundle could not be removed"
mv "$HOME/Library/Input Methods/.shanjie-staging" "$HOME/Library/Input Methods/shanjie.app"

# 3. Stop the running old copy, matched by its full path; none running is fine.
rc=0
pkill -f "$HOME/Library/Input Methods/shanjie.app/Contents/MacOS/shanjie" || rc=$?
[ "$rc" -eq 0 ] || [ "$rc" -eq 1 ] || fail "pkill failed ($rc)"

# 4. Register and enable both input modes, from the installed copy.
"$HOME/Library/Input Methods/shanjie.app/Contents/MacOS/shanjie" install

echo
echo "Next: open System Settings > Keyboard > Input Sources and check that 善解（標準）and"
echo "善解（倚天）are listed. If they are not, log out and log back in."
