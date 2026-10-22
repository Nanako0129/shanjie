# 契約：沙盒外的獨立更新程式「善解更新」（standalone-updater）

狀態：**維護者核准**（2026-10-20）：security-reviewer、兩次 plan-verifier 與一次收尾審查，處置都在 §8；收尾審查之後的最小修改由維護者核准。和 `system-install.md`（安裝方式由使用者選）、`app-sandbox.md`（修訂三）一起以 v0.5.0 發佈。

維護者的決定（2026-10-20，選擇題）：沙盒外的獨立更新程式（app-sandbox §6 第 4 項的根本解，原排在 0c 開關 B 片）一起進 v0.5.0；架構採研究顧問分析的選項 A；先在虛擬機跑 V1–V5 再寫契約（§1.1，都量完了）。

## 0. 要做到什麼、怎麼看到

- **問題**：app-sandbox 修訂二的 F1 與 system-install 安全審查的 P1-A。輸入法在沙盒裡，但為了一鍵更新，它帶著 Sparkle 的 `Installer.xpc` 和兩個 mach-lookup 例外（`<ID>-spks`、`<ID>-spki`）。Sparkle 2.10.0 的安裝服務不綁呼叫者：要更新哪個 bundle、用哪種安裝類型都由呼叫者給（〔原始碼〕`SUInstallerLauncher.m`）。所以被攻破的沙盒輸入法理論上可以借它在沙盒外執行程式；選「套件安裝」類型時還會直接走系統 domain、以善解的名義跳管理員密碼框。裝到 `/Library` 之後，「善解要管理員密碼」變成常態，假冒更容易成功。
- **做法**：輸入法 bundle 裡放一個**不在沙盒、內嵌 Sparkle 的小 App**：`Contents/Helpers/善解更新.app`。它只更新包著自己的那份輸入法，而且那份必須在兩個允許的安裝路徑之一；它不吃任何呼叫者的輸入。輸入法拿掉 XPC 服務與兩個 mach-lookup 例外，entitlements 從四個變三個；使用者在關於頁按「更新到 X」時，輸入法只做一件事：啟動善解更新。
- **怎麼算做到**（誰、在哪裡看到）：
  1. 維護者在自己的 Mac 用 v0.5.0 的 dry run 安裝程式重裝後，關於頁按「更新到 0.4.7」：設定視窗關掉、「善解更新」出現在最前面、顯示 Sparkle 的 0.4.7 對話框；按取消（裝下去會變成沒有沙盒的 0.4.7）。
  2. canary：v0.5.0 → v0.5.1 兩條都成功：維護者的 Mac（`/Library`，系統 domain，一次管理員密碼）與虛擬機裡的正式版（`~/Library`，使用者 domain，不問密碼）。兩條都要：container 裡的學習資料還在、`log` 沒有 §4.2 允許清單以外的沙盒拒絕。

## 1. 已經量到的

### 1.1 虛擬機 V1–V5（2026-10-20）

tart 虛擬機（macOS 26.6.2），原型分支 `feat/standalone-updater`（`89c9e43a`），開發版 ID 的更新測試版（ad-hoc、`ShanjieUpdateBuild=test`、本機 feed、測試金鑰），照 app-sandbox §4.3 A 的做法。一台虛擬機依序走完：A0（沙盒之前的 0.0.1，等於現在的 0.4.x 使用者）→ B（0.0.2，新配置）→ C（0.0.3，新配置）。

| # | 情境 | 結果 |
|---|---|---|
| V4 | A0 經自己的 Sparkle（程序內）更新到 B | 過：B 接手、資料搬進 container。0.4.7 → 0.5.0 走的就是這條 |
| V1 | B 的關於頁「更新到 0.0.3」→ 沙盒輸入法用 `NSWorkspace.openApplication` 啟動 `Contents/Helpers` 裡的善解更新 | 啟得來，沒有沙盒拒絕。**但它的視窗被我們的設定視窗擋住**：設定視窗是 `.floating`（`SettingsWindow.swift` 的 `level`，為了讓 agent App 的視窗看得到），蓋在一般 App 的視窗上面。關掉設定視窗後就看得到。修法見 §2.3 |
| V2 | 帳號層（`~/Library`）B → C，使用者 domain | 過：不問密碼、C 接手、container 沿用、輸入法被結束並重新啟動。`log` 有 **1 筆沙盒拒絕**：輸入法程序裡的 Sparkle（只做探測的那份）查 `<ID>-spks` 被擋（`deny(1) mach-lookup ….dev-spks`）。探測照常顯示新版本、更新照常完成。處置見 §2.2。維護者的回報裡沒有提到「存取其他 App 的資料」之類的詢問；§4.2 重跑時明確看一次 |
| V5 | 把輸入法 bundle 複製到桌面，打開副本裡的善解更新 | 過：立刻結束、沒有視窗（路徑檢查） |
| V3 | B 以 root 放在 `/Library/Input Methods/善解（開發版）.app`（`root:wheel`、全部可讀）、登出再登入，再更新到 C | 過：系統的管理員密碼框，文字是「善解更新 wants permission to update 善解（開發版）.」；輸入密碼後 C 是 `root:wheel`、全部可讀，container 沿用，0 沙盒拒絕；`authd` 記的授權名稱是 `<更新程式 ID>.<輸入法 ID>.sparkle2-auth`（和修訂三從 `Installer.xpc` 走的那條不同，舊的那條留著無害） |

