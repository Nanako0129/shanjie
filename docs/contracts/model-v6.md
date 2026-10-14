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
- **維護者 2026-10-14（續）**：model-v6 **開 KN，核心一起改**（讓在 KN 開著時調過的語意校正模型能一起載入，§2.7）；今晚照關卡做，明天凌晨發版。
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
- **重現核對**：`build_counts.py`、`build_counts_text.py`、`count_lexicon.py`、`experiments/s2-classes/cluster.py`、`tools/build_classes.py` 對 model-v5 建置的 commit（98da9ad）diff 為空、`tools/build_lm.py` 只有 kn-core 片的重構（`merged_counts`）時，不重跑 model-v5 的重現核對；另外用同一份 model-v5 計數跑新的 `build_lm.py`，模型（加指紋前）要等於 `7961cb9d…`。§7 P3-4 給 `build_lm.py`、`build_classes.py` 加的預設關 `--extra-overlay` **不算**需要重跑的改動，條件是：不帶這個參數時，同一份計數建出的模型等於 `7961cb9d…`、`build_classes.py` 建出的詞類表和 classes-v3 逐位元組相同（兩項都在 188 上跑、記進研究紀錄）。任何一個腳本有別的改動，就照 model-v5 §2.1 跑一次重現核對。
- **覆蓋量測**（計數之前）：在同樣的 20 萬篇維基與兩個口語檔裡，數 A3 新增詞與名單詞至少出現一次的個數，以及「酷澎」「洛琪希」「虎杖悠仁」的次數，寫進研究紀錄（日期在計數之前）。只報告，不設門檻（§0 已知計數救不了的名字由 §2.4 處理）。

### 2.1b 口語補次數（S7f 生成管線）

- **種子詞**：`experiments/model-v6/seeds-topup.txt`（進 git，CC0，main 手寫）：錯字回報與錯誤桶表裡「詞不常見、模型沒見過接續」的口語詞與量詞（坨、一整坨、整坨…），加上 §2.2b 量詞表裡的罕見量詞。只用一般常識寫，不收任何私人聊天裡看到的詞。
- **生成**：`generate.py --provider cerebras --model gpt-oss-120b`，把關規則照 S7f（簡體字、長度、去重、和評測集重疊的句子拿掉，含 `--dedupe-against` 既有的口語檔）；`--budget-usd 15`、帳本記進研究紀錄；字數上限 `--chars` 由 main 依帳本試跑的單價定，不超過 50 萬字。金鑰只在單一程序的環境變數，不印、不寫檔。
- **計數**：輸出檔放 repo 外（`~/.cache/shanjie/`、188），記 SHA-256；和口語檔一樣用 `build_counts_text.py --expected`、權重 ×5。
- **只記錄**：生成後在補次數檔裡數「坨」「整坨」與種子詞的出現次數，寫進研究紀錄。

### 2.2b 量詞詞類（classes-v4）

- 量詞表 `experiments/model-v6/classifiers.txt`（進 git，CC0，main 手寫）：常見中文量詞一行一個。
- 分群（`cluster.py --keep-from`）之後、`build_classes.py` 之前，新的小工具 `experiments/model-v6/force_classes.py`：讀 `cluster.py` 的輸出資料夾（`edges.npz`、`cls-N-K.npz`），量詞表裡 **`edges.npz` 的 `uni` 計數（加權後的計數，維基 ×1＋口語 ×5，`build_classes.py --edges` 讀的同一個欄位）少於 1,000** 而且類別和「個」不同的詞，改成「個」的類別；不在詞彙裡的表列詞印出來、略過。輸出**另存**一個新的 `cls-N-K` 檔（不覆寫 `cluster.py` 的輸出），記 SHA-256，交給 `build_classes.py --cls`；印出搬了哪些詞（詞、原類別、`uni` 計數）。`uni` ≥ 1,000 的量詞不動。單元測試用合成的分群檔：門檻以下才搬、錨點類別正確、不在表裡的詞不動、不在詞彙裡的表列詞略過、錨點不存在就停，以及一個「原始次數 < 1,000 但加權 `uni` ≥ 1,000」的詞不搬。
- 量到的：「一整坨」兩種設定的名次（目標是第一）；§3.2 的守門照常。

