# S5k: local order-free judges (Laya, Bonsai, Qwen3)

Spec: `docs/contracts/s5k-local-scorers.md`. This directory holds the tooling and the dev302 / typing76 per-row results. Status: full run done 2026-10-05; no condition met the pre-registered bar (see Results).

## Files

| File | Role |
|---|---|
| `s5k.py` | shared: SHA-256 table (contract §2), hash-checked row loading, output paths, tie-break pick, degeneracy check. Forces `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1` |
| `run_laya.py` | L-noul, L-choice-fwd, L-choice-rev, L-pos (venv A) |
| `run_ll.py` | Q-ll (venv A), B1-ll / B4-ll (venv B) |
| `run_ll_cuda.py` | Q8-ll: Qwen3-8B 4-bit (bitsandbytes nf4) on CUDA, for 188 (contract §11); pure scoring functions import without torch |
| `test_run_ll_cuda.py` | offline tests for `run_ll_cuda.py` (`python3 -m unittest test_run_ll_cuda`, no torch) |
| `score.py` | metrics, tau, pairing with S5j (any Python) |
| `selftest.py` | error-path checks: prefix-only scorer trips the stop (exit 3); wrong hash exits non-zero |

Only S5j pure functions are imported (`lenient_fn`, `mcnemar`, `pct`, `positions`, `pos_decision`, `INSTR`, `POS_INSTR`). `prep()`, `read_rows()`, `set_dir()` are never called. Every S5j input is hash-checked before it is opened; a missing file prints `s5k: INPUT MISSING`, a mismatch `s5k: INPUT HASH MISMATCH`, both exit 1. All stdout and stderr carry only row numbers, numbers and fixed strings.

## Versions (pinned; installed by main)

| Item | Version |
|---|---|
| venv A | `~/.cache/shanjie/venv-s5k-a`, Python 3.12, laya-mlx 0.3.0, mlx 0.32.3, mlx-lm 0.32.0, huggingface_hub 1.33.0 |
| venv B | `~/.cache/shanjie/venv-s5k-b`, mlx-lm 0.32.0 plus the PrismML MLX fork at `bbc151c6360ee79a7412fa23cfc5c5fc63ced1ca`. mlx `0.32.3.dev20261005+bbc151c6` built from the fork commit with `MACOSX_DEPLOYMENT_TARGET=26.1`; the NAX kernels are skipped because they do not compile against the macOS 27 SDK (build note from main) |
| Laya | `laya-multilingual-mlx-fp16`: `laya-mlx convert` from `convaiinnovations/laya-multilingual@1720e3e3357cfe1e281542e223f8273b0890ca34`, fp16 |
| Bonsai 1.7B | `bonsai-1.7b-mlx-1bit`: `prism-ml/Bonsai-1.7B-mlx-1bit@fac480d016cf265838ad087f9f3f2abac4a2c1a5` |
| Bonsai 4B | `bonsai-4b-mlx-1bit`: `prism-ml/Bonsai-4B-mlx-1bit@3d25c52f7fa171affb188e27796be61be3df52db` |
| Qwen3 | `qwen3-1.7b-4bit`: `mlx-community/Qwen3-1.7B-4bit@3b1b1768f8f8cf8351c712464f906e86c2b8269e` |

Models live in `~/.cache/shanjie/models/`; all Apache-2.0. Models load only from those local paths.

## Hash tables (contract §2, SHA-256, in `s5k.py`)

| File | Rows | SHA-256 |
|---|---|---|
| discordtune `rows.jsonl` | 1000 | `8217e3242c3b302b55485f9d65ffba9e5dcca47130dca0b885da63e8d761bbd9` |
| cvtune `rows.jsonl` | 1000 | `98eec57ff74ca1cc423804c3cfa1afb250314f3eb32c869f728e60a1ce4e170d` |
| dev302 `rows.jsonl` | 302 | `70aaee641fa78b757683c47595197745075d69b92ccb0b9d5846cd39e7f65c39` |
| typing76 `rows.jsonl` | 76 | `ed0bb91342ad9bc64cefe4189e809725e0fca5b3acbe7eb8ef2919640c7bd34a` |
| discordtune `apple-fwd.tsv` | | `bcaddc91ec8bdcfb78b7a3111058a74d4e185d885de03ba471e832c723d235e9` |
| cvtune `apple-fwd.tsv` | | `a53445736473f92ed9e60840217126ba9d3aba8c6db09ba236426ed4bbeabedd` |
| cvtune `jev-sent-fwd.jsonl` | | `ea8b5637606e0589758b7d51356ce1a29d21af343fbd02e1e6bcf3d64b925594` |
| dev302 `apple-fwd.tsv` | | `11698af04c35aea6764bd421152747811634b644e183428826646fa65431a96f` |
| dev302 `jev-sent-fwd.jsonl` | | `5a909cf09c3539609306614ebf3b0a658df18e460935380615f4dc3ac3b5fd5f` |
| typing76 `apple-fwd.tsv` | | `26b29dd0d6d370d8a73804705501cf87fb219abcad157618aa2701d22f48e47b` |
| typing76 `jev-sent-fwd.jsonl` | | `0577a26da9a7a77b6e6ca23c45a4cf9ace25e31f60c9174246548e17ca534e61` |

