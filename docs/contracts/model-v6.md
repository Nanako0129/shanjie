# 契約：model-v6、classes-v4、kn-v1 與 tinylm-v5（A3 詞包與專有名詞進計數；名單層上線；Kneser–Ney 回退開啟；量詞詞類；口語補次數）

狀態：草稿（2026-10-11 起草，2026-10-14 補完），待 plan-verifier。前提是 A3（#111）、kn-core（#107）已合併；N1（`feat/names-lexicon`，#110）在 2026-10-14 收尾後合併。維護者 2026-10-14 選「照草稿全做、拚今天」。

## 0. 依據（使用者的決定與量過的事）

- **使用者 2026-10-10**：
  - Kneser–Ney 回退分布（θ=1、β=1，`docs/contracts/kn-smoothing.md` §7）併進下一次模型重建一起做進核心；核心這一層是 kn-core 片（預設關、沒有側檔時逐位元組不變）。
  - 常見公司、品牌、學校、店名放進**一般詞庫**（常開，不是選用詞包）；N1 片建好名單與評測選項，上線放在 model-v6（`docs/contracts/names-lexicon.md` §0）。
  - 「能做的實驗都把他做完，可用的就迭代模型」。
- **A3 詞包**（v0.4.1）：間隔號名字與角色列表條目，詞包詞變多；和 model-v5 的 A2 一樣，詞包詞要進斷詞詞彙才有自己的計數。
- **量過的限制**（研究紀錄 2026-10-10、2026-10-11）：
  - 計數只救得了語料裡出現夠多次的名字（model-v5 §7：宿儺期望次數 2，仍輸「素娜」；角色名片段聊天 67→76）。
  - 語料出現過的疊加詞、詞包詞，詞條分數會被壓到語料頻率：`min(檔案分數, log10(c/N))`（`core/src/lm.rs` `CappedLexicon::new`）。
  - 專有名詞在 model-v5 的口語訓練檔（Mac 上的 `colloquial-train.txt`）只出現 1,622 個裡的 54 個，「酷澎」0 次；維基只取最舊的 20 萬篇。所以**重算計數多半救不了「酷澎」**（N1 檢查集裡它是第 6 名，聊天）。名單層的分數規則要另外定（§2.4）。
  - KN 開著時，詞彙內的詞的 `pb` 由 `β·N′/ΣN′ + (1−β)·pb` 取代；β=1 時選字記憶加在詞條分數上的加分只剩 `(1−λ)·lp` 那一項（kn-core 契約 §4 範圍外，留給這一片量）。

- **維護者 2026-10-14**（選擇題）：
  - 「一整坨」（錯字回報第 108 列）：量詞詞類（讓罕見量詞和常見量詞同類）與口語語料補次數兩條都做（§2.1b、§2.2b）。
  - 口語補次數用 S7f 的生成管線（`experiments/s7f-corpus/generate.py`，Cerebras gpt-oss-120b），**花費上限 US$15**。
  - N1 與 ACG 詞包同音的「旗農」（對奇儂）、「錸德」（對萊德）從名單拿掉；丸亀製麵改收台灣寫法「丸龜製麵」。
  - 名單層分數 +2（§2.4，2026-10-11 的探索：兩種設定「酷澎」第一、守門 0 改壞）。
  - tinylm 綁 n-gram 模型的雜湊，換 model-v6 後語意校正的三檔要在新模型上重選，發 `tinylm-v5`（§2.7）。
- **詞庫已變**：拿掉粵語用字的疊加層詞（#152）改了 `overlay-add.tsv`，所以它的雜湊和 model-v5 不同（§2.1 的輸入固定照此放寬）。

## 1. 要做到什麼、怎麼看到

- App 內建的模型換成 model-v6＋classes-v4，旁邊附 KN 側檔 `kn.sjkn`（開啟）；專有名詞名單 `names-add.tsv` 成為常開的一般詞庫層。
- 一般打字不退步（§3.2 的守門）；專有名詞檢查集與動漫集變好或不變。
- **怎麼看到**：
  - §3 的評測表、動漫集、名單檢查集；
  - 使用者實機：打「酷澎」「蝦皮購物」「八方雲集」「洛琪希」「虎杖悠仁」，以及選字記憶（改選一次「再」之後，同前文再打一次會出現改選的字）。

## 2. 改動

### 2.1 計數：A3 詞包詞與專有名詞進斷詞詞彙

