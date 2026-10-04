# S5j: Apple Foundation Models and Jev as same-reading judges

Spec: `docs/contracts/s5j-judges.md` (prompts, conditions and decision rules are frozen there). This directory is the tooling only.

| File | Role |
|---|---|
| `judge.swift` | Apple judge. Input TSV `id, context, cand1..candK`; appends `id, pick, ms, status` (ok / blocked / unparsable / error). stdout carries counts only. `--force-fail` makes every answer unparsable (error-path test). |
| `s5.py` | Driver: `prep`, `apple`, `jev`, `score`. Resumable: every stage skips ids already in its output file. |
| `results/` | Per-row results for dev302 and typing76 only. |

## Setup

```sh
cargo build --release --bin shanjie-eval          # data/lm/bigram.sjlm must be present
mkdir -p build && swiftc -O -parse-as-library experiments/s5-judges/judge.swift -o build/s5-judge   # build/ is gitignored
```

Jev reads its key as in `experiments/jev/jev.py` (never printed); the model is pinned to `jev-1.13.0`.

## Where files go (contract section 6)

| Set | Per-row files |
|---|---|
| discordtune | `~/side-project/shanjie-private/s5-judges/discordtune/` (sample, `--rows`, `--dump`, Swift I/O, checkpoints) and `tau.json` there |
| cvtune | `~/.cache/shanjie/work/s5-judges/cvtune/` |
| dev302, typing76 | `experiments/s5-judges/results/<set>/` |

Runs with `--limit N` (smoke) write to `<set>-nN/`; the repo ignores `results/*-n*/`. Stdout and stderr never carry sentence text; an unexpected exception prints only its type (and the HTTP status for Jev).

Sampling: `random.Random(20261004).sample` over the set's rows (1,000 for discordtune and cvtune); discordtune half A is the first 500 of the sample, half B the last 500. Candidates are the first 8 distinct dump strings per row; margin is the score gap between the first two distinct strings.

## Full run (do not start without the agreed window for Apple)

```sh
cd <worktree>
python3 experiments/s5-judges/s5.py prep  --sets discordtune,cvtune,dev302,typing76
# Apple, the Mac idle (ask the user first). --idle marks the latency as valid.
python3 experiments/s5-judges/s5.py apple --sets discordtune,cvtune,dev302,typing76 --idle
# Jev: network only, public sets only (any other set exits 1 before the key is read)
python3 experiments/s5-judges/s5.py jev   --sets cvtune,dev302,typing76
# tau grid chosen on discordtune half A (written to tau.json), then every table row
python3 experiments/s5-judges/s5.py score --sets discordtune,cvtune,dev302,typing76
```

If a run is interrupted, rerun the same command: it resumes and never resamples.

Calls: Apple about 5,200 (fwd 1,000+1,000+302+76; rev the same; ctx about 460 discordtune rows) at roughly 0.4 s plus session setup, so about 40 to 60 minutes. Jev about 400 requests (J-sent 70 per direction, J-pos about 255 with 20 questions per request), roughly 10 minutes, far under the 5,000 cap.

Smoke (first 20 rows of dev302, all conditions):

```sh
S=experiments/s5-judges/s5.py
python3 $S apple --sets dev302 --limit 20 --synth-ctx "<any context string>"
python3 $S jev   --sets dev302 --limit 20 --jev-cap 20
python3 $S score --sets dev302 --limit 20 --tau-self
```

## Notes on conditions

- A-ctx runs on discordtune rows that have a context (or on public rows when `--synth-ctx` is given, smoke only) and is paired with A-fwd on those rows.
- A-both uses A-fwd's candidate only when A-rev picked the same one; otherwise rank 1. Latency is not reported for it.
- J-pos: the response carries per-option `probabilities`, so the Jevboard rule (p >= 0.5 and >= 2x the unchanged option) applies; the `mode` column records `prob` or `choice`. Only candidates of the same length as rank 1 produce position variants.
- Rows with a single candidate are never sent to a judge; they stay at rank 1.
- Latency columns: Apple is "idle window" only when `apple` ran with `--idle`, else "load unknown" (not usable for S5 decisions). Jev latency is per request, in ms.
- The Swift catch uses the deprecated `LanguageModelSession.GenerationError` type (macOS 27 SDK warning); a guardrail error not matching it would show up as `error`, which is counted and is a stop condition.

## Results

Fill from `score` output (numbers only). `fixed`/`broken` against rank 1 (A-ctx against A-fwd); p is McNemar exact two-sided; SE is the paired standard error of the accuracy difference; flip is the order-flip rate on the `-rev` rows.

| Set | Cond | n | Lenient acc | Rank-1 acc | oracle@8 | A1b@8 | Fixed | Broken | p | Paired SE | First-pick share | Flip | Blocked | Unparsable | Latency p50/p95/max (ms) | Jev input tokens |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| discordtune | A-fwd | | | | | | | | | | | | | | | |
| discordtune | A-rev | | | | | | | | | | | | | | | |
| discordtune | A-both | | | | | | | | | | | | | | | |
| discordtune (tau, half B) | A-fwd, A-both | | | | | | | | | | | | | | | |
| discordtune (ctx rows) | A-ctx vs A-fwd | | | | | | | | | | | | | | | |
| cvtune | A-fwd, A-rev, A-both, tau variants | | | | | | | | | | | | | | | |
| cvtune | J-sent-fwd, J-sent-rev, J-pos | | | | | | | | | | | | | | | |
| dev302 | all conditions | | | | | | | | | | | | | | | |
| typing76 | all conditions | | | | | | | | | | | | | | | |
