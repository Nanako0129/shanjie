# 按鍵、組字區與送出

這份寫的是現在的行為。

範圍：核心（`core/src/engine.rs`）怎麼把按鍵變成組字區、候選與送出的字串，加上殼（`macos/`）裡直接影響這些行為的三件事：Enter 等語意校正、標點表與名稱表的讀取、單按 Shift 切換中英。涵蓋鍵盤排列、拼音節、按鍵規則表、組字區上限與長段前段固定、候選與固定詞（含改選後其他字不動）、送出、標點、單按 Shift。

不在這份：英文段、英文 token、數字列與回退（`docs/spec/english-mixed.md`）、預測列與可省略韻母（`docs/spec/prediction.md`）、選字記憶（`docs/spec/learning.md`）、解碼與模糊輸入（`docs/spec/decoder.md`）、語意校正（`docs/spec/semantic-rerank.md`）、候選窗的畫法與 IMK 回呼（`docs/spec/macos-shell.md`）、C 介面（`docs/spec/c-abi.md`）。這些地方在規則表裡只留一句和指向。

## 1. 名詞與資料流

- **單位**：組字區裡佔一格的東西。音節、標點、英文 token、數字串 token、空白單位都是單位。核心用一個讀音陣列 `syls` 存（標點的讀音是 `_punct_` 加那個字元），游標在單位之間。
- **未完成音節**：還沒按聲調鍵的注音符號，放在游標處，不算單位。
- **固定詞**（`Fixed`）：把 `[start, end)` 的音節固定成一個字串，解碼不動它。有三種來源：標點、英文 token、空白單位（長度 1，永遠存在）；手動改選的詞；凍結詞（`frozen`，改選後留在原處的詞、長段的前段、刪除時拆出的單字）。
- **畫面字串**（`display`）：固定詞和自由段的解碼結果串起來；未完成音節的符號在輸出時才插在游標處。`preedit` 是兩者合起來，`cursor_utf16` 是游標在 `preedit` 裡的位置（UTF-16 code unit），在未完成音節的後面。
- 每鍵一次：殼把一個按鍵交給 `shanjie_engine_key`，核心回一個輸出（`handled`、`commit`、`preedit`、候選欄位）。非 0 的回傳碼表示按鍵直通、不記錄（`docs/spec/c-abi.md`）。

## 2. 鍵盤排列

### 2.1 注音鍵與聲調鍵

排列取自小麥注音（MIT）的標準與倚天兩種。符號分三欄：聲母（ㄅ–ㄙ，21 個）、介音（ㄧㄨㄩ，3 個）、韻母（ㄚ–ㄦ，13 個）；共 37 個符號。表中的鍵是不按 Shift 的 ASCII 字元。

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

- 程式裡的表是 `KEYS_STANDARD`、`KEYS_ETEN`（37 個鍵，順序對應 `SYMBOLS`）與 `TONE_KEYS_STANDARD`（`6 3 4 7`）、`TONE_KEYS_ETEN`（`2 3 4 1`）。
- 「注音鍵」「聲調鍵」都指沒按 Shift 的鍵。兩種排列的空白鍵都是一聲。
- 倚天排列的 `'` 與 `=` 是注音鍵，標準排列不是（§8.1 的單按標點因此只在不是注音鍵時成立）。
- 驗證：`core/tests/engine.rs` 的 `layout_standard_covers_37_symbols_and_5_tones`、`layout_eten_covers_37_symbols_and_5_tones`。

### 2.2 殼送來的按鍵

- 殼（`KeyMap`）只讀實體鍵碼和修飾旗標，不讀 `event.characters`：鍵碼對到 ANSI 排列的不按 Shift 的字元，所以鍵盤配置和死鍵不會改變核心收到的東西。唯一的例外是單按 Shift 切成英數狀態且組字區有字時，可印字元改送 `event.characters` 的字元（§9.2）。
- 特殊鍵：Return（36）與數字鍵盤 Enter（76）都是 Enter；空白、Backspace（51）、Forward Delete（117）、Esc、四個方向鍵、Home、End、Tab。
- Page Up（116）、Page Down（121）不在一般的表裡；殼只在最後一次輸出的 `candidate_vertical` 為 1（直排候選窗開著）而且沒有任何修飾鍵（含 Caps Lock）時，才送成 kind 14、15。
- 兩張表都沒有的鍵（數字鍵盤的數字、功能鍵等）不進核心：組字中時殼先送出組字區（`reset` 模式 0），再讓這個鍵直通。
- 修飾旗標：bit0 Shift、bit1 Control、bit2 Option、bit3 Command、bit4 Caps Lock。
- 驗證：`KeyMapTests`（見 `docs/spec/macos-shell.md`）。

## 3. 拼音節

### 3.1 未完成音節

- 未完成音節有聲母、介音、韻母三欄，各放一個符號；同一欄再按一個注音鍵就取代原本的符號。
- 顯示順序固定是聲母、介音、韻母，與打字順序無關。Backspace 依顯示順序從後面刪：韻母、介音、聲母。例：打 ㄉㄨㄟ，改打 ㄅ 取代 ㄉ，Backspace 兩次依序得到 ㄅㄨ、ㄅ（與蘋果注音、小麥注音實測相同）。
- 按聲調鍵（含空白鍵）完成音節。音節字串的格式和詞庫、評測檔相同：聲母加介音加韻母加調號，一聲不加調號，˙ 放最後（例 `ㄇㄚ˙`、`ㄕˋ`、`ㄧ`）。
- 新音節插在游標處，游標移到它右邊。

### 3.2 詞庫沒有的音節不收

- 完成時若詞庫對這個單音節的詞條是空的，這個聲調鍵回報「已處理」，未完成音節保持原狀，組字區不變（Backspace 可以修正）。詞庫裡每個音節都有單音節詞條，所以收下的音節一定解得出來；解碼仍回錯時是內部錯誤（C ABI 的碼 4）。
- 兩個例外都在 `finish_syllable`，順序固定：先問鄰鍵，再問回退。

### 3.3 例外：鄰鍵打錯與回退

- **鄰鍵打錯**（`set_fuzzy` 的位元 4）：位元 4 開著、已載入 n-gram 語言模型、標準排列（倚天排列下沒有作用）、這個音節不在詞庫而且有鄰鍵替代讀音（同一個鍵盤列左右各一鍵）時，收下這個音節：存打的讀音，畫面照常顯示解出的字；解碼時用替代讀音，分數減 2（`DELTA_ADJACENT`）。之後把位元 4 關掉，已收下的音節照樣解碼。沒有替代讀音、沒有語言模型、位元 4 關著或倚天排列時照上面「不收」。
- **回退成英文**（`set_english(1)`、標準排列、可省略韻母關）：鄰鍵沒有收下時，空白鍵完成詞庫沒有的音節會把按鍵還原成一個英文 token；「依按鍵順序判斷英文」開著時聲調鍵也一樣，英文段留著開。條件、影子、還原點見 `docs/spec/english-mixed.md`。回退不生效時照「不收」。
- 驗證：`syllable_not_in_lexicon_is_rejected_composition_unchanged`（`core/tests/engine.rs`）；鄰鍵與回退的測試在 `core/tests/engine_fuzzy.rs`、`core/tests/engine_english.rs`。

### 3.4 多個未完成單位

「可省略韻母」（`set_abbreviation`，預設關）開著、預測列開著、游標在組字區尾端時，已有符號的那一欄再按注音鍵會開新的未完成單位（上限 `ABBR_MAX_UNITS` = 10，且完整音節數加單位數不超過 128）。有兩個以上單位時聲調鍵與空白鍵被吃掉，單位只靠預測列選取、Backspace、Esc 解決。規則見 `docs/spec/prediction.md`。

## 4. 按鍵規則

### 4.1 判斷順序與規則表

改核心的按鍵行為時，這張表與 `core/include/shanjie.h` 在同一個改動裡一起改。

判斷順序（`dispatch_key`）：

1. Page Up／Page Down：不是「直排候選窗開著、沒有修飾鍵」時，一律不處理、不改任何狀態（規則 5q）。
2. 預測列已進入時的 ⌘⌫（規則 0）；候選窗開著時的 ⌘⌫（遺忘選取的詞，見 `docs/spec/learning.md`；規則 0a）。這兩條在規則 1 之前，否則 ⌘ 會被直通。
3. 規則 1（修飾鍵直通）。
4. 單按 Shift 切換的英數狀態（§9.3）。
5. 已進入的預測列（規則 1a–1e）。
6. 數字串、英文段（`docs/spec/english-mixed.md`）。
7. 規則 2（標點）。
8. Shift＋字母開英文段。
9. 候選窗開著（規則 3–8），數字列的起頭。
10. 有未完成音節（規則 9–13）、沒有未完成音節（規則 14–22）。

由上往下，第一條符合的生效。「注音鍵」「聲調鍵」只指沒按 Shift 的鍵。

按鍵習慣 1（`shanjie_engine_set_key_scheme`，見下一段）的 `m` 規則插在這個順序的下列位置：規則 1m 在第 3 步，是規則 1 的例外；規則 2m 在第 7 步，取代規則 2 對 Shift+, 、Shift+. 的處理；規則 2n 在第 8 步之前（規則 2 之後）；規則 3m–8m 在第 9 步，取代規則 3–8；規則 12m 在第 10 步，取代規則 12a、12b；規則 15m、15d、16m、16u、21m、19m 在第 10 步，各取代規則 15 的空白鍵、規則 15 的 ↓、規則 16 的聲調鍵、規則 16 的 ↑、規則 21 的 Tab、規則 19a。

