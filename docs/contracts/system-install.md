# 系統層安裝：善解裝到 `/Library/Input Methods`（契約）

狀態：**待改寫**（2026-10-20）。第一版草稿經 `pilotfish:plan-verifier` 與 `pilotfish:security-reviewer` 都是 REVISE（§7）；安全審查指出 Sparkle 更新走系統 domain 會讓「善解要管理員密碼」變成常態，而 Sparkle 的安裝服務不綁呼叫者（app-sandbox 修訂二的 F1）讓假冒的密碼框有機會成功。維護者同日的決定（兩次選擇題）：(1) 安裝方式**讓使用者自己選**，安裝程式並列兩個選項、沒有預設，各寫清楚功能與取捨；(2) 裝給整台 Mac 的更新**保留管理員密碼**；(3) **沙盒外的獨立更新程式（根本解，原排在 0c 開關 B 片）一起進 v0.5.0**。下一版契約照這三點與 §7 的發現改寫，§1 的量測照用。

原本的狀態：這片和 App 沙盒（`docs/contracts/app-sandbox.md`）一起以 v0.5.0 發佈。

## 0. 為什麼、怎麼算做到

- **問題**：任何程式持有安全輸入時（密碼管理器、終端機的「加密的鍵盤輸入」、某些背景工具），macOS 會把裝在 `~/Library/Input Methods` 的善解反灰，使用者只能改用蘋果注音。2026-10-05 起使用者遇過多次（研究紀錄同日、PLAN「安全輸入時第三方輸入法反灰」）。
- **原因**（研究紀錄 2026-10-20，量到）：是安裝位置，不是子模式。同一個 Developer ID 版放在 `/Library/Input Methods`（`root:wheel`、所有人可讀）時，安全輸入期間不反灰、打得出中文，選單第一項是既有的「學習已暫停（安全輸入）」。系統為什麼這樣分沒有 Apple 文件，〔推論〕是使用者自己寫得進的位置不被信任。
- **維護者決定**（2026-10-20 選擇題）：v0.5.0 等這片一起發；先跑三個探針，都過才併（都過了，見 §1）。
- **怎麼算做到**：維護者在自己的 Mac 用 v0.5.0 的 dry run 安裝程式安裝（一次管理員密碼、登出一次），之後背景程式持有安全輸入時，善解不反灰、打得出中文、選單顯示「學習已暫停（安全輸入）」、Caps Lock 切換正常；接著用關於頁「立即檢查」看得到正式 feed（不按更新，同 app-sandbox §4.3 B 第 10 項）。canary 0.5.0 → 0.5.1 經 Sparkle 更新成功（跳一次管理員密碼框）。

## 1. 已經量到的（研究紀錄 2026-10-20）

| 探針 | 在哪裡 | 結果 |
|---|---|---|
| 位置對照 | tart 虛擬機（macOS 26.6.2），dry run Developer ID 版 | `~/Library` 反灰；搬到 `/Library`（`root:wheel`、`chmod -R a+rX`）並重開機後不反灰、能打字、選單顯示學習暫停 |
| **P1** 搬家 | 全新虛擬機 | 搬完不做別的：不能用（系統還指著舊路徑）；對新路徑 `TISRegisterInputSource`＋啟用：能打字，但安全輸入期間只出英文、新開的 App 接不上、系統設定看不到；**登出再登入：全部正常**，container 沿用、學習檔雜湊不變、0 沙盒 deny。＝要重新登記＋登出一次，不用重開機 |
| **P3** Sparkle 更新 root 擁有的 bundle | 虛擬機，開發版 ID 的更新測試版（ad-hoc） | 「更新到 0.0.2」→ 系統管理員密碼框；第一次取消沒有錯誤、照常打字；第二次輸入密碼 → `authd` 記授權成功與 `ServiceManagement.daemons.modify`，更新完成；結果 `root:wheel`、全部可讀（zip 先 `chmod -R a+rX`）、container 沿用、0 deny |
| **P0** 維護者的 Mac | macOS 27.0.1，把裝著的 dry run 0.0.0 照 P1 的順序搬到 `/Library`、登出再登入 | 安全輸入期間不反灰、選單第一項「學習已暫停（安全輸入）」、打得出中文、**Caps Lock 切換正常**（重新登記發生在換位置之後，這次沒有觸發 installer-v2 §9 的問題） |
| 打包權限 | `release.yml` | sign 步驟先 `umask 077`（第 223 行）再 `tar -xf`（第 251 行），zip 裡的 bundle 是 0700／0600（實機與虛擬機都看到 `drwx------`）。在 `~/Library`（使用者擁有）無害；root 擁有時使用者讀不到、載不起來；Sparkle 以 root 換檔只改擁有者、不改權限（〔原始碼〕`SUPlainInstaller`、`SUFileManager`） |

