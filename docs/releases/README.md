# Release 說明

每一個 GitHub Release 的說明本文都放在這裡，一個 Release 一個檔，檔名就是 tag（`v0.3.0.md`、`model-v3.md`、`classes-v1.md`）。內容是中文、手寫，不用 GitHub 自動產生的說明。以這裡為準：在網頁上改了說明，要同步回這個檔。契約：`docs/contracts/release-notes.md`。

- **輸入法的版本（`v*`）**：推 tag 之前，`<tag>.md` 要先寫好、經 PR 合併到 main（推 tag 仍要維護者當下同意）；跨版本 benchmark 與保留集也要先跑完、結果進 main（`docs/verification.md` 的「跨版本基準測試」）。`release.yml` 的 gate 找不到這個檔、或檔案只有空白，就不發版（在等 CI 之前就檢查）；publish 用它當說明（`--notes-file`）。它是目前最大的版本號時標成 Latest；替舊版本線補發的修正版不搶 Latest。重跑 publish 時 Release 已經存在，說明和這個檔不同會出警告。
- **模型的對外名稱**（維護者 2026-10-16 選擇題）：每種模型一個中文名與一個「SJ-」開頭的功能代號，名稱寫成「代號－版本－規模」；版本號延續舊編號，規模用最能代表該模型大小的量。Release 代號用小寫（例如 `sj-sr-6-14m`），發版說明與網站寫「語意校正模型會意（SJ-SR-6-14M）」。只改對外名稱：App 內的檔名（`bigram.sjlm` 等）與程式不改；在這之前建的 Release 保留舊代號（`model-v6` 等），新版本才用新名稱。

  | 中文名 | 代號 | 舊代號 | 檔案 | 規模的單位 | 目前這一版 |
  |---|---|---|---|---|---|
  | 通語（語言模型） | SJ-LM | `model-vN` | `bigram.sjlm` | 二元組合條數 | model-v6 ＝ SJ-LM-6-9.1M（9,101,437 條，詞彙 353,497） |
  | 辨類（詞類） | SJ-WC | `classes-vN` | `classes.sjc` | 詞類數 | classes-v5 ＝ SJ-WC-5-512C |
  | 承接（平滑） | SJ-KN | `kn-vN` | `kn.sjkn` | 詞彙數 | kn-v1 ＝ SJ-KN-1-353K |
  | 連文（前兩個詞） | SJ-CX | （新） | `trigram.sjt3` | 三元組合條數 | `sj-cx-1-7.7m` ＝ SJ-CX-1-7.7M（7,700,964 條） |
  | 會意（語意校正） | SJ-SR | `tinylm-vN` | `tinylm.sjtl` | 參數數 | `sj-sr-6-14m` ＝ SJ-SR-6-14M（1,440 萬參數；tinylm-v5 ＝ SJ-SR-5-14M） |
  | 常談（口語表） | SJ-CL | （新） | `colloquial.sjlm` | 詞彙數 | `sj-cl-1-68k` ＝ SJ-CL-1-68K（詞彙 68,055） |

  連文的第一版原本以 `trigram-v1` 建立，還沒有任何地方下載就改成 `sj-cx-1-7.7m`（舊的 Release 刪除）。

- **模型、詞類表與語意校正模型（`model-v…`、`classes-v…`、`tinylm-v…`）**：手動建，說明檔同樣先進 main。上傳的一律是雜湊檔記的那個路徑（`data/lm/<檔名>`，附件名稱就是檔名）；上傳前用進版控的雜湊檔核對，上傳後重新下載、用同一個雜湊檔再核對一次（兩次都看結束碼，不用眼睛比）：

  ```sh
  shasum -a 256 -c data/<檔名>.sha256                                   # 上傳前（repo 根目錄）
  gh release create <tag> data/lm/<檔名> -R Nanako0129/shanjie --title "<標題>" --notes-file docs/releases/<tag>.md --latest=false
  rm -rf /tmp/check-<tag> && gh release download <tag> -R Nanako0129/shanjie -p <檔名> -D /tmp/check-<tag>/data/lm \
    && (cd /tmp/check-<tag> && shasum -a 256 -c "$OLDPWD/data/<檔名>.sha256")   # 上傳後
  ```

  **常談（`sj-cl-…`）有三個附件**：`colloquial.sjlm`、`colloquial-attribution.tsv`（口語池的署名清單，雜湊檔 `data/colloquial-attribution.tsv.sha256`）與 NOTICE（`docs/releases/<tag>.NOTICE.txt`，列四個來源的授權；CC BY-SA 4.0 的檔案要附署名）。建立與核對的指令寫在 `docs/releases/sj-cl-1-68k.md`；下載時兩個檔用 `-p colloquial.sjlm -p colloquial-attribution.tsv` 一起抓。

  **雜湊檔什麼時候改**：換掉現有的檔（例如 model-v5 → model-v6）時，雜湊檔和 CI、`release.yml` 的下載在**同一個**切換的 PR 裡改（先把新雜湊合進 main，CI 會拿舊檔對新雜湊而失敗）。第一次加的新檔（例如 `tinylm-v1`）沒有人讀，雜湊檔可以和說明檔一起先進 main。

  **換檔時建 Release**（例如 `tinylm-v2`，雜湊檔在切換的 PR 才改）：這時 repo 的雜湊檔與 `data/lm/<檔名>` 都還是舊版，照上面的指令會核對並上傳舊檔。改成在暫存資料夾放新檔，上傳前後都用說明檔裡寫的雜湊核對：

  ```sh
  S=/tmp/stage-<tag> && rm -rf "$S" && mkdir -p "$S/data/lm" && cp <新檔> "$S/data/lm/<檔名>"
  (cd "$S" && echo "<說明檔裡的雜湊>  data/lm/<檔名>" | shasum -a 256 -c)   # 上傳前
  gh release create <tag> "$S/data/lm/<檔名>" -R Nanako0129/shanjie --title "<標題>" --notes-file docs/releases/<tag>.md --latest=false
  rm -rf /tmp/check-<tag> && gh release download <tag> -R Nanako0129/shanjie -p <檔名> -D /tmp/check-<tag>/data/lm \
    && (cd /tmp/check-<tag> && echo "<說明檔裡的雜湊>  data/lm/<檔名>" | shasum -a 256 -c)   # 上傳後
  ```

  **說明檔不能帶草稿標記**：說明檔在 Release 建好之前可以有「〔草稿…〕」一行，建之前要先用 PR 拿掉；建 Release 的指令前先跑 `! grep -q '〔草稿' docs/releases/<tag>.md`，有標記就停。

  資料類的 Release 一律不標 Latest，Releases 頁的 Latest 永遠是輸入法的最新版。

