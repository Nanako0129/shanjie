#!/bin/bash
# Build the input method bundle (docs/spec/macos-shell.md §1, §1.2, docs/spec/install-and-update.md §2.3, §2.1, §2.6, §6.1, §2.2–§2.4): the Rust
# core, the Swift shell, the bundle with its data, model and licenses, Sparkle.framework (no XPC services) and the standalone
# updater 善解更新.app in Contents/Helpers (docs/spec/install-and-update.md §4), then an ad-hoc signature (without the
# hardened runtime, see the signing step) with the three App Sandbox entitlements on the input method only. Used locally and in CI;
# it never touches a keychain, installs or launches the app. Developer ID signing happens only in
# .github/workflows/release.yml, which signs the app with its own literal copy of the same entitlements.
#
#   scripts/build-app.sh
#
# Environment (all optional):
#   SHANJIE_VERSION  e.g. 0.1.0; otherwise the newest git tag (v0.1.0 -> 0.1.0), otherwise 0.0.0
#   BUNDLE_ID        default com.nyanako.inputmethod.shanjie.dev, the development ID (docs/spec/install-and-update.md §2.1, §2.6, §6.1). The
#                    shipping ID com.nyanako.inputmethod.shanjie is passed explicitly (release.yml;
#                    `make bundle BUNDLE_ID=com.nyanako.inputmethod.shanjie`). The ID alone decides:
#                      shipping ID  善解輸入法.app, 善解輸入法 / BopoMola, with container-migration.plist
#                      any other    善解（開發版）.app, 善解（開發版） / BopoMola (Dev), no migration list
#                    The input mode ID, the connection name, the helper's bundle ID (<ID>.updater), the preferences domain and
#                    the sandbox container all follow the ID.
#   OUT_DIR          default build, relative to the repository; the app is $OUT_DIR/<folder above>
#   SHANJIE_UPDATE_TEST_FEED, SHANJIE_UPDATE_TEST_KEY
#                    both or neither: a local test build for the update device check (docs/spec/install-and-update.md
#                    §10.4) with a test feed URL and a test EdDSA public key instead of the production ones below.
#                    Refused when CI is set. scripts/check-app.sh rejects such a build unless given the same two
#                    variables; release.yml's verify always rejects it.
#   SHANJIE_RELEASE_BUILD
#                    1 only in release.yml's build job: marks the bundle as a release build. The updater starts only in
#                    a release or an update test build (Info.plist ShanjieUpdateBuild: release, test, or local for
#                    every other build), so a copy built here and installed never reads the production feed. Requires
#                    the shipping BUNDLE_ID.
#   SHANJIE_TIMING   1: the timing build (docs/spec/semantic-rerank.md §8.6), compiled with -DSHANJIE_TIMING. A measuring build only:
#                    refused together with SHANJIE_RELEASE_BUILD. Unset, the swift build command is the same as without it.
#
# The folder name is the user-facing name; the executable (Contents/MacOS/shanjie), the bundle ID and
# the release asset (shanjie-<version>.zip) keep the ASCII name.
set -euo pipefail

# Sparkle (docs/spec/install-and-update.md §2.2, §3.7, §7.2–§7.4). Production values, written here and nowhere else in this script; no
# environment variable overrides them (the test build above is a separate, local-only option). scripts/check-app.sh and
# release.yml compare the bundle's Info.plist with their own copies of these literals.
SPARKLE_PUBLIC_KEY='cM3S7qQf78Hi2yH5gwD+nZm5T/6Vw05tD9ZWET3Rcew='
SPARKLE_FEED_URL='https://raw.githubusercontent.com/Nanako0129/shanjie/main/appcast.xml'

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
fail() { echo "error: $*" >&2; exit 1; }

# The model is not in git (docs/spec/models.md); check it before spending time on the build.
[ -f data/lm/bigram.sjlm ] || fail "data/lm/bigram.sjlm is missing. Download the sj-lm-7-9.7m release asset:
  gh release download sj-lm-7-9.7m -R Nanako0129/shanjie -p bigram.sjlm -D data/lm"
shasum -a 256 -c data/bigram.sjlm.sha256 >/dev/null \
  || fail "data/lm/bigram.sjlm does not match data/bigram.sjlm.sha256; download sj-lm-7-9.7m again"
