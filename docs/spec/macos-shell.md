# macOS 外殼（Swift 輸入法本體）

這份寫的是現在的行為。

範圍：Swift 寫的輸入法本體（`macos/`）把 macOS 的按鍵事件交給 Rust 核心、把核心的輸出畫到 App 上的部分，包括輸入法 bundle 的組成、組字擁有者、輸入選單、自建的玻璃候選窗（橫排、展開網格、直排）、設定視窗、關於頁的模型列表、App 圖示與選單列圖示。按鍵規則本身在核心，見 `docs/spec/keys-and-composition.md`；C ABI 見 `docs/spec/c-abi.md`；安裝程式、更新與註冊見 `docs/spec/install-and-update.md`；建置、簽章與發佈見 `docs/spec/release-and-ci.md`。

## 1. 程式結構與 bundle

### 1.1 SwiftPM 目標

Swift 程式放在 `macos/`（`Package.swift`，swift-tools 6.0，平台 macOS 26.0）。

| 目標 | 種類 | 內容 |
|---|---|---|
| `CShanjie` | systemLibrary | 只有 module map，直接引用 `core/include/shanjie.h`（不複製，免得和 `core/` 分岔） |
| `ShanjieKit` | library | 按鍵翻譯、輸出套用、組字擁有者、設定判斷、候選資料、候選窗的純函式與格子、設定視窗的 view model、自測。透過 `TextClient`、`CandidatePanel` 等協定和 IMK、AppKit 視窗隔開；測試一律經由真正的 C 核心，不注入假引擎 |
| `Shanjie` | executable | `IMKInputController` 子類別、`main.swift`、`NSPanel` 候選窗、設定視窗的 SwiftUI 畫面、UserDefaults 版的各個 store、Sparkle 的接線。Swift 5 語言模式（IMK 標頭沒有 actor 標註，`IMKInputController` 的 override 不能是 main actor isolated，每個回呼用 `MainActor.assumeIsolated` 在執行期檢查主執行緒） |
| `ShanjieInstall` | library | 輸入來源的註冊（連結 Carbon），給 `shanjie install` 與安裝程式共用；`ShanjieKit` 不碰 TIS |
| `ShanjieInstaller`、`UpdateHost`、`ShanjieUpdater` | executable／library | 安裝程式與獨立更新程式，見 `docs/spec/install-and-update.md` |
| `ShanjieKitTests`、`ShanjieInstallTests`、`UpdateHostTests` | test | XCTest |

- 連結核心：`Package.swift` 以 `#filePath` 算出 repo 根目錄，把 `target/release/libcore.a` 的完整路徑交給連結器，不用 `-lcore` 加搜尋路徑，所以搜尋路徑上別的 `libcore` 不可能被誤連。另連結 Accelerate（核心的小模型宣告了它的符號，Rust 的 `#[link(kind = "framework")]` 不會跟著靜態庫走）。
- `ShanjieKit` 與測試用 Swift 6 嚴格模式，型別標 `@MainActor`；C ABI 只在主執行緒呼叫。
- 唯一的外部相依是 Sparkle，釘死 2.10.0，`Package.resolved` 進版控，建置一律 `--force-resolved-versions`。Sparkle 只連進 `Shanjie` 與 `ShanjieUpdater`，`ShanjieKit` 與測試不碰它（更新的判斷放在 `ShanjieKit/Updates.swift`）。
- 本機 `make` 的過期防護：`target/release/libcore.a` 比 Swift 執行檔新就刪執行檔強迫重連；`core/include/shanjie.h` 比較新就刪 module cache 與匯入它的目標的產物（SwiftPM 不追蹤這兩樣）。

### 1.2 輸入法 bundle 的內容與 Info.plist

`scripts/build-app.sh` 組出 `<資料夾>.app`（資料夾與名稱見 §1.3），內容：

- `Contents/MacOS/shanjie`：執行檔。
- `Contents/Resources/`：詞庫與資料檔（`mcbpmf-data.txt`、`overlay-add.tsv`、`sandhi-add.tsv`、`names-add.tsv`、`demote.tsv`、`variant-order.tsv`、`std-exempt.tsv`、`rerank-protect.tsv`、`digits.tsv`、`mixed-words.tsv`，見 `docs/spec/lexicon-and-packs.md`）、`packs/`（詞包）、`data/models.tsv` 列的每個模型檔加 `models.tsv`（見 `docs/spec/models.md`，§10）、`shanjie.tiff`（選單列圖示）、`Assets.car` 與 `AppIcon.icns`（App 圖示）、`zh-Hant.lproj` 與 `en.lproj` 的 `InfoPlist.strings`、`LICENSES/`。只有出貨的 bundle ID 另帶 `container-migration.plist`。
- `Contents/Frameworks/Updater/Sparkle.framework`（沒有 XPC 服務）與 `Contents/Helpers/善解更新.app`，見 `docs/spec/install-and-update.md`。
- 簽章帶三個 entitlements：App 沙盒、網路用戶端、以 `<bundle ID>_Connection` 為名的 mach-register 例外；沒有 `get-task-allow` 與 `disable-library-validation`。細節與檢查見 `docs/spec/install-and-update.md`、`docs/spec/release-and-ci.md`。

`Info.plist`（由腳本產生）的鍵：

| 鍵 | 值 |
|---|---|
| `CFBundleIdentifier` | `$BUNDLE_ID` |
| `CFBundleExecutable` | `shanjie` |
| `CFBundleName`／`CFBundleDisplayName` | 英文名／中文名（§1.3） |
| `CFBundleDevelopmentRegion` | `en` |
| `CFBundleIconFile`、`CFBundleIconName` | 都是 `AppIcon` |
| `LSMinimumSystemVersion` | 26.0 |
| `LSUIElement`、`LSHasLocalizedDisplayName` | true |
| `InputMethodConnectionName` | `${BUNDLE_ID}_Connection`（等於 mach-register 例外） |
| `InputMethodServerControllerClass`、`InputMethodServerDelegateClass` | `ShanjieInputController` |
| `TICapsLockLanguageSwitchCapable` | true |
| `TISInputSourceID`、`TISIntendedLanguage` | `$BUNDLE_ID`、`zh-Hant` |
| `ComponentInputModeDict` | 恰好一個輸入模式 `$BUNDLE_ID.zhuyin`（`TISIntendedLanguage` zh-Hant、`tsInputModeScriptKey` smTradChinese、選單與面板圖示 `shanjie.tiff` 且 `TISIconIsTemplate`），`tsVisibleInputModeOrderedArrayKey` 只有它 |
| `Sparkle` 與 `ShanjieUpdateBuild` 相關鍵 | 見 `docs/spec/install-and-update.md` |

- 輸入方式清單裡只有一個「善解輸入法」；鍵盤排列（標準／倚天）在輸入選單與設定視窗裡選，不靠輸入模式 ID。殼不覆寫 `setValue(_:forTag:client:)`，任何模式 ID（包括舊版還沒被停用的 `.standard`、`.eten`）都不會改變排列。
- 版本號來自 `SHANJIE_VERSION`，沒有就取最新的 git tag（去掉 `v`），再沒有就 `0.0.0`；必須是 `主.次.修`。

驗證：`scripts/check-app.sh` 檢查 bundle：`plutil -lint`、單一輸入模式、資料夾與名稱、`InfoPlist.strings`、資料檔與 repo 內相同、圖示檔與 Info.plist 的圖示鍵、模型（§10）、entitlements。`scripts/check-installer.sh` 對安裝程式做同樣的圖示檢查。

### 1.3 名稱與在地化

名稱只由 `BUNDLE_ID` 決定：

| bundle ID | 資料夾 | 中文名／英文名 |
|---|---|---|
| `com.nyanako.inputmethod.shanjie`（出貨） | `善解輸入法.app` | 善解輸入法／Shanjie |
| 其他（`build-app.sh` 與 `make bundle` 的預設是 `….shanjie.dev`） | `善解（開發版）.app` | 善解（開發版）／Shanjie (Dev) |

- Info.plist 的基礎 `CFBundleDisplayName` 等於資料夾名稱（Finder 只在兩者一致時套用在地化名稱）；`zh-Hant.lproj` 給中文名，`en.lproj` 與 `CFBundleName` 給英文名；`InfoPlist.strings` 另有輸入模式 ID 的名稱。非中文系統顯示英文名。
- 不變的 ASCII 名稱：執行檔 `Contents/MacOS/shanjie`、bundle ID、Release 附件 `shanjie-<版本>.zip`（解壓後是中文名的 App；用 Finder 或 `ditto` 解壓，`unzip` 指令可能把中文檔名弄亂）。
- 輸入模式 ID、連線名稱、更新程式的 bundle ID（`<ID>.updater`）、偏好設定網域、沙盒 container 都跟著 bundle ID。
- `build-app.sh` 在建置輸出資料夾放 `.metadata_never_index`，並在建置前清掉兩種名稱的舊 bundle；`build/` 裡的 bundle 不得啟動或註冊，實際使用與驗收以 `~/Library/Input Methods` 裡安裝的那份為準；`make clean-bundle` 對兩種名稱逐一 `lsregister -u` 後刪除。

### 1.4 選單列圖示與 App 圖示

**選單列圖示**（`shanjie.tiff`）：`scripts/make-icon.swift` 用 CoreText 畫，單色 template，不下載字型或圖。16 pt 畫布、圓角方框（內縮 1 pt 的 14 × 14，圓角 3 pt，線寬 1.2）加上系統字型 10.5 pt（zh-Hant）的「解」，字形外框置中；輸出 16 px 與 32 px 兩個表示法的 TIFF（都是 16 pt 大小）。32 px 那張不可再加 scale：rep 的 size 是 16 pt 時 context 已經把點對到像素，多乘一次只會畫到右上四分之一。

**App 圖示**（輸入法 App 與安裝程式用同一個）：

- 朱紅 `#B83A2E`（`srgb:0.72157,0.22745,0.18039`）的 `automatic-gradient` 底，淺色與深色外觀相同；圓角框與「解」是兩層白色的玻璃圖層，系統畫出來是透著朱紅的乳白。
- 原始檔 `packaging/AppIcon.icon/`（`icon.json`、`Assets/frame.svg`、`Assets/glyph.svg`）。參數：字那一組 `glass`、`lighting individual`、陰影 `layer-color` 0.6、`specular`、透光 0.5；框那一組 `glass`、陰影 `neutral` 0.4、`specular`、透光 0.5。這些是挑樣稿選的值，不是 Icon Composer 的預設。
- 「解」是 Noto Serif CJK TC Black 的字形外框轉成的 SVG 路徑，App 與 CI 都不需要字型檔。`scripts/make-app-icon.swift <字型檔> <輸出資料夾>` 重新產生兩個 SVG，只接受 SHA-256 與字型名稱都符合的那個檔（上游改版不會讓字形悄悄改變）。幾何是具名常數：畫布 1024、框 696 pt 方形、圓角 120、線寬 22、字 560 pt，整體再放大 1.10。字型授權為 OFL，圖示用字形外框不受 OFL 約束，仍在 `LICENSES/data.md` 註明出處。
- 建置：`build-app.sh` 與 `build-installer.sh` 都呼叫 `scripts/compile-app-icon.sh <絕對路徑的 Resources>`：`xcrun actool … --app-icon AppIcon --platform macosx --minimum-deployment-target 26.0` 把 `.icon` 編成 `Assets.car` 與 `AppIcon.icns`。`--app-icon` 和 `.icon` 資料夾名不符時 actool 結束碼 0 卻什麼都不寫，所以編完會確認兩個檔存在且不是空的。每次編出的 `Assets.car` 位元組不同（`AppIcon.icns` 相同）。
- 檢查：`check-app.sh`、`check-installer.sh` 確認兩個檔存在，且 `CFBundleIconName`、`CFBundleIconFile` 都是 `AppIcon`。

