# 中英混打、數字列與中英文空格

這份寫的是現在的行為。

範圍：在注音組字裡不切換輸入法就能打出的英文與數字（直接回退、英文段、英文 token、短詞清單、數字列的評分切分）、英文詞後面的中文預測、送出文字的「中英文之間自動加空格」，以及英打設定的預設值、一次性重設與說明視窗。按鍵規則總表見 `docs/spec/keys-and-composition.md`，預測列的共同機制見 `docs/spec/prediction.md`，學習鍵見 `docs/spec/learning.md`，C ABI 全表見 `docs/spec/c-abi.md`，輸入法殼的其餘部分見 `docs/spec/macos-shell.md`。

## 1. 開關與生效條件

### 1.1 開關與預設值

核心有四個開關，殼的設定頁各有一個勾選，每次建引擎時都送進去。核心的預設值是引擎建立時的值，殼的預設值是 UserDefaults 沒有存值時的值（存過的值優先）。

| 開關 | 核心函式（預設） | 殼的鍵（預設） | 設定頁名稱 |
|---|---|---|---|
| 中英混打（總開關） | `set_english`（關） | `english`（開） | 中英混打（實驗） |
| 重打同一個位置算英文 | `set_english_overwrite`（開） | `englishOverwrite`（關） | 重打同一個位置算英文 |
| 依按鍵順序判斷英文 | `set_english_key_order`（開） | `englishKeyOrder`（開） | 依按鍵順序判斷英文 |
| 快打時鍵序顛倒不當英文 | `set_english_reversal_guard`（開） | `englishReversalGuard`（開） | 快打時鍵序顛倒不當英文 |

- 總開關同時是數字列的開關（見 §7），沒有另外的數字開關。
- 核心的預設與殼的預設不同：殼的預設值依 §10 調整，核心的預設不變。
- 「單按 Shift 切換中英」是另一個開關（殼的鍵 `shiftToggle`，預設關），規則見 `docs/spec/keys-and-composition.md`；它借用英文段（§4.1），所以在這裡提到。
- 「中英文之間自動加空格」（鍵 `cjkSpacing`，預設關）只在殼，核心不知道（§9）。

### 1.2 生效條件

- **影子與三種回退觸發**（`shadow_on`）：總開關開、縮寫（可省略韻母）關、標準排列。倚天排列、可省略韻母、總開關關著時，影子不記、三種觸發都不發生、英文段的新規則（§4.2、§4.3）也不生效，行為與沒有這個功能的引擎逐位元相同（Shift＋字母照 `docs/spec/keys-and-composition.md` 的舊規則）。
- **數字列**（`digits_active`）：總開關開、縮寫關、標準排列，而且語言模型與數字表 `digits.tsv` 都已載入。沒有表或沒有模型時數字功能不生效，也不回錯誤碼。
- **英文後預測**（§8）：模型已載入、預測列開，並且載入了英文前文表；沒有表時預測列與沒有這個功能的引擎逐項相同。
- 游標不在組字區尾端時，回退與英文段照樣生效：英文段開在游標處，兩邊的字分開解碼（§4.7）。
- 數字列的串開著時，鍵先進數字層（§7.4），不經過回退規則；英文段開著時，數字層不介入，數字鍵接在英文段後面。

## 2. 名詞

- **欄位**：未完成音節的三欄，0 聲母、1 介音、2 韻母。標準排列的字母鍵：聲母 `q a z w s x e d c r f v t g b y h n`、介音 `u j m`、韻母 `i k o l p`；數字鍵：聲母 `1 2 5`、韻母 `8 9 0`；韻母另有 `, . ; / -`；聲調鍵 `3 4 6 7`，空白鍵是一聲。
- **影子**：現在放在每一欄的符號是哪個鍵打的（鍵帽字元，不含 Shift），另帶這個鍵到達的順序。所以影子最多 3 個鍵、永遠和畫面上的欄位一致；以到達順序排列的字串就是「照打的順序」。
- **回退**：丟掉未完成音節，在游標處開一個英文段，內容是影子照打的順序，後面接觸發的那個鍵。
- **英文段**（`run`）：組字區裡游標處一段正在輸入的英文，最多 32 個字元，尚未成為單位。
- **英文 token**：一個組字單位，讀音是 `_punct_` 加照打的文字，固定詞（顯示與送出的文字）是它的寫法（§4.4）：多半就是照打的文字，正式寫法表裡的字是表的寫法。
- **空白單位**：讀音 `_punct_ `（一個半形空白）的單位。
- **音節在詞庫裡**：`Lexicon::entries(&[音節])` 非空，與按鍵規則表（`docs/spec/keys-and-composition.md` §4.1）第 10 條相同的判斷。

## 3. 直接回退

每個注音鍵同時進「試組的注音」與「影子」；注音不可能是使用者要的時候，整段直接回退成原始按鍵，當英文。不查英文單字表。

### 3.1 影子

- 第一個鍵開新音節、其餘鍵放進欄位時，`shadow_key[欄] = 鍵帽字元`，到達順序是目前影子裡最大順序加一。
- Backspace 刪掉顯示順序最後一個符號時，那一欄的影子一起清掉；`clear_pend` 清空三欄。
- 影子只在 `shadow_on` 成立時記；不成立時三欄一律 `None`。總開關在半個音節時打開，影子會對齊目前欄位（每個符號取它的鍵帽）。
- 開關關閉（`set_english(0)`）時影子清掉，開著的英文段留成 token。

### 3.2 三種觸發

有一個未完成音節、沒有數字串、沒有開著的英文段時：

| 鍵 | 條件 | 行為 |
|---|---|---|
| 注音鍵 | (a) 那一欄已有符號，且「重打同一個位置算英文」開 | 回退：英文段＝影子（照打的順序）＋這個鍵。開關關：取代那一欄（舊行為） |
| 注音鍵 | (b) 那一欄是空的、更後面的欄已有符號，且「依按鍵順序判斷英文」開、「快打時鍵序顛倒不當英文」關 | 回退，同上。否則放進欄位（舊行為，與蘋果注音相同） |
| 空白鍵 | (c) 三欄加一聲不在詞庫，且鄰鍵沒有收下（§3.4） | 回退，英文段＝影子；這個空白鍵立刻結束英文段（被吃掉），所以一次輸出就是一個英文 token。只要總開關開就生效 |
| 聲調鍵 `3 4 6 7` | (c) 三欄加這個聲調不在詞庫，且鄰鍵沒有收下 | 回退，英文段＝影子＋這個數字，英文段留著開。要「依按鍵順序判斷英文」開；關著時這個鍵什麼都不做（Backspace 可修） |
| 空白鍵、聲調鍵 | 音節在詞庫裡 | 照按鍵規則表（`docs/spec/keys-and-composition.md` §4.1）第 10 條完成音節；§6 的短詞清單在這裡記 |

- 「快打時鍵序顛倒不當英文」只管 (b)；(a) 與 (c) 不受影響。它開著時，快打常見的同音節鍵序顛倒（例如「個」`e k 7` 打成 `k e 7`）照注音處理。
- 窮舉測試保證：詞庫每個音節單獨照欄位順序打、再加取樣的整個讀音，三個開關全開時一次都不回退、不切分，最後的音節與總開關關著的引擎逐項相同（§13.1）。

### 3.3 還原點與中文項

- 回退時記下還原點：(a)、(b) 是「這個鍵照舊規則放進欄位之後的三欄與影子」，(c) 在聲調鍵是「按聲調前的三欄與影子」。英文段被改過（再打字、Backspace）就丟掉還原點。(c) 在空白鍵直接成 token，沒有還原點。
- (a)、(b) 另記一個**中文項**：放進欄位之後的三欄加一聲若在詞庫裡，記下這個音節。英文段沒改過字母就結束（空白、標點、Enter、不是字母的注音鍵…）時，產生的 token 帶這個中文項，之後 ↓ 的寫法清單最後多一項：詞庫對那個一聲音節的第一個詞條（§5.2）。英文段裡又打了字或按了 Backspace 就丟掉。(c) 沒有中文項。

### 3.4 與鄰鍵打錯的先後

- 「模糊輸入」的鄰鍵位元（`set_fuzzy` 位元 4，預設關）在 `finish_syllable` 對詞庫沒有的音節有一條例外：開著、模型已載入、標準排列、詞庫沒有這個音節而有同一列左右鄰鍵換出的音節時，收下。
- 順序固定：先問鄰鍵，收下就是中文，(c) 不發生；沒收下才輪到 (c)。所以鄰鍵開著時，`go`、`cup`、`cp` 這類 (c) 才能打出的字會被收成中文；要英文就用 Shift 或關掉鄰鍵。(a)、(b) 與鄰鍵無關。

### 3.5 回退當下的輸出

- `preedit` 是組字區加英文段；`english_run_utf16_start`／`english_run_utf16_len` 指向英文段（UTF-16 單位），殼畫粗底線並顯示提示（§4.6）。
- 回退不送出任何東西（英文留在組字區）、不學習；英文段開著時沒有預測列、沒有候選窗。游標不在尾端時，英文段在中間，其餘的字分開解碼（§4.7）。

## 4. 英文段

### 4.1 怎麼開