還沒量、這片要量的：安裝程式在 hardened runtime 下用 `NSAppleScript` 取得管理員權限（§4 第 0 步）；第二個帳號（§4，只影響文件）；Developer ID 簽章下的 Sparkle 系統 domain 更新（照 app-sandbox 原本的規定留給 canary）。

## 2. 設計

### 2.1 安裝位置與誰能裝

| ID | 位置 | 說明 |
|---|---|---|
| 正式 `com.nyanako.inputmethod.shanjie` | **預設 `/Library/Input Methods/善解輸入法.app`**，`root:wheel`、目錄與執行檔 755、其他 644（`chmod -R a+rX`） | 和自然輸入法相同的擁有者與權限 |
| 正式，選「只裝在我的帳號」 | `~/Library/Input Methods/善解輸入法.app`（現在的路） | 給標準帳號、不想輸密碼的人；安全輸入時照舊會被系統停用 |
| 開發版 `….dev` | 只在 `~/Library`，規則一字不改 | `install-ime.sh` 的 root 階段拒絕開發版 ID |

- 一台 Mac 同一時間只留一份正式 ID：裝到 `/Library` 時移除帳號層那份（留成 `~/Library/Input Methods/.shanjie-previous`）；反過來選「只裝在我的帳號」時，`/Library` 那份已存在就停下來，告訴使用者先用 `sudo` 移除（指令印在畫面上），不自動刪。

### 2.2 `scripts/install-ime.sh` 拆成兩個階段

- **root 階段**（新，`install-ime.sh --system-files <src>`，必須以 root 執行）：只寫 `/Library/Input Methods` 底下的 `善解輸入法.app`、`.shanjie-previous`、暫存目錄三個路徑。複製到暫存 → `chown -R root:wheel` → `chmod -R a+rX` → 舊版改名 `.shanjie-previous` → 新版就位。只收正式 ID、而且 Developer ID 簽章（Team `2LJ882GPY8`）的 bundle；簽章檢查在呼叫 `sudo` 之前、在使用者程序裡也做一次。不讀 HOME（`sudo` 下 HOME 不是帳號的）。
- **使用者階段**（現有的那條，不用 root）：正式 ID 預設先呼叫 root 階段（終端機用 `sudo bash "$0" --system-files "$SRC"`），接著 `lsregister -u` 並移走帳號層的舊版（含舊名 `shanjie.app`）、`lsregister -f` 新位置、結束舊程序（樣式同時比對 `/Library` 與 `$HOME/Library` 兩個位置）、對新路徑 `TISRegisterInputSource` 並啟用（P1：搬家後一定要），最後提示「請登出再登入一次」。
- `--user`：正式 ID 走現在的帳號層路徑，行為和今天相同。
- 舊版還原指令（腳本印出的）對 `/Library` 要帶 `sudo`。
- 測試：`scripts/test-install-ime.sh` 加 `SHANJIE_INSTALL_SYSTEM_ROOT=<暫存目錄>`，在 files-only 模式下讓 root 階段寫暫存目錄、不呼叫 `chown`；CI 不跑真的 `sudo`。

### 2.3 安裝程式（`ShanjieInstaller`）