## 2. 行程、引擎與組字擁有者

### 2.1 引擎的建立與重建

- `main.swift` 的 `runServer()` 先跑英文預設值的一次性重設（`docs/spec/english-mixed.md`），建 `NSApplication`、`IMKServer`（名稱取 `InputMethodConnectionName`），之後才建 `Shell`；`Shell` 持有行程唯一的引擎、候選窗與組字擁有者。接著建 `Updates`／`Updater`（`docs/spec/install-and-update.md`）與設定視窗，設好 `onOpenSettings`、`onOpenCandidateSettings`。
- **同一時間只有一個引擎**（每個約 240 MB）。`Shell.init` 先讀鍵盤排列偏好，再呼叫唯一一次 `build()`；先建再切會建兩次。
- `build()` 依序：釋放舊引擎；用目前的詞包遮罩建引擎（`shanjie_engine_new_packs`，沒有詞包用 `shanjie_engine_new`）；`load_lm(bigram.sjlm)`；成功才 `load_tinylm`，再成功才 `load_colloquial`（小模型與常談表載入失敗只記回傳碼，引擎照常，語意校正不啟用或不帶常談項）；然後把殼持有的每一個設定**每次重送一遍**（重建的引擎一律從核心預設值開始）：避免敏感字詞、即時預測、可省略韻母、中英混打與它的三個選項、ASCII 狀態、模糊輸入旗標、候選窗方向、語意校正與強度、聊天／書面、標點表、選字記憶資料夾。
- 詞包載入失敗的退讓：合併建置失敗時，先建一個不帶詞包的引擎判斷是不是詞包的錯（只有一個啟用的詞包檔案在時沒有「少一個」可試，這個引擎成功就直接留下，`docs/spec/lexicon-and-packs.md` §4.3）；是，就對每個啟用中、檔案也在的詞包依設定順序各試「少這一個」，第一個成功的留下，其餘詞包照開；都不行就建不帶詞包的引擎。偏好值不動，也不顯示錯誤，下一次重建會再試。連不帶詞包的引擎都建不出來時記兩個回傳碼，結束。
- 引擎建不出來：沒有引擎，所有按鍵直通（`Session.handle` 回傳 `false`）。載入 LM 失敗：沒有 LM 的引擎仍可打字，只記回傳碼。
- 換鍵盤排列或切詞包會重建：先把組字送回擁有者（沒有擁有者就丟棄），再改設定，再 `build()`。重建在主執行緒阻塞約一秒以上，期間按鍵直通；排列很少切換，先接受。其餘設定（開關、強度、方向、聊天／書面）都不重建，直接送給現有的引擎。
- 所有 C ABI 呼叫都在主執行緒。

### 2.2 組字擁有者

IMK 對每個 client 各建一個 controller，但引擎只有一個。殼用 `weak var owner: Session?` 記住目前組字屬於誰；「仍有效」的定義是弱參照不是 nil。不用 `ObjectIdentifier`（被釋放的 controller 位址可能被新的重用）。

- `Session.claim()`（`handle`、`activate`、選單切換前呼叫）：已是擁有者就什麼都不做；組字區有字且擁有者不是自己時，擁有者還在就 `finish(mode: 0)` 把組字送回**它自己的** client，否則丟棄（`discardOrphan`）；之後擁有者改成自己。**擁有者一改變就依新擁有者的 client 重新 `set_profile`**（§5）。
- `activate()` 的順序固定：`claim()`、讀 bundle ID 並套用聊天／書面、同步 ASCII 狀態（`docs/spec/english-mixed.md`）。
- `deactivate()`／`commitComposition()`：**只有呼叫者就是擁有者**才動作；不是擁有者時不碰組字，也不碰共用的候選窗。擁有者的 `deactivate` 把組字用 `reset(0)` 送回 client；安全輸入（`IsSecureEventInputEnabled()`，以閉包注入 `Shell`，測試用假閉包）生效時改 `reset(1)` 丟棄，免得把組字送進剛取得焦點的密碼欄；最後隱藏候選窗。安全輸入只在 `deactivate` 檢查；它是全系統旗標，任何 App 開著時其他的送出都檢查，會讓打到一半的字悄悄消失。
- `isolated deinit`：此時弱參照已讀成 nil，若 `shell.owner == nil` 且 `composing`，就 `discardOrphan()`（`reset(1)`、隱藏候選窗）。
- 回傳碼非 0：記碼（只記碼）、`reset(1)` 讓核心也丟棄（核心只在碼 4 時自己丟棄）、清掉 client 的組字、隱藏候選窗、`composing = false`、回傳 `false`。

### 2.3 IMK 回呼

`ShanjieInputController`（`@objc(ShanjieInputController)`）每個 client 一個，持有一個 `Session`；`ClientAdapter` 把 `TextClient` 協定接到 `IMKTextInput`。

- `recognizedEvents` 回 `keyDown` 與 `flagsChanged`（後者給單按 Shift 切換中英，`Session.handle` 對它永遠回 `false`，App 看到的 Shift 和沒有輸入法時一樣）。
- `ClientAdapter.lineRect(cursor:)`：取組字區內的位置（不是整份文件的位置）；從游標前一個字開始，client 回的矩形原點是 (0, 0) 就往前找，都沒有就回 `nil`；沒有組字時回 `nil`。`caretRect()`：沒有組字時用索引 0。
- `ClientAdapter.appearance`：候選窗跟著被輸入的 view 的淺色或深色。IMK 沒有公開呼叫；client 物件有私有的 `viewEffectiveAppearance`、`windowEffectiveAppearance`（型別編碼 `@16@0:8`）。只在方法還是這個形狀時才用，形狀不同或沒有就退回系統外觀。client 是轉發用的 tracer，要從 `type(of:)` 讀方法，不能用 `object_getClass`。
- 滑鼠點選候選見 §8.1。選單見 §7。

## 3. 按鍵翻譯

`KeyMap`（`ShanjieKit/KeyMap.swift`）只讀實體鍵碼（`keyCode`）與 `modifierFlags`，不讀 `event.characters`，所以鍵盤配置與死鍵不會改變核心收到的東西。

- `kind`：Return 36 與小鍵盤 Enter 76 → ENTER；Space 49；Delete 51 → BACKSPACE；Forward Delete 117 → DELETE；Esc 53；←123 →124 ↑126 ↓125；Home 115；End 119；Tab 48。PAGE_UP 14、PAGE_DOWN 15 見下。
- `CHAR` 的 `ch`：依鍵碼查 **ANSI 實體鍵位表**取不按 Shift 的 ASCII（a–z、0–9、`` ` ``、`-`、`=`、`[`、`]`、`\`、`;`、`'`、`,`、`.`、`/`）。
- `modifiers`：bit0 Shift、bit1 Control、bit2 Option、bit3 Command、bit4 Caps Lock。
- **表外的鍵**（數字鍵盤、功能鍵等）與 IMK 傳入的 `nil` 事件不送進核心：組字區有字時先 `finish(mode: 0)` 送出，再回傳「不處理」；組字區空時直接回傳「不處理」。
- **例外：Page Up／Down**。`KeyMap.pageKeys` 把 `kVK_PageUp`（116）、`kVK_PageDown`（121）對到 14／15，但 `KeyMap.translate` 不含它們；`Session.handle` 只在直排候選窗開著（最後套用的輸出 `candidate_vertical` 為 1）而且沒有任何修飾鍵（Shift、Control、Option、Command、Caps Lock）時才轉送，其餘情況（橫排候選、網格、組字中、未完成音節、預測列、組字區空、帶修飾鍵）都走上一條的表外鍵路徑，行為和沒有這個功能時完全相同。核心收到 14／15 而當下不是直排候選窗時不處理、不改狀態、不送出（`handled` 0）。
- 英文狀態（單按 Shift 切換）下組字區空時，所有按鍵直接交給 App，不經過核心；組字中且核心有影子時以 `event.characters` 產生字元鍵。見 `docs/spec/english-mixed.md`。
- 按 Enter 時（候選窗沒開），送進核心前先讓畫面上的組字追上正在跑的語意校正（最多等 `ENTER_RERANK_WAIT_MS`），見 `docs/spec/semantic-rerank.md`。

驗證：`KeyMapTests`（`testEveryAnsiKeyIsItsUnshiftedCharacter`、`testSpecialKeys`、`testModifierBits`、`testKeysOutsideTheTablesAreNotTranslated`、`testBothLayoutsThroughTheCore`；特殊鍵碼另對 Carbon 的 `kVK_*` 交叉核對）。

## 4. 輸出套用

核心的輸出在收到的同一個呼叫裡複製成 Swift 值，立刻 `shanjie_output_free`。`CoreOutput` 不是 `Error`，也不實作 `CustomStringConvertible`／`CustomDebugStringConvertible`，輸入文字因此不會經由當機報告或日誌的描述漏出。

`Session.apply` 的順序：

1. `commit` 非空：`insertText(commit, replacementRange: NSRange(location: NSNotFound, length: 0))`；「中英文之間自動加空格」開著時先交給 `CJKSpacing.apply`（`docs/spec/english-mixed.md`）。
2. `preedit` 非空：`setMarkedText`，整段單線底線，中英混打的英文段改粗底線（範圍夾在文字長度內，核心給錯範圍也不會丟例外）；選取範圍 `NSRange(location: cursor_utf16, length: 0)`。`preedit` 變成空而原本有組字時，用空字串清掉。組字字串、游標、英文段的起點與長度都和這個 session 上一次送出的相同、而且這次沒有 `commit` 時，不重送 `setMarkedText`（例如 Tab 進入或移動預測列）：Chromium 先把輸入法吃下的鍵交給網頁（keyCode 229、`key` 保留原鍵），再套用這次的組字；網頁在 Tab 上移走焦點時（對話框的焦點陷阱），遲到的組字會讓欄位拿回焦點、多出一份文字。用空字串清掉組字時一併忘記上一次送出的值，所以之後打出相同的組字照樣會送；有 `commit` 時一定重送，因為 `insertText` 已經換掉 client 的 marked text。
3. `handled = 0` 且 `commit` 為空是純直通（核心狀態沒變）：殼完全不碰 client，App 看到的和沒按過一樣。`handled = 0` 但有 `commit`：先套用再回傳 `false` 讓 App 處理這個鍵。
4. 候選：`candidates` 為空且沒有英文段提示，或被「瀏覽器第一個字不顯示預測」隱藏（`docs/spec/prediction.md`）時，隱藏候選窗並清掉游標行快取；否則取游標所在行矩形（同一個 `preedit` 與游標的重複輸出重用上一次的答案，因為問 client 是同步 IPC）交給候選窗。英文段開著時，候選窗的位置改以那一段的起點為準，並顯示一行提示（`docs/spec/english-mixed.md`）。
5. 最後取出核心留下的語意校正工作交給執行器（`docs/spec/semantic-rerank.md`）。

## 5. 聊天／書面的判斷

### 5.1 判斷函式