- **回退**（§3.2）。
- **Shift＋英文字母**（總開關開、標準排列）：不再照舊規則忽略或送出，而是把「未完成音節的影子的每個鍵照打的順序」加上大寫字母開成英文段，清掉未完成音節；候選窗開著就先關。組字區空的、或只有字沒有未完成音節時，英文段就只有這個大寫字母。總開關關著或倚天排列時，Shift＋字母照 `docs/spec/keys-and-composition.md` 的舊規則。
- **單按 Shift 切換成英數狀態**（`set_ascii(1)`）：未完成音節的鍵（照打的順序）成為英文段，之後沒有修飾鍵的可印字元（`0x21`–`0x7E`）中，字母、數字與 `, . ; / -` 照打的樣子接在英文段後面（不轉小寫、不當聲調或標點、不回退不切分，`1` 是字元不是選字）；其他可印字元先結束英文段（不加空白），再各自成為一個半形標點單位；細節見 `docs/spec/keys-and-composition.md`。

### 4.2 段內的鍵

英文段開著時，鍵先交給它：

| 鍵 | 行為 |
|---|---|
| 英文字母（有無 Shift 都算） | 接在英文段後面；有 Shift 為大寫，否則小寫。段已有 32 個字元時，先結束這一段（成為 token），第 33 個字元開新的一段 |
| 注音鍵裡不是字母的：`1 2 5 8 9 0 , . ; / -` | 接在英文段後面（`gta6`、`rdr2`、`win11`、`e.g` 可一口氣打完），同樣受 32 字元限制 |
| 聲調鍵 `3 4 6 7` | 見 §4.3 |
| 空白鍵 | 結束英文段成為 token，空白被吃掉（再按一下才是空白單位，§5.4）。不切分 |
| Backspace | 刪段的最後一個字元；刪光就關掉英文段（然後組字區重新整體解碼） |
| Esc | 見 §4.5 |
| 標點、Enter、Shift＋Enter、Tab、方向鍵、Home／End、Delete、其他 | 先結束英文段（成為 token），再照原本規則處理這個鍵；結束時若第 128 個單位使組字區整串送出，送出的文字放在這個鍵的輸出的 `commit`；Enter 遇到這種情況就到此為止 |

- 英文段開著時，預測列清空，Tab 因此先結束英文段，沒有預測列可進。

### 4.3 聲調鍵切分

- 英文段結尾能組成注音的**尾音節**，從英文段的文字當場算出（不另存狀態，所以 Backspace 不會讓它和畫面分歧）：從最後一個字元往前，每個字元必須是標準排列的注音鍵（小寫字母，或 `1 2 5 8 9 0 , . ; / -`；大寫字母、聲調數字與其他字元都停住），欄位嚴格遞增（往前看嚴格遞減），最多 3 個。例：`okji` 的尾音節是 `ji`（ㄨㄛ）；`gta` 的是 `a`；`A123` 的是空的。
- 聲調鍵到達時：尾音節至少 2 個符號，而且「尾音節加這個聲調」在詞庫裡，就**切分**——尾音節前面的字元（非空時）成為英文 token，尾音節成為一個完成的音節單位（讀音含聲調），英文段關掉；`english_split()` 為真。否則這個數字接在英文段後面。
- 例：`o k j i 3` 是 token `ok` 加 ㄨㄛˇ；`o k 1 8 7` 是 `ok` 加 ㄅㄚ˙；`o k g o 4` 不切（`go` 加四聲 ㄕㄟˋ 不在詞庫），成為英文段 `okgo4`；`r u s t 3 ␣` 是 token `rust3`（單一符號不切）。

### 4.4 結束成英文 token

- 英文段變成 token 有兩條路：結束英文段（`end_run`：空白、標點、Enter、其他鍵、設定呼叫）與聲調鍵切分（§4.3，尾音節前面的字元）。兩者都用 `insert_token` 在游標處插入英文 token，讀音 `_punct_` 加照打的文字，固定詞是那串文字的**寫法**（`english_writing`）。
- **寫法**：照打的文字全小寫、或只有第一個字母大寫，而且它的小寫等於正式寫法表某一行的小寫時，寫法是那一行（`chatgpt`、`Chatgpt` → `ChatGPT`；`macos` → `macOS`）；其他情況就是照打的文字（`CHATGPT`、`cHatgpt` 是使用者自己按 Shift 打的，不蓋掉；表裡沒有的 `rust`、`Rust` 照舊）。讀音不變，所以解碼的段落、學習的鄰居鍵與英文後預測（以小寫查表）都不受寫法影響。
- **正式寫法表**由兩份檔組成，都是本專案自寫（CC0）、都編進核心（不經 C ABI、不打包成資源、沒有開關）：`data/english-extra.txt`（產品、品牌與縮寫；只收確定官方寫法的，常用英文字（rust、swift、line）與聊天裡多半刻意打小寫的縮寫不收）與 `data/lexicon/names-english.txt`（名單層的英文寫法，`docs/spec/lexicon-and-packs.md` §3）。兩份格式相同：一行一個寫法，`[A-Za-z]{3,32}` 而且至少一個大寫字母，`#` 開頭的行與空行略過；小寫在同一份或兩份之間都不得重複。格式錯誤在測試就失敗。
- **英數狀態下打的英文段不換**（英數狀態下用 Backspace 重開的英文段也算）：單按 Shift 的英數狀態（`set_ascii(1)`）下開的、接了字的，或打開英數時由未完成音節的鍵變成的英文段，不管由哪個鍵或呼叫結束（包括 `set_ascii(0)`，它先改狀態再結束英文段）都照打的留著，和組字區空著時字母直接交給 App 的結果一致。
- **系統送出**：`reset` 模式 0（失焦、切換 App 時殼送出整個組字區）遇到開著的英文段，送出的那一段是它的寫法，和先按 Enter 相同；英數狀態下打的照打的送出。英文段開著時畫面上仍是照打的字。
- 短詞清單的兩項（§6）、數字串與寫法清單的選擇不經過寫法：它們不是英文段結束，清單的選擇是使用者自己挑的。
- 英文本身不學；結束時若第 128 個單位使組字區整串送出，送出的文字放在觸發結束的那個鍵（或那次設定／選字呼叫）的輸出的 `commit`，不留到之後的輸出。
- 回退開的、還沒被改過的英文段結束時，token 帶中文項與原讀音（§3.3）。
- 「中英混打」被關掉，或縮寫開啟時，開著的英文段留成 token（若是第 128 個單位則整串放進該次設定呼叫的快照的 `commit`）。

### 4.5 Esc 與 Backspace

- Esc：
  - 回退開的、回退之後還沒改過（沒再打字、沒 Backspace）的英文段：拿掉英文段，把未完成音節**還原**成還原點（§3.3）並重設影子——(a)、(b) 是這個鍵照舊規則放進欄位之後的三欄（`r u s`＋Esc＋`3` 是「你」）；(c) 在聲調鍵是按聲調前的三欄（接著用 Backspace 改錯的符號）。還原之後照舊規則繼續，再打下一個鍵才重新判斷回退。
  - 其他英文段（Shift 開的、回退後改過的）：整段拿掉，組字區其他部分不變。
  - (c) 在空白鍵已經成 token 的：沒有英文段也沒有還原點，Esc 就是任何英文 token 之後的 Esc，清掉整個組字區。
- Backspace 刪段尾一個字元，見 §4.2。

### 4.6 畫面回饋

- 輸出多兩個欄位 `english_run_utf16_start`、`english_run_utf16_len`：英文段開著時是那段字母在 `preedit` 裡的 UTF-16 起點與長度，其他時候都是 0。
- 殼：組字區的 marked text 整段細底線，英文段範圍改粗底線（`NSUnderlineStyle.thick`）；英文段開著、沒有候選時，候選窗的位置顯示一行不能選的提示「空白：英文斷開」，樣式與預測列相同（直式候選窗時用直式預測列樣式），沒有數字、沒有展開箭頭，點它不會送到核心。提示對齊英文段的起點，每個英文段只向輸入 App 問一次行位置，不隨每個字母重問。英文段結束時粗底線與提示一起消失。

### 4.7 英文段在組字區中間

- 游標不在尾端時開英文段（回退、Shift＋字母、Backspace 重開 token），組字區當場重新解碼，英文段在解碼上與英文 token 一樣切開段落：左右兩邊的字分開解碼，右邊第一個字以「前面不是漢字」處理；英文段開著時打字母與 Backspace 都不改到英文段以外的字。英文段不經結束就關掉（Esc、Backspace 刪光）時，組字區重新整體解碼。

## 5. 英文 token 與空白單位

### 5.1 定義

- **英文 token**（`english_of`）：讀音是 `_punct_` 加文字，文字非空、每個字元都在 `[A-Za-z0-9,.;/-]` 裡，而且不是單獨一個數字字母表字元（那是數字串 token，§7）。標點 token（單按標點、Shift 標點全形、Ctrl+\）、聲調符號 token、空白單位、數字串 token 都不是英文 token。
- 英文 token 與標點相同：切開解碼的段落、後面的字以「前面不是漢字」處理（看前文選字與學習都一樣）、方向鍵一次跳過、Enter 與其他送出照順序一起送出、算進組字區單位上限、不學英文本身。
- 詞庫或詞包裡以字母結尾的詞（例如「卡拉OK」）顯示成字母，但它的最後一個單位是音節，不算英文 token。

### 5.2 寫法清單與中文項

- 游標緊接在英文 token 後面、沒有未完成音節、沒有開著的窗時，↓ 開寫法清單：token 目前的文字在前（表換過的就是正式寫法），再接小寫、首字大寫、全大寫，去掉重複；不查標點替代表。選了就把 token 換成那一項。表只換全小寫與首字大寫的字，所以照打的那一種一定在清單裡。
- 回退開的 token 帶中文項（§3.3）時，清單最後多一項：詞庫對那個一聲音節的第一個詞條（`Lexicon::entries` 的第一項）。選它：token 換成那個中文詞（照按鍵規則表（`docs/spec/keys-and-composition.md` §4.1）第 10 條插入、固定那個詞、不學）。
- 短詞清單的兩項（§6）是另一條路，只出現在剛由短詞的鍵完成的音節上。

