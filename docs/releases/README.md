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
  | 會意（語意校正） | SJ-SR | `tinylm-vN` | `tinylm.sjtl` | 參數數 | tinylm-v5 ＝ SJ-SR-5-14M（1,440 萬參數） |

  連文的第一版原本以 `trigram-v1` 建立，還沒有任何地方下載就改成 `sj-cx-1-7.7m`（舊的 Release 刪除）。

- **模型、詞類表與語意校正模型（`model-v…`、`classes-v…`、`tinylm-v…`）**：手動建，說明檔同樣先進 main。上傳的一律是雜湊檔記的那個路徑（`data/lm/<檔名>`，附件名稱就是檔名）；上傳前用進版控的雜湊檔核對，上傳後重新下載、用同一個雜湊檔再核對一次（兩次都看結束碼，不用眼睛比）：

  ```sh
  shasum -a 256 -c data/<檔名>.sha256                                   # 上傳前（repo 根目錄）
  gh release create <tag> data/lm/<檔名> -R Nanako0129/shanjie --title "<標題>" --notes-file docs/releases/<tag>.md --latest=false
  rm -rf /tmp/check-<tag> && gh release download <tag> -R Nanako0129/shanjie -p <檔名> -D /tmp/check-<tag>/data/lm \
    && (cd /tmp/check-<tag> && shasum -a 256 -c "$OLDPWD/data/<檔名>.sha256")   # 上傳後
  ```

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
- **發版時 `release.yml` 做的事**：sign 工作由內而外簽 Sparkle 的 `Autoupdate`、`Updater.app`、framework 與 App；`verify_sparkle` 檢查四處的 Team ID、runtime、沒有 entitlements、沒有 XPC 服務、Info.plist 的 Sparkle 鍵逐字相符；乾跑與推 tag 都從 secret 推出公鑰，和 zip 裡的 `SUPublicEDKey` 比對。只有推 tag 時，在刪掉簽章鑰匙圈之後，用 Sparkle 官方 2.9.3 tarball（SHA-256 寫在 workflow）的 `generate_appcast` 產生並簽 `appcast.xml`；publish 先確認 main 的 `appcast.xml` 從 tag 之後沒被改過，建好 Release 後用 contents API 寫回 main（帶 main 目前的 blob SHA，期間被改過就被拒）。
- **推 tag 之後要核對**（契約 §3 第 6 項）：main 的 `appcast.xml` 有這一版的項目，enclosure 網址是 `https://github.com/Nanako0129/shanjie/releases/download/v<V>/shanjie-<V>.zip`、`length` 等於上傳的 zip 大小；那個網址下載下來的 SHA-256 等於 `shanjie-<V>.zip.sha256`；已安裝的這一版在設定→關於按「立即檢查」顯示「已是最新」。任一項不對就當場修 appcast 或 workflow，所有這一版的使用者都靠它更新。
- **Homebrew 舊使用者**：cask 是 `auto_updates true`。v0.4.6 以前用 brew 裝的人還沒有 Sparkle，v0.4.7 的發版說明要寫一次性的 `brew upgrade --cask nanako0129/tap/shanjie`。

### 金鑰外洩或要輪換

先記住限制：Sparkle 接受「EdDSA 簽章通過」**或**「新版的 Apple 簽章符合已安裝版本的 designated requirement（同一個 Team ID）」任一成立的更新；EdDSA 沒有撤銷機制。

1. **EdDSA 私鑰外洩**：產生新的金鑰對（維護者在自己的終端機跑 `generate_keys`），新私鑰換進 `SPARKLE_PRIVATE_KEY`，新公鑰改進上面三處；發一個新版本。這一版的壓縮檔用新私鑰簽，舊用戶端的公鑰是舊的、驗不過 EdDSA，所以靠第二條路接受它：新版要用同一個 Team（`2LJ882GPY8`）的 Developer ID 簽（`release.yml` 本來就這樣做）。Sparkle 允許新版帶不同的 `SUPublicEDKey`（輪換），不允許拿掉它。
2. **feed 有簽章**（`SURequireSignedFeed`）：輪換那一版的 `appcast.xml` 由新私鑰簽，舊用戶端用舊公鑰驗 feed 會失敗。依契約 §6 的評估，舊用戶端最多約 20 天收不到新 feed（這個數字來自審查，沒有實測）；這段期間請使用者用安裝程式或 `brew upgrade --cask nanako0129/tap/shanjie` 手動更新，發版說明與網站都要寫。
3. **還沒更新的使用者仍有風險**：攻擊者同時有舊私鑰與 feed 的寫入權（main 的 `appcast.xml`）時，可以對還沒換到新公鑰的使用者推更新。外洩後檢查 main 的 `appcast.xml` 歷史與 repo 的寫入權限（branch 規則、deploy key、Actions 權杖）。
4. **Developer ID 憑證外洩**：同樣能推更新（第二條路），處理方式是向 Apple 撤銷憑證、換新的 P12，並照 1 換 EdDSA 金鑰。
5. **要拿掉 Sparkle**：新版仍要保留 `SUPublicEDKey` 並用同一個 Team 簽；或讓 appcast 不再有新項目，使用者改用安裝程式更新。