### 2.2 模型、詞類與私有條目

- `tools/build_lm.py` 除了 §7 P3-4 的預設關 `--extra-overlay` 之外不改；指紋條目照 model-v4、v5 的做法由 main 在私有流程加回（不進 repo 與公開文件），在算雜湊、建詞類表、產生 KN 側檔之前加。
- 詞類：`cluster.py --keep-from` 指向 model-v5 那次分群的輸出（舊詞沿用 classes-v3 的類別，model-v5 §9），新詞貼進最佳類別；`tools/build_classes.py` 對加過私有條目的模型建 `classes.sjc`（classes-v4）。

### 2.3 KN 側檔（kn-v1）

- 188：`tools/kn_cont.py --theta 1 --beta 1` 用 model-v6 的計數與**最終模型**（加過私有條目）產生 `SJKN0002`，記 SHA-256。ΣN′ 由 `lm.kn_total`＋`build_lm.variant_classes` 算好寫進檔頭（kn-core §1）；`lm_eval.py` 讀它時用 `kn_classes` 重算比對。
- 放在模型旁邊（`data/lm/kn.sjkn`、App 的 Resources），Rust 核心自動載入。
- **跨語言核對**（決定性的一項，照 kn-core §3 第 5 項）：最終檔＋側檔＋名單層（三者都開），Rust CLI 與 `lm_eval.py` 在 dev302、打字測驗、錯字回報、cvtune、wikitune × 聊天、書面（`--context`）的摘要行逐字相同（含 `+kn:` 與 `top1_sha256`）。另加一個 golden 例子含只在名單裡的詞（例如「酷澎」）。

### 2.4 名單層的分數

- **規則**（維護者固定的選擇，不在 cvtune、wikitune 上選）：名單詞的詞條分數＝`build_names.py` 照 `scores()[字數]` 算出的分數 **＋2**（log10）。加分在建置時寫進 `names-add.tsv`（`build_names.py` 的常數 `NAMES_BONUS = 2`），載入端不另外加：Rust 與 Python 都把 `names-add.tsv` 當成一般疊加層讀，之後照疊加層原本的規則——語料出現過的詞封頂成 `min(檔案分數, log10(c/N))`，沒出現過的扣 `UNSEEN_OVERLAY_PENALTY`（1.0）。所以沒有計數的名字最後是 `scores()[n] + 1`，和 2026-10-11 的探索（N1 研究紀錄同日「探索：名單層的分數往上加」，d＝2 時「酷澎」兩種設定第一、守門集合改壞 0）一致。
- N1 的 (c) 同音規則照新分數重跑（`build_names.py` 的衝突偵測讀的就是加分後的檔），結果和處置進 N1 合併前的那次重建。
- 單元測試：`build_names.py` 的測試斷言一個合成名字的輸出分數等於 `scores()[n] + 2`；Rust 與 Python 各一個測試斷言 `names-add.tsv` 的詞照疊加層規則封頂與扣分（同一個合成列，兩邊的詞條分數相同）。

### 2.5 App 與發版

