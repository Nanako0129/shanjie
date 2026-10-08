# 契約：安裝程式第二版（分步驟、白話、內建試打）

狀態：第三版草稿（2026-10-08，兩次 REVISE 之後逐項處置，見 §8）。使用者 2026-10-08 說「安裝流程先處理好一下，現在太簡陋了」，並選了「重做自己的安裝程式」：分步驟、白話、品牌外觀、最後內建試打。排在 App 沙盒之前。

**第一版審查之後縮小的範圍**：plan-verifier 第一次 REVISE，下面三項移出這一片。

- **brew 安裝後自動註冊**：不做。`postflight_steps` 在 Homebrew 的沙盒裡執行，HOME 是暫存資料夾、不能讀真正的家目錄，`postflight do` 也過不了 `brew style`。這是 2026-10-04 plan-verifier 讀 Homebrew 原始碼指出、寫進 s3b §14.1 與研究紀錄的結論（讀原始碼得到，沒有實測；TIS 在沙盒裡能不能註冊也沒量過），第一版又寫了一次（教訓寫進 `docs/methodology.md`）。
- **升級時結束舊程序**：改由 s3b §15（修訂四，分支 `fix/brew-upgrade`）處理。新的 cask 用 Homebrew 內建的 `terminate_process`，不需要輔助程式。
- **註冊輔助程式 `Contents/Helpers/shanjie-install`**：留給 App 沙盒那一片。只有那一片需要「在沙盒外註冊」，簽章、公證與身分的問題（第一次審查的 3、4 點）一起在那裡處理。

這一片因此只改安裝程式（`安裝善解輸入法.app`），不動輸入法本體、cask 和 release 的簽章流程。

## 0. 要做到什麼、怎麼看到

- 第一次安裝的人照著安裝程式一步一步按，最後在安裝程式的試打框裡用善解打出字，過程中看不到技術名詞。
- **怎麼看到**（每一項都是 §3 的必要項目）：
  - main 用 §1.4 的畫面輸出，把每個畫面的淺色、深色版截圖交給使用者看，照回饋修改（§3 第 3 項）。
  - 使用者在這台機器上用新安裝程式走一次「重新安裝 → 試打」，在試打框打出字（§3 第 4 項）。
  - 使用者在虛擬機裡、從沒裝過善解的 macOS 帳號，用新安裝程式走一次全新安裝，包括需要登出的那一步，最後在試打框打出字（§3 第 5 項，使用者 2026-10-08 選了用虛擬機）。
- **決定邏輯不變**：`InstallerPlan.decide`（要安裝、更新、啟用還是重新安裝）與 `Registration.run`（註冊、啟用、確認系統接受）一行都不改。s3c 對這兩者的測試與實機紀錄繼續有效。這一片新增的只有「結果 → 畫面」的對應，以及畫面本身。

## 1. 安裝程式（`macos/Sources/ShanjieInstaller`）

### 1.1 步驟與畫面

文字以實作時的版本為準。原則：白話，不出現路徑與檔名；技術細節收進「詳細資訊」。

| 步驟 | 內容 | 按鈕 |
|---|---|---|
| 歡迎 | 印章，「善解輸入法 <版本>」，一句「開源的 macOS 注音輸入法」，以及「只裝在你的帳號，不需要管理員密碼」。依 `InstallerPlan` 加一句：已裝舊版時寫「會從 X 更新到 Y」；已裝同版時寫「這個版本已經裝好了」；已裝較新版時寫「已經有更新的版本，不會降版」。可展開的「詳細資訊」裡放安裝位置、上一版保留成 `.shanjie-previous`，以及授權文字與「顯示授權」按鈕 | 取消／安裝、更新、啟用；`enableSame` 時多一個「重新安裝」（照現在的 `InstallerPlan`） |
| 安裝 | 轉圈，加一句白話狀態（「正在複製…」「正在請系統啟用…」「正在等系統啟用…」）。失敗時寫白話說明，下面放可選取的錯誤細節和「拷貝錯誤細節」按鈕（家目錄顯示成 `~`，B-4） | 進行中不能關閉（照現在的 `isBusy`）；失敗時只有「完成」 |
| 啟用 | 只在系統需要登出時出現，不需要時直接跳過。分三步寫：1. 從蘋果選單登出；2. 再登入；3. 重新打開這個安裝程式，按「啟用」。**沒有「現在登出」按鈕**：要送 Apple Event 給 loginwindow，hardened runtime 下需要 `automation.apple-events` entitlement，和安裝程式「不帶 entitlements」的規則（`check-installer.sh`）衝突 | 重新檢查／完成 |
| 試打 | 「已經裝好了」，一個可輸入的一般文字框（不是 `NSSecureTextField`，安全輸入會讓善解反灰；內容不存到任何地方，B-6），「切換到善解」按鈕，再加一句「之後從選單列的輸入法選單，或按 Caps Lock、⌃空白鍵切換」 | 完成 |

