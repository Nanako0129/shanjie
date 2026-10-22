#!/usr/bin/env bash
# docs/contracts/about-models.md acceptance 2 and 4: scripts/check-models.sh on fake folders (temp dirs only).
#
#   scripts/test-check-models.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
C="$ROOT/scripts/check-models.sh"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
sha() { shasum -a 256 "$1" | awk '{ print $1 }'; }

# A fake repo: three tiny models, their data/<file>.sha256, data/lm/<file> and data/models.tsv.
mk() {
  rm -rf "$T/r"; mkdir -p "$T/r/data/lm"
  : >| "$T/r/data/models.tsv"
  for f in a.sjlm b.sjc c.sjtl; do
    echo "model $f" >| "$T/r/data/lm/$f"
    echo "$(sha "$T/r/data/lm/$f")  data/lm/$f" >| "$T/r/data/$f.sha256"
    printf '%s\t%s\t名%s\tSJ-X-1-1\n' "$f" "$(sha "$T/r/data/lm/$f")" "$f" >> "$T/r/data/models.tsv"
  done
}
# expect ok|bad NAME CMD...: bad means a non-zero exit with a message naming the file.
expect() {
  local want="$1" name="$2" file="$3"; shift 3
  if "$@" >|"$T/out" 2>&1; then
    [ "$want" = ok ] || fail "$name: passed, expected a failure"
  else
    [ "$want" = bad ] || fail "$name: failed, expected a pass: $(cat "$T/out")"
    grep -q "$file" "$T/out" || fail "$name: the message does not name $file: $(cat "$T/out")"
  fi
  echo "ok: $name"
}
HASH0=0000000000000000000000000000000000000000000000000000000000000000

mk
expect ok "build: as is" - "$C" build "$T/r"
mk; python3 -c 'import re,sys; p=sys.argv[1]; s=open(p).read(); open(p,"w").write(re.sub(r"(b\.sjc\t)[0-9a-f]{64}", r"\g<1>"+sys.argv[2], s))' "$T/r/data/models.tsv" "$HASH0"
grep -q "$HASH0" "$T/r/data/models.tsv" || fail "fixture: the row hash was not changed"
expect bad "build: row hash != data/*.sha256" b.sjc "$C" build "$T/r"
mk; echo changed >| "$T/r/data/lm/c.sjtl"
expect bad "build: data/lm file differs from the row" c.sjtl "$C" build "$T/r"
mk; rm "$T/r/data/lm/a.sjlm"
expect bad "build: data/lm file missing" a.sjlm "$C" build "$T/r"
mk; printf 'a.sjlm\tx\n' >| "$T/r/data/models.tsv"
expect bad "build: row with the wrong field count" models.tsv "$C" build "$T/r"

mk; printf 'x' >> "$T/r/data/models.tsv"; truncate -s -2 "$T/r/data/models.tsv"
expect bad "build: no final newline" models.tsv "$C" build "$T/r"
mk; python3 -c 'import sys; p=sys.argv[1]; s=open(p).read(); open(p,"w").write(s.replace("\t","\t\t",1))' "$T/r/data/models.tsv"
expect bad "build: an extra tab" models.tsv "$C" build "$T/r"
mk; python3 -c 'import sys; p=sys.argv[1]; s=open(p).read(); open(p,"w").write("\t"+s)' "$T/r/data/models.tsv"
expect bad "build: a leading tab" models.tsv "$C" build "$T/r"
mk; python3 -c 'import sys; p=sys.argv[1]; s=open(p).read(); open(p,"w").write(s.replace("\n","\r\n"))' "$T/r/data/models.tsv"
expect bad "build: CRLF" "holds a CR" "$C" build "$T/r"
mk; python3 -c 'import sys; p=sys.argv[1]; s=open(p).read(); a=s.split("\t"); a[2]=""; open(p,"w").write("\t".join(a))' "$T/r/data/models.tsv"
expect bad "build: an empty field" models.tsv "$C" build "$T/r"

# The real repo's own list passes (data/lm is present in a built worktree; skipped where the models are not downloaded).
if [ -f "$ROOT/data/lm/bigram.sjlm" ]; then expect ok "build: the repo's models.tsv" - "$C" build "$ROOT"; fi

# A fake Resources folder.
mkb() { mk; mkdir "$T/r/Res"; cp "$T/r/data/lm/"* "$T/r/data/models.tsv" "$T/r/Res/"; }
mkb
expect ok "bundle: as is" - "$C" bundle "$T/r/Res" "$T/r"
mkb; echo extra >| "$T/r/Res/d.sjkn"
expect bad "bundle: model file without a row" d.sjkn "$C" bundle "$T/r/Res" "$T/r"
mkb; python3 -c 'import re,sys; p=sys.argv[1]; s=open(p).read(); open(p,"w").write(re.sub(r"(b\.sjc\t)[0-9a-f]{64}", r"\g<1>"+sys.argv[2], s))' "$T/r/Res/models.tsv" "$HASH0"
expect bad "bundle: row hash != data/*.sha256" b.sjc "$C" bundle "$T/r/Res" "$T/r"
mkb; rm "$T/r/Res/b.sjc"
expect bad "bundle: row whose file is missing" b.sjc "$C" bundle "$T/r/Res" "$T/r"
mkb; echo changed >| "$T/r/Res/a.sjlm"
expect bad "bundle: hash mismatch" a.sjlm "$C" bundle "$T/r/Res" "$T/r"
