#!/bin/bash
# docs/contracts/app-sandbox.md section 2.6 step 8, standalone-updater.md sections 2.4 and 4.1: copies of the shipping-ID build,
# each changed one way and re-signed ad-hoc, against scripts/check-entitlements.sh and scripts/check-app.sh (SKIP_RUN=1:
# checks 2 and 3 only). One mutation per assertion of standalone-updater.md section 2.4 (Team and hardened runtime are not
# checked on the ad-hoc build). Nothing is ever launched; the copies live in a temporary directory that is removed at the end.
# Runs locally and in CI.
#
#   scripts/test-sandbox-mutations.sh [build/善解輸入法.app]
#
# Expected:
#   1 re-signed without --preserve-metadata           check-entitlements.sh fails (the entitlements are gone)
#   2 re-signed with --preserve-metadata=entitlements check-entitlements.sh passes
#   3 one more entitlement                            check-entitlements.sh fails
#   4 the Sparkle mach-lookup exception -spks back    check-entitlements.sh fails (exactly three keys)
#   5 an .xpc in the input method's framework         check-app.sh check 3 fails (no .xpc anywhere)
#   6 an .xpc in the helper's framework               check-app.sh check 3 fails
#   7 Autoupdate back in the input method's framework check-app.sh check 3 fails
#   8 Updater.app back in the input method's one      check-app.sh check 3 fails
#   9 Autoupdate gone from the helper's framework     check-app.sh check 3 fails
#  10 Updater.app gone from the helper's framework    check-app.sh check 3 fails
#  11 an entitlement on the helper                    check-app.sh check 3 fails (no entitlements)
#  12 an entitlement on the helper's Autoupdate       check-app.sh check 3 fails
#  13 SUFeedURL in the helper's Info.plist            check-app.sh check 3 fails
#  14 SUPublicEDKey in the helper's Info.plist        check-app.sh check 3 fails
#  15 an SUEnable* key in the helper's Info.plist     check-app.sh check 3 fails
#  16 SUEnableInstallerLauncherService true           check-app.sh check 2 fails
#  17 a second item in Contents/Helpers               check-app.sh check 3 fails (exactly 善解更新.app)
#  18 a file with mode 700                            check-app.sh check 3 fails (o+r)
#  19 a directory without o+x                         check-app.sh check 3 fails
#  20 an executable without o+x                       check-app.sh check 3 fails
#  21 a file with g+w                                 check-app.sh check 3 fails (no group or other write)
#  22 a setuid file                                   check-app.sh check 3 fails
#  23 a setgid file                                   check-app.sh check 3 fails
#  24 CFBundleLocalizations gone from the helper       check-app.sh check 3 fails (Sparkle's dialogs in English)
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
ent_fails 3 "$A" "are not exactly the three keys"

A="$(copy 4)"; ents "$A" "$T/4.plist"
K=:com.apple.security.temporary-exception.mach-lookup.global-name
/usr/libexec/PlistBuddy -c "Add $K array" -c "Add $K:0 string com.nyanako.inputmethod.shanjie-spks" "$T/4.plist"
codesign --force --sign - --entitlements "$T/4.plist" "$A" 2>/dev/null
ent_fails 4 "$A" "are not exactly the three keys"

# From here on each mutation re-signs, inside out, what it touched and everything that contains it, keeping only the input
# method's entitlements, so only the mutation differs.
F=Contents/Frameworks/Updater/Sparkle.framework
H=Contents/Helpers/善解更新.app
HF=$H/Contents/Frameworks/Sparkle.framework
# A nested part that will not sign (5 and 6: an empty file named .xpc) is left as it is: check-app.sh must then fail on the
# mutation itself, and app_fails rejects a failure for any other reason.
sign() { codesign --force --sign - "$@" 2>/dev/null || true; }
resign_app() { codesign --force --sign - --preserve-metadata=entitlements "$1" 2>/dev/null; }
resign_all() { sign "$1/$HF"; sign "$1/$H"; sign "$1/$F"; resign_app "$1"; }
# A one-key entitlements file for 11 and 12.
printf '%s\n' '<?xml version="1.0" encoding="UTF-8"?>' \
  '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">' \
  '<plist version="1.0"><dict><key>com.apple.security.network.client</key><true/></dict></plist>' > "$T/one.plist"

A="$(copy 5)"; mkdir -p "$A/$F/Versions/B/XPCServices"; touch "$A/$F/Versions/B/XPCServices/Installer.xpc"; resign_all "$A"
app_fails 5 "$A" 3 "the bundle contains an .xpc"

