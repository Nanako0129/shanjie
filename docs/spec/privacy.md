# 隱私

這份寫的是現在的行為。

輸入法看得到使用者按的每一個鍵與游標前的文字，所以這份把「什麼會被記、什麼會被送出去、什麼絕對不會」逐項寫成可以測的規則。學習檔的內容與演算法見 `docs/spec/learning.md`；前文如何影響選字見 `docs/spec/decoder.md`；沙盒、簽章與更新的機制見 `docs/spec/install-and-update.md`；C ABI 的回傳碼見 `docs/spec/c-abi.md`。

## 1. 範圍與總則

- 輸入法程序（`善解輸入法.app/Contents/MacOS/shanjie`）會碰到的使用者資料只有：組字與候選、游標前的文字、前景 App 的 bundle ID、學習檔、偏好設定。
- 目前的程式裡沒有任何雲端功能、帳號、遙測，也不讀剪貼簿、不發 Apple events、不建 XPC 連線、不啟動子程序、不用鑰匙圈。核心（Rust）沒有網路程式碼。
- 唯一的對外連線是更新探測（§8）。
- 診斷紀錄（§11）只在輸入法程序的記憶體裡；只有使用者按「複製診斷紀錄…」、看過全文、按「複製」時才寫進剪貼簿。
- 組字、候選、前文、送出的文字、路徑、bundle ID 不進任何日誌、錯誤訊息、當機報告（§3、§4）。診斷紀錄（§11）不是日誌：它在記憶體裡記 bundle ID，只經使用者的複製離開程序。
- 寫到磁碟的使用者內容只有學習檔（§6）與偏好設定（§7）；兩者都只在使用者本機，不上傳。
- 輸入法在 App 沙盒裡執行，entitlements 只有三個鍵（§2）。對外不說「輸入內容無法外流」，因為沙盒擋不住把輸入內容送出去；對外的說法是：輸入法在 App 沙盒裡執行；更新由另一個不在沙盒裡、只會更新已安裝那一份善解的小程式完成。

## 2. 程序的權限

- 正式版輸入法的 entitlements 恰好三個鍵，建置與簽章時都逐鍵比對（`scripts/check-entitlements.sh`，以及 `release.yml` 的 `verify()`）：
  - `com.apple.security.app-sandbox = true`；
  - `com.apple.security.network.client = true`（只為了更新探測）；
  - `com.apple.security.temporary-exception.mach-register.global-name = [<bundle ID>_Connection]`，必須逐字等於 Info.plist 的 `InputMethodConnectionName`，由 `BUNDLE_ID` 產生，不寫死。
- Sparkle 的程式碼位置與 `善解更新.app` 沒有 entitlements；安裝程式（`com.nyanako.shanjie.installer`）沒有 entitlements。
- 輸入法程序寫入的檔案只有學習資料夾 `~/Library/Application Support/shanjie`（沙盒版實際在 container 裡）與 `UserDefaults.standard` 的偏好設定。沒有其他快取、暫存檔或日誌檔。
- 讀取：模型、詞庫與詞包都來自 bundle 的 `Resources`；三元表用唯讀 mmap；系統標點表來自 `/System/Library/PrivateFrameworks/CoreChineseEngine.framework`。
- 沙盒版第一次啟動時，系統把舊版（沒有沙盒）的學習資料夾與偏好設定搬進 container；移轉清單 `container-migration.plist`（`Move = [${ApplicationSupport}/shanjie]`）只放在正式 bundle ID 的組建，開發版與拋棄式組建不帶，所以不會搬走真正的學習資料。偏好設定不在清單裡，系統自己搬。
- 本機組建預設用開發版 bundle ID（`com.nyanako.inputmethod.shanjie.dev`），不碰正式 ID 的資料；`scripts/check-app.sh` 在 `CI` 未設定時拒絕執行正式 ID 的組建。

## 3. 日誌