- 歡迎頁：主按鈕「安裝給這台 Mac」，說明一句「需要管理員密碼；安全輸入時也能用」；次要按鈕「只裝在我的帳號」，說明「不需要密碼；有程式開著安全輸入時，系統會停用善解」。取代現在的「只裝在你的帳號，不需要管理員密碼」。
- 主按鈕：在安裝程式自己的程序裡用 `NSAppleScript` 執行 `do shell script "<內附 install-ime.sh 的 root 階段>" with administrator privileges`（對話框掛安裝程式的名字）；使用者按取消就回到歡迎頁，不留任何改動。之後的使用者階段照現有流程在安裝程式程序裡做。
- `InstallerPlan.decide` 多一個輸入：`/Library` 那份在不在、版本。已在 `/Library` 的同版或新版照現有規則（只啟用、不降版）；帳號層有舊版、`/Library` 沒有時一律走「安裝」，並寫「會把善解改裝給整台 Mac；你帳號裡的那份會移除，學習資料與設定不受影響」。
- 完成頁多一句「請登出再登入一次，善解才會在所有 App 裡正常」（P1）。
- 不加任何 entitlement、不改簽章規則；需要 entitlement 才拿得到授權就是停止條件（§5）。

### 2.4 打包權限（不論位置都要修）

- `release.yml` sign 步驟：解開建置產物改用 `tar -xpf`，簽章前再 `chmod -R a+rX` 兩個 `.app`；安裝程式 zip 同樣處理。
- `verify()` 與 `scripts/check-app.sh` 加斷言：`find "$APP" ! -perm -o+r` 為空，目錄與執行檔 `! -perm -o+x` 為空。

### 2.5 Sparkle 與關於頁

- Sparkle 不改程式：bundle 或其上層不可寫時，它自己走系統 domain、跳管理員密碼框（P3 量到）。背景只檢查、不自動安裝（about-update §1），所以不會在背景卡在要授權的更新上。
- 關於頁：bundle 在 `/Library` 時加一行「更新需要管理員密碼」；在 `~/Library` 時加一行「目前只裝在你的帳號；有程式開著安全輸入時系統會停用善解。要改裝給整台 Mac，請下載安裝程式」＋連結。判斷只看 `Bundle.main.bundleURL` 的上層路徑。
- 0.4.x 經 Sparkle 更新上來的人仍在 `~/Library`（Sparkle 不搬位置），照常能用；要搬只能重跑安裝程式，所以發版說明與關於頁都要寫。

### 2.6 不動的部分

- Homebrew cask：程式不動，仍裝在帳號層；註解、README、網站寫清楚差別（`cp -pR` 保留來源擁有者、`brew uninstall` 碰到不可寫的上層會失敗，要乾淨進 `/Library` 只能 pkg，不在這片）。
- 核心、選字、學習、container 與移轉清單：位置無關（container 依 bundle ID）。`privacyGate` 已在沙盒裡量過正確（app-sandbox §4.3 B 第 4 項的更正）。
- 開發流程、`imeshot`、`make selftest-bundled`。

### 2.7 要跟著改的文件與對外說法（同一個 PR）

| 檔案 | 改什麼 |
|---|---|
| `site/index.html` 安裝段 | 安裝程式＝整台 Mac、要管理員密碼、安全輸入時也能用；Homebrew＝只在帳號、安全輸入時會被停用；換方式的刪除指令加 `sudo` 版本（部署前問維護者） |
| `README.md` 安裝段 | 同上，加解除安裝（`sudo rm -rf` 兩個路徑、各帳號刪 container） |
| `docs/contracts/s3c-installer.md` §5、`installer-v2.md` 歡迎列、`s3b.md` §4／§13.3 | 「不用 sudo、不寫系統目錄」改成「檔案階段以管理員權限只寫 `/Library/Input Methods` 的三個路徑；註冊在使用者程序」 |
| `docs/PLAN.md` R9、安全輸入條目 | 「執行不需要 root；安裝與更新需要管理員」；條目指向這份契約 |
| `docs/contracts/app-sandbox.md` §2.5、§2.8、§4.4、§5 | 退回加 `sudo`；§2.8 加「root 擁有的 bundle，更新需管理員密碼」；canary 從 `/Library` 跑 |
| `docs/contracts/about-update.md` §0.1、§3 | 信任模型加「Sparkle 以 root 換檔，換完仍以使用者身分執行」；驗收加密碼框與權限 |
| `docs/sandbox-restore.md` | `pkill` 樣式加 `/Library` |
| `packaging/Casks/shanjie.rb` | 註解：cask 只做帳號層 |
| `docs/releases/v0.5.0.md` | 安裝位置改變、要管理員密碼、安全輸入時不再反灰、既有使用者要重開安裝程式才會搬、登出一次、更新失敗就重下安裝程式 |
| `docs/research-log.md`、`docs/methodology.md` | 這片的量測；方法論記「`umask 077` 下 `tar -xf` 會把權限吃掉」 |