| # | 狀態 | 按鍵 | 行為 |
|---|---|---|---|
| 0 | 預測列已進入 | ⌘⌫ | 遺忘選取的那一項（不動任何固定詞）：丟掉同一個（詞、讀音）的待學記錄，照候選窗 ⌘⌫ 的規則改寫學習檔（學習暫停時也可以），重算預測列；仍是已進入，選取移到那個詞的新位置（不在列中是 0）。未進入時照規則 1 直通 |
| 0a | 候選窗開啟 | ⌘⌫ | 遺忘反白的詞，窗保持開著（細節見 §6.6 與 `docs/spec/learning.md`）；反白在預測項目時照規則 0 的遺忘 |
| 1 | 任何 | 帶 Option、Command、Caps Lock，或帶 Control 但不是 Ctrl+\（修飾鍵恰為 Control、字元為 `\`；Ctrl+Shift+\ 屬本條） | 不處理（直通，`handled` 0、`commit` 空），**不改任何狀態**：一段組字中間插入任意個本條的鍵，之後的輸出和沒按過時逐欄位相同。按鍵習慣 1 的 Ctrl+, 、Ctrl+. 除外（規則 1m） |
| 1a | 預測列已進入 | 1–9（無修飾鍵） | 選預測列的第 n 個（超過個數就吃掉、不動作） |
| 1b | 預測列已進入 | ←→、Tab（無 Shift） | 移動選取（Tab 往後），到兩端停住。設定是直排時 ↓ 同 →、↑ 同 ←，也在兩端停住；橫排時 ↑↓ 不是預測列的鍵，照規則 1e |
| 1c | 預測列已進入 | Shift+Tab、Esc | 退回未進入（預測列保留） |
| 1d | 預測列已進入 | Enter（含 Shift+Enter） | 選目前選取的，不送出也不換行 |
| 1e | 預測列已進入 | 其他鍵（含 Backspace；橫排時含 ↑↓） | 退回未進入，再從規則 2 起處理這個鍵。Backspace 有未完成音節時照規則 11 刪最後一個符號並重算預測列；沒有時照規則 18 |
| 2 | 任何 | §8.1 的標點鍵、Ctrl+\ | 丟掉未完成音節（多個單位時是全部）、關閉候選，把標點插入組字區游標處（不送出）。組字區空的也一樣 |
| 3 | 候選開啟（橫排） | 1–9 | 收合：選目前這一頁的第 n 個，窗關閉；超出本頁則忽略（窗保持開著）。展開：選選取所在那一排的第 n 個（每排 9 個；最後一排不足 n 個則吃掉、不動作） |
| 4 | 候選開啟（橫排） | ↑↓、←→ | 收合：↑ ← 上一個、→ 下一個（跨頁，逐一移動，不翻頁），↓ 展開成網格（選取不動）。展開（一律 9 欄，一排是一頁，展開時選取所在的那一頁在最上面）：↓ 下一排同一個位置（最後一排較短時到該排最後一個，已在最後一排時不動）；↑ 上一排，只有在整份清單的第 0 排才收回成一列（選取不變），其他排往上捲；← → 上一個、下一個（跨列） |
| 5 | 候選開啟（橫排） | 空白鍵 | 收合：下一頁，最後一頁再按回第一頁的第一個。展開：和 ↓ 相同，但在最後一排時回第一排同一個位置 |
| 3v | 直排候選窗開啟 | 1–9 | 取代規則 3：選可見範圍的第 n 個，超出可見個數則忽略（窗保持開著） |
| 4v | 直排候選窗開啟 | ↓、→ | 取代規則 4：下一個；選取在可見範圍最後一個時，可見範圍往下捲一個（編號跟著可見的列重排成 1–9）；已是整份清單最後一個時不動。直排沒有網格，↓ 不展開 |
| 4w | 直排候選窗開啟 | ↑、← | 取代規則 4：上一個；選取在可見範圍第一個時，可見範圍往上捲一個；已是整份清單第一個時不動 |
| 5v | 直排候選窗開啟 | 空白鍵 | 取代規則 5：和 ↓ 相同（下一個，不是翻頁；蘋果注音直排實測），已是最後一個時不動（不回第一個） |
| 5p | 直排候選窗開啟，無修飾鍵 | Page Down（kind 15）、Page Up（kind 14） | Page Down：可見範圍往後移 9 個（不超過讓最後一個候選可見的位置），選取移到新的可見範圍第一個，但選取不往回：新的第一個比原本的選取還前面時選取不動（10 個候選，↓ 八次後 Page Down，選取仍在位置 8）；已在最後時不動。Page Up：往前移 9 個（最小 0），選取在第一個；已在最前時不動。兩者都是已處理 |
| 5q | 不是直排候選窗（橫排候選、網格、組字中、未完成音節、預測列、組字區空），或帶任何修飾鍵 | Page Down、Page Up | 不處理（`handled` 0、`commit` 空），不改任何狀態。殼平常不送這兩個鍵，只有殼出錯時才收到 |
| 6 | 候選開啟 | Enter | 選目前選取的，窗關閉 |
| 7 | 候選開啟 | Esc、Backspace | 關閉候選，組字區不變 |
| 8 | 候選開啟 | 其他鍵 | 關閉候選，再從規則 9 起處理這個鍵（Home 會真的把游標移到 0） |
| 8a | 預測列未進入且不是空的、候選窗關閉 | Tab（無修飾鍵） | 進入預測列，選取第 0 個。預測列是空的時照規則 13、21 |
| 9 | 有未完成音節 | 注音鍵 | 放進對應欄位（同一欄再按是取代）。例外：可省略韻母開著時已有符號的那一欄開新單位（§3.4）；回退成英文的兩種觸發（同一個位置重打、欄位逆序）見 `docs/spec/english-mixed.md`，游標不在尾端也生效 |
| 9a | 有兩個以上未完成單位 | 聲調鍵、空白鍵 | 吃掉、不動作 |
| 10 | 有未完成音節 | 聲調鍵、空白鍵 | 完成音節（§3.2）。回退（詞庫沒有的音節）與短詞清單見 `docs/spec/english-mixed.md` |
| 11 | 有未完成音節 | Backspace | 刪掉顯示位置最後的符號（韻母、介音、聲母）。多個單位時刪最後一個單位的最後一個符號，空了的單位拿掉 |
| 12 | 有未完成音節 | Esc | 清掉未完成音節（多個單位時是全部單位；組字區的字、固定詞、游標不動） |
| 12a | 有未完成音節 | Enter（無修飾鍵） | 送出組字區顯示的整串：游標前的顯示文字、未完成音節的符號（照顯示順序）、游標後的顯示文字，然後清空組字區（§7.1） |
| 12b | 有未完成音節 | Shift+Enter | 照規則 12a 送出，然後直通這個鍵（`handled` 0，App 收到換行） |
| 13 | 有未完成音節 | 其他鍵（方向鍵、Home、End、Delete、Tab 等） | 已處理，不動作，預測列保留。例外：Shift＋英文字母開英文段（`docs/spec/english-mixed.md`）；恰好一個未完成單位、這個鍵之前顯示著未進入的預測列、按的是 ↓ 時，開候選窗，清單只有預測列的項目；預測列是空的時 ↓ 忽略 |
| 14 | 沒有未完成音節 | 注音鍵 | 開始新的未完成音節 |
| 15 | 組字區有字 | 空白鍵、↓ | 開啟候選（§6.1）。這個鍵之前顯示著未進入的預測列時，清單先放預測列的項目，再接詞庫清單（`docs/spec/prediction.md`）。游標左邊緊接標點時開標點候選（§8.3）；緊接英文 token 時開寫法清單（`docs/spec/english-mixed.md`）。游標在尾端、前面是英文 token 加幾個音節，而且中英混合詞表有這個詞時，清單第一項是那個混合詞，這個窗不帶預測列的項目（`docs/spec/english-mixed.md` §5.7）。可列的音節長度為 0 時「已處理、忽略」 |
| 16 | 組字區有字 | 聲調鍵（空白鍵以外）、↑ | 已處理，忽略 |
| 16a | 沒有未完成音節、沒有開著的窗，游標緊接在英文 token 或空白單位後面（中英混打開著） | 空白鍵 | 取代規則 15：在游標處插入一個半形空白單位（`docs/spec/english-mixed.md`） |
| 17 | 組字區有字 | ←→、Home、End | 游標在單位之間移動；已在邊界時不動 |
| 18 | 組字區有字 | Backspace、Delete | 刪掉游標左邊（Backspace）或右邊（Delete）的單位；游標在邊界時忽略。刪在多音節詞裡面時，那個詞的其他字逐字保留（§6.5）。中英混打開著、游標左邊緊接英文 token 時，Backspace 逐字母刪（`docs/spec/english-mixed.md`） |
| 19 | 組字區有字 | Enter（無修飾鍵） | 送出組字區顯示的整句 |
| 19a | 組字區有字 | Shift+Enter | 送出整句，然後直通這個鍵（`handled` 0，App 收到換行） |
| 20 | 組字區有字 | Esc | 清空組字區 |
| 21 | 組字區有字 | 其他鍵（含 Shift＋非標點、Tab） | 送出組字區，然後直通這個鍵（`handled` 0、`commit` 非空）。例外：中英混打開著時 Shift＋英文字母開始或延續英文段，不送出 |
| 22a | 組字區空 | 聲調鍵（空白鍵以外） | 把該聲調符號（ˊ ˇ ˋ ˙）放進組字區，和標點一樣（Backspace 可刪、Enter 才送出）。標準排列、數字列生效時這個鍵是數字，倚天仍是符號 |
| 22 | 組字區空 | 其他鍵 | 不處理（直通），並清掉 `set_left_context` 存的前文。例外：中英混打開著時 Shift＋英文字母在游標處開英文段 |

### 4.1.1 按鍵習慣 1 的 `m` 規則

`shanjie_engine_set_key_scheme(engine, scheme, out)`：`scheme` 0（預設）是上面的表，Mac 版只用這個；1 是微軟注音的按鍵習慣，Windows 版的文字服務在建引擎後呼叫（`docs/spec/windows-tip.md` §3.4）。其他值回 2、狀態不變；回傳快照的方式同 `set_candidate_vertical`。**一個按鍵用哪一種習慣，在按鍵到達時決定一次**：有窗開著，就是這個窗開窗時的習慣（之後設定怎麼改都不影響這個窗）；沒有窗，就是目前的設定。窗在這個鍵裡因規則 8 關掉、鍵繼續往下走時，仍用這個值，不重讀設定，所以開窗後才改的設定，要到窗關掉之後的下一個按鍵才影響下表的列（Ctrl+, 、Shift+, 、Shift＋字母、Tab、空白等都一樣）。新開的窗用開窗那一刻的設定。下表只在 `scheme` 1 生效，沒列到的鍵都照 §4.1 的表。以下「S0」是組字區空、沒有未完成音節；「S1」是有未完成音節；「S2」是組字區有字、沒有未完成音節、沒有窗；「S3」是候選窗開著。

| # | 狀態 | 按鍵 | 行為 |
|---|---|---|---|
| 1m | 任何（沒有英文段、數字串、不在 ASCII 狀態） | Ctrl+, 、Ctrl+.（修飾鍵恰為 Control） | 丟掉未完成音節、關閉候選，把全形「，」（U+FF0C）、「。」（U+3002）插入組字區游標處（不送出），同規則 2 的標點單位。微軟注音的量測只有游標在組字尾端的情況；游標在組字中間時沒有量，這裡照規則 2 插在游標處 |
| 2m | S3 | Shift+, 、Shift+. | 吃掉、不動作，窗開著、組字不變 |
| 2m | S0 | Shift+, 、Shift+. | 不處理（直通，同規則 22 清掉前文），App 收到 `<` `>` |
| 2m | S1 且組字區沒有字 | Shift+, 、Shift+. | 丟掉未完成音節，`commit` 是 `<` 或 `>`、`handled` 1、組字區空。不直通：IMM32 宿主會吞掉「先宣告吃、再放行」的鍵（`docs/spec/windows-tip.md` §5.4） |
| 2m | S1 且組字區有字，或 S2 | Shift+, 、Shift+. | 丟掉未完成音節（若有），在游標處插入半形 `<` `>` 單位（讀音 `_punct_`＋字元，同 `set_ascii` 的半形標點單位） |
| 2n | S1 且組字區沒有字 | Shift＋字母 | 丟掉未完成音節，`commit` 是大寫字母、`handled` 1、組字區空（理由同上） |
| 2n | S1 且組字區有字，或 S2 | Shift＋字母 | 丟掉未完成音節（若有），在游標處插入半形大寫字母單位；Backspace 整個刪掉。S0 與 S3 不屬本條（S0 照規則 22，S3 照 8m 的「其他所有鍵」） |
| 3m | S3，直排窗 | 1–9 | 同規則 3v |
| 4m | S3，直排窗 | ↓ | 下一個，跨頁時可見範圍換到下一頁；整份清單最後一個時回到第一個（第一頁） |
| 4m | S3，直排窗 | ↑ | 上一個，跨頁時換到上一頁；整份清單第一個時跳到最後一頁的最後一項 |
| 5m | S3，直排窗 | 空白鍵、Page Down | 下一頁，選取到新頁的第一個（不保留頁內位置）；最後一頁時回到第一頁第一個 |
| 5m | S3，直排窗 | Page Up | 上一頁，選取到那一頁的第一個；第一頁時跳到最後一頁、選第一個 |
| 5m | S3，直排窗 | End | 跳到最後一頁、選最後一項 |
| 6m | S3，直排窗 | Enter | 選目前選取的，窗關閉（同規則 6）；Shift+Enter：關窗，組字區留著、不選 |
| 7m | S3，直排窗 | Esc | 關閉候選，組字區不變（同規則 7） |
| 8m | S3，直排窗 | 其他到得了第 9 步的鍵（Backspace、Tab、Shift+Tab、←、→、Home、Delete、注音鍵、Shift＋字母） | 吃掉、不動作，窗開著、選取與組字不變；第 9 步沒有任何鍵關窗後再往下走（取代規則 8）。例外是第 7 步的規則 2 標點鍵（`[`、`]`、`\`、`'`、`=`、`` ` ``、Shift+/、Shift+1、Shift+;、Shift+[、Shift+]、Shift+9、Shift+0、Shift+`、Shift+\、Shift+'、Shift+=、Ctrl+\；Shift+, 、Shift+. 不在其中，見 2m）：兩種習慣下都照規則 2，關窗並把全形標點插進組字區。微軟注音對這些鍵沒有量 |
| 12m | S1 | Enter、Shift+Enter | 吃掉、不動作 |
| 15m | S2 | 空白鍵 | 在游標處插入一個半形空白單位（同規則 16a），之後 ↓、選字、送出都留著；只有 ↓ 開候選窗 |
| 16m | S2 | 聲調鍵（空白鍵以外） | 在游標處插入聲調符號單位（同規則 22a） |
| 21m | S2 | Tab、Shift+Tab | 吃掉、不動作 |
| 19m | S2 | Shift+Enter | 送出整句，`handled` 1（App 不收到換行） |
| 16u | S2 | ↑ | 換游標前那一個字。基準清單＝目前顯示的那個字（`span_text`）排第一，再接這個音節在 §6.1 L＝1 的詞庫單字（原順序，不含新詞、模糊音、預測列），在一輪的第一次 ↑ 時固定。第一次換成基準清單第 3 項、第二次第 2 項，都等於在 `[a−1, a)` 選了那個長度 1 的字（§6.2、§6.3：其他已顯示的字凍結；同一段再選時保留第一次的 `pre`，所以送出時只學最後停的那個字）；第三次照規則 15d 開窗，之後的 ↑ 是規則 4m。清單只有 2 項時第一次換第 2 項、第二次開窗；只有 1 項時第一次就開窗。游標在 0、游標前是標點、空白、英文或數字單位、顯示的字不在單字清單裡時，照規則 16 忽略（清單是空的音節打不進組字，§3.2）。**一輪**：規則 1、5q 之後的任何一個不是 ↑ 的鍵結束這一輪（規則 1、5q 直通的鍵不改任何狀態，所以不結束）；第三次 ↑ 開窗也結束這一輪；不是按鍵的呼叫（`set_*`、`pick`、`reset` 等）之後，音節串、游標或游標前那個字有任何一項不是上一次 ↑ 留下的樣子，下一次 ↑ 就是新一輪的第一次 |
| 15d | S2 | ↓ | 照規則 15 開候選窗，再把清單裡第一個「顯示的字和組字區那一段（`[a−L, a)`，游標在 0 時 `[0, L)`）相同」的詞庫項目移到詞庫項目的最前面，其他照原順序，選取在它、`candidate_first` 0。預測列的項目（Windows 版關掉預測列，沒有這種項目）仍排最前；英文短詞的兩項、數字的替代項這類綁在清單最後的項目不移。沒有相同的項目時照原順序 |

