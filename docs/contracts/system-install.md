# 契約：安裝方式由使用者選，「這台 Mac」裝到 `/Library/Input Methods`（system-install）

狀態：**修訂一草稿**（2026-10-20）：security-reviewer 已審、處置在 §7，待 fresh `pilotfish:plan-verifier`。第一版兩份審查都是 REVISE（§7），這一版照它們與維護者同日的三個決定改寫：

1. 安裝方式**讓使用者自己選**：安裝程式並列兩個選項、**沒有預設**，各寫清楚功能與取捨；
2. 裝給整台 Mac 的更新**保留管理員密碼**；
3. **沙盒外的獨立更新程式一起進 v0.5.0**（`docs/contracts/standalone-updater.md`），處理安全審查的 P1-A。

和 App 沙盒（`app-sandbox.md`）、獨立更新程式一起以 v0.5.0 發佈。實作片稱 U2；`standalone-updater.md` 是 U1，合併順序 U1 先（§4.5）。

## 0. 為什麼、怎麼算做到

- **問題**：任何程式持有安全輸入時（密碼管理器、終端機的「加密的鍵盤輸入」、某些背景工具），macOS 會把裝在 `~/Library/Input Methods` 的善解反灰，使用者只能改用蘋果注音。2026-10-05 起維護者遇過多次（研究紀錄同日、PLAN「安全輸入時第三方輸入法反灰」）。
- **原因**（研究紀錄 2026-10-20，量到）：是安裝位置，不是子模式。同一個 Developer ID 版放在 `/Library/Input Methods`（`root:wheel`、所有人可讀）時，安全輸入期間不反灰、打得出中文，選單第一項是既有的「學習已暫停（安全輸入）」。系統為什麼這樣分沒有 Apple 文件，〔推論〕是使用者自己寫得進的位置不被信任。
- **代價**：`/Library` 要管理員密碼才能安裝與更新，而且只有管理員能裝。所以兩種都留，讓使用者選。
- **怎麼算做到**（和 §4 的步驟一一對應）：
  1. 虛擬機（§4.3）：全新安裝兩個選項都裝得起來；0.4.7 使用者用安裝程式選「這台 Mac」，搬到 `/Library`、登出一次後能打字，學習資料還在；背景持有安全輸入時不反灰、打得出中文。
  2. 維護者的 Mac（§4.4）：dry run 的安裝程式看得到兩個並列選項；選「這台 Mac」走「同版、只啟用」，再按「重新安裝」跳一次管理員密碼、成功；之後安全輸入期間不反灰、打得出中文、選單顯示「學習已暫停（安全輸入）」、Caps Lock 切換正常。
  3. canary 0.5.1 從 `/Library` 經善解更新成功（`standalone-updater.md` §4.4）。

## 1. 已經量到的（研究紀錄 2026-10-20）

| 探針 | 在哪裡 | 結果 |
|---|---|---|
| 位置對照 | tart 虛擬機（macOS 26.6.2），dry run Developer ID 版 | `~/Library` 反灰；搬到 `/Library`（`root:wheel`、`chmod -R a+rX`）並重開機後不反灰、能打字、選單顯示學習暫停 |
| **P1** 搬家 | 全新虛擬機 | 搬完不做別的：不能用（系統還指著舊路徑）；對新路徑 `TISRegisterInputSource`＋啟用：能打字，但安全輸入期間只出英文、新開的 App 接不上、系統設定看不到；**登出再登入：全部正常**，container 沿用、學習檔雜湊不變、0 沙盒 deny。＝要重新登記＋登出一次，不用重開機 |
| **P3** Sparkle 更新 root 擁有的 bundle | 虛擬機，開發版 ID 的更新測試版（ad-hoc） | 「更新到 0.0.2」→ 系統管理員密碼框；第一次取消沒有錯誤、照常打字；第二次輸入密碼 → 更新完成；結果 `root:wheel`、全部可讀（zip 先 `chmod -R a+rX`）、container 沿用、0 deny。善解更新從 `/Library` 走同一條路也過（`standalone-updater.md` §1.1 V3） |
| **P0** 維護者的 Mac | macOS 27.0.1，把裝著的 dry run 0.0.0 照 P1 的順序搬到 `/Library`、登出再登入 | 安全輸入期間不反灰、選單第一項「學習已暫停（安全輸入）」、打得出中文、**Caps Lock 切換正常**（重新登記發生在換位置之後，這次沒有觸發 installer-v2 §9 的問題；只量了一次） |
| 打包權限 | `release.yml` | sign 步驟先 `umask 077` 再 `tar -xf`，zip 裡的 bundle 是 0700／0600。在 `~/Library` 無害；root 擁有時使用者讀不到、載不起來；Sparkle 以 root 換檔只改擁有者、不改權限。修正放在 U1（`standalone-updater.md` §2.4），這片依賴它 |
| 程式啟用輸入法的系統詢問 | 虛擬機（macOS 26.6.2），P1 用的小工具呼叫 `TISEnableInputSource` | 系統跳出「Allow "<呼叫者>" to enable "Shanjie"?」，按允許才啟用。安裝程式的啟用步驟會不會跳、按「不允許」之後怎麼走，§4.3 量 |

