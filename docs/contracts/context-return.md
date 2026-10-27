# 契約：前文回傳（context-return）＋英文前文第二段

狀態：**過審**（2026-10-25 收尾審查 READY；實作時補記兩條：Esc 只清未完成符號時的殘留、空組字區不出英文預測列）。修訂三（第二次 REVISE 一項處置：先去掉被切斷的第一個 grapheme，再找換行與 NUL，和今天的 `han` 同順序；之後收尾審查）。修訂二（plan-verifier 第一次 REVISE 五項：宣告 L 時模型實際收到的前文、英文情境的比對基準、english-next 核心是前提、延遲的量法、PLAN 第 37 行）。修訂一（security-reviewer 實作前審查：沒有 P0／P1，P2 四項、P3 六項全部採納，處置在 §6；P2-1 由維護者選擇題決定「終端機只收漢字」）。下一步 plan-verifier。

## 0. 起因、已定的事、要做到什麼

- 維護者 2026-10-20 的決定（研究紀錄同日、`docs/iteration-plan.md`）：前文回傳獨立成一片、接在 v0.5.0 之後：殼一律交給核心**同一則訊息、跨標點、最多 64 字**的前文，核心依**模型宣告的前文長度**截斷；現在的 `.sjtl` 檔頭只有模型視窗、沒有「使用的前文長度」，所以檔頭加一個前文長度欄位，沒有這個欄位的現有模型（含出貨的會意 SJ-SR-6-14M）視為 2 字，選字逐列不變；前文只在記憶體、不寫檔、不記 log、安全輸入期間不讀；PLAN R4 的文字跟著改；這片要過 security-reviewer。
- 英文前文第二段（`docs/contracts/english-next.md` §0）：App 裡已經送出的英文（例如先送出「Joe」、新開組字區再打 ㄕ）要能用英文前文表；殼今天只傳漢字，所以和這片同一個讀取點一起做。核心另存一個欄位，不動 `self.left`。
- 維護者 2026-10-25（選擇題，security-reviewer P2-1）：**終端機類 App 只收漢字**（照今天），其他 App 照 64 字。
- **要做到**：(1) 殼傳的前文從「結尾 ≤ 2 個漢字」改成「同一則訊息、最多 64 個 grapheme、原文」（終端機類除外）；(2) 有宣告前文長度的會意模型讀到那麼長的前文；(3) 現行模型與 n-gram、學習、選字逐位元組不變；(4) 前文結尾是英文詞時，英文前文表在新組字區的游標起點也生效；(5) 核心只留用得到的長度（資料最小化）。
- **怎麼看到**：§4 的測試與逐列比對；實機（維護者）：在「訊息」或文字編輯先打「Joe」送出，新開組字區打 ㄕ，預測列有表裡 joe 後面常接的詞（英文前文表出貨之後）；在終端機做同樣的事，預測列和今天相同；平常打幾句，選字和今天相同。

## 1. 殼：讀什麼（取代 R4 的長度規則）

- **讀取點與條件不變**：組字開始讀一次；`privacyGate`（安全輸入、denylist、取不到 bundle ID）擋的時候不讀、傳 NULL；有 marked range 時不讀（`leftContext()` 現有的判斷）。
- **終端機類 App**：bundle ID 在一份具名清單裡（`com.apple.Terminal`、`com.googlecode.iterm2`、`dev.warp.Warp-Stable`、`net.kovidgoyal.kitty`、`com.github.wez.wezterm`、`org.alacritty`、`co.zeit.hyper`、`com.mitchellh.ghostty`；清單放在 `LeftContext` 旁、註解寫出處；編輯器整個 App 不算，因為它的終端機面板和編輯區是同一個 bundle ID〔已知限制，記進範圍外〕）：照今天只傳結尾 ≤ 2 個漢字（`LeftContext.han`）。
- **其他 App 讀多少**：`LeftContext.maxUTF16` 從 32 改成 256 UTF-16 單位（64 個 grapheme 最多約 128 個單位，留餘裕給組合字元與 emoji；`oc-experimental.md` 寫的 128 一併改成 256）。取回的字串：
  1. **先夾住**：App 回傳的字串比請求的長時，只取最後 `maxUTF16` 個 UTF-16 單位，並當作被截過（P3-2）。
  2. **被截過時，先**在整段（夾住之後的）讀取上去掉開頭可能被切斷的第一個 grapheme（照現有 `han(_, truncated:)` 的順序：先去掉、再往回找）。
  3. 從最後一個換行之後開始，換行用 Swift `Character.isNewline`（含 `\n`、`\r\n`、U+2028、U+2029、VT、FF、NEL；「同一則訊息」＝同一行；R4 的「最後換行截斷」）。
  4. U+0000 當成換行（從最後一個 NUL 之後開始），不刪除（刪除會把 `中\0文` 接成「中文」、改變 `self.left`）（P3-1）。
  5. 以 grapheme（Swift `Character`）計數，留最後 64 個；不濾掉標點、空白、英文、emoji（「跨標點」）。