- 候選窗的規則 3m–8m 只套用在「`scheme` 1 之下開的直排窗」；橫排窗（含網格）在 `scheme` 1 之下照規則 3–8（沒測，Windows 版的候選窗一律直排）。
- 沒有微軟式新規則的列：S1 的空白鍵（ㄋ 之類詞庫沒有的音節照規則 10 本來就不動作）、組字區上限（照 §5.2 的 128 個單位；微軟在第 32 個音節起把前段送出，核心沒有這個機制）、S1 的聲調鍵（照規則 10）。
- 規則 16u、15d 的量測（`docs/windows/ms-zhuyin-ref.md`「補量」的「組字中按 ↑」）只有單字清單 3 項以上、游標在組字尾端、連按中間不夾別的鍵的情況；清單不到 3 項、游標前不是字、游標在中間、中間夾別的鍵、換的字要不要學，都是沒量過、我們的選擇。微軟的窗只列游標前那個字的單字，善解的窗照 §6.1 先列多字詞，所以「目前的字排第一」用「和顯示的那一段相同的項目」來算。
- 1m、2m、2n、15m、16m 新插入的單位都算進 §5.2 的 128 個單位，第 128 個單位照現行規則整段送出。
- 和英文混打、可省略韻母、倚天排列同時開著的組合沒測；Windows 版三者都關。
- 驗證：`core/tests/engine_scheme.rs`（每一列在兩種 `scheme` 下各一個測試，`scheme` 0 的對照是逐欄位相同；整頁模型斷言 `candidate_first % 9 == 0`；新單位連打到第 128 個單位整段送出；16u、15d 是 `k18_*` 那幾個測試），`core/tests/engine_learn.rs` 的 `up_swaps_teach_the_last_character_only`。

表外的幾條規則：

- 規則 3–8 的「其他鍵」是它們沒列到的所有鍵（含 Delete、Home、End、Tab、字元鍵；Enter 屬規則 6）。
- 規則 13 把方向鍵也忽略：有未完成音節時游標不能移動。
- 規則 16 的聲調鍵以外，空白鍵在有字時是規則 15。
- Shift＋←→ 等帶 Shift 的方向鍵與不帶時相同（規則 1 不擋 Shift）。
- 驗證：`core/tests/engine.rs` 的 `row1_passthrough_keys_leave_no_trace`、`row2_*`、`rows3_to_8_candidate_keys`、`grid_*`、`rows9_to_14_pending_syllable`、`rows16_to_20_composition_editing`、`row21_other_key_commits_then_passes_through`、`row22_empty_composition_passes_through`、`row22a_*`；直排在 `core/tests/engine_vertical.rs`（`page_down_never_moves_the_selection_backwards`、`page_keys_do_nothing_without_a_vertical_window` 等）。

### 4.2 規則表之外的狀態

- **英文段與數字串**（Shift＋字母、回退、單按 Shift 的英數狀態、數字列的鍵）：開著時先看按鍵，規則 2 以下的順序不適用；規則見 `docs/spec/english-mixed.md`。
- **預測列**：有語言模型、游標在組字區尾端、候選窗關閉時，規則 9、10、11、14 處理完會重算；其他狀態變化都清掉；什麼都沒改的鍵保留它。規則見 `docs/spec/prediction.md`。
- **語意校正套用**（`apply_rerank`，不是按鍵）：一段的 n-gram 第一名含有手動加的詞（疊加層標 `manual`）時，重排只換成在同樣音節上仍保有這些詞的候選。見 `docs/spec/semantic-rerank.md`。