- 配方照 model-v5（`experiments/model-v5/README.md` 的 `count.bat`）：維基 `build_counts.py --articles 200000 --procs 10 --expected`（×1，不加 `--mw`）、口語 `build_counts_text.py --expected`（×5），加法計數（model-v5 §8），`tools/build_lm.py` 的 `PRUNE=2`、`D=0.75` 不變。在 188 跑，綁 P-core、絕對路徑。
- **計數詞表**：`experiments/model-v5/count_lexicon.py` 的規則（model-v5 §9：有同音衝突的詞與衝突讀音的片段不進計數）分別套用在
  - A3 合併後的 `data/packs/acg-add.tsv`（衝突讀音＝`acg-collisions.tsv`、`collision-readings-v5.txt`、以及 A3 的處置檔），
  - `data/lexicon/names-add.tsv`（衝突讀音＝`names-collisions.tsv`），
  兩份輸出各給一次 `--extra-lexicon`。`count_lexicon.py` 若要改成能吃名單，改動與單元測試在這一片。
- **輸入固定**：照 model-v5 的輸入表記 SHA-256（維基 dump、`colloquial-train.txt`、`synth.txt`、`mcbpmf-data.txt`、`overlay-add.tsv`、OpenCC 兩個表），除了兩份計數詞表、`overlay-add.tsv`（#152 拿掉粵語詞後的版本，記新雜湊）與 §2.1b 新增的補次數檔，每一列都要等於 model-v5 那次（`experiments/model-v5/README.md` 的表）。
- **重現核對**：`build_counts.py`、`build_counts_text.py`、`count_lexicon.py`、`experiments/s2-classes/cluster.py`、`tools/build_classes.py` 對 model-v5 建置的 commit（98da9ad）diff 為空、`tools/build_lm.py` 只有 kn-core 片的重構（`merged_counts`）時，不重跑 model-v5 的重現核對；另外用同一份 model-v5 計數跑新的 `build_lm.py`，模型（加指紋前）要等於 `7961cb9d…`。任何一個腳本有別的改動，就照 model-v5 §2.1 跑一次重現核對。
- **覆蓋量測**（計數之前）：在同樣的 20 萬篇維基與兩個口語檔裡，數 A3 新增詞與名單詞至少出現一次的個數，以及「酷澎」「洛琪希」「虎杖悠仁」的次數，寫進研究紀錄（日期在計數之前）。只報告，不設門檻（§0 已知計數救不了的名字由 §2.4 處理）。

### 2.1b 口語補次數（S7f 生成管線）

- **種子詞**：`experiments/model-v6/seeds-topup.txt`（進 git，CC0，main 手寫）：錯字回報與錯誤桶表裡「詞不常見、模型沒見過接續」的口語詞與量詞（坨、一整坨、整坨…），加上 §2.2b 量詞表裡的罕見量詞。只用一般常識寫，不收任何私人聊天裡看到的詞。
- **生成**：`generate.py --provider cerebras --model gpt-oss-120b`，把關規則照 S7f（簡體字、長度、去重、和評測集重疊的句子拿掉，含 `--dedupe-against` 既有的口語檔）；`--budget-usd 15`、帳本記進研究紀錄；字數上限 `--chars` 由 main 依帳本試跑的單價定，不超過 50 萬字。金鑰只在單一程序的環境變數，不印、不寫檔。
- **計數**：輸出檔放 repo 外（`~/.cache/shanjie/`、188），記 SHA-256；和口語檔一樣用 `build_counts_text.py --expected`、權重 ×5。
- **只記錄**：生成後在補次數檔裡數「坨」「整坨」與種子詞的出現次數，寫進研究紀錄。

### 2.2b 量詞詞類（classes-v4）

- 量詞表 `experiments/model-v6/classifiers.txt`（進 git，CC0，main 手寫）：常見中文量詞一行一個。
- 分群（`cluster.py --keep-from`）之後、`build_classes.py` 之前，新的小工具 `experiments/model-v6/force_classes.py`：量詞表裡**計數少於 1,000** 而且類別和「個」不同的詞，改成「個」的類別；印出搬了哪些詞（詞、原類別、計數）。計數 ≥ 1,000 的量詞不動（它們自己的接續已經夠）。單元測試用合成的分群檔：門檻以下才搬、錨點類別正確、不在表裡的詞不動、錨點不存在就停。
- 量到的：「一整坨」兩種設定的名次（目標是第一）；§3.2 的守門照常。

### 2.2 模型、詞類與私有條目

