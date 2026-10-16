#!/usr/bin/env bash
# Refuse to release a commit whose CI on main is not green (docs/contracts/s3b.md section 3).
# Adapted from syrtis's scripts/check_ci_gate.sh; shanjie has one CI workflow, ci.yml.
#
#   scripts/check-ci-gate.sh <commit-sha>
#
# CI does not run on tags, so release.yml runs this first: the newest push run of ci.yml on main
# for this exact commit must have concluded `success`. Waits while it is queued or running (up to
# CI_GATE_TIMEOUT s) and while it does not exist yet (up to CI_GATE_GRACE s: a tag pushed right
# after a merge can arrive first). Anything else fails, including three unreadable API answers in a
# row. A mistake guard, not a security boundary: the tagged commit carries this script.
#
# A commit whose only change against its single parent is appcast.xml is the feed release.yml's
# publish job writes to main with GITHUB_TOKEN (docs/contracts/about-update.md), and such a push
# starts no ci.yml run. The gate walks back over those commits (at most 20) and requires the CI run
# of the first other commit: appcast.xml is data no build or test reads.
#
# Needs GH_TOKEN and GITHUB_REPOSITORY. CI_GATE_GH replaces `gh` (scripts/test-check-ci-gate.sh).
set -euo pipefail

sha="${1:-}"
repo="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is not set}"
interval="${CI_GATE_INTERVAL:-30}"
timeout="${CI_GATE_TIMEOUT:-1800}"
grace="${CI_GATE_GRACE:-300}"

[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo "::error::expected a 40-character commit SHA"; exit 2; }
gh_cmd="${CI_GATE_GH:-gh}"

for _ in $(seq 20); do
  # "<parent> <true|false>": the single parent, and whether appcast.xml is the only file changed.
  info=$("$gh_cmd" api "repos/$repo/commits/$sha" \
    | jq -er 'if (.parents | length) == 1 then [.parents[0].sha, ([.files[].filename] == ["appcast.xml"])] else ["", false] end | @tsv') \
    || { echo "::error::could not read commit $sha"; exit 1; }
  IFS=$'\t' read -r parent only <<<"$info"
  [ "$only" = true ] && [[ "$parent" =~ ^[0-9a-f]{40}$ ]] || break
  echo "check-ci-gate: $sha changes only appcast.xml (no ci.yml run); using its parent $parent"
  sha="$parent"
done

recovery="Once ci.yml is green on main for $sha, re-run this Release run. A tag on the wrong commit needs a new tag on the merge commit."

start=$(date +%s)
api_failures=0
while :; do
  elapsed=$(( $(date +%s) - start ))
  if ! body=$("$gh_cmd" api "repos/$repo/actions/workflows/ci.yml/runs?head_sha=$sha&event=push&branch=main&per_page=100") \
     || ! line=$(jq -er --arg sha "$sha" '
          [.workflow_runs[] | select(.head_sha == $sha and .head_branch == "main" and .event == "push")]
          | if length == 0 then "none" else (sort_by(.created_at) | last | [.status, (.conclusion // "none"), .html_url] | @tsv) end' <<<"$body"); then
    api_failures=$((api_failures + 1))
    (( api_failures < 3 )) || { echo "::error::could not read ci.yml runs for $sha (3 attempts)"; echo "$recovery"; exit 1; }
    echo "check-ci-gate: API read failed; retrying (${api_failures}/3)"
    sleep "$interval"
    continue
  fi
  api_failures=0
  IFS=$'\t' read -r status conclusion url <<<"$line"
  if [[ "$status" == none ]]; then
    (( elapsed < grace )) || { echo "::error::no ci.yml push run on main for $sha after ${grace}s"; echo "$recovery"; exit 1; }
    echo "check-ci-gate: no ci.yml run for $sha yet (${elapsed}s)"
  elif [[ "$status" != completed ]]; then
    (( elapsed < timeout )) || { echo "::error::ci.yml for $sha still $status after ${timeout}s: $url"; echo "$recovery"; exit 1; }
    echo "check-ci-gate: ci.yml for $sha is $status (${elapsed}s): $url"
  elif [[ "$conclusion" == success ]]; then
    echo "check-ci-gate: ci.yml passed for $sha: $url"
    exit 0
  else
    echo "::error::ci.yml for $sha concluded '$conclusion': $url"
    echo "$recovery"
    exit 1
  fi
  sleep "$interval"
done