## Sparkle 自動更新（v0.4.7 起）

契約 `docs/contracts/about-update.md`；信任模型在它的 §0.1 與 `docs/PLAN.md` R10。

- **金鑰**：EdDSA 私鑰只在 GitHub 的 `release` Environment secret `SPARKLE_PRIVATE_KEY`（Sparkle `generate_keys` 匯出的 32 位元組種子，base64）；公鑰寫在三處，要一起改：`scripts/build-app.sh` 的 `SPARKLE_PUBLIC_KEY`、`scripts/check-app.sh` 的 `SPARKLE_PUBLIC_KEY`、`.github/workflows/release.yml` sign 工作的 `env.SPARKLE_PUBLIC_KEY`。
- **發版時 `release.yml` 做的事**：build 工作以 `SHANJIE_RELEASE_BUILD=1` 與正式 bundle ID `BUNDLE_ID=com.nyanako.inputmethod.shanjie` 建置（Info.plist `ShanjieUpdateBuild` 是 `release`，只有這種建置與更新測試版會啟動更新服務）。sign 工作（v0.5.0 起，`docs/contracts/standalone-updater.md` §2.4）用 `tar -xpf` 解開建置產物，由內而外簽善解更新那份 Sparkle 的 `Autoupdate`、`Updater.app`、framework，再簽善解更新、輸入法那份 framework（都不帶 entitlements），最後用 workflow 裡以字面值寫的三個 App 沙盒 entitlements 簽輸入法；公證並蓋章之後、打包 zip 之前 `chmod -R a+rX,go-w`（staple 在 `umask 077` 下寫出的檔是 0600）；`verify()` 要求輸入法的 entitlements 整份等於那三個鍵，`verify_sparkle` 檢查整個 bundle 沒有 `.xpc`、輸入法那份 framework 沒有 `Autoupdate` 與 `Updater.app`、善解更新那份兩個都在、各處的 Team ID 與 runtime、除了輸入法都沒有 entitlements、善解更新的 Info.plist 沒有 feed、公鑰與 `SUEnable*` 鍵、輸入法的 Sparkle 鍵逐字相符（`SUEnableInstallerLauncherService` 是 false）、檔案權限；乾跑與推 tag 都從 secret 推出公鑰，和 zip 裡的 `SUPublicEDKey` 比對。只有推 tag 時，在刪掉簽章鑰匙圈之後，sparse checkout `appcast.xml` 與 `docs/releases/<tag>.md`（只當資料讀），用 Sparkle 官方 2.10.0 tarball（SHA-256 寫在 workflow）的 `generate_appcast` 產生並簽 `appcast.xml`：發版說明嵌進新項目、保留所有版本（`--maximum-versions 0`）、指名新版本（`--versions`），然後只檢查新項目。publish 先確認 main 的 `appcast.xml` 從 tag 之後沒被改過，建好 Release 後用 contents API 寫回 main（帶 main 目前的 blob SHA，期間被改過就被拒）。
- **appcast 的 commit 沒有 CI**：它是 `GITHUB_TOKEN` 做的，不會觸發 `ci.yml`。下一次發版的 CI 關卡（`scripts/check-ci-gate.sh`）會跳過只改 `appcast.xml` 的 commit，看它前一個 commit 的 CI。
- **推 tag 之後要核對**（契約 §3 第 6 項）：main 的 `appcast.xml` 有這一版的項目，enclosure 網址是 `https://github.com/Nanako0129/shanjie/releases/download/v<V>/shanjie-<V>.zip`、`length` 等於上傳的 zip 大小；那個網址下載下來的 SHA-256 等於 `shanjie-<V>.zip.sha256`；已安裝的這一版在設定→關於按「立即檢查」顯示「已是最新」。任一項不對就當場修 appcast 或 workflow，所有這一版的使用者都靠它更新。
- **Sparkle 版本是發版關卡**（v0.5.0 起）：裝在 `/Library` 時，系統 domain 的更新由 Sparkle 的 `Autoupdate` 以 root 執行，Sparkle 以 root 執行的漏洞類別（2.9.6 修過的兩則）因此相關。版本由 `macos/Package.swift` 的 `exact:` 與 `Package.resolved` 釘住；推 tag 前看一次 Sparkle 的安全公告，有新的就先升級、再發版。
- **Homebrew 舊使用者**：cask 是 `auto_updates true`。v0.4.6 以前用 brew 裝的人還沒有 Sparkle，v0.4.7 的發版說明要寫一次性的 `brew upgrade --cask nanako0129/tap/shanjie`。