- **名單層常開**（安全審查的要求在 §7 的 P2-3、P3-4、P3-6、P3-7、P3-8）：`load_lexicon*` 在 `sandhi-add.tsv` 之後讀 `names-add.tsv`，同時併進封頂用的疊加層文字（和 N1 的 `--extra-overlay` 同一條路，`--extra-overlay` 保留給研究用）；缺檔是錯誤（和 `overlay-add.tsv` 一樣，不靜默略過）。FFI、`core/include/shanjie.h`、`Support.swift`、`check-app.sh` 的檔案清單跟著改。
- **授權**：`build-app.sh` 寫進 App 的資料授權說明加名單的三方標示（name-suggestion-index 的 BSD-3 版權聲明全文、政府資料開放授權條款第 1 版的標示文字、Wikipedia 貢獻者 CC BY-SA 4.0），`LICENSES/name-suggestion-index-BSD-3-Clause.txt` 隨 App 內附；`LICENSES/data.md` 的「不隨 App 內附」改掉。
- **KN 側檔**：`build-app.sh` 複製並核對 `data/kn.sjkn.sha256`；`check-app.sh` 檢查它在 Resources；`ci.yml`、`release.yml` 下載 Release `kn-v1` 並核對雜湊。
- **Python 參考實作**：`ime.OVERLAYS` 在 `sandhi-add.tsv` 之後加 `names-add.tsv`；`lm_eval.py` 與 `predict.py`（`sp-predict.txt` 的產生器）封頂用的疊加層詞集合也讀 `names-add.tsv`（和 Rust 的 `load_lexicon*` 同一個順序與規則），在模型旁邊有 `kn.sjkn` 時照樣載入；所有依模型的 golden 用 Python 產生、Rust 逐位元組相同。

### 2.7 語意校正重調（tinylm-v5）

- **核心改動：小模型和 KN 一起載入（SJTL0004）**（安全審查的要求在 §7，全部是這一節的驗收）。現在 `TinyLm::load_paired` 在 KN 開著時一律拒絕（`TinyLmError::KneserNey`，因為 α、τ 是在沒有 KN 時調的）。改成：
  - 新版 SJTL0004＝SJTL0003 的 212 bytes 檔頭之後接 32 bytes：調參時用的 KN 側檔 SHA-256（全 0＝沒有開 KN 時調的）；字表與張量不變。SJTL0001–0003 視為全 0。
  - `load_paired`：模型雜湊照舊要相同；KN 開著時，檔裡記的側檔雜湊要等於載入的 `kn.sjkn` 的 SHA-256（`Lm` 加一個 `kn_sha256()`），否則照舊回 `KneserNey`；KN 關著時，檔裡記的必須是全 0（在 KN 開著時調的檔，配沒有 KN 的模型也拒絕，回 `KneserNey`）。CLI 的 `--tinylm` 錯誤訊息照新規則改；`--no-kn` 照舊。
  - `export_sjtl.py` 加 `--kn-side <kn.sjkn>`（寫入它的雜湊）；**只有給了 `--levels` 才輸出 SJTL0004**，這時沒給 `--kn-side` 就寫全 0；沒給 `--levels` 時照舊輸出 v1／v2；只給 `--kn-side` 是 usage error（§7 P3-5，兩處同一個規則）。
  - 測試（Rust，用合成的小模型與小側檔，照 rerank-strength §9 的讀檔規矩）：KN 開＋雜湊相符 → 載入成功；KN 開＋不符 → `KneserNey`；KN 開＋SJTL0001–0003 → `KneserNey`；KN 關＋檔裡有非 0 雜湊 → `KneserNey`；KN 關＋全 0 → 成功；SJTL0004 每個前綴長度都回 `Format` 不 panic；`export_sjtl.py` 的單元測試核對寫入的雜湊與位移。讀檔與格式屬安全審查範圍，交 `pilotfish:security-executor`。
- **重調**：權重不變（S7a M），在 model-v6＋classes-v4＋kn-v1＋名單層的候選上照 `docs/contracts/rerank-strength.md` §11 重做，但**KN 開著**（覆寫 §11.1 的「不載 KN」）：
  - 分數檔用一個暫時的 `.sjtl`：`export_sjtl.py --type int8 --ngram-model <最終 model-v6> --kn-side <最終 kn.sjkn> --chat 0.3 2 0.25 --formal 0.7 1 2 --levels 1 2 3 0.3 2 0.25 0.7 0.5 2 0.5 1 0.25`（輸出 SJTL0004；融合參數用 tinylm-v4 的值當佔位，分數檔只用到權重，之後重選），在 188 用唯一的 CLI commit 跑 `--tinylm <暫時檔> --tinylm-scores … --dump …`（側檔在模型旁邊，KN 開著）。
  - §1 選值（標準那一檔也在新候選上重選，因為模型換了）、§4.2–§4.4、D2c 拆解只記錄；匯出 `tinylm-v5`：`--ngram-model <model-v6> --kn-side <kn-v1> --chat … --formal … --levels …`（SJTL0004）；§4.5 保留集（fresh verifier）。
