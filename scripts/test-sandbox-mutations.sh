#!/bin/bash
# docs/contracts/app-sandbox.md section 2.6 step 8: ten copies of the shipping-ID build, each changed one way and re-signed
# ad-hoc, against scripts/check-entitlements.sh and scripts/check-app.sh (SKIP_RUN=1: checks 2 and 3 only). Nothing is ever
# launched; the copies live in a temporary directory that is removed at the end. Runs locally and in CI.
#
#   scripts/test-sandbox-mutations.sh [build/善解輸入法.app]
#
# Expected (section 4.1 item 1):
#   1 re-signed without --preserve-metadata          check-entitlements.sh fails (the entitlements are gone)
#   2 re-signed with --preserve-metadata=entitlements check-entitlements.sh passes
#   3 one more entitlement                           check-entitlements.sh fails
#   4 Sparkle's two mach names for another ID        check-entitlements.sh fails
#   5 Installer.xpc removed                          check-app.sh check 3 fails
#   6 a second .xpc in XPCServices                   check-app.sh check 3 fails
#   7 SUEnableInstallerLauncherService false         check-app.sh check 2 fails
#   8 a second .xpc outside XPCServices              check-app.sh check 3 fails (the whole-framework XPC layout)
#   9 the framework back in Contents/Frameworks      check-app.sh check 3 fails (the framework's place, section 2.8)
#  10 a second framework in Contents/Frameworks too  check-app.sh check 3 fails (exactly Updater/Sparkle.framework)
# release.yml's verify() has its own copy of the comparison and is not mutated here (`sign` runs only on main).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${1:-$ROOT/build/善解輸入法.app}"
NAME="$(basename "$SRC")"
fail() { echo "FAIL: $*" >&2; exit 1; }
[ "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$SRC/Contents/Info.plist")" = com.nyanako.inputmethod.shanjie ] \
  || fail "$SRC is not the shipping-ID build"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
# Before any copy exists: Spotlight and LaunchServices must not index the shipping-ID copies (as build-app.sh does for build/).
touch "$T/.metadata_never_index"

# APFS clones (cp -c) where the file system supports them, a full ditto otherwise.
copy() { mkdir -p "$T/$1"; cp -Rc "$SRC" "$T/$1/$NAME" 2>/dev/null || { rm -rf "${T:?}/$1/$NAME"; ditto "$SRC" "$T/$1/$NAME"; }; echo "$T/$1/$NAME"; }
ents() { codesign -d --entitlements - --xml "$1" 2>/dev/null > "$2"; plutil -lint "$2" >/dev/null; }
ent_fails() {  # ent_fails <n> <app> <message it must print>
  if "$ROOT/scripts/check-entitlements.sh" "$2" > "$T/$1.out" 2>&1; then cat "$T/$1.out"; fail "mutation $1: check-entitlements.sh passed"; fi
  grep -qF "$3" "$T/$1.out" || { cat "$T/$1.out"; fail "mutation $1: check-entitlements.sh failed for another reason"; }
  echo "mutation $1: check-entitlements.sh failed, as expected ($3)"
}
# app_fails <n> <app> <check that must fail> <message it must print>
app_fails() {
  local rc=0
  SKIP_RUN=1 EXPECTED_BUNDLE_ID=com.nyanako.inputmethod.shanjie "$ROOT/scripts/check-app.sh" "$2" > "$T/$1.out" 2>&1 || rc=$?
  [ "$rc" -ne 0 ] || { cat "$T/$1.out"; fail "mutation $1: check-app.sh passed"; }
  grep -qF "$4" "$T/$1.out" || { cat "$T/$1.out"; fail "mutation $1: check-app.sh failed for another reason"; }
  if [ "$3" = 3 ]; then grep -qx 'check 2: ok' "$T/$1.out" || { cat "$T/$1.out"; fail "mutation $1: check 2 did not pass first"; }; fi
  ! grep -qx "check $3: ok" "$T/$1.out" || fail "mutation $1: check $3 passed"
  echo "mutation $1: check-app.sh check $3 failed, as expected ($4)"
}

