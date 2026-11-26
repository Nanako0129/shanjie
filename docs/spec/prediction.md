# 即時預測（預測列）

這份寫的是現在的行為。

預測列是候選窗位置上的一排候選：還沒選完字的時候，從已打的按鍵猜使用者要打的詞，讓使用者用 Tab 或滑鼠直接選。它只在載入語言模型時存在，由核心（`core/src/predict.rs` 與 `core/src/engine.rs` 的 `recompute_pred`）算出，殼只負責畫。數字與英文脈絡下的預測列（數字後面、英文單字後面）用同一個機制，規則見 `docs/spec/english-mixed.md`；候選窗的一般規則見 `docs/spec/keys-and-composition.md`；選字記憶的資料格式與衰減見 `docs/spec/learning.md`。

## 1. 輸出與顯示條件

### 1.1 預測列在輸出裡的樣子

預測列重用候選欄位，不另有欄位：

| 狀態 | `candidate_count` | `candidate_selected` | `candidate_columns` | `candidate_first` | `candidate_total` | `candidate_vertical` |
|---|---|---|---|---|---|---|
| 未進入（被動） | 1 至 9 | -1 | 0 | 0 | 等於 count | 橫排 0、直排 2 |
| 已進入 | 1 至 9 | 0 以上 | 0 | 0 | 等於 count | 同上 |
| 候選窗開啟 | 照候選窗 | 照候選窗 | 照候選窗 | 照候選窗 | 照候選窗 | 照候選窗 |

- `candidate_selected == -1` 且 `candidate_count > 0` 就是未進入的預測列。
- 預測列最多 `PREDICT_MAX = 9` 項，等於一頁 `PAGE_SIZE`。
- 零個候選時不顯示（候選欄位是 0 與 -1）。
- 直排設定下預測列的 `candidate_vertical` 是 2；預測列沒有「開啟」動作，每個輸出都照目前的直排設定（`set_candidate_vertical` 的快照會把正在顯示的預測列改成新方向）。

### 1.2 什麼時候有預測列

- 必須同時成立：已載入語言模型、`set_prediction` 開著（預設開）、游標在組字區尾端、候選窗關閉、沒有開著的英文段（`run`）。例外：Shift 開的、全是字母的英文段有自己的預測列（§3.5：中英混合詞與英文補完），不需要語言模型。
- 重算的時機：注音鍵放進未完成音節、完成一個音節、Backspace 刪未完成音節的符號、開始新的未完成音節；這些處理完仍符合上一條時才重算（引擎內以 `pred_dirty` 標記，鍵處理完統一重算一次）。詞庫沒有的音節不完成時（`finish_syllable` 提早返回），預測列不變。
- 其他所有狀態變化都清掉預測列：標點、聲調符號放進空組字區、開候選窗、候選窗裡的按鍵、游標移動、刪音節、送出、Esc、`reset`、`set_profile`、`pick`、選了預測、回傳碼 4、組字區達到 `MAX_SYLLABLES`（128）而自動送出。
- 什麼都不做的鍵（例如游標已在尾端時按 → 或 End）不清預測列。
- 候選窗開著時預測列不顯示，窗關了也不恢復；但開窗時預測列的項目會帶進窗裡（見 §5）。
- 沒有載入模型的引擎沒有預測列。

## 2. 候選的計算（`predict`）

`core/src/predict.rs` 的 `predict(idx, lm, cfg, v, units, mode, limit)` 回傳 `(詞, 分數, 是否後繼詞, 讀音)`。Python 參考實作是 `reference/proto/predict.py`、`predict3.py`（`reference`、`order_s`），Rust 的結果與它逐位元相同。

### 2.1 單位與相容

- 輸入按鍵先分成「單位」（`units_of`）：每個注音符號加進目前未完成的單位；聲調鍵（空白是一聲）把目前單位標成完成並記下聲調，之後的符號開新單位。
- 引擎自己組單位序列時：起點到游標的完整音節各是一個完成單位（`unit_of_syllable`，帶聲調，輕聲在前也算），再接上未完成的單位。有任何一個音節沒有注音符號（例如英文字、標點）就不查這個起點。
- 單位與詞庫讀音相容：
  - 未完成單位：該音節的符號序列以單位的符號為前綴，不限聲調。
  - 完成單位：符號與聲調都相同。
  - 前綴解讀（模式 P）：除了最後一個單位以外都要是完成的，詞的音節數不少於單位數，逐一相容。
  - 縮寫解讀（模式 PA 另外加上）：單位數等於音節數，每個單位都是未完成，逐一相容。
  - 模式 P 只用前綴解讀；模式 PA 是前綴與縮寫的聯集。引擎有兩個以上未完成單位時用 PA，其餘用 P。
- 詞庫中有音節不含注音符號的讀音不進索引。

### 2.2 後繼詞優先、兩層排序

- 歷史詞 `v`：呼叫端用 `lm::history` 求得，可以是 `<s>`。
- `succ(v)`：模型裡 `v` 的前文條目所列的後一個詞；沒有前文條目時是空的；不在詞庫的略過。
- 候選：詞庫裡至少有一個讀音與單位相容的字串，同一個字串只算一次。
- 兩層：第一層是 `succ(v)` 裡的候選，第二層是其餘候選；每個候選帶「是否後繼詞」旗標。
- 順序（S）：先第一層、再第二層；層內依序比 -分數、字數、讀音字串、詞的 code point。
- 讀音字串是達到 `lp_max` 的相容讀音中字串最小的那個，音節以 `-` 相接（`predict.py` 的 `det_key`）。`lp_max` 是這個詞在相容讀音裡最高的 capped 分數，也就是 bucket 順序裡第一個相容的項目。
- 取前 `limit` 個。實作上每一層先 `select_nth_unstable_by` 再排序，結果與全排序相同。
- 每個項目帶的讀音就是上面達到 `lp_max` 的那個相容讀音；選取時用它換掉組字區的音節。

### 2.3 分數