還沒量、這片要量的：安裝程式在 hardened runtime 下用 `NSAppleScript` 取得管理員權限（§4.0）；第二個帳號（§4.3 選做，只影響文件）。

## 2. 設計

### 2.1 兩個選項

| | **這台 Mac** | **只有我的帳號** |
|---|---|---|
| 位置 | `/Library/Input Methods/善解輸入法.app`，`root:wheel`、目錄與執行檔 755、其他 644、沒有群組或其他人可寫、沒有 setuid／setgid | `~/Library/Input Methods/善解輸入法.app`（現在的路，行為不變） |
| 安全輸入 | 照常能用（學習暫停、不讀前文） | 系統會暫時停用善解 |
| 安裝與更新 | 安裝與每次更新各一次管理員密碼（更新由善解更新完成，`standalone-updater.md`） | 不要密碼、一鍵更新 |
| 誰能裝 | 管理員（標準帳號要請管理員輸入帳密） | 任何帳號 |
| 其他帳號 | 都能用，各自一個 container | 看不到 |

- 一台 Mac 同一時間只留一份正式 ID。選「這台 Mac」時，帳號層那份（含舊名 `shanjie.app`）移到 `~/Library/Input Methods/.shanjie-previous`；選「只有我的帳號」時，`/Library` 那份已存在就停下來，畫面印出完整加引號的 `sudo` 移除指令，不自動刪。
- 開發版 ID（`….dev`）只在 `~/Library`，規則一字不改；安裝程式本來就只裝正式 ID。
- `install-ime.sh` 不加系統層的模式（終端機使用者照舊裝帳號層）；只加一個防呆：正式 ID 要裝進 `~/Library`、而 `/Library/Input Methods/善解輸入法.app` 存在時，停下來並印出同樣的移除指令。安裝程式選「只有我的帳號」也是經過這一條。

### 2.2 決策表（`InstallerPlan`，純函式，單元測試逐列）

輸入：`/Library` 那份的版本 S（沒有／較舊／同版／較新，和內附的版本比）、帳號層有沒有正式 ID 的那份 U、使用者的選擇。「強制登記」＝不看 installer-v2 §9 的跳過規則，對 `/Library` 的路徑 `TISRegisterInputSource` 並啟用；「照舊」＝ installer-v2 §9 的規則（系統已認得就不碰）。

| 選擇 | S | U | 動作 | 密碼 | 登記 | 完成頁要求登出 |
|---|---|---|---|---|---|---|
| 這台 Mac | 沒有 | 沒有 | 安裝到 `/Library` | 要 | 強制 | 要 |
| 這台 Mac | 沒有 | 有 | 安裝到 `/Library`；帳號層那份移走 | 要 | 強制 | 要 |
| 這台 Mac | 較舊 | 沒有 | 更新 `/Library`，舊版留成 `/Library/Input Methods/.shanjie-previous` | 要 | 照舊 | 不要 |
| 這台 Mac | 較舊 | 有 | 同上；帳號層那份移走 | 要 | 強制 | 要 |
| 這台 Mac | 同版 | 沒有 | 只啟用；次要動作「重新安裝」＝上面「較舊、沒有」那一列 | 不要（重新安裝才要） | 照舊 | 不要 |
| 這台 Mac | 同版或較新 | 有 | 不換 `/Library`；帳號層那份移走 | 不要 | 強制 | 要 |
| 這台 Mac | 較新 | 沒有 | 只啟用，不降版 | 不要 | 照舊 | 不要 |
| 只有我的帳號 | 沒有 | 任意 | 現在的路（`decide(installed: 帳號層版本, bundled:)`，安裝／更新／只啟用／不降版） | 不要 | 照舊 | 照舊 |
| 只有我的帳號 | 有 | 任意 | 停下來，畫面說明「這台 Mac 已經裝了善解」並印出移除指令；不改任何東西 | — | — | — |

