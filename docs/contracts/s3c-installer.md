# S3c 契約：安裝程式 app（使用者 2026-10-04）

使用者實測 v0.1.0 時，把 `善解輸入法.app` 放進 `/Applications` 點兩下，什麼都沒發生（輸入法不是點開來用的 app），之後才改用 brew 加上一行 `install` 指令，還要登出再登入一次。使用者要求：「下載、點兩下就裝好」，並選擇**另做一個安裝程式 app**（仿小麥注音的 `McBopomofoInstaller`，本機原始碼 `~/side-project/ime-research/repos/McBopomofo/Source/Installer/`，MIT）。

**前提**：PR #7（分支 `fix/install-enable-parent`：`install` 啟用輸入法本體並確認系統接受、選單圖示、←→ 鍵）**已合併進 main**，本片才開始實作（分支先 rebase 到那之後的 main）。

**怎麼算做到**：使用者從 Release 下載 `shanjie-installer-<版本>.zip`，Finder 解壓，點兩下「安裝善解輸入法」，按「安裝」，之後在選單列的輸入法選單選得到「善解輸入法」並能打字。由使用者實測（§7）。

## 1. 產物

- `安裝善解輸入法.app`（英文名 `Shanjie Installer`），bundle ID `com.nyanako.shanjie.installer`（刻意不用輸入法 ID 當開頭，免得 LaunchServices 與模式 ID 的查詢混在一起；小麥注音也是分開的）；內附版本取自自己的 `CFBundleShortVersionString`（建置時由 `SHANJIE_VERSION` 設定），`check-installer.sh` 斷言它和 `Resources/shanjie-<版本>.zip` 的檔名一致，一般的 GUI app（不是 `LSUIElement`），`LSMinimumSystemVersion` 26.0，arm64，hardened runtime，**沒有 entitlements**。
- 內容：
  - `Contents/MacOS/shanjie-installer`
  - `Contents/Resources/shanjie-<版本>.zip`：Release 的同名附件原檔（裡面是已簽章、已公證、已 staple 的輸入法本體），`cmp` 逐位元組相同。**不放展開的 .app**（仿小麥注音的 `NotarizedArchives`）：展開的輸入法放在 Resources 裡，可能被 LaunchServices 登記成同一個 bundle ID 的第二份，codesign 也只把它當資源資料（security-reviewer P2）。
  - `Contents/Resources/install-ime.sh`：repo 的 `scripts/install-ime.sh`，原樣複製。
  - `Contents/Resources/LICENSES/`：和輸入法本體相同的授權檔。
  - `zh-Hant.lproj`、`en.lproj` 的顯示名稱（`InfoPlist.strings`）。介面字串寫在程式裡，依第一個偏好語言選繁體中文（`zh-Hant`、`zh-TW`、`zh-HK`、`zh-MO`）或英文。
- Release 附件新增 `shanjie-installer-<版本>.zip`（ASCII 檔名；解壓後是 `安裝善解輸入法.app`）與它的 `.sha256`。原本的 `shanjie-<版本>.zip` 保留（brew cask 與 `install-ime.sh` 用）。

## 2. 安裝流程（安裝程式內）

1. 開啟後顯示一個視窗：標題、一段說明（裝到哪裡、會結束執行中的舊版）、授權摘要與「顯示授權」，以及依目前狀態決定的按鈕：
   - 沒裝過，或已安裝的版本較舊（比較 `CFBundleShortVersionString`，數字比較）→ 主按鈕「安裝」／「更新」，走第 2–4 步。
   - **已安裝的版本和內附的相同**（例如登出再登入後重開）→ 主按鈕「啟用」，**只做第 3–4 步，不呼叫 install-ime.sh**；次要按鈕「重新安裝」走第 2–4 步，旁邊說明「會把目前這份當成上一版保留，取代原本保留的上一版」。（plan-verifier：重跑整個腳本會刪掉真正的上一版、結束執行中的輸入法。）
   - 已安裝的版本較新 → 只顯示說明與「啟用」（第 3–4 步），不降版。
   - 這段狀態判斷寫成不碰系統的純函式，有單元測試（§6）。
