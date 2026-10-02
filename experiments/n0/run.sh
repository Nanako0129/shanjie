#!/bin/bash
# usage: run.sh <model> <m1|m2> [beams] [limit] [tag]  -> results/<tag>.txt/.jsonl (runs on 188, from this Mac)
set -e; cd "$(dirname "$0")"
K=(-i $HOME/.ssh/coralline_winvm -o IdentitiesOnly=yes -o BatchMode=yes)
M=$1; ME=$2; B=${3:-4}; LIM=${4:-}; TAG=${5:-$M-$ME-b$B}
PINENV=""; [ -n "$N0_PIN" ] && PINENV="set N0_PIN=1&& "
LIMARG=""; [ -n "$LIM" ] && LIMARG="--limit $LIM"
ssh "${K[@]}" Nanako@192.168.123.188 "cd %USERPROFILE%\\shanjie-n0 && set PYTHONUTF8=1&& set HF_HUB_OFFLINE=1&& $PINENV %USERPROFILE%\\ime-research\\proto\\.venv\\Scripts\\python experiments\\n0\\n0.py --method $ME --model $M --beams $B $LIMARG --out experiments\\n0\\results\\$TAG.jsonl" 2>/dev/null >| results/$TAG.txt
scp "${K[@]}" Nanako@192.168.123.188:shanjie-n0/experiments/n0/results/$TAG.jsonl results/ >/dev/null
cat results/$TAG.txt | grep -E "^## (dev|peak)"
