# S3b 契約：Swift 輸入法本體（最小可安裝版）

計畫見 `docs/PLAN.md` §S3b。核心的按鍵規則、C ABI 與 R2 規則見 `docs/contracts/s3a.md`，語言模型見 PLAN §S2c。本片寫殼：把 macOS 的按鍵事件翻成 `ShanjieKey`、把 `ShanjieOutput` 畫到 App 上，並用 GitHub Actions 建置、簽章、公證、發布（流程仿 syrtis `Nanako0129/syrtis` 的 `.github/workflows/`）。結構參考小麥注音（MIT，本機 `~/side-project/ime-research/repos/McBopomofo`）。

## 1. 外觀參考（使用者 2026-10-03 提供的 macOS 27 內建注音截圖）

截圖只存在本機 `~/side-project/shanjie-private/s3b-reference/`（不進 repo）。觀察：

| 編號 | 畫面 | 觀察 |
|---|---|---|
| 1 | 橫式候選條（深色） | 膠囊形、半透明玻璃底；每個候選前有小號碼（次要文字色）；選取中的候選是系統強調色（藍）的圓角底、白字；一行 7–8 個；最右邊有展開箭頭 ⌄ |
| 2 | 展開後的網格 | 第一列有號碼，其餘沒有；可捲動；底部分頁「詞頻／部首／表情／拆字」 |
| 3 | 單字「一」 | 候選含表情符號（1️⃣）；候選窗開著時，組字區正在選的那一段有底色框 |
| 4 | 直式清單 | 每列號碼＋候選；右側有「常用」標籤，罕用字旁標注音；底部同樣的分頁 |

所有截圖的組字區都是**底線**。淺色模式與選單列圖示沒有截圖，安裝後由使用者對照。

**這一版做**：橫式候選條（截圖 1，不含展開箭頭）、組字底線、數字選字。**不做**（記為 S3b-2）：展開網格與分頁、直式清單、表情候選、「常用」標籤與注音標示、組字區的分段底色框、設定頁、自建玻璃候選窗。

## 2. 程式結構與建置

- Swift 程式放在 `macos/`（SwiftPM），分三個 target：
  - `ShanjieKit`（library）：按鍵翻譯、輸出套用、組字擁有者、設定判斷、候選資料。透過一個 client 協定和 IMK 隔開，測試用假的 client；**一律經由真正的 C 核心**（連結 `target/release/libcore.a`），不得注入假引擎。
  - `Shanjie`（executable）：`IMKInputController` 子類別、`main.swift`、`install` 與 `--selftest` 的參數處理、TIS 註冊。只有這個 target 連結 Carbon 的 TIS。
  - `ShanjieKitTests`（XCTest）。
  - 連結 `libcore.a` 與 `core/include/shanjie.h` 的方式（module map 等）由 executor 決定，寫進 §11。
- `scripts/build-app.sh`：`cargo build --release --locked -p core` → `swift build -c release`（在 `macos/`）→ 組出 `build/shanjie.app` → **ad-hoc 簽章**：`codesign --force --sign - --options runtime`，不給任何 entitlements 檔。本機與 CI 都用這支；agent 執行它不碰任何鑰匙圈。不安裝、不啟動 app。
- `shanjie.app/Contents`：
  - `MacOS/shanjie`
  - `Resources/`：`mcbpmf-data.txt`、`overlay-add.tsv`（從 `data/lexicon/` 複製）、`bigram.sjlm`（從 `data/lm/` 複製；不存在、或 SHA-256 和 repo 追蹤的 `data/bigram.sjlm.sha256` 不符，就建置失敗，訊息說明可從 `model-v1` Release 下載）、選單列圖示、`zh-Hant.lproj/InfoPlist.strings`。
  - `Resources/LICENSES/`：`LICENSE`（Apache-2.0）、`LICENSES/McBopomofo-MIT.txt`、`LICENSES/data.md`，以及一份 CC BY-SA 4.0 的署名說明（overlay 與模型的來源與授權網址）。小麥的 MIT 要求隨附版權聲明，CC BY-SA 要求署名。
  - `Info.plist`：照小麥的鍵（`InputMethodConnectionName`、`InputMethodServerControllerClass`、`InputMethodServerDelegateClass`、`LSUIElement`、`ComponentInputModeDict`、`tsVisibleInputModeOrderedArrayKey`），bundle ID `com.nyanako.inputmethod.shanjie`，版本號來自 git tag（沒有 tag 時用 `0.0.0`）。
  - **兩個輸入模式**：`com.nyanako.inputmethod.shanjie.standard`（「善解（標準）」）與 `com.nyanako.inputmethod.shanjie.eten`（「善解（倚天）」），`TISIntendedLanguage` 為 `zh-Hant`，`tsInputModeScriptKey` 為 `smTradChinese`。
- 選單列圖示：單色 template 圖，「解」字加圓角方框（呼應網站的印章），由腳本用 CoreText 產生 TIFF，不下載字型或圖。
- **entitlements：一律沒有**（不提供 entitlements 檔，所以也不會有 `get-task-allow` 或 `disable-library-validation`）；hardened runtime 一定要開（R9）。

### 2.1 本機建置與測試流程（仿 syrtis，使用者 2026-10-03 要求）

