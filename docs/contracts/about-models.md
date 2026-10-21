# 契約：關於頁的模型列表（about-models）

狀態：草稿（2026-10-21），待 plan-verifier。

## 0. 起因、要做到什麼、怎麼看到

- **起因**：常談的契約寫了「關於頁的模型列表多一列」，但關於頁本來沒有模型列表（v0.5.0 虛擬機驗收時發現）。維護者 2026-10-21 選擇題：加一個，跟 v0.5.0 一起出。
- **要做到**：設定的「關於」頁多一節「模型」，列出這一版 App 內附的每個模型。每列顯示中文名與對外名稱（`docs/releases/README.md` 的命名規則，例如「通語」配「SJ-LM-6-9.1M」）。列表跟著 App 內附的檔案走：換了模型而表沒更新時，建置要失敗，不能顯示錯的版本。
- **怎麼看到**：維護者在虛擬機裝 dry run，打開「設定 → 關於」，看到「模型」一節與六列，名稱和 `docs/releases/README.md` 的表一致。外觀照實機一輪一輪調。

## 1. 現況

- 關於頁（`macos/Sources/Shanjie/SettingsWindow.swift` 的 `AboutSections`）只有三節：版本、更新、網站與授權一句。
- App 內附的模型（`scripts/build-app.sh` 第 218 行附近複製進 `Resources/`）：`bigram.sjlm`、`classes.sjc`、`kn.sjkn`、`tinylm.sjtl`、`trigram.sjt3`、`colloquial.sjlm`。每個檔建置前都對 `data/<檔名>.sha256` 核對。
- 對外名稱只寫在 `docs/releases/README.md` 的表裡（文件），App 不知道。

## 2. 設計

- **資料**：新增追蹤檔 `data/models.tsv`，每列 `檔名<TAB>SHA-256<TAB>中文名<TAB>對外名稱`，依「通語、辨類、承接、連文、會意、常談」的順序：

  | 檔名 | 中文名 | 對外名稱 |
  |---|---|---|
  | `bigram.sjlm` | 通語 | SJ-LM-6-9.1M |
  | `classes.sjc` | 辨類 | SJ-WC-5-512C |
  | `kn.sjkn` | 承接 | SJ-KN-1-353K |
  | `trigram.sjt3` | 連文 | SJ-CX-1-7.7M |
  | `tinylm.sjtl` | 會意 | SJ-SR-6-14M |
  | `colloquial.sjlm` | 常談 | SJ-CL-1-68K |

  實作時逐一對 `docs/releases/README.md` 的表核對名稱與版本；SHA-256 欄等於 `data/<檔名>.sha256`。
- **建置**：`scripts/build-app.sh` 在建置前檢查（和其他模型檢查放在一起）：
  - `data/models.tsv` 的每一列，SHA-256 欄等於 `data/<檔名>.sha256`；
  - 要複製進 bundle 的六個模型檔都各有一列；
  - 不符就停，訊息寫哪一列、哪個檔。
  - 通過後把 `models.tsv` 複製進 `Resources/`。`scripts/check-app.sh` 檢查 bundle 裡有這個檔，且每一列的 SHA-256 等於 bundle 裡同名模型檔的實際雜湊。
- **App**：
  - `AboutSections` 在「善解輸入法」那一節之後加一節「模型」，每列是 `LabeledContent(中文名, value: 對外名稱)`。
  - 讀 `Bundle.main` 的 `models.tsv`，解析成純函式（列的順序照檔案）。檔案不在或讀不了時，整節不顯示（開發環境、測試）。
  - 不加說明文字，維持關於頁的「第一層簡潔」。
- 外觀用系統預設的 `Form` 樣式，不另訂尺寸；實機有意見再一輪一輪調。

## 3. 驗收

1. `data/models.tsv` 的六列和 `docs/releases/README.md` 的表一致；SHA-256 等於 `data/*.sha256`。
2. 建置檢查：
   - 改壞 `models.tsv` 某一列的雜湊，`build-app.sh` 會停；
   - 拿掉某一列，也會停；
   - 照原樣則通過。
   - 以上用一個 shell 測試或 `make` 目標跑，不需要真的建完整個 App；檢查寫成 `build-app.sh` 可以單獨呼叫的函式或腳本。
3. Swift 單元測試：解析函式照順序回傳六列；空檔、欄位數不對的列、檔案不存在各一個案例。
4. `check-app.sh`（`SKIP_RUN=1`）在 dry run 的 bundle 上通過，且改壞 bundle 裡 `models.tsv` 的一個雜湊時會失敗。
5. 實機（維護者，虛擬機裡的 dry run）：「設定 → 關於」看到「模型」一節與六列。
6. `/code-review` ＋ fresh verifier ＋ CI。

## 4. 發佈、預算、退回

- 和 v0.5.0 一起出；發版說明一句：「關於頁列出內附的模型與版本」。
- 預算：實作自己做或交給 executor 一次，修正最多一次；不需要雲端。
- 退回：revert 這個 PR（關於頁回到三節，`models.tsv` 不再打包）。

## 5. 停止條件、範圍外

- **停止條件**：`docs/releases/README.md` 的表和 `data/*.sha256` 對不上（先查是哪一邊錯，問維護者）；要動到模型檔本身或核心才做得到。
- **範圍外**：每個模型的授權說明（已經在 `Resources/LICENSES`）；顯示模型「是否載入成功」；英文介面的翻譯調整（照 App 現有的做法）。
