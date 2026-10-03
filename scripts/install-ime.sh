#!/bin/bash
# Install 善解輸入法.app as the current user's input method (docs/contracts/s3b.md sections 4 and
# 13.3). Run by the user. Agents and CI only run it through scripts/test-install-ime.sh, with HOME
# on a temporary directory, a stub app and SHANJIE_INSTALL_FILES_ONLY=1. No sudo: it writes only to
# the user's ~/Library/Input Methods. Do not run two installs at the same time.
#
#   scripts/install-ime.sh <path to 善解輸入法.app>
#
# The argument is usually the unzipped release asset; a local build/善解輸入法.app (ad-hoc signed)
# works too. Versions before section 13 were installed as shanjie.app; that copy is upgraded too.
set -euo pipefail

fail() { echo "error: $*" >&2; exit 1; }
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister

[ -n "${HOME:-}" ] || fail "HOME is empty"
[ "$(id -u)" -ne 0 ] || fail "do not run this with sudo or as root"
[ "$#" -eq 1 ] || fail "usage: $0 <path to 善解輸入法.app>"
SRC="$1"
[ -d "$SRC" ] && [ -x "$SRC/Contents/MacOS/shanjie" ] || fail "$SRC is not a 善解輸入法.app"

# Only a real install may touch the system (LaunchServices, running processes, the input source
# registry). With HOME pointed anywhere other than this account's home directory, as the tests do,
# the script refuses unless SHANJIE_INSTALL_FILES_ONLY=1, which stops before any of that. A stray
# registration of an unsigned bundle makes macOS show "shanjie.app is damaged" dialogs on the
# user's screen (seen 2026-10-04, when a review agent ran a copied binary inside such a bundle).
FILES_ONLY="${SHANJIE_INSTALL_FILES_ONLY:-}"
ACCOUNT_HOME="$(dscl . -read "/Users/$(id -un)" NFSHomeDirectory 2>/dev/null | awk '{print $2}')"
if [ "$FILES_ONLY" != 1 ]; then
  [ -n "$ACCOUNT_HOME" ] && [ "$(cd "$HOME" && pwd -P)" = "$(cd "$ACCOUNT_HOME" && pwd -P)" ] \
    || fail "HOME is not this account's home directory; refusing to install (tests set SHANJIE_INSTALL_FILES_ONLY=1)"
else
  # Tests point this at a stand-in that only records calls, as a tripwire: files-only mode must
  # never call lsregister. Ignored outside files-only mode.
  LSREGISTER="${SHANJIE_TEST_LSREGISTER:-$LSREGISTER}"
fi
unregister() { [ "$FILES_ONLY" = 1 ] || "$LSREGISTER" -u "$1" 2>/dev/null || true; }

# The only paths this script removes or moves, all inside ~/Library/Input Methods: the installed
# bundle, the bundle under its name before section 13, staging directories and the kept previous
# version. The helper directory names do not end in .app (the staging directory does contain a
# 善解輸入法.app while the copy runs).
DEST="$HOME/Library/Input Methods/善解輸入法.app"
LEGACY="$HOME/Library/Input Methods/shanjie.app"
PREV="$HOME/Library/Input Methods/.shanjie-previous"
mkdir -p "$HOME/Library/Input Methods"
# A run killed outright (SIGKILL, power loss) can leave a .shanjie-staging-* behind; it is not
# removed automatically, because a concurrent run's live staging directory looks the same.

# 1. Copy the new bundle to a fresh staging directory next to the destination. A failed copy
#    leaves the installed bundle untouched.
STAGE="$(mktemp -d "$HOME/Library/Input Methods/.shanjie-staging-XXXXXX")"
SWAPPING=
KEPT=
cleanup() {
  # Only if the script stops between setting the old bundle aside and moving the new one in: put
  # the new one in place, so the user is not left without an installed copy. Never on a failed
  # copy, where the staged bundle may be incomplete.
  if [ -n "$SWAPPING" ] && [ ! -e "$DEST" ] && [ -d "$STAGE/善解輸入法.app" ]; then
    mv "$STAGE/善解輸入法.app" "$DEST" || true
    echo "interrupted: the new version was moved into place but not registered; run this script again" >&2
  fi
  rm -rf "$STAGE"
}
trap cleanup EXIT
ditto "$SRC" "$STAGE/善解輸入法.app"