- repo 根目錄的 `Makefile` 是本機入口：`rust`、`build`、`test`（`cargo test --release --locked`＋`swift test`）、`bundle`（`build/shanjie.app`，出貨 bundle ID）、`selftest-bundled`、`clean-bundle`。
- **過期防護**（SwiftPM 不追蹤這兩樣）：`target/release/libcore.a` 比 Swift 執行檔新，就刪掉執行檔強迫重新連結；`core/include/shanjie.h` 比較新，就刪掉 module cache 與匯入它的 target 的建置產物。不加的話，Swift 沒改時會沿用舊的執行檔，悄悄包進舊的核心。
- `scripts/build-app.sh` 有 `BUNDLE_ID`（預設 `com.nyanako.inputmethod.shanjie`）與 `OUT_DIR`（預設 `build`）兩個環境變數；輸入模式的 ID 由 `BUNDLE_ID` 衍生；組裝前先 `touch "$OUT_DIR/.metadata_never_index"`，避免 Spotlight 與 LaunchServices 登記本機的 bundle（對輸入法也避免系統依 bundle ID 啟動到 `build/` 裡那份）。
- **bundled selftest**：`make selftest-bundled` 在 `build/selftest/` 組一份 release bundle 再跑 `--selftest`。本機預設用拋棄式的 `com.nyanako.inputmethod.shanjie.selftest`（不碰正式版的偏好，但 gate 較弱）；CI 用 `make selftest-bundled SELFTEST_BUNDLE_ID=`（空值＝出貨 ID，runner 是拋棄式的）。
- **本機 bundle 邊界**：`build/` 裡的 bundle 不得啟動或註冊；實際使用與驗收以 `~/Library/Input Methods/shanjie.app` 為準。用完以 `make clean-bundle`（`lsregister -u` 後刪除）清掉。
- `docs/verification.md` 記錄本機 gate、CI 跑什麼、selftest 的 ID 取捨與清理指令。

## 3. CI 與發布（GitHub Actions）

- **模型雜湊只有一份**：`data/bigram.sjlm.sha256`（進 git）。`build-app.sh`、`ci.yml` 的兩個 job、`release.yml` 都讀它，不得各自寫死（原本 `ci.yml` 的 `LM_SHA256` 環境變數已移除）。
- **Rust 工具鏈**：所有會建 `libcore.a` 的 job（`ci.yml` 的 `core` 與 `shell`、`release.yml` 的 `build`）都用和 `core` job 相同的 `dtolnay/rust-toolchain` 步驟，固定 1.97.1。
- **`ci.yml`**（已在 main）加一個 `shell` 工作：下載 `model-v1` 的模型並比對雜湊 → `scripts/build-app.sh` → `swift test`（`macos/`）→ `build/shanjie.app/Contents/MacOS/shanjie --selftest` → §10 的驗收 2、3、6。
- **`release.yml`**（新增，仿 syrtis 的 `release.yml`）：推 `v*` tag 時：
  1. **gate**：要求這個 commit 在 main 上 `ci.yml` 的 run 全綠（善解只有 `ci.yml`，不得照抄 syrtis 的 `ci-release.yml`），否則拒絕。
  2. **build**（`xcode-27`）：先從 `model-v1` 下載 `bigram.sjlm` 並以 `shasum -a 256 -c data/bigram.sjlm.sha256` 比對，再跑 `scripts/build-app.sh`，把 ad-hoc 簽章的 app 打包成 artifact。
  3. **sign**（`environment: release`，只接受 `refs/tags/v*`）：從 secrets `DEVELOPER_ID_P12_BASE64`、`DEVELOPER_ID_P12_PASSWORD`、`NOTARY_KEY_P8` 與 variables `NOTARY_KEY_ID`、`NOTARY_ISSUER_ID`、`APPLE_TEAM_ID` 建**一次性的鑰匙圈**（隨機密碼、結束時刪除），用 `Developer ID Application`（Team `2LJ882GPY8`）、`--options runtime --timestamp` 簽章，`notarytool submit --wait` 公證，`stapler staple`；再驗證：`codesign --verify --strict --deep`、`spctl -a -t exec -vv`、entitlements 為空、flags 含 `runtime`、Team ID 相符。缺任何一項材料就失敗，不得退回 ad-hoc。
  4. **publish**（只在 `v*` tag）：建立 GitHub Release，附件 `shanjie-<版本>.zip`（`ditto -c -k --keepParent` 打包公證後的 app，app 內含 `Resources/LICENSES/`）與它的 SHA-256。
- **試跑**：`workflow_dispatch`（只接受 `main`）跑 gate、build、sign 與驗證，不 publish；讓使用者設好材料後先確認簽章與公證可行，再推 tag。
- 簽章材料由**使用者**放進 repo 的 `release` environment（值和 syrtis 用的相同）；agent 不讀、不寫、不轉貼任何 secret。variables 可由 agent 從 syrtis 複製（不是秘密），但要先問。
- 本機的 login keychain 在整個流程裡都不會被碰到。

## 4. 安裝（由使用者執行）

