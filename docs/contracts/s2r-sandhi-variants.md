# S2r 契約：依教育部規範補齊「一」「不」等標準讀音（使用者 2026-10-04）

使用者說「詞庫有點不夠日常」，同意照 Fable 5.1 審查的順序先補讀音、再提前做 S4（`experiments/s2/review-fable-colloquial.md`）；並要求規則以中文標準讀音（教育部規範、正音班所教）為準、查清楚。研究與詞庫比對全文在 `experiments/s2/research-moe-readings.md`（main 核對過引用的詞庫行與審訂表條目）。

## 0. 依據（標準）

| 規範 | 內容 | 出處 |
|---|---|---|
| 《國語辭典簡編本》〈單一音讀〉 | 「一」若為數詞、序詞，一律讀本調；置於兩疊字動詞中間一律標本調（問一問）；「一」在去聲字前標變調 ㄧˊ，在陰平、陽平、上聲字前標變調 ㄧˋ；「不」在去聲字前標變調 ㄅㄨˊ；做法是先標本調、再以（變）標變調 | https://dict.concised.moe.edu.tw/page.jsp?ID=55&la=0&powerMode=0 |
| 88 年《國語一字多音審訂表》（現行；101 年版只是初稿、未定案） | 只列本調（一 ㄧ、不 ㄅㄨˋ），連讀變調不在審訂範圍；「和」4.ㄏㄢˋ（我和你）；「法」1.ㄈㄚˇ（法國）2.ㄈㄚˊ（限讀 法子） | https://language.moe.gov.tw/uploads/files/17860007801063.pdf |
| 《重編國語辭典修訂本》 | 一、不一律只標本調 | https://dict.revised.moe.edu.tw/ |

所以一個符合規範的詞庫，對同一個詞**本調與（合規則的）變調兩種打法都要收**：重編本與審訂表的使用者打本調，簡編本與教科書的使用者打變調。教育部資料是 CC BY-ND 或授權未定，**本片不散布任何教育部資料**：變調是規則，其餘讀音都已在小麥基底（MIT）裡。

**現況缺口**（`research-moe-readings.md` §4，以 python 在 `mcbpmf-data.txt` 量得）：「不」只收變調的詞 820 個（不是、不要、不錯、不太、是不是、找不到…，只有不會、不用、不過等少數兩種都有）；「一」只收變調的詞 124 個（看一下、一定要、進一步）；「一」只收本調、缺變調的 354 列；「法國」只有 ㄈㄚˋ，標準讀音 ㄈㄚˇ 打不出整詞。

**不在本片**：
- 疊加層（`overlay-add.tsv`）的上游錯誤（814 列非去聲前的「不」標成 ㄅㄨˊ、576 列人名地名的「和」標成 ㄏㄢˋ，起因是 `reference/proto/ime.py` 的 `to_syllables` 取單字最高分讀音）與疊加層的變體：下一片 S2r-2。
- 使用者個人打法（例如「一」一律打 ㄧˋ 這種非規範的習慣）：交給 Q（新手測驗）與 S4（學習），不寫進散布的詞庫。
- 輕聲（對不起 ˙ㄅㄨ、看一看 ˙ㄧ）：簡編本標本調，不生成輕聲變體。
- 大陸讀音（期 ㄑㄧ、危 ㄨㄟ、垃圾 ㄌㄚ ㄐㄧ…）：不加。
- 回報的「不做／不作」：不是讀音問題（`review-fable-colloquial.md` 開頭的更正），不為它調整。

**怎麼算做到**：使用者打 `ㄅㄨˋ ㄕˋ`（本調）與 `ㄅㄨˊ ㄕˋ`（變調）都得到「不是」；打 `ㄎㄢˋ ㄧ ㄒㄧㄚˋ` 得到「看一下」；打 `ㄈㄚˇ ㄍㄨㄛˊ` 得到「法國」。這幾個例子在實作第 0 步先確認「現況下會錯」，確認不了的換成量得到的例子（§4）。

## 1. 產生規則（`tools/build_sandhi.py`）