### 5.3 Backspace 逐字母刪除

- 游標緊接在英文 token 後面（沒有未完成音節、沒有英文段、沒有窗）按 Backspace：拿掉那個 token 單位，在原位開英文段，內容是 token 目前的文字去掉最後一個字母；單一字母的 token 就整個拿掉、不開英文段。來源（回退、Shift 段、短詞項目）不論。例外：token 的文字是表換上的寫法（文字和讀音裡照打的字不同，而且正是照打的字的寫法）時，用照打的字去掉最後一個字母，`ChatGPT`（照打 `Chatgpt`）重開成 `Chatgp`，補回 `t` 再結束又是 `ChatGPT`。文字和「照打的文字的寫法」不同的 token 是使用者自己選的（從清單挑的其他寫法，或英數狀態下打、沒被換成表的寫法的表內字）：照目前的文字重開，這一段結束時也不換寫法，不論是英文段結束還是聲調鍵切分（挑回 `Chatgpt` 的 token 刪一個字母再補回來仍是 `Chatgpt`）。文字已經等於那個寫法的 token（表裡沒有的字，含英數狀態下打的）和一般英文段相同。空白單位與標點 token 仍一次刪掉整個單位。這條只在 `shadow_on` 成立時適用；中英混打關著時英文 token 一次刪掉。

### 5.4 空白鍵

- 游標緊接在英文 token 或空白單位後面、沒有未完成音節、沒有開著的窗時（`breaks_with_space`，總開關開或數字列生效時成立），空白鍵在游標處插入一個空白單位。所以英文段結束是「第一下空白斷開（被吃掉），第二下空白才是空白」：Shift＋`W`＋`indows`＋空白＋空白＋「很好」送出「Windows 很好」，只按一下則是「Windows很好」。
- 空白單位沒有清單：緊接在它後面按 ↓ 什麼都不開；游標在 0、最前面是英文 token 或空白單位時，空白鍵與 ↓ 都不開寫法清單（吃掉、狀態不變）。
- 數字串 token 後面的空白鍵同樣插入空白單位（§7.4），不要求其他條件。
- 中英混打與數字都不生效時，英文 token 後面的空白鍵照舊（按鍵規則表（`docs/spec/keys-and-composition.md` §4.1）第 15 條，開寫法清單）。

### 5.5 學習與鄰居鍵

- 句首的詞後面緊接英文 token 或空白單位時，學習與查詢的鄰居鍵一律是 `^>_punct_en`（不含英文字本身）；緊接數字串 token 時是 `^>_punct_num`。`learn::next_key` 一處換算，解碼、送出、預測列、改選都經過它。中間鍵與細鍵遇到英文、數字、空白單位時不產生，只留粗鍵。
- 英文 token 的固定詞不產生學習紀錄；英文前文也不進學習檔。

### 5.6 單位上限

- 英文 token 與空白單位算進組字區 128 個單位的上限（`MAX_SYLLABLES`）；插入使單位數達 128 時整串送出，文字放在該次輸出的 `commit`。英文段本身不算單位，結束成 token 時才算。

### 5.7 中英混合詞

英文後面接中文、合起來是一個詞的（CP值、SIM卡、QR碼、hold住、get不到）不在詞庫裡，也解不出來：英文 token 切開解碼的段落，候選範圍遇到 token 就停。它們另外放一張表，只當候選，解碼不變。

- **表** `data/lexicon/mixed-words.tsv`（本專案自寫，CC0；每列寫法的依據在 `mixed-words-sources.tsv`）：`英文\t讀音\t寫法\t次數`，`#` 開頭的行與空行略過。英文是小寫 `[a-z][a-z0-9]{1,15}`；讀音是 1 到 4 個詞庫有的音節，以空白分隔；寫法的開頭不分大小寫等於英文，後面是和音節數一樣多的漢字；次數是正整數；同一組（英文、讀音）只能有一列；最多 5,000 列、256 KiB。`Engine::new` 從資料夾讀它（和 `digits.tsv` 同一處）；沒有這個檔、或任何一列不合規時整張表不載入（檔頭帶 BOM 也算不合規），引擎和沒有這個功能時相同，不回錯誤碼。沒有開關、沒有 C ABI 函式；中英混打關掉之後，組字區裡既有的英文 token 照樣會列出混合詞。
- **什麼時候出現**：游標在組字區尾端、沒有未完成音節，游標前是一個英文 token 加 k 個音節（k ≥ 1，token 到游標之間全部的音節），而且表裡有（token 讀音裡照打的文字轉小寫、這 k 個音節）這一列。只比對打的讀音，模糊輸入的替代讀音不比。游標在中間、token 和游標之間有其他 token、或音節數對不上時沒有這一項。
- **位置**：↓ 或空白開的候選窗（按鍵規則表第 15 條；按鍵方案 1 的空白是空白，只有 ↓ 開窗）第一項就是混合詞，後面照舊是詞庫的詞、容錯詞、數字項與短詞的兩項（§6）。開窗時游標在第一項，所以 Enter 或 `1` 就選它；組字區顯示的字在選之前不變。這個窗不帶預測列的項目（`docs/spec/prediction.md` §5），即使開窗前預測列正顯示著。按鍵方案 1 的第 15d 條照舊把畫面上的那個詞選起來，它排在混合詞後面（第二項）。
- **選了之後**：英文 token 和那 k 個音節換成**一個 token**，讀音 `_punct_` 加寫法（`_punct_CP值`），固定詞是寫法。寫法含漢字，所以它不是英文 token（§5.1），而是和標點同一種單位：切開解碼的段落（後面的字從句首開始解碼）、選它本身不學習、方向鍵一次跳過、Backspace 與 Delete 整個拿掉、Enter 送出寫法、Esc 清掉整個組字區；游標緊接在它後面按 ↓ 或空白開一個只有寫法一項的窗，選了不改任何東西。
- **學習看到的前文**：混合詞之後打的字，學習的前文鍵取的是畫面文字尾端的漢字，所以是混合詞的最後一個字（`CP值` 之後改選的詞記在「值」底下；逗號之後的記在句首的鍵）。句首的詞後面緊接混合詞時，鄰居鍵是 `^>_punct_` 加那個寫法，每個混合詞各一個（英文 token 共用 `^>_punct_en`，§5.5）。
- **⌘⌫**：對混合詞這一項不做任何事（它不是學來的）。遺忘清單裡別的新詞之後，混合詞仍是第一項、仍然選得到。
- 不讓解碼器自己選混合詞：沒有從清單選它的話，任何按鍵序列的組字區與送出文字都和沒有這張表時相同。
- 限制：英文要先成為 token（Shift 開頭打，或回退成英文）；照欄位順序剛好是合法注音的英文（短詞清單的字）與鄰鍵打錯開著時被收成中文的（§3.4）不會成為 token。只收英文在前、中文在後的詞。Windows 版中英混打關著，沒有這個功能。

## 6. 短詞清單

- 核心常數 `ENGLISH_SHORT`：`ai ui up co hp jp mp mp3 np rm xl`。這些字照欄位順序就能打出來，而且一聲（`mp3` 是三聲）音節在詞庫裡，所以三種觸發都不會回退它們；另給一條路。`go cp so to do hi am zi cup` 的音節不在詞庫，靠 (c) 在空白鍵就打得出來，不在清單裡。測試 `english_short_is_as_documented` 逐字核對詞庫。
- **記錄**：完成音節時（空白或聲調鍵，音節在詞庫裡、回退生效），影子照打的順序接上聲調鍵（空白不接）正好等於清單的一個字，且這次輸出沒有送出（`commit` 為空），記 `last_short = (音節位置 i, 字)`。下一個改變狀態的鍵清掉它（注音鍵、Backspace、方向鍵、Esc、送出、`reset`、會重算組字區的設定函式）；什麼都不改的鍵（直通、↑、不起作用的聲調鍵）不清，且把標記還回去。先結束英文段或數字緩衝再重送自己的鍵算有改變。`set_learning`、`set_left_context`、`set_punctuation`、`set_newword*`、`set_scan_gate`、`set_eps_global`、`set_today`、`set_english_next` 與所有 `load_*`、`learning_open`、`learning_clear` 不清。
- **清單項目**：`last_short` 有值、開窗那一刻 `cursor == i + 1`、候選清單非空、而且沒有同時有數字項時，第 15 條的 ↓ 或空白開的候選窗在清單**最後面**加兩項：那個字的小寫與全大寫（`ai`／`AI`、`mp3`／`MP3`），第一個候選與其他候選不變。與前面有幾個音節、候選範圍跨幾個音節無關，這兩項永遠只對應位置 `i` 那一個音節。
- **選了**：只拿掉位置 `i` 的那個音節，在原位插入英文 token（讀音 `_punct_` 加選的那項、固定詞同），前面的音節與固定詞不動；之後和任何英文 token 一樣；不學。⌘⌫ 對這兩項什麼都不忘記。

## 7. 數字列

中英混打開著時，不切輸入法就能在中文組字裡打出半形數字串（含 `.` `/` `-`），數字留在組字區、和中文一起送出。還沒判定是數字還是注音時，畫面顯示語境下較可能的那一種，另一種留在 ↓ 清單。

### 7.1 名詞