### 4.3 候選窗的三種形態

- **橫排收合**：一列，每頁 9 個（`PAGE_SIZE`）；輸出的 `candidates` 是目前這一頁。
- **展開的網格**：一律 9 欄，一排就是收合時的一頁；可見最多 5 排（`GRID_ROWS`），選取離開可見範圍時捲動；`candidate_columns` 為 9。
- **直排**：可見範圍固定 9 列（不足 9 個就是全部）；狀態是「整份清單、可見範圍的第一個、選取」，開窗時從 0 開始、選取 0；`candidates` 是可見範圍、`candidate_selected` 是在可見範圍裡的位置、`candidate_columns` 一律 0。
- **整頁的直排**（`scheme` 1 之下開的直排窗，§4.1.1）：可見範圍以整頁（9 個）為單位，`candidate_first` 一律是 9 的倍數，窗裡的編號 1–9 對整頁；選取永遠在可見的那一頁裡。翻頁、↑、↓ 都在這個模型上（規則 4m、5m），沒有任何規則讓 `candidate_first` 不是 9 的倍數（`candidate_first` 沿用 `scheme` 0 的欄位意義：`candidates[0]` 在整份清單的位置）。
- 方向由 `shanjie_engine_set_candidate_vertical` 設定（預設橫排），**在開窗時固定**：設定只影響下一次開窗，開著的窗（含網格）照開啟時的方向。預測列沒有開窗的時間點，每個輸出都照引擎當下的設定。
- 輸出欄位 `candidate_vertical`：0 橫排或沒有候選；1 直排候選窗；2 直排預測列（進入或未進入）。
- 選字、滑鼠點選都走同一個 `choose`；滑鼠點選的 `index` 是這次輸出 `candidates` 裡的位置，核心選 `candidate_first + index`（候選窗關閉但有預測列時，選預測列的第 index 項）。
- 驗證：`core/tests/engine_vertical.rs`（`opens_vertical_with_the_first_rows_and_zero_selected`、`down_right_space_select_next_and_scroll_one_row_at_the_bottom`、`switching_while_a_horizontal_window_is_open_changes_nothing_until_it_reopens` 等）；`core/tests/engine.rs` 的 `grid_expanded_keys`、`grid_scrolls_to_keep_the_selected_row_visible`、`pick_follows_candidate_first`。

### 4.4 其他細則

- 候選邊界：↑↓ 在第一個、最後一個停住不繞回；只有收合時的空白鍵會從最後一頁繞回第一頁。候選為空時不開窗，空白與 ↓ 是「已處理、忽略」。
- 游標位置：`cursor_utf16` 等於游標前的顯示字元的 UTF-16 長度加未完成音節的 UTF-16 長度；一個音節在顯示字串裡佔一個字元，標點則佔它的固定詞的字數（`⋯⋯` 佔兩個）。
- 直通的輸出（規則 1、22）是目前狀態的快照：`handled` 0、`commit` 空。
- 取代後的 Backspace：同一欄被取代時新符號直接換掉舊符號，不會還原舊符號。
- 中英切換：系統的「使用大寫鎖定鍵切換輸入方式」切換時系統停用本輸入法，殼在停用時送出或丟棄組字區；Caps Lock 帶著的按鍵走規則 1。另有選用的單按 Shift 切換（§9）。

## 5. 組字區

### 5.1 單位與顯示

- 單位的種類見 §1。標點與英文 token、空白單位都是「長度 1 的固定詞」，切開解碼的段落，Backspace 與方向鍵整個處理一次。
- 游標在單位之間移動；在游標處插入的單位把右邊的固定詞整段右移（右移的固定詞清掉學習用的前文鍵 `key`）。
- 畫面字串由自由段的第一名和固定詞依序串接。沒有語言模型時（unigram）每段用 `decode_beam` 取第一名。

### 5.2 上限 128 個單位

- `MAX_SYLLABLES` = 128。音節、標點、英文 token、數字串 token、空白單位各算一個單位；插入第 128 個單位後立刻送出整段（`handled` 1），組字區清空，學習照一般送出處理。
- 預測的單位上限 `ABBR_MAX_UNITS` 不變，完整音節數加單位數不超過 128。
- 設計取捨：上限 128 讓長句不被中途送出（蘋果注音 51 字全在組字區），同時限制每鍵延遲與文字留在記憶體的時間；128 超過會意的視窗（ctx 64），所以長段需要 §5.3 的前段固定。

### 5.3 長段前段固定（`cap_snapshot`、`cap_freeze`、`cap_refreeze`）

- **自由段**：組字區裡兩個固定詞（手動改選的詞、凍結詞、標點、英文 token、數字串 token、空白單位）之間、或頭尾到固定詞之間的連續音節，也就是語意校正工作會拿去當一段的範圍。
- **視窗 W**：載入的會意檔頭的 `ctx − 3`（出貨的 ctx 是 64，W 是 61）；沒有載入會意時 W 是 61（`CAP_WINDOW_NO_MODEL`）。減 3 是因為 `ll_many` 會先丟掉前文鍵的字讓鍵加候選塞進視窗，留 2 個字的位置給最多 2 個漢字的前文鍵。W、自由段長度都以字數計（候選的字元數，`TooLong` 用的同一個單位），不是音節數。
- **規則**：自由段的字數超過 W 時，從這段的開頭往後一個詞一個詞固定成凍結詞，一直固定到剩下的字數 ≤ W − H（`CAP_BUFFER` H = 16；W 不大於 H 時 H 當 0）。跨過「段尾往前 W − H 字」那一點的詞也固定。因此自由段落在 45 到 61 字之間，往尾端打字時大約每 16 個字才有一鍵解碼兩次。
- **每一個鍵（`key()` 與 `pick()`）的順序**：
  1. 進來時、`dispatch` 之前，存下當下的 `path` 與 `syls`（已套用的重排結果都在 `path` 裡）。只在這一鍵可能讓自由段超過 W 時才存（顯示字數加一鍵最多加的字數再加 2 超過 W），而且要有 n-gram 語言模型；沒有語言模型（unigram，沒有 `path`）時不固定。
  2. 照一般規則處理這個鍵。
  3. 只有這一鍵真的重新解碼了（世代號變了）而且組字區不空時才做：第 1 步的 `path` 各詞音節數加起來要等於第 1 步的 `syls` 長度，且這一鍵之後的 `path` 也對得上之後的 `syls`，否則什麼都不固定。對每個超過 W 的自由段，依第 1 步存下的詞，一個詞一個詞固定，凍結詞的內容取第 1 步 `path` 裡的那個詞。
  4. 第 3 步至少固定一個詞時，用新狀態重新解碼並重建這一鍵的輸出（`handled`、`commit` 照 `dispatch` 的），所以畫面上的組字、游標與之後 Enter 送出的字串是同一份。
- **第 3 步停下的條件**（碰到就不再往後固定）：
  - 詞超出第 1 步的 `syls` 與這一鍵之後的 `syls` 的最長共同前綴（這一鍵插入、刪除或預測選取改到的位置及其後面）。
  - 詞在這一鍵之前是自由的，而且它的結尾離最長共同前綴不到 M = 4（`CAP_EDGE`）個音節。游標在長段中間打字時，剛打的音節不能在下一鍵就被固定成單字，多音節詞才組得起來。第 1 步組字區裡已經是凍結詞的舊詞不受這條限制（釋放後再固定回去）。
  - 詞落在這一鍵被 ⌘⌫ 遺忘而放開的範圍（遺忘要在畫面上生效，不能被立刻固定回去）。
  - 詞和它的音節在詞庫的分數查不到（`best_lp_delta` 為空）。
- **凍結詞的釋放**：這樣固定的詞和改選後的凍結詞（§6.3）是同一種，釋放規則照舊（§6.4、§6.5）；釋放後同一鍵的第 3 步若這段又超過 W，就再固定一次，等於取消那個交界的合併。手動改選的詞本來就固定，第 3 步不碰它。凍結詞不學習。
- **不變的部分**：組字區從來沒有自由段超過 W 字時，解碼與輸出和沒有這條規則時逐位元組相同。不論語意校正開或關、有沒有載入會意都固定，因為固定後前段不再解碼，每鍵的解碼時間就不隨組字區變長。
- **非同步重排**：重排在背景跑，套用可能晚於下一鍵。「固定成會意改過的字」只對那一鍵之前已經套用的結果成立；在每鍵套用後才送下一鍵的重播（`shanjie-replay --rerank each`）下是確定的，不等套用的重播下前段固定成 n-gram 的字。
- **取捨（量測）**：H = 0 時超過 W 之後幾乎每鍵都要再固定一個詞、解碼兩次（單鍵最大值約 26 ms，預期 13 到 17 ms），所以固定時一次固定到 W − 16。M = 4 是因為 `b <= lcp` 的版本會把剛打的「ㄉㄧㄢˋ ㄋㄠˇ」固定成「店腦」。

### 5.4 驗證