- 歡迎頁在 S 不是「沒有」時寫出目前的狀態（「已裝給這台 Mac，版本 X」）。
- 「強制登記」只在帳號層那份被移走或 `/Library` 原本沒有時發生，也就是**換了位置**；同位置的更新照 installer-v2 §9 跳過登記（避免 Caps Lock 的問題）。P0 量到換位置後的登記沒有觸發 Caps Lock 問題，但只有一次；壞掉是停止條件（§5）。
- 「帳號層那份移走」在 root 階段成功之後才做；使用者在密碼框按取消時什麼都不動（§2.3）。

### 2.3 root 階段（只在安裝程式裡；安全審查 P1-B、P2-C、P3）

- **常數指令**：root 階段是編進安裝程式（`ShanjieInstall`）的固定指令字串，**不執行 bundle 裡的任何腳本**（安裝程式與它的資源在使用者寫得到的位置）。唯一的變數是安裝程式自己解開的暫存路徑（`NSTemporaryDirectory()` 底下、安裝程式自己建的 UUID 資料夾）；組字串前先斷言它只含預期的字元，再用單引號跳脫。
- **執行**：`NSAppleScript` 的 `do shell script "<指令>" with administrator privileges`，在主執行緒呼叫。指令的結構固定是 `/usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin /bin/sh -c '<腳本>'`（`env -i` 只作用在它後面那一個指令，`umask 022` 是 shell 內建，所以腳本必須在 `sh -c` 裡）；腳本裡全部用絕對路徑。兩層跳脫（腳本裡的暫存路徑用 shell 單引號、整串放進 AppleScript 字串字面值時跳脫 `\` 與 `"`，指定需求裡的雙引號就在這一層）由同一個純函式負責，測試把組出的 AppleScript 來源還原回腳本與路徑、逐字比對（安全審查 P3-5）。使用者按取消（AppleScript 錯誤 −128）就回到歡迎頁。
- **步驟**（以 root；任何一步失敗就停、清掉 staging、不動現有的 bundle）：
  1. 斷言 `/Library/Input Methods` 是目錄、不是符號連結、擁有者 uid 0、群組與其他人都不可寫（群組不限定 wheel：出廠的群組 §4.3 a 在全新虛擬機量一次）；不存在就以 `root:wheel` 755 建立；
  2. `mktemp -d "/Library/Input Methods/.shanjie-staging.XXXXXX"`（root 擁有、只有 root 能寫）；
  3. `ditto --noqtn --noacl <暫存路徑>/善解輸入法.app <staging>/善解輸入法.app`：從使用者的暫存**複製進 root 擁有的目錄**，不帶 ACL（`ditto` 預設會保留 ACL，安全審查 P1-1）；之後使用者改不到它；
  4. `chown -R root:wheel`、`chmod -RN`（清掉任何 ACL）、`chmod -R u+rwX,go+rX,go-w,ug-s`；
  5. **在 root 擁有的副本上驗簽章**：`codesign --verify --strict --deep -R='anchor apple generic and identifier "com.nyanako.inputmethod.shanjie" and certificate leaf[subject.OU] = "2LJ882GPY8"'`（驗的是完整封印，不是 `codesign -dv` 的讀資訊）；
  6. **斷言中繼資料與版本**：整棵樹沒有 ACL（`/bin/ls -leR` 的輸出沒有 ACL 列）；副本的 `CFBundleVersion`（`/usr/bin/plutil -extract CFBundleVersion raw`）**等於編進安裝程式執行檔的常數**（不讀安裝程式的 Info.plist），而且不低於 `/Library` 現有那份的 `CFBundleVersion`（「重新安裝」允許相同）。這兩條擋住「趁使用者輸入密碼的幾秒，同使用者的程式對解開的檔加 ACL」與「把暫存換成任何一個 Developer ID 簽過的舊版」（安全審查 P1-1、P2-2）；
  7. 目的地若是符號連結就停；舊的 `.shanjie-previous` 刪掉，現有的 `善解輸入法.app` 改名成 `.shanjie-previous`，副本 `mv` 到 `善解輸入法.app`（同一個檔案系統，改名是原子的）；刪掉 staging。
