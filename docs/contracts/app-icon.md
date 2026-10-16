# 契約：善解的 App 圖示（app-icon）

狀態：READY（2026-10-17，plan-verifier 一次 READY）；實作的 /code-review 處置見 §7。

## 0. 依據

- 維護者 2026-10-17：「善解的 icon 還沒正式定下來」，選擇題選「A 朱印」（和網站 favicon 一致的朱紅印章「解」）與「趕進 v0.4.7」；看過系統實際畫出的樣稿後要「更 liquid glass 的感覺」，在四個版本裡選 g2b（框與字各自是一層玻璃），再選「整體放大 10%」。
- 現況：`scripts/build-app.sh` 只產生選單列的單色 `shanjie.tiff`（`scripts/make-icon.swift`）；輸入法 App 與安裝程式的 Info.plist 都沒有圖示鍵，Finder、安裝程式、Sparkle 的更新視窗（`docs/contracts/about-update.md`）顯示的是系統預設的空白 App 圖示。
- macOS 26 起的 App 圖示用 Icon Composer 的 `.icon` 文件，由 `actool` 編成 `Assets.car`（`CFBundleIconName`）與相容用的 `.icns`（`CFBundleIconFile`）。這台 Mac 是 Xcode 27，`actool` 編得了 `.icon`（2026-10-17 實測）；CI 的 `xcode-27` 機器編不編得了由驗收第 3 項證明（推論是可以，同一版 Xcode）。

## 1. 要做到什麼、怎麼看到

- 輸入法 App（`善解輸入法.app`）與安裝程式（`安裝善解輸入法.app`）都帶同一個圖示：朱紅（#B83A2E）漸層底；圓角內框與 Noto Serif CJK TC Black 的「解」是兩層白色的玻璃圖層（`glass`、`specular`、透光 0.5），系統畫出來是透著朱紅的乳白（和網站 favicon 的米白 `#F6F1E7` 不同色，favicon 之後另改）；淺色與深色外觀都是朱紅底。
- 視覺參數（維護者選的 g2b，再整體放大 10%；出處見研究紀錄 2026-10-17「App 圖示」）：字那一組陰影 `layer-color` 0.6、`lighting` `individual`；框那一組陰影 `neutral` 0.4；兩組 `specular`、透光 0.5；底色 `automatic-gradient` 加深色外觀的同色。這些是 main 做 g2 樣稿時選的值，不是 Icon Composer 的預設，維護者看過樣稿後選用。
- 看到它有效：維護者在 Finder 打開 `~/Library/Input Methods/` 看到朱印圖示；安裝程式的 zip 解開後也是；之後 Sparkle 的更新視窗左上角也是這個圖示（隨 about-update 合併後的下一次更新看到）。
- 選單列圖示（`shanjie.tiff`，單色 template）不變。

## 2. 實作

- 原始檔：`packaging/AppIcon.icon/`（`icon.json`、`Assets/frame.svg`、`Assets/glyph.svg`）。「解」是 Noto Serif CJK TC Black 的字形外框轉成的 SVG 路徑，App 與 CI 都不需要字型檔。
- 產生器：`scripts/make-app-icon.swift <字型檔> <輸出資料夾>` 從字型檔重新產生兩個 SVG；幾何（框的位置、圓角、線寬、字的大小、整體放大倍數）是具名常數，註解寫出處（維護者哪一輪選的）。字型檔不進 repo；腳本只接受 SHA-256 與字型名稱都符合的那一個檔（2026-10-17 下載的 `NotoSerifCJKtc-Black.otf`），上游改版不會讓字形悄悄改變。
- 建置：`scripts/build-app.sh` 與 `scripts/build-installer.sh` 都呼叫 `scripts/compile-app-icon.sh <Resources>`：`xcrun actool … --app-icon AppIcon --platform macosx --minimum-deployment-target 26.0` 把 `.icon` 編進 `Resources/`，編完確認 `Assets.car` 與 `AppIcon.icns` 存在（`--app-icon` 和資料夾名不符時 actool 結束碼 0 卻什麼都不寫，實測）；Info.plist 加 `CFBundleIconName` 與 `CFBundleIconFile`（都是 `AppIcon`）。發版的 build 工作跑同一支建置腳本，所以發版路徑也有這個檢查。每次編出的 `Assets.car` 位元組不同（`AppIcon.icns` 相同），只影響外觀檔、不影響其他資源。
- 檢查：`scripts/check-app.sh` 與 `scripts/check-installer.sh` 檢查 `Resources/Assets.car`、`Resources/AppIcon.icns` 存在、兩個 Info.plist 鍵等於 `AppIcon`。
- 授權：OFL FAQ 1.1 允許把 OFL 字型用在 logo 與任何設計（「Yes. You are very welcome to do so.」，問題本身寫到依字形外框製作物件），1.13 寫明用 OFL 字型做出的圖不受 OFL 約束（https://openfontlicense.org/ofl-faq/ ）。仍在 `LICENSES/data.md` 註明圖示的「解」取自 Noto Serif CJK TC（SIL Open Font License 1.1）。
- 文件：`docs/contracts/s3b.md` §2、`docs/contracts/s3c-installer.md` §1、§3、§6、`docs/verification.md`、`docs/PLAN.md`、研究紀錄（樣稿的輪次與選擇）。網站 favicon 不在這片改（另外部署要維護者同意）。

