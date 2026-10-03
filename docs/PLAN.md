# 計畫 v5：開源 macOS 注音輸入法「善解」（shanjie）

v5（2026-10-03，N0 之後）。S0、E 完成；N0 未過門檻（§2 N0 結果）；N1 雲端判斷器實測完成。
- 使用者決定：三條路線都排進來，「三層架構、先做詞庫」先做（§4）。
- 本版改動：
  - A1 拆成候選含正解率與判斷挑對率；新增 A7 不拖慢電腦。
  - 模型改用「從候選重排」；雲端改成可切換供應商。
  - S1 寫成完整的可執行片；S5、S6、S7 依實測改寫。
- 審查：v5 送 fresh `pilotfish:plan-verifier` 審 envelope 與 S1。

v4 日期 2026-10-03。v1 經 `pilotfish:plan-verifier`（REVISE，4 項）與 `pilotfish:security-reviewer`（P1×3、P2×5、P3×1）審查；v2 經 fresh `pilotfish:plan-verifier`（REVISE，2 項）審查；v3 經最後一次收尾審查（REVISE，1 項），審查次數已達上限，v4 的修正**未再經審查**，交使用者決定。處置見 §7。研究依據：`~/side-project/ime-research/README.md`、本目錄 `patents.out`、`methods.out`（grok，二手），以及 main 親自核對的事實（§6）。

## 1. Envelope

**目標。** 做一個以「選字正確」為第一優先的 macOS 注音輸入法：送進 App 的文字永遠是本機詞庫 lattice 產生的候選之一；整句轉換看左右文（含應用程式游標同一行的前文）；使用者學習以前文為 key，不因一次改選污染其他句子；選用端上神經驗證與雲端校正，但兩者都只能「選」本機候選，不能產生文字。

**怎麼看到它成功、由誰看。** 使用者在日常 App（Notes、Safari、Slack、VS Code、Terminal）用它取代自然輸入法打字（使用者親自看）；同時 harness 量到下列數字（main 與 verifier 看）。

**v1 驗收（全部成立才算 v1 完成）。**

| ID | 條件 | 量法 |
|---|---|---|
| A1 | 口語評測集整句正確率（句尾計分），拆成兩個可分開歸責的數字（v5，N0 後）：**A1a 候選含正解率**（同步路徑前 64 名候選含正解；使用者 2026-10-03 決定交給判斷器 64 名）≥ 98%，由 S1＋S2 負責；**A1b 判斷挑對率**（正解在候選內的句子裡，重排挑對的比例）≥ 97%，**在端上路徑量，由 S5 負責；v1 完成以端上為準**。雲端（S6，選用）啟用時另報雲端的 A1b，不構成 v1 完成條件。兩者相乘約等於原本的完整端上路徑 ≥ 95%。只有 lattice＋n-gram 的同步路徑 top-1 ≥ 85% | 片 E 產出的保留集，只在片結束時由 verifier 跑 |
| A2 | 學習不污染：改選一次冷門詞後，常用詞句 0 退步；同前文的冷門詞句 ≥ 80% 學會 | 片 E 產出的 ≥ 10 組學習案例 |
| A3 | 延遲（M5 Mac）：同步路徑每鍵 p95 < 16 ms；神經驗證 p95 < 300 ms、非同步、不阻塞按鍵。雲端主力模式（選用，§2 S6）另訂：送出前等待 p95 ≤ 1 秒（使用者 2026-10-03 核定），硬逾時就送本機結果 | 核心 bench＋殼內打點 |
| A7 | 不拖慢電腦（v5，使用者 2026-10-03 在 Mac 量測時回報「電腦很卡」）：本機模型推論期間，按鍵到組字區顯示的 p95 仍 < 16 ms；推論用低 QoS、新按鍵就取消進行中的推論；使用者實測打字時不卡 | 殼內打點＋使用者實測 |
| A4 | 前綴穩定：已鎖定的字（顯示超過一個驗證週期且使用者未回頭改）後續按鍵 0 次被改寫 | 按鍵重播測試 |
| A5 | 隱私：見 §7 的 R1–R9；其中可自動測的全部有測試，需人眼的交給使用者 | 自動測試＋使用者實測清單 |
| A6 | 授權：每份資料、模型都有授權清單，且可合法再散布，或在執行期另行下載並顯示授權 | `LICENSES/` 清單審查 |

**非目標（v1）。** iOS、Windows；拼音、倉頡；縮寫打字／首碼快打（中研院 US8364468B2 有效到 2029、訴訟中）；從使用者指定的檔案或資料夾學習（微軟 US9824085B2 有效到 2032）；下一詞預測；帳號同步；自動更新；中國大陸散布的專利評估；雲端或模型回傳的自由文字。

**已定的架構決策。**

- Rust 核心（lattice、n-gram、使用者模型、驗證協調）＋ Swift InputMethodKit 殼，介面為 C ABI；FFI 邊界一律 `catch_unwind`，panic 訊息不含輸入內容。
- 神經模型（S5）放在沙盒化的 XPC helper（無網路權限）裡載入與推論，不在看得到所有按鍵的主 process 裡解析外部權重；其餘核心先 in-process。
- 左右文：`client.string(from:actualRange:)`，在最後一個換行處截斷，以 grapheme 計數，左側上限 64 字；只存在這一次轉換的記憶體裡。
- 不連結 LGPL 元件（KenLM 是 LGPL）；n-gram 格式自寫。
- 程式碼授權：Apache-2.0（使用者 2026-10-03 確認）。
- 簽章：使用者有 Apple Developer Program（Team `2LJ882GPY8`，本機有 Apple Development 與 Developer ID Application 憑證）。開發期用 Apple Development 簽章（身分固定，Keychain ACL 不會每次重建都跳提示，XPC helper 的沙盒 entitlements 有效）；S8 用 Developer ID＋`notarytool` 公證，公證憑證由使用者自己 `xcrun notarytool store-credentials` 存入鑰匙圈，agent 不碰。

**資料策略。**

| 用途 | 來源 | 授權 | 用法 |
|---|---|---|---|
| 詞庫起點 | 小麥注音 `Source/Data/`（片語源自 libtabe） | MIT／BSD | 可再散布 |
| 單字台灣讀音後備 | Unihan kMandarin 第二值 | Unicode License v3 | 可再散布 |
| 讀音標注（建置期） | g2pW（程式 Apache-2.0、權重以注音訓練） | Apache-2.0 | 只在建置期用，不散布權重 |
| 評測 | 片 E 自建（CC0）；教育部辭典／萌典 | CC0；CC BY-ND 3.0 TW | 萌典只評測、不建詞庫 |
| 語料 | 中文維基（CC BY-SA）、政府開放資料（OGDL）、TAIC（需申請，可訓練不可轉散布）、Gemma 4 E2B 合成的口語句 | 各自 | 只用來訓練，不再散布原文 |

**模型策略。** 執行期模型不打包進安裝檔，第一次啟用時下載並顯示授權。候選：Gemma 4 E2B（Apache-2.0；HF `gated: False`）、Qwen3-1.7B（Apache-2.0）。Llama-3-Taiwan 不用：llama3 授權禁止以其輸出改良其他 LLM，且要求標示「Built with Meta Llama 3」。N0 實測（188、RTX 3070，開發集 302 句）：
- 方法：「從本機 N-best 中重排」（M1）勝過「讀音約束的字級生成」（M2）。Gemma M1 是 91.4%，M2 B8 只有 82.5%；M2 偏好流暢的常用詞，例如把拭鏡布改成試鏡布。
- Mac（M5、MLX 4-bit，2026-10-03）：Qwen M1 p50 487–893 ms，量的時候電腦有其他負載，只能當上限；Gemma 未量。
- 結論：本機層要先在使用者沒在用電腦時重量，並且要做到 A7（不拖慢電腦）。

**雲端（選用、預設關）。** OpenAI 相容的 chat completions 端點，供應商可在設定裡切換，key 存 Keychain（R6）。結構化輸出**只回本機 N-best 的索引**（R1）；raw HTTPS；只在送出、驗證或一鍵校正時送，不逐鍵送；送出內容只有本機 N-best 與截斷後的同一行左文。逾時、拒答、網路錯誤、索引越界一律退回本機結果。N1 實測（2026-10-03，開發集 302 句，k=16，`experiments/providers/`）：
- 最準：OpenRouter gpt-6-luna（effort low）92.7%，p95 3146 ms。
- 1 秒內最準：Cerebras gpt-oss-120b（medium）92.4%，p95 740 ms。
- 最快且約 90%：Cerebras qwen-3.8-27b（none）90.4%，p95 438 ms。
- 所有判斷器都被候選上限 94.7% 卡住。
- Claude 未量（費用考量）；各供應商的資料保留條款在 S6 前核對。

