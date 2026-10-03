# S3a 契約：核心的按鍵引擎與 C ABI

計畫見 `docs/PLAN.md` §2 S3a。本檔是實作要照的細節。排列表取自小麥注音 `Source/Engine/Mandarin/Mandarin.cpp`（MIT）的 `CreateStandardLayout`、`CreateETenLayout`，main 已逐鍵核對。

## 1. 鍵盤排列

注音符號照小麥的元件順序分三欄：聲母（ㄅ–ㄙ）、介音（ㄧㄨㄩ）、韻母（ㄚ–ㄦ）；聲調另列。表中的按鍵是**不按 Shift 的 ASCII 字元**。

| 符號 | 標準 | 倚天 | 符號 | 標準 | 倚天 | 符號 | 標準 | 倚天 |
|---|---|---|---|---|---|---|---|---|
| ㄅ | 1 | b | ㄗ | y | ; | ㄛ | i | o |
| ㄆ | q | p | ㄘ | h | ' | ㄜ | k | r |
| ㄇ | a | m | ㄙ | n | s | ㄝ | , | w |
| ㄈ | z | f | ㄧ | u | e | ㄞ | 9 | i |
| ㄉ | 2 | d | ㄨ | j | x | ㄟ | o | q |
| ㄊ | w | t | ㄩ | m | u | ㄠ | l | z |
| ㄋ | s | n | ㄚ | 8 | a | ㄡ | . | y |
| ㄌ | x | l | | | | ㄢ | 0 | 8 |
| ㄍ | e | v | | | | ㄣ | p | 9 |
| ㄎ | d | k | | | | ㄤ | ; | 0 |
| ㄏ | c | h | | | | ㄥ | / | - |
| ㄐ | r | g | | | | ㄦ | - | = |
| ㄑ | f | 7 | | | | | | |
| ㄒ | v | c | | | | | | |
| ㄓ | 5 | , | | | | | | |
| ㄔ | t | . | | | | | | |
| ㄕ | g | / | | | | | | |
| ㄖ | b | j | | | | | | |

| 聲調 | 標準 | 倚天 |
|---|---|---|
| 一聲 | 空白鍵 | 空白鍵 |
| ˊ | 6 | 2 |
| ˇ | 3 | 3 |
| ˋ | 4 | 4 |
| ˙ | 7 | 1 |

兩種排列的空白鍵都當一聲用（小麥兩種排列皆然）。

## 2. 拼音節

- 未完成音節有聲母、介音、韻母三欄，各放一個符號；同一欄再按就**取代**原本的符號。
- 按聲調鍵（含空白鍵）完成音節。音節字串的格式和詞庫、評測檔相同：聲母＋介音＋韻母＋調號，一聲不加調號，˙ 放最後（例：`ㄇㄚ˙`、`ㄕˋ`、`ㄧ`）。
- **詞庫沒有的音節不收**：完成時若 `lex.entries(&[音節])` 是空的，這個聲調鍵回報「已處理」，未完成音節保持原狀，組字區不變（使用者可用 Backspace 修正）。實測詞庫的 1,417 個音節都有單音節詞條，所以收進來的音節一定解得出來；`decode_beam` 若仍回錯，視為內部錯誤（§6 的回傳碼 4）。
- 新音節插在游標處，游標移到它右邊。

## 3. 按鍵行為（暫定，S3b 會對照使用者的截圖與實機修正）

**判斷順序**：由上往下，第一條符合的規則生效。