輸入：`data/lexicon/mcbpmf-data.txt`（基底，MIT）；讀 `data/lexicon/overlay-add.tsv` 只為了去重。輸出：`data/lexicon/sandhi-add.tsv`，每列 `讀音\t詞\t分數\tsandhi`；`--check` 重產並和現有檔逐位元組比對。

- 只處理基底的**多字**列（單字列不動：ㄧ／ㄧˊ／ㄧˋ、ㄅㄨˋ／ㄅㄨˊ 都已存在），略過 `#`、`_` 開頭與字數不等於音節數的列（和 `Lexicon::parse_with` 相同）。
- 「下一音節的聲調」：看讀音字串最後的聲調符號（無符號＝陰平、ˊ＝陽平、ˇ＝上聲、ˋ＝去聲、˙＝輕聲）；下一個字是「不」時視為去聲（不的本調是去聲）；下一個字是「個」而讀輕聲時視為去聲（審訂表「個」不取輕聲，`research-moe-readings.md` §1.2；ㄧˊ 是推論，標為未驗證）。
- **排除**（「一」一律只用本調、不生成變調）：
  - 前一字是「第」或數字字（零〇一二三四五六七八九十百千萬億兆兩）；
  - 後一字是數字字或「月」；
  - 「一」在詞尾；
  - 疊字動詞中間（前一字＝後一字，例：問一問、看一看）。
- 每條規則對一列至多產生一個變體（把這一列中所有符合條件的位置一起改），規則各自獨立套用在**原列**上：

| 規則 | 條件 | 產生 | 預期新增列（`research-moe-readings.md` §7） |
|---|---|---|---|
| R1 一 本調→變調 | 「一」讀 ㄧ、不在排除範圍、下一音節有聲調可判 | 去聲前 → ㄧˊ；陰平／陽平／上聲前 → ㄧˋ；其他（輕聲且下一字不是「個」）不改 | 354 |
| R2 一／不 變調→本調 | 「一」讀 ㄧˊ 或 ㄧˋ，或「不」讀 ㄅㄨˊ | 這一列所有的「一」改成 ㄧ、所有的「不」改成 ㄅㄨˋ（同一詞兩個字都在變調時，產生的是全部本調的那一列，例：一動不動） | 約 987（研究報告分開量的 R2a 134＋R2b 853，兩者重疊很少） |
| R3 不 本調→變調 | 「不」讀 ㄅㄨˋ 且下一音節是去聲 | 改成 ㄅㄨˊ | 13 |
| R5 法 | 「法」讀 ㄈㄚˋ（審訂表無此音） | 改成 ㄈㄚˇ | 28 |

- 分數照抄來源列；（讀音, 詞）已在基底或疊加層就不產生；同一個（讀音, 詞）由多列產生時取最高分；輸出依（讀音, 詞）的 UTF-8 位元組序排序。
- 生成器印出每條規則的列數。和上表任一數字相差超過 10% 就停下來查（§5）。R5 已由審查者核對：多字詞中「法」讀 ㄈㄚˋ 的 36 列，其中 8 個詞已有 ㄈㄚˇ 列，36 − 8 = 28。
- 授權：MIT 基底的衍生，檔案仍是 MIT；`LICENSES/data.md` 加一列，寫明依據的規則出處（規則是事實，不含教育部資料）。

## 2. 載入（每一處都要改，否則 Rust 與 Python 不一致）

