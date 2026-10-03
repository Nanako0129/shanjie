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

# Paths this script removes or moves: the installed bundle, staging directories and the kept
# previous version, all inside ~/Library/Input Methods. The helper directory names do not end in
# .app (the staging directory does contain a shanjie.app while the copy runs).
mkdir -p "$HOME/Library/Input Methods"
rm -rf "$HOME/Library/Input Methods"/.shanjie-staging-*   # left by an earlier run that was killed

# 1. Copy the new bundle to a fresh staging directory next to the destination. A failed copy
#    leaves the installed bundle untouched.
STAGE="$(mktemp -d "$HOME/Library/Input Methods/.shanjie-staging-XXXXXX")"
SWAPPING=
KEPT=
cleanup() {
  # Only if the script stops between setting the old bundle aside and moving the new one in: put
  # the new one in place, so the user is not left without an installed copy. Never on a failed
  # copy, where the staged bundle may be incomplete.
  if [ -n "$SWAPPING" ] && [ ! -e "$HOME/Library/Input Methods/shanjie.app" ] && [ -d "$STAGE/shanjie.app" ]; then
    mv "$STAGE/shanjie.app" "$HOME/Library/Input Methods/shanjie.app" || true
  fi
  rm -rf "$STAGE"
}
trap cleanup EXIT
ditto "$SRC" "$STAGE/shanjie.app"

# 2. Keep the previous version as ~/Library/Input Methods/.shanjie-previous (replacing an older one
#    kept there), then move the new bundle into place. The old bundle is unregistered from
#    LaunchServices just before it is moved, and the new one is registered in step 3, so the system
#    resolves the bundle ID to the installed copy. (`shanjie install` skips TIS registration when
#    the bundle ID is already known, so it would not do this on an upgrade.) Not measured on a real
#    system yet; the user's install test checks which copy runs.
if [ -e "$HOME/Library/Input Methods/shanjie.app" ] || [ -L "$HOME/Library/Input Methods/shanjie.app" ]; then
  rm -rf "$HOME/Library/Input Methods/.shanjie-previous"
  SWAPPING=1
  "$LSREGISTER" -u "$HOME/Library/Input Methods/shanjie.app" 2>/dev/null || true
  mv "$HOME/Library/Input Methods/shanjie.app" "$HOME/Library/Input Methods/.shanjie-previous"
  KEPT=1
fi
mv "$STAGE/shanjie.app" "$HOME/Library/Input Methods/shanjie.app"
SWAPPING=
[ "$KEPT" = 1 ] && echo "previous version kept at ~/Library/Input Methods/.shanjie-previous"

# Test hook (scripts/test-install-ime.sh): stop after the file swap, before touching running
# processes or the input source registry.
if [ "${SHANJIE_INSTALL_FILES_ONLY:-}" = 1 ]; then
  echo "files only: installed to $HOME/Library/Input Methods/shanjie.app"
  exit 0
fi

# 3. Register the new bundle with LaunchServices (after the test hook: tests must not register
#    anything), then stop the running old copy, matched by its full path (regex characters in HOME
#    escaped); none running is fine.
"$LSREGISTER" -f "$HOME/Library/Input Methods/shanjie.app" 2>/dev/null \
  || echo "warning: lsregister -f failed; log out and back in if the old version keeps running" >&2
REAL_HOME="$(cd "$HOME" && pwd -P)"
PATTERN="^$(printf '%s' "$REAL_HOME/Library/Input Methods/shanjie.app/Contents/MacOS/shanjie" | sed 's/[][\.*^$+?(){}|]/\\&/g')( |\$)"
rc=0
pkill -f "$PATTERN" || rc=$?
[ "$rc" -eq 0 ] || [ "$rc" -eq 1 ] || fail "pkill failed ($rc)"
# Wait for it to exit, so the new process can take over the input method's connection name.
for _ in 1 2 3 4 5 6 7 8 9 10; do pgrep -f "$PATTERN" >/dev/null || break; sleep 0.5; done

# 4. Register and enable both input modes, from the installed copy.
if ! "$HOME/Library/Input Methods/shanjie.app/Contents/MacOS/shanjie" install; then
  echo "error: registering the input method failed." >&2
  # Checked on disk, not by KEPT: an earlier run killed between the two renames also leaves one.
  if [ -d "$HOME/Library/Input Methods/.shanjie-previous" ]; then
    echo "To go back to the previous version:" >&2
    echo "  pkill -f 'Input Methods/shanjie.app/Contents/MacOS/shanjie'" >&2
    echo "  rm -rf ~/Library/Input\\ Methods/shanjie.app" >&2
    echo "  mv ~/Library/Input\\ Methods/.shanjie-previous ~/Library/Input\\ Methods/shanjie.app" >&2
    echo "  $LSREGISTER -f ~/Library/Input\\ Methods/shanjie.app" >&2
    echo "  ~/Library/Input\\ Methods/shanjie.app/Contents/MacOS/shanjie install" >&2
  fi
  exit 1
fi

echo
echo "Next: open System Settings > Keyboard > Input Sources and check that 善解（標準）and"
echo "善解（倚天）are listed. If they are not, log out and log back in."
