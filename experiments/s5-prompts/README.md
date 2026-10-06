# S5p: cloud prompt variants, Jev vs Clef-flash

Spec: `docs/contracts/s5p-cloud-prompts.md` (texts, rules and stops are frozen there). Tooling only; reuses S5c's `Client` (`experiments/s5-clef/clef_run.py`). All network calls are run by main.

| File | Role |
|---|---|
| `p_run.py` | Driver: `--provider jev\|clef --sets S[,S] [--half A\|B] [--variants v1,v2,v3,v4] [--limit N]`; `--print-request`; `--check-examples`. Resumable, one request per row. |
| `p_score.py` | `select` (A half) and `test --jev vN --clef vN` (B half, the 5 named tests). |
| `test_prompts.py` | Offline tests: `python3 -B -m unittest discover -s experiments/s5-prompts` |

Keys: Jev `TYPESAFE_API_KEY` (else `~/.config/typesafe/api_key`), Clef `CF_AI_TOKEN` + `CF_ACCOUNT_ID`; read only after the set allowlist and hash checks. Clef refuses to run until `CLEF_V0_SHA` in `p_run.py` holds the contract section 12 hash. Output: `~/.cache/shanjie/work/s5-prompts/<set>[-nN]/{jev,clef}-<variant>.jsonl` (cvtune and smoke), `results/<set>/` for full dev302/typing76 runs. Budget US$2 shared (Clef from tokens, Jev estimated at S5j's J1 rate); Jev hard cap 6,000 requests.

## Results: Jev half (2026-10-06)

Jev `jev-1.13.0` ran on public sets only. Total estimated spend was US$0.16: 4,876 requests, under the 6,000 cap and the US$2 budget. The price is not published, so the estimate uses S5j's billed J1 rate.

**Smoke (dev302, first 20 rows):**
- V1 and V3 picked c1 on 0.80 of rows, and no row had all-equal P(true).
- V2 and V4 picked the first-presented option on 0.90–0.95 of rows in forward order and 0 in reverse, so Jev follows content, not position.
- The first smoke stopped on "no per-option probabilities". A probe with a self-written sentence showed Jev's noul answer is `{"type": "noul", "noul": p}`; the parser was fixed (see git log).

**Half A (cvtune, 500 rows): selection.**

| Variant | Accuracy |
|---|---|
| V1, noul per candidate, English | 89.2% |
| V2, structured choice, English, forward | 91.0% |
| V3, noul per candidate, Chinese | 89.6% |
| **V4, structured choice, Chinese, forward** | **91.2%**, selected |

**Half B (cvtune, 500 rows): pre-registered tests.**

| Test | Fixed | Broken | Net | p | Verdict |
|---|---|---|---|---|---|
| V4 vs V0 (S5j J-sent-fwd) | 14 | 5 | +9 | 0.064 | no significant difference |
| V4 vs rank 1 | 37 | 7 | +30 | 5.3e-06 | candidate for H/S6 |

- **V4 on half B:** accuracy 91.4% (V0 89.6%, rank 1 85.4%) and flip rate 9.4%.
- **Latency:** one request per row, p50 207 ms and p95 260 ms.
- **Public sets (recorded only):** dev302 V4 274/302 = 90.7% (V0 91.7%), typing76 71/76 = 93.4% (V0 93.4%).

**Reading:**
- The documented prompt style (Chinese instructions; a named state with context, reading and candidates) trends better than the original English list prompt (+9 on half B), but the improvement is not established.
- Per-candidate noul (V1, V3), the docs' rerank recipe, was the weakest of the four on half A.

## Clef results (clef-flash and the full 27B, run 2026-10-06; contract §12, §13)

V0 for each Clef model is its S5c 8-way forward file (hashes in §12). Total S5p spend by the driver's ledger: US$1.37 including Jev's estimated US$0.16, under the US$2 cap (Clef from reported tokens at list prices, not reconciled with the bill).

**Half A (cvtune, 500 rows): selection** (choice variants scored on forward order only).

| Variant | Jev | clef-flash | clef 27B |
|---|---|---|---|
| V1, noul per candidate, English | 89.2% | 84.0% | 88.2% |
| V2, structured choice, English | 91.0% | 87.4% | **91.2%**, selected (tie with V4, lower number) |
| V3, noul per candidate, Chinese | 89.6% | 85.2% | 87.8% |
| V4, structured choice, Chinese | **91.2%**, selected | **88.2%**, selected | 91.2% |

**Half B (cvtune, 500 rows): the 8 pre-registered tests.**

| Test | Fixed | Broken | Net | p | Verdict |
|---|---|---|---|---|---|
| Jev V4 vs V0 | 14 | 5 | +9 | 0.064 | no significant difference |
| Jev V4 vs rank 1 | 37 | 7 | +30 | 5.3e-06 | candidate for H/S6 |
| flash V4 vs V0 | 142 | 17 | +125 | 9.5e-26 | prompt improvement holds |
| flash V4 vs rank 1 | 39 | 19 | +20 | 0.012 | candidate for H/S6 |
| 27B V2 vs V0 | 70 | 9 | +61 | 7.8e-13 | prompt improvement holds |
| 27B V2 vs rank 1 | 40 | 7 | +33 | 1.1e-06 | candidate for H/S6 |
| flash V4 vs Jev V4 | 8 | 18 | −10 | 0.076 | no significant difference |
| 27B V2 vs Jev V4 | 8 | 5 | +3 | 0.58 | no significant difference |

Report only (not a test): 27B V2 vs flash V4, 19 fixed / 6 broken, p = 0.015.

- The Clef V0 was the 8-way list in rank order, on which both Clef models were strongly order-sensitive (S5c). The structured Chinese and English prompts lift them to Jev's level; the big V4/V2-vs-V0 gains mostly measure V0's weakness.
- **Flip rate** between the two orders of the chosen choice variant on half B: Jev 9.4%, flash 16.4%, 27B 9.4%.
- **Public sets (recorded only)**, rank 1 / Jev V4 / flash V4 / 27B V2: dev302 238 / 274 / 277 / 289 of 302; typing76 65 / 71 / 69 / 72 of 76.

**Feasibility (contract §6; numbers from half B of each chosen variant).**

| | Jev (jev-1.13.0) | clef-flash | clef (27B) |
|---|---|---|---|
| Accuracy on half B | 91.4% | 89.4% | 92.0% |
| vs rank 1 | +30 (37/7) | +20 (39/19) | +33 (40/7) |
| Latency per row (one request), p50 / p95 | 207 / 260 ms | 422 / 921 ms | 806 / 1,443 ms |
| Cost per 1,000 rows | unverified (no published price) | US$0.043 (tokens x US$0.09/M) | US$0.117 (tokens x US$0.24/M) |
| Version pinning | response names `jev-1.13.0`; pinning unverified | none (S5c) | none (S5c) |
| Request limits | unverified | unverified | 65,536-token context (Cloudflare docs) |
| Open weights / self-host | unverified | unverified | unverified |
| Data retention and training | unverified | Cloudflare blog: not stored or trained on; docs page has no statement (S5c) | same as flash |

**Reading:**
- All three are candidates against rank 1 on half B, and none differs significantly from another in the pre-registered tests. 27B has the highest accuracy and the lowest flip rate with Jev, but it is four times slower than Jev.
- Latencies are from this Mac per request with a fresh TLS connection for Clef; Jev reused one connection.