| 位置 | 改法 |
|---|---|
| `core/src/engine.rs` `load_lexicon` | 讀 `sandhi-add.tsv`，依固定順序（先 `overlay-add.tsv`、後 `sandhi-add.tsv`）串接後交給 `Lexicon::parse_with`；串接時若前一份沒有結尾換行就補一個；檔案不存在回 `LoadFailed`（碼 3）。`load_lm` 給 `CappedLexicon::new` 的仍只有 `overlay-add.tsv` 的文字（變體詞是基底詞，不封頂） |
| `cli/src/main.rs` | LM 模式走 `load_lexicon`（自動含）；unigram 模式讀檔的那段也串接 `sandhi-add.tsv`；`--no-overlay` 兩份都不載 |
| `reference/proto/ime.py` `Lexicon` | `overlay` 參數同時接受單一路徑（現行呼叫者不用改）或路徑清單（依序接在基底後面，規則同現行）；`lm_eval.py`、`experiments/s2/iter2.py` 依同一順序傳入兩份；`cap_overlay` 的詞集仍只取 `overlay-add.tsv` |
| `scripts/build-app.sh` | 複製 `sandhi-add.tsv` 進 `Resources/`，加進必要檔案的檢查 |
| `scripts/check-app.sh` | 必要 Resources 清單加 `sandhi-add.tsv`（第 47–50 行的迴圈） |
| `macos/Tests/ShanjieKitTests/Support.swift` | `TestData.files` 加 `sandhi-add.tsv`（否則所有用 `TestData.resources()` 建引擎的殼測試都會回碼 3） |
| `macos/Tests/ShanjieKitTests/SelftestTests.swift` | 第 14、24 行的 `only:` 清單加 `sandhi-add.tsv`：這兩個測試要測「缺模型／模型錯誤」，若缺的是新檔，會在載入詞庫時就回碼 3，照樣通過卻不再測到模型那條路徑。第 19 行（缺基底）不用改 |
| `core/src/ffi.rs`（只動測試） | `tiny_dir` 也寫一個空的 `sandhi-add.tsv`；新增「缺 `sandhi-add.tsv` → `shanjie_engine_new` 回 3」的斷言 |
| 文件 | `docs/contracts/s3a.md` §5（data_dir 必要檔案）、`docs/contracts/s3b.md` §2（Resources 清單）與描述測試資料目錄的段落、`LICENSES/data.md`、`docs/PLAN.md`（S2r 條目） |

變體列和來源列同分，而 `by_word` 只在嚴格較高時取代（Rust `lib.rs:181`、Python `ime.py:30-31`），所以 `to_syllables` 與 `--check-readings` 的結果不變。`tools/readings.py` 不受影響的原因不同：它**只載入基底**（`readings.py:45`），從基底的所有讀音裡挑；**不要讓它載入 `sandhi-add.tsv`**，否則它的規則 1（「一」「不」打本調）會改選本調、改掉評測集的讀音。結論：**評測集的讀音不重產**。

## 3. golden 與一致性

| golden | 產生者 | 預期 |
|---|---|---|
| `eval/golden/unigram.txt`、`s1-dev302-nooverlay.txt` | — | 逐位元組不變（不載入疊加層） |
| `s1-dev302.txt`、`s1-overlay-sets.txt` | Rust CLI（和原本一樣） | 重產 |
| `s2-lm.txt`、`s2-lm-dev302-top1.tsv` | Python `lm_eval.py` | 重產 |

- 重產**前**先寫下每份預期變動的列數（推法：數該集合裡讀音含新變體讀音的列），重產後列出實際變動的列（公開集合可列句子），和預期比對；差很多就停。
- **跨語言檢查**（取代「Rust 跟自己比」）：新增 `reference/proto/check_unigram_overlay.py`，用 Python `ime.Lexicon`（兩份疊加層）以 unigram 解碼 dev302 與 S1 的疊加層集合，第一名必須和新的 Rust golden 完全一致；本機與 verifier 都跑。

## 4. 評測（`docs/methodology.md` §3 的規則）

