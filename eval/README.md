# 評測資料

| 路徑 | 列數 | 讀音 | 用途 |
|---|---|---|---|
| `sets/trap.txt`、`sets/daily.txt`、`sets/moedict.txt` | 64、45、300 | 自動產生（`to_syllables`） | S0 golden 專用，不再修改 |
| `dev/existing.txt` | 109 | 確認過 | 同 trap＋daily，讀音改成使用者實際會打的 |
| `dev/homophones.txt` | 165 | 確認過 | 同音陷阱與單字混淆（同音組取奇數行） |
| `dev/oov.txt`、`dev/oov_words.list` | 25 | 確認過 | 關鍵詞不在 S0 詞庫快照裡、但每個字都讀得出 |
| `dev/user-reported.txt` | 127 | 使用者實際打的注音（2026-10-09 起的列由 `tools/readings.py` 產生，研究紀錄逐批註明；第 81、89、119、120 列的讀音是手動定的，重產時不要用工具覆蓋） | 使用者遇到的錯字（2026-10-03 同意以 CC0 釋出）；之後回報的句子直接加在這裡 |
| `dev/user-typing.txt` | 76 | 確認過（「那麼多」「覺得」「寫得」「找得到」改成輕聲，「欸」改成 ㄟˋ） | 打字測驗：十八段專案自寫的文章（CC0；第一輪六段書面、第二輪十二段口語／流行語／AI 用語），在標點處切成子句，前文是同一段裡前面的子句。2026-10-03 使用者用蘋果內建、小麥、自然三套輸入法實打，結果見 `docs/typing-test.md`。檔名排在 `user-reported` 之後，不影響「開發集前 302 列」 |
| `dev/acg/works.txt`、`dev/acg/characters.txt`、`dev/acg/character-parts.txt` | 142、227、105 | `tools/readings.py` 產生，標 CHECK 的列（依序 7、12、11 列）沒有逐列確認，無法讀出的 1 列沒收 | ACG 詞包（`docs/contracts/acg-pack.md` A.3）的評測：Wikidata 上有中文維基條目、sitelink 至少 3、有 zh-tw 標籤的作品名、角色名與角色名片段（只留 2–10 個漢字、整串一次打、沒有前文）。**來源是 Wikidata 的標籤，CC0**（Wikidata 的資料授權）。在 `dev/acg/` 子目錄，所以 `--dev N` 不會讀到；只用來回報，**不用來調任何參數** |
| `rerank/s7e-function-words.txt` | 186 | `tools/readings.py` 產生；標 CHECK 的 10 列與工具沒標的 2 列由 main 逐列確認（`docs/contracts/s7e-fewer-breaks.md` §2.1） | 功能字測試句（專案自寫，CC0）：在／再、是／事／試／式／市、的／得／地、做／作 等同音功能字，第二批 64 列專挑功能字和後面的字能組成常見詞的搭配（再這樣、是出）。S7e 用來檢查語意重排有沒有把功能字改壞，不用來調參；第 80、85 列的「地」讀音是 ㄉㄧˋ（不是副詞的 ㄉㄜ˙），測不到的／得／地的混淆，之後在最後面補改正的列。不放在 `probe/`：CLI 的 `--set probe` 會讀那個資料夾的每個檔案（S2r 探針集的 golden）；句子凍結後不改，要加就加在最後面 |
| `holdout/news.txt`、`holdout/talk.txt` | 150、150 | `tools/readings.py` 產生（CHECK 列由抽樣的 agent 決定，定不了的丟掉）；抽查 40 列錯 1 列（2.5%） | **保留集（現役，2026-10-16 起）**：換新的第一份（shard-1，`docs/contracts/holdout-refresh.md`）。公開語料的新聞與口語書寫，逐列出處在 `holdout/next/sources.tsv`（第 1 份）。只有片結束的 fresh verifier 會跑 |
| `dev/holdout-2026-10-03/holdout.txt`、`oov_words.list` | 227（最後 25 列是 OOV） | 寫作者確認 | 2026-10-03 到 10-16 的保留集，回填之後退役，改當**可讀的挑戰集**（同音組取偶數行）；在 `dev/` 的子資料夾，`--dev N` 不會讀到，開發集前 302 列不變 |
| `holdout/next/shard-{2,3}/news.txt`、`talk.txt` | 每份新聞 150、口語 150 | `tools/readings.py` 產生（CHECK 列由抽樣的 agent 決定，定不了的丟掉）；抽查 40 列錯 1 列（2.5%） | **之後的保留集**（`docs/contracts/holdout-refresh.md`）：和現役的 shard-1 同一次抽出、三份互斥（固定種子 20261015）；逐列出處在 `holdout/next/sources.tsv`。`--set holdout` 不讀子資料夾，換上之前不會被量到 |
| `learn/cases.tsv` | 14 組 × 5 列 | 確認過 | 學習模擬（teach／common／rare＋same_ctx） |

## 規則

