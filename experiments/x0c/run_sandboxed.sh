#!/bin/bash
# X0c (docs/contracts/x0c-compare.md section 8, F4): every run that loads the third-party model goes through here.
# The venv's Python runs under sandbox-exec with no network, no reads of the private folder, keys or configs (except
# the files named with --read), no writes under $HOME except --out, and no other program may be executed.
#   experiments/x0c/run_sandboxed.sh --out DIR [--read FILE]... -- <python arguments>
# Denials return a permission error (no SIGKILL: sbench.md 3.7 notes a killed process pops a macOS crash report).
set -euo pipefail
PY="$HOME/.cache/shanjie/x0c/venv/bin/python"
OUT="" READS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    --read) READS+=("$(cd "$(dirname "$2")" && pwd -P)/$(basename "$2")"); shift 2 ;;
    --) shift; break ;;
    *) echo "usage: $0 --out DIR [--read FILE]... -- <python arguments>" >&2; exit 2 ;;
  esac
done
[ -n "$OUT" ] || { echo "--out is required" >&2; exit 2; }
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd -P)"
H="$(cd "$HOME" && pwd -P)"
REAL_PY="$(realpath "$PY")"
for p in "$OUT" "$PY" "$REAL_PY" "${READS[@]+"${READS[@]}"}"; do
  case "$p" in *'"'*|*'\'*) echo "path with a quote or backslash: $p" >&2; exit 2 ;; esac
done
q() { printf '"%s"' "$1"; }
ALLOW_READS=""
for r in "${READS[@]+"${READS[@]}"}"; do ALLOW_READS+="(allow file-read* (literal $(q "$r")))"; done
PROFILE="(version 1)(allow default)
(deny network*)
(deny file-read* (subpath $(q "$H/side-project/shanjie-private")) (subpath $(q "$H/.ssh")) (subpath $(q "$H/.config"))
  (subpath $(q "$H/Library/Keychains")) (subpath $(q "$H/.codex")) (subpath $(q "$H/.grok")) (literal $(q "$H/side-project/.hackmd_key")))
$ALLOW_READS
(deny file-write* (subpath $(q "$H")))
(allow file-write* (subpath $(q "$OUT")))
(deny process-exec* (require-not (literal $(q "$PY") $(q "$REAL_PY"))))"
# The venv path, not the real binary: Python finds the venv (pyvenv.cfg) from the path it was started with.
exec sandbox-exec -p "$PROFILE" "$PY" -I -B "$@"