2. 按下後（按鈕立刻停用，避免重複執行）：
   - 用 `/usr/bin/ditto -x -k --noqtn` 把 `Resources/shanjie-<版本>.zip` 解到 `NSTemporaryDirectory()` 底下新建的資料夾。`--noqtn`：不讓 quarantine 跟著複製到已安裝那份（security-reviewer P1-B。小麥注音用 `/usr/bin/unzip` 解內附的 zip，附帶避開了 quarantine；OpenVanilla 的做法只看到搜尋摘要，未核對）。外層安裝程式在啟動時已經過 Gatekeeper，內層由外層簽章封住。
   - 執行 `Resources/install-ime.sh <暫存區/善解輸入法.app>`：`Process` 的 `executableURL` 是 `/bin/bash`、參數用陣列（不經 `sh -c`），**不繼承環境**，只給 `HOME`（取自 `getpwuid(getuid())`，和 Swift 端算目的地用同一個來源）、`PATH=/usr/bin:/bin:/usr/sbin:/sbin`、`SHANJIE_INSTALL_SKIP_REGISTER=1`（security-reviewer P3：繼承來的 `SHANJIE_INSTALL_FILES_ONLY`、`BASH_ENV`、`PATH` 會讓安裝靜靜走錯路）。
   - stdout 丟棄；stderr 讀到 EOF 後才 `waitUntilExit()`（避免管線塞滿卡住），截斷到 4 KB，當純文字顯示（不進格式字串）。
   - 結束後刪除暫存資料夾。
   - `SHANJIE_INSTALL_SKIP_REGISTER` 下腳本的行為：
   - 腳本照現行規則完成檔案交換（暫存、`.shanjie-previous`、舊名稱 `shanjie.app`、`lsregister -f`、結束舊行程），然後**在第 4 步（`shanjie install`）之前結束**，exit 0。
   - 這個變數只在非 files-only 模式下有意義；HOME 檢查照舊（安裝程式由使用者本人執行，HOME 就是帳號的家目錄）。
   - 腳本失敗（非 0）時，安裝程式顯示失敗與腳本的 stderr（固定文字與路徑，沒有使用者輸入），不做後續步驟。
3. > 已由 installer-v2 §9 修訂：註冊在已啟用時跳過；還差一步改成帶去系統設定（第 4 點的「登出再登入」不再適用）。

   **註冊與啟用在安裝程式自己的行程裡做**（不呼叫 `shanjie install`）：理由是小麥注音的安裝程式註解寫「System Settings now asks the user whether to activate the IME」（`AppDelegate.swift:283`），而 0.1.0 的 `shanjie install` 是一跑完就結束的終端機行程，量到的現象是啟用呼叫都回 noErr、輸入法本體卻沒被系統接受，要登出再登入才生效（s3b §13.3 修訂三）。**未驗證**：有視窗、有 run loop 的行程啟用時，系統是否會跳出確認、確認後是否不必登出。
   - 邏輯和 v0.1.1 的 `shanjie install` 相同（倚天偏好的延續寫進輸入法自己的網域 `UserDefaults(suiteName: "com.nyanako.inputmethod.shanjie")`，由參數傳入；`shanjie install` 傳 `UserDefaults.standard`，security-reviewer P1-A）：`TISRegisterInputSource` → 啟用輸入法本體 → 啟用 `.zhuyin` 模式 → 停用舊的 `.standard`／`.eten`。2026-10-10 起，安裝程式另外在**換檔之前**、用開始時讀到的狀態寫一次倚天延續（`app-sandbox.md` §2.7：沙盒版啟動後偏好設定在 container 裡，之後才寫會靜靜地失效）；上面這條路徑裡的寫入保留，已經選過排列時不重寫。
   - 共用程式：把這段 TIS 邏輯從 `macos/Sources/Shanjie/main.swift` 搬到新的 library target `ShanjieInstall`（連結 Carbon），**bundle URL 與 bundle ID 是參數**；`Shanjie` 傳 `Bundle.main.bundleURL`（行為不變），安裝程式傳**已安裝那一份** `~/Library/Input Methods/善解輸入法.app`，絕不傳自己 bundle 裡的路徑（被 App Translocation 時那是隨機的唯讀路徑）。`ShanjieKit` 仍不連結 TIS。
4. 確認結果：用主執行緒 run loop 上的 `Timer`（不是背景執行緒 sleep；TIS 可能只在 run loop 上收到清單更新，未驗證）每 0.5 秒查一次已啟用清單（`TISCreateInputSourceList(…, false)`）裡有沒有輸入法本體，最多 30 秒，期間視窗顯示「如果系統跳出視窗，請允許『善解輸入法』」。
   - 查得到 → 「安裝完成：從選單列的輸入法選單選『善解輸入法』」，按鈕「完成」。
   - 若共用的註冊邏輯在「列不到模式」時就提前結束（不會啟用），輪詢不可能成功；這時直接顯示下一行的「還差一步」，倚天延續與停用舊模式也一起延到重新檢查或登出後的「啟用」。
   - 30 秒內查不到 → 「還差一步：請登出再登入，然後再打開這個安裝程式一次，按『啟用』」，按鈕「重新檢查」與「完成」。重新檢查只重做第 3、4 步；登出後重開時走第 1 步「版本相同 → 啟用」那條路。
