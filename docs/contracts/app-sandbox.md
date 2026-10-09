# 契約：App 沙盒第一片（輸入法本體進沙盒、資料移轉）

狀態：收尾審查 READY（2026-10-10，§8）；實作中。這片在 v0.4.0 發版之後才合併、以 v0.5.0 發佈（§4.4；使用者 2026-10-10 不發 v0.3.1，原本寫 v0.3.1 之後）。`docs/PLAN.md`「v0.3.0 之後」排定的兩片中的第一片。

- 系統行為的量測見研究紀錄 2026-10-09「App 沙盒的可行性」。
- 開工前的安全審查（`pilotfish:security-reviewer`，2026-10-09，只讀）逐項處置在 §7。

## 0. 要做到什麼、怎麼看到

- 正式版的輸入法本體（`善解輸入法.app/Contents/MacOS/shanjie`）在 App 沙盒裡執行，entitlements 只有兩個。
- 第一次啟動沙盒版時，學習資料與偏好設定搬進 container。使用者看不出差別：學過的選字、倚天排列、選單設定都在。
- **怎麼看到**：
  - CI：entitlements 逐鍵比對；沙盒裡的 selftest；正式 ID 的移轉測試（在拋棄式 runner 上）。
  - 虛擬機：release dry run 產出的 Developer ID 版，從 0.3.x 升級，再退回、照文件把資料搬回。
  - 使用者實機，用 dry run 產出的 Developer ID 版（正式發佈之前，先備份，§4.3 第 0 步）：
    - 活動監視器的「沙盒」欄位是「是」；
    - 升級前學過的選字仍排第一；
    - 倚天排列還在；
    - 安全輸入時選單顯示「學習已暫停（安全輸入）」；
    - Caps Lock 切換正常。

## 1. 量到的與沒量到的

研究紀錄 2026-10-09，虛擬機 macOS 26.6.2，拋棄式 ID，帶移轉清單，各一次：

- selftest 在沙盒裡結束碼 0。
- IMK 的連線名稱登記成功，沒有沙盒拒絕。
- 沙盒裡的 `shanjie install` 能註冊。
- 學習資料夾被「搬」進 container（不是複製），權限與不備份旗標保留。
- 偏好設定沒寫進清單也被搬走。
- container 的擁有者記成那次的簽章。
- 移轉清單的路徑和 bundle ID 無關：拋棄式 ID 的組建搬走了正式 ID 的學習資料。

**沒量到、而且這片的安全靠它的**：沒有移轉清單時不搬。這是推論（只量過有清單的情況；偏好設定沒寫進清單也被搬了，所以不能當然成立）。§4.1 第 3 項在 CI 斷言；不成立就停（§5）。

其他沒量到的項目，在 §4 用 CI、虛擬機與實機補量：

- 實際打字；
- 由 LaunchServices 啟動；
- `IsSecureEventInputEnabled`；
- Caps Lock；
- 同 ID 重新 ad-hoc 組建之後的 container；
- Developer ID 的 container；
- 其他 macOS 版本。

## 2. 改動

### 2.1 entitlements

- `scripts/build-app.sh` 依 `BUNDLE_ID` 產生 entitlements 檔，兩個鍵：
  - `com.apple.security.app-sandbox = true`；
  - `com.apple.security.temporary-exception.mach-register.global-name = [<BUNDLE_ID>_Connection]`。這個名稱必須逐字等於 Info.plist 的 `InputMethodConnectionName`，所以不寫死，由 `BUNDLE_ID` 產生。
- ad-hoc 簽章時帶上：`codesign --force --sign - --options runtime --entitlements <檔>`。
- 新增 `scripts/check-entitlements.sh <app>`：`codesign -d --entitlements - --xml` 經 `plutil` 正規化後，必須**完全等於**上面兩個鍵，mach 名稱等於該 bundle 的 `InputMethodConnectionName`；不符就結束碼非 0。`check-app.sh` 第 3 項改呼叫它（原本要求「沒有 entitlements」）。
- 安裝程式不變，仍然沒有 entitlements（`check-installer.sh` 不動）。

### 2.2 移轉清單只給正式 ID

