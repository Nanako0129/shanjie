# 選字記憶（使用者學習）

這份寫的是現在的行為。

範圍：使用者在候選窗改選、或從預測列選詞之後，輸入法怎麼記住、怎麼存、下次怎麼用；包含句首單字的學習、新詞（詞庫沒有的詞）的學習、學習檔、隱私閘門、前文讀取與清除。程式在 `core/src/learn.rs`（紀錄與查詢）、`core/src/learn_store.rs`（學習檔）、`core/src/engine.rs`（送出時學、忘記、預測列）、`core/src/lm.rs` 與 `core/src/tinylm.rs`（解碼與重排用紀錄）、`macos/Sources/ShanjieKit/Learning.swift`（殼）。C 介面的函式簽章見 `docs/spec/c-abi.md`；重排模型本身見 `docs/spec/semantic-rerank.md`；預測列的排序見 `docs/spec/prediction.md`；隱私總則見 `docs/spec/privacy.md`。

## 1. 總覽

### 1.1 學什麼

- **改選**：在候選窗把某段的詞改成另一個詞，按 Enter 送出後，記下「前文、讀音、選到的詞」，被換掉的詞權重減半。下次在相同前文下打相同讀音，選到的詞排在前面。
- **預測列選取**：從預測列選的詞也記，沒有被取代的詞，所以不減半（見 4.3）。
- **句首單字**：句首（前文沒有漢字）的單字改選，用「後面接的一到三個音節」當鍵（見 7）。
- **新詞**：逐字改選出詞庫沒有的 2 到 4 字詞，記成不看前文的詞（見 8）。
- 沒有開候選窗、直接打字送出的字不學；唯一的更新是新詞的「用到」（只動既有紀錄的權重與日期，不新增內容，見 8.5）。

### 1.2 資料流

1. 殼在每個按鍵之前取樣隱私閘門（3），把結果送給核心 `set_learning`。
2. 組字開始時殼讀一次插入點前的文字，送給核心 `set_left_context`（2）。
3. 使用者改選：核心的 `choose()` 把那一段固定成 `Fixed`，並記下選字前畫面上的字（`pre`）與當時的句首鍵（`pick_keys`）。
4. 送出（4.1）：核心 `learn_commit` 算出要教的紀錄，交給 `Learner`（`core/src/learn.rs`），再由 `persist` 寫進 `learning.tsv`（10.3）。
5. 解碼與重排讀 `Learner`：n-gram 解碼（6）、預測列（6.5）、句首單字的重排（7）、新詞列舉（8.4）。

### 1.3 預設值

- 引擎建立時學習是關的（fail-closed）；殼確認閘門不擋之後才打開。
- 新詞學習預設開（`newword`），子段（8.2 第 5 點）預設開（`NEWWORD_SUBSPANS`）；兩者只有測試掛鉤可關。
- 全域那一層的加分是 0（6.2）。

## 2. 前文

### 2.1 前文鍵 `context_key`

- `context_key(prefix)`：從 `prefix` 尾端往前取連續的漢字（`is_han`），最多 `MAX_CONTEXT` ＝ 2 字；遇到任何非漢字（標點、英數、空白、控制字元、emoji）就停；一個字都沒取到時回傳句首哨兵 `^`（`SENTINEL`）。
- `prefix` 是殼傳入的前文，加上組字區裡這一段之前已顯示的文字。學習（送出當下）與解碼（用路徑上已走過的 `h.surface`）呼叫同一個函式。
- `is_han` 涵蓋 CJK 統一表意文字各區、相容表意文字與 `〇`（U+3007）；`Learning.swift` 的 `LeftContext.isHan` 照同一張表。

### 2.2 殼讀前文

- **讀一次**：每次組字開始、第一次 `setMarkedText` 之前讀；只有「不帶 Control、Option、Command、Caps Lock 的字元鍵」且殼目前不在組字中時才讀。組字期間與送出前不再讀。
- **什麼時候算沒有前文**：`selectedRange` 是 `NSNotFound`；或 client 還有 marked text（`markedRange` 長度大於 0，且位置不是 `NSNotFound`）；或讀取回傳 nil；或隱私閘門擋住（擋時不讀，傳 NULL）。`markedRange` 長度是 0 時不論位置都算沒有 marked text，照樣讀：實機上 VS Code、Chrome、Finder 搜尋框回 `{插入點, 0}`，有的 App 回 `{NSNotFound, NSNotFound}`。
- **範圍**：最多請求插入點前 256 個 UTF-16 單位（`LeftContext.maxUTF16`）；App 回傳更長時先夾到最後 256 個單位。範圍被截過（起點大於 0 或回傳被夾）時丟掉第一個 grapheme，避免切到代理對或 grapheme 中間。
- **送給核心的內容**：從最後一個換行（`Character.isNewline`）或 NUL 之後取起（NUL 是斷點，不是刪掉），保留最後 64 個 grapheme 的原文（`LeftContext.maxGraphemes`）。終端機類 App（`LeftContext.terminals`）只傳尾端連續的漢字、最多 2 字，因為終端機的一行裡是指令與輸出。
- 前文只存在記憶體：殼不另外存放，核心在每次 commit、reset、錯誤碼 4、規則 22 放行之後清掉；空組字區收到按鍵而沒有剛設過前文時也清（`docs/spec/semantic-rerank.md` §5.2）。

### 2.3 核心收前文

- `set_left_context(text)`：學習的鍵與 n-gram 的歷史詞照舊只用結尾 ≤ 2 個漢字（`Engine::left`）；解碼另外查前文最後 64 個字元的漢字對集合（前文重現項，`docs/spec/decoder.md` §4.6），這一項不寫學習檔，學習不看它。另外只留重排模型宣告的前文長度的字，或結尾的英文詞（`left_full`），用於重排與英文表，不用在學習，也不寫進檔案。NULL 或空字串表示沒有前文；不是 UTF-8 時回 2 並清空。

## 3. 隱私閘門

### 3.1 判斷與取樣

- 以下任一成立就暫停學習（`Shell.learningPause`）：
  - 安全輸入開著（`IsSecureEventInputEnabled`）；
  - 目前 client 的 bundle ID 在學習 denylist（`Shell.learningDenylist`：1Password 兩個 ID、Bitwarden、KeePassXC、Apple 密碼、鑰匙圈存取）；
  - 判斷不了，例如取不到或讀到空的 bundle ID。
- 終端機不在 denylist：終端機照樣學，內容仍限於用注音打、有改選的中文詞。
- bundle ID 只拿來比對，不傳給核心、不記 log、不儲存。denylist 的 ID 沒有逐一在實機上核對（見 14）。
- **取樣時機**：每次呼叫可能學習的核心函式之前都重新取樣，而且每次都呼叫 `set_learning`，不只在值改變時：`Session.send()`（每個按鍵）、`finish()`（reset）、滑鼠點選候選字（`candidateSelected`）。換排列重建引擎後引擎預設是關的，不每次送就會一直停著。

### 3.2 核心語意

- `set_learning(0)` 丟掉尚未送出的待學段落（每個 `Fixed` 的 `pre` 與 `displaced_new`）。組字區非空時它也標記這段組字為「暫停過」（8.1）。
- 一段只在「`choose()` 當下」與「送出當下」旗標都是 1 時才學。
- `set_learning` 不改解碼：解碼只要學習資料非空就讀它；旗標只管要不要記錄選字，所以也不讓重排工作過期。
- 只接受 0 和 1，其他值回 2。

### 3.3 選單

選單第一項在暫停時顯示固定字串「學習已暫停（安全輸入）」或「學習已暫停（此 App）」，不放 App 名稱。

