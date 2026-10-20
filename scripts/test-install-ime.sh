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
DEV=善解（開發版）.app
SHIPPING_ID=com.nyanako.inputmethod.shanjie

# Stand-ins that only record their calls.
CALLS="$T/calls"
mkdir -p "$T/tools"
for tool in lsregister pkill pgrep; do
  printf '#!/bin/sh\necho "%s $*" >> "%s"\nexit 0\n' "$tool" "$CALLS" > "$T/tools/$tool"
  chmod +x "$T/tools/$tool"
done
no_calls() { [ ! -e "$CALLS" ] || fail "$1: the system was called: $(cat "$CALLS")"; }

# stub <dir> <marker> [bundle ID, default the shipping ID]: a minimal bundle whose executable
# refuses to run; install-ime.sh picks the destination from the ID (app-sandbox.md section 2.3).
stub() {
  mkdir -p "$1/Contents/MacOS"
  printf '#!/bin/sh\nexit 1\n' > "$1/Contents/MacOS/shanjie"
  chmod +x "$1/Contents/MacOS/shanjie"
  echo "$2" > "$1/Contents/marker"
  plutil -create xml1 "$1/Contents/Info.plist"
  plutil -insert CFBundleIdentifier -string "${3:-$SHIPPING_ID}" "$1/Contents/Info.plist"
}
# run <home> <source> [files-only value]. SYS is the stand-in for /Library/Input Methods (system-install.md), empty
# unless a case puts a copy there; install-ime.sh honours it only in files-only mode.
SYS="$T/system/Input Methods"
mkdir -p "$SYS"
run() {
  HOME="$1" SHANJIE_INSTALL_FILES_ONLY="${3-1}" SHANJIE_TEST_LSREGISTER="$T/tools/lsregister" SHANJIE_TEST_SYSTEM_IM="$SYS" \
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
# stopped it. A development-ID stub, so the shipping ID's signature check (case 12) does not stop it first.
mkdir "$T/ro"
chmod 500 "$T/ro"
stub "$T/guard/$DEV" guard "$SHIPPING_ID.dev"
if err=$(run "$T/ro" "$T/guard/$DEV" "" 2>&1 >/dev/null); then fail "ran on a temporary HOME without the hook"; fi
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

# 9. app-sandbox.md section 2.3: a development build next to an installed shipping copy goes to
#    善解（開發版）.app and keeps its own previous version; the shipping copy and its kept previous
#    version stay byte for byte as they were.
H="$T/dev"
IM="$H/Library/Input Methods"
stub "$IM/$APP" ship
stub "$IM/.shanjie-previous" ship-old
stub "$IM/shanjie.app" legacy
SNAP() { (cd "$IM" && find "$APP" .shanjie-previous shanjie.app -type f -exec shasum -a 256 {} + | sort); }
before=$(SNAP)
stub "$T/dev1/$DEV" dev1 "$SHIPPING_ID.dev"
stub "$T/dev2/$DEV" dev2 "$SHIPPING_ID.dev"
run "$H" "$T/dev1/$DEV" >/dev/null
[ "$(marker "$IM/$DEV")" = dev1 ] || fail "the development build was not installed as $DEV"
[ ! -e "$IM/.shanjie-dev-previous" ] || fail "a fresh development install kept a previous version"
run "$H" "$T/dev2/$DEV" >/dev/null
[ "$(marker "$IM/$DEV")" = dev2 ] || fail "the development reinstall did not replace $DEV"
[ "$(marker "$IM/.shanjie-dev-previous")" = dev1 ] || fail "the development reinstall did not keep .shanjie-dev-previous"
[ "$(SNAP)" = "$before" ] || fail "installing a development build changed the shipping copy, its previous version or shanjie.app"
no_staging "$H" || fail "the development install left a staging directory"
no_calls "development install next to the shipping copy"

# 10. The shipping build still goes to 善解輸入法.app, with a development copy installed: the
#     development copy and its previous version are untouched.
dsnap() { (cd "$IM" && find "$DEV" .shanjie-dev-previous -type f -exec shasum -a 256 {} + | sort); }
before=$(dsnap)
run "$H" "$T/v2/$APP" >/dev/null
[ "$(marker "$IM/$APP")" = v2 ] || fail "the shipping build was not installed as $APP"
[ "$(marker "$IM/.shanjie-previous")" = ship ] || fail "the shipping install did not keep its previous version"
[ ! -e "$IM/shanjie.app" ] || fail "the shipping install left shanjie.app"
[ "$(dsnap)" = "$before" ] || fail "installing the shipping build changed the development copy"
no_calls "shipping install next to the development copy"

# 11. Any other bundle ID is refused before anything is created.
H="$T/other"
mkdir "$H"
stub "$T/other-src/$APP" other com.nyanako.inputmethod.shanjie.selftest
if err=$(run "$H" "$T/other-src/$APP" 2>&1 >/dev/null); then fail "a build with another bundle ID was installed"; fi
grep -q "only $SHIPPING_ID and $SHIPPING_ID.dev are installed" <<<"$err" || fail "the refusal did not come from the ID check: $err"
[ -z "$(ls -A "$H")" ] || fail "the refused install created files"
no_calls "other bundle ID"

# 12. app-sandbox.md section 2.3: outside files-only mode, a shipping-ID bundle that is not signed with this project's
#     Developer ID is refused before anything is created: an ad-hoc one, and an unsigned one (codesign fails). The HOME is
#     temporary and read-only, so a regressed check would still be stopped by the HOME check, never reach the system.
chmod 700 "$T/ro"; rm -rf "$T/ro"; mkdir "$T/ro"; chmod 500 "$T/ro"
stub "$T/adhoc/$APP" adhoc
rm "$T/adhoc/$APP/Contents/marker"   # codesign refuses a loose file in Contents
codesign --sign - "$T/adhoc/$APP/Contents/MacOS/shanjie"
codesign --sign - "$T/adhoc/$APP"
stub "$T/unsigned/$APP" unsigned
for src in "$T/adhoc/$APP" "$T/unsigned/$APP"; do
  if err=$(run "$T/ro" "$src" "" 2>&1 >/dev/null); then fail "installed a shipping-ID build that is not Developer ID signed: $src"; fi
  grep -q "has the shipping bundle ID but .*refusing. For a local build use the development ID: make bundle" <<<"$err" \
    || fail "the refusal did not come from the signature check: $err"
  [ -z "$(ls -A "$T/ro")" ] || fail "the refused install created files"
done
no_calls "shipping ID without the Developer ID signature"

# 13. system-install.md section 2.1: with a copy in /Library (the stand-in), the shipping ID is not installed into the
#     account; the error prints the fully quoted removal command and nothing is created. The development ID still installs.
stub "$SYS/$APP" system
H="$T/guarded"
mkdir "$H"
if err=$(run "$H" "$T/v2/$APP" 2>&1 >/dev/null); then fail "installed into the account while /Library holds a copy"; fi
grep -qF "  sudo rm -rf \"$SYS/$APP\" \"$SYS/.shanjie-previous\"" <<<"$err" || fail "the refusal did not print the removal command: $err"
[ -z "$(ls -A "$H")" ] || fail "the refused install created files"
[ "$(marker "$SYS/$APP")" = system ] || fail "the refused install changed the /Library copy"
stub "$T/dev3/$DEV" dev3 "$SHIPPING_ID.dev"
run "$H" "$T/dev3/$DEV" >/dev/null
[ "$(marker "$H/Library/Input Methods/$DEV")" = dev3 ] || fail "the guard stopped a development build"
no_calls "shipping ID with a copy in /Library"

# 14. The installer's user stage (SHANJIE_INSTALL_NO_COPY=1, section 2.4): nothing is copied; this account's copy becomes
#     .shanjie-previous (replacing an older one), shanjie.app is removed, the /Library copy is untouched.
H="$T/nocopy"
IM="$H/Library/Input Methods"
stub "$IM/$APP" account
stub "$IM/shanjie.app" legacy
stub "$IM/.shanjie-previous" older
snap() { (cd "$SYS" && find "$APP" -type f -exec shasum -a 256 {} + | sort); }
before=$(snap)
out=$(SHANJIE_INSTALL_NO_COPY=1 run "$H" "$SYS/$APP")
grep -q '^files only: moved this account' <<<"$out" || fail "the no-copy mode did not end at its files-only line: $out"
[ "$(marker "$IM/.shanjie-previous")" = account ] || fail "the no-copy mode did not keep the account copy as .shanjie-previous"
[ ! -e "$IM/$APP" ] && [ ! -e "$IM/shanjie.app" ] || fail "the no-copy mode left an account copy: $(ls -A "$IM")"
[ "$(ls -A "$IM")" = .shanjie-previous ] || fail "unexpected files: $(ls -A "$IM")"
[ "$(snap)" = "$before" ] || fail "the no-copy mode changed the /Library copy"
no_calls "no-copy mode"
#     Only the legacy name: it becomes .shanjie-previous.
H="$T/nocopy-legacy"
IM="$H/Library/Input Methods"
stub "$IM/shanjie.app" legacy
SHANJIE_INSTALL_NO_COPY=1 run "$H" "$SYS/$APP" >/dev/null
[ "$(marker "$IM/.shanjie-previous")" = legacy ] && [ ! -e "$IM/shanjie.app" ] || fail "the no-copy mode did not move shanjie.app"
#     Nothing in the account: nothing to do, no error.
H="$T/nocopy-empty"
mkdir "$H"
SHANJIE_INSTALL_NO_COPY=1 run "$H" "$SYS/$APP" >/dev/null
[ -z "$(ls -A "$H/Library/Input Methods")" ] || fail "the no-copy mode created files in an empty account"
#     Any other argument, or the development ID, is refused before anything changes.
H="$T/nocopy-bad"
IM="$H/Library/Input Methods"
stub "$IM/$APP" account
for src in "$T/v2/$APP" "$T/dev3/$DEV"; do
  if err=$(SHANJIE_INSTALL_NO_COPY=1 run "$H" "$src" 2>&1 >/dev/null); then fail "the no-copy mode accepted $src"; fi
  grep -q "SHANJIE_INSTALL_NO_COPY=1 takes only" <<<"$err" || fail "refused for another reason: $err"
done
[ "$(marker "$IM/$APP")" = account ] && [ ! -e "$IM/.shanjie-previous" ] || fail "a refused no-copy run changed the account"
no_calls "no-copy refusals"

echo "install-ime.sh file handling: ok"
