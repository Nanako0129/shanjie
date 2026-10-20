#!/bin/bash
# Prints the version linked into the installer executable as __TEXT,__shanjie_ver (docs/contracts/system-install.md
# section 2.3 step 6; scripts/build-installer.sh writes it). Reads the file only; never runs it. Prints nothing when the
# section is absent.
#
#   scripts/installer-version.sh <installer executable>
set -euo pipefail
[ "$#" -eq 1 ] && [ -f "$1" ] || { echo "usage: $0 <installer executable>" >&2; exit 1; }
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT
# segedit -extract writes the section's bytes to a new file and leaves the executable as it is.
segedit "$1" -extract __TEXT __shanjie_ver "$OUT/v" 2>/dev/null || exit 0
tr -d '\0' < "$OUT/v"