**需要使用者當次同意的外部動作。** 建 GitHub repo、push、發布、簽章／公證、TAIC 申請、每一次付費雲端 API 實測、在 188 上安裝新軟體。

**角色。** main：架構、契約、整合、驗收判斷。`pilotfish:executor`：核心與殼的實作。`pilotfish:mech-executor`：資料轉換、測試搬遷、評測資料。`pilotfish:security-executor`：§7 R1–R9 的實作（privacy gate、日誌規則、Keychain、雲端送出、學習檔儲存、模型下載驗證）。每片結束：fresh `pilotfish:verifier` 對該片驗收條件。UI 實機確認：交給使用者，main 先備好具體動作與預期結果。所有 agent 不得碰使用者的 login keychain（不讀、不寫、不 lock/unlock），測試一律用注入的記憶體 store。

**回滾。** 全部在新 repo `~/side-project/shanjie`；安裝只放 `~/Library/Input Methods/shanjie.app`，刪掉即復原；不讀寫小麥注音、自然輸入法、Apple 注音的任何使用者資料。

**全域停止條件。** 同因失敗 2 次：main 接手或改切法；任一片超過預算 2 倍：暫停回報；驗收數字對不上：不調參數硬湊，先回報差異。

## 2. 切片

依賴（v5）：

- 已完成：S0 → E → N0（未過門檻）→ N1（雲端實測）。
- 三條路線，使用者 2026-10-03 指示「可以同時做，但路線 1 先做」：
  - **路線 1（主線，先做）。** S1（✅ 2026-10-03）→ S2（含 P1）→ S5（本機重排）。L（精簡詞庫格式，✅ 2026-10-03）是 S3 的前提。S3 之後是 H、S4、Q。
  - **S3 與 S1 平行的條件。** 兩項都要成立：
    - main 已寫好 `docs/contracts/abi.md`，並和 S3 一起經使用者核准。
    - 檔案擁有範圍不重疊：S3 只新增 `core/src/ffi.rs`、在 `core/src/lib.rs` 加一行 `pub mod ffi;`，以及 `shell/`；`core/src/lib.rs` 的其餘部分、`core/src/eval.rs`、`cli/` 歸 S1。
    - 平行時兩片分別在不同的 git worktree，由 main 整合。
  - **路線 2（雲端當主力）。** 掛在 S6，需要 S3 的殼與 H 的雲端判斷器管線。S6 的雲端主力模式開工前，要先做一次 `pilotfish:security-reviewer`：每句都送出是新的資料流。
  - **路線 3（S7 自訓小模型）。** 研究性質，在 188 上跑。S1 的詞庫與 S2 的語料定案後才開始，不擋路線 1。
- S8 最後。
- 片 E 是 S1、S2、S4 的前置（它們的驗收用 E 的資料）。

### S0（v4 的第一個可執行片，已完成）：repo 與評測基準

- **前置（全部可檢查）。**
  1. 使用者已定名稱（§4.1）；
  2. 使用者已核准 envelope 與 S0（§4.3）；
  3. main 已 `git init` 新 repo（只在本機），寫好 `docs/contracts/s0.md`（下方契約全文），並把 golden 輸出放在 `eval/golden/unigram.txt`。golden 由 main 在本機用 `proto/.venv/bin/python proto/eval.py`（不帶模型）產生，已於 2026-10-03 在暫存區預跑過：陷阱集 0.641、日常集 0.733、萌典 0.573，`summarize.py` 印出 74/109（67.9%）、萌典 57.3%，與 188 的 `results/` 一致。輸入檔 SHA-256（契約檔內寫完整值）：`data/mcbpmf-data.txt` `0deae7b7c1dcde1d7a30d139e7068543e0c0e7112e944b63eb52947bca1db7ac`、`tests/homophones.txt` `9657119f08c4e3550b247ac082324cde0a1cd916adbf7e7eb17523045e69702c`、`tests/daily.txt` `ccdbb649a51f3a5d5b8916b020782c3d9aba46ebdb275cb7c7f0656b756cc7d7`、`tests/moedict_sample.txt` `5a0e19a89fba49cc08904751877c4d85b4d54999c02b6f0fcf8b8845a6b4f904`。
- **契約（`docs/contracts/s0.md`，語意基準是 `proto/ime.py`＋`proto/eval.py`）。**
  - 詞庫：`data/lexicon/mcbpmf-data.txt`（複製自原型，SHA-256 同上）。格式 `讀音 詞 log10分數`，讀音以 `-` 連接音節；略過 `#` 與 `_` 開頭的行、欄位數 ≠ 3 的行、詞長 ≠ 音節數的行。
  - 搜尋參數：`BEAM=32`、`PER_KEY=12`、詞最長 = 詞庫最長讀音。
  - 讀音產生 `to_syllables`：字元 DP，嚴格 `>` 才取代，詞取「最高分讀音」；轉不出讀音的測試列在評測前剔除（陷阱集因此是 64 句）。
  - 同分規則：每個讀音的詞依分數穩定排序（同分保留檔案順序）；N-best 候選以 surface 字串去重，只有分數嚴格較高才取代，插入順序穩定；取前 BEAM 名是穩定排序。
  - 指標：`n`、`sent_acc`、`lenient_acc`、`char_acc`、`oracle@32`；捨入與 Python `round()` 相同（以二進位浮點值為準的 half-even），位數同原型；字長、切片、`char_acc` 的單位都是 Unicode code point（不是 grapheme）；LENIENT 對照 `她妳它牠嘗周臺裏 → 他你他他嚐週台裡`。
  - CLI：`shanjie-eval [--set trap|daily|moedict|dev|holdout ...] [--learn-sim] [--check-readings]`；測試檔格式 `前文|句子` 或 `前文|句子|讀音`（讀音以空白分隔音節）：有第三欄就直接用、不重算，沒有就照 `to_syllables` 產生；`--check-readings` 列出每一列實際使用的讀音並與第三欄比對，回報不一致列數；`dev`、`holdout` 讀 `eval/dev/`、`eval/holdout/`（S0 時可為空目錄，只要求解析器有測試）；集名對照 `trap → 同音陷阱集`、`daily → 日常驗證集`、`moedict → 萌典例句`；輸出行 `## <中文集名>  unigram  {...}`，`{...}` 必須和 Python `repr(dict)` 逐字相同（單引號、key 順序、`round()` 後的浮點表示），因此也能被 `ast.literal_eval` 解析；學習模擬表格式同 golden。golden 裡還有三件只看原型才知道的格式：miss 行 `   ✗ 正解 → 輸出`（萌典不印）、區塊前的空行、`1.0` 的寫法；契約檔要逐一點名。
  - 學習模擬的兩個副作用照抄：`Promotion.bonus` 在晉升時會寫入 `top`；`dict(by_reading[讀音])[詞]` 遇到同讀音重複詞時取最後一筆。
  - 錯誤訊息（R2）：核心的所有 `Err` 與 panic 訊息不得包含輸入的讀音或文字，只帶錯誤種類與長度。
- **產出。** 新 git repo（只在本機）；Cargo workspace：`core`、`cli`；`eval/` 含三個測試集、學習模擬（四策略）、golden。
- **驗收。**
  1. `shanjie-eval --set trap daily moedict --learn-sim` 的輸出與 `eval/golden/unigram.txt` 逐行相同（`diff` 為空）；
  2. 把輸出餵給 `proto/summarize.py`，印出 unigram 74/109 與萌典 57.3%；
  3. `cargo test` 綠，且 mutation 抽查一次：故意破壞 decode 的同分規則或 beam，測試會失敗（看 exit code 與失敗斷言，不看編譯錯誤）；
  4. release 版每句解碼 p95 < 5 ms（在三個集合合計 409 句上量）；
  5. R2 測試：用一段標記字串觸發核心的錯誤路徑（例如無效音節），斷言回傳的錯誤訊息不含該標記；
  6. 三欄格式的解析器與 `--check-readings` 有單元測試（含一列故意不一致的讀音會被抓到），且加入這些功能後第 1 條的 golden `diff` 仍為空。
- **範圍外。** IMK 殼、n-gram、神經模型、雲端、使用者模型持久化。
- **擁有者。** main：前置 3 與契約；`pilotfish:executor`：實作；fresh `pilotfish:verifier`：驗收。
- **預算。** executor 1 回合，必要時 1 次修正回合。
- **停止。** 對不上時回報第一個不同的句子、兩邊的 N-best 與分數，不調參數。
- **回滾。** 刪 repo。

### E：評測資料（S1、S2、S4 的前置，已完成）