- 只有 `BUNDLE_ID` 是正式 ID（`com.nyanako.inputmethod.shanjie`）時，`build-app.sh` 才放 `Contents/Resources/container-migration.plist`，內容是 `Move = [${ApplicationSupport}/shanjie]`。
- 其他 ID 不放。依 §1 的推論，沒有清單就不搬，所以開發版與拋棄式組建不會帶走真正的學習資料；§4.1 第 3 項驗證這個推論。
- 偏好設定不寫進清單：系統自己會搬（§1 量到一次），由 §4.1 第 3 項在 CI 斷言。

### 2.3 本機組建預設用開發版 ID（使用者 2026-10-09 決定）

- **ID 與名稱**（`build-app.sh` 依 ID 決定，沒有其他開關）：

  | ID | 資料夾 | `CFBundleDisplayName`、zh-Hant 名稱 | en 名稱 | 移轉清單 |
  |---|---|---|---|---|
  | `com.nyanako.inputmethod.shanjie`（正式） | `善解輸入法.app` | 善解輸入法 | Shanjie | 有 |
  | 其他任何 ID（預設 `com.nyanako.inputmethod.shanjie.dev`；selftest 與 CI 的拋棄式 ID 也是） | `善解（開發版）.app` | 善解（開發版） | Shanjie (Dev) | 沒有 |

  輸入模式 ID、連線名稱、偏好設定網域、container 都跟著 ID 走，開發版不碰正式版的任何一樣。
- `build-app.sh` 的 `BUNDLE_ID` 預設改成 `com.nyanako.inputmethod.shanjie.dev`。`Makefile` 加 `DEV_APP_NAME := 善解（開發版）.app`，`selftest-bundled` 與 `clean-bundle` 用它；`make installer` 只接受正式 ID 的組建（`build/善解輸入法.app` 不存在就報錯，訊息寫要先 `make bundle BUNDLE_ID=com.nyanako.inputmethod.shanjie`）。
- **`scripts/install-ime.sh`**（安裝程式裡附的是同一份，`check-installer.sh:27` 逐位元比對）：從傳入的 bundle 讀 `CFBundleIdentifier` 決定目的地，不寫死開發版的名稱。
  - 正式 ID：目的地 `善解輸入法.app`、舊版 `shanjie.app` 的升級、`.shanjie-previous`，和現在逐行相同。
  - `com.nyanako.inputmethod.shanjie.dev`：目的地 `善解（開發版）.app`，保留上一版在 `.shanjie-dev-previous`，沒有舊版名稱的處理；結束舊程序的比對樣式只比對開發版的路徑。
  - 其他 ID：拒絕安裝，結束碼非 0。
  - `scripts/test-install-ime.sh`（暫存 HOME）加兩個情境：已有正式版 `善解輸入法.app` 時安裝開發版，正式版原封不動；正式版照舊裝到 `善解輸入法.app`。
- **`scripts/check-app.sh`**：要檢查的 ID 由環境變數 `EXPECTED_BUNDLE_ID` 給（沒給時用正式 ID），資料夾與各個名稱照上表比對，不再寫死。另外：
  - CI 以外（`CI` 不是 `true`）遇到正式 ID 的組建時，第 6 項（執行 selftest 與被拒絕的參數）不跑，結束碼非 0，訊息寫明原因：本機的 ad-hoc 組建不能先建立正式 ID 的 container。
  - `SKIP_RUN=1` 時只做第 2、3 項，不執行 bundle，結束碼 0（§5 的本機限制期間用；正式 ID 的組建也一樣）。
  - 第 6 項前後快照比對的內容不變；container 是否存在不放進那個相等比對，改由 §2.6 的步驟分別檢查「之前沒有、之後有」。
- `make selftest-bundled` 照舊用拋棄式 ID（本機預設 `com.nyanako.inputmethod.shanjie.selftest`），沒有移轉清單。s3b §9 與 `Makefile` 註解的「selftest 不寫任何檔案或偏好設定」改成：selftest 本身不寫，但第一次在沙盒裡執行會建立那個 ID 的 container。
- CONTRIBUTING 與 `docs/verification.md` 的本機安裝說明改成：本機組建裝成「善解（開發版）」，要在系統設定加入一次，學習資料和正式版分開。

### 2.4 釋出流程