- 呼叫 root 階段之前，安裝程式在自己的程序裡對解開的 bundle 先驗一次同樣的指定需求，失敗就不跳密碼框、直接顯示錯誤（給使用者看的提早失敗，不是安全檢查；安全檢查是第 5 步）。
- 不讀 HOME、不碰任何帳號的檔案；只寫 `/Library/Input Methods` 底下的 `善解輸入法.app`、`.shanjie-previous`、`.shanjie-staging.*` 三種路徑。
- 不加任何 entitlement、不改簽章規則；需要 entitlement 才拿得到授權就是停止條件（§5）。
- **測試**：指令由純函式組出，參數是根目錄、擁有者與指定需求（正式值是常數）。CI 以一般使用者跑組出的指令：根目錄是暫存目錄、擁有者是自己；ad-hoc 的測試 bundle 用 `identifier` 需求時要成功，用正式需求時要失敗而且目的地沒變、staging 清掉；開發版 ID 被拒；`/Library/Input Methods` 的替身被設成其他人可寫時要停；**來源帶 ACL 時結果沒有 ACL**（`chmod +a` 不需要 root）；**版本低於常數或低於目的地現有那份時被拒、目的地不變**；AppleScript 來源的還原比對（含路徑裡的空格與全形字）。

### 2.4 使用者階段與登記

- root 階段成功之後，在安裝程式程序裡、以使用者身分：
  1. 帳號層有正式 ID 那份時（含舊名 `shanjie.app`）：`lsregister -u` 它、移到 `~/Library/Input Methods/.shanjie-previous`（原有的 `.shanjie-previous` 先刪）；
  2. `lsregister -f` `/Library` 那份；
  3. 結束舊程序：樣式同時比對 `/Library/Input Methods/` 與家目錄的 `Library/Input Methods/` 兩個位置，只結束自己帳號的；
  4. 登記照 §2.2 的「強制」或「照舊」；強制時 `Registration.decide` 多一個輸入 `moved`，`moved` 為真一律走完整流程（註冊、啟用、確認）。
- 這幾步重用 `install-ime.sh` 已有的搬移、`lsregister` 與結束程序的邏輯（加一個不複製檔案、只做這四步之前三步的模式），不另寫一份。
- 啟用時如果系統問「Allow "安裝善解輸入法" to enable "Shanjie"?」（§1 最後一列），啟用步驟的畫面寫「請按『允許』」；使用者按了「不允許」，照 installer-v2 §9.2 的輪詢逾時後引導到系統設定加入。
- 完成頁：§2.2 要求登出的列，寫「請登出再登入一次，善解才會在所有 App 裡正常」（P1）。

### 2.5 安裝程式的畫面

- **歡迎頁**：兩個同樣大小的選項並排（或上下），**沒有預設按鈕**（Return 不綁任何一個，兩個都能用 Tab 選到）；取代現在的「只裝在你的帳號，不需要管理員密碼」。

  | | 這台 Mac | 只有我的帳號 |
  |---|---|---|
  | 說明 | 這台 Mac 的每個帳號都能用。有程式開著安全輸入時（密碼管理器、終端機的「加密的鍵盤輸入」），善解照常能用。 | 不需要密碼，一鍵更新。有程式開著安全輸入時，系統會暫時停用善解。 |
  | 代價 | 安裝和每次更新都要輸入一次管理員密碼。 | 其他帳號看不到善解。 |
  | 按鈕 | 安裝到這台 Mac | 只裝在我的帳號 |
  | 英文說明 | Every account on this Mac can use it. Keeps working while another app holds Secure Keyboard Entry. | No password, one-click updates. The system disables Shanjie while another app holds Secure Keyboard Entry. |
  | 英文代價 | Installing and every update ask for an administrator password once. | Other accounts do not see Shanjie. |
  | 英文按鈕 | Install for This Mac | My Account Only |

