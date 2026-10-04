#!/usr/bin/env bash
# Decides whether ci.yml's macOS jobs have anything to test. Reads the changed paths on stdin, one
# per line, and prints `run=true` unless every path is documentation or the website, which no build
# or test reads. LICENSES/ is bundled into the app (scripts/build-app.sh), so it always counts.
# No input means run: an empty or unreadable diff never skips the tests.
#
#   git diff --name-only BASE...HEAD | scripts/ci-changes.sh >> "$GITHUB_OUTPUT"
set -euo pipefail

run=false
seen=false
while IFS= read -r f; do
  [ -n "$f" ] || continue
  seen=true
  case "$f" in
    LICENSES/*) run=true ;;
    docs/* | site/* | scripts/deploy-site.sh | *.md) ;;
    *) run=true ;;
  esac
done
[ "$seen" = true ] || run=true
echo "run=$run"