- `release.yml` 的 build 步驟明確傳 `BUNDLE_ID=com.nyanako.inputmethod.shanjie`。
- `sign` 重簽 IME 時加 `--preserve-metadata=entitlements`。`sign` 仍然不執行 repo 裡的程式碼。
- `verify()` 改成依 bundle 給預期值（比對方式和 `check-entitlements.sh` 相同，寫在 workflow 裡，因為 `sign` 不執行 repo 程式碼；兩處的說明互相指向對方）：
  - IME：`CFBundleIdentifier` 是正式 ID，entitlements 完全等於 §2.1 的兩個鍵，mach 名稱等於 `InputMethodConnectionName`；
  - 安裝程式：`CFBundleIdentifier` 是 `com.nyanako.shanjie.installer`，沒有任何 entitlements（現行規則）。
- **dry run 保留產出檔一天**（使用者 2026-10-09 決定）：dry run 用 `actions/upload-artifact` 上傳簽章、公證後的兩個 zip，`retention-days: 1`，給 §4.2 的虛擬機與 §4.3 的實機驗收用。正式發佈不變。
- s3b §3、§9 與 `docs/verification.md` 裡「沒有 entitlements」的說法跟著改；PLAN §7 的 R9 改成「hardened runtime＋App 沙盒（兩個 entitlements）」。

### 2.5 卸載、退回與搬回

- cask 的 `zap` 加上 `~/Library/Application Support/shanjie` 與 `~/Library/Containers/com.nyanako.inputmethod.shanjie`。
- **退回**：
  - 移轉是「搬」。降回沒有沙盒的版本時，舊版看到的是空的學習資料與預設設定。
  - 退回期間學到的資料留在舊位置；再升級時 container 已經存在，不會再搬。
  - 這兩點寫進 `install-ime.sh` 與安裝程式「重新安裝」的退回說明。
- **搬回**：`docs/sandbox-restore.md` 寫一個手動指令：先結束輸入法程序；把 container 裡的學習資料與偏好設定 `ditto` 複製回原位置，比對雜湊；相同時刪掉**整個** `~/Library/Containers/com.nyanako.inputmethod.shanjie`。container 只在第一次建立時移轉，留著它的話，下次升級到沙盒版不會再搬，搬回來的資料就看起來不見了。§4.2 第 3 項在虛擬機驗證。不做自動搬回。
- 不照蘋果文件「刪掉 container 再測一次」的步驟，那會刪掉唯一的一份資料。只在拋棄式 runner 或虛擬機裡這樣做。

### 2.6 CI 的步驟與順序（`ci.yml` 的 shell job）

照這個順序，前面的步驟不得啟動任何沙盒組建：

0. `make bundle OUT_DIR=build/default`（不帶 `BUNDLE_ID`）：只建置。用 PlistBuddy 斷言 `CFBundleIdentifier` 是 `com.nyanako.inputmethod.shanjie.dev`、資料夾與名稱照 §2.3 的表、沒有 `Contents/Resources/container-migration.plist`。
1. `make bundle BUNDLE_ID=com.nyanako.inputmethod.shanjie`：正式 ID，`build/善解輸入法.app`。只建置，不執行。斷言有 `container-migration.plist`。
2. `make bundle BUNDLE_ID=com.nyanako.inputmethod.shanjie.ci-nomanifest OUT_DIR=build/nomanifest`：拋棄式 ID，沒有移轉清單。只建置。
2a. **本機防呆的驗證**（在 CI 裡做，不在維護者的機器上）：`CI= EXPECTED_BUNDLE_ID=com.nyanako.inputmethod.shanjie scripts/check-app.sh build/善解輸入法.app` 必須結束碼非 0、印出拒絕訊息；接著斷言 `~/Library/Containers/com.nyanako.inputmethod.shanjie` 仍然不存在。`SKIP_RUN=1` 的同一個指令必須結束碼 0（只做第 2、3 項）。防呆如果寫錯，第 3 步的「container 已存在就拒絕」也會擋下來。
3. **`scripts/test-sandbox-migration.sh build/善解輸入法.app "build/nomanifest/善解（開發版）.app"`**：這個 job 第一次啟動沙盒組建。
   - **拒絕條件**（結束碼非 0、什麼都不寫、不啟動任何東西；依這個順序檢查）：`CI` 不是 `true`；`HOME` 不是這個帳號真正的家目錄（用 `dscl` 查 `NFSHomeDirectory`，和 `install-ime.sh` 的做法相同；container 的移轉看的是帳號的家目錄，不是 `$HOME`）；或在帳號的家目錄下，下列任一已經存在：`~/Library/Application Support/shanjie`、`~/Library/Preferences/com.nyanako.inputmethod.shanjie.plist`、`~/Library/Containers/com.nyanako.inputmethod.shanjie`、拋棄式 ID 的 container。
   - **放好來源**：合成的 `learning.tsv`、`learning.tsv.corrupt`，對目錄設不備份旗標，`defaults write com.nyanako.inputmethod.shanjie layout eten`（安裝程式寫的就是這個鍵，§2.7）。記下每個檔的雜湊與權限。
   - **沒有清單不搬**（§1 的推論）：執行拋棄式組建的 `--selftest` 一次。斷言：來源還在原位、逐位元相同；拋棄式 ID 的 container 之前不存在、之後存在（沙盒有套上）；裡面沒有學習資料。
   - **移轉**：執行正式組建的 `--selftest` 一次。斷言：
     - 來源消失；
     - container 裡逐位元組相同，0700／0600；
     - 不備份旗標保留；
     - container 裡的偏好設定 `layout=eten`。
   - **只搬一次**：再放一份新的來源，再執行一次：不會再移轉，來源原封不動。
   - 結束前確認來源的偏好設定檔 `~/Library/Preferences/com.nyanako.inputmethod.shanjie.plist` 已不存在（被搬走），第 7 步的快照才不會受 cfprefsd 延遲寫入影響。
   - 讀 container 如果在 CI 上跳 App Data 的 TCC 詢問而卡住（N6），改成由同一個沙盒執行檔回報它讀到的內容；改法寫進這份契約再做。
