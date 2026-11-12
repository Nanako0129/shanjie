# C ABI 的版本、回傳碼與相容規則

這份寫的是現在的行為。

範圍：核心（`core`）對殼的 C 介面怎麼演進、怎麼回報錯誤、記憶體歸誰、哪些呼叫順序與預設值是約定。宣告和每個函式的語意以 `core/include/shanjie.h` 為準（標頭註解就是函式參考），實作在 `core/src/ffi.rs`；這份不重抄函式清單，只寫版本、回傳碼、所有權與相容規則。按鍵規則見 `docs/spec/keys-and-composition.md`，隱私規則 R2 的全文見 `docs/spec/privacy.md`，殼怎麼呼叫見 `docs/spec/macos-shell.md`，Windows 的使用方式見 `docs/spec/windows-tip.md`。

## 1. 來源與優先順序

- 標頭 `core/include/shanjie.h` 與 `ffi.rs` 的 `#[repr(C)]` 型別、`extern "C"` 簽名、回傳碼必須逐項一致（欄位順序、型別、碼）。兩者不一致時以 `ffi.rs` 的實際行為為準，並在同一個改動裡把標頭改對。
- 標頭註解與這份規格不一致時，以標頭為準；這份只在版本、碼、所有權與相容規則上有發言權。
- 每個匯出函式的語意、預設值與例外寫在標頭裡它自己的註解，不在這裡重複。

## 2. 版本與相容

### 2.1 沒有版本號

- ABI 沒有版本號、版本查詢函式或功能探測；`core` crate 的版本固定是 `0.0.0`（`core/Cargo.toml`），不代表 ABI 版本。
- 相容靠「標頭與靜態庫來自同一個 commit 的同一次建置」。混用不同次建置的標頭與 `libcore.a` 是未定義行為。

### 2.2 一起建置

- `make rust` 先建 `libcore.a`（`cargo build --release --locked -p core`），Swift 套件才連結它（`make test-swift`、`make build`、`make bundle` 都依賴 `rust`）。
- 殼從原處匯入標頭：`macos/Sources/CShanjie/module.modulemap` 直接指到 `core/include/shanjie.h`，沒有複本可以漂移。
- Makefile 有兩道後備，避免 SwiftPM 沒有重編：`libcore.a` 比 Swift 產物新就刪掉產物重連結；標頭比產物新就刪掉 Clang 模組快取與相關產物（標頭預編譯模組有快取，只改標頭會拿舊宣告編譯）。
- App 把核心靜態連結進輸入法程式，所以執行期不會有版本錯配，只有建置期的錯配要防。

### 2.3 怎麼擴充

目前的做法，改 ABI 時照辦：

- **新增函式**：加在標頭與 `ffi.rs`，不改既有函式的簽名與意義。
- **輸出結構新增欄位**：只加在 `ShanjieOutput` 的尾端（`candidate_vertical`、`english_run_utf16_start`、`english_run_utf16_len` 就是這樣加的）；既有欄位的位置、型別、意義不改。輸出由核心配置、殼只讀，尾端新欄位不會破壞只讀前面欄位的程式，但殼仍要用新標頭重編，不混用。
- **新設定**：預設值要讓引擎和沒有這個功能時逐位元相同（例如 `set_fuzzy` 的 0 什麼都不改）。殼在每次建引擎後把所有設定重送一遍，因為重建的引擎一律從核心預設值開始（§6）。
- **位元遮罩**：未定義的位元回 2 且狀態不變（`new_packs` 的 `packs`、`set_fuzzy` 的 `flags`）；新增位元就是延伸可接受的遮罩。
- **移除或改簽名**：核心與殼在同一個改動裡改（舊的 `set_english_inline` 就是被 `set_english_overwrite` 取代後刪除，單字表的載入函式也是）。
- **改按鍵行為**：`docs/spec/keys-and-composition.md` 的規則表與 `shanjie.h` 在同一個改動裡一起改。
- 標頭是 C11，CI 用 `-std=c11 -Wall -Wextra -Werror` 編譯（§9），所以宣告不能有警告。

### 2.4 兩種使用方式