- 分數用 `Lm::word_by_id(cfg, ctx, wid, lp)`：模型的二元詞組分數加上 Kneser-Ney 項（模型旁邊有 `kn.sjkn` 時），λ 取目前設定（`Lm::scoring(profile)`）。
- 預測列永遠用二元詞組分數，不論設定是聊天或書面，不讀三元詞組表。
- 分數用詞庫已套 `cap_overlay` 的 capped `lp`；解碼用的降權（`demote`）不套用在預測上。
- 不在語言模型裡的詞（`wid == u32::MAX`）照樣是候選，只是不會在第一層。

### 2.4 索引

- `Index::new` 把詞庫項目依 (-lp、字數、讀音、詞) 排序，再依第一個音節的每個符號前綴分桶（`by_prefix`），查詢只掃第一個單位符號序列對應的桶。
- 索引在第一次需要時建立（建立要數百毫秒，所以引擎載入模型後就建好，不等第一個鍵）；每個 (詞庫, 模型) 組合建一次。

### 2.5 驗證

- `eval/golden/sp-predict.txt`：由 `reference/proto/golden_predict.py` 用 Python 實作產生，聊天設定。查詢行 `## <模式>\t<歷史詞>\t<按鍵>\t<單位序列>`，其後依 S 順序列前 9 個候選 `詞\t分數\t是否後繼詞`。涵蓋 typing76 每個樣本的 P1 至 P4、A1、A2，以及 P 模式查 A1、A2 的單位序列（那組查詢用來抓「P 誤用聯集」）。
- `cli/tests/golden.rs` 的 `predict_matches_python_golden`：`shanjie-eval --predict <檔> --lm <模型> --profile chat` 的輸出，候選字串、順序、後繼詞旗標相同，分數按 f64 位元比對。`predict_no_kn_scores_differently_from_the_golden` 確認 `--no-kn` 會改變多數分數；另一個測試確認 formal 設定下 `--predict` 不讀三元詞組表。
- `reference/proto/test_predict3.py`（`make test-python` 跑）：手造小詞庫與替身模型，檢查 S 的前段是後繼詞、後段是其他相容字串、層內依分數、沒有後繼詞時等於全部依分數排序。
- 換模型時照同一指令重產 golden，與其他依賴模型的 golden 一起。
- 計時：`shanjie-eval --predict … --predict-time` 在 stderr 印每次 `predict` 的 p50、p95、最大值，以及索引的建立時間與常駐記憶體。

## 3. 引擎：預測列怎麼組出來

### 3.1 起點

引擎不知道詞從哪個音節開始，所以每次重算對好幾個「起點」各查一次 `predict`。令 n 為游標位置（組字區的單位數，一個標點佔一個位置）。

- **游標起點**：只有未完成單位（或數字、英文脈絡，見 `docs/spec/english-mixed.md`）時才查，單位序列就是未完成單位。組字區裡沒有未完成單位時，游標起點沒有項目。
- **詞的起點（長起點）**：最佳路徑（`path`）裡最後兩個詞的起點，加上更前面每個詞的起點中 ≥ n - `PREDICT_BACK`（5）者。
- **詞中間的起點**：[n - 5, n) 裡不是詞起點的每個位置。使用者自己固定的詞（`Fixed`，`frozen == false`）內部的位置除外：從那裡選項目會把使用者的選擇連同待學記錄拆掉。從該固定詞開頭開始的項目照常；自動凍結的詞（`frozen == true`）內部的位置照常可當起點，選取時會把凍結的詞拆開。
- 起點到游標之間有標點就略過那個起點。
- 有兩個以上未完成單位（縮寫）時，含完整音節的起點不可能相容，所以只查游標起點，不查長起點與詞中間的起點。數字緩衝（數字鍵尚未變成字元）打開時同樣只查游標起點。
- 每個起點的單位序列 = 起點到游標的完整音節 + 未完成單位。

設計取捨：

- 起點取到往回 5 個音節，是因為長名字常被解成三段以上（例如「巴／基／斯」），真正的起點落在最後兩個詞之外。在 typing76 上，往回 5 個把「第一音節打完」階段的命中從 64.6% 提到 67.5%（8 個變好、0 個變壞）。
- 詞中間的起點排在最後：與詞的起點一起由遠到近排，會讓新詞第一鍵退步；排在游標起點之前也有新詞第一鍵退步的樣本；排最後沒有退步。它救的是解碼器切錯詞的情形（例如「想再｜家」，正確的詞「在家」從「再」開始）。

### 3.2 歷史詞

每個起點的歷史詞 `v`：輸入框前文（`left`）接上組字區起點之前的顯示字串，取最後至多 2 個連續漢字（`MAX_CONTEXT = 2`，與選字記憶的前文鍵同一個規則 `context_key`），再用 `lm::history` 求得；沒有漢字時是 `<s>`。

### 3.3 合併與學習重排

單一管線，依序執行：

1. 查詢：每個起點呼叫 `predict(…, limit)`。學習記錄裡有任何一筆的讀音可能符合這個起點的單位序列（`reading_matches_in`）時 `limit = PREDICT_SCAN`（27），其餘（含學習記錄是空的）`limit = PREDICT_MAX`（9）。新詞記錄（`NEWWORD`）不算「可能符合」。沒有符合的記錄時所有權重都是 0，兩種做法結果相同。
2. 保留：每個起點只留兩種項目，在 `predict` 原始輸出裡的索引小於 9 的，或學習權重 > 0 的；索引在任何過濾之前算。
3. 過濾：拿掉與「該起點到游標目前顯示的字串」相同的項目（例如已經解成「奶茶」就不再列「奶茶」）。
4. 長起點串接：依起點由遠到近串成一串 L，同一個詞留第一個位置、權重取所有副本中最大者。
5. 組合：head = L 裡權重 > 0 的項目（照 L 的順序）加 L 前 3 個位置（`PREDICT_LONG_CAP = 3`）裡權重 0 的項目；tail = L 其餘權重 0 的項目；整串 = head、游標起點的項目、tail、詞中間起點的項目（由遠到近）。
6. 排序：依權重由大到小做穩定排序，學過的項目排到最前面。
7. 去重（同一個詞留第一個）並取前 9 個。

學習權重 = `learner.lookup(起點的前文鍵, 讀音, 今天)` 命中裡該詞的權重；只算 `Exact` 與 `LastChar` 層，`Global` 層算 0（全域記錄不能蓋過模型有把握的結果）。前文鍵是句首 `^` 時查 `^>$`（預測的詞結束在游標，後面還沒有音節）。學習記錄是空的時，預測列與沒有學習機制時逐項相同。