沒量到的：Developer ID 簽章下的兩條 domain：系統 domain 第一次是發佈後維護者的 Mac 從 dry run 的 0.0.0 更新到 0.5.0（§4.4，那時已是新配置），使用者 domain 第一次是 canary 的虛擬機那條；巢狀 App 的公證（dry run 會跑）；macOS 27 上的系統 domain（就是維護者的 Mac 那一次）。

### 1.2 依據（原始碼與文件）

| # | 事實 | 來源 |
|---|---|---|
| S1 | `SPUUpdater(hostBundle:applicationBundle:userDriver:delegate:)`：host 是要更新的 bundle，application 是要等它結束再重新啟動的 App，兩者可以不是自己。從另一個程序更新固定路徑的 bundle 是 Sparkle 寫明的用法（`sparkle-cli` 就是這樣） | 〔原始碼〕Sparkle 2.10.0 `SPUUpdater.h`、`sparkle-cli` |
| S2 | `SUPublicEDKey`、`SURequireSignedFeed`、`SUVerifyUpdateBeforeExtraction` 只從 **host 的 Info.plist** 讀；`SUEnableInstallerLauncherService` 這類 XPC 開關從**執行中的主 bundle** 讀。**`SUFeedURL` 不一樣**：host 不是主 bundle 時，Sparkle 用 host 的 bundle ID 開一個 user defaults（`initWithSuiteName:`），先讀 defaults、沒有才讀 Info.plist，而「defaults 裡有 `SUFeedURL`」的警告只在更新主 bundle 時才出現。所以 feed 網址可以被 host 網域的偏好設定蓋掉（安全審查 2026-10-20 P2-1），要由 delegate 釘住（§2.1） | 〔原始碼〕`SUHost.m`、`SPUUpdater.m` |
| S3 | 主 bundle 的 `SUEnableInstallerLauncherService` 是 false 時，安裝端在自己程序內執行、不經 XPC。善解更新不在沙盒，所以不需要任何 XPC 服務或 mach-lookup 例外；輸入法把它設成 false、刪掉 XPC 服務，照樣能做探測 | 〔原始碼〕`SPUInstallerDriver.m`、`SPUUpdater.m`；〔量到〕V2 |
| S4 | domain：非 root 時，「套件安裝」類型直接走系統 domain；其他類型看 host 與上層可不可寫。授權提示字串由 launcher 就地組出，host 不是主 bundle 時是「`<主 bundle 名> wants permission to update <host 名>.`」，不在地化 | 〔原始碼〕`SUInstallerLauncher.m`；〔量到〕V3 |
| S5 | `Autoupdate` 以 root 執行時：下載檔搬進只有 root 能讀的唯一目錄、隨機檔名、確認是一般檔；檢查已安裝的 host 的 bundle ID；新版的 `CFBundleVersion` 低於 host 時 `SUPlainInstaller` 中止（擋降版）；EdDSA 公鑰取自 host 的 Info.plist（`/Library` 那份是 root 擁有的）。「新 bundle 的 ID 要等於 host」這一條在 2.10.0 的原始碼裡**沒有找到**，不當成保證 | 〔原始碼〕`AppInstaller.m`、`SUPlainInstaller.m`（2.9.6 的修補） |
| S6 | 主 bundle 的 `SUEnableInstallerLauncherService` 是 false 時，`SPUInstallerDriver` 在**自己的程序裡**建 `SUInstallerLauncher`，它用 `SMJobSubmit` 送出 `Autoupdate`，本身沒有沙盒檢查。所以輸入法程序裡只要還有 Sparkle，就有一份 launcher 可以叫；F1 關不關，取決於沙盒擋不擋這個送出（沒量，§4.2 V7） | 〔原始碼〕`SPUInstallerDriver.m`、`SUInstallerLauncher.m` |
| A1 | 沙盒程序用 `NSWorkspace.OpenConfiguration.arguments` 與 `environment` 傳的參數與環境變數，系統一律忽略 | Apple 文件（兩頁都寫了） |

## 2. 設計

### 2.1 善解更新（新 target `ShanjieUpdater`，純函式在 `UpdateHost`）

