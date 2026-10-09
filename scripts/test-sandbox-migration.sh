#!/bin/bash
# docs/contracts/app-sandbox.md section 2.6 step 3: the container migration, on a throwaway CI runner
# only. It runs the shipping-ID build and a build with no migration list, both sandboxed, against
# synthetic learning data and preferences placed where the unsandboxed versions keep them, and
# asserts:
#   - no migration list, no move: the no-list build's container appears, the data stays put;
#   - the shipping build moves the learning folder (same bytes, 0700/0600, the do-not-back-up flag)
#     and the preferences (layout=eten, written with `defaults` before the first launch, as the
#     installer does, section 2.7) into its container;
#   - only once: a second launch moves nothing.
#
#   CI=true scripts/test-sandbox-migration.sh <善解輸入法.app (shipping ID)> <no-list build>
#
# Refuses, exits non-zero, writes nothing and launches nothing, checked in this order: CI is not
# `true`; HOME is not this account's home directory (from dscl, as install-ime.sh does: the system
# migrates into the account's home, not $HOME); or, in that home, any of the learning folder, the
# shipping preferences file, the shipping container or the no-list build's container already exists.
# Never run it on a machine whose data matters: on success the learning data and the preferences
# are inside ~/Library/Containers/com.nyanako.inputmethod.shanjie.
set -euo pipefail

fail() { echo "FAIL: $*" >&2; exit 1; }
ok() { echo "ok: $*"; }
[ "$#" -eq 2 ] || fail "usage: CI=true $0 <shipping app> <no-list app>"
SHIP="$1"
NOLIST="$2"
SHIPPING_ID=com.nyanako.inputmethod.shanjie

# --- refusals, before anything is written or launched
[ "${CI:-}" = true ] || fail "refused: CI is not true. This test moves the account's real learning data into a sandbox container; it runs only on a throwaway CI runner."
ACCOUNT_HOME="$(dscl . -read "/Users/$(id -un)" NFSHomeDirectory 2>/dev/null | awk '{print $2}')"
[ -n "$ACCOUNT_HOME" ] && [ "$(cd "$HOME" 2>/dev/null && pwd -P)" = "$(cd "$ACCOUNT_HOME" 2>/dev/null && pwd -P)" ] \
  || fail "refused: HOME ($HOME) is not this account's home directory ($ACCOUNT_HOME); the system migrates into the account's home, so a temporary HOME would not isolate anything."
pb() { /usr/libexec/PlistBuddy -c "Print :$1" "$2" 2>/dev/null; }
NOLIST_ID="$(pb CFBundleIdentifier "$NOLIST/Contents/Info.plist")" || fail "refused: no bundle ID in $NOLIST"
AS="$HOME/Library/Application Support/shanjie"
PREFS="$HOME/Library/Preferences/$SHIPPING_ID.plist"
CS="$HOME/Library/Containers/$SHIPPING_ID"
CN="$HOME/Library/Containers/$NOLIST_ID"
for p in "$AS" "$PREFS" "$CS" "$CN"; do
  [ ! -e "$p" ] || fail "refused: $p already exists"
done
[ "$(pb CFBundleIdentifier "$SHIP/Contents/Info.plist")" = "$SHIPPING_ID" ] || fail "refused: $SHIP is not the shipping-ID build"
[ -f "$SHIP/Contents/Resources/container-migration.plist" ] || fail "refused: $SHIP has no container-migration.plist"
[ "$NOLIST_ID" != "$SHIPPING_ID" ] || fail "refused: $NOLIST has the shipping ID"
[ ! -e "$NOLIST/Contents/Resources/container-migration.plist" ] || fail "refused: $NOLIST has a container-migration.plist"
ok "refusal checks passed: CI=true, HOME is the account home, none of the four paths exists (the $NOLIST_ID container did not exist before)"