## 4. 什麼時候學

### 4.1 送出路徑

只有 `take_commit`（把組字區整串送出並清空狀態）會呼叫 `learn_commit`。

| 路徑 | 學不學 |
|---|---|
| Enter（Shift+Enter 也是，之後把按鍵放行給 App） | 學 |
| 規則 21：組字中按了引擎不處理的鍵，先送出再交給 App（見 `docs/spec/keys-and-composition.md`） | 學 |
| 滿 `MAX_SYLLABLES`（128）的自動送出，含預測列選取、英文詞造成的 | 學 |
| `reset`（Commit 或 Discard，殼從 deactivate、commitComposition、換擁有者、切換排列等任何地方呼叫） | 不學：這些多半是焦點或 App 改變，常和安全輸入切換同時發生 |
| Esc、Backspace／Delete 清空 | 不學 |
| 解碼失敗（`key()` 回 4，引擎重置） | 不學 |

### 4.2 哪些選字算改選

- 由候選窗固定、而且「選字前那一段畫面上的字」（`pre`）和選到的詞不同的段落，送出時才教。`pre` 在 `choose()` 當下記下；再次改選同一段時沿用第一次的 `pre`。
- 不算：標點替代字（讀音以 `_punct_` 開頭的段落）；引擎自己固定的段落（`frozen`，Backspace 切開的碎片與改選時保留的旁邊詞）；選到的詞不在詞庫（新詞，8.4）；學習關著時選的。
- 送出的文字用「解碼出的組字區文字」，不含尚未完成的注音符號；所以組字區多一個未完成的 ㄅ，學到的紀錄不變。

### 4.3 預測列選取

預測列選的詞固定時，學習開著就記下 `pre ＝ ""`（沒有被取代的詞），其餘照改選處理：鍵是該詞起點的前文鍵、讀音是那一段、權重 +1、不減半。共用同一份記錄、檔案、衰減、全域化、修剪、忘記與清除。預測列選取發生在組字區尾端，後面沒有音節，所以前文鍵是 `^` 時一律存成 `^>$`（5.3），單字因此不記。

### 4.4 失敗

- 學習在算出 commit 文字之後才執行，包在內層 `catch_unwind` 裡；學習或寫檔出錯（含 panic）只放棄這一次學習，`key()` 照樣帶著 commit 回 0。
- 解碼失敗那條路徑（錯誤碼 4）會把引擎重置，沒有任何東西被學。

## 5. 紀錄

### 5.1 欄位、衰減、加權

- 一筆紀錄（`Record`）：前文鍵、讀音（音節陣列）、詞、權重、日期（本地日曆日，距 1970-01-01 的天數）。`Record` 不實作 `Debug`，因為它含使用者輸入的詞。
- **衰減**：解碼或比較時，權重乘 `0.5^(經過天數 / HALF_LIFE_DAYS)`，`HALF_LIFE_DAYS` ＝ 14。日期差夾到 ≥ 0，時鐘倒退時權重不會變大。
- **教一次**：同一組（前文鍵、讀音、詞）的權重 ＝ 衰減後權重 + 1，日期改成今天。
- **作用門檻**：衰減後權重 ≥ `ACTIVE`（0.5）的紀錄才加分。

### 5.2 前文鍵的種類

| 鍵 | 內容 | 誰可以用 |
|---|---|---|
| 一或兩個漢字 | `context_key` 的值 | 多字詞（1 或 2 字的鍵都行）；單字只限剛好 2 字 |
| `^>`＋一個音節的讀音 | 句首粗鍵：前文沒有漢字，鍵帶詞後面第一個音節 | 多字詞、單字 |
| `^>$` | 句首、詞在組字區最後 | 多字詞；單字不學 |
| `^>_punct_en`、`^>_punct_num` | 句首、後面接英文（含空白單位）、接數字 | 多字詞；單字不學 |
| `^>_punct_，` 這類 | 句首、後面接標點 | 多字詞；單字也可（粗鍵） |
| 中鍵、細鍵 | `^>`＋後兩個／三個音節，音節間隔 `FINE_SEP`（U+001F） | 只有單字（7.1） |
| 空字串（`GLOBAL`） | 全域 | 只有 2 字以上的詞 |
| `#`（`NEWWORD`） | 新詞，不看前文 | 只有新詞（8） |

單獨的 `^` 永遠不是紀錄的鍵：`Learner::teach` 不產生，`learn_store` 載入時丟掉。

### 5.3 單字與句首的鍵規則

- **句首鍵帶下一個音節**：前文鍵是 `^`（句首、標點後、英數後、讀不到前文）時，學與查都換成 `record_key`：`^>`＋這個詞後面第一個音節的讀音，詞在組字區最後時是 `^>$`；後面是英文單位或空白單位一律算 `^>_punct_en`，是數字串一律算 `^>_punct_num`（`next_key`）。解碼、`learn_commit`、預測列的 `learned_weight` 都呼叫這一個 `record_key`。查詢時，詞結束在段內位置 i，下一個音節是 `syls[i]`；在段的最後則用段右邊的第一個音節（`Learn.after`），沒有就是 `$`。合成鍵（`^>…`）不參加「最後一個字」那一層。
- **單字要兩字前文**：1 個字的詞只在前文鍵剛好兩個漢字，或在句首的 `^>`＋音節讀音鍵下學與查（`single_key_ok`，`teach` 與 `learn_store` 共用）。一個漢字的前文、`^>$`、`^>_punct_en`、`^>_punct_num`、單獨的 `^` 都不學。
  - 單字永遠不全域化，不走「最後一個字」那一層。
  - 理由：單字在寬鬆的鍵下教一次，會讓同一個字在所有符合的位置加分（「再」教過之後，「現在在做」被拆成「現／再」）。`^>_punct_en` 與 `^>_punct_num` 涵蓋後面接任何英文或任何數字，和「讀音相同才生效」不合，所以單字不記。
- **合法的鍵**：`learn_store` 的 `valid` 是唯一的過濾點，載入與寫出都用它。合法的鍵 ＝ `GLOBAL`（只給 2 字以上）、一或兩個漢字（單字只能配剛好兩個）、`^>$`、`^>`＋非空且不含控制字元的讀音（可含 `-`）、單字的中鍵與細鍵（格式必須恰好是 `mid_key`／`fine_key` 做得出來的，7.1）、`#`（新詞，8.3）。載入時不合法的紀錄當壞行丟掉：單字配一字鍵、單字配全域鍵、單字配 `^>$`／英文類／數字類、單獨 `^`；它們不佔容量，也不影響 `has_reading`、`words_of`、`is_empty`，下一次完整重寫後檔案裡就沒有了。
- 2 字以上的詞不受上面的單字限制：照舊在完整前文、最後一個字、全域三層查。

### 5.4 全域化

同一組（讀音、詞）在 ≥ 2 種不同的完整前文鍵下都有衰減後權重 ≥ 0.5 的紀錄時，產生一筆前文為空的全域紀錄（把全域紀錄補到權重 1）。計數時：所有 `^>…` 句首鍵合起來算一種；全域鍵與 `#` 不算。單字不全域化。全域紀錄照常寫檔、衰減、遺忘，但解碼時加分是 0（6.2）。

### 5.5 取代的詞減半

改選時，被換掉的詞（`pre`）在同一個鍵下的權重減半（衰減後 ×0.5，日期改今天），選到的詞 +1。

