# 中英混打第四片：直接回退（每一鍵判斷、不查單字表）

狀態：2026-10-30 接回，另加 `zhen-reapply.md`（新開關「快打時鍵序顛倒不當英文」，預設開，(b) 逆序因此預設不發生）。2026-10-28 曾因維護者實機誤觸太多從 main revert（研究紀錄同日「中英混打回退與單按 Shift 從 main revert」）。

舊狀態：**READY**（2026-10-27 收尾審查；main 的子代理起草，plan-verifier 第 1 次 REVISE 七項逐項處置在 §10；§9 是維護者同日的決定，含 (A) 鄰鍵優先、(B) 正式寫法另開片）。**順序**：這一片接在模糊輸入（`docs/contracts/fuzzy-input.md`，分支 `feat/fuzzy-input`，PR #273 開著、領先 main 39 個 commit）**之後**：兩片都改 `finish_syllable`「詞庫沒有的音節」那一支，PR #273 先合併，本片從合併後的 main 開分支（§2.9、§8）。接在 `docs/contracts/zhen-mixed.md`（第一片）、`zhen-inline.md`（第二片）、`zhen-keyorder.md`（第三片，PR #311）之後；這一片**取代**第一片的英文窗與單字表觸發、第二片的顯示規則 D 與自動固定、第三片的證據計數與 `TWO_LETTER`，沒寫到的照那三份。標〔推論〕的是從程式讀出來、沒有在契約裡寫過的行為；標〔量過〕的是在 `data/lexicon/mcbpmf-data.txt` 查過的事實（只查基底檔，疊加層沒查，executor 實作前用 `lex.entries` 再核對一次）。

## 0. 起因與決定

- Threads 上的使用者回報（2026-10-27）：0.5.0 打 `ok` 變成「阿」、`ok`／`AI`／`PR` 這類兩個字母的詞打不出來、`gta6`／`rdr2`／`poe2` 打不出來。研究紀錄 2026-10-26「探索——唯音（vChewing）的中英混打與值得學的地方」已經量到今天的現況：`o k ␣` → 阿、`l s ␣` → 孬、`c u p ␣` 沒反應。
- 維護者 2026-10-27 的決定：
  1. **照唯音的做法「直接回退」**：每個鍵同時進原始按鍵軌道（今天的影子）與試組的注音；注音不可能是使用者要的時候，整段**直接**回退成原始按鍵（字母與數字照打的樣子）當英文，**不查英文單字表**。三種觸發都要：(a) 鍵覆寫同一欄已有的符號；(b) 鍵序不合注音欄位順序（第三片的逆序）；(c) 音節完成時（空白或聲調鍵）詞庫沒有這個音節。原本規劃的「字母＋數字詞表」不做，`gta6`、`rdr2`、`poe2`、`ok`、`ls` 都要靠回退打出來。
  2. **照欄位順序就是合法注音的短詞（`ai`、`ui`、`go`、`cp`…）永遠不回退**。核心內建一份小清單；原始按鍵正好是清單裡的字時，候選清單（↓）的**最後面**加上原樣小寫與全大寫兩項，不自動取代、第一個候選不變。
  3. 「依按鍵順序判斷英文」（第三片的開關，原預設關）改成**預設開**；維護者 2026-10-26 說過「打錯注音直接塞下個注音去覆蓋」算英文的部分**要單獨做開關**。
  4. **代價要先量**（用同一欄重打改錯的人，改錯會變成英文）：用現有的 `english-sim`，`--p 0.02` 的覆寫修正與 Backspace 修正、5 個種子、cvtune-clean／wikitune-clean／dev302／打字測驗，門檻事先寫好，不過就停下來把數字交給維護者。
- 從唯音學的只有「兩條軌道、每鍵判斷、不用單字表」；**不學**的照研究紀錄：唯音切出英文時把前面的中文一起送出，善解的英文留在組字區成為英文段，可以編輯（zhen-inline §1.3b 起）。唯音在空白鍵也會試「英文前綴＋注音後綴」的切分；本片不在空白鍵切（§2.4 說明原因）。

## 1. 名詞

- **欄位**：未完成音節的三欄，0 聲母、1 介音、2 韻母（`pend[col]`）；標準排列的鍵對欄位見 s3a §1（字母：聲母 `q a z w s x e d c r f v t g b y h n`、介音 `u j m`、韻母 `i k o l p`；數字：聲母 `1 2 5`、韻母 `8 9 0`；韻母另有 `, . ; / -`；聲調 `6 3 4 7`，空白是一聲）。
- **影子**（重定義，取代 zhen-mixed §2.2 的「最多 32 個鍵加覆寫次數」）：每一欄記下**現在放在那一欄的符號是哪個鍵打的**（`shadow_key: [Option<char>; 3]`，鍵帽字元、不含 Shift）。所以影子最多 3 個鍵、永遠和畫面上的欄位一致；覆寫次數、逆序次數、第 33 個鍵的規則都不再存在。
- **回退**：丟掉未完成音節，在游標處開一個**英文段**（zhen-inline §1.3b 的 run，同一個東西，同樣的粗底線與「空白：英文斷開」提示），內容是**影子的鍵照打的順序排成的字串（〔2026-10-30，`zhen-reapply.md`〕原本寫欄位順序；顛倒保護開著時兩者可能不同），後面接觸發的那個鍵**（(c) 在空白鍵觸發時不接空白；在聲調鍵觸發時接那個數字）。因為任何破壞欄位順序的鍵都當場回退，開關都開著時「影子照欄位順序」就等於「照打的順序」。
- **英文段的尾音節**：英文段結尾能組成注音的那一段：從最後一個字元往前取，每個字元都是標準排列的注音鍵（小寫字母或 `1 2 5 8 9 0 , . ; / -`；大寫字母、聲調數字 `3 4 6 7`、其他字元都停住），而且欄位嚴格遞增（往前看就是嚴格遞減），最多 3 個。這是從英文段的文字當場算出來的，不另存狀態，所以 Backspace 不會讓它和畫面分歧。例：`okji` 的尾音節是 `ji`（ㄨㄛ）；`gta` 是 `a`（ㄇ；`t` 和 `a` 同欄，停住）；`A123` 是空的（`3` 是聲調鍵）。
- **音節在詞庫裡**：`lex.entries(&[音節]).is_empty()` 為假，和 s3a 第 10 條、第三片同一個判斷。注音字母詞（「ㄅ」「ㄋ」這種詞條）會讓單一符號的音節「在詞庫裡」；〔量過〕基底詞庫這類詞條 42 筆全是單一符號（另有一筆兩音節的 `ㄎㄜ-ㄎㄜ`），所以 §2.4 要求尾音節至少兩個符號之後不會碰到它們。
- **英文 token**（放寬 zhen-inline §1.0 的定義）：讀音 `_punct_` ＋ 文字，文字非空、每個字元都在 `[A-Za-z0-9,.;/-]` 裡，而且不是單獨一個數字字母表字元（那是數字串 token，`digits.md` A.3.1）。〔推論〕現有的 token 沒有一個落在這個定義裡：單按標點是 `` [ ] \ ' = ` ``、Shift 標點與 Ctrl+\ 是全形、22a 是聲調符號、空白單位是 `" "`、數字串 token 正好一個字元。`learn::english_of`、`breaks_with_space`、`next_key` 的 `^>_punct_en`、寫法清單（`english_writings`：原樣、小寫、首字大寫、全大寫，去重；`gta6` → `gta6`、`Gta6`、`GTA6`；`3ds` → `3ds`、`3DS`）都照這個定義。

## 2. 規則（生效時；其他時候和今天逐位元相同）

### 2.1 生效條件與開關

- **回退生效**（取代 `shadow_on()`）：`set_english(1)`、`set_abbreviation(0)`、標準排列。**不再要求單字表**（§2.8：單字表退場）。倚天、可省略韻母、中英混打關著時影子不記、三種觸發都不發生、英文段的新規則（§2.4）也不生效，和今天逐位元相同。
- 三種觸發對三個開關（都是殼的 UserDefaults，每次建引擎都送；核心預設值寫在 §3）：

| 觸發 | 開關 | 殼的預設 |
|---|---|---|
| (a) 覆寫同一欄 | **新開關「重打同一個位置算英文」**（`set_english_overwrite`，鍵 `englishOverwrite`） | 開（§4 的量測過了才定；§9 第 1 題） |
| (b) 逆序 | 第三片的「依按鍵順序判斷英文」（`set_english_key_order`，鍵 `englishKeyOrder`）；〔2026-10-30〕另受「快打時鍵序顛倒不當英文」（`set_english_reversal_guard`，鍵 `englishReversalGuard`，預設開）管：它開著時 (b) 不發生（`zhen-reapply.md` §1.2） | **開**（維護者 2026-10-27；原本關） |
| (c) 音節完成時詞庫沒有這個音節——**空白鍵** | 中英混打總開關（`english`） | 開 |
| (c) ——**聲調鍵** | 「依按鍵順序判斷英文」 | 開 |

