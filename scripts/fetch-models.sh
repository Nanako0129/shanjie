#!/bin/bash
# Downloads the model files into data/lm and checks them against data/*.sha256: the one copy of this step for every
# ci.yml job (docs/contracts/ci-speed-2.md). release.yml keeps its own.
#
# The bigram model is not in git (docs/PLAN.md S2c); it is the model-v6 release asset, its word-class table (S2k) is the
# classes-v5 asset and its Kneser-Ney side file (kn-core) is the kn-v1 asset. The small model of 語意校正 (S7c-2b) is the
# sj-sr-6-14m asset (會意 SJ-SR-6-14M, SJTL0004: it records model-v6's and kn-v1's hashes). The trigram table of the formal
# profile (trigram-core) is the sj-cx-1-7.7m asset (bound to model-v6's hash). The colloquial table (常談 SJ-CL-1-68K,
# colloquial-term.md) and its attribution list are the sj-cl-1-68k assets.
# Checking them first fails a job in seconds with a clear message instead of after the build. The hash files are also
# read by build-app.sh and release.yml; cli/tests/golden.rs repeats the first two, so a model switch updates both
# (docs/data-files.md).
#
# Needs gh with a token (GH_TOKEN in Actions). The repository is $GITHUB_REPOSITORY, which Actions sets.
set -euo pipefail
cd "$(dirname "$0")/.."
get() { gh release download "$1" --repo "$GITHUB_REPOSITORY" -D data/lm "${@:2}"; }
get model-v6 -p bigram.sjlm
get classes-v5 -p classes.sjc
get kn-v1 -p kn.sjkn
get sj-sr-6-14m -p tinylm.sjtl
get sj-cx-1-7.7m -p trigram.sjt3
get sj-cl-1-68k -p colloquial.sjlm -p colloquial-attribution.tsv
shasum -a 256 -c data/bigram.sjlm.sha256 data/classes.sjc.sha256 data/kn.sjkn.sha256 data/tinylm.sjtl.sha256 \
  data/trigram.sjt3.sha256 data/colloquial.sjlm.sha256 data/colloquial-attribution.tsv.sha256
