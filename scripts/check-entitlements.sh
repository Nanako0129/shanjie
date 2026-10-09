#!/bin/bash
# docs/contracts/app-sandbox.md section 2.1: the input method's entitlements must be exactly the App
# Sandbox and one mach-register exception whose name equals the bundle's InputMethodConnectionName.
# Nothing more (no get-task-allow, no disable-library-validation), nothing less (a re-sign without
# --preserve-metadata=entitlements drops them all). Reads only; never runs anything in the bundle.
# release.yml's verify() makes the same comparison inline (its sign job runs no repository code);
# a change here goes there too.
#
#   scripts/check-entitlements.sh <app>
set -euo pipefail

fail() { echo "error: $*" >&2; exit 1; }
[ "$#" -eq 1 ] || fail "usage: $0 <app>"
APP="$1"
[ -d "$APP" ] || fail "$APP is not a directory"
CONN="$(/usr/libexec/PlistBuddy -c 'Print :InputMethodConnectionName' "$APP/Contents/Info.plist" 2>/dev/null)" \
  || fail "no InputMethodConnectionName in $APP"
[ -n "$CONN" ] || fail "empty InputMethodConnectionName in $APP"

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
  <key>com.apple.security.temporary-exception.mach-register.global-name</key>
  <array><string>$CONN</string></array>
</dict>
</plist>
EOF
# Normalized by plutil (sorted keys, one format), so only the content is compared.
plutil -convert xml1 "$T/actual.plist" || fail "the entitlements of $APP are not a property list"
plutil -convert xml1 "$T/expected.plist"
cmp -s "$T/actual.plist" "$T/expected.plist" || {
  echo "expected:" >&2; cat "$T/expected.plist" >&2
  echo "actual:" >&2; cat "$T/actual.plist" >&2
  fail "the entitlements of $APP are not exactly the sandbox and $CONN"
}
echo "entitlements: ok ($CONN)"
