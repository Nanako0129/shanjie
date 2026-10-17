# 契約：預測列與 ↓ 候選一致、未進入的預測列前面放 Tab 提示（predict-down）

狀態：READY（2026-10-17 收尾審查，唯一一項「⌘⌫ 後清單變空要關窗」照它的最小修改改完）。修訂二（2026-10-17；plan-verifier 第一次 REVISE：有未完成音節時的 ↓、Tab 進出時橫排的第 0 格、v3-engine 要改的段落；第二次 REVISE：既有測試怎麼改、哪些格子的數字欄加寬）。

讀過的程式與行號是 main `af78dedc`（到 `d52949b3` 為止 `core/`、`macos/`、`cli/`、`tools/`、`experiments/` 都沒有改動）。標〔推論〕的沒有量過。三片的順序是 B → C → A（A 是 `docs/contracts/digits.md`，等 C 的判讀定稿後再送審）。


## B.1 依據

- 維護者 2026-10-17：「平常及時預測上次有說，↓ 候選要一致還沒做，另外及時預測要做一個 tab 的圖示在前面提示會更好」；答覆 2：預測列的詞放前面、同順序，再接去重的詞庫候選。
- **今天兩份清單為什麼不同**（讀程式）：預測列是 `recompute_pred`（engine.rs 1644）對每個起點呼叫 `predict`（predict.rs 227），列**讀音以目前音節開頭**的詞（含比游標長的詞），依語言模型分數排、前文的後繼詞先一層（predict.rs 237）、學過的詞再提到最前（1718）、拿掉和目前顯示相同的詞（1696），最多 9 個。↓／空白鍵開的候選窗是 `open_candidates`（2469）列**讀音剛好等於游標前 L 個音節**的詞，L 從 `max_len` 遞減、同一個 L 內照**原始詞庫** `self.lex.entries` 的順序（2512–2519；s3a §3.1 第 130 行），不看模型、前文、學習。開窗時預測列被清掉、關窗不恢復（v3-engine §1.1；engine.rs 1647；測試 `the_row_is_cleared_by_other_state_changes` engine_predict.rs 606）。
- Tab 提示今天沒有：未進入的預測列是沒有數字、不反白的一列（shanjie.h 77；CandidateCells.swift 201–202 把數字藏起來但保留位置）。英文段的提示是一個沒有數字欄的格子（`showHint` Shell.swift 933；`numbered: false` InputController.swift 552）。

## B.2 要做到什麼、怎麼看到它有效

- 預測列顯示時按 ↓，候選窗前面幾項就是預測列那幾個詞、同樣順序：沒有未完成音節時之後接今天的詞庫候選去掉已列過的詞（空白鍵同 ↓）；有一個未完成音節時（預測列最常出現的情形）只列預測列的詞（未完成的音節沒有詞庫候選）。↓ 之後按 1 和 Tab 之後按 1 選到同一個詞、結果逐欄位相同。
- 未進入的預測列第一格前面有 Tab 鍵圖示；Tab 進入後換成數字 1。
- **誰看、怎麼看**：維護者實機（`imeshot`，≤ 15 秒）：前文「我想喝一杯」打 ㄋ（未完成音節），看到預測列最前面有 ⇥；按 ↓，候選窗的項目和剛才的預測列相同、同順序；Esc；Tab，第一格的 ⇥ 變成反白的 1、沒有殘留的 ⇥。另打「我想」ㄋㄧˇ（完成音節）再按 ↓：前幾項是預測列、後面接詞庫候選。main 與 verifier 看 B.4。

## B.3 行為規則

### B.3.1 候選窗帶著預測列的項目（核心 `engine.rs`）

