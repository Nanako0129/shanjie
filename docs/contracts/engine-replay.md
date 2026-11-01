# E-E 真引擎重播 `shanjie-replay`（研究工具，不出貨）

狀態：已實作（PR #210；`--learn-file` 為後加的唯讀選項，§2、§6；`--learn-guard` 為 2026-10-20 後加的評測開關，§2、§5、§6、§7）；契約 READY（2026-10-18 第二次審查，plan-verifier 第一次 REVISE 的 4 項照改）；`/code-review` 第一輪後修訂（§10）。

## 0. 起因

- `docs/iteration-plan.md` Phase 1 的 E-E，以及 §6 每版 KPI 的「錯字回報的真引擎重播」「按鍵分數」兩欄。
- 現在所有評測都走 CLI（`shanjie-eval`）：每列一次解碼、看第一名。使用者實際看到的是引擎（`core::engine::Engine`）逐鍵的結果：每一鍵重新解碼、語意校正的工作非同步套用、改選會凍結其他字（`docs/contracts/repick-freeze.md`）、學習會改變之後的列。CLI 量不到這些差別，也量不到「使用者要按幾個鍵才能打對」。
- 既有的重播只在測試裡（`core/tests/engine_rerank.rs` 的 `replay`、`core/tests/engine_replay.rs`）：只打 dev302、不改選、不學習、不輸出逐列檔。

## 1. 要做到什麼

1. 新的執行檔 `shanjie-replay`（`cli/src/bin/` 底下，cli crate 多一個 `[[bin]]`），讀列檔（`--rows FILE`，或 `--dev N`），**和 CLI 的 LM 模式用同一個讀列函式**（`--rows` 的 `three_field_rows`、`--dev` 的載入，搬到兩個執行檔共用的地方），列與列號才和 CLI 的 rowstats 對得上。輸出檔在讀完列檔之後才建立；任何輸出路徑和列檔是同一個檔時拒絕執行。整次執行只照 App 的方式建**一個**引擎（§2，和 App 一樣，一個引擎服務所有的組字），之後每列：
   1. `set_left_context`（列的第一欄，和 CLI 的 `--context` 相同；送出時 `clear_all` 會清掉前文與組字狀態），逐鍵打出讀音。
   2. 每一鍵之後依 `--rerank` 的時序處理語意校正的工作（§3）。
   3. 打完時記下「第一次顯示」：這一列打字中途引擎已經送出的文字（`Output.commit`，例如組字達到上限自動送出、數字列送出）接上當下的 `preedit`（Enter 會送出的文字）。中途有送出的列數記在摘要行（`autocommits`）。
   4. `--correct` 時模擬使用者改選，直到顯示的文字和正解相同或改不了（§4）。
   5. 按 Enter 送出。`--learn on` 時引擎從這次送出學習，學到的留給後面的列（列照檔案順序）。
2. 輸出（都只有數字，不含任何句子）：
   - `--rowstats FILE`：第一次顯示的逐列統計，格式與 `docs/contracts/eval-stats.md` §1 完全相同（`row\tok\terrors\tgold_len`；共用同一個寫檔函式，必要時把它搬到 core），可直接給 `tools/evalstats.py compare`。
   - `--keys FILE`：每列一行 `row\tideal\tactual\tpicks\tfinal_ok\trewrites`（§5）。
   - 摘要行一行到 stdout（§5）。
3. 單元測試 `cli/tests/replay.rs`：每條規則至少一個「規則壞掉就會失敗」的測試（§6）。

## 2. 引擎的設定（照 App）