- **產出。**
  1. 口語評測集 ≥ 500 句，切成開發集 300 句與保留集 ≥ 200 句。來源三種：(a) 使用者提供的真實句子（選用、最真實；使用者同意以 CC0 收進 repo，否則只放本機不進 repo）；(b) 由詞庫找出「同讀音、兩個詞都常用」的同音組，fresh agent 依組寫台灣口語句；(c) 使用者遇到的三句（常常／嚐嚐、那邊／納編、「什麼品牌或款式的拭鏡布比較好」）與既有 109 句（只進開發集）；(d) 「詞庫沒收的詞」類 ≥ 50 句（例：拭鏡布），開發集與保留集各半；「沒收」以 S0 的詞庫快照（`data/lexicon/mcbpmf-data.txt`，SHA-256 見 S0）判定並寫進檔案，S1 擴充詞庫後不重新分類。
  2. 學習案例 ≥ 10 組同音詞，每組含示範句、常用詞句、冷門詞句。
  3. 讀音：先用 `to_syllables` 自動產生，含破音字的句子列出清單，由 main 逐句確認；**確認後的讀音寫進檔案**。新格式 `前文|句子|讀音`（讀音以空白分隔音節）。評測 CLI 遇到第三欄時直接使用、不重算；沒有第三欄的舊檔（陷阱集、日常集）維持 S0 的即時產生，以保住與 golden 一致。
  4. 學習案例的每個冷門詞句標記「與示範句同前文」或「不同前文」，A2 的 80% 只算同前文的那些。
  5. `eval/user-reported.txt`：使用者遇到錯字時貼進來的句子（格式同開發集，讀音由 main 確認），每一句自動成為回歸測試，只進開發集；收進 repo 前要使用者同意以 CC0 釋出，不同意就只放本機、不進 repo。
- **保留集規則。** 保留集由一個不參與任何實作的 fresh `pilotfish:mech-executor` 撰寫，放在 `eval/holdout/`；main 記錄其 SHA-256；所有 executor 的 brief 都寫明不得讀取 `eval/holdout/`；只有片結束的 fresh verifier 會跑它。這是流程規則、不是技術防護，會在每片報告裡揭露。
- **驗收。** 句數與切分符合；開發集與保留集每一列都有讀音欄；破音字清單逐句確認完；保留集 SHA-256 已記錄；開發集與保留集零重疊（程式檢查）；用之後任何版本的詞庫跑開發集，CLI 實際使用的讀音與檔案逐列相同（程式比對）。
- **範圍外。** 修改既有 109 句與萌典抽樣；按鍵層級（KySS）語料。
- **擁有者。** main（同音組規則、讀音確認、跑 `--check-readings`）；fresh `pilotfish:mech-executor` ×2（開發集與保留集分開寫，兩者都不寫程式）。E 用到的 CLI 功能（三欄解析、`--set dev|holdout`、`--check-readings`）由 S0 的 executor 實作並驗收，E 不新增程式碼。
- **預算。** 每個 mech-executor 1 回合；main 的讀音確認不超過開發集＋保留集總句數一輪。
- **停止。** 同音組少於 10 組，或使用者提供句子的授權不清楚時，暫停回報。
- **回滾。** 刪 `eval/dev/`、`eval/holdout/`。

### N0：語意解碼可行性（最早驗證最大的賭注）

- **產出。** 核心之外的獨立實驗程式（Python，沿用原型）：讀音約束的字級搜尋（每個音節的全部同音字都是候選），Gemma 4 E2B 打分，左文當條件；與 unigram 草稿＋前綴約束的 Zenzai 式變體比較。
- **驗收。** 開發集整句正確率（句尾計分）與「詞庫沒收的詞」類分開報告；M5 Mac 上單句延遲 p50／p95（Mac 記憶體吃緊時改在 188 量準確度、Mac 只量延遲並記錄條件）；使用者的三句都要列出結果。
- **決策門檻（main 判斷後報使用者）。** 門檻用的延遲是「整句轉換完成的 p95」（字級搜尋一句可能要多次模型呼叫，全部算進去），不是單次呼叫。開發集 ≥ 95% 且整句 p95 ≤ 300 ms：照原計畫。準確度夠但延遲不夠：S5 改成「只在送出前驗證」或優先做 S7 小模型；前者仍受 A3 的 300 ms 限制，若超過要把 A3 的修改提給使用者決定，不自行放寬。準確度不夠：暫停，回報替代路線（更大模型走 188／雲端、或自訓）。
- **範圍外。** IMK 殼、C ABI、持久化。
- **擁有者。** `pilotfish:executor`（實驗程式）；fresh `pilotfish:verifier`（重跑數字）；在 188 上跑模型依記憶 `host-188-gpu`。
- **預算。** executor 1 回合＋1 次修正；GPU 時間 ≤ 2 小時。
- **停止。** 同一設定重跑結果不穩定（兩次差 > 2 句）就停，先查非決定性來源。
- **回滾。** 實驗程式獨立於核心，刪掉即可。
- **結果（2026-10-03，未過門檻）。** 數字在 `experiments/n0/README.md`。
  - 本機最佳是 M1 Gemma：91.4%，p95 848 ms；M1 Qwen 87.1%，p95 171 ms（188、3070、綁 P-core）。
  - 開發集上沒有任何設定同時做到 ≥ 95% 且 p95 ≤ 300 ms。
  - N1 雲端最佳 92.7%，見 §1 雲端。
  - 根因：候選上限。k=16 時正解在候選內的比例（oracle@16）只有 94.7%，漏掉 16 句。16 句全都能用詞庫裡的詞（含單字）拼出來，其中 7 句在前 32 名裡；檢查時沒套用 PER_KEY=12 的限制，所以「拼得出來」不等於「實際網格到得了」。漏掉的 16 句分兩類：
    - 9 句是同音詞排序（舊規定、設壇、身世、這棵樹…），交給 S2。
    - 7 句是詞庫沒收的詞（拭鏡布 ×2、收納盒、防滑墊、密封罐、濾水壺、散熱墊），交給 S1。
  - 使用者決定見 §4。

### S1：詞庫 v1 與候選生成（第一個可執行片，v5；✅ 完成 2026-10-03）

- **結果。** fresh verifier 在 commit 57bd3d5 判定 REFUTED：7 項中 6 項通過，第 3 項的保留集部分沒過。完整紀錄在 `docs/contracts/s1.md`。
  - 保留集的 OOV 子集在沒有疊加層時就已經 25/25 進前 64 名，屬於天花板效應，多救回 0 列。
  - 保留集整體：oracle@64 從 220 到 222（/227），top-1 從 94 到 93。
  - 記憶體：CLI 峰值 RSS 從 127 MiB 到 375 MiB。
  - **使用者決定接受**。保留集指標改成「整體 oracle@64 有疊加層 > 無疊加層」，實際 222 > 220。**這是看過保留集結果之後才改的**，以後引用時要一併揭露。
  - 新增 L（精簡詞庫格式）作為 S3 的前提。
  - 本機競品實測（`footprint`，2026-10-03）：小麥注音 17 MB，自然輸入法 GOING13 131 MB（尖峰 172 MB）。


- **目標。** 讓正解進入同步路徑的前 64 名候選（A1a 的詞庫那一半）。只改候選生成，不碰重排。
- **前置（全部成立）。**
  1. 使用者核准 v5 與 S1。
  2. 使用者在步驟 1 停止條件觸發後做了兩個決定：補詞來源用 CC BY-SA 的維基標題與維基詞典；候選數放寬到 64。日期 2026-10-03。
  3. `docs/contracts/s1.md` 記錄步驟 1 的調查與下方各值。
- **名詞。**
  - **開發集前 302 列。** `eval/dev/*.txt` 依檔名排序（CLI 現行行為），依序為 existing 109、homophones 165、oov 25、user-reported 前 3 列。之後增加的列不算在內，所以這 302 列在 S1 期間固定不變。
  - **OOV 子集。** 該集合中，句子含有該集合 `oov_words.list` 任一詞的列。開發集用 `eval/dev/oov_words.list`，保留集用 `eval/holdout/oov_words.list`。由 CLI 判定；verifier 不開保留集內容。
  - **每鍵延遲。** 每一句對讀音的每個前綴（第 1..n 個音節）各重新解碼一次，模擬每打完一個音節就重算；p95 取全部前綴的計時。
  - **既有 109 句。** 指 `eval/dev/existing.txt`，讀音已確認過；不是 golden 用的 trap＋daily。