- **傳給核心**：上面那段原文（UTF-8；NUL 已在第 4 步排除）。`cjk-spacing` 用的 `last` 照舊從同一次讀取取。
- **只在記憶體**：不寫檔、不寫偏好、不記 log（行為測試見 §4，不用掃原始碼的方式）。

## 2. 核心

- `set_left_context(text)`：
  - `self.left` 照舊是 `context_key(text)`（結尾 ≤ 2 個漢字）。n-gram 解碼的歷史詞、選字記憶的鍵、預測列的 `key`、數字層都照舊只看它，所以這些逐位元組不變。
  - 新欄位 `self.left_full`，**只留用得到的長度**（P2-2）：
    - 載入的會意模型宣告了前文長度 L：`text` 的最後 L 個 Unicode 純量值；
    - 否則，`text` 的結尾（去掉最多一個半形空白後）是 2–32 個 ASCII 字母、前一個字元不是字母或數字：只留那個英文詞（和它後面的一個空白）；
    - 都不是：空字串。
    - 截斷一律用 `chars()`（不用位元組切片，P3-5）；英文結尾的判斷只看 ASCII。
  - 不是 UTF-8 時照今天回 2 並清空（`self.left` 與 `self.left_full` 都清）。
  - **清除**：`clear_all`（送出、Esc、`reset`、回傳碼 4、`MAX_SYLLABLES` 自動送出都經過它）清 `self.left` 與 `self.left_full`；第 22 條的直通（空組字區上沒處理的字元鍵）之後組字區是空的，也清（P3-4）。
  - **不外流**（P2-3）：`left_full` 不經 C ABI 回傳給殼、不進 `ShanjieOutput`、不寫學習檔、不跨程序、不給任何網路功能；`RerankJob` 只帶最後 L 個字元（沒有宣告 L 時照今天帶 `self.left`）。
  - **接受的殘留**（寫明）：已經排進佇列的 RerankJob 複本活到那次執行結束（殼最多一個在跑、一個在等）；Rust `String` 與 Swift 字串釋放時不清零。另外，只有未完成符號時按 Esc 走 `clear_pend`、不經過 `clear_all`，`self.left` 與 `left_full` 留到下一次組字開始的 `set_left_context`（今天的 `self.left` 也是這樣；實作時記下）。
  - 起點 0、組字區完全空（沒有音節也沒有未完成單位）時不出英文前文的預測列（Backspace 刪光之後不在空組字區冒出一列；今天這個起點本來就是空的）。
- **會意（tinylm）的前文**：
  - `.sjtl` 新版本 SJTL0005＝SJTL0004 加一個 u32 欄位「前文長度 L」（字元數，1 ≤ L ≤ ctx − 2；越界載入失敗）；舊版本（≤ 0004）當作沒有宣告。
  - 有宣告：每個重排段送給模型的前文（`RerankJob::run` 算的 key、`Choice.key`）＝`left_full`＋組字區在這一段之前的顯示（含前面各段已選的字）接起來之後的最後 L 個字元（`chars()`），**不經過 `context_key`**；詞表外的字照模型的 UNK 規則；「^」也照原文（不當成 `context_key` 的哨兵，空的 k 只用在 ≤ 0004）。模型實際看到的是其中最後 min(L, ctx − 1 − 候選字數) 個字元：`[BOS]`＋前文＋候選放不下時，`ll_many` 先丟前文最舊的字（`core/tests/tinylm_tiny.rs` 的 `a_long_candidate_sees_only_the_tail_of_a_declared_key`）。評測 CLI 用同一個函式（`TinyLm::key_for`）。
  - 沒有宣告（現行出貨的 SJ-SR-6-14M）：照今天，`context_key(self.left + 組字區在這一段之前的顯示)`。