The private root is taken from the environment variable `SHANJIE_PRIVATE` (no default; only the discordtune runs need it), so no private path appears in the repo.

## Output layout

Per condition and context mode one JSONL: `<cond>.noctx.jsonl` / `<cond>.ctx.jsonl` (`k` = row index in the S5j `rows.jsonl`, `scores` in rank order, `ms`; L-pos stores adopted `[position, option]` pairs; no sentence text). `meta.jsonl` has load time, peak memory, and the load note. Output directories: discordtune in `$SHANJIE_PRIVATE/s5-local/`, cvtune in `~/.cache/shanjie/work/s5-local/`, dev302 / typing76 in `results/`. `--limit N` writes `<set>-nN/` (smoke; not committed). Runs resume per row.

`lat_first` in `score.json` is the first record of the file, i.e. "first row after load" only for a condition that starts its process (L-noul in `run_laya.py`, every ll model); other conditions show "—". `score.py` exits 1 with `s5k: TAU MISSING` when a non-discordtune set is scored and `tau.json` is absent. `selftest.py` writes into a temporary directory removed at exit.

Context modes: `none` (empty context), `real` (each row's own context, only rows that have one; discordtune), `synth` (the fixed 10-character context 「我們等一下要去吃飯，」, public sets only, smoke). `score.py` names the conditions `X` (no context), `X+ctx` (rows with context, paired with the same rows' `X`), `X+ctxall` (discordtune: context where the row has one, none elsewhere, the 1000-row "with context" table), and `@tau` for gated variants.

## Full-run commands (main runs; ask the user for a quiet window first, add `--idle` only inside it)

```
cd /Users/nanako/side-project/shanjie-s5k/experiments/s5-local   # or the merged checkout
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 SHANJIE_PRIVATE=<private data root>
A=~/.cache/shanjie/venv-s5k-a/bin/python
for S in discordtune cvtune dev302 typing76; do
  $A run_laya.py --set $S --ctx none && $A run_ll.py --model Q-ll --set $S --ctx none || break
done
$A run_laya.py --set discordtune --ctx real && $A run_ll.py --model Q-ll --set discordtune --ctx real
# Bonsai (venv B, PrismML fork):
B=~/.cache/shanjie/venv-s5k-b/bin/python
for S in discordtune cvtune dev302 typing76; do for M in B1-ll B4-ll; do $B run_ll.py --model $M --set $S --ctx none; done; done
for M in B1-ll B4-ll; do $B run_ll.py --model $M --set discordtune --ctx real; done
# scoring: discordtune first (it chooses and stores tau on half A)
for S in discordtune cvtune dev302 typing76; do $A score.py --set $S; done
```

Each command resumes where it stopped. The smoke test and `selftest.py` are `--limit 20 --ctx none|synth --set dev302` and `$A selftest.py`.

## Limitations (contract §2, §3)

* cvtune reference sentences carry Simplified/Traditional conversion residue (PLAN "待排入"); same as S5j.
* Candidates, margins and halves come from S5j's `rows.jsonl` (CLI at c6abd58, chat profile); nothing is regenerated.
* Ll scores are not length-normalised. Candidates have equal pronunciation and character count but can differ in token count; candidates with fewer tokens are favoured. Deliberate.
* Ll scoring starts at the first token after the prefix; rows whose prefix+candidate tokenisation does not start with the prefix's own tokens are not scored and counted (above 1% of rows the run stops). The prefix KV is computed once per row and each candidate continues from a copy of the cache. Cache vs full-forward scores differ by about 0.14 nats on one checked row of Qwen3 4-bit (numerical, near-constant across candidates); not otherwise characterised.
* L-pos sends no context in S5j (`s5.py` passes an empty string), so S5j's J-pos corresponds to the no-context L-pos. `POS_INSTR` does not mention the context, so the with/without comparison for L-pos may show nothing.
* About 14 settings use p < 0.05 without multiple-comparison correction; one "candidate" can appear by chance.
* dev302 / typing76 are recorded only; S5j showed dev302 can point the wrong way.
* Latency is valid only when measured in a window agreed with the user; otherwise it is labelled "load unknown".
* The 8-of-8 numbers are not extrapolated to 64 candidates.

