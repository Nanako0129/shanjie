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

# The only paths rm -rf or mv ever touch, written out literally: the installed bundle, the staged
# copy and the set-aside previous bundle. The two helper names are not *.app, so the input method
# system never treats them as input methods.
cleanup() {
  # Interrupted or failed between setting the old bundle aside and moving the new one in: put the
  # old one back, so the user is never left without an installed copy.
  if [ ! -e "$HOME/Library/Input Methods/shanjie.app" ] && [ -e "$HOME/Library/Input Methods/.shanjie-old" ]; then
    mv "$HOME/Library/Input Methods/.shanjie-old" "$HOME/Library/Input Methods/shanjie.app" || true
  fi
  rm -rf "$HOME/Library/Input Methods/.shanjie-staging"
}
trap cleanup EXIT

# 1. Stage the new bundle next to the destination (same volume). A failed copy leaves the installed
#    bundle untouched.
mkdir -p "$HOME/Library/Input Methods"
rm -rf "$HOME/Library/Input Methods/.shanjie-staging" "$HOME/Library/Input Methods/.shanjie-old"
ditto "$SRC" "$HOME/Library/Input Methods/.shanjie-staging"

# 2. Swap by renames: set the old bundle aside, move the new one in (cleanup restores the old one if
#    this fails), then delete the old one. A failure to delete it leaves .shanjie-old behind with a
#    warning; the new bundle is already in place.
if [ -e "$HOME/Library/Input Methods/shanjie.app" ]; then
  mv "$HOME/Library/Input Methods/shanjie.app" "$HOME/Library/Input Methods/.shanjie-old"
fi
mv "$HOME/Library/Input Methods/.shanjie-staging" "$HOME/Library/Input Methods/shanjie.app"
rm -rf "$HOME/Library/Input Methods/.shanjie-old" \
  || echo "warning: could not delete the previous bundle at ~/Library/Input Methods/.shanjie-old" >&2

# Test hook (scripts/test-install-ime.sh, with HOME pointed at a temporary directory): stop after the
# file swap, before touching running processes or the input source registry.
if [ "${SHANJIE_INSTALL_FILES_ONLY:-}" = 1 ]; then
  echo "files only: installed to $HOME/Library/Input Methods/shanjie.app"
  exit 0
fi

# 3. Stop the running old copy, matched by its full path; none running is fine.
rc=0
pkill -f "$HOME/Library/Input Methods/shanjie.app/Contents/MacOS/shanjie" || rc=$?
[ "$rc" -eq 0 ] || [ "$rc" -eq 1 ] || fail "pkill failed ($rc)"

# 4. Register and enable both input modes, from the installed copy.
"$HOME/Library/Input Methods/shanjie.app/Contents/MacOS/shanjie" install

echo
echo "Next: open System Settings > Keyboard > Input Sources and check that 善解（標準）and"
echo "善解（倚天）are listed. If they are not, log out and log back in."
