# S5c: Cloudflare Clef-flash against Jev on public sets

Spec: `docs/contracts/s5c-clef-cloud.md` (conditions, hashes, decision rules and stop conditions are frozen there). This directory is the tooling only; nothing here touches `core/`, `macos/`, `data/` or `eval/`.

| File | Role |
|---|---|
| `clef_run.py` | Driver. `--sets cvtune,dev302,typing76 [--limit N]`. Resumable. HTTP layer is injectable (`Client(post=...)`). |
| `clef_score.py` | Offline scorer: S5j's metrics for C-sent-fwd, C-sent-rev, C-pos and (as a control) J-*, plus the C-vs-J pairs and the section 3 verdict. |
| `test_clef.py` | Offline tests (fake HTTP, decoding, scorer reproduction of S5j's Jev rows). |
| `results/` | Per-row results for dev302 and typing76 only (created by the run). |

S5j's constants and pure functions (`INSTR`, `POS_INSTR`, `BATCH`, `positions`, `pos_decision`, `mcnemar`, `pct`, `lenient`) are imported from `experiments/s5-judges/s5.py`. Its `set_dir`, `read_rows`, `score_set`, `prep` are never called; the `jev_conds`/`score_set` arithmetic is copied into the scorer. `sys.dont_write_bytecode` is set so no `__pycache__` appears in S5j's folders.

## Setup

Python 3 standard library only. The token comes from the environment, nothing else:

```sh
CF_AI_TOKEN=... CF_ACCOUNT_ID=... python3 -B experiments/s5-clef/clef_run.py --sets ...
```

The driver checks the set allowlist (cvtune, dev302, typing76; anything else, discordtune included, exits 1), then the input hashes, and only then reads the two variables and opens a connection. Endpoint: `POST https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/@cf/cloudflare/clef-flash` with `"model": "clef-flash"`. Workers AI has no version pinning: every record stores the response's `model` and the run date.

## Inputs (read-only, SHA-256 checked before they are opened; a missing or different file exits 1 with a fixed string and is never regenerated)

| Set | Directory | Files |
|---|---|---|
| cvtune | `~/.cache/shanjie/work/s5-judges/cvtune/` | `rows.jsonl` `98eec57f...e170d`, `jev-sent-fwd.jsonl` `ea8b5637...5594`, `jev-sent-rev.jsonl` `9cb4e99d...5462`, `jev-pos.jsonl` `cfea93c3...70a0`, `score.json` `e6420bd2...f3cef0` |
| dev302 | `experiments/s5-judges/results/dev302/` | `rows.jsonl` `70aaee64...5c39`, `jev-sent-fwd.jsonl` `5a909cf0...5fd5f`, `jev-sent-rev.jsonl` `a0a2d6c3...8cab53`, `jev-pos.jsonl` `844f3a43...9f411f9195`, `score.json` `2a2ebeb3...711373` |
| typing76 | `experiments/s5-judges/results/typing76/` | `rows.jsonl` `ed0bb913...bd34a`, `jev-sent-fwd.jsonl` `0577a26d...34e61`, `jev-sent-rev.jsonl` `32bb17c4...fbd75fca`, `jev-pos.jsonl` `37f22a6a...ab04a0`, `score.json` `59987391...ee5fb` |

Full digests are the `SHA` table in `clef_run.py` (the same values as contract section 2). `score.json` is opened only by the tests.

## Conditions

| Condition | Request (20 questions each, state `{"rows":[{"context": ""}, ...]}`) |
|---|---|
| C-sent-fwd | `INSTR`, 8 candidates in rank order |
| C-sent-rev | `INSTR`, the same 8 in reverse order |
| C-pos | `POS_INSTR`, one question per varied position, options[0] = unchanged rank 1, context empty (as S5j's J-pos actually sent) |

Public sets carry empty contexts, so C-sent and C-pos send empty contexts, like J-sent and J-pos.

Parsing, per question:

| Condition | Rule |
|---|---|
| C-sent | The `choice` field when it names a valid option (what S5j used); otherwise the highest probability, ties to the lowest number. Every option must have a probability, else the question is a parse failure. `choice_agree` = share where `choice` equals the top probability. |
| C-pos | Every option must have a probability (checked first, because S5j's `pos_decision` silently falls back to `choice`); then `pos_decision`: adopt when p >= 0.5 and >= 2x the unchanged option. |

A parse failure keeps rank 1 for that row and is counted (`unparsable`). Per-row files `clef-<cond>.jsonl` hold the raw `answers` (per-option probabilities) next to the decision (`picks`/`decisions`), seconds, input tokens, `tok_est`, `model`, `date`. No sentence text.

## Decision rules (contract section 3, cvtune only; dev302 and typing76 are recorded)

| Question | Rule |
|---|---|
| Is a condition a candidate for H and S6? | vs rank 1: p < 0.05 and net > 0, each of the three on its own |
| Clef vs Jev | McNemar on C-sent-fwd/J-sent-fwd, C-sent-rev/J-sent-rev, C-pos/J-pos. At least one significantly better and none significantly worse: "Clef better". The mirror: "Clef worse". None significant: "no significant difference" (choose on speed and price). Both directions: "mixed", listed per pair. |

`clef_score.py` prints these verdict lines for cvtune. Cloud evidence is weaker than the local experiments (discordtune cannot go to the cloud); the report must say so.

## Stop conditions the driver applies

| Event | Behaviour |
|---|---|
| 401, 403, 404, other 4xx | Stop at once (exit 1), fixed message with the status code. |
| 429, 5xx, timeout, connection error | Up to 3 retries after 2, 4, 8 s, then stop. |
| Response with no per-option probabilities for any question of a request | Stop; that request is not recorded. |
| Parse failures above 1% in a condition | Checked once at least 200 items are answered in that condition, or at its end (a 20-row smoke is checked at its end). Stop (the failed rows stay recorded; delete the condition's file to rerun it). |
| Spend at or above 1 USD | Checked before every request, so a resumed run already at the cap sends nothing; stop. Spend = input tokens x 0.09 USD per million, from the response `usage` (or `prompt_tokens`); with no `usage`, the request JSON's character count is used as an upper-bound estimate and `tok_est` is true. One cap for the whole experiment: `spent_usd(dirs)` sums every `clef-*.jsonl` under `~/.cache/shanjie/work/s5-clef/*` (all sets and the smoke `-nN` dirs) and `experiments/s5-clef/results/*`. |
| `--limit` smoke: both C-sent first-pick ratios >= 0.95 | Stop after printing. |
| Interrupted run | Rerun the same command; keys already in the output files are not resent. |

Stdout and stderr carry counts, numbers, HTTP statuses and fixed strings. An unexpected exception prints only its type.

## Where files go

| Run | Output |
|---|---|
| cvtune | `~/.cache/shanjie/work/s5-clef/cvtune/` |
| dev302, typing76 | `experiments/s5-clef/results/<set>/` |
| `--limit N` (dev302 or typing76 only) | `~/.cache/shanjie/work/s5-clef/<set>-nN/`, never the repo; the first N rows of the set |

`clef_score.py` writes `score.json` into the same directory. S5j's two locations are never written.

## Commands for main

`CF_AI_TOKEN` and `CF_ACCOUNT_ID` are read from the keychain item `cloudflare-workers-ai` (account field = account ID) into that one command's environment.

```sh
cd /Users/nanako/side-project/shanjie-s5c

# offline tests (no network)
python3 -B -m unittest discover -s experiments/s5-clef -p 'test_*.py'

# 1. real smoke: dev302 first 20 rows, every condition (about a dozen requests)
CF_AI_TOKEN=... CF_ACCOUNT_ID=... python3 -B experiments/s5-clef/clef_run.py --sets dev302 --limit 20
python3 -B experiments/s5-clef/clef_score.py --sets dev302 --limit 20     # first-pick, flip rate, accuracy
git status --porcelain                                                    # must show no path containing -n20

# 2. wrong token: must print "s5c: stop: HTTP 401: token or permission"
CF_AI_TOKEN=wrong-token CF_ACCOUNT_ID=... python3 -B experiments/s5-clef/clef_run.py --sets dev302 --limit 1

# 3. full run (about 400 requests, under 0.1 USD); rerun the same command to resume
CF_AI_TOKEN=... CF_ACCOUNT_ID=... python3 -B experiments/s5-clef/clef_run.py --sets cvtune,dev302,typing76
python3 -B experiments/s5-clef/clef_score.py --sets cvtune,dev302,typing76
```

The smoke prints the answer field names and probability keys (first request), the first-pick ratios for C-sent-fwd and C-sent-rev; the flip rate is in the scorer output.

## Limitations

- Workers AI offers no version pin; results are for the model as served on the run date.
- Cloudflare's blog says requests are not read, stored or trained on; the Workers AI docs page has no retention statement (unchecked). Only CC0 and CC BY public sentences are sent.
- The response envelope (`result` wrapper, `usage` key names, probabilities under which key) is assumed from the Jev-compatible API description; the driver finds the probability dict like S5j does (the first dict value holding every option) and stops rather than interpreting an unfamiliar shape. The real smoke is the first sight of it.
- Latency is per request, measured on the successful attempt from this Mac; it includes the network round trip and a fresh TLS connection each time (Jev reused one connection), so the comparison favours Jev slightly.
- Public rows have empty contexts, so this says nothing about context use.
- No discordtune data: the cloud comparison is weaker evidence than S5j's local conditions.
