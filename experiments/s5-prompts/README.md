# S5p: cloud prompt variants, Jev vs Clef-flash

Spec: `docs/contracts/s5p-cloud-prompts.md` (texts, rules and stops are frozen there). Tooling only; reuses S5c's `Client` (`experiments/s5-clef/clef_run.py`). All network calls are run by main.

| File | Role |
|---|---|
| `p_run.py` | Driver: `--provider jev\|clef --sets S[,S] [--half A\|B] [--variants v1,v2,v3,v4] [--limit N]`; `--print-request`; `--check-examples`. Resumable, one request per row. |
| `p_score.py` | `select` (A half) and `test --jev vN --clef vN` (B half, the 5 named tests). |
| `test_prompts.py` | Offline tests: `python3 -B -m unittest discover -s experiments/s5-prompts` |

Keys: Jev `TYPESAFE_API_KEY` (else `~/.config/typesafe/api_key`), Clef `CF_AI_TOKEN` + `CF_ACCOUNT_ID`; read only after the set allowlist and hash checks. Clef refuses to run until `CLEF_V0_SHA` in `p_run.py` holds the contract section 12 hash. Output: `~/.cache/shanjie/work/s5-prompts/<set>[-nN]/{jev,clef}-<variant>.jsonl` (cvtune and smoke), `results/<set>/` for full dev302/typing76 runs. Budget US$2 shared (Clef from tokens, Jev estimated at S5j's J1 rate); Jev hard cap 6,000 requests.
