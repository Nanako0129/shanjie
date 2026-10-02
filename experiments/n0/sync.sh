#!/bin/bash
# Copy the dev-only working set to 188 (never eval/holdout).
set -e; cd "$(dirname "$0")/../.."
K=(-i $HOME/.ssh/coralline_winvm -o IdentitiesOnly=yes -o BatchMode=yes)
ssh "${K[@]}" Nanako@192.168.123.188 'mkdir shanjie-n0\experiments\n0 shanjie-n0\data\lexicon shanjie-n0\eval\dev shanjie-n0\reference\proto shanjie-n0\experiments\n0\results 2>nul & exit /b 0'
scp "${K[@]}" experiments/n0/n0.py Nanako@192.168.123.188:shanjie-n0/experiments/n0/
scp "${K[@]}" data/lexicon/mcbpmf-data.txt Nanako@192.168.123.188:shanjie-n0/data/lexicon/
scp "${K[@]}" eval/dev/*.txt Nanako@192.168.123.188:shanjie-n0/eval/dev/
scp "${K[@]}" reference/proto/ime.py Nanako@192.168.123.188:shanjie-n0/reference/proto/