- **前提與順序**：語意校正強度那一片（`feat/rerank-strength`，SJTL0003 與設定 UI，切到 tinylm-v4）先合併；SJTL0004 的核心改動在這一片。**App 切到 model-v6＋kn-v1 的 PR 必須同時切到 tinylm-v5**（不留下「KN 開著、小模型載不進來」的合併狀態）。

### 2.6 順序與 PR（照 model-v5 §2.5）

1. 前提：A3、N1、kn-core 合併；本分支從含三者的 main 開出（N1 未合併前，§2.1b 的生成與 §2.2b 的工具可以先做）。
2. 覆蓋量測、計數詞表（§2.1）、補次數檔（§2.1b），188 計數 → `build_lm.py --extra-overlay data/lexicon/names-add.tsv` → 私有條目 → 分群＋`force_classes.py`＋`build_classes.py --extra-overlay data/lexicon/names-add.tsv` → `kn_cont.py --extra-overlay data/lexicon/names-add.tsv`。三個檔是最終檔，記 SHA-256；完整指令記進 `experiments/model-v6/README.md`。
3. 跨語言核對（§2.3）；§2.4 的分數規則；§3 的量測、所有 golden 重產，都在最終檔上做。
4. 詞包與名單的衝突在 model-v6 上重新偵測（§3.3）。
5. fresh verifier：§3 重跑抽查、**保留集（在問 Release 之前）**；main：私有條目核對（開著 KN）。
6. 以上通過後，**才問使用者**能不能建 Release `model-v6`、`classes-v4`、`kn-v1`（`--latest=false`）；建好後下載回來核對雜湊。接著 §2.7 的 tinylm-v5，保留集過了再問 Release `tinylm-v5`。
7. PR 1：這份契約、四份 `docs/releases/*.md`、計數詞表與 `force_classes.py`、`build_lm.py`／`build_classes.py`／`kn_cont.py`／`lm_eval.py` 的預設關 `--extra-overlay`（§7 P3-4）、SJTL0004 核心改動與測試（SJTL0001–0003 的行為不變，出貨的 tinylm-v4 照常載入）。PR 2：改引用（model-v6、classes-v4、kn-v1、tinylm-v5 同一個 PR）、**Python 與 Rust 的名單層載入（檔案缺時是錯誤）與 App 內附 `names-add.tsv`（`build-app.sh`、`check-app.sh`）**、側檔、授權、所有 golden。名單層的預設載入、打包與它造成的每一個 golden 改動都在 PR 2，main 不會有「要讀名單檔、App 卻沒附」的狀態。

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

## 6. 審查紀錄