## 3. 驗收

1. 本機：`scripts/build-app.sh` 與 `scripts/build-installer.sh` 建出的兩個 App，`check-app.sh`、`check-installer.sh` 通過；突變：拿掉 Info.plist 的 `CFBundleIconName`，檢查要失敗。
2. 系統畫出的圖示：把建出的 `Resources/Assets.car` 與 `AppIcon.icns` 複製進暫存區一個測試用 bundle ID 的空殼 App（資料夾有 `.metadata_never_index`），用 `NSWorkspace.icon(forFile:)` 在淺色、深色外觀各畫一張，和維護者選定的樣稿（g2b 放大 10%）目視相同，交給維護者看。不對 `build/` 裡的 App 呼叫，免得系統以正式的 bundle ID 登記那一份（`docs/verification.md` 的 build/ 規則）。
3. CI 全綠（`shell` 工作會跑 `build-app.sh` 與 `check-app.sh`，證明 CI 的 Xcode 編得了 `.icon`）。
4. 實機（維護者）：裝上後 Finder 的 `~/Library/Input Methods/善解輸入法.app` 顯示朱印圖示。

## 4. 停止條件

- CI 的 `actool` 編不了 `.icon`（版本不支援）：停，回報維護者（改用只帶 `.icns` 的做法要先問）。

## 5. 範圍外

- 網站 favicon、OG 圖、Release 頁的圖示（之後另外改、部署要問）。
- 選單列圖示的樣式。
- 關於頁顯示圖示（about-update 合併後再說）。

## 6. 限制與回滾

- executor 或 agent：不安裝、不啟動建出的 App、不呼叫 TIS 或 lsregister、不碰 `~/Library`；`NSWorkspace.icon(forFile:)` 只對暫存區的測試空殼呼叫。
- 回滾：revert 這個 PR；圖示只影響外觀，不影響輸入。

## 7. 審查紀錄

| 輪次 | 結果 | 處置 |
|---|---|---|
| 契約 plan-verifier | READY | — |
| 實作的 /code-review（3a7c45ce） | 11 項：actool 名稱不符時結束碼 0 又不輸出、建置腳本不會發現；發版路徑沒有檢查圖示；「CI 也編得了」寫成事實；契約狀態還是草稿、PLAN 沒有這一片；安裝程式契約與 verification.md 沒跟上；安裝程式的暫存資料夾在失敗時留下；同一段 actool 寫兩份、`AppIcon` 寫在多處；一次建置編三次、`Assets.car` 每次不同；字型沒釘版本、字型集合檔會選到日文字形；契約寫米白但圖層是白色；玻璃參數沒寫出處 | 全部處理：共用的 `scripts/compile-app-icon.sh` 在編完後確認兩個檔存在（建置腳本與發版的 build 工作都會跑到），並清掉暫存資料夾；CI 的說法改成待驗收第 3 項證明；狀態、PLAN、s3c、verification.md 更新；產生器核對字型 SHA-256 與名稱；顏色與參數照實寫。沒有改成「安裝程式複製輸入法的圖示」：發版時安裝程式是空殼建置，手上沒有輸入法 App；每次多編約 5 秒、`Assets.car` 位元組不同，寫在 §2 |