## 3. 安全

- **不多暴露資料**：密碼欄由系統強制 ASCII，善解收不到按鍵；背景程式持有安全輸入時，按鍵本來就會經過某個輸入法（蘋果注音或善解），差別只是哪一個。安全輸入期間學習暫停、不讀前文（`privacyGate`，沙盒裡量過），不寫日誌（R2），不連網。
- **root 擁有的 bundle**：使用者層程序（含被攻破後逃出沙盒的輸入法）不能改寫自己的 bundle 來持久化，比今天多一層（〔推論〕由檔案權限）。
- **app-sandbox 修訂二的 F1（`Installer.xpc` 不綁 host）不會升成 root**：Sparkle 依 host 路徑可不可寫決定 domain；攻擊者造得出的假 host 都在可寫位置，走的是使用者 domain、不跳密碼；要走系統 domain 得指向真的 `/Library` host，那時更新要過我們的 EdDSA 公鑰（〔推論〕，依 10-19 的安全審查機制與 Sparkle 原始碼；§4.5 的虛擬機驗證照原計畫做）。
- **安裝程式拿管理員權限**：只在使用者按主按鈕時、只跑內附腳本的 root 階段、只寫三個路徑；腳本路徑與參數都來自安裝程式自己的 bundle，不接受外部輸入。
- **持有 EdDSA 私鑰與 feed 寫入權的人**：今天就能讓所有使用者的輸入法換成任意程式（R10）；之後多了「以 root 換檔」，換完仍以使用者身分執行。R10 與 about-update §0.1 的信任模型照實加一句。
- 對外說法仍是「縱深防禦」；可以寫「程式檔由系統保護、更新需要管理員密碼」，不寫「root 擁有讓攻擊者碰不到」。

## 4. 驗收

0. **先量（實作前，虛擬機）**：ad-hoc 的安裝程式用 `NSAppleScript` 跑 `id -u`，對話框出現、回傳 0、標題是安裝程式的名字。不行就停（§5），改評估 `SMJobSubmit` 或 `.pkg`。
1. **CI**：`test-install-ime.sh` 的新情境（root 階段寫暫存目錄、只收正式 ID、拒絕開發版 ID 與 ad-hoc 正式 ID、`--user` 照舊）；`check-app.sh` 與 `verify()` 的權限斷言（突變：拿掉 `chmod`／改回 `tar -xf` 要讓斷言失敗）；安裝程式的 `InstallerPlan` 單元測試（`/Library` 在／不在 × 帳號層在／不在）。
2. **dry run**（main 觸發，事先問維護者）：zip 裡沒有讀不到的檔。
3. **虛擬機**（全新 clone，dry run 的安裝程式）：
   - 全新安裝：密碼框 → `/Library/Input Methods/善解輸入法.app` 是 `root:wheel`、全部可讀 → 登出再登入 → 系統設定加入 → 打字 → 背景持有安全輸入時不反灰、選單顯示學習暫停、打得出中文。
   - 移轉：0.4.7（`~/Library`）→ 安裝程式 → `/Library` 那份被啟動、`~/Library` 只剩 `.shanjie-previous`、container 裡的學習檔雜湊不變、登出一次、不重開機、0 deny。
   - 「只裝在我的帳號」：照舊裝在 `~/Library`；`/Library` 已有時停下並印出移除指令。
   - 取消密碼框：回到歡迎頁，`/Library` 沒有任何改動。
   - 退回：`sudo rm -rf` 兩個路徑 → `install-ime.sh --user` 裝回 → 能打字、學習資料還在。
   - 選做：第二個帳號登入後能加入、能打字、有自己的 container（只影響文件）。