4. 同一個腳本的拒絕測試，兩種情況都要結束碼非 0、暫存目錄裡沒有新檔案：`CI= HOME=<暫存目錄> …`；`CI=true HOME=<暫存目錄> …`（log 裡有家目錄不符的訊息）。
5. `scripts/test-install-ime.sh`、`swift test`（不變）。
6. `make selftest-bundled SELFTEST_BUNDLE_ID=com.nyanako.inputmethod.shanjie.selftest-$GITHUB_RUN_ID`，前後各檢查一次 `~/Library/Containers/<該 ID>`：之前不存在、之後存在。原本傳空值、用正式 ID 的那一步取消（正式 ID 的 selftest 已在第 3 步與第 7 步跑過）。
7. `EXPECTED_BUNDLE_ID=com.nyanako.inputmethod.shanjie scripts/check-app.sh build/善解輸入法.app`。
8. **entitlements 的突變測試**：複製 `build/善解輸入法.app` 兩份，一份用 `codesign --force --sign - --options runtime` 重簽（不帶 `--preserve-metadata`），另一份帶 `--preserve-metadata=entitlements` 重簽。`scripts/check-entitlements.sh` 對前者要失敗、對後者要通過。
9. `make installer`、`scripts/check-installer.sh`（不變，用第 1 步的正式組建）。

### 2.7 倚天排列的沿用改在換檔之前寫

- 現況：`Registration.fullFlow` 在註冊、啟用、而且使用者在系統設定接受之後，才寫 `layout=eten`（`Registration.swift` 約第 165 行）。安裝程式等使用者接受的那段時間，沙盒版可能已經啟動、偏好設定已經搬走，這時寫在 container 外，靜靜地失效。
- 改法：把「只開了倚天模式、而且還沒選過排列」的判斷與寫入抽成一個函式。安裝程式在**換檔之前**、用開始時讀到的輸入法狀態呼叫它一次（那時裝的是沒有沙盒的舊版，container 還不存在），之後第一次啟動沙盒版時隨偏好設定一起搬。`fullFlow` 裡原本的呼叫保留（`shanjie install` 在沙盒裡執行，寫的就是 container 裡的網域；已經選過排列時不重寫）。
- 測試：安裝程式流程的單元測試斷言這個函式在換檔之前被呼叫；§2.6 第 3 步斷言「在第一次啟動之前、用 `defaults` 寫的 `layout`」會被搬進 container。

## 3. 不在這一片（第二片或之後）

- 網站隱私說明補 Time Machine（F6）；
- 學習的 denylist 擴充（F7）；
- 安裝程式的簽章檢查（F9）；
- 使用者層的詞包位置（N7，ACG 詞包 C 片要先決定）；
- 註冊用的輔助程式：§1 量到不需要，除非 §4 的實機推翻。

## 4. 驗收

### 4.1 CI（§2.6 的步驟，PR 的 CI 紀錄裡看得到順序與 ID）