- 下方「詳細資訊」（展開才看得到）：「這台 Mac」把善解放進系統的輸入法資料夾，由系統保護檔案；之後更新時，善解更新會跳出系統的密碼框，文字是「善解更新 wants permission to update 善解輸入法.」，只在你按下關於頁的「更新」之後出現。選「這台 Mac」時，有程式開著安全輸入的期間，你打的字會經過善解（和平常一樣：學習暫停、不讀前文、不記錄）；終端機開著「加密的鍵盤輸入」時，`sudo` 的密碼提示也一樣會經過善解。「只有我的帳號」就是之前的安裝方式。兩種之間可以重新打開安裝程式切換，學習資料與設定不受影響（container 依 bundle ID）。不是管理員的帳號，請選「只有我的帳號」，或請管理員安裝。
- 「這台 Mac」按下後，系統的管理員密碼框掛安裝程式的名字；取消就回到歡迎頁。
- `--render-steps` 多畫：歡迎頁（S 沒有、S 已有兩種）、「只有我的帳號」被擋下的畫面、要求登出的完成頁。文字與版面照外觀規則由維護者看圖逐輪定。

### 2.6 關於頁

- 依 `Bundle.main.bundleURL` 的上層是 `/Library/Input Methods` 還是家目錄的，各多一段說明，文字在 `standalone-updater.md` §2.3（那片實作；這片只核對文字和安裝程式的說法一致）。

### 2.7 不動的部分

- Homebrew cask：程式不動，仍裝在帳號層；註解、README、網站寫清楚差別（cask 只做帳號層；`/Library` 用安裝程式）。brew 裝的時候 `/Library` 已有一份會變成兩份，cask 的說明寫一句，不擋。
- 核心、選字、學習、container 與移轉清單：位置無關。`privacyGate` 在沙盒裡量過正確（app-sandbox §4.3 B 第 4 項的更正）。
- 開發流程、`imeshot`、`make selftest-bundled`、開發版的 `install-ime.sh`。

### 2.8 要跟著改的文件與對外說法（同一個 PR）

| 檔案 | 改什麼 |
|---|---|
| `site/index.html` 安裝段 | 安裝程式有兩個選項，各一行取捨；Homebrew＝只在帳號；換方式的刪除指令加 `sudo` 版本（部署前問維護者） |
| `README.md` 安裝段 | 同上，加解除安裝（`/Library`：`sudo rm -rf` 兩個路徑，完整加引號；各帳號刪 container、在系統設定移除） |
| `docs/contracts/s3c-installer.md` §5、`installer-v2.md` 歡迎列與 §9、`s3b.md` §4／§13.3 | 「不用 sudo、不寫系統目錄」改成「選『這台 Mac』時，root 階段以常數指令只寫 `/Library/Input Methods` 的三種路徑；註冊在使用者程序」；§9 加「換位置時強制登記」 |
| `docs/PLAN.md` R9、安全輸入條目 | 「執行不需要 root；選『這台 Mac』時，安裝與更新需要管理員」；條目指向這份契約 |
| `docs/contracts/app-sandbox.md` §2.5 | 退回與卸載加 `sudo` 版本 |
| `docs/sandbox-restore.md` | `pkill` 樣式加 `/Library` |
| `packaging/Casks/shanjie.rb` | 註解：cask 只做帳號層 |
| `docs/releases/v0.5.0.md` | 安裝程式多了「這台 Mac」：安全輸入時不再反灰、安裝與每次更新一次管理員密碼；自動更新上來的人仍在自己的帳號裡，要搬就重新打開安裝程式；搬完要登出一次 |
| `docs/research-log.md`、`docs/methodology.md` | 這片的量測；方法論記「`umask 077` 下 `tar -xf` 會把權限吃掉」 |

## 3. 安全