- **契約。**
  - **基底不動。** `data/lexicon/mcbpmf-data.txt`（SHA-256 見 S0）原封不動。新詞放在疊加層 `data/lexicon/overlay-add.tsv`，欄位為 `讀音	詞	分數	來源標籤`，讀音以 `-` 連接音節，和基底相同。
    - 疊加層只收基底**沒有的詞**：以詞判斷，不管讀音。核心載入時遇到和基底同讀音同詞的列，就回報錯誤，不靜默覆蓋。所以 S1 不會改動既有詞的分數，同音詞排序交給 S2。
    - **合併規則**（同分很常見，所以必須寫死）：
      1. 先照 S0 解析基底。
      2. 疊加層各列依檔案順序接在同一個讀音清單的後面。
      3. 每個讀音清單做一次穩定排序，分數由高到低；同分時基底在前，疊加層之間照檔案順序。

      疊加層的詞也加進 `by_word`，規則同 S0：分數嚴格較高才取代。這只影響開了疊加層時沒有讀音欄的集合；golden 用 `--no-overlay`，不受影響。參考實作是 `reference/proto/ime.py` 的 `Lexicon(path, overlay=...)`。
    - **檔案格式。**
      - 每列 `讀音\t詞\t分數\t來源標籤\n`，沒有標頭。
      - 依詞的 code point 排序。
      - 分數是 Python `repr(float)` 的字面，例如 `-7.17149945`。
    - `shanjie-eval --no-overlay` 只用基底。`unigram` 行沿用 S0 的搜尋參數，因此 `--no-overlay` 跑 trap、daily、moedict、`--learn-sim` 的輸出必須和 `eval/golden/unigram.txt` 逐行相同。
  - **來源（使用者 2026-10-03 決定，鎖定版本）。**

    | 標籤 | 檔案 | SHA-256 | 授權 | 用途 |
    |---|---|---|---|---|
    | `zhwiki` | `https://dumps.wikimedia.org/zhwiki/20261001/zhwiki-20261001-all-titles-in-ns0.gz` | `016e97bf584196a0eb0522ab9b6300ffccae6fb64d2188bd81a53182a28d6d67` | CC BY-SA 4.0 | 詞 |
    | `wikt` | `https://dumps.wikimedia.org/enwiktionary/20261001/enwiktionary-20261001-all-titles-in-ns0.gz` | `1c840da0ddb78eed78e6f1b6fe613c1a2eced952801fd7d676f4dac9e581d4fd` | CC BY-SA 4.0 | 詞 |
    | `wikt` | `https://dumps.wikimedia.org/zhwiktionary/20261001/zhwiktionary-20261001-all-titles-in-ns0.gz` | `95e915cd85992b4fe990187dca845ea85e257deba39d8054b014781015d3f7f0` | CC BY-SA 4.0 | 詞 |
    | — | OpenCC `data/dictionary/STCharacters.txt`，commit `3ac34aa439a9908dd49fa92b5174b46314787ac2` | `a0ca1601c70648cf48b33c3c6210ccbecc5c7eead4b4c3daf76587ba2c03582b` | Apache-2.0 | 只用來過濾，不進疊加層 |

    三個 dump 的 sha1 已和 Wikimedia 官方的 `sha1sums.txt` 比對相符。來源檔不進 repo，因為合計 55 MB。`tools/build_overlay.py` 下載到 `~/.cache/shanjie/sources/` 後驗證 SHA-256，不符就中止。
  - **篩選。** 由 `tools/build_overlay.py` 依序做，不得手動加減。這支腳本就是本契約的參考實作，下列文字和腳本不一致時以腳本為準：
    1. 純漢字 2–4 字（U+4E00–U+9FFF）。
    2. 不在基底的詞表裡。基底詞表是照 S0 規則解析後 `by_word` 的鍵。
    3. 每個字都是基底的單字詞條。
    4. 不含簡體專用字：在 STCharacters 中，繁體對應清單裡不含它自己的字。例如「干」的對應清單含「干」，所以不算。
    5. 維基詞典（英、中）的標題全收。中文維基的標題只收「複合詞」：去掉第一個字或最後一個字之後，剩下的是基底裡的多字詞。

    複合詞規則是 main 看過開發集 OOV 詞（收納盒、防滑墊…）之後定的。它是一般的構詞規則，不是逐詞挑選，但仍有偏向開發集的風險，由保留集把關，報告時要揭露。**禁止從 `eval/` 的句子挑詞加入**。
  - **讀音。** 用基底的 `to_syllables`：以基底詞切分，取最高分讀音；轉不出讀音的詞略過。不做人工確認，避免人手偏向評測句。`tools/readings.py` 不改。
  - **分數。** 同字數基底詞條分數的第 25 百分位：所有讀音長度為 L 的詞條分數由小到大排序，取 `sorted[len // 4]`。結果是 2 字 `-7.17149945`、3 字 `-7.04116568`、4 字 `-6.60980192`。
  - **來源標籤。** 出現在任一維基詞典的標 `wikt`，其餘標 `zhwiki`。
  - **授權。** `overlay-add.tsv` 以 CC BY-SA 4.0 釋出，署名 Wikipedia 與 Wiktionary 貢獻者，寫進 `LICENSES/data.md`；程式碼仍是 Apache-2.0。
  - **搜尋參數。** S1 路徑 `BEAM = 64`：要 64 名候選，BEAM 至少 64。`PER_KEY = 12`：步驟 1 實測放寬到 24 也沒有差別。S0 的常數保留給 `unigram` 行與 golden。
  - **參考結果。** `reference/proto/ime.py` 載入 commit 的 `overlay-add.tsv`（342,761 列），開發集前 302 列，`decode(beam=64)`，`PER_KEY` 12：
    - oracle@16 為 288/302（基底 286）。
    - oracle@64 為 297/302（基底 295）；漏掉的是第 127、229、275、282、300 列（從 1 起算）。
    - unigram top-1 為 163/302（基底 156）；existing 的 top-1 為 76/109（基底 75）。
    - OOV 子集 @64 為 24/27（基底 22/27）。
  - **評測 CLI。**
    - `unigram` 行格式與參數不變。
    - 只對 `dev`、`holdout` 另印一行 `## <集名>  extra  {'oracle@16': …, 'oracle@64': …, 'oov_n': …, 'oov_sent_acc': …, 'oov_oracle@64': …}`。這行一律用 S1 參數，所以有疊加層和 `--no-overlay` 兩種跑法的差距只來自疊加層。
    - `--limit N`：只對 `dev` 有效，取依檔名排序後的前 N 列。
    - `--bench` 改量每鍵延遲（定義見上）。
    - 開發集另印 oracle@64 漏掉的列，附序號；保留集只印數字、不印句子。
- **步驟與擁有者。**
  1. ✅ main（唯讀）：調查來源，使用者已決定；篩選、讀音、分數已定。調查紀錄在 `docs/contracts/s1.md`。
  2. ✅ main：寫好 `tools/build_overlay.py`（含 `--check`）、原型的 `overlay=` 參數、`data/lexicon/overlay-add.tsv`、`LICENSES/data.md`。
     - 原本排給 mech-executor，改由 main 做：腳本就是契約的參考，和步驟 1 的證據綁在一起。
     - 已驗：`--check` 一致；手動加一列，`--check` 失敗（exit 1）；原型在 commit 的檔案上重現上列參考結果。
  3. `pilotfish:executor`：完成核心的疊加層載入（重複就回報錯誤）、`--no-overlay`、S1 常數、`extra` 行、`--limit`、每鍵 bench，並更新 `core/src/tests.rs`。擁有 `core/src/lib.rs`、`core/src/eval.rs`、`core/src/tests.rs`、`cli/`。
  4. fresh `pilotfish:verifier`：跑驗收，包含保留集。只執行 CLI，不開保留集內容。
- **驗收。**
  1. `--no-overlay` 的 golden `diff` 為空。
  2. `--set dev --limit 302`（n = 302）與參考結果**逐項相同**：
     - oracle@16 288、oracle@64 297。
     - 漏掉的列號 127、229、275、282、300。
     - unigram top-1 163。
     - 不同就停下來，回報第一個不同的列，不調參數。
  3. OOV 子集 oracle@64：
     - 開發集 = 24/27（參考值）。
     - 保留集：verifier 跑 `--set holdout` 兩次，「有疊加層」比「`--no-overlay`」多救回至少 2 列，並報 `oov_n` 與百分點差。門檻 2 列取自開發集的效果，22→24；原本寫的 8 個百分點高於開發集的 +7.4，已修正。
  4. top-1 不退步：`--set dev --limit 109`（只含 existing.txt）的 top-1 ≥ 75/109；前 302 列有疊加層時的 top-1 不低於 `--no-overlay`。
  5. 載入疊加層後，每鍵解碼 p95 < 16 ms（release 版，`--set dev --limit 302 --bench`）；另報詞庫載入時間與記憶體。
  6. 可重現：verifier 跑 `python3 tools/build_overlay.py --check` 得到 exit 0：來源 SHA-256 相符，產出和 commit 的檔案相同。手動多加一列就會變成 exit 1，main 已實測。
  7. `cargo test` 綠；單元測試涵蓋：疊加層和基底同讀音同詞時載入回報錯誤；`--no-overlay` 不載入疊加層。
