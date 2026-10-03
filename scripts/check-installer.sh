#!/bin/bash
# Acceptance 1 of docs/contracts/s3c-installer.md section 6, on a built installer. Reads only; never
# launches the installer or runs anything inside it.
#
#   scripts/check-installer.sh [installer app] [input method zip]
#
# Defaults: build/安裝善解輸入法.app and build/shanjie-<its version>.zip.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="${1:-$ROOT/build/安裝善解輸入法.app}"
fail() { echo "FAIL: $*" >&2; exit 1; }
PB=/usr/libexec/PlistBuddy
PLIST="$APP/Contents/Info.plist"
RES="$APP/Contents/Resources"

[ -x "$APP/Contents/MacOS/shanjie-installer" ] || fail "no installer executable"
[ "$($PB -c 'Print :CFBundleIdentifier' "$PLIST")" = com.nyanako.shanjie.installer ] || fail "bundle ID"
[ "$($PB -c 'Print :LSMinimumSystemVersion' "$PLIST")" = 26.0 ] || fail "minimum system version"
if $PB -c 'Print :LSUIElement' "$PLIST" >/dev/null 2>&1; then fail "the installer must be a regular app (no LSUIElement)"; fi

VERSION="$($PB -c 'Print :CFBundleShortVersionString' "$PLIST")"
ZIP="${2:-$ROOT/build/shanjie-$VERSION.zip}"
[ -f "$RES/shanjie-$VERSION.zip" ] || fail "Resources/shanjie-$VERSION.zip missing (the name must carry the installer's own version)"
cmp -s "$RES/shanjie-$VERSION.zip" "$ZIP" || fail "the bundled zip differs from $ZIP"
[ "$(find "$RES" -name '*.app' | wc -l | tr -d ' ')" = 0 ] || fail "Resources must not contain an expanded .app"
cmp -s "$RES/install-ime.sh" "$ROOT/scripts/install-ime.sh" || fail "the bundled install-ime.sh differs from scripts/install-ime.sh"
for f in LICENSE McBopomofo-MIT.txt data.md; do [ -f "$RES/LICENSES/$f" ] || fail "LICENSES/$f missing"; done

ent="$(codesign -d --entitlements - "$APP" 2>/dev/null | tr -d '\0' | grep -v '^Executable=' || true)"
[ -z "$ent" ] || fail "the installer has entitlements: $ent"
info="$(codesign -dv "$APP" 2>&1)"   # captured first: grep -q would SIGPIPE codesign under pipefail
grep -q 'flags=.*runtime' <<<"$info" || fail "hardened runtime is not on"
codesign --verify --strict --deep "$APP" || fail "codesign --verify"
echo "installer checks: ok ($APP, $VERSION)"