- 為什麼這樣分：(c) 在空白鍵零成本（今天第 10 條對這個空白什麼都不做；第三片 §2.1「沒有證據也開」在它的開關下做過同一件事，還多了單字表條件，本片因為零成本改放總開關）；(c) 在聲調鍵和 (b) 是同一類代價——「我打錯了一個符號、按到聲調才發現」的人今天只要 Backspace，之後要 Esc 重打——所以放同一個開關；(a) 是維護者點名要單獨開關的那一種。開關關著時那一種觸發不發生，鍵照今天處理（覆寫取代、逆序照放、音節不收）。
- 游標**不在尾端也生效**（放寬 zhen-mixed §2.1）：回退在游標處開英文段，左右兩邊的字照 zhen-inline §1.3h 分開解碼〔推論：`start_run` 在游標不在尾端時已經 `refresh`，英文段在中間的解碼切段已經有測試〕。
- 數字層（`digits.md`）開著串（`drun`）時，鍵先進數字層（A.3.4），**不經過本片**：串裡的覆寫與逆序由數字層的切分評分處理（沒有合法尾段就吐成數字 token、新鍵開新音節）。本片只管「沒有串、有一個未完成音節」的鍵，也就是 s3a 第 9、10 條的路徑。〔推論：`dispatch` 先看 `drun`，`digit_start` 只在沒有未完成音節時開串〕

### 2.2 影子

- 第 14 條開新音節、第 9 條放進欄位時，`shadow_key[col] = 鍵帽字元`；第 11 條 Backspace 刪掉顯示位置最後的符號時，那一欄的影子一起清掉（今天「影子丟最後一個記下的鍵」在欄位順序下就是這一欄）；`clear_pend` 清空三欄。
- 影子只在回退生效時記；不生效時三欄一律 `None`。

### 2.3 三種觸發（取代第 10e 條、英文窗、顯示規則 D、自動固定）

**有一個未完成音節、沒有數字串、沒有開著的英文段**時：

| 鍵 | 條件 | 行為 |
|---|---|---|
| 注音鍵（第 9 條） | 那一欄已有符號 | (a) 開著：**回退**，英文段 ＝ 影子（照打的順序；〔2026-10-30，`zhen-reapply.md`〕原本寫欄位順序）＋這個鍵，記住中文項（下一點）。關著：取代（今天） |
| 注音鍵 | 那一欄是空的，但更後面的欄已有符號 | (b) 開著：**回退**，同上。關著：放進欄位（今天） |
| 注音鍵 | 其他 | 放進欄位、記影子（今天） |
| 空白鍵（第 10 條之前） | 三欄加一聲不在詞庫，**而且鄰鍵（§2.9）沒有收下** | (c)：**回退**，英文段 ＝ 影子（照打的順序）；這個空白鍵接著照 §2.4 **結束英文段**（被吃掉），所以一次輸出就是一個英文 token。總開關開著就生效 |
| 聲調鍵 `3 4 6 7`（第 10 條之前） | 三欄加這個聲調不在詞庫，**而且鄰鍵（§2.9）沒有收下** | (c)：「依按鍵順序」開著：**回退**，英文段 ＝ 影子（照打的順序）＋這個數字，英文段留著開。關著：今天（已處理、不收，Backspace 可修） |
| 空白鍵、聲調鍵 | 音節在詞庫裡 | 第 10 條完成音節（今天）；§2.6 的短詞清單在這裡記 |

- **還原點與中文項**：回退時記下「今天的規則會有的三欄與它們的影子」當還原點（§2.4 的 Esc 用；(a)、(b) 是這個鍵照今天放進欄位之後的三欄，(c) 在聲調鍵是按聲調前的三欄），英文段改過（再打字或 Backspace）就丟掉。(a)、(b) 另外把「這個鍵照今天放進欄位之後的三欄加一聲」記成英文段的中文項（zhen-inline §1.3i 的 `run_zh`，同一個機制）：那個音節在詞庫裡時，英文段**沒改過字母就結束**（空白、標點、Enter…）變成的 token，↓ 的寫法清單最後多一個中文項（詞庫對那個一聲音節的第一個詞條，和今天自動固定的 token 相同，§1.3c）；英文段裡又打了字或按了 Backspace 就丟掉（§1.3i 的規則不變）。(c) 沒有中文項（音節不存在）。例：`o k`（ㄟ→ㄜ）的中文項是 ㄜ 的第一個詞條；`l s`（ㄋㄠ）是 孬；`r u s`（ㄋㄧ）〔量過〕ㄋㄧ 一聲不在詞庫，沒有中文項。
- 回退當下的輸出：`preedit` 是組字區加英文段的字母，`english_run_utf16_start/len` 指向英文段（殼畫粗底線、顯示「空白：英文斷開」，zhen-inline §1.3f，不改殼）。預測列：英文段開著時沒有預測列（今天的規則）。
- 回退不送出任何東西（英文留在組字區）、不學習；重排工作與預測列照「英文段開著」的既有規則；英文段在組字區中間時照 §1.3h 切開解碼。

### 2.4 英文段裡的鍵（修改 zhen-inline §1.3b 的「進行中」與「結束」；Shift 開的英文段和回退開的英文段**一視同仁**）

| 鍵 | 行為 |
|---|---|
| 英文字母（有沒有 Shift 都算） | 接在英文段後面，照打的大小寫（今天） |
| **注音鍵裡不是字母的那些**：`1 2 5 8 9 0 , . ; / -` | **接在英文段後面**（鍵帽字元原樣）。今天這些鍵會先結束英文段再開新音節或進數字層；改掉的原因是 `gta6`、`rdr2`、`poe2`、`win11`、`e.g` 要能一口氣打完 |
| **聲調鍵 `3 4 6 7`** | 算尾音節（§1）。**尾音節至少 2 個符號、而且尾音節加這個聲調在詞庫裡**：**切分**——尾音節前面的字元（非空時）照 §2.5 變成英文 token，尾音節照第 10 條成為一個完成的音節（讀音含聲調，`insert_token`），英文段關掉；token 成為第 128 個單位時送出的文字放在這個鍵的輸出的 `commit`（§1.3b 既有規則）。**否則**這個數字接在英文段後面 |
| 空白鍵 | 結束英文段（§2.5），空白被吃掉（今天；再一下才是空白單位，§1.3e）。**不切分** |
| Backspace | 刪英文段最後一個字元；刪光就關掉英文段（今天） |
| Esc | **回退開的英文段、回退之後還沒改過**（沒有再打字、沒有 Backspace）：拿掉英文段，把未完成音節**還原**成「今天的規則會有的欄位」並重設影子——(a)、(b) 是這個鍵照今天放進欄位之後的三欄（和中文項同一個音節，`r u s`＋Esc → ㄋㄧ，接著 `3` 就是「你」）；(c) 在聲調鍵是按聲調前的三欄（接著用 Backspace 改錯的符號）。還原之後照今天的規則繼續，再打下一個鍵才重新判斷回退。**其他情況**（Shift 開的英文段、回退之後改過的段）：照今天拿掉整段。**(c) 在空白鍵已經變成 token 的**沒有英文段、也沒有還原點，Esc 就是今天沒有未完成音節時的 Esc：清掉整個組字區，和任何英文 token 之後的 Esc 相同（〔2026-10-27 實作修訂〕原文寫「照今天拿掉整段」，容易讀成只拿掉那個 token；實作與測試 `esc_after_a_space_token_is_esc_after_any_token` 照現狀，所以設定說明不承諾這一種能 Esc 變回，要用 Backspace 修）（維護者 2026-10-27 §9 第 4 題選還原） |
| 標點（第 2 條）、Enter、Shift+Enter、Tab、方向鍵、Home／End、Delete、其他 | 先結束英文段（§2.5），再照原本的規則處理這個鍵（今天） |

- **為什麼聲調鍵切、空白鍵不切，而且要兩個符號**：
  - 空白鍵是最常見的「英文結束」：〔量過〕`feel`、`real`、`keep`、`final`、`intel`、`email`、`model`、`hotel`、`social` 的最後兩個鍵分別是 ㄍㄠ 高、ㄇㄠ 貓、ㄍㄣ 跟、ㄇㄠ、ㄍㄠ、ㄇㄛ 摸、ㄍㄠ、ㄍㄠ、ㄇㄠ，都是在詞庫裡的一聲音節；空白鍵若切，這些字全部斷成「英文＋一個漢字」。代價是英文後面接一聲的字（`ok他`）要多按一下空白（`ok ␣ w 8 ␣`），和今天 Shift 英文段的規則相同。唯音在空白鍵會切，本片不學。
  - 聲調鍵後面是英文的情形少（`gta6`、`A123456789`、`ps4`），而 `ok我`（`okji3`）、`ok吧`（`ok187`，ㄅㄚ˙）、`rust編譯`（`rust1u03`）切得出來，是今天「自動固定」的替代。
  - 單一符號不切：〔量過〕ㄓˊ 直、ㄔ 吃、ㄕˋ 是 都在詞庫裡，不設下限的話 `A12345 6` 會切成 `A1234`＋直、`gta6` 只因 ㄇˊ 不在詞庫才倖免。代價是 `ok是`（`okg4`）要先按空白。
- 數字層在英文段開著時不介入（今天就是：英文段先看鍵）；所以 `iPhone15` 是一個 token、`Windows 11`（空白兩下再打數字）照舊走數字層。

### 2.5 英文段的結束與英文 token

