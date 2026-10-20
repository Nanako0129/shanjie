#!/bin/bash
# Build the input method bundle (docs/contracts/s3b.md sections 2 and 13, app-sandbox.md sections 2.1-2.3 and 2.8): the Rust
# core, the Swift shell, the bundle with its data, model and licenses, Sparkle.framework (no XPC services) and the standalone
# updater 善解更新.app in Contents/Helpers (docs/contracts/standalone-updater.md), then an ad-hoc signature (without the
# hardened runtime, see the signing step) with the three App Sandbox entitlements on the input method only. Used locally and in CI;
# it never touches a keychain, installs or launches the app. Developer ID signing happens only in
# .github/workflows/release.yml, which signs the app with its own literal copy of the same entitlements.
#
#   scripts/build-app.sh
#
# Environment (all optional):
#   SHANJIE_VERSION  e.g. 0.1.0; otherwise the newest git tag (v0.1.0 -> 0.1.0), otherwise 0.0.0
#   BUNDLE_ID        default com.nyanako.inputmethod.shanjie.dev, the development ID (app-sandbox.md section 2.3). The
#                    shipping ID com.nyanako.inputmethod.shanjie is passed explicitly (release.yml;
#                    `make bundle BUNDLE_ID=com.nyanako.inputmethod.shanjie`). The ID alone decides:
#                      shipping ID  善解輸入法.app, 善解輸入法 / Shanjie, with container-migration.plist
#                      any other    善解（開發版）.app, 善解（開發版） / Shanjie (Dev), no migration list
#                    The input mode ID, the connection name, the helper's bundle ID (<ID>.updater), the preferences domain and
#                    the sandbox container all follow the ID.
#   OUT_DIR          default build, relative to the repository; the app is $OUT_DIR/<folder above>
#   SHANJIE_UPDATE_TEST_FEED, SHANJIE_UPDATE_TEST_KEY
#                    both or neither: a local test build for the update device check (docs/contracts/about-update.md
#                    section 3.4) with a test feed URL and a test EdDSA public key instead of the production ones below.
#                    Refused when CI is set. scripts/check-app.sh rejects such a build unless given the same two
#                    variables; release.yml's verify always rejects it.
#   SHANJIE_RELEASE_BUILD
#                    1 only in release.yml's build job: marks the bundle as a release build. The updater starts only in
#                    a release or an update test build (Info.plist ShanjieUpdateBuild: release, test, or local for
#                    every other build), so a copy built here and installed never reads the production feed. Requires
#                    the shipping BUNDLE_ID.
#
# The folder name is the user-facing name; the executable (Contents/MacOS/shanjie), the bundle ID and
# the release asset (shanjie-<version>.zip) keep the ASCII name.
set -euo pipefail

# Sparkle (docs/contracts/about-update.md section 2). Production values, written here and nowhere else in this script; no
# environment variable overrides them (the test build above is a separate, local-only option). scripts/check-app.sh and
# release.yml compare the bundle's Info.plist with their own copies of these literals.
SPARKLE_PUBLIC_KEY='cM3S7qQf78Hi2yH5gwD+nZm5T/6Vw05tD9ZWET3Rcew='
SPARKLE_FEED_URL='https://raw.githubusercontent.com/Nanako0129/shanjie/main/appcast.xml'

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
fail() { echo "error: $*" >&2; exit 1; }

# The model is not in git (docs/PLAN.md S2c); check it before spending time on the build.
[ -f data/lm/bigram.sjlm ] || fail "data/lm/bigram.sjlm is missing. Download the model-v6 release asset:
  gh release download model-v6 -R Nanako0129/shanjie -p bigram.sjlm -D data/lm"
shasum -a 256 -c data/bigram.sjlm.sha256 >/dev/null \
  || fail "data/lm/bigram.sjlm does not match data/bigram.sjlm.sha256; download model-v6 again"
[ -f data/lm/classes.sjc ] || fail "data/lm/classes.sjc is missing (the word-class term of the model, S2k). Download the classes-v5 release asset:
  gh release download classes-v5 -R Nanako0129/shanjie -p classes.sjc -D data/lm"
