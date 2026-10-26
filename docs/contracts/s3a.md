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
- **串、緩衝、切分（數字列的鍵，`docs/contracts/digits.md`；中英混打開著、不是可省略韻母、標準排列、有模型和 `digits.tsv` 時才存在，否則逐位元同沒有這一段）**：沒有未完成音節時，字母表 `{0-9 . / -}` 的鍵開一個**串**；串裡還沒變成 token 的鍵放在**緩衝**（最多 4 個，第 5 個鍵進來時最舊的先吐成數字串 token）。緩衝的 n 個鍵每一鍵都分成「前 m 個是數字、後 t 個是音節尾段」（尾段要合法：是詞庫某個音節的前綴；以聲調鍵或空白結尾時要是詞庫裡含漢字詞條的完整音節），由 `digits::Scorer` 照 `digit-context.md` 的表打分選一個（θ＝3 保護整串都當注音的讀法；修訂三起不在字母表的注音鍵也算；`digits.md` A.3.3、A.9）。畫面上數字顯示成半形、尾段顯示成注音；尾段同時放進未完成音節（`pend`），所以預測列、Esc、Backspace 照未完成音節的規則看它。數字串 token 的讀音是 `_punct_` 加那個字元，固定詞是那個字元，像標點一樣只佔一格。其他鍵的規則在 §3.2。
- **影子（中英混打，`docs/contracts/zhen-mixed.md` §2.2）**：`set_english(1)`、`set_abbreviation(0)`、標準排列、單字表已載入，四個都成立時，未完成音節從第一個注音鍵起另外記下每個**注音鍵**（第 9、14 條處理的鍵）的鍵帽字元（不含 Shift，最多 32 個）和「覆寫次數」（第 9 條裡那一欄已有符號、被取代的次數）；否則影子一律是空的、不記。聲調鍵與空白鍵不記。第 33 個注音鍵把影子清空（長度 0、覆寫 0），之後的鍵重新開始記。影子跟著 `clear_pend` 一起清（完成音節、Esc、標點、送出、reset、回傳碼 4、第 11 條刪到未完成音節消失）。倚天排列不記影子。**〔第三片，`zhen-keyorder.md` §2.1〕** 影子另外記「逆序次數」（不是覆寫、而且這個鍵的欄位小於未完成音節目前已有符號的某一欄的鍵數，欄位 0 聲母、1 介音、2 韻母，看目前三欄、不看打過的鍵）；逆序次數只在 `set_english_key_order(1)` 時用，次數只增不減（第 11 條不減，影子清掉時一起清）。**影子現在決定顯示與送出的內容**：顯示規則 D（下一條）成立時，組字區顯示、Enter 與 `reset(commit)` 送出的是影子的字母，不是注音。所以 D 成立（字母在畫面上）時的 Backspace 不照第 11 條（修訂四，`zhen-inline.md` §1.3g）：影子的字母去掉最後一個，剩下的在游標處變成英文段（未完成音節的符號與影子清掉），不重放、不回到注音。D 不成立（畫面上是注音，或「按空白才顯示」）時第 11 條照舊：刪顯示位置最後的符號，影子丟掉最後一個記下的鍵、覆寫次數不變。
- **英文 token 與顯示規則 D（中英混打第二片，`docs/contracts/zhen-inline.md`）**：選了英文窗的英文項，英文不送出，而是在游標處插入一個**英文 token**（讀音 `_punct_` ＋ 那一項，固定詞就是那一項，和第 2 條的標點 token 一樣只佔一格：切開解碼的段落、後面的字以「前面不是漢字」處理、Backspace 與方向鍵整個一次、Enter 時照順序送出、算進 128 個單位的上限、英文本身不學；修訂四起中英混打開著時 Backspace 對英文 token 逐字母刪，見第 18 條）。兩個和標點不同的地方：緊接在它後面按 ↓（修訂三起空白不再開清單，見第 16a 條）列出這個字的寫法：照打的原樣（token 目前的文字）在前，再接小寫、首字大寫、全大寫，去掉重複的（選了就換掉，不查標點替代表；修訂二起，細節見下方「英文 token 改選」）；句首的詞後面緊接它時，第 8 條的鍵（`learn::next_key`）用 `^>_punct_en`，不含英文字（空白單位同樣用這個鍵）。**D**（`set_english_inline` 預設 1）：影子在記錄、游標在尾端且只有一個未完成單位、影子至少 3 個字母、覆寫條件和觸發相同（覆寫 ≥ 2，或 ≥ 1 而且殘餘音節不在詞庫；`set_english_key_order(1)` 時「覆寫」換成 E，見第 10e 條。「殘餘音節不存在可以不要證據」與兩字母詞只在空白鍵，D 與自動固定不放寬，兩字母詞也不在打字時顯示或自動固定）、單字表裡有字以影子開頭，五個都成立時，組字區把未完成音節的注音換成影子的字母（`shown()`；候選窗、預測列不變）。每個按鍵處理完重新判斷，不記狀態。

## 3. 按鍵行為（暫定，S3b 會對照使用者的截圖與實機修正）

**判斷順序**：由上往下，第一條符合的規則生效。

