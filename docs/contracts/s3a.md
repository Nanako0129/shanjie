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
// S2c 新增（docs/PLAN.md §S2c）
int32_t shanjie_engine_load_lm(ShanjieEngine *engine, const char *path);               // 不改目前的組字區顯示
int32_t shanjie_engine_set_profile(ShanjieEngine *engine, uint32_t profile, ShanjieOutput **out); // 0 chat（預設）、1 formal；重算組字區並回傳快照
```

- **回傳碼**：0 成功、1 必要的指標是 NULL、2 輸入不合法（data_dir 或 LM 路徑不是 UTF-8、`ch` 不是合法的 Unicode scalar、layout、mode 或 profile 超出範圍）、3 資料載入失敗（含 LM 檔讀取或格式錯誤、引擎沒有 data_dir）、4 內部錯誤（攔下的 panic 或解碼錯誤）。`load_lm` 失敗時 LM 維持原狀；碼 4 時任何函式都照下面的規則丟棄組字。
- **reset**：mode 0 時 `commit` 是目前組字區的顯示字串（未完成音節丟掉）；mode 1 時 `commit` 為空。兩者都清掉所有組字狀態，之後的輸出必須和新建的 engine 相同；S2c 起是「新建、載入相同 LM、使用相同設定的 engine」，reset 不清 LM 與設定。殼在 `commitComposition:`、`deactivateServer`、換 client、privacyGate 轉為生效、Caps Lock 打開時呼叫（S3b 決定用哪個 mode）。
- **記憶體與生命週期**：
  - 回傳非 0 時，`*out` 一律設成 NULL，而且不配置任何記憶體。
  - `shanjie_engine_free(NULL)`、`shanjie_output_free(NULL)` 什麼都不做。重複釋放、或釋放不是核心配置的指標，屬於未定義行為。
  - `ShanjieOutput` 擁有自己的字串副本，有效期到 `shanjie_output_free` 為止，不受之後的 `engine_key`、`engine_reset`、`engine_set_profile`、`engine_load_lm`、`engine_free` 影響。
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
- **S2c**：子行程測試 `child_lm`／`load_lm_and_set_profile_codes` 用手工的小 SJLM 檔，涵蓋 load_lm 的碼 1、2、3 與失敗時保留原本的 LM、set_profile 的碼 1、2、4（注入 panic）與成功快照、reset 保留 LM 與設定，並檢查子行程輸出不含暫存路徑。

### 7.4 C 標頭冒煙測試（`core/tests/c/abi_smoke.c`）

`cargo test` 只從 Rust 呼叫匯出函式，看不到 `shanjie.h` 與 Rust 結構是否一致；S3b 連結的是這個標頭，所以要用 C 編譯器實際編譯、連結一次。不加 crate，用系統的 `cc`。

- 驅動程式 `abi_smoke.c` 只 include `shanjie.h`，參數是資料目錄（S2c 起加第二個參數：LM 路徑，檢查 301–317 驗證 load_lm、set_profile 與 reset 保留 LM，見 PLAN §S2c 驗收 6）。對兩種排列各做一次：
  - `shanjie_engine_new(data_dir, layout, &e)` 回 0。
  - 依排列送出「你好」的按鍵（標準 `s u 3 c l 3`、倚天 `n e 3 h z 3`），每鍵都 `shanjie_output_free`；送 Enter 後檢查 `handled == 1`、`commit` 是「你好」、`preedit` 是空字串、`cursor_utf16 == 0`、`candidate_count == 0`、`candidates == NULL`、`candidate_selected == -1`。
  - 再送 `ㄋㄧˇ` 的按鍵和空白鍵，檢查候選已開：`candidate_count` 在 1–9、`candidates[0]` 不是 NULL、`candidate_selected == 0`；接著 `shanjie_engine_reset(e, 1, &o)`，檢查 `commit` 為空字串、`preedit` 為空字串。
  - `shanjie_engine_free(e)`；最後呼叫 `shanjie_engine_free(NULL)`、`shanjie_output_free(NULL)`。
  - 全部通過時 exit 0，任何一項不符時印出項目編號（不印任何字串內容）並 exit 1。
- 指令（在 repo 根目錄，`$T` 是暫存目錄）：

  ```sh
  cargo build --release -p core
  # zsh 不拆 $LIBS 這種變數，所以命令替換直接放進 cc 的參數（sh、bash、zsh 都會拆）。
  # 2026-10-03 實際印出 `-lSystem -lc -lm`；ld 會警告 `ignoring duplicate libraries: '-lSystem'`，無害。
  cc -std=c11 -Wall -Wextra -Werror -Icore/include core/tests/c/abi_smoke.c target/release/libcore.a \
    $(cargo rustc --color never --release -p core --crate-type staticlib -- --print native-static-libs 2>&1 | sed -n 's/.*native-static-libs: //p') \
    -o "$T/abi_smoke"
  "$T/abi_smoke" data/lexicon data/lm/bigram.sjlm
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
- **C ABI 的輸入檢查（`ffi.rs`）**：`kind` 不在 1–13 時回傳 2（同「輸入不合法」）；`modifiers` 的未定義位元（bit5 以上）不擋，照原樣交給引擎。輸出字串含 NUL 而無法建 `CString` 時回傳 4，engine 先丟棄組字再返回。
- **panic hook 是行程全域的**：靜音 hook 會取代宿主行程原有的 hook。S3b 的輸入法行程只有這個函式庫，所以沒有影響；呼叫匯出函式的測試都在子行程裡跑，以免影響同一個測試執行檔裡其他測試的 panic 訊息。