- `core/tests/engine_cap.rs`（只在 release 編譯，70 到 128 個音節且帶語意校正，debug 跑太慢）：
  - `the_model_window_is_64`：出貨會意的 ctx 是 64。
  - `the_head_of_a_long_composition_is_frozen_word_by_word`：連打 70 個音節（沒有標點、每個工作跑完再打下一鍵）：每個工作的每個候選都 ≤ W 字、沒有 `TooLong`，凍結的詞是上一鍵畫面上的樣子，輸出的 `preedit` 等於 Enter 會送出的字串，會意改過的字移出視窗後仍是改過的樣子。
  - `a_syllable_typed_in_front_of_the_stretch_freezes_nothing_after_it`：61 字的自由段，游標移到第一個音節後打一個音節，新音節與它之後的詞不固定、不出錯。
  - `the_word_being_typed_in_the_middle_is_not_frozen_as_a_single_character`：Home 之後連打「ㄉㄧㄢˋ ㄋㄠˇ ㄓㄨㄛ」，前兩個音節沒有被固定成單字（拿掉 M 時失敗）。
  - `a_frozen_word_that_merges_with_the_new_syllable_is_frozen_again`、`a_piece_left_by_backspace_is_frozen_again_when_a_syllable_follows`：釋放後同一鍵再固定。
  - `rerank_off_freezes_the_head_and_shows_the_ngram_first`：重排關也固定，顯示是凍結詞加自由段的 n-gram 第一名。
  - `deleting_a_mark_between_two_long_sentences_freezes_the_front_one`：兩段各 40 字、中間一個標點，刪掉標點的那一鍵前段固定。
  - `the_128th_unit_sends_and_the_127th_does_not`。
  - `keys_that_decode_nothing_leave_the_state_alone_and_a_fitting_key_decodes_once`：←、↓ 開窗等不解碼的鍵不動 `path`、`display`、世代號與等著的重排工作；沒有固定任何詞的鍵只解碼一次。
  - `the_freeze_has_a_buffer_and_needs_neither_the_rerank_nor_the_model`：四種設定各連打 128 個音節，每個自由段都 ≤ W，解碼兩次的鍵數 ≤ ⌈(字數 − W) ÷ H⌉ + 1（拿掉緩衝或把條件寫成「語意校正開」時失敗）。
  - `a_word_the_user_picked_is_never_frozen_over`：手動改選的詞不被固定動到。
  - `a_forgotten_head_word_is_not_frozen_back_in_the_same_key`、`a_composition_typed_before_a_model_has_no_path_to_freeze_from`、`rows_rebuilt_by_a_forget_come_from_the_decode_after_the_freeze`。
- 延遲：`core/tests/engine_predict.rs` 的 `timing_composition_cap_128_syllables`（`cargo test --release -p core --test engine_predict timing_composition_cap -- --ignored --nocapture`，低負載下跑）。重播 128 個音節的長句（語意校正開、會意與常談載入），5 輪取中位數，斷言語意校正開的按鍵路徑 p95 < 16 ms、`RerankJob::run` 的 p95 < 300 ms、語意校正關的按鍵路徑 p95 < 16 ms。驗收時量到按鍵路徑 p95 約 13 ms、`RerankJob::run` 約 40 ms，單鍵最大值低於 50 ms。
- 重播評測：`shanjie-replay --rerank each`（與 `core/tests/engine_replay.rs`）在 dev302、打字測驗、錯字回報，聊天與書面兩種設定，`top1_sha256` 和固定規則加入前相同（評測集沒有超過 32 個音節的列，不會碰到這條規則）。

## 6. 候選與固定詞

### 6.1 候選範圍與排序

- 設 a 為游標位置。可用來列候選的音節，是游標左邊連續、不含標點的那一段（`avail`）；a 為 0 時改用游標右邊、從位置 0 開始連續不含標點的那一段。長度為 0（游標左邊緊鄰標點，或 a 為 0 而第一個單位是標點）時，空白鍵與 ↓ 是「已處理、忽略」，標點另有候選（§8.3）。
- 列出讀音等於 `音節[a−L..a]`（a 為 0 時是 `音節[0..L]`）的所有詞，L 從 `min(max(詞庫最長讀音, 4), 可用長度)` 遞減到 1；同一個 L 內：選字記憶裡這個讀音的新詞先列（權重由高到低），再照詞庫順序（分數由高到低）；同一個字串只出現第一次。
- 開窗的鍵之前顯示著未進入的預測列時，清單最前面是預測列的項目，詞庫清單接在後面並去掉預測列已有的詞（`docs/spec/prediction.md`）。
- 異體寫法：詞庫載入時已把少用寫法的分數壓到常用寫法之下，所以清單裡常用寫法在前；預測列帶進窗之後少用寫法在前時，再把少用寫法移到常用寫法的緊後面（少用寫法帶學習權重時不移）。不受任何設定影響（`docs/spec/decoder.md`）。
- 模糊輸入開著時，清單在所有完全吻合的詞之後列出容錯得到的詞（長讀音在前、同讀音照分數）；選了以後那一段換成詞的詞庫讀音。
- 分頁：收合每頁 9 個；開啟時在第一頁、選取第 0 個。
- 驗證：`core/tests/engine.rs` 的 `row15_candidate_range_order_and_dedup`、`candidates_never_span_punctuation`。

### 6.2 選字與固定詞

- 選了長度 L 的詞 w，就把區間 `[a−L, a)`（或 `[0, L)`）固定成 w；與既有固定詞重疊的移除；游標不動。固定詞的分數用它紀錄的分數；紀錄不在了（⌘⌫ 遺忘、清除）的新詞，那一段解除固定、照未固定重新解碼，不回錯誤。
- 固定詞隨編輯調整：在游標插入或刪除單位時，完全在左邊的固定詞不動，完全在右邊的跟著平移，跨過該位置的移除（凍結詞例外，§6.4、§6.5）。
- 重算：組字區有變動就重算畫面字串。固定詞把音節切成幾段，每段自由區間各自解碼取第一名，再和固定詞依序串接；有語言模型時左邊緊鄰的固定詞當前文，是標點則用句首。
- 驗證：`core/tests/engine.rs` 的 `fixed_one_syllable_word_survives_edits_and_is_removed_with_its_syllable`、`fixed_two_syllable_word_shifts_and_is_removed_when_cut`。

### 6.3 改選後其他字不動（`choose`、`freeze_outside`）

有語言模型時，候選窗改選一段（或預測選取、滑鼠點選）之後，那一段以外、改選前已經顯示的詞全部固定成凍結詞（`frozen`，`pre`、`key` 都是空，送出時不學、不遺忘）：

1. 完全在範圍外的詞（含標點）：整個固定。已經是固定詞的不重複加；詞庫在該讀音沒有這個詞（例如經鄰鍵或模糊音讀出來的，`refresh_lm` 以它站的讀音計分）時留給解碼；標點與學習過的新詞照樣固定。
2. 和範圍部分重疊的詞：字數等於音節數時，範圍外的每個音節各自看顯示的字，詞庫在該音節有這個單字就固定成單字，沒有就留給解碼；字數不等於音節數的詞，範圍外的音節不固定。
3. 改選的那一段照一般規則加入（學習的 `pre`、`key` 語意不變）。
4. 路徑上每個詞帶它佔的音節數（解碼器逐詞給出），不用字數推算：疊加層可以有字數與音節數不同的條目。
5. 候選窗裡讀音正好是游標前已打音節的預測項目，就是詞庫在這個範圍的候選，照改選處理；其他預測項目（補完未完成音節、縮寫、往後補字）和直接從預測列選，也凍結 `[起點, 游標)` 以外的詞（例：「以備」後從預測列選「時」→「以備時」，而不是重新解碼成「已被時」）。選標點替代字、英文 token 的寫法時同樣凍結範圍外的詞。
6. 沒有語言模型（沒有 `path`）時不凍結。
7. 語意校正：固定的詞不屬於任何自由段，重排只動沒被固定的段落，所以改選之後範圍外的字（含重排選過的）不會再被重排改動。
8. ⌘⌫ 遺忘的範圍見 §6.6。

設計取捨：照蘋果注音實測，改選只動選的那一段，其他已經顯示的字不動；小麥注音是重新解碼其餘的字。

驗證：`core/tests/engine_repick.rs` 的 `freeze_reported_case_banhkong`、`freeze_row_133_six_steps`（拿掉 `freeze_outside` 時書面設定失敗）、`freeze_teaches_only_the_picked_span`、`freeze_partial_overlap_keeps_the_second_character`、`freeze_words_outside_stay`、`freeze_keeps_a_word_with_fewer_characters_than_syllables`、`freeze_leaves_a_syllable_without_a_single_entry_to_the_decoder`、`freeze_partial_overlap_of_a_mismatched_word_is_left_to_the_decoder`、`prediction_row_starts_inside_a_frozen_word`。

### 6.4 插入音節（`insert_token`、`release_frozen`）

- 先把「刪除拆出」的單字全部放開（§6.5；只在新打的是音節時，插入標點、英文 token、空白單位不放開）。
- 插入點落在多音節凍結詞內部時，不整個移除，改逐音節拆成單字凍結詞（字數等於音節數，而且該音節有這個單字條目的才保留，其餘交給解碼）；這次拆出的單字不當這次插入的放開候選。手動改選的固定詞照一般規則整個移除。
- 緊貼插入點的左鄰凍結詞（`end == 游標`）與右鄰凍結詞（平移後 `start == 游標 + 1`）是放開候選；標點、手動改選的詞不是，插入的單位本身是標點時也沒有候選。先拿掉候選做一次暫定解碼；某個候選只有在暫定路徑裡有一個詞涵蓋它的全部音節加上新音節時才放開，其餘放回再解碼；放回之後再檢查一次放開的是否仍被涵蓋（最多再兩次解碼，候選最多兩個）。
- 放開的詞與新音節都是自由的，不再被固定，之後打的音節可以再改寫它們（例：「她是」＋ ㄐㄧˋ ㄓㄜˇ →「她是記者」，與蘋果注音相同）。例外是 §5.3：放開後的自由段超過 W 時同一鍵再固定。
- 驗證：`insert_after_repick_merges_with_the_frozen_neighbour`、`insert_that_does_not_merge_keeps_the_frozen_word`、`insert_with_a_frozen_word_on_each_side`、`insert_inside_a_frozen_word_keeps_both_halves`、`apple_case_1_typing_on_after_a_repick_rewrites_the_neighbour`、`apple_case_4_typing_on_after_a_repick_gives_the_new_word`。

### 6.5 刪除音節（`remove_syllable`）