[ -f data/lm/classes.sjc ] || fail "data/lm/classes.sjc is missing (the word-class term of the model, S2k). Download the sj-wc-6-512c release asset:
  gh release download sj-wc-6-512c -R Nanako0129/shanjie -p classes.sjc -D data/lm"
shasum -a 256 -c data/classes.sjc.sha256 >/dev/null \
  || fail "data/lm/classes.sjc does not match data/classes.sjc.sha256; download sj-wc-6-512c again"
# The Kneser-Ney side file (docs/spec/decoder.md §3.3): bound to sj-lm-7-9.7m's hash; the core turns the term on
# when it sits beside the model, and the small model below records its SHA-256.
[ -f data/lm/kn.sjkn ] || fail "data/lm/kn.sjkn is missing (the Kneser-Ney side file of the model). Download the sj-kn-2-397k release asset:
  gh release download sj-kn-2-397k -R Nanako0129/shanjie -p kn.sjkn -D data/lm"
shasum -a 256 -c data/kn.sjkn.sha256 >/dev/null \
  || fail "data/lm/kn.sjkn does not match data/kn.sjkn.sha256; download sj-kn-2-397k again"
# The small model behind 語意校正 (docs/spec/semantic-rerank.md §8.3), paired with sj-lm-7-9.7m's and sj-kn-2-397k's hashes (SJTL0006).
[ -f data/lm/tinylm.sjtl ] || fail "data/lm/tinylm.sjtl is missing (the small model of 語意校正, S7c). Download the sj-sr-10-31m release asset (會意 SJ-SR-10-31M):
  gh release download sj-sr-10-31m -R Nanako0129/shanjie -p tinylm.sjtl -D data/lm"
shasum -a 256 -c data/tinylm.sjtl.sha256 >/dev/null \
  || fail "data/lm/tinylm.sjtl does not match data/tinylm.sjtl.sha256; download sj-sr-10-31m (會意 SJ-SR-10-31M) again"
# The trigram table of the formal profile (docs/spec/decoder.md §3.4): bound to the bigram model's hash; the core loads it from beside the model.
[ -f data/lm/trigram.sjt3 ] || fail "data/lm/trigram.sjt3 is missing (the trigram table of the formal profile). Download the sj-cx-2-7.7m release asset:
  gh release download sj-cx-2-7.7m -R Nanako0129/shanjie -p trigram.sjt3 -D data/lm"
shasum -a 256 -c data/trigram.sjt3.sha256 >/dev/null \
  || fail "data/lm/trigram.sjt3 does not match data/trigram.sjt3.sha256; download sj-cx-2-7.7m again"
# The trigram table of the chat profile (docs/spec/decoder.md §3.6): bound to the same bigram.sjlm; the core loads it from beside the model too, and a
# missing one would silently leave chat on the bigram.
[ -f data/lm/trigram-chat.sjt3 ] || fail "data/lm/trigram-chat.sjt3 is missing (the trigram table of the chat profile, docs/spec/decoder.md §3.4). Download the sj-cx-2-950k release asset:
  gh release download sj-cx-2-950k -R Nanako0129/shanjie -p trigram-chat.sjt3 -D data/lm"
shasum -a 256 -c data/trigram-chat.sjt3.sha256 >/dev/null \
  || fail "data/lm/trigram-chat.sjt3 does not match data/trigram-chat.sjt3.sha256; download sj-cx-2-950k again"
# The colloquial table of the chat rerank (docs/spec/semantic-rerank.md §4.6) and the list that attributes the public sentences
# its counts come from (docs/spec/semantic-rerank.md §4.7): both are assets of the data release sj-cl-2-69k, and both must match the hashes recorded in git.
[ -f data/lm/colloquial.sjlm ] || fail "data/lm/colloquial.sjlm is missing (the colloquial table, 常談). Download the sj-cl-2-69k release assets:
  gh release download sj-cl-2-69k -R Nanako0129/shanjie -p colloquial.sjlm -p colloquial-attribution.tsv -D data/lm"
