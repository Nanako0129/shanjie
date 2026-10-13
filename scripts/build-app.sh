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
# The small model behind 語意校正 (docs/contracts/s7c2b-shell-rerank.md section 2.4), paired with model-v5's hash.
[ -f data/lm/tinylm.sjtl ] || fail "data/lm/tinylm.sjtl is missing (the small model of 語意校正, S7c). Download the tinylm-v3 release asset:
  gh release download tinylm-v3 -R Nanako0129/shanjie -p tinylm.sjtl -D data/lm"
shasum -a 256 -c data/tinylm.sjtl.sha256 >/dev/null \
  || fail "data/lm/tinylm.sjtl does not match data/tinylm.sjtl.sha256; download tinylm-v3 again"
for f in data/lexicon/mcbpmf-data.txt data/lexicon/overlay-add.tsv data/lexicon/sandhi-add.tsv data/lexicon/demote.tsv data/packs/acg-add.tsv data/packs/acg-sources.tsv data/packs/acg.json LICENSE LICENSES/McBopomofo-MIT.txt LICENSES/data.md LICENSES/SCOWL.txt LICENSES/SymSpell-MIT.txt; do
  [ -f "$f" ] || fail "$f is missing"
done

# The English word list of 中英混打 (docs/contracts/zhen-mixed.md section 3.4): not in git. Downloaded from the pinned SymSpell commit
# into the cache (the URL, the commit and the input hash live only in tools/build_english_words.py), checked, generated, and the
# output checked against data/english-words.txt.sha256. A download failure or either hash mismatch fails the build.
EN_URL="$(python3 tools/build_english_words.py --print-url)"
EN_COMMIT="$(printf '%s' "$EN_URL" | sed -E 's|.*/([0-9a-f]{40})/.*|\1|')"
EN_IN_SHA="$(python3 tools/build_english_words.py --print-input-sha256)"
EN_IN="${SHANJIE_ENGLISH_CACHE:-$HOME/.cache/shanjie/english}/frequency_dictionary_en_82_765.txt"
en_input_ok() { [ -f "$EN_IN" ] && [ "$(shasum -a 256 "$EN_IN" | awk '{ print $1 }')" = "$EN_IN_SHA" ]; }
if ! en_input_ok; then
  mkdir -p "$(dirname "$EN_IN")"
  rm -f "$EN_IN"
  curl -fsSL --retry 3 -o "$EN_IN.part" "$EN_URL" || fail "cannot download the English word list input from $EN_URL"
  mv "$EN_IN.part" "$EN_IN"
  en_input_ok || { rm -f "$EN_IN"; fail "the downloaded English word list input does not match SHA-256 $EN_IN_SHA"; }
fi
EN_TMP="$(mktemp -d)"
trap 'rm -rf "$EN_TMP"' EXIT
python3 tools/build_english_words.py --input "$EN_IN" --out "$EN_TMP/english-words.txt" >/dev/null || fail "tools/build_english_words.py failed"
EN_OUT_SHA="$(shasum -a 256 "$EN_TMP/english-words.txt" | awk '{ print $1 }')"
EN_RECORDED="$(awk 'NR == 1 { print $1 }' data/english-words.txt.sha256)"
if [[ "$EN_RECORDED" =~ ^[0-9a-f]{64}$ ]]; then
  [ "$EN_OUT_SHA" = "$EN_RECORDED" ] || fail "the generated english-words.txt (SHA-256 $EN_OUT_SHA) does not match data/english-words.txt.sha256"
elif [ "${SHANJIE_ENGLISH_ALLOW_UNPINNED:-}" = 1 ]; then
  # CI of the branch that adds the feature, before the final caps are chosen (zhen-mixed section 3.4 step 3): the list is built
  # from the starting caps and its hash is not checked. release.yml never sets this.
  echo "warning: data/english-words.txt.sha256 is not pinned; english-words.txt is unchecked (SHA-256 $EN_OUT_SHA)" >&2
else
  fail "data/english-words.txt.sha256 is not pinned yet (the generated list has SHA-256 $EN_OUT_SHA). Pin it once the caps are final; CI of the feature branch sets SHANJIE_ENGLISH_ALLOW_UNPINNED=1"
fi

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
cp -L data/lm/bigram.sjlm data/lm/classes.sjc data/lm/tinylm.sjtl "$RES/"   # -L: the worktree's model may be a symlink
cp "$EN_TMP/english-words.txt" "$RES/"   # generated above (docs/contracts/zhen-mixed.md section 3.4)
swift scripts/make-icon.swift "$RES/shanjie.tiff"

cp LICENSE "$RES/LICENSES/LICENSE"
cp LICENSES/McBopomofo-MIT.txt LICENSES/data.md LICENSES/SCOWL.txt LICENSES/SymSpell-MIT.txt "$RES/LICENSES/"
cat > "$RES/LICENSES/CC-BY-SA-4.0-attribution.txt" <<'EOF'
overlay-add.tsv, packs/acg-add.tsv, packs/acg-sources.tsv, packs/acg.json, demote.tsv, bigram.sjlm,
classes.sjc and tinylm.sjtl are licensed under the Creative Commons Attribution-ShareAlike 4.0 International license
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
  sentences (CC0), Tatoeba sentences (CC BY 2.0 FR) and synthetic sentences;
  the anime and game pack words (Chinese Wikipedia, and Wikidata, CC0) are
  also counted as words.
  Attribution: Wikipedia contributors, Tatoeba contributors.
classes.sjc: word classes and class transition probabilities computed from
  the same counts as bigram.sjlm; same sources and attribution.
tinylm.sjtl: a small character model (the 語意校正 rerank) trained on text
  from the same sources as bigram.sjlm: Wikipedia articles, Mozilla Common
  Voice sentences (CC0), Tatoeba sentences (CC BY 2.0 FR) and synthetic
  sentences. No private conversation data.
  Attribution: Wikipedia contributors, Tatoeba contributors.

The program itself is Apache-2.0 (LICENSE); mcbpmf-data.txt is MIT
(McBopomofo-MIT.txt). Details: data.md.
EOF

# english-words.txt is not CC BY-SA: its own attribution, not folded into the file above (docs/contracts/zhen-mixed.md section 3.4).
cat > "$RES/LICENSES/english-words-attribution.txt" <<EOF
english-words.txt is generated at build time from frequency_dictionary_en_82_765.txt of SymSpell
(https://github.com/wolfgarbe/SymSpell, commit $EN_COMMIT): the words of 3 to 32 lowercase letters,
the most frequent ones of each length class, sorted. That list is the intersection of two sources:

1. Word frequencies: Google Books Ngram Viewer data, Creative Commons Attribution 3.0 Unported (CC BY 3.0),
   https://creativecommons.org/licenses/by/3.0/ . The data was filtered and cut as described above.
2. Words: SCOWL (Spell Checker Oriented Word Lists), Copyright 2000-2026 by Kevin Atkinson. Its notice is
   LICENSES/SCOWL.txt. SymSpell does not say which SCOWL version it used; the notice included is the current release's.
3. The list itself: SymSpell, MIT License, Copyright (c) 2018 Wolf Garbe. Its text is LICENSES/SymSpell-MIT.txt.

The program is Apache-2.0 (LICENSE). Details: data.md.
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