- 讀音由 `tools/readings.py` 產生，人工確認的詞記在 `tools/reading_overrides.tsv`；評測時有第三欄就直接用、不重算。
- 保留集的報法（2026-10-08）：fresh verifier 對基準與新版各跑一次 `shanjie-eval --lm data/lm/bigram.sjlm --profile chat|formal --set holdout --rowstats FILE`（寫到它自己的暫存目錄；檔內只有數字、tab、換行，已檢查 `[0-9\t\n]`），再用 `tools/evalstats.py compare` 只回報那一行表格（n、top1、改對／改壞、McNemar p、CER、Δ 區間），然後刪掉兩個檔。`--set holdout --dump` 仍然禁止。其他集合的報法見 `CLAUDE.md`「評測與資料」。
- `tools/evalstats.py compare … --net`（E2，`docs/contracts/e2-chat-eval.md`）：表格最後多三欄，淨值（改對 − 改壞）、改壞率（改壞 ÷ n）、改壞數的 95% 精確 Poisson 單尾上界；不加 `--net` 時輸出不變。
- 保留集（現役，2026-10-16 起）：從公開語料由 fresh agent 抽樣、另一個 fresh agent 盲後處理，main 只看列號、計數與雜湊（`docs/contracts/holdout-refresh.md` §4）。舊集（`dev/holdout-2026-10-03/`）由不參與實作的 fresh agent 在獨立 worktree 撰寫；main 沒有讀過內容。所有 executor 的 brief 都禁止讀 `holdout/`；`shanjie-eval` 對保留集只印指標、不印錯句。這是流程規則，不是技術防護。
- 保留集合併後做過兩次盲處理（只印數量）：一／不 變調改本調（0 列需要改）；刪掉與開發集重複的 2 句（229 → 227）。
- 開發集與保留集零重疊（程式檢查）。

## 保留集雜湊（變動就代表被改過）

| 檔案 | SHA-256 |
|---|---|
| `holdout/news.txt`（shard-1，現役） | `b9bc7190e81bb546baf390ed442cf953ef65fdcfe0922f62e8fbe274b423cbf4` |
| `holdout/talk.txt`（shard-1，現役） | `667e3e31534a846f02ffc8d2ae19d18224e0a0cd60978e5ba443d1ee2ae8781d` |
| `dev/holdout-2026-10-03/holdout.txt`（退役） | `2445f7ad0347cc68e2c7a62a1cbb25870247a43aec6d11a20d981bd439f5ad0b` |
| `dev/holdout-2026-10-03/oov_words.list`（退役） | `211771e80a1b17529c3791eef77ab639d305554770730b3d43de9623e4fe3f45` |
| `holdout/next/shard-2/news.txt` | `1c9c928c307e54d29768450f1f936f16ea426c8758cedf2fc430bf1812d8eca4` |
| `holdout/next/shard-2/talk.txt` | `71f8a5e1a422c00a37598d237744ff49a229ce04dc3f37a1170ea98fe8b6e67e` |
| `holdout/next/shard-3/news.txt` | `fc1477681a6f97aaef83b4df388de3f0bc066eb3ef3b936677fcd94ade14e1b4` |
| `holdout/next/shard-3/talk.txt` | `743d3687f74e43c9a0d5ea40f7c6b463cb360b0e60bd7bd5b11265f291d984d3` |
| `holdout/next/sources.tsv` | `f58939d776d6c5132004878a3ed2a700fc9d4f8f044b175f6d704448d599eb5d` |

## 保留集的換新規則與使用紀錄（`docs/contracts/holdout-refresh.md` §7）

- **一次「使用」**：現役保留集的任何一個數字回到 main 或維護者手上。回填凍結的 tag、量份與份之間的雜訊底線不算。
- **換下一份**（符合任一個就換）：使用滿 8 次；或任何決定是因它而做（觸發停止條件，或看過數字之後改門檻、改值）。
- 三份都用完後，把時間窗往後延，用同樣的規則重抽。
- 新的份是怎麼來的：抽樣與過濾的 agent 和量測的 agent 都是 fresh agent，main 只看列號、計數與雜湊（契約 §4）；和訓練文字、所有公開評測集、舊保留集與行政院 294 句的包含比對丟掉 508 句（契約 §8）。

| 日期 | 現役 | 片 | PR | 第幾次 |
|---|---|---|---|---|
| （shard-1 從 2026-10-16 起現役；之後每次使用加一列） | | | | |

### shard-1 回填（2026-10-16，不計入使用次數）

fresh verifier 用 `tools/bench.py` `VERSIONS2` 裡各版的出貨旗標與模型（v0.4.6 用 `7c47266`，和 tag `58da6d2` 及當時的 main `2dbec4f` 在引擎與資料路徑上沒有差異），`--rows`（news 接 talk）量，n 都是 300。

| 版本 | 聊天 top1 | 書面 top1 | 和上一版（聊天，改對／改壞，p） | 和上一版（書面） |
|---|---|---|---|---|
| v0.4.0 | 223 | 225 | | |
| v0.4.1 | 235 | 227 | 12／0，0.0005 | 2／0，0.50 |
| v0.4.2 | 235 | 227 | 0／0 | 0／0 |
| v0.4.3 | 234 | 229 | 0／1，1 | 3／1，0.63 |
| v0.4.4 | 233 | 227 | 1／2，1 | 3／5，0.73 |
| v0.4.5 | 233 | 227 | 0／0 | 0／0 |
| v0.4.6 | 234 | 230 | 3／2，1 | 6／3，0.51 |

雜訊底線（v0.4.6 在三份上的 top1，不同的句子，只比總數）：聊天 shard-1／2／3 為 234／247／236（差 −13、−2、+11），書面 230／239／229（差 −9、+1、+10）。份與份之間的差可到 13 句（約 4%），換份前後的數字不能直接比。

## 基準（unigram，2026-10-03）

| 集合 | 列數 | 整句 | 寬鬆 | oracle@32 |
|---|---|---|---|---|
| 開發集 | 302 | 51.7% | 52.6% | 96.7% |
| 保留集 | 227 | 41.4% | 44.5% | 95.6% |