- **數字字母表** A＝`0123456789./-`（標準排列：1ㄅ 2ㄉ 5ㄓ 8ㄚ 9ㄞ 0ㄢ -ㄦ .ㄡ /ㄥ；3ˇ 4ˋ 6ˊ 7˙）。
- **數字串 token**：一個字一個 token，讀音 `_punct_` 加那個半形字（`_punct_2`、`_punct_.`…），固定詞是那個字。`_punct_` 後恰一個 A 裡的字元才算（`learn::digit_of`）。Backspace、方向鍵、128 單位上限、解碼與重排的切段都沿用標點 token。
- **串**（`DRun`）：從第一個進字母表的注音鍵起、連續的字母表鍵；記串開始時的歷史詞 C（`lm::history(context_key(左文＋串前的顯示字串))`，沒有漢字就是 `<s>`）。串在邊界鍵、尾段收進非字母表鍵、或尾段完成音節時結束。
- **緩衝**：串裡還沒變成 token 的鍵，最多 4 個（`MAX_BUF`；音節最多三個符號加一個聲調，更早的鍵一定不屬於任何音節），第 5 個起最舊的鍵立刻吐成 token。
- **切分**：緩衝的 n 個鍵分成「前 m 個是數字、後 t 個是音節尾段」（m＋t＝n，t ≤ 4）。尾段要**合法**：符號照聲介韻順序，是詞庫某個去聲調音節的前綴；尾段最後一個鍵是聲調鍵或空白時，必須是詞庫裡的完整音節，而且那個音節至少有一個詞條含漢字（注音字母詞如「ㄅ」不算）。t＝0（全數字）永遠合法。
- **顯示**：被選中的切分，前 m 個鍵顯示成半形數字，尾段顯示成注音欄位；尾段鏡射進 `pend` 與影子，所以預測列等讀未完成音節的地方不必特別處理。

### 7.2 評分用的機率

- 數字表 `digits.tsv` 提供計數（§7.8）。前文 C 是 `lm::history(context_key(串前的文字))`，與建表時是同一個函式。
- `P(D | C)` ＝ (N(C→D) ＋ β·P(D)) ／ (N(C) ＋ β)，P(D)＝N(D)／T；C 沒有 `D` 列時＝P(D)；β＝1。
- `P_len(m | 結束)` ＝ N(L=m)／N(D)；`P_len(m | 未結束)` ＝ N(L≥m)／N(D)；m ≥ 10 都讀 `10+` 列。
- `P_ch(k)` ＝ N(C=k)／ΣN(C)。
- `P(w | v, D)` ＝ (N(v,D→w) ＋ γ·P(w | D)) ／ (N(v,D→·) ＋ γ)，分母是 `Q` 列；沒有 `Q` 列時＝`P(w | D)`；`P(w | D)` ＝ (N(D→w) ＋ γ·P(w)) ／ (N(D) ＋ γ)；P(w) 是模型的單字後備機率（含 KN 混合）；γ＝1。
- 全部用 log10。每個運算與求和順序固定，Rust 與 `reference/proto/digits_score.py` 逐位元比對（§13.1）。

### 7.3 切分的分數與選擇

- 切分 (m, t) 的分數 S＝lpD ＋ lpZ；d＝已吐出的數字鍵數＋m。
  - lpD：d＝0 → 0；否則 log P(D | C) ＋ log P_len(d | 結束或未結束) ＋ 串的前 d 個鍵的 Σ log P_ch；t > 0 或這是邊界時用「結束」。相加順序：P(D | C)、P_len、再依鍵序加 P_ch。
  - lpZ：t＝0 → 0；否則 log Σ_{w 與尾段相容} P(w | C′)。d＝0 時 C′ 是詞語境 C，用機率 `lm.prob(C, w, pb)`（不是 λ 混合分數：lpD 與數字語境都是機率，同一鍵的各切分才可比）；d ≥ 1 時 C′ 是數字語境 (v＝C, D)，用 §7.2。「相容」與 `predict` 同一套（桶、`compat_prefix`，每個詞取最佳相容讀音的 lp 算 P(w)）。尾段是完成的音節時，「相容」＝讀音以那個完整音節開頭。
- **θ ＝ 3**：尾段是完成的音節（聲調或空白）時，不含任何數字（d＝0）的注音切分分數加 θ 再比，含數字的注音切分不加；全數字切分的分數要**嚴格大於**最好的注音切分（加過 θ）才贏，否則取最好的注音切分（注音之間平手取尾段較長）。θ 只保護「整串都當注音」的讀法；串裡已經有數字時只是在判定數字串在哪裡結束。不在字母表的注音鍵（`u j m i k , o l p ;` 與字母）當場判定、之後改不回來，所以也算「完成」，同樣套 θ。
- **其他鍵**：分數最大的切分。平手：目前顯示的數字總數不變的優先，沒有就取 t 較大者（偏注音）；串的第一個鍵沒有「目前顯示」，平手取注音。
- 同一串每鍵重算、可以翻轉（「ㄅㄞ」→「198」→「1984」）；翻轉只影響緩衝，已吐出的 token 不動。每鍵最多 5 個切分，各一次相容掃描，只在緩衝非空時。

### 7.4 各鍵

| 狀態 | 按鍵 | 行為 |
|---|---|---|
| 候選窗開著、預測列已進入 | 任何鍵 | 照按鍵規則表（`docs/spec/keys-and-composition.md` §4.1）第 1a、3、3v 條，數字層不介入 |
| 無緩衝、無未完成音節 | 字母表裡的注音鍵 | 開串；緩衝＝這個鍵；切分只有 (0,1)（當注音）與 (1,0)（當數字），評分、顯示 |
| 無緩衝、無未完成音節 | 3 4 6 7 | 聲調鍵不能單獨成尾段，一律成為數字 token（取代舊的「組字區空時打出聲調符號」與「有字時吃掉」）。↑ 照舊忽略 |
| 緩衝非空 | 字母表裡的鍵 | 接進緩衝（第 5 個起最舊的先吐成 token），評分、顯示。聲調鍵且被選切分有尾段時：前段吐 token、音節照第 10 條完成、串結束，記住尾段的鍵給 §7.7 |
| 緩衝非空 | 不在字母表的注音鍵 | 只能進尾段：尾段＝緩衝最後 ≤ 3 個鍵加這個鍵裡合法的切分，評分（含 θ）選一個；前段立刻吐 token，尾段成為一般的未完成音節（`pend`），串結束。沒有合法尾段：緩衝全吐成 token，這個鍵開新音節 |
| 緩衝非空 | 空白鍵 | 尾段以一聲完成對全數字加邊界，評分（含 θ）。注音贏：前段吐 token、完成一聲；數字贏：緩衝全吐成 token，空白重送（落到「數字後｜空白鍵」） |
| 緩衝非空 | Backspace | 去掉緩衝最後一個鍵，只重算顯示（此時不許任何尾段完成音節）；緩衝空了且沒有已吐出的 token 時串結束，有的話串留著（緩衝為空） |
| 串開著、緩衝為空 | Backspace | 串結束，這個 Backspace 照一般規則刪最後一個單位（第 11／18 條） |
| 緩衝非空 | Esc | 清掉緩衝與尾段，已吐出的 token 留著 |
| 緩衝非空，預測列非空 | Tab | 照第 8a 條進入預測列，不吐 |
| 緩衝非空 | ↑ | 照舊（未完成音節後按 ↑ 不做事），不吐 token |
| 緩衝非空 | Enter、Shift＋Enter、↓ | 照畫面顯示結束（§7.5） |
| 緩衝非空 | 標點、Shift＋字母（開英文段）、← → Home End Delete、Tab（列空）、Shift＋非標點、`reset(Commit)`、128 單位 | **邊界**：緩衝全吐成 token（沒有聲調的尾段不是詞，字母表鍵一律當數字），再照原規則處理這個鍵。例：`2 0 2 6 ⏎` → 2026；`1` Ctrl+\ → 「1、」；`1 0 0` Shift+5 → `commit`「100」、`handled` 0 |
| 數字後（左邊是數字串 token、無緩衝，而且上一個鍵就是這一串的鍵） | 字母表裡的注音鍵 | 同一串延續（`run_ctx` 不變，d 含已吐出的數字，所以 m＝0 的注音切分也用數字語境，P_len 用整串長度）；中間隔過邊界鍵（← → 等）就是新串 |
| 數字後 | 3 4 6 7 | 延續串，只有全數字切分 |
| 數字後 | ↓ | 開候選窗：數字語境預測項在前；再接那個半形字與它的全形（`０–９ ．／－`，寫死在引擎，不走標點表）；再接 §7.7 的注音讀法項 |
| 數字後 | 空白鍵 | 插入一個空白單位（§5.4）；不要求其他條件，數字功能生效就成立 |
| 任何 | `reset(Discard)` | 緩衝與串一起丟 |
| 任何 | 數字鍵盤、預測列與候選窗的 1–9、Shift＋數字的標點、Option＋數字 | 不變 |

- 128 單位：吐 token 碰到第 128 個單位時送出整串，文字放在這個鍵的輸出的 `commit`，與重送那個鍵的輸出合併；送出時先清掉上一鍵的尾段，不把未完成音節帶進 `commit`。Enter 在這種情況下，只在組字區已空時停在送出，否則照按鍵規則表（`docs/spec/keys-and-composition.md` §4.1）第 12a 條全部送出。
- 總開關關閉或縮寫開啟時，緩衝全吐成 token（快照的 `commit` 可能非空），`last_digits` 清掉。
- 學習：數字串 token 的固定詞不學；`learn::next_key` 把它正規化成 `^>_punct_num`。語意校正、trigram、重排：token 切開 stretch，同英文 token。
- 英文段開著時數字層不介入：`P4`、`A123456789`、`iPhone15` 是一個英文 token。