- 結束＝照 zhen-inline §1.0 用 `insert_token` 插入英文 token：讀音 `_punct_` ＋ 字元原樣、固定詞就是那串字；§1.3b 的 128 單位規則、§1.3e 空白單位、§1.3f 欄位歸零、§1.3g Backspace 逐字母刪、§1.0 的 ↓ 寫法清單與中文項（§2.3）全部照舊。所以 `rust ␣` 之後的狀態和今天「英文窗選第 1 項」逐欄相同（沒有英文窗那一步）。
- 第 32 個字元之後的第 33 個：先結束前一段再開新的一段（今天），字元不限字母。
- **留給正式寫法（維護者決定 (B)，§7、§9 第 8 題）的接口**：英文段變成 token 的地方只有一個（`end_run` → `insert_english` → `put_english`），token 的文字（固定詞）與讀音分開存，↓ 清單第一項是「token 目前的文字」（`english_writings` 的「原樣在前」）。之後的 zhen-words 片在這一處查補充表 `data/english-extra.txt`（鍵＝小寫），把 token 的文字換成正式寫法（`chatgpt` → `ChatGPT`）、照打的寫法留在清單裡；本片**不**在這裡查任何表、不改這三個函式的分工、不動那個資料檔（它在分支 `feat/zhen-words`，main 上沒有）。

### 2.6 短詞清單：候選清單加英文（維護者決定 2）

- 核心常數 `ENGLISH_SHORT: &[&str]`（取代 `TWO_LETTER`），每一個都是**照欄位順序能打出來、而且一聲（或 `mp3` 的三聲）音節在詞庫裡**的字串——這些字三種觸發都不會發生，所以要另給一條路。起始內容（〔量過〕基底詞庫；executor 用 `lex.entries` 逐個核對，不符就刪掉並寫進測試註解）：

| 字 | 鍵 → 注音 | 音節 | 第一個詞條（檔案順序，實際以 `lex.entries` 為準） |
|---|---|---|---|
| `ai` | a ㄇ、i ㄛ | ㄇㄛ | 摸 |
| `ui` | u ㄧ、i ㄛ | ㄧㄛ | 唷 |
| `up` | u ㄧ、p ㄣ | ㄧㄣ | 因 |
| `co` | c ㄏ、o ㄟ | ㄏㄟ | 黑 |
| `hp` | h ㄘ、p ㄣ | ㄘㄣ | 參 |
| `jp` | j ㄨ、p ㄣ | ㄨㄣ | 溫 |
| `mp` | m ㄩ、p ㄣ | ㄩㄣ | 暈 |
| `mp3` | m ㄩ、p ㄣ、3 ˇ | ㄩㄣˇ | 允 |
| `np` | n ㄙ、p ㄣ | ㄙㄣ | 森 |
| `rm` | r ㄐ、m ㄩ | ㄐㄩ | 居 |
| `xl` | x ㄌ、l ㄠ | ㄌㄠ | 撈 |

- **不放**清單、靠 (c) 就打得出來的（〔量過〕一聲音節不在詞庫）：`go`（ㄕㄟ）、`cp`（ㄏㄣ）、`so`（ㄋㄟ）、`to`（ㄔㄟ）、`do`（ㄎㄟ）、`hi`（ㄘㄛ）、`am`（ㄇㄩ）、`zi`（ㄈㄛ）、`cup`（ㄏㄧㄣ）。維護者點名的 `go`、`cp` 屬於這一類：空白鍵時直接成為英文 token。
- 靠 (a)／(b) 打得出來、不放清單的：`ok ip as at an be by he we tv vs hr qa db ts cd`（覆寫：兩個鍵同欄）、`if in is it me my of on or us ux pc id os pm js py pr ls mv`（逆序：後一個鍵的欄位在前一個之前）；第三片的 `TWO_LETTER` 19 個（ok ls cd mv ip id ux js ts py vs pr os tv pc db qa pm hr）全在這兩類裡。
- **記錄**：第 10 條完成音節時（空白或聲調），回退生效、影子的鍵照打的順序接上聲調數字（空白不接）正好等於清單裡的一個字，就記 `last_short = Some((i, 字))`，`i` 是那個音節插入後在 `syls` 裡的位置（和 `digits.md` A.3.6 的 `last_digits` 同一種狀態：下一個**改狀態的鍵**清掉——注音鍵、Backspace、方向鍵、Esc、送出、`reset`、設定函式都算；什麼都不改的鍵（第 1 條的直通、↑、不起作用的聲調鍵）不清）。〔2026-10-27 實作修訂，code-review 兩輪〕(1)「什麼都不改」指整個鍵什麼都沒改：先結束英文段或數字緩衝（那已經是改變）再重送自己、內層才不做事的鍵不算；實作是 `Engine::nothing`／`mark_noop` 只在最外層 `dispatch` 生效，`key()` 一處放回。(2)「設定函式都算」只指會重算組字區、改變按鍵規則或候選清單的設定（`set_lm`、`set_rerank`、`set_rerank_level`、`set_demote`、`set_prediction`、`set_candidate_vertical`、`set_abbreviation`、`set_fuzzy`、`set_profile`、`set_learn_guard`、`set_reading_channel`、`set_english`、`set_english_overwrite`、`set_english_key_order`，一律呼叫 `Engine::setting_changed`）。**例外，不清**：`set_learning`、`set_left_context`（殼在每個按鍵之前都呼叫，清了就沒有標記可用）、`set_punctuation`、`set_newword`、`set_newword_subspans`、`set_scan_gate`、`set_eps_global`、`set_today`、`set_english_next`（只改表或學習旋鈕，不改組字區也不改短詞項目），以及所有 `load_*`、`learning_open`、`learning_clear`（引擎建立時、第一個鍵之前呼叫，不重算組字區）。
- **加項目的條件只有兩個**，和前面有幾個音節、組字區多長無關：`last_short` 是 `Some((i, 字))`，而且開窗那一刻 `cursor == i + 1`。第 15 條的 ↓ 或空白開的候選窗（含 B.3.1 前面放預測列項目的情形）在清單**最後面**加兩項：那個字的小寫、全大寫（`ai`、`AI`；`mp3`、`MP3`），用「索引在清單哪一段」判斷（A.3.6 的做法，`dalt` 多一種 `Short { i, word }`）。候選窗的範圍跨幾個音節（例如「要」＋ㄇㄛ 列出兩個音節的詞）不影響這兩項：它們永遠只對應位置 `i` 那**一個**音節。
- **選了**：只拿掉位置 `i` 那個音節（一個單位），在原位插入英文 token（讀音 `_punct_` ＋ 選的那一項、固定詞同），前面的音節與固定詞不動，之後和任何英文 token 一樣（↓ 寫法清單、Backspace 逐字母）；不學。選其他候選照今天。第一個候選、其他候選、預測列項目都不變。
- 條件不成立（`last_short` 是 `None`、游標不在 `i + 1`）時清單一個項目都不加。

### 2.7 作廢、改寫與保留的規則

**作廢**（程式一起刪；對應的測試刪或改）：

- 第 10e 條、英文窗（`Cands.english`、`open_english`、`choose_english`、英文窗的按鍵表、⌘⌫ 的英文窗分支）、觸發函式（`english_trigger`、`overwrite_enough`、`evidence_enough`、`eligible_shadow`、`shadow_candidate`、`evidence`）、`shadow_over`／`shadow_inv`／`SHADOW_MAX` 對影子的用途（英文段的 32 字元上限留著）。
- 顯示規則 D（zhen-inline §1.1、§1.2、`english_inline_shown`）、「打字時就顯示／按空白才顯示」選項（§1.4、`set_english_inline`、鍵 `englishInline`）：回退當下字母就在英文段裡，沒有「先顯示注音、空白才換」的狀態可以保留；§9 第 2 題。
- 自動固定（§1.3 表的注音鍵列、`autokeep_word`、`insert_english(auto)`、`english_autokept`）、§1.3h 對「D 成立時按標點」與「英文窗開著時」的三條、§1.3i（自動固定的 token 刪光新音節時變回英文段）：沒有自動固定就沒有這些狀態。§1.3i 的 `run_zh` 機制改給 §2.3 的中文項用。
- 第三片的證據 E、`TWO_LETTER`、兩字母只在空白鍵開窗、「沒有證據也開」；`english-sim` 的 `two` 行、`probe` 行、四個覆寫分類。
- 單字表（§2.8）。

**保留**：Shift＋字母開英文段（§1.3b）與英文段的所有既有行為（除 §2.4 改的鍵）；英文 token、寫法清單、`^>_punct_en`、空白單位（§1.3e）、粗底線與提示（§1.3f）、Backspace 逐字母刪（§1.3g，兩點都留：字母顯示中那一點改成「英文段開著時」）、英文段在中間的切段解碼（§1.3h 第四點）；數字層全部；倚天與可省略韻母的互斥。

### 2.8 單字表退場（分兩個 PR；PR 1 讓引擎不再讀它，PR 2 才拆掉載入與打包）

