# N0: semantic decoding feasibility (dev set only)

Code: `n0.py` (M0/M1/M2), `run.sh` (runs one config on 188), `sync.sh` (copies dev-only working set to 188, never `eval/holdout`), `diag.py` (reachability diagnostic). Raw outputs: `results/` (`*.txt` metric lines, `*.jsonl` per-row outputs; `*-pin*` = final pinned runs, files without `-pin` are the first unpinned runs).

Commands (Mac, from this dir; env `N0_PIN=1` = pinned):
```
bash sync.sh
N0_PIN=1 ./run.sh <qwen|gemma> m1 4 "" <tag>        # M1: unigram beam-32 N-best + sentence-end LLM rerank
N0_PIN=1 ./run.sh <qwen|gemma> m2 <4|8> "" <tag>    # M2: reading-constrained beam search, HF generate
N0_PIN=1 ./run.sh <model> m2 <B> 50 stab-<model>-b<B>-<a|b>   # stability
```
`run.sh` runs on 188: `python experiments\n0\n0.py --method m2 --model gemma --beams 8 --out ...` with `PYTHONUTF8=1 HF_HUB_OFFLINE=1`. M0: `--method m0`.

## Measurement conditions (every latency number)
Host 188: Intel i5-12600K (hybrid), RTX 3070 8 GB. Final latencies: process pinned to P-cores, affinity mask 0xFFF (logical 0-11), priority HIGH (set from inside Python via kernel32 at startup, `N0_PIN=1`; no system-wide settings touched). Unpinned runs: default affinity (0xFFFF), Normal priority. Latency = wall time per sentence incl. N-best decode, tokenization, all forward passes, CUDA sync; one model warm-up call before timing; p50 = sorted[n//2], p95 = sorted[int(0.95 n)].

Dev set = 302 rows (existing 109, homophones 165, oov 25, user-reported 3). user-reported.txt grew from 1 to 3 rows while I worked (file mtime 05:51); the unpinned runs saw 300 rows (1 user-reported row), pinned runs saw 302. Accuracy on the other 299 rows was identical between pinned and unpinned runs (deterministic).

## Results (pinned, final). sent_acc / lenient_acc ; p50 / p95 ms
| config | dev (302) | existing (109) | homophones (165) | oov (25) | user-reported (3) | peak GPU MiB |
|---|---|---|---|---|---|---|
| M0 unigram | 0.517 / 0.526 | 0.688 / 0.697 | 0.461 / 0.473 | 0.200 / 0.200 | 0.0 / 0.0 | - |
| M1 gemma | 0.914 / 0.937 ; 295 / 848 | 0.963 / 0.982 ; 290 / 402 | 0.909 / 0.939 ; 322 / 989 | 0.760 / 0.760 ; 285 / 315 | 0.667 / 0.667 ; 282 / 908 | 5776 |
| M2 B4 gemma | 0.772 / 0.841 ; 608 / 783 | 0.780 / 0.862 ; 553 / 742 | 0.782 / 0.848 ; 618 / 783 | 0.720 / 0.760 ; 671 / 800 | 0.333 / 0.333 ; 866 / 2182 | 5305 |
| M2 B8 gemma | 0.825 / 0.884 ; 613 / 800 | 0.817 / 0.890 ; 551 / 770 | 0.842 / 0.903 ; 627 / 780 | 0.800 / 0.800 ; 706 / 842 | 0.333 / 0.333 ; 913 / 1920 | 5329 |
| M1 qwen | 0.871 / 0.911 ; 153 / 171 | 0.890 / 0.954 ; 146 / 164 | 0.879 / 0.909 ; 155 / 175 | 0.760 / 0.760 ; 158 / 161 | 0.667 / 0.667 ; 165 / 188 | 3587 |
| M2 B4 qwen | 0.732 / 0.772 ; 326 / 427 | 0.734 / 0.798 ; 270 / 363 | 0.770 / 0.800 ; 335 / 423 | 0.560 / 0.560 ; 368 / 457 | 0.0 / 0.0 ; 532 / 1050 | 3320 |
| M2 B8 qwen | 0.762 / 0.801 ; 333 / 444 | 0.798 / 0.853 ; 296 / 400 | 0.788 / 0.824 ; 350 / 436 | 0.520 / 0.520 ; 384 / 519 | 0.0 / 0.0 ; 532 / 1319 | 3352 |

M0 latency is not applicable (sub-ms Rust/Python lookup; CLI M0 = 0.52 on the original 300 rows, Python M0 on 300 rows also 0.52). B=16 not run. char_acc and raw metric dicts are in `results/*.txt`.

## Unpinned vs pinned (dev, p50 / p95 ms, same code)
| config | unpinned (300 rows) | pinned (302 rows) |
|---|---|---|
| M2 B8 gemma (best M2) | 1267 / 1640 | 613 / 800 |
| M2 B4 gemma | 1145 / 1542 | 608 / 783 |
| M1 gemma | 550 / 926 | 295 / 848 |
| M1 qwen | 317 / 348 | 153 / 171 |
| M2 B4 qwen | 753 / 963 | 326 / 427 |
| M2 B8 qwen | 890 / 3255 | 333 / 444 |
Pinning roughly halves latency. Gemma M1/M2 p95 tails (848 ms M1 on homophones, ~2 s on user-reported) are the long sentences / long context.

## Stability (pinned, first 50 dev rows, each M2 config run twice)
Differences between the two runs: qwen B4 0, qwen B8 0, gemma B4 0, gemma B8 0 sentences (also identical to the same rows in the full run). No nondeterminism.

## Per-layer embedding (Gemma 4 E2B)
Loaded the whole model on CPU (no device_map), temporarily swapped `model.language_model.embed_tokens_per_layer` for `Identity`, called `model.to("cuda")`, put the table back on CPU, and replaced its `forward` with a wrapper: lookup on CPU, move only the looked-up rows to CUDA. Worked: peak GPU 5.3-5.8 GiB, generation correct. Latency vs the prior accelerate-offload run (2.8 s/sentence) is not a like-for-like comparison (different task), but the 20-row smoke test of M2 B4 ran at ~1.07 s p50 unpinned.

## M2 caveats (diagnostics, `results/diag.txt`)
- Allowed-token rule exactly as specified (pure-Han token, per-char reading from single-syllable lexicon entries). Truth chars unreachable because of the reading table: only rows containing 媽 (2/302; reading not in the single-syllable entries; the 認爲/部份 in the third user-reported output are valid reachable variants that the model preferred (not a reachability failure)).
- Qwen: 34/302 rows contain a char with no single-char Han token (byte-fallback chars cannot be generated under the "pure-CJK token" rule); Gemma: 0/302. This depresses M2-Qwen (oov especially), not M2-Gemma.
- M2 favours LLM-fluent common words over the truth for rare words (試鏡布 for 拭鏡布, 脫鞋架 for 拖鞋架) and picks the Gemma-preferred variant 藍芽 over 藍牙.
- M1 has the unigram candidate set as an upper bound (oracle@32 = 96.7% on the original 300 rows) while M2 has none.

## Gemma M2 B8 (best M2): the 25 OOV rows (truth -> output)
| truth | output | |
|---|---|---|
| 我買了一條拭鏡布 | 我買了一條試鏡布 | MISS |
| 我用洗衣球洗了一堆衣服 | 我用洗衣球洗了一堆衣服 | ok |
| 我新買的藍牙耳機很好用 | 我新買的藍芽耳機很好用 | MISS |
| 請放在隔熱墊上 | 請放在隔熱墊上 | ok |
| 我把線材都放進收納盒 | 我把線材都放進收納盒 | ok |
| 出門前記得帶充電線 | 出門前記得帶充電線 | ok |
| 浴室地板要鋪防滑墊 | 欲試的板要鋪防滑墊 | MISS |
| 這罐密封罐可以裝餅乾 | 這罐密封罐可以裝餅乾 | ok |
| 我買了一個螢幕支架 | 我買了一個螢幕支架 | ok |
| 廚房新裝了一個濾水壺 | 廚房新裝了一個濾水壺 | ok |
| 請把衣服放進脫水籃 | 請把衣服放進脫水籃 | ok |
| 我幫新平板買了平板殼 | 我幫新平版買了平板殼 | MISS |
| 可以墊一個散熱墊 | 可以墊一個散熱墊 | ok |
| 早上我用咖啡濾紙泡咖啡 | 早上我用咖啡濾紙泡咖啡 | ok |
| 請拿洗碗海綿給我 | 請拿洗碗海綿給我 | ok |
| 下午我們去買手搖杯 | 下午我們去買手搖杯 | ok |
| 店員問我要不要電子發票 | 店員問我要不要電子發票 | ok |
| 我想買一台空氣炸鍋 | 我想買一台空氣炸鍋 | ok |
| 我帶了行動電源 | 我帶了行動電源 | ok |
| 下雨天要帶摺疊傘 | 下雨天要帶摺疊傘 | ok |
| 玄關裝了一個拖鞋架 | 玄關裝了一個脫鞋架 | MISS |
| 這個保險箱用的是密碼鎖 | 這個保險箱用的是密碼鎖 | ok |
| 我只喝無糖的氣泡水 | 我只喝無糖的氣泡水 | ok |
| 這家店的排骨酥很好吃 | 這家店的排骨酥很好吃 | ok |
| 我需要一個轉接頭 | 我需要一個轉接頭 | ok |
## User-reported sentences (3)
| truth | M2 B8 gemma | M1 gemma | M1 qwen |
|---|---|---|---|
| 什麼品牌或款式的拭鏡布比較好 | 什麼品牌和款式的試鏡布比較好 (miss) | 什麼品牌或款式的試鏡不比較好 (miss) | 什麼品牌或款式的是敬不比較好 (miss) |
| 我想參考蘋果原生的介面 | ok | ok | ok |
| 所以我認為針對高延遲的部分可以在使用者輸入完用一個快速鍵自動校正 | 索以我認爲真對高延遲的部份可以… (miss) | ok | ok |

## Mac (M5) latency, partial, measured under load (2026-10-03)
The user approved measuring latency only on the Mac. `mac_latency.py` runs the same M1 method with MLX (`~/side-project/ime-research/proto/.venv`, mlx 0.32.3, mlx-lm 0.32.0) on every 5th of the first 302 dev rows (61 rows); accuracy there is only a 4-bit sanity check. Qwen 4-bit was converted locally from the cached `Qwen/Qwen3-1.7B-Base` (`mlx_lm convert -q --q-bits 4`, 948 MB); Gemma would use `mlx-community/gemma-4-e2b-4bit` (downloaded, not measured).

| config | sent / lenient (61 rows) | p50 / p95 ms | peak MiB |
|---|---|---|---|
| Qwen 4-bit, chunk 8 | 0.803 / 0.934 | 893 / 1558 | 1324 |
| Qwen 4-bit, chunk 32 | 0.820 / 0.918 | 487 / 1175 | 2076 |
| Qwen bf16, chunk 8 | 0.852 / 0.918 | 814 / 1137 | 3537 |
| Qwen bf16, chunk 32 | 0.852 / 0.918 | 629 / 980 | 4139 |

Not like-for-like with 188 and not a final number: other cloud benchmark batches were running, and the user reported the machine lagging, so the runs were stopped (Gemma never ran). A diagnostic on Qwen 4-bit, chunk 32, confirmed MLX ran on `Device(gpu, 0)` with 4-bit packed weights (923 MiB of Metal buffers) and split the per-sentence time into Python N-best decode (median 6 ms) and MLX scoring (median 397 ms); one 1x40-token forward took 117 ms under that load. Remeasure only with the user's consent while the Mac is otherwise idle (PLAN S5), with prefix KV caching and fewer candidates.
