# 「幀」的口語讀音 ㄓㄣ、ㄓㄥ（S2r 規則 R7）

狀態：修訂二，plan-verifier 收尾審查 READY（2026-10-31）。plan-verifier 第一次 REVISE 三項（評測句的詞、授權歸屬、建置指令）照最小修改處理；第二次 REVISE 兩項（檔尾重算的冪等、s2r §0 的例外文字）逐項處置為修，送收尾審查。

## 0. 起因與決定

- 錯字回報第 175 列（`|其實兩個都會卡幀數不到|ㄑㄧˊ ㄕˊ ㄌㄧㄤˇ ㄍㄜˋ ㄉㄡ ㄏㄨㄟˋ ㄎㄚˇ ㄓㄣ ㄕㄨˋ ㄅㄨˋ ㄉㄠˋ`，main `6b9ec674`）：維護者打「幀數」得到「真數」，並說 ㄓㄣ、ㄓㄥ 兩種打法都找不到「幀」。
- 詞庫裡「幀」與所有含「幀」的詞都只收 ㄓㄥˋ（基底 6 列、疊加層 11 列）。2026-10-31 用 CLI 在聊天設定量到：
  - ㄓㄥˋ ㄕㄨˋ：「幀數」排第 5，由「幀」＋「數」拼出；
  - ㄓㄣ ㄕㄨˋ、ㄓㄥ ㄕㄨˋ：候選裡沒有「幀」。
- 維護者 2026-10-31 選「加 ㄓㄣ 讀音，連常用詞一起（推薦）」，並說兩種打法都找不到，所以 ㄓㄣ、ㄓㄥ 都收。「常用詞」是詞庫裡已有、含「幀」的詞：幀率、掉幀、關鍵幀、幀速率等。
- **不加新詞**：專案規則禁止從評測句挑詞加進詞庫（`docs/methodology.md`、`docs/PLAN.md`），所以不收第 175 列裡的「幀數」。「幀」單字有了 ㄓㄣ、ㄓㄥ 之後，「幀」＋「數」兩個字照舊分開選：實作後 CLI 在聊天設定量到，`ㄓㄥ ㄕㄨˋ` 的整句候選第 1 名是「幀數」（由「幀」＋「數」拼出），`ㄓㄣ ㄕㄨˋ` 的前 64 名裡沒有「幀」開頭的候選（CLI 的整句候選池很小，真引擎的候選窗可以逐字改選，見下）。
- **和 S2r 的關係**：照 `zha-reading.md` §0 放寬的那一類收：**口語通行讀音**，指審訂音以外、但候選裡完全打不出那個字的讀音，經維護者逐字同意後收。和 R6 不同的地方是，「幀」的常用詞都在疊加層，所以疊加層的詞也處理。為了授權一致，結果分兩個檔存（§1）。

**怎麼看到它有效**：
- 打 `ㄓㄣ` 或 `ㄓㄥ` 時，候選有「幀」；打 `ㄓㄣ ㄌㄩˋ` 時，候選有「幀率」。
- 錯字回報第 175 列：預期的 oracle@64 由 0 變 1 沒有成立，照實量到的是：CLI 的 oracle@64 兩種設定都仍是 0、top1 仍是「真數」（聊天）與「偵數」（書面），golden 不變；真引擎重播（`shanjie-replay --correct`）則從「不能改成正解」變成「可以用改選改成正解」（unfixable 1 → 0）。這一列的 top1 沒有進步，是否算改對留給 §3 的評測。
- 其他結果照出貨標準不退步（§3）。

## 1. 規則與資料

