# 語意機率 S7a：小型字元語言模型重排（離線）

PLAN「S7：自訓小模型」底下的第一片（編號 S7a；PLAN 的 S6 是雲端，不是這片）。

使用者 2026-10-10：「繼續解決……語意機率」。PLAN「判斷器與語言模型的貝氏融合」暫停的原因是有效的判斷器是 8B 或雲端，放不進輸入法。這一片回答：**一個專為善解訓練、放得進輸入法的小型字元語言模型，當判斷器重排 n-gram 的前幾名，能不能拿回融合的大部分效果？** 只做離線量測；做進核心是下一片（看這片的結果）。設計先請 Fable 5.1 分析過（研究紀錄同日）。

## 0. 依據（都是量過的）

- 融合（`docs/contracts/fusion-offline.md`，discordtune B 半 500 列，固定 8 個候選）：n-gram 第一名 84.4%；Qwen3-8B 逐候選對數機率加前文的加權融合 89.2%（對第一名修好 33、弄壞 9，p = 0.0003）；分差門檻 τ 87.8%。
- Qwen3-1.7B 4-bit（S5k）：設門檻後 B 半 87.2%（p = 0.081）；8 個候選每列 p50 245–360 ms、p95 510–640 ms，太慢也太大。
- 小模型的理由（推論，Fable 5.1）：8B 的效果大部分只要「中等的語言模型＋門檻」就拿得到（1.7B 已拿回約 2.8 個百分點）；卡住的是大小與延遲，不是品質。專為這個任務（zh-tw、同音候選、組字區內的前文）訓練的小模型可能就夠。

## 1. 要做到什麼、怎麼看到

- 產出：兩個大小的字元語言模型、每個集合每列前 8 名候選的分數、融合後的標準表格（`tools/evalstats.py compare`），以及事先寫死的「要不要做進核心」判斷。
- 看到它有效的方式：discordtune（使用者真實聊天，main 在本機跑、只報數字）照 §4 的規則過關。

## 2. 做法

### 2.1 候選

- 用現行出貨的解碼：model-v5（`f81a021e…`）＋classes-v3、`--context`、不開詞包，CLI 或 Python 參考實作的 `--dump`（64 名）取前 8 名與 n-gram 分數。聊天、書面兩種設定各一份。
- 集合：cvtune（`31de456d…`）、wikitune（`8dcfe40c…`）調參數；dev302、打字測驗、錯字回報（當下列數）、discordtune 只記錄。只用候選數 ≥ 2 的列計算重排，其他列照第一名計分（全部列都進表格）。
- 不重用 S5j 存的舊候選：那是舊模型的候選，現在出貨的不是它們。融合的 8B 數字只當背景，不和這片逐列比較。

### 2.2 模型

- 字元級、只看漢字。**訓練文字用和 model-v5 的 n-gram 完全相同的函式產生**（不另寫一套）：
  - 維基：`build_counts.articles(200000)` 的每一篇，照 `count_batch` 的順序做 HTML 實體還原、`TEMPLATE` 三次、`MARKUP`、`SENT` 切句，每句 `convert()`（OpenCC 表＋TWVariants，**不用** S2w 的 MediaWiki 轉換：model-v5 的計數沒有加 `--mw`，`docs/contracts/model-v5.md`），再取 `HAN` 漢字段、丟掉長度 < 2 的段。
  - 口語：`colloquial-train.txt` 與 `synth.txt` 的每一行照 `build_counts_text.py` 的做法 `convert()` 後取 `HAN` 段、丟掉長度 < 2 的段。
  - 每段一個序列、前面加句首符號 BOS。字表取訓練文字裡出現 ≥ 5 次的字，其他字映到 UNK。
  - 輸入檔的 SHA-256 必須等於 `experiments/model-v5/README.md` 那張表的值（維基 dump `5db9052e…`、`colloquial-train.txt` `3e833d06…`、`synth.txt` `bec7a6a2…`、TWVariants `245b94eb…`、STCharacters `a0ca1601…`），不符就停。