| 項目 | 規格 |
|---|---|
| 位置 | `<輸入法>.app/Contents/Helpers/善解更新.app`，跟著輸入法一起簽章、公證、安裝、更新 |
| bundle ID | `<輸入法 ID>.updater`（正式 `com.nyanako.inputmethod.shanjie.updater`、開發版 `….dev.updater`） |
| Info.plist | 一般 App（不設 `LSUIElement`，更新期間 Dock 會短暫出現圖示）；`LSMinimumSystemVersion` 同輸入法；**沒有** `SUFeedURL`、`SUPublicEDKey`、任何 `SUEnable*` 鍵 |
| 沙盒、entitlements | 都沒有；Developer ID 版是 hardened runtime |
| host | `Bundle.main.bundleURL` 往上三層取 `realpath`，必須**完全等於** `/Library/Input Methods/<名稱>` 或 `<家目錄>/Library/Input Methods/<名稱>`，名稱是 `善解輸入法.app` 或 `善解（開發版）.app`；家目錄用 `getpwuid`（不用 `$HOME`）。不符就結束碼 1、不開視窗 |
| host 的組建類型 | 讀 host 的 `ShanjieUpdateBuild`：`release` → host 要通過指定需求 `anchor apple generic and identifier "com.nyanako.inputmethod.shanjie" and certificate 1[field.1.2.840.113635.100.6.2.6] exists and certificate leaf[field.1.2.840.113635.100.6.1.13] exists and certificate leaf[subject.OU] = "2LJ882GPY8"`（`SecStaticCodeCheckValidity`；〔code review 2026-10-20 加強〕兩個憑證欄位是 Developer ID 的中繼憑證與 Developer ID Application 的葉憑證，原本只要求 Apple 錨點與 Team），不過就結束；`test`（ad-hoc 的更新測試版）→ 不驗簽章；其他（含 `local`）→ 結束 |
| 參數與輸入 | 不讀命令列參數、環境變數、URL、剪貼簿或任何 IPC；啟動它的人什麼都給不了（A1）。開檔事件：實作一個什麼都不做的 `application(_:open:)`，避免 AppKit 對送來的檔名跳「無法打開」的警告（安全審查 P3-4，§4.2 量） |
| feed 網址 | delegate 的 `feedURLString(for:)` 回傳 **host 的 Info.plist** 裡的 `SUFeedURL`（`Bundle(url: host).object(forInfoDictionaryKey:)`，讀的是 bundle 的 Info.plist，不經 user defaults）；讀不到就結束。理由在 S2：不釘住的話，被攻破的輸入法若能在 host 網域的偏好設定寫 `SUFeedURL`，就能讓善解更新去讀一份重播的舊 appcast（有簽章、但沒有比現在新的版本），使用者永遠看不到新版，等於把有漏洞的版本凍結住。輸入法本體的探測也照做（它自己的 `Updater` delegate） |
| 流程 | `SPUUpdater(hostBundle: host, applicationBundle: host, userDriver: SPUStandardUserDriver(hostBundle: host))`；先 `checkForUpdateInformation()`（靜默），**有新版才** `checkForUpdates()`（Sparkle 的標準對話框）；沒有新版、使用者取消或更新完成就結束。沒有新版時不出現任何視窗，所以被攻破的輸入法啟動它也叫不出任何畫面 |
| 跳過版本 | Sparkle 把「跳過這個版本」記在 host 網域的偏好設定（存的鍵名是 `SUSkippedVersion`、`SUSkippedMajorVersion`、`SUSkippedMajorSubreleaseVersion`；第一個在原始碼裡的常數名叫 `SUSkippedMinorVersionKey`，〔原始碼〕`SUConstants.m`），背景檢查與探測會照它濾掉版本（〔原始碼〕`SPUSkippedUpdate.m`、`SUAppcastDriver.m`；安全審查之後的 plan-verifier 2026-10-20）。兩個問題：善解更新寫的是 `~/Library/Preferences/<ID>.plist`、輸入法讀的是 container，按了跳過之後關於頁仍顯示「更新到 X」、再按卻什麼都不出現（功能悄悄失效）；被攻破的輸入法也能寫一個很大的跳過版本，讓探測永遠看不到新版（凍結）。處置：**善解更新啟動時、輸入法每次探測前，都刪掉自己讀的那個網域裡這三個鍵**。善解只在使用者按下「更新」時才裝，所以「跳過」本來就沒有用處；按了之後下一次「更新到 X」照常出現對話框 |
| 安裝類型 | delegate 的 `shouldProceedWithUpdate` 只放行 `application` 類型，其他（含套件安裝）丟錯誤。接續先前中斷的安裝（resume）時 Sparkle 不呼叫這個 delegate；那條路仍關得住：`SUInstaller` 會比對壓縮檔裡實際是 `.app` 還是 `.pkg` 與項目的類型，不符就失敗，而能先起一個安裝的只有不在沙盒的同使用者程式（〔原始碼〕`SPUBasicUpdateDriver.m`、`SUInstaller.m`，安全審查 P3-3） |
| 介面 | 只有 Sparkle 的標準對話框，用 host 的名字與圖示；不自己畫視窗 |
| 日誌 | 靜態字串（R2） |

### 2.2 輸入法本體

| 項目 | 現在（修訂三） | 之後 |
|---|---|---|
| entitlements | 4 組：沙盒、`network.client`、IMK 的 mach-register、Sparkle 的兩個 mach-lookup | **3 個**：`app-sandbox`、`network.client`（探測用）、`mach-register.global-name = <ID>_Connection` |
| XPC 服務 | `Installer.xpc` 在 `Contents/Frameworks/Updater/Sparkle.framework` | **整個輸入法 bundle 裡沒有 `.xpc`**（含善解更新那份 framework）。修訂三「XPC 服務組合一變就換資料夾」的規則失效：沒有 XPC 服務，就沒有系統照路徑記住的問題；`Frameworks/Updater` 的資料夾名稱留著不改，只是歷史 |
| 輸入法那份 framework 裡的 `Autoupdate`、`Updater.app` | 在 | **刪掉**（只做探測用不到；少兩個能在沙盒外被送出的程式，S6）。〔沒量〕Sparkle 啟動與探測時應該用不到它們，§4.2 V2 量探測照常；探測壞了就留著它們、只靠 V7 的結果。善解更新那份照留 |
| Info.plist | `SUEnableInstallerLauncherService = true` | **false** |
| Sparkle | 探測＋互動更新＋把 Sparkle 視窗抬到設定視窗層（`watch`／`raise`） | **只做每日探測與「立即檢查」**（`checkForUpdateInformation`）；互動路徑與抬視窗整段刪掉；探測前刪掉自己網域的三個跳過鍵、feed 由 delegate 釘在 Info.plist（§2.1） |
| 「更新到 X」 | `checkForUpdates` | 先關掉設定視窗（§2.3），再 `NSWorkspace.openApplication(at: Contents/Helpers/善解更新.app)`、`activates = true`、不帶參數 |