- **白名單**：輸入法的 Swift 程式只能用 `os.Logger`，訊息只能是靜態字串與 C ABI 回傳碼。不得內插任何其他值（keyCode、字元、游標位置、數量、路徑、bundle ID、組字、候選、送出內容都不行）；不得用 `.public`，也不得用 hash 遮罩；不得用 `print`、`NSLog`、`debugPrint`、`dump`。
- 日誌的 `Logger` 只有幾個：外殼 `Log.shell`（category `shell`）、更新（`Updater.swift`，category `update`）、安裝程式、`善解更新`。
- 例外兩處：
  - `善解更新` 的 `exitQuietly` 以 `.public` 記一個 `StaticString`（編譯期字面值，不可能帶使用者內容）；
  - Sparkle 自己的日誌（`SULog`）用 `%{public}` 並寫到磁碟，內容是路徑、網址與錯誤，沒有輸入內容；這不是善解的程式碼，無法改。
- 學習檔讀寫模組（`core/src/learn_store.rs`）不記日誌：路徑與紀錄只以資料的形式離開那個模組。
- 當機報告：`fatalError`、`precondition` 不得內插任何值；禁用 `try!`；裝著輸入文字的型別不得是 `Error`，也不得實作 `CustomStringConvertible`／`CustomDebugStringConvertible`。

## 4. 核心的錯誤與 panic

- C ABI 的每個匯出函式都包 `catch_unwind`，並安裝靜音的 panic hook（`core/src/ffi.rs` 的 `guard`、`quiet_panics`）；panic 變成回傳碼 4（`SHANJIE_ERR_INTERNAL`），不印 payload。
- `panic = "abort"` 時建置失敗（`compile_error!`）：靜音機制依賴 unwind。
- 核心的所有 `Err` 與 panic 訊息只帶錯誤種類與長度，不含輸入的讀音、文字或路徑。
- `unsafe` 只在這幾處：C ABI（`ffi.rs`）；trigram 表的唯讀映射（`trigram.rs` 的 `Mapped`：Unix 的 `mmap`／`munmap`，Windows 的 `CreateFileMappingW`／`MapViewOfFile`）與 `lm.rs` 開表的那一呼叫；學習檔的 `fcntl`（`F_BARRIERFSYNC`／`F_FULLFSYNC`）與 `geteuid`（`learn_store.rs`）；取本地日期的 `time`／`localtime_r`、Windows 的 `GetLocalTime`（`learn.rs`）；會意在 macOS 走 Accelerate 的 `cblas_sgemm`、`vvtanhf`／`vvexpf`（`tinylm.rs`）；`lm.rs` 延長預測索引生命期的一處 `transmute`。
- 學習紀錄在送出之後的處理（`engine.rs` 中送出後學習的區塊）自己有 `catch_unwind`：學習失敗不影響已經送出的文字。

## 5. 隱私閘門（privacyGate）與前文

### 5.1 閘門

- 下列任一情形，學習停止、不讀前文：
  - 安全輸入開著（`IsSecureEventInputEnabled()`；密碼欄、終端機的 Secure Keyboard Entry）；
  - 前景 App 在學習拒絕清單內（密碼管理器：`com.1password.1password`、`com.agilebits.onepassword7`、`com.bitwarden.desktop`、`org.keepassxc.keepassxc`、`com.apple.Passwords`、`com.apple.keychainaccess`）；
  - 讀不到 bundle ID（空值也算）。判斷不了就擋。
- 拒絕清單只在殼裡查表，不傳給核心、不記日誌、不存檔；使用者的「這個 App 用聊天設定」選擇不影響它。清單裡的 bundle ID 是依公開資料整理，沒有逐一在本機核對。
- 閘門在每次可能學習的核心呼叫之前取樣，並且每次都傳給核心（重建後的引擎預設是停用）。選單在閘門生效時顯示一列不可點的「學習已暫停（安全輸入）」或「學習已暫停（此 App）」，固定字串，不含 App 名稱。
- 英數直通（Command、Option、Control、Caps Lock 組合鍵與英文輸入）不暫存也不學，所以密碼與 `sudo` 不進任何狀態。
- 終端機類 App 的學習照常開（使用者常在終端機打中文）；學習只收「注音模式下在候選窗改選、或從預測列選取的中文詞」與逐字改選處附近的連續漢字。

### 5.2 前文（游標前的文字）