| # | 狀態 | 按鍵 | 行為 |
|---|---|---|---|
| 1 | 任何 | 帶 COMMAND、OPTION、CAPSLOCK，或帶 CONTROL 但不是 Ctrl+\ | 不處理（直通），**不改任何狀態** |
| 2 | 任何 | Shift＋§4 標點鍵、Ctrl+\ | 丟掉未完成音節、關閉候選，送出組字區，再送出標點 |
| 3 | 候選開啟 | 1–9 | 選目前頁的第 n 個（超出本頁則忽略），關閉候選 |
| 4 | 候選開啟 | ↑↓ | 移動選取（跨頁） |
| 5 | 候選開啟 | ←→、空白鍵 | 上一頁／下一頁；空白鍵＝下一頁，最後一頁再按回第一頁 |
| 6 | 候選開啟 | Enter | 選目前選取的，關閉候選 |
| 7 | 候選開啟 | Esc、Backspace | 關閉候選，不改變 |
| 8 | 候選開啟 | 其他鍵 | 關閉候選（不改變），再從第 9 條起處理這個鍵 |
| 9 | 有未完成音節 | 注音鍵 | 放進對應欄位 |
| 10 | 有未完成音節 | 聲調鍵、空白鍵 | 完成音節（§2） |
| 11 | 有未完成音節 | Backspace | 刪掉最後放進的符號 |
| 12 | 有未完成音節 | Esc | 清掉未完成音節 |
| 13 | 有未完成音節 | 其他鍵 | 已處理，忽略 |
| 14 | 沒有未完成音節 | 注音鍵 | 開始新的未完成音節 |
| 15 | 組字區有字 | 空白鍵、↓ | 開啟候選（§3.1） |
| 16 | 組字區有字 | 聲調鍵（空白鍵以外）、↑ | 已處理，忽略 |
| 17 | 組字區有字 | ←→、Home、End | 游標在音節之間移動 |
| 18 | 組字區有字 | Backspace／Delete | 刪掉游標左邊／右邊的音節；游標在邊界時忽略 |
| 19 | 組字區有字 | Enter | 送出組字區顯示的整句 |
| 20 | 組字區有字 | Esc | 清空組字區 |
| 21 | 組字區有字 | 其他鍵（含 Shift＋非標點、Tab） | 送出組字區，然後直通這個鍵（`handled = 0`、`commit` 非空） |
| 22 | 組字區空 | 其他鍵 | 不處理（直通） |

- 「注音鍵」與「聲調鍵」指 §1 表中的鍵、且沒按 Shift。
- 第 1 條的直通不改任何狀態：一段組字中間插入任意個第 1 條的鍵，之後的輸出必須和沒按過時逐欄位相同。
- 中英切換（Shift 單按）的狀態放在殼裡：英文模式下殼不把按鍵送進核心。
- **組字區上限 40 個音節**：完成第 40 個音節時自動送出整段，限制每鍵延遲與文字留在記憶體的時間。開發集最長一列是 32 個音節，重播測試不會碰到上限。
- **送出、Esc 清空、reset（§6）時，連同固定詞清掉所有組字狀態。**

### 3.1 候選與固定詞

- **候選範圍**：設 a＝游標位置；a 為 0 時改用「從位置 0 開始」。列出讀音等於 `音節[a−L..a]`（a 為 0 時是 `音節[0..L]`）的所有詞，L 從 `min(max_len, 可用長度)` 遞減到 1；同一個 L 內照 `lex.entries` 的順序（詞庫分數由高到低）；同一個字串只出現第一次。
- **分頁**：每頁 9 個。開啟時在第一頁、選取第 0 個。
- **固定詞**：選了長度 L 的詞 w，就把區間 `[a−L, a)`（或 `[0, L)`）固定成 w。與既有固定詞重疊的，舊的移除。游標不動。
- **重算**：組字區有變動就重算顯示字串。固定詞把音節切成幾段，每段空白區間各自用 `decode_beam(lex, 段, &mut NoLearning, BEAM_S1)` 取第一名，再和固定詞依序串接。現行解碼是 unigram，分數可加，所以這和「整句解碼、限制路徑經過固定詞」的第一名相同。
- **固定詞隨編輯調整**：在游標插入或刪除音節時，完全在左邊的固定詞不動，完全在右邊的跟著平移，跨過該位置的移除。
- 這片只用核心現有的 unigram 解碼與 `NoLearning`；S2 的語言模型與 S4 的學習之後再接上。

## 4. 中文標點（本專案自訂，不複製 Apple 的資料檔）