[ -s data/lm/colloquial-attribution.tsv ] || fail "data/lm/colloquial-attribution.tsv is missing or empty (the attribution list of the colloquial table). Download the sj-cl-2-69k release assets:
  gh release download sj-cl-2-69k -R Nanako0129/shanjie -p colloquial.sjlm -p colloquial-attribution.tsv -D data/lm"
shasum -a 256 -c data/colloquial.sjlm.sha256 data/colloquial-attribution.tsv.sha256 >/dev/null \
  || fail "data/lm/colloquial.sjlm or colloquial-attribution.tsv does not match its data/*.sha256; download sj-cl-2-69k again"
# The one list of bundled models and their About-page names (docs/spec/models.md): hashes against data/*.sha256 and data/lm.
scripts/check-models.sh build "$ROOT" || exit 1
for f in data/lexicon/mcbpmf-data.txt data/lexicon/overlay-add.tsv data/lexicon/sandhi-add.tsv data/lexicon/names-add.tsv data/lexicon/mixed-words.tsv data/lexicon/english.tsv data/lexicon/demote.tsv data/lexicon/variant-order.tsv data/lexicon/std-exempt.tsv data/lexicon/rerank-protect.tsv data/lexicon/digits.tsv data/packs/acg-add.tsv data/packs/acg-sources.tsv data/packs/acg.json data/packs/it-add.tsv data/packs/it-sources.tsv data/packs/it.json data/packs/beauty-add.tsv data/packs/beauty-sources.tsv data/packs/beauty.json data/packs/meme-add.tsv data/packs/meme-sources.tsv data/packs/meme.json data/packs/quotes-add.tsv data/packs/quotes.json LICENSE LICENSES/McBopomofo-MIT.txt LICENSES/data.md LICENSES/name-suggestion-index-BSD-3-Clause.txt LICENSES/Sparkle-MIT.txt LICENSES/MOE-dict-revised-terms.txt LICENSES/MOE-dict-concised-terms.txt; do
  [ -f "$f" ] || fail "$f is missing"
done

SHIPPING_ID=com.nyanako.inputmethod.shanjie
BUNDLE_ID="${BUNDLE_ID:-$SHIPPING_ID.dev}"
[[ "$BUNDLE_ID" =~ ^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$ ]] || fail "BUNDLE_ID '$BUNDLE_ID' is not a reverse-DNS identifier"
# docs/spec/install-and-update.md §2.1, §2.6, §6.1: the names by ID (scripts/check-app.sh and install-ime.sh use the same table). The base display
# name must equal the folder name.
if [ "$BUNDLE_ID" = "$SHIPPING_ID" ]; then
  NAME_ZH=善解輸入法 NAME_EN=BopoMola
else
  NAME_ZH=善解（開發版） NAME_EN="BopoMola (Dev)"