A="$(copy 6)"; mkdir -p "$A/$HF/Versions/B/XPCServices"; touch "$A/$HF/Versions/B/XPCServices/Installer.xpc"; resign_all "$A"
app_fails 6 "$A" 3 "the bundle contains an .xpc"

A="$(copy 7)"; ditto "$A/$HF/Versions/B/Autoupdate" "$A/$F/Versions/B/Autoupdate"; resign_all "$A"
app_fails 7 "$A" 3 "the input method's Sparkle.framework contains Autoupdate or Updater.app"

A="$(copy 8)"; ditto "$A/$HF/Versions/B/Updater.app" "$A/$F/Versions/B/Updater.app"; resign_all "$A"
app_fails 8 "$A" 3 "the input method's Sparkle.framework contains Autoupdate or Updater.app"

A="$(copy 9)"; rm -rf "$A/$HF/Versions/B/Autoupdate" "$A/$HF/Autoupdate"; resign_all "$A"
app_fails 9 "$A" 3 "Autoupdate is missing"

A="$(copy 10)"; rm -rf "$A/$HF/Versions/B/Updater.app" "$A/$HF/Updater.app"; resign_all "$A"
app_fails 10 "$A" 3 "Updater.app is missing"

A="$(copy 11)"; sign --entitlements "$T/one.plist" "$A/$H"; sign "$A/$F"; resign_app "$A"
app_fails 11 "$A" 3 "善解更新.app carries entitlements"

# Not the framework itself: codesign leaves a library's signature without entitlements even when given some (measured
# 2026-10-20 on Sparkle.framework), so the check's framework case cannot be mutated; Autoupdate, the program that runs as
# root in the system domain, can.
A="$(copy 12)"; sign --entitlements "$T/one.plist" "$A/$HF/Versions/B/Autoupdate"; sign "$A/$HF"; sign "$A/$H"; resign_app "$A"
app_fails 12 "$A" 3 "Autoupdate carries entitlements"

A="$(copy 13)"; plutil -insert SUFeedURL -string https://example.com/appcast.xml "$A/$H/Contents/Info.plist"; resign_all "$A"
app_fails 13 "$A" 3 "the helper's Info.plist sets SUFeedURL"

A="$(copy 14)"; plutil -insert SUPublicEDKey -string AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA= "$A/$H/Contents/Info.plist"; resign_all "$A"
app_fails 14 "$A" 3 "the helper's Info.plist sets SUPublicEDKey"

A="$(copy 15)"; plutil -insert SUEnableInstallerLauncherService -bool false "$A/$H/Contents/Info.plist"; resign_all "$A"
app_fails 15 "$A" 3 "the helper's Info.plist sets SUEnableInstallerLauncherService"

A="$(copy 16)"; plutil -replace SUEnableInstallerLauncherService -bool true "$A/Contents/Info.plist"; resign_app "$A"
app_fails 16 "$A" 2 "SUEnableInstallerLauncherService is not the bool false"

A="$(copy 17)"; ditto "$A/$H" "$A/Contents/Helpers/Other.app"; resign_app "$A"
app_fails 17 "$A" 3 "Contents/Helpers is not exactly"

# Modes are not part of a code signature, so 18 to 23 need no re-signing.
A="$(copy 18)"; chmod 700 "$A/Contents/Resources/english-words.txt"
app_fails 18 "$A" 3 "a file in the bundle lacks o+r"

A="$(copy 19)"; chmod o-x "$A/Contents/Resources/packs"
app_fails 19 "$A" 3 "a directory in the bundle lacks o+x"

A="$(copy 20)"; chmod o-x "$A/$H/Contents/MacOS/shanjie-updater"
app_fails 20 "$A" 3 "an executable in the bundle lacks o+x"

A="$(copy 21)"; chmod g+w "$A/Contents/Resources/english-words.txt"
app_fails 21 "$A" 3 "a file in the bundle is writable by group or others"

A="$(copy 22)"; chmod u+s "$A/$H/Contents/MacOS/shanjie-updater"
app_fails 22 "$A" 3 "a file in the bundle is setuid or setgid"

A="$(copy 23)"; chmod g+s "$A/$H/Contents/MacOS/shanjie-updater"
app_fails 23 "$A" 3 "a file in the bundle is setuid or setgid"

A="$(copy 24)"; plutil -remove CFBundleLocalizations "$A/$H/Contents/Info.plist"; resign_all "$A"
app_fails 24 "$A" 3 "the helper's CFBundleLocalizations lacks zh-Hant"

echo "sandbox mutations: all 24 as expected"