- `scripts/install-ime.sh <shanjie.app 的路徑>`（通常是解壓後的 Release 附件；也接受 `build/shanjie.app` 自己建的 ad-hoc 版）：
  - `#!/bin/bash`、`set -euo pipefail`；`$HOME` 為空就中止；不用 sudo（R9）。
  - 目的地固定為字面路徑 `"$HOME/Library/Input Methods/shanjie.app"`。腳本只刪除或搬移 `~/Library/Input Methods` 裡的三個位置：這個目的地、這次執行用 `mktemp` 建的暫存資料夾、保留上一版的 `.shanjie-previous`。輔助資料夾的名稱不是 `.app`（暫存資料夾在複製期間裡面有一個 `shanjie.app`）。不要同時執行兩次安裝（未加鎖）；被直接砍掉的執行可能留下 `.shanjie-staging-*`，不會自動清（同時執行的另一份看起來一樣），要手動刪。**HOME 不是這個帳號真正的家目錄時，除非設了 `SHANJIE_INSTALL_FILES_ONLY=1`，腳本一開始就拒絕執行**；files-only 模式完全不呼叫 lsregister、pkill 或註冊。
  - 順序：`ditto` 到暫存資料夾（複製失敗時什麼都不放上去）→ 刪掉更早保留的 `.shanjie-previous`（先 `chmod -R u+w`）→ 舊版 `lsregister -u` 後改名為 `.shanjie-previous` → 新版改名就位 → 新版 `lsregister -f`（`shanjie install` 在覆蓋安裝時會略過 TIS 註冊）→ 以完整路徑（HOME 解析成實際路徑、正規表示式字元跳脫、錨定）結束舊行程並等它退出，5 秒後仍在就警告 → 執行**已安裝那一份**的 `install`；註冊失敗時印出換回上一版的指令（順序：刪新版 → 搬回上一版 → `lsregister -f` → 結束行程 → `install`）。兩次改名之間被中斷時，`trap` 把新版放上去並提示重跑（這條路徑沒有自動測試；2026-10-04 的審查 agent 在沙盒中以 SIGINT／SIGTERM／SIGHUP 手動驗證過還原）。LaunchServices 的登記行為沒有在真實系統量過。設計演變（2026-10-04）：改名＋失敗還原的版本每修一輪就多一種邊界情況；移到 `~/.Trash` 可能被隱私保護擋下、垃圾桶裡的舊版可能仍登記在 LaunchServices（小麥注音用的是 `NSWorkspace.recycle`，不是 `mv`），所以改成在同一個資料夾保留一份不是 `.app` 的上一版。`scripts/test-install-ime.sh` 用執行檔只會 `exit 1` 的假 app、在暫存 HOME 裡驗證：沒開 files-only 時拒絕執行、全新安裝時複製失敗（什麼都沒放上去）、全新安裝、覆蓋安裝（上一版被保留）、從已安裝那份重裝、覆蓋時複製失敗，CI 也跑。在真實系統上的行為由使用者驗收 10 確認。已知限制：同一版重裝一次會讓 `.shanjie-previous` 也變成這一版。
  - 最後印出下一步：到「系統設定 → 鍵盤 → 輸入方式」確認「善解」已出現；沒出現就登出再登入。
- **agent 不得對真實的 HOME 執行 `install-ime.sh`、執行 `shanjie install`、或啟動 app**；agent 與 CI 只能透過 `scripts/test-install-ime.sh`（暫存 HOME、假 app、`SHANJIE_INSTALL_FILES_ONLY=1`）執行它。真正的安裝只由使用者執行。

## 5. 行程、引擎與組字擁有者

- 啟動時（IMK server 建立後）用 `Bundle.main.resourceURL`（絕對路徑）建一個引擎（目前輸入模式的排列；第一次預設標準），接著 `shanjie_engine_load_lm(Resources/bigram.sjlm)`。任一步回傳非 0：記下錯誤碼（只記碼），引擎維持沒有 LM 的狀態仍可打字；建不出引擎時所有按鍵直通。
- **同一時間只有一個引擎**（每個約 240 MB）。切換輸入模式（標準 ↔ 倚天）時：`reset(0)` 送出目前的組字、釋放舊引擎、用新排列重建、重新載入 LM 與目前的設定。
- 所有 C ABI 呼叫都在主執行緒（IMK 的回呼執行緒）。
- **組字擁有者**：IMK 對每個 client 各建一個 controller，但引擎只有一個。殼用**弱參照**（`weak` 指向擁有者 controller）記住目前組字屬於誰；「擁有者仍有效」的定義是弱參照不是 nil。不得只記 `ObjectIdentifier`（被釋放的 controller 位址可能被新的重用）。
  - 某個 controller 的 `handle` 或 `activateServer` 進來時，若組字區有字而擁有者不是它：擁有者仍有效就 `reset(0)` 送回擁有者的 client，否則 `reset(1)` 丟棄；之後才處理新的事件，擁有者改成目前這個 controller。**擁有者一改變就依新擁有者的 client 重新 `set_profile`。**
  - `activateServer` 的順序固定為：先處理擁有者（上一條）、再 `set_profile`。
  - `deactivateServer` 與 `commitComposition(_:)`：**只有呼叫者就是擁有者時**才 reset 並送出、隱藏候選窗、清掉殼保存的候選陣列（`deactivateServer` 用 `reset(0)` 送回它的 client；secure input 生效時改用 `reset(1)` 丟棄，避免把組字送進剛取得焦點的密碼欄）。不是擁有者時什麼都不做：不碰組字，也不碰共用的候選窗與候選陣列。
  - **secure input 只在 `deactivateServer` 檢查**（2026-10-04 決定）。CodeRabbit 在 PR #3 建議所有非按鍵的送出都檢查；試做後的本地審查指出 `IsSecureEventInputEnabled()` 是全系統旗標，任何 App 開著 secure input 時，點別處、切換排列或換擁有者都會悄悄丟掉使用者打的字。組字實際會被送到哪個 client 要實機才知道，所以延到 S4 的 privacyGate，以實機證據決定。
  - secure input 的判斷由 `Shanjie` target 以閉包（包 `IsSecureEventInputEnabled()`）注入 `ShanjieKit`，測試用假的閉包驅動兩種情況。
  - controller 的 `deinit`：weak 參照在 deinit 時已經讀成 nil，所以規則是「deinit 時若擁有者讀成 nil 且組字區有字，就 `reset(1)`、隱藏候選窗、清掉候選陣列」。