1. entitlements：第 7 步的 `check-app.sh` 第 3 項通過；第 8 步的突變：不帶 `--preserve-metadata` 的那份被拒絕、帶的那份通過。
2. 沙盒有套上：第 3 步拋棄式 ID 的 container、第 6 步 selftest ID 的 container，都是之前不存在、之後存在。
3. 移轉與「沒有清單不搬」：第 3 步的所有斷言。
4. 拒絕：第 4 步。
5. 非正式 ID 的組建沒有 `container-migration.plist`、名稱照 §2.3 的表；正式 ID 的有（§2.6 第 0、1 步）。
6. `make bundle` 不帶參數時的 `CFBundleIdentifier` 是開發版 ID（§2.6 第 0 步）。
7. `test-install-ime.sh` 的兩個新情境（§2.3）；`check-installer.sh` 照樣通過。
8. 本機防呆：§2.6 第 2a 步（在 CI 裡驗證，不在維護者的機器上跑任何正式 ID 組建）。
9. §2.7 的單元測試。

### 4.2 虛擬機（main 執行，用 dry run 的 Developer ID 版）

1. 在全新帳號裝 0.3.x、學一個詞，再升級成沙盒版：學習資料與偏好設定搬好、selftest 正常、`log` 沒有善解的沙盒拒絕。
2. 同一個開發版 ID 重新 ad-hoc 組建兩次，第二次仍能讀到第一次 container 裡的學習資料，也不跳詢問。
3. **退回、搬回、再升級**：升級之後降回 0.3.x，照 `docs/sandbox-restore.md` 的指令搬回。斷言：指令結束碼 0、沒有跳詢問、`learning.tsv` 和升級前的那份逐位元相同、權限 0600、container 已不存在。接著再升級成沙盒版一次：container 裡的 `learning.tsv` 和搬回的那份逐位元相同，沒有跳詢問。（虛擬機裡沒辦法用介面打字，所以用檔案比對，不用「選字排第一」。）
4. 從只開倚天模式的雙模式舊版，用安裝程式升級：container 裡的偏好設定 `layout=eten`（`~/Library/Containers/com.nyanako.inputmethod.shanjie/Data/Library/Preferences/`）。舊版要在系統設定手動加入，虛擬機的介面操作做不到時，這一項改由 §2.7 的單元測試與 §2.6 第 3 步代替，並在 PR 寫明。

### 4.3 實機（使用者，用 dry run 的 Developer ID 版；每次 ≤ 15 秒的部分由 `imeshot` 做）

0. **先備份**（使用者自己做，main 給指令）：第一次啟動沙盒版之前，把 `~/Library/Application Support/shanjie` 與 `~/Library/Preferences/com.nyanako.inputmethod.shanjie.plist` 複製到備份位置。
1. 打一句、改選、按 Enter。升級前學過的選字仍排第一，倚天排列還在，選單設定還在。
2. Terminal 開 Secure Keyboard Entry，到別的 App 開輸入法選單：第一項是「學習已暫停（安全輸入）」。
3. 清除學習資料的視窗出得來，關掉後焦點回到原本的 App。
4. 更新之後 Caps Lock 切換正常。
5. 活動監視器的「沙盒」欄位是「是」。
6. 安裝時記下 `shanjie install` 或安裝程式的結果。
7. `log show --last 5m --predicate 'sender == "Sandbox" AND eventMessage CONTAINS "shanjie"'` 沒有任何 deny；log 裡沒有 "system table unavailable"（系統標點表讀得到）。

### 4.4 順序與 tag

- dry run 只能從 main 觸發（`release.yml` 的 `gate` 與 `sign` 只接受 `refs/heads/main` 的 `workflow_dispatch`），所以順序是：§4.1 的 CI 通過 → 合併 → dry run → §4.2 → §4.3。
- 這片在 v0.4.0 發版**之後**才合併，不和 v0.4.0 一起出；新詞學習（v0.4.1）已經準備好的話先發它，再合併這片。這片以 v0.5.0 發佈（使用者 2026-10-10：不發 v0.3.1，v0.4.0＝設定視窗＋A2＋model-v5；原本寫 v0.3.1 之後）。
- 從合併到 §4.3 全部通過之前，main 不推任何 tag。
- 實機都通過之後，才推正式的 tag（照例要使用者當下同意）。

## 5. 停止條件、預算、回滾