| 審查 | 要點 | 處置 |
|---|---|---|
| plan-verifier 第 1 次（2026-10-14，REVISE） | 1. KN 開著時小模型載不進來（`load_paired` 拒絕），§2.7 照寫跑不起來；2. §2.4 名單分數還是「待定」；3. Python 參考實作沒有名單層，跨語言核對與 golden 對不上；4. 量詞門檻沒說用哪一種計數、輸出寫到哪 | 全部 FIX：1 → §2.7 加 SJTL0004（記 KN 側檔雜湊）、`load_paired` 的新規則與測試、分數檔用暫時的 `.sjtl` 且 KN 開著、App 切 model-v6＋kn-v1 的 PR 同時切 tinylm-v5（維護者同日選「開 KN，核心一起改」）；2 → §2.4 寫成固定規則（建置時 +2 寫進 `names-add.tsv`，載入照疊加層規則）；3 → §2.5 Python 的 `ime.OVERLAYS` 與封頂詞集合讀 `names-add.tsv`，§2.3 三者都開；4 → §2.2b 用 `edges.npz` 的加權 `uni`、不在詞彙的略過、另存新 `cls` 檔並記雜湊 |
| 第 4 次審查（2026-10-14，REVISE） | P3-4 的新參數沒同步到 §2.1 的重現核對、§2.2 的「不改」、§2.6 的建置步驟與 PR 1 清單 | FIX：§2.2 改成只加 `--extra-overlay`；§2.1 寫明它不觸發重現核對的條件（不帶參數時模型 `7961cb9d…`、詞類表等於 classes-v3）；§2.6 第 2、7 項寫出參數 |
| 收尾審查（2026-10-14，REVISE；維護者選「改完再送一次審查」） | 1. 暫時 `.sjtl` 的指令缺 `--levels`，和 P3-5 的版本規則矛盾；2. P3-4 把 `ime.OVERLAYS` 放 PR 1，和「名單載入與 golden 都在 PR 2」矛盾 | 1 FIX：指令補 `--levels`（tinylm-v4 的值當佔位），§2.7 與 P3-5 的版本規則寫成同一條；2 FIX：PR 1 只給建置工具與 `lm_eval.py` 加預設關的 `--extra-overlay`，`ime.OVERLAYS` 改動移到 PR 2 |
| plan-verifier 第 2 次（2026-10-14，REVISE；之後逐項處置、收尾審查一次） | 1. PR 1 讓名單層強制載入但 App 沒附檔、golden 也還沒重產，main 會壞；2. SJTL0004 讀檔、`load_paired` 綁定、`kn_sha256` 與新的必要資料檔沒有安全審查 | 1 FIX：名單層載入、打包、golden 全部移到 PR 2（§2.6 第 7 項）；2 FIX：`pilotfish:security-reviewer` 審 §2.7 與 §2.5，結論與處置記在 §7 |

## 7. 安全審查（`pilotfish:security-reviewer`，2026-10-14）與處置

沒有 P0／P1。下列 FIX 都是 §2.5、§2.7 的實作與驗收要求（測試寫在這裡，executor 照做；每一條的突變都要以斷言失敗）。