5. 安裝程式不寫自己的偏好設定（只有上面那個寫進輸入法網域的倚天延續）、不連網、不留下背景行程，按「完成」或關閉視窗就結束。

## 3. 建置與發布

- `macos/Package.swift` 新增 executable target `ShanjieInstaller`（AppKit，程式碼建的視窗，不用 nib）與 library target `ShanjieInstall`。
- `scripts/build-installer.sh <輸出資料夾> [輸入法 zip]`：組出 `安裝善解輸入法.app`（執行檔、Info.plist、lproj、`install-ime.sh`、授權檔）。給了 zip 就複製進 `Resources/` 並 ad-hoc 簽章（本機與 CI 的檢查用）；沒給就只輸出未簽章骨架（release 的 build job 用，zip 由 sign job 放入）。
- `Makefile` 新增 `installer`（先 `make bundle`，不重建）：把 `build/善解輸入法.app` 用 `ditto -c -k --keepParent` 壓成 `build/shanjie-<它的版本>.zip`，再組出 `build/安裝善解輸入法.app`；`build-installer.sh` 的版本一律由呼叫端以 `SHANJIE_VERSION` 傳入（版本規則只在 build-app.sh）；`clean-bundle` 一併處理（`lsregister -u` 後刪除）。
- `release.yml`：
  - `build` job 用 `build-installer.sh` 組出**完整的未簽章安裝程式 bundle，只缺 `Resources/shanjie-<版本>.zip`**，和輸入法本體一樣用 tar 上傳。
  - `sign` job **仍然不 checkout、不跑任何 repo 的程式**，只用 Apple 的工具：在輸入法本體**公證並 staple、壓成 `shanjie-<版本>.zip` 之後**，把那個 zip 複製進安裝程式的 `Resources/`、重新 `security unlock-keychain`（第一次公證可能等很久，鑰匙圈的自動上鎖是 3600 秒）、用 Developer ID 簽章（hardened runtime、timestamp，不加 `--deep`：外層沒有巢狀程式碼，zip 只是資源）、`ditto -c -k --keepParent` 壓縮、送公證、staple、從 zip 解出來驗證（`codesign --verify --strict --deep`、`spctl -a -vv` 為 Notarized Developer ID、外層也跑 `stapler validate`、`Resources/shanjie-<版本>.zip` 和要發布的附件 `cmp` 相同）。兩次公證都在同一個 step 裡做（p8 由這個 step 的 `trap` 刪除）；`timeout-minutes` 從 90 調到 150。
  - `publish` job 多上傳 `shanjie-installer-<版本>.zip` 與 `.sha256`；重跑時的「附件都在且 `.sha256` 相同才略過」規則涵蓋兩組附件。
- brew cask 不變。**README 在本片不改安裝方式的順序**：只在「手動」一段加一句「也可以用 Release 的安裝程式（測試中）」。§7 通過、記錄在 research log 之後，才另開一個小 PR 把安裝程式改成第一種方式。

## 4. install-ime.sh 的變更

- 新增 `SHANJIE_INSTALL_SKIP_REGISTER=1`：在第 4 步之前 `exit 0`，印一行「files and processes done; registration skipped」。
- files-only 模式優先（files-only 本來就停在更前面）；兩個變數都沒設時行為不變。
- `test-install-ime.sh` 加一個情況：同時設 files-only 與 skip-register 時，行為和只有 files-only 相同，並**斷言 stdout 是「files only:」那一行**（不是「registration skipped」）。
- skip-register 的 `exit 0` 必須在第 3 步（`lsregister -f`、結束舊行程）**之後**、第 4 步之前；測試到不了那裡，由 code review 與 verifier 讀程式確認。

## 5. 安全與界線