`Shell.profile(bundle:mode:overrides:)` 是決定聊天或書面設定的**唯一**地方：

- 模式 `chat` → 聊天，`formal` → 書面。
- 模式 `auto`：bundle ID 是 nil 或空字串 → 書面；`overrides` 有這個 bundle → 用它；否則在內建聊天 App 清單（`Shell.chatApps`）就是聊天，其餘書面。
- 內建聊天 App：`com.hnc.Discord`、`jp.naver.line.mac`、`com.apple.MobileSMS`、`com.tinyspeck.slackmacgap`、`ru.keepcoder.Telegram`、`com.tdesktop.Telegram`、`net.whatsapp.WhatsApp`、`com.facebook.archon`。
- 結果以**列舉**（聊天 0、書面 1）傳給核心（`shanjie_engine_set_profile`）；bundle ID 不傳給核心、不寫日誌。

呼叫時機：`activate`、組字擁有者改變（`handle` 的 `claim()` 成功時）、選單切換之後（§5.3）、設定視窗改切換方式或移除 App 之後。

### 5.2 儲存

- `ProfileModeStore.profileMode: String?`，UserDefaults 鍵 `profileMode`，值 `auto`／`chat`／`formal`；沒設過或不是這三個值都當 `auto`。
- `AppProfileStore`：讀出**原始值**（`object(forKey: "appProfiles")`），寫入收 `[String: String]`（空的就刪鍵）。鍵 `appProfiles`，值是 bundle ID → `chat`／`formal`；整份是一個頂層鍵，不用 key path（bundle ID 有點）。
- `AppProfiles.parse(_:)` **逐項**過濾：根不是 dictionary → 空；只留鍵是非空字串、值是字串 `chat` 或 `formal` 的項目，其他略過且不影響有效的項目（不能整份 `as? [String: String]`：一個壞值會讓整份變 nil，下一次寫入把有效設定也蓋掉）。略過的項目在下一次寫入時消失。這條路徑沒有 `as!` 或強制解包。
- `Shell.init` 兩個 store 都必填、沒有預設值；值在 init 時讀進快取，之後只經過 `Shell` 的 setter。輸入法執行中用 `defaults` 手動改的值不會被讀到，下一次切換會蓋掉。
- `appProfiles` 只存和內建判斷不同的 App：`Shell.setAppProfile` 在新值等於內建判斷時移除該項，所以同一個 App 按兩次回到沒有個別設定。

### 5.3 選單與設定視窗的切換

- 選單項目「這個 App 用聊天設定」（固定字串，不含 App 名稱）：勾選＝這個 client 目前的結果是聊天；client 的 bundle ID 是 nil 或空字串時不顯示；模式不是 `auto` 時顯示成反灰（勾選照強制的設定）。按下去存的是目前結果的相反，然後走 `claim()` 再 `applyProfile()`（`set_profile` 重算組字並回傳快照），最後發 `didChangeSettings`。必須先 `claim()`：`applyProfile()` 沒有擁有者檢查，只呼叫它會把別的 App 的組字重算後送進這個 client。
- 設定視窗改「切換方式」或移除一個 App：`Shell` 存下、發 `didChangeSettings`，並對目前的組字擁有者（有的話）重設 profile；擁有者本來就擁有組字，不需要 `claim()`。
- 「個別設定的 App」清單的畫面見 §9.3。

### 5.4 隱私

- 只有使用者在選單上切換過的 App，其 bundle ID 才存在 App 自己的 UserDefaults（`appProfiles`，值只有 `chat`／`formal`）；其他 App 的 bundle ID 不存。照舊不傳給核心、不寫日誌。
- 「清除選字記憶…」不清這份清單（它是設定，不是學習資料）；它和其他偏好一樣會被 Time Machine 備份（「不要備份選字記憶」只管學習資料夾）。
- `appProfiles` 不影響學習的暫停條件：`learningPause` 只看安全輸入、寫死的密碼管理器清單與取不到 bundle ID（`docs/spec/learning.md`），不讀這份清單（密碼管理器被設成聊天，照樣暫停學習）。
- `didChangeSettings` 是程序內的 `NotificationCenter` 通知，沒有 `userInfo`。

### 5.5 驗證

`ProfileChoiceTests`：`testProfileTable`（三種模式 ×（內建聊天 App、其他 App、nil、空字串、其他 App 設成聊天、內建聊天 App 設成書面））、`testMenuSwitchChangesTextEditToChatAndDiscordToFormal`、`testForcedModes`、`testSwitchMidCompositionRecomputesAtOnce`、`testMenuItemPositionCheckAndAvailability`、`testPressingTwiceLeavesNoEntry`、`testUnsetOrUnknownProfileModeIsAuto`、`testParseFiltersEntryByEntry`、`testStoredProfileModeAppliesFromTheStart`、`testStoredAppProfilesApplyFromTheStartAndSurviveTheNextWrite`（init 必須讀 store；壞項目共存時有效項目留下；下一次寫入不把存檔蓋掉）、`testSwitchInAnotherSessionCommitsTheOwnersCompositionToItsOwnClient`、`testWindowChangesReapplyToTheComposingOwner`、`testPickerWritesTheStoreAndPosts`、`testListFollowsTheMenuAndRemoveDropsOne`。

## 6. 日誌規則、自測與命令列

### 6.1 日誌規則（R2）

- 日誌只用 `os.Logger`（subsystem `com.nyanako.inputmethod.shanjie`，category `shell`），訊息**只能是靜態字串與 C ABI 回傳碼**；不得內插任何其他值（含 keyCode、`ch`、游標位置、數量、路徑、bundle ID、組字、候選、送出內容）；不得用 `.public`，也不得用雜湊遮罩。不用 `print`、`NSLog`、`debugPrint`、`dump`。實際只用 `.error`（失敗碼）、`.notice`（標點表不可用）與 `.debug`（「input mode switched」）。回傳碼用預設隱私內插（整數預設公開）。
- 當機報告：`fatalError`、`precondition` 不內插任何值；禁用 `try!`；裝著輸入文字的型別不是 `Error`，也不實作 `CustomStringConvertible`／`CustomDebugStringConvertible`。
- 命令列模式的錯誤訊息用 `StaticString` 加數字寫到 stderr，型別上就不能內插輸入。候選窗與設定視窗的程式不記任何日誌。
- 系統框架在輸入法程序裡自己記的 Debug 訊息（例如 LaunchServices 的前景 App 通知）不在這條規則內；規則管的是善解自己的 subsystem 與程式。

驗證：`LogTests.testShellLogsNothingButStaticTextAndCodes`。

- 另起 `/usr/bin/log stream --level debug --style ndjson`，predicate 只依測試行程的 PID 過濾；同時擷取測試行程自己的 stdout／stderr（有 stderr 的行程裡，`NSLog` 與 `print` 只寫到 stderr／stdout，不進 unified log）。
- 起始標記：殼同一個 Logger、同一個 subsystem、以 `.public` 記一個標記，輪詢到它才開始負向動作；10 秒沒看到就判失敗（擷取沒接上）。結束標記同理，不同的標記，兩者都出現測試才有效。
- 負向動作（各帶一個隨機 nonce）：打字送出標記句、回傳碼非 0 的路徑、資料目錄路徑含標記時建引擎失敗、假 client 的 bundle ID 含標記時切換設定、`deactivate`、用帶標記的 bundle ID 按兩次 `toggleAppChat`、再用 `SettingsModel` 改模式並移除該 App。
- 斷言：擷取結果不含任何負向標記（送出的漢字、注音 preedit、按鍵字元序列、bundle ID、路徑），殼的 subsystem 輸出裡沒有 `<private>`；正向對照：殼自己的錯誤訊息必須出現。擷取期間 XCTest 的 issue 先扣住，擷取結束才記錄，免得失敗訊息把句子帶進擷取內容。

### 6.2 命令列參數

可執行檔只接受：沒有參數（以輸入法身分執行）、剛好 `install`、剛好 `--selftest`。其他參數一律印 `usage: shanjie [install | --selftest]` 並以 64 結束，沒有任何副作用。輸出都是固定文字，不含路徑、bundle ID 或輸入。

`install` 透過 `ShanjieInstall` 註冊輸入來源，行為與結束碼見 `docs/spec/install-and-update.md`。

### 6.3 自測

`shanjie --selftest`（`ShanjieKit/Selftest.swift`）：從 `Resources/` 建引擎並檢查，全部通過才 exit 0。不呼叫 TIS、不寫檔案或 UserDefaults、不建 `NSApplication` 或 IMK server；只往 stderr 寫固定訊息與回傳碼。檢查依序：

1. 不帶詞包的引擎，標準排列載入 LM；在聊天與書面設定下打 dev302 第 131 列（`ㄅㄣˇ ㄕˋ ㄒㄧㄚˋ ㄓㄡ ㄙㄢ ㄏㄨㄟˋ ㄧㄡˇ ㄉㄚˋ ㄩˇ`），分別得到 `本市下週三會有大雨` 與 `本是下週三會有大雨`（書面設定有 `trigram.sjt3` 才是「本是」，所以也擋得到缺表）；第 10 列（`ㄑㄧˊ ㄓㄨㄥ ㄅㄠˋ ㄍㄠˋ ㄇㄧㄥˊ ㄊㄧㄢ ㄧㄠˋ ㄐㄧㄠ`）在聊天設定得到 `期中報告明天要交`（有 `trigram-chat.sjt3` 才是「交」，擋得到缺聊天表）。
2. 帶詞包（動漫與遊戲、資訊與 AI、迷因與實況用語、美妝與保養，和殼出貨時一樣）的引擎重做 1，再要求 `ㄕㄣˊ ㄧㄡˊ ㄕˇ` 得到 `榊遊矢`、`ㄧㄥˊ ㄇㄨˋ ㄅㄠˇ ㄏㄨˋ ㄑㄧˋ` 得到 `螢幕保護器`、`ㄇㄟˊ ㄘㄞˇ ㄍㄠ` 得到 `眉彩膏`、`ㄎㄜˇ ㄧˇ ㄙㄜˋ ㄙㄜˋ` 得到 `可以色色`（`meme-add.tsv` 的第一列）；詞包缺檔、壞檔或沒生效都失敗。
3. 載入小模型：第 65 列（`ㄨㄛˇ ㄉㄥˇ ㄧ ㄒㄧㄚˋ ㄗㄞˋ ㄍㄨㄛˋ ㄑㄩˋ`）語意校正開時得到 `我等一下再過去`、關時得到 `我等一下在過去`。
4. 載入常談表：校正開、左文 `這間店的蛋糕很好吃`，第 108 列（`ㄋㄧˇ ㄧㄠˋ ㄅㄨˊ ㄧㄠˋ ㄔㄤˊ ㄔㄤˊ ㄎㄢˋ`）得到 `你要不要嚐嚐看`。
5. 開中英混打，打 `rust` 加空白，組字區剛好是一個英文 token `rust`（無候選、沒有開著的英文段）。

選擇這幾列的原則：每一列要在「該載入的檔」有或沒有時給出不同的答案，所以缺檔、壞檔不會悄悄通過；模型或解碼改變導致兩種設定的答案變得相同時，換另一列（常數 `row131Standard`、`row10Standard`、`rerankRowStandard`、`colloquialRowStandard` 附有量測方式）。按鍵字串用空白代表第一聲，測試另從注音獨立推出同一串並斷言相同。

