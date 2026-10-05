# S5k: local order-free judges (Laya, Bonsai, Qwen3)

Spec: `docs/contracts/s5k-local-scorers.md`. This directory holds the tooling and, later, the dev302 / typing76 per-row results. Results, interpretation and the research log are written by main. Status: tooling smoke-tested on dev302 first 20 rows; B1-ll and B4-ll smoke-tested too, under venv B.

## Files

| File | Role |
|---|---|
| `s5k.py` | shared: SHA-256 table (contract §2), hash-checked row loading, output paths, tie-break pick, degeneracy check. Forces `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1` |
| `run_laya.py` | L-noul, L-choice-fwd, L-choice-rev, L-pos (venv A) |
| `run_ll.py` | Q-ll (venv A), B1-ll / B4-ll (venv B) |
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