### 金鑰外洩或要輪換

先記住限制（契約 §0.1，讀 Sparkle 2.9.3 原始碼）：我們的更新是 zip，開了 `SUVerifyUpdateBeforeExtraction`。Sparkle 解壓前驗 zip 的 EdDSA 簽章；它的備援是驗壓縮檔本身的 Developer ID 簽章，zip 帶不了程式碼簽章，所以**只有 EdDSA 能讓更新被接受**，Developer ID 幫不了輪換。EdDSA 也沒有撤銷機制。已安裝的 App 只認自己 Info.plist 裡的那把公鑰，驗 feed 與壓縮檔都用它。

1. **計畫中的輪換（舊私鑰還在、沒外洩）**：維護者在自己的終端機用 `generate_keys` 產生新的金鑰對。發一個「輪換版」：App 帶**新**公鑰（改上面三處），但壓縮檔與 feed 用**舊**私鑰簽，已安裝的舊版才驗得過。之後的版本才換成新私鑰（`SPARKLE_PRIVATE_KEY` 在輪換版發完之後才換）。
   - 現在的 workflow 做不到輪換版，要另開 PR 改一次：`generate_appcast` 只在 App 的 `SUPublicEDKey` 和簽章用的私鑰相符時才簽壓縮檔（不符只印警告，新項目沒有 `sparkle:edSignature`），而金鑰成對檢查與新項目檢查都會擋下。輪換版要用舊私鑰跑 `sign_update` 簽 zip、把簽章與長度填進新項目，再用 `sign_update` 簽整份 feed；那個 PR 要寫清楚只用一次、下一版改回來。
   - 還沒裝到輪換版的使用者，在 feed 換成新私鑰簽之後就驗不過 feed，收不到之後的更新；發版說明與網站要請他們手動更新（安裝程式，或 `brew upgrade --cask nanako0129/tap/shanjie`）。
2. **舊私鑰外洩**：照 1 盡快發輪換版（攻擊者也有舊私鑰，所以同時檢查並收緊 main 的 `appcast.xml` 的寫入權：branch 規則、deploy key、Actions 權杖；契約 §0.1：私鑰加上 feed 寫入權就能推更新）。裝在 `/Library` 的使用者（v0.5.0 起）多一層：私鑰加 feed 寫入權能讓 `Autoupdate` **以 root 換掉** `/Library/Input Methods` 裡所有帳號共用的那一份（換完仍以使用者身分執行；善解更新只接受 `application` 類型，拿不到以 root 跑 `installer`），而且使用者按下更新時看到的是系統真的密碼框。發版說明與網站要點名這些使用者，請他們在輪換版之前不要按更新、改用安裝程式重裝。還沒換到新公鑰的使用者，在攻擊者同時有舊私鑰與 feed 寫入權時仍有風險；發版說明與網站要寫明，請大家盡快更新。
3. **舊私鑰遺失**：Sparkle 推不了任何更新（沒有私鑰簽不出已安裝版本驗得過的東西）。只能請使用者手動重裝新版（新公鑰），網站、README 與發版說明都要寫。
4. **Developer ID 憑證外洩**：它單獨推不了 Sparkle 更新（zip 沒有備援），但仍能簽出讓 Gatekeeper 放行的程式；照 Apple 的流程撤銷憑證、換新的 P12。
5. **要拿掉 Sparkle**：Sparkle 拒絕「新版拿掉 `SUPublicEDKey`」或拿掉程式碼簽章的更新，所以拿掉 Sparkle 的那一版仍要帶 `SUPublicEDKey`、用目前的私鑰簽；或讓 appcast 不再有新項目，使用者改用安裝程式更新。
