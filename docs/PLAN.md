# 計畫 v4：開源 macOS 注音輸入法「善解」（shanjie）

日期 2026-10-03。v1 經 `pilotfish:plan-verifier`（REVISE，4 項）與 `pilotfish:security-reviewer`（P1×3、P2×5、P3×1）審查；v2 經 fresh `pilotfish:plan-verifier`（REVISE，2 項）審查；v3 經最後一次收尾審查（REVISE，1 項），審查次數已達上限，v4 的修正**未再經審查**，交使用者決定。處置見 §7。研究依據：`~/side-project/ime-research/README.md`、本目錄 `patents.out`、`methods.out`（grok，二手），以及 main 親自核對的事實（§6）。

## 1. Envelope

**目標。** 做一個以「選字正確」為第一優先的 macOS 注音輸入法：送進 App 的文字永遠是本機詞庫 lattice 產生的候選之一；整句轉換看左右文（含應用程式游標同一行的前文）；使用者學習以前文為 key，不因一次改選污染其他句子；選用端上神經驗證與雲端校正，但兩者都只能「選」本機候選，不能產生文字。

**怎麼看到它成功、由誰看。** 使用者在日常 App（Notes、Safari、Slack、VS Code、Terminal）用它取代自然輸入法打字（使用者親自看）；同時 harness 量到下列數字（main 與 verifier 看）。

**v1 驗收（全部成立才算 v1 完成）。**

| ID | 條件 | 量法 |
|---|---|---|
| A1 | 口語評測集整句正確率（句尾計分）：完整端上路徑 ≥ 95%；只有 lattice＋n-gram 的同步路徑 ≥ 85% | 片 E 產出的保留集，只在片結束時由 verifier 跑 |
| A2 | 學習不污染：改選一次冷門詞後，常用詞句 0 退步；同前文的冷門詞句 ≥ 80% 學會 | 片 E 產出的 ≥ 10 組學習案例 |
| A3 | 延遲（M5 Mac）：同步路徑每鍵 p95 < 16 ms；神經驗證 p95 < 300 ms、非同步、不阻塞按鍵 | 核心 bench＋殼內打點 |
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

**模型策略。** 執行期模型不打包進安裝檔，第一次啟用時下載並顯示授權。首選 Gemma 4 E2B（Apache-2.0；HF `gated: False`；口語 109 句實測 96.3%）；備選 Qwen3-1.7B（Apache-2.0）。Llama-3-Taiwan 不用：llama3 授權禁止以其輸出改良其他 LLM，且要求標示「Built with Meta Llama 3」。

**雲端（選用、預設關）。** Claude API，`claude-opus-5-5`、`output_config.effort: "low"`、結構化輸出**只回本機 N-best 的索引**；raw HTTPS（Swift 無官方 SDK）；只在送出或驗證時機送，不逐鍵送；送出內容只有本機 N-best 與截斷後的同一行左文。逾時、拒答（`stop_reason: refusal`）、網路錯誤、索引越界一律退回本機結果。實際延遲與費用未量。

**需要使用者當次同意的外部動作。** 建 GitHub repo、push、發布、簽章／公證、TAIC 申請、每一次付費雲端 API 實測、在 188 上安裝新軟體。

**角色。** main：架構、契約、整合、驗收判斷。`pilotfish:executor`：核心與殼的實作。`pilotfish:mech-executor`：資料轉換、測試搬遷、評測資料。`pilotfish:security-executor`：§7 R1–R9 的實作（privacy gate、日誌規則、Keychain、雲端送出、學習檔儲存、模型下載驗證）。每片結束：fresh `pilotfish:verifier` 對該片驗收條件。UI 實機確認：交給使用者，main 先備好具體動作與預期結果。所有 agent 不得碰使用者的 login keychain（不讀、不寫、不 lock/unlock），測試一律用注入的記憶體 store。

**回滾。** 全部在新 repo `~/side-project/shanjie`；安裝只放 `~/Library/Input Methods/shanjie.app`，刪掉即復原；不讀寫小麥注音、自然輸入法、Apple 注音的任何使用者資料。

**全域停止條件。** 同因失敗 2 次：main 接手或改切法；任一片超過預算 2 倍：暫停回報；驗收數字對不上：不調參數硬湊，先回報差異。

## 2. 切片

依賴：S0 → E → **N0** → S1、S3（S1 與 S3 在資料格式與 C ABI 定好後可平行）→ S2 → S4 → S5 → S6；S7 可延後；S8 最後。片 E 是 N0、S2、S4 的前置（它們的驗收用 E 的資料）。N0 排在最前面，因為「語意選字」是這個專案最大的技術賭注，也是使用者最在意的（2026-10-03：「輸入法根本不瞭解語意」）；N0 不過，就在投入 S1–S4 之前換路線。

### S0（第一個可執行片）：repo 與評測基準

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

### E：評測資料（S2、S4 的前置）

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

### S1：詞庫 v1（台灣讀音、口語詞、新聞偏差修正）