- **macOS**：Swift 經 `CShanjie` 模組匯入標頭，連結 `libcore.a`（`core` 的 crate-type 含 `staticlib`）。
- **Windows**：TSF DLL（`windows/tip`）把核心當 Rust 依賴（crate-type 含 `rlib`），直接呼叫 `core::ffi` 裡同名的 `extern "C"` 函式，不經過標頭；回傳碼、所有權與執行緒規則相同。Windows 目前沒有選字記憶檔：`learning_open` 一律回 3（`learn_store.rs` 在非 Unix 上 `open` 回 I/O 錯誤，且不碰檔案系統）。

## 3. 型別約定

### 3.1 `ShanjieKey`

- `kind`：1 CHAR、2 SPACE、3 ENTER、4 BACKSPACE、5 DELETE、6 ESC、7 LEFT、8 RIGHT、9 UP、10 DOWN、11 HOME、12 END、13 TAB、14 PAGE_UP、15 PAGE_DOWN。不在 1–15 回 2。14、15 只有殼在直排候選窗開著且沒有修飾鍵時才送（規則見 `docs/spec/keys-and-composition.md`）。
- `ch`：`kind` 是 CHAR 時為 Unicode scalar（不按 Shift 的鍵帽字元；單按 Shift 的英數狀態下是鍵盤配置產生的字元）；不是合法 scalar（代理碼位或超過 U+10FFFF）回 2；其他 `kind` 時忽略。
- `modifiers`：bit0 Shift、bit1 Control、bit2 Option、bit3 Command、bit4 Caps Lock。bit5 以上的未定義位元不擋，照原樣交給引擎。

### 3.2 `ShanjieOutput`

- `handled`：1 = 引擎處理了，殼不再把按鍵交給 App；0 = 直通：先插入 `commit`，再放行按鍵。
- `commit`、`preedit`：UTF-8，永遠不是 NULL，可以是空字串。`commit` 是要立刻插入的文字；`preedit` 是組字區顯示（未完成音節的注音插在游標處）。任何一個字串含 NUL 而建不出 C 字串時，整次呼叫回 4。
- `cursor_utf16`：游標在 `preedit` 裡的位置，單位是 UTF-16 code unit（給 `NSRange` 用），在未完成音節之後。
- `candidate_count`／`candidates`：這次輸出的候選數與陣列（收合時是目前這一頁 0–9 個，展開時是可見的排，直排時是可見的列，預測列最多 9 個）；數量為 0 時 `candidates` 是 NULL。
- `candidate_selected`：在這次輸出的 `candidates` 裡的位置；沒開候選時為 −1。−1 而 `candidate_count` > 0 是未進入的預測列（不顯示選取）。
- `candidate_columns`：0 = 收合的一列或直排；展開時一律 9。`candidate_first`、`candidate_total`：`candidates[0]` 在整份清單的位置與清單長度；沒開候選時為 0。
- `candidate_vertical`：0 橫排或沒有候選；1 直排候選窗；2 直排預測列。
- `english_run_utf16_start`、`english_run_utf16_len`：英文段開著時，段在 `preedit` 裡的起點與長度（UTF-16 單位）；沒有英文段時兩個都是 0。
- 輸出是快照：不論這次呼叫是按鍵、選取、重設或設定函式，欄位意義相同。

## 4. 回傳碼

### 4.1 碼的意義

| 碼 | 名稱 | 意義 |
|---|---|---|
| 0 | `SHANJIE_OK` | 成功 |
| 1 | `SHANJIE_ERR_NULL` | 必要的指標是 NULL（引擎、輸出指標、路徑、工作等） |
| 2 | `SHANJIE_ERR_INVALID` | 輸入不合法：字串不是 UTF-8、`ch` 不是合法 scalar、`kind`／layout／mode／profile／語意校正強度超出範圍、遮罩或旗標有未定義的位元、設定值不是 0 或 1（`set_fuzzy` 例外：任一位元）、標點表格式不合法或超過上限、`pick` 的 `index` 超出這次輸出或沒有候選窗也沒有預測列 |
| 3 | `SHANJIE_ERR_LOAD` | 資料載入失敗：資料檔或模型讀不到、格式錯、與模型不配對；`learning_open` 的 I/O 失敗或資料夾不歸目前帳號所有 |
| 4 | `SHANJIE_ERR_INTERNAL` | 內部錯誤：攔下的 panic、解碼錯誤，或輸出字串含 NUL |
| 5 | `SHANJIE_NO_CHANGE` | 只有 `shanjie_engine_rerank_apply` 會回：沒有改變 |

