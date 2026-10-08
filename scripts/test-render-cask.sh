#!/usr/bin/env bash
# Checks scripts/render-cask.sh (docs/contracts/s3b.md section 14.5, acceptance 2): good arguments
# change only the version and sha256 lines; every bad argument or template exits 1 with no output.
# Then (section 15.3) the cask's postflight step as Homebrew parses it, and its process pattern.
#
#   scripts/test-render-cask.sh
set -euo pipefail

fail() { echo "FAIL: $*" >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
R="$ROOT/scripts/render-cask.sh"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
SHA=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef

# Good arguments: the output is the template with exactly those two lines replaced, nothing else
# added or removed.
"$R" 1.2.3 "$SHA" > "$T/out.rb"
sed -e 's/^  version "[^"]*"$/  version "1.2.3"/' -e "s/^  sha256 \"[^\"]*\"\$/  sha256 \"$SHA\"/" \
  "$ROOT/packaging/Casks/shanjie.rb" > "$T/expected.rb"
cmp -s "$T/expected.rb" "$T/out.rb" || fail "output is not the template with two lines replaced: $(diff "$T/expected.rb" "$T/out.rb")"
[ "$(diff "$ROOT/packaging/Casks/shanjie.rb" "$T/out.rb" | grep -c '^[<>]')" -eq 4 ] \
  || fail "expected exactly two lines to change"
grep -qx '  version "1.2.3"' "$T/out.rb" || fail "version line not replaced"
grep -qx "  sha256 \"$SHA\"" "$T/out.rb" || fail "sha256 line not replaced"

# Bad arguments and templates: exit 1, nothing on stdout.
refuses() {  # refuses <label> <args...>: exit code exactly 1, empty stdout
  local label="$1" rc=0; shift
  "$@" > "$T/stdout" 2>/dev/null || rc=$?
  [ "$rc" -eq 1 ] || fail "$label: exit code $rc, expected 1"
  [ ! -s "$T/stdout" ] || fail "$label: printed output"
}
refuses "no arguments" "$R"
refuses "one argument" "$R" 1.2.3
refuses "three arguments" "$R" 1.2.3 "$SHA" x
refuses "v prefix" "$R" v1.2.3 "$SHA"
refuses "prerelease" "$R" 1.2.3-beta "$SHA"
refuses "two-part version" "$R" 1.2 "$SHA"
refuses "uppercase sha" "$R" 1.2.3 "$(tr a-f A-F <<<"$SHA")"
refuses "short sha" "$R" 1.2.3 "${SHA:1}"
refuses "sha with quote" "$R" 1.2.3 "${SHA:1}\""
grep -v '^  version ' "$ROOT/packaging/Casks/shanjie.rb" > "$T/no-version.rb"
refuses "template without version" env SHANJIE_CASK_TEMPLATE="$T/no-version.rb" "$R" 1.2.3 "$SHA"
{ cat "$ROOT/packaging/Casks/shanjie.rb"; echo '  sha256 "x"'; } > "$T/two-sha.rb"
refuses "template with two sha256 lines" env SHANJIE_CASK_TEMPLATE="$T/two-sha.rb" "$R" 1.2.3 "$SHA"
refuses "missing template" env SHANJIE_CASK_TEMPLATE="$T/none.rb" "$R" 1.2.3 "$SHA"

# The postflight step (section 15) as Homebrew itself parses the rendered cask: exactly one
# terminate_process, in postflight_steps (after the new bundle is in place) and with match "full"
# (without it Homebrew runs killall, which matches nothing).
"$R" 0.0.0 "$(printf '0%.0s' $(seq 64))" > "$T/cask.rb"
PAT="$(HOMEBREW_NO_AUTO_UPDATE=1 brew ruby -e '
  cask = Cask::CaskLoader::FromContentLoader.new(File.read(ARGV[0])).load(config: nil)
  kills = cask.artifacts.grep(Cask::Artifact::PostflightSteps).flat_map(&:steps)
              .select { |step| step["type"] == "terminate_process" }
  abort "expected one terminate_process in postflight_steps, found #{kills.size}" unless kills.size == 1
  abort "terminate_process must use match: :full" unless kills.first["match"] == "full"
  puts kills.first["name"]' "$T/cask.rb")" || fail "the cask's postflight step (message above)"

# Its pattern, through pgrep, which matches like the pkill Homebrew runs: stand-in processes (cat on
# a pipe, command line set with exec -a) that look like a running input method match, including a
# system-wide one (--input-methoddir=/Library/Input Methods); ones that only mention its path, the
# install subcommand and the previous copy kept by install-ime.sh (the renamed bundle) do not.
IM="$T/Library/Input Methods"
cases=("yes|$IM/善解輸入法.app/Contents/MacOS/shanjie"
       "yes|$IM/shanjie.app/Contents/MacOS/shanjie"
       "yes|/Library/Input Methods/善解輸入法.app/Contents/MacOS/shanjie"
       "no|$IM/善解輸入法.app/Contents/MacOS/shanjie install"
       "no|/usr/bin/editor $IM/善解輸入法.app/Contents/MacOS/shanjie"
       "no|$IM/.shanjie-previous/Contents/MacOS/shanjie")
pids=()
for c in "${cases[@]}"; do
  sleep 30 2>/dev/null | (exec -a "${c#*|}" cat) &
  pids+=("$!")
done
trap 'kill "${pids[@]}" 2>/dev/null || true; rm -rf "$T"' EXIT
for i in "${!cases[@]}"; do  # wait until each stand-in has exec'd (UTF-8, or ps escapes the name)
  for _ in $(seq 50); do
    [ "$(LC_ALL=en_US.UTF-8 ps -o command= -p "${pids[$i]}")" = "${cases[$i]#*|}" ] && continue 2
    sleep 0.1
  done
  fail "stand-in '${cases[$i]#*|}' did not start"
done
found=" $(pgrep -f "$PAT" | tr '\n' ' ' || true)"
for i in "${!cases[@]}"; do
  want="${cases[$i]%%|*}"; got=no
  [ "${found/ ${pids[$i]} /}" = "$found" ] || got=yes
  [ "$got" = "$want" ] || fail "pattern match for '${cases[$i]#*|}': $got, expected $want"
done

echo "render-cask.sh: ok"