### 1.2 「切換到善解」

- 用 `Registration.inputSources`／`inputModeID`（不另寫查詢），只選輸入方式 ID 等於 `<已安裝那一份的 bundle ID>.zhuyin`、`kTISPropertyInputSourceIsSelectCapable` 為真的那一個。同一個 bundle ID 也會列出輸入法本體（不能選）和停用失敗時留下的舊模式，選錯會靜靜地沒切到（安全審查 B-1）。對它呼叫 `TISSelectInputSource`，再讓文字框成為 first responder。
- 呼叫之後重新讀 `TISCopyCurrentKeyboardInputSource`，目前的輸入方式真的是那個 ID 才算成功；回傳 noErr 但沒切過去（例如安全輸入開著，第三方輸入法反灰）也當失敗（B-5）。
- 只在使用者按下按鈕時做這件事，不會自動切換。
- 找不到、`TISSelectInputSource` 回傳錯誤，或重讀後不是善解時，按鈕旁寫一句白話，例如「切換失敗，請從選單列的輸入法選單選『善解輸入法』」，不會讓程式停住。
- 這些呼叫都不需要 entitlement，安裝程式維持沒有 entitlements、開 hardened runtime。

### 1.3 外觀

- 頂端放步驟指示：四個點，目前的步驟是實心，跳過的步驟顯示為完成。
- 外觀跟著系統的淺色、深色。印章顏色與標題字型比照網站（朱紅印章、宋體標題）。
- 視覺參數放具名常數，註解寫出處。外觀怎麼收斂，照使用者的回饋（CLAUDE.md「外觀與行為」）。
- 中英文照舊用 `L(zh, en)`。

### 1.4 畫面輸出（給截圖與審查用）

- `shanjie-installer --render-steps <資料夾>`（剛好這兩個參數才進這條路；資料夾必須已經存在；PNG 用不覆蓋的方式寫，同名檔已存在就失敗，B-2）：把每個畫面在淺色、深色下各畫成一張 PNG，寫進那個資料夾，然後結束。
  - 畫面有：歡迎（安裝、更新、`enableSame`、`enableNewer` 四種）、安裝中、失敗、啟用、試打，共 8 個畫面、16 張。
  - 版本號、錯誤文字都用寫死的假資料。
- 不開視窗，不讀 `~/Library/Input Methods`，不呼叫 `copyFiles`、`Registration`、TIS，也不寫那個資料夾以外的任何東西。
  - 做法：`main.swift` 在 `NSApplication.run()` 之前先檢查這個參數。有的話只呼叫建畫面的程式碼，在離屏 view 上設 `appearance`，用 `cacheDisplay` 輸出 PNG。
  - 真正的流程和畫面輸出共用同一份建畫面程式碼，截圖看到的就是實際的畫面。
- **s3c 規則的例外**：s3c §5 規定 agent 與 CI 都不啟動安裝程式、不執行它的執行檔；s3c §2.5 規定安裝程式不寫自己的東西。畫面輸出是唯一的例外：
  - 只有 main 執行，agent 與 CI 照舊不執行；
  - 執行的是 `swift build` 出來的裸執行檔（不在 `.app` 裡），參數只有 `--render-steps <資料夾>`，資料夾在 repo 外的 session 暫存區；
  - 只寫那個資料夾裡的 16 張 PNG。
  - 裸執行檔不在 bundle 裡，推論不會在 LaunchServices 登記；§3 第 3 項量執行前後的 LaunchServices 紀錄。
  - s3c §5 那一條加註指向這裡。

### 1.5 結果到畫面的對應（可測）

