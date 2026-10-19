#!/bin/bash
# docs/contracts/app-sandbox.md section 2.1: the input method's entitlements must be exactly these four keys, nothing more (no
# get-task-allow, no disable-library-validation) and nothing less (a re-sign without --preserve-metadata=entitlements drops
# them all):
#   com.apple.security.app-sandbox                                     true
#   com.apple.security.network.client                                  true (Sparkle only, section 2.8)
#   com.apple.security.temporary-exception.mach-register.global-name   [<ID>_Connection]
#   com.apple.security.temporary-exception.mach-lookup.global-name     [<ID>-spks, <ID>-spki]
# <ID> is the bundle's own CFBundleIdentifier, and the connection name must also equal its InputMethodConnectionName. The
# comparison is the whole plutil-normalized property list, so an extra key fails (security review F7). Reads only; never runs
# anything in the bundle. release.yml's verify() makes the same comparison inline (its sign job runs no repository code); a
# change here goes there too.
#
#   scripts/check-entitlements.sh <app>
set -euo pipefail

fail() { echo "error: $*" >&2; exit 1; }
[ "$#" -eq 1 ] || fail "usage: $0 <app>"
APP="$1"
[ -d "$APP" ] || fail "$APP is not a directory"
pb() { /usr/libexec/PlistBuddy -c "Print :$1" "$APP/Contents/Info.plist" 2>/dev/null; }
ID="$(pb CFBundleIdentifier)" || fail "no CFBundleIdentifier in $APP"
[[ "$ID" =~ ^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$ ]] || fail "CFBundleIdentifier '$ID' is not a reverse-DNS identifier"
[ "$(pb InputMethodConnectionName)" = "${ID}_Connection" ] || fail "InputMethodConnectionName is not ${ID}_Connection"

T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
codesign -d --entitlements - --xml "$APP" > "$T/actual.plist" 2>/dev/null || true
[ -s "$T/actual.plist" ] || fail "$APP carries no entitlements"
cat > "$T/expected.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>com.apple.security.app-sandbox</key><true/>
  <key>com.apple.security.network.client</key><true/>
  <key>com.apple.security.temporary-exception.mach-register.global-name</key>
  <array><string>${ID}_Connection</string></array>
  <key>com.apple.security.temporary-exception.mach-lookup.global-name</key>
  <array><string>${ID}-spks</string><string>${ID}-spki</string></array>
</dict>
</plist>
EOF
# Normalized by plutil (sorted keys, one format), so only the content is compared.
plutil -convert xml1 "$T/actual.plist" || fail "the entitlements of $APP are not a property list"
plutil -convert xml1 "$T/expected.plist"
cmp -s "$T/actual.plist" "$T/expected.plist" || {
  echo "expected:" >&2; cat "$T/expected.plist" >&2
  echo "actual:" >&2; cat "$T/actual.plist" >&2
  fail "the entitlements of $APP are not exactly the four keys for $ID"
}
echo "entitlements: ok ($ID)"