- **停止條件**：
  - 沒有移轉清單時資料被搬走（§2.6 第 3 步）；
  - IMK 在沙盒裡連不上；
  - `IsSecureEventInputEnabled` 在沙盒裡永遠是 false，學習會在密碼欄繼續；
  - 移轉測試掉資料，或偏好設定沒有搬；
  - Developer ID 版接手不了 container；
  - 搬回的指令跳詢問或資料不同；
  - Caps Lock 壞掉；
  - 系統標點表在沙盒裡讀不到。
- **預算**：`pilotfish:security-executor` 實作 1 次＋修正 1 次。
- **限制**：
  - agent 不安裝、不啟動 App、不呼叫 TIS 或 lsregister、不在 .app 裡執行任何東西、不碰 `~/Library` 與鑰匙圈；
  - 本機不執行正式 ID 的沙盒組建，`test-sandbox-migration.sh` 只在 CI 跑；
  - **在這片 PR 的 CI 跑過 §2.6 第 3 步、證明「沒有清單不搬」之前，維護者的機器上不執行任何 ID 的沙盒組建**（`make selftest-bundled`、`check-app.sh` 第 6 項、開發版的 `install-ime.sh`），main 與使用者都一樣，不只 agent。`docs/verification.md` 的本機關卡在這段期間改用 CI；`check-app.sh` 在本機可以加 `SKIP_RUN=1` 只做第 2、3 項；
  - 不推 tag。dry run 由 main 觸發，事先問使用者。
- **回滾**：
  - 合併之後、發佈之前（§4.2 或 §4.3 碰到停止條件）：先在 main 上 revert 這一片（任何 tag 之前），再處理資料：裝回 0.3.x，照 `docs/sandbox-restore.md` 搬回，或用 §4.3 第 0 步的備份。
  - 發佈之後：revert 這一片並發新版，再照同一份文件搬回。

## 6. 使用者的決定（2026-10-09）

1. **本機組建用開發版 ID**「善解（開發版）」（§2.3）。
2. **release dry run 保留產出檔一天**，供虛擬機與實機驗收 Developer ID 版（§2.4）。

## 7. 安全審查的處置（2026-10-09 審查，2026-10-10 處置）

| 項目 | 嚴重度 | 內容 | 處置 |
|---|---|---|---|
| F1 | P0 | 釋出時 `codesign --force` 重簽會丟掉 entitlements，`verify()` 與 `check-app.sh` 卻要求它是空的，全部綠燈 | FIX：§2.1、§2.4、§2.6 第 8 步 |
| N1 | P0 | 移轉清單的路徑和 ID 無關，任何 ID 的沙盒組建第一次啟動都會搬走真正的資料，包括本機的 `check-app.sh` 與 `make selftest-bundled` | FIX：§2.2（只有正式 ID 放清單）、§2.3（預設開發版 ID、`check-app.sh` 拒絕）、§2.6 第 3 步驗證「沒有清單不搬」。審查建議其他 ID 用二元素路徑；改成不放清單，比較簡單，但要靠 CI 驗證那個推論 |
| F3a | P1 | 沒有移轉清單，升級後選字記憶「消失」；清單必須和 entitlements 在同一版 | FIX：§2.2；同一個 PR |
| F3b | P1 | 蘋果文件沒寫偏好設定會自動搬 | 量過（§1）：會自動搬。CI 斷言（§2.6 第 3 步）；搬不了就停 |
| F10 | P1 | selftest 不建 IMKServer，mach 名稱寫錯 CI 抓不到 | FIX：名稱由 `BUNDLE_ID` 產生，`check-entitlements.sh` 斷言等於 `InputMethodConnectionName`；IMK 實際能不能用由實機驗收（§4.3） |
| N2 | P1 | 改了預設 ID，釋出版可能帶錯 ID | FIX：§2.4，`release.yml` 明確傳、`verify()` 依 bundle 斷言 |
| N3 | P1（推測） | ad-hoc 組建先建立的 container，Developer ID 版可能接手不了 | 避免：§2.3、§5 本機不執行正式 ID 的沙盒組建。量：§4.2 第 1 項；接手不了就停 |
| F3c | P2 | 安裝程式在沙盒外寫 `layout` | FIX：§2.7 改在換檔之前寫；§4.2 第 4 項 |
| F4 | P2 | `shanjie install` 和輸入法是同一個執行檔，會在沙盒裡註冊 | 量過（§1）：做得到。實機記結果（§4.3 第 6 項）；做不到才加輔助程式 |
| F2 | P2 | `IsSecureEventInputEnabled` 在沙盒裡沒量過；永遠 false 會讓密碼欄繼續學習 | 實機量（§4.3 第 2 項）；停止條件 |
| N4 | P2 | 不備份旗標搬完後還在不在 | 量過（§1）：保留。CI 斷言（§2.6 第 3 步） |
| N6 | P2（推測） | 測試讀別的 App 的 container，可能跳 TCC 詢問 | §2.6 第 3 步寫好替代做法；搬回指令在 §4.2 第 3 項量 |
| F8 | P3 | selftest「不碰任何檔案」在沙盒下不再成立 | FIX：§2.3 改說法；container 由 §2.6 第 3、6 步分別檢查 |
| F5 | P3 | cask 的 `zap` 漏了學習資料與 container | FIX：§2.5 |
| F12 | P3 | 只有實機看得到沙盒拒絕 | §4.3 第 7 項 |
| N5 | P3 | 退回舊版讓資料滯留 | FIX：§2.5 寫進退回說明與搬回文件 |
| F6、F7 | P3 | 網站沒提 Time Machine；denylist 太短 | DEFER：第二片（§3） |
| F9 | P4 | 安裝程式的簽章檢查 | DEFER：第二片；F1 修好後 `verify()` 已會檢查 |
| F11 | P4 | CI runner 是不是 GitHub-hosted | 確認是（`xcode-27` 是 GitHub-hosted 映像）；實作時看 job log 的 Runner Image 一次 |
| N7 | — | 使用者層詞包的位置 | DEFER：ACG 詞包 C 片 |

