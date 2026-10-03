#!/bin/bash
# Exercises install-ime.sh's file handling without touching the real system: HOME points at a
# temporary directory, SHANJIE_INSTALL_FILES_ONLY=1 stops before pkill and registration, and the
# app is a stub whose executable only exits 1, so even if the hook stopped working nothing could be
# registered.
#
#   scripts/test-install-ime.sh
set -euo pipefail

fail() { echo "FAIL: $*" >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
IM="$T/Library/Input Methods"

stub() {  # stub <dir> <marker>: a minimal shanjie.app whose executable refuses to run
  mkdir -p "$1/Contents/MacOS"
  printf '#!/bin/sh\nexit 1\n' > "$1/Contents/MacOS/shanjie"
  chmod +x "$1/Contents/MacOS/shanjie"
  echo "$2" > "$1/Contents/marker"
}
run() { HOME="$T" SHANJIE_INSTALL_FILES_ONLY=1 "$ROOT/scripts/install-ime.sh" "$1"; }

stub "$T/v1/shanjie.app" v1
stub "$T/v2/shanjie.app" v2

# 1. Fresh install.
run "$T/v1/shanjie.app" >/dev/null
[ "$(cat "$IM/shanjie.app/Contents/marker")" = v1 ] || fail "fresh install did not place the bundle"
! ls -d "$IM"/.shanjie-staging-* >/dev/null 2>&1 || fail "fresh install left a staging directory"

# 2. Reinstall over it: the new bundle is in place and the old one is in the Trash, not deleted.
run "$T/v2/shanjie.app" >/dev/null
[ "$(cat "$IM/shanjie.app/Contents/marker")" = v2 ] || fail "reinstall did not replace the bundle"
ls "$T/.Trash"/shanjie-*.app/Contents/marker >/dev/null 2>&1 || fail "the previous bundle is not in the Trash"
[ "$(cat "$T/.Trash"/shanjie-*.app/Contents/marker)" = v1 ] || fail "the Trash holds the wrong bundle"
! ls -d "$IM"/.shanjie-staging-* >/dev/null 2>&1 || fail "reinstall left a staging directory"

# 3. Reinstalling from the installed copy itself works (the copy is staged first).
run "$IM/shanjie.app" >/dev/null
[ "$(cat "$IM/shanjie.app/Contents/marker")" = v2 ] || fail "reinstalling the installed copy broke it"

# 4. A failing copy (an unreadable file in the source) leaves the installed bundle untouched.
stub "$T/bad/shanjie.app" bad
chmod 000 "$T/bad/shanjie.app/Contents/marker"
if run "$T/bad/shanjie.app" >/dev/null 2>&1; then fail "install from an unreadable source succeeded"; fi
chmod 644 "$T/bad/shanjie.app/Contents/marker"
[ "$(cat "$IM/shanjie.app/Contents/marker")" = v2 ] || fail "a failed copy changed the installed bundle"
! ls -d "$IM"/.shanjie-staging-* >/dev/null 2>&1 || fail "a failed copy left a staging directory"

echo "install-ime.sh file handling: ok"