- 前文鍵是 `^` 時，改選當下的合成鍵記在 `Fixed.pick_keys`（`pick_keys()`：`^>`＋當時的下一個音節，沒有就 `^>$`；單字另加當時它後面兩個與三個音節做成的中鍵與細鍵）。送出時，`pre` 在「送出時的鍵」下減半，也在 `pick_keys` 裡不是送出時任何一個鍵的鍵下減半（`Learner::halve`）。理由：改選前畫面上的詞，是改選當下的鍵加的分；詞還在最後時改選、再繼續打字的常見流程，只在送出時的鍵下減半會讓錯字那筆紀錄不變弱。再次改選同一段沿用第一次的 `pick_keys`。
- 預測列選取沒有被取代的詞，不減半。

### 5.6 容量與修剪

- 衰減後權重 < `PRUNE_FLOOR`（0.05，約 60 天沒再用到）的紀錄，在載入與每次完整重寫時刪掉。
- 一般紀錄最多 `CAPACITY` ＝ 50,000 筆，超過先刪權重最低的（同權重刪後面的紀錄）；新詞另算，最多 `NEWWORD_CAP` ＝ 2,000 筆（8.3）。
- 刪除用 `swap_remove` 並修補索引，不重建；紀錄順序不保留。

## 6. 解碼時怎麼用

### 6.1 查詢順序

`Learner::lookup(key, 讀音, today)` 依序查三層，第一個有可用紀錄（權重 ≥ 0.5）的層回答，同一個詞在該層有好幾筆時取最高權重：

1. 完全比對：鍵完全相同（含一個漢字的鍵與句首合成鍵）。
2. 最後一字：所有一或兩個漢字的鍵，最後一個字和查詢的最後一個字相同；單字的紀錄不參加；句首合成鍵、全域鍵、新詞不參加。
3. 全域。

例：在「管把」後面教一次，之後「肯把」後面也查得到（第 2 層），而且不產生全域紀錄。

### 6.2 加分

- 查到紀錄的詞，詞庫分數（log10）改成 `max(原分數, 該讀音最高分 + ε × w／(w + 1))`，`w` 是衰減後權重；只能把詞往上提，不能壓低其他詞。權重大的紀錄勝過小的。
- ε：完全比對與最後一字層用 `LEARN_EPS` ＝ 6.0；全域層用 `LEARN_EPS_GLOBAL` ＝ 0，為 0 時全域這一層完全關閉，包括「不在前 `PER_KEY` 名」的列舉。
- 句首單字（規則 10）的紀錄不走這個加分（7）；新詞也不走（8.4）。

### 6.3 超出 `PER_KEY` 的詞

解碼每個讀音只列舉詞庫分數最高的 `PER_KEY` ＝ 12 個詞。有學習紀錄的詞即使排在 12 名之後也加入列舉（`words_of`），且只在路徑的前文鍵確實有它的紀錄時才可用（`extra` 條目沒有紀錄就略過）。經過容錯讀音（模糊音、相鄰鍵）找到的詞不列舉超出 `PER_KEY` 的學習詞。

### 6.4 Kneser–Ney 與三連

被加分的詞條（加分後分數高於原分數）不經過 Kneser–Ney 替換、也不查三連表：語言模型那一項用 `prob_learned`，`pb` 是加分後的值；沒被加分的詞條照一般規則。否則加分會被 `N′/ΣN′` 或回退權重換掉，只剩 `(1−λ)·lp` 那一份。被加分的詞條不加讀音項（`rd_term`）。

### 6.5 預測列

預測列每個候選的學習權重是 `learned_weight`：精確或最後一字層的權重，全域層算 0；前文鍵是 `^` 時查 `^>$`（預測的詞結束在游標）。只有「有紀錄可能對上」的起點才多要 `PREDICT_SCAN`（27）個，否則只要 `PREDICT_MAX`（9）個；有權重的項目排在前面（穩定排序），不受長起點上限 `PREDICT_LONG_CAP` 的限制。新詞紀錄不開那個較寬的掃描。細節見 `docs/spec/prediction.md`。

### 6.6 沒有紀錄時

學習資料是空的，或沒有紀錄碰到打的讀音，輸出和沒有學習功能逐位元組相同。

## 7. 句首單字（規則 10）

句首的單字在重排開著時有自己的一套用法（程式註解稱為規則 10，SINIT）；它的紀錄不走 6.2 的加分。

### 7.1 三層鍵

單字在句首（前文鍵是 `^`）改選時，教一筆粗鍵，後面還有足夠音節時再教中鍵與細鍵：

- 粗鍵：`^>`＋下一個音節（`next_key`）。
- 中鍵（`learn::mid_key`）：`^>`＋下兩個音節，音節之間隔 `FINE_SEP`。
- 細鍵（`learn::fine_key`）：`^>`＋下三個音節；組字區在第一個字之後剛好只有兩個音節時，細鍵是那兩個音節加句尾記號 `$`。
- 只在第一個字之後只剩一個音節時只記粗鍵；後面沒有音節（`^>$`）不記任何一層。
- 英文、空白、數字讓每一層都不記；標點只讓中鍵與細鍵不記，下一個是標點時粗鍵 `^>_punct_，` 照記。中鍵或細鍵需要的某個音節是英文、數字、空白或標點，就不記那一層和更長的。
- 預測列選的單字存 `^>$`，所以不記。
- `is_mid_key`、`is_fine_key`、`single_key_ok`、`valid` 只收建構函式做得出來的格式；舊程式載入時因為鍵裡有控制字元把它丟掉，不會出錯。
- 忘記（⌘⌫）一次拿掉三層；改選取代的單字在三層（以及 `pick_keys`）下都減半。

### 7.2 不抬升的池

一段（rerank 的一個自由段）帶著重排工作（語意校正開、會意模型已載入）、從句首開始、第一個音節有規則 10 紀錄時：候選池、每條候選的 n-gram 分數、錨點（池的第一條）、重排之前的顯示與學習守門，都用「不看規則 10 紀錄」的解碼（`Sinit::Off`，含讀音項）。另一次解碼（`Sinit::Lift`）只用來找「第一個字是學到的字」的最好路徑：那個字的 lp 抬到 `max(自己的 lp, 同讀音第一名的 lp)`，照一般路徑計分（承接 `prob_tri`，不走 `prob_learned`）。

### 7.3 進候選池

候選的第一個字（照字面認，不管路徑把它切成單字或多字詞）等於紀錄的字，而且紀錄在那個字打的讀音與句首鍵之下，才算學到的候選（`sinit_b`）。這條路徑照不抬升的 lp 與分數（等於它在 `Sinit::Off` 解碼的分數，`sinit_unlift`）換進池：不在池的前 `TOPK`（8）條就換掉最後一條，永遠不換第一條；n-best 裡沒有就不補；有模糊音字的路徑不算。

### 7.4 融合分數的學習項

每條候選有 `b_i ＝ w／(w+1)`（`w` 是那一層的鍵、那一條候選第一個字的紀錄權重，沒有就是 0）。一段只用命中的最長一層（那一段的開頭在那一層的鍵有任何字的紀錄就算命中），三層不相加（`sinit_chars`、`SinitLayer`）：

| 層 | 鍵 | 學習項 | 常數 |
|---|---|---|---|
| 細 | `^>`＋後三個音節（或兩個加 `$`） | `EPS_SINIT_FINE·(b_i − b_0)`，正負兩向 | `EPS_SINIT_FINE` ＝ 4 |
| 中 | `^>`＋後兩個音節 | `EPS_SINIT_MID·max(b_i − b_0, 0)`，只在小模型的 `ll_i > ll_0` 時算，否則 0 | `EPS_SINIT_MID` ＝ 4 |
| 粗 | `^>`＋後一個音節 | `EPS_SINIT_COARSE·max(b_i − b_0, 0)`，同樣只在 `ll_i > ll_0` 時算 | `EPS_SINIT_COARSE` ＝ 2 |

