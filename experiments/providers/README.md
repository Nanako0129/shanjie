# N1: cloud providers as the homophone judge (dev set only)

Code: `n1_providers.py` (one provider/model/effort per run), `run_none.sh` (the effort=none batch), `summarize.py` (the table in `results/summary.md`). Raw outputs: `results/*.jsonl` (per row: truth, the 16 candidates, picked index, first 200 chars of the reply, latency, OpenRouter's `served_by`), `results/*.txt` (metric lines), `results/run*.log`, `results/spend*.json` (per-batch ledgers).

## Task and conditions
- Each dev row: the unigram lattice N-best (deduplicated, top k=16) for the row's Zhuyin, plus the left context. The model sees the candidates numbered 1..16 and must answer `{"index": n}` (security rule R1: index only, never free text). `temperature 0`, `max_tokens 800`, `response_format` json_schema (strict); `--json-object` for Groq-served Llama, which rejects json_schema. An unparsable or out-of-range answer counts as invalid and falls back to candidate 1.
- `--reasoning`: OpenRouter `reasoning.effort`, other providers `reasoning_effort`; `default` sends neither.
- `--or-provider groq`: OpenRouter `provider.order=["groq"]`, `allow_fallbacks=false`; every row recorded `served_by: "Groq"`.
- Latency is wall time per request from a Mac on a home connection (one HTTPS connection reused per run), not server time.
- The dev set grew from 302 to 308 rows while batches ran (new user-reported rows are appended at the end), so `summarize.py` recomputes every cloud row from the jsonl on the common first 302 rows.
- Cost: OpenRouter rows use the cost OpenRouter reports. Cerebras, OpenGateway and Gemini rows are estimates from the `--price-in/--price-out` values passed on the command line (see the logs), not from invoices or verified price pages.

## Results
See `results/summary.md`. On the common 302 rows the candidate oracle@16 is 94.7%, so no judge over these candidates can exceed 94.7%; the best (OpenRouter gpt-6-luna, effort low) reached 92.7%.

Not measured: OpenRouter `qwen/qwen3.8-flash` (upstream 429 on three attempts, 1 and 5 rows saved), OpenRouter `z-ai/glm-5.3-flash` with effort none (400: reasoning is mandatory), Gemini `gemini-3.5-flash-lite` with effort none (400: invalid argument). OpenRouter-served gpt-oss on Groq also rejects effort none.