- 新增一個純函式，放在 `ShanjieInstall`（例如 `InstallerFlow.next(after: Registration.Outcome)`），把結果對應到畫面。§1.2 挑輸入方式的函式也放在 `ShanjieInstall`（`Registration.inputSources`、`inputModeID` 是 internal，`Registration.run` 不改）：
  - `done` → 試打；
  - `modeNotListed` → 啟用；
  - `notAccepted` → 等待（安裝畫面的狀態「正在等系統啟用…」，不是另一個畫面）；
  - `registrationFailed`、`enableFailed` → 失敗。
- 等待最多 30 秒，等到了就是試打，沒等到就是啟用。這段照 s3c §2.4，等待的計時不放進這個函式。
- `Controller` 只依這個函式的回傳值換畫面。

## 2. 不變的部分

- 複製檔案照舊：內附的 zip 用 `ditto --noqtn` 解開，用內附的 `install-ime.sh` 以 `SHANJIE_INSTALL_SKIP_REGISTER=1` 和最小環境執行。
- 註冊照舊在安裝程式自己的行程裡做，針對已安裝的那一份，用它的 bundle ID 與 `UserDefaults(suiteName:)`，即 s3c §2.3 與安全審查 P1-A。
- `check-installer.sh`、`build-installer.sh`、`release.yml` 的檢查（沒有 entitlements、開 hardened runtime、內附的 zip 和腳本與 repo 相同）全部照舊；這一片不改它們。

## 3. 驗收

1. 單元測試（`swift test`，`ShanjieInstallTests`）：
   - 每個 `Registration.Outcome` 經 §1.5 的函式對應到正確的畫面；
   - 「切換到善解」挑輸入方式的那一段寫成純函式（輸入是一串 ID 與是否可選），餵「本體＋`.zhuyin`＋舊的 `.standard`」時選到 `.zhuyin`，沒有 `.zhuyin` 時回傳沒有（B-1）；
   - `InstallerPlan.decide` 的既有測試照舊通過。
2. `make test`、`make installer`、`scripts/check-installer.sh` 全部通過。
3. **畫面輸出**（main 執行）：
   - 用 `--render-steps` 產生 16 張圖，交給使用者看，照回饋修改，每一輪只改使用者指出的地方。
   - 執行前後各記錄一次 `ls -laT "$HOME/Library/Input Methods"`（含隱藏檔與修改時間）、`defaults read com.apple.HIToolbox AppleEnabledInputSources`，以及 `lsregister -dump` 裡 `com.nyanako.shanjie.installer` 與那個裸執行檔路徑出現的行（main 執行），三份前後都要完全相同。
   - fresh verifier 讀程式碼，確認 `--render-steps` 那條路徑沒有呼叫 §1.4 列的任何動作，而且建畫面的函式只接參數、不引用 `Paths`、`InstallerPlan.decide`、`copyFiles`、`Registration`、`TIS*`（B-3）。
4. **實機**（使用者操作，main 先準備好）：
   - 準備：main 從 Release 下載正式的 `shanjie-0.3.0.zip`，對照 `.sha256` 確認，再用 `SHANJIE_VERSION=0.3.0 scripts/build-installer.sh build-device <那個 zip>` 組出新安裝程式（裡面的輸入法是 Developer ID 簽章的正式版）。
   - 開始前的狀態：`~/Library/Input Methods/善解輸入法.app` 是 0.3.0 正式版（簽章 Team 2LJ882GPY8）。main 記錄它的 cdhash，以及 `.shanjie-previous` 是否存在。
   - 使用者操作：
     1. 打開新安裝程式，在歡迎畫面按「重新安裝」。
     2. 在試打畫面按「切換到善解」，打一個詞，例如 ㄕㄢˋ ㄐㄧㄝˇ 打出「善解」。main 事後確認目前的輸入方式是 `com.nyanako.inputmethod.shanjie.zhuyin`（B-5）。
     3. 拍下試打畫面，淺色或深色都可以。
   - 結束後的狀態：`善解輸入法.app` 仍是 0.3.0 正式版，cdhash 和開始前相同；`.shanjie-previous` 被換成剛才那一份（原本那份較舊的上一版不保留）。不需要還原。
   - 啟用畫面（需要登出）這條路，這台機器走不到，因為輸入法已經註冊過，由第 5 項涵蓋。