## 6. 按鍵翻譯

- 只處理 `keyDown`；`flagsChanged` 不送進核心。**中英切換用系統的「使用大寫鎖定鍵切換輸入方式」**（使用者 2026-10-03 選 Caps Lock）：**未實測**：預期切換時系統會停用本輸入法並呼叫 `deactivateServer`（依 IMK 文件推論，使用者實測 13 會確認）。殼不保留中英狀態（s3a §3 原本寫的 Shift 單按切換不做）。
- `kind` 依 `keyCode`：Return／keypad Enter → ENTER、Space → SPACE、Delete(51) → BACKSPACE、Forward Delete(117) → DELETE、Esc → ESC、←→↑↓、Home(115)、End(119)、Tab。
- `CHAR` 的 `ch` 依 `keyCode` 查 **ANSI 實體鍵位表**取「不按 Shift 的 ASCII」（a–z、0–9、`` ` ``、`-`、`=`、`[`、`]`、`\`、`;`、`'`、`,`、`.`、`/`），不使用 `event.characters`。
- **表外的鍵**（數字鍵盤、功能鍵等）與 IMK 傳入的 `nil` 事件不送進核心：組字區有字時先 `reset(0)` 送出，然後回傳「不處理」；組字區空時直接回傳「不處理」。
- `modifiers`：Shift、Control、Option、Command、Caps Lock 依 `modifierFlags` 對到 bit0–4。

## 7. 輸出套用

- **回傳碼非 0**：先 `shanjie_engine_reset(1)` 讓核心也丟棄組字（核心只在碼 4 時自己丟棄，碼 1、2 不會），再 `setMarkedText("")`、隱藏候選窗、清掉候選陣列，回傳 `false`；不記錄按鍵內容。
- `commit` 非空：`insertText(commit, replacementRange: NSRange(location: NSNotFound, length: 0))`。
- `preedit`：非空時 `setMarkedText`，整段單線底線，選取範圍 `NSRange(location: cursor_utf16, length: 0)`；變成空字串時用空字串清掉組字。
- `handled = 0`：先套用 commit 與 preedit，再回傳 `false` 讓 App 處理這個鍵。
- 收到輸出後在同一個呼叫裡把字串複製成 Swift `String`，立刻 `shanjie_output_free`。

## 8. 候選窗

- 用 InputMethodKit 的 `IMKCandidates`，單列樣式（`kIMKSingleRowSteppingCandidatePanel`），只負責**顯示**核心給的這一頁（≤ 9 個）與選取位置，號碼 1–9；位置在組字區下方。按鍵一律由核心處理，不把按鍵交給 `IMKCandidates`。
- `candidate_count` 變成 0、或擁有者的 `deactivateServer` 時（§5）：隱藏並把殼保存的候選陣列清成空的。非擁有者的 `deactivateServer` 不碰候選窗。
- 滑鼠點選（`candidateSelected(_:)`）：轉成對應的數字鍵送進核心；不得直接 `insertText`。
- 若 `IMKCandidates` 無法只當顯示用（會自己吃按鍵、選取無法同步），executor 停下來回報；自建玻璃視窗是下一輪（S3b-2）。

## 9. 設定判斷與隱私

