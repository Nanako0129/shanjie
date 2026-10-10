#!/bin/bash
# Build build/善解輸入法.app (docs/contracts/s3b.md sections 2 and 13): the Rust core, the Swift shell, the
# bundle with its data, model and licenses, then an ad-hoc signature with the hardened runtime and
# no entitlements. Used locally and in CI; it never touches a keychain, installs or launches the
# app. Developer ID signing happens only in .github/workflows/release.yml.
#
#   scripts/build-app.sh
#
# Environment (all optional):
#   SHANJIE_VERSION  e.g. 0.1.0; otherwise the newest git tag (v0.1.0 -> 0.1.0), otherwise 0.0.0
#   BUNDLE_ID        default com.nyanako.inputmethod.shanjie, the shipping ID (the one place it is
#                    spelled for builds; `make selftest-bundled` passes a throwaway one locally).
#                    The input mode ID and the connection name derive from it.
#   OUT_DIR          default build, relative to the repository; the app is $OUT_DIR/善解輸入法.app
#
# The folder name is the user-facing name; the executable (Contents/MacOS/shanjie), the bundle ID and
# the release asset (shanjie-<version>.zip) keep the ASCII name.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
fail() { echo "error: $*" >&2; exit 1; }

# The model is not in git (docs/PLAN.md S2c); check it before spending time on the build.
[ -f data/lm/bigram.sjlm ] || fail "data/lm/bigram.sjlm is missing. Download the model-v5 release asset:
  gh release download model-v5 -R Nanako0129/shanjie -p bigram.sjlm -D data/lm"
shasum -a 256 -c data/bigram.sjlm.sha256 >/dev/null \
  || fail "data/lm/bigram.sjlm does not match data/bigram.sjlm.sha256; download model-v5 again"
[ -f data/lm/classes.sjc ] || fail "data/lm/classes.sjc is missing (the word-class term of the model, S2k). Download the classes-v3 release asset:
  gh release download classes-v3 -R Nanako0129/shanjie -p classes.sjc -D data/lm"
shasum -a 256 -c data/classes.sjc.sha256 >/dev/null \
  || fail "data/lm/classes.sjc does not match data/classes.sjc.sha256; download classes-v3 again"
for f in data/lexicon/mcbpmf-data.txt data/lexicon/overlay-add.tsv data/lexicon/sandhi-add.tsv data/lexicon/demote.tsv data/packs/acg-add.tsv data/packs/acg-sources.tsv data/packs/acg.json LICENSE LICENSES/McBopomofo-MIT.txt LICENSES/data.md; do
  [ -f "$f" ] || fail "$f is missing"
done