- **V2 那筆拒絕的處置**：輸入法裡只做探測的 Sparkle 會查 `<ID>-spks`（Sparkle 確認有沒有進行中的安裝），沙盒擋下、Sparkle 當成沒有，探測與更新都不受影響（V2 量到）。這是沙盒照設計在擋，不加例外。驗收的「沒有沙盒拒絕」改成允許清單（§4.2）：**只允許輸入法程序的 `mach-lookup <ID>-spks`**，其他任何一筆都算失敗。輸入法整個拿掉 Sparkle、自己讀 feed（研究顧問分析的 A-2）之後這筆就會消失，排在 0.5.x（§6）。

### 2.3 關於頁

- 「更新到 X」按下去：**先關掉設定視窗，再啟動善解更新**（V1 的修法）。設定視窗的 `.floating` 不改：它是 agent App 的視窗能被看到的原因。關掉之後關於頁沒有東西要顯示，所以不另加「已開啟善解更新」的狀態列。
- 「更新」區塊的說明依位置多一段（判斷只看 `Bundle.main.bundleURL` 的上層是 `/Library/Input Methods` 還是家目錄的；文字和 `system-install.md` §2.5 的安裝程式說明一起定稿）：
  - `/Library`：「善解裝給這台 Mac 的所有帳號。按更新後，善解更新會先下載並驗證，再跳出系統的密碼框，請你輸入密碼來允許更新；框裡有一行說明固定是英文：『善解更新 wants permission to update 善解輸入法.』。善解只會在你按下這裡的『更新』之後才要密碼。不是管理員的帳號，請管理員更新。」（2026-10-22 在繁體中文的虛擬機量：密碼框裡系統自己的字翻成中文，這一行是 Sparkle 寫死的英文、不翻譯，`SUInstallerLauncher.m` 的註解寫明刻意不做在地化）
  - `~/Library`：「善解只裝在你的帳號，更新不需要密碼。有程式開著安全輸入時，系統會暫時停用善解；要改裝給整台 Mac，請重新下載安裝程式並選『這台 Mac』。」最後這一句只在 U2 一起發佈時才有；「只出 U1」時拿掉，文字結束在「系統會暫時停用善解。」
  - 英文版同義，跟著 `system-install.md` 一起定。

### 2.4 打包、簽章、檢查

| 檔案 | 改動 |
|---|---|
| `macos/Package.swift` | 新 `executableTarget ShanjieUpdater`（相依 Sparkle、`UpdateHost`）與 `UpdateHost`（只用 Foundation）、`UpdateHostTests`；輸入法的 Sparkle 相依保留 |
| `scripts/build-app.sh` | 建善解更新、放進 `Contents/Helpers`，它自己的 `Contents/Frameworks` 帶一份 Sparkle.framework（刪掉 `XPCServices`）；輸入法那份 framework 刪掉 `XPCServices`、`Autoupdate`、`Updater.app`；ad-hoc 簽章由內而外：善解更新的 Sparkle（`Autoupdate`、`Updater.app`、framework）→ 善解更新 → 輸入法的 Sparkle → 輸入法（三個 entitlements）；簽章前 `chmod -R a+rX,go-w` |
| `.github/workflows/release.yml` `sign` | 同一個順序，全部 `--options runtime --timestamp`，只有輸入法帶 `--entitlements`（字面值改成三個鍵）；**解開建置產物用 `tar -xpf`（保留 build-app.sh 設好的權限）；公證並蓋章之後、打包 zip 之前 `chmod -R a+rX,go-w`**（`stapler` 在 `umask 077` 下會把 `CodeResources` 寫成 0600，程式審查量到）（`umask 077` 會讓 bundle 變成 0700／0600，root 擁有時使用者讀不到、善解更新也執行不了；system-install §1）；公證一次。`verify()`：entitlements 整份等於三個鍵；兩份 framework 都沒有 `.xpc`，輸入法那份沒有 `Autoupdate` 與 `Updater.app`、善解更新那份兩個都在；都是 Team 簽章、hardened runtime、沒有 entitlements；善解更新是 Team 簽章、hardened runtime、沒有 entitlements、Info.plist 沒有那幾個鍵；輸入法的 `SUEnableInstallerLauncherService` 是 false；`find ! -perm -o+r` 為空、目錄與執行檔都有 `o+x`、沒有群組或其他人可寫、沒有 setuid／setgid |
| `scripts/check-app.sh`、`check-entitlements.sh` | 和 `verify()` 同樣的斷言（ad-hoc 版不查 Team 與 runtime）；刪掉修訂三的 `Installer.xpc` 與資料夾位置那幾條 |
| `scripts/test-sandbox-mutations.sh` | 刪掉修訂三那三種；每種新斷言一個突變，都要讓檢查失敗：entitlements 加回 `-spks`、輸入法 `SUEnableInstallerLauncherService` 改 true、善解更新的 Info.plist 塞 `SUFeedURL`、任何一份 framework 放回 `.xpc`、輸入法那份放回 `Autoupdate`、`Contents/Helpers` 多一個 App、`chmod 700` 一個檔、`chmod g+w` 一個檔 |
| 安裝程式 | 不變（輸入法 zip 是它的資源） |

### 2.5 移轉與共存