沙盒：bundle 在沙盒裡，第一次執行 `--selftest` 時系統會建立那個 bundle ID 的 container；出貨 ID 的第一次執行還會把學習資料搬進去。所以本機只對拋棄式 ID 的 bundle 跑（`make selftest-bundled`，bundle 在 `build/selftest/善解（開發版）.app`），出貨 ID 的 selftest 只在 CI 跑。

驗證：`SelftestTests`（`testPassesWithTheFullResources`，以及缺詞包、缺 IT 詞包、缺美妝詞包、缺迷因詞包、壞詞包、壞美妝詞包、壞迷因詞包、缺小模型、壞小模型、缺常談表、壞常談表、缺模型、缺 `classes.sjc`、缺書面與聊天 trigram、壞 trigram、缺詞庫、非模型檔各一個失敗案例；`lastFailure` 讓測試分辨是哪一項失敗，載入被拒不等於句子錯）；`make selftest-bundled` 與 `scripts/check-app.sh` 在 CI 執行；把 bundle 裡的 `bigram.sjlm` 改名後 selftest 必須 exit 非 0。

## 7. 輸入選單

`IMKInputController.menu()` 每次呼叫都重建 `NSMenu`；項目由 `Session.menu`（`ShanjieKit`，`MenuEntry` 陣列）產生，App 只把它轉成 `NSMenuItem`：一般項目是 `NSMenuItem(title:action:keyEquivalent:)`，小標題是 `NSMenuItem.sectionHeader(title:)`，分隔線是 `NSMenuItem.separator()`（都是系統原生元件；`sectionHeader` 從 macOS 14 起有，App 最低 macOS 26）。每個動作有**獨立的 selector**（IMK 呼叫時 `sender` 不一定是 `NSMenuItem`，也不看 `sender`）；沒有動作或 `enabled == false` 的項目不設 action，所以選單把它畫成反灰，勾選狀態照顯示。

項目依序（括號是出現條件）：

| 項目 | 動作 | 說明 |
|---|---|---|
| 學習已暫停（安全輸入）或（此 App） | 無 | 學習被暫停時才有，見 `docs/spec/learning.md` |
| 標準鍵盤、倚天鍵盤 | `layout` | 互斥打勾；`selectLayout` 先走 `switchMode`（§2.1），再存偏好；選目前的排列不重建，仍寫一次偏好 |
| 避免把敏感字詞排在前面 | `toggleDemote` | 預設開，鍵 `demoteSensitive` |
| 即時預測 | `togglePrediction` | 預設開，鍵 `prediction` |
| 可省略韻母 | `toggleAbbreviation` | 目前 App 傳 `showsAbbreviation: false`，整個隱藏且一律關；顯示時預測關閉會反灰 |
| 中英混打（實驗） | `toggleEnglish` | 預設開，鍵 `english`；倚天排列時反灰 |
| 語意校正 | `toggleRerank` | 預設開，鍵 `rerank`；敏感字詞降權關閉或小模型沒載入時反灰 |
| 分隔線、小標題「已開啟的詞包」、每個詞包一行、分隔線 | 無 | 引擎實際載入的詞包，每個一行、不能點；小標題把它下面到分隔線為止的項目歸成一組，所以前後各一條分隔線（選單其他地方沒有分隔線）；都沒載入就整段（含分隔線）不出現；詞包的開關只在設定視窗 |
| 這個 App 用聊天設定 | `toggleAppChat` | §5.3 |
| 有新版本 X，按此更新… | `installUpdate` | 只在已知有新版本時出現，檢查中反灰；見 `docs/spec/install-and-update.md` |
| 善解設定… | `openSettings` | 開設定視窗（§9） |
| 清除選字記憶… | `clear` | 走確認視窗（下） |
| 不要備份選字記憶 | `toggleBackup` | 對學習資料夾設 `isExcludedFromBackup` |
| 選字記憶無法存檔 | 無 | 只在核心回報寫入失敗或資料夾開不了時出現 |

- 開關類項目在擁有者組字中切換：核心重算並回傳快照，擁有組字的 session 立刻顯示（例如「搞完這波」與「睪丸這波」之間馬上變）；不是擁有者的 session 按選單不改別人的組字。
- 項目標題都是固定字串（不含 App 名稱、路徑或數量）。
- **清除選字記憶…**的視窗是 `NSAlert`，不用 `runModal`（會讓輸入法在視窗開著時停止服務其他 App），按鈕的 target／action 回呼。「取消」排第一個、是預設鈕（Return），「清除」標為破壞性。輸入法是 agent app（`LSUIElement`）：開窗時記下前景 App，`NSApp.activate()`，視窗層級 `.modalPanel`，置中；按下按鈕後把焦點還給記下的 App（後續的失敗視窗開著時不還）。清除失敗另開一個「清除選字記憶失敗」視窗，不假裝完成。同時只開一個。

驗證：`ShellTests` 的選單與偏好案例（`testMenuSelectionCommitsSwitchesAndStores`、`testStoredEtenLayoutAppliesFromTheStart`、`testMissingOrInvalidLayoutIsStandard`、各開關的 `…MenuItemTogglesStoresAndSurvivesRestart`）、`SettingsTests.testMenuHasTheSettingsItemBeforeClear`；詞包那一段：`ShellTests` 的 `testPackLineNamesTheLoadedPacks`、`testPackLineNamesTheBeautyPackInSettingsOrder`（含位置緊接「語意校正」），都經過檢查整段形狀的 `packLines`（一個小標題、前後各一條分隔線、詞包行不能點也不打勾；沒有詞包時選單裡沒有分隔線）。不合法的排列偏好（不存在、空字串、大小寫不同、`zhuyin`、完整模式 ID）一律當標準。

## 8. 候選窗

候選窗是自建的：IMK 的 `IMKCandidates` 用自己的字級畫整格文字、字型被忽略（名稱無法比標點小）、公開 API 沒有展開成網格，私有的 `IMKCandidate` 在實機上只畫出空框。改用 `NSGlassEffectView`（macOS 26 起公開），在一個不搶焦點的 `NSPanel` 裡畫 Liquid Glass。

### 8.1 視窗與資料流

- `CandidatePanelAdapter`（App target）實作 `ShanjieKit` 的 `CandidatePanel` 協定；`Shell` 只持有協定，測試用 `FakePanel`。它只顯示、不收按鍵：按鍵一律由 `handle` 送進核心。
- `NSPanel`：樣式 `[.borderless, .nonactivatingPanel]`、`isOpaque = false`、背景透明、有陰影、`level = popUpMenu + 1`、`canBecomeKey`／`canBecomeMain` 都是 false、`hidesOnDeactivate = false`（輸入法是 agent app，從不是 active app）、`isReleasedWhenClosed = false`。內容是 `NSGlassEffectView`，其下**一個翻轉座標的內容 view 在整個生命週期不換**；換內容、改視窗大小或重排視窗順序會讓玻璃的光暈閃爍，所以選取移動只更新既有的格子、捲動只換進出的那一排。
- 深淺色跟著 client 的外觀（§2.3），沒有就跟系統；強調色用 `NSColor.controlAccentColor`。
- `CandidatePanel.show(candidates, notes, selected, columns, first, total, vertical, lineRect, appearance, glassTint)`：`notes[i]` 是候選旁的小字名稱（目前只有標點有）；`columns` 為 0 是收合的一列、大於 0 是展開網格的欄數；`first`、`total` 是 `candidates[0]` 在整份清單的位置與清單長度（捲動指示用）；`vertical` 是核心輸出的 `candidate_vertical`（0 橫排、1 直排候選窗、2 直排預測列）；`glassTint` 每次顯示都套用，0 也套用。面板只畫被告知的東西，不讀任何設定。
- 輸出到面板的流程：`Session.apply` → `Shell.showCandidates`（記下 `shownVertical`、`candidateSelected`，名稱由標點名稱表查，只有完整等於一個標點才有名稱）→ `panel.show`。`hide()` 時一併結束進行中的動畫並清空內容。
- **滑鼠點選**：格子回報它在最後一次輸出裡的位置 `i` → `Shell` 的 `panel.onSelect` → `Session.candidateSelected(at:)`：只在這個 session 是擁有者、引擎存在、畫面上不是英文段提示時動作；先取樣選字記憶的閘門（和按鍵一樣），再呼叫 `shanjie_engine_pick(i)`，核心走和 Enter、數字鍵同一個 `choose()`（學習與固定詞的語意完全相同）並檢查範圍；回傳碼 2（候選沒開或超出）只記碼、不改狀態，其他非 0 走 §2.2 的失敗路徑。預測列也能點（不論有沒有進入）。點選不得直接 `insertText`。
- 不變的行為：候選窗不收按鍵；擁有者、`deactivate`、安全輸入的規則照 §2.2。

### 8.2 橫排候選列的版面

數值取自蘋果注音同比例截圖（1x 螢幕，1 px = 1 pt）的量測；沒有量到的是第一版估值，名稱註明。

| 項目 | 值 |
|---|---|
| 候選列高度 `barHeight` | 28 pt；圓角 = 高的一半。蘋果的列量到 30 pt（上緣到下緣）；view 高 28 時我們量到 30（view 外上下各多約 1 pt，原因沒量），30 時量到 32 |
| 反白膠囊高 `capsuleHeight` | 24 pt，圓角 = 高的一半，在列中垂直置中；蘋果單字格寬 37 pt、標點格寬 84 pt |
| 膠囊左緣距列左緣 `barInset` | 3 pt |
| 格距 `cellSpacing` | 2 pt（蘋果單字格每格 41 pt 對 37 pt 的膠囊；我們的格子量到比蘋果寬 2 pt，所以扣 2） |
| 格內：號碼左緣 `numberLeading` | 4 pt（蘋果量到號碼墨跡距膠囊左緣 5 pt） |
| 號碼 → 候選字 | 6 pt |
| 候選字 → 名稱 | 6 pt |
| 最後一個字串 → 膠囊右緣 `trailing` | 7 pt |
| 字級 | 候選 16 pt、號碼 9 pt、名稱 12 pt，都是系統字型。漢字墨跡約 0.88 em，所以 16 pt 得到蘋果的 14 pt 墨跡；名稱 11 pt 時「全形逗號」只有 42 pt 墨跡（蘋果 46），12 pt 才對 |
| 顏色 | 號碼與名稱 `secondaryLabelColor`，候選 `labelColor`；選取的格子是強調色膠囊、裡面的字全為白色 |
| 展開記號 | 列尾一條 1 pt 寬、18 pt 高的 `separatorColor` 細線（距記號區左緣 2 pt）加 SF Symbol `chevron.down`（11 pt、medium、`tertiaryLabelColor`，距記號區左緣 3 pt）；記號區寬 `chevronArea` 28 pt。只是顯示，點它不會展開（展開靠 ↓）。英文段提示和網格沒有這個記號 |

