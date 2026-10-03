#!/bin/bash
# Exercises install-ime.sh's file handling without touching the real system (docs/contracts/s3b.md
# sections 4 and 13.3): HOME points at a temporary directory and SHANJIE_INSTALL_FILES_ONLY=1, in
# which the script calls no lsregister, pkill or registration at all. That is checked, not assumed:
# SHANJIE_TEST_LSREGISTER and stand-ins for pkill and pgrep first on PATH record any call, and
# every case asserts none was made. The app is a stub whose executable only exits 1; it is never
# run.
#
#   scripts/test-install-ime.sh
set -euo pipefail

fail() { echo "FAIL: $*" >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
T="$(mktemp -d)"
trap 'chmod -R u+w "$T" 2>/dev/null; rm -rf "$T"' EXIT
APP=善解輸入法.app

# Stand-ins that only record their calls.
CALLS="$T/calls"
mkdir -p "$T/tools"
for tool in lsregister pkill pgrep; do
  printf '#!/bin/sh\necho "%s $*" >> "%s"\nexit 0\n' "$tool" "$CALLS" > "$T/tools/$tool"
  chmod +x "$T/tools/$tool"
done
no_calls() { [ ! -e "$CALLS" ] || fail "$1: the system was called: $(cat "$CALLS")"; }

stub() {  # stub <dir> <marker>: a minimal bundle whose executable refuses to run
  mkdir -p "$1/Contents/MacOS"
  printf '#!/bin/sh\nexit 1\n' > "$1/Contents/MacOS/shanjie"
  chmod +x "$1/Contents/MacOS/shanjie"
  echo "$2" > "$1/Contents/marker"
}
# run <home> <source> [files-only value]
run() {
  HOME="$1" SHANJIE_INSTALL_FILES_ONLY="${3-1}" SHANJIE_TEST_LSREGISTER="$T/tools/lsregister" \
    PATH="$T/tools:$PATH" "$ROOT/scripts/install-ime.sh" "$2"
}
marker() { cat "$1/Contents/marker"; }
no_staging() { ! ls -d "$1/Library/Input Methods"/.shanjie-staging-* >/dev/null 2>&1; }

stub "$T/v1/$APP" v1
stub "$T/v2/$APP" v2

# Guard: without the files-only hook, a HOME other than the account's real home is refused before
# anything is copied, registered or stopped. The HOME is read-only, so if the HOME check ever
# regressed the script would fail creating its directory, before reaching the real lsregister
# (SHANJIE_TEST_LSREGISTER is ignored outside files-only mode); the message proves which check
# stopped it.
mkdir "$T/ro"
chmod 500 "$T/ro"
if err=$(run "$T/ro" "$T/v1/$APP" "" 2>&1 >/dev/null); then fail "ran on a temporary HOME without the hook"; fi
grep -q "HOME is not this account's home directory" <<<"$err" || fail "the refusal did not come from the HOME check: $err"
[ -z "$(ls -A "$T/ro")" ] || fail "the refused run still created files"
no_calls refusal

H="$T/home"
IM="$H/Library/Input Methods"
mkdir "$H"

# 0. A failing copy on a fresh install leaves nothing installed (the staged copy may be partial).
stub "$T/bad0/$APP" bad0
chmod 000 "$T/bad0/$APP/Contents/marker"
if run "$H" "$T/bad0/$APP" >/dev/null 2>&1; then fail "fresh install from an unreadable source succeeded"; fi
chmod 644 "$T/bad0/$APP/Contents/marker"
[ ! -e "$IM/$APP" ] || fail "a failed fresh copy installed a partial bundle"
no_staging "$H" || fail "a failed fresh copy left a staging directory"
no_calls "failed fresh copy"

# 1. Fresh install: no previous version is kept.
run "$H" "$T/v1/$APP" >/dev/null
[ "$(marker "$IM/$APP")" = v1 ] || fail "fresh install did not place the bundle"
[ ! -e "$IM/.shanjie-previous" ] || fail "fresh install kept a previous version"
no_staging "$H" || fail "fresh install left a staging directory"
no_calls "fresh install"

# 2. Overwrite: the new bundle is in place and the old one is kept as .shanjie-previous.
run "$H" "$T/v2/$APP" >/dev/null
[ "$(marker "$IM/$APP")" = v2 ] || fail "reinstall did not replace the bundle"
[ "$(marker "$IM/.shanjie-previous")" = v1 ] || fail "the previous version was not kept"
no_staging "$H" || fail "reinstall left a staging directory"
no_calls overwrite

# 3. Reinstall from the installed copy itself: a real reinstall (the copy is staged first), so the
#    kept previous version is now that same v2.
run "$H" "$IM/$APP" >/dev/null
[ "$(marker "$IM/$APP")" = v2 ] || fail "reinstalling the installed copy broke it"
[ "$(marker "$IM/.shanjie-previous")" = v2 ] || fail "reinstalling the installed copy did not run"
no_calls "reinstall from the installed copy"

# 4. A failing copy (an unreadable file in the source) stops at ditto and leaves everything as is.
stub "$T/bad/$APP" bad
chmod 000 "$T/bad/$APP/Contents/marker"
if err=$(run "$H" "$T/bad/$APP" 2>&1 >/dev/null); then fail "install from an unreadable source succeeded"; fi
chmod 644 "$T/bad/$APP/Contents/marker"
grep -q ditto <<<"$err" || fail "the failure did not come from the copy: $err"
[ "$(marker "$IM/$APP")" = v2 ] || fail "a failed copy changed the installed bundle"
[ "$(marker "$IM/.shanjie-previous")" = v2 ] || fail "a failed copy changed the kept previous version"
no_staging "$H" || fail "a failed copy left a staging directory"
no_calls "failed copy"

# 5. Upgrade from a version before section 13, installed only as shanjie.app: it becomes the kept
#    previous version and only 善解輸入法.app is left.
H="$T/legacy"
IM="$H/Library/Input Methods"
stub "$IM/shanjie.app" old
run "$H" "$T/v2/$APP" >/dev/null
[ "$(marker "$IM/$APP")" = v2 ] || fail "legacy upgrade did not place the bundle"
[ "$(marker "$IM/.shanjie-previous")" = old ] || fail "legacy upgrade did not keep shanjie.app as the previous version"
[ ! -e "$IM/shanjie.app" ] || fail "legacy upgrade left shanjie.app"
no_staging "$H" || fail "legacy upgrade left a staging directory"
no_calls "legacy upgrade"

# 6. Both names installed (and an older kept version): the 善解輸入法.app copy is the previous
#    version, shanjie.app is removed, even when read-only.
H="$T/both"
IM="$H/Library/Input Methods"
stub "$IM/shanjie.app" old
stub "$IM/$APP" v1
stub "$IM/.shanjie-previous" older
chmod -R a-w "$IM/shanjie.app"
run "$H" "$T/v2/$APP" >/dev/null
[ "$(marker "$IM/$APP")" = v2 ] || fail "upgrade with both names did not place the bundle"
[ "$(marker "$IM/.shanjie-previous")" = v1 ] || fail "upgrade with both names did not keep 善解輸入法.app as the previous version"
[ ! -e "$IM/shanjie.app" ] || fail "upgrade with both names left shanjie.app"
[ "$(ls -A "$IM" | wc -l)" -eq 2 ] || fail "unexpected files: $(ls -A "$IM")"
no_calls "upgrade with both names"


# 7. Legacy upgrade where moving shanjie.app aside fails (a mv stand-in refuses it): the new bundle
#    must not be put next to the legacy one, and the legacy one stays where it was.
H="$T/legacy-mv"
IM="$H/Library/Input Methods"
stub "$IM/shanjie.app" old
cat > "$T/tools/mv" <<'MV'
#!/bin/bash
case "$1" in */shanjie.app) exit 1 ;; esac
exec /bin/mv "$@"
MV
chmod +x "$T/tools/mv"
if run "$H" "$T/v2/$APP" >/dev/null 2>&1; then fail "a failed move of shanjie.app was not reported"; fi
rm -f "$T/tools/mv"
[ ! -e "$IM/$APP" ] || fail "a failed legacy move put the new bundle next to shanjie.app"
[ "$(marker "$IM/shanjie.app")" = old ] || fail "a failed legacy move changed shanjie.app"
no_staging "$H" || fail "a failed legacy move left a staging directory"
no_calls "failed legacy move"

# 8. Files-only and skip-register together (the installer's variable must not defeat the test
#    hook): files-only still decides, so the run ends at the files-only line, before step 3.
H="$T/both-flags"
IM="$H/Library/Input Methods"
mkdir -p "$H"
out=$(SHANJIE_INSTALL_SKIP_REGISTER=1 run "$H" "$T/v1/$APP")
grep -q '^files only: installed to ' <<<"$out" || fail "skip-register changed the files-only exit: $out"
! grep -q 'registration skipped' <<<"$out" || fail "skip-register ran past the files-only hook: $out"
[ "$(marker "$IM/$APP")" = v1 ] || fail "files-only with skip-register did not install"
no_calls "files-only with skip-register"

echo "install-ime.sh file handling: ok"