- 殼在組字開始時讀一次，只在記憶體：不寫檔、不寫偏好、不記日誌、不經 C ABI 回傳。
- 不讀的情形：閘門生效；沒有插入點；組字區有 marked text（marked range 長度為 0 或 `NSNotFound` 才算沒有）。
- 一般 App：向 App 要最多 256 個 UTF-16 單位，回傳的字串更長時只取最後 256 個並視為被截過；被截過時先去掉開頭可能被切斷的第一個 grapheme；從最後一個換行（Swift `Character.isNewline`）或 NUL 之後開始（NUL 當換行，不刪除）；留最後 64 個 grapheme，標點、空白、英文、emoji 都保留（同一則訊息、跨標點）。
- 終端機類 App（`com.apple.Terminal`、`com.googlecode.iterm2`、`dev.warp.Warp-Stable`、`net.kovidgoyal.kitty`、`com.github.wez.wezterm`、`org.alacritty`、`co.zeit.hyper`、`com.mitchellh.ghostty`）只傳結尾 ≤ 2 個漢字，因為終端機的一行裡有指令與輸出。內建終端機面板的編輯器與編輯區是同一個 bundle ID，不在清單內（已知限制）。清單裡只有前兩個核對過 Info.plist 的 `CFBundleIdentifier`，其餘依公開資料整理。
- 核心只留用得到的長度：`self.left` 是結尾 ≤ 2 個漢字；`left_full` 是載入的會意模型宣告的前文長度 L 個字元，或（模型沒宣告 L 時）結尾 2–32 個 ASCII 字母的英文詞加一個空白，否則空。`left_full` 不經 C ABI 回傳、不寫學習檔、不跨程序、不給任何網路功能；會意工作只帶最後 L 個字元。
- 送出、Esc、重置、回傳碼 4、自動送出都清掉前文。接受的殘留：已排進佇列的重排工作複本活到那次執行結束；Rust `String` 與 Swift 字串釋放時不清零；只有未完成符號時按 Esc 不經清除，前文留到下一次組字開始。

## 6. 學習檔

- 位置 `~/Library/Application Support/shanjie/learning.tsv`（路徑由 `FileManager` 算出）；核心建立資料夾，權限 0700 並檢查擁有者，不符時改正為 0700。
- 檔案：權限 0600；開啟用 `O_NOFOLLOW`；寫入用固定名稱的暫存檔 `learning.tsv.tmp`（先 unlink，`create_new`、`O_NOFOLLOW`、0600），寫完 `F_BARRIERFSYNC`（失敗改 `F_FULLFSYNC`）再 rename；附加寫入只對既有的、屬於自己的一般檔，沒有 group/other 權限位元、只有一個連結、至少有檔頭、上限 16 MB 之下。
- 載入：`O_NOFOLLOW|O_NONBLOCK`，fstat 必須是一般檔，上限 16 MB；檔頭錯或超過上限，改名為 `learning.tsv.corrupt`（0600，只留一份）。
- 內容只有：前文鍵（≤ 2 個漢字，或句首的 `^>` 加下一個音節的讀音，或新詞的 `#`，或空字串表示全域）、讀音、詞、權重、日期。寫不回原樣的紀錄（欄位含 tab、換行、控制字元，讀音含空音節，鍵、權重、日期不合法）不寫，載入時逐行略過。
- 什麼會被學：
  - 只有在候選窗改選、或從預測列選取，才學；沒有改選的送出不新增任何字，只更新既有新詞的權重與日期。
  - 逐字改選學到的新詞，是送出文字裡含改選字的一段 2–4 個連續漢字（遇標點、非漢字、功能字切開，其中最多 3 字沒被改選），所以每次逐字改選，檔案裡最多出現改選處附近連續 6 個漢字（改選的那一段 ≤ 4 字，加前面 ≤ 2 字的前文鍵）。
  - 學習檔不含沒被改選、也沒從預測列選過的內容；不含前文的原文（`left_full` 不落地）。
- **清除**：選單「清除選字記憶…」先跳 `NSAlert`（不在選單裡二次確認，也不阻塞輸入法）。確認後刪除記憶、尚未寫入的學習，以及 `learning.tsv`、`learning.tsv.tmp`、`learning.tsv.corrupt` 三個檔；資料夾與不備份旗標保留。刪除失敗時顯示「清除選字記憶失敗」，不假裝成功。提示窗文字說明：清除只刪本機檔案，已進 Time Machine 備份或本機快照的副本不受影響。沒有成功開過學習檔（沒有 store）時清除回報失敗。
- **備份**：預設會被 Time Machine 備份；選單「不要備份選字記憶」在資料夾上設 `isExcludedFromBackup`（設在資料夾，不設在檔案，因為清除會刪檔）；失敗時旗標與勾選不變。
- 學習檔無法開啟或核心回報寫入失敗時，選單多一列不可點的「選字記憶無法存檔」。
- 加密：靠 FileVault，不另加密。

