#!/bin/bash
# Install 善解輸入法.app as the current user's input method (docs/contracts/s3b.md sections 4 and
# 13.3). Run by the user. Agents and CI only run it through scripts/test-install-ime.sh, with HOME
# on a temporary directory, a stub app and SHANJIE_INSTALL_FILES_ONLY=1. No sudo: it writes only to
# the user's ~/Library/Input Methods. Do not run two installs at the same time.
#
#   scripts/install-ime.sh <path to 善解輸入法.app or 善解（開發版）.app>
#
# The argument is usually the unzipped release asset; a local build works too. The destination
# follows the bundle's CFBundleIdentifier (docs/contracts/app-sandbox.md section 2.3):
#   com.nyanako.inputmethod.shanjie      善解輸入法.app, previous kept as .shanjie-previous; versions
#                                        before section 13 were installed as shanjie.app, and that
#                                        copy is upgraded too
#   com.nyanako.inputmethod.shanjie.dev  善解（開發版）.app (the default local build), previous kept as
#                                        .shanjie-dev-previous; never touches the shipping copy
#   anything else                        refused
set -euo pipefail

fail() { echo "error: $*" >&2; exit 1; }
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister

[ -n "${HOME:-}" ] || fail "HOME is empty"
[ "$(id -u)" -ne 0 ] || fail "do not run this with sudo or as root"
[ "$#" -eq 1 ] || fail "usage: $0 <path to 善解輸入法.app>"
SRC="$1"
[ -d "$SRC" ] && [ -x "$SRC/Contents/MacOS/shanjie" ] || fail "$SRC is not a 善解輸入法.app"
SHIPPING_ID=com.nyanako.inputmethod.shanjie
ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$SRC/Contents/Info.plist" 2>/dev/null || true)"
IM="$HOME/Library/Input Methods"
case "$ID" in
  "$SHIPPING_ID")     NAME=善解輸入法 LEGACY="$IM/shanjie.app" PREV="$IM/.shanjie-previous" ;;
  "$SHIPPING_ID.dev") NAME=善解（開發版） LEGACY= PREV="$IM/.shanjie-dev-previous" ;;
  *) fail "$SRC has bundle ID '$ID'; only $SHIPPING_ID and $SHIPPING_ID.dev are installed" ;;
esac
DEST="$IM/$NAME.app"

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
# bundle (DEST), the bundle under its name before section 13 (LEGACY, shipping ID only; empty for
# the development ID, and `[ -e "" ]` is false), staging directories and the kept previous version
# (PREV). The helper directory names do not end in .app (the staging directory does contain a
# $NAME.app while the copy runs).
mkdir -p "$IM"
# A run killed outright (SIGKILL, power loss) can leave a .shanjie-staging-* behind; it is not
# removed automatically, because a concurrent run's live staging directory looks the same.

# 1. Copy the new bundle to a fresh staging directory next to the destination. A failed copy
#    leaves the installed bundle untouched.
STAGE="$(mktemp -d "$IM/.shanjie-staging-XXXXXX")"
MOVED=
KEPT=
cleanup() {
  # Only if the script stops after the old bundle was actually moved aside (MOVED, set once that
  # mv succeeded) but before the new one moved in: put the new one in place, so the user is not
  # left without an installed copy. Never on a failed copy, where the staged bundle may be
  # incomplete; and not keyed on "$DEST is missing", which is also true when only the legacy
  # shanjie.app was installed.
  if [ -n "$MOVED" ] && [ ! -e "$DEST" ] && [ -d "$STAGE/$NAME.app" ]; then
    mv "$STAGE/$NAME.app" "$DEST" || true
    # Not "rerun the script": that would keep this version as the previous one and drop the real one.
    echo "interrupted: the new version is in place but not registered. Run:" >&2
    echo "  $LSREGISTER -f ~/Library/Input\\ Methods/$NAME.app" >&2
    echo "  ~/Library/Input\\ Methods/$NAME.app/Contents/MacOS/shanjie install" >&2
  fi
  rm -rf "$STAGE"
}
trap cleanup EXIT
ditto "$SRC" "$STAGE/$NAME.app"

# 2. Keep the previous version as PREV (replacing an older one kept there), then move the new
#    bundle into place. The previous version is the installed DEST, or, when only the legacy
#    shanjie.app is installed (shipping ID), that one. It is moved aside first and then unregistered
#    from LaunchServices, and the new one is registered in step 3, so the system resolves the
#    bundle ID to the installed copy. Not measured on a real system yet; the user's install test
#    checks which copy runs.
OLD=
if [ -e "$DEST" ]; then OLD="$DEST"; elif [ -e "$LEGACY" ]; then OLD="$LEGACY"; fi
if [ -n "$OLD" ]; then
  if [ -e "$PREV" ]; then
    chmod -R u+w "$PREV"   # a read-only directory must not block upgrades
    rm -rf "$PREV"
  fi
  # Move first, unregister after: if the move fails, the old bundle stays registered where it is.
  mv "$OLD" "$PREV"
  MOVED=1
  unregister "$PREV"
  KEPT=1
fi
mv "$STAGE/$NAME.app" "$DEST"
MOVED=
[ "$KEPT" = 1 ] && echo "previous version kept at ~/Library/Input Methods/$(basename "$PREV")"
# Both names were installed: the new-name copy was kept above, so the legacy one is only removed.
# A failure here only warns: the new bundle is already in place and must still be registered.
if [ -e "$LEGACY" ]; then
  unregister "$LEGACY"
  if chmod -R u+w "$LEGACY" && rm -rf "$LEGACY"; then
    echo "removed the earlier shanjie.app"
  else
    echo "warning: could not delete ~/Library/Input Methods/shanjie.app; delete it by hand" >&2
  fi