## Results (full run 2026-10-05, 21:19–22:31)

- Candidates and samples are S5j's prep files (hash-checked).
- Every model ran locally and offline, while the user was away from the Mac (`--idle`). The load average at each step's start was 2.9–10.0 (background rclone, other sessions); the log keeps it per step.
- discordtune rows are aggregates only; the per-row files stay in the private root.
- tau was chosen on discordtune half A: 0.161 for every condition except Q-ll (0.631). The same values apply to half B and every other set.
- Latency is per row over 8 candidates (p50/p95 ms).
- `+ctxall` is discordtune with each row's real context where it has one (457 of 1,000 rows), and none elsewhere.
- Only the conditions the decision rules need are listed; the full output is in the run log.

| Set | Subset | Cond | n | Acc (base → judge) | Fixed | Broken | Net | p | First-pick | Flip | vs A-fwd (fixed/broken) | vs J-sent-fwd | Latency p50/p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| discordtune | all | L-noul | 1000 | 83.5% → 11.3% | 19 | 741 | -722 | 0 | 0.084 | — | 25/666 p=0 | — | 52/219 |
| discordtune | all | L-choice-fwd | 1000 | 83.5% → 8.1% | 33 | 787 | -754 | 0 | 0.012 | — | — | — | 15/45 |
| discordtune | all | L-choice-rev | 1000 | 83.5% → 9.7% | 12 | 750 | -738 | 0 | 0.074 | 0.822 | — | — | 14/49 |
| discordtune | all | L-pos | 1000 | 83.5% → 51.6% | 9 | 328 | -319 | 0 | — | — | — | — | 40/206 |
| discordtune | all | B1-ll | 1000 | 83.5% → 70.7% | 57 | 185 | -128 | 0 | 0.673 | — | 123/170 p=0.0071 | — | 199/349 |
| discordtune | all | B4-ll | 1000 | 83.5% → 69.5% | 62 | 202 | -140 | 0 | 0.65 | — | 115/174 p=0.0006 | — | 620/1063 |
| discordtune | all | Q-ll | 1000 | 83.5% → 81.8% | 86 | 103 | -17 | 0.244 | 0.751 | — | 135/71 p=0 | — | 293/589 |
| discordtune | all | Q-ll+ctx | 457 | 82.3% → 85.6% | 49 | 34 | +15 | 0.124 | 0.7724 | — | 78/27 p=0 | — | 244/513 |
| discordtune | all | L-noul+ctxall | 1000 | 83.5% → 13.7% | 24 | 722 | -698 | 0 | 0.105 | — | 30/647 p=0 | — | 39/132 |
| discordtune | all | B1-ll+ctxall | 1000 | 83.5% → 69.6% | 61 | 200 | -139 | 0 | 0.663 | — | 127/185 p=0.0012 | — | 249/431 |
| discordtune | all | B4-ll+ctxall | 1000 | 83.5% → 70.7% | 62 | 190 | -128 | 0 | 0.663 | — | 122/169 p=0.0069 | — | 749/1320 |
| discordtune | all | Q-ll+ctxall | 1000 | 83.5% → 84.2% | 92 | 85 | +7 | 0.652 | 0.771 | — | 148/60 p=0 | — | 244/513 |
| discordtune | all | L-noul@tau | 1000 | 83.5% → 80.7% | 6 | 34 | -28 | 0 | 0.908 | — | 130/77 p=0.0003 | — | 52/219 |
| discordtune | all | B1-ll@tau | 1000 | 83.5% → 84.6% | 27 | 16 | +11 | 0.126 | 0.932 | — | 144/52 p=0 | — | 199/349 |
| discordtune | all | B4-ll@tau | 1000 | 83.5% → 83.5% | 23 | 23 | +0 | 1 | 0.93 | — | 143/62 p=0 | — | 620/1063 |
| discordtune | all | Q-ll@tau | 1000 | 83.5% → 86.3% | 71 | 43 | +28 | 0.0111 | 0.845 | — | 156/47 p=0 | — | 293/589 |
| discordtune | all | Q-ll+ctx@tau | 457 | 82.3% → 87.5% | 39 | 15 | +24 | 0.0015 | 0.8468 | — | 85/25 p=0 | — | 244/513 |
| discordtune | all | Q-ll+ctxall@tau | 1000 | 83.5% → 87.2% | 75 | 38 | +37 | 0.0006 | 0.85 | — | 162/44 p=0 | — | 244/513 |
| discordtune | B | L-noul@tau | 500 | 84.4% → 80.4% | 3 | 23 | -20 | 0.0001 | 0.894 | — | 63/37 p=0.012 | — | — |
| discordtune | B | B1-ll@tau | 500 | 84.4% → 85.0% | 11 | 8 | +3 | 0.648 | 0.928 | — | 73/24 p=0 | — | — |
| discordtune | B | B4-ll@tau | 500 | 84.4% → 83.6% | 10 | 14 | -4 | 0.541 | 0.926 | — | 72/30 p=0 | — | — |
| discordtune | B | Q-ll@tau | 500 | 84.4% → 87.0% | 34 | 21 | +13 | 0.105 | 0.844 | — | 77/18 p=0 | — | — |
| discordtune | B | Q-ll+ctx@tau | 206 | 83.5% → 86.9% | 16 | 9 | +7 | 0.23 | 0.835 | — | 39/11 p=0.0001 | — | — |
| discordtune | B | Q-ll+ctxall@tau | 500 | 84.4% → 87.2% | 35 | 21 | +14 | 0.0814 | 0.846 | — | 79/19 p=0 | — | — |
| cvtune | all | L-noul | 1000 | 85.1% → 19.7% | 27 | 681 | -654 | 0 | 0.105 | — | 27/647 p=0 | 14/714 p=0 | 43/109 |
| cvtune | all | L-choice-fwd | 1000 | 85.1% → 15.6% | 26 | 721 | -695 | 0 | 0.012 | — | — | — | 12/54 |
| cvtune | all | L-choice-rev | 1000 | 85.1% → 16.9% | 11 | 693 | -682 | 0 | 0.094 | 0.81 | — | — | 12/57 |
| cvtune | all | L-pos | 1000 | 85.1% → 51.7% | 4 | 338 | -334 | 0 | — | — | — | — | 41/188 |
| cvtune | all | B1-ll | 1000 | 85.1% → 77.0% | 64 | 145 | -81 | 0 | 0.692 | — | 98/145 p=0.0031 | 29/156 p=0 | 208/334 |
| cvtune | all | B4-ll | 1000 | 85.1% → 79.9% | 64 | 116 | -52 | 0.0001 | 0.732 | — | 98/116 p=0.245 | 28/126 p=0 | 800/1224 |
| cvtune | all | Q-ll | 1000 | 85.1% → 89.2% | 82 | 41 | +41 | 0.0003 | 0.789 | — | 115/40 p=0 | 40/45 p=0.665 | 361/636 |
| cvtune | all | L-noul@tau | 1000 | 85.1% → 83.4% | 8 | 25 | -17 | 0.0046 | 0.929 | — | 103/86 p=0.244 | 18/81 p=0 | 43/109 |
| cvtune | all | B1-ll@tau | 1000 | 85.1% → 86.1% | 20 | 10 | +10 | 0.0987 | 0.947 | — | 106/62 p=0.0009 | 20/56 p=0 | 208/334 |
| cvtune | all | B4-ll@tau | 1000 | 85.1% → 85.6% | 17 | 12 | +5 | 0.458 | 0.949 | — | 104/65 p=0.0033 | 18/59 p=0 | 800/1224 |
| cvtune | all | Q-ll@tau | 1000 | 85.1% → 89.8% | 62 | 15 | +47 | 0 | 0.855 | — | 113/32 p=0 | 32/31 p=1 | 361/636 |
| dev302 | all | L-noul | 302 | 78.8% → 24.5% | 15 | 179 | -164 | 0 | 0.1424 | — | 7/198 p=0 | 5/208 p=0 | 44/97 |
| dev302 | all | L-choice-fwd | 302 | 78.8% → 12.9% | 10 | 209 | -199 | 0 | 0.0099 | — | — | — | 13/52 |
| dev302 | all | L-choice-rev | 302 | 78.8% → 11.9% | 7 | 209 | -202 | 0 | 0.0464 | 0.8146 | — | — | 13/41 |
| dev302 | all | L-pos | 302 | 78.8% → 45.4% | 5 | 106 | -101 | 0 | — | — | — | — | 24/85 |
| dev302 | all | B1-ll | 302 | 78.8% → 71.9% | 31 | 52 | -21 | 0.0275 | 0.6523 | — | 16/64 p=0 | 8/68 p=0 | 298/400 |
| dev302 | all | B4-ll | 302 | 78.8% → 76.5% | 34 | 41 | -7 | 0.489 | 0.6854 | — | 17/51 p=0 | 10/56 p=0 | 925/1218 |
| dev302 | all | Q-ll | 302 | 78.8% → 90.4% | 49 | 14 | +35 | 0 | 0.7152 | — | 23/15 p=0.256 | 14/18 p=0.597 | 330/608 |
| dev302 | all | L-noul@tau | 302 | 78.8% → 74.8% | 5 | 17 | -12 | 0.0169 | 0.8642 | — | 11/50 p=0 | 2/53 p=0 | 44/97 |
| dev302 | all | B1-ll@tau | 302 | 78.8% → 78.8% | 7 | 7 | +0 | 1 | 0.9205 | — | 13/40 p=0.0003 | 3/42 p=0 | 298/400 |
| dev302 | all | B4-ll@tau | 302 | 78.8% → 79.5% | 9 | 7 | +2 | 0.804 | 0.9139 | — | 14/39 p=0.0008 | 3/40 p=0 | 925/1218 |
| dev302 | all | Q-ll@tau | 302 | 78.8% → 88.7% | 37 | 7 | +30 | 0 | 0.7947 | — | 19/16 p=0.736 | 10/19 p=0.136 | 330/608 |
| typing76 | all | L-noul | 76 | 85.5% → 13.2% | 1 | 56 | -55 | 0 | 0.0658 | — | 0/55 p=0 | 1/62 p=0 | 39/72 |
| typing76 | all | L-choice-fwd | 76 | 85.5% → 7.9% | 2 | 61 | -59 | 0 | 0.0132 | — | — | — | 11/50 |
| typing76 | all | L-choice-rev | 76 | 85.5% → 11.8% | 1 | 57 | -56 | 0 | 0.0789 | 0.8684 | — | — | 12/38 |
| typing76 | all | L-pos | 76 | 85.5% → 43.4% | 1 | 33 | -32 | 0 | — | — | — | — | 21/130 |
| typing76 | all | B1-ll | 76 | 85.5% → 67.1% | 4 | 18 | -14 | 0.0043 | 0.6053 | — | 6/20 p=0.0094 | 1/21 p=0 | 313/482 |
| typing76 | all | B4-ll | 76 | 85.5% → 65.8% | 6 | 21 | -15 | 0.0059 | 0.5789 | — | 5/20 p=0.0041 | 2/23 p=0 | 988/1348 |
| typing76 | all | Q-ll | 76 | 85.5% → 85.5% | 7 | 7 | +0 | 1 | 0.7105 | — | 6/6 p=1 | 2/8 p=0.109 | 319/527 |
| typing76 | all | L-noul@tau | 76 | 85.5% → 84.2% | 1 | 2 | -1 | 1 | 0.9474 | — | 6/7 p=1 | 2/9 p=0.0654 | 39/72 |
| typing76 | all | B1-ll@tau | 76 | 85.5% → 85.5% | 1 | 1 | +0 | 1 | 0.9737 | — | 6/6 p=1 | 2/8 p=0.109 | 313/482 |
| typing76 | all | B4-ll@tau | 76 | 85.5% → 86.8% | 2 | 1 | +1 | 1 | 0.9605 | — | 6/5 p=1 | 2/7 p=0.18 | 988/1348 |
| typing76 | all | Q-ll@tau | 76 | 85.5% → 89.5% | 6 | 3 | +3 | 0.508 | 0.7763 | — | 7/4 p=0.549 | 2/5 p=0.453 | 319/527 |