- **開窗**（第 15 條，`dispatch` 2223；空白鍵與 ↓ 共用）：游標在尾端、沒有未完成音節、這個鍵之前顯示著未進入的預測列（`dispatch` 2065 取走的 `old`；已進入的列按 ↓／空白先照第 1e 條退回，`old` 仍是那一列）時，清單＝**預測列的項目（原順序）**＋**今天的詞庫清單去掉已出現的詞**（2512–2519 的 `seen` 已依詞字串去重，先把預測列的詞放進 `seen`）。預測列不重算，用 `old`。
- **有未完成音節時的 ↓**（改第 13 條的一種情形；今天 ↓ 在 `has_pending` 那一支被忽略，2194–2196）：有一個未完成音節、這個鍵之前顯示著未進入的預測列（`old` 非空）時，↓ 開候選窗，清單＝**預測列的項目（原順序）**，沒有詞庫項目；選取與 ⌘⌫ 照下面兩條。`old` 是空的時候 ↓ 照舊忽略。這時的空白鍵不變（照今天完成一聲音節），Tab 進入預測列不變。
- 只有詞庫清單那一支（2512）才加；標點、英文 token、空白單位那幾支（2481–2511）不變（那時預測列本來就是空的，1671）。游標不在尾端時預測列是空的（1647），清單同今天。
- `Cands` 另存這些項目（`Vec<Pred>`，從 `old` 搬進去；`self.pred` 在窗開著時仍是空的，`view` 1813 照舊只顯示窗；關窗（第 7 條 `close_cands` 1218）時項目跟著窗丟掉——「關掉後也不恢復」不變）。**不要**用 `list` 的長度欄位標記（英文窗用 0 表示英文項，2489；`forget_highlighted` 2555 用長度算區間，長度 0 在尾端會索引 `syls[cursor]`，越界）。用「索引 < 預測項目數」判斷。
- **選取**（`choose` 2586；1–9、Enter、滑鼠 `pick` 2383 都經過它）：索引落在預測項目 → 和在預測列選同一項完全相同（`choose_pred` 1738：讀音換掉 `syls[start..cursor]`、固定詞、游標、學習開著時 `pre = Some("")` 與起點鍵，§10.1），關窗。〔2026-10-17 修訂：預測項目的讀音正好等於游標前已打的音節（`reading == syls[start..cursor]`，沒有補完未完成音節或縮寫）時，它就是詞庫在這個範圍的候選，照詞庫項目改選：被換掉的字照 S4 §1.3 減半，改選凍結與新詞 B 也把它當改選。讀音比已打的音節長或不同的項目（補完未完成音節、縮寫、往後補字，例：ㄋㄧˇ 打完時的「你們」）照舊在預測列選。起因：改選凍結（#194）與新詞 B（#196）合併這一片之後，預測列開著（預設）時第 133 列在尾端開窗選「六步」又改到左邊、開窗改選「彬」學不到「正彬」，因為兩者選到的都是窗裡的預測項目。測試 `an_exact_row_item_chosen_from_the_window_is_a_repick`。〕落在詞庫項目 → 今天的改選。
- **⌘⌫**（`forget_highlighted` 2552）：反白在預測項目時照已進入預測列的 ⌘⌫（§10.3）：`forget_entry(讀音, 詞)`、重新解碼、重算預測列（同已進入預測列的 ⌘⌫；它的前文 key 與「去掉和顯示字相同的詞」跟著新的解碼）、去掉那個詞後重建清單〔2026-10-17 本地 review 後改：原本寫「預測列不重算、用同樣的 `old`」，遺忘改到前段解碼時，其他項目會帶著舊的前文 key 被選〕、選取移到同一個詞的新位置（不在就 0）、窗保持開著（維護者 2026-10-17 選擇題）；重建後清單是空的（有未完成音節、預測列只有那一項時）就關窗，同 Esc。反白在詞庫項目照今天。窗開著時關掉預測或換設定（`set_prediction(false)`、`set_profile`），帶預測項目的窗照 Esc 關掉，只有詞庫項目的窗不動（2026-10-17 本地 review 後加）。
- 直排候選窗（3v–5p）：清單同樣是「預測項目＋詞庫項目」（有未完成音節時只有預測項目），其他不變。
- 不改：`recompute_pred`、預測列的順序與內容、開窗時預測列被清掉、Tab／Shift+Tab／1–9／Esc 在預測列的規則、`pick` 在未進入預測列上的行為。

### B.3.2 Tab 圖示（殼 `CandidateCells.swift`）

- `CandidateCells.update`（188）：`selected < 0 && numbered`（未進入的預測列；候選窗一律 `selected ≥ 0`，英文段提示傳 `numbered: false`）時，第 0 格的 `numberText` 是 `CellMetrics.tabGlyph`、`showsNumber` 為 true；其他格照今天。進入後第 0 格回到 "1"。
- `CellMetrics.tabGlyph`：具名常數，起始值 `"⇥"`（U+21E5，macOS 選單對 Tab 鍵用的符號；字型與顏色先用數字欄的 `numberFont` 9 pt、secondary），註解寫出處；大小、顏色、位置由維護者實機輪次決定。
- 直排的數字欄固定寬度 `numberSlot`（74）改成「數字 1–9 與 `tabGlyph` 量到的最寬者」，進入時候選的字才不會左右移動（〔推論〕"⇥" 在 9 pt 比 "8" 寬，執行者在測試裡量）。
- **進出預測列時第 0 格重用**：橫排的重用規則（204–205）把數字當成鍵的一部分，Tab 進入時第 0 格的數字從圖示變成 1 會被重建；而 `render` 這時走 `inPlace`（InputController.swift 560–561、575–577：候選、first、total、columns 都沒變，只有 selected 從 −1 變 0），`inPlace` 不加新格、不拿掉舊格（641、646 只在非 inPlace 時做），畫面會留下舊的「⇥」格。所以改 `CandidateCells.update`：只差在「⇥」與「1」（未進入↔進入）時第 0 格算同一格，重用並 `setNumber`；**橫排裡每一個帶數字的第 0 格**（未進入的預測列、已進入的預測列、候選窗的第一格都算）數字欄寬度都取「1」與 `tabGlyph` 較寬者，換字不移位、格子的寬度不變。候選窗的第一格因此比今天略寬，這是接受的外觀變化，由維護者在實機輪次看。`render` 不改，靠第 0 格重用讓 `inPlace` 路徑正確；Shift+Tab／Esc 退回未進入時同理。
- 點圖示那一格仍是點第 0 項（155）；核心、C ABI、`Session` 都不用改。

