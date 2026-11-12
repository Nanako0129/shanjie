# 評測資料

| 路徑 | 列數 | 讀音 | 用途 |
|---|---|---|---|
| `sets/trap.txt`、`sets/daily.txt`、`sets/moedict.txt` | 64、45、300 | 自動產生（`to_syllables`） | S0 golden 專用，不再修改 |
| `dev/existing.txt` | 109 | 確認過 | 同 trap＋daily，讀音改成使用者實際會打的 |
| `dev/homophones.txt` | 165 | 確認過 | 同音陷阱與單字混淆（同音組取奇數行） |
| `dev/oov.txt`、`dev/oov_words.list` | 25 | 確認過 | 關鍵詞不在 S0 詞庫快照裡、但每個字都讀得出 |
| `dev/user-reported.txt` | 187 | 使用者實際打的注音（2026-10-09 起的列由 `tools/readings.py` 產生；第 3、81、89、119、120、151、156、159、163、166、168、170、173、175、179、180、181、182、187 列的讀音是手動定的（第 187 列「偽」照實際的打法用 ㄨㄟˇ，詞庫與工具給 ㄨㄟˋ；第 175 列「幀」照實際的打法用 ㄓㄣ，詞庫與工具給 ㄓㄥˋ；第 166、168、173 列「長這樣」、第 170 列「長高」的「長」照實際的打法用 ㄓㄤˇ，工具給 ㄔㄤˊ；第 3 列「一個」與第 163 列「變一樣了」照實際的打法用變調 ㄧˊ；第 156 列「紮」照實際的打法用 ㄓㄚ，萌典是 ㄗㄚ），重產時不要用工具覆蓋） | 使用者遇到的錯字（CC0）；之後回報的句子直接加在這裡 |
| `dev/variant-order.txt` | 35 | 讀音照 `data/lexicon/variant-order.tsv`（詞庫的寫法） | 異體寫法排序（`docs/spec/decoder.md` §2.3）的檢查集：35 組的常用寫法各一列、沒有前文，聊天與書面標準檔各量一次，報改前改後的第一名正確數，改壞要是 0；公開、CC0。在 `dev/` 的最後面（檔名排在 `user-reported`、`user-typing` 之後），開發集前 302 列不變 |
| `dev/user-typing.txt` | 76 | 確認過（「那麼多」「覺得」「寫得」「找得到」改成輕聲，「欸」改成 ㄟˋ） | 打字測驗：十八段專案自寫的文章（CC0；第一輪六段書面、第二輪十二段口語／流行語／AI 用語），在標點處切成子句，前文是同一段裡前面的子句。2026-10-03 用蘋果內建、小麥、自然三套輸入法實打，結果在 `eval/bench/static/typing-test.json`。檔名排在 `user-reported` 之後，不影響「開發集前 302 列」 |
| `dev/acg/works.txt`、`dev/acg/characters.txt`、`dev/acg/character-parts.txt` | 142、227、105 | `tools/readings.py` 產生，標 CHECK 的列（依序 7、12、11 列）沒有逐列確認，無法讀出的 1 列沒收 | ACG 詞包（`docs/spec/lexicon-and-packs.md` §5.7）的評測：Wikidata 上有中文維基條目、sitelink 至少 3、有 zh-tw 標籤的作品名、角色名與角色名片段（只留 2–10 個漢字、整串一次打、沒有前文）。**來源是 Wikidata 的標籤，CC0**（Wikidata 的資料授權）。在 `dev/acg/` 子目錄，所以 `--dev N` 不會讀到；只用來回報，**不用來調任何參數** |
| `dev/it/words.txt` | 60 | 讀音照建置用的讀音（`tools/readings.py`＋`data/packs/it-readings.tsv`），標 CHECK 的詞不抽 | 資訊＋AI 詞包（`docs/spec/lexicon-and-packs.md` §6.4）的檢查集：從 `data/packs/it-add.tsv` 依來源比例抽 60 個詞（固定種子 20261024），一列一詞、沒有前文。授權同 `it-add.tsv`（CC BY-SA 4.0）。只用來回報，**不用來調任何參數**；`--dev N` 不會讀到 |
| `dev/meme/words.txt` | 60 | 讀音照建置用的讀音（`tools/readings.py`＋`data/packs/meme-readings.tsv`，沒有該檔就沒有覆寫） | 迷因與實況用語詞包（`docs/spec/lexicon-and-packs.md` §9.4）的檢查集：就是建置輸出的 `words.txt`（固定種子 20261025，不到 60 個就全收），一列一詞、沒有前文。CC BY-SA 4.0，同 `meme-add.tsv`。只用來回報，**不用來調任何參數**；`--dev N` 不會讀到 |
| `dev/meme/words-all.txt` | 138 | 同 `words.txt` | 迷因詞包（`docs/spec/lexicon-and-packs.md` §9.4）全部的詞（讀音標 `CHECK` 的除外，131 列），之後接 `meme-extra-readings.tsv` 的另外的讀音各一列（7 列），建置輸出的同名檔，一列一個詞與讀音、沒有前文；開著詞包時第一名要對，不開時對的列開了不能變錯；也是預測列測試的輸入。CC BY-SA 4.0，同 `meme-add.tsv`。只用來回報，**不用來調任何參數**；`--dev N` 不會讀到 |
| `dev/meme/embedded.txt` | 828 | 同 `words-all.txt`（讀音寫在 `tools/gen_meme_embedded.py`） | 把 `words-all.txt` 的每個詞接在三個固定開頭（他說、真的是、我覺得）之後、兩個固定結尾（了、吧）之前的句子（138 × 6）：確認詞包詞放進句子裡不會變錯。工具造的句子，只確認方向，不當出貨標準的證據。CC BY-SA 4.0。`--dev N` 不會讀到 |
| `dev/beauty/words.txt` | 60 | 讀音照建置用的讀音（`tools/readings.py`＋`data/packs/beauty-readings.tsv`） | 美妝與保養詞包（`docs/spec/lexicon-and-packs.md` §8.4）的檢查集：從 `data/packs/beauty-add.tsv` 抽 60 個詞（固定種子 20261108），一列一詞、沒有前文。CC0，同 `beauty-add.tsv`。只用來回報，**不用來調任何參數**；`--dev N` 不會讀到 |
| `dev/net-slang/words.txt` | 每個讀音一列 | 讀音同 `data/lexicon/slang-add.tsv` | 網路用語（`docs/spec/lexicon-and-packs.md` §2.10）的檢查集：每個讀音上排第一的收錄詞一列（同讀音排後面的寫法只當候選、不收），加 4 個已經打得對的對照（幹你娘、靠北、三小、北七）；一列一詞、沒有前文。授權同 `slang-add.tsv`（CC BY-SA 4.0）。只用來回報，**不用來調任何參數**；放在子資料夾，`--dev N` 不會讀到 |
| `dev/net-slang/round2.txt` | 906 | 讀音同 `data/lexicon/slang-add.tsv` | 網路用語第二輪（`docs/spec/lexicon-and-packs.md` §2.10）的檢查集：每個讀音上排第一的第二輪新詞一列，沒有前文。授權同 `slang-add.tsv`（CC BY-SA 4.0）。只用來回報，**不用來調任何參數**；門檻不算會意重排 |
| `rerank/s7e-function-words.txt` | 186 | `tools/readings.py` 產生；標 CHECK 的 10 列與工具沒標的 2 列逐列人工確認 | 功能字測試句（專案自寫，CC0）：在／再、是／事／試／式／市、的／得／地、做／作 等同音功能字，第二批 64 列專挑功能字和後面的字能組成常見詞的搭配（再這樣、是出）。S7e 用來檢查語意重排有沒有把功能字改壞，不用來調參；第 80、85 列的「地」讀音是 ㄉㄧˋ（不是副詞的 ㄉㄜ˙），測不到的／得／地的混淆，之後在最後面補改正的列。不放在 `probe/`：CLI 的 `--set probe` 會讀那個資料夾的每個檔案（S2r 探針集的 golden）；句子凍結後不改，要加就加在最後面 |
| `holdout/talk.txt` | 150 | `tools/readings.py` 產生（CHECK 列由抽樣的 agent 決定，定不了的丟掉）；抽查 40 列錯 1 列（2.5%） | **保留集（現役，2026-11-11 起）**：第二份（shard-2，`docs/spec/evaluation.md` §5），原本放在 `next/shard-2/`。公開語料的口語書寫，逐列出處在 `holdout/next/sources.tsv`（第 2 份）。**新聞的列已退役**（2026-10-29）：`holdout/news.txt` 與 `next/shard-{2,3}/news.txt` 已刪，`sources.tsv` 裡新聞那幾列留著當退役紀錄；新聞半邊改用私人檔（見下）。只在推 `v*` tag 前由 fresh verifier 跑（2026-10-18 起；之前是每片結束時） |
| `dev/holdout-2026-10-03/holdout.txt`、`oov_words.list` | 227（最後 25 列是 OOV） | 寫作者確認 | 2026-10-03 到 10-16 的保留集，回填之後退役，改當**可讀的挑戰集**（同音組取偶數行）；在 `dev/` 的子資料夾，`--dev N` 不會讀到，開發集前 302 列不變 |
| `holdout/next/shard-3/talk.txt` | 150 | `tools/readings.py` 產生（CHECK 列由抽樣的 agent 決定，定不了的丟掉）；抽查 40 列錯 1 列（2.5%） | **之後的保留集**（`docs/spec/evaluation.md` §5）：和 shard-1、shard-2 同一次抽出、三份互斥（固定種子 20261015）；逐列出處在 `holdout/next/sources.tsv`。`--set holdout` 不讀子資料夾，換上之前不會被量到 |
| `holdout/retired/shard-1/talk.txt` | 150 | 同上 | **退役的口語半邊**（shard-1，2026-10-16 到 2026-11-11 現役，用滿 8 次）：留在 `holdout/` 底下，照樣不讀、不調參；換份前後的數字不跨份比 |
| `english/probe-common.txt`、`english/probe-ai.txt` | 208、56 | 不適用（英文字，一行一個，小寫） | 中英混打正式寫法表（`data/english-extra.txt`，`docs/spec/english-mixed.md` §4.4）的涵蓋清單：常見的技術、品牌與 App 名稱，以及 AI 廠商與產品名，專案自寫（CC0）。只用來數「幾個字在表裡」，不是評測列 |
| `dev/mixed-words/words.txt` | 29 | 讀音同 `data/lexicon/mixed-words.tsv` | 中英混合詞（`docs/spec/english-mixed.md` §5.7）的檢查集，`英文\|讀音\|寫法`，和詞表逐列相同；`core/tests/engine_mixed.rs` 逐列打（英文第一個字母按 Shift），要在 ↓ 清單裡。專案自寫（CC0） |
| `learn/cases.tsv` | 14 組 × 5 列 | 確認過 | 學習模擬（teach／common／rare＋same_ctx） |
| `ablation/v0.7.0/` | — | — | v0.7.0 標準檔的逐項拿掉表（三個集合的結果、每一格的 CLI 指令）與兩個閘門列的離線重算 `gate_offline.py`；不是評測列，只有結果與重跑方法 |