| 情境 | 行為 |
|---|---|
| 0.4.7（`~/Library`、沒有沙盒、程序內 Sparkle）→ 0.5.0 | 0.4.7 自己的 Sparkle 換檔（V4）；第一次啟動 0.5.0 照 app-sandbox §2.6 搬資料 |
| 維護者的 0.0.0 → 0.5.0 | §4.3 之後，維護者的 Mac 裝的是 U1＋U2 的 dry run（0.0.0、新配置、`ShanjieUpdateBuild=release`、`/Library`），所以這一步是**善解更新在 Developer ID 下第一次走系統 domain**（§4.4）。不再有修訂三配置的那份 |
| 0.5.0 之後的每次更新 | 善解更新：`~/Library` 使用者 domain、不問密碼；`/Library` 系統 domain、一次管理員密碼 |
| 從 `~/Library` 搬到 `/Library` | 只有安裝程式（`system-install.md`）；Sparkle 不搬位置 |
| 多使用者 Mac（`/Library`） | 任一管理員按更新，換的是所有人共用的那一份；其他已登入帳號的舊程序〔推論〕要到登出或重新載入才換，發版說明寫一句。標準帳號按更新會停在密碼框 |
| Homebrew | cask 不動（帳號層）；brew 裝的那份按更新也走善解更新（使用者 domain） |
| 開發版 | `local` 組建的善解更新直接結束；更新測試版（`test`）只在虛擬機用 |

## 3. 安全

攻擊者假設：已經在沙盒輸入法程序裡執行程式。

| 路徑 | 修訂三 | 這片之後 |
|---|---|---|
| 對安裝服務給假 host（可寫位置、自己的公鑰）→ 在沙盒外執行 | 可以（F1） | 輸入法 bundle 裡沒有 XPC 安裝服務，也沒有 mach-lookup 例外；善解更新裡的 launcher 只用它自己算出的 host。**前提沒量**：已經在輸入法程序裡執行程式的攻擊者可以不經 Sparkle、直接呼叫 `SMJobSubmit` 送任何程式（S6 的程序內 launcher 只是其中一條路）；沙盒擋不擋這個送出由 §4.2 V7 量，擋不住就是停止條件。輸入法那份 framework 刪掉 `Autoupdate` 與 `Updater.app` 只是少兩個現成的程式，擋不住這種攻擊者，不當成緩解 |
| 在 host 網域的偏好設定寫 `SUFeedURL`（讀重播的舊 appcast）或 `SUSkipped*`（濾掉新版），凍結更新 | — | 不行：feed 由 delegate 釘在 Info.plist；跳過的三個鍵在善解更新啟動時與每次探測前刪掉（§2.1），§4.2 V6 量 |
| 套件安裝類型 → 直接系統 domain → 以善解的名義跳密碼框 | 可以（P1-A） | 不行：類型來自有簽章的 feed，沒有 EdDSA 私鑰改不了；善解更新另外只接受 `application` 類型 |
| 啟動善解更新 | — | 可以，但帶不了參數（A1）；沒有新版就靜靜結束；有新版時出現的是真的更新，等於替使用者按了一次「更新」 |
| 把輸入法 bundle 複製到 container、改 Info.plist、啟動裡面的善解更新 | — | 路徑檢查失敗就結束（V5）；沙盒寫出的檔〔推論，沒量〕帶 quarantine |
| 對善解更新送開檔事件，讓 AppKit 跳出帶攻擊者檔名的警告 | — | 什麼都不做的 `application(_:open:)`（§2.1），§4.2 量 |
| 自己畫一個假的密碼框 | 可以 | 仍然可以（任何有介面的 App 都做得到）。真的密碼框只在使用者按下「更新」、而且有新版時出現，標題是系統的，文字有「善解更新」與「善解輸入法」兩個名字；關於頁照實寫（§2.3） |
| 把按鍵送出去 | 可以（`network.client`） | 不變。這是沙盒對這個 App 擋不住的事 |
| 讀使用者文件、其他 App 資料、寫 container 外 | 擋得住，但 F1 可逃 | 擋得住，沒有已知的逃脫路 |

- **更新路徑上以 root 執行的程式碼**：只有 Sparkle 的 `Autoupdate`（系統 domain 時，由系統的 `SMJobSubmit` 啟動）；更新路徑上沒有我們的 root 程式碼，善解更新以使用者身分執行。（安裝程式「這台 Mac」的 root 階段是我們的 root 程式碼，在 `system-install.md` §2.3。）
- **持有 EdDSA 私鑰與 feed 寫入權的人**：今天能讓所有使用者的輸入法換成任意程式（R10）；`/Library` 之後多了「以 root 換檔」，換完仍以使用者身分執行。善解更新只接受 `application` 類型，所以拿不到「以 root 跑 `installer`」這條。R10 與 about-update §0.1 照實加這兩句。
- **Sparkle 版本從此是發版關卡**：系統 domain 讓 Sparkle 以 root 執行的漏洞類別（2.9.6 修過的兩則）變得相關。`Package.resolved` 釘住版本；Sparkle 有新的安全公告就先升再發，寫進 `docs/releases/README.md`。
- **還留著的攻擊面**：探測的 Sparkle（XML 與簽章解析）仍在看得到按鍵的程序裡，A-2 才搬走；`network.client` 不變；不在沙盒的使用者層惡意程式本來就能換掉 `~/Library` 的輸入法與善解更新，`/Library` 多一層 root 擁有。
- **對外說法**：從修訂二的「縱深防禦」改成「輸入法在 App 沙盒裡執行；更新由另一個不在沙盒裡、只會更新已安裝那一份善解的小程式完成」。仍不寫「輸入內容無法外流」。
- **app-sandbox §4.5**（F1 的虛擬機驗證與給 Sparkle 的私下回報）照原計畫做，對所有沙盒化的 Sparkle App 都成立；不再擋我們任何事。

## 4. 驗收

### 4.1 CI

