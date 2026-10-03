#!/bin/bash
# Exercises install-ime.sh's file swap without touching the real system: HOME points at a temporary
# directory and SHANJIE_INSTALL_FILES_ONLY=1 stops before pkill and input source registration.
#
#   scripts/test-install-ime.sh <path to shanjie.app>
set -euo pipefail
[ "$#" -eq 1 ] && [ -x "$1/Contents/MacOS/shanjie" ] || { echo "usage: $0 <path to shanjie.app>" >&2; exit 2; }
APP="$(cd "$1" && pwd -P)"
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
T="$(mktemp -d)"
trap 'chflags -R nouchg "$T" 2>/dev/null || true; rm -rf "$T"' EXIT
IM="$T/Library/Input Methods"
run() { HOME="$T" SHANJIE_INSTALL_FILES_ONLY=1 "$ROOT/scripts/install-ime.sh" "$@"; }
check() { [ "$1" ] || { echo "FAIL: $2" >&2; exit 1; }; }
leftovers() { [ ! -e "$IM/.shanjie-staging" ] && [ ! -e "$IM/.shanjie-old" ]; }

# 1. Fresh install, then a reinstall over it: the bundle is in place, nothing is left behind.
run "$APP" >/dev/null
check "$(cmp -s "$APP/Contents/MacOS/shanjie" "$IM/shanjie.app/Contents/MacOS/shanjie" && echo y)" "fresh install did not place the bundle"
leftovers && check y "" || check "" "fresh install left a helper directory"
echo marker > "$IM/shanjie.app/Contents/old-marker"
run "$APP" >/dev/null
check "$([ ! -e "$IM/shanjie.app/Contents/old-marker" ] && echo y)" "reinstall did not replace the old bundle"
leftovers && check y "" || check "" "reinstall left a helper directory"

# 2. A failing copy (an unreadable source file) leaves the installed bundle untouched.
BAD="$T/bad/shanjie.app"; mkdir -p "$T/bad"; ditto "$APP" "$BAD"
echo marker > "$IM/shanjie.app/Contents/old-marker"
chmod 000 "$BAD/Contents/Resources/bigram.sjlm"
if run "$BAD" >/dev/null 2>&1; then check "" "install from an unreadable source succeeded"; fi
chmod 644 "$BAD/Contents/Resources/bigram.sjlm"
check "$([ -e "$IM/shanjie.app/Contents/old-marker" ] && echo y)" "a failed copy changed the installed bundle"
leftovers && check y "" || check "" "a failed copy left a helper directory"

# 3. An old bundle that cannot be deleted: the new bundle still goes in, the old one stays aside
#    with a warning, and the next install cleans it up once it is deletable.
chflags uchg "$IM/shanjie.app/Contents/old-marker"
out=$(run "$APP" 2>&1)
check "$([ ! -e "$IM/shanjie.app/Contents/old-marker" ] && echo y)" "the new bundle is not in place"
check "$(grep -q "could not delete the previous bundle" <<<"$out" && echo y)" "no warning about the undeletable old bundle"
chflags -R nouchg "$IM/.shanjie-old"
run "$APP" >/dev/null
leftovers && check y "" || check "" "the next install did not clean up .shanjie-old"

echo "install-ime.sh file swap: ok"