5. **全新安裝（虛擬機，必要）**：s3c 那次也沒有實機測過全新安裝（研究紀錄 2026-10-04：brew 移除後系統還記得已啟用）。使用者 2026-10-08 選了用虛擬機跑。
   - 準備：虛擬機軟體與 macOS 26 以上的映像。用哪一套、要不要安裝，先問使用者（要裝軟體）。新安裝程式同第 4 項，用正式的 0.3.0 zip 組成，放進虛擬機。
   - 使用者在虛擬機裡、沒裝過善解的帳號操作：打開安裝程式 → 安裝 → 依啟用畫面登出、登入 → 重新打開按「啟用」→ 試打框打出字，並拍下歡迎、啟用、試打三個畫面。
   - 通過：試打框打出中文，目前的輸入方式是 `com.nyanako.inputmethod.shanjie.zhuyin`。如果系統不需要登出就接受了，照實記錄（啟用畫面那條路就仍然沒走到，寫進研究紀錄）。
6. 研究紀錄寫下這一片的實測結果；方法論寫下第一版的教訓（改到已經有決定的地方，先讀研究紀錄裡同主題的條目）。

## 4. 停止條件

- `--render-steps` 執行前後，第 3 項的兩份紀錄有任何不同。
- 實機上「重新安裝」之後，選單裡的善解無法使用，或 cdhash 和開始前不同。
- 需要讓安裝程式帶任何 entitlement 才做得到的功能，一律拿掉，不改簽章規則。
- 虛擬機跑不起來（例如沒有可用的 macOS 映像，或輸入法在虛擬機裡無法使用）：停下來問使用者，不自行縮小 §0。
- **預算**：實作加審查最多 3 輪（每一輪是「修改 → `/code-review` → fresh verifier」），使用者的外觀回饋輪數不算在內。超過就停下來問使用者。

## 5. 回滾

- 合併之前：關掉 PR，刪除分支與 worktree。
- 發佈之後才發現問題：
  - 這一片只改安裝程式。輸入法的 zip 與 cask 都不受影響。
  - 由使用者決定，從那個 Release 刪掉 `shanjie-installer-<版本>.zip` 附件（外部動作，要先問），README 改回只寫 brew 與手動安裝；然後 revert 本片的 merge commit，再發一個修正版。
  - 已經用新安裝程式裝好的人沒有殘留：安裝結果和舊安裝程式相同，都是同一個 `install-ime.sh` 加上同一個 `Registration`。

## 6. 範圍外

- 註冊輔助程式、App 沙盒（下一片）。
- brew 升級接手（s3b §15）、brew 第一次安裝時自動註冊（§14.1 的原因仍然成立）。
- `.pkg` 安裝程式、自動更新檢查、「現在登出」按鈕。

## 7. 安全審查

`pilotfish:security-reviewer`（2026-10-08，讀程式碼，沒有執行）：沒有 P0–P2。

| ID | 嚴重度 | 內容 | 處置 |
|---|---|---|---|
| B-1 | P3 | 同一個 bundle ID 會列出本體、`.zhuyin` 與舊模式，挑錯就靜靜地沒切到 | FIX：§1.2 只選 `.zhuyin` 且可選的那一個，§3 第 1 項加單元測試 |
| B-2 | P4 | `--render-steps` 會跟隨符號連結、覆蓋同名檔；只有已經能用本人身分執行指令的人能傳這個參數 | FIX（便宜）：剛好兩個參數、資料夾須已存在、不覆蓋寫入。不用 `#if DEBUG`，截圖才來自正式版同一份程式 |
| B-3 | P4 | 共用的建畫面程式碼如果引用 `Paths` 或 `InstallerPlan.decide`，畫面輸出就會讀到已安裝的那一份 | FIX：§3 第 3 項的 verifier 明確檢查 |
| B-4 | P4 | 「拷貝錯誤細節」的內容含家目錄路徑（帳號名） | FIX（一行）：顯示與拷貝前把家目錄換成 `~` |
| B-5 | P4 | 安全輸入開著時 `TISSelectInputSource` 可能回 noErr 卻沒切過去 | FIX：§1.2 重讀目前的輸入方式才算成功 |
| B-6 | P4 | 試打框不能是安全輸入框；輸入法會從那裡學習 | FIX：§1.1 寫明一般文字框、內容不存；學習是輸入法本來的行為，ACCEPT |
| — | — | 同帳號的人可以在安裝路徑放別的 bundle，讓按鈕切到別的輸入法 | ACCEPT：他本來就控制這個帳號 |