- 格子（`CandidateCell`，`ShanjieKit/CandidateCells.swift`）一格一個 view，號碼、候選、名稱用 `NSAttributedString` 量寬並在 `draw(_:)` 自己畫；沒有名稱的格子不留名稱的空間。量出的寬度向上進位到裝置像素，用**所有螢幕中最高的**縮放倍率（AppKit 的文字欄位也是這樣進位）。
- 數字顯示：橫排的格子全部顯示號碼 1–9；網格只在選取所在那一排顯示（其他排保留號碼的空間但不畫，所以上下對齊）；預測列未進入時沒有選取，第一格的號碼位置畫 Tab 鍵符號 `⇥`（U+21E5），表示 Tab 進入預測列，進入後它變成「1」。橫排第一格的號碼至少留「1」與 `⇥` 兩者較寬的空間，所以進入與離開預測列時候選字不會移動。
- 無障礙：每格是 `button` 角色，label 是候選本身、有名稱時加上名稱，選取的格子設為已選取。VoiceOver 實際念得對不對沒有量過。
- 英文段提示（一行小字「空白：英文斷開」）用同一個面板、預測列的方向（橫排 0、直排 2），沒有號碼的空間、沒有展開記號、沒有反白；點它不選任何東西。見 `docs/spec/english-mixed.md`。

### 8.3 位置

`PanelPlacement.topLeft(line:last:size:sideHeight:alignOffset:screens:main:)` 是純函式（座標為 AppKit 的左下原點）：

- 左上角放在行矩形下方 `gap` = 6 pt（和蘋果同比例對照：我們的列在同一個組字行下比蘋果高 2 pt，6 才對齊；上方放置用同一個值，蘋果在那裡的值沒量）；水平往左偏 `alignOffset`，讓第一個候選字和組字區的字左緣對齊（橫排 `alignOffset` = `barInset` + 第一格的 `candidateMinX`；直排 = `sideInset` + 第一格的 `candidateMinX`）。
- 下方放不下（`top - sideHeight < visibleFrame.minY`）就改放到行的上方；右邊超出就往左移，左邊超出就貼齊左緣；上緣夾回 `visibleFrame`。
- 用哪個螢幕：frame 包含行矩形原點的那個，找不到用主螢幕。上下的判斷用 `sideHeight`（沒給就是面板自己的高度）；高度隨輸出變的面板（直排預測列）傳它最高的高度，才會停在行的同一側，左上角仍依實際高度算。
- 行矩形是 `nil`：用上一次收到的行矩形，依這一次的面板大小重算（記的是行位置、不是面板左上角，因為左上角隨面板大小而變）；記住的行矩形原點不在任何螢幕的 frame 裡（螢幕拔掉了）時這次不用它（記住的值保留）；兩者都沒有時放在主螢幕 `visibleFrame` 的左下角，不記住。
- 第一個候選是多字詞（例如游標在句尾時的「你好」）時，對齊的是游標前一個字，所以詞的第一個字落在組字區最後一個字底下。

驗證：`PanelPlacementTests`（`testBelowTheLineAlignedToTheText`、`testNoRoomBelowGoesAbove`、`testRightEdgeMovesLeft`、`testLeftEdgeSticks`、`testUsesTheScreenThatHoldsTheLine`、`testNoLineUsesTheLastLine`、`testLastLineOnNoScreenIsDropped`、`testNoLineAndNoLastGoesBottomLeftOfMain`）。

### 8.4 展開成網格

核心在候選開著時按 ↓ 展開（`candidate_columns` 一律 9，一排就是收合時的一頁；展開時收合的那一頁固定在最上面；可見 5 排，更多的捲動；往上捲到第 0 排再按 ↑ 才收回）；按鍵規則見 `docs/spec/keys-and-composition.md`。面板的處理：

- **欄寬**（`GridLayout.layout`，純函式）：每個格子保有自己的內容寬度；一欄的寬度是它在可見各排裡最寬的格子，展開當下算出，之後**只增不減**（收回、關閉或隱藏後重算），所以捲動不會讓整片左右跳。格子 x 與網格總寬只能來自這個函式。總寬 = 各欄寬 + 欄距（`cellSpacing`）+ 左內距 `barInset` + 右邊 `barInset` + 捲動軌道 `scrollGutter`（9 pt）。第一排和收合時同一頁、同位置，除非下面某一排在某一欄的格子比第一排寬，那一欄才變寬，第一排在它右邊的格子往右移。
- 網格幾何：列距 `gridRowPitch` 28 pt、上下內距 `gridInset` 5 pt、圓角 16 pt；高 = 5 + 排數 × 28 + 5。這些是第一版估值，沒有對蘋果截圖量過。
- 捲動指示：右緣的 5 pt 寬圓角細條（`scrollThumbWidth`），最短 12 pt，色 `tertiaryLabelColor`，位置與長度依可見排數佔全部排數的比例；`total` 大於可見候選數才有。
- **動畫**（`NSAnimationContext` 改視窗框與格子原點，系統預設時間曲線，不自訂彈性）：收合 → 展開 0.3 秒（第一排的格子從候選列的位置滑到網格欄位，視窗同時往下長出其餘各排）；展開 → 收回（第一排按 ↑）0.25 秒（視窗往上縮，回到候選列的字從網格位置飛回，其餘的字留在原位、被縮小的視窗裁掉，動畫結束才移除，期間不收滑鼠）；網格開著時某一欄變寬 0.2 秒；關閉（Esc）沒有動畫。動畫跑著時又來一次 `show` 或 `hide`，用時間 0 的動畫群組把視窗與每個格子放到最終位置來取代進行中的動畫（這個取代的行為是依 AppKit 的 `animator()` 語意寫的，沒有用錄影驗證）；動畫跑著而只有選取移動時，不打斷它。
- 只有選取移動、其他都不變（候選、名稱、`first`、`total`、欄數）時，只更新既有格子的選取與號碼，不重建視窗。

### 8.5 格子的重用

`CandidateCells`（`ShanjieKit`）決定下一次輸出時哪些舊格子留下、哪些新建、哪些移除，並照決定套用；面板只把回報的 view 加上或移除並放到位置，自己不建格子、不記點選位置：

- 同一模式（欄數相同、直排規則相同、有無號碼相同）下，新輸出裡「整份清單的位置、文字、名稱、號碼」都相同的格子留下並更新選取、號碼是否顯示與位置；所以網格捲動一排時，留在畫面上的格子重用，只新建進來的那一排、移除離開的那一排（可見 5 排共 45 格時留下 36 格）。
- 直排（`renumber`）改成「候選與名稱相同就留下」，並換上新號碼（一次捲動一列時九格留八格）；直排的號碼佔固定寬度 `numberSlot`（1–9 與 `⇥` 在號碼字型下最寬者），否則系統字型的數字是比例寬度（9 pt 下「1」4.34 pt、「8」5.92 pt），候選字會隨列移動。
- 模式不同的格子不互相重用；格子點選回報的是「最新輸出裡的位置」。

這種畫法的原因（量測）：離屏建 45 個格子、每格三個 `NSTextField` 並 `sizeToFit`，200 輪的 p50 約 10–15 ms、p95 約 18–84 ms（系統負載 17–20），網格每按一鍵都重建會超出每鍵多 2 ms 的預算；改成每格一個 view 自己畫字後 p50 2.2 ms、p95 4.3 ms。

驗證：`CandidateCellTests`（`testScrollingOneRowReusesThirtySixCells`、`testSelectionMoveInsideVisibleRowsReusesEveryCell`、`testChangingModeRebuildsEverything`、`testClickAfterScrollReportsPositionInTheNewOutputAndOneSelection`、`testIgnoredMouseTakesNoClick`、`testPassiveRowShowsTheTabGlyphOnTheFirstCell`、`testNumberSlotCoversTheTabGlyph`、`testEnteringTheRowReusesTheFirstCell`、`testCellWidths`、`testWidthsAndInkMatchTheOldCells`）；`GridLayoutTests`（第一排最寬時第一排位置不變、下面一排較寬時那一欄變寬且右邊往右移、內容變窄時欄寬不變、只增不減、短的第一排不為空欄留位）；`DecorSetTests`；`ShellTests` 的網格與點選案例（`testExpandedGridReachesThePanelAndAClickPicksTheCell`、`testClickAfterScrollingCountsFromFirst`、`testClickByNonOwnerOrOutOfPageDoesNothing`、`testMouseClickPicksThroughTheCoreAndNeverInsertsDirectly`）。效能量測 `CandidateCellTests.testBenchmark` 只在 `SHANJIE_BENCH=1 swift test -c release --package-path macos --filter testBenchmark` 時執行。

### 8.6 直排

設定「候選窗方向」選直排時，候選窗與即時預測列都畫成一欄的直排；預設橫排。

#### 8.6.1 設定與方向的決定

- 偏好：`CandidateOrientationStore`（`ShanjieKit`，記憶體版供測試），UserDefaults 鍵 `candidateVertical`（Bool，沒設過是 false＝橫排）；`Shell` 持有並快取。
- 設定視窗「外觀」頁的分段控制「橫排／直排」，放在玻璃深淺之前；選單不加。核心 setter `shanjie_engine_set_candidate_vertical(engine, enabled, out)`（0 或 1，其他值回 2；回傳快照）在**每次建引擎**與設定改變時呼叫。
- **候選窗的方向在開窗時固定**：setter 只影響下一次開啟的候選窗（含標點候選），開著的窗（含展開的網格）照它開啟時的方向繼續，setter 回傳的快照和呼叫前相同。預測列沒有「開窗」的時間點（每按一鍵重算），每個輸出都照引擎當下的方向，正在顯示的預測列會換成新方向；但設定只能在設定視窗改，而點設定視窗會讓輸入法程序到前景、原本的 App 失去焦點、組字被送出、面板收起（這一段是推論，沒量過），所以這條「換方向」路徑只有 API 與測試走得到。
- 輸出帶方向標記：`ShanjieOutput` 尾端的 `candidate_vertical`：0 橫排（或沒有候選）、1 直排候選窗、2 直排預測列（進入或未進入）。`CoreOutput.vertical` 與 `CandidatePanel.show` 都帶這個 0／1／2 的值；殼的 Page 鍵只看值是 1。

#### 8.6.2 核心的按鍵規則

直排候選窗的狀態是「整份清單、可見範圍的第一個 `first`、選取 `sel`」，可見範圍固定 9 個（不足就是全部）；輸出的 `candidates` 是可見範圍、`candidate_selected` 是在可見範圍裡的位置、`candidate_columns` 一律 0。開啟時 `first` 從 0 開始、選取 0。

- 1–9：選可見範圍的第 n 個（超出則忽略），關閉候選。
- ↓、→、空白鍵：下一個；選取在可見範圍最後一個時，可見範圍往下捲一個；已是整份清單最後一個時不動。
- ↑、←：上一個；選取在可見範圍第一個時，可見範圍往上捲一個；已在最前時不動。
- Page Down：可見範圍往後移 9 個（不超過讓最後一個候選出現在第九列的位置），選取到新的第一個，**但選取不往回**（被夾住時新的第一個可能在原選取之前，這時選取不動）；已在最後時不動。Page Up：往前移 9 個（最小 0），選取到第一個；已在最前時不動。
- Enter、Esc、Backspace、⌘⌫、其他鍵：和橫排相同。
- 蘋果直排實測（§8.6.5）空白鍵也是「下一個」，不是翻頁。選取改變時蘋果的組字區即時換成選取的字，善解不這樣做（橫排直排都一樣）。


#### 8.6.3 面板版面

幾何在 `VerticalLayout`（`ShanjieKit`，純函式，座標是翻轉的內容 view，y 向下）：