## 規則

- 讀音由 `tools/readings.py` 產生，人工確認的詞記在 `tools/reading_overrides.tsv`；評測時有第三欄就直接用、不重算。
- 保留集的報法（2026-10-08）：fresh verifier 對基準與新版各跑一次 `shanjie-eval --lm data/lm/bigram.sjlm --profile chat --set holdout --rowstats FILE`（2026-10-29 起 `--set holdout` 只讀口語半邊，只用聊天設定；新聞半邊另見下一條）（寫到它自己的暫存目錄；檔內只有數字、tab、換行，已檢查 `[0-9\t\n]`），再用 `tools/evalstats.py compare` 只回報那一行表格（n、top1、改對／改壞、McNemar p、CER、Δ 區間），然後刪掉兩個檔。`--set holdout --dump` 仍然禁止。新聞半邊：`shanjie-eval --lm … --profile formal --rows <私人檔> --rowstats FILE`，報法同上，沒有別的指令。其他集合的報法見 `docs/spec/evaluation.md` §2.6。
- 保留集的兩個半邊（`docs/spec/evaluation.md` §5.1；2026-10-29 換手）：`--set holdout` 從此只讀 `holdout/talk.txt`（n 150），就是**口語半邊**，用聊天設定量，存進 v2 JSON 的 `holdout.chat` 與 `eval/bench/kpi/<版本>.json` 的 `holdout_talk_chat`。**新聞半邊**不在 repo：fresh verifier 在評測機器上對私人檔 `holdout-news-1.txt`（雜湊見下）用 `--rows <檔> --profile formal --rowstats` 量基準與新版（檔用前核對雜湊，量完刪掉 rowstats 與那台機器上的副本），再 `evalstats compare --net`，存進 v2 JSON 的 `holdout.formal` 與 kpi JSON 的 `holdout_news_formal`（`n`、`top1`、`fixed`、`broken`、`p`，另加那個檔的 `rows_sha256`，兩個表都依它分欄）。基準與新版的口語半邊 rowstats 不必再切段。
- **量新聞半邊的旗標禁令（流程規則，不是技術防護）**：`--rows` 不像 `--set holdout` 會拒絕逐列輸出，所以量新聞半邊的 verifier，brief 明寫禁止 `--dump`、`--dump-words`、`--picks`、`--tinylm-scores`，只准 `--rowstats`（檔內只有 `[0-9\t\n]`），照 `docs/spec/evaluation.md` §5.3。兩個半邊各自計次：同一次發版前兩個都量，各加一次「使用」（見下面的使用紀錄）。`rows_sha256`：口語半邊是 `holdout/talk.txt` 的 sha256、新聞半邊是那個私人檔的 sha256，一定要填。
- `tools/evalstats.py compare … --net`（E2，`docs/spec/evaluation.md` §2.4）：表格最後多三欄，淨值（改對 − 改壞）、改壞率（改壞 ÷ n）、改壞數的 95% 精確 Poisson 單尾上界；不加 `--net` 時輸出不變。
- 保留集（現役，2026-10-16 起）：從公開語料由 fresh agent 抽樣、另一個 fresh agent 盲後處理，實作者只看列號、計數與雜湊。舊集（`dev/holdout-2026-10-03/`）由不參與實作的 fresh agent 在獨立 worktree 撰寫；實作者沒有讀過內容。所有 executor 的 brief 都禁止讀 `holdout/`；`shanjie-eval` 對保留集只印指標、不印錯句。這是流程規則，不是技術防護。
- 保留集合併後做過兩次盲處理（只印數量）：一／不 變調改本調（0 列需要改）；刪掉與開發集重複的 2 句（229 → 227）。
- 開發集與保留集零重疊（程式檢查）。