fi

# Test hook (scripts/test-install-ime.sh): stop after the file swap, before touching running
# processes or the input source registry.
if [ "$FILES_ONLY" = 1 ]; then
  echo "files only: installed to $DEST"
  exit 0
fi

# 3. Register the new bundle with LaunchServices (after the test hook: tests must not register
#    anything), then stop the running old copy, matched by its full path (regex characters in HOME
#    escaped): for the shipping ID under either name, for the development ID only its own folder,
#    so installing a development build never stops the shipping copy. None running is fine.
"$LSREGISTER" -f "$DEST" 2>/dev/null \
  || echo "warning: lsregister -f failed; log out and back in if the old version keeps running" >&2
REAL_HOME="$(cd "$HOME" && pwd -P)"
NAMES='(善解輸入法|shanjie)'
[ "$ID" = "$SHIPPING_ID" ] || NAMES="$NAME"   # full-width parentheses are not regex characters
PATTERN="^$(printf '%s' "$REAL_HOME/Library/Input Methods/" | sed 's/[][\.*^$+?(){}|]/\\&/g')$NAMES\\.app/Contents/MacOS/shanjie( |\$)"
rc=0
pkill -f "$PATTERN" || rc=$?
[ "$rc" -eq 0 ] || [ "$rc" -eq 1 ] || fail "pkill failed ($rc)"
# Wait for it to exit, so the new process can take over the input method's connection name.
for _ in 1 2 3 4 5 6 7 8 9 10; do pgrep -f "$PATTERN" >/dev/null || break; sleep 0.5; done
pgrep -f "$PATTERN" >/dev/null \
  && echo "warning: a shanjie process is still running after 5 s; if the old version keeps serving input, log out and back in" >&2

# The installer app (docs/contracts/s3c-installer.md section 4) registers and enables in its own
# process, so it stops here, after the file swap, lsregister -f and stopping the old process.
# Files-only mode exits earlier, before step 3, so it takes precedence.
if [ "${SHANJIE_INSTALL_SKIP_REGISTER:-}" = 1 ]; then
  echo "files and processes done; registration skipped"
  exit 0
fi

# 4. Register, enable the input mode and disable the old two modes, from the installed copy. The
#    exit code is kept, not swallowed: 3 means the system has not accepted the input method yet
#    (add it in System Settings > Keyboard > Input Sources) or has not loaded the new input mode
#    list. installer-v2.md section 9: `install` registers only when the system does not know the
#    mode yet, so an update of an enabled copy changes nothing and prints "registration skipped".
rc=0
"$DEST/Contents/MacOS/shanjie" install || rc=$?
if [ "$rc" -eq 3 ]; then
  # Not accepted yet is fixed in System Settings, not by running anything again; rerunning the
  # whole script would keep the version just installed as the previous one and delete the real
  # previous version.
  echo "系統還沒接受新的輸入法：請打開「系統設定 → 鍵盤 → 輸入方式」，按「+」，選「繁體中文」裡的「${NAME}」，按「加入」。" >&2
  exit 3
fi
if [ "$rc" -ne 0 ]; then
  echo "error: registering the input method failed." >&2
  # Checked on disk, not by KEPT: an earlier run killed between the two renames also leaves one.
  if [ -d "$PREV" ] && grep -q '\.standard<' "$PREV/Contents/Info.plist" 2>/dev/null; then
    # The kept version is a two-mode build from before section 13; its own install skips
    # registration when the bundle ID is known, so TIS must reload the bundle after a log out.
    echo "The previous version is the earlier two-mode build. To go back to it:" >&2
    echo "  rm -rf ~/Library/Input\\ Methods/善解輸入法.app" >&2
    echo "  mv ~/Library/Input\\ Methods/.shanjie-previous ~/Library/Input\\ Methods/shanjie.app" >&2
    echo "  $LSREGISTER -f ~/Library/Input\\ Methods/shanjie.app" >&2
    echo "  then log out and back in, and add 善解（標準） or 善解（倚天） in System Settings > Keyboard > Input Sources" >&2
  elif [ -d "$PREV" ]; then
    echo "To go back to the previous version:" >&2
    echo "  rm -rf ~/Library/Input\\ Methods/$NAME.app" >&2
    echo "  mv ~/Library/Input\\ Methods/$(basename "$PREV") ~/Library/Input\\ Methods/$NAME.app" >&2
    echo "  $LSREGISTER -f ~/Library/Input\\ Methods/$NAME.app" >&2
    echo "  pkill -f '$PATTERN'" >&2
    echo "  ~/Library/Input\\ Methods/$NAME.app/Contents/MacOS/shanjie install" >&2
  fi
  # app-sandbox.md section 2.5: the first sandboxed run moved the data, so an older version without
  # the sandbox starts empty.
  if [ -d "$PREV" ] && [ "$ID" = "$SHIPPING_ID" ]; then
    echo "If the previous version is one without the App Sandbox (0.4.x or earlier), it starts with no" >&2
    echo "learned words and default settings: the sandboxed version moved them into" >&2
    echo "~/Library/Containers/$SHIPPING_ID on its first run. What the older version learns stays in the" >&2
    echo "old place and is not moved again on the next upgrade. To move the data back:" >&2
    echo "https://github.com/Nanako0129/shanjie/blob/main/docs/sandbox-restore.md" >&2
  fi
  exit 1
fi

echo
echo "Next: if $NAME is not in System Settings > Keyboard > Input Sources yet, open it, press +,"
echo "choose $NAME under 繁體中文 and press 加入. Nothing to do if it is already there."