| 項目 | 值 | 出處 |
|---|---|---|
| 可見列數 `visibleRows` | 9 | 核心的 `PAGE_SIZE`；蘋果可見 9 列、第 10 列露出上半 |
| 列距 `rowPitch` | 28 pt | 蘋果的選取框下緣依序在 109、137、165、193 |
| 最小寬度 `minWidth` | 226 pt | 蘋果選取膠囊約 226 pt 寬；這裡用在面板寬度，所以膠囊比蘋果的窄幾個 pt（差距沒量） |
| 上下內距 `inset` | 5 pt | 橫排網格的值，估值 |
| 左右內距 `sideInset` | 3 pt | 橫排列的 `barInset`，估值 |
| 玻璃圓角 | 16 pt | 橫排網格的值，估值 |
| 捲動軌道 `scrollGutter`／細條寬 `thumbWidth`／最短 `thumbMinHeight` | 9／5／12 pt | 橫排網格的值，估值 |
| 列分隔線 | 1 個裝置像素（傳入 1 / backingScaleFactor）、顏色 `separatorColor`；落在兩列之間，y = 該列膠囊上緣 + 25，左右各縮 6 pt（量到 x 17…231 對膠囊 11…237）；第一列上方與最後一列下方沒有；碰到選取列的上下兩條隱藏，沒有選取（未進入的預測列）時全部顯示 | 蘋果 1x 暗色量測；2x 的粗細是推論，亮色沒量 |

- 高度 = `inset × 2 + 列數 × rowPitch`。寬度 = max(`minWidth`、目前寬度、最寬格子的內容寬度 + 兩側內距 + 軌道)，進位成整數 pt；目前寬度只增不減。
- 每個格子是一整列寬的膠囊（x = `sideInset`、寬 = 面板寬 − 兩側內距 − 軌道、高 24 pt），左邊號碼、右邊字（含名稱註記）；選取是整列寬的強調色膠囊；滑鼠點一列等於選那一個。
- **尺寸與重設**（`VerticalLayout.plan`，輸入前一次的計畫、這次的值 0／1／2、個數、總數、各格內容寬度）：
  - 值改變（0↔1↔2 任兩者，含 1→2、2→1）一律先重設內容，不沿用前一個計畫的列數與寬度（開在寬的預測列上的候選窗從最小寬度開始）。
  - 值 1（候選窗）：高度在開窗時固定為 min(個數, 9) 列，寬度只放寬；只有候選窗在清單比可見列數長時才預留軌道（寬度不隨它變），並畫捲動細條。
  - 值 2（預測列）：每次輸出的高度依個數（1–9 列），連續輸出都是 2 時寬度只放寬；沒有捲動。
  - 值 0 由橫排版面自己算。
- 位置：照 §8.3，只是窗的大小換成直排面板的大小；預測列的 `sideHeight` 是九列的高度（`VerticalLayout.sideHeight(kind:)`）。
- 不做動畫：直排從不展開或收回，換寬很少見（較寬的名稱捲進來時才有）；一律一次 `setFrame`，以左上角為準，高度改變時不分兩步。
- 號碼：候選窗依可見列重排成 1–9（連按 ↓ 捲一列後，第一列的號碼是 1、字是原本第二個）；預測列未進入時第一列的號碼位置是 `⇥`，其他列沒有號碼，進入後才有編號 1–9 與選取膠囊。
- 不做：「常用」標籤、底下的「詞頻／部首／表情／拆字」分頁、字級設定。

驗證：`VerticalLayoutTests`（量測值斷言為數字、列 y、高度、號碼隨可見列、寬度規則、`plan` 各種前後值組合、分隔線、細條、位置與預測列停在行的同一側）；`VerticalShellTests`（`testNeverSetIsHorizontal`、`testBuildPathSendsTheStoredOrientationToEveryEngine`、`testSettingChangeIsStoredSentToTheCoreAndAppliesToTheNextWindow`、`testStoredChoiceSurvivesARestart`、`testPanelReceivesTheCoreVerticalFlag`、`testPredictionRowFollowsTheSettingAndTheWindowOverItIsOne`、`testSettingChangeReRendersAShownPredictionRow`、`testSettingChangeAppliesNothingWhenNoPredictionRowTurns`、`testPageKeysTurnPagesInsideTheVerticalWindow`、`testDigitPicksTheVisibleRowAfterAPageTurn`、`testPageKeysAreKeysOutsideTheTablesOnceTheWindowIsClosed`、`testPageKeysOutsideTheVerticalWindowTakeTheOffTablePath`）；核心側 `core/tests/engine_vertical.rs`。

#### 8.6.4 即時預測列的直排

預測列在直排設定下畫成一欄、列數＝預測個數（1–9），沒有捲動，輸出值 2（`candidate_selected` 未進入時 -1、`candidate_columns` 0、`candidate_first` 0、`candidate_total` ＝個數）。未進入時和橫排一樣沒有選取，第一列的號碼位置是 `⇥`，其他列沒有號碼；進入後有編號 1–9 與選取膠囊。方向跟著引擎當下的設定（§8.6.1）；尺寸與重設的規則是 §8.6.3 的 `VerticalLayout.plan`（值 2：每次輸出的高度依個數，連續輸出都是 2 時寬度只增不減；與值 1 之間互換一律重設）。

按鍵只改進入後的直排預測列（橫排與未進入的預測列完全不變）：↓ 下一個、↑ 上一個，兩端停住（Shift+Tab 仍是退回未進入）；←→、Tab、1–9、Enter、Esc、⌘⌫、其他鍵照橫排。未進入的直排預測列：↓ 開啟候選（候選窗以直排開），↑ 忽略。Page Up／Down 不轉送（殼只轉送值為 1 的候選窗），走表外鍵路徑。

預測列會不會閃爍、貼近螢幕下緣時是否停在行的同一側，只能實機看；位置用九列高度決定上下是為了避免隨個數翻邊。

#### 8.6.5 蘋果直排的量測依據

直排的外觀與按鍵取自蘋果注音直排候選窗的實測（蘋果說明寫直排時空白鍵翻頁，實測不是）：一欄、9 列可見、左邊編號 1–9、右邊字、最上面的候選右邊有灰字「常用」；選取是整列寬的膠囊、右側有捲軸；最下面有四個分頁。↓ 與 → 都是下一個，↑ 與 ← 都是上一個，空白鍵也是下一個；選取在第九列再按 ↓，清單往下捲一列、選取留在第九列、編號重排；Page Down 往後 9 個、選取到第一列，Page Up 反向。沒量到的：整份清單最後一個再按 ↓、第一頁按 Page Up、最後一頁按 Page Down、數字鍵 1–9 與 Enter、Esc 以外的鍵，這些照橫排一致的做法。

### 8.7 玻璃深淺

- 滑桿值 0…1 → 疊在玻璃上的色調：深色外觀疊黑、淺色外觀疊白，不透明度 = 值 × `GLASS_TINT_MAX`（0.5）；值 0（含 NaN、負數夾成 0）不設色調（`nil`），這時外觀和預設逐位元相同。材質不變，只調玻璃的色調。0.5 是看實機挑的口味值，沒有對蘋果截圖量過。
- `GlassTint.tint(value:dark:)` 回 `Tint?`；`GlassTint.Applier` 記住上一次的色調，只在變了的時候交出 `.set(Tint)` 或 `.clear`（從大於 0 改回 0 要清除，把 `NSGlassEffectView.tintColor` 設成 nil），沒變就不賦值（避免玻璃閃爍），所以從沒上過色的面板在 0 時完全不動。候選窗、英文段提示窗、設定視窗的範例列都用同一個 `GlassTint.isDark`。
- 儲存：`GlassTintStore`，UserDefaults 鍵 `glassTint`（Double，沒設過是 0）；`Shell` 在 init 快取，`setGlassTint` 存、通知面板立即更新正在顯示的窗（`panel.setGlassTint`）、發 `didChangeSettings`。改值後下一次出現的候選窗用新的值；拖滑桿時候選窗通常看不到（點設定視窗會讓原 App 失去焦點，推論）。
- 預覽：設定視窗滑桿下方有一列範例候選列（「1 善　2 解　3 輸入法」，第一個被選取），是 `SampleCandidateBar`（`NSGlassEffectView`），用和真的候選窗相同的 `CandidateCells`、`Metrics` 的列高與間距與圓角、`GlassTint.Applier`；差別是沒有展開記號。外觀跟著它自己的 effective appearance；玻璃深淺本身的視覺要實機看。

驗證：`SettingsTests`（`testGlassTintDefaultsToZeroAndIsHandedToEveryShow`、`testTintConversion`、`testIsDarkFollowsTheAppearance`、`testSliderReachesAVisiblePanelAtOnce`、`testApplierSetsClearsAndSkipsUnchanged`、`testGlassTintIsCachedFromTheStoreAtStart`）。

## 9. 設定視窗

### 9.1 視窗

在輸入法自己的程序裡開一個 `NSWindow`（`SettingsWindowController`），內容是 SwiftUI。標題「善解設定」。從輸入選單的「善解設定…」開（`MenuEntry.Action.openSettings`）；SwiftUI 的 `Settings` scene 從 macOS 14 起無法從輸入選單打開，第三方輸入法也沒有公開的方法把設定放進「系統設定」，所以走自己的視窗。

- 同時只開一個（`isReleasedWhenClosed = false`，再點選單就把已開的帶到前面）；`SettingsModel` 在第一次開窗時才建。
- 樣式 `[.titled, .closable, .fullSizeContentView]`，`titlebarAppearsTransparent`，`isOpaque = false`、背景透明；層級 `.floating`。
- **背景**：`NSVisualEffectView`（材質 `.popover`、`.behindWindow`、`state .active`）放在內容**後面**，填滿整個視窗並延伸到標題列底下；SwiftUI 的內容從標題列下面開始（上緣約束取 `NSWindow.frameRect(forContentRect:styleMask:)` 算出的標題列高，一般 32 pt；`host.safeAreaRegions = []`），`Form` 與左欄的 `List` 都 `.scrollContentBackground(.hidden)` 讓玻璃透出，兩欄質感相同。玻璃不包住內容，否則內容會套 vibrancy、文字變淡。材質 `.popover` 是看系統清單挑的，沒有量測；不用 Liquid Glass 是因為在設定視窗太透。
- **帶到前面**：開窗時記下前景 App（不是自己才記），`NSApp.activate()`、`makeKeyAndOrderFront`，再 `orderFrontRegardless()`（`activate()` 從 macOS 14 起只是請求，實測被拒絕時視窗會留在前景 App 後面）；視窗因此一定在最前面，但不一定是 key window，點一下才是。不改成 `.regular`（會在 Dock 多一個圖示）。關窗時，只在輸入法仍是 active app 的情況下把焦點還給記下的 App（否則使用者已經去別處，不搶焦點）。`close()` 先丟掉記下的 App，讓更新程式要往前時不會先喚醒別的 App。
- 視窗裡**沒有文字欄位**：只有選擇器、開關、滑桿與按鈕，所以不需要裝 Edit 選單，輸入法也不會在自己的視窗裡處理打字。
- 安全輸入期間輸入選單反灰（系統行為），開不了設定視窗。

### 9.2 版面與尺寸