- 回傳非 0 時，輸出指標（`*out`、`*job`）一律設成 NULL，而且不配置任何記憶體；`out` 本身是 NULL 時什麼都不寫（回 1）。
- 碼 5：沒有結果（還沒 run，或 run 回 4）、結果過期、組字裡已不是工作記下的詞、候選窗開著、預測列已進入、每段都選第一名、同一個工作已經套用過。
- 載入函式失敗時引擎維持原狀（沒有就沒有，有舊的就用舊的）：`load_lm`、`load_tinylm`、`load_colloquial`、`set_punctuation`。

### 4.2 各族函式怎麼用碼

- **按鍵與選取**（`shanjie_engine_key`、`shanjie_engine_pick`）：非 0 時殼當作「不處理」直通，而且不記錄這個按鍵。`pick` 回 2 時狀態不變。
- **重排的三個函式**（`rerank_take`、`rerank_run`、`rerank_apply`）：它們沒有對應的按鍵，非 0 **不代表**按鍵直通。`rerank_run` 沒有引擎，碼 4 不重設任何東西。
- **回傳快照的設定函式**：碼 1、2、4，成功回 0 與快照（`handled` 1、`commit` 空，除非這次設定結束了一個英文段而讓組字區到達第 128 個單位，那時 `commit` 是送出的整串）。
- **不回輸出的函式**（`set_punctuation`、`set_left_context`、`set_learning`、`learning_*`、`rerank_levels`）：只有碼，沒有快照。`set_left_context` 收到不是 UTF-8 的字串回 2 並清掉前文；NULL 或空字串表示沒有前文。

### 4.3 碼 4 之後

- 解碼、載入、設定、重排工作與按鍵這一類呼叫回 4 時，引擎自動 `reset`（丟棄模式）：組字區、固定詞與前文清空，語言模型、設定與 ASCII 狀態保留，之後的輸出和新建、載入相同模型、使用相同設定的引擎相同。`set_profile` 回 4 時新的設定保留，只丟棄組字（設定是殼的意圖，組字才可能只改一半）。
- 不重設的例外：`shanjie_rerank_run`（沒有引擎）；沒有改任何狀態的函式（`rerank_levels`、`learning_status`）；`set_punctuation`、`set_left_context`、`set_learning`、`learning_open`、`learning_clear`（沒有組字狀態在半路上）。
- panic 一律變成碼 4，不會穿過 C 邊界。

## 5. 所有權與執行緒

### 5.1 輸出

- 非 NULL 的 `ShanjieOutput*` 由呼叫端用 `shanjie_output_free` 釋放，恰好一次。輸出自己擁有字串與候選陣列的副本，有效期到釋放為止，不受之後的 `key`、`reset`、`set_*`、`load_lm`、`engine_free` 影響。
- `shanjie_engine_free(NULL)`、`shanjie_output_free(NULL)`、`shanjie_rerank_free(NULL)` 什麼都不做。重複釋放、或釋放不是核心配置的指標，是未定義行為。
- 殼收到輸出後，在同一個呼叫裡複製成 Swift `String` 並立刻釋放，不保留輸出指標。

### 5.2 引擎

- 引擎 handle 不是 thread-safe：所有呼叫都在同一個執行緒（IMK 主執行緒）。例外只有 `shanjie_rerank_run` 與 `shanjie_rerank_free`。
- `shanjie_engine_free` 之後不得再用這個 handle。

### 5.3 重排工作

- `rerank_take` 交出上一次改變留下的工作（只交一次；沒有工作時回 0 且 `*job` 為 NULL）。交出的每個工作由殼恰好 `rerank_free` 一次。
- 工作只放擁有的資料（字串、分數、範圍、世代號、設定、模型），不借用引擎，所以引擎釋放之後工作仍可 `run` 或 `free`。
- `rerank_run` 可在任何執行緒呼叫，不能取消；之後的改變只會讓結果作廢。每次 `run` 開頭先清掉前一次的結果，所以 `run` 回 4 時不留任何結果（連之前那次 run 的也沒有）。
- `rerank_apply` 在引擎的執行緒、`run` 回傳之後呼叫；不釋放工作；同一個工作再 apply 一律回 5。
- 未定義行為：同一個工作 `free` 兩次、`free` 之後再 `run` 或 `apply`、`free` 不是 `rerank_take` 交出的指標、同一個工作同時在兩個執行緒使用（`run` 進行中同時 `apply` 或 `free`）。
- 過期：只有會重新解碼的改變讓結果作廢（完成音節、刪除、選字、預測列選字、標點、⌘⌫ 遺忘、改變解碼的設定、換模型、選字記憶的開檔與清除）；不重新解碼的鍵（游標移動、未完成的注音符號、進入預測列、直通鍵）和 `set_learning` 不讓結果作廢。送出時還沒套用的工作作廢。細節見 `docs/spec/semantic-rerank.md`。
- 工作與結果內含候選文字：不寫進任何紀錄（含指標），`load_tinylm` 的路徑也不寫。