## 9. S2c 實作決定

契約（`docs/PLAN.md` §S2c）沒寫、`core/src/lm.rs` 與引擎接 LM 時自行決定的事：

- **模型讀取（`Lm::load`／`Lm::parse`）**：除了魔數、各段長度、無多餘位元組，還檢查查表依賴的不變式（詞彙依 UTF-8 位元組遞增、前文與下一詞 id 遞增且小於 V、偏移量遞增且落在範圍內、前文總次數非 0），所以任何查表都不會越界。錯誤只有 `LmError::Io`、`LmError::Format`，不帶任何內容。`back(v)` 在載入時依檔案順序累加後除一次，存成陣列。
- **查表**：詞彙用位元組二分搜尋（id 0、1 是 `<s>`、`</s>`），前文與下一詞用二分搜尋，沒有雜湊表。
- **`pow(10, lp)` 的坑**：Rust 的 `10f64.powf(lp)` 在底數是常數時，LLVM 會改寫成 `exp10`，末位和 Python 的 `10 ** lp`（libm `pow`）差 1 ulp（formal 的 dump 有 2 列的分數差約 3.6e-15）。`lm.rs` 的 `pow10` 用 `black_box` 藏起底數，改後 chat、formal 的 `--dump` 與 Python 逐位元組相同。
- **上限後詞庫（`CappedLexicon::new(Arc<Lexicon>, overlay 文字, &Lm)`）**：與原詞庫共用字串池與讀音表（只多一份 `ents` 陣列，每筆 16 bytes），每個讀音的詞條範圍不變、只在範圍內重排。疊加層詞集是 `overlay-add.tsv` 每行第二欄，與 `lm_eval.py` 相同。CLI 與 `Engine::load_lm` 都呼叫它。
- **解碼**：`decode_segment(lex, 音節, lm, λ, 前文詞, End, beam)` 是唯一的實作；`decode` 是前文 `<s>`、`End::Eos` 的特例。每個候選的 `pb` 與詞 id 在迴圈外算一次，只是提出共同項，沒有改運算順序。路徑同時帶著每個詞實際用的 lp，供 `total_score` 使用。
- **引擎**：`Engine::new` 記住 `data_dir`；`load_lm(path)` 失敗時（含沒有 `data_dir`）維持原狀並回 `LoadFailed`，成功時不重算顯示。`set_lm(Arc<Lm>, Arc<CappedLexicon>)` 是 `with_lexicon` 引擎專用的注入（測試用）。`set_profile(Profile)` 先記住再重算，解碼失敗時引擎自行清空並回 `Internal`；沒載入 LM 時顯示不變。`Profile::from_code` 供 step 2 的 ABI 使用（0 chat、1 formal）。
- **`lp_F`**：`CappedLexicon::best_lp(讀音, 詞)`，同讀音下同字串取最大值；找不到（不會發生，候選就來自這個讀音）視為內部錯誤。固定詞之間沒有空白區間時，不需要特別處理：兩個固定詞各自以「前一詞」計分，這件事只出現在 `total_score`（沿路徑逐詞累加）。
- **`total_score()`**：沿目前最佳路徑（`refresh_lm` 同時存下的 `(詞, lp)`）依序加 `word(λ, 前一詞, 詞, lp)`，最後加 `eos`；沒有 LM 或組字區空時回 `None`。
- **reset**：`clear_all` 只清組字狀態（含路徑），不碰 `lm`、`profile`、`data_dir`。
- **評測 CLI 的資料來源**：`--dev N` 與 `--rows` 照 `lm_eval.py` 的 `rows_of`，只收恰好三欄的列，不套 `usable`（dev 的 390 列都是三欄，與 S3a 的 302 列相同）；`--set holdout` 走既有的 `load_set`（`usable` 加 `row_syllables`）。`--name` 缺省時 `--rows` 用檔名。`top1_sha256` 用 `core::eval::sha256_hex`（專案沒有雜湊 crate，自寫 FIPS 180-4，附已知向量測試）。
- **逐分數對照（驗收 3）的指令**：`python3 reference/proto/lm_eval.py --lm data/lm/bigram.sjlm --profile chat --dev 302 --dump $T/chat.dump`，對 `shanjie-eval --lm … --profile chat --dev 302 --dump $T/r-chat.dump`，formal 同理；`cmp` 兩邊檔案（結果是位元組相同）。這個比對需要 Python，所以沒放進 `cargo test`；`cargo test` 涵蓋的是 4 組參數的摘要行（含 `top1_sha256`）與 `s2-lm-dev302-top1.tsv`。
- **beam 近似**：驗收 5(a) 的廣度測試（前 12 列、兩種設定、每個詞各固定一次）有 2 個詞因 beam＝64 的近似得到不同整句，測試容許至多 2 個；驗收用的案例（疊加層詞五個、5(b) 兩個）都精確相等。
- **S2c：set_profile 回傳碼 4 時**（引擎呼叫成功之後才 panic 或轉 C 字串失敗），新的設定保留、只丟棄組字；§6 只要求丟棄組字，這是刻意的：設定是殼的意圖，組字才是可能只改一半的狀態。load_lm 的碼 4 只能由 panic 觸發，沒有測試。