- 細層：學到的字是錨點時，別的候選要贏過它也要多付這一項。
- 一段只有 2 個音節時只有粗層可用；細層與中層需要段內（或段右邊緊接）至少三個音節。
- 學習項加在 `pick_masked`、`colloquial::fused_pick`、`colloquial::guarded_pick` 三條選字路徑上，手動詞遮罩照舊；三檔語意校正強度共用同一組常數。
- 錨點學過、候選沒學時學習項是 0（只抬不壓，細層除外）。

### 7.5 τ、δ 與守門

- n-gram 第一名與第二名的差 `ng[0] − ng[1] ≥ τ` 而且有候選的學習項 > 0 時，只有學習項 > 0 的候選可以換掉錨點，且要贏過錨點至少 δ；差 < τ 時所有候選照一般規則比。三條選字路徑同一個規則。
- 規則 10 的紀錄不觸發學習守門（`boosted`）：守門只看 6.2 的加分；其他規則的紀錄照舊觸發守門，守門擋住的段不重排。

### 7.6 沒有重排工作時

語意校正關、或會意模型沒載入、或這一段沒送進重排時，規則 10 的紀錄不影響選字（`Sinit::Off`），也不觸發守門；紀錄照存，打開語意校正就生效。

### 7.7 已知代價

同鍵的兩字詞分不出來：句首教了單字「再」之後，只打兩個字的 `ㄗㄞˋ ㄐㄧㄚ`（想打「在家」）在聊天設定會是「再加」。後面接更多音節時（例如 `ㄗㄞˋ ㄐㄧㄚ ㄒㄧㄡ ㄒㄧˊ`）學到的字只在候選裡，偏好看整句，仍是「在家休息」。沒有重排工作時紀錄不影響選字。

## 8. 新詞

詞庫沒有的詞，從逐字改選學起，不看前文，在任何前文下都能打出來。

### 8.1 什麼時候學

- 這次組字至少有一段由候選窗改選成單字，而且 `pre`（選字前畫面上的字）和選到的字不同（4.2）。從預測列選的詞不算。
- 學習旗標在改選當下與送出當下都是 1。
- 暫停：這段組字期間有任何一個按鍵是在學習旗標為 0 時處理的，或組字中途 `set_learning(0)`，送出時就不學新詞、也不計「用到」。引擎處理每個按鍵時，組字區是空的就先清掉暫停旗標，學習旗標是 0 就設旗標；組字中 `set_learning(0)` 也設；`clear_all` 清掉。暫停期間打的字不會因為相鄰的改選被存下。
- 送出路徑與 4.1 相同。

### 8.2 學什麼

1. 取送出當下組字區最後路徑上的詞（顯示用的切分），找出連續的單字詞（每個詞一個音節、一個漢字）段落。遇標點替代字、非漢字、功能字表 `NEWWORD_BREAK` 裡的字就切開。
2. 切開後的每一段，同時符合下列條件才學：
   - 至少含一個 8.1 的改選字；
   - 長度 `NEWWORD_MIN`（2）到 `NEWWORD_MAX`（4）個音節；
   - 這一串讀音下，詞庫沒有這個詞。「詞庫」指引擎實際載入的詞庫（基底、`overlay-add.tsv`、`sandhi-add.tsv`、`names-add.tsv`、目前載入的詞包），分數用檔案原分數（封頂與降權之前）；
   - 不蓋掉強的詞庫詞：這一串讀音下分數最高的詞庫詞要 ≤ `NEWWORD_SHADOW`（−6.5）；讀音下沒有詞庫詞也算符合；
   - 不是全由 `NEWWORD_DIGITS`（〇零一二三四五六七八九十百千萬億兩壹貳參肆伍陸柒捌玖拾佰仟）組成。
3. 已經學過的同一組（讀音、詞）：權重 ＝ 衰減後權重 + 1。
4. 同一次送出裡同一組（讀音、詞）只學一次，不論它出現在幾段或幾個子段。
5. **子段**（`NEWWORD_SUBSPANS`）：符合第 2 點的段落，另外學它每一個 2 到 4 字、含改選字、不全是數字字、而且讀音在詞庫裡完全沒有任何詞的連續子段（「進版控」學成整段，也學「進版」「版控」）。子段都在原本那一段裡，所以每次逐字改選，檔案裡最多出現改選處附近連續 6 個漢字（單字紀錄的 ≤ 2 字前文加 ≤ 4 字的新詞段）。
6. `NEWWORD_BREAK` ＝ 的了是在再嗎呢吧啊喔耶囉啦欸也而就和或與及跟你我他她它們這那有沒要會很都還又給把被讓。

### 8.3 存放與上限

- 和一般紀錄同一個 `learning.tsv`、同一套寫入、衰減、修剪、清除。一筆新詞的前文欄是 `#`：`#\t讀音\t詞\t權重\t日期`。
- `valid` 只收前文剛好是 `#`、詞是 2 到 4 個漢字、讀音音節數等於字數的列。標頭版本不升（`#shanjie-learning v1`）。
- `#` 永遠不會被一般的前文查詢比對到（`context_key` 不會回傳 `#`），也不算全域化的一種前文。
- 上限 `NEWWORD_CAP` ＝ 2,000 筆，和一般的 50,000 筆分開算：學到新詞時立刻在記憶體裡修剪（`learn_new`），載入與完整重寫時也修剪，所以記憶體裡永遠不超過 2,000 筆。兩次完整重寫之間追加的列可能讓檔案暫時超過。
- 舊版程式載入時，`#` 列因為前文欄對不上 `context_key` 被逐行略過；降回舊版後第一次完整重寫，它們從檔案消失。

### 8.4 解碼與候選窗

- 衰減後權重 ≥ 0.5 的新詞，和詞庫詞一起列舉在它的讀音上（讀音通常在詞庫裡一個詞也沒有，所以要另外列舉，最長 `NEWWORD_MAX` 個音節）。詞條分數（log10）：`newword_score(w) ＝ NEWWORD_S_MIN + (NEWWORD_S_MAX − NEWWORD_S_MIN) × w／(w + 1)`，`NEWWORD_S_MIN` ＝ −3，`NEWWORD_S_MAX` ＝ −1。語言模型那一項和詞包裡模型沒看過的詞相同；沒有 S4 加分、沒有降權。詞庫或詞包後來收了同一個詞時，解碼就不另外列舉。
- store 空、或沒有 `#` 列、或打的讀音沒碰到任何新詞時，解碼逐位元組不變。
- **候選窗**：新詞照讀音和詞庫詞一起列進候選窗，在同長度的詞庫詞前面；列的是所有權重（低於 0.5 的也列），所以 ⌘⌫ 找得到磁碟上還有的紀錄。
- **從候選窗選新詞**：照一般選字固定成 `Fixed`。`refresh_lm` 給固定的新詞的分數是它 `#` 紀錄的 `newword_score`，不看 0.5 門檻；紀錄不在了（被 ⌘⌫ 忘記、被清除），那一段解除固定、照未固定重新解碼，不回錯誤。選新詞不走一般紀錄的教學（`pre ＝ None`）。

### 8.5 用到與被改走