| 按鍵 | 輸出 | 按鍵 | 輸出 |
|---|---|---|---|
| Shift+, | ， | Shift+[ | 「 |
| Shift+. | 。 | Shift+] | 」 |
| Shift+/ | ？ | Shift+9 | （ |
| Shift+1 | ！ | Shift+0 | ） |
| Shift+; | ： | Shift+` | ～ |
| Ctrl+\ | 、 | | |

## 5. 資料載入

`data_dir` 裡必須有 `mcbpmf-data.txt` 與 `overlay-add.tsv`，用 `Lexicon::parse_with(基底, Some(疊加層))` 載入，和評測 CLI 預設相同。任一個不存在或解析失敗，回傳碼 3。

## 6. C ABI（`core/include/shanjie.h`）

```c
typedef struct { uint32_t kind; uint32_t ch; uint32_t modifiers; } ShanjieKey;
// kind：1 CHAR、2 SPACE、3 ENTER、4 BACKSPACE、5 DELETE、6 ESC、7 LEFT、8 RIGHT、9 UP、10 DOWN、11 HOME、12 END、13 TAB
// ch：kind 為 CHAR 時的 Unicode scalar（不含 Shift 的鍵帽字元）；其他 kind 時忽略
// modifiers：bit0 SHIFT、bit1 CONTROL、bit2 OPTION、bit3 COMMAND、bit4 CAPSLOCK
typedef struct {
  int32_t handled;            // 1 = 引擎處理了，殼不要再送給 App；0 = 直通（先插入 commit，再放行按鍵）
  const char *commit;         // UTF-8，要立刻插入的文字，可為空字串，不為 NULL
  const char *preedit;        // UTF-8，組字區顯示字串（未完成音節的注音插在游標處），不為 NULL
  uint32_t cursor_utf16;      // 游標在 preedit 中的位置，單位是 UTF-16 code unit（給 NSRange 用）；在未完成音節之後
  uint32_t candidate_count;   // 目前這一頁的候選數（0–9）
  const char *const *candidates; // candidate_count 為 0 時是 NULL
  int32_t candidate_selected; // 目前頁內的選取位置；沒開候選時為 -1
} ShanjieOutput;
typedef struct ShanjieEngine ShanjieEngine;

int32_t shanjie_engine_new(const char *data_dir, uint32_t layout, ShanjieEngine **out); // layout 0 標準、1 倚天
void    shanjie_engine_free(ShanjieEngine *engine);
int32_t shanjie_engine_key(ShanjieEngine *engine, ShanjieKey key, ShanjieOutput **out);
int32_t shanjie_engine_reset(ShanjieEngine *engine, uint32_t mode, ShanjieOutput **out); // mode 0 送出後清空、1 丟棄
void    shanjie_output_free(ShanjieOutput *output);
```

- **回傳碼**：0 成功、1 必要的指標是 NULL、2 輸入不合法（data_dir 不是 UTF-8、`ch` 不是合法的 Unicode scalar、layout 或 mode 超出範圍）、3 資料載入失敗、4 內部錯誤（攔下的 panic 或解碼錯誤）。
- **reset**：mode 0 時 `commit` 是目前組字區的顯示字串（未完成音節丟掉）；mode 1 時 `commit` 為空。兩者都清掉所有組字狀態，之後的輸出必須和新建的 engine 相同。殼在 `commitComposition:`、`deactivateServer`、換 client、privacyGate 轉為生效、Caps Lock 打開時呼叫（S3b 決定用哪個 mode）。
- **記憶體與生命週期**：
  - 回傳非 0 時，`*out` 一律設成 NULL，而且不配置任何記憶體。
  - `shanjie_engine_free(NULL)`、`shanjie_output_free(NULL)` 什麼都不做。重複釋放、或釋放不是核心配置的指標，屬於未定義行為。
  - `ShanjieOutput` 擁有自己的字串副本，有效期到 `shanjie_output_free` 為止，不受之後的 `engine_key`、`engine_reset`、`engine_free` 影響。
  - handle 不是 thread-safe：所有呼叫都在同一個執行緒（IMK 主執行緒）。
  - 回傳碼 4 之後，engine 自動 reset（mode 1），丟掉可能只改了一半的組字。
  - 殼收到非 0 回傳時，當作「不處理」直通，而且不記錄這個按鍵。
- **R2 規則**：
  - 每個匯出函式都包 `catch_unwind`。`ffi.rs` 加 `#[cfg(panic = "abort")] compile_error!(…)`，讓 release 設定改成 abort 時建置失敗。
  - 匯出函式的入口用 `std::sync::Once` 安裝不印任何東西的 panic hook（`|_| {}`）；不在 `Engine::new` 裡裝，引擎單元測試仍保有 panic 訊息。
  - 錯誤訊息與 stderr 不得含任何輸入或資料內容。引擎與 FFI 不用 byte 位移切 `str`（用 `char_indices` 或已知邊界）；建 `CString` 失敗走錯誤碼、不 `unwrap`；裝著輸入文字的型別不 `derive(Debug)`；`expect`、`assert`、`unreachable` 只用靜態字串。
