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
- Latency columns: Apple is "idle window" only when `apple` ran with `--idle` and the user did not report otherwise. A `load` field in `apple-meta.json` holds the load the user reported for the run; it overrides the label and the latency is not decision-grade. Jev latency is per request, in ms.
- The Swift catch uses the deprecated `LanguageModelSession.GenerationError` type (macOS 27 SDK warning); a guardrail error not matching it would show up as `error`, which is counted and is a stop condition.

## Results (full run 2026-10-05)

Candidates from the production CLI at c6abd58 (chat profile), top 8 distinct strings. discordtune rows are aggregates only (per-row files stay in shanjie-private). tau was chosen on discordtune half A: A-fwd 0.161, A-both 0.378; the same values apply to half B and every other set. Apple latency was taken while the Mac was in moderate-to-high everyday use, as the user reported (snapshot at 00:18: about 22 foreground apps, 22 Claude Code sessions, load average 15–18, 51% memory free), so it is not decision-grade. Jev latency is per request of up to 20 questions. Every Apple call returned (0 blocked, 0 unparsable).

| Set | Subset | Cond | n | Acc (base → judge) | oracle@8 | A1b@8 | Fixed | Broken | Net | p | First-pick | Flip | Blocked/unparsable | Latency p50/p95/max ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| discordtune | all | A-fwd | 1000 | 83.5% → 75.4% | 97.3% | 77.5% | 57 | 138 | -81 | 0 | 72.4% | — | 0/0 | 355/470/1467 |
| discordtune | all | A-rev | 1000 | 83.5% → 49.3% | 97.3% | 50.7% | 33 | 375 | -342 | 0 | 12.3% | 54.8% | 0/0 | 374/546/943 |
| discordtune | all | A-ctx | 457 | 74.4% → 54.5% | 98.0% | 55.6% | 17 | 108 | -91 | 0 | 45.3% | — | 0/0 | 398/632/1086 |
| discordtune | all | A-both | 1000 | 83.5% → 82.2% | 97.3% | 84.5% | 17 | 30 | -13 | 0.0789 | — | 54.8% | 0/0 | — |
| discordtune | all | A-fwd@tau | 1000 | 83.5% → 84.0% | 97.3% | 86.3% | 19 | 14 | 5 | 0.487 | 72.4% | — | 0/0 | 355/470/1467 |
| discordtune | all | A-both@tau | 1000 | 83.5% → 84.3% | 97.3% | 86.6% | 10 | 2 | 8 | 0.0386 | — | — | 0/0 | — |
| discordtune | A | A-fwd | 500 | 82.6% → 75.6% | 97.0% | 77.9% | 35 | 70 | -35 | 0.0008 | 70.4% | — | 0/0 | 360/487/1467 |
| discordtune | A | A-rev | 500 | 82.6% → 49.2% | 97.0% | 50.7% | 23 | 190 | -167 | 0 | 10.4% | 55.6% | 0/0 | 384/581/943 |
| discordtune | A | A-ctx | 251 | 75.3% → 55.8% | 97.6% | 57.1% | 8 | 57 | -49 | 0 | 48.2% | — | 0/0 | 390/527/727 |
| discordtune | A | A-both | 500 | 82.6% → 81.8% | 97.0% | 84.3% | 14 | 18 | -4 | 0.597 | — | 55.6% | 0/0 | — |
| discordtune | A | A-fwd@tau | 500 | 82.6% → 83.6% | 97.0% | 86.2% | 11 | 6 | 5 | 0.332 | 70.4% | — | 0/0 | 360/487/1467 |
| discordtune | A | A-both@tau | 500 | 82.6% → 84.0% | 97.0% | 86.6% | 9 | 2 | 7 | 0.0654 | — | — | 0/0 | — |
| discordtune | B | A-fwd | 500 | 84.4% → 75.2% | 97.6% | 77.0% | 22 | 68 | -46 | 0 | 74.4% | — | 0/0 | 351/451/661 |
| discordtune | B | A-rev | 500 | 84.4% → 49.4% | 97.6% | 50.6% | 10 | 185 | -175 | 0 | 14.2% | 54.0% | 0/0 | 364/498/700 |
| discordtune | B | A-ctx | 206 | 73.3% → 52.9% | 98.5% | 53.7% | 9 | 51 | -42 | 0 | 41.7% | — | 0/0 | 421/682/1086 |
| discordtune | B | A-both | 500 | 84.4% → 82.6% | 97.6% | 84.6% | 3 | 12 | -9 | 0.0352 | — | 54.0% | 0/0 | — |
| discordtune | B | A-fwd@tau | 500 | 84.4% → 84.4% | 97.6% | 86.5% | 8 | 8 | 0 | 1 | 74.4% | — | 0/0 | 351/451/661 |
| discordtune | B | A-both@tau | 500 | 84.4% → 84.6% | 97.6% | 86.7% | 1 | 0 | 1 | 1 | — | — | 0/0 | — |
| cvtune | all | A-fwd | 1000 | 85.1% → 81.7% | 97.2% | 84.1% | 70 | 104 | -34 | 0.0121 | 72.7% | — | 0/0 | 476/1075/4202 |
| cvtune | all | A-rev | 1000 | 85.1% → 59.7% | 97.2% | 61.4% | 48 | 302 | -254 | 0 | 10.1% | 51.1% | 0/0 | 408/686/2125 |
| cvtune | all | A-both | 1000 | 85.1% → 84.3% | 97.2% | 86.7% | 29 | 37 | -8 | 0.389 | — | 51.1% | 0/0 | — |
| cvtune | all | J-sent-fwd | 1000 | 85.1% → 89.7% | 97.2% | 92.3% | 67 | 21 | 46 | 0 | 88.3% | — | 0/0 | 220/292/348 |
| cvtune | all | J-sent-rev | 1000 | 85.1% → 88.5% | 97.2% | 91.0% | 71 | 37 | 34 | 0.0014 | 2.1% | 11.2% | 0/0 | 213/268/409 |
| cvtune | all | J-pos | 1000 | 85.1% → 89.0% | 97.2% | 91.5% | 66 | 27 | 39 | 0.0001 | — | — | 0/0 | 207/280/366 |
| cvtune | all | A-fwd@tau | 1000 | 85.1% → 86.6% | 97.2% | 89.1% | 22 | 7 | 15 | 0.0081 | 72.7% | — | 0/0 | 476/1075/4202 |
| cvtune | all | A-both@tau | 1000 | 85.1% → 86.3% | 97.2% | 88.8% | 16 | 4 | 12 | 0.0118 | — | — | 0/0 | — |
| dev302 | all | A-fwd | 302 | 78.8% → 87.7% | 97.7% | 89.8% | 40 | 13 | 27 | 0.0003 | 76.8% | — | 0/0 | 383/471/708 |
| dev302 | all | A-rev | 302 | 78.8% → 57.3% | 97.7% | 58.6% | 25 | 90 | -65 | 0 | 13.9% | 54.3% | 0/0 | 326/448/624 |
| dev302 | all | A-both | 302 | 78.8% → 82.8% | 97.7% | 84.7% | 14 | 2 | 12 | 0.0042 | — | 54.3% | 0/0 | — |
| dev302 | all | J-sent-fwd | 302 | 78.8% → 91.7% | 97.7% | 93.9% | 41 | 2 | 39 | 0 | 82.5% | — | 0/0 | 217/249/249 |
| dev302 | all | J-sent-rev | 302 | 78.8% → 90.4% | 97.7% | 92.5% | 40 | 5 | 35 | 0 | 2.0% | 9.6% | 0/0 | 217/252/252 |
| dev302 | all | J-pos | 302 | 78.8% → 90.4% | 97.7% | 92.5% | 37 | 2 | 35 | 0 | — | — | 0/0 | 208/260/319 |
| dev302 | all | A-fwd@tau | 302 | 78.8% → 82.5% | 97.7% | 84.4% | 14 | 3 | 11 | 0.0127 | 76.8% | — | 0/0 | 383/471/708 |
| dev302 | all | A-both@tau | 302 | 78.8% → 81.1% | 97.7% | 83.1% | 7 | 0 | 7 | 0.0156 | — | — | 0/0 | — |
| typing76 | all | A-fwd | 76 | 85.5% → 85.5% | 97.4% | 87.8% | 6 | 6 | 0 | 1 | 77.6% | — | 0/0 | 388/578/634 |
| typing76 | all | A-rev | 76 | 85.5% → 59.2% | 97.4% | 60.8% | 6 | 26 | -20 | 0.0005 | 14.5% | 48.7% | 0/0 | 437/766/1245 |
| typing76 | all | A-both | 76 | 85.5% → 85.5% | 97.4% | 87.8% | 2 | 2 | 0 | 1 | — | 48.7% | 0/0 | — |
| typing76 | all | J-sent-fwd | 76 | 85.5% → 93.4% | 97.4% | 95.9% | 8 | 2 | 6 | 0.109 | 85.5% | — | 0/0 | 216/235/235 |
| typing76 | all | J-sent-rev | 76 | 85.5% → 89.5% | 97.4% | 91.9% | 7 | 4 | 3 | 0.549 | 1.3% | 5.3% | 0/0 | 250/263/263 |
| typing76 | all | J-pos | 76 | 85.5% → 92.1% | 97.4% | 94.6% | 8 | 3 | 5 | 0.227 | — | — | 0/0 | 209/252/252 |
| typing76 | all | A-fwd@tau | 76 | 85.5% → 88.2% | 97.4% | 90.5% | 2 | 0 | 2 | 0.5 | 77.6% | — | 0/0 | 388/578/634 |
| typing76 | all | A-both@tau | 76 | 85.5% → 86.8% | 97.4% | 89.2% | 2 | 1 | 1 | 1 | — | — | 0/0 | — |