- **範圍外。** 既有詞分數調整、n-gram 與詞性（S2）；重排模型；使用者詞庫（S4）；萌典衍生資料；C ABI（S3）；`tools/readings.py`。
- **預算。** mech-executor 1 回合＋1 次修正；executor 1 回合＋1 次修正。
- **停止。**
  - 來源下載不到，或 SHA-256 不符（Wikimedia 會下架舊 dump）：中止，回報。
  - 驗收 2 或 3 達不到：不手動補詞或調分數硬湊；回報漏掉的列與原因分類。
- **回滾。** 刪疊加層檔案，或用 `--no-overlay`。

### L：精簡詞庫格式（S3 的前提；使用者 2026-10-03 定為 S2 之前做）

- **結果（✅ 2026-10-03，commit 3f7910f）。** fresh verifier 判 CONFIRMED。
  - 驗收第 1 到 5 項都成立。
  - verifier 另外建置舊版核心（57bd3d5）逐項對照，結果完全相同：21 種錯誤優先順序案例、重複詞的學習加分、311 列完整 N-best 與分數（67 萬行），以及每個詞的 `to_syllables`。
  - 峰值 RSS：有疊加層 88,244,224 bytes（舊版 392,249,344），無疊加層 37,650,432 bytes。
  - 每鍵 p95 中位數 1.651 ms；載入 125–144 ms。


- **目標。** 換掉 S0 為了逐位元組對照原型而用的直白結構，讓詞庫的記憶體降到競品的量級；**純重構，所有輸出逐字不變**。
  - 現行結構是 `HashMap<Vec<String>, Vec<(String, f64)>>` 加上 `HashMap<String, (Vec<String>, f64)>`，每個讀音、每個詞都是獨立配置的 `String`。
  - S1 載入基底＋疊加層後，CLI 峰值 RSS 約 375 MiB；只載基底約 127 MiB。
  - 競品實測：小麥注音 17 MB、自然輸入法 131 MB。
- **前置（全部成立）。**
  1. 使用者同意「過審就開工」（§4）。
  2. main 已在重構前 commit 三個守門測試，golden 碰不到它們描述的行為，名稱也要寫進 `docs/contracts/l.md`：
     - `learner_word_score_takes_last_duplicate`
     - `overlay_duplicate_within_overlay_is_error`
     - `overlay_bad_rows_report_length_only`

     這三個測試的斷言在重構後不得修改。
  3. main 已 commit S1 的完整輸出當對照檔。這三份都在 HEAD 用 release 版產生，重跑兩次逐位元組相同：
     - `eval/golden/s1-dev302.txt`：`--set dev --limit 302`。
     - `eval/golden/s1-dev302-nooverlay.txt`：同上加 `--no-overlay`。
     - `eval/golden/s1-overlay-sets.txt`：`--set trap daily moedict --learn-sim`，有疊加層。
- **契約。**
  - **語意不變。** 下列行為全部照舊，以現行 `core/src/lib.rs` 為準：
    - 解析與合併：行過濾、基底先、疊加層依檔案順序接在後、一次穩定排序、同分保留順序。
    - `by_word` 的嚴格較高才取代。
    - `word_score` 的「同讀音重複詞取最後一筆」。
    - `best_score`、`max_len`。
    - `segment_spans` 的嚴格 `>`。
    - `decode` 與 `decode_beam` 的展開順序、同分處理、截斷。
    - 錯誤種類與訊息（R2：只帶長度）。
  - **分數維持 `f64`。** 不得改成 `f32` 或定點數：路徑分數是許多 log10 相加，精度一變，同分與排序就會變。
  - **只用標準函式庫。** 不加 crate，不用 `unsafe`，不用記憶體映射。資料仍然在執行時從文字檔載入；建置期二進位格式不在 L 範圍。
  - **結構由 executor 決定**，但要寫進 `docs/contracts/l.md`。建議方向：音節字串化成整數 id，詞串進一個共用字串池再用（位移, 長度）引用，各讀音的詞條放在一個連續陣列裡用區間引用。
  - **公開介面。** `Lexicon` 的欄位可以改成私有、改用存取方法。`core/src/tests.rs` 跟著改，但每個既有測試斷言的行為都要保留，不得刪掉測試來讓它通過。
- **步驟與擁有者。**
  1. `pilotfish:executor`：完成重構與 `docs/contracts/l.md`，並在 `cli/tests/` 加一個測試，比對上述三份對照檔。擁有 `core/src/lib.rs`、`core/src/eval.rs`、`core/src/tests.rs`、`cli/`、`docs/contracts/l.md`。
  2. fresh `pilotfish:verifier`：驗收。
- **驗收。**
  1. 舊 golden：`--set trap daily moedict --learn-sim --no-overlay` 和 `eval/golden/unigram.txt` 的 diff 為空。
  2. 三份 S1 對照檔各自用對應的指令重新產生，diff 都為空。`cargo test` 必須涵蓋這三項比對。
  3. 峰值 RSS（`/usr/bin/time -l` 的 maximum resident set size，單位是位元組）：`--set dev --limit 302`（有疊加層）≤ 137,363,456 bytes（131 MiB）；另報 `--no-overlay` 的值。競品的 131 MB 是 `footprint` 量的，和 RSS 不是同一種量法，比較只能當近似。
  4. 每鍵延遲：`--set dev --limit 302 --bench` 跑 3 次取中位數，p95 ≤ 5 ms（S1 實測 2.6–3.9 ms），且遠低於 A3 的 16 ms；另報載入時間。
  5. `cargo test` 綠。既有測試的斷言都還在，只是改用新的存取方式。
- **範圍外。** 任何會改變輸出的改動，包括分數、參數、排序規則；建置期二進位格式與記憶體映射；n-gram（S2）。
- **預算。** executor 1 回合＋1 次修正。
- **停止。**
  - 對照檔出現任何差異：停下來，回報第一個不同的行和原因，不得修改對照檔。
  - 記憶體在 std-only 的限制下降不到 131 MB：回報實測值和瓶頸。
- **回滾。** revert L 的 commit。

### S2：基礎 n-gram（同步路徑）

- 字／詞 trigram，自寫訓練與讀取（不用 KenLM）；口語合成句由 Gemma 4 E2B 在 188 上產生。
- 實驗結果（2026-10-03，`experiments/s2/`、`docs/research-log.md`）：
  - 統一先驗 interp：λ·log10 P(w|v)＋(1−λ)·lp，backoff 用詞庫先驗。
  - 句尾項、疊加層分數以語料頻率為上限。
  - 語料：維基 20 萬篇＋口語 k=5。
- **使用者決定（2026-10-03）：依 App 切換語言模型設定。** 聊天 App 用聊天設定（λ=0.5），其他 App 用書面設定（λ=0.7）；聊天 App 清單可在設定中修改。這需要 S3 殼把前景 App 的 bundle ID 傳給核心（R3 的 privacyGate 本來就要讀這個值）。
- 驗收：開發集同步路徑 ≥ 85%，片結束時保留集 ≥ 85%；**A1a：開發集與保留集 oracle@64 ≥ 98%**（S1＋S2 合計；N0 的 9 句同音詞排序漏掉屬於這片）；每鍵 p95 < 16 ms；模型檔 ≤ 100 MB。P1 詞性連接併入這片（見下）。

### 使用者提出的兩個方向（2026-10-03，待排入；排入前要過 plan-verifier）

**P1 詞性連接（併入 S2）。** 同音詞若詞性不同（常常 副詞／嚐嚐 動詞、在／再、的／得／地），用詞性連接分數（Mozc 式 connection cost）或詞類 n-gram 判斷，在本機毫秒等級完成。估算（main 人工標記，不是量測）：開發集 unigram 的 185 次錯誤中 101 次（55%）是詞性不同；144 句錯句中 67 句（47%）的錯誤全是詞性不同，若全部修好，開發集上限 52.0% → 74.3%。其餘 45% 是同詞性（權力／權利、公式／公事），只能靠語意。待解：繁體中文詞性資料的授權（CKIP 為 GPL、UD Chinese-GSD 為 CC BY-SA、平衡語料庫只限學術），以及用 Gemma 4 E2B 或 Jev 標詞性的可行性。