# The original passes both, or the mutations below prove nothing.
"$ROOT/scripts/check-entitlements.sh" "$SRC" >/dev/null || fail "the unmodified build fails check-entitlements.sh"
SKIP_RUN=1 EXPECTED_BUNDLE_ID=com.nyanako.inputmethod.shanjie "$ROOT/scripts/check-app.sh" "$SRC" > "$T/0.out" 2>&1 \
  || { cat "$T/0.out"; fail "the unmodified build fails check-app.sh checks 2 and 3"; }

A="$(copy 1)"; codesign --force --sign - "$A" 2>/dev/null
ent_fails 1 "$A" "carries no entitlements"

A="$(copy 2)"; codesign --force --sign - --preserve-metadata=entitlements "$A" 2>/dev/null
"$ROOT/scripts/check-entitlements.sh" "$A" > "$T/2.out" 2>&1 || { cat "$T/2.out"; fail "mutation 2: check-entitlements.sh failed"; }
echo "mutation 2: check-entitlements.sh passed, as expected"

A="$(copy 3)"; ents "$A" "$T/3.plist"
# PlistBuddy, not plutil: plutil reads the dots in an entitlement name as a key path.
/usr/libexec/PlistBuddy -c 'Add :com.apple.security.cs.disable-library-validation bool true' "$T/3.plist"
codesign --force --sign - --entitlements "$T/3.plist" "$A" 2>/dev/null
ent_fails 3 "$A" "are not exactly the four keys"

A="$(copy 4)"; ents "$A" "$T/4.plist"
K=:com.apple.security.temporary-exception.mach-lookup.global-name
/usr/libexec/PlistBuddy -c "Set $K:0 com.example.other-spks" -c "Set $K:1 com.example.other-spki" "$T/4.plist"
codesign --force --sign - --entitlements "$T/4.plist" "$A" 2>/dev/null
ent_fails 4 "$A" "are not exactly the four keys"

# 5 and 6 re-sign the framework and then the app with its entitlements kept, so only the XPC services differ.
F=Contents/Frameworks/Updater/Sparkle.framework
resign() { codesign --force --sign - "$1/$F" 2>/dev/null; codesign --force --sign - --preserve-metadata=entitlements "$1" 2>/dev/null; }
X=$F/Versions/B/XPCServices
A="$(copy 5)"; rm -rf "$A/$X/Installer.xpc"; resign "$A"
app_fails 5 "$A" 3 "XPCServices is not exactly Installer.xpc"

A="$(copy 6)"; ditto "$A/$X/Installer.xpc" "$A/$X/Extra.xpc"; resign "$A"
app_fails 6 "$A" 3 "XPCServices is not exactly Installer.xpc"

A="$(copy 7)"; plutil -replace SUEnableInstallerLauncherService -bool false "$A/Contents/Info.plist"
codesign --force --sign - --preserve-metadata=entitlements "$A" 2>/dev/null
app_fails 7 "$A" 2 "SUEnableInstallerLauncherService is not the bool true"

A="$(copy 8)"; ditto "$A/$X/Installer.xpc" "$A/$F/Versions/B/Extra.xpc"; resign "$A"
app_fails 8 "$A" 3 "Sparkle.framework's XPC layout is not exactly"

A="$(copy 9)"; mv "$A/$F" "$A/Contents/Frameworks/"; rmdir "$A/Contents/Frameworks/Updater"
codesign --force --sign - --preserve-metadata=entitlements "$A" 2>/dev/null
app_fails 9 "$A" 3 "Contents/Frameworks is not exactly Updater/Sparkle.framework"

A="$(copy 10)"; ditto "$A/$F" "$A/Contents/Frameworks/Sparkle.framework"
codesign --force --sign - --preserve-metadata=entitlements "$A" 2>/dev/null
app_fails 10 "$A" 3 "Contents/Frameworks is not exactly Updater/Sparkle.framework"

echo "sandbox mutations: all ten as expected"