- **共同規則**：詞含「幀」、而且那個「幀」讀 ㄓㄥˋ：把這些位置改成 ㄓㄣ 產生一列，改成 ㄓㄥ 再產生一列。分數照抄來源列；（讀音, 詞）已在基底、疊加層或變調層就不產生。單字列也處理（和 R6 相同）。
- **基底的列 → `sandhi-add.tsv`（MIT）**：
  - `tools/build_sandhi.py` 加 R7，只讀基底（`mcbpmf-data.txt`），和 R6 相同；加進 `SINGLE_CHAR_RULES`。
  - 預期 **12 列**：基底 6 列 × 2 個讀音（幀、這幀、十幀、一幀 ㄧ 與 ㄧˊ 兩列、連篇累幀）。生成器印出 R7 列數；不是 12 時 `tools/test_build_sandhi.py` 的 `test_the_committed_base_gives_exactly_twelve_rows` 不過（數目只在那個測試守，生成器本身不中止）。
- **疊加層的列 → `overlay-add.tsv` 的檔尾（CC BY-SA 4.0）**：
  - `tools/build_overlay.py` 的檔尾改成三段：`manual`、`slang`、`colloq-reading`。`tail_rows` 多收一個參數「已產生的列」，在網路用語之後接上其中含「幀」的列的 R7 變體（新標籤 `colloq-reading`）。
  - `rebuild_tails` 把 `colloq-reading` 也當檔尾標籤：讀檔時和 `manual`、`slang` 一起拿掉再重算，所以 `--tails-only` 連跑兩次結果逐位元組相同（不會把 22 列重複寫一次，重複會讓核心整個載入失敗）。完整建置（`build()`）把它產生的列傳給 `tail_rows`。
  - 來源列是同一個檔裡已產生的列；變體留在同一個 CC BY-SA 的檔，授權跟著來源。
  - 預期 **22 列**：疊加層 11 列 × 2，即幀中繼、幀率、幀速率、幀頻、幀頻率、影幀、掉幀、數據幀、裝幀、關鍵幀、零幀起手。不是 22 就停。
  - 規則表（R7 要改的字、原讀音、新讀音）只定義一次，兩支工具共用。
- **建置**：
  1. `python3 tools/build_overlay.py --tails-only`：不讀維基 dump 與私人語料，保留產生的列，重算檔尾。
  2. `python3 tools/build_sandhi.py`。
  
  `overlay-add.tsv` 的差異只能是新增的 22 列；再跑一次 `--tails-only`，檔案不變。
- **去重**：核心把疊加層、變調層、名單、詞包串成一份文字，同一個（讀音, 詞）重複會整個載入失敗。兩組列的詞不同（基底的詞 vs 疊加層的詞），`build_sandhi.py` 照舊對疊加層去重。
- 文件：`s2r-sandhi-variants.md` §0 的「口語通行讀音」例外改成：基底詞的收進 `sandhi-add.tsv`，疊加層詞的收進 `overlay-add.tsv` 檔尾（`colloq-reading`），目前有「紮」ㄓㄚ（R6）與「幀」ㄓㄣ、ㄓㄥ（R7）；§1 的規則表加 R7 一列；`LICENSES/data.md` 的兩列照實寫：`sandhi-add.tsv` 的 R7 列是 MIT 基底的衍生；`overlay-add.tsv` 的 `colloq-reading` 列是疊加層的衍生，CC BY-SA 4.0，署名照該檔原本的。

## 2. 載入、golden、測試

- 載入不改：兩個檔都已經在每一個載入點，核心程式的行為不動（`core/src/engine.rs` 的 `load_lexicon` 只在註解補上 R7）。
- golden 預期（重產前寫下，重產後 `git diff eval/golden/` 逐項核對）：
  - `eval/golden/s2h-lm-context.txt` 錯字回報的聊天、書面兩行：`oracle@64` 可能 +1（第 175 列）、`top1` 可能 +1、`top1_sha256` 跟著第 175 列的選字變；這三項以外不變。
  - `eval/golden/sp-predict.txt`：讀音 ㄓㄣ 或 ㄓㄥ 的預測列可能多出「幀」或幀字詞。變了就逐行看，只能是新增的「幀」詞造成的差異。
  - 其他 golden 不變。