- 兩個大小（decoder-only Transformer，tied embedding，context 64）：S = 4 層、d 256、4 頭；M = 6 層、d 384、6 頭。參數量照實報。
- 權重和 n-gram 相同：維基 ×1，口語（`colloquial-train.txt`＋`synth.txt`）×5（每個 epoch 重複取樣 5 次）。
- **不重疊的定義（建構檢查）**：wikitune 的來源條目序號 ≥ 300,000（訓練只用前 200,000 篇）；cvtune 與 `colloquial-train.txt` 的切分以**原始句**判斷（`build_tune.py`：`is_tune(raw)` 為真的句子只進 cvtune，為假的轉換後逐行寫進 `colloquial-train.txt`，沒有其他過濾）。檢查在 188 上對原始來源（`~/.cache/shanjie/sources/colloquial/` 的 `cv-*` 與 `cmn_sentences.tsv.bz2`，雜湊記進報告）逐行算 `is_tune(raw)`：為假的行數必須等於 `colloquial-train.txt`（`3e833d06…`）的行數，為真與為假的行數加起來等於原始總行數。**不對轉換後的句子套 `is_tune`**（轉換會改字，雜湊就變了）。腳本逐項檢查，**只有建構檢查失敗才停**。另外報一個記錄用的數字：調參集正解句整句以子字串出現在訓練文字裡的比例（各集合分開報，不是停止條件；小模型可能背下短句，這個數字讓讀者判斷調參集的分數有多可信）。
- 一個 epoch、AdamW、cosine、峰值學習率 1e-3、隨機種子固定；188 的 RTX 3070 上訓練（main 執行）。權重與中間檔放 188 的 `%USERPROFILE%\.cache\shanjie\work\s7a\`，不進 repo。

### 2.3 分數與融合

- 每個候選 c 的輸入序列是 `[BOS] + k + c`，k 是該列前文經引擎的 `context_key`（最多 2 個漢字；結果是句首哨兵「^」時 k 為空）。`LL(c) = Σ_{i ∈ c 的位置} log10 P(c_i | 前面所有字)`，只加候選本身的位置；字表外的字當 UNK。**候選打分與 §3 第 2 項的困惑度用同一個函式**。另外一組**只記錄**：k 改成前文欄最後 16 個連續漢字（同一個序列格式），看加長前文值不值得改殼的介面。
- 同一列的候選字數都相同（同一串注音），所以不做長度正規化。
- 融合：`s'(c) = s_ngram(c) + α · LL(c)`，只在 n-gram 第一名與第二名的分差 `< τ` 時重排（log10 單位）；否則照第一名。
- 格點：α ∈ {0.1, 0.2, 0.3, 0.5, 0.7, 1, 1.5, 2}，τ ∈ {不設門檻, 0.5, 1, 2, 3, 5}。
- **選法（事先寫死）**：每個大小、每種設定（聊天、書面）各選一組 (α, τ)：在 cvtune 與 wikitune 合計的 top1 最高；同分取 τ 較小、再同分取 α 較小。選好就凍結，記錄集合只用凍結的值。

## 3. 驗收