- **安全輸入期間，按鍵會經過善解**（安全審查 P2-B，照實寫）：之前善解被反灰，背景程式持有安全輸入時使用者改用蘋果注音，按鍵經過的是蘋果注音；選「這台 Mac」之後經過的是善解。〔沒量〕系統的密碼欄（`NSSecureTextField`）應該會強制 ASCII、不經輸入法；但**不是密碼欄的地方不受這條保護**：例如終端機開著「加密的鍵盤輸入」時，`sudo` 的密碼提示只是一般文字，按鍵會照常經過善解（英數直通）。安裝程式的「詳細資訊」照實寫（§2.5）。善解在安全輸入期間：學習暫停（每次可能學習的核心呼叫前都送 `setLearning(false)`）、不讀前文（`leftContext` 只在沒暫停時讀），兩者都由同一個 `learningPause` 決定；不寫日誌（R2）。網路只有每日的更新探測，和按鍵無關。
- **規則**：現在與之後任何會讀取或留存使用者文字的功能（例如 App 計數器、「回報這句」、前文回傳），都要經過 `learningPause`，在它的契約裡寫明。這片之後安全輸入期間善解會一直顯示學習暫停，「指出卡住安全輸入的程式」建議排下一小片（§6）。
- **root 階段**（P1-B、P2-C、P3；修訂一的安全審查 P1-1、P2-2、P3-5、P3-6）：§2.3。重點：不執行使用者位置的腳本；先複製到 root 擁有的目錄（不帶 ACL）、再在那裡驗完整簽章與指定需求、斷言沒有 ACL、版本等於編進安裝程式的常數而且不降版，程式碼與中繼資料都沒有檢查與使用之間的空窗；清環境變數、絕對路徑、指令結構固定；權限清掉群組與其他人的寫入與 setuid／setgid；上層的擁有者、權限、符號連結先斷言。
- **root 擁有的 bundle**：使用者層程序（含被攻破的輸入法）改不了自己的 bundle 來持久化，比帳號層多一層。
- **更新**：由善解更新完成（`standalone-updater.md` §3）。善解只在使用者按下關於頁的「更新」、而且有新版時才會出現系統密碼框；安裝程式的說明與關於頁照實寫，讓使用者認得真的框。被攻破的輸入法自己畫的假密碼框擋不住，和任何 App 一樣。
- **持有 EdDSA 私鑰與 feed 寫入權的人**：多了「以 root 換檔」，換完仍以使用者身分執行；善解更新只接受 `application` 類型。R10 與 about-update §0.1 照實加一句（U1 的 PR）。
- **Sparkle 版本是發版關卡**（P2-A）：`standalone-updater.md` §3。
- **對外說法**：可以寫「程式檔由系統保護、更新需要管理員密碼」；不寫「root 擁有讓攻擊者碰不到」。

## 4. 驗收

### 4.0 先量（實作前，虛擬機）

- ad-hoc 簽章、hardened runtime 的最小安裝程式用 `NSAppleScript` 跑 `id -u`：密碼框出現、回傳 0、框上是安裝程式的名字。不行就停（§5）。

### 4.1 CI

- `InstallerPlan` 的單元測試：§2.2 每一列一個案例；`Registration.decide` 加 `moved` 的案例（`moved` 為真時不跳過）。突變：拿掉「帳號層移走 → 強制登記」那條要讓測試失敗。
- §2.3 的 root 指令測試（成功、正式需求下失敗且目的地不變、開發版 ID 被拒、上層可寫時停）。
- `test-install-ime.sh`：`/Library` 那份存在時，正式 ID 裝帳號層要停下並印出移除指令；新的「不複製、只搬移與登記前三步」模式的情境。
- 權限斷言在 U1（`standalone-updater.md` §2.4）。

### 4.2 dry run

- main 觸發，事先問維護者；在 U1、U2 都合併之後（§4.5）。zip 裡沒有讀不到或群組可寫的檔（`verify()`）。

### 4.3 虛擬機（全新 clone，dry run 的安裝程式）

