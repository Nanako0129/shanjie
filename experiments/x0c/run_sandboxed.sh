#!/bin/bash
# X0c (docs/contracts/x0c-compare.md section 8, F4): every run that loads the third-party model goes through here.
# The venv's Python runs under sandbox-exec. Denied (measured, research log 2026-10-13 X0c): the network; reads of the
# private folder, the HackMD key and ~/.claude (the other key and config paths in PROFILE use the same rule but were not
# probed one by one; files named with --read are allowed); writes
# anywhere except --out and /dev/null; executing any other program; looking up the LaunchServices and pasteboard services
# (so the process cannot ask another one to open a URL or hand over data). Sending Apple events is denied in PROFILE too
# but not measured (a probe would have to send one to a real app). Everything else is allowed (`allow default`): this is
# a list of denials, not an allow-list.
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
  (subpath $(q "$H/Library/Keychains")) (subpath $(q "$H/.codex")) (subpath $(q "$H/.grok")) (literal $(q "$H/side-project/.hackmd_key"))
  (subpath $(q "$H/.claude")) (subpath $(q "$H/.gnupg")) (subpath $(q "$H/.aws")) (literal $(q "$H/.netrc")))
$ALLOW_READS
(deny file-write*)
(allow file-write* (subpath $(q "$OUT")) (literal $(q /dev/null)))
(deny appleevent-send)
(deny mach-lookup (global-name $(q com.apple.coreservices.launchservicesd)) (global-name-prefix $(q com.apple.pasteboard)))
(deny process-exec* (require-not (literal $(q "$PY") $(q "$REAL_PY"))))"
# The venv path, not the real binary: Python finds the venv (pyvenv.cfg) from the path it was started with.
exec sandbox-exec -p "$PROFILE" "$PY" -I -B "$@"