- **PR 1**（本片的行為）：引擎不再讀單字表——`shadow_on` 不看 `english_words`，D、觸發、自動固定連同 `is_word`、`has_prefix` 一起刪（它們沒有別的呼叫者）。**但 `Engine::load_english`、`english_words` 欄位、`ENGLISH_MAX_*`、`shanjie_engine_load_english`、它的 FFI 子行程測試與 `abi_smoke.c` 那一段、殼的 `loadEnglish`／`englishAvailable`／`englishFile`、`Selftest.swift` 的載入步驟、`build-app.sh` 的下載與打包、`check-app.sh` 的檔案檢查都照舊留著**（載入仍驗證格式、仍存起來，只是沒有人讀）。PR 1 只拿掉 `set_english_inline`（§3）。`Selftest.swift` 的**預期**在 PR 1 改（`rust ␣` 不再開英文窗，見 §5.2），載入那一步留到 PR 2。
- **PR 2**（純刪除，`/code-review`＋fresh verifier＋CI 照「程式」級關卡）要改的每一處（2026-10-27 用 `grep -rn -E "english-words|english_words|loadEnglish|load_english|englishAvailable|englishFile|english-tiny|build_english_words|SCOWL|SymSpell|english-words-attribution"` 列出，行號是 main `ab70d3da`）：

| 檔 | 要改的 |
|---|---|
| `core/src/engine.rs` | `load_english`、`english_words`、`ENGLISH_MAX_BYTES`／`ENGLISH_MAX_LINES`／`ENGLISH_LEN`、載入時的排序與重複檢查刪掉 |
| `core/src/ffi.rs`（17 處） | `shanjie_engine_load_english` 與它的子行程測試（碼 1／2／3、保留舊表）刪掉 |
| `core/src/tinylm.rs:301` | 註解裡「`Engine::load_english`, with its own cap」改寫 |
| `core/tests/c/abi_smoke.c`（4 處） | `load_english` 的那一段刪掉 |
| `core/tests/engine_english.rs`（16 處）、`engine_english_next.rs`（55、259）、`engine_rerank.rs`（720–723）、`tri_tiny.rs`（324、418）、`engine_digits.rs`（45、522）、`engine_digits_off.rs`（23、41）、`engine_predict.rs`（1068、1111）、`engine_replay.rs`（52） | 拿掉 `load_english(eval/golden/english-tiny.txt)` 的呼叫（PR 1 之後它們對行為沒有影響，fixture `digits-off*.txt` 不變） |
| `cli/src/english.rs`（5 處）、`cli/src/english_next.rs:93` | 打字模式不再 `load_english`；`--words FILE` 只給 `--recall` 當輸入清單（直接讀檔，不經引擎）；`english_next.rs` 的 `load_english` 呼叫刪掉 |
| `cli/tests/english_sim.rs`（7 處） | 跟著 CLI 改；`eval/golden/english-tiny.txt` **留著**當 `--recall` 的輸入 |
| `macos/Sources/ShanjieKit/Core.swift:143` | `loadEnglish` 刪掉 |
| `macos/Sources/ShanjieKit/Shell.swift`（405–407、442、475、910、950–952） | `englishAvailable`、`englishFile`、`build()` 的載入刪掉；`englishEnabled` 只剩 `mode == .standard` |
| `macos/Sources/ShanjieKit/Selftest.swift`（8、55–58、111–113） | 載入步驤刪掉（`rust ␣` 的新預期在 PR 1 已改） |
| `macos/Tests/ShanjieKitTests/ShellTests.swift`（882–906）、`SettingsTests.swift`（96、115、148、179）、`SelftestTests.swift`（75、83、90）、`Support.swift`（27–28） | 「缺表／拒絕時反灰」的測試刪掉，`TestData.files` 拿掉 `english-words.txt` 那一列 |
| `scripts/build-app.sh`（80–81、85–105、224、255、338–347） | 下載、產生、雜湊核對、`cp english-words.txt`、`LICENSES/SCOWL.txt`／`SymSpell-MIT.txt` 的複製、`english-words-attribution.txt` 的產生全部刪掉 |
| `scripts/check-app.sh`（80、125–134） | 必要檔清單拿掉 `english-words.txt`、`LICENSES/SCOWL.txt`、`LICENSES/SymSpell-MIT.txt`、`LICENSES/english-words-attribution.txt`；內容格式、三方署名、`data/english-words.txt.sha256` 的核對三段刪掉 |
| `scripts/test-sandbox-mutations.sh`（150、159） | 第 18（mode 700）、21（g+w）案改用別的資源檔（`Contents/Resources/digits.tsv`）；表頭說明不變 |
| `.github/workflows/ci.yml`（369–375）、`release.yml`（116–122） | 「Cache the English word list input」兩段刪掉；`ci.yml` 267–269 與 `Makefile:52` 的 `tools/test_build_english_words.py` **留著**（工具留著） |
| `data/english-words.txt.sha256`、`LICENSES/SCOWL.txt`、`LICENSES/SymSpell-MIT.txt` | 刪檔 |
| `LICENSES/data.md:52`、`docs/data-files.md:30` | 那一列刪掉 |
| `docs/contracts/s3b.md`（§13.2 的開關說明與 check-app 一段）、`s3a.md`（§5 資料載入或 §6 的一句）、`zhen-mixed.md`／`zhen-inline.md`／`zhen-keyorder.md` 的狀態列、`docs/PLAN.md` | 改成「單字表已退場（本片 §2.8）」；`derisk-1015.md`、研究紀錄是歷史紀錄不改 |
| `site/index.html:434` | 授權那一句拿掉（部署另問維護者） |

- **留著不刪**：`tools/build_english_words.py` 與 `tools/test_build_english_words.py`（產生 `english-sim --recall` 的輸入，不進 App）、`eval/golden/english-tiny.txt`（CLI 測試的輸入）。zhen-words 片之後只用補充表，不用 SymSpell，所以這裡刪掉下載不影響它。
- **PR 2 的驗收 grep**：合併後 `grep -rn -E "english-words|english_words|loadEnglish|load_english|englishAvailable|englishFile|english-words-attribution|SCOWL|SymSpell" --exclude-dir=target .` 的命中只剩：`tools/build_english_words.py`、`tools/test_build_english_words.py`、`Makefile:52`、`.github/workflows/ci.yml` 267–269、`cli/src/english.rs` 與 `cli/tests/english_sim.rs`（`--words` 的召回輸入）、`docs/research-log.md`、`docs/contracts/zhen-mixed.md`／`zhen-inline.md`／`zhen-keyorder.md`／`derisk-1015.md`（歷史）、`docs/contracts/zhen-revert.md`（本片）。其他任何命中都是漏改。`scripts/build-app.sh` 建出的 App 跑 `scripts/check-app.sh` 通過、`scripts/test-sandbox-mutations.sh` 24 案全過。

### 2.9 與模糊輸入的鄰鍵（維護者決定 (A)，2026-10-27）

- 模糊輸入（`fuzzy-input.md` §1、§2.1；`set_fuzzy` 位元 4「鄰鍵打錯」，殼鍵 `fuzzyFlags`，**預設關**）在 `finish_syllable` 對「詞庫沒有的音節」已經有一條例外：鄰鍵開著、語言模型已載入、標準排列、而且把一個注音鍵換成同一列左右的鄰鍵能得到詞庫有的音節時，**收下**這個音節（`syls` 存打的讀音，解碼用替代讀音、分數減 δ）。
- **順序寫死**：第 10 條遇到詞庫沒有的音節時，**先問鄰鍵**；鄰鍵收下就是中文，(c) 不發生、影子照 `clear_pend` 清掉、不記 `last_short`（替代讀音不是清單的字）；鄰鍵**沒有收下**（開關關、沒有模型、倚天、或沒有任何替代讀音）才輪到 (c)。所以鄰鍵開著時 (c) 只剩「連鄰鍵都救不回的音節」。
- 後果（寫進鄰鍵的設定說明一句）：鄰鍵開著時 `go`（ㄕㄟ → o→p 得 ㄕㄣ 身）、`cup`（ㄏㄧㄣ → c→v 得 ㄒㄧㄣ 心）、`cp`（ㄏㄣ → p→o 得 ㄏㄟ 黑）會被收成中文，不會變英文；要英文就用 Shift 或把鄰鍵關掉。鄰鍵預設關，所以預設的行為是 §2.3 的表。〔推論：三個替代讀音照 `fuzzy-research.md` §2 的鍵盤定義推，executor 用鄰鍵表核對；不符就換測試用的字〕
- (a)、(b) 和鄰鍵無關（鄰鍵只在完成音節時看）。
- **s3a §2「詞庫沒有的音節不收」**那一句在 PR #273 已加鄰鍵的例外；本片在它後面再加一句：「鄰鍵沒有收下、回退生效時，照 `zhen-revert.md` §2.3 (c) 回退」。
- **分支順序**：PR #273（`feat/fuzzy-input`）先合併；本片的分支從合併後的 main 開（兩片都改 `finish_syllable` 這一支與 s3a §2 同一句）。若 PR #273 合併前本片已經開始，本片 rebase，不反過來。

## 3. 設定與介面