- 建立順序與預設值照 `macos/Sources/ShanjieKit/Shell.swift` 的 `build()`：帶詞包建引擎（`acg` 與 `it`，App 的預設兩個都開；2026-10-29 起，之前只有 `acg`）、`load_lm`（bigram，連帶的 classes／kn／trigram 照 `load_lm` 的規則）、`load_tinylm`、`load_colloquial`（口語表，見下）、降權開、語意校正開、強度照 App 預設、`set_profile`。
- **和 App 的已知差異**（寫進設定標記）：預測列關（`set_prediction(false)`）、英文混打關（不載英文詞表、`set_english(false)`）。App 預設兩者都開；開著時 Space 開的候選窗會把預測列的項目排在前面、有的項目不是以游標為結尾的範圍，英文也可能搶走 Space，改選的鍵數就無法照 §4 定義。兩者都不影響 n-gram 與語意校正的選字。縮寫、直式候選窗照 App 預設（橫式，一頁 9 項）。
- `--profile chat|formal`（預設 chat）。`--no-tinylm` 關掉語意校正（對照用）。
- 口語表（colloquial-term.md §2.3）：App 的 `build()` 在小模型載入後一定載入 `colloquial.sjlm`，所以重播預設在小模型開著且 `data/lm/colloquial.sjlm` 存在時載入它（`--no-tinylm` 時不載，App 小模型沒載入也不載）。`--colloquial FILE` 改載另一份表，`--no-colloquial` 不載（對照用；兩者併用是錯誤）。載入時 stderr 的 `models:` 行最後多一欄 `colloquial.sjlm:<sha8>`，設定標記最後加 `+colloq:<sha8>`；是否真的用到口語項仍由核心照條件決定（聊天、配對的小模型、表已載入；任何強度，各用自己的 α、τ、δ，colloquial-levels.md；重播只有 App 預設的強度）。沒有 `data/lm/colloquial.sjlm` 時輸出和以前逐位元組相同。口語項生效時，讀音通道項（reading-channel.md）跟著它，重播預設和 App 一樣；`--no-reading-channel`（研究用）只留口語項（P4d 那一格），設定標記：聊天、載入了口語表、小模型是配對的那一份（`--no-tinylm` 時沒有）時是 `+colloq:<sha8>+rc`，加 `--no-reading-channel` 就沒有 `+rc`（沒載入口語表時這個旗標什麼都不改）。
- 學習：`--learn off|on`（預設 off）。on 時在第一個鍵之前呼叫一次 `set_learning(true)`、單獨使用時不開學習檔（只在記憶體，`learning_open` 不呼叫；要載入學習檔見 `--learn-file`），新詞 B 照預設開。學習狀態只在這一個引擎、這一次執行內累積。
- `--learn-file FILE`（唯讀載入學習檔）：引擎建好後、建立任何輸出檔之前，讀 FILE 的位元組（只有位元組，不帶權限、旗標、ACL、xattr），寫進本次執行專用的暫存目錄（0700、每次不同、在 `std::env::temp_dir()` 下）的 `learning.tsv`（`create_new`、0600），對該目錄呼叫 `learning_open`，**載入完立刻刪掉暫存目錄**（學習資料已在記憶體；刪除失敗在 stderr 警告；之後 `--learn on` 的存檔因目錄不在而無害地失敗）。原檔只被讀，不寫、不改名、不隔離。FILE 與 `--rows` 或任一輸出檔是同一個檔（比對裝置與 inode，硬連結也算）就拒絕（結束碼 2）；與 `--firsts` 併用一律拒絕（第一次顯示可能含學習檔裡的詞）。載入結果不是正常載入（檔頭錯、`Corrupt`、`Fresh`）、沒有任何可用的筆數、或 `learning_open` 失敗（錯誤只帶 `StoreError` 的種類）就報錯結束，不會在沒有學習資料下默默重放；這些失敗都發生在輸出檔建立之前，既有輸出檔不會被截斷。解碼不論 `--learn` 開關都讀載入的學習資料。摘要標記加 `+learnfile`；stderr 只印筆數與跳過的行數，不印檔案內容衍生的任何字、前文或讀音。**刻意不記錄載入的是哪一份學習檔**（隱私），所以不同快照的 `+learnfile` 結果無法從輸出區分，由執行者自己記。
- 模型路徑同 CLI（`data/lm/`），啟動時印出各模型的 sha256 前 8 碼與旗標（像 CLI 的摘要行標記），讓結果對得回模型。