- **`shanjie.h` 註解要寫給 S3b**：不得用 `NSLog`、`print`、`debugPrint`、`dump` 處理輸出內容；`fatalError`、`precondition` 不得內插輸入文字；收到輸出後在同一個呼叫裡複製成 Swift `String` 並立刻 `shanjie_output_free`。
- **左文不在這片**：S3a 的解碼不讀左文，ABI 也不收。R4（截斷、計數單位、上限）移到第一個讀左文的切片；屆時 staticlib 和殼一起重新建置即可改 ABI。

## 7. 測試

### 7.1 引擎行為（`core/tests/engine*.rs`）

- 排列表：兩種排列各自涵蓋 37 個注音符號與 5 個聲調鍵。
- §3 表的每一條至少一個測試；第 1 條用「插入直通鍵前後輸出逐欄位相同」驗證。
- 詞庫沒有的音節（例：標準排列的 ㄅㄩ）被拒、組字區不變。
- 候選：範圍、排序、分頁、選字後固定、固定詞在插入與刪除時的平移與移除。
- reset 後的輸出和新建的 engine 相同（兩種 mode）。
- 組字區上限：第 40 個音節自動送出。

### 7.2 重播整合測試

- 資料：和 `--set dev --limit 302` 相同的 302 列（`load_set` 的檔案順序、經 `usable()` 過濾後取前 302 列；沒有讀音欄的列用 `to_syllables`）。
- 每列的讀音依排列轉成按鍵：每個音節依聲母、介音、韻母順序按符號鍵，再按聲調鍵（一聲用空白鍵）；最後按 Enter。
- 預期：送出的字串＝`decode_beam(lex, 讀音, &mut NoLearning, BEAM_S1)` 的第一名，302／302。標準與倚天兩種排列都要跑。
- 這個測試只證明「按鍵 → 音節 → 解碼」這條路正確；選字、游標、刪除由 §7.1 負責。
- 每鍵處理時間：release 建置，p95 < 16 ms。

### 7.3 FFI（`ffi.rs` 內部單元測試）

- **R2 panic**：用子行程跑（`std::process::Command` 執行 `current_exe()`，以環境變數指定子測試，並加 `--nocapture --exact`，否則 libtest 會攔下輸出，測試變成空包彈）。子行程經 `shanjie_engine_key` 輸入一段標記字串，觸發一個 `#[cfg(test)]` 的注入點，panic payload 必須含該標記。斷言：回傳碼 4、`*out` 為 NULL、子行程真正的 stdout 與 stderr 都不含標記、下一鍵回傳 0 且組字區為空。
- **正向對照**：同一個子行程改用預設 hook 時，stderr 必須看得到標記；看不到就代表測試沒測到東西。
- **經 C ABI 的重播**：在子行程裡經 C ABI 跑完 §7.2 的前 20 列，結果與 §7.2 相同。子行程的 stdout、stderr 除了 libtest 自己的 harness 行（`running 1 test`、`test … ok`、`test result: …` 與空行）之外沒有其他內容，而且不含這 20 列任何一列的句子或讀音字串。
- 兩個 free 函式傳 NULL 不崩潰；各種錯誤碼的情境回傳正確的碼且 `*out` 為 NULL。

### 7.4 C 標頭冒煙測試（`core/tests/c/abi_smoke.c`）

`cargo test` 只從 Rust 呼叫匯出函式，看不到 `shanjie.h` 與 Rust 結構是否一致；S3b 連結的是這個標頭，所以要用 C 編譯器實際編譯、連結一次。不加 crate，用系統的 `cc`。

- 驅動程式 `abi_smoke.c` 只 include `shanjie.h`，參數是資料目錄。對兩種排列各做一次：
  - `shanjie_engine_new(data_dir, layout, &e)` 回 0。
  - 依排列送出「你好」的按鍵（標準 `s u 3 c l 3`、倚天 `n e 3 h z 3`），每鍵都 `shanjie_output_free`；送 Enter 後檢查 `handled == 1`、`commit` 是「你好」、`preedit` 是空字串、`cursor_utf16 == 0`、`candidate_count == 0`、`candidates == NULL`、`candidate_selected == -1`。
  - 再送 `ㄋㄧˇ` 的按鍵和空白鍵，檢查候選已開：`candidate_count` 在 1–9、`candidates[0]` 不是 NULL、`candidate_selected == 0`；接著 `shanjie_engine_reset(e, 1, &o)`，檢查 `commit` 為空字串、`preedit` 為空字串。
  - `shanjie_engine_free(e)`；最後呼叫 `shanjie_engine_free(NULL)`、`shanjie_output_free(NULL)`。
  - 全部通過時 exit 0，任何一項不符時印出項目編號（不印任何字串內容）並 exit 1。