- **核心**：`Engine::set_english_overwrite(bool) -> Result<Output, EngineError>`，預設 **true**（`Engine::new`）；`set_english_key_order` 預設改成 **true**（原本 false；核心與殼同時改，不像第一片把預設留在核心外，§9 第 1 題）；`set_english_inline` 刪掉（§9 第 2 題）；`load_english` 在 PR 1 **留著**（§2.8）。切換這兩個開關時沒有窗要關、英文段是文字不受影響，只記開關、回快照。`#[doc(hidden)]` 工具鉤子：`english_reverted() -> Option<Revert>`（`Revert::{Overwrite, Order, Missing}`，最近一次 `key()` 是否回退、哪一種；每次 `key()` 開頭清掉，和 `english_autokept` 同一種做法）、`english_split() -> bool`（最近一次 `key()` 是否切分）、`english_shadow() -> String`（影子照欄位順序）、`english_columns` 留著；`english_open`、`english_inline_shown`、`english_autokept`、`english_evidence`、`english_window_shadow`、`english_shadow_len` 刪掉。`pub const ENGLISH_SHORT`。
- **C ABI**（`core/include/shanjie.h`，s3a §6 同步）：新增 `shanjie_engine_set_english_overwrite(engine, uint32_t enabled, ShanjieOutput **out)`，回傳碼與快照照 `set_english_key_order`（0；NULL 回 1；0、1 以外回 2、狀態不變）。PR 1 只刪 `shanjie_engine_set_english_inline`（`shanjie_engine_load_english` 到 PR 2 才刪，§2.8）。`set_english`、`set_english_key_order` 的註解改寫成本片的行為（三種觸發、英文段、短詞清單、鄰鍵優先）。`abi_smoke.c` 跟著改。
- **殼**：UserDefaults 鍵 `englishOverwrite`（沒有值當 true）、`englishKeyOrder`（沒有值改當 **true**；存過 false 的仍是 false）；兩個都在每次建引擎時送（放在 `setEnglishKeyOrder` 旁邊）。`loadEnglish`、`englishAvailable` 在 PR 1 不動（§2.8）。設定視窗「中英混打（實驗）」底下：拿掉「英文的顯示」那一列（`englishInline` 的 store 與 `setEnglishInline` 呼叫一起刪）；「依按鍵順序判斷英文」留著、預設打勾，說明改成「`ls`、`pr`、`a4` 這類字不用按 Shift。開了之後，注音鍵沒照聲母、介音、韻母的順序，或打了聲調鍵才發現音節不存在，整段會變成英文（馬上按 Esc 變回注音）。」；在它之後加勾選「重打同一個位置算英文」，預設打勾，說明「`ok`、`rust`、`gta6` 這類字要靠它。開了之後，用重打同一個位置來改錯的注音也會變成英文（馬上按 Esc 變回注音）。」。反灰條件和「依按鍵順序判斷英文」相同。中英混打的說明那一行改成「打英文不用切換輸入法：打的字不可能是注音時直接變成英文，留在組字區；按空白才發現音節不存在（例如 so）也一樣，那一種要用 Backspace 修，不能按 Esc 變回注音；數字列的鍵也會打出數字（例如 2026年、3.14）。」。〔2026-10-27 實作修訂：兩句說明照 code-review 改成這樣。原寫法「或打出詞庫沒有的音節」連空白鍵的 (c) 也算，但空白鍵的 (c) 做出來的是完成的 token、沒有還原點，Esc 不會變回注音；例子不用 `cup`，因為鄰鍵打錯開著時 `cup` 會被收成中文（§2.9），`so` 鄰鍵救不回來。〕選單不加項目。
- **文件同 PR 改**：`docs/contracts/s3a.md`（§3.1 的表）、`core/include/shanjie.h`、`docs/contracts/zhen-mixed.md`／`zhen-inline.md`／`zhen-keyorder.md` 的狀態列指到這份並寫明哪些節被取代、`docs/contracts/digits.md` A.3.4「中英混打的影子」那一點（改成「字母表鍵在未完成音節裡和其他注音鍵一樣記影子；英文段開著時字母表鍵接在英文段後面」）、`docs/contracts/settings-window.md` §2.2、`docs/contracts/s3b.md` §13.2 與 §13.2 的 check-app 一段、`docs/PLAN.md` 的中英混打一項、README 的功能一句、研究紀錄（決定、量測表、數字、雜湊）、`docs/methodology.md`（若有教訓）。網站 `site/` 的說明與授權句另問維護者再部署。

### 3.1 s3a 規則表要改的列（executor 同一個 PR）

| 位置 | 改法 |
|---|---|
| §2「影子」一段 | 重寫成 §1、§2.2 的定義（每欄一鍵；沒有次數、沒有第 33 個鍵）；「影子現在決定顯示與送出的內容」與 D 的句子刪掉 |
| §2「英文 token 與顯示規則 D」一段 | 拿掉 D；英文 token 的定義換成 §1 的放寬版；中文項改成「回退做出來的英文段」 |
| 第 9 條 | 「自動固定」例外刪掉，換成 §2.3 的兩列（覆寫→回退 (a)、逆序→回退 (b)，各註明開關） |
| 第 10e 條 | 刪掉 |
| §2「詞庫沒有的音節不收」 | 鄰鍵的例外（PR #273）之後加一句：鄰鍵沒有收下、回退生效時照本片 §2.3 (c) 回退（§2.9） |
| 第 10 條 | 加 (c)：空白或聲調鍵、音節不在詞庫、鄰鍵沒有收下、回退生效（聲調鍵另看開關）→ 回退，空白鍵接著結束英文段；音節在詞庫裡時記 `last_short`（§2.6） |
| 第 11 條 | 刪掉 D 的例外與修訂六那一句；加「刪掉的那一欄的影子一起清」 |
| 第 12a 條 | 刪掉「顯示規則 D 成立時是影子的字母」（英文段開著時 Enter 走英文段的規則，表外那一段已寫） |
| 第 15 條 | 加一句：`last_short` 記著位置 `i`、游標在 `i + 1` 時，清單最後加那個字的小寫與全大寫兩項，選了只換位置 `i` 那個音節（§2.6） |
| 英文窗小節（第 10e 條開的候選窗與它的按鍵表） | 整段刪掉 |
| 表外「自動固定」一點 | 刪掉 |
| 表外「英文 token 改選」 | 中文項的來源改成「(a)、(b) 回退開的英文段沒改過字就結束的 token」 |
| 表外「英文段」 | 加 §2.4：不是字母的注音鍵接在段後、聲調鍵的切分規則（尾音節 ≥ 2 符號、在詞庫）、空白不切；開段的方式多一種「回退」 |
| §3.0 數字列（A.3.4 的摘要） | 「英文段開著時字母表鍵先結束段再處理」改成「接在英文段後面」 |
| §6 ABI | PR 1：新函式 `set_english_overwrite`、刪 `set_english_inline`、兩段註解；PR 2：刪 `load_english` |
| §7 測試 | 加 `engine_english.rs` 的新測試群與窮舉測試一句 |

## 4. 量測（main，GCP 或 188，不在 Mac；每一步帶滿核心：`english-sim` 單程序時用 `xargs -P` 把 4 個集合 × 4 個臂同時跑，開跑 5 分鐘內看 `uptime`）

### 4.1 工具：`english-sim` 改版

- 選項：`--overwrite on|off`（預設 on，對應 `set_english_overwrite`）、`--key-order on|off`（改成有值；預設 on）；`--inline` 刪掉；`--digits`、`--p`、`--repair`、`--seed`、`--repeat`、`--rows` 照舊。〔2026-10-28 PR 2：`--words FILE` 只給 `--recall`（§2.8），打字模式不收；和 `--rows` 一起給會被拒絕。〕
- 每個鍵之後問引擎 `english_reverted()`：有就依種類計一次，然後**復原**：Esc（還原成注音，§2.4）、再一次 Esc（第 12 條清掉未完成音節；組字區回到音節開始前的樣子），再把這個音節**不含錯打**地從頭重打；重打又回退就再計、再復原，三次後中止那一列並記到 stderr（和今天自動固定的復原同一套）。每個鍵之後也問 `english_split()`，中文列裡任何切分都算一次 `split`（只會在復原失敗時出現，預期 0）。列尾 Enter 之後，送出的文字裡若有 ASCII 字母或英文 token 殘留，計一次 `english_final`。
- 輸出一行 TSV：`rows_sha256 rows syllables keys typos revert_a_mean revert_a_max revert_b_mean revert_b_max revert_c_mean revert_c_max split_mean split_max english_final_mean english_final_max`；`typos` 是 5 個種子注入的錯打總數（新欄，用來算比例）；`*_mean`／`*_max` 是每千音節、跨種子。`--digits` 的六欄接在後面不變。`cli/tests/english_sim.rs` 的固定種子數字跟著改；`inline_is_for_the_typing_mode`、`autokeep_columns`、`key_order_*` 兩條改寫成新欄位的測試。
- **召回模式**（`--recall --words FILE`，只報）：每個詞新引擎、逐字母 `Key::ch`、空白；分類：回退 (a)／(b)／(c)、沒有回退（成為中文音節）；`opened` ＝ 組字區正好是一個英文 token 等於這個詞。另對 `ok ls cd mv pr ai ui go cp cup npm gta6 rdr2 poe2 mp3 a4 i7 s7` 各印一行 `probe <詞> <結果：token／中文／其他>`（詞是公開的固定清單，可以印）。
- 錯打模型不改（zhen-mixed §4.2）。**它看不到 (c) 在聲調鍵的代價**：模擬器在錯鍵之後立刻修正，聲調鍵到的時候欄位已經是對的，所以 (c) 在聲調鍵永遠不會發生；這一種觸發的代價本片**量不到**，§9 第 3 題。

