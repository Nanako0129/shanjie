#!/bin/bash
# Install shanjie.app as the current user's input method (docs/contracts/s3b.md section 4).
# Run by the user. Agents and CI only run it through scripts/test-install-ime.sh, with HOME on a
# temporary directory, a stub app and SHANJIE_INSTALL_FILES_ONLY=1. No sudo: it writes only to the
# user's ~/Library/Input Methods (and moves a previous copy to ~/.Trash).
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

# The only paths this script removes or moves, written out literally. The staging name carries
# this run's PID, so two overlapping installs never touch each other's copy, and it is not *.app,
# so the input method system never treats it as an input method. The previous bundle is moved to
# the Trash under a unique name and never deleted.
#
# 1. Copy the new bundle to this run's staging directory next to the destination. A failed copy
#    leaves any installed bundle untouched.
mkdir -p "$HOME/Library/Input Methods"
trap 'rm -rf "$HOME/Library/Input Methods/.shanjie-staging-$$"' EXIT
ditto "$SRC" "$HOME/Library/Input Methods/.shanjie-staging-$$"

# 2. Move a previous bundle to the Trash (as McBopomofo's installer does) instead of deleting it,
#    so it can be put back by hand; then rename the staged copy into place.
if [ -e "$HOME/Library/Input Methods/shanjie.app" ]; then
  mkdir -p "$HOME/.Trash"
  OLD="$HOME/.Trash/shanjie-$(date +%Y%m%d-%H%M%S)-$$.app"
  mv "$HOME/Library/Input Methods/shanjie.app" "$OLD"
  echo "previous version moved to the Trash: $OLD"
fi
mv "$HOME/Library/Input Methods/.shanjie-staging-$$" "$HOME/Library/Input Methods/shanjie.app"

# Test hook (scripts/test-install-ime.sh): stop after the file swap, before touching running
# processes or the input source registry.
if [ "${SHANJIE_INSTALL_FILES_ONLY:-}" = 1 ]; then
  echo "files only: installed to $HOME/Library/Input Methods/shanjie.app"
  exit 0
fi

# 3. Stop the running old copy, matched by its full path; none running is fine.
rc=0
pkill -f "$HOME/Library/Input Methods/shanjie.app/Contents/MacOS/shanjie" || rc=$?
[ "$rc" -eq 0 ] || [ "$rc" -eq 1 ] || fail "pkill failed ($rc)"

# 4. Register and enable both input modes, from the installed copy.
"$HOME/Library/Input Methods/shanjie.app/Contents/MacOS/shanjie" install \
  || fail "registering the input method failed; if a previous version is in the Trash, you can put it back in ~/Library/Input Methods"

echo
echo "Next: open System Settings > Keyboard > Input Sources and check that 善解（標準）and"
echo "善解（倚天）are listed. If they are not, log out and log back in."