- **聊天／書面設定**：`activateServer` 時、以及組字擁有者改變時（§5），用擁有者 client 的 `bundleIdentifier()` 查內建的聊天 App 清單：Discord（`com.hnc.Discord`）、LINE（`jp.naver.line.mac`）、訊息（`com.apple.MobileSMS`）、Slack（`com.tinyspeck.slackmacgap`）、Telegram（`ru.keepcoder.Telegram`、Telegram Desktop `com.tdesktop.Telegram`）、WhatsApp（`net.whatsapp.WhatsApp`）、Messenger（`com.facebook.archon`）。bundle ID 是依公開資料整理，未逐一在本機核對；使用者實測 15 會涵蓋 Discord。在清單內用 chat，其餘用 formal，以 `shanjie_engine_set_profile` 傳**列舉**給核心；bundle ID 不傳給核心、不寫 log、不存檔。清單在這一版寫在程式裡；PLAN §S2 的「清單可在設定中修改」延到設定頁（S3b-2）。
- **日誌（R2，白名單）**：只能用 `os.Logger`，訊息**只能是靜態字串與 C ABI 回傳碼**；不得內插任何其他值（含 keyCode、`ch`、游標位置、數量、路徑、bundle ID、組字、候選、送出內容）；不得使用 `.public`，也不得用 hash mask。不得用 `print`、`NSLog`、`debugPrint`、`dump`。
- **當機報告**：`fatalError`／`precondition` 不得內插任何值；禁用 `try!`；裝著輸入文字的型別不得是 `Error`，也不得實作 `CustomStringConvertible`／`CustomDebugStringConvertible`。
- **privacyGate（R3）延到 S4**：這一版沒有學習、雲端、左文，gate 沒有東西可擋。PLAN 與 s3a 原本列在 S3 的 gate、gate 單元測試、「privacyGate 轉為生效時 reset」都移到 S4。
- **自測**：`shanjie --selftest`：
  - 參數只接受完全相符的 `install` 或 `--selftest`；其他參數一律 exit 非 0、沒有任何副作用。
  - 從 `Resources/` 建引擎並載入 LM；`shanjie_engine_new` 或 `shanjie_engine_load_lm` 回傳非 0 就 exit 非 0（只印錯誤碼）。
  - 用標準排列打 dev302 第 10 列（`ㄑㄧˊ ㄓㄨㄥ ㄅㄠˋ ㄍㄠˋ ㄇㄧㄥˊ ㄊㄧㄢ ㄧㄠˋ ㄐㄧㄠ`），以 `set_profile(0)` 與 `set_profile(1)` 各送出一次：分別等於 `其中報告明天要交`（chat）與 `期中報告明天要交`（formal）才 exit 0。不印任何內容。
  - 不得呼叫 TIS、不得寫任何檔案或 UserDefaults、不得建立 NSApplication 或 IMK server。

## 10. 驗收

**agent 可做的（executor 做、verifier 重做；CI 也跑 1–6）：**
1. `scripts/build-app.sh` 成功，產出 `build/shanjie.app`。
2. `plutil -lint` 通過；兩個輸入模式、bundle ID、`InputMethodConnectionName`、`InputMethodServerControllerClass` 都在；`Resources/` 有三個資料檔、圖示與 `LICENSES/`（含 Apache-2.0、小麥 MIT、`data.md`、CC BY-SA 署名說明）。
3. `codesign --verify --strict --deep` 通過；`codesign -d --entitlements - build/shanjie.app` 的輸出沒有任何 entitlement；`codesign -dv` 的 flags 含 `runtime`。
4. Swift 測試（`swift test`，在 `macos/`），全部經由真正的 C 核心（`Resources` 等同的 `data/lexicon` 與 `data/lm/bigram.sjlm`；缺檔就失敗並說明怎麼取得）：
   - 按鍵翻譯：ANSI 表每個鍵、兩種排列的 37 個注音鍵與 5 個聲調鍵、各特殊鍵、修飾鍵位元、`nil` 事件。
   - 假 client：打「你好」＋Enter → `insertText("你好")` 且組字清空；打 ㄋㄧˇ＋空白 → 候選顯示（2–9 個，因為接著要按 2）、按 2 → 組字更新、候選隱藏；帶 Caps Lock 的鍵 → 回傳 false 且輸出不變；組字中按表外的鍵 → 先送出再回傳 false；`commitComposition` → 送出並清空；回傳碼非 0 的路徑 → 組字清空、候選隱藏、回傳 false。
   - **設定（可觀察）**：以 Discord 的 bundle ID 啟動後打第 10 列＋Enter，送出 chat 第一名；以 TextEdit 啟動送出 formal 第一名；兩者不同。
   - **組字擁有者**：兩個假 controller。(a) A 組字中直接對 B 按鍵：B 的輸出不含 A 的文字，A 的組字送回 A。(b) A 組字中被釋放、之後 B 按鍵：A 的組字被丟棄，不出現在 B。(c) B 組字中，A 的 `deactivateServer` 晚到：B 的組字不會出現在 A 的 client。verifier 把擁有者檢查拿掉時，至少一個測試必須失敗。
   - 切換輸入模式：標準組字中切到倚天 → 先送出、之後倚天的鍵位生效。
   - 回傳碼非 0：之後核心的組字也是空的（下一鍵不會讓舊組字重新出現）。
   - 擁有者改變時重設設定：A（Discord）組字中，B（TextEdit）直接按鍵打第 10 列並送出，得到 formal 第一名。
   - secure input：假閉包回傳 true 時，擁有者的 `deactivateServer` 丟棄組字、不送出。
   - 非擁有者的 `deactivateServer` 不隱藏擁有者的候選窗、不清候選陣列。
   - verifier 把殼裡的 `shanjie_engine_key` 呼叫改成固定回傳非 0，或把 `set_profile` 改成不呼叫核心時，必須有測試失敗。