4. **實機**（維護者的 Mac，接在 app-sandbox §4.3 B 之後）：用新的 dry run 安裝程式重裝一次（你的 Mac 現在已在 `/Library`，走「同版、只啟用」的路）；安全輸入、打中文、Caps Lock、關於頁「有新版本 0.4.7」（不按）。
5. **canary**：v0.5.0 → v0.5.1 從 `/Library` 經 Sparkle 更新：密碼框 → 成功 → container 還在 → 0 deny（app-sandbox §4.4 的 canary 條文照改）。

## 5. 停止條件、預算、回滾

- **停止條件**：安裝程式要任何 entitlement 才拿得到授權；`NSAppleScript` 在 hardened runtime 下拿不到授權（§4 第 0 步）；搬家要重開機才生效；Caps Lock 壞掉而慢速按鍵修不回來；虛擬機的 Sparkle 系統 domain 更新失敗而且不是權限問題。碰到就停，回報維護者；v0.5.0 可以退回只出沙盒（Sparkle 使用者本來就留在 `~/Library`）。
- **預算**：實作 1 次＋修正 1 次；executor 約兩天；審查與虛擬機驗收一天；維護者實機約 15 秒 × 3。
- **回滾**：revert 這片只改回安裝程式與腳本的預設值；已在 `/Library` 的使用者留在那裡，Sparkle 照常更新（程式不看路徑）。個人回到帳號層：`sudo rm -rf` 兩個路徑再 `--user` 安裝。

## 6. 範圍外

- `.pkg` 與 Homebrew 進 `/Library`（要 Developer ID Installer 憑證）。
- 「指出卡住安全輸入的程式」（PLAN 已記；這片之後安全輸入期間善解會一直顯示學習暫停，建議排下一小片）。
- 自動偵測標準帳號（先靠次要按鈕）。

## 7. 審查紀錄

| 審查 | 結果 | 處置 |
|---|---|---|
| plan-verifier（2026-10-20） | REVISE，8 項：沒有 security-reviewer 的紀錄；root 階段的信任邊界（腳本與來源 App 在使用者可寫的位置、`codesign -dv` 不驗封印、檢查與使用之間有空窗、`chmod -R a+rX` 不清寫入權與 setuid）；搬家時要強制重新登記，和 installer-v2 §9 的跳過規則衝突、要寫明換位置才註冊、同位置更新照舊跳過；移轉驗收在「搬了沒登記」的壞狀態也會過；`InstallerPlan.decide` 的決策表不完整（兩份都在、帳號層較新、重新安裝裝到哪）；§0 與實機步驟對不上；沒有凍結 tag；Sparkle 系統 domain 的停止條件對不到任何步驟 | 暫緩，重開時照改 |
| security-reviewer（2026-10-20） | P1-A：§3「F1 不會升成 root」不成立：Sparkle 2.10.0 的 `SPUUsesSystemDomainForBundlePath` 對「套件安裝」類型不看 host 路徑就走系統 domain，host 路徑與類型都由呼叫者給、不綁呼叫者，同 Team 簽章的程序都連得上（〔原始碼〕，未實測）；`/Library` 不新增這條路，但讓密碼框變常態。P1-B：root 階段要先複製到 root 擁有的暫存目錄、驗完整簽章與指定需求、清環境變數、用編進安裝程式的常數指令。P2-A：系統 domain 讓 Sparkle 以 root 執行的漏洞類別（GHSA-4v99、3x7w，2.9.6 修）變得相關，版本要當發版關卡。P2-B：安全輸入期間不再擋住按鍵，要照實寫，並要求所有會讀或留存文字的功能都受 `learningPause` 管。P2-C：`NSAppleScript` 的引號與環境、要在主執行緒。P3：`/Library/Input Methods` 的擁有者與權限要先斷言；權限斷言加群組／其他可寫與 setuid；還原與解除安裝指令要完整加引號 | 維護者同日決定讓使用者選安裝方式、保留密碼，並把沙盒外的獨立更新程式一起放進 v0.5.0（根本解，處理 P1-A）；P1-A 的系統 domain 變體另外併進 app-sandbox §4.5 的虛擬機驗證與給 Sparkle 的私下回報 |
