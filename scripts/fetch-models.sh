#!/bin/bash
# Downloads the model files into data/lm and checks them against data/*.sha256: the one copy of this step for every
# ci.yml job (docs/spec/release-and-ci.md). release.yml keeps its own.
#
# The bigram model is not in git (docs/spec/models.md); it is the sj-lm-7-9.7m release asset, its word-class table (S2k) is the
# sj-wc-6-512c asset and its Kneser-Ney side file (kn-core) is the sj-kn-2-397k asset. The small model of 語意校正 (S7c-2b) is the
# sj-sr-8-15m asset (會意 SJ-SR-8-15M, SJTL0004: it records sj-lm-7-9.7m's and sj-kn-2-397k's hashes). The trigram tables of the formal
# and chat profiles (trigram-core) are the sj-cx-2-7.7m and sj-cx-2-950k assets (both bound to sj-lm-7-9.7m's hash). The colloquial table (常談 SJ-CL-2-69K,
# docs/spec/semantic-rerank.md §4) and its attribution list are the sj-cl-2-69k assets.
# Checking them first fails a job in seconds with a clear message instead of after the build. The hash files are also
# read by build-app.sh and release.yml; cli/tests/golden.rs repeats the first two, so a model switch updates both
# (docs/spec/models.md §11).
#
# Needs gh with a token (GH_TOKEN in Actions). The repository is $GITHUB_REPOSITORY, which Actions sets.
set -euo pipefail
cd "$(dirname "$0")/.."
get() { gh release download "$1" --repo "$GITHUB_REPOSITORY" -D data/lm "${@:2}"; }
get sj-lm-7-9.7m -p bigram.sjlm
get sj-wc-6-512c -p classes.sjc
get sj-kn-2-397k -p kn.sjkn
get sj-sr-8-15m -p tinylm.sjtl
get sj-cx-2-7.7m -p trigram.sjt3
get sj-cx-2-950k -p trigram-chat.sjt3
get sj-cl-2-69k -p colloquial.sjlm -p colloquial-attribution.tsv
# macOS and Linux have shasum; Git Bash on the Windows runner may have only sha256sum (docs/spec/windows-tip.md §2.9). Same check file format.
if command -v shasum >/dev/null 2>&1; then check=(shasum -a 256 -c); else check=(sha256sum -c); fi
"${check[@]}" data/bigram.sjlm.sha256 data/classes.sjc.sha256 data/kn.sjkn.sha256 data/tinylm.sjtl.sha256 \
  data/trigram.sjt3.sha256 data/trigram-chat.sjt3.sha256 data/colloquial.sjlm.sha256 data/colloquial-attribution.tsv.sha256