設計取捨：

- 學過的項目要「移到最前面」而不是加分：一筆權重 1 的記錄只讓分數加 3.0，跨不過層級，另一層排第 9 的詞加分後只到第 7 位。
- `PREDICT_SCAN` 是 27：教過的情境都在前 9，變體在前 27。
- `reading_matches_in` 每筆記錄、每個起點都會跑（5 萬筆記錄的每鍵時間主要花在這裡），所以逐字元比對、不配置字串；它不會對 `predict` 可能回傳的讀音說 false。

### 3.4 選取

選了項目（詞 W、讀音 R 共 m 個音節、起點 s）：

- 丟掉未完成單位與預測列。
- 保留 `[s, 游標)` 範圍之外的詞原樣顯示（凍結，`freeze_outside`）；移除與範圍重疊的固定詞。
- 把 `syls[s..游標]` 換成 R（比原本多的音節插在游標處），加固定詞 `Fixed { start: s, end: s + m, word: W }`，游標移到 s + m，重新解碼。
- 學習開著時，固定詞的待學標記 `pre` 是 `Some("")`（表示預測選取、沒有被取代的顯示字），並存下建立預測列時那個起點的前文鍵；送出時照改選的規則記錄（鍵 = 起點的前文鍵，讀音 = `syls[s..s+m]`，詞 = W，權重 +1.0），沒有被取代的舊詞，所以不做減半。學習關著時 `pre` 是 `None`，什麼都不記。選取時與送出時學習都要開著才記；Esc、`reset`、回傳碼 4 都不記。
- 前文鍵作廢的情形：使用者自己改了起點左邊的字（在左邊插入或刪除音節、在候選窗改選左邊的一段），改用送出時的前文鍵。
- 預測選取的詞，在候選窗對同一段再選一次同一個詞時，保留原本的預測待學記錄。
- 前文鍵是 `^` 的兩個字以上的詞，送出時換成 `^>` 加後面那個音節的讀音（後面沒有音節就是 `^>$`）。前文鍵不是剛好兩個漢字的單字不記。
- 組字區達到 128（`MAX_SYLLABLES`）個單位時照一般規則自動送出，送出的文字放在這個輸出的 `commit`。

### 3.5 英文段開著時的預測列（中英混合詞與英文補完）

- **條件**：英文段是 Shift＋字母開的、內容全是字母（1 到 32 個，第一個是大寫，其餘大小寫都可以；有數字、符號或聲調數字就不算），不是英數狀態打的、不是回退開的；`set_prediction` 開著、中英混打開著、游標在組字區尾端。判斷只看英文段現在的文字，所以用 Backspace 從 token 重開的段內容符合時也算。不需要語言模型。
- **項目分兩類，先混合詞、後英文補完**：
  - **混合詞**：只在內容恰好是 1 到 16 個大寫字母 U 時有，是中英混合詞表（`docs/spec/english-mixed.md` §5.7）裡英文等於 U 的小寫、而且沒有標 `nopredict` 的列的寫法欄（標了的列不進預測列，其餘照舊：Shift+A 的預測列沒有「A片」，打完 ㄆㄧㄢˋ 照樣是「A片」）。同一個寫法只列一次：有好幾列時取次數最大的那一列，同次數取讀音字串（音節以空白相接）最小的那一列，選取時接的就是這一列的讀音（`get不到` 的 ㄅㄨˊ ㄉㄠˋ）。排序：有學習紀錄的在前，依衰減後權重由大到小（紀錄的鍵是 `^en:`＋U 的小寫，衰減後權重 ≥ 0.5 才算，`docs/spec/learning.md` §4.3）；其餘依次數由大到小；同分依寫法字串。
  - **英文補完**：英文表 `english.tsv` 的 `E` 列（字母開頭的）、`data/english-extra.txt`、`data/lexicon/names-english.txt` 裡，小寫以目前內容的小寫開頭、而且比目前內容長的字；同一個小寫只列一次。寫法：正式寫法表有這個字就用正式寫法（`Chatg` → `ChatGPT`）；沒有時，目前內容以兩個以上的大寫字母開頭的整個字大寫（`OK` → `OKAY`），否則是照打的內容接上這個字其餘的小寫字母（`Kar` → `Karaoke`）。排序：有 `E` 列次數的依次數由大到小，沒有次數的（只在另外兩份裡的字）排在後面；同次數或同為沒有次數時依小寫字串。沒載入英文表的引擎只有另外兩份的字。
  - **格數**：兩類合計最多 `PREDICT_MAX` 個；英文補完至少佔 min(3, 英文補完數) 格，混合詞多時從混合詞那邊少放。
- **輸出**：和一般預測列相同的欄位（`candidates`、`selected` −1、`total`），英文段的範圍照舊在 `english_run_utf16_*`。殼在候選窗的位置顯示預測列、不顯示「空白：英文斷開」的提示（`docs/spec/english-mixed.md` §4.6）；兩類項目同一種樣式。兩類都沒有項目時沒有預測列，殼顯示提示。
- **按鍵**：Tab 進入（§4.1），進入後的鍵照 §4.2（Enter、數字鍵選取，←→ 移動，Esc 或 Shift+Tab 退出）；點選照 §4.3；⌘⌫ 在混合詞上照 §4.4 忘掉那一項的紀錄，在英文補完上不做任何事。不進入、繼續打鍵時，組字區與送出文字和沒有這一列時相同。
- **重算**：開英文段、每打一個字母、Backspace 之後重算。多了小寫字母之後混合詞消失、只剩英文補完；Backspace 刪回純大寫字母時混合詞再出現。英文段不論由按鍵還是設定呼叫結束，預測列跟著清掉。
- **選混合詞**：英文段關掉，U 成為英文 token，後面插入那一列的音節，之後和照按鍵打完那一列的讀音相同：由解碼器決定（`docs/spec/decoder.md` §4.7，不固定），游標在尾端，不送出；選完到下一個鍵之前沒有預測列。Shift+K、Tab、Enter 之後 `syls` 是 `_punct_K`、ㄕㄨ，畫面「K書」；再打 ㄕˋ 是「K書是」。學習見 `docs/spec/learning.md` §4.3；只有這一列讀 `^en:` 的紀錄。
- **選英文補完**：英文段換成那個字（上面的寫法）、關掉，成為一個英文 token，游標在尾端，不送出；不學習、不改字表。
- **每鍵時間**：補完每鍵在出貨字表上查一次（字表在第一次用到時排好，之後是二分搜尋加一段前綴）。`core/tests/engine_mixed.rs` 的 `timing_english_completions`（只在 macOS 跑）：`english.tsv` 次數最高的 200 個全字母的字，每個字 Shift＋首字母、其餘字母、空白、`reset`，在 App 建法的引擎上；五輪，每輪補完開與關（測試鉤子 `set_english_complete`）交替，取各輪每鍵 p95 的中位數，開著不得比關著多 1 ms 以上。本機量到開 0.018 ms、關 0.001 ms。