## 合法寫法表雜湊

| 檔案 | SHA-256 |
|---|---|
| `forms.tsv`（`forms-v1`，`tools/build_forms.py --check` 重建並比對；只在 `--forms-rowstats` 時讀，`docs/spec/evaluation.md` §3） | `85f4e034817d14b3263223b19a0fadbb32fa734d3e5ded7d8ad3aee945a03130` |

## 保留集雜湊（變動就代表被改過）

| 檔案 | SHA-256 |
|---|---|
| `holdout/talk.txt`（shard-2，現役） | `71f8a5e1a422c00a37598d237744ff49a229ce04dc3f37a1170ea98fe8b6e67e` |
| `dev/holdout-2026-10-03/holdout.txt`（退役） | `2445f7ad0347cc68e2c7a62a1cbb25870247a43aec6d11a20d981bd439f5ad0b` |
| `dev/holdout-2026-10-03/oov_words.list`（退役） | `211771e80a1b17529c3791eef77ab639d305554770730b3d43de9623e4fe3f45` |
| `holdout/retired/shard-1/talk.txt`（退役） | `667e3e31534a846f02ffc8d2ae19d18224e0a0cd60978e5ba443d1ee2ae8781d` |
| `holdout/next/shard-3/talk.txt` | `743d3687f74e43c9a0d5ea40f7c6b463cb360b0e60bd7bd5b11265f291d984d3` |
| `holdout/next/sources.tsv` | `f58939d776d6c5132004878a3ed2a700fc9d4f8f044b175f6d704448d599eb5d` |

