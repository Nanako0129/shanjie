#!/bin/bash
# The six evaluation runs of docs/spec/windows-tip.md §2.9, one copy for the Linux `debug` job and the `windows` job so both run
# the same commands: dev302, the typing test and the user-reported rows, each under the chat and the formal profile, with the shipping flags
# (context, the acg and it packs, the small model; the chat runs add the colloquial table). One rowstats file per run goes to $1; ci.yml
# compares the Linux files with the Windows ones byte for byte. Needs the models in data/lm (scripts/fetch-models.sh) and the release CLI
# (`cargo build --release --locked -p cli --bin shanjie-eval`). --threads does not change a byte of the output (cli/src/main.rs).
set -euo pipefail
cd "$(dirname "$0")/.."
out="${1:?usage: ci-rowstats.sh OUTPUT_DIR}"
mkdir -p "$out"
cli=target/release/shanjie-eval
[ -x "$cli.exe" ] && cli="$cli.exe"
for profile in chat formal; do
  extra=()
  [ "$profile" = chat ] && extra=(--colloquial-core data/lm/colloquial.sjlm)
  for set in dev302 user-typing user-reported; do
    if [ "$set" = dev302 ]; then rows=(--dev 302); else rows=(--rows "eval/dev/$set.txt"); fi
    "$cli" --lm data/lm/bigram.sjlm --profile "$profile" "${rows[@]}" --context --packs acg,it --tinylm data/lm/tinylm.sjtl \
      ${extra[@]+"${extra[@]}"} --threads 4 --rowstats "$out/$set-$profile.rowstats"
  done
done
ls -l "$out"