- 刪除前，畫面路徑上含該音節的詞（解碼出來的、凍結的、手動改選的都算）若有兩個以上音節，其餘音節就地變成單字凍結詞（有單字條目才保留，其餘交給解碼），標成「刪除拆出」；手動改選的那個詞本身照一般規則移除（待學紀錄跟著消失，拆出的單字是凍結詞、不學）。再刪那個音節，右邊的固定詞平移。標點、英文 token、空白單位只佔一個音節，不受影響。
- 沒有「放開紀錄」：刪字不會把舊字放回（「她世界」Backspace →「她世」）。
- 「刪除拆出」的單字在新打一個音節（`insert_token` 的音節）時、在解碼之前全部放開，照未固定的段落重新解碼（「他世界」Backspace →「他世」，再打 ㄏㄣˇ →「他是很」，與蘋果注音相同）。在那之前畫面照舊保留；Backspace、Delete、方向鍵、改選、送出都不放開。改選與預測選取凍結的詞、手動改選的詞不受影響。
- 驗證：`backspace_after_a_merge_fixes_the_word_again`、`deleting_inside_a_frozen_word_keeps_the_rest`、`backspace_in_a_merged_or_frozen_word_like_apple`、`apple_case_2_backspace_keeps_the_rest_of_a_decoded_word`、`section_15_a_new_syllable_frees_the_pieces_a_backspace_split_off`、`section_15_arrow_keys_do_not_free_the_pieces_but_the_next_syllable_does`、`section_15_a_repick_freeze_is_not_released_by_a_new_syllable`（`core/tests/engine_repick.rs`）。

### 6.6 ⌘⌫ 遺忘與凍結詞

- 候選窗的 ⌘⌫ 遺忘反白的詞時，先查被遺忘的（讀音, 詞）在這個範圍的前文下是否生效（`learned_weight` > 0，精確或最後一字層；全域層不算）。生效才解除和候選範圍重疊的凍結詞再解碼；不生效只刪紀錄，畫面不動。
- 預測列的 ⌘⌫ 從不解除固定詞。
- 這一鍵遺忘的範圍不會被 §5.3 的前段固定在同一鍵固定回去。
- 驗證：`forget_without_records_keeps_the_frozen_words`、`prediction_row_forget_keeps_the_frozen_words`、`candidate_forget_of_an_entry_not_in_effect_keeps_the_frozen_words`、`candidate_forget_of_an_entry_in_effect_frees_the_frozen_word`（`core/tests/engine_repick.rs`）；切詞後遺忘在 `core/tests/engine_learn.rs`。

## 7. 送出

### 7.1 Enter 與 Shift+Enter

- 組字區有字、無修飾鍵的 Enter（規則 19、12a）：送出組字區顯示的整串，未完成的注音符號照它在畫面上的位置一起送出（游標在中間打「你好嗎」← 兩次再打 ㄅ，Enter 送出「你ㄅ好嗎」），然後清空組字區；只有未完成音節時送出那幾個符號（ㄅ、ㄋㄧ）。`handled` 1。與蘋果注音實測相同。
- Shift+Enter（規則 19a、12b）：先送出同樣的整串，再直通這個鍵（`handled` 0、`commit` 非空），App 收到換行。蘋果注音相同。
- 候選窗開著、預測列已進入時，Enter 與 Shift+Enter 都是選取（規則 6、1d），不送出也不換行。
- Option、Command、Control＋Enter 走規則 1，直通且不改狀態；數字鍵盤的 Enter 與 Return 同一種按鍵。
- 學習只看組字區已解出的部分（`learn_commit` 的輸入是解碼後的顯示文字，不含未完成符號）；未完成的符號不學、不進前文鍵。預測列清掉。
- 驗證：`core/tests/engine.rs` 的 `enter_commits_what_is_shown_unfinished_symbols_included`（只有未完成音節、尾端有未完成音節、游標在中間、Shift+Enter 有無未完成音節、⌘Enter 直通且規則 13 仍吃掉 ↑）；`macos/Tests/ShanjieKitTests/ShellTests.swift` 的 `testShiftEnterCommitsThenPassesTheKeyOn`。

### 7.2 其他送出路徑

- **規則 21**：組字區有字時按了核心不處理的鍵（Shift＋非標點、Tab 等），送出組字區再直通這個鍵。
- **自動送出**：第 128 個單位（§5.2）。
- **`reset`**：模式 0 送出目前顯示的整串（含未完成音節，與 Enter 相同，所以失焦時打的注音不會無聲消失；開著的英文段以它的寫法送出，`docs/spec/english-mixed.md` §4.4，也與 Enter 相同）後清空，不學習；模式 1 丟棄。兩者都清掉組字區、固定詞、前文，不清語言模型、設定與 ASCII 狀態。殼在 `commitComposition`、停用（輸入框變成密碼欄時改丟棄）、組字擁有者改變、表外按鍵時呼叫。
- 送出、Esc 清空、`reset` 時，連同固定詞清掉所有組字狀態。
- 驗證：`reset_both_modes_equal_fresh_engine`、`auto_commit_at_128th_syllable`（`core/tests/engine.rs`）。

### 7.3 Enter 等目前的語意校正（殼）

- 問題：殼每處理一鍵就把整個組字區重新解碼，前一次套用的語意校正結果在這個鍵的重排工作跑完前不在畫面上；Enter 在那之前到，會送出 n-gram 的字，連畫面上已經改對的段也變回去。
- 做法：Enter（`KeyMap` 對到 Enter 的鍵，含數字鍵盤的 Enter 與 Shift+Enter）、組字區有字、語意校正開著、候選沒有選取（候選窗開著或預測列已進入時核心一律不套用，所以不等），而且這個 session 與目前引擎有重排工作在跑或排隊時，殼在主執行緒等那些工作完成並照現有規則套用，再把 Enter 交給核心。
- 等候放在 `claim()`、`applyProfile`、`syncAscii` 之後、Enter 的 `send` 之前（放在前面，設定變更會讓工作過期）。
- 上限 `ENTER_RERANK_WAIT_MS` = 100 ms，**整次 Enter 一個期限**：Enter 到時用單調時鐘算期限，有一個在跑、一個在排隊時每次等候只給剩下的時間；過了期限就送出畫面上的字（沒有等候時的行為），還在跑的工作完成後被丟掉。等候期間主執行緒不處理別的事件，所以按鍵順序不變。
- 實作約束：阻塞式等候，用每個工作各自的信號量（初始 0，`run()` 回傳之後才發訊號，不共用一個）；不用巢狀 run loop，不用無上限的 `wait()`；碼在發訊號之前寫進盒子（`Synchronization.Mutex`）。等到之後由殼直接呼叫完成處理（`rerankFinished`，與非同步完成同一條路徑），原本排進主佇列的那次回呼只在「handle 還是殼目前的執行中 handle 而且還沒完成」時才處理，比對用強參照 `===`，所以同一個工作只完成一次、`rerankRunning` 不會卡住。
- 已知限制：等候不提升推論佇列（`.utility`）的優先權，負載高時可能逾時，退回舊行為。同步套用回 4（引擎重設）時照舊由 `fail()` 處理。
- 設計取捨：上限取高於量到的最大值（虛擬機上最後一個重排工作 33 到 84 ms，Mac 上 dev302 每列 p95 約 12.6 ms）。這是「按鍵不等語意校正」的唯一例外，只有 Enter 等，而且只在有工作時；其他送出路徑（換 App、點滑鼠、`commitComposition`、自動送出）不等。
- 驗證：`macos/Tests/ShanjieKitTests/RerankTests.swift` 的 `testEnterWaitsForTheRunningJobAndSendsTheRerankedText`、`testEnterWaitsForTheRunningAndTheWaitingJob`、`testEnterAfterTheLimitSendsTheShownTextAndTheQueueGoesOn`、`testEnterDoesNotWaitWithoutAJobOrWithTheSwitchOffOrAnEmptyOrSelectedComposition`、`testOneDeadlineForTheWholeEnter`、`testEnterWaitsForTheRealQueue`（真實佇列；出貨常數 100 ms 另斷言）。指令：`make test-swift`；資料競爭另用 `swift test --sanitize=thread --filter RerankTests` 檢查。

## 8. 標點

### 8.1 按鍵

| 按鍵 | 輸出 | 按鍵 | 輸出 |
|---|---|---|---|
| Shift+, | ， | Shift+[ | 『 |
| Shift+. | 。 | Shift+] | 』 |
| Shift+/ | ？ | Shift+9 | （ |
| Shift+1 | ！ | Shift+0 | ） |
| Shift+; | ： | Shift+` | ～ |
| Shift+\ | ｜ | Shift+' | “ |
| Shift+= | ＋ | Ctrl+\ | 、 |

單按也是標點的鍵（該鍵在目前排列不是注音鍵或聲調鍵時才算）：

| 按鍵 | 輸出 | 按鍵 | 輸出 |
|---|---|---|---|
| [ | 「 | ] | 」 |
| \ | 、 | ' | ‘ |
| = | ＝ | ` | · |

- Shift 表只認修飾鍵恰為 Shift；單按表只認沒有修飾鍵。倚天排列的 `'`、`=` 是注音鍵，照舊。程式裡是 `SHIFT_PUNCT`（13 個）與 `PLAIN_PUNCT`（6 個）。
- 表照蘋果內建繁體注音（`com.apple.inputmethod.TCIM.Zhuyin`）用只對自己視窗送鍵的探測程式實測，沒有複製蘋果的任何資料檔。
- 驗證：`core/tests/engine.rs` 的 `row2_unshifted_bracket_row_is_punctuation`、`row2_punctuation_goes_into_the_composition`。

### 8.2 標點是組字區的單位