- 安裝程式只寫暫存資料夾、輸入法網域的 `layout` 偏好，以及 `~/Library/Input Methods/` 裡 install-ime.sh 已列出的四個路徑，不用 sudo、不要求管理員密碼、不寫系統目錄。
- 不執行從網路下載的東西：輸入法本體來自自己的 `Resources/`，由外層簽章封住。Gatekeeper 只在第一次啟動時檢查封印，之後同一個使用者的行程理論上能改 bundle；那樣的行程本來就能直接寫 `~/Library/Input Methods`，不多給權限，接受（不加啟動時的 `SecStaticCodeCheckValidity`）。
- 被 Gatekeeper 隨機搬移（App Translocation）時，`Resources/` 是唯讀的隨機路徑；安裝程式只從那裡讀 zip，註冊用的一律是已安裝的路徑。**未驗證**：translocation 下的實際行為，§7 記錄。
- 2026-10-04 量測：brew 安裝的那份帶 quarantine（旗標含使用者已核准；推測是使用者在終端機執行過它，未驗證），執行中的輸入法 `lsof` 顯示的是 `~/Library/Input Methods/善解輸入法.app` 本身，沒有被搬到隨機路徑。安裝程式路徑用 `--noqtn`，不依賴這個核准旗標。
- **agent 與 CI 都不啟動安裝程式**、不執行它的執行檔（契約與 brief 明寫）；它只能由使用者在真實帳號上點開。CI 只檢查 bundle 結構與簽章。唯一例外是 `installer-v2.md` §1.4 的畫面輸出：只由 main 執行 `swift build` 出來的裸執行檔，參數只有 `--render-steps`，只寫指定資料夾裡的 PNG。

## 6. 驗收（agent 可做的部分）

1. `make installer` 後 `scripts/check-installer.sh build/安裝善解輸入法.app`：
   - 結構：執行檔、`Resources/shanjie-<版本>.zip`（和 `build/` 那份 `cmp` 相同、`Resources/` 裡沒有任何 `.app`）、`Resources/install-ime.sh`（和 repo 的同一份逐位元組相同）、授權檔；
   - `Info.plist`：bundle ID、最低系統版本、沒有 `LSUIElement`；
   - `codesign -d --entitlements -` 外層沒有 entitlements，flags 含 runtime；`codesign --verify --strict --deep` 通過。
2. `ShanjieInstall` 搬移後，`make test` 全綠；`shanjie install` 的行為和 v0.1.1 相同，傳入 `Bundle.main.bundleURL`、`Bundle.main.bundleIdentifier`、`UserDefaults.standard`（讀程式碼確認；TIS 部分無法在 agent 端執行）；安裝程式傳入已安裝路徑、從那個 bundle 讀出的 ID、`UserDefaults(suiteName: "com.nyanako.inputmethod.shanjie")`（讀程式碼確認）。
2a. 安裝程式的狀態判斷（沒裝、較舊、相同、較新 → 按鈕與步驟）有單元測試；把「相同 → 只啟用」改成走完整安裝時測試失敗（突變）。
3. `test-install-ime.sh` 含 §4 的新情況並通過；把 skip-register 的判斷改成也在 files-only 前生效時，至少一個情況失敗（突變）。
4. CI 的 shell job 跑 1、2、3。
5. 合併**之後**的 Release 試跑（workflow_dispatch 只能在 main）：推 tag 前必須通過；sign job 的驗證步驟全部通過，`shanjie-installer-0.0.0.zip` 留在一天的 artifact。

## 7. 使用者實測

在一個還沒裝善解的帳號（或先 `brew uninstall --cask shanjie`、刪掉 `~/Library/Input Methods/善解輸入法.app` 與 `.shanjie-previous`，並登出再登入），從 Release 下載安裝程式：

1. Finder 解壓、點兩下，記錄有沒有 Gatekeeper 視窗、內容是什麼。
2. 按「安裝」，記錄有沒有系統的「允許輸入法」確認視窗、最後顯示「安裝完成」還是「還差一步」。
3. 選單列選得到「善解輸入法」並能打字；若顯示「還差一步」，登出再登入後再開一次安裝程式，記錄結果。
4. `xattr -l ~/Library/Input\ Methods/善解輸入法.app` 沒有 `com.apple.quarantine`；`lsof -p <PID> -d txt` 的路徑不在 `AppTranslocation` 底下；`lsregister -dump` 裡 identifier **完全等於** `com.nyanako.inputmethod.shanjie` 的 bundle 只有 `~/Library/Input Methods` 那一份。前提：開發用的 `build/善解輸入法.app`（repo 與 worktree）先 `make clean-bundle` 或 `lsregister -u`，或改用沒建置過的帳號。
5. 「更新」路徑（安裝程式在 App Management 保護下搬移已啟動過的輸入法）要到第二次含安裝程式的發版才測得到，屆時加測；未驗證。

**通過條件**：步驟 3 成立（必要時經過一次登出再登入）。若安裝程式需要登出的情況比 brew 加指令更多，或有無法放行的 Gatekeeper 視窗，停在這裡、README 先不推薦安裝程式。

