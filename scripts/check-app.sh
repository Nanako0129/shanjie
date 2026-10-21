#!/bin/bash
# Acceptance checks 2, 3 and 6 of docs/contracts/s3b.md section 10 on a built app (CI runs this
# after scripts/build-app.sh). Read-only towards the system: the input source list is only read
# (TISCreateInputSourceList), never registered or enabled, and the app is never launched as an
# input method: it runs only with `--selftest` and with arguments it must refuse.
#
# app-sandbox.md section 2.3: the bundle is sandboxed, so check 6 creates the bundle ID's container on its first run, and for
# the shipping ID moves the user's learning data and preferences into it. Outside CI (CI is not `true`) check 6 therefore
# refuses a shipping-ID build and exits non-zero: an ad-hoc local build must not create the shipping container first.
# SKIP_RUN=1 runs checks 2 and 3 only, never executes the bundle, and exits 0 when they pass (any ID). Until CI has shown
# that a build without a migration list moves no data, run this locally only with SKIP_RUN=1 (app-sandbox.md section 5).
#
#   [EXPECTED_BUNDLE_ID=<id>] [SKIP_RUN=1] scripts/check-app.sh [app]
#
# EXPECTED_BUNDLE_ID defaults to the shipping ID; the folder and names follow from it (the table in app-sandbox.md section
# 2.3, the same as scripts/build-app.sh). The app defaults to build/<folder>.
#
# The Sparkle keys are compared with the production values below (docs/contracts/about-update.md section 3.2). An update
# test build (scripts/build-app.sh with SHANJIE_UPDATE_TEST_FEED and SHANJIE_UPDATE_TEST_KEY) passes only when the same two
# variables are set here, and never with the production key. The update build marker (Info.plist ShanjieUpdateBuild) must be
# `local` by default, `test` for an update test build, and `release` with SHANJIE_RELEASE_BUILD=1 (a build from release.yml).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
fail() { echo "error: $*" >&2; exit 1; }
SHIPPING_ID=com.nyanako.inputmethod.shanjie
ID="${EXPECTED_BUNDLE_ID:-$SHIPPING_ID}"
if [ "$ID" = "$SHIPPING_ID" ]; then
  NAME_ZH=善解輸入法 NAME_EN=Shanjie
else
  NAME_ZH=善解（開發版） NAME_EN="Shanjie (Dev)"
fi
APP="${1:-$ROOT/build/$NAME_ZH.app}"
BIN="$APP/Contents/MacOS/shanjie"
PLIST="$APP/Contents/Info.plist"
pb() { /usr/libexec/PlistBuddy -c "Print :$1" "${2:-$PLIST}" 2>/dev/null; }
MODE="$ID.zhuyin"
MIGRATION="$APP/Contents/Resources/container-migration.plist"
# Production Sparkle values, written out again rather than read from build-app.sh, so a wrong value there fails here.
SPARKLE_PUBLIC_KEY='cM3S7qQf78Hi2yH5gwD+nZm5T/6Vw05tD9ZWET3Rcew='
SPARKLE_FEED_URL='https://raw.githubusercontent.com/Nanako0129/shanjie/main/appcast.xml'

# --- 2: Info.plist and Resources (with section 13: one input mode, the folder and the zh-Hant / en names; with
# app-sandbox.md section 2.2: the migration list only for the shipping ID)
[ "$(basename "$APP")" = "$NAME_ZH.app" ] || fail "the bundle folder is not $NAME_ZH.app"
plutil -lint "$PLIST" >/dev/null || fail "Info.plist does not lint"
[ "$(pb CFBundleDevelopmentRegion)" = en ] || fail "CFBundleDevelopmentRegion"
[ "$(pb CFBundleName)" = "$NAME_EN" ] || fail "CFBundleName"
[ "$(pb CFBundleDisplayName)" = "$NAME_ZH" ] || fail "CFBundleDisplayName must equal the folder name"
[ "$(pb LSHasLocalizedDisplayName)" = true ] || fail "LSHasLocalizedDisplayName"
[ "$(pb CFBundleIdentifier)" = "$ID" ] || fail "bundle ID is not $ID"
[ "$(pb InputMethodConnectionName)" = "${ID}_Connection" ] || fail "InputMethodConnectionName"
if [ "$ID" = "$SHIPPING_ID" ]; then
  python3 -I -c 'import plistlib, sys; sys.exit(plistlib.load(open(sys.argv[1], "rb")) != {"Move": ["${ApplicationSupport}/shanjie"]})' \
    "$MIGRATION" 2>/dev/null || fail "container-migration.plist is not exactly Move = [\${ApplicationSupport}/shanjie]"