- `UpdateHostTests`：允許路徑（兩個位置 × 兩個名稱）、往上三層、符號連結指到別處、container 與桌面的副本、家目錄字串的邊界（`/Users/a` 與 `/Users/ab`）；`trust(build:)` 的三種值與缺值。每個判斷至少一個突變要讓測試失敗。
- `UpdatesTests`：「更新到 X」呼叫的是「關設定視窗、啟動善解更新」的閉包；`focus` 再啟動一次。
- 安裝類型的判斷抽成純函式並測試（`application` 放行，其他不放行），突變一個。
- §2.4 的 `check-app.sh`、`check-entitlements.sh` 與每一種突變。

### 4.2 虛擬機（main 執行；最後的程式、開發版 ID 的更新測試版，照 §1.1 的做法重跑一次）

- V4、V1、V2、V5、V3 依序，和 §1.1 同樣的斷言，另外：
  - V1：按「更新到 X」後設定視窗關掉、善解更新的視窗在最前面（維護者在虛擬機裡看）；
  - V2：明確記下有沒有「存取其他 App 的資料」詢問（有的話不是停止條件，寫進發版說明與研究紀錄）；
  - 每一步的 `log`（從按下更新到新版啟動的沙盒拒絕）由腳本逐筆比對：**只允許**程序名稱是輸入法、操作是 `mach-lookup`、服務名稱**整串等於** `<ID>-spks` 的那一種，其他任何一筆（含 `-spki`）都算失敗。`log` 會合併重複的報告、也有不記錄的規則，所以「沒有其他拒絕」只是煙霧測試，不是證明；證明靠 V6、V7 的正面量測；
  - V3 另做一次取消：密碼框按取消，沒有錯誤視窗、輸入法照常打字、`/Library` 的 bundle 沒變。
- 新增三項（安全審查 2026-10-20）：
  - **V6 偏好設定左右不了更新**：(a) 在輸入法 container 的偏好設定與 `~/Library/Preferences/<ID>.plist` 各寫 `SUFeedURL=http://127.0.0.1:<另一個埠>/x`，啟動善解更新與按「立即檢查」：那個埠收不到任何請求、照常讀測試 feed；同時記下 container 的那份有沒有被不在沙盒的善解更新讀到（cfprefsd 導向的假說），寫進研究紀錄。(b) 兩個網域都寫 `SUSkippedVersion=999`（`SUSkippedMajorVersion` 只濾掉設了 `minimumAutoupdateVersion` 的項目，我們的 appcast 沒有設，寫它在壞的組建裡也看不出差別；〔原始碼〕`SUAppcastDriver.m`）：「立即檢查」照樣顯示新版、按「更新到 X」照樣出現 Sparkle 對話框。(c) 在善解更新的對話框按「跳過這個版本」，再按「更新到 X」：對話框照常出現。不釘住或不刪鍵的組建，(a)、(b)、(c) 都會失敗。
  - **V7 沙盒擋不擋 `SMJobSubmit`**（S6）：真正要判斷的只有這件事，因為已經在輸入法程序裡執行程式的攻擊者可以不經 Sparkle、直接呼叫它。用一份只在虛擬機用的測試組建：main 在虛擬機裡對 U1 的程式套一個不進 repo 的小 patch，讓關於頁的一個按鈕直接呼叫一次 `SMJobSubmit(kSMDomainUserLaunchd, …)`，送一個良性的工作（`/usr/bin/touch` 在 container 外建一個標記檔，例如 `/Users/Shared/shanjie-v7-marker`），把回傳值與錯誤寫進靜態 log。三個互斥的結果：
    - **對照組先跑**：同一個 patch、同一份工作字典，在一份拿掉 `com.apple.security.app-sandbox` 的組建（只在虛擬機用）裡跑：`SMJobSubmit` 要回傳 true、標記檔要出現。對照組沒成功，就是工作字典或 patch 本身有問題，下面的結果一律算「沒有結論」。
    - **關住**（對照組成功之後）：沙盒組建的 `SMJobSubmit` 回傳 false，標記檔沒有出現，`launchctl print gui/<uid>/<工作名稱>` 查不到；log 有沒有沙盒拒絕的紀錄都記下來。
    - **沒關**：`SMJobSubmit` 回傳 true，或標記檔出現，或 `launchctl` 查得到那個工作。停止條件（F1 沒關，不管更新本身會不會成功）。
    - **沒有結論**：對照組沒成功，或 patch 沒跑到呼叫（例如按鈕沒反應、程序崩潰），沙盒沒被測到。修 patch 再跑一次；仍沒有結論就是停止條件。
  - **開檔事件**：`open -a <善解更新.app 的路徑> /tmp/任意檔名.txt`：沒有視窗、沒有警告，立刻結束。

### 4.3 dry run 與維護者的 Mac

- dry run（main 觸發，事先問維護者）：`verify()` 全過、公證通過（巢狀 App 被公證拒絕是停止條件）。
- 維護者的 Mac（每步 15 秒級）：用 dry run 的安裝程式重裝（`system-install.md` §4 第 4 項）；關於頁看得到 `/Library` 那段說明；按「更新到 0.4.7」→ 設定視窗關掉、善解更新在最前面、顯示 0.4.7 的 Sparkle 對話框 → **按取消**；`log` 照 §4.2 的允許清單。

### 4.4 順序、tag、canary

