#!/bin/bash
# E-C (experiments/e-c/README.md): decode every cell with small-model scores on one GCP C4D machine (row shards via
# shardcli.py), then ecana.py. Set files are placed in ~/sets by the Mac; numbers only come back.
set -u
exec > ~/ec.log 2>&1
sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq build-essential git curl python3-numpy >/dev/null || exit 2
curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal >/dev/null && . ~/.cargo/env || exit 2
git clone -q -b exp/e-c-joint https://github.com/Nanako0129/shanjie ~/rs && cd ~/rs || exit 2
[ "$(git rev-parse HEAD)" = "$EC_HEAD" ] || { echo "STOP head $(git rev-parse HEAD)"; exit 2; }
cargo build --release -q -p cli || { echo "STOP build"; exit 2; }
R=https://github.com/Nanako0129/shanjie/releases/download
curl -sSL --create-dirs -o data/lm/bigram.sjlm $R/model-v6/bigram.sjlm
curl -sSL -o data/lm/classes.sjc $R/classes-v5/classes.sjc
curl -sSL -o data/lm/kn.sjkn $R/kn-v1/kn.sjkn
curl -sSL -o data/lm/tinylm.sjtl $R/sj-sr-6-14m/tinylm.sjtl
curl -sSL -o data/lm/trigram.sjt3 $R/sj-cx-1-7.7m/trigram.sjt3
sha256sum -c data/bigram.sjlm.sha256 data/classes.sjc.sha256 data/kn.sjkn.sha256 data/tinylm.sjtl.sha256 data/trigram.sjt3.sha256 || exit 3
export SHANJIE_VARIANTS=$PWD/eval/variants.tsv SHANJIE_EVAL_BIN=$PWD/target/release/shanjie-eval
S=~/sets; O=~/ec; mkdir -p $O
declare -A F=([chatclause]=$S/tune.rows.txt [discordtune]=$S/discord-tune-rows.txt [lytune]=$S/lytune.txt [chatjudge]=$S/chatjudge.rows.txt
  [cvtune-clean]=$S/cvtune-clean.txt [wikitune-clean]=$S/wikitune-clean.txt [wikijudge]=$S/wikijudge.txt [wikijudge2]=$S/wikijudge2.txt [wikijudge3]=$S/wikijudge3.txt)
cell() {  # tag profile set, with the override variables already in the environment
  local n=$(grep -c '^[^|]*|[^|]*|[^|]*$' "${F[$3]}"); local per=$(( (n + 63) / 64 )); [ $per -lt 20 ] && per=20
  SHARD_ROWS=$per python3 ~/shardcli.py --lm data/lm/bigram.sjlm --profile $2 --context --packs acg --tinylm data/lm/tinylm.sjtl \
    --rows "${F[$3]}" --tinylm-scores $O/$1.$2.$3.jsonl > $O/$1.$2.$3.sum 2>$O/$1.$2.$3.err || echo "FAIL $1 $2 $3"
}
echo "== $(date +%T) chat"
for l in 0.3 0.4 0.5 0.6 0.7; do for b in 0.5 0.75 1.0; do
  for s in chatclause discordtune lytune chatjudge; do SHANJIE_LAMBDA_CHAT=$l SHANJIE_KN_BETA=$b cell l${l}_b$b chat $s; done
  echo "chat l$l b$b done $(date +%T)"
done; done
echo "== $(date +%T) formal"
for l in 0.5 0.6 0.7 0.8 0.9; do for f in 0.3 0.5 0.7; do
  for s in cvtune-clean wikitune-clean wikijudge wikijudge2 wikijudge3; do SHANJIE_LAMBDA_FORMAL=$l SHANJIE_TRI_FLOOR=$f cell l${l}_f$f formal $s; done
  echo "formal l$l f$f done $(date +%T)"
done; done
echo "== $(date +%T) analysis"
python3 experiments/e-c/ecana.py ~/rs $O > ~/ec.out 2>&1; echo "ecana rc=$?"
echo "== done $(date +%T)"