- 以小麥注音原始檔重建詞庫；加輕聲／又讀變體；口語詞補強；新聞偏差詞降權（例：公視 vs 公式／公事）；授權清單 `LICENSES/data.md`。
- 另補商品、日常用品、網路用語詞彙（例：拭鏡布），並量測開發集的詞庫未收率（OOV rate）。
- 驗收：萌典 oracle@32 從 89.7% → ≥ 93%；開發集 unigram 提升，且既有 109 句不退步；開發集 OOV rate 有量測並記錄。
- 擁有者：`pilotfish:mech-executor`（轉換腳本）＋ main（規則與取捨）。

### S2：基礎 n-gram（同步路徑）

- 字／詞 trigram，自寫訓練與讀取（不用 KenLM）；口語合成句由 Gemma 4 E2B 在 188 上產生。
- 驗收：開發集同步路徑 ≥ 85%，片結束時保留集 ≥ 85%；每鍵 p95 < 16 ms；模型檔 ≤ 100 MB。

### 使用者提出的兩個方向（2026-10-03，待排入；排入前要過 plan-verifier）

**P1 詞性連接（併入 S2）。** 同音詞若詞性不同（常常 副詞／嚐嚐 動詞、在／再、的／得／地），用詞性連接分數（Mozc 式 connection cost）或詞類 n-gram 判斷，在本機毫秒等級完成。估算（main 人工標記，不是量測）：開發集 unigram 的 185 次錯誤中 101 次（55%）是詞性不同；144 句錯句中 67 句（47%）的錯誤全是詞性不同，若全部修好，開發集上限 52.0% → 74.3%。其餘 45% 是同詞性（權力／權利、公式／公事），只能靠語意。待解：繁體中文詞性資料的授權（CKIP 為 GPL、UD Chinese-GSD 為 CC BY-SA、平衡語料庫只限學術），以及用 Gemma 4 E2B 或 Jev 標詞性的可行性。

**H 一鍵校正（新切片，S3 之後）。** 打完一段後按快速鍵，對剛送出的文字用大模型做讀音約束重算，顯示差異，Enter 接受、Esc 取消。
- 延遲不受 A3 限制（使用者主動要求），可用雲端 Jev（單題實測約 200 ms）、Claude 或 188 上的大模型。
- 隱私：只有按快速鍵的那段會送出（R1、R7 仍適用：只回候選索引、不回自由文字）。
- 輸入法在記憶體裡保留最近送出句子的注音（不寫磁碟、切換 App 即清），重算用原始注音，不從漢字反推。
- 替換已送出文字用 `insertText:replacementRange:`，替換前確認該段文字未被改動，改過就放棄；終端機與 Electron App 是否支援要實測。
- 接受校正是明確的學習訊號，交給 S4。
- 參考：azooKey-Desktop 的 `azooKeyMacInputController+SelectedTextTransform.swift`。

### S3：IMK 殼 MVP

- 標準注音鍵盤、組字區、候選窗、送出、Shift 中英切換、英數直通完全不暫存；`privacyGate`（R3）；呼叫核心 C ABI。
- 這一片引入 C ABI：每個匯出函式包 `catch_unwind`，panic hook 不印 payload（R2）；左文讀取在最後一個換行截斷、以 grapheme 計數、上限 64（R4）。
- 驗收：XCTest 驅動按鍵狀態機（組字、選字、刪除、送出）與 gate 單元測試；FFI 測試：讓核心在處理標記字串時 panic，C ABI 回傳錯誤碼而不是 abort，且 stderr 與回傳訊息都不含標記（R2）；左文測試：多行、emoji（含代理對與組合字）、超長三種邊界（R4）；日誌行為測試：重播標記字串後 `/usr/bin/log show` 找不到（R2；zsh 內建 `log` 會攔截，必須寫完整路徑）；使用者實測：TextEdit／Notes／Safari 打陷阱集前 10 句，以及 R3 的三處 secure input 情境。

### S4：使用者模型

- 前文 key 用字（≤ 2 字，不用切詞結果）、天級衰減、跨 ≥ 2 種前文才全域化、`max(系統分, 混合分)`、只有打開候選窗改選才學、候選窗一鍵忘記、改選走時舊紀錄減半；儲存依 R5。
- 驗收：A2；gate 生效時 0 筆學習；R5 的清除測試；重播標記字串後學習檔不含該標記（R2）。
- 擁有者：`pilotfish:executor`；儲存與清除由 `pilotfish:security-executor`。

### S5：端上神經驗證器