- 順序：這片（U1）的 PR 先合併，`system-install.md` 那片（U2）後合併；兩片都合併後才 dry run。從 U1 合併到 §4.3 與 `system-install.md` §4 全部通過之前，main 不推任何 tag；其他片照常合併、一起等。
- v0.5.0（推 tag 照例當下問維護者）發佈後：
  - 維護者的 0.0.0（U1＋U2 的 dry run、`/Library`）按「更新到 0.5.0」：善解更新在 Developer ID 下第一次走系統 domain。密碼框文字、成功、`root:wheel` 可讀、container 在、`log` 照允許清單；記下開機時間（`sysctl -n kern.boottime`）。失敗照 §5 的停止條件。
  - **canary 0.5.1**：盡快發一個只有小改動的 v0.5.1。維護者的 Mac（`/Library`）按「更新到 0.5.1」：善解更新、密碼框、成功、container 在、`log` 照允許清單；虛擬機裡用正式的 v0.5.0 安裝程式選「只有我的帳號」，再按「更新到 0.5.1」：不問密碼、成功、container 在。**兩條都過之前不發別的版本。**
  - v0.5.0 的發版說明與網站寫明：自動更新失敗時，下載安裝程式重裝即可，資料在 container 裡不受影響。
- **「只出 U1」**（U2 照 `system-install.md` §5 被 revert 時）：沒有 `/Library` 的路，所以
  - 凍結改成：從 U1 合併到 §4.2 的虛擬機與下面的維護者步驟都通過之前，main 不推任何 tag；
  - 維護者的 Mac 先照 `system-install.md` §4.3 g 的指令退回帳號層（`sudo rm -rf` 由維護者自己跑），再用只有帳號層的 dry run 安裝程式重裝；§4.3 其餘步驟照做，但「關於頁看得到 `/Library` 那段說明」改成「關於頁看得到 `~/Library` 那段說明，而且沒有提到『這台 Mac』」；
  - 發佈後的「0.0.0 → 0.5.0」與 canary 都只走使用者 domain：維護者的 Mac 與虛擬機都在 `~/Library`，不問密碼；系統 domain 的 Developer ID 量測留給之後的 `/Library` 片。

## 5. 停止條件、預算、回滾

- **停止條件**（碰到就停，回報維護者）：
  - 沙盒輸入法啟動不了善解更新，或關掉設定視窗之後它的視窗仍抬不到前面（§4.2 V1）；
  - 系統 domain 從善解更新走不通（§4.2 V3；canary 的 `/Library` 那條）；
  - §4.2 V7 是「沒關」或兩次「沒有結論」；§4.2 V6 的 feed 網址或跳過版本仍被偏好設定左右；
  - 維護者的 Mac 從 0.0.0 經善解更新到 0.5.0 失敗（§4.4，和 canary 的 `/Library` 那條同一類：停發其他版本，使用者照發版說明重裝）；
  - 允許清單以外的沙盒拒絕（§4.2、§4.3、canary）；
  - 公證拒絕巢狀 App（§4.3）；
  - 輸入法需要第四個 entitlement、或善解更新需要任何 entitlement 才能運作：停下來問，不自行加；
  - canary 任何一條失敗：停發其他版本。
- **預算**：`pilotfish:security-executor` 實作 1 次＋修正 1 次，從原型 `feat/standalone-updater`（`89c9e43a`）接著做；虛擬機一輪。
- **限制**：agent 不安裝、不啟動 App、不呼叫 TIS 或 lsregister、不在 .app 裡執行任何東西、不碰 `~/Library` 與鑰匙圈、不推 tag；dry run 由 main 觸發、事先問維護者。
- **回滾**（發佈前；§2.4 的權限修正 `tar -xpf`、`chmod -R a+rX,go-w` 與它的檢查**一律保留**，它對修訂三的配置也成立，不留 0700 的 bundle）：
  - 只有 U1 合併：revert U1 除權限修正以外的部分，回到修訂三（輸入法帶 `Installer.xpc`、四個 entitlements）。
  - U1、U2 都已合併、停在 U1 的問題：U2 依賴善解更新（安全審查 P1-A 的前提），所以兩片都 revert（U1 一樣保留權限修正）；v0.5.0 只出沙盒（修訂三）。已經裝在 `/Library` 的虛擬機與維護者的 Mac 照 `system-install.md` §4.3 g 退回帳號層。
  - 停在 U2 的問題：照 `system-install.md` §5 只 revert U2，U1 照出（「只出 U1」，驗收與凍結照 §4.4 最後一點）。
  - 沒有資料移轉，container 不動。
- 發佈後善解更新壞掉：不回頭用 `Installer.xpc`，向前修一版，使用者照發版說明下載安裝程式重裝（安裝程式和 Sparkle 無關，永遠可用）。

## 6. 範圍外

- A-2：輸入法拿掉 Sparkle、自己讀 `appcast.xml`（0.5.x；V2 那筆拒絕跟著消失）。
- 善解更新常駐或背景探測、登入項目、`SMAppService`。
- 善解更新自己的介面或在地化（全部用 Sparkle 的標準對話框）。

## 7. 要跟著改的文件（實作的 PR）

| 檔案 | 改什麼 |
|---|---|
| `docs/contracts/app-sandbox.md` | §0 與 §2.1 entitlements 改三個；§2.4、§2.8 改寫（`Installer.xpc` 刪除、修訂三的資料夾規則改成歷史註記、定位改成 §3 的說法）；§3 刪「根本解排到 B 片」；§4.4 canary 改指這份 §4.4；§5 刪「新位置登記不到安裝服務」 |
| `docs/contracts/about-update.md` | §0.1 加 root 換檔與 `application` 類型限制；§1「更新到 X」改成啟動善解更新；§2 Info.plist 與打包；§3 驗收 |
| `docs/contracts/oc-experimental.md` §5 | 獨立更新程式已在 v0.5.0 做掉，B 片只剩 R8 的推論輔助程式與模型下載 |
| `docs/PLAN.md` | R9 三個 entitlements；R10 的 XPC 段改寫、加 root 換檔與類型限制 |
| `docs/releases/README.md` | Sparkle 版本是發版關卡；金鑰外洩手冊加 `/Library` 的 root 換檔 |
| `docs/research-log.md`、`docs/methodology.md` | §4.2 的量測；方法論：「XPC 服務照路徑被系統記住」用「不放 XPC 服務」解，比換資料夾乾淨 |