1. **第 0 步**：輸入雜湊（§2.2 的表）；建構檢查（§2.2）與記錄用的整句子字串比例；候選檔的雜湊與列數寫進報告；用 dump 的第一名重算 n-gram 基準，必須等於 CLI 摘要行的 top1。
2. **訓練**：S、M 各一次；報訓練文字的字數、字表大小、最終 loss、在 wikitune 與 cvtune 正解句上的每字困惑度（只看正解，用和候選打分同一個 `LL` 函式）。
3. **表格**：每個大小 × 兩種設定 × 每個集合一行（`tools/evalstats.py compare`，基準是 n-gram 第一名）：n、top1 基準／新、改對／改壞、精確 McNemar p、CER 基準／新、Δtop1 與 ΔCER 的配對 bootstrap 95% 區間。另外報選出的 (α, τ)、門檻觸發的列比例、前 8 名的 oracle（正解在前 8 名的比例）、**小模型單獨挑**（只用 `LL` 選前 8 名裡最高的）的正確率，以及每個集合「前文 key 不是哨兵的列數」與「加不加前文 `LL` 有變的列數」——後者在錯字回報檔（有前文）與 discordtune 都必須 ≥ 1，否則前文沒有接上，停下來查。16 字前文那組另列。
4. **一致性**：報「Mac numpy 對 188 torch，實際權重，cvtune 聊天全部候選的最大 |ΔLL|」。
5. **延遲與大小**：Mac 上用 numpy 推論對 8 個候選 × 17 字的一批量 p50／p95（200 次），S 與 M 各一；int8 量化後的檔案大小（只量，不出貨）。
6. **判斷**：照 §4 寫成一行結論，寫進研究紀錄；PLAN「S7：自訓小模型」那一節更新狀態。
7. 單元測試（不需要 GPU）：
   - 資料準備：一段原始維基文字與一行口語，經這一片的函式得到的漢字段，和 `build_counts` 的路徑（同一組函式）逐段相同。
   - 字表與序列（UNK、BOS）；前文取法：前文結尾是漢字與結尾是標點兩種情況，`k` 分別是最後 2 個漢字與空。
   - `LL`：一個手算的玩具模型；同一個玩具模型上前文會改變 `LL` 的例子（有 k 與沒有 k 的值不同、都等於手算值）；UNK 的情況。
   - numpy 推論與 torch 推論在同一組權重上的 `LL` 差 < 1e-4，**經過實際的 `.npz` 匯出函式**（LayerNorm、GELU、因果遮罩、tied embedding 都要涵蓋）；這個測試需要 torch，由 main 在 188 用 CPU 跑（§5）。
   - 建構檢查：玩具資料裡有一行原始句 `is_tune` 為假、轉換後的字串 `is_tune` 為真，檢查仍判定通過。
   - 融合在 `τ` 門檻上下的行為與同分規則；選法只讀調參集。

## 4. 做進核心的判斷（事先寫死）

照 §2.3 凍結的 (α, τ)，**discordtune 聊天設定**：

- Δtop1 ≥ +1.0 個百分點（4,958 列裡至少 +50 列），精確 McNemar p < 0.05，Δtop1 的 bootstrap 95% 區間不含 0，ΔCER ≤ 0；
- 而且 cvtune、wikitune、dev302、打字測驗、錯字回報在兩種設定都沒有「淨值為負而且 p < 0.05」的格。

S 過就選 S。只有 M 過時，要使用者同意多約 30 MB 才算過（問題留給使用者）。過了才開下一片（核心實作、Rust 推論、保留集由 fresh verifier 跑一次）。

**停止條件**：S 與 M 在 cvtune＋wikitune 合計的最佳格點都比 n-gram 少於 +0.5 個百分點，就停：寫進研究紀錄，下一步改試詞類 trigram（Fable 5.1 的備案）。M 在 Mac 上（numpy）p95 > 150 ms 時只考慮 S。訓練發散、GPU 記憶體不夠、輸入雜湊不符、建構檢查失敗、前文有接上的列數是 0、或一致性關卡的最大 |ΔLL| ≥ 1e-4，也停下來回報。

## 5. 範圍外、預算、限制

- **範圍外**：Rust 推論與出貨；改殼的前文長度；保留集（只在通過 §4 之後的下一片跑）；詞包開著的情況。
- **負責與機器**（executor 寫 `experiments/s7a-tinylm/` 的全部程式、單元測試與 README，不連網、不跑 188；其餘由 main 執行）：
  | 步驟 | 誰 | 哪台 |
  |---|---|---|
  | torch 相依的單元測試（numpy／torch 一致、匯出、訓練程式的小型試跑），**排在完整訓練之前** | main | 188（同一個 venv，CPU） |
  | 訓練文字、建構檢查、訓練 S／M（torch，GPU） | main | 188（`%USERPROFILE%\ime-research\proto\.venv`，torch 2.11＋cu128） |
  | 公開集合的候選（Python 參考實作 `--dump`，兩種設定） | main | 188 |
  | 公開集合的 `LL` 打分 | main | 188 |
  | 權重匯出成 `.npz`、拷回 Mac（`~/.cache/shanjie/work/s7a/`，不進 repo） | main | 188 → Mac |
  | **一致性關卡**：Mac 上用 numpy 對 cvtune 聊天設定的全部候選重算 `LL`，和 188 上 torch 的結果逐項比對（實際權重） | main | Mac |
  | discordtune 的候選與 `LL` 打分（**numpy 推論**，不用 torch）、只回數字、檔案用完刪掉；排在一致性關卡通過之後 | main | Mac（`/opt/homebrew/bin/python3`，numpy 2.5，已裝，不需要安裝任何東西） |
  | 融合格點、表格（numpy） | main | Mac |
  | 延遲（numpy 推論，8 個候選 × 17 字一批，200 次的 p50／p95） | main | Mac |
  - executor 要寫一個不依賴 torch 的 numpy 前向推論（讀 `.npz`），和 torch 的結果在單元測試裡比對。延遲用 numpy 量，是 Rust 版的上限參考（推論）。