## 6. 呼叫順序與預設值

### 6.1 順序約定

- `load_lm` 在啟動時載一次，此時組字區是空的；它不重算目前的顯示，下一次組字改變才用新模型解碼。成功的 `load_lm` 會丟掉小模型與依賴舊模型的快取，所以 `load_tinylm` 要在它之後呼叫。
- `load_colloquial`（常談表）與 `load_lm`、`load_tinylm` 的先後無關；它會讓進行中的重排工作作廢。
- 語意校正強度（`set_rerank_level`）在沒有小模型時也記下；`load_lm`、`load_tinylm` 保留它。殼在每次建引擎、`load_tinylm` 之後送一次存起來的強度。
- 設定函式在每次建立引擎之後都要呼叫一遍（重建的引擎一律從核心預設值開始）；`set_left_context` 在每次組字開始時呼叫（核心在送出、重設、碼 4、規則 22 直通之後自己清掉前文）；`set_learning` 由殼在每個按鍵之前取樣隱私閘門後呼叫。
- 換排列、換詞包（建立時的參數）要釋放引擎再建一個；詞包的詞在建立時併入詞庫，所以開關在引擎生命期內固定。
- `set_profile` 在載入語言模型之前設定也會被記住，載入後生效。

### 6.2 建立時的預設值

| 設定 | 預設 |
|---|---|
| profile | 0（聊天） |
| `set_demote` | 1 |
| `set_prediction` | 1 |
| `set_candidate_vertical` | 0（橫排） |
| `set_key_scheme` | 0（現行規則；1 是微軟注音的按鍵習慣，只有 Windows 版的文字服務呼叫） |
| `set_abbreviation` | 0 |
| `set_english` | 0 |
| `set_english_overwrite` | 1 |
| `set_english_key_order` | 1 |
| `set_english_reversal_guard` | 1 |
| `set_fuzzy` | 0 |
| `set_ascii` | 0 |
| `set_rerank` | 0 |
| `set_rerank_level` | 1（標準） |
| `set_learning` | 0（失敗時關閉：沒有明確開就不學） |
| 標點候選表 | 核心內建的預設表（`set_punctuation` 成功後才換） |

殼不依賴這些預設值：它把自己持有的值每次都送出。

## 7. 隱私與錯誤處理

- 每個匯出函式包在 `guard`（`catch_unwind` 加靜音的 panic hook）裡；`ffi.rs` 有 `#[cfg(panic = "abort")] compile_error!`，把 release 設定改成 abort 時建置失敗。
- 靜音 hook 用 `std::sync::Once` 在第一次呼叫匯出函式時安裝（`|_| {}`），不在 `Engine::new` 裡裝，所以引擎單元測試仍保有 panic 訊息。hook 是行程全域的，會取代宿主行程原有的 hook；輸入法行程只有這個函式庫，所以沒有影響，呼叫匯出函式的測試都在子行程裡跑，免得影響同一個測試執行檔裡別的測試。
- 錯誤只有碼，沒有文字。錯誤訊息與 stderr 不得含任何輸入或資料內容（讀音、組字、候選）。引擎與 FFI 不用 byte 位移切 `str`（用 `char_indices` 或已知邊界）；建 `CString` 失敗走碼 4、不 `unwrap`；裝著輸入文字的型別（`ShanjieKey`、`Key`、`Output`、`Engine`、重排工作）不 `derive(Debug)`；`expect`、`assert`、`unreachable` 只用靜態字串。
- 給殼的規則（寫在標頭開頭）：輸出的內容是輸入，不得傳給 `NSLog`、`print`、`debugPrint`、`dump`、`os_log` 或任何紀錄；`fatalError`、`precondition` 的訊息不得內插輸入文字；語言模型與小模型的路徑、重排工作與它的指標也不寫進紀錄。