- **讀音約束的字級搜尋**，不是只重排 N-best：每個音節的全部同音字都是候選（PinyinGPT 的約束解碼＋Zenzai 的草稿→驗證→前綴約束）。依據：「什麼品牌或款式的拭鏡布比較好」的正解在 unigram 512-best 裡都不存在（「拭鏡布」不在詞庫，「拭」在 ㄕˋ 單字排第 21，超過 PER_KEY=12），只重排救不回來。模型在 XPC helper（R8）；MLX-Swift 或 llama.cpp（先量再選）；下載依 R8。
- 先量：M5 上 Gemma 4 E2B 與 Qwen3-1.7B 單次驗證延遲；超過 A3 就改成「只在送出前驗證」或換更小的模型。
- 驗收：A1（端上）、A3、A4；R1 的 mock 測試；片 E 的「詞庫沒收的詞」類在保留集 ≥ 80%。R1 的「只回索引」在這裡的意思是：模型只能在約束集合（每個音節的同音字）裡選，輸出的每個字都必須屬於該音節的讀音表。

### S6：雲端校正（選用）

- `pilotfish:security-executor` 實作 R1、R6、R7；左文截斷沿用 S3 的 R4，不重做；預設關。
- 驗收：payload 稽核測試（denylist 中、gate 生效、超長左文）；R1 mock 測試；延遲與費用實測需使用者同意後才跑。

### S7：自訓字級轉換模型（研究片，可延後）

- 26–100M 字級模型，讀音約束 loss（PinyinGPT 做法），教師 Gemma 4 E2B，權重 Apache-2.0。
- 目標：準確度距 S5 在 2 個百分點內、延遲降 10 倍。

### S8：打包與釋出

- R9；Developer ID 簽章＋公證（Team `2LJ882GPY8`）；README、授權清單。外部動作每一步都要使用者當次同意。

## 3. 風險

| 風險 | 處置 |
|---|---|
| 口語語料沒有可再散布的大資料 | 只訓練不散布；合成句用 Apache-2.0 教師；S2 若達不到 85% 再評估 TAIC |
| 讀音標注偏大陸讀音 | 只用 g2pW 注音權重＋小麥讀音；以萌典評測讀音一致率 |
| 神經驗證延遲 | S5 先量後選；超標就退成送出前驗證 |
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
| R1 | P1 | 雲端只回 N-best 索引；端上驗證器只能從每個音節的同音字集合裡選字（S5 改為字級約束搜尋後的等價規則）；其他一律丟棄；回應綁定組字 session ID，client 或 bundle 換了就丟；mock 測試：含 `\n`、索引越界、遲到回應 → 只送本機結果 | 採納；自由文字列為非目標 | S5、S6 |
| R2 | P1 | 組字、候選、前文、payload 不進任何 log／panic／fatalError；Swift 用 `Logger` 且內容標 `privacy: .private`；FFI `catch_unwind`；行為測試：重播標記字串 → `log show` 與學習檔都找不到 | 採納 | S0（核心錯誤訊息規則＋測試，驗收 5）、S3（`catch_unwind`、FFI panic 測試、日誌行為測試）、S4（學習檔不含標記） |
| R3 | P1 | `privacyGate(bundleID)`：`IsSecureEventInputEnabled()` 或 denylist 就停學習、停雲端、不讀左文；判斷不了就擋；選單顯示暫停狀態；使用者實測 Safari 密碼欄、Terminal 開 Secure Keyboard Entry、Terminal `sudo` | 部分採納：終端機與密碼管理器預設在**雲端** denylist；**學習**在終端機預設開。理由：使用者大量在終端機打中文；只學「注音模式下打開候選窗改選的中文詞」，英數直通（密碼、sudo）不暫存也不學。使用者可改（§4.4） | S3、S4、S6 |
| R4 | P2 | 左文在最後換行截斷、grapheme 計數、只活在記憶體；多行與 emoji 邊界測試 | 採納 | S3（實作＋邊界測試）；S6 只沿用 |
| R5 | P2 | 學習檔放 `~/Library/Application Support/shanjie/`，權限 0600，只存「前文 ≤ 2 字、詞、分數、日期」；一鍵清除含記憶體與附屬檔（SQLite `-wal`、`-shm`）；靠 FileVault 不另加密；設定頁揭露 Time Machine 並提供排除備份 | 採納 | S4 |
| R6 | P2 | Keychain：service＝bundle ID、`SecItemUpdate` 並檢查狀態、key 不進 URL／log／錯誤訊息；agent 測試用記憶體 store；真 Keychain 交給使用者實測；預告 ad-hoc 簽章可能跳授權提示 | 採納 | S6 |
| R7 | P2 | `URLSessionConfiguration.ephemeral`、系統 ATS／TLS、硬逾時 2 秒、只在送出或驗證時送；opt-in 對話框列出送出內容範例與 Anthropic 現行資料保留政策（S6 時核對最新條款） | 採納 | S6 |
| R8 | P2 | 模型清單內建 HF repo＋commit SHA＋每檔 SHA-256，下載到暫存檔驗證後才改名；只收 safetensors／GGUF／JSON；權重在無網路的沙盒 XPC helper 解析 | 採納；XPC 從條件式改成 S5 必做 | S5 |
| R9 | P3 | hardened runtime、不加 `disable-library-validation`、不需 root、v1 不做自動安裝（只檢查版本開瀏覽器） | 採納 | S8 |