## 4. 按鍵

### 4.1 未進入的預測列（被動）

- Tab（沒有修飾鍵）且預測列不空：進入預測列，選取第 0 項（`candidate_selected = 0`）。
- 預測列是空的：Tab 在有未完成音節時被吃掉，沒有時送出組字區並直通。Shift+Tab 在被動預測列上照一般規則，不進入。
- Enter：送出組字區目前顯示的內容（含未完成的注音符號），不選預測。
- ↓ 與空白：開候選窗，見 §5。直排時 ↑ 被忽略。
- 其他鍵照一般規則，同時清掉預測列。

### 4.2 已進入的預測列

插在「修飾鍵直通」（第 1 條）與標點（第 2 條）之間：

| 鍵 | 行為 |
|---|---|
| 1 至 9 | 選第 n 個；超過個數就吃掉、不動作 |
| ←、→、Tab | 移動選取（Tab 往後），到兩端停住 |
| Shift+Tab、Esc | 退回未進入（預測列保留） |
| Enter | 選目前選取的 |
| 直排時 ↑、↓ | 與 ←、→ 相同，到兩端停住（橫排時 ↑↓ 不是預測列的鍵，退出後照一般規則） |
| Backspace | 退回未進入，再照一般規則：有未完成音節時刪最後一個符號並重算預測列；沒有時刪游標左邊的音節，預測列清掉 |
| ⌘⌫ | 遺忘選取的那一項，見 §4.4 |
| 其他鍵 | 退回未進入，再從一般規則處理這個鍵 |

直通的修飾鍵（⌘C 等）不改任何狀態，預測列仍是已進入。Page Up 與 Page Down 不轉送給核心。

### 4.3 滑鼠

`shanjie_engine_pick(index)`：候選窗關閉但有預測列時（進入或未進入都一樣），選預測列的第 index 項，結果與在列上選同一項相同；候選窗開啟時照窗的規則；兩者都沒有時回 2。點未進入的預測列是明確的選取，直接選。

### 4.4 ⌘⌫ 遺忘

- 已進入的預測列上按 ⌘⌫（在第 1 條之前判斷）：對選取的項目呼叫 `forget_entry(讀音, 詞)`（丟掉同一個詞的待學記錄、刪掉選字記憶裡的記錄並改寫檔案；學習暫停時也可以遺忘），重新解碼組字區，重算預測列，仍保持已進入，選取移到同一個詞的新位置（不在列中就是 0）。
- 未進入時 ⌘⌫ 直通。

## 5. ↓ 與空白開窗時帶著預測列

預測列顯示時（未進入）按 ↓ 或空白，候選窗前面幾項就是預測列的項目、同順序，這樣預測列與 ↓ 的候選一致：

- 沒有未完成音節：清單 = 預測列的項目 + 一般的詞庫清單（讀音剛好等於游標前 L 個音節的詞，L 從長到短）去掉已出現的詞。空白與 ↓ 開出同一個窗。例外：清單有中英混合詞時（`docs/spec/english-mixed.md` §5.7），混合詞是第一項，這個窗不帶預測列的項目。
- 有一個未完成音節：↓ 開窗，清單只有預測列的項目（未完成音節沒有詞庫候選）；預測列是空的時 ↓ 被忽略。這時的空白鍵不變（照一般規則完成一聲音節），Tab 進入預測列不變。有兩個以上未完成單位時 ↓ 也被忽略。
- 已進入的預測列按 ↓ 或空白（橫排）：先退回未進入，再照上面開窗。
- 預測項目另存在窗裡（`Cands.rows`）；以「索引 < 預測項目數」判斷一項是不是預測項目，不用 `list` 的長度欄位。
- **選取**（1 至 9、Enter、滑鼠都經過同一條路）：
  - 預測項目的讀音比已打的音節長或不同（補完未完成音節、縮寫、往後補字）：與在預測列選同一項完全相同，窗關閉。
  - 預測項目的讀音正好等於游標前已打的音節（`reading == syls[start..游標]`）：它就是詞庫在這個範圍的候選，照詞庫項目的改選處理（被換掉的字減半、改選凍結、新詞學習都把它當改選）。
  - 詞庫項目：一般的改選。
- ⌘⌫：反白在預測項目時照已進入預測列的 ⌘⌫ 遺忘（`forget_entry`、重新解碼、重算預測列、去掉那個詞、重建清單），選取移到同一個詞的新位置（不在就 0），窗保持開著；重建後清單是空的就關窗，同 Esc。反白在詞庫項目照一般規則。
- 窗開著時關掉預測、換設定（`set_prediction(false)`、`set_profile`），帶預測項目的窗照 Esc 關掉；只有詞庫項目的窗不動。
- Esc 關窗後預測列不恢復。標點、英文 token、空白單位那幾種窗不帶預測項目（數字脈絡的窗另有規則，見 `docs/spec/english-mixed.md`）。
- 直排候選窗：清單同樣是「預測項目 + 詞庫項目」（有未完成音節時只有預測項目）。
- 異體寫法排序（`variant_move`）在清單組好後執行：同一個讀音下，異體表的兩個寫法都在窗裡、少用寫法排在常用寫法前面時，把少用寫法移到常用寫法的緊後面。兩個寫法都是預測項目時，預測項目跟著重排；少用寫法是預測項目、常用寫法是詞庫項目時，只有少用寫法的讀音正好等於已打音節才讓它離開預測項目並移到後面，否則不動；只有少用寫法在窗裡時不動；少用寫法帶學習權重時不動（異體表不擋學習）。異體表本身見 `docs/spec/lexicon-and-packs.md`。