| # | 狀態 | 按鍵 | 行為 |
|---|---|---|---|
| 0 | 預測列已進入 | ⌘⌫ | 遺忘選取的那一項（不動任何固定詞，repick-freeze §7.2）：丟掉同一個（詞、讀音）的待學記錄，`learner.forget` 並照候選窗 ⌘⌫ 的規則改寫學習檔（學習暫停時也可以），重算預測列，仍是已進入、選取移到那個詞的新位置（不在列中是 0）；判斷在第 1 條之前（V3，`v3-engine.md` §10.3；修訂一取代原本的「吃掉、不動作」）。未進入時照第 1 條直通 |
| 1 | 任何 | 帶 COMMAND、OPTION、CAPSLOCK，或帶 CONTROL 但不是 Ctrl+\ | 不處理（直通），**不改任何狀態** |
| 1a | 預測列已進入 | 1–9 | 選預測列的第 n 個（超過個數就吃掉、不動作）；選取見 `v3-engine.md` §2 |
| 1b | 預測列已進入 | ←→、Tab（無修飾鍵） | 移動選取（Tab 往後，到最後一個停住）。**設定是直排時**（`candidate-vertical.md` §2.4）↓ 同 →、↑ 同 ←，也在兩端停住；橫排時 ↑↓ 不是預測列的鍵，照第 1e 條 |
| 1c | 預測列已進入 | Shift+Tab、Esc | 退回未進入（預測列保留） |
| 1d | 預測列已進入 | Enter | 選目前選取的 |
| 1e | 預測列已進入 | 其他鍵（含 Backspace；橫排時含 ↑↓） | 退回未進入，再從第 2 條起處理這個鍵。Backspace 有未完成音節時照第 11 條刪最後一個符號並重算預測列（和第 7 條不同，使用者選擇）；沒有未完成音節時照第 18 條 |
| 2 | 任何 | §4 的標點鍵（Shift 表與單按表）、Ctrl+\ | 丟掉未完成音節（多個單位時是全部）、關閉候選，**把標點插入組字區的游標處**（不送出；2026-10-04 起，見 `docs/contracts/s3d-punctuation.md`；原本是「送出組字區，再送出標點」） |
| 3 | 候選開啟 | 1–9 | 收合：選目前頁的第 n 個（超出本頁則忽略），關閉候選。展開：選選取所在那一排的第 n 個（每排 9 個；最後一排不足 n 個則吃掉、不動作，s3b2 §8.2、§9） |
| 4 | 候選開啟 | ↑↓、←→ | 收合：↑←上一個、→下一個（跨頁），↓ 展開成網格（選取不動）。展開（一律 9 欄，一排是一頁，展開時選取所在的那一頁在最上面，s3b2 §9）：↓ 下一排同一個位置（最後一排較短時到該排最後一個，已在最後一排時不動）、↑ 上一排（在最上面那排但不是整份清單的第 0 排時往上捲一排；只有第 0 排才收回，選取不變）、←→ 上／下一個（跨列）。←→ 和系統注音一樣逐一移動（2026-10-04 使用者實測後改；原本是翻頁；2026-10-05 起 ↓ 改為展開，s3b2 §8.2） |
| 5 | 候選開啟 | 空白鍵 | 收合：下一頁，最後一頁再按回第一頁。展開：和 ↓ 相同，但在最後一排時回第一排同一個位置 |
| 3v | 直排候選窗開啟（`candidate_vertical` 1） | 1–9 | 取代第 3 條。選**可見範圍**的第 n 個（超出可見個數則忽略、候選保持），關閉候選（`candidate-vertical.md` §2.2） |
| 4v | 直排候選窗開啟 | ↓、→ | 取代第 4 條。下一個；選取在可見範圍最後一個時，可見範圍往下捲**一個**（選取停在可見的最後一列，編號跟著可見的列重排成 1–9）；已是整份清單最後一個時不動。直排沒有網格，↓ 不展開 |
| 4w | 直排候選窗開啟 | ↑、← | 取代第 4 條。上一個；選取在可見範圍第一個時，可見範圍往上捲一個；已是整份清單第一個時不動 |
| 5v | 直排候選窗開啟 | 空白鍵 | 取代第 5 條。和 ↓ 相同（下一個，不是翻頁；蘋果注音直排實測），已是整份清單最後一個時不動（不回第一個） |
| 5p | 直排候選窗開啟，無修飾鍵 | Page Down（kind 15）、Page Up（kind 14） | Page Down：可見範圍往後移 9 個（不超過讓最後一個候選可見的位置），選取在新的可見範圍第一個，**但選取不往回**：被夾住、新的第一個比原本的選取還前面時選取不動（10 個候選，↓ 八次（選取在位置 8，從 0 起算，即第 9 個候選）後 Page Down，選取仍在位置 8）；已在最後時不動。Page Up：往前移 9 個（最小 0），選取在第一個；已在最前時不動。兩者都是已處理（`handled` 1）。殼只在最後一次輸出的 `candidate_vertical` 為 1 且沒有任何修飾鍵時才送這兩個 kind |
| 5q | 不是直排候選窗（橫排候選、網格、組字中、未完成音節、預測列（含直排預測列）、組字區空），或帶任何修飾鍵 | Page Down、Page Up（kind 15、14） | 不處理（`handled` 0、`commit` 空），**不改任何狀態**（含預測列）。只有殼出錯時才會收到；殼平常不把這兩個鍵送給核心，它們走 `s3b.md` 的表外鍵路徑 |
| 6 | 候選開啟 | Enter | 選目前選取的，關閉候選 |
| 7 | 候選開啟 | Esc、Backspace | 關閉候選，不改變 |
| 8 | 候選開啟 | 其他鍵 | 關閉候選（不改變），再從第 9 條起處理這個鍵 |
| 8a | 預測列未進入且不是空的（候選窗關閉） | Tab（無修飾鍵） | 進入預測列，選取第 0 個（插在第 9 條之前）；預測列是空的時照第 13、21 條 |
| 9 | 有未完成音節 | 注音鍵 | 放進對應欄位（同一欄再按一次是取代）。**例外（V3 第二片，`v3-engine.md` §12）**：「可省略韻母」開、預測列開、游標在組字區尾端時，那一欄已有符號就**開新的未完成單位**（最後一個單位的那一欄是空的就放進去）；有兩個以上單位時，聲調鍵與空白鍵見 9a。要有語言模型；單位上限 `ABBR_MAX_UNITS` = 10，到了上限已有符號的那一欄照取代。**例外（中英混打修訂二，`zhen-inline.md` §1.3「自動固定」）**：顯示規則 D 成立（字母在畫面上）、影子本身是單字表的完整單字、影子加上這個鍵不是任何單字的前綴時，這個鍵不放進欄位：先把影子的字母變成英文 token（和英文窗選第一項、小寫原樣相同，另外記下當時欄位上的未完成音節，見英文 token 改選），清掉未完成音節與影子，再把這個鍵當新音節的第一個鍵（第 14 條）。聲調鍵、空白鍵不觸發；「按空白才顯示」時 D 不成立，不觸發。自動固定讓組字區到 128 個單位而送出時，送出的文字在**這個鍵的這一次輸出**的 `commit` 裡，這個鍵同一次輸出已在清空的組字區開始新音節 |
| 9a | 有兩個以上未完成單位 | 聲調鍵、空白鍵 | 吃掉、不動作（單位只靠預測列選取、Backspace、Esc 解決；最多 10 個單位，見第 9 條）；只有一個單位時照第 10 條 |
| 10e | 有未完成音節（一個單位），影子是單字表裡的字、覆寫次數 ≥ 1（殘餘音節——目前三欄的符號加一聲——在詞庫裡時要 ≥ 2），游標在組字區尾端（`zhen-mixed.md` §2.2）。**〔第三片，`zhen-keyorder.md` §2〕`set_english_key_order(1)` 時**：「覆寫次數」換成英文證據 E ＝ 覆寫次數 ＋ 逆序次數（一個鍵同時是覆寫又是逆序只算覆寫）；另外兩種情況也算：影子是單字表裡的字而殘餘音節不存在（不要證據，第 10 條對這個空白本來什麼都不做），和影子正好是 `TWO_LETTER`（核心常數：ok ls cd mv ip id ux js ts py vs pr os tv pc db qa pm hr）裡的一個字而且 E ≥ 1（不論殘餘音節存不存在） | 空白鍵 | 不當一聲，開英文窗（見下）。其他情況照第 10 條 |
| 10 | 有未完成音節 | 聲調鍵、空白鍵 | 完成音節（§2） |
| 11 | 有未完成音節 | Backspace | 刪掉顯示位置最後的符號（韻母→介音→聲母；2026-10-06 實測蘋果注音與小麥注音）。多個單位時刪最後一個單位的最後一個符號，空了的單位拿掉。「打字時就顯示」、中英混打開著、未完成音節刪到消失、游標左邊緊接自動固定做出來的英文 token 時，那個 token 以全部文字變回英文段（修訂六，`zhen-inline.md` §1.3i）。**例外**：中英混打的顯示規則 D 成立（畫面上是影子的字母）時（修訂四，`zhen-inline.md` §1.3g），刪最後一個字母，剩下的字母變成英文段（見 §2 影子） |
| 12 | 有未完成音節 | Esc | 清掉未完成音節（多個單位時是全部單位；組字區的字、固定詞、游標不動） |
| 12a | 有未完成音節 | Enter（沒有修飾鍵） | 送出組字區顯示的整串：游標前的顯示文字、未完成音節（多個單位時是全部單位）的符號（照顯示順序；**顯示規則 D 成立時是影子的字母，不加空白**，見 §2）、游標後的顯示文字，然後清空組字區（2026-10-09 起，蘋果注音實測；`docs/contracts/enter-pending.md`；原本是第 13 條的吃掉） |
| 12b | 有未完成音節 | Shift+Enter | 照第 12a 條送出，然後直通這個鍵（`handled = 0`，App 收到換行；蘋果注音實測） |
| 13 | 有未完成音節 | 其他鍵 | 已處理，忽略。**例外（中英混打修訂二，`zhen-inline.md` §1.3b）**：中英混打開著時 Shift＋英文字母（A–Z）開始英文段，不忽略（見下）。**例外（預測列與 ↓ 一致，`predict-down.md` B.3.1）**：恰好一個未完成單位、這個鍵之前顯示著未進入的預測列（不是空的）時，↓ 開候選窗，清單只有預測列的項目（原順序；未完成的音節沒有詞庫候選）；預測列是空的時 ↓ 照舊忽略。空白鍵、Tab 不變 |
| 14 | 沒有未完成音節 | 注音鍵 | 開始新的未完成音節 |
| 15 | 組字區有字 | 空白鍵、↓ | 開啟候選（§3.1）。這個鍵之前顯示著未進入的預測列（已進入的列先照第 1e 條退回）時，清單先放預測列的項目（原順序），再接 §3.1 的詞庫清單去掉已出現的詞（`predict-down.md` B.3.1）；選預測項目等於在預測列選它（`choose_pred`），但讀音正好是游標前已打的音節的項目就是詞庫在那個範圍的候選，照改選處理（B.3.1 修訂，2026-10-17），⌘⌫ 在預測項目上遺忘它（`s4-learning.md` §1.5） |
| 16 | 組字區有字 | 聲調鍵（空白鍵以外）、↑ | 已處理，忽略 |
| 16a | 沒有未完成音節、沒有開著的窗，游標緊接在**英文 token 或空白單位**後面（中英混打修訂三，`zhen-inline.md` §1.3e） | 空白鍵 | 取代第 15 條：在游標處插入一個半形空白，作為一個單位（讀音 `_punct_ `、固定詞 `" "`，和標點 token 相同：切開解碼段落、Backspace 與方向鍵整個一次、Enter 與其他送出照順序送出、算進 128 個單位的上限，到上限時送出的文字在這個鍵的輸出的 `commit`）。英文段結束是「第一下空白斷開（吃掉，見英文段）、第二下空白才是空白」。其他位置的空白鍵照舊；英文 token 的寫法清單只用 ↓ 開：游標在 0、最前面是英文 token 或空白單位時空白鍵吃掉、不開清單；空白單位沒有清單（↓ 也不開）。本條只在中英混打開著時成立，關著時 token 後面的空白鍵照第 16 條。空白單位學習時和英文 token 同一個鍵（`^>_punct_en`） |
| 17 | 組字區有字 | ←→、Home、End | 游標在音節之間移動 |
| 18 | 組字區有字 | Backspace／Delete | 刪掉游標左邊／右邊的音節；游標在邊界時忽略。**例外（中英混打修訂四，`zhen-inline.md` §1.3g）**：中英混打開著（`shadow_on()`）、沒有未完成音節、沒有開著的窗與英文段，Backspace 時游標左邊緊接英文 token：拿掉那個 token，在原位開英文段，內容是 token 的文字去掉最後一個字母（大小寫照原樣；只有一個字母就整個拿掉，不開段）；兩種顯示設定都適用。空白單位、標點 token 與中英混打關著時照舊整個刪。刪在多音節詞裡面時（解碼出來的、`frozen` 的或使用者自己選的，不論有沒有改選過），那個詞的其他字逐字保留（repick-freeze §11） |
| 19 | 組字區有字 | Enter（沒有修飾鍵） | 送出組字區顯示的整句 |
| 19a | 組字區有字 | Shift+Enter | 送出組字區顯示的整句，然後直通這個鍵（2026-10-09 起，蘋果注音實測；原本送出後吃掉，App 收不到換行） |
| 20 | 組字區有字 | Esc | 清空組字區 |
| 21 | 組字區有字 | 其他鍵（含 Shift＋非標點、Tab） | 送出組字區，然後直通這個鍵（`handled = 0`、`commit` 非空）。**例外（中英混打修訂二，`zhen-inline.md` §1.3b）**：中英混打開著時 Shift＋英文字母開始或延續英文段，不送出、不直通；中英混打關著或倚天排列時照舊 |
| 22a | 組字區空 | 聲調鍵（空白鍵以外） | 把該聲調符號（ˊ ˇ ˋ ˙）放進組字區，和第 2 條的標點相同（2026-10-05 起；蘋果注音實測按 3 打出「ˇ」但立刻送出，使用者選擇留在組字區，Backspace 可刪、Enter 才送出；原本直通成數字） |
| 22 | 組字區空 | 其他鍵 | 不處理（直通），並清掉 `set_left_context` 存的前文（漢字結尾與 `left_full`，`context-return.md` §2）。**例外（中英混打修訂二）**：中英混打開著時 Shift＋英文字母在游標處開一個英文段（組字區因此有字） |

**英文窗（中英混打，`docs/contracts/zhen-mixed.md` §2.3）**：第 10e 條開的候選窗，`Cands.english`。清單依序是影子的小寫字 `w`、首字大寫、全大寫（影子是小寫，所以「照打的原樣在前」就是 `w`；寫法清單的一般順序見下方英文 token 改選），殘餘音節在詞庫裡時第四項是詞庫對那個一聲音節的第一個詞條（`lex.entries(&[音節])[0]`）；方向照 `set_candidate_vertical`，開窗時固定。開窗時預測列清掉，preedit 照現行顯示（顯示規則 D 成立時是影子的字母，否則是未完成音節的注音）。第 3–8、3v–5p 條的其餘行為不變，英文窗開著時：

| 按鍵 | 行為 |
|---|---|
| 1–9、Enter（含 Shift+Enter）、滑鼠 `pick(index)` | 選第 n 項／選取的那項（超出項數照第 3 條忽略）。有窗開著時 Enter 是窗的，不走第 12a 條。**英文項**（中英混打第二片起不送出）：先 `clear_pend`（影子跟著清），再在游標處插入英文 token（§2；`commit` 空，組字區繼續，游標在它後面；重排工作因重新解碼作廢；預測列照選了候選的規則重算）。**中文項**：清影子與未完成音節，把那個一聲音節插在游標處並固定那個詞（長度 1、`pre` 是 `None`，不學），和第 10 條一樣重算、預測列重算 |
| ←→↑、↓（展開）、空白鍵（只有一頁，回到第 0 項）、Page Up／Down（直排） | 照第 4、5、4v–5p 條，不送出 |
| Esc、Backspace | 關窗、**清影子**，未完成音節保留（第 7 條加上清影子；再按空白鍵就照第 10 條） |
| ⌘⌫ | 吃掉、不動作（`handled` 1、狀態不變、不碰學習檔） |
| 注音鍵（不是選項目的數字鍵 1–9 的那些，即字母、`0`、`,` `.` `;` `/` `-`；中英混打修訂二，`zhen-inline.md` §1.3） | **先把窗目前選中的項目固定**：和按 Enter 選它相同（英文項成為英文 token 並記下當時欄位上的未完成音節，中文項成為固定詞、不算自動固定）、關窗、清影子，再把這個鍵當新音節的第一個鍵（第 14 條）。所以 `r u s t ␣` 之後直接打中文，英文會留著。128 個單位的上限照 §1.3：送出的文字在這個鍵的輸出的 `commit` 裡 |
| 其他鍵（含聲調鍵、Tab、Delete、Home、End；聲調鍵 6 3 4 7 在標準排列是 1–9 的數字，仍是選項目鍵） | 第 8 條：關窗、清影子，再從第 9 條起處理這個鍵 |
| Shift＋英文字母（修訂二，§1.3b） | 關窗，影子的字母變成英文段的開頭，再接上大寫字母（見英文段） |
| 標點（第 2 條） | 第 2 條在第 3–8 條之前：丟掉未完成音節（影子跟著清）、關窗、插入標點 |