else
  [ ! -e "$MIGRATION" ] || fail "a non-shipping build carries container-migration.plist"
fi
[ "$(pb InputMethodServerControllerClass)" = ShanjieInputController ] || fail "InputMethodServerControllerClass"
[ "$(pb InputMethodServerDelegateClass)" = ShanjieInputController ] || fail "InputMethodServerDelegateClass"
[ "$(pb LSUIElement)" = true ] || fail "LSUIElement"
# Exactly one input mode: its entries are the only lines indented by one level.
[ "$(pb ComponentInputModeDict:tsInputModeListKey | grep -E '^    [^ }]' | sed 's/ = .*//')" = "    $MODE" ] \
  || fail "the input mode list is not exactly $MODE"
K="ComponentInputModeDict:tsInputModeListKey:$MODE"
[ "$(pb "$K:TISIntendedLanguage")" = zh-Hant ] || fail "mode: TISIntendedLanguage"
[ "$(pb "$K:tsInputModeScriptKey")" = smTradChinese ] || fail "mode: tsInputModeScriptKey"
[ "$(pb ComponentInputModeDict:tsVisibleInputModeOrderedArrayKey)" = "$(printf 'Array {\n    %s\n}' "$MODE")" ] || fail "visible mode list"
R="$APP/Contents/Resources"
for lp in "zh-Hant|$NAME_ZH" "en|$NAME_EN"; do
  IFS='|' read -r l name <<<"$lp"
  S="$R/$l.lproj/InfoPlist.strings"
  plutil -lint "$S" >/dev/null || fail "$l InfoPlist.strings does not lint"
  for k in CFBundleName CFBundleDisplayName "$MODE"; do
    [ "$(pb "$k" "$S")" = "$name" ] || fail "$l InfoPlist.strings: $k"
  done
done
for f in mcbpmf-data.txt overlay-add.tsv sandhi-add.tsv names-add.tsv demote.tsv digits.tsv models.tsv shanjie.tiff \
         packs/acg-add.tsv packs/acg-sources.tsv packs/acg.json LICENSES/LICENSE LICENSES/McBopomofo-MIT.txt LICENSES/data.md LICENSES/CC-BY-SA-4.0-attribution.txt \
         english-words.txt LICENSES/SCOWL.txt LICENSES/SymSpell-MIT.txt LICENSES/english-words-attribution.txt \
         LICENSES/name-suggestion-index-BSD-3-Clause.txt LICENSES/Sparkle-MIT.txt LICENSES/colloquial-attribution.tsv; do
  [ -s "$R/$f" ] || fail "Resources/$f is missing or empty"
done
# The models: the bundled models.tsv is the list (about-models.md section 2), checked both ways against the files.
"$(dirname "$0")/check-models.sh" bundle "$R" || exit 1
cmp -s "$R/models.tsv" "$(dirname "$0")/../data/models.tsv" || fail "Resources/models.tsv differs from data/models.tsv"
while IFS=$'\t' read -r mf _; do [ -s "$R/$mf" ] || fail "Resources/$mf is missing or empty"; done < "$R/models.tsv"
# digits.tsv is optional for the core (a missing table turns the feature off without a word), so the bundle must carry exactly the repo's.
cmp -s "$R/digits.tsv" "$(dirname "$0")/../data/lexicon/digits.tsv" || fail "Resources/digits.tsv differs from data/lexicon/digits.tsv"
# The app icon (docs/contracts/app-icon.md): compiled by actool, named in Info.plist.
for f in Assets.car AppIcon.icns; do [ -s "$R/$f" ] || fail "Resources/$f (the app icon) is missing or empty"; done
for k in CFBundleIconName CFBundleIconFile; do [ "$(pb "$k")" = AppIcon ] || fail "Info.plist $k is not AppIcon"; done
# The word pack manifest (docs/contracts/acg-pack.md A.2): every file it lists is there with the SHA-256 it records,
# and the attribution file names the pack.
python3 - "$R/packs" <<'PY' || fail "packs/acg.json does not match the pack files"
import hashlib, json, os, sys
d = sys.argv[1]
files = json.load(open(os.path.join(d, "acg.json"), encoding="utf-8"))["files"]
# Explicit exits, not assert: PYTHONOPTIMIZE strips asserts and the check would pass vacuously.
if "acg-add.tsv" not in files:
    sys.exit("acg-add.tsv is not in the manifest")
