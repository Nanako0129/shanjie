#!/bin/bash
# Install shanjie.app as the current user's input method (docs/contracts/s3b.md section 4).
# Run by the user. Agents and CI only run it through scripts/test-install-ime.sh, with HOME on a
# temporary directory, a stub app and SHANJIE_INSTALL_FILES_ONLY=1. No sudo: it writes only to the
# user's ~/Library/Input Methods. Do not run two installs at the same time.
#
#   scripts/install-ime.sh <path to shanjie.app>
#
# The argument is usually the unzipped release asset; a local build/shanjie.app (ad-hoc signed)
# works too.
set -euo pipefail

fail() { echo "error: $*" >&2; exit 1; }
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister

[ -n "${HOME:-}" ] || fail "HOME is empty"
[ "$(id -u)" -ne 0 ] || fail "do not run this with sudo or as root"
[ "$#" -eq 1 ] || fail "usage: $0 <path to shanjie.app>"
SRC="$1"
[ -d "$SRC" ] && [ -x "$SRC/Contents/MacOS/shanjie" ] || fail "$SRC is not a shanjie.app"

# Paths this script removes or moves: the installed bundle, a unique staging directory and the
# kept previous version, all inside ~/Library/Input Methods. Neither helper name ends in .app, so
# neither the input method system nor LaunchServices treats them as apps.
mkdir -p "$HOME/Library/Input Methods"

# 1. Copy the new bundle to a fresh staging directory next to the destination. A failed copy
#    leaves the installed bundle untouched.
STAGE="$(mktemp -d "$HOME/Library/Input Methods/.shanjie-staging-XXXXXX")"
cleanup() {
  # If the script stops after the old bundle was set aside but before the new one moved in, put
  # the new one in place rather than deleting it: the user always ends up with an installed copy.
  if [ ! -e "$HOME/Library/Input Methods/shanjie.app" ] && [ -d "$STAGE/shanjie.app" ]; then
    mv "$STAGE/shanjie.app" "$HOME/Library/Input Methods/shanjie.app" || true
  fi
  rm -rf "$STAGE"
}
trap cleanup EXIT
ditto "$SRC" "$STAGE/shanjie.app"

# 2. Keep the previous version as ~/Library/Input Methods/.shanjie-previous (replacing an older one
#    kept there), unregistered from LaunchServices so the system cannot launch it by bundle ID,
#    then move the new bundle into place.
if [ -e "$HOME/Library/Input Methods/shanjie.app" ]; then
  "$LSREGISTER" -u "$HOME/Library/Input Methods/shanjie.app" 2>/dev/null || true
  rm -rf "$HOME/Library/Input Methods/.shanjie-previous"
  mv "$HOME/Library/Input Methods/shanjie.app" "$HOME/Library/Input Methods/.shanjie-previous"
  KEPT=1
fi
mv "$STAGE/shanjie.app" "$HOME/Library/Input Methods/shanjie.app"
[ "${KEPT:-}" = 1 ] && echo "previous version kept at ~/Library/Input Methods/.shanjie-previous"

# Test hook (scripts/test-install-ime.sh): stop after the file swap, before touching running
# processes or the input source registry.
if [ "${SHANJIE_INSTALL_FILES_ONLY:-}" = 1 ]; then
  echo "files only: installed to $HOME/Library/Input Methods/shanjie.app"
  exit 0
fi

# 3. Stop the running old copy, matched by its full path (regex characters in HOME escaped);
#    none running is fine.
PATTERN="$(printf '%s' "$HOME/Library/Input Methods/shanjie.app/Contents/MacOS/shanjie" | sed 's/[][\.*^$+?(){}|]/\\&/g')"
rc=0
pkill -f "$PATTERN" || rc=$?
[ "$rc" -eq 0 ] || [ "$rc" -eq 1 ] || fail "pkill failed ($rc)"

# 4. Register and enable both input modes, from the installed copy.
if ! "$HOME/Library/Input Methods/shanjie.app/Contents/MacOS/shanjie" install; then
  echo "error: registering the input method failed." >&2
  # Checked on disk, not by KEPT: an earlier run killed between the two renames also leaves one.
  if [ -d "$HOME/Library/Input Methods/.shanjie-previous" ]; then
    echo "To go back to the previous version:" >&2
    echo "  rm -rf ~/Library/Input\\ Methods/shanjie.app" >&2
    echo "  mv ~/Library/Input\\ Methods/.shanjie-previous ~/Library/Input\\ Methods/shanjie.app" >&2
    echo "  ~/Library/Input\\ Methods/shanjie.app/Contents/MacOS/shanjie install" >&2
  fi
  exit 1
fi

echo
echo "Next: open System Settings > Keyboard > Input Sources and check that 善解（標準）and"
echo "善解（倚天）are listed. If they are not, log out and log back in."