In this table, fixed/broken are against rank 1, except A-ctx, whose fixed/broken are against A-fwd on the same rows. The base accuracy shown for A-ctx is A-fwd's on those rows; rank 1 on the 457 discordtune rows with a context is 82.3%. Apple latency is per row and split by half; Jev latency is per request over the whole set.

### Pre-registered decision (contract section 5)

- **Apple, ungated (A-fwd, A-rev, A-both)**: none is significantly better on discordtune. A-fwd and A-rev are significantly worse (net −81 and −342 on 1,000 rows), and the cvtune gate also rejects them (p < 0.05 with a negative net). **Not a candidate.**
- **Apple, gated (A-fwd@tau, A-both@tau)**: on discordtune half B, out of sample, net 0 (p = 1) and +1 (p = 1). **Not a candidate.** On cvtune, the same tau gives +15 and +12, both significant.
- **Jev (J-sent-fwd, J-sent-rev, J-pos)**: significantly better on cvtune (net +46, +34, +39). **Candidate, under the declared deviation**: it was never run on discordtune for privacy, so the evidence is weaker.
- **A-ctx**: adding the left context makes Apple worse than A-fwd on the same 457 rows (17 fixed, 108 broken).

### What the numbers say

- **dev302 points the wrong way.** The 2026-10-04 probe and this run's dev302 row agree with each other (Apple +27), but dev302 does not predict the user's chats (discordtune −81). Sentences written by the project or an LLM do not stand in for real chat.
- **Apple's on-device model picks by position.** Reversing the candidate order changes its pick on 49–55% of rows. On discordtune it chooses the first option 72% of the time in forward order and 12% in reverse (72–78% and 10–15% across the four sets). Jev's flip rate is 5–11%.
- **Next experiment for an on-device judge**: a question shape that does not present a list of options, such as per-candidate scoring or the per-position shape, before any S5 integration.