設計取捨：候選窗的詞庫清單不看模型、前文、學習，只照詞庫順序；預測列看這些，所以兩份清單本來不同。把預測項目放在窗前面，使用者按 ↓ 看到的就是剛才看到的那一排。

## 6. 可省略韻母（縮寫組字器）

這個功能在核心完整實作，但 App 目前不提供開關：`Shell(showsAbbreviation: false)`（`macos/Sources/Shanjie/main.swift`），選單與設定視窗都不顯示，狀態一律關。

- 核心設定：`shanjie_engine_set_abbreviation(engine, 0|1, out)`，預設 0；其他值回 2，狀態不變。
- 生效條件（組字器「開新單位」）：縮寫設定開、預測列開、已載入模型、游標在組字區尾端、單位數未滿 `ABBR_MAX_UNITS`（10）、完整音節數加單位數小於 `MAX_SYLLABLES`。其他時候照一般規則「同一欄再按一次是取代」。10 是 ACG 詞包最長的名字的字數，縮寫只有在最長的詞以內才有用。
- 組字器：未完成音節變成有順序的未完成單位清單。注音鍵：最後一個單位的那一欄是空的就放進去，已有符號就開新單位。沒有聲母的後續音節（例如「安」ㄢ）會接到前一個單位，不開新單位（已知缺口，兩字以上的詞約 13 至 16% 打不出縮寫）。
- 有兩個以上單位時：
  - 聲調鍵與空白被吃掉、不動作（任何兩個以上單位串起來都不是合法音節，單位只靠預測列、Backspace、Esc 解決）。
  - Backspace 刪最後一個單位的最後一個符號，空的單位拿掉。
  - Esc 只清掉全部未完成單位，組字區已有的字、固定詞、游標都不動。
  - Enter 與 Shift+Enter 送出顯示的整串（所有單位的符號照順序）。
  - Tab 進入預測列（預測列是空的就吃掉）；標點照一般規則，丟掉全部單位再插入標點。
- 查詢：單位序列 = 起點後的完整音節 + 全部未完成單位，模式 PA（只有一個單位時用 P）。學習重排也套用在縮寫查詢上，`reading_matches_in` 照查詢模式判斷（PA 時前綴或縮寫解讀任一個相容就算）。縮寫選取與前綴補完的選取同樣被學習。
- 關掉縮寫，或關掉預測列，而當時有兩個以上單位：清掉全部單位並回傳清掉後的狀態；只有一個單位時不動。打開縮寫不改任何已有狀態；同時結束進行中的英文段（見 `docs/spec/english-mixed.md`）。
- 殼：存在 UserDefaults 鍵 `abbreviation`，預設關；`Shell.build()` 每建一個引擎就送一次設定（換排列、開關詞包都會重建引擎，新引擎的預設是 0）。選單項目「可省略韻母」緊接在「即時預測」之後，預測列關閉時反灰（仍顯示勾選狀態）。
- 打開縮寫時瀏覽器第一個音節的隱藏規則不生效（見 §9），因為「ㄋㄔ」是兩個未完成單位，只能靠預測列解決。

### 6.1 縮寫的離線量測 `shanjie-eval --abbr`

- 用法：`shanjie-eval --abbr A|B|C --rows <列檔> [--rows <列檔> …] --lm <模型> [--abbr-dump <檔>]`。只量、不改引擎。A 每個字只打聲母（沒有聲母的字打第一個符號）；B 去掉聲母（只有聲母的字留聲母）；C 符號全打、不按聲調。三種都不打聲調、去掉輕聲符號。
- 單位是詞：每一列（`前文|句子|讀音`，同一行在所有檔案裡只算一次）由左到右切成最長的「詞與讀音都在詞庫裡」的詞條（含 App 預設的詞包），沒有詞條蓋得到的字自己算一個排除項。每個詞都從 `reset` 之後的引擎開始，左前文是「這一列的前文＋句子裡在它前面的部分」。固定設定：聊天、詞包 `acg,it,meme,beauty`、預測列與可省略韻母開、中英混打與學習關、標準排列；摘要行的標記只寫打法、profile 與詞包（例如 `abbr-A  lm-chat+acg+it+meme+beauty`），其餘是寫死的，不另標。
- 排除（照這個順序取第一個成立的）：詞裡有注音符號、超過 `ABBR_MAX_UNITS` 個音節、組字器切出的單位不是一個音節一個（組字器合併：組字器只在那一欄已有符號時開新單位，所以下一個字的符號會落進前一個單位的空欄。例如只打聲母時「一個」的 ㄧ ㄍ 變成一個單位 ㄍㄧ，以及本節上面「沒有聲母的後續音節會接到前一個單位」）。其餘的詞報預測列的第一名、前 9 名與 MRR，另用引擎自己的索引、模型與計分設定呼叫一次不設上限的 `predict`，把完整名次分成第 1 名、第 2–9 名、第 10 名以後、不在候選四桶。B 另分「B 的按鍵等於 C 的按鍵」與其他詞兩組，並報離線的相容集合（每個音節套用同一條 B 改寫後等於單位的詞條）裡的名次。
- 工具自己檢查、不成立就不印數字並以非 0 結束：預測列是不設上限清單的前 9 名；A、C 的可量詞都在候選裡；B 的離線集合含正解；照組字器規則模擬判成「會合併」的詞，和引擎 `pending_units` 判成組字器合併的詞完全相同；句子字數和讀音音節數不同的列（錯誤訊息寫出檔名與行號、兩邊的數目，不印列的內容）。
- 最後一行 `## merges` 把組字器合併分成兩類：`backward_only`（詞裡每個合併點，下一個字的第一個符號所在的欄都排在那個單位已填的最後一欄之前，例如只打聲母時「一個」的 ㄍ 接在 ㄧ 後面）與 `has_forward`（至少一個合併點往後，例如不打聲調時「西安」的 ㄢ 接在 ㄒㄧ 後面）；兩類加起來等於 `excl_merged`。欄序是聲母、介音、韻母。`--abbr-dump` 照 `--predict` 的格式寫出每個可量詞的查詢與預測列（沒有分數欄），`--predict` 讀它要印出相同的詞。
- 為此核心有兩支不進 C 介面的唯讀函式（`#[doc(hidden)]`）：`Engine::pending_units`（目前的未完成單位，照打字順序）與 `Engine::with_predict_parts`（交出預測列用的那一份索引、模型與計分設定；沒有模型時是 `None`）。兩者都不改引擎狀態；加上它們之前與之後，dev302、打字測驗、錯字回報的聊天與書面 rowstats 和 dev302 的重播 rowstats 逐位元組相同。