- **預算**：executor 實作 1 次＋修正 1 次；訓練 S、M 各一次（不調超參數）。
- **限制**：executor 不讀 `eval/holdout`、`~/side-project/shanjie-private`、學習檔、`~/Library`、鑰匙圈；不安裝、不啟動 App；不 push、不開 PR、不碰其他 worktree；不連網（程式要能離線跑）。
- **授權**：模型由維基（CC BY-SA 4.0）與 Common Voice（CC0）、Tatoeba（CC BY 2.0 FR）訓練，和 n-gram 相同；這一片不出貨，出貨時照 `LICENSES/data.md` 補一列。

## 6. 回滾

只新增 `experiments/s7a-tinylm/` 與文件；revert 即可，不影響出貨。

## 7. 審查紀錄

- 第一次 plan-verifier（2026-10-10）：REVISE 4 點，全部 **FIX**。
  1. 訓練文字的轉換前後矛盾（寫了 S2w，model-v5 沒用 `--mw`）、步驟沒寫全、沒有釘輸入雜湊：改成用 `build_counts` 同一組函式（OpenCC `convert()`），列出每一步，雜湊照 model-v5 的表，加單元測試。
  2. 「不重疊」的檢查算不出來：定義成建構檢查（只有它會停），整句子字串比例只記錄。
  3. 前文怎麼進 `LL` 沒寫清楚、測不到：寫明 `[BOS] + k + c`，`LL` 與困惑度同一個函式，加前文與 UNK 的測試，表格報「前文有效的列數」（必須 ≥ 1）與小模型單獨挑的正確率。
  4. 步驟沒有負責人與機器、Mac 沒有 torch：列出每一步的負責人與機器；Mac 端用 numpy 推論，不需要安裝。
- 第二次 plan-verifier（2026-10-10）：REVISE 2 點，逐項處置，兩點都 **FIX**（之後再一次收尾審查）。
  1. 建構檢查把 `is_tune` 套在轉換後的句子上，一跑就失敗：改成在 188 上對原始來源算 `is_tune(raw)`，用行數核對 `colloquial-train.txt`；加單元測試。
  2. numpy／torch 一致性沒有人能跑：torch 相依的測試由 main 在 188 用 CPU 跑、排在訓練前；discordtune 之前加一致性關卡（Mac numpy 對 188 torch，實際權重，cvtune 聊天），差 ≥ 1e-4 就停。
- 同日改編號：S6 → S7a（PLAN 的 S6 是雲端；這片屬於 S7 自訓小模型）。

## 8. 結果（2026-10-11）

- 一致性關卡通過（最大 |ΔLL| 1.5e-5）。§4 的 discordtune 聊天：**M 全部通過**（+67 列、p = 0.0008、區間 [29, 105]、ΔCER −0.23 個百分點、其他集合沒有顯著淨負），**S 沒過**（+31 列、p = 0.149）。照本節「只有 M 過時，要使用者同意多約 30 MB 才算過」，**等使用者決定**；通過是用 float32 權重量的（M 57.7 MB）；int8 複本 14.7 MB 只量了大小、準確度沒量過，出貨若用 int8 要重量 §4。Mac numpy float32 p95 44.8 ms。數字與表格在研究紀錄 2026-10-11。