- **用到**：學習旗標在送出當下開著、這次組字沒暫停的送出，最後路徑上有新詞（不是詞庫詞）：權重 ＝ 衰減後權重 + 1，**每個新詞每個本地日最多一次**（紀錄的日期已是今天就不加、不寫）。沒有改選的送出也算；只更新既有 `#` 列，不建立新列。
- **被改走**：改選的範圍和路徑上某個新詞重疊時，該新詞權重減半。選到的字和改選前畫面上那一段相同（`pre ＝ 詞`）時畫面沒變，不算；同一段後來又改選回那個新詞不減半；新的改選蓋掉先前重疊的改選時，先前那些改選改走的新詞照樣在送出時減半（`Fixed.displaced_new`）。

### 8.6 忘記

在候選窗對新詞按 ⌘⌫：刪掉它的 `#` 列，一律完整重寫；開著的窗不再列它，選取停在補上它位置的那一項，窗裡只剩它時關窗。這段組字裡和該讀音任何出現位置重疊的固定段，待學的改選一律丟掉，否則畫面上的改選字會在下一次 Enter 把它學回來。⌘⌫ 只忘記新詞；同一次改選留下的一般單字紀錄不會跟著刪，要另外在候選窗忘記，或用清除。沒有「刪過的詞不再學」的排除表：排除表本身會把刪掉的詞留在檔案裡，和「忘記後檔案裡沒有該詞」衝突。

## 9. 忘記與清除

### 9.1 ⌘⌫ 忘記

- 候選窗開著時按 ⌘⌫（KEY 加 COMMAND 修飾、種類 BACKSPACE），刪除反白的那個詞在這個讀音下的所有紀錄：所有前文鍵（含 `^>…`、中鍵、細鍵與全域）、含新詞。同一段組字裡同讀音、同詞的待學選字也一併丟掉，否則下一次 Enter 會學回來。
- 刪完組字區重新解碼，回到沒學過時的輸出。固定詞：只有候選窗的 ⌘⌫，而且被遺忘的（讀音、詞）在這個範圍的前文下確實生效（精確或最後一字層含有它；句首時記錄的鍵帶著範圍後面那個音節；全域層加分是 0，不算）時，和候選範圍重疊的 `frozen` 固定詞才解除；不生效時只刪紀錄，固定詞不動。使用者自己選的固定詞照舊只清 `pre`。
- 有學習檔時，忘記**一律**完整重寫（不論記憶體有沒有變：載入時被修剪掉的紀錄可能只剩在檔案裡）；沒有 store 時只改記憶體，不動旗標。
- 反白在候選窗前面的預測項目時，照已進入預測列的 ⌘⌫：遺忘（讀音、詞）、重新解碼、重算預測列、清單重建成「新的預測項目去掉那個詞＋詞庫項目」，選取移到同一個詞的新位置（不在就是第一項），窗保持開著；重建後清單是空的就關窗，同 Esc。已進入預測列時的 ⌘⌫ 遺忘選取的那一項，不動任何固定詞。

### 9.2 清除

- `learning_clear`：清空記憶體、丟掉待學段落、刪除 `learning.tsv`、`learning.tsv.tmp`、`learning.tsv.corrupt`（檔案本來不存在算成功；每個檔都嘗試，一個失敗不影響其他）。保留目錄與它的備份排除旗標。
- 沒有 store（`learning_open` 沒成功過）時清除回 3，記憶體與待學段落照樣丟掉。其他刪除失敗也回 3。
- 清除一開始就設 `must_rewrite`；刪檔成功後把「上次完整重寫日期」設成無、再清旗標，所以下一次學習的 Enter 一定是完整重寫，從標頭開始建新檔，只含清除後學到的紀錄。
- 殼的清除流程見 11。

## 10. 儲存

### 10.1 路徑、格式、權限

- 路徑：殼傳入的學習目錄（App 內是 `~/Library/Application Support/shanjie/`，`Shell.learningURL()`）下的 `learning.tsv`；暫存檔 `learning.tsv.tmp`，隔離副本 `learning.tsv.corrupt`。不用 SQLite，沒有 `-wal`、`-shm`。
- 格式：第一行 `#shanjie-learning v1`；之後每行五欄，tab 分隔：`前文鍵  讀音  詞  權重  日期`。讀音的音節用 `-` 連接；欄位不含 tab、換行或其他控制字元（中鍵與細鍵的 `FINE_SEP` 是唯一例外，只在前文鍵欄）；讀音沒有空音節。不會讀回成同一筆的紀錄不寫出。
- 目錄建立時 0700；已存在時檢查是 symlink 就拒絕、不是目錄就拒絕、擁有者不是自己回 `Ownership`，權限不是 0700 就 `chmod 0700`。檔案與暫存檔、`.corrupt` 都是 0600。
- 檔案內容只有：前文鍵（≤ 2 個漢字、`^>` 加後面 1 到 3 個音節的讀音、空字串、`#`）、讀音、詞、權重、日期。核心沒有 logger，任何路徑與紀錄都不離開這個模組（除了作為資料）。
- Windows 版：這個型別編得過，但 `open` 在非 Unix 上直接失敗，沒有學習檔（見 14）。

### 10.2 讀取

- 用 `O_NOFOLLOW|O_NONBLOCK` 開檔：symlink 以 ELOOP 失敗（當作損毀、不讀）、FIFO 立刻開得起來（不是一般檔案就當損毀）。`fstat` 必須是一般檔案；超過 `MAX_BYTES`（16 MB）當作損毀，不讀內容。
- 標頭不對或超過 16 MB：整個檔改名成 `learning.tsv.corrupt`（只留一份、直接覆蓋，rename 後補 `chmod 0600`），從空的開始，回 0。檔案不存在：從空的開始，回 0。
- 壞行（欄位數不對、權重是 NaN／inf／負數、日期不合法或超過 9999-12-31、前文鍵不合法、新詞不合格式）逐行略過；同一組（前文鍵、讀音、詞）出現多行時，以最後一行為準（追加的結果）。
- `Opened` 回報 `Fresh`、`Loaded { skipped }`、`Corrupt`。載入後在記憶體修剪（5.6），檔案留到下一次寫入整理。

### 10.3 寫入

兩條路寫同一個檔，格式相同：

- **追加**（`append`）：每次送出且學習有變動時，只把這次改到的紀錄（教到的、被改選走而減半的、全域、新詞的學到／用到／減半，句首單字的中鍵與細鍵各一筆）用同樣的五欄格式加到檔尾。用 `O_WRONLY|O_APPEND|O_NOFOLLOW|O_NONBLOCK` 開**既有**的檔，不建立；`fstat` 必須是一般檔案、擁有者是自己、權限沒有群組與其他人的位元、`st_nlink ＝ 1`、檔案至少是標頭長度加 1（使用者手動清空的檔不追加，否則追加的內容下次載入因為沒有標頭整個被改名）、加上去不超過 16 MB。一次寫入，不做 fsync，只寫進一部分算追加失敗。任何一項不成立就改走完整重寫。
- **完整重寫**（`save`）：先修剪，再寫暫存檔（先 `unlink`、`create_new`、`O_NOFOLLOW`、`mode 0600`），寫完 `F_BARRIERFSYNC`（失敗改 `F_FULLFSYNC`），rename 取代。忘記用 `save_forgetting`：兩種 fsync 都失敗時照樣 rename（隱私優先於耐久）；其他情況 fsync 失敗算寫入失敗，不 rename。
- **什麼時候完整重寫**（`persist`）：忘記；`must_rewrite` 旗標是 1；上次完整重寫的本地日期不是今天；上次完整重寫後追加的行數會達到 `JOURNAL_MAX`（1,024）；追加失敗。上次完整重寫日期在開啟與清除成功後設為「無」，所以開啟後第一次寫入、清除後第一次寫入、每天第一次寫入都是完整重寫：載入時修剪、去重、略過的行與截斷的檔尾都在這次整理掉。
- **沒有東西被碰到**時不寫（例如單字配不合規則的鍵），除非有完整重寫在等（失敗過的重寫或忘記）；它由「學習開著而且有待學改選或有新詞紀錄」的送出重試，其他送出讓它繼續等。
- **沒有 store**（沒開檔或 Windows）：只修剪記憶體；學習只在這個工作階段的記憶體裡有效。