5. **日誌行為測試（R2）**：
   - 另起 `/usr/bin/log stream --level debug --predicate 'processIdentifier == <測試行程的 PID>'` 擷取，**只依行程過濾**，其他 subsystem 的 Logger 也會被抓到；subsystem 只用在下面的 `<private>` 斷言（zsh 的 `log` 是內建指令，必須寫完整路徑）。
   - 同時擷取測試行程自己的 stdout／stderr：有 stderr 的行程（例如測試）裡，`NSLog` 與 `print` 只寫到 stderr／stdout、不進 unified log（2026-10-03 實測）。擷取期間的 XCTest issue 先扣住、擷取結束後才記錄，免得失敗訊息把句子帶進擷取內容。
   - **起始標記**：用殼同一個 Logger、同一個 subsystem、殼會用到的最低層級，以 `.public` 記一個起始標記；輪詢擷取結果，看到它才開始負向動作；10 秒內沒看到就判測試失敗（擷取沒接上）。
   - 負向動作（各帶這一輪的 nonce）：打字送出標記句；回傳碼非 0 的路徑；資料目錄路徑含標記時建引擎失敗；假 client 的 bundle ID 含標記（例 `com.marker.<nonce>`）時切換設定；`deactivateServer`。
   - 之後**結束標記**：同樣的 Logger 與層級，以 `.public` 記一個**不同的**標記；輪詢到它出現才停止擷取。起始與結束標記都出現，測試才有效。
   - 斷言：擷取結果不含任何負向標記（送出的漢字、它的注音 preedit、按鍵字元序列、bundle ID 標記、路徑標記），而且殼的 subsystem 輸出裡沒有 `<private>`（殼只准記靜態字串與回傳碼）。
   - verifier 在殼的輸出套用路徑暫時加一行 `logger.debug("\(commit, privacy: .public)")` 時，這個測試必須失敗；改成 `.private` 時，`<private>` 斷言必須失敗；改成 `NSLog("%@", commit)` 時也必須失敗。
6. `make selftest-bundled` 與 `build/shanjie.app/Contents/MacOS/shanjie --selftest` 都 exit 0；`make` 的過期防護有效（只動 `core/` 後 `make build`，執行檔會重新連結）；執行前後 `~/Library/Input Methods/`、`~/Library/Preferences/com.nyanako.inputmethod.shanjie.plist`、TIS 的輸入法清單都沒有變化。verifier 把 `build/shanjie.app/Contents/Resources/bigram.sjlm` 改名後再跑，必須 exit 非 0；還原後 exit 0。
7. 核心的 `cargo test` 與 PR #1 的 CI 步驟照舊全綠。
8. `release.yml`：在 PR 上無法真正簽章，所以 agent 只驗證結構：gate 只等 `ci.yml`；有 `workflow_dispatch` 試跑（不 publish）；build 工作用固定的 Rust 1.97.1，並在 `build-app.sh` 之前下載模型、以 `data/bigram.sjlm.sha256` 比對；sign 工作的 `environment: release`、只接受 `refs/tags/v*`、缺材料就失敗的檢查、一次性鑰匙圈在結束時刪除、驗證步驟齊全。第一次真正的發布由使用者推 tag 觸發（見下）。

**使用者實測（安裝後，由使用者執行並回報）：**
9. 使用者在 `release` environment 設好簽章材料，先以 `workflow_dispatch` 試跑簽章與公證，全綠後推 `v0.1.0` tag；release 工作全綠，Release 頁面有 `shanjie-0.1.0.zip`。
10. 下載、解壓，執行 `scripts/install-ime.sh <解壓後的 shanjie.app>`：「善解（標準）」「善解（倚天）」出現在輸入方式中；再執行一次（覆蓋安裝）仍正常，上一版保留為 `.shanjie-previous`，`~/Library/Input Methods/` 裡沒有 `.shanjie-staging-*` 殘留，執行中的 shanjie 行程載入的是新的那一份：`lsof -p $(pgrep -f 'Input Methods/shanjie.app/Contents/MacOS/shanjie') -d txt | grep MacOS/shanjie` 顯示的路徑在 `shanjie.app` 而不是 `.shanjie-previous`，且行程啟動時間（`ps -o lstart= -p <PID>`）晚於安裝時間。（`pgrep -fl` 只看命令列，bundle 被改名後舊行程仍顯示原路徑，分辨不出新舊。）
11. 在 TextEdit、備忘錄、Safari 各打陷阱集前 10 句（main 會提供按鍵清單）：組字有底線、候選窗出現在下方、數字選字、Enter 送出。
12. 對照截圖 1：候選條的形狀、號碼、選取色、深淺色模式；不像的地方記下來，進 S3b-2。
13. 組字中按 Caps Lock 切到英文：記錄組字是被送出、丟棄還是殘留（§6 的推論在此實測）；再切回；倚天模式打幾句。
14. Safari 密碼欄位：預期不出現組字。Terminal 開 Secure Keyboard Entry 或 `sudo` 時：記錄實際行為（這一版不存也不記任何內容，所以出現組字不算缺陷）。
15. 聊天 App（例如 Discord）與 TextEdit 打第 10 列，結果不同。
16. 實測 11 進行時，另開終端機執行 `/usr/bin/log stream --level debug --predicate 'process == "shanjie"'`（debug 等級不會寫進磁碟，事後用 `log show` 看不到），結束後確認輸出裡沒有陷阱句、App 的 bundle ID 或 Resources 路徑。

## 11. 實作決定

契約沒寫、實作時自行決定的事（含 coordinator 在實作中轉來的修訂 1–10 與 Makefile 範圍）：