選了英文項之後：`handled` 1、`commit` 空、`preedit` 是組字區（含那個英文）、沒有候選（插入第 128 個單位時照第 2 條送出整串）。游標不在尾端、倚天排列、`set_english(0)`、沒有單字表、`set_abbreviation(1)` 時不記影子（或不觸發）、不開窗，和沒有這個功能時逐位元相同。

**英文 token 改選與英文段（中英混打修訂二，`docs/contracts/zhen-inline.md` §1.3、§1.3b、§1.3c；放在表外）**：

- **自動固定**：第 9 條的例外與英文窗表的「注音鍵」那一列。自動固定產生的 token（D 成立時打接不下去的注音鍵、英文窗開著時打注音鍵）會記住做出它的那一刻欄位上的未完成音節（`pending()`，一聲；不是重放影子的字母，第 11 條刪過符號時兩者不同）；引擎用 `#[doc(hidden)] Engine::english_autokept()` 回報「最近一次呼叫（`key`、`pick`、`reset` 與回傳快照的 `set_*`，每次呼叫開頭先清掉；其他設定函式不動它）做了自動固定」，英文窗開著、選中的是中文項時打注音鍵不算（模擬器計數用，不經 C ABI）。
- **英文 token 改選**：游標緊接在英文 token 後面（可以用方向鍵移到那裡）、沒有未完成音節時，↓ 開出這個 token 的寫法清單（修訂三起空白插入空白，第 16a 條）（有未完成音節時方向鍵與 ↓ 照第 13 條，其他鍵忽略；第 15、16 條是沒有未完成音節時的規則）：照打的原樣在前，再接小寫、首字大寫、全大寫，去掉重複的（`iPhone` → `iPhone`、`iphone`、`Iphone`、`IPHONE`）。**自動固定產生的 token** 另外多一個中文項：做出 token 當時欄位上的未完成音節（一聲）在詞庫裡的第一個詞條（和英文窗的第四項同一規則；音節不在詞庫就沒有中文項，例如 `rust` 的 ㄔㄧ）。選中文項：token 的讀音換成那個音節、固定那個詞（不學），英文不見；選另一種寫法：token 換成那一項，仍記著那個音節。英文段與英文窗選的 token 沒有音節可記，清單只有寫法。
- **英文段（Shift＋英文字母，中英混打開著，`shadow_on()`；倚天或關著時第 13、21、22 條照舊）**：開始時，組字區空或有字沒有未完成音節 → 在游標處開段，第一個字母是大寫（核心用 `ch` 與 SHIFT 位元算出，殼不改：`KeyMap.translate` 把字母鍵的未按 Shift 鍵帽放在 `ch`、Shift 放在 `modifiers` 位元 0，`Session.send` 原樣交給 `engine.key`）；有未完成音節而且影子全是英文字母（含英文窗開著，窗關掉）→ 影子的字母照打的樣子成為段的開頭再接大寫字母；影子含非字母或是空的 → 照 Esc 丟掉未完成音節再開段。段開著時不算組字區單位；組字區顯示它，游標在它後面；最多 32 個字母，第 33 個先結束前一段再開新的一段。段開著時：字母鍵（有沒有 Shift 都算，照打的大小寫）接在段後面，**段不自動固定**（修訂三：沒按 Shift 的字母也接在段後面，要回中文先按空白）；Backspace 刪最後一個字母（刪光就關段）；Esc 拿掉整段；空白鍵結束並被吃掉；其他鍵（聲調鍵、數字鍵、標點、Enter、Tab、方向鍵…）先結束，再照原本的規則處理。結束＝照 `insert_token` 插入英文 token（讀音 `_punct_` ＋ 字母原樣），到 128 個單位時送出整串，送出的文字放在結束這個段的那個鍵的輸出的 `commit`，這個鍵接著在清空的組字區照原規則處理（空白被吃掉、Enter 不直通也不另外送出）。段開著時輸出的 `english_run_utf16_start`／`english_run_utf16_len` 是段在 `preedit` 裡的起點與長度（UTF-16），其他時候都是 0；殼在那個範圍畫粗底線、在候選窗的位置顯示一行提示「空白：英文斷開」（`zhen-inline.md` §1.3f）。`reset(commit)` 與 Enter 送出段的字母原樣；`reset(discard)` 清掉段；`set_english(0)` 與 `set_abbreviation(1)` 把段留成 token（到 128 個單位而送出時，文字放在該設定函式回傳的快照的 `commit`）。

**直排候選窗與直排預測列**（`docs/contracts/candidate-vertical.md`，放在表外：夾在表中間會讓後面的列不再顯示成表格）：方向由 `shanjie_engine_set_candidate_vertical` 設定（預設橫排）。**候選窗在開啟時固定方向**（第 15 條與 s3e 的標點候選開窗時讀；設定只影響下一次開窗，開著的窗，含展開的網格，照開啟時的方向）。**預測列沒有開窗的時間點**，每個輸出都照 engine 當下的設定，所以設定的回傳快照裡正在顯示的預測列就換成新的方向。直排候選窗的狀態是「整份清單、可見範圍的第一個、選取」，可見範圍固定 9 個（不足 9 個就是全部）；開窗時從 0 開始、選取 0。輸出：`candidates` 是可見範圍、`candidate_selected` 是在可見範圍裡的位置、`candidate_first`／`candidate_total` 照現有意義、`candidate_columns` 一律 0。`candidate_vertical` 是 0（橫排，或沒有候選）、1（直排候選窗）、2（直排預測列，進入或未進入；其他欄位的意義不變）。第 3v–5p 條取代直排候選窗的第 3–5 條，第 6–8 條、⌘⌫ 遺忘與其他條不變；直排預測列進入後的 ↑↓ 見第 1b 條，未進入時 ↓ 照第 15 條、↑ 照第 16 條，與橫排相同。

- **預測列（V3，`docs/contracts/v3-engine.md`）**：有載入語言模型、游標在組字區尾端、候選窗關閉時，第 9、10、11、14 條處理完之後重算（第 10 條遇到詞庫沒有的音節而不完成時不變）；其他所有狀態變化都清掉（標點、22a、開候選窗與候選窗內的鍵、游標移動、刪音節、送出、Esc、`reset`、`set_profile`／`set_demote`／`set_rerank`／`set_rerank_level`／`set_prediction`（關閉時；開啟時重算）、`pick`、選了預測、回傳碼 4）；英文段（Shift＋英文字母，`zhen-inline.md` §1.3b）開著時不產生預測列（Backspace 把段刪光才重算，Tab 先結束段）；什麼都沒改的鍵（↑、不起作用的聲調鍵、游標在邊界的 Backspace／Delete、有未完成音節時的其他鍵、第 1 條的直通）保留預測列。預測列透過候選欄位輸出（§6）。「可省略韻母」（`shanjie_engine_set_abbreviation`，預設關）見第 9、9a、11、12 條與 `v3-engine.md` §12：兩個以上的未完成單位在關掉縮寫或關掉預測列時一併清掉（等於 Esc），一個單位時不動。
- **預測列在英文詞後面（`docs/contracts/english-next.md`）**：資料夾有 `english-next.tsv`（選用，沒有就逐位元同沒有這一段）、有語言模型、預測開著時，預測列起點前面的那一個單位是英文 token（或是一個空白單位、它前面是英文 token；看單位不看顯示，以字母結尾的詞庫詞不算）、而且表裡有那個英文詞（小寫）時，先列表裡這個詞後面常接的中文詞（有未完成單位時只列相容的，其餘相容的詞照原本的順序接在後面），游標在英文 token 或它後面的空白單位後面、沒有未完成單位時也列（只列表裡、詞庫有的詞）。Space 結束英文段後與接著插入空白單位的 Space 之後都重算預測列（所以這時 Tab 照既有規則進預測列，不再把英文送出）。數字語境優先；解碼、候選窗、學習、詞包、語意校正都不經過這張表；FFI 不變。**組字區的起點（`context-return.md` §2）**：起點前面沒有單位時，看 `set_left_context` 的前文結尾（去掉最多一個半形空白後）是不是 2–32 個 ASCII 字母、前一個字元不是 ASCII 字母或數字；宣告了前文長度 L 的會意模型下看保留的 L 個字元，前文比 L 長而這個英文詞從保留的第 0 個字元開始時不算（可能是半個詞）。組字區空著（沒有音節也沒有未完成單位）時沒有預測列。
- 「注音鍵」與「聲調鍵」指 §1 表中的鍵、且沒按 Shift。
- 第 1 條的直通不改任何狀態：一段組字中間插入任意個第 1 條的鍵，之後的輸出必須和沒按過時逐欄位相同。
- 中英切換：用系統的「使用大寫鎖定鍵切換輸入方式」（使用者 2026-10-03 選 Caps Lock），切換時系統停用本輸入法；殼不保留中英狀態，原本寫的 Shift 單按切換不做（見 `docs/contracts/s3b.md` §6）。
- **組字區上限 128 個單位**（`MAX_SYLLABLES`；組字區上限契約 `docs/contracts/composition-cap.md`，原本是 40）：完成第 128 個單位時自動送出整段，限制每鍵延遲與文字留在記憶體的時間。開發集最長一列是 32 個音節，重播測試不會碰到上限。核心另外把超過視窗 W（會意檔的 `ctx − 3` 字，沒有會意時 61；重排開或關都一樣）的自由段前段固定成凍結詞，一次固定到剩 W − 16 字，見 composition-cap §1.2。
- **送出、Esc 清空、reset（§6）時，連同固定詞清掉所有組字狀態。**

### 3.0 數字列的鍵（中英混打開著，`docs/contracts/digits.md` A.3.4）

下面對第 2、9、10、11、12a、13、14、16、22a 條加例外；功能不生效時（關著、可省略韻母、倚天、沒有模型或表）這些條照原文。