### 4.2 集合、臂、門檻

- 集合：cvtune-clean、wikitune-clean（選門檻用；`tools/bench.py` 現行的調參集，檔案與雜湊照工具印出的寫進研究紀錄）；dev302、typing76（驗證）。全部 `--repeat 5 --seed 20261014`。
- 臂（三個開關的組合，數字層照出貨開著，模糊輸入照出貨 `fuzzy 0`——鄰鍵預設關，開著時 (c) 只會更少，不另設臂）：A 只開總開關（`--overwrite off --key-order off`：只有 (c) 空白）；B 加「依按鍵順序」；C 加「重打同一個位置」；D 全開（提議的預設）。每個臂跑 `--p 0`、`--p 0.02 --repair backspace`、`--p 0.02 --repair overwrite`。
- **門檻（事先寫好）**：
  1. `--p 0`（照順序、不打錯）：四個集合、四個臂，`revert_*`、`split`、`english_final` **全部 0**。硬門檻；§4.3 的窮舉測試從結構上保證它。
  2. `--p 0.02 --repair backspace`（用 Backspace 改錯的人）：臂 D 在 cvtune-clean 與 wikitune-clean 的 **回退總數 ÷ typos ≤ 0.25**（也報每千音節）。理由：一次回退在模擬器裡是兩次 Esc 再重打這個音節（≤ 5 鍵；真人多半是 Esc 還原後 Backspace 改一個符號，更少），大約是一次 Backspace 修正（2 鍵）的兩倍；比例 ≤ 1/4 時，這個功能讓 Backspace 改錯的人多付的鍵數不超過他原本改錯成本的一半。臂 B、C 各自的比例一起報，用來看哪一個開關貢獻多少。dev302、typing76 同一條門檻當驗證。〔推論〕從鍵盤幾何估：鄰鍵錯打落到別欄而且被當成覆寫或逆序的，大約是介音錯打的一半（`u`↔`y`、`j`↔`h`、`m`↔`n`）加上有介音時韻母 `k`↔`j`、`i`↔`u` 的錯打，約每千音節 9 次、約占錯打的 20%；這只是估計，以量到的為準。
  3. `--p 0.02 --repair overwrite`：**只報**。這就是開關說明寫的代價：覆寫改錯的人每一次同欄改錯都回退（臂 C、D），不可能低。
  4. `english_final`、`split`：三種設定都要是 0（復原應該把英文都拿掉）；不是 0 就是工具或引擎的錯，先修。
- **不過時**：門檻 1 不過＝引擎或工具有錯，修了重量，不是調門檻。門檻 2 不過＝**停下來**，把四個臂 × 四個集合的表（每千音節與比例）交給維護者，選項照 §9 第 1 題（預設關掉貢獻最多的那個開關、放寬門檻、或縮小觸發）；看過數字再改門檻這一片就算作廢，重開。

### 4.3 窮舉測試（第三片 §4 的 `key_order_clean_typing_of_every_reading_is_never_english` 擴充，進 `make test-rust`）

- 詞庫每一個讀音（每個音節在 `lex` 的讀音表裡），照欄位順序乾淨地打、每個音節以聲調鍵或空白收尾，三個開關全開、數字層關（沒有模型的引擎）：**一次都不回退、不切分**（`english_reverted()` 一律 `None`、`english_split()` 一律 false），而且最後的 `syls` 和同一組鍵打進 `set_english(0)` 的引擎逐項相同。跑超過 60 秒就改抽樣（固定種子、至少 20,000 個讀音），寫進測試註解。
- 另一個窮舉：`ENGLISH_SHORT` 每個字逐鍵打、收尾（`mp3` 用 `3`，其餘空白）：不回退、`syls` 是對應的音節、↓ 清單最後兩項是小寫與全大寫。

## 5. 驗收

### 5.1 引擎測試（`core/tests/engine_english.rs`；真實詞庫、標準排列、`set_english(1)`、三個開關預設；每一條至少一個會讓它失敗的突變，看結束碼與斷言數；`␣` 空白、`⏎` Enter）

| 名稱 | 按鍵 | 預期 |
|---|---|---|
| `overwrite_reverts_to_the_keys` | `o k` | `k` 後英文段 `ok`（`english_run_utf16_len` 2）、沒有未完成音節、`english_reverted() == Some(Overwrite)`；`␣` 後組字區是英文 token `ok`、`preedit == "ok"`、沒有候選；↓ 清單是 `ok`、`Ok`、`OK`、ㄜ 的第一個詞條（4 項） |
| `order_reverts_to_the_keys` | `l s ␣`；`p r ␣` | token `ls`（↓ 第 4 項是 ㄋㄠ 的第一個詞條）、token `pr` |
| `missing_syllable_on_space_is_one_token` | `c u p ␣`；`g o ␣`；`c p ␣` | 各一個 token，`preedit` 等於那串字，沒有候選；↓ 只有三種寫法（沒有中文項） |
| `missing_syllable_on_tone_opens_a_run` | `s 7`；`a 4`；`i 7` | 英文段 `s7`／`a4`／`i7`；`␣` 後是 token |
| `missing_syllable_on_tone_needs_key_order` | `set_english_key_order(0)`，`s 7` | 沒有英文段、pend 仍是 ㄋ（今天） |
| `letters_and_digits_go_on_the_run` | `g t a 6 ␣`；`r d r 2 ␣`；`p o e 2 ␣`；`w i n 1 1 ␣`；`e . g ␣` | token `gta6`、`rdr2`、`poe2`、`win11`、`e.g`；每個 `preedit` 等於那串字 |
| `tone_splits_a_two_symbol_tail` | `o k j i 3`；`o k 1 8 7`；`r u s t 1 u 0 3 u 4` | `syls == ["_punct_ok", "ㄨㄛˇ"]`；`["_punct_ok", "ㄅㄚ˙"]`；`["_punct_rust", "ㄅㄧㄢˇ", "ㄧˋ"]`；`english_split()` 在切分那個鍵為 true |
| `tone_does_not_split_one_symbol` | Shift+A `1 2 3 4 5 6 7 8 9 ⏎`；`r u s t 3 ␣`；`o k g 4 ␣` | 送出 `A123456789`（digits.md #27 不變）；token `rust3`；token `okg4` |
| `tone_split_needs_the_lexicon` | `o k g o 4` | 尾音節是 `go`（ㄕㄟ，2 個符號、欄位 0→2），ㄕㄟˋ 不在詞庫（〔量過〕基底檔與 `data/lexicon/` 全部疊加層都沒有 ㄕㄟˋ，也沒有一聲 ㄕㄟ）→ **不切**：英文段 `okgo4`、`english_split() == false`、`syls` 空。突變「切分不查詞庫」會切成 token `ok`＋ㄕㄟˋ，這一條以斷言失敗 |
| `missing_syllable_defers_to_adjacent_key_fuzzy` | 載入模型，`set_fuzzy(16)`（鄰鍵開），`g o ␣`；另一個引擎 `set_fuzzy(0)` 同按鍵 | 鄰鍵開：`syls == ["ㄕㄟ"]`（fuzzy-input §2.1 存打的讀音，解碼用 ㄕㄣ 的詞）、沒有英文段、沒有英文 token、`english_reverted() == None`；鄰鍵關：英文 token `go`、`preedit == "go"`。突變「(c) 排在鄰鍵之前」讓鄰鍵開的那一半失敗 |
| `space_never_splits` | `o k w 8 ␣`；`r u s t ␣` | token `okw8`；token `rust`（ㄔ 在詞庫也不切） |
| `shift_run_takes_digits_too` | Shift+P `4 ⏎`；Shift+A Shift+T Shift+M `5 j 0 3 ⏎` | 送出 `P4`（一個 token）；送出 `ATM轉`（`syls` 是 token `ATM` 加 ㄓㄨㄢˇ） |
| `chinese_item_follows_the_revert` | `o k ␣ ↓`；`o k s ␣ ↓`；`r u s ␣ ↓` | 4 項、第 4 項是 ㄜ 的第一個詞條；`oks` 3 項（打了字就丟掉）；`rus` 3 項（ㄋㄧ 不在詞庫）；選 `ok` 的中文項後 `syls == ["ㄜ"]`、那個字固定、不學 |
| `revert_in_the_middle_decodes_apart` | 「我在用」＋「編譯」，← 兩下，`o k` | 英文段在中間、左右兩邊的解碼和把 `ok` 換成標點 token 時相同（§1.3h 的斷言）；`␣` 後 token 在中間 |
| `backspace_and_escape_on_a_reverted_run` | `g t a` Backspace Backspace Backspace；`g t` Esc；`g t a` Esc；`r u s` Esc `3`；Shift+`A` `b` Esc | 逐字刪到英文段關掉、組字區空；`g t`＋Esc 還原成 ㄔ（覆寫後的欄位）；`g t a`（回退後又打了 `a`）＋Esc 拿掉整段；`r u s`＋Esc＋`3` 是「你」；Shift 開的段 Esc 拿掉整段 |
| `backspace_before_a_revert_clears_the_column_key` | `s u` Backspace `s`（ㄋ 之後再按 s） | Backspace 後影子只剩 `s`；再按 `s` 是覆寫 (a) → 英文段 `ss` |
| `overwrite_switch_off_is_today` | `set_english_overwrite(0)`，`r u s 3`、`o k ␣` | `syls == ["ㄋㄧˇ"]`（取代）；`o k ␣` 是 ㄜ 的音節（今天） |
| `key_order_switch_off_is_today` | `set_english_key_order(0)`，`l s 3`、`c u p ␣` | ㄋㄠˇ；`cup` 仍是 token（(c) 空白在總開關下） |
| `short_list_adds_two_items_at_the_end` | `a i ␣ ↓`；`m p 3 ↓`；`u p ␣ ␣`（第 15 條的空白） | 清單前面是 ㄇㄛ 的候選（第一項不變），最後兩項 `ai`、`AI`；`mp3`、`MP3`；`up`、`UP`。選最後一項：`syls == ["_punct_AI"]`，之後 ↓ 是寫法清單 |
| `short_list_after_preceding_syllables` | `j i 3 u l 4`（我要）＋`a i ␣ ↓` | 清單最後兩項是 `ai`、`AI`（前面是這個範圍的候選，第一項不變）；選 `AI` 後 `syls == ["ㄨㄛˇ", "ㄧㄠˋ", "_punct_AI"]`，前兩個音節的固定詞不動。突變「選了換掉整個候選範圍」讓它失敗 |
| `short_list_is_only_for_the_syllable_just_made` | `a i ␣ s u 3 ↓`；`a i ␣ ← ↓`；`a i ␣ ← → ↓` | 三個都沒有 `ai`／`AI`（第三個：← 清掉了 `last_short`，游標回到 `i + 1` 也不加） |
| `short_list_never_reverts` | 窮舉（§4.3 第二條） | — |
| `digits_layer_is_untouched` | `2 0 2 6 s u 0 6 ⏎`；`1 m , 4 ⏎`；`3 d s ␣`；`1 9 8 4 ⏎` | 送出 `2026年`、`1月`、`3ds`（數字 token `3` 加英文 token `ds`）、`1984`；digits.md A.4.1 的 50 列照舊通過 |
| `english_token_definition_covers_digits` | `g t a 6 ␣` 之後 Backspace；↓ | Backspace 開英文段 `gta`（§1.3g）；↓ 在 token 後是 `gta6`、`Gta6`、`GTA6`；學習鍵（zhen-inline §4 既有測試的做法）是 `^>_punct_en` |
| `revert_hook_describes_only_the_latest_key` | `o k`、再 `j` | `k` 後 `Some(Overwrite)`，`j` 後 `None` |
| `eten_abbreviation_and_off_are_bitwise_today` | 倚天／`set_abbreviation(1)`／`set_english(0)` 各一個引擎，`o k ␣`、`l s ␣`、`g t a 6 ␣` | 和沒有這個功能的引擎逐欄相同；`english_reverted()` 一律 `None` |
| `replay_with_english_on`（`engine_replay.rs` 改） | dev302，三個開關全開 | 302／302 相同、全程 `english_reverted()` 是 `None`、`english_split()` false；release p95 < 16 ms |
| §4.3 兩個窮舉 | — | — |