- **連結 libcore.a**：`macos/Sources/CShanjie/module.modulemap` 是 `systemLibrary` target，直接引用 `core/include/shanjie.h`（不複製，避免和 `core/` 分岔）。`Package.swift` 以 `#filePath` 算出 repo 根目錄，用 `unsafeFlags` 把 `target/release/libcore.a` 的**完整路徑**交給連結器，不用 `-lcore`＋搜尋路徑，所以搜尋路徑上其他 `libcore` 不可能被誤用。平台設為 macOS 26：Homebrew rustc 的 std 是為 26.0 編的，設更低會出現大量「built for newer macOS」警告。
- **語言模式**：`ShanjieKit` 與測試是 Swift 6 嚴格模式，型別都標 `@MainActor`（C ABI 只在主執行緒呼叫）。`Shanjie` target 改用 Swift 5 模式：IMK 標頭沒有 actor 標註，`IMKInputController` 的 override 在 Swift 6 不能是 main actor isolated，所以每個回呼用 `MainActor.assumeIsolated` 在執行期檢查主執行緒（不在主執行緒就以靜態訊息中止）。
- **結構**：`Shell` 是行程唯一的狀態（一個引擎、一個候選窗、殼保存的候選陣列、弱參照的擁有者、`composing` 旗標）。`Session` 對應一個 IMK controller，持有它的 client；`ShanjieInputController` 只轉呼叫。`CoreEngine` 包 C handle，`CoreOutput` 在同一個呼叫裡複製成 Swift 值後立刻 `shanjie_output_free`；`CoreOutput` 不是 `Error`，也不實作 `CustomStringConvertible`／`CustomDebugStringConvertible`。
- **假 controller 怎麼模擬 IMK 生命週期**：測試的 `Controller` 就是一個 `Session`＋`FakeClient`；把它設成 `nil` 等於 IMK 釋放 controller。`Session` 用 `isolated deinit`，在主執行緒同步執行；deinit 時弱參照已經讀成 nil，規則照修訂 3(a)：擁有者讀成 nil 且組字非空 → `reset(1)`、隱藏候選窗、清空陣列。`claim()`（`handle`／`activateServer` 開頭）也會丟棄擁有者已是 nil 的組字，作為第二道。`FakeClient` 模擬文字框：`insertText` 會取代組字。
- **擁有者改變時**（修訂 3(b)）：`handle` 與 `activateServer` 先處理擁有者，擁有者換人時用新擁有者 client 的 bundle ID 重設 `set_profile`；`activateServer` 一律重設。非擁有者的 `deactivateServer`／`commitComposition` 完全不動作（不隱藏共用候選窗、不清陣列）。
- **IMKCandidates 只當顯示**：一個 server 一個 `IMKCandidates`（`kIMKSingleRowSteppingCandidatePanel`），屬於 `Shell`。用 `setCandidateData` 給這一頁、`selectCandidate(withIdentifier: candidateStringIdentifier(...))` 同步選取、`show(kIMKLocateCandidatesBelowHint)`。設定 `IMKCandidatesSendServerKeyEventFirst = YES`：依 `IMKCandidates.h`，候選窗顯示時按鍵先送到 controller；候選開著時核心處理每個鍵（s3a §3 第 3–8 條），所以候選窗不應該收到要處理的鍵。**未驗證**：這是文件描述的行為，沒有啟動輸入法實測（契約禁止）。仍可能把鍵交給候選窗的情況：核心直通的鍵（第 1 條，Command／Option／Control／Caps Lock 組合鍵）在候選開著時回傳 false。若實測（驗收 11）發現候選窗自己吃鍵或選取不同步，就是 §8 的停止條件，改做自建視窗（S3b-2）。滑鼠點選（`candidateSelected(_:)`）轉成數字鍵 `1`＋位置送進核心。
- **直通不碰 client**：核心回 `handled = 0` 且 `commit` 為空（s3a 第 1、22 條，狀態不變）時，殼不呼叫 `setMarkedText`，App 看到的跟沒按過一樣。
- **回傳碼非 0**（修訂 1）：先 `reset(1)`（核心只有碼 4 會自己丟棄），再 `setMarkedText("")`、隱藏候選窗、清空陣列、回傳 false；只記回傳碼。
- **表外的鍵與 `nil` 事件**：同一條路徑，有組字就 `reset(0)` 送出再回傳 false。`recognizedEvents` 只回 `keyDown`，`flagsChanged` 即使送來也直接回傳 false。
- **輸入模式**：模式 ID 是 `<bundle ID>.standard`／`.eten`，殼只看最後一段，`install` 也從執行中的 bundle ID 推出兩個模式 ID，所以拋棄式 bundle ID 的自測 bundle 也一致。IMK 每次啟用都會呼叫 `setValue`，模式沒變時不重建引擎。
- **日誌**：`Logger(subsystem: "com.nyanako.inputmethod.shanjie", category: "shell")`，只用 `.error`（失敗碼）與 `.debug`（「input mode switched」）。回傳碼以預設隱私內插（整數預設公開，不會出現 `<private>`）。自測與 `install` 的錯誤訊息用 `StaticString`＋數字寫到 stderr，型別上就不能內插輸入。
- **日誌行為測試**：`log stream --level debug --style ndjson`，predicate 只濾**行程**（修訂 9），`<private>` 斷言才看殼的 subsystem；ndjson 先解碼 JSON 再比對，標記不會藏在 `\uXXXX` 後面。實測發現：測試行程有 stderr 時，`NSLog` 只寫 stderr、**不進統一日誌**（2026-10-03，探測程式在 stderr 接管線、接 `/dev/null` 時都一樣），所以單靠 `log stream` 抓不到 `NSLog`。測試因此在負向動作期間另外把本行程的 stdout／stderr（fd 1、2）導進管線，用同一組負向標記檢查，並先寫一個探針確認擷取有接上。由 launchd 啟動的輸入法沒有 stderr，那時 `NSLog` 會進統一日誌，由 `log stream` 這一半負責。另加正向對照：殼自己的錯誤訊息（`shanjie_engine_new failed, code 3`）必須出現在擷取裡。
- **自測**：`Selftest.row10Standard` 是第 10 列在標準排列的按鍵；測試用獨立謄寫的 s3a §1 表從注音推出同一串，並斷言兩者相同。
- **建置**：`scripts/build-app.sh` 先比對 `data/bigram.sjlm.sha256` 再建置；`BUNDLE_ID`、`OUT_DIR`（必須是 repo 內的相對路徑，因為之後會 `rm -rf "$OUT_DIR/shanjie.app"`）、`SHANJIE_VERSION` 三個環境變數；Info.plist 與 `InfoPlist.strings` 由腳本產生，模式名稱用 `InfoPlist.strings` 的「模式 ID = 名稱」；`CFBundleDevelopmentRegion` 設 `zh-Hant`，所以非中文系統也顯示中文名。授權檔放 `Resources/LICENSES/`：`LICENSE`、`McBopomofo-MIT.txt`、`data.md`，以及 `CC-BY-SA-4.0-attribution.txt`（overlay 的署名照 `LICENSES/data.md`：Wikipedia 與 Wiktionary 貢獻者；模型：Wikipedia 與 Tatoeba 貢獻者）。
- **選單列圖示**：`scripts/make-icon.swift` 用 CoreText 以系統字型畫「解」加圓角方框，輸出 16 px 與 32 px 兩層的 TIFF；Info.plist 每個模式加 `TISIconIsTemplate = true`。淺色、深色模式下的實際外觀與 template 是否生效**未驗證**，由使用者在驗收 12 對照。
- **CI**：`ci.yml` 新增 `shell` 工作（`make bundle`、`swift test`、`make selftest-bundled SELFTEST_BUNDLE_ID=`、`scripts/check-app.sh`）；core 工作改讀 `data/bigram.sjlm.sha256`。`scripts/check-app.sh` 的檢查 6 比較執行前後的 `~/Library/Input Methods` 列表、偏好設定檔雜湊、以及 `TISCreateInputSourceList` 列出的全部輸入法（ID、模式、是否啟用；只讀取），並確認 `foo`、`install x`、`--selftest x`、`--SELFTEST`、空字串等參數都被拒絕。
- **release.yml**：gate 只等 `ci.yml`（`scripts/check-ci-gate.sh`，改寫自 syrtis，用假 `gh` 測過成功、失敗、沒有 run、API 失敗四種情況）。sign 工作不 checkout、不執行任何 repo 程式，只對 artifact 用 Apple 的工具；Team ID 寫死為 `2LJ882GPY8`，`vars.APPLE_TEAM_ID` 不同就失敗。手動觸發只接受 main，跑到驗證為止，不發布。
- **Makefile 的過期檢查**：照 syrtis 的 `relink_if_stale`／`rebuild_if_header_stale`，路徑改成新版 SwiftPM 的 `macos/.build/out/...`。實測 Swift 6.4 在 `libcore.a` 變動時本來就會重新連結；標頭內容變動沒辦法在不改 `core/` 的前提下實測（只改時間戳不會重編）。
- **日誌擷取的過濾條件**：`LogTests` 用 `processIdentifier == <測試行程的 PID>`（§10 驗收 5 已改成這個寫法）。log stream 只抓得到 unified log；`NSLog`／`print` 在測試行程裡只寫到 stderr／stdout，由同時進行的 stdout／stderr 擷取負責抓。
- **擷取期間扣住 issue**：`LogTests` 以 `nonisolated` 覆寫 `record(_:)`（XCTest 可能從任何執行緒記錄，扣住的清單有鎖），在 stdout／stderr 擷取期間扣住所有 XCTest issue（包括 `FakeClient` 等輔助程式裡的斷言），擷取結束後才記錄；所以擷取期間可以直接斷言。`defer` 確保提早離開時也會還原 stdout／stderr 並記錄扣住的 issue。`StdCapture.finish()` 可重複呼叫、每次回傳相同內容，`deinit` 也會還原。擷取期間取候選用 `first` 加 `if let`（不在擷取期間 throw）；`ShellTests` 用 `XCTUnwrap`，候選不足時是斷言失敗、不是陣列越界中止（verifier P4）。

## 12. 範圍外

展開網格、分頁、直式、表情候選、標籤、分段底色框、自建玻璃候選窗、設定頁與聊天 App 清單的修改介面、Homebrew cask、自動更新（Sparkle）、學習與 privacyGate（S4）、一鍵校正（H）、雲端（S6）。

**已知限制**：切換輸入模式（標準 ↔ 倚天）要重建引擎，會在主執行緒卡約 1 秒以上、期間按鍵直通。排列很少切換，先接受；之後在核心加 `set_layout`（S3b-2）。