### B.3.3 文件（同一個 PR）

`docs/contracts/s3a.md`（第 13、15 條、1.5 節 ⌘⌫、§3.1）；`shanjie.h`（`shanjie_engine_pick` 註解）；`v3-engine.md` §1.1、第 8 行的總覽（未進入的列「不帶數字」）、§3 直排那一段（98 行，↓ 開的窗前面是預測項目）、§5（124 行「數字只在 `selected ≥ 0` 時顯示」，改成未進入時第一格是 Tab 圖示）；`s3b2-glass-panel.md` 或 `candidate-vertical.md`（圖示一句）；研究紀錄。

## B.4 驗收

1. **引擎測試**（`core/tests/engine_predict.rs`，真實模型，標準排列）：

   | 名稱 | 按鍵／狀態 | 預期 |
   |---|---|---|
   | `down_opens_the_window_with_the_row_in_front` | 前文「我想」、音節 [ㄋㄧˇ]、沒有未完成音節（先斷言列非空 R）；↓ | `candidates[..R.len()] == R`；其後等於沒有這一片的清單去掉 R 的詞（測試自己照 §3.1 用 `lex.entries` 算）；`total == R.len() + 去重後的長度`；`selected == Some(0)` |
   | `space_opens_the_same_window_as_down` | 同上，另一個引擎按空白鍵 | 兩個 `Output` 相等 |
   | `down_with_a_pending_syllable_lists_only_the_row` | `build` 的狀態 (b)（前文「我想」、[ㄏㄜ ㄧ ㄅㄟ]、未完成 ㄋ，列非空 R）；↓ | `candidates == R`（同順序、沒有其他項）；`selected == Some(0)`；未完成音節還在 |
   | `down_with_a_pending_syllable_and_no_row_is_ignored` | 未完成音節、列是空的；↓ | 輸出和今天相同（第 13 條） |
   | `a_row_item_chosen_with_a_pending_syllable_equals_tab_then_the_digit` | 狀態 (b)，引擎 1：↓、`2`；引擎 2：Tab、`2` | 兩個 `Output` 相等 |
   | `a_row_item_chosen_from_the_window_equals_tab_then_the_digit` | 引擎 1：↓、`2`；引擎 2：Tab、`2` | 兩個 `Output` 相等；學習開著時各自 Enter 後 `learner().records()` 相等 |
   | `a_lexicon_item_chosen_from_the_window_is_a_repick` | ↓ 後 `pick(R.len())`（第一個詞庫項目；R 可能有 9 項，不用數字鍵） | 固定詞區間 `[cursor−L, cursor)`、`pre` 是被取代的顯示字 |
   | `pick_on_a_row_item_in_the_window_equals_the_digit` | ↓ 後 `pick(0)` 對 `1` | 相等 |
   | `the_window_after_punctuation_has_no_row_items` | 「，」之後 ↓ | 清單同今天 |
   | `esc_closes_the_window_and_the_row_stays_gone` | ↓、Esc | 沒有候選、沒有預測列 |
   | `cmd_backspace_on_a_row_item_in_the_window_forgets_it` | 先教「牛奶」；前文「我想喝一杯」、音節 [ㄋㄧㄡˊ]、沒有未完成音節（斷言 R 第 0 項是「牛奶」）；↓、⌘⌫ | 該（讀音、詞）0 筆；窗仍開；清單＝`old` 去掉那個詞＋詞庫項目；選取 0；新引擎讀到 0 筆 |
   | `cmd_backspace_on_the_only_row_item_closes_the_window` | 未完成音節、預測列只有一項；↓、⌘⌫ | 沒有候選、沒有預測列、那一項遺忘了 |
   | `vertical_window_lists_the_row_first`（`engine_vertical.rs`） | `set_candidate_vertical(1)`、同第一列、↓ | `vertical == 1`、前 R.len() 項等於 R |
   | `the_row_is_cleared_by_other_state_changes`（既有 606） | 只改 617 行的註解 | 仍通過 |

   **既有測試**：預測列預設開，載入模型、沒有未完成音節時按 ↓／空白開窗的既有測試，清單前面會多出預測項目。主題是詞庫清單的測試在設定時加 `set_prediction(false)`，不刪、不放寬任何斷言；至少 `engine_learn.rs` 的 `tiny`／`highlight`／`pick_open`／`row_expanded_pick_equals_enter` 一帶、`engine_predict.rs` 的 `a_prediction_pick_is_learned_like_a_repick`（570–578，要測的是詞庫改選）、`engine_english.rs` 用真模型的 `choose_word`／`repick_two`（`engine_learn.rs:790` 仍斷言 `candidates[1] == "欣"`）與 `engine_pack.rs` 的 `the_pack_switch_changes_the_result…`（它第四段「未完成 ㄋ 再按 ↓」從忽略變成開窗，也一併關掉預測）。執行者用 grep 找出 `engine_lm`、`engine_rerank`、`engine_demote`、`kn_tiny`、`tri_tiny`、`engine_vertical` 裡同樣情形的測試照辦。真正要改預期的，PR 逐項列出並說明；既有測試檔的 diff 只有 `set_prediction(false)` 與列出的預期變更。

   **突變**（每項讓某個測試以斷言失敗；看結束碼與斷言數）：不放預測項目；放在詞庫項目之後；不去重；`choose` 把預測項目當詞庫項目；⌘⌫ 對預測項目不遺忘；有未完成音節時 ↓ 照舊忽略。〔「列是空的時 ↓ 也開窗」是等價突變：列是空的時清單也是空的、什麼都不開，2026-10-17 review 後拿掉，程式裡的對應判斷也拿掉。〕
