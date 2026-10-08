#!/bin/bash
# Acceptance checks 2, 3 and 6 of docs/contracts/s3b.md section 10 on a built app (CI runs this
# after scripts/build-app.sh). Read-only towards the system: the input source list is only read
# (TISCreateInputSourceList), never registered or enabled, and the app is never launched as an
# input method: it runs only with `--selftest` and with arguments it must refuse.
#
#   scripts/check-app.sh [build/善解輸入法.app]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="${1:-$ROOT/build/善解輸入法.app}"
BIN="$APP/Contents/MacOS/shanjie"
PLIST="$APP/Contents/Info.plist"
fail() { echo "error: $*" >&2; exit 1; }
pb() { /usr/libexec/PlistBuddy -c "Print :$1" "${2:-$PLIST}" 2>/dev/null; }
MODE=com.nyanako.inputmethod.shanjie.zhuyin

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
assert "acg-add.tsv" in files, "acg-add.tsv is not in the manifest"
for name, meta in files.items():
    data = open(os.path.join(d, name), "rb").read()
    assert hashlib.sha256(data).hexdigest() == meta["sha256"] and len(data) == meta["bytes"], name
PY
grep -q 'packs/acg-add.tsv' "$R/LICENSES/CC-BY-SA-4.0-attribution.txt" || fail "the attribution file does not list packs/acg-add.tsv"
echo "check 2: ok"

# --- 3: signature: valid, hardened runtime, no entitlements at all
codesign --verify --strict --deep "$APP" || fail "codesign --verify --strict --deep"
ENT="$(codesign -d --entitlements - "$APP" 2>/dev/null || true)"
[ -z "$ENT" ] || fail "the app carries entitlements"
INFO="$(codesign -dv "$APP" 2>&1)"
grep -Eq 'flags=0x[0-9a-f]+\([^)]*runtime' <<<"$INFO" || fail "hardened runtime flag missing"
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