### 10.4 失敗狀態

- `must_rewrite`（只在引擎記憶體）：忘記與清除在改記憶體之前就設 1；每一次完整重寫開始前設 1；只有重寫的 rename 成功才清成 0（清除則在刪檔成功後清）。中途 panic 或失敗時旗標自然留在 1，所以下一次寫入不會改用追加。
- `learning_status` 的 bit0：上次**完整重寫**失敗（含追加失敗後改走的重寫）；只有成功的完整重寫或成功的清除才清成 0。bit0 是 1 時 `must_rewrite` 一定是 1。
- 忘記的重寫失敗時，`must_rewrite` 只活在這個引擎裡：換排列重建引擎或輸入法重啟後，會從還沒重寫的檔案把該詞載回來；選單會顯示寫入失敗，誠實反映「忘記沒寫進去」。
- `.corrupt` 隔離副本只有清除才會刪，忘記不碰它。
- 寫入失敗就放棄，不影響打字。

### 10.5 耐久性

- `write` 回傳後資料已交給作業系統，輸入法閃退不會丟；斷電或 kernel panic 可能丟掉最近一段時間的追加（未實測）。被截斷的最後一行在載入時略過，或變成同一筆較舊的版本；開啟後第一次寫入一律整檔重寫，所以截斷的檔尾不會和之後的追加黏在一起。
- 完整重寫用 barrier 讓資料先於 rename 落地，不會留下空的或半個取代檔。rename 與刪檔本身不保證耐久：忘記或清除之後不久斷電，舊檔可能回來。
- 殘留上限：載入時修剪掉的紀錄，最多留在檔案裡到下一次寫入；在當天完整重寫之後才跌破 `PRUNE_FLOOR` 的紀錄，最多留到隔天第一次寫入。

### 10.6 Time Machine 與揭露

- 預設會進 Time Machine 備份（換電腦時記憶跟著走）。選單「不要備份選字記憶」對目錄設 `isExcludedFromBackup`（設在目錄上，才不會因為清除刪檔而消失）。
- 清除只刪本機檔案；已經進 Time Machine 備份或本機快照的副本不受影響。
- 學習檔記錄使用者改選或從預測列選過的詞（可能包含人名）、前文最多 2 個漢字、讀音與日期；它不受 macOS 的隱私權限保護，靠 FileVault 加密。

## 11. 殼的介面

### 11.1 選單與設定

選單（`Session.menu`）與學習相關的項目：

- 暫停時的固定字串（3.3），在最上面。
- 「清除選字記憶…」：呼叫 `confirmAndClear`，設定視窗裡的清除按鈕走同一條。
- 「不要備份選字記憶」：勾選狀態是目錄的 `isExcludedFromBackup`；切換失敗時勾選維持原值。
- 「選字記憶無法存檔」（灰色，不可點）：`learning_open` 失敗，或 `learning_status` 的 bit0 是 1 時顯示。

學習檔路徑由 `Shell.learningURL()` 給（`applicationSupportDirectory/shanjie`），殼不建立目錄（核心在 `learning_open` 建 0700）。

### 11.2 清除視窗與揭露文字

清除用系統的 `NSAlert`（`LearningDialogs`），不阻塞輸入法；輸入法是 agent app，跳窗前把自己帶到前面、關掉後把焦點還給原本的 App。

- 標題「要清除選字記憶嗎？」；內文（`DialogText.clearMessage`）：「會刪除這台電腦上記住的改選、預測列選取，以及從逐字改選學到的新詞，之後要重新學。清除只刪本機檔案；已經進 Time Machine 備份或本機快照的副本不受影響。」
- 按鈕：「取消」是預設（Return），「清除」是破壞性動作。
- 清除失敗（回非 0）：另跳「清除選字記憶失敗」視窗，內文「有檔案沒有刪掉，請再試一次。」，不假裝成功。
- 所有固定字串不放 App 名稱、路徑或筆數。

## 12. C ABI

函式簽章與錯誤碼表見 `docs/spec/c-abi.md`；語意（`core/include/shanjie.h`）：

| 函式 | 行為 |
|---|---|
| `set_left_context(engine, utf8)` | 2.3；NULL 引擎回 1；不是 UTF-8 回 2 並清空 |
| `set_learning(engine, 0 或 1)` | 3.2；其他值回 2；引擎建立時預設 0 |
| `learning_open(engine, dir)` | 載入；目錄不存在就建（0700）；檔案不存在或損毀（改名成 `.corrupt`）都回 0 並從空的開始；NULL 回 1，不是 UTF-8 回 2，I/O 失敗或目錄不是自己的回 3；Windows 上有效的呼叫回 3、不碰檔案系統 |
| `learning_clear(engine)` | 9.2；失敗或沒有 store 回 3 |
| `learning_status(engine, &flags)` | bit0 ＝ 上次完整重寫失敗（10.4） |

這些函式都不改目前的顯示；忘記與清除可能在下一個按鍵重新解碼。新詞沒有新函式。⌘⌫ 是一般按鍵（種類 BACKSPACE、修飾 COMMAND），沒有候選窗時放行。

## 13. 驗證

### 13.1 核心測試

指令（需要 `data/lm/bigram.sjlm`，與其他引擎測試相同）：`make test-rust`（等於 `cargo test --release --locked`）；單跑：`cargo test --release --locked -p core --test engine_learn`、`--test learn_store`、`--test engine_newword`、`--lib learn`。