先前沙盒那一片的安全審查清單（F1–F12，還沒寫進 repo，會寫進沙盒契約）沒有一項需要搬進這一片：F4 已移出，F5 交給沙盒那一片（資料位置會變），F1、F9、F11 是簽章與 release，這一片不改；其餘與這一片無關。

## 8. 審查紀錄與處置

- 第一次 plan-verifier（第一版）：REVISE 8 點，第二版縮小範圍處理（見開頭）。
- 第二次 plan-verifier（第二版）：REVISE 4 點，逐項處置如下，接著做一次收尾審查。
  1. 畫面輸出違反 s3c「不執行安裝程式」的規則，LaunchServices 那句沒量：FIX，§1.4 寫明例外與範圍，s3c §5 加註；§3 第 3 項加 LaunchServices 前後比對。
  2. 主要結果在驗收裡看不到（全新安裝是選擇性）：FIX，使用者選了虛擬機，§3 第 5 項改成必要，§0 每一項對應到 §3 的必要項目；虛擬機跑不起來是停止條件。
  3. 沒有預算：FIX，§4 加「實作加審查最多 3 輪」。
  4. 把讀原始碼的結論寫成「量過」：FIX，開頭那段改成讀原始碼得到；方法論那條（brew 分支）一起改。
  - 非阻擋的三點（`inputSources` 是 internal、「等待」不是獨立畫面、`.shanjie-previous` 會被換掉）都已寫進 §1.5、§3 第 4 項。

## 9. 修訂一：更新時不再註冊；啟用改由系統設定（2026-10-09）

### 9.1 量到的事（研究紀錄 2026-10-09 兩節）

- **Caps Lock**：bundle 換過之後，再呼叫註冊／啟用（`Registration.run` 裡的 `TISRegisterInputSource`、`TISEnableInputSource`），系統的 Caps Lock 切換就會壞，要用慢速按鍵修復。只換檔案、只結束程序、只 `lsregister -f`、bundle 沒換過時只註冊，這幾種都不會壞。每種情況只試一次，由使用者按 Caps Lock 回報。新安裝程式的「重新安裝」會觸發；手動照同樣四步做、每步之間停 3 秒，也會觸發。
- **程式化啟用**：善解本體在「系統還沒接受」的狀態下，`TISEnableInputSource` 回 noErr，本體仍是未啟用。量到兩次：
  - 本機 brew 換版後被移出已啟用清單；
  - 虛擬機（macOS 26.6.2）全新安裝並重開機後。
  
  兩次都是使用者在「系統設定 → 鍵盤 → 輸入方式」加入後才生效。這推翻了 s3c 與本契約 §1.1「登出、登入、按啟用」的前提（那個前提來自 2026-10-04 的 0.1.0 實測）。

### 9.2 改動

1. **只有系統還不認得時才註冊**（`ShanjieInstall`；`shanjie install`、安裝程式、`install-ime.sh` 共用）：
   - `Registration.run` 一開始只讀狀態，不改任何東西。讀三件事：
     - `.zhuyin` 模式有沒有出現在已安裝清單（`modeListed`）；
     - 本體有沒有在已啟用清單（`accepted`，即 `isAccepted`）；
     - 有沒有已啟用的舊模式（`.standard`、`.eten`，即 `legacyEnabled`）。
   - 純函式 `Registration.decide(modeListed:accepted:legacyEnabled:)` 決定怎麼做：

     | 情況 | 動作 | 回傳 |
     |---|---|---|
     | 已接受、沒有舊模式 | 什麼都不改 | `done`，`skipped = true` |
     | 系統認得模式、還沒接受、沒有舊模式 | 什麼都不改。這時再註冊或啟用沒有效果（實測），還會讓 Caps Lock 壞掉 | `notAccepted`，`skipped = true` |
     | 系統還不認得模式（第一次安裝），或有舊模式（從兩模式舊版升級） | 照舊走完整流程（註冊、啟用、確認、倚天延續、停用舊模式） | 照舊 |

   - 倚天延續的條件本來就要求 `.eten` 已啟用，所以前兩種情況跳過時不會少做任何事。