### 7.5 顯示、↓／Enter 照畫面

- Enter、Shift＋Enter、↓ 在緩衝非空時照畫面上被選中的切分結束這一串：前 m 個鍵（畫面顯示成數字的）吐成數字 token，尾段 t 個鍵（顯示成注音的）留成一般的未完成音節，然後照原規則處理這個鍵（↓：有未完成音節時開預測 ↓ 清單；Enter：送出組字區顯示的整串；Shift＋Enter：送出後把 Enter 直通）。t＝0 時與邊界相同。
- 因此句首單獨打 `2` 再 Enter，畫面是「ㄉ」，送出「ㄉ」；要數字就再打一個鍵讓畫面變成數字，或用預測列／↓ 清單的數字項。`2 0 2 6 ⏎` 這類畫面已顯示成數字的照舊送出數字。

### 7.6 數字後的預測列

- **尾段怎麼餵預測列**：被選切分 t > 0 時，尾段的鍵就是預測列的未完成單位。d＝0（串裡還沒有數字）時就是一般的預測列；d ≥ 1 時用數字語境。
- 游標起點的歷史不是 `<s>` 而是數字語境 (v, D)，v＝串開始的 C（沒有串時取數字 token 群之前的文字）。後繼層＝N(v,D→w) > 0 或 N(D→w) > 0 的詞。
- 有未完成單位：候選同 `predict`（相容、桶、去重），分數＝λ·log10 P(w | v, D) ＋ (1−λ)·lp，後繼層一層、其餘一層，各層依分數由大到小、同分依字數、讀音序、詞排。沒有未完成單位（串剛打完、切分是全數字、剛吐完 token）：只列後繼層裡詞庫有的詞，分數＝log10 P(w | v, D)，排序同，取前 `PREDICT_MAX`（9）個。實作 `predict_digits`，與 `predict` 共用分層與排序。
- 長起點與詞中起點略過含 token 的範圍；**緩衝裡顯示成數字的鍵（m ≥ 1、還沒吐成 token）也照 token 算**，所以只有游標起點那一列（否則選長起點項目會讀成「下午＋ㄢ」並把剛吐出的數字刪掉）。
- 選了列的項目（Tab／數字／`pick`／↓ 清單）：被選切分的前 m 個鍵吐成 token，尾段照 `pend` 被選到的詞消化掉、不吐成數字；項目的起點指吐完 token 之後的位置；學習的前文鍵在吐完 token 後重算（數字不是漢字，所以是句首鍵）。

### 7.7 ↓ 清單裡的另一種讀法

- **音節剛由字母表鍵完成**（聲調或空白、注音贏）：記住尾段的鍵（`last_digits`，下一個改狀態的鍵清掉）。這時 ↓（或空白）開的清單＝預測項目＋詞庫項目＋**最後一項那串數字**（例：句首 `8 4` → 啊；↓ 最後是「84」）；選它：拿掉那個音節，在原位逐字插入數字串 token。音節隨 128 單位整串送出時不記。
- **緩衝剛吐成 token**（數字贏或邊界吐出）且最後 ≤ 4 個 token 的鍵能組成含漢字詞條的完整音節（最後一鍵為聲調）：↓ 的清單在全形項之後加**那個音節的第一個詞條**（例：下午 `2 0 3` 判成數字後 ↓ 最後是「膽」）；選它：拿掉那幾個 token，插入那個音節並固定那個詞（不學）。取最長的符合者。
- 兩種都以「索引在清單的哪一段」判斷，不用長度欄位。⌘⌫ 忘掉預測項目後重建清單時，把這個尾項接回去；滑鼠點預測列會清掉 `last_digits`。

### 7.8 數字表 `data/lexicon/digits.tsv`

- 可有可無：缺檔或格式不對時 `Engine::new` 不回錯誤，數字功能不生效。App 打包時複製進 `Resources/`，`scripts/check-app.sh` 要求存在並與 repo 的那份 `cmp`（沒打包就會靜靜失效）。
- 格式：UTF-8、LF、`#` 開頭的檔頭行（記工具版本、k、輸入雜湊）、其餘每行 tab 分欄，依位元組排序。列：`T N`（單位總數）、`N N(D)`（數字串數）、`S N`、`D v N(v→D) N(v)`、`W w N(D→w)`、`Q v N(v,D→·)`、`P v w N(v,D→w)`、`L n N`（串長 1–9 與 `10+`）、`C ch N`（0–9 . / -）。必須有 T、N 與 C 的總和 > 0，未知的列首字就是格式錯誤。
- 建置：`tools/build_digits.py` 讀維基前 200,000 篇與口語語料，前處理沿用計數建置的共同程式，只把「取漢字段」換成保留數字串；排除留存評測句（同一計數層，含該句的漢字段不計數且當邊界）；v 與評分器的串前文是同一個函式（建表要載入模型）。檔案超過 2,000,000 位元組時 k 依序放大（1 2 3 5 10 20 30 50）直到不超過；D 與 Q 列需 N(v→D) ≥ k，W 列需 N(D→w) ≥ k，P 列需次數 ≥ max(2, k)，N(v) ≥ 1000 的 D 列與 `<s>` 的 D 列不受 k 限制。

## 8. 英文詞後面的中文預測

- 預測列的每個起點 s：看**單位**，不看顯示的字。s 前面那一個單位是英文 token（讀音 `_punct_` 加 2–32 個 ASCII 字母），或前面是一個空白單位、空白單位前面是英文 token，就取那個 token 的文字轉小寫當 e；表裡有 `E` 列的 e 才生效。起點 0 另看 App 已送出的前文結尾的英文詞（`left_full`，由殼的前文回傳提供，只在核心裡存、不回傳不寫檔；結尾 2–32 個 ASCII 字母，前面不是字母或數字，後面最多一個半形空白；前文被截斷而詞從第一個字元開始時不算）。
- 有表時，游標起點一律查（沒有未完成單位時也查）；數字語境優先於這條。預測開關關著、沒有模型、沒有表時不生效。
- **表** `english-next.tsv`：列 `E e N(e)` 與 `W e w N(e→w)`（`#` 開頭與空行略過；`W` 列必須在它的 `E` 列之後；次數為正整數；N(e) 不得小於它的 W 次數合計；e 不得重複）。可有可無：不存在就關閉功能；存在但讀不懂是載入錯誤（`Engine::new` 回 `LoadFailed`），不悄悄關掉。在 `Engine::new` 之後可用 `load_english_next` 載入（測試與量測工具用）。目前的建置沒有帶這張表，也沒有建表工具，功能在引擎與量測 CLI 都已具備，但出貨的 App 不載入它，行為與沒有這個功能相同。
- **分數**：P(w | e) ＝ (N(e→w) ＋ γ·P(w)) ／ (N(e) ＋ γ)，γ＝1，P(w) 用模型的單字後備機率。有未完成單位：相容的詞，e 的 `W` 列裡有的詞為第一層，分數 λ·log10 P(w|e) ＋ (1−λ)·lp；其餘相容的詞照 `predict` 的分層與分數（前文後繼詞優先）。沒有未完成單位：只列 e 的 `W` 列裡詞庫有的詞，依 P(w|e) 排序，同分依字數、讀音序、詞，最多 `PREDICT_MAX` 個。
- 解碼、候選窗、學習、詞包、語意校正都不經過這張表；學習的加權照一般預測列。

## 9. 中英文之間自動加空格

- 設定「中英文之間自動加空格」（鍵 `cjkSpacing`，預設關），只改善解**送出的文字**：殼呼叫 `insertText` 之前對 `commit` 套 `CJKSpacing.apply(previous:text:)`。核心送出、學習、前文用的字串不變；組字區顯示的 marked text 不加。
- **字元分類**：漢字＝一個字（grapheme）的**第一個 scalar** 符合 `LeftContext.isHan`（與核心逐 scalar 的 `is_han` 一致，所以帶異體字選擇符的漢字也算）；半形英數＝ASCII `A–Z a–z 0–9`；其他一律不觸發（全形與半形標點、符號、空白、注音符號、全形英數、emoji）。
- **送出的文字裡面**：漢字與半形英數直接相鄰（兩個方向）時插入一個 U+0020。「我用claude寫」→「我用 claude 寫」；「2026下午3點」→「2026 下午 3 點」；已有空白的、「（2026）」、「，claude」不變。
- **開頭**：令 p 是送出前游標前面的最後一個字元；p 是半形英數而送出文字第一個字元是漢字，或 p 是漢字而第一個字元是半形英數時，在開頭補一個空白；p 未知時不補。結尾不補。
- **p 的生命週期**（存在每個 `Session`）：
  - 每個可能開始組字的鍵（`!shell.composing`）先把 p 清成未知，不管帶不帶修飾鍵。
  - 接著，字元鍵（修飾鍵只允許 Shift）且學習沒有暫停時，用同一次讀取前文的結果的最後一個字元當 p（與設定核心前文用的是同一次讀取）；讀不到時 p 維持未知。
  - 每次送出之後，p 改成這次送出（加空白之後）的最後一個字元，所以同一個組字分兩次送出（碰到 128 單位）或送出後直接從預測列選字，都依上一次送出的最後一個字判斷。
  - 核心回「不處理」的鍵（直通）一律清成未知（方向鍵、Cmd＋V、數字鍵盤可能移動游標或插入文字）；`deactivate` 與換擁有者清成未知。