- **既有測試的處置**：英文窗、D、自動固定、第三片證據的測試（`opens_on_space_keeps_the_word_in_the_composition` 到 `pick_chinese_item_equals_digit`、`inline_shows_the_letters_as_typed`、`show_on_space_shows_the_zhuyin`、`backspace_under_d_*`、`rule_d_*`、`the_word_list_is_sorted_on_load`、`autokeep_*`、`window_open_*`、`the_chinese_item_of_a_kept_token_*`、`a_zhuyin_key_over_the_chinese_item_*`、`autokept_describes_only_the_latest_key`、`punctuation_keeps_the_letters_that_are_shown`、`punctuation_with_the_window_open_*`、`backspace_with_the_window_open_*`、`backspace_after_an_auto_keep_opens_the_word_again`、`key_order_*` 除窮舉）刪掉或改寫成上表；英文段、英文 token、空白單位、學習鍵、倚天、`switching_mixing_off_keeps_the_run_as_a_token` 等保留不動；`other_keys_end_the_run_first` 改成「字母、不是字母的注音鍵、聲調鍵之外的鍵先結束英文段」。PR 附一張對照表。
- **突變**（每項讓某個測試以斷言失敗）：覆寫不回退（`overwrite_reverts_to_the_keys`）；逆序不回退（`order_reverts_*`）；(c) 空白不回退（`missing_syllable_on_space_*`）；數字鍵結束英文段（`letters_and_digits_go_on_the_run`）；空白鍵也切分（`space_never_splits`）；單一符號也切（`tone_does_not_split_one_symbol`）；切分不查詞庫（`tone_split_needs_the_lexicon`，`okgo4` 會切成 `ok`＋ㄕㄟˋ）；(c) 排在鄰鍵之前（`missing_syllable_defers_to_adjacent_key_fuzzy`）；短詞項目對整個候選範圍而不是位置 `i`（`short_list_after_preceding_syllables`）；影子不跟 Backspace 清欄（`backspace_before_a_revert_*`）；開關不生效（兩條 `*_switch_off_is_today`）；短詞項目加在前面或換掉第一項（`short_list_adds_two_items_at_the_end`）；英文 token 定義不含數字（`english_token_definition_covers_digits`）；倚天也回退（`eten_*`）。

### 5.2 FFI、C 冒煙、殼

- `ffi.rs` 子行程測試：`set_english_overwrite` 的碼 0／1／2 與 NULL；經 C ABI 打 `o k ␣` 得 `preedit` `ok`、`candidate_count` 0；打 `g t a 6 ␣` 得 `preedit` `gta6`；`set_english_inline` 的測試刪掉；**`load_english` 的測試在 PR 1 不動**（PR 2 才刪）。`abi_smoke.c` 加一段、刪一段。
- `swift test`：`englishOverwrite` 沒有值是 true、`englishKeyOrder` 沒有值是 true、存過 false 的是 false；兩個都在每次 `build()` 送；設定視窗沒有「英文的顯示」、有兩個勾選與說明、反灰條件；`englishAvailable` 的測試在 PR 1 照舊通過。`Selftest.swift`（PR 1）：仍載入單字表（回 0），`rust ␣` 的預期改成「`preedit` 是 `rust`、沒有候選、`syls` 是一個英文 token」（`englishWindow` 常數改名 `englishToken`）；`scripts/check-app.sh` 跑 selftest 那一步因此跟著過，s3b §13.2 那一段同步。

### 5.3 不變與速度

- 中英混打關（核心預設）：所有 golden、重播、`cli/tests/golden.rs`、既有測試一個位元組不改。
- 開著：`replay_with_english_on` 302／302、p95 < 16 ms；digits.md A.4.1 的 50 列照舊（#26、#27、#28 的送出文字相同，`syls` 若改變（`P4` 成為一個 token）在 PR 列出）。

### 5.4 量測門檻（§4.2）、研究紀錄

- 門檻 1 全 0；門檻 2 過；臂 B、C、D 三種設定的表與召回／probe 結果進研究紀錄與 Release 說明；`ENGLISH_SHORT` 的核對結果進測試註解。

### 5.5 實機（維護者，`imeshot` 每次 ≤ 15 秒，分三次）

1. 打「我要」＋`o k`（看到粗底線的 `ok` 與提示）＋`␣`＋「看看」、Enter：送出「我要ok看看」。接著 `g t a 6 ␣ r d r 2 ␣ p o e 2`、Enter：送出 `gta6rdr2poe2`（中間沒有空白；要空白就空白兩下）。
2. `o k j i 3`：組字區是「ok我」（或 ㄨㄛˇ 的解碼結果）；`a i ␣ ↓`：候選最後兩項 `ai`、`AI`，選 `AI`。
3. 設定裡關掉「重打同一個位置算英文」，打 `s j u 3`：是「你」（取代）；再開回來，打 `s j u`：變成 `sju` 英文段，Esc 變回注音（ㄋㄨ…照 §2.4 的還原點），再 Esc 清掉。

### 5.6 保留集

- 收尾的 fresh verifier 照規則跑一次（中英混打在核心預設關，選字結果應不變）。

## 6. 停止條件

- §4.2 門檻 1 不過（修到過為止，不是門檻的問題）；門檻 2 不過（停、交數字、§9 第 1 題）。
- 中英混打關著時任何 golden、重播或既有測試改變；開著時重播 ≠ 302／302 或 p95 ≥ 16 ms；digits.md A.4.1 任何一列的**送出文字**改變。
- 要改到第 2、11、12、22 條在中英混打關著時的行為才做得到。
- `ENGLISH_SHORT` 核對後剩不到 5 個字（清單機制不值得做，改問維護者）。

## 7. 範圍外