2. **殼**（`swift test`）：`CandidateCellTests.testPassiveRowShowsTheTabGlyphOnTheFirstCell`（`update(selected: -1, numbered: true)` → `cells[0].numberText == tabGlyph`、`showsNumber`；`cells[1].showsNumber == false`；`update(selected: 0)` → "1"；`numbered: false` → ""）；`testNumberSlotCoversTheTabGlyph`；`testEnteringTheRowReusesTheFirstCell`（**同一個** `CandidateCells` 先 `update(selected: -1)` 再 `update(selected: 0)`，候選相同：`reused[0] == true`、`removed` 是空的、`cells[0].numberText == "1"`；另一個新的 `CandidateCells` 從 `selected: 0` 開始再到 −1，同樣斷言；兩個方向前後的 `cells[0].contentWidth` 與 `candidateMinX` 都相同；今天的程式會失敗，只加寬未進入時建出的格子也會失敗）；`ShellTests.testPredictionRowShowsNoNumbersUntilTabEnters`（73）加圖示斷言。突變：圖示不設；`numberSlot` 不含圖示；第 0 格不重用（`testEnteringTheRowReusesTheFirstCell` 失敗）。
3. **不變**：`eval/golden/` 一個位元組不改；`engine_replay.rs` 三個 run 302／302；`cli/tests/golden.rs` 不變；`timing_typing76_keys` p95 < 16 ms。
4. `make test-rust`（Windows 機器的 WSL）、`make test-swift`、CI 全綠；`/code-review` 無未處置；fresh verifier CONFIRMED。
5. 實機（B.2）。
6. 保留集：不跑（不碰解碼，第一名不變；每跑一次算一次使用）。

## B.5 停止條件

任何 golden 或重播改變；p95 ≥ 16 ms；要改 `recompute_pred` 或預測列順序才做得到；實機 ↓ 後按數字選錯項。

## B.6 範圍外

詞庫候選改用語言模型排序；有未完成音節時的空白鍵；候選窗開啟時選取停在目前的詞；圖示換 SF Symbol 圖片、動畫。

## B.7 限制

executor 不連網；不讀 `eval/holdout/`、`shanjie-private/`、Discord 資料、學習檔、聊天紀錄；不安裝、不啟動 App、不執行 `.app` 裡的程式、不呼叫 TIS／lsregister、不碰 `~/Library` 與鑰匙圈；不 push、不開 PR、不碰其他 worktree；Mac 只跑改到的測試檔（`cargo test -p core --test engine_predict --test engine_vertical`、`make test-swift`），完整 `make test-rust` 由 main 在 Windows 機器的 WSL 跑；不改 `recompute_pred`、`predict`、解碼。預算：executor 1 回合＋1 次修正；外觀由維護者輪次決定。
