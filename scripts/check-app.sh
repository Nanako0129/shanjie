#!/bin/bash
# Acceptance checks 2, 3 and 6 of docs/contracts/s3b.md section 10 on a built app (CI runs this
# after scripts/build-app.sh). Read-only towards the system: the input source list is only read
# (TISCreateInputSourceList), never registered or enabled, and the app is never launched as an
# input method: it runs only with `--selftest` and with arguments it must refuse.
#
# app-sandbox.md section 2.3: the bundle is sandboxed, so check 6 creates the bundle ID's container
# on its first run, and for the shipping ID moves the user's learning data and preferences into it.
# Outside CI (CI is not `true`) check 6 therefore refuses a shipping-ID build and exits non-zero: an
# ad-hoc local build must not create the shipping container first. SKIP_RUN=1 runs checks 2 and 3
# only, never executes the bundle, and exits 0 when they pass (any ID).
#
#   [EXPECTED_BUNDLE_ID=<id>] [SKIP_RUN=1] scripts/check-app.sh [app]
#
# EXPECTED_BUNDLE_ID defaults to the shipping ID; the folder and names follow from it (the table in
# app-sandbox.md section 2.3, the same as scripts/build-app.sh). The app defaults to build/<folder>.
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

# --- 2: Info.plist and Resources (with section 13: one input mode, the folder and the zh-Hant / en
# names; with app-sandbox.md section 2.2: the migration list only for the shipping ID)
[ "$(basename "$APP")" = "$NAME_ZH.app" ] || fail "the bundle folder is not $NAME_ZH.app"
plutil -lint "$PLIST" >/dev/null || fail "Info.plist does not lint"
[ "$(pb CFBundleDevelopmentRegion)" = en ] || fail "CFBundleDevelopmentRegion"
[ "$(pb CFBundleName)" = "$NAME_EN" ] || fail "CFBundleName"
[ "$(pb CFBundleDisplayName)" = "$NAME_ZH" ] || fail "CFBundleDisplayName must equal the folder name"
[ "$(pb LSHasLocalizedDisplayName)" = true ] || fail "LSHasLocalizedDisplayName"
[ "$(pb CFBundleIdentifier)" = "$ID" ] || fail "bundle ID is not $ID"
[ "$(pb InputMethodConnectionName)" = "${ID}_Connection" ] || fail "InputMethodConnectionName"
if [ "$ID" = "$SHIPPING_ID" ]; then
  [ "$(pb Move:0 "$MIGRATION")" = '${ApplicationSupport}/shanjie' ] && ! pb Move:1 "$MIGRATION" >/dev/null \
    || fail "container-migration.plist is not exactly Move = [\${ApplicationSupport}/shanjie]"
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
for lp in "zh-Hant $NAME_ZH" "en $NAME_EN"; do
  read -r l name <<<"$lp"
  S="$R/$l.lproj/InfoPlist.strings"
  plutil -lint "$S" >/dev/null || fail "$l InfoPlist.strings does not lint"
  for k in CFBundleName CFBundleDisplayName "$MODE"; do
    [ "$(pb "$k" "$S")" = "$name" ] || fail "$l InfoPlist.strings: $k"
  done
done
for f in mcbpmf-data.txt overlay-add.tsv sandhi-add.tsv demote.tsv bigram.sjlm classes.sjc shanjie.tiff \
         packs/acg-add.tsv packs/acg-sources.tsv packs/acg.json LICENSES/LICENSE LICENSES/McBopomofo-MIT.txt LICENSES/data.md LICENSES/CC-BY-SA-4.0-attribution.txt; do
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
echo "check 2: ok"

# --- 3: signature: valid, hardened runtime, exactly the two sandbox entitlements (app-sandbox.md
# section 2.1)
codesign --verify --strict --deep "$APP" || fail "codesign --verify --strict --deep"
"$ROOT/scripts/check-entitlements.sh" "$APP" || fail "entitlements"
INFO="$(codesign -dv "$APP" 2>&1)"
grep -Eq 'flags=0x[0-9a-f]+\([^)]*runtime' <<<"$INFO" || fail "hardened runtime flag missing"
echo "check 3: ok"

if [ "${SKIP_RUN:-}" = 1 ]; then
  echo "check 6: skipped (SKIP_RUN=1; the bundle was not run)"
  exit 0
fi
if [ "${CI:-}" != true ] && [ "$(pb CFBundleIdentifier)" = "$SHIPPING_ID" ]; then
  fail "check 6 refused: running a shipping-ID build outside CI would create ~/Library/Containers/$SHIPPING_ID from an ad-hoc signature and move the learning data and preferences into it (app-sandbox.md section 2.3). Use SKIP_RUN=1 for checks 2 and 3."
fi
# --- 6: selftest and refused arguments, with no side effects. The sandbox container the first run
# creates is not part of this comparison; CI checks it before and after separately (app-sandbox.md
# section 2.6).
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
