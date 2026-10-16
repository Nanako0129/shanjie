#!/bin/bash
# The app icon (docs/contracts/app-icon.md): compiles packaging/AppIcon.icon into <Resources>/Assets.car and AppIcon.icns,
# the files Info.plist's CFBundleIconName and CFBundleIconFile (AppIcon) name. Shared by build-app.sh and build-installer.sh.
#
#   scripts/compile-app-icon.sh <absolute Resources dir>
#
# The minimum deployment target matches LSMinimumSystemVersion (26.0) in both Info.plists.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
fail() { echo "error: $*" >&2; exit 1; }
[ "$#" -eq 1 ] || fail "usage: $0 <absolute Resources dir>"
RES="$1"
# actool resolved relative paths against another directory (measured 2026-10-17).
[[ "$RES" == /* && -d "$RES" ]] || fail "the Resources dir must be an existing absolute path"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
xcrun actool "$ROOT/packaging/AppIcon.icon" --compile "$RES" --platform macosx --minimum-deployment-target 26.0 \
  --app-icon AppIcon --output-partial-info-plist "$TMP/icon.plist" >/dev/null || fail "actool could not compile packaging/AppIcon.icon"
# actool exits 0 and writes nothing when --app-icon does not match the .icon folder's name (Xcode 27, measured 2026-10-17).
[ -s "$RES/Assets.car" ] && [ -s "$RES/AppIcon.icns" ] || fail "actool produced no Assets.car or AppIcon.icns"
