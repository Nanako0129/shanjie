#!/usr/bin/env bash
# Decides whether ci.yml's test jobs (macOS and Linux) have anything to test. Reads the changed paths on stdin, one
# per line, and prints `run=true` unless every path is documentation (docs/, specs included), the website
# (site/, its data files included) or a cross-version benchmark record (eval/bench/results/ and eval/bench/kpi/,
# read only by `tools/bench.py` and `tools/kpi.py`, which CI never runs), which no build or test reads.
# LICENSES/ is bundled into the app (scripts/build-app.sh), so it always counts.
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
    docs/* | docs/spec/* | site/* | site/data/* | scripts/deploy-site.sh | *.md | eval/bench/results/* | eval/bench/kpi/*) ;;
    *) run=true ;;
  esac
done
[ "$seen" = true ] || run=true
echo "run=$run"