- **英文前文第二段**（`english-next.md`）：`english_before(s)` 在 s 是 0 時看 `left_full` 是不是英文詞（上面第二種情形，或宣告 L 時 `left_full` 的結尾符合同一規則）；`left_full` 被截過、而且英文詞從截斷後的第 0 個字元開始時不算（可能是半個詞，P3-6）。組字區裡有單位時照第一段（看單位）。
- C ABI：`shanjie_engine_set_left_context` 的簽名不變；`core/include/shanjie.h` 的註解改成「同一則訊息最多 64 個 grapheme 的原文；核心只把結尾 ≤ 2 個漢字用在解碼與學習，另外只留宣告的前文長度或結尾的英文詞給會意模型與英文前文表；不是 UTF-8 回 2 並清空」。`docs/contracts/s3a.md` 的前文說明跟著改。

## 3. 文件（同一個 PR 一起改，grep 全部出處）

- `docs/PLAN.md`：
  - 第 37 行（已定的架構決策裡的前文規則）：照 R4 同樣改（終端機類只收漢字、核心只留用得到的長度），讀取的 API 照實寫 `attributedSubstring(from:)`。
  - R4（第 920 行一列與第 721 行）：長度改成「同一則訊息、最多 64 個 grapheme（終端機類照舊只收漢字）、核心只留用得到的長度」，其餘（最後換行截斷、grapheme 計數、只活在記憶體、多行與 emoji 邊界測試、排除 marked range）不變；並寫明 R4 的 64 字只適用程序內。
  - R3（第 919 行）的處置文字：終端機照舊不擋學習的理由改成「終端機類 App 的前文只收漢字（context-return.md）」。
  - S6（第 57 行雲端送出的「截斷後的同一行左文」、第 742 行）與 R4 一列的「S6 只沿用」：改成「S6 雲端自訂上限，重新過 security-reviewer」。
- `docs/contracts/s4-learning.md` §2（第 91–95 行）與 §6 驗收 11（第 213 行的「≤ 32」）、`s2h-left-context.md`（第 20、22 行）：加註殼現在傳 64 個 grapheme（終端機類除外），核心在學習與 n-gram 只用結尾 ≤ 2 個漢字。
- `docs/contracts/oc-experimental.md`（第 127 行的 `maxUTF16` 32→128）：改成 256，並註明 0c 讀 `left_full` 要另過 security-reviewer（含 XPC 跨程序）。
- `site/index.html`（第 368 行的前文說明）與 README 若有提到前文長度：同步。

## 4. 驗收

- **殼測試**（`make test-swift`）：
  - 最後換行截斷：`\n`、`\r\n`、U+2028、VT、NEL；NUL 當換行（`中\0文` 傳「文」）。
  - 65 個 grapheme 留 64；emoji（ZWJ 序列、膚色修飾）與組合字元各算 1；4 位元組的字（`𠮷`）在第 64 個的邊界上。
  - 讀取範圍從中間開始時第一個 grapheme 去掉；假 client 回超過 `maxUTF16` 個單位、結尾是「…\n你好」與「…\0你好」時，傳給核心的原文都是「你好」，`LeftContext.han` 對同一次讀取也是「你好」；假 client 回 1 MB 沒有換行的字串時，處理的長度 ≤ `maxUTF16`，量一次延遲。
  - 終端機類 bundle ID（清單裡每一個）傳的是 `LeftContext.han` 的結果；清單外的 App 傳原文。
  - `privacyGate` 擋的時候傳 NULL；有 marked range 時不讀。
  - **log 的行為測試**（取代原本掃原始碼的那條，P2-4）：擴充 `macos/Tests/ShanjieKitTests/LogTests.swift` 的 `testShellLogsNothingButStaticTextAndCodes`：前文放「ASCII 隨機標記＋空白＋英文詞＋漢字」、長度超過 2 個漢字，負面標記加上那個 ASCII 標記與英文詞，chat 那一輪也帶前文跑一次重排。
- **核心測試**：
  - `set_left_context("……好")` 與只傳「好」：解碼、候選、預測列、學習鍵逐位元組相同。
  - **模型實際收到的前文**：載入 SJTL0005（L ＝ 8）跑一個有兩個重排段的 job，`RerankResult::keys()` 等於兩段各自「`left_full`＋前面的顯示」的最後 8 個字元；載入 SJTL0004 時等於今天的 `context_key` 值；L 越界載入失敗。
  - `set_left_context("我說Joe")`、組字區空著打 ㄕ：預測列有英文前文表裡 joe 的詞；`"我說Joe"` 與 `""`（今天殼對這段文字傳的就是空的：`LeftContext.han("我說Joe")` 是 nil）的解碼與重排輸入逐位元組相同；`"Joe "` 生效，`"Joe  "`、`"Joe1"`、`"J"` 不生效；被截過而從第 0 個字元開始的英文詞不生效。
  - 學習檔：`set_left_context("我說Joe")` 時從預測列選一個詞並送出，學習檔和 `set_left_context("")` 時逐行相同、沒有 `joe`；前文帶 ASCII 標記、改選後送出，`r2_marker_characters_on_disk` 斷言學習檔沒有那個標記。
  - 清除：送出、Esc、`reset`、回傳碼 4、直通之後 `left_full` 是空的。
  - `r2_rerank_run_panic_is_silent`（`core/src/ffi.rs`）注入的 payload 加上 `left_full`，panic 仍不印任何內容。