### 私人新聞集（2026-10-28／29 建，檔在 repo 外，只記雜湊與列數）

來源是「報社新聞（只在本機驗證，不訓練、不散布）」加中文維基新聞驗證那一邊的條目。保留集每份是 30 列維基新聞＋120 列報社新聞；`newstune` 是 72＋1,928。

| 檔案 | 列數 | 用途 | SHA-256 |
|---|---|---|---|
| `newstune.txt` | 2,000 | 開發層，書面判讀集之一（可調參、選臂，只報數字） | `c69f8f0b5a4fefb9f07393964b1ecbac291ff0e2d9b22f567f168e5bf1c77d0a` |
| `holdout-news-1.txt` | 150 | 保留集新聞半邊（現役） | `637c9bf96b27b83d498e1b3550ce93fc08e8f0ea061dcaefb9c5b2d434b05a38` |
| `holdout-news-2.txt` | 150 | 保留集新聞半邊（之後） | `d0c0b82ea936349128117d69b2d89c54ffa527279d94787ee6d2e5ee1bf194d5` |
| `holdout-news-3.txt` | 150 | 保留集新聞半邊（之後） | `344432589b80d168e297b6009e522b954b3e9b54615f023c9d128c065086a203` |

使用次數：三份保留集新聞半邊都從 0 開始（口語半邊接著舊的累計）；換份與換新規則同下。行政院 294 句新聞稿驗證集已退役，從 2026-10-29 起是訓練語料。
## 保留集的換新規則與使用紀錄（`docs/spec/evaluation.md` §5.2）