- 關閉時 `insertText` 收到的字串與 `commit` 逐字相同。
- 不寫進紀錄。

## 10. 英打預設值、一次性重設與說明視窗

### 10.1 預設值

- 「重打同一個位置算英文」與「單按 Shift 切換中英」沒有存值時預設關；`Shell`、`SettingsModel` 的初始值同步是關。其餘英打選項（總開關、依按鍵順序判斷英文、快打時鍵序顛倒不當英文）不變（開）。設定頁兩個開關的說明文字描述開了之後的行為。
- 原因：人打錯注音習慣不刪掉、直接接著打下一個注音，這個習慣剛好會被「重打同一個位置算英文」當成英文，看起來是中文打到一半突然變成英文；單按 Shift 在不經意碰到時會無聲切走。

### 10.2 一次性重設

- 純函式 `EnglishDefaultsReset.writes(domainKeys:learningHasFiles:markerPresent:)`（ShanjieKit）：沒有標記鍵 `englishDefaults20261104` 時，輸出把 `englishOverwrite` 與 `shiftToggle` 寫成 `false`、標記鍵寫成 `true`；有標記鍵時輸出為空（之後使用者自己打開的值不會再被重設）。
- **判斷「從舊版更新上來」**：本 App 自己的 persistent domain 的鍵（`UserDefaults.standard.persistentDomain(forName:)`，不是 `dictionaryRepresentation`）去掉標記鍵與 `englishDefaultsNoticePending` 之後還有任何鍵（設定、更新程式的 `update*`、Sparkle 的 `SU*` 都算），或容器 `Application Support/shanjie/` 底下有任何檔案，就同時寫 `englishDefaultsNoticePending = true`。兩者都沒有＝全新安裝，兩個值照樣寫成關，不寫待顯示旗標。
- `main.swift` 的 `resetEnglishDefaults` 在建 `Shell`、`Updates`、更新程式之前執行（它們之後會寫 `update*`，順序不能反），只負責讀寫 UserDefaults 與檔案系統。
- 已知邊界：沒改過設定、用沒有更新程式的開發版、也沒有學習資料的舊使用者會被當成全新安裝，值照樣被重設成關，只是不跳視窗。

### 10.3 說明視窗

- 標題「英打相關設定改成預設關」；內文三段（兩個設定被重設為關；重打同一個位置算英文的影響，重點是打錯注音後直接接著打下一個注音的習慣；單按 Shift 的影響）加一行「要再打開，到「設定 → 選字」。」；按鈕「打開設定」（開設定視窗並直接切到「選字」頁）與「知道了」。文字在 `EnglishNoticeText`。
- **狀態在 `Shell`**（面板用 protocol 注入，旗標用 store 注入，測試用假面板與假 store）：待顯示旗標為真時，任何 session 的 `activate`，或沒有 `activateServer` 就換手的 keyDown（程序剛啟動時正在打字的 App 收不到 `activateServer`），都把「下一個 keyDown 要顯示」設成真。`Session.handle` 處理 keyDown 時，這個鍵先照常處理（不被吃掉、不延遲），處理完（`defer`）檢查：要顯示且視窗沒在顯示就顯示，並把「要顯示」設成假。`flagsChanged` 不觸發。
- **收起與再跳**：「打開設定」、「知道了」、關閉鈕都清掉待顯示旗標並收起視窗。都沒按就切走：**任何一個** `deactivate` 只收起視窗，不看 owner、不改「要顯示」；新 App 的第一個 keyDown 再顯示。下次程序啟動時旗標還在，同樣再跳。
- **樣式與位置**：善解自己的小視窗（`NSPanel`，titled、closable、nonactivating，與候選窗同一個 `PanelWindow`，`canBecomeKey` 為假，首次點擊即算），不搶打字 App 的鍵盤焦點；寬 400。位置：取游標所在的螢幕（拿不到游標就用主螢幕）；游標在該螢幕上半部 → 視窗放在可用區域下半部的中間（垂直中心在可用高度 25% 處）；游標在下半部或未知 → 放在上半部中間（75% 處）。水平置中。候選窗與組字區都在游標附近，所以視窗不會蓋到它們。外觀與動畫用系統預設元件。

## 11. 設定頁與選單

- 選單「中英混打（實驗）」緊接在「即時預測」（以及顯示時的「可省略韻母」）之後、「語意校正」之前，預設打勾，倚天排列時反灰（勾選狀態仍顯示）。切換時有組字擁有者就顯示快照。選單不為其他英打選項加項目。
- 設定頁「選字」區塊，由上而下：即時預測、瀏覽器第一字不顯示預測、（可省略韻母）、**中英混打（實驗）**（下方一行說明）、**快打時鍵序顛倒不當英文**、**依按鍵順序判斷英文**、**重打同一個位置算英文**（各有一行說明）、**單按 Shift 切換中英**、**中英文之間自動加空格**、語意校正等。
  - 反灰條件：中英混打跟隨排列（只有標準排列可用）；「重打同一個位置算英文」與「依按鍵順序判斷英文」在中英混打反灰或關著時反灰；「快打時鍵序顛倒不當英文」在「依按鍵順序判斷英文」反灰或關著時反灰（因為 (b) 需要後者開著）。反灰時存值保留。「單按 Shift 切換中英」與「中英文之間自動加空格」永遠可操作。
  - 說明文字（`MenuEntry.Text`）：
    - 中英混打：「打英文不用切換輸入法：打的字不可能是注音時直接變成英文，留在組字區；按空白才發現音節不存在（例如 so）也一樣，那一種要用 Backspace 修，不能按 Esc 變回注音；數字列的鍵也會打出數字（例如 2026年、3.14）。」
    - 快打時鍵序顛倒不當英文：「快打時，同一個字的注音鍵先後顛倒（例如 ㄍㄜˋ 打成 ㄜㄍˋ）照注音處理，不會變成英文。關掉而且「依按鍵順序判斷英文」開著時，ls、mv 這類字不用按 Shift，但快打容易誤觸。」
    - 依按鍵順序判斷英文：「打了聲調鍵才發現音節不存在時，整段變成英文（馬上按 Esc 變回注音）；「快打時鍵序顛倒不當英文」關著時，注音鍵沒照順序也會變成英文。」
    - 重打同一個位置算英文：「ok、rust、gta6 這類字要靠它。開了之後，用重打同一個位置來改錯的注音也會變成英文（馬上按 Esc 變回注音）。」
    - 中英文之間自動加空格：「在漢字和半形英文、數字之間自動加一個空格（俗稱「盤古之白」）。只改善解送出的文字。」
- **換 App 或停打後的模式提示**（`shiftToggle` 開著時）：輸入法被啟用、組字擁有者換手（沒有 `activate` 就收到鍵），或距離上一個「打字的鍵」達到 30 秒（`Shell.modeHintIdleSeconds`，含睡眠時間的單調時鐘，可由測試替換），之後的第一個打字的鍵處理完，且當時組字區是空的，就在游標旁顯示一次「英」或「中」（同單按 Shift 切換時的提示），然後記下時間。「打字的鍵」＝沒有 Control、Option、Command、Caps Lock 的一個可印字元鍵（`asciiKey`），或沒有 Command／Control／Option 的空白鍵；方向鍵、Delete、Esc、Return、快捷鍵都不算也不清除待提示。單按 Shift 顯示提示時也把時間設成現在，不連續顯示兩次。提示只含模式，不含任何輸入內容。

## 12. 核心設定函式與 C ABI

- `shanjie_engine_set_english`、`_set_english_overwrite`、`_set_english_key_order`、`_set_english_reversal_guard`：參數 `enabled` 為 0 或 1，其他值回 2 且狀態不變；engine 或 out 為 NULL 回 1；成功回 0 並回快照（`handled` 1，`commit` 空，唯一例外：`set_english(0)` 結束開著的英文段或數字緩衝時，若第 128 個單位使組字區整串送出，`commit` 為那串文字）。`set_english` 預設 0，其餘三個預設 1。每次建引擎後都要呼叫（殼在 `build()` 裡送）。
- 輸出結構末尾的 `english_run_utf16_start`、`english_run_utf16_len` 見 §4.6。
- 沒有載入單字表的函式（早期的英文單字表已移除）。`set_left_context` 的前文用途見 §8。
- 工具鉤子（`#[doc(hidden)]`，不進 C ABI）：`english_reverted() -> Option<Revert>`（`Revert::{Overwrite, Order, Missing}`，最近一次 `key()` 是否回退、哪一種）、`english_split()`、`english_shadow()`（影子照打的順序）、`english_columns()`、`digits_shown()`／`digits_emitted()`／`digits_typed()`、`set_digits_trace`、`set_digit_table`／`load_digits`、`set_english_next`／`load_english_next`。每次 `key()` 開頭清除 `reverted`／`split`。
- 所有英文、影子、數字的內容不進訊息與紀錄，錯誤只有碼。

## 13. 驗證

### 13.1 核心與 CLI 測試（`make test-rust`）

