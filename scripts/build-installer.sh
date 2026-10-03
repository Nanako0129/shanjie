#!/bin/bash
# Build 安裝善解輸入法.app (docs/contracts/s3c-installer.md section 3): the installer executable,
# Info.plist, localized names, the bundled install-ime.sh and the licenses. Never launches it.
#
#   scripts/build-installer.sh <out dir> [shanjie-<version>.zip]
#
# With the zip: it is copied into Resources/ and the bundle is ad-hoc signed (local and CI checks).
# Without it: an unsigned skeleton, which release.yml's sign job completes with the notarized zip
# (the sign job runs no repository code).
#
# Environment: SHANJIE_VERSION is required: the version of the input method build it wraps (the
# Makefile reads it from build/善解輸入法.app, release.yml from the tag), so the version rule
# lives only in build-app.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
fail() { echo "error: $*" >&2; exit 1; }

[ "$#" -ge 1 ] && [ "$#" -le 2 ] || fail "usage: $0 <out dir> [shanjie-<version>.zip]"
OUT_DIR="$1"
ZIP="${2:-}"
# rm -rf below acts on $OUT_DIR/安裝善解輸入法.app: keep it inside the repository.
[[ -n "$OUT_DIR" && "$OUT_DIR" != /* && "/$OUT_DIR/" != */../* ]] || fail "the out dir must be a relative path inside the repository"

VERSION="${SHANJIE_VERSION:-}"
[ -n "$VERSION" ] || fail "SHANJIE_VERSION is required"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "version '$VERSION' is not MAJOR.MINOR.PATCH"
if [ -n "$ZIP" ]; then
  [ -f "$ZIP" ] || fail "$ZIP not found"
  # The installer finds the zip by its own version, so the name must match.
  [ "$(basename "$ZIP")" = "shanjie-$VERSION.zip" ] || fail "the zip must be named shanjie-$VERSION.zip"
fi

swift build -c release --package-path macos --product ShanjieInstaller
BIN="$(swift build -c release --package-path macos --show-bin-path)/ShanjieInstaller"
[ -x "$BIN" ] || fail "the Swift build produced no installer executable"

mkdir -p "$ROOT/$OUT_DIR"
touch "$ROOT/$OUT_DIR/.metadata_never_index"
APP="$ROOT/$OUT_DIR/安裝善解輸入法.app"
rm -rf "$APP"
RES="$APP/Contents/Resources"
mkdir -p "$APP/Contents/MacOS" "$RES/zh-Hant.lproj" "$RES/en.lproj" "$RES/LICENSES"
cp "$BIN" "$APP/Contents/MacOS/shanjie-installer"
cp scripts/install-ime.sh "$RES/install-ime.sh"
cp LICENSE "$RES/LICENSES/LICENSE"
cp LICENSES/McBopomofo-MIT.txt LICENSES/data.md "$RES/LICENSES/"

printf '"CFBundleName" = "%s";\n"CFBundleDisplayName" = "%s";\n' 安裝善解輸入法 安裝善解輸入法 > "$RES/zh-Hant.lproj/InfoPlist.strings"
printf '"CFBundleName" = "%s";\n"CFBundleDisplayName" = "%s";\n' "Shanjie Installer" "Shanjie Installer" > "$RES/en.lproj/InfoPlist.strings"

# Bundle ID deliberately outside the input method's prefix (contract section 1).
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key><string>en</string>
  <key>CFBundleExecutable</key><string>shanjie-installer</string>
  <key>CFBundleIdentifier</key><string>com.nyanako.shanjie.installer</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleName</key><string>Shanjie Installer</string>
  <key>CFBundleDisplayName</key><string>安裝善解輸入法</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>26.0</string>
  <key>LSHasLocalizedDisplayName</key><true/>
  <key>NSPrincipalClass</key><string>NSApplication</string>
</dict>
</plist>
EOF
plutil -lint "$APP/Contents/Info.plist" >/dev/null

if [ -n "$ZIP" ]; then
  cp "$ZIP" "$RES/shanjie-$VERSION.zip"
  # Ad-hoc, hardened runtime, no entitlements; no --deep: the zip is a resource, not nested code.
  codesign --force --sign - --options runtime "$APP"
  codesign --verify --strict --deep "$APP"
  echo "built $APP ($VERSION, ad-hoc, with shanjie-$VERSION.zip)"
else
  echo "built $APP ($VERSION, unsigned skeleton without the input method zip)"
fi