## 7. 偏好設定中的 App 識別

- 其他 App 的 bundle ID 預設不存、不傳給核心、不記日誌。例外：診斷紀錄（§11）預設開，把清理過的 bundle ID 留在記憶體裡（不寫檔），可關。
- 唯一的例外：使用者在選單或設定頁對某個 App 切換「聊天／書面」之後，該 bundle ID 與 `chat`／`formal` 存在輸入法自己的 `UserDefaults`（鍵 `appProfiles`）。新的值與內建判斷相同時，該項移除；空字典時整個鍵刪掉。
- 聊天／書面的自動判斷用內建清單查前景 App 的 bundle ID（`NSWorkspace.frontmostApplication`），結果以列舉傳給核心，bundle ID 本身不傳。
- 設定頁「App 設定」用 `NSWorkspace` 取 App 的名稱與圖示，只在視窗裡顯示。

## 8. 連網與更新

- 輸入法程序的連線只有 Sparkle 的更新探測：只查資訊、不開視窗；背景檢查在「自動檢查更新」開著（預設開，設定頁可關）時每天最多一次（上次檢查距今未滿一天則不查；時鐘被往回調時不被卡住）；第一次檢查在啟動一分鐘後，之後每小時看一次是否到期；使用者在設定按「立即檢查」不受開關與一天一次限制。
- 只有 release workflow 建的正式版與更新測試版（Info.plist `ShanjieUpdateBuild`）才啟動更新服務；本機建置不讀正式 feed。
- feed 網址釘在 Info.plist 的 `SUFeedURL`，不讀偏好設定裡的值；Sparkle 排程相關的偏好鍵與跳過版本鍵在每次檢查前清掉；委派只允許資訊探測。
- 探測不送使用者內容；`SUEnableSystemProfiling` 為 false；feed 要求簽章（`SURequireSignedFeed`）；更新只在使用者按下之後才安裝。
- 更新連線用 Sparkle 預設的 session，不是 ephemeral。
- 失敗只記靜態字串（「update check failed」、「the updater did not start」），不記版本、網址或錯誤內容。
- 沒有逐鍵或逐句的連線，沒有雲端。若將來加入任何會送出使用者內容的功能，必須：預設關、使用者主動開啟並看到送出內容的範例與服務商現行的資料保留條款；只在使用者明確送出時送，不逐鍵送；只接受回傳本機候選的索引，不接受自由文字；金鑰存鑰匙圈、不進網址與日誌；硬逾時 2 秒、失敗退回本機結果；終端機與密碼管理器預設不送；並重新過安全審查。目前沒有這類程式碼。

## 9. 模型與資料的來源與驗證

- 輸入法不在執行時下載任何模型：模型、詞庫與詞包都隨 App 打包，路徑寫死、不可設定。
- 模型檔是自訂格式、嚴格讀檔器載入（載入失敗時關閉對應功能，不讓程序當掉）；會意小模型只能在 n-gram 的前 8 名之間重排，不能產生任意文字。
- 每個模型檔的 SHA-256 進版控（`data/*.sha256`）：CI 的 `scripts/fetch-models.sh` 與 `release.yml` 的下載在建置前核對；`scripts/build-app.sh` 與 `scripts/check-app.sh` 核對打包進 App 的檔；關於頁的模型列表來自 `data/models.tsv`，`scripts/check-models.sh` 核對。細節見 `docs/spec/release-and-ci.md` 與 `docs/spec/models.md`。

## 10. 驗證