## 8. 審查紀錄

| 審查 | 問題 | 處置 |
|---|---|---|
| plan-verifier 第 1 次 REVISE（2026-10-10） | 1. CI 的步驟、ID 與順序沒寫，和現有腳本矛盾；2. 移轉測試在 CI 以外會動到真正的資料；3.「沒有清單不搬」寫成量過，其實沒量；4. `verify()` 同時檢查安裝程式，照原寫法每次發佈都會失敗；5. entitlements 的突變測試只能在 main 上做、還要花一次公證；6. 開發版的名稱與安裝位置沒定，`install-ime.sh` 會把正式版蓋掉；7. 倚天排列沿用的寫入時機在使用者接受之後，推論站不住；8. 搬回沒有驗收，實機升級前沒有備份 | 全部 FIX：1 → §2.6；2 → §2.6 第 3、4 步的拒絕條件；3 → §1 改成推論、§2.6 第 3 步驗證、§5 停止條件；4 → §2.4 依 bundle 給預期值；5 → §2.6 第 8 步在分支上做；6 → §2.3 的名稱表與 `install-ime.sh`、`check-app.sh` 的改法；7 → §2.7；8 → §2.5 的搬回文件、§4.2 第 3 項、§4.3 第 0 步 |
| 收尾審查 REVISE（2026-10-10） | 1. §4.1 第 8 項要在維護者的機器上驗證本機防呆，防呆寫錯就會搬走真正的資料；P3：第 4 步家目錄檢查的順序、`SKIP_RUN` 的結束碼、第 7 步快照可能受 cfprefsd 影響 | 使用者決定「修掉再審一次」：1 → §2.6 第 2a 步在 CI 驗證、§4.1 第 8 項改指向它；P3 → 拒絕條件寫明檢查順序、`SKIP_RUN` 結束碼 0、第 3 步結束前確認來源偏好設定檔已搬走 |
| plan-verifier 第 2 次 REVISE（2026-10-10；第二次之後逐項處置） | 1. 虛擬機與實機驗收只能在合併後做，沒寫順序、沒凍結 tag；2. 「沒有清單不搬」證明之前，本機的 selftest、check-app、開發版安裝都會啟動沙盒組建；3. 遷移測試的拒絕條件看 `$HOME`，`CI=true HOME=<暫存>` 可以繞過；4. §4.1 第 5、6 項沒有對應的 CI 步驟；5. 搬回後 container 還在，下次升級不會再搬 | 全部 FIX：1 → §4.4（v0.3.1 之後合併、凍結 tag）、§5 回滾先 revert；2 → §5 限制擴及維護者，`check-app.sh` 加 `SKIP_RUN`；3 → §2.6 第 3 步用 `dscl` 查帳號家目錄、第 4 步加這個情況；4 → §2.6 第 0 步；5 → §2.5 刪整個 container、§4.2 第 3 項加再升級 |