# state <learning folder>: the folder's mode, then each file's mode and SHA-256.
state() {
  local d="$1" f
  echo ". $(stat -f %Lp "$d")"
  for f in learning.tsv learning.tsv.corrupt; do
    echo "$f $(stat -f %Lp "$d/$f") $(shasum -a 256 < "$d/$f" | cut -d ' ' -f 1)"
  done
}
# seed <text>: a learning folder as the unsandboxed versions leave it.
seed() {
  mkdir -p "$HOME/Library/Application Support"
  mkdir -m 700 "$AS"
  printf '# synthetic (%s)\n善解\tㄕㄢˋ ㄐㄧㄝˇ\t3\n' "$1" > "$AS/learning.tsv"
  printf 'corrupt synthetic (%s)\n' "$1" > "$AS/learning.tsv.corrupt"
  chmod 600 "$AS/learning.tsv" "$AS/learning.tsv.corrupt"
}
# Captured first: `tmutil | grep -q` under pipefail can fail on SIGPIPE.
excluded() { local out; out="$(tmutil isexcluded "$1")" && grep -q '^\[Excluded\]' <<<"$out"; }
run_selftest() {  # run_selftest <app>
  local rc=0
  "$1/Contents/MacOS/shanjie" --selftest || rc=$?
  [ "$rc" -eq 0 ] || fail "--selftest of $1 exited $rc"
}
# listing <dir>: every learning file under a container; fails (instead of reporting nothing) when
# the directory cannot be read, e.g. denied by TCC. Assign it to a variable (errexit then stops the
# script); inside `[ -z "$(listing ...)" ]` a failure would read as "nothing found".
listing() {
  local out
  out="$(find "$1" -name 'learning.tsv*' 2>&1)" || fail "cannot read $1: $out"
  printf '%s' "$out"
}

# --- sources
seed first
tmutil addexclusion "$AS"
excluded "$AS" || fail "could not set the do-not-back-up flag on the source"
defaults write "$SHIPPING_ID" layout eten
for _ in $(seq 20); do [ "$(pb layout "$PREFS")" = eten ] && break; sleep 0.5; done
[ "$(pb layout "$PREFS")" = eten ] || fail "defaults write did not reach $PREFS"
BEFORE="$(state "$AS")"
echo "source:"; echo "$BEFORE"
ok "sources placed: learning folder (excluded from backup), $PREFS with layout=eten"

# --- no migration list, no move (section 1's inference)
run_selftest "$NOLIST"
[ -d "$CN" ] || fail "no container for $NOLIST_ID after its first run: the sandbox was not applied"
ok "$NOLIST_ID: container absent before, present after (sandboxed)"
[ "$(state "$AS")" = "$BEFORE" ] || fail "STOP CONDITION: the no-list build changed the learning folder (moved data without a migration list)"
[ "$(pb layout "$PREFS")" = eten ] || fail "STOP CONDITION: the no-list build changed the shipping preferences"
FOUND="$(listing "$CN")"
[ -z "$FOUND" ] || fail "STOP CONDITION: learning data inside the $NOLIST_ID container: $FOUND"
ok "no list, no move: source byte for byte the same, preferences in place, no learning data in $CN"

# --- migration by the shipping build
run_selftest "$SHIP"
[ -d "$CS" ] || fail "no container for $SHIPPING_ID after its first run"
MOVED="$CS/Data/Library/Application Support/shanjie"
[ ! -e "$AS" ] || fail "the learning folder is still at its source after the migration"
[ -d "$MOVED" ] || fail "no learning folder in the container; it contains: $(listing "$CS")"
[ "$(state "$MOVED")" = "$BEFORE" ] || fail "the moved learning folder differs: $(state "$MOVED")"
excluded "$MOVED" || fail "the do-not-back-up flag was lost in the move"
CPREFS="$CS/Data/Library/Preferences/$SHIPPING_ID.plist"
[ "$(pb layout "$CPREFS")" = eten ] || fail "the container's preferences do not have layout=eten"
[ ! -e "$PREFS" ] || fail "the source preferences file is still there after the migration"
ok "migration: source gone; in the container the same bytes, 0700/0600, excluded from backup; container preferences layout=eten; source preferences file gone"

# --- only once
seed second
SECOND="$(state "$AS")"
run_selftest "$SHIP"
[ "$(state "$AS")" = "$SECOND" ] || fail "a second launch changed the new source"
[ "$(state "$MOVED")" = "$BEFORE" ] || fail "a second launch changed the container's learning folder"
ok "only once: the second launch left the new source and the container as they were"

[ ! -e "$PREFS" ] || fail "$PREFS came back after the test (cfprefsd)"
ok "end: $PREFS does not exist"
echo "sandbox migration: ok"