- `tools/build_lm.py` 不改；指紋條目照 model-v4、v5 的做法由 main 在私有流程加回（不進 repo 與公開文件），在算雜湊、建詞類表、產生 KN 側檔之前加。
- 詞類：`cluster.py --keep-from` 指向 model-v5 那次分群的輸出（舊詞沿用 classes-v3 的類別，model-v5 §9），新詞貼進最佳類別；`tools/build_classes.py` 對加過私有條目的模型建 `classes.sjc`（classes-v4）。

### 2.3 KN 側檔（kn-v1）

- 188：`tools/kn_cont.py --theta 1 --beta 1` 用 model-v6 的計數與**最終模型**（加過私有條目）產生 `SJKN0002`，記 SHA-256。ΣN′ 由 `lm.kn_total`＋`build_lm.variant_classes` 算好寫進檔頭（kn-core §1）；`lm_eval.py` 讀它時用 `kn_classes` 重算比對。
- 放在模型旁邊（`data/lm/kn.sjkn`、App 的 Resources），Rust 核心自動載入。
- **跨語言核對**（決定性的一項，照 kn-core §3 第 5 項）：最終檔＋側檔，Rust CLI 與 `lm_eval.py` 在 dev302、打字測驗、錯字回報、cvtune、wikitune × 聊天、書面（`--context`）的摘要行逐字相同（含 `+kn:` 與 `top1_sha256`）。

### 2.4 名單層的分數

（待定：依量測結果寫。候選：名單層不扣 `UNSEEN_OVERLAY_PENALTY`；或照固定選法在調參集上選一個加分。）

### 2.5 App 與發版

- **名單層常開**：`load_lexicon*` 在 `sandhi-add.tsv` 之後讀 `names-add.tsv`，同時併進封頂用的疊加層文字（和 N1 的 `--extra-overlay` 同一條路，`--extra-overlay` 保留給研究用）；缺檔是錯誤（和 `overlay-add.tsv` 一樣，不靜默略過）。FFI、`core/include/shanjie.h`、`Support.swift`、`check-app.sh` 的檔案清單跟著改。
- **授權**：`build-app.sh` 寫進 App 的資料授權說明加名單的三方標示（name-suggestion-index 的 BSD-3 版權聲明全文、政府資料開放授權條款第 1 版的標示文字、Wikipedia 貢獻者 CC BY-SA 4.0），`LICENSES/name-suggestion-index-BSD-3-Clause.txt` 隨 App 內附；`LICENSES/data.md` 的「不隨 App 內附」改掉。
- **KN 側檔**：`build-app.sh` 複製並核對 `data/kn.sjkn.sha256`；`check-app.sh` 檢查它在 Resources；`ci.yml`、`release.yml` 下載 Release `kn-v1` 並核對雜湊。
- **Python 參考實作**：`lm_eval.py` 與 `predict.py`（`sp-predict.txt` 的產生器）在模型旁邊有 `kn.sjkn` 時照樣載入，和 Rust 一致；所有依模型的 golden 用 Python 產生、Rust 逐位元組相同。

### 2.7 語意校正重調（tinylm-v5）

- 權重不變（S7a M），只在 model-v6＋classes-v4＋kn-v1＋名單層的候選上照 `docs/contracts/rerank-strength.md` §11 重做：分數檔（188，唯一的 CLI commit 照 §11.1）、§1 選值、§4.2–§4.4、D2c 拆解只記錄；`export_sjtl.py --type int8 --ngram-model <model-v6> --levels …` 匯出 SJTL0003；§4.5 保留集（fresh verifier）。
- 前提：語意校正強度那一片（`feat/rerank-strength`，SJTL0003 讀檔、設定 UI）先合併；它切換到 `tinylm-v4` 的 PR 若還沒合併，就直接切到 `tinylm-v5`。

### 2.6 順序與 PR（照 model-v5 §2.5）

1. 前提：A3、N1、kn-core 合併；本分支從含三者的 main 開出（N1 未合併前，§2.1b 的生成與 §2.2b 的工具可以先做）。
2. 覆蓋量測、計數詞表（§2.1）、補次數檔（§2.1b），188 計數 → `build_lm.py` → 私有條目 → 分群＋`force_classes.py`＋`build_classes.py` → `kn_cont.py`。三個檔是最終檔，記 SHA-256。
3. 跨語言核對（§2.3）；§2.4 的分數規則；§3 的量測、所有 golden 重產，都在最終檔上做。
4. 詞包與名單的衝突在 model-v6 上重新偵測（§3.3）。
5. fresh verifier：§3 重跑抽查、**保留集（在問 Release 之前）**；main：私有條目核對（開著 KN）。
6. 以上通過後，**才問使用者**能不能建 Release `model-v6`、`classes-v4`、`kn-v1`（`--latest=false`）；建好後下載回來核對雜湊。接著 §2.7 的 tinylm-v5，保留集過了再問 Release `tinylm-v5`。
7. PR 1：這份契約、三份 `docs/releases/*.md`、計數詞表的腳本改動。PR 2：改引用、App 載入名單層與側檔、授權、golden。