for name, meta in files.items():
    data = open(os.path.join(d, name), "rb").read()
    if hashlib.sha256(data).hexdigest() != meta["sha256"] or len(data) != meta["bytes"]:
        sys.exit(name + " does not match the manifest")
PY
grep -q 'packs/acg-add.tsv' "$R/LICENSES/CC-BY-SA-4.0-attribution.txt" || fail "the attribution file does not list packs/acg-add.tsv"
# The names layer (model-v6 section 7 P3-7): the bundled file is the committed one, and the attribution file names it with its three
# sources, the name-suggestion-index notice in full among them.
cmp -s "$R/names-add.tsv" "$ROOT/data/lexicon/names-add.tsv" || fail "Resources/names-add.tsv differs from data/lexicon/names-add.tsv"
for s in 'names-add.tsv' 'name-suggestion-index' 'Wikipedia contributors' '政府資料開放授權條款' 'Redistribution and use in source and binary forms'; do
  grep -qF "$s" "$R/LICENSES/CC-BY-SA-4.0-attribution.txt" || fail "the attribution file does not have: $s"
done
# The small model (docs/contracts/s7c2b-shell-rerank.md section 2.4, F6): the bundled file against the hash recorded in git,
# not against data/lm (that would only show the copy was faithful).
grep -q 'tinylm.sjtl' "$R/LICENSES/CC-BY-SA-4.0-attribution.txt" || fail "the attribution file does not list tinylm.sjtl"
TINY_RECORDED="$(awk 'NR == 1 { print $1 }' "$ROOT/data/tinylm.sjtl.sha256")"
[[ "$TINY_RECORDED" =~ ^[0-9a-f]{64}$ ]] || fail "data/tinylm.sjtl.sha256 holds no SHA-256"
[ "$(shasum -a 256 "$R/tinylm.sjtl" | awk '{ print $1 }')" = "$TINY_RECORDED" ] \
  || fail "Resources/tinylm.sjtl does not match data/tinylm.sjtl.sha256"