- 測試：
  - `make test-python`：`tools/test_build_overlay.py` 裡寫死兩段檔尾的斷言（`("manual", "slang")` 的排序與檔尾起點，兩個測試）改成三段 `("manual", "slang", "colloq-reading")`；新增 R7 檔尾的測試（22 列、`--tails-only` 冪等）；`tools/test_build_sandhi.py` 加 R7 的斷言。
  - `python3 tools/build_sandhi.py --check`；
  - `make test-golden`。

## 3. 評測（照現行出貨標準，對 main 的出貨值）

- 基準與新版各跑一次 CLI 出貨設定（`--context --packs acg,it --tinylm`；聊天加 `--colloquial-core`），同一台 GCP VM、同一種 CPU，`--rowstats`，再 `tools/evalstats.py compare --net`：
  1. discordtune（過濾版，聊天）：不顯著變差，而且改壞÷改對 ≤ 0.6；
  2. 回歸層：打字測驗（聊天、書面）、dev302 書面，逐列相同或改壞 ≤ 2 且淨值 ≥ 0；dev302 聊天只報；
  3. cvtune-clean 聊天：Δtop1 的 90% 配對 bootstrap 下界 ≥ −1.0 個百分點；
  4. 錯字回報（兩種設定合計，全部 175 列）：淨值 > 0 且改壞÷改對 ≤ 0.6。
- 另報：
  - 第 175 列在兩種設定的選字（公開列，可寫句子）；
  - 評測集量不到、但會變的地方：ㄓㄣ、ㄓㄥ 的單字候選順序，以及「真數」「爭數」這類同讀音詞會不會被「幀」詞擠下。
- 四條全過才出貨。照 R6 的先例，四條全過時報數字，**請維護者決定要不要免確認**（逐次決定，記在 `decisions.md`）。
- 條件 4 若是 0 改對 0 改壞：照登記不過，報數字、和維護者討論（R6 當時就是這樣）。任一條不過：報「照登記不過」和數字。

## 4. 停止條件、範圍外

- **停止**：
  - `sandhi-add.tsv` 的 R7 列不是 12（`test_the_committed_base_gives_exactly_twelve_rows` 不過），或 `overlay-add.tsv` 的差異不是剛好 22 列 `colloq-reading`（22 列由 `tools/test_build_overlay.py` 的 R7 檔尾測試守，基底的 12 列由 `tools/test_build_sandhi.py` 守）；
  - §2 預期以外的 golden 改變；
  - `build_sandhi.py --check` 或 `make test-python` 不過；
  - 評測的列檔雜湊或模型雜湊不符。
- **檔案範圍**：
  - `tools/build_sandhi.py`、`tools/test_build_sandhi.py`、`tools/build_overlay.py`、`tools/test_build_overlay.py`；
  - `core/src/engine.rs`（只改註解）、`README.md`（功能列）；
  - `data/lexicon/sandhi-add.tsv`、`data/lexicon/overlay-add.tsv`（只多 22 列）；
  - `docs/contracts/s2r-sandhi-variants.md`、`docs/contracts/frame-reading.md`、`LICENSES/data.md`；
  - `eval/golden/s2h-lm-context.txt`、`eval/golden/sp-predict.txt`（只在 §2 的條件下）；
  - `docs/research-log.md`。
- **回滾**：revert 本片的 merge commit。只有資料與生成器，沒有核心程式。
- **預算**：GCP VM 一台 ≤ 1 小時；超過就停。
- **範圍外**：
  - 新詞（包括「幀數」）；
  - 其他字的口語讀音（之後有回報再逐字問維護者）；
  - 模糊音（`fuzzy-input.md`；「幀」的 ㄓㄣ 和 ㄓㄥˋ 聲調也不同，模糊音救不了）；
  - 學習與新手測驗；
  - 之後才加進詞包、手動詞、網路用語或名單層的含「幀」詞不會自動有 ㄓㄣ、ㄓㄥ：R7 只作用在基底與目前疊加層產生的列；核心端統一補別名是另一片（已知限制）；
  - 「一幀」的 R7 列沿用來源的「一」讀音（ㄧ、ㄧˊ），不照新的「幀」讀音重算變調（已知限制）。