**H 一鍵校正（新切片，S3 之後）。** 打完一段後按快速鍵，對剛送出的文字用大模型做讀音約束重算，顯示差異，Enter 接受、Esc 取消。
- 延遲不受 A3 限制（使用者主動要求）。判斷器依 N1 實測：預設 gpt-6-luna（OpenRouter，92.7%）；可換成其他 OpenAI 相容供應商或 188。Jev 只有 81%，不用。
- 隱私：只有按快速鍵的那段會送出（R1、R7 仍適用：只回候選索引、不回自由文字）。
- 輸入法在記憶體裡保留最近送出句子的注音（不寫磁碟、切換 App 即清），重算用原始注音，不從漢字反推。
- 替換已送出文字用 `insertText:replacementRange:`，替換前確認該段文字未被改動，改過就放棄；終端機與 Electron App 是否支援要實測。
- 接受校正是明確的學習訊號，交給 S4。
- 參考：azooKey-Desktop 的 `azooKeyMacInputController+SelectedTextTransform.swift`。

**Q 新手測驗與使用者設定檔（新切片，S3 之後、與 S4 並行）。** 第一次啟用時做簡易測驗，了解使用者常聊的領域與打字習慣。
- 三段：(1) 常聊領域（多選：軟體／程式、醫療、法律、財經、學校、遊戲…）→ 領域詞加權，並作為模型的使用者簡介（Zenzai v3 的 profile 前例）；(2) 實際打 10–20 句準備好的測驗句，記錄使用者真正按的注音（和 ㄏㄢˋ／ㄏㄜˊ、一 的本調／變調、輕聲、ㄣ／ㄥ 與 ㄓ／ㄗ 是否混用）→ 讀音變體與模糊音設定；(3) 用字偏好（台／臺、裡／裏、週／周、念／唸、嚐／嘗、妳）→ 異體字預設。
- 隱私：結果只存本機（R5 規則）；雲端功能要用簡介時另外取得同意，且只送領域標籤。
- 專利：不做「匯入使用者文件學習」（微軟 US9824085B2，見非目標）；測驗只用專案準備的句子與使用者選的領域。
- 驗收（初稿）：有／無簡介在開發集領域子集上的正確率差異；使用者在測驗中的讀音都能被正確轉換。
- 觀察：使用者的 macOS 注音把 14 個專業詞庫全開，冷僻詞會搶常用詞；測驗挑領域的精準度應優於全開。
- 領域詞包來源（使用者 2026-10-03 提供）：國家教育研究院「樂詞網」（https://terms.naer.edu.tw/ ），學術名詞約 193 萬則（206 類）、雙語詞彙約 2 萬則，各類可單獨下載（ODS）。
  - 授權：「政府網站資料開放宣告」允許無償重製、改作、散布、開發產品，須註明出處。但同站的隱私權聲明另寫「任何形式之轉載，請先與本院聯繫」，兩份文件互相矛盾，正式散布前要寄信確認。
  - 實測「電子計算機名詞」：12 萬個漢字名詞，其中 11.2 萬個不在善解詞庫裡；收了「語言模型」「回傳」「多模態」，但沒有「提示詞」「向量資料庫」「檢索增強生成」「智慧代理」「持續整合」。
  - 用法：只在使用者選了對應領域時才載入，預設不開，避免冷僻術語污染日常候選。維基標題全收（V3）比只收複合詞（V2）差，就是這種污染。

### S3：IMK 殼 MVP