# The colloquial table (docs/contracts/colloquial-term.md section 2.3, 3.6): the bundled table and attribution list are the ones recorded in git.
# The selftest (step 6 below, `--selftest`) then shows the term in effect on a sentence the shipping rerank gets wrong.
for pair in "colloquial.sjlm:$R/colloquial.sjlm" "colloquial-attribution.tsv:$R/LICENSES/colloquial-attribution.tsv"; do
  recorded="$(awk 'NR == 1 { print $1 }' "$ROOT/data/${pair%%:*}.sha256")"
  [[ "$recorded" =~ ^[0-9a-f]{64}$ ]] || fail "data/${pair%%:*}.sha256 holds no SHA-256"
  [ "$(shasum -a 256 "${pair#*:}" | awk '{ print $1 }')" = "$recorded" ] || fail "${pair#*:} does not match data/${pair%%:*}.sha256"
done
# The English word list of 中英混打 (docs/contracts/zhen-mixed.md section 3.4): every line a word the core's loader accepts (the
# loader's own limits are tested in core), the three attributions present, and the bundled file against the hash recorded in git
# (a record that is not a SHA-256 fails, like tinylm.sjtl's above).
! grep -Eqv '^[a-z]{3,32}$' "$R/english-words.txt" || fail "english-words.txt has a line outside [a-z]{3,32}"
grep -q 'SymSpell' "$R/LICENSES/english-words-attribution.txt" && grep -q 'Google Books Ngram' "$R/LICENSES/english-words-attribution.txt" \
  && grep -q 'SCOWL' "$R/LICENSES/english-words-attribution.txt" || fail "the English attribution file does not name its three sources"
EN_RECORDED="$(awk 'NR == 1 { print $1 }' "$ROOT/data/english-words.txt.sha256")"
[[ "$EN_RECORDED" =~ ^[0-9a-f]{64}$ ]] || fail "data/english-words.txt.sha256 holds no SHA-256"
[ "$(shasum -a 256 "$R/english-words.txt" | awk '{ print $1 }')" = "$EN_RECORDED" ] \
  || fail "Resources/english-words.txt does not match data/english-words.txt.sha256"
# The Kneser-Ney side file (model-v6 section 7 P3-7): against the hash recorded in git, like tinylm.sjtl; the bundled selftest then
# shows the small model paired with it (a mismatch would leave 語意校正 off).
grep -q 'kn.sjkn' "$R/LICENSES/CC-BY-SA-4.0-attribution.txt" || fail "the attribution file does not list kn.sjkn"
KN_RECORDED="$(awk 'NR == 1 { print $1 }' "$ROOT/data/kn.sjkn.sha256")"
[[ "$KN_RECORDED" =~ ^[0-9a-f]{64}$ ]] || fail "data/kn.sjkn.sha256 holds no SHA-256"
[ "$(shasum -a 256 "$R/kn.sjkn" | awk '{ print $1 }')" = "$KN_RECORDED" ] \
  || fail "Resources/kn.sjkn does not match data/kn.sjkn.sha256"
# Sparkle's keys, verbatim (docs/contracts/about-update.md section 2). A key missing from Info.plist reads as empty and fails.
WANT_FEED="$SPARKLE_FEED_URL"; WANT_KEY="$SPARKLE_PUBLIC_KEY"; WANT_BUILD=local
[ "${SHANJIE_RELEASE_BUILD:-}" != 1 ] || WANT_BUILD=release
if [ -n "${SHANJIE_UPDATE_TEST_FEED:-}${SHANJIE_UPDATE_TEST_KEY:-}" ]; then
  [ -n "${SHANJIE_UPDATE_TEST_FEED:-}" ] && [ -n "${SHANJIE_UPDATE_TEST_KEY:-}" ] || fail "SHANJIE_UPDATE_TEST_FEED and SHANJIE_UPDATE_TEST_KEY go together"
  [ "$SHANJIE_UPDATE_TEST_KEY" != "$SPARKLE_PUBLIC_KEY" ] || fail "an update test build must not carry the production key"
  [ "$WANT_BUILD" = local ] || fail "SHANJIE_RELEASE_BUILD and the update test variables exclude each other"
  WANT_FEED="$SHANJIE_UPDATE_TEST_FEED"; WANT_KEY="$SHANJIE_UPDATE_TEST_KEY"; WANT_BUILD="test"
  echo "note: checking an UPDATE TEST BUILD (test feed and key); it must never ship"
fi
# plutil -expect checks the type too: a string "true" is not the boolean Sparkle reads.
sparkle_key() {  # sparkle_key <key> <type> <value>
  [ "$(plutil -extract "$1" raw -expect "$2" -o - "$PLIST" 2>/dev/null)" = "$3" ] || fail "Info.plist $1 is not the $2 $3"
}
# Only a release or an update test build starts the updater (Updates.runsUpdater); a local build never reads the feed.
sparkle_key ShanjieUpdateBuild string "$WANT_BUILD"
sparkle_key SUFeedURL string "$WANT_FEED"
sparkle_key SUPublicEDKey string "$WANT_KEY"
sparkle_key SURequireSignedFeed bool true
sparkle_key SUVerifyUpdateBeforeExtraction bool true
sparkle_key SUAllowsAutomaticUpdates bool false
sparkle_key SUEnableAutomaticChecks bool false
# standalone-updater.md section 2.2: the installer service off (the install is 善解更新.app's), the downloader service never set.
sparkle_key SUEnableInstallerLauncherService bool false
! pb SUEnableDownloaderService >/dev/null || fail "Info.plist sets SUEnableDownloaderService"
sparkle_key SUEnableSystemProfiling bool false
# An EdDSA public key is 32 bytes: 44 base64 characters ending in one '='; an empty or placeholder value fails.
[[ "$(pb SUPublicEDKey)" =~ ^[A-Za-z0-9+/]{43}=$ ]] || fail "SUPublicEDKey is not a 44-character base64 key"
! pb SUEnableJavaScript >/dev/null || fail "Info.plist sets SUEnableJavaScript"
# Sparkle's licence (MIT, with the third-party notices its LICENSE carries): the bundled copy is the committed one.
cmp -s "$R/LICENSES/Sparkle-MIT.txt" "$ROOT/LICENSES/Sparkle-MIT.txt" || fail "Resources/LICENSES/Sparkle-MIT.txt differs from LICENSES/Sparkle-MIT.txt"
grep -q 'EXTERNAL LICENSES' "$R/LICENSES/Sparkle-MIT.txt" || fail "Sparkle-MIT.txt lacks the third-party notices"
# The trigram table of the formal profile (trigram-core): against the hash recorded in git, like kn.sjkn.
grep -q 'trigram.sjt3' "$R/LICENSES/CC-BY-SA-4.0-attribution.txt" || fail "the attribution file does not list trigram.sjt3"
TRI_RECORDED="$(awk 'NR == 1 { print $1 }' "$ROOT/data/trigram.sjt3.sha256")"
[[ "$TRI_RECORDED" =~ ^[0-9a-f]{64}$ ]] || fail "data/trigram.sjt3.sha256 holds no SHA-256"
[ "$(shasum -a 256 "$R/trigram.sjt3" | awk '{ print $1 }')" = "$TRI_RECORDED" ] \
  || fail "Resources/trigram.sjt3 does not match data/trigram.sjt3.sha256"
echo "check 2: ok"

# --- 3: signature: valid, exactly the three App Sandbox entitlements on the app (app-sandbox.md section 2.1,
# scripts/check-entitlements.sh), and the layout and permissions of standalone-updater.md section 2.4, the same assertions as
# release.yml's verify() except Team and hardened runtime: the ad-hoc build has no runtime (scripts/build-app.sh: library
# validation refuses the ad-hoc Sparkle.framework) and no Team.
# No XPC service anywhere (the system has nothing to remember by path), looked for first: a stray one also breaks the seals.
[ -z "$(find "$APP" -name '*.xpc')" ] || fail "the bundle contains an .xpc"
codesign --verify --strict --deep "$APP" || fail "codesign --verify --strict --deep"
"$ROOT/scripts/check-entitlements.sh" "$APP" || fail "entitlements"
# Contents/Helpers holds exactly 善解更新.app, whose Info.plist has no feed, key or SUEnable* key (Sparkle reads them from the
# host) and which is not LSUIElement. The input method's framework (Contents/Frameworks/Updater) has no Autoupdate or
# Updater.app, the helper's (its own Contents/Frameworks) has both. Both frameworks and the helper carry no entitlements; a
# path that is gone fails rather than reading as "no entitlements".
[ "$(ls -A "$APP/Contents/Helpers" 2>/dev/null)" = 善解更新.app ] || fail "Contents/Helpers is not exactly 善解更新.app"
HELPER="$APP/Contents/Helpers/善解更新.app"
HPLIST="$HELPER/Contents/Info.plist"
plutil -lint "$HPLIST" >/dev/null || fail "the helper's Info.plist does not lint"
[ "$(pb CFBundleIdentifier "$HPLIST")" = "$ID.updater" ] || fail "the helper's bundle ID is not $ID.updater"
HKEY="$(python3 -I -c 'import plistlib, sys
d = plistlib.load(open(sys.argv[1], "rb"))
print(" ".join(sorted(k for k in d if k in ("SUFeedURL", "SUPublicEDKey", "LSUIElement") or k.startswith("SUEnable"))))' "$HPLIST")" \
  || fail "the helper's Info.plist cannot be read"
[ -z "$HKEY" ] || fail "the helper's Info.plist sets $HKEY"
[ -x "$HELPER/Contents/MacOS/$(pb CFBundleExecutable "$HPLIST")" ] || fail "the helper's executable is missing"
# Sparkle's dialogs in Chinese (build-app.sh): the helper lists zh-Hant and allows the framework's own localizations.
pb CFBundleLocalizations "$HPLIST" | grep -qx '    zh-Hant' || fail "the helper's CFBundleLocalizations lacks zh-Hant"
[ "$(plutil -extract CFBundleAllowMixedLocalizations raw -expect bool -o - "$HPLIST" 2>/dev/null)" = true ] \
  || fail "the helper's CFBundleAllowMixedLocalizations is not true"
SPARKLE="$APP/Contents/Frameworks/Updater/Sparkle.framework"
HSPARKLE="$HELPER/Contents/Frameworks/Sparkle.framework"
[ -z "$(find "$SPARKLE" \( -name Autoupdate -o -name Updater.app \))" ] \
  || fail "the input method's Sparkle.framework contains Autoupdate or Updater.app"
for code in "$SPARKLE" "$HSPARKLE" "$HSPARKLE/Versions/B/Autoupdate" "$HSPARKLE/Versions/B/Updater.app" "$HELPER"; do
  [ -e "$code" ] || fail "$code is missing"
  codesign --verify --strict "$code" || fail "codesign --verify --strict: $code"
  ENT="$(codesign -d --entitlements - "$code" 2>/dev/null || true)"
  [ -z "$ENT" ] || fail "$code carries entitlements"
done
# Permissions (system-install section 1: a zip extracted under umask 077 once gave 0700 bundles): a root-owned copy in
# /Library must be readable and runnable by every user and changeable by nobody else. Symlinks are left out of the write
# check: their own mode is not used.
# Symbolic links are left out: macOS ignores a link's own mode, and ditto -x creates links with the extracting umask
# (release.yml extracts under umask 077, so every link in the checked copy is lrwx------; dry run 2026-10-20).
BAD=$(find "$APP" ! -type l ! -perm -o+r -print -quit)
[ -z "$BAD" ] || fail "a file in the bundle lacks o+r: $BAD"
[ -z "$(find "$APP" -type d ! -perm -o+x)" ] || fail "a directory in the bundle lacks o+x"
[ -z "$(find "$APP" -type f -perm -u+x ! -perm -o+x)" ] || fail "an executable in the bundle lacks o+x"
[ -z "$(find "$APP" ! -type l \( -perm -g+w -o -perm -o+w \))" ] || fail "a file in the bundle is writable by group or others"
[ -z "$(find "$APP" \( -perm -u+s -o -perm -g+s \))" ] || fail "a file in the bundle is setuid or setgid"
echo "check 3: ok"

if [ "${SKIP_RUN:-}" = 1 ]; then
  echo "check 6: skipped (SKIP_RUN=1; the bundle was not run)"
  exit 0
fi
if [ "${CI:-}" != true ] && [ "$ID" = "$SHIPPING_ID" ]; then
  fail "check 6 refused: running a shipping-ID build outside CI would create ~/Library/Containers/$SHIPPING_ID from an ad-hoc signature and move the learning data and preferences into it (app-sandbox.md section 2.3). Use SKIP_RUN=1 for checks 2 and 3."
fi

# --- 6: selftest and refused arguments, with no side effects. The sandbox container the first run creates is not part of
# this comparison; CI checks it before and after separately (app-sandbox.md section 2.6).
snapshot() {
  ls -laR "$HOME/Library/Input Methods" 2>&1 || true
  shasum -a 256 "$HOME/Library/Preferences/com.nyanako.inputmethod.shanjie.plist" 2>&1 || true
  swift - <<'EOF'
import Carbon
let all = TISCreateInputSourceList(nil, true)?.takeRetainedValue() as? [TISInputSource] ?? []
func prop(_ s: TISInputSource, _ k: CFString) -> String {
    guard let p = TISGetInputSourceProperty(s, k) else { return "-" }
    let v = Unmanaged<AnyObject>.fromOpaque(p).takeUnretainedValue()
    return (v as? String) ?? ((v as? Bool).map { $0 ? "1" : "0" } ?? "?")
}
for s in all {
    print(prop(s, kTISPropertyInputSourceID), prop(s, kTISPropertyInputModeID), prop(s, kTISPropertyInputSourceIsEnabled))
}
EOF
}
BEFORE="$(snapshot)"
[ -n "$BEFORE" ] || fail "empty system snapshot"

"$BIN" --selftest || fail "--selftest exited $?"
for args in "foo" "--selftest x" "install x" "--SELFTEST" "-selftest" "Install" "--install" ""; do
  rc=0
  if [ -z "$args" ]; then "$BIN" "" 2>/dev/null || rc=$?; else read -ra A <<<"$args"; "$BIN" "${A[@]}" 2>/dev/null || rc=$?; fi
  [ "$rc" -ne 0 ] || fail "arguments '$args' were accepted"
done

AFTER="$(snapshot)"
[ "$BEFORE" = "$AFTER" ] || fail "Input Methods, the preferences file or the input source list changed"
echo "check 6: ok"