## 7. 設定與 C ABI

- `shanjie_engine_set_prediction(engine, 0|1, out)`：預設 1；0 或 1 回 0，其他值回 2（狀態不變），NULL 回 1。關閉時清掉預測列、已進入的列退出（之後 Tab 照被動規則）、有兩個以上未完成單位時一併清掉、帶預測項目的窗關閉；開啟時重算並回傳快照，符合 §1.2 就馬上出現。關閉時 `recompute_pred` 直接返回。沒有載入模型時沒有效果。
- `set_profile` 的快照同時清掉預測列再重算。
- `shanjie_engine_pick` 的規則見 §4.3，回傳碼：1（engine 或 out 為 NULL）、2（沒有候選窗也沒有預測列，或 index 超出）、4（內部錯誤，引擎重置）；錯誤時 `*out` 是 NULL。
- `core/include/shanjie.h` 的註解與 `core/tests/c/abi_smoke.c`（第一鍵就有預測列、`set_prediction` 的 0、1、2 回傳碼）一致。ABI 的整體說明見 `docs/spec/c-abi.md`。
- 殼：`PredictionStore`，`DefaultsPredictionStore` 的 UserDefaults 鍵是 `prediction`，沒存過算開；選單項目「即時預測」（勾選表示開）與設定視窗「選字」區的同名開關同步；啟動與每次 `build()` 都送一次。

## 8. 殼：預測列的外觀

- 未進入的預測列第一格前面顯示 Tab 鍵圖示 `CellMetrics.tabGlyph`（"⇥"，U+21E5，macOS 選單對 Tab 鍵用的符號，以數字欄的 9 pt 字型與次要顏色畫），其他格不帶數字；進入後第一格換成數字 1。`CandidateCells.update` 的條件是 `selected < 0 && numbered`（候選窗一律 `selected ≥ 0`；英文段提示傳 `numbered: false`，沒有數字欄）。
- 橫排：第 0 格的數字欄寬度取「1」與圖示較寬者（`firstCellNumberRoom`），換字時格子寬度與候選位置不變；進出預測列時第 0 格重用並 `setNumber`（`render` 的 `inPlace` 路徑只改選取、不加不減格子，若不重用會留下舊的圖示格）。候選窗的第一格因此比只有數字時略寬。
- 直排：數字欄固定寬度 `numberSlot` 取數字 1 至 9 與圖示量到的最寬者，進入時候選的字不會左右移動。
- 點圖示那一格等於點第 0 項。
- 預測列與候選窗用同一個玻璃面板與同樣的格子；面板位置照一般的 `lineRect` 規則；殼不另外加動畫。
- 原生程度：預測列是自製的格子（系統沒有對應元件），外觀以接近蘋果注音的候選窗為準。

## 9. 瀏覽器裡第一個音節不顯示預測列

### 9.1 原因

Google 文件使用 EditContext，回給輸入法的組字範圍是 `{1, n}`（組字前面多放一格）。殼用 `attributes(forCharacterIndex:)` 問第 0 個字的位置時，Chrome 收到 `{0,1}`，起點在組字範圍之前，回傳過時的 `first_selection_rect`，也就是視窗左上角；第一個音節時索引 1 以上沒有轉給 Chrome，殼拿到 (0, 0)；`firstRect(forCharacterRange:)` 與不顯示的 `IMKCandidates` 的 `candidateFrame()` 也拿不到位置。第二個音節起問到的是組字範圍內的字，位置是對的。第三方輸入法經過 IMK 能用的幾條路，量到都拿不到 Docs 第一個音節時的正確位置，所以改為這個時期不顯示預測列。

以上是 Chrome 156（.0.8078.79、.80）量到的。Chrome 157（.0.8103.51）起第一個音節的位置是對的：Docs 組字時呼叫 `updateCharacterBounds`、`updateSelectionBounds`，第一個鍵後 0.15 秒的預測列與按 ↓ 的候選窗都在那一行下面（行首組字範圍 `{0,1}`、行中 `{1,2}` 都量過），同一台 Mac 從 156 更新到 157 後也不再跑到左上角。所以這個開關改成給 Chrome 156 以前（與同代的 Chromium 瀏覽器）用，預設關。

### 9.2 規則

預測列這個輸出（`candidate_selected == -1`、有候選、沒有英文段提示）在同時滿足下列條件時，殼不顯示（`hideCandidates()`）：

- 開關 `browserFirstSyllable` 開著（預設關，給 Chrome 156 以前用；「即時預測」關掉時整個預測列都不顯示，這個開關反灰，設定保留）。
- 「可省略韻母」關著。
- 組字區只有一個音節（`Shell.singleSyllable(preedit)`）：連續的注音符號（ㄅ 至 ㄩ，U+3105 至 U+3129）與聲調（ˊ ˇ ˋ ˙）合算一個，其他每個字元各算一個，總數 ≤ 1 就算。「ㄘㄜ」是 1、「測」是 1、「1」是 1、空字串是 1，「測ㄕ」是 2；顯示規則把未完成音節顯示成英文字母時照字元數算。
- 目前 App 的 bundle ID 在 `Shell.browserApps`。

只有殼不顯示，核心照常計算預測列：Tab 進入後 `candidate_selected ≥ 0`，照常顯示；↓、空白開的候選窗也照常顯示。第二個音節的第一個注音鍵起預測列照常顯示。

### 9.3 瀏覽器清單