- 標準注音鍵盤、組字區、候選窗、送出、Shift 中英切換、英數直通完全不暫存；`privacyGate`（R3）；呼叫核心 C ABI。
- 外觀（使用者 2026-10-03）：參考 Apple 原生注音的介面與 Liquid Glass。優先用系統元件（先試 IMK 內建的 `IMKCandidates`，不夠再用 macOS 的玻璃效果元件自建視窗），動畫用系統預設、不自訂（使用者嫌自訂的 Liquid Glass 行為「太 Q」）。開工前請使用者提供原生注音候選窗的實際截圖當參考，不從程式碼推測外觀。
- 按鍵（使用者 2026-10-03）：中文模式下 Ctrl+\ 輸出「、」（照字面實作）。其餘標點行為參考原生注音；Apple 系統檔 `CoreChineseEngine.framework/.../CIMPunctuationCandidates.plist` 顯示「、」的替代候選為 `\`、`＼`、`｜`，推測原生注音的反斜線鍵輸出「、」（未在介面實測）。對照表由本專案自行定義，不複製 Apple 的資料檔（著作權）。
- 這一片引入 C ABI：每個匯出函式包 `catch_unwind`，panic hook 不印 payload（R2）；左文讀取在最後一個換行截斷、以 grapheme 計數、上限 64（R4）。
- 驗收：XCTest 驅動按鍵狀態機（組字、選字、刪除、送出）與 gate 單元測試；FFI 測試：讓核心在處理標記字串時 panic，C ABI 回傳錯誤碼而不是 abort，且 stderr 與回傳訊息都不含標記（R2）；左文測試：多行、emoji（含代理對與組合字）、超長三種邊界（R4）；日誌行為測試：重播標記字串後 `/usr/bin/log show` 找不到（R2；zsh 內建 `log` 會攔截，必須寫完整路徑）；使用者實測：TextEdit／Notes／Safari 打陷阱集前 10 句，以及 R3 的三處 secure input 情境。

### S4：使用者模型

- 前文 key 用字（≤ 2 字，不用切詞結果）、天級衰減、跨 ≥ 2 種前文才全域化、`max(系統分, 混合分)`、只有打開候選窗改選才學、候選窗一鍵忘記、改選走時舊紀錄減半；儲存依 R5。
- 驗收：A2；gate 生效時 0 筆學習；R5 的清除測試；重播標記字串後學習檔不含該標記（R2）。
- 擁有者：`pilotfish:executor`；儲存與清除由 `pilotfish:security-executor`。

### S5：端上重排（v5 改寫）

- **從本機 N-best 重排**（N0 的 M1），不做字級生成。N0 實測 M1 勝過 M2：Gemma 91.4% 對 82.5%，M2 偏好流暢的常用詞。v4 寫的字級搜尋是為了救「拭鏡布」這種詞庫沒收的詞，現在改由 S1 補詞處理。
- 模型在 XPC helper（R8）；推論引擎用 MLX-Swift 或 Core ML／ANE（先量再選）；下載依 R8。
- **先量，在使用者沒在用電腦時、經使用者同意。** 2026-10-03 的 Mac 量測（Qwen 4-bit，p50 487–893 ms）是在電腦很卡時量的，不能當定論。要量的項目：
  - 同一句的候選共用前文的 KV 快取，只算候選本身。
  - 候選數 8／16／32。
  - Qwen3-1.7B 與 Gemma 4 E2B。
  - GPU（MLX）與 ANE（Core ML）。
  - 推論時的 A7：用一個按鍵重播程式同時量按鍵延遲。
  - 超過 A3 就改成「只在送出前重排」，或換更小的模型，或走 S7。
- 驗收：A1b ≥ 97%（端上）、A3、A4、A7；R1 的 mock 測試：模型只能回本機候選的索引。

### S6：雲端（選用、預設關；路線 2 在這裡）

- `pilotfish:security-executor` 實作 R1、R6、R7；左文截斷沿用 S3 的 R4，不重做；供應商可切換（OpenAI 相容端點）。
- 兩種模式：
  - **一鍵校正。** 給 H 用，延遲不受限。N1 預設最準的 gpt-6-luna（OpenRouter，effort low）。
  - **雲端主力模式（路線 2）。** 每次送出前把 N-best 送雲端判斷，等待硬逾時就送本機結果。預設 Cerebras gpt-oss-120b（effort medium），92.4%，p95 740 ms；要更快可換 Cerebras qwen-3.8-27b（effort none），p95 438 ms。
- 開工前提：
  - 雲端主力模式每句都送出，是 v4 沒審過的資料流，要先做一次 `pilotfish:security-reviewer`，再寫 S6 契約。
  - 核對所選供應商的資料保留與訓練使用條款，寫進 opt-in 對話框（R7）。
- 驗收：
  - payload 稽核測試：denylist 中、gate 生效、超長左文。
  - R1 mock 測試。
  - 雲端主力模式的 A3 另訂值：p95 ≤ 1 秒，逾時後送出本機結果。硬逾時的值在 S6 契約定，不超過 R7 的 2 秒。
  - 雲端的 A1b 另報，不構成 v1 完成條件（見 A1）。
  - 延遲與費用實測需使用者同意後才跑。

### S7：自訓小模型（研究片；路線 3，與路線 1 平行，在 188 上）

- 26–100M 的重排或轉換模型，教師是 N1／N0 最準的判斷器；權重 Apache-2.0；訓練語料依資料策略，只訓練、不散布原文。
- 開工條件：S1 詞庫與 S2 語料定案，候選生成才穩定。不擋路線 1。
- 目標：判斷挑對率（A1b）距 S5 在 2 個百分點內，Mac 上延遲 p95 ≤ 50 ms，且滿足 A7。

### S8：打包與釋出

- R9；Developer ID 簽章＋公證（Team `2LJ882GPY8`）；README、授權清單。外部動作每一步都要使用者當次同意。

## 3. 風險

| 風險 | 處置 |
|---|---|
| 口語語料沒有可再散布的大資料 | 只訓練不散布；合成句用 Apache-2.0 教師；S2 若達不到 85% 再評估 TAIC |
| 讀音標注偏大陸讀音 | 只用 g2pW 注音權重＋小麥讀音；以萌典評測讀音一致率 |
| 神經驗證延遲 | S5 先量後選；超標就退成送出前驗證；路線 3（S7）是更小模型的後備 |
| 本機模型拖慢整台電腦（2026-10-03 實際發生） | A7；低 QoS、新按鍵就取消；Mac 量測經使用者同意並挑時間 |
| 64 名候選讓判斷器更容易挑錯（N1／N0 是用 16／32 名量的） | S5、S6 量 A1b 時改用 64 名，必要時再決定判斷器實際看幾名 |
| 疊加層以 CC BY-SA 釋出（share-alike） | 只限 `overlay-add.tsv` 這個檔；程式碼仍是 Apache-2.0；`LICENSES/data.md` 註明署名 |
| 詞庫補詞過度擬合評測集 | S1 禁止從 `eval/` 挑詞、每筆有來源標籤；保留集由 verifier 量 |
| 輸入法看得到所有按鍵 | §7 R1–R9 |
| 專利 | §6；不是法律意見；若改成商業產品要做 FTO |
| 保留集被看過 | 片 E 的流程規則；每片報告揭露 |

## 4. 使用者決定（2026-10-03）

| 項目 | 決定 |
|---|---|
| 名稱 | 善解 shanjie；bundle ID `com.nyanako.inputmethod.shanjie`；GitHub、網路、Homebrew 查無撞名，TIPO 商標未查 |
| 授權 | Apache-2.0 |
| 核准 | 核准 v4（含未再審的最後修正）；AUTO 執行 S0 → E → N0，含本機 git commit，不 push、不建 GitHub repo；N0 決策門檻停下報告 |
| 終端機 | 學習預設開；雲端不從終端機送出 |
| N0 量測位置 | 使用者先前指示模型丟 188 跑（Mac 記憶體不夠），N0 準確度與延遲都在 188 量；Mac 實測延遲在門檻時另問 |
| N0 之後的路線（v5） | 「看起來可以同時做啊，但 1 先做」：三條路線都排入，路線 1（三層架構、先做詞庫）先做；路線 2 為 S6 的雲端主力模式；路線 3 為 S7 |
| Mac 延遲量測 | 同意「只量延遲」；量測中使用者回報「電腦很卡」，已停。之後 Mac 上的量測先問時間，在使用者沒在用電腦時跑（S5） |
| 雲端實測（N1） | 同意總預算 US$5、只跑便宜模型；花了約 US$2.24（部分為估算，見 `experiments/providers/README.md`） |
| 雲端主力模式的延遲 | p95 ≤ 1 秒（使用者選「寬一點」）；預設的本機路徑仍是 300 ms |
| S1 補詞來源與候選數 | 步驟 1 的停止條件觸發（可再散布來源都不到一半）後，使用者選：補詞用 CC BY-SA 的中文維基標題與維基詞典；判斷器的候選從 16 名放寬到 64 名 |
| S1 驗收沒過之後 | 使用者先問競品的記憶體用量；main 實測小麥注音 17 MB、自然輸入法 131 MB 之後，使用者選「接受，加精簡格式」：S1 完成，保留集指標事後改成整體 oracle@64（已揭露），新增 L |
| S3 操作習慣（2026-10-03） | 選字鍵跟 macOS 內建注音一樣；中英切換用 Caps Lock；第一版支援標準與倚天兩種鍵盤排列；S3 和 S2 的判別式重排實驗同步進行。外觀以使用者提供的內建注音截圖為準 |
| L 與 S2 | L 先、S2 後（兩片都改 `core/src/lib.rs`，不平行）；契約通過 fresh `pilotfish:plan-verifier` 就以 AUTO 開工（只在本機 commit、不 push）。遇到停止條件、驗收不過、要裝軟體或要花錢時，停下來問使用者 |
| 核准（v5） | 核准 v5 與 S1（含未再審的收尾修正）；S1 以 AUTO 執行：只在本機 commit、不 push；可再散布來源不夠、驗收達不到、或要裝新軟體時停下來問 |

原本的待決清單：

1. 名稱（repo 與 bundle ID）。
2. 程式碼授權：Apache-2.0（建議，含專利授權）或 MIT。
3. 核准 envelope、片 E 與 S0。
4. 終端機的預設：雲端一律不送（已定）；學習預設開（建議，見 §7 R3 的理由）或預設關。

## 5. 研究重點

- 選字錯誤分兩種：學習策略污染（嚐嚐）、詞庫／語料偏差（納編）。
- 11 個模型實測（188、RTX 3070）：口語 109 句句尾計分，Gemma 4 E2B 96.3%、Llama-3-Taiwan-8B 96.3%、Gemma 3 4B 93.6%、unigram 67.9%。
- 學習模擬：全域置頂改選一次壞 5/7；前文 key 0 退步。

## 6. main 親自核對的事實

| 項目 | 結果 | 出處 |
|---|---|---|
| Google「Contextual input method」台灣案 TWI475406B | 2016-12-01 未繳費失效 | Google Patents |
| 同族美國案 US8028230B2 | 未繳費失效（2019） | Google Patents |
| 微軟依前文調適台灣案 TWI484476B | 2019-02-11 未繳費失效 | Google Patents |
| Gemma 4 E2B／E4B 授權 | Apache-2.0（另有 prohibited use policy）；HF `gated: False` | HF API＋ai.google.dev |
| Llama 3 授權 | 禁止以輸出改良其他 LLM；須標示「Built with Meta Llama 3」 | dev.meta.ai |
| KenLM | LGPL | GitHub LICENSE |
| 小麥注音 | MIT；片語源自 libtabe（BSD） | repo LICENSE、Source/Data/README.md |
| S0 golden | 本機預跑與 188 結果一致（74/109、57.3%） | 本目錄 `golden.txt` |

## 7. 審查處置

### plan-verifier（v1：REVISE）

| # | 問題 | 處置 |
|---|---|---|
| 1 | S0 契約缺語意（beam、同分、剔除、指標、集名、golden） | FIX：§2 S0 契約逐條寫出；golden 已在本機預跑 |
| 2 | ≥ 500 句與 ≥ 10 組學習案例沒有擁有者 | FIX：新增片 E，S2、S4 依賴它 |
| 3 | 缺 security review | FIX：本節 security-reviewer 處置 |
| 4 | S0 缺前置 | FIX：S0 前置三項 |

### plan-verifier（v2：REVISE，第二次自動 REVISE）

| # | 問題 | 處置 |
|---|---|---|
| 1 | 片 E 確認過的讀音沒有落地，評測輸入會隨詞庫變 | FIX：新格式 `前文\|句子\|讀音`，CLI 有第三欄就不重算；E 驗收加讀音欄與逐列比對 |
| 2 | R2、R4 的落點在切片裡沒有規則與測試 | FIX：S0 契約加錯誤訊息規則與驗收 5；S3 加 `catch_unwind`、FFI panic 測試、左文邊界測試；S6 不再宣稱實作 R4 |
| 非阻擋 | git init 擁有者、完整 SHA、`round()` 語意、code point 單位、golden 格式細節、學習模擬副作用、E 的預算／停止／回滾、A2 同前文標記、p95 量測集合 | 全部 FIX（見 S0 前置與契約、片 E） |

### plan-verifier（v3：REVISE，最後一次收尾審查）

| # | 問題 | 處置 |
|---|---|---|
| 1 | E 要求的 CLI 功能（三欄讀音、dev／holdout、讀音比對）沒有擁有者 | FIX：併入 S0 契約與驗收 6，由 S0 executor 實作；golden diff 維持為空 |
| 非阻擋 | N0 門檻用哪個延遲；user-reported 的 CC0 同意；S4 驗收缺 R2 標記測試；OOV 用哪版詞庫判定 | 全部 FIX |

審查次數已達上限（v1、v2、v3 各一次），v4 未再經審查。

### security-reviewer（v1：無 P0）

| ID | 等級 | 設計規則／測試 | 處置 | 落在哪片 |
|---|---|---|---|---|
| R1 | P1 | 雲端與端上重排都只回本機 N-best 的索引（v5：S5 改回 N-best 重排，字級規則不再需要）；其他一律丟棄；回應綁定組字 session ID，client 或 bundle 換了就丟；mock 測試：含 `\n`、索引越界、遲到回應 → 只送本機結果 | 採納；自由文字列為非目標 | S5、S6 |
| R2 | P1 | 組字、候選、前文、payload 不進任何 log／panic／fatalError；Swift 用 `Logger` 且內容標 `privacy: .private`；FFI `catch_unwind`；行為測試：重播標記字串 → `log show` 與學習檔都找不到 | 採納 | S0（核心錯誤訊息規則＋測試，驗收 5）、S3（`catch_unwind`、FFI panic 測試、日誌行為測試）、S4（學習檔不含標記） |
| R3 | P1 | `privacyGate(bundleID)`：`IsSecureEventInputEnabled()` 或 denylist 就停學習、停雲端、不讀左文；判斷不了就擋；選單顯示暫停狀態；使用者實測 Safari 密碼欄、Terminal 開 Secure Keyboard Entry、Terminal `sudo` | 部分採納：終端機與密碼管理器預設在**雲端** denylist；**學習**在終端機預設開。理由：使用者大量在終端機打中文；只學「注音模式下打開候選窗改選的中文詞」，英數直通（密碼、sudo）不暫存也不學。使用者可改（§4.4） | S3、S4、S6 |
| R4 | P2 | 左文在最後換行截斷、grapheme 計數、只活在記憶體；多行與 emoji 邊界測試 | 採納 | S3（實作＋邊界測試）；S6 只沿用 |
| R5 | P2 | 學習檔放 `~/Library/Application Support/shanjie/`，權限 0600，只存「前文 ≤ 2 字、詞、分數、日期」；一鍵清除含記憶體與附屬檔（SQLite `-wal`、`-shm`）；靠 FileVault 不另加密；設定頁揭露 Time Machine 並提供排除備份 | 採納 | S4 |
| R6 | P2 | Keychain：service＝bundle ID、`SecItemUpdate` 並檢查狀態、key 不進 URL／log／錯誤訊息；agent 測試用記憶體 store；真 Keychain 交給使用者實測；預告 ad-hoc 簽章可能跳授權提示 | 採納 | S6 |
| R7 | P2 | `URLSessionConfiguration.ephemeral`、系統 ATS／TLS、硬逾時 2 秒、只在送出或驗證時送；opt-in 對話框列出送出內容範例與所選供應商現行的資料保留政策（v5：供應商可切換；S6 時逐家核對最新條款） | 採納 | S6 |
| R8 | P2 | 模型清單內建 HF repo＋commit SHA＋每檔 SHA-256，下載到暫存檔驗證後才改名；只收 safetensors／GGUF／JSON；權重在無網路的沙盒 XPC helper 解析 | 採納；XPC 從條件式改成 S5 必做 | S5 |
| R9 | P3 | hardened runtime、不加 `disable-library-validation`、不需 root、v1 不做自動安裝（只檢查版本開瀏覽器） | 採納 | S8 |

### plan-verifier（v5 第一次：REVISE）

審查範圍：v5 envelope＋S1。5 項阻擋，main 逐項核對 `cli/src/main.rs`、`eval/README.md`、`tools/readings.py`，證據都屬實。

| # | 問題 | 處置 |
|---|---|---|
| 1 | 保留集 OOV 子集量不出來（CLI 只印整集一行），verifier 只能違規讀保留集 | FIX：以各集合的 `oov_words.list` 定義 OOV 子集，CLI 對開發集與保留集都印子集 `oracle@16`，有／無疊加層各一次，保留集只印數字 |
| 2 | `overlay-adjust.tsv` 沒有分數來源規則，可以手調湊數字 | FIX：S1 拿掉既有詞分數調整，交給 S2 |
| 3 | 「追回原始檔」的檢查只核對標籤，擋不住手動補詞 | FIX：來源檔 SHA-256＋`tools/build_overlay.py`；verifier 重新產生後 `diff` 為空；手動多加一列就必須失敗 |
| 4 | A1b 的負責片與路徑在 A1、S5、S6 三處不一致 | FIX：A1b 在端上量、由 S5 負責、v1 以端上為準；雲端的 A1b 另報 |
| 5 | S3 與 S1 平行的前提沒有落點，`core` 的檔案所有權重疊 | FIX：前提改成 `docs/contracts/abi.md` 存在並經核准；S3 只新增 `ffi.rs`、`pub mod ffi;`、`shell/`；平行時用不同 worktree |
| 非阻擋 | 每鍵延遲量法、302 列定義、「既有 109 句」指哪個檔、預設分數何時補進契約、`readings.py` 無條件載入萌典與 repo 外路徑、E／S0 標題、雲端硬逾時值 | 全部 FIX（S1「名詞」與契約、S6 驗收、標題） |

### plan-verifier（v5 收尾審查：REVISE）

前次五項：#1、#3、#4、#5 關閉；#2 只關了一半，見下表第 1 項。另外抓到兩項是修訂引起的。main 核對了 `core/src/eval.rs:70` 的 `repr` 固定鍵、`core/src/tests.rs:58,74` 寫死的常數、`cli/src/main.rs` 串接開發集的方式，證據都屬實。

| # | 問題 | 處置 |
|---|---|---|
| 1 | 合併規則「同讀音同詞取疊加層分數」等於還有一條改既有詞分數的路 | FIX：疊加層只收基底沒有的組合；`build_overlay.py` 濾掉重複；核心遇到重複就回報錯誤；單元測試跟著改 |
| 2 | 新指標加進 `repr` 會讓 `--no-overlay` 的 golden diff 不為空 | FIX：新指標只對 dev／holdout 另印一行 `extra`，既有行不變 |
| 3 | 沒有機制只量「前 302 列」 | FIX：新增 `--limit N`（只對 dev），驗收寫明 `--set dev --limit 302`，n 必須是 302；漏掉的列附序號 |
| 非阻擋 | 保留集差距的方向與 `oov_n`；參數和疊加層效果混在一起；`tests.rs` 的擁有者；CHECK 確認的步驟；`build_overlay.py` 要讀 `reading_overrides.tsv`；來源檔放在哪裡 | 全部 FIX |

這一輪已經是第二次 REVISE，而且收尾審查也是 REVISE，依審查規則不再自動送審。修正由 main 依審查者給的最小修改做完，**未再經審查**，交使用者決定核准。

### plan-verifier（S1 改寫後：REVISE）

S1 依使用者決定改寫後，再送一次 fresh 審查。兩項阻擋都屬實，用審查者的最小修改 (a)＋(b) 修掉；之後由使用者說「繼續」，不再送審。

| # | 問題 | 處置 |
|---|---|---|
| 1 | 疊加層合併進詞庫的順序沒定，同分必然存在，核心可能合理地算出和原型不同的 oracle | FIX：寫死合併規則（基底先、疊加層依檔案順序接在後、一次穩定排序、同分基底在前、也進 `by_word`）；原型加 `overlay=` 參數作為參考 |
| 2 | 參考數字來自沒 commit 的腳本，疊加層內容與順序無法重現 | FIX：`tools/build_overlay.py` 由 main 寫好並 commit，作為參考實作；寫明列順序、過濾規則 4 的語意、基底詞表的定義、分數格式；在 commit 的檔案上重現參考值，並寫下漏掉的列號 |
| 非阻擋 | 保留集 8 個百分點高於開發集的 +7.4；existing 的 top-1 怎麼量；疊加層要另寫解析器；`extra` 行的小數位數 | 門檻改成「多救回 ≥ 2 列」；改用 `--limit 109`；其餘留給 executor |

### verifier（S1 收尾：REFUTED）

| # | 發現 | 處置 |
|---|---|---|
| F1（P2） | 保留集 OOV 子集多救回 0 列（要求 ≥ 2）；天花板效應，無疊加層時已是 25/25 | 使用者決定接受；指標事後改成整體 oracle@64（220 → 222），已揭露 |
| A1（P3） | 保留集 top-1 少 1 列（94 → 93） | 記錄；S2 的 n-gram 要重新量 top-1 |
| A2（P3） | 記憶體約 3 倍（127 → 375 MiB） | 新增 L（精簡詞庫格式），作為 S3 的前提 |
| A3（P4） | `no_overlay_parse_is_identical` 是同一條路徑自己比自己，攔不到任何東西 | 刪除；真正的防護是 golden CLI 測試 |