# 2. Keep the previous version as .shanjie-previous (replacing an older one kept there), then move
#    the new bundle into place. The previous version is the installed 善解輸入法.app, or, when only
#    the legacy shanjie.app is installed, that one. It is unregistered from LaunchServices just
#    before it is moved, and the new one is registered in step 3, so the system resolves the
#    bundle ID to the installed copy. Not measured on a real system yet; the user's install test
#    checks which copy runs.
OLD=
if [ -e "$DEST" ]; then OLD="$DEST"; elif [ -e "$LEGACY" ]; then OLD="$LEGACY"; fi
if [ -n "$OLD" ]; then
  if [ -e "$PREV" ]; then
    chmod -R u+w "$PREV"   # a read-only directory must not block upgrades
    rm -rf "$PREV"
  fi
  SWAPPING=1
  unregister "$OLD"
  mv "$OLD" "$PREV"
  KEPT=1
fi
mv "$STAGE/善解輸入法.app" "$DEST"
SWAPPING=
[ "$KEPT" = 1 ] && echo "previous version kept at ~/Library/Input Methods/.shanjie-previous"
# Both names were installed: the new-name copy was kept above, so the legacy one is only removed.
if [ -e "$LEGACY" ]; then
  unregister "$LEGACY"
  chmod -R u+w "$LEGACY"
  rm -rf "$LEGACY"
  echo "removed the earlier shanjie.app"
fi

# Test hook (scripts/test-install-ime.sh): stop after the file swap, before touching running
# processes or the input source registry.
if [ "$FILES_ONLY" = 1 ]; then
  echo "files only: installed to $DEST"
  exit 0
fi

# 3. Register the new bundle with LaunchServices (after the test hook: tests must not register
#    anything), then stop the running old copy under either name, matched by its full path (regex
#    characters in HOME escaped); none running is fine.
"$LSREGISTER" -f "$DEST" 2>/dev/null \
  || echo "warning: lsregister -f failed; log out and back in if the old version keeps running" >&2
REAL_HOME="$(cd "$HOME" && pwd -P)"
PATTERN="^$(printf '%s' "$REAL_HOME/Library/Input Methods/" | sed 's/[][\.*^$+?(){}|]/\\&/g')(善解輸入法|shanjie)\\.app/Contents/MacOS/shanjie( |\$)"
rc=0
pkill -f "$PATTERN" || rc=$?
[ "$rc" -eq 0 ] || [ "$rc" -eq 1 ] || fail "pkill failed ($rc)"
# Wait for it to exit, so the new process can take over the input method's connection name.
for _ in 1 2 3 4 5 6 7 8 9 10; do pgrep -f "$PATTERN" >/dev/null || break; sleep 0.5; done
pgrep -f "$PATTERN" >/dev/null \
  && echo "warning: a shanjie process is still running after 5 s; if the old version keeps serving input, log out and back in" >&2

# 4. Register, enable the input mode and disable the old two modes, from the installed copy. The
#    exit code is kept, not swallowed: 3 means the system has not loaded the new input mode list.
rc=0
"$DEST/Contents/MacOS/shanjie" install || rc=$?
if [ "$rc" -eq 3 ]; then
  # Rerunning the whole script would keep the version just installed as the previous one and
  # delete the real previous version, so only `install` is to be run again.
  echo "系統還沒載入新的輸入方式清單：請登出再登入，然後只執行 ~/Library/Input\\ Methods/善解輸入法.app/Contents/MacOS/shanjie install" >&2
  exit 3
fi
if [ "$rc" -ne 0 ]; then
  echo "error: registering the input method failed." >&2
  # Checked on disk, not by KEPT: an earlier run killed between the two renames also leaves one.
  if [ -d "$PREV" ]; then
    echo "To go back to the previous version:" >&2
    echo "  rm -rf ~/Library/Input\\ Methods/善解輸入法.app" >&2
    echo "  mv ~/Library/Input\\ Methods/.shanjie-previous ~/Library/Input\\ Methods/善解輸入法.app" >&2
    echo "  $LSREGISTER -f ~/Library/Input\\ Methods/善解輸入法.app" >&2
    echo "  pkill -f '$PATTERN'" >&2
    echo "  ~/Library/Input\\ Methods/善解輸入法.app/Contents/MacOS/shanjie install" >&2
  fi
  exit 1
fi

echo
echo "Next: open System Settings > Keyboard > Input Sources and check that 善解輸入法 is listed."
echo "If it is not, log out and log back in."