| # | 要點 | 處置 |
|---|---|---|
| P2-1 | SJTL0004 的版本判斷要帶「有三檔」，否則強度控制靜默反灰；`kn_sha` 要在長度檢查之後讀 | FIX：同一個 match 決定 v4＝檔頭 244 bytes、有 δ、有三檔；`kn_sha`＝`bytes[212..244]`，長度檢查之後才讀；`has_levels()` 對 v4 為真、`rerank_levels` 回 7。測試：v4 小檔 12 個強度欄位填不同值、`params` 逐一相等；v4 與去掉 212..244 的 v3 的標準三元組、`ngram_sha256`、`ll_many` 逐位元組相同；前綴長度 0..=244 都回 `Format` 不 panic。突變：v4 的 levels 設 false、檔頭長度用 212、拿掉長度檢查 |
| P2-2 | `kn_sha256()` 要用 `add_kn` 驗過的同一份 bytes、比完整 32 bytes | FIX：`Lm::kn_sha256() -> Option<[u8; 32]>`，從 `Kn.raw` 算（可和 `kn_tag` 共用 `OnceLock`），不重讀檔；`load_paired` 比 32 bytes 原始值；定義＝整個 `kn.sjkn` 的 SHA-256（和 `export_sjtl.py` 的 `hashlib.sha256` 相同）。測試：只差最後一個 byte → `KneserNey`；前 4 bytes 相同 → `KneserNey`；Rust 對 `eval/golden/kn-tiny/kn.sjkn` 的值等於 Python `hashlib`。突變：只比前 8 hex |
| P2-3 | 名單要進 engine 路徑的封頂文字，不能只在 CLI | FIX：名單在 core 共用的 `load_lexicon_packs_detailed` 與 `capping_overlay_detailed` 裡讀，CLI 與 engine 共用。測試：經 `Engine::new_with_packs`＋`load_lm`（或 FFI）斷言合成名單列的分數＝檔案分數 − 1.0（詞彙外）；FFI 缺 `names-add.tsv` 時 `new` 回 3、`load_lm` 回 3。突變：從 `capping_overlay_detailed` 拿掉名單 |
| P3-4 | `ime.OVERLAYS` 加名單會改到建模的變體類與 ΣN′ | FIX：`ime.OVERLAYS` 本身**不在 PR 1 改**（和 Rust 的預設載入、Python 的封頂詞集合、golden 一起在 PR 2）。PR 1 給 `tools/build_lm.py`、`tools/build_classes.py`、`tools/kn_cont.py`、`reference/proto/lm_eval.py` 各加一個預設關的 `--extra-overlay FILE`（把檔案接在 `ime.OVERLAYS` 之後、也併進封頂詞集合，和 Rust CLI 既有的 `--extra-overlay` 同一個規則），不給時輸出逐位元組不變（PR 1 的 CI 與 golden 不用重產）；建置 model-v6 的指令一律帶 `--extra-overlay data/lexicon/names-add.tsv`，完整指令記進 `experiments/model-v6/README.md`；§2.3 的跨語言核對兩邊都帶 `--extra-overlay`；§2.1 的重現核對不帶它；對最終名單算有／無名單的 `variant_classes`，差異寫進研究紀錄 |
| P3-5 | `export_sjtl.py --kn-side` 的版本規則與輸入驗證 | FIX：給了 `--levels` 就寫 v4（沒有 `--kn-side` 時 32 bytes 全 0）；沒給 `--levels` 時 v1／v2 不變；只給 `--kn-side` 是 usage error；`--kn-side` 的檔要是 `SJKN0002` 而且記的模型雜湊等於 `--ngram-model`，寫檔前驗證；`read_sjtl` 讀得了 v4。測試照這四條 |
| P3-6 | Swift 的版本改寫測試在 v4＋KN 時會因錯的原因通過 | FIX：`versionedResources()` 處理真檔是 v4 的情況；有 `kn.sjkn` 時只用 v4 配對，v2／v3 的 Resources 不放 `kn.sjkn`；每個衍生檔先斷言 `rerankEnabled == true` 再斷言強度控制；`Support.swift` 與 `resources(only:)` 清單加 `kn.sjkn`、`names-add.tsv` |
| P3-7 | `check-app.sh` 要核對 `kn.sjkn` 的雜湊；selftest 是 App 裡配對成功的證據 | FIX：`check-app.sh` 比對 `Resources/kn.sjkn` 與 `data/kn.sjkn.sha256`，`names-add.tsv` 加進存在檢查，授權檔列出名單；§3 以帶 KN 的 bundle 上 `--selftest` 通過為「App 裡 model-v6＋kn-v1＋tinylm-v5 配上了」的證據，selftest 的檢查句在新模型上重新確認 |
| P3-8 | 名單常開後，衝突重驗會重複載入或低報 | FIX：§3.3 的衝突重驗在預設載入改動之前，用 `--extra-overlay` 跑（PR 1 的分支上）；N1 的「名單與 `acg-add.tsv` 交集為空」測試保留不可移除 |
| P4-9 | 「(use --no-kn)」對新情況是錯的建議 | FIX：錯誤訊息分三種情況寫（KN 開但側檔不符；KN 關但檔是在 KN 開時調的；舊版檔遇到 KN）；`TinyLmError` 的 Display、`experiments/s7a-tinylm/README.md`、`cli/tests/tinylm.rs` 跟著改 |
| P4-10 | `kn.sjkn`、模型、名單的讀法沒有上限 | 接受：App 只讀簽章過的 bundle；既有行為，這一片不改 |
| P4-11 | SJTL0004 不綁 `classes.sjc`、名單、詞包 | 接受，記錄為刻意的設計（和現行一致） |
| P4-12 | 名單分數可能是 NaN／inf | FIX：測試斷言已提交的 `names-add.tsv` 分數都是有限值、音節數等於字數；`triage.py`、`bench.py` 的 v4／KN 支援另開（發版前的跨版本基準要支援 KN，記在 §3.6） |