- **左側清單**：左欄是固定寬度 `sidebarWidth` = 160 pt 的 `List(selection:)`（`.sidebar` 樣式），每項是系統的 `Label`（文字加 SF Symbol）；右邊是選到那一頁的 `.grouped` `Form`，寬度 `formWidth` = 420 pt；視窗寬 580 pt。不用 `NavigationSplitView`：它的左欄可以收合，而這個視窗沒有工具列或「顯示方式」選單能把它叫回來。選取不會變成空的（點左欄空白處或 ⌘點不取消選取），換頁時右邊從最上面開始（`.id(page)`）。
- 六頁依序：一般（`gearshape`）、選字（`character.cursor.ibeam`）、外觀（`paintpalette`）、選字記憶（`brain`）、診斷（`stethoscope`）、關於（`info.circle`）。預設選「一般」；同一次執行裡再開，停在上次的頁（不存到 UserDefaults）。`show(page:)` 可指定頁（英文預設值說明窗的「到設定看」開「選字」頁）。
- **固定大小**：視窗的 frame 高度（內容加標題列）＝ min(`SettingsLayout.preferredHeight`, 螢幕 `visibleFrame` 的高度)，`preferredHeight` 是具名常數 620 pt，不隨內容長高，切換頁時大小不變；內容比視窗高的頁在視窗裡捲動（`Form` 不加 fixedSize；hosting view 不限制內容尺寸，`sizingOptions = []`）。620 是起點，不是對照某個參考量出來的；一台內建螢幕可見高度 1074.5 pt、外接 1409 pt 都留有上下空間。可見高度不到 620 的螢幕上視窗縮成可見高度，不超出上下緣。計算是 `SettingsLayout.frameHeight(visibleHeight:)`（純函式，`ShanjieKit`）。不再量內容高度（沒有隱藏的 `NSHostingView` 量測，也不因「個別設定的 App」清單列數改變而重算）。
- **位置**：`show()` 設好大小之後置中在滑鼠所在螢幕的 `visibleFrame`（輸入法程序沒有 key window，`NSScreen.main` 不可用；使用者剛在滑鼠所在的螢幕點選單）。螢幕參數改變（解析度、Dock、文字大小、拔掉螢幕）時重算高度，並以 `settingsFrame(_:clampedInto:)` 把 frame 夾回視窗所在螢幕的 `visibleFrame`（超出下緣往上移、超出上緣往下移；高度已被夾住，所以兩者不會同時發生）。

驗證：`SettingsTests.testSettingsHeightIsThePreferredOneClampedIntoTheScreen`（可見高度 `preferredHeight + 280`、`preferredHeight - 120`、剛好 `preferredHeight`；數值用常數表示，調常數不必改測試）、`testFrameIsClampedIntoTheUsableArea`。視窗是否整個在螢幕內、上緣在選單列下、下緣在 Dock 上，以及更大的系統文字下仍可捲到最後一項，要實機看。

### 9.3 頁面內容與 view model

每一項設定和輸入選單改的是同一份設定（同一個 store 與 `Shell` setter），任一邊改了另一邊馬上看到。每個控制項都是系統元件。

| 頁 | 區塊與項目 |
|---|---|
| 一般 | 「鍵盤」：鍵盤排列（標準／倚天）。「聊天／書面」：切換方式（依 App 自動／一律聊天／一律書面）、灰色說明、「個別設定的 App」清單 |
| 選字 | 「選字」：即時預測、在瀏覽器裡第一個字不顯示預測（預測關時反灰）、可省略韻母（`showsAbbreviation` 為 false 時不顯示）、中英混打（實驗）與其說明、快打時鍵序顛倒不當英文、依按鍵順序判斷英文、重打同一個位置算英文（各附說明；三個選項在中英混打關閉或倚天排列時都反灰，「快打時鍵序顛倒不當英文」另在「依按鍵順序判斷英文」關閉時反灰，設定值仍顯示）、單按 Shift 切換中英、中英文之間自動加空格、避免把敏感字詞排在前面、語意校正與「強度」（保守／標準／積極的分段控制，隨語意校正與載入的模型反灰）。「詞包」：動漫與遊戲詞（下一行灰字「資料更新至 YYYY-MM-DD（維基百科）」）、經典台詞、資訊與 AI 詞、美妝與保養詞、迷因與實況用語；所有詞包的開關只在這裡。「模糊輸入」：四個音對（ㄣ／ㄥ、ㄓ／ㄗ、ㄔ／ㄘ、ㄕ／ㄙ）與「鄰鍵打錯」（倚天排列時反灰，值保留）及一段說明 |
| 外觀 | 候選窗方向（橫排／直排的分段控制）、候選窗玻璃深淺（滑桿，兩端是裝飾用的圖示 `rectangle.on.rectangle`（中空）與 `rectangle.fill.on.rectangle.fill`（實心），放在滑桿兩旁的 `HStack`，VoiceOver 不讀，滑桿本身帶標籤；放在 `Slider` 自己的兩端標籤時兩個在分組的 `Form` 裡都變實心）、範例候選列預覽 |
| 選字記憶 | 狀態列（學習已暫停（安全輸入）、選字記憶無法存檔）、不要備份選字記憶、清除選字記憶…（走和選單相同的確認視窗） |
| 診斷 | 「記錄偶發問題的診斷資料」開關與說明、「複製診斷紀錄…」（見 §13.3） |
| 關於 | 見 §10 與 `docs/spec/install-and-update.md` |

- 開關類項目的文字、灰字說明與預設值各屬於所講功能的規格（`docs/spec/english-mixed.md`、`docs/spec/prediction.md`、`docs/spec/semantic-rerank.md`、`docs/spec/lexicon-and-packs.md`、`docs/spec/keys-and-composition.md`）；這裡只定它們放在哪一頁。
- **「個別設定的 App」清單**：每列是 App 圖示（16 pt）、名稱、右邊「聊天」或「書面」、一個移除按鈕（圖示按鈕，VoiceOver 讀「移除」）。名稱與圖示用 `NSWorkspace.urlForApplication(withBundleIdentifier:)` 找，找不到（App 已刪除）時名稱顯示 bundle ID、用一般 App 圖示；畫面依名稱排序（`localizedStandardCompare`，同名再依 bundle ID），`SettingsModel` 依 bundle ID 排序（供測試固定順序，也不碰 `NSWorkspace`）。清單是空的時顯示灰字「在 App 裡打開善解選單，勾選「這個 App 用聊天設定」，就會列在這裡。」切換方式不是「依 App 自動」時清單照常顯示、聊天／書面文字轉淡（設定保留，切回自動時生效），**移除按鈕仍可按**。名稱與圖示依 bundle ID 快取（`AppLabels`），只在清單內容改變時查一次，不在每次重繪時查（拖玻璃滑桿也會重繪）。
- 狀態列只有「學習已暫停（安全輸入）」與「選字記憶無法存檔」；「學習已暫停（此 App）」取自某一個輸入 session 的 client，設定視窗不屬於任何 session，所以只留在選單。

**view model 與同步**：`SettingsModel`（`ShanjieKit/Settings.swift`，`ObservableObject`）是 view model：每個控制項都經過和選單相同的 `Shell` setter；SwiftUI 畫面只綁定它。`Shell` 在任何設定改變時發程序內通知 `didChangeSettings`，model 收到就重讀 `Shell` 已快取的設定值（`refreshSettings`，每次通知都跑，拖滑桿也是，所以不讀外部狀態）。外部狀態（安全輸入、備份旗標、核心的寫入狀態）沒有通知，只在開窗與視窗成為 key window 時讀（`refreshExternal`）；視窗自己改備份旗標或清除之後也立刻重讀。

驗證（`SettingsTests`）：每個控制項改的是和選單同一個 store 與 setter（`testPredictionFromTheWindowReachesStoreAndMenu`、`testAbbreviationFromTheWindowReachesStoreAndMenuAndFollowsPrediction`、`testEnglishFromTheWindowReachesStoreAndMenu`、`testEnglishIsGreyedInTheEtenLayout`、英文三個選項的案例、`testRerankFromTheWindowReachesMenuAndFollowsDemote`、`testDemoteFromTheWindowReachesStoreAndMenu`、`testLayoutPickerCallsSelectLayout`、詞包案例、`testBackupSwitchAndClear`）、`testStatusRows`、`testExternalStateIsReadOnRequest`；選單改了 model 跟著變。畫面本身（在 App target，沒有測試）只能實機看。

## 10. 關於頁的模型列表

設定「關於」頁的「模型」一節列出這一版內附的每個模型，每列顯示中文名與對外名稱（`LabeledContent(中文名, value: 對外名稱)`）。資料來自 bundle 的 `Resources/models.tsv`（格式、建置檢查與雙向核對見 `docs/spec/models.md`），目前七列，依序是通語、辨類、承接、連文（書面）、連文（聊天）、會意、常談。

- `ModelList.parse(_:)`（`ShanjieKit`）：列的順序照檔案；檔案必須以換行結尾、沒有 CR、每行剛好四個非空欄位，任何一項不符整份視為不可讀，回 `[]`，這時整個「模型」一節不顯示。它不重複 shell 檢查的 SHA-256 與重複檔名規則。`ModelList.load(from:)` 在檔案不在或讀不了（開發環境、測試）時回 `[]`。
- 位置：「善解輸入法」（版本，`0.0.0` 顯示「開發版」）之後、「更新」之前；沒有說明文字，維持關於頁的簡潔。其餘各節（更新、網站連結、授權說明）見 `docs/spec/install-and-update.md`。

驗證：`ModelListTests`（`testRowsInFileOrder`、`testEmptyFileHasNoRows`、`testRowWithTheWrongFieldCountMakesTheFileUnreadable`、`testMissingFileHasNoRows`、`testShapesTheBuildRefusesAreUnreadable`、`testTheRepoListHasTheSevenModels`）。名稱與 `docs/releases/README.md` 的命名表是否一致由審查核對（建置不檢查名稱）。

## 11. 驗證

- `make test-swift`（`swift test --package-path macos`）：`ShanjieKitTests` 的測試資料由 `Support.swift` 的 `TestData.files` 列出（`data/lexicon/*` 與 `data/lm/*` 的符號連結拼成和 App 的 `Resources` 相同的資料夾）；缺檔就失敗並說明怎麼取得，不跳過（唯一的選用項目是 `testBenchmark`）。測試一律經過真正的 C 核心。
- 殼層測試用 `FakeClient`（模擬文字框，`insertText` 會取代組字）與 `FakePanel`，`Controller` 是 `Session` 加 `FakeClient`，把它設成 `nil` 等於 IMK 釋放 controller。涵蓋：打字送出、候選顯示與數字選字、帶 Caps Lock 的鍵不處理、組字中按表外鍵先送出、`commitComposition`、回傳碼非 0 的路徑（之後核心的組字也是空的）、聊天與書面設定可觀察的差異、組字擁有者三種情形（A 組字中對 B 按鍵、擁有者被釋放後 B 按鍵、B 組字中 A 的 `deactivate` 晚到）、擁有者改變時重設設定、安全輸入時擁有者的 `deactivate` 丟棄組字、非擁有者的 `deactivate` 不碰共用候選窗；拿掉擁有者檢查或把 `shanjie_engine_key` 改成固定回傳非 0 時至少一個測試要失敗。
- `scripts/check-app.sh`（對建出來的 bundle）、`scripts/check-installer.sh`、`make selftest-bundled`：§1.2、§6.3。
- 實機才看得到的：候選窗的玻璃樣子、深淺色、位置與動畫順不順、閃爍、直排預測列貼近螢幕下緣的行為、設定視窗的置中與夾回、VoiceOver。

## 12. 已知限制與設計取捨