- 指令（在 repo 根目錄，`$T` 是暫存目錄）：

  ```sh
  cargo build --release -p core
  LIBS=$(cargo rustc --release -p core --crate-type staticlib -- --print native-static-libs 2>&1 | sed -n 's/.*native-static-libs: //p')
  cc -std=c11 -Wall -Wextra -Werror -Icore/include core/tests/c/abi_smoke.c target/release/libcore.a $LIBS -o "$T/abi_smoke"
  "$T/abi_smoke" data/lexicon
  ```

  連結參數以 `--print native-static-libs` 實際印出的為準；若和上面的寫法不同，executor 照實際值改指令並寫回本節。
- 這個測試不在 `cargo test` 裡；由 security-executor 執行一次並回報輸出，verifier 再獨立執行。
- **反向檢查**（verifier 做）：把 `shanjie.h` 裡 `ShanjieOutput` 的 `commit` 和 `preedit` 兩個欄位對調後重新編譯，驅動程式必須失敗；檢查完把標頭還原。

## 8. 實作決定

契約沒寫、`engine.rs` 實作時自行決定的事（S3b 對照截圖時可改）：

- **API 形狀**：`Engine::new(data_dir, Layout)` 讀檔，`Engine::with_lexicon(Arc<Lexicon>, Layout)` 共用已載入的詞庫（測試每個測試檔只載入一次）；`load_lexicon(data_dir)` 單獨公開。`Key { kind: KeyKind, ch: char, modifiers: u32 }`（`KeyKind::from_code` 把 ABI 的 1–13 轉成列舉，`ch` 的合法性由 `ffi.rs` 先擋）。`Output` 欄位與 §6 一一對應（`selected: Option<usize>`、`candidates: Vec<String>`）。錯誤只有 `EngineError::LoadFailed`（碼 3）與 `Internal`（碼 4）；`key` 回 `Internal` 之前引擎已自行清空（等同 reset 模式 1）。`reset(mode)` 不會失敗。
- **R2**：`Key`、`Output`、`Engine` 都不 `derive(Debug)`；`Output` 只 derive `PartialEq`，測試用 `assert!(a == b)`。
- **Ctrl+\ 的判定**：只有「修飾鍵恰為 CONTROL、字元為 `\`」算 Ctrl+\；Ctrl+Shift+\ 屬第 1 條（直通）。標點鍵只認「修飾鍵恰為 SHIFT」。
- **第 1 條的輸出**：直通時回傳 `handled = 0`、`commit` 為空，其餘欄位是目前狀態的快照（狀態不變）。第 22 條直通同理。
- **取代後的 Backspace（第 11 條）**：同一欄被取代時，新符號算「最後放進」；Backspace 先刪它，不會還原舊符號。
- **候選邊界**：↑↓ 在第一個／最後一個停住不繞回；←→ 在第一頁／最後一頁停住（換頁後選取回到該頁第 0 個）；只有空白鍵會從最後一頁繞回第一頁。候選開啟時 Esc／Backspace 是「已處理」（第 7 條），不會落到第 12 條以後。候選為空（不會發生，因每個音節都有詞條）時不開啟。
- **第 8 條**：「其他鍵」＝第 3–7 條沒列到的所有鍵（含 Delete、Home、End、Tab、Enter 以外的字元；注意 Enter 屬第 6 條）。候選已關閉後，該鍵接著照第 9 條起處理，所以 Home 會真的把游標移到 0。
- **游標在 preedit 的位置**：假設每個音節對應顯示字串的一個字元（詞庫載入時已過濾字數不等的詞；疊加層未過濾，若有不符則游標位置夾在字串長度內）。`cursor_utf16` ＝游標前的字元＋未完成音節的 UTF-16 長度。
- **固定詞**：選字後游標不動；固定詞範圍以音節為單位；插入發生在固定詞左邊界（`start == 游標`）時整段右移，發生在右邊界（`end == 游標`）時不動；刪除的音節落在 `[start, end)` 內就移除。
- **自動送出**：第 40 個音節完成後（`syls.len() >= 40`）立刻送出整段並清空，`handled = 1`。
- **解碼成本**：每次組字區變動都重算所有空白段（沒做快取）；重播測試 8,336 鍵的 p95 約 1.2 ms、最大約 9 ms（release，標準與倚天相近）。