## 8. 資料目錄

`shanjie_engine_new`／`_new_packs` 的 `data_dir` 與 `load_lm` 的模型旁邊必須有的檔，缺一個或格式錯都回 3（細節在 `docs/spec/lexicon-and-packs.md`、`docs/spec/models.md`、`docs/spec/decoder.md`）：

- 建立時：`mcbpmf-data.txt`、`overlay-add.tsv`、`sandhi-add.tsv`、`names-add.tsv`、`variant-order.tsv`、`demote.tsv`、`rerank-protect.tsv`。疊加層依 `overlay-add.tsv`、`sandhi-add.tsv`、`names-add.tsv`、啟用的詞包這個固定順序接起來（前一份沒有結尾換行就補一個）。
- `load_lm`：再讀一次 `overlay-add.tsv`、`names-add.tsv`、`demote.tsv`、`variant-order.tsv`（建構之後被移走也是 3）、`std-exempt.tsv`（`rerank-protect.tsv` 只在建立時讀，`load_lm` 不再讀）；模型檔旁必須有 `classes.sjc`；有選用的 `kn.sjkn`、`trigram.sjt3`、`trigram-chat.sjt3` 時，壞了或與模型不配對就整個載入失敗（缺哪張只關哪個設定的那一層）。
- 選用：`digits.tsv`（缺或壞時數字層靜靜關閉）、`mixed-words.tsv`（缺或壞時候選清單沒有中英混合詞，`docs/spec/english-mixed.md` §5.7）、`english-next.tsv`（不存在就關閉；存在卻讀不了或解析失敗回 3，免得出貨錯誤讓功能無聲消失）。
- 詞包：`new_packs` 的 `packs` 是位元遮罩（1 動漫與遊戲、2 經典台詞、4 資訊與 AI、8 迷因與實況用語、16 美妝與保養；檔案 `acg-add.tsv`、`quotes-add.tsv`、`it-add.tsv`、`meme-add.tsv`、`beauty-add.tsv` 依這個順序讀）。遮罩有未定義的位元、layout 超出範圍、路徑不是 UTF-8 回 2；`packs` 非 0 而 `packs_dir` 為 NULL 回 1；檔案存在卻讀不了或解析失敗回 3；檔案不存在就什麼都不加（引擎和沒有詞包時逐位元相同）。詞包的詞怎麼計分見 `docs/spec/lexicon-and-packs.md`。

## 9. 驗證

- **`core/src/ffi.rs` 的單元測試**：
  - R2 panic：用子行程跑（`std::process::Command` 執行 `current_exe()`，以環境變數指定子測試，並加 `--nocapture --exact`，否則 libtest 會攔下輸出，測試變成空包彈）。子行程經 `shanjie_engine_key` 輸入一段標記字串，觸發 `#[cfg(test)]` 的注入點，panic payload 含該標記。斷言：回傳碼 4、`*out` 為 NULL、子行程真正的 stdout 與 stderr 都不含標記、下一鍵回傳 0 且組字區為空。
  - 正向對照：同一個子行程改用預設 hook 時 stderr 必須看得到標記；看不到就代表測試沒測到東西。
  - 經 C ABI 的重播：子行程裡經 C ABI 跑完前 20 列（`c_abi_replay_20_rows_prints_only_harness_lines`），結果與按鍵重播測試相同；子行程的 stdout、stderr 除了 libtest 自己的 harness 行之外沒有其他內容，也不含任何一列的句子或讀音。
  - 兩個 free 函式傳 NULL 不崩潰；各種錯誤碼的情境回傳正確的碼且 `*out` 為 NULL。
  - 各族的碼：`child_lm`（`load_lm` 的碼 1、2、3，失敗時保留原本的模型，`reset` 保留模型與設定）、`rerank_codes_through_the_c_abi`（`load_tinylm`、`set_rerank`、take／run／apply 的所有 NULL 組合、沒有工作時 NULL、還沒 run 就 apply 回 5、同一個工作再 apply 回 5、apply 注入 panic 回 4、引擎釋放後工作仍可 run 與 free）、`r2_rerank_run_panic_is_silent`（第二次 run 在注入點 panic 回 4，不留結果，apply 回 5）、`rerank_level_through_the_c_abi`（強度的碼與遮罩）、`english_codes_through_the_c_abi`（含 `set_ascii`）、`fuzzy_codes_through_the_c_abi`、`candidate_vertical_through_the_c_abi`、`digits_through_the_c_abi`、`expand_and_pick_through_the_c_abi`。