shasum -a 256 -c data/classes.sjc.sha256 >/dev/null \
  || fail "data/lm/classes.sjc does not match data/classes.sjc.sha256; download classes-v5 again"
# The Kneser-Ney side file (docs/contracts/kn-core.md, model-v6 section 2.3): bound to model-v6's hash; the core turns the term on
# when it sits beside the model, and the small model below records its SHA-256.
[ -f data/lm/kn.sjkn ] || fail "data/lm/kn.sjkn is missing (the Kneser-Ney side file of the model). Download the kn-v1 release asset:
  gh release download kn-v1 -R Nanako0129/shanjie -p kn.sjkn -D data/lm"
shasum -a 256 -c data/kn.sjkn.sha256 >/dev/null \
  || fail "data/lm/kn.sjkn does not match data/kn.sjkn.sha256; download kn-v1 again"
# The small model behind 語意校正 (docs/contracts/s7c2b-shell-rerank.md section 2.4), paired with model-v6's and kn-v1's hashes (SJTL0004).
[ -f data/lm/tinylm.sjtl ] || fail "data/lm/tinylm.sjtl is missing (the small model of 語意校正, S7c). Download the sj-sr-6-14m release asset:
  gh release download sj-sr-6-14m -R Nanako0129/shanjie -p tinylm.sjtl -D data/lm"
shasum -a 256 -c data/tinylm.sjtl.sha256 >/dev/null \
  || fail "data/lm/tinylm.sjtl does not match data/tinylm.sjtl.sha256; download sj-sr-6-14m again"
# The trigram table of the formal profile (docs/contracts/trigram-core.md): bound to model-v6's hash; the core loads it from beside the model.
[ -f data/lm/trigram.sjt3 ] || fail "data/lm/trigram.sjt3 is missing (the trigram table of the formal profile). Download the sj-cx-1-7.7m release asset:
  gh release download sj-cx-1-7.7m -R Nanako0129/shanjie -p trigram.sjt3 -D data/lm"
shasum -a 256 -c data/trigram.sjt3.sha256 >/dev/null \
  || fail "data/lm/trigram.sjt3 does not match data/trigram.sjt3.sha256; download sj-cx-1-7.7m again"
for f in data/lexicon/mcbpmf-data.txt data/lexicon/overlay-add.tsv data/lexicon/sandhi-add.tsv data/lexicon/names-add.tsv data/lexicon/demote.tsv data/lexicon/digits.tsv data/packs/acg-add.tsv data/packs/acg-sources.tsv data/packs/acg.json LICENSE LICENSES/McBopomofo-MIT.txt LICENSES/data.md LICENSES/SCOWL.txt LICENSES/SymSpell-MIT.txt LICENSES/name-suggestion-index-BSD-3-Clause.txt LICENSES/Sparkle-MIT.txt; do
  [ -f "$f" ] || fail "$f is missing"
done

# The English word list of 中英混打 (docs/contracts/zhen-mixed.md section 3.4): not in git. Downloaded from the pinned SymSpell commit
# into the cache (the URL, the commit and the input hash live only in tools/build_english_words.py), checked, generated, and the
# output checked against data/english-words.txt.sha256. A download failure or either hash mismatch fails the build.
EN_URL="$(python3 tools/build_english_words.py --print-url)"
EN_COMMIT="$(python3 tools/build_english_words.py --print-commit)"
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
[ "$EN_OUT_SHA" = "$EN_RECORDED" ] || fail "the generated english-words.txt (SHA-256 $EN_OUT_SHA) does not match data/english-words.txt.sha256"

SHIPPING_ID=com.nyanako.inputmethod.shanjie
BUNDLE_ID="${BUNDLE_ID:-$SHIPPING_ID.dev}"
[[ "$BUNDLE_ID" =~ ^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$ ]] || fail "BUNDLE_ID '$BUNDLE_ID' is not a reverse-DNS identifier"
# app-sandbox.md section 2.3: the names by ID (scripts/check-app.sh and install-ime.sh use the same table). The base display
# name must equal the folder name.
if [ "$BUNDLE_ID" = "$SHIPPING_ID" ]; then
  NAME_ZH=善解輸入法 NAME_EN=Shanjie