- `core/src/learn.rs` 的單元測試：中鍵、細鍵的建構與格式（`the_mid_key_is_two_syllables_and_nothing_else`、`the_fine_key_is_three_syllables_or_two_and_the_end`）、單字教中鍵與細鍵（`teach_takes_the_mid_and_fine_keys_for_a_single_character`）、`context_key_is_the_han_tail`、新詞不被前文查詢比對（`new_words_never_answer_a_context_lookup`）、新詞不算全域化的前文（`globalization_does_not_count_the_new_word_record`）、新詞權重與上限。
- `core/tests/engine_learn.rs`：
  - 學習率：`a2_candidate_pick_learning_on_cases_tsv`（`eval/learn/cases.tsv` 的 14 組，正式路徑按鍵重播）與 `a2_mirror_teach_the_other_word`（在教學句的同一位置改選配對的另一個詞），同前文學會率 ≥ 80%；常用詞句在不碰撞的列 0 退步。
  - 前文一致：`context_key_learned_equals_context_key_queried`（前一個詞是單字、接在固定詞後、標點後、句首，學習寫入的鍵等於解碼查的鍵）。
  - 超出 `PER_KEY`：`learned_word_beyond_per_key_is_enumerated`；空 store 逐位元組不變：`empty_learner_matches_goldens`。
  - 衰減、減半、修剪、忘記：`decay_halves_every_fourteen_days_and_never_grows_back`、`repick_halves_the_displaced_word_and_adds_to_the_new_one`、`prune_drops_decayed_records_and_caps_the_total`、`forget_removes_every_key`、`command_backspace_forgets_the_highlighted_word`。
  - 送出路徑：`row_enter_learns`、`row_rule21_passthrough_commit_learns`、`row_max_syllables_commit_learns`、`row_reset_commit_and_discard_do_not_learn`、`row_esc_does_not_learn`、`row_decode_failure_does_not_learn`、`row_punctuation_replacement_does_not_learn`、`row_flag_must_be_on_at_pick_and_at_commit`；panic 不吃字：`learning_panic_keeps_the_commit`；前文送出後清掉：`left_context_is_dropped_after_commit`。
  - 單字與全域：`single_character_never_globalizes_two_characters_do`、`old_single_character_global_record_is_dropped_at_load`、`pollution_zai_taught_under_two_contexts`、`single_character_rules_in_the_learner`、`single_character_pick_stays_where_it_was_taught`、`global_eps_table`（全域 ε 掃描，ε ＝ 0 時全域污染 0 退步）、`zero_global_eps_keeps_beyond_per_key_words_out`。
  - 句首鍵：`rule8_*`（句首教 `我試`／`我再` 不影響其他句、同一個下一音節仍加分、`Learn.after` 取右邊固定詞或標點的音節）、`rule7_single_character_after_one_han_character_is_not_learned`、`repick_halves_the_displaced_word_under_the_pick_time_key`、`sentence_start_keys_count_as_one_key_for_globalization`、`bare_caret_word_leaves_other_sentences_alone`。
  - 儲存（`store_*`）：寫檔後重開讀得回來、忘記後不會學回來、學習關著不寫、寫入失敗設狀態旗標且保留 commit、重寫失敗後忘記、忘記中途 panic、100 天不重開與重開都修剪、截斷檔尾不黏、追加只多改到的筆數並在 `JOURNAL_MAX` 改完整重寫、追加被拒時退回完整重寫。
  - 句首單字的 `sinit_*` 見 13.4。
  - 效能：`perf_full_store_per_key_and_enter_with_write`（50,000 筆的 store，每鍵與「帶改選的 Enter（含追加寫檔）」p95 都要 < 16 ms；開啟後第一次寫入的完整重寫另外報）。
- `core/tests/learn_store.rs`（只用暫存目錄）：權限（目錄 0700、檔案與暫存檔與 `.corrupt` 0600）、擁有者不符與 symlink／FIFO 不卡住、原子寫入失敗不留半個檔、壞行逐行略過、標頭不對改名、超過 16 MB、追加兩次重開只剩後寫的一筆、`records_that_would_not_read_back_are_never_written`、`single_character_global_records_are_not_loaded_or_written`、`synthetic_keys_round_trip_and_old_records_are_dropped`、`mid_and_fine_keys_round_trip_and_malformed_ones_are_dropped`、`new_word_records_need_two_to_four_han_characters_one_syllable_each`、`old_versions_skip_new_word_lines`、`new_words_have_their_own_cap_and_decay`。
- 預測列：`core/tests/engine_predict.rs` 的 `a_prediction_pick_is_learned_like_a_repick`、`t3_a_single_character_needs_a_han_context`、`t3b_a_sentence_start_pick_is_stored_under_the_next_syllable_and_reorders_the_row`、`t3c_…`（選了再繼續打仍存 `^>$`）、`t16_*`（預測列選取存在選取當下的鍵，前面插入或刪除音節時換成送出時的鍵）、`t17_choosing_the_same_word_again_in_the_window_keeps_the_prediction_learn`；`core/tests/engine_rerank.rs` 的 `set_learning_keeps_the_job_with_either_value`（`set_learning` 不讓重排工作過期，兩種起始值）。

### 13.2 新詞測試與量測工具

- `core/tests/engine_newword.rs`：功能字切開、2 到 4 個音節、詞庫已有不學、強詞庫詞擋學與弱詞庫詞不擋（`a_strong_lexicon_word_blocks_the_new_word`、`a_weak_lexicon_word_does_not_block_the_new_word`、`a_names_layer_word_blocks_the_new_word`）、數字不學、沒改選不學、關著學習不學、reset 與 Esc 不學、暫停旗標（`a_pause_during_the_composition_blocks_new_words`、`a_key_handled_while_learning_is_off_marks_the_composition`、`the_pause_goes_when_backspace_empties_the_composition`）、`store_writes_the_new_word_line_and_reopen_restores`、候選窗順序與選新詞、紀錄不在時解除固定、用到每日一次、改走減半、子段只學一次、忘記後檔案沒有該詞且不會學回來、清除後沒有 `#` 列、`r2_marker_characters_on_disk`（用一串詞庫沒有的字當標記，檔案裡標記的字只出現在含改選字的 ≤ 4 字段與它前面 ≤ 2 字，沒改選的送出不新增含標記的列）、`perf_two_thousand_new_words_per_key`（2,000 筆新詞時每鍵 p95 比沒有新詞多不到 1 ms）。
- 量測工具：`measure_newword`（`#[ignore]`）。環境變數 `NEWWORD_MODE`（`base` 只有一般紀錄、`new` 加新詞）、`NEWWORD_PROFILE`、`NEWWORD_TEACH`、`NEWWORD_REPLAY`、`NEWWORD_OUT`（逐列結果）；同一個引擎用記憶體 store 依序教每一列，再關掉學習重播。`NEWWORD_DUMP`（逐列顯示與正解）與 `NEWWORD_SHOW`（學到的新詞）的輸出含句子文字，只留在跑的那台機器、用完刪掉，不進 repo。公開的探針與邊界集合在 `eval/learn/newword-probe.txt`、`newword-probe-boundary.txt`、`newword-boundary-teach.txt`。

### 13.3 殼測試

指令：`make test-swift`；`macos/Tests/ShanjieKitTests/LearningTests.swift`：

- 前文：只留尾端漢字、範圍至多 256 單位、最後換行截斷、64 個 grapheme、截過的讀取丟第一個 grapheme、過長回覆先夾住、每個按鍵只讀一次 bundle ID、終端機只給漢字尾巴、沒有插入點或有殘留 marked text 時沒有前文、空的 marked range 在任何位置都照讀、組字期間不再讀、閘門擋時不讀。
- 閘門：安全輸入、denylist、未知 App 都暫停；選單第一項固定字串；組字中途閘門打開或擋住時存的紀錄為 0；reset 路徑在安全輸入下為 0；換排列重建引擎後學習重新打開；滑鼠點選暫停時不學、未暫停時學。
- 清除：先跳視窗、內文提到新詞、失敗要顯示、清除後檔案與記憶消失目錄還在、備份排除旗標維持原值、核心清除失敗與寫入失敗都要顯示。
- `testAppLearningDirectoryIsApplicationSupportShanjie`。

### 13.4 句首單字（SINIT）的測試與重播