- 切換鍵盤排列、詞包要重建引擎，主執行緒阻塞約一秒以上，期間按鍵直通。取捨：排列很少切換；之後可在核心加 `set_layout`。
- 安全輸入只在擁有者的 `deactivate` 檢查，其他送出路徑不檢查：`IsSecureEventInputEnabled()` 是全系統旗標，任何 App 開著時，點別處、切換排列或換擁有者都會悄悄丟掉打到一半的字。
- 選取移動時，組字區不會像蘋果那樣即時換成選取的候選。
- 橫排網格的第一個候選是多字詞時，候選窗對齊的是游標前一個字（§8.3）。
- 從來沒給過行位置的 App（`lineRect` 一直回 `nil`），候選窗放在主螢幕可見範圍的左下角；有行位置的 App 不受影響。
- 直排預測列的「換方向」只有 API 與測試走得到（§8.6.1）；`NSGlassEffectView.tintColor` 在 macOS 26 上的效果、動畫被取代時的行為（§8.4）都沒有用錄影驗證。
- 範例候選列與真的候選窗的玻璃深度（一個在設定視窗的背景上、一個在 App 上）是否一致沒有比較過。
- 不做：直排的「常用」標籤與底下的分頁、字級設定、表情候選、展開網格底部的「詞頻／部首／表情／拆字」分頁。

## 13. 診斷紀錄

偶發問題（例如單按 Shift 偶爾卡在英文）在測試裡重現不了，所以殼在記憶體裡記一份切換與換 App 的紀錄，由使用者自己複製、自己貼給開發者。善解不自動送出，也不新增任何連線。程式在 `ShanjieKit/DiagLog.swift`。

### 13.1 記什麼

兩個環狀緩衝，各自滿了丟掉最舊的：`shift` 最多 200 筆（`DiagLog.shiftLimit`），其他事件最多 800 筆（`otherLimit`）；Shift 很常按（大寫字母），分開才不會把切換與換 App 的紀錄擠掉。每筆是單調時鐘的秒數（`CLOCK_MONOTONIC`，睡眠時照樣計時；`systemUptime` 不算睡眠，會把睡前的事件顯示得比實際近）、事件與下列欄位，沒有其他欄位。

| 事件 | 何時記 | 欄位 |
|---|---|---|
| `shift` | `ShiftTap` 在 Shift **放開**時判斷之後；按下不記；按下到放開之間有 `keyDown`（打大寫字母或符號）的那一次不記；有 Shift 按著（或按鍵的旗標帶 Shift）時打過鍵，就只記 `toggled`，直到兩個 Shift 都放開、或有一次乾淨的按放（放開的那一鍵有按下紀錄、按下之後沒有按鍵；這一次本身照清掉之前的標記判斷，不是 `toggled` 就不記）為止（兩個 Shift 交疊時被打斷的那一側的紀錄會被另一側取代，它的放開不能拿來分辨大寫；乾淨的按放是在最後一個鍵之後才按下的，每次放開都清掉按下紀錄，所以之後的紀錄只看之後的事件；只等兩個 Shift 都放開的話，另一側的位元殘留時會一直少記）；`isSecureInput()` 為真時不記；「單按 Shift 切換中英」關著時不處理 `flagsChanged`，也不記 | 左或右、結果（`ShiftTap.Outcome`） |
| `toggle` | `toggleAscii` 之後 | bundle ID（`toggleAscii` 已讀的那一次）、切換後的中英、提示有沒有畫出來（`showShiftHint` 的回傳，沿用它那一次的位置讀取） |
| `activate`、`deactivate` | `activate` 讀過 bundle 之後；`deactivate` 每一次，不是擁有者的也記 | bundle ID（`activate` 讀的那一次；`deactivate` 不讀，用 session 記住的 `bundle`）、這個 session 是不是擁有者、當下的 `engineAscii` |
| `owner` | `claim()` 回 true、呼叫處讀完 bundle 之後（`activate`、`handle`、`toggleAscii`、`toggleAppChat`） | 新擁有者的 bundle ID（呼叫處讀的那一次） |
| `ascii` | `pushAscii` 真的改了狀態；`applyShiftToggle` 關掉開關、沒有擁有者時直接改 `engineAscii` 的那一支 | bundle ID（session 的 `knownBundle`；沒有擁有者時 `-`）、新狀態 |

`ShiftTap.Outcome`：`toggled`，或不算的原因，每條退出路徑一個值。放開時依序判斷，幾個同時成立時取第一個：(1) 沒看到按下（`noPress`）；(2) 放開的是另一側的鍵（`otherKeyReleased`）；(3) 按下時記下的原因：其他修飾鍵（`modifierAtPress`）先於 Caps Lock（`capsLockAtPress`）；(4) 按下之後另一個修飾鍵動了（`modifierBetween`，只在按下時沒有原因時記）；(5) 另一側 Shift 的狀態和按下時不同（`otherSideChanged`）；(6) Control／Option／Command／Fn 按著（`modifierAtRelease`）；(7) Caps Lock 開著（`capsLockAtRelease`）；(8) 超過 `SHIFT_TAP_MAX`（`tooLong`）；(9) 之間有滑鼠按鍵（`mouse`）。沒看到按下是 `ShiftTap` 被重設，或啟用時 Shift 已按著；兩個 Shift 先後按下時第二次按下取代第一次的紀錄，結果是 `otherKeyReleased` 再 `noPress`。被 `keyDown` 打斷優先於任何原因：打斷了就不記。切換與否和加紀錄之前完全相同（「打過鍵」的標記只決定記不記，不影響切換）。

- **不新增客戶端讀取**：bundle ID 只用呼叫處已經讀到的值或 session 記住的值，提示的位置只讀一次。
- **bundle ID 先清理**（`DiagLog.clean`）：1–255 個 `[A-Za-z0-9._-]` 照記；其他的記 `?`；nil 或空字串記 `-`。這是唯一的字串欄位，其他都是列舉或布林。
- **永遠不記**：打的字、組字、候選、預測列、前文、學習內容、按鍵的字元或鍵碼（Shift 的左右除外）、視窗標題、網址、文件內容。
- **只在記憶體**：不寫檔、不用 `Logger`、`print` 或檔案 I/O，不進學習檔；輸入法程序結束就沒了。

### 13.2 匯出格式

第一行 `shanjie <版本> macOS <x.y.z> exported <匯出時刻，ISO 8601 UTC>`；之後一行一筆、舊的在前，時間寫成匯出前幾秒（`-12.345`），不寫時刻：

```
-12.345 shift left toggled
-12.345 ascii com.apple.TextEdit en
-12.345 toggle com.apple.TextEdit en hint
-3.000 deactivate com.apple.TextEdit owner en
-2.900 owner com.apple.Safari
-2.900 activate com.apple.Safari owner zh
```

每一行都在一個封閉的文法裡：事件名稱、固定的列舉值（`left`／`right`、`Outcome` 的值、`en`／`zh`、`hint`／`nohint`、`owner`／`notowner`）與清理過的 bundle ID。

### 13.3 設定與複製

- 設定視窗的「診斷」頁（§9.2）：開關「記錄偶發問題的診斷資料」，預設開，UserDefaults 鍵 `diagLog`（沒存過算開）；灰字說明「只存在這台 Mac 的記憶體裡，不會自動送出。會記下放開 Shift 時算不算單按（按著 Shift 打字時不記）、切換中英、換 App 的時間與 App 的識別碼（例如 com.apple.TextEdit），不記你打的字。紀錄留到輸入法結束，可能涵蓋好幾天；安全輸入時不記 Shift，切換中英照記。」關掉時兩個緩衝都清空、之後不再記；打開時也清空，從空的開始。在設定視窗以外改這個鍵（`defaults write` 寫容器的偏好檔）時，殼經 KVO 收到 UserDefaults 的變更通知（`DefaultsDiagLogStore`），不看新值、一律清空兩個緩衝（`DiagLog.storeChanged()`；關了又馬上打開，兩次寫入可能都在處理通知之前落地）；通知處理之前，關著就算空的（不匯出、按鈕反灰）。預覽開著時緩衝被清空過（`DiagLog.clears` 和開預覽時不同），按「複製」不寫剪貼簿、直接關掉。收不到通知的情形，舊紀錄會留到下一次清空：系統把「關再開」合併成沒有變更、直接改偏好檔而不經 `cfprefsd`、`defaults write <bundle id>` 寫到容器以外的一般網域（輸入法讀不到）。
- 按鈕「複製診斷紀錄…」：先用 sheet 顯示要複製的全文（唯讀、不能選取的文字：選取後按 ⌘C 會走一般的複製、繞過下面兩項保護；加「取消」「複製」），按「複製」才把**同一份文字**寫進剪貼簿。緩衝是空的時按鈕反灰（緩衝沒有變更通知，空不空在開窗、視窗成為 key window 與切換開關時讀）。
- 寫剪貼簿：`prepareForNewContents(with: .currentHostOnly)`（不經通用剪貼板同步到其他裝置），再用一次 `writeObjects` 寫一個同時帶純文字與 `org.nspasteboard.TransientType`（剪貼簿管理程式的慣例：不記進歷史）的項目，讀剪貼簿的程式不會先看到沒有標記的文字。之後由剪貼簿處理；系統自己的剪貼簿歷史（macOS 26 Spotlight）是否理會這個標記沒有文件，也沒有量過。
- 介面都是系統元件（`Toggle`、`Button`、`sheet`、`NSPasteboard`）。不用存檔面板：那要多一個沙盒權限（`docs/spec/privacy.md` §2），剪貼簿不用。

### 13.4 驗證

`DiagLogTests`：
- `testRecordingAddsNoClientRead`：`activate`、`deactivate`、`commitComposition`、Shift 切換、`toggleAppChat` 與按鍵各一步，在 Shift 切換開與關兩種設定下，bundle ID、文字讀取（`selectedRange`、`markedRange`、`attributedSubstring`）、游標位置與行位置的讀取次數等於加紀錄之前量到的常數。
- 緩衝上限、每種事件的格式、幾個原因同時成立時取第一個、預覽之後的事件不在複製的內容裡、開關（含重建 Shell 仍是關、只改 store 關掉時算空的、經設定或 `storeChanged()` 再打開仍是空的、預覽之後被清空就不複製）、兩個 Shift 交疊或 Shift 早已按著時打字不記 `shift`、另一側位元殘留時乾淨的單按之後照記、一次單按記 `shift` 與 `toggle`（提示畫不出來記 `nohint`）、每一個不算的原因各一個序列、安全輸入時不記 `shift` 但 `toggle` 照記、被按鍵打斷的不記、bundle 清理、剪貼簿的呼叫、預覽與複製逐位元組相同與空緩衝反灰。
- 打字不進紀錄：標記字串在中文組字送出、英文狀態的 `asciiKey` 路徑、`commitComposition` 三條路徑各打一次，匯出不含標記的任何 4 個以上字母的片段、不含注音符號，每一行都符合 §13.2 的文法，打字不新增事件。
- `LogTests.testShellLogsNothingButStaticTextAndCodes`（§6.1）：用標記 bundle ID 啟用、單按 Shift 切換、經假剪貼簿複製，標記不出現在整個程序的日誌與 stdout／stderr，紀錄的事件行不出現在殼的 subsystem。

實機（要真人或虛擬機）：設定頁、預覽 sheet、剪貼簿內容，以及終端機開著「加密的鍵盤輸入」時單按 Shift 只多 `toggle`、沒有 `shift`。