In this table, fixed/broken are against rank 1. "vs A-fwd" and "vs J-sent-fwd" are paired against S5j's Apple and Jev picks on the same rows; the second number is the judge's broken count.

### Pre-registered decision (contract §5)

- **Ungated, discordtune all 1,000 rows (p < 0.05 and a positive net needed): no candidate.**
  - The best is Q-ll+ctxall at 84.2% vs 83.5% (net +7, p = 0.65). Q-ll without context is −17 (p = 0.24).
  - B1-ll (70.7%), B4-ll (69.5%) and every Laya condition (8–52%) are significantly worse.
- **Gated, discordtune half B (out of sample): no candidate.**
  - The best is Q-ll+ctxall@tau at 87.2% vs 84.4% (net +14, p = 0.081), and Q-ll@tau at +13 (p = 0.105).
  - B1-ll@tau is +3 (p = 0.65), B4-ll@tau −4, and every Laya condition is negative.
- **cvtune gate**: no condition above passed the discordtune rule, so the gate decides nothing. For the record, Q-ll is +41 on cvtune (p = 0.0003) and Q-ll@tau +47 (p < 1e-4).
- The table lists a subset. main checked every setting in the full discordtune output: all 21 ungated settings on the 1,000 rows and all 21 gated settings on half B. None is both significant (p < 0.05) and positive.
- About 14 settings are tested at p < 0.05 with no multiple-comparison correction (contract §5); with no candidate, that caveat changes nothing here.

