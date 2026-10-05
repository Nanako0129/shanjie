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
- The Clef half and the feasibility table follow once the Workers AI token exists and S5c's full run is done (contract §8, §12).