| 條 | 例外 |
|---|---|
| 2 | 串開著時標點鍵先把緩衝吐成數字串 token，再照原規則插入標點（`1` Ctrl+\ 是「1、」）。 |
| 9、14 | 沒有未完成音節時，字母表的注音鍵（1 2 5 8 9 0 - . /）開串（評分決定顯示數字還是注音）；有未完成音節時照原規則。串開著時字母表的鍵進緩衝；不在字母表的注音鍵只能進尾段（最好的合法尾段成為未完成音節，前段吐成 token；沒有合法尾段就全吐成數字、這個鍵開新音節）。 |
| 10 | 聲調鍵或空白鍵在串裡：以它完成的音節（詞庫裡含漢字）對「全數字」評分，音節贏就吐出前段、完成音節（記住這幾個鍵，↓ 的最後一項是這串數字，A.3.6）；數字贏時聲調鍵留在緩衝、空白鍵變成空白單位。 |
| 11 | 緩衝非空時 Backspace 去掉緩衝最後一個鍵並重新評分；緩衝空了照原規則（已吐出的 token 一次一格）。 |
| 12a | Enter 照畫面顯示結束串（`digits.md` A.12）：顯示成數字的前 m 個鍵吐成 token、顯示成注音的尾段留成未完成音節，再送出組字區顯示的整串（`2` 單獨按 Enter 送出「ㄉ」）；其他邊界鍵先把緩衝全吐成 token，再送出整串；`reset(commit)` 同。Esc 清掉緩衝與尾段（token 留著）。 |
| 13 | 其他鍵（方向鍵、Home、End、Delete、Tab 列空、Shift＋字母、Shift＋非標點）是邊界：緩衝先吐成 token，再照原規則處理這個鍵。↓ 與 Shift+Enter 照畫面顯示結束串（`digits.md` A.12）：尾段顯示成注音的鍵留成未完成音節，↓ 開它的預測清單（B.3.1）。 |
| 16 | 聲調鍵 3 4 6 7 在串裡是數字（尾段完成音節除外）；在組字區有字、沒有串時開串，吐成數字串 token，不再被吃掉。數字後面的空白鍵插入一個空白單位（不要求單字表）。 |
| 22a | 標準排列、功能生效時，組字區空的聲調鍵是數字串 token；倚天仍是符號。 |
| ↓ | 數字串 token 後：預測列（數字語境）、那個字元、它的全形（０–９ ．／－）、（有的話）最後幾個 token 可能是的音節的第一個詞條；選全形字固定在那一格，選音節把那幾個 token 換成固定詞。 |

### 3.1 候選與固定詞

- **候選範圍**：設 a＝游標位置；a 為 0 時改用「從位置 0 開始」。列出讀音等於 `音節[a−L..a]`（a 為 0 時是 `音節[0..L]`）的所有詞，L 從 `min(max(max_len, 4), 可用長度)` 遞減到 1；同一個 L 內照 `lex.entries` 的順序（詞庫分數由高到低）；同一個字串只出現第一次。**開窗的鍵之前顯示著預測列時**（`predict-down.md` B.3.1），清單最前面是預測列的項目（原順序），詞庫清單接在後面並去掉預測列已有的詞（去重照詞字串，預測項目先算「出現過」）；`Cands` 另存這些項目，「索引小於預測項目數」就是預測項目。標點、英文 token、空白單位的窗不加預測項目。
- **新詞**（新詞 B，`docs/contracts/newword-b.md` §2.4）：學習檔裡有這個讀音的新詞時（不論權重，所以 ⌘⌫ 找得到每一筆），同一個 L 內先列新詞（權重由高到低），再照 `lex.entries`；詞庫已有的同一個詞照詞庫的位置列。
- **分頁**：收合時每頁 9 個。開啟時在第一頁、選取第 0 個。展開（s3b2 §8.2、§9）為網格，一律 9 欄，輸出可見的最多 5 排。
- **固定詞**：選了長度 L 的詞 w，就把區間 `[a−L, a)`（或 `[0, L)`）固定成 w。與既有固定詞重疊的，舊的移除。游標不動。固定的新詞用它紀錄的分數；紀錄不在了（⌘⌫ 忘記、清除）時，那一段解除固定、照未固定重新解碼，不回錯誤。
- **改選後其他字不動（`docs/contracts/repick-freeze.md`，2026-10-08，有語言模型時）**：選字當下，依選字前顯示的路徑（解碼器為每個詞帶出它佔的音節數，`path` 逐詞存），範圍外的詞全部固定成 `frozen` 固定詞（`pre`、`key` 都是 `None`，送出時不學、不遺忘）：完全在外的詞整個固定（含標點；已經是固定詞的不重複加）；和範圍部分重疊的詞，字數等於音節數時，範圍外的每個音節各自看顯示的字，詞庫有這個單字就固定成單字，沒有就留給解碼；字數不等於音節數的詞，範圍外的音節不固定。使用者自己選的固定詞 `frozen` 是 `false`。候選窗裡讀音正好是已打音節的預測項目照改選（predict-down B.3.1 2026-10-17 修訂），同樣凍結範圍外的詞；其他預測項目（補完未完成音節、縮寫、往後補字）與直接從預測列選，也凍結 `[起點, 游標)` 以外的詞（repick-freeze §13，例（聊天設定）：「以備」後從預測列選「時」→「以備時」；書面設定解成「已被」，選完是「已被時」，同樣不動左邊）。沒有語言模型（unigram）時不變。**插入音節時（修訂一 §6.1）**：左鄰 frozen 詞（`end == 游標`）與右鄰 frozen 詞（平移後 `start == 游標+1`）（標點與使用者自己選的固定詞不算）先拿掉做一次暫定解碼；某個 frozen 詞只有在暫定路徑裡有一個詞涵蓋它的全部音節加上新音節時才放開，其餘放回、再解碼一次，這次的結果才是顯示與 `path`；放開的詞若在第二次解碼後不再被涵蓋，也放回、再解碼一次（修訂二 §7.4）。**修訂五 §11**：放開的鄰居與新音節都是自由的，不再把涵蓋它們的詞凍結，之後的音節可以再改寫它們（蘋果實測：「她是」＋ ㄐㄧˋ ㄓㄜˇ →「她是記者」）；插入點落在多音節 frozen 詞內部時逐音節拆成單字 frozen，這次插入不放開拆出來的字（修訂二 §7.3）。**刪除音節時**，畫面路徑上含該音節的詞（解碼出來的、frozen 的或使用者自己選的固定詞都算）若有兩個以上音節，其餘音節就地變成單字 frozen（有單字條目才保留，其餘交給解碼），再刪那個音節；使用者自己選的那個詞本身照舊移除（待學紀錄跟著消失，拆出來的單字是 frozen、不學）。標點、英文 token、空白單位只佔一個音節，不受影響。沒有「放開紀錄」，刪字不會把舊字放回（蘋果實測：「她世界」Backspace →「她世」）。**修訂七 §15**：這樣拆出的單字記成「刪除拆出」，之後**新打一個音節**（`insert_token` 的音節，不含標點、英文 token、空白單位）時，在解碼之前全部放開、不再固定，照未固定的段落重新解碼（蘋果實測：「他世界」Backspace →「他世」，再打 ㄏㄣˇ →「他是很」）；在那之前畫面照舊保留，Backspace、Delete、方向鍵、改選、送出都不放開。改選與預測選取凍結的詞、使用者自己選的詞不受影響。例：「版控」後在「版」之後打 ㄐㄧㄢ →「版監控」，Backspace →「版控」。例：「他是」改選「她」後再打 ㄐㄧㄝˋ →「她世界」（蘋果注音同）；「版控」後打 ㄐㄧㄢ →「版控間」，「控」不變。例：ㄗㄞˋ ㄧㄝˇ 預設「在野」，第一字改選「再」→「再野」（原本是「再也」）。**語意校正（重排）**：固定的詞不屬於任何自由段落，重排只動沒被固定的段落，所以改選之後範圍外的字（已經是畫面上顯示的，含重排選過的）不會再被重排改動；之後新打的音節照常解碼、重排（2026-10-17 合併 main 後的結果）。
- **重算**：組字區有變動就重算顯示字串。固定詞把音節切成幾段，每段空白區間各自用 `decode_beam(lex, 段, &mut NoLearning, BEAM_S1)` 取第一名，再和固定詞依序串接。現行解碼是 unigram，分數可加，所以這和「整句解碼、限制路徑經過固定詞」的第一名相同。
- **固定詞隨編輯調整**：在游標插入或刪除音節時，完全在左邊的固定詞不動，完全在右邊的跟著平移，跨過該位置的移除。
- 這片只用核心現有的 unigram 解碼與 `NoLearning`；S2 的語言模型與 S4 的學習之後再接上。

## 4. 中文標點（本專案自訂，不複製 Apple 的資料檔）

| 按鍵 | 輸出 | 按鍵 | 輸出 |
|---|---|---|---|
| Shift+, | ， | Shift+[ | 『 |
| Shift+. | 。 | Shift+] | 』 |
| Shift+/ | ？ | Shift+9 | （ |
| Shift+1 | ！ | Shift+0 | ） |
| Shift+; | ： | Shift+` | ～ |
| Shift+\ | ｜ | Shift+' | “ |
| Shift+= | ＋ | Ctrl+\ | 、 |

**單按也是標點的鍵**（2026-10-05 起，該鍵在目前排列不是注音鍵或聲調鍵時才算）：

| 按鍵 | 輸出 | 按鍵 | 輸出 |
|---|---|---|---|
| [ | 「 | ] | 」 |
| \ | 、 | ' | ‘ |
| = | ＝ | ` | · |