- `--packs LIST`（2026-11-01 加，PUNCT-0 `docs/contracts/punct-composition.md`）：`acg`、`it`、`quotes` 的逗號清單，研究用。不給時等於 App 的預設 `acg,it`，輸出逐位元組不變；給了就照清單建引擎，設定標記照位元順序列出詞包（例如 `+acg+quotes+it`）。
- 列裡的標點（2026-11-01 加，PUNCT-0）：讀音欄可以有單獨一個記號 `，` `。` `？` `！` `：`（照 App 送 Shift＋`,` `.` `/` `1` `;`）或 `、`（不按 Shift 的 `\`），標點和注音留在同一個組字區，每個標點算一鍵。其他記號（例如 `；`，標準排列沒有直接的鍵）照舊報錯。
- `--learn-guard on|off`（預設 on，等於 App；2026-10-20 加，診斷 D5 (3)）：學習守門（S7c-2a §2.2：一段的 n-gram 第一名裡有學習加過分的詞，整段不送語意校正）開或關。off 時那些段照常送語意校正，其他一切不變。用途是量守門在有學習檔時擋掉多少段、擋掉的段裡語意校正會不會改對（研究紀錄 2026-10-20「讀 0c 開源的輸入法 App」）。引擎多一個 Rust 的設定函式 `set_learn_guard(bool)`（預設 true）與唯讀的 `guard_skips()`，**不進 `shanjie.h`、App 與 CLI 都不提供**。設定標記 off 時加 `+noguard`。

## 3. 語意校正的時序 `--rerank each|final`

- `each`（預設）：每一鍵之後 `take_rerank_job` → `run` → `apply_rerank`，等於模型每次都在下一鍵之前算完。
- `final`：打字中的工作都不套用（下一鍵會讓它過期），只在最後一鍵之後、Enter（或改選）之前套用一次，等於使用者打得比模型快、送出前停一下。
- 兩者都計數「顯示後被改掉」：`rewrites`＝`apply_rerank` 回傳 `Some`、而且套用後的 `preedit` 和套用前不同的次數（只算語意校正造成的改字；n-gram 自己逐鍵改字不算，和 `core/tests/engine_rerank.rs` 的 `Replay::net` 不同）。`final` 的那一次套用也算，所以 `final` 每列最多 1。

## 4. 改選模擬 `--correct`

- 比對用 CLI 的寬鬆比對（`lenient`，異體字視為相同）。讀音每個音節對一個字，顯示的文字與正解字數不同時不改選、記成改不了。中途已送出的部分不能再改，只改還在組字區的部分（和正解對應的那一段）。
- 每一輪：找第一個不同的字的位置 i；從游標位置 j = i+1 往右找第一個「候選窗裡有一項，它的範圍蓋住 i、文字等於正解同一段」的 j（候選窗列出以游標為結尾的各種長度，`window_list`）。找到就照最少鍵的走法選它：游標移到 j（Left／Right 每鍵一個音節，Home／End 比較少鍵時用它們）、Space 開窗、Space 翻到那一頁、按那一項在頁內的數字鍵（橫式一頁 9 項，每一項都有數字鍵；數字鍵直接選定，不必再按 Enter）。選字不移動游標；之後的改選會蓋掉它重疊到的凍結詞，所以凍結不會擋住後面的修正。只計成功那一條路的鍵，找不到的位置不計（假設使用者知道要去哪裡改）。
- 選完重新比對，再下一輪；最多改選 2 × 字數次。找不到任何 j，或選完顯示的文字沒有變，就停止改選、記成改不了，直接送出。只往右找到 i 加最長詞長為止（再往右的候選窗不可能蓋住 i），結果不變。
- `--correct` 不給時不改選，第一次顯示就是送出的文字。

## 5. 數字的定義

- `ideal`：照讀音打的最少鍵數（每個音節的注音符號鍵，加聲調鍵；一聲用 Space）＋ 1（Enter）。
- `actual`：實際送進引擎的鍵數（打字＋改選的游標、開窗、翻頁、選字鍵＋ Enter）。
- 按鍵分數＝Σideal ÷ Σactual（KySS 的做法，注音版）。
- `picks`：改選次數；`final_ok`：送出的文字是否等於正解（寬鬆比對）。
- 摘要行：列數、第一次顯示的 top1、送出時正確的列數、改不了的列數、按鍵分數、每百字改選次數、`rewrites` 合計、`top1_sha256`（第一次顯示的文字以 `\n` 串接的 sha256，算法與 `cli/src/main.rs` 的 `top1_sha256` 相同），以及設定標記（profile、learn、learnfile、rerank、correct、`--learn-guard off` 時的 `+noguard`、模型雜湊前綴、預測列與英文關）。
- `guard_skips`（摘要行多一欄，2026-10-20）：每列打完最後一鍵時（第一次顯示之前、`final` 套用之前），最近一次解碼裡「本來會送語意校正（語意校正開、模型已載入、那段的候選 ≥ 2）而被學習守門擋下」的段數，全部列加總；另一欄 `guard_rows` 是其中段數 ≥ 1 的列數。引擎的 `guard_skips()` 回傳最近一次解碼的段數（每次解碼重算，不累加）。`--learn-guard off` 時兩欄都是 0。沒有學習資料或 `--no-tinylm` 時恆為 0。2026-11-01 以前重播的列沒有標點鍵，一列最多一段，兩欄相等；列裡有標點時一列可以有好幾段；最後一鍵觸發 128 音節自動送出時，工作隨送出作廢，那一列計 0。有標點的列 `guard_rows` 可以小於 `guard_skips`。
- `--firsts FILE`（只給一致性比對用）：每列一行第一次顯示的文字。只能用在公開集合（dev302、typing76、錯字回報），檔案不進 repo；工具不知道哪個集合是私人的，這條由使用的人遵守。
- 「本來對、學了之後錯」：同一份列檔跑 `--learn off` 與 `--learn on` 各一次（都 `--correct`），兩個 `--rowstats` 用 `evalstats compare`：改壞數就是這個數字。工具本身不另算。

## 6. 驗收

1. **一致性**：`--learn off --rerank each`、不改選時，第一次顯示的文字和 CLI 出貨設定（`--lm data/lm/bigram.sjlm --profile P --context --packs acg,it --tinylm data/lm/tinylm.sjtl`；2026-10-29 之前是 `--packs acg`）每一列的選字相同。在 dev302、typing76、錯字回報（全部列）兩種設定上比：
   - 先比摘要行的 `top1_sha256`，相同就通過；
   - 不同時，用重播的 `--firsts` 對 CLI 每列的選字（由 CLI 的 `--dump` 加 `--tinylm-scores` 的 `"c"` 欄還原，做法同研究紀錄 2026-10-17「rerank-size」一節的重播一致性比對）逐列比，列出差異列號並歸因：重播工具的錯就修；引擎和 CLI 真的不同就寫進研究紀錄（這正是「錯字回報離線是對的那一桶」要拆的東西），不改引擎。
2. 單元測試（合成的小詞庫或真詞庫的短句皆可）：
   - 鍵數：一句已知讀音的 `ideal` 與不改選時的 `actual` 等於手算值；
   - 改選：一列第一次顯示錯、候選窗裡有正解的，改選後 `final_ok` 為 1、`picks` ≥ 1、`actual` 等於手算的改選鍵數；
   - 改不了：正解不在任何候選窗時記成改不了、不無限迴圈；
   - 學習：同一句打兩次（同一個引擎，`--learn on --correct`），第二次的第一次顯示就是第一次改成的字；`--learn off` 時不會；
   - 時序：找一列 `each` 的 `rewrites` ≥ 2 的句子，`final` 在同一列的 `rewrites` ≤ 1（`final` 若在打字中途套用，這條會失敗）；
   - 中途送出：一列讀音超過組字上限時，第一次顯示包含中途送出的文字、`autocommits` 計到這一列；
   - 輸出：`--rowstats`、`--keys` 只有 `[0-9\t\n]`；`--rowstats` 和 CLI 同一份列檔的格式逐位元組相容（`evalstats compare` 讀得進去）。
   - `--learn-guard`（`core/tests/engine_rerank.rs` 與 `cli/tests/replay.rs`）：照 `learned_stretch_is_not_reranked` 的設定（教過 回覆、郵件 把列切成兩段），守門開時工作只有右邊那一段、`guard_skips()` 是 1；`set_learn_guard(false)` 時兩段都在工作裡、`guard_skips()` 是 0。重播：用合成學習檔，`--learn-guard off` 與預設各跑一次，預設的 `guard_skips` ≥ 1、off 是 0、設定標記多 `+noguard`；預設的輸出和明寫 `--learn-guard on` 逐位元組相同。突變：讓 `set_learn_guard(false)` 不生效，引擎測試要失敗。
   - `--learn-file`（`cli/tests/replay.rs`，只用合成檔）：載入後第一次顯示改變、原檔位元組與 mtime 不變（含 `--learn on --correct`）、暫存目錄不留；檔頭錯、沒有可用筆數、檔不存在都報錯且既有輸出檔不被截斷、不產生 `.corrupt`；輸出是同一個檔或硬連結（`--rowstats`、`--keys`）拒絕且原檔不變；與 `--firsts` 併用拒絕。
3. 在 GCP 或 Windows 機器（不在 Mac）跑 dev302 與錯字回報兩種設定，learn off／on × correct，把摘要行與 `evalstats compare` 的結果寫進研究紀錄：這是每版 KPI「按鍵分數」與「錯字回報真引擎重播」的第一筆。
4. `/code-review` ＋ fresh verifier ＋ CI（程式改動的關卡）。

## 7. 範圍外

- 不改引擎行為；需要讀取引擎內部狀態時只加唯讀的存取函式（不改既有函式的行為）。要改行為才能重播 → 停止條件。例外（2026-10-20）：只給評測用、預設等於 App 的開關 `--learn-guard`（§2），引擎的設定函式不進 `shanjie.h`。
- 不模擬打字速度與真的非同步執行緒（`final` 是極端情況的近似）；不用預測列、英文混打、標點、多句。
- 不跑保留集；私人集合（discordtune 等）只在 Mac 以外的機器跑、只帶回數字。
- 每版 KPI 表本身（回填 v0.4.0–v0.4.7）是 Phase 0 第 6 項，另做；這片只提供工具與第一筆數字。

## 8. 停止條件

- 一致性比對出現無法歸因的差異（重播工具與 CLI 都看不出原因）。
- 要改引擎行為才能重播。
- dev302 一種設定在 GCP 單核跑超過 30 分鐘（工具太慢，要先找原因再擴到大集合）。

## 9. 負責人

- 契約、一致性歸因、研究紀錄：main。
- 實作與單元測試：executor（在 `../shanjie-ee`，分支 `feat/engine-replay`）。
- 大集合的執行：main（GCP）。

## 10. 審查紀錄

- `/code-review` 第一輪（PR #210，`d6046fc8`）11 項：
  - 修：中途送出與音節數（§1.1.3、§4、§6.2）；讀列和 CLI 共用同一個函式（§1.1）；輸出檔晚於讀列建立、不能和列檔同一個（§1.1）；改選沒有進展就停、只往右找到最長詞長（§4）；時序測試改成會失敗的版本（§6.2）；契約狀態與 `docs/iteration-plan.md` 的指向；測試用的重複按鍵函式。
  - 延後：每個程序重算模型雜湊與重讀詞庫（只影響啟動時間）；改用唯讀的候選窗存取函式取代用按鍵探候選窗（要在 core 加函式，現在的做法和 CLI 逐列相同）。
- fresh verifier（`e4de008c`）CONFIRMED。兩項建議延後：拒絕「輸出等於列檔」比的是正規化後的路徑，hard link 抓不到（要自己建 hard link 才會發生，列檔都在版控或可重建）；`--dev` 沒有同檔檢查（`--dev` 讀的是 `eval/dev/*.txt`，輸出指到那裡會覆寫進版控的檔，可以用 git 還原）。