- **一次「使用」**：現役保留集的任何一個數字被讀取或回報。回填凍結的 tag、量份與份之間的雜訊底線不算。
- **換下一份**（符合任一個就換）：使用滿 8 次；或任何改動是因它的數字而做（觸發停止條件，或看過數字之後改門檻、改值）。
- 三份都用完後，把時間窗往後延，用同樣的規則重抽。
- 新的份是怎麼來的：抽樣與過濾的 agent 和量測的 agent 都是 fresh agent，實作者只看列號、計數與雜湊；和訓練文字、所有公開評測集、舊保留集與行政院 294 句的包含比對丟掉 508 句（`docs/spec/evaluation.md` §5.4）。

| 日期 | 現役 | 半邊 | 片 | 第幾次 |
|---|---|---|---|---|
| （shard-1 從 2026-10-16 起現役；之後每次使用加一列） | | | | |
| 2026-10-17 | shard-1 | 兩個半邊（舊集合） | v0.4.7 發版（v0.4.6 對 v0.4.7，fresh verifier；聊天 234 → 234、書面 230 → 233） | 1 |
| 2026-10-17 | shard-1 | 兩個半邊（舊集合） | rerank-formal（會意 SJ-SR-6 書面三檔，fresh verifier；對 tinylm-v5：標準與積極 +8／−0、保守 0／0；對只用 n-gram：標準與積極 +12／−0、保守 +3／−0） | 2 |
| 2026-10-17 | shard-1 | 兩個半邊（舊集合） | newword-b（新詞 B，fresh verifier；對 main：聊天 +5／−0、書面 +5／−0） | 3 |
| 2026-10-22 | shard-1 | 兩個半邊（舊集合） | v0.5.0 發版（v0.4.7 對 v0.5.0，fresh verifier；聊天 234 → 237（7／4）、書面 233 → 241（8／0）；口語半邊聊天 116 → 115（3／4）、新聞半邊書面 116 → 120（4／0）） | 4 |
| 2026-10-29 | shard-1 | 口語半邊（`talk.txt`，聊天） | v0.6.0 發版（v0.5.0 對 v0.6.0，fresh verifier；115 → 124（10／1，p 0.012）） | 5 |
| 2026-10-29 | `holdout-news-1` | 新聞半邊（書面） | v0.6.0 發版（v0.5.0 對 v0.6.0，fresh verifier；125 → 128（3／0，p 0.25）） | 1 |
| 2026-10-31 | shard-1 | 口語半邊（`talk.txt`，聊天） | v0.6.1 發版（v0.6.0 對 v0.6.1，fresh verifier；124 → 124（0／0）） | 6 |
| 2026-10-31 | `holdout-news-1` | 新聞半邊（書面） | v0.6.1 發版（v0.6.0 對 v0.6.1，fresh verifier；128 → 128（0／0）） | 2 |
| 2026-11-10 | shard-1 | 口語半邊（`talk.txt`，聊天） | v0.7.0 發版（v0.6.1 對 v0.7.0，fresh verifier；124 → 127（3／0，p 0.25）） | 7 |
| 2026-11-10 | `holdout-news-1` | 新聞半邊（書面） | v0.7.0 發版（v0.6.1 對 v0.7.0，fresh verifier；128 → 126（1／3，p 0.625）） | 3 |
| 2026-11-11 | shard-1 | 口語半邊（`talk.txt`，聊天） | v0.7.0 發版重量（加入重排保護清單之後的樹，v0.6.1 對 v0.7.0，fresh verifier；124 → 127（3／0，p 0.25）） | 8 |
| 2026-11-11 | `holdout-news-1` | 新聞半邊（書面） | v0.7.0 發版重量（同上；128 → 126（1／3，p 0.625）） | 4 |
| 2026-11-11 | shard-2 | 口語半邊 | 換份：shard-1 用滿 8 次，`next/shard-2/talk.txt` 移到 `holdout/talk.txt`，shard-1 移到 `holdout/retired/shard-1/` | 0 |