`Shell.browserApps`（與 `chatApps` 並排，用 bundle ID 比對）：`com.google.Chrome`（含 `.beta`、`.dev`、`.canary`）、`org.chromium.Chromium`、`com.microsoft.edgemac`（含 `.Beta`、`.Dev`、`.Canary`）、`com.brave.Browser`（含 `.beta`、`.nightly`）、`company.thebrowser.Browser`（Arc）、`com.vivaldi.Vivaldi`、`com.operasoftware.Opera`、`com.operasoftware.OperaGX`、Crest（`com.pauldavis.crest`）、Aside（`at.studio.AsideBrowser`）。Electron App 不列：沒有量到它們有這個問題。

### 9.4 開關與判斷位置

- 設定視窗「選字」區，「即時預測」下面：開關「在瀏覽器裡，第一個字不顯示預測」，下面一行說明（給 Chrome 156 以前或同代 Chromium 瀏覽器的 Google 文件用、打開會擋掉第一個字的預測列、Chrome 157 起不需要）；選單不加。
- 存在 `PredictionStore` 的 `browserFirstSyllable`（UserDefaults 鍵 `browserFirstSyllable`），沒存過算關；存過的（在設定裡改過，不論開或關）照存的值。
- 判斷放在 `Session.apply(_:)`（`hidesFirstSyllableRow`），所以每一種輸出都照同一條：按鍵、點選、重排工作、各個設定的快照、activate。bundle 不另外讀：Session 記住最近一次既有讀取的值（`send` 每鍵讀的那一次，以及 `applyProfile(bundle:)` 收到的那一次），不新增 `bundleIdentifier` 讀取。

### 9.5 驗證

`macos/Tests/ShanjieKitTests/BrowserFirstSyllableTests.swift`（`make test-swift`，用假的 client 與面板，經過真的核心）：

- `testABrowserHidesTheRowForTheFirstSyllableOnly`、`testCrestAndAsideHideTheRowForTheFirstSyllable`：清單內的 App，第一個音節的每一鍵不顯示，第二個音節的第一鍵顯示。
- 測試的 Shell 預設把開關打開（規則的案例都在開著時跑），沒存過的預設另外測。
- `testOtherAppsAndTheSwitchOffKeepTheRow`：不在清單或開關關閉時與一般相同。
- `testNeverChosenShowsTheRowInABrowser`：沒存過時，清單內的瀏覽器每一鍵都顯示預測列。
- `testTabAndDownStillShow`：Tab 與 ↓ 照常顯示。
- `testSingleSyllable`：判斷函式。
- `testOneBundleReadPerKey`：清單內外每鍵 `bundleReads` 增量都是 1。
- `testTheSwitchIsStored`：預設關；打開後存成 true、重建 Shell 仍是開；再關掉存成 false。
- `testANonKeyOutputFollowsTheRule`：非按鍵的輸出（設定快照）也套用同一條。
- `testAbbreviationOnKeepsTheRow`：縮寫開時預測列不隱藏。

### 9.6 已知限制

- Chrome 156 以前：第一個音節時按 Tab 進入的預測列、游標移到組字區開頭（←）之後的預測列，殼問的仍是起點 0 的範圍，在 Docs 裡位置應該同樣錯（推論，沒有量過）；第一個音節時的候選窗（↓、空白）跑到左上角（156.0.8078.80 量到）。
- Chrome 157：第一個音節按 ↓ 的候選窗位置正確（157.0.8103.51 量到）；Tab 與 ← 之後的預測列沒有量。
- 「不用組字區前面那一格方框」的做法（對瀏覽器改問索引 1 以上）試過：Chrome 的一般 `<textarea>` 對索引 0 會回組字的第一個字、位置本來就對，用「瀏覽器且組字起點 > 0」的判斷會讓這類欄位變差，要另找能分辨 Docs 的訊號；所以沒有做。
- 不在清單的 Chromium 瀏覽器與 Electron App 不套用。

## 10. 驗證方式

- 核心：`core/tests/engine_predict.rs`（`make test-rust`，真實模型）。
  - 預測列等於獨立實作：`row_equals_the_independent_computation_in_states_a_to_e`（狀態 (a) 空組字區、(b) 起點前已有解碼的詞、(c) 長起點範圍內有標點、(d) 目前的詞已解成項目之一、(e) 兩個長起點加游標起點超過 3 項）、`production_path_shows_milk_tea_and_equals_the_oracle`、`after_a_cup_the_row_has_milk_tea`（前文「我想喝一杯」打 ㄋ，含「奶茶」）。
  - 起點：`a_word_start_three_words_back_is_a_long_start`、`a_word_start_exactly_five_syllables_back_is_a_long_start`、`a_position_inside_a_word_adds_its_items_after_the_others`、`positions_inside_words_come_last_so_the_cursor_start_keeps_its_items`、`a_learned_item_of_a_position_inside_a_word_comes_first`、`positions_inside_a_word_the_user_fixed_are_not_starts`、`reading_matches_compares_the_first_syllable_as_before`。
  - 進出與按鍵：`tab_enters_and_shift_tab_leaves`、`another_key_leaves_and_is_handled_as_usual`、`digits_select_and_out_of_range_digits_are_swallowed`、`enter_selects_the_highlighted_item`、`enter_commits_with_a_passive_row_and_selects_with_an_entered_one`、`backspace_with_a_pending_unit_leaves_deletes_the_symbol_and_recomputes`、`backspace_with_only_whole_syllables_leaves_deletes_the_syllable_and_clears_the_row`、`the_row_is_cleared_by_other_state_changes`、`keys_that_do_nothing_keep_the_row`、`the_row_is_empty_when_the_cursor_is_not_at_the_end`、`no_model_no_row`、`pick_selects_from_the_passive_row_and_fails_without_any_row`。
  - 選取：`selection_replaces_the_readings_moves_the_cursor_and_fixes_the_word`、`a_prediction_pick_is_learned_like_a_repick`。
  - 學習（T1 至 T20 系列）：`t1_the_written_milk_case`、`t2_gates_record_nothing`、`t3_a_single_character_needs_a_han_context`、`t4_*`、`t5_a_global_record_does_not_move_the_row`、`t6_the_order_survives_a_restart`、`t7_cmd_backspace_on_the_entered_row_forgets_the_item`、`t8_the_switch`、`t10_*` 至 `t14_*`（學過的項目不受長起點上限限制）、`t15_*`、`t16*`（記錄的鍵）、`t17_*`、`t18_*`、`t19_*`、`t20_the_scan_gate_changes_nothing_but_time`。
  - 縮寫：`abbreviation_two_units_show_and_select_milk_tea`、`abbreviation_needs_the_setting_the_row_and_the_end_of_the_composition`、`abbreviation_needs_a_model_and_stops_at_the_unit_cap`、`abbreviation_keys_with_two_units`、`abbreviation_esc_drops_only_the_units`、`abbreviation_off_or_row_off_drops_two_units_only`、`abbreviation_picks_are_learned_like_prefix_picks_and_come_first_next_time`、`reading_matches_follows_the_query_mode`。
  - 直排：`the_prediction_row_carries_two_when_vertical_and_zero_when_horizontal`、`the_setter_snapshot_re_renders_a_shown_row_in_the_new_orientation`、`the_entered_vertical_row_moves_with_up_and_down_and_stops_at_both_ends`、`the_passive_vertical_row_keeps_down_as_open_and_up_as_ignore`、`the_horizontal_prediction_row_keeps_its_keys_and_output`。
  - ↓ 開窗：`down_opens_the_window_with_the_row_in_front`、`space_opens_the_same_window_as_down`、`down_with_a_pending_syllable_lists_only_the_row`、`down_with_a_pending_syllable_and_no_row_is_ignored`、`a_row_item_chosen_with_a_pending_syllable_equals_tab_then_the_digit`、`a_row_item_chosen_from_the_window_equals_tab_then_the_digit`、`a_lexicon_item_chosen_from_the_window_is_a_repick`、`an_exact_row_item_chosen_from_the_window_is_a_repick`、`pick_on_a_row_item_in_the_window_equals_the_digit`、`the_window_after_punctuation_has_no_row_items`、`esc_closes_the_window_and_the_row_stays_gone`、`cmd_backspace_on_a_row_item_in_the_window_forgets_it`、`cmd_backspace_on_the_only_row_item_closes_the_window`、`prediction_off_or_a_profile_change_closes_a_window_with_row_items`；異體表：`variant_order_in_the_window_with_the_prediction_row` 等。直排窗：`core/tests/engine_vertical.rs` 的 `vertical_window_lists_the_row_first`。