- `core/tests/engine_english.rs`（真實詞庫、標準排列）：回退——`overwrite_reverts_to_the_keys`、`order_reverts_to_the_keys`、`missing_syllable_on_space_is_one_token`、`missing_syllable_on_tone_opens_a_run`、`missing_syllable_on_tone_needs_key_order`、`missing_syllable_defers_to_adjacent_key_fuzzy`、`overwrite_switch_off_is_today`、`key_order_switch_off_is_today`、`eten_abbreviation_and_off_are_bitwise_today`、`revert_hook_describes_only_the_latest_key`、`backspace_before_a_revert_clears_the_column_key`；英文段——`letters_and_digits_go_on_the_run`、`tone_splits_a_two_symbol_tail`、`tone_does_not_split_one_symbol`、`tone_split_needs_the_lexicon`、`space_never_splits`、`shift_run_takes_digits_too`、`run_takes_the_shadow_letters_and_the_shift_letter`、`run_keeps_every_unfinished_key_a_run_accepts`、`run_is_32_letters_and_the_33rd_starts_the_next`、`run_backspace_and_escape`、`backspace_and_escape_on_a_reverted_run`、`other_keys_end_the_run_first`、`run_at_the_unit_limit_sends_in_the_output_of_the_ending_key`、`run_fields_point_at_the_run`、`a_run_in_the_middle_decodes_its_two_sides_apart`、`revert_in_the_middle_decodes_apart`、`esc_after_a_space_token_is_esc_after_any_token`；token——`chinese_item_follows_the_revert`、`english_token_definition_covers_digits`、`down_after_the_token_lists_its_three_writings_and_space_types_a_space`、`space_twice_types_a_space_unit_and_one_backspace_takes_it`、`a_space_unit_has_no_list_and_space_in_front_of_a_token_opens_nothing`、`backspace_after_an_english_token_deletes_one_letter_at_a_time`、`token_next_key_is_the_fixed_en_for_learning_and_lookup`；短詞——`english_short_is_as_documented`、`short_list_adds_two_items_at_the_end`、`short_list_after_preceding_syllables`、`short_list_is_only_for_the_syllable_just_made`、`short_list_never_reverts`、`every_setting_clears_the_short_word_mark`、`keys_that_change_nothing_keep_the_short_word_mark`；窮舉——`every_syllable_alone_and_sampled_readings_never_revert`（取樣 40,000 個讀音，固定種子）、`every_reversed_key_order_of_every_syllable_is_never_english`、`reversal_guard_chinese_in_a_hurry_english_words_still_come`、`reversal_guard_keeps_the_typed_order_in_every_run`；單按 Shift 的英數狀態——`ascii_*`；正式寫法（§4.4、§5.3）——`a_word_of_the_table_takes_its_writing`、`a_name_of_the_names_layer_takes_its_writing`、`other_casings_stay_as_typed`、`the_chinese_item_stays_after_the_writing`、`the_head_of_a_tone_split_takes_its_writing`、`backspace_reopens_what_was_typed`、`a_run_typed_in_the_ascii_state_stays_as_typed`、`reset_commit_sends_an_open_run_in_its_writing`。表本身的檢查在 `core/src/engine.rs` 的 `english_table_tests`（兩份檔都載入、壞行與同一份或兩份之間的重複被拒絕）。
- `core/tests/engine_mixed.rs`（真實詞庫與模型、測試用小表）：§5.7 的每一條——`revert_token_then_syllable_lists_the_mixed_word`、`shift_token_then_syllables`、`pick_makes_one_token`、`pick_then_more_syllables_decode_alone`、`pick_is_not_learned`、`edit_after_pick`、`down_and_space_after_pick`、`forget_on_mixed_item_does_nothing`、`forgetting_a_new_word_leaves_the_mixed_item`、`middle_cursor_no_table_and_no_match_are_today`、`bad_table_is_not_loaded`、`mixed_item_sits_before_the_short_items`、`the_mixed_word_is_first_with_a_prediction_row_showing`、`key_scheme_1_keeps_the_mixed_word_first`、`the_table_comes_with_the_data_folder`、`the_learning_context_after_the_mixed_word`；`every_check_set_word_is_listed` 用出貨的表逐列打檢查集 `eval/dev/mixed-words/words.txt`（英文第一個字母按 Shift），每一列都要在清單裡，檢查集與表逐列相同。
- `core/tests/engine_digits.rs`：`row01`–`row47`（每一列對應一個按鍵序列的預期，句首語境下的評分列斷言最後的 `syls` 與 golden 記的每鍵顯示，不是手寫的直覺）、`a12_*`（↓／Enter 照畫面）、`review_p2_*`／`p3_*`／`p4_*`（128 單位送出不帶舊尾段、Enter 送出剩餘緩衝、⌘⌫ 後保留數字項、點預測列清 `last_digits`、設定函式清 `last_digits`、↑ 不吐 token）。`row49_scores_match_the_python_golden_bit_for_bit` 與 `predict_digits_matches_the_python_golden_bit_for_bit` 把每鍵的切分與分數（解析成 f64 比位元）、預測列與 `reference/proto/digits_score.py` 產生的 `eval/golden/digits-score.txt`、`digits-pred.txt` 比對；golden 在 macOS 產生，Linux 的 libm 末位不同，這兩個測試只在 macOS 的 CI 跑（Linux／WSL 跳過）。`goldens_name_the_files_they_were_made_with` 核對 golden 檔頭記的表、模型與詞庫雜湊。
- `core/tests/engine_digits_off.rs`：總開關關著、或開著而沒有數字表時，`digits-off.txt`／`digits-off-en.txt` 兩個 fixture 逐欄與沒有數字功能的引擎相同（fixture 是功能加入前的程式產生的輸出，不是和自己比）。
- `core/tests/engine_english_next.rs`：`the_key_sequence_makes_an_english_token`、`the_row_behind_joe`、`no_table_or_no_row_for_the_word_is_today`、`a_table_that_does_not_parse_is_an_error`、`the_cache_follows_the_table`、`prediction_off_has_no_effect`、`the_digit_context_comes_first`、`decoding_is_the_same_with_and_without_the_table`、`the_row_behind_an_english_left_text`、`the_english_left_text_never_reaches_the_learning_file`。
- `core/tests/engine_replay.rs`：dev302 的 302 列在總開關開、各開關開著時 302／302 相同、全程沒有回退或切分，release 每鍵 p95 < 16 ms。
- `core/src/ffi.rs` 子行程測試與 `core/tests/c/abi_smoke.c`：四個設定函式的回傳碼 0／1／2 與 NULL；經 C ABI 打 `o k ␣` 得 `preedit` `ok`、無候選；數字（`digits.tsv` 小表加小模型）`2 0 2 6 ⏎` 得 `commit` `2026`；輸出不含按鍵字串。
- 突變驗證：每條規則至少有一個讓某個測試以斷言失敗的突變（看結束碼與斷言數，不是編譯錯誤）：例如覆寫不回退、逆序不回退、(c) 空白不回退、數字鍵結束英文段、空白也切分、單一符號也切、切分不查詞庫、(c) 排在鄰鍵之前、短詞項目對整個候選範圍、θ 不套用、缺表回錯誤碼。

### 13.2 殼測試（`make test-swift`）

- `CJKSpacingTests`：純函式表格、送出與 p 的生命週期（分兩次送出、學習暫停只略過開頭、直通鍵清 p、組字開始的鍵帶修飾鍵清舊 p、異體字選擇符的漢字算漢字）、`testSettingsModelFollowsTheShellAndTheNoteIsTheContractsLine`（設定模型跟 `Shell`，說明文字逐字等於 §11）。
- `EnglishDefaultsNoticeTests`：重設邏輯（空 domain、舊設定鍵、其他任何鍵、只有標記鍵、只有學習檔、已有標記、沒存值預設關、存開仍開）、說明視窗決策（activate 後第一個鍵顯示且照常處理、沒有 activate 的換手、`flagsChanged` 不觸發、按鈕與關閉鈕、activate 與晚到的 deactivate 兩種順序、新程序旗標還在再跳）、位置與文字。
- 設定與選單測試：各存值預設、每次 `build()` 送四個開關、反灰條件、選單位置。

### 13.3 `english-sim`（`cargo run --release -q -p cli -- english-sim`）

- 驅動真的引擎，量「照順序打中文時回退誤觸的頻率」：`--rows FILE`（`前文|句子|讀音`）、`--p`（每個注音鍵的錯打機率，0＝無錯打）、`--repair overwrite|backspace`、`--seed`（預設 20261014）、`--repeat`（預設 5 個種子）、`--overwrite on|off`、`--key-order on|off`（預設 on）、`--reversal-guard on|off`（工具預設 off，好讓 `revert_b_*` 欄描述關著顛倒保護時的 (b)，與引擎和 App 的預設 on 不同）、`--digits [--lm FILE]`。輸出一行 TSV、不含任何內容文字：`rows_sha256 rows syllables keys typos` 加 `revert_a/b/c`、`split`、`english_final` 各 mean／max（每千音節、跨種子）；`--digits` 再接六欄（最後數字 token 數、瞬間顯示成數字的鍵數、因數字多按的 Backspace，各 mean／max）。
- 錯打模型固定：只對有鄰鍵的注音鍵（同一列左右各一，只留標準排列的注音鍵）以機率 `p` 錯打，一個或兩個鄰鍵均勻抽；聲調鍵、空白鍵、Backspace 與修正的鍵不錯打；修正 `overwrite` 在錯鍵與對鍵同欄時直接按對鍵一次、否則退回 `backspace`（按 Backspace 到欄位是預期的前綴再補打）。回退發生後模擬器照使用者會做的事復原（Esc 還原、再 Esc 清掉未完成音節，或 Space 直接成 token 時 Backspace 再 Esc），再把這個音節不含錯打地從頭重打；重打又回退則再計、再復原，`--retries`（預設 3）次後中止那一列。`--digits` 時 `backspace` 修正改看引擎實際的 preedit（使用者看得到 ㄅ ㄉ ㄓ 後面鄰鍵打錯而前面多出的數字，會多按一次 Backspace）。
- `--words FILE --recall`：每個詞新引擎、逐字母、空白，印分類（回退 (a)／(b)／(c)、沒有回退）與固定公開清單 `ok ls cd mv pr ai ui go cp cup npm gta6 rdr2 poe2 mp3 a4 i7 s7` 的 `probe` 行（token／中文／其他）。「成為英文 token」要畫面上是那個詞的寫法（§4.4；清單裡的 `google` 顯示成 `Google` 也算）。
- 出貨前的檢查（照順序、不打錯）：所有集合與開關組合的 `revert_*`、`split`、`english_final` 全部為 0；數字的最後 token 在 `--p 0` 與 `--p 0.02 --repair backspace` 也是 0。用覆寫改錯的 `--p 0.02 --repair overwrite` 只報、不擋——那就是「重打同一個位置算英文」的代價。
- 測試：`cli/tests/english_sim.rs` 的固定種子數字（`fixed_seed_numbers`、`the_switches_take_on_or_off`、`reversal_guard_stops_trigger_b`、`recall_counts_the_classes_and_the_probes`、`digits_columns`、`digits_backspace_repair_reads_the_screen`、`a_row_that_keeps_reverting_is_stopped_and_the_run_goes_on`）；改錯打模型要同時改這些數字。
- 這個工具不看選字結果，所以量不到 (c) 在聲調鍵的代價（模擬器在錯鍵之後立刻修正，聲調鍵到的時候欄位已是對的）。