fi
OUT_DIR="${OUT_DIR:-build}"
# rm -rf below acts on $OUT_DIR/<name>.app: keep it inside the repository.
[[ -n "$OUT_DIR" && "$OUT_DIR" != /* && "/$OUT_DIR/" != */../* ]] || fail "OUT_DIR must be a relative path inside the repository"

VERSION="${SHANJIE_VERSION:-}"
if [ -z "$VERSION" ]; then
  VERSION="$(git tag --list 'v[0-9]*' --sort=-v:refname 2>/dev/null | head -n 1 || true)"
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
     [ "$BUNDLE_ID" = "$SHIPPING_ID" ] || fail "SHANJIE_RELEASE_BUILD=1 requires BUNDLE_ID=$SHIPPING_ID (docs/spec/install-and-update.md §2.1, §2.6, §6.1)"
     UPDATE_BUILD=release ;;
  *) fail "SHANJIE_RELEASE_BUILD must be 1 or unset" ;;
esac
[ "$ED_KEY" = "$SPARKLE_PUBLIC_KEY" ] || UPDATE_BUILD="test"
# An EdDSA public key is 32 bytes: 44 base64 characters ending in one '='.
[[ "$ED_KEY" =~ ^[A-Za-z0-9+/]{43}=$ ]] || fail "the Sparkle public key is not 44 base64 characters"

TIMING_FLAGS=()
case "${SHANJIE_TIMING:-}" in
  "") ;;
  1) [ -z "${SHANJIE_RELEASE_BUILD:-}" ] || fail "SHANJIE_TIMING and SHANJIE_RELEASE_BUILD exclude each other"
     TIMING_FLAGS=(-Xswiftc -DSHANJIE_TIMING) ;;
  *) fail "SHANJIE_TIMING must be 1 or unset" ;;
esac

cargo build --release --locked -p core
# --force-resolved-versions: Sparkle exactly as macos/Package.resolved pins it, never re-resolved.
swift build -c release --package-path macos --force-resolved-versions ${TIMING_FLAGS[@]+"${TIMING_FLAGS[@]}"}
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
# framework, which keeps Autoupdate and Updater.app (docs/spec/install-and-update.md §2.2, §2.3, §3.6, §2.4, §2.5, §7.2). With no XPC service in the
# bundle, the system has nothing to remember by path (the folder name Updater is history from the earlier layout, where Installer.xpc was kept and the framework's folder had to change whenever the services did; docs/spec/install-and-update.md §2.2–§2.4).
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
# code already running in the process, only less to ship.
# The top-level symlinks to them go too, or they would dangle.
rm -rf "$SPARKLE/Versions/B/Autoupdate" "$SPARKLE/Versions/B/Updater.app" "$SPARKLE/Autoupdate" "$SPARKLE/Updater.app"

# 善解更新.app (docs/spec/install-and-update.md §4.1–§4.5, §2.4, §2.5, §7.2): not sandboxed, not LSUIElement, and no SUFeedURL, SUPublicEDKey or
# SUEnable* key: Sparkle reads those from the host, the input method that contains it. Its own Sparkle.framework has no XPC
# services either.
HELPER="$APP/Contents/Helpers/善解更新.app"
[ -x "$BIN_DIR/ShanjieUpdater" ] || fail "the Swift build produced no ShanjieUpdater executable"
mkdir -p "$HELPER/Contents/MacOS" "$HELPER/Contents/Resources" "$HELPER/Contents/Frameworks"
cp "$BIN_DIR/ShanjieUpdater" "$HELPER/Contents/MacOS/shanjie-updater"
ditto "$BIN_DIR/Sparkle.framework" "$HELPER/Contents/Frameworks/Sparkle.framework"
HSPARKLE="$HELPER/Contents/Frameworks/Sparkle.framework"
strip_xpc "$HSPARKLE"

RES="$APP/Contents/Resources"
# names-add.tsv: the always-on common proper nouns (docs/spec/lexicon-and-packs.md §1.1, §3); the core fails to start without it.
cp data/lexicon/mcbpmf-data.txt data/lexicon/overlay-add.tsv data/lexicon/sandhi-add.tsv data/lexicon/names-add.tsv data/lexicon/demote.tsv data/lexicon/variant-order.tsv data/lexicon/std-exempt.tsv data/lexicon/rerank-protect.tsv "$RES/"
# digits.tsv: the count table of digits in the composition (docs/spec/english-mixed.md §7.2, §7.8). Optional for the core, but a bundle without it
# silently loses the feature, so check-app.sh requires it and compares it with this file.
cp data/lexicon/digits.tsv "$RES/"
# mixed-words.tsv: the English+Chinese words of the candidate list (docs/spec/english-mixed.md §5.7). Optional for the core like digits.tsv,
# so check-app.sh requires it and compares it with this file.
cp data/lexicon/mixed-words.tsv "$RES/"
# english.tsv: the count table of "English or Zhuyin at the closing key" (docs/spec/english-mixed.md §16.5). Optional for the core like digits.tsv
# (a missing table turns the comparison off without a word), so check-app.sh requires it and compares it with this file.
cp data/lexicon/english.tsv "$RES/"
# The optional word packs (docs/spec/lexicon-and-packs.md §4.1, §4.2): the runtime files only, not the build inputs
# (acg-groups.tsv, acg-collisions.tsv, acg-exclude.tsv, acg-manual.tsv, quotes-manual.tsv). The shell passes Resources/packs to shanjie_engine_new_packs.
mkdir -p "$RES/packs"
cp data/packs/acg-add.tsv data/packs/acg-sources.tsv data/packs/acg.json data/packs/quotes-add.tsv data/packs/quotes.json "$RES/packs/"
# The IT+AI pack (docs/spec/lexicon-and-packs.md §4.2, §4.3), same arrangement (build inputs it-manual.tsv, it-collisions.tsv stay out).
cp data/packs/it-add.tsv data/packs/it-sources.tsv data/packs/it.json "$RES/packs/"
# The beauty pack (docs/spec/lexicon-and-packs.md §4.4, §8.3), same arrangement (build inputs beauty-exclude.tsv, beauty-collisions.tsv, beauty-readings.tsv stay out).
# CC0, so there is no paragraph for it in the CC BY-SA attribution file below.
cp data/packs/beauty-add.tsv data/packs/beauty-sources.tsv data/packs/beauty.json "$RES/packs/"
# The meme pack (docs/spec/lexicon-and-packs.md §4.4, §9.3), same arrangement (build inputs meme-allow.tsv, meme-collisions.tsv and meme-extra-readings.tsv stay out). CC BY-SA 4.0: its paragraph is in the attribution file below.
cp data/packs/meme-add.tsv data/packs/meme-sources.tsv data/packs/meme.json "$RES/packs/"
while IFS=$'\t' read -r mf _; do cp -L "data/lm/$mf" "$RES/"; done < data/models.tsv   # -L: the worktree's model may be a symlink; models.tsv is the one list
cp data/models.tsv "$RES/"
swift scripts/make-icon.swift "$RES/shanjie.tiff"
scripts/compile-app-icon.sh "$RES"   # the app icon (docs/spec/macos-shell.md §1.4): Assets.car and AppIcon.icns
cp "$RES/AppIcon.icns" "$HELPER/Contents/Resources/"   # the helper's Dock icon while it runs
cat > "$HELPER/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key><string>en</string>
  <!-- The helper has no .lproj of its own; without these, Sparkle's dialogs follow the development region (English).
       With them the framework picks its own zh_TW strings for a zh-Hant system (checked in a VM). -->
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
cp LICENSES/McBopomofo-MIT.txt LICENSES/data.md LICENSES/name-suggestion-index-BSD-3-Clause.txt "$RES/LICENSES/"
# The Ministry of Education's notices of use for its two dictionaries (CC BY-ND 3.0 TW), whole, as their section 二 requires: the dictionaries decide which
# words get the `colloq` rows of sandhi-add.tsv and overlay-add.tsv (docs/spec/decoder.md §8.7) and readings of overlay-add.tsv and std-exempt.tsv.
cp LICENSES/MOE-dict-revised-terms.txt LICENSES/MOE-dict-concised-terms.txt "$RES/LICENSES/"
# Sparkle.framework ships in Contents/Frameworks/Updater: its MIT notice with the bundled third-party notices (Sparkle's LICENSE, 2.10.0).
cp LICENSES/Sparkle-MIT.txt "$RES/LICENSES/"
# The per-unit attribution of the colloquial table's public sentences (docs/spec/semantic-rerank.md §4.7); no corpus text in it.
cp -L data/lm/colloquial-attribution.tsv "$RES/LICENSES/"
cat > "$RES/LICENSES/CC-BY-SA-4.0-attribution.txt" <<'EOF'
overlay-add.tsv, names-add.tsv, packs/acg-add.tsv, packs/acg-sources.tsv, packs/acg.json, packs/it-add.tsv, packs/it-sources.tsv, packs/it.json, packs/meme-add.tsv, packs/meme-sources.tsv, packs/meme.json, demote.tsv, std-exempt.tsv, digits.tsv, english.tsv, bigram.sjlm,
classes.sjc, kn.sjkn, trigram.sjt3, trigram-chat.sjt3, tinylm.sjtl and colloquial.sjlm are licensed under the Creative Commons Attribution-ShareAlike 4.0 International license
(CC BY-SA 4.0):
https://creativecommons.org/licenses/by-sa/4.0/

overlay-add.tsv: built from Wikipedia and Wiktionary page titles; its `slang` rows (internet set phrases) also from
  PTT 鄉民百科 (Fandom) category titles and Fandom 迷因 Wiki page titles, whose CC BY-SA 3.0 text is used under
  CC BY-SA 4.0 by the 3.0 later-version clause.
  Attribution: Wikipedia contributors, Wiktionary contributors, PTT 鄉民百科 contributors, 迷因 Wiki contributors (Fandom).
packs/acg-add.tsv: the optional anime and game word pack, built from Chinese Wikipedia (common
  conversion groups, anime article titles, character names in those articles). The page and revision
  every word comes from are listed in packs/acg-sources.tsv.
  Attribution: Wikipedia contributors.
packs/it-add.tsv: the optional IT and AI word pack (words and readings only, no definitions). Sources, listed per word in
  packs/it-sources.tsv: (1) the National Academy for Educational Research (國家教育研究院) 電子計算機學術名詞 and
  兩岸對照名詞—計算機學術名詞 (data.gov.tw datasets 15416 and 15275), released under the Open Government Data License
  (政府資料開放授權條款, https://data.gov.tw/license ); (2) Chinese Wikipedia conversion groups Module:CGroup/IT and
  Module:CGroup/Electronics and the titles of articles in its AI categories (Wikipedia contributors, CC BY-SA 4.0);
  (3) a hand-written list by the project (CC0).
packs/meme-add.tsv: the optional meme and livestream slang word pack (words and readings only, no definitions). Titles come from
  Fandom 迷因 Wiki and PTT 鄉民百科 (Fandom) (CC BY-SA 3.0, used under CC BY-SA 4.0 by the 3.0 later-version clause) and Chinese
  Wikipedia (CC BY-SA 4.0); the usage counts that admitted each word are in packs/meme-sources.tsv. Some candidate strings
  also came from model suggestions and from a public website that was used only as a source of candidate strings (no text, no definitions).
  Attribution: 迷因 Wiki contributors, PTT 鄉民百科 contributors (Fandom), Wikipedia contributors.
names-add.tsv: common proper nouns (brands, shops, companies, transit stations,
  hospitals, venues, geographic places and trails, schools), always on. Built
  from three groups of sources; the source of every word is listed in
  data/lexicon/names-sources.tsv of the project repository. Attribution, one
  per source:
  (1) name-suggestion-index (https://github.com/osmlab/name-suggestion-index ),
      commit ac22bfd445b729b567070c1b0900be6d0c6ef924 (32 categories under
      /data/brands/), BSD-3-Clause. Its copyright notice and conditions are
      reproduced in full at the end of this file and in
      name-suggestion-index-BSD-3-Clause.txt. Neither the copyright holder nor
      its contributors endorse this product.
  (2) Wikipedia contributors (CC BY-SA 4.0; category pages, article titles, and
      hospital and school abbreviation redirects); Wikidata structured data is CC0.
  (3) Government Open Data License v1 (政府資料開放授權條款第 1 版,
      https://data.gov.tw/license ), used under its terms:
      - 2026 年 10 月取得：教育部統計處〈大專校院名錄〉（資料集 6091）與〈一般高級中等學校名錄〉（資料集 6089），
        兩份名錄皆為 115 學年度（專案儲存庫 data/lexicon/names.json 的 moe 欄記有檔案與學年度）。
      - 2026 年 11 月取得之 47 份政府開放資料集（專案儲存庫 data/lexicon/names.json 的 opendata 欄記有各檔雜湊與列數，
        共 30 個提供機關）：
        * 國營臺灣鐵路股份有限公司：〈TRA official station registry／臺鐵車站基本資料〉（資料集 33425）
        * 內政部國土測繪中心：〈THSR stations／高鐵車站地標〉（資料集 73225）、
          〈TRA + Alishan Forest Railway stations／鐵路車站地標〉（資料集 73226）、
          〈Danhai LRT + Ankeng LRT + Kaohsiung Circular LRT／輕軌捷運車站地標〉（資料集 73230）、
          〈TRTC/NTMC/TYMetro/TMRT/KRTC/Maokong stations and exits／捷運車站地標〉（資料集 73233）
        * 臺北大眾捷運股份有限公司：〈Taipei Metro BR/R/G/O/BL stations／臺北捷運車站基本資料〉（資料集 131734）
        * 臺北市政府捷運工程局：〈Taipei Metro network point GeoJSON／臺北捷運路網車站點位〉（資料集 121140）
        * 桃園市政府桃園捷運公司：〈Taoyuan Airport MRT stations／桃園機場捷運車站資料〉（資料集 128394）
        * 臺中捷運股份有限公司：〈Taichung Metro Green Line stations／臺中捷運綠線車站資料〉（資料集 144164）
        * 高雄市政府捷運工程局：〈Kaohsiung Metro Red/Orange Line stations／高雄捷運紅橘線車站資料〉（資料集 88233）、
          〈Kaohsiung Circular LRT stations／高雄環狀輕軌車站資料〉（資料集 102184）
        * 衛生福利部中央健康保險署：〈NHI contracted medical centers／健保特約醫學中心〉（資料集 39280）、
          〈NHI contracted regional hospitals／健保特約區域醫院〉（資料集 39281）、
          〈NHI contracted district hospitals／健保特約地區醫院〉（資料集 39282）
        * 勞動部勞工保險局：〈衛生福利部評鑑合格之醫院名單／MOHW-accredited hospitals & teaching hospitals〉（資料集 6271）
        * 國防部軍醫局：〈國軍醫院基本資料／Military hospitals directory〉（資料集 136354）
        * 金融監督管理委員會：〈All FSC financial institutions／金融機構基本資料（總機構）〉（資料集 6041）
        * 金融監督管理委員會證券期貨局：〈TWSE listed companies／上市公司基本資料〉（資料集 18419）、
          〈TPEx OTC companies／上櫃公司基本資料〉（資料集 25036）、
          〈TPEx emerging (興櫃) companies／興櫃公司基本資料〉（資料集 28568）、
          〈FSC public reporting (公開發行) companies／公開發行公司基本資料〉（資料集 28567）、
          〈64 securities firms with short names／證券商基本資料〉（資料集 20765）、
          〈64 securities firm HQs／證券商總公司基本資料〉（資料集 11545）
        * 金融監督管理委員會銀行局：〈14 unique FHCs／金融控股公司及直接投資之子公司〉（資料集 28775）
        * 金融監督管理委員會保險局：〈52 insurers／保險公司基本資料〉（資料集 7186）
        * 文化部：〈全國博物館名錄〉（資料集 6242）、〈全國地方文化館名錄〉（資料集 6244）
        * 文化部影視及流行音樂產業局：〈2025年全國電影院位置資料〉（資料集 22213）
        * 原住民族委員會：〈臺灣原住民族地方文化館〉（資料集 164242）、〈原住民族相關博物館〉（資料集 164266）
        * 交通部觀光署：〈民營遊樂園基本資料〉（資料集 38257）、〈觀光資訊資料庫-景點〉（資料集 7777）
        * 經濟部產業發展署：〈全國觀光工廠名錄〉（資料集 6848）
        * 農業部林業及自然保育署：〈自然步道基本資料〉（資料集 71520）、〈所轄森林遊樂區基本資料〉（資料集 37675）
        * 內政部國家公園署：〈國家公園內遊憩資訊-步道簡介〉（資料集 9963）、〈國家公園景點-遊憩據點〉（資料集 9968）
        * 臺北市市場處：〈臺北市攤販集中場〉（資料集 121594）
        * 桃園市政府經濟發展局：〈桃園市內觀光夜市〉（資料集 25965）
        * 桃園市政府地政局：〈桃園市公辦市地重劃區〉（資料集 46397）
        * 臺中市政府經濟發展局：〈臺中市列管夜市〉（資料集 85028）
        * 臺南市政府：〈臺南市地名舊稱對照〉（資料集 140066）
        * 臺南市政府經濟發展局：〈臺南市已取得設置許可夜市名冊〉（資料集 53634）
        * 臺南市政府地政局：〈臺南市政府公辦市地重劃執行情形〉（資料集 53421）
        * 高雄市政府經濟發展局：〈高雄市攤集場名冊〉（資料集 47005）
        * 新北市政府地政局：〈新北市公辦市地重劃成果〉（資料集 125432）、〈新北市自辦市地重劃成果〉（資料集 123714）
      上述開放資料依政府資料開放授權條款第 1 版 (Open Government Data License) 進行公眾釋出，
      使用者於遵守本條款各項規定之前提下，得利用之（https://data.gov.tw/license ）。
demote.tsv: written for this project; no third-party data.
variant-order.tsv: written for this project and dedicated to the public domain (CC0 1.0); no third-party data. It is not part of the CC BY-SA list above.
rerank-protect.tsv: written for this project and dedicated to the public domain (CC0 1.0); no third-party data. The words the semantic rerank may not
  take out of the n-gram's first choice. It is not part of the CC BY-SA list above.
std-exempt.tsv: the 16 (reading, character) pairs the decoder's reading term does not charge. Chosen by comparing the readings of
  mcbpmf-data.txt with the first reading of each character in the Ministry of Education's 《國語辭典簡編本》 (12 pairs) and with
  the characters its 《重編國語辭典修訂本》 gives a single reading (4 pairs)
  (both CC BY-ND 3.0 Taiwan; only used to decide these readings, none of their data is in the file or distributed).
bigram.sjlm: word counts from Wikipedia articles, Mozilla Common Voice
  sentences (CC0), Tatoeba sentences (CC BY 2.0 FR), synthetic sentences,
  public messages of the g0v Slack #rand0m channel (CC0) and articles and
  replies of public Taiwanese web forums (counted only, no original text);
  the anime and game pack words (Chinese Wikipedia, and Wikidata, CC0) and
  the words of names-add.tsv (sources above) are also counted as words.
  Attribution: Wikipedia contributors, Tatoeba contributors.
classes.sjc: word classes and class transition probabilities computed from
  the same counts as bigram.sjlm; same sources and attribution.
kn.sjkn: Kneser-Ney continuation counts computed from the same counts as
  bigram.sjlm; same sources and attribution.
trigram.sjt3, trigram-chat.sjt3: pruned trigram counts of the same corpus as bigram.sjlm, mapped to
  its word ids (the formal profile's and the chat profile's table); same sources and attribution.
tinylm.sjtl: a small character model (the 語意校正 rerank) trained on text
  from the same sources as bigram.sjlm: Wikipedia articles, Mozilla Common
  Voice sentences (CC0), Tatoeba sentences (CC BY 2.0 FR), synthetic
  sentences, g0v Slack #rand0m messages (CC0), public Taiwanese web forum articles and
  replies (no original text), and OPUS OpenSubtitles v2018 Traditional-script
  subtitles (training only, no original text). No private conversation data.
  Attribution: Wikipedia contributors, Tatoeba contributors, OpenSubtitles
  (P. Lison and J. Tiedemann, 2016, OpenSubtitles2016: Extracting Large
  Parallel Corpora from Movie and TV Subtitles, LREC 2016; http://www.opensubtitles.org/ ).
colloquial.sjlm: the colloquial table (常談 SJ-CL-2-69K, the chat rerank's colloquial term): word counts of colloquial text only,
  from two groups of sources.
  (1) The colloquial text counted under the SJ-LM-7 word list (no g0v or forum text): Mozilla Common Voice sentences (CC0), Tatoeba sentences
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

# One input mode (docs/spec/macos-shell.md §7); the keyboard layout is chosen in its menu, not by mode.
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
  <!-- Sparkle (docs/spec/install-and-update.md §2.2, §3.7). No SUEnableJavaScript and no
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

# docs/spec/install-and-update.md §2.1: only the shipping ID carries the migration list. With no list the system is expected not to move
# the user's data into a development or throwaway build's container (an inference; CI asserts it,
# scripts/test-sandbox-migration.sh). Preferences are not listed: the system moved them by itself
# (the same test asserts it). New-word learning lives in the same learning.tsv, so it moves with the folder.
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

# docs/spec/install-and-update.md §2.3, §2.2, §3.6: exactly three entitlements, derived from BUNDLE_ID: the
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
# docs/spec/install-and-update.md §2.4, §2.5, §7.2); check-app.sh asserts both.
chmod -R a+rX,go-w "$APP"

# Ad-hoc, inside out, never --deep (docs/spec/install-and-update.md §2.4, §2.5, §7.2): the helper's Autoupdate, Updater.app and framework, the
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