- 英文段開著時的預測列（§3.5，`core/tests/engine_mixed.rs`）：混合詞 `the_run_row_lists_the_rows_by_count`、`one_writing_is_listed_once`、`a_nopredict_row_is_not_in_the_run_row`、`choosing_from_the_run_row`、`no_run_row_without_its_conditions`、`a_chosen_item_is_learned_under_the_en_key`、`forgetting_an_item_of_the_run_row`（這幾個用測試鉤子關掉英文補完，只看混合詞）；英文補完 `the_run_row_completes_english_words`、`the_run_row_keeps_three_places_for_english`、`timing_english_completions`；殼 `ShellTests.testRunRowTakesTheHintsPlace`。
- C ABI：`core/tests/c/abi_smoke.c`、`core/src/ffi.rs` 內的 `set_prediction` 與 `set_abbreviation` 回傳碼測試。
- 殼：`macos/Tests/ShanjieKitTests/ShellTests.swift` 的 `testPredictionRowShowsNoNumbersUntilTabEnters`（第一鍵就有預測列、沒有選取與數字、Tab 後有數字與圖示）、`testPredictionMenuItemTogglesStoresAndSurvivesRestart`、`testAbbreviationMenuItemTogglesStoresRebuildsAndSurvivesRestart`；`CandidateCellTests.swift` 的 `testPassiveRowShowsTheTabGlyphOnTheFirstCell`、`testNumberSlotCoversTheTabGlyph`、`testEnteringTheRowReusesTheFirstCell`。
- 不變的檢查：預測不影響解碼，選字結果與既有 golden 一個位元組都不改；重播測試的解碼結果不變。
- 計時（`#[ignore]`，release、低負載時跑）：`cargo test --release -p core --test engine_predict timing_typing76_keys -- --ignored --nocapture --exact`，另有學習 5 萬筆記錄與縮寫變體（`timing_typing76_keys_with_taught_picks_and_50k`、`timing_typing76_keys_abbreviation`、`timing_typing76_keys_with_taught_picks_and_50k_abbreviation`）。每鍵 p95 必須小於 16 ms。往回 5 個音節的起點在 5 萬筆學習記錄下 p95 約 3 ms（10 核機器、負載高於核心數時量到 2.9 至 3.2 ms），沒有學習記錄時與往回 0 的差別在雜訊範圍內。
- 起點範圍的量測：`measure_start_range` 與 `measure_start_range_acg`（`#[ignore]`）。每個量測狀態平均的 `predict` 次數：typing76 約 4.2，詞包名字單獨打約 3.1。

## 11. 已知限制

- 縮寫：沒有聲母的後續音節接到前一個單位，兩字以上的詞約 13 至 16% 打不出縮寫；整句縮寫沒有做；App 不提供這個開關。
- 往回 6 個音節對長名字更好（第五音節以後的第一鍵 85% 對 92%），代價是每個量測狀態多 0.02 至 0.77 次 `predict`；目前維持 5。
- 詞中間的起點排在游標起點之前會讓第二音節第一鍵多命中 12 個、新詞第一鍵少命中 4 個，目前維持排最後。
- 預測列原本是空的狀態，會因為詞中間的項目變成不是空的，Tab 的行為跟著改；在 typing76 的 904 個量測狀態裡只有 2 個。
- 預測項目的學習與重排只重排預測列的項目，不把選字記憶當候選來源。
- 英文段的預測列（§3.5）：混合詞只在內容是純大寫字母時有，多打一個小寫字母或注音鍵之後不依它篩選、直接消失（Shift+K 再打 `g` 不會只留 ㄍ 開頭的詞）；英文補完只看字母，內容有數字（`Gta6`）就沒有。游標不在尾端時整列沒有。英文補完的來源是出貨的字表，不看前文、不學習。
- 詞庫候選窗的清單不用語言模型排序。
- 瀏覽器第一個音節的位置問題見 §9.6。