- 日誌：`macos/Tests/ShanjieKitTests/LogTests.swift` 的 `testShellLogsNothingButStaticTextAndCodes`。另起 `/usr/bin/log stream --level debug --style ndjson`，只依行程過濾，同時擷取該行程的 stdout／stderr；先確認起始標記看得到，再重播帶標記的字串、bundle ID、路徑與左文，走過打字、滑鼠選字、非 0 回傳碼、重排、清除、備份、App 設定切換、設定視窗與缺檔的錯誤路徑，最後確認擷取內容與 stdout／stderr 都找不到任何標記，也沒有 `<private>`；並確認外殼自己的錯誤日誌（固定字串加回傳碼）確實進了擷取（正向對照）。verifier 在套用路徑加一行 `.public` 或 `NSLog` 時這個測試必須失敗。zsh 的 `log` 是內建指令，要寫 `/usr/bin/log`。
- 核心的靜音 hook：`core/src/ffi.rs` 內的測試，含用預設 hook 時看得到訊息的正向對照。
- 學習檔：`core/tests/engine_newword.rs` 的 `r2_marker_characters_on_disk`、`r2_marker_characters_on_disk_with_subspans`（逐字改選一個標記字串後，標記字元只出現在含改選字的 ≤ 4 字段與前面 ≤ 2 字；沒有改選的送出不新增含標記的列；游標前的英文標記詞不出現在檔案裡）；`core/tests/learn_store.rs`（讀寫、損壞檔、權限；斷言失敗時不印紀錄內容）；`core/tests/engine_learn.rs` 的 `learning_panic_keeps_the_commit`。
- 閘門與清除：`macos/Tests/ShanjieKitTests/LearningTests.swift` 的 `testGatePausesOnSecureInputDenylistAndUnknownApps`、`testResetPathsWithSecureInputStoreNothing`、`testClearAsksInAWindowFirst`、`testClearRemovesFilesKeepsDirectoryAndFlag`、`testCoreClearFailureIsShown`、`testBackupIsOnByDefaultAndTheExclusionSurvivesAClear`。
- 更新：`macos/Tests/ShanjieKitTests/UpdatesTests.swift` 的 `testShouldCheckDecision`；`macos/Tests/UpdateHostTests/UpdateHostTests.swift` 的 `testFeedURLFromInfoPlist`、`testWhichChecksMayRun`。
- entitlements：CI `shell` job 的 entitlements 與 Sparkle 變造測試（`scripts/test-sandbox-mutations.sh`）與 `scripts/check-app.sh`。
- 需要真人的部分：安全輸入的三處情境（Safari 密碼欄、Terminal 開 Secure Keyboard Entry、Terminal `sudo`）；活動監視器的「沙盒」欄位是「是」。
- 已知限制：裝在使用者帳號的 `~/Library/Input Methods` 時，安全輸入期間系統會把第三方輸入法反灰，選單打不開，所以那時看不到「學習已暫停（安全輸入）」那一列。

## 11. 診斷紀錄

規格在 `docs/spec/macos-shell.md` §13。隱私上的規則：
- 欄位只有列舉、布林與清理過的 bundle ID（1–255 個 `[A-Za-z0-9._-]`，其他記 `?`）；不記打的字、組字、候選、前文、按鍵字元或鍵碼。
- 只在記憶體：Shift 最多 200 筆、其他最多 800 筆；不寫檔、不用 `Logger`／`print`／檔案 I/O；程序結束就沒了。
- 安全輸入期間不記 Shift（逐次的 Shift 會透露密碼裡有幾個要按 Shift 的字、用哪一側）；切換中英、換 App 這類狀態照記，所以安全輸入時的單按 Shift 仍留下一筆 `toggle`。不在安全輸入時，有 Shift 按著時打過鍵（大寫），到兩個 Shift 都放開、或下一次乾淨的按放為止只記真的切換；乾淨的按放之後的紀錄和更早打的鍵無關（`ShiftTapTests` 窮舉長度 7 以內的按鍵序列；理由是乾淨的按放之後狀態整個歸零）。
- 時間是匯出前幾秒，加上第一行的匯出時刻就能換算成時刻；紀錄留到輸入法結束，可能涵蓋好幾天，說明文字照寫。
- 記錄不新增任何 IMK 讀取。
- 不自動送出：複製前先顯示全文（不能選取，免得 ⌘C 走一般的複製）；剪貼簿用 `.currentHostOnly`，一次寫入同時帶 `org.nspasteboard.TransientType` 的項目。預設開，設定→診斷可關，關掉、打開都清空；在設定以外改這個開關時，收到 UserDefaults 的變更通知就清空，預覽開著時被清空就不複製（收不到通知的情形見 `docs/spec/macos-shell.md` §13.3）。

驗證：`DiagLogTests` 與 `LogTests`（`docs/spec/macos-shell.md` §13.4）。