2026-10-29 換手：兩個半邊從此各算各的使用次數，上表原有的第 1–4 次是舊新聞半邊加口語半邊合併的 shard-1 用量，列保持原樣。**口語半邊**（shard-1 的 `talk.txt`）接著上表的 4 次往下數，滿 8 次或有決定因它而做就換成 `next/shard-2/talk.txt`；**新聞半邊**（`holdout-news-1.txt`）從 0 起算，同一條規則，滿了換成 `holdout-news-2.txt`。每個半邊各自適用上面的「換下一份」規則，不互相牽動。換份前後的數字不跨份比。之後每次使用在上表為兩個半邊各記一列（「半邊」欄寫口語或新聞）。2026-11-11 口語半邊用滿 8 次，換成 shard-2，從 0 起算；新聞半邊照舊是 `holdout-news-1.txt`（4 次）。shard-2 換上前有一列內容進過一個審查 agent 的 session（見下一段，沒有數字回來、不計使用），照規則照常換上。

不計使用的讀取：2026-11-09 一支審查用的比對腳本讀入 `holdout/next/sources.tsv`（三份口語半邊的逐列出處，含現役 shard-1）做字串比對；內容沒有印出，沒有任何保留集的數字回來，不計使用，各份照舊。同日另有一道審查用的 `git grep -l` 掃過 `eval/`（含 `holdout/`），只拿到命中檔數 0，沒有檔名或內容回來，不計使用。同日又有一次審查時的 `git grep` 沒排除 `eval/holdout/`，`next/shard-2/talk.txt` 的一列內容進了審查 agent 的 session；沒有數字回來，不計使用、不換份。

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

### 調參集 TW0（2026-11-05 建，檔在 repo 外，只記雜湊與列數）

正解的繁體句不套 OpenCC `TWPhrases`（`docs/spec/evaluation.md` §6.1），對 SR-TW 的七個訓練來源去重。SR-TW-V 起，出貨標準第 3 條用 cvtune-clean-tw0、書面調參用 wikitune-clean-tw0；舊的 cvtune-clean（3,286 列 `d0960ebe…`）、wikitune-clean（2,181 列 `d576ea94…`）留著對照過去的數字，新舊兩版的列不一一對應，數字不跨版比。

| 集合 | 列數 | SHA-256 | 來源 |
|---|---|---|---|
| cvtune-clean-tw0 | 2,992 | `91e7097d36b3812d58c8672501153d51116bef8f57a48c9350876523a7fbbe04` | Common Voice zh-TW＋Tatoeba cmn 的 tune 20%（`build_tune.py --part cv --cv-no-trad-tw-phrases`） |
| wikitune-clean-tw0 | 2,182 | `ccddaa4f0ace8f17d69290a2b0f06dcd0fbcfeb54d6bd21242385dde5bf2cbe2` | 維基第 300,000～304,999 篇（`--part wiki --cv-no-trad-tw-phrases --tune-no-trad-tw-phrases`：維基的抽樣接在 cv 半的亂數狀態後面，兩個旗標都要帶） |