- 選字函式（`core/src/tinylm.rs`）：學習項全 0 時三條路與沒有學習項逐一相同（隨機分數，固定種子的 LCG）；細鍵正負兩向、中鍵與粗鍵受 `ll_i > ll_0` 門檻、錨點學過候選沒學時是 0、只用最長一層、τ 的三條路各一例。
- `core/tests/engine_learn.rs` 的 `sinit_*`：沒有重排工作時紀錄沒有作用（`sinit_row16_without_a_rerank_job_the_record_has_no_effect`）、改選取代的單字在改選當下的中鍵與細鍵下減半、兩個漢字的前文後面不作用且下一個音節不同時解碼和沒有紀錄時相同（`sinit_not_after_two_han_characters_and_not_before_another_syllable`）、單字在最後不學（`sinit_single_character_at_the_end_is_not_learned`）、英文與數字鄰居不學、規則 10 的紀錄不觸發守門、池是不抬升的 n-best 且換進的路徑帶不抬升的分數、`sinit_at_eps_0_a_record_changes_nothing_and_a_large_eps_takes_it`、`sinit_the_job_reads_the_longest_layer_that_has_a_record`、`sinit_mid_and_fine_keys_are_learned_by_what_follows_and_forgotten_with_the_coarse_one`、模糊音路徑上的學到字不改變結果、`sinit_taught_zai_does_not_pollute_zai_jia_xiu_xi`（不污染）、`sinit_dai_wo_chu_xi_hui_yi_does_not_take_dai_wo_chu_qu_wan`（細鍵分得出第四個音節不同的句子）、`sinit_learned_once`（出貨模型下錯字回報的幾列教一次就對）。
- `shanjie-replay`（逐列逐鍵經真引擎重播）：`--learn off|on`、`--learn-file FILE`、`--rerank-level conservative|standard|aggressive`（只用於評測，三個常數不隨檔位改）、`--sinit-stretches FILE`（規則 10 各段的 `Δng` 與 `α·Δll`）；`--learn on` 時 stderr 有 `sinit:` 計數行（`stretches`、`swaps`、`missing`、`overturned`、`tau_opened`）。指令與評測方式見 `docs/spec/evaluation.md`。

### 13.5 這份規格沒有自動測試的部分

沒有自動測試：denylist 各 bundle ID 是否為真實的 App（未在實機逐一核對）；斷電下的耐久性（未實測）；Time Machine 實際是否收進學習檔；`mdfind` 是否索引學習檔；組字中焦點移到密碼欄時組字送到哪個 client。

## 14. 已知限制

- 一個漢字的前文後面改選單字不會被記住；句首學到的詞只在後面接同一個音節時加分（句首單字則有三層鍵，7）。
- 2 字以上的詞在「最後一個字」那一層仍會在其他前文的結尾字相同時加分（例如 `我再` 也會在前文結尾是「天」的位置生效）。
- 全域層的加分是 0：全域紀錄照寫照存，但不影響選字。
- 句首單字的細層與中層需要段內至少三個音節；兩個音節的段只有粗層。
- 句首單字在沒有重排工作時沒有作用（7.6）。
- 同鍵的兩字詞分不出來（7.7）。
- 容錯讀音（模糊音、相鄰鍵）找到的詞不列舉超出 `PER_KEY` 的學習詞；句首單字學到的字也不走模糊音路徑。
- 前文：終端機類 App 只給漢字尾巴；內建終端機的編輯器因為 panel 拿到的是編輯器的 bundle ID，不在終端機清單內。
- 學習 denylist 的 bundle ID 未逐一在實機核對；只列了密碼管理器，沒有列其他密碼類 App。
- 斷電可能丟掉最近的追加；忘記或清除之後不久斷電，舊檔可能回來。
- 忘記的重寫失敗後，`must_rewrite` 只在該引擎裡，重啟或換排列後該詞會從檔案載回來（選單顯示寫入失敗）。
- Windows 版沒有學習檔，學習只存在工作階段的記憶體裡。
- 追加路徑一次送出最多寫入的筆數可超過 3 筆：句首單字最多同時寫粗、中、細三筆加三層的減半，新詞每個學到、用到、減半各一筆；`JOURNAL_MAX` 計的是實際追加的行數，不是送出次數。

## 15. 設計取捨

### 15.1 前文與鍵

- 前文鍵只用 ≤ 2 個漢字：檔案能含的內容越少隱私面越小，並且學習端與解碼端共用同一個函式才能保證鍵一致。前文重現項不寫檔、不進鍵，所以這條不變。
- 句首鍵帶下一個音節：不帶時，句首教的 `我試` 會在所有標點後、英數後與讀不到前文的位置生效（`^` 涵蓋太廣）。
- 單字限制（5.3）：「再」在「可以」與句首各學一次，產生的全域紀錄會讓所有位置的 `ㄗㄞˋ` 偏向「再」，連「現在」都被拆開。
- 全域加分用過 0.5 到 6.0 各值：和詞類項並用時，從 0.1 到 0.5 每個值都讓至少一句正確的常用句變錯，所以全域層加分是 0。

### 15.2 數值

- 半衰期 14 天：教一次大約兩週有效，常用的詞靠再次改選保持；`PRUNE_FLOOR` 0.05 約 60 天沒再用到，權重 1 的紀錄在那之前還能累積。
- `LEARN_EPS` ＝ 6.0：在 `eval/learn/cases.tsv` 的鏡像測試上，2.0 學會 4／10、4.0 學會 8／10、6.0 與 8.0 都是 9／10，取最小的 6.0。
- 句首單字的三個常數（4、4、2）：由離線投影選出；單用下一個音節的鍵分不出「再加一」與「在家睡覺」，所以粗層只在小模型也偏向時抬升、數值只有中層的一半；兩個音節的鍵分不出前三個音節相同的句子（「代我出席會議」與「帶我出去玩」），所以細層看三個音節，中層受模型門檻。
- 句首單字的紀錄不直接加在 n-gram 分數上，而是只把候選送進池、偏好交給融合分數：單字拿一般加分時，同鍵的常用句（「在寫報告」「在找鑰匙」）被拉走。

### 15.3 新詞的參數

- 分數範圍 `[−3, −1]`：在三個候選（`[−5,−3]`、`[−4,−2]`、`[−3,−1]`）中，三個在調參集合（聊天與書面各兩格）上都通過否決檢定、淨值 ≥ 0 與「不蓋掉強詞庫詞」；取四格淨值總和最大的。在私人的調參集合上選，不公開。
- `NEWWORD_SHADOW` ＝ −6.5：疊加層由維基來源產生的列都落在 [−7.67, −6.61]，基底底部三個沒計數的眾數落在 −7.47 到 −7.04，這些不擋；手動加的詞（−6.29 到 −5.43）與名單層全部（−5.54 到 −3.75）高於 −6.5，會擋，學到的詞因此不會蓋掉手動加的詞與名單裡的名字。這個值是看過被蓋掉的 16 列之後定的（樣本內）。
- `NEWWORD_BREAK` 是事前模擬用的那一張表，沒有在調參集合上掃過其他表。
- 沒有改選的送出不新增任何字，「用到」每個新詞每日最多一次：寫檔頻率降到每詞每天最多一次，檔案透露的時間精度和日期欄相同，解碼器自己放上路徑的碎片也不會無限自我強化；常用的新詞不會在 60 天後被修剪掉，打錯學到的碎片則會被改走而淡掉。
- 「每次送出都學」的對照量測：317 句原本就對的句子裡，80 句會學進碎片，弄壞 3 到 6 句，所以只在有改選時學。

### 15.4 儲存

- 就地追加而非每次整檔重寫：50,000 筆時整檔序列化約 7 ms、合法性檢查約 3 ms、`F_FULLFSYNC` p99 35 ms；追加 1 到 3 行（開檔、`fstat`、寫、關）p95 ≤ 0.2 ms。
- 完整重寫用 `F_BARRIERFSYNC`（p50 0.9–1.6 ms）而非 `F_FULLFSYNC`：學習檔需要「不壞、閃退不丟」，不需要「斷電也不丟」。
- 容量 50,000 是防爆上限；權重 1 的紀錄約 60 天降到修剪門檻以下，穩定狀態約等於每天不同的改選數乘 60（推算，未實測）。
- 隱私優先於耐久：忘記時兩種 fsync 都失敗仍然 rename。
- 預設備份進 Time Machine：換電腦時記憶跟著走；選單可排除。