倚天排列的 `'`、`=` 是注音鍵，照舊。依據：使用者說「[ 還要打 Shift」，main 用一支只對自己視窗送鍵的探測程式，實測 macOS 內建繁體注音（`com.apple.inputmethod.TCIM.Zhuyin`）在這 6 個鍵單按與 Shift 的輸出（關掉文字視窗的自動替換），表上照它；原本 Shift+[ ／ Shift+] 的「」也改成蘋果的『』。實測沒有複製 Apple 的任何資料檔。

## 5. 資料載入

`data_dir` 裡必須有 `mcbpmf-data.txt`、`overlay-add.tsv` 與 `sandhi-add.tsv`（S2r 加入），用 `Lexicon::parse_with(基底, Some(疊加層))` 載入，疊加層是 `overlay-add.tsv` 接著 `sandhi-add.tsv`（`engine::join_overlays`，前一份沒有結尾換行就補一個），和評測 CLI 預設相同。任一個不存在或解析失敗，回傳碼 3（`load_lm` 重讀 `overlay-add.tsv` 與 `demote.tsv`，建構之後它們被移走也是碼 3）。

詞包（acg-pack 契約 A.2）：`Engine::new_with_packs(data_dir, layout, Some((packs_dir, mask)))` 把啟用的詞包檔（`PACK_ACG` → `packs_dir/acg-add.tsv`、`PACK_IT` = 4 → `packs_dir/it-add.tsv`，依這個順序；it-pack 契約 §3.1）接在 `sandhi-add.tsv` 後面一起解析；`load_lm` 重新讀 `data_dir/overlay-add.tsv`（`engine::capping_overlay`；引擎不為此留一份約 17 MB 的副本），後面接建構時 `load_lexicon_packs` 給出的啟用詞包列（詞包檔不重讀；沒有啟用就沒有），交給 `CappedLexicon::with_packs`。〔2026-10-24，`pack-oov.md`〕`PACK_OOV`（現在只有動漫詞包）的詞**不封頂**、用詞包檔的分數，而且解碼與 `total_score` 把詞包詞當成模型不認識的詞（`CappedLexicon::model_id` 回 `None`：詞項、句尾、前一詞、段開頭、`End::Next` 都一樣；`history()` 不變）；其他詞包（資訊詞包）與非詞包的疊加層照舊依語料頻率封頂（沒看過的減 1.0）。沒有啟用的位元、或檔案不存在，就不讀任何詞包，引擎和 `Engine::new` 完全相同（`core/tests/engine_pack.rs` 逐位元比對解碼、候選清單與 V3 預測列）。未定義的位元是 `LoadFailed`。評測 CLI 的 `--packs acg|it|acg,it [--packs-dir DIR]`（可重複；要求的詞包檔不在就中止並報路徑，摘要行多 `+acg`、`+it`）走同一組函式（`load_lexicon_packs_detailed`、`capping_overlay_detailed`，讀取失敗帶出錯的檔案與 `ErrorKind`；解析失敗帶資料夾與解析器的 `detail`（指不出是哪個檔，見 acg-pack 契約 A2.7 的延後項）；`load_lexicon_packs` 與 `capping_overlay` 是把它們的錯誤對應成 `LoadFailed` 的包裝）。

## 6. C ABI（`core/include/shanjie.h`）

```c
typedef struct { uint32_t kind; uint32_t ch; uint32_t modifiers; } ShanjieKey;
// kind：1 CHAR、2 SPACE、3 ENTER、4 BACKSPACE、5 DELETE、6 ESC、7 LEFT、8 RIGHT、9 UP、10 DOWN、11 HOME、12 END、13 TAB、14 PAGE_UP、15 PAGE_DOWN（候選窗直排的規則 5p／5q；殼只在直排候選窗開著、沒有修飾鍵時才送）
// ch：kind 為 CHAR 時的 Unicode scalar（不含 Shift 的鍵帽字元）；其他 kind 時忽略
// modifiers：bit0 SHIFT、bit1 CONTROL、bit2 OPTION、bit3 COMMAND、bit4 CAPSLOCK
typedef struct {
  int32_t handled;            // 1 = 引擎處理了，殼不要再送給 App；0 = 直通（先插入 commit，再放行按鍵）
  const char *commit;         // UTF-8，要立刻插入的文字，可為空字串，不為 NULL
  const char *preedit;        // UTF-8，組字區顯示字串（未完成音節的注音插在游標處），不為 NULL
  uint32_t cursor_utf16;      // 游標在 preedit 中的位置，單位是 UTF-16 code unit（給 NSRange 用）；在未完成音節之後
  uint32_t candidate_count;   // 這次輸出的候選數：收合時是目前這一頁（0–9），展開時是可見的排（最多 5 × 9）
  const char *const *candidates; // candidate_count 為 0 時是 NULL
  int32_t candidate_selected; // 在這次輸出的 candidates 裡的選取位置；沒開候選時為 -1。V3：-1 而 candidate_count > 0 是未進入的預測列（不顯示選取，第一格的編號位置顯示 Tab 鍵圖示，其他格沒有數字；columns 0、first 0、total = count）；>= 0 是候選窗或已進入的預測列
  uint32_t candidate_columns; // s3b2 §8.2：0 = 收合的一列；展開時一律 9（一排一頁，s3b2 §9）
  uint32_t candidate_first;   // candidates[0] 在整份候選清單的位置；沒開候選時為 0
  uint32_t candidate_total;   // 整份候選清單的數量；沒開候選時為 0
  uint32_t candidate_vertical; // 0 = 橫排或沒有候選；1 = 直排候選窗（candidate_columns 為 0，最多 9 列，從 candidate_first 起，開窗時固定方向）；2 = 直排預測列（進入或未進入，照當下的設定，columns 0、first 0）。加在結構尾端
  uint32_t english_run_utf16_start; // 英文段開著時，段在 preedit 裡的起點（UTF-16 單位）；沒有英文段時是 0（zhen-inline §1.3f）。加在結構尾端
  uint32_t english_run_utf16_len;   // 英文段在 preedit 裡的長度（UTF-16 單位）；沒有英文段時是 0。加在結構尾端
} ShanjieOutput;
typedef struct ShanjieEngine ShanjieEngine;

int32_t shanjie_engine_new(const char *data_dir, uint32_t layout, ShanjieEngine **out); // layout 0 標準、1 倚天
void    shanjie_engine_free(ShanjieEngine *engine);
int32_t shanjie_engine_key(ShanjieEngine *engine, ShanjieKey key, ShanjieOutput **out);
int32_t shanjie_engine_reset(ShanjieEngine *engine, uint32_t mode, ShanjieOutput **out); // mode 0 送出顯示的整串（含未完成的注音，和 Enter 相同）後清空、1 丟棄
void    shanjie_output_free(ShanjieOutput *output);
// s3b2 §8.2 新增：滑鼠點選。index 是這次輸出 candidates 裡的位置；核心選 candidate_first + index，走和 Enter 同一個 choose()。
// V3：候選窗關閉但有預測列（進入或未進入）時，選預測列的第 index 個（點擊是明確的選取；學習開著時照改選學，v3-engine.md §10.1）。
// 1 = engine 或 out 為 NULL；2 = 候選窗和預測列都沒有，或 index 超出這次輸出（狀態不變）；4 = 內部錯誤（核心丟棄組字）；非 0 時 *out 是 NULL
int32_t shanjie_engine_pick(ShanjieEngine *engine, uint32_t index, ShanjieOutput **out);
// S2c 新增（docs/PLAN.md §S2c）
int32_t shanjie_engine_load_lm(ShanjieEngine *engine, const char *path);               // 不改目前的組字區顯示
int32_t shanjie_engine_set_profile(ShanjieEngine *engine, uint32_t profile, ShanjieOutput **out); // 0 chat（預設）、1 formal；重算組字區並回傳快照
// acg-pack 新增（docs/contracts/acg-pack.md A.2）：詞包。packs 是位元遮罩（bit0 = ACG，檔案 packs_dir/acg-add.tsv；
// 值 4 = 資訊＋AI 詞包 SHANJIE_PACK_IT，檔案 packs_dir/it-add.tsv，it-pack 契約 §3.1，接在動漫詞包的列後面，兩個位元可以一起；值 2 留給台詞詞包，目前未定義）；
// 詞包的列在建立時併入詞庫，所以開關在引擎生命期內固定，殼要換就釋放再建一個（和換排列相同）。packs 為 0 時不看 packs_dir（可為 NULL），
// 等同 shanjie_engine_new；檔案不存在就什麼都不加（引擎和沒有詞包時逐位元相同）。遮罩有未定義的位元或 layout 超出範圍或 data_dir、packs_dir 不是 UTF-8 回 2；
// packs 非 0 而 packs_dir 為 NULL 回 1；檔案存在卻讀不了或解析失敗回 3。詞包的詞在 load_lm 時不封頂、用詞包檔的分數，解碼時當成語言模型不認識的詞（pack-oov）。
int32_t shanjie_engine_new_packs(const char *data_dir, uint32_t layout, const char *packs_dir, uint32_t packs, ShanjieEngine **out);
// s3e 新增（docs/contracts/s3e-punctuation-candidates.md）
int32_t shanjie_engine_set_punctuation(ShanjieEngine *engine, const char *table); // 標點候選表；不合法回 2 並保留原表；不改目前顯示
// candidate-vertical 新增（docs/contracts/candidate-vertical.md §2.2）：下一次開啟的候選窗的方向。enabled 0（預設，橫排）或 1（直排），其他值回 2、狀態不變；
// 1 = engine 或 out 為 NULL；回傳快照（handled 1、commit ""），和 set_prediction 相同。開著的候選窗照開啟時的方向（快照和呼叫前的輸出相同），正在顯示的預測列則換成新的方向（值 2）。殼在每次建 engine 後都要呼叫。
int32_t shanjie_engine_set_candidate_vertical(ShanjieEngine *engine, uint32_t enabled, ShanjieOutput **out);
// zhen-mixed 新增（docs/contracts/zhen-mixed.md §3.1）：英文單字表。0 成功；1 engine 或 path 為 NULL；2 路徑不是 UTF-8；3 讀不了、不是一般檔案、超過 4 MiB 或 200,000 行、
// 有一行不符 [a-z]{3,32}、有重複行、有 BOM。失敗時保留之前的單字表（或沒有）。不改開關、不改目前顯示。建立 engine 後呼叫一次。
int32_t shanjie_engine_load_english(ShanjieEngine *engine, const char *path);
// set_english：0 或 1（其他回 2、狀態不變），建立時預設 0，碼與快照同 set_abbreviation（handled 1；`commit` 空，除了下面「關掉」那一種）；engine 或 out 為 NULL 回 1。
// 1 且有單字表、標準排列、「可省略韻母」關時，英文打進一個未完成音節後的空白鍵開英文窗（第 10e 條）；選了英文項不送出，英文留在組字區（zhen-inline）；自動固定（D 成立時的字母，英文段不自動固定）、Shift＋英文字母的英文段、英文 token 改選（只用 ↓ 開寫法清單）與「英文 token 或空白單位後面的空白鍵插入一個空白單位」見 zhen-inline §1.3、§1.3b、§1.3c、§1.3e；英文段開著時輸出的 `english_run_utf16_start`／`len`（§1.3f）；英文 token 或空白單位成為第 128 個單位時，送出的整串放在做這件事的那一次輸出（那個鍵、`pick` 或設定函式的快照）的 `commit`。關掉時開著的英文窗跟著關，開著的英文段結束成 token（成為第 128 個單位時快照的 `commit` 非空）；`set_abbreviation(1)` 同樣如此。沒有單字表時無作用。殼在每次建 engine 後都要呼叫。
// 數字列（digits.md）：開著（標準排列、可省略韻母關、有模型與 digits.tsv）時 0-9 . / - 的鍵打出半形數字串 token，數字或注音由前文評分決定（§3.0）；關掉（或開可省略韻母）把緩衝的數字吐成 token，快照的 `commit` 可能是第 128 個單位送出的組字。
int32_t shanjie_engine_set_english(ShanjieEngine *engine, uint32_t enabled, ShanjieOutput **out);
// zhen-inline 新增（docs/contracts/zhen-inline.md §2）：英文的顯示方式。enabled 1（預設）＝打字時就顯示（顯示規則 D，§2）、0＝按空白才顯示；其他值回 2、狀態不變；
// engine 或 out 為 NULL 回 1；成功回 0 並回傳快照（handled 1、commit ""），正在顯示的組字區照新的顯示重畫。只在 set_english(1) 時有作用。殼在每次建 engine 後都要呼叫。
int32_t shanjie_engine_set_english_inline(ShanjieEngine *engine, uint32_t enabled, ShanjieOutput **out);
// zhen-keyorder（docs/contracts/zhen-keyorder.md §3）：依按鍵順序判斷英文。enabled 0（預設）或 1，其他值回 2、狀態不變；engine 或 out 為 NULL 回 1；成功回 0 並回傳快照（handled 1、commit ""）。關掉（1→0）時開著的英文窗跟著關（不管窗是哪種證據開的）、影子照關窗的做法清掉，未完成音節保留；沒有英文窗時什麼都不動。只在 set_english(1) 時有作用；殼在每次建 engine 後都要呼叫。
int32_t shanjie_engine_set_english_key_order(ShanjieEngine *engine, uint32_t enabled, ShanjieOutput **out);
// S7c-2a 新增（docs/contracts/s7c2a-core-rerank.md §2.5）：小模型重排的兩段式 API。每次組字改變可能留下一個重排工作；殼取走、在別的執行緒 run、回到引擎的執行緒 apply。
#define SHANJIE_NO_CHANGE 5
typedef struct ShanjieRerankJob ShanjieRerankJob;
// load_tinylm：讀檔失敗、不是一般檔案、超過 64 MiB、格式錯、沒有載入 n-gram、.sjtl 記的 n-gram 雜湊不同、KN 側檔的狀態和 .sjtl 記的不同（v4 記調參時的 kn.sjkn 的 SHA-256、全 0＝沒有；v1～v3 當成沒有；載入的 kn.sjkn 或沒有，32 bytes 都要相同；model-v6 §2.7） → 3（只是配對檢查，不是 .sjtl 的完整性檢查），
// 引擎維持原狀；NULL → 1、路徑不是 UTF-8 → 2。不改 set_rerank 的開關、不改目前顯示。load_lm 成功會丟掉小模型（要在 load_lm 之後呼叫）。
int32_t shanjie_engine_load_tinylm(ShanjieEngine *engine, const char *path);
// load_colloquial（colloquial-term.md §2.2）：口語表（常談 colloquial.sjlm）。引擎從建立時的詞庫資料夾讀 k 的詞集合（mcbpmf-data.txt、overlay-add.tsv），加上建立時讀的詞包列；可以在 load_lm、load_tinylm 之前或之後呼叫（表和它們無關）。
// 口語項在每個工作建立時決定要不要用：聊天設定、載入的 .sjtl 的 SHA-256 等於量測用的那一份，三個條件（含表已載入；任何強度都用，保守、標準、積極各用自己的 α、τ、δ，colloquial-levels.md）都成立才用；其他情況的工作和沒有表的引擎逐位元組相同。口語項生效時融合另加讀音通道項（reading-channel.md）：每個單字詞的讀音對照詞庫裡該字最常用的讀音，罕用讀音扣分（每字最多 4），不生效的地方沒有這一項；FFI 與設定都不變。
// 讀檔失敗或格式錯 → 3（引擎維持原本的表，沒有就沒有）；NULL → 1；路徑不是 UTF-8 → 2。會讓進行中的重排工作作廢。路徑不寫進任何紀錄。
int32_t shanjie_engine_load_colloquial(ShanjieEngine *engine, const char *path);
// set_rerank：0 或 1（其他回 2、狀態不變），建立時預設 0（S7c-2c 起；之前是 1）；碼與快照同 set_demote（重算組字，關掉時畫面回到 n-gram 的結果）。殼每次建 engine 後都要呼叫。
int32_t shanjie_engine_set_rerank(ShanjieEngine *engine, uint32_t enabled, ShanjieOutput **out);
// 語意校正強度（docs/contracts/rerank-strength.md §2.2）：set_rerank_level 0 保守、1 標準（建立時預設）、2 積極，決定工作用小模型的哪一組 α、τ、δ；
// 碼與快照同 set_profile（engine 或 out 為 NULL → 1；大於 2 → 2、狀態與世代號都不變；4 → 引擎 discard、新等級保留）。會讓之前取走的工作作廢；工作建立時記下當時的 α、τ、δ。
// 沒有小模型時記下等級、回 0；v1／v2 的 .sjtl（只有標準一組）任何等級都記下、回 0、實際用標準。load_tinylm、load_lm 保留等級。殼每次建 engine、load_tinylm 之後送一次存起來的等級。
int32_t shanjie_engine_set_rerank_level(ShanjieEngine *engine, uint32_t level, ShanjieOutput **out);
// rerank_levels：*levels 是載入的小模型提供的等級遮罩（bit0 保守、bit1 標準、bit2 積極）：沒有小模型 0、v1／v2 檔 2、v3／v4 檔一律 7（不管值是否和標準相同、也不管 profile）。
// 0 成功；engine 或 levels 為 NULL → 1（不寫 *levels）；4 內部錯誤（不改任何狀態，所以也不 reset）。
int32_t shanjie_engine_rerank_levels(ShanjieEngine *engine, uint32_t *levels);
// rerank_take：交出上一次改變的工作（只交一次）；沒有工作時回 0、*job 為 NULL。engine 或 job 為 NULL → 1（job 非 NULL 時先設 *job = NULL）；4 → 引擎 discard。
int32_t shanjie_engine_rerank_take(ShanjieEngine *engine, ShanjieRerankJob **job);
// rerank_run：任何執行緒都可呼叫，不需要引擎；結果存在工作裡。0 成功、1 job 為 NULL、4 攔下的 panic（沒有碰任何引擎，組字不變；不留結果，連之前那次 run 的結果也清掉）。
int32_t shanjie_rerank_run(ShanjieRerankJob *job);
// rerank_apply：在引擎的執行緒、run 回傳之後。有改變 → 0 與快照（預測列只在套用當下有顯示時才重算）；沒有結果、過期、組字裡已不是工作記下的詞、候選窗開著、預測列已進入、每段都選第一名、已套用過 → SHANJIE_NO_CHANGE、*out 為 NULL；
// 不釋放工作。engine、job、out 任一為 NULL → 1；4 → 引擎 discard、*out 為 NULL。
int32_t shanjie_engine_rerank_apply(ShanjieEngine *engine, ShanjieRerankJob *job, ShanjieOutput **out);
// rerank_free(NULL) 不做事。
void    shanjie_rerank_free(ShanjieRerankJob *job);
```

- **回傳碼**：0 成功、1 必要的指標是 NULL、2 輸入不合法（data_dir、LM 路徑或標點表不是 UTF-8、`ch` 不是合法的 Unicode scalar、layout、mode、profile 或語意校正強度超出範圍、標點表格式不合法或超過上限，見 s3e §3）、3 資料載入失敗（含 LM 檔讀取或格式錯誤、引擎沒有 data_dir）、4 內部錯誤（攔下的 panic 或解碼錯誤）、5 沒有改變（只有 `rerank_apply`，S7c-2a）。`load_lm` 失敗時 LM 維持原狀；碼 4 時任何函式都照下面的規則丟棄組字（`rerank_run` 例外，見下）。
- **reset**：mode 0 時 `commit` 是目前組字區的顯示字串（未完成音節丟掉）；mode 1 時 `commit` 為空。兩者都清掉所有組字狀態，之後的輸出必須和新建的 engine 相同；S2c 起是「新建、載入相同 LM、使用相同設定的 engine」，reset 不清 LM 與設定。殼在 `commitComposition:`、`deactivateServer`、組字擁有者改變時呼叫（mode 與時機見 `docs/contracts/s3b.md` §5）。Caps Lock 由系統切換輸入方式，會觸發 `deactivateServer`；「privacyGate 轉為生效時」延到 S4。
- **記憶體與生命週期**：
  - 回傳非 0 時，`*out` 一律設成 NULL，而且不配置任何記憶體。
  - `shanjie_engine_free(NULL)`、`shanjie_output_free(NULL)` 什麼都不做。重複釋放、或釋放不是核心配置的指標，屬於未定義行為。
  - `ShanjieOutput` 擁有自己的字串副本，有效期到 `shanjie_output_free` 為止，不受之後的 `engine_key`、`engine_reset`、`engine_set_profile`、`engine_load_lm`、`engine_free` 影響。
  - handle 不是 thread-safe：所有呼叫都在同一個執行緒（IMK 主執行緒）。例外是 `shanjie_rerank_run` 與 `shanjie_rerank_free`（不需要引擎，S7c-2a；free 要在 run 回傳之後，同一個工作不可同時 run 與 free）。
  - 回傳碼 4 之後，engine 自動 reset（mode 1），丟掉可能只改了一半的組字。**例外**：`shanjie_rerank_run` 的 4 不重設任何東西（它沒有引擎）。
  - 殼收到 `engine_key`、`engine_pick` 的非 0 回傳時，當作「不處理」直通，而且不記錄這個按鍵。`rerank_take`、`rerank_run`、`rerank_apply` 的非 0 碼**不代表按鍵直通**（它們沒有對應的按鍵）。
  - 回傳碼 5（`SHANJIE_NO_CHANGE`）只有 `rerank_apply` 會回：沒有改變、過期、組字裡已不是工作記下的詞、候選窗開著、預測列已進入、還沒 run（或 run 回 4）、已套用過。
  - **重排工作（S7c-2a）**：`rerank_take` 交出的每個工作都由殼恰好 `rerank_free` 一次；`rerank_apply` 不釋放工作，同一個工作再 apply 一律回 5。同一個工作不可同時在兩個執行緒使用（run 回傳之後才能 apply 或 free）；run 不可取消，之後的改變只會讓結果作廢。**過期**（s7c2a §2.3 修正一）：只有會重新解碼的改變讓結果作廢——完成音節、刪除、選字或預測列選字、標點（標點不是送出：它進組字區、重新解碼、產生新工作）、⌘⌫ 忘記、改變解碼的設定（profile、降權、重排開關、語意校正強度、換模型、選字記憶的開檔／清除；`set_learning` 不論設成什麼值都不讓結果作廢：它只決定選字要不要記下來，不改變解碼，S7c-2b）；不重新解碼的鍵（游標移動、還沒完成音節的注音符號、進入預測列、直通鍵）不讓結果作廢。送出（Enter、128 個字元自動送出、reset）送出畫面上的內容，還沒套用的工作作廢。工作只放擁有的資料（字串、分數、範圍、世代號、設定、模型），不借用引擎，所以引擎釋放之後工作仍可 run 或 free。未定義行為：同一個工作 free 兩次、free 之後再 run 或 apply、free 一個不是 `rerank_take` 交出的指標、run 進行中同時 apply 或 free。工作與結果內含候選文字：不寫進任何紀錄（含指標），`load_tinylm` 的路徑也不寫。
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
- 組字區上限：第 128 個音節自動送出（原本 40，composition-cap）。

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
- **S7c-2a**：子行程測試 `child_rerank`／`rerank_codes_through_the_c_abi` 用真的詞庫與 n-gram 模型、`eval/golden/tinylm-tiny/` 的玩具模型（執行時改寫 n-gram 雜湊與 alpha／tau），涵蓋 load_tinylm 的碼 1、2、3（沒有 n-gram、不存在、壞檔、別的模型的雜湊、資料夾）、set_rerank 的碼、take／run／apply 的所有 NULL 組合、沒有工作時 NULL、還沒 run 就 apply 回 5、run → apply → 同一個工作再 apply 回 5、游標移動後照樣套用、Backspace 刪音節後過期回 5、apply 注入 panic 回 4 之後組字為空、load_tinylm 不改開關、引擎釋放後工作仍可 run 與 free，並檢查子行程輸出不含暫存路徑。`child_rerank_panic`／`r2_rerank_run_panic_is_silent`：第一次 run 成功之後，第二次 run 在注入點 panic（payload 含候選文字）回 4，子行程的 stdout／stderr 不含該文字（預設 hook 的正向對照看得到），之後組字不變、apply 回 5（第一次的結果也不留）。
- **語意校正強度**（`rerank-strength.md` §2.2、§9）：子行程測試 `child_rerank_level`／`rerank_level_through_the_c_abi` 用合成的 v3 小檔（`eval/golden/tinylm-tiny/tiny-int8-v3.sjtl`，執行時改寫 n-gram 雜湊與六組 α、τ、δ：只有一個等級會改字），涵蓋 set_rerank_level 的碼 1、2（`*out` 為 NULL）、4（注入 panic：組字清空、新等級保留）；rerank_levels 的碼 1 與遮罩 0（沒有小模型、load_lm 之後）、2（v1、v2 檔）、7（v3 檔）；沒有模型時記下的等級在載入後生效；碼 2 之後先前 run 好的工作照樣套用（世代號與等級都沒變）；改等級（即使同一個值）讓 run 好的工作過期回 5；兩種 profile 下三個等級各自只用自己那一組；load_lm 之後重新載入仍保留等級；v1、v2 檔每個等級都用標準那組，並檢查子行程輸出不含暫存路徑。格式測試在 `core/tests/tinylm_tiny.rs`（`v3_*`，含魔數與版本的交叉組合）。

`core/tests/engine_pack.rs`（acg-pack 契約 A.5）：走出貨的建構子 `new_with_packs`，用會變的輸入（碇源堂、螢火蟲之墓的讀音，聊天與書面）。遮罩 0 且檔案存在、遮罩 1 且檔案不存在，兩者的每一個 `Output`（解碼、候選窗、預測列）都和 `Engine::new` 逐位元相同；遮罩 1 且檔案存在時，兩個名字是第一個候選。把遮罩改成什麼都不做會讓 `on != plain` 的斷言失敗（2026-10-09 實測，exit 101）。資訊詞包的位元另有 `the_it_pack_has_its_own_bit_and_file`：用「圖像生成」的讀音，位元 4 清掉但檔案在、位元 4 設了但檔案不在，都和沒有詞包的視窗第一候選相同；位元 4 設了且檔案在，聊天與書面的第一候選都是它，和動漫詞包同開時碇源堂照樣是第一候選；值 2 仍是 `LoadFailed`（`an_unknown_pack_bit_is_refused`）。

### 7.4 C 標頭冒煙測試（`core/tests/c/abi_smoke.c`）

`cargo test` 只從 Rust 呼叫匯出函式，看不到 `shanjie.h` 與 Rust 結構是否一致；S3b 連結的是這個標頭，所以要用 C 編譯器實際編譯、連結一次。不加 crate，用系統的 `cc`。

- 驅動程式 `abi_smoke.c` 只 include `shanjie.h`，參數是資料目錄（S2c 起加第二個參數：LM 路徑，檢查 301–317 驗證 load_lm、set_profile 與 reset 保留 LM，見 PLAN §S2c 驗收 6）。對兩種排列各做一次：
  - `shanjie_engine_new(data_dir, layout, &e)` 回 0。
  - 依排列送出「你好」的按鍵（標準 `s u 3 c l 3`、倚天 `n e 3 h z 3`），每鍵都 `shanjie_output_free`；送 Enter 後檢查 `handled == 1`、`commit` 是「你好」、`preedit` 是空字串、`cursor_utf16 == 0`、`candidate_count == 0`、`candidates == NULL`、`candidate_selected == -1`。
  - 再送 `ㄋㄧˇ` 的按鍵和空白鍵，檢查候選已開：`candidate_count` 在 1–9、`candidates[0]` 不是 NULL、`candidate_selected == 0`；接著 `shanjie_engine_reset(e, 1, &o)`，檢查 `commit` 為空字串、`preedit` 為空字串。
  - `shanjie_engine_free(e)`；最後呼叫 `shanjie_engine_free(NULL)`、`shanjie_output_free(NULL)`。
  - S7c-2a（檢查 901–918）：沒有 n-gram 時 `load_tinylm` 回 3、NULL 回 1、不存在的檔案回 3；`set_rerank` 的碼；沒有小模型時打字後 `rerank_take` 回 0 且 `*job` 為 NULL；take、run、apply 的 NULL 組合；`rerank_free(NULL)`。冒煙測試沒有 `.sjtl`，成功的 run／apply 由 §7.3 的子行程測試負責。
  - 語意校正強度（檢查 951–960）：沒有小模型時 `rerank_levels` 回 0、遮罩 0，NULL 回 1；`set_rerank_level` 的碼 1、2（`*out` 為 NULL）與沒有模型時回 0 和快照；`load_lm` 之後遮罩仍是 0。
  - 口語表（檢查 961–963）：`load_colloquial` 的 NULL 引擎、NULL 路徑回 1，不存在的檔案回 3；成功載入與口語項本身由 `core/tests/engine_colloquial.rs` 負責。
  - 全部通過時 exit 0，任何一項不符時印出項目編號（不印任何字串內容）並 exit 1。
- 指令（在 repo 根目錄，`$T` 是暫存目錄）：

  ```sh
  cargo build --release -p core
  # zsh 不拆 $LIBS 這種變數，所以命令替換直接放進 cc 的參數（sh、bash、zsh 都會拆）。
  # 2026-10-03 實際印出 `-lSystem -lc -lm`；ld 會警告 `ignoring duplicate libraries: '-lSystem'`，無害。
  # --color never：CI 的 rust-toolchain action 設了 CARGO_TERM_COLOR=always，不關掉的話最後一個旗標會接上色碼（`-lm\e[0m`）而連結失敗。
  # CI（.github/workflows/ci.yml）另加 --locked，並先檢查擷取結果不是空的（macOS 的 cc 不帶這些旗標也能連結）。
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

- **API 形狀**：`Engine::new(data_dir, Layout)` 讀檔，`Engine::with_lexicon(Arc<Lexicon>, Layout)` 共用已載入的詞庫（測試每個測試檔只載入一次）；`load_lexicon(data_dir)` 單獨公開。`Key { kind: KeyKind, ch: char, modifiers: u32 }`（`KeyKind::from_code` 把 ABI 的 1–15 轉成列舉，`ch` 的合法性由 `ffi.rs` 先擋）。`Output` 欄位與 §6 一一對應（`selected: Option<usize>`、`candidates: Vec<String>`）。錯誤只有 `EngineError::LoadFailed`（碼 3）與 `Internal`（碼 4）；`key` 回 `Internal` 之前引擎已自行清空（等同 reset 模式 1）。`reset(mode)` 不會失敗。
- **R2**：`Key`、`Output`、`Engine` 都不 `derive(Debug)`；`Output` 只 derive `PartialEq`，測試用 `assert!(a == b)`。
- **Ctrl+\ 的判定**：只有「修飾鍵恰為 CONTROL、字元為 `\`」算 Ctrl+\；Ctrl+Shift+\ 屬第 1 條（直通）。§4 的 Shift 表只認「修飾鍵恰為 SHIFT」；單按表只認「沒有修飾鍵、而且該鍵在目前排列不是注音鍵或聲調鍵」（2026-10-05 起）。
- **第 1 條的輸出**：直通時回傳 `handled = 0`、`commit` 為空，其餘欄位是目前狀態的快照（狀態不變）。第 22 條直通同理。
- **取代後的 Backspace（第 11 條）**：同一欄被取代時，新符號直接換掉舊符號，不會還原舊符號；Backspace 照顯示位置從後面刪，和打字順序無關。2026-10-06 更正：原本寫「新符號算最後放進、先刪它」，是對照蘋果之前的暫定行為。實測蘋果注音與小麥注音都是打 ㄉㄨㄟ、改打 ㄅ 後，按 Backspace 依序得到 ㄅㄨ、ㄅ。
- **候選邊界**：↑↓ 在第一個／最後一個停住不繞回；←→ 和 ↑↓ 相同；只有空白鍵會從最後一頁繞回第一頁。候選開啟時 Esc／Backspace 是「已處理」（第 7 條），不會落到第 12 條以後。候選為空（不會發生，因每個音節都有詞條）時不開啟。
- **第 8 條**：「其他鍵」＝第 3–7 條沒列到的所有鍵（含 Delete、Home、End、Tab、Enter 以外的字元；注意 Enter 屬第 6 條）。候選已關閉後，該鍵接著照第 9 條起處理，所以 Home 會真的把游標移到 0。
- **游標在 preedit 的位置**：假設每個音節對應顯示字串的一個字元（詞庫載入時已過濾字數不等的詞；疊加層未過濾，若有不符則游標位置夾在字串長度內）。`cursor_utf16` ＝游標前的字元＋未完成音節的 UTF-16 長度。
- **固定詞**：選字後游標不動；固定詞範圍以音節為單位；插入發生在固定詞左邊界（`start == 游標`）時整段右移，發生在右邊界（`end == 游標`）時不動；刪除的音節落在 `[start, end)` 內就移除。改選後新增的 `frozen` 固定詞照同樣的平移規則；插入落在多音節 `frozen` 詞內部時先逐音節拆開；刪除時，含該音節的多音節詞（任何來源）的其餘音節先變成單字 `frozen`；插入音節時 frozen 鄰居的放開見 §3.1（放開後不再凍結）。`recompute_pred` 用 `path` 每個詞的音節數（不是字數）算詞的起點，只有使用者自己選的固定詞內部不當起點（`frozen` 詞內部可以）；`boosted` 與 `total_score` 也用音節數定位。
- **自動送出**：第 128 個音節完成後（`syls.len() >= MAX_SYLLABLES`，128）立刻送出整段並清空，`handled = 1`。
- **解碼成本**：每次組字區變動都重算所有空白段（沒做快取）；重播測試 8,336 鍵的 p95 約 1.2 ms、最大約 9 ms（release，標準與倚天相近）。
- **C ABI 的輸入檢查（`ffi.rs`）**：`kind` 不在 1–15 時回傳 2（同「輸入不合法」）；`modifiers` 的未定義位元（bit5 以上）不擋，照原樣交給引擎。輸出字串含 NUL 而無法建 `CString` 時回傳 4，engine 先丟棄組字再返回。
- **panic hook 是行程全域的**：靜音 hook 會取代宿主行程原有的 hook。S3b 的輸入法行程只有這個函式庫，所以沒有影響；呼叫匯出函式的測試都在子行程裡跑，以免影響同一個測試執行檔裡其他測試的 panic 訊息。

## 9. S2c 實作決定

契約（`docs/PLAN.md` §S2c）沒寫、`core/src/lm.rs` 與引擎接 LM 時自行決定的事：

- **模型讀取（`Lm::load`／`Lm::parse`）**：除了魔數、各段長度、無多餘位元組，還檢查查表依賴的不變式（詞彙依 UTF-8 位元組遞增、前文與下一詞 id 遞增且小於 V、偏移量遞增且落在範圍內、前文總次數非 0），所以任何查表都不會越界。錯誤只有 `LmError::Io`、`LmError::Format`，不帶任何內容。`back(v)` 在載入時依檔案順序累加後除一次，存成陣列。
- **查表**：詞彙用位元組二分搜尋（id 0、1 是 `<s>`、`</s>`），前文與下一詞用二分搜尋，沒有雜湊表。
- **`pow(10, lp)` 的坑**：Rust 的 `10f64.powf(lp)` 在底數是常數時，LLVM 會改寫成 `exp10`，末位和 Python 的 `10 ** lp`（libm `pow`）差 1 ulp（formal 的 dump 有 2 列的分數差約 3.6e-15）。`lm.rs` 的 `pow10` 用 `black_box` 藏起底數，改後 chat、formal 的 `--dump` 與 Python 逐位元組相同。
- **上限後詞庫（`CappedLexicon::new(Arc<Lexicon>, overlay 文字, &Lm)`）**：與原詞庫共用字串池與讀音表（只多一份 `ents` 陣列，每筆 16 bytes），每個讀音的詞條範圍不變、只在範圍內重排。疊加層詞集是 `overlay-add.tsv` 每行第二欄，與 `lm_eval.py` 相同。CLI 與 `Engine::load_lm` 都呼叫它。
- **解碼**：`decode_segment(lex, 音節, lm, λ, 前文詞, End, beam)` 是唯一的實作；`decode` 是前文 `<s>`、`End::Eos` 的特例。組字區第一段（左邊沒有固定詞的那段）的前文詞是 `lm::history(前文, lm)`：`set_left_context` 存的前文（最多 2 個漢字）整段或最後 1 個字，取第一個在模型裡有 bigram 歷史紀錄的，都沒有就是 `<s>`（S2h §1）。殼傳的是同一則訊息最多 64 個 grapheme 的原文（`context-return.md`），核心在解碼、學習、預測列的 `key` 與數字層只用它結尾 ≤ 2 個漢字；另外只留 `left_full`：載入的會意模型宣告了前文長度 L（SJTL0005）時是原文的最後 L 個字元，否則是結尾的英文詞（和它後面的一個空白），都不是時是空的；`left_full` 不經 C ABI 回傳、不寫檔、不記 log。宣告 L 的模型，每個重排段的前文是 `left_full`＋組字區在這一段之前的顯示的最後 L 個字元（不經過 `context_key`）；沒有宣告的照舊。其他段照舊：左邊是固定詞就用那個詞，是標點就用 `<s>`。每個候選的 `pb` 與詞 id 在迴圈外算一次，只是提出共同項，沒有改運算順序。路徑同時帶著每個詞實際用的 lp，供 `total_score` 使用。
  **trigram 層**（`docs/contracts/trigram-core.md`，第 A 片）：解碼傳的是 `Scoring`（λ、KN 開關、trigram 開關，`Lm::scoring(profile)`；裸 λ 等於舊行為）。書面且模型旁有 `trigram.sjt3` 時是 trigram 開、KN 關，其餘（聊天、沒有表）不變；預測列（V3）不經過它，兩種設定都照 KN 側檔、不用 trigram。狀態是最後兩個詞，重組鍵是（表面字串, 前一詞, 最後一詞）（沒表時仍是舊的兩元鍵）。每段的起始歷史 (u, v)：前文詞是 `<s>`（句首、標點之後、英文段之後、前文給 `<s>`）→ (`<s>`, `<s>`)；其他詞（固定的字、輸入框前文）→ (未知, 該詞)，第一個詞只用 bigram。收尾項（`Eos`、`End::Next`）用 (倒數第二詞, 最後一詞)；段內只有一個詞時，倒數第二詞是起始的 v。起始的 u 未知（固定的字、輸入框前文）而且段內只有一個詞時，收尾項的 u 也當作未知，用 bigram（第 A2 片 (b′)）；起始是 (`<s>`, `<s>`) 的段不變。經過 trigram 的每一項取 `max(P3, 0.5·P2)`（第 A2 片 (e)，`TRI_FLOOR`）。被學習加分的詞條（`was_boosted`）不經過 trigram，也不經過 KN（`prob_learned`）。

  **載入方式**（`docs/contracts/trigram-mmap.md`）：`Lm::load_opts` 以唯讀 `mmap` 載入 `trigram.sjt3`（`core/src/trigram.rs` 的 `Mapped`），不整檔讀進記憶體；檔案在映射期間被原地截短或改寫會讓讀到的頁面收到 SIGBUS，所以安裝流程（`scripts/install-ime.sh`、安裝程式、Homebrew cask）都只能整個 bundle 以 rename 換掉，不可原地改寫這個檔。
- **引擎**：`Engine::new` 記住 `data_dir`；`load_lm(path)` 失敗時（含沒有 `data_dir`）維持原狀並回 `LoadFailed`，成功時不重算顯示。`set_lm(Arc<Lm>, Arc<CappedLexicon>)` 是 `with_lexicon` 引擎專用的注入（測試用）。`set_profile(Profile)` 先記住再重算，解碼失敗時引擎自行清空並回 `Internal`；沒載入 LM 時顯示不變。`Profile::from_code` 供 step 2 的 ABI 使用（0 chat、1 formal）。
- **`lp_F`**：`CappedLexicon::best_lp(讀音, 詞)`，同讀音下同字串取最大值；找不到（不會發生，候選就來自這個讀音）視為內部錯誤。固定詞之間沒有空白區間時，不需要特別處理：兩個固定詞各自以「前一詞」計分，這件事只出現在 `total_score`（沿路徑逐詞累加）。
- **`total_score()`**：從第一段的前文詞（同上，S2h §1）開始，沿目前最佳路徑（`refresh_lm` 同時存下的 `(詞, lp)`）依序加 `word(λ, 前一詞, 詞, lp)`，最後加 `eos`；前文本身不計分；沒有 LM 或組字區空時回 `None`。例外（model-v6 §2.8 規則 1）：路徑上被學習加分的詞（`lp > best_lp(讀音, 詞)`，和 `Engine::boosted` 同一個判斷）用 `word_learned`（不經過 KN 替換），和解碼時的算法相同，所以總分仍等於解碼第一名的分數。書面有 trigram 表時逐詞帶兩個歷史詞，和解碼的段交界一致：標點之後是 (`<s>`, `<s>`)，固定的字之後的第一個詞是 (未知, 固定的字)，其他詞是 (前前詞, 前詞)；`eos` 與到固定詞的轉移項同樣用 trigram（`Lm::word3`／`eos3`），只有一個詞、起始的 u 未知的段落例外：這兩項的 u 當作未知，用 bigram（第 A2 片 (b′)，`total_score` 從段落開頭數詞數）。被學習加分的詞（上面的例外）不經過表也不經過 KN，但仍是下一個詞的歷史詞。開著的英文段（`run_cut`，游標處）在有表時照 `refresh_lm` 切開：左半以 `eos` 收尾，右半從 (`<s>`, `<s>`) 重起，和標點一樣，所以英文段落在一段組字的中間時總分仍等於解碼第一名的分數（`core/tests/tri_tiny.rs`，突變「不切英文段」以斷言失敗）。**已知例外**（PR #175 verifier 量到）：英文段緊貼在固定的字旁邊時（固定的字因此成了段的邊緣），`total_score` 會多算固定的字的句尾項（英文段在它後面）或它的詞項（英文段在它前面），解碼器不算這兩項；和沒有英文段、固定的字在組字區最後時 main 上本來就有的差異是同一類（有表、沒表都一樣）。`total_score` 目前只有測試在用，App 沒有呼叫，選字不受影響；要讓它在這些情況也相等，改成由 `refresh_lm` 存下每段的解碼分數再相加，另開一片。沒有表時英文段仍不是這個總分的邊界（bigram 的總分從來沒有在那裡切，不改，聊天設定逐位元組不變）。
- **reset**：`clear_all` 只清組字狀態（含路徑），不碰 `lm`、`profile`、`data_dir`。
- **評測 CLI 的資料來源**：`--dev N` 與 `--rows` 照 `lm_eval.py` 的 `rows_of`，只收恰好三欄的列，不套 `usable`（dev 的 390 列都是三欄，與 S3a 的 302 列相同）；`--set holdout` 走既有的 `load_set`（`usable` 加 `row_syllables`）。`--name` 缺省時 `--rows` 用檔名。`top1_sha256` 用 `core::eval::sha256_hex`（專案沒有雜湊 crate，自寫 FIPS 180-4，附已知向量測試）。
- **逐分數對照（驗收 3）的指令**：`python3 reference/proto/lm_eval.py --lm data/lm/bigram.sjlm --profile chat --dev 302 --dump $T/chat.dump`，對 `shanjie-eval --lm … --profile chat --dev 302 --dump $T/r-chat.dump`，formal 同理；`cmp` 兩邊檔案（結果是位元組相同）。這個比對需要 Python，所以沒放進 `cargo test`；`cargo test` 涵蓋的是 4 組參數的摘要行（含 `top1_sha256`）與 `s2-lm-dev302-top1.tsv`。
- **beam 近似**：驗收 5(a) 的廣度測試（前 12 列、兩種設定、每個詞各固定一次）有 2 個詞因 beam＝64 的近似得到不同整句，測試容許至多 2 個；驗收用的案例（疊加層詞五個、5(b) 兩個）都精確相等。
- **S2c：set_profile 回傳碼 4 時**（引擎呼叫成功之後才 panic 或轉 C 字串失敗），新的設定保留、只丟棄組字；§6 只要求丟棄組字，這是刻意的：設定是殼的意圖，組字才是可能只改一半的狀態。load_lm 的碼 4 只能由 panic 觸發，沒有測試。
