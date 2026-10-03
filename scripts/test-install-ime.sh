#!/bin/bash
# Exercises install-ime.sh's file handling without touching the real system: HOME points at a
# temporary directory, SHANJIE_INSTALL_FILES_ONLY=1 stops before pkill and registration, and the
# app is a stub whose executable only exits 1, so even if the hook stopped working nothing could be
# registered. (lsregister -u on the stub's temporary path is the one system call it makes; it only
# removes a registration, and the stub was never registered.)
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
marker() { cat "$1/Contents/marker"; }
no_staging() { ! ls -d "$IM"/.shanjie-staging-* >/dev/null 2>&1; }

stub "$T/v1/shanjie.app" v1
stub "$T/v2/shanjie.app" v2

# 1. Fresh install: no previous version is kept.
run "$T/v1/shanjie.app" >/dev/null
[ "$(marker "$IM/shanjie.app")" = v1 ] || fail "fresh install did not place the bundle"
[ ! -e "$IM/.shanjie-previous" ] || fail "fresh install kept a previous version"
no_staging || fail "fresh install left a staging directory"

# 2. Reinstall: the new bundle is in place and the old one is kept as .shanjie-previous.
run "$T/v2/shanjie.app" >/dev/null
[ "$(marker "$IM/shanjie.app")" = v2 ] || fail "reinstall did not replace the bundle"
[ "$(marker "$IM/.shanjie-previous")" = v1 ] || fail "the previous version was not kept"
no_staging || fail "reinstall left a staging directory"

# 3. Reinstall from the installed copy itself: a real reinstall (the copy is staged first), so the
#    kept previous version is now that same v2.
run "$IM/shanjie.app" >/dev/null
[ "$(marker "$IM/shanjie.app")" = v2 ] || fail "reinstalling the installed copy broke it"
[ "$(marker "$IM/.shanjie-previous")" = v2 ] || fail "reinstalling the installed copy did not run"

# 4. A failing copy (an unreadable file in the source) stops at ditto and leaves everything as is.
stub "$T/bad/shanjie.app" bad
chmod 000 "$T/bad/shanjie.app/Contents/marker"
if err=$(run "$T/bad/shanjie.app" 2>&1 >/dev/null); then fail "install from an unreadable source succeeded"; fi
chmod 644 "$T/bad/shanjie.app/Contents/marker"
grep -q ditto <<<"$err" || fail "the failure did not come from the copy: $err"
[ "$(marker "$IM/shanjie.app")" = v2 ] || fail "a failed copy changed the installed bundle"
[ "$(marker "$IM/.shanjie-previous")" = v2 ] || fail "a failed copy changed the kept previous version"
no_staging || fail "a failed copy left a staging directory"

echo "install-ime.sh file handling: ok"