| # | 情境 | 斷言 |
|---|---|---|
| a | 全新安裝「這台 Mac」 | 安裝前記下出廠的 `/Library/Input Methods` 擁有者、群組與權限（§2.3 第 1 步的條件）；密碼框 → `/Library/Input Methods/善解輸入法.app` 是 `root:wheel`、全部可讀、沒有群組可寫；記下有沒有「Allow … to enable "Shanjie"?」詢問與按下後的流程 → 完成頁要求登出 → 登出再登入 → 執行中的善解程序路徑在 `/Library`（`ps -o comm=`）→ 新開的文字編輯能打中文 → 背景持有安全輸入時不反灰、選單顯示學習暫停、打得出中文 |
| b | 取消密碼框 | 回到歡迎頁；`/Library/Input Methods` 沒有任何新檔（含 staging） |
| c | 移轉：0.4.7（`~/Library`）→ 安裝程式選「這台 Mac」 | 帳號層只剩 `.shanjie-previous`；安裝程式回報走了強制登記（不是跳過）；登出一次、不重開機；登入後程序路徑在 `/Library`、新開的 App 能打字、系統設定看得到善解；container 裡的學習檔雜湊不變；0 沙盒拒絕。「搬了沒登記」的壞狀態在「程序路徑」與「新開的 App 能打字」兩條會失敗（P1） |
| d | 「只有我的帳號」，`/Library` 已有 | 停下並印出移除指令；什麼都沒改 |
| e | 「只有我的帳號」，全新 | 裝在 `~/Library`，行為和 v0.4.7 的安裝程式相同 |
| f | 「這台 Mac」，`/Library` 同版、按「重新安裝」 | 密碼框 → 成功；舊的那份在 `/Library/Input Methods/.shanjie-previous`；登記跳過、不要求登出 |
| g | 退回 | 照 README 的 `sudo rm -rf` 兩個路徑 → 安裝程式選「只有我的帳號」→ 能打字、學習資料還在 |
| h | 選做：第二個帳號 | 能加入、能打字、有自己的 container（只影響文件） |

### 4.4 維護者的 Mac（接在 app-sandbox §4.3 B 之後；每步 15 秒級）

- 維護者的 Mac 現在是 `/Library` 的 dry run 0.0.0（P0 搬過去的）。用新的 dry run 安裝程式：歡迎頁寫「已裝給這台 Mac」、兩個選項並列 → 選「這台 Mac」走「同版、只啟用」→ 再按「重新安裝」→ 密碼框（框上是安裝程式的名字）→ 成功、不要求登出。之後：背景持有安全輸入時不反灰、打得出中文、選單顯示學習暫停；Caps Lock 切換正常；關於頁看得到 `/Library` 那段說明（善解更新那邊的步驟在 `standalone-updater.md` §4.3）。

### 4.5 順序與 tag

- U1（`standalone-updater.md`）先合併，這片（U2）後合併；之後 dry run（§4.2）→ 虛擬機（§4.3、`standalone-updater.md` §4.2）→ 維護者的 Mac（§4.4、`standalone-updater.md` §4.3）。
- 從 U1 合併到上面全部通過之前，main 不推任何 tag；其他片照常合併、一起等。全部通過後才推 v0.5.0（照例當下問維護者）。canary 在 `standalone-updater.md` §4.4。

## 5. 停止條件、預算、回滾

| 停止條件 | 在哪一步看到 |
|---|---|
| `NSAppleScript` 在 hardened runtime 下拿不到授權，或要任何 entitlement 才拿得到 | §4.0；改評估 `SMJobSubmit` 或 `.pkg` 前先問維護者 |
| 搬家要重開機才生效 | §4.3 c |
| 換位置後的強制登記讓 Caps Lock 壞掉、而慢速按鍵修不回來 | §4.4（虛擬機測不了 Caps Lock） |
| Sparkle／善解更新的系統 domain 更新失敗而且不是權限問題 | `standalone-updater.md` §4.2 V3、canary |
| root 階段在正式需求下接受了不合格的 bundle，或取消後 `/Library` 有殘留 | §4.1、§4.3 b |

- 碰到就停，回報維護者。時間不夠或停在這片時，v0.5.0 可以只出沙盒＋善解更新（U1），`/Library` 進 0.5.x；不砍 U1。
- **預算**：`pilotfish:security-executor` 實作 1 次＋修正 1 次（root 階段是信任邊界）；審查與虛擬機一天；維護者實機約 15 秒 × 3。
- **限制**：agent 不安裝、不啟動 App、不呼叫 TIS 或 lsregister、不在 .app 裡執行任何東西、不碰 `~/Library`、`/Library` 與鑰匙圈、不跑 `sudo`、不推 tag。
- **回滾**：發佈前 revert 這一片，安裝程式回到只有帳號層；已在 `/Library` 的人（虛擬機、維護者）照 §4.3 g 退回。發佈後：已在 `/Library` 的使用者留在那裡，善解更新照常更新（程式不看路徑，只看 Sparkle 自己的 domain 判斷）；要回到帳號層照 README。

## 6. 範圍外