## 3. 驗收

### 3.1 評測表（照 repo `CLAUDE.md`）

- 基準＝出貨中的設定（model-v5＋classes-v3、A3 詞包、沒有名單層、沒有 KN）；新＝model-v6＋classes-v4＋kn-v1＋名單層。
- dev302、打字測驗、錯字回報，聊天與書面，開詞包（預設）各一行、不開詞包各一行；Discord 調參集由 main 在本機跑，只貼數字。
- **拆解**（只記錄）：model-v6 不開 KN、不開名單層各一組，看三個改動各自的貢獻。

### 3.2 守門（cvtune、wikitune；不用 dev302 調）

- cvtune、wikitune、dev302、打字測驗任何一格「配對檢定 p < 0.05 而且淨值為負」就否決。
- §2.4 若要選數值，只用 cvtune、wikitune 選，名單檢查集只報。

### 3.3 名字、動漫與衝突

- 名單檢查集 `eval/dev/names/names.txt`、動漫集（works、characters、charparts、recent、interpunct），兩種設定的表；「酷澎」的名次記錄。
- **衝突重驗**：用最終檔對詞包與名單各跑一次 `detect_collisions`。沒處置的 (a)、(b) 就停，用選擇題問使用者；(c)（詞包或名單的詞把詞庫的同音詞擠下第一名）報數字與清單，問使用者（model-v5 §9 的先例是「詞包名字排第一」，名單層沒有先例）。
- 詞包與名單之間的同音（一個詞包詞和一個名單詞同讀音）一併列出。

### 3.4 選字記憶與探針

- `make test` 全綠（`core/tests/engine_learn.rs` 的鏡像、A2、污染測試用真模型，側檔在 `data/lm` 時就是開著 KN 跑）。任何一條因為 KN 失敗就停，回報哪一條、差多少，用選擇題問（候選：把學到的加分疊在 KN 之後；β 改小；這一版不開 KN）。
- 「大概十分鐘後到」、s2f 的「竈門」、s2h 的「好吧」不退；`eval/probe/s2r-probe.txt` 不退。

### 3.5 規模

- 模型 < 100 MB；側檔大小；`/usr/bin/time -l` 的記憶體（名單層＋側檔相對 model-v5）；每鍵延遲照既有 perf 測試，另報 KN 開關的 p95。

### 3.6 收尾

- fresh verifier 跑保留集（新、舊各一次 `--set holdout --context --rowstats`，只回表格），**在問 Release 之前**；
- 私有條目核對照 model-v4 的標準，開著 KN；
- 使用者實機（§1）。

## 4. 停止條件、預算、限制

- **停止條件**：守門否決；輸入雜湊和 model-v5 不同（計數詞表、`overlay-add.tsv`、補次數檔除外）；補次數的帳本達到 US$15；重現核對不成立；跨語言核對有任何一行不同；沒處置的衝突；選字記憶測試失敗；188 單批超過 8 小時；下載回來的雜湊不符。
- **預算**：生成花費 ≤ US$15（帳本）；計數、建模、側檔、分數檔由 main 在 188 跑（Colab 只在 188 的輸出能逐位元組重現時才用，見 rerank-strength §11.1 的紀錄）；`force_classes.py` 與種子、量詞表交 executor 或 main；App 載入名單層、授權、golden 重產交 executor 1 次＋修正 1 次。
- **限制**：建 Release 先問使用者；agent 不讀 `eval/holdout`（verifier 照規則除外）、學習檔、`~/Library`、鑰匙圈、`~/side-project/shanjie-private`；私有條目的一切不進 repo；agent 不安裝、不啟動 App。

## 5. 範圍外

- 資訊＋AI 領域詞包（另一片）；Rust 測試改在 188 WSL 跑（另一片，`chore/linux-tests`）。
- 罕見名字的分數下限（宿儺這類，詞包詞）；疊加層「台」「臺」寫法和官網不同（臺北城大、臺南藝大）；在／再（P1a 已停）；S7a 小模型重排；Pitman–Yor；舊 Release 的刪除。