2. **等待改由呼叫端做，期間不呼叫任何 TIS 修改函式**：
   - 換完 bundle 後，系統可能有一下子把本體標成未啟用。brew 實測 0.07–0.12 秒內恢復，見 `fix/brew-upgrade` 分支的 s3b §15.6；那個分支合併後就在 main。這時 `Registration.run` 回 `notAccepted` 而且沒有改任何東西，呼叫端只要等。
   - **安裝程式**：沿用 s3c §2.4 的輪詢，每 0.5 秒查 `isAccepted`，最多 30 秒。
     - 等到接受了：再呼叫一次 `Registration.run`，這時它會跳過；有舊模式的話會做完舊模式的處理。然後到試打。
     - 等不到：到「啟用」步驟。
     - 所有在接受之後走到試打的路，都經過 `Registration.run`：「安裝／更新／重新安裝」後的 enable、輪詢、「重新檢查」。
   - **`shanjie install`**：拿到 `notAccepted` 時，每 0.5 秒查一次，最多 5 秒（命令列可以等）。
     - 等到了：印 `install: already enabled; registration skipped`，exit 0。
     - 等不到：exit 3，訊息改成系統設定的三步。
   - 決策表與這兩個等待的終點都要有單元測試（9.3）。
3. **「啟用」步驟改成帶去系統設定**：
   - 說明改成三步：
     1. 按「打開輸入方式設定」；
     2. 在「輸入方式」按「+」；
     3. 選「繁體中文」裡的「善解輸入法」，按「加入」。
   - 按鈕有三個：
     - 「打開輸入方式設定」：用 `NSWorkspace.open` 打開 `x-apple.systempreferences:com.apple.Keyboard-Settings.extension`，不需要 entitlement；
     - 「重新檢查」：呼叫 `Registration.run`。系統還沒接受時，它依第 1 項什麼都不改、回 `notAccepted`，畫面停在啟用並加一句「還沒偵測到，請照上面三步加入」；回 `done` 就到試打；
     - 「完成」。
   - 不再寫「登出再登入」。
4. **`install-ime.sh`**：
   - 最後一步仍執行 `shanjie install`。已啟用時，或換完檔案後幾秒內恢復時，它什麼都不改（第 1、2 項），只印出 `install: already enabled; registration skipped`，結束碼 0。
   - 結束碼 3 的訊息與最後的「Next:」改成系統設定的三步，拿掉「登出再登入，然後只執行 … install」。
   - 「lsregister 失敗」與「舊程序還在」這兩則警告裡提到的「登出再登入」，是讓舊版停止服務的方法，和註冊無關，保留。
5. **s3b**（本片一起改，每處加修訂註記並指向本節）：
   - §4 的 `install-ime.sh` 順序：「執行已安裝那一份的 `install`（一律重新註冊）」改成「只在需要時註冊」，最後一行訊息改成系統設定的三步。「沒出現就登出再登入」改掉。
   - §13.3 的 `shanjie install`：
     - 加上第 1 項的決策表；
     - exit 0 包含 `already enabled; registration skipped`；
     - exit 3 的意思改成「到系統設定 → 鍵盤 → 輸入方式加入」；
     - 「`TISRegisterInputSource` 一律呼叫」改成「系統還不認得模式，或有舊模式時才呼叫」。
   - s3b 的審查紀錄裡提到「一律呼叫」的那一列保留原文，加一句指向本節。
   - `Shanjie/main.swift`、`Registration.swift` 的註解同時改。
6. **s3c §2.3–2.4**：開頭加一行「已由 installer-v2 §9 修訂：註冊在已啟用時跳過；還差一步改成帶去系統設定」。
7. **brew caveats 與 README**：第一次安裝的說明改成「執行 `install`；結束碼 3 時到系統設定加入」，拿掉「登出再登入再執行一次」。這一條在 `fix/brew-upgrade` 分支，和本片分開合併。

**本修訂取代的地方**：

| 位置 | 原本 | 改成 |
|---|---|---|
| §0 第 1 點「決定邏輯不變」 | 「`Registration.run` 一行都不改」 | 依 9.2 第 1 項，`Registration.run` 開頭加決策表 |
| §0「怎麼看到」第 3 點 | 全新安裝要包括「需要登出的那一步」 | 改成「從系統設定加入的那一步」 |
| §1.1「啟用」那一列 | 分三步寫登出、登入、按啟用 | 9.2 第 3 項 |
| §1.1 歡迎頁 `enableSame` 那句 | 「登出再登入後回到這裡，也是按這個」 | 改成「從系統設定加入後回到這裡，也是按這個」 |
| §1.5 `modeNotListed` → 啟用 | 隱含需要登出 | 啟用畫面是系統設定的步驟 |
| §3 第 4 項最後一點 | 「啟用畫面（需要登出）」 | 改成「啟用畫面」 |
| §3 第 5 項的使用者步驟與通過條件 | 依啟用畫面登出、登入，再按啟用 | 由 9.3 第 3 項取代；全新安裝只有一個通過條件，就是 9.3 第 3 項 |