- `.pkg` 與 Homebrew 進 `/Library`（要 Developer ID Installer 憑證）。
- 「指出卡住安全輸入的程式」（PLAN 已記，建議排下一小片）。
- 自動偵測標準帳號（先靠說明文字）。
- 經 Sparkle 搬位置（Sparkle 只原地換檔；搬位置只走安裝程式）。

## 7. 審查紀錄

| 審查 | 結果 | 處置 |
|---|---|---|
| plan-verifier（2026-10-20，第一版） | REVISE，8 項：(1) 沒有 security-reviewer 的紀錄；(2) root 階段的信任邊界（腳本與來源 App 在使用者可寫的位置、`codesign -dv` 不驗封印、檢查與使用之間有空窗、`chmod -R a+rX` 不清寫入權與 setuid）；(3) 搬家時要強制重新登記，和 installer-v2 §9 的跳過規則衝突、要寫明換位置才註冊、同位置更新照舊跳過；(4) 移轉驗收在「搬了沒登記」的壞狀態也會過；(5) `InstallerPlan.decide` 的決策表不完整（兩份都在、帳號層較新、重新安裝裝到哪）；(6) §0 與實機步驟對不上；(7) 沒有凍結 tag；(8) Sparkle 系統 domain 的停止條件對不到任何步驟 | 修訂一全部 FIX：(1) 下一列；(2) §2.3；(3) §2.2 的登記欄、§2.4 第 4 步；(4) §4.3 c；(5) §2.2；(6) §0 改成對應 §4 的三項；(7) §4.5；(8) §5 表格 |
| security-reviewer（2026-10-20，第一版） | P1-A：§3「F1 不會升成 root」不成立：Sparkle 2.10.0 對「套件安裝」類型不看 host 路徑就走系統 domain，host 路徑與類型都由呼叫者給、不綁呼叫者（〔原始碼〕，未實測）；`/Library` 不新增這條路，但讓密碼框變常態。P1-B：root 階段要先複製到 root 擁有的暫存目錄、驗完整簽章與指定需求、清環境變數、用編進安裝程式的常數指令。P2-A：系統 domain 讓 Sparkle 以 root 執行的漏洞類別（2.9.6 修）變得相關，版本要當發版關卡。P2-B：安全輸入期間不再擋住按鍵，要照實寫，並要求所有會讀或留存文字的功能都受 `learningPause` 管。P2-C：`NSAppleScript` 的引號與環境、要在主執行緒。P3：`/Library/Input Methods` 的擁有者與權限要先斷言；權限斷言加群組／其他可寫與 setuid；還原與解除安裝指令要完整加引號 | P1-A：維護者決定把獨立更新程式放進 v0.5.0（`standalone-updater.md` §3）；本文 §3 刪掉那句。P1-B、P2-C、P3：§2.3、§2.8 的 README 列。P2-A：`standalone-updater.md` §3。P2-B：§3 前兩點 |
| security-reviewer（2026-10-20，修訂一） | P1-1：root 階段的 `ditto` 預設保留 ACL，`chown`／`chmod` 不清、`codesign` 不看；同使用者的程式趁輸入密碼的幾秒對解開的檔加 ACL，`/Library` 那份就變成這個使用者改得動、所有帳號都會載入。P2-2：指定需求擋不住任何一個 Developer ID 簽過的舊版，root 階段沒有版本檢查（降版到沒有沙盒的 0.4.7）。P2-B（第一版）只部分關：歡迎頁沒告訴使用者安全輸入期間按鍵會經過善解；終端機 `sudo` 提示不是密碼欄。P2-C 部分關：`env -i` 包在哪一層、AppleScript 跳脫沒寫死（P3-5）。P3-6：上層群組限定 wheel 可能太嚴（fail closed）。P3-7：「密碼欄強制 ASCII」沒量 | 全部 FIX：P1-1 → §2.3 第 3、4、6 步（`--noacl`、`chmod -RN`、斷言沒有 ACL）與 CI 案例；P2-2 → §2.3 第 6 步（版本等於編進執行檔的常數、不低於現有那份）與 CI 案例；P2-B → §2.5 詳細資訊、§3 第一點；P3-5 → §2.3 執行；P3-6 → §2.3 第 1 步、§4.3 a；P3-7 → §3 第一點標〔沒量〕 |