## 8. 停止條件、預算與回滾

- **停止條件**：PR #7 還沒合併；Release 試跑在同一個原因上失敗兩次；公證拒絕外層（含 `Resources/` 裡的 zip）；§6 的突變存活。遇到就停下來問使用者。
- **預算**：實作加審查最多三輪（每輪 = 修改 → `/code-review` → verifier）；超過就停下來報告。
- **發版**：本片合併後，含安裝程式的第一個 tag（預計 v0.1.2，或和 v0.1.1 合併發）一樣要使用者當次同意才推。
- **回滾**：
  - 程式：revert 本片的 merge commit。
  - 已發布的 Release 安裝程式有問題：經使用者同意後只刪除 `shanjie-installer-*` 兩個附件，`shanjie-<版本>.zip` 與 brew cask 不受影響；README 的「測試中」一句一併拿掉。

## 9. 審查處置（2026-10-04）

| 來源 | 問題 | 處置 |
|---|---|---|
| plan-verifier P1 | 缺安全審查 | FIX：已審，本表 |
| plan-verifier P2 | sign job 組安裝程式會跑 repo 程式碼 | FIX：build 組好未簽章骨架，sign 只用 Apple 工具（§3） |
| plan-verifier P2 | 登出後重開只能「重新安裝」，會刪掉真正的上一版 | FIX：版本相同時主按鈕「啟用」只做第 3–4 步（§2.1） |
| plan-verifier P2 | TIS 註冊對象不明 | FIX：`ShanjieInstall` 收 URL／ID 參數，安裝程式傳已安裝路徑（§2.3） |
| plan-verifier P2 | README 在實測前就推薦安裝程式 | FIX：§7 通過後才另開 PR 調順序（§3） |
| plan-verifier P2 | 缺回滾、停止條件、預算 | FIX：§8 |
| security P1-A | `install()` 用 `Bundle.main` 與 `UserDefaults.standard`，搬進安裝程式會註冊錯的 bundle、寫錯網域 | FIX：參數化 URL、ID、defaults（§2.3、§6.2） |
| security P1-B | quarantine 跟著 `ditto` 進已安裝那份，可能被隨機搬移、`pkill` 比對不到 | FIX（安裝程式路徑）：內附 zip，`ditto -x -k --noqtn`；brew／手動路徑維持現狀（已量到沒被搬移），§7 驗收 4 |
| security P2 | 展開的內層 app 可能被 LaunchServices 登記成第二份 | FIX：Resources 放 zip，不放展開的 app（§1） |
| security P2 | sign job 組裝 | FIX：同 plan-verifier |
| security P3 | 繼承環境變數 | FIX：明確最小環境、參數陣列（§2.2） |
| security P3 | stderr 讀取卡死、格式字串 | FIX：讀到 EOF 再 wait、截斷、純文字（§2.2） |
| security P3 | 兩次公證的鑰匙圈上鎖與逾時 | FIX：簽外層前重新解鎖、timeout 150 分（§3） |
| security P3 | 更新時的 App Management 保護 | DEFER：第二次發版實測（§7.5） |
| security P4 | `pkill -U`、內層巢狀驗證、`dscl` 只查本機節點、同時執行兩個安裝程式 | ACCEPT：pattern 已錨定本帳號 HOME；內層由 zip 的 `cmp` 與原本對 zip 內 app 的驗證涵蓋；`dscl` 是既有行為；按鈕按下即停用 |
| plan-verifier 第二輪 P2 | 安裝程式 bundle ID 以輸入法 ID 開頭，§7.4 的 LaunchServices 檢查必然誤判 | FIX：改成 `com.nyanako.shanjie.installer`；§7.4 改成 identifier 完全相符，並先清掉開發建置 |
| plan-verifier 第二輪 P3 | 過時用語、未核對的引述、skip-register 的位置與 stdout 斷言、版本來源、提前結束的路徑、試跑順序、安裝程式傳入參數的檢查、C locale | FIX（前七項，§1–§8）；C locale ACCEPT：路徑比對已在 s3b §11 量過，安裝程式路徑由 §7 實測涵蓋 |
| plan-verifier P3 | 外層 `stapler validate`、run loop 上的 Timer、狀態判斷的單元測試、§5 未量的敘述 | FIX：§3、§2.4、§6.2a、§5 |

## 10. 範圍外

解除安裝功能（用 brew 或手動刪除）；自動更新；`.pkg`；全系統安裝（`/Library/Input Methods`）；安裝程式內的設定選項。