### 9.3 驗收（加在 §3 之後）

1. 單元測試：
   - `Registration.decide` 的全部組合（`modeListed` × `accepted` × `legacyEnabled`），特別是「認得、未接受、沒有舊模式」不註冊；
   - `shanjie install` 的等待：注入假的狀態序列，「5 秒內接受」得到 exit 0 加 skipped，「一直未接受」得到 exit 3，而且兩者都沒有呼叫 TIS 修改函式（以注入的計數斷言）；
   - fresh verifier 讀 `Controller`：每一條在接受後到 `showTry` 的路都經過 `Registration.run`；「重新檢查」在未接受時不會走到任何 TIS 修改函式。
2. 實機（使用者操作，main 準備）：
   - 用新安裝程式「重新安裝」：試打能打字；回到原本的 App 按 Caps Lock，能切換。
   - 再用 `scripts/install-ime.sh` 裝一次：Caps Lock 仍能切換；輸出有「registration skipped」。要裝的 bundle 由本分支 `make bundle` 組出（`build/善解輸入法.app`），不是 0.3.0 正式版，因為 0.3.0 的 `install` 早於 §9，每次都會註冊。
3. **虛擬機全新安裝**，取代 §3 第 5 項的步驟與通過條件：
   - 安裝後如果走到「啟用」步驟：按「打開輸入方式設定」，照三步加入，再按「重新檢查」。
   - 通過：到試打頁並打出中文，目前的輸入方式是 `com.nyanako.inputmethod.shanjie.zhuyin`。
   - 如果系統不需要加入就直接接受了，照實記錄。
   - 另外記錄：裝完之後 Caps Lock 能不能切換。
4. 研究紀錄寫下實測結果，包括全新安裝後 Caps Lock 是否正常。

### 9.4 停止條件

- 跳過註冊之後，更新完 Caps Lock 仍然會壞：停下來重新查，表示還有別的觸發條件。
- 跳過註冊之後，更新完善解從已啟用清單消失，而且輪詢期間沒恢復：停下來問使用者。

### 9.5 範圍外

- 第一次安裝時 Caps Lock 是否也會被註冊觸發：第一次安裝本來就需要註冊；後面的「從系統設定加入」能不能讓它恢復，由 9.3 第 3 項觀察記錄，不在這片修。
- 從兩模式舊版（0.1.0）升級時照舊完整註冊，可能觸發 Caps Lock；這是一次性的，不在這片處理，只在研究紀錄註明。
- 兩模式舊版升級時，如果走完整流程後回 `notAccepted`（倚天延續與停用舊模式都排在接受檢查之後），舊模式的清理要等到下一次更新才會做。只影響從 0.1.x 升上來的人。

### 9.6 本修訂的審查紀錄與處置

- 第一次 plan-verifier：REVISE 3 點。
  - 試打前的路要經過 `Registration.run`；
  - 列出被取代的段落；
  - s3b、s3c 與 `install-ime.sh` 的登出說明要一起改。
  
  三點都在第二版處理。
- 第二次 plan-verifier：REVISE 2 點，逐項處置如下（兩次 REVISE 之後的處置，接著做一次收尾審查）：
  1. 「重新檢查」與 `install-ime.sh` 仍可能在換完檔案、系統還沒接受時註冊：**FIX**。
     - 規則改成單一的決策表（9.2 第 1 項）：「認得但未接受」一律不呼叫任何 TIS 修改函式。
     - 等待改由呼叫端做（9.2 第 2 項），拿掉原本的 `afterCopy` 快照設計。
     - 9.3 第 1 項加上決策表與等待的單元測試。
  2. s3b §4 與審查紀錄仍寫「一律重新註冊」「登出再登入」：**FIX**。§4 兩行改寫並加上指向本節的註記；§13.3 開頭加修訂註記；審查紀錄那一列加指標。
  - 非阻擋項（0.07–0.12 秒的出處）：**FIX**，9.2 第 2 項註明出處是 `fix/brew-upgrade` 分支的 s3b §15.6。