英數前綴接中文的切分（`0c輸入法`、`3c產品`、`4g`：本片要 `0c ␣ 輸入法`，數字 token `0` 與英文 token `c` 分開；研究紀錄 2026-10-26 的第 2 項另開片）；**正式寫法（維護者決定 (B)，另開一片、接在本片之後）**：zhen-words（分支 `feat/zhen-words`，契約 READY 但落後 main 1,324 個 commit）只保留補充表 `data/english-extra.txt`（268 行，CC0，正式寫法一行一個），維基計數那一半不做；英文段結束成 token、token 的小寫等於表裡的鍵時，token 的文字換成正式寫法（`chatgpt` → `ChatGPT`），照打的寫法留在 ↓ 寫法清單；接口在 §2.5 留好。那一片重寫 `zhen-words.md` 的狀態列與 §2.2、§2.3、§3（英文窗已不存在），本片不改那份契約；Shift＋空白逃生鍵；空白鍵切分；倚天排列；英文的學習；英文 token 左右自動加空格以外的排版；`ENGLISH_SHORT` 由使用者自訂；重新引入單字表（例如用它擋 `go`、`so` 這類被 (c) 誤判成英文的情況——(c) 只在今天沒反應的空白鍵上發生，不是誤判）。

## 8. 負責人、順序、預算、限制

- **順序**：這份契約 → fresh `pilotfish:plan-verifier`（READY 才做）→ **PR #273（模糊輸入）先合併**，本片從合併後的 main 開分支（§2.9；若已開始就 rebase）→ **PR 1**（executor：核心、FFI、CLI、殼、測試、文件；只刪 `set_english_inline`，單字表的載入與打包不動）→ main 在 GCP／188 跑 §4（送出前腳本先過 `/code-review`；用 Monitor 看）→ 門檻過 → `/code-review`＋fresh verifier＋CI → merge commit → **PR 2**（單字表退場，§2.8 的表；純刪除，照「程式」級關卡，驗收是那條 grep 加 `check-app.sh`、`test-sandbox-mutations.sh`）→ 維護者實機（§5.5）→ 保留集 → 之後另開 zhen-words 的正式寫法片（§7）。
- **回復**（要退回時）：**先 revert PR 2**（`data/english-words.txt.sha256`、`LICENSES/SCOWL.txt`／`SymSpell-MIT.txt`、`build-app.sh` 的下載與打包、`load_english`／`loadEnglish` 都回來，建置重新產生單字表），**再 revert PR 1**（引擎回到英文窗、D、自動固定；殼回到有「英文的顯示」）。兩個 PR 都是 merge commit，各 `git revert -m 1`。設定：舊版殼讀不到的鍵 `englishOverwrite` 直接忽略；`englishKeyOrder` 沒存過值的使用者在舊版下又讀成 **false**（舊預設），存過 true 的照存的值〔推論：store 只在使用者切換時寫值，沒切換過就沒有值〕。單字表退場後再回來不需要資料遷移（表是建置時產生的，不在使用者機器上）。
- **executor 不可以**：連網；讀 `eval/holdout/`、維護者的私人資料；安裝、啟動 App，執行 `.app` 裡的程式，呼叫 TIS 或 lsregister，碰 `~/Library` 與鑰匙圈；push、開 PR、留言、發版；碰其他 worktree；改 `eval/bench/` 的檔或雜湊、`data/lexicon/`、`eval/golden/digits-*.txt`；在 Mac 跑完整 `make test-rust`（只跑改到的測試檔）。
- **main 不可以**：把評測句、私人訊息寫進任何地方；看過 §4 的數字之後改門檻（改了就是作廢重開）。
- 預算：executor 1 回合＋1 次修正；量測 GCP 一次 ≤ 2 小時（32 核 Spot）。

## 9. 維護者的決定（2026-10-27 選擇題）

1. 三個開關**全開，量完誤觸才定案**：§4 門檻 2 過了才算定案，不過就把貢獻最多的那個改成預設關、其他照舊，把數字給維護者。
2. **拿掉**「英文的顯示：打字時就顯示／按空白才顯示」設定（`englishInline`、`set_english_inline` 一起刪）。
3. (c) 在聲調鍵**做**，放在「依按鍵順序判斷英文」底下；代價模擬器量不到，實機回饋決定。
4. 回退後的 Esc **還原成回退前的注音**（§2.4；只對回退開、之後沒改過的英文段）。
5. `ENGLISH_SHORT` 照 §2.6，核對後只刪不加（沒另外問，照推薦）。
6. 聲調鍵切分要兩個符號（沒另外問，照推薦）。
7. **(A) 鄰鍵優先於 (c)**（2026-10-27）：模糊輸入的「鄰鍵打錯」開著、而且詞庫沒有的音節有鄰鍵替代讀音時，鄰鍵贏（照 fuzzy-input 收下音節）；沒有任何替代讀音才回退 (c)。模糊輸入先合併，本片接在後面（§2.9、§8）。
8. **(B) 正式寫法另開片**（2026-10-27）：zhen-words 只保留補充表 `data/english-extra.txt`，維基計數那一半不做；英文段結束成的 token 小寫等於表裡的鍵時換成正式寫法、照打的寫法留在 ↓ 清單。是本片之後的獨立一片（§7），`zhen-words.md` 的狀態列在那一片重寫；本片不能擋到它（§2.5 的接口）。

## 10. 審查紀錄

| 審查 | 問題 | 處置 |
|---|---|---|
| plan-verifier 第 1 次（2026-10-27，REVISE） | 1. 和模糊輸入的鄰鍵（`fuzzy-input.md`，PR #273）在「詞庫沒有的音節」上沒有先後規則、哪一片先合併沒寫、s3a §2 沒跟、沒有鄰鍵開著的測試 | FIX：維護者決定 (A) → §2.9（鄰鍵先、沒收下才 (c)；PR #273 先合併、本片 rebase）、§2.3 兩列加條件、§3.1 加 s3a §2 一列、§4.2 註明臂用 `fuzzy 0`、§5.1 加 `missing_syllable_defers_to_adjacent_key_fuzzy`（`g o ␣` 鄰鍵開收成 ㄕㄟ、關是 token `go`） |
| 同上 | 2. zhen-words（正式寫法）的去向沒寫 | FIX：維護者決定 (B) → §7 寫成本片之後的獨立片（只留補充表、維基計數不做、`zhen-words.md` 狀態列在那一片重寫）、§2.5 留接口（結束英文段只有一處、token 文字與讀音分開、↓ 第一項是 token 目前的文字）、§2.8 註明刪 SymSpell 不影響它 |
| 同上 | 3. `tone_split_needs_the_lexicon` 用 `gta6` 測不到「查詞庫」：尾音節只有一個符號，單符號規則就擋掉了 | FIX：改成 `o k g o 4`（尾音節 `go` ㄕㄟ 兩個符號，ㄕㄟˋ 在基底檔與全部疊加層都沒有，量過），預期英文段 `okgo4`、`english_split()` false；突變「不查詞庫」會切成 `ok`＋ㄕㄟˋ |
| 同上 | 4. PR 1／PR 2 的分界不一致（§2.8 說 `load_english` 刪、§3 與 §5.2 又在 PR 1 刪） | FIX：PR 1 只刪 `set_english_inline`（ABI、FFI 測試、殼的 store）；`load_english`、ABI、FFI 測試、`loadEnglish`／`englishAvailable`、Selftest 的載入全在 PR 2；§2.8、§3、§5.2、§8 同步；Selftest 的 `rust ␣` 預期在 PR 1 改、載入步驟 PR 2 刪 |
| 同上 | 5. PR 2 的刪除清單不完整 | FIX：§2.8 改成逐檔逐行的表（grep 2026-10-27，main `ab70d3da`）：`check-app.sh` 80、125–134，`test-sandbox-mutations.sh` 150、159（第 18、21 案換資源檔），`Selftest.swift` 載入，`ci.yml`／`release.yml` 的快取，`LICENSES`，`data-files.md`，殼與測試的每一處；加驗收 grep 與允許殘留的檔案清單 |
| 同上 | 6. §2.6 短詞項目的條件不可判定（「兩個以上音節之後」）、候選範圍跨音節時換什麼沒寫 | FIX：`last_short = Some((i, 字))`，條件只有 `last_short` 是 `Some` 且 `cursor == i + 1`，和前面幾個音節無關；選了只換位置 `i` 那一個音節，候選範圍跨幾個音節都不影響；§5.1 加 `short_list_after_preceding_syllables`（我要＋`a i ␣ ↓`，選後 `syls == ["ㄨㄛˇ","ㄧㄠˋ","_punct_AI"]`）、`short_list_is_only_for_the_syllable_just_made` 加 ← → 的一例；§3.1 第 15 條那一列同步 |
| 同上 | 7. §8 沒有回復順序與舊版對新設定鍵的行為 | FIX：§8 加「回復」：先 revert PR 2（雜湊檔與下載回來）再 revert PR 1；舊版忽略 `englishOverwrite`；`englishKeyOrder` 沒存過值的在舊版又讀成 false |
| plan-verifier 收尾審查 | — | READY |
| PR 1 實作的 code-review（2026-10-27，兩輪） | 空白鍵 (c) 的 token 沒有還原點，設定說明卻承諾 Esc 變回注音；說明把空白鍵的 (c) 放在「依按鍵順序」底下，但它屬於總開關；例子 `cup` 在鄰鍵開著時是中文 | FIX：§2.4 的 Esc 列、§2.6 的記錄、§3 的兩句說明改成實作（各處標 2026-10-27 實作修訂）；例子改 `so`；README、digits.md、settings-window.md 同步 |