### What the numbers say

- **Laya cannot judge Chinese homophones.**
  - L-noul picks the right sentence on 11–20% of rows. L-choice is position-bound: reversing the options changes 81–93% of picks.
  - L-pos holds about 50% only because it mostly keeps rank 1.
  - The checkpoint is multilingual, but nothing here suggests it reads Traditional Chinese well enough for this task.
- **1-bit costs about 11 points.** B1-ll and Q-ll share the Qwen3-1.7B architecture, but on discordtune B1-ll is 70.7% and Q-ll (4-bit) 81.8%. The 1-bit 4B (B4-ll) is no better than the 1-bit 1.7B.
- **Qwen3-1.7B 4-bit, scoring each candidate's log-probability, is the only promising local judge.**
  - Context helps it: on the 457 discordtune rows that have a context, it fixed 32 and broke 8 against the same model without context (p = 0.0002).
  - On cvtune it ties cloud Jev (J-sent-fwd), 40 vs 45 discordant rows (p = 0.66). On the user's chats the gated form trends up (+14, p = 0.08) but misses the pre-registered bar.
- **Latency is over budget.** Q-ll takes p50 245–360 ms and p95 510–640 ms per row for 8 candidates, against A3's p95 < 300 ms for the neural path.
- **dev302 points the wrong way again**: Q-ll is +35 there, against −17 on discordtune.

