| 來源 | 模型 | effort | n | 整句 | 寬鬆 | p50 ms | p95 ms | 不合法 | US$/千句 |
|---|---|---|---|---|---|---|---|---|---|
| openrouter | openai_gpt-6-luna | low | 302 | 92.7% | 93.4% | 1136 | 3146 | 3 | 0.048 |
| cerebras | gpt-oss-120b | medium | 302 | 92.4% | 93.0% | 356 | 740 | 3 | 0.994 |
| gemini | gemini-3.8-flash | none | 302 | 92.4% | 93.0% | 1241 | 2188 | 1 | 0.216 |
| openrouter | openai_gpt-6-luna | none | 302 | 92.1% | 93.0% | 1058 | 1449 | 4 | 0.036 |
| gemini | gemini-3.8-flash | low | 302 | 92.1% | 92.7% | 1235 | 2456 | 1 | 0.216 |
| openrouter | openai_gpt-6-luna | medium | 302 | 92.1% | 92.7% | 2190 | 4340 | 0 | 0.058 |
| 本機 188 | M1-nbest32/gemma | — | 302 | 91.4% | 93.7% | 295 | 848 | — | — |
| opengateway | openai_gpt-6-luna | none | 302 | 91.4% | 92.7% | 1054 | 1709 | 0 | 0.342 |
| opengateway | openai_gpt-6-luna | low | 302 | 91.4% | 92.4% | 1162 | 2345 | 0 | 0.424 |
| opengateway | deepseek_deepseek-v4.1-flash-ultrafast | low | 302 | 91.4% | 92.4% | 1055 | 6445 | 10 | 0.623 |
| opengateway | moonshotai_kimi-k3-ultrafast | low | 302 | 91.1% | 92.1% | 4057 | 7779 | 1 | 0.794 |
| cerebras | qwen-3.8-27b | none | 302 | 90.4% | 91.4% | 309 | 438 | 0 | 0.237 |
| opengateway | deepseek_deepseek-v4.1-flash-ultrafast | none | 302 | 90.4% | 92.7% | 378 | 1590 | 0 | 0.269 |
| opengateway | z-ai_glm-5.3-flash-ultrafast | none | 302 | 90.4% | 91.4% | 546 | 1601 | 0 | 0.320 |
| opengateway | z-ai_glm-5.3-flash-ultrafast | low | 302 | 90.4% | 91.4% | 509 | 1858 | 0 | 0.320 |
| opengateway | moonshotai_kimi-k3-ultrafast | none | 302 | 90.4% | 92.1% | 2058 | 2852 | 0 | 0.435 |
| openrouter | deepseek_deepseek-v4.1-flash | low | 302 | 90.4% | 91.4% | 1253 | 5902 | 13 | 0.147 |
| cerebras | gpt-oss-120b | low | 302 | 90.1% | 90.7% | 298 | 510 | 0 | 0.347 |
| openrouter@groq | openai_gpt-oss-120b | low | 302 | 90.1% | 90.7% | 368 | 688 | 0 | 0.093 |
| cerebras | qwen-3.8-27b | low | 302 | 89.7% | 90.7% | 594 | 977 | 18 | 0.787 |
| openrouter | z-ai_glm-5.3-flash | low | 302 | 89.4% | 90.7% | 520 | 1349 | 0 | 0.185 |
| openrouter | deepseek_deepseek-v4.1-flash | none | 302 | 89.4% | 90.7% | 988 | 1978 | 0 | 0.040 |
| 本機 188 | M1-nbest32/qwen | — | 302 | 87.1% | 91.1% | 153 | 171 | — | — |
| gemini | gemini-3.5-flash-lite | low | 302 | 87.1% | 88.4% | 743 | 1021 | 3 | 0.092 |
| openrouter@groq | openai_gpt-oss-20b | low | 302 | 85.8% | 87.4% | 580 | 842 | 12 | 0.044 |
| openrouter@groq | meta-llama_llama-3.3-70b-instruct | default | 302 | 85.4% | 87.7% | 230 | 360 | 2 | 0.198 |
| 本機 188 | M2-B8/gemma | — | 302 | 82.5% | 88.4% | 613 | 800 | — | — |
| TypeSafe（候選 8） | jev-1.13.0 | — | 300 | 81.0% | 82.7% | 187 | 229 | — | — |
| 本機 188 | M2-B4/gemma | — | 302 | 77.2% | 84.1% | 608 | 783 | — | — |
| 本機 188 | M2-B8/qwen | — | 302 | 76.2% | 80.1% | 333 | 444 | — | — |
| 本機 188 | M2-B4/qwen | — | 302 | 73.2% | 77.2% | 326 | 427 | — | — |
| openrouter@groq | meta-llama_llama-3.1-8b-instruct | default | 302 | 72.8% | 78.5% | 221 | 344 | 0 | 0.017 |

雲端 k=16 候選在前 302 列的 oracle（正解在候選內的比例）：94.7%

未跑滿 302 列（不列入比較）：
- gemini gemini-3.5-flash-lite none：0 列
- openrouter qwen_qwen3.8-flash low：1 列
- openrouter qwen_qwen3.8-flash none：5 列
- openrouter z-ai_glm-5.3-flash none：0 列

全部帳本合計：US$2.24