BUNDLE_ID="${BUNDLE_ID:-com.nyanako.inputmethod.shanjie}"
[[ "$BUNDLE_ID" =~ ^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$ ]] || fail "BUNDLE_ID '$BUNDLE_ID' is not a reverse-DNS identifier"
OUT_DIR="${OUT_DIR:-build}"
# rm -rf below acts on $OUT_DIR/善解輸入法.app: keep it inside the repository.
[[ -n "$OUT_DIR" && "$OUT_DIR" != /* && "/$OUT_DIR/" != */../* ]] || fail "OUT_DIR must be a relative path inside the repository"

VERSION="${SHANJIE_VERSION:-}"
if [ -z "$VERSION" ]; then
  VERSION="$(git describe --tags --abbrev=0 --match 'v[0-9]*' 2>/dev/null || true)"
  VERSION="${VERSION#v}"
fi
VERSION="${VERSION:-0.0.0}"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "version '$VERSION' is not MAJOR.MINOR.PATCH"

cargo build --release --locked -p core
swift build -c release --package-path macos
BIN="$(swift build -c release --package-path macos --show-bin-path)/Shanjie"
[ -x "$BIN" ] || fail "the Swift build produced no executable"

mkdir -p "$ROOT/$OUT_DIR"
# Keeps Spotlight and LaunchServices from indexing local bundles, so the system never finds (or
# launches) a build/ copy of the input method by its bundle ID.
touch "$ROOT/$OUT_DIR/.metadata_never_index"
APP="$ROOT/$OUT_DIR/善解輸入法.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/zh-Hant.lproj" "$APP/Contents/Resources/en.lproj" \
  "$APP/Contents/Resources/LICENSES"
cp "$BIN" "$APP/Contents/MacOS/shanjie"

RES="$APP/Contents/Resources"
cp data/lexicon/mcbpmf-data.txt data/lexicon/overlay-add.tsv data/lexicon/sandhi-add.tsv data/lexicon/demote.tsv "$RES/"
# The optional word packs (docs/contracts/acg-pack.md A.2): the runtime files only, not the build inputs
# (acg-groups.tsv, acg-collisions.tsv, acg-exclude.tsv, acg-manual.tsv). The shell passes Resources/packs to shanjie_engine_new_packs.
mkdir -p "$RES/packs"
cp data/packs/acg-add.tsv data/packs/acg-sources.tsv data/packs/acg.json "$RES/packs/"
cp -L data/lm/bigram.sjlm data/lm/classes.sjc "$RES/"   # -L: the worktree's model may be a symlink
swift scripts/make-icon.swift "$RES/shanjie.tiff"

cp LICENSE "$RES/LICENSES/LICENSE"
cp LICENSES/McBopomofo-MIT.txt LICENSES/data.md "$RES/LICENSES/"
cat > "$RES/LICENSES/CC-BY-SA-4.0-attribution.txt" <<'EOF'
overlay-add.tsv, packs/acg-add.tsv, packs/acg-sources.tsv, packs/acg.json, demote.tsv, bigram.sjlm and
classes.sjc are licensed under the Creative Commons Attribution-ShareAlike 4.0 International license
(CC BY-SA 4.0):
https://creativecommons.org/licenses/by-sa/4.0/

overlay-add.tsv: built from Wikipedia and Wiktionary page titles.
  Attribution: Wikipedia contributors, Wiktionary contributors.
packs/acg-add.tsv: the optional anime and game word pack, built from Chinese Wikipedia (common
  conversion groups, anime article titles, character names in those articles). The page and revision
  every word comes from are listed in packs/acg-sources.tsv.
  Attribution: Wikipedia contributors.
demote.tsv: written for this project; no third-party data.
bigram.sjlm: word counts from Wikipedia articles, Mozilla Common Voice
  sentences (CC0), Tatoeba sentences (CC BY 2.0 FR) and synthetic sentences.
  Attribution: Wikipedia contributors, Tatoeba contributors.
classes.sjc: word classes and class transition probabilities computed from
  the same counts as bigram.sjlm; same sources and attribution.

The program itself is Apache-2.0 (LICENSE); mcbpmf-data.txt is MIT
(McBopomofo-MIT.txt). Details: data.md.
EOF

# One input mode (section 13.2); the keyboard layout is chosen in its menu, not by mode.
MODE="$BUNDLE_ID.zhuyin"
strings_for() {  # strings_for <lproj> <name>
  cat > "$RES/$1/InfoPlist.strings" <<EOF
"CFBundleName" = "$2";
"CFBundleDisplayName" = "$2";
"$MODE" = "$2";
EOF
}
strings_for zh-Hant.lproj 善解輸入法
strings_for en.lproj Shanjie

mode() {
  cat <<EOF
      <key>$1</key>
      <dict>
        <key>TISIntendedLanguage</key><string>zh-Hant</string>
        <key>TISIconIsTemplate</key><true/>
        <key>tsInputModeAlternateMenuIconFileKey</key><string>shanjie.tiff</string>
        <key>tsInputModeCharacterRepertoireKey</key><array><string>Hant</string><string>Han</string></array>
        <key>tsInputModeDefaultStateKey</key><true/>
        <key>tsInputModeIsVisibleKey</key><true/>
        <key>tsInputModeMenuIconFileKey</key><string>shanjie.tiff</string>
        <key>tsInputModePaletteIconFileKey</key><string>shanjie.tiff</string>
        <key>tsInputModePrimaryInScriptKey</key><true/>
        <key>tsInputModeScriptKey</key><string>smTradChinese</string>
      </dict>
EOF
}

cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key><string>en</string>
  <key>CFBundleExecutable</key><string>shanjie</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleName</key><string>Shanjie</string>
  <!-- Finder applies the localized names only when this base display name equals the folder name
       (善解輸入法.app); the English name comes from en.lproj. Unverified on a real system. -->
  <key>CFBundleDisplayName</key><string>善解輸入法</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>26.0</string>
  <key>LSUIElement</key><true/>
  <key>LSHasLocalizedDisplayName</key><true/>
  <key>NSPrincipalClass</key><string>NSApplication</string>
  <key>InputMethodConnectionName</key><string>${BUNDLE_ID}_Connection</string>
  <key>InputMethodServerControllerClass</key><string>ShanjieInputController</string>
  <key>InputMethodServerDelegateClass</key><string>ShanjieInputController</string>
  <key>TICapsLockLanguageSwitchCapable</key><true/>
  <key>TISInputSourceID</key><string>$BUNDLE_ID</string>
  <key>TISIntendedLanguage</key><string>zh-Hant</string>
  <key>tsInputMethodCharacterRepertoireKey</key><array><string>Hant</string></array>
  <key>tsInputMethodIconFileKey</key><string>shanjie.tiff</string>
  <key>ComponentInputModeDict</key>
  <dict>
    <key>tsInputModeListKey</key>
    <dict>
$(mode "$MODE")
    </dict>
    <key>tsVisibleInputModeOrderedArrayKey</key>
    <array>
      <string>$MODE</string>
    </array>
  </dict>
</dict>
</plist>
EOF
plutil -lint "$APP/Contents/Info.plist" >/dev/null

# Ad-hoc, hardened runtime, and deliberately no --entitlements (R9).
codesign --force --sign - --options runtime "$APP"
codesign --verify --strict --deep "$APP"
echo "built $APP ($BUNDLE_ID $VERSION, ad-hoc)"