### 13.4 `--english-next`（量測 CLI）

- `cli --english-next ROWS --table FILE [--lm FILE]`：列 `e|w|讀音`；每列從空的組字區開始送 Shift＋e 的首字母、其餘字母、Space，讀預測列 (i)（沒打注音時 w 是否在前 9 名）；再送 w 的第一個音節的第一個注音鍵，讀 (ii)（前 9 名）、(iii)（第一名）。基準與有表兩臂是同一個引擎，只差有沒有 `load_english_next`；先暖機一遍、再各量一遍每鍵 p95（單執行緒）。輸出每種設定（聊天、書面）一行加記憶體一行，不含列文字。Space 之後不是恰好一個英文 token 的列算 `no_token` 並在兩臂都算沒命中。
- 判讀慣例：(ii) 對基準用精確 McNemar（改對／改壞），(i)、(iii) 只報。測試 `cli/tests/english_next.rs`。

### 13.5 單字清單建置工具

- `tools/build_english_words.py` 從固定版本的公開英文詞頻表（輸入雜湊寫死在腳本，不符就失敗）切出 `[a-z]{3,32}` 的詞（長度 3 取前 3000 個、長度 ≥ 4 不設上限），按位元組排序，每行一個，只當 `english-sim --recall --words` 的輸入清單，不進 App、不載入引擎。`tools/test_build_english_words.py`（合成輸入，不連網：過濾、切法、排序、輸入雜湊檢查）在 `make test-python` 與 CI 的 Python 步驟裡跑。

## 14. 已知限制

- 輸入法選單與設定只有標準排列可以用英打；倚天排列與可省略韻母開著時整套規則不生效。
- 回退 (c) 在聲調鍵的代價量不到（§13.3），只靠實機回饋；空白鍵的 (c) 做出來的是完成的 token，沒有還原點，Esc 不能變回注音，要用 Backspace 修。
- 英文後面接一聲的字（`ok他`）要多按一下空白結束英文段（空白不切分，因為 `feel`、`real`、`keep` 等字的最後兩鍵剛好是詞庫裡的一聲音節）；後面接二、三、四聲且尾音節至少兩個符號時，聲調鍵會切分（`ok我`、`ok吧`、`rust編譯`）。單一符號的尾音節不切，所以 `ok是`（`okg4`）要先按空白。
- 鄰鍵打錯開著時，`go`、`cup`、`cp` 會被收成中文，不會變英文（§3.4）。
- 短詞清單只涵蓋一聲音節在詞庫裡的字；只能靠逆序打出的字（`ls`、`mv`）在「快打時鍵序顛倒不當英文」開著（預設）時要用 Shift 打。
- 中英文空格：
  - 在 Mac 上切換中英文用 Caps Lock 時善解不在作用中，之後在其他輸入法打的英文前面不會有空白；反過來由開頭規則處理。
  - 核心直通給 App 的鍵（數字鍵盤的數字、總開關關著時的 Shift＋字母）由 App 自己插入，善解不在它們前面補空白（組字中按數字鍵盤的 3：「下午」先送出，再由 App 插入 `3`，結果是「下午3 點」）。
  - 用滑鼠移動游標，殼收不到通知；之後若沒有再開始組字就直接從預測列送出，p 可能是舊的。
  - 開頭判斷靠 App 回報的游標前文字；回報不可靠的 App（部分瀏覽器與程式編輯器）可能多一個或少一個開頭空白。
- 模式提示（§11）在 Google 文件等游標位置回報不準的 App 可能落在視窗左上角；英數狀態下在終端機翻頁，每次停 30 秒以上再按鍵都會閃一次「英」。時鐘依 `clock_gettime(CLOCK_MONOTONIC)` 的文件行為會計入睡眠時間，沒有在實機睡眠後量過。
- 數字列：畫面顯示「ㄅ」（或 ㄉ ㄓ ㄚ ㄞ ㄢ ㄦ ㄡ ㄥ 開頭的單一鍵）時按 Enter 送出的是「ㄅ」，不是「1」（§7.5）；ㄅ、ㄉ、ㄓ 開頭的音節第二個鍵打錯成不合法的組合時，前面的鍵會先變成數字，要多按一次 Backspace。
- 說明視窗：游標位置未知時視窗放在上半部（§10.3）；游標在螢幕正中線時算上半部。

## 15. 設計取捨

- 不查英文單字表：「注音不可能是使用者要的」這個判斷只看鍵序與詞庫，零成本、不挑詞；`gta6`、`rdr2`、`ok`、`ls` 含數字或只有兩個字母，本來就不在三個字母以上的字母詞表裡，而且詞表要打包並附授權。
- 空白鍵不切分、聲調鍵才切分且要至少兩個符號：空白是最常見的英文結束，`feel`、`real`、`keep`、`final`、`email`、`model`、`social` 的最後兩鍵都是詞庫裡的一聲音節，空白若切，這些字全部斷成「英文加一個漢字」。單一符號不切，因為 ㄓˊ、ㄔ、ㄕˋ 都在詞庫裡，不設下限的話 `A12345 6` 會切成 `A1234`＋直。
- 影子每欄一鍵而不是記整串按鍵：影子永遠和畫面欄位一致，Backspace 與覆寫不會讓兩者分歧，也不需要「第 33 個鍵」之類的上限。
- 回退的文字取「照打的順序」而不是欄位順序：顛倒保護關著時 (b) 的英文段才會是使用者打的順序（`ls` 而不是 `sl`）。
- (c) 在空白鍵只看總開關、在聲調鍵看「依按鍵順序判斷英文」：空白鍵上這個動作原本什麼都不做，所以零成本；聲調鍵上「我打錯了一個符號、按到聲調才發現」的人原本只要 Backspace，現在要 Esc 重打，與 (b) 是同一類代價。
- 鄰鍵先於 (c)：鄰鍵打錯是更常見的原因，兩者共用「詞庫沒有的音節」這個入口，先救得回來的。
- token 的文字與讀音分開存：讀音留著照打的字，文字是寫法（`english_writing`，一個函式，英文段結束、聲調鍵切分、系統送出三處共用），所以換寫法不動解碼、學習與英文後預測，寫法清單第一項就是 token 目前的文字。
- 正式寫法表編進核心而不是載入的資料檔：兩百多行、不需要開關，Mac 與 Windows 的殼都不必多一個載入呼叫或打包步驟。只換全小寫與首字大寫：這兩種是回退與 Shift 開頭自然打出來的樣子，其他大小寫是使用者特地打的。英數狀態不換：那是把鍵盤當一般英文鍵盤用。
- 數字用評分切分而不是結構規則：同一串鍵可能是注音也可能是數字（「ㄅㄞ」與 198），合法不等於可能；結構規則在判讀集上的最後正確率低於評分切分（89.8% 對 85.2%），評分切分的純中文最後誤判在 dev302、打字測驗都是 0。θ＝3 保護整串當注音的讀法；不在字母表的注音鍵也算「完成」並套 θ，因為它當場判定、之後改不回來——不套時 dev302 有一列（前文「嗎」尚未解成「碼」）會把 ㄅ 判成 1。
- 緩衝上限 4 個鍵：音節最多三個符號加一個聲調，更早的鍵不可能屬於任何音節，立刻吐成 token 讓每鍵的切分數有上限（最多 5 個）。
- 數字語境表是計數表而非模型：N(C→D)、N(v,D→w) 等稀疏計數加平滑，檔案 ≤ 2 MB，缺檔時整個功能無聲關閉，不影響其餘。
- 英文前文表缺檔無聲關閉、讀不懂卻是錯誤：缺檔是可選功能不在，讀不懂是出貨錯誤，不能悄悄關掉。
- 中英文空格只在殼、只動送出的文字：核心的送出、學習、前文字串不變，所以學習與預測不受影響；p 存在 `Session` 而不是共用的 `Shell`，因為游標前文是每個輸入 client 自己的。結尾不補空白，因為不知道接下來會打什麼。
- 兩個英打開關預設關並一次性重設：「重打同一個位置算英文」會把習慣性的覆寫改錯當成英文，`english-sim --repair overwrite` 量到的誤觸只報、不設上限；單按 Shift 一碰到就無聲切走輸入狀態。兩者預設關，需要的人到設定打開。
