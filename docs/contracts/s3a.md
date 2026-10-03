# S3a 契約：核心的按鍵引擎與 C ABI

計畫見 `docs/PLAN.md` §2 S3a。本檔是實作要照的細節。排列表取自小麥注音 `Source/Engine/Mandarin/Mandarin.cpp`（MIT）的 `CreateStandardLayout`、`CreateETenLayout`，main 已逐鍵核對。

## 1. 鍵盤排列

注音符號照小麥的元件順序分四欄：聲母（ㄅ–ㄙ）、介音（ㄧㄨㄩ）、韻母（ㄚ–ㄦ）、聲調。表中的按鍵是**不按 Shift 的 ASCII 字元**。

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

## 2. 拼音節

- 四欄各放一個符號；同一欄再按就**取代**原本的符號。
- 按聲調鍵（含一聲的空白鍵）完成音節：音節加進組字區，游標後移。
- 只有聲調、沒有任何注音符號時，聲調鍵不處理（直通）。

## 3. 按鍵行為（暫定，S3b 會對照使用者的截圖與實機修正）

| 狀態 | 按鍵 | 行為 |
|---|---|---|
| 有未完成音節 | 注音鍵 | 放進對應欄位 |
| 有未完成音節 | 聲調鍵／空白鍵 | 完成音節 |
| 有未完成音節 | Backspace | 刪掉最後放進的符號 |
| 有未完成音節 | Esc | 清掉未完成音節 |
| 沒有未完成音節、組字區有字 | 空白鍵、↓ | 開啟候選：游標所在位置往左、讀音相同的所有詞，依核心分數排序 |
| 同上 | ←→ | 游標在音節之間移動 |
| 同上 | Backspace／Delete | 刪掉游標左邊／右邊的音節 |
| 同上 | Enter | 送出組字區顯示的整句 |
| 同上 | Esc | 清空組字區 |
| 候選開啟 | 1–9 | 選第 n 個，換掉游標處的詞，關閉候選 |
| 候選開啟 | ↑↓ | 移動選取；←→ 換頁 |
| 候選開啟 | Enter | 選目前選取的 |
| 候選開啟 | Esc | 關閉候選，不改變 |
| 任何狀態 | 標點（見 §4） | 先送出組字區，再送出標點 |
| 任何狀態 | Ctrl+\ | 先送出組字區，再送出「、」 |
| Caps Lock 開啟 | 任何鍵 | 不處理（直通），不暫存 |
| 組字區空、沒有未完成音節 | 非注音的鍵 | 不處理（直通） |

使用者選了一個候選，該處的詞就固定下來；之後重算組字區時保留這個選擇。這時只用核心現有的 `decode`，學習屬於 S4。

## 4. 中文標點（本專案自訂，不複製 Apple 的資料檔）

| 按鍵 | 輸出 | 按鍵 | 輸出 |
|---|---|---|---|
| Shift+, | ， | Shift+[ | 「 |
| Shift+. | 。 | Shift+] | 」 |
| Shift+/ | ？ | Shift+9 | （ |
| Shift+1 | ！ | Shift+0 | ） |
| Shift+; | ： | Shift+` | ～ |
| Ctrl+\ | 、 | | |

## 5. 解碼

組字區有變動就呼叫核心的 `decode_beam(…, BEAM_S1)` 取第一名（基底＋疊加層，現行的 unigram），套上使用者選過的固定詞。S2 的語言模型完成後再接上，介面預留設定欄位。

## 6. C ABI（`core/include/shanjie.h`）

```c
typedef struct { uint32_t kind; uint32_t ch; uint32_t modifiers; } ShanjieKey;
// kind：1 CHAR、2 SPACE、3 ENTER、4 BACKSPACE、5 DELETE、6 ESC、7 LEFT、8 RIGHT、9 UP、10 DOWN、11 HOME、12 END、13 TAB
// ch：kind 為 CHAR 時的 Unicode scalar（不含 Shift 的鍵帽字元）
// modifiers：bit0 SHIFT、bit1 CONTROL、bit2 OPTION、bit3 COMMAND、bit4 CAPSLOCK
typedef struct {
  int32_t handled;            // 1 = 引擎處理了，殼不要再送給 App；0 = 直通
  const char *commit;         // UTF-8，要立刻插入的文字，可為空字串
  const char *preedit;        // UTF-8，組字區顯示字串（含未完成音節的注音）
  uint32_t cursor_utf16;      // 游標在 preedit 中的位置，單位是 UTF-16 code unit（給 NSRange 用）
  uint32_t candidate_count;
  const char *const *candidates;
  int32_t candidate_selected; // 沒開候選時為 -1
} ShanjieOutput;
typedef struct ShanjieEngine ShanjieEngine;

int32_t shanjie_engine_new(const char *data_dir, uint32_t layout, ShanjieEngine **out); // layout 0 標準、1 倚天
void    shanjie_engine_free(ShanjieEngine *engine);
int32_t shanjie_engine_key(ShanjieEngine *engine, ShanjieKey key, const char *left_context, ShanjieOutput **out);
void    shanjie_output_free(ShanjieOutput *output);
```

- 回傳碼：0 成功、1 參數是 NULL、2 不是合法 UTF-8、3 資料載入失敗、4 內部 panic（已攔下）。
- 規則：
  - 每個匯出函式都包 `catch_unwind`；panic hook 不印 payload。
  - 錯誤訊息與 stderr 不得含任何輸入或資料內容（R2）。
  - 輸出由核心配置，殼用 `shanjie_output_free` 釋放。
- 左文（R4）：在最後一個換行處截斷，以 grapheme 計數取最右邊 64 個；只存在這次呼叫的記憶體中。

## 7. 重播整合測試

- 資料：開發集前 302 列。每列的讀音依排列轉成按鍵：每個音節是符號鍵依聲母、介音、韻母順序，再加聲調鍵（一聲用空白鍵）；最後按 Enter。
- 預期：引擎送出的字串＝`decode_beam(讀音, BEAM_S1)` 的第一名（基底＋疊加層），302／302。標準與倚天兩種排列都要跑。
- 每列都傳入該列的前文當左文。