- 按標點鍵：丟掉未完成音節、關閉候選，把標點插入游標處，游標移到它右邊；不送出任何東西（`handled` 1、`commit` 空）。組字區原本是空的也一樣，標點自己成為組字區，按 Enter 才送出。這樣打完「你好，」後順手按 Enter，不會在聊天 App 裡把訊息送出去。
- 內部：讀音 `_punct_<標點字>`，同時是一個長度 1 的固定詞，固定詞依 `start` 排序；判斷是否為標點只看讀音是否以 `_punct_` 開頭。選字不會移除標點固定詞（候選範圍不跨過標點）。
- 編輯：←→、Home、End 當一格跳過；Backspace、Delete 刪整個標點；算進 128 個單位。
- 解碼：標點是句子邊界。左邊緊鄰標點的自由段，前文用句首；右邊緊鄰標點的自由段，用句尾結束。所以 `A，B` 的畫面字串等於 A 單獨解碼加「，」加 B 單獨解碼。標點固定詞不查詞庫分數（分數記 0）。
- 規則 21 不變：Shift＋非標點仍屬規則 21。
- 驗證：`punctuation_is_one_cell_for_editing`、`punctuation_counts_toward_the_limit`、`candidates_never_span_punctuation`（`core/tests/engine.rs`）；標點是句子邊界的測試是 `core/tests/engine_lm.rs` 的 `punctuation_is_a_sentence_boundary`。

### 8.3 標點候選

- 游標左邊緊鄰的單位是標點（或游標為 0 而第一個單位是標點）時，空白鍵與 ↓ 開候選：第一個是原本按鍵打出的標點，接著是表裡這個標點的替代，去重；沒有替代時清單只有它自己。分頁、選取、數字鍵照規則 3–8。
- 選擇：選中的字串取代那個標點的顯示（固定詞的 `word` 換成它），讀音維持 `_punct_<原本的標點>`，所以再開候選時清單不變、第一個仍是原本的標點；它仍是標點單位（句子邊界、一格）。多字元的替代（`⋯⋯`）：顯示與游標位置依單位計算，標點單位的寬度是它的固定詞的字數。
- 候選表的來源：殼在建引擎時讀本機 macOS 的 `CIMPunctuationCandidates.plist`（私有框架的資源，`/System/Library/PrivateFrameworks/CoreChineseEngine.framework/Versions/A/Resources/` 下），轉成核心的格式，用 `shanjie_engine_set_punctuation` 交給核心；repo 與發佈檔不放蘋果的任何資料。檔案不存在或系統更新改了格式時（哪些系統版本有這個檔沒有逐版驗證），讀不到、解析失敗、結果是空的或核心拒收，核心用內建的預設表，殼記一筆固定文字的 log（`punctuation candidates: system table unavailable, using the built-in list`、`punctuation candidates: core rejected the system table, code N`）。
- 表的格式（殼轉換出來、核心接收）：UTF-8，以 `\n` 分行，每行以 `\t` 分欄，第一欄是鍵（恰一個 Unicode scalar），其後至少一欄替代且每欄非空；同一個鍵出現多次時後面的取代前面的。殼的轉換規則與核心相同（鍵不是單一 scalar、鍵或替代是空字串或含 `\t`、`\n`、NUL 的項目整筆略過，逐個 Unicode scalar 檢查），所以核心不會拒收殼產出的表。上限 64 KB、1,000 行。核心的 `set_punctuation`：整份表取代（不是合併），失敗回 2 並保留原表，不重算組字區，`reset` 不清這張表。
- 內建預設表（寫在核心程式碼裡的常數 `DEFAULT_PUNCT`）：

  | 標點 | 預設替代 | 來源 |
  |---|---|---|
  | ， | 〈 《 ︿ ︽ | McBopomofo 詞庫（MIT）`_punctuation_Standard_<` |
  | 。 | ． 〉 》 ﹀ ︾ | 同上 `_punctuation_Standard_>` |
  | ： | ； | 同上 `_punctuation_:` |
  | 「 | 『 《 〔 ｛ 〈 【 〖 | 同上 `_punctuation_{` |
  | 」 | 』 》 〕 ｝ 〉 】 〗 | 同上 `_punctuation_}` |
  | 、 | ＼ ／ | 同上 `_punctuation_\` |
  | 『 | 「 《 〔 ｛ 〈 【 〖 | 本專案（借用「的清單） |
  | 』 | 」 》 〕 ｝ 〉 】 〗 | 本專案（借用」的清單） |
  | ‘ | ’ | 本專案（沒有系統表時仍打得出右引號） |
  | “ | ” | 本專案 |
  | ？ ！ （ ） ～ ｜ ＋ ＝ · | （無） | 各自只有本身 |

- 驗證：`core/tests/engine.rs` 的 `default_punctuation_candidates`、`chosen_punctuation_alternative_replaces_the_mark`、`multi_char_punctuation_keeps_the_caret`、`set_punctuation_validates_and_keeps_the_old_table`；殼 `macos/Tests/ShanjieKitTests/PunctuationTableTests.swift` 的 `testConversionSkipsWhatTheCoreWouldReject`、`testSystemTableReachesTheCore`（讀本機實際的系統檔、交給真的核心，斷言回傳 0 且「，」的候選含「、」；檔案不存在時測試失敗，不靜靜跳過）、`testMissingTableFallsBackToTheBuiltInList`（log 與預設表）。

### 8.4 標點名稱

- 殼在建構時讀本機 macOS 的 `CIMPunctuationDescription_zh_Hant.strings`（和候選表同一個資源目錄），得到「標點 → 名稱」（例 `，` → 全形逗號、`、` → 頓號、`　` → 全形空格），不複製進 repo 或發佈檔。讀不到時候選照常顯示、沒有名稱，記一句 log（`punctuation names: system table unavailable, candidates show without names`）。
- 過濾：鍵或名稱是空字串、含 tab、換行、NUL，或名稱超過 16 個字（`maxName`）的項目略過；超過 1,000 筆（`maxEntries`）的檔不當名稱表。
- 只有完全等於某個鍵的候選才有名稱，所以詞的候選永遠沒有名稱。名稱以小字顯示在標點旁；核心與送出的內容始終是標點本身，點選時殼用顯示的位置對回核心的 `index`，絕不把名稱送出。畫法見 `docs/spec/macos-shell.md`。
- 驗證：`PunctuationTableTests` 的 `testNamesSkipWhatCannotBeShown`、`testSystemNamesReachThePanel`（讀本機系統名稱表，點第二格選到「、」，組字不含名稱；檔案不存在時失敗）、`testWordCandidatesAndMissingNamesShowNoNames`。

## 9. 單按 Shift 切換中英

設定「單按 Shift 切換中英」（殼的偏好鍵 `shiftToggle`）預設關（沒有值當關）。開啟後，單按一下左或右 Shift 在中文和英文（英數狀態）之間切換，游標旁短暫顯示「英」或「中」。

### 9.1 單按的判定（`ShiftTap`，不碰 AppKit 的小狀態機）

- 殼的 `recognizedEvents` 含 `flagsChanged`；`Session.handle` 對它永遠回 false，App 看到的 Shift 和沒有輸入法時一樣。
- 每個事件都從旗標重建「哪個 Shift 按著」（左 `0x2`、右 `0x4` 的裝置位元；事件沒有裝置位元時用通用的 Shift 位元），漏掉一個放開事件不會留下過期的狀態。
- **按下**：左或右 Shift 從放開變按下，而且當下沒有其他修飾鍵（Control、Option、Command、Fn）、Caps Lock 燈沒亮。記下按下的時間和另一側 Shift 此刻的狀態。別的修飾鍵動了就取消這次候選。
- **放開**：同一個 Shift 放開時，下面全部成立才切換：
  1. 按下到放開之間沒有任何 `keyDown`（Shift＋字母、Shift＋方向鍵都不算）；
  2. 沒有按過別的修飾鍵、Caps Lock 沒亮；
  3. 期間沒有滑鼠按下（用 `CGEventSource.secondsSinceLastEventType` 查左、右、其他鍵的滑鼠按下距今秒數，比「處理放開事件的時間減按下時間」短就是期間按過；Shift＋點擊不切換；不需要額外權限）；
  4. 另一側 Shift 的狀態和按下時相同：早就殘留在旗標裡、沒有變的位元不擋（藍牙漏了放開、遊戲吃掉事件時，另一側的位元會殘留），按下到放開之間變了才不算；
  5. 按住的時間 ≤ `SHIFT_TAP_MAX` = 0.5 秒。
- 取捨：`SHIFT_TAP_MAX` 沒有參考實測值（微軟注音與唯音都沒有文件寫），是避免「按住 Shift 想打大寫又放棄」也切換的推論值。另一個取捨是真的按住一側 Shift、再單按另一側會切換（這個動作沒有別的用途）。
- 設定關掉時：`flagsChanged` 一律不處理，每個 App 的英數狀態全部清掉，核心 `set_ascii(0)`。
- 驗證：`ShiftTapTests`（`testLoneTapSwitchesLeftAndRight`、`testAKeyBetweenIsNotATap`、`testAnotherModifierIsNotATap`、`testMouseDownBetweenIsNotATap`、`testAClickBeforeALateHandledReleaseIsNotATap`、`testHeldLongerThanTheLimitIsNotATap`、`testCapsLockOnIsNotATap`、`testHoldingOneShiftAndTappingTheOtherSwitches`、`testAStaleOtherShiftBitDoesNotSpoilATap`、`testTwoShiftKeysPressedTogetherAreNotATap`、`testAMissedLeftReleaseDoesNotSpoilARightTap`）。

### 9.2 每個 App 的狀態與殼的按鍵

- 英數狀態每個 App（`client.bundleIdentifier`）各自一個布林，存在殼的記憶體裡（輸入法程序重開就回到中文；新 App 預設中文）。殼在 `activate`、組字擁有者改變、切換、設定關閉時把這個 App 的狀態交給核心（`set_ascii`）。
- **一個控制器只動它自己這個 App**：`Session` 另存 `knownBundle`，最近一次讀到的非空 bundle ID（`activate`、換 owner、切換這三處讀到非空就更新，讀到 nil 或空字串不覆蓋；設定關著時也照樣記）。英數狀態用的 App 鍵在讀到 nil 或空字串時改用 `knownBundle`；`knownBundle` 也是 nil（從來沒讀到過）才用共用的 `""`。這個後備只用在英數狀態的鍵：`applyProfile` 的 bundle、`bundle` 欄位與瀏覽器首音節規則、學習閘門與前文、選單的暫停學習與「這個 App 用聊天設定」，nil 一律照舊。
- **組字區是空的、英數模式**：所有按鍵直通給 App（`handle` 回 false，不經核心），包括標點與空白。
- **組字區有字、英數模式**：殼照常把鍵送進核心，但可印字元的鍵（`event.characters` 是一個 ASCII `0x21`–`0x7E` 的字元，沒有 Control、Option、Command、Caps Lock）送 `ShanjieKey(kind: char, ch: 那個字元, modifiers: 0)`；字元照目前鍵盤配置從 `event.characters` 取（Shift＋`1` 是 `!`），不是鍵帽。空白鍵、Enter、Backspace、Esc、方向鍵照組字區的規則。核心沒有影子可用時（`shadowOn` 為 false：倚天排列、可省略韻母、中英混打關著）不送 ASCII 字元。
- **核心沒有英文段可用時**：組字區有字時切到英數，殼先照 Enter 送出組字區（`reset` 模式 0），再進英數模式。
- 回中文：再單按 Shift。Caps Lock 燈亮時單按 Shift 不切換；英數模式不改變 Caps Lock 鍵的行為（規則 1 直通）。安全輸入（密碼欄）時殼不送鍵給核心，英數模式不改變這一點。
- 驗證：`ShiftToggleSessionTests`（`testTapSwitchesAndShowsTheHintAtTheInsertionPoint`、`testEnglishWithNothingComposedPassesEveryKeyThrough`、`testComposedTextIsKeptAndTheNextCharactersAreEnglish`、`testShiftOneIsTheCharacterTheKeyboardMadeNotTheKeycap`、`testEachAppRemembersItsOwnState`、`testANilBundleReadToggleUsesTheKnownApp`、`testAnEmptyBundleReadToggleUsesTheKnownApp`、`testSessionsActivatedWithTheToggleOffStillKnowTheirApp`、`testAClientThatNeverReadABundleIDSharesTheEmptyEntry`、`testWithoutAShadowTheCompositionIsSentFirst`、`testSettingOffStopsTheTapAndClearsEveryApp`、`testAsciiKeysAreNotSentWithoutAShadow`、`testARightShiftTapSwitchesToo`）。

### 9.3 核心的 ASCII 狀態（`set_ascii`）

- `shanjie_engine_set_ascii(on)`（預設 0）是殼依 App 交給核心的狀態；`reset`、送出都不清它。回傳快照（`commit` 可能非空，例如結束英文段時組字區到第 128 個單位整個送出）。組字區是空的時只記下狀態。
- **`set_ascii(1)`**（核心的 `shadow_on()` 為 false 時只記下狀態，組字區不動，ASCII 字元分支也不適用），照順序：(1) 候選窗或預測列開著 → 關掉，不選；(2) 數字串開著 → 照數字層的邊界規則結束（緩衝吐成 token）；(3) 有未完成音節 → 影子的鍵照打的順序成為英文段的開頭（和 Shift＋字母開段相同，打 ㄏ 再切英數，段是 `c`）；(4) 沒有英文段 → 下一個字元進來時在游標處開一段（空段不存在）。
- **ASCII 狀態下的鍵**：緊接在規則 1 之後、在已進入的預測列、Tab 進預測列、數字串、英文段、候選窗之前，而且核心有影子可用：`kind` 是 char、`ch` 是 `0x21`–`0x7E`、沒有修飾位元 → 先關掉候選窗或預測列（不選，所以 `1` 永遠是字元，不是選字）。
  - 字元是英文段收的（`[A-Za-z0-9,.;/-]`）→ 原樣接進英文段（沒有段就開一段；不轉小寫、不走聲調鍵、不回退、不切分；段長上限 32，第 33 個先結束前一段）。
  - 其他可印字元（`! ? @ ' ( :` 等）→ 先把開著的英文段結束成 token（不插空白），再以這個字元本身當一個半形標點單位（讀音 `_punct_` 加字元；Backspace 整個刪掉；它不是英文 token，所以每個英文 token 都守英文段的字元集不變量）。
  - 一個字元的英文段是數字（`3`）或 `.`、`/`、`-` 時，結束成的是數字 token。
  - **空白鍵**：沒有英文段開著、游標緊接在任何 token 單位之後（英文 token、半形標點單位、數字 token、空白單位），而且窗與預測列都沒開 → 打一個半形空白單位（規則 16a）；有英文段開著 → 結束它（下一個空白鍵才打空白）。所以 `Hi! there`、`3 apples` 都打得出來。其他鍵照一般規則（↓ 開寫法清單、Enter 送出、Backspace 刪段的最後一個字元）。
