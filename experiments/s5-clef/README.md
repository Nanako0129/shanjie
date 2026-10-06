# S5c: Cloudflare Clef-flash against Jev on public sets

Spec: `docs/contracts/s5c-clef-cloud.md` (conditions, hashes, decision rules and stop conditions are frozen there). This directory is the tooling only; nothing here touches `core/`, `macos/`, `data/` or `eval/`.

| File | Role |
|---|---|
| `clef_run.py` | Driver. `--sets cvtune,dev302,typing76 [--limit N] [--model clef-flash\|clef]` (`clef` is the 27B, files `clef27-*`). Resumable. Requests carry `cf-aig-gateway-id`. HTTP layer is injectable (`Client(post=...)`). |
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

`CF_AI_TOKEN` and `CF_ACCOUNT_ID` come from the environment. Only main runs paid calls; agent briefs forbid running `clef_run.py`.

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

## Results (run 2026-10-06, model `clef-flash` as served that day)

Smoke (dev302, first 20 rows): answers carry `choice`, `confidence`, `probabilities`, `type`; probabilities are keyed `c1`..`c8`; a wrong token stops with `HTTP 401: token or permission`. Full run: 357 requests. Spend, smoke included: US$0.139, computed by the driver from the input tokens Clef reported (`tokens_estimated=False`) at the US$0.09 per million list price; not checked against the Cloudflare bill.

cvtune (decides; 1,000 rows, rank-1 baseline 85.1%):

| Condition | Accuracy | vs rank 1 (fixed/broken, p) | First pick | Flip (fwd vs rev) | Latency p50/p95 per request of 20 questions |
|---|---|---|---|---|---|
| C-sent-fwd | 62.3% | 89/317, p = 4e-31 | 0.316 | | 780/1458 ms |
| C-sent-rev | 88.3% | 82/50, p = 0.0067 | 0.008 | 0.544 | 650/956 ms |
| C-pos | 80.0% | 61/112, p = 0.0001 | | | 567/899 ms |
| J-sent-fwd (S5j) | 89.7% | 67/21 | 0.883 | | 220/292 ms |
| J-sent-rev (S5j) | 88.5% | 71/37 | 0.021 | 0.112 | 213/268 ms |
| J-pos (S5j) | 89.0% | 66/27 | | | 207/280 ms |

Paired with Jev on cvtune: C-sent-fwd vs J-sent-fwd 30/304 (p ≈ 0), C-sent-rev vs J-sent-rev 35/37 (p = 0.91), C-pos vs J-pos 21/111 (p ≈ 0).

Verdicts by the pre-registered rules (contract section 3):
- **vs rank 1**: C-sent-rev is a candidate for H/S6 (net +32, p = 0.0067); C-sent-fwd and C-pos are not.
- **vs Jev: Clef worse than Jev** (two pairs significantly worse, none better).

Order sensitivity (measured from the per-row picks): the same rank-1 candidate is picked on 31.6% of rows when it is listed first (forward) and on 76.9% when it is listed last (reversed); picks by presented position are 316/280/130/85/58/44/49/38 forward and 8/12/15/18/36/45/97/769 reversed, and 54% of rows change answer between the two orders, against 11% for Jev. This is not a plain preference for the last slot (forward, the last slot gets 3.8%); the mechanism was not tested. Because the C-sent-rev candidate verdict holds in one order and fails badly in the other, it is not a basis for H/S6 without an order-free design.

dev302 and typing76 (recorded only): C-sent-fwd 72.9% / 73.7% (rank 1: 78.8% / 85.5%), C-sent-rev 91.1% / 89.5%, C-pos 84.1% / 86.8%, flip 0.42 / 0.36. They differ from cvtune in places: on dev302, C-pos beats rank 1 (+16, p = 0.033) where cvtune loses, and C-sent-fwd is not significant (p = 0.13). Per-row files and `score.json` are in `results/`.

## Results: full Clef, 27B (run 2026-10-06, `--model clef`, contract section 10)

Same questions, sets and parsing; only the model changed. Files are `clef27-*.jsonl` next to the flash ones. Spend for the whole experiment (both models, smokes included) is US$0.509 by the driver's ledger at list prices, under the US$1 cap; the 27B share is about US$0.37. Not checked against the Cloudflare bill.

cvtune (decides; rank-1 baseline 85.1%):

| Condition | Accuracy | vs rank 1 (fixed/broken, p) | vs Jev, same condition (fixed/broken, p) | vs flash, same condition (report only) | First pick | Flip | Latency p50/p95 per request |
|---|---|---|---|---|---|---|---|
| C27-sent-fwd | 80.0% | 99/150, p = 0.0015 | 44/141, p ≈ 0 | 210/33 | 0.500 | | 1733/2691 ms |
| C27-sent-rev | 90.5% | 81/27, p = 2e-7 | 39/19, p = 0.012 | 38/16 | 0.004 | 0.36 | 1800/2512 ms |
| C27-pos | 85.7% | 64/58, p = 0.65 | 25/58, p = 0.0004 | 90/33 | | | 1484/3399 ms |

Verdicts by the pre-registered rules:
- **vs rank 1**: C27-sent-rev is a candidate for H/S6 (net +54); C27-sent-fwd is significantly worse; C27-pos is not significant.
- **vs Jev: mixed**. C27-sent-rev is significantly better than J-sent-rev; C27-sent-fwd and C27-pos are significantly worse than their Jev pairs.

Order sensitivity is smaller than flash's but still large: the rank-1 candidate is picked on 50.0% of rows when listed first and on 81.5% when listed last; picks by presented position are 500/246/96/57/34/26/24/17 forward and 4/5/5/10/17/39/105/815 reversed; 36% of rows change answer between orders (flash 54%, Jev 11%). As with flash, the win holds in one order only, so it is not a basis for H/S6 without an order-free design.

dev302 and typing76 (recorded only): C27-sent-fwd 85.4% / 90.8%, C27-sent-rev 95.0% / 97.4%, C27-pos 87.7% / 89.5% (rank 1: 78.8% / 85.5%; J-sent-fwd 91.7% / 93.4%). Flip 0.25 / 0.13. Every C27 condition beats its flash counterpart on cvtune (p < 0.004 each, report only).

Latency is about 2.5 times flash's and 8 times Jev's per request of 20 questions; one dev302 request took 13.3 s.

## Limitations

- Workers AI offers no version pin; results are for the model as served on the run date.
- Cloudflare's blog says requests are not read, stored or trained on; the Workers AI docs page has no retention statement (unchecked). Only CC0 and CC BY public sentences are sent.
- The response envelope (`result` wrapper, `usage` key names, probabilities under which key) is assumed from the Jev-compatible API description; the driver finds the probability dict like S5j does (the first dict value holding every option) and stops rather than interpreting an unfamiliar shape. The real smoke is the first sight of it.
- Latency is per request, measured on the successful attempt from this Mac; it includes the network round trip and a fresh TLS connection each time (Jev reused one connection), so the comparison favours Jev slightly.
- Public rows have empty contexts, so this says nothing about context use.
- No discordtune data: the cloud comparison is weaker evidence than S5j's local conditions.