- **延遲**（停止條件用）：在這台 Mac、release 建置、閒置窗（load < 2），殼的 XCTest 量「`leftContext()`＋`setLeftContext`＋第一個 `engine.key`」的時間，假 client 回一段 64 字的中英混合原文，重複 1,000 次取 p95；同一個 session 先在 main 量一次、再在分支量一次。分支的 p95 比 main 多 2 ms 以上就停。兩個數字記進研究紀錄。
- **逐列不變**：dev302、打字測驗、錯字回報（聊天、書面）、discordtune 的 `top1_sha256` 和 main 相同；golden 不變。
- **security-reviewer**：實作前的審查與處置在 §6；實作後 fresh verifier。
- **合併關卡**：程式改動（`/code-review`＋fresh verifier＋CI＋188 WSL 完整 Rust 測試＋`make test-swift`）。
- **實機**（維護者）：§0 的三個情境。

## 5. 順序、停止、範圍外

- **順序**：plan-verifier → **english-next 的核心 PR（#286：`english_before`、英文前文表、游標起點的查詢）先合併** → 這一片從含它的 main 開分支，實作（`pilotfish:security-executor`，因為改到殼讀 App 文字）→ 審查與驗證。英文前文表的資料檔可以在這片之前或之後出貨（沒有表時英文規則不生效，測試用小表）。
- **停止**：plan-verifier 第二次 REVISE 後仍有阻擋項；任何逐列比對不同；§4「延遲」那一項分支比 main 多 2 ms 以上。
- **範圍外**：訓練會讀長前文的會意模型（之後的研究片）；跨訊息、跨行的前文；把長前文用在 n-gram 解碼或學習；0c 開關讀 `left_full` 與 S6 雲端（各自另過 security-reviewer）；編輯器內建的終端機面板（和編輯區同一個 bundle ID，分不出來）；密碼管理器 denylist 的補齊（既有缺口，另案）。
- **限制**：executor 不安裝 App、不啟動 App、不呼叫 TIS 或 lsregister、不在 `.app` 裡執行、不碰 `~/Library` 與鑰匙圈、不 push、不用 GCP；Mac 只跑改到的測試檔。

## 6. security-reviewer 的發現與處置（2026-10-25，實作前；沒有 P0／P1）

| 項 | 發現 | 處置 |
|---|---|---|
| P2-1 | R3 當初放行終端機的前提（前文只收漢字）被拿掉：終端機與編輯器同一行的 ASCII 內容會進記憶體 | 維護者選擇題：終端機類 App 只收漢字（§1）；PLAN R3 的處置文字改寫（§3） |
| P2-2 | 核心存了用不到的 64 字，和「依模型宣告截斷」不一致 | `left_full` 只留宣告的 L 或結尾的英文詞；RerankJob 只帶 L 個字元（§2） |
| P2-3 | R4 放寬會順帶放寬 S6 雲端與 0c B 片（XPC） | 寫明不外流、不跨程序；R4 的 64 字只適用程序內；S6、0c 各自另審（§2、§3、§5） |
| P2-4 | log 防護原本是掃原始碼；現有行為測試的前文只有漢字 | 改成擴充 LogTests 的行為測試（§4） |
| P3-1 | 刪 NUL 會改到 `self.left` | NUL 當換行（§1） |
| P3-2 | App 回傳超長字串沒有夾住，主執行緒 O(n) | 先夾到 `maxUTF16`，1 MB 測試（§1、§4） |
| P3-3 | 換行集合不完整 | `Character.isNewline`（§1） |
| P3-4 | 清除只寫了部分時機；直通後殘留 | 寫成在 `clear_all` 清、直通後也清；殘留寫明（§2） |
| P3-5 | 位元組切片可能 panic、訊息帶內容 | 一律 `chars()`，`𠮷` 邊界測試（§2、§4） |
| P3-6 | 學習檔與重排輸入的測試缺口；截斷後的半個英文詞 | 學習檔逐行比對與標記測試；截斷後第 0 個字元開始的英文詞不算（§2、§4） |
