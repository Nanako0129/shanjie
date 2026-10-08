# 評測資料

| 路徑 | 列數 | 讀音 | 用途 |
|---|---|---|---|
| `sets/trap.txt`、`sets/daily.txt`、`sets/moedict.txt` | 64、45、300 | 自動產生（`to_syllables`） | S0 golden 專用，不再修改 |
| `dev/existing.txt` | 109 | 確認過 | 同 trap＋daily，讀音改成使用者實際會打的 |
| `dev/homophones.txt` | 165 | 確認過 | 同音陷阱與單字混淆（同音組取奇數行） |
| `dev/oov.txt`、`dev/oov_words.list` | 25 | 確認過 | 關鍵詞不在 S0 詞庫快照裡、但每個字都讀得出 |
| `dev/user-reported.txt` | 52 | 使用者實際打的注音（2026-10-09 起的列由 `tools/readings.py` 產生，研究紀錄逐批註明） | 使用者遇到的錯字（2026-10-03 同意以 CC0 釋出）；之後回報的句子直接加在這裡 |
| `dev/user-typing.txt` | 76 | 確認過（「那麼多」「覺得」「寫得」「找得到」改成輕聲，「欸」改成 ㄟˋ） | 打字測驗：十八段專案自寫的文章（CC0；第一輪六段書面、第二輪十二段口語／流行語／AI 用語），在標點處切成子句，前文是同一段裡前面的子句。2026-10-03 使用者用蘋果內建、小麥、自然三套輸入法實打，結果見 `docs/typing-test.md`。檔名排在 `user-reported` 之後，不影響「開發集前 302 列」 |
| `holdout/holdout.txt`、`holdout/oov_words.list` | 227（最後 25 列是 OOV） | 寫作者確認 | **保留集**：只有片結束的 fresh verifier 會跑；同音組取偶數行 |
| `learn/cases.tsv` | 14 組 × 5 列 | 確認過 | 學習模擬（teach／common／rare＋same_ctx） |

## 規則

- 讀音由 `tools/readings.py` 產生，人工確認的詞記在 `tools/reading_overrides.tsv`；評測時有第三欄就直接用、不重算。
- 保留集的報法（2026-10-08）：fresh verifier 對基準與新版各跑一次 `shanjie-eval --lm data/lm/bigram.sjlm --profile chat|formal --set holdout --rowstats FILE`（寫到它自己的暫存目錄；檔內只有數字、tab、換行，已檢查 `[0-9\t\n]`），再用 `tools/evalstats.py compare` 只回報那一行表格（n、top1、改對／改壞、McNemar p、CER、Δ 區間），然後刪掉兩個檔。`--set holdout --dump` 仍然禁止。其他集合的報法見 `CLAUDE.md`「評測與資料」。
- 保留集：由不參與實作的 fresh agent 在獨立 worktree 撰寫；main 沒有讀過內容。所有 executor 的 brief 都禁止讀 `holdout/`；`shanjie-eval` 對保留集只印指標、不印錯句。這是流程規則，不是技術防護。
- 保留集合併後做過兩次盲處理（只印數量）：一／不 變調改本調（0 列需要改）；刪掉與開發集重複的 2 句（229 → 227）。
- 開發集與保留集零重疊（程式檢查）。

## 保留集雜湊（變動就代表被改過）

| 檔案 | SHA-256 |
|---|---|
| `holdout/holdout.txt` | `2445f7ad0347cc68e2c7a62a1cbb25870247a43aec6d11a20d981bd439f5ad0b` |
| `holdout/oov_words.list` | `211771e80a1b17529c3791eef77ab639d305554770730b3d43de9623e4fe3f45` |

## 基準（unigram，2026-10-03）

| 集合 | 列數 | 整句 | 寬鬆 | oracle@32 |
|---|---|---|---|---|
| 開發集 | 302 | 51.7% | 52.6% | 96.7% |
| 保留集 | 227 | 41.4% | 44.5% | 95.6% |