### Next (for the S5 contract, not decided here)

- A larger discordtune sample, to settle Q-ll+ctxall@tau: its half-B p is 0.081 at n = 500.
- Shorter prompts or fewer candidates for latency (8 candidates per row now).
- Qwen3 4B 4-bit as the next size up.
- Prompt changes for the cloud judges, including Jev, are a separate tuning round.

## Q8-ll on 188 (contract §11; main runs it, nothing here is executed on the Mac)

`run_ll_cuda.py` takes the same `--set`, `--limit`, `--ctx none|real|synth`, `--idle` as `run_ll.py` and writes the same `Q8-ll.<noctx|ctx>.jsonl` records (`k`, `scores`, `ms`) into the same output directories. Its meta goes to `meta-188.jsonl` (run_ll's keys plus torch, transformers, bitsandbytes, GPU, quant, compute dtype, `kv_reuse`, `bos_token_id`), never to `meta.jsonl`. No KV reuse: one full forward per candidate (`kv_reuse=false`). The tokenizer adds only its default special tokens; Qwen3 has no BOS, which `bos_token_id` records. Compute dtype is bfloat16, quant nf4.

Layout on 188 (`~` is `%USERPROFILE%`): repo copy `%USERPROFILE%\s5k-188\repo\` (with `experiments/s5-local/`, `experiments/s5-judges/s5.py`, `reference/proto/`, the dev302/typing76 `rows.jsonl`), cvtune `rows.jsonl` at `%USERPROFILE%\.cache\shanjie\work\s5-judges\cvtune\`, discordtune `rows.jsonl` at `%USERPROFILE%\s5k-188\private\s5-judges\discordtune\`, model at `%USERPROFILE%\models\Qwen3-8B`. Interpreter: `%USERPROFILE%\ime-research\proto\.venv\Scripts\python.exe`. One program at a time, pinned to the P-cores.

```
cd /d %USERPROFILE%\s5k-188\repo\experiments\s5-local
set HF_HUB_OFFLINE=1
set TRANSFORMERS_OFFLINE=1
set SHANJIE_PRIVATE=%USERPROFILE%\s5k-188\private
set PY=%USERPROFILE%\ime-research\proto\.venv\Scripts\python.exe

rem smoke: first 20 rows of dev302, then check the five numbers with the same score checks as §8.2 (d)(h)
start "" /b /wait /affinity FFF %PY% run_ll_cuda.py --set dev302 --limit 20 --ctx none
start "" /b /wait /affinity FFF %PY% run_ll_cuda.py --set dev302 --limit 20 --ctx synth

rem full runs, only after the smoke is clean (each command resumes where it stopped)
for %S in (discordtune cvtune dev302 typing76) do start "" /b /wait /affinity FFF %PY% run_ll_cuda.py --set %S --ctx none
start "" /b /wait /affinity FFF %PY% run_ll_cuda.py --set discordtune --ctx real
```

(In a `.bat` file write `%%S`.) Exit 3 on a smoke run means the degeneracy stop fired; `s5k: STOP ...` or `s5k: INPUT ...` are the other stops. The private directory is deleted afterwards whatever the result (`rmdir /s /q %USERPROFILE%\s5k-188\private`, then `dir` to confirm), and only `Q8-ll.*.jsonl` and `meta-188.jsonl` are copied back, per §11.

`score.py` lists `Q8-ll` among the base conditions (paired with A-fwd and J-sent-fwd) and adds `vs_Q-ll=n=... fixed=... broken=... p=...` for every Q8-ll condition (`Q8-ll`, `+ctx`, `+ctxall` and their `@tau` variants): Q8-ll against Q-ll (1.7B), same condition, report only.

### Q8-ll results (run 2026-10-06 on 188, scored on the Mac)

Qwen3-8B (HF commit b968826d), nf4 with bf16 compute, RTX 3070. All sets ran `--ctx none`; discordtune also `--ctx real` (457 rows with a context). Boundary mismatches 0 everywhere. The batch stopped once after dev302 when its ssh session dropped (the next `echo` had no stdout); typing76 and discordtune `real` were rerun with output to a file. dev302 therefore has no `meta-188.jsonl`. Latency per row on discordtune: p50 491 ms, p95 508 ms without context; p50 629 ms with context.

| Condition (discordtune) | All 1,000 rows: acc, fixed/broken, p | Half B: acc, fixed/broken, p | vs Q-ll (1.7B), same condition, all rows |
|---|---|---|---|
| Q8-ll | 82.1%, 82/96, p = 0.33 | 82.0%, 37/49 | 66/63 (p = 0.86) |
| Q8-ll+ctxall | 85.3%, 89/71, p = 0.18 | 85.2%, 40/36 | 62/51 (p = 0.35) |
| Q8-ll@tau (τ = 0.6309) | 86.3%, 70/42 | 85.8%, 31/24, p = 0.42 | 29/29 (p = 1.0) |
| **Q8-ll+ctxall@tau** | 88.3%, 76/28 | **87.6%, 34/18, p = 0.037** | 32/21 (p = 0.17); half B 17/15 (p = 0.86) |

Rank-1 baseline: 83.5% on all rows, 84.4% on half B. cvtune (guard): Q8-ll 88.4% (+33, p = 0.0035), Q8-ll@tau 89.5% (+44, p < 0.001); no veto. dev302 92.4% (+41), typing76 88.2% (+2), recorded only.

- **Pre-registered decision (§5):** Q8-ll+ctxall@tau is an S5 candidate (half B p < 0.05 with a positive net, cvtune not vetoed). It is the first condition in S5k to pass. No other Q8-ll setting passes.
- **Caveats:** about 14 settings are tested at p < 0.05 without correction, so one pass by chance is plausible. Against the 1.7B model under the same condition the 8B is not significantly better (half B 17/15, p = 0.86); Q-ll+ctxall@tau had p = 0.081 on half B, so passing versus not passing is within noise.
- **Context helps (report only):** on the 457 rows with a context, adding it to Q8-ll fixed 38 and broke 6 (p < 0.0001); 1.7B 32/8.
- **Cost:** about 0.5–0.6 s per row on a desktop GPU; not usable inside the input method as is.