else
  NAME_ZH=善解（開發版） NAME_EN="Shanjie (Dev)"
fi
OUT_DIR="${OUT_DIR:-build}"
# rm -rf below acts on $OUT_DIR/<name>.app: keep it inside the repository.
[[ -n "$OUT_DIR" && "$OUT_DIR" != /* && "/$OUT_DIR/" != */../* ]] || fail "OUT_DIR must be a relative path inside the repository"

VERSION="${SHANJIE_VERSION:-}"
if [ -z "$VERSION" ]; then
  VERSION="$(git describe --tags --abbrev=0 --match 'v[0-9]*' 2>/dev/null || true)"
  VERSION="${VERSION#v}"
fi
VERSION="${VERSION:-0.0.0}"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "version '$VERSION' is not MAJOR.MINOR.PATCH"

FEED_URL="$SPARKLE_FEED_URL"
ED_KEY="$SPARKLE_PUBLIC_KEY"
if [ -n "${SHANJIE_UPDATE_TEST_FEED:-}${SHANJIE_UPDATE_TEST_KEY:-}" ]; then
  [ -z "${CI:-}" ] || fail "the update test build is local only (CI is set)"
  [ -n "${SHANJIE_UPDATE_TEST_FEED:-}" ] && [ -n "${SHANJIE_UPDATE_TEST_KEY:-}" ] \
    || fail "SHANJIE_UPDATE_TEST_FEED and SHANJIE_UPDATE_TEST_KEY go together"
  # Sparkle 2.9.3 refuses file:// for the feed and the download ("The download request URL must use http or https",
  # measured 2026-10-16), so a local test feed is served over http from this machine only. App Transport Security does
  # not block http to 127.0.0.1 without any NSAppTransportSecurity key (measured 2026-10-16: 200 from 127.0.0.1,
  # NSURLErrorDomain -1022 from http://example.com, same binary).
  [[ "$SHANJIE_UPDATE_TEST_FEED" =~ ^(https://|http://127\.0\.0\.1(:[0-9]+)?/)[^[:space:]\<\>\&]+$ ]] \
    || fail "SHANJIE_UPDATE_TEST_FEED is not an https:// or http://127.0.0.1 URL"
  [ "$SHANJIE_UPDATE_TEST_KEY" != "$SPARKLE_PUBLIC_KEY" ] || fail "the update test build must not use the production key"
  FEED_URL="$SHANJIE_UPDATE_TEST_FEED"
  ED_KEY="$SHANJIE_UPDATE_TEST_KEY"
fi
UPDATE_BUILD=local
case "${SHANJIE_RELEASE_BUILD:-}" in
  "") ;;
  1) [ "$ED_KEY" = "$SPARKLE_PUBLIC_KEY" ] || fail "SHANJIE_RELEASE_BUILD and the update test build exclude each other"
     [ "$BUNDLE_ID" = "$SHIPPING_ID" ] || fail "SHANJIE_RELEASE_BUILD=1 requires BUNDLE_ID=$SHIPPING_ID (app-sandbox.md section 2.3)"
     UPDATE_BUILD=release ;;
  *) fail "SHANJIE_RELEASE_BUILD must be 1 or unset" ;;
esac
[ "$ED_KEY" = "$SPARKLE_PUBLIC_KEY" ] || UPDATE_BUILD="test"
# An EdDSA public key is 32 bytes: 44 base64 characters ending in one '='.
[[ "$ED_KEY" =~ ^[A-Za-z0-9+/]{43}=$ ]] || fail "the Sparkle public key is not 44 base64 characters"

cargo build --release --locked -p core
# --force-resolved-versions: Sparkle exactly as macos/Package.resolved pins it, never re-resolved.
swift build -c release --package-path macos --force-resolved-versions
BIN_DIR="$(swift build -c release --package-path macos --force-resolved-versions --show-bin-path)"
BIN="$BIN_DIR/Shanjie"
[ -x "$BIN" ] || fail "the Swift build produced no executable"
[ -d "$BIN_DIR/Sparkle.framework" ] || fail "the Swift build left no Sparkle.framework beside the executable"

mkdir -p "$ROOT/$OUT_DIR"
# Keeps Spotlight and LaunchServices from indexing local bundles, so the system never finds (or
# launches) a build/ copy of the input method by its bundle ID.
touch "$ROOT/$OUT_DIR/.metadata_never_index"
APP="$ROOT/$OUT_DIR/$NAME_ZH.app"
# Both folder names, not only this ID's: a bundle left from a build with the other ID would otherwise sit next to this one
# and be checked, wrapped by `make installer` or run by mistake.
rm -rf "$ROOT/$OUT_DIR/善解輸入法.app" "$ROOT/$OUT_DIR/善解（開發版）.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/zh-Hant.lproj" "$APP/Contents/Resources/en.lproj" \
  "$APP/Contents/Resources/LICENSES"
cp "$BIN" "$APP/Contents/MacOS/shanjie"

# Sparkle.framework in Contents/Frameworks/Updater (the executable's rpath besides SwiftPM's @loader_path,
# macos/Package.swift), with no XPC services, no Autoupdate and no Updater.app: the input method only probes the feed
# (SUEnableInstallerLauncherService false), and the install runs in 善解更新.app, outside the sandbox, with its own copy of the
# framework, which keeps Autoupdate and Updater.app (standalone-updater.md sections 2.2 and 2.4). With no XPC service in the
# bundle, the system has nothing to remember by path (the folder name Updater is history from app-sandbox.md section 2.8's
# revision three, where Installer.xpc was kept and the framework's folder had to change whenever the services did).
strip_xpc() {  # strip_xpc <Sparkle.framework>: Versions/B/XPCServices and the top-level symlink to it
  rm -rf "$1/Versions/B/XPCServices" "$1/XPCServices"
  [ -z "$(find "$1" -name '*.xpc')" ] || fail "an .xpc is left in $1"
  for code in "$1/Versions/B/Autoupdate" "$1/Versions/B/Updater.app"; do
    [ -e "$code" ] || fail "$code is missing; Sparkle's layout changed"
  done
}
mkdir -p "$APP/Contents/Frameworks/Updater"
ditto "$BIN_DIR/Sparkle.framework" "$APP/Contents/Frameworks/Updater/Sparkle.framework"
SPARKLE="$APP/Contents/Frameworks/Updater/Sparkle.framework"
strip_xpc "$SPARKLE"
# Only the probe runs here: two programs fewer that could be submitted outside the sandbox (S6). Not a mitigation against
# code already running in the process (section 3), only less to ship.
# The top-level symlinks to them go too, or they would dangle.
rm -rf "$SPARKLE/Versions/B/Autoupdate" "$SPARKLE/Versions/B/Updater.app" "$SPARKLE/Autoupdate" "$SPARKLE/Updater.app"

# 善解更新.app (standalone-updater.md sections 2.1 and 2.4): not sandboxed, not LSUIElement, and no SUFeedURL, SUPublicEDKey or
# SUEnable* key: Sparkle reads those from the host, the input method that contains it. Its own Sparkle.framework has no XPC
# services either.
HELPER="$APP/Contents/Helpers/善解更新.app"
[ -x "$BIN_DIR/ShanjieUpdater" ] || fail "the Swift build produced no ShanjieUpdater executable"
mkdir -p "$HELPER/Contents/MacOS" "$HELPER/Contents/Resources" "$HELPER/Contents/Frameworks"
cp "$BIN_DIR/ShanjieUpdater" "$HELPER/Contents/MacOS/shanjie-updater"
ditto "$BIN_DIR/Sparkle.framework" "$HELPER/Contents/Frameworks/Sparkle.framework"
HSPARKLE="$HELPER/Contents/Frameworks/Sparkle.framework"
strip_xpc "$HSPARKLE"

# The colloquial table of the chat rerank (docs/contracts/colloquial-term.md section 2.3) and the list that attributes the public sentences
# its counts come from (section 2.1): both are assets of the data release sj-cl-1-68k, and both must match the hashes recorded in git.
[ -f data/lm/colloquial.sjlm ] || fail "data/lm/colloquial.sjlm is missing (the colloquial table, 常談). Download the sj-cl-1-68k release assets:
  gh release download sj-cl-1-68k -R Nanako0129/shanjie -p colloquial.sjlm -p colloquial-attribution.tsv -D data/lm"
[ -s data/lm/colloquial-attribution.tsv ] || fail "data/lm/colloquial-attribution.tsv is missing or empty (the attribution list of the colloquial table). Download the sj-cl-1-68k release assets:
  gh release download sj-cl-1-68k -R Nanako0129/shanjie -p colloquial.sjlm -p colloquial-attribution.tsv -D data/lm"
shasum -a 256 -c data/colloquial.sjlm.sha256 data/colloquial-attribution.tsv.sha256 >/dev/null \
  || fail "data/lm/colloquial.sjlm or colloquial-attribution.tsv does not match its data/*.sha256; download sj-cl-1-68k again"
RES="$APP/Contents/Resources"
# names-add.tsv: the always-on common proper nouns (model-v6 section 2.5); the core fails to start without it.
cp data/lexicon/mcbpmf-data.txt data/lexicon/overlay-add.tsv data/lexicon/sandhi-add.tsv data/lexicon/names-add.tsv data/lexicon/demote.tsv "$RES/"
# digits.tsv: the count table of digits in the composition (docs/contracts/digits.md A.3.2). Optional for the core, but a bundle without it
# silently loses the feature, so check-app.sh requires it and compares it with this file.
cp data/lexicon/digits.tsv "$RES/"
# The optional word packs (docs/contracts/acg-pack.md A.2): the runtime files only, not the build inputs
# (acg-groups.tsv, acg-collisions.tsv, acg-exclude.tsv, acg-manual.tsv). The shell passes Resources/packs to shanjie_engine_new_packs.
mkdir -p "$RES/packs"
cp data/packs/acg-add.tsv data/packs/acg-sources.tsv data/packs/acg.json "$RES/packs/"
cp -L data/lm/bigram.sjlm data/lm/classes.sjc data/lm/kn.sjkn data/lm/tinylm.sjtl data/lm/trigram.sjt3 data/lm/colloquial.sjlm "$RES/"   # -L: the worktree's model may be a symlink
cp "$EN_TMP/english-words.txt" "$RES/"   # generated above (docs/contracts/zhen-mixed.md section 3.4)
swift scripts/make-icon.swift "$RES/shanjie.tiff"
scripts/compile-app-icon.sh "$RES"   # the app icon (docs/contracts/app-icon.md): Assets.car and AppIcon.icns
cp "$RES/AppIcon.icns" "$HELPER/Contents/Resources/"   # the helper's Dock icon while it runs
cat > "$HELPER/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key><string>en</string>
  <!-- The helper has no .lproj of its own; without these, Sparkle's dialogs follow the development region (English).
       With them the framework picks its own zh_TW strings for a zh-Hant system (code review 2026-10-20; checked in a VM). -->
  <key>CFBundleLocalizations</key><array><string>zh-Hant</string><string>en</string></array>
  <key>CFBundleAllowMixedLocalizations</key><true/>
  <key>CFBundleExecutable</key><string>shanjie-updater</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID.updater</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleName</key><string>善解更新</string>
  <key>CFBundleDisplayName</key><string>善解更新</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>26.0</string>
  <key>NSPrincipalClass</key><string>NSApplication</string>
</dict>
</plist>
EOF
plutil -lint "$HELPER/Contents/Info.plist" >/dev/null

cp LICENSE "$RES/LICENSES/LICENSE"
cp LICENSES/McBopomofo-MIT.txt LICENSES/data.md LICENSES/SCOWL.txt LICENSES/SymSpell-MIT.txt LICENSES/name-suggestion-index-BSD-3-Clause.txt "$RES/LICENSES/"
# Sparkle.framework ships in Contents/Frameworks/Updater: its MIT notice with the bundled third-party notices (Sparkle's LICENSE, 2.10.0).
cp LICENSES/Sparkle-MIT.txt "$RES/LICENSES/"
# The per-unit attribution of the colloquial table's public sentences (colloquial-term.md section 2.1); no corpus text in it.
cp -L data/lm/colloquial-attribution.tsv "$RES/LICENSES/"
cat > "$RES/LICENSES/CC-BY-SA-4.0-attribution.txt" <<'EOF'
overlay-add.tsv, names-add.tsv, packs/acg-add.tsv, packs/acg-sources.tsv, packs/acg.json, demote.tsv, digits.tsv, bigram.sjlm,
classes.sjc, kn.sjkn, trigram.sjt3, tinylm.sjtl and colloquial.sjlm are licensed under the Creative Commons Attribution-ShareAlike 4.0 International license
(CC BY-SA 4.0):
https://creativecommons.org/licenses/by-sa/4.0/

overlay-add.tsv: built from Wikipedia and Wiktionary page titles.
  Attribution: Wikipedia contributors, Wiktionary contributors.
packs/acg-add.tsv: the optional anime and game word pack, built from Chinese Wikipedia (common
  conversion groups, anime article titles, character names in those articles). The page and revision
  every word comes from are listed in packs/acg-sources.tsv.
  Attribution: Wikipedia contributors.
names-add.tsv: common proper nouns (brands, shops, companies, schools), always
  on. Built from three sources; the source of every word is listed in
  data/lexicon/names-sources.tsv of the project repository. Attribution, one
  per source:
  (1) name-suggestion-index (https://github.com/osmlab/name-suggestion-index),
      BSD-3-Clause. Its copyright notice and conditions are reproduced in full
      at the end of this file and in name-suggestion-index-BSD-3-Clause.txt.
      Neither the copyright holder nor its contributors endorse this product.
  (2) Wikipedia contributors (CC BY-SA 4.0); Wikidata structured data is CC0.
  (3) 教育部統計處〈大專校院名錄〉（資料集 6091）與〈一般高級中等學校名錄〉（資料集 6089），
      2026 年 10 月取得，兩份名錄皆為 115 學年度（專案儲存庫 data/lexicon/names.json 的 moe 欄記有檔案與學年度）；此開放資料依政府資料開放授權條款
      (Open Government Data License) 進行公眾釋出，使用者於遵守本條款各項規定之前提下，
      得利用之（https://data.gov.tw/license ）。
demote.tsv: written for this project; no third-party data.
bigram.sjlm: word counts from Wikipedia articles, Mozilla Common Voice
  sentences (CC0), Tatoeba sentences (CC BY 2.0 FR) and synthetic sentences;
  the anime and game pack words (Chinese Wikipedia, and Wikidata, CC0) and
  the words of names-add.tsv (sources above) are also counted as words.
  Attribution: Wikipedia contributors, Tatoeba contributors.
classes.sjc: word classes and class transition probabilities computed from
  the same counts as bigram.sjlm; same sources and attribution.
kn.sjkn: Kneser-Ney continuation counts computed from the same counts as
  bigram.sjlm; same sources and attribution.
trigram.sjt3: pruned trigram counts of the same corpus as bigram.sjlm, mapped to
  its word ids; same sources and attribution.
tinylm.sjtl: a small character model (the 語意校正 rerank) trained on text
  from the same sources as bigram.sjlm: Wikipedia articles, Mozilla Common
  Voice sentences (CC0), Tatoeba sentences (CC BY 2.0 FR) and synthetic
  sentences. No private conversation data.
  Attribution: Wikipedia contributors, Tatoeba contributors.
colloquial.sjlm: the colloquial table (常談 SJ-CL-1-68K, the chat rerank's colloquial term): word counts of colloquial text only,
  from two groups of sources.
  (1) The colloquial text counted into bigram.sjlm (model-v6): Mozilla Common Voice sentences (CC0), Tatoeba sentences
      (CC BY 2.0 FR) and synthetic sentences. Attribution: Tatoeba contributors.
  (2) A public pool of 143,895 colloquial sentences from four sources, each used under its own licence (all of them only ask for
      attribution, which this paragraph and colloquial-attribution.tsv give):
      - WildChat-1M, Taiwan subset (allenai/WildChat-1M, https://huggingface.co/datasets/allenai/WildChat-1M ), Allen Institute for AI,
        Open Data Commons Attribution License v1.0 (ODC-BY 1.0, https://opendatacommons.org/licenses/by/1-0/ ): 3,491 sentences.
      - YODAS zh000 Traditional-script subtitles (espnet/yodas, https://huggingface.co/datasets/espnet/yodas ) of YouTube videos
        published under the Creative Commons Attribution 3.0 licence (CC BY 3.0, https://creativecommons.org/licenses/by/3.0/ ):
        90,080 sentences from 1,138 videos. The dataset supplies the video id only (no channel name), so each video is attributed
        by its id and URL in colloquial-attribution.tsv.
      - Tatoeba Traditional-script Mandarin sentences (https://tatoeba.org ), Creative Commons Attribution 2.0 France
        (CC BY 2.0 FR, https://creativecommons.org/licenses/by/2.0/fr/ ): 36,839 sentences, each attributed to its author by sentence id
        in colloquial-attribution.tsv.
      - MASSIVE zh-TW (AmazonScience/massive, https://huggingface.co/datasets/AmazonScience/massive ), Amazon.com Inc.,
        Creative Commons Attribution 4.0 (CC BY 4.0, https://creativecommons.org/licenses/by/4.0/ ): 13,485 sentences.
  The counts are not the sentences: the table holds words and word-pair counts, no sentence text. No private conversation data.
  Attribution: Tatoeba contributors, the sources above; per-video and per-sentence attributions: LICENSES/colloquial-attribution.tsv.

The program itself is Apache-2.0 (LICENSE); mcbpmf-data.txt is MIT
(McBopomofo-MIT.txt). Details: data.md.

The name-suggestion-index notice (names-add.tsv, source 1), in full:

EOF
cat LICENSES/name-suggestion-index-BSD-3-Clause.txt >> "$RES/LICENSES/CC-BY-SA-4.0-attribution.txt"

# english-words.txt is not CC BY-SA: its own attribution, not folded into the file above (docs/contracts/zhen-mixed.md section 3.4).
cat > "$RES/LICENSES/english-words-attribution.txt" <<EOF
english-words.txt is generated at build time from frequency_dictionary_en_82_765.txt of SymSpell
(https://github.com/wolfgarbe/SymSpell, commit $EN_COMMIT): all of its words of 3 to 32 lowercase ASCII letters
(words with an apostrophe, a digit or a capital letter dropped), sorted. SymSpell's list is the intersection of two sources (1 and 2), and the list
itself is SymSpell's (3):

1. Word frequencies: Google Books Ngram Viewer data, Creative Commons Attribution 3.0 Unported (CC BY 3.0),
   https://creativecommons.org/licenses/by/3.0/ . The data was filtered as described above.
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
strings_for zh-Hant.lproj "$NAME_ZH"
strings_for en.lproj "$NAME_EN"

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
  <key>CFBundleName</key><string>$NAME_EN</string>
  <!-- Finder applies the localized names only when this base display name equals the folder name
       ($NAME_ZH.app); the English name comes from en.lproj. Unverified on a real system. -->
  <key>CFBundleDisplayName</key><string>$NAME_ZH</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleIconName</key><string>AppIcon</string>
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
  <!-- Sparkle (docs/contracts/about-update.md section 2, standalone-updater.md section 2.2). No SUEnableJavaScript and no
       SUEnableDownloaderService; SUEnableInstallerLauncherService false (no XPC services: the install is 善解更新.app's, which
       reads the feed and the key from here). A local build never starts the updater. -->
  <key>ShanjieUpdateBuild</key><string>$UPDATE_BUILD</string>
  <key>SUFeedURL</key><string>$FEED_URL</string>
  <key>SUPublicEDKey</key><string>$ED_KEY</string>
  <key>SURequireSignedFeed</key><true/>
  <key>SUVerifyUpdateBeforeExtraction</key><true/>
  <key>SUAllowsAutomaticUpdates</key><false/>
  <key>SUEnableAutomaticChecks</key><false/>
  <key>SUEnableInstallerLauncherService</key><false/>
  <key>SUEnableSystemProfiling</key><false/>
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

# app-sandbox.md section 2.2: only the shipping ID carries the migration list. With no list the system is expected not to move
# the user's data into a development or throwaway build's container (an inference; CI asserts it,
# scripts/test-sandbox-migration.sh). Preferences are not listed: the system moved them by itself (research log 2026-10-09;
# the same test asserts it). New-word learning lives in the same learning.tsv, so it moves with the folder.
if [ "$BUNDLE_ID" = "$SHIPPING_ID" ]; then
  cat > "$RES/container-migration.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Move</key>
  <array>
    <string>${ApplicationSupport}/shanjie</string>
  </array>
</dict>
</plist>
PLIST
  plutil -lint "$RES/container-migration.plist" >/dev/null
fi

# app-sandbox.md section 2.1 and standalone-updater.md section 2.2: exactly three entitlements, derived from BUNDLE_ID: the
# sandbox, the network (the feed probe) and the IMK connection name (must equal InputMethodConnectionName above). Checked by
# scripts/check-entitlements.sh; release.yml signs with its own literals. The file stays next to the app
# in $OUT_DIR (ignored by git).
ENTITLEMENTS="$ROOT/$OUT_DIR/.entitlements-$BUNDLE_ID.plist"
cat > "$ENTITLEMENTS" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>com.apple.security.app-sandbox</key><true/>
  <key>com.apple.security.network.client</key><true/>
  <key>com.apple.security.temporary-exception.mach-register.global-name</key>
  <array>
    <string>${BUNDLE_ID}_Connection</string>
  </array>
</dict>
</plist>
PLIST
plutil -lint "$ENTITLEMENTS" >/dev/null

# Every file readable by others and none writable by group or others (cp keeps a source file's 0600 and a umask may leave
# group write; a root-owned bundle in /Library must load for the user and must not be changeable by anyone else,
# standalone-updater.md section 2.4); check-app.sh asserts both.
chmod -R a+rX,go-w "$APP"

# Ad-hoc, inside out, never --deep (standalone-updater.md section 2.4): the helper's Autoupdate, Updater.app and framework, the
# helper app (no entitlements), then the input method's framework (no Autoupdate or Updater.app left in it), then the input
# method, the only one with --entitlements. Removing parts broke each framework's own seal, hence signing them here. No
# hardened runtime: with it, library validation refuses the ad-hoc Sparkle.framework (dyld: "mapping process and mapped file
# (non-platform) have different Team IDs", measured 2026-10-16 with --selftest), since ad-hoc code has no Team ID to match; the
# sandbox does not need it. The shipped app gets the runtime from release.yml's Developer ID signature.
for code in "$HSPARKLE/Versions/B/Autoupdate" "$HSPARKLE/Versions/B/Updater.app" "$HSPARKLE" "$HELPER" "$SPARKLE"; do
  codesign --force --sign - "$code"
done
codesign --force --sign - --entitlements "$ENTITLEMENTS" "$APP"
codesign --verify --strict --deep "$APP"
case "$UPDATE_BUILD" in
  release) echo "built $APP ($BUNDLE_ID $VERSION, ad-hoc, release build: the updater starts)" ;;
  test) echo "built $APP ($BUNDLE_ID $VERSION, ad-hoc, UPDATE TEST BUILD: test feed and key, the updater starts)" ;;
  *) echo "built $APP ($BUNDLE_ID $VERSION, ad-hoc, local build: the updater never starts)" ;;
esac