## 8. 審查紀錄

| 審查 | 結果 | 處置 |
|---|---|---|
| security-reviewer（2026-10-20，草稿） | 這份沒有 P0、P1。P2-1：`SUFeedURL` 會先讀 host 網域的 user defaults（host 不是主 bundle 時），S2 不成立；被攻破的輸入法若寫得進那個網域，可以讓善解更新讀重播的舊 appcast，凍結更新（〔原始碼〕，cfprefsd 導向是假說）。P3-1：輸入法程序裡仍有程序內的 launcher，F1 關不關取決於沙盒擋不擋 `SMJobSubmit`，沒量；V2 那筆拒絕是探測的狀態查詢，不是證據。P3-2：允許清單要逐字比對，`log` 的「沒有拒絕」只是煙霧測試。P3-3：resume 不經過類型的 delegate。P3-4：開檔事件可能讓 AppKit 跳警告（假說）。P3-7：S5「新 bundle 的 ID 要等於 host」在原始碼裡找不到；「沒有 root 程式碼」要限定在更新路徑；quarantine 是推論；A1 可補 `environment` | 全部 FIX：P2-1 → S2 改寫、§2.1 feed 網址列（delegate 釘在 Info.plist，輸入法也照做）、§3 新列、§4.2 V6、§5；P3-1 → S6、§2.2 刪輸入法那份的 `Autoupdate`／`Updater.app`、§2.4、§3 第一列、§4.2 V7、§5；P3-2 → §4.2 腳本逐字比對；P3-3 → §2.1 安裝類型列；P3-4 → §2.1 參數列、§3、§4.2；P3-7 → S5、§3、A1。U2 的 P1-1、P2-2 等在 `system-install.md` §7 |
| plan-verifier（2026-10-20，安全處置之後） | REVISE，4 項：(1) V7 沒有「沒有結論」這一支：launcher 在 `SMJobSubmit` 之前還有好幾個自己失敗的出口，看起來像過關、其實沙盒沒被測到；V7 組建由誰、怎麼做沒寫；(2) 只釘住 `SUFeedURL`，Sparkle 的跳過版本鍵也在 host 網域：按了跳過之後關於頁仍顯示新版、再按卻什麼都不出現，被攻破的輸入法也能寫跳過鍵凍結更新（〔原始碼〕）；(3) 維護者 Mac 的 0.0.0 前後說法矛盾：§4.3 重裝之後已是新配置，「0.0.0 → 0.5.0」其實是善解更新在 Developer ID 下第一次走系統 domain，失敗沒有對到停止條件；(4) 回滾只寫 revert U1，U2 已合併時會留下權限壞掉的 `/Library` bundle | 全部 FIX：(1) §4.2 V7 三個互斥結果（關住要看到 `SMJobSubmit` 被拒的紀錄）、組建是 main 在虛擬機套的不進 repo 的 patch，§5；(2) §2.1 跳過版本列、§2.2 Sparkle 列、§3 凍結列、§4.2 V6 (b)(c)；(3) §1.1 沒量到的、§2.5、§4.4、§5；(4) §5 回滾分三種情況，權限修正一律保留 |
| plan-verifier（2026-10-20，第二次；第二次自動 REVISE） | REVISE，3 項：(1) V7 的「關住」在沙盒沒被測到時也會成立：`Failed to submit installer job` 在授權失敗時也會印，而「沒關」定義成「更新裝成功」在沒有 `-spki` 例外時到不了；§3「launcher 找不到要送的程式」擋不住已經在執行程式的攻擊者；(2) V6 (b) 用 `SUSkippedMajorVersion`，它只濾掉設了 `minimumAutoupdateVersion` 的項目，壞的組建也會過；(3)「只出 U1」這條路的凍結與驗收綁著 U2 的步驟，執行不了 | 逐項處置，全部 FIX：(1) §4.2 V7 改成 patch 直接呼叫一次 `SMJobSubmit` 送良性工作，以回傳值、標記檔、`launchctl` 判三個結果，§3 第一列刪掉那個緩解說法；(2) V6 (b) 改寫 `SUSkippedMinorVersion`；(3) §4.4「只出 U1」一點、§5 回滾指向它。照流程這一版只再送一次收尾審查，再 REVISE 就交維護者 |
| plan-verifier（2026-10-20，收尾審查） | REVISE，3 項：(1) V7 的「關住」只靠回傳 false，工作字典寫錯也會回 false，要有不在沙盒的對照組；(2) Sparkle 存的跳過鍵名是 `SUSkippedVersion`，`SUSkippedMinorVersion` 只是常數名，照原文刪鍵與 V6 (b) 都會落空；(3)「只出 U1」時 §4.3 仍要看 `/Library` 那段說明，`~/Library` 那段又叫使用者去選不存在的「這台 Mac」 | 照它的最小修改 FIX（第 2 項核對過 `SUConstants.m` 第 26 行）：(1) §4.2 V7 加對照組；(2) §2.1、V6 (b) 改用存的鍵名；(3) §2.3 那句只在 U2 一起發佈時才有、§4.4「只出 U1」改看 `~/Library` 那段。收尾審查之後的修改，照流程交維護者決定是否核准，不再自動送審 |