- **C 標頭冒煙測試**（`core/tests/c/abi_smoke.c`）：`cargo test` 只從 Rust 呼叫匯出函式，看不到標頭與 Rust 結構是否一致，所以要用 C 編譯器實際編譯、連結一次。驅動程式只 include `shanjie.h`，參數是資料目錄（第二個參數是語言模型路徑，第三個是學習資料夾）：
  - 兩種排列各做一次：`shanjie_engine_new` 回 0；依排列送出「你好」的按鍵（標準 `s u 3 c l 3`、倚天 `n e 3 h z 3`），每鍵都 `shanjie_output_free`；Enter 之後 `handled == 1`、`commit` 是「你好」、`preedit` 是空字串、`cursor_utf16 == 0`、`candidate_count == 0`、`candidates == NULL`、`candidate_selected == -1`；再送 `ㄋㄧˇ` 和空白鍵，候選已開（`candidate_count` 在 1–9、`candidates[0]` 不是 NULL、`candidate_selected == 0`）；`shanjie_engine_reset(e, 1, &o)` 之後 `commit`、`preedit` 都是空字串；`shanjie_engine_free(e)`，最後對 `shanjie_engine_free(NULL)`、`shanjie_output_free(NULL)` 各呼叫一次。
  - 有語言模型的檢查（編號 301–317：`load_lm`、`set_profile`、`reset` 保留模型）、選字記憶（518–521，要第三個參數）、重排（901–918：沒有 n-gram 時 `load_tinylm` 回 3、NULL 回 1、不存在的檔案回 3；`rerank_free(NULL)`）、強度（951–960）、常談表（961–963）、模糊輸入（1101–1107：位元超出回 2 且 `*out` 為 NULL）。
  - 全部通過時 exit 0；任何一項不符時只印項目編號（不印任何字串內容）並 exit 1。
  - 指令（repo 根目錄，`$T` 是暫存目錄；CI 在 macOS runner 上做同樣的事，另加 `--locked`，並先檢查擷取的連結參數不是空的，因為 macOS 的 `cc` 不帶這些旗標也能連結）：

    ```sh
    cargo build --release --locked -p core
    cc -std=c11 -Wall -Wextra -Werror -Icore/include core/tests/c/abi_smoke.c target/release/libcore.a \
      $(cargo rustc --color never --release -p core --crate-type staticlib -- --print native-static-libs 2>&1 | sed -n 's/.*native-static-libs: //p') \
      -o "$T/abi_smoke"
    "$T/abi_smoke" data/lexicon data/lm/bigram.sjlm
    "$T/abi_smoke" data/lexicon data/lm/bigram.sjlm "$T/learn"
    ```

    連結參數以 `--print native-static-libs` 實際印出的為準（macOS 上印出 `-lSystem -lc -lm`，連結器會警告重複的 `-lSystem`，無害）；`--color never` 是因為 CI 設了 `CARGO_TERM_COLOR=always`，不關掉的話最後一個旗標會接上色碼而連結失敗。
  - 反向檢查：把 `shanjie.h` 裡 `ShanjieOutput` 的 `commit` 和 `preedit` 兩個欄位對調後重新編譯，驅動程式必須失敗；檢查完把標頭還原。
  - 這個測試不在 `cargo test` 裡；CI 的「C header smoke test」步驟跑它。
- **按鍵到輸出的行為**：`core/tests/engine*.rs`，見 `docs/spec/keys-and-composition.md`。

## 10. 已知限制

- 沒有 ABI 版本號與功能探測；相容只靠同一次建置。
- panic hook 是行程全域的；把核心嵌進別的宿主行程時會取代宿主的 hook。
- 引擎 handle 不是 thread-safe，由呼叫端保證單執行緒；重排工作的未定義行為（§5.3）不做執行期檢查。
- Windows 沒有選字記憶檔（`learning_open` 回 3）；選字照常，學習只在這次執行的記憶體裡，不寫檔。
- 標頭是人讀的註解，沒有機器可讀的 ABI 描述；一致性靠 `ffi.rs` 的測試、C 冒煙測試與審查。
- `ffi.rs` 的 `shanjie_engine_new_packs` 文件註解只寫了位元 0 與 2，漏了經典台詞（值 2）；以標頭為準。
