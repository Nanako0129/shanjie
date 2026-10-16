#!/bin/bash
# Acceptance checks 2, 3 and 6 of docs/contracts/s3b.md section 10 on a built app (CI runs this
# after scripts/build-app.sh). Read-only towards the system: the input source list is only read
# (TISCreateInputSourceList), never registered or enabled, and the app is never launched as an
# input method: it runs only with `--selftest` and with arguments it must refuse.
#
#   scripts/check-app.sh [build/善解輸入法.app]
#
# The Sparkle keys are compared with the production values below (docs/contracts/about-update.md section 3.2). An update
# test build (scripts/build-app.sh with SHANJIE_UPDATE_TEST_FEED and SHANJIE_UPDATE_TEST_KEY) passes only when the same two
# variables are set here, and never with the production key. The update build marker (Info.plist ShanjieUpdateBuild) must be
# `local` by default, `test` for an update test build, and `release` with SHANJIE_RELEASE_BUILD=1 (a build from release.yml).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="${1:-$ROOT/build/善解輸入法.app}"
BIN="$APP/Contents/MacOS/shanjie"
PLIST="$APP/Contents/Info.plist"
fail() { echo "error: $*" >&2; exit 1; }
pb() { /usr/libexec/PlistBuddy -c "Print :$1" "${2:-$PLIST}" 2>/dev/null; }
MODE=com.nyanako.inputmethod.shanjie.zhuyin
# Production Sparkle values, written out again rather than read from build-app.sh, so a wrong value there fails here.
SPARKLE_PUBLIC_KEY='cM3S7qQf78Hi2yH5gwD+nZm5T/6Vw05tD9ZWET3Rcew='
SPARKLE_FEED_URL='https://raw.githubusercontent.com/Nanako0129/shanjie/main/appcast.xml'

# --- 2: Info.plist and Resources (with section 13: one input mode, the 善解輸入法.app folder and
# the zh-Hant / en names)
[ "$(basename "$APP")" = 善解輸入法.app ] || fail "the bundle folder is not 善解輸入法.app"
plutil -lint "$PLIST" >/dev/null || fail "Info.plist does not lint"
[ "$(pb CFBundleDevelopmentRegion)" = en ] || fail "CFBundleDevelopmentRegion"
[ "$(pb CFBundleName)" = Shanjie ] || fail "CFBundleName"
[ "$(pb CFBundleDisplayName)" = 善解輸入法 ] || fail "CFBundleDisplayName must equal the folder name"
[ "$(pb LSHasLocalizedDisplayName)" = true ] || fail "LSHasLocalizedDisplayName"
[ "$(pb CFBundleIdentifier)" = com.nyanako.inputmethod.shanjie ] || fail "bundle ID"
[ "$(pb InputMethodConnectionName)" = com.nyanako.inputmethod.shanjie_Connection ] || fail "InputMethodConnectionName"
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
for lp in "zh-Hant 善解輸入法" "en Shanjie"; do
  read -r l name <<<"$lp"
  S="$R/$l.lproj/InfoPlist.strings"
  plutil -lint "$S" >/dev/null || fail "$l InfoPlist.strings does not lint"
  for k in CFBundleName CFBundleDisplayName "$MODE"; do
    [ "$(pb "$k" "$S")" = "$name" ] || fail "$l InfoPlist.strings: $k"
  done
done
for f in mcbpmf-data.txt overlay-add.tsv sandhi-add.tsv names-add.tsv demote.tsv bigram.sjlm classes.sjc kn.sjkn trigram.sjt3 tinylm.sjtl shanjie.tiff \
         packs/acg-add.tsv packs/acg-sources.tsv packs/acg.json LICENSES/LICENSE LICENSES/McBopomofo-MIT.txt LICENSES/data.md LICENSES/CC-BY-SA-4.0-attribution.txt \
         english-words.txt LICENSES/SCOWL.txt LICENSES/SymSpell-MIT.txt LICENSES/english-words-attribution.txt \
         LICENSES/name-suggestion-index-BSD-3-Clause.txt LICENSES/Sparkle-MIT.txt; do
  [ -s "$R/$f" ] || fail "Resources/$f is missing or empty"
done
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
# Only an update test build with a feed on 127.0.0.1 may relax App Transport Security, and only for local networking.
if [ "$WANT_BUILD" = test ] && [[ "$WANT_FEED" == http://127.0.0.1* ]]; then
  sparkle_key NSAppTransportSecurity.NSAllowsLocalNetworking bool true
else
  ! plutil -extract NSAppTransportSecurity raw -o - "$PLIST" >/dev/null 2>&1 || fail "Info.plist sets NSAppTransportSecurity"
fi
sparkle_key SUFeedURL string "$WANT_FEED"
sparkle_key SUPublicEDKey string "$WANT_KEY"
sparkle_key SURequireSignedFeed bool true
sparkle_key SUVerifyUpdateBeforeExtraction bool true
sparkle_key SUAllowsAutomaticUpdates bool false
sparkle_key SUEnableAutomaticChecks bool false
sparkle_key SUEnableInstallerLauncherService bool false
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

# --- 3: signature: valid, no entitlements at all. The ad-hoc build has no hardened runtime (scripts/build-app.sh: library
# validation refuses the ad-hoc Sparkle.framework); release.yml's verify() requires it on the Developer ID build that ships.
codesign --verify --strict --deep "$APP" || fail "codesign --verify --strict --deep"
ENT="$(codesign -d --entitlements - "$APP" 2>/dev/null || true)"
[ -z "$ENT" ] || fail "the app carries entitlements"
# Sparkle (docs/contracts/about-update.md section 3.2): the framework is there without its XPC services, and none of the four
# code locations carries entitlements. A path that is gone fails rather than reading as "no entitlements".
SPARKLE="$APP/Contents/Frameworks/Sparkle.framework"
[ ! -e "$SPARKLE/Versions/B/XPCServices" ] && [ ! -e "$SPARKLE/XPCServices" ] || fail "Sparkle.framework still has XPCServices"
for code in "$APP" "$SPARKLE" "$SPARKLE/Versions/B/Autoupdate" "$SPARKLE/Versions/B/Updater.app"; do
  [ -e "$code" ] || fail "$code is missing"
  codesign --verify --strict "$code" || fail "codesign --verify --strict: $code"
  ENT="$(codesign -d --entitlements - "$code" 2>/dev/null || true)"
  [ -z "$ENT" ] || fail "$code carries entitlements"
done
echo "check 3: ok"

# --- 6: selftest and refused arguments, with no side effects
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
