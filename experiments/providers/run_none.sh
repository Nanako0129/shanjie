#!/bin/bash
# 所有模型的 reasoning=none 對照組（使用者 2026-10-03 要求）。四家平行，各用一份帳本、各自 US$0.5 上限。
# Gemini 與 OpenGateway 先等目前的 low 批次跑完，避免同時打同一家（Gemini 429 要通知使用者換 key）。
# key 由呼叫者放在環境變數；本腳本不讀鑰匙圈、不印 key。
set -u
cd "$(dirname "$0")"
R=results
run() {  # provider model price_in price_out log ledger
  echo "=== $1 $2 effort=none $(date +%T)" >> "$5"
  N1_LEDGER="$R/$6" python3 n1_providers.py "$1" "$2" --reasoning none --price-in "$3" --price-out "$4" --budget-usd 0.5 >> "$5" 2>&1 \
    || echo "FAILED $1 $2 none" >> "$5"
}
(
  run cerebras qwen-3.8-27b 0.6 1.2 "$R/run-none-cerebras.log" spend-none-cerebras.json
  echo "NONE-CEREBRAS DONE $(date +%T)" >> "$R/run-none-cerebras.log"
) &
(
  for m in deepseek/deepseek-v4.1-flash z-ai/glm-5.3-flash qwen/qwen3.8-flash; do
    run openrouter "$m" 0.6 1.2 "$R/run-none-openrouter.log" spend-none-openrouter.json
  done
  echo "NONE-OPENROUTER DONE $(date +%T)" >> "$R/run-none-openrouter.log"
) &
(
  while pgrep -f "n1_providers.py gemini" > /dev/null; do sleep 15; done
  run gemini gemini-3.5-flash-lite 0.3 2.5 "$R/run-none-gemini.log" spend-none-gemini.json
  run gemini gemini-3.8-flash 0.75 3.75 "$R/run-none-gemini.log" spend-none-gemini.json
  echo "NONE-GEMINI DONE $(date +%T)" >> "$R/run-none-gemini.log"
) &
(
  until grep -q '^OG DONE' "$R/run-og.log" 2>/dev/null; do sleep 15; done
  for m in z-ai/glm-5.3-flash-ultrafast openai/gpt-6-luna moonshotai/kimi-k3-ultrafast; do
    run opengateway "$m" 1 3 "$R/run-none-og.log" spend-none-og.json
  done
  echo "NONE-OG DONE $(date +%T)" >> "$R/run-none-og.log"
) &
wait
echo "ALL NONE DONE $(date +%T)"