0. **基準（停止條件）**：新增 `experiments/s2/build_probe.py`（含 `--check`：重產並逐位元組比對），從 cvtune、dev302、typing76 取含「一」或「不」的列，依 §1 的規則把每個可換的位置換成**另一種合規讀音**（本調↔變調；排除範圍不換），一列產生一個探針列；公開的探針列寫到 `eval/probe/s2r-probe.txt`（格式同 dev 檔 `前文|句子|讀音`，**不放在 `eval/dev/` 底下**，否則會改變「開發集前 302 列」）；discordtune 用同一規則產生，存在 `~/side-project/shanjie-private/s2-probe/`，在 `iter2.py` 的集合清單註冊為私有，只出列數與數字。以**現行**詞庫量探針 top-1，和同一批列原本讀音的 top-1 配對比較。兩者差距在一個配對標準誤以內就停：前提不成立。
1. **主指標**：補齊後探針 top-1 對補齊前，cvtune 探針與 discordtune 探針各自配對比較，p < 0.05 且淨值為正（gate 集合）；dev302、typing76 的探針只報數字（挑戰集，不當門檻，`methodology.md` §2）。
2. **不退步**：cvtune、wikitune、discordtune 用原本的讀音配對比較，不得顯著退步；dev302、typing76 報數字。跑之前先寫下預期變動的列數。
3. **正式路徑**：
   - 引擎走 `Engine::new` 加 `load_lm`，重播 `eval/probe/s2r-probe.txt`，第一名必須等於 **Python 產生並 commit 的期望檔**（`eval/golden/s2r-probe-top1.tsv`，同 `s2-lm-dev302-top1.tsv` 的做法）。
   - 另加字面斷言：第 0 步找到的、現況錯而補齊後對的例子至少兩個（一個「不」、一個「一」），在正式路徑上斷言補齊後的字串。
4. **必須失敗的突變**（兩個，各自檢查）：
   - Rust `load_lexicon` 跳過 `sandhi-add.tsv` → 第 3 項的 cargo test 以斷言失敗（不是編譯錯誤）；
   - Python `ime.Lexicon` 跳過它 → 第 1 項的探針 top-1 回到第 0 步的基準。
5. 每鍵 p95 < 16 ms（沿用既有量法）；回報新增列數與 RSS 變化。
5a. `make test`（含 macOS 的 `swift test`）全綠；`SelftestTests` 的兩個模型負向測試在新清單下確實越過詞庫載入、因模型失敗（verifier 用暫時的斷言或 log 確認失敗碼來自模型那一步）；`make bundle && scripts/check-app.sh` 通過，且把 `Resources/sandhi-add.tsv` 拿掉時 `check-app.sh` 失敗。`s1-dev302-nooverlay.txt` 逐位元組不變、dev302 仍是 302 列。
6. **人工抽查**：抽 50 列變體，以 §0 的教育部規則（含排除範圍）判斷；超過 2 列不合規則就停下來修生成器（不是縮小範圍）。
7. 片結束時由 fresh verifier 量一次保留集，不退步（main 不讀保留集）。

## 5. 負責人、範圍、停止條件與預算

- 負責人：main 親自實作（判斷多、檔案跨 Rust／Python／腳本），不委派；`core/src/ffi.rs` 只改測試資料與一條斷言，不動匯出函式。
- 檔案範圍（只改這些）：`tools/build_sandhi.py`、`data/lexicon/sandhi-add.tsv`、`eval/probe/s2r-probe.txt`、`scripts/check-app.sh`、`macos/Tests/ShanjieKitTests/Support.swift`、`macos/Tests/ShanjieKitTests/SelftestTests.swift`、`core/src/engine.rs`、`core/src/ffi.rs`（測試）、`core/tests/`（新斷言）、`cli/src/main.rs`、`reference/proto/ime.py`、`reference/proto/lm_eval.py`、`reference/proto/check_unigram_overlay.py`、`experiments/s2/iter2.py`、`experiments/s2/build_probe.py`、`eval/golden/`（重產與新增期望檔）、`scripts/build-app.sh`、`LICENSES/data.md`、`docs/contracts/s3a.md`、`docs/contracts/s3b.md`、`docs/PLAN.md`、`docs/research-log.md`、`docs/methodology.md`（探針集的說明）。
- 停止條件：第 0 步基準差距在一個標準誤內；任一 gate 集合顯著退步；生成器列數和 §1 預期差超過 10%；golden 變動列數和預期差很多；抽查不合規則超過 2 列；跨語言檢查不一致。
- 預算：實作加審查最多三輪；評測在本機跑，沿用 S2 的工具（`experiments/s2/log2.tsv` 記錄的過往 run：每組設定解碼約 30–90 秒、載入 40–160 秒；本片的實際耗時未量）。
- 回滾：revert 本片的 merge commit；`sandhi-add.tsv` 是新增檔。