- **`set_ascii(0)`**：開著的英文段結束成 token（不插空白），回到一般規則；組字區其他部分不動。
- 規則細節（英文段、影子、token）見 `docs/spec/english-mixed.md`。
- 驗證：`core/tests/engine_english.rs`（「我要」＋`set_ascii(1)`＋`ok`＋Enter 送出「我要ok」；`set_ascii(0)` 之後的注音照常；候選窗與預測列在 `set_ascii(1)` 時關閉；數字串先吐成 token；`!`、`3`、`/`、大寫 `O` 原樣接進段）；`core/src/ffi.rs` 的 `english_codes_through_the_c_abi` 涵蓋 `on` 不是 0／1 回 2。

### 9.4 英／中提示

- 每次切換顯示「英」或「中」，`SHIFT_HINT_SECONDS` = 1.0 秒後淡出（系統預設的動畫曲線）。輸入法被啟用（換 App 或組字擁有者改變）之後的第一個打字的鍵、同一個 App 裡停打 30 秒以上（`modeHintIdleSeconds`，含睡眠時間）之後的第一個打字的鍵也顯示一次，位置與時間相同；組字中不顯示，切換剛顯示過也不再顯示第二次；設定關著時都不顯示。
- 自己一個視窗（不用候選窗的 `PanelWindow`，候選窗開著時兩個都看得到），外觀用候選窗的玻璃面板，只放一個字；高度、字級（16 pt）與左右留白（10 pt）取候選窗的值。
- 位置：錨在客戶端游標的矩形，放在這一行的上方、左緣對齊游標（候選窗在下方，不重疊）。組字區有字時用標記文字的矩形，空時用客戶端的插入點矩形。客戶端給不出矩形時不顯示，不退到畫面角落或上一次的位置；狀態照樣切換（`testNoRectangleNoHintButTheStateSwitches`）。
- 候選窗開著時單按 Shift：提示出現，候選窗照 §9.3 的第 1 步關掉。

### 9.5 偶爾卡在英文的兩個情況

- (A) Shift 的 `flagsChanged` 落到另一個控制器，它讀到的 bundle ID 是 nil，切換的是共用的 `""` 那一格，提示畫在讀不到游標的客戶端上所以不顯示；下一個按鍵回到原控制器，`claim()` 後的 `syncAscii` 又照原 App 的紀錄拉回英文。處理：§9.2 的 `knownBundle`。
- (B) 另一側 Shift 的裝置位元殘留在旗標裡：`ShiftTap` 看到兩個 Shift 都按著，每一次單按都不算，直到那一側再按放一次。處理：§9.1 第 4 項（另一側狀態和按下時相同就照常判斷）。
- 這兩條路徑是從回報和程式閱讀推論出來的，沒有實機紀錄證實；若再有回報卡在英文，需要記下當時的 App、動作與是否出現提示。
- 驗證：`testANilBundleReadToggleUsesTheKnownApp` 與 `testAClientThatNeverReadABundleIDSharesTheEmptyEntry`（(A)）；`testAStaleOtherShiftBitDoesNotSpoilATap`、`testTwoShiftKeysPressedTogetherAreNotATap`（(B)）。

## 10. 驗證指令

- 核心與評測：`make test-rust`（`cargo test --release --locked`，含 `core/tests/*.rs`、`cli/tests/*.rs`）；只跑一個檔：`cargo test --release -p core --test engine_repick`。`core/tests/engine_cap.rs` 只在 release 編譯。
- 按鍵重播：`core/tests/engine_replay.rs` 的 `replay_standard_302_of_302`、`replay_eten_302_of_302`：把 `--set dev --limit 302` 的 302 列讀音依排列轉成按鍵（每個音節依聲母、介音、韻母按符號鍵，再按聲調鍵，一聲用空白鍵，最後按 Enter），送出的字串必須等於 `decode_beam(lex, 讀音, NoLearning, BEAM_S1)` 的第一名，302／302。這個測試只證明「按鍵 → 音節 → 解碼」這條路正確；選字、游標、刪除由引擎行為測試負責。
- 每鍵時間：`core/tests/engine_predict.rs` 的 `timing_typing76_keys`（`cargo test --release -p core --test engine_predict timing_typing76_keys -- --ignored --nocapture --exact`），斷言每鍵 p95 < 16 ms。
- 殼：`make test-swift`（`ShiftTapTests`、`ShiftToggleSessionTests`、`RerankTests`、`PunctuationTableTests`、`ShellTests`）。
- 回歸：`make test-golden` 與 dev302、打字測驗、錯字回報的重播在按鍵規則不變的改動上必須逐列相同。

## 11. 已知限制

- 長段前段固定需要 n-gram 語言模型；沒有模型（unigram）時組字區不固定也不限長度（仍受 128 單位限制）。
- 前段固定以字數計，字數與音節數不同的條目（疊加層可以有）照字數；出貨詞庫目前沒有這種條目。
- 改選不會重新解碼範圍外的字，所以改選之前兩個詞合起來才對的錯誤（解碼的錯在範圍外）要靠再改選去修。
- 合併出來的詞之後不會自己拆開重算，只能用改選。
- 標點表與名稱表讀的是私有框架的資源，系統更新可能改位置或格式，屆時退回內建表（沒有名稱）。
- 單按 Shift 的 `SHIFT_TAP_MAX` 是推論值；按住一側 Shift、單按另一側會切換。
- 等候重排不提升佇列優先權，負載高時 Enter 可能逾時而送出 n-gram 的字。
- 單鍵延遲依機器和負載而變；§5.4 的數字是低負載下的量測。
